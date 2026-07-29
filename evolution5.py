# -*- coding: utf-8 -*-
"""Эволюция v5: классические индикаторы как гены-фильтры поверх итогов v4.

Новые гены (все с "выкл"):
  ema_mode (0..2), ema_n_idx -> {50,100,200,400,600}:
      1 = торговать по тренду EMA (лонг выше EMA), 2 = против, 0 = выкл.
  ma_mode (0..2), masf_idx -> {10,20,40}, masl_idx -> {100,200,400}:
      фильтр по положению быстрой SMA к медленной. 0 = выкл.
  aroon_idx -> {14,25,50,100}, aroon_long_min (0..100), aroon_short_min (0..100):
      лонг требует AroonDown >= порога (входим после свежего минимума),
      шорт — AroonUp >= порога. 0 = выкл.

База каждой монеты = итог v4 (кандидат, если принят, иначе прежний конфиг),
его гены сохраняются и продолжают участвовать в отборе.
"""

import json
from collections import deque

import evolution as ev
import evolution2 as e2
import evolution4 as e4
import ext_data as xd

EMA_SET = [50, 100, 200, 400, 600]
MAF_SET = [10, 20, 40]
MAS_SET = [100, 200, 400]
ARN_SET = [14, 25, 50, 100]

GENES5 = dict(e4.GENES4)
GENES5.update({
    "ema_mode":        (0, 2, True),
    "ema_n_idx":       (0, len(EMA_SET) - 1, True),
    "ma_mode":         (0, 2, True),
    "masf_idx":        (0, len(MAF_SET) - 1, True),
    "masl_idx":        (0, len(MAS_SET) - 1, True),
    "aroon_idx":       (0, len(ARN_SET) - 1, True),
    "aroon_long_min":  (0, 100, True),
    "aroon_short_min": (0, 100, True),
})
OFF5 = dict(ema_mode=0, ma_mode=0, aroon_long_min=0, aroon_short_min=0,
            ema_n_idx=2, masf_idx=1, masl_idx=1, aroon_idx=1)


def calc_ema(closes, n):
    ema = [None] * len(closes)
    if len(closes) < n:
        return ema
    s = sum(closes[:n]) / n
    ema[n - 1] = s
    a = 2 / (n + 1)
    for i in range(n, len(closes)):
        s = closes[i] * a + s * (1 - a)
        ema[i] = s
    return ema


def calc_sma(closes, n):
    out = [None] * len(closes)
    s = 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= n:
            s -= closes[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def calc_aroon(candles, n):
    """(aroon_up[], aroon_down[]) за n свечей."""
    up = [None] * len(candles)
    dn = [None] * len(candles)
    dq_max, dq_min = deque(), deque()
    for i, c in enumerate(candles):
        h, l = c[2], c[3]
        while dq_max and candles[dq_max[-1]][2] <= h:
            dq_max.pop()
        dq_max.append(i)
        while dq_min and candles[dq_min[-1]][3] >= l:
            dq_min.pop()
        dq_min.append(i)
        while dq_max[0] <= i - n:
            dq_max.popleft()
        while dq_min[0] <= i - n:
            dq_min.popleft()
        if i >= n - 1:
            up[i] = (n - (i - dq_max[0])) / n * 100
            dn[i] = (n - (i - dq_min[0])) / n * 100
    return up, dn


def make_filter5(g, aux):
    base_f = e4.make_filter4(g, aux)
    closes = aux["closes"]
    ema = aux["ema"][g["ema_n_idx"]]
    smaf = aux["smaf"][g["masf_idx"]]
    smas = aux["smas"][g["masl_idx"]]
    a_up, a_dn = aux["aroon"][g["aroon_idx"]]

    def f(side, i):
        side = base_f(side, i)
        if not side:
            return None
        c = closes[i]
        if g["ema_mode"]:
            e_ = ema[i]
            if e_ is not None:
                above = c > e_
                want_long = above if g["ema_mode"] == 1 else not above
                if side == "L" and not want_long:
                    return None
                if side == "S" and want_long:
                    return None
        if g["ma_mode"]:
            f_, s_ = smaf[i], smas[i]
            if f_ is not None and s_ is not None:
                upt = f_ > s_
                want_long = upt if g["ma_mode"] == 1 else not upt
                if side == "L" and not want_long:
                    return None
                if side == "S" and want_long:
                    return None
        if side == "L" and g["aroon_long_min"]:
            v = a_dn[i]
            if v is not None and v < g["aroon_long_min"]:
                return None
        if side == "S" and g["aroon_short_min"]:
            v = a_up[i]
            if v is not None and v < g["aroon_short_min"]:
                return None
        return side

    return f


def main():
    pct5 = xd.fetch_daily_pct5()
    with open("evolution4_winners.json", encoding="utf-8") as fh:
        v4 = json.load(fh)

    base_src = {}
    for sym in e4.SYMBOLS:
        g = v4[sym]["genome"] if v4[sym]["adopt"] else v4[sym]["base_genome"]
        base_src[sym] = g

    def aux_builder(sym, candles):
        funding = xd.fetch_funding(sym, e4.DAYS + 50)
        oi = xd.fetch_oi(sym)
        aux = xd.build_aux4(candles, funding, oi, pct5)
        closes = [c[4] for c in candles]
        aux["closes"] = closes
        aux["ema"] = [calc_ema(closes, n) for n in EMA_SET]
        aux["smaf"] = [calc_sma(closes, n) for n in MAF_SET]
        aux["smas"] = [calc_sma(closes, n) for n in MAS_SET]
        aux["aroon"] = [calc_aroon(candles, n) for n in ARN_SET]
        return aux

    e4.run_version(GENES5, OFF5, make_filter5, aux_builder, "evolution5",
                   base_src)


if __name__ == "__main__":
    main()
