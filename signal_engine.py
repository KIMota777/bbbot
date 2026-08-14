# -*- coding: utf-8 -*-
"""Движок сигнального помощника: 6 среднесрочных сетапов на BTC.

Сетапы (сигнал ищется на ЗАКРЫТОМ 4ч-баре, контекст — дневной режим рынка):
  range_long   — боковик, цена у нижней границы диапазона, RSI перепродан
  range_short  — боковик, цена у верхней границы, RSI перекуплен
  sweep_long   — ложный пробой значимого минимума (сбор ликвидности) и
                 возврат: свеча проколола уровень, но закрылась выше
  sweep_short  — зеркально: ложный пробой максимума
  dump_long    — сильное падение за 1-3 дня + разворотная свеча -> лонг
  pump_short   — вертикальный рост за 1-3 дня + разворотная свеча -> шорт

Правила сделки (одинаковые для всех сетапов):
  - стоп : тейк = 1 : 3 (фиксировано, риск 1R -> цель 3R);
  - БЕЗ переноса в безубыток (урок v8: be_move на грубых барах прячет риск);
  - стоп за структурой (за уровнем/фитилём/экстремумом движения) с буфером
    в долях дневного ATR, но общая ширина стопа ограничена stop_cap —
    иначе сигнал ПРОПУСКАЕТСЯ (на x15-20 широкий стоп = ликвидация раньше);
  - таймаут hold_days дней (среднесрок: примерно до недели), выход по рынку;
  - одна позиция на сетап, кулдаун после выхода.

Мультитаймфрейм честно: сигнал на 4ч, исполнение и проверка стопа/тейка —
на 15-минутных свечах (вход на open первой 15m-свечи ПОСЛЕ закрытия
сигнального 4ч-бара; при касании стопа и тейка в одной 15m-свече
засчитывается стоп — консервативно). Комиссии/фандинг/проскальзывание —
те же константы, что в честном движке evolution2.
"""

import bisect
from collections import deque

import evolution as ev
import evolution2 as e2
import evolution6 as e6

TAKER, MAKER = e2.TAKER, e2.MAKER
SLIP = e2.SLIP
FUND_8H = e2.FUND_8H
MM = e2.MM
START, MARGIN = 20.0, 5.0
RR = 3.0                 # тейк = RR x стоп (1 к 3)
MIN_STOP = 0.006         # стоп уже 0.6% не берём — шумовые сигналы
OOS_MIN_TRADES = 5       # меньше сделок в окне — окно не экзамен, а прогул
OOS_THIN_PENALTY = -3.0  # цена прогула: пустой фолд не должен быть "ничьей"
RSI_SET = e2.RSI_SET     # [7, 10, 14, 21]

SETUPS = ["range_long", "range_short", "sweep_long", "sweep_short",
          "dump_long", "pump_short"]

DEFAULTS = dict(rsi_idx=2, window=90, zone=0.15, rsi_os=30, buf_atr=0.5,
                stop_cap=0.028, poke_atr=0.4, age=8, drop_frac=0.08,
                drop_days=2, hold_days=6, cooldown=6)


def atr_daily_frac(c4, n=6):
    """Дневной ATR как доля цены, по 4ч-барам (6 баров = сутки)."""
    m = len(c4)
    out = [None] * m
    prev_c = c4[0][4]
    val, trs = None, []
    for i in range(1, m):
        h, l, c = c4[i][2], c4[i][3], c4[i][4]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        prev_c = c
        if val is None:
            trs.append(tr)
            if len(trs) == n:
                val = sum(trs) / n
        else:
            val = (val * (n - 1) + tr) / n
        if val is not None:
            out[i] = val / c
    return out


def rolling_extremes_lagged(c4, window, age):
    """(lows[i], highs[i]) = экстремумы окна из `window` баров, ЗАКАНЧИВАЮЩЕГОСЯ
    за `age` баров до i — уровень должен быть "старым", чтобы прокол был
    именно сбором ликвидности, а не продолжением свежего движения."""
    m = len(c4)
    lows = [None] * m
    highs = [None] * m
    dq_min, dq_max = deque(), deque()
    for j in range(m):
        while dq_min and c4[dq_min[-1]][3] >= c4[j][3]:
            dq_min.pop()
        dq_min.append(j)
        while dq_max and c4[dq_max[-1]][2] <= c4[j][2]:
            dq_max.pop()
        dq_max.append(j)
        while dq_min[0] <= j - window:
            dq_min.popleft()
        while dq_max[0] <= j - window:
            dq_max.popleft()
        i = j + age  # окно [j-window+1 .. j] обслуживает бар i = j+age
        if i < m and j >= window - 1:
            lows[i] = c4[dq_min[0]][3]
            highs[i] = c4[dq_max[0]][2]
    return lows, highs


def prep_context(c4):
    """Общий контекст по 4ч-серии (не зависит от генов)."""
    closes = [c[4] for c in c4]
    return dict(
        closes=closes,
        rsi={p: e2.bg.calc_rsi(closes, p) for p in RSI_SET},
        atr_d=atr_daily_frac(c4),
        regime=e6.calc_regime(c4),  # 0 bull / 1 range / 2 bear (по дневным)
    )


def detect(setup, i, c4, ctx, g, ext):
    """Сигнал на закрытии 4ч-бара i. Возвращает (entry_ref, stop_px)|None.
    entry_ref — цена закрытия бара (реальный вход будет по 15m open позже)."""
    rsi = ctx["rsi"][RSI_SET[g["rsi_idx"]]][i]
    atr = ctx["atr_d"][i]
    lo, hi = ext[0][i], ext[1][i]
    if rsi is None or atr is None or lo is None or hi is None or hi <= lo:
        return None
    ts, o, h, l, c = c4[i]
    buf = g["buf_atr"] * atr * c

    if setup == "range_long":
        if ctx["regime"][i] != 1:
            return None
        zone_pos = (c - lo) / (hi - lo)
        if zone_pos < g["zone"] and rsi < g["rsi_os"]:
            return c, min(lo, l) - buf
    elif setup == "range_short":
        if ctx["regime"][i] != 1:
            return None
        zone_pos = (c - lo) / (hi - lo)
        if zone_pos > 1 - g["zone"] and rsi > 100 - g["rsi_os"]:
            return c, max(hi, h) + buf
    elif setup == "sweep_long":
        poke = lo - l
        if l < lo and c > lo and poke >= g["poke_atr"] * atr * c:
            return c, l - buf
    elif setup == "sweep_short":
        poke = h - hi
        if h > hi and c < hi and poke >= g["poke_atr"] * atr * c:
            return c, h + buf
    elif setup == "dump_long":
        d = g["drop_days"] * 6
        if i < d:
            return None
        drop = (ctx["closes"][i - d] - c) / ctx["closes"][i - d]
        if drop >= g["drop_frac"] and c > o and rsi < g["rsi_os"]:
            low_d = min(x[3] for x in c4[i - d:i + 1])
            return c, low_d - buf
    elif setup == "pump_short":
        d = g["drop_days"] * 6
        if i < d:
            return None
        rise = (c - ctx["closes"][i - d]) / ctx["closes"][i - d]
        if rise >= g["drop_frac"] and c < o and rsi > 100 - g["rsi_os"]:
            high_d = max(x[2] for x in c4[i - d:i + 1])
            return c, high_d + buf
    return None


# ------------------------------------------------- модель исполнения и издержек
#
# Одна формула на два места. Публичный учёт сетапов (advisor.py) обязан считать
# исход теми же издержками, которыми сетапы отбирались, иначе история на сайте
# систематически лучше бэктеста: номинальный тейк 3R после комиссий и
# проскальзывания даёт ~2.9R, а номинальный стоп -1R стоит ~-1.15R — то есть
# порог безубыточного винрейта выше объявленного. Дублировать формулу нельзя:
# разъедется молча.

def entry_fill(ref_px, sgn):
    """Вход маркет-ордером по open 15м-свечи: проскальзывание против нас."""
    return ref_px * (1 + sgn * SLIP)


def exit_fill(ref_px, sgn, taker=True):
    """Выход: стоп и таймаут — маркет со слиппеджем, тейк — лимит по своей цене."""
    return ref_px * (1 - sgn * SLIP) if taker else ref_px


def liq_price(entry_px, sgn, lev):
    """Цена ликвидации: неблагоприятный ход MM/lev от входа."""
    return entry_px * (1 - sgn * MM / lev)


def funding_step(qty, px):
    """Плата за удержание за одну 15м-свечу (фандинг раз в 8ч = 32 свечи)."""
    return qty * px * FUND_8H / 32


def trade_pnl(sgn, entry_px, exit_px, qty, taker_exit, funding=0.0):
    """Итог сделки в долларах: ход цены минус комиссии входа/выхода и фандинг.
    Проскальзывание уже сидит в ценах (entry_fill/exit_fill)."""
    fee_in = qty * entry_px * TAKER
    fee_out = qty * exit_px * (TAKER if taker_exit else MAKER)
    return sgn * (exit_px - entry_px) * qty - fee_in - fee_out - funding


def liq_pnl(margin, qty, entry_px, funding=0.0):
    """Ликвидация: теряем почти всю маржу плюс уже уплаченные издержки."""
    return -margin * MM - qty * entry_px * TAKER - funding


def r_multiple(pnl, margin, lev, dist):
    """Итог в R, где 1R — НОМИНАЛЬНЫЙ риск на стопе (margin x lev x ширина стопа).
    Именно поэтому честный стоп выходит хуже -1R: издержки сверх номинала."""
    return pnl / (margin * lev * dist) if dist else 0.0


def simulate_trade(side, entry_i15, stop_px, tp_px, c15, ts15, lev,
                   hold_bars15, margin=MARGIN):
    """Ведёт сделку по 15m-свечам. Возвращает (pnl, exit_i15, reason).
    Консервативно: стоп и тейк в одной свече -> стоп; ликвидация при
    неблагоприятном ходе >= MM/lev от входа."""
    sgn = 1 if side == "L" else -1
    entry_px = entry_fill(c15[entry_i15][1], sgn)
    qty = margin * lev / entry_px
    fund = 0.0
    liq_px = liq_price(entry_px, sgn, lev)
    end_i = min(entry_i15 + hold_bars15, len(c15) - 1)

    for k in range(entry_i15, end_i + 1):
        _, o, h, l, c = c15[k]
        fund += funding_step(qty, c)
        hit_liq = (l <= liq_px) if sgn == 1 else (h >= liq_px)
        hit_stop = (l <= stop_px) if sgn == 1 else (h >= stop_px)
        hit_tp = (h >= tp_px) if sgn == 1 else (l <= tp_px)
        if hit_liq and (not hit_stop or (sgn == 1 and liq_px >= stop_px)
                        or (sgn == -1 and liq_px <= stop_px)):
            return liq_pnl(margin, qty, entry_px, fund), k, "liq"
        if hit_stop:
            px = exit_fill(stop_px, sgn)
            return trade_pnl(sgn, entry_px, px, qty, True, fund), k, "stop"
        if hit_tp:
            return trade_pnl(sgn, entry_px, tp_px, qty, False, fund), k, "tp"
    px = exit_fill(c15[end_i][4], sgn)
    return trade_pnl(sgn, entry_px, px, qty, True, fund), end_i, "timeout"


def run_setup(setup, g, c4, ctx, c15, ts15, lev, signal_range=None):
    """Полный прогон сетапа. signal_range=(a,b) — сигналы только из 4ч-баров
    [a,b) (walk-forward), сделки могут закрываться позже b."""
    side = "L" if setup.endswith("long") else "S"
    sgn = 1 if side == "L" else -1
    ext = rolling_extremes_lagged(
        c4, g["window"], g["age"] if setup.startswith("sweep") else 1)
    a, b = signal_range or (0, len(c4))
    a = max(a, g["window"] + g["age"] + 40)

    balance, peak, max_dd = START, START, 0.0
    trades = []
    monthly = {}
    t0 = c4[a][0] if a < len(c4) else c4[-1][0]
    busy_until_ts = 0
    cooldown_ts = 0
    hold_bars15 = g["hold_days"] * 96

    for i in range(a, min(b, len(c4))):
        ts = c4[i][0]
        if ts < busy_until_ts or ts < cooldown_ts:
            continue
        sig = detect(setup, i, c4, ctx, g, ext)
        if not sig:
            continue
        ref_px, stop_px = sig
        dist = sgn * (ref_px - stop_px) / ref_px
        if dist < MIN_STOP or dist > g["stop_cap"]:
            continue  # стоп слишком узкий (шум) или слишком широкий (x15-20)
        tp_px = ref_px * (1 + sgn * RR * dist)

        close_ts = ts + 4 * 3600 * 1000  # вход после закрытия 4ч-бара
        j = bisect.bisect_left(ts15, close_ts)
        if j >= len(c15) - 2:
            break
        pnl, exit_j, reason = simulate_trade(
            side, j, stop_px, tp_px, c15, ts15, lev, hold_bars15)
        balance += pnl
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak)
        exit_ts = ts15[exit_j]
        monthly[(exit_ts - t0) // e2.MONTH_MS] = \
            monthly.get((exit_ts - t0) // e2.MONTH_MS, 0) + pnl
        trades.append(dict(
            entry_ts=close_ts, exit_ts=exit_ts, side=side,
            entry=round(ref_px, 2), stop=round(stop_px, 2),
            tp=round(tp_px, 2), pnl=round(pnl, 4), reason=reason,
            r=round(r_multiple(pnl, MARGIN, lev, dist), 2),
            hold_h=round((exit_ts - close_ts) / 3600000, 1)))
        busy_until_ts = exit_ts
        cooldown_ts = exit_ts + g["cooldown"] * 4 * 3600 * 1000
        if balance < MARGIN:
            return dict(balance=balance, trades=trades, monthly=monthly,
                        max_dd=max_dd, ruined=True,
                        months=(c4[min(b, len(c4)) - 1][0] - t0) / e2.MONTH_MS)

    return dict(balance=balance, trades=trades, monthly=monthly,
                max_dd=max_dd, ruined=False,
                months=max(1e-9, (c4[min(b, len(c4)) - 1][0] - t0) / e2.MONTH_MS))


def stats(r):
    n_months = max(1, int(r["months"]))
    rets = [(r["monthly"].get(m, 0.0) / START) * 100 for m in range(n_months)]
    med = sorted(rets)[len(rets) // 2] if rets else 0.0
    p25 = ev.percentile(rets, 0.25)
    wins = sum(1 for t in r["trades"] if t["pnl"] > 0)
    n = len(r["trades"])
    holds = [t["hold_h"] for t in r["trades"]]
    return dict(
        med=med, p25=p25, n=n, wins=wins,
        wr=round(wins / n * 100, 1) if n else 0,
        tpm=n / n_months,
        avg_hold_h=round(sum(holds) / n, 1) if n else 0,
        pos_share=round(sum(1 for x in rets if x > 0) / len(rets) * 100)
        if rets else 0)


def fitness(r):
    st = stats(r)
    if st["n"] < 10:  # статистический пол: меньше 10 сделок = не доверяем
        return -1.0
    f = st["p25"] + 0.5 * st["med"]
    f *= min(1.0, st["tpm"] / 0.6)  # мягкий штраф за редкость (<~0.6/мес)
    if r["ruined"]:
        f -= 50
    return f


def oos_score(r):
    """Балл экзаменационного окна.

    Раньше окно с n<3 давало 0.0 — «ничью», и это ломало весь экзамен: сетап,
    который за полгода не дал ни одной сделки, не сдавал экзамен, а прогуливал,
    но средний балл от этого не страдал. Так range_long прошёл отбор со
    средним 1.89 на фолдах [0.0, 5.68, 0.0] — то есть по одному
    информативному окну из трёх. Теперь неинформативное окно — незачёт: балл
    отрицательный, и средний по фолдам такой сетап не вытягивает.
    """
    st = stats(r)
    if st["n"] < OOS_MIN_TRADES:
        return OOS_THIN_PENALTY
    return st["p25"] + 0.5 * st["med"] - (100 if r["ruined"] else 0)
