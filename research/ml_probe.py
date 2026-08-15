# -*- coding: utf-8 -*-
u"""Разведка: причинность признаков и сколько вообще есть сделок для обучения.

Смысл шага. Мета-модели нужна выборка. Если у правила на обучающем куске
двести входов, никакая модель ничего не выучит, и отрицательный ответ будет
означать лишь «данных мало», а не «перевеса нет». Поэтому счёт сделок
печатается ДО всякого обучения, и от него зависит, о чём вообще можно
говорить.
"""
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import numpy as np      # noqa: E402

import ml_lib           # noqa: E402
import rdata            # noqa: E402

RULES = ["supertrend", "bos", "vol_spike", "c_willr"]


def main():
    print(u"1. ПРИЗНАКИ НЕ ЗНАЮТ БУДУЩЕГО")
    for tf in ("60", "240"):
        ml_lib.assert_features_causal(tf=tf)
        print(u"   таймфрейм %sм: проверка порчей будущего пройдена" % tf)

    t0, t1 = rdata.SPLITS["trainval"]
    print(u"\n2. СКОЛЬКО ВХОДОВ ДАЁТ ПРАВИЛО НА ОБУЧЕНИИ+ПРОВЕРКЕ")
    print(u"   (срединное сочетание сетки, все пять монет)")
    print(u"   %-12s %-4s %7s %7s %8s %9s"
          % (u"правило", u"тф", u"входов", u"плюс,%", u"средн,%", u"держ,ч"))
    reg = ml_lib.registry()
    for name in RULES:
        st = reg[name]
        p = ml_lib.center_combo(st)
        for tf in ("60", "240"):
            d = ml_lib.dataset(name, p, tf, t0, t1)
            if d is None:
                print(u"   %-12s %-4s   нет сделок" % (name, tf))
                continue
            hold = (d["t_out"] - d["t_in"]) / 3600000.0
            print(u"   %-12s %-4s %7d %7.1f %8.3f %9.1f"
                  % (name, tf, len(d["y"]), 100 * d["y"].mean(),
                     100 * d["ret"].mean(), float(np.median(hold))))
        print(u"      сочетание: %s" % p)


if __name__ == "__main__":
    main()
