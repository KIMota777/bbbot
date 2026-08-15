# -*- coding: utf-8 -*-
u"""Сколько стоит зазор: та же модель без purge/embargo.

Проверка не ради результата, а ради доверия к остальным числам. Если убрать
зазор между обучением и проверкой, обучающие сделки, закрывшиеся уже внутри
проверочного куска, приносят туда знание о том же движении рынка. Модель
начинает выглядеть лучше — и ровно на эту величину любая работа без зазора
завышена. Печатается разница, чтобы было видно, что зазор здесь не украшение.
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

from sklearn.metrics import roc_auc_score  # noqa: E402


def run(d, kind, embargo_on):
    t0, t1 = rdata.SPLITS["trainval"]
    t_in, t_out = d["t_in"], d["t_out"]
    if embargo_on:
        folds = ml_lib.purged_folds(t_in, t_out, t0, t1, ml_meta.N_FOLDS)
    else:
        # БЕЗ зазора: обучаемся на всём, что ОТКРЫЛОСЬ раньше проверки, не
        # обращая внимания на то, когда оно закрылось. Именно так выглядит
        # обычное «разбиение по времени», и именно так протекает будущее.
        edges = np.linspace(t0, t1, ml_meta.N_FOLDS + 2)
        folds = []
        for k in range(1, ml_meta.N_FOLDS + 1):
            te0, te1 = edges[k], edges[k + 1]
            tr = np.flatnonzero(t_in < te0)
            te = np.flatnonzero((t_in >= te0) & (t_in < te1))
            if len(tr) < 250 or len(te) < 20:
                continue
            folds.append(dict(train=tr, test=te))
    X, y, ret = d["X"], d["y"], d["ret"]
    ps, ys, rs, keeps = [], [], [], []
    for f in folds:
        tr, te = f["train"], f["test"]
        m = ml_meta.make_model(kind)
        m.fit(X[tr], y[tr])
        p_tr = m.predict_proba(X[tr])[:, 1]
        p_te = m.predict_proba(X[te])[:, 1]
        ps.append(p_te)
        ys.append(y[te])
        rs.append(ret[te])
        keeps.append(p_te >= float(np.median(p_tr)))
    p, yy = np.concatenate(ps), np.concatenate(ys)
    rr, kk = np.concatenate(rs), np.concatenate(keeps)
    return dict(folds=len(folds), auc=float(roc_auc_score(yy, p)),
                dmean=float(rr[kk].mean() - rr.mean()))


def main():
    t0, t1 = rdata.SPLITS["trainval"]
    reg = ml_lib.registry()
    print(u"ЧТО ДАЁТ ОТСУТСТВИЕ ЗАЗОРА МЕЖДУ ОБУЧЕНИЕМ И ПРОВЕРКОЙ")
    print(u"Слева — как считалось везде выше: обучающая сделка обязана быть")
    print(u"ЗАКРЫТА до начала зазора. Справа — обычное разбиение по времени,")
    print(u"где достаточно, чтобы сделка была ОТКРЫТА раньше.\n")
    print(u"   %-22s %8s %9s | %8s %9s"
          % (u"конфигурация", u"AUC", u"прибавка", u"AUC", u"прибавка"))
    print(u"   %-22s %8s %9s | %8s %9s"
          % ("", u"с зазор", u"с зазором", u"без", u"без зазора"))
    da, dd = [], []
    for name in ml_meta.RULES:
        pc = ml_lib.center_combo(reg[name])
        for tf in ("60", "240"):
            d = ml_lib.dataset(name, pc, tf, t0, t1, causal_first=False)
            if d is None:
                continue
            for kind in (u"логрег", u"бустинг"):
                a = run(d, kind, True)
                b = run(d, kind, False)
                da.append(b["auc"] - a["auc"])
                dd.append(b["dmean"] - a["dmean"])
                print(u"   %-22s %8.3f %+8.4f%% | %8.3f %+8.4f%%"
                      % ("%s|%s|%s" % (name, tf, kind), a["auc"],
                         100 * a["dmean"], b["auc"], 100 * b["dmean"]))
    print(u"\n   без зазора AUC выше в среднем на %+.3f" % np.mean(da))
    print(u"   без зазора прибавка выше в среднем на %+.4f%% на вход"
          % (100 * np.mean(dd)))
    print(u"\n   ЧИТАЕТСЯ ЭТО ТАК. Обычно снятие зазора завышает результат:")
    print(u"   перекрывающиеся сделки приносят в проверку знание о том же")
    print(u"   движении рынка. Здесь завышения нет — знак вообще случайный.")
    print(u"   Причина простая и она же и есть ответ всей работы: чтобы")
    print(u"   протечь, сначала надо чему-то научиться. Учиться нечему, и")
    print(u"   разница между строгим и нестрогим разбиением оказывается той")
    print(u"   же величины, что и весь искомый перевес. Когда поправка на")
    print(u"   методику сравнима с самим эффектом, эффекта нет.")


if __name__ == "__main__":
    main()
