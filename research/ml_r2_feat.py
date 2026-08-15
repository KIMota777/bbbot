# -*- coding: utf-8 -*-
u"""За что модель цепляется и как это выглядит на кривой капитала.

ДВА ВОПРОСА.

  1. УСТОЙЧИВА ЛИ ВАЖНОСТЬ ПРИЗНАКОВ МЕЖДУ ОКНАМИ. Важность считается
     перемешиванием признака на ПРОВЕРОЧНОМ куске: насколько падает AUC, если
     столбец испортить. Если в каждом окне наверху оказываются разные
     признаки, модель ловит не свойство рынка, а особенность куска. Мерило —
     ранговая связь важностей между окнами: у настоящего свойства она высокая
     и положительная.

  2. КАК ЭТО ВЫГЛЯДИТ НА ДЕНЬГАХ. Накопленный капитал по внеобучающим окнам
     подряд, с фильтром и без. Это то самое сравнение, ради которого всё и
     считалось, и его нельзя подменить ни точностью, ни AUC.
"""
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import ml_lib          # noqa: E402
import ml_r2_lib as L  # noqa: E402
import ml_r2_pool as P  # noqa: E402

CASES = [
    (u"ансамбль", P.RULES4, u"бустинг", u"нет", False),
    (u"ансамбль", P.RULES4, u"логрег", u"|доход|", False),
    (u"по одному из семейства", P.RULES10, u"бустинг", u"нет", True),
]


def main():
    print(u"1. УСТОЙЧИВОСТЬ ВАЖНОСТИ ПРИЗНАКОВ МЕЖДУ ОКНАМИ\n")
    cache = {}
    for tag, rules, mk, wk, ident in CASES:
        key = tuple(rules)
        if key not in cache:
            d = L.pooled(rules)
            cache[key] = (d, P.purged_folds(d["t_in"], d["t_out"]))
        d, folds = cache[key]
        r = P.run_variant(d, folds, mk, wk, ident, want_imp=True)
        imps = np.array(r["imps"])
        names = r["imp_names"]
        print(u"   %s | %s | веса %s | признаки %s"
              % (tag, mk, wk, u"все" if ident else u"обстановка"))
        cs = [P.spearman(imps[i], imps[i + 1]) for i in range(len(imps) - 1)]
        cs = [c for c in cs if np.isfinite(c)]
        pair = [P.spearman(imps[i], imps[j])
                for i in range(len(imps)) for j in range(i + 1, len(imps))]
        pair = [c for c in pair if np.isfinite(c)]
        print(u"      связь важностей соседних окон: %s   (среднее %+.2f)"
              % (", ".join("%+.2f" % c for c in cs), float(np.mean(cs))))
        print(u"      среднее по всем парам окон: %+.2f" % float(np.mean(pair)))
        tops = []
        for i, row in enumerate(imps):
            t = [names[j] for j in np.argsort(row)[::-1][:4]]
            tops.append(t)
            print(u"      окно %d, верх: %s" % (i + 1, ", ".join(t)))
        common = set(tops[0])
        for t in tops[1:]:
            common &= set(t)
        print(u"      попали в верх во ВСЕХ окнах: %s"
              % (", ".join(sorted(common)) if common else u"ни одного"))
        print(u"      средняя важность лучшего признака: %+.4f AUC "
              u"(0 = признак не нужен)\n" % float(np.max(imps.mean(axis=0))))

    print(u"\n2. НАКОПЛЕННЫЙ КАПИТАЛ ПО ВНЕОБУЧАЮЩИМ ОКНАМ, С ФИЛЬТРОМ И БЕЗ")
    print(u"   Риск 1%% на сделку, старт 1.000. «Без» — все входы правил")
    print(u"   подряд, «с фильтром» — те же входы, но отвергнутые моделью")
    print(u"   пропущены. Порог взят из обучающего куска.\n")
    print(u"   %-34s %-9s %8s %8s %8s"
          % (u"конфигурация", u"порог", u"без", u"с фильтр", u"разница"))
    for tag, rules, mk, wk, ident in CASES:
        d, folds = cache[tuple(rules)]
        r = P.run_variant(d, folds, mk, wk, ident)
        nm = u"%s|%s|%s" % (tag[:14], mk, wk)
        for ptag in (u"медиана", u"верх 30%"):
            a = r[ptag]
            print(u"   %-34s %-9s %8.3f %8.3f %+8.3f"
                  % (nm, ptag, a["eq_base"], a["eq_filt"],
                     a["eq_filt"] - a["eq_base"]))
        print(u"   %-34s %-9s %8s %8s   (сделок %d -> %d)"
              % ("", u"", "", "", r["n"], r[u"верх 30%"]["n"]))

    print(u"\n   ЧИТАЕТСЯ ЭТО ТАК. Объединённая куча правил на срединных")
    print(u"   сочетаниях параметров глубоко убыточна сама по себе (капитал")
    print(u"   уходит к нулю). Фильтр теряет меньше — но теряет. Перевес")
    print(u"   означал бы кривую выше единицы, а не менее крутое падение:")
    print(u"   «торговать вдвое меньше» — это не умение, а половина ставки.")

    print(u"\n\n3. ТО ЖЕ НА 4-ЧАСОВОМ СРЕЗЕ, КОТОРЫЙ И ТОРГУЕТСЯ")
    print(u"   (FINAL_SPEC торгует только 4ч; на нём правила прибыльны на")
    print(u"    обучении, и вопрос «улучшает ли фильтр» задаётся честно)\n")
    d, folds = cache[tuple(P.RULES4)]
    m4 = np.array([c.endswith("|240") for c in d["cfg"]])
    d4 = {k: (v[m4] if isinstance(v, np.ndarray) else v)
          for k, v in d.items()}
    d4["names"] = d["names"]
    f4 = P.purged_folds(d4["t_in"], d4["t_out"], min_train=300)
    print(u"   %-24s %8s %9s %9s %9s"
          % (u"модель|веса|порог", u"входов", u"без,%мес", u"с,%мес",
             u"прибавка"))
    for mk in (u"логрег", u"бустинг"):
        for wk in (u"нет", u"|доход|"):
            r = P.run_variant(d4, f4, mk, wk, False)
            if r is None:
                continue
            for ptag in (u"медиана", u"верх 30%"):
                a = r[ptag]
                print(u"   %-24s %8d %+8.2f%% %+8.2f%% %+8.4f%%"
                      % ("%s|%s|%s" % (mk, wk, ptag), a["n"],
                         100 * r["base_mo"], 100 * a["mo"],
                         100 * a["dmean_in"]))


if __name__ == "__main__":
    main()
