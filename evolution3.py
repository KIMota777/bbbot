# -*- coding: utf-8 -*-
"""Эволюция v3: проверка внешних сигналов как генов.

Новые гены поверх честного движка v2:
  - fg_long_max  (40..100): лонг разрешён только при Fear&Greed <= порога
                            (100 = фильтр выключен). Идея: покупать страх.
  - fg_short_min (0..60):   шорт разрешён только при F&G >= порога
                            (0 = выключен). Идея: продавать жадность.
  - btc_knife    (0..3.5):  запрет лонга альта, если BTC за 8 свечей упал
                            сильнее k x ATR(BTC); зеркально для шорта.
                            (0 = выключен). Идея: альты ходят за битком.
  - btc_trend    (0..2):    0 = выкл; 1 = лонг только когда BTC выше EMA400
                            (по тренду BTC); 2 = наоборот (контртренд).

Эволюция сама решает, полезен ли сигнал: "выключено" — тоже точка в
пространстве генов. Если фильтр не добавляет денег, победитель придёт
с выключенным геном — это и есть ответ.

База сравнения: победители v2 с выключенными новыми генами.
Walk-forward тот же: 3 фолда, экзамен = средний OOS по фолдам.

Запуск: python evolution3.py  (после завершения evolution2.py)
"""

import json
import os
import random
import statistics
import time
import urllib.request

import evolution as ev
import evolution2 as e2

random.seed(44)

SYMBOLS = ["DOGEUSDT", "LTCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
POP, GENS, ELITE = 40, 16, 5
DAYS = 730

GENES3 = dict(e2.GENES)
GENES3.update({
    "fg_long_max":  (40, 100, True),
    "fg_short_min": (0, 60, True),
    "btc_knife":    (0.0, 3.5, False),
    "btc_trend":    (0, 2, True),
})


# ---------- внешние данные ----------

def fetch_fng():
    cache = "fng_history.json"
    if os.path.exists(cache):
        with open(cache) as fh:
            return {int(k): v for k, v in json.load(fh).items()}
    import requests
    last_err = None
    for attempt in range(4):
        try:
            resp = requests.get(
                "https://api.alternative.me/fng/",
                params={"limit": 800, "format": "json"},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=90)
            data = resp.json()
            break
        except Exception as e:
            last_err = e
            time.sleep(5)
    else:
        raise last_err
    fg = {int(x["timestamp"]) // 86400 * 86400: int(x["value"])
          for x in data["data"]}
    with open(cache, "w") as fh:
        json.dump(fg, fh)
    return fg


def calc_ema(closes, n=400):
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


def build_aux(candles, btc_candles, fg_map):
    """Массивы внешних сигналов, выровненные по свечам candles."""
    btc_idx = {c[0]: k for k, c in enumerate(btc_candles)}
    btc_closes = [c[4] for c in btc_candles]
    btc_atr = ev.calc_atr_pct(btc_candles)
    btc_ema = calc_ema(btc_closes)

    fg = [None] * len(candles)
    btc_mv = [None] * len(candles)   # движение BTC за 8 свечей в долях ATR BTC
    btc_ab = [None] * len(candles)   # BTC выше EMA400?
    for i, c in enumerate(candles):
        day = c[0] // 1000 // 86400 * 86400
        fg[i] = fg_map.get(day)
        k = btc_idx.get(c[0])
        if k is not None and k >= 8 and btc_atr[k]:
            btc_mv[i] = ((btc_closes[k] - btc_closes[k - 8]) / btc_closes[k]
                         / btc_atr[k])
            if btc_ema[k] is not None:
                btc_ab[i] = btc_closes[k] > btc_ema[k]
    return dict(fg=fg, btc_mv=btc_mv, btc_ab=btc_ab)


def make_filter(g, aux):
    fg, btc_mv, btc_ab = aux["fg"], aux["btc_mv"], aux["btc_ab"]

    def entry_filter(side, i):
        v = fg[i]
        if v is not None:
            if side == "L" and v > g["fg_long_max"]:
                return None
            if side == "S" and v < g["fg_short_min"]:
                return None
        mv = btc_mv[i]
        if mv is not None and g["btc_knife"] > 0.05:
            if side == "L" and mv < -g["btc_knife"]:
                return None
            if side == "S" and mv > g["btc_knife"]:
                return None
        if g["btc_trend"]:
            ab = btc_ab[i]
            if ab is not None:
                want_long = ab if g["btc_trend"] == 1 else not ab
                if side == "L" and not want_long:
                    return None
                if side == "S" and want_long:
                    return None
        return side

    return entry_filter


# ---------- GA на расширенных генах ----------

def rand_genome():
    g = {}
    for k, (lo, hi, is_int) in GENES3.items():
        v = random.uniform(lo, hi)
        g[k] = int(round(v)) if is_int else v
    return g


def clamp(g):
    out = {}
    for k, (lo, hi, is_int) in GENES3.items():
        v = min(hi, max(lo, g[k]))
        out[k] = int(round(v)) if is_int else v
    return out


def mutate(g):
    out = dict(g)
    for k, (lo, hi, is_int) in GENES3.items():
        if random.random() < 0.25:
            out[k] = out[k] + random.gauss(0, 0.15 * (hi - lo))
    return clamp(out)


def crossover(a, b):
    return clamp({k: (a[k] if random.random() < 0.5 else b[k]) for k in GENES3})


OFF = dict(fg_long_max=100, fg_short_min=0, btc_knife=0.0, btc_trend=0)


def evolve3(candles, pre, aux, seeds, prefix):
    cache = {}

    def eval_g(g):
        key = tuple(round(g[k], 4) if not GENES3[k][2] else g[k] for k in GENES3)
        if key not in cache:
            r = e2.run5(candles, pre, g, entry_filter=make_filter(g, aux))
            cache[key] = e2.fitness(r)
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


def main():
    fg_map = fetch_fng()
    print(f"Fear&Greed: {len(fg_map)} дней истории")
    btc_candles = ev.fetch("BTCUSDT", "15", DAYS)

    with open("evolution2_winners.json", encoding="utf-8") as fh:
        v2 = json.load(fh)

    results = {}
    for sym in SYMBOLS:
        print(f"\n================ {sym} ================")
        candles = ev.fetch(sym, "15", DAYS)
        n = len(candles)
        folds = e2.fold_bounds(n)

        # база = принятый конфиг: кандидат v2, если он прошёл экзамен,
        # иначе прежний (e2.BASELINE)
        gsrc = v2[sym]["genome"] if v2[sym]["adopt"] else e2.BASELINE[sym]
        base_genome = clamp(dict(gsrc, **OFF))

        oos_segs = []
        for (a, b, e) in folds:
            seg = candles[b:e]
            btc_seg = None  # aux строится по полному BTC-ряду через timestamp
            oos_segs.append((seg, e2.prep(seg),
                             build_aux(seg, btc_candles, fg_map)))

        def agg_oos(g):
            scores = []
            for seg, pre_seg, aux_seg in oos_segs:
                r = e2.run5(seg, pre_seg, g, entry_filter=make_filter(g, aux_seg))
                scores.append(e2.oos_score(r))
            return sum(scores) / len(scores), scores

        base_mean, base_scores = agg_oos(base_genome)
        print(f"БАЗА (v2-победитель, внешние фильтры ВЫКЛ): "
              f"средний OOS {base_mean:+.2f} | {['%+.2f' % s for s in base_scores]}")

        candidates = []
        for fi, (a, b, e) in enumerate(folds):
            train = candles[a:b]
            pre_tr = e2.prep(train)
            aux_tr = build_aux(train, btc_candles, fg_map)
            scored = evolve3(train, pre_tr, aux_tr, [base_genome],
                             f"{sym[:3]}-f{fi+1}")
            seen = set()
            for f, g in scored:
                key = tuple(g[k] for k in GENES3)
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
        used = {k: g_win[k] for k in OFF}
        print(f"ЛУЧШИЙ v3: средний OOS {mean:+.2f} | {['%+.2f' % s for s in scores]}")
        print(f"  внешние гены победителя: {used}")
        print(f"  (fg_long_max=100 и fg_short_min=0 и btc_knife=0 и btc_trend=0 "
              f"= фильтры выключены)")
        results[sym] = dict(base_oos=base_mean, v3_oos=mean,
                            improved=bool(mean > base_mean), genome=g_win)

    # артефакт прошлого прогона не переписывается (ev.save_artifact)
    out = ev.save_artifact("evolution3_winners.json", results)
    print(f"\nИтоги в {out}")


if __name__ == "__main__":
    main()
