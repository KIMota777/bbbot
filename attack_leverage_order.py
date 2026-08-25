# -*- coding: utf-8 -*-
"""АТАКА на «единое плечо x5»: тот же расчёт, что all_configs_honest, но на
нескольких единых плечах сразу. Половины и соседи считаются ОДИН раз на конфиг,
плечо меняется только в прогоне — значит разница в таблице это ровно эффект
плеча, а не разные данные/соседи.

Запуск: python attack_leverage_order.py [--pert 40] [--jobs 10]
Результат: attack_leverage_order.json (в scratch не кладём — нужен рядом).
"""
import argparse
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

import all_configs_honest as ach
import bots_honest as bh
import ext_data as xd

LEVS = [3.0, 5.0, 8.0, 10.0, 15.0]


def one_half_full(part, g, lev):
    """ТОЧНО как ach.one_half (версия с обнулением копеечных), плюс разбор
    причин выхода и сырой итог — чтобы видеть, что именно меняет плечо."""
    evs = []
    r = bh.run_at(part["candles"], part["pre"], g, part["filt"], lev,
                  events=evs)
    m = bh.summarize(r, evs, part["months"])
    tiny = bh.cycle_metrics(evs, part["candles"], g, part["months"])
    curve_nt = [(e["t"], 0.0 if bh.is_tiny(e["pnl"]) else e["pnl"])
                for e in evs if e["type"] == "close" and e["pnl"] is not None]
    comp_nt, dd_nt = bh.portfolio({"one": curve_nt}, 1)
    reasons = {}
    for e in evs:
        if e["type"] != "close":
            continue
        k = "liq" if e.get("liq") else e.get("reason", "?")
        reasons[k] = reasons.get(k, 0) + 1
    return dict(comp=float(comp_nt), dd=float(dd_nt),
                comp_raw=float(m.get("comp", 0.0)),
                ret=float(m.get("ret", 0.0)),
                trades=int(m.get("trades", 0)),
                ruined=bool(m.get("ruined", False)),
                liqs=int(m.get("liqs", 0)),
                tiny_share=float(tiny.get("tiny_share", 0.0)),
                tiny_n=int(tiny.get("tiny_n", 0)),
                reasons=reasons,
                months=float(part["months"]))


def task(job):
    idx, rec, n_pert = job
    try:
        pct5 = xd.fetch_daily_pct5()
        hs = ach.halves(rec["sym"], rec["g"], pct5)
        rng = np.random.default_rng(7)          # ТЕ ЖЕ соседи, что у автора
        neigh = [ach.perturb(rec["g"], rng) for _ in range(n_pert)]
        out = {}
        for lev in LEVS:
            res = {}
            for name in ("train", "hold"):
                res[name] = one_half_full(hs[name], rec["g"], lev)
                vals = []
                for ng in neigh:
                    try:
                        vals.append(one_half_full(hs[name], ng, lev)["comp"])
                    except Exception:              # noqa: BLE001
                        continue
                res[name]["rob"] = float(np.median(vals)) if vals else 0.0
                res[name]["rob_n"] = len(vals)
            out["%g" % lev] = res
        return dict(idx=idx, ok=True, levs=out)
    except Exception as exc:                       # noqa: BLE001
        import traceback
        return dict(idx=idx, ok=False, err=str(exc)[:200],
                    tb=traceback.format_exc()[-600:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pert", type=int, default=40)
    ap.add_argument("--jobs", type=int,
                    default=max(1, (os.cpu_count() or 4) - 3))
    a = ap.parse_args()

    recs = ach.collect()
    print("собрано уникальных геномов: %d" % len(recs))
    print("плечи: %s | возмущений: %d | процессов: %d"
          % (LEVS, a.pert, a.jobs))
    sys.stdout.flush()
    xd.fetch_daily_pct5()
    jobs = [(i, r, a.pert) for i, r in enumerate(recs)]
    t0 = time.time()
    done = {}
    with Pool(a.jobs, maxtasksperchild=4) as pool:
        for k, r in enumerate(pool.imap_unordered(task, jobs), 1):
            done[r["idx"]] = r
            if k % 5 == 0 or k == len(jobs):
                print("  %d/%d  %.0fs" % (k, len(jobs), time.time() - t0))
                sys.stdout.flush()

    rows, errs = [], {}
    for i, rec in enumerate(recs):
        r = done.get(i)
        if not r or not r.get("ok"):
            e = (r or {}).get("err", "нет результата")
            errs[e] = errs.get(e, 0) + 1
            continue
        rows.append(dict(sym=rec["sym"], src=rec["src"], tag=rec["tag"],
                         own_lev=rec["own_lev"], levs=r["levs"]))
    with open("attack_leverage_order.json", "w", encoding="utf-8") as fh:
        json.dump(dict(levs=LEVS, pert=a.pert, rows=rows, errs=errs), fh,
                  ensure_ascii=False)
    print("посчитано: %d, не посчиталось: %d" % (rows.__len__(), sum(errs.values())))
    for e, n in sorted(errs.items(), key=lambda x: -x[1])[:8]:
        print("  %3d x %s" % (n, e))
    print("-> attack_leverage_order.json  (%.0fs)" % (time.time() - t0))


if __name__ == "__main__":
    main()
