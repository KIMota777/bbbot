# -*- coding: utf-8 -*-
"""Быстрая проверка evolution8.py на маленьком срезе, прежде чем запускать
полный прогон (GA + 2 таймфрейма + лестница) в фоне надолго."""

import evolution as ev
import evolution2 as e2
import evolution8 as e8
import ext_data as xd

pct5 = xd.fetch_daily_pct5()
base_src = e8.build_base_src()
print("base_src монеты:", list(base_src.keys()))
print("DOGE base genome keys:", sorted(base_src["DOGEUSDT"].keys()))

sym = "DOGEUSDT"
candles15 = ev.fetch(sym, "15", 1150)[-5000:]  # маленький срез для скорости
aux_builder = e8.make_aux_builder(pct5, 96)
aux = aux_builder(sym, candles15)
print("aux ключи:", sorted(aux.keys()))

g = base_src[sym]
filt = e8.make_filter8(g, aux)
pre = e2.prep(candles15)
r = e2.run5(candles15, pre, g, entry_filter=filt)
print(f"15m срез: сделок {r['trades']}, баланс {r['balance']:.2f}, слив {r['ruined']}")

# теперь то же на 4ч
candles4h = ev.fetch(sym, "240", 1150)[-1000:]
aux_builder4 = e8.make_aux_builder(pct5, 6)
aux4 = aux_builder4(sym, candles4h)
old_bpd = e2.BARS_PER_DAY
e2.BARS_PER_DAY = 6
try:
    filt4 = e8.make_filter8(g, aux4)
    pre4 = e2.prep(candles4h)
    r4 = e2.run5(candles4h, pre4, g, entry_filter=filt4)
finally:
    e2.BARS_PER_DAY = old_bpd
print(f"4h срез: сделок {r4['trades']}, баланс {r4['balance']:.2f}, слив {r4['ruined']}")

# лестница плечей на маленьком срезе (проверка ladder_for)
ladder = e8.ladder_for(sym, g, candles15, aux, "15")
print("лестница (15m срез):", [(x["lev"], x["ret"]) for x in ladder])

print("\nOK — все функции evolution8.py отрабатывают без ошибок")
