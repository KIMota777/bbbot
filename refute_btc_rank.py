# -*- coding: utf-8 -*-
"""ПОПЫТКА СЛОМАТЬ НАХОДКУ, ЧАСТЬ 3: чем именно отвергли и что вообще
предсказывает порядок по холдауту.

Блок 10. ИСХОДНЫЙ КРИТЕРИЙ ОТБОРА, ПОСЧИТАННЫЙ ЗАНОВО. Волны v4..v8 отбирали
  не по итогу за период, а по evolution2.oos_score = p25 месяца + 0.5*медиана
  месяца, усреднённому по трём экзаменам walk-forward (e4.fold_bounds_3y).
  Считаем эту величину для BTC normal и BTC final — так видно, по какой мере
  normal проиграл, а не догадки об этом.

Блок 11. ПРЕДСКАЗЫВАЕТ ЛИ ХОЛДОУТ ХОТЬ ЧТО-НИБУДЬ. Все конфиги из config.py
  (normal/bear/final по пяти монетам) прогоняются на обучающей части и на
  холдоуте при ОДНОМ плече x5. Если порядок «кто лучше» на двух половинах
  истории не совпадает (ранговая корреляция около нуля или отрицательна), то
  «лучший по холдауту» — это выбор по шуму, и утверждение «normal лучше
  final» не несёт информации о будущем. Это и есть проверка самой процедуры,
  которой сделана находка.

Блок 12. НУЛЕВОЕ РАСПРЕДЕЛЕНИЕ, ОЧИЩЕННОЕ. В прошлом прогоне большая часть
  случайных геномов вообще не торговала (итог ровно 0%), и они занижали
  планку. Пересчитываем, оставляя только те, что сделали не меньше 10 сделок.
"""
import random as pyrand
import sys
import time

import numpy as np

import bots_honest as bh
import refute_btc_cache as cache
import config
import evolution as ev
import evolution2 as e2
import evolution4 as e4
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

MODES = ("normal", "bear", "final")


def seg(candles, aux, a, b):
    c = candles[a:b]
    return dict(candles=c, pre=e2.prep(c),
                aux={k: bh.slice_aux(v, a, b) for k, v in aux.items()},
                months=(c[-1][0] - c[0][0]) / (30 * 86400000))


def run(s, g, lev):
    evs = []
    r = bh.run_at(s["candles"], s["pre"], g, e8.make_filter8(g, s["aux"]),
                  lev, events=evs)
    m = bh.summarize(r, evs, s["months"])
    m["r"] = r
    return m


def spearman(x, y):
    """Ранговая корреляция без scipy: корреляция Пирсона по рангам."""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos + 1.0
        return np.array(r)
    a, b = ranks(x), ranks(y)
    return float(np.corrcoef(a, b)[0, 1])


def main():
    tee = bh.Tee("refute_btc_rank_out.txt")
    old, sys.stdout = sys.stdout, tee
    try:
        body()
    finally:
        sys.stdout = old


def body():
    print("ПОПЫТКА ОПРОВЕРЖЕНИЯ, ЧАСТЬ 3 — " + time.strftime("%Y-%m-%d %H:%M"))
    btc_c, btc_aux = cache.load()
    e2.BARS_PER_DAY = 96
    n = len(btc_c)
    h = int(n * bh.HOLD_FRAC)

    def gen(sym, mode):
        p = config.SYMBOL_PARAMS[sym].get(mode)
        if not p or "step" not in p:
            return None, None
        g = e7.cfg_to_genome(p, mode)
        for k, v in e8.OFF8.items():
            g.setdefault(k, v)
        return g, p.get("lev", 5)

    gn, _ = gen("BTCUSDT", "normal")
    gf, _ = gen("BTCUSDT", "final")

    # ---- 10. исходный критерий отбора -----------------------------------
    print("\n" + "=" * 100)
    print("БЛОК 10. КРИТЕРИЙ ОТБОРА ВОЛН v4..v8, ПОСЧИТАННЫЙ ЗАНОВО")
    print("  oos_score = p25 месячной доходности + 0.5*медиана, три экзамена "
          "walk-forward")
    folds = e4.fold_bounds_3y(n)
    for name, g in (("normal", gn), ("final", gf)):
        for lev in (5, 15):
            sc, det = [], []
            for (a, b, e) in folds:
                s = seg(btc_c, btc_aux, b, e)
                m = run(s, g, lev)
                sc.append(e2.oos_score(m["r"]))
                det.append("%+0.2f (%d сд.)" % (sc[-1], m["trades"]))
            print("  %-7s x%-3d средний OOS %+6.2f  |  %s"
                  % (name, lev, sum(sc) / len(sc), "  ".join(det)))

    # ---- 11. предсказывает ли холдоут -----------------------------------
    print("\n" + "=" * 100)
    print("БЛОК 11. ВСЕ КОНФИГИ config.py НА ОБЕИХ ПОЛОВИНАХ, ПЛЕЧО x5")
    pct5 = xd.fetch_daily_pct5()
    rows = []
    for sym in config.SYMBOL_PARAMS:
        if sym == "BTCUSDT":
            candles, aux = btc_c, btc_aux
        else:
            candles = ev.fetch(sym, "15", bh.DAYS)
            aux = e8.make_aux_builder(pct5, 96)(sym, candles)
        nn = len(candles)
        hh = int(nn * bh.HOLD_FRAC)
        tr = seg(candles, aux, 0, hh)
        ho = seg(candles, aux, hh, nn)
        for mode in MODES:
            g, lev = gen(sym, mode)
            if g is None:
                continue
            a = run(tr, g, 5)
            b = run(ho, g, 5)
            rows.append((sym.replace("USDT", ""), mode, a["comp"], a["trades"],
                         b["comp"], b["trades"]))
            print("  %-5s %-7s обучение %+8.1f%% (%4d сд.)   "
                  "холдоут %+8.1f%% (%4d сд.)"
                  % rows[-1])
            sys.stdout.flush()
    xs = [r[2] for r in rows]
    ys = [r[4] for r in rows]
    print("\n  ранговая корреляция «обучение -> холдоут» по %d конфигам: "
          "%+0.2f" % (len(rows), spearman(xs, ys)))
    best_tr = max(rows, key=lambda r: r[2])
    best_ho = max(rows, key=lambda r: r[4])
    order_ho = sorted(rows, key=lambda r: -r[4])
    order_tr = sorted(rows, key=lambda r: -r[2])
    print("  лучший по обучению: %s %s -> на холдауте место %d из %d"
          % (best_tr[0], best_tr[1], order_ho.index(best_tr) + 1, len(rows)))
    print("  лучший по холдауту: %s %s -> на обучении место %d из %d"
          % (best_ho[0], best_ho[1], order_tr.index(best_ho) + 1, len(rows)))
    btc_n = [r for r in rows if r[0] == "BTC" and r[1] == "normal"][0]
    print("  BTC normal: место %d из %d на обучении, место %d из %d на "
          "холдауте"
          % (order_tr.index(btc_n) + 1, len(rows),
             order_ho.index(btc_n) + 1, len(rows)))

    # ---- 12. очищенное нулевое распределение ----------------------------
    print("\n" + "=" * 100)
    print("БЛОК 12. СЛУЧАЙНЫЕ ГЕНОМЫ, ТОЛЬКО ТОРГУЮЩИЕ (>= 10 сделок), x5")
    ho_btc = seg(btc_c, btc_aux, h, n)
    tr_btc = seg(btc_c, btc_aux, 0, h)
    for tag, s in (("холдоут", ho_btc), ("обучение", tr_btc)):
        pr = pyrand.Random(2026)
        vals, zero = [], 0
        for _ in range(400):
            g = bh.rand_core(e2.GENES, pr)
            for k, v in e8.OFF8.items():
                g.setdefault(k, v)
            try:
                m = run(s, g, 5)
            except Exception:                      # noqa: BLE001
                continue
            if m["trades"] < 10:
                zero += 1
                continue
            vals.append(m["comp"])
        a = np.array(vals)
        print("  %-9s из 400 случайных торговали %d (не торговали %d) | "
              "медиана %+7.1f%% | доля в плюсе %3.0f%% | 90%% %+7.1f%% | "
              "макс %+7.1f%%"
              % (tag, len(a), zero, np.median(a), (a > 0).mean() * 100,
                 np.percentile(a, 90), a.max()))
        sys.stdout.flush()
    mn = run(ho_btc, gn, 5)
    pr = pyrand.Random(2026)
    vals = []
    for _ in range(400):
        g = bh.rand_core(e2.GENES, pr)
        for k, v in e8.OFF8.items():
            g.setdefault(k, v)
        try:
            m = run(ho_btc, g, 5)
        except Exception:                          # noqa: BLE001
            continue
        if m["trades"] >= 10:
            vals.append(m["comp"])
    a = np.array(vals)
    print("  доля торгующих случайных, которые НЕ ХУЖЕ normal (%+.1f%%): "
          "%.1f%%" % (mn["comp"], (a >= mn["comp"]).mean() * 100))


if __name__ == "__main__":
    main()
