# -*- coding: utf-8 -*-
"""Считает аналитику для страниц ботов и сигналов:
  - кривая капитала с реинвестом от $50 (маржа = 25% капитала, как в отборе);
  - кривая просадки (% от пика капитала);
  - помесячные бары доходности (% на фикс-базе $20 — сопоставимо со всеми
    прежними отчётами);
  - статистика "убытки к доходам": profit factor, средняя прибыльная /
    убыточная сделка, лучшая/худшая, макс. серии, лучший/худший месяц.
  - ЦИКЛЫ БОТОВ ЗА ВСЮ ИСТОРИЮ для графика на странице бота: вход, каждая
    доливка сетки, средняя цена позиции после каждой доливки, стоп при
    входе, тейк от средней, выход и PnL (webapp/data/bot_trades_<SYM>.json).

Для 5 финальных ботов и рабочих сигнальных сетапов.
Результат: webapp/data/analytics_<key>.json (key: bot_<SYM> | sig_<name>),
           webapp/data/bot_trades_<SYM>.json.
Переиспользует функции build_pnl_curves — данные гарантированно совпадают
со страницей /pnl.

Запуск:
    python build_analytics.py            # всё + самопроверка совпадения с /pnl
    python build_analytics.py bots       # только боты (быстрее)
    python build_analytics.py --no-check # без сверки с build_pnl_curves
"""

import json
import os
import sys
import time
from collections import defaultdict

import build_pnl_curves as bpc
import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd
import signal_engine as se

OUT_DIR = os.path.join("webapp", "data")
SLEEVE0 = 50.0
BT_BASE = 20.0
MONTH = 30 * 86400


def analyze(t0, pnls, extra=None):
    """pnls: [(ts_сек, pnl_$5маржи)]. Возвращает полный набор аналитики."""
    eq, peak = SLEEVE0, SLEEVE0
    equity = [[t0, round(SLEEVE0, 2)]]
    drawdown = [[t0, 0.0]]
    monthly = defaultdict(float)
    wins, losses = [], []
    streak, max_win_streak, max_loss_streak = 0, 0, 0

    for ts, pnl in pnls:
        eq *= (1 + pnl / BT_BASE)
        peak = max(peak, eq)
        equity.append([ts, round(eq, 2)])
        drawdown.append([ts, round(-(peak - eq) / peak * 100, 2)])
        monthly[(ts - t0) // MONTH] += pnl
        if pnl > 0:
            wins.append(pnl)
            streak = streak + 1 if streak >= 0 else 1
            max_win_streak = max(max_win_streak, streak)
        elif pnl < 0:
            losses.append(pnl)
            streak = streak - 1 if streak <= 0 else -1
            max_loss_streak = max(max_loss_streak, -streak)

    n_months = max(monthly) + 1 if monthly else 1
    monthly_pts = []
    for m in range(n_months):
        pct = monthly.get(m, 0.0) / BT_BASE * 100
        monthly_pts.append([t0 + m * MONTH, round(pct, 2)])
    month_vals = [p[1] for p in monthly_pts]

    gross_p, gross_l = sum(wins), -sum(losses)
    stats = dict(
        final_usd=round(eq, 2),
        final_pct=round((eq / SLEEVE0 - 1) * 100, 1),
        max_dd=round(max((-d[1] for d in drawdown), default=0), 1),
        trades=len(pnls), wins=len(wins), losses=len(losses),
        wr=round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
        profit_factor=round(gross_p / gross_l, 2) if gross_l > 0 else None,
        gross_profit=round(gross_p, 2), gross_loss=round(gross_l, 2),
        avg_win=round(gross_p / len(wins), 3) if wins else 0,
        avg_loss=round(-gross_l / len(losses), 3) if losses else 0,
        best_trade=round(max(wins), 3) if wins else 0,
        worst_trade=round(min(losses), 3) if losses else 0,
        max_win_streak=max_win_streak, max_loss_streak=max_loss_streak,
        best_month=round(max(month_vals), 2) if month_vals else 0,
        worst_month=round(min(month_vals), 2) if month_vals else 0,
        months_pos=sum(1 for v in month_vals if v > 0),
        months_total=len(month_vals))
    if extra:
        stats.update(extra)
    return dict(equity=bpc.downsample(equity),
                drawdown=bpc.downsample(drawdown),
                monthly=monthly_pts, stats=stats)


def rp(v):
    """Цена в JSON: 7 значащих цифр. BTC 104532.1 и DOGE 0.2345678 одинаково
    точны, а файл сделок не раздувается хвостами float."""
    return None if v is None else float(f"{v:.7g}")


def bot_run(sym, p, pct5):
    """Один прогон движка -> (t0, pnls, cycles, genome, candles).

    pnls собираются ровно так же, как в build_pnl_curves.bot_pnls (те же
    свечи, тот же геном, тот же фильтр) — цифры страницы бота и /pnl
    совпадают сделка в сделку; сверка включена в main().

    cycles — то, чего в pnls нет: где был вход, где встали лимитки сетки,
    какой стала средняя цена (от неё считаются стоп и тейк) и чем цикл
    закончился.
    """
    interval = str(p.get("interval", "15"))
    bars_per_day = max(4, 1440 // int(interval))
    g = e7.cfg_to_genome(p, "final")
    for k, v in e8.OFF8.items():
        g.setdefault(k, v)
    candles = ev.fetch(sym, interval, 1150)
    aux = e8.make_aux_builder(pct5, bars_per_day)(sym, candles)
    pre = e2.prep(candles)
    filt = e8.make_filter8(g, aux)
    old_lev, old_bpd = e2.LEV, e2.BARS_PER_DAY
    e2.LEV = p.get("lev", 5)
    e2.BARS_PER_DAY = bars_per_day
    try:
        events = []
        e2.run5(candles, pre, g, entry_filter=filt, events=events)
    finally:
        e2.LEV, e2.BARS_PER_DAY = old_lev, old_bpd

    pnls = [(e["t"] // 1000, e["pnl"]) for e in events if e["type"] == "close"]
    cycles = build_cycles(events, candles, g)
    return candles[0][0] // 1000, pnls, cycles, g, candles


def build_cycles(events, candles, g):
    """События движка -> циклы со средней ценой, стопом и тейком.

    Средняя считается ровно как в evolution2.close_pos: доли маржи по
    коленам w_k = mult^k, объём кolena q_k = m_k * LEV / цена, поэтому
        avg = SUM(w_k) / SUM(w_k / цена_k)
    (LEV и MARGIN — общие множители, сокращаются). Стоп берём тот, что
    движок ставит при входе: за экстремумом окна с запасом sweep. Тейк —
    от средней, как в движке (tp_pre = avg * (1 + sgn * tp)).
    """
    rlow, rhigh = ev.rolling_extremes(candles, g["window"])
    idx = {c[0]: i for i, c in enumerate(candles)}
    w = [g["mult"] ** k for k in range(g["levels"])]

    def finish(c):
        prices = [c["_entry_raw"]] + [a[1] for a in c["adds"]]
        num = den = 0.0
        avgs = []
        for k, px in enumerate(prices):
            wk = w[k] if k < len(w) else w[-1]
            num += wk
            den += wk / px
            avgs.append(num / den)
        for k, a in enumerate(c["adds"]):
            a.append(rp(avgs[k + 1]))
        sgn = 1 if c["side"] == "L" else -1
        c["avg"] = rp(avgs[-1])
        c["tp"] = rp(avgs[-1] * (1 + sgn * g["tp"]))
        i = idx.get(c.pop("_bar_ms"))
        if i is not None:
            c["stop"] = rp(rlow[i] * (1 - g["sweep"]) if sgn == 1
                           else rhigh[i] * (1 + g["sweep"]))
        c.pop("_entry_raw")
        return c

    out, cur = [], None
    for e in events:
        t = e["t"] // 1000
        if e["type"] == "entry":
            if cur is not None:              # не должно случаться
                out.append(finish(cur))
            cur = dict(side=e["side"], entry_ts=t, entry=rp(e["price"]),
                       _entry_raw=e["price"], _bar_ms=e["t"], adds=[])
        elif e["type"] == "add" and cur is not None:
            cur["adds"].append([t, rp(e["price"])])
        elif e["type"] == "close" and cur is not None:
            cur["exit_ts"] = t
            cur["exit"] = rp(e["price"])
            cur["pnl"] = round(e["pnl"], 4)
            cur["reason"] = e.get("reason") or "?"
            if e.get("liq"):
                cur["liq"] = 1
            out.append(finish(cur))
            cur = None
    if cur is not None:
        out.append(finish(cur))
    for n, c in enumerate(out, 1):
        c["i"] = n
    return out


def write_bot_trades(sym, p, g, cycles, candles, hold_ts):
    """webapp/data/bot_trades_<SYM>.json — источник графика страницы бота."""
    path = os.path.join(OUT_DIR, f"bot_trades_{sym}.json")
    n_hold = sum(1 for c in cycles if c["entry_ts"] >= hold_ts)
    data = dict(
        symbol=sym, interval=str(p.get("interval", "15")),
        lev=p.get("lev", 5), levels=g["levels"], tp_pct=g["tp"],
        step=g["step"], sweep=g["sweep"], be_move=int(g.get("be_move", 0)),
        holdout_from=hold_ts,
        first_ts=candles[0][0] // 1000, last_ts=candles[-1][0] // 1000,
        n=len(cycles), n_holdout=n_hold,
        generated=time.strftime("%Y-%m-%d %H:%M"),
        trades=cycles)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    kb = os.path.getsize(path) / 1024
    print(f"  циклов {len(cycles)} (на холдоуте {n_hold}), "
          f"{os.path.basename(path)} {kb:.0f} КБ")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    args = sys.argv[1:]
    only_bots = "bots" in args
    check = "--no-check" not in args
    pct5 = xd.fetch_daily_pct5()
    hold_ts = bpc.holdout_start_ts()
    print(f"граница экзамена: {time.strftime('%Y-%m-%d', time.gmtime(hold_ts))}")

    for sym, modes in config.SYMBOL_PARAMS.items():
        p = modes.get("final")
        if not p:
            continue
        print(f"bot_{sym}...")
        t0, pnls, cycles, g, candles = bot_run(sym, p, pct5)
        if check:
            # сверка с тем, что рисует /pnl: один и тот же список сделок
            ref_t0, ref = bpc.bot_pnls(sym, p, pct5)
            ok = (ref_t0 == t0 and ref == pnls)
            print(f"  сверка с build_pnl_curves: {'OK' if ok else 'РАСХОЖДЕНИЕ'}"
                  f" ({len(pnls)} сделок)")
            if not ok:
                raise SystemExit(f"{sym}: список сделок разошёлся с /pnl — "
                                 "предпосчёт остановлен")
        data = analyze(t0, pnls, extra=dict(lev=p.get("lev", 5)))
        with open(os.path.join(OUT_DIR, f"analytics_bot_{sym}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        write_bot_trades(sym, p, g, cycles, candles, hold_ts)

    if only_bots:
        print("-> webapp/data/analytics_bot_*.json, bot_trades_*.json")
        return

    with open("signal_setups.json", encoding="utf-8") as fh:
        setups = json.load(fh)
    c4 = ev.fetch("BTCUSDT", "240", 1150)
    c15 = ev.fetch("BTCUSDT", "15", 1150)
    ts15 = [c[0] for c in c15]
    ctx = se.prep_context(c4)
    for name, rec in setups.items():
        if not rec.get("enabled"):
            continue
        print(f"sig_{name}...")
        r = se.run_setup(name, rec["genome"], c4, ctx, c15, ts15,
                         rec.get("rec_lev", 15))
        t0 = c4[0][0] // 1000
        pnls = [(t["exit_ts"] // 1000, t["pnl"]) for t in r["trades"]]
        reasons = defaultdict(int)
        holds = []
        for t in r["trades"]:
            reasons[t["reason"]] += 1
            holds.append(t["hold_h"])
        extra = dict(lev=rec.get("rec_lev", 15),
                     n_tp=reasons.get("tp", 0), n_stop=reasons.get("stop", 0),
                     n_timeout=reasons.get("timeout", 0),
                     n_liq=reasons.get("liq", 0),
                     avg_hold_h=round(sum(holds) / len(holds), 1) if holds else 0)
        data = analyze(t0, pnls, extra=extra)
        with open(os.path.join(OUT_DIR, f"analytics_sig_{name}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)

    print("-> webapp/data/analytics_*.json, bot_trades_*.json")


if __name__ == "__main__":
    main()
