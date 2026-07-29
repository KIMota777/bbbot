# -*- coding: utf-8 -*-
"""Смоук-тест сигнального движка: 6 сетапов на BTC, дефолтные параметры."""

import time

import evolution as ev
import signal_engine as se

c4 = ev.fetch("BTCUSDT", "240", 1150)
c15 = ev.fetch("BTCUSDT", "15", 1150)
ts15 = [c[0] for c in c15]
print(f"4ч: {len(c4)} свечей | 15м: {len(c15)} свечей")

t0 = time.time()
ctx = se.prep_context(c4)
print(f"контекст за {time.time()-t0:.2f}с | режимы: "
      f"bull {ctx['regime'].count(0)} range {ctx['regime'].count(1)} "
      f"bear {ctx['regime'].count(2)}")

g = dict(se.DEFAULTS)
for setup in se.SETUPS:
    t0 = time.time()
    r = se.run_setup(setup, g, c4, ctx, c15, ts15, lev=15)
    st = se.stats(r)
    ret = (r["balance"] / se.START - 1) * 100
    reasons = {}
    for t in r["trades"]:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    print(f"{setup:12} | {st['n']:3} сделок | WR {st['wr']:5.1f}% | "
          f"итог {ret:+7.1f}% | DD {r['max_dd']*100:4.1f}% | "
          f"ср.удерж {st['avg_hold_h']:5.1f}ч | {reasons} | {time.time()-t0:.2f}с")
