# -*- coding: utf-8 -*-
"""ETH: действующий short-only против кандидата v2 на трёх OOS-фолдах."""
import json

import evolution as ev
import evolution2 as e2

SHORT_ONLY = dict(rsi_idx=2, rsi_os=28, zone_l=0.51, zone_s=0.51, window=199,
                  step=0.030, levels=2, mult=1.6, tp=0.014, sweep=0.023,
                  max_bars=70, cooldown=18, knife=3.3, be_move=0)

with open("evolution2_winners.json", encoding="utf-8") as fh:
    cand = json.load(fh)["ETHUSDT"]["genome"]
cand = {k: (int(round(v)) if e2.GENES[k][2] else v) for k, v in cand.items()}

candles = ev.fetch("ETHUSDT", "15", 730)
folds = e2.fold_bounds(len(candles))


def sfilter(side, i):
    return side if side == "S" else None


for label, g, filt in (("short-only (текущий)", SHORT_ONLY, sfilter),
                       ("кандидат v2 (обе стороны)", cand, None)):
    scores = []
    for (a, b, e) in folds:
        seg = candles[b:e]
        r = e2.run5(seg, e2.prep(seg), g, entry_filter=filt)
        scores.append(e2.oos_score(r))
    print(f"{label}: средний OOS {sum(scores)/3:+.2f} | "
          f"{['%+.2f' % s for s in scores]}")
