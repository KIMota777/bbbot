# -*- coding: utf-8 -*-
"""ТРЕТЬЯ ПОПЫТКА: не «хорош ли normal x5», а «есть ли РАЗРЫВ между ним и
final x15» — потому что находка сформулирована как сравнение.

Из окон видно, что почти весь разрыв сидит в одном квартале (февраль-апрель
2026). Здесь это проверяется в лоб:

  1. Разрыв по кварталам холдоута: сколько из него даёт один кусок.
  2. Холдоут БЕЗ этого квартала — остаётся ли превосходство.
  3. Худшие сделки final: не одна ли сделка на x15 сделала весь его минус.
  4. Разница средних по сделкам как статистика (Уэлч + перестановочный тест):
     31 и 14 сделок — отличима ли разница от нуля вообще.
  5. Просадка при ОДИНАКОВОМ плече — своя ли она у конфига или это плечо.
  6. Отбор лучшего из 13 архивных конфигов: сколько их вообще было в плюсе на
     холдоуте (лучший из многих на одном куске завышен по построению).
"""
import math
import random
import statistics

import bots_honest as bh
import refute_btc_common as R

N_PERM = 20000
QUARTERS = [("2025-10", "2025-12"), ("2026-01", "2026-03"),
            ("2026-04", "2026-06"), ("2026-07", "2026-08")]


def in_q(t, q):
    return q[0] <= R.day(t)[:7] <= q[1]


def main():
    c, aux = R.load()
    n = len(c)
    h = int(n * 0.72)
    gN, lN = R.genome("BTCUSDT", "normal")
    gF, lF = R.genome("BTCUSDT", "final")
    mN = R.run_seg(c, aux, h, n, gN, lN)
    mF = R.run_seg(c, aux, h, n, gF, lF)

    print("=" * 96)
    print("1. ГДЕ ЖИВЁТ РАЗРЫВ. PnL $ по кварталам холдоута (база $20), "
          "разрыв = normal минус final")
    print("%-18s %6s %9s | %6s %9s | %9s" %
          ("квартал", "сд.N", "PnL N$", "сд.F", "PnL F$", "разрыв $"))
    gaps = []
    for q in QUARTERS:
        a = [p for t, p in mN["closes"] if in_q(t, q)]
        b = [p for t, p in mF["closes"] if in_q(t, q)]
        gap = sum(a) - sum(b)
        gaps.append((q, gap))
        print("%-18s %6d %+9.2f | %6d %+9.2f | %+9.2f"
              % (q[0] + ".." + q[1], len(a), sum(a), len(b), sum(b), gap))
    tot_gap = mN["pnl_usd"] - mF["pnl_usd"]
    top = max(gaps, key=lambda x: x[1])
    print("   ВЕСЬ разрыв за холдоут: %+.2f$ (%.1f п.п. от базы $20)"
          % (tot_gap, tot_gap / 20 * 100))
    print("   Лучший для normal квартал %s..%s даёт %+.2f$ = %.0f%% всего "
          "разрыва." % (top[0][0], top[0][1], top[1], top[1] / tot_gap * 100))

    print()
    print("=" * 96)
    print("2. ТОТ ЖЕ ХОЛДОУТ БЕЗ РЕШАЮЩЕГО КВАРТАЛА (%s..%s выброшен)"
          % (top[0][0], top[0][1]))
    a = [p for t, p in mN["closes"] if not in_q(t, top[0])]
    b = [p for t, p in mF["closes"] if not in_q(t, top[0])]
    print("   normal x5 : %2d сделок, %+.2f$ = %+.1f%% от базы"
          % (len(a), sum(a), sum(a) / 20 * 100))
    print("   final x15 : %2d сделок, %+.2f$ = %+.1f%% от базы"
          % (len(b), sum(b), sum(b) / 20 * 100))
    print("   -> %s" % ("превосходство normal ПЕРЕЖИВАЕТ выброс квартала"
                        if sum(a) > sum(b) else
                        "без одного квартала normal УЖЕ НЕ ЛУЧШЕ — весь "
                        "разрыв держался на нём"))

    print()
    print("=" * 96)
    print("3. НЕ ОДНА ЛИ СДЕЛКА СДЕЛАЛА МИНУС final x15")
    for name, m in (("normal x5", mN), ("final x15", mF)):
        pn = sorted(m["closes"], key=lambda tp: tp[1])
        print("   %-10s итог %+.2f$ | три худшие сделки: %s"
              % (name, m["pnl_usd"],
                 ", ".join("%s %+.2f$" % (R.day(t), p) for t, p in pn[:3])))
        print("   %-10s без худшей сделки итог был бы %+.2f$ (%+.1f%% от базы)"
              % ("", m["pnl_usd"] - pn[0][1],
                 (m["pnl_usd"] - pn[0][1]) / 20 * 100))

    print()
    print("=" * 96)
    print("4. РАЗНИЦА МЕЖДУ КОНФИГАМИ КАК СТАТИСТИКА (копеечные выходы "
          "обнулены)")
    A = [0.0 if bh.is_tiny(p) else p for _, p in mN["closes"]]
    B = [0.0 if bh.is_tiny(p) else p for _, p in mF["closes"]]
    mA, mB = statistics.mean(A), statistics.mean(B)
    sA, sB = statistics.stdev(A), statistics.stdev(B)
    seA, seB = sA / math.sqrt(len(A)), sB / math.sqrt(len(B))
    t = (mA - mB) / math.sqrt(seA ** 2 + seB ** 2)
    print("   средняя сделка normal %+.4f$ +- %.4f (n=%d, разброс %.3f)"
          % (mA, seA, len(A), sA))
    print("   средняя сделка final  %+.4f$ +- %.4f (n=%d, разброс %.3f)"
          % (mB, seB, len(B), sB))
    print("   t Уэлча на разницу средних = %+.2f  -> %s" %
          (t, "разница ЗНАЧИМА" if abs(t) > 2 else
           "разница от нуля НЕ ОТЛИЧИМА (|t| < 2)"))
    rng = random.Random(4242)
    pool = A + B
    obs = mA - mB
    cnt = 0
    for _ in range(N_PERM):
        rng.shuffle(pool)
        d = statistics.mean(pool[:len(A)]) - statistics.mean(pool[len(A):])
        if d >= obs:
            cnt += 1
    print("   перестановочный тест (%d перемешиваний ярлыков): разницу такого "
          "размера или больше" % N_PERM)
    print("   случайность даёт в %.1f%% случаев -> %s"
          % (cnt / N_PERM * 100,
             "это не случайность" if cnt / N_PERM < 0.05 else
             "разрыв НЕ выходит за пределы случайного"))
    print("   Заметьте разброс: у final он в %.1f раза шире. Именно широкий "
          "разброс на x15, а не" % (sB / sA))
    print("   систематический минус, делает его «-6%» — при 14 сделках это "
          "просто шум.")

    print()
    print("=" * 96)
    print("5. ПРОСАДКА ПРИ ОДИНАКОВОМ ПЛЕЧЕ (ось «9.3% против 22.4%» из "
          "находки)")
    for lv in (5, 15):
        a1 = R.run_seg(c, aux, h, n, gN, lv)
        b1 = R.run_seg(c, aux, h, n, gF, lv)
        print("   x%-2d  normal просадка %5.1f%% | final просадка %5.1f%% | "
              "разница %+.1f п.п." % (lv, a1["dd"], b1["dd"],
                                      b1["dd"] - a1["dd"]))
    print("   Если на равном плече просадки почти совпадают, то «9.3 против "
          "22.4» — это про плечо x5 vs x15,")
    print("   а не про конфиг, и как ось сравнения конфигов она не считается.")

    print()
    print("=" * 96)
    print("6. ЛУЧШИЙ ИЗ СКОЛЬКИХ. Архивная таблица — это отбор победителя по "
          "тому же холдоуту")
    import json
    d = json.load(open("webapp/data/archive_honest.json", encoding="utf-8"))
    rows = d["rows"]
    pos = [r for r in rows if r["rob_med_nt"] > 0]
    print("   в архиве посчитано %d конфигов; по устойчивости в плюсе %d"
          % (len(rows), len(pos)))
    for r in sorted(rows, key=lambda r: -r["rob_med_nt"])[:5]:
        print("     %-9s %-7s x%-2d  холдаут %+7.1f%%  робаст %+6.1f%%  "
              "доля+ %3.0f%%" % (r["symbol"], r["mode"], r["lev"], r["comp"],
                                 r["rob_med_nt"], r["share_pos_nt"]))
    print("   Победитель выбран ПО ТОМУ ЖЕ куску, на котором потом и "
          "празднуется его результат.")
    print("   Максимум из %d шумных чисел смещён вверх — это надо вычесть из "
          "восторга." % len(rows))


if __name__ == "__main__":
    main()
