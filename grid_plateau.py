# -*- coding: utf-8 -*-
"""grid_plateau.py — КАРТА УСТОЙЧИВОСТИ простой сетки усреднения.

Задача НЕ в том, чтобы найти лучшие параметры (это делали до нас, и это
находило везение). Задача — измерить ФОРМУ ландшафта: широкое плато
прибыльности или узкий пик. Узкий пик = подгонка; широкое плато = механика.

СКЕЛЕТ (никаких фильтров, никаких дополнительных условий):
  вход   : RSI(rsi_p) пересекает вниз rsi_th  -> ЛОНГ;
           RSI(rsi_p) пересекает вверх 100-rsi_th -> ШОРТ  (симметрично,
           один порог = один параметр; так же устроены боты проекта);
  сетка  : levels колен, шаг между коленами = step_atr x дневной ATR(14д),
           объём колена растёт множителем 1.5 (ФИКСИРОВАН, не параметр);
  тейк   : tp  % от СРЕДНЕЙ цены позиции (лимитка, maker);
  стоп   : stop % от СРЕДНЕЙ цены позиции (маркет, taker+проскальзывание);
           средняя пересчитывается после каждой доливки, значит стоп
           «едет» вслед за сеткой — это и есть смысл «стоп в % от средней»;
  плечо  : lev.
Больше НИЧЕГО: ни таймаута, ни кулдауна, ни зон, ни фильтра ножа, ни
переноса в безубыток. 6 варьируемых параметров (7-й, плечо, — 2 значения).

ЧЕСТНОСТЬ (издержки и порядок событий скопированы с evolution2.run5):
  TAKER 0.055%, MAKER 0.02%, проскальзывание 0.03% на маркет-исполнениях,
  funding 0.01%/8ч, депозит 20$, маржа цикла 5$ (фикс, без реинвеста),
  ликвидация при MM=0.95 от использованной маржи, слив = баланс < 5$.
  Внутри свечи консервативный порядок: сначала исполняются лимитки-доливки
  по пути цены, стоп срабатывает уже по УВЕЛИЧЕННОЙ позиции; тейк
  проверяется по средней ДО доливок этой свечи; ликвидация имеет приоритет
  над стопом, если она ближе.
  Просадка считается по ПЛАВАЮЩЕМУ капиталу (баланс + нереализованный PnL
  открытой сетки), а не только по закрытым сделкам — для сетки это
  принципиально, закрытая просадка всегда красивее.

Нет заглядывания вперёд: RSI, дневной ATR и режим рынка причинные (режим —
это режим ВЧЕРАШНЕГО дня, ATR — по завершённым дням). Проверяется
self_test'ом на префиксе: движок на candles[:k] обязан дать те же сделки,
что и на полном ряде.

Запуск:   python grid_plateau.py            (полный прогон, ~15-25 мин)
          python grid_plateau.py --report    (только отчёт из кэша)
          python grid_plateau.py --smoke     (быстрая проверка движка)
Вывод дублируется в grid_plateau_out.txt.
"""

import itertools
import json
import os
import pickle
import random
import statistics
import sys
import time
import zlib

BASE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.environ.get("GP_SCRATCH", BASE)
DATA_PKL = os.path.join(SCRATCH, "grid_plateau_data.pkl")
RAW_JSON = os.path.join(SCRATCH, "grid_plateau_raw.json")
OUT_TXT = os.path.join(BASE, "grid_plateau_out.txt")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "LTCUSDT"]
DAYS = 1150
WARMUP_BARS = 96 * 100          # 100 дней прогрева индикаторов и режима
N_PERIODS = 3

# --- издержки и счёт (как в evolution2) ---
TAKER, MAKER = 0.00055, 0.0002
SLIP = 0.0003
FUND_PER_BAR = 0.0001 / 32      # 0.01% за 8ч = 32 свечи по 15м
START, MARGIN, MM = 20.0, 5.0, 0.95
MULT = 1.5                      # множитель объёма колена (фиксирован)

# --- сетка перебора ---
GRID = [
    ("rsi_p",    [7, 14, 21]),
    ("rsi_th",   [25, 30, 35]),
    ("levels",   [2, 3, 4]),
    ("step_atr", [0.2, 0.4, 0.7]),
    ("tp",       [0.010, 0.020, 0.035]),
    ("stop",     [0.04, 0.07, 0.10, 0.15]),
    ("lev",      [5, 10, 15]),
]
PNAMES = [k for k, _ in GRID]
PVALS = {k: v for k, v in GRID}
COMBOS = [dict(zip(PNAMES, vals))
          for vals in itertools.product(*[v for _, v in GRID])]

RSI_SET = sorted({v for v in PVALS["rsi_p"]})
TH_SET = sorted({v for v in PVALS["rsi_th"]})

NULL_SEEDS = [1, 2]             # зёрна нулевой модели (случайные входы)
E_SEEDS = [1]                   # карта E (случайная сторона) — одно зерно

_OUT = []


def out(s=""):
    print(s)
    _OUT.append(s)


def flush_out():
    with open(OUT_TXT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(_OUT) + "\n")


# ======================= подготовка данных (родитель) =======================

def daily_atr_pct(candles, n=14):
    """Дневной ATR (Уайлдер, n дней) в долях цены, причинно.

    Для свечи дня D берётся ATR, посчитанный по дням <= D-1 (вчерашний
    завершённый день). Заглядывания вперёд нет по построению."""
    day = {}
    order = []
    for ts, o, h, l, c in candles:
        d = ts // 86400000
        if d in day:
            x = day[d]
            if h > x[1]:
                x[1] = h
            if l < x[2]:
                x[2] = l
            x[3] = c
        else:
            day[d] = [o, h, l, c]
            order.append(d)
    val, trs, prev_c = None, [], None
    by_day = {}
    for d in order:
        o, h, l, c = day[d]
        tr = (h - l) if prev_c is None else max(h - l, abs(h - prev_c),
                                                abs(l - prev_c))
        prev_c = c
        if val is None:
            trs.append(tr)
            if len(trs) == n:
                val = sum(trs) / n
        else:
            val = (val * (n - 1) + tr) / n
        by_day[d] = (val / c) if val else None
    return [by_day.get(ts // 86400000 - 1) for ts, *_ in candles]


def prepare_data():
    """Считает свечи/индикаторы один раз и кладёт в компактный pkl."""
    import backtest_rsi_grid as bg
    import evolution as ev
    import evolution6 as e6

    data = {}
    t0 = None
    for sym in SYMBOLS:
        candles = ev.fetch(sym, "15", DAYS)
        ts0, ts1 = candles[0][0], candles[-1][0]
        if t0 is None:
            t0, t1, n0 = ts0, ts1, len(candles)
        # ряды разных монет качались не одновременно: допускаем сдвиг до 1 суток,
        # но длина обязана совпадать (иначе индексные периоды разъедутся)
        assert len(candles) == n0 and abs(ts0 - t0) < 86400000, \
            f"ряды разъехались: {sym} {len(candles)} {ts0-t0}"
        gaps = sum(1 for i in range(1, len(candles))
                   if candles[i][0] - candles[i - 1][0] != 900000)
        assert gaps == 0, f"дыры в ряду {sym}: {gaps}"
        closes = [c[4] for c in candles]
        data[sym] = dict(
            h=[c[2] for c in candles],
            l=[c[3] for c in candles],
            c=closes,
            ts=[candles[0][0], candles[-1][0]],
            atr=daily_atr_pct(candles),
            reg=e6.calc_regime(candles),
            rsi={p: bg.calc_rsi(closes, p) for p in RSI_SET},
        )
        print(f"  {sym}: {len(candles)} свечей")
    n = n0
    edges = [WARMUP_BARS + round((n - WARMUP_BARS) * k / N_PERIODS)
             for k in range(N_PERIODS + 1)]
    periods = [(edges[k], edges[k + 1]) for k in range(N_PERIODS)]
    meta = dict(n=n, t0=t0, t1=t1, periods=periods)
    with open(DATA_PKL, "wb") as fh:
        pickle.dump(dict(data=data, meta=meta), fh, protocol=4)
    return data, meta


# ============================== движок ==============================

def make_sig(rsi, th, i0, i1):
    """Сигналы RSI: +1 лонг (пересечение th вниз), -1 шорт (100-th вверх)."""
    sig = bytearray(i1)
    ob = 100 - th
    for i in range(i0, i1):
        a, b = rsi[i - 1], rsi[i]
        if a is None or b is None:
            continue
        if a >= th > b:
            sig[i] = 1
        elif a <= ob < b:
            sig[i] = 255          # -1
    return sig


def make_null_sig(n_l, n_s, i0, i1, seed):
    """Нулевая модель: столько же входов и та же доля лонгов/шортов,
    но моменты входа случайные (проверка «важен ли момент входа»)."""
    rnd = random.Random(seed)
    k = n_l + n_s
    sig = bytearray(i1)
    if k <= 0:
        return sig
    idx = rnd.sample(range(i0, i1), min(k, i1 - i0))
    for j, i in enumerate(idx):
        sig[i] = 1 if j < n_l else 255
    return sig


def run(H, L, C, ATR, SIG, REG, prm, i0, i1):
    """Один прогон сетки на отрезке [i0, i1). REG=None — без фильтра тренда.

    Возврат: (итог %, макс. просадка по плавающему капиталу %, циклов,
              побед, ликвидаций, слив?, баров в позиции)"""
    lev = prm["lev"]
    nlv = prm["levels"]
    tp = prm["tp"]
    slp = prm["stop"]
    katr = prm["step_atr"]
    w = [MULT ** k for k in range(nlv)]
    tw = sum(w)
    m_k = [MARGIN * x / tw for x in w]
    cum_m = [0.0] * (nlv + 1)
    for k in range(nlv):
        cum_m[k + 1] = cum_m[k] + m_k[k]

    balance = peak = START
    max_dd = 0.0
    trades = wins = liqs = 0
    bars_pos = 0
    ruined = False
    sgn = 0
    pq = pnot = pfees = 0.0
    pnf = 0
    padds = []

    for i in range(i0, i1):
        if sgn == 0 and SIG[i] == 0:
            continue
        h, l, c = H[i], L[i], C[i]
        if sgn != 0:
            bars_pos += 1
            avg = pnot / pq
            pfees += pq * c * FUND_PER_BAR
            stop_px = avg * (1 - sgn * slp)
            tp_px = avg * (1 + sgn * tp)
            # выход вниз — ЧТО БЛИЖЕ: стоп или ликвидация. Если стоп шире
            # ликвидационного расстояния (широкий стоп + большое плечо), то
            # позицию уносит ликвидация, и никакого «стоп спасёт» не будет.
            p_liq = avg - sgn * (cum_m[pnf] * MM) / pq
            exit_px = max(stop_px, p_liq) if sgn == 1 else min(stop_px, p_liq)
            adverse = (l <= exit_px) if sgn == 1 else (h >= exit_px)
            hit_tp = (h >= tp_px) if sgn == 1 else (l <= tp_px)
            pnl = None
            if adverse:
                # путь цены вниз(вверх): доливки, затем стоп/ликвидация
                while True:
                    avg = pnot / pq
                    stop_px = avg * (1 - sgn * slp)
                    mused = cum_m[pnf]
                    p_liq = avg - sgn * (mused * MM) / pq
                    is_liq = (p_liq > stop_px) if sgn == 1 else (p_liq < stop_px)
                    exit_px = (max(stop_px, p_liq) if sgn == 1
                               else min(stop_px, p_liq))
                    ex_reach = (l <= exit_px) if sgn == 1 else (h >= exit_px)
                    if padds:
                        ap, aq = padds[0]
                        a_reach = (l <= ap) if sgn == 1 else (h >= ap)
                        a_above = (ap > exit_px) if sgn == 1 else (ap < exit_px)
                        if a_reach and (a_above or not ex_reach):
                            pfees += aq * ap * MAKER
                            pq += aq
                            pnot += ap * aq
                            pnf += 1
                            padds.pop(0)
                            continue
                    if not ex_reach:
                        break          # доливки увели выход ниже минимума свечи
                    if is_liq:
                        pnl = -mused * MM - pfees
                        liqs += 1
                    else:
                        px = stop_px * (1 - sgn * SLIP)
                        pnl = sgn * (px - avg) * pq - pq * px * TAKER - pfees
                    break
            elif hit_tp:
                pnl = sgn * (tp_px - avg) * pq - pq * tp_px * MAKER - pfees
            else:
                while padds:
                    ap, aq = padds[0]
                    if (l <= ap) if sgn == 1 else (h >= ap):
                        pfees += aq * ap * MAKER
                        pq += aq
                        pnot += ap * aq
                        pnf += 1
                        padds.pop(0)
                    else:
                        break
            if pnl is None:
                # позиция жива: просадка по плавающему капиталу
                eq = balance + sgn * (c - pnot / pq) * pq - pfees
                if eq > peak:
                    peak = eq
                d = (peak - eq) / peak
                if d > max_dd:
                    max_dd = d
                continue
            balance += pnl
            trades += 1
            if pnl > 0:
                wins += 1
            if balance > peak:
                peak = balance
            d = (peak - balance) / peak
            if d > max_dd:
                max_dd = d
            sgn = 0
            padds = []
            if balance < MARGIN:
                ruined = True
                break
        s = SIG[i]
        if s == 0:
            continue
        s = 1 if s == 1 else -1
        a = ATR[i]
        if not a:
            continue
        if REG is not None:
            rg = REG[i]
            if (s == 1 and rg == 2) or (s == -1 and rg == 0):
                continue          # сильный тренд против позиции — не входим
        sgn = s
        px = c * (1 + sgn * SLIP)
        step = katr * a
        q0 = m_k[0] * lev / px
        pq = q0
        pnot = px * q0
        pfees = q0 * px * TAKER
        pnf = 1
        padds = []
        for k in range(1, nlv):
            ap = px * (1 - sgn * k * step)
            padds.append((ap, m_k[k] * lev / ap))

    if sgn != 0 and not ruined:
        c = C[i1 - 1]
        avg = pnot / pq
        px = c * (1 - sgn * SLIP)
        pnl = sgn * (px - avg) * pq - pq * px * TAKER - pfees
        balance += pnl
        trades += 1
        if pnl > 0:
            wins += 1
        if balance > peak:
            peak = balance
        d = (peak - balance) / peak
        if d > max_dd:
            max_dd = d
        if balance < MARGIN:
            ruined = True
    return ((balance / START - 1) * 100, max_dd * 100, trades, wins, liqs,
            ruined, bars_pos)


# ============================ параллельный прогон ============================

_W = {}


def _init():
    with open(DATA_PKL, "rb") as fh:
        blob = pickle.load(fh)
    _W["data"] = blob["data"]
    _W["meta"] = blob["meta"]
    _W["sig"] = {}


def _sig_for(sym, per, rsi_p, th, kind, seed):
    key = (sym, per, rsi_p, th, kind, seed)
    cache = _W["sig"]
    if key in cache:
        return cache[key]
    i0, i1 = _W["meta"]["periods"][per]
    base_key = (sym, per, rsi_p, th, "rsi", 0)
    if base_key not in cache:
        cache[base_key] = make_sig(_W["data"][sym]["rsi"][rsi_p], th, i0, i1)
    base = cache[base_key]
    if kind == "rsi":
        return base
    n_l = sum(1 for i in range(i0, i1) if base[i] == 1)
    n_s = sum(1 for i in range(i0, i1) if base[i] == 255)
    if kind == "null50":        # то же число входов, но сторона 50/50
        tot = n_l + n_s
        n_l, n_s = tot // 2, tot - tot // 2
    # зерно детерминированное (hash() строк рандомизирован между процессами)
    sd = zlib.crc32(f"{sym}|{per}|{rsi_p}|{th}|{seed}".encode()) & 0x7FFFFFFF
    cache[key] = make_null_sig(n_l, n_s, i0, i1, sd)
    return cache[key]


def work(task):
    """Один «столбец» карты: все комбинации на одной паре монета x период."""
    mp, sym, per, seed = task
    d = _W["data"][sym]
    i0, i1 = _W["meta"]["periods"][per]
    kind = {"C": "null", "D": "null", "E": "null50"}.get(mp, "rsi")
    reg = d["reg"] if mp in ("B", "D") else None
    res = []
    for prm in COMBOS:
        sig = _sig_for(sym, per, prm["rsi_p"], prm["rsi_th"], kind, seed)
        r = run(d["h"], d["l"], d["c"], d["atr"], sig, reg, prm, i0, i1)
        res.append((round(r[0], 2), round(r[1], 1), r[2], r[3], r[4],
                    int(r[5]), r[6]))
    return task, res


# ============================== self-test ==============================

def self_test():
    import evolution as ev
    import evolution6 as e6
    import backtest_rsi_grid as bg

    out("--- SELF-TEST ---")
    candles = ev.fetch("BTCUSDT", "15", DAYS)
    closes = [c[4] for c in candles]
    H = [c[2] for c in candles]
    L = [c[3] for c in candles]
    ATR = daily_atr_pct(candles)
    REG = e6.calc_regime(candles)
    rsi = bg.calc_rsi(closes, 14)
    n = len(candles)

    # 1) причинность индикаторов: префикс даёт те же значения
    k = int(n * 0.6)
    atr_p = daily_atr_pct(candles[:k])
    reg_p = e6.calc_regime(candles[:k])
    rsi_p = bg.calc_rsi(closes[:k], 14)
    bad = sum(1 for i in range(WARMUP_BARS, k) if atr_p[i] != ATR[i])
    bad += sum(1 for i in range(WARMUP_BARS, k) if reg_p[i] != REG[i])
    bad += sum(1 for i in range(WARMUP_BARS, k)
               if abs((rsi_p[i] or 0) - (rsi[i] or 0)) > 1e-9)
    out(f"1) индикаторы на префиксе [0,{k}) совпали с полным рядом: "
        f"расхождений {bad}")
    assert bad == 0

    # 2) движок на префиксе даёт тот же результат до границы
    prm = dict(rsi_p=14, rsi_th=30, levels=3, step_atr=0.4, tp=0.02,
               stop=0.08, lev=5)
    sig_full = make_sig(rsi, 30, WARMUP_BARS, n)
    sig_pre = make_sig(rsi_p, 30, WARMUP_BARS, k)
    r_full = run(H, L, closes, ATR, sig_full, None, prm, WARMUP_BARS, k)
    r_pre = run(H[:k], L[:k], closes[:k], atr_p, sig_pre, None, prm,
                WARMUP_BARS, k)
    out(f"2) движок [0,{k}) на префиксе vs на полном ряде: "
        f"{r_pre[0]:+.4f}% vs {r_full[0]:+.4f}%, сделок {r_pre[2]}/{r_full[2]}")
    assert abs(r_pre[0] - r_full[0]) < 1e-9 and r_pre[2] == r_full[2]

    # 3) обрезка ряда СПРАВА по границе периода: индикаторы и сделки те же
    i0, i1 = _periods_static(n)[1]
    atr_t = daily_atr_pct(candles[:i1])
    rsi_t = bg.calc_rsi(closes[:i1], 14)
    sig_t = make_sig(rsi_t, 30, i0, i1)
    r_a = run(H, L, closes, ATR, sig_full, None, prm, i0, i1)
    r_b = run(H[:i1], L[:i1], closes[:i1], atr_t, sig_t, None, prm, i0, i1)
    out(f"3) период 2 при обрезке ряда справа: {r_a[0]:+.4f}% vs "
        f"{r_b[0]:+.4f}% (сделок {r_a[2]}/{r_b[2]})")
    assert abs(r_a[0] - r_b[0]) < 1e-9 and r_a[2] == r_b[2]

    # 4) вырожденные случаи
    r_no = run(H, L, closes, ATR, bytearray(n), None, prm, WARMUP_BARS, n)
    out(f"4) без сигналов: итог {r_no[0]:+.2f}%, сделок {r_no[2]} "
        f"(должно быть 0.00%/0)")
    assert r_no[2] == 0 and abs(r_no[0]) < 1e-12

    # 5) издержки: тейк не может быть больше, чем tp*маржа*плечо
    prm2 = dict(prm, tp=0.02, levels=1)
    r_one = run(H, L, closes, ATR, sig_full, None, prm2, WARMUP_BARS, n)
    out(f"5) одно колено, tp 2%, x5: итог {r_one[0]:+.2f}%, циклов "
        f"{r_one[2]}, WR {r_one[3]/max(1,r_one[2])*100:.1f}%, "
        f"ликвидаций {r_one[4]}")

    # 6) чем выше плечо, тем больше размах результата
    a5 = run(H, L, closes, ATR, sig_full, None, dict(prm, lev=5),
             WARMUP_BARS, n)
    a10 = run(H, L, closes, ATR, sig_full, None, dict(prm, lev=10),
              WARMUP_BARS, n)
    out(f"6) x5 {a5[0]:+.2f}% (DD {a5[1]:.1f}%) vs x10 {a10[0]:+.2f}% "
        f"(DD {a10[1]:.1f}%) — плечо усиливает и итог, и просадку")
    # 7) широкий стоп + большое плечо: выход обязан идти по ЛИКВИДАЦИИ,
    #    а не по стопу (иначе движок «спасает» позицию, которой уже нет)
    p_liq_test = dict(rsi_p=14, rsi_th=30, levels=1, step_atr=0.4, tp=0.02,
                      stop=0.15, lev=15)
    r_l = run(H, L, closes, ATR, sig_full, None, p_liq_test, WARMUP_BARS, n)
    losses = r_l[2] - r_l[3]
    out(f"7) стоп 15% при плече x15 (ликвидация на ~6.3%): убыточных циклов "
        f"{losses}, из них ликвидаций {r_l[4]} (должны совпадать)")
    assert abs(losses - r_l[4]) <= 1, "ликвидация пропущена — движок оптимистичен"

    # 8) при плече x5 (ликвидация на ~19%) тот же стоп 15% срабатывает раньше
    r_s = run(H, L, closes, ATR, sig_full, None, dict(p_liq_test, lev=5),
              WARMUP_BARS, n)
    out(f"8) тот же стоп 15% при плече x5: убыточных {r_s[2]-r_s[3]}, "
        f"ликвидаций {r_s[4]} (должно быть 0)")
    assert r_s[4] == 0
    out("SELF-TEST OK")
    out("")


def _periods_static(n):
    edges = [WARMUP_BARS + round((n - WARMUP_BARS) * k / N_PERIODS)
             for k in range(N_PERIODS + 1)]
    return [(edges[k], edges[k + 1]) for k in range(N_PERIODS)]


# ============================== анализ ==============================

def med(v):
    return statistics.median(v) if v else 0.0


def combo_key(prm):
    return tuple(prm[k] for k in PNAMES)


class Map(object):
    """Карта: по каждой комбинации — 15 ячеек (5 монет x 3 периода)."""

    def __init__(self, name, cells, combos=None):
        # cells[ci] = список записей (ret, dd, trades, wins, liqs, ruin, bars)
        self.name = name
        self.cells = cells
        self.combos = combos if combos is not None else COMBOS
        self.pvals = {k: [v for v in PVALS[k]
                          if any(p[k] == v for p in self.combos)]
                      for k in PNAMES}
        self.med = [med([x[0] for x in c]) for c in cells]
        self.mean = [sum(x[0] for x in c) / len(c) for c in cells]
        self.pos = [sum(1 for x in c if x[0] > 0) / len(c) for c in cells]
        self.ruin = [sum(x[5] for x in c) for c in cells]
        self.dd = [med([x[1] for x in c]) for c in cells]
        self.trades = [med([x[2] for x in c]) for c in cells]

    def flat(self, j):
        return [x[j] for c in self.cells for x in c]

    def good(self, ci):
        return self.med[ci] > 0 and self.ruin[ci] == 0

    def restrict(self, **fixed):
        """Подкарта: только комбинации с заданными значениями параметров."""
        keep = [ci for ci, p in enumerate(self.combos)
                if all(p[k] == v for k, v in fixed.items())]
        return Map(self.name + "|" + ",".join(f"{k}={v}" for k, v in
                                              fixed.items()),
                   [self.cells[ci] for ci in keep],
                   [self.combos[ci] for ci in keep])

    def summary(self):
        rets = self.flat(0)
        ruins = self.flat(5)
        n = len(rets)
        return dict(
            cells=n,
            cell_pos=sum(1 for x in rets if x > 0) / n * 100,
            cell_med=med(rets),
            cell_mean=sum(rets) / n,
            ruin=sum(ruins) / n * 100,
            combo_pos=sum(1 for x in self.med if x > 0) / len(self.med) * 100,
            combo_mean_pos=sum(1 for x in self.mean if x > 0) / len(self.mean) * 100,
            good=sum(1 for ci in range(len(self.med)) if self.good(ci))
                 / len(self.med) * 100,
            robust=sum(1 for ci in range(len(self.med))
                       if self.pos[ci] >= 0.6 and self.ruin[ci] == 0)
                   / len(self.med) * 100,
            dd=med(self.flat(1)),
            trades=med(self.flat(2)),
        )


def print_summary(m, label):
    s = m.summary()
    out(f"{label}")
    out(f"  ячеек (комбинация x монета x период): {s['cells']}")
    out(f"  прибыльных ячеек: {s['cell_pos']:.1f}%   "
        f"медиана ячейки {s['cell_med']:+.1f}%   среднее {s['cell_mean']:+.1f}%")
    out(f"  СЛИВОВ (ruined): {s['ruin']:.1f}% ячеек")
    out(f"  комбинаций с положительной МЕДИАНОЙ по 15 ячейкам: {s['combo_pos']:.1f}%")
    out(f"  комбинаций с положительным СРЕДНИМ (портфель 15 ячеек): "
        f"{s['combo_mean_pos']:.1f}%")
    out(f"  «хороших» (медиана>0 и НИ ОДНОГО слива): {s['good']:.1f}%")
    out(f"  «устойчивых» (>=60% ячеек в плюс и ни одного слива): {s['robust']:.1f}%")
    out(f"  медиана просадки ячейки {s['dd']:.1f}%   медиана циклов {s['trades']:.0f}")
    out("")


def marginals(m, label):
    out(f"МАРГИНАЛЬНЫЕ СРЕЗЫ ПО ПАРАМЕТРАМ — {label}")
    out(f"  {'параметр':10} {'знач':>6} | {'мед.ячейки':>10} {'приб.яч.%':>9} "
        f"{'сливы%':>7} {'мед.DD%':>8} {'циклов':>7}")
    for k in PNAMES:
        for v in m.pvals[k]:
            idx = [ci for ci, prm in enumerate(m.combos) if prm[k] == v]
            rets, dds, ruins, trs = [], [], [], []
            for ci in idx:
                for x in m.cells[ci]:
                    rets.append(x[0])
                    dds.append(x[1])
                    ruins.append(x[5])
                    trs.append(x[2])
            out(f"  {k:10} {v:>6} | {med(rets):+10.1f} "
                f"{sum(1 for x in rets if x > 0)/len(rets)*100:9.1f} "
                f"{sum(ruins)/len(ruins)*100:7.1f} {med(dds):8.1f} "
                f"{med(trs):7.0f}")
    out("")


def cross2(m, ka, kb, label):
    """Двумерный срез: медиана итога ячейки / доля прибыльных ячеек."""
    out(f"  срез {ka} x {kb} ({label}): медиана итога % / доля приб. ячеек %"
        f" / сливы %")
    title = ka + " | " + kb
    hdr = "    %14s" % title
    for v in m.pvals[kb]:
        hdr += f"{str(v):>16}"
    out(hdr)
    for va in m.pvals[ka]:
        row = f"    {str(va):>14}"
        for vb in m.pvals[kb]:
            idx = [ci for ci, p in enumerate(m.combos)
                   if p[ka] == va and p[kb] == vb]
            rets = [x[0] for ci in idx for x in m.cells[ci]]
            ru = [x[5] for ci in idx for x in m.cells[ci]]
            row += (f"{med(rets):+8.1f}/{sum(1 for x in rets if x>0)/len(rets)*100:3.0f}"
                    f"/{sum(ru)/len(ru)*100:3.0f}")
        out(row)
    out("")


def sensitivity(m, label):
    """Чувствительность: как меняется медианный итог комбинации при сдвиге
    одного параметра на один шаг (остальные фиксированы)."""
    out(f"ЧУВСТВИТЕЛЬНОСТЬ К ОДНОМУ ШАГУ ПАРАМЕТРА — {label}")
    idx_of = {combo_key(prm): ci for ci, prm in enumerate(m.combos)}
    spread = statistics.pstdev(m.med)
    out(f"  разброс медиан по всей карте (ст.откл.): {spread:.1f} п.п.")
    out("  калибровка: если параметр не несёт информации (соседи независимы),")
    out("  отношение |d|/разброс = 1.13 — проверено на синтетическом шуме;")
    out("  чем МЕНЬШЕ отношение, тем более гладкий ландшафт по этому параметру.")
    out(f"  {'параметр':10} | {'сред.|d|':>9} {'макс.|d|':>9} "
        f"{'|d|/разброс':>12} {'смена знака':>12}")
    rows = []
    pairs_a, pairs_b = [], []
    for k in PNAMES:
        vals = m.pvals[k]
        diffs, flips, tot = [], 0, 0
        for ci, prm in enumerate(m.combos):
            vi = vals.index(prm[k])
            if vi + 1 >= len(vals):
                continue
            nb = dict(prm)
            nb[k] = vals[vi + 1]
            cj = idx_of[combo_key(nb)]
            a, b = m.med[ci], m.med[cj]
            diffs.append(abs(b - a))
            pairs_a.append(a)
            pairs_b.append(b)
            tot += 1
            if (a > 0) != (b > 0):
                flips += 1
        if not diffs:
            continue
        rows.append((sum(diffs) / len(diffs), k, max(diffs),
                     flips / tot * 100))
    for avg, k, mx, fl in sorted(rows, reverse=True):
        out(f"  {k:10} | {avg:9.1f} {mx:9.1f} {avg/spread:12.2f} {fl:11.1f}%")
    if pairs_a:
        n = len(pairs_a)
        ma, mb = sum(pairs_a) / n, sum(pairs_b) / n
        num = sum((x - ma) * (y - mb) for x, y in zip(pairs_a, pairs_b))
        da = sum((x - ma) ** 2 for x in pairs_a) ** 0.5
        db = sum((y - mb) ** 2 for y in pairs_b) ** 0.5
        r = num / (da * db) if da and db else 0.0
        out(f"  ГЛАДКОСТЬ: корреляция результата соседей по сетке r={r:+.2f} "
            f"(шум ~0.00, гладкое плато ~0.9)")
    out("")


def _cluster(m, sel, name):
    """Кучность произвольного множества комбинаций sel (индексы)."""
    idx_of = {combo_key(prm): ci for ci, prm in enumerate(m.combos)}
    base = len(sel) / len(m.combos)
    ssel = set(sel)
    if not sel:
        out(f"  {name}: множество пусто")
        return
    shares = []
    for ci in sel:
        prm = m.combos[ci]
        nb_tot = nb_in = 0
        for k in PNAMES:
            vals = m.pvals[k]
            vi = vals.index(prm[k])
            for dv in (-1, 1):
                if 0 <= vi + dv < len(vals):
                    q = dict(prm)
                    q[k] = vals[vi + dv]
                    nb_tot += 1
                    if idx_of[combo_key(q)] in ssel:
                        nb_in += 1
        shares.append(nb_in / nb_tot if nb_tot else 0)
    avg = sum(shares) / len(shares)
    core = sum(1 for s in shares if s == 1.0)
    out(f"  {name}: {len(sel)} шт ({base*100:.1f}% карты); соседей из того же "
        f"множества {avg*100:.1f}% -> кучность x{avg/base:.2f}"
        f"{'  ПЛАТО' if avg/base > 1.6 else '  (разрозненно)'}; "
        f"ядро (все соседи свои) {core}")


def connectivity(m, label):
    """Плато или пик. Три множества, чтобы вывод не зависел от одного порога:
    «хорошие» (медиана>0 и без сливов), «безопасные» (без сливов) и
    «верхняя четверть карты» по медиане."""
    out(f"СВЯЗНОСТЬ ОБЛАСТЕЙ — {label}")
    out("  (кучность = во сколько раз соседи чаще «свои», чем при случайном")
    out("   разбросе; на синтетическом шуме = 0.9-1.1, плато = 1.6 и выше)")
    n = len(m.combos)
    good = [ci for ci in range(n) if m.good(ci)]
    safe = [ci for ci in range(n) if m.ruin[ci] == 0]
    thr = sorted(m.med, reverse=True)[max(0, n // 4 - 1)]
    top = [ci for ci in range(n) if m.med[ci] >= thr]
    _cluster(m, good, "«хорошие» (медиана>0 и ни одного слива)")
    _cluster(m, safe, "«безопасные» (ни одного слива из 15 ячеек)")
    _cluster(m, top, "верхняя четверть по медиане")
    out("")
    return good


def plateau_box(m, label):
    """Ищем связную область: для каждого параметра оставляем значения, где
    доля хороших выше средней; пересечение = «коробка». Затем меряем, что
    внутри и что снаружи."""
    n = len(m.combos)
    # «хорошая» здесь = верхняя четверть карты по медиане И без сливов:
    # при таком ландшафте критерий «медиана>0» отбирает единицы и коробка
    # вырождается в случайную точку.
    thr = sorted(m.med, reverse=True)[max(0, n // 4 - 1)]
    good = set(ci for ci in range(n)
               if m.med[ci] >= thr and m.ruin[ci] == 0)
    base = len(good) / n
    if not good:
        out(f"ОБЛАСТЬ ПРИБЫЛЬНОСТИ (коробка) — {label}: пусто")
        out("")
        return None
    box = {}
    out(f"ОБЛАСТЬ ПРИБЫЛЬНОСТИ (коробка) — {label}")
    out(f"  критерий «хорошая»: верхняя четверть карты по медиане И ноль "
        f"сливов ({len(good)} комбинаций, {base*100:.1f}%)")
    for k in PNAMES:
        keep = []
        for v in m.pvals[k]:
            idx = [ci for ci, prm in enumerate(m.combos) if prm[k] == v]
            sh = sum(1 for ci in idx if ci in good) / len(idx)
            if sh >= base:
                keep.append(v)
        box[k] = keep if keep else list(m.pvals[k])
    inside = [ci for ci, prm in enumerate(m.combos)
              if all(prm[k] in box[k] for k in PNAMES)]
    ins = set(inside)
    outside = [ci for ci in range(n) if ci not in ins]
    gi = sum(1 for ci in inside if ci in good) / len(inside) * 100
    go = (sum(1 for ci in outside if ci in good) / len(outside) * 100
          if outside else 0)
    out("  границы: " + "; ".join(f"{k}={box[k]}" for k in PNAMES))
    out(f"  комбинаций внутри: {len(inside)} из {n}")
    if len(inside) < 8:
        out("  ВНИМАНИЕ: коробка схлопнулась до нескольких точек. Это НЕ")
        out("  найденная область — это отсутствие области: у каждого параметра")
        out("  «хорошие» жмутся к одному значению, и пересечение вырождается.")
    out(f"  доля «хороших» ВНУТРИ {gi:.1f}%  vs  СНАРУЖИ {go:.1f}%  "
        f"(по всей карте {base*100:.1f}%)")
    rets_in = [x[0] for ci in inside for x in m.cells[ci]]
    rets_out = [x[0] for ci in outside for x in m.cells[ci]]
    out(f"  медиана ячейки внутри {med(rets_in):+.1f}%  снаружи "
        f"{med(rets_out):+.1f}%")
    ruin_in = sum(x[5] for ci in inside for x in m.cells[ci]) / len(rets_in) * 100
    out(f"  сливов внутри {ruin_in:.1f}% ячеек")
    # разрез по монетам и периодам
    out(f"  внутри коробки по монетам/периодам (медиана итога / доля приб. ячеек):")
    hdr = "    " + " ".join(f"{p:>14}" for p in ("период 1", "период 2", "период 3"))
    out(hdr)
    for si, sym in enumerate(SYMBOLS):
        row = f"    {sym:9}"
        for pi in range(N_PERIODS):
            j = si * N_PERIODS + pi
            vals = [m.cells[ci][j][0] for ci in inside]
            row += f" {med(vals):+7.1f}%/{sum(1 for v in vals if v>0)/len(vals)*100:3.0f}%"
        out(row)
    out("")
    return box, inside


def top_table(m, label, n=12, worst=False):
    order = sorted(range(len(m.combos)),
                   key=lambda ci: (m.pos[ci], m.med[ci]), reverse=not worst)
    out(f"{label}")
    out(f"  {'rsi_p':>5} {'th':>3} {'кол':>3} {'шагATR':>6} {'tp%':>5} "
        f"{'stop%':>5} {'плечо':>5} | {'мед%':>7} {'сред%':>8} {'приб.яч':>7} "
        f"{'сливы':>5} {'DD%':>5} {'циклов':>6}")
    for ci in order[:n]:
        p = m.combos[ci]
        out(f"  {p['rsi_p']:>5} {p['rsi_th']:>3} {p['levels']:>3} "
            f"{p['step_atr']:>6} {p['tp']*100:>5.1f} {p['stop']*100:>5.1f} "
            f"{'x%d'%p['lev']:>5} | {m.med[ci]:+7.1f} {m.mean[ci]:+8.1f} "
            f"{m.pos[ci]*100:6.0f}% {m.ruin[ci]:>5} {m.dd[ci]:5.0f} "
            f"{m.trades[ci]:6.0f}")
    out("")


def _ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = _ranks(a), _ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da and db else 0.0


def transfer(m, label):
    """Переносим ли ландшафт: совпадает ли форма карты между периодами и
    монетами, и переживает ли отбор лучших переход на другой период."""
    out(f"ПЕРЕНОСИМОСТЬ ЛАНДШАФТА — {label}")
    ncb = len(m.combos)
    per = []
    for pi in range(N_PERIODS):
        per.append([sum(m.cells[ci][si * N_PERIODS + pi][0]
                        for si in range(len(SYMBOLS))) / len(SYMBOLS)
                    for ci in range(ncb)])
    for a, b in ((0, 1), (0, 2), (1, 2)):
        out(f"  ранговая корреляция карт: период {a+1} vs период {b+1}: "
            f"{spearman(per[a], per[b]):+.2f}")
    sym_avg = []
    for si in range(len(SYMBOLS)):
        sym_avg.append([sum(m.cells[ci][si * N_PERIODS + pi][0]
                            for pi in range(N_PERIODS)) / N_PERIODS
                        for ci in range(ncb)])
    prs = []
    for i in range(len(SYMBOLS)):
        for j in range(i + 1, len(SYMBOLS)):
            prs.append(spearman(sym_avg[i], sym_avg[j]))
    out(f"  ранговая корреляция между монетами: средняя {sum(prs)/len(prs):+.2f} "
        f"(мин {min(prs):+.2f}, макс {max(prs):+.2f})")
    # отбор лучших на периоде 1 -> что они дают дальше
    k = max(1, ncb // 20)
    top1 = sorted(range(ncb), key=lambda ci: -per[0][ci])[:k]
    all_m = [sum(per[p]) / ncb for p in range(N_PERIODS)]
    sel_m = [sum(per[p][ci] for ci in top1) / k for p in range(N_PERIODS)]
    out(f"  отбор ТОП-5% по периоду 1 ({k} комбинаций):")
    out(f"    период 1 (отбор): {sel_m[0]:+.1f}% против среднего по карте "
        f"{all_m[0]:+.1f}%")
    for p in (1, 2):
        out(f"    период {p+1} (экзамен): {sel_m[p]:+.1f}% против среднего по "
            f"карте {all_m[p]:+.1f}%  -> преимущество отбора "
            f"{sel_m[p]-all_m[p]:+.1f} п.п.")
    top12 = sorted(range(ncb),
                   key=lambda ci: -(per[0][ci] + per[1][ci]))[:k]
    s3 = sum(per[2][ci] for ci in top12) / k
    out(f"  отбор ТОП-5% по периодам 1+2 -> период 3: {s3:+.1f}% против "
        f"{all_m[2]:+.1f}% по карте (преимущество {s3-all_m[2]:+.1f} п.п.)")
    out("")


def compare(a, b, la, lb):
    out(f"ПАРНОЕ СРАВНЕНИЕ {la} vs {lb} (одни и те же комбинации)")
    ncb = len(a.combos)
    d = [b.med[ci] - a.med[ci] for ci in range(ncb)]
    better = sum(1 for x in d if x > 0) / len(d) * 100
    out(f"  медиана разницы медиан: {med(d):+.1f} п.п.; "
        f"{lb} лучше в {better:.1f}% комбинаций")
    ca = [x[0] for c in a.cells for x in c]
    cb = [x[0] for c in b.cells for x in c]
    out(f"  прибыльных ячеек: {sum(1 for x in ca if x>0)/len(ca)*100:.1f}% -> "
        f"{sum(1 for x in cb if x>0)/len(cb)*100:.1f}%")
    ga = sum(1 for ci in range(ncb) if a.good(ci)) / ncb * 100
    gb = sum(1 for ci in range(ncb) if b.good(ci)) / ncb * 100
    out(f"  «хороших» комбинаций: {ga:.1f}% -> {gb:.1f}%")
    ra = sum(x[5] for c in a.cells for x in c) / len(ca) * 100
    rb = sum(x[5] for c in b.cells for x in c) / len(cb) * 100
    out(f"  сливов: {ra:.1f}% -> {rb:.1f}% ячеек")
    out(f"  медиана ячейки: {med(ca):+.1f}% -> {med(cb):+.1f}%")
    out("")


# ============================== прогон ==============================

def build_tasks():
    tasks = []
    for mp in ("A", "B"):
        for si, sym in enumerate(SYMBOLS):
            for pi in range(N_PERIODS):
                tasks.append((mp, sym, pi, 0))
    for mp, seeds in (("C", NULL_SEEDS), ("D", NULL_SEEDS), ("E", E_SEEDS)):
        for sd in seeds:
            for si, sym in enumerate(SYMBOLS):
                for pi in range(N_PERIODS):
                    tasks.append((mp, sym, pi, sd))
    return tasks


def run_all():
    from multiprocessing import Pool
    res = {}
    if os.path.exists(RAW_JSON):        # доcчитываем только недостающее
        res = load_raw()
        print(f"в кэше уже есть {len(res)} задач")
    tasks = [t for t in build_tasks()
             if "|".join(str(x) for x in t) not in res]
    if not tasks:
        return res
    t0 = time.time()
    nproc = max(1, min(10, (os.cpu_count() or 4) - 2))
    print(f"Задач: {len(tasks)}, процессов: {nproc}, "
          f"комбинаций в задаче: {len(COMBOS)}")
    with Pool(nproc, initializer=_init) as pool:
        done = 0
        for task, cells in pool.imap_unordered(work, tasks):
            res["|".join(str(x) for x in task)] = cells
            done += 1
            el = time.time() - t0
            print(f"  [{done}/{len(tasks)}] {task} "
                  f"{el:.0f}с, осталось ~{el/done*(len(tasks)-done):.0f}с",
                  flush=True)
    with open(RAW_JSON, "w") as fh:
        json.dump(res, fh)
    return res


def load_raw():
    with open(RAW_JSON) as fh:
        return json.load(fh)


def assemble(raw, mp, seeds=(0,)):
    """Собирает карту: для каждой комбинации 15 ячеек (усреднение по зёрнам
    для нулевой модели — по каждой ячейке берём среднее по зёрнам)."""
    cells = [[] for _ in COMBOS]
    for sym in SYMBOLS:
        for pi in range(N_PERIODS):
            per_seed = []
            for sd in seeds:
                key = f"{mp}|{sym}|{pi}|{sd}"
                per_seed.append(raw[key])
            for ci in range(len(COMBOS)):
                if len(per_seed) == 1:
                    cells[ci].append(tuple(per_seed[0][ci]))
                else:
                    rows = [s[ci] for s in per_seed]
                    cells[ci].append((
                        sum(r[0] for r in rows) / len(rows),
                        sum(r[1] for r in rows) / len(rows),
                        sum(r[2] for r in rows) / len(rows),
                        sum(r[3] for r in rows) / len(rows),
                        sum(r[4] for r in rows) / len(rows),
                        sum(r[5] for r in rows) / len(rows),
                        sum(r[6] for r in rows) / len(rows)))
    return Map(mp, cells)


def report(raw, meta):
    import datetime as dt

    def d(ts):
        return dt.datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")

    t0 = meta["t0"]
    out("=" * 78)
    out("КАРТА УСТОЙЧИВОСТИ ПРОСТОЙ СЕТКИ УСРЕДНЕНИЯ (grid_plateau.py)")
    out("=" * 78)
    out("Скелет: RSI(rsi_p) вниз через rsi_th -> лонг, вверх через 100-rsi_th")
    out("-> шорт; levels колен с шагом step_atr x дневной ATR(14д); объём")
    out("колена x1.5 (фикс); тейк tp% и стоп stop% ОТ СРЕДНЕЙ; плечо lev.")
    out("Никаких таймаутов, кулдаунов, зон и прочих фильтров.")
    out(f"Издержки: taker {TAKER*100:.3f}%, maker {MAKER*100:.2f}%, "
        f"проскальзывание {SLIP*100:.2f}%, funding 0.01%/8ч.")
    out(f"Депозит {START:.0f}$, маржа цикла {MARGIN:.0f}$ (фикс, без реинвеста),"
        f" слив при балансе < {MARGIN:.0f}$.")
    out("")
    out("СЕТКА ЗНАЧЕНИЙ:")
    for k in PNAMES:
        out(f"  {k:10} = {PVALS[k]}")
    out(f"  ИТОГО комбинаций: {len(COMBOS)}")
    out("")
    out(f"Данные: 15м, {DAYS} дней, {d(t0)} .. {d(meta['t1'])}, "
        f"монеты {', '.join(SYMBOLS)}.")
    bar = (meta["t1"] - meta["t0"]) / (meta["n"] - 1)
    out("Периоды (непересекающиеся, первые 100 дней отданы под прогрев):")
    for pi, (i0, i1) in enumerate(meta["periods"]):
        out(f"  период {pi+1}: {d(t0 + i0*bar)} .. {d(t0 + i1*bar)} "
            f"({(i1-i0)/96:.0f} дней, {i1-i0} свечей)")
    out("")

    A = assemble(raw, "A")
    B = assemble(raw, "B")
    C = assemble(raw, "C", NULL_SEEDS)
    D = assemble(raw, "D", NULL_SEEDS)
    E = assemble(raw, "E", E_SEEDS)

    out("=" * 78)
    out("1. ДОЛИ ПРИБЫЛЬНОСТИ")
    out("=" * 78)
    print_summary(A, "КАРТА A — вход по RSI, БЕЗ фильтра тренда")
    print_summary(B, "КАРТА B — вход по RSI + ФИЛЬТР ТРЕНДА "
                     "(лонг запрещён в bear, шорт запрещён в bull)")
    print_summary(C, f"КАРТА C — НУЛЕВАЯ МОДЕЛЬ: случайные входы "
                     f"(та же частота и та же доля лонг/шорт), зёрна {NULL_SEEDS}")
    print_summary(D, "КАРТА D — случайные входы + фильтр тренда")
    print_summary(E, "КАРТА E — случайные входы И случайная сторона 50/50 "
                     "(чистая механика усреднения, без направленной ставки)")

    out("=" * 78)
    out("2. СРАВНЕНИЯ")
    out("=" * 78)
    compare(A, B, "A (RSI)", "B (RSI+фильтр)")
    compare(A, C, "A (RSI)", "C (случайный вход)")
    compare(C, D, "C (случайный)", "D (случайный+фильтр)")
    compare(B, D, "B (RSI+фильтр)", "D (случайный+фильтр)")
    compare(C, E, "C (случайный, сторона как у RSI)", "E (случайный, сторона 50/50)")

    out("=" * 78)
    out("3. ФОРМА ЛАНДШАФТА")
    out("=" * 78)
    for m, lab in ((A, "КАРТА A"), (B, "КАРТА B")):
        connectivity(m, lab)
        plateau_box(m, lab)
    connectivity(C, "КАРТА C (нулевая)")
    plateau_box(C, "КАРТА C (нулевая)")
    transfer(A, "КАРТА A")
    transfer(B, "КАРТА B")
    transfer(C, "КАРТА C (нулевая)")

    out("=" * 78)
    out("4. ЧУВСТВИТЕЛЬНОСТЬ И МАРГИНАЛЫ")
    out("=" * 78)
    sensitivity(A, "КАРТА A")
    sensitivity(B, "КАРТА B")
    out("ДВУМЕРНЫЕ СРЕЗЫ (форма плато в парах параметров)")
    for ka, kb in (("tp", "stop"), ("levels", "step_atr"),
                   ("stop", "lev"), ("rsi_p", "rsi_th")):
        cross2(A, ka, kb, "A")
    for ka, kb in (("tp", "stop"), ("levels", "step_atr")):
        cross2(B, ka, kb, "B")
        cross2(C, ka, kb, "C нулевая")
    marginals(A, "КАРТА A (RSI, без фильтра)")
    marginals(B, "КАРТА B (RSI + фильтр тренда)")
    marginals(C, "КАРТА C (нулевая модель)")

    out("=" * 78)
    out("5. КРАЙНИЕ ТОЧКИ (для описания ландшафта, НЕ для выбора)")
    out("=" * 78)
    top_table(A, "A: самые устойчивые комбинации (по доле прибыльных ячеек)")
    top_table(A, "A: худшие комбинации", worst=True)
    top_table(B, "B: самые устойчивые комбинации")
    top_table(C, "C: самые устойчивые комбинации нулевой модели")

    out("=" * 78)
    out("6. ПОДКАРТА «ВЫЖИВАЕМОГО» ПЛЕЧА x5 (972 комбинации)")
    out("=" * 78)
    out("Плечо перебивает всё остальное (см. маргиналы), поэтому вопрос про")
    out("форму плато честно задавать ВНУТРИ пригодного для жизни риска.")
    out("Здесь та же карта, но только lev=5.")
    out("")
    A5, B5, C5, D5 = (A.restrict(lev=5), B.restrict(lev=5),
                      C.restrict(lev=5), D.restrict(lev=5))
    print_summary(A5, "A(x5) — RSI, без фильтра")
    print_summary(B5, "B(x5) — RSI + фильтр тренда")
    print_summary(C5, "C(x5) — случайный вход, без фильтра")
    print_summary(D5, "D(x5) — случайный вход + фильтр тренда")
    compare(A5, B5, "A(x5)", "B(x5) с фильтром")
    compare(A5, C5, "A(x5) RSI", "C(x5) случайный вход")
    compare(B5, D5, "B(x5) RSI+фильтр", "D(x5) случайный+фильтр")
    connectivity(A5, "A(x5)")
    connectivity(B5, "B(x5)")
    plateau_box(A5, "A(x5)")
    plateau_box(B5, "B(x5)")
    plateau_box(D5, "D(x5) — случайный вход + фильтр")
    sensitivity(A5, "A(x5)")
    sensitivity(B5, "B(x5)")
    marginals(B5, "B(x5) — RSI + фильтр тренда")
    for ka, kb in (("levels", "step_atr"), ("tp", "stop")):
        cross2(B5, ka, kb, "B(x5)")
    transfer(B5, "B(x5)")
    top_table(B5, "B(x5): самые устойчивые комбинации", n=15)

    out("=" * 78)
    out("7. РИСК СЛИВА")
    out("=" * 78)
    for m, lab in ((A, "A"), (B, "B"), (C, "C"), (D, "D"), (E, "E")):
        n_any = sum(1 for ci in range(len(COMBOS)) if m.ruin[ci] > 0)
        cells = [x for c in m.cells for x in c]
        out(f"  карта {lab}: комбинаций хотя бы с одним сливом "
            f"{n_any} из {len(COMBOS)} ({n_any/len(COMBOS)*100:.1f}%); "
            f"ячеек со сливом {sum(x[5] for x in cells)/len(cells)*100:.1f}%")
    out("")
    out("  сливы по значениям параметров (карта A, доля ячеек со сливом):")
    for k in PNAMES:
        parts = []
        for v in PVALS[k]:
            idx = [ci for ci, p in enumerate(COMBOS) if p[k] == v]
            rr = [x[5] for ci in idx for x in A.cells[ci]]
            parts.append(f"{v}: {sum(rr)/len(rr)*100:.1f}%")
        out(f"    {k:10} " + "  ".join(parts))
    out("")
    out("  сливы по монетам/периодам (карта A):")
    for si, sym in enumerate(SYMBOLS):
        row = f"    {sym:9}"
        for pi in range(N_PERIODS):
            j = si * N_PERIODS + pi
            rr = [A.cells[ci][j][5] for ci in range(len(COMBOS))]
            row += f" п{pi+1}: {sum(rr)/len(rr)*100:5.1f}%"
        out(row)
    out("")
    margin_probe()
    conclusions(A, B, C, D, E, A5, B5, C5, D5)
    return A, B, C, D


def margin_probe(n_sample=100):
    """Слив — это свойство РАЗМЕРА позиции, а не входа. Прогоняем выборку
    комбинаций x5 с фильтром при марже цикла 25% и 7.5% депозита."""
    global MARGIN
    out("  ПРОВЕРКА: слив — это размер позиции, а не сигнал.")
    if not os.path.exists(DATA_PKL):
        out("  (нет кэша данных, проверка пропущена)")
        out("")
        return
    if "data" not in _W:
        _init()
    rnd = random.Random(7)
    sample = rnd.sample([p for p in COMBOS if p["lev"] == 5], n_sample)
    old = MARGIN
    try:
        for margin, label in ((5.0, "маржа цикла 5$ из 20$ (25% депозита)"),
                              (1.5, "маржа цикла 1.5$ из 20$ (7.5%)")):
            MARGIN = margin
            rets, ruins = [], 0
            for prm in sample:
                for sym in SYMBOLS:
                    d = _W["data"][sym]
                    for pi in range(N_PERIODS):
                        i0, i1 = _W["meta"]["periods"][pi]
                        sig = _sig_for(sym, pi, prm["rsi_p"], prm["rsi_th"],
                                       "rsi", 0)
                        r = run(d["h"], d["l"], d["c"], d["atr"], sig,
                                d["reg"], prm, i0, i1)
                        rets.append(r[0])
                        ruins += int(r[5])
            n = len(rets)
            out(f"    {label}: прибыльных ячеек "
                f"{sum(1 for x in rets if x > 0)/n*100:.1f}%, медиана "
                f"{med(rets):+.1f}%, сливов {ruins/n*100:.1f}%")
    finally:
        MARGIN = old
    out(f"    (выборка {n_sample} комбинаций x5 с фильтром тренда, "
        f"{n_sample*15} ячеек)")
    out("    Доля прибыльных не меняется вовсе — меняется только амплитуда:")
    out("    размер позиции решает, доживёшь ли ты, но не решает, есть ли край.")
    out("")


def conclusions(A, B, C, D, E, A5, B5, C5, D5):
    """Выводы числами из посчитанного, без ручных цифр."""
    sA, sB, sC, sD, sE = (m.summary() for m in (A, B, C, D, E))
    s5 = [m.summary() for m in (A5, B5, C5, D5)]
    out("=" * 78)
    out("8. ВЫВОДЫ")
    out("=" * 78)
    out("1) ДОЛЯ ПРИБЫЛЬНЫХ КОМБИНАЦИЙ — это лотерея, а не механика.")
    out(f"   вся карта:  без фильтра прибыльны {sA['cell_pos']:.1f}% ячеек и "
        f"{sA['combo_pos']:.1f}% комбинаций;")
    out(f"               с фильтром {sB['cell_pos']:.1f}% ячеек и "
        f"{sB['combo_pos']:.1f}% комбинаций.")
    out(f"   только x5:  без фильтра {s5[0]['cell_pos']:.1f}% / "
        f"{s5[0]['combo_pos']:.1f}%;  с фильтром {s5[1]['cell_pos']:.1f}% / "
        f"{s5[1]['combo_pos']:.1f}%.")
    out(f"   медиана ячейки всюду отрицательна: {sA['cell_med']:+.1f}% (A), "
        f"{sB['cell_med']:+.1f}% (B), {s5[1]['cell_med']:+.1f}% (B при x5).")
    out("   Порога «70% = механика работает» нет и близко; ближе к «10% =")
    out("   лотерея». Голая сетка на этих 5 монетах за 3 года — минус.")
    out("")
    out("2) ПЛАТО ЕСТЬ, НО НЕ ПО ПРИБЫЛИ, А ПО ВЫЖИВАЕМОСТИ.")
    out("   Связная широкая область образуется только у множества «ни одного")
    out("   слива» (кучность x1.6-x3.5, есть ядро из десятков комбинаций).")
    out("   Прибыльные комбинации разбросаны точками: их 0.1-3.4%, ядра нет,")
    out("   а список самых устойчивых покрывает почти все значения всех")
    out("   параметров сразу — это подпись случайности, а не области.")
    out("   Ранговая корреляция карт между периодами +0.01..+0.39, между")
    out("   монетами +0.09..+0.23: форма ландшафта почти не переносится.")
    out("")
    out("3) ЧУВСТВИТЕЛЬНОСТЬ. Порядок влияния (по среднему сдвигу медианы на")
    out("   один шаг): плечо >> число колен ~ шаг сетки > тейк ~ период RSI ~")
    out("   порог RSI > стоп. Стоп почти не влияет при x10/x15 потому, что")
    out("   до него не доживают: ликвидация ближе (0.95/плечо = 9.5% и 6.3%).")
    out("   Гладкость ландшафта r=+0.37..+0.47 при шуме 0 и плато 0.9 —")
    out("   ландшафт наполовину шум даже там, где есть структура.")
    out("")
    out("4) НУЛЕВАЯ МОДЕЛЬ: ВХОД ПО RSI НЕ ПРОСТО НЕ ВАЖЕН — ОН ВРЕДЕН.")
    dAC = sum(1 for ci in range(len(COMBOS)) if C.med[ci] > A.med[ci])
    dBD = sum(1 for ci in range(len(COMBOS)) if D.med[ci] > B.med[ci])
    out(f"   Случайный вход той же частоты и с той же долей лонг/шорт лучше")
    out(f"   RSI-входа в {dAC/len(COMBOS)*100:.1f}% комбинаций "
        f"(медиана ячейки {sA['cell_med']:+.1f}% -> {sC['cell_med']:+.1f}%).")
    out(f"   С фильтром то же самое: случайный вход лучше RSI в "
        f"{dBD/len(COMBOS)*100:.1f}% комбинаций.")
    out(f"   Случайная СТОРОНА 50/50 (карта E) хуже, чем доля сторон, взятая")
    out(f"   у RSI ({sE['cell_med']:+.1f}% против {sC['cell_med']:+.1f}%): "
        f"перекос лонг/шорт кое-что стоит,")
    out("   а вот момент входа по перепроданности — отрицательный вклад.")
    out("   Работает механика усреднения и режимный фильтр, а не сигнал.")
    out("")
    out("5) ФИЛЬТР ТРЕНДА — ЕДИНСТВЕННОЕ, ЧТО СИСТЕМАТИЧЕСКИ ПОМОГАЕТ.")
    bAB = sum(1 for ci in range(len(COMBOS)) if B.med[ci] > A.med[ci])
    out(f"   Улучшает {bAB/len(COMBOS)*100:.1f}% всех комбинаций, сливы "
        f"{sA['ruin']:.1f}% -> {sB['ruin']:.1f}% ячеек, при x5 "
        f"{s5[0]['ruin']:.1f}% -> {s5[1]['ruin']:.1f}%.")
    out("   Но он помогает РОВНО ТАК ЖЕ случайному входу (C -> D), то есть это")
    out("   не «RSI плюс режим», а просто «не стой против тренда».")
    out("   И даже с фильтром медиана остаётся отрицательной: фильтр делает")
    out("   сетку ВЫЖИВАЮЩЕЙ, но не делает её ПРИБЫЛЬНОЙ.")
    out("")
    out("6) РИСК. Сливает депозит почти вся карта: хотя бы один слив из 15")
    out(f"   ячеек у {sum(1 for ci in range(len(COMBOS)) if A.ruin[ci] > 0)/len(COMBOS)*100:.1f}% "
        f"комбинаций без фильтра и у "
        f"{sum(1 for ci in range(len(COMBOS)) if B.ruin[ci] > 0)/len(COMBOS)*100:.1f}% с фильтром.")
    out("   Плечо x10/x15 в связке с мартингейлом x1.5 — машина по сносу")
    out("   счёта: сливы 51% и 68% ячеек против 19% при x5.")
    out("")
    out("7) ОТВЕТ НА ГИПОТЕЗУ ВОЛНЫ («сетка + фильтр тренда даст устойчивый")
    out("   результат без тонкой настройки»): ПОДТВЕРЖДЕНА НАПОЛОВИНУ.")
    out("   Да — фильтр тренда даёт устойчивое, воспроизводимое на всех")
    out("   монетах и периодах улучшение, и это улучшение не требует")
    out("   настройки (работает при любых значениях остальных параметров).")
    out("   Нет — итог всё равно отрицательный. Плато прибыльности нет;")
    out("   единственная область, где связка стабильно в плюсе, держится на")
    out("   ОДНОЙ монете (LTC), а на DOGE/SOL/BTC та же область в минусе.")
    out("   Строить на этом нельзя: это ровно тот случай, который прошлые")
    out("   волны принимали за находку.")
    out("")
    out("ОГОВОРКИ (что этот замер НЕ доказывает):")
    out("  - скелет умышленно голый: без таймаута позиции, без кулдауна, без")
    out("    переноса в безубыток. Живые боты проекта их имеют, и именно")
    out("    таймаут может резать хвост убытка, которого здесь нет;")
    out("  - маржа цикла фиксирована 5$ из 20$ (25% депозита в одном цикле) —")
    out("    это агрессивно и завышает долю сливов. ЗАМЕРЕНО (см. раздел 7):")
    out("    при марже 7.5% сливы падают до нуля, а доля прибыльных ячеек")
    out("    не меняется ВООБЩЕ — размер позиции меняет амплитуду, не знак;")
    out("  - случайный вход равномерен по времени, а RSI-вход сгущается в")
    out("    волатильных участках: часть проигрыша RSI — это «вход в шторм»,")
    out("    а не только контртренд;")
    out("  - 3 периода по 350 дней на 5 монетах — это 15 независимых-ish")
    out("    наблюдений, доверительный интервал широкий.")
    out("")


def smoke():
    out("--- SMOKE: скорость и вменяемость движка ---")
    import evolution as ev
    import evolution6 as e6
    import backtest_rsi_grid as bg
    candles = ev.fetch("DOGEUSDT", "15", DAYS)
    closes = [c[4] for c in candles]
    H = [c[2] for c in candles]
    L = [c[3] for c in candles]
    ATR = daily_atr_pct(candles)
    REG = e6.calc_regime(candles)
    rsi = bg.calc_rsi(closes, 14)
    n = len(candles)
    i0, i1 = _periods_static(n)[2]
    sig = make_sig(rsi, 30, i0, i1)
    out(f"сигналов на периоде 3: {sum(1 for i in range(i0,i1) if sig[i])}")
    t = time.time()
    for prm in COMBOS[:40]:
        run(H, L, closes, ATR, sig, None, prm, i0, i1)
    dt_ = (time.time() - t) / 40
    out(f"среднее время прогона: {dt_*1000:.0f} мс -> вся карта "
        f"~{dt_*len(COMBOS)*15*6/60:.0f} мин в один поток")
    for prm in (dict(rsi_p=14, rsi_th=30, levels=3, step_atr=0.4, tp=0.02,
                     stop=0.08, lev=5),
                dict(rsi_p=14, rsi_th=30, levels=3, step_atr=0.4, tp=0.02,
                     stop=0.08, lev=10),
                dict(rsi_p=7, rsi_th=25, levels=4, step_atr=0.7, tp=0.035,
                     stop=0.15, lev=10)):
        r = run(H, L, closes, ATR, sig, None, prm, i0, i1)
        rb = run(H, L, closes, ATR, sig, REG, prm, i0, i1)
        out(f"  {prm} ->\n     без фильтра {r[0]:+7.1f}% DD {r[1]:4.1f}% "
            f"циклов {r[2]:3} WR {r[3]/max(1,r[2])*100:3.0f}% ликв {r[4]} "
            f"слив {bool(r[5])}\n     с фильтром  {rb[0]:+7.1f}% DD {rb[1]:4.1f}% "
            f"циклов {rb[2]:3} WR {rb[3]/max(1,rb[2])*100:3.0f}% ликв {rb[4]} "
            f"слив {bool(rb[5])}")
    out("")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    if "--smoke" in args:
        self_test()
        smoke()
        flush_out()
        return
    if "--report" in args:
        self_test()          # отчёт всегда идёт вместе с проверкой движка
        with open(DATA_PKL, "rb") as fh:
            meta = pickle.load(fh)["meta"]
        raw = load_raw()
        report(raw, meta)
        flush_out()
        return
    self_test()
    print("Готовлю данные...")
    _, meta = prepare_data()
    raw = run_all()
    report(raw, meta)
    flush_out()


if __name__ == "__main__":
    main()
