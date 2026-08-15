# -*- coding: utf-8 -*-
u"""Проверка единственного намёка на пользу: supertrend.

В контрольных опытах вся выборка вела себя как шум, кроме одного места:
фильтр над supertrend дважды оказался лучше перемешанных меток. Отмахнуться
от этого нельзя — это как раз тот случай, когда автор мог что-то упустить.
Поэтому здесь намёк проверяется на излом, четырьмя независимыми способами:

  А. ОДНА ЛИ ЭТО ТОЧКА СЕТКИ. Мета-модель переобучается на восьми случайных
     сочетаниях параметров правила (зерно зафиксировано, ни одно не выбрано по
     результату). Настоящее свойство должно жить на большинстве, а не на
     срединном сочетании.

  Б. ОДНО ЛИ ЭТО ОКНО. Прибавка разносится по окнам проверки. Свойство,
     живущее в одном окне из пяти, — это одно окно, а не свойство.

  В. НЕ СТАВКА ЛИ ЭТО НА СТОРОНУ. Обучение пришлось на растущий рынок. Модель,
     выучившая «покупать хорошо, продавать плохо», покажет ровно такую
     прибавку — и развернётся в минус на падающем рынке. Считается доля
     длинных среди оставленных против доли среди всех.

  Г. ЧТО ОСТАНЕТСЯ БЕЗ НАПРАВЛЕНИЯ. Модель переобучается на признаках, из
     которых удалено всё направленное: сторона сделки, знак расстояний до
     средних, направление BTC. Останется волатильность, час, объём, фандинг.
     Если прибавка исчезает — она была ставкой на сторону, а не отбором входов.
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

DIRECTIONAL = ["side", "s_ema50_d", "s_ema200_d", "s_ret24", "s_btc_ret24",
               "s_rsi", "s_di_diff", "btc_ret24", "btc_above", "ema50_d",
               "ema200_d", "ret1", "ret6", "ret24", "di_diff"]


def fold_detail(d, kind, seed=0):
    u"""Прибавка на вход по каждому окну отдельно, плюс доля длинных."""
    t0, t1 = rdata.SPLITS["trainval"]
    folds = ml_lib.purged_folds(d["t_in"], d["t_out"], t0, t1, ml_meta.N_FOLDS)
    X, y, ret = d["X"], d["y"], d["ret"]
    j_side = ml_lib.FEATS.index("side")
    out = []
    for f in folds:
        tr, te = f["train"], f["test"]
        m = ml_meta.make_model(kind, seed)
        m.fit(X[tr], y[tr])
        p_tr = m.predict_proba(X[tr])[:, 1]
        p_te = m.predict_proba(X[te])[:, 1]
        keep = p_te >= float(np.median(p_tr))
        if keep.sum() < 5:
            out.append(None)
            continue
        out.append(dict(
            n=int(len(te)), n_keep=int(keep.sum()),
            base=float(ret[te].mean()), filt=float(ret[te][keep].mean()),
            long_all=float((X[te][:, j_side] > 0).mean()),
            long_keep=float((X[te][keep][:, j_side] > 0).mean())))
    return out


def drop_directional(d):
    keep = [i for i, n in enumerate(ml_lib.FEATS) if n not in DIRECTIONAL]
    e = dict(d)
    e["X"] = d["X"][:, keep]
    return e, [ml_lib.FEATS[i] for i in keep]


def main():
    t0, t1 = rdata.SPLITS["trainval"]
    reg = ml_lib.registry()
    st = reg["supertrend"]

    print(u"А. ЖИВЁТ ЛИ ПРИБАВКА НА ДРУГИХ СОЧЕТАНИЯХ ПАРАМЕТРОВ ПРАВИЛА")
    print(u"   Восемь случайных сочетаний сетки, зерно 17. Ни одно не выбрано")
    print(u"   по результату. Прибавка — доходность на вход с фильтром минус")
    print(u"   без фильтра, в процентах капитала на сделку.\n")
    combos = [ml_lib.center_combo(st)] + ml_lib.random_combos(st, 8)
    tags = [u"срединное"] + [u"случайное %d" % (i + 1) for i in range(8)]
    print(u"   %-14s %-4s %6s | %9s %9s"
          % (u"сочетание", u"тф", u"входов", u"логрег", u"бустинг"))
    tab = {u"логрег": [], u"бустинг": []}
    for tf in ("60", "240"):
        for tag, p in zip(tags, combos):
            d = ml_lib.dataset("supertrend", p, tf, t0, t1, causal_first=False)
            if d is None or len(d["y"]) < 200:
                print(u"   %-14s %-4s %6s | мало сделок"
                      % (tag, tf, 0 if d is None else len(d["y"])))
                continue
            vals = []
            for kind in (u"логрег", u"бустинг"):
                r = ml_meta.run_config(d, kind)
                v = r[u"половина лучших"]["dmean"] if r else float("nan")
                vals.append(v)
                if np.isfinite(v):
                    tab[kind].append(v)
            print(u"   %-14s %-4s %6d | %+8.4f%% %+8.4f%%"
                  % (tag, tf, len(d["y"]), 100 * vals[0], 100 * vals[1]))
    for kind in (u"логрег", u"бустинг"):
        a = np.array(tab[kind])
        se = a.std(ddof=1) / np.sqrt(len(a))
        print(u"   %s: положительных %d из %d, среднее %+.4f%% ± %.4f%%"
              % (kind, int((a > 0).sum()), len(a), 100 * a.mean(), 100 * se))

    print(u"\n\nБ и В. ПО ОКНАМ ОТДЕЛЬНО И ДОЛЯ ДЛИННЫХ")
    print(u"   Если прибавка сидит в одном окне — это окно, а не свойство.")
    print(u"   Если доля длинных среди оставленных заметно выше, чем среди")
    print(u"   всех, — модель ставит на сторону, а обучение пришлось на рост.\n")
    for tf in ("60", "240"):
        for kind in (u"логрег", u"бустинг"):
            d = ml_lib.dataset("supertrend", ml_lib.center_combo(st), tf,
                               t0, t1, causal_first=False)
            det = fold_detail(d, kind)
            print(u"   supertrend|%s|%s" % (tf, kind))
            print(u"      %-5s %7s %9s %9s %9s | %8s %8s"
                  % (u"окно", u"входов", u"без, %", u"с, %", u"разн, %",
                     u"длин все", u"длин ост"))
            for i, f in enumerate(det):
                if f is None:
                    print(u"      %-5d   фильтр пуст" % (i + 1))
                    continue
                print(u"      %-5d %7d %+8.3f%% %+8.3f%% %+8.3f%% | %7.0f%% "
                      u"%7.0f%%"
                      % (i + 1, f["n"], 100 * f["base"], 100 * f["filt"],
                         100 * (f["filt"] - f["base"]),
                         100 * f["long_all"], 100 * f["long_keep"]))

    print(u"\n\nГ. ТО ЖЕ БЕЗ ЕДИНОГО НАПРАВЛЕННОГО ПРИЗНАКА")
    print(u"   Убраны: сторона сделки, знаковые расстояния до средних,")
    print(u"   импульс, направление BTC. Остались волатильность, ADX, час,")
    print(u"   день, объём, фандинг, положение в канале.\n")
    print(u"   %-22s %11s %11s %9s"
          % (u"конфигурация", u"со всеми", u"без напр.", u"AUC без"))
    for tf in ("60", "240"):
        d = ml_lib.dataset("supertrend", ml_lib.center_combo(st), tf, t0, t1,
                           causal_first=False)
        e, names = drop_directional(d)
        for kind in (u"логрег", u"бустинг"):
            a = ml_meta.run_config(d, kind)
            b = ml_meta.run_config(e, kind)
            print(u"   %-22s %+10.4f%% %+10.4f%% %8.3f"
                  % ("supertrend|%s|%s" % (tf, kind),
                     100 * a[u"половина лучших"]["dmean"],
                     100 * b[u"половина лучших"]["dmean"], b["auc_pool"]))
    print(u"   осталось признаков: %d из %d (%s)"
          % (len(names), len(ml_lib.FEATS), ", ".join(names)))


if __name__ == "__main__":
    main()
