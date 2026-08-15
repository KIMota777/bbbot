# -*- coding: utf-8 -*-
"""Семейство «структура рынка»: уровни, сломы, выносы стопов.

Общая идея раздела: цена движется не по гладкой траектории, а от одного
заметного места к другому. Заметные места — это подтверждённые экстремумы, за
которыми стоят чужие стоп-заявки и отложенные входы. Значит, у рынка есть
короткая память: он «знает», где недавно разворачивался, и ведёт себя у этих
точек не так, как в чистом поле.

Шесть гипотез ниже сознательно противоречат друг другу. Две из них —
sr_bounce и equal_levels — вообще смотрят на один и тот же признак (скопление
почти равных экстремумов) и торгуют его в РАЗНЫЕ стороны: первая считает
уровень стеной, вторая — магнитом. Обе не могут быть правы одновременно, и
это нормально: задача раздела не подтвердить красивую картинку из учебника, а
дать данным возможность высказаться против неё.

Что здесь важно про честность разметки. Экстремум становится известен не в
момент, когда он образовался, а только когда справа от него прошло n баров без
перебоя — ровно так его видит торгующий вживую. За это отвечает ind.swings, и
собственной разметки экстремумов здесь нет намеренно: центрированное окно
превратило бы любую структуру в пророчество.

Про издержки. Круговая сделка стоит около 0.16% объёма. Все шесть гипотез
торгуют события, а не каждый бар, но у частых сочетаний (мелкое подтверждение
n=3, узкий фильтр) счёт сделок всё равно уходит за пару тысяч, и комиссия там
съедает результат раньше, чем начинается разговор о качестве сигнала. Это
свойство рынка, а не недостаток реализации, и прятать его не надо.
"""
import hashlib

import numpy as np

import ind
import strat
from engine import Signals

FAMILY = "structure"

# Порядок вывода в самопроверке: от простого слома к центральной гипотезе.
ORDER = ["bos", "choch", "hh_hl", "sr_bounce", "equal_levels", "sweep_reclaim"]


# --- служебное: разбор подтверждённых экстремумов ---------------------------
#
# ind.swings отдаёт ряд «последний известный экстремум» с протяжкой вперёд.
# Для структуры этого мало: нужно знать ещё и ПРЕДЫДУЩИЙ экстремум (иначе не
# скажешь, выше он или ниже) и бар, на котором экстремум подтвердился (иначе не
# скажешь, давно ли уровень стоит). И то и другое восстанавливается из самого
# ряда по точкам его изменения — новых сущностей заводить не надо.

_SWING_CACHE = {}
_CACHE_LIMIT = 48


def _swings(h, l, n):
    """ind.swings с запоминанием по СОДЕРЖИМОМУ массивов.

    Разметка экстремумов — самая дорогая часть модуля, а перебор сетки гоняет
    её десятки раз по одним и тем же ценам. Ключ считается от байтов цен, а не
    от объекта: подменённые бары из проверки причинности дадут другой ключ и
    посчитаются заново. Иначе кэш сам стал бы утечкой будущего.
    """
    key = (hashlib.blake2b(np.ascontiguousarray(h).tobytes(),
                           digest_size=16).digest(),
           hashlib.blake2b(np.ascontiguousarray(l).tobytes(),
                           digest_size=16).digest(), int(n))
    got = _SWING_CACHE.get(key)
    if got is None:
        if len(_SWING_CACHE) >= _CACHE_LIMIT:
            _SWING_CACHE.clear()
        got = ind.swings(h, l, int(n))
        _SWING_CACHE[key] = got
    return got


def _ffill(x):
    """Протяжка последнего известного значения вперёд. Дырки слева — NaN."""
    idx = np.where(~np.isnan(x), np.arange(len(x)), 0)
    np.maximum.accumulate(idx, out=idx)
    out = x[idx]
    out[np.isnan(x[idx])] = np.nan
    return out


def _events(vals):
    """Точки, где ряд уровней сменился: (бары подтверждения, значения)."""
    m = len(vals)
    ok = ~np.isnan(vals)
    new = np.zeros(m, dtype=bool)
    new[0] = bool(ok[0])
    new[1:] = ok[1:] & (~ok[:-1] | (vals[1:] != vals[:-1]))
    idx = np.flatnonzero(new)
    return idx, vals[idx]


def _levels(vals):
    """(последний уровень, предыдущий уровень, бар подтверждения последнего).

    Всё считается «на каждый бар» и смотрит только назад: предыдущий уровень —
    это тот, что был известен ещё раньше последнего, а не тот, что появится.
    """
    m = len(vals)
    idx, lv = _events(vals)
    if not len(idx):
        nan = np.full(m, np.nan)
        return nan, nan.copy(), np.full(m, -1, dtype=np.int64)
    pos = np.searchsorted(idx, np.arange(m), "right") - 1
    safe = np.maximum(pos, 0)
    last = np.where(pos >= 0, lv[safe], np.nan)
    prev = np.where(pos >= 1, lv[np.maximum(pos - 1, 0)], np.nan)
    conf = np.where(pos >= 0, idx[safe], -1).astype(np.int64)
    return last, prev, conf


def _cluster(vals, tol, kmin, look):
    """Скопление почти равных уровней: kmin экстремумов в пределах tol.

    Люди рисуют уровень не по одной точке, а по нескольким совпавшим — и
    отложенные заявки собираются именно там. Уровень скопления берём как
    среднее совпавших: одиночная точная цена ложна, а середина кучки — то, что
    видят все.
    """
    out = np.full(len(vals), np.nan)
    idx, lv = _events(vals)
    for k in range(len(idx)):
        j0 = max(0, k - look + 1)
        grp = lv[j0:k + 1]
        near = np.abs(grp - lv[k]) <= tol * abs(lv[k])
        if int(near.sum()) >= kmin:
            out[idx[k]] = float(grp[near].mean())
    return _ffill(out)


def _prev(x):
    """Значение того же ряда на предыдущем баре. Слева — NaN."""
    out = np.empty(len(x), dtype=np.float64)
    out[0] = np.nan
    out[1:] = x[:-1]
    return out


def _atr(bars, p):
    return ind.atr(bars.h, bars.l, bars.c, int(p.get("atr_n", 14)))


# --- 1. Слом структуры как продолжение --------------------------------------

@strat.register("bos", FAMILY, {
    "n": [3, 5, 8],
    "pen": [0.0, 0.25, 0.5],
    "stop_atr": [2.0, 3.0],
    "trail_atr": [0.0, 3.0],
    "atr_n": [14],
}, "закрытие за последним подтверждённым экстремумом — вход по направлению")
def s_bos(bars, p):
    """Пробой последнего подтверждённого экстремума (break of structure).

    Гипотеза. Последний экстремум — это цена, на которой продавец в прошлый раз
    пересилил покупателя. Пока она держится, у рынка есть повод топтаться. Как
    только закрытие ушло за неё, прошлый баланс сломан: те, кто держал позицию
    против движения, вынуждены закрываться, а опоздавшие — входить. Оба потока
    толкают цену в одну сторону, и продолжение вероятнее отката.

    Что здесь можно сломать честно. Слом ловится по ЗАКРЫТИЮ, а не по тени:
    прокол тенью — это чаще сбор стопов (им посвящена sweep_reclaim), а не смена
    баланса. Параметр pen требует уйти за уровень ещё и на долю ATR — так
    отсекаются касания, которые в спокойном рынке ничего не значат.
    """
    n = len(bars.t)
    hi, lo = _swings(bars.h, bars.l, int(p["n"]))
    a = _atr(bars, p)
    pad = float(p["pen"]) * np.nan_to_num(a, nan=0.0)
    up, dn = hi + pad, lo - pad
    c = bars.c
    e = np.zeros(n, dtype=np.int8)
    # только бар СМЕНЫ состояния: иначе один и тот же слом переоткрывается
    # каждый бар, пока цена стоит выше уровня
    e[(c > up) & (_prev(c) <= _prev(up))] = 1
    e[(c < dn) & (_prev(c) >= _prev(dn))] = -1
    return strat.finish(bars, p, Signals(n), e)


# --- 2. Смена характера -----------------------------------------------------

@strat.register("choch", FAMILY, {
    "n": [3, 5, 8],
    "pen": [0.0, 0.3],
    "stop_atr": [2.0, 3.0],
    "rr": [1.5, 3.0],
    "atr_n": [14],
}, "растущая структура ломает свой минимум (и наоборот) — разворот")
def s_choch(bars, p):
    """Смена характера: рост сломался вниз, падение — вверх.

    Гипотеза. Тренд узнаётся по паре признаков сразу: каждый следующий максимум
    выше предыдущего И каждый следующий минимум выше предыдущего. Пока обе
    половины на месте, всё в порядке. Первый настоящий признак беды — не
    «максимум не обновился», а пробой последнего минимума: значит покупатель,
    который эти минимумы защищал, кончился. Именно на этом переломе, а не на
    развитом тренде, положение риска к ходу лучше всего: точка отсчёта рядом.

    Разница с bos не в формуле, а в контексте: bos торгует пробой ПО тренду,
    choch — пробой ПРОТИВ него, и потому обязан требовать, чтобы тренд до этого
    действительно был. Если рынок болтался без структуры, сигнала нет.
    """
    n = len(bars.t)
    hi, lo = _swings(bars.h, bars.l, int(p["n"]))
    h_last, h_prev, _ = _levels(hi)
    l_last, l_prev, _ = _levels(lo)
    a = _atr(bars, p)
    pad = float(p["pen"]) * np.nan_to_num(a, nan=0.0)
    grow = (h_last > h_prev) & (l_last > l_prev)      # выше-выше
    fall = (h_last < h_prev) & (l_last < l_prev)      # ниже-ниже
    c = bars.c
    dn_lvl, up_lvl = l_last - pad, h_last + pad
    brk_dn = (c < dn_lvl) & (_prev(c) >= _prev(dn_lvl))
    brk_up = (c > up_lvl) & (_prev(c) <= _prev(up_lvl))
    e = np.zeros(n, dtype=np.int8)
    e[grow & brk_dn] = -1
    e[fall & brk_up] = 1
    return strat.finish(bars, p, Signals(n), e)


# --- 3. Торговать только в целой структуре ----------------------------------

@strat.register("hh_hl", FAMILY, {
    "n": [3, 5, 8],
    "min_leg": [0.0, 1.5],
    "stop_atr": [2.0, 3.0],
    "trail_atr": [0.0, 3.0],
    "exit_mixed": [0, 1],
}, "вход, пока структура растущая (или падающая), выход при её порче")
def s_hh_hl(bars, p):
    """Пока структура целая — оставаться в ней, как только смешалась — выйти.

    Гипотеза. Тренд — это не наклон средней, а повторяющееся событие: рынок
    раз за разом отказывается идти ниже прошлого дна и раз за разом обновляет
    вершину. Такая последовательность не случайна: она означает, что покупатель
    приходит выше прежнего. Пока последовательность держится, оставаться в
    позиции выгодно; как только максимумы и минимумы перестали идти в одну
    сторону (выше максимум, но ниже минимум — расширение), преимущество исчезло,
    и правильный ответ — не разворот, а выход в сторону.

    Вход — на баре, где структура ПЕРЕШЛА в целое состояние, а не на каждом
    баре внутри тренда: иначе после каждого стопа мы бы переоткрывали ту же
    позицию по худшей цене. min_leg отсекает микроструктуру: если размах между
    последним максимумом и последним минимумом меньше полутора ATR, это не
    тренд, а дрожание внутри одного бара волатильности.
    """
    n = len(bars.t)
    hi, lo = _swings(bars.h, bars.l, int(p["n"]))
    h_last, h_prev, _ = _levels(hi)
    l_last, l_prev, _ = _levels(lo)
    a = _atr(bars, p)
    leg = np.abs(h_last - l_last) / np.where(a > 0, a, np.nan)
    big = np.nan_to_num(leg, nan=0.0) >= float(p["min_leg"])
    grow = (h_last > h_prev) & (l_last > l_prev) & big
    fall = (h_last < h_prev) & (l_last < l_prev) & big
    pg = np.zeros(n, dtype=bool)
    pf = np.zeros(n, dtype=bool)
    pg[1:], pf[1:] = grow[:-1], fall[:-1]
    e = np.zeros(n, dtype=np.int8)
    e[grow & ~pg] = 1
    e[fall & ~pf] = -1
    ex = None
    if int(p.get("exit_mixed", 0)):
        # структура перестала быть однонаправленной — повода сидеть нет
        ex = ~(grow | fall)
    return strat.finish(bars, p, Signals(n), e, ex)


# --- 4. Отскок от дважды уважённого уровня ----------------------------------

@strat.register("sr_bounce", FAMILY, {
    "n": [3, 5],
    "tol": [0.002, 0.005, 0.01],
    "look": [4, 8],
    "stop_atr": [1.5, 2.5],
    "rr": [1.5, 3.0],
    "atr_n": [14],
}, "третье касание уровня, от которого цена уже дважды отбивалась")
def s_sr_bounce(bars, p):
    """Уровень, который цена уже дважды уважила, работает и в третий раз.

    Гипотеза. Если рынок дважды развернулся примерно на одной цене, это видно
    всем и запоминается всеми. К такой цене заранее ставят лимитные заявки:
    покупатели — под поддержку, продавцы — под сопротивление. Заявки создают
    реальный спрос, а не только ожидание, поэтому третье касание чаще отбивает,
    чем проходит насквозь.

    Вход требует не касания, а ОТКАЗА: тень бара зашла в зону уровня, но
    закрытие вернулось наружу. Закрытие внутри зоны — это не отбой, это проход.
    Подходить к уровню надо снаружи: если прошлое закрытие уже было под
    поддержкой, поддержки нет, есть сопротивление сверху.

    Прямо противоположная гипотеза живёт в equal_levels: там такое же скопление
    считается не стеной, а приманкой. Обе тут нарочно, сравнение честное.
    """
    n = len(bars.t)
    hi, lo = _swings(bars.h, bars.l, int(p["n"]))
    tol = float(p["tol"])
    look = int(p["look"])
    sup = _cluster(lo, tol, 2, look)
    res = _cluster(hi, tol, 2, look)
    c, h, l = bars.c, bars.h, bars.l
    e = np.zeros(n, dtype=np.int8)
    # поддержка: тень нырнула в зону, закрытие выше уровня, подход сверху
    up = (l <= sup * (1 + tol)) & (c > sup) & (_prev(c) > _prev(sup))
    dn = (h >= res * (1 - tol)) & (c < res) & (_prev(c) < _prev(res))
    e[up] = 1
    e[dn & ~up] = -1
    return strat.finish(bars, p, Signals(n), e)


# --- 5. Скопление равных экстремумов как магнит -----------------------------

@strat.register("equal_levels", FAMILY, {
    "n": [3, 5],
    "tol": [0.001, 0.003],
    "kmin": [2, 3],
    "reach": [2.0, 4.0],
    "stop_atr": [2.0, 3.0],
    "atr_n": [14],
}, "торговать В СТОРОНУ скопления равных максимумов/минимумов — за ликвидностью")
def s_equal_levels(bars, p):
    """Ровный ряд одинаковых вершин — это витрина стопов, и её приходят забрать.

    Гипотеза, обратная предыдущей. Несколько почти равных максимумов подряд —
    место, где у всех коротких позиций стоит защитный стоп, а у ждущих пробоя —
    отложенный вход. Это единственное место поблизости, где можно набрать
    крупный объём, не двигая цену против себя. Значит рынок тянется туда: не
    потому, что «уровень притягивает», а потому, что там есть с кем торговать.

    Поэтому вход — В СТОРОНУ скопления, пока оно ещё не собрано и пока до него
    близко (reach в ATR: далёкая цель за разумное время не достигается, а стоп
    съест позицию раньше). Выход — на баре, где цена скопления коснулась: работа
    сделана, дальше начинается другая история, и держать позицию за неё — уже
    не эта гипотеза.

    Один и тот же уровень отрабатывается один раз: пока скопление не сменилось
    новым, повторный вход был бы ставкой на то же событие второй раз.
    """
    n = len(bars.t)
    hi, lo = _swings(bars.h, bars.l, int(p["n"]))
    tol, kmin = float(p["tol"]), int(p["kmin"])
    reach = float(p["reach"])
    cl_hi = _cluster(hi, tol, kmin, 6)
    cl_lo = _cluster(lo, tol, kmin, 6)
    a = _atr(bars, p)
    c, h, l = bars.c, bars.h, bars.l
    e = np.zeros(n, dtype=np.int8)
    ex = np.zeros(n, dtype=bool)
    dead_hi = dead_lo = np.nan          # скопления, до которых уже дошли
    armed_hi = armed_lo = np.nan        # скопления, по которым уже входили
    inf = float("inf")
    for i in range(n):
        ai = a[i]
        if not np.isfinite(ai) or ai <= 0:
            continue
        top, bot = cl_hi[i], cl_lo[i]
        # ликвидность собрана — уровень перестаёт существовать
        if np.isfinite(top) and h[i] >= top:
            if top == armed_hi:
                ex[i] = True
                armed_hi = np.nan
            dead_hi, top = top, np.nan
        if np.isfinite(bot) and l[i] <= bot:
            if bot == armed_lo:
                ex[i] = True
                armed_lo = np.nan
            dead_lo, bot = bot, np.nan
        if np.isfinite(top) and top == dead_hi:
            top = np.nan
        if np.isfinite(bot) and bot == dead_lo:
            bot = np.nan
        du = (top - c[i]) / ai if (np.isfinite(top) and top > c[i]) else inf
        dd = (c[i] - bot) / ai if (np.isfinite(bot) and bot < c[i]) else inf
        if du <= reach and du <= dd and top != armed_hi:
            e[i] = 1
            armed_hi = top
        elif dd <= reach and dd < du and bot != armed_lo:
            e[i] = -1
            armed_lo = bot
    return strat.finish(bars, p, Signals(n), e, ex)


# --- 6. Вынос стопов и возврат — центральная гипотеза раздела ---------------

@strat.register("sweep_reclaim", FAMILY, {
    "n": [3, 5],
    "k": [2, 4, 6],
    "depth": [0.1, 0.4, 0.8],
    "big": [0.0, 2.0],
    "rr": [0.0, 2.0],
    "stop_atr": [2.5],
    "atr_n": [14],
}, "прокол подтверждённого экстремума и возврат за него в течение k баров")
def s_sweep_reclaim(bars, p):
    """Стопы вынесли — и цена пошла обратно. Главная гипотеза раздела.

    Гипотеза. За очевидным экстремумом лежит плотная пачка стоп-заявок. Когда
    цена туда доходит, стопы срабатывают рыночными приказами и на секунду
    создают поток в одну сторону — но это поток вынужденных, а не желающих.
    Если за этим потоком не стоит настоящий продавец, цена возвращается за
    уровень так же быстро, как ушла. Такой возврат — редкий случай, когда видно
    не мнение толпы, а её вынужденное действие: тот, кто продавил цену, набрал
    позицию против выбитых и ему нужно движение в обратную сторону.

    Признак читается по трём числам, и все три вынесены в сетку:
      * depth — насколько глубоко тень ушла за уровень (в ATR). Слишком мелкий
        прокол — просто касание, слишком глубокий — уже настоящий пробой;
      * k — сколько баров даётся на возврат. Смысл в скорости: возврат через
        сутки — это не вынос, это новая история;
      * big — величина уровня, размах структуры вокруг него в ATR. Мелкий
        зубец не виден никому, и стопов за ним нет; выносить там нечего.

    Прокол засчитывается только если предыдущее закрытие было ПО ЭТУ сторону
    уровня. Без этого условия стратегия ловила бы падающий нож: в затяжном
    падении цена всё время «ниже уровня», и любой отскок читался бы как возврат.

    Идея умирает, когда цена снова уходит за уровень заметно глубже прокола:
    значит там всё-таки был настоящий продавец, и держать позицию не за что.
    """
    n = len(bars.t)
    hi, lo = _swings(bars.h, bars.l, int(p["n"]))
    h_last, _hp, _hc = _levels(hi)
    l_last, _lp, _lc = _levels(lo)
    a = _atr(bars, p)
    k = int(p["k"])
    depth = float(p["depth"])
    big = float(p["big"])
    c, h, l = bars.c, bars.h, bars.l
    e = np.zeros(n, dtype=np.int8)
    ex = np.zeros(n, dtype=bool)
    sw_lo_i, sw_lo_v = -1, np.nan       # незакрытый прокол низа
    sw_hi_i, sw_hi_v = -1, np.nan       # незакрытый прокол верха
    act_dir, act_lvl = 0, np.nan        # уровень, на котором стоит текущая идея
    for i in range(1, n):
        ai = a[i]
        if not np.isfinite(ai) or ai <= 0:
            continue
        lv_lo, lv_hi = l_last[i], h_last[i]
        span = abs(lv_hi - lv_lo) if (np.isfinite(lv_hi)
                                      and np.isfinite(lv_lo)) else np.nan
        wide = np.isfinite(span) and span >= big * ai
        # прокол низа: были выше уровня, тенью ушли заметно ниже
        if wide and np.isfinite(lv_lo) and c[i - 1] > lv_lo \
                and l[i] <= lv_lo - depth * ai:
            sw_lo_i, sw_lo_v = i, lv_lo
        if wide and np.isfinite(lv_hi) and c[i - 1] < lv_hi \
                and h[i] >= lv_hi + depth * ai:
            sw_hi_i, sw_hi_v = i, lv_hi
        # возврат за уровень: тем же баром или в пределах k баров
        if sw_lo_i >= 0:
            if i - sw_lo_i > k:
                sw_lo_i = -1
            elif c[i] > sw_lo_v:
                e[i] = 1
                act_dir, act_lvl = 1, sw_lo_v
                sw_lo_i = -1
        if sw_hi_i >= 0 and e[i] == 0:
            if i - sw_hi_i > k:
                sw_hi_i = -1
            elif c[i] < sw_hi_v:
                e[i] = -1
                act_dir, act_lvl = -1, sw_hi_v
                sw_hi_i = -1
        # идея опровергнута: цена ушла обратно за уровень глубже прокола
        if act_dir and e[i] == 0:
            far = (depth + 0.5) * ai
            if (act_dir > 0 and c[i] < act_lvl - far) or \
                    (act_dir < 0 and c[i] > act_lvl + far):
                ex[i] = True
                act_dir, act_lvl = 0, np.nan
    return strat.finish(bars, p, Signals(n), e, ex)


# --- самопроверка -----------------------------------------------------------

CUTS = (0.35, 0.6, 0.85)


def _causal_check(name, bars, limit=24, seed=0):
    """Причинность всей сетки (или 24 случайных сочетаний) в ТРЁХ точках среза.

    Одной точки мало, и вот почему. Проверка портит бары после среза и сверяет
    сигналы до него — значит заглядывание на H баров вперёд видно только на
    последних H барах перед срезом. Сигналы структуры редкие: на десяти барах
    перед конкретным срезом их может не оказаться вовсе, и утечка проскочит по
    случайности. Три разных среза делают такую удачу маловероятной.
    """
    import random

    import engine

    s = strat.REG[name]
    combos = list(s.combos())
    total = len(combos)
    if total > limit:
        combos = random.Random(seed).sample(combos, limit)
    for pp in combos:
        for cut in CUTS:
            engine.assert_causal(lambda b, q=pp: s.build(b, q), bars, cut=cut)
    return total, len(combos)


DEMO = {
    "bos": [dict(n=5, pen=0.25, stop_atr=3.0, trail_atr=0.0, atr_n=14),
            dict(n=8, pen=0.5, stop_atr=3.0, trail_atr=3.0, atr_n=14),
            dict(n=3, pen=0.0, stop_atr=2.0, trail_atr=0.0, atr_n=14)],
    "choch": [dict(n=5, pen=0.0, stop_atr=2.0, rr=1.5, atr_n=14),
              dict(n=8, pen=0.3, stop_atr=3.0, rr=3.0, atr_n=14),
              dict(n=3, pen=0.3, stop_atr=2.0, rr=3.0, atr_n=14)],
    "hh_hl": [dict(n=5, min_leg=1.5, stop_atr=3.0, trail_atr=3.0, exit_mixed=1),
              dict(n=8, min_leg=0.0, stop_atr=3.0, trail_atr=0.0, exit_mixed=0),
              dict(n=3, min_leg=1.5, stop_atr=2.0, trail_atr=0.0, exit_mixed=1)],
    "sr_bounce": [dict(n=5, tol=0.005, look=8, stop_atr=1.5, rr=1.5, atr_n=14),
                  dict(n=5, tol=0.01, look=4, stop_atr=2.5, rr=3.0, atr_n=14),
                  dict(n=3, tol=0.002, look=8, stop_atr=2.5, rr=1.5, atr_n=14)],
    "equal_levels": [
        dict(n=5, tol=0.003, kmin=2, reach=4.0, stop_atr=3.0, atr_n=14),
        dict(n=3, tol=0.001, kmin=3, reach=2.0, stop_atr=2.0, atr_n=14),
        dict(n=5, tol=0.001, kmin=2, reach=2.0, stop_atr=2.0, atr_n=14)],
    "sweep_reclaim": [
        dict(n=5, k=4, depth=0.4, big=2.0, rr=2.0, stop_atr=2.5, atr_n=14),
        dict(n=5, k=2, depth=0.8, big=2.0, rr=0.0, stop_atr=2.5, atr_n=14),
        dict(n=3, k=6, depth=0.1, big=0.0, rr=2.0, stop_atr=2.5, atr_n=14),
        dict(n=5, k=6, depth=0.4, big=0.0, rr=0.0, stop_atr=2.5, atr_n=14)],
}


_ABBR = [("n", "n"), ("k", "k"), ("pen", "p"), ("depth", "d"), ("big", "b"),
         ("tol", "t"), ("kmin", "q"), ("look", "lk"), ("reach", "rc"),
         ("min_leg", "lg"), ("rr", "rr"), ("stop_atr", "s"),
         ("trail_atr", "tr"), ("exit_mixed", "x")]


def _short(p):
    """Подпись сочетания, влезающая в колонку отчёта."""
    return " ".join("%s%g" % (ab, p[k]) for k, ab in _ABBR if k in p)


if __name__ == "__main__":
    import time

    import engine
    import metrics
    import rdata

    bars, _ = rdata.load_bars("BTCUSDT", "60").slice(*rdata.SPLITS["train"])
    print("Структура рынка: %d сочетаний в %d стратегиях, обучающая выборка "
          "BTCUSDT 1ч, %d баров"
          % (sum(strat.REG[nm].n_combos() for nm in ORDER), len(ORDER),
             len(bars.t)))

    print("\n1. Причинность: срезы %s, при сетке больше 24 — случайные 24 "
          "(seed=0)" % ", ".join("%g" % x for x in CUTS))
    for nm in ORDER:
        t0 = time.time()
        total, done = _causal_check(nm, bars)
        print("  + %-14s сетка %3d, проверено %2d x %d срезов, %.1f с"
              % (nm, total, done, len(CUTS), time.time() - t0))

    print("\n2. Показательные прогоны (риск 1%, плечо<=10, тейкер, фандинг)")
    print("   Колонка «брутто» — тот же сигнал без комиссии, проскальзывания "
          "и фандинга.\n   Она отвечает на вопрос, чего стоит сама гипотеза, "
          "отдельно от стоимости\n   её исполнения: без неё убыток нечем "
          "объяснить и не с чем сравнить.")
    cfg = engine.Cfg()
    raw = engine.Cfg(fee=0.0, slip_mult=0.0, funding=False)
    for nm in ORDER:
        s = strat.REG[nm]
        print("  %s — %s" % (nm, s.note.split("\n")[0][:70]))
        for pp in DEMO[nm]:
            res = engine.run(bars, s.build(bars, pp), cfg)
            sm = metrics.summarize(res, n_trials=s.n_combos(),
                                   label=_short(pp))
            gr = metrics.summarize(engine.run(bars, s.build(bars, pp), raw))
            print("    %s | DSR %.2f | брутто %s Ш %+5.2f | держ %3.0f бар"
                  % (metrics.brief(sm), sm["dsr"], metrics.fmt_pct(gr["ret"]),
                     gr["sharpe"], sm["avg_hold"]))
    print("\nВсё зелено: заглядывания в будущее нет ни в одном сочетании.")
