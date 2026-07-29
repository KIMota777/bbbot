# -*- coding: utf-8 -*-
"""Геометрия подвижной сетки и подвижных стоп/тейка.

Единый источник истины: этим модулем пользуются И живой бот (bot_rsi.py),
И бэктест-движок (evolution2.run5). Иначе бэктест проверял бы одну сетку,
а торговала бы другая — самая дорогая ошибка, которую тут можно совершить.

ГЛАВНОЕ СВОЙСТВО (на нём держится требование «не убыточнее текущей»):
при OFF-значениях всех новых генов каждая функция сводится РОВНО к прежней
формуле, без потери бита. Не «примерно так же», а тождественно:

    grid_prices(..., mode=0, spread=1.0, vol_k=1.0)
        -> ap = ap * (1 - sgn*step)          # прежний цикл в run5/enter
    tp_factor(..., k=0)      -> 1.0
    trail_stop(..., k=0)     -> стоп не двигается

Поэтому конфиг с выключенными генами даёт побайтово тот же список сделок,
что и до правки. Проверяется в test_gridlib.py на всех пяти боевых конфигах.

Режимы сетки (ген grid_mode):
  0 — КАК СЕЙЧАС: колена через фиксированный процент цены. Минус: шаг никак
      не связан с тем, где стоит стоп. У BTC step=0.004 при стопе в ~2% —
      сетка успевает добрать лишь малую часть пути и почти не улучшает среднюю.
  1 — ПО ПУТИ ДО СТОПА: колена расставляются долями расстояния «вход -> стоп».
      Последнее колено гарантированно ближе стопа (span <= 0.95), то есть
      сетка всегда успевает отработать до того, как позицию вынесет.
"""

VOL_MIN, VOL_MAX = 0.5, 2.5      # клампы множителя волатильности
TP_VOL_MIN, TP_VOL_MAX = 0.6, 1.8
ATR_REF_BARS = 480               # окно «своей нормы» волатильности (5 суток
                                 # на 15m). Одна константа на движок и бота:
                                 # разные окна = разные сетки при одном геноме.

# OFF-значения новых генов. Аналог OFF7/OFF8 из прежних волн отбора:
# «все новые механизмы выключены, поведение прежнее».
OFF10 = dict(grid_mode=0, grid_span=0.60, grid_spread=1.0, grid_atr_k=0.0,
             grid_w_atr_k=0.0, grid_retune=0, tp_atr_k=0.0, trail_k=0.0,
             trail_start=0.5)
MULT_MIN, MULT_MAX = 1.0, 2.0    # границы мартингейла (как в GENES эволюции)


def leg_weights(mult, levels, atr_now, atr_ref, k):
    """Доли маржи по коленам сетки, нормированные (сумма == 1).

    Адаптирует не расстояние между коленами, а их ОБЪЁМЫ. k<0 делает лестницу
    площе в шторм (меньше мартингейла там, где движение может не вернуться),
    k>0 — наоборот агрессивнее. Сумма маржи цикла не меняется ни при каком k,
    поэтому потолок риска на цикл и цена ликвидации к раскладке инвариантны:
    mused и q масштабируются одинаково.

    k=0 -> веса ровно те же, что и раньше ([mult**j] нормированные).
    """
    m = mult
    if k:
        m = min(MULT_MAX, max(MULT_MIN,
                              mult * vol_factor(atr_now, atr_ref, k, 0.7, 1.4)))
    w = [m ** j for j in range(levels)]
    total = sum(w)
    return [x / total for x in w]


def vol_factor(atr_now, atr_ref, k, lo=VOL_MIN, hi=VOL_MAX):
    """Множитель «текущая волатильность против своей нормы».

    k=0 -> ровно 1.0 (ранний выход, чтобы 0.0*inf/nan не дало NaN при битых
    данных ATR — при k=0 поведение обязано быть прежним при ЛЮБОМ входе).
    """
    if not k:
        return 1.0
    if not atr_now or not atr_ref:
        return 1.0
    return min(hi, max(lo, 1.0 + k * (atr_now / atr_ref - 1.0)))


def atr_series(candles, n):
    """ATR как доля цены, по бару. Сглаживание Уайлдера.

    Копия evolution.calc_atr_pct — намеренная, чтобы gridlib оставался листом
    без зависимостей от проекта. Совпадение до последнего бита проверяется в
    test_gridlib.py: если формулы разойдутся, тест упадёт.

    Живой бот считает свой ATR простым средним (bot_rsi.calc_atr_pct) и
    использует его для нож-фильтра — это НЕ трогаем, иначе изменилось бы
    поведение существующих конфигов. Множители волатильности считаются только
    от этого ряда, общего с движком.
    """
    atr = [None] * len(candles)
    if not candles:
        return atr
    prev_c = candles[0][4]
    val, trs = None, []
    for i in range(1, len(candles)):
        _, o, h, l, c = candles[i]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        prev_c = c
        if val is None:
            trs.append(tr)
            if len(trs) == n:
                val = sum(trs) / n
        else:
            val = (val * (n - 1) + tr) / n
        if val is not None:
            atr[i] = val / c
    return atr


def rolling_mean(values, window):
    """Скользящее среднее по префиксным суммам; None там, где данных мало.

    Нужна «своя норма» ATR: сравнивать сегодняшнюю волатильность имеет смысл
    только с недавней волатильностью этой же монеты, а не с константой.
    """
    out = [None] * len(values)
    acc, cnt, buf = 0.0, 0, []
    for i, v in enumerate(values):
        if v is None:
            buf.append(0.0)
        else:
            buf.append(v)
            acc += v
            cnt += 1
        if len(buf) > window:
            old = buf[i - window]
            if old:
                acc -= old
                cnt -= 1
        if cnt >= max(2, window // 4):
            out[i] = acc / cnt
    return out


def _fractions(n, span, spread):
    """Доли пути до стопа под колена 1..n. spread=1.0 — равномерно."""
    if spread == 1.0:
        return [span * k / n for k in range(1, n + 1)]
    w = [spread ** k for k in range(n)]
    total = sum(w)
    acc, out = 0.0, []
    for x in w:
        acc += x
        out.append(span * acc / total)
    return out


def grid_prices(px, stop, sgn, levels, step, mode=0, span=0.60, spread=1.0,
                vol_k=1.0):
    """Цены колен сетки (без первого входа). len == levels-1.

    px    — цена входа, stop — цена стопа, sgn — +1 лонг / -1 шорт.
    mode=0: ap_{k} = ap_{k-1} * (1 - sgn*step*vol_k*spread^{k-1})
    mode=1: ap_k   = px * (1 - sgn*d*f_k), d = |px-stop|/px, f_k из _fractions
    """
    n = levels - 1
    if n <= 0:
        return []
    if mode == 0:
        out, ap, s = [], px, step * vol_k
        for _ in range(n):
            ap = ap * (1 - sgn * s)
            out.append(ap)
            s = s * spread
        return out
    d = abs(px - stop) / px
    if d <= 0:
        return [px] * n
    return [px * (1 - sgn * d * f) for f in _fractions(n, span, spread)]


def retune_prices(px, stop, sgn, n_left, levels, step, mode, span, spread,
                  vol_k, current, policy):
    """Перевыставление НЕИСПОЛНЕННЫХ колен на закрытии бара.

    Смысл «подвижной» сетки в прямом смысле: стоп привязан к границе диапазона
    и с каждым баром едет, а колена, выставленные при входе, остаются висеть по
    старым ценам. policy:
      0 — не трогать (как сейчас);
      1 — пересчитать от текущего стопа, но двигать колено ТОЛЬКО дальше от
          цены (консервативно: позиция набирается позже, экспозиция меньше);
      2 — пересчитать свободно (колено может и приблизиться).
    Возвращает новый список цен той же длины, что current.
    """
    if not policy or not current:
        return current
    fresh = grid_prices(px, stop, sgn, levels, step, mode, span, spread, vol_k)
    fresh = fresh[levels - 1 - n_left:]
    if len(fresh) != len(current):
        return current
    if policy == 2:
        return fresh
    # policy 1: «дальше от цены» = ниже для лонга, выше для шорта
    return [min(a, b) if sgn == 1 else max(a, b)
            for a, b in zip(current, fresh)]


def tp_factor(atr_now, atr_ref, k):
    """Множитель к тейку от волатильности. k=0 -> 1.0 (тейк как сейчас)."""
    return vol_factor(atr_now, atr_ref, k, TP_VOL_MIN, TP_VOL_MAX)


def trail_stop(avg, best, stop, sgn, tp_dist, k, start, bound=None):
    """Подтягивающийся стоп. Возвращает новый стоп (или прежний).

    Включается, когда цена прошла долю start пути до тейка, и фиксирует долю k
    уже пройденного благоприятного хода. Стоп двигается ТОЛЬКО в сторону
    прибыли — вернуть его назад нельзя ни при каких значениях генов.

    bound — цена закрытия бара, на котором стоп переставляется. КЛЮЧЕВОЙ
    аргумент, и вот почему. Если считать ход по ХАЮ бара и не ограничивать
    результат, стоп встанет туда, где рынок уже побывал ВНУТРИ этого бара —
    до того, как стоп там появился. На следующем баре движок честно «исполнит»
    выход по этой цене, и в бэктест попадёт прибыль, которой никогда не было.
    Именно на этом первый прогон v10 выдал LTC +552% при просадке 2.1%:
    генетика мгновенно нашла эту дыру и выкрутила trail_k на максимум.
    Стоп не может быть выгоднее закрытия бара, на котором он выставлен.
    """
    if not k or tp_dist <= 0:
        return stop
    move = sgn * (best - avg)
    if move <= 0 or move < start * tp_dist:
        return stop
    cand = avg + sgn * move * k
    if bound is not None:
        cand = min(cand, bound) if sgn == 1 else max(cand, bound)
    better = (cand > stop) if sgn == 1 else (cand < stop)
    return cand if better else stop
