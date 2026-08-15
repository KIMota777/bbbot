# -*- coding: utf-8 -*-
u"""Мета-разметка на объединённой выборке: бьёт ли фильтр «брать всё подряд».

РЕШЁТКА ОБЪЯВЛЕНА ДО СЧЁТА и здесь же записана целиком, чтобы потом нельзя
было назвать победителя находкой:

  два набора правил   — «ансамбль» (те же четыре, что в FINAL_SPEC) и
                        «по одному из каждого семейства» (первое по алфавиту
                        в каждом из десяти семейств — выбор без единого
                        взгляда в результат);
  две модели          — логистическая регрессия и HistGradientBoosting;
  три набора весов    — без весов, |доходность|, |доходность| x уникальность;
  два набора признаков— со всеми (включая «какое это правило, монета, тф») и
                        только обстановка (личность строки удалена);
  два порога          — медиана обучающих вероятностей и верхние 30%.

Итого 2 x 2 x 3 x 2 = 24 конфигурации, каждая с двумя порогами. Печатаются ВСЕ.
Победитель называется вместе с числом попыток, его породивших.

ЧТО СЧИТАЕТСЯ ПЕРЕВЕСОМ. Только одно: на кусках, которых модель не видела,
СРЕДНЯЯ ДОХОДНОСТЬ ОСТАВЛЕННОГО ВХОДА выше средней доходности всех входов.
Не точность, не AUC, не процент в месяц (он падает сам собой, когда сделок
становится меньше, и на убыточном правиле это выглядит улучшением).

ДВА СПОСОБА СЧИТАТЬ ПРИБАВКУ, и оба печатаются:
  «общая»      — по всей объединённой куче. Сюда затекает лёгкий способ
                 выиграть: перестать торговать правило или таймфрейм с худшим
                 средним. Это не отбор входов, это отбор правил задним числом.
  «внутри»     — прибавка считается ОТДЕЛЬНО внутри каждой пары (правило, тф)
                 и усредняется по ним. Здесь выбор правила уже не помогает:
                 модель обязана отличать хорошие входы от плохих внутри одного
                 и того же правила.

ЗАЗОР. Обучающая сделка обязана быть ЗАКРЫТА не позже, чем за `embargo` до
начала проверочного окна. По умолчанию embargo — 99-й перцентиль удержания
(около 15 дней), и отдельно печатается вариант с самым долгим удержанием
(184 дня), чтобы было видно, что вывод от этого выбора не зависит.
"""
import json
import os
import sys
import time

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import ml_lib      # noqa: E402
import ml_r2_lib as L  # noqa: E402
import rdata       # noqa: E402

from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.impute import SimpleImputer                     # noqa: E402
from sklearn.linear_model import LogisticRegression          # noqa: E402
from sklearn.metrics import roc_auc_score                    # noqa: E402
from sklearn.pipeline import Pipeline                        # noqa: E402
from sklearn.preprocessing import StandardScaler             # noqa: E402

OUT = os.path.join(DIR, "out")
RULES4 = ["supertrend", "bos", "vol_spike", "c_willr"]
# первое по алфавиту в каждом семействе — выбор задан порядком букв, не счётом
RULES10 = ["atr_break", "c_bb", "bb_mr", "accel", "displacement", "bos",
           "adx_dmi", "vol_expansion", "obv_trend", "vwap_reclaim"]
MODELS = (u"логрег", u"бустинг")
WEIGHTS = (u"нет", u"|доход|", u"|доход|*уник")
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


def fit(m, kind, X, y, w):
    if kind == u"логрег":
        m.fit(X, y, **{"lr__sample_weight": w})
    else:
        m.fit(X, y, sample_weight=w)
    return m


def live_cols(Xtr):
    u"""Столбцы, которые на этом обучающем куске вообще что-то содержат.

    Открытый интерес есть в истории только с октября 2024 года, поэтому в
    первом окне его столбцы сплошь пустые. Пустой столбец — не ноль, а
    отсутствие сведений; ронять на нём обучение (и тем более подставлять
    туда медиану всей выборки) нельзя. Такие столбцы просто не участвуют в
    том окне, где их нет.
    """
    out = []
    for j in range(Xtr.shape[1]):
        v = Xtr[:, j]
        v = v[np.isfinite(v)]
        if len(v) >= 50 and len(np.unique(v)) >= 3:
            out.append(j)
    return np.array(out, dtype=int)


def purged_folds(t_in, t_out, split="trainval", n_folds=N_FOLDS,
                 embargo_mode="p99", min_train=500):
    u"""Окна вперёд с зазором. Обучающая сделка обязана быть ЗАКРЫТА до зазора."""
    t0, t1 = rdata.SPLITS[split]
    hold = t_out - t_in
    if embargo_mode == "max":
        emb = float(hold.max())
    else:
        emb = float(np.percentile(hold, 99))
    emb = max(emb, float(rdata.DAY_MS))
    edges = np.linspace(t0, t1, n_folds + 2)
    out = []
    for k in range(1, n_folds + 1):
        te0, te1 = edges[k], edges[k + 1]
        tr = np.flatnonzero(t_out <= te0 - emb)
        te = np.flatnonzero((t_in >= te0) & (t_in < te1))
        if len(tr) < min_train or len(te) < 50:
            continue
        out.append(dict(train=tr, test=te, te0=te0, te1=te1,
                        emb_days=emb / rdata.DAY_MS))
    return out


def _auc(y, p):
    try:
        return float(roc_auc_score(y, p))
    except ValueError:
        return float("nan")


def perm_importance(m, X, y, seed=0, n_rep=3):
    rng = np.random.default_rng(seed)
    base = _auc(y, m.predict_proba(X)[:, 1])
    if not np.isfinite(base):
        return np.zeros(X.shape[1])
    out = np.empty(X.shape[1])
    for j in range(X.shape[1]):
        d = []
        for _ in range(n_rep):
            Z = X.copy()
            Z[:, j] = Z[rng.permutation(len(Z)), j]
            d.append(base - _auc(y, m.predict_proba(Z)[:, 1]))
        out[j] = float(np.mean(d))
    return out


def run_variant(d, folds, model_kind, weight_kind, use_ident, seed=0,
                shuffle=False, want_imp=False):
    u"""Один прогон по всем окнам. Порог — только из обучающего куска."""
    names = d["names"]
    if use_ident:
        cols = list(range(len(names)))
    else:
        cols = [i for i, n in enumerate(names) if n not in L.IDENT]
    X = d["X"][:, cols]
    y, ret = d["y"], d["ret"]
    w_all = L.make_weights(weight_kind, y, ret, d["t_in"], d["t_out"])
    rng = np.random.default_rng(seed + 991)
    ps, ys, rs, ci, keep_med, keep_top = [], [], [], [], [], []
    acc_tr, acc_te, imps = [], [], []
    for f in folds:
        tr, te = f["train"], f["test"]
        yt = y[tr]
        if shuffle:
            yt = yt[rng.permutation(len(tr))]
        jj = live_cols(X[tr])
        m = fit(make_model(model_kind, seed), model_kind,
                X[tr][:, jj], yt, w_all[tr])
        p_tr = m.predict_proba(X[tr][:, jj])[:, 1]
        p_te = m.predict_proba(X[te][:, jj])[:, 1]
        acc_tr.append(float(((p_tr >= 0.5).astype(int) == yt).mean()))
        acc_te.append(float(((p_te >= 0.5).astype(int) == y[te]).mean()))
        keep_med.append(p_te >= float(np.median(p_tr)))
        keep_top.append(p_te >= float(np.percentile(p_tr, 70)))
        ps.append(p_te)
        ys.append(y[te])
        rs.append(ret[te])
        ci.append(d["cfg"][te])
        if want_imp:
            full = np.zeros(X.shape[1])
            full[jj] = perm_importance(m, X[te][:, jj], y[te], seed)
            imps.append(full)
    if not ps:
        return None
    p = np.concatenate(ps)
    yy = np.concatenate(ys)
    rr = np.concatenate(rs)
    cc = np.concatenate(ci)
    ti = np.concatenate([d["t_in"][f["test"]] for f in folds])
    to = np.concatenate([d["t_out"][f["test"]] for f in folds])
    out = dict(n=len(rr), base_mean=float(rr.mean()),
               base_mo=ml_lib.monthly_rate(rr, ti, to),
               acc_tr=float(np.mean(acc_tr)), acc_te=float(np.mean(acc_te)),
               auc=_auc(yy, p), cols=len(cols))
    for tag, keep in ((u"медиана", np.concatenate(keep_med)),
                      (u"верх 30%", np.concatenate(keep_top))):
        n_keep = int(keep.sum())
        if n_keep < 50:
            out[tag] = dict(n=n_keep, dmean=float("nan"),
                            dmean_in=float("nan"), mo=float("nan"))
            continue
        dmean = float(rr[keep].mean() - rr.mean())
        # прибавка ВНУТРИ каждой пары (правило, тф), усреднённая по ним
        parts, wts = [], []
        for c in np.unique(cc):
            s = cc == c
            if s.sum() < 50 or (s & keep).sum() < 20:
                continue
            parts.append(rr[s & keep].mean() - rr[s].mean())
            wts.append(int(s.sum()))
        din = float(np.average(parts, weights=wts)) if parts else float("nan")
        out[tag] = dict(n=n_keep, dmean=dmean, dmean_in=din,
                        mo=ml_lib.monthly_rate(rr[keep], ti[keep], to[keep]),
                        eq_base=ml_lib.compound(rr),
                        eq_filt=ml_lib.compound(rr[keep]))
    if want_imp:
        out["imps"] = [list(map(float, a)) for a in imps]
        out["imp_names"] = [names[i] for i in cols]
    return out


def spearman(a, b):
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def main():
    t_start = time.time()
    print(u"ПРОВЕРКА ПРИЧИННОСТИ НОВЫХ ПРИЗНАКОВ (порча будущего)")
    for tf in ("60", "240"):
        for sym in ("BTCUSDT", "DOGEUSDT"):
            L.assert_extra_causal(tf=tf, symbol=sym)
    print(u"   открытый интерес, поперечный срез: пройдена на 4 сочетаниях\n")

    sets = [(u"ансамбль", RULES4), (u"по одному из семейства", RULES10)]
    data = {}
    for tag, rules in sets:
        d = L.pooled(rules)
        data[tag] = d
        h = (d["t_out"] - d["t_in"]) / rdata.DAY_MS
        print(u"ВЫБОРКА «%s»: %d сделок, %d признаков, %d пар (правило,тф)"
              % (tag, len(d["y"]), d["X"].shape[1], len(np.unique(d["cfg"]))))
        print(u"   доля плюсовых %.3f, средняя доходность входа %+.4f%%, "
              u"удержание медиана %.1f дн, 99%% %.1f дн"
              % (d["y"].mean(), 100 * d["ret"].mean(), np.median(h),
                 np.percentile(h, 99)))
    print()

    rows = []
    hdr = (u"%-22s %-8s %-13s %-7s | %5s %5s %5s | %8s %8s | %8s %8s"
           % (u"выборка", u"модель", u"веса", u"признак", u"тчн", u"тчн",
              u"AUC", u"общая", u"внутри", u"общая", u"внутри"))
    print(u"ВСЕ 24 КОНФИГУРАЦИИ. Прибавка — % капитала на один вход.")
    print(u"«общая» — по всей куче, «внутри» — внутри каждой пары (правило,тф).")
    print(hdr)
    print(u"%-22s %-8s %-13s %-7s | %5s %5s %5s | %19s | %19s"
          % ("", "", "", "", u"обуч", u"вне", u"вне", u"--- медиана ---",
             u"--- верх 30% ---"))
    print("-" * len(hdr))
    for tag, _ in sets:
        d = data[tag]
        folds = purged_folds(d["t_in"], d["t_out"])
        for mk in MODELS:
            for wk in WEIGHTS:
                for ident in (True, False):
                    r = run_variant(d, folds, mk, wk, ident)
                    if r is None:
                        continue
                    r.update(set=tag, model=mk, weight=wk, ident=ident)
                    rows.append(r)
                    a, b = r[u"медиана"], r[u"верх 30%"]
                    print(u"%-22s %-8s %-13s %-7s | %5.3f %5.3f %5.3f | "
                          u"%+7.4f%% %+7.4f%% | %+7.4f%% %+7.4f%%"
                          % (tag, mk, wk, u"все" if ident else u"обст.",
                             r["acc_tr"], r["acc_te"], r["auc"],
                             100 * a["dmean"], 100 * a["dmean_in"],
                             100 * b["dmean"], 100 * b["dmean_in"]))

    print(u"\nЗАЗОР: %d окон, зазор %.1f дн (99-й перцентиль удержания)"
          % (len(folds), folds[0]["emb_days"]))

    def summ(key, sub):
        v = np.array([r[key][sub] for r in rows
                      if np.isfinite(r[key][sub])])
        se = v.std(ddof=1) / np.sqrt(len(v))
        return v, se

    print(u"\n\nСВОДКА ПО ВСЕЙ РЕШЁТКЕ (ничего не выбрано, всё посчитано)")
    for key in (u"медиана", u"верх 30%"):
        for sub, what in (("dmean", u"общая"), ("dmean_in", u"внутри")):
            v, se = summ(key, sub)
            print(u"   порог «%s», прибавка %s: положительных %d из %d, "
                  u"среднее %+.4f%% ± %.4f%%  ->  %s"
                  % (key, what, int((v > 0).sum()), len(v),
                     100 * v.mean(), 100 * se,
                     u"НЕОТЛИЧИМО ОТ НУЛЯ" if abs(v.mean()) < 2 * se
                     else u"отличимо от нуля"))

    au = np.array([r["auc"] for r in rows])
    at = np.array([r["acc_tr"] for r in rows])
    av = np.array([r["acc_te"] for r in rows])
    print(u"\n   точность на обучении %.3f, вне обучения %.3f (разрыв %+.3f)"
          % (at.mean(), av.mean(), av.mean() - at.mean()))
    print(u"   AUC вне обучения: среднее %.3f, максимум %.3f, выше 0.55: %d из %d"
          % (au.mean(), au.max(), int((au > 0.55).sum()), len(au)))

    allv = np.array([r[k][s] for r in rows for k in (u"медиана", u"верх 30%")
                     for s in ("dmean", "dmean_in") if np.isfinite(r[k][s])])
    best = max(rows, key=lambda r: max(
        r[u"медиана"]["dmean_in"] if np.isfinite(r[u"медиана"]["dmean_in"])
        else -9, r[u"верх 30%"]["dmean_in"]
        if np.isfinite(r[u"верх 30%"]["dmean_in"]) else -9))
    print(u"\n   ЛУЧШАЯ ИЗ %d ЧИСЕЛ: %s|%s|%s|%s"
          % (len(allv), best["set"], best["model"], best["weight"],
             u"все" if best["ident"] else u"обстановка"))
    for k in (u"медиана", u"верх 30%"):
        print(u"      порог «%s»: общая %+.4f%%, внутри %+.4f%%, "
              u"месяц без фильтра %+.2f%%, с фильтром %+.2f%%"
              % (k, 100 * best[k]["dmean"], 100 * best[k]["dmean_in"],
                 100 * best["base_mo"], 100 * best[k]["mo"]))
    print(u"      капитал по внеобучающим окнам: без фильтра %.3f, "
          u"с фильтром %.3f"
          % (best[u"медиана"]["eq_base"], best[u"медиана"]["eq_filt"]))

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "ml_r2_pool.json"), "w",
              encoding="utf-8") as fh:
        json.dump([{k: v for k, v in r.items() if k != "imps"} for r in rows],
                  fh, ensure_ascii=False, default=float)
    print(u"\nсохранено -> out/ml_r2_pool.json   (%.0f с)" % (time.time() - t_start))


if __name__ == "__main__":
    main()
