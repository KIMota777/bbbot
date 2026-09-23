"""Поиск кандидатов: кто крупно торгует в пулах токенов, которые есть и на DEX, и на CEX.

Источники по убыванию качества:
1. Birdeye (бесплатный ключ): топ трейдеров токена по реализованному PnL за 30/90 дней;
2. GeckoTerminal (без ключа): последние крупные сделки пула с адресами —
   опрашиваем пул по кругу, копим встречаемость адресов;
3. импорт вручную — например, адреса из вкладки Top Traders на DexScreener.
"""
from __future__ import annotations

import logging
import time

from ..sources.cex import MULT_RE
from ..sources.dexscreener import OUR_TO_DS, clean_symbol, parse_url
from ..util import now

log = logging.getLogger("wsa.discover")

# не цели для копирования: стейблы, золото, обёртки мейджоров (на DEX их крутят арбитражёры)
NOT_TRADING_TARGETS = {"USDC", "USDT", "DAI", "USDE", "RLUSD", "PYUSD", "FDUSD", "XAUT", "PAXG", "WBTC",
                       "BTC", "ETH", "XRP", "LTC", "ADA", "XLM", "BNB", "DOGE", "AVAX", "SUI", "TRX", "BCH", "DOT"}


def resolve_pool(ctx, target: str) -> tuple[str, str, dict]:
    """Ссылка DexScreener, адрес пула или адрес токена -> (сеть, пул, пара DexScreener)."""
    chain = "solana"
    ident = target.strip()
    if "dexscreener.com" in ident:
        chain, ident = parse_url(ident)
    elif ":" in ident:
        chain, ident = ident.split(":", 1)
    pair = ctx.ds.pair(chain, ident)
    if pair:
        return chain, pair["pairAddress"], pair
    # не пара — возможно, токен: берём самый ликвидный пул
    best = ctx.ds.tokens(chain, [ident]).get(ident)
    if best:
        return chain, best["pairAddress"], best
    raise ValueError(f"не нашёл пару/токен {target} в сети {chain}")


def sample_pool(ctx, chain: str, pool: str, minutes: float, interval: int, min_usd: float,
                label: str | None = None, progress=None) -> dict:
    """Опрашивает сделки пула minutes минут, копит кандидатов в базе."""
    seen_tx: set[str] = set()
    counts: dict[str, dict] = {}
    t_end = time.time() + minutes * 60
    rounds = 0
    while True:
        rounds += 1
        try:
            trades = ctx.gt.pool_trades(chain, pool, min_usd)
        except Exception as e:
            log.warning("GeckoTerminal: %s", e)
            trades = []
        for t in trades:
            if not t["wallet"] or t["tx"] in seen_tx:
                continue
            seen_tx.add(t["tx"])
            c = counts.setdefault(t["wallet"], {"n": 0, "usd": 0.0, "first": t["ts"], "last": t["ts"]})
            c["n"] += 1
            c["usd"] += t["usd"]
            c["last"] = max(c["last"], t["ts"])
            c["first"] = min(c["first"], t["ts"])
        if progress:
            progress(rounds, len(counts), len(seen_tx))
        if time.time() + interval > t_end:
            break
        time.sleep(interval)
    new = 0
    for w, c in counts.items():
        if ctx.db.upsert_candidate(chain, w, source=f"pool:{pool}", pool=pool, trades=c["n"],
                                   volume=c["usd"], label=None):
            new += 1
    span = 0
    if seen_tx and counts:
        span = max(c["last"] for c in counts.values()) - min(c["first"] for c in counts.values())
    return {"wallets": len(counts), "new": new, "trades": len(seen_tx), "rounds": rounds,
            "span_sec": span, "counts": counts}


def from_birdeye(ctx, chain: str, token: str, time_frame: str = "30d") -> dict:
    if not ctx.birdeye.enabled:
        return {"skipped": "нет BIRDEYE_API_KEY"}
    rows = ctx.birdeye.top_traders(chain, token, time_frame=time_frame, sort_by="realized_pnl", pages=5)
    new = 0
    for r in rows:
        if (r.get("realized_pnl") or 0) <= 0:
            continue
        if ctx.db.upsert_candidate(chain, r["wallet"], source=f"birdeye:{token}", pool=token,
                                   trades=int(r.get("trades") or 0), volume=float(r.get("volume_usd") or 0)):
            new += 1
    return {"wallets": len(rows), "new": new}


def from_birdeye_gainers(ctx, chain: str, period: str = "30d") -> dict:
    if not ctx.birdeye.enabled:
        return {"skipped": "нет BIRDEYE_API_KEY"}
    rows = ctx.birdeye.gainers(chain, period=period)
    new = sum(1 for r in rows if ctx.db.upsert_candidate(chain, r["wallet"], source=f"birdeye:gainers:{period}",
                                                          trades=int(r.get("trades") or 0),
                                                          volume=float(r.get("volume_usd") or 0)))
    return {"wallets": len(rows), "new": new}


def import_wallets(ctx, chain: str, addresses: list[str], label: str | None = None,
                   source: str = "import") -> int:
    n = 0
    for a in addresses:
        a = a.strip()
        if not a or a.startswith("#"):
            continue
        if ctx.db.upsert_candidate(chain, a, source=source, label=label):
            n += 1
        elif label:
            ctx.db.set_wallet(chain, a, label=label)
    return n


def universe_tokens(ctx, chain: str, limit: int = 40) -> list[dict]:
    """Токены сети, которые торгуются на нашей CEX: по пересечению тикеров и сверке цены.

    Берём самые ликвидные пулы из поиска DexScreener по тикерам CEX. Результат
    кэшируется в таблице universe и используется для поиска кандидатов и сканера.
    """
    cex = ctx.cex
    cex.markets()
    cex.tickers()
    ranked = sorted(((s, cex.turnover_24h(s) or 0) for s in cex.symbols()), key=lambda x: -x[1])
    out = []
    ds_chain = OUR_TO_DS.get(chain, chain)
    min_dex_vol = ctx.cfg.discovery.get("universe_min_dex_vol", 100_000)
    seen_tokens = set()
    for sym, turnover in ranked:
        if len(out) >= limit:
            break
        if turnover < ctx.cfg.cex.min_turnover_24h_usd:
            break
        base = cex.ex.markets[sym]["base"].upper()
        mm = MULT_RE.match(base)
        mult = float(mm.group(1)) if mm else 1.0
        base = mm.group(2) if mm else base
        if "USD" in base or base in NOT_TRADING_TARGETS:
            continue
        try:
            pairs = ctx.ds.search(base)
        except Exception:
            continue
        px = cex.last_price(sym)
        best = None
        for p in pairs:
            if p.get("chainId") != ds_chain:
                continue
            if clean_symbol((p.get("baseToken") or {}).get("symbol")) != base:
                continue
            try:
                dpx = float(p.get("priceUsd") or 0)
            except ValueError:
                continue
            if not dpx or not px:
                continue
            if abs((px / mult) / dpx - 1) > ctx.cfg.cex.price_match_tolerance:
                continue
            vol = float((p.get("volume") or {}).get("h24") or 0)
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            # живая торговля, а не «нарисованная» ликвидность обёрнутых токенов:
            # у фальшивых пулов ликвидность в сотни миллионов при объёме $44 в сутки
            if vol < min_dex_vol or (vol and liq > 200 * vol):
                continue
            if best is None or vol > float((best.get("volume") or {}).get("h24") or 0):
                best = p
        if best and best["baseToken"]["address"] not in seen_tokens:
            seen_tokens.add(best["baseToken"]["address"])
            row = {"chain": chain, "token": best["baseToken"]["address"], "symbol": base, "exchange": cex.id,
                   "cex_symbol": sym, "best_pair": best["pairAddress"],
                   "liquidity_usd": (best.get("liquidity") or {}).get("usd"),
                   "vol24": (best.get("volume") or {}).get("h24"), "cex_turnover_24h": turnover}
            out.append(row)
            ctx.db.execute(
                "INSERT OR REPLACE INTO universe(chain, token, symbol, exchange, cex_symbol, multiplier, best_pair,"
                " liquidity_usd, vol24, cex_turnover_24h, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (chain, row["token"], base, cex.id, sym, mult, row["best_pair"], row["liquidity_usd"], row["vol24"],
                 turnover, now()))
    return out
