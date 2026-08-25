# -*- coding: utf-8 -*-
"""Разбор bh.perturb: что реально двигается, что зажимается, что теряется."""
import json
import numpy as np
import bots_honest as bh
import evolution8 as e8
import all_configs_honest as ach

G = e8.GENES8
print("генов в GENES8: %d" % len(G))
num = [k for k in bh.NUMERIC_GENES]
print("NUMERIC_GENES (%d): %s" % (len(num), ", ".join(num)))
print("DISCRETE_GENES (%d): %s" % (len(bh.DISCRETE_GENES),
                                   ", ".join(bh.DISCRETE_GENES)))
print("NUMERIC не входящие в GENES8: %s"
      % [k for k in num if k not in G])
print("GENES8 не покрытые ни одним списком: %s"
      % [k for k in G if k not in num and k not in bh.DISCRETE_GENES])

recs = ach.collect()
print("\nсобрано: %d" % len(recs))
keysets = {}
for r in recs:
    keysets.setdefault(tuple(sorted(r["g"])), []).append(r["src"])
print("разных наборов ключей генома: %d" % len(keysets))
for ks, srcs in keysets.items():
    print("   %d ключей, %d конфигов, напр. %s" % (len(ks), len(srcs), srcs[0]))
    miss = [k for k in G if k not in ks]
    extra = [k for k in ks if k not in G]
    print("      нет в геноме из GENES8: %s | лишние (clamp их выбросит): %s"
          % (miss, extra))

print("\n=== сколько генов реально ДВИГАЕТСЯ у каждого конфига ===")
rows = []
for r in recs:
    g = r["g"]
    moved, atlo, athi, out_lo, out_hi = 0, 0, 0, 0, 0
    for k in num:
        if k not in g or k not in G:
            continue
        lo, hi, ii = G[k]
        v = float(g[k])
        moved += 1
        if v < lo:
            out_lo += 1
        elif v > hi:
            out_hi += 1
        # зажим кусает, если +-10% вылезает за границу
        if v * 0.9 < lo:
            atlo += 1
        if v * 1.1 > hi:
            athi += 1
    rows.append((r["sym"], r["src"], r["tag"], moved, out_lo + out_hi,
                 atlo + athi))
for x in sorted(rows, key=lambda z: (-z[5], -z[4])):
    print("%-8s %-24s %-8s двиг=%2d вне_границ=%d зажим_кусает=%d"
          % (x[0], x[1], x[2], x[3], x[4], x[5]))

print("\n=== что perturb делает с ключами ===")
rng = np.random.default_rng(7)
g0 = recs[0]["g"]
p = bh.perturb(g0, G, rng, 0.10)
print("вход %d ключей, выход %d ключей" % (len(g0), len(p)))
print("потеряны: %s" % [k for k in g0 if k not in p])
print("добавлены: %s" % [k for k in p if k not in g0])
