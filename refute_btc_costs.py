# -*- coding: utf-8 -*-
"""Линза 1: выживает ли BTC normal x5, когда условия торговли ухудшаются.

Комиссии, проскальзывание и фандинг живут в глобалах evolution2 (TAKER,
MAKER, SLIP, FUND_8H). Множим их на время прогона — файл движка не трогаем,
ровно как это делает раздел 4 в bots_honest.

Почему это честная проверка: подбор конфига видел ОДИН набор издержек.
Если перевес настоящий (цена действительно возвращается к середине
диапазона), полуторные комиссии его только уменьшат. Если перевес —
это подгонка под конкретную сетку сделок, лишние доли процента на входе
сдвинут путь исполнения и результат развалится непропорционально.
"""
import sys

import evolution2 as e2
import refute_btc_common as rc

BASE = dict(TAKER=e2.TAKER, MAKER=e2.MAKER, SLIP=e2.SLIP, FUND_8H=e2.FUND_8H)

# (подпись, множитель комиссий+фандинга, множитель проскальзывания)
CASES = [
    ("базовые издержки",            1.0, 1.0),
    ("комиссии x1.25, слип x1.5",   1.25, 1.5),
    ("комиссии x1.5,  слип x2",     1.5, 2.0),
    ("комиссии x2,    слип x3",     2.0, 3.0),
    ("комиссии x3,    слип x4",     3.0, 4.0),
    ("только слип x2",              1.0, 2.0),
    ("только комиссии x2",          2.0, 1.0),
]


def run_case(d, fee_k, slip_k):
    old = {k: getattr(e2, k) for k in BASE}
    e2.TAKER = BASE["TAKER"] * fee_k
    e2.MAKER = BASE["MAKER"] * fee_k
    e2.FUND_8H = BASE["FUND_8H"] * fee_k
    e2.SLIP = BASE["SLIP"] * slip_k
    try:
        r, evs = rc.run_hold(d)
    finally:
        for k, v in old.items():
            setattr(e2, k, v)
    return rc.nums(r, evs, d["hold"]["months"])


def main():
    d = rc.build()
    print("BTC normal x5, ХОЛДАУТ %.1f мес — устойчивость к ухудшению условий"
          % d["hold"]["months"])
    print("%-28s %6s %7s %9s %10s %8s %8s"
          % ("случай", "сдел", "ВР%", "холдаут%", "б/копеек%", "просад%",
             "тейкер%"))
    for name, fk, sk in CASES:
        m = run_case(d, fk, sk)
        print("%-28s %6d %6.1f %+8.1f%% %+9.1f%% %7.1f%% %7.4f%%"
              % (name, m["trades"], m["wr"], m["comp"], m["comp_nt"], m["dd"],
                 BASE["TAKER"] * fk * 100))
        sys.stdout.flush()

    # Отдельно: сколько денег вообще уходит на издержки при базовых условиях.
    # Если доля мала, полуторные издержки конфиг убить не могут в принципе —
    # и тогда эта линза его не проверяет, о чём надо сказать прямо.
    old = {k: getattr(e2, k) for k in BASE}
    for k in BASE:
        setattr(e2, k, 0.0)
    try:
        r, evs = rc.run_hold(d)
    finally:
        for k, v in old.items():
            setattr(e2, k, v)
    gross = rc.nums(r, evs, d["hold"]["months"])
    base = run_case(d, 1.0, 1.0)
    print()
    print("валовый итог без единой издержки: %+.1f%% (сделок %d)"
          % (gross["comp"], gross["trades"]))
    print("издержки съедают %.1f%% валовой прибыли"
          % ((gross["comp"] - base["comp"]) / gross["comp"] * 100))


if __name__ == "__main__":
    main()
