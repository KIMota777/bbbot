# -*- coding: utf-8 -*-
"""Эволюционный отбор параметров стратегии RSI-сетка (без турбо, x5).

Цель — БЫСТРЫЙ и СТАБИЛЬНЫЙ доход, а не годовая сумма:
  fitness = (P25 месячных доходов + 0.5 x медиана)
            x min(1, сделок_в_месяц / 6)          # штраф за редкие сделки
            x 96 / (96 + среднее_удержание_баров)  # штраф за долгое удержание
            - 50 при сливе депозита

Схема анти-подгонки:
  - эволюция идёт на первых 8 месяцах истории (train);
  - топ-5 финалистов сдают экзамен на последних 4 месяцах (OOS);
  - победитель дополнительно проверяется на 5m-свечах (120 дней).

Запуск: python evolution.py
"""

import itertools
import json
import os
import random
import statistics
import time
from collections import deque

from pybit.unified_trading import HTTP

import backtest_rsi_grid as bg
import config

random.seed(42)

SYMBOLS = ["DOGEUSDT", "LTCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
TAKER, MAKER = 0.00055, 0.0002
START, MARGIN = 20.0, 5.0
LEV = 5
MM = 0.95
RSI_PERIOD = 14
MONTH_MS = 30 * 86400 * 1000

POP, GENS = 32, 12
ELITE = 4


def save_artifact(name, data, log=print):
    """Запись результатов волны БЕЗ затирания артефакта прошлого прогона.

    Зачем. Файлы *_winners.json / *_final.json — единственное, что осталось от
    волн 08.2026: конфиги в config.py отбирались протекавшей схемой, и доказать
    это можно только их собственными записями (например, отзыв вердикта по SOL
    в evolution11.py прямо ссылается на записанный там adopt:true). Прогон,
    который открывает тот же файл на запись, уничтожает доказательство, на
    которое ссылается текст.

    Правило простое: существующий файл не трогаем, новый пишем рядом с меткой
    времени и ГРОМКО говорим об этом. Молчать нельзя ещё и потому, что
    следующие волны читают короткое имя (v3 читает evolution2_winners.json,
    v6 — evolution5_winners.json и evolution4_winners.json, v7 —
    evolution6_winners.json): пока файл не переименован руками, они возьмут
    СТАРЫЙ результат.

    Живёт здесь, а не в evolution4, потому что ранние волны (v1/v2/v3) импорт
    evolution4 сделать не могут — он сам импортирует их, вышла бы петля. До
    четвёртого круга защита стояла только на поздних волнах, и первый же запуск
    v1/v2/v3 стирал их артефакты. e4.save_artifact — псевдоним этой функции.
    """
    if not os.path.exists(name):
        with open(name, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, default=float)
        return name
    stem, ext = os.path.splitext(name)
    out = f"{stem}_{time.strftime('%Y%m%d-%H%M%S')}{ext}"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, default=float)
    log(f"ВНИМАНИЕ: {name} уже существует — это артефакт прошлого прогона, он "
        f"НЕ переписан. Результаты этого прогона в {out}. Волны, которые читают "
        f"{name}, возьмут СТАРЫЙ файл, пока имя не заменено вручную")
    return out

# Ограничение направления сделок: "B" = оба, "L" = только лонг, "S" = только шорт
DIRECTION = "B"

# ген: (мин, макс, целый?)
GENES = {
    "rsi_os":   (15, 40, True),
    "zone":     (0.15, 0.55, False),
    "window":   (150, 900, True),
    "step":     (0.005, 0.03, False),
    "levels":   (2, 4, True),
    "mult":     (1.0, 2.0, False),
    "tp":       (0.008, 0.05, False),
    "sweep":    (0.005, 0.035, False),
    "max_bars": (24, 288, True),
    "cooldown": (0, 48, True),
    "knife":    (0.0, 3.5, False),  # 0 = фильтр ножа выключен
}


# ---------- данные ----------

def fetch(symbol, interval, days):
    cache = f"history_{symbol}_{interval}m_{days}d.json"
    if os.path.exists(cache):
        with open(cache) as fh:
            return json.load(fh)
    session = HTTP(testnet=False)
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    out, cursor = [], end
    while cursor > start:
        r = session.get_kline(category="linear", symbol=symbol,
                              interval=interval, limit=1000, end=cursor)
        rows = r["result"]["list"]
        if not rows:
            break
        out = rows[::-1] + out
        oldest = int(rows[-1][0])
        if oldest >= cursor:
            break
        cursor = oldest - 1
        time.sleep(0.1)
    candles = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])]
               for x in out if int(x[0]) >= start][:-1]
    with open(cache, "w") as fh:
        json.dump(candles, fh)
    return candles


def rolling_extremes(candles, window):
    lows = [0.0] * len(candles)
    highs = [0.0] * len(candles)
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


def calc_atr_pct(candles, n=96):
    atr = [None] * len(candles)
    prev_c = candles[0][4]
    val, trs = None, []
    for i in range(1, len(candles)):
        _, o, h, l, c = candles[i]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        prev_c = c
        if val is None:
            trs.append(tr)
            if len(trs) == n:
                val = sum(trs) / n
        else:
            val = (val * (n - 1) + tr) / n
        if val is not None:
            atr[i] = val / c
    return atr


def prep(candles):
    closes = [c[4] for c in candles]
    return dict(rsi=bg.calc_rsi(closes, RSI_PERIOD), closes=closes,
                atr=calc_atr_pct(candles))


# ---------- движок с помесячной статистикой ----------

def run4(candles, pre, g):
    rsi, closes, atr = pre["rsi"], pre["closes"], pre["atr"]
    rlow, rhigh = rolling_extremes(candles, g["window"])
    rsi_os, rsi_ob = g["rsi_os"], 100 - g["rsi_os"]
    balance, peak, max_dd = START, START, 0.0
    trades = wins = 0
    monthly = {}
    hold_bars = []
    pos = None
    cooldown_until = 0
    t0 = candles[0][0]
    weights = [g["mult"] ** k for k in range(g["levels"])]
    m_k = [MARGIN * w / sum(weights) for w in weights]

    def book(pnl, ts, opened_i, i):
        nonlocal balance, trades, wins, peak, max_dd
        balance += pnl
        trades += 1
        if pnl > 0:
            wins += 1
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak)
        monthly[(ts - t0) // MONTH_MS] = monthly.get((ts - t0) // MONTH_MS, 0) + pnl
        hold_bars.append(i - opened_i)

    start_i = max(g["window"], RSI_PERIOD + 1, 98)
    for i in range(start_i, len(candles)):
        ts, o, h, l, c = candles[i]
        if pos:
            sgn = 1 if pos["side"] == "L" else -1
            q = sum(f[1] for f in pos["fills"])
            avg = sum(fp * fq for fp, fq in pos["fills"]) / q
            mused = sum(m_k[k] for k in range(len(pos["fills"])))
            p_liq = avg - sgn * (mused * MM) / q
            tp = avg * (1 + sgn * g["tp"])
            stop = pos["stop"]
            hit_liq = l <= p_liq if sgn == 1 else h >= p_liq
            hit_stop = l <= stop if sgn == 1 else h >= stop
            hit_tp = h >= tp if sgn == 1 else l <= tp
            if hit_liq and (not hit_stop or (sgn == 1 and p_liq >= stop) or
                            (sgn == -1 and p_liq <= stop)):
                book(-mused * MM - q * avg * TAKER, ts, pos["opened_i"], i)
                pos = None
                cooldown_until = i + g["cooldown"]
            elif hit_stop:
                book(sgn * (stop - avg) * q - q * stop * TAKER, ts,
                     pos["opened_i"], i)
                pos = None
                cooldown_until = i + g["cooldown"]
            elif hit_tp:
                book(sgn * (tp - avg) * q - q * tp * MAKER, ts,
                     pos["opened_i"], i)
                pos = None
            else:
                if pos["adds"]:
                    ap, aq = pos["adds"][0]
                    if (l <= ap if sgn == 1 else h >= ap):
                        balance -= aq * ap * MAKER
                        pos["fills"].append((ap, aq))
                        pos["adds"].pop(0)
                if pos and i - pos["opened_i"] > g["max_bars"]:
                    r = rsi[i]
                    if r is not None and (r >= 50 if sgn == 1 else r <= 50):
                        book(sgn * (c - avg) * q - q * c * TAKER, ts,
                             pos["opened_i"], i)
                        pos = None
            if balance < MARGIN:
                return dict(balance=balance, trades=trades, wins=wins,
                            max_dd=max_dd, ruined=True, monthly=monthly,
                            hold=hold_bars, months=(candles[-1][0] - t0) / MONTH_MS)
            if pos:
                continue

        if i < cooldown_until:
            continue
        r_now, r_prev = rsi[i], rsi[i - 1]
        if r_now is None or r_prev is None:
            continue
        rng = rhigh[i] - rlow[i]
        if rng <= 0:
            continue
        zpos = (c - rlow[i]) / rng
        side = None
        if r_prev >= rsi_os and r_now < rsi_os and zpos < g["zone"]:
            side = "L"
        elif r_prev <= rsi_ob and r_now > rsi_ob and zpos > 1 - g["zone"]:
            side = "S"
        if side and DIRECTION != "B" and side != DIRECTION:
            side = None
        if side and g["knife"] > 0.05 and atr[i]:
            move = (closes[i - 8] - c) / c
            if side == "L" and move > g["knife"] * atr[i]:
                side = None
            elif side == "S" and -move > g["knife"] * atr[i]:
                side = None
        if not side:
            continue
        sgn = 1 if side == "L" else -1
        q0 = m_k[0] * LEV / c
        balance -= q0 * c * TAKER
        stop = (rlow[i] * (1 - g["sweep"]) if side == "L"
                else rhigh[i] * (1 + g["sweep"]))
        adds = []
        ap = c
        for k in range(1, g["levels"]):
            ap = ap * (1 - sgn * g["step"])
            adds.append((ap, m_k[k] * LEV / ap))
        pos = dict(side=side, fills=[(c, q0)], stop=stop, adds=adds, opened_i=i)

    return dict(balance=balance, trades=trades, wins=wins, max_dd=max_dd,
                ruined=False, monthly=monthly, hold=hold_bars,
                months=(candles[-1][0] - t0) / MONTH_MS)


def percentile(vals, p):
    s = sorted(vals)
    if not s:
        return 0.0
    k = (len(s) - 1) * p
    f = int(k)
    return s[f] + (s[min(f + 1, len(s) - 1)] - s[f]) * (k - f)


def stats(r):
    n_months = max(1, int(r["months"]))
    rets = [(r["monthly"].get(m, 0.0) / START) * 100 for m in range(n_months)]
    med = statistics.median(rets) if rets else 0.0
    p25 = percentile(rets, 0.25)
    pos_share = sum(1 for x in rets if x > 0) / len(rets) if rets else 0
    tpm = r["trades"] / n_months
    avg_hold = statistics.mean(r["hold"]) if r["hold"] else 999
    return dict(med=med, p25=p25, pos_share=pos_share, tpm=tpm,
                avg_hold=avg_hold, rets=rets)


def fitness(r):
    st = stats(r)
    f = (st["p25"] + 0.5 * st["med"])
    f *= min(1.0, st["tpm"] / 6.0)
    f *= 96.0 / (96.0 + st["avg_hold"])
    if r["ruined"]:
        f -= 50
    return f


# ---------- генетика ----------

def rand_genome():
    g = {}
    for k, (lo, hi, is_int) in GENES.items():
        v = random.uniform(lo, hi)
        g[k] = int(round(v)) if is_int else v
    return g


def clamp(g):
    out = {}
    for k, (lo, hi, is_int) in GENES.items():
        v = min(hi, max(lo, g[k]))
        out[k] = int(round(v)) if is_int else v
    return out


def mutate(g):
    out = dict(g)
    for k, (lo, hi, is_int) in GENES.items():
        if random.random() < 0.25:
            out[k] = out[k] + random.gauss(0, 0.15 * (hi - lo))
    return clamp(out)


def crossover(a, b):
    return clamp({k: (a[k] if random.random() < 0.5 else b[k]) for k in GENES})


def evolve(candles, pre, seed_genomes, log_prefix):
    cache = {}

    def eval_g(g):
        key = tuple(round(g[k], 4) if not GENES[k][2] else g[k] for k in GENES)
        if key not in cache:
            cache[key] = fitness(run4(candles, pre, g))
        return cache[key]

    pop = [clamp(dict(s)) for s in seed_genomes]
    while len(pop) < POP:
        pop.append(rand_genome())
    scored = sorted(((eval_g(g), g) for g in pop), key=lambda x: -x[0])
    for gen in range(GENS):
        new = [g for _, g in scored[:ELITE]]
        while len(new) < POP:
            if random.random() < 0.3:
                new.append(mutate(scored[random.randrange(ELITE)][1]))
            else:
                a = max(random.sample(scored, 3), key=lambda x: x[0])[1]
                b = max(random.sample(scored, 3), key=lambda x: x[0])[1]
                new.append(mutate(crossover(a, b)))
        scored = sorted(((eval_g(g), g) for g in new), key=lambda x: -x[0])
        print(f"  {log_prefix} поколение {gen+1:2}: best fitness {scored[0][0]:+.2f}")
    return scored


def fmt_genome(g):
    return (f"RSI{g['rsi_os']} зона{g['zone']:.2f} окно{g['window']} "
            f"шаг{g['step']*100:.1f}% x{g['levels']}кол m{g['mult']:.1f} "
            f"TP{g['tp']*100:.1f}% буф{g['sweep']*100:.1f}% "
            f"таймаут{g['max_bars']} кд{g['cooldown']} нож{g['knife']:.1f}")


def report(r, label):
    st = stats(r)
    ret_total = (r["balance"] / START - 1) * 100
    wr = r["wins"] / r["trades"] * 100 if r["trades"] else 0
    hold_h = st["avg_hold"] * int(config.INTERVAL) / 60
    print(f"    {label}: итог {ret_total:+7.1f}% | мес.медиана {st['med']:+5.2f}% "
          f"| P25 {st['p25']:+5.2f}% | приб.мес {st['pos_share']*100:3.0f}% "
          f"| сделок/мес {st['tpm']:4.1f} | удерж {hold_h:4.1f}ч "
          f"| WR {wr:4.1f}% | DD {r['max_dd']*100:4.1f}%"
          f"{' СЛИВ' if r['ruined'] else ''}")
    return st


CURRENT_SEEDS = {
    s: dict(rsi_os=config.SYMBOL_PARAMS[s]["normal"]["rsi_os"],
            zone=config.SYMBOL_PARAMS[s]["normal"].get(
                "zone", config.SYMBOL_PARAMS[s]["normal"].get("zone_l", 0.25)),
            window=400,
            step=config.SYMBOL_PARAMS[s]["normal"]["step"], levels=3, mult=1.5,
            tp=config.SYMBOL_PARAMS[s]["normal"]["tp"],
            sweep=config.SYMBOL_PARAMS[s]["normal"]["sweep"],
            max_bars=192, cooldown=0, knife=0.0)
    for s in SYMBOLS
}


def main():
    winners = {}
    for sym in SYMBOLS:
        print(f"\n================ {sym} ================")
        candles = fetch(sym, config.INTERVAL, 365)
        split = int(len(candles) * 2 / 3)  # ~8 месяцев train / 4 месяца OOS
        train, oos = candles[:split], candles[split:]
        pre_tr, pre_oos = prep(train), prep(oos)

        scored = evolve(train, pre_tr, [CURRENT_SEEDS[sym]], sym[:3])

        # топ-5 уникальных на экзамен OOS
        seen, finalists = set(), []
        for f, g in scored:
            key = tuple(g[k] for k in GENES)
            if key not in seen:
                seen.add(key)
                finalists.append((f, g))
            if len(finalists) == 5:
                break

        print(f"  --- OOS-экзамен (последние 4 месяца, не виденные эволюцией) ---")
        best = None
        for f, g in finalists:
            r_oos = run4(oos, pre_oos, g)
            st = stats(r_oos)
            score = st["p25"] + 0.5 * st["med"] - (100 if r_oos["ruined"] else 0)
            if best is None or score > best[0]:
                best = (score, g, r_oos)
        _, g_win, r_win = best
        print(f"  Победитель: {fmt_genome(g_win)}")
        report(r_win, "OOS 15m")

        r_cur = run4(oos, pre_oos, CURRENT_SEEDS[sym])
        report(r_cur, "OOS 15m (текущий конфиг)")

        # проверка на 5-минутках (120 дней)
        c5 = fetch(sym, "5", 120)
        r5 = run4(c5, prep(c5), g_win)
        report(r5, "5m 120д (победитель)")

        winners[sym] = dict(genome=g_win,
                            oos=stats(r_win), oos_ruined=r_win["ruined"])

    out = save_artifact("evolution_winners.json", winners)
    print(f"\nПобедители сохранены в {out}")


if __name__ == "__main__":
    main()
