# -*- coding: utf-8 -*-
"""АТАКА 3. Порядок таблицы против ЗЕРНА возмущения + случайные геномы ядра.

A. worst_rob (медиана 40 соседей, худшая половина) пересчитывается с другими
   зёрнами RNG. Если порядок держится на зерне 7 — порядка нет.
B. Случайные геномы ЯДРА (bh.rand_core) на тех же половинах, x5. Сколько
   случайных дотягивает до уровня лидеров.
"""
import json, os, sys, time
from multiprocessing import Pool
import numpy as np
import attack_significance_lib as L
import bots_honest as bh
import evolution8 as e8
import ext_data as xd

RANKS = [1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 20, 26, 30, 41, 47]
SEEDS = [7, 101, 202, 303]
NRAND = 300


def job_rank(a):
    rk, seed = a
    pct5 = xd.fetch_daily_pct5()
    x = L.rows()[rk - 1]
    g = x["genome"]
    hs = L.halves(x["sym"], g, pct5)
    rng = np.random.default_rng(seed)
    neigh = [bh.perturb(g, e8.GENES8, rng, 0.10) for _ in range(40)]
    res = {}
    for h in ("train", "hold"):
        vals = []
        for ng in neigh:
            try:
                vals.append(L.run_half(hs[h], ng)["comp"])
            except Exception:
                continue
        res[h] = float(np.median(vals)) if vals else 0.0
    return dict(rk=rk, seed=seed, train=res["train"], hold=res["hold"],
                worst=min(res["train"], res["hold"]))


def job_rand(a):
    sym, i0, n = a
    pct5 = xd.fetch_daily_pct5()
    rng = np.random.default_rng(900000 + i0)
    out = []
    hs_cache = None
    for i in range(n):
        g = bh.rand_core(e8.GENES8, rng)
        try:
            hs = L.halves(sym, g, pct5)
            tr = L.run_half(hs["train"], g)
            ho = L.run_half(hs["hold"], g)
        except Exception:
            continue
        if tr["trades"] + ho["trades"] == 0:
            continue
        out.append((tr["comp"], ho["comp"], tr["trades"], ho["trades"]))
    return sym, out


def main():
    xd.fetch_daily_pct5()
    jobs = [(rk, s) for rk in RANKS for s in SEEDS]
    t0 = time.time()
    res = {}
    with Pool(10, maxtasksperchild=6) as p:
        for k, r in enumerate(p.imap_unordered(job_rank, jobs), 1):
            res[(r["rk"], r["seed"])] = r
            if k % 20 == 0:
                print("  %d/%d %.0fs" % (k, len(jobs), time.time() - t0))
                sys.stdout.flush()

    print("\n=== A. worst_rob ПРИ РАЗНЫХ ЗЁРНАХ ВОЗМУЩЕНИЯ ===")
    print("%-5s %-8s %-24s %s" % ("ранг", "мон", "откуда",
                                  "  ".join("сид %-6d" % s for s in SEEDS)))
    rows = L.rows()
    tab = {}
    for rk in RANKS:
        x = rows[rk - 1]
        vs = [res[(rk, s)]["worst"] for s in SEEDS]
        tab[rk] = vs
        print("%-5d %-8s %-24s %s"
              % (rk, x["sym"].replace("USDT", ""),
                 (x["src"] + "/" + x["tag"])[:24],
                 "  ".join("%+9.2f" % v for v in vs)))
    print("\nпорядок по каждому зерну (ранги исходной таблицы сверху вниз):")
    for j, s in enumerate(SEEDS):
        order = sorted(RANKS, key=lambda r: -tab[r][j])
        print("  сид %-4d: %s" % (s, " ".join("%d" % r for r in order)))
    from scipy.stats import spearmanr
    base = [tab[r][0] for r in RANKS]
    for j, s in enumerate(SEEDS[1:], 1):
        rho = spearmanr(base, [tab[r][j] for r in RANKS]).statistic
        print("  Спирмен сид7 vs сид%-4d = %+0.3f" % (s, rho))
    json.dump({str(k): v for k, v in tab.items()},
              open("attack_significance_rank_seeds.json", "w"))

    print("\n=== B. СЛУЧАЙНЫЕ ГЕНОМЫ ЯДРА, x5 ===")
    rj = []
    for sym in ("BTCUSDT", "DOGEUSDT", "SOLUSDT"):
        for i in range(6):
            rj.append((sym, i * 1000, NRAND // 6))
    acc = {}
    with Pool(10, maxtasksperchild=3) as p:
        for sym, out in p.imap_unordered(job_rand, rj):
            acc.setdefault(sym, []).extend(out)
    lead = {}
    for rk in [1, 2, 3, 4, 5]:
        x = rows[rk - 1]
        lead.setdefault(x["sym"], []).append((rk, min(x["train"]["comp"],
                                                      x["hold"]["comp"])))
    for sym, out in sorted(acc.items()):
        w = np.array([min(a, b) for a, b, _, _ in out])
        ho = np.array([b for _, b, _, _ in out])
        print("\n  %s: случайных с торговлей %d" % (sym, len(w)))
        print("    худшая половина: медиана %+7.2f  90%%=%+7.2f  95%%=%+7.2f  "
              "макс %+7.2f  доля>0 %.2f"
              % (np.median(w), np.percentile(w, 90), np.percentile(w, 95),
                 w.max(), (w > 0).mean()))
        print("    холдоут:         медиана %+7.2f  90%%=%+7.2f  95%%=%+7.2f  "
              "макс %+7.2f  доля>0 %.2f"
              % (np.median(ho), np.percentile(ho, 90), np.percentile(ho, 95),
                 ho.max(), (ho > 0).mean()))
        for rk, v in lead.get(sym, []):
            print("    лидер ранг %d: худшая половина %+7.2f -> перцентиль "
                  "среди случайных %.1f%%" % (rk, v, (w < v).mean() * 100))
        for rk in RANKS:
            x = rows[rk - 1]
            if x["sym"] != sym:
                continue
            v = min(x["train"]["comp"], x["hold"]["comp"])
            print("      ранг %-3d худ.пол %+8.2f  перцентиль %.1f%%"
                  % (rk, v, (w < v).mean() * 100))
    json.dump({k: [list(t) for t in v] for k, v in acc.items()},
              open("attack_significance_rand.json", "w"))


if __name__ == "__main__":
    main()
