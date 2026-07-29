# -*- coding: utf-8 -*-
"""Бэктест стратегии "RSI + сетка + уровни ликвидности".

Логика (зеркально для шорта):
  - уровни = min/max за WINDOW свечей (границы диапазона);
  - лонг только если цена в нижней части диапазона (< ZONE);
  - вход: RSI пересекает уровень перепроданности сверху вниз;
  - сетка: докупка при падении на GRID_STEP от последнего входа,
    объём каждого колена растёт в GRID_MULT раза, максимум GRID_LEVELS колен;
  - тейк: TP_PCT от средней цены (лимит, комиссия мейкера);
  - "обманный" стоп: НЕ под уровнем, а с буфером SWEEP_BUF ниже минимума
    диапазона на момент входа (за зоной сбора стопов);
  - выход по времени: позиция старше MAX_BARS свечей и RSI вернулся к 50.

Комиссии: тейкер 0.055% (вход по рынку, стоп), мейкер 0.02% (сетка, тейк).
Запуск: python backtest_rsi_grid.py
"""

import itertools
import json
import os
import time
from collections import deque

from pybit.unified_trading import HTTP

import config

DAYS = 365
CACHE = "history_cache.json"
TAKER = 0.00055
MAKER = 0.0002
START_BALANCE = 7.4
MARGIN_TOTAL = 5.0     # суммарная маржа на всю сетку (USDT)
RSI_PERIOD = 14
WINDOW = 400           # свечей для расчёта уровней диапазона
ZONE = 0.35            # нижняя/верхняя доля диапазона, где разрешён вход
MAX_BARS = 192         # 2 суток на 15m — таймаут позиции

# --- сетка перебора параметров ---
P_RSI_OS = [25, 30]           # порог перепроданности (перекупл. = 100 - x)
P_GRID_STEP = [0.015, 0.025]  # шаг сетки
P_GRID_LEVELS = [3, 4]        # колен в сетке
P_TP = [0.012, 0.02, 0.03]    # тейк от средней
P_SWEEP = [0.01, 0.02]        # буфер стопа за уровнем
P_LEV = [3, 5]                # плечо
GRID_MULT = 1.5               # множитель объёма колена


def fetch_history():
    if os.path.exists(CACHE):
        with open(CACHE) as fh:
            data = json.load(fh)
        if data["symbol"] == config.SYMBOL and data["interval"] == config.INTERVAL:
            return data["candles"]
    session = HTTP(testnet=False)
    end = int(time.time() * 1000)
    start = end - DAYS * 86400 * 1000
    out, cursor = [], end
    while cursor > start:
        r = session.get_kline(category="linear", symbol=config.SYMBOL,
                              interval=config.INTERVAL, limit=1000, end=cursor)
        rows = r["result"]["list"]
        if not rows:
            break
        out = rows[::-1] + out
        oldest = int(rows[-1][0])
        if oldest >= cursor:
            break
        cursor = oldest - 1
        time.sleep(0.12)
    candles = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])]
               for x in out if int(x[0]) >= start][:-1]
    with open(CACHE, "w") as fh:
        json.dump({"symbol": config.SYMBOL, "interval": config.INTERVAL,
                   "candles": candles}, fh)
    return candles


def calc_rsi(closes, period):
    rsi = [None] * len(closes)
    gain = loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gain += max(d, 0)
        loss += max(-d, 0)
    ag, al = gain / period, loss / period
    rsi[period] = 100 - 100 / (1 + (ag / al if al else float("inf")))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
        rsi[i] = 100 - 100 / (1 + (ag / al if al else float("inf")))
    return rsi


def rolling_extremes(candles, window):
    """(range_low[i], range_high[i]) за window свечей ДО i включительно."""
    lows = [None] * len(candles)
    highs = [None] * len(candles)
    dq_min, dq_max = deque(), deque()
    for i, c in enumerate(candles):
        l, h = c[3], c[2]
        while dq_min and candles[dq_min[-1]][3] >= l:
            dq_min.pop()
        dq_min.append(i)
        while dq_max and candles[dq_max[-1]][2] <= h:
            dq_max.pop()
        dq_max.append(i)
        while dq_min[0] <= i - window:
            dq_min.popleft()
        while dq_max[0] <= i - window:
            dq_max.popleft()
        lows[i] = candles[dq_min[0]][3]
        highs[i] = candles[dq_max[0]][2]
    return lows, highs


def run(candles, rsi, rlow, rhigh, rsi_os, grid_step, grid_levels,
        tp_pct, sweep, lev):
    rsi_ob = 100 - rsi_os
    balance = START_BALANCE
    peak, max_dd = balance, 0.0
    trades = wins = 0
    pos = None  # dict: side, fills[(price,qty)], stop, next_add, adds_left, opened_i
    weights = [GRID_MULT ** k for k in range(grid_levels)]
    wsum = sum(weights)

    def margin_k(k):  # маржа k-го колена
        return MARGIN_TOTAL * weights[k] / wsum

    def avg_entry(p):
        q = sum(f[1] for f in p["fills"])
        return sum(f[0] * f[1] for f in p["fills"]) / q, q

    def book(pnl):
        nonlocal balance, trades, wins, peak, max_dd
        balance += pnl
        trades += 1
        if pnl > 0:
            wins += 1
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak)

    start_i = max(WINDOW, RSI_PERIOD + 1)
    for i in range(start_i, len(candles)):
        ts, o, h, l, c = candles[i]
        if pos:
            sgn = 1 if pos["side"] == "L" else -1
            avg, qty = avg_entry(pos)
            margin_used = sum(margin_k(k) for k in range(len(pos["fills"])))
            # ликвидация (изолированно): убыток >= марже
            p_liq = avg - sgn * margin_used / qty
            hit_liq = l <= p_liq if sgn == 1 else h >= p_liq
            hit_stop = l <= pos["stop"] if sgn == 1 else h >= pos["stop"]
            tp_price = avg * (1 + sgn * tp_pct)
            hit_tp = h >= tp_price if sgn == 1 else l <= tp_price
            if hit_liq:
                book(-margin_used - sum(f[0] * f[1] for f in pos["fills"]) * TAKER)
                pos = None
            elif hit_stop:
                pnl = sgn * (pos["stop"] - avg) * qty - qty * pos["stop"] * TAKER
                book(pnl)
                pos = None
            elif hit_tp:
                pnl = sgn * (tp_price - avg) * qty - qty * tp_price * MAKER
                book(pnl)
                pos = None
            else:
                # докупка сеткой (лимитка -> мейкер)
                if pos["adds_left"] > 0:
                    add_p = pos["next_add"]
                    hit_add = l <= add_p if sgn == 1 else h >= add_p
                    if hit_add:
                        k = len(pos["fills"])
                        q_add = margin_k(k) * lev / add_p
                        balance -= q_add * add_p * MAKER
                        pos["fills"].append((add_p, q_add))
                        pos["adds_left"] -= 1
                        pos["next_add"] = add_p * (1 - sgn * grid_step)
                # таймаут: RSI вернулся к середине, а тейка нет
                if pos and i - pos["opened_i"] > MAX_BARS:
                    r = rsi[i]
                    if r is not None and (r >= 50 if sgn == 1 else r <= 50):
                        avg, qty = avg_entry(pos)
                        pnl = sgn * (c - avg) * qty - qty * c * TAKER
                        book(pnl)
                        pos = None
            if balance < MARGIN_TOTAL:
                return dict(balance=balance, trades=trades, wins=wins,
                            max_dd=max_dd, ruined=True)
            if pos:
                continue  # позиция открыта — новых входов не ищем

        # --- поиск входа ---
        r_now, r_prev = rsi[i], rsi[i - 1]
        if r_now is None or r_prev is None:
            continue
        rng = rhigh[i] - rlow[i]
        if rng <= 0:
            continue
        zone_pos = (c - rlow[i]) / rng
        side = None
        if r_prev >= rsi_os and r_now < rsi_os and zone_pos < ZONE:
            side = "L"
        elif r_prev <= rsi_ob and r_now > rsi_ob and zone_pos > 1 - ZONE:
            side = "S"
        if not side:
            continue
        sgn = 1 if side == "L" else -1
        q0 = margin_k(0) * lev / c
        balance -= q0 * c * TAKER
        stop = rlow[i] * (1 - sweep) if side == "L" else rhigh[i] * (1 + sweep)
        # стоп не дальше ликвидации первого колена не проверяем — ликвидация
        # моделируется отдельно и худший случай учтён
        pos = dict(side=side, fills=[(c, q0)], stop=stop,
                   next_add=c * (1 - sgn * grid_step),
                   adds_left=grid_levels - 1, opened_i=i)

    if pos:
        sgn = 1 if pos["side"] == "L" else -1
        avg, qty = avg_entry(pos)
        c = candles[-1][4]
        book(sgn * (c - avg) * qty - qty * c * TAKER)
    return dict(balance=balance, trades=trades, wins=wins,
                max_dd=max_dd, ruined=False)


def main():
    print(f"История {config.SYMBOL} {config.INTERVAL}m за {DAYS} дней...")
    candles = fetch_history()
    closes = [c[4] for c in candles]
    print(f"Свечей: {len(candles)}")
    rsi = calc_rsi(closes, RSI_PERIOD)
    rlow, rhigh = rolling_extremes(candles, WINDOW)
    hold = (closes[-1] / closes[0] - 1) * 100
    print(f"Buy&hold за период: {hold:+.1f}%\n")

    combos = list(itertools.product(P_RSI_OS, P_GRID_STEP, P_GRID_LEVELS,
                                    P_TP, P_SWEEP, P_LEV))
    results = []
    t0 = time.time()
    for os_, step, levels, tp, sweep, lev in combos:
        r = run(candles, rsi, rlow, rhigh, os_, step, levels, tp, sweep, lev)
        results.append(((os_, step, levels, tp, sweep, lev), r))
    print(f"Прогнано {len(combos)} комбинаций за {time.time()-t0:.1f} c\n")

    results.sort(key=lambda x: x[1]["balance"], reverse=True)
    hdr = (f"{'RSI':>4} {'шаг':>6} {'колен':>5} {'TP':>6} {'буфер':>6} {'плечо':>5} | "
           f"{'Итог':>8} {'Доход':>8} {'Сделок':>6} {'WR':>6} {'MaxDD':>6} Слив")
    print(hdr)
    print("-" * len(hdr))
    for (os_, step, levels, tp, sweep, lev), r in results[:12]:
        ret = (r["balance"] / START_BALANCE - 1) * 100
        wr = r["wins"] / r["trades"] * 100 if r["trades"] else 0
        print(f"{os_:>4} {step*100:>5.1f}% {levels:>5} {tp*100:>5.1f}% "
              f"{sweep*100:>5.1f}% {('x'+str(lev)):>5} | {r['balance']:>8.2f} "
              f"{ret:>+7.1f}% {r['trades']:>6} {wr:>5.1f}% {r['max_dd']*100:>5.1f}% "
              f"{'ДА' if r['ruined'] else 'нет'}")
    print("\nХудшие 3:")
    for (os_, step, levels, tp, sweep, lev), r in results[-3:]:
        ret = (r["balance"] / START_BALANCE - 1) * 100
        print(f"  RSI {os_}, шаг {step*100:.1f}%, {levels} колен, TP {tp*100:.1f}%, "
              f"буфер {sweep*100:.1f}%, x{lev}: {ret:+.1f}%"
              f"{' (СЛИТ)' if r['ruined'] else ''}")


if __name__ == "__main__":
    main()
