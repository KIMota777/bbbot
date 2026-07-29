# -*- coding: utf-8 -*-
"""Диагностика: почему у ETH 4h max_dd = 0.0% при 307 сделках?"""

import json

import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

with open("evolution8_4h_final.json", encoding="utf-8") as fh:
    rec = json.load(fh)["ETHUSDT"]
g = rec["genome"]

pct5 = xd.fetch_daily_pct5()
candles = ev.fetch("ETHUSDT", "240", 1150)
aux = e8.make_aux_builder(pct5, 6)("ETHUSDT", candles)
pre = e2.prep(candles)
filt = e8.make_filter8(g, aux)

old_lev, old_bpd = e2.LEV, e2.BARS_PER_DAY
e2.LEV = 15
e2.BARS_PER_DAY = 6
try:
    events = []
    r = e2.run5(candles, pre, g, entry_filter=filt, events=events)
finally:
    e2.LEV, e2.BARS_PER_DAY = old_lev, old_bpd

print(f"баланс {r['balance']:.2f} | сделок {r['trades']} | max_dd {r['max_dd']} | ruined {r['ruined']}")

closes_ev = [e for e in events if e["type"] == "close"]
pnls = [e["pnl"] for e in closes_ev]
neg = [p for p in pnls if p < 0]
print(f"циклов закрыто: {len(closes_ev)} | из них убыточных: {len(neg)}")

from collections import Counter
reasons = Counter(e.get("reason", "?") for e in closes_ev)
print("причины выхода:", dict(reasons))
for reason in reasons:
    rp = [e["pnl"] for e in closes_ev if e.get("reason") == reason]
    print(f"  {reason}: {len(rp)} шт, pnl мин {min(rp):.3f} макс {max(rp):.3f} сред {sum(rp)/len(rp):.3f}")
if neg:
    print(f"худшие 10 убытков: {sorted(neg)[:10]}")
else:
    print("УБЫТОЧНЫХ ЦИКЛОВ НЕТ ВООБЩЕ — либо стоп ни разу не сработал, либо это баг")

# восстановим траекторию баланса
bal = e2.START
peak = bal
maxdd_manual = 0.0
running = []
for p in pnls:
    bal += p
    peak = max(peak, bal)
    dd = (peak - bal) / peak if peak > 0 else 0
    maxdd_manual = max(maxdd_manual, dd)
    running.append(bal)
print(f"ручной пересчёт max_dd по events: {maxdd_manual*100:.3f}% (движок сказал {r['max_dd']*100:.3f}%)")
print(f"баланс: старт {e2.START}, финиш по events {bal:.2f}, финиш по r {r['balance']:.2f}")
print(f"мин баланс за всю историю (по events): {min(running):.2f}, старт {e2.START}")

# первые несколько сделок для проверки side/stop/tp логики
print("\nпервые 6 событий:")
for e in events[:6]:
    print(" ", e)
