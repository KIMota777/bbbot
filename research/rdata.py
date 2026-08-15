# -*- coding: utf-8 -*-
"""Слой данных исследования: свечи, фандинг, открытый интерес, деление выборки.

Одна точка правды по трём вещам, вокруг которых легче всего налгать себе:

1. ГРАНИЦЫ ВЫБОРКИ. TRAIN/VALIDATION/TEST заданы датами здесь и нигде больше.
   TEST не читается ни одной функцией отбора — за этим следит assert_no_test().

2. ВЫРАВНИВАНИЕ ПО ВРЕМЕНИ. Свеча помечена временем СВОЕГО НАЧАЛА (так отдаёт
   Bybit). Значит бар с меткой 12:00 на 15м закрывается в 12:15, и всё, что
   известно «на баре i», известно только к концу бара i. Отсюда правило
   движка: сигнал бара i исполняется на открытии бара i+1. Здесь это правило
   поддержано тем, что старший таймфрейм подтягивается к младшему со сдвигом:
   значение 4ч-бара становится видно только после его закрытия.

3. ФАНДИНГ И ОИ КАК РЯДЫ ПРОШЛОГО. Ставка фандинга публикуется на момент
   расчёта; использовать её раньше этого момента — заглядывание в будущее.
   as_of_series() возвращает «последнее известное к этому времени» значение.
"""
import json
import os

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(DIR, "data")
ROOT = os.path.dirname(DIR)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT"]
TFS = {"15": 15, "60": 60, "240": 240}
MS_MIN = 60_000
DAY_MS = 86_400_000

# --- деление выборки -------------------------------------------------------
# История: 2023-06-21 .. 2026-08-13 (1150 дней).
# TRAIN   740 дней  — на нём и только на нём ищутся параметры.
# VAL     244 дня   — на нём сравниваются кандидаты между собой.
# TEST    166 дней  — НЕ ТРОГАТЬ до финального прогона.
TRAIN_END_MS = 1750982400000     # 2025-06-27
VAL_END_MS = 1772064000000       # 2026-02-26
HIST_START_MS = 1687357800000    # 2023-06-21
HIST_END_MS = 1786716000000      # 2026-08-13

SPLITS = {
    "train": (HIST_START_MS, TRAIN_END_MS),
    "val": (TRAIN_END_MS, VAL_END_MS),
    "test": (VAL_END_MS, HIST_END_MS + 1),
    "trainval": (HIST_START_MS, VAL_END_MS),
    "all": (HIST_START_MS, HIST_END_MS + 1),
}

# Комиссии Bybit linear perpetual, по факту тарифа.
TAKER_FEE = 0.00055
MAKER_FEE = 0.00020
# Проскальзывание на маркет-ордере, доли цены. Разное по монетам: BTC глубокий,
# DOGE тонкий. Это база; стресс-тест умножает её.
SLIP_BPS = {
    "BTCUSDT": 0.00025, "ETHUSDT": 0.00030, "SOLUSDT": 0.00045,
    "LTCUSDT": 0.00050, "DOGEUSDT": 0.00060,
}
# Поддерживающая маржа Bybit для малых позиций (нижняя ступень риск-лимита).
MAINT_MARGIN = {
    "BTCUSDT": 0.005, "ETHUSDT": 0.005, "SOLUSDT": 0.0075,
    "LTCUSDT": 0.0075, "DOGEUSDT": 0.0075,
}

_CACHE = {}


class Bars:
    """Свечи одного символа и таймфрейма как колонки numpy.

    Держим колонками, а не списком кортежей: индикаторы считаются векторно,
    иначе перебор параметров упирается в интерпретатор.
    """

    __slots__ = ("symbol", "tf", "t", "o", "h", "l", "c", "v", "turnover")

    def __init__(self, symbol, tf, arr):
        self.symbol = symbol
        self.tf = int(tf)
        a = np.asarray(arr, dtype=np.float64)
        self.t = a[:, 0].astype(np.int64)
        self.o, self.h, self.l, self.c = a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        self.v = a[:, 5] if a.shape[1] > 5 else np.ones(len(a))
        self.turnover = a[:, 6] if a.shape[1] > 6 else self.c * self.v

    def __len__(self):
        return len(self.t)

    def slice(self, t0, t1, warmup=0):
        """Окно [t0, t1) плюс warmup баров слева на прогрев индикаторов.

        Прогрев обязателен: без него первые сделки окна считаются по
        недосчитанному индикатору, и результат окна зависит от того, где мы
        его нарезали. Прогретые бары входят в расчёт индикаторов, но сделки на
        них не открываются — за это отвечает поле `start` результата.
        """
        i0 = int(np.searchsorted(self.t, t0, "left"))
        i1 = int(np.searchsorted(self.t, t1, "left"))
        j0 = max(0, i0 - warmup)
        out = Bars.__new__(Bars)
        out.symbol, out.tf = self.symbol, self.tf
        for f in ("t", "o", "h", "l", "c", "v", "turnover"):
            setattr(out, f, getattr(self, f)[j0:i1])
        return out, i0 - j0

    def bar_ms(self):
        return self.tf * MS_MIN


def load_bars(symbol, tf="60"):
    """Свечи из двоичного кэша, а при его отсутствии — из JSON с созданием кэша.

    Двоичный кэш здесь не про удобство, а про возможность считать вообще:
    разбор семимегабайтного JSON занимает под сотню мегабайт временных
    объектов, и тринадцать процессов перебора одновременно упирались в память.
    np.load читает готовый массив без разбора текста.
    """
    key = ("bars", symbol, str(tf))
    if key in _CACHE:
        return _CACHE[key]
    npy = os.path.join(DATA, "ohlcv_%s_%s.npy" % (symbol, tf))
    if os.path.exists(npy):
        arr = np.load(npy)
    else:
        path = os.path.join(DATA, "ohlcv_%s_%s.json" % (symbol, tf))
        if not os.path.exists(path):
            raise FileNotFoundError(
                "нет %s — сначала research/fetch_ohlcv.py" % path)
        with open(path) as fh:
            rows = json.load(fh)
        arr = np.array([r for r in rows if HIST_START_MS <= r[0] <= HIST_END_MS],
                       dtype=np.float64)
        np.save(npy, arr)
    b = Bars(symbol, tf, arr)
    _check_grid(b)
    _CACHE[key] = b
    return b


def _check_grid(b):
    """Пропуски и дубли в сетке времени. Молча их терпеть нельзя.

    Дубль времени ломает любую логику «следующий бар», пропуск — растягивает
    удержание позиции и занижает стоимость фандинга. Оба случая должны быть
    видны, а не сглажены.
    """
    d = np.diff(b.t)
    step = b.tf * MS_MIN
    if len(d) and (d <= 0).any():
        raise ValueError("%s %sм: время не возрастает" % (b.symbol, b.tf))
    gaps = int((d != step).sum()) if len(d) else 0
    if gaps:
        big = d[d != step]
        b_max = int(big.max() // step)
        if gaps > len(b) * 0.01 or b_max > 96:
            raise ValueError("%s %sм: %d разрывов, максимум %d баров"
                             % (b.symbol, b.tf, gaps, b_max))


def load_funding(symbol):
    """Ставка фандинга: (моменты расчёта, ставки). Раз в 8 часов."""
    key = ("fund", symbol)
    if key in _CACHE:
        return _CACHE[key]
    path = os.path.join(ROOT, "funding_%s.json" % symbol)
    with open(path) as fh:
        rows = sorted(json.load(fh))
    t = np.array([r[0] for r in rows], dtype=np.int64)
    v = np.array([r[1] for r in rows], dtype=np.float64)
    _CACHE[key] = (t, v)
    return t, v


def load_oi(symbol):
    key = ("oi", symbol)
    if key in _CACHE:
        return _CACHE[key]
    path = os.path.join(ROOT, "oi_%s.json" % symbol)
    with open(path) as fh:
        rows = sorted(json.load(fh))
    t = np.array([r[0] for r in rows], dtype=np.int64)
    v = np.array([r[1] for r in rows], dtype=np.float64)
    _CACHE[key] = (t, v)
    return t, v


def as_of(series_t, series_v, at_t):
    """Последнее значение, ИЗВЕСТНОЕ к моменту at_t (строго раньше него).

    Именно strict less: значение, помеченное 08:00, публикуется в 08:00, и
    сделка в 08:00 не может на него опираться. searchsorted(..., 'left') - 1
    даёт ровно это. Где известного значения ещё нет — NaN, и стратегия обязана
    трактовать NaN как «фильтра нет», а не как ноль.
    """
    idx = np.searchsorted(series_t, at_t, "left") - 1
    out = np.full(len(at_t), np.nan)
    ok = idx >= 0
    out[ok] = series_v[idx[ok]]
    return out


def funding_paid(symbol, t_from, t_to, notional, side):
    """Фандинг, уплаченный позицией за время удержания, в долларах.

    Лонг платит при положительной ставке, шорт получает. Списание происходит
    в моменты расчёта, попавшие внутрь (t_from, t_to].
    """
    ft, fv = load_funding(symbol)
    i0 = np.searchsorted(ft, t_from, "right")
    i1 = np.searchsorted(ft, t_to, "right")
    if i1 <= i0:
        return 0.0
    rate = float(fv[i0:i1].sum())
    return notional * rate * (1.0 if side > 0 else -1.0)


def htf_aligned(bars, htf_bars, values):
    """Значение старшего таймфрейма, выровненное на младший БЕЗ заглядывания.

    Значение, посчитанное по 4ч-бару с меткой 12:00, становится известно
    только в 16:00 — когда бар закрылся. Поэтому берём последний старший бар,
    закрывшийся строго раньше начала младшего бара. Ошибка на один бар здесь
    даёт бесплатное знание будущего на четыре часа и рисует любой график
    прибыльным.
    """
    close_t = htf_bars.t + htf_bars.bar_ms()      # момент закрытия старшего
    idx = np.searchsorted(close_t, bars.t, "right") - 1
    out = np.full(len(bars.t), np.nan)
    ok = idx >= 0
    out[ok] = values[idx[ok]]
    return out


def split_mask(bars, split):
    t0, t1 = SPLITS[split]
    return (bars.t >= t0) & (bars.t < t1)


def assert_no_test(split):
    if split in ("test", "all"):
        raise AssertionError(
            "TEST закрыт до финального прогона (протокол, раздел 25/50)")


def describe():
    out = []
    for sym in SYMBOLS:
        try:
            b = load_bars(sym, "60")
        except FileNotFoundError:
            continue
        for name in ("train", "val", "test"):
            m = split_mask(b, name)
            out.append((sym, name, int(m.sum()),
                        int(b.t[m][0]) if m.any() else 0,
                        int(b.t[m][-1]) if m.any() else 0))
    return out


if __name__ == "__main__":
    import datetime as dt

    def d(ms):
        return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")

    print("%-9s %-6s %7s  %s" % ("символ", "часть", "баров", "период (1ч)"))
    for sym, name, n, a, z in describe():
        print("%-9s %-6s %7d  %s .. %s" % (sym, name, n, d(a), d(z)))
