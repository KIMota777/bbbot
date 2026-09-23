"""Живое слежение за кошельками из списка наблюдения (status='watch').

Каждые poll_sec секунд:
1. новые транзакции каждого кошелька -> ноги сделок (source='live');
2. покупка лидера -> снимок рынка токена (ликвидность, MC, держатели) ->
   проверка стратегией -> сигнал -> (если включена торговля) движок;
3. продажа лидера -> выход вслед за ним, если он сбросил ≥ заданной доли;
4. ведение открытых позиций (стопы, тейки, трейлинг, тайм-стоп);
5. раз в interval_min — сканер рынка CEX (если в стратегии есть принятые правила);
6. раз в rescore_hours — пересчёт метрик кошельков: лидер, который перестал
   быть прибыльным, опускается ниже порога и перестаёт давать сигналы.

Всё, что видел трекер, копится в базе — через период наблюдения на этих
данных пересобирается стратегия (`wsa strategy build`).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from ..core.positions import build_round_trips
from ..util import fmt_usd, short_addr
from .scan import analyze, fetch_evm, fetch_solana

log = logging.getLogger("wsa.track")


def poll_wallet(ctx, chain: str, address: str) -> list[dict]:
    if chain == "solana":
        res = fetch_solana(ctx, address, max_tx=300, days=3, new_only=True, source="live")
    else:
        res = fetch_evm(ctx, chain, address, max_items=300, days=3, source="live")
    legs = res.get("new_legs") or []
    for l in legs:
        l["chain"], l["wallet"] = chain, address
    return [l for l in legs if l["side"] in ("buy", "sell") and l.get("usd")]


def leader_sold_fraction(ctx, chain: str, wallet: str, leg: dict) -> float:
    """Какую долю своей позиции лидер продал этой сделкой."""
    legs = [l for l in ctx.db.legs(chain, wallet) if l["token"] == leg["token"]]
    trips, _ = build_round_trips(chain, wallet, legs)
    for t in trips:
        for f in t.fills:
            if f.sig == leg["sig"] and f.side == "sell" and f.pos_before > 0:
                return min(1.0, f.qty / f.pos_before)
    return 1.0


def run(ctx, strategy=None, mode: str | None = None, once: bool = False, max_loops: int | None = None) -> None:
    from ..trading.engine import Engine
    from ..trading.signals import evaluate_wallet_buy, record, scan_market

    cfg = ctx.cfg
    engine = Engine(ctx, strategy, mode) if (mode and strategy) else None
    if mode and not strategy:
        raise RuntimeError("для торговли нужна стратегия: сначала `wsa strategy build`")
    watch = [dict(w) for w in ctx.db.wallets("watch")]
    if not watch:
        raise RuntimeError("список наблюдения пуст: `wsa watch add <адрес>` или `wsa watch auto`")
    log.info("слежу за %d кошельками; торговля: %s; стратегия: %s", len(watch), mode or "выключена",
             "есть" if strategy else "нет (только наблюдение)")
    # первая синхронизация: не считаем сигналами старые сделки
    for w in watch:
        st = ctx.db.sync(w["chain"], w["address"])
        if not st.get("newest_sig") and not st.get("newest_block"):
            log.info("  %s: первая синхронизация (история до этого момента — не сигналы)", short_addr(w["address"]))
            try:
                poll_wallet(ctx, w["chain"], w["address"])
            except Exception as e:
                log.warning("  %s: %s", short_addr(w["address"]), e)
    last_scan = last_rescore = 0.0
    loops = 0
    strat_path = Path(cfg.strategy.path)
    strat_mtime = strat_path.stat().st_mtime if strat_path.exists() else 0
    while True:
        t0 = time.time()
        loops += 1
        # автопилот исследования мог добавить лидеров или пересобрать стратегию
        fresh = [dict(w) for w in ctx.db.wallets("watch")]
        if {w["address"] for w in fresh} != {w["address"] for w in watch}:
            added = {w["address"] for w in fresh} - {w["address"] for w in watch}
            for w in fresh:
                if w["address"] in added:
                    log.info("новый лидер в наблюдении: %s — первая синхронизация", short_addr(w["address"]))
                    try:
                        poll_wallet(ctx, w["chain"], w["address"])
                    except Exception as e:
                        log.warning("  %s: %s", short_addr(w["address"]), e)
            watch = fresh
        if strat_path.exists() and strat_path.stat().st_mtime != strat_mtime:
            from ..core.strategy import Strategy
            strat_mtime = strat_path.stat().st_mtime
            new_s = Strategy.load(str(strat_path))
            if new_s:
                strategy = new_s
                if engine:
                    engine.strategy = new_s
                log.info("стратегия обновлена: принятых правил %d (кошелёк) / %d (сканер)",
                         len(new_s.wallet_rules), len(new_s.scanner_rules))
        for w in watch:
            try:
                new = poll_wallet(ctx, w["chain"], w["address"])
            except Exception as e:
                log.warning("%s: опрос не удался: %s", short_addr(w["address"]), e)
                continue
            for leg in new:
                meta = ctx.tokens.meta(leg["chain"], leg["token"], max_age=60)
                sym = meta.get("symbol") or short_addr(leg["token"])
                log.info("%s %s %s на %s", w.get("label") or short_addr(w["address"]),
                         "КУПИЛ" if leg["side"] == "buy" else "ПРОДАЛ", sym, fmt_usd(leg["usd"]))
                try:
                    ctx.tokens.holders(leg["chain"], leg["token"], max_age=cfg.tracking.snapshot_holders_min * 60)
                except Exception:
                    pass
                if leg["side"] == "buy" and strategy:
                    sig, status, reason = evaluate_wallet_buy(ctx, strategy, w, leg)
                    if status == "new" and engine:
                        engine.on_signal(sig)
                    else:
                        record(ctx, sig, status if status != "new" else "observed", reason)
                        if status == "new":
                            log.info("  → СИГНАЛ %s (%s), торговля выключена", sig.get("symbol"), sig.get("rule"))
                        else:
                            log.info("  → без сигнала: %s", reason)
                elif leg["side"] == "sell" and engine:
                    engine.on_leader_sell(w["address"], leg["token"], leader_sold_fraction(ctx, leg["chain"], w["address"], leg))
        if engine:
            engine.manage()
        if strategy and strategy.scanner_rules and time.time() - last_scan > cfg.scanner.interval_min * 60:
            last_scan = time.time()
            for s in scan_market(ctx, strategy, cfg.scanner.max_symbols):
                if engine:
                    engine.on_signal(s)
                else:
                    record(ctx, s, "observed", "сканер, торговля выключена")
        if time.time() - last_rescore > cfg.tracking.rescore_hours * 3600:
            if last_rescore:
                for w in watch:
                    try:
                        r = analyze(ctx, w["chain"], w["address"])
                        w["score"] = r["score"]
                    except Exception as e:
                        log.warning("пересчёт %s: %s", short_addr(w["address"]), e)
            last_rescore = time.time()
        if once or (max_loops and loops >= max_loops):
            break
        time.sleep(max(1.0, cfg.tracking.poll_sec - (time.time() - t0)))
