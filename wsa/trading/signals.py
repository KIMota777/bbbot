"""Сигналы: покупка отслеживаемого кошелька -> проверка стратегией -> заявка движку.

Два источника:
- wallet — лидер купил токен на DEX, токен торгуется на нашей CEX, условия
  момента входа совпадают с принятым правилом стратегии;
- scanner — правило сканера совпало на тикере CEX само по себе (без лидера);
  включается, только если такие правила прошли проверку на отложенных данных.
"""
from __future__ import annotations

import json
import logging

from ..core.features import bucket_buy_index, classify, price_features, regime
from ..core.positions import build_round_trips
from ..core.pricing import native_id
from ..util import DAY, now

log = logging.getLogger("wsa.signals")


def live_features(ctx, chain: str, token: str, ts: int, price: float | None, usd: float | None = None,
                  wallet_metrics: dict | None = None, buy_index: int = 1) -> dict:
    """Те же признаки, что и в обучении, но на текущий момент."""
    s = ctx.pricer.token_series(chain, token, ts - 31 * DAY, ts, allow_dex=True)
    f = price_features(s, ts, price)
    btc = ctx.pricer._cex_series(ctx.ref, "BTC/USDT", "1h", ts - 9 * DAY, ts)
    nat = ctx.pricer.token_series(chain, native_id(chain), ts - 9 * DAY, ts, allow_dex=False)
    f.update(regime(btc, nat, ts))
    meta = ctx.tokens.meta(chain, token)
    created = ctx.tokens.created_at(chain, token)
    if created:
        f["token_age_days"] = max(0.0, (ts - created) / DAY)
    if meta.get("mc") and meta.get("price_usd") and price:
        f["mc_entry"] = meta["mc"] * price / meta["price_usd"]
    if meta.get("fdv") and meta.get("price_usd") and price:
        f["fdv_entry"] = meta["fdv"] * price / meta["price_usd"]
    f["liquidity_now"] = meta.get("liquidity_usd")
    f["sector"] = ctx.tokens.sector(chain, token)
    cexm = ctx.tokens.cex_map(chain, token, which="trade")
    f["cex_listed_now"] = bool(cexm)
    ls = (cexm or {}).get("listed_since")
    f["cex_listed"] = bool(ls and ls <= ts)
    if ls:
        f["days_since_listing"] = (ts - ls) / DAY
    h = ctx.tokens.holders(chain, token)
    if h.get("top10_pct") is not None:
        f["top10_pct"] = h["top10_pct"]
    g = ctx.tokens.holder_growth(chain, token, ts)
    if g is not None:
        f["holders_growth_24h"] = g
    if usd and wallet_metrics and wallet_metrics.get("median_position"):
        f["size_rel"] = usd / wallet_metrics["median_position"]
    f["buy_index"] = buy_index
    f["buy_index_bucket"] = bucket_buy_index(buy_index)
    f["entry_type"] = classify(f, ctx.cfg)
    return f


def consensus(ctx, strategy, chain: str, token: str, ts: int, wallet: str, window: int = DAY) -> int:
    ent = {c["address"]: c.get("entity", c["address"]) for c in strategy.d.get("cohort", [])}
    rows = ctx.db.query("SELECT DISTINCT wallet FROM legs WHERE chain=? AND token=? AND side='buy'"
                        " AND ts BETWEEN ? AND ?", (chain, token, ts - window, ts))
    me = ent.get(wallet, wallet)
    others = {ent.get(r["wallet"], r["wallet"]) for r in rows if r["wallet"] in ent} - {me}
    return len(others)


def buy_index_of(ctx, chain: str, wallet: str, token: str, sig: str) -> int:
    """Какая по счёту это покупка в текущем цикле кошелька (1 — первый вход)."""
    legs = [l for l in ctx.db.legs(chain, wallet) if l["token"] == token]
    trips, _ = build_round_trips(chain, wallet, legs)
    for t in trips:
        for i, b in enumerate(t.buys):
            if b.sig == sig:
                return i + 1
    return 1


def record(ctx, sig: dict, status: str, reason: str) -> int:
    cur = ctx.db.execute(
        "INSERT INTO signals(ts, kind, chain, wallet, token, sig, exchange, symbol, side, rule, score, features,"
        " status, reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sig.get("ts") or now(), sig["kind"], sig.get("chain"), sig.get("wallet"), sig.get("token"), sig.get("sig"),
         sig.get("exchange"), sig.get("symbol"), sig.get("side", "buy"), sig.get("rule"), sig.get("score"),
         json.dumps(sig.get("features") or {}, default=str), status, reason))
    return cur.lastrowid


def evaluate_wallet_buy(ctx, strategy, wallet_row: dict, leg: dict) -> tuple[dict, str, str]:
    """-> (сигнал, статус new|filtered, причина)."""
    cfg = ctx.cfg
    chain, wallet, token = leg["chain"], leg["wallet"], leg["token"]
    wt = strategy.d.get("wallet_trigger", {})
    sig = {"kind": "wallet", "chain": chain, "wallet": wallet, "token": token, "sig": leg["sig"],
           "ts": leg["ts"], "leader_price": leg.get("price"), "exchange": cfg.cex.exchange}
    score = wallet_row.get("score") or 0
    sig["score"] = score
    if score < wt.get("min_wallet_score", cfg.trading.min_wallet_score):
        return sig, "filtered", f"балл кошелька {score} ниже порога"
    cexm = ctx.tokens.cex_map(chain, token, which="trade")
    if not cexm:
        return sig, "filtered", "токена нет на нашей CEX"
    sig["symbol"] = cexm["symbol"]
    sig["multiplier"] = cexm.get("multiplier", 1.0)
    turnover = ctx.cex.turnover_24h(cexm["symbol"]) or 0
    if turnover < cfg.cex.min_turnover_24h_usd:
        return sig, "filtered", f"оборот на CEX ${turnover:,.0f} мал"
    bi = buy_index_of(ctx, chain, wallet, token, leg["sig"])
    if wt.get("first_buy_only", True) and bi > 1:
        return sig, "filtered", f"это добор №{bi}, не первый вход"
    a = ctx.db.analysis(chain, wallet) or {}
    f = live_features(ctx, chain, token, leg["ts"], leg.get("price"), leg.get("usd"), a.get("metrics"), bi)
    f["consensus_24h"] = consensus(ctx, strategy, chain, token, leg["ts"], wallet)
    sig["features"] = f
    if f["consensus_24h"] + 1 < wt.get("min_consensus", 1):
        return sig, "filtered", f"консенсус {f['consensus_24h'] + 1} < {wt.get('min_consensus')}"
    if strategy.wallet_rules:
        rule = strategy.match_wallet(f)
        if not rule:
            return sig, "filtered", "условия входа не совпали ни с одним принятым правилом"
        sig["rule"] = rule.id
    elif not wt.get("follow_all_if_no_rules"):
        return sig, "filtered", "в стратегии нет принятых правил — только наблюдение"
    return sig, "new", "ok"


def scan_market(ctx, strategy, max_symbols: int) -> list[dict]:
    """Сканер CEX: правила, работающие без лидера, на текущих свечах."""
    if not strategy.scanner_rules:
        return []
    cex = ctx.cex
    cex.tickers()
    ranked = sorted(((s, cex.turnover_24h(s) or 0) for s in cex.symbols()), key=lambda x: -x[1])
    ranked = [x for x in ranked if x[1] >= ctx.cfg.cex.min_turnover_24h_usd][:max_symbols]
    uni = {r["cex_symbol"]: dict(r) for r in ctx.db.query("SELECT * FROM universe WHERE exchange=?", (cex.id,))}
    out = []
    ts = now()
    btc = ctx.pricer._cex_series(ctx.ref, "BTC/USDT", "1h", ts - 9 * DAY, ts)
    for sym, _ in ranked:
        bars = cex.ohlcv(sym, "1h", ts - 31 * DAY, ts)
        if len(bars) < 200:
            continue
        from ..core.series import Series
        s = Series(bars, 3600, f"cex:{cex.id}:{sym}")
        px = cex.last_price(sym)
        f = price_features(s, ts, px)
        f.update(regime(btc, None, ts))
        u = uni.get(sym)
        if u:
            meta = ctx.tokens.meta(u["chain"], u["token"])
            f["liquidity_now"] = meta.get("liquidity_usd")
            f["sector"] = ctx.tokens.sector(u["chain"], u["token"])
            if meta.get("mc"):
                f["mc_entry"] = meta["mc"]
        f["cex_listed"] = True
        f["entry_type"] = classify(f, ctx.cfg)
        rule = strategy.match_scanner(f)
        if rule:
            out.append({"kind": "scanner", "ts": ts, "exchange": cex.id, "symbol": sym, "rule": rule.id,
                        "features": f, "token": (u or {}).get("token"), "chain": (u or {}).get("chain"),
                        "leader_price": None})
    return out
