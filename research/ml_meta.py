# -*- coding: utf-8 -*-
u"""Мета-разметка: бьёт ли модель опорную линию «брать все входы подряд».

ЧТО СЧИТАЕТСЯ ОТВЕТОМ. Не точность и не AUC — они умеют быть красивыми при
нулевой пользе. Ответом считается ровно одно: на кусках, которых модель не
видела, кривая капитала ТОГО ЖЕ правила с фильтром выше, чем без фильтра.
Всё остальное печатается как диагностика.

ПОЧЕМУ ЗДЕСЬ НЕЛЬЗЯ ПРОСТО ПОСМОТРЕТЬ ТОЧНОСТЬ. Доля выигрышных входов у
трендового правила около 30%: оно живёт длинными редкими выигрышами. Модель,
поднявшая точность, может при этом отсечь именно те входы, которые кормят всю
кривую. Поэтому рядом с точностью всегда стоит доходность, и решает она.

ПОРОГ ФИЛЬТРА. Два, оба взяты ТОЛЬКО из обучающего куска:
  * 0.5 — «модель считает вход скорее прибыльным»;
  * медиана обучающих вероятностей — «половина лучших входов», порог не
    зависит от того, насколько модель откалибрована.
Ни один порог не подбирается по проверочному куску.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import ml_lib   # noqa: E402
import rdata    # noqa: E402

from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.impute import SimpleImputer                     # noqa: E402
from sklearn.linear_model import LogisticRegression          # noqa: E402
from sklearn.metrics import roc_auc_score                    # noqa: E402
from sklearn.pipeline import Pipeline                        # noqa: E402
from sklearn.preprocessing import StandardScaler             # noqa: E402

OUT = os.path.join(DIR, "out")
RULES = ["supertrend", "bos", "vol_spike", "c_willr"]
N_FOLDS = 5


def make_model(kind, seed=0):
    if kind == u"логрег":
        return Pipeline([
            ("imp", SimpleImputer(strategy="median")),
            ("sc", StandardScaler()),
            ("lr", LogisticRegression(C=0.1, max_iter=2000,
                                      class_weight="balanced")),
        ])
    return HistGradientBoostingClassifier(
        max_depth=3, max_iter=200, learning_rate=0.05,
        min_samples_leaf=40, l2_regularization=1.0,
        early_stopping=True, validation_fraction=0.2, random_state=seed)


def run_config(d, kind, seed=0, n_folds=N_FOLDS):
    u"""Прогон одной пары (правило, модель) по всем окнам. Возвращает сводку."""
    t0, t1 = rdata.SPLITS["trainval"]
    folds = ml_lib.purged_folds(d["t_in"], d["t_out"], t0, t1, n_folds)
    if not folds:
        return None
    X, y, ret = d["X"], d["y"], d["ret"]
    rows = []
    all_p, all_y, all_ret, all_tin, all_tout = [], [], [], [], []
    imps = []
    for f in folds:
        tr, te = f["train"], f["test"]
        m = make_model(kind, seed)
        m.fit(X[tr], y[tr])
        p_tr = m.predict_proba(X[tr])[:, 1]
        p_te = m.predict_proba(X[te])[:, 1]
        thr_med = float(np.median(p_tr))
        acc_tr = float(((p_tr >= 0.5).astype(int) == y[tr]).mean())
        acc_te = float(((p_te >= 0.5).astype(int) == y[te]).mean())
        try:
            auc = float(roc_auc_score(y[te], p_te))
        except ValueError:
            auc = float("nan")
        rows.append(dict(n_train=len(tr), n_test=len(te), acc_tr=acc_tr,
                         acc_te=acc_te, auc=auc, thr_med=thr_med,
                         embargo=f["embargo_days"]))
        all_p.append(p_te)
        all_y.append(y[te])
        all_ret.append(ret[te])
        all_tin.append(d["t_in"][te])
        all_tout.append(d["t_out"][te])
        imps.append(perm_importance(m, X[te], y[te], seed))
        rows[-1]["thr_med_val"] = thr_med
    p = np.concatenate(all_p)
    yy = np.concatenate(all_y)
    rr = np.concatenate(all_ret)
    ti = np.concatenate(all_tin)
    to = np.concatenate(all_tout)
    # пороги применяются пофолдово: медиана берётся из СВОЕГО обучения
    keep_med = np.concatenate([
        pp >= r["thr_med"] for pp, r in zip(all_p, rows)])
    keep_half = keep_med
    keep_05 = p >= 0.5

    base = dict(n=len(rr), mo=ml_lib.monthly_rate(rr, ti, to),
                mean=float(rr.mean()), eq=ml_lib.compound(rr),
                wr=float(yy.mean()))
    out = dict(kind=kind, folds=rows, base=base,
               acc_tr=float(np.mean([r["acc_tr"] for r in rows])),
               acc_te=float(np.mean([r["acc_te"] for r in rows])),
               auc=float(np.nanmean([r["auc"] for r in rows])),
               auc_pool=_auc(yy, p))
    # ВЫРОЖДЕННЫЙ СЛУЧАЙ. Если порог отсекает почти всё, «доходность в месяц»
    # такого фильтра близка к нулю — и на убыточном правиле это выглядит как
    # улучшение. Не торговать не значит иметь перевес, поэтому такие случаи
    # помечаются и в сводку по улучшению не идут.
    for tag, keep in ((u"p>=0.5", keep_05), (u"половина лучших", keep_half)):
        n_keep = int(keep.sum())
        deg = n_keep < max(30, 0.1 * len(rr))
        r2 = rr[keep]
        out[tag] = dict(
            n=n_keep, degenerate=bool(deg),
            mo=ml_lib.monthly_rate(r2, ti[keep], to[keep]) if n_keep else 0.0,
            mean=float(r2.mean()) if n_keep else 0.0,
            eq=ml_lib.compound(r2) if n_keep else 1.0,
            wr=float(yy[keep].mean()) if n_keep else 0.0)
        out[tag]["delta"] = out[tag]["mo"] - base["mo"]
        out[tag]["dmean"] = out[tag]["mean"] - base["mean"]
    # терцили: есть ли вообще упорядочивание входов по качеству
    q = np.quantile(p, [1 / 3.0, 2 / 3.0])
    terc = []
    for lo, hi in ((-1e9, q[0]), (q[0], q[1]), (q[1], 1e9)):
        s = (p >= lo) & (p < hi)
        terc.append(dict(n=int(s.sum()),
                         mean=float(rr[s].mean()) if s.any() else 0.0,
                         wr=float(yy[s].mean()) if s.any() else 0.0))
    out["terciles"] = terc
    out["importance"] = imps
    # СОХРАНЯЕМ ПРЕДСКАЗАНИЯ, чтобы контрольные опыты не переобучали модель
    # заново и работали ровно с теми же числами, что и таблица выше.
    out["_pred"] = dict(p=p, y=yy, ret=rr, t_in=ti, t_out=to,
                        keep_med=keep_med,
                        fold=np.concatenate([np.full(len(a), i)
                                             for i, a in enumerate(all_p)]))
    return out


def _auc(y, p):
    try:
        return float(roc_auc_score(y, p))
    except ValueError:
        return float("nan")


def perm_importance(model, X, y, seed=0, n_rep=5):
    u"""Важность признака = насколько падает AUC, если признак перемешать.

    Считается на ПРОВЕРОЧНОМ куске: важность на обучении показывает, за что
    модель уцепилась, а не что реально помогает вне обучения.
    """
    rng = np.random.default_rng(seed)
    base = _auc(y, model.predict_proba(X)[:, 1])
    if not np.isfinite(base):
        return [0.0] * X.shape[1]
    out = []
    for j in range(X.shape[1]):
        drops = []
        for _ in range(n_rep):
            Z = X.copy()
            Z[:, j] = Z[rng.permutation(len(Z)), j]
            drops.append(base - _auc(y, model.predict_proba(Z)[:, 1]))
        out.append(float(np.mean(drops)))
    return out


def spearman(a, b):
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    if np.std(ra) == 0 or np.std(rb) == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def main():
    t0, t1 = rdata.SPLITS["trainval"]
    reg = ml_lib.registry()
    tfs = ("60", "240")
    results = {}
    print(u"МЕТА-РАЗМЕТКА: МОДЕЛЬ ФИЛЬТРУЕТ ВХОДЫ ГОТОВОГО ПРАВИЛА")
    print(u"Обучение и проверка — только TRAIN+VAL. Экзамен не открывался.")
    print(u"Разбиение по времени, %d окна вперёд, зазор = самое долгое "
          u"удержание.\n" % N_FOLDS)
    hdr = (u"%-11s %-4s %-8s | %6s %6s %6s | %8s %8s %8s | %8s %8s"
           % (u"правило", u"тф", u"модель", u"точн", u"точн", u"AUC",
              u"всё", u"p>=.5", u"полов", u"на вход", u"с фильтр"))
    print(hdr)
    print(u"%-11s %-4s %-8s | %6s %6s %6s | %8s %8s %8s | %8s %8s"
          % ("", "", "", u"обуч", u"вне", u"вне", u"%/мес", u"%/мес",
             u"%/мес", u"без, %", u"полов, %"))
    print("-" * len(hdr))
    for name in RULES:
        p = ml_lib.center_combo(reg[name])
        for tf in tfs:
            d = ml_lib.dataset(name, p, tf, t0, t1)
            if d is None:
                continue
            for kind in (u"логрег", u"бустинг"):
                r = run_config(d, kind)
                if r is None:
                    continue
                results["%s|%s|%s" % (name, tf, kind)] = r
                a, b = r[u"p>=0.5"], r[u"половина лучших"]
                s05 = u"%+6.2f%%%s" % (100 * a["mo"],
                                       u"!" if a["degenerate"] else u" ")
                shf = u"%+6.2f%%%s" % (100 * b["mo"],
                                       u"!" if b["degenerate"] else u" ")
                print(u"%-11s %-4s %-8s | %6.3f %6.3f %6.3f | %+7.2f%% "
                      u"%8s %8s | %7.3f%% %7.3f%%"
                      % (name, tf, kind, r["acc_tr"], r["acc_te"],
                         r["auc_pool"], 100 * r["base"]["mo"], s05, shf,
                         100 * r["base"]["mean"], 100 * b["mean"]))
    print(u"! — фильтр отсёк почти всё: «не торговать» не считается перевесом")

    print(u"\n\nСКОЛЬКО РАЗ ФИЛЬТР ВООБЩЕ ПОМОГ")
    print(u"   Сравнение по ДОХОДНОСТИ НА ОДИН ВХОД, а не по проценту в месяц.")
    print(u"   Процент в месяц у фильтра падает просто оттого, что сделок")
    print(u"   меньше; на убыточном правиле это выглядит как улучшение, хотя")
    print(u"   улучшения нет. Перевес есть тогда, когда КАЖДЫЙ оставленный")
    print(u"   вход в среднем лучше среднего входа без фильтра.")
    pairs = []
    for k, v in results.items():
        for tag in (u"p>=0.5", u"половина лучших"):
            if v[tag]["degenerate"]:
                continue
            pairs.append((k + "|" + tag, v[tag]["dmean"],
                          v[tag]["mo"] - v["base"]["mo"]))
    dm = np.array([p[1] for p in pairs])
    dmo = np.array([p[2] for p in pairs])
    print(u"   невырожденных пар: %d" % len(pairs))
    print(u"   доходность на вход выше, чем без фильтра: %d из %d"
          % (int((dm > 0).sum()), len(dm)))
    print(u"   медиана прибавки на вход: %+.4f%% (ожидание при отсутствии "
          u"перевеса — ноль)" % (100 * np.median(dm)))
    print(u"   среднее прибавки на вход:  %+.4f%%" % (100 * dm.mean()))
    print(u"   для справки, по проценту в месяц: лучше в %d из %d"
          % (int((dmo > 0).sum()), len(dmo)))

    print(u"\nТОЧНОСТЬ: ОБУЧЕНИЕ ПРОТИВ ВНЕОБУЧАЮЩЕЙ ЧАСТИ")
    at = np.array([v["acc_tr"] for v in results.values()])
    av = np.array([v["acc_te"] for v in results.values()])
    au = np.array([v["auc_pool"] for v in results.values()])
    print(u"   средняя точность на обучении: %.3f" % at.mean())
    print(u"   средняя точность вне обучения: %.3f" % av.mean())
    print(u"   средний AUC вне обучения: %.3f (0.5 = монетка)" % au.mean())
    print(u"   AUC выше 0.55 хотя бы раз: %d из %d"
          % (int((au > 0.55).sum()), len(au)))

    print(u"\nТЕРЦИЛИ ВЕРОЯТНОСТИ: упорядочивает ли модель входы по качеству")
    print(u"   (если да — средняя доходность растёт слева направо)")
    print(u"   %-11s %-4s %-8s %9s %9s %9s  %s"
          % (u"правило", u"тф", u"модель", u"низ", u"середина", u"верх",
             u"монотонно"))
    mono = 0
    for k, v in results.items():
        nm, tf, kind = k.split("|")
        m = [t["mean"] for t in v["terciles"]]
        ok = m[0] < m[1] < m[2]
        mono += int(ok)
        print(u"   %-11s %-4s %-8s %8.3f%% %8.3f%% %8.3f%%  %s"
              % (nm, tf, kind, 100 * m[0], 100 * m[1], 100 * m[2],
                 u"да" if ok else u"нет"))
    print(u"   монотонных: %d из %d (случайно ожидается %.1f)"
          % (mono, len(results), len(results) / 6.0))

    print(u"\nУСТОЙЧИВОСТЬ ВАЖНОСТИ ПРИЗНАКОВ МЕЖДУ ОКНАМИ")
    print(u"   (ранговая связь важностей соседних окон; около нуля значит,")
    print(u"    что в каждом окне модель цепляется за другое — то есть за шум)")
    for k, v in results.items():
        imps = v["importance"]
        cs = [spearman(imps[i], imps[i + 1]) for i in range(len(imps) - 1)]
        cs = [c for c in cs if np.isfinite(c)]
        top = np.argsort(np.mean(imps, axis=0))[::-1][:3]
        names = [ml_lib.FEATS[i] for i in top]
        print(u"   %-28s связь окон %+.2f   верх: %s"
              % (k, float(np.mean(cs)) if cs else float("nan"),
                 ", ".join(names)))

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    npz = {}
    slim = {}
    for k, v in results.items():
        pr = v.pop("_pred")
        for f, arr in pr.items():
            npz["%s@%s" % (k, f)] = arr
        slim[k] = v
    np.savez_compressed(os.path.join(OUT, "ml_pred.npz"), **npz)
    with open(os.path.join(OUT, "ml_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(slim, fh, ensure_ascii=False, default=float)
    print(u"\nсохранено -> out/ml_meta.json, out/ml_pred.npz")


if __name__ == "__main__":
    main()
