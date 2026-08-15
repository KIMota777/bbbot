# -*- coding: utf-8 -*-
"""Общий кэш для проверки прочности отрицательного вывода.

Пересчитывает внеобучающие сделки тех же двадцати кандидатов, что разбирал
post_mortem.py, НО ТОЛЬКО ДО ГРАНИЦЫ ПРОВЕРКИ. Экзаменационная выборка здесь
не читается вовсе: границей прогона стоит VAL_END_MS.

Зачем свой кэш: post_mortem сохраняет только итоговые числа, а для оценки
мощности и разброса нужны сами сделки — из них строятся помесячные ряды и
любые подокна.

Окна нарезаются так же (360/90, скользящие), поэтому сделки внутри
обучения+проверки совпадают с теми, что считал автор.
"""
import json
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import portfolio   # noqa: E402
import rdata       # noqa: E402
import universe    # noqa: E402

OUT = os.path.join(DIR, "out")
PATH = os.path.join(OUT, "refute_stats_trades.json")

NAMES = ["c_willr", "bos", "vol_spike", "supertrend", "c_rsi", "c_mfi",
         "c_cci", "c_bb", "macd", "atr_break", "bb_break", "vol_adj_mom",
         "mom_continuation", "vol_expansion", "rsi_mr", "ema_dist",
         "false_break", "keltner_mr", "donchian", "tsmom"]
ENSEMBLE = ["c_willr", "bos", "vol_spike", "supertrend"]
TF = "240"


def build():
    reg, _ = universe.load()
    t0, t1 = rdata.SPLITS["trainval"]          # экзамен не затрагивается
    data = {}
    for name in NAMES:
        st = reg.get(name)
        if st is None:
            continue
        per = {}
        for sym in rdata.SYMBOLS:
            trs, _ = portfolio.oos_trades(st, sym, TF, t_start=t0, t_end=t1)
            if trs:
                per[sym] = [[t["t_in"], t["t_out"], t["ret"], t["low"]]
                            for t in trs]
        if per:
            data[name] = per
            print("  %-18s сделок %5d"
                  % (name, sum(len(v) for v in per.values())))
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return data


def load():
    if os.path.exists(PATH):
        with open(PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return build()


def as_trades(raw, names=None):
    """{имя: {символ: [сделка,...]}} в формате portfolio.combine."""
    out = {}
    for name, per in raw.items():
        if names is not None and name not in names:
            continue
        out[name] = {sym: [dict(symbol=sym, t_in=int(a), t_out=int(b),
                                ret=float(r), low=float(lo))
                           for a, b, r, lo in rows]
                     for sym, rows in per.items()}
    return out


if __name__ == "__main__":
    build()
    print("сохранено -> %s" % PATH)
