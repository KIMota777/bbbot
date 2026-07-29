# -*- coding: utf-8 -*-
"""Детектор классических графических паттернов (см. cheatsheet: двойные/тройные
вершины-донья, голова-плечи, треугольники, клинья, флаги).

Метод: находим фракталы (локальные экстремумы за w баров в обе стороны),
строим зигзаг чередующихся H/L, и на каждом новом подтверждённом пивоте
проверяем геометрию последних 3-5 точек зигзага на совпадение с паттерном.
Треугольники/клинья оцениваются по наклону линий через последние вершины/
донья. Флаги — отдельным скользящим проходом (импульс + узкая консолидация).

Допуски (tol) и минимальный размах ноги (leg) считаются от ATR бара, а не
фиксированным %, чтобы работать что на BTC (ATR~0.1-0.3%), что на DOGE
(ATR~0.3-0.5%).

Каждый найденный паттерн создаёт сигнал bull/bear, активный `expiry` баров
после подтверждения (несколько паттернов подряд просто продлевают сигнал).

Это геометрическая эвристика по правилам с чарт-шита, а не ML-классификатор:
осознанное упрощение ради интерпретируемости и скорости на 100k+ свечей.

Публичная функция: compute_pattern_signals(candles) -> (bull[bool], bear[bool])
candles: [[ts, open, high, low, close], ...] (списки или кортежи — не важно)

Модуль самодостаточен (не тянет evolution.py/config.py) — его безопасно
импортировать и из исследовательских скриптов, и из боевого bot_rsi.py.
"""

W = 6            # полуширина окна фрактала (баров с каждой стороны)
EXPIRY = 48      # сколько баров сигнал считается активным (~12ч на 15m)
FLAG_IMPULSE = 20
FLAG_CONSOL = 10
FLAG_IMPULSE_K = 2.2   # импульс >= K x ATR
FLAG_CONSOL_K = 1.0    # консолидация <= K x ATR


def _calc_atr_pct(candles, n=96):
    """ATR за n свечей как доля цены; None, пока истории не хватает."""
    m = len(candles)
    atr = [None] * m
    if m < 2:
        return atr
    prev_c = candles[0][4]
    val, trs = None, []
    for i in range(1, m):
        h, l, c = candles[i][2], candles[i][3], candles[i][4]
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


def find_pivots(candles, w=W):
    n = len(candles)
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    piv_high = [False] * n
    piv_low = [False] * n
    for i in range(w, n - w):
        seg_h = highs[i - w:i + w + 1]
        if seg_h.index(max(seg_h)) == w:
            piv_high[i] = True
        seg_l = lows[i - w:i + w + 1]
        if seg_l.index(min(seg_l)) == w:
            piv_low[i] = True
    return piv_high, piv_low


def build_zigzag(piv_high, piv_low, highs, lows):
    """[(pivot_bar, 'H'|'L', price), ...] чередующиеся, по возрастанию бара."""
    events = [(i, "H", highs[i]) for i, v in enumerate(piv_high) if v]
    events += [(i, "L", lows[i]) for i, v in enumerate(piv_low) if v]
    events.sort(key=lambda e: e[0])
    zz = []
    for pb, typ, price in events:
        if zz and zz[-1][1] == typ:
            better = (price > zz[-1][2]) if typ == "H" else (price < zz[-1][2])
            if better:
                zz[-1] = (pb, typ, price)
        else:
            zz.append((pb, typ, price))
    return zz


def compute_pattern_signals(candles, w=W, expiry=EXPIRY, bars_per_day=96):
    """bars_per_day: баров в сутках для ATR внутри функции (15m -> 96,
    4ч/"240" -> 6). На неверном значении волатильность считалась бы по
    чужому окну (16 суток вместо суток на 4ч) — не декоративный параметр."""
    n = len(candles)
    if n < 4 * w + 20:
        return [False] * n, [False] * n
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    atr = _calc_atr_pct(candles, n=bars_per_day)
    piv_h, piv_l = find_pivots(candles, w)
    zz = build_zigzag(piv_h, piv_l, highs, lows)

    def tol_at(bar):
        a = atr[bar] if bar < len(atr) and atr[bar] else 0.01
        return max(0.008, 1.5 * a)

    def leg_at(bar):
        a = atr[bar] if bar < len(atr) and atr[bar] else 0.01
        return max(0.012, 2.0 * a)

    events = []  # (confirm_bar, +1 bull / -1 bear)
    last_highs, last_lows = [], []

    for idx, (pb, typ, price) in enumerate(zz):
        confirm = min(pb + w, n - 1)
        if typ == "H":
            last_highs.append((pb, price))
            if len(last_highs) > 6:
                last_highs.pop(0)
        else:
            last_lows.append((pb, price))
            if len(last_lows) > 6:
                last_lows.pop(0)

        tol, leg = tol_at(confirm), leg_at(confirm)
        window = zz[max(0, idx - 4):idx + 1]

        if len(window) >= 3 and typ == "H":
            p3, p2, p1 = window[-3], window[-2], window[-1]
            if p3[1] == "H" and p2[1] == "L" and p1[1] == "H":
                if abs(p1[2] - p3[2]) / p3[2] <= tol and (p3[2] - p2[2]) / p3[2] >= leg:
                    events.append((confirm, -1))  # double top
        if len(window) >= 3 and typ == "L":
            p3, p2, p1 = window[-3], window[-2], window[-1]
            if p3[1] == "L" and p2[1] == "H" and p1[1] == "L":
                if abs(p1[2] - p3[2]) / p3[2] <= tol and (p2[2] - p3[2]) / p3[2] >= leg:
                    events.append((confirm, +1))  # double bottom

        if len(window) >= 5 and typ == "H":
            a, b, c, d, e = window[-5:]
            if (a[1], b[1], c[1], d[1], e[1]) == ("H", "L", "H", "L", "H"):
                tops = [a[2], c[2], e[2]]
                spread = (max(tops) - min(tops)) / (sum(tops) / 3)
                if spread <= tol and min(a[2] - b[2], c[2] - d[2]) / a[2] >= leg * 0.7:
                    events.append((confirm, -1))  # triple top
                elif (c[2] > a[2] * (1 + leg) and c[2] > e[2] * (1 + leg)
                      and abs(e[2] - a[2]) / a[2] <= tol * 1.6
                      and abs(d[2] - b[2]) / b[2] <= tol * 2.2):
                    events.append((confirm, -1))  # head & shoulders
        if len(window) >= 5 and typ == "L":
            a, b, c, d, e = window[-5:]
            if (a[1], b[1], c[1], d[1], e[1]) == ("L", "H", "L", "H", "L"):
                bots = [a[2], c[2], e[2]]
                spread = (max(bots) - min(bots)) / (sum(bots) / 3)
                if spread <= tol and min(b[2] - a[2], d[2] - c[2]) / a[2] >= leg * 0.7:
                    events.append((confirm, +1))  # triple bottom
                elif (c[2] < a[2] * (1 - leg) and c[2] < e[2] * (1 - leg)
                      and abs(e[2] - a[2]) / a[2] <= tol * 1.6
                      and abs(d[2] - b[2]) / b[2] <= tol * 2.2):
                    events.append((confirm, +1))  # inverted H&S

        if len(last_highs) >= 3 and len(last_lows) >= 3:
            hh, ll = last_highs[-3:], last_lows[-3:]
            span = max(hh[-1][0], ll[-1][0]) - min(hh[0][0], ll[0][0])
            if span <= 160:
                hi_chg = (hh[-1][1] - hh[0][1]) / hh[0][1]
                lo_chg = (ll[-1][1] - ll[0][1]) / ll[0][1]
                flat_eps = max(0.006, 1.2 * tol)
                rng_first, rng_last = hh[0][1] - ll[0][1], hh[-1][1] - ll[-1][1]
                if rng_first > 0 and rng_last < rng_first * 0.82:
                    if abs(hi_chg) < flat_eps and lo_chg > flat_eps:
                        events.append((confirm, +1))       # ascending triangle
                    elif hi_chg < -flat_eps and abs(lo_chg) < flat_eps:
                        events.append((confirm, -1))       # descending triangle
                    elif hi_chg > flat_eps and lo_chg > flat_eps:
                        events.append((confirm, -1))       # rising wedge
                    elif hi_chg < -flat_eps and lo_chg < -flat_eps:
                        events.append((confirm, +1))       # falling wedge
                    elif hi_chg < -flat_eps and lo_chg > flat_eps:
                        start = min(hh[0][0], ll[0][0])
                        prior = max(0, start - 40)
                        events.append((confirm, +1 if closes[start] > closes[prior] else -1))

    for i in range(FLAG_IMPULSE + FLAG_CONSOL, n):
        a = atr[i] if i < len(atr) and atr[i] else None
        if not a:
            continue
        base = closes[i - FLAG_CONSOL - FLAG_IMPULSE]
        impulse = (closes[i - FLAG_CONSOL] - base) / base
        cons_hi = max(highs[i - FLAG_CONSOL:i + 1])
        cons_lo = min(lows[i - FLAG_CONSOL:i + 1])
        cons_rng = (cons_hi - cons_lo) / closes[i]
        if abs(impulse) >= FLAG_IMPULSE_K * a and cons_rng <= FLAG_CONSOL_K * a:
            events.append((i, 1 if impulse > 0 else -1))

    delta_b, delta_r = [0] * (n + 1), [0] * (n + 1)
    for bar, d in events:
        end = min(n, bar + expiry)
        if d > 0:
            delta_b[bar] += 1
            delta_b[end] -= 1
        else:
            delta_r[bar] += 1
            delta_r[end] -= 1
    bull, bear = [False] * n, [False] * n
    cb = cr = 0
    for i in range(n):
        cb += delta_b[i]
        cr += delta_r[i]
        bull[i] = cb > 0
        bear[i] = cr > 0
    return bull, bear
