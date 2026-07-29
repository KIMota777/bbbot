# -*- coding: utf-8 -*-
"""Эволюция v4: биржевые прокси китов (funding, OI) + фонда/золото.
Данные: 1150 дней (~3.2 года) 15m. Walk-forward: 3 экзамена
(train 18м -> OOS 6м; 24 -> 6; 30 -> 8).

Новые гены (все с состоянием "выкл"):
  fund_long_max  (-0.05..0.06): лонг только при funding <= порога
                 (отрицательный funding = шорты платят = сигнал дна). 0.06=выкл.
  fund_short_min (-0.06..0.05): шорт только при funding >= порога. -0.06=выкл.
  oi_gate        (0..2): 1 = лонг при падающем OI за 24ч / шорт при растущем
                 (делевередж); 2 = наоборот; 0 = выкл.
  spx_long_min   (-6..0): лонг запрещён, если S&P за 5д упал ниже порога. -6=выкл.
  dxy_long_max   (0..4):  лонг запрещён, если DXY за 5д вырос выше порога. 4=выкл.
  gold_long_max  (0..6):  лонг запрещён, если золото за 5д выросло выше порога
                 (бегство в защиту). 6=выкл.
"""

import json
import random
import time

import evolution as ev
import evolution2 as e2
import ext_data as xd

random.seed(45)

SYMBOLS = ["DOGEUSDT", "LTCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
POP, GENS, ELITE = 40, 16, 5
DAYS = 1150

GENES4 = dict(e2.GENES)
GENES4.update({
    "fund_long_max":  (-0.05, 0.06, False),
    "fund_short_min": (-0.06, 0.05, False),
    "oi_gate":        (0, 2, True),
    "spx_long_min":   (-6.0, 0.0, False),
    "dxy_long_max":   (0.0, 4.0, False),
    "gold_long_max":  (0.0, 6.0, False),
})
OFF4 = dict(fund_long_max=0.06, fund_short_min=-0.06, oi_gate=0,
            spx_long_min=-6.0, dxy_long_max=4.0, gold_long_max=6.0)

# действующие конфиги (после v2/v3) в генах движка
CURRENT = {
    "DOGEUSDT": dict(rsi_idx=2, rsi_os=35, zone_l=0.28, zone_s=0.28, window=518,
                     step=0.011, levels=3, mult=1.8, tp=0.038, sweep=0.020,
                     max_bars=205, cooldown=0, knife=0.0, be_move=0),
    "LTCUSDT": dict(rsi_idx=2, rsi_os=34, zone_l=0.25, zone_s=0.25, window=377,
                    step=0.008, levels=3, mult=1.2, tp=0.020, sweep=0.020,
                    max_bars=160, cooldown=0, knife=0.0, be_move=0),
    "BTCUSDT": dict(rsi_idx=2, rsi_os=23, zone_l=0.40, zone_s=0.18, window=877,
                    step=0.004, levels=2, mult=2.0, tp=0.021, sweep=0.020,
                    max_bars=243, cooldown=30, knife=3.5, be_move=0),
    "ETHUSDT": dict(rsi_idx=2, rsi_os=25, zone_l=0.50, zone_s=0.41, window=724,
                    step=0.007, levels=3, mult=1.6, tp=0.010, sweep=0.025,
                    max_bars=139, cooldown=48, knife=3.5, be_move=0),
    "SOLUSDT": dict(rsi_idx=2, rsi_os=30, zone_l=0.25, zone_s=0.25, window=400,
                    step=0.010, levels=3, mult=1.5, tp=0.020, sweep=0.020,
                    max_bars=192, cooldown=0, knife=0.0, be_move=0),
}


def make_filter4(g, aux):
    fund, oi_chg = aux["fund"], aux["oi_chg"]
    spx5, dxy5, gold5 = aux["spx5"], aux["dxy5"], aux["gold5"]

    def f(side, i):
        v = fund[i]
        if v is not None:
            if side == "L" and v > g["fund_long_max"]:
                return None
            if side == "S" and v < g["fund_short_min"]:
                return None
        o = oi_chg[i]
        if o is not None and g["oi_gate"]:
            fall = o < 0
            ok_long = fall if g["oi_gate"] == 1 else not fall
            if side == "L" and not ok_long:
                return None
            if side == "S" and ok_long:
                return None
        if side == "L":
            s = spx5[i]
            if s is not None and s < g["spx_long_min"]:
                return None
            d = dxy5[i]
            if d is not None and d > g["dxy_long_max"]:
                return None
            au = gold5[i]
            if au is not None and au > g["gold_long_max"]:
                return None
        return side

    return f


def ga_tools(genes):
    def rand_g():
        return {k: (int(round(random.uniform(lo, hi))) if ii
                    else random.uniform(lo, hi))
                for k, (lo, hi, ii) in genes.items()}

    def clamp(g):
        return {k: (int(round(min(hi, max(lo, g[k])))) if ii
                    else min(hi, max(lo, g[k])))
                for k, (lo, hi, ii) in genes.items()}

    def mutate(g):
        out = dict(g)
        for k, (lo, hi, ii) in genes.items():
            if random.random() < 0.25:
                out[k] = out[k] + random.gauss(0, 0.15 * (hi - lo))
        return clamp(out)

    def cross(a, b):
        return clamp({k: (a[k] if random.random() < 0.5 else b[k])
                      for k in genes})
    return rand_g, clamp, mutate, cross


def evolve_ext(candles, pre, aux, seeds, genes, make_f, prefix):
    rand_g, clamp, mutate, cross = ga_tools(genes)
    cache = {}

    def eval_g(g):
        key = tuple(round(g[k], 4) if not genes[k][2] else g[k] for k in genes)
        if key not in cache:
            r = e2.run5(candles, pre, g, entry_filter=make_f(g, aux))
            cache[key] = e2.fitness(r)
        return cache[key]

    pop = [clamp(dict(s)) for s in seeds]
    while len(pop) < POP:
        pop.append(rand_g())
    scored = sorted(((eval_g(g), g) for g in pop), key=lambda x: -x[0])
    for gen in range(GENS):
        new = [g for _, g in scored[:ELITE]]
        while len(new) < POP:
            if random.random() < 0.3:
                new.append(mutate(scored[random.randrange(ELITE)][1]))
            else:
                a = max(random.sample(scored, 3), key=lambda x: x[0])[1]
                b = max(random.sample(scored, 3), key=lambda x: x[0])[1]
                new.append(mutate(cross(a, b)))
        scored = sorted(((eval_g(g), g) for g in new), key=lambda x: -x[0])
        if (gen + 1) % 4 == 0:
            print(f"  {prefix} поколение {gen+1:2}: best {scored[0][0]:+.2f}")
    return scored


def fold_bounds_3y(n):
    """train 18м/24м/30м -> OOS 6/6/8м при ~38.4м данных."""
    total_m = 38.4
    return [(0, int(n * 18 / total_m), int(n * 24 / total_m)),
            (0, int(n * 24 / total_m), int(n * 30 / total_m)),
            (0, int(n * 30 / total_m), n)]


def run_version(genes, off, make_f, aux_builder, tag, base_src,
                interval="15", symbols=None, days=None):
    """interval/days: таймфрейм и глубина истории (по умолчанию 15m/3.2г —
    не менять существующим вызовам). e2.BARS_PER_DAY переключается на время
    прогона под фактический таймфрейм и восстанавливается в finally, чтобы
    ATR "суток" и штраф за долгое удержание в fitness() считались верно."""
    results = {}
    bars_per_day = max(4, 1440 // int(interval))
    old_bpd = e2.BARS_PER_DAY
    e2.BARS_PER_DAY = bars_per_day
    try:
        results = _run_version_body(genes, off, make_f, aux_builder, tag,
                                    base_src, interval, symbols or SYMBOLS,
                                    days or DAYS)
    finally:
        e2.BARS_PER_DAY = old_bpd
    return results


def _run_version_body(genes, off, make_f, aux_builder, tag, base_src,
                      interval, symbols, days):
    results = {}
    for sym in symbols:
        print(f"\n================ {sym} ({tag}, {interval}m) ================")
        candles = ev.fetch(sym, interval, days)
        n = len(candles)
        print(f"Свечей: {n}")
        folds = fold_bounds_3y(n)
        aux_full = aux_builder(sym, candles)

        def _slice(v, b_, e_):
            if isinstance(v, tuple):
                return tuple(_slice(x, b_, e_) for x in v)
            if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
                return [_slice(x, b_, e_) for x in v]
            return v[b_:e_]

        def slice_aux(a, b_, e_):
            return {k: _slice(v, b_, e_) for k, v in aux_full.items()}

        oos = []
        for (a, b, e) in folds:
            seg = candles[b:e]
            oos.append((seg, e2.prep(seg), slice_aux(aux_full, b, e)))

        base = dict(base_src[sym])
        for k, v in off.items():
            base.setdefault(k, v)
        _, clamp, _, _ = ga_tools(genes)
        base = clamp(base)

        def agg(g):
            sc = []
            for seg, pre_seg, aux_seg in oos:
                r = e2.run5(seg, pre_seg, g, entry_filter=make_f(g, aux_seg))
                sc.append(e2.oos_score(r))
            return sum(sc) / len(sc), sc

        base_mean, base_sc = agg(base)
        print(f"БАЗА: средний OOS {base_mean:+.2f} | {['%+.2f' % s for s in base_sc]}")

        cand = []
        for fi, (a, b, e) in enumerate(folds):
            train = candles[a:b]
            scored = evolve_ext(train, e2.prep(train), slice_aux(aux_full, a, b),
                                [base], genes, make_f, f"{sym[:3]}-f{fi+1}")
            seen = set()
            for f_, g in scored:
                key = tuple(g[k] for k in genes)
                if key not in seen:
                    seen.add(key)
                    cand.append(g)
                if len(seen) == 3:
                    break

        best = None
        for g in cand:
            m, sc = agg(g)
            if best is None or m > best[0]:
                best = (m, sc, g)
        m, sc, g_win = best
        adopt = m > base_mean + 0.5  # осмысленный отрыв, не шум
        ext_used = {k: g_win[k] for k in off}
        print(f"ЛУЧШИЙ {tag}: средний OOS {m:+.2f} | {['%+.2f' % s for s in sc]} "
              f"| принят: {'ДА' if adopt else 'нет'}")
        print(f"  внешние гены: {ext_used}")
        results[sym] = dict(base_oos=base_mean, cand_oos=m, adopt=bool(adopt),
                            genome=g_win, base_genome=base)
    with open(f"{tag}_winners.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2, default=float)
    print(f"\nИтоги в {tag}_winners.json")
    return results


def main():
    pct5 = xd.fetch_daily_pct5()
    print("SPX/DXY/gold: дневные ряды загружены")

    def aux_builder(sym, candles):
        funding = xd.fetch_funding(sym, DAYS + 50)
        oi = xd.fetch_oi(sym)
        cov_f = sum(1 for _ in funding)
        print(f"  {sym}: funding {cov_f} точек, OI {len(oi)} часов")
        return xd.build_aux4(candles, funding, oi, pct5)

    run_version(GENES4, OFF4, make_filter4, aux_builder, "evolution4", CURRENT)


if __name__ == "__main__":
    main()
