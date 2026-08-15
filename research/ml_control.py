# -*- coding: utf-8 -*-
u"""Контрольные опыты: отличается ли фильтр модели от выбрасывания наугад.

ЗАЧЕМ ЭТО ОТДЕЛЬНО. В таблице ml_meta фильтр часто «улучшает» результат, и
почти всегда по одной и той же причине: правило само по себе убыточно, а
фильтр сокращает число сделок. Меньше сделок — меньше убыток. Это не перевес,
это выключенный компьютер.

Здесь спрашивается ровно то, что нужно спросить:

  ОПЫТ 1. СЛУЧАЙНЫЙ ФИЛЬТР ТОГО ЖЕ РАЗМЕРА. Берём столько же входов, сколько
  оставила модель, но наугад — две тысячи раз. Если результат модели лежит в
  середине этого облака, модель не отличила ничего: любая монетка, отбросившая
  столько же сделок, дала бы то же самое.

  ОПЫТ 2. ПЕРЕМЕШАННАЯ РАЗМЕТКА. Модель обучается на тех же признаках, но
  метки «прибыльно/убыточно» перемешаны внутри обучающего куска. Настоящей
  связи там нет по построению. Насколько хорошо выглядит такая модель — и есть
  цена честного нуля для нашей процедуры: всё, что не выше этого, объясняется
  свободой подгонки, а не рынком.

  ОПЫТ 3. СВЯЗЬ ВЕРОЯТНОСТИ С ДОХОДНОСТЬЮ. Ранговая связь предсказанной
  вероятности с фактической доходностью входа (информационный коэффициент).
  Прямой ответ на вопрос «упорядочивает ли модель входы», без порогов и
  без бухгалтерии.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import ml_lib   # noqa: E402
import ml_meta  # noqa: E402
import rdata    # noqa: E402

from sklearn.metrics import roc_auc_score  # noqa: E402

OUT = os.path.join(DIR, "out")
N_RAND = 2000
N_SHUF = 40


def rank_ic(p, r):
    a = np.argsort(np.argsort(p)).astype(float)
    b = np.argsort(np.argsort(r)).astype(float)
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def random_control(p, ret, t_in, t_out, keep, seed=0):
    u"""Где лежит результат модели среди случайных выборок того же размера."""
    n_keep = int(keep.sum())
    if n_keep < 10 or n_keep >= len(ret):
        return None
    real_mo = ml_lib.monthly_rate(ret[keep], t_in[keep], t_out[keep])
    real_mean = float(ret[keep].mean())
    rng = np.random.default_rng(seed)
    mos = np.empty(N_RAND)
    mns = np.empty(N_RAND)
    n = len(ret)
    for s in range(N_RAND):
        idx = rng.choice(n, n_keep, replace=False)
        idx.sort()
        mos[s] = ml_lib.monthly_rate(ret[idx], t_in[idx], t_out[idx])
        mns[s] = ret[idx].mean()
    return dict(real_mo=real_mo, real_mean=real_mean,
                pct_mo=float((mos < real_mo).mean()),
                pct_mean=float((mns < real_mean).mean()),
                rand_mo_med=float(np.median(mos)),
                rand_mean_med=float(np.median(mns)), n_keep=n_keep)


def shuffled_labels(d, kind, n_rep=N_SHUF, seed=0):
    u"""Та же процедура на перемешанных метках — цена свободы подгонки."""
    t0, t1 = rdata.SPLITS["trainval"]
    folds = ml_lib.purged_folds(d["t_in"], d["t_out"], t0, t1, ml_meta.N_FOLDS)
    X, y, ret = d["X"], d["y"], d["ret"]
    rng = np.random.default_rng(seed)
    aucs, dmeans = [], []
    for rep in range(n_rep):
        ps, ys, rs, keeps = [], [], [], []
        for f in folds:
            tr, te = f["train"], f["test"]
            ysh = y[tr][rng.permutation(len(tr))]
            m = ml_meta.make_model(kind, seed=rep)
            m.fit(X[tr], ysh)
            p_tr = m.predict_proba(X[tr])[:, 1]
            p_te = m.predict_proba(X[te])[:, 1]
            ps.append(p_te)
            ys.append(y[te])
            rs.append(ret[te])
            keeps.append(p_te >= np.median(p_tr))
        p = np.concatenate(ps)
        yy = np.concatenate(ys)
        rr = np.concatenate(rs)
        kk = np.concatenate(keeps)
        try:
            aucs.append(float(roc_auc_score(yy, p)))
        except ValueError:
            pass
        if kk.sum() >= 10:
            dmeans.append(float(rr[kk].mean() - rr.mean()))
    return np.array(aucs), np.array(dmeans)


def main():
    path = os.path.join(OUT, "ml_pred.npz")
    if not os.path.exists(path):
        raise SystemExit(u"сначала ml_meta.py — нет out/ml_pred.npz")
    z = np.load(path, allow_pickle=False)
    keys = sorted({k.split("@")[0] for k in z.files})

    print(u"ОПЫТ 1. ФИЛЬТР МОДЕЛИ ПРОТИВ ВЫБРАСЫВАНИЯ НАУГАД")
    print(u"Столько же входов, сколько оставила модель, но выбранных монеткой,")
    print(u"%d раз. «Место» — доля случайных выборок ХУЖЕ модели. 50%% значит,"
          % N_RAND)
    print(u"что модель неотличима от монетки; 95%% и выше — что отличима.\n")
    print(u"   %-28s %6s %9s %9s %7s"
          % (u"конфигурация", u"вход", u"модель", u"монетка", u"место"))
    print(u"   %-28s %6s %9s %9s %7s"
          % ("", u"ов", u"%/мес", u"%/мес", ""))
    places = []
    ics = []
    for k in keys:
        p = z["%s@p" % k]
        ret = z["%s@ret" % k]
        ti = z["%s@t_in" % k]
        to = z["%s@t_out" % k]
        keep = z["%s@keep_med" % k].astype(bool)
        r = random_control(p, ret, ti, to, keep)
        ic = rank_ic(p, ret)
        ics.append(ic)
        if r is None:
            print(u"   %-28s   фильтр вырожден" % k)
            continue
        places.append(r["pct_mo"])
        print(u"   %-28s %6d %+8.2f%% %+8.2f%% %6.0f%%"
              % (k, r["n_keep"], 100 * r["real_mo"], 100 * r["rand_mo_med"],
                 100 * r["pct_mo"]))
    pl = np.array(places)
    print(u"\n   среднее место: %.0f%% (при отсутствии перевеса ожидается 50%%)"
          % (100 * pl.mean()))
    print(u"   выше 95%% места: %d из %d; ниже 5%%: %d"
          % (int((pl > 0.95).sum()), len(pl), int((pl < 0.05).sum())))

    print(u"\n\nОПЫТ 3. СВЯЗЬ ПРЕДСКАЗАННОЙ ВЕРОЯТНОСТИ С ФАКТИЧЕСКОЙ ДОХОДНОСТЬЮ")
    print(u"   (ранговая; положительная и устойчивая означала бы, что модель")
    print(u"    действительно упорядочивает входы по качеству)")
    for k, ic in zip(keys, ics):
        print(u"   %-28s IC = %+.3f" % (k, ic))
    ica = np.array([x for x in ics if np.isfinite(x)])
    print(u"   среднее IC: %+.3f, положительных: %d из %d"
          % (ica.mean(), int((ica > 0).sum()), len(ica)))
    se = ica.std(ddof=1) / np.sqrt(len(ica))
    print(u"   среднее ± ошибка: %+.3f ± %.3f  ->  %s"
          % (ica.mean(), se,
             u"неотличимо от нуля" if abs(ica.mean()) < 2 * se
             else u"отличимо от нуля"))

    print(u"\n\nОПЫТ 2. ТА ЖЕ ПРОЦЕДУРА НА ПЕРЕМЕШАННЫХ МЕТКАХ")
    print(u"Связи в данных нет по построению. Всё, что модель показывает")
    print(u"здесь, — цена свободы подгонки, а не свойство рынка.\n")
    t0, t1 = rdata.SPLITS["trainval"]
    reg = ml_lib.registry()
    print(u"   %-22s %14s %14s"
          % (u"конфигурация", u"AUC настоящий", u"AUC на шуме"))
    for name, tf in ((u"supertrend", "60"), (u"c_willr", "60"),
                     (u"supertrend", "240")):
        p0 = ml_lib.center_combo(reg[name])
        d = ml_lib.dataset(name, p0, tf, t0, t1)
        for kind in (u"логрег", u"бустинг"):
            real = ml_meta.run_config(d, kind)
            aucs, dmeans = shuffled_labels(d, kind)
            print(u"   %-22s %13.3f  %6.3f ± %.3f (p95 %.3f)"
                  % ("%s|%s|%s" % (name, tf, kind), real["auc_pool"],
                     aucs.mean(), aucs.std(ddof=1),
                     float(np.percentile(aucs, 95))))
            rd = real[u"половина лучших"]["dmean"]
            print(u"      прибавка на вход: настоящая %+.4f%%, "
                  u"на шуме %+.4f%% ± %.4f, доля шумовых лучше настоящей %.0f%%"
                  % (100 * rd, 100 * dmeans.mean(), 100 * dmeans.std(ddof=1),
                     100 * float((dmeans >= rd).mean())))

    with open(os.path.join(OUT, "ml_control.json"), "w",
              encoding="utf-8") as fh:
        json.dump(dict(places=list(pl), ics=list(ics)), fh,
                  ensure_ascii=False, default=float)
    print(u"\nсохранено -> out/ml_control.json")


if __name__ == "__main__":
    main()
