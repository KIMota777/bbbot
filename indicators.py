# -*- coding: utf-8 -*-
"""Дополнительные индикаторы, общие для движка и живого бота.

Единый источник истины — как gridlib для сетки: если бот посчитает индикатор
иначе, чем движок, отбор проверит одну стратегию, а торговать будет другая.

Пока здесь один индикатор — ADX (Average Directional Index, сила тренда по
Уайлдеру). Зачем он появился (волна v12): стратегия проекта — возврат к
средней, вход у границы диапазона. Её убытки концентрируются там, где
диапазон ломается в устойчивый тренд, а среди прежних индикаторов силу
тренда не меряет ни один: MA-кросс и EMA дают НАПРАВЛЕНИЕ, Aroon — свежесть
экстремума, RSI — перепроданность. ADX закрывает именно эту дыру: гейт
«не входить, пока ADX выше порога» отсекает входы против набравшего силу
движения независимо от его направления.
"""


def calc_adx(candles, n=14):
    """ADX по Уайлдеру, выровнен по свечам. candles: [(ts,o,h,l,c), ...].

    None, пока индикатор не прогрет (первые ~2n баров). Сглаживание — точно
    по Уайлдеру: первые n значений суммируются, дальше val = val - val/n + x.
    """
    m = len(candles)
    adx = [None] * m
    if m < 2 * n + 1:
        return adx
    trs, pdms, ndms = [], [], []
    for i in range(1, m):
        _, o, h, l, c = candles[i]
        ph, pl, pc = candles[i - 1][2], candles[i - 1][3], candles[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        up, dn = h - ph, pl - l
        pdms.append(up if (up > dn and up > 0) else 0.0)
        ndms.append(dn if (dn > up and dn > 0) else 0.0)
    atr = sum(trs[:n])
    pdm = sum(pdms[:n])
    ndm = sum(ndms[:n])
    dx_sum, dx_count, val = 0.0, 0, None
    for j in range(n, len(trs)):
        atr = atr - atr / n + trs[j]
        pdm = pdm - pdm / n + pdms[j]
        ndm = ndm - ndm / n + ndms[j]
        pdi = 100.0 * pdm / atr if atr else 0.0
        ndi = 100.0 * ndm / atr if atr else 0.0
        s = pdi + ndi
        dx = 100.0 * abs(pdi - ndi) / s if s else 0.0
        if val is None:
            dx_sum += dx        # первые n значений DX — простое среднее
            dx_count += 1
            if dx_count == n:
                val = dx_sum / n
        else:
            val = (val * (n - 1) + dx) / n
        if val is not None:
            adx[j + 1] = val    # trs[j] соответствует свече j+1
    return adx
