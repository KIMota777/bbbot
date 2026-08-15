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

print("\n3b. Безубыток: стоп нельзя ставить за уже пройденной ценой")
# регрессия на дефект, найденный 31.07: be_move ставил стоп по хаю бара, и у
# DOGE/LTC/SOL это происходило в 20-25% срабатываний — именно на таких
# несуществующих выходах держалась их бэктестовая доходность
s, done = gridlib.breakeven_stop(100.0, 92.0, 1, 101.0)
check(done and abs(s - 100.15) < 1e-9, f"лонг: закрытие выше безубытка -> перенос ({s})")
s, done = gridlib.breakeven_stop(100.0, 92.0, 1, 100.0)
check(not done and s == 92.0, "лонг: закрытие ниже безубытка -> перенос отложен")
s, done = gridlib.breakeven_stop(100.0, 108.0, -1, 99.0)
check(done and abs(s - 99.85) < 1e-9, f"шорт: закрытие ниже безубытка -> перенос ({s})")
s, done = gridlib.breakeven_stop(100.0, 108.0, -1, 100.5)
check(not done and s == 108.0, "шорт: закрытие выше безубытка -> перенос отложен")
s, done = gridlib.breakeven_stop(100.0, 100.5, 1, 101.0)
check(done and s == 100.5, "безубыток не откатывает уже лучший стоп назад")

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

print("\n6. Движок: конфиги БЕЗ ключей v10 тождественны принудительному OFF10;"
      "\n   конфиги С ключами (LTC v12) от OFF10 отличаются — гены реально работают")
pct5 = xd.fetch_daily_pct5()
aux_builder = e8.make_aux_builder(pct5, 96)
for sym, modes in config.SYMBOL_PARAMS.items():
    p = modes["final"]
    has_grid = any(k in p for k in gridlib.OFF10)
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
    same = (a["balance"] == b["balance"] and a["trades"] == b["trades"]
            and a["max_dd"] == b["max_dd"])
    if has_grid:
        check(not same,
              f"{sym}: гены v10 из конфига реально меняют поведение "
              f"(баланс {a['balance']:.4f} vs OFF {b['balance']:.4f})")
    else:
        check(same, f"{sym}: без ключей v10 == с OFF10 "
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

print("\n8. Фильтр v12 (ADX): при выключенном гейте тождественен v8")
import evolution12 as e12
import indicators
sym = "LTCUSDT"
candles = ev.fetch(sym, "15", 1150)[-30000:]
aux8 = aux_builder(sym, candles)
aux12 = dict(aux8)
aux12["adx"] = [indicators.calc_adx(candles, n) for n in e12.ADX_SET]
g = e7.cfg_to_genome(config.SYMBOL_PARAMS[sym]["final"], "final")
for k, v in e12.OFF12.items():
    g.setdefault(k, v)
a = e2.run5(candles, e2.prep(candles), g, entry_filter=e8.make_filter8(g, aux8))
b = e2.run5(candles, e2.prep(candles), g, entry_filter=e12.make_filter12(g, aux12))
check(a["balance"] == b["balance"] and a["trades"] == b["trades"],
      f"filter12(adx_gate=0) == filter8 (баланс {a['balance']:.6f}, "
      f"сделок {a['trades']})")
g_adx = dict(g, adx_gate=1, adx_max=20.0)
r = e2.run5(candles, e2.prep(candles), g_adx,
            entry_filter=e12.make_filter12(g_adx, aux12))
check(r["trades"] < a["trades"], f"ADX-гейт режет входы ({a['trades']} -> {r['trades']})")
# синтетика: тренд/пила
_tr = [(i, 100 + i, 100.6 + i, 99.8 + i, 100.5 + i) for i in range(200)]
_ch = [(i, 100, 100.6, 99.4, 100 + (0.3 if i % 2 else -0.3)) for i in range(200)]
check(indicators.calc_adx(_tr, 14)[-1] > 60, "ADX высокий на тренде")
check(indicators.calc_adx(_ch, 14)[-1] < 20, "ADX низкий на пиле")

print("\n9. Внутрисвечной порядок: колено + тейк в ОДНОЙ свече не дают"
      "\n   прибыль из воздуха, а плавающая просадка доезжает до результата")
# Дыра, ради которой этот раздел написан: в свече, где цена достаёт и колено
# сетки, и тейк, движок исполнял доливку (средняя падала), но закрывался по
# тейку, посчитанному от средней ДО доливки, — «купил на дне свечи, продал по
# цене до дна». Живой бот так не может: тейк лежит на бирже через
# set_trading_stop от ТЕКУЩЕЙ средней и после доливки сразу переезжает вниз.
# На боевых конфигах угол стоил LTC +86.0->+75.8%, а DOGE менял знак.
_SYN_G = dict(rsi_idx=2, rsi_os=30, zone_l=0.5, zone_s=0.5, window=100,
              step=0.01, levels=2, mult=1.0, tp=0.02, sweep=0.05,
              max_bars=500, cooldown=0, knife=0.0, be_move=0)


def _syn_head():
    """120 баров пилы + 5 баров ровного падения: RSI(14) пробивает 30 вниз на
    последнем баре и движок открывает лонг ровно на нём (бар 124). levels=2 и
    mult=1.0 выбраны нарочно: колена равны по МАРЖЕ, значит равны по нотионалу,
    и средняя после доливки — ровно гармоническое среднее двух цен, которое
    тест считает сам, не подглядывая во внутренности движка."""
    cs, t = [], 0
    for i in range(120):
        px = 100.0 + (0.4 if i % 2 else 0.0)
        cs.append([t, px, px + 0.2, px - 0.2, px])
        t += 900_000
    for i in range(5):
        px = 100.0 - 0.6 * (i + 1)
        cs.append([t, px + 0.3, px + 0.35, px - 0.35, px])
        t += 900_000
    return cs, t


def _syn_run(cs):
    evs = []
    r = e2.run5(cs, e2.prep(cs), _SYN_G, events=evs)
    kind = {e["type"]: e for e in evs}
    return r, evs, kind


# --- 9a. свеча-ловушка: лоу достаёт колено, хай достаёт СТАРЫЙ тейк ---
_cs, _t = _syn_head()
_cs.append([_t, 97.0, 99.5, 96.0, 98.5])          # бар 125 — ловушка
_t += 900_000
for _i in range(5):
    _cs.append([_t, 98.5, 98.6, 98.4, 98.5])
    _t += 900_000
_r, _evs, _k = _syn_run(_cs)
check(_r["trades"] == 1 and set(_k) == {"entry", "add", "close"},
      f"сценарий воспроизведён: 1 цикл, вход+колено+выход ({len(_evs)} событий)")
check(_k["add"]["t"] == _k["close"]["t"] and _k["close"]["reason"] == "tp",
      "колено и тейк случились в ОДНОЙ свече")

_px, _ap = _k["entry"]["price"], _k["add"]["price"]
_q0 = (e2.MARGIN / 2) * e2.LEV / _px
_q1 = (e2.MARGIN / 2) * e2.LEV / _ap
_q = _q0 + _q1
_avg = (_px * _q0 + _ap * _q1) / _q          # == гармоническое среднее
_tp_new = _avg * (1 + _SYN_G["tp"])          # тейк от НОВОЙ средней
_tp_old = _px * (1 + _SYN_G["tp"])           # тейк от средней ДО доливки
check(abs(_k["close"]["price"] - _tp_new) < 1e-9,
      f"выход по тейку от НОВОЙ средней {_tp_new:.6f}, а не от старой "
      f"{_tp_old:.6f}")
check(_k["close"]["price"] < _tp_old - 1e-9,
      f"цена выхода ниже старой на {_tp_old - _tp_new:.4f} — воздух убран")

# Модель выхода по тейку зафиксирована здесь ЯВНО, а не через флаг
# e2.TP_MARKET_EXIT. Раньше тест считал ожидаемый PnL «как решит флаг» и
# поэтому подстраивался под любое его значение: откат правки (мейкерский тейк
# без слиппеджа) проходил все проверки. Тейк уходит на биржу через
# set_trading_stop — это условный МАРКЕТ по триггеру: цена хуже уровня на
# величину проскальзывания, комиссия тейкера.
_fees = (_q0 * _px * e2.TAKER + _q1 * _ap * e2.MAKER
         + _q0 * _cs[125][4] * e2.FUND_8H / e2.BARS_8H)
_exit_px = _tp_new * (1 - e2.SLIP)
_fee_out = _q * _exit_px * e2.TAKER
_pnl_fair = (_exit_px - _avg) * _q - _fee_out - _fees
# мейкерская модель (как было до правки): лимитка ровно по тейку, без
# слиппеджа и с комиссией мейкера
_pnl_maker = (_tp_new - _avg) * _q - _q * _tp_new * e2.MAKER - _fees
_pnl_air = (_tp_old - _avg) * _q - _q * _tp_old * e2.MAKER - _fees
check(abs((_r["balance"] - e2.START) - _pnl_fair) < 1e-9,
      f"PnL цикла считается по консервативной модели ({_pnl_fair:+.4f})")
check(e2.TP_MARKET_EXIT is True,
      "тейк помечен рыночным выходом (TP_MARKET_EXIT)")
check(_pnl_fair < _pnl_maker - 1e-9,
      f"мейкерская модель тейка дарила бы {_pnl_maker:+.4f} вместо "
      f"{_pnl_fair:+.4f} — {(_pnl_maker - _pnl_fair) / _q / _tp_new * 1e4:.2f} "
      f"б.п. оборота из воздуха на каждой прибыльной сделке")
check(_pnl_fair < _pnl_air - 1e-9,
      f"старый порядок дал бы {_pnl_air:+.4f} вместо {_pnl_fair:+.4f} — "
      f"{(1 - _pnl_fair / _pnl_air) * 100:.0f}% прибыли цикла было воздухом")

# --- 9b. контроль: без доливки в свече тейк остаётся прежним ---
# Если бы пересчёт срабатывал всегда, он ломал бы обычные циклы. Ловушку
# поднимаем так, чтобы лоу НЕ доставал колено — тейк обязан остаться старым.
_cs2, _t2 = _syn_head()
_cs2.append([_t2, 97.0, 99.5, 96.5, 98.5])        # лоу выше колена 96.0588
_t2 += 900_000
for _i in range(5):
    _cs2.append([_t2, 98.5, 98.6, 98.4, 98.5])
    _t2 += 900_000
_r2, _evs2, _k2 = _syn_run(_cs2)
check("add" not in _k2 and _k2["close"]["reason"] == "tp",
      "колено не задето — цикл закрылся тейком без доливки")
check(abs(_k2["close"]["price"] - _tp_old) < 1e-9,
      f"без доливки тейк прежний ({_tp_old:.6f}) — пересчёт не трогает "
      f"обычные циклы")

# --- 9d. срабатывание тейка НЕ ослабляется пересчётом ---
# Обратная дыра, куда легко провалиться при починке: если проверять
# срабатывание по пересчитанному (более близкому) тейку, движок начинает
# закрывать в плюс циклы, которые в жизни выносило стопом. Замер на боевых
# конфигах: DOGE +116.2% вместо −38.1%, SOL +13.8% вместо −79.6%, а «тейков в
# свече с доливкой» становится 106 вместо 11. Хай ловушки поднимаем ровно
# между новым и старым тейком: закрытия быть НЕ должно.
#
# Утверждение сделано ТОЧНЫМ: раньше проверялось только reason != "tp", и это
# выполнялось «само» — позиция просто доживала до конца данных. Теперь после
# ловушки цена уходит под стоп, и цикл обязан закрыться именно СТОПОМ, на
# известном баре и по известной цене. Ошибочная реализация (ловить тейк по
# пересчитанному уровню) закрыла бы цикл в плюс ещё на баре ловушки.
_cs4, _t4 = _syn_head()
_mid = (_tp_new + _tp_old) / 2
_stop4 = min(c[3] for c in _syn_head()[0][25:]) * (1 - _SYN_G["sweep"])
_cs4.append([_t4, 97.0, _mid, 96.0, 97.5])       # бар 125 — ловушка
_t4 += 900_000
_trap_t = _t4 - 900_000
for _px4 in (95.0, 93.0, _stop4 - 0.5):          # уход под стоп
    _cs4.append([_t4, _px4 + 0.2, _px4 + 0.25, _px4 - 0.2, _px4])
    _t4 += 900_000
_stop_t = _t4 - 900_000
for _i in range(3):
    _cs4.append([_t4, 91.0, 91.1, 90.9, 91.0])
    _t4 += 900_000
_r4, _evs4, _k4 = _syn_run(_cs4)
check("add" in _k4 and _k4["add"]["t"] == _trap_t,
      "колено на баре ловушки исполнилось (средняя упала, тейк переехал вниз)")
check(_k4["close"]["reason"] == "stop" and _k4["close"]["t"] == _stop_t,
      f"хай между новым и старым тейком ({_tp_new:.4f} < {_mid:.4f} < "
      f"{_tp_old:.4f}) тейком НЕ считается: цикл дожил и вынесен стопом на "
      f"баре ухода под {_stop4:.4f} (выход «{_k4['close']['reason']}»)")
check(abs(_k4["close"]["price"] - _stop4) < 1e-9 and _r4["balance"] < e2.START,
      f"выход ровно по цене стопа {_stop4:.4f}, цикл убыточный "
      f"({_r4['balance'] - e2.START:+.4f})")

# --- 9c. плавающая просадка: цикл ушёл в минус, но вышел в плюс ---
# Ровно тот случай, который старая метрика не видела вообще: max_dd считает
# только закрытые сделки, и прибыльный цикл давал по ней 0% просадки, сколько
# бы недель он ни висел в минусе. Плечо выбиралось именно по этой метрике.
_cs3, _t3 = _syn_head()
for _px3 in (96.0, 95.0, 94.0, 93.0, 93.0, 94.0, 95.5, 97.0):
    _cs3.append([_t3, _px3, _px3 + 0.2, _px3 - 0.2, _px3])
    _t3 += 900_000
_cs3.append([_t3, 97.0, 99.0, 96.9, 98.9])
_t3 += 900_000
for _i in range(3):
    _cs3.append([_t3, 98.9, 99.0, 98.8, 98.9])
    _t3 += 900_000
_r3, _evs3, _k3 = _syn_run(_cs3)
check(_r3["balance"] > e2.START and _r3["max_dd"] == 0.0,
      f"цикл закрыт в плюс, закрытая просадка нулевая "
      f"(баланс {_r3['balance']:.4f})")
check(_r3["max_dd_mtm"] > 0.04,
      f"плавающая просадка это видит: {_r3['max_dd_mtm'] * 100:.2f}%")
check("max_dd_mtm" in _r3 and "max_dd_mtm" in _r,
      "max_dd_mtm есть в результате прогона (обе ветки return)")
check(_r3["max_dd_float"] == _r3["max_dd_mtm"],
      "старое имя max_dd_float оставлено алиасом (его читают evolution4/honest_eval)")
check(_k3["close"]["worst"] < min(0.0, _k3["close"]["pnl"]),
      f"в событие close записана худшая точка цикла "
      f"({_k3['close']['worst']:+.4f} при итоге {_k3['close']['pnl']:+.4f})")

# --- 9e. бар ЗАКРЫТИЯ тоже переоценивается ---
# Занижение, найденное на втором круге: переоценка делалась только для циклов,
# доживших до конца бара, поэтому ход внутри бара, на котором цикл закрылся,
# не учитывался вовсе. Классика — тейк на баре, у которого лоу далеко внизу:
# на экране был глубокий минус, а в метрику попадала одна прибыль по закрытию.
# Бар-ловушка та же, что в 9a, но с лоу 93.0 вместо 96.0: вход, колено, тейк и
# комиссии остаются прежними до бита, меняется ТОЛЬКО глубина хода внутри бара.
_cs5, _t5 = _syn_head()
_cs5.append([_t5, 97.0, 99.5, 93.0, 98.5])
_t5 += 900_000
for _i in range(5):
    _cs5.append([_t5, 98.5, 98.6, 98.4, 98.5])
    _t5 += 900_000
_r5, _evs5, _k5 = _syn_run(_cs5)
_worst5 = (93.0 - _avg) * _q - _fees          # минус на экране в лоу свечи
check(_k5["close"]["reason"] == "tp"
      and abs((_r5["balance"] - e2.START) - _pnl_fair) < 1e-9,
      f"тот же цикл с тем же итогом (+{_r5['balance'] - e2.START:.4f})")
check(abs(_k5["close"]["worst"] - round(_worst5, 4)) < 1e-4,
      f"худшая точка цикла взята из бара ЗАКРЫТИЯ: {_worst5:+.4f} "
      f"(в событии {_k5['close']['worst']:+.4f})")
check(_k5["close"]["worst"] < _k["close"]["worst"] - 0.5,
      f"глубже, чем у той же сделки с мелким лоу ({_k['close']['worst']:+.4f})")
check(abs(_r5["max_dd_mtm"] - (-_worst5) / e2.START) < 1e-9,
      f"и она доехала до просадки: {_r5['max_dd_mtm'] * 100:.2f}% "
      f"против {_r['max_dd_mtm'] * 100:.2f}% у мелкого лоу")

# --- 9f. ликвидация: порог не позже биржевого, теряется ВСЯ маржа ---
print("\n9f. Ликвидация: порог не позже биржевого, теряется ВСЯ маржа цикла")
for _lev in (5, 10, 15):
    for _sgn in (1, -1):
        _a, _m = 100.0, 5.0
        _qq = _m * _lev / _a
        _ours = e2.liq_price(_a, _m, _qq, _sgn)
        # Bybit: LP = вход*(1 - IM + MM) для лонга, вход*(1 + IM - MM) для
        # шорта; IM = 1/плечо, MM = maintenance margin rate
        _byb = _a * (1 - _sgn * (1.0 / _lev - e2.MMR))
        _bank = _a * (1 - _sgn / _lev)        # банкротная цена (MM не учтён)
        _side = "лонг" if _sgn == 1 else "шорт"
        check((_ours >= _byb - 1e-12) if _sgn == 1 else (_ours <= _byb + 1e-12),
              f"x{_lev} {_side}: наша {_ours:.4f} наступает не позже биржевой "
              f"{_byb:.4f}")
        check((_ours > _bank + 1e-12) if _sgn == 1 else (_ours < _bank - 1e-12),
              f"x{_lev} {_side}: и раньше банкротной {_bank:.4f} — "
              f"maintenance margin учтён")

# сценарий на движке: одно колено (levels=1), плечо x25, обвал ниже
# ликвидации, но ВЫШЕ стопа — значит закрывать обязана ликвидация
_LIQ_G = dict(_SYN_G, levels=1)
_cs6, _t6 = _syn_head()
_cs6.append([_t6, 96.5, 96.6, 92.5, 93.0])
_t6 += 900_000
for _i in range(3):
    _cs6.append([_t6, 93.0, 93.1, 92.9, 93.0])
    _t6 += 900_000
_old_lev, e2.LEV = e2.LEV, 25
try:
    _evs6 = []
    _r6 = e2.run5(_cs6, e2.prep(_cs6), _LIQ_G, events=_evs6)
    _k6 = {e["type"]: e for e in _evs6}
    # тот же сценарий на маленьком счёте: одной ликвидации хватает на слив
    _old_start, e2.START = e2.START, 6.0
    try:
        _r7 = e2.run5(_cs6, e2.prep(_cs6), _LIQ_G)
    finally:
        e2.START = _old_start
finally:
    e2.LEV = _old_lev
_px6 = _k6["entry"]["price"]
_q6 = e2.MARGIN * 25 / _px6
_fees6 = _q6 * _px6 * e2.TAKER + _q6 * 93.0 * e2.FUND_8H / e2.BARS_8H
_bank6 = _px6 * (1 - 1 / 25)
check(_k6["close"]["reason"] == "liq" and _k6["close"]["liq"] is True
      and "add" not in _k6,
      f"обвал закрыт ликвидацией, а не стопом (цена {_k6['close']['price']:.4f})")
check(_k6["close"]["price"] > _bank6 + 1e-9,
      f"ликвидация раньше банкротной цены: {_k6['close']['price']:.4f} > "
      f"{_bank6:.4f}")
check(abs((_r6["balance"] - e2.START) - (-e2.MARGIN - _fees6)) < 1e-9,
      f"теряется ВСЯ маржа цикла плюс уже уплаченные комиссии "
      f"({_r6['balance'] - e2.START:+.4f}), остаток биржа съедает "
      f"ликвидационной комиссией")
check(_r6["ruined"] is False and _r6["ruined_trade"] is None,
      f"счёт в $20 такую ликвидацию переживает (баланс {_r6['balance']:.4f})")
check(_r7["ruined"] is True and _r7["ruined_trade"] == 1
      and _r7["ruined_ts"] == _cs6[125][0],
      f"а счёт в $6 слит: прогон оборван на {_r7['ruined_trade']}-й сделке "
      f"(эти поля и едут на сайт как признак слива)")
check(_r7["max_dd_float"] == _r7["max_dd_mtm"],
      "в оборванном прогоне алиас max_dd_float тоже на месте")

# --- 9g. вход — тоже точка кривой: слиппедж и комиссия видны сразу ---
print("\n9g. Стоимость входа доезжает до плавающей просадки")
# Правка, которую этот раздел закрепляет: в run5 после открытия цикла стоит
# mark(balance + pos["worst"]). Без неё минус, который владелец видит на экране
# В ТУ ЖЕ СЕКУНДУ (проскальзывание входа плюс комиссия тейкера), не попадал в
# max_dd_mtm вообще: следующая переоценка делается только в конце СЛЕДУЮЩЕГО
# бара, и если цена сразу пошла в нужную сторону, отрицательных точек у цикла
# не оставалось ни одной. Сценарий подобран так, что стоимость входа —
# ЕДИНСТВЕННЫЙ минус за весь прогон: сразу после входа бар уходит вверх и
# закрывает цикл тейком, а его лоу выше средней, поэтому переоценка на баре
# закрытия положительна. Значит откат правки даёт max_dd_mtm ровно 0.0.
_cs7, _t7 = _syn_head()
_cs7.append([_t7, 97.5, 99.5, 97.4, 99.0])        # хай берёт тейк, лоу выше средней
_t7 += 900_000
for _i in range(5):
    _cs7.append([_t7, 99.0, 99.1, 98.9, 99.0])
    _t7 += 900_000
_r8, _evs8, _k8 = _syn_run(_cs7)
check(_r8["trades"] == 1 and _k8["close"]["reason"] == "tp" and "add" not in _k8,
      "сценарий: один цикл, вход и сразу тейк, колено не задето")
_px8 = _k8["entry"]["price"]
_q8 = (e2.MARGIN / 2) * e2.LEV / _px8
# минус в момент входа: цена входа хуже закрытия на слиппедж, плюс тейкер
_cost8 = (_cs7[124][4] - _px8) * _q8 - _q8 * _px8 * e2.TAKER
check(_cost8 < 0 and abs(_k8["close"]["worst"] - round(_cost8, 4)) < 1e-4,
      f"худшая точка цикла = стоимость входа {_cost8:+.4f} "
      f"(в событии {_k8['close']['worst']:+.4f}) — значит все последующие "
      f"переоценки были в плюс")
check(abs(_r8["max_dd_mtm"] - (-_cost8) / e2.START) < 1e-12,
      f"и она доехала до max_dd_mtm: {_r8['max_dd_mtm'] * 100:.4f}% "
      f"(без mark() после входа тут был бы ровно 0)")
check(_r8["max_dd_mtm"] > 0 and _r8["max_dd"] == 0.0,
      f"по закрытым сделкам просадки нет ({_r8['max_dd']}), а по плавающей "
      f"есть — вход стоит денег до всякого хода цены")

# --- 9h. ликвидационное дно переоценки: минус не больше маржи цикла ---
print("\n9h. Плавающий минус ограничен снизу маржой цикла (ликвидационное дно)")
# Ограничение снизу в float_mark выглядит мёртвым кодом: счётчик срабатываний
# на боевых прогонах (SOL x5, DOGE x10, DOGE x25) — ноль. Так и должно быть, и
# причина ровно одна: ликвидация проверяется РАНЬШЕ любой переоценки. Пока цикл
# жив, бар не доставал ни стопа, ни цены ликвидации, значит и до банкротной
# цены (где минус равен всей марже) не доходил — дно недостижимо.
#
# Достижимый путь ровно один, и он воспроизведён ниже: цикл, открытый на
# ПОСЛЕДНЕЙ свече данных. Его закрывает принудительный close_pos после цикла по
# барам, и переоценка идёт по лоу ТОГО ЖЕ бара — единственного, который
# ликвидационная проверка не смотрела (на момент входа он уже прошёл). Без
# ограничения снизу на экране рисуется минус БОЛЬШЕ всей изолированной маржи
# цикла — то, чего в изолированной марже не бывает: биржа закрыла бы позицию
# раньше. Поэтому дно остаётся и проверяется здесь.
_LIQF_G = dict(_SYN_G, levels=1,   # одно колено: вся маржа цикла в одном месте
               zone_l=0.95)        # глубокий лоу поднимает zpos, ослабляем зону
_cs9, _t9 = _syn_head()
_lo9 = 90.0
_cs9[-1] = [_cs9[-1][0], 97.3, 97.35, _lo9, 97.0]   # вход на последнем баре
_old_lev9, e2.LEV = e2.LEV, 25       # чем выше плечо, тем ближе банкротная цена
try:
    _evs9 = []
    _r9 = e2.run5(_cs9, e2.prep(_cs9), _LIQF_G, events=_evs9)
finally:
    e2.LEV = _old_lev9
_k9 = {e["type"]: e for e in _evs9}
check(_r9["trades"] == 1 and _k9["close"]["reason"] == "?"
      and _k9["close"]["t"] == _cs9[-1][0],
      "сценарий: цикл открыт на последней свече и закрыт принудительно на ней же")
_px9 = _k9["entry"]["price"]
_q9 = e2.MARGIN * 25 / _px9
_fees9 = _q9 * _px9 * e2.TAKER
_raw9 = (_lo9 - _px9) * _q9 - _fees9          # переоценка БЕЗ дна
_floor9 = -e2.MARGIN * e2.LIQ_LOSS - _fees9   # вся маржа цикла плюс комиссии
check(_raw9 < _floor9 - 1.0,
      f"сценарий действительно упирается в дно: без него минус был бы "
      f"{_raw9:+.4f} при марже цикла ${e2.MARGIN:.2f}")
check(abs(_k9["close"]["worst"] - round(_floor9, 4)) < 1e-4,
      f"минус ограничен ликвидационным убытком {_floor9:+.4f} "
      f"(в событии {_k9['close']['worst']:+.4f}), а не {_raw9:+.4f}")
check(abs(_r9["max_dd_mtm"] - (-_floor9) / e2.START) < 1e-12,
      f"и в просадку едет он же: {_r9['max_dd_mtm'] * 100:.2f}% "
      f"(без дна вышло бы {(-_raw9) / e2.START * 100:.2f}% — минус больше, чем "
      f"вся маржа цикла)")

print(f"\nВСЕ {OK} ПРОВЕРОК ПРОЙДЕНЫ")
