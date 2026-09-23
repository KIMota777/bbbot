"""Снимок анализатора для сайта: webapp/data/wallets.json -> страница /wallets.

Сайт на сервере анализатор не запускает — он показывает этот файл, как и
остальные webapp/data/*.json. Пересборка: `./wsa.sh site` (автопилот делает
это сам после каждого цикла); на сайт снимок попадает с коммитом.
В снимке только публичные данные сети и расчёты — никаких ключей.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import ROOT
from ..util import now, short_addr

SITE_JSON = ROOT / "webapp" / "data" / "wallets.json"


def _wallet_row(r, a: dict) -> dict:
    m = a.get("metrics") or {}
    prof = a.get("profile") or {}
    fl = a.get("flags") or {}
    after = (prof.get("after_entry") or {})
    return {
        "address": r["address"], "short": short_addr(r["address"], 5), "chain": r["chain"],
        "label": r["label"], "status": r["status"], "score": r["score"], "depth": r["scan_depth"],
        "closed": m.get("closed"), "win_rate": m.get("win_rate"), "win_rate_lb": m.get("win_rate_lb"),
        "pf": m.get("profit_factor"), "realized": m.get("realized_pnl"), "unrealized": m.get("unrealized_pnl"),
        "roi": m.get("roi_capital"), "dd": m.get("max_dd_norm"), "hold": (m.get("hold") or {}).get("p50"),
        "cex_share": m.get("cex_volume_share"), "history_days": m.get("history_days"), "n_tokens": m.get("n_tokens"),
        "volume": m.get("volume_usd"),
        "flags": [{"code": k, "level": v.get("level"), "msg": v.get("msg")} for k, v in fl.items()],
        "style": (prof.get("style") or {}).get("label"),
        "detected": prof.get("detected_strategy"),
        "types": (prof.get("entry") or {}).get("types"),
        "sectors": dict(list(((prof.get("selection") or {}).get("sectors") or {}).items())[:4]),
        "after_24h": (after.get("24h") or {}).get("p50"), "after_7d": (after.get("7d") or {}).get("p50"),
        "cex_at_entry": (prof.get("selection") or {}).get("cex_listed_at_entry_share"),
    }


def build_snapshot(ctx) -> dict:
    db = ctx.db
    counts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM wallets GROUP BY status")}
    rows = db.query("SELECT * FROM wallets WHERE scanned_at IS NOT NULL AND status NOT IN ('bot')"
                    " ORDER BY (status='watch') DESC, score DESC NULLS LAST LIMIT 25")
    wallets = [_wallet_row(r, db.analysis(r["chain"], r["address"]) or {}) for r in rows]
    bots = []
    for r in db.query("SELECT w.address, w.note, a.flags, a.metrics FROM wallets w LEFT JOIN analysis a"
                      " ON a.chain=w.chain AND a.address=w.address WHERE w.status='bot' ORDER BY w.scanned_at DESC LIMIT 15"):
        fl = json.loads(r["flags"] or "{}")
        bots.append({"short": short_addr(r["address"], 5), "address": r["address"],
                     "why": "; ".join(v["msg"] for v in fl.values()) or r["note"] or ""})
    spd = [json.loads(r["metrics"] or "{}").get("sigs_per_day") for r in
           db.query("SELECT a.metrics FROM wallets w JOIN analysis a ON a.chain=w.chain AND a.address=w.address WHERE w.status='bot'")]
    spd = sorted(x for x in spd if x)
    uni = [dict(r) for r in db.query("SELECT symbol, cex_symbol, liquidity_usd, vol24, cex_turnover_24h FROM universe"
                                     " WHERE exchange=? ORDER BY cex_turnover_24h DESC LIMIT 30", (ctx.cfg.cex.exchange,))]
    strat = None
    sp = Path(ctx.cfg.strategy.path)
    if sp.exists():
        s = json.loads(sp.read_text(encoding="utf-8"))
        strat = {k: s.get(k) for k in ("built_at", "accepted", "horizon", "summary_ru", "exits", "entities", "data")}
        strat["rules"] = [{k: r.get(k) for k in ("id", "text", "accepted", "reason", "train", "test")}
                          for r in (s.get("wallet_trigger") or {}).get("rules", [])]
        strat["scanner_rules"] = [{k: r.get(k) for k in ("id", "text", "accepted", "reason", "train", "test")}
                                  for r in (s.get("scanner") or {}).get("rules", [])]
        strat["signature"] = (s.get("signature") or [])[:12]
        strat["cohort"] = len(s.get("cohort") or [])
    bt = db.kv_get("backtest:last")
    backtest = None
    if bt and bt.get("summary"):
        backtest = {"summary": bt["summary"], "random": {k: v for k, v in (bt.get("random") or {}).items() if k != "all"},
                    "beats_random_share": bt.get("beats_random_share"), "signals": bt.get("signals"), "ts": bt.get("ts")}
    sigs = [{"ts": r["ts"], "kind": r["kind"], "wallet": short_addr(r["wallet"]) if r["wallet"] else None,
             "symbol": r["symbol"], "rule": r["rule"], "status": r["status"], "reason": r["reason"]}
            for r in db.query("SELECT * FROM signals ORDER BY id DESC LIMIT 30")]
    pos = db.one("SELECT SUM(status='open') o, SUM(status='closed') c, SUM(CASE WHEN status='closed'"
                 " THEN realized_usd - fees_usd END) pnl FROM positions WHERE mode='paper'")
    return {
        "generated_at": now(), "exchange": ctx.cfg.cex.exchange, "market": ctx.cfg.cex.market,
        "counts": counts,
        "parsed_tx": db.one("SELECT COUNT(*) n FROM seen_tx")["n"], "legs": db.one("SELECT COUNT(*) n FROM legs")["n"],
        "bot_median_tx_per_day": spd[len(spd) // 2] if spd else None,
        "universe": uni, "wallets": wallets, "bots": bots, "strategy": strat, "backtest": backtest,
        "signals": sigs, "paper": {"open": (pos["o"] or 0) if pos else 0, "closed": (pos["c"] or 0) if pos else 0,
                                   "realized": (pos["pnl"] or 0) if pos else 0},
    }


def export(ctx, path: Path | None = None) -> Path:
    path = Path(path) if path else SITE_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_snapshot(ctx), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path
