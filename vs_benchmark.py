# -*- coding: utf-8 -*-
"""Боты против бенчмарков на одном окне 3.2 года:
S&P 500, buy&hold каждой монеты (по тем же свечам, что у ботов) и
бот-стратегии с реинвестом (из analytics_*.json)."""

import json
import time

import evolution as ev


def hold_stats(candles):
    closes = [c[4] for c in candles]
    ret = (closes[-1] / closes[0] - 1) * 100
    peak, dd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        dd = max(dd, (peak - c) / peak)
    return ret, dd * 100


print("Окно: ", end="")
c4 = ev.fetch("BTCUSDT", "240", 1150)
print(time.strftime("%Y-%m-%d", time.gmtime(c4[0][0] / 1000)), "..",
      time.strftime("%Y-%m-%d", time.gmtime(c4[-1][0] / 1000)))

print(f"\n{'Инструмент':34} {'Доход 3.2г':>11} {'Макс. DD':>9}")
print("-" * 58)

# S&P 500 за то же окно
import yfinance as yf
df = yf.download("^GSPC", period="4y", interval="1d", progress=False)
t0 = c4[0][0] / 1000
closes = [float(x) for x in df["Close"].values.ravel()]
dates = [t.timestamp() for t in df.index]
idx0 = next(i for i, d in enumerate(dates) if d >= t0)
spx = closes[idx0:]
ret = (spx[-1] / spx[0] - 1) * 100
peak, dd = spx[0], 0.0
for c in spx:
    peak = max(peak, c)
    dd = max(dd, (peak - c) / peak)
print(f"{'S&P 500 (индекс, холд)':34} {ret:>+10.1f}% {dd*100:>8.1f}%")

for sym in ["BTCUSDT", "ETHUSDT", "LTCUSDT", "DOGEUSDT", "SOLUSDT"]:
    candles = ev.fetch(sym, "240", 1150)
    ret, dd = hold_stats(candles)
    print(f"{'холд ' + sym.replace('USDT',''):34} {ret:>+10.1f}% {dd:>8.1f}%")

print()
for sym in ["DOGEUSDT", "LTCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]:
    with open(f"webapp/data/analytics_bot_{sym}.json", encoding="utf-8") as fh:
        s = json.load(fh)["stats"]
    print(f"{'БОТ ' + sym.replace('USDT','') + ' (реинвест)':34} "
          f"{s['final_pct']:>+10.1f}% {s['max_dd']:>8.1f}%")

with open("webapp/data/pnl_curves.json", encoding="utf-8") as fh:
    d = json.load(fh)
for s in d["series"]:
    if s["group"] == "portfolio":
        print(f"{s['label'][:34]:34} {s['final_pct']:>+10.1f}% "
              f"{s.get('dd', '?'):>8}%")
