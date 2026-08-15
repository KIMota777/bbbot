# -*- coding: utf-8 -*-
"""Индикаторы: векторные и строго причинные.

ПРАВИЛО МОДУЛЯ, действующее без исключений: значение с индексом i считается
только по барам 0..i включительно. Ни одна функция не центрирует окно и не
нормирует по всей выборке. Непрогретый участок — NaN, а не ноль: ноль
означал бы «индикатор говорит ноль», и стратегия приняла бы его за сигнал.

Все скользящие окна закрываются на текущем баре. То есть sma(c, 20)[i] —
среднее баров i-19..i, и оно известно к закрытию бара i. Дальше движок сам
сдвигает исполнение на бар вперёд, поэтому здесь сдвигать НЕ НАДО: двойной
сдвиг так же портит исследование, как и его отсутствие, только незаметнее.
"""
import numpy as np

NAN = np.nan


def _win(x, n):
    """Окна длины n, закрытые на каждом баре. Первые n-1 позиций непригодны."""
    return np.lib.stride_tricks.sliding_window_view(x, n)


def sma(x, n):
    out = np.full(len(x), NAN)
    if n <= 0 or len(x) < n:
        return out
    cs = np.concatenate([[0.0], np.cumsum(x)])
    out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def ema(x, n):
    """Экспоненциальное среднее. Разгон — простым средним первых n баров."""
    out = np.full(len(x), NAN)
    if n <= 0 or len(x) < n:
        return out
    a = 2.0 / (n + 1.0)
    v = float(x[:n].mean())
    out[n - 1] = v
    for i in range(n, len(x)):
        v = a * x[i] + (1 - a) * v
        out[i] = v
    return out


def wilder(x, n):
    """Сглаживание Уайлдера (ATR, RSI, ADX). a = 1/n, разгон средним."""
    out = np.full(len(x), NAN)
    if n <= 0 or len(x) < n:
        return out
    v = float(x[:n].mean())
    out[n - 1] = v
    for i in range(n, len(x)):
        v = v + (x[i] - v) / n
        out[i] = v
    return out


def wma(x, n):
    out = np.full(len(x), NAN)
    if len(x) < n:
        return out
    w = np.arange(1, n + 1, dtype=np.float64)
    out[n - 1:] = _win(x, n) @ w / w.sum()
    return out


def rolling_max(x, n):
    out = np.full(len(x), NAN)
    if len(x) >= n:
        out[n - 1:] = _win(x, n).max(axis=1)
    return out


def rolling_min(x, n):
    out = np.full(len(x), NAN)
    if len(x) >= n:
        out[n - 1:] = _win(x, n).min(axis=1)
    return out


def rolling_std(x, n):
    out = np.full(len(x), NAN)
    if len(x) >= n:
        out[n - 1:] = _win(x, n).std(axis=1, ddof=1)
    return out


def rolling_sum(x, n):
    out = np.full(len(x), NAN)
    if len(x) >= n:
        cs = np.concatenate([[0.0], np.cumsum(x)])
        out[n - 1:] = cs[n:] - cs[:-n]
    return out


def rolling_rank(x, n):
    """Доля баров окна, которые НЕ выше текущего. Перцентиль без параметров.

    Нужен там, где абсолютный порог не переносится между активами: ATR в
    долларах у BTC и DOGE несравним, а «ATR выше своей нормы за 200 баров» —
    сравним.
    """
    out = np.full(len(x), NAN)
    if len(x) >= n:
        w = _win(x, n)
        out[n - 1:] = (w <= w[:, -1:]).sum(axis=1) / float(n)
    return out


def true_range(h, l, c):
    pc = np.concatenate([[c[0]], c[:-1]])
    return np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))


def atr(h, l, c, n=14):
    return wilder(true_range(h, l, c), n)


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    up = wilder(np.maximum(d, 0.0), n)
    dn = wilder(np.maximum(-d, 0.0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(dn > 0, up / dn, np.inf)
    out = 100.0 - 100.0 / (1.0 + rs)
    out[np.isnan(up) | np.isnan(dn)] = NAN
    return out


def adx(h, l, c, n=14):
    """ADX по Уайлдеру. Возвращает (adx, +DI, -DI)."""
    tr = true_range(h, l, c)
    up = np.diff(h, prepend=h[0])
    dn = -np.diff(l, prepend=l[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = wilder(tr, n)
    p = wilder(pdm, n)
    m = wilder(ndm, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * p / a
        ndi = 100.0 * m / a
        dx = 100.0 * np.abs(pdi - ndi) / (pdi + ndi)
    return wilder(np.nan_to_num(dx, nan=0.0), n), pdi, ndi


def bbands(c, n=20, k=2.0):
    m = sma(c, n)
    s = rolling_std(c, n)
    return m, m + k * s, m - k * s, np.where(m > 0, 2 * k * s / m, NAN)


def zscore(x, n=100):
    m = sma(x, n)
    s = rolling_std(x, n)
    return np.where(s > 0, (x - m) / s, NAN)


def donchian(h, l, n=20):
    """Канал Дончиана СО СДВИГОМ НА БАР — иначе пробой ловит сам себя.

    Если верх канала считать вместе с текущим баром, то условие «цена выше
    верха» не выполняется никогда: текущий бар и сформировал этот верх.
    А условие «high >= верх» выполняется на каждом новом максимуме, то есть
    сравнивает бар с самим собой. Правильный канал — по барам ДО текущего.
    """
    hi = np.full(len(h), NAN)
    lo = np.full(len(l), NAN)
    hi[1:] = rolling_max(h, n)[:-1]
    lo[1:] = rolling_min(l, n)[:-1]
    return hi, lo


def macd(c, fast=12, slow=26, sig=9):
    line = ema(c, fast) - ema(c, slow)
    ok = ~np.isnan(line)
    sl = np.full(len(c), NAN)
    if ok.sum() > sig:
        sl[ok] = ema(line[ok], sig)
    return line, sl, line - sl


def stoch(h, l, c, n=14, d=3):
    hh = rolling_max(h, n)
    ll = rolling_min(l, n)
    rng = hh - ll
    k = np.where(rng > 0, 100.0 * (c - ll) / rng, NAN)
    return k, sma(np.nan_to_num(k, nan=50.0), d)


def cci(h, l, c, n=20):
    tp = (h + l + c) / 3.0
    m = sma(tp, n)
    out = np.full(len(c), NAN)
    if len(c) >= n:
        w = _win(tp, n)
        md = np.abs(w - w.mean(axis=1, keepdims=True)).mean(axis=1)
        out[n - 1:] = np.where(md > 0, (tp[n - 1:] - m[n - 1:]) / (0.015 * md),
                               NAN)
    return out


def willr(h, l, c, n=14):
    hh, ll = rolling_max(h, n), rolling_min(l, n)
    rng = hh - ll
    return np.where(rng > 0, -100.0 * (hh - c) / rng, NAN)


def roc(c, n=10):
    out = np.full(len(c), NAN)
    out[n:] = c[n:] / c[:-n] - 1.0
    return out


def obv(c, v):
    d = np.sign(np.diff(c, prepend=c[0]))
    return np.cumsum(d * v)


def mfi(h, l, c, v, n=14):
    tp = (h + l + c) / 3.0
    flow = tp * v
    d = np.diff(tp, prepend=tp[0])
    pos = rolling_sum(np.where(d > 0, flow, 0.0), n)
    neg = rolling_sum(np.where(d < 0, flow, 0.0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(neg > 0, pos / neg, np.inf)
    return 100.0 - 100.0 / (1.0 + r)


def vwap_rolling(turnover, v, n):
    """Скользящий VWAP по фактическому обороту, а не по (h+l+c)/3.

    Оборот в долларах биржа отдаёт напрямую; типичная цена — лишь приближение,
    и на баре с длинной тенью ошибается заметно.
    """
    tv = rolling_sum(turnover, n)
    vv = rolling_sum(v, n)
    return np.where(vv > 0, tv / vv, NAN)


def vwap_anchored(t, turnover, v, period_ms):
    """VWAP от начала периода (суток/недели). Считается нарастающим итогом."""
    grp = (t // period_ms).astype(np.int64)
    new = np.concatenate([[True], grp[1:] != grp[:-1]])
    ct = np.cumsum(turnover)
    cv = np.cumsum(v)
    base_t = np.where(new, ct - turnover, 0.0)
    base_v = np.where(new, cv - v, 0.0)
    idx = np.maximum.accumulate(np.where(new, np.arange(len(t)), 0))
    bt = np.where(new, base_t, 0.0)[idx]
    bv = np.where(new, base_v, 0.0)[idx]
    num, den = ct - bt, cv - bv
    return np.where(den > 0, num / den, NAN)


def supertrend(h, l, c, n=10, k=3.0):
    """Supertrend. Возвращает (линия, направление +1/-1).

    Полосы «залипают» рекурсивно, поэтому цикл по барам неизбежен: значение
    зависит от собственного прошлого значения, а не только от цен.
    """
    a = atr(h, l, c, n)
    mid = (h + l) / 2.0
    up, dn = mid + k * a, mid - k * a
    line = np.full(len(c), NAN)
    dirn = np.zeros(len(c), dtype=np.int8)
    fu, fl, d = np.nan, np.nan, 1
    for i in range(len(c)):
        if np.isnan(a[i]):
            continue
        if np.isnan(fu):
            fu, fl = up[i], dn[i]
        else:
            fu = up[i] if (up[i] < fu or c[i - 1] > fu) else fu
            fl = dn[i] if (dn[i] > fl or c[i - 1] < fl) else fl
        if c[i] > fu:
            d = 1
        elif c[i] < fl:
            d = -1
        line[i] = fl if d > 0 else fu
        dirn[i] = d
    return line, dirn


def ichimoku(h, l, c, a=9, b=26, s=52):
    """Ишимоку БЕЗ сдвига облака вперёд.

    Классическое построение рисует облако на 26 баров ВПЕРЁД — на графике это
    удобно, а в бэктесте означает, что в момент i используется значение,
    посчитанное позже. Здесь облако остаётся на своём баре, и сравнение цены с
    ним честное: сравниваем с тем, что уже посчитано.
    """
    conv = (rolling_max(h, a) + rolling_min(l, a)) / 2.0
    base = (rolling_max(h, b) + rolling_min(l, b)) / 2.0
    span_a = (conv + base) / 2.0
    span_b = (rolling_max(h, s) + rolling_min(l, s)) / 2.0
    return conv, base, span_a, span_b


def psar(h, l, step=0.02, mx=0.2):
    n = len(h)
    out = np.full(n, NAN)
    dirn = np.zeros(n, dtype=np.int8)
    if n < 3:
        return out, dirn
    up = True
    af, ep, sar = step, h[0], l[0]
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, l[i - 1], l[max(0, i - 2)])
            if l[i] < sar:
                up, sar, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(mx, af + step)
        else:
            sar = max(sar, h[i - 1], h[max(0, i - 2)])
            if h[i] > sar:
                up, sar, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(mx, af + step)
        out[i] = sar
        dirn[i] = 1 if up else -1
    return out, dirn


def realized_vol(c, n=96):
    """Реализованная волатильность в долях цены за бар (сигма лог-доходности)."""
    r = np.zeros(len(c))
    r[1:] = np.log(np.maximum(c[1:], 1e-12) / np.maximum(c[:-1], 1e-12))
    return rolling_std(r, n)


def swings(h, l, n=5):
    """Свинг-экстремумы, ПОДТВЕРЖДЁННЫЕ через n баров.

    Экстремум становится известен не в момент своего появления, а только когда
    справа от него прошло n баров без перебоя. Именно так его увидел бы
    торгующий в реальном времени. Классическая ошибка разметки структуры —
    брать экстремум по центрированному окну: тогда стратегия знает вершину в
    момент вершины, и любая «структура» становится пророческой.
    """
    m = len(h)
    hi = np.full(m, NAN)
    lo = np.full(m, NAN)
    if m < 2 * n + 1:
        return hi, lo
    for i in range(n, m - n):
        c_h, c_l = h[i], l[i]
        if c_h >= h[i - n:i + n + 1].max() and c_h > h[i - n:i].max():
            hi[i + n] = c_h            # известен только через n баров
        if c_l <= l[i - n:i + n + 1].min() and c_l < l[i - n:i].min():
            lo[i + n] = c_l
    return _ffill(hi), _ffill(lo)


def _ffill(x):
    idx = np.where(~np.isnan(x), np.arange(len(x)), 0)
    np.maximum.accumulate(idx, out=idx)
    out = x[idx]
    out[np.isnan(x[idx])] = NAN
    return out


def keltner(h, l, c, n=20, k=2.0):
    m = ema(c, n)
    a = atr(h, l, c, n)
    return m, m + k * a, m - k * a
