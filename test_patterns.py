# -*- coding: utf-8 -*-
import time

import evolution as ev
import patterns as pt

candles = ev.fetch("BTCUSDT", "15", 365)
t0 = time.time()
bull, bear = pt.compute_pattern_signals(candles)
dt = time.time() - t0

n = len(candles)
bull_share = sum(bull) / n * 100
bear_share = sum(bear) / n * 100
both = sum(1 for i in range(n) if bull[i] and bear[i])
print(f"свечей {n}, время расчёта {dt:.2f}с")
print(f"bull активен {bull_share:.1f}% времени, bear {bear_share:.1f}%, "
      f"одновременно оба {both} баров ({both/n*100:.2f}%)")

# несколько примеров переходов сигнала (для проверки, что вообще срабатывает)
transitions = 0
for i in range(1, n):
    if bull[i] != bull[i - 1] or bear[i] != bear[i - 1]:
        transitions += 1
print(f"переключений сигнала: {transitions}")
