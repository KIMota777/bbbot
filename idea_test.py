# -*- coding: utf-8 -*-
"""Проверка СЫРОГО преимущества торговых идей БЕЗ оптимизации параметров.

Зачем. Двенадцать волн подбора параметров давали красивые цифры, которые не
воспроизводились при смене случайного зерна. Значит проверять надо не
"можно ли подобрать параметры", а "есть ли преимущество в САМОЙ ИДЕЕ".
Поэтому здесь НЕТ НИ ОДНОГО подбираемого числа: все настройки заданы
заранее "из учебника" и зафиксированы в коде. Меняется только сетка
проверки: 5 монет x 3 непересекающихся периода = 15 независимых
комбинаций на каждый вариант идеи.

Идеи (каждая с 2-3 фиксированными вариантами):
  A  капитуляция-лонг   : падение >= X% за 2 суток + зелёная свеча + RSI14<30
  B  пробой-лонг        : закрытие выше максимума N суток (Дончиан 20/55)
  C  тренд-пулбэк-шорт  : цена < SMA200, отскок к SMA20, красная свеча
  D  перегрев-шорт      : рост >= X% за 2 суток + RSI14>70 + красная свеча
  E  сетка контр-тренд  : RSI(7) 30/70, сетка усреднения (движок e2.run5)

Издержки и порядок исполнения — ровно проектные (evolution2 / signal_engine2):
  TAKER 0.055%, MAKER 0.02%, SLIP 0.03%, FUND_8H 0.01% за 8ч,
  стоп и тейк в одной свече -> СТОП, ликвидация по 1/плечо - 0.5%.
  Плечо x5, маржа $5 на сделку, стартовая база $20, одна позиция.

A-D: сигнал определяется по ЗАКРЫТИЮ 4ч-свечи, вход по ОТКРЫТИЮ следующей,
ведение сделки по 15м-свечам (точное разрешение внутри бара).
E: 15м, механика сетки целиком из evolution2.run5 (не переписана).

Запуск:  python idea_test.py      (вывод дублируется в idea_test_out.txt)
"""

import bisect
import math
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest_rsi_grid as bg      # calc_rsi
import evolution as ev              # fetch, rolling_extremes, calc_atr_pct
import evolution2 as e2             # ЧЕСТНЫЙ движок сетки run5

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------- константы
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT"]
DAYS = 1150

TAKER, MAKER = e2.TAKER, e2.MAKER      # 0.055% / 0.02%
SLIP = e2.SLIP                         # 0.03%
FUND_8H = e2.FUND_8H                   # 0.01% за 8ч
MMR = 0.005                            # поддерживающая маржа Bybit
LEV = 5                                # плечо фиксировано для всех идей
MARGIN = 5.0                           # маржа на сделку, USDT
START = 20.0                           # стартовая база, USDT
MIN_STOP = 0.005                       # стоп ближе 0.5% — шум, расширяем

BARS4H_DAY = 6                         # 4ч-баров в сутках
BARS15_DAY = 96                        # 15м-баров в сутках
MAX_HOLD_DAYS = 60                     # единый таймаут удержания для A-D
MAX_HOLD_15 = MAX_HOLD_DAYS * BARS15_DAY

LIQ_FRAC = 1.0 / LEV - MMR             # 19.5% для x5

# --- ЗАРАНЕЕ ОБЪЯВЛЕННЫЕ критерии вердикта (до просмотра результатов) ---
CRIT_T = 2.0            # t-статистика по пулу сделок варианта
CRIT_COMBOS = 10        # прибыльных комбинаций из 15
CRIT_PF = 1.2           # порог PF для доли "хороших" комбинаций
N_VARIANTS = 12         # всего проверяемых вариантов (для поправки Бонферрони)


# ---------------------------------------------------------------- утилиты
def sma(vals, n):
    """Простая скользящая средняя, только данные <= i (без заглядывания)."""
    out = [None] * len(vals)
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def binom_p_ge(k, n):
    """P(X >= k) при n испытаниях и p=0.5 (точная биномиальная)."""
    return sum(math.comb(n, j) for j in range(k, n + 1)) / (2.0 ** n)


def p_two_sided(t):
    """Двусторонний p по нормальному приближению."""
    return math.erfc(abs(t) / math.sqrt(2.0))


def z_for_p(p):
    """Двусторонний критический z для заданного p."""
    lo, hi = 0.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if math.erfc(mid / math.sqrt(2.0)) > p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def fmt_date(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def fmt_pf(pf):
    if pf is None:
        return "  inf"
    return f"{pf:5.2f}"


def bootstrap_p(rs, seed=7):
    """Доля бутстрэп-выборок со средним <= 0. Устойчивее t-статистики,
    когда распределение R с тяжёлым правым хвостом (трендовые выходы)."""
    n = len(rs)
    if n < 5:
        return 1.0
    n_iter = 20000 if n <= 500 else 3000
    rnd = random.Random(seed)
    bad = 0
    for _ in range(n_iter):
        if sum(rnd.choices(rs, k=n)) <= 0:
            bad += 1
    return bad / n_iter


def concentration(rs):
    """Доля суммарного положительного R, которую дают 3 лучшие сделки."""
    pos = sorted((x for x in rs if x > 0), reverse=True)
    tot = sum(pos)
    if tot <= 0:
        return 1.0
    return sum(pos[:3]) / tot


# ---------------------------------------------------------------- индикаторы
def build_ind(c4):
    """Все ряды причинные: на баре i используются только данные <= i."""
    close = [c[4] for c in c4]
    open_ = [c[1] for c in c4]
    atr_pct = ev.calc_atr_pct(c4, n=14)
    atr_abs = [(atr_pct[i] * close[i]) if atr_pct[i] else None
               for i in range(len(c4))]
    W2 = 2 * BARS4H_DAY                       # 2 суток = 12 баров
    low2d, high2d = ev.rolling_extremes(c4, W2 + 1)     # окно [i-12..i]
    dl20, dh20 = ev.rolling_extremes(c4, 20 * BARS4H_DAY)   # экстремум 20 суток
    _, dh55 = ev.rolling_extremes(c4, 55 * BARS4H_DAY)      # максимум 55 суток
    dl10, dh10 = ev.rolling_extremes(c4, 10 * BARS4H_DAY)   # экстремум 10 суток
    _, h3 = ev.rolling_extremes(c4, 3)
    l3, _ = ev.rolling_extremes(c4, 3)
    return dict(
        close=close, open=open_,
        rsi14=bg.calc_rsi(close, 14),
        atr14=atr_abs,
        low2d=low2d, high2d=high2d,
        dh20=dh20, dh55=dh55, dl10=dl10, dh10=dh10, dl20=dl20, h3=h3,
        sma20=sma(close, 20), sma200=sma(close, 200),
        sma20d=sma(close, 20 * BARS4H_DAY),     # "дневная" SMA20 на 4ч барах
        sma200d=sma(close, 200 * BARS4H_DAY),   # классическая дневная SMA200
        W2=W2,
    )


# ---------------------------------------------------------------- детекторы
# Каждый возвращает список сигналов: dict(i, side, stop_px|stop_dist,
# tp_mult|None, trail=None|"dl10").  Вход всегда по ОТКРЫТИЮ бара i+1.

def det_A(c4, ind, x):
    """A. Капитуляция-лонг: падение >= x за 2 суток, зелёная свеча, RSI14<30."""
    out, W = [], ind["W2"]
    cl, op, rsi = ind["close"], ind["open"], ind["rsi14"]
    for i in range(W + 1, len(c4) - 1):
        if rsi[i] is None:
            continue
        if (cl[i - W] - cl[i]) / cl[i - W] < x:
            continue
        if cl[i] <= op[i]:                      # свеча не зелёная
            continue
        if rsi[i] >= 30.0:
            continue
        out.append(dict(i=i, side="L", stop_px=ind["low2d"][i] * 0.999,
                        tp_mult=2.0, trail=None))
    return out


def det_B(c4, ind, n_days, exit_mode):
    """B. Пробой-лонг: закрытие выше максимума n_days суток (Дончиан)."""
    out = []
    cl, atr = ind["close"], ind["atr14"]
    dh = ind["dh20"] if n_days == 20 else ind["dh55"]
    warm = n_days * BARS4H_DAY + 1
    for i in range(warm, len(c4) - 1):
        if atr[i] is None or dh[i - 1] is None:
            continue
        if cl[i] <= dh[i - 1]:                  # нет пробоя максимума
            continue
        out.append(dict(i=i, side="L", stop_dist=2.0 * atr[i],
                        tp_mult=(1.5 if exit_mode == "tp" else None),
                        trail=(None if exit_mode == "tp" else "dl10")))
    return out


def det_B_mirror(c4, ind):
    """ДИАГНОСТИКА (не кандидат): зеркало B3 — пробой МИНИМУМА 20 суток вниз,
    стоп 2ATR, трейлинг по максимуму 10 суток. Если работает только лонговая
    половина, значит меряется рост рынка, а не пробой как явление."""
    out = []
    cl, atr, dl = ind["close"], ind["atr14"], ind["dl20"]
    for i in range(20 * BARS4H_DAY + 1, len(c4) - 1):
        if atr[i] is None or dl[i - 1] is None:
            continue
        if cl[i] >= dl[i - 1]:
            continue
        out.append(dict(i=i, side="S", stop_dist=2.0 * atr[i],
                        tp_mult=None, trail="dh10"))
    return out


def det_C(c4, ind, scale):
    """C. Тренд-пулбэк-шорт: цена < SMA200, отскок к SMA20, красная свеча."""
    out = []
    cl, op = ind["close"], ind["open"]
    s_slow = ind["sma200"] if scale == "4h" else ind["sma200d"]
    s_fast = ind["sma20"] if scale == "4h" else ind["sma20d"]
    for i in range(2, len(c4) - 1):
        if s_slow[i] is None or s_fast[i] is None:
            continue
        if cl[i] >= s_slow[i]:                  # тренд не нисходящий
            continue
        if c4[i][2] < s_fast[i]:                # до SMA20 не дотянулись
            continue
        if cl[i] >= s_fast[i]:                  # закрылись выше SMA20 — не отбой
            continue
        if cl[i] >= op[i]:                      # свеча не красная
            continue
        out.append(dict(i=i, side="S", stop_px=ind["h3"][i] * 1.001,
                        tp_mult=2.0, trail=None))
    return out


def det_D(c4, ind, x):
    """D. Перегрев-шорт: рост >= x за 2 суток, RSI14>70, красная свеча."""
    out, W = [], ind["W2"]
    cl, op, rsi = ind["close"], ind["open"], ind["rsi14"]
    for i in range(W + 1, len(c4) - 1):
        if rsi[i] is None:
            continue
        if (cl[i] - cl[i - W]) / cl[i - W] < x:
            continue
        if rsi[i] <= 70.0:
            continue
        if cl[i] >= op[i]:                      # свеча не красная
            continue
        out.append(dict(i=i, side="S", stop_px=ind["high2d"][i] * 1.001,
                        tp_mult=2.0, trail=None))
    return out


# ---------------------------------------------------------------- исполнение
def run_trade(sig, c4, c15, k0, ts4_idx, ind):
    """Одна сделка на 15м-свечах. Порядок проверок — как в
    signal_engine2.simulate_trade: ликвидация (если ближе стопа) -> стоп ->
    ликвидация -> тейк.  Стоп и тейк в одной свече = СТОП."""
    side = sig["side"]
    sgn = 1 if side == "L" else -1
    entry_px = c15[k0][1]
    fill = entry_px * (1 + sgn * SLIP)
    qty = MARGIN * LEV / fill
    fees = qty * fill * TAKER

    if sig.get("stop_px") is not None:
        stop = sig["stop_px"]
    else:
        stop = fill - sgn * sig["stop_dist"]
    risk_px = abs(fill - stop)
    wrong_side = (stop >= fill) if sgn == 1 else (stop <= fill)
    if wrong_side or risk_px < fill * MIN_STOP:
        risk_px = fill * MIN_STOP
        stop = fill - sgn * risk_px
    risk0 = risk_px                 # начальный риск: он и есть 1R для метрик
    tp = fill + sgn * sig["tp_mult"] * risk_px if sig["tp_mult"] else None

    liq_px = fill * (1 - sgn * LIQ_FRAC)
    liq_closer = (liq_px > stop) if sgn == 1 else (liq_px < stop)
    k_end = min(k0 + MAX_HOLD_15, len(c15) - 1)

    pnl, k_exit, reason = None, k_end, "timeout"
    for k in range(k0, k_end + 1):
        ts, o_, h_, l_, c_ = c15[k]
        fees += qty * c_ * FUND_8H / 32          # фандинг: 8ч = 32 бара по 15м
        if sig["trail"] and k > k0:
            j4 = ts4_idx.get(ts)
            if j4 is not None and j4 >= 1 and ind[sig["trail"]][j4 - 1] is not None:
                # трейлинг обновляем ТОЛЬКО по закрытым 4ч-барам (без заглядывания)
                new = ind[sig["trail"]][j4 - 1]
                if sgn == 1 and new > stop:
                    stop = new
                elif sgn == -1 and new < stop:
                    stop = new
                liq_closer = (liq_px > stop) if sgn == 1 else (liq_px < stop)
        hit_liq = (l_ <= liq_px) if sgn == 1 else (h_ >= liq_px)
        hit_stop = (l_ <= stop) if sgn == 1 else (h_ >= stop)
        hit_tp = tp is not None and ((h_ >= tp) if sgn == 1 else (l_ <= tp))
        if liq_closer and hit_liq:
            pnl, k_exit, reason = -MARGIN - fees, k, "liq"
            break
        if hit_stop:
            px = stop * (1 - sgn * SLIP)
            pnl = sgn * (px - fill) * qty - qty * px * TAKER - fees
            k_exit, reason = k, "stop"
            break
        if hit_liq:
            pnl, k_exit, reason = -MARGIN - fees, k, "liq"
            break
        if hit_tp:
            pnl = sgn * (tp - fill) * qty - qty * tp * MAKER - fees
            k_exit, reason = k, "tp"
            break
    if pnl is None:
        px = c15[k_end][4] * (1 - sgn * SLIP)
        pnl = sgn * (px - fill) * qty - qty * px * TAKER - fees
        k_exit, reason = k_end, "timeout"

    risk_usd = qty * risk0
    return dict(pnl=pnl, r=pnl / risk_usd, reason=reason, k_exit=k_exit)


def run_combo_ad(sigs, c4, c15, k_of_i4, ts4_idx, ind, t_a, t_b):
    """Последовательный прогон сигналов внутри периода [t_a, t_b).
    Одна позиция: новый вход только после выхода из предыдущей сделки."""
    trades, last_exit = [], -1
    bal, peak, max_dd, ruined = START, START, 0.0, False
    busy = 0
    for sig in sigs:
        i = sig["i"]
        ts_sig = c4[i][0]
        if ts_sig < t_a or ts_sig >= t_b:
            continue
        k0 = k_of_i4.get(c4[i + 1][0])
        if k0 is None or k0 + 2 >= len(c15):
            continue
        if k0 <= last_exit:
            busy += 1
            continue
        tr = run_trade(sig, c4, c15, k0, ts4_idx, ind)
        last_exit = tr["k_exit"]
        trades.append(tr)
        bal += tr["pnl"]
        peak = max(peak, bal)
        max_dd = max(max_dd, (peak - bal) / peak if peak > 0 else 0.0)
        if bal < MARGIN:
            ruined = True
            break
    return dict(trades=trades, max_dd=max_dd, ruined=ruined, busy=busy)


# ---------------------------------------------------------------- идея E
GEN_E = dict(rsi_idx=0, rsi_os=30, zone_l=0.51, zone_s=0.51, window=200,
             step=0.015, levels=3, mult=1.5, tp=0.015, sweep=0.0,
             max_bars=288, cooldown=0, knife=0.0, be_move=0)
STOP_E = 0.06                       # стоп 6% от цены входа
R_UNIT_E = MARGIN * LEV * STOP_E    # $1.50 — единица риска для exp_r идеи E


def _fixed_stop_extremes(candles, window):
    """Подмена ev.rolling_extremes ТОЛЬКО на время прогона идеи E.

    run5 ставит стоп как rlow[i]*(1-sweep) / rhigh[i]*(1+sweep) и считает
    положение в диапазоне zpos=(c-rlow)/(rhigh-rlow). Возвращая
    rlow=c*(1-6%), rhigh=c*(1+6%) при sweep=0, получаем ровно техзадание:
    "стоп 6% от цены входа" и zpos=0.5 всегда (зонный фильтр нейтрализован,
    вход определяется ТОЛЬКО пересечением RSI(7) 30/70).
    Механика сетки, ликвидаций, фандинга и порядка исполнения — родная run5.
    """
    lows = [c[4] * (1 - STOP_E) for c in candles]
    highs = [c[4] * (1 + STOP_E) for c in candles]
    return lows, highs


def run_combo_e(seg15, pre, variant):
    events = []
    ef = None
    if variant == "long":
        ef = lambda side, i: side if side == "L" else None
    g = dict(GEN_E)
    if variant == "nogrid":
        g["levels"] = 1
    r = e2.run5(seg15, pre, g, entry_filter=ef, events=events)
    trades = []
    for e in events:
        if e["type"] == "close":
            trades.append(dict(pnl=e["pnl"], r=e["pnl"] / R_UNIT_E,
                               reason=("liq" if e["liq"] else e["reason"]),
                               k_exit=0))
    return dict(trades=trades, max_dd=r["max_dd"], ruined=r["ruined"], busy=0)


# ---------------------------------------------------------------- метрики
def combo_metrics(res, bh_pct):
    tr = res["trades"]
    n = len(tr)
    if n == 0:
        return dict(n=0, wr=0.0, exp_r=0.0, pf=None, ret=0.0,
                    dd=0.0, bh=bh_pct, ruined=res["ruined"], rs=[])
    rs = [t["r"] for t in tr]
    pnls = [t["pnl"] for t in tr]
    gp = sum(p for p in pnls if p > 0)
    gl = -sum(p for p in pnls if p <= 0)
    return dict(n=n,
                wr=100.0 * sum(1 for p in pnls if p > 0) / n,
                exp_r=sum(rs) / n,
                pf=(gp / gl if gl > 0 else None),
                ret=100.0 * sum(pnls) / START,
                dd=100.0 * res["max_dd"],
                bh=bh_pct, ruined=res["ruined"], rs=rs)


def variant_summary(rows):
    with_tr = [m for m in rows if m["n"] > 0]
    prof = [m for m in with_tr if m["ret"] > 0]
    pf_ok = [m for m in with_tr if m["pf"] is None or m["pf"] >= CRIT_PF]
    all_r = [x for m in rows for x in m["rs"]]
    n = len(all_r)
    if n >= 2:
        mu = statistics.mean(all_r)
        sd = statistics.pstdev(all_r) * math.sqrt(n / (n - 1.0))
        t = mu / (sd / math.sqrt(n)) if sd > 0 else 0.0
    else:
        mu, sd, t = 0.0, 0.0, 0.0
    med = statistics.median([m["exp_r"] for m in with_tr]) if with_tr else 0.0
    beat_bh = sum(1 for m in with_tr if m["ret"] > m["bh"])
    return dict(combos=len(rows), with_tr=len(with_tr), prof=len(prof),
                pf_ok=len(pf_ok), n_trades=n, mean_r=mu, t=t,
                p_t=p_two_sided(t) if n >= 2 else 1.0,
                p_boot=bootstrap_p(all_r), conc3=concentration(all_r),
                med_exp_r=med,
                p_binom=binom_p_ge(len(prof), len(with_tr)) if with_tr else 1.0,
                total_ret=sum(m["ret"] for m in rows),
                beat_bh=beat_bh,
                mean_bh=statistics.mean([m["bh"] for m in rows]) if rows else 0.0)


# ---------------------------------------------------------------- контроль
def check_no_lookahead(c4, out):
    """Обязательная проверка проекта: сигналы на ПРЕФИКСЕ истории обязаны
    совпадать с сигналами на полной истории в той же зоне."""
    n = len(c4)
    cut = int(n * 0.6)
    pref = c4[:cut]
    ind_full, ind_pref = build_ind(c4), build_ind(pref)
    dets = [("A5", lambda cc, ii: det_A(cc, ii, 0.05)),
            ("A8", lambda cc, ii: det_A(cc, ii, 0.08)),
            ("B20tp", lambda cc, ii: det_B(cc, ii, 20, "tp")),
            ("B55tp", lambda cc, ii: det_B(cc, ii, 55, "tp")),
            ("B20tr", lambda cc, ii: det_B(cc, ii, 20, "trail")),
            ("C4h", lambda cc, ii: det_C(cc, ii, "4h")),
            ("C1d", lambda cc, ii: det_C(cc, ii, "1d")),
            ("D8", lambda cc, ii: det_D(cc, ii, 0.08)),
            ("D12", lambda cc, ii: det_D(cc, ii, 0.12))]
    ok, worst = True, 0.0
    for name, fn in dets:
        sf_ = [s for s in fn(c4, ind_full) if s["i"] < cut - 1]
        sp_ = [s for s in fn(pref, ind_pref) if s["i"] < cut - 1]
        if len(sf_) != len(sp_):
            out(f"  ЗАГЛЯДЫВАНИЕ в {name}: {len(sf_)} против {len(sp_)} сигналов")
            ok = False
            continue
        for a, b in zip(sf_, sp_):
            if a["i"] != b["i"] or a["side"] != b["side"]:
                out(f"  ЗАГЛЯДЫВАНИЕ в {name}: расходятся бары/стороны")
                ok = False
                break
            for key in ("stop_px", "stop_dist"):
                va, vb = a.get(key), b.get(key)
                if va is None or vb is None:
                    continue
                worst = max(worst, abs(va - vb) / max(1e-12, abs(va)))
    out(f"  проверка 'префикс против полной истории' по 9 детекторам: "
        f"{'СОВПАДАЕТ' if ok else 'ПРОВАЛЕНА'}, "
        f"макс. относительное расхождение цен стопа {worst:.2e}")
    return ok


def check_vs_engine(c4, c15, ind, out):
    """Сверка исполнителя сделок с signal_engine2.simulate_trade — модулем,
    которому проект уже доверяет. Те же вход/стоп/тейк/плечо/окно обязаны
    дать тот же PnL до копейки."""
    try:
        import signal_engine2 as se2
    except Exception as exc:
        out(f"  сверка с signal_engine2 недоступна: {exc}")
        return True
    k_of = {c[0]: k for k, c in enumerate(c15)}
    ts4 = {c[0]: j for j, c in enumerate(c4)}
    pairs, worst, n = [], 0.0, 0
    for sigs in (det_B(c4, ind, 20, "tp"), det_D(c4, ind, 0.08),
                 det_A(c4, ind, 0.05), det_C(c4, ind, "4h")):
        for sig in sigs[:80]:
            k0 = k_of.get(c4[sig["i"] + 1][0])
            if k0 is None or k0 + 2 >= len(c15):
                continue
            sgn = 1 if sig["side"] == "L" else -1
            entry_px = c15[k0][1]
            fill = entry_px * (1 + sgn * SLIP)
            stop = (sig["stop_px"] if sig.get("stop_px") is not None
                    else fill - sgn * sig["stop_dist"])
            risk = abs(fill - stop)
            wrong = (stop >= fill) if sgn == 1 else (stop <= fill)
            if wrong or risk < fill * MIN_STOP:
                continue            # эти случаи движки трактуют по-разному
            tp = fill + sgn * sig["tp_mult"] * risk
            mine = run_trade(sig, c4, c15, k0, ts4, ind)["pnl"]
            theirs = se2.simulate_trade(sig["side"], k0, entry_px, stop, tp,
                                        c15, LEV, MAX_HOLD_15)["pnl"]
            pairs.append((mine, theirs))
            worst = max(worst, abs(mine - theirs))
            n += 1
    out(f"  сверка с signal_engine2.simulate_trade на {n} сделках: "
        f"макс. расхождение PnL {worst:.2e} USDT "
        f"({'СОВПАДАЕТ' if worst < 1e-9 else 'РАСХОЖДЕНИЕ'})")
    return worst < 1e-9


# ---------------------------------------------------------------- прогон
VARIANTS_AD = [
    ("A1", "A", "капитуляция-лонг, падение >=5% за 2 суток",
     lambda cc, ii: det_A(cc, ii, 0.05)),
    ("A2", "A", "капитуляция-лонг, падение >=8% за 2 суток",
     lambda cc, ii: det_A(cc, ii, 0.08)),
    ("B1", "B", "пробой максимума 20 суток, стоп 2ATR, тейк 3ATR",
     lambda cc, ii: det_B(cc, ii, 20, "tp")),
    ("B2", "B", "пробой максимума 55 суток, стоп 2ATR, тейк 3ATR",
     lambda cc, ii: det_B(cc, ii, 55, "tp")),
    ("B3", "B", "пробой максимума 20 суток, стоп 2ATR, трейлинг мин.10 суток",
     lambda cc, ii: det_B(cc, ii, 20, "trail")),
    ("C1", "C", "пулбэк-шорт, SMA200/SMA20 по 4ч барам",
     lambda cc, ii: det_C(cc, ii, "4h")),
    ("C2", "C", "пулбэк-шорт, классические дневные SMA200/SMA20",
     lambda cc, ii: det_C(cc, ii, "1d")),
    ("D1", "D", "перегрев-шорт, рост >=8% за 2 суток",
     lambda cc, ii: det_D(cc, ii, 0.08)),
    ("D2", "D", "перегрев-шорт, рост >=12% за 2 суток",
     lambda cc, ii: det_D(cc, ii, 0.12)),
]

VARIANTS_E = [
    ("E1", "E", "сетка 3 колена, обе стороны, тейк 1.5%, стоп 6%", "both"),
    ("E2", "E", "то же, только ЛОНГ", "long"),
    ("E3", "E", "тот же вход/выход БЕЗ сетки (1 колено) — контроль", "nogrid"),
]

# минимальный прогретый бар и правило постройки сделки для КОНТРОЛЯ
# случайными входами: сторона, стоп и выход берутся те же, меняется только
# МОМЕНТ входа. Если случайный вход даёт тот же результат — сигнал пустой.
WARM = {"A1": 13, "A2": 13, "B1": 121, "B2": 331, "B3": 121,
        "C1": 200, "C2": 1200, "D1": 13, "D2": 13}
RAND_SEEDS = [11, 22, 33, 44, 55]


def make_sig(code, ind, i):
    """Сделка по тем же правилам стопа/тейка, но на произвольном баре i."""
    if code in ("A1", "A2"):
        return dict(i=i, side="L", stop_px=ind["low2d"][i] * 0.999,
                    tp_mult=2.0, trail=None)
    if code in ("B1", "B2", "B3"):
        if ind["atr14"][i] is None:
            return None
        return dict(i=i, side="L", stop_dist=2.0 * ind["atr14"][i],
                    tp_mult=(None if code == "B3" else 1.5),
                    trail=("dl10" if code == "B3" else None))
    if code in ("C1", "C2"):
        return dict(i=i, side="S", stop_px=ind["h3"][i] * 1.001,
                    tp_mult=2.0, trail=None)
    return dict(i=i, side="S", stop_px=ind["high2d"][i] * 1.001,
                tp_mult=2.0, trail=None)


def random_control(code, data, n_by_combo, syms):
    """Тот же выход, та же сторона, столько же входов — но моменты входа
    случайны. Усреднение по 5 зёрнам."""
    tot_ret, all_r, per_seed = [], [], []
    for seed in RAND_SEEDS:
        rnd = random.Random(seed)
        ret_sum, rs_seed = 0.0, []
        for s in syms:
            d = data[s]
            ts4 = [c[0] for c in d["c4"]]
            for p, (a, b) in enumerate(d["bnds"]):
                n = n_by_combo.get((s, p), 0)
                if n == 0:
                    continue
                lo = max(WARM[code], bisect.bisect_left(ts4, a))
                hi = min(len(d["c4"]) - 2, bisect.bisect_left(ts4, b) - 1)
                if hi - lo < n + 5:
                    continue
                bars = sorted(rnd.sample(range(lo, hi), n))
                sigs = [x for x in (make_sig(code, d["ind"], i) for i in bars)
                        if x is not None]
                res = run_combo_ad(sigs, d["c4"], d["c15"], d["k_of_ts"],
                                   d["ts4_idx"], d["ind"], a, b)
                m = combo_metrics(res, 0.0)
                ret_sum += m["ret"]
                rs_seed += m["rs"]
        per_seed.append(ret_sum)
        tot_ret.append(ret_sum)
        all_r += rs_seed
    mean_r = statistics.mean(all_r) if all_r else 0.0
    return dict(ret=statistics.mean(tot_ret), ret_sd=statistics.pstdev(tot_ret),
                mean_r=mean_r, n=len(all_r), per_seed=per_seed, rs=all_r)


def welch_t(a, b):
    """t-статистика Уэлча: несёт ли сигнал что-то сверх выхода и дрейфа рынка."""
    if len(a) < 3 or len(b) < 3:
        return 0.0
    va = statistics.variance(a) / len(a)
    vb = statistics.variance(b) / len(b)
    if va + vb <= 0:
        return 0.0
    return (statistics.mean(a) - statistics.mean(b)) / math.sqrt(va + vb)


def main():
    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    out("=" * 100)
    out("ПРОВЕРКА СЫРОГО ПРЕИМУЩЕСТВА ИДЕЙ БЕЗ ОПТИМИЗАЦИИ ПАРАМЕТРОВ")
    out("=" * 100)
    out("Ни один параметр не подбирался: все значения заданы заранее, 'из учебника'.")
    out(f"Издержки: тейкер {TAKER*100:.3f}%, мейкер {MAKER*100:.3f}%, "
        f"проскальзывание {SLIP*100:.2f}%, фандинг {FUND_8H*100:.2f}%/8ч.")
    out(f"Плечо x{LEV}, маржа ${MARGIN:.0f} на сделку, база ${START:.0f}, "
        f"одна позиция, ликвидация при ходе {LIQ_FRAC*100:.1f}%.")
    out("Стоп и тейк внутри одной свечи -> засчитывается СТОП (консервативно).")
    out(f"A-D: сигнал по закрытию 4ч-свечи, вход по открытию следующей, "
        f"ведение по 15м, таймаут {MAX_HOLD_DAYS} суток.")
    out("E: 15м, механика сетки — evolution2.run5 без изменений.")
    out()
    out("ЗАРАНЕЕ ОБЪЯВЛЕННЫЕ КРИТЕРИИ (зафиксированы ДО просмотра результатов):")
    out(f"  сырое преимущество = t-статистика >= {CRIT_T:.1f} И прибыльных "
        f"комбинаций >= {CRIT_COMBOS} из 15 И медианный exp_r > 0.")
    zb = z_for_p(0.05 / N_VARIANTS)
    out(f"  проверяется {N_VARIANTS} вариантов, поэтому поправка Бонферрони: "
        f"строгий порог |t| >= {zb:.2f} (alpha 0.05/{N_VARIANTS}).")
    out(f"  доля комбинаций с PF >= {CRIT_PF} — вспомогательный показатель.")
    out()

    # ---------------- данные
    out("-" * 100)
    out("ДАННЫЕ")
    out("-" * 100)
    data = {}
    for s in SYMS:
        c4 = ev.fetch(s, "240", DAYS)
        c15 = ev.fetch(s, "15", DAYS)
        t_lo = max(c4[0][0], c15[0][0])
        t_hi = min(c4[-1][0], c15[-1][0])
        data[s] = dict(c4=c4, c15=c15, t_lo=t_lo, t_hi=t_hi)
        out(f"  {s:9} 4ч: {len(c4):6} баров  15м: {len(c15):7} баров  "
            f"общий отрезок {fmt_date(t_lo)} .. {fmt_date(t_hi)}")
    out()

    # ---------------- контроль заглядывания
    out("-" * 100)
    out("САМОПРОВЕРКИ")
    out("-" * 100)
    la_ok = check_no_lookahead(data["BTCUSDT"]["c4"], out)
    _b = data["BTCUSDT"]
    eng_ok = check_vs_engine(_b["c4"], _b["c15"], build_ind(_b["c4"]), out)
    out()

    # ---------------- периоды
    out("-" * 100)
    out("ТРИ НЕПЕРЕСЕКАЮЩИХСЯ ПЕРИОДА (треть общего отрезка каждый)")
    out("-" * 100)
    for s in SYMS:
        d = data[s]
        span = d["t_hi"] - d["t_lo"]
        bnds = []
        for k in range(3):
            a = d["t_lo"] + span * k // 3
            b = d["t_lo"] + span * (k + 1) // 3 if k < 2 else d["t_hi"] + 1
            bnds.append((a, b))
        d["bnds"] = bnds
        if s == SYMS[0]:
            for k, (a, b) in enumerate(bnds):
                out(f"  П{k+1}: {fmt_date(a)} .. {fmt_date(b)}  "
                    f"(~{(b-a)/86400000:.0f} суток)")
    out("  (границы считаются по каждой монете отдельно из её общего отрезка,")
    out("   различия дат между монетами — не более пары суток)")
    out()

    # ---------------- подготовка индексов
    for s in SYMS:
        d = data[s]
        d["ind"] = build_ind(d["c4"])
        d["ts4_idx"] = {c[0]: j for j, c in enumerate(d["c4"])}
        d["k_of_ts"] = {c[0]: k for k, c in enumerate(d["c15"])}
        ts15 = [c[0] for c in d["c15"]]
        d["ts15"] = ts15
        # доходность "купил и держал" по каждому периоду
        bh = []
        cl4 = [c[0] for c in d["c4"]]
        for (a, b) in d["bnds"]:
            ia = bisect.bisect_left(cl4, a)
            ib = bisect.bisect_left(cl4, b) - 1
            bh.append(100.0 * (d["c4"][ib][4] / d["c4"][ia][4] - 1.0))
        d["bh"] = bh

    out("-" * 100)
    out("РЕЖИМ РЫНКА В КАЖДОМ ПЕРИОДЕ ('купил и держал', % за период)")
    out("-" * 100)
    out("  период  " + "  ".join(f"{s[:-4]:>8}" for s in SYMS) + "   среднее")
    for p in range(3):
        vals = [data[s]["bh"][p] for s in SYMS]
        out(f"    П{p+1}    " + "  ".join(f"{v:+8.1f}" for v in vals) +
            f"   {statistics.mean(vals):+8.1f}")
    out("  Это ключевой контекст: лонговые идеи обязаны проверяться на П3,")
    out("  шортовые — на П1. Иначе меряется направление рынка, а не идея.")
    out()

    results = {}

    def print_variant(code, idea, title, rows, reasons):
        out()
        out("=" * 100)
        out(f"ВАРИАНТ {code} ({idea}) — {title}")
        out("=" * 100)
        out("  монета    период  сделок    WR%    expR     PF    итог%    "
            "просадка%   купил-держал%")
        for m in rows:
            pf = fmt_pf(m["pf"])
            flag = "  СЛИВ" if m["ruined"] else ""
            out(f"  {m['sym']:9} П{m['per']}     {m['n']:5}  {m['wr']:5.1f}  "
                f"{m['exp_r']:+6.2f}  {pf}  {m['ret']:+8.1f}   "
                f"{m['dd']:7.1f}     {m['bh']:+10.1f}{flag}")
        s = variant_summary(rows)
        tot = sum(reasons.values()) or 1
        rs = "  ".join(f"{k} {v} ({100.0*v/tot:.0f}%)"
                       for k, v in sorted(reasons.items(), key=lambda x: -x[1]))
        out("  " + "-" * 96)
        out(f"  ИТОГО: сделок {s['n_trades']}, комбинаций с сигналами "
            f"{s['with_tr']}/15, прибыльных {s['prof']}/{s['with_tr']}, "
            f"PF>={CRIT_PF} в {s['pf_ok']}/{s['with_tr']}")
        out(f"  выходы: {rs}")
        out(f"  медианный exp_r по комбинациям {s['med_exp_r']:+.3f}; "
            f"средний R по всем сделкам {s['mean_r']:+.3f}")
        out(f"  t-статистика по пулу сделок {s['t']:+.2f} (p={s['p_t']:.4f}); "
            f"биномиальный p доли прибыльных комбинаций {s['p_binom']:.4f}")
        out(f"  бутстрэп: доля выборок с неположительным средним "
            f"{s['p_boot']:.4f}; 3 лучшие сделки дают {s['conc3']*100:.0f}% "
            f"всей прибыли")
        out(f"  суммарный итог по 15 комбинациям {s['total_ret']:+.1f}% базы; "
            f"обыграли 'купил и держал' в {s['beat_bh']}/{s['with_tr']} "
            f"(средний B&H {s['mean_bh']:+.1f}%)")
        # разбивка по периодам: живёт идея во всех режимах рынка или в одном?
        parts = []
        for p in (1, 2, 3):
            rr = [x for m in rows if m["per"] == p for x in m["rs"]]
            rt = sum(m["ret"] for m in rows if m["per"] == p)
            pr = sum(1 for m in rows if m["per"] == p and m["ret"] > 0)
            mr = statistics.mean(rr) if rr else 0.0
            parts.append(f"П{p}: ср.R {mr:+.3f}, итог {rt:+.0f}%, приб. {pr}/5")
        out("  по периодам  " + " | ".join(parts))
        results[code] = dict(idea=idea, title=title,
                             all_r=[x for m in rows for x in m["rs"]], **s)

    # ---------------- идеи A-D
    n_by_combo = {}
    for code, idea, title, det in VARIANTS_AD:
        rows, reasons, nbc = [], {}, {}
        for s in SYMS:
            d = data[s]
            sigs = det(d["c4"], d["ind"])
            for p, (a, b) in enumerate(d["bnds"]):
                res = run_combo_ad(sigs, d["c4"], d["c15"], d["k_of_ts"],
                                   d["ts4_idx"], d["ind"], a, b)
                m = combo_metrics(res, d["bh"][p])
                m["sym"], m["per"] = s, p + 1
                rows.append(m)
                nbc[(s, p)] = m["n"]
                for t in res["trades"]:
                    reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
        n_by_combo[code] = nbc
        print_variant(code, idea, title, rows, reasons)

    # ---------------- идея E (сетка, движок run5)
    out()
    out("=" * 100)
    out("ИДЕЯ E — СЕТОЧНАЯ КОНТР-ТРЕНДОВАЯ (движок evolution2.run5)")
    out("=" * 100)
    out(f"  Фиксированный геном: RSI(7) пересекает 30/70, {GEN_E['levels']} колена "
        f"(вход + 2 доливки), шаг {GEN_E['step']*100:.1f}%, множитель "
        f"{GEN_E['mult']}, тейк {GEN_E['tp']*100:.1f}% от средней,")
    out(f"  стоп {STOP_E*100:.0f}% от цены входа, таймаут "
        f"{GEN_E['max_bars']//BARS15_DAY} суток. Единица риска для exp_r: "
        f"${R_UNIT_E:.2f} (маржа x плечо x стоп).")
    out("  Внимание: exp_r идеи E нормирован иначе, чем у A-D, поэтому сравнимы")
    out("  между идеями только t-статистика, PF, WR и итог% (они от нормировки")
    out("  не зависят).")

    e2.LEV, e2.BARS_PER_DAY = LEV, BARS15_DAY
    orig_extremes = ev.rolling_extremes
    try:
        ev.rolling_extremes = _fixed_stop_extremes
        # сегменты готовим по одному (иначе 15 наборов рядов съедают память)
        rows_e = {c: [] for c, _, _, _ in VARIANTS_E}
        reas_e = {c: {} for c, _, _, _ in VARIANTS_E}
        for s in SYMS:
            d = data[s]
            for p, (a, b) in enumerate(d["bnds"]):
                ia = bisect.bisect_left(d["ts15"], a)
                ib = bisect.bisect_left(d["ts15"], b)
                seg = d["c15"][ia:ib]
                pre = e2.prep(seg)
                for code, idea, title, variant in VARIANTS_E:
                    res = run_combo_e(seg, pre, variant)
                    m = combo_metrics(res, d["bh"][p])
                    m["sym"], m["per"] = s, p + 1
                    rows_e[code].append(m)
                    for t in res["trades"]:
                        reas_e[code][t["reason"]] = \
                            reas_e[code].get(t["reason"], 0) + 1
                del seg, pre
        for code, idea, title, variant in VARIANTS_E:
            print_variant(code, idea, title, rows_e[code], reas_e[code])
    finally:
        ev.rolling_extremes = orig_extremes

    # ---------------- контроль: те же выходы, но СЛУЧАЙНЫЕ моменты входа
    out()
    out("=" * 100)
    out("КОНТРОЛЬ 'НЕОБУЧЕННОЕ СЕМЯ': ТЕ ЖЕ ВЫХОДЫ, НО СЛУЧАЙНЫЕ ВХОДЫ")
    out("=" * 100)
    out("Сторона, стоп, тейк/трейлинг и число сделок в каждой комбинации те же,")
    out("что у реального сигнала — случаен ТОЛЬКО момент входа. Усреднение по")
    out("5 зёрнам. Если случайный вход даёт то же самое, распознавание паттерна")
    out("не несёт информации, а результат — это свойство ВЫХОДА и самого рынка.")
    out()
    out(" вар   реал.итог%   случ.итог%(разброс)  реал.ср.R  случ.ср.R"
        "  вклад сигнала  t Уэлча   p")
    for code, idea, title, det in VARIANTS_AD:
        rc = random_control(code, data, n_by_combo[code], SYMS)
        s = results[code]
        results[code]["ctrl_ret"] = rc["ret"]
        results[code]["ctrl_r"] = rc["mean_r"]
        d_r = s["mean_r"] - rc["mean_r"]
        tw = welch_t(s["all_r"], rc["rs"])
        results[code]["t_welch"] = tw
        out(f" {code:4} {s['total_ret']:+10.1f}  {rc['ret']:+10.1f} "
            f"(±{rc['ret_sd']:>3.0f})  {s['mean_r']:+9.3f}  {rc['mean_r']:+9.3f}"
            f"     {d_r:+9.3f}   {tw:+6.2f} {p_two_sided(tw):7.4f}")
    out()
    out("Столбец 'вклад сигнала' = насколько распознавание паттерна улучшает")
    out("средний R по сравнению со случайным входом в тот же рынок. t Уэлча —")
    out(f"значимость этого вклада; порог с поправкой Бонферрони |t| >= {zb:.2f}.")
    out()
    out("-" * 100)
    out("ДИАГНОСТИКА ЗЕРКАЛА: B3 наоборот — пробой МИНИМУМА 20 суток в ШОРТ,")
    out("стоп 2ATR, трейлинг по максимуму 10 суток. В диагностику, не в кандидаты.")
    out("-" * 100)
    rows_m, reas_m = [], {}
    for s in SYMS:
        d = data[s]
        sigs = det_B_mirror(d["c4"], d["ind"])
        for p, (a, b) in enumerate(d["bnds"]):
            res = run_combo_ad(sigs, d["c4"], d["c15"], d["k_of_ts"],
                               d["ts4_idx"], d["ind"], a, b)
            m = combo_metrics(res, d["bh"][p])
            m["sym"], m["per"] = s, p + 1
            rows_m.append(m)
            for t in res["trades"]:
                reas_m[t["reason"]] = reas_m.get(t["reason"], 0) + 1
    sm = variant_summary(rows_m)
    b3 = results["B3"]
    out(f"  зеркало (шорт):  сделок {sm['n_trades']}, прибыльных комбинаций "
        f"{sm['prof']}/{sm['with_tr']}, ср.R {sm['mean_r']:+.3f}, "
        f"t {sm['t']:+.2f}, итог {sm['total_ret']:+.1f}%")
    out(f"  оригинал B3:     сделок {b3['n_trades']}, прибыльных комбинаций "
        f"{b3['prof']}/{b3['with_tr']}, ср.R {b3['mean_r']:+.3f}, "
        f"t {b3['t']:+.2f}, итог {b3['total_ret']:+.1f}%")
    for p in (1, 2, 3):
        rr = [x for m in rows_m if m["per"] == p for x in m["rs"]]
        rt = sum(m["ret"] for m in rows_m if m["per"] == p)
        out(f"    зеркало П{p}: ср.R "
            f"{(statistics.mean(rr) if rr else 0.0):+.3f}, итог {rt:+.0f}%")
    out("  Если знак у зеркала противоположен оригиналу и совпадает со знаком")
    out("  движения рынка в тех же периодах — измеряется бета, а не пробой.")

    out()
    out("Идея E контролируется вариантом E3 (тот же вход и выход, но без сетки):")
    out("он показывает, что сам вход по RSI(7) убыточен, а сетка лишь маскирует")
    out("это высоким winrate, не превращая знак результата в плюс.")

    # ---------------- сводка и вердикты
    out()
    out("=" * 100)
    out("СВОДНАЯ ТАБЛИЦА ПО ВСЕМ ВАРИАНТАМ")
    out("=" * 100)
    out(" вар  сделок  приб.комб  PF>=1.2  мед.expR   ср.R      t      p(t)   "
        "p(бут)  p(бином)   итог%   >B&H")
    for code in list(results):
        s = results[code]
        out(f" {code:4} {s['n_trades']:6}   {s['prof']:2}/{s['with_tr']:<2}      "
            f"{s['pf_ok']:2}/{s['with_tr']:<2}   {s['med_exp_r']:+7.3f}  "
            f"{s['mean_r']:+6.3f}  {s['t']:+6.2f}  {s['p_t']:7.4f} "
            f"{s['p_boot']:7.4f}  "
            f"{s['p_binom']:8.4f} {s['total_ret']:+8.1f}  {s['beat_bh']:2}/15")

    out()
    out("=" * 100)
    out("ВЕРДИКТЫ ПО ВАРИАНТАМ (критерии объявлены до просмотра результатов)")
    out("=" * 100)
    for code in list(results):
        s = results[code]
        c1 = s["t"] >= CRIT_T
        c2 = s["prof"] >= CRIT_COMBOS
        c3 = s["med_exp_r"] > 0
        cb = s["t"] >= zb
        npass = sum([c1, c2, c3])
        if npass == 3 and cb:
            verd = "ПРЕИМУЩЕСТВО ЕСТЬ (проходит и поправку Бонферрони)"
        elif npass == 3:
            verd = "преимущество слабое (не проходит поправку на 12 проверок)"
        elif npass == 2:
            verd = "признаки есть, но критерии не выполнены"
        else:
            verd = "ПРЕИМУЩЕСТВА НЕТ"
        ctrl = results[code].get("ctrl_r")
        ctxt = ""
        if ctrl is not None:
            ctxt = (f" | лучше случайных входов "
                    f"{'да' if s['mean_r'] > ctrl else 'нет':3}")
        out(f" {code}: t>={CRIT_T} {'да' if c1 else 'нет':3} | "
            f"приб.комб>={CRIT_COMBOS} {'да' if c2 else 'нет':3} | "
            f"мед.expR>0 {'да' if c3 else 'нет':3} | "
            f"Бонферрони {'да' if cb else 'нет':3}{ctxt}  ->  {verd}")

    out()
    out("=" * 100)
    out("ИТОГ: ИДЕИ С СЫРЫМ ПРЕИМУЩЕСТВОМ")
    out("=" * 100)
    winners = []
    for idea in ["A", "B", "C", "D", "E"]:
        vs = [(c, results[c]) for c in results if results[c]["idea"] == idea]
        best = max(vs, key=lambda x: x[1]["t"])
        full = [c for c, s in vs
                if s["t"] >= CRIT_T and s["prof"] >= CRIT_COMBOS
                and s["med_exp_r"] > 0]
        strict = [c for c in full if results[c]["t"] >= zb]
        pos_t = sum(1 for _, s in vs if s["t"] > 0)
        if strict:
            winners.append(idea)
            tag = f"ЕСТЬ (варианты {', '.join(strict)})"
        elif full:
            tag = f"слабое (варианты {', '.join(full)}, но не переживает поправку)"
        else:
            tag = "НЕТ"
        out(f"  Идея {idea}: {tag}")
        out(f"    вариантов {len(vs)}, из них с положительной t: {pos_t}; "
            f"лучший {best[0]} t={best[1]['t']:+.2f}, "
            f"прибыльных комбинаций {best[1]['prof']}/{best[1]['with_tr']}, "
            f"итог {best[1]['total_ret']:+.1f}%")
    out()
    wtxt = (", ".join(winners) if winners
            else "ПУСТО — ни одна идея не дала сырого преимущества")
    out(f"  СПИСОК ИДЕЙ ДЛЯ ДВИЖКА v3: {wtxt}")

    out()
    out("-" * 100)
    out("НАБЛЮДЕНИЯ (посчитаны из таблиц выше, не оценочные суждения)")
    out("-" * 100)
    all_pos, all_neg = [], []
    for idea in ["A", "B", "C", "D", "E"]:
        mrs = [results[c]["mean_r"] for c in results if results[c]["idea"] == idea]
        if all(x > 0 for x in mrs):
            all_pos.append(idea)
        if all(x < 0 for x in mrs):
            all_neg.append(idea)
    out(f"  идеи, где ВСЕ варианты дали положительный средний R: "
        f"{', '.join(all_pos) if all_pos else 'нет'}")
    out(f"  идеи, где ВСЕ варианты дали отрицательный средний R: "
        f"{', '.join(all_neg) if all_neg else 'нет'}")
    ranked = sorted((c for c in results if "t_welch" in results[c]),
                    key=lambda c: -results[c]["t_welch"])
    out("  вклад сигнала над случайным входом, по убыванию значимости:")
    for c in ranked:
        out(f"    {c}: вклад {results[c]['mean_r']-results[c]['ctrl_r']:+.3f} R, "
            f"t Уэлча {results[c]['t_welch']:+.2f}, "
            f"p {p_two_sided(results[c]['t_welch']):.4f}")
    bb = min(results, key=lambda c: results[c]["p_boot"])
    out(f"  лучший бутстрэп по всем вариантам: {bb} "
        f"(доля выборок со средним <= 0: {results[bb]['p_boot']:.4f}, "
        f"нужен < 0.05, а с поправкой на {N_VARIANTS} проверок "
        f"< {0.05/N_VARIANTS:.4f})")
    p1 = sum(results[c]["total_ret"] for c in results)
    out(f"  сумма итогов всех 12 вариантов по всем 15 комбинациям: {p1:+.0f}% базы")
    out()
    out(f"  Контроль заглядывания: {'пройден' if la_ok else 'ПРОВАЛЕН'}; "
        f"сверка с signal_engine2: {'пройдена' if eng_ok else 'ПРОВАЛЕНА'}")
    out("=" * 100)

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "idea_test_out.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"\n[время работы {time.time()-t0:.1f} с]")
