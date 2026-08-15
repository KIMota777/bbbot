# -*- coding: utf-8 -*-
u"""Последний вопрос: настолько ли хорош лучший случай, чтобы ему верить.

Из 78 конфигураций лучшей оказалась supertrend|240|срединное сочетание|логрег:
прибавка +0.187% на вход, месяц с +1.60% до +3.00%. Число само по себе
приличное. Вопрос ровно один — сколько таких чисел выдаёт та же процедура,
когда предсказывать нечего.

Считается два раза, двумя разными нулями:

  НУЛЬ 1, ПЕРЕМЕШАННЫЕ МЕТКИ. Признаки настоящие, метка «прибыльно» перемешана
  внутри обучающего куска. Модель по-прежнему что-то выучивает и что-то
  отбирает — но выучивать там нечего. 200 повторов.

  НУЛЬ 2, СЛУЧАЙНАЯ ПОЛОВИНА. Просто половина входов наугад в каждом окне.
  Самый честный ноль: ровно та же арифметика, ровно то же число сделок.

И рядом — то, ради чего всё считалось: кривая накопленной прибыли того же
правила с фильтром и без, по окнам проверки.
"""
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import ml_lib   # noqa: E402
import ml_meta  # noqa: E402
import rdata    # noqa: E402

N_SHUF = 200


def null_shuffled(d, kind, n_rep, seed=0):
    t0, t1 = rdata.SPLITS["trainval"]
    folds = ml_lib.purged_folds(d["t_in"], d["t_out"], t0, t1, ml_meta.N_FOLDS)
    X, y, ret = d["X"], d["y"], d["ret"]
    rng = np.random.default_rng(seed)
    out = []
    for rep in range(n_rep):
        rs, keeps = [], []
        for f in folds:
            tr, te = f["train"], f["test"]
            m = ml_meta.make_model(kind, seed=rep)
            m.fit(X[tr], y[tr][rng.permutation(len(tr))])
            p_tr = m.predict_proba(X[tr])[:, 1]
            p_te = m.predict_proba(X[te])[:, 1]
            keeps.append(p_te >= float(np.median(p_tr)))
            rs.append(ret[te])
        rr = np.concatenate(rs)
        kk = np.concatenate(keeps)
        if kk.sum() >= 5:
            out.append(float(rr[kk].mean() - rr.mean()))
    return np.array(out)


def null_random(d, n_rep, seed=1):
    t0, t1 = rdata.SPLITS["trainval"]
    folds = ml_lib.purged_folds(d["t_in"], d["t_out"], t0, t1, ml_meta.N_FOLDS)
    ret = d["ret"]
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n_rep):
        rs, keeps = [], []
        for f in folds:
            te = f["test"]
            k = np.zeros(len(te), dtype=bool)
            k[rng.choice(len(te), len(te) // 2, replace=False)] = True
            keeps.append(k)
            rs.append(ret[te])
        rr = np.concatenate(rs)
        kk = np.concatenate(keeps)
        out.append(float(rr[kk].mean() - rr.mean()))
    return np.array(out)


def main():
    t0, t1 = rdata.SPLITS["trainval"]
    reg = ml_lib.registry()
    st = reg["supertrend"]
    p = ml_lib.center_combo(st)
    d = ml_lib.dataset("supertrend", p, "240", t0, t1)
    kind = u"логрег"
    real = ml_meta.run_config(d, kind)
    rd = real[u"половина лучших"]["dmean"]

    print(u"ЛУЧШИЙ СЛУЧАЙ ИЗ 78: supertrend, 4ч, срединное сочетание, логрег")
    print(u"   входов: %d, окон проверки: %d, зазор: %.1f дня"
          % (len(d["y"]), len(real["folds"]), real["folds"][0]["embargo"]))
    print(u"   без фильтра: %+.2f%% в месяц, доходность на вход %+.4f%%"
          % (100 * real["base"]["mo"], 100 * real["base"]["mean"]))
    print(u"   с фильтром:  %+.2f%% в месяц, доходность на вход %+.4f%%"
          % (100 * real[u"половина лучших"]["mo"],
             100 * real[u"половина лучших"]["mean"]))
    print(u"   прибавка: %+.4f%% на вход" % (100 * rd))

    print(u"\nНУЛЬ 1: ТА ЖЕ ПРОЦЕДУРА НА ПЕРЕМЕШАННЫХ МЕТКАХ, %d повторов"
          % N_SHUF)
    a = null_shuffled(d, kind, N_SHUF)
    print(u"   прибавка на шуме: среднее %+.4f%%, разброс %.4f%%, "
          u"95-й перцентиль %+.4f%%"
          % (100 * a.mean(), 100 * a.std(ddof=1),
             100 * np.percentile(a, 95)))
    pv = float((a >= rd).mean())
    print(u"   доля шумовых прогонов не хуже настоящего: %.1f%%  (это и есть p)"
          % (100 * pv))

    print(u"\nНУЛЬ 2: ПРОСТО СЛУЧАЙНАЯ ПОЛОВИНА ВХОДОВ, 5000 повторов")
    b = null_random(d, 5000)
    print(u"   прибавка наугад: среднее %+.4f%%, разброс %.4f%%, "
          u"95-й перцентиль %+.4f%%"
          % (100 * b.mean(), 100 * b.std(ddof=1),
             100 * np.percentile(b, 95)))
    print(u"   доля случайных выборок не хуже настоящего: %.1f%%"
          % (100 * float((b >= rd).mean())))

    print(u"\nПОПРАВКА НА ЧИСЛО ПОПЫТОК")
    print(u"   Этот случай — лучший из 78 посчитанных конфигураций.")
    print(u"   При p = %.3f на одну попытку ожидаемое число таких же "
          u"«находок»" % pv)
    print(u"   среди 78 составляет %.1f. Найдена одна." % (78 * pv))
    pfam = 1.0 - (1.0 - pv) ** 78 if pv > 0 else 1.0
    print(u"   вероятность увидеть хотя бы одну такую при 78 попытках "
          u"и полном отсутствии перевеса: %.0f%%" % (100 * pfam))

    print(u"\n\nКРИВАЯ НАКОПЛЕННОЙ ПРИБЫЛИ, С ФИЛЬТРОМ И БЕЗ")
    print(u"   Капитал, начатый с 1.000, по внеобучающим окнам подряд.")
    print(u"   Риск на сделку 1%%. Слева — правило как есть, справа — оно же,")
    print(u"   но входы, которые модель сочла плохими, пропущены.\n")
    print(u"   %-11s %-4s %-8s %10s %10s %10s"
          % (u"правило", u"тф", u"модель", u"без", u"с фильтр", u"разница"))
    for name in ml_meta.RULES:
        pc = ml_lib.center_combo(reg[name])
        for tf in ("60", "240"):
            dd = ml_lib.dataset(name, pc, tf, t0, t1, causal_first=False)
            if dd is None:
                continue
            for kk in (u"логрег", u"бустинг"):
                r = ml_meta.run_config(dd, kk)
                if r is None:
                    continue
                e0 = r["base"]["eq"]
                e1 = r[u"половина лучших"]["eq"]
                print(u"   %-11s %-4s %-8s %10.3f %10.3f %+9.3f"
                      % (name, tf, kk, e0, e1, e1 - e0))


if __name__ == "__main__":
    main()
