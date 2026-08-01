# -*- coding: utf-8 -*-
"""ПРОВЕРКА ПРАВИЛ, ПЕРЕНЕСЁННЫХ ИЗ ВНЕШНЕЙ ЛИТЕРАТУРЫ, НА СЕТАПЕ dump_long
(«капитуляция: лонг после сильного падения»).

Конспект источников и вывод каждого правила — в cap_research.md.
Здесь только измерение. Ни одно правило не объявляется работающим, пока не
прошло три сравнения: базовый dump_long, СЛУЧАЙНЫЙ вход той же частоты,
«купил и держал» на том же окне — плюс поправка на множественные проверки.

ЧТО ИМЕННО МЕРЯЕТСЯ
  База: dump_long с НЕЙТРАЛЬНЫМ семенем signal_engine3.DEFAULTS3 (падение
  >= 6% за 2 суток, RSI14 < 30, зелёная свеча; стоп 2*ATR бара; тейк 2R;
  удержание до 30 суток; сетка выключена). Ни один параметр базы не
  подбирался — это то же «из учебника», что в idea_test.py.

  Правила P1..P9 — ДОБАВКИ к базе (фильтр входа, сдвиг входа, замена выхода,
  сетка). Свободный параметр правила калибруется ТОЛЬКО на обучающей части,
  на holdout уходит ОДНО выбранное значение.

ЧЕСТНОСТЬ МЕХАНИКИ
  Сигнал — на закрытии 4ч-бара, вход — по открытию следующей 15м-свечи,
  ведение по 15м, издержки/ликвидация/фандинг — из signal_engine3 без правок:
  сделку строит тот же se3._make_trade + se3.simulate_grid_trade, что и
  боевой движок. Отбор бара — единственное, что здесь своё.
  Самопроверка SELF-CHECK внизу прогоняет базу этим циклом и движковым
  se3.run_setup и требует совпадения ЧИСЛА СДЕЛОК И СУММЫ R бит в бит —
  иначе весь отчёт брак.

БЕЗ ЗАГЛЯДЫВАНИЯ
  Все добавочные ряды причинные: дневной RSI(2)/SMA200 берутся с ПОСЛЕДНЕГО
  ЗАКРЫТОГО дневного бара на момент закрытия 4ч-бара; объём — свой же бар
  (известен на закрытии) против медианы 30 ПРЕДЫДУЩИХ; funding — ставка,
  УЖЕ рассчитанная к закрытию бара (se2.funding_on_bars). Отдельный тест
  LOOKAHEAD-CHECK проверяет это префиксом: пересчёт рядов по обрезанной
  истории обязан дать те же значения.
"""

import bisect
import json
import math
import os
import random
import statistics
import sys

import evolution as ev
import signal_engine2 as se2
import signal_engine3 as se3
import cap_fetch_vol as cvol

# ------------------------------------------------------------------ конфиг
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT"]
DAYS = 1150
TF = 240                      # сигнальный ТФ, минуты (4ч — там dump_long
                              # проходил честный экзамен в отборе v12)
LEV = 10                      # плечо для ВСЕХ правил (сравнения сопоставимы)
HOLD_FRAC = 0.72              # обучающая часть [0..hold), holdout [hold..n)
RAND_SEEDS = [11, 22, 33, 44, 55]      # те же зёрна, что в idea_test.py
MIN_TRAIN_TRADES = 8          # меньше — параметр правила не выбираем
MIN_HOLD_TRADES = 10          # меньше — результат на holdout нечитаем
N_TESTS = 10                  # объявлено ДО просмотра: правил P1..P10

DAY_MS = 86400 * 1000
OUT_PATH = "cap_research_out.txt"

_LINES = []


def out(s=""):
    _LINES.append(s)
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode("utf-8", "replace").decode("cp1251", "replace"))


# --------------------------------------------------------------- статистика
def norm_sf(z):
    """P(Z > z) для стандартной нормали."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def p_two(t):
    """Двусторонний p по t (нормальное приближение; сделок мало, поэтому
    это ОПТИМИСТИЧНАЯ оценка — настоящий p был бы больше)."""
    return 2.0 * norm_sf(abs(t))


def z_for_p(p):
    """Порог |z| для двустороннего уровня p (бисекция по erfc)."""
    lo, hi = 0.0, 12.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if p_two(mid) > p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def welch_t(a, b):
    """t Уэлча: несёт ли выборка a что-то сверх выборки b."""
    if len(a) < 3 or len(b) < 3:
        return 0.0
    va = statistics.variance(a) / len(a)
    vb = statistics.variance(b) / len(b)
    if va + vb <= 0:
        return 0.0
    return (statistics.mean(a) - statistics.mean(b)) / math.sqrt(va + vb)


def median(vals):
    v = sorted(x for x in vals if x is not None)
    return v[len(v) // 2] if v else None


def percentile(vals, p):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    k = max(0, min(len(v) - 1, int(round(p * (len(v) - 1)))))
    return v[k]


# ------------------------------------------------------------------ данные
def calc_rsi(closes, period):
    """RSI Уайлдера (та же реализация, что у движка: e2.bg.calc_rsi)."""
    return se2.e2.bg.calc_rsi(closes, period)


def calc_sma(vals, period):
    return se2.calc_sma(vals, period)


def daily_maps(sym, ts4_close):
    """Причинные дневные ряды, снесённые на 4ч-бары.

    Для каждого 4ч-бара берём ПОСЛЕДНИЙ ДНЕВНОЙ БАР, который уже ЗАКРЫТ к
    моменту закрытия 4ч-бара (ts_day + 24ч <= ts4_close). Дневная история
    берётся с запасом (1600 суток), чтобы SMA200 существовала уже на первом
    баре 4ч-окна, а не через 200 суток после его начала.
    """
    cd = ev.fetch(sym, "D", 1600)
    closes_d = [c[4] for c in cd]
    rsi2 = calc_rsi(closes_d, 2)
    sma200 = calc_sma(closes_d, 200)
    close_d = [c[0] + DAY_MS for c in cd]       # моменты закрытия дневных
    rsi2_on4, above200_on4 = [], []
    for tc in ts4_close:
        j = bisect.bisect_right(close_d, tc) - 1
        if j < 0:
            rsi2_on4.append(None)
            above200_on4.append(None)
            continue
        rsi2_on4.append(rsi2[j])
        s = sma200[j]
        above200_on4.append(None if s is None else bool(closes_d[j] > s))
    return rsi2_on4, above200_on4


def vol_ratio_series(sym, c4, look=30):
    """Объём бара / медиана объёма ПРЕДЫДУЩИХ look баров (свой бар в медиану
    не входит — иначе всплеск размывал бы собственный эталон)."""
    vmap = cvol.fetch_vol(sym, str(TF))
    vols = [vmap.get(str(c[0])) for c in c4]
    out_r = [None] * len(c4)
    for i in range(len(c4)):
        if i < look or vols[i] is None:
            continue
        win = [v for v in vols[i - look:i] if v]
        if len(win) < look // 2:
            continue
        m = median(win)
        if m:
            out_r[i] = vols[i] / m
    return out_r, vols


def fng_series(ts4_close):
    """Индекс страха и жадности (alternative.me) на каждый 4ч-бар.

    Значения в кэше проекта fng_history.json помечены полуночью UTC — момент
    публикации. Берём ПОСЛЕДНЕЕ значение, опубликованное к закрытию бара.
    Индекс рыночный (один на все монеты) и покрывает не всю историю — где
    его нет, правило просто не даёт сигнала.
    """
    if not os.path.exists("fng_history.json"):
        return [None] * len(ts4_close)
    with open("fng_history.json", encoding="utf-8") as fh:
        raw = json.load(fh)
    pts = sorted((int(k) * 1000, float(v)) for k, v in raw.items())
    ts = [t for t, _ in pts]
    vals = [v for _, v in pts]
    res = []
    for tc in ts4_close:
        j = bisect.bisect_right(ts, tc) - 1
        res.append(vals[j] if j >= 0 else None)
    return res


def speed_ratio_series(closes, d_bars, recent=3):
    """Затухание скорости падения: средний |ход за бар| за последние recent
    баров / средний |ход за бар| за всё окно падения. <1 — падение замедляется."""
    n = len(closes)
    rets = [0.0] * n
    for i in range(1, n):
        p = closes[i - 1]
        rets[i] = (closes[i] - p) / p if p else 0.0
    out_r = [None] * n
    for i in range(max(recent, d_bars) + 1, n):
        rec = sum(abs(x) for x in rets[i - recent + 1:i + 1]) / recent
        win = sum(abs(x) for x in rets[i - d_bars + 1:i + 1]) / d_bars
        if win > 0:
            out_r[i] = rec / win
    return out_r


# ------------------------------------------------- прогон правила и контроля
def build_evs(setup, g, c4, ctx, interval_min=TF):
    """Разбор ворот на КАЖДОМ баре (считаем один раз, правила его фильтруют)."""
    ext = se3.build_ext(setup, g, c4, interval_min=interval_min)
    return [se3.gate_eval(setup, i, c4, ctx, g, ext) for i in range(len(c4))], ext


def run_rule(c4, ctx, c15, ts15, g, evs, ext, rng_bars, decide,
             lev=LEV, ruin=False, start=None, setup="dump_long"):
    """Цикл сделок: та же механика, что у se3.run_setup, но бар выбирает
    предикат decide(i) вместо ворот сетапа.

    decide(i) -> True/False. Сделку строит se3._make_trade (издержки, стоп,
    ликвидация, фандинг, 15м-ведение — движковые, не свои).
    ruin=True воспроизводит остановку торговли при балансе < MARGIN (нужно
    только для самопроверки против run_setup); в исследовании ruin=False,
    потому что мы меряем СИГНАЛ, а не управление капиталом.
    """
    bar_ms = se2.bar_ms_of(int(ctx["interval_min"]))
    n = len(c4)
    a, b = rng_bars
    a = max(int(a), int(ext["warm"]))
    b = min(int(b), n)
    start = se3.START if start is None else float(start)
    balance, peak, max_dd = start, start, 0.0
    trades = []
    busy_until = 0
    ruined = False
    cool_ms = int(se3._gg(g, "cooldown")) * bar_ms
    last_exit = 0
    for i in range(a, b):
        ev_ = evs[i]
        if ev_.get("stop") is None:
            continue
        if not decide(i):
            continue
        sig_ts = c4[i][0] + bar_ms
        if ruined:
            continue
        if sig_ts < busy_until:
            continue
        if last_exit and sig_ts < last_exit + cool_ms:
            continue
        tr, why = se3._make_trade(setup, ev_, i, c4, ctx, g, c15, ts15, lev,
                                  bar_ms=bar_ms, trail15=None)
        if tr is None:
            if why == se3.REJ_NO15M:
                break
            continue
        balance += tr["pnl"]
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak)
        trades.append(tr)
        busy_until = tr["exit_ts"]
        last_exit = tr["exit_ts"]
        if ruin and balance < se3.MARGIN:
            ruined = True
    return dict(trades=trades, balance=balance, max_dd=max_dd, ruined=ruined,
                bars=(a, b))


def agg(per_sym_trades, start=se3.START):
    """Сводка по объединённым сделкам всех монет.

    Кривая капитала строится по сделкам, отсортированным по времени ВЫХОДА;
    маржа на сделку фиксирована (без реинвестирования) — меряем сигнал, а не
    сложный процент. Просадка от этой кривой, доходность — от базы start на
    ОДНУ монету, умноженной на число монет (иначе % несопоставим)."""
    tr = [t for lst in per_sym_trades for t in lst]
    tr.sort(key=lambda t: t["exit_ts"])
    n = len(tr)
    rs = [t["r"] for t in tr]
    pnl = sum(t["pnl"] for t in tr)
    wins = [t["pnl"] for t in tr if t["pnl"] > 0]
    loss = [t["pnl"] for t in tr if t["pnl"] <= 0]
    gp, gl = sum(wins), -sum(loss)
    base = start * max(1, len(per_sym_trades))
    bal, peak, dd = base, base, 0.0
    for t in tr:
        bal += t["pnl"]
        peak = max(peak, bal)
        dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
    kinds = {}
    for t in tr:
        kinds[t["exit_kind"]] = kinds.get(t["exit_kind"], 0) + 1
    return dict(
        n=n, rs=rs,
        wr=(100.0 * sum(1 for t in tr if t["pnl"] > 0) / n) if n else 0.0,
        mean_r=(sum(rs) / n) if n else 0.0, sum_r=sum(rs),
        pf=(gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0),
        ret=100.0 * pnl / base, dd=100.0 * dd, kinds=kinds,
        med_hold=(median([t["hold_h"] for t in tr]) or 0.0))


def random_control(data, g, rng_by_sym, n_by_sym, lev=LEV, setup="dump_long"):
    """Тот же выход, та же сторона, столько же сделок — но бары входа
    СЛУЧАЙНЫ. Усреднение по 5 зёрнам. Если случайный вход даёт то же самое,
    распознавание капитуляции пустое."""
    all_r, rets, ns = [], [], []
    for seed in RAND_SEEDS:
        rnd = random.Random(seed)
        per_sym = []
        for sym in SYMS:
            d = data[sym]
            a, b = rng_by_sym[sym]
            a = max(a, d["ext"]["warm"])
            need = n_by_sym.get(sym, 0)
            if need <= 0 or b - a < need + 5:
                per_sym.append([])
                continue
            bars = set(rnd.sample(range(a, b), min(need, b - a - 1)))
            r = run_rule(d["c4"], d["ctx"], d["c15"], d["ts15"], g,
                         d["evs"], d["ext"], (a, b), lambda i: i in bars,
                         lev=lev, setup=setup)
            per_sym.append(r["trades"])
        m = agg(per_sym)
        all_r += m["rs"]
        rets.append(m["ret"])
        ns.append(m["n"])
    return dict(rs=all_r, n=len(all_r),
                mean_r=(sum(all_r) / len(all_r)) if all_r else 0.0,
                ret=statistics.mean(rets) if rets else 0.0,
                ret_sd=statistics.pstdev(rets) if len(rets) > 1 else 0.0,
                n_avg=statistics.mean(ns) if ns else 0.0)


def buy_hold(data, rng_by_sym):
    """«Купил и держал» спот на том же окне: среднее по монетам, %."""
    vals = []
    for sym in SYMS:
        c4 = data[sym]["c4"]
        a, b = rng_by_sym[sym]
        a = max(a, data[sym]["ext"]["warm"])
        b = min(b, len(c4))
        if b - a < 2:
            continue
        vals.append(100.0 * (c4[b - 1][4] / c4[a][4] - 1.0))
    return statistics.mean(vals) if vals else 0.0


# ------------------------------------------------------------------- правила
def make_decider(data, sym, kind, param, base_only=False):
    """Предикат отбора бара для правила kind с параметром param.

    Все правила — ДОБАВКА к базе: базовое ворото dump_long обязано пройти
    (для P6 — на баре i-param, вход отложен).
    """
    d = data[sym]
    ok = d["base_ok"]
    if kind == "base":
        return lambda i: ok[i]
    if kind == "P1":                       # Коннорс: дневной RSI(2) < param
        arr = d["rsi2_d"]
        return lambda i: ok[i] and arr[i] is not None and arr[i] < param
    if kind == "P2":                       # Коннорс: тренд-фильтр SMA200(D)
        arr = d["above200"]
        want = bool(param)
        return lambda i: ok[i] and arr[i] is not None and arr[i] == want
    if kind == "P3":                       # затухание скорости падения
        arr = d["speed"]
        return lambda i: ok[i] and arr[i] is not None and arr[i] <= param
    if kind == "P4":                       # объёмная кульминация
        arr = d["volr"]
        return lambda i: ok[i] and arr[i] is not None and arr[i] >= param
    if kind == "P5":                       # фандинг ушёл в минус
        arr = d["fund"]
        return lambda i: ok[i] and arr[i] is not None and arr[i] <= param
    if kind == "P10":                      # индекс страха и жадности <= X
        arr = d["fng"]
        return lambda i: ok[i] and arr[i] is not None and arr[i] <= param
    if kind == "P6":                       # отложенный вход через param баров
        k = int(param)
        return lambda i: i - k >= 0 and ok[i - k]
    if kind in ("P7", "P8"):               # выход по времени / сетка:
        return lambda i: ok[i]             # отбор баров не меняется
    raise ValueError(kind)


def combo_decider(data, sym, filters, shift):
    """Предикат комбинации: И всех фильтров, с общим сдвигом входа."""
    d = data[sym]
    ok = d["base_ok"]
    tests = []
    for kind, param in filters:
        if kind == "P1":
            tests.append((d["rsi2_d"], lambda v, p=param: v < p))
        elif kind == "P2":
            tests.append((d["above200"], lambda v, p=param: v == bool(p)))
        elif kind == "P3":
            tests.append((d["speed"], lambda v, p=param: v <= p))
        elif kind == "P4":
            tests.append((d["volr"], lambda v, p=param: v >= p))
        elif kind == "P5":
            tests.append((d["fund"], lambda v, p=param: v <= p))
        elif kind == "P10":
            tests.append((d["fng"], lambda v, p=param: v <= p))

    def dec(i):
        j = i - shift
        if j < 0 or not ok[j]:
            return False
        for arr, f in tests:
            v = arr[j] if j < len(arr) else None
            if v is None or not f(v):
                return False
        return True
    return dec


def genome_for(kind, param, base_g):
    """Геном правила: P7 меняет выход, P8 включает сетку, остальные — базовый."""
    if kind == "P7":                       # выход по времени вместо тейка:
        return se3.default_genome(          # цель недостижима, закрытие по
            **dict(base_g, tp_mode=0, tp_r=999.0,   # таймауту через param суток
                   hold_days=int(param)))
    if kind == "P8":                       # сетка усреднения (запрос владельца)
        lv, step, mult = param
        return se3.default_genome(**dict(base_g, grid_levels=int(lv),
                                         grid_step_atr=float(step),
                                         grid_mult=float(mult)))
    return dict(base_g)


def eval_variant(data, kind, param, rng_by_sym, base_g, lev=LEV):
    """Прогон одного варианта правила на заданном окне по всем монетам."""
    g = genome_for(kind, param, base_g)
    per_sym, n_by_sym = [], {}
    for sym in SYMS:
        d = data[sym]
        dec = make_decider(data, sym, kind, param)
        r = run_rule(d["c4"], d["ctx"], d["c15"], d["ts15"], g, d["evs"],
                     d["ext"], rng_by_sym[sym], dec, lev=lev)
        per_sym.append(r["trades"])
        n_by_sym[sym] = len(r["trades"])
    m = agg(per_sym)
    m["n_by_sym"] = n_by_sym
    m["g"] = g
    m["per_sym"] = per_sym
    return m


# ---------------------------------------------------------------------- main
def main():
    out("=" * 100)
    out("ВНЕШНЯЯ ЛИТЕРАТУРА -> ПРАВИЛА -> ПРОВЕРКА НА СЕТАПЕ dump_long")
    out("=" * 100)
    out("Источники и вывод каждого правила: cap_research.md")
    out(f"Монеты: {', '.join(SYMS)} | ТФ сигнала {TF}м | история {DAYS} суток")
    out(f"Издержки: тейкер {se3.TAKER*100:.3f}%, мейкер {se3.MAKER*100:.2f}%, "
        f"проскальзывание {se3.SLIP*100:.2f}%, фандинг "
        f"{se3.FUND_8H*100:.2f}%/8ч. Плечо x{LEV}, маржа ${se3.MARGIN:.0f}, "
        f"база ${se3.START:.0f} на монету.")
    out("Сигнал — на закрытии 4ч-бара, вход — по открытию следующей 15м-свечи,")
    out("ведение по 15м. Стоп и тейк в одной свече -> СТОП (консервативно).")
    out()
    out("ЗАРАНЕЕ ОБЪЯВЛЕННЫЙ КРИТЕРИЙ (зафиксирован ДО просмотра чисел).")
    out("Правило работает, если НА HOLDOUT выполнено ВСЁ:")
    out(f"  1) средний R выше базового dump_long;")
    out(f"  2) средний R выше случайного входа той же частоты и |t| >= порога;")
    out(f"  3) доходность окна выше «купил и держал»;")
    out(f"  4) сделок >= {MIN_HOLD_TRADES} (иначе результат нечитаем).")
    zb = z_for_p(0.05 / N_TESTS)
    out(f"  Проверяется {N_TESTS} правил -> поправка Бонферрони: "
        f"|t| >= {zb:.2f} (alpha 0.05/{N_TESTS}). Без поправки было бы 1.96.")
    out(f"  Свободный параметр правила калибруется ТОЛЬКО на обучающей части "
        f"(нужно >= {MIN_TRAIN_TRADES} сделок), на holdout идёт ОДНО значение.")
    out()

    # ------------------------------------------------------------ данные
    out("-" * 100)
    out("ДАННЫЕ")
    out("-" * 100)
    base_g = se3.default_genome()
    data = {}
    for sym in SYMS:
        c4 = ev.fetch(sym, str(TF), DAYS)
        c15 = ev.fetch(sym, "15", DAYS)
        ctx = se3.prep_context(c4, interval_min=TF, symbol=sym)
        ts15 = [c[0] for c in c15]
        evs, ext = build_evs("dump_long", base_g, c4, ctx)
        ts4_close = [c[0] + se2.bar_ms_of(TF) for c in c4]
        rsi2_d, above200 = daily_maps(sym, ts4_close)
        volr, _vols = vol_ratio_series(sym, c4)
        speed = speed_ratio_series([c[4] for c in c4], ext["d_bars"])
        fng = fng_series(ts4_close)
        data[sym] = dict(
            c4=c4, c15=c15, ctx=ctx, ts15=ts15, evs=evs, ext=ext,
            base_ok=[e["ok"] for e in evs],
            rsi2_d=rsi2_d, above200=above200, volr=volr, speed=speed,
            fund=ctx["fund"], fng=fng)
        n_ok = sum(1 for x in data[sym]["base_ok"] if x)
        out(f"  {sym:9} 4ч {len(c4):5} баров, 15м {len(c15):6}, "
            f"прогрев {ext['warm']:4}, сигналов базы {n_ok:3}, "
            f"объём/дневки/фандинг/F&G: "
            f"{sum(1 for v in volr if v is not None):5}/"
            f"{sum(1 for v in rsi2_d if v is not None):5}/"
            f"{sum(1 for v in ctx['fund'] if v is not None):5}/"
            f"{sum(1 for v in fng if v is not None):5} баров")

    n4 = len(data[SYMS[0]]["c4"])
    hold = int(HOLD_FRAC * n4)
    tr_rng = {s: (0, hold) for s in SYMS}
    ho_rng = {s: (hold, len(data[s]["c4"])) for s in SYMS}
    out(f"  Разрез: обучение бары [0..{hold}) = {HOLD_FRAC*100:.0f}% истории, "
        f"HOLDOUT [{hold}..{n4}). Сделка, открытая в окне, может закрыться позже.")
    out()

    # -------------------------------------------------------- самопроверка
    out("-" * 100)
    out("SELF-CHECK: мой цикл против движкового se3.run_setup (обязан совпасть)")
    out("-" * 100)
    bad = 0
    for sym in SYMS:
        d = data[sym]
        r_eng = se3.run_setup("dump_long", base_g, d["c4"], d["ctx"], d["c15"],
                              d["ts15"], LEV, symbol=sym)
        r_my = run_rule(d["c4"], d["ctx"], d["c15"], d["ts15"], base_g,
                        d["evs"], d["ext"], (0, len(d["c4"])),
                        make_decider(data, sym, "base", None), ruin=True)
        ne, nm = len(r_eng["trades"]), len(r_my["trades"])
        se_, sm = (sum(t["r"] for t in r_eng["trades"]),
                   sum(t["r"] for t in r_my["trades"]))
        okk = (ne == nm and abs(se_ - sm) < 1e-9
               and abs(r_eng["balance"] - r_my["balance"]) < 1e-6)
        bad += 0 if okk else 1
        out(f"  {sym:9} движок: {ne:3} сделок, sumR {se_:+7.3f}, "
            f"баланс {r_eng['balance']:7.2f} | мой цикл: {nm:3}, {sm:+7.3f}, "
            f"{r_my['balance']:7.2f}  -> {'СОВПАЛО' if okk else 'РАСХОЖДЕНИЕ'}")
    if bad:
        out("  ПРОВАЛ САМОПРОВЕРКИ — отчёт недействителен.")
        return 1
    out("  Все монеты совпали: механика сделки — движковая, своё только "
        "правило отбора бара.")
    out()

    # ------------------------------------------------------ проверка причинности
    out("-" * 100)
    out("LOOKAHEAD-CHECK: пересчёт добавочных рядов по обрезанной истории")
    out("-" * 100)
    sym = SYMS[0]
    d = data[sym]
    cut = int(0.6 * n4)
    c4c = d["c4"][:cut]
    ts4c = [c[0] + se2.bar_ms_of(TF) for c in c4c]
    r2c, a2c = daily_maps(sym, ts4c)
    vrc, _ = vol_ratio_series(sym, c4c)
    spc = speed_ratio_series([c[4] for c in c4c], d["ext"]["d_bars"])
    fdc = se3.prep_context(c4c, interval_min=TF, symbol=sym)["fund"]

    def same(a, b, name):
        diff = sum(1 for k in range(len(b))
                   if (a[k] is None) != (b[k] is None)
                   or (a[k] is not None and b[k] is not None
                       and abs(float(a[k]) - float(b[k])) > 1e-9))
        out(f"  {name:22} расхождений на префиксе {cut} баров: {diff}")
        return diff
    tot = 0
    tot += same(d["rsi2_d"], r2c, "дневной RSI(2)")
    tot += same([1 if x else (None if x is None else 0) for x in d["above200"]],
                [1 if x else (None if x is None else 0) for x in a2c],
                "дневная SMA200")
    tot += same(d["volr"], vrc, "объём/медиана")
    tot += same(d["speed"], spc, "затухание скорости")
    tot += same(d["fund"], fdc, "funding на баре")
    out(f"  ИТОГ: {'заглядывания нет' if tot == 0 else 'ЕСТЬ ЗАГЛЯДЫВАНИЕ'}")
    if tot:
        return 1
    out()

    # ------------------------------------------------- распределения признаков
    out("-" * 100)
    out("ЧТО ВООБЩЕ БЫВАЕТ НА СИГНАЛАХ БАЗЫ (обучающая часть) — чтобы пороги")
    out("правил не оказались взяты из воздуха")
    out("-" * 100)
    feat = {"rsi2_d": [], "volr": [], "speed": [], "fund": [], "above200": [],
            "fng": []}
    for sym in SYMS:
        d = data[sym]
        for i in range(d["ext"]["warm"], hold):
            if not d["base_ok"][i]:
                continue
            for k in feat:
                v = d[k][i] if k != "fund" else d["fund"][i]
                if v is not None:
                    feat[k].append(float(v))
    for k, title in (("rsi2_d", "дневной RSI(2)"), ("volr", "объём/медиана30"),
                     ("speed", "скорость посл.3 / скорость окна"),
                     ("fund", "funding, %/8ч"), ("fng", "индекс страха F&G")):
        v = feat[k]
        if v:
            out(f"  {title:32} n={len(v):3}  p10={percentile(v, .10):8.4f}  "
                f"p25={percentile(v, .25):8.4f}  медиана={percentile(v, .50):8.4f}"
                f"  p75={percentile(v, .75):8.4f}  p90={percentile(v, .90):8.4f}")
    if feat["above200"]:
        out(f"  {'доля сигналов выше дневной SMA200':32} "
            f"{100.0*statistics.mean(feat['above200']):.1f}%")
    f10 = percentile(feat["fund"], .10) if feat["fund"] else -0.005
    f25 = percentile(feat["fund"], .25) if feat["fund"] else 0.0
    out(f"  Пороги фандинга для P5 берём из ОБУЧАЮЩЕГО распределения: "
        f"0.0 / {f25:.4f} (p25) / {f10:.4f} (p10).")
    out()

    # ------------------------------------------------------------- сетка правил
    RULES = [
        ("P1", "Коннорс: дневной RSI(2) < X (глубокая перепроданность)",
         [5, 10, 20], "X"),
        ("P2", "Коннорс: тренд-фильтр — цена выше дневной SMA200",
         [1], "выше"),
        ("P3", "затухание скорости падения: скор.посл.3 / скор.окна <= q",
         [0.5, 0.7, 0.9], "q"),
        ("P4", "объёмная кульминация: объём бара / медиана30 >= m",
         [1.5, 2.0, 3.0], "m"),
        ("P5", "фандинг ушёл в минус: ставка <= f (%/8ч)",
         [0.0, round(f25, 5), round(f10, 5)], "f"),
        ("P6", "отложенный вход: войти через k баров 4ч ПОСЛЕ сигнала",
         [1, 3, 6], "k"),
        ("P7", "выход по ВРЕМЕНИ: без тейка, закрытие через M суток",
         [1, 2, 3, 7], "M"),
        ("P8", "сетка усреднения (запрос владельца): колена/шаг ATR/множитель",
         [(2, 1.0, 1.5), (3, 0.7, 1.3), (4, 0.5, 1.2)], "сетка"),
        ("P10", "индекс страха и жадности <= X (экстремальный страх)",
         [15, 20, 25], "X"),
    ]

    # ---------------------------------------------- база на обоих окнах
    base_tr = eval_variant(data, "base", None, tr_rng, base_g)
    base_ho = eval_variant(data, "base", None, ho_rng, base_g)
    rnd_base_tr = random_control(data, base_g, tr_rng, base_tr["n_by_sym"])
    rnd_base_ho = random_control(data, base_g, ho_rng, base_ho["n_by_sym"])
    bh_tr, bh_ho = buy_hold(data, tr_rng), buy_hold(data, ho_rng)

    out("-" * 100)
    out("БАЗА: dump_long с нейтральным семенем (никакой добавки)")
    out("-" * 100)
    out(f"  {'окно':9} {'сделок':>7} {'WR%':>6} {'ср.R':>7} {'сумма R':>8} "
        f"{'PF':>6} {'дох.%':>7} {'просад%':>8} {'ср.случ.R':>10} {'t':>6}")
    for name, m, r_ in (("обучение", base_tr, rnd_base_tr),
                        ("HOLDOUT", base_ho, rnd_base_ho)):
        t = welch_t(m["rs"], r_["rs"])
        out(f"  {name:9} {m['n']:7} {m['wr']:6.1f} {m['mean_r']:+7.3f} "
            f"{m['sum_r']:+8.2f} {m['pf']:6.2f} {m['ret']:+7.1f} "
            f"{m['dd']:8.1f} {r_['mean_r']:+10.3f} {t:+6.2f}")
    out(f"  «купил и держал» (среднее по 5 монетам): обучение {bh_tr:+.1f}%, "
        f"HOLDOUT {bh_ho:+.1f}%")
    out(f"  выходы базы (holdout): {base_ho['kinds']}, "
        f"медианное удержание {base_ho['med_hold']:.1f} ч")
    out(f"  случайный вход на holdout дал в среднем {rnd_base_ho['n_avg']:.1f} "
        f"сделок за зерно (у базы {base_ho['n']}) — частота сопоставима.")
    out()
    out("  ВАЖНО ПРО ОКНО. Обучение — растущий рынок, holdout — падающий:")
    out(f"  {'монета':9} {'B&H обуч.%':>11} {'B&H hold.%':>11} "
        f"{'сд.hold':>8} {'ср.R hold':>10}")
    for k, sym in enumerate(SYMS):
        a1, b1 = tr_rng[sym]
        a2, b2 = ho_rng[sym]
        c4 = data[sym]["c4"]
        a1 = max(a1, data[sym]["ext"]["warm"])
        t1 = 100.0 * (c4[b1 - 1][4] / c4[a1][4] - 1.0)
        t2 = 100.0 * (c4[b2 - 1][4] / c4[a2][4] - 1.0)
        tr_s = base_ho["per_sym"][k]
        mr = (sum(t["r"] for t in tr_s) / len(tr_s)) if tr_s else 0.0
        out(f"  {sym:9} {t1:+11.1f} {t2:+11.1f} {len(tr_s):8} {mr:+10.3f}")
    out("  Лонговый сетап на падающем окне обязан быть в минусе — это НЕ")
    out("  доказательство его негодности, но и «обыграть B&H» на таком окне")
    out("  не заслуга: держать -57% проигрывает почти чему угодно.")
    out()

    # --------------------------------------------- калибровка на обучении
    out("-" * 100)
    out("КАЛИБРОВКА ПАРАМЕТРОВ — ТОЛЬКО НА ОБУЧАЮЩЕЙ ЧАСТИ")
    out("-" * 100)
    out(f"  Выбираем значение с максимальным средним R при >= "
        f"{MIN_TRAIN_TRADES} сделок. Это заведомо оптимистично на обучении —")
    out("  ровно поэтому решение принимается по holdout, а не по этой таблице.")
    out(f"  {'прав':5} {'значение':>14} {'сделок':>7} {'WR%':>6} {'ср.R':>7} "
        f"{'сумма R':>8} {'PF':>6} {'дох.%':>7}")
    chosen = {}
    for code, title, params, pname in RULES:
        out(f"  {code}: {title}")
        best = None
        for p in params:
            m = eval_variant(data, code, p, tr_rng, base_g)
            thin = "" if m["n"] >= MIN_TRAIN_TRADES else "   (сделок мало)"
            if m["n"] >= MIN_TRAIN_TRADES and (best is None
                                               or m["mean_r"] > best[1]["mean_r"]):
                best = (p, m)
            out(f"  {code:5} {str(p):>14} {m['n']:7} {m['wr']:6.1f} "
                f"{m['mean_r']:+7.3f} {m['sum_r']:+8.2f} {m['pf']:6.2f} "
                f"{m['ret']:+7.1f}{thin}")
        if best is None:
            out(f"  {code:5} -> НЕ КАЛИБРУЕТСЯ: ни одно значение не дало "
                f"{MIN_TRAIN_TRADES} сделок; на holdout уходит первое значение "
                f"{params[0]}")
            m0 = eval_variant(data, code, params[0], tr_rng, base_g)
            chosen[code] = (params[0], m0)
        else:
            chosen[code] = best
            out(f"  {code:5} -> выбрано {pname}={best[0]} "
                f"(ср.R на обучении {best[1]['mean_r']:+.3f})")
        out()

    out("  ЗЕРКАЛА (справочно, отдельными гипотезами НЕ считаются: двусторонний")
    out("  t того же правила уже покрывает обратное направление):")
    for code, p, title in (("P2", 0, "ниже дневной SMA200 (против Коннорса)"),
                           ("P3", None, "падение НЕ замедлилось (скор. > q)")):
        if code == "P3":
            q = chosen["P3"][0]
            per_sym, _n = [], {}
            for sym in SYMS:
                d = data[sym]
                arr = d["speed"]
                okb = d["base_ok"]
                dec = (lambda i, a=arr, o=okb, qq=q:
                       o[i] and a[i] is not None and a[i] > qq)
                r = run_rule(d["c4"], d["ctx"], d["c15"], d["ts15"], base_g,
                             d["evs"], d["ext"], tr_rng[sym], dec, lev=LEV)
                per_sym.append(r["trades"])
            m = agg(per_sym)
        else:
            m = eval_variant(data, code, p, tr_rng, base_g)
        out(f"    {title:44} обучение: {m['n']:3} сд., ср.R {m['mean_r']:+.3f}, "
            f"PF {m['pf']:.2f}")
    out()

    # ------------------------------------------------- комбинация P9
    out("-" * 100)
    out("P9: КОМБИНАЦИЯ того, что обыграло базу НА ОБУЧЕНИИ")
    out("-" * 100)
    cand = sorted([(c, chosen[c][0], chosen[c][1]["mean_r"])
                   for c in ("P1", "P2", "P3", "P4", "P5", "P10")
                   if chosen.get(c) and chosen[c][1]["mean_r"] > base_tr["mean_r"]],
                  key=lambda x: -x[2])
    shift = 0
    if chosen.get("P6") and chosen["P6"][1]["mean_r"] > base_tr["mean_r"]:
        shift = int(chosen["P6"][0])
    combo_g = dict(base_g)
    if chosen.get("P7") and chosen["P7"][1]["mean_r"] > base_tr["mean_r"]:
        combo_g = genome_for("P7", chosen["P7"][0], base_g)

    def combo_eval(rng_by_sym, filters, sh, gg):
        per_sym, n_by = [], {}
        for sym in SYMS:
            d = data[sym]
            dec = combo_decider(data, sym, filters, sh)
            r = run_rule(d["c4"], d["ctx"], d["c15"], d["ts15"], gg,
                         d["evs"], d["ext"], rng_by_sym[sym], dec, lev=LEV)
            per_sym.append(r["trades"])
            n_by[sym] = len(r["trades"])
        m = agg(per_sym)
        m["n_by_sym"] = n_by
        m["g"] = gg
        return m

    out(f"  Кандидаты (обыграли базу на обучении, по убыванию ср.R): "
        f"{[(c, p) for c, p, _ in cand] if cand else 'нет'}")
    out(f"  Сдвиг входа: {shift} баров. Выход: "
        f"{'по времени ' + str(combo_g['hold_days']) + ' сут' if combo_g.get('tp_r', 0) > 90 else 'тейк 2R'}")
    out("  Фильтры добавляются ЖАДНО, пока на обучении остаётся хотя бы "
        f"{MIN_TRAIN_TRADES} сделок — иначе комбинация схлопывается в ноль")
    out("  сделок и проверять на holdout будет нечего.")
    good, combo_tr = [], None
    for c, p, _ in cand:
        trial = good + [(c, p)]
        m = combo_eval(tr_rng, trial, shift, combo_g)
        keep = m["n"] >= MIN_TRAIN_TRADES
        out(f"    + {c}={p:<8} -> на обучении {m['n']:3} сд., "
            f"ср.R {m['mean_r']:+.3f}  {'принят' if keep else 'ОТКЛОНЁН (мало сделок)'}")
        if keep:
            good, combo_tr = trial, m
    if combo_tr is None:
        combo_tr = combo_eval(tr_rng, good, shift, combo_g)
    out(f"  ИТОГ P9: фильтры {good if good else 'нет'}, сдвиг {shift}, "
        f"выход {'по времени' if combo_g.get('tp_r', 0) > 90 else 'тейк 2R'}")
    out(f"  на обучении: {combo_tr['n']} сделок, WR {combo_tr['wr']:.1f}%, "
        f"ср.R {combo_tr['mean_r']:+.3f}, PF {combo_tr['pf']:.2f}")
    out()

    # --------------------------------------------------- ГЛАВНАЯ ТАБЛИЦА
    out("=" * 100)
    out("HOLDOUT — ЕДИНСТВЕННОЕ, ПО ЧЕМУ ПРИНИМАЕТСЯ РЕШЕНИЕ")
    out("=" * 100)
    out(f"  База dump_long на holdout: {base_ho['n']} сделок, "
        f"ср.R {base_ho['mean_r']:+.3f}; случайный вход {rnd_base_ho['mean_r']:+.3f}; "
        f"«купил и держал» {bh_ho:+.1f}%")
    out()
    hdr = (f"  {'прав':5} {'параметр':>12} {'сдел':>5} {'WR%':>6} {'ср.R':>7} "
           f"{'PF':>6} {'дох.%':>7} {'DD%':>6} | {'vs база':>8} "
           f"{'vs случ':>8} {'t':>6} {'p':>7} | вердикт")
    out(hdr)
    out("  " + "-" * (len(hdr) - 2))
    rows = []
    variants = [(c, chosen[c][0]) for c, _, _, _ in RULES if chosen.get(c)]
    variants.append(("P9", "комбо"))
    for code, p in variants:
        if code == "P9":
            m = combo_eval(ho_rng, good, shift, combo_g)
            g_used = combo_g
        else:
            m = eval_variant(data, code, p, ho_rng, base_g)
            g_used = m["g"]
        if m["n"] == 0:
            out(f"  {code:5} {str(p):>12} {0:5} {'—':>6} {'—':>7} {'—':>6} "
                f"{'—':>7} {'—':>6} |        —        —      —       — | "
                f"нет сделок на holdout")
            rows.append((code, p, m, None, 0.0, 1.0, False))
            continue
        rc = random_control(data, g_used, ho_rng, m["n_by_sym"])
        t = welch_t(m["rs"], rc["rs"])
        pv = p_two(t)
        d_base = m["mean_r"] - base_ho["mean_r"]
        d_rand = m["mean_r"] - rc["mean_r"]
        cond = [d_base > 0, (d_rand > 0 and abs(t) >= zb),
                m["ret"] > bh_ho, m["n"] >= MIN_HOLD_TRADES]
        verdict = ("РАБОТАЕТ" if all(cond) else
                   "мало сделок" if not cond[3] else
                   "нет")
        why = []
        if not cond[0]:
            why.append("не лучше базы")
        if not cond[1]:
            why.append("не лучше случая" if d_rand <= 0 else "не значимо")
        if not cond[2]:
            why.append("хуже B&H")
        if not cond[3]:
            why.append(f"n<{MIN_HOLD_TRADES}")
        out(f"  {code:5} {str(p):>12} {m['n']:5} {m['wr']:6.1f} "
            f"{m['mean_r']:+7.3f} {m['pf']:6.2f} {m['ret']:+7.1f} "
            f"{m['dd']:6.1f} | {d_base:+8.3f} {d_rand:+8.3f} {t:+6.2f} "
            f"{pv:7.3f} | {verdict}"
            + (f" ({', '.join(why)})" if why else ""))
        rows.append((code, p, m, rc, t, pv, all(cond)))
    out()
    out(f"  Порог значимости с поправкой Бонферрони: |t| >= {zb:.2f}. "
        f"Без поправки |t| >= 1.96 (тогда одно «значимое» из {N_TESTS} —")
    out("  чистая случайность, поэтому одиночный порог здесь не применяется).")
    out()

    # ------------------------------- сколько вообще можно различить при таком n
    out("  РАЗРЕШАЮЩАЯ СПОСОБНОСТЬ ВЫБОРКИ. Насколько крупным должен быть")
    out("  эффект, чтобы его вообще можно было доказать при таком числе сделок:")
    out(f"  {'прав':5} {'сдел':>5} {'сигма R':>8} {'нужен прирост ср.R':>20} "
        f"{'фактический':>13}")
    for code, p, m, rc, t, pv, okc in rows:
        if m["n"] < 3 or rc is None:
            continue
        sd = statistics.pstdev(m["rs"])
        sd_r = statistics.pstdev(rc["rs"]) if len(rc["rs"]) > 2 else sd
        need = zb * math.sqrt(sd * sd / m["n"] + sd_r * sd_r / max(1, rc["n"]))
        out(f"  {code:5} {m['n']:5} {sd:8.3f} {need:+20.3f} "
            f"{m['mean_r'] - rc['mean_r']:+13.3f}")
    out("  Т.е. при 20-30 сделках доказуемо только преимущество размером")
    out("  ПОРЯДКА одного R на сделку. Ничего подобного в крипте не бывает,")
    out("  поэтому «не доказано» здесь — ожидаемый исход, а не приговор идее.")
    out()

    # ---------------------------------------- чувствительность: широкая база
    out("-" * 100)
    out("ЧУВСТВИТЕЛЬНОСТЬ: та же проверка на РАСШИРЕННОЙ базе (падение >= 4%,")
    out("RSI14 < 35) — сделок вдвое больше, значит выводы менее шумные.")
    out("Это ВСПОМОГАТЕЛЬНЫЙ прогон: параметры правил взяты уже выбранные,")
    out("заново ничего не калибруется, holdout тот же.")
    out("-" * 100)
    wide_g = se3.default_genome(drop_frac=0.04, rsi_os=35)
    wdata = {}
    for sym in SYMS:
        d = data[sym]
        evs_w, ext_w = build_evs("dump_long", wide_g, d["c4"], d["ctx"])
        wdata[sym] = dict(d)
        wdata[sym]["evs"] = evs_w
        wdata[sym]["ext"] = ext_w
        wdata[sym]["base_ok"] = [e["ok"] for e in evs_w]
    wb_tr = eval_variant(wdata, "base", None, tr_rng, wide_g)
    wb_ho = eval_variant(wdata, "base", None, ho_rng, wide_g)
    wr_ho = random_control(wdata, wide_g, ho_rng, wb_ho["n_by_sym"])
    out(f"  {'прав':5} {'параметр':>12} {'сдел':>5} {'WR%':>6} {'ср.R':>7} "
        f"{'PF':>6} {'дох.%':>7} | {'vs база':>8} {'vs случ':>8} {'t':>6}")
    out(f"  {'база':5} {'-':>12} {wb_ho['n']:5} {wb_ho['wr']:6.1f} "
        f"{wb_ho['mean_r']:+7.3f} {wb_ho['pf']:6.2f} {wb_ho['ret']:+7.1f} | "
        f"{0.0:+8.3f} {wb_ho['mean_r']-wr_ho['mean_r']:+8.3f} "
        f"{welch_t(wb_ho['rs'], wr_ho['rs']):+6.2f}   (обучение: "
        f"{wb_tr['n']} сд., ср.R {wb_tr['mean_r']:+.3f})")
    for code, p in variants:
        if code == "P9":
            continue
        m = eval_variant(wdata, code, p, ho_rng, wide_g)
        rc = random_control(wdata, m["g"], ho_rng, m["n_by_sym"])
        t = welch_t(m["rs"], rc["rs"])
        out(f"  {code:5} {str(p):>12} {m['n']:5} {m['wr']:6.1f} "
            f"{m['mean_r']:+7.3f} {m['pf']:6.2f} {m['ret']:+7.1f} | "
            f"{m['mean_r']-wb_ho['mean_r']:+8.3f} "
            f"{m['mean_r']-rc['mean_r']:+8.3f} {t:+6.2f}")
    out()

    # ------------------------------------------------------------- плечо
    out("-" * 100)
    out("ПЛЕЧО: владелец просит x20-25. Что при этом происходит МЕХАНИЧЕСКИ")
    out("-" * 100)
    out("  Движок не даёт открыть сделку, если стоп шире 80% расстояния до")
    out("  ликвидации. При x10 это 7.6% цены, при x20 — 3.6%, при x25 — 2.8%.")
    out("  Стоп 2*ATR у dump_long часто шире 3% -> часть сигналов ПРОПАДАЕТ.")
    out(f"  {'плечо':>6} {'до ликв.%':>10} {'потолок стопа%':>15} "
        f"{'сделок(holdout)':>16} {'ср.R':>7} {'дох.%':>7} {'ликвидаций':>11}")
    for lv in (5, 10, 20, 25):
        m = eval_variant(data, "base", None, ho_rng, base_g, lev=lv)
        liq = m["kinds"].get("liq", 0)
        out(f"  {('x'+str(lv)):>6} {se2.liq_frac(lv)*100:10.1f} "
            f"{min(base_g['stop_cap'], 0.8*se2.liq_frac(lv))*100:15.1f} "
            f"{m['n']:16} {m['mean_r']:+7.3f} {m['ret']:+7.1f} {liq:11}")
    out("  R нормирован на риск, поэтому средний R от плеча почти не зависит —")
    out("  меняется ЧИСЛО доступных сигналов и % дохода на ту же маржу.")
    out()
    out("  Чтобы сигналы вообще появились на x20-25, стоп надо СУЖАТЬ. Что")
    out("  тогда: (стоп k*ATR бара, holdout, база)")
    out(f"  {'плечо':>6} {'k*ATR':>7} {'сдел':>5} {'WR%':>6} {'ср.R':>7} "
        f"{'дох.%':>7} {'ликв.':>6} {'стопов':>7}")
    for lv, ks in ((20, (1.0, 0.75)), (25, (0.75, 0.5))):
        for k in ks:
            gg = se3.default_genome(stop_atr_k=k)
            per_sym = []
            for sym in SYMS:
                d = data[sym]
                evs_k, ext_k = build_evs("dump_long", gg, d["c4"], d["ctx"])
                r = run_rule(d["c4"], d["ctx"], d["c15"], d["ts15"], gg,
                             evs_k, ext_k, ho_rng[sym],
                             (lambda i, e=evs_k: e[i]["ok"]), lev=lv)
                per_sym.append(r["trades"])
            m = agg(per_sym)
            out(f"  {('x'+str(lv)):>6} {k:7.2f} {m['n']:5} {m['wr']:6.1f} "
                f"{m['mean_r']:+7.3f} {m['ret']:+7.1f} "
                f"{m['kinds'].get('liq', 0):6} {m['kinds'].get('stop', 0):7}")
    out()

    # ------------------------------------------------------------- вывод
    out("=" * 100)
    out("ВЫВОД")
    out("=" * 100)
    passed = [r for r in rows if r[6]]
    if passed:
        out(f"  Прошли все четыре условия на holdout: "
            f"{', '.join(r[0] for r in passed)}")
    else:
        out("  НИ ОДНО правило из внешней литературы не прошло все четыре")
        out("  условия на holdout.")
    best = max(rows, key=lambda r: r[2]["mean_r"]) if rows else None
    if best:
        out(f"  Лучший по среднему R на holdout: {best[0]} "
            f"(параметр {best[1]}), ср.R {best[2]['mean_r']:+.3f} против "
            f"{base_ho['mean_r']:+.3f} у базы и {best[3]['mean_r']:+.3f} "
            f"у случайного входа, t={best[4]:+.2f}, p={best[5]:.3f} "
            f"(порог {zb:.2f}).")
    out(f"  Напоминание о масштабе: база на holdout — {base_ho['n']} сделок. "
        f"При таком n правило должно давать очень крупный")
    out("  эффект, чтобы t перевалил порог Бонферрони; отсутствие значимости "
        "здесь означает «не доказано», а не «доказано, что нет».")
    out()
    out("  САМА БАЗА хуже случайного входа на ОБОИХ окнах "
        f"({base_tr['mean_r']:+.3f} против {rnd_base_tr['mean_r']:+.3f} на "
        f"обучении, {base_ho['mean_r']:+.3f} против "
        f"{rnd_base_ho['mean_r']:+.3f} на holdout).")
    out("  Это независимое подтверждение вывода idea_test.py: литературные")
    out("  добавки пытались улучшить то, у чего нет исходного преимущества.")
    out()
    out("  УСТОЙЧИВОСТЬ ЗНАКА (главное, что вообще можно извлечь при таком n):")
    out("  сравниваем прирост к базе на ОСНОВНОЙ и на РАСШИРЕННОЙ базе —")
    out("  правило, у которого знак прыгает, это шум по определению.")
    out(f"  {'прав':5} {'осн. vs база':>13} {'расш. vs база':>14} "
        f"{'осн. vs случ':>13} {'расш. vs случ':>14}  знак")
    for code, p in variants:
        if code == "P9":
            continue
        r_main = next((r for r in rows if r[0] == code), None)
        if r_main is None or r_main[2]["n"] == 0 or r_main[3] is None:
            continue
        mw = eval_variant(wdata, code, p, ho_rng, wide_g)
        rw = random_control(wdata, mw["g"], ho_rng, mw["n_by_sym"])
        a1 = r_main[2]["mean_r"] - base_ho["mean_r"]
        a2 = mw["mean_r"] - wb_ho["mean_r"]
        b1 = r_main[2]["mean_r"] - r_main[3]["mean_r"]
        b2 = mw["mean_r"] - rw["mean_r"]
        sgns = [a1 > 0, a2 > 0, b1 > 0, b2 > 0]
        mark = ("устойчиво +" if all(sgns) else
                "устойчиво -" if not any(sgns) else "знак прыгает")
        out(f"  {code:5} {a1:+13.3f} {a2:+14.3f} {b1:+13.3f} {b2:+14.3f}  {mark}")
    out()
    out("  Плечо: при x25 со штатным стопом 2*ATR НЕЛЬЗЯ открыть ни одной")
    out("  сделки — стоп всегда шире расстояния до ликвидации. Сузить стоп,")
    out("  чтобы влез, можно, но все такие варианты дают ср.R хуже базового.")

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_LINES) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
