# -*- coding: utf-8 -*-
"""Общая подготовка для проверок BTC: свечи, aux, срез холдоута, геномы.

Вынесено отдельно, потому что загрузка истории и пересчёт внешних рядов
занимают секунды, а сам прогон — сотые доли секунды: нет смысла платить за
подготовку в каждом скрипте заново.
"""
import bots_honest as bh
import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

SYM = "BTCUSDT"
_PCT5 = None


def prep(sym=SYM):
    """История монеты, внешние ряды и готовый срез холдоута (последние 28%)."""
    global _PCT5
    if _PCT5 is None:
        _PCT5 = xd.fetch_daily_pct5()
    e2.BARS_PER_DAY = 96
    candles = ev.fetch(sym, "15", bh.DAYS)
    aux = e8.make_aux_builder(_PCT5, 96)(sym, candles)
    n = len(candles)
    h = int(n * bh.HOLD_FRAC)
    ho, tr = candles[h:], candles[:h]
    return dict(sym=sym, candles=candles, aux=aux, n=n, h=h,
                ho=ho, pre=e2.prep(ho),
                aux_h={k: bh.slice_aux(v, h, n) for k, v in aux.items()},
                tr=tr, pre_tr=e2.prep(tr),
                aux_tr={k: bh.slice_aux(v, 0, h) for k, v in aux.items()},
                months=(ho[-1][0] - ho[0][0]) / (30 * 86400000),
                months_tr=(tr[-1][0] - tr[0][0]) / (30 * 86400000))


def genome(mode, sym=SYM):
    p = config.SYMBOL_PARAMS[sym][mode]
    g = e7.cfg_to_genome(p, mode)
    for k, v in e8.OFF8.items():
        g.setdefault(k, v)
    return g, p.get("lev", 5)


def one(P, g, lev, part="ho"):
    """Один прогон на выбранной части истории -> числа, которыми сравниваем."""
    if part == "ho":
        cnd, pre, aux, mon = P["ho"], P["pre"], P["aux_h"], P["months"]
    else:
        cnd, pre, aux, mon = P["tr"], P["pre_tr"], P["aux_tr"], P["months_tr"]
    evs = []
    r = bh.run_at(cnd, pre, g, e8.make_filter8(g, aux), lev, events=evs)
    m = bh.summarize(r, evs, mon)
    pnls = [e["pnl"] for e in evs if e["type"] == "close"]
    return dict(trades=m["trades"], wr=m["wr"], ret=m["ret"], comp=m["comp"],
                dd=m["dd"], ruined=m["ruined"],
                ret_nt=bh.ret_no_tiny(pnls), pnls=pnls,
                ts=[e["t"] for e in evs if e["type"] == "close"],
                sides=[e["side"] for e in evs if e["type"] == "entry"])
