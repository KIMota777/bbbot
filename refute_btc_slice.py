# -*- coding: utf-8 -*-
"""Что осталось проверить после трёх линз: не держится ли результат
на самом РАЗРЕЗЕ истории и на горстке сделок.

Три вопроса, каждый из которых может обрушить находку сам по себе.

1. ГРАНИЦА ХОЛДАУТА. Доля 0.72 взята у сигналов и к этому конфигу отношения
   не имеет. Настоящее свойство рынка не должно знать, где мы провели черту.
   Двигаю границу от 0.50 до 0.88 и смотрю, что происходит с итогом.

2. ВНУТРИ ХОЛДАУТА. Разбиваю его на равные куски и на месяцы. Если весь
   плюс сделан за один-два месяца, а остальное время бот стоит в нуле, это
   не доходность, а одно удачное событие.

3. СКОЛЬКО СДЕЛОК ДЕЛАЮТ РЕЗУЛЬТАТ. 31 сделка — это очень мало. Считаю,
   что останется, если выкинуть лучшие 1, 2, 3 сделки, и какова доля самой
   крупной в итоге. Заодно — простой перестановочный тест: доля бутстрэпов
   (тяну 31 сделку с возвратом), где итог остаётся положительным.
"""
import sys

import numpy as np

import bots_honest as bh
import evolution2 as e2
import evolution8 as e8
import refute_btc_common as rc


def run_segment(d, a, b):
    """Прогон на срезе свечей [a, b) со своим aux и своим prep."""
    cn = d["candles"][a:b]
    aux = {k: bh.slice_aux(v, a, b) for k, v in d["aux"].items()}
    evs = []
    r = bh.run_at(cn, e2.prep(cn), d["g"], e8.make_filter8(d["g"], aux),
                  d["lev"], events=evs)
    months = (cn[-1][0] - cn[0][0]) / (30 * 86400000)
    return rc.nums(r, evs, months), months


def main():
    d = rc.build()
    n = d["n"]

    print("1. ГДЕ ПРОВЕСТИ ЧЕРТУ. Итог BTC normal x5 на «холдауте», "
          "если долю обучения менять")
    print("%-10s %8s %8s %6s %7s %9s %10s %8s"
          % ("доля", "бар", "мес", "сдел", "ВР%", "холдаут%", "б/копеек%",
             "просад%"))
    for frac in [0.50, 0.55, 0.60, 0.65, 0.70, 0.72, 0.75, 0.80, 0.85, 0.88]:
        h = int(n * frac)
        m, mo = run_segment(d, h, n)
        mark = "  <- рабочая" if abs(frac - 0.72) < 1e-9 else ""
        print("%-10.2f %8d %8.1f %6d %6.1f %+8.1f%% %+9.1f%% %7.1f%%%s"
              % (frac, h, mo, m["trades"], m["wr"], m["comp"], m["comp_nt"],
                 m["dd"], mark))
        sys.stdout.flush()

    print()
    print("   Для сравнения — обучающая часть (первые 72%), которую подбор "
          "видел заведомо:")
    m, mo = run_segment(d, 0, d["hold_i"])
    print("   обучение %.1f мес: сделок %d, ВР %.1f%%, итог %+.1f%%, "
          "просадка %.1f%%" % (mo, m["trades"], m["wr"], m["comp"], m["dd"]))

    print()
    print("2. ЧТО ВНУТРИ ХОЛДАУТА. Четыре равных куска по времени:")
    h = d["hold_i"]
    edges = [h + round((n - h) * k / 4) for k in range(5)]
    for k in range(4):
        m, mo = run_segment(d, edges[k], edges[k + 1])
        print("   кусок %d (%.1f мес): сделок %2d, итог %+7.1f%%, "
              "просадка %5.1f%%" % (k + 1, mo, m["trades"], m["comp"],
                                    m["dd"]))
        sys.stdout.flush()

    print()
    print("3. НА СКОЛЬКИХ СДЕЛКАХ ВСЁ ДЕРЖИТСЯ.")
    r0, e0 = rc.run_hold(d)
    m0 = rc.nums(r0, e0, d["hold"]["months"])
    pnls = np.array(m0["pnls"], dtype=float)
    tot = pnls.sum()
    order = np.sort(pnls)[::-1]
    print("   сделок %d, сумма PnL %+.2f$ на базе $%.0f = %+.1f%% "
          "(фикс. маржа)" % (len(pnls), tot, e2.START, tot / e2.START * 100))
    print("   пять лучших сделок: " + ", ".join("%+.2f$" % x for x in order[:5]))
    print("   пять худших сделок: " + ", ".join("%+.2f$" % x for x in order[-5:]))
    for k in (1, 2, 3, 5):
        rest = tot - order[:k].sum()
        print("   без %d лучших сделок: %+.2f$ = %+.1f%% от базы"
              % (k, rest, rest / e2.START * 100))
    print("   доля самой крупной сделки в итоге: %.0f%%"
          % (order[0] / tot * 100))
    # бутстрэп: тянем те же 31 сделку с возвратом
    rng = np.random.default_rng(20260821)
    boot = rng.choice(pnls, size=(20000, len(pnls)), replace=True).sum(axis=1)
    print("   бутстрэп 20000 наборов из тех же %d сделок: медиана %+.1f%%, "
          "5-й процентиль %+.1f%%, доля положительных %.0f%%"
          % (len(pnls), np.median(boot) / e2.START * 100,
             np.percentile(boot, 5) / e2.START * 100, (boot > 0).mean() * 100))
    # месяцы
    ts = np.array([e["t"] for e in e0 if e["type"] == "close"], dtype=float)
    t0 = d["hold"]["candles"][0][0]
    mo_idx = ((ts - t0) // (30 * 86400000)).astype(int)
    print("   по месяцам холдаута (номер: сделок / PnL%% от базы):")
    line = []
    for k in range(mo_idx.max() + 1):
        sel = pnls[mo_idx == k]
        line.append("%d:%d/%+.1f%%" % (k + 1, len(sel),
                                       sel.sum() / e2.START * 100))
    print("     " + "  ".join(line))
    pos_m = sum(1 for k in range(mo_idx.max() + 1)
                if pnls[mo_idx == k].sum() > 0)
    print("   прибыльных месяцев: %d из %d" % (pos_m, mo_idx.max() + 1))


if __name__ == "__main__":
    main()
