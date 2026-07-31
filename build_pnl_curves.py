# -*- coding: utf-8 -*-
"""Строит кривые капитала для страницы /pnl — реальная картина С РЕИНВЕСТОМ.

Модель (по условию владельца): у КАЖДОЙ стратегии свой стартовый капитал
$50 (5 ботов + 2 рабочих сигнальных сетапа = 7 линий, суммарно $350).
Маржа сделки = 25% текущего капитала стратегии — та же доля, что в отборе
($5 от $20), поэтому риск на сделку в % не меняется, а PnL масштабируется
точно (объём/комиссии/фандинг/ликвидация линейны от маржи):
    капитал *= (1 + pnl_сделки / 20)

Портфель — две линии от $350:
  - «без ребаланса»: сумма семи независимых капиталов;
  - «ежемесячный ребаланс»: капитал общий, в конце месяца прибыль всех
    распределяется на всех (r_мес = суммарный PnL / (7 x $20-база)).
    Даёт бонус диверсификации; умножает и ошибку бэктеста — смотри обе.

Результат: webapp/data/pnl_curves.json. Пересчёт после смены конфигов.
"""

import json
import os
from collections import defaultdict

import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import evolution12 as e12
import ext_data as xd
import signal_engine as se

OUT = os.path.join("webapp", "data", "pnl_curves.json")
MAX_POINTS = 1600
SLEEVE0 = 50.0       # стартовый капитал КАЖДОЙ стратегии
BT_BASE = 20.0       # база бэктеста: $20 капитала, $5 маржи (25%)
MONTH = 30 * 86400

COLORS = {
    "DOGEUSDT": "#f0b90b", "LTCUSDT": "#4aa8ff", "BTCUSDT": "#ff7a30",
    "ETHUSDT": "#c88cff", "SOLUSDT": "#46C186",
    "pump_short": "#e056a2", "range_long": "#7ee0d2",
}
# бенчмарки "купил на $50 и держал" (yfinance, дневные закрытия)
BENCHMARKS = [
    ("bench_gold", "Золото — холд $50", "GC=F", "#d4af37"),
    ("bench_silver", "Серебро — холд $50", "SI=F", "#a8b0bd"),
    ("bench_spx", "S&P 500 — холд $50", "^GSPC", "#6f8ef2"),
]
RU_SETUPS = {
    "range_long": "Сигналы: лонг от низа боковика",
    "pump_short": "Сигналы: шорт после пампа",
}


def downsample(points):
    if len(points) <= MAX_POINTS:
        return points
    step = len(points) / MAX_POINTS
    out = [points[int(i * step)] for i in range(MAX_POINTS)]
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def bot_pnls(sym, p, pct5):
    """[(ts_сек, pnl_в_$5-масштабе), ...] по закрытым сделкам бота."""
    interval = str(p.get("interval", "15"))
    bars_per_day = max(4, 1440 // int(interval))
    g = e7.cfg_to_genome(p, "final")
    for k, v in e8.OFF8.items():
        g.setdefault(k, v)
    candles = ev.fetch(sym, interval, 1150)
    aux = e12.make_aux_builder(pct5, bars_per_day)(sym, candles)
    pre = e2.prep(candles)
    filt = e12.make_filter12(g, aux)
    old_lev, old_bpd = e2.LEV, e2.BARS_PER_DAY
    e2.LEV = p.get("lev", 5)
    e2.BARS_PER_DAY = bars_per_day
    try:
        events = []
        e2.run5(candles, pre, g, entry_filter=filt, events=events)
    finally:
        e2.LEV, e2.BARS_PER_DAY = old_lev, old_bpd
    start_ts = candles[0][0] // 1000
    return start_ts, [(e_["t"] // 1000, e_["pnl"]) for e_ in events
                      if e_["type"] == "close"]


def signal_pnls(name, rec, c4, ctx, c15, ts15):
    r = se.run_setup(name, rec["genome"], c4, ctx, c15, ts15,
                     rec.get("rec_lev", 15))
    start_ts = c4[0][0] // 1000
    return start_ts, [(t["exit_ts"] // 1000, t["pnl"]) for t in r["trades"]]


def dd_of(points):
    peak, dd = points[0][1], 0.0
    for _, v in points:
        peak = max(peak, v)
        if peak > 0:
            dd = max(dd, (peak - v) / peak)
    return round(dd * 100, 1)


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    pct5 = xd.fetch_daily_pct5()

    streams = []  # (key, label, color, group, start_ts, [(ts, pnl)])
    for sym, modes in config.SYMBOL_PARAMS.items():
        p = modes.get("final")
        if not p:
            continue
        print(f"{sym}: сделки бота (x{p.get('lev', 5)})...")
        st, pnls = bot_pnls(sym, p, pct5)
        streams.append((f"bot_{sym}",
                        f"{sym.replace('USDT','')} — бот x{p.get('lev', 5)}",
                        COLORS.get(sym, "#aaaaaa"), "bot", st, pnls))

    with open("signal_setups.json", encoding="utf-8") as fh:
        setups = json.load(fh)
    c4 = ev.fetch("BTCUSDT", "240", 1150)
    c15 = ev.fetch("BTCUSDT", "15", 1150)
    ts15 = [c[0] for c in c15]
    ctx = se.prep_context(c4)
    for name, rec in setups.items():
        if not rec.get("enabled"):
            continue
        print(f"{name}: сделки сигналов (x{rec.get('rec_lev', 15)})...")
        st, pnls = signal_pnls(name, rec, c4, ctx, c15, ts15)
        streams.append((f"sig_{name}",
                        f"{RU_SETUPS.get(name, name)} x{rec.get('rec_lev', 15)}",
                        COLORS.get(name, "#dddddd"), "signal", st, pnls))

    n = len(streams)
    t0 = min(s[4] for s in streams)
    total0 = SLEEVE0 * n

    series = []
    for key, label, color, group, st, pnls in streams:
        eq = SLEEVE0
        pts = [[t0, round(SLEEVE0, 2)]]
        for ts, pnl in pnls:
            eq *= (1 + pnl / BT_BASE)
            pts.append([ts, round(eq, 2)])
        series.append(dict(
            key=key, label=label, group=group, color=color,
            final_usd=round(eq, 2),
            final_pct=round((eq / SLEEVE0 - 1) * 100, 1),
            dd=dd_of(pts), points=downsample(pts)))

    # события всех стратегий по времени — для портфельных линий
    merged = []
    for key, label, color, group, st, pnls in streams:
        for ts, pnl in pnls:
            merged.append((ts, key, pnl))
    merged.sort()

    # портфель без ребаланса: сумма независимых капиталов
    sleeves = {s[0]: SLEEVE0 for s in streams}
    pts_cons = [[t0, round(total0, 2)]]
    for ts, key, pnl in merged:
        sleeves[key] *= (1 + pnl / BT_BASE)
        pts_cons.append([ts, round(sum(sleeves.values()), 2)])
    series.append(dict(
        key="pf_cons", label="ПОРТФЕЛЬ $350 — без ребаланса",
        group="portfolio", color="#e8e6df",
        final_usd=pts_cons[-1][1],
        final_pct=round((pts_cons[-1][1] / total0 - 1) * 100, 1),
        dd=dd_of(pts_cons), points=downsample(pts_cons)))

    # портфель с ежемесячным ребалансом: r_мес = сумм. PnL / (n x $20-база)
    monthly = defaultdict(float)
    for ts, key, pnl in merged:
        monthly[ts // MONTH] += pnl
    eq = total0
    pts_reb = [[t0, round(eq, 2)]]
    for m in sorted(monthly):
        eq *= (1 + monthly[m] / (n * BT_BASE))
        pts_reb.append([(m + 1) * MONTH, round(eq, 2)])
    series.append(dict(
        key="pf_reb", label="ПОРТФЕЛЬ $350 — ежемесячный ребаланс",
        group="portfolio", color="#E3A83E",
        final_usd=pts_reb[-1][1],
        final_pct=round((pts_reb[-1][1] / total0 - 1) * 100, 1),
        dd=dd_of(pts_reb), points=downsample(pts_reb)))

    # бенчмарки: золото / серебро / S&P 500 как холд от $50 с той же даты
    try:
        import yfinance as yf
        for key, label, ticker, color in BENCHMARKS:
            df = yf.download(ticker, period="4y", interval="1d",
                             progress=False)
            closes = [float(x) for x in df["Close"].values.ravel()]
            times = [int(t.timestamp()) for t in df.index]
            rows = [(ts, c) for ts, c in zip(times, closes) if ts >= t0]
            if len(rows) < 50:
                print(f"  {label}: мало данных, пропущен")
                continue
            base = rows[0][1]
            pts = [[ts, round(SLEEVE0 * c / base, 2)] for ts, c in rows]
            series.append(dict(
                key=key, label=label, group="bench", color=color,
                final_usd=pts[-1][1],
                final_pct=round((pts[-1][1] / SLEEVE0 - 1) * 100, 1),
                dd=dd_of(pts), points=downsample(pts)))
    except Exception as e:
        print(f"  бенчмарки пропущены: {e}")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dict(sleeve=SLEEVE0, total=total0, series=series),
                  fh, ensure_ascii=False)
    for s in series:
        print(f"  {s['label']:44} ${s['final_usd']:>9.2f} "
              f"({s['final_pct']:+8.1f}%) DD {s['dd']}%")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
