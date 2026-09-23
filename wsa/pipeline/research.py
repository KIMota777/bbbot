"""Автопилот исследования: сам ищет, проверяет и отбирает кошельки, пересобирает стратегию.

Один цикл:
1. раз в discover_hours — новые кандидаты из пулов вселенной DEX+CEX (и Birdeye, если есть ключ);
2. быстрый скан кандидатов в порядке «похожести на человека», в пределах бюджета времени;
3. глубокий скан тех, кто после быстрого выглядит перспективно;
4. профили (обогащение) для прошедших порог;
5. список наблюдения: лучшие по баллу без жёстких флагов (если auto_watch);
6. раз в rebuild_hours — сводная стратегия и бэктест, затем отчёт.

Работает параллельно с трекером (`wsa track`): тот подхватывает и новых лидеров,
и новую стратегию без перезапуска.
"""
from __future__ import annotations

import json
import logging
import time

from ..util import fmt_usd, now, short_addr

log = logging.getLogger("wsa.research")


def _promising(ctx, limit: int) -> list:
    """После быстрого скана: не боты, есть закрытые сделки, прибыль > 0, ещё не глубокий скан."""
    rows = ctx.db.query(
        "SELECT w.*, a.metrics, a.flags FROM wallets w JOIN analysis a ON a.chain=w.chain AND a.address=w.address"
        " WHERE w.status IN ('scanned','watch') AND COALESCE(w.scan_depth,'quick')='quick' ORDER BY w.score DESC")
    out = []
    for r in rows:
        m = json.loads(r["metrics"] or "{}")
        fl = json.loads(r["flags"] or "{}")
        if any(v["level"] == "hard" for v in fl.values()):
            continue
        if (m.get("realized_pnl") or 0) > 0 and (m.get("closed") or 0) >= 3:
            out.append(r)
        if len(out) >= limit:
            break
    return out


def cycle(ctx, opts: dict) -> dict:
    from ..pipeline import discover as d
    from .enrich import enrich_wallet
    from .scan import scan_wallet
    st = ctx.db.kv_get("research:state", {}) or {}
    rep = {"discovered": 0, "quick": 0, "deep": 0, "enriched": 0, "watch_added": 0, "strategy": None}
    chain = opts.get("chain", "solana")

    # 1. кандидаты
    if now() - st.get("last_discover", 0) > opts["discover_hours"] * 3600:
        try:
            uni = d.universe_tokens(ctx, chain, opts["universe_limit"])
            for u in uni:
                res = d.sample_pool(ctx, chain, u["best_pair"], 0, 60, opts["min_usd"])
                rep["discovered"] += res["new"]
                if ctx.birdeye.enabled:
                    try:
                        rep["discovered"] += d.from_birdeye(ctx, chain, u["token"], "30d").get("new", 0)
                    except Exception as e:
                        log.warning("Birdeye %s: %s", u["symbol"], e)
            st["last_discover"] = now()
            log.info("кандидаты: +%d новых из %d пулов", rep["discovered"], len(uni))
        except Exception as e:
            log.warning("поиск кандидатов не удался: %s", e)

    # 2а. догрузка очереди повторов: транзакции, которые RPC не отдал в прошлый раз
    t_end = time.time() + opts["scan_minutes"] * 60
    for r in ctx.db.query("SELECT DISTINCT s.chain, s.address, w.scan_depth FROM seen_tx s JOIN wallets w"
                          " ON w.chain=s.chain AND w.address=s.address WHERE s.kind='retry' AND w.status!='bot'"):
        if time.time() > t_end:
            break
        try:
            scan_wallet(ctx, r["chain"], r["address"], depth=r["scan_depth"] or "quick")
        except Exception as e:
            log.warning("догрузка %s: %s", short_addr(r["address"]), e)

    # 2б. быстрый скан новых кандидатов в пределах бюджета
    todo = ctx.db.query(
        "SELECT * FROM wallets WHERE status='candidate' ORDER BY (json_array_length(COALESCE(seen_pools,'[]')) > 2),"
        " (seen_trades > 5), seen_volume_usd DESC LIMIT 400")
    for w in todo:
        if time.time() > t_end:
            break
        try:
            r = scan_wallet(ctx, w["chain"], w["address"], depth="quick")
            rep["quick"] += 1
            if r["status"] == "ok" and (r.get("metrics") or {}).get("realized_pnl", 0) > 0:
                log.info("  %s: балл %s, PnL %s", short_addr(w["address"]), r["score"],
                         fmt_usd(r["metrics"]["realized_pnl"], signed=True))
        except Exception as e:
            log.warning("скан %s: %s", short_addr(w["address"]), e)
            ctx.db.set_wallet(w["chain"], w["address"], status="rejected", note=str(e)[:200])

    # 3. глубокий скан перспективных
    for w in _promising(ctx, opts["deep_per_cycle"]):
        try:
            scan_wallet(ctx, w["chain"], w["address"], depth="deep")
            rep["deep"] += 1
        except Exception as e:
            log.warning("глубокий скан %s: %s", short_addr(w["address"]), e)

    # 4. профили для прошедших порог
    rows = ctx.db.query("SELECT w.* FROM wallets w JOIN analysis a ON a.chain=w.chain AND a.address=w.address"
                        " WHERE w.scan_depth='deep' AND w.score >= ? AND (a.profile IS NULL OR a.computed_at < ?)",
                        (opts["min_score"], now() - 24 * 3600))
    for w in rows:
        try:
            enrich_wallet(ctx, w["chain"], w["address"])
            rep["enriched"] += 1
        except Exception as e:
            log.warning("профиль %s: %s", short_addr(w["address"]), e)

    # 5. список наблюдения
    if opts["auto_watch"]:
        cur = {r["address"] for r in ctx.db.wallets("watch")}
        cand = ctx.db.query("SELECT w.*, a.flags FROM wallets w JOIN analysis a ON a.chain=w.chain AND a.address=w.address"
                            " WHERE w.status='scanned' AND w.scan_depth='deep' AND w.score >= ? ORDER BY w.score DESC",
                            (opts["min_score"],))
        for r in cand:
            if len(cur) >= opts["watch_max"]:
                break
            if any(v["level"] == "hard" for v in json.loads(r["flags"] or "{}").values()):
                continue
            ctx.db.set_wallet(r["chain"], r["address"], status="watch")
            cur.add(r["address"])
            rep["watch_added"] += 1
            log.info("в наблюдение: %s (балл %s)", short_addr(r["address"]), r["score"])

    # 6. стратегия
    if now() - st.get("last_build", 0) > opts["rebuild_hours"] * 3600 and len(ctx.db.wallets("watch")) >= opts["min_cohort"]:
        try:
            from ..core.backtest import backtest_strategy
            from ..core.strategy import Strategy, build
            cohort = []
            for w in ctx.db.wallets("watch"):
                a = ctx.db.analysis(w["chain"], w["address"]) or {}
                if not a.get("profile"):
                    enrich_wallet(ctx, w["chain"], w["address"])
                    a = ctx.db.analysis(w["chain"], w["address"]) or {}
                m = a.get("metrics") or {}
                cohort.append({"chain": w["chain"], "address": w["address"], "score": w["score"],
                               "hold_p50": (m.get("hold") or {}).get("p50")})
            s = build(ctx, cohort)
            if s.get("ok"):
                Strategy(s).save(ctx.cfg.strategy.path)
                bt = backtest_strategy(ctx, s)
                ctx.db.kv_set("backtest:last", {k: v for k, v in bt.items() if k != "trades"} | {"trades": bt["trades"][-200:], "ts": now()})
                rep["strategy"] = "принята" if s.get("accepted") else "не принята"
            else:
                rep["strategy"] = s.get("reason")
            st["last_build"] = now()
        except Exception as e:
            log.warning("сборка стратегии: %s", e)
    try:
        from pathlib import Path

        from ..report.html import build_report
        from ..report.site import export
        build_report(ctx, Path(ctx.cfg.general.db_path).parent / "report.html")
        export(ctx)  # webapp/data/wallets.json — страница /wallets сайта
    except Exception as e:
        log.warning("отчёт/снимок: %s", e)
    ctx.db.kv_set("research:state", st)
    return rep


def run(ctx, loop: bool = False, cycles: int | None = None, **opts) -> None:
    defaults = {"discover_hours": 2, "universe_limit": 25, "min_usd": 500, "scan_minutes": 20,
                "deep_per_cycle": 5, "min_score": ctx.cfg.trading.min_wallet_score, "auto_watch": True,
                "watch_max": 15, "rebuild_hours": 24, "min_cohort": 3, "pause_minutes": 5}
    defaults.update({k: v for k, v in opts.items() if v is not None})
    n = 0
    while True:
        n += 1
        t0 = time.time()
        rep = cycle(ctx, defaults)
        log.info("цикл %d за %.0f мин: %s", n, (time.time() - t0) / 60, rep)
        if not loop or (cycles and n >= cycles):
            break
        time.sleep(defaults["pause_minutes"] * 60)
