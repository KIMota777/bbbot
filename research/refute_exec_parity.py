# -*- coding: utf-8 -*-
"""Проверка, что новый движок исполнения не подменил задачу.

Два требования, оба обязательные:
  1. При entry="market", delay=0 и выключенных надстройках refute_exec_engine
     обязан давать ТЕ ЖЕ сделки в те же цены, что engine.run. Иначе любое
     «улучшение» ниже — это разница движков, а не разница исполнения.
  2. Сигналы не тронуты, поэтому engine.assert_causal обязан проходить на всех
     четырёх правилах ансамбля.
"""
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import engine            # noqa: E402
import rdata             # noqa: E402
import refute_exec_engine as xe   # noqa: E402
import universe          # noqa: E402

MEMBERS = ["c_willr", "bos", "vol_spike", "supertrend"]


def main():
    reg, _ = universe.load()
    print("1) ТОЖДЕСТВО С ИСХОДНЫМ ДВИЖКОМ (market, delay=0, без надстроек)")
    bad = 0
    total = 0
    for name in MEMBERS:
        st = reg[name]
        for sym in rdata.SYMBOLS:
            bars = rdata.load_bars(sym, "240")
            sub, off = bars.slice(rdata.HIST_START_MS, rdata.VAL_END_MS,
                                  warmup=600)
            for j, p in enumerate(st.combos()):
                if j % 7:                       # выборочно, чтобы не ждать
                    continue
                sig = st.build(sub, p)
                if int((sig.entry != 0).sum()) == 0:
                    continue
                cfg = engine.Cfg(risk_frac=0.01)
                a = engine.run(sub, sig, cfg, start_i=off)
                b = xe.run(sub, sig, cfg, xe.XCfg(), start_i=off)
                total += 1
                if len(a.trades) != len(b.trades):
                    bad += 1
                    print("   РАСХОЖДЕНИЕ %s %s %s: сделок %d vs %d"
                          % (name, sym, p, len(a.trades), len(b.trades)))
                    continue
                da = np.array([t.pnl for t in a.trades])
                db = np.array([t.pnl for t in b.trades])
                if len(da) and not np.allclose(da, db, rtol=1e-9, atol=1e-9):
                    bad += 1
                    k = int(np.argmax(np.abs(da - db)))
                    print("   РАСХОЖДЕНИЕ pnl %s %s %s: сделка %d %.6f vs %.6f"
                          % (name, sym, p, k, da[k], db[k]))
    print("   проверено %d прогонов, расхождений %d" % (total, bad))

    print("\n2) ПРИЧИННОСТЬ СИГНАЛОВ (сигналы не менялись, но проверим)")
    for name in MEMBERS:
        st = reg[name]
        bars = rdata.load_bars("BTCUSDT", "240")
        sub, _ = bars.slice(rdata.HIST_START_MS, rdata.VAL_END_MS, warmup=600)
        ok = 0
        for p in st.combos():
            try:
                engine.assert_causal(lambda b, _p=p: st.build(b, _p), sub)
                ok += 1
            except AssertionError as e:
                print("   %s %s -> %s" % (name, p, e))
        print("   %-12s прошло %d из %d сочетаний" % (name, ok, st.n_combos()))


if __name__ == "__main__":
    main()
