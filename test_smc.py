# -*- coding: utf-8 -*-
import time

import evolution as ev
import smc

candles = ev.fetch("BTCUSDT", "15", 365)
n = len(candles)

t0 = time.time()
fb, fr = smc.find_fvg(candles)
print(f"FVG: {time.time()-t0:.2f}с | bull активен {sum(fb)/n*100:.1f}% | bear {sum(fr)/n*100:.1f}%")

t0 = time.time()
ob, obr = smc.find_order_blocks(candles)
print(f"OB:  {time.time()-t0:.2f}с | bull активен {sum(ob)/n*100:.1f}% | bear {sum(obr)/n*100:.1f}%")

t0 = time.time()
bias = smc.structure_bias(candles)
flips = sum(1 for i in range(1, n) if bias[i] != bias[i-1])
print(f"Structure: {time.time()-t0:.2f}с | bull {sum(1 for b in bias if b>0)/n*100:.1f}% | "
      f"bear {sum(1 for b in bias if b<0)/n*100:.1f}% | neutral {sum(1 for b in bias if b==0)/n*100:.1f}% | "
      f"переворотов {flips}")
