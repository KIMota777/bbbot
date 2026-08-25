# -*- coding: utf-8 -*-
"""АТАКА 1. Блочный бутстрэп по месяцам: отличается ли верх таблицы от середины?

Пересэмплируются КАЛЕНДАРНЫЕ МЕСЯЦЫ (общий набор для всех конфигов сразу —
так сохраняется общий рыночный шок), внутри месяца сделки идут как были.
"""
import sys, json
import numpy as np
import attack_significance_lib as L
import ext_data as xd

RANKS = [1, 2, 3, 4, 5, 26, 27, 28, 29, 30]
B = 4000


def main():
    pct5 = xd.fetch_daily_pct5()
    rows = L.rows()
    sel = [(r, rows[r - 1]) for r in RANKS]
    data = {}
    for rank, x in sel:
        hs = L.halves(x["sym"], x["genome"], pct5)
        d = {}
        for h in ("train", "hold"):
            d[h] = L.run_half(hs[h], x["genome"], 5.0)
        data[rank] = d
        print("ранг %-3d %-8s %-28s  обуч %+8.2f (json %+8.2f, n=%d)  "
              "холд %+8.2f (json %+8.2f, n=%d)"
              % (rank, x["sym"], (x["src"] + "/" + x["tag"])[:28],
                 d["train"]["comp"], x["train"]["comp"], d["train"]["trades"],
                 d["hold"]["comp"], x["hold"]["comp"], d["hold"]["trades"]))
        sys.stdout.flush()

    rng = np.random.default_rng(20260825)
    for h in ("train", "hold"):
        # общий список месяцев по этой половине
        allm = sorted({L.month_key(t) for rank in data
                       for t, _ in data[rank][h]["closes"]})
        by = {}
        for rank in data:
            m = {}
            for t, p in data[rank][h]["closes"]:
                m.setdefault(L.month_key(t), []).append(p)
            by[rank] = m
        M = len(allm)
        print("\n=== %s: месяцев в блоке %d ===" % (h.upper(), M))
        draws = rng.integers(0, M, size=(B, M))
        boot = {}
        for rank in data:
            m = by[rank]
            per = [m.get(k, []) for k in allm]
            vals = np.empty(B)
            for b in range(B):
                pn = []
                for j in draws[b]:
                    pn.extend(per[j])
                vals[b] = L.comp_of(pn)
            boot[rank] = vals
            lo, hi = np.percentile(vals, [2.5, 97.5])
            print("  ранг %-3d  факт %+9.2f%%   95%% ДИ [%+9.2f%% .. %+9.2f%%]"
                  " P(<=0)=%.3f"
                  % (rank, data[rank][h]["comp"], lo, hi, float((vals <= 0).mean())))
        print("  -- разности «лидер минус середина» (парные месяцы) --")
        for a in RANKS[:5]:
            for c in RANKS[5:]:
                d = boot[a] - boot[c]
                lo, hi = np.percentile(d, [2.5, 97.5])
                print("     %2d - %2d: медиана %+8.2f  ДИ [%+8.2f .. %+8.2f]  "
                      "P(лидер хуже)=%.3f"
                      % (a, c, float(np.median(d)), lo, hi, float((d < 0).mean())))
        json.dump({str(k): boot[k].tolist() for k in boot},
                  open("attack_significance_boot_%s.json" % h, "w"))


if __name__ == "__main__":
    main()
