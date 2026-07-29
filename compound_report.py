# -*- coding: utf-8 -*-
"""Что даёт реинвест (компаундинг) и портфель — на ТЕХ ЖЕ сделках из
pnl_curves.json, без новых допущений.

Математика точная, не приближение: PnL сделки пропорционален марже (объём,
комиссии, funding, ликвидация — всё линейно от маржи), поэтому если вместо
фиксированных $5 ставить 25% текущего капитала (та же доля, что $5 от $20),
капитал после каждой сделки умножается на (1 + pnl_i/20). Просадки в % те
же самые по последовательности — риск на сделку не меняется, меняется только
то, что прибыль начинает работать.

Портфель: помесячные PnL всех стратегий складываются на общем капитале $35
(7 стратегий x $5 одновременной маржи), компаундинг помесячный.
"""

import json
from collections import defaultdict

with open("webapp/data/pnl_curves.json", encoding="utf-8") as fh:
    data = json.load(fh)

START = 20.0
MONTH = 30 * 86400

print(f"{'Стратегия':40} {'Линейно':>10} {'С реинвестом':>13} {'DD реинв.':>10}")
print("-" * 78)

monthly_all = defaultdict(float)
t_min = None
for s in data["series"]:
    pts = s["points"]
    # восстанавливаем PnL каждой сделки из кривой (точки не прорежены —
    # проверено: len(points) = сделки + 1 у всех серий)
    pnls = []
    for i in range(1, len(pts)):
        pnls.append((pts[i][1] - pts[i - 1][1]) / 100 * START)
        monthly_all[pts[i][0] // MONTH] += pnls[-1]
    if t_min is None or pts[0][0] < t_min:
        t_min = pts[0][0]

    eq, peak, dd = 1.0, 1.0, 0.0
    for p in pnls:
        eq *= (1 + p / START)
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    print(f"{s['label']:40} {s['final']:>+9.1f}% {(eq - 1) * 100:>+12.1f}% "
          f"{dd * 100:>9.1f}%")

# --- портфель ---
CAP = 35.0  # 7 стратегий x $5 одновременной маржи
months = sorted(monthly_all)
lin = sum(monthly_all.values())
eq, peak, dd = 1.0, 1.0, 0.0
for m in months:
    eq *= (1 + monthly_all[m] / CAP)
    peak = max(peak, eq)
    dd = max(dd, (peak - eq) / peak)
years = (months[-1] - months[0] + 1) * MONTH / 86400 / 365

print("-" * 78)
print(f"{'ПОРТФЕЛЬ (7 стратегий, капитал $35)':40} "
      f"{lin / CAP * 100:>+9.1f}% {(eq - 1) * 100:>+12.1f}% {dd * 100:>9.1f}%")
print(f"\nПериод: {years:.1f} года. Портфель с реинвестом: "
      f"${CAP:.0f} -> ${CAP * eq:.0f}")
cagr = eq ** (1 / years) - 1
print(f"Годовая ставка портфеля (CAGR): {cagr * 100:+.1f}%")
for cap0 in (100, 500, 1000):
    print(f"  тот же % на капитале ${cap0}: -> ${cap0 * eq:.0f} за {years:.1f}г "
          f"(+${cap0 * (eq - 1):.0f})")
