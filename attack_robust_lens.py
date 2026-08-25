# -*- coding: utf-8 -*-
"""ЛИНЗА: мера устойчивости в all_configs_honest.py.

Проверяем три вещи разом:
  A. дрожание медианы 40 соседей от зерна (10 зёрен);
  B. ЗАМОРОЖЕННЫЙ ФИЛЬТР: скрипт строит filt из БАЗОВОГО генома один раз в
     halves() и прогоняет им ВСЕХ соседей. Считаем ту же устойчивость с
     фильтром, пересобранным под каждого соседа (как делает сам bots_honest);
  C. сила возмущения k = 0.05 / 0.10 / 0.20.
"""
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

import all_configs_honest as ach
import bots_honest as bh
import evolution as ev
import evolution2 as e2
import evolution8 as e8
import ext_data as xd

LEV = 5.0
NP = 40
SEEDS = list(range(10))
_CACHE = {}


def build(sym, g):
    """Половины БЕЗ предсобранного фильтра + сырой aux, чтобы пересобирать."""
    key = sym
    if key not in _CACHE:
        pct5 = xd.fetch_daily_pct5()
        candles = ev.fetch(sym, "15", bh.DAYS)
        aux = e8.make_aux_builder(pct5, 96)(sym, candles)
        n = len(candles)
        h = int(n * bh.HOLD_FRAC)
        parts = {}
        for name, a, b in (("train", 0, h), ("hold", h, n)):
            c = candles[a:b]
            parts[name] = dict(
                candles=c, pre=e2.prep(c),
                months=(c[-1][0] - c[0][0]) / (30 * 86400000),
                aux=dict((k, bh.slice_aux(v, a, b)) for k, v in aux.items()))
        _CACHE[key] = parts
    return _CACHE[key]


def comp_of(part, g, filt):
    evs = []
    bh.run_at(part["candles"], part["pre"], g, filt, LEV, events=evs)
    curve = [(e["t"], 0.0 if bh.is_tiny(e["pnl"]) else e["pnl"])
             for e in evs if e["type"] == "close" and e["pnl"] is not None]
    c, _ = bh.portfolio({"one": curve}, 1)
    return float(c)


def job(spec):
    tgt, seed, k = spec
    parts = build(tgt["sym"], tgt["g"])
    g0 = tgt["g"]
    rng = np.random.default_rng(seed)
    neigh = [bh.perturb(g0, e8.GENES8, rng, k) for _ in range(NP)]
    out = dict(tag=tgt["tag"], seed=seed, k=k)
    for name in ("train", "hold"):
        p = parts[name]
        f0 = e8.make_filter8(g0, p["aux"])          # фильтр базового генома
        out[name + "_base"] = comp_of(p, g0, f0)
        froz, live = [], []
        for ng in neigh:
            try:
                froz.append(comp_of(p, ng, f0))      # как в скрипте
            except Exception:                        # noqa: BLE001
                pass
            try:
                live.append(comp_of(p, ng, e8.make_filter8(ng, p["aux"])))
            except Exception:                        # noqa: BLE001
                pass
        out[name + "_froz"] = froz
        out[name + "_live"] = live
    # нулевое возмущение: только clamp, без сдвига
    rz = np.random.default_rng(1)
    gz = bh.perturb(g0, e8.GENES8, rz, 0.0)
    for name in ("train", "hold"):
        p = parts[name]
        out[name + "_clamp0"] = comp_of(p, gz, e8.make_filter8(gz, p["aux"]))
    return out


WANT = [("BTCUSDT", "config.py", "normal"),
        ("DOGEUSDT", "config.py", "final"),
        ("DOGEUSDT", "evolution2_winners", "волна"),
        ("LTCUSDT", "config.py", "normal"),
        ("SOLUSDT", "config.py", "bear"),
        ("ETHUSDT", "evolution8_15m_winners", "волна"),
        ("BTCUSDT", "evolution8_15m_winners", "волна"),
        ("SOLUSDT", "config.py", "final")]


def main():
    recs = ach.collect()
    tgts = []
    for sym, src, tag in WANT:
        for r in recs:
            if r["sym"] == sym and r["src"] == src and r["tag"] == tag:
                tgts.append(dict(sym=sym, g=r["g"],
                                 tag="%s %s/%s" % (sym.replace("USDT", ""),
                                                   src, tag)))
                break
    print("целей: %d" % len(tgts))
    specs = []
    for t in tgts:
        for s in SEEDS:
            specs.append((t, s, 0.10))
        specs.append((t, 7, 0.05))
        specs.append((t, 7, 0.20))
    specs.sort(key=lambda s: s[0]["sym"])
    print("заданий: %d" % len(specs))
    sys.stdout.flush()
    t0 = time.time()
    res = []
    with Pool(8) as pool:
        for i, r in enumerate(pool.imap_unordered(job, specs, chunksize=1), 1):
            res.append(r)
            if i % 10 == 0:
                print("  %d/%d  %.0fs" % (i, len(specs), time.time() - t0))
                sys.stdout.flush()
    json.dump(res, open("attack_robust_lens.json", "w", encoding="utf-8"),
              ensure_ascii=False)
    print("-> attack_robust_lens.json  %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
