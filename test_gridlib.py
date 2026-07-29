# -*- coding: utf-8 -*-
"""Проверки подвижной сетки и подвижных SL/TP (волна v10).

Главное, что тут доказывается: при OFF-значениях генов новый код даёт РОВНО
прежний результат — тот же список сделок и тот же баланс до последнего бита.
Без этого требование «сетка не должна быть убыточнее текущей» ничем не
обеспечено: любая разница в третьем знаке шага сдвигает, какие свечи задели
колена, и весь бэктест уезжает.

Запуск: python test_gridlib.py
"""

import math

import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd
import gridlib

OK = 0


def check(cond, name):
    global OK
    assert cond, f"ПРОВАЛ: {name}"
    OK += 1
    print(f"  ok  {name}")


def old_grid_prices(px, sgn, levels, step):
    """Эталон: как сетка строилась ДО волны v10 (скопировано из run5)."""
    out, ap = [], px
    for _ in range(1, levels):
        ap = ap * (1 - sgn * step)
        out.append(ap)
    return out


print("1. Геометрия сетки при выключенных генах == прежняя формула")
for px in (0.07021, 45.02, 1894.17, 64072.7):
    for sgn in (1, -1):
        for levels in (1, 2, 3, 4):
            for step in (0.004, 0.0056974, 0.0137541, 0.03):
                stop = px * (1 - sgn * 0.05)
                new = gridlib.grid_prices(
                    px, stop, sgn, levels, step,
                    mode=gridlib.OFF10["grid_mode"],
                    span=gridlib.OFF10["grid_span"],
                    spread=gridlib.OFF10["grid_spread"],
                    vol_k=gridlib.vol_factor(0.01, 0.02,
                                             gridlib.OFF10["grid_atr_k"]))
                check(new == old_grid_prices(px, sgn, levels, step),
                      f"сетка px={px} sgn={sgn} levels={levels} step={step}")

print("\n1b. gridlib.atr_series совпадает с evolution.calc_atr_pct до бита")
_c = ev.fetch("BTCUSDT", "15", 1150)[-6000:]
for n in (24, 96):
    check(gridlib.atr_series(_c, n) == ev.calc_atr_pct(_c, n),
          f"ряд ATR идентичен движку (n={n})")

print("\n2. Множители при k=0 равны 1.0 даже на битых данных")
for bad in (None, 0.0, float("nan"), float("inf")):
    check(gridlib.vol_factor(bad, bad, 0) == 1.0, f"vol_factor(k=0, {bad})")
    check(gridlib.tp_factor(bad, bad, 0) == 1.0, f"tp_factor(k=0, {bad})")
check(gridlib.vol_factor(0.02, 0.01, 0.5) == 1.5, "vol_factor растёт с ATR")
check(gridlib.vol_factor(1.0, 0.001, 1.0) == gridlib.VOL_MAX, "vol_factor кламп сверху")
check(gridlib.vol_factor(0.001, 1.0, 1.0) == gridlib.VOL_MIN, "vol_factor кламп снизу")

print("\n2b. Веса колен: при k=0 прежние, сумма маржи не меняется никогда")
for mult in (1.0, 1.271012, 1.6, 1.84941, 2.0):
    for levels in (2, 3, 4):
        old = [mult ** j for j in range(levels)]
        old = [x / sum(old) for x in old]
        check(gridlib.leg_weights(mult, levels, 0.02, 0.01, 0.0) == old,
              f"веса при k=0 прежние (mult={mult}, levels={levels})")
        for k in (-1.0, -0.4, 0.4, 1.0):
            w = gridlib.leg_weights(mult, levels, 0.03, 0.01, k)
            check(abs(sum(w) - 1.0) < 1e-12 and all(x > 0 for x in w),
                  f"сумма маржи == 1 при k={k} (mult={mult}, levels={levels})")
flat = gridlib.leg_weights(2.0, 3, 0.04, 0.01, -1.0)
steep = gridlib.leg_weights(2.0, 3, 0.04, 0.01, 1.0)
check(flat[0] > steep[0], "k<0 в шторм делает лестницу площе (больше в первом колене)")

print("\n3. Подвижный стоп: при k=0 не двигается, назад не ходит никогда")
check(gridlib.trail_stop(100, 130, 95, 1, 10, 0.0, 0.5) == 95, "trail k=0")
check(gridlib.trail_stop(100, 101, 95, 1, 10, 1.0, 0.5) == 95,
      "trail не включился (ход мал)")
moved = gridlib.trail_stop(100, 108, 95, 1, 10, 0.5, 0.5)
check(moved == 104.0, f"trail подтянул стоп до {moved}")
check(gridlib.trail_stop(100, 108, 106, 1, 10, 0.5, 0.5) == 106,
      "trail не откатывает стоп назад")
check(gridlib.trail_stop(100, 92, 105, -1, 10, 0.5, 0.5) == 96.0,
      "trail для шорта")
# регрессия на дыру, найденную в первом прогоне v10: без ограничения по
# закрытию бара стоп вставал туда, где рынок уже побывал внутри бара, и
# бэктест рисовал прибыль из воздуха (LTC показывал +552% при DD 2.1%)
check(gridlib.trail_stop(100, 130, 95, 1, 10, 0.9, 0.5, bound=101) == 101,
      "trail не ставит стоп выше закрытия бара (лонг)")
check(gridlib.trail_stop(100, 70, 105, -1, 10, 0.9, 0.5, bound=99) == 99,
      "trail не ставит стоп ниже закрытия бара (шорт)")
check(gridlib.trail_stop(100, 130, 102, 1, 10, 0.9, 0.5, bound=101) == 102,
      "ограничение по закрытию не откатывает уже лучший стоп назад")

print("\n4. Режим 1: последнее колено всегда ВНУТРИ стопа")
for sgn in (1, -1):
    for span in (0.30, 0.60, 0.95):
        px, stop = 100.0, 100.0 * (1 - sgn * 0.08)
        pr = gridlib.grid_prices(px, stop, sgn, 4, 0.01, mode=1, span=span)
        last = pr[-1]
        inside = (last > stop) if sgn == 1 else (last < stop)
        check(inside and len(pr) == 3,
              f"mode=1 sgn={sgn} span={span}: последнее колено {last:.4f} "
              f"внутри стопа {stop:.4f}")
        check(all((a > b) if sgn == 1 else (a < b)
                  for a, b in zip(pr, pr[1:])), f"колена по порядку span={span}")

print("\n5. Перевыставление: политика 1 не двигает колено ближе к цене")
px, sgn = 100.0, 1
cur = gridlib.grid_prices(px, 92.0, sgn, 3, 0.01, mode=1, span=0.6)
tighter = gridlib.retune_prices(px, 96.0, sgn, len(cur), 3, 0.01, 1, 0.6, 1.0,
                                1.0, cur, policy=1)
check(all(t <= c for t, c in zip(tighter, cur)), "политика 1 только дальше")
free = gridlib.retune_prices(px, 96.0, sgn, len(cur), 3, 0.01, 1, 0.6, 1.0,
                             1.0, cur, policy=2)
check(any(f > c for f, c in zip(free, cur)), "политика 2 может приблизить")
check(gridlib.retune_prices(px, 96.0, sgn, len(cur), 3, 0.01, 1, 0.6, 1.0,
                            1.0, cur, policy=0) == cur, "политика 0 не трогает")

print("\n6. Движок: боевые конфиги с OFF-генами дают тот же результат, что и "
      "геном вообще без ключей v10")
pct5 = xd.fetch_daily_pct5()
aux_builder = e8.make_aux_builder(pct5, 96)
for sym, modes in config.SYMBOL_PARAMS.items():
    p = modes["final"]
    candles = ev.fetch(sym, "15", 1150)
    aux = aux_builder(sym, candles)
    g_bare = e7.cfg_to_genome(p, "final")
    for k, v in e8.OFF8.items():
        g_bare.setdefault(k, v)
    g_off = dict(g_bare)
    g_off.update(gridlib.OFF10)
    filt = e8.make_filter8(g_bare, aux)
    old_lev, e2.LEV = e2.LEV, p["lev"]
    try:
        a = e2.run5(candles, e2.prep(candles), g_bare, entry_filter=filt)
        b = e2.run5(candles, e2.prep(candles), g_off, entry_filter=filt)
    finally:
        e2.LEV = old_lev
    check(a["balance"] == b["balance"] and a["trades"] == b["trades"]
          and a["max_dd"] == b["max_dd"],
          f"{sym}: без ключей v10 == с OFF10 "
          f"(баланс {a['balance']:.6f}, сделок {a['trades']})")

print("\n7. Инвариант движка сохраняется при ВКЛЮЧЁННОЙ адаптации")
sym = "DOGEUSDT"
candles = ev.fetch(sym, "15", 1150)[-20000:]
aux = aux_builder(sym, candles)
g = e7.cfg_to_genome(config.SYMBOL_PARAMS[sym]["final"], "final")
for k, v in e8.OFF8.items():
    g.setdefault(k, v)
for tag, extra in (
        ("mode=1", dict(grid_mode=1, grid_span=0.7, grid_spread=1.2)),
        ("atr", dict(grid_atr_k=0.6)),
        # ген весов колен ОДИН, без прочих ATR-генов: проверяем, что он
        # реально работает, а не молчит из-за неподготовленной нормы ATR
        ("веса колен", dict(grid_w_atr_k=-0.8)),
        ("trail", dict(trail_k=0.5, trail_start=0.4)),
        ("tp_atr", dict(tp_atr_k=0.5)),
        ("retune1", dict(grid_mode=1, grid_retune=1)),
        ("retune2", dict(grid_mode=1, grid_retune=2)),
        ("всё сразу", dict(grid_mode=1, grid_span=0.8, grid_spread=1.3,
                           grid_atr_k=0.5, grid_retune=1, tp_atr_k=0.4,
                           trail_k=0.5, trail_start=0.45))):
    gg = dict(g)
    gg.update(gridlib.OFF10)
    gg.update(extra)
    r = e2.run5(candles, e2.prep(candles), gg,
                entry_filter=e8.make_filter8(gg, aux))
    diff = abs((e2.START + sum(r["monthly"].values())) - r["balance"])
    check(diff < 1e-9 and math.isfinite(r["balance"]),
          f"{tag}: баланс сходится с помесячным PnL (расхождение {diff:.2e}, "
          f"сделок {r['trades']})")

print(f"\nВСЕ {OK} ПРОВЕРОК ПРОЙДЕНЫ")
