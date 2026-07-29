# -*- coding: utf-8 -*-
"""Эксперименты со стратегией RSI-сетка: перебор параметров + новые механики.

Новое относительно backtest_rsi_grid.py:
  - zone / window / grid_mult как параметры;
  - partial: частичный тейк 50% на половине пути + стоп в безубыток;
  - trend: фильтр по EMA400 ('with' - по тренду, 'counter' - против, None - выкл).

Этапы:
  1) широкий перебор базовых параметров;
  2) к топ-конфигурациям добавляем механики;
  3) проверка лучших на двух половинах года отдельно (анти-подгонка).

Запуск: python experiment.py
"""

import itertools
import time

import backtest_rsi_grid as bg
import config

TAKER, MAKER = bg.TAKER, bg.MAKER
START = bg.START_BALANCE
MARGIN = bg.MARGIN_TOTAL
LEV = 5
MAX_BARS = 192
EMA_N = 400


def calc_ema(closes, n):
    ema = [None] * len(closes)
    if len(closes) < n:
        return ema
    s = sum(closes[:n]) / n
    ema[n - 1] = s
    a = 2 / (n + 1)
    for i in range(n, len(closes)):
        s = closes[i] * a + s * (1 - a)
        ema[i] = s
    return ema


def run2(candles, rsi, ext, ema, p):
    """ext: {window: (lows, highs)}; p: dict параметров."""
    rlow, rhigh = ext[p["window"]]
    rsi_os, rsi_ob = p["os"], 100 - p["os"]
    balance, peak, max_dd = START, START, 0.0
    trades = wins = 0
    pos = None
    weights = [p["mult"] ** k for k in range(p["levels"])]
    wsum = sum(weights)
    m_k = [MARGIN * w / wsum for w in weights]

    def close_cycle(pnl):
        nonlocal balance, trades, wins, peak, max_dd
        balance += pnl
        trades += 1
        if pnl > 0:
            wins += 1
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak)

    start_i = max(p["window"], bg.RSI_PERIOD + 1, EMA_N)
    for i in range(start_i, len(candles)):
        ts, o, h, l, c = candles[i]
        if pos:
            sgn = 1 if pos["side"] == "L" else -1
            q = sum(f[1] for f in pos["fills"])
            avg = sum(fp * fq for fp, fq in pos["fills"]) / q
            mused = sum(m_k[k] for k in range(len(pos["fills"])))
            p_liq = avg - sgn * mused / q
            tp_full = avg * (1 + sgn * p["tp"])
            tp_half = avg * (1 + sgn * p["tp"] / 2)
            stop = pos["stop"]

            hit_liq = l <= p_liq if sgn == 1 else h >= p_liq
            hit_stop = l <= stop if sgn == 1 else h >= stop
            hit_half = (h >= tp_half if sgn == 1 else l <= tp_half)
            hit_full = (h >= tp_full if sgn == 1 else l <= tp_full)

            if hit_liq:
                close_cycle(pos["realized"] - mused - q * avg * TAKER)
                pos = None
            elif hit_stop:
                pnl = sgn * (stop - avg) * q - q * stop * TAKER
                close_cycle(pos["realized"] + pnl)
                pos = None
            else:
                if p["partial"] and not pos["half_done"] and hit_half:
                    q_half = q / 2
                    pos["realized"] += (sgn * (tp_half - avg) * q_half
                                       - q_half * tp_half * MAKER)
                    # уменьшаем позицию наполовину, стоп в безубыток
                    pos["fills"] = [(avg, q_half)]
                    pos["stop"] = avg
                    pos["half_done"] = True
                    q = q_half
                if hit_full:
                    q = sum(f[1] for f in pos["fills"])
                    avg = sum(fp * fq for fp, fq in pos["fills"]) / q
                    pnl = sgn * (tp_full - avg) * q - q * tp_full * MAKER
                    close_cycle(pos["realized"] + pnl)
                    pos = None
                elif pos and pos["adds"]:
                    ap, aq, ak = pos["adds"][0]
                    if (l <= ap if sgn == 1 else h >= ap):
                        balance -= aq * ap * MAKER
                        pos["fills"].append((ap, aq))
                        pos["adds"].pop(0)
                if pos and i - pos["opened_i"] > MAX_BARS:
                    r = rsi[i]
                    if r is not None and (r >= 50 if sgn == 1 else r <= 50):
                        q = sum(f[1] for f in pos["fills"])
                        avg = sum(fp * fq for fp, fq in pos["fills"]) / q
                        pnl = sgn * (c - avg) * q - q * c * TAKER
                        close_cycle(pos["realized"] + pnl)
                        pos = None
            if balance < MARGIN:
                return dict(balance=balance, trades=trades, wins=wins,
                            max_dd=max_dd, ruined=True)
            if pos:
                continue

        r_now, r_prev = rsi[i], rsi[i - 1]
        if r_now is None or r_prev is None or ema[i] is None:
            continue
        rng = rhigh[i] - rlow[i]
        if rng <= 0:
            continue
        zpos = (c - rlow[i]) / rng
        side = None
        if r_prev >= rsi_os and r_now < rsi_os and zpos < p["zone"]:
            side = "L"
        elif r_prev <= rsi_ob and r_now > rsi_ob and zpos > 1 - p["zone"]:
            side = "S"
        if side and p["trend"]:
            above = c > ema[i]
            ok = (above if side == "L" else not above)
            if p["trend"] == "counter":
                ok = not ok
            if not ok:
                side = None
        if not side:
            continue
        sgn = 1 if side == "L" else -1
        q0 = m_k[0] * LEV / c
        balance -= q0 * c * TAKER
        stop = rlow[i] * (1 - p["sweep"]) if side == "L" else rhigh[i] * (1 + p["sweep"])
        adds = []
        ap = c
        for k in range(1, p["levels"]):
            ap = ap * (1 - sgn * p["step"])
            adds.append((ap, m_k[k] * LEV / ap, k))
        pos = dict(side=side, fills=[(c, q0)], stop=stop, adds=adds,
                   opened_i=i, realized=0.0, half_done=False)

    if pos:
        sgn = 1 if pos["side"] == "L" else -1
        q = sum(f[1] for f in pos["fills"])
        avg = sum(fp * fq for fp, fq in pos["fills"]) / q
        close_cycle(pos["realized"] + sgn * (candles[-1][4] - avg) * q)
    return dict(balance=balance, trades=trades, wins=wins,
                max_dd=max_dd, ruined=False)


def prep(candles):
    closes = [c[4] for c in candles]
    rsi = bg.calc_rsi(closes, bg.RSI_PERIOD)
    ext = {w: bg.rolling_extremes(candles, w) for w in (200, 400, 800)}
    ema = calc_ema(closes, EMA_N)
    return rsi, ext, ema


def fmt(p, r):
    ret = (r["balance"] / START - 1) * 100
    wr = r["wins"] / r["trades"] * 100 if r["trades"] else 0
    return (f"RSI{p['os']} шаг{p['step']*100:.1f}% x{p['levels']}кол "
            f"m{p['mult']} TP{p['tp']*100:.1f}% буф{p['sweep']*100:.1f}% "
            f"зона{p['zone']} окно{p['window']} "
            f"част={'да' if p['partial'] else 'нет'} тренд={p['trend'] or '-'} | "
            f"{ret:+7.1f}% | сделок {r['trades']:3} | WR {wr:5.1f}% | "
            f"DD {r['max_dd']*100:4.1f}%{' СЛИТ' if r['ruined'] else ''}")


def main():
    candles = bg.fetch_history()
    print(f"Свечей: {len(candles)}")
    rsi, ext, ema = prep(candles)

    # --- Этап 1: широкий перебор базы ---
    base = dict(mult=1.5, partial=False, trend=None)
    grid = list(itertools.product(
        [25, 30, 35],            # os
        [0.01, 0.015, 0.025],    # step
        [3, 4],                  # levels
        [0.02, 0.03, 0.04],      # tp
        [0.02, 0.03],            # sweep
        [0.25, 0.35, 0.5],       # zone
        [200, 400, 800],         # window
    ))
    t0 = time.time()
    res1 = []
    for os_, step, lv, tp, sw, zn, wn in grid:
        p = dict(base, os=os_, step=step, levels=lv, tp=tp, sweep=sw,
                 zone=zn, window=wn)
        res1.append((p, run2(candles, rsi, ext, ema, p)))
    print(f"Этап 1: {len(grid)} комбинаций за {time.time()-t0:.0f} c")
    res1.sort(key=lambda x: x[1]["balance"], reverse=True)

    # --- Этап 2: механики поверх топ-5 ---
    res2 = []
    for p0, _ in res1[:5]:
        for mult, partial, trend in itertools.product(
                [1.0, 1.5, 2.0], [False, True], [None, "with", "counter"]):
            p = dict(p0, mult=mult, partial=partial, trend=trend)
            res2.append((p, run2(candles, rsi, ext, ema, p)))
    print(f"Этап 2: +{len(res2)} прогонов с механиками")

    # --- Этап 3: устойчивость на половинах ---
    all_res = res1[:20] + res2
    all_res.sort(key=lambda x: x[1]["balance"], reverse=True)
    seen, cand = set(), []
    for p, r in all_res:
        key = tuple(sorted(p.items(), key=lambda kv: kv[0]))
        if key not in seen:
            seen.add(key)
            cand.append((p, r))
        if len(cand) >= 15:
            break

    half = len(candles) // 2
    c1, c2 = candles[:half], candles[half:]
    rsi1, ext1, ema1 = prep(c1)
    rsi2, ext2, ema2 = prep(c2)
    robust = []
    for p, r_full in cand:
        r1 = run2(c1, rsi1, ext1, ema1, p)
        r2 = run2(c2, rsi2, ext2, ema2, p)
        ret1 = (r1["balance"] / START - 1) * 100
        ret2 = (r2["balance"] / START - 1) * 100
        robust.append((p, r_full, ret1, ret2, min(ret1, ret2)))
    robust.sort(key=lambda x: x[4], reverse=True)

    print("\n=== ТОП-10 по устойчивости (мин. доход из двух полугодий) ===")
    for p, r_full, ret1, ret2, worst in robust[:10]:
        print(fmt(p, r_full))
        print(f"    1-е полугодие {ret1:+.1f}% | 2-е полугодие {ret2:+.1f}%")

    print("\n=== ТОП-5 просто по итогу за год (для сравнения) ===")
    for p, r in all_res[:5]:
        print(fmt(p, r))


if __name__ == "__main__":
    main()
