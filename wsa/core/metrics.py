"""Метрики эффективности кошелька по восстановленным циклам сделок.

Два вида просадки:
- max_dd_capital — по собственному капиталу кошелька (пик вложенного + PnL);
- max_dd_norm — «нормированная»: каждая сделка на 10% условного капитала
  последовательно. Не зависит от размера кошелька, поэтому ею сравниваем
  кошельки между собой и с ней же будет работать наша стратегия.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from ..util import DAY, clean, mean, median, quantile, quantiles, safe_div, stdev, tstat, wilson_lb
from .positions import RoundTrip

WINDOWS = {"30d": 30, "90d": 90, "180d": 180, "365d": 365}
MIN_CAPITAL = 100.0  # меньше — ROI и просадка на капитале не показательны
NORM_ALLOC = 0.10


def _pf(pnls: list[float]) -> float | None:
    gp = sum(p for p in pnls if p > 0)
    gl = -sum(p for p in pnls if p < 0)
    if gl == 0:
        return None if gp == 0 else 99.0
    return gp / gl


def subset_stats(rts: list[RoundTrip]) -> dict:
    pnls = [r.realized for r in rts]
    pcts = [r.pnl_pct for r in rts if r.pnl_pct is not None]
    wins = sum(1 for p in pnls if p > 0)
    cost = sum(r.matched_cost for r in rts)
    return {
        "n": len(rts),
        "pnl": sum(pnls),
        "win_rate": safe_div(wins, len(rts), None),
        "pf": _pf(pnls),
        "roi": safe_div(sum(pnls), cost, None),
        "median_pct": median(pcts),
    }


def max_drawdown(values: list[float]) -> float:
    peak, dd = None, 0.0
    for v in values:
        if peak is None or v > peak:
            peak = v
        if peak and peak > 0:
            dd = max(dd, (peak - v) / peak)
    return dd


def deployed_capital_curve(legs: list[dict], valid_tokens: set[str] | None = None) -> float:
    """Пик одновременно вложенных денег (по себестоимости) — прокси капитала кошелька."""
    open_cost: dict[str, float] = defaultdict(float)
    open_qty: dict[str, float] = defaultdict(float)
    peak = 0.0
    for l in legs:
        if l["side"] not in ("buy", "sell") or not l.get("usd"):
            continue
        t = l["token"]
        if valid_tokens is not None and t not in valid_tokens:
            continue
        if l["side"] == "buy":
            open_cost[t] += l["usd"]
            open_qty[t] += l["qty"]
        else:
            q = open_qty[t]
            if q > 0:
                frac = min(1.0, l["qty"] / q)
                open_cost[t] -= open_cost[t] * frac
                open_qty[t] -= q * frac
        peak = max(peak, sum(v for v in open_cost.values() if v > 0))
    return peak


def compute_metrics(rts: list[RoundTrip], legs: list[dict], now_ts: int,
                    prices_now: dict[str, float] | None = None, orphan: dict | None = None,
                    sig_stats: dict | None = None) -> dict:
    prices_now = prices_now or {}
    closed = sorted([r for r in rts if r.valid], key=lambda r: r.close_ts)
    opened = [r for r in rts if r.status == "open" and r.qty > 0]
    trade_legs = [l for l in legs if l["side"] in ("buy", "sell") and l.get("usd")]
    buys = [l for l in trade_legs if l["side"] == "buy"]
    sells = [l for l in trade_legs if l["side"] == "sell"]

    first_ts = min((l["ts"] for l in trade_legs), default=None)
    last_ts = max((l["ts"] for l in trade_legs), default=None)
    history_days = (now_ts - first_ts) / DAY if first_ts else 0.0
    active_days = len({l["ts"] // DAY for l in trade_legs})
    swaps = len({l["sig"] for l in trade_legs})

    # реализованный PnL — по всем продажам, включая частичные в ещё открытых циклах
    realized = sum(f.pnl for r in rts for f in r.sells if f.pnl is not None)
    unrealized = 0.0
    open_value = 0.0
    for r in opened:
        px = prices_now.get(r.token)
        if px is None:
            continue
        remaining_cost = r.open_cost
        value = r.qty * px
        open_value += value
        unrealized += value - remaining_cost

    pnls = [r.realized for r in closed]
    pcts = [r.pnl_pct for r in closed if r.pnl_pct is not None]
    wins = [r for r in closed if r.realized > 0]
    losses = [r for r in closed if r.realized <= 0]
    gp = sum(r.realized for r in wins)
    gl = -sum(r.realized for r in losses)
    matched_cost = sum(r.matched_cost for r in closed)
    peak_deployed = deployed_capital_curve(legs)

    # просадка по капиталу кошелька
    eq_events = sorted(((f.ts, f.pnl) for r in rts for f in r.sells if f.pnl is not None), key=lambda x: x[0])
    base = max(peak_deployed, MIN_CAPITAL)
    cum, curve = 0.0, [base]
    for _, p in eq_events:
        cum += p
        curve.append(base + cum)
    dd_capital = max_drawdown(curve)
    # нормированная просадка
    eq, norm_curve = 1.0, [1.0]
    for r in closed:
        if r.pnl_pct is not None:
            eq *= 1 + NORM_ALLOC * max(r.pnl_pct, -1.0)
            norm_curve.append(eq)
    dd_norm = max_drawdown(norm_curve)

    holds = [r.hold_sec for r in closed if r.hold_sec is not None]
    positions = [r.cost for r in closed if r.cost > 0]

    # концентрация результата
    by_token: dict[str, float] = defaultdict(float)
    for r in closed:
        by_token[r.token] += r.realized
    pos_total = sum(v for v in by_token.values() if v > 0)
    top1_token, top1_val = (max(by_token.items(), key=lambda kv: kv[1]) if by_token else (None, 0.0))
    top3_trades = sum(sorted((p for p in pnls if p > 0), reverse=True)[:3])

    # окна и месяцы
    windows = {}
    for name, days in WINDOWS.items():
        sub = [r for r in closed if r.close_ts >= now_ts - days * DAY]
        windows[name] = subset_stats(sub)
    months: dict[str, list] = defaultdict(list)
    for r in closed:
        m = datetime.fromtimestamp(r.close_ts, tz=timezone.utc).strftime("%Y-%m")
        months[m].append(r.realized)
    monthly = [{"month": m, "pnl": sum(v), "n": len(v)} for m, v in sorted(months.items())]
    months_eligible = [m for m in monthly if m["n"] >= 2]
    win_windows = [w for w in windows.values() if w["n"] >= 3]

    # одновременно открытых позиций
    events = []
    for r in rts:
        events.append((r.open_ts, 1))
        if r.close_ts:
            events.append((r.close_ts, -1))
    cur = mx = 0
    for _, d in sorted(events):
        cur += d
        mx = max(mx, cur)

    buy_ts = sorted(l["ts"] for l in buys)
    gaps = [b - a for a, b in zip(buy_ts, buy_ts[1:])]

    # сделки «в одном слоте» — сэндвичи/арбитраж
    fast = sum(1 for r in closed if r.duration is not None and r.duration <= 10)

    unmatched_usd = sum(r.unmatched_usd for r in rts) + (orphan or {}).get("sell_usd", 0.0)
    sell_usd = sum(l["usd"] for l in sells)
    tin_usd = sum(l["usd"] for l in legs if l["side"] == "tin" and l.get("usd"))

    m = {
        "first_ts": first_ts, "last_ts": last_ts,
        "history_days": history_days, "active_days": active_days,
        "n_swaps": swaps, "n_buys": len(buys), "n_sells": len(sells),
        "n_tokens": len({l["token"] for l in trade_legs}),
        "swaps_per_day": safe_div(swaps, max(active_days, 1)),
        "trades_per_week": safe_div(len(rts), max(history_days / 7, 1)),
        "trades_per_month": safe_div(len(rts), max(history_days / 30, 1)),
        "volume_usd": sum(l["usd"] for l in trade_legs),
        "buy_volume": sum(l["usd"] for l in buys), "sell_volume": sell_usd,
        "buy_sell_ratio": safe_div(len(buys), len(sells), None),
        "avg_buy_interval_sec": mean(gaps), "median_buy_interval_sec": median(gaps),
        "closed": len(closed), "open": len(opened), "round_trips": len(rts),
        "realized_pnl": realized, "unrealized_pnl": unrealized, "total_pnl": realized + unrealized,
        "open_value": open_value,
        "gross_profit": gp, "gross_loss": gl,
        "profit_factor": (gp / gl) if gl > 0 else (99.0 if gp > 0 else None),
        "wins": len(wins), "losses": len(losses),
        "win_rate": safe_div(len(wins), len(closed), None),
        "win_rate_lb": wilson_lb(len(wins), len(closed)),
        "avg_win": mean([r.realized for r in wins]), "avg_loss": mean([r.realized for r in losses]),
        "avg_win_pct": mean([r.pnl_pct for r in wins]), "avg_loss_pct": mean([r.pnl_pct for r in losses]),
        "median_pnl_pct": median(pcts), "expectancy_pct": mean(pcts), "stdev_pct": stdev(pcts),
        "tstat_pct": tstat(pcts),
        "payoff": safe_div(mean([r.realized for r in wins]) or 0, abs(mean([r.realized for r in losses]) or 0), None),
        "roi_trades": safe_div(sum(pnls), matched_cost, None),
        # ROI на капитал — только если вложено хоть сколько-то заметно: при крошечном
        # «капитале» (большинство покупок не оценены) деление даёт тысячи процентов
        "roi_capital": safe_div(realized, peak_deployed, None) if peak_deployed >= MIN_CAPITAL else None,
        "peak_deployed": peak_deployed,
        "avg_position": mean(positions), "median_position": median(positions),
        "max_dd_capital": dd_capital, "max_dd_norm": dd_norm,
        "hold": {"avg": mean(holds), **quantiles(holds)},
        "duration_median": median([r.duration for r in closed]),
        "max_concurrent": mx,
        "top1_token": top1_token, "top1_token_share": safe_div(max(top1_val, 0), pos_total, None),
        "top3_trades_share": safe_div(top3_trades, gp, None),
        "profitable_tokens": sum(1 for v in by_token.values() if v > 0),
        "unmatched_share": safe_div(unmatched_usd, sell_usd, 0.0),
        "transfer_in_share": safe_div(tin_usd, sum(l["usd"] for l in buys) + tin_usd, 0.0),
        "fast_share": safe_div(fast, len(closed), 0.0),
        "windows": windows,
        "monthly": monthly,
        "positive_months_share": safe_div(sum(1 for x in months_eligible if x["pnl"] > 0), len(months_eligible), None),
        "positive_windows_share": safe_div(sum(1 for w in win_windows if w["pnl"] > 0), len(win_windows), None),
    }
    if sig_stats:
        m.update({k: v for k, v in sig_stats.items()})
    return m
