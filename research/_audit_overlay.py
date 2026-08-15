# -*- coding: utf-8 -*-
"""Аудит накладок: assert_causal + assert_causal_external, 2 монеты, 2 тф, 3 seed."""
import numpy as np

import engine
import overlay
import rdata
import universe

REG, _ = universe.load()

SYMS = ["BTCUSDT", "SOLUSDT"]
TFS = ["60", "240"]
SEEDS = [0, 1, 2]
BASE_P = dict(n=55, exit_n=20, stop_atr=3.0, atr_n=14, trail_atr=0.0)
BASE = REG["donchian"]
EXT = ["htf_trend", "btc_regime", "btc_vol_size", "funding_filter", "oi_filter"]


def pick(name, k=6):
    cs = list(overlay.combos(name))
    if len(cs) <= k:
        return cs
    idx = np.linspace(0, len(cs) - 1, k).astype(int)
    return [cs[i] for i in idx]


def warm():
    for s in SYMS:
        for tf in ("60", "240"):
            rdata.load_bars(s, tf)
        try:
            rdata.load_funding(s)
            rdata.load_oi(s)
        except Exception as e:  # noqa: BLE001
            print("нет внешнего ряда %s: %s" % (s, e))


def main():
    warm()
    print("=== 1. assert_causal (цены своего ряда) ===")
    for name in sorted(overlay.OVERLAYS):
        errs = []
        for p in pick(name):
            for sym in SYMS:
                for tf in TFS:
                    bars = rdata.load_bars(sym, tf)

                    def build(b, _p=p, _n=name):
                        return overlay.apply_overlay(
                            b, BASE.build(b, BASE_P), _n, _p)
                    for seed in SEEDS:
                        try:
                            engine.assert_causal(build, bars, seed=seed)
                        except AssertionError as e:
                            errs.append((p, sym, tf, seed, str(e)[:200]))
                        except Exception as e:  # noqa: BLE001
                            errs.append((p, sym, tf, seed,
                                         "ИСКЛ " + type(e).__name__ + ": "
                                         + str(e)[:160]))
        print("%s %-16s падений=%d" % ("!" if errs else "+", name, len(errs)))
        for r in errs[:3]:
            print("     %s %s tf=%s seed=%d\n       %s" % r)

    print("\n=== 2. assert_causal_external (чужие файлы) ===")
    for name in EXT:
        errs = []
        for p in pick(name):
            for sym in SYMS:
                for tf in TFS:
                    bars = rdata.load_bars(sym, tf)

                    def build(b, _p=p, _n=name):
                        return overlay.apply_overlay(
                            b, BASE.build(b, BASE_P), _n, _p)
                    for seed in SEEDS:
                        try:
                            overlay.assert_causal_external(build, bars,
                                                           seed=seed)
                        except AssertionError as e:
                            errs.append((p, sym, tf, seed, str(e)[:200]))
                        except Exception as e:  # noqa: BLE001
                            errs.append((p, sym, tf, seed,
                                         "ИСКЛ " + type(e).__name__ + ": "
                                         + str(e)[:160]))
        print("%s %-16s падений=%d" % ("!" if errs else "+", name, len(errs)))
        for r in errs[:3]:
            print("     %s %s tf=%s seed=%d\n       %s" % r)


if __name__ == "__main__":
    main()
