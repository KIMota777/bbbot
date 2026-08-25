# -*- coding: utf-8 -*-
"""Общая часть атаки на значимость. Ничего чужого не меняет."""
import json, os
import numpy as np
import bots_honest as bh
import evolution as ev
import evolution2 as e2
import evolution8 as e8
import ext_data as xd

e2.BARS_PER_DAY = 96
JSON = os.path.join("webapp", "data", "all_configs_honest.json")

_AUX = {}
_CND = {}


def prep_sym(sym, pct5):
    if sym not in _CND:
        c = ev.fetch(sym, "15", bh.DAYS)
        _CND[sym] = c
        _AUX[sym] = e8.make_aux_builder(pct5, 96)(sym, c)
    return _CND[sym], _AUX[sym]


def halves(sym, g, pct5):
    candles, aux = prep_sym(sym, pct5)
    n = len(candles)
    h = int(n * bh.HOLD_FRAC)
    out = {}
    for name, a, b in (("train", 0, h), ("hold", h, n)):
        c = candles[a:b]
        out[name] = dict(candles=c, pre=e2.prep(c),
                         months=(c[-1][0] - c[0][0]) / (30 * 86400000),
                         filt=e8.make_filter8(
                             g, dict((k, bh.slice_aux(v, a, b))
                                     for k, v in aux.items())))
    return out


def run_half(part, g, lev=5.0):
    """Как one_half в all_configs_honest, но ещё отдаёт сделки с временем."""
    evs = []
    r = bh.run_at(part["candles"], part["pre"], g, part["filt"], lev, events=evs)
    m = bh.summarize(r, evs, part["months"])
    tiny = bh.cycle_metrics(evs, part["candles"], g, part["months"])
    cl = [(e["t"], e["pnl"]) for e in evs if e["type"] == "close"]
    return dict(comp=float(tiny.get("comp_nt", m.get("comp", 0.0))),
                trades=int(m.get("trades", 0)),
                closes=cl, months=float(part["months"]))


def comp_of(pnls):
    eq = 1.0
    for p in pnls:
        eq *= (1 + p / e2.START)
    return (eq - 1) * 100


def month_key(ms):
    import time
    t = time.gmtime(ms / 1000)
    return t.tm_year * 12 + t.tm_mon


def rows():
    return json.load(open(JSON, encoding="utf-8"))["rows"]
