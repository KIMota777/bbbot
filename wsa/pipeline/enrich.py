"""Обогащение сделок кошелька: признаки каждого входа и выхода, форвард-доходности, профиль.

Ряды цен: для токенов с листингом на CEX — часовые свечи биржи (годы истории),
для остальных — свечи DEX-пула из GeckoTerminal (≤180 дней, ~10 запросов/мин,
поэтому только для самых оборотных токенов кошелька).
"""
from __future__ import annotations

import json
import logging
from bisect import bisect_left
from collections import defaultdict

from ..core.features import bucket_buy_index, classify, forward, price_features, regime
from ..core.positions import build_round_trips
from ..core.pricing import native_id
from ..core.profile import build_profile
from ..util import DAY, now
from .scan import trade_legs

log = logging.getLogger("wsa.enrich")


def token_series_map(ctx, chain: str, trips, max_dex_tokens: int, max_sector_lookups: int):
    """Ряды цен и справка по всем токенам кошелька за один проход."""
    vol: dict[str, float] = defaultdict(float)
    span: dict[str, list[int]] = {}
    for t in trips:
        vol[t.token] += t.cost
        end = t.close_ts or now()
        lo, hi = span.get(t.token, [t.open_ts, end])
        span[t.token] = [min(lo, t.open_ts), max(hi, end)]
    ctx.tokens.refresh(chain, list(vol))
    series, meta = {}, {}
    dex_budget = max_dex_tokens
    sector_budget = max_sector_lookups
    for tok, _ in sorted(vol.items(), key=lambda kv: -kv[1]):
        t0, t1 = span[tok]
        t0 -= 31 * DAY
        t1 = min(now(), t1 + 31 * DAY)
        m = dict(ctx.tokens.meta(chain, tok, max_age=6 * 3600) or {})
        cexm = ctx.tokens.cex_map(chain, tok, which="trade")
        refm = ctx.tokens.cex_map(chain, tok, which="ref") if ctx.ref is not ctx.cex else cexm
        s = None
        if refm or tok == native_id(chain):
            s = ctx.pricer.token_series(chain, tok, t0, t1)
        elif dex_budget > 0 and ctx.cfg.context.dex_ohlcv:
            dex_budget -= 1
            s = ctx.pricer.dex_series(chain, tok, t0, t1)
        if cexm or sector_budget > 0:
            if not cexm:
                sector_budget -= 1
            m["sector"] = ctx.tokens.sector(chain, tok)
        else:
            m["sector"] = m.get("sector") or "Unknown"
        m["created_at"] = ctx.tokens.created_at(chain, tok) if (cexm or s is not None) else m.get("created_at")
        m["cex"] = cexm
        series[tok] = s
        meta[tok] = m
    return series, meta


def snapshot_at(ctx, chain: str, token: str, ts: int, window: int = 2 * 3600) -> dict | None:
    """Ближайший снимок рынка токена не позже ts (в пределах двух часов)."""
    rows = ctx.db.query("SELECT * FROM token_snapshots WHERE chain=? AND address=? AND ts BETWEEN ? AND ?"
                        " ORDER BY ts DESC", (chain, token, ts - window, ts + 60))
    if not rows:
        return None
    out: dict = {}
    for r in rows:  # от свежих к старым: берём первое непустое значение каждого поля
        for k in ("liquidity", "mc", "holders", "top10_pct", "price"):
            if out.get(k) is None and r[k] is not None:
                out[k] = r[k]
    return out


def enrich_wallet(ctx, chain: str, address: str, max_dex_tokens: int = 15, max_sector_lookups: int = 12) -> dict:
    cfg = ctx.cfg
    legs = trade_legs(ctx, chain, address)
    trips, _ = build_round_trips(chain, address, legs)
    a = ctx.db.analysis(chain, address) or {}
    m = a.get("metrics") or {}
    if not trips:
        return {"entries": 0}
    series, meta = token_series_map(ctx, chain, trips, max_dex_tokens, max_sector_lookups)
    T0 = min(t.open_ts for t in trips) - 8 * DAY
    T1 = now()
    btc = ctx.pricer._cex_series(ctx.ref, "BTC/USDT", "1h", T0, T1)
    nat = ctx.pricer.token_series(chain, native_id(chain), T0, T1)
    median_pos = m.get("median_position") or None
    peak = m.get("peak_deployed") or None

    entries, exits = [], []
    prev_rts: dict[str, int] = defaultdict(int)
    buy_times = sorted(l["ts"] for l in legs if l["side"] == "buy" and l.get("usd"))
    for rt in sorted(trips, key=lambda t: t.open_ts):
        s = series.get(rt.token)
        mt = meta.get(rt.token) or {}
        cexm = mt.get("cex")
        created = mt.get("created_at")
        for i, b in enumerate(rt.buys):
            if not b.usd or not b.price:
                continue
            f = price_features(s, b.ts, b.price)
            f.update(regime(btc, nat, b.ts))
            if created and created <= b.ts + 3600:
                f["token_age_days"] = max(0.0, (b.ts - created) / DAY)
            if mt.get("mc") and mt.get("price_usd"):
                f["mc_entry"] = mt["mc"] * b.price / mt["price_usd"]
            if mt.get("fdv") and mt.get("price_usd"):
                f["fdv_entry"] = mt["fdv"] * b.price / mt["price_usd"]
            f["liquidity_now"] = mt.get("liquidity_usd")
            snap = snapshot_at(ctx, chain, rt.token, b.ts)
            if snap:
                # живой снимок рынка на момент входа (копится трекером) точнее «текущих» значений
                if snap.get("liquidity") is not None:
                    f["liquidity_now"] = snap["liquidity"]
                if snap.get("mc") is not None:
                    f["mc_entry"] = snap["mc"]
                if snap.get("top10_pct") is not None:
                    f["top10_pct"] = snap["top10_pct"]
            g = ctx.tokens.holder_growth(chain, rt.token, b.ts)
            if g is not None:
                f["holders_growth_24h"] = g
            f["sector"] = mt.get("sector") or "Unknown"
            ls = (cexm or {}).get("listed_since")
            f["cex_listed"] = bool(ls and ls <= b.ts)
            f["cex_listed_now"] = bool(cexm)
            if ls:
                f["days_since_listing"] = (b.ts - ls) / DAY
            f["size_usd"] = b.usd
            f["size_rel"] = b.usd / median_pos if median_pos else None
            f["size_pct_capital"] = b.usd / peak if peak else None
            f["buy_index"] = i + 1
            f["buy_index_bucket"] = bucket_buy_index(i + 1)
            f["prev_rts"] = prev_rts[rt.token]
            k = bisect_left(buy_times, b.ts)
            if k > 0:
                f["hours_since_prev_buy"] = (b.ts - buy_times[k - 1]) / 3600
            f["entry_type"] = classify(f, cfg)
            fwd = forward(s, b.ts, b.price)
            outcome = {"rt_pnl_pct": rt.pnl_pct, "rt_pnl": rt.realized, "rt_status": rt.status,
                       "rt_hold": rt.hold_sec, "rt_valid": rt.valid}
            entries.append({"sig": b.sig, "idx": b.idx, "token": rt.token, "ts": b.ts, "usd": b.usd,
                            "price": b.price, "rt_key": rt.key, "buy_index": i + 1,
                            "entry_type": f["entry_type"], "features": f, "fwd": fwd, "outcome": outcome,
                            "price_src": s.src if s else "none"})
        for j, sf in enumerate(rt.sells):
            if not sf.usd or not sf.price:
                continue
            pf = price_features(s, sf.ts, sf.price)
            fw = forward(s, sf.ts, sf.price)
            exits.append({"token": rt.token, "ts": sf.ts, "gain": sf.gain, "hold": sf.hold, "usd": sf.usd,
                          "sell_index": j + 1, "n_sells": rt.n_sells, "is_last": j == rt.n_sells - 1,
                          "frac": (sf.qty / rt.max_qty) if rt.max_qty else None,
                          "ret_24h_before": pf.get("ret_24h"), "vol_ratio": pf.get("vol_ratio"),
                          "after_24h": fw.get("24h"), "after_7d": fw.get("7d"), "rt_pnl_pct": rt.pnl_pct,
                          "rt_valid": rt.valid})
        prev_rts[rt.token] += 1

    rows = [(chain, address, e["sig"], e["idx"], e["token"], e["ts"], e["usd"], e["price"], e["rt_key"],
             e["buy_index"], e["entry_type"], json.dumps(e["features"]), json.dumps(e["fwd"]),
             json.dumps(e["outcome"]), e["price_src"], now()) for e in entries]
    with ctx.db.tx() as c:
        c.execute("DELETE FROM entries WHERE chain=? AND wallet=?", (chain, address))
        c.executemany(
            "INSERT INTO entries(chain, wallet, sig, idx, token, ts, usd, price, rt_key, buy_index, entry_type,"
            " features, fwd, outcome, price_src, computed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        for e in entries:
            if e["buy_index"] == 1:
                c.execute("UPDATE round_trips SET entry_type=? WHERE chain=? AND wallet=? AND rt_key=?",
                          (e["entry_type"], chain, address, e["rt_key"]))
    profile = build_profile(m, trips, entries, exits, meta, cfg, legs=legs)
    ctx.db.save_analysis(chain, address, a.get("score"), m, a.get("flags") or {}, profile)
    return {"entries": len(entries), "exits": len(exits), "profile": profile}
