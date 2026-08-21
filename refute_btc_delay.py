# -*- coding: utf-8 -*-
"""Линза 3: задержка исполнения — сигнал на баре i, вход на баре i+N.

Зачем. Живой бот видит закрытие свечи, считает индикаторы, шлёт ордер. Между
сигналом и реальным входом проходит время: опрос биржи, сеть, очередь ордера,
а на 15-минутке ещё и то, что закрытие свечи ты узнаёшь уже после факта. Если
весь перевес сидит в первых пятнадцати минутах после сигнала, в реальной
торговле его не будет — цена уйдёт раньше, чем ты в неё попадёшь.

КАК СДЕЛАНА ЗАДЕРЖКА (движок не тронут, всё через подмену входных рядов):
  * ряды pre (RSI, ATR, closes) сдвинуты на N баров назад, поэтому условие
    входа на баре i считает ровно то, что было на баре i-N;
  * rolling_extremes подменена на время прогона так, чтобы она возвращала
    границы окна бара i-N, СМЕЩЁННЫЕ на разницу цен (c_i - c_{i-N}). Тогда
    положение цены в диапазоне (zpos) получается бит в бит тем, что было на
    баре i-N, а стоп остаётся на том же расстоянии от входа. То есть решение
    — старое, а исполняется оно по НОВОЙ цене; это ровно то, что нужно;
  * ряд closes для фильтра ножа отмасштабирован так, чтобы 8-барное движение
    считалось от старой цены — иначе нож мерил бы чужой отрезок;
  * внешний фильтр входа спрашивается по индексу i-N.

Единственная неточность, которую честно назову: подтверждение выхода по
таймауту (RSI выше/ниже 50) тоже смотрит RSI бара i-N. На выходы задержка
формально не распространяется, но отделить это в чужом движке нечем, а
влияние — единицы баров на редких таймаутных выходах.
"""
import sys

import numpy as np

import evolution as ev
import evolution2 as e2
import evolution8 as e8
import refute_btc_common as rc


def shift_pre(pre, candles, n):
    """Ряды индикаторов, сдвинутые на n баров: значение бара i-n лежит в i."""
    if n == 0:
        return pre
    closes = pre["closes"]
    L = len(closes)
    out = dict(rsi={}, atr=[None] * L)
    for p, arr in pre["rsi"].items():
        out["rsi"][p] = [None] * n + list(arr[:L - n])
    out["atr"] = [None] * n + list(pre["atr"][:L - n])
    # closes: нужен для ножа как move = (closes[i-8] - c_i)/c_i.
    # Масштабируем так, чтобы движение считалось от цены бара i-n.
    c = [x[4] for x in candles]
    nc = list(closes)
    for k in range(n + 8, L - 8):
        j = k + 8                      # бар, на котором этот элемент прочтут
        if j - n < 0 or c[j - n] == 0:
            continue
        nc[k] = closes[k - n] * c[j] / c[j - n]
    out["closes"] = nc
    return out


def patched_extremes(candles, n):
    """Подмена ev.rolling_extremes: границы окна бара i-n, сдвинутые на
    разницу цен. Даёт старое zpos при новой цене входа."""
    real = ev.rolling_extremes
    c = [x[4] for x in candles]

    def f(cnd, window):
        lo, hi = real(cnd, window)
        if n == 0:
            return lo, hi
        L = len(lo)
        nlo, nhi = list(lo), list(hi)
        for i in range(n, L):
            d = c[i] - c[i - n]
            nlo[i] = lo[i - n] + d
            nhi[i] = hi[i - n] + d
        return nlo, nhi

    return f, real


def run_delay(d, n):
    s = d["hold"]
    pre = shift_pre(s["pre"], s["candles"], n)
    base_filt = e8.make_filter8(d["g"], d["aux_h"])

    def filt(side, i):
        j = i - n
        return base_filt(side, j) if j >= 0 else None

    patch, real = patched_extremes(s["candles"], n)
    ev.rolling_extremes = patch
    try:
        r, evs = rc.run_hold(d, pre=pre, filt=filt)
    finally:
        ev.rolling_extremes = real
    return rc.nums(r, evs, s["months"])


def main():
    d = rc.build()
    print("BTC normal x5, ХОЛДАУТ %.1f мес — задержка исполнения"
          % d["hold"]["months"])
    print("N баров по 15 минут: 1 = четверть часа, 4 = час, 96 = сутки")
    print("%-22s %6s %7s %9s %10s %8s"
          % ("задержка", "сдел", "ВР%", "холдаут%", "б/копеек%", "просад%"))
    base = None
    for n in [0, 1, 2, 3, 4, 8, 16, 32, 96]:
        m = run_delay(d, n)
        if n == 0:
            base = m
        tag = "нет (контроль)" if n == 0 else "%d бар(ов) = %s" % (
            n, ("%d мин" % (n * 15)) if n * 15 < 120 else "%.0f ч" % (n / 4))
        print("%-22s %6d %6.1f %+8.1f%% %+9.1f%% %7.1f%%"
              % (tag, m["trades"], m["wr"], m["comp"], m["comp_nt"], m["dd"]))
        sys.stdout.flush()

    # Самопроверка: при N=0 подмена обязана воспроизвести обычный прогон
    r0, e0 = rc.run_hold(d)
    m0 = rc.nums(r0, e0, d["hold"]["months"])
    ok = (base["trades"] == m0["trades"]
          and abs(base["comp"] - m0["comp"]) < 1e-6)
    print()
    print("самопроверка N=0: подмена рядов %s обычный прогон (%d сд, %+.3f%% "
          "против %d сд, %+.3f%%)"
          % ("воспроизводит" if ok else "НЕ воспроизводит",
             base["trades"], base["comp"], m0["trades"], m0["comp"]))


if __name__ == "__main__":
    main()
