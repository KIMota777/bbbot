# -*- coding: utf-8 -*-
u"""Сводный ответ: даёт ли мета-модель перевес, если ничего не выбирать.

ЗАЧЕМ ЕЩЁ ОДИН ПРОГОН. В ml_meta мета-модель считалась на одном, срединном
сочетании параметров каждого правила. В ml_stress выяснилось, что у supertrend
единственный красивый случай — именно это сочетание, а на восьми других той же
сетки прибавка вдесятеро меньше. Это ровно та ловушка, из-за которой всё
исследование и дало отрицательный ответ: лучший из многих хорош тем, что он
лучший из многих.

Поэтому здесь считается ВСЯ решётка сразу и печатается не победитель, а
распределение. Победитель всё равно называется — но рядом с числом, сколько
попыток его породило.

ПОДСЧЁТ НЕЗАВИСИМЫХ ПОПЫТОК. Сочетания одной сетки часто дают ОДИН И ТОТ ЖЕ
набор входов (например stop_atr меняет ширину стопа, но не момент входа).
Считать их за разные наблюдения — самообман, раздувающий значимость. Поэтому
конфигурации группируются по (правило, таймфрейм, число входов), и сводная
ошибка считается по группам, а не по строкам.
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

OUT = os.path.join(DIR, "out")
RULES = ["supertrend", "bos", "vol_spike", "c_willr"]
N_COMBO = 5     # срединное + четыре случайных


def main():
    t0, t1 = rdata.SPLITS["trainval"]
    reg = ml_lib.registry()
    rows = []
    print(u"ВСЯ РЕШЁТКА СРАЗУ: 4 правила x 2 тф x %d сочетания x 2 модели"
          % N_COMBO)
    print(u"Прибавка — доходность на один вход с фильтром минус без фильтра.")
    print(u"Ноль означает «модель не отличила хорошие входы от плохих».\n")
    print(u"   %-11s %-4s %-11s %6s | %9s %9s | %8s %8s"
          % (u"правило", u"тф", u"сочетание", u"входов", u"логрег",
             u"бустинг", u"без, мес", u"AUC л/б"))
    for name in RULES:
        st = reg[name]
        combos = [ml_lib.center_combo(st)] + \
            ml_lib.random_combos(st, N_COMBO - 1)
        tags = [u"срединное"] + [u"случ. %d" % (i + 1)
                                 for i in range(N_COMBO - 1)]
        for tf in ("60", "240"):
            for tag, p in zip(tags, combos):
                d = ml_lib.dataset(name, p, tf, t0, t1, causal_first=False)
                if d is None or len(d["y"]) < 200:
                    continue
                vals, aucs, mos = [], [], []
                for kind in (u"логрег", u"бустинг"):
                    r = ml_meta.run_config(d, kind)
                    if r is None:
                        vals.append(np.nan)
                        aucs.append(np.nan)
                        continue
                    vals.append(r[u"половина лучших"]["dmean"])
                    aucs.append(r["auc_pool"])
                    mos.append(r["base"]["mo"])
                    rows.append(dict(rule=name, tf=tf, combo=tag, kind=kind,
                                     n=len(d["y"]), dmean=vals[-1],
                                     auc=aucs[-1], base_mo=r["base"]["mo"],
                                     filt_mo=r[u"половина лучших"]["mo"],
                                     group="%s|%s|%d" % (name, tf, len(d["y"]))))
                print(u"   %-11s %-4s %-11s %6d | %+8.4f%% %+8.4f%% | "
                      u"%+7.2f%% %.2f/%.2f"
                      % (name, tf, tag, len(d["y"]), 100 * vals[0],
                         100 * vals[1], 100 * (mos[0] if mos else 0),
                         aucs[0], aucs[1]))

    dm = np.array([r["dmean"] for r in rows])
    au = np.array([r["auc"] for r in rows])
    print(u"\n\nСВОДКА ПО ВСЕЙ РЕШЁТКЕ")
    print(u"   всего конфигураций: %d" % len(rows))
    print(u"   прибавка положительна: %d из %d (при отсутствии перевеса "
          u"ожидается половина)" % (int((dm > 0).sum()), len(dm)))
    print(u"   медиана прибавки: %+.4f%% на вход" % (100 * np.median(dm)))
    print(u"   среднее прибавки:  %+.4f%% на вход" % (100 * dm.mean()))
    print(u"   AUC вне обучения: среднее %.3f, максимум %.3f, "
          u"выше 0.55: %d из %d"
          % (au.mean(), au.max(), int((au > 0.55).sum()), len(au)))

    # ошибка считается по НЕЗАВИСИМЫМ группам входов, а не по строкам
    groups = {}
    for r in rows:
        groups.setdefault((r["group"], r["kind"]), []).append(r["dmean"])
    g = np.array([np.mean(v) for v in groups.values()])
    se = g.std(ddof=1) / np.sqrt(len(g))
    print(u"\n   независимых наборов входов (x модель): %d" % len(g))
    print(u"   средняя прибавка по ним: %+.4f%% ± %.4f%% на вход"
          % (100 * g.mean(), 100 * se))
    print(u"   отношение к ошибке: %.2f  ->  %s"
          % (g.mean() / se if se else 0.0,
             u"НЕОТЛИЧИМО ОТ НУЛЯ" if abs(g.mean()) < 2 * se
             else u"отличимо от нуля"))
    print(u"   для сравнения: комиссия и проскальзывание одного круга —")
    print(u"   около 0.11%% оборота, что при плече ~7 съедает примерно")
    print(u"   0.77%% капитала на сделку. Прибавка модели меньше этого в "
          u"%.0f раз." % (0.0077 / max(abs(g.mean()), 1e-9)))

    best = max(rows, key=lambda r: r["dmean"])
    rank = sorted(dm)[::-1]
    print(u"\n   ЛУЧШАЯ КОНФИГУРАЦИЯ ИЗ %d: %s|%s|%s|%s"
          % (len(rows), best["rule"], best["tf"], best["combo"], best["kind"]))
    print(u"      прибавка %+.4f%% на вход, без фильтра %+.2f%% в месяц, "
          u"с фильтром %+.2f%%"
          % (100 * best["dmean"], 100 * best["base_mo"],
             100 * best["filt_mo"]))
    print(u"      она же — %d-я сверху из %d попыток; вторая даёт %+.4f%%"
          % (1, len(dm), 100 * rank[1]))
    print(u"      разрыв между первой и второй: %.1f раза"
          % (rank[0] / max(rank[1], 1e-9)))

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "ml_verdict.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, default=float)
    print(u"\nсохранено -> out/ml_verdict.json")


if __name__ == "__main__":
    main()
