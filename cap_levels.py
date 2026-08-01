# -*- coding: utf-8 -*-
"""Капитуляция-лонг (dump_long): помогает ли привязка к ГОРИЗОНТАЛЬНЫМ
ОБЪЁМНЫМ и ЦЕНОВЫМ УРОВНЯМ, какой стоп лучше и работает ли SMART MONEY.

ЗАЧЕМ. Владелец просил посмотреть «отскок от горизонтальных объёмов, уровней
со стопами, смартмани». На сетапе dump_long эту группу идей ещё не мерили —
это единственная непроверенная группа. Всё остальное про сетап уже известно:
в честном отборе v12 dump_long@240 давал +0.544R на 12 сделках, но при смене
зерна отбора воспроизводился 1 раз из 3; а в тесте идей с ФИКСИРОВАННЫМИ
параметрами капитуляция-лонг оказалась ХУЖЕ случайного входа (-0.215R).
Значит сырого преимущества у идеи нет, и любая «привязка к уровню» обязана
доказываться против случайного входа той же частоты, а не против нуля.

ЧТО ИМЕННО ПРОВЕРЯЕТСЯ (заявлено ДО просмотра результатов)
  1. ОБЪЁМНЫЕ УРОВНИ. Скользящий профиль объёма за 30 и 90 суток: корзины
     цен в ЛОГ-шкале (шаг 0.25%), объём бара (turnover, USD) размазывается
     равномерно по его диапазону low..high. Из профиля: POC, зона стоимости
     70% (value area), и главная метрика — ПЛОТНОСТЬ объёма в точке остановки
     падения: dens = (объём в полосе +-0.5 ATR вокруг минимума падения на
     корзину) / (средний объём на корзину во всём профиле). dens>=1 — падение
     встало НА объёме, dens<1 — в пустоте. Гипотеза: dens>=1 отскакивает лучше.
  2. ЦЕНОВЫЕ УРОВНИ БЕЗ ОБЪЁМА: предыдущие свинг-минимумы (пивот w=6,
     подтверждение через 6 баров), круглые числа (шаг — крупнейшее из
     {1,2,5}x10^k, не превышающее 2% цены), минимум диапазона ДО падения.
  3. СТОПЫ: (а) за минимумом падения, (б) под ближайшим объёмным кластером,
     (в) под свинг-минимумом, (г) k*ATR при k=1.5/2.0/3.0.
  4. SMART MONEY (smc.py): бычий ордер-блок, бычий FVG, структурный тренд
     BOS/CHoCH — как фильтр входа.

МЕТОДОЛОГИЯ (нарушение = брак)
  - Обучающая часть [0..hold), hold = 72% баров истории. HOLDOUT [hold..n) —
    ТОЛЬКО финальная проверка: ни один порог, ни одна монета, ни один вариант
    стопа не выбираются по нему.
  - Каждое «улучшение» — три сравнения: против базового dump_long без
    улучшения; против СЛУЧАЙНОГО входа той же частоты (та же сторона, тот же
    стоп, тот же выход, случаен только момент); против «купил и держал».
  - Значимость: t Уэлча по сделкам + поправка Бонферрони на число заявленных
    проверок. Дополнительно блочный бутстрэп по КАЛЕНДАРНЫМ КВАРТАЛАМ, потому
    что в этом проекте уже измерено: разницу результатов объясняет календарное
    окно (39.4% дисперсии), а не монета (1.1%) — значит сделки внутри одного
    квартала зависимы и наивный t завышен.
  - Никакого заглядывания вперёд: все ряды считаются префиксом и это
    проверяется явно (блок САМОПРОВЕРКИ).
  - Отрицательный результат — валидный результат.

ДАННЫЕ. Свечи 4ч и 15м — кэш проекта (ev.fetch). Объёма в этом кэше НЕТ
(хранятся [ts,o,h,l,c]), поэтому объёмы качаются отдельно через pybit
get_kline(category="linear") в СВОЙ кэш volcache_{sym}_{iv}m_{days}d.json
формата [ts,o,h,l,c,volume,turnover]. Чужие файлы не трогаются.

Издержки проектные: TAKER 0.055%, MAKER 0.02%, SLIP 0.03%, FUND 0.01%/8ч,
исполнение — signal_engine3.simulate_grid_trade (стоп и тейк в одной 15м
свече -> СТОП).

РЕЗУЛЬТАТ (кратко, подробности и все числа — в cap_levels_out.txt):
  Гипотеза владельца НЕ подтвердилась, и знак обратный. Из 42 контрастов
  строгий порог Бонферрони прошли 5, но при переносе на holdout НИ ОДИН из
  них не сохранил знак. Сохранили знак только три, и все три говорят
  одно: капитуляция, упершаяся в объёмный уровень, отскакивает ХУЖЕ
  ушедшей в пустоту (обучение -0.29R, holdout -0.61R). Варианты стопа
  между собой статистически неотличимы (макс |t| = 0.72), стоп «под
  объёмным кластером» применим лишь к трети сигналов. SMC (order blocks,
  FVG, BOS/CHoCH) не работает: ордер-блок как фильтр отбирает сделки хуже
  отсеянных, причём тот же знак виден и на случайных входах.
  Плечо x20-25 при честном стопе физически отсекает половину сигналов.

Запуск: python cap_levels.py     (вывод дублируется в cap_levels_out.txt)
"""

import bisect
import json
import math
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import evolution as ev
import patterns as pt
import signal_engine3 as se3
import smc

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT"]
DAYS = 1150
IV = 240                       # сигнальный ТФ — 4ч (на нём dump_long прошёл экзамен)
VOL_IV = "60"                  # ТФ объёмных данных для профиля
BARS_DAY_4H = 6
HOLD_DAYS = 30
HOLD15 = HOLD_DAYS * 96
LEV = 5                        # базовое плечо (см. блок про x20-25 в конце)
HOLD_FRAC = 0.72               # доля истории на обучение

# профиль объёма
BSTEP = 0.0025                 # ширина ценовой корзины, 0.25%
LOG_STEP = math.log(1.0 + BSTEP)
VA_FRAC = 0.70                 # зона стоимости 70%
HVN_Q = 0.70                   # корзина «объёмная», если выше 70-го перцентиля
BAND_ATR = 0.5                 # полуширина полосы плотности, в ATR

# заранее объявленные пороги и число проверок (Бонферрони)
DENS_THR = 1.0                 # dens>=1 — «на объёме», <1 — «в пустоте»
NEAR_ATR = 0.5                 # «у уровня» — ближе 0.5 ATR
ROUND_FRAC = 0.20              # «на круглом» — ближе 20% полушага
N_TESTS = 43                   # всего заявленных проверок
RAND_SEEDS = [11, 22, 33, 44, 55]

MS_4H = 4 * 3600 * 1000
MS_1H = 3600 * 1000

TAKER = se3.TAKER
MIN_STOP = se3.MIN_STOP
MARGIN = se3.MARGIN
START = se3.START

_LINES = []
_TESTS = []          # реестр всех контрастов: (имя, t, n_a, n_b, dexp)


def out(s=""):
    print(s)
    _LINES.append(s)


# ------------------------------------------------------------------ статистика
def p_two_sided(t):
    return math.erfc(abs(t) / math.sqrt(2.0))


def z_for_p(p):
    lo, hi = 0.0, 12.0
    for _ in range(90):
        mid = (lo + hi) / 2
        if math.erfc(mid / math.sqrt(2.0)) > p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def welch_t(a, b):
    """t Уэлча: отличается ли среднее a от среднего b."""
    if len(a) < 3 or len(b) < 3:
        return 0.0
    va = statistics.variance(a) / len(a)
    vb = statistics.variance(b) / len(b)
    if va + vb <= 0:
        return 0.0
    return (statistics.mean(a) - statistics.mean(b)) / math.sqrt(va + vb)


def t_one(rs):
    """t против нуля по пулу сделок."""
    if len(rs) < 3:
        return 0.0
    sd = statistics.pstdev(rs)
    if sd <= 0:
        return 0.0
    return statistics.mean(rs) / (sd / math.sqrt(len(rs)))


def block_boot_p(rows_a, rows_b, seed=7, n_iter=4000):
    """Блочный бутстрэп по КАЛЕНДАРНЫМ КВАРТАЛАМ: ресэмплим кварталы, а не
    отдельные сделки. Внутри квартала сделки зависимы (общий рынок), поэтому
    наивный t завышает значимость. Возвращает долю выборок, где разница
    средних R (a-b) <= 0."""
    if len(rows_a) < 5 or len(rows_b) < 5:
        return 1.0
    qa, qb = {}, {}
    for r in rows_a:
        qa.setdefault(r["q"], []).append(r["r"])
    for r in rows_b:
        qb.setdefault(r["q"], []).append(r["r"])
    quarters = sorted(set(qa) | set(qb))
    if len(quarters) < 4:
        return 1.0
    rnd = random.Random(seed)
    bad = 0
    k = len(quarters)
    for _ in range(n_iter):
        pick = [quarters[rnd.randrange(k)] for _ in range(k)]
        sa = [x for q in pick for x in qa.get(q, ())]
        sb = [x for q in pick for x in qb.get(q, ())]
        if len(sa) < 3 or len(sb) < 3:
            bad += 1
            continue
        if statistics.mean(sa) - statistics.mean(sb) <= 0:
            bad += 1
    return bad / n_iter


def agg(rows):
    """Сводка по набору сделок."""
    n = len(rows)
    if not n:
        return dict(n=0, exp_r=0.0, wr=0.0, pf=None, ret=0.0, t=0.0,
                    stop_pct=0.0, mfe=0.0, mae=0.0)
    rs = [x["r"] for x in rows]
    gp = sum(x["pnl"] for x in rows if x["pnl"] > 0)
    gl = -sum(x["pnl"] for x in rows if x["pnl"] < 0)
    return dict(
        n=n, exp_r=statistics.mean(rs),
        wr=100.0 * sum(1 for x in rows if x["pnl"] > 0) / n,
        pf=(gp / gl if gl > 0 else None),
        ret=100.0 * sum(x["pnl"] for x in rows) / START,
        t=t_one(rs),
        stop_pct=100.0 * statistics.mean([x["stop_frac"] for x in rows]),
        mfe=statistics.mean([x["mfe_r"] for x in rows]),
        mae=statistics.mean([x["mae_r"] for x in rows]),
    )


def fmt_pf(pf):
    return "  inf" if pf is None else f"{pf:5.2f}"


def line(tag, a, extra=""):
    # при n<10 t по пулу сделок бессмысленна (несколько одинаковых стопов дают
    # почти нулевую дисперсию и гигантскую t) — помечаем явно
    warn = "  !мало" if 0 < a["n"] < 10 else ""
    return (f"  {tag:34} n={a['n']:4}  exp_r={a['exp_r']:+7.3f}  "
            f"WR={a['wr']:5.1f}%  PF={fmt_pf(a['pf'])}  "
            f"ret={a['ret']:+8.1f}%  t={a['t']:+5.2f}  "
            f"стоп={a['stop_pct']:4.2f}%{extra}{warn}")


def concentration(rows):
    """Какую долю всей положительной R дают 3 лучшие сделки. Близко к 1 —
    «преимущество» держится на паре везучих сделок."""
    pos = sorted((x["r"] for x in rows if x["r"] > 0), reverse=True)
    tot = sum(pos)
    return (sum(pos[:3]) / tot) if tot > 0 else 1.0


# ------------------------------------------------------------- объёмные данные
def fetch_vol(symbol, interval, days):
    """[ts,o,h,l,c,volume,turnover] в СВОЙ кэш. В кэше свечей проекта объёма
    нет (только [ts,o,h,l,c]), поэтому качаем отдельно."""
    cache = os.path.join(BASE, f"volcache_{symbol}_{interval}m_{days}d.json")
    if os.path.exists(cache):
        with open(cache) as fh:
            return json.load(fh)
    from pybit.unified_trading import HTTP
    session = HTTP(testnet=False)
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    raw, cursor = [], end
    while cursor > start:
        r = session.get_kline(category="linear", symbol=symbol,
                              interval=interval, limit=1000, end=cursor)
        rows = r["result"]["list"]
        if not rows:
            break
        raw = rows[::-1] + raw
        oldest = int(rows[-1][0])
        if oldest >= cursor:
            break
        cursor = oldest - 1
        time.sleep(0.05)
    bars = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]),
             float(x[5]), float(x[6])] for x in raw if int(x[0]) >= start][:-1]
    with open(cache, "w") as fh:
        json.dump(bars, fh)
    return bars


# ------------------------------------------------------------- профиль объёма
def _bkt(p):
    """Индекс ценовой корзины в ЛОГ-шкале: корзина одинаковой ОТНОСИТЕЛЬНОЙ
    ширины на любой монете (BTC 60000 и DOGE 0.15 меряются одинаково)."""
    return int(math.floor(math.log(p) / LOG_STEP))


def _bkt_mid(b):
    return math.exp((b + 0.5) * LOG_STEP)


def _bkt_lo(b):
    return math.exp(b * LOG_STEP)


def volume_profile(vb, i_hi, n_bars):
    """Профиль объёма по барам (i_hi-n_bars, i_hi] ВКЛЮЧИТЕЛЬНО i_hi.
    Объём (turnover, USD) каждого бара размазан РАВНОМЕРНО по корзинам между
    его low и high — стандартное приближение при отсутствии тиковых данных."""
    prof = {}
    a = max(0, i_hi - n_bars + 1)
    for k in range(a, i_hi + 1):
        row = vb[k]
        l, h, t = row[3], row[2], row[6]
        if t <= 0 or l <= 0:
            continue
        b0, b1 = _bkt(l), _bkt(h)
        m = b1 - b0 + 1
        share = t / m
        for b in range(b0, b1 + 1):
            prof[b] = prof.get(b, 0.0) + share
    return prof


def value_area(prof, frac=VA_FRAC):
    """Зона стоимости: расширяемся от POC к более объёмному соседу, пока не
    наберём frac от общего объёма профиля (классический алгоритм)."""
    if not prof:
        return None, None, None
    tot = sum(prof.values())
    poc = max(prof, key=prof.get)
    lo = hi = poc
    acc = prof[poc]
    target = tot * frac
    b_min, b_max = min(prof), max(prof)
    while acc < target and (lo > b_min or hi < b_max):
        v_dn = prof.get(lo - 1, 0.0) if lo > b_min else -1.0
        v_up = prof.get(hi + 1, 0.0) if hi < b_max else -1.0
        if v_up >= v_dn:
            hi += 1
            acc += max(0.0, v_up)
        else:
            lo -= 1
            acc += max(0.0, v_dn)
    return lo, hi, poc


def hvn_clusters(prof, q=HVN_Q):
    """Кластеры высокого объёма: смежные корзины выше q-го перцентиля.
    Возвращает [(b_lo, b_hi, объём кластера)]."""
    if not prof:
        return []
    vals = sorted(prof.values())
    thr = vals[min(len(vals) - 1, int(q * len(vals)))]
    keys = sorted(b for b, v in prof.items() if v >= thr)
    cl, cur = [], None
    for b in keys:
        if cur is not None and b == cur[1] + 1:
            cur = (cur[0], b, cur[2] + prof[b])
        else:
            if cur is not None:
                cl.append(cur)
            cur = (b, b, prof[b])
    if cur is not None:
        cl.append(cur)
    return cl


def dens_at(vb, vts, ts_end, px, atr_abs, n_bars, iv_ms):
    """Плотность объёма вокруг px по окну, заканчивающемуся на ts_end
    (учитываются только бары, ЗАКРЫТЫЕ не позже ts_end). Отдельная функция —
    для проверки, зависит ли вывод от разрешения объёмных данных."""
    j = bisect.bisect_right(vts, ts_end - iv_ms) - 1
    if j < 20:
        return None
    pf = prof_feats(volume_profile(vb, j, n_bars), px, atr_abs)
    return pf["dens"] if pf else None


def prof_feats(prof, px, atr_abs):
    """Признаки точки px относительно профиля.
    dens — ПЛОТНОСТЬ объёма в полосе +-BAND_ATR*ATR вокруг px, нормированная
    на среднюю плотность профиля: 1.0 = средняя, >1 — объёмный уровень,
    <1 — пустота. Метрика безразмерная, сравнима между монетами и окнами."""
    if not prof or px <= 0 or atr_abs <= 0:
        return None
    tot = sum(prof.values())
    b_min, b_max = min(prof), max(prof)
    n_span = b_max - b_min + 1
    band = BAND_ATR * atr_abs
    lo_b = _bkt(max(px - band, px * 0.5))
    hi_b = _bkt(px + band)
    v_band = sum(prof.get(b, 0.0) for b in range(lo_b, hi_b + 1))
    n_band = hi_b - lo_b + 1
    dens = (v_band / n_band) / (tot / n_span) if tot > 0 else 0.0
    va_lo, va_hi, poc = value_area(prof)
    b_px = _bkt(px)
    return dict(
        dens=dens,
        in_va=bool(va_lo is not None and va_lo <= b_px <= va_hi),
        d_poc_atr=(px - _bkt_mid(poc)) / atr_abs,
        below_prof=bool(b_px < b_min),
        poc_px=_bkt_mid(poc),
    )


def hvn_stop(prof, px, atr_abs, max_atr=3.0, buf_atr=0.15):
    """Стоп «под ближайшим объёмным уровнем»: кластер высокого объёма,
    который СОДЕРЖИТ точку остановки падения либо лежит ближайшим ПОД ней;
    стоп — под нижней границей кластера с буфером. None, если подходящего
    кластера нет в пределах max_atr ATR ниже."""
    cls = hvn_clusters(prof)
    if not cls:
        return None
    b_px = _bkt(px)
    best = None
    for b_lo, b_hi, _v in cls:
        if b_lo <= b_px <= b_hi:                 # точка внутри кластера
            best = b_lo
            break
        if b_hi < b_px:                          # кластер ниже точки
            if best is None or b_lo > best:
                best = b_lo
    if best is None:
        return None
    edge = _bkt_lo(best)
    if (px - edge) > max_atr * atr_abs:
        return None
    return edge - buf_atr * atr_abs


# --------------------------------------------------------- ценовые уровни
def round_step(px):
    """Шаг «круглых» чисел: крупнейшее из {1,2,5}x10^k, не превышающее 2% цены.
    Правило одинаково для BTC (60000 -> 1000) и для DOGE (0.15 -> 0.002)."""
    lim = 0.02 * px
    k = math.floor(math.log10(lim))
    best = 10.0 ** k
    for m in (1.0, 2.0, 5.0):
        v = m * 10.0 ** k
        if v <= lim and v > best:
            best = v
    return best


def swing_low_list(c4, w=6):
    """Подтверждённые свинг-минимумы: (бар подтверждения p+w, цена).
    Пивот на баре p виден только с бара p+w — без заглядывания вперёд."""
    piv_h, piv_l = pt.find_pivots(c4, w)
    return sorted((p + w, c4[p][3]) for p, v in enumerate(piv_l) if v)


# ------------------------------------------------------------------- данные
def load_all():
    data = {}
    for s in SYMS:
        c4 = ev.fetch(s, str(IV), DAYS)
        c15 = ev.fetch(s, "15", DAYS)
        vb = fetch_vol(s, VOL_IV, DAYS)
        ctx = se3.prep_context(c4, interval_min=IV, symbol=s)
        ts15 = [c[0] for c in c15]
        vts = [b[0] for b in vb]
        sw = swing_low_list(c4, w=pt.W)
        fvg_b, _ = smc.find_fvg(c4, bars_per_day=BARS_DAY_4H)
        ob_b, _ = smc.find_order_blocks(c4, bars_per_day=BARS_DAY_4H)
        bias = smc.structure_bias(c4)
        data[s] = dict(c4=c4, c15=c15, ts15=ts15, ctx=ctx, vb=vb, vts=vts,
                       sw=sw, fvg=fvg_b, ob=ob_b, bias=bias,
                       closes=[c[4] for c in c4], lows=[c[3] for c in c4])
    return data


def quarter_of(ms):
    tm = time.gmtime(ms / 1000)
    return f"{tm.tm_year}Q{(tm.tm_mon - 1) // 3 + 1}"


# --------------------------------------------------------------- признаки бара
def bar_feats(d, i, g):
    """Все признаки уровней для бара i (только данные <= i).

    dump_low — минимум падения: минимум low за окно drop_days (то самое место,
    «за которым» ставится стоп в исходном описании сетапа)."""
    c4, ctx = d["c4"], d["ctx"]
    n_day = BARS_DAY_4H
    d_bars = max(1, int(g["drop_days"]) * n_day)
    if i < d_bars + 2:
        return None
    atr_b = ctx["atr_bar"][i]
    if not atr_b:
        return None
    px_c = c4[i][4]
    atr_abs = atr_b * px_c
    dump_low = min(d["lows"][i - d_bars:i + 1])

    # --- профиль объёма: только бары, ЗАКРЫТЫЕ до нужного момента.
    # ДВА якоря окна, и это принципиально:
    #   "СЕЙЧАС" — окно кончается закрытием сигнального бара: так профиль
    #      увидит и объём САМОГО падения (его же и наторговали), поэтому
    #      «упёрлись в объём» частично тавтологично;
    #   "ДО"     — окно кончается ПЕРЕД началом падения: это и есть проверка
    #      гипотезы «падение остановилось на ЗАРАНЕЕ существовавшем уровне».
    # Гипотезу владельца проверяет якорь "ДО", живой бот считал бы "СЕЙЧАС".
    iv_ms = MS_1H if VOL_IV == "60" else 15 * 60 * 1000
    j_now = bisect.bisect_right(d["vts"], c4[i][0] + MS_4H - iv_ms) - 1
    j_pre = bisect.bisect_right(d["vts"], c4[i - d_bars][0] - iv_ms) - 1
    f = dict(dump_low=dump_low, atr_abs=atr_abs, ts=c4[i][0])
    n_win = {30: 30 * 24, 90: 90 * 24} if VOL_IV == "60" else {30: 30 * 96,
                                                               90: 90 * 96}
    for tag, days, j in (("30", 30, j_now), ("90", 90, j_now),
                         ("30pre", 30, j_pre), ("90pre", 90, j_pre)):
        f[f"dens{tag}"] = None
        if j < 20:
            continue
        prof = volume_profile(d["vb"], j, n_win[days])
        pf = prof_feats(prof, dump_low, atr_abs)
        if pf:
            f[f"dens{tag}"] = pf["dens"]
            f[f"inva{tag}"] = pf["in_va"]
            f[f"dpoc{tag}"] = pf["d_poc_atr"]
        if tag == "90":
            # цену стопа «под объёмным кластером» считаем СРАЗУ и профиль не
            # храним: тысячи профилей по 500 корзин съели бы сотни мегабайт
            f["hvn_stop"] = hvn_stop(prof, dump_low, atr_abs)

    # --- свинг-минимумы: только СТАРЫЕ, сформированные ДО начала падения.
    # Иначе минимум падения почти всегда «совпадает» со свингом внутри самого
    # падения — это была бы тавтология, а не отскок от прошлого уровня.
    k = bisect.bisect_right(d["sw"], (i, float("inf")))
    prev = [p for cp, p in d["sw"][:k] if (cp - pt.W) < i - d_bars]
    f["d_swing_atr"] = (min(abs(dump_low - p) for p in prev) / atr_abs
                        if prev else None)
    f["n_swing"] = len(prev)
    # тот же признак, но по свингам ТОЛЬКО за последние 90 суток: за 3 года
    # свингов накапливается столько, что «далеко от всех» становится всё реже,
    # и признак начинает мерить возраст истории, а не уровень
    lim = i - 90 * n_day
    rec = [p for cp, p in d["sw"][:k] if lim <= (cp - pt.W) < i - d_bars]
    f["d_swing90_atr"] = (min(abs(dump_low - p) for p in rec) / atr_abs
                          if rec else None)
    f["n_swing90"] = len(rec)

    # --- круглые числа
    st = round_step(dump_low)
    nearest = round(dump_low / st) * st
    f["round_frac"] = abs(dump_low - nearest) / (st / 2.0)
    f["d_round_atr"] = abs(dump_low - nearest) / atr_abs
    f["round_step_pct"] = 100.0 * st / dump_low

    # --- минимум диапазона ДО падения (граница диапазона)
    a0 = max(0, i - d_bars - int(g["window"]))
    a1 = max(a0 + 1, i - d_bars)
    prev_low = min(d["lows"][a0:a1])
    f["d_prevlow_atr"] = (dump_low - prev_low) / atr_abs
    f["prev_low"] = prev_low

    # --- объёмные кластеры ВНУТРИ зоны падения: где именно набирался объём.
    # pos=0 — объём набран у самого низа (поглощение/капитуляция),
    # pos=1 — у верха (распределение перед падением).
    # vol_spike — объём окна падения к среднему объёму такого же окна за 30 сут.
    f["drop_poc_pos"] = None
    f["vol_spike"] = None
    if j_now >= 20:
        v_lo = bisect.bisect_left(d["vts"], c4[i - d_bars][0])
        if j_now > v_lo + 3:
            prof_d = volume_profile(d["vb"], j_now, j_now - v_lo + 1)
            if prof_d:
                poc_d = _bkt_mid(max(prof_d, key=prof_d.get))
                hi_d = max(c4[k][2] for k in range(i - d_bars, i + 1))
                if hi_d > dump_low:
                    f["drop_poc_pos"] = min(1.0, max(0.0,
                                                     (poc_d - dump_low)
                                                     / (hi_d - dump_low)))
                m = j_now - v_lo + 1
                a30 = max(0, j_now - 30 * 24 + 1)
                base_v = sum(b[6] for b in d["vb"][a30:j_now + 1])
                n30 = j_now - a30 + 1
                cur_v = sum(b[6] for b in d["vb"][v_lo:j_now + 1])
                if base_v > 0 and n30 > m:
                    f["vol_spike"] = (cur_v / m) / (base_v / n30)

    # --- контекст для контроля путаницы факторов
    f["drop_pct"] = (d["closes"][i - d_bars] - c4[i][4]) / d["closes"][i - d_bars]
    f["regime"] = int(ctx["regime"][i])
    f["rsi"] = ctx["rsi"][14][i]

    # --- SMC
    f["ob"] = bool(d["ob"][i])
    f["fvg"] = bool(d["fvg"][i])
    f["bias"] = int(d["bias"][i])

    # --- свинг-минимум для стопа (как stop_mode=1 в движке)
    sb = max(1, int(g["swing_bars"]))
    f["swing_stop_ref"] = min(d["lows"][max(0, i - sb + 1):i + 1])
    return f


# ------------------------------------------------------------ варианты стопа
def stop_price(kind, d, i, f, g):
    """Цена стопа по варианту. Все — только из данных <= i."""
    atr_abs = f["atr_abs"]
    if kind == "dumplow":                       # (а) за минимумом падения
        return f["dump_low"] * 0.999
    if kind == "hvn":                           # (б) под объёмным кластером
        return f.get("hvn_stop")
    if kind == "swing":                         # (в) под свинг-минимумом
        return f["swing_stop_ref"] - 0.3 * atr_abs
    if kind.startswith("atr"):                  # (г) k*ATR от закрытия сигнала
        k = float(kind[3:])
        return d["c4"][i][4] - k * atr_abs
    raise ValueError(kind)


# ------------------------------------------------------------------- сделка
def make_trade(d, i, stop_px, lev=LEV, tp_r=2.0):
    """Одна сделка: вход по открытию первой 15м-свечи ПОСЛЕ закрытия бара i,
    ведение — signal_engine3.simulate_grid_trade (один вход, тейк tp_r*риск,
    таймаут HOLD_DAYS). Возвращает None, если вход невозможен."""
    if stop_px is None or stop_px <= 0:
        return None
    c15, ts15 = d["c15"], d["ts15"]
    j, entry_px = se3._plan_entry(d["c4"][i][0], c15, ts15, MS_4H)
    if j is None:
        return None
    if j + HOLD15 > len(c15) - 1:               # окно удержания не помещается
        return None
    frac = (entry_px - stop_px) / entry_px
    if frac < MIN_STOP or frac > 0.8 * se3.liq_frac(lev):
        return None
    sim = se3.simulate_grid_trade("L", j, entry_px, stop_px, c15, lev, HOLD15,
                                  levels=1, tp_mode=0, tp_r=tp_r)
    return dict(i=i, ts=d["c4"][i][0], entry_ts=ts15[j],
                exit_ts=ts15[sim["exit_i15"]],
                r=sim["r"], pnl=sim["pnl"], reason=sim["reason"],
                stop_frac=frac, mfe_r=sim["mfe_r"], mae_r=sim["mae_r"],
                would_tp=bool(sim["would_hit_tp_later"]),
                q=quarter_of(d["c4"][i][0]))


# ------------------------------------------------------------------ сигналы
def dump_signals(d, g, lo, hi):
    """Бары-сигналы dump_long по ЖЕЛЕЗНОМУ движку (se3.gate_eval) в
    полуинтервале баров [lo, hi)."""
    ext = se3.build_ext("dump_long", g, d["c4"], interval_min=IV)
    outv = []
    for i in range(max(lo, ext["warm"]), hi):
        e = se3.gate_eval("dump_long", i, d["c4"], d["ctx"], g, ext)
        if e["ok"]:
            outv.append(i)
    return outv, ext


def build_rows(data, sig_by_sym, g, kind="dumplow", lev=LEV, feats_cache=None):
    """Сделки + признаки по списку сигналов. feats_cache — общий кэш признаков
    (они не зависят от варианта стопа)."""
    rows = []
    for s, idxs in sig_by_sym.items():
        d = data[s]
        for i in idxs:
            key = (s, i)
            f = feats_cache.get(key) if feats_cache is not None else None
            if f is None:
                f = bar_feats(d, i, g)
                if feats_cache is not None:
                    feats_cache[key] = f
            if f is None:
                continue
            sp = stop_price(kind, d, i, f, g)
            tr = make_trade(d, i, sp, lev=lev)
            if tr is None:
                continue
            tr["sym"] = s
            tr["f"] = f
            rows.append(tr)
    return rows


def random_rows(data, n_by_sym, g, lo, hi, seed, kind="dumplow", lev=LEV,
                feats_cache=None):
    """КОНТРОЛЬ: столько же входов, та же сторона, тот же стоп и тот же выход,
    случаен только МОМЕНТ входа (в тех же барах [lo,hi))."""
    rnd = random.Random(seed)
    pick = {}
    for s, n in n_by_sym.items():
        if n <= 0:
            continue
        d = data[s]
        ext = se3.build_ext("dump_long", g, d["c4"], interval_min=IV)
        a = max(lo, ext["warm"])
        b = min(hi, len(d["c4"]))
        if b - a < n + 5:
            continue
        pick[s] = sorted(rnd.sample(range(a, b), n))
    return build_rows(data, pick, g, kind=kind, lev=lev,
                      feats_cache=feats_cache)


# ------------------------------------------------------------------ разбиение
def split_by(rows, key, thr, ge=True):
    """Делит сделки на «признак выполнен» / «нет». None-признак отбрасывается."""
    a, b, skip = [], [], 0
    for r in rows:
        v = r["f"].get(key)
        if v is None:
            skip += 1
            continue
        ok = (v >= thr) if ge else (v <= thr)
        (a if ok else b).append(r)
    return a, b, skip


def contrast(name, rows_a, rows_b, tag_a, tag_b, boot=True, reg=None):
    """Печатает контраст двух подмножеств и возвращает (t, p_boot).

    p_boot — доля квартальных бутстрэп-выборок, где разница (a минус b) <= 0.
    Читается так: p_boot около 0 — a устойчиво ЛУЧШЕ b; p_boot около 1 — a
    устойчиво ХУЖЕ b; около 0.5 — разницы нет.
    reg — имя для реестра проверок (итоговая таблица множественности)."""
    A, B = agg(rows_a), agg(rows_b)
    out(line(f"{name}: {tag_a}", A))
    out(line(f"{name}: {tag_b}", B))
    t = welch_t([x["r"] for x in rows_a], [x["r"] for x in rows_b])
    pb = block_boot_p(rows_a, rows_b) if boot else None
    extra = f"   бутстрэп по кварталам p={pb:.3f}" if pb is not None else ""
    out(f"  {'-> разница ' + tag_a + ' минус ' + tag_b:34} "
        f"dexp={A['exp_r'] - B['exp_r']:+7.3f}  t={t:+5.2f}  "
        f"p={p_two_sided(t):.4f}{extra}")
    if reg:
        _TESTS.append((reg, t, A["n"], B["n"], A["exp_r"] - B["exp_r"]))
    return t, pb


def regime_mix(rows):
    """Состав режимов рынка в подмножестве (0 бык / 1 боковик / 2 медведь)."""
    if not rows:
        return "нет сделок"
    c = {0: 0, 1: 0, 2: 0}
    for r in rows:
        c[r["f"]["regime"]] = c.get(r["f"]["regime"], 0) + 1
    n = len(rows)
    return (f"бык {100*c[0]/n:4.0f}%  боковик {100*c[1]/n:4.0f}%  "
            f"медведь {100*c[2]/n:4.0f}%")


# =============================================================== самопроверки
def selfcheck_prefix(d, out_fn):
    """Заглядывание вперёд: значение признака на баре i, посчитанное по
    ПРЕФИКСУ c4[:i+1], обязано совпасть со значением из полного ряда."""
    c4 = d["c4"]
    n = len(c4)
    tests = [
        ("smc.find_fvg", lambda cs: smc.find_fvg(cs, bars_per_day=BARS_DAY_4H)[0]),
        ("smc.find_order_blocks",
         lambda cs: smc.find_order_blocks(cs, bars_per_day=BARS_DAY_4H)[0]),
        ("smc.structure_bias", lambda cs: smc.structure_bias(cs)),
    ]
    ok_all = True
    for nm, fn in tests:
        full = fn(c4)
        bad = 0
        for i in (900, 1500, 2400, 3300, 4200, 5100, 6000, 6800):
            if i >= n:
                continue
            pref = fn(c4[:i + 1])
            if pref[i] != full[i]:
                bad += 1
        ok_all &= bad == 0
        out_fn(f"  {nm:26} префикс == полный ряд на 8 барах: "
               f"{'ДА' if bad == 0 else f'НЕТ ({bad} расхождений)'}")

    # СКВОЗНАЯ проверка: весь bar_feats на УРЕЗАННЫХ данных (и свечи, и объём
    # обрезаны по сигнальному бару) обязан дать те же числа, что на полных.
    g = se3.default_genome(hold_days=HOLD_DAYS, stop_cap=0.150)
    bad, checked = 0, 0
    for i in (1200, 2400, 3600, 4800, 6000):
        if i >= n:
            continue
        full = bar_feats(d, i, g)
        cut_ts = c4[i][0] + MS_4H
        kv = bisect.bisect_right(d["vts"], cut_ts - 1)
        d_cut = dict(d)
        d_cut["c4"] = c4[:i + 1]
        d_cut["vb"] = d["vb"][:kv]
        d_cut["vts"] = d["vts"][:kv]
        d_cut["closes"] = d["closes"][:i + 1]
        d_cut["lows"] = d["lows"][:i + 1]
        d_cut["sw"] = [x for x in d["sw"] if x[0] <= i]
        d_cut["ctx"] = se3.prep_context(c4[:i + 1], interval_min=IV,
                                        symbol=d["ctx"]["symbol"])
        cut = bar_feats(d_cut, i, g)
        for key in ("dens30", "dens90", "dens30pre", "dens90pre",
                    "d_swing_atr", "round_frac", "d_prevlow_atr",
                    "drop_poc_pos", "vol_spike", "hvn_stop", "dump_low"):
            va, vb_ = full.get(key), cut.get(key)
            checked += 1
            if va is None and vb_ is None:
                continue
            if va is None or vb_ is None or abs(va - vb_) > 1e-9 * max(
                    1.0, abs(va)):
                bad += 1
    ok_all &= bad == 0
    out_fn(f"  {'ВСЕ признаки уровней':26} на урезанных данных совпали: "
           f"{'ДА' if bad == 0 else f'НЕТ ({bad} из {checked})'} "
           f"({checked} сравнений)")

    # свинг-минимумы
    bad = 0
    for i in (900, 2400, 4200, 6000):
        if i >= n:
            continue
        full = [p for cp, p in swing_low_list(c4, pt.W) if cp <= i]
        pref = [p for cp, p in swing_low_list(c4[:i + 1], pt.W) if cp <= i]
        if full != pref:
            bad += 1
    ok_all &= bad == 0
    out_fn(f"  {'свинг-минимумы (пивот w=6)':26} префикс == полный ряд: "
           f"{'ДА' if bad == 0 else f'НЕТ ({bad})'}")

    # профиль объёма: не использует ни одного бара, закрывшегося ПОСЛЕ сигнала
    g = se3.default_genome()
    worst = 0
    for i in (1200, 3000, 5000, 6500):
        if i >= n:
            continue
        close_ts = c4[i][0] + MS_4H
        j = bisect.bisect_right(d["vts"], close_ts - MS_1H) - 1
        worst = max(worst, d["vts"][j] + MS_1H - close_ts)
    ok_all &= worst <= 0
    out_fn(f"  {'профиль объёма':26} последний учтённый бар закрыт не позже "
           f"сигнала: {'ДА' if worst <= 0 else 'НЕТ'} (запас {-worst/60000:.0f} мин)")
    return ok_all


def selfcheck_engine(d, out_fn):
    """Наша сделка обязана совпасть со сделкой самого движка signal_engine3
    при том же геноме (stop_mode=2, k=2, tp_r=2, одиночный вход)."""
    g = se3.default_genome(hold_days=HOLD_DAYS, stop_cap=0.150)
    r = se3.run_setup("dump_long", g, d["c4"], d["ctx"], d["c15"], d["ts15"],
                      LEV, symbol=d["ctx"]["symbol"], collect_diag=False,
                      interval_min=IV)
    eng = {t["signal_ts"]: round(t["r"], 3) for t in r["trades"]}
    idxs, _ext = dump_signals(d, g, 0, len(d["c4"]))
    fc = {}
    mine = {}
    for i in idxs:
        f = bar_feats(d, i, g)
        if f is None:
            continue
        tr = make_trade(d, i, stop_price("atr2.0", d, i, f, g))
        if tr is not None:
            mine[tr["ts"]] = round(tr["r"], 3)
    common = set(eng) & set(mine)
    same = sum(1 for k in common if abs(eng[k] - mine[k]) < 1e-6)
    out_fn(f"  сделок у движка {len(eng)}, у нас {len(mine)}, общих {len(common)}, "
           f"совпало R бит-в-бит {same}")
    out_fn("  (движок держит ОДНУ позицию и кулдаун, мы считаем каждый сигнал "
           "независимо — поэтому у нас сделок больше; важно совпадение общих)")
    return len(common) > 0 and same == len(common)


# =================================================================== основное
def main():
    t_start = time.time()
    # stop_cap=0.150 (верхняя граница гена), а НЕ дефолтные 0.06 — принципиально.
    # Ворото ширины стопа в движке считает стоп по stop_mode=2 (2*ATR), и при
    # cap=0.06 из отбора вылетала бы половина капитуляций (263 -> 134 события) —
    # причём самые волатильные, то есть самые сильные сливы. Раз мы СРАВНИВАЕМ
    # варианты стопа, набор событий не имеет права зависеть от одного из них.
    g_base = se3.default_genome(hold_days=HOLD_DAYS, stop_cap=0.150)
    g_soft = se3.default_genome(hold_days=HOLD_DAYS, stop_cap=0.150,
                                drop_frac=0.04, rsi_os=35)

    out("=" * 100)
    out("ГОРИЗОНТАЛЬНЫЕ УРОВНИ, ОБЪЁМНЫЙ ПРОФИЛЬ, СТОПЫ И SMART MONEY "
        "НА СЕТАПЕ dump_long")
    out("=" * 100)
    out("Сетап: капитуляция-лонг. Падение >= X% за 2 суток + RSI14 < Y + "
        "зелёная свеча -> лонг.")
    out(f"ТФ сигнала 4ч, ведение по 15м, плечо x{LEV}, маржа ${MARGIN:.0f}, "
        f"тейк 2R, таймаут {HOLD_DAYS} суток.")
    out(f"Издержки: тейкер {se3.TAKER*100:.3f}%, мейкер {se3.MAKER*100:.3f}%, "
        f"слип {se3.SLIP*100:.2f}%, фандинг {se3.FUND_8H*100:.2f}%/8ч. "
        f"Стоп и тейк в одной свече -> СТОП.")
    out()
    out("ЧТО УЖЕ ИЗВЕСТНО ПРО ЭТОТ СЕТАП (не переоткрываем):")
    out("  - в отборе v12 dump_long@240 давал +0.544R на 12 сделках экзамена,")
    out("    но воспроизводился 1 раз из 3 при смене зерна отбора;")
    out("  - в тесте идей с фиксированными параметрами капитуляция-лонг была")
    out("    ХУЖЕ случайного входа (вклад -0.215R). Сырого преимущества нет.")
    out("  Поэтому здесь проверяется не «работает ли dump_long», а «добавляет")
    out("  ли привязка к уровням что-нибудь СВЕРХ него и сверх случайного входа».")
    out()
    zb = z_for_p(0.05 / N_TESTS)
    out(f"ЗАРАНЕЕ ОБЪЯВЛЕНО (до просмотра результатов):")
    out(f"  всего проверок {N_TESTS}: объём — плотность 2 набора x 2 окна x 2 "
        f"якоря = 8, зона стоимости 2, POC 2, кластер внутри падения 4;")
    out(f"  цена: свинг 2, круглое 2, минимум диапазона 2, пробой минимума 2;")
    out(f"  стопы: 5 вариантов против базового x 2 набора = 10; SMC 4 x 2 = 8;")
    out(f"  плюс 1 итоговый кандидат на holdout). Поправка Бонферрони -> "
        f"строгий порог |t| >= {zb:.2f} (alpha 0.05/{N_TESTS});")
    out(f"  номинальный порог |t| >= 2.00 — при {N_TESTS} проверках примерно "
        f"2 «значимых» результата ожидаются просто по случайности.")
    out(f"  «на объёме» = плотность dens >= {DENS_THR:.1f}; «у уровня» = ближе "
        f"{NEAR_ATR} ATR; «на круглом» = ближе {ROUND_FRAC:.0%} полушага.")
    out(f"  обучающая часть — первые {HOLD_FRAC:.0%} баров, остальное HOLDOUT "
        f"(в выборе НИЧЕГО не участвует).")
    out("  потолок ширины стопа в воротах поднят до 15% (верх гена), иначе")
    out("  дефолтные 6% выкинули бы половину капитуляций — самые волатильные;")
    out("  набор событий не должен зависеть от одного из сравниваемых стопов.")
    out("  два набора событий: БАЗА (падение>=6%, RSI<30) — рабочая")
    out("  конфигурация, и МЯГКИЙ (падение>=4%, RSI<35) — тот же сетап, но")
    out("  событий втрое больше; мягкий взят РАДИ МОЩНОСТИ, оба отчитываются.")
    out()

    # ---------------------------------------------------------------- данные
    out("-" * 100)
    out("ДАННЫЕ")
    out("-" * 100)
    data = load_all()
    n4 = len(data[SYMS[0]]["c4"])
    hold_i = int(n4 * HOLD_FRAC)
    for s in SYMS:
        d = data[s]
        out(f"  {s:9} 4ч {len(d['c4']):5} баров, 15м {len(d['c15']):7}, "
            f"объём {VOL_IV}м {len(d['vb']):6} баров  "
            f"{time.strftime('%Y-%m-%d', time.gmtime(d['c4'][0][0]/1000))}"
            f" .. {time.strftime('%Y-%m-%d', time.gmtime(d['c4'][-1][0]/1000))}")
    out(f"  граница обучение/HOLDOUT: бар {hold_i} из {n4} "
        f"({time.strftime('%Y-%m-%d', time.gmtime(data[SYMS[0]]['c4'][hold_i][0]/1000))})")
    out()

    # ---------------------------------------------------------- самопроверки
    out("-" * 100)
    out("САМОПРОВЕРКИ (без них цифрам верить нельзя)")
    out("-" * 100)
    ok_pref = selfcheck_prefix(data["BTCUSDT"], out)
    ok_eng = selfcheck_engine(data["BTCUSDT"], out)
    out(f"  ИТОГ самопроверок: префикс {'ОК' if ok_pref else 'ПРОВАЛ'}, "
        f"совпадение с движком {'ОК' if ok_eng else 'ПРОВАЛ'}")
    out()

    # ------------------------------------------------------------- сигналы
    out("-" * 100)
    out("СОБЫТИЯ (сигналы) НА ОБУЧАЮЩЕЙ ЧАСТИ")
    out("-" * 100)
    sets = {}
    for tag, g in (("БАЗА", g_base), ("МЯГКИЙ", g_soft)):
        sig_tr, sig_ho = {}, {}
        for s in SYMS:
            idxs, _ = dump_signals(data[s], g, 0, n4)
            sig_tr[s] = [i for i in idxs if i < hold_i]
            sig_ho[s] = [i for i in idxs if i >= hold_i]
        sets[tag] = dict(g=g, tr=sig_tr, ho=sig_ho)
        out(f"  {tag:7} обучение: " +
            "  ".join(f"{s[:3]}={len(sig_tr[s])}" for s in SYMS) +
            f"  всего {sum(len(v) for v in sig_tr.values())};  "
            f"HOLDOUT всего {sum(len(v) for v in sig_ho.values())}")
    out()

    fc = {}          # общий кэш признаков (сигналы)
    fcr = {}         # общий кэш признаков (случайные входы)
    trades = {}
    for tag in sets:
        trades[tag] = build_rows(data, sets[tag]["tr"], sets[tag]["g"],
                                 kind="dumplow", feats_cache=fc)

    # случайный контроль (тот же стоп «за минимумом падения»)
    rand = {}
    for tag in sets:
        n_by = {s: len(v) for s, v in sets[tag]["tr"].items()}
        rows = []
        for sd in RAND_SEEDS:
            rows += random_rows(data, n_by, sets[tag]["g"], 0, hold_i, sd,
                                kind="dumplow", feats_cache=fcr)
        rand[tag] = rows

    out("-" * 100)
    out("БАЗОВАЯ ЛИНИЯ: dump_long БЕЗ привязки к уровням (стоп за минимумом "
        "падения, тейк 2R)")
    out("-" * 100)
    out("  Каждый сигнал считается ОТДЕЛЬНОЙ сделкой (без блокировки занятости) —")
    out("  так подмножества сравнимы между собой; портфельный прогон с одной")
    out("  позицией сделан отдельно в конце.")
    out(f"  ВНИМАНИЕ: у случайного входа n и ret просуммированы по "
        f"{len(RAND_SEEDS)} зёрнам —")
    out("  сравнивать с сигналом надо exp_r (на сделку), а ret делить на число")
    out("  зёрен. exp_r от числа зёрен не зависит.")
    for tag in ("БАЗА", "МЯГКИЙ"):
        A = agg(trades[tag])
        R = agg(rand[tag])
        out(line(f"{tag} сигнал", A))
        out(line(f"{tag} СЛУЧАЙНЫЙ вход ({len(RAND_SEEDS)} зёрен)", R))
        t = welch_t([x["r"] for x in trades[tag]], [x["r"] for x in rand[tag]])
        out(f"  {'-> вклад сигнала над случайным':34} "
            f"dexp={A['exp_r'] - R['exp_r']:+7.3f}  t={t:+5.2f}  "
            f"p={p_two_sided(t):.4f}")
    # купил и держал
    out()
    out("  «Купил и держал» на обучающем окне (спот, без плеча):")
    for s in SYMS:
        c4 = data[s]["c4"]
        bh = (c4[hold_i - 1][4] / c4[200][4] - 1) * 100
        out(f"    {s:9} {bh:+8.1f}%")
    out()

    # =================================================== 1. ОБЪЁМНЫЕ УРОВНИ
    out("=" * 100)
    out("1. ГОРИЗОНТАЛЬНЫЕ ОБЪЁМНЫЕ УРОВНИ")
    out("=" * 100)
    out("Профиль: корзины 0.25% в лог-шкале, объём (turnover, USD) размазан по")
    out("диапазону каждого часового бара. dens = плотность объёма в полосе")
    out("+-0.5 ATR вокруг МИНИМУМА ПАДЕНИЯ, делённая на среднюю плотность")
    out("профиля. dens>=1 — падение встало на объёме, dens<1 — в пустоте.")
    out("Окно профиля берётся в двух вариантах:")
    out("  'ДО'     — заканчивается ПЕРЕД началом падения. Это и есть гипотеза")
    out("             владельца: остановились на ЗАРАНЕЕ существовавшем уровне;")
    out("  'СЕЙЧАС' — заканчивается закрытием сигнального бара, то есть видит")
    out("             и объём самого обвала (так считал бы живой бот).")
    out()
    for tag in ("БАЗА", "МЯГКИЙ"):
        rows = trades[tag]
        for key, nm in (("dens90pre", "ДО"), ("dens90", "СЕЙЧАС")):
            vals = [r["f"][key] for r in rows if r["f"].get(key) is not None]
            if vals:
                vs = sorted(vals)
                out(f"  {tag:7} dens(90д, {nm:6}) по {len(vs):4} сделкам: "
                    f"мин {vs[0]:5.2f}  Q1 {vs[len(vs)//4]:5.2f}  медиана "
                    f"{vs[len(vs)//2]:5.2f}  Q3 {vs[3*len(vs)//4]:5.2f}  "
                    f"макс {vs[-1]:6.2f}   доля dens>=1: "
                    f"{100.0*sum(1 for x in vs if x >= DENS_THR)/len(vs):4.1f}%")
    out()
    for tag in ("БАЗА", "МЯГКИЙ"):
        for win, nm in (("30pre", "30 сут, ДО"), ("90pre", "90 сут, ДО"),
                        ("30", "30 сут, СЕЙЧАС"), ("90", "90 сут, СЕЙЧАС")):
            out(f"--- {tag}, окно профиля {nm}")
            a, b, skip = split_by(trades[tag], f"dens{win}", DENS_THR)
            contrast("сигнал", a, b, "на объёме", "в пустоте",
                     reg=f"1.объём dens {tag} {nm}")
            # тот же контраст на СЛУЧАЙНЫХ входах: есть ли эффект уровня
            # вообще, или он специфичен для капитуляции
            ra, rb, _ = split_by(rand[tag], f"dens{win}", DENS_THR)
            contrast("СЛУЧАЙНЫЙ вход", ra, rb, "на объёме", "в пустоте",
                     boot=False)
            out(f"  {'-> эффект уровня СВЕРХ случайного':34} "
                f"разница разниц = "
                f"{(agg(a)['exp_r']-agg(b)['exp_r'])-(agg(ra)['exp_r']-agg(rb)['exp_r']):+7.3f}")
            if skip:
                out(f"  (пропущено без профиля: {skip})")
            out()
    out("--- монотонность: терцили плотности (МЯГКИЙ набор, окно 90 сут 'ДО')")
    rows = [r for r in trades["МЯГКИЙ"] if r["f"].get("dens90pre") is not None]
    rows.sort(key=lambda r: r["f"]["dens90pre"])
    k = len(rows) // 3
    for nm, part in (("нижняя треть (пустота)", rows[:k]),
                     ("средняя треть", rows[k:2 * k]),
                     ("верхняя треть (объём)", rows[2 * k:])):
        if part:
            out(line(nm, agg(part),
                     f"  dens {part[0]['f']['dens90pre']:.2f}.."
                     f"{part[-1]['f']['dens90pre']:.2f}"))
    out()

    out("--- РАЗРЕШЕНИЕ ДАННЫХ: тот же контраст на 15-минутных объёмах")
    out("  (профиль из 1ч-баров огрубляет: 1ч-бар размазывается на 3-5 корзин.")
    out("  Если вывод держится и на 15м-объёмах, он не артефакт разрешения.)")
    vb15 = {s: fetch_vol(s, "15", DAYS) for s in SYMS}
    vts15 = {s: [b[0] for b in vb15[s]] for s in SYMS}
    for tag in ("БАЗА", "МЯГКИЙ"):
        a15, b15, skip15 = [], [], 0
        p1, p2, flip = [], [], 0
        for r in trades[tag]:
            s, i = r["sym"], r["i"]
            d = data[s]
            d_bars = max(1, int(sets[tag]["g"]["drop_days"]) * BARS_DAY_4H)
            v = dens_at(vb15[s], vts15[s], d["c4"][i - d_bars][0],
                        r["f"]["dump_low"], r["f"]["atr_abs"],
                        90 * 96, 15 * 60 * 1000)
            if v is None:
                skip15 += 1
                continue
            v1 = r["f"].get("dens90pre")
            if v1 is not None:
                p1.append(v1)
                p2.append(v)
                flip += 1 if (v1 >= DENS_THR) != (v >= DENS_THR) else 0
            if v >= DENS_THR:
                a15.append(r)
            else:
                b15.append(r)
        contrast(f"{tag} (объём 15м)", a15, b15, "на объёме", "в пустоте",
                 boot=False)
        if len(p1) > 3:
            out(f"  корреляция плотности 1ч и 15м: "
                f"{statistics.correlation(p1, p2):+.4f}; "
                f"порог 1.0 пересекают по-разному {flip} сделок из {len(p1)} "
                f"-> разрешение данных вывод НЕ меняет")
    out()
    out("  доля капитуляций, чей минимум оказался ВООБЩЕ ВНЕ 90-дневного")
    out("  профиля (ниже всей наторгованной зоны, dens=0):")
    for tag in ("БАЗА", "МЯГКИЙ"):
        vv = [r["f"]["dens90pre"] for r in trades[tag]
              if r["f"].get("dens90pre") is not None]
        z = [r for r in trades[tag] if r["f"].get("dens90pre") == 0.0]
        nz = [r for r in trades[tag] if r["f"].get("dens90pre") not in (None, 0.0)]
        out(f"    {tag:7} {100.0*len(vv and z)/max(1,len(vv)):5.1f}% "
            f"({len(z)} из {len(vv)});  их exp_r={agg(z)['exp_r']:+.3f}, "
            f"у остальных {agg(nz)['exp_r']:+.3f}")
    out()

    out("--- зона стоимости 70% (value area) и POC, окно 90 суток, якорь 'ДО'")
    for tag in ("БАЗА", "МЯГКИЙ"):
        a, b, _ = split_by(trades[tag], "inva90pre", 0.5)
        contrast(f"{tag}", a, b, "минимум В зоне стоимости", "вне зоны",
                 reg=f"1.зона стоимости {tag}")
        rows = [r for r in trades[tag] if r["f"].get("dpoc90pre") is not None]
        near = [r for r in rows if abs(r["f"]["dpoc90pre"]) <= 1.0]
        far = [r for r in rows if abs(r["f"]["dpoc90pre"]) > 1.0]
        contrast(f"{tag}", near, far, "минимум у POC (<=1 ATR)",
                 "далеко от POC", reg=f"1.близость к POC {tag}")
        out()

    out("--- ОБЪЁМНЫЕ КЛАСТЕРЫ В САМОЙ ЗОНЕ ПАДЕНИЯ: где набирался объём")
    out("  drop_poc_pos = положение POC окна падения внутри диапазона падения:")
    out("  0 — объём набран у самого низа (поглощение, «капитуляцию выкупают»),")
    out("  1 — у верха (распределение перед сливом). vol_spike = объём окна")
    out("  падения к среднему объёму такого же окна за 30 суток.")
    for tag in ("БАЗА", "МЯГКИЙ"):
        rows = [r for r in trades[tag] if r["f"].get("drop_poc_pos") is not None]
        if not rows:
            continue
        rows.sort(key=lambda r: r["f"]["drop_poc_pos"])
        k = len(rows) // 3
        out(f"  {tag}: терцили drop_poc_pos ({len(rows)} сделок)")
        for nm, part in (("объём у НИЗА падения", rows[:k]),
                         ("объём в середине", rows[k:2 * k]),
                         ("объём у ВЕРХА падения", rows[2 * k:])):
            if part:
                out(line("   " + nm, agg(part),
                         f"  pos {part[0]['f']['drop_poc_pos']:.2f}.."
                         f"{part[-1]['f']['drop_poc_pos']:.2f}"))
        contrast("   низ vs верх", rows[:k], rows[2 * k:],
                 "объём у низа", "объём у верха", boot=False,
                 reg=f"1.объём у низа падения {tag}")
        sp = [r for r in trades[tag] if r["f"].get("vol_spike") is not None]
        sp.sort(key=lambda r: r["f"]["vol_spike"])
        h = len(sp) // 2
        contrast("   всплеск объёма", sp[h:], sp[:h],
                 "объём выше медианы", "ниже медианы", boot=False,
                 reg=f"1.всплеск объёма {tag}")
    out()

    # =================================================== 2. ЦЕНОВЫЕ УРОВНИ
    out("=" * 100)
    out("2. ГОРИЗОНТАЛЬНЫЕ ЦЕНОВЫЕ УРОВНИ (без объёма)")
    out("=" * 100)
    for tag in ("БАЗА", "МЯГКИЙ"):
        out(f"--- {tag}")
        a, b, _ = split_by(trades[tag], "d_swing_atr", NEAR_ATR, ge=False)
        contrast("свинг-минимум", a, b, f"минимум у свинга (<={NEAR_ATR} ATR)",
                 "не у свинга", reg=f"2.свинг-минимум {tag}")
        ra, rb, _ = split_by(rand[tag], "d_swing_atr", NEAR_ATR, ge=False)
        out(f"  {'   контроль (случайный вход)':34} "
            f"dexp={agg(ra)['exp_r']-agg(rb)['exp_r']:+7.3f} "
            f"(n {len(ra)}/{len(rb)})")
        a, b, _ = split_by(trades[tag], "round_frac", ROUND_FRAC, ge=False)
        contrast("круглое число", a, b, "минимум на круглом", "не на круглом",
                 reg=f"2.круглое число {tag}")
        ra, rb, _ = split_by(rand[tag], "round_frac", ROUND_FRAC, ge=False)
        out(f"  {'   контроль (случайный вход)':34} "
            f"dexp={agg(ra)['exp_r']-agg(rb)['exp_r']:+7.3f} "
            f"(n {len(ra)}/{len(rb)})")
        rows = [r for r in trades[tag] if r["f"].get("d_prevlow_atr") is not None]
        a = [r for r in rows if abs(r["f"]["d_prevlow_atr"]) <= NEAR_ATR]
        b = [r for r in rows if abs(r["f"]["d_prevlow_atr"]) > NEAR_ATR]
        contrast("минимум диапазона", a, b, "остановились у прошлого мин.",
                 "не у прошлого мин.", reg=f"2.минимум диапазона {tag}")
        # дополнительно: пробили прошлый минимум или нет
        br = [r for r in rows if r["f"]["d_prevlow_atr"] < 0]
        nb = [r for r in rows if r["f"]["d_prevlow_atr"] >= 0]
        contrast("пробой прошлого минимума", br, nb, "ПРОБИЛИ прошлый мин.",
                 "удержали", boot=False, reg=f"2.пробой прошлого мин. {tag}")
        out()

    # ------------------- контроль путаницы факторов (post-hoc, честно помечен)
    out("=" * 100)
    out("2b. КОНТРОЛЬ ПУТАНИЦЫ ФАКТОРОВ (post-hoc, добавлен ПОСЛЕ просмотра "
        "разделов 1-2)")
    out("=" * 100)
    out("Все три меры «уровня» (плотность объёма, близость к свингу, пробой")
    out("прошлого минимума) указывают в одну сторону. Прежде чем называть это")
    out("эффектом уровня, надо исключить, что это просто ГЛУБИНА ПАДЕНИЯ или")
    out("РЕЖИМ РЫНКА: глубокий слив в бычьем рынке и уходит в пустоту, и")
    out("отскакивает лучше — уровень тут ни при чём.")
    out()
    rows = [r for r in trades["МЯГКИЙ"] if r["f"].get("d_swing_atr") is not None]
    a = [r for r in rows if r["f"]["d_swing_atr"] <= NEAR_ATR]
    b = [r for r in rows if r["f"]["d_swing_atr"] > NEAR_ATR]
    out(f"  состав режимов: у свинга      -> {regime_mix(a)}")
    out(f"  состав режимов: НЕ у свинга   -> {regime_mix(b)}")
    out(f"  средняя глубина падения: у свинга {100*statistics.mean([r['f']['drop_pct'] for r in a]):.2f}%"
        f"  не у свинга {100*statistics.mean([r['f']['drop_pct'] for r in b]):.2f}%")
    dn = [r for r in rows if r["f"].get("dens90pre") is not None]
    lo = [r for r in dn if r["f"]["dens90pre"] < DENS_THR]
    hi = [r for r in dn if r["f"]["dens90pre"] >= DENS_THR]
    out(f"  состав режимов: в пустоте     -> {regime_mix(lo)}")
    out(f"  состав режимов: на объёме     -> {regime_mix(hi)}")
    out(f"  средняя глубина падения: в пустоте {100*statistics.mean([r['f']['drop_pct'] for r in lo]):.2f}%"
        f"  на объёме {100*statistics.mean([r['f']['drop_pct'] for r in hi]):.2f}%")
    out()
    out("  ЭФФЕКТ УРОВНЯ ВНУТРИ ТЕРЦИЛЕЙ ГЛУБИНЫ ПАДЕНИЯ (МЯГКИЙ набор):")
    srt = sorted(rows, key=lambda r: r["f"]["drop_pct"])
    k = len(srt) // 3
    for nm, part in (("мелкие сливы", srt[:k]), ("средние", srt[k:2 * k]),
                     ("глубокие", srt[2 * k:])):
        pa = [r for r in part if r["f"]["d_swing_atr"] <= NEAR_ATR]
        pb = [r for r in part if r["f"]["d_swing_atr"] > NEAR_ATR]
        A, B = agg(pa), agg(pb)
        t = welch_t([x["r"] for x in pa], [x["r"] for x in pb])
        out(f"    {nm:14} падение {100*part[0]['f']['drop_pct']:4.1f}.."
            f"{100*part[-1]['f']['drop_pct']:5.1f}%   "
            f"у свинга exp_r={A['exp_r']:+6.3f} (n={A['n']:3})  "
            f"не у свинга exp_r={B['exp_r']:+6.3f} (n={B['n']:3})  "
            f"dexp={B['exp_r']-A['exp_r']:+6.3f}  t={-t:+5.2f}")
    out()
    out("  ЭФФЕКТ УРОВНЯ ВНУТРИ РЕЖИМОВ РЫНКА (МЯГКИЙ набор):")
    for rg, nm in ((0, "бык"), (1, "боковик"), (2, "медведь")):
        part = [r for r in rows if r["f"]["regime"] == rg]
        pa = [r for r in part if r["f"]["d_swing_atr"] <= NEAR_ATR]
        pb = [r for r in part if r["f"]["d_swing_atr"] > NEAR_ATR]
        A, B = agg(pa), agg(pb)
        t = welch_t([x["r"] for x in pa], [x["r"] for x in pb])
        out(f"    {nm:14} у свинга exp_r={A['exp_r']:+6.3f} (n={A['n']:3})  "
            f"не у свинга exp_r={B['exp_r']:+6.3f} (n={B['n']:3})  "
            f"dexp={B['exp_r']-A['exp_r']:+6.3f}  t={-t:+5.2f}")
    out()
    out("  ВСПЛЕСК ОБЪЁМА ВНУТРИ ТЕРЦИЛЕЙ ГЛУБИНЫ ПАДЕНИЯ (МЯГКИЙ набор):")
    out("  (объём во время слива коррелирует с его глубиной +0.23 — надо")
    out("  убедиться, что это не переодетая глубина)")
    vs = [r for r in trades["МЯГКИЙ"] if r["f"].get("vol_spike") is not None]
    vmed = sorted(r["f"]["vol_spike"] for r in vs)[len(vs) // 2]
    srt2 = sorted(vs, key=lambda r: r["f"]["drop_pct"])
    k2 = len(srt2) // 3
    for nm, part in (("мелкие сливы", srt2[:k2]), ("средние", srt2[k2:2 * k2]),
                     ("глубокие", srt2[2 * k2:])):
        pa = [r for r in part if r["f"]["vol_spike"] >= vmed]
        pb = [r for r in part if r["f"]["vol_spike"] < vmed]
        A, B = agg(pa), agg(pb)
        t = welch_t([x["r"] for x in pa], [x["r"] for x in pb])
        out(f"    {nm:14} объём выше медианы exp_r={A['exp_r']:+6.3f} "
            f"(n={A['n']:3})  ниже exp_r={B['exp_r']:+6.3f} (n={B['n']:3})  "
            f"dexp={A['exp_r']-B['exp_r']:+6.3f}  t={t:+5.2f}")
    out("  ВСПЛЕСК ОБЪЁМА ВНУТРИ РЕЖИМОВ РЫНКА:")
    for rg, nm in ((0, "бык"), (1, "боковик"), (2, "медведь")):
        part = [r for r in vs if r["f"]["regime"] == rg]
        pa = [r for r in part if r["f"]["vol_spike"] >= vmed]
        pb = [r for r in part if r["f"]["vol_spike"] < vmed]
        A, B = agg(pa), agg(pb)
        t = welch_t([x["r"] for x in pa], [x["r"] for x in pb])
        out(f"    {nm:14} выше медианы exp_r={A['exp_r']:+6.3f} (n={A['n']:3})"
            f"  ниже exp_r={B['exp_r']:+6.3f} (n={B['n']:3})  "
            f"dexp={A['exp_r']-B['exp_r']:+6.3f}  t={t:+5.2f}")
    out("  ВСПЛЕСК ОБЪЁМА ПО МОНЕТАМ и ПО ГОДАМ:")
    same = 0
    for sym in SYMS:
        part = [r for r in vs if r["sym"] == sym]
        pa = [r for r in part if r["f"]["vol_spike"] >= vmed]
        pb = [r for r in part if r["f"]["vol_spike"] < vmed]
        dd = agg(pa)["exp_r"] - agg(pb)["exp_r"]
        same += 1 if dd > 0 else 0
        out(f"    {sym:9} dexp={dd:+6.3f}  (n={len(pa)}/{len(pb)})")
    out(f"    в ту же сторону: {same} из {len(SYMS)} монет")
    for y in sorted({time.gmtime(r["ts"] / 1000).tm_year for r in vs}):
        part = [r for r in vs if time.gmtime(r["ts"] / 1000).tm_year == y]
        pa = [r for r in part if r["f"]["vol_spike"] >= vmed]
        pb = [r for r in part if r["f"]["vol_spike"] < vmed]
        out(f"    {y}      dexp={agg(pa)['exp_r']-agg(pb)['exp_r']:+6.3f}  "
            f"(n={len(pa)}/{len(pb)})")
    out("  А ТО ЖЕ НА СЛУЧАЙНЫХ ВХОДАХ (есть ли эффект вне капитуляции):")
    rv = [r for r in rand["МЯГКИЙ"] if r["f"].get("vol_spike") is not None]
    ra = [r for r in rv if r["f"]["vol_spike"] >= vmed]
    rb = [r for r in rv if r["f"]["vol_spike"] < vmed]
    out(f"    случайный вход dexp="
        f"{agg(ra)['exp_r']-agg(rb)['exp_r']:+6.3f}  (n={len(ra)}/{len(rb)})  "
        f"t={welch_t([x['r'] for x in ra], [x['r'] for x in rb]):+5.2f}")
    out()
    out("  ЧИСТО РАЗМЕР ПАДЕНИЯ (без всяких уровней), МЯГКИЙ набор:")
    for nm, part in (("мелкие сливы", srt[:k]), ("средние", srt[k:2 * k]),
                     ("глубокие", srt[2 * k:])):
        out(line("   " + nm, agg(part),
                 f"  падение {100*part[0]['f']['drop_pct']:4.1f}.."
                 f"{100*part[-1]['f']['drop_pct']:5.1f}%"))
    out()
    out("  ПРИЗНАК «НЕ У СВИНГА» НЕ ДОЛЖЕН МЕРИТЬ ВОЗРАСТ ИСТОРИИ.")
    out("  За 3 года свингов накапливается всё больше, и «далеко от всех»")
    out("  автоматически становится реже. Поэтому тот же контраст пересчитан")
    out("  по свингам ТОЛЬКО за последние 90 суток:")
    out(f"    среднее число учтённых свингов: вся история "
        f"{statistics.mean([r['f']['n_swing'] for r in rows]):.0f}, "
        f"за 90 суток "
        f"{statistics.mean([r['f']['n_swing90'] for r in rows if r['f'].get('n_swing90') is not None]):.1f}")
    a9 = [r for r in rows if r["f"].get("d_swing90_atr") is not None
          and r["f"]["d_swing90_atr"] <= NEAR_ATR]
    b9 = [r for r in rows if r["f"].get("d_swing90_atr") is not None
          and r["f"]["d_swing90_atr"] > NEAR_ATR]
    contrast("свинги за 90 суток", a9, b9, "минимум у свинга", "не у свинга")
    out()
    out("  РАСПРЕДЕЛЕНИЕ ПО ГОДАМ (не «прячется» ли эффект в одном периоде):")
    yrs = sorted({time.gmtime(r["ts"] / 1000).tm_year for r in rows})
    for y in yrs:
        pa = [r for r in rows if time.gmtime(r["ts"] / 1000).tm_year == y
              and r["f"]["d_swing_atr"] <= NEAR_ATR]
        pb = [r for r in rows if time.gmtime(r["ts"] / 1000).tm_year == y
              and r["f"]["d_swing_atr"] > NEAR_ATR]
        A, B = agg(pa), agg(pb)
        out(f"    {y}  у свинга exp_r={A['exp_r']:+6.3f} (n={A['n']:3})  "
            f"не у свинга exp_r={B['exp_r']:+6.3f} (n={B['n']:3})  "
            f"dexp={B['exp_r']-A['exp_r']:+6.3f}")
    out()
    out("  ПО МОНЕТАМ (сколько монет из 5 показывают эффект в ту же сторону):")
    same = 0
    for s in SYMS:
        pa = [r for r in rows if r["sym"] == s
              and r["f"]["d_swing_atr"] <= NEAR_ATR]
        pb = [r for r in rows if r["sym"] == s
              and r["f"]["d_swing_atr"] > NEAR_ATR]
        A, B = agg(pa), agg(pb)
        dd = B["exp_r"] - A["exp_r"]
        same += 1 if dd > 0 else 0
        out(f"    {s:9} у свинга exp_r={A['exp_r']:+6.3f} (n={A['n']:3})  "
            f"не у свинга exp_r={B['exp_r']:+6.3f} (n={B['n']:3})  "
            f"dexp={dd:+6.3f}")
    out(f"    в ту же сторону: {same} из {len(SYMS)} монет")
    out()
    out("  ПО ПОЛОВИНАМ ОБУЧАЮЩЕГО ОКНА (holdout не трогаем):")
    mid = hold_i // 2
    for nm, sel in (("первая половина", lambda r: r["i"] < mid),
                    ("вторая половина", lambda r: r["i"] >= mid)):
        pa = [r for r in rows if sel(r) and r["f"]["d_swing_atr"] <= NEAR_ATR]
        pb = [r for r in rows if sel(r) and r["f"]["d_swing_atr"] > NEAR_ATR]
        A, B = agg(pa), agg(pb)
        out(f"    {nm:16} у свинга exp_r={A['exp_r']:+6.3f} (n={A['n']:3})  "
            f"не у свинга exp_r={B['exp_r']:+6.3f} (n={B['n']:3})  "
            f"dexp={B['exp_r']-A['exp_r']:+6.3f}")
    out()
    out(f"  КОНЦЕНТРАЦИЯ: доля всей плюсовой R от 3 лучших сделок в группе")
    out(f"    «не у свинга» {concentration(b):.2f}  (n={len(b)});  "
        f"«у свинга» {concentration(a):.2f} (n={len(a)});  "
        f"весь набор {concentration(rows):.2f}")
    out()
    out("  ПЕРЕСЕЧЕНИЕ ПРИЗНАКОВ (корреляция Пирсона, МЯГКИЙ набор):")
    keys = ["dens90pre", "d_swing_atr", "drop_poc_pos", "vol_spike",
            "drop_pct"]
    ok = [r for r in rows if all(r["f"].get(kk) is not None for kk in keys)]
    for x in range(len(keys)):
        vals = []
        for y in range(len(keys)):
            va = [r["f"][keys[x]] for r in ok]
            vb = [r["f"][keys[y]] for r in ok]
            try:
                vals.append(f"{statistics.correlation(va, vb):+5.2f}")
            except Exception:
                vals.append("  n/a")
        out(f"    {keys[x]:14} " + " ".join(vals))
    out(f"    (порядок: {', '.join(keys)}; n={len(ok)})")
    out()

    # ========================================================= 3. СТОПЫ
    out("=" * 100)
    out("3. ВАРИАНТЫ СТОПА")
    out("=" * 100)
    out("(а) dumplow — за минимумом падения (как в определении сетапа);")
    out("(б) hvn     — под нижней границей ближайшего объёмного кластера;")
    out("(в) swing   — под свинг-минимумом swing_bars=8 минус 0.3 ATR;")
    out("(г) atrK    — K*ATR от закрытия сигнальной свечи, K=1.5/2.0/3.0.")
    out("Метрики: exp_r, доля стопов, после которых цена ВСЁ РАВНО дошла бы до")
    out("тейка (слишком тесный стоп), средняя ширина стопа, сколько сделок")
    out("вообще состоялось (узкие/широкие стопы отсекаются лимитами).")
    out()
    KINDS = ["dumplow", "hvn", "swing", "atr1.5", "atr2.0", "atr3.0"]
    stop_rows = {}
    for tag in ("БАЗА", "МЯГКИЙ"):
        out(f"--- {tag}  (всего сигналов на обучении "
            f"{sum(len(v) for v in sets[tag]['tr'].values())})")
        base_rows = None
        for kind in KINDS:
            rows = (trades[tag] if kind == "dumplow"
                    else build_rows(data, sets[tag]["tr"], sets[tag]["g"],
                                    kind=kind, feats_cache=fc))
            stop_rows[(tag, kind)] = rows
            A = agg(rows)
            st = [r for r in rows if r["reason"] == "stop"]
            tight = (100.0 * sum(1 for r in st if r["would_tp"]) / len(st)
                     if st else 0.0)
            fit20 = 100.0 * sum(1 for r in rows
                                if r["stop_frac"] <= 0.8 * se3.liq_frac(20)) / max(1, len(rows))
            fit25 = 100.0 * sum(1 for r in rows
                                if r["stop_frac"] <= 0.8 * se3.liq_frac(25)) / max(1, len(rows))
            extra = (f"  тесных={tight:4.1f}%  "
                     f"влезает x20/x25={fit20:4.0f}/{fit25:4.0f}%")
            out(line(kind, A, extra))
            if kind == "dumplow":
                base_rows = rows
        out("  сравнение с (а) за минимумом падения:")
        for kind in KINDS[1:]:
            rows = stop_rows[(tag, kind)]
            t = welch_t([x["r"] for x in rows], [x["r"] for x in base_rows])
            dd = agg(rows)["exp_r"] - agg(base_rows)["exp_r"]
            out(f"    {kind:10} dexp={dd:+7.3f}  "
                f"t={t:+5.2f}  p={p_two_sided(t):.4f}")
            _TESTS.append((f"3.стоп {kind} vs dumplow {tag}", t, len(rows),
                           len(base_rows), dd))
        out()

    # случайный контроль для лучших вариантов стопа
    out("--- случайный вход с ТЕМИ ЖЕ вариантами стопа (МЯГКИЙ набор, 3 зерна)")
    n_by = {s: len(v) for s, v in sets["МЯГКИЙ"]["tr"].items()}
    for kind in KINDS:
        rows = []
        for sd in RAND_SEEDS[:3]:
            rows += random_rows(data, n_by, g_soft, 0, hold_i, sd, kind=kind,
                                feats_cache=fcr)
        A = agg(rows)
        S = agg(stop_rows[("МЯГКИЙ", kind)])
        t = welch_t([x["r"] for x in stop_rows[("МЯГКИЙ", kind)]],
                    [x["r"] for x in rows])
        out(f"  {kind:10} случайный exp_r={A['exp_r']:+7.3f} (n={A['n']:4})  "
            f"сигнал exp_r={S['exp_r']:+7.3f}  вклад={S['exp_r']-A['exp_r']:+7.3f}  "
            f"t={t:+5.2f}")
    out()

    # ========================================================= 4. SMART MONEY
    out("=" * 100)
    out("4. SMART MONEY (smc.py): order blocks, FVG, BOS/CHoCH")
    out("=" * 100)
    out("Фильтр входа: пускаем сделку только если на сигнальном баре цена")
    out("внутри бычьего ордер-блока / бычьего FVG / структурный тренд не")
    out("медвежий. В прошлых волнах SMC экзамены не проходил, но на dump_long")
    out("отдельно не проверялся.")
    out()
    for tag in ("БАЗА", "МЯГКИЙ"):
        out(f"--- {tag}")
        rows = trades[tag]
        for nm, fn in (("бычий ордер-блок", lambda r: r["f"]["ob"]),
                       ("бычий FVG", lambda r: r["f"]["fvg"]),
                       ("структура НЕ медвежья (bias>=0)",
                        lambda r: r["f"]["bias"] >= 0),
                       ("структура медвежья (bias<0)",
                        lambda r: r["f"]["bias"] < 0)):
            a = [r for r in rows if fn(r)]
            b = [r for r in rows if not fn(r)]
            contrast(nm, a, b, "фильтр ПРОШЁЛ", "фильтр отсёк", boot=False,
                     reg=f"4.SMC {nm} {tag}")
        ra = [r for r in rand[tag] if r["f"]["ob"]]
        rb = [r for r in rand[tag] if not r["f"]["ob"]]
        out(f"  {'   контроль OB на случайных':34} "
            f"dexp={agg(ra)['exp_r']-agg(rb)['exp_r']:+7.3f} "
            f"(n {len(ra)}/{len(rb)})")
        out()

    # ========================================================= 5. HOLDOUT
    out("=" * 100)
    out("5. HOLDOUT — единственная финальная проверка")
    out("=" * 100)
    out("Ни один порог и ни одна монета не выбирались по этому окну.")
    out()
    out("  «Купил и держал» на HOLDOUT (спот, без плеча) — контекст окна:")
    for s in SYMS:
        c4 = data[s]["c4"]
        out(f"    {s:9} {(c4[-1][4] / c4[hold_i][4] - 1) * 100:+8.1f}%")
    out()
    ho = {}
    for tag in ("БАЗА", "МЯГКИЙ"):
        ho[tag] = build_rows(data, sets[tag]["ho"], sets[tag]["g"],
                             kind="dumplow", feats_cache=fc)
    ho_rand = {}
    for tag in ("БАЗА", "МЯГКИЙ"):
        n_by = {s: len(v) for s, v in sets[tag]["ho"].items()}
        rows = []
        for sd in RAND_SEEDS:
            rows += random_rows(data, n_by, sets[tag]["g"], hold_i, n4, sd,
                                kind="dumplow", feats_cache=fcr)
        ho_rand[tag] = rows
    for tag in ("БАЗА", "МЯГКИЙ"):
        out(f"--- {tag}")
        out(line("holdout: все сигналы", agg(ho[tag])))
        out(line("holdout: случайный вход", agg(ho_rand[tag])))
        a, b, _ = split_by(ho[tag], "dens90pre", DENS_THR)
        out(line("holdout: минимум НА объёме", agg(a)))
        out(line("holdout: минимум В ПУСТОТЕ", agg(b)))
        a2, b2, _ = split_by(ho[tag], "d_swing_atr", NEAR_ATR, ge=False)
        out(line("holdout: минимум у свинга", agg(a2)))
        out(line("holdout: минимум не у свинга", agg(b2)))
        for kind in KINDS:
            rr = build_rows(data, sets[tag]["ho"], sets[tag]["g"], kind=kind,
                            feats_cache=fc)
            A = agg(rr)
            st = [r for r in rr if r["reason"] == "stop"]
            tight = (100.0 * sum(1 for r in st if r["would_tp"]) / len(st)
                     if st else 0.0)
            out(line(f"holdout стоп {kind}", A, f"  тесных={tight:4.1f}%"))
        out()

    # --- ПЕРЕНОС НА ЭКЗАМЕН ВСЕХ ПРИЗНАКОВ, ПРОШЕДШИХ СТРОГИЙ ПОРОГ ---
    out("-" * 100)
    out("ПРИЗНАКИ, ПРОШЕДШИЕ СТРОГИЙ ПОРОГ НА ОБУЧЕНИИ, — ПРОВЕРКА НА HOLDOUT")
    out("-" * 100)
    out("Отбор механический: берём КАЖДЫЙ признак, чей контраст на обучении")
    out(f"дал |t| >= 2.00 при выборках >=10 сделок в обеих группах (строгий")
    out(f"порог {zb:.2f} помечается отдельно), и считаем ровно тот же контраст")
    out("на holdout. Пороги (медианы, терцили) взяты С ОБУЧЕНИЯ и на holdout")
    out("НЕ пересчитываются. Совпадение знака — минимальное требование к")
    out("любому «улучшению»: если знак переворачивается, улучшения нет.")
    out()

    def _med(rows, key):
        vv = sorted(r["f"][key] for r in rows if r["f"].get(key) is not None)
        return vv[len(vv) // 2] if vv else None

    def _terc(rows, key):
        vv = sorted(r["f"][key] for r in rows if r["f"].get(key) is not None)
        if len(vv) < 6:
            return None, None
        return vv[len(vv) // 3], vv[2 * len(vv) // 3]

    checks = []
    for tag in ("БАЗА", "МЯГКИЙ"):
        tr = trades[tag]
        vs_med = _med(tr, "vol_spike")
        dp_lo, dp_hi = _terc(tr, "drop_poc_pos")
        checks += [
            (f"1.всплеск объёма {tag}", tag,
             lambda r, m=vs_med: (r["f"].get("vol_spike") is not None
                                  and r["f"]["vol_spike"] >= m),
             lambda r, m=vs_med: (r["f"].get("vol_spike") is not None
                                  and r["f"]["vol_spike"] < m),
             "объём выше медианы", "ниже медианы"),
            (f"1.объём у низа падения {tag}", tag,
             lambda r, x=dp_lo: (r["f"].get("drop_poc_pos") is not None
                                 and r["f"]["drop_poc_pos"] <= x),
             lambda r, x=dp_hi: (r["f"].get("drop_poc_pos") is not None
                                 and r["f"]["drop_poc_pos"] >= x),
             "объём у низа", "объём у верха"),
            (f"2.пробой прошлого мин. {tag}", tag,
             lambda r: r["f"].get("d_prevlow_atr") is not None
             and r["f"]["d_prevlow_atr"] < 0,
             lambda r: r["f"].get("d_prevlow_atr") is not None
             and r["f"]["d_prevlow_atr"] >= 0,
             "ПРОБИЛИ прошлый мин.", "удержали"),
            (f"2.свинг-минимум {tag}", tag,
             lambda r: r["f"].get("d_swing_atr") is not None
             and r["f"]["d_swing_atr"] <= NEAR_ATR,
             lambda r: r["f"].get("d_swing_atr") is not None
             and r["f"]["d_swing_atr"] > NEAR_ATR,
             "минимум у свинга", "не у свинга"),
        ]
        for key, nm in (("dens30pre", "30 сут, ДО"),
                        ("dens90pre", "90 сут, ДО"),
                        ("dens30", "30 сут, СЕЙЧАС"),
                        ("dens90", "90 сут, СЕЙЧАС")):
            checks.append(
                (f"1.объём dens {tag} {nm}", tag,
                 lambda r, k=key: (r["f"].get(k) is not None
                                   and r["f"][k] >= DENS_THR),
                 lambda r, k=key: (r["f"].get(k) is not None
                                   and r["f"][k] < DENS_THR),
                 "на объёме", "в пустоте"))
    reg = {x[0]: x for x in _TESTS}
    n_strict = 0
    repl = []
    for nm, tag, fa, fb, la, lb in checks:
        rec = reg.get(nm)
        if rec is None or abs(rec[1]) < 2.0 or min(rec[2], rec[3]) < 10:
            continue
        n_strict += 1
        ta = [r for r in trades[tag] if fa(r)]
        tb = [r for r in trades[tag] if fb(r)]
        ha = [r for r in ho[tag] if fa(r)]
        hb = [r for r in ho[tag] if fb(r)]
        t_tr = welch_t([x["r"] for x in ta], [x["r"] for x in tb])
        t_ho = welch_t([x["r"] for x in ha], [x["r"] for x in hb])
        d_tr = agg(ta)["exp_r"] - agg(tb)["exp_r"]
        d_ho = agg(ha)["exp_r"] - agg(hb)["exp_r"]
        out(f"  {nm}")
        out(line(f"   обучение: {la}", agg(ta)))
        out(line(f"   обучение: {lb}", agg(tb)))
        out(line(f"   HOLDOUT:  {la}", agg(ha)))
        out(line(f"   HOLDOUT:  {lb}", agg(hb)))
        keep = d_tr * d_ho > 0
        repl.append((nm, d_tr, t_tr, d_ho, t_ho, keep, abs(rec[1]) >= zb))
        out(f"     {'СТРОГО значим на обучении' if abs(rec[1]) >= zb else 'номинально значим на обучении'}: "
            f"dexp обучение {d_tr:+.3f} (t={t_tr:+.2f})  ->  "
            f"HOLDOUT {d_ho:+.3f} (t={t_ho:+.2f})   "
            f"{'ЗНАК СОХРАНИЛСЯ' if keep else 'ЗНАК ПЕРЕВЕРНУЛСЯ'}")
        out()
    out(f"  ИТОГ: перенесено {len(repl)} признаков, знак сохранился у "
        f"{sum(1 for x in repl if x[5])}, перевернулся у "
        f"{sum(1 for x in repl if not x[5])}.")
    st_repl = [x for x in repl if x[6]]
    out(f"  Из них строго значимых на обучении {len(st_repl)}, знак сохранился "
        f"у {sum(1 for x in st_repl if x[5])}.")
    out()
    if not n_strict:
        out("  На обучении строгий порог не прошёл ни один признак — переносить")
        out("  на holdout нечего.")
        out()

    # --- ЕДИНСТВЕННОЕ решение, вынесенное с обучения на экзамен ---
    out("-" * 100)
    out("КАНДИДАТ, ВЫБРАННЫЙ ПО ОБУЧАЮЩЕЙ ЧАСТИ, — ОДНА проверка на HOLDOUT")
    out("-" * 100)
    out("На обучении единственная связка, дававшая номинальный плюс, оказалась")
    out("ОБРАТНОЙ гипотезе владельца: брать только те капитуляции, чей минимум")
    out("НЕ у прошлого свинга и НЕ на объёме (ушёл в пустоту). Проверяем её на")
    out("HOLDOUT ровно один раз, без правок.")

    def cand(r):
        f = r["f"]
        return (f.get("d_swing_atr") is not None
                and f["d_swing_atr"] > NEAR_ATR
                and f.get("dens90pre") is not None
                and f["dens90pre"] < DENS_THR)

    for tag in ("БАЗА", "МЯГКИЙ"):
        tr_c = [r for r in trades[tag] if cand(r)]
        ho_c = [r for r in ho[tag] if cand(r)]
        ho_r = [r for r in ho_rand[tag] if cand(r)]
        out(line(f"{tag} обучение, кандидат", agg(tr_c)))
        out(line(f"{tag} HOLDOUT,  кандидат", agg(ho_c)))
        out(line(f"{tag} HOLDOUT,  случайный вход с тем же фильтром", agg(ho_r)))
        out(line(f"{tag} HOLDOUT,  все сигналы (для сравнения)", agg(ho[tag])))
    out()

    # ------------------------------------------------ портфельный прогон
    out("-" * 100)
    out("ПОРТФЕЛЬНЫЙ ПРОГОН (одна позиция на монету, как в движке) — контроль,")
    out("что выводы не артефакт независимого счёта сделок")
    out("-" * 100)
    for tag, g in (("БАЗА", g_base), ("МЯГКИЙ", g_soft)):
        for nm, rng in (("обучение", (0, hold_i)), ("HOLDOUT", (hold_i, n4))):
            tot_r, tot_n, tot_pnl = [], 0, 0.0
            for s in SYMS:
                r = se3.run_setup("dump_long", g, data[s]["c4"], data[s]["ctx"],
                                  data[s]["c15"], data[s]["ts15"], LEV,
                                  symbol=s, signal_range=rng,
                                  collect_diag=False, interval_min=IV)
                tot_r += [t["r"] for t in r["trades"]]
                tot_n += len(r["trades"])
                tot_pnl += sum(t["pnl"] for t in r["trades"])
            out(f"  {tag:7} {nm:9} сделок {tot_n:4}  "
                f"exp_r={statistics.mean(tot_r) if tot_r else 0:+7.3f}  "
                f"сумма pnl={tot_pnl:+8.2f}$  "
                f"t={t_one(tot_r):+5.2f}   (движок, stop_mode=2 k=2ATR)")
    out()

    # ======================================================== ВЫВОДЫ
    out("=" * 100)
    out("ВЫВОДЫ")
    out("=" * 100)
    zb = z_for_p(0.05 / N_TESTS)
    rows = [r for r in trades["МЯГКИЙ"] if r["f"].get("d_swing_atr") is not None]
    a = [r for r in rows if r["f"]["d_swing_atr"] <= NEAR_ATR]
    b = [r for r in rows if r["f"]["d_swing_atr"] > NEAR_ATR]
    dn = [r for r in trades["МЯГКИЙ"] if r["f"].get("dens90pre") is not None]
    lo = [r for r in dn if r["f"]["dens90pre"] < DENS_THR]
    hi = [r for r in dn if r["f"]["dens90pre"] >= DENS_THR]
    hdn = [r for r in ho["МЯГКИЙ"] if r["f"].get("dens90pre") is not None]
    hlo = [r for r in hdn if r["f"]["dens90pre"] < DENS_THR]
    hhi = [r for r in hdn if r["f"]["dens90pre"] >= DENS_THR]

    def treg(name):
        """t и dexp из реестра проверок по имени."""
        for nm, t, na, nb, dd in _TESTS:
            if nm == name:
                return t, dd, na, nb
        return 0.0, 0.0, 0, 0

    out()
    out("0. ТАБЛИЦА ВСЕХ ЗАЯВЛЕННЫХ ПРОВЕРОК (поправка на множественность).")
    out(f"   Проведено контрастов: {len(_TESTS)}; плюс перенос значимых признаков "
        f"и итогового кандидата")
    out(f"   на holdout. Строгий порог |t| >= {zb:.2f}, номинальный 2.00.")
    out("   dexp — насколько лучше первая группа второй, в единицах риска R.")
    for nm, t, na, nb, dd in sorted(_TESTS, key=lambda x: -abs(x[1]))[:12]:
        if min(na, nb) < 10:
            mark = "n<10 — t недостоверна, не засчитываем"
        elif abs(t) >= zb:
            mark = "СТРОГО ЗНАЧИМО"
        elif abs(t) >= 2.0:
            mark = "номинально"
        else:
            mark = ""
        out(f"     {nm:38} dexp={dd:+6.3f}  t={t:+6.2f}  n={na}/{nb}  {mark}")
    strict = [x for x in _TESTS if abs(x[1]) >= zb and min(x[2], x[3]) >= 10]
    nomin = [x for x in _TESTS if abs(x[1]) >= 2.0 and min(x[2], x[3]) >= 10]
    out(f"   ИТОГО при выборках >=10 сделок в обеих группах: строгий порог "
        f"прошли {len(strict)}, номинальный {len(nomin)} из {len(_TESTS)}.")
    out(f"   Случайно ожидалось бы примерно {0.05 * len(_TESTS):.1f} "
        f"номинальных — значит структура в данных ЕСТЬ.")
    out()
    out(f"   ГЛАВНОЕ — ПЕРЕНОС НА HOLDOUT. Из {len(repl)} признаков, значимых "
        f"на обучении, знак сохранился")
    out(f"   у {sum(1 for x in repl if x[5])}, перевернулся у "
        f"{sum(1 for x in repl if not x[5])}; из {len(st_repl)} СТРОГО "
        f"значимых не выжил НИ ОДИН.")
    out("   Уцелели только эти, и ВСЕ они говорят, что уровень МЕШАЕТ:")
    for nm, d_tr, t_tr, d_ho, t_ho, keep, st in repl:
        if keep:
            out(f"     {nm:34} обучение {d_tr:+.3f} -> holdout {d_ho:+.3f} "
                f"(t={t_ho:+.2f})")
    out()
    out("1. ПОМОГАЕТ ЛИ ПРИВЯЗКА К ОБЪЁМНЫМ УРОВНЯМ — НЕТ. ЗНАК ОБРАТНЫЙ,")
    out("   И ЭТО ЕДИНСТВЕННОЕ, ЧТО ПОВТОРИЛОСЬ НА ЭКЗАМЕНЕ.")
    out("   Гипотеза «капитуляция, вставшая НА объёмном уровне, отскакивает")
    out("   лучше» не подтвердилась ни в одном из 8 сочетаний (2 набора x 2")
    out("   окна x 2 якоря) — разница везде в МИНУС.")
    out(f"     обучение: на объёме {agg(hi)['exp_r']:+.3f}R (n={len(hi)}) "
        f"против {agg(lo)['exp_r']:+.3f}R в пустоте (n={len(lo)}), "
        f"dexp={agg(hi)['exp_r'] - agg(lo)['exp_r']:+.3f};")
    out(f"     HOLDOUT:  на объёме {agg(hhi)['exp_r']:+.3f}R (n={len(hhi)}) "
        f"против {agg(hlo)['exp_r']:+.3f}R в пустоте (n={len(hlo)}), "
        f"dexp={agg(hhi)['exp_r'] - agg(hlo)['exp_r']:+.3f}.")
    out("   То есть в ОБОИХ окнах падение, упершееся в объёмную полку,")
    out("   отскакивает ХУЖЕ падения, ушедшего в пустоту. Как фильтр входа")
    out("   «отскок от объёма» отбирает худшую часть сделок, а не лучшую.")
    z0 = [r for r in trades["МЯГКИЙ"] if r["f"].get("dens90pre") == 0.0]
    nz0 = [r for r in trades["МЯГКИЙ"]
           if r["f"].get("dens90pre") not in (None, 0.0)]
    out("   Крайний случай в ту же сторону: капитуляции, чей минимум ушёл")
    out(f"   ВООБЩЕ НИЖЕ всего 90-дневного профиля ({len(z0)} из "
        f"{len(z0) + len(nz0)}), дают {agg(z0)['exp_r']:+.3f}R против "
        f"{agg(nz0)['exp_r']:+.3f}R.")
    dv = sorted(r["f"]["dens90pre"] for r in trades["МЯГКИЙ"]
                if r["f"].get("dens90pre") is not None)
    out(f"   Почему так: медиана плотности в точке остановки — "
        f"{dv[len(dv) // 2]:.2f} от средней по профилю.")
    out("   Падение почти всегда заканчивается НИЖЕ наторгованного диапазона,")
    out(f"   а не на полке объёма; «встали на объёме» — редкие "
        f"{100.0 * sum(1 for x in dv if x >= DENS_THR) / len(dv):.0f}% случаев.")
    out("   Разрешение данных ни при чём: плотность по 1ч- и по 15м-объёмам")
    out("   коррелирует +0.9996..+0.9998, порог 1.0 пересекают по-разному")
    out("   единицы сделок из сотен.")
    t_va, d_va, _, _ = treg("1.зона стоимости МЯГКИЙ")
    t_pc, d_pc, npc, _ = treg("1.близость к POC МЯГКИЙ")
    out(f"   Зона стоимости и POC — то же самое: минимум В зоне стоимости "
        f"даёт {d_va:+.3f}R")
    out(f"   относительно «вне зоны» (t={t_va:+.2f}); у POC {d_pc:+.3f}R "
        f"относительно «далеко» (t={t_pc:+.2f}, n={npc}).")
    out()
    out("2. ЦЕНОВЫЕ УРОВНИ БЕЗ ОБЪЁМА — ТА ЖЕ КАРТИНА, НО СЛАБЕЕ.")
    hrows = [r for r in ho["МЯГКИЙ"] if r["f"].get("d_swing_atr") is not None]
    hsa = [r for r in hrows if r["f"]["d_swing_atr"] <= NEAR_ATR]
    hsb = [r for r in hrows if r["f"]["d_swing_atr"] > NEAR_ATR]
    t_sw, d_sw, _, _ = treg("2.свинг-минимум МЯГКИЙ")
    t_rd, d_rd, _, _ = treg("2.круглое число МЯГКИЙ")
    out(f"   Свинг-минимум: у свинга {agg(a)['exp_r']:+.3f}R (n={len(a)}), "
        f"не у свинга {agg(b)['exp_r']:+.3f}R (n={len(b)}),")
    out(f"   dexp={d_sw:+.3f} (t={t_sw:+.2f}); на HOLDOUT знак тот же, но "
        f"эффект почти исчез:")
    out(f"   {agg(hsa)['exp_r']:+.3f}R против {agg(hsb)['exp_r']:+.3f}R "
        f"(dexp={agg(hsa)['exp_r'] - agg(hsb)['exp_r']:+.3f}).")
    out(f"   Круглые числа: dexp={d_rd:+.3f} (t={t_rd:+.2f}) — направление то "
        f"же, значимости нет.")
    out("   Контроль случайными входами: у случайного входа такого расслоения")
    out("   нет (dexp -0.01..+0.13), то есть расслоение связано именно с")
    out("   капитуляцией — но направлено ПРОТИВ идеи отскока от уровня.")
    a9 = [r for r in rows if r["f"].get("d_swing90_atr") is not None
          and r["f"]["d_swing90_atr"] <= NEAR_ATR]
    b9 = [r for r in rows if r["f"].get("d_swing90_atr") is not None
          and r["f"]["d_swing90_atr"] > NEAR_ATR]
    d9 = agg(a9)["exp_r"] - agg(b9)["exp_r"]
    n_all = statistics.mean([r["f"]["n_swing"] for r in rows])
    n_90 = statistics.mean([r["f"]["n_swing90"] for r in rows
                            if r["f"].get("n_swing90") is not None])
    out("   Признак «не у свинга» частично мерит возраст истории: за 3 года")
    out(f"   накапливается в среднем {n_all:.0f} свингов против {n_90:.0f} за "
        f"последние 90 суток,")
    out("   и «далеко от всех» механически становится всё реже. Если считать")
    out(f"   только свежие свинги, эффект слабеет: dexp={d9:+.3f} вместо "
        f"{d_sw:+.3f}")
    out(f"   (t={welch_t([x['r'] for x in a9], [x['r'] for x in b9]):+.2f}).")
    out()
    out("3. СТОПЫ: РАЗНИЦЫ В ОЖИДАНИИ НЕТ, РАЗНИЦА ТОЛЬКО В ШИРИНЕ.")
    st_t = max(abs(x[1]) for x in _TESTS if x[0].startswith("3.стоп"))
    out(f"   Максимальная |t| среди 10 сравнений с базовым стопом = {st_t:.2f} "
        f"при строгом пороге {zb:.2f}.")
    out("   Обучение (МЯГКИЙ) / HOLDOUT (МЯГКИЙ):")
    for kind in KINDS:
        A = agg(stop_rows[("МЯГКИЙ", kind)])
        H = agg(build_rows(data, sets["МЯГКИЙ"]["ho"], sets["МЯГКИЙ"]["g"],
                           kind=kind, feats_cache=fc))
        stp = [r for r in stop_rows[("МЯГКИЙ", kind)] if r["reason"] == "stop"]
        tight = (100.0 * sum(1 for r in stp if r["would_tp"]) / len(stp)
                 if stp else 0.0)
        n_all = max(1, A["n"])
        f20 = 100.0 * sum(1 for r in stop_rows[("МЯГКИЙ", kind)]
                          if r["stop_frac"] <= 0.8 * se3.liq_frac(20)) / n_all
        f25 = 100.0 * sum(1 for r in stop_rows[("МЯГКИЙ", kind)]
                          if r["stop_frac"] <= 0.8 * se3.liq_frac(25)) / n_all
        out(f"     {kind:8} ширина {A['stop_pct']:4.2f}%  "
            f"exp_r обуч {A['exp_r']:+6.3f} (n={A['n']:3})  "
            f"holdout {H['exp_r']:+6.3f} (n={H['n']:3})  "
            f"тесных {tight:4.1f}%  влезает x20/x25 {f20:3.0f}/{f25:3.0f}%")
    n_hvn = agg(stop_rows[("МЯГКИЙ", "hvn")])["n"]
    n_dl = max(1, agg(stop_rows[("МЯГКИЙ", "dumplow")])["n"])
    out("   ЛУЧШИЙ ВАРИАНТ ВЫБРАТЬ НЕЛЬЗЯ: на обучении впереди hvn/swing, на")
    out("   holdout — atr1.5, и все различия статистически неотличимы от нуля.")
    out("   Практический выбор приходится делать по другим соображениям:")
    out(f"     - (б) под объёмным кластером ОТПАДАЕТ: подходящий кластер ниже")
    out(f"       минимума падения есть лишь в {100.0 * n_hvn / n_dl:.0f}% "
        f"случаев, остальные сигналы теряются,")
    out(f"       а стопы получаются самыми широкими "
        f"({agg(stop_rows[('МЯГКИЙ', 'hvn')])['stop_pct']:.2f}%);")
    out("     - (а) за минимумом падения и (г) 1.5*ATR — самые узкие и почти")
    out("       не теряют сигналов; (в) свинг-минимум им эквивалентен;")
    out("     - (г) 2*ATR — единственный вариант, стабильно ХУДШИЙ на обоих")
    out("       окнах (хотя и не значимо). Именно он стоит в дефолте движка.")
    out()
    out("   ПЛЕЧО x20-25 (прямой запрос владельца). Предельная ширина стопа —")
    out(f"   0.8*(1/плечо - 0.5%) = {0.8 * se3.liq_frac(20) * 100:.1f}% при "
        f"x20 и {0.8 * se3.liq_frac(25) * 100:.1f}% при x25.")
    dl = stop_rows[("МЯГКИЙ", "dumplow")]
    out(f"   Стоп за минимумом падения влезает в x20 в "
        f"{100.0 * sum(1 for r in dl if r['stop_frac'] <= 0.8 * se3.liq_frac(20)) / len(dl):.0f}%"
        f" сделок и в x25 — в "
        f"{100.0 * sum(1 for r in dl if r['stop_frac'] <= 0.8 * se3.liq_frac(25)) / len(dl):.0f}%;")
    out("   3*ATR не влезает практически никогда. То есть x20-25 на этом сетапе")
    out("   не «ускоряет прибыль», а МЕХАНИЧЕСКИ выбрасывает половину сигналов")
    out("   и заставляет ставить тесный стоп. Цена тесного стопа измерена: у")
    out("   самых узких вариантов доля «выбило зря» (цена всё равно дошла бы до")
    out("   тейка) — 49-58%, у 3*ATR — 16%. За быстрый выход платят тем, что")
    out("   каждая вторая выбитая сделка была бы прибыльной.")
    out()
    out("4. SMART MONEY (smc.py) НА dump_long НЕ РАБОТАЕТ.")
    t_ob1, d_ob1, _, _ = treg("4.SMC бычий ордер-блок БАЗА")
    t_ob2, d_ob2, _, _ = treg("4.SMC бычий ордер-блок МЯГКИЙ")
    t_fv1, d_fv1, _, _ = treg("4.SMC бычий FVG БАЗА")
    t_fv2, d_fv2, _, _ = treg("4.SMC бычий FVG МЯГКИЙ")
    out(f"   Ордер-блок как фильтр входа: БАЗА dexp={d_ob1:+.3f} "
        f"(t={t_ob1:+.2f}), МЯГКИЙ {d_ob2:+.3f} (t={t_ob2:+.2f}) —")
    out("   фильтр отбирает сделки ХУЖЕ отсеянных, а не лучше. На случайных")
    out("   входах тот же знак (dexp -0.14..-0.17), значит это свойство самого")
    out("   индикатора, а не капитуляции.")
    out(f"   FVG: {d_fv1:+.3f} (t={t_fv1:+.2f}) и {d_fv2:+.3f} "
        f"(t={t_fv2:+.2f}) — знаки разные, это шум.")
    nb1 = sum(1 for r in trades["МЯГКИЙ"] if r["f"]["bias"] < 0)
    out(f"   Структура BOS/CHoCH как фильтр бесполезна по построению: "
        f"{100.0 * nb1 / len(trades['МЯГКИЙ']):.0f}% капитуляций")
    out("   происходят при медвежьей структуре, «не медвежьих» единицы, и они")
    out("   заметно хуже. Итог совпадает с прошлыми волнами: SMC не проходит")
    out("   экзамен и на этом сетапе.")
    out()
    out("5. ЧТО ЭТО ЗНАЧИТ ДЛЯ СЕТАПА.")
    out(f"   Сам dump_long на обучении даёт "
        f"{agg(trades['МЯГКИЙ'])['exp_r']:+.3f}R против "
        f"{agg(rand['МЯГКИЙ'])['exp_r']:+.3f}R у случайного входа той же")
    out(f"   частоты (вклад "
        f"{agg(trades['МЯГКИЙ'])['exp_r'] - agg(rand['МЯГКИЙ'])['exp_r']:+.3f}), "
        f"на holdout {agg(ho['МЯГКИЙ'])['exp_r']:+.3f}R против "
        f"{agg(ho_rand['МЯГКИЙ'])['exp_r']:+.3f}R")
    out(f"   (вклад "
        f"{agg(ho['МЯГКИЙ'])['exp_r'] - agg(ho_rand['МЯГКИЙ'])['exp_r']:+.3f}). "
        f"Знак вклада между окнами меняется — сырого")
    out("   преимущества как не было, так и нет; это воспроизводит вывод")
    out("   idea_test, полученный другим кодом и на других параметрах.")
    out("   Окна очень разные: на обучении купил-и-держал +12..+921%, на")
    out("   holdout -42..-68%. Уровень доходности объясняет ОКНО, а не сетап —")
    out("   ровно как и было измерено раньше (календарь 39.4% дисперсии).")
    hc = [r for r in ho["МЯГКИЙ"] if cand(r)]
    hcr = [r for r in ho_rand["МЯГКИЙ"] if cand(r)]
    tc = [r for r in trades["МЯГКИЙ"] if cand(r)]
    out("   Кандидат, отобранный по обучению (минимум НЕ у свинга И НЕ на")
    out(f"   объёме): обучение {agg(tc)['exp_r']:+.3f}R (n={len(tc)}), "
        f"holdout {agg(hc)['exp_r']:+.3f}R (n={len(hc)}) против")
    out(f"   {agg(hcr)['exp_r']:+.3f}R у случайного входа с тем же фильтром "
        f"(n={len(hcr)}). Знак сохранился,")
    out(f"   но {len(hc)} сделок — это не доказательство. Как правило брать "
        f"нельзя.")
    out()
    out("   ЧТО ДЕЛАТЬ ПО ИТОГАМ (честно):")
    out("   - «отскок от горизонтальных объёмов и уровней» на dump_long НЕ")
    out("     подтверждается ни как фильтр входа, ни как способ поставить стоп;")
    out("     единственный воспроизводимый эффект — обратный: у уровня хуже;")
    out("   - привязывать стоп к объёмному кластеру нельзя: правило применимо")
    out("     лишь к трети сигналов и даёт самые широкие стопы;")
    out("   - SMC (order blocks / FVG / BOS-CHoCH) на этом сетапе не работает —")
    out("     эту ветку можно закрыть;")
    out("   - плечо x20-25 при честном стопе отсекает половину сигналов; если")
    out("     владелец хочет быстрый выход, менять надо ВЫХОД (тейк, трейлинг,")
    out("     таймаут), а не плечо: плечо не создаёт преимущества, а только")
    out("     масштабирует уже нулевое;")
    out("   - подтвердившиеся на обучении, но НЕ выжившие на holdout гипотезы")
    out("     (всплеск объёма в сливе, положение объёма внутри падения, пробой")
    out("     прошлого минимума) — не «почти работающие», а урок: на 443")
    out("     сделках t=+3.8 с поправкой Бонферрони всё равно оказалось")
    out("     свойством окна. Любой будущий фильтр обязан проверяться")
    out("     ПЕРЕНОСОМ ЗНАКА на отдельное окно, а не только t-статистикой.")
    out()

    out("-" * 100)
    out(f"время работы {time.time() - t_start:.0f}с")
    out("-" * 100)

    with open(os.path.join(BASE, "cap_levels_out.txt"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(_LINES) + "\n")


if __name__ == "__main__":
    main()
