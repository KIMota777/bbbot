# -*- coding: utf-8 -*-
u"""Проверки самого инструмента, прежде чем ему верить.

Порядок тот же, что у автора: сначала доказать, что измерительный прибор не
врёт, и только потом мерить им.

1. Дневные бары действительно собраны из 4ч без потерь: открытие первого,
   закрытие последнего, экстремумы по всей группе, число дней сходится.
2. Симулятор весов при весе 1.0 даёт ровно «купил и держу» с точностью до
   одной комиссии на входе.
3. Причинность правил проверяется порчей будущего.
4. Симулятор весов и сделочный движок автора на ОДНОЙ И ТОЙ ЖЕ простейшей
   системе дают согласующийся ответ по знаку и порядку величины.
"""
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import rdata                     # noqa: E402
import refute_horizon_lib as L   # noqa: E402


def check_resample():
    b4 = rdata.load_bars("BTCUSDT", "240")
    d = L.daily("BTCUSDT")
    w = L.weekly("BTCUSDT")
    print(u"дневных баров: %d, недельных: %d (из %d четырёхчасовых)"
          % (len(d), len(w), len(b4)))
    assert np.all(np.diff(d.t) == L.DAY), u"дневная сетка рваная"
    assert np.all(np.diff(w.t) == 7 * L.DAY), u"недельная сетка рваная"
    # выборочная сверка десяти дней
    bad = 0
    for j in np.linspace(1, len(d) - 2, 10).astype(int):
        m = (b4.t >= d.t[j]) & (b4.t < d.t[j] + L.DAY)
        if m.sum() != 6:
            bad += 1
            continue
        ok = (abs(b4.o[m][0] - d.o[j]) < 1e-9 and
              abs(b4.c[m][-1] - d.c[j]) < 1e-9 and
              abs(b4.h[m].max() - d.h[j]) < 1e-9 and
              abs(b4.l[m].min() - d.l[j]) < 1e-9)
        if not ok:
            bad += 1
    print(u"сверка дней с исходными 4ч: расхождений %d из 10" % bad)
    assert bad == 0
    import datetime as dt
    f = dt.datetime.fromtimestamp(d.t[0] / 1000, dt.UTC)
    assert f.hour == 0, u"день не начинается в 00:00 UTC"
    fw = dt.datetime.fromtimestamp(w.t[0] / 1000, dt.UTC)
    print(u"первый день %s, первая неделя %s (%s)"
          % (f.date(), fw.date(), fw.strftime("%A")))


def check_buyhold():
    t, px = L.panel(["BTCUSDT"], "D")
    m = L.in_split(t, "trainval")
    t2 = t[m]
    px2 = {s: {k: v[m] for k, v in p.items() if k != "t"} for s, p in px.items()}
    px2["BTCUSDT"]["t"] = t2
    wts = {"BTCUSDT": np.ones(len(t2))}
    r = L.WSim(t2, px2, wts, funding=False, fee=0.0).run()
    # эталон: цена открытия первого дня -> цена открытия последнего
    o = px2["BTCUSDT"]["o"]
    ref = o[len(r["curve"]) - 1 + 1] / o[1] - 1.0
    print(u"симулятор при весе 1.0: %+.2f%%, «купил и держу»: %+.2f%% "
          u"(без комиссий и фандинга)" % (100 * r["ret"], 100 * ref))
    assert abs(r["ret"] - ref) < 0.02, u"симулятор расходится с buy&hold"


def check_causal():
    t, px = L.panel(rdata.SYMBOLS, "D")

    def w_tsmom(times, p):
        out = {}
        for s, q in p.items():
            c = q["c"]
            r = np.full(len(c), np.nan)
            r[90:] = c[90:] / c[:-90] - 1.0
            out[s] = np.where(np.isnan(r), 0.0, np.sign(r))
        return out

    L.refute_causal_w(w_tsmom, t, px)
    print(u"проверка причинности правил-весов: пройдена")


def check_vs_engine():
    u"""Одно и то же правило двумя способами. Абсолютного совпадения быть не
    может (движок держит стоп и считает размер от риска), но знак и порядок
    величины обязаны сойтись, иначе один из двух счётчиков врёт."""
    import engine
    import strat  # noqa: F401
    b = L.daily("BTCUSDT")
    sub, off = b.slice(*rdata.SPLITS["trainval"], warmup=0)

    def build(bb):
        n = len(bb.t)
        sig = engine.Signals(n)
        c = bb.c
        r = np.zeros(n)
        r[90:] = c[90:] / c[:-90] - 1.0
        e = np.zeros(n, dtype=np.int8)
        e[r > 0] = 1
        e[r < 0] = -1
        e[:91] = 0
        sig.entry = e
        sig.stop = np.full(n, 0.25)
        return sig

    engine.assert_causal(build, sub)
    print(u"engine.assert_causal на дневных барах: пройдена")
    # risk_frac=0.25 при стопе 0.25 даёт объём ровно в капитал — то есть
    # плечо 1, как и вес ±1 в симуляторе. Иначе сравнивать нечего.
    res = engine.run(sub, build(sub), engine.Cfg(risk_frac=0.25, max_lev=1.0),
                     start_i=off)
    days = (sub.t[-1] - sub.t[off]) / L.DAY
    ret = res.final_equity / res.start_equity - 1.0
    print(u"движок, TSMOM-90 на BTC, плечо 1: %+.1f%% за %.0f дней, сделок %d"
          % (100 * ret, days, len(res.trades)))

    t, px = L.panel(["BTCUSDT"], "D")
    m = L.in_split(t, "trainval")
    t2 = t[m]
    px2 = {s: {k: v[m] for k, v in q.items() if k != "t"} for s, q in px.items()}
    c = px2["BTCUSDT"]["c"]
    r = np.zeros(len(c))
    r[90:] = c[90:] / c[:-90] - 1.0
    w = np.sign(r)
    w[:91] = 0.0
    rr = L.WSim(t2, px2, {"BTCUSDT": w}).run()
    print(u"симулятор весов, то же правило, вес ±1:  %+.1f%% за %.0f дней"
          % (100 * rr["ret"], rr["days"]))


if __name__ == "__main__":
    check_resample()
    print()
    check_buyhold()
    print()
    check_causal()
    print()
    check_vs_engine()
