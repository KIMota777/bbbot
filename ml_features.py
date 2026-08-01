# -*- coding: utf-8 -*-
"""Признаки бара для ML-проверки (постановка B: предсказание доходности).

ВСЁ СТРОГО ПРИЧИННО. Признак с индексом i считается только по барам <= i
(закрытие бара i уже известно — мы «стоим» в момент его закрытия). Цель
(forward-доходность) строится отдельно в ml_test.py и никогда не попадает
в признаки.

Проверка причинности встроена: causality_check() пересчитывает признаки на
префиксе истории и сверяет с полным прогоном — если хоть один признак
подглядывает в будущее, значения разойдутся.

Данные: свечи [ts, o, h, l, c] (evolution.fetch) + объёмы cap_vol_*.json.
Модуль ничего не пишет на диск и ничего не знает про модели.
"""

import json
import math
import os

import numpy as np

FEATURE_NAMES = None            # заполняется в build_features


def _ema(x, span):
    a = 2.0 / (span + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1.0 - a) * out[i - 1]
    return out


def _rma(x, n):
    """Сглаживание Уайлдера (для RSI/ATR)."""
    a = 1.0 / n
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1.0 - a) * out[i - 1]
    return out


def _roll_max(x, w):
    n = len(x)
    out = np.empty(n)
    for i in range(n):
        out[i] = x[max(0, i - w + 1):i + 1].max()
    return out


def _roll_min(x, w):
    n = len(x)
    out = np.empty(n)
    for i in range(n):
        out[i] = x[max(0, i - w + 1):i + 1].min()
    return out


def _roll_mean(x, w):
    c = np.concatenate(([0.0], np.cumsum(x)))
    n = len(x)
    idx = np.arange(n)
    lo = np.maximum(0, idx - w + 1)
    cnt = idx - lo + 1
    return (c[idx + 1] - c[lo]) / cnt


def _roll_std(x, w):
    m1 = _roll_mean(x, w)
    m2 = _roll_mean(x * x, w)
    return np.sqrt(np.maximum(0.0, m2 - m1 * m1))


def _roll_rank(x, w):
    """Доля баров окна (включая текущий), которые НЕ выше текущего: 0..1."""
    n = len(x)
    out = np.empty(n)
    for i in range(n):
        seg = x[max(0, i - w + 1):i + 1]
        out[i] = float((seg <= x[i]).sum()) / len(seg)
    return out


def _shift(x, k, fill=0.0):
    out = np.full(len(x), fill, dtype=float)
    if k < len(x):
        out[k:] = x[:len(x) - k]
    return out


def _ret_over(c, k):
    """Лог-доходность за k баров назад: log(c[i]/c[i-k]); на прогреве 0."""
    out = np.zeros(len(c))
    if k < len(c):
        out[k:] = np.log(c[k:] / c[:len(c) - k])
    return out


def _aroon(h, l, w):
    n = len(h)
    up = np.empty(n)
    dn = np.empty(n)
    for i in range(n):
        a = max(0, i - w + 1)
        sh, sl = h[a:i + 1], l[a:i + 1]
        m = len(sh)
        up[i] = (int(np.argmax(sh[::-1])) * -1 + m - 1 + 1) / m * 100.0
        dn[i] = (int(np.argmin(sl[::-1])) * -1 + m - 1 + 1) / m * 100.0
    return up, dn


def load_candles(symbol, interval, days=1150, folder="."):
    """Свечи из кэша проекта (сеть не трогаем)."""
    path = os.path.join(folder, f"history_{symbol}_{interval}m_{days}d.json")
    with open(path) as fh:
        rows = json.load(fh)
    return rows


def load_volume(symbol, interval, folder="."):
    """Объёмы cap_vol_*.json -> dict ts->volume. Нет файла — None."""
    path = os.path.join(folder, f"cap_vol_{symbol}_{interval}.json")
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        d = json.load(fh)
    return {int(k): float(v) for k, v in d.items()}


def build_features(candles, volume=None, bar_min=240, market_close=None):
    """Матрица признаков (n x F) + список имён.

    candles      — [[ts,o,h,l,c], ...] по возрастанию времени;
    volume       — dict ts->объём или None (объёмные признаки станут 0);
    bar_min      — длина бара в минутах (для часа суток и «баров в сутках»);
    market_close — закрытия BTC той же сетки (для относительной силы) или None.
    """
    global FEATURE_NAMES
    a = np.asarray(candles, dtype=float)
    ts, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
    n = len(c)
    day = max(1, int(1440 // bar_min))          # баров в сутках

    cols, names = [], []

    def add(name, arr):
        v = np.asarray(arr, dtype=float)
        v = np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0)
        cols.append(v)
        names.append(name)

    # ---- 1. доходности прошлого (лаги) -------------------------------
    r1 = _ret_over(c, 1)
    for k in (1, 2, 3, 6, 12, 24, 48):
        add(f"ret_{k}", _ret_over(c, k))

    # ---- 2. RSI ------------------------------------------------------
    d = np.diff(c, prepend=c[0])
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    for p in (7, 14):
        ru, rd = _rma(up, p), _rma(dn, p)
        rs = ru / np.where(rd <= 0, 1e-12, rd)
        add(f"rsi_{p}", 100.0 - 100.0 / (1.0 + rs))

    # ---- 3. ATR и его ранг -------------------------------------------
    pc = _shift(c, 1, c[0])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = _rma(tr, 14)
    atr_pct = atr / c
    add("atr_pct", atr_pct)
    add("atr_rank200", _roll_rank(atr_pct, 200))

    # ---- 4. положение в диапазоне ------------------------------------
    for w in (24, 72, 168):
        hi, lo = _roll_max(h, w), _roll_min(l, w)
        rng = np.where(hi - lo <= 0, 1e-12, hi - lo)
        add(f"range_pos_{w}", (c - lo) / rng)

    # просадка от годового максимума
    hi365 = _roll_max(h, 365 if bar_min >= 240 else 2000)
    add("dd_from_high", (c - hi365) / np.where(hi365 <= 0, 1e-12, hi365))

    # ---- 5. EMA-расстояния и наклон ----------------------------------
    e20, e50, e200 = _ema(c, 20), _ema(c, 50), _ema(c, 200)
    add("ema20_dist", (c - e20) / e20)
    add("ema50_dist", (c - e50) / e50)
    add("ema200_dist", (c - e200) / e200)
    e50p = _shift(e50, day, e50[0])
    add("ema50_slope", (e50 - e50p) / np.where(e50p <= 0, 1e-12, e50p))

    # ---- 6. волатильность --------------------------------------------
    v24, v72 = _roll_std(r1, 24), _roll_std(r1, 72)
    add("vol_24", v24)
    add("vol_72", v72)
    add("vol_ratio", v24 / np.where(v72 <= 0, 1e-12, v72))

    # ---- 7. форма бара -----------------------------------------------
    rng = np.where(h - l <= 0, 1e-12, h - l)
    add("body_frac", np.abs(c - o) / rng)
    add("dir_body", (c - o) / rng)
    add("up_wick", (h - np.maximum(o, c)) / rng)
    add("dn_wick", (np.minimum(o, c) - l) / rng)
    add("gap", (o - pc) / pc)
    add("bar_range_pct", (h - l) / c)

    # ---- 8. объём -----------------------------------------------------
    if volume:
        vol = np.array([volume.get(int(t), 0.0) for t in ts], dtype=float)
        lv = np.log1p(vol)
        m24, s24 = _roll_mean(lv, 24), _roll_std(lv, 24)
        add("vol_z24", (lv - m24) / np.where(s24 <= 0, 1e-12, s24))
        m6, m72 = _roll_mean(vol, 6), _roll_mean(vol, 72)
        add("vol_ratio_6_72", m6 / np.where(m72 <= 0, 1e-12, m72))
        add("vol_rank200", _roll_rank(vol, 200))
        add("dollar_vol_z", (np.log1p(vol * c) -
                             _roll_mean(np.log1p(vol * c), 72)))
    else:
        for nm in ("vol_z24", "vol_ratio_6_72", "vol_rank200", "dollar_vol_z"):
            add(nm, np.zeros(n))

    # ---- 9. календарь (в прошлой волне лез в топ признаков) ----------
    hour = (ts / 1000.0 / 3600.0) % 24.0
    dow = ((ts / 1000.0 / 86400.0) + 4.0) % 7.0        # 0=чт эпохи -> пн=0
    add("hour_sin", np.sin(2 * math.pi * hour / 24.0))
    add("hour_cos", np.cos(2 * math.pi * hour / 24.0))
    add("dow_sin", np.sin(2 * math.pi * dow / 7.0))
    add("dow_cos", np.cos(2 * math.pi * dow / 7.0))

    # ---- 10. MACD / Bollinger / Aroon --------------------------------
    macd = _ema(c, 12) - _ema(c, 26)
    add("macd_hist", (macd - _ema(macd, 9)) / c)
    m20, s20 = _roll_mean(c, 20), _roll_std(c, 20)
    add("bb_pos", (c - m20) / np.where(s20 <= 0, 1e-12, 2.0 * s20))
    au, ad = _aroon(h, l, 25)
    add("aroon_up", au)
    add("aroon_dn", ad)

    # ---- 11. серия однонаправленных баров ----------------------------
    streak = np.zeros(n)
    for i in range(1, n):
        s = 1.0 if c[i] > c[i - 1] else (-1.0 if c[i] < c[i - 1] else 0.0)
        streak[i] = streak[i - 1] + s if s != 0 and streak[i - 1] * s > 0 else s
    add("streak", streak)

    # ---- 12. рынок (BTC) и относительная сила -------------------------
    if market_close is not None and len(market_close) == n:
        mc = np.asarray(market_close, dtype=float)
        m24r = _ret_over(mc, 24)
        add("btc_ret_24", m24r)
        add("btc_ret_6", _ret_over(mc, 6))
        add("rel_str_24", _ret_over(c, 24) - m24r)
    else:
        add("btc_ret_24", np.zeros(n))
        add("btc_ret_6", np.zeros(n))
        add("rel_str_24", np.zeros(n))

    X = np.column_stack(cols)
    FEATURE_NAMES = names
    return X, names


def warmup_bars(bar_min=240):
    """Сколько первых баров выбрасываем (прогрев самых длинных окон).

    Самые длинные окна: hi365 (365 баров на 4ч / 2000 на 15м), ema200,
    atr_rank200. Берём с запасом, чтобы вклад начального значения EMA был
    пренебрежимо мал."""
    return 2400 if bar_min < 240 else 500


def causality_check(candles, volume=None, bar_min=240, cut=None, tol=1e-9):
    """Пересчёт признаков на префиксе [0..cut) и сверка с полным прогоном.

    Возвращает (ok, max_abs_diff, worst_feature)."""
    n = len(candles)
    cut = cut or int(n * 0.6)
    Xf, names = build_features(candles, volume, bar_min)
    Xp, _ = build_features(candles[:cut], volume, bar_min)
    # сверяем последние 50 строк префикса — именно там будущего «нет»
    a = Xp[cut - 50:cut]
    b = Xf[cut - 50:cut]
    diff = np.abs(a - b)
    j = int(np.unravel_index(np.argmax(diff), diff.shape)[1])
    mx = float(diff.max())
    return mx <= tol, mx, names[j]
