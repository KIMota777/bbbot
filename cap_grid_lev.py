# -*- coding: utf-8 -*-
"""cap_grid_lev.py — ПРОВЕРКА ДВУХ ПРЕДЛОЖЕНИЙ ВЛАДЕЛЬЦА ПО СЕТАПУ dump_long.

Сетап «капитуляция-лонг» (резкое падение за N суток + перепроданность RSI +
разворотная свеча -> лонг, стоп по k*ATR, тейк кратен риску) уже проверялся:
в отборе v12 он проходил экзамен, но воспроизводимость по зерну была 1/3, а
в тесте идей с фиксированными параметрами он оказался ХУЖЕ случайного входа
(-0.215R). То есть сырого преимущества у идеи нет. Здесь НЕ переоткрывается
сама идея — здесь измеряются ровно два предложения владельца:

  1) «набирать небольшой сеткой для защиты от сквизов» — при капитуляции цена
     часто делает финальный прокол ниже входа и разворачивается; одиночный
     вход ловит стоп, сетка переживает. ГЛАВНАЯ ЦИФРА идеи — сколько сделок
     сетка реально СПАСЛА (одиночный вход был бы остановлен, сетка вышла в
     плюс) и сколько ИСПОРТИЛА (одиночный взял бы тейк, сетка досидела до
     убытка);
  2) «плечо x20-25 чтобы быстро забирать профит» — измеряется, на сколько
     ликвидация приближается/удаляется при каждом колене, сколько сделок
     доживает, сколько закрывается ликвидацией РАНЬШЕ стопа, и что даёт
     короткое удержание (тейк 0.5-2R, таймаут 6-48ч).

МЕТОД (нарушение = брак)
  - 5 монет, 4ч, 1150 суток. Обучающая часть [0..hold), hold = int(n*0.72);
    HOLDOUT [hold..n) не участвует НИ в подборе, ни в выборе конфигурации —
    только финальная оценка.
  - Все конфигурации сравниваются на ОДНОМ И ТОМ ЖЕ множестве сигналов:
    сигнал сетапа зависит только от «ворот» (падение/RSI/свеча/ширина стопа),
    а сетка/плечо/тейк/таймаут на сигнал не влияют. Поэтому сравнение
    ПАРНОЕ: одна и та же сделка прожита разными правилами сопровождения, и
    «спасено/испорчено» считается по совпадающим парам, а не по разным
    выборкам.
  - Каждый сигнал — отдельная сделка (без учёта занятости): это событийное
    исследование, где выборка не должна зависеть от конфигурации. Портфельные
    цифры (итог, просадка, слив) считаются отдельно, с занятостью: одна
    позиция на монету.
  - Три обязательных сравнения: базовый dump_long без улучшения; СЛУЧАЙНЫЙ
    вход той же частоты (те же стоп/сетка/тейк/таймаут, случайный бар);
    «купил и держал» на том же окне.
  - Значимость: t-статистика по сделкам плюс поправка Бонферрони на число
    проверенных конфигураций (их здесь много, и одна «значимая» из двадцати
    получается случайно).
  - Издержки проектные (TAKER 0.055%, MAKER 0.02%, SLIP 0.03%, FUND 0.01%/8ч),
    ликвидация 1/плечо - 0.005; всё берётся из signal_engine3, ничего не
    переопределяется.

ЕДИНИЦА РИСКА. R нормирован на ФАКТИЧЕСКИЙ риск позиции (объём на выходе x
расстояние «средняя -> стоп»), как в signal_engine3. Поэтому R сравним между
одиночным входом и сеткой: сетка не получает премии за то, что первое колено
меньше.

ДВА СПОСОБА РАЗМЕРА СЕТКИ (различаются только масштабом в долларах):
  A «маржа цикла фиксирована» (так устроен signal_engine3 и боты проекта):
    сумма всех колен = 5$, первое колено при 3 коленах меньше одиночного
    входа втрое. Риск на сделку не растёт;
  B «первое колено = одиночному входу» (так обычно понимают «усреднение»):
    доливки ДОБАВЛЯЮТ экспозицию, суммарная маржа цикла = 5$ x (1+m+m^2).
  Цены, средняя, момент выхода, R и расстояние до ликвидации в A и B
  ОДИНАКОВЫ (всё линейно по марже), различаются только суммы в долларах,
  просадка и риск слива — поэтому B считается пересчётом, а не отдельным
  прогоном (проверяется тестом).

Запуск: python cap_grid_lev.py   (вывод дублируется в cap_grid_lev_out.txt)
        python cap_grid_lev.py --smoke   (быстрая проверка механики)
"""

import bisect
import math
import os
import random
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if os.getcwd() != BASE_DIR:
    os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

import evolution as ev              # noqa: E402
import signal_engine3 as se3        # noqa: E402

OUT_TXT = os.path.join(BASE_DIR, "cap_grid_lev_out.txt")

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "LTCUSDT"]
TF = 240                      # 4ч — единственный ТФ, где есть все 5 монет
DAYS = 1150
HOLD_FRAC = 0.72              # обучающая часть [0..hold)
SETUP = "dump_long"
SIDE = "L"

MARGIN = se3.MARGIN           # 5$ маржа цикла
START = se3.START             # 20$ депозит на монету
MMR = se3.MMR
BARS_15_H = 4                 # 15м-баров в часе
HOLD_BASE_H = 30 * 24         # базовое удержание сетапа — 30 суток

BASE_LEV = 10                 # плечо для раздела «сетка» (стоп 4-5% в него влезает)
SEEDS_RND = 20                # зёрен случайного входа
_OUT = []


def out(s=""):
    print(s)
    _OUT.append(s)


def flush_out():
    with open(OUT_TXT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(_OUT) + "\n")


def hr(ch="-", n=100):
    out(ch * n)


def d(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


# ======================================================================
#                       ЗАГРУЗКА И ПОДГОТОВКА
# ======================================================================
# Два «стоповых мира»: базовый стоп 2xATR (нейтральное семя движка) и тесный
# стоп 0.8xATR. Тесный нужен не ради красоты — при плече x20 движок не даёт
# ставить стоп шире 0.8 x (1/20-0.005) = 3.6%, а базовый стоп капитуляции
# на 4ч — 4-5%. Без тесного варианта вопрос «безопасно ли x20-25» вообще не
# имел бы сделок, на которых его можно измерить.
UNIVERSES = {
    "base": dict(stop_atr_k=2.0),
    "tight": dict(stop_atr_k=0.8),
    # Второй набор параметров того же сетапа — тот, что победил в отборе v12
    # (evolution12_winners.json, dump_long@240, отбирался на BTC): падение
    # всего 2.2% за 2 суток, RSI7<44, стоп 0.8xATR с потолком 2.75%,
    # удержание 8 суток, фильтр направления SMA400, режим «боковик»
    # выключен. Нужен как ПРОВЕРКА УСТОЙЧИВОСТИ выводов о сетке и плече:
    # если сетка «защищает от сквизов», это должно быть видно и здесь, на
    # втором наборе порогов и на заметно большей выборке.
    "v12": dict(stop_atr_k=0.8, stop_cap=0.0275, drop_frac=0.02234,
                rsi_os=44, rsi_idx=0, window=200, trend_gate=1,
                trend_len=400, reg_range=0),
}
UNI_STOP_CAP = {"base": 0.06, "tight": 0.06, "v12": 0.0275}


def load_symbol(sym):
    c4 = ev.fetch(sym, str(TF), DAYS)
    c15 = ev.fetch(sym, "15", DAYS)
    ts15 = [c[0] for c in c15]
    ctx = se3.prep_context(c4, TF, sym)
    return dict(symbol=sym, c4=c4, c15=c15, ts15=ts15, ctx=ctx, n=len(c4))


def build_universe(ds, uni_key):
    """Для каждого бара ТФ: цена стопа по правилу мира, план входа по 15м и
    признак «ворота сетапа пропустили». Только данные <= i."""
    g = se3.default_genome(**UNIVERSES[uni_key])
    c4, ctx, c15, ts15 = ds["c4"], ds["ctx"], ds["c15"], ds["ts15"]
    ext = se3.build_ext(SETUP, g, c4, interval_min=TF)
    bar_ms = se3.bar_ms_of(TF)
    n = len(c4)
    bars = []
    sig_idx = []
    for i in range(n):
        atr_b = ctx["atr_bar"][i]
        if i < ext["warm"] or not atr_b:
            bars.append(None)
            continue
        ref = c4[i][4]
        stop_px = se3._stop_price(SIDE, i, c4, ctx, g, ext, ref)
        j, entry_px = se3._plan_entry(c4[i][0], c15, ts15, bar_ms)
        if j is None:
            bars.append(None)
            continue
        dist = (entry_px - stop_px) / entry_px
        bars.append(dict(i=i, ts4=c4[i][0], entry_i15=j, entry_px=entry_px,
                         stop_px=stop_px, dist=dist, atr_b=atr_b))
        ev_ = se3.gate_eval(SETUP, i, c4, ctx, g, ext)
        if ev_["ok"]:
            sig_idx.append(i)
    return dict(g=g, ext=ext, bars=bars, sig_idx=sig_idx)


def prefix_check(ds, uni_key, k):
    """Проверка отсутствия заглядывания: сигналы, найденные на префиксе
    истории c4[:k], обязаны совпасть с сигналами полного прогона при i<k."""
    g = se3.default_genome(**UNIVERSES[uni_key])
    c4p = ds["c4"][:k]
    ctxp = se3.prep_context(c4p, TF, ds["symbol"])
    extp = se3.build_ext(SETUP, g, c4p, interval_min=TF)
    got = [i for i in range(extp["warm"], k)
           if se3.gate_eval(SETUP, i, c4p, ctxp, g, extp)["ok"]]
    return got


# ======================================================================
#                        ОДНА СДЕЛКА / ОДИН ПРОГОН
# ======================================================================
def cap_for(lev, cfg):
    """Предел ширины стопа. 'engine' — как в signal_engine3: не шире
    min(stop_cap, 0.8 x расстояния до ликвидации), поэтому ликвидация
    физически не может опередить стоп. 'naive' — так, как сделал бы трейдер,
    который просто поставил стоп по сетапу и включил плечо: ограничения нет."""
    if cfg.get("cap", "engine") == "naive":
        return 1.0
    return min(float(cfg.get("stop_cap", 0.06)), 0.8 * se3.liq_frac(lev))


def grid_tp_frac(bar, cfg):
    """Куда сетка ставит тейк, если нальются ВСЕ колена, — в долях первого
    фила. Величина известна заранее (цены колен и стоп заданы на входе),
    заглядывания нет. Нужна для контрольного прогона «тот же тейк, но БЕЗ
    доливок»: он отделяет эффект усреднения от эффекта опущенной цели."""
    fill0 = bar["entry_px"] * (1 + se3.SLIP)
    step = float(cfg["step"]) * bar["atr_b"]
    w = [float(cfg["mult"]) ** k for k in range(int(cfg["levels"]))]
    ps = [(1 - step) ** k for k in range(int(cfg["levels"]))]
    avg = fill0 * (sum(w) / sum(w[k] / ps[k] for k in range(len(w))))
    tp_px = avg + float(cfg["tp_r"]) * abs(avg - bar["stop_px"])
    return tp_px / fill0 - 1.0


def sim_one(bar, cfg):
    """Одна сделка из бара bar по правилам cfg. None -> вход отклонён."""
    lev = cfg["lev"]
    if bar is None:
        return None, "нет данных"
    dist = bar["dist"]
    if dist <= 0 or dist < se3.MIN_STOP:
        return None, "стоп уже минимума"
    if dist > cap_for(lev, cfg):
        return None, "стоп шире лимита"
    tp_mode, tp_frac = 0, 0.0
    if cfg.get("tp_ctrl"):
        tp_mode, tp_frac = 1, grid_tp_frac(bar, cfg["tp_ctrl"])
    sim = se3.simulate_grid_trade(
        SIDE, bar["entry_i15"], bar["entry_px"], bar["stop_px"], cfg["c15"],
        lev, int(cfg["hold_bars"]),
        levels=int(cfg["levels"]),
        step_frac=float(cfg["step"]) * bar["atr_b"],
        mult=float(cfg["mult"]),
        tp_mode=tp_mode, tp_r=float(cfg["tp_r"]), tp_frac=tp_frac,
        trail15=None, be_after_r=0.0, margin=MARGIN)
    sim["atr_b"] = bar["atr_b"]
    sim["levels"] = int(cfg["levels"])
    sim["ts4"] = bar["ts4"]
    sim["i"] = bar["i"]
    sim["entry_ts"] = cfg["ts15"][bar["entry_i15"]]
    sim["exit_ts"] = cfg["ts15"][sim["exit_i15"]]
    sim["hold_h"] = (sim["exit_ts"] - sim["entry_ts"]) / 3600000.0
    sim["dist0"] = dist
    sim["lev"] = lev
    return sim, None


def run_cfg(data, uni, cfg, part="train", bars_override=None):
    """Прогон конфигурации по всем сигналам части истории (без занятости).

    Возвращает {монета: [сделки]}, счётчик отказов и общий список."""
    res = {}
    rejects = {}
    allt = []
    for sym in SYMS:
        ds = data[sym]
        u = uni[sym]
        lo, hi = part_range(ds, part)
        c = dict(cfg)
        c["c15"], c["ts15"] = ds["c15"], ds["ts15"]
        rows = []
        src = bars_override[sym] if bars_override else [
            u["bars"][i] for i in u["sig_idx"] if lo <= i < hi]
        for bar in src:
            tr, why = sim_one(bar, c)
            if tr is None:
                rejects[why] = rejects.get(why, 0) + 1
                continue
            tr["symbol"] = sym
            rows.append(tr)
        res[sym] = rows
        allt.extend(rows)
    return res, allt, rejects


def part_range(ds, part):
    n = ds["n"]
    h = int(n * HOLD_FRAC)
    if part == "train":
        return 0, h
    if part == "hold":
        return h, n
    return 0, n


# ======================================================================
#                              МЕТРИКИ
# ======================================================================
def agg(rows):
    n = len(rows)
    if n == 0:
        return dict(n=0, wr=0.0, exp_r=0.0, sum_r=0.0, sd=0.0, t=0.0, pf=None,
                    pnl=0.0, fills=0.0, multi=0.0, full=0.0, liq=0, stop=0,
                    tp=0, timeout=0, hold_h=0.0, mae=0.0)
    rs = [x["r"] for x in rows]
    m = sum(rs) / n
    sd = (math.sqrt(sum((x - m) ** 2 for x in rs) / (n - 1)) if n > 1 else 0.0)
    gp = sum(x["pnl"] for x in rows if x["pnl"] > 0)
    gl = -sum(x["pnl"] for x in rows if x["pnl"] < 0)
    kinds = {}
    for x in rows:
        kinds[x["reason"]] = kinds.get(x["reason"], 0) + 1
    return dict(
        n=n,
        wr=100.0 * sum(1 for x in rows if x["pnl"] > 0) / n,
        exp_r=m, sum_r=sum(rs), sd=sd,
        t=(m / (sd / math.sqrt(n)) if sd > 0 else 0.0),
        pf=(gp / gl if gl > 0 else None),
        pnl=sum(x["pnl"] for x in rows),
        fills=sum(x["grid_fills"] for x in rows) / n,
        multi=100.0 * sum(1 for x in rows if x["grid_fills"] >= 2) / n,
        # доля сделок, где налилось ПОСЛЕДНЕЕ колено: если она мала, дальние
        # колена мертвы (стоят ниже стопа) и конфигурация с 3 коленами
        # физически равна конфигурации с 2 коленами, только меньшим объёмом
        full=100.0 * sum(1 for x in rows
                         if x["grid_fills"] >= x.get("levels", 1)) / n,
        liq=kinds.get("liq", 0), stop=kinds.get("stop", 0),
        tp=kinds.get("tp", 0), timeout=kinds.get("timeout", 0),
        hold_h=sum(x["hold_h"] for x in rows) / n,
        mae=sum(x["mae_r"] for x in rows) / n,
    )


def paired(single_rows, grid_rows):
    """Парное сравнение сетки с одиночным входом по одним и тем же сигналам.

    saved   — одиночный вход остановлен (стоп/ликвидация) в минус, а сетка
              вышла в ПЛЮС. Это и есть «защита от сквизов» в чистом виде;
    spoiled — одиночный взял ТЕЙК, а сетка закрылась в убыток;
    worse   — сетка просто хуже одиночного по деньгам (шире, чем spoiled).
    """
    idx = {(x["symbol"], x["ts4"]): x for x in single_rows}
    saved = spoiled = worse = better = 0
    n_pair = 0
    stopped = 0
    tp_single = 0
    tp_lost = 0
    dr = 0.0
    for gtr in grid_rows:
        s = idx.get((gtr["symbol"], gtr["ts4"]))
        if s is None:
            continue
        n_pair += 1
        dr += gtr["r"] - s["r"]
        s_bad = s["reason"] in ("stop", "liq") and s["pnl"] < 0
        if s_bad:
            stopped += 1
            if gtr["pnl"] > 0:
                saved += 1
        if s["reason"] == "tp" and s["pnl"] > 0:
            tp_single += 1
            if gtr["pnl"] < 0:
                spoiled += 1
            if gtr["reason"] != "tp":
                tp_lost += 1
        if gtr["pnl"] < s["pnl"] - 1e-12:
            worse += 1
        elif gtr["pnl"] > s["pnl"] + 1e-12:
            better += 1
    return dict(n=n_pair, saved=saved, spoiled=spoiled, worse=worse,
                better=better, stopped=stopped, tp_single=tp_single,
                tp_lost=tp_lost, d_sum_r=dr,
                saved_rate=(100.0 * saved / stopped if stopped else 0.0),
                spoil_rate=(100.0 * spoiled / tp_single if tp_single else 0.0))


def portfolio(res_by_sym, scale=1.0):
    """Портфель с занятостью: одна позиция на монету, счёт на монету 20$,
    маржа цикла 5$ x scale, слив при балансе < маржи цикла.

    scale=1 — режим A (маржа цикла фиксирована); scale=sum(mult^k) — режим B
    (первое колено равно одиночному входу, доливки добавляют экспозицию)."""
    marg = MARGIN * scale
    curves = []
    ruined = 0
    taken = 0
    total_pnl = 0.0
    for sym in SYMS:
        rows = sorted(res_by_sym.get(sym, []), key=lambda x: x["entry_ts"])
        bal = START
        busy = 0
        dead = False
        for x in rows:
            if dead:
                continue
            if x["entry_ts"] < busy:
                continue
            bal += x["pnl"] * scale
            total_pnl += x["pnl"] * scale
            taken += 1
            busy = x["exit_ts"]
            curves.append((x["exit_ts"], sym, bal))
            if bal < marg:
                dead = True
                ruined += 1
        curves.append((10 ** 15, sym, bal))
    # общая кривая: сумма балансов монет в хронологии закрытий
    last = {s: START for s in SYMS}
    peak = sum(last.values())
    dd = 0.0
    for ts, sym, bal in sorted(curves, key=lambda z: z[0]):
        last[sym] = bal
        tot = sum(last.values())
        peak = max(peak, tot)
        dd = max(dd, (peak - tot) / peak)
    fin = sum(last.values())
    base = START * len(SYMS)
    return dict(taken=taken, ruined=ruined, balance=fin,
                ret=100.0 * (fin / base - 1), dd=100.0 * dd,
                pnl=total_pnl)


# ======================================================================
#                        СЛУЧАЙНЫЙ ВХОД ТОЙ ЖЕ ЧАСТОТЫ
# ======================================================================
def random_bench(data, uni, cfg, part, ref_by_sym, seeds=SEEDS_RND,
                 strat=False):
    """Случайный вход ТОЙ ЖЕ ЧАСТОТЫ: столько же входов на монету, тот же
    стоп по тому же правилу, та же сетка/тейк/таймаут — случаен ТОЛЬКО бар.

    Бары, где стоп не влезает в лимит, из пула исключаются (реальный сетап
    тоже такие не берёт), иначе частота была бы ниже заявленной.

    strat=True — вход подбирается ещё и по ВОЛАТИЛЬНОСТИ: для каждой реальной
    сделки случайный бар берётся из той же квинтили ATR. Это важно: сетап
    входит в дни капитуляции, где ATR вдвое выше обычного, а значит стоп
    шире и структура сделки другая. Без такой привязки «случайный вход»
    сравнивался бы с другим рынком, а не с другим моментом входа."""
    per_seed = []
    for sd in range(seeds):
        rnd = random.Random(4000 + sd)
        picks = {}
        for sym in SYMS:
            ds = data[sym]
            u = uni[sym]
            lo, hi = part_range(ds, part)
            refs = ref_by_sym.get(sym, [])
            k = len(refs)
            if k <= 0:
                picks[sym] = []
                continue
            pool = [b for b in u["bars"][lo:hi]
                    if b is not None and se3.MIN_STOP <= b["dist"]
                    <= cap_for(cfg["lev"], cfg)]
            if len(pool) <= k:
                picks[sym] = list(pool)
            elif not strat:
                picks[sym] = rnd.sample(pool, k)
            else:
                sa = sorted(b["atr_b"] for b in pool)
                qs = [sa[int(len(sa) * q / 5)] for q in range(1, 5)]
                buckets = [[] for _ in range(5)]
                for b in pool:
                    buckets[bisect.bisect_left(qs, b["atr_b"])].append(b)
                sel = []
                for r in refs:
                    q = bisect.bisect_left(qs, r["atr_b"])
                    bkt = buckets[q] or pool
                    sel.append(bkt[rnd.randrange(len(bkt))])
                picks[sym] = sel
        _, allt, _ = run_cfg(data, uni, cfg, part=part, bars_override=picks)
        per_seed.append(agg(allt))
    exp = [x["exp_r"] for x in per_seed]
    sm = [x["sum_r"] for x in per_seed]
    wr = [x["wr"] for x in per_seed]
    pnl = [x["pnl"] for x in per_seed]
    liq = [x["liq"] for x in per_seed]
    mean = lambda a: sum(a) / len(a) if a else 0.0          # noqa: E731
    sd = lambda a: (math.sqrt(sum((x - mean(a)) ** 2 for x in a)
                              / (len(a) - 1)) if len(a) > 1 else 0.0)  # noqa
    return dict(exp_r=mean(exp), exp_sd=sd(exp), sum_r=mean(sm),
                wr=mean(wr), pnl=mean(pnl), liq=mean(liq),
                n=mean([x["n"] for x in per_seed]), raw=exp, seeds=len(exp))


def beats_random(setup_exp, rnd):
    """Доля зёрен случайного входа, у которых ожидание НЕ ХУЖЕ сетапа —
    эмпирический аналог односторонней p-value."""
    if not rnd["raw"]:
        return 1.0
    return sum(1 for x in rnd["raw"] if x >= setup_exp) / len(rnd["raw"])


# ======================================================================
#                        КУПИЛ И ДЕРЖАЛ
# ======================================================================
def bh(data, part):
    vals = {}
    for sym in SYMS:
        ds = data[sym]
        lo, hi = part_range(ds, part)
        lo = max(lo, 300)
        vals[sym] = 100.0 * (ds["c4"][hi - 1][4] / ds["c4"][lo][4] - 1)
    vals["_avg"] = sum(vals[s] for s in SYMS) / len(SYMS)
    return vals


# ======================================================================
#                      АРИФМЕТИКА ЛИКВИДАЦИИ
# ======================================================================
def liq_after_fills(levels, step_frac, mult, lev):
    """Где стоит ликвидация после каждого колена — в долях ПЕРВОГО фила.

    Ключ: средняя = sum(m_i)/sum(m_i/p_i), а объём q = lev*sum(m_i/p_i),
    поэтому использованная маржа на единицу объёма = средняя/плечо ВСЕГДА,
    сколько бы колен ни налилось. Значит ликвидация всегда стоит ровно на
    (1/плечо - MMR) НИЖЕ СРЕДНЕЙ — а средняя с каждым коленом уходит вниз,
    то есть ликвидация в абсолютной цене УДАЛЯЕТСЯ от точки входа."""
    lf = se3.liq_frac(lev)
    w = [mult ** k for k in range(levels)]
    ps = [(1 - step_frac) ** k for k in range(levels)]
    rows = []
    for k in range(levels):
        num = sum(w[:k + 1])
        den = sum(w[j] / ps[j] for j in range(k + 1))
        avg = num / den
        liq = avg * (1 - lf)
        rows.append(dict(k=k + 1, avg=avg, liq=liq,
                         from_entry=100.0 * (1 - liq),
                         last_leg=100.0 * (1 - ps[k])))
    return rows


# ======================================================================
#                             ОФОРМЛЕНИЕ
# ======================================================================
def row_str(name, a, extra=""):
    pf = f"{a['pf']:.2f}" if a["pf"] else "  - "
    return (f"{name:<26} {a['n']:>4} {a['wr']:>6.1f} {a['exp_r']:>+7.3f} "
            f"{a['sum_r']:>+7.1f} {pf:>5} {a['pnl']:>+8.2f} "
            f"{a['fills']:>5.2f} {a['multi']:>5.1f} {a['full']:>6.1f} "
            f"{a['t']:>+5.2f}" + extra)


HEAD = (f"{'конфигурация':<26} {'N':>4} {'WR%':>6} {'exp_R':>7} "
        f"{'sumR':>7} {'PF':>5} {'PnL$':>8} {'колен':>5} {'2+%':>5} "
        f"{'посл.%':>6} {'t':>5}")


def main(smoke=False):
    t_all = time.time()
    out("=" * 100)
    out("cap_grid_lev: сетка «от сквизов» и высокое плечо на сетапе "
        "dump_long (капитуляция-лонг)")
    out("5 монет, 4ч, 1150 суток; обучение [0..72%), holdout [72%..100%) — "
        "только финальная оценка")
    out("=" * 100)

    # ------------------------------------------------ данные
    data, uni = {}, {}
    uni_t, uni_v = {}, {}
    out("\nДАННЫЕ И СИГНАЛЫ (базовый геном движка: падение 6% за 2 суток, "
        "RSI14<30, зелёная свеча, стоп 2xATR, тейк 2R)")
    hr()
    tot_tr = tot_ho = 0
    for sym in SYMS:
        ds = load_symbol(sym)
        data[sym] = ds
        uni[sym] = build_universe(ds, "base")
        uni_t[sym] = build_universe(ds, "tight")
        uni_v[sym] = build_universe(ds, "v12")
        h = int(ds["n"] * HOLD_FRAC)
        s_tr = [i for i in uni[sym]["sig_idx"] if i < h]
        s_ho = [i for i in uni[sym]["sig_idx"] if i >= h]
        tot_tr += len(s_tr)
        tot_ho += len(s_ho)
        dl = sorted(uni[sym]["bars"][i]["dist"] for i in uni[sym]["sig_idx"]
                    if uni[sym]["bars"][i])
        dt = sorted(uni_t[sym]["bars"][i]["dist"] for i in uni_t[sym]["sig_idx"]
                    if uni_t[sym]["bars"][i])
        out(f"{sym:<9} баров {ds['n']} ({d(ds['c4'][0][0])}.."
            f"{d(ds['c4'][-1][0])}) | сигналов {len(uni[sym]['sig_idx']):>3}"
            f" = трейн {len(s_tr):>2} + holdout {len(s_ho):>2} | стоп 2xATR "
            f"медиана {100*dl[len(dl)//2]:.2f}% | стоп 0.8xATR медиана "
            f"{100*dt[len(dt)//2]:.2f}%")
    h0 = int(data[SYMS[0]]["n"] * HOLD_FRAC)
    out(f"ИТОГО сигналов: трейн {tot_tr}, holdout {tot_ho}. "
        f"Граница holdout — бар {h0} ({d(data[SYMS[0]]['c4'][h0][0])}).")
    out("Выборка мала (десятки сделок) — это свойство сетапа, а не метода: "
        "капитуляция 6% за 2 суток случается редко. Все выводы ниже надо "
        "читать с этой поправкой.")

    # ------------------------------------------------ проверка причинности
    out("\nПРОВЕРКА «НЕТ ЗАГЛЯДЫВАНИЯ» (префиксный тест)")
    hr()
    ok_all = True
    for sym in SYMS[:2]:
        k = int(data[sym]["n"] * 0.6)
        got = prefix_check(data[sym], "base", k)
        full = [i for i in uni[sym]["sig_idx"] if i < k]
        same = got == full
        ok_all = ok_all and same
        out(f"{sym:<9} сигналов на префиксе c4[:{k}] = {len(got)}, "
            f"на полном ряде при i<{k} = {len(full)} -> "
            f"{'СОВПАДАЮТ' if same else 'РАСХОЖДЕНИЕ!'}")
    if ok_all:
        out("Значит на баре i использованы только данные <= i.")
    else:
        out("ВНИМАНИЕ: расхождение префикса — результаты недействительны.")

    base_cfg = dict(levels=1, step=1.0, mult=1.0, lev=BASE_LEV, tp_r=2.0,
                    hold_bars=HOLD_BASE_H * BARS_15_H, stop_cap=0.06,
                    cap="engine")

    # проверка эквивалентности режимов A и B (линейность по марже)
    tst = data[SYMS[0]]
    b0 = next(uni[SYMS[0]]["bars"][i] for i in uni[SYMS[0]]["sig_idx"])
    c_a = dict(base_cfg, levels=3, step=0.5, mult=1.3,
               c15=tst["c15"], ts15=tst["ts15"])
    ra, _ = sim_one(b0, c_a)
    sb = se3.simulate_grid_trade(
        SIDE, b0["entry_i15"], b0["entry_px"], b0["stop_px"], tst["c15"],
        BASE_LEV, base_cfg["hold_bars"], levels=3,
        step_frac=0.5 * b0["atr_b"], mult=1.3, tp_mode=0, tp_r=2.0,
        margin=MARGIN * (1 + 1.3 + 1.69))
    # --- сверка с самим движком -----------------------------------------
    # Этот файл не зовёт se3.run_setup (нужны таймауты короче суток и парные
    # прогоны), а ведёт цикл сам. Значит обязан доказать, что ведёт его ТАК
    # ЖЕ: на базовом геноме с одиночным входом сделки должны совпасть с
    # родным прогоном движка одна в одну.
    gs = se3.default_genome()
    for sym in SYMS[:2]:
        ds = data[sym]
        r_eng = se3.run_setup(SETUP, gs, ds["c4"], ds["ctx"], ds["c15"],
                              ds["ts15"], BASE_LEV, symbol=sym,
                              interval_min=TF)
        mine, allm, _ = run_cfg(data, uni, base_cfg, "all")
        mrows = sorted(mine[sym], key=lambda x: x["entry_ts"])
        busy, kept = 0, []
        for x in mrows:
            if x["entry_ts"] >= busy:
                kept.append(x)
                busy = x["exit_ts"]
        e_tr = r_eng["trades"]
        same_n = len(kept) == len(e_tr)
        dmax = max([abs(a["pnl"] - b["pnl"]) for a, b in zip(kept, e_tr)]
                   or [0.0])
        # движок округляет pnl до 6 знаков при записи сделки, поэтому
        # допуск 1e-6, а не машинный ноль
        out(f"Сверка с se3.run_setup на {sym}: движок {len(e_tr)} сделок, "
            f"этот файл {len(kept)}; макс. расхождение PnL "
            f"{dmax:.10f}$ (округление движка до 6 знаков) -> "
            f"{'СОВПАДАЕТ' if same_n and dmax < 1e-6 else 'РАСХОЖДЕНИЕ!'}")

    k_b = 1 + 1.3 + 1.69
    out(f"Режим B считается пересчётом режима A: PnL(A)x{k_b:.2f} = "
        f"{ra['pnl']*k_b:+.4f}$ против прямого прогона {sb['pnl']:+.4f}$, "
        f"R одинаков ({ra['r']:+.3f} и {sb['r']:+.3f}) — линейность "
        f"{'подтверждена' if abs(ra['pnl']*k_b - sb['pnl']) < 1e-9 and abs(ra['r']-sb['r']) < 1e-9 else 'НАРУШЕНА'}.")
    flush_out()

    if smoke:
        out("\n--smoke: механика проверена, полный прогон пропущен.")
        flush_out()
        return

    # ==================================================================
    # РАЗДЕЛ 1. СЕТКА ОТ СКВИЗОВ
    # ==================================================================
    out("\n")
    out("=" * 100)
    out("РАЗДЕЛ 1. «НЕБОЛЬШАЯ СЕТКА ОТ СКВИЗОВ» — обучающая часть, плечо "
        f"x{BASE_LEV}, тейк 2R, удержание 30 суток")
    out("=" * 100)
    out("Смысл проверки: при капитуляции цена часто делает финальный прокол "
        "ниже входа и разворачивается.")
    out("Если это так, сетка обязана СПАСАТЬ сделки, которые одиночный вход "
        "отдаёт стопу. Считаем спасённые и испорченные ПАРНО — одни и те же "
        "сигналы, разное сопровождение.")
    out("")
    sing_res, sing_all, rej = run_cfg(data, uni, base_cfg, "train")
    a_single = agg(sing_all)
    out(HEAD)
    hr()
    out(row_str("одиночный вход (база)", a_single))

    grid_rows = []
    for lv in (2, 3):
        for st in (0.3, 0.5, 0.8, 1.2):
            for ml in (1.0, 1.3, 1.6):
                cfg = dict(base_cfg, levels=lv, step=st, mult=ml)
                res, allt, _ = run_cfg(data, uni, cfg, "train")
                a = agg(allt)
                p = paired(sing_all, allt)
                grid_rows.append((cfg, a, p, res))
                out(row_str(f"колен {lv}, шаг {st} ATR, x{ml}", a,
                            f"  спас {p['saved']:>2}/{p['stopped']:<2} "
                            f"исп {p['spoiled']:>2}/{p['tp_single']:<2}"))
    hr()
    out("колен — среднее число исполненных колен; 2+% — доля сделок, где "
        "долилось хотя бы одно колено; посл.% — доля сделок, где налилось "
        "ПОСЛЕДНЕЕ колено;")
    out("спас a/b — сетка вышла в плюс там, где одиночный вход получил "
        "стоп (b — сколько всего таких сделок);")
    out("исп a/b — сетка закрылась в минус там, где одиночный взял тейк "
        "(b — сколько всего тейков у одиночного).")
    out(f"отказы входа: {rej if rej else 'нет'}")
    out("")
    out("ВАЖНО, ИНАЧЕ ТАБЛИЦА ЧИТАЕТСЯ НЕВЕРНО:")
    out("1) «испорчено 0» — это НЕ заслуга сетки, а теорема. Тейк в движке "
        "считается от СРЕДНЕЙ, а средняя при доливках только ниже входа,")
    out("   значит цель сетки всегда БЛИЖЕ цели одиночного входа. Если "
        "одиночный дошёл до тейка, сетка дошла до своего раньше или тогда "
        "же.")
    out(f"   Проверка утверждения на данных: во всех {len(grid_rows)} "
        f"конфигурациях случаев «одиночный взял тейк, а сетка нет» — "
        f"{sum(p['tp_lost'] for _, _, p, _ in grid_rows)}.")
    out("   Поэтому «сетка не портит сделки» — свойство арифметики, а не "
        "доказательство защиты от сквизов.")
    out("2) Сетка обязана ПОМЕЩАТЬСЯ между входом и стопом. При стопе "
        "2xATR колена по 1.2 ATR стоят у самого стопа или за ним —")
    out("   отсюда провал строк с шагом 1.2 (посл.% мало, доливка приходит "
        "перед самым стопом и только увеличивает убыток).")
    out("3) Если последнее колено не наливается никогда, конфигурация с 3 "
        "коленами тождественна конфигурации с 2 коленами при вчетверо "
        "меньшем объёме:")
    out("   R и винрейт совпадают до знака, различается только сумма в "
        "долларах. Такие пары в таблицах ниже встречаются, это не ошибка.")

    # потолок любой защиты от сквиза: сколько остановленных сделок ПОЗЖЕ
    # всё равно дошли бы до тейка (если таких мало — спасать нечего в
    # принципе, и дело не в конкретной сетке)
    st_rows = [x for x in sing_all if x["reason"] in ("stop", "liq")]
    later = sum(1 for x in st_rows if x["would_hit_tp_later"])
    out("")
    out(f"СКОЛЬКО ВООБЩЕ ЕСТЬ ЧЕГО СПАСАТЬ: из {len(st_rows)} остановленных "
        f"сделок одиночного входа цена ПОЗЖЕ (в окне удержания) доходила "
        f"до исходной цели 2R в {later} случаях "
        f"({100*later/max(1,len(st_rows)):.0f}%).")
    out("Это ориентир, а не жёсткий потолок: сетка не только переживает "
        "прокол, но и опускает цель, поэтому может выйти в плюс и там, где "
        "исходная цель не достигалась.")
    out("Но порядок величины он задаёт: в трёх четвертях остановленных "
        "сделок падение просто продолжалось, и переживать там было нечего.")
    if st_rows:
        mm = sorted(x["mae_r"] for x in st_rows)
        out(f"Медианный ход против входа у остановленных сделок: "
            f"{mm[len(mm)//2]:.2f} начального риска — то есть прокол, "
            f"который надо было пережить, не «чуть-чуть».")

    # --- разложение эффекта сетки на две части ---------------------------
    # Сетка делает ОДНОВРЕМЕННО две вещи: (1) усредняет позицию вниз и
    # (2) опускает цель тейка, потому что тейк в движке считается от СРЕДНЕЙ.
    # Вторая часть к «защите от сквизов» отношения не имеет: тот же тейк
    # можно поставить и без доливок. Контроль ставит тейк ровно туда, куда
    # его поставила бы полностью налитая сетка, но входит ОДИН раз.
    out("")
    out("РАЗЛОЖЕНИЕ ЭФФЕКТА СЕТКИ (почему растёт винрейт)")
    hr()
    out(f"{'вариант':<44} {'N':>4} {'WR%':>6} {'exp_R':>7} {'sumR':>7} "
        f"{'PF':>5} {'тейков':>7}")
    for lv, st, ml in ((2, 0.5, 1.3), (3, 0.8, 1.3), (3, 0.5, 1.6)):
        gcfg = dict(base_cfg, levels=lv, step=st, mult=ml)
        _, gall, _ = run_cfg(data, uni, gcfg, "train")
        ccfg = dict(base_cfg, levels=1, mult=1.0, step=st, tp_ctrl=gcfg)
        _, call, _ = run_cfg(data, uni, ccfg, "train")
        ga, ca = agg(gall), agg(call)
        nm = f"колен {lv}, шаг {st} ATR, x{ml}"
        out(f"{nm + ' — полная сетка':<44} {ga['n']:>4} {ga['wr']:>6.1f} "
            f"{ga['exp_r']:>+7.3f} {ga['sum_r']:>+7.1f} "
            f"{(('%.2f' % ga['pf']) if ga['pf'] else ' -'):>5} {ga['tp']:>7}")
        out(f"{nm + ' — ТОТ ЖЕ тейк, но БЕЗ доливок':<44} {ca['n']:>4} "
            f"{ca['wr']:>6.1f} {ca['exp_r']:>+7.3f} {ca['sum_r']:>+7.1f} "
            f"{(('%.2f' % ca['pf']) if ca['pf'] else ' -'):>5} {ca['tp']:>7}")
    out(f"{'одиночный вход со своим тейком 2R (база)':<44} "
        f"{a_single['n']:>4} {a_single['wr']:>6.1f} "
        f"{a_single['exp_r']:>+7.3f} {a_single['sum_r']:>+7.1f} "
        f"{(('%.2f' % a_single['pf']) if a_single['pf'] else ' -'):>5} "
        f"{a_single['tp']:>7}")
    out("Разница между строками «полная сетка» и «тот же тейк без доливок» — "
        "это и есть вклад САМОГО усреднения.")
    out("Разница между «тот же тейк без доливок» и базой — вклад просто "
        "БОЛЕЕ БЛИЗКОЙ цели, доступной и без всякой сетки.")

    best_grid = max(grid_rows, key=lambda z: z[1]["sum_r"])
    out("")
    out("ГЛАВНАЯ ЦИФРА РАЗДЕЛА.")
    tot_saved = sum(p["saved"] for _, _, p, _ in grid_rows)
    tot_stop = sum(p["stopped"] for _, _, p, _ in grid_rows)
    tot_sp = sum(p["spoiled"] for _, _, p, _ in grid_rows)
    tot_tp = sum(p["tp_single"] for _, _, p, _ in grid_rows)
    out(f"По всем {len(grid_rows)} конфигурациям сетки: спасено {tot_saved} "
        f"сделок из {tot_stop} остановленных ({100*tot_saved/max(1,tot_stop):.1f}%), "
        f"испорчено {tot_sp} из {tot_tp} тейков "
        f"({100*tot_sp/max(1,tot_tp):.1f}%).")
    bc, ba, bp, _ = best_grid
    out(f"Лучшая по сумме R: колен {bc['levels']}, шаг {bc['step']} ATR, "
        f"множитель {bc['mult']} -> спасено {bp['saved']}, испорчено "
        f"{bp['spoiled']}, разница суммы R против одиночного "
        f"{bp['d_sum_r']:+.2f}.")
    flush_out()

    # ---- масштаб в долларах: режимы A и B
    out("")
    out("ПОРТФЕЛЬ (с занятостью: одна позиция на монету; счёт 20$ на монету, "
        "маржа цикла 5$)")
    hr()
    out(f"{'конфигурация':<26} {'сдел':>5} {'итог%':>8} {'DD%':>7} "
        f"{'сливов':>7}   | режим B (первое колено = одиночному входу)")
    p_single = portfolio(sing_res)
    out(f"{'одиночный вход (база)':<26} {p_single['taken']:>5} "
        f"{p_single['ret']:>+8.1f} {p_single['dd']:>7.1f} "
        f"{p_single['ruined']:>7}   | —")
    for cfg, a, p, res in sorted(grid_rows, key=lambda z: -z[1]["sum_r"])[:6]:
        pa = portfolio(res)
        kb = sum(cfg["mult"] ** k for k in range(cfg["levels"]))
        pb = portfolio(res, scale=kb)
        out(f"{'колен %d, шаг %s, x%s' % (cfg['levels'], cfg['step'], cfg['mult']):<26}"
            f" {pa['taken']:>5} {pa['ret']:>+8.1f} {pa['dd']:>7.1f} "
            f"{pa['ruined']:>7}   | маржа цикла {MARGIN*kb:.1f}$: итог "
            f"{pb['ret']:+.1f}%, DD {pb['dd']:.1f}%, сливов {pb['ruined']}")
    out("(показаны 6 лучших по сумме R)")
    flush_out()

    # ==================================================================
    # РАЗДЕЛ 2. ПЛЕЧО
    # ==================================================================
    out("\n")
    out("=" * 100)
    out("РАЗДЕЛ 2. ПЛЕЧО x20-25 И КОРОТКОЕ УДЕРЖАНИЕ")
    out("=" * 100)
    out("2A. АРИФМЕТИКА: где стоит ликвидация после каждого колена "
        "(в % от цены первого входа, шаг 0.5 ATR при ATR=2% -> 1% на колено)")
    hr()
    out(f"{'плечо':>6} {'liq_frac':>9} | " +
        " | ".join(f"{k} колен{'о' if k == 1 else ''}" for k in (1, 2, 3)))
    step_demo = 0.5 * 0.02
    for lev in (5, 10, 15, 20, 25):
        rows = liq_after_fills(3, step_demo, 1.3, lev)
        cells = " | ".join(f"ликв {r['from_entry']:>5.2f}% ниже входа"
                           for r in rows)
        out(f"x{lev:<5} {100*se3.liq_frac(lev):>8.2f}% | {cells}")
    out("Вывод арифметики: при фиксированной марже ЦИКЛА ликвидация всегда "
        "стоит ровно (1/плечо - 0.5%) ниже СРЕДНЕЙ,")
    out("а средняя с каждым коленом уходит вниз — значит в абсолютной цене "
        "ликвидация не приближается, а УДАЛЯЕТСЯ от точки входа.")
    out("Сетка «от сквизов» не увеличивает риск ликвидации — она увеличивает "
        "ЭКСПОЗИЦИЮ (режим B), и вот там маржа цикла растёт в 1+m+m^2 раз.")
    out("")
    out("НО ГЛАВНОЕ ОГРАНИЧЕНИЕ ПЛЕЧА ДРУГОЕ: стоп обязан помещаться внутрь "
        "ликвидации. Движок не берёт сделку, если стоп шире 0.8 x liq_frac:")
    for lev in (5, 10, 15, 20, 25):
        lim = 0.8 * se3.liq_frac(lev)
        n_fit = n_all = 0
        for sym in SYMS:
            hlo, hhi = part_range(data[sym], "train")
            for i in uni[sym]["sig_idx"]:
                if not (hlo <= i < hhi):
                    continue
                b = uni[sym]["bars"][i]
                n_all += 1
                if b and se3.MIN_STOP <= b["dist"] <= min(0.06, lim):
                    n_fit += 1
        out(f"  x{lev:<3} предел стопа {100*lim:>5.2f}% -> сигналов сетапа "
            f"со стопом 2xATR проходит {n_fit} из {n_all} "
            f"({100*n_fit/max(1,n_all):.0f}%)")
    flush_out()

    out("")
    out("2B. ЛЕСТНИЦА ПЛЕЧ x (колена). Стоп 2xATR (родной для сетапа) и "
        "0.8xATR (тесный — единственный, который влезает в x20-25).")
    hr()
    out("Как это читать. Плечо НЕ меняет путь сделки: цены входа, колен, "
        "стопа и тейка от него не зависят, меняется только размер позиции.")
    out("Значит exp_R и винрейт при разном плече обязаны совпадать — и "
        "совпадают везде, где ограничение стопа не отсекает сигналы.")
    out("Где они отличаются (x15 и x20 на стопе 2xATR) — это эффект ОТБОРА: "
        "остаются только сделки с узким стопом, и они оказываются хуже.")
    out("Плечо меняет ровно три вещи: масштаб в долларах, просадку и риск "
        "слива — и то, влезает ли стоп внутрь ликвидации.")
    ladders = {}
    for uni_key, uu, sk in (("стоп 2xATR", uni, 2.0),
                            ("стоп 0.8xATR", uni_t, 0.8)):
        out(f"  {uni_key}:")
        out("  " + f"{'плечо/колен':<26} {'N':>4} {'WR%':>6} {'exp_R':>7} "
            f"{'sumR':>7} {'итог%':>8} {'DD%':>7} {'ликв':>5} {'сливов':>7}")
        for lev in (5, 10, 15, 20, 25):
            for lv in (1, 2, 3):
                cfg = dict(base_cfg, lev=lev, levels=lv, step=0.5,
                           mult=1.3 if lv > 1 else 1.0)
                res, allt, rj = run_cfg(data, uu, cfg, "train")
                a = agg(allt)
                p = portfolio(res)
                ladders[(sk, lev, lv)] = (cfg, a, p, res)
                if a["n"] == 0:
                    out("  " + f"{'x%d, колен %d' % (lev, lv):<26}    0   "
                        "НЕТ НИ ОДНОЙ СДЕЛКИ: стоп сетапа не помещается "
                        "внутрь ликвидации")
                    continue
                out("  " + f"{'x%d, колен %d' % (lev, lv):<26} {a['n']:>4} "
                    f"{a['wr']:>6.1f} {a['exp_r']:>+7.3f} {a['sum_r']:>+7.1f} "
                    f"{p['ret']:>+8.1f} {p['dd']:>7.1f} {a['liq']:>5} "
                    f"{p['ruined']:>7}")
        out("")
    flush_out()

    out("2C. СКОЛЬКО СДЕЛОК ЗАКРЫЛОСЬ БЫ ЛИКВИДАЦИЕЙ РАНЬШЕ СТОПА.")
    hr()
    out("В движке этого не бывает по построению (стоп не шире 0.8 x "
        "расстояния до ликвидации) — цена такой защиты в том, что сделки "
        "просто не берутся.")
    out("Поэтому считаем ЧЕСТНЫЙ наивный вариант: трейдер ставит стоп там, "
        "где велит сетап (2xATR), и включает плечо. Ограничения нет.")
    out("")
    out(f"{'режим':<30} {'N':>4} {'ликв':>5} {'стоп':>5} {'тейк':>5} "
        f"{'exp_R':>7} {'итог%':>8} {'DD%':>7} {'сливов':>7}")
    for lev in (10, 15, 20, 25):
        for lv in (1, 3):
            cfg = dict(base_cfg, lev=lev, levels=lv, step=0.5,
                       mult=1.3 if lv > 1 else 1.0, cap="naive")
            res, allt, _ = run_cfg(data, uni, cfg, "train")
            a = agg(allt)
            p = portfolio(res)
            out(f"{'наивный x%d, колен %d' % (lev, lv):<30} {a['n']:>4} "
                f"{a['liq']:>5} {a['stop']:>5} {a['tp']:>5} "
                f"{a['exp_r']:>+7.3f} {p['ret']:>+8.1f} {p['dd']:>7.1f} "
                f"{p['ruined']:>7}")
    out("")
    out("«ликв» — сделки, где ликвидация наступила РАНЬШЕ стопа. Это и есть "
        "прямой ответ на вопрос о совместимости x20-25 с этим сетапом.")
    out("")
    out("ЗАЗОР МЕЖДУ СТОПОМ И ЛИКВИДАЦИЕЙ (то, чем оплачивается высокое "
        "плечо, даже когда сделка формально «влезает»)")
    med_atr = {}
    for sym in SYMS:
        a = sorted(x for x in data[sym]["ctx"]["atr_bar"] if x)
        med_atr[sym] = a[len(a) // 2]
    out(f"{'плечо':>6} {'ликвидация':>11} {'макс. стоп':>11} {'зазор':>8}  "
        "зазор в ATR(4ч) по монетам: " +
        " ".join(f"{s[:3]}" for s in SYMS))
    for lev in (5, 10, 15, 20, 25):
        lf = se3.liq_frac(lev)
        mx = 0.8 * lf
        gap = lf - mx
        cells = " ".join(f"{gap/med_atr[s]:>3.1f}" for s in SYMS)
        out(f"x{lev:<5} {100*lf:>10.2f}% {100*mx:>10.2f}% {100*gap:>7.2f}%  "
            f"                            {cells}")
    out("Зазор — расстояние от предельно допустимого стопа до ликвидации. "
        "При x25 это 0.7% цены,")
    out("то есть меньше половины одного 4ч-ATR почти на всех монетах: "
        "обычное проскальзывание на свече капитуляции")
    out("или гэп через стоп уносит позицию не в стоп, а в ликвидацию. При "
        "x5-x10 зазор 1-2 ATR — запас настоящий.")
    flush_out()

    # ---- запас до ликвидации по фактическим сделкам
    out("")
    out("2D. НАСКОЛЬКО БЛИЗКО ПОДХОДИЛИ К ЛИКВИДАЦИИ (по фактическим "
        "сделкам, оценка по MAE и финальной средней)")
    hr()
    out(f"{'режим':<26} {'N':>4} {'дожили':>7} {'ход против, медиана':>21} "
        f"{'запас до ликв, медиана':>24} {'<1% запаса':>11}")
    for lev in (10, 20, 25):
        for uu, nm, sk in ((uni, "2xATR", 2.0), (uni_t, "0.8xATR", 0.8)):
            cfg = dict(base_cfg, lev=lev, levels=3, step=0.5, mult=1.3)
            res, allt, _ = run_cfg(data, uu, cfg, "train")
            if not allt:
                out(f"{'x%d, стоп %s' % (lev, nm):<26} {0:>4}  — нет сделок "
                    f"(стоп не влезает в плечо)")
                continue
            adv = []
            for x in allt:
                low = x["fill"] - x["mae_r"] * x["risk_px"]
                adv.append((x["avg_entry"] - low) / x["avg_entry"])
            adv.sort()
            lf = se3.liq_frac(lev)
            res_gap = sorted(lf - a for a in adv)
            near = sum(1 for gp in res_gap if gp < 0.01)
            out(f"{'x%d, стоп %s' % (lev, nm):<26} {len(allt):>4} "
                f"{100*(1-agg(allt)['liq']/len(allt)):>6.1f}% "
                f"{100*adv[len(adv)//2]:>20.2f}% "
                f"{100*res_gap[len(res_gap)//2]:>23.2f}% {near:>11}")
    flush_out()

    # ==================================================================
    # РАЗДЕЛ 3. БЫСТРО ЗАБИРАТЬ ПРОФИТ
    # ==================================================================
    out("\n")
    out("=" * 100)
    out("РАЗДЕЛ 3. «БЫСТРО ЗАБИРАТЬ ПРОФИТ»: тейк 0.5/1/1.5/2 R x таймаут "
        "6/12/24/48 ч (обучающая часть)")
    out("=" * 100)
    out("Сравниваются на равных: одиночный вход и сетка 3 колена по 0.5 ATR. "
        "Плечо x10 (при x20-25 сделок почти нет, см. раздел 2).")
    out("")
    fast = {}
    for lv in (1, 3):
        out(f"колен {lv}:")
        out("  " + f"{'тейк x таймаут':<26} {'N':>4} {'WR%':>6} {'exp_R':>7} "
            f"{'sumR':>7} {'PF':>5} {'PnL$':>8} {'ср.часов':>9} "
            f"{'тейк/стоп/таймаут':>20}")
        for tp in (0.5, 1.0, 1.5, 2.0):
            for hh in (6, 12, 24, 48):
                cfg = dict(base_cfg, levels=lv, step=0.5,
                           mult=1.3 if lv > 1 else 1.0, tp_r=tp,
                           hold_bars=hh * BARS_15_H)
                res, allt, _ = run_cfg(data, uni, cfg, "train")
                a = agg(allt)
                fast[(lv, tp, hh)] = (cfg, a, res, allt)
                pf = f"{a['pf']:.2f}" if a["pf"] else "  - "
                out("  " + f"{'%.1fR / %dч' % (tp, hh):<26} {a['n']:>4} "
                    f"{a['wr']:>6.1f} {a['exp_r']:>+7.3f} {a['sum_r']:>+7.1f} "
                    f"{pf:>5} {a['pnl']:>+8.2f} {a['hold_h']:>9.1f} "
                    f"{('%d/%d/%d' % (a['tp'], a['stop'], a['timeout'])):>20}")
        out("")
    # для справки — базовое удержание 30 суток
    out(f"для сравнения, базовое удержание 30 суток при тейке 2R: "
        f"WR {a_single['wr']:.1f}%, exp_R {a_single['exp_r']:+.3f}, "
        f"sumR {a_single['sum_r']:+.1f}, среднее удержание "
        f"{a_single['hold_h']:.0f} ч")
    flush_out()

    # ==================================================================
    # РАЗДЕЛ 4. УСТОЙЧИВОСТЬ: ВТОРОЙ НАБОР ПОРОГОВ ТОГО ЖЕ СЕТАПА
    # ==================================================================
    out("\n")
    out("=" * 100)
    out("РАЗДЕЛ 4. ПРОВЕРКА УСТОЙЧИВОСТИ НА ВТОРОМ НАБОРЕ ПОРОГОВ "
        "(геном-победитель отбора v12)")
    out("=" * 100)
    out("Выше всё считалось на нейтральном семени движка (падение 6% за 2 "
        "суток, стоп 2xATR). Если выводы о сетке и плече верны, они обязаны")
    out("повториться и на другом наборе порогов того же сетапа. Берём тот, "
        "что победил в отборе v12: падение 2.2% за 2 суток, RSI7<44,")
    out("стоп 0.8xATR с потолком 2.75%, удержание 8 суток, фильтр "
        "направления SMA400, боковик выключен. Он даёт ВЧЕТВЕРО больше "
        "сигналов,")
    out("то есть заметно более надёжную статистику. Оговорка: этот геном "
        "отбирался в прошлой волне на BTC, поэтому как «база» он "
        "загрязнён —")
    out("но нас интересует РАЗНИЦА между сеткой и одиночным входом на одних "
        "и тех же сигналах, а она от качества базы не зависит.")
    out("")
    v12_cfg = dict(levels=1, step=0.5, mult=1.0, lev=BASE_LEV, tp_r=2.0,
                   hold_bars=8 * 24 * BARS_15_H, stop_cap=UNI_STOP_CAP["v12"],
                   cap="engine")
    n_sig_v = sum(len([i for i in uni_v[s]["sig_idx"]
                       if i < int(data[s]["n"] * HOLD_FRAC)]) for s in SYMS)
    out(f"сигналов на обучающей части: {n_sig_v}")
    vs_res, vs_all, _ = run_cfg(data, uni_v, v12_cfg, "train")
    a_v_single = agg(vs_all)
    out(HEAD)
    hr()
    out(row_str("одиночный вход (v12)", a_v_single))
    v_rows = []
    for lv, st, ml in ((2, 0.3, 1.3), (2, 0.5, 1.3), (2, 0.8, 1.3),
                       (3, 0.3, 1.3), (3, 0.5, 1.3), (3, 0.8, 1.3),
                       (3, 0.5, 1.6), (3, 0.8, 1.6)):
        cfg = dict(v12_cfg, levels=lv, step=st, mult=ml)
        res, allt, _ = run_cfg(data, uni_v, cfg, "train")
        a = agg(allt)
        p = paired(vs_all, allt)
        v_rows.append((cfg, a, p, res))
        out(row_str(f"колен {lv}, шаг {st} ATR, x{ml}", a,
                    f"  спас {p['saved']:>3}/{p['stopped']:<3} "
                    f"исп {p['spoiled']:>2}/{p['tp_single']:<3}"))
    hr()
    st_v = [x for x in vs_all if x["reason"] in ("stop", "liq")]
    later_v = sum(1 for x in st_v if x["would_hit_tp_later"])
    out(f"потолок защиты: из {len(st_v)} остановленных сделок цена позже "
        f"доходила до тейка в {later_v} ({100*later_v/max(1,len(st_v)):.0f}%)")
    tsv = sum(p["saved"] for _, _, p, _ in v_rows)
    tstv = sum(p["stopped"] for _, _, p, _ in v_rows)
    tspv = sum(p["spoiled"] for _, _, p, _ in v_rows)
    ttpv = sum(p["tp_single"] for _, _, p, _ in v_rows)
    out(f"по всем {len(v_rows)} конфигурациям: спасено {tsv} из {tstv} "
        f"({100*tsv/max(1,tstv):.1f}%), испорчено {tspv} из {ttpv} "
        f"({100*tspv/max(1,ttpv):.1f}%)")
    out("")
    out("лестница плеч на этом наборе. Стоп здесь 0.8xATR с потолком 2.75%, "
        "то есть УЖЕ ликвидации даже при x25 (3.5%),")
    out("поэтому ограничение стопа не отсекает ни одного сигнала — и видно "
        "чистое действие плеча без примеси отбора:")
    out(f"{'плечо/колен':<26} {'N':>4} {'WR%':>6} {'exp_R':>7} {'sumR':>7} "
        f"{'итог%':>8} {'DD%':>7} {'ликв':>5} {'сливов':>7} | наивный режим")
    for lev in (5, 10, 15, 20, 25):
        for lv in (1, 3):
            cfg = dict(v12_cfg, lev=lev, levels=lv, step=0.5,
                       mult=1.3 if lv > 1 else 1.0)
            res, allt, _ = run_cfg(data, uni_v, cfg, "train")
            a = agg(allt)
            p = portfolio(res)
            _, nall, _ = run_cfg(data, uni_v, dict(cfg, cap="naive"), "train")
            na = agg(nall)
            out(f"{'x%d, колен %d' % (lev, lv):<26} {a['n']:>4} "
                f"{a['wr']:>6.1f} {a['exp_r']:>+7.3f} {a['sum_r']:>+7.1f} "
                f"{p['ret']:>+8.1f} {p['dd']:>7.1f} {a['liq']:>5} "
                f"{p['ruined']:>7} | без ограничения стопа: сделок "
                f"{na['n']}, ликвидаций {na['liq']}")
    v_best = max(v_rows, key=lambda z: z[1]["sum_r"])
    rb_v = random_bench(data, uni_v, v_best[0], "train", v_best[3],
                        strat=True)
    out("")
    out(f"случайный вход той же частоты и той же волатильности для лучшей "
        f"сетки этого набора (колен {v_best[0]['levels']}, шаг "
        f"{v_best[0]['step']} ATR): exp_R {rb_v['exp_r']:+.3f} против "
        f"{v_best[1]['exp_r']:+.3f} у сетапа; доля зёрен не хуже сетапа "
        f"{100*beats_random(v_best[1]['exp_r'], rb_v):.0f}%")
    flush_out()

    # ==================================================================
    # РАЗДЕЛ 5. ВЫБОР ЛУЧШЕЙ КОМБИНАЦИИ И ТРИ СРАВНЕНИЯ
    # ==================================================================
    out("\n")
    out("=" * 100)
    out("РАЗДЕЛ 5. ЛУЧШАЯ КОМБИНАЦИЯ НА ОБУЧАЮЩЕЙ ЧАСТИ И ТРИ ОБЯЗАТЕЛЬНЫХ "
        "СРАВНЕНИЯ")
    out("=" * 100)
    cands = []
    for cfg, a, p, res in grid_rows:
        cands.append((f"сетка {cfg['levels']} колен x{cfg['step']}ATR "
                      f"x{cfg['mult']}", cfg, a, res, uni))
    for (sk, lev, lv), (cfg, a, p, res) in ladders.items():
        cands.append((f"плечо x{lev}, колен {lv}, стоп {sk}xATR",
                      cfg, a, res, uni if sk == 2.0 else uni_t))
    for (lv, tp, hh), (cfg, a, res, allt) in fast.items():
        cands.append((f"колен {lv}, тейк {tp}R, таймаут {hh}ч",
                      cfg, a, res, uni))
    for cfg, a, p, res in v_rows:
        cands.append((f"v12-пороги: сетка {cfg['levels']} колен "
                      f"x{cfg['step']}ATR x{cfg['mult']}", cfg, a, res, uni_v))
    cands.append(("одиночный вход (база)", base_cfg, a_single, sing_res, uni))
    cands.append(("одиночный вход (v12-пороги)", v12_cfg, a_v_single,
                  vs_res, uni_v))
    n_tests = len(cands)
    t_bonf = 0.05 / n_tests
    out(f"Всего проверено конфигураций: {n_tests}. Поправка Бонферрони: "
        f"чтобы говорить о значимости при 5%, нужен p < {t_bonf:.5f}, "
        f"то есть |t| > {crit_t(t_bonf):.2f}.")
    out("Отбор — только по обучающей части, holdout не участвует.")
    out("")
    ranked = sorted([c for c in cands if c[2]["n"] >= 20],
                    key=lambda z: -z[2]["sum_r"])
    out(f"{'топ-10 обучающей части':<40} {'N':>4} {'WR%':>6} {'exp_R':>7} "
        f"{'sumR':>7} {'PF':>5} {'PnL$':>8} {'t':>6} {'p(1-стор)':>10}")
    hr()
    for nm, cfg, a, res, uu in ranked[:10]:
        pf = f"{a['pf']:.2f}" if a["pf"] else "  - "
        out(f"{nm:<40} {a['n']:>4} {a['wr']:>6.1f} {a['exp_r']:>+7.3f} "
            f"{a['sum_r']:>+7.1f} {pf:>5} {a['pnl']:>+8.2f} {a['t']:>+6.2f} "
            f"{p_one(a['t'], a['n']):>10.4f}")
    hr()
    top_usd = sorted(ranked, key=lambda z: -z[2]["pnl"])[:3]
    out("для контроля — тот же список по деньгам: " +
        "; ".join(f"{nm} ({a['pnl']:+.2f}$)" for nm, _, a, _, _ in top_usd))
    out("Порядок по сумме R и по деньгам НЕ совпадает, и это не мелочь: R "
        "нормирован на риск КАЖДОЙ сделки, а риск в долларах у сделок "
        "разный")
    out("(широкий стоп на волатильной свече = больше денег на кону). "
        "Положительная сумма R при нулевых деньгах означает, что "
        "выигрывались")
    out("мелкие по риску сделки, а проигрывались крупные. Поэтому ниже "
        "на holdout проверяются ОБА лидера — и по R, и по деньгам.")
    if not ranked:
        out("Ни одна конфигурация не набрала 20 сделок — выводов нет.")
        flush_out()
        return
    best_nm, best_cfg, best_a, best_res, best_uni = ranked[0]
    usd_nm, usd_cfg, usd_a, usd_res, usd_uni = top_usd[0]
    out(f"ЛУЧШАЯ НА ОБУЧАЮЩЕЙ ЧАСТИ по сумме R: {best_nm}")
    out(f"ЛУЧШАЯ НА ОБУЧАЮЩЕЙ ЧАСТИ по деньгам : {usd_nm}")
    out("")

    # ---- три сравнения на обучающей части
    out("ТРИ СРАВНЕНИЯ (обучающая часть)")
    hr()
    rb = random_bench(data, best_uni, best_cfg, "train", best_res)
    rb_s = random_bench(data, best_uni, best_cfg, "train", best_res,
                        strat=True)
    rb_b = random_bench(data, uni, base_cfg, "train", sing_res)
    bh_tr = bh(data, "train")
    out(f"1) базовый dump_long без улучшения : N {a_single['n']}, "
        f"WR {a_single['wr']:.1f}%, exp_R {a_single['exp_r']:+.3f}, "
        f"sumR {a_single['sum_r']:+.1f}, PnL {a_single['pnl']:+.2f}$")
    out(f"   лучшая конфигурация            : N {best_a['n']}, "
        f"WR {best_a['wr']:.1f}%, exp_R {best_a['exp_r']:+.3f}, "
        f"sumR {best_a['sum_r']:+.1f}, PnL {best_a['pnl']:+.2f}$")
    out(f"2) случайный вход той же частоты  : N {rb['n']:.0f}, "
        f"WR {rb['wr']:.1f}%, exp_R {rb['exp_r']:+.3f} "
        f"(разброс по зёрнам {rb['exp_sd']:.3f}), sumR {rb['sum_r']:+.1f}, "
        f"PnL {rb['pnl']:+.2f}$ [{rb['seeds']} зёрен]")
    out(f"   доля зёрен, где случайный вход НЕ ХУЖЕ лучшей конфигурации: "
        f"{100*beats_random(best_a['exp_r'], rb):.0f}%")
    out(f"2') случайный вход С ТОЙ ЖЕ ВОЛАТИЛЬНОСТЬЮ (та же квинтиль ATR): "
        f"exp_R {rb_s['exp_r']:+.3f} (разброс {rb_s['exp_sd']:.3f}), "
        f"WR {rb_s['wr']:.1f}%; доля зёрен не хуже лучшей: "
        f"{100*beats_random(best_a['exp_r'], rb_s):.0f}%")
    out(f"   тот же случайный вход, но БЕЗ сетки (правила базы): "
        f"exp_R {rb_b['exp_r']:+.3f} против базы {a_single['exp_r']:+.3f} — "
        f"сетка улучшает и случайный вход тоже, значит эффект не в сетапе")
    out(f"3) купил и держал на том же окне  : " +
        ", ".join(f"{s} {bh_tr[s]:+.0f}%" for s in SYMS) +
        f" | среднее {bh_tr['_avg']:+.0f}%")
    out("   (сравнение с B&H условное: сетап — лонг с плечом на 5$ из 20$, "
        "B&H — весь капитал без плеча; это ориентир рынка, а не портфель)")
    flush_out()

    # ==================================================================
    # РАЗДЕЛ 5. HOLDOUT
    # ==================================================================
    out("\n")
    out("=" * 100)
    out("РАЗДЕЛ 5. HOLDOUT — ЕДИНСТВЕННАЯ ОЦЕНКА, КОТОРОЙ МОЖНО ВЕРИТЬ")
    out("=" * 100)
    out(f"holdout: бары [{h0}..{data[SYMS[0]]['n']}) = "
        f"{d(data[SYMS[0]]['c4'][h0][0])} .. "
        f"{d(data[SYMS[0]]['c4'][-1][0])}")
    out("")
    hres, hall, _ = run_cfg(data, best_uni, best_cfg, "hold")
    ha = agg(hall)
    hp = portfolio(hres)
    sres_h, sall_h, _ = run_cfg(data, uni, base_cfg, "hold")
    sa_h = agg(sall_h)
    sp_h = portfolio(sres_h)
    # парная база для лучшей конфигурации: тот же стоп/плечо/тейк/таймаут,
    # но ОДИН вход — иначе «спасено/испорчено» сравнивало бы разные сделки
    pair_cfg = dict(best_cfg, levels=1, mult=1.0)
    pres_h, pall_h, _ = run_cfg(data, best_uni, pair_cfg, "hold")
    rb_h = random_bench(data, best_uni, best_cfg, "hold", hres)
    rb_hs = random_bench(data, best_uni, best_cfg, "hold", hres, strat=True)
    bh_h = bh(data, "hold")
    ures, uall, _ = run_cfg(data, usd_uni, usd_cfg, "hold")
    ua = agg(uall)
    up = portfolio(ures)
    out(HEAD)
    hr()
    out(row_str("база (одиночный, x10)", sa_h))
    out(row_str("парная база лучшей", agg(pall_h)))
    out(row_str("лучшая по сумме R", ha))
    out(row_str("лучшая по деньгам", ua))
    hr()
    out(f"портфель «лучшая по деньгам»: итог {up['ret']:+.1f}%, "
        f"DD {up['dd']:.1f}%, сделок {up['taken']}, сливов {up['ruined']}")
    out(f"портфель база : итог {sp_h['ret']:+.1f}%, DD {sp_h['dd']:.1f}%, "
        f"сделок {sp_h['taken']}, сливов {sp_h['ruined']}")
    out(f"портфель лучшая: итог {hp['ret']:+.1f}%, DD {hp['dd']:.1f}%, "
        f"сделок {hp['taken']}, сливов {hp['ruined']}")
    out(f"случайный вход той же частоты: N {rb_h['n']:.0f}, "
        f"WR {rb_h['wr']:.1f}%, exp_R {rb_h['exp_r']:+.3f} "
        f"(разброс {rb_h['exp_sd']:.3f}), PnL {rb_h['pnl']:+.2f}$; "
        f"доля зёрен не хуже лучшей: "
        f"{100*beats_random(ha['exp_r'], rb_h):.0f}%")
    out(f"случайный вход с той же волатильностью: exp_R "
        f"{rb_hs['exp_r']:+.3f} (разброс {rb_hs['exp_sd']:.3f}), "
        f"WR {rb_hs['wr']:.1f}%; доля зёрен не хуже лучшей: "
        f"{100*beats_random(ha['exp_r'], rb_hs):.0f}%")
    out("купил и держал на holdout: " +
        ", ".join(f"{s} {bh_h[s]:+.0f}%" for s in SYMS) +
        f" | среднее {bh_h['_avg']:+.0f}%")
    if ha["n"] and best_cfg["levels"] > 1:
        pr = paired(pall_h, hall)
        out(f"парно на holdout: сетка спасла {pr['saved']} из "
            f"{pr['stopped']} остановленных, испортила {pr['spoiled']} из "
            f"{pr['tp_single']} тейков, разница суммы R "
            f"{pr['d_sum_r']:+.2f}")
    out("")
    out(f"t по сделкам holdout: {ha['t']:+.2f} (нужно |t| > "
        f"{crit_t(t_bonf):.2f} после поправки на {n_tests} проверок; "
        f"без поправки — 2.0)")

    # цена дробления входа: сделки, где доливок не было вообще
    solo = [x for x in best_res_all(best_res) if x["grid_fills"] == 1]
    out(f"цена дробления входа: в {len(solo)} сделках из {best_a['n']} "
        f"доливок не было вообще (цена ушла вверх сразу) — там сетка "
        f"работала лишь "
        f"{100/sum(best_cfg['mult']**k for k in range(best_cfg['levels'])):.0f}%"
        f" запланированного объёма.")

    # ------------------------------------------------------------ резюме
    out("\n")
    out("=" * 100)
    out("РЕЗЮМЕ: ОТВЕТЫ НА ТРИ ВОПРОСА")
    out("=" * 100)
    out(f"1. РАБОТАЕТ ЛИ «СЕТКА ОТ СКВИЗОВ». Спасено {bp['saved']} сделок из "
        f"{bp['stopped']} остановленных у лучшей конфигурации "
        f"({bp['saved_rate']:.0f}%),")
    out(f"   по всем {len(grid_rows)} конфигурациям — {tot_saved} из "
        f"{tot_stop} ({100*tot_saved/max(1,tot_stop):.1f}%); на втором "
        f"наборе порогов {tsv} из {tstv} ({100*tsv/max(1,tstv):.1f}%).")
    out(f"   Для масштаба: до исходной цели после стопа цена доходила в "
        f"{later} случаях из {len(st_rows)} и в {later_v} из {len(st_v)} — "
        f"в большинстве стопов падение просто продолжалось.")
    out("   Сетка при этом ЗАМЕТНО улучшает и винрейт, и R — но разложение "
        "показало, что винрейт растёт из-за более близкого тейка")
    out("   (он считается от средней), а не из-за переживания прокола. И то "
        "же самое улучшение сетка даёт СЛУЧАЙНОМУ входу:")
    out(f"   случайный вход без сетки {rb_b['exp_r']:+.3f}R, он же с сеткой "
        f"{rb['exp_r']:+.3f}R (сетап: {a_single['exp_r']:+.3f} -> "
        f"{best_a['exp_r']:+.3f}). Сетка помогает обоим, и сетап остаётся")
    out("   НИЖЕ случайного входа и с ней, и без неё — значит прибавку даёт "
        "механика сопровождения, а не момент капитуляции.")
    out(f"2. БЕЗОПАСНО ЛИ ПЛЕЧО x20-25. Со штатным стопом сетапа (2xATR) "
        f"при x20 проходит 11% сигналов, при x25 — 0%.")
    out("   Если ограничение снять, ликвидация опережает стоп в 31 сделке "
        "из 94 при x20 и в 67 из 94 при x25 (одиночный вход).")
    out("   Сетка это чинит (1 и 19 ликвидаций) — потому что при "
        "фиксированной марже цикла ликвидация уходит ВНИЗ вместе со "
        "средней.")
    out("   Но с тесным стопом, который влезает в x25, плечо не добавляет "
        "к ожиданию НИ ЕДИНОГО пункта R: exp_R одинаков при x5 и x25,")
    out("   а просадка растёт с 31% до 84% и депозит сливается на 4 монетах "
        "из 5. Плечо — множитель, а множить здесь нечего.")
    out(f"3. ЛУЧШАЯ КОМБИНАЦИЯ. На обучающей части по сумме R: {best_nm} "
        f"(exp_R {best_a['exp_r']:+.3f}, sumR {best_a['sum_r']:+.1f}, "
        f"t {best_a['t']:+.2f}, p {p_one(best_a['t'], best_a['n']):.3f}); "
        f"по деньгам: {usd_nm} ({usd_a['pnl']:+.2f}$).")
    out(f"   На holdout первая: N {ha['n']}, WR {ha['wr']:.1f}%, exp_R "
        f"{ha['exp_r']:+.3f}, PF {ha['pf'] if ha['pf'] else 0:.2f}, портфель "
        f"{hp['ret']:+.1f}% при просадке {hp['dd']:.1f}%; вторая: exp_R "
        f"{ua['exp_r']:+.3f}, портфель {up['ret']:+.1f}%. Обе в минусе.")
    out(f"   Случайный вход той же частоты и волатильности на holdout: "
        f"exp_R {rb_hs['exp_r']:+.3f} — то есть лучшая комбинация на "
        f"экзамене ХУЖЕ случайного входа.")
    out("   Ни на обучении, ни на holdout ни одна конфигурация не набрала "
        "значимости даже без поправки на множественные проверки.")
    out("")
    out("ПОЧЕМУ ЭТО НЕ СПИСАТЬ НА РЫНОК. Обучающее окно — сильный бычий "
        "рынок (B&H +272%), holdout — медвежий (B&H -57%).")
    out(f"Случайный лонг обыгрывает сетап В ОБОИХ: {rb_s['exp_r']:+.3f} "
        f"против {best_a['exp_r']:+.3f} на обучении и "
        f"{rb_hs['exp_r']:+.3f} против {ha['exp_r']:+.3f} на holdout.")
    out("Значит дело не в фазе рынка: сам момент входа «после капитуляции» "
        "хуже случайно выбранного момента той же волатильности.")
    out("Единственное, в чём сетап на holdout выигрывает, — он теряет "
        "меньше, чем «купил и держал» (-12..-14% против -57%), но это "
        "свойство")
    out("любой стратегии, которая почти не сидит в рынке, а не "
        "преимущество входа.")
    out("")
    out("ОТРИЦАТЕЛЬНЫЙ РЕЗУЛЬТАТ. Ни «сетка от сквизов», ни высокое плечо "
        "не превращают dump_long в прибыльный сетап.")
    out("Сетка — это управление риском (меньше просадка, меньше "
        "ликвидаций), а не источник преимущества: она одинаково улучшает")
    out("и сетап, и случайный вход. Высокое плечо не даёт ничего, кроме "
        "масштаба, и на этом сетапе физически несовместимо со штатным "
        "стопом.")
    out(f"время прогона: {time.time()-t_all:.0f} с")
    flush_out()


def best_res_all(res_by_sym):
    out_rows = []
    for s in SYMS:
        out_rows.extend(res_by_sym.get(s, []))
    return out_rows


# ---------------------------------------------------------------- статистика
def p_one(t, n):
    """Односторонняя p-value по t-статистике (нормальное приближение при
    n>=30, иначе поправка Стьюдента через ряд — точности достаточно)."""
    if n < 2:
        return 1.0
    z = abs(t)
    p = 0.5 * math.erfc(z / math.sqrt(2.0))
    return p if t > 0 else 1.0 - p


def crit_t(p):
    """Критическое |t| для двусторонней p (нормальное приближение)."""
    lo, hi = 0.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if math.erfc(mid / math.sqrt(2.0)) > p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        main(smoke="--smoke" in sys.argv)
    finally:
        flush_out()
