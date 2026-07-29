# -*- coding: utf-8 -*-
"""ATR-версия стратегии RSI-сетка + турбо-режим x20.

Новое:
  - шаг сетки / тейк / буфер стопа задаются в долях ATR (суточный ATR на 15m,
    период 96) -> один конфиг масштабируется под волатильность любой монеты;
  - фильтр "падающего ножа": вход запрещён, если за последние 8 свечей цена
    прошла против входа больше K x ATR;
  - кулдаун после стопа: N свечей без новых входов;
  - режим 'tight' для x20: стоп короткий, от средней цены (K x ATR), т.к.
    ликвидация на x20 наступает на ~4.7% и "стоп за уровнем" невозможен.

Депозит в тестах: 20 USDT (маржа 5 на цикл). Метрика отбора — худшее из
двух полугодий, по всем монетам сразу (для универсального конфига).
"""

import itertools

import backtest_rsi_grid as bg
import config

TAKER, MAKER = bg.TAKER, bg.MAKER
START = 20.0
MARGIN = 5.0
ATR_N = 96
WINDOW = 400
MAX_BARS = 192
MM = 0.95  # запас до ликвидации (маинтенанс-маржа)

SYMBOLS = ["DOGEUSDT", "LTCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]


def calc_atr_pct(candles, n=ATR_N):
    atr = [None] * len(candles)
    trs = []
    prev_c = candles[0][4]
    val = None
    for i in range(1, len(candles)):
        _, o, h, l, c = candles[i]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        prev_c = c
        if val is None:
            trs.append(tr)
            if len(trs) == n:
                val = sum(trs) / n
                atr[i] = val / c
        else:
            val = (val * (n - 1) + tr) / n
            atr[i] = val / c
    return atr


def prep(candles):
    closes = [c[4] for c in candles]
    return dict(
        rsi=bg.calc_rsi(closes, bg.RSI_PERIOD),
        ext=bg.rolling_extremes(candles, WINDOW),
        atr=calc_atr_pct(candles),
        closes=closes,
    )


def run3(candles, pre, p):
    """p: os, zone, levels, mult, step_k, tp_k, sweep_k, stop_k, knife,
    cooldown, lev, mode ('range'|'tight')."""
    rsi, (rlow, rhigh), atr = pre["rsi"], pre["ext"], pre["atr"]
    closes = pre["closes"]
    rsi_os, rsi_ob = p["os"], 100 - p["os"]
    balance, peak, max_dd = START, START, 0.0
    trades = wins = 0
    pos = None
    cooldown_until = 0
    weights = [p["mult"] ** k for k in range(p["levels"])]
    m_k = [MARGIN * w / sum(weights) for w in weights]

    def close_cycle(pnl):
        nonlocal balance, trades, wins, peak, max_dd
        balance += pnl
        trades += 1
        if pnl > 0:
            wins += 1
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak)

    start_i = max(WINDOW, ATR_N + 2)
    for i in range(start_i, len(candles)):
        ts, o, h, l, c = candles[i]
        if pos:
            sgn = 1 if pos["side"] == "L" else -1
            q = sum(f[1] for f in pos["fills"])
            avg = sum(fp * fq for fp, fq in pos["fills"]) / q
            mused = sum(m_k[k] for k in range(len(pos["fills"])))
            p_liq = avg - sgn * (mused * MM) / q
            a0 = pos["atr0"]
            tp = avg * (1 + sgn * p["tp_k"] * a0)
            if p["mode"] == "tight":
                stop = avg * (1 - sgn * p["stop_k"] * a0)
            else:
                stop = pos["stop"]

            hit_liq = l <= p_liq if sgn == 1 else h >= p_liq
            hit_stop = l <= stop if sgn == 1 else h >= stop
            hit_tp = h >= tp if sgn == 1 else l <= tp
            if hit_liq and (not hit_stop or (sgn == 1 and p_liq >= stop) or
                            (sgn == -1 and p_liq <= stop)):
                close_cycle(-mused * MM - q * avg * TAKER)
                pos = None
                cooldown_until = i + p["cooldown"]
            elif hit_stop:
                close_cycle(sgn * (stop - avg) * q - q * stop * TAKER)
                pos = None
                cooldown_until = i + p["cooldown"]
            elif hit_tp:
                close_cycle(sgn * (tp - avg) * q - q * tp * MAKER)
                pos = None
            else:
                if pos["adds"]:
                    ap, aq = pos["adds"][0]
                    if (l <= ap if sgn == 1 else h >= ap):
                        balance -= aq * ap * MAKER
                        pos["fills"].append((ap, aq))
                        pos["adds"].pop(0)
                if pos and i - pos["opened_i"] > MAX_BARS:
                    r = rsi[i]
                    if r is not None and (r >= 50 if sgn == 1 else r <= 50):
                        close_cycle(sgn * (c - avg) * q - q * c * TAKER)
                        pos = None
            if balance < MARGIN:
                return dict(balance=balance, trades=trades, wins=wins,
                            max_dd=max_dd, ruined=True)
            if pos:
                continue

        if i < cooldown_until:
            continue
        r_now, r_prev, a = rsi[i], rsi[i - 1], atr[i]
        if r_now is None or r_prev is None or not a:
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
        if side and p["knife"]:
            move = (closes[i - 8] - c) / c
            if side == "L" and move > p["knife"] * a:
                side = None
            elif side == "S" and -move > p["knife"] * a:
                side = None
        if not side:
            continue
        sgn = 1 if side == "L" else -1
        q0 = m_k[0] * p["lev"] / c
        balance -= q0 * c * TAKER
        stop = (rlow[i] * (1 - p["sweep_k"] * a) if side == "L"
                else rhigh[i] * (1 + p["sweep_k"] * a))
        adds = []
        ap = c
        for k in range(1, p["levels"]):
            ap = ap * (1 - sgn * p["step_k"] * a)
            adds.append((ap, m_k[k] * p["lev"] / ap))
        pos = dict(side=side, fills=[(c, q0)], stop=stop, adds=adds,
                   opened_i=i, atr0=a)

    if pos:
        sgn = 1 if pos["side"] == "L" else -1
        q = sum(f[1] for f in pos["fills"])
        avg = sum(fp * fq for fp, fq in pos["fills"]) / q
        close_cycle(sgn * (closes[-1] - avg) * q)
    return dict(balance=balance, trades=trades, wins=wins,
                max_dd=max_dd, ruined=False)


def load_all():
    data = {}
    for s in SYMBOLS:
        config.SYMBOL = s
        bg.CACHE = f"history_{s}.json"
        candles = bg.fetch_history()
        half = len(candles) // 2
        data[s] = dict(
            full=(candles, prep(candles)),
            h1=(candles[:half], prep(candles[:half])),
            h2=(candles[half:], prep(candles[half:])),
        )
    return data


def ret(r):
    return (r["balance"] / START - 1) * 100


def eval_config(data, p):
    """Возвращает {sym: (full_res, ret_h1, ret_h2)} и worst-метрику."""
    out, worst = {}, 1e9
    for s in SYMBOLS:
        rf = run3(*data[s]["full"], p)
        r1 = ret(run3(*data[s]["h1"], p))
        r2 = ret(run3(*data[s]["h2"], p))
        out[s] = (rf, r1, r2)
        worst = min(worst, r1, r2)
    return out, worst


def main():
    data = load_all()
    print("Данные загружены.\n")

    # === Универсальный конфиг, режим range, x5 ===
    base = dict(levels=3, mult=1.5, stop_k=0, lev=5, mode="range")
    grid = list(itertools.product(
        [25, 30],          # os
        [0.75, 1.25],      # step_k (x ATR)
        [2.0, 3.0, 4.0],   # tp_k
        [1.5, 2.5],        # sweep_k
        [0.25, 0.35],      # zone
        [None, 2.5],       # knife
        [0, 16],           # cooldown
    ))
    best = []
    for os_, sk, tk, swk, zn, kn, cd in grid:
        p = dict(base, os=os_, step_k=sk, tp_k=tk, sweep_k=swk, zone=zn,
                 knife=kn, cooldown=cd)
        out, worst = eval_config(data, p)
        avg = sum(ret(out[s][0]) for s in SYMBOLS) / len(SYMBOLS)
        best.append((worst, avg, p, out))
    best.sort(key=lambda x: (x[0], x[1]), reverse=True)

    print("=== УНИВЕРСАЛЬНЫЙ КОНФИГ x5 (топ-3 по худшему полугодию всех монет) ===")
    for worst, avg, p, out in best[:3]:
        print(f"\nRSI{p['os']} шаг{p['step_k']}xATR TP{p['tp_k']}xATR "
              f"буф{p['sweep_k']}xATR зона{p['zone']} нож={p['knife']} "
              f"кулдаун={p['cooldown']} | худшее полугодие {worst:+.1f}%, "
              f"средний год {avg:+.1f}%")
        for s in SYMBOLS:
            rf, r1, r2 = out[s]
            wr = rf["wins"] / rf["trades"] * 100 if rf["trades"] else 0
            print(f"   {s:9} год {ret(rf):+8.1f}% | WR {wr:4.1f}% | "
                  f"DD {rf['max_dd']*100:4.1f}% | {r1:+7.1f}% / {r2:+7.1f}%")

    # === ТУРБО x20, режим tight, по монетам ===
    tbase = dict(mult=1.5, sweep_k=0, lev=20, mode="tight", knife=2.5,
                 cooldown=32, step_k=1.0)
    tgrid = list(itertools.product(
        [20, 25],        # os — вход глубже
        [0.15, 0.25],    # zone — только самый край
        [1, 2],          # levels
        [1.5, 2.5],      # stop_k
        [2.0, 3.0],      # tp_k
    ))
    print("\n\n=== ТУРБО x20 (стоп от средней, K x ATR; лучший на монету) ===")
    for s in SYMBOLS:
        bs = None
        for os_, zn, lv, stk, tk in tgrid:
            p = dict(tbase, os=os_, zone=zn, levels=lv, stop_k=stk, tp_k=tk)
            rf = run3(*data[s]["full"], p)
            r1 = ret(run3(*data[s]["h1"], p))
            r2 = ret(run3(*data[s]["h2"], p))
            worst = min(r1, r2)
            if bs is None or worst > bs[0]:
                bs = (worst, p, rf, r1, r2)
        worst, p, rf, r1, r2 = bs
        wr = rf["wins"] / rf["trades"] * 100 if rf["trades"] else 0
        print(f"{s:9} год {ret(rf):+8.1f}% | WR {wr:4.1f}% | DD {rf['max_dd']*100:4.1f}% "
              f"| {r1:+7.1f}% / {r2:+7.1f}% | слив {'ДА' if rf['ruined'] else 'нет'} "
              f"| RSI{p['os']} зона{p['zone']} колен{p['levels']} "
              f"стоп{p['stop_k']}xATR TP{p['tp_k']}xATR")


if __name__ == "__main__":
    main()
