# -*- coding: utf-8 -*-
u"""Пол под моделью: умеет ли хоть ОДИН признак отличить хороший вход от плохого.

ЗАЧЕМ. У отрицательного ответа модели всегда есть два объяснения, и они
противоположны по смыслу: либо в данных нечего искать, либо модель слишком
груба (или слишком гибка) и не находит того, что есть. Разделить их можно
самым простым из возможных фильтров: один признак, порог — его медиана на
обучающем куске, сторона («выше» или «ниже») тоже выбрана по обучающему куску.
Одна ручка, никакой гибкости, никакого переобучения сверх выбора стороны.

Если такой фильтр не работает НИ НА ОДНОМ из сорока с лишним признаков — дело
не в модели. Если работает на каком-то, а модель этого не нашла — виновата
модель, и её надо чинить, а не хоронить.

СЧИТАЕТСЯ ЧЕСТНО: те же окна с зазором, порог и сторона только из обучения,
мерило — доходность оставленного входа минус доходность всех входов внутри
каждой пары (правило, тф). Рядом печатается разброс той же величины при
случайном отборе того же размера: сорок семь попыток сами по себе выдают
2-3 «сигмы», и без этой поправки любой такой список читается неверно.
"""
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import ml_r2_lib as L   # noqa: E402
import ml_r2_null as N  # noqa: E402
import ml_r2_pool as P  # noqa: E402

N_RAND = 3000


def one_feature(d, folds, j):
    u"""Один признак, порог и сторона — из обучающего куска, применение — вне."""
    X, ret = d["X"], d["ret"]
    keeps, tes = [], []
    for f in folds:
        tr, te = f["train"], f["test"]
        v_tr, v_te = X[tr, j], X[te, j]
        ok = np.isfinite(v_tr)
        if ok.sum() < 200 or len(np.unique(v_tr[ok])) < 5:
            return None
        thr = float(np.median(v_tr[ok]))
        hi = v_tr >= thr
        # сторону выбирает ОБУЧЕНИЕ: где средняя доходность была выше
        m_hi = ret[tr][hi & ok].mean() if (hi & ok).sum() > 20 else -9
        m_lo = ret[tr][(~hi) & ok].mean() if ((~hi) & ok).sum() > 20 else -9
        want_hi = m_hi >= m_lo
        k = (v_te >= thr) if want_hi else (v_te < thr)
        k = k & np.isfinite(v_te)
        keeps.append(k)
        tes.append(te)
    keep = np.concatenate(keeps)
    te = np.concatenate(tes)
    if keep.sum() < 100:
        return None
    return keep, d["ret"][te], d["cfg"][te]


def scan(d, folds, title):
    print(u"\n%s" % title)
    print(u"   сделок вне обучения: %d" % sum(len(f["test"]) for f in folds))
    res = []
    for j, name in enumerate(d["names"]):
        r = one_feature(d, folds, j)
        if r is None:
            continue
        keep, ret, cfg = r
        res.append((name, N.dmean_in(ret, cfg, keep), int(keep.sum()),
                    len(ret)))
    if not res:
        print(u"   ни один признак не дал достаточного числа сделок")
        return
    # разброс той же величины при случайном отборе того же размера
    keep, ret, cfg = one_feature(d, folds, 0)
    n_by = {c: int((keep & (cfg == c)).sum()) for c in np.unique(cfg)}
    nul = N.rand_null(ret, cfg, n_by, n_rep=N_RAND)
    sd = float(nul.std(ddof=1))
    res.sort(key=lambda a: -a[1])
    print(u"   разброс при случайном отборе того же размера: ±%.4f%%" % (100 * sd))
    print(u"   %-14s %9s %8s   %-14s %9s %8s"
          % (u"признак", u"прибавка", u"сигм", u"признак", u"прибавка",
             u"сигм"))
    top, bot = res[:8], res[-8:][::-1]
    for a, b in zip(top, bot):
        print(u"   %-14s %+8.4f%% %7.2f   %-14s %+8.4f%% %7.2f"
              % (a[0], 100 * a[1], a[1] / sd, b[0], 100 * b[1], b[1] / sd))
    v = np.array([r[1] for r in res])
    print(u"   всего признаков: %d; положительных %d; среднее %+.4f%%"
          % (len(v), int((v > 0).sum()), 100 * v.mean()))
    print(u"   лучший: %s, %.2f сигмы. Ожидаемый максимум из %d независимых"
          % (res[0][0], v.max() / sd, len(v)))
    print(u"   попыток при полном отсутствии умения: около %.2f сигмы."
          % _expected_max(len(v)))
    print(u"   %s"
          % (u"ЛУЧШИЙ ПРИЗНАК НЕ ВЫХОДИТ ЗА ОЖИДАЕМЫЙ ШУМ"
             if v.max() / sd <= _expected_max(len(v))
             else u"лучший признак выше ожидаемого шума — стоит проверять"))


def _expected_max(n):
    u"""Ожидаемый максимум n независимых стандартных нормальных величин."""
    return float(np.sqrt(2 * np.log(max(n, 2))) -
                 (np.log(np.log(max(n, 3))) + np.log(4 * np.pi))
                 / (2 * np.sqrt(2 * np.log(max(n, 2)))))


def main():
    d = L.pooled(P.RULES4)
    scan(d, P.purged_folds(d["t_in"], d["t_out"]),
         u"А. АНСАМБЛЬ, 1ч+4ч, все пять монет")

    m4 = np.array([c.endswith("|240") for c in d["cfg"]])
    d4 = {k: (v[m4] if isinstance(v, np.ndarray) else v) for k, v in d.items()}
    d4["names"] = d["names"]
    scan(d4, P.purged_folds(d4["t_in"], d4["t_out"], min_train=300),
         u"Б. ТОЛЬКО 4ч — то, что торгуется по FINAL_SPEC")

    d10 = L.pooled(P.RULES10)
    scan(d10, P.purged_folds(d10["t_in"], d10["t_out"]),
         u"В. ПО ОДНОМУ ПРАВИЛУ ИЗ КАЖДОГО СЕМЕЙСТВА, 1ч+4ч")


if __name__ == "__main__":
    main()
