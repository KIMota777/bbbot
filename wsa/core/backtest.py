"""Бэктест стратегии на свечах CEX — так, как её исполнит бот.

Честный движок (те же принципы, что в bbbot):
- вход по открытию первой свечи, начавшейся после сигнала + задержка;
- комиссия тейкера и проскальзывание на каждой сделке;
- внутри свечи сначала проверяется стоп, потом тейк: если свеча задела
  и то и другое, считаем, что сначала сработал стоп (пессимистично);
- трейлинг после первого тейка не может оказаться выгоднее закрытия свечи;
- ограничение числа одновременных позиций и одна позиция на тикер;
- сравнение с теми же правилами выхода на СЛУЧАЙНЫХ моментах в тех же
  токенах: если стратегия не лучше случайности — преимущества нет.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..util import DAY, HOUR, mean, now, quantile
from .metrics import max_drawdown


@dataclass
class BtTrade:
    token: str
    symbol: str
    signal_ts: int
    entry_ts: int
    entry: float
    qty_frac: float = 1.0
    exits: list = field(default_factory=list)  # (ts, price, frac, reason)
    pnl_pct: float = 0.0
    exit_ts: int | None = None
    reason: str = ""


def simulate_trade(bars: list[list[float]], step: int, signal_ts: int, exits: dict, latency: int,
                   fee: float, slippage: float) -> BtTrade | None:
    """Одна сделка по готовому ряду свечей (ts, o, h, l, c, v)."""
    t_enter = signal_ts + latency
    i = next((k for k, b in enumerate(bars) if b[0] >= t_enter), None)
    if i is None:
        return None
    entry_ts, entry = bars[i][0], bars[i][1] * (1 + slippage)
    sl_pct = exits["sl"]
    tps = [(entry * (1 + t), f) for t, f in exits["tp"]]
    stop = entry * (1 + sl_pct)
    left = 1.0
    t_end = entry_ts + int(exits["time_stop_sec"])
    tr = BtTrade("", "", signal_ts, entry_ts, entry)
    trail = exits.get("trail_after_tp1")
    tp_done = 0
    for b in bars[i:]:
        ts, o, h, l, c = b[0], b[1], b[2], b[3], b[4]
        # 1) стоп первым (пессимистично)
        if l <= stop:
            px = min(o, stop) * (1 - slippage)  # гэп вниз — исполнение по открытию
            tr.exits.append((ts, px, left, "stop" if tp_done == 0 else "trail"))
            left = 0.0
            break
        # 2) тейки по очереди
        while tp_done < len(tps) and h >= tps[tp_done][0] and left > 1e-9:
            px_tp, frac = tps[tp_done]
            take = min(frac, left)
            tr.exits.append((ts, max(o, px_tp) * (1 - slippage), take, f"tp{tp_done + 1}"))
            left -= take
            tp_done += 1
            if trail and tp_done == 1:
                # после первого тейка стоп в безубыток, но не выше закрытия свечи
                stop = min(max(stop, entry * (1 + 2 * fee)), c)
        if left <= 1e-9:
            break
        # 3) тайм-стоп
        if ts + step >= t_end:
            tr.exits.append((ts + step, c * (1 - slippage), left, "time"))
            left = 0.0
            break
        if trail and tp_done >= 1:
            # подтягиваем стоп за ценой: половина пути от входа до максимума свечи,
            # но не выше закрытия (иначе стоп оказался бы выше рынка)
            stop = min(max(stop, entry + 0.5 * (h - entry)), c)
    if left > 1e-9:
        return None  # данных не хватило до выхода — сделку не засчитываем
    gross = sum(px * frac for _, px, frac, _ in tr.exits) / entry - 1
    tr.pnl_pct = gross - 2 * fee
    tr.exit_ts = tr.exits[-1][0]
    tr.reason = tr.exits[-1][3]
    return tr


def run(signals: list[dict], series_for, exits: dict, cfg, equity0: float = 1000.0) -> dict:
    """signals: [{ts, token, symbol, chain}] по времени. series_for(sig) -> (bars, step)."""
    bt = cfg.backtest
    tcfg = cfg.trading
    fee = bt.taker_fee
    trades: list[BtTrade] = []
    open_until: dict[str, int] = {}
    active: list[BtTrade] = []
    equity = equity0
    curve = [(signals[0]["ts"] if signals else now(), equity)]
    pos_pct = tcfg.max_position_pct
    sl_abs = abs(exits["sl"]) or 0.1
    risk_pct = min(pos_pct, tcfg.risk_per_trade / sl_abs)
    for s in sorted(signals, key=lambda x: x["ts"]):
        active = [t for t in active if (t.exit_ts or 0) > s["ts"]]
        if len(active) >= tcfg.max_open_positions:
            continue
        if open_until.get(s["symbol"], 0) > s["ts"]:
            continue
        got = series_for(s)
        if not got:
            continue
        bars, step = got
        tr = simulate_trade(bars, step, s["ts"], exits, bt.latency_sec, fee, bt.slippage)
        if tr is None:
            continue
        tr.token, tr.symbol = s["token"], s["symbol"]
        trades.append(tr)
        active.append(tr)
        open_until[s["symbol"]] = tr.exit_ts
    # эквити по времени закрытия (размер позиции от текущего капитала)
    for tr in sorted(trades, key=lambda t: t.exit_ts):
        equity *= 1 + risk_pct * tr.pnl_pct
        curve.append((tr.exit_ts, equity))
    return {"trades": trades, "curve": curve, "summary": summarize(trades, curve, equity0, risk_pct)}


def summarize(trades: list[BtTrade], curve, equity0: float, alloc: float) -> dict:
    pn = [t.pnl_pct for t in trades]
    wins = [p for p in pn if p > 0]
    losses = [p for p in pn if p <= 0]
    gp, gl = sum(wins), -sum(losses)
    reasons = {}
    for t in trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1
    return {
        "n": len(trades), "win_rate": len(wins) / len(trades) if trades else None,
        "avg_pct": mean(pn), "median_pct": quantile(pn, 0.5),
        "profit_factor": (gp / gl) if gl > 0 else (99.0 if gp > 0 else None),
        "total_return": curve[-1][1] / equity0 - 1 if curve else 0.0,
        "max_dd": max_drawdown([v for _, v in curve]),
        "alloc_per_trade": alloc,
        "avg_hold_h": mean([(t.exit_ts - t.entry_ts) / HOUR for t in trades]),
        "exit_reasons": reasons,
    }


def random_baseline(signals: list[dict], series_for, exits: dict, cfg, trials: int, seed: int = 11) -> dict:
    """Те же токены, то же число входов, те же выходы — но случайные моменты входа.

    Случайный момент берётся внутри доступного ряда своего же тикера, чтобы
    после входа хватало свечей до тайм-стопа.
    """
    rng = random.Random(seed)
    if not signals:
        return {}
    ranges = {}
    for s in signals:
        if s["symbol"] in ranges:
            continue
        got = series_for(s)
        if not got or not got[0]:
            continue
        bars = got[0]
        lo, hi = bars[0][0] + HOUR, bars[-1][0] - int(exits["time_stop_sec"])
        if hi > lo:
            ranges[s["symbol"]] = (lo, hi)
    pool = [s for s in signals if s["symbol"] in ranges]
    results = []
    for _ in range(trials):
        fake = [{**s, "ts": rng.randint(*ranges[s["symbol"]])} for s in pool]
        r = run(fake, series_for, exits, cfg)
        results.append(r["summary"]["total_return"])
    results.sort()
    return {"trials": trials, "median": quantile(results, 0.5), "p90": quantile(results, 0.9),
            "p10": quantile(results, 0.1), "all": results}


def percentile_of(value: float, dist: list[float]) -> float | None:
    if not dist:
        return None
    return sum(1 for x in dist if x < value) / len(dist)


def backtest_strategy(ctx, strategy: dict, only_test: bool = True, tf: str = "15m") -> dict:
    """Сигналы = входы группы, прошедшие принятые правила; свечи — торговой CEX."""
    from .strategy import Strategy, load_rows, add_consensus, entity_map, relationships, dedupe
    st = Strategy(strategy)
    cohort = [(c["chain"], c["address"]) for c in strategy["cohort"]]
    rows = load_rows(ctx, cohort)
    add_consensus(rows, entity_map(relationships(rows)))
    rows = dedupe(rows)
    split_ts = strategy["data"]["split_ts"]
    sigs = []
    for r in rows:
        if only_test and r["ts"] < split_ts:
            continue
        rule = st.match_wallet(r["f"]) if st.wallet_rules else None
        if st.wallet_rules and rule is None:
            continue
        m = ctx.tokens.cex_map(r["chain"], r["token"], which="trade")
        if not m or not m.get("listed_since") or m["listed_since"] > r["ts"]:
            continue
        sigs.append({"ts": r["ts"], "token": r["token"], "chain": r["chain"], "symbol": m["symbol"],
                     "multiplier": m.get("multiplier", 1.0), "rule": rule.id if rule else None})
    step = {"15m": 900, "1h": 3600}[tf]
    cache: dict[str, tuple] = {}

    def series_for(sig):
        k = sig["symbol"]
        if k not in cache:
            lo = min(s["ts"] for s in sigs if s["symbol"] == k) - DAY
            hi = min(now(), max(s["ts"] for s in sigs if s["symbol"] == k) + 8 * DAY)
            bars = ctx.cex.ohlcv(k, tf, lo, hi)
            cache[k] = (bars, step) if bars else None
        return cache[k]

    res = run(sigs, series_for, strategy["exits"], ctx.cfg)
    base = random_baseline(sigs, series_for, strategy["exits"], ctx.cfg, ctx.cfg.backtest.random_trials) if sigs else {}
    pct = percentile_of(res["summary"]["total_return"], base.get("all", []))
    return {"signals": len(sigs), "summary": res["summary"], "random": {k: v for k, v in base.items() if k != "all"},
            "beats_random_share": pct, "trades": [t.__dict__ for t in res["trades"]],
            "curve": res["curve"], "tf": tf, "only_test": only_test}
