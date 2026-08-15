# -*- coding: utf-8 -*-
u"""Что именно выносится на экзамен. Считается ДО того, как экзамен прочитан.

Из четырёх проверенных направлений уцелело одно — дневной трендовый горизонт.
Поперечный импульс и нормировка по волатильности отвергнуты в
refute_horizon_xs.py. Здесь остаток доводится до состояния, которое можно
предъявить один раз.

ТРИ ВЕЩИ, КОТОРЫЕ ЗДЕСЬ ДЕЛАЮТСЯ

1. ДИАГНОСТИКА ОТБОРА НА ДНЕВНОМ ГОРИЗОНТЕ. У автора связь «результат на
   обучении — результат на проверке» вышла -0.17, и это его главный вывод.
   Тот же счёт повторяется для девяти дневных правил. Если и здесь связь
   около нуля — значит дневной горизонт не лечит ОТБОР, и единственный
   законный ход — не выбирать вовсе.

2. КАЛИБРОВКА РИСКА. Целевая волатильность подбирается двоичным поиском так,
   чтобы историческая просадка на обучении+проверке равнялась 20% — тому же
   потолку, по которому калибровался автор. Риск здесь не источник дохода, а
   способ сравнивать с его числами на равных.

3. ЗАПИСЬ РЕШЕНИЯ. Состав, параметры и ожидания печатаются и сохраняются до
   прогона на экзамене.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import rdata                       # noqa: E402
import refute_horizon_combo as C   # noqa: E402
import refute_horizon_lib as L     # noqa: E402
import refute_horizon_ts as TS     # noqa: E402

OUT = os.path.join(DIR, "out")


def selection_diagnosis():
    u"""Предсказывает ли результат на обучении результат на проверке — здесь."""
    a, b, names = [], [], []
    for name, fn, arg in C.TREND:
        ra = TS.run_on("train", TS.make_weights(fn, arg))
        rb = TS.run_on("val", TS.make_weights(fn, arg))
        a.append(ra["sharpe"])
        b.append(rb["sharpe"])
        names.append(name)
    a, b = np.array(a), np.array(b)
    c = float(np.corrcoef(a, b)[0][1])
    print(u"СВЯЗЬ «ХОРОШО НА ОБУЧЕНИИ» И «ХОРОШО НА ПРОВЕРКЕ»")
    print(u"по девяти дневным правилам, мера — Шарп\n")
    print(u"%-16s %9s %9s" % (u"правило", u"обучение", u"проверка"))
    for n, x, y in zip(names, a, b):
        print(u"%-16s %9.2f %9.2f" % (n, x, y))
    print(u"\n   связь: %+.2f   (у автора на его каталоге было -0.17)" % c)
    best = names[int(np.argmax(a))]
    print(u"   лучшее по обучению — «%s», на проверке оно %.2f при "
          u"среднем %.2f" % (best, b[int(np.argmax(a))], b.mean()))
    print(u"   ВЫВОД: выбирать лучшее по прошлому по-прежнему бессмысленно.")
    print(u"   Поэтому в решение идёт СРЕДНЕЕ ВСЕХ ДЕВЯТИ, а не лучшее.\n")
    return c


def calibrate(target_dd=0.20):
    u"""Целевая волатильность под заданную историческую просадку."""
    lo, hi = 0.05, 1.20
    for _ in range(18):
        mid = 0.5 * (lo + hi)
        TS.TARGET_VOL = mid
        r = TS.run_on("trainval", C.build_composite())
        if r["maxdd"] > target_dd:
            hi = mid
        else:
            lo = mid
    TS.TARGET_VOL = lo
    r = TS.run_on("trainval", C.build_composite())
    return lo, r


def main():
    selection_diagnosis()

    vol, r = calibrate(0.20)
    print(u"КАЛИБРОВКА РИСКА")
    print(u"   целевая волатильность %.1f%% годовых даёт просадку %.1f%% "
          u"на обучении+проверке" % (100 * vol, 100 * r["maxdd"]))
    print(u"   при ней: итог %+.1f%%, в месяц %+.2f%%, медиана месяца %+.2f%%,"
          % (100 * r["ret"], 100 * r["mo"], 100 * r["mo_med"]))
    print(u"            Шарп %.2f, плюсовых месяцев %.0f%%, месяцев всего %d"
          % (r["sharpe"], 100 * r["mo_pos"], r["n_months"]))
    print(u"   оборот за весь срок %.0f капиталов — примерно %.1f в месяц"
          % (r["turnover"] / 10000.0, r["turnover"] / 10000.0 / 32.0))
    print(u"   уплачено издержек %.0f$ и фандинга %.0f$ на 10000$ старта\n"
          % (r["costs"], r["funding"]))

    a = C.daily_returns(r)
    bs = C.block_boot(a)
    lo95, hi95 = np.percentile(bs, [2.5, 97.5])
    print(u"   в месяц с границами: %+.2f%%  (%.2f%% .. %+.2f%%), "
          u"ниже нуля %.0f%% прогонов"
          % (100 * ((1 + a.mean()) ** 30 - 1), 100 * ((1 + lo95) ** 30 - 1),
             100 * ((1 + hi95) ** 30 - 1), 100 * (bs < 0).mean()))

    spec = dict(
        rule=u"среднее девяти канонических трендовых правил",
        rules=[n for n, _f, _a in C.TREND],
        bars=u"дневные, собраны из 4ч",
        symbols=rdata.SYMBOLS,
        side=u"обе стороны",
        target_vol=vol, vol_n=TS.VOL_N, lev_cap=TS.LEV_CAP,
        trainval=dict(ret=r["ret"], mo=r["mo"], mo_med=r["mo_med"],
                      maxdd=r["maxdd"], sharpe=r["sharpe"],
                      mo_pos=r["mo_pos"]),
        ci_month=[float((1 + lo95) ** 30 - 1), float((1 + hi95) ** 30 - 1)],
    )
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "refute_horizon_spec.json"), "w",
              encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=1)

    print(u"\nЧТО ВЫНОСИТСЯ НА ЭКЗАМЕН (зафиксировано, дальше не меняется)")
    print(u"   правило .......... среднее девяти трендовых правил, дневные бары")
    print(u"   монеты ........... все пять, равными долями")
    print(u"   стороны .......... обе (лонг и шорт)")
    print(u"   размер ........... обратно 30-дневной волатильности, "
          u"цель %.1f%% годовых, потолок веса %.1f" % (100 * vol, TS.LEV_CAP))
    print(u"   пересмотр ........ ежедневно на открытии")
    print(u"   ничего не выбрано по результату: ни правило, ни монета, ни срок")
    print(u"\n   ОЖИДАНИЕ. Перевес не доказан: на обучении+проверке он "
          u"неотличим\n   от нуля (доля прогонов ниже нуля %.0f%%, "
          u"перестановочный тест 12%%).\n   Экзамен не может это исправить — "
          u"169 дней ещё короче. Он может\n   только показать, ведёт ли себя "
          u"правило так же, как раньше, или\n   разваливается, как ансамбль "
          u"автора." % (100 * (bs < 0).mean()))
    print(u"\nсохранено -> out/refute_horizon_spec.json")


if __name__ == "__main__":
    main()
