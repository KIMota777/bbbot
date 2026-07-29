# -*- coding: utf-8 -*-
"""Smart Money Concepts: Order Blocks, Fair Value Gaps, структура рынка
(Break of Structure / Change of Character).

Переиспользует пивоты/зигзаг из patterns.py — та же дисциплина "не
заглядывать вперёд": пивот на баре p подтверждается только на баре p+w,
после импульса ордер-блок подтверждается только после того, как импульс
реально состоялся (через impulse_bars баров), FVG по конструкции не требует
задержки (виден сразу на баре его формирования — 3 закрытых свечи).

Публичные функции, каждая -> (bull[bool per bar], bear[bool per bar]):
  find_fvg(candles)          — цена внутри неотработанного (не закрытого) FVG
  find_order_blocks(candles) — цена внутри неотработанного ордер-блока
  structure_bias(candles)    — не bool-пара, а bias[i] in {-1,0,+1}: текущий
                                структурный тренд по BOS/CHoCH (свинги)

Модуль самодостаточен (переиспользует только patterns.py, без evolution/config)
— безопасен и для исследований, и для живого бота.
"""

from patterns import W as PIVOT_W
from patterns import _calc_atr_pct, build_zigzag, find_pivots

FVG_MIN_ATR = 0.15      # мин. размер гэпа в долях ATR, иначе шум
OB_IMPULSE_BARS = 5     # за сколько баров после кандидата должен состояться импульс
OB_IMPULSE_K = 2.0      # импульс >= K x ATR, иначе не считаем ордер-блоком


def find_fvg(candles, bars_per_day=96):
    """Fair Value Gap: разрыв между high/low через одну свечу. Зона активна,
    пока цена не протолкнулась через неё насквозь (митигация).
    bars_per_day: баров в сутках для внутреннего ATR (15m->96, 4ч->6)."""
    n = len(candles)
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    atr = _calc_atr_pct(candles, n=bars_per_day)
    bull, bear = [False] * n, [False] * n
    open_bull, open_bear = [], []  # список [lo, hi] активных зон

    for i in range(2, n):
        a = atr[i] if atr[i] else 0.01
        # новый бычий FVG: low[i] > high[i-2]
        if lows[i] > highs[i - 2] and (lows[i] - highs[i - 2]) / candles[i][4] >= FVG_MIN_ATR * a:
            open_bull.append([highs[i - 2], lows[i]])
        # новый медвежий FVG: high[i] < low[i-2]
        if highs[i] < lows[i - 2] and (lows[i - 2] - highs[i]) / candles[i][4] >= FVG_MIN_ATR * a:
            open_bear.append([highs[i], lows[i - 2]])

        c_lo, c_hi = lows[i], highs[i]
        open_bull = [z for z in open_bull if c_lo > z[0]]   # митигация: цена ушла ниже зоны
        open_bear = [z for z in open_bear if c_hi < z[1]]   # митигация: цена ушла выше зоны

        bull[i] = any(z[0] <= c_hi and c_lo <= z[1] for z in open_bull)
        bear[i] = any(z[0] <= c_hi and c_lo <= z[1] for z in open_bear)
    return bull, bear


def find_order_blocks(candles, impulse_bars=OB_IMPULSE_BARS,
                      impulse_k=OB_IMPULSE_K, bars_per_day=96):
    """Ордер-блок: последняя противоположная свеча перед сильным импульсом.
    Подтверждается с задержкой impulse_bars (без заглядывания вперёд)."""
    n = len(candles)
    opens = [c[1] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    atr = _calc_atr_pct(candles, n=bars_per_day)
    bull, bear = [False] * n, [False] * n
    open_bull, open_bear = [], []

    for i in range(impulse_bars, n):
        cand = i - impulse_bars  # кандидат в ордер-блок, импульс проверяем от cand до i
        a = atr[cand] if cand < len(atr) and atr[cand] else None
        if a:
            impulse = (closes[i] - closes[cand]) / closes[cand]
            is_down_candle = closes[cand] < opens[cand]
            is_up_candle = closes[cand] > opens[cand]
            if impulse >= impulse_k * a and is_down_candle:
                open_bull.append([lows[cand], highs[cand]])
            elif -impulse >= impulse_k * a and is_up_candle:
                open_bear.append([lows[cand], highs[cand]])

        c_lo, c_hi = lows[i], highs[i]
        open_bull = [z for z in open_bull if closes[i] > z[0]]  # митигация: закрылись ниже
        open_bear = [z for z in open_bear if closes[i] < z[1]]  # митигация: закрылись выше

        bull[i] = any(z[0] <= c_hi and c_lo <= z[1] for z in open_bull)
        bear[i] = any(z[0] <= c_hi and c_lo <= z[1] for z in open_bear)
    return bull, bear


def structure_bias(candles, w=None):
    """Текущий структурный тренд по свингам: +1 бычий, -1 медвежий, 0 не
    определён. Стандартное определение: закрытие выше последнего свинг-хая
    -> бычий пробой (BOS, если бias уже был бычьим, иначе CHoCH-разворот);
    закрытие ниже последнего свинг-лоя -> зеркально медвежий. Пивот на баре
    p используется только начиная с бара p+w (без заглядывания вперёд)."""
    w = w or PIVOT_W
    n = len(candles)
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    piv_h, piv_l = find_pivots(candles, w)
    zz = build_zigzag(piv_h, piv_l, highs, lows)

    confirmations = sorted(
        ((min(pb + w, n - 1), typ, price) for pb, typ, price in zz),
        key=lambda e: e[0])

    out = [0] * n
    bias = 0
    last_high = last_low = None
    ci = 0
    for i in range(n):
        while ci < len(confirmations) and confirmations[ci][0] <= i:
            _, typ, price = confirmations[ci]
            if typ == "H":
                last_high = price
            else:
                last_low = price
            ci += 1
        if last_high is not None and closes[i] > last_high:
            bias = 1
        elif last_low is not None and closes[i] < last_low:
            bias = -1
        out[i] = bias
    return out
