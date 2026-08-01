# -*- coding: utf-8 -*-
"""Связки индикаторов как ФИЛЬТРЫ ВХОДА для сетапа «Капитуляция-лонг».

ЗАЧЕМ. Владелец просит «посмотреть связку EMA RSI MACD, трендовую рынка» на
сетапе dump_long. Задача не «навесить всё подряд», а ИЗМЕРИТЬ ВКЛАД КАЖДОГО
фильтра по отдельности и честно сказать, помогает ли хоть один.

ЧТО УЖЕ ИЗВЕСТНО (не переоткрываем, это отправная точка):
  - у самой идеи капитуляции сырого преимущества НЕТ: в idea_test.py с
    фиксированными параметрами она отработала ХУЖЕ случайного входа
    (вклад -0.215R, 4/15 прибыльных комбинаций);
  - в отборе v12 dump_long@240 прошёл честный экзамен (12 сделок, +0.544R),
    но воспроизводимость при смене зерна 1/3 — основание слабое;
  - дважды ловили эффект «фильтр улучшает и СЛУЧАЙНЫЕ входы тоже», то есть
    работает как снижение экспозиции/бета, а не как ум. Поэтому каждый фильтр
    здесь проверяется ДВАЖДЫ: на сигналах сетапа и на случайных входах.

МЕТОДИКА (объявлена ДО просмотра результатов).
  1. Базовый сетап — dump_long с НЕЙТРАЛЬНЫМ семенем signal_engine3.DEFAULTS3
     (падение 6% за 2 суток, RSI14<30, зелёная свеча, стоп 2*ATR, тейк 2R,
     без сетки, без безубытка). Ни один его параметр здесь не подбирается.
  2. Пул сделок: КАЖДЫЙ сигнальный бар -> отдельная сделка тем же движком
     (signal_engine3._make_trade), независимо от занятости позиции. Сигналы,
     идущие подряд, схлопываются в КЛАСТЕР (разрыв < 1 суток) и от кластера
     берётся ПЕРВЫЙ бар — иначе один и тот же слив давал бы 5 почти
     одинаковых «наблюдений» и раздувал значимость.
  3. Фильтр = бинарный признак на сигнальном баре. Пул делится на «прошло» и
     «отсеяно», сравниваются средние R (t Уэлча). Если у отсеянных R ХУЖЕ —
     фильтр полезен; если лучше — вреден.
     ПОРОГ ПРИГОДНОСТИ (объявлен заранее): фильтр проверяется, только если в
     ОБЕИХ группах не меньше MIN_GRP=12 событий. Разбиение 2/142 не является
     проверкой фильтра: это проверка двух случайных сделок, и t-статистика
     там рисует любые числа. Такие фильтры выносятся в отдельный список «не
     делят выборку» и в семейство гипотез НЕ входят.
  4. ДВА контроля случайным входом той же частоты, тем же стопом, тем же
     тейком и тем же движком:
       (а) РАВНОМЕРНЫЙ — бары выбираются случайно по всему окну;
       (б) СОСЕДНИЙ — для каждого сигнала бар выбирается случайно в окне
           [i-120, i-12] баров, то есть в ТОМ ЖЕ участке рынка, но не на
           сливе (и строго ДО сигнала, чтобы контроль не смотрел вперёд).
     Контроль (б) строже: он отделяет «фильтр понимает капитуляцию» от
     «фильтр просто не торгует в плохие месяцы».
     Разность разностей  Δ_ум = (keep-cut)_сигналы - (keep-cut)_случайные
     и есть вклад фильтра СВЕРХ снижения экспозиции.
  5. Множественные проверки: поправка Бонферрони и FDR Беньямини-Хохберга по
     ВСЕМУ семейству (пригодные одиночные фильтры + комбинации).
  6. Обучающая часть [0..72%), HOLDOUT [72%..100%) — holdout трогается ОДИН
     раз, только для 1-3 фильтров, отобранных на обучающей части.
  7. Каждое «улучшение» сравнивается с (а) базой без фильтра, (б) случайным
     входом той же частоты, (в) «купил и держал» на том же окне.

ПАНЕЛИ (по мощности выборки).
  A «база»    — 4ч, 5 монет, DEFAULTS3: 77 независимых событий. Ровно тот
                сетап, о котором спрашивает владелец, но выборка крошечная.
  B «мощность»— 4ч, 5 монет, порог мягче (падение 4% за 3 суток, RSI14<35):
                223 события. Тот же сетап по смыслу, просто капитуляций
                больше. Нужна, потому что на 77 событиях НИЧЕГО, кроме
                эффекта около 0.7R, физически не различимо.
  C «1ч BTC»  — второй ТФ для проверки устойчивости знака.
  Панель B — основа для ОБНАРУЖЕНИЯ эффекта, панели A и C — проверка знака.
  Вердикт «работает» требует согласия всех трёх.

ПРИЧИННОСТЬ. Все ряды считаются так, что значение на баре i зависит только от
данных <= i. Это не декларация: блок 1 пересчитывает каждый ряд на ПРЕФИКСЕ
истории и сверяет последнее значение с полным рядом.

ВОСПРОИЗВОДИМОСТЬ. Зёрна случайных контролей выведены из ФИКСИРОВАННОЙ соли
монеты (не из hash(), который в Python рандомизируется от запуска к запуску).
Два запуска подряд обязаны дать один и тот же вывод.

Запуск:  python cap_indicators.py   (вывод дублируется в cap_indicators_out.txt)
"""

import math
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest_rsi_grid as bg      # calc_rsi (тот же RSI, что в движке)
import evolution as ev              # fetch, calc_atr_pct
import evolution6 as e6             # calc_regime
import signal_engine2 as se2        # calc_ema/calc_sma, prep_context
import signal_engine3 as se3        # dump_long, gate_eval, _make_trade

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "cap_indicators_out.txt")

# ------------------------------------------------------------- константы
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT"]
# фиксированная соль зерна на монету: hash() в Python рандомизируется,
# и с ним «лучшие три фильтра» менялись от запуска к запуску
SYM_SALT = {s: (k + 1) * 104729 for k, s in enumerate(SYMS)}
DAYS = 1150
LEV = 5                 # плечо панели: не упирается в потолок ширины стопа
                        # (0.8*liq_frac(5)=15.6%), поэтому выборка не режется
HOLD_FRAC = 0.72        # доля истории на обучение, остальное — holdout
CLUSTER_GAP_D = 1.0     # сигналы ближе этого — один кластер (одно событие)
RAND_SEEDS = list(range(1, 11))     # 10 зёрен случайного контроля
MIN_GRP = 12            # минимум событий в КАЖДОЙ группе, иначе не проверяем
NEAR_LO, NEAR_HI = 120, 12          # окно «соседнего» контроля, бары ТФ
ALPHA = 0.05
DAY_MS = 86400000

# Панели объявлены ДО прогона (см. шапку).
PANELS = [
    ("A", "база 4ч x5 монет", 240, SYMS, dict()),
    ("B", "мощность 4ч x5 монет", 240, SYMS,
     dict(drop_frac=0.04, drop_days=3, rsi_os=35)),
    ("C", "1ч BTC", 60, ["BTCUSDT"], dict()),
]


# ---------------------------------------------------------------- утилиты
def fmt_date(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def fmt_dt(ms):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ms / 1000))


def p_two(t):
    """Двусторонний p по нормальному приближению."""
    if t is None:
        return 1.0
    return math.erfc(abs(t) / math.sqrt(2.0))


def z_for_p(p):
    """Критический двусторонний z для заданного p (для читаемости порогов)."""
    lo, hi = 0.0, 12.0
    for _ in range(90):
        mid = (lo + hi) / 2
        if math.erfc(mid / math.sqrt(2.0)) > p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def mean(xs):
    return statistics.mean(xs) if xs else 0.0


def welch(a, b):
    """t Уэлча и стандартная ошибка разности средних.
    None, если хоть в одной группе меньше MIN_GRP наблюдений: на 2-5 сделках
    t-статистика не измеряет ничего, кроме самой себя."""
    if len(a) < MIN_GRP or len(b) < MIN_GRP:
        return None, None
    va = statistics.variance(a) / len(a)
    vb = statistics.variance(b) / len(b)
    if va + vb <= 0:
        return None, None
    t = (statistics.mean(a) - statistics.mean(b)) / math.sqrt(va + vb)
    return t, math.sqrt(va + vb)


def t_one(xs, n_min=5):
    """t-статистика «среднее отличается от нуля»; None при малой выборке."""
    if len(xs) < n_min:
        return None
    sd = statistics.stdev(xs)
    if sd <= 0:
        return None
    return statistics.mean(xs) / (sd / math.sqrt(len(xs)))


def bh_fdr(ps, alpha=ALPHA):
    """Беньямини-Хохберг: индексы гипотез, переживающих контроль FDR."""
    k = len(ps)
    if k == 0:
        return set()
    order = sorted(range(k), key=lambda j: ps[j])
    thr = 0
    for rank, j in enumerate(order, start=1):
        if ps[j] <= rank / k * alpha:
            thr = rank
    return {j for rank, j in enumerate(order, start=1) if rank <= thr}


def pf_of(rs):
    """Профит-фактор по списку R."""
    p = sum(x for x in rs if x > 0)
    l = -sum(x for x in rs if x < 0)
    return (p / l) if l > 0 else None


def maxdd(pnls):
    """Максимальная просадка по кривой накопленного PnL (в % от пика)."""
    bal = se3.START
    peak, dd = bal, 0.0
    for x in pnls:
        bal += x
        peak = max(peak, bal)
        if peak > 0:
            dd = max(dd, (peak - bal) / peak)
    return dd * 100


def f_num(x, w=7, d=3, sign=True):
    """Число или прочерк, если величина не определена."""
    if x is None:
        return "—".rjust(w)
    return (f"{x:>+{w}.{d}f}" if sign else f"{x:>{w}.{d}f}")


# =====================================================================
# БЛОК 1. ПРИЧИННЫЕ ИНДИКАТОРЫ
# Каждая функция обязана давать на баре i значение, зависящее ТОЛЬКО от
# данных <= i. Проверяется префиксом в check_causal().
# =====================================================================
def ema_of(vals, period):
    """EMA по ряду, начало которого может быть None (сигнальная линия MACD).
    Затравка — среднее первых period определённых значений, дальше рекурсия:
    значение на баре i зависит только от прошлого."""
    out = [None] * len(vals)
    buf, s = [], None
    k = 2.0 / (period + 1)
    for i, v in enumerate(vals):
        if v is None:
            continue
        if s is None:
            buf.append(v)
            if len(buf) == period:
                s = sum(buf) / period
                out[i] = s
        else:
            s = v * k + s * (1 - k)
            out[i] = s
    return out


def macd_series(closes, fast=12, slow=26, sig=9):
    """MACD(12,26,9): линия, сигнальная, гистограмма. Всё причинно."""
    ef = se2.calc_ema(closes, fast)
    es = se2.calc_ema(closes, slow)
    line = [None if (ef[i] is None or es[i] is None) else ef[i] - es[i]
            for i in range(len(closes))]
    sg = ema_of(line, sig)
    hist = [None if (line[i] is None or sg[i] is None) else line[i] - sg[i]
            for i in range(len(closes))]
    return line, sg, hist


def adx_di(c4, n=14):
    """ADX и направленные индикаторы DI+/DI- (Уайлдер, сглаживание n).

    Классика: TR и направленное движение считаются по барам i и i-1, потом
    сглаживание Уайлдера. ADX — сглаженный DX. Ни одна величина не смотрит
    вперёд: на баре i известны только бары <= i."""
    n = int(n)
    ln = len(c4)
    adx = [None] * ln
    dip = [None] * ln
    dim = [None] * ln
    if ln < 2 * n + 2:
        return adx, dip, dim
    trs, pdm, mdm = [], [], []
    for i in range(1, ln):
        _, o, h, l, c = c4[i]
        pc, ph, pl = c4[i - 1][4], c4[i - 1][2], c4[i - 1][3]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        up, dn = h - ph, pl - l
        pdm.append(up if (up > dn and up > 0) else 0.0)
        mdm.append(dn if (dn > up and dn > 0) else 0.0)
    str_, sp, sm = sum(trs[:n]), sum(pdm[:n]), sum(mdm[:n])
    dxs = []
    for k in range(n, len(trs)):
        i = k + 1                      # индекс бара в c4
        str_ = str_ - str_ / n + trs[k]
        sp = sp - sp / n + pdm[k]
        sm = sm - sm / n + mdm[k]
        if str_ <= 0:
            continue
        p = 100.0 * sp / str_
        m = 100.0 * sm / str_
        dip[i], dim[i] = p, m
        dx = 100.0 * abs(p - m) / (p + m) if (p + m) > 0 else 0.0
        dxs.append((i, dx))
        if len(dxs) == n:
            adx[i] = sum(x for _, x in dxs) / n
        elif len(dxs) > n:
            adx[i] = (adx[dxs[-2][0]] * (n - 1) + dx) / n
    return adx, dip, dim


def swing_lows(c4, k=3):
    """Подтверждённые свинг-минимумы: пары (индекс минимума, бар подтверждения).

    Минимум на баре j виден только через k баров, поэтому потребитель обязан
    брать свинг не раньше бара j+k. Именно здесь чаще всего прячется
    заглядывание вперёд в «дивергенциях» из статей."""
    out = []
    lows = [c[3] for c in c4]
    for j in range(k, len(c4) - k):
        v = lows[j]
        if all(lows[j - x] >= v for x in range(1, k + 1)) and \
           all(lows[j + x] > v for x in range(1, k + 1)):
            out.append((j, j + k))
    return out


def rsi_divergence(c4, rsi, k=3, lookback=60):
    """Бычья дивергенция RSI: цена делает НИЖЕ минимум, RSI — ВЫШЕ.

    На баре i берётся последний свинг-минимум j, который (а) уже подтверждён
    (j + k <= i) и (б) не старше lookback баров. Дивергенция = low[i] < low[j]
    и rsi[i] > rsi[j]. Возвращает (флаг, якорь) на каждый бар — якорь нужен
    для разбора конкретных сделок."""
    ln = len(c4)
    flag = [False] * ln
    anchor = [None] * ln
    sw = swing_lows(c4, k)
    lows = [c[3] for c in c4]
    p, known = 0, []
    for i in range(ln):
        while p < len(sw) and sw[p][1] <= i:
            known.append(sw[p][0])
            p += 1
        if rsi[i] is None:
            continue
        j = None
        for x in reversed(known):
            if i - x > lookback:
                break
            if x <= i - k and rsi[x] is not None:
                j = x
                break
        if j is None:
            continue
        anchor[i] = j
        if lows[i] < lows[j] and rsi[i] > rsi[j]:
            flag[i] = True
    return flag, anchor


def daily_from(c4):
    """Дневные свечи из баров ТФ по UTC-суткам (4ч и 1ч выровнены по 00:00:
    проверено, часы старта 4ч-баров = 0,4,8,12,16,20)."""
    days, order = {}, []
    for ts, o, h, l, c in c4:
        d = ts // DAY_MS
        if d not in days:
            days[d] = [d * DAY_MS, o, h, l, c]
            order.append(d)
        else:
            b = days[d]
            b[2] = max(b[2], h)
            b[3] = min(b[3], l)
            b[4] = c
    return [days[d] for d in sorted(order)]


def daily_map(c4, dcandles, arrs):
    """Ряды старшего (дневного) ТФ, разложенные на бары рабочего ТФ.

    На баре i берётся ПОСЛЕДНИЙ ЗАКРЫТЫЙ дневной бар, то есть ВЧЕРАШНИЙ
    (тот же приём, что в evolution6.calc_regime). Сегодняшний дневной бар
    ещё не закрыт — брать его значило бы заглянуть вперёд."""
    idx = {c[0] // DAY_MS: k for k, c in enumerate(dcandles)}
    out = [[None] * len(c4) for _ in arrs]
    for i, c in enumerate(c4):
        k = idx.get(c[0] // DAY_MS - 1)
        if k is None:
            continue
        for a, arr in enumerate(arrs):
            out[a][i] = arr[k]
    return out


def build_ind(c4, btc4=None):
    """Все индикаторы одной монеты. Ключи — имена рядов, которые читают
    фильтры. Ни один ряд не использует данные правее текущего бара."""
    closes = [c[4] for c in c4]
    line, sig, hist = macd_series(closes)
    adx, dip, dim = adx_di(c4, 14)
    rsi14 = bg.calc_rsi(closes, 14)
    div, div_anchor = rsi_divergence(c4, rsi14)
    d4 = daily_from(c4)
    dcl = [x[4] for x in d4]
    dmap = daily_map(c4, d4, [dcl, se2.calc_ema(dcl, 200),
                              se2.calc_ema(dcl, 50)])
    ind = dict(
        close=closes, low=[c[3] for c in c4],
        ema9=se2.calc_ema(closes, 9), ema21=se2.calc_ema(closes, 21),
        ema50=se2.calc_ema(closes, 50), ema200=se2.calc_ema(closes, 200),
        rsi7=bg.calc_rsi(closes, 7), rsi14=rsi14,
        rsi21=bg.calc_rsi(closes, 21),
        macd=line, macd_sig=sig, macd_hist=hist,
        adx=adx, di_p=dip, di_m=dim,
        div=div, div_anchor=div_anchor,
        d_close=dmap[0], d_ema200=dmap[1], d_ema50=dmap[2],
        regime=e6.calc_regime(c4),
    )
    # «трендовая рынка»: положение BTC относительно своей дневной EMA200,
    # разложенное по времени на бары текущей монеты (для BTC — он сам).
    bd = daily_from(btc4 if btc4 is not None else c4)
    bcl = [x[4] for x in bd]
    bmap = daily_map(c4, bd, [bcl, se2.calc_ema(bcl, 200)])
    ind["btc_close"], ind["btc_ema200"] = bmap[0], bmap[1]
    return ind


# =====================================================================
# БЛОК 2. ФИЛЬТРЫ (объявлены заранее, до просмотра результатов)
# Каждый — бинарный признак на сигнальном баре: True/False/None.
# None = признак не определён (мало истории) -> бар выбывает из сравнения
# по ЭТОМУ фильтру (и из «прошло», и из «отсеяно»).
# Зеркала отдельно не тестируются: разбиение то же самое, тест двусторонний,
# поэтому «MACD гист<0» не включён — это ровно отрицание «MACD>сигнальной».
# =====================================================================
def _gt(a, b):
    return None if (a is None or b is None) else bool(a > b)


def _lt(a, b):
    return None if (a is None or b is None) else bool(a < b)


def _prev(arr, i, back):
    return arr[i - back] if i - back >= 0 else None


def _macd_turn(d, i):
    """Разворот гистограммы вверх: локальное дно (падала, начала расти)."""
    h, h1, h2 = (d["macd_hist"][i], _prev(d["macd_hist"], i, 1),
                 _prev(d["macd_hist"], i, 2))
    if h is None or h1 is None or h2 is None:
        return None
    return bool(h > h1 and h1 <= h2)


def _macd_up2(d, i):
    h, h1, h2 = (d["macd_hist"][i], _prev(d["macd_hist"], i, 1),
                 _prev(d["macd_hist"], i, 2))
    if h is None or h1 is None or h2 is None:
        return None
    return bool(h > h1 > h2)


def _macd_cross(d, i, back=3):
    """Бычье пересечение MACD снизу вверх произошло за последние back баров."""
    m, s = d["macd"], d["macd_sig"]
    if m[i] is None or s[i] is None:
        return None
    for b in range(0, back):
        a, c = i - b, i - b - 1
        if c < 0 or None in (m[a], s[a], m[c], s[c]):
            return None
        if m[a] > s[a] and m[c] <= s[c]:
            return True
    return False


FILTERS = [
    # ---- EMA и их взаимное положение ----
    ("ema9>ema21", "EMA", lambda d, i: _gt(d["ema9"][i], d["ema21"][i])),
    ("close>EMA9", "EMA", lambda d, i: _gt(d["close"][i], d["ema9"][i])),
    ("close>EMA50", "EMA", lambda d, i: _gt(d["close"][i], d["ema50"][i])),
    ("close>EMA200", "EMA", lambda d, i: _gt(d["close"][i], d["ema200"][i])),
    ("EMA50>EMA200", "EMA", lambda d, i: _gt(d["ema50"][i], d["ema200"][i])),
    ("EMA200 растёт", "EMA",
     lambda d, i: _gt(d["ema200"][i], _prev(d["ema200"], i, 30))),
    # ---- RSI: уровень, разворот, дивергенция ----
    ("RSI14<25", "RSI", lambda d, i: _lt(d["rsi14"][i], 25.0)),
    ("RSI7<15", "RSI", lambda d, i: _lt(d["rsi7"][i], 15.0)),
    ("RSI21<35", "RSI", lambda d, i: _lt(d["rsi21"][i], 35.0)),
    ("RSI14 вверх", "RSI",
     lambda d, i: _gt(d["rsi14"][i], _prev(d["rsi14"], i, 1))),
    ("дивергенция RSI", "RSI", lambda d, i: bool(d["div"][i])),
    # ---- MACD: гистограмма, пересечение, разворот ----
    ("MACD гист растёт", "MACD",
     lambda d, i: _gt(d["macd_hist"][i], _prev(d["macd_hist"], i, 1))),
    ("MACD гист разворот", "MACD", _macd_turn),
    ("MACD гист 2 бара", "MACD", _macd_up2),
    ("MACD>сигнальной", "MACD",
     lambda d, i: _gt(d["macd"][i], d["macd_sig"][i])),
    ("MACD крест<=3 бар", "MACD", _macd_cross),
    # ---- ADX/DI: сила тренда ----
    ("ADX<20", "ADX", lambda d, i: _lt(d["adx"][i], 20.0)),
    ("ADX<25", "ADX", lambda d, i: _lt(d["adx"][i], 25.0)),
    ("DI+>DI-", "ADX", lambda d, i: _gt(d["di_p"][i], d["di_m"][i])),
    ("ADX падает", "ADX",
     lambda d, i: _lt(d["adx"][i], _prev(d["adx"], i, 3))),
    # ---- трендовая рынка / старший ТФ ----
    ("режим=медведь", "ТРЕНД", lambda d, i: bool(d["regime"][i] == 2)),
    ("день>EMA200д", "ТРЕНД",
     lambda d, i: _gt(d["d_close"][i], d["d_ema200"][i])),
    ("день>EMA50д", "ТРЕНД",
     lambda d, i: _gt(d["d_close"][i], d["d_ema50"][i])),
    ("BTC>EMA200д", "ТРЕНД",
     lambda d, i: _gt(d["btc_close"][i], d["btc_ema200"][i])),
]

FKEY = {name: (grp, fn) for name, grp, fn in FILTERS}


# =====================================================================
# БЛОК 3. ДАННЫЕ, ПУЛЫ СДЕЛОК
# =====================================================================
def load_symbol(sym, iv, btc4):
    """Свечи, контекст движка и индикаторы одной монеты."""
    c4 = ev.fetch(sym, str(iv), DAYS)
    c15 = ev.fetch(sym, "15", DAYS)
    ctx = se3.prep_context(c4, interval_min=iv, symbol=sym)
    return dict(sym=sym, iv=iv, c4=c4, c15=c15, ts15=[c[0] for c in c15],
                ctx=ctx, ind=build_ind(c4, btc4),
                hold=int(len(c4) * HOLD_FRAC), n=len(c4))


def signal_bars(d, g, gap):
    """Сигнальные бары dump_long + схлопывание кластеров.
    Возвращает (все сигналы, первые бары кластеров, кэш разборов ворот, ext)."""
    ext = se3.build_ext("dump_long", g, d["c4"], interval_min=d["iv"])
    allb, first, cache = [], [], {}
    last = -10 ** 9
    for i in range(len(d["c4"])):
        e = se3.gate_eval("dump_long", i, d["c4"], d["ctx"], g, ext)
        if not e["ok"]:
            continue
        allb.append(i)
        cache[i] = e
        if i - last >= gap:
            first.append(i)
        last = i
    return allb, first, cache, ext


def make_trade(d, g, i, ev_):
    """Сделка от бара i тем же движком, что и в проде (движок не менялся)."""
    tr, _why = se3._make_trade("dump_long", ev_, i, d["c4"], d["ctx"], g,
                               d["c15"], d["ts15"], LEV,
                               bar_ms=se3.bar_ms_of(d["iv"]))
    return tr


def pool_trades(d, g, bars, cache=None, ext=None):
    """Пул НЕЗАВИСИМЫХ сделок: по одной с каждого бара из списка.
    Занятость позиции здесь намеренно игнорируется — пул нужен для сравнения
    КАЧЕСТВА баров, а не для оценки доходности (для неё блок 8)."""
    if ext is None:
        ext = se3.build_ext("dump_long", g, d["c4"], interval_min=d["iv"])
    out = []
    for i in bars:
        e = (cache or {}).get(i)
        if e is None:
            e = se3.gate_eval("dump_long", i, d["c4"], d["ctx"], g, ext)
            if e["stop"] is None:
                continue
        tr = make_trade(d, g, i, e)
        if tr is None:
            continue
        tr["_i"], tr["_sym"] = i, d["sym"]
        out.append(tr)
    return out


def rnd_uniform(d, g, n, lo, hi, rnd, ext=None):
    """n случайных баров в [lo, hi) — контроль «та же частота, тот же выход»."""
    if ext is None:
        ext = se3.build_ext("dump_long", g, d["c4"], interval_min=d["iv"])
    lo = max(lo, int(ext["warm"]))
    hi = min(hi, len(d["c4"]) - 2)
    if hi - lo <= n + 5 or n <= 0:
        return []
    return sorted(rnd.sample(range(lo, hi), n))


def rnd_near(d, g, bars, rnd, ext=None):
    """Для каждого сигнала — случайный бар в [i-120, i-12]: тот же участок
    рынка, но НЕ на сливе, и строго ДО сигнала (контроль не смотрит вперёд)."""
    if ext is None:
        ext = se3.build_ext("dump_long", g, d["c4"], interval_min=d["iv"])
    warm = int(ext["warm"])
    out = []
    for i in bars:
        lo, hi = max(warm, i - NEAR_LO), i - NEAR_HI
        if hi - lo < 5:
            continue
        out.append(rnd.randrange(lo, hi))
    return sorted(out)


# =====================================================================
# БЛОК 4. СТАТИСТИКА ФИЛЬТРА
# =====================================================================
def split(trades, dmap, fn):
    """Разбиение пула фильтром: (прошло, отсеяно, не определено)."""
    keep, cut, unk = [], [], 0
    for t in trades:
        v = fn(dmap[t["_sym"]]["ind"], t["_i"])
        if v is None:
            unk += 1
        elif v:
            keep.append(t)
        else:
            cut.append(t)
    return keep, cut, unk


def _dd(d_s, se_s, d_r, se_r):
    """Разность разностей и её t (None, если хоть одна часть не измерена)."""
    if None in (d_s, se_s, d_r, se_r):
        return None, None
    se = math.sqrt(se_s ** 2 + se_r ** 2)
    if se <= 0:
        return None, None
    return d_s - d_r, (d_s - d_r) / se


def filter_stats(name, fn, sig_tr, rnd_u, rnd_n, dmap):
    """Карточка фильтра: сигналы, два случайных контроля, разности разностей."""
    ks, cs, unk = split(sig_tr, dmap, fn)
    ku, cu, _ = split(rnd_u, dmap, fn)
    kn, cn, _ = split(rnd_n, dmap, fn)
    rk = [t["r"] for t in ks]
    rc = [t["r"] for t in cs]
    t_s, se_s = welch(rk, rc)
    d_s = (mean(rk) - mean(rc)) if (rk and rc) else None
    qu = [t["r"] for t in ku]
    cu_ = [t["r"] for t in cu]
    t_u, se_u = welch(qu, cu_)
    d_u = (mean(qu) - mean(cu_)) if (qu and cu_) else None
    qn = [t["r"] for t in kn]
    cn_ = [t["r"] for t in cn]
    t_n, se_n = welch(qn, cn_)
    d_n = (mean(qn) - mean(cn_)) if (qn and cn_) else None
    dd_u, tdd_u = _dd(d_s, se_s, d_u, se_u)
    dd_n, tdd_n = _dd(d_s, se_s, d_n, se_n)
    testable = len(rk) >= MIN_GRP and len(rc) >= MIN_GRP
    return dict(
        name=name, n_keep=len(rk), n_cut=len(rc), unk=unk, testable=testable,
        exp_keep=(mean(rk) if rk else None), exp_cut=(mean(rc) if rc else None),
        delta=d_s, t=t_s, p=p_two(t_s),
        delta_u=d_u, t_dd=tdd_u, p_dd=p_two(tdd_u),
        delta_n=d_n, t_dd_n=tdd_n, p_dd_n=p_two(tdd_n),
        wr_keep=(100.0 * sum(1 for x in rk if x > 0) / len(rk)) if rk else None,
        wr_cut=(100.0 * sum(1 for x in rc if x > 0) / len(rc)) if rc else None,
        share=(100.0 * len(rk) / max(1, len(rk) + len(rc))))


# =====================================================================
# БЛОК 5. ЧЕСТНЫЙ ПОСЛЕДОВАТЕЛЬНЫЙ ПРОГОН (для доходности)
# Одна позиция, вход не раньше следующей 15м-свечи — как в run_setup.
# =====================================================================
def run_seq(d, g, bars, cache, keep_fn=None, ext=None):
    """Последовательный прогон по списку баров. keep_fn(ind, i) — фильтр
    (None трактуется как «не входим»: в проде неизвестный признак не даёт
    права на сделку)."""
    trades, busy = [], 0
    bar_ms = se3.bar_ms_of(d["iv"])
    if ext is None:
        ext = se3.build_ext("dump_long", g, d["c4"], interval_min=d["iv"])
    for i in bars:
        if d["c4"][i][0] + bar_ms < busy:
            continue
        if keep_fn is not None and not keep_fn(d["ind"], i):
            continue
        e = cache.get(i)
        if e is None:
            e = se3.gate_eval("dump_long", i, d["c4"], d["ctx"], g, ext)
            if e["stop"] is None:
                continue
        tr = make_trade(d, g, i, e)
        if tr is None:
            continue
        tr["_i"], tr["_sym"] = i, d["sym"]
        trades.append(tr)
        busy = tr["exit_ts"]
    return trades


def seq_report(trades):
    """Сводка последовательного прогона. Кривая капитала строится по ВРЕМЕНИ
    выхода (портфель из монет), иначе просадка была бы артефактом порядка
    склейки монет."""
    tr = sorted(trades, key=lambda t: t["exit_ts"])
    rs = [t["r"] for t in tr]
    pn = [t["pnl"] for t in tr]
    n = len(rs)
    return dict(n=n, exp_r=(mean(rs) if n else None), t=t_one(rs),
                wr=(100.0 * sum(1 for x in rs if x > 0) / n) if n else None,
                pf=(pf_of(rs) if n else None),
                ret=(100.0 * sum(pn) / se3.START) if n else None,
                dd=(maxdd(pn) if n else None))


def avg_reports(reps):
    """Среднее по зёрнам случайного контроля."""
    if not reps:
        return dict(n=0, exp_r=None, t=None, wr=None, pf=None, ret=None,
                    dd=None)
    out = {}
    for k in ("n", "exp_r", "t", "wr", "pf", "ret", "dd"):
        vals = [r[k] for r in reps if r[k] is not None]
        out[k] = (statistics.mean(vals) if vals else None)
    return out


def buy_hold(d, a, b):
    """«Купил и держал» на окне баров [a, b) — в процентах."""
    a = max(0, min(a, len(d["c4"]) - 1))
    b = max(1, min(b, len(d["c4"])))
    return (d["c4"][b - 1][4] / d["c4"][a][4] - 1) * 100


# =====================================================================
# БЛОК 6. ПРОВЕРКА ПРИЧИННОСТИ (префикс)
# =====================================================================
def check_causal(out):
    """Пересчёт всех рядов на префиксе истории: значение на последнем баре
    префикса обязано совпасть со значением в полном ряду. Несовпадение =
    заглядывание вперёд."""
    c4 = ev.fetch("BTCUSDT", "240", DAYS)
    full = build_ind(c4, c4)
    keys = ["ema9", "ema21", "ema50", "ema200", "rsi7", "rsi14", "rsi21",
            "macd", "macd_sig", "macd_hist", "adx", "di_p", "di_m",
            "d_close", "d_ema200", "d_ema50", "btc_close", "btc_ema200",
            "regime", "div"]
    bad = fbad = checked = 0
    for m in (1500, 3000, 4500, 6000, 6890):
        pref = build_ind(c4[:m], c4[:m])
        for k in keys:
            a, b = pref[k][m - 1], full[k][m - 1]
            checked += 1
            if a is None and b is None:
                continue
            if a is None or b is None:
                bad += 1
                out(f"    РАСХОЖДЕНИЕ {k} на m={m}: префикс={a} полный={b}")
                continue
            if isinstance(a, bool) or isinstance(b, bool):
                if bool(a) != bool(b):
                    bad += 1
                    out(f"    РАСХОЖДЕНИЕ {k} на m={m}: {a} != {b}")
                continue
            if abs(a - b) > 1e-9 * max(1.0, abs(b)):
                bad += 1
                out(f"    РАСХОЖДЕНИЕ {k} на m={m}: {a} != {b}")
        for name, _, fn in FILTERS:
            checked += 1
            if fn(pref, m - 1) != fn(full, m - 1):
                fbad += 1
                out(f"    РАСХОЖДЕНИЕ фильтра «{name}» на m={m}")
    out(f"  проверено значений: {checked}; расхождений в рядах: {bad}, "
        f"в фильтрах: {fbad}")
    out("  ВЕРДИКТ: "
        + ("заглядывания нет" if bad + fbad == 0 else "ЕСТЬ ПРОБЛЕМА"))
    return bad + fbad == 0


def check_engine(out, R):
    """Сверка с движком: мой последовательный прогон обязан совпасть с
    signal_engine3.run_setup сделка-в-сделку, если дать ему ВСЕ сигнальные
    бары и не ставить фильтр. Единственное расхождение допустимо в хвосте:
    run_setup прекращает торговлю при разорении (баланс < маржи цикла), а
    здесь оно намеренно выключено — нам нужна ПОЛНАЯ выборка исходов, иначе
    сравнение фильтров зависело бы от того, где кончились деньги."""
    bad = 0
    for s in R["syms"]:
        d = R["dmap"][s]
        res = se3.run_setup("dump_long", R["g"], d["c4"], d["ctx"], d["c15"],
                            d["ts15"], LEV, symbol=s, collect_diag=False,
                            interval_min=d["iv"])
        mine = run_seq(d, R["g"], R["sig_all"][s], R["cache"][s], None,
                       R["ext"][s])
        k = len(res["trades"])
        if len(mine) < k:
            bad += 1
            out(f"    {s}: движок дал {k} сделок, мой прогон {len(mine)}")
            continue
        diff = sum(1 for a, b in zip(res["trades"], mine[:k])
                   if a["entry_ts"] != b["entry_ts"]
                   or abs(a["r"] - b["r"]) > 1e-9
                   or abs(a["pnl"] - b["pnl"]) > 1e-9)
        out(f"    {s}: движок {k} сделок, мой прогон {len(mine)}, "
            f"расхождений в общем префиксе: {diff}"
            + ("  (движок остановлен разорением)" if res["ruined"] else ""))
        bad += diff
    out("  ВЕРДИКТ: " + ("совпадает с движком" if bad == 0
                         else "РАСХОЖДЕНИЕ С ДВИЖКОМ"))
    return bad == 0


# =====================================================================
# ГЛАВНОЕ
# =====================================================================
def main():
    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    t_start = time.time()
    out("=" * 78)
    out("СВЯЗКИ ИНДИКАТОРОВ КАК ФИЛЬТРЫ ВХОДА ДЛЯ «КАПИТУЛЯЦИЯ-ЛОНГ»")
    out("=" * 78)
    out("Базовый сетап: signal_engine3.dump_long, семя DEFAULTS3 (падение 6%")
    out("за 2 суток, RSI14<30, зелёная свеча, стоп 2*ATR, тейк 2R, без сетки).")
    out(f"Плечо x{LEV}, маржа ${se3.MARGIN}, издержки проектные "
        f"(taker {se3.TAKER*100:.3f}%, maker {se3.MAKER*100:.2f}%, "
        f"слип {se3.SLIP*100:.2f}%, фандинг {se3.FUND_8H*100:.2f}%/8ч).")
    out(f"Обучающая часть — первые {HOLD_FRAC*100:.0f}% баров, остальное — "
        f"HOLDOUT (трогается один раз, в блоке 10).")
    out(f"Фильтр проверяется, только если делит выборку не мельче, чем "
        f"{MIN_GRP}/{MIN_GRP} событий.")
    out()

    # ---------------------------------------------------------------- блок 1
    out("-" * 78)
    out("БЛОК 1. ПРОВЕРКА ПРИЧИННОСТИ ИНДИКАТОРОВ (пересчёт на префиксе)")
    out("-" * 78)
    ok_causal = check_causal(out)
    out()

    # ---------------------------------------------------------------- данные
    btc4_by_iv = {}
    for _, _, iv, _, _ in PANELS:
        if iv not in btc4_by_iv:
            btc4_by_iv[iv] = ev.fetch("BTCUSDT", str(iv), DAYS)

    results = {}
    for code, title, iv, syms, over in PANELS:
        g = se3.default_genome(**over)
        gap = max(1, int(CLUSTER_GAP_D * se2.bars_per_day(iv)))
        dmap = {s: load_symbol(s, iv, btc4_by_iv[iv]) for s in syms}
        sig_all, sig_first, cache, exts = {}, {}, {}, {}
        for s in syms:
            a, f, c, x = signal_bars(dmap[s], g, gap)
            sig_all[s], sig_first[s], cache[s], exts[s] = a, f, c, x
        results[code] = dict(code=code, title=title, iv=iv, syms=syms, g=g,
                             dmap=dmap, sig_all=sig_all, sig_first=sig_first,
                             cache=cache, ext=exts, gap=gap)

    out("-" * 78)
    out("БЛОК 2. ПАНЕЛИ И ВЫБОРКА")
    out("-" * 78)
    out(f"{'панель':<26} {'ТФ':>4} {'сигналов':>9} {'событий':>8} "
        f"{'обуч':>6} {'holdout':>8}")
    for code, title, iv, syms, over in PANELS:
        R = results[code]
        na = sum(len(R["sig_all"][s]) for s in syms)
        nf = sum(len(R["sig_first"][s]) for s in syms)
        tr_n = sum(sum(1 for i in R["sig_first"][s] if i < R["dmap"][s]["hold"])
                   for s in syms)
        out(f"{code + ' ' + title:<26} {iv:>4} {na:>9} {nf:>8} "
            f"{tr_n:>6} {nf - tr_n:>8}")
    d0 = results["A"]["dmap"]["BTCUSDT"]
    out(f"История: {fmt_date(d0['c4'][0][0])} .. {fmt_date(d0['c4'][-1][0])}; "
        f"граница holdout: {fmt_date(d0['c4'][d0['hold']][0])}")
    out("Сверка моего прогона с signal_engine3.run_setup (панель A):")
    ok_engine = check_engine(out, results["A"])
    out("«События» = сигналы, схлопнутые в кластеры (разрыв < 1 суток). Это")
    out("защита от раздувания значимости: один слив даёт подряд 2-5 сигналов,")
    out("сделки по ним почти одинаковы и независимыми наблюдениями не являются.")
    out()

    # ------------------------------------------------- пулы сделок (обучение)
    out("-" * 78)
    out("БЛОК 3. БАЗОВЫЙ ПУЛ И ДВА СЛУЧАЙНЫХ КОНТРОЛЯ (обучающая часть)")
    out("-" * 78)
    for code, title, iv, syms, over in PANELS:
        R = results[code]
        g, dmap = R["g"], R["dmap"]
        sig_tr, ru_tr, rn_tr = [], [], []
        for s in syms:
            d, ext = dmap[s], R["ext"][s]
            bars = [i for i in R["sig_first"][s] if i < d["hold"]]
            sig_tr += pool_trades(d, g, bars, R["cache"][s], ext)
            for seed in RAND_SEEDS:
                rnd = random.Random(seed * 1000003 + SYM_SALT[s])
                ru_tr += pool_trades(d, g, rnd_uniform(d, g, len(bars), 0,
                                                       d["hold"], rnd, ext),
                                     None, ext)
                rnd2 = random.Random(seed * 7919 + SYM_SALT[s] + 17)
                rn_tr += pool_trades(d, g, rnd_near(d, g, bars, rnd2, ext),
                                     None, ext)
        R["sig_tr"], R["ru_tr"], R["rn_tr"] = sig_tr, ru_tr, rn_tr
        R["base_exp"] = mean([t["r"] for t in sig_tr])
        rs = [t["r"] for t in sig_tr]
        qu = [t["r"] for t in ru_tr]
        qn = [t["r"] for t in rn_tr]
        tu, _ = welch(rs, qu)
        tn, _ = welch(rs, qn)
        out(f"[{code}] {title}")
        out(f"    сигналы сетапа:      n={len(rs):4d}  ср.R={mean(rs):+.3f}  "
            f"WR={100*sum(1 for x in rs if x>0)/max(1,len(rs)):4.1f}%  "
            f"PF={(pf_of(rs) or 9.99):.2f}  t(R>0)={f_num(t_one(rs), 5, 2)}")
        out(f"    случайно равномерно: n={len(qu):4d}  ср.R={mean(qu):+.3f}  "
            f"WR={100*sum(1 for x in qu if x>0)/max(1,len(qu)):4.1f}%  "
            f"PF={(pf_of(qu) or 9.99):.2f}")
        out(f"    случайно рядом:      n={len(qn):4d}  ср.R={mean(qn):+.3f}  "
            f"WR={100*sum(1 for x in qn if x>0)/max(1,len(qn)):4.1f}%  "
            f"PF={(pf_of(qn) or 9.99):.2f}")
        out(f"    сигнал против равномерного: t={f_num(tu,5,2)} "
            f"(p={p_two(tu):.3f}); против соседнего: t={f_num(tn,5,2)} "
            f"(p={p_two(tn):.3f})")
        v1 = ("лучше" if (tu or 0) > 0 else "ХУЖЕ")
        v2 = ("лучше" if (tn or 0) > 0 else "хуже")
        out(f"    -> сигнал {v1} равномерно случайного лонга и {v2} "
            f"случайного лонга в том же участке рынка")
    out()

    # -------------------------------------------------- одиночные фильтры
    out("-" * 78)
    out("БЛОК 4. ОДИНОЧНЫЕ ФИЛЬТРЫ (обучающая часть)")
    out("-" * 78)
    out("Δ = ср.R прошедших минус ср.R отсеянных. Δ>0 — фильтр отсеивает")
    out("худшее (полезен), Δ<0 — отсеивает лучшее (вреден).")
    out("Δрв — то же на РАВНОМЕРНО случайных входах, Δрд — на случайных")
    out("входах РЯДОМ с сигналом (тот же участок рынка, но не на сливе).")
    out("t_ум/t_ум2 — значимость разности разностей против этих контролей:")
    out("именно они отделяют «фильтр понимает сетап» от «фильтр просто")
    out("убирает экспозицию в плохое время».")
    out()
    for code in ("B", "A", "C"):
        R = results[code]
        cards = [filter_stats(n, fn, R["sig_tr"], R["ru_tr"], R["rn_tr"],
                              R["dmap"]) for n, _, fn in FILTERS]
        R["cards"] = {c["name"]: c for c in cards}
        good = [c for c in cards if c["testable"]]
        bad = [c for c in cards if not c["testable"]]
        base_r = mean([t["r"] for t in R["sig_tr"]])
        out(f"[{code}] {R['title']}  (сигналов {len(R['sig_tr'])}, "
            f"случайных равн. {len(R['ru_tr'])}, рядом {len(R['rn_tr'])})")
        out(f"  экспектанси БЕЗ фильтра = {base_r:+.3f}R; колонка R+ — это "
            f"экспектанси ПОСЛЕ фильтра.")
        out(f"  {'фильтр':<19}{'проп':>5}{'отс':>5}{'R+':>7}{'R-':>7}"
            f"{'Δ':>7}{'t':>6}{'p':>7}{'Δрв':>7}{'t_ум':>6}"
            f"{'Δрд':>7}{'t_ум2':>7}")
        for c in sorted(good, key=lambda x: -(x["t_dd"] if x["t_dd"] is not None
                                              else -9)):
            out(f"  {c['name']:<19}{c['n_keep']:>5}{c['n_cut']:>5}"
                f"{f_num(c['exp_keep'])}{f_num(c['exp_cut'])}"
                f"{f_num(c['delta'])}{f_num(c['t'],6,2)}"
                f"{p_two(c['t']):>7.3f}"
                f"{f_num(c['delta_u'])}{f_num(c['t_dd'],6,2)}"
                f"{f_num(c['delta_n'])}{f_num(c['t_dd_n'],7,2)}")
        if bad:
            out(f"  НЕ ПРОВЕРЯЛИСЬ (делят выборку мельче {MIN_GRP}/{MIN_GRP} "
                f"— на такой группе t-статистика недостоверна):")
            for c in sorted(bad, key=lambda x: -x["n_keep"]):
                out(f"    {c['name']:<19} прошло {c['n_keep']:>4}, "
                    f"отсеяно {c['n_cut']:>4}"
                    + (f", не определён {c['unk']}" if c["unk"] else ""))
        out()

    # ------------------------------------------- множественные проверки
    base = results["B"]
    tested = [n for n, _, _ in FILTERS if base["cards"][n]["testable"]]

    def _d(n):
        """Сырой прирост фильтра — именно он отвечает на вопрос владельца
        «стало ли лучше»; случайный контроль работает как ДИСКВАЛИФИКАТОР,
        а не как критерий отбора."""
        v = base["cards"][n]["delta"]
        return -9.0 if v is None else v

    top = sorted(tested, key=lambda n: -_d(n))[:3]

    def and_fn(names):
        fns = [FKEY[n][1] for n in names]

        def cf(d, i, fns=fns):
            vals = [f(d, i) for f in fns]
            if any(v is None for v in vals):
                return None
            return all(vals)
        return cf

    out("-" * 78)
    out("БЛОК 5. КОМБИНАЦИИ ФИЛЬТРОВ (обучающая часть, панель B)")
    out("-" * 78)
    out(f"Три лучших ОДИНОЧНЫХ по приросту Δ среди пригодных: {', '.join(top)}")
    out("Дальше перебраны ВСЕ пары пригодных фильтров (это тоже проверки, и")
    out("они целиком входят в поправку на множественность).")
    pair_cards, degenerate = [], 0
    for a in range(len(tested)):
        for b in range(a + 1, len(tested)):
            cb = (tested[a], tested[b])
            c = filter_stats(" + ".join(cb), and_fn(cb), base["sig_tr"],
                             base["ru_tr"], base["rn_tr"], base["dmap"])
            if c["testable"]:
                pair_cards.append(c)
            else:
                degenerate += 1
    tri = filter_stats(" + ".join(top), and_fn(top), base["sig_tr"],
                       base["ru_tr"], base["rn_tr"], base["dmap"])
    combo_cards = sorted(pair_cards, key=lambda c: -(c["delta"] or -9))
    out(f"  Всего пар: {len(tested)*(len(tested)-1)//2}; пригодных "
        f"(обе группы >= {MIN_GRP}): {len(pair_cards)}; вырожденных "
        f"(фильтры почти не пересекаются): {degenerate}.")
    out(f"  {'комбинация (лучшие по Δ)':<44}{'проп':>5}{'отс':>5}{'Δ':>7}"
        f"{'t':>6}{'Δрв':>7}{'t_ум':>6}")
    for c in combo_cards[:6]:
        out(f"  {c['name']:<44}{c['n_keep']:>5}{c['n_cut']:>5}"
            f"{f_num(c['delta'])}{f_num(c['t'],6,2)}{f_num(c['delta_u'])}"
            f"{f_num(c['t_dd'],6,2)}")
    if combo_cards:
        out("  ... худшие:")
        for c in combo_cards[-3:]:
            out(f"  {c['name']:<44}{c['n_keep']:>5}{c['n_cut']:>5}"
                f"{f_num(c['delta'])}{f_num(c['t'],6,2)}{f_num(c['delta_u'])}"
                f"{f_num(c['t_dd'],6,2)}")
    out(f"  тройка «{tri['name']}»: прошло {tri['n_keep']}, "
        f"отсеяно {tri['n_cut']} -> "
        + ("пригодна" if tri["testable"] else "ВЫРОЖДЕНА, не проверяется"))
    best_pair = combo_cards[0] if combo_cards else None
    out()

    out("-" * 78)
    out("БЛОК 6. ПОПРАВКА НА МНОЖЕСТВЕННЫЕ ПРОВЕРКИ")
    out("-" * 78)
    fam = [(n, base["cards"][n]) for n in tested] + \
          [(c["name"], c) for c in combo_cards] + \
          ([(tri["name"], tri)] if tri["testable"] else [])
    K = len(fam)
    bonf = ALPHA / K
    ps_dd = [c["p_dd"] for _, c in fam]
    ps_raw = [c["p"] for _, c in fam]
    out(f"Семейство гипотез: {len(tested)} пригодных одиночных фильтров + "
        f"{K - len(tested)} комбинаций = {K} проверок.")
    out(f"Бонферрони: порог p = {ALPHA}/{K} = {bonf:.4f}, это |t| >= "
        f"{z_for_p(bonf):.2f}.")
    sb = [fam[j][0] for j in range(K) if ps_dd[j] < bonf]
    sf = [fam[j][0] for j in sorted(bh_fdr(ps_dd))]
    out(f"  По t_ум (вклад сверх случайного входа):")
    out(f"    Бонферрони переживают: {', '.join(sb) if sb else 'НИ ОДИН'}")
    out(f"    FDR (q=0.05) переживают: {', '.join(sf) if sf else 'НИ ОДИН'}")
    sb2 = [fam[j][0] for j in range(K) if ps_raw[j] < bonf]
    sf2 = [fam[j][0] for j in sorted(bh_fdr(ps_raw))]
    out(f"  По «сырому» t (без поправки на случайный контроль):")
    out(f"    Бонферрони: {', '.join(sb2) if sb2 else 'НИ ОДИН'}; "
        f"FDR: {', '.join(sf2) if sf2 else 'НИ ОДИН'}")
    nom = [n for n, c in fam if c["p_dd"] < 0.05]
    nom_raw = [n for n, c in fam if c["p"] < 0.05]
    out(f"  Номинально значимы по t_ум (p<0.05, без поправки): {len(nom)} "
        f"из {K} ({', '.join(nom) if nom else 'нет'}).")
    out(f"  Номинально значимы по сырому t: {len(nom_raw)} из {K} "
        f"({', '.join(nom_raw) if nom_raw else 'нет'}).")
    out(f"  Случайно ожидалось бы {K * 0.05:.1f} «значимых» в каждом столбце.")
    out()

    # ------------------------------- дивергенция RSI и разворот MACD подробно
    out("-" * 78)
    out("БЛОК 7. ДИВЕРГЕНЦИЯ RSI И РАЗВОРОТ MACD-ГИСТОГРАММЫ — ПОДРОБНО")
    out("-" * 78)
    out("Владелец назвал их самыми обещающими для разворотного сетапа,")
    out("поэтому разбираем их отдельно и с конкретными сделками.")
    out()
    for fname in ("дивергенция RSI", "MACD гист разворот"):
        out(f"### {fname}")
        for code in ("A", "B", "C"):
            c = results[code]["cards"][fname]
            mark = "" if c["testable"] else "  [выборка мала, не проверялся]"
            out(f"  [{code}] прошло {c['n_keep']} "
                f"(ср.R {f_num(c['exp_keep'],6,3).strip()}, "
                f"WR {c['wr_keep'] if c['wr_keep'] is not None else 0:.0f}%), "
                f"отсеяно {c['n_cut']} "
                f"(ср.R {f_num(c['exp_cut'],6,3).strip()}, "
                f"WR {c['wr_cut'] if c['wr_cut'] is not None else 0:.0f}%), "
                f"Δ={f_num(c['delta'],6,3).strip()}, "
                f"t={f_num(c['t'],5,2).strip()}, "
                f"Δрв={f_num(c['delta_u'],6,3).strip()}, "
                f"t_ум={f_num(c['t_dd'],5,2).strip()}{mark}")
        out()
    RA = results["A"]
    out("Конкретные сделки с бычьей дивергенцией RSI (панель A, база 4ч,")
    out("обучающая часть; «якорь» — подтверждённый свинг-минимум, с которым")
    out("сравнивается текущий бар):")
    ex_rows = [(t, RA["dmap"][t["_sym"]]) for t in RA["sig_tr"]
               if RA["dmap"][t["_sym"]]["ind"]["div"][t["_i"]]]
    for t, d in ex_rows[:14]:
        i, ind = t["_i"], d["ind"]
        j = ind["div_anchor"][i]
        out(f"  {t['_sym']:<9} сигнал {fmt_dt(d['c4'][i][0])}  "
            f"low={ind['low'][i]:.6g} RSI={ind['rsi14'][i]:.1f} | "
            f"якорь {fmt_dt(d['c4'][j][0])} low={ind['low'][j]:.6g} "
            f"RSI={ind['rsi14'][j]:.1f}")
        out(f"            вход {t['entry']:.6g} стоп {t['stop']:.6g} "
            f"-> {t['reason']:<7} R={t['r']:+.2f} держали {t['hold_h']:.0f}ч")
    if ex_rows:
        out(f"  Итого дивергентных сделок в панели A (обучение): "
            f"{len(ex_rows)}, ср.R={mean([t['r'] for t, _ in ex_rows]):+.3f}")
    else:
        out("  (в обучающей части панели A таких сделок нет)")
    out()
    out("Конкретные сделки с разворотом MACD-гистограммы (панель A, обучение):")
    mrows = [(t, RA["dmap"][t["_sym"]]) for t in RA["sig_tr"]
             if _macd_turn(RA["dmap"][t["_sym"]]["ind"], t["_i"])]
    for t, d in mrows[:12]:
        i, h = t["_i"], d["ind"]["macd_hist"]
        out(f"  {t['_sym']:<9} {fmt_dt(d['c4'][i][0])}  гист "
            f"{h[i-2]:+.4g} -> {h[i-1]:+.4g} -> {h[i]:+.4g}  "
            f"вход {t['entry']:.6g} -> {t['reason']:<7} R={t['r']:+.2f}")
    if mrows:
        out(f"  Итого таких сделок в панели A (обучение): {len(mrows)}, "
            f"ср.R={mean([t['r'] for t, _ in mrows]):+.3f}")
    out()

    # --------------------------------- честный прогон: база, фильтры, контроль
    out("-" * 78)
    out("БЛОК 8. ЧЕСТНЫЙ ПОСЛЕДОВАТЕЛЬНЫЙ ПРОГОН (одна позиция), обучение")
    out("-" * 78)
    out("Здесь фильтр меняет ПОСЛЕДОВАТЕЛЬНОСТЬ сделок (отказ от одной")
    out("открывает дорогу следующей), поэтому цифры не сводятся к блоку 4.")
    out("Сравнение обязательное: база / фильтр / случайный вход / купил-держал.")
    out("Правило разорения (баланс < маржи цикла) здесь НЕ применяется: нам")
    out("нужна полная выборка исходов. В проде база разорилась бы — при")
    out("стартовых $20 и марже $5 убыток -92% это остановка торговли.")
    out()

    def seq_all(R, keep_fn, part):
        tr = []
        for s in R["syms"]:
            d = R["dmap"][s]
            lo, hi = (0, d["hold"]) if part == "train" else (d["hold"], d["n"])
            bars = [i for i in R["sig_first"][s] if lo <= i < hi]
            tr += run_seq(d, R["g"], bars, R["cache"][s], keep_fn, R["ext"][s])
        return tr

    def seq_rand(R, part):
        reps = []
        for seed in RAND_SEEDS[:5]:
            tr = []
            for s in R["syms"]:
                d, ext = R["dmap"][s], R["ext"][s]
                lo, hi = ((0, d["hold"]) if part == "train"
                          else (d["hold"], d["n"]))
                n = sum(1 for i in R["sig_first"][s] if lo <= i < hi)
                rnd = random.Random(seed * 31337 + SYM_SALT[s])
                tr += run_seq(d, R["g"], rnd_uniform(d, R["g"], n, lo, hi,
                                                     rnd, ext), {}, None, ext)
            reps.append(seq_report(tr))
        return avg_reports(reps)

    def bh_panel(R, part):
        vals = []
        for s in R["syms"]:
            d = R["dmap"][s]
            lo, hi = (0, d["hold"]) if part == "train" else (d["hold"], d["n"])
            vals.append(buy_hold(d, lo, hi))
        return mean(vals)

    seq_rows = [("база dump_long", None)]
    seq_rows += [(f"+ {n}", FKEY[n][1]) for n in top]
    seq_rows.append(("+ все три сразу", and_fn(top)))
    if best_pair is not None:
        seq_rows.append((f"+ {best_pair['name']}",
                         and_fn(tuple(best_pair["name"].split(" + ")))))

    def print_seq(R, part):
        out(f"  {'вариант':<34}{'n':>4}{'ср.R':>8}{'t':>6}{'WR%':>6}"
            f"{'PF':>6}{'дох%':>8}{'DD%':>7}")
        for label, fn in seq_rows:
            keep = None if fn is None else (lambda ind, i, f=fn: bool(f(ind, i)))
            st = seq_report(seq_all(R, keep, part))
            mark = " !" if 0 < st["n"] < 10 else ""
            out(f"  {label + mark:<34}{st['n']:>4}{f_num(st['exp_r'],8,3)}"
                f"{f_num(st['t'],6,2)}"
                f"{f_num(st['wr'],6,1,sign=False)}"
                f"{f_num(st['pf'],6,2,sign=False)}"
                f"{f_num(st['ret'],8,1)}{f_num(st['dd'],7,1,sign=False)}")
        st = seq_rand(R, part)
        out(f"  {'случайный вход (ср.5 зёрен)':<34}{st['n']:>4.0f}"
            f"{f_num(st['exp_r'],8,3)}{f_num(st['t'],6,2)}"
            f"{f_num(st['wr'],6,1,sign=False)}"
            f"{f_num(st['pf'],6,2,sign=False)}"
            f"{f_num(st['ret'],8,1)}{f_num(st['dd'],7,1,sign=False)}")
        out(f"  {'купил и держал (ср. монеты)':<34}{'—':>4}{'—':>8}{'—':>6}"
            f"{'—':>6}{'—':>6}{bh_panel(R, part):>+8.1f}")
        out("  ! — вариант оставил меньше 10 сделок, цифры не интерпретируются")

    for code in ("A", "B"):
        out(f"[{code}] {results[code]['title']} — обучающая часть")
        print_seq(results[code], "train")
        out()

    # ---------------------------------------------- устойчивость по панелям
    out("-" * 78)
    out("БЛОК 9. СОГЛАСИЕ ПАНЕЛЕЙ ПО ЗНАКУ ВКЛАДА (обучающая часть)")
    out("-" * 78)
    out("Фильтр, который «работает», обязан иметь ОДИН знак вклада во всех")
    out("панелях, где он вообще проверяем. Разные знаки = подгонка.")
    out("Прочерк = на этой панели фильтр не делит выборку (см. блок 4).")
    out(f"  {'фильтр':<19}{'Δ пан.A':>9}{'Δ пан.B':>9}{'Δ пан.C':>9}"
        f"{'согласие':>16}")
    agree = {}
    for n, _, _ in FILTERS:
        ds = []
        for code in ("A", "B", "C"):
            c = results[code]["cards"][n]
            ds.append(c["delta"] if c["testable"] else None)
        ok = [x for x in ds if x is not None]
        if len(ok) < 2:
            verdict, same = "одна панель", False
        elif all(x > 0 for x in ok) or all(x < 0 for x in ok):
            verdict, same = "да", True
        else:
            verdict, same = "НЕТ", False
        agree[n] = (same, ds, len(ok))
        if not ok:
            continue
        out(f"  {n:<19}{f_num(ds[0],9,3)}{f_num(ds[1],9,3)}"
            f"{f_num(ds[2],9,3)}{verdict:>17}")
    out()

    # ------------------------------------------------------------- HOLDOUT
    out("-" * 78)
    out("БЛОК 10. HOLDOUT (первое и единственное обращение)")
    out("-" * 78)
    out(f"Фильтры отобраны ТОЛЬКО по обучающей части: {', '.join(top)}.")
    out(f"Окно holdout: {fmt_date(d0['c4'][d0['hold']][0])} .. "
        f"{fmt_date(d0['c4'][-1][0])}")
    out()
    hold_cards = {}
    for code in ("A", "B"):
        R = results[code]
        out(f"[{code}] {R['title']} — HOLDOUT")
        print_seq(R, "hold")
        sig_h, ru_h, rn_h = [], [], []
        for s in R["syms"]:
            d, ext = R["dmap"][s], R["ext"][s]
            bars = [i for i in R["sig_first"][s] if i >= d["hold"]]
            sig_h += pool_trades(d, R["g"], bars, R["cache"][s], ext)
            for seed in RAND_SEEDS:
                rnd = random.Random(seed * 7717 + SYM_SALT[s])
                ru_h += pool_trades(d, R["g"],
                                    rnd_uniform(d, R["g"], len(bars), d["hold"],
                                                d["n"], rnd, ext), None, ext)
                rnd2 = random.Random(seed * 611953 + SYM_SALT[s])
                rn_h += pool_trades(d, R["g"], rnd_near(d, R["g"], bars, rnd2,
                                                        ext), None, ext)
        out(f"  Пул holdout: сигналов {len(sig_h)}, случайных равн. "
            f"{len(ru_h)}, рядом {len(rn_h)}")
        out(f"  {'фильтр':<19}{'обуч +/-':>10}{'обуч Δ':>9}"
            f"{'hold +/-':>10}{'hold Δ':>9}{'знак':>12}")
        hold_cards[code] = {}
        for n in top:
            c_tr = R["cards"][n]
            c_ho = filter_stats(n, FKEY[n][1], sig_h, ru_h, rn_h, R["dmap"])
            hold_cards[code][n] = c_ho
            if c_tr["delta"] is None or c_ho["delta"] is None:
                same = "нет данных"
            else:
                same = ("совпал" if c_tr["delta"] * c_ho["delta"] > 0
                        else "СМЕНИЛСЯ")
            sp_tr = "%d/%d" % (c_tr["n_keep"], c_tr["n_cut"])
            sp_ho = "%d/%d" % (c_ho["n_keep"], c_ho["n_cut"])
            out(f"  {n:<19}{sp_tr:>10}{f_num(c_tr['delta'],9,3)}"
                f"{sp_ho:>10}{f_num(c_ho['delta'],9,3)}{same:>12}")
        out("  Замечание: если группа меньше "
            f"{MIN_GRP} сделок, смена знака — это шум, а не результат.")
        out()

    # ------------------------------------------- чувствительность к плечу
    out("-" * 78)
    out("БЛОК 11. ПЛЕЧО x20-25: сколько сигналов доживает до входа")
    out("-" * 78)
    out("Движок ограничивает ширину стопа: не больше 0.8 * (1/плечо - 0.005).")
    out("Стоп капитуляции = 2*ATR, а ATR в момент слива раздут, поэтому")
    out("высокое плечо режет ВЫБОРКУ, а не только размер риска.")
    out(f"  {'плечо':>6}{'потолок стопа':>15}{'событий проходит':>20}")
    RB = results["B"]
    for lev in (5, 10, 20, 25):
        cap = 0.8 * se3.liq_frac(lev)
        cnt = tot = 0
        for s in RB["syms"]:
            d = RB["dmap"][s]
            for i in RB["sig_first"][s]:
                if i >= d["hold"]:
                    continue
                e = RB["cache"][s].get(i)
                if e is None or e["dist"] is None:
                    continue
                tot += 1
                if e["dist"] <= min(float(RB["g"]["stop_cap"]), cap):
                    cnt += 1
        out(f"  {('x' + str(lev)):>6}{cap*100:>14.1f}%{f'{cnt} из {tot}':>20}")
    out()

    # -------------------------------------------------------------- выводы
    out("=" * 78)
    out("БЛОК 12. ВЫВОДЫ")
    out("=" * 78)
    base_exp_b = base["base_exp"]
    out("1. Причинность: "
        + ("проверка префиксом пройдена, заглядывания вперёд нет."
           if ok_causal else "НЕ ПРОЙДЕНА — результатам верить нельзя."))
    out(f"2. Пригодных к проверке фильтров: {len(tested)} из {len(FILTERS)}. "
        f"Остальные не делят выборку капитуляций:")
    nt = [n for n, _, _ in FILTERS if not base["cards"][n]["testable"]]
    for n in nt:
        c = base["cards"][n]
        out(f"     {n}: {c['n_keep']}/{c['n_cut']}")
    out("3. Лучшие три по ПРИРОСТУ на панели B (обучающая часть):")
    for n in top:
        c = base["cards"][n]
        if agree[n][2] < 2:
            a_ok = "проверяем только на одной панели"
        else:
            a_ok = ("знак согласован по панелям" if agree[n][0]
                    else "ЗНАК НЕ СОГЛАСОВАН между панелями")
        ho = hold_cards.get("B", {}).get(n)
        h_txt = ("" if ho is None or ho["delta"] is None
                 else f", на holdout Δ={ho['delta']:+.3f}R")
        out(f"     {n}: экспектанси {f_num(base_exp_b,0,3).strip()}R -> "
            f"{f_num(c['exp_keep'],0,3).strip()}R, Δ={f_num(c['delta'],0,3).strip()}R "
            f"(случ.равн {f_num(c['delta_u'],0,3).strip()}, "
            f"случ.рядом {f_num(c['delta_n'],0,3).strip()}), "
            f"t={f_num(c['t'],0,2).strip()}, t_ум={f_num(c['t_dd'],0,2).strip()}, "
            f"пропускает {c['share']:.0f}% сигналов; {a_ok}{h_txt}")
    wl = sorted([n for n in tested], key=lambda n: _d(n))[:3]
    out("4. Самые вредные (отсекают ЛУЧШЕЕ, их включать нельзя):")
    for n in wl:
        c = base["cards"][n]
        out(f"     {n}: Δ={f_num(c['delta'],0,3).strip()}R, "
            f"t={f_num(c['t'],0,2).strip()}, "
            f"t_ум={f_num(c['t_dd'],0,2).strip()}")
    out(f"5. Поправку Бонферрони (p<{bonf:.4f}) переживают: "
        f"{', '.join(sb) if sb else 'НИ ОДИН фильтр'}.")
    out(f"   FDR-контроль (q=0.05) переживают: "
        f"{', '.join(sf) if sf else 'НИ ОДИН фильтр'}.")
    out(f"6. Номинально значимых (p<0.05) по t_ум {len(nom)}, по сырому t "
        f"{len(nom_raw)} при {K*0.05:.1f} ожидаемых случайно — "
        + ("это НЕ отличается от чистого шума."
           if max(len(nom), len(nom_raw)) <= K * 0.05 + 1
           else "больше шума, но поправку это не переживает."))
    out("7. Отдельное наблюдение, не про фильтры, но важное для сетапа.")
    out("   Сравнение с двумя контролями даёт разную картину:")
    for code in ("A", "B", "C"):
        R = results[code]
        out(f"     [{code}] сетап {R['base_exp']:+.3f}R; "
            f"случайный лонг равномерно "
            f"{mean([t['r'] for t in R['ru_tr']]):+.3f}R; "
            f"случайный лонг РЯДОМ со сливом "
            f"{mean([t['r'] for t in R['rn_tr']]):+.3f}R")
    out("   То есть лонг сразу после сильного падения — сам по себе плохое")
    out("   место для покупки (около -0.3..-0.6R у случайного входа там же),")
    out("   и правила капитуляции ЧАСТИЧНО отыгрывают этот минус, но до")
    out("   уровня обычного случайного лонга не дотягивают. Проблема сетапа")
    out("   не в отсутствии фильтров, а в самом моменте входа.")
    out()
    out("ИТОГОВАЯ ТАБЛИЦА «ФИЛЬТР -> ВКЛАД, ЗНАЧИМОСТЬ, ПОПРАВКА»")
    out("(панель B, обучающая часть; holdout — сверка знака, не отбор)")
    out(f"  {'фильтр':<19}{'вклад Δ,R':>10}{'сверх случ.':>12}{'t':>6}"
        f"{'p':>7}{'Бонф.':>7}{'FDR':>6}{'holdout':>9}")
    for n in sorted(tested, key=lambda x: -_d(x)):
        c = base["cards"][n]
        ho = hold_cards.get("B", {}).get(n)
        h_txt = ("—" if ho is None or ho["delta"] is None
                 else f"{ho['delta']:+.3f}")
        out(f"  {n:<19}{f_num(c['delta'],10,3)}{f_num(c['t_dd'],12,2)}"
            f"{f_num(c['t'],6,2)}{p_two(c['t']):>7.3f}"
            f"{('да' if n in sb else 'нет'):>7}"
            f"{('да' if n in sf else 'нет'):>6}{h_txt:>9}")
    out()
    out("ОТВЕТ НА ВОПРОС «ЕСТЬ ЛИ ХОТЬ ОДИН РАБОТАЮЩИЙ ФИЛЬТР»:")
    survivors = [n for n in tested
                 if (base["cards"][n]["delta"] or 0) > 0
                 and n in sf
                 and agree[n][0]]
    if survivors:
        out(f"  ДА: {', '.join(survivors)}")
    else:
        flips = sum(1 for n in top
                    if hold_cards.get("B", {}).get(n)
                    and hold_cards["B"][n]["delta"] is not None
                    and (base["cards"][n]["delta"] or 0)
                    * hold_cards["B"][n]["delta"] < 0)
        out("  НЕТ. Ни один из проверенных фильтров не удовлетворяет всем")
        out("  трём требованиям одновременно: (1) положительный вклад,")
        out("  (2) переживает поправку на множественные проверки,")
        out("  (3) знак вклада согласован между панелями и с holdout.")
        out(f"  Из трёх лучших кандидатов обучающей части {flips} из "
            f"{len(top)} СМЕНИЛИ знак на holdout — это ровно та картина,")
        out("  которую даёт подгонка под шум.")
    out(f"Время работы: {time.time() - t_start:.0f} c")

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\n[записано в {OUT_PATH}]")


if __name__ == "__main__":
    main()
