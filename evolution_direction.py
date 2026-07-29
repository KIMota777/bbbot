# -*- coding: utf-8 -*-
"""Эволюция чисто лонговых и чисто шортовых вариантов стратегии.

Для каждой монеты:
  - GA (train 8 мес) отдельно для long-only и short-only;
  - экзамен OOS (последние 4 мес);
  - сравнение с текущим лучшим двусторонним конфигом на OOS и полном годе.
"""

import evolution as ev

SYMBOLS = ["DOGEUSDT", "LTCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]

# текущие лучшие двусторонние (то, что сейчас в config.py)
BEST_BOTH = {
    "DOGEUSDT": dict(rsi_os=35, zone=0.28, window=518, step=0.011, levels=3,
                     mult=1.8, tp=0.038, sweep=0.020, max_bars=205, cooldown=0,
                     knife=0.0),
    "LTCUSDT": dict(rsi_os=34, zone=0.25, window=377, step=0.008, levels=3,
                    mult=1.2, tp=0.020, sweep=0.020, max_bars=160, cooldown=0,
                    knife=0.0),
    "BTCUSDT": dict(rsi_os=25, zone=0.25, window=400, step=0.015, levels=3,
                    mult=1.5, tp=0.020, sweep=0.020, max_bars=192, cooldown=0,
                    knife=0.0),
    "ETHUSDT": dict(rsi_os=25, zone=0.49, window=160, step=0.014, levels=3,
                    mult=1.6, tp=0.018, sweep=0.018, max_bars=192, cooldown=0,
                    knife=0.0),
    "SOLUSDT": dict(rsi_os=30, zone=0.25, window=400, step=0.010, levels=3,
                    mult=1.5, tp=0.020, sweep=0.020, max_bars=192, cooldown=0,
                    knife=0.0),
}


def full_report(candles, pre, g, half):
    r = ev.run4(candles, pre, g)
    st = ev.stats(r)
    r1 = ev.run4(candles[:half], ev.prep(candles[:half]), g)
    r2 = ev.run4(candles[half:], ev.prep(candles[half:]), g)
    ret = (r["balance"] / ev.START - 1) * 100
    ret1 = (r1["balance"] / ev.START - 1) * 100
    ret2 = (r2["balance"] / ev.START - 1) * 100
    wr = r["wins"] / r["trades"] * 100 if r["trades"] else 0
    return (f"год {ret:+8.1f}% ({ret1:+.1f}/{ret2:+.1f}) | "
            f"мес.мед {st['med']:+5.2f}% | P25 {st['p25']:+5.2f}% | "
            f"WR {wr:4.1f}% | DD {r['max_dd']*100:4.1f}% | сделок {r['trades']}"
            f"{' СЛИВ' if r['ruined'] else ''}")


def main():
    for sym in SYMBOLS:
        candles = ev.fetch(sym, "15", 365)
        pre = ev.prep(candles)
        split = int(len(candles) * 2 / 3)
        train, oos = candles[:split], candles[split:]
        pre_tr, pre_oos = ev.prep(train), ev.prep(oos)
        half = len(candles) // 2

        print(f"\n================ {sym} ================")
        ev.DIRECTION = "B"
        print(f"  ОБА НАПРАВЛЕНИЯ (текущий лучший):")
        print(f"    {full_report(candles, pre, BEST_BOTH[sym], half)}")

        for d, label in (("L", "ТОЛЬКО ЛОНГ"), ("S", "ТОЛЬКО ШОРТ")):
            ev.DIRECTION = d
            scored = ev.evolve(train, pre_tr, [BEST_BOTH[sym]], f"{sym[:3]}-{d}")
            seen, finalists = set(), []
            for f, g in scored:
                key = tuple(g[k] for k in ev.GENES)
                if key not in seen:
                    seen.add(key)
                    finalists.append((f, g))
                if len(finalists) == 5:
                    break
            best = None
            for f, g in finalists:
                r_oos = ev.run4(oos, pre_oos, g)
                st = ev.stats(r_oos)
                score = st["p25"] + 0.5 * st["med"] - (100 if r_oos["ruined"] else 0)
                if best is None or score > best[0]:
                    best = (score, g)
            _, g_win = best
            print(f"  {label} (победитель эволюции): {ev.fmt_genome(g_win)}")
            r_oos = ev.run4(oos, pre_oos, g_win)
            st = ev.stats(r_oos)
            oos_ret = (r_oos["balance"] / ev.START - 1) * 100
            print(f"    OOS 4мес: {oos_ret:+7.1f}% | мес.мед {st['med']:+5.2f}% | "
                  f"P25 {st['p25']:+5.2f}% | приб.мес {st['pos_share']*100:3.0f}%")
            print(f"    {full_report(candles, pre, g_win, half)}")
        ev.DIRECTION = "B"


if __name__ == "__main__":
    main()
