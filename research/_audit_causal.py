# -*- coding: utf-8 -*-
"""Аудит: assert_causal по всем стратегиям x монеты x тф x seed."""
import sys
import traceback

import numpy as np

import engine
import rdata
import universe

REG, _ = universe.load()

SYMS = ["BTCUSDT", "SOLUSDT"]
TFS = ["60", "240"]
SEEDS = [0, 1, 2]


def pick(strat, k=4):
    combos = list(strat.combos())
    if len(combos) <= k:
        return combos
    idx = np.linspace(0, len(combos) - 1, k).astype(int)
    return [combos[i] for i in idx]


def main():
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    fails = {}
    errs = {}
    for name in sorted(REG):
        if only and name not in only:
            continue
        st = REG[name]
        for p in pick(st, 4):
            for sym in SYMS:
                for tf in TFS:
                    bars = rdata.load_bars(sym, tf)
                    for seed in SEEDS:
                        try:
                            engine.assert_causal(
                                lambda b, _p=p, _s=st: _s.build(b, _p),
                                bars, seed=seed)
                        except AssertionError as e:
                            key = name
                            fails.setdefault(key, []).append(
                                (p, sym, tf, seed, str(e)[:220]))
                        except Exception as e:  # noqa: BLE001
                            errs.setdefault(name, []).append(
                                (p, sym, tf, seed,
                                 type(e).__name__ + ": " + str(e)[:200]))
    print("=== УТЕЧКА БУДУЩЕГО ===")
    for name in sorted(fails):
        lst = fails[name]
        print("\n%s : %d падений" % (name, len(lst)))
        for p, sym, tf, seed, msg in lst[:3]:
            print("   %s %s tf=%s seed=%d\n     %s" % (p, sym, tf, seed, msg))
    if not fails:
        print("(нет)")
    print("\n=== ИСКЛЮЧЕНИЯ ===")
    for name in sorted(errs):
        lst = errs[name]
        print("\n%s : %d" % (name, len(lst)))
        for p, sym, tf, seed, msg in lst[:3]:
            print("   %s %s tf=%s seed=%d -> %s" % (p, sym, tf, seed, msg))
    if not errs:
        print("(нет)")


if __name__ == "__main__":
    main()
