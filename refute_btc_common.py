# -*- coding: utf-8 -*-
"""Общая подготовка для попыток сломать находку «BTC normal x5 из архива».

Ничего не подбирает и не переписывает движок: собирает ровно тот же объект,
что собирает archive_honest.build, и даёт удобные обёртки для прогонов.
Всё считается на ХОЛДАУТЕ (последние 28% истории) — той же долей 0.72.
"""
import numpy as np

import bots_honest as bh
import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

SYM, MODE = "BTCUSDT", "normal"


def build():
    """BTC normal x5: геном, свечи, aux, срез холдаута — как в archive_honest."""
    pct5 = xd.fetch_daily_pct5()
    e2.BARS_PER_DAY = 96
    p = config.SYMBOL_PARAMS[SYM][MODE]
    g = e7.cfg_to_genome(p, MODE)
    for k, v in e8.OFF8.items():
        g.setdefault(k, v)
    candles = ev.fetch(SYM, "15", bh.DAYS)
    aux = e8.make_aux_builder(pct5, 96)(SYM, candles)
    n = len(candles)
    h = int(n * bh.HOLD_FRAC)
    ho = candles[h:]
    aux_h = {k: bh.slice_aux(v, h, n) for k, v in aux.items()}
    return dict(g=g, lev=p.get("lev", 5), candles=candles, aux=aux, n=n,
                hold_i=h, aux_h=aux_h,
                hold=dict(candles=ho, pre=e2.prep(ho), aux=aux_h,
                          months=(ho[-1][0] - ho[0][0]) / (30 * 86400000),
                          filt=e8.make_filter8(g, aux_h)))


def run_hold(d, g=None, pre=None, filt=None, lev=None):
    """Один прогон на холдауте. Возвращает (итог движка, события)."""
    g = g if g is not None else d["g"]
    s = d["hold"]
    evs = []
    r = bh.run_at(s["candles"], pre if pre is not None else s["pre"], g,
                  filt if filt is not None else s["filt"],
                  lev if lev is not None else d["lev"], events=evs)
    return r, evs


def nums(r, evs, months):
    """Набор чисел одного прогона в тех же единицах, что в archive_honest."""
    m = bh.summarize(r, evs, months)
    pnls = [e["pnl"] for e in evs if e["type"] == "close"]
    return dict(trades=m["trades"], wr=m["wr"], ret=m["ret"], comp=m["comp"],
                dd=m["dd"], ruined=m["ruined"],
                comp_nt=bh.ret_no_tiny(pnls),
                tiny_share=(100.0 * sum(1 for p in pnls if bh.is_tiny(p))
                            / len(pnls)) if pnls else 0.0,
                pnls=pnls)


def pct(a, q):
    return float(np.percentile(np.array(a, dtype=float), q)) if len(a) else 0.0
