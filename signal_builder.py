# -*- coding: utf-8 -*-
"""КОНСТРУКТОР СИГНАЛОВ: пользователь описывает идею — система честно её мерит.

ЗАЧЕМ. За 14 волн исследования ни один сигнальный сетап проекта не подтвердил
преимущества, а из пяти ботов honest-проверку пережили два (+0.5%/мес). Главный
вывод не «мы плохо искали», а «почти всё, что выглядит как закономерность, ею
не является»: случайный геном проходит наш порог в 1.4% случаев, отобранные —
в 25%, но при смене зерна побеждают ДРУГИЕ конфиги. Поэтому ценность этого
модуля не в том, что он даёт собрать ещё одну идею, а в том, что он даёт её
БЫСТРО ОПРОВЕРГНУТЬ. Красивый бэктест здесь — не результат; результат — это
вердикт после холдоута, случайного контроля, возмущений и поправки на число
проверенных вариантов.

ЧТО ЭТО ТАКОЕ
  spec (JSON) -> build_run(spec)  -> прогон, совместимый с signal_stats.full_stats
  spec (JSON) -> evaluate(spec)   -> честная проверка: train / holdout /
                                     случайный контроль / buy&hold / значимость /
                                     риск / устойчивость / вердикт / оговорки
  describe()                      -> машинная схема языка (контракт для UI)

ЧЕМ Я НЕ ЗАНИМАЮСЬ (принципиально). Механику сделки — вход по открытию первой
15м-свечи после закрытия сигнального бара, сетку усреднения, безубыток,
трейлинг, ликвидацию по средней цене, издержки (taker 0.055% / maker 0.02% /
слип 0.03% / фандинг 0.01% за 8ч) — целиком ведёт signal_engine3
(_make_trade -> simulate_grid_trade). Здесь НЕТ своей арифметики сделок: иначе
цифры конструктора разошлись бы с остальным проектом, и сравнивать их было бы
не с чем. Мой слой — только УСЛОВИЯ ВХОДА, стоп-правило, сборка прогона и
честная проверка. Тест test_builder.py это фиксирует: spec, повторяющий
dump_long из DEFAULTS3, обязан дать те же сделки, что signal_engine3.run_setup.

ПРАВИЛА ЧЕСТНОСТИ (нарушение = брак, тест их проверяет)
  1. ПРИЧИННОСТЬ. Каждый ряд условия на баре i считается только по данным <= i.
     Пивоты подтверждаются правыми барами, лежащими в прошлом (пивот на баре j
     виден с бара j+piv), режим рынка берётся по ВЧЕРАШНЕМУ дню, funding — та
     ставка, что уже рассчитана к закрытию бара. Проверка: маска входов,
     посчитанная на префиксе истории, обязана совпасть с маской на полной
     истории бит в бит (test_builder, раздел «lookahead»).
  2. ХОЛДОУТ. Последние 28% баров (HOLD_FRAC=0.72) — экзамен. Он не участвует
     ни в каком подборе; всё, что делает пользователь, он делает глядя на
     train, а вердикт выносится по holdout.
  3. КОНТРОЛЬ СЛУЧАЙНЫМ ВХОДОМ. Те же выходы, то же число входов, но бары
     выбраны наугад, 40 зёрен. Если случайный вход не хуже — измерена
     волатильность рынка, а не идея.
  4. ПОПРАВКА НА МНОЖЕСТВЕННОСТЬ. Пользователь перебирает варианты; p без
     поправки врёт. Поле spec["tried_variants"] (или аргумент tried=) умножает
     p на число просмотренных вариантов (Бонферрони) — это надо честно
     заполнять, иначе значимость декоративная.
  5. УСТОЙЧИВОСТЬ. 30 возмущений числовых параметров +-10%. Настоящая
     закономерность — плато (соседи дают похожее), подгонка — шпиль.
  6. СДЕЛКА «В НОЛЬ» — НЕ ПОБЕДА. Перенос стопа в безубыток закрывает сделку
     на +0.001$: winrate растёт, денег нет. У ботов проекта так закрывалось
     до 89% циклов (SOL: winrate 98% против честных 78%, весь плюс +9.16$
     оказался артефактом — без пустых сделок -8.58$). Поэтому «в ноль» =
     |PnL| < 1% маржи цикла ($0.05) считается отдельным классом (is_tiny,
     tiny_stats), а доля таких сделок выше MAX_TINY_SHARE=40% закрывает
     вердикт «работает» независимо от остальных цифр.
  7. ОТРИЦАТЕЛЬНЫЙ РЕЗУЛЬТАТ — ВАЛИДНЫЙ. Вердикт «не подтверждён» это ответ,
     а не повод крутить пороги.

ЯЗЫК ОПИСАНИЯ (полная схема — describe(); пример — EXAMPLE_SPEC)
{
  "symbol": "BTCUSDT", "interval": "240", "side": "long",
  "entry": [ {"kind": "rsi", "period": 14, "op": "<", "value": 30}, ... ],
  "exit":  {"stop": {"type": "atr", "value": 2.0},
            "tp":   {"type": "r",   "value": 2.0},
            "trail": {"bars": 60},            # необязательно; заменяет tp
            "be_after_r": 0.0, "timeout_days": 30,
            "max_stop_pct": 6.0},
  "grid":  {"levels": 1, "step_atr": 1.0, "mult": 1.5},
  "lev": 5, "cooldown_bars": 0, "tried_variants": 1
}
Все условия entry соединяются логическим И. Неизвестное условие или параметр
вне диапазона -> SpecError с текстом по-русски (движок не падает молча).

ЕДИНИЦЫ (одинаково осмысленны на BTC и на DOGE)
  ATR   — ATR(14) баров СВОЕГО ТФ в долях цены (ctx["atr_bar"]);
  pct   — проценты (6 = 6%), НЕ доли;
  days  — сутки (переводятся в бары своего ТФ);
  atr_rank — перцентиль дневного ATR за 90 суток, 0..1;
  funding  — %/8ч (0.01 = 0.01%), как в ext_data;
  час/день недели — UTC, момент ЗАКРЫТИЯ сигнального бара (= момент входа),
  день недели 0=понедельник.

Запуск проверок: python test_builder.py
"""

import bisect
import heapq
import json
import math
import os
import random
import statistics
import time

import signal_engine2 as se2
import signal_engine3 as se3
import signal_stats as ss

BASE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------ константы
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT"]
DAYS = 1150                     # глубина кэша свечей проекта
HOLD_FRAC = 0.72                # холдоут = последние 28% баров (как везде)
START = se3.START               # 20$ база бэктеста
MARGIN = se3.MARGIN             # 5$ маржи на цикл (25% базы)
MIN_STOP = se3.MIN_STOP         # 0.5% — уже стопа не бывает
DEF_MAX_STOP_PCT = 6.0          # потолок ширины стопа по умолчанию, % цены
MONTH_MS = se3.MONTH_MS

# --------- ПОРОГИ ВЕРДИКТА (объявлены здесь, а не подкручиваются по факту) ---
MIN_TRADES = 20                 # holdout: меньше 20 сделок — выводов нет
MIN_EXP_R = 0.0                 # ожидание в R должно быть положительным
MIN_PF = 1.2                    # профит-фактор
MAX_CTRL_SHARE = 0.20           # доля зёрен случайного контроля «не хуже»
MIN_ROBUST_POS = 0.60           # доля прибыльных возмущений генома
MAX_P_ADJ = 0.05                # значимость с поправкой на число попыток
MAX_ABS_R = 20.0                # предел |R| одной сделки: выше — искажение
                                # шкалы безубытком, а не реальная прибыль
# --- «ПУСТЫЕ» СДЕЛКИ (закрытые в ноль) ---
# Перенос стопа в безубыток закрывает сделку на +0.001$: формально победа,
# фактически ноль. У ботов проекта таких циклов до 89% (SOL), и winrate из-за
# них раздувался с 83% до 98%, а весь «заработок» оказывался артефактом
# (SOL без них -8.58$ вместо +9.16$). Здесь ловим ровно это: если пустых
# сделок больше MAX_TINY_SHARE, winrate/exp_r меряют не торговлю, а перенос
# стопа, и вердикт «работает» невозможен.
TINY_FRAC = 0.01                # «в ноль» = |PnL| < 1% маржи цикла
TINY_USD = round(MARGIN * TINY_FRAC, 4)   # = 0.05$ при марже 5$: это примерно
                                # половина издержек одного цикла, то есть шум
MAX_TINY_SHARE = 0.40           # доля пустых сделок, выше которой вердикт
                                # «работает» невозможен ни при каких цифрах
N_CTRL_SEEDS = 40               # зёрен случайного контроля
N_PERT = 30                     # возмущений параметров
PERT = 0.10                     # +-10%
CTRL_SEED0 = 20260731
PERT_SEED = 4242

# --------- параметры риска ---------
BOOT_N = 400                    # траекторий блочного бутстрапа
BOOT_BLOCK = (5, 15)            # длина блока в сделках (сохраняет серии)
RUIN_LEVEL = 0.20               # «слив» = капитал ниже 20% старта
HALF_LEVEL = 0.50
DD_BUDGET = 0.20                # готовы отдать под просадку 20% депозита
CONC_SHARE = 0.89               # «89% денег дают N сделок» — как в отчётах
NEAR_CAP = 300                  # максимум симуляций near-miss на прогон

LONG, SHORT = "long", "short"


class SpecError(ValueError):
    """Ошибка описания сетапа. Текст — по-русски и с указанием места."""


# ============================================================ данные и кэш
_SERIES = {}          # (symbol, interval) -> словарь серии
_VOLCACHE = {}        # (symbol, interval) -> [объём на бар] или None


def _hist_path(symbol, interval, days=DAYS):
    return os.path.join(BASE, f"history_{symbol}_{interval}m_{days}d.json")


def available_data():
    """Какие пары (монета, ТФ) реально есть в кэше свечей. Скачивать данные
    из конструктора нельзя: страница сайта не должна висеть минуты на сети,
    поэтому недостающий ТФ — это понятная ошибка, а не тихая загрузка."""
    out = {}
    for sym in SYMBOLS:
        ivs = [iv for iv in ("240", "60", "15")
               if os.path.exists(_hist_path(sym, iv))]
        if os.path.exists(_hist_path(sym, "15")):
            out[sym] = ivs
    return out


def load_series(symbol, interval):
    """Свечи сигнального ТФ + 15м-серия исполнения + контекст se3.

    Всё кэшируется в модуле: evaluate делает десятки прогонов, и повторное
    чтение 110к 15м-баров съело бы весь бюджет времени."""
    symbol = str(symbol).upper()
    interval = str(interval)
    key = (symbol, interval)
    if key in _SERIES:
        return _SERIES[key]
    if symbol not in SYMBOLS:
        raise SpecError(f"монета {symbol} не поддерживается; доступны: "
                        f"{', '.join(SYMBOLS)}")
    path = _hist_path(symbol, interval)
    if not os.path.exists(path):
        have = available_data().get(symbol, [])
        raise SpecError(f"нет кэша свечей {symbol} {interval}м; для этой монеты "
                        f"доступны интервалы: {', '.join(have) or 'нет'}")
    with open(path, encoding="utf-8") as fh:
        c4 = json.load(fh)
    if interval == "15":
        c15 = c4
    else:
        p15 = _hist_path(symbol, "15")
        if not os.path.exists(p15):
            raise SpecError(f"нет 15м-кэша {symbol} — без него нельзя честно "
                            f"исполнять сделки")
        with open(p15, encoding="utf-8") as fh:
            c15 = json.load(fh)
    ser = series_from_candles(symbol, interval, c4, c15)
    _SERIES[key] = ser
    return ser


def series_from_candles(symbol, interval, c4, c15=None):
    """Серия из ПРОИЗВОЛЬНЫХ свечей и БЕЗ кэша.

    Нужна проверке причинности: там история обрезается префиксом, и весь
    контекст обязан пересчитаться заново — иначе тест проверял бы кэш, а не
    заглядывание вперёд."""
    interval = str(interval)
    iv_min = int(interval)
    if c15 is None:
        c15 = c4
    ser = dict(
        symbol=symbol, interval=interval, interval_min=iv_min,
        c4=c4, c15=c15, ts15=[c[0] for c in c15],
        bar_ms=se2.bar_ms_of(iv_min), n_day=se2.bars_per_day(iv_min),
        ctx=se3.prep_context(c4, interval_min=iv_min, symbol=symbol),
        n=len(c4), memo={},
    )
    ser["closes"] = ser["ctx"]["closes"]
    ser["hold_i"] = int(len(c4) * HOLD_FRAC)
    return ser


def clear_cache():
    """Сброс модульного кэша (для тестов причинности: там серия строится на
    ОБРЕЗАННОЙ истории и не должна брать готовые ряды полной)."""
    _SERIES.clear()
    _VOLCACHE.clear()


def _memo(ser, key, fn):
    """Кэш производных рядов внутри серии (EMA/SMA/экстремумы/RSI, профиль
    объёма...). Возмущения параметров дают мало разных целых периодов, поэтому
    попаданий много.

    Размер ограничен ПО ПАМЯТИ, а не по числу записей: один ряд на 15м-истории
    это 110 тысяч значений (~3 МБ), и лимит «400 записей» означал бы больше
    гигабайта. Вытеснение — самое давно не использованное."""
    m = ser["memo"]
    if key in m:
        m[key] = m.pop(key)              # освежаем позицию (LRU)
        return m[key]
    v = fn()
    m[key] = v
    limit = max(24, int(2_500_000 / max(1, ser["n"])))
    while len(m) > limit:
        m.pop(next(iter(m)))
    return v


# ------------------------------------------------------------- объём (для volume/POC)
def _vol_by_ts(sym, iv, bar_ms):
    """{ts бара: объём} — сырой источник объёма.

    В кэше свечей проекта объёма нет ([ts,o,h,l,c]), поэтому берём:
      1) volcache_{sym}_{iv}m_1150d.json   — [ts,o,h,l,c,volume,turnover];
      2) cap_vol_{sym}_{iv}.json           — {ts: base_volume};
      3) агрегируем из более мелкого ТФ (60м/15м) — суммой внутри бара.
    Ничего не скачиваем и чужие файлы только ЧИТАЕМ."""
    key = (sym, iv)
    if key in _VOLCACHE:
        return _VOLCACHE[key]
    by_ts = None
    p = os.path.join(BASE, f"volcache_{sym}_{iv}m_{DAYS}d.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            rows = json.load(fh)
        by_ts = {int(r[0]): float(r[6] if len(r) > 6 else r[5]) for r in rows}
    if by_ts is None:
        p = os.path.join(BASE, f"cap_vol_{sym}_{iv}.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                raw = json.load(fh)
            by_ts = {int(k): float(v) for k, v in raw.items()}
    if by_ts is None:                       # агрегация из мелкого ТФ
        for small in ("60", "15"):
            ps = os.path.join(BASE, f"volcache_{sym}_{small}m_{DAYS}d.json")
            if int(small) >= int(iv) or not os.path.exists(ps):
                continue
            with open(ps, encoding="utf-8") as fh:
                rows = json.load(fh)
            agg = {}
            for r in rows:
                b = (int(r[0]) // bar_ms) * bar_ms
                agg[b] = agg.get(b, 0.0) + float(r[6] if len(r) > 6 else r[5])
            by_ts = agg
            break
    _VOLCACHE[key] = by_ts
    return by_ts


def volume_series(ser):
    """Объём на каждый бар СВОЕЙ серии (None, если данных нет вовсе).
    Выравнивание идёт по времени бара, поэтому обрезанная история (тест
    причинности) получает ровно свой кусок, а не чужой длинный ряд."""
    def build():
        by_ts = _vol_by_ts(ser["symbol"], ser["interval"], ser["bar_ms"])
        if by_ts is None:
            return None
        return [by_ts.get(c[0]) for c in ser["c4"]]
    return _memo(ser, ("volume",), build)


# ====================================================== индикаторы (причинные)
def _rsi(ser, period):
    period = int(period)
    pre = ser["ctx"]["rsi"].get(period)
    if pre is not None:
        return pre
    return _memo(ser, ("rsi", period),
                 lambda: se2.e2.bg.calc_rsi(ser["closes"], period))


def _ema(ser, period):
    return _memo(ser, ("ema", int(period)),
                 lambda: se2.calc_ema(ser["closes"], int(period)))


def _sma(ser, period):
    return _memo(ser, ("sma", int(period)),
                 lambda: se2.calc_sma(ser["closes"], int(period)))


def _trail_ext(ser, n):
    return _memo(ser, ("tex", int(n)),
                 lambda: se2.trailing_extremes(ser["c4"], int(n)))


def _lagged_ext(ser, n):
    return _memo(ser, ("lex", int(n)),
                 lambda: se2.rolling_extremes_lagged(ser["c4"], int(n), 1))


def _ema_skip_none(vals, period):
    """EMA по ряду, у которого начало — None (нужно для сигнальной линии MACD).
    Первый определённый элемент — среднее первых `period` значений."""
    out = [None] * len(vals)
    idx = [i for i, v in enumerate(vals) if v is not None]
    if len(idx) < period:
        return out
    k = 2.0 / (period + 1)
    s = sum(vals[i] for i in idx[:period]) / period
    out[idx[period - 1]] = s
    for i in idx[period:]:
        s = vals[i] * k + s * (1 - k)
        out[i] = s
    return out


def _macd(ser, fast, slow, signal):
    def build():
        ef, es = _ema(ser, fast), _ema(ser, slow)
        line = [None if (ef[i] is None or es[i] is None) else ef[i] - es[i]
                for i in range(len(ef))]
        sig = _ema_skip_none(line, int(signal))
        hist = [None if (line[i] is None or sig[i] is None)
                else line[i] - sig[i] for i in range(len(line))]
        return line, sig, hist
    return _memo(ser, ("macd", int(fast), int(slow), int(signal)), build)


def _roll_median_prev(vals, w):
    """Медиана ПРЕДЫДУЩИХ w значений (текущий бар не входит — иначе всплеск
    объёма частично сравнивался бы сам с собой)."""
    n = len(vals)
    out = [None] * n
    buf = []
    for i in range(n):
        if len(buf) >= w:
            out[i] = buf[len(buf) // 2] if len(buf) % 2 else \
                (buf[len(buf) // 2 - 1] + buf[len(buf) // 2]) / 2.0
        v = vals[i]
        if v is not None:
            bisect.insort(buf, v)
            if len(buf) > w:
                old = vals[i - w]
                if old is not None:
                    j = bisect.bisect_left(buf, old)
                    if j < len(buf) and buf[j] == old:
                        buf.pop(j)
    return out


def _pivots(ser, piv):
    """Подтверждённые пивоты. pivot_low на баре j — минимум окна
    [j-piv, j+piv]; ПОДТВЕРЖДАЕТСЯ на баре j+piv, значит на баре i известны
    только пивоты с j <= i-piv. Заглядывания нет.
    -> (lows, highs), где lows[i] = список уровней, известных на баре i
    (храним как отсортированный список уровней, накопительно)."""
    def build():
        c4 = ser["c4"]
        n = len(c4)
        lo_at = [[] for _ in range(n)]     # уровни, ставшие известными на i
        hi_at = [[] for _ in range(n)]
        p = int(piv)
        for j in range(p, n - p):
            l = c4[j][3]
            h = c4[j][2]
            is_lo = all(c4[k][3] >= l for k in range(j - p, j + p + 1))
            is_hi = all(c4[k][2] <= h for k in range(j - p, j + p + 1))
            if is_lo:
                lo_at[j + p].append(l)
            if is_hi:
                hi_at[j + p].append(h)
        return lo_at, hi_at
    return _memo(ser, ("piv", int(piv)), build)


def _nearest_pivot(ser, piv, kind):
    """Ближайший к цене подтверждённый пивот на каждом баре (уровень поддержки
    снизу для kind='low', сопротивления сверху для kind='high')."""
    def build():
        lo_at, hi_at = _pivots(ser, piv)
        src = lo_at if kind == "low" else hi_at
        c4 = ser["c4"]
        known = []
        out = [None] * len(c4)
        for i in range(len(c4)):
            for lvl in src[i]:
                bisect.insort(known, lvl)
            if not known:
                continue
            c = c4[i][4]
            j = bisect.bisect_left(known, c)
            if kind == "low":
                out[i] = known[j - 1] if j > 0 else known[0]
            else:
                out[i] = known[j] if j < len(known) else known[-1]
        return out
    return _memo(ser, ("npiv", int(piv), kind), build)


LOG_STEP = 0.005          # корзина профиля объёма: 0.5% цены (как в cap_levels)


def _bar_buckets(ser):
    """Разбиение КАЖДОГО бара на лог-корзины (индекс корзины, доля объёма).
    Не зависит от окна, поэтому считается один раз на серию — иначе профиль
    объёма пересчитывался бы с нуля на каждом возмущении параметра."""
    def build():
        vols = volume_series(ser)
        if vols is None:
            return None
        out = []
        for i, row in enumerate(ser["c4"]):
            v = vols[i]
            h, l = row[2], row[3]
            if not v or l <= 0 or h <= 0:
                out.append(())
                continue
            b0 = int(math.floor(math.log(l) / LOG_STEP))
            b1 = int(math.floor(math.log(h) / LOG_STEP))
            part = v / (b1 - b0 + 1)
            out.append(tuple((b, part) for b in range(b0, b1 + 1)))
        return out
    return _memo(ser, ("buckets",), build)


def _poc(ser, window):
    """POC (цена максимального объёма) по скользящему окну window баров,
    только бары <= i. Объём бара размазан равномерно по лог-корзинам между
    его low и high — стандартное приближение без тиковых данных.

    Максимум держим ЛЕНИВОЙ кучей: прямой max() по корзинам на каждом баре
    стоил 47 секунд из 59 на 110 тысячах 15м-баров (профилировано), а куча с
    отложенным удалением устаревших записей делает то же самое за секунды.
    Результат идентичен — это чистая оптимизация, не смена метода."""
    def build():
        bk = _bar_buckets(ser)
        if bk is None:
            return None
        n = len(bk)
        w = max(1, int(window))
        cnt = {}
        heap = []
        out = [None] * n
        push = heapq.heappush
        for i in range(n):
            for b, a in bk[i]:
                v = cnt.get(b, 0.0) + a
                cnt[b] = v
                push(heap, (-v, b))
            j = i - w
            if j >= 0:
                for b, a in bk[j]:
                    v = cnt.get(b, 0.0) - a
                    if v <= 1e-9:
                        cnt.pop(b, None)
                    else:
                        cnt[b] = v
                        push(heap, (-v, b))
            if i >= w - 1 and cnt:
                while heap and -heap[0][0] != cnt.get(heap[0][1]):
                    heapq.heappop(heap)
                if heap:
                    out[i] = math.exp((heap[0][1] + 0.5) * LOG_STEP)
                if len(heap) > 8 * len(cnt) + 256:      # чистка мусора
                    heap = [(-v, b) for b, v in cnt.items()]
                    heapq.heapify(heap)
        return out
    return _memo(ser, ("poc", int(window)), build)


def _round_level(price):
    """Ближайший «круглый» уровень. Шаг = 10^floor(log10(цена)-1), то есть
    1..10% цены на любой монете: BTC 60000 -> шаг 1000, DOGE 0.15 -> 0.01."""
    if price <= 0:
        return None
    step = 10.0 ** math.floor(math.log10(price) - 1)
    return round(price / step) * step


# ================================================== реестр условий (контракт)
def _P(t, **kw):
    d = dict(type=t)
    d.update(kw)
    return d


COND_DEFS = {
    "rsi": dict(
        title="RSI", doc="RSI периода period сравнить с value",
        near_tol=5.0,
        params=dict(
            period=_P("int", min=2, max=200, default=14, perturb=True),
            op=_P("enum", values=["<", ">"], default="<"),
            value=_P("float", min=0.0, max=100.0, default=30.0, perturb=True))),
    "drop": dict(
        title="падение", doc="цена упала на pct% за days суток",
        near_tol=None,
        params=dict(
            pct=_P("float", min=0.1, max=90.0, default=6.0, perturb=True,
                   doc="в ПРОЦЕНТАХ (6 = 6%)"),
            days=_P("float", min=0.25, max=90.0, default=2.0, perturb=True))),
    "rise": dict(
        title="рост", doc="цена выросла на pct% за days суток",
        near_tol=None,
        params=dict(
            pct=_P("float", min=0.1, max=90.0, default=6.0, perturb=True),
            days=_P("float", min=0.25, max=90.0, default=2.0, perturb=True))),
    "ema": dict(
        title="EMA/EMA", doc="быстрая EMA относительно медленной",
        params=dict(
            fast=_P("int", min=2, max=400, default=50, perturb=True),
            slow=_P("int", min=3, max=800, default=200, perturb=True),
            op=_P("enum", values=["above", "below", "cross_up", "cross_down"],
                  default="above"))),
    "ema_price": dict(
        title="цена/EMA", doc="закрытие выше или ниже EMA(period)",
        near_tol=0.01,
        params=dict(
            period=_P("int", min=2, max=800, default=200, perturb=True),
            op=_P("enum", values=["above", "below"], default="above"))),
    "macd": dict(
        title="MACD", doc="гистограмма растёт/падает или пересечение сигнальной",
        params=dict(
            fast=_P("int", min=2, max=200, default=12, perturb=True),
            slow=_P("int", min=3, max=400, default=26, perturb=True),
            signal=_P("int", min=2, max=100, default=9, perturb=True),
            cond=_P("enum",
                    values=["hist_up", "hist_down", "cross_up", "cross_down"],
                    default="hist_up"))),
    "breakout": dict(
        title="пробой экстремума", near_tol=0.5,
        doc="закрытие за экстремумом days суток (окно кончается на i-1)",
        params=dict(
            days=_P("float", min=0.25, max=200.0, default=20.0, perturb=True),
            dir=_P("enum", values=["high", "low"], default="high"))),
    "candle": dict(
        title="свеча", doc="направление свечи и минимальное тело",
        near_tol=0.15,
        params=dict(
            dir=_P("enum", values=["green", "red"], default="green"),
            body_min=_P("float", min=0.0, max=1.0, default=0.0, perturb=True,
                        doc="тело/(хай-лоу), 0 = не проверять"))),
    "regime": dict(
        title="режим рынка", doc="режим по evolution6.calc_regime (вчерашний день)",
        params=dict(
            allow=_P("list_enum", values=["bull", "range", "bear"],
                     default=["bull", "range", "bear"]))),
    "atr_rank": dict(
        title="перцентиль ATR", near_tol=0.10,
        doc="перцентиль дневного ATR среди последних 90 суток, 0..1",
        params=dict(
            op=_P("enum", values=["<", ">"], default=">"),
            value=_P("float", min=0.0, max=1.0, default=0.5, perturb=True))),
    "volume": dict(
        title="объём", near_tol=0.25,
        doc="объём бара >= mult * медианы предыдущих window баров",
        params=dict(
            mult=_P("float", min=0.5, max=10.0, default=1.5, perturb=True),
            window=_P("int", min=5, max=500, default=50, perturb=True))),
    "funding": dict(
        title="funding", near_tol=0.01,
        doc="ставка фандинга, %/8ч (0.01 = 0.01%); известна на закрытии бара",
        params=dict(
            op=_P("enum", values=["<", ">"], default="<"),
            value=_P("float", min=-0.5, max=0.5, default=0.0, perturb=True))),
    "level_dist": dict(
        title="близко к уровню", near_tol=0.5,
        doc="расстояние от закрытия до уровня <= max_atr ATR. ВНИМАНИЕ: тип "
            "уровня называется 'level', а не 'kind' — ключ 'kind' занят типом "
            "самого условия (в форме {\"level_dist\": {...}} принимается и "
            "'kind')",
        params=dict(
            level=_P("enum", values=["swing_low", "swing_high", "round",
                                     "volume_poc"], default="swing_low"),
            max_atr=_P("float", min=0.05, max=10.0, default=1.0, perturb=True),
            pivot=_P("int", min=2, max=50, default=5, perturb=True,
                     doc="плечо пивота для swing_*"),
            window=_P("int", min=20, max=1000, default=120, perturb=True,
                      doc="окно профиля объёма для volume_poc"))),
    "divergence": dict(
        title="дивергенция RSI",
        doc="новый экстремум цены без нового экстремума RSI за period баров. "
            "Направление называется 'dir' (ключ 'kind' занят типом условия)",
        params=dict(
            period=_P("int", min=5, max=300, default=40, perturb=True),
            dir=_P("enum", values=["bull", "bear"], default="bull"),
            rsi_period=_P("int", min=2, max=100, default=14, perturb=True))),
    "hour": dict(
        title="час входа (UTC)", doc="час закрытия сигнального бара в [from, to]",
        params=dict(**{"from": _P("int", min=0, max=23, default=0),
                       "to": _P("int", min=0, max=23, default=23)})),
    "weekday": dict(
        title="день недели (UTC)", doc="0=понедельник ... 6=воскресенье",
        params=dict(days=_P("list_int", min=0, max=6,
                            default=[0, 1, 2, 3, 4, 5, 6]))),
}

EXIT_DEFS = dict(
    stop=dict(doc="стоп: atr = value*ATR, pct = value% цены, swing = за "
                  "экстремумом value баров + buf_atr*ATR",
              params=dict(
                  type=_P("enum", values=["atr", "pct", "swing"],
                          default="atr"),
                  value=_P("float", min=0.1, max=60.0, default=2.0,
                           perturb=True),
                  buf_atr=_P("float", min=0.0, max=3.0, default=0.2,
                             perturb=True, doc="только для swing"))),
    tp=dict(doc="тейк: r = value*риск, atr = value*ATR, pct = value% цены",
            params=dict(
                type=_P("enum", values=["r", "atr", "pct"], default="r"),
                value=_P("float", min=0.1, max=30.0, default=2.0,
                         perturb=True))),
    trail=dict(doc="трейлинг за экстремумом bars баров своего ТФ; ЗАМЕНЯЕТ tp",
               optional=True,
               params=dict(bars=_P("int", min=2, max=400, default=60,
                                   perturb=True))),
    be_after_r=_P("float", min=0.0, max=5.0, default=0.0, perturb=True,
                  doc="перенос стопа в безубыток после X R (0 = выкл)"),
    timeout_days=_P("float", min=1.0, max=120.0, default=30.0, perturb=True,
                    doc="предельное удержание, суток (движок считает целыми "
                        "сутками, дробь округляется)"),
    max_stop_pct=_P("float", min=0.5, max=15.0, default=DEF_MAX_STOP_PCT,
                    perturb=True, doc="потолок ширины стопа, % цены"),
)

GRID_DEFS = dict(
    levels=_P("int", min=1, max=4, default=1, doc="1 = обычный одиночный вход"),
    step_atr=_P("float", min=0.1, max=5.0, default=1.0, perturb=True),
    mult=_P("float", min=1.0, max=3.0, default=1.5, perturb=True),
)

ROOT_DEFS = dict(
    symbol=_P("enum", values=SYMBOLS, default="BTCUSDT"),
    interval=_P("enum", values=["240", "60", "15"], default="240"),
    side=_P("enum", values=[LONG, SHORT], default=LONG),
    lev=_P("int", min=1, max=25, default=5),
    cooldown_bars=_P("int", min=0, max=200, default=0, perturb=True),
    tried_variants=_P("int", min=1, max=10000, default=1,
                      doc="сколько вариантов пользователь уже посмотрел — "
                          "поправка на множественность (Бонферрони)"),
)

EXAMPLE_SPEC = {
    "symbol": "BTCUSDT", "interval": "240", "side": "long",
    "entry": [
        {"kind": "drop", "pct": 6.0, "days": 2},
        {"kind": "rsi", "period": 14, "op": "<", "value": 30},
        {"kind": "candle", "dir": "green", "body_min": 0.0},
    ],
    "exit": {"stop": {"type": "atr", "value": 2.0},
             "tp": {"type": "r", "value": 2.0},
             "be_after_r": 0.0, "timeout_days": 30, "max_stop_pct": 6.0},
    "grid": {"levels": 1, "step_atr": 1.0, "mult": 1.5},
    "lev": 5, "cooldown_bars": 0, "tried_variants": 1,
}


# Формат результата evaluate() — контракт для страницы. Держится рядом с
# кодом, чтобы UI не гадал по примеру, а читал перечень полей.
RESULT_DOC = {
    "spec": "нормализованный spec (со всеми умолчаниями)",
    "tried_variants": "сколько вариантов учтено в поправке на множественность",
    "data": "symbol, interval, bars, warm (прогрев в барах), hold_i (граница "
            "холдоута), train_from/train_to/holdout_from/holdout_to (мс), "
            "entry_bars_train/entry_bars_holdout (баров с сигналом)",
    "train": "метрики на [0..72%): tiny_n/tiny_share/tiny_thr_usd (сделки "
             "«в ноль»: |PnL| < 1% маржи цикла), tiny_win_n/tiny_loss_n, "
             "real_win_n/real_loss_n, wr_honest (в ноль — не победа), "
             "wr_ex_tiny (только настоящие сделки), pnl_tiny_usd, "
             "pnl_without_tiny, ret_pct_ex_tiny, be_n/be_share (перенос стопа "
             "в безубыток), avg_tiny_usd, n, wins, wr, wr_breakeven, exp_r, exp_usd, "
             "sum_r, pnl_usd, pf, ret_pct, dd_pct, months, tpm, avg_win_r, "
             "avg_loss_r, best_r, worst_r, max_loss_streak, avg_stop_pct, "
             "avg_atr_pct, avg_hold_h, ruined, signals, bars_eval, executed, "
             "blocked_busy, blocked_cooldown, outcomes{tp,stop,liq,timeout}, "
             "outcome_r, months_pos/months_total, reinvest_pct, "
             "avg_grid_fills, period_start/period_end, near_n, near_avg_r + "
             "кривые equity/drawdown/monthly/by_month/r_hist/by_regime/"
             "rejects/warnings_engine (при curves=True)",
    "holdout": "то же на [72%..100%) — ИМЕННО по нему выносится вердикт",
    "random_control": "seeds, entries_per_seed, median_exp_r, p10/p90_exp_r, "
                      "median_ret_pct, median_trades, user_exp_r, "
                      "share_not_worse (доля зёрен не хуже пользователя)",
    "buyhold": "ret_pct, per_month_pct, months, short_ret_pct, price_start/end",
    "significance": "n, mean_r, sd_r, t, p, p_adj (Бонферрони), ci[2], tried",
    "significance_all": "то же по train+holdout вместе",
    "risk": "max_dd_pct/usd, liquidations, ruined, worst_trade_r, "
            "max_loss_streak, avg_risk_usd, liq_frac_pct, stop_vs_liq, "
            "capital_required_usd, capital_required_p95_usd, bootstrap{"
            "ruin_pct, half_pct, final_p5/p50/p95, dd_p50_pct, dd_p95_pct}",
    "robustness": "n, pert_pct, median_exp_r, median_ret_pct, p10/p90_ret_pct, "
                  "share_positive, median_trades, n_empty, base_ret_pct",
    "costs": "net_usd, gross_usd (без издержек), cost_usd, "
             "share_of_gross_profit_pct",
    "concentration": "n_for_share (сколько сделок дают 89% прибыли), "
                     "top3_share_pct, n_pos",
    "entry_conditions": "[{label, kind, pass_bars}] — что и как часто "
                        "срабатывает на холдоуте",
    "verdict": "{status: работает|наблюдение|не подтверждён, failed, "
               "checks:[{ok,text}], reasons:[строки]}",
    "warnings": "список текстовых оговорок по-русски",
    "elapsed_sec": "время расчёта",
    "method": "одной строкой — чем именно мерили",
}

VERDICT_RULES = [
    f"«работает» — пройдены ВСЕ девять критериев: сделок на холдоуте "
    f">= {MIN_TRADES}, exp_r > {MIN_EXP_R}, PF >= {MIN_PF}, доля зёрен "
    f"случайного контроля «не хуже» < {MAX_CTRL_SHARE}, доля прибыльных "
    f"возмущений >= {MIN_ROBUST_POS}, значимость p с поправкой на число "
    f"попыток < {MAX_P_ADJ}, плюс на ОБУЧАЮЩЕМ периоде тоже, максимум |R| "
    f"одной сделки <= {MAX_ABS_R}, доля сделок «в ноль» "
    f"<= {MAX_TINY_SHARE * 100:.0f}%",
    "три критерия (значимость, плюс на обучении, предел |R|) добавлены после "
    "аудита: без них «работает» получали 2.8% чисто случайных сигналов, а "
    "сетап, отобранный перебором 4320 вариантов по холдоуту (обучение -75.8%, "
    "холдоут +121.8%), проходил все проверки",
    f"критерий «сделок в ноль» добавлен после разбора ботов: сделка, закрытая "
    f"переносом стопа в безубыток, даёт +0.001$ — формально победа, "
    f"фактически ноль. У SOL таких циклов 89%, winrate 98% против честных "
    f"78%, и весь плюс +9.16$ оказался артефактом (без пустых сделок "
    f"-8.58$). Порог: «в ноль» = |PnL| < {TINY_FRAC:.0%} маржи цикла "
    f"(${TINY_USD:g}), выше {MAX_TINY_SHARE * 100:.0f}% таких сделок winrate "
    f"и ожидание меряют перенос стопа, а не торговлю",
    "«наблюдение» — сделок >= 10, exp_r > 0 и провалено не больше двух "
    "критериев: идея не опровергнута, но и не подтверждена",
    "«не подтверждён» — всё остальное. Это валидный результат, а не повод "
    "менять пороги",
]


def describe():
    """Полный машинный контракт языка — это то, из чего UI строит форму.
    Возвращается КОПИЯ: словари реестра — состояние модуля, их правка со
    стороны страницы молча меняла бы валидацию."""
    return json.loads(json.dumps(_describe(), ensure_ascii=False))


def _describe():
    return dict(
        version="1.0",
        conditions=COND_DEFS, exit=EXIT_DEFS, grid=GRID_DEFS, root=ROOT_DEFS,
        example=EXAMPLE_SPEC, data=available_data(),
        result=RESULT_DOC, verdict_rules=VERDICT_RULES,
        thresholds=dict(min_trades=MIN_TRADES, min_exp_r=MIN_EXP_R,
                        min_pf=MIN_PF, max_ctrl_share=MAX_CTRL_SHARE,
                        min_robust_pos=MIN_ROBUST_POS,
                        max_p_adj=MAX_P_ADJ, max_abs_r=MAX_ABS_R,
                        max_tiny_share=MAX_TINY_SHARE, tiny_usd=TINY_USD,
                        tiny_frac=TINY_FRAC, margin_usd=MARGIN,
                        hold_frac=HOLD_FRAC, n_ctrl_seeds=N_CTRL_SEEDS,
                        n_pert=N_PERT, pert=PERT),
        notes=[
            "все условия entry соединяются логическим И",
            f"сделка «в ноль» — |PnL| < {TINY_FRAC:.0%} маржи цикла "
            f"(${TINY_USD:g} при марже ${MARGIN:g}); такие сделки не считаются "
            f"победой ни в winrate, ни на графике (серый маркер «≈0»)",
            "pct — проценты, days — сутки, ATR — ATR(14) своего ТФ в долях цены",
            "час и день недели считаются по МОМЕНТУ ЗАКРЫТИЯ сигнального бара "
            "(UTC), день недели 0=понедельник",
            "trail, если задан, ЗАМЕНЯЕТ tp (движок умеет один режим выхода)",
            "holdout = последние 28% баров, в подборе не участвует",
        ])


# ================================================================ валидация
def _num(v, p, where):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SpecError(f"{where}: ожидалось число, получено {v!r}")
    v = float(v)
    if p.get("min") is not None and v < p["min"] - 1e-12:
        raise SpecError(f"{where}: {v} меньше минимума {p['min']}")
    if p.get("max") is not None and v > p["max"] + 1e-12:
        raise SpecError(f"{where}: {v} больше максимума {p['max']}")
    return int(round(v)) if p["type"] == "int" else v


def _check_params(cfg, defs, where, kind_key=None):
    """Проверка параметров по описанию: неизвестный ключ — ошибка (иначе
    опечатка в UI молча теряется), недостающий — берётся default."""
    out = {}
    known = set(defs) | ({kind_key} if kind_key else set())
    for k in cfg:
        if k not in known:
            raise SpecError(f"{where}: неизвестный параметр '{k}'; "
                            f"допустимы: {', '.join(sorted(defs))}")
    for name, p in defs.items():
        v = cfg.get(name, p.get("default"))
        if v is None:
            raise SpecError(f"{where}: не задан обязательный параметр '{name}'")
        t = p["type"]
        if t in ("int", "float"):
            out[name] = _num(v, p, f"{where}.{name}")
        elif t == "enum":
            if v not in p["values"]:
                raise SpecError(f"{where}.{name}: '{v}' — недопустимое значение;"
                                f" допустимы: {', '.join(map(str, p['values']))}")
            out[name] = v
        elif t == "list_enum":
            if isinstance(v, str):
                v = [v]
            if not isinstance(v, (list, tuple)) or not v:
                raise SpecError(f"{where}.{name}: ожидался непустой список из "
                                f"{p['values']}")
            for x in v:
                if x not in p["values"]:
                    raise SpecError(f"{where}.{name}: '{x}' не из "
                                    f"{p['values']}")
            out[name] = list(v)
        elif t == "list_int":
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                v = [v]
            if not isinstance(v, (list, tuple)) or not v:
                raise SpecError(f"{where}.{name}: ожидался непустой список "
                                f"целых {p['min']}..{p['max']}")
            vv = []
            for x in v:
                if isinstance(x, bool) or not isinstance(x, (int, float)):
                    raise SpecError(f"{where}.{name}: '{x}' не целое число")
                x = int(x)
                if x < p["min"] or x > p["max"]:
                    raise SpecError(f"{where}.{name}: {x} вне "
                                    f"{p['min']}..{p['max']}")
                vv.append(x)
            out[name] = sorted(set(vv))
        else:
            out[name] = v
    return out


# Псевдонимы параметров: ключ 'kind' у самого условия занят ТИПОМ условия,
# поэтому у level_dist и divergence одноимённые параметры переименованы. В
# вложенной форме {"level_dist": {"kind": "round", ...}} старое имя ещё
# работает — UI-агенту не придётся объяснять пользователю тонкость.
COND_ALIASES = {
    "level_dist": {"kind": "level"},
    "divergence": {"kind": "dir", "type": "dir"},
}


def _cond_kind(c, idx):
    """Каноническая форма условия — {'kind': ..., параметры}. Дополнительно
    принимаем {'type': ...} и {'rsi': {...}} — UI-агенту так удобнее, а
    неоднозначности нет."""
    if not isinstance(c, dict):
        raise SpecError(f"entry[{idx}]: условие должно быть объектом JSON")
    if "kind" in c or "type" in c:
        kind = c.get("kind", c.get("type"))
        cfg = {k: v for k, v in c.items() if k not in ("kind", "type")}
        return kind, cfg
    keys = [k for k in c if k in COND_DEFS]
    if len(keys) == 1 and isinstance(c[keys[0]], dict) and len(c) == 1:
        return keys[0], dict(c[keys[0]])
    raise SpecError(f"entry[{idx}]: не понял условие {json.dumps(c, ensure_ascii=False)}"
                    f" — нужен ключ 'kind', например "
                    f"{{\"kind\": \"rsi\", \"period\": 14, \"op\": \"<\", "
                    f"\"value\": 30}}")


def validate(spec):
    """Проверка и нормализация spec. Возвращает (норм_spec, предупреждения).
    Любая ошибка — SpecError с понятным текстом, а не падение движка."""
    if not isinstance(spec, dict):
        raise SpecError("spec должен быть объектом JSON")
    unknown = set(spec) - {"symbol", "interval", "side", "entry", "exit",
                           "grid", "lev", "cooldown_bars", "tried_variants",
                           "title", "note"}
    if unknown:
        raise SpecError(f"неизвестные поля верхнего уровня: "
                        f"{', '.join(sorted(unknown))}")
    warn = []
    raw_root = {k: v for k, v in spec.items() if k in ROOT_DEFS}
    if "symbol" in raw_root:                 # UI может прислать что угодно
        raw_root["symbol"] = str(raw_root["symbol"]).upper()
    if "interval" in raw_root:               # 240 (число) и "240" — одно и то же
        raw_root["interval"] = str(raw_root["interval"])
    root = _check_params(raw_root, ROOT_DEFS, "spec")
    have = available_data()
    if root["symbol"] not in have:
        raise SpecError(f"нет данных по монете {root['symbol']}; доступны: "
                        f"{', '.join(sorted(have))}")
    if root["interval"] not in have[root["symbol"]]:
        raise SpecError(f"для {root['symbol']} нет кэша ТФ {root['interval']}м; "
                        f"доступны: {', '.join(have[root['symbol']])}")

    entry = spec.get("entry")
    if not isinstance(entry, list) or not entry:
        raise SpecError("entry: нужен непустой список условий "
                        "(все соединяются логическим И)")
    conds = []
    for idx, c in enumerate(entry):
        kind, cfg = _cond_kind(c, idx)
        if kind not in COND_DEFS:
            raise SpecError(
                f"entry[{idx}]: неизвестное условие '{kind}'; доступны: "
                f"{', '.join(sorted(COND_DEFS))}")
        d = COND_DEFS[kind]
        for old, new in COND_ALIASES.get(kind, {}).items():
            if old in cfg and new not in cfg:
                cfg[new] = cfg.pop(old)
        out = _check_params(cfg, d["params"], f"entry[{idx}]({kind})")
        out["kind"] = kind
        conds.append(out)
        if kind in ("ema", "macd") and out.get("fast", 0) >= out.get("slow", 1):
            raise SpecError(f"entry[{idx}]({kind}): fast должен быть меньше "
                            f"slow (сейчас {out['fast']} и {out['slow']})")
    kinds = [c["kind"] for c in conds]
    for k in set(kinds):
        if kinds.count(k) > 1 and k not in ("rsi", "ema_price", "level_dist",
                                            "breakout", "volume"):
            warn.append(f"условие '{k}' задано {kinds.count(k)} раза — "
                        f"убедитесь, что это не опечатка")

    ex = dict(spec.get("exit") or {})
    unknown = set(ex) - set(EXIT_DEFS)
    if unknown:
        raise SpecError(f"exit: неизвестные поля: {', '.join(sorted(unknown))}")
    out_ex = {}
    for name in ("stop", "tp"):
        cfg = ex.get(name)
        if cfg is None:
            if name == "stop":
                raise SpecError("exit.stop обязателен: без стопа честного "
                                "бэктеста не бывает")
            cfg = {}
        if not isinstance(cfg, dict):
            raise SpecError(f"exit.{name}: ожидался объект")
        out_ex[name] = _check_params(cfg, EXIT_DEFS[name]["params"],
                                     f"exit.{name}")
    if ex.get("trail") is not None:
        if not isinstance(ex["trail"], dict):
            raise SpecError("exit.trail: ожидался объект {\"bars\": N}")
        out_ex["trail"] = _check_params(ex["trail"],
                                        EXIT_DEFS["trail"]["params"],
                                        "exit.trail")
        if ex.get("tp") is not None:
            warn.append("задан trail — выход трейлингом, exit.tp игнорируется")
    for name in ("be_after_r", "timeout_days", "max_stop_pct"):
        out_ex[name] = _num(ex.get(name, EXIT_DEFS[name]["default"]),
                            EXIT_DEFS[name], f"exit.{name}")

    grid = _check_params(dict(spec.get("grid") or {}), GRID_DEFS, "grid")

    # данные под условия проверяем СРАЗУ: объёма может не быть по этой монете
    # или ТФ, и узнать об этом на середине долгого прогона — плохая новость
    need_vol = [c for c in conds if c["kind"] == "volume"
                or (c["kind"] == "level_dist" and c["level"] == "volume_poc")]
    if need_vol and _vol_by_ts(root["symbol"], root["interval"],
                               se2.bar_ms_of(int(root["interval"]))) is None:
        raise SpecError(
            f"нет данных объёма для {root['symbol']} {root['interval']}м — "
            f"условия {', '.join(sorted({c['kind'] for c in need_vol}))} "
            f"использовать нельзя")

    lf = se2.liq_frac(root["lev"])
    if out_ex["max_stop_pct"] / 100.0 > 0.8 * lf:
        warn.append(
            f"стоп до {out_ex['max_stop_pct']}% при плече x{root['lev']} "
            f"упирается в ликвидацию ({lf * 100:.1f}% хода): движок обрежет "
            f"допуск до {0.8 * lf * 100:.2f}%, часть сигналов не исполнится")
    norm = dict(root)
    norm["entry"] = conds
    norm["exit"] = out_ex
    norm["grid"] = grid
    if spec.get("title"):
        norm["title"] = str(spec["title"])
    return norm, warn


# ====================================================== построение условий
def _b_rsi(cfg, ser, side):
    arr = _rsi(ser, cfg["period"])
    v, op = cfg["value"], cfg["op"]
    sgn = 1.0 if op == ">" else -1.0
    marg = [None if x is None else sgn * (x - v) for x in arr]
    return dict(margin=marg, warm=int(cfg["period"]) + 2,
                got=arr, need=v,
                label=f"RSI{int(cfg['period'])}{op}{v:g}")


def _b_move(cfg, ser, side, up):
    d_bars = max(1, int(round(cfg["days"] * ser["n_day"])))
    cl = ser["closes"]
    need = cfg["pct"]
    marg = [None] * len(cl)
    got = [None] * len(cl)
    for i in range(d_bars, len(cl)):
        base = cl[i - d_bars]
        if base <= 0:
            continue
        mv = ((cl[i] - base) / base if up else (base - cl[i]) / base) * 100.0
        got[i] = mv
        marg[i] = mv - need
    kind = "рост" if up else "падение"
    return dict(margin=marg, warm=d_bars + 1, got=got, need=need,
                tol=0.30 * need,
                label=f"{kind} {need:g}% за {cfg['days']:g}д")


def _b_ema(cfg, ser, side):
    ef, es = _ema(ser, cfg["fast"]), _ema(ser, cfg["slow"])
    n = len(ef)
    op = cfg["op"]
    mask = [False] * n
    for i in range(n):
        a, b = ef[i], es[i]
        if a is None or b is None:
            continue
        if op == "above":
            mask[i] = a > b
        elif op == "below":
            mask[i] = a < b
        else:
            pa, pb = ef[i - 1], es[i - 1]
            if i == 0 or pa is None or pb is None:
                continue
            mask[i] = (a > b and pa <= pb) if op == "cross_up" \
                else (a < b and pa >= pb)
    return dict(mask=mask, warm=int(cfg["slow"]) + 2,
                label=f"EMA{int(cfg['fast'])} {op} EMA{int(cfg['slow'])}")


def _b_ema_price(cfg, ser, side):
    e = _ema(ser, cfg["period"])
    cl = ser["closes"]
    sgn = 1.0 if cfg["op"] == "above" else -1.0
    marg = [None if e[i] is None or not e[i] else sgn * (cl[i] - e[i]) / e[i]
            for i in range(len(cl))]
    return dict(margin=marg, warm=int(cfg["period"]) + 2,
                label=f"цена {cfg['op']} EMA{int(cfg['period'])}")


def _b_macd(cfg, ser, side):
    line, sig, hist = _macd(ser, cfg["fast"], cfg["slow"], cfg["signal"])
    n = len(line)
    cond = cfg["cond"]
    mask = [False] * n
    for i in range(1, n):
        if cond in ("hist_up", "hist_down"):
            a, b = hist[i], hist[i - 1]
            if a is None or b is None:
                continue
            mask[i] = a > b if cond == "hist_up" else a < b
        else:
            a, b = line[i], sig[i]
            pa, pb = line[i - 1], sig[i - 1]
            if None in (a, b, pa, pb):
                continue
            mask[i] = (a > b and pa <= pb) if cond == "cross_up" \
                else (a < b and pa >= pb)
    return dict(mask=mask, warm=int(cfg["slow"]) + int(cfg["signal"]) + 2,
                label=f"MACD {cond}")


def _b_breakout(cfg, ser, side):
    w = max(2, int(round(cfg["days"] * ser["n_day"])))
    lo, hi = _lagged_ext(ser, w)
    c4, ctx = ser["c4"], ser["ctx"]
    up = cfg["dir"] == "high"
    n = len(c4)
    marg = [None] * n
    got = [None] * n
    for i in range(n):
        lvl = hi[i] if up else lo[i]
        a = ctx["atr_bar"][i]
        if lvl is None or not a:
            continue
        c = c4[i][4]
        d = ((c - lvl) if up else (lvl - c)) / (a * c)
        got[i] = d
        marg[i] = d
    return dict(margin=marg, warm=w + 2, got=got, need=0.0,
                label=f"пробой {cfg['days']:g}д {cfg['dir']}")


def _b_candle(cfg, ser, side):
    c4 = ser["c4"]
    green = cfg["dir"] == "green"
    bmin = cfg["body_min"]
    n = len(c4)
    marg = [None] * n
    for i in range(n):
        _, o, h, l, c = c4[i]
        if (c > o) if green else (c < o):
            rng = h - l
            if bmin <= 0:
                marg[i] = 1.0
            else:
                marg[i] = (abs(c - o) / rng - bmin) if rng > 0 else -1.0
        else:
            marg[i] = -1.0
    return dict(margin=marg, warm=1,
                label=f"свеча {cfg['dir']}" +
                      (f" тело>={bmin:g}" if bmin > 0 else ""))


REG_IDX = {"bull": 0, "range": 1, "bear": 2}


def _b_regime(cfg, ser, side):
    allow = {REG_IDX[x] for x in cfg["allow"]}
    reg = ser["ctx"]["regime"]
    return dict(mask=[r in allow for r in reg], warm=1,
                label="режим " + "/".join(cfg["allow"]))


def _b_atr_rank(cfg, ser, side):
    arr = ser["ctx"]["atr_rank"]
    sgn = 1.0 if cfg["op"] == ">" else -1.0
    v = cfg["value"]
    marg = [None if x is None else sgn * (x - v) for x in arr]
    return dict(margin=marg, warm=2, got=arr, need=v,
                label=f"ATR-ранг{cfg['op']}{v:g}")


def _b_volume(cfg, ser, side):
    vols = volume_series(ser)
    if vols is None:
        raise SpecError(f"нет данных объёма для {ser['symbol']} "
                        f"{ser['interval']}м — условие 'volume' недоступно")
    w = int(cfg["window"])
    med = _memo(ser, ("vmed", w), lambda: _roll_median_prev(vols, w))
    mult = cfg["mult"]
    n = len(vols)
    marg = [None] * n
    got = [None] * n
    for i in range(n):
        m, v = med[i], vols[i]
        if not m or v is None:
            continue
        got[i] = v / m
        marg[i] = v / m - mult
    return dict(margin=marg, warm=w + 1, got=got, need=mult,
                label=f"объём>={mult:g}x медианы {w}")


def _b_funding(cfg, ser, side):
    arr = ser["ctx"].get("fund")
    if arr is None or all(x is None for x in arr):
        raise SpecError(f"нет данных funding для {ser['symbol']} — "
                        f"условие 'funding' недоступно")
    sgn = 1.0 if cfg["op"] == ">" else -1.0
    v = cfg["value"]
    marg = [None if x is None else sgn * (x - v) for x in arr]
    return dict(margin=marg, warm=1, got=arr, need=v,
                label=f"funding{cfg['op']}{v:g}%")


def _b_level_dist(cfg, ser, side):
    kind = cfg["level"]
    c4, ctx = ser["c4"], ser["ctx"]
    n = len(c4)
    warm = 2
    if kind in ("swing_low", "swing_high"):
        lv = _nearest_pivot(ser, cfg["pivot"], "low" if kind == "swing_low"
                            else "high")
        warm = int(cfg["pivot"]) * 2 + 2
    elif kind == "round":
        lv = [_round_level(c[4]) for c in c4]
    else:
        lv = _poc(ser, cfg["window"])
        if lv is None:
            raise SpecError(f"нет данных объёма для {ser['symbol']} "
                            f"{ser['interval']}м — уровень volume_poc "
                            f"посчитать нечем")
        warm = int(cfg["window"]) + 1
    mx = cfg["max_atr"]
    marg = [None] * n
    got = [None] * n
    for i in range(n):
        a = ctx["atr_bar"][i]
        if lv[i] is None or not a:
            continue
        c = c4[i][4]
        d = abs(c - lv[i]) / (a * c)
        got[i] = d
        marg[i] = mx - d
    return dict(margin=marg, warm=warm, got=got, need=mx, levels=lv,
                label=f"до {kind} <= {mx:g} ATR")


def _b_divergence(cfg, ser, side):
    """Классическая дивергенция, причинно и без пивотов будущего:
    bull — текущий бар обновил минимум окна period, а RSI на нём ВЫШЕ, чем на
    баре прежнего минимума (между экстремумами оставляем зазор GAP баров,
    иначе «два экстремума» — это один и тот же кусок)."""
    GAP = 3
    per = int(cfg["period"])
    rsi = _rsi(ser, cfg["rsi_period"])
    c4 = ser["c4"]
    n = len(c4)
    bull = cfg["dir"] == "bull"
    mask = [False] * n
    for i in range(per + GAP, n):
        a, b = i - per, i - GAP
        if b <= a:
            continue
        if bull:
            j = min(range(a, b + 1), key=lambda k: c4[k][3])
            if c4[i][3] < c4[j][3] and rsi[i] is not None and rsi[j] is not None:
                mask[i] = rsi[i] > rsi[j]
        else:
            j = max(range(a, b + 1), key=lambda k: c4[k][2])
            if c4[i][2] > c4[j][2] and rsi[i] is not None and rsi[j] is not None:
                mask[i] = rsi[i] < rsi[j]
    return dict(mask=mask, warm=per + GAP + int(cfg["rsi_period"]) + 2,
                label=f"дивергенция {cfg['dir']} {per}")


def _b_hour(cfg, ser, side):
    a, b = int(cfg["from"]), int(cfg["to"])
    bar_ms = ser["bar_ms"]
    ok_h = set(range(a, b + 1)) if a <= b else \
        set(range(a, 24)) | set(range(0, b + 1))
    mask = [time.gmtime((c[0] + bar_ms) / 1000).tm_hour in ok_h
            for c in ser["c4"]]
    return dict(mask=mask, warm=1, label=f"час {a}-{b} UTC")


def _b_weekday(cfg, ser, side):
    days = set(cfg["days"])
    bar_ms = ser["bar_ms"]
    mask = [time.gmtime((c[0] + bar_ms) / 1000).tm_wday in days
            for c in ser["c4"]]
    return dict(mask=mask, warm=1,
                label="дни " + ",".join(map(str, sorted(days))))


_BUILDERS = {
    "rsi": _b_rsi,
    "drop": lambda cfg, ser, side: _b_move(cfg, ser, side, up=False),
    "rise": lambda cfg, ser, side: _b_move(cfg, ser, side, up=True),
    "ema": _b_ema, "ema_price": _b_ema_price, "macd": _b_macd,
    "breakout": _b_breakout, "candle": _b_candle, "regime": _b_regime,
    "atr_rank": _b_atr_rank, "volume": _b_volume, "funding": _b_funding,
    "level_dist": _b_level_dist, "divergence": _b_divergence,
    "hour": _b_hour, "weekday": _b_weekday,
}


def build_conditions(conds, ser, side):
    """Компиляция условий в булевы ряды + числовые запасы (для near-miss).
    Возвращает список словарей с ключами mask/margin/warm/label/tol."""
    out = []
    n = ser["n"]
    for c in conds:
        b = _BUILDERS[c["kind"]](c, ser, side)
        if "mask" not in b:
            b["mask"] = [m is not None and m >= 0 for m in b["margin"]]
        else:
            b.setdefault("margin", None)
        if len(b["mask"]) != n:
            raise SpecError(f"внутренняя ошибка: ряд условия {c['kind']} "
                            f"длины {len(b['mask'])} вместо {n}")
        b["kind"] = c["kind"]
        b.setdefault("tol", COND_DEFS[c["kind"]].get("near_tol"))
        out.append(b)
    return out


def entry_mask(spec, ser):
    """Итоговая маска входов (И по всем условиям) + прогрев + диагностика.
    Прогрев = максимум прогревов условий: раньше него условие физически
    нельзя посчитать, и молча считать это «сигнала нет» нечестно."""
    side = "L" if spec["side"] == LONG else "S"
    parts = build_conditions(spec["entry"], ser, side)
    n = ser["n"]
    warm = max([p["warm"] for p in parts] + [se3.ATR_LEN + 2, 2])
    mask = parts[0]["mask"] if len(parts) == 1 else None
    if mask is None:
        mask = [all(t) for t in zip(*[p["mask"] for p in parts])]
    else:
        mask = list(mask)
    for i in range(min(warm, n)):
        mask[i] = False
    return mask, warm, parts


# ============================================ отображение spec -> геном se3
def _genome(spec):
    """Геном signal_engine3 из spec. Он нужен только затем, чтобы вызвать
    ЧУЖУЮ проверенную механику сделки: гены входа (сетапы/ворота) здесь ни на
    что не влияют — вход уже решён маской условий."""
    ex = spec["exit"]
    g = se3.default_genome(
        hold_days=max(1, int(round(ex["timeout_days"] * 1))),
        cooldown=int(spec["cooldown_bars"]),
        grid_levels=int(spec["grid"]["levels"]),
        grid_step_atr=float(spec["grid"]["step_atr"]),
        grid_mult=float(spec["grid"]["mult"]),
        be_after_r=float(ex["be_after_r"]),
        stop_cap=max(MIN_STOP, ex["max_stop_pct"] / 100.0),
    )
    if "trail" in ex:
        g["tp_mode"] = 2
        g["trail_bars"] = int(ex["trail"]["bars"])
    elif ex["tp"]["type"] == "r":
        g["tp_mode"] = 0
        g["tp_r"] = float(ex["tp"]["value"])
    elif ex["tp"]["type"] == "atr":
        g["tp_mode"] = 1
        g["tp_atr"] = float(ex["tp"]["value"])
    else:                                   # pct -> тот же путь, но цель в
        g["tp_mode"] = 1                    # долях цены: tp_atr подбирается
        g["tp_atr"] = None                  # на баре (см. _bar_genome)
    return g


def _bar_genome(g, spec, atr_b):
    """Ген tp_atr для тейка в ПРОЦЕНТАХ зависит от ATR бара (движок умеет
    цель только в ATR или в R), поэтому геном для такого тейка достраивается
    на баре. Копия делается лишь на сигнальных барах — это не горячий путь."""
    if g.get("tp_atr") is None:
        gg = dict(g)
        gg["tp_atr"] = (spec["exit"]["tp"]["value"] / 100.0) / max(atr_b, 1e-9)
        return gg
    return g


def _stop_price(i, ser, side, spec, ref):
    """Цена стопа на баре i. Только данные <= i."""
    st = spec["exit"]["stop"]
    sgn = 1 if side == "L" else -1
    atr_b = ser["ctx"]["atr_bar"][i]
    if st["type"] == "atr":
        if not atr_b:
            return None
        return ref * (1 - sgn * st["value"] * atr_b)
    if st["type"] == "pct":
        return ref * (1 - sgn * st["value"] / 100.0)
    lo, hi = _trail_ext(ser, max(2, int(round(st["value"]))))
    if lo[i] is None or not atr_b:
        return None
    buf = st["buf_atr"] * atr_b * ref
    return (lo[i] - buf) if sgn == 1 else (hi[i] + buf)


REJ_STOP = "ширина стопа"
REJ_NO15M = se3.REJ_NO15M


# ==================================================================== прогон
def _run_mask(spec, ser, mask, a, b, parts=None, collect_diag=True,
              near_cap=NEAR_CAP):
    """Ядро прогона: по маске входов строит сделки ЧУЖОЙ механикой
    (signal_engine3._make_trade -> simulate_grid_trade). Формат результата —
    как у signal_engine3.run_setup, поэтому signal_stats.full_stats читает его
    без единой правки.

    Одна позиция за раз; кулдаун — в барах своего ТФ; при balance < MARGIN
    цикл объявляется разорённым (маржи на следующий вход уже нет)."""
    c4, c15, ts15 = ser["c4"], ser["c15"], ser["ts15"]
    ctx, bar_ms = ser["ctx"], ser["bar_ms"]
    side = "L" if spec["side"] == LONG else "S"
    sgn = 1 if side == "L" else -1
    lev = int(spec["lev"])
    g = _genome(spec)
    setup = f"custom_{spec['side']}"
    n = ser["n"]
    a, b = max(0, int(a)), min(int(b), n)

    trail15 = None
    if int(g["tp_mode"]) == 2:
        lo, hi = _trail_ext(ser, max(2, int(g["trail_bars"])))
        trail15 = se3.trail_series_15(c4, bar_ms, ts15, lo if side == "L" else hi)

    win_lo, win_hi = _trail_ext(ser, 60)         # только для диагностики
    swing_lo = swing_hi = None
    if spec["exit"]["stop"]["type"] == "swing":
        swing_lo, swing_hi = _trail_ext(
            ser, max(2, int(round(spec["exit"]["stop"]["value"]))))
    rsi_ref = _rsi(ser, next((c["period"] for c in spec["entry"]
                              if c["kind"] == "rsi"), 14))

    balance, peak, max_dd = START, START, 0.0
    trades, near_misses = [], []
    reject_counts, gate_solo, monthly = {}, {}, {}
    blocked_busy = blocked_cooldown = blocked_ruined = 0
    signals = bars_eval = 0
    pass_by_regime = {0: 0, 1: 0, 2: 0}
    busy_until_ts = last_exit_ts = 0
    ruined = False
    cool_ms = int(spec["cooldown_bars"]) * bar_ms
    t0 = c4[a][0] if a < n else c4[-1][0]

    if collect_diag and parts:
        for p in parts:
            miss = sum(1 for i in range(a, b) if not p["mask"][i])
            gate_solo[p["label"]] = miss

    for i in range(a, b):
        ts = c4[i][0]
        sig_ts = ts + bar_ms
        bars_eval += 1
        cool_ok = (not last_exit_ts) or (sig_ts >= last_exit_ts + cool_ms)
        free = sig_ts >= busy_until_ts and cool_ok

        if not mask[i]:
            if collect_diag and parts:
                bad = next((p for p in parts if not p["mask"][i]), None)
                key = bad["label"] if bad else "прогрев"
                reject_counts[key] = reject_counts.get(key, 0) + 1
                # near-miss: провалено РОВНО одно условие и не хватило чуть-чуть
                if (free and bad is not None and len(near_misses) < near_cap
                        and bad.get("margin") is not None
                        and bad.get("tol") and sum(
                            1 for p in parts if not p["mask"][i]) == 1):
                    m = bad["margin"][i]
                    if m is not None and m >= -abs(bad["tol"]):
                        row = _near_row(spec, ser, i, side, bad, g,
                                        win_lo, win_hi, rsi_ref)
                        if row:
                            near_misses.append(row)
            continue

        ref = c4[i][4]
        stop_px = _stop_price(i, ser, side, spec, ref)
        if stop_px is None:
            reject_counts[REJ_STOP] = reject_counts.get(REJ_STOP, 0) + 1
            continue
        dist = sgn * (ref - stop_px) / ref
        cap = min(spec["exit"]["max_stop_pct"] / 100.0,
                  0.8 * se2.liq_frac(lev))
        if dist < MIN_STOP or dist > cap:
            reject_counts[REJ_STOP] = reject_counts.get(REJ_STOP, 0) + 1
            gate_solo[REJ_STOP] = gate_solo.get(REJ_STOP, 0) + 1
            continue

        signals += 1
        pass_by_regime[ctx["regime"][i]] = pass_by_regime.get(
            ctx["regime"][i], 0) + 1
        if ruined:
            blocked_ruined += 1
            continue
        if sig_ts < busy_until_ts:
            blocked_busy += 1
            continue
        if not cool_ok:
            blocked_cooldown += 1
            continue

        ev_ = _ev_row(spec, ser, i, side, ref, stop_px, dist,
                      win_lo, win_hi, rsi_ref, swing_lo, swing_hi)
        gg = _bar_genome(g, spec, ctx["atr_bar"][i])
        tr, why = se3._make_trade(setup, ev_, i, c4, ctx, gg, c15, ts15, lev,
                                  bar_ms=bar_ms, trail15=trail15)
        if tr is None:
            reject_counts[why] = reject_counts.get(why, 0) + 1
            if why == REJ_NO15M:
                break                      # 15м-данные кончились
            continue

        balance += tr["pnl"]
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak)
        m_idx = int((tr["exit_ts"] - t0) // MONTH_MS)
        monthly[m_idx] = monthly.get(m_idx, 0.0) + tr["pnl"]
        trades.append(tr)
        busy_until_ts = last_exit_ts = tr["exit_ts"]
        if balance < MARGIN:
            ruined = True

    last_ts = c4[min(max(a, b - 1), n - 1)][0] if n else t0
    n_tr = len(trades)
    fills_hist = {}
    for t in trades:
        fills_hist[t["grid_fills"]] = fills_hist.get(t["grid_fills"], 0) + 1
    be_exits = [t for t in trades if t["be_exit"]]
    return dict(
        balance=balance, trades=trades, near_misses=near_misses,
        reject_counts=reject_counts, blocked_busy=blocked_busy,
        blocked_cooldown=blocked_cooldown, blocked_ruined=blocked_ruined,
        monthly=monthly, max_dd=max_dd, ruined=ruined,
        months=max(1e-9, (last_ts - t0) / MONTH_MS),
        setup=setup, lev=lev, signals=signals,
        pass_by_regime=pass_by_regime, gate_solo=gate_solo,
        bars_eval=bars_eval, signal_range=(a, b),
        interval_min=ser["interval_min"], symbol=ser["symbol"],
        grid_fills=sum(t["grid_fills"] for t in trades),
        grid_fill_hist=fills_hist,
        avg_grid_fills=(round(sum(t["grid_fills"] for t in trades) / n_tr, 3)
                        if n_tr else 0.0),
        avg_entry=(round(sum(t["avg_entry"] for t in trades) / n_tr, 6)
                   if n_tr else None),
        be_moved=sum(1 for t in trades if t["be_moved"]),
        be_hit=len(be_exits),
        be_hit_share=(round(len(be_exits) / n_tr * 100, 1) if n_tr else 0.0),
        be_no_stop=sum(1 for t in be_exits if not t["orig_stop_touched"]),
        trail_exits=sum(1 for t in trades if t["exit_kind"] == "trail"),
        t0_ms=t0,
    )


def _ev_row(spec, ser, i, side, ref, stop_px, dist, win_lo, win_hi, rsi_ref,
            swing_lo=None, swing_hi=None):
    """Заготовка «сигнала» в формате signal_engine3.gate_eval — её ждёт
    _make_trade. Диагностика (RSI, зона, ATR) нужна карточке сделки на сайте."""
    ctx = ser["ctx"]
    lo, hi = win_lo[i], win_hi[i]
    c = ser["c4"][i][4]
    atr_b = ctx["atr_bar"][i]
    tp = None
    ex = spec["exit"]
    sgn = 1 if side == "L" else -1
    if "trail" not in ex:
        if ex["tp"]["type"] == "r":
            tp = ref * (1 + sgn * ex["tp"]["value"] * dist)
        elif ex["tp"]["type"] == "atr":
            tp = ref * (1 + sgn * ex["tp"]["value"] * (atr_b or 0.0))
        else:
            tp = ref * (1 + sgn * ex["tp"]["value"] / 100.0)
    diag = dict(
        rsi=rsi_ref[i], zone_pos=((c - lo) / (hi - lo)) if hi and hi > lo
        else None, atr=ctx["atr_d"][i], atr_bar=atr_b,
        regime=ctx["regime"][i], range_lo=lo, range_hi=hi,
        rsi_thr=None, zone_thr=None,
        swing_lo=swing_lo[i] if swing_lo else None,
        swing_hi=swing_hi[i] if swing_hi else None)
    return dict(side=side, entry_ref=ref, stop=stop_px, tp=tp, dist=dist,
                gates=[], ok=True, n_failed=0, first_fail=None,
                near_miss=False, diag=diag, setup=f"custom_{spec['side']}")


def _near_row(spec, ser, i, side, bad, g, win_lo, win_hi, rsi_ref):
    """Строка near-miss с ГИПОТЕТИЧЕСКОЙ сделкой (та же механика, hypo=True).
    Показывает, врут ли пороги пользователя: если «почти прошедшие» бары в
    среднем прибыльны, порог случайный, а не найденный."""
    ctx = ser["ctx"]
    ref = ser["c4"][i][4]
    stop_px = _stop_price(i, ser, side, spec, ref)
    if stop_px is None:
        return None
    sgn = 1 if side == "L" else -1
    dist = sgn * (ref - stop_px) / ref
    if dist < MIN_STOP or dist > 0.8 * se2.liq_frac(int(spec["lev"])):
        return None
    ev_ = _ev_row(spec, ser, i, side, ref, stop_px, dist, win_lo, win_hi,
                  rsi_ref)
    gg = _bar_genome(g, spec, ctx["atr_bar"][i])
    hypo, why = se3._make_trade(f"custom_{spec['side']}", ev_, i, ser["c4"],
                                ctx, gg, ser["c15"], ser["ts15"],
                                int(spec["lev"]), hypo=True,
                                bar_ms=ser["bar_ms"])
    got = bad.get("got")
    gv = got[i] if isinstance(got, list) else got
    return dict(
        ts=ser["c4"][i][0], side=side, gate=bad["label"],
        got=round(float(gv), 4) if gv is not None else 0.0,
        need=round(float(bad.get("need") or 0.0), 4),
        margin=round(float(bad["margin"][i]), 4),
        entry=ref, stop=stop_px, tp=ev_["tp"], dist=round(dist, 5),
        hypo_r=hypo["r"] if hypo else 0.0,
        hypo_reason=hypo["reason"] if hypo else why,
        hypo_mfe_r=hypo["mfe_r"] if hypo else 0.0,
        hypo_mae_r=hypo["mae_r"] if hypo else 0.0,
        hypo_pnl=hypo["pnl"] if hypo else 0.0,
        hypo_grid_fills=hypo["grid_fills"] if hypo else 0,
        regime=ctx["regime"][i],
        rsi=round(rsi_ref[i], 1) if rsi_ref[i] is not None else None,
        zone_pos=ev_["diag"]["zone_pos"],
        range_lo=win_lo[i], range_hi=win_hi[i],
        atr_pct=round(ctx["atr_d"][i] * 100, 3) if ctx["atr_d"][i] else None,
        stop_pct=round(dist * 100, 3), rsi_thr=None, zone_thr=None)


def part_range(ser, part):
    """Границы куска истории: train = [0, 72%), holdout = [72%, 100%)."""
    n, h = ser["n"], ser["hold_i"]
    if part in (None, "all"):
        return 0, n
    if part == "train":
        return 0, h
    if part == "holdout":
        return h, n
    raise SpecError(f"неизвестная часть истории '{part}' "
                    f"(нужно all / train / holdout)")


def build_run(spec, part="all", signal_range=None, collect_diag=True,
              ser=None):
    """Публичный прогон: spec -> результат в формате signal_engine3.run_setup
    (его напрямую ест signal_stats.full_stats).

    part          — all | train | holdout;
    signal_range  — (a, b) в барах ТФ, если нужен произвольный кусок;
    collect_diag  — считать near-miss и разбор отказов (в перебор не берём:
                    там это только время);
    ser           — готовая серия (тест причинности гоняет ОБРЕЗАННУЮ)."""
    norm = spec if spec.get("_normalized") else validate(spec)[0]
    ser = ser or load_series(norm["symbol"], norm["interval"])
    mask, warm, parts = entry_mask(norm, ser)
    a, b = signal_range if signal_range else part_range(ser, part)
    a = max(int(a), warm)
    r = _run_mask(norm, ser, mask, a, b, parts=parts, collect_diag=collect_diag)
    r["warm"] = warm
    r["part"] = part
    r["entry_conditions"] = [
        dict(label=p["label"], kind=p["kind"],
             pass_bars=sum(1 for i in range(a, min(b, ser["n"]))
                           if p["mask"][i]),
             bars=max(0, min(b, ser["n"]) - a))
        for p in parts]
    r["mask_signals"] = sum(1 for i in range(a, min(b, ser["n"])) if mask[i])
    return r


# =============================================================== метрики
def _pct(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    x = q * (len(s) - 1)
    i = int(x)
    return s[i] + (s[min(i + 1, len(s) - 1)] - s[i]) * (x - i)


def is_tiny(t):
    """Сделка «в ноль»: |PnL| меньше 1% маржи цикла ($0.05 при марже $5).

    Почему по деньгам, а не по R: после переноса стопа в безубыток
    фактический риск падает почти до нуля, и та же копеечная сделка получает
    R хоть +50, хоть -0.001 — по R её не поймать. Деньги не врут: $0.03 это
    $0.03, меньше половины издержек одного цикла."""
    return abs(float(t.get("pnl") or 0.0)) < TINY_USD


def tiny_stats(trades):
    """Разбор сделок на три класса: настоящая прибыль / настоящий убыток /
    «в ноль». Winrate, посчитанный без этого разделения, меряет не торговлю,
    а срабатывание безубытка — именно так у ботов проекта получались 95-98%
    при честных 78-90%.

    be_n — сколько сделок закрылись ПОСЛЕ переноса стопа в безубыток (флаг
    движка be_moved, а не догадка по цене): это причина появления пустых
    сделок, и полезно видеть, совпадают ли числа."""
    n = len(trades)
    tiny = [t for t in trades if is_tiny(t)]
    real = [t for t in trades if not is_tiny(t)]
    real_win = [t for t in real if t["pnl"] > 0]
    real_loss = [t for t in real if t["pnl"] <= 0]
    be = [t for t in trades if t.get("be_moved")]
    pnl_tiny = sum(t["pnl"] for t in tiny)
    pnl_all = sum(t["pnl"] for t in trades)
    return dict(
        tiny_n=len(tiny), tiny_share=round(len(tiny) / n * 100, 1) if n else 0.0,
        tiny_thr_usd=TINY_USD, tiny_frac_of_margin=TINY_FRAC,
        tiny_win_n=sum(1 for t in tiny if t["pnl"] > 0),
        tiny_loss_n=sum(1 for t in tiny if t["pnl"] <= 0),
        real_win_n=len(real_win), real_loss_n=len(real_loss),
        # winrate, где «в ноль» НЕ победа (делится на все сделки) и winrate
        # по одним лишь настоящим сделкам — вторая цифра честнее к идее,
        # первая честнее к деньгам
        wr_honest=round(len(real_win) / n * 100, 1) if n else 0.0,
        wr_ex_tiny=round(len(real_win) / len(real) * 100, 1) if real else 0.0,
        pnl_tiny_usd=round(pnl_tiny, 3),
        pnl_without_tiny=round(pnl_all - pnl_tiny, 3),
        be_n=len(be), be_share=round(len(be) / n * 100, 1) if n else 0.0,
        avg_tiny_usd=round(pnl_tiny / len(tiny), 4) if tiny else 0.0)


def metrics(r, lev=None, curves=True):
    """Компактный срез метрик прогона поверх signal_stats.full_stats
    (полный набор из ~57 метрик доступен UI отдельным вызовом full_stats)."""
    lev = lev or r.get("lev") or 5
    s = ss.full_stats(r, lev, t0_ms=r.get("t0_ms"))
    tr = r["trades"]
    out = dict(
        n=s["n"], wins=s["wins"], wr=s["wr"], wr_breakeven=s["wr_breakeven"],
        exp_r=s["exp_r"], exp_usd=s["exp_usd"], sum_r=s["sum_r"],
        pnl_usd=s["pnl_usd"], pf=s["pf"], ret_pct=s["ret_pct"],
        dd_pct=s["dd_pct"], months=s["months"], tpm=s["tpm"],
        avg_win_r=s["avg_win_r"], avg_loss_r=s["avg_loss_r"],
        best_r=s["best_r"], worst_r=s["worst_r"],
        max_loss_streak=s["max_loss_streak"],
        avg_stop_pct=s["avg_stop_pct"], avg_atr_pct=s["avg_atr_pct"],
        avg_hold_h=s["timing"]["hold_avg_h"],
        ruined=s["ruined"], signals=r.get("signals", 0),
        bars_eval=r.get("bars_eval", 0), executed=s["n"],
        blocked_busy=r.get("blocked_busy", 0),
        blocked_cooldown=r.get("blocked_cooldown", 0),
        outcomes={k: v["n"] for k, v in s["outcomes"].items()},
        outcome_r={k: v["sum_r"] for k, v in s["outcomes"].items()},
        months_pos=s["reinvest"]["months_pos"],
        months_total=s["reinvest"]["months_total"],
        reinvest_pct=s["reinvest"]["final_pct"],
        avg_grid_fills=r.get("avg_grid_fills", 0.0),
        period_start=s["period_start"], period_end=s["period_end"],
        near_n=s["near"]["n"], near_avg_r=s["near"]["avg_r"],
    )
    # ---- сделки «в ноль»: без них winrate и ожидание читать нельзя ----
    out.update(tiny_stats(tr))
    # итог счёта, если пустые сделки просто выкинуть (база фиксированная,
    # поэтому это ровно вычитание их денег — так же считает bots_honest)
    out["ret_pct_ex_tiny"] = round(out["pnl_without_tiny"] / START * 100, 1)
    if curves:
        out["equity"] = s["equity"]
        out["drawdown"] = s["drawdown"]
        out["monthly"] = s["monthly"]
        out["by_month"] = s["by_month"]
        out["r_hist"] = s["r_hist"]
        out["by_regime"] = {k: dict(n=v["n"], wr=v["wr"], exp_r=v["exp_r"])
                            for k, v in s["by_regime"].items()}
        out["warnings_engine"] = s["warnings"]
        out["rejects"] = s["rejects"]["gates"][:8]
    if tr:
        out["first_trade"] = tr[0]["entry_ts"]
        out["last_trade"] = tr[-1]["exit_ts"]
    return out


def significance(rs, tried=1):
    """t-статистика и p для среднего R + 95% ДИ. p корректируется на число
    ПРОСМОТРЕННЫХ вариантов (Бонферрони): если пользователь перебрал 50 идей,
    «p=0.03» означает p_adj=1.0 и никакого открытия нет."""
    n = len(rs)
    if n < 3:
        return dict(n=n, t=None, p=None, p_adj=None, ci=[None, None],
                    tried=tried, note="меньше 3 сделок — значимость не считаем")
    m = statistics.fmean(rs)
    sd = statistics.stdev(rs)
    se = sd / math.sqrt(n) if sd > 0 else 0.0
    t = m / se if se > 0 else 0.0
    p = math.erfc(abs(t) / math.sqrt(2.0)) if se > 0 else 1.0
    return dict(n=n, mean_r=round(m, 4), sd_r=round(sd, 4),
                t=round(t, 3), p=round(p, 5),
                p_adj=round(min(1.0, p * max(1, int(tried))), 5),
                tried=int(tried),
                ci=[round(m - 1.959964 * se, 4), round(m + 1.959964 * se, 4)],
                note="ДИ 95% для среднего R; p двусторонний, нормальное "
                     "приближение (как в остальных проверках проекта)")


def _block_bootstrap(rets, n_path, rng, nboot=BOOT_N, block=BOOT_BLOCK):
    """Блочный бутстрап траекторий капитала. Блоки сохраняют серии подряд
    идущих убытков — обычный бутстрап их разбивает и занижает риск."""
    n = len(rets)
    if n < 8 or n_path < 1:
        return None
    ruin = half = 0
    finals, dds = [], []
    for _ in range(nboot):
        seq = []
        while len(seq) < n_path:
            L = rng.randint(block[0], block[1])
            s = rng.randrange(n)
            seq.extend(rets[(s + j) % n] for j in range(L))
        seq = seq[:n_path]
        eq = peak = 1.0
        dd = 0.0
        hit_r = hit_h = False
        for x in seq:
            eq *= (1 + x)
            peak = max(peak, eq)
            dd = max(dd, (peak - eq) / peak)
            if eq <= HALF_LEVEL:
                hit_h = True
            if eq <= RUIN_LEVEL:
                hit_r = True
                break
        finals.append(eq)
        dds.append(dd)
        ruin += 1 if hit_r else 0
        half += 1 if hit_h else 0
    return dict(ruin_pct=round(ruin / nboot * 100, 1),
                half_pct=round(half / nboot * 100, 1),
                final_p5=round(_pct(finals, 0.05), 3),
                final_p50=round(_pct(finals, 0.50), 3),
                final_p95=round(_pct(finals, 0.95), 3),
                dd_p50_pct=round(_pct(dds, 0.50) * 100, 1),
                dd_p95_pct=round(_pct(dds, 0.95) * 100, 1),
                n_trades_path=n_path, nboot=nboot, block=list(block))


def _concentration(trades):
    """Сколько сделок дают CONC_SHARE (89%) всей положительной прибыли и
    какова доля трёх лучших. Если «прибыль» — это три сделки, стратегии нет."""
    pos = sorted((t["pnl"] for t in trades if t["pnl"] > 0), reverse=True)
    tot = sum(pos)
    if tot <= 0:
        return dict(n_for_share=None, share=CONC_SHARE, top3_share_pct=None,
                    n_pos=len(pos))
    acc, k = 0.0, 0
    for v in pos:
        acc += v
        k += 1
        if acc >= CONC_SHARE * tot:
            break
    return dict(n_for_share=k, share=CONC_SHARE, n_pos=len(pos),
                top3_share_pct=round(sum(pos[:3]) / tot * 100, 1))


def _costs(spec, ser, mask, a, b):
    """Издержки — РАЗНИЦЕЙ прогонов: обычного и с обнулёнными глобалами
    комиссий/слипа/фандинга в signal_engine3 (файл движка не трогаем, значения
    возвращаются в finally). Тот же приём, что в bots_honest."""
    base = _run_mask(spec, ser, mask, a, b, collect_diag=False)
    saved = (se3.TAKER, se3.MAKER, se3.SLIP, se3.FUND_8H)
    try:
        se3.TAKER = se3.MAKER = se3.SLIP = se3.FUND_8H = 0.0
        free = _run_mask(spec, ser, mask, a, b, collect_diag=False)
    finally:
        se3.TAKER, se3.MAKER, se3.SLIP, se3.FUND_8H = saved
    net = sum(t["pnl"] for t in base["trades"])
    gross = sum(t["pnl"] for t in free["trades"])
    gp = sum(t["pnl"] for t in free["trades"] if t["pnl"] > 0)
    cost = gross - net
    return dict(net_usd=round(net, 3), gross_usd=round(gross, 3),
                cost_usd=round(cost, 3),
                share_of_gross_profit_pct=(round(cost / gp * 100, 1)
                                           if gp > 0 else None),
                n_trades_net=len(base["trades"]),
                n_trades_gross=len(free["trades"]),
                note="прогон с нулевыми комиссиями/слипом/фандингом минус "
                     "обычный; число сделок может отличаться — без издержек "
                     "часть сделок доходит до тейка")


# ---------------------------------------------------------- возмущения spec
def _walk_numeric(spec, fn):
    """Обход всех ЧИСЛОВЫХ параметров spec, помеченных perturb=True.
    Переключатели, списки дней, число колен сетки и плечо не трогаются: у них
    нет «на 10% больше», это была бы уже другая стратегия."""
    out = json.loads(json.dumps(spec))
    for c in out["entry"]:
        for name, p in COND_DEFS[c["kind"]]["params"].items():
            if p.get("perturb") and name in c:
                c[name] = fn(c[name], p)
    ex = out["exit"]
    for name in ("stop", "tp"):
        for pn, p in EXIT_DEFS[name]["params"].items():
            if p.get("perturb") and pn in ex.get(name, {}):
                ex[name][pn] = fn(ex[name][pn], p)
    if "trail" in ex:
        p = EXIT_DEFS["trail"]["params"]["bars"]
        ex["trail"]["bars"] = fn(ex["trail"]["bars"], p)
    for name in ("be_after_r", "timeout_days"):
        if ex.get(name):
            ex[name] = fn(ex[name], EXIT_DEFS[name])
    for pn, p in GRID_DEFS.items():
        if p.get("perturb"):
            out["grid"][pn] = fn(out["grid"][pn], p)
    return out


def perturb_spec(spec, rng, frac=PERT):
    def f(v, p):
        nv = v * (1 + rng.uniform(-frac, frac))
        nv = max(p.get("min", -1e18), min(p.get("max", 1e18), nv))
        return int(round(nv)) if p["type"] == "int" else nv
    out = _walk_numeric(spec, f)
    out["_normalized"] = True
    return out


def numeric_params(spec):
    """Список числовых параметров, участвующих в тесте устойчивости
    (нужен UI, чтобы показать, что именно шатали)."""
    names = []

    def f(v, p):
        names.append(v)
        return v
    _walk_numeric(spec, f)
    return len(names)


# ================================================== случайный контроль
def _random_mask(ser, a, b, k, rng, warm):
    """k случайных баров в [max(a,warm), b) — «вход той же частоты, но без
    идеи». Контроль обязателен: рынок сам по себе даёт и плюс, и минус."""
    a = max(a, warm)
    n = ser["n"]
    mask = [False] * n
    pool = range(a, min(b, n))
    if len(pool) == 0 or k <= 0:
        return mask
    k = min(k, len(pool))
    for i in rng.sample(list(pool), k):
        mask[i] = True
    return mask


# ======================================================== главная проверка
def evaluate(spec, tried=None, n_ctrl=N_CTRL_SEEDS, n_pert=N_PERT,
             curves=True, verbose=False):
    """ЧЕСТНАЯ ПРОВЕРКА ИДЕИ. Возвращает словарь (см. модульную документацию и
    describe()): train / holdout / random_control / buyhold / significance /
    risk / robustness / verdict / warnings.

    Порядок именно такой: сначала меряем, потом судим. Все пороги — константы
    модуля (MIN_TRADES, MIN_EXP_R, MIN_PF, MAX_CTRL_SHARE, MIN_ROBUST_POS),
    их нельзя двигать «под результат»."""
    t_start = time.time()
    norm, warn = validate(spec)
    tried = int(tried if tried is not None else norm.get("tried_variants", 1))
    ser = load_series(norm["symbol"], norm["interval"])
    mask, warm, parts = entry_mask(norm, ser)
    n = ser["n"]
    h = ser["hold_i"]
    a_tr, b_tr = max(0, warm), h
    a_ho, b_ho = max(h, warm), n
    norm_run = dict(norm)
    norm_run["_normalized"] = True

    r_tr = _run_mask(norm, ser, mask, a_tr, b_tr, parts=parts)
    r_ho = _run_mask(norm, ser, mask, a_ho, b_ho, parts=parts)
    m_tr = metrics(r_tr, norm["lev"], curves=curves)
    m_ho = metrics(r_ho, norm["lev"], curves=curves)
    if verbose:
        print(f"  train {m_tr['n']:4} сделок / holdout {m_ho['n']:4} "
              f"({time.time() - t_start:.1f}s)")

    # ------------------------------------------------ случайный контроль
    # Частота входов должна совпасть по ЧИСЛУ СДЕЛОК, а не по числу баров:
    # у случайных баров другая доля отказов по ширине стопа (сетап может
    # сидеть в спокойных или, наоборот, в штормовых участках). Поэтому один
    # калибровочный прогон, а потом 40 зёрен уже с исправленным k.
    user_exp = m_ho["exp_r"]
    k = max(1, r_ho["signals"])
    if m_ho["n"]:
        cm0 = _random_mask(ser, a_ho, b_ho, k, random.Random(CTRL_SEED0 - 1),
                           warm)
        n0 = len(_run_mask(norm, ser, cm0, a_ho, b_ho,
                           collect_diag=False)["trades"])
        # поправку ограничиваем вдвое: при разорении счёта прогон обрывается
        # раньше, и «докрутить» контроль до того же числа сделок нечестно
        ratio = min(2.0, max(0.5, m_ho["n"] / max(1, n0)))
        k = max(1, min(b_ho - max(a_ho, warm), int(round(k * ratio))))
    ctrl_exp, ctrl_ret, ctrl_n = [], [], []
    for s in range(n_ctrl):
        rng = random.Random(CTRL_SEED0 + s)
        cm = _random_mask(ser, a_ho, b_ho, k, rng, warm)
        rc = _run_mask(norm, ser, cm, a_ho, b_ho, collect_diag=False)
        tr = rc["trades"]
        ctrl_n.append(len(tr))
        ctrl_exp.append(sum(t["r"] for t in tr) / len(tr) if tr else 0.0)
        ctrl_ret.append(round((rc["balance"] / START - 1) * 100, 2))
    n_sig_ho = k
    not_worse = sum(1 for x in ctrl_exp if x >= user_exp)
    control = dict(
        seeds=n_ctrl, entries_per_seed=n_sig_ho,
        median_exp_r=round(statistics.median(ctrl_exp), 4) if ctrl_exp else None,
        p10_exp_r=round(_pct(ctrl_exp, 0.10), 4),
        p90_exp_r=round(_pct(ctrl_exp, 0.90), 4),
        median_ret_pct=round(statistics.median(ctrl_ret), 2) if ctrl_ret else None,
        median_trades=round(statistics.median(ctrl_n), 1) if ctrl_n else 0,
        user_exp_r=user_exp,
        share_not_worse=round(not_worse / n_ctrl, 3) if n_ctrl else None,
        note="случайные бары того же количества, те же выходы и издержки; "
             "доля зёрен, у которых exp_r не хуже пользовательского")
    if verbose:
        print(f"  контроль: медиана {control['median_exp_r']}R, "
              f"не хуже {control['share_not_worse']} "
              f"({time.time() - t_start:.1f}s)")

    # ------------------------------------------------------- buy & hold
    c0, c1 = ser["c4"][a_ho][4], ser["c4"][min(b_ho, n) - 1][4]
    months_ho = (ser["c4"][min(b_ho, n) - 1][0] - ser["c4"][a_ho][0]) / MONTH_MS
    bh = round((c1 / c0 - 1) * 100, 2)
    buyhold = dict(ret_pct=bh, per_month_pct=round(bh / max(months_ho, 1e-9), 2),
                   months=round(months_ho, 2),
                   short_ret_pct=round((c0 / c1 - 1) * 100, 2),
                   price_start=c0, price_end=c1,
                   note="1x без плеча и без издержек — планка, ниже которой "
                        "торговать бессмысленно")

    # ------------------------------------------------------ значимость
    rs_ho = [t["r"] for t in r_ho["trades"]]
    sig = significance(rs_ho, tried=tried)
    sig_all = significance([t["r"] for t in r_tr["trades"]] + rs_ho, tried=tried)

    # ------------------------------------------------------------ риск
    rets = [t["pnl"] / START for t in r_ho["trades"]]
    tpm = (len(rets) / months_ho) if months_ho > 0 else 0.0
    boot = _block_bootstrap(rets, max(8, int(round(tpm * 12))),
                            random.Random(CTRL_SEED0))
    dd_usd = m_ho["dd_pct"] / 100.0 * START
    risk = dict(
        max_dd_pct=m_ho["dd_pct"], max_dd_usd=round(dd_usd, 2),
        liquidations=m_ho["outcomes"].get("liq", 0),
        ruined=bool(r_ho["ruined"]), blocked_after_ruin=r_ho["blocked_ruined"],
        worst_trade_r=m_ho["worst_r"], max_loss_streak=m_ho["max_loss_streak"],
        avg_risk_usd=round(MARGIN * norm["lev"]
                           * (m_ho["avg_stop_pct"] or 0) / 100, 3),
        liq_frac_pct=round(se2.liq_frac(norm["lev"]) * 100, 2),
        stop_vs_liq=round((m_ho["avg_stop_pct"] or 0)
                          / max(se2.liq_frac(norm["lev"]) * 100, 1e-9), 3),
        bootstrap=boot,
        capital_required_usd=round(dd_usd / DD_BUDGET, 1) if dd_usd else None,
        capital_required_p95_usd=(round(boot["dd_p95_pct"] / 100 * START
                                        / DD_BUDGET, 1) if boot else None),
        note=f"требуемый капитал = историческая просадка / {DD_BUDGET:.0%} при "
             f"марже ${MARGIN:g} на цикл; p95 — по блочному бутстрапу. "
             f"Бутстрап считает КОМПАУНД (доля капитала на цикл фиксирована, "
             f"как на /pnl), а флаг ruined — фиксированную базу ${START:g} с "
             f"маржой ${MARGIN:g}: при компаунде позиция уменьшается вместе со "
             f"счётом, поэтому вероятность разорения там ниже")

    # ------------------------------------------------------ устойчивость
    rng = random.Random(PERT_SEED)
    p_exp, p_ret, p_n = [], [], []
    for _ in range(n_pert):
        sp = perturb_spec(norm_run, rng)
        pm, pw, _pp = entry_mask(sp, ser)
        rp = _run_mask(sp, ser, pm, max(h, pw), n, collect_diag=False)
        tr = rp["trades"]
        p_n.append(len(tr))
        p_exp.append(sum(t["r"] for t in tr) / len(tr) if tr else 0.0)
        p_ret.append(round((rp["balance"] / START - 1) * 100, 2))
    pos = sum(1 for x in p_ret if x > 0)
    robustness = dict(
        n=n_pert, pert_pct=PERT * 100,
        median_exp_r=round(statistics.median(p_exp), 4) if p_exp else None,
        median_ret_pct=round(statistics.median(p_ret), 2) if p_ret else None,
        p10_ret_pct=round(_pct(p_ret, 0.10), 2),
        p90_ret_pct=round(_pct(p_ret, 0.90), 2),
        share_positive=round(pos / n_pert, 3) if n_pert else None,
        median_trades=round(statistics.median(p_n), 1) if p_n else 0,
        n_empty=sum(1 for x in p_n if x == 0),
        base_ret_pct=m_ho["ret_pct"], params_perturbed=numeric_params(norm_run),
        note="каждый числовой параметр умножается на 1+U(-10%,+10%); "
             "закономерность — это плато, подгонка — шпиль")
    if verbose:
        print(f"  устойчивость: медиана {robustness['median_ret_pct']}%, "
              f"прибыльных {robustness['share_positive']} "
              f"({time.time() - t_start:.1f}s)")

    # ---------------------------------------------------------- издержки
    costs = _costs(norm_run, ser, mask, a_ho, b_ho)
    conc = _concentration(r_ho["trades"])

    # ------------------------------------------------------------ вердикт
    checks = []

    def chk(ok, text):
        checks.append((bool(ok), text))
        return bool(ok)

    c1_ = chk(m_ho["n"] >= MIN_TRADES,
              f"сделок на холдоуте {m_ho['n']} (нужно >= {MIN_TRADES})")
    c2_ = chk(m_ho["exp_r"] > MIN_EXP_R,
              f"ожидание {m_ho['exp_r']:+.3f}R на сделку "
              f"(нужно > {MIN_EXP_R})")
    c3_ = chk(m_ho["pf"] is not None and m_ho["pf"] >= MIN_PF,
              f"профит-фактор {m_ho['pf']} (нужно >= {MIN_PF})")
    c4_ = chk(control["share_not_worse"] is not None
              and control["share_not_worse"] < MAX_CTRL_SHARE,
              f"случайный вход не хуже в "
              f"{(control['share_not_worse'] or 0) * 100:.0f}% зёрен "
              f"(нужно < {MAX_CTRL_SHARE * 100:.0f}%)")
    c5_ = chk(robustness["share_positive"] is not None
              and robustness["share_positive"] >= MIN_ROBUST_POS,
              f"прибыльных возмущений {(robustness['share_positive'] or 0) * 100:.0f}%"
              f" (нужно >= {MIN_ROBUST_POS * 100:.0f}%)")

    # --- критерии, добавленные по итогам аудита конструктора ---
    # Аудит показал: без них «работает» получали 2.8% ЧИСТО СЛУЧАЙНЫХ
    # сигналов, а сетап, отобранный перебором 4320 вариантов по холдоуту
    # (обучение -75.8%, холдоут +121.8%), проходил все проверки.

    # 6) статистическая значимость с поправкой на число просмотренных
    #    вариантов: без неё 4 из 7 ложных «работает» имели p до 0.558,
    #    а счётчик попыток был чисто декоративным
    p_adj = sig.get("p_adj")
    c6_ = chk(p_adj is not None and p_adj < MAX_P_ADJ,
              f"значимость p={p_adj if p_adj is None else round(p_adj, 4)} "
              f"с поправкой на {tried} проверенных вариантов "
              f"(нужно < {MAX_P_ADJ})")

    # 7) согласие обучения и холдоута: подгонка под экзамен даёт минус на
    #    обучении и плюс на холдоуте. Корреляция между частями по 2148
    #    вариантам оказалась -0.50 — то есть «лучшее на обучении» системно
    #    хуже на экзамене, и одного холдоута для вердикта недостаточно
    c7_ = chk(m_tr["n"] >= 5 and m_tr["exp_r"] > 0,
              f"на обучающем периоде тоже плюс: {m_tr['exp_r']:+.3f}R "
              f"на {m_tr['n']} сделках (нужно > 0)")

    # 8) защита от искажения R безубытком: после переноса стопа фактический
    #    риск падает почти до нуля, и одна сделка получает +99R, раздувая
    #    среднее. Тогда exp_r, контроль и t-тест считаются по мусорной шкале
    c8_ = chk(abs(m_ho.get("best_r") or 0) <= MAX_ABS_R,
              f"нет сделок с аномальным R (максимум "
              f"{m_ho.get('best_r')}, предел {MAX_ABS_R}) — иначе "
              f"безубыток обнуляет знаменатель и ломает статистику")

    # 9) «ПУСТЫЕ» СДЕЛКИ. Тот же безубыток портит статистику и вторым,
    #    более тихим способом: сделка закрывается на +0.001$ — формально
    #    победа, фактически ноль. Критерий 8 её не ловит: там нужен
    #    аномальный R, а тут R может быть и +0.02. Найдено на ботах
    #    проекта: у SOL 89% циклов закрылись «в ноль», winrate 98% против
    #    честных 78%, и весь плюс +9.16$ оказался артефактом (без пустых
    #    сделок -8.58$). Если доля пустых больше порога, winrate,
    #    ожидание и профит-фактор меряют не торговлю, а перенос стопа —
    #    вердикт «работает» при этом невозможен по построению.
    ts_ho = m_ho.get("tiny_share") or 0.0
    c9_ = chk(m_ho["n"] == 0 or ts_ho <= MAX_TINY_SHARE * 100,
              f"сделок «в ноль» {m_ho.get('tiny_n', 0)} из {m_ho['n']} = "
              f"{ts_ho:.1f}% (нужно <= {MAX_TINY_SHARE * 100:.0f}%): |итог| "
              f"меньше ${TINY_USD:g}, это {TINY_FRAC:.0%} маржи цикла. "
              f"Winrate {m_ho['wr']:.1f}% -> честный "
              f"{m_ho.get('wr_honest', 0):.1f}%; выше предела winrate и "
              f"ожидание меряют перенос стопа в безубыток, а не торговлю")

    n_fail = sum(1 for ok, _ in checks if not ok)
    if n_fail == 0:
        status = "работает"
    elif m_ho["n"] >= 10 and m_ho["exp_r"] > 0 and n_fail <= 2:
        status = "наблюдение"
    else:
        status = "не подтверждён"
    # ЖЁСТКИЙ ЗАПРЕТ по пустым сделкам. Остальные критерии говорят «результат
    # плохой», а этот — «результат НЕИЗМЕРИМ»: если больше
    # MAX_TINY_SHARE сделок закрылись в ноль, winrate, ожидание и
    # профит-фактор описывают срабатывание безубытка, а не торговлю. Поэтому
    # «работает» запрещено явно, а не через арифметику n_fail <= 2: если
    # пороги других критериев когда-нибудь поедут, запрет должен устоять.
    if status == "работает" and not c9_:
        status = "наблюдение"
    reasons = [("OK   " if ok else "ПРОВАЛ ") + t for ok, t in checks]
    if status == "работает":
        reasons.append(f"все {len(checks)} критериев пройдены; это НЕ гарантия "
                       f"будущего — это отсутствие явных признаков подгонки")
    elif status == "наблюдение":
        reasons.append("часть критериев не пройдена: идея не опровергнута, но "
                       "и не подтверждена — нужны новые данные, а не новый "
                       "перебор параметров")
    else:
        reasons.append("идея не подтверждена; отрицательный результат — тоже "
                       "результат, менять пороги под него нельзя")

    # ------------------------------------------------------- оговорки
    w = list(warn)
    if m_ho["n"] and m_ho.get("tiny_n"):
        w.append(f"сделок «в ноль» {m_ho['tiny_n']} из {m_ho['n']} "
                 f"({m_ho['tiny_share']}%): |итог| меньше ${TINY_USD:g} — это "
                 f"1% маржи цикла и примерно половина издержек одной сделки. "
                 f"Winrate {m_ho['wr']}% с ними, честный "
                 f"{m_ho['wr_honest']}%; без них итог счёта "
                 f"{m_ho['ret_pct_ex_tiny']:+.1f}% вместо {m_ho['ret_pct']:+.1f}%"
                 + (f". Стоп переезжал в безубыток в {m_ho['be_n']} сделках"
                    if m_ho.get("be_n") else ""))
    if m_ho["n"] < MIN_TRADES:
        w.append(f"мало сделок на холдоуте ({m_ho['n']}): на такой выборке "
                 f"любой вывод — шум")
    if m_ho["n"] and costs["share_of_gross_profit_pct"] is not None:
        cs = costs["share_of_gross_profit_pct"]
        if cs >= 50:
            w.append(f"издержки съедают {cs}% валовой прибыли — идея живёт "
                     f"на комиссиях брокера, а не на рынке")
        elif cs >= 25:
            w.append(f"издержки съедают {cs}% валовой прибыли")
    if costs["gross_usd"] > 0 and costs["net_usd"] <= 0:
        w.append("без издержек идея прибыльна, с издержками — нет; это не "
                 "стратегия, а надежда на нулевые комиссии")
    if conc["n_for_share"] and m_ho["n"]:
        w.append(f"{CONC_SHARE * 100:.0f}% всей прибыли дают "
                 f"{conc['n_for_share']} сделок из {m_ho['n']} "
                 f"(три лучшие — {conc['top3_share_pct']}%)")
    bm = m_ho.get("by_month") or []
    tot = sum(x["pnl_usd"] for x in bm)
    if bm and tot > 0:
        best = max(bm, key=lambda x: x["pnl_usd"])
        if best["pnl_usd"] >= 0.8 * tot:
            w.append(f"результат держится на одном месяце {best['label']} "
                     f"({best['pnl_usd']:+.2f}$ из {tot:+.2f}$)")
    if m_ho["months_total"]:
        w.append(f"холдоут {m_ho['months']} мес, плюсовых месяцев "
                 f"{m_ho['months_pos']}/{m_ho['months_total']}")
    if risk["liquidations"]:
        w.append(f"ликвидаций: {risk['liquidations']} — плечо x{norm['lev']} "
                 f"не выдерживает эту ширину стопа")
    if r_ho["ruined"]:
        w.append("счёт разорён внутри холдоута (баланс упал ниже маржи цикла)")
    if risk["stop_vs_liq"] >= 0.8:
        w.append(f"стоп занимает {risk['stop_vs_liq'] * 100:.0f}% пути до "
                 f"ликвидации при x{norm['lev']} — запаса нет")
    if sig["p_adj"] is not None and sig["p_adj"] > 0.05 >= (sig["p"] or 1):
        w.append(f"p={sig['p']} выглядит значимым, но после поправки на "
                 f"{tried} просмотренных вариантов p={sig['p_adj']} — "
                 f"значимости нет")
    if tried <= 1:
        w.append("tried_variants=1: если вы уже перебирали варианты, укажите "
                 "их число — иначе значимость завышена")
    if m_ho["n"] and m_ho["ret_pct"] > 0 and buyhold["ret_pct"] > m_ho["ret_pct"]:
        w.append(f"«купил и держал» дал {buyhold['ret_pct']}% против "
                 f"{m_ho['ret_pct']}% у сетапа — идея проигрывает бездействию")
    if any(c["kind"] in ("hour", "weekday") for c in norm["entry"]):
        w.append("во входе есть фильтр по времени/дню недели — это самый "
                 "частый источник ложных находок, проверьте вердикт особенно "
                 "строго")
    if m_ho["near_n"] >= 5 and m_ho["near_avg_r"] > max(0.0, m_ho["exp_r"]):
        w.append(f"«почти прошедшие» бары дают {m_ho['near_avg_r']:+.2f}R "
                 f"против {m_ho['exp_r']:+.2f}R у прошедших — пороги условий "
                 f"скорее случайны")
    if r_ho["blocked_busy"] > r_ho["signals"] * 0.5 and r_ho["signals"]:
        w.append(f"{r_ho['blocked_busy']} из {r_ho['signals']} сигналов "
                 f"пропущены из-за занятости позиции — реальная частота "
                 f"сильно ниже задуманной")

    out = dict(
        spec=norm, tried_variants=tried,
        data=dict(symbol=norm["symbol"], interval=norm["interval"],
                  bars=n, warm=warm, hold_i=h,
                  train_from=ser["c4"][a_tr][0], train_to=ser["c4"][h - 1][0],
                  holdout_from=ser["c4"][a_ho][0],
                  holdout_to=ser["c4"][n - 1][0],
                  entry_bars_train=sum(1 for i in range(a_tr, b_tr) if mask[i]),
                  entry_bars_holdout=sum(1 for i in range(a_ho, b_ho)
                                         if mask[i])),
        train=m_tr, holdout=m_ho, random_control=control, buyhold=buyhold,
        significance=sig, significance_all=sig_all, risk=risk,
        robustness=robustness, costs=costs, concentration=conc,
        entry_conditions=[dict(label=p["label"], kind=p["kind"],
                               pass_bars=sum(1 for i in range(a_ho, b_ho)
                                             if p["mask"][i]))
                          for p in parts],
        verdict=dict(status=status, reasons=reasons, failed=n_fail,
                     checks=[dict(ok=ok, text=t) for ok, t in checks]),
        warnings=w, elapsed_sec=round(time.time() - t_start, 2),
        method="holdout 28% + случайный контроль 40 зёрен + поправка на "
               "множественность + возмущения +-10%; механика сделок — "
               "signal_engine3 (издержки taker 0.055%/maker 0.02%/слип 0.03%/"
               "фандинг 0.01% за 8ч)")
    return out


# =================================================================== отчёт
def print_report(res, prefix=""):
    """Короткий человеческий отчёт (для консоли и логов)."""
    p = prefix
    sp = res["spec"]
    print(f"{p}{sp['symbol']} {sp['interval']}м {sp['side']} x{sp['lev']} | "
          f"условий {len(sp['entry'])}: "
          f"{', '.join(c['label'] for c in res['entry_conditions'])}")
    for name in ("train", "holdout"):
        m = res[name]
        print(f"{p}  {name:8}: сделок {m['n']:4} WR {m['wr']:5.1f}% "
              f"{m['exp_r']:+.3f}R PF {m['pf']} итог {m['ret_pct']:+.1f}% "
              f"DD {m['dd_pct']}% (мес {m['months']})")
        print(f"{p}            в ноль {m.get('tiny_n', 0):4} "
              f"({m.get('tiny_share', 0):4.1f}%) безубыток {m.get('be_n', 0):4} "
              f"| честный WR {m.get('wr_honest', 0):5.1f}% "
              f"| итог без пустых {m.get('ret_pct_ex_tiny', 0):+.1f}%")
    c = res["random_control"]
    print(f"{p}  контроль: медиана {c['median_exp_r']:+.3f}R "
          f"({c['median_trades']} сделок), не хуже "
          f"{c['share_not_worse']} зёрен | buy&hold "
          f"{res['buyhold']['ret_pct']:+.1f}%")
    ct = res["costs"]
    print(f"{p}  издержки: {ct['cost_usd']:+.2f}$ "
          f"({ct['share_of_gross_profit_pct']}% валовой прибыли), "
          f"без них итог {ct['gross_usd']:+.2f}$ против {ct['net_usd']:+.2f}$")
    s = res["significance"]
    print(f"{p}  значимость: t={s['t']} p={s['p']} p_adj={s['p_adj']} "
          f"ДИ {s['ci']}")
    rb = res["robustness"]
    print(f"{p}  устойчивость: медиана {rb['median_ret_pct']:+.1f}%, "
          f"прибыльных {rb['share_positive']}")
    rk = res["risk"]
    b = rk["bootstrap"]
    print(f"{p}  риск: DD {rk['max_dd_pct']}% | ликвидаций "
          f"{rk['liquidations']} | разорение "
          f"{(b['ruin_pct'] if b else '—')}% | капитал "
          f"${rk['capital_required_usd']}")
    print(f"{p}  ВЕРДИКТ: {res['verdict']['status'].upper()}")
    for r in res["verdict"]["reasons"]:
        print(f"{p}    - {r}")
    for wline in res["warnings"]:
        print(f"{p}    ! {wline}")


if __name__ == "__main__":
    t = time.time()
    res = evaluate(EXAMPLE_SPEC, verbose=True)
    print_report(res)
    print(f"время: {time.time() - t:.1f}s")
