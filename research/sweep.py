# -*- coding: utf-8 -*-
"""Перебор каталога по протоколу: обучение на прошлом, проверка на следующем.

Что здесь считается и в каком порядке (порядок и есть суть):

  1. Стратегия прогоняется по TRAIN+VAL один раз на каждое сочетание
     параметров. Результат сжимается в «ленту» долей капитала (protocol.Tape).
  2. По ленте нарезаются окна скользящей проверки вперёд. В каждом окне
     параметры выбираются ТОЛЬКО по обучающему куску и только по сглаженной
     оценке (плато, не пик).
  3. Внеобучающие куски склеиваются в одну кривую. Её доходность и просадка —
     единственное число, которому здесь можно верить.
  4. Для сравнения считается и «лучшее по всей выборке» — заведомо
     завышенная величина. Она печатается рядом НАМЕРЕННО: разрыв между ней и
     склеенной кривой показывает, сколько в результате подгонки.

TEST не читается. Границы берутся из rdata.SPLITS и заканчиваются на VAL.

Запуск:  python sweep.py [--tf 60,240] [--sym BTCUSDT,...] [--jobs 12]
Результат: research/out/sweep.json
"""
import argparse
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import engine     # noqa: E402
import protocol   # noqa: E402
import rdata      # noqa: E402
import universe   # noqa: E402

OUT = os.path.join(DIR, "out")
WARMUP = 600          # баров на прогрев индикаторов перед началом окна


def one_task(task):
    """Один (стратегия, монета, таймфрейм). Возвращает компактный отчёт."""
    name, sym, tf, is_days, oos_days = task
    reg, _ = universe.load()
    st = reg.get(name)
    if st is None:
        return None
    try:
        bars = rdata.load_bars(sym, tf)
    except FileNotFoundError:
        return None
    t0, t1 = rdata.SPLITS["trainval"]
    sub, _ = bars.slice(t0, t1, warmup=0)
    if len(sub) < 2000:
        return None

    cfg = engine.Cfg(risk_frac=0.01, max_lev=10.0)
    keys = sorted(st.grid)
    tapes, degenerate = {}, 0
    t_begin = time.time()
    for p in st.combos():
        try:
            sig = st.build(sub, p)
        except Exception:                       # noqa: BLE001
            degenerate += 1
            continue
        if int((sig.entry != 0).sum()) == 0:
            degenerate += 1
            continue
        res = engine.run(sub, sig, cfg)
        if len(res.trades) < 3:
            degenerate += 1
            continue
        tapes[tuple(p[k] for k in keys)] = protocol.Tape(res)
    if not tapes:
        return dict(strategy=name, symbol=sym, tf=tf, ok=False,
                    reason="ни одного рабочего сочетания",
                    degenerate=degenerate, combos=st.n_combos())

    wf = protocol.walk_forward(tapes, st.grid, is_days, oos_days,
                               anchored=False, t_start=t0, t_end=t1)
    wf_a = protocol.walk_forward(tapes, st.grid, is_days, oos_days,
                                 anchored=True, t_start=t0, t_end=t1)
    st_roll = protocol.stitch(wf, tapes)
    st_anch = protocol.stitch(wf_a, tapes)

    # «лучшее по всей выборке» — верхняя граница самообмана, для сравнения
    full = [(k, protocol.replay(t, t0, t1)) for k, t in tapes.items()]
    full.sort(key=lambda x: -x[1]["score"])
    best_k, best_m = full[0]

    ok_folds = [f for f in wf if f.get("oos")]
    oos_rets = [f["oos"]["ret"] for f in ok_folds]
    return dict(
        strategy=name, symbol=sym, tf=tf, ok=True,
        family=st.family, combos=st.n_combos(), working=len(tapes),
        degenerate=degenerate, secs=round(time.time() - t_begin, 1),
        wf=dict(ret=st_roll["ret"], cagr=st_roll["cagr"], mo=st_roll["mo"],
                maxdd=st_roll["maxdd"], trades=st_roll["trades"],
                days=st_roll["days"]),
        wf_anchored=dict(ret=st_anch["ret"], mo=st_anch["mo"],
                         maxdd=st_anch["maxdd"], trades=st_anch["trades"]),
        folds=len(ok_folds),
        folds_pos=int(sum(1 for r in oos_rets if r > 0)),
        fold_rets=[round(r, 4) for r in oos_rets],
        fold_choices=[f["chosen"] for f in ok_folds],
        insample_best=dict(params=dict(zip(keys, best_k)),
                           ret=best_m["ret"], maxdd=best_m["maxdd"],
                           trades=best_m["trades"], score=best_m["score"]),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="60,240")
    ap.add_argument("--sym", default=",".join(rdata.SYMBOLS))
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--is-days", type=int, default=360)
    ap.add_argument("--oos-days", type=int, default=90)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="sweep.json")
    a = ap.parse_args()

    reg, added = universe.load(verbose=True)
    names = sorted(reg)
    if a.only:
        want = set(a.only.split(","))
        names = [n for n in names if n in want]
    tfs = a.tf.split(",")
    syms = a.sym.split(",")
    tasks = [(n, s, tf, a.is_days, a.oos_days)
             for n in names for s in syms for tf in tfs]
    total_combos = sum(reg[n].n_combos() for n in names) * len(syms) * len(tfs)
    print("стратегий %d, задач %d, сочетаний всего %d"
          % (len(names), len(tasks), total_combos))
    if universe.BROKEN:
        print("НЕ ЗАГРУЗИЛИСЬ: %s" % ", ".join(sorted(universe.BROKEN)))

    t0 = time.time()
    rows = []
    with Pool(a.jobs) as pool:
        for i, r in enumerate(pool.imap_unordered(one_task, tasks, 1), 1):
            if r:
                rows.append(r)
            if i % 25 == 0 or i == len(tasks):
                print("  %d/%d  %.0fs" % (i, len(tasks), time.time() - t0))
                sys.stdout.flush()

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    path = os.path.join(OUT, a.out)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dict(rows=rows, total_combos=total_combos,
                       is_days=a.is_days, oos_days=a.oos_days,
                       symbols=syms, tfs=tfs), fh, ensure_ascii=False)
    print("\nготово за %.0fs -> %s" % (time.time() - t0, path))

    good = [r for r in rows if r.get("ok")]
    good.sort(key=lambda r: -r["wf"]["mo"])
    print("\nЛУЧШИЕ ПО СКЛЕЕННОЙ ВНЕОБУЧАЮЩЕЙ КРИВОЙ (месяц / просадка):")
    print("%-18s %-5s %-4s %8s %8s %7s %6s %7s"
          % ("стратегия", "мон", "тф", "мес", "итог", "просад", "сдел", "окон+"))
    for r in good[:25]:
        print("%-18s %-5s %-4s %+7.2f%% %+7.1f%% %6.1f%% %6d %3d/%d"
              % (r["strategy"], r["symbol"].replace("USDT", ""), r["tf"],
                 100 * r["wf"]["mo"], 100 * r["wf"]["ret"],
                 100 * r["wf"]["maxdd"], r["wf"]["trades"],
                 r["folds_pos"], r["folds"]))


if __name__ == "__main__":
    main()
