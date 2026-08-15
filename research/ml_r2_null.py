# -*- coding: utf-8 -*-
u"""Цена находки: сколько такого же даёт та же процедура, когда учить нечему.

В ml_r2_pool объединённая выборка дала прибавку +0.012% на вход при пороге
«медиана» и +0.025% при пороге «верх 30%», положительную в 20 случаях из 24.
Разброс по конфигурациям при этом крошечный — но он и не может служить
мерилом: все 24 конфигурации считаны на ОДНИХ И ТЕХ ЖЕ сделках, их ошибки
почти полностью общие. «Среднее ± ошибка по конфигурациям» здесь измеряет
согласие моделей между собой, а не надёжность числа.

Настоящих мерил ровно четыре, и здесь считаются все.

  1. СЛУЧАЙНЫЙ ФИЛЬТР ТОГО ЖЕ РАЗМЕРА. Оставить столько же входов, но
     монеткой, 5000 раз. Даёт честный разброс самой ВЕЛИЧИНЫ при полном
     отсутствии умения — с настоящими, тяжёлыми хвостами доходности, а не с
     нормальным приближением.

  2. ПЕРЕМЕШАННЫЕ МЕТКИ. Признаки настоящие, метка «прибыльно» перемешана
     внутри обучающего куска. Модель по-прежнему обучается, отбирает и имеет
     ровно ту же свободу — но выучивать нечего. Это цена свободы подгонки.

  3. ПО ОКНАМ ОТДЕЛЬНО. Свойство рынка живёт во всех окнах. Одно окно из пяти
     это одно окно.

  4. НЕ СТАВКА ЛИ ЭТО НА СТОРОНУ И НЕ ТРИ ЛИ ЭТО СДЕЛКИ. Доля длинных среди
     оставленных против доли среди всех (обучение пришлось на растущий рынок),
     и та же прибавка на подрезанных по 1-му и 99-му перцентилю доходностях —
     если прибавка держится на нескольких удачных хвостах, подрезка её убьёт.
"""
import json
import os
import sys
import time

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import ml_r2_lib as L      # noqa: E402
import ml_r2_pool as P     # noqa: E402

OUT = os.path.join(DIR, "out")
N_RAND = 5000
N_SHUF = 40

# конфигурации объявлены здесь: две «обычные» (по одной на модель) и две
# лучшие из решётки ml_r2_pool. Лучшие помечены как лучшие и оцениваются с
# поправкой на 96 попыток, из которых они вышли.
CASES = [
    (u"ансамбль", u"логрег", u"|доход|", False, u"обычная"),
    (u"ансамбль", u"бустинг", u"|доход|", False, u"обычная"),
    (u"ансамбль", u"бустинг", u"нет", False, u"лучшая из 96"),
    (u"по одному из семейства", u"бустинг", u"нет", True, u"вторая из 96"),
]
SETS = {u"ансамбль": P.RULES4, u"по одному из семейства": P.RULES10}


def keep_masks(d, folds, mk, wk, ident, seed=0, shuffle=False):
    u"""Маски «оставить» по обоим порогам плюс сопутствующие ряды."""
    names = d["names"]
    cols = (list(range(len(names))) if ident
            else [i for i, n in enumerate(names) if n not in L.IDENT])
    X = d["X"][:, cols]
    y, ret = d["y"], d["ret"]
    w = L.make_weights(wk, y, ret, d["t_in"], d["t_out"])
    rng = np.random.default_rng(seed + 991)
    km, kt, rr, cc, ff, sd = [], [], [], [], [], []
    j_side = names.index("side")
    for i, f in enumerate(folds):
        tr, te = f["train"], f["test"]
        yt = y[tr][rng.permutation(len(tr))] if shuffle else y[tr]
        jj = P.live_cols(X[tr])
        m = P.fit(P.make_model(mk, seed), mk, X[tr][:, jj], yt, w[tr])
        p_tr = m.predict_proba(X[tr][:, jj])[:, 1]
        p_te = m.predict_proba(X[te][:, jj])[:, 1]
        km.append(p_te >= float(np.median(p_tr)))
        kt.append(p_te >= float(np.percentile(p_tr, 70)))
        rr.append(ret[te])
        cc.append(d["cfg"][te])
        ff.append(np.full(len(te), i))
        sd.append(d["X"][te, j_side])
    return (np.concatenate(km), np.concatenate(kt), np.concatenate(rr),
            np.concatenate(cc), np.concatenate(ff), np.concatenate(sd))


def dmean_in(ret, cfg, keep):
    u"""Прибавка ВНУТРИ каждой пары (правило, тф), усреднённая по ним."""
    parts, wts = [], []
    for c in np.unique(cfg):
        s = cfg == c
        if s.sum() < 50 or (s & keep).sum() < 20:
            continue
        parts.append(ret[s & keep].mean() - ret[s].mean())
        wts.append(int(s.sum()))
    return float(np.average(parts, weights=wts)) if parts else float("nan")


def rand_null(ret, cfg, n_keep_by_cfg, n_rep=N_RAND, seed=0):
    u"""Случайный фильтр того же размера ВНУТРИ каждой пары (правило, тф)."""
    rng = np.random.default_rng(seed)
    out = np.empty(n_rep)
    idx_by_cfg = {c: np.flatnonzero(cfg == c) for c in np.unique(cfg)}
    for s in range(n_rep):
        keep = np.zeros(len(ret), dtype=bool)
        for c, idx in idx_by_cfg.items():
            k = n_keep_by_cfg.get(c, 0)
            if k <= 0 or k >= len(idx):
                continue
            keep[rng.choice(idx, k, replace=False)] = True
        out[s] = dmean_in(ret, cfg, keep)
    return out


def main():
    t0 = time.time()
    data, folds = {}, {}
    for tag, rules in SETS.items():
        d = L.pooled(rules)
        data[tag] = d
        folds[tag] = P.purged_folds(d["t_in"], d["t_out"])

    report = []
    for tag, mk, wk, ident, note in CASES:
        d, fl = data[tag], folds[tag]
        km, kt, rr, cc, ff, sd = keep_masks(d, fl, mk, wk, ident)
        name = u"%s|%s|%s|%s" % (tag, mk, wk, u"все" if ident else u"обст.")
        print(u"\n" + u"=" * 78)
        print(u"%s   (%s)" % (name, note))
        print(u"   сделок вне обучения: %d, окон: %d" % (len(rr), len(fl)))
        for ptag, keep in ((u"медиана", km), (u"верх 30%", kt)):
            real = dmean_in(rr, cc, keep)
            n_by = {c: int((keep & (cc == c)).sum()) for c in np.unique(cc)}
            nul = rand_null(rr, cc, n_by)
            se = float(nul.std(ddof=1))
            pv = float((nul >= real).mean())
            print(u"\n   ПОРОГ «%s»: оставлено %d из %d"
                  % (ptag, int(keep.sum()), len(rr)))
            print(u"      прибавка внутри пар: %+.4f%% на вход" % (100 * real))
            print(u"      1. случайный фильтр того же размера (%d раз): "
                  u"%+.4f%% ± %.4f%%" % (N_RAND, 100 * nul.mean(), 100 * se))
            print(u"         место настоящего среди случайных: %.1f%% "
                  u"(50%% = неотличимо)" % (100 * (1 - pv)))
            print(u"         отношение к разбросу: %.2f сигмы" % (real / se))
            print(u"         вероятность увидеть такое монеткой: %.3f; "
                  u"с поправкой на 96 попыток: %.2f"
                  % (pv, min(1.0, 96 * pv)))
            # подрезка хвостов
            lo, hi = np.percentile(rr, [1, 99])
            rw = np.clip(rr, lo, hi)
            print(u"      4а. на подрезанных по 1/99 перцентилю доходностях: "
                  u"%+.4f%%" % (100 * dmean_in(rw, cc, keep)))
            ls_all = float((sd > 0).mean())
            ls_keep = float((sd[keep] > 0).mean())
            print(u"      4б. доля длинных: среди всех %.0f%%, среди "
                  u"оставленных %.0f%%" % (100 * ls_all, 100 * ls_keep))
            print(u"      3. по окнам:", end=" ")
            per = []
            for i in range(len(fl)):
                s = ff == i
                v = dmean_in(rr[s], cc[s], keep[s])
                per.append(v)
                print(u"%+.3f%%" % (100 * v), end=" ")
            per = np.array([x for x in per if np.isfinite(x)])
            print(u"  положительных %d из %d" % (int((per > 0).sum()), len(per)))
            report.append(dict(case=name, note=note, thr=ptag, real=real,
                               null_mean=float(nul.mean()), null_sd=se,
                               p=pv, per_fold=list(per),
                               trim=dmean_in(rw, cc, keep),
                               long_all=ls_all, long_keep=ls_keep))

        # перемешанные метки — только для порога «верх 30%», он самый выгодный
        print(u"\n   2. ПЕРЕМЕШАННЫЕ МЕТКИ, %d повторов (цена свободы подгонки)"
              % N_SHUF)
        vals = []
        for rep in range(N_SHUF):
            _, kt2, rr2, cc2, _, _ = keep_masks(d, fl, mk, wk, ident,
                                                seed=rep, shuffle=True)
            vals.append(dmean_in(rr2, cc2, kt2))
        vals = np.array([v for v in vals if np.isfinite(v)])
        real_top = dmean_in(rr, cc, kt)
        pv = float((vals >= real_top).mean())
        print(u"      на шуме: %+.4f%% ± %.4f%%, 95-й перцентиль %+.4f%%"
              % (100 * vals.mean(), 100 * vals.std(ddof=1),
                 100 * np.percentile(vals, 95)))
        print(u"      настоящая %+.4f%%; доля шумовых не хуже: %.1f%%"
              % (100 * real_top, 100 * pv))
        report.append(dict(case=name, thr=u"верх 30%|шум", real=real_top,
                           shuf_mean=float(vals.mean()),
                           shuf_sd=float(vals.std(ddof=1)), p=pv))

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "ml_r2_null.json"), "w",
              encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, default=float)
    print(u"\nсохранено -> out/ml_r2_null.json   (%.0f с)" % (time.time() - t0))


if __name__ == "__main__":
    main()
