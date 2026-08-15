# -*- coding: utf-8 -*-
"""Разбор перебора: что выжило, и сколько в этом случайности.

Главная опасность на этом шаге — прочитать верхнюю строку таблицы как ответ.
Проверено 26 тысяч сочетаний; лучшее из 26 тысяч выглядит прекрасно даже на
чистом шуме. Поэтому смотрим не на победителя, а на три вещи, которые шум
подделать не может:

  СОГЛАСИЕ МЕЖДУ АКТИВАМИ. Стратегия, работающая на одной монете из десяти
  пар (монета, таймфрейм), — это найденное свойство одной монеты. Работающая
  на восьми из десяти — кандидат в свойство рынка.

  СОГЛАСИЕ МЕЖДУ ОКНАМИ. Шесть независимых внеобучающих кусков. Четыре и
  более положительных — сигнал; три из шести — это подбрасывание монеты.

  СОГЛАСИЕ МЕЖДУ СХЕМАМИ ОТБОРА. Скользящее окно обучения и растущее от
  начала истории дают разные параметры. Если результат есть только у одной
  схемы, он держится на схеме, а не на рынке.
"""
import argparse
import json
import math
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

OUT = os.path.join(DIR, "out")


def load(name="sweep_main.json"):
    with open(os.path.join(OUT, name), encoding="utf-8") as fh:
        return json.load(fh)


def by_strategy(rows):
    """Сводка по стратегии: сколько пар (монета, ТФ) дали плюс вне обучения."""
    agg = {}
    for r in rows:
        if not r.get("ok"):
            continue
        a = agg.setdefault(r["strategy"], dict(
            family=r["family"], pairs=0, pos=0, mo=[], dd=[], folds=[],
            trades=0, anch_mo=[]))
        a["pairs"] += 1
        a["mo"].append(r["wf"]["mo"])
        a["dd"].append(r["wf"]["maxdd"])
        a["trades"] += r["wf"]["trades"]
        a["folds"].append((r["folds_pos"], r["folds"]))
        a["anch_mo"].append(r["wf_anchored"]["mo"])
        if r["wf"]["mo"] > 0:
            a["pos"] += 1
    out = []
    for name, a in agg.items():
        mo = np.array(a["mo"])
        fp = sum(x for x, _ in a["folds"])
        ft = sum(y for _, y in a["folds"])
        out.append(dict(
            strategy=name, family=a["family"], pairs=a["pairs"], pos=a["pos"],
            share=a["pos"] / max(a["pairs"], 1),
            mo_med=float(np.median(mo)), mo_best=float(mo.max()),
            mo_worst=float(mo.min()),
            dd_med=float(np.median(a["dd"])),
            anch_med=float(np.median(a["anch_mo"])),
            folds_pos=fp, folds_all=ft,
            fold_share=fp / max(ft, 1), trades=a["trades"]))
    return sorted(out, key=lambda x: (-x["share"], -x["mo_med"]))


def by_family(rows):
    agg = {}
    for r in rows:
        if not r.get("ok"):
            continue
        a = agg.setdefault(r["family"], dict(n=0, pos=0, mo=[]))
        a["n"] += 1
        a["mo"].append(r["wf"]["mo"])
        if r["wf"]["mo"] > 0:
            a["pos"] += 1
    out = []
    for f, a in agg.items():
        mo = np.array(a["mo"])
        out.append(dict(family=f, n=a["n"], pos=a["pos"],
                        share=a["pos"] / a["n"],
                        mo_med=float(np.median(mo)),
                        mo_mean=float(mo.mean())))
    return sorted(out, key=lambda x: -x["mo_med"])


def luck_threshold(n_tests, n_obs, sd_hint=None):
    """Насколько хорош должен быть результат, чтобы не быть везением.

    Ожидаемый максимум из n независимых стандартных нормальных величин растёт
    примерно как sqrt(2*ln n). При 26 тысячах попыток это около 3.9 сигмы: то
    есть даже на полностью случайных данных лучшая стратегия покажет результат,
    отстоящий от нуля на четыре стандартных отклонения. Любой отбор без этой
    поправки находит именно такую стратегию.
    """
    if n_tests < 2:
        return 0.0
    return math.sqrt(2.0 * math.log(n_tests))


def binom_p(k, n, p=0.5):
    """Вероятность получить k и более успехов из n при честной монете."""
    from scipy import stats
    return float(stats.binom.sf(k - 1, n, p))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="sweep_main.json")
    ap.add_argument("--top", type=int, default=30)
    a = ap.parse_args()
    d = load(a.file)
    rows = d["rows"]
    ok = [r for r in rows if r.get("ok")]

    print("ПЕРЕБОР: %d задач, из них рабочих %d, сочетаний параметров %d"
          % (len(rows), len(ok), d["total_combos"]))
    print("Окно обучения %d дней, окно проверки %d дней, окон на пару 6."
          % (d["is_days"], d["oos_days"]))
    z = luck_threshold(d["total_combos"], 0)
    print("Порог везения: при %d попытках лучший результат отстоит от нуля"
          % d["total_combos"])
    print("на %.2f стандартных отклонения ДАЖЕ НА ЧИСТОМ ШУМЕ." % z)

    print("\n\nСЕМЕЙСТВА (доля пар монета-ТФ, вышедших в плюс вне обучения)")
    print("%-14s %5s %6s %8s %9s" % ("семейство", "пар", "плюс", "доля",
                                     "медиана мес"))
    for f in by_family(ok):
        print("%-14s %5d %6d %7.0f%% %+8.2f%%"
              % (f["family"], f["n"], f["pos"], 100 * f["share"],
                 100 * f["mo_med"]))

    print("\n\nСТРАТЕГИИ ПО СОГЛАСИЮ МЕЖДУ АКТИВАМИ")
    print("Сортировка по доле пар в плюсе, а не по лучшему результату.")
    print("%-18s %-11s %4s %5s %6s %9s %9s %8s %7s"
          % ("стратегия", "семейство", "пар", "плюс", "доля", "медиана",
             "лучшая", "просадка", "окна+"))
    st = by_strategy(ok)
    for s in st[:a.top]:
        print("%-18s %-11s %4d %5d %5.0f%% %+8.2f%% %+8.2f%% %7.0f%% %3d/%d"
              % (s["strategy"], s["family"], s["pairs"], s["pos"],
                 100 * s["share"], 100 * s["mo_med"], 100 * s["mo_best"],
                 100 * s["dd_med"], s["folds_pos"], s["folds_all"]))

    print("\n\nПРОВЕРКА НА МОНЕТКУ: сколько окон вышло в плюс")
    print("Если бы стратегия была шумом, доля положительных окон была бы ~50%.")
    print("%-18s %8s %10s  %s" % ("стратегия", "окна+", "вероятн.", "вывод"))
    for s in st[:a.top]:
        if s["folds_all"] < 8:
            continue
        p = binom_p(s["folds_pos"], s["folds_all"])
        verdict = ("похоже на сигнал" if p < 0.01 else
                   "слабо" if p < 0.10 else "неотличимо от монетки")
        print("%-18s %4d/%-3d %9.4f  %s"
              % (s["strategy"], s["folds_pos"], s["folds_all"], p, verdict))

    print("\n\nСОГЛАСИЕ ДВУХ СХЕМ ОТБОРА (скользящее окно против растущего)")
    print("%-18s %10s %10s  %s" % ("стратегия", "скользящ", "растущее",
                                   "согласны?"))
    for s in st[:a.top]:
        agree = "да" if (s["mo_med"] > 0) == (s["anch_med"] > 0) else "НЕТ"
        print("%-18s %+9.2f%% %+9.2f%%  %s"
              % (s["strategy"], 100 * s["mo_med"], 100 * s["anch_med"], agree))

    print("\n\nЛУЧШИЕ ОТДЕЛЬНЫЕ ПАРЫ (для справки — это уже отбор из 610)")
    ok.sort(key=lambda r: -r["wf"]["mo"])
    print("%-18s %-5s %-4s %9s %8s %7s %6s %7s"
          % ("стратегия", "мон", "тф", "мес", "итог", "просад", "сдел", "окна+"))
    for r in ok[:20]:
        print("%-18s %-5s %-4s %+8.2f%% %+7.1f%% %6.1f%% %6d %3d/%d"
              % (r["strategy"], r["symbol"].replace("USDT", ""), r["tf"],
                 100 * r["wf"]["mo"], 100 * r["wf"]["ret"],
                 100 * r["wf"]["maxdd"], r["wf"]["trades"],
                 r["folds_pos"], r["folds"]))

    print("\n\nРАЗРЫВ МЕЖДУ ПОДГОНКОЙ И ЧЕСТНОСТЬЮ")
    print("Слева — лучшее сочетание, подобранное по ВСЕЙ выборке (так делать")
    print("нельзя, это верхняя граница самообмана). Справа — та же стратегия")
    print("по протоколу. Разрыв показывает, сколько в результате подгонки.")
    print("%-18s %-5s %-4s %10s %10s %8s"
          % ("стратегия", "мон", "тф", "подгонка", "честно", "разрыв"))
    for r in ok[:15]:
        isb = r["insample_best"]["ret"]
        wf = r["wf"]["ret"]
        print("%-18s %-5s %-4s %+9.1f%% %+9.1f%% %7.1fx"
              % (r["strategy"], r["symbol"].replace("USDT", ""), r["tf"],
                 100 * isb, 100 * wf,
                 (1 + isb) / max(1 + wf, 1e-6)))


if __name__ == "__main__":
    main()
