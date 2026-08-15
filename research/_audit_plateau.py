# -*- coding: utf-8 -*-
"""Что делает сглаживание «по плато» с непорядковыми осями сетки."""
import numpy as np

import engine
import protocol
import rdata
import universe

REG, _ = universe.load()


def tapes_for(name, sym="BTCUSDT", tf="60"):
    st = REG[name]
    bars = rdata.load_bars(sym, tf)
    t0, t1 = rdata.SPLITS["trainval"]
    sub, _ = bars.slice(t0, t1)
    cfg = engine.Cfg(risk_frac=0.01, max_lev=10.0)
    keys = sorted(st.grid)
    tapes = {}
    for p in st.combos():
        sig = st.build(sub, p)
        if int((sig.entry != 0).sum()) == 0:
            continue
        res = engine.run(sub, sig, cfg)
        if len(res.trades) < 3:
            continue
        tapes[tuple(p[k] for k in keys)] = protocol.Tape(res)
    return st, keys, tapes, t0, t1


def show(name):
    st, keys, tapes, t0, t1 = tapes_for(name)
    a, b = t0, t0 + 360 * protocol.DAY
    rows = []
    for ck, tp in tapes.items():
        m = protocol.replay(tp, a, b)
        if m["trades"] >= 5:
            rows.append((dict(zip(keys, ck)), m["score"]))
    if not rows:
        print(name, "нет кандидатов")
        return
    sm = protocol.plateau_scores(rows, st.grid)
    raw = sorted(rows, key=lambda x: -x[1])[0]
    smo = sorted(sm, key=lambda x: -x[1])[0]
    print("\n=== %s (BTCUSDT 1ч, первое окно обучения 360 дней, %d кандидатов) ==="
          % (name, len(rows)))
    print("  лучший по СЫРОЙ оценке     : %s  score=%.3f" % (raw[0], raw[1]))
    print("  лучший по СГЛАЖЕННОЙ оценке: %s  smooth=%.3f (своя=%.3f, соседей=%d)"
          % (smo[0], smo[1], smo[2], smo[3]))
    # насколько сглаживание тянет через непорядковую ось
    g = st.grid
    ax = [k for k in g if k in ("mode", "regime", "persist", "use_volume",
                                "use_regime", "eod", "filt", "need_cloud",
                                "exit_weak", "exit_mixed", "trend_only")
          and len(g[k]) > 1]
    idx = {tuple(c[k] for k in keys): s for c, s in rows}
    for k in ax:
        diffs = []
        for c, s in rows:
            i = g[k].index(c[k])
            for j in (i - 1, i + 1):
                if 0 <= j < len(g[k]):
                    cc = dict(c)
                    cc[k] = g[k][j]
                    kk = tuple(cc[q] for q in keys)
                    if kk in idx:
                        diffs.append(abs(s - idx[kk]))
        if diffs:
            print("  ось %-12s непорядковая: средний |разрыв| между "
                  "соседями по ней = %.3f (n=%d)"
                  % (k, float(np.mean(diffs)), len(diffs)))
    # сколько сочетаний осталось без части соседей
    holes = [z for z in sm if z[3] < 2 * sum(1 for k in keys
                                             if len(g[k]) > 1)]
    thin = [z for z in sm if z[3] <= 1]
    print("  сочетаний со сглаживанием по <=1 соседу: %d из %d "
          "(у них «плато» = «пик»)" % (len(thin), len(sm)))


for nm in ("vol_spike", "pdh_pdl", "c_rsi", "macd"):
    show(nm)
