# -*- coding: utf-8 -*-
"""Эволюция v2: честный движок + walk-forward + расширенные гены.

Исправления движка относительно v1 (все делали результаты оптимистичнее):
  - если свеча дошла до стопа/ликвидации, сначала исполняются лимитки сетки
    выше стопа (по пути цены), стопится уже УВЕЛИЧЕННАЯ позиция;
  - funding rate: 0.01% за 8ч удержания позиции;
  - проскальзывание 0.03% на всех маркет-исполнениях (вход, стоп, таймаут);
  - комиссии входа/сетки включены в PnL цикла (честный winrate и месяцы).

Глубина:
  - 730 дней данных, 15m;
  - GA: популяция 48, 24 поколения, элита 6;
  - walk-forward: 3 фолда (train 12м -> OOS 4м; 16 -> 4; 20 -> 4);
    победитель = лучший СРЕДНИЙ результат по всем трём OOS-экзаменам.

Новые гены: период RSI {7,10,14,21}, раздельные зоны лонга/шорта,
перенос стопа в безубыток (be_move).

Штормовой фильтр (необязательный, ПО УМОЛЧАНИЮ ВЫКЛЮЧЕН): run5(..., storm=...)
принимает ряды storm_filter.build(...) и не открывает новые циклы в
экстремальном движении рынка. Та же функция решает и в живом боте
(bot_rsi.entry_allowed), поэтому тест и реал не расходятся. storm=None
(умолчание) — прежнее поведение бит в бит. Замер эффекта на 5 финальных
ботах: storm_bots_report.py (вывод — storm_bots_report_out.txt).

Запуск: python evolution2.py
"""

import itertools
import json
import random
import statistics
import time

import backtest_rsi_grid as bg
import evolution as ev  # fetch, rolling_extremes, calc_atr_pct, percentile
import storm_filter as sf  # штормовой фильтр (общий с живым ботом)

random.seed(43)

SYMBOLS = ["DOGEUSDT", "LTCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
TAKER, MAKER = 0.00055, 0.0002
SLIP = 0.0003          # проскальзывание маркет-ордеров
FUND_8H = 0.0001       # funding 0.01% за 8ч (плата за удержание, консервативно)
BARS_8H = 32           # 8ч в 15m-свечах
START, MARGIN = 20.0, 5.0
LEV = 5
MM = 0.95
MONTH_MS = 30 * 86400 * 1000
RSI_SET = [7, 10, 14, 21]

POP, GENS, ELITE = 48, 24, 6
DAYS = 730

GENES = {
    "rsi_idx":  (0, 3, True),        # индекс в RSI_SET
    "rsi_os":   (15, 40, True),
    "zone_l":   (0.10, 0.55, False),
    "zone_s":   (0.10, 0.55, False),
    "window":   (150, 900, True),
    "step":     (0.004, 0.03, False),
    "levels":   (2, 4, True),
    "mult":     (1.0, 2.0, False),
    "tp":       (0.008, 0.05, False),
    "sweep":    (0.005, 0.035, False),
    "max_bars": (24, 288, True),
    "cooldown": (0, 48, True),
    "knife":    (0.0, 3.5, False),
    "be_move":  (0, 1, True),        # 1 = стоп в безубыток на полпути к TP
}


# Баров в сутках для текущего таймфрейма движка (15m -> 96). Временно
# переключай перед прогоном на другом интервале (напр. 4ч -> 6), как LEV:
#   old = BARS_PER_DAY; BARS_PER_DAY = 6
#   try: ...
#   finally: BARS_PER_DAY = old
# Иначе ATR "суток" и штраф за долгое удержание в fitness() посчитаются по
# 15-минутному смыслу "96 баров" даже на свечах другой длины — это баг,
# который был бы легко пропустить.
BARS_PER_DAY = 96


def prep(candles):
    closes = [c[4] for c in candles]
    return dict(closes=closes,
                rsi={p: bg.calc_rsi(closes, p) for p in RSI_SET},
                atr=ev.calc_atr_pct(candles, n=BARS_PER_DAY))


def run5(candles, pre, g, entry_filter=None, events=None, storm=None):
    """entry_filter(side, i) -> side|None — внешний фильтр входов (F&G, BTC...).
    events: если передан список — в него пишутся сделки (вход/сетка/выход).
    storm: ряды штормового фильтра (storm_filter.build(...) на ТЕХ ЖЕ свечах)
        или None — фильтр выключен. ПО УМОЛЧАНИЮ ВЫКЛЮЧЕН: все прежние
        результаты воспроизводятся бит в бит. Блокируется только открытие
        НОВОГО цикла; уже открытая позиция (сетка, стоп, тейк, таймаут)
        ведётся как обычно — фильтр стоит ниже блока сопровождения позиции.
        Решение принимает storm_filter.blocked_at — та же функция, что
        вызывает живой бот, поэтому тест и реал не разойдутся."""
    closes, atr = pre["closes"], pre["atr"]
    rsi = pre["rsi"][RSI_SET[g["rsi_idx"]]]
    rlow, rhigh = ev.rolling_extremes(candles, g["window"])
    rsi_os, rsi_ob = g["rsi_os"], 100 - g["rsi_os"]
    balance, peak, max_dd = START, START, 0.0
    trades = wins = 0
    monthly, hold_bars = {}, []
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

    def close_pos(p, exit_price, ts, i, taker_exit, liq=False, reason="?"):
        sgn = 1 if p["side"] == "L" else -1
        q = sum(f[1] for f in p["fills"])
        avg = sum(fp * fq for fp, fq in p["fills"]) / q
        if liq:
            mused = sum(m_k[k] for k in range(len(p["fills"])))
            pnl = -mused * MM - p["fees"]
        else:
            px = exit_price * (1 - sgn * SLIP) if taker_exit else exit_price
            fee = q * px * (TAKER if taker_exit else MAKER)
            pnl = sgn * (px - avg) * q - fee - p["fees"]
        book(pnl, ts, p["opened_i"], i)
        if events is not None:
            events.append(dict(t=ts, type="close", price=exit_price,
                               pnl=round(pnl, 4), liq=liq, reason=reason))

    start_i = max(g["window"], max(RSI_SET) + 1, 98)
    for i in range(start_i, len(candles)):
        ts, o, h, l, c = candles[i]
        if pos:
            sgn = 1 if pos["side"] == "L" else -1
            q_pre = sum(f[1] for f in pos["fills"])
            avg_pre = sum(fp * fq for fp, fq in pos["fills"]) / q_pre
            # funding за свечу удержания
            pos["fees"] += q_pre * c * FUND_8H / BARS_8H
            stop = pos["stop"]
            tp_pre = avg_pre * (1 + sgn * g["tp"])

            adverse = (l <= stop) if sgn == 1 else (h >= stop)
            # тейк проверяем по средней ДО новых доливок этой свечи (консервативно)
            hit_tp = (h >= tp_pre) if sgn == 1 else (l <= tp_pre)

            if adverse:
                # по пути к стопу исполняются лимитки сетки выше стопа
                while pos["adds"]:
                    ap, aq = pos["adds"][0]
                    reachable = (l <= ap) if sgn == 1 else (h >= ap)
                    above_stop = (ap > stop) if sgn == 1 else (ap < stop)
                    if reachable and above_stop:
                        pos["fees"] += aq * ap * MAKER
                        pos["fills"].append((ap, aq))
                        pos["adds"].pop(0)
                        if events is not None:
                            events.append(dict(t=ts, type="add", price=ap))
                    else:
                        break
                q = sum(f[1] for f in pos["fills"])
                avg = sum(fp * fq for fp, fq in pos["fills"]) / q
                mused = sum(m_k[k] for k in range(len(pos["fills"])))
                p_liq = avg - sgn * (mused * MM) / q
                liq_first = (p_liq >= stop) if sgn == 1 else (p_liq <= stop)
                liq_hit = (l <= p_liq) if sgn == 1 else (h >= p_liq)
                if liq_first and liq_hit:
                    close_pos(pos, p_liq, ts, i, taker_exit=True, liq=True, reason="liq")
                else:
                    close_pos(pos, stop, ts, i, taker_exit=True, reason="stop")
                pos = None
                cooldown_until = i + g["cooldown"]
            elif hit_tp:
                close_pos(pos, tp_pre, ts, i, taker_exit=False, reason="tp")
                pos = None
            else:
                # обычные доливки (без стопа в этой свече)
                while pos["adds"]:
                    ap, aq = pos["adds"][0]
                    if (l <= ap) if sgn == 1 else (h >= ap):
                        pos["fees"] += aq * ap * MAKER
                        pos["fills"].append((ap, aq))
                        pos["adds"].pop(0)
                        if events is not None:
                            events.append(dict(t=ts, type="add", price=ap))
                    else:
                        break
                # перенос стопа в безубыток на полпути к тейку
                if g["be_move"] and not pos["be_done"]:
                    q = sum(f[1] for f in pos["fills"])
                    avg = sum(fp * fq for fp, fq in pos["fills"]) / q
                    trig = avg * (1 + sgn * g["tp"] * 0.5)
                    if (h >= trig) if sgn == 1 else (l <= trig):
                        be = avg * (1 + sgn * 0.0015)  # безубыток + комиссии
                        better = (be > pos["stop"]) if sgn == 1 else (be < pos["stop"])
                        if better:
                            pos["stop"] = be
                        pos["be_done"] = True
                # таймаут
                if pos and i - pos["opened_i"] > g["max_bars"]:
                    r = rsi[i]
                    if r is not None and (r >= 50 if sgn == 1 else r <= 50):
                        close_pos(pos, c, ts, i, taker_exit=True, reason="timeout")
                        pos = None
            if balance < MARGIN:
                return dict(balance=balance, trades=trades, wins=wins,
                            max_dd=max_dd, ruined=True, monthly=monthly,
                            hold=hold_bars,
                            months=(candles[-1][0] - t0) / MONTH_MS)
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
        if r_prev >= rsi_os and r_now < rsi_os and zpos < g["zone_l"]:
            side = "L"
        elif r_prev <= rsi_ob and r_now > rsi_ob and zpos > 1 - g["zone_s"]:
            side = "S"
        if side and g["knife"] > 0.05 and atr[i]:
            move = (closes[i - 8] - c) / c
            if side == "L" and move > g["knife"] * atr[i]:
                side = None
            elif side == "S" and -move > g["knife"] * atr[i]:
                side = None
        # шторм: рынок в экстремальном движении — новых циклов не открываем
        if side and storm is not None and sf.blocked_at(side, storm, i):
            side = None
        if side and entry_filter:
            side = entry_filter(side, i)
        if not side:
            continue
        sgn = 1 if side == "L" else -1
        px = c * (1 + sgn * SLIP)
        q0 = m_k[0] * LEV / px
        stop = (rlow[i] * (1 - g["sweep"]) if side == "L"
                else rhigh[i] * (1 + g["sweep"]))
        adds = []
        ap = px
        for k in range(1, g["levels"]):
            ap = ap * (1 - sgn * g["step"])
            adds.append((ap, m_k[k] * LEV / ap))
        pos = dict(side=side, fills=[(px, q0)], stop=stop, adds=adds,
                   opened_i=i, fees=q0 * px * TAKER, be_done=False)
        if events is not None:
            events.append(dict(t=ts, type="entry", side=side, price=px))

    if pos:
        close_pos(pos, closes[-1], candles[-1][0], len(candles) - 1,
                  taker_exit=True)
    return dict(balance=balance, trades=trades, wins=wins, max_dd=max_dd,
                ruined=False, monthly=monthly, hold=hold_bars,
                months=(candles[-1][0] - t0) / MONTH_MS)


def stats(r):
    n_months = max(1, int(r["months"]))
    rets = [(r["monthly"].get(m, 0.0) / START) * 100 for m in range(n_months)]
    med = statistics.median(rets) if rets else 0.0
    p25 = ev.percentile(rets, 0.25)
    pos_share = sum(1 for x in rets if x > 0) / len(rets) if rets else 0
    tpm = r["trades"] / n_months
    avg_hold = statistics.mean(r["hold"]) if r["hold"] else 999
    return dict(med=med, p25=p25, pos_share=pos_share, tpm=tpm,
                avg_hold=avg_hold)


def fitness(r):
    st = stats(r)
    f = (st["p25"] + 0.5 * st["med"])
    f *= min(1.0, st["tpm"] / 6.0)
    f *= BARS_PER_DAY / (BARS_PER_DAY + st["avg_hold"])
    if r["ruined"]:
        f -= 50
    return f


def oos_score(r):
    st = stats(r)
    return st["p25"] + 0.5 * st["med"] - (100 if r["ruined"] else 0)


# --- генетика (гены v2) ---

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


def evolve(candles, pre, seeds, prefix):
    cache = {}

    def eval_g(g):
        key = tuple(round(g[k], 4) if not GENES[k][2] else g[k] for k in GENES)
        if key not in cache:
            cache[key] = fitness(run5(candles, pre, g))
        return cache[key]

    pop = [clamp(dict(s)) for s in seeds]
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
        if (gen + 1) % 4 == 0:
            print(f"  {prefix} поколение {gen+1:2}: best {scored[0][0]:+.2f}")
    return scored


# --- текущие конфиги в генах v2 (baseline) ---

BASELINE = {
    "DOGEUSDT": dict(rsi_idx=2, rsi_os=35, zone_l=0.28, zone_s=0.28, window=518,
                     step=0.011, levels=3, mult=1.8, tp=0.038, sweep=0.020,
                     max_bars=205, cooldown=0, knife=0.0, be_move=0),
    "LTCUSDT": dict(rsi_idx=2, rsi_os=34, zone_l=0.25, zone_s=0.25, window=377,
                    step=0.008, levels=3, mult=1.2, tp=0.020, sweep=0.020,
                    max_bars=160, cooldown=0, knife=0.0, be_move=0),
    "BTCUSDT": dict(rsi_idx=2, rsi_os=25, zone_l=0.25, zone_s=0.25, window=400,
                    step=0.015, levels=3, mult=1.5, tp=0.020, sweep=0.020,
                    max_bars=192, cooldown=0, knife=0.0, be_move=0),
    "ETHUSDT": dict(rsi_idx=2, rsi_os=25, zone_l=0.49, zone_s=0.49, window=160,
                    step=0.014, levels=3, mult=1.6, tp=0.018, sweep=0.018,
                    max_bars=192, cooldown=0, knife=0.0, be_move=0),
    "SOLUSDT": dict(rsi_idx=2, rsi_os=30, zone_l=0.25, zone_s=0.25, window=400,
                    step=0.010, levels=3, mult=1.5, tp=0.020, sweep=0.020,
                    max_bars=192, cooldown=0, knife=0.0, be_move=0),
}


def self_test():
    """Инвариант: сумма помесячных PnL == изменение баланса."""
    candles = ev.fetch("DOGEUSDT", "15", 365)
    pre = prep(candles)
    for _ in range(3):
        g = rand_genome()
        r = run5(candles, pre, g)
        diff = abs((START + sum(r["monthly"].values())) - r["balance"])
        assert diff < 1e-6, f"инвариант нарушен: {diff}"
    print("self-test OK: баланс сходится с помесячным PnL")


def fold_bounds(n):
    """3 фолда walk-forward для 24 месяцев."""
    return [(0, int(n * 12 / 24), int(n * 16 / 24)),
            (0, int(n * 16 / 24), int(n * 20 / 24)),
            (0, int(n * 20 / 24), n)]


def main():
    self_test()
    results = {}
    for sym in SYMBOLS:
        print(f"\n================ {sym} ================")
        candles = ev.fetch(sym, "15", DAYS)
        n = len(candles)
        print(f"Свечей: {n} ({DAYS} дней)")
        folds = fold_bounds(n)

        # OOS-сегменты и их prep (общие для baseline и кандидатов)
        oos_segs = []
        for (a, b, e) in folds:
            seg = candles[b:e]
            oos_segs.append((seg, prep(seg)))

        def agg_oos(g):
            scores = []
            for seg, pre_seg in oos_segs:
                scores.append(oos_score(run5(seg, pre_seg, g)))
            return sum(scores) / len(scores), scores

        base_mean, base_scores = agg_oos(BASELINE[sym])
        print(f"BASELINE (текущий конфиг, честный движок): "
              f"средний OOS {base_mean:+.2f} | по фолдам "
              f"{['%+.2f' % s for s in base_scores]}")

        # эволюция на каждом фолде
        candidates = []
        for fi, (a, b, e) in enumerate(folds):
            train = candles[a:b]
            pre_tr = prep(train)
            scored = evolve(train, pre_tr, [BASELINE[sym]], f"{sym[:3]}-f{fi+1}")
            seen = set()
            for f, g in scored:
                key = tuple(g[k] for k in GENES)
                if key not in seen:
                    seen.add(key)
                    candidates.append(g)
                if len(seen) == 3:
                    break

        best = None
        for g in candidates:
            mean, scores = agg_oos(g)
            if best is None or mean > best[0]:
                best = (mean, scores, g)
        mean, scores, g_win = best
        print(f"ЛУЧШИЙ КАНДИДАТ: средний OOS {mean:+.2f} | по фолдам "
              f"{['%+.2f' % s for s in scores]}")
        print(f"  гены: {g_win}")

        # финальная сводка на последнем годе честным движком
        last_year = candles[-min(35040, n):]
        pre_y = prep(last_year)
        for label, g in (("baseline", BASELINE[sym]), ("candidate", g_win)):
            r = run5(last_year, pre_y, g)
            st = stats(r)
            ret = (r["balance"] / START - 1) * 100
            wr = r["wins"] / r["trades"] * 100 if r["trades"] else 0
            print(f"  {label:9} год(честн.): {ret:+8.1f}% | мес.мед {st['med']:+5.2f}% "
                  f"| P25 {st['p25']:+5.2f}% | WR {wr:4.1f}% | "
                  f"DD {r['max_dd']*100:4.1f}% | сделок {r['trades']}"
                  f"{' СЛИВ' if r['ruined'] else ''}")

        results[sym] = dict(
            baseline_oos=base_mean, candidate_oos=mean,
            adopt=bool(mean > base_mean), genome=g_win)

    with open("evolution2_winners.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2, default=float)
    print("\nИтоги в evolution2_winners.json")


if __name__ == "__main__":
    main()
