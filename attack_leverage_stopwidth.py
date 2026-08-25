# -*- coding: utf-8 -*-
"""Ширина стопа каждого конфига в процентах от цены входа.

Стоп в движке ставится за экстремум окна с запасом sweep и от плеча НЕ зависит,
а цена ликвидации зависит: p_liq отстоит от средней на MM/LEV (0.95/плечо).
Значит стоп шириной больше 19% недостижим раньше ликвидации при x5, но при x15
ликвидация наступает уже на 6.3%.
"""
import json
import os
import sys
import time
from multiprocessing import Pool

import evolution as ev
import evolution2 as e2
import all_configs_honest as ach
import bots_honest as bh
import ext_data as xd

e2.BARS_PER_DAY = 96


def stop_widths(part, g):
    """Медиана |вход - стоп| / вход по всем входам половины."""
    candles, pre = part["candles"], part["pre"]
    rlow, rhigh = ev.rolling_extremes(candles, g["window"])
    evs = []
    bh.run_at(candles, pre, g, part["filt"], 5.0, events=evs)
    ws = []
    idx = {c[0]: i for i, c in enumerate(candles)}
    for e in evs:
        if e["type"] != "entry":
            continue
        i = idx.get(e["t"])
        if i is None:
            continue
        px = e["price"]
        stop = (rlow[i] * (1 - g["sweep"]) if e["side"] == "L"
                else rhigh[i] * (1 + g["sweep"]))
        ws.append(abs(px - stop) / px * 100.0)
    ws.sort()
    return (ws[len(ws) // 2] if ws else None), len(ws)


def task(job):
    idx, rec = job
    try:
        pct5 = xd.fetch_daily_pct5()
        hs = ach.halves(rec["sym"], rec["g"], pct5)
        out = {}
        for name in ("train", "hold"):
            w, n = stop_widths(hs[name], rec["g"])
            out[name] = dict(w=w, n=n)
        return dict(idx=idx, ok=True, w=out)
    except Exception as exc:                       # noqa: BLE001
        return dict(idx=idx, ok=False, err=str(exc)[:160])


def main():
    recs = ach.collect()
    xd.fetch_daily_pct5()
    jobs = [(i, r) for i, r in enumerate(recs)]
    done = {}
    with Pool(max(1, (os.cpu_count() or 4) - 3), maxtasksperchild=4) as pool:
        for r in pool.imap_unordered(task, jobs):
            done[r["idx"]] = r
    rows = []
    for i, rec in enumerate(recs):
        r = done.get(i)
        if not r or not r.get("ok"):
            continue
        rows.append(dict(sym=rec["sym"], src=rec["src"], tag=rec["tag"],
                         sweep=rec["g"]["sweep"], window=rec["g"]["window"],
                         w=r["w"]))
    with open("attack_leverage_stopwidth.json", "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False)
    print("ширина стопа (медиана по входам, %% от цены входа)")
    print("%-5s %-26s %8s %8s %7s" % ("мон", "откуда", "обуч", "холд", "sweep"))
    for x in sorted(rows, key=lambda z: -(z["w"]["train"]["w"] or 0)):
        print("%-5s %-26s %8s %8s %7.3f"
              % (x["sym"].replace("USDT", ""),
                 ("%s/%s" % (x["src"], x["tag"]))[:26],
                 ("%.2f" % x["w"]["train"]["w"]) if x["w"]["train"]["w"] else "-",
                 ("%.2f" % x["w"]["hold"]["w"]) if x["w"]["hold"]["w"] else "-",
                 x["sweep"]))
    print("\nпорог ликвидации MM/плечо: x3 %.1f%%  x5 %.1f%%  x8 %.1f%%  "
          "x10 %.1f%%  x15 %.1f%%"
          % tuple(e2.MM / L * 100 for L in (3, 5, 8, 10, 15)))


if __name__ == "__main__":
    main()
