# -*- coding: utf-8 -*-
u"""Инструменты для проверки гипотезы «перевес живёт на дневном горизонте».

Автор исследования считал только 1ч и 4ч и только сделочной механикой
(вход-стоп-выход). Здесь добавляются две вещи, которых у него не было:

1. ДНЕВНЫЕ И НЕДЕЛЬНЫЕ БАРЫ, собранные из 4ч. Собранные, а не скачанные:
   так исключается расхождение источников, и видно, что ничего не потерялось.

2. СЧЁТ ПО ПОЗИЦИИ, А НЕ ПО СДЕЛКЕ. Управляемые фьючерсы (managed futures)
   исторически зарабатывали не входами-выходами со стопом, а непрерывно
   удерживаемой позицией, размер которой обратен волатильности. Такую систему
   сделочный движок выразить не может: у него одна позиция на символ, стоп
   задаёт размер, а поперечная (рыночно-нейтральная) ставка вообще
   невыразима. Поэтому здесь отдельный симулятор ВЕСОВ.

ЧТО В НЁМ ЧЕСТНОГО (те же требования, что у автора)

  * Вес, посчитанный по бару i, действует с ОТКРЫТИЯ бара i+1 до открытия
    бара i+2. Ни один вес не может опереться на цену, из которой он получен.
  * Доходность считается от открытия к открытию — по ценам, по которым
    сделка физически исполнима, а не по закрытиям.
  * Оборот стоит денег: комиссия тейкера и проскальзывание берутся с
    ИЗМЕНЕНИЯ веса, по ставкам rdata (BTC 0.025%, DOGE 0.06%).
  * Фандинг платится по фактической истории ставок, пропорционально весу.
  * Просадка считается по худшей внутридневной переоценке (лонг — по
    минимуму бара, шорт — по максимуму), а не по открытиям.
  * Причинность проверяется механически: refute_causal_w портит будущее
    случайным блужданием и требует, чтобы прошлые веса не шелохнулись.
"""
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import rdata  # noqa: E402

DAY = rdata.DAY_MS


# --- сборка старших баров ---------------------------------------------------

def resample(bars, period_ms, origin_ms=0):
    u"""Собрать бары периода period_ms из младших. Неполные группы отброшены.

    Группа берётся по метке начала: бар с меткой t принадлежит группе
    floor((t - origin) / period). Открытие группы — открытие первого бара,
    закрытие — закрытие последнего, максимум и минимум — по всей группе.
    Отбрасываются группы, в которых баров меньше, чем должно быть: иначе
    первый и последний день истории оказываются короче остальных, и любое
    правило, читающее «вчерашний диапазон», получает искажённый вход.
    """
    step = bars.tf * rdata.MS_MIN
    need = period_ms // step
    key = (bars.t - origin_ms) // period_ms
    # границы групп
    edges = np.flatnonzero(np.diff(key)) + 1
    starts = np.concatenate([[0], edges])
    ends = np.concatenate([edges, [len(bars.t)]])
    keep = (ends - starts) == need
    starts, ends = starts[keep], ends[keep]
    n = len(starts)
    out = np.empty((n, 7))
    out[:, 0] = (key[starts] * period_ms + origin_ms)
    out[:, 1] = bars.o[starts]
    out[:, 4] = bars.c[ends - 1]
    for j in range(n):
        a, b = starts[j], ends[j]
        out[j, 2] = bars.h[a:b].max()
        out[j, 3] = bars.l[a:b].min()
        out[j, 5] = bars.v[a:b].sum()
        out[j, 6] = bars.turnover[a:b].sum()
    b2 = rdata.Bars(bars.symbol, period_ms // rdata.MS_MIN, out)
    return b2


_DCACHE = {}


def daily(symbol):
    if ("D", symbol) not in _DCACHE:
        _DCACHE[("D", symbol)] = resample(rdata.load_bars(symbol, "240"), DAY)
    return _DCACHE[("D", symbol)]


def weekly(symbol):
    u"""Недельные бары. Origin выбран так, чтобы неделя начиналась в понедельник
    00:00 UTC (1970-01-01 был четверг, поэтому сдвиг на 4 дня)."""
    if ("W", symbol) not in _DCACHE:
        _DCACHE[("W", symbol)] = resample(rdata.load_bars(symbol, "240"),
                                          7 * DAY, origin_ms=4 * DAY)
    return _DCACHE[("W", symbol)]


def panel(symbols, tf="D"):
    u"""Общая временная сетка для нескольких монет: только те бары, что есть у
    всех. Без этого поперечное сравнение сравнивало бы разные моменты."""
    fn = daily if tf == "D" else weekly
    bs = [fn(s) for s in symbols]
    common = bs[0].t
    for b in bs[1:]:
        common = np.intersect1d(common, b.t)
    out = {}
    for s, b in zip(symbols, bs):
        idx = np.searchsorted(b.t, common)
        out[s] = dict(t=common, o=b.o[idx], h=b.h[idx], l=b.l[idx],
                      c=b.c[idx], v=b.v[idx])
    return common, out


# --- симулятор весов --------------------------------------------------------

class WSim:
    u"""Портфель заданных весов с честными издержками.

    weights[s][i] — доля капитала в монете s, ЖЕЛАЕМАЯ по итогам бара i.
    Фактически она удерживается с открытия i+1 до открытия i+2.
    """

    def __init__(self, times, px, weights, start=10000.0, fee=None,
                 funding=True, max_gross=10.0):
        self.t = np.asarray(times, dtype=np.int64)
        self.px = px
        self.w = weights
        self.start = start
        self.fee = rdata.TAKER_FEE if fee is None else fee
        self.funding = funding
        self.max_gross = max_gross

    def run(self):
        t = self.t
        n = len(t)
        syms = sorted(self.w)
        eq = self.start
        peak = eq
        curve = [eq]
        curve_t = [int(t[0])]
        worst_curve = [eq]
        prev_w = {s: 0.0 for s in syms}
        cost_total = 0.0
        fund_total = 0.0
        turn_total = 0.0
        gross_hits = 0
        # позиция, выставленная по бару i, живёт с открытия i+1 до открытия i+2
        for i in range(n - 2):
            tgt = {}
            gross = sum(abs(float(self.w[s][i])) for s in syms)
            k = 1.0
            if gross > self.max_gross:
                k = self.max_gross / gross
                gross_hits += 1
            for s in syms:
                v = float(self.w[s][i]) * k
                tgt[s] = 0.0 if not np.isfinite(v) else v
            # издержки перестановки — на открытии бара i+1
            cost = 0.0
            for s in syms:
                d = abs(tgt[s] - prev_w[s])
                if d > 0:
                    slip = rdata.SLIP_BPS.get(s, 0.0005)
                    cost += eq * d * (self.fee + slip)
                    turn_total += eq * d
            eq -= cost
            cost_total += cost
            # доход позиции: открытие i+1 -> открытие i+2
            pnl = 0.0
            adverse = 0.0          # худшая переоценка внутри удерживаемого бара
            for s in syms:
                w = tgt[s]
                if w == 0.0:
                    continue
                p = self.px[s]
                o1, o2 = p["o"][i + 1], p["o"][i + 2]
                pnl += eq * w * (o2 / o1 - 1.0)
                lo = w * (p["l"][i + 1] / o1 - 1.0)
                hi = w * (p["h"][i + 1] / o1 - 1.0)
                adverse += eq * min(lo, hi, 0.0)
                if self.funding:
                    f = rdata.funding_paid(s, int(t[i + 1]), int(t[i + 2]),
                                           eq * abs(w), 1 if w > 0 else -1)
                    pnl -= f
                    fund_total += f
            worst_curve.append(max(eq + adverse, 0.0))
            eq += pnl
            eq = max(eq, 0.0)
            curve.append(eq)
            curve_t.append(int(t[i + 2]))
            peak = max(peak, eq)
            prev_w = tgt
            if eq <= 0:
                break
        v = np.asarray(curve)
        # просадка: чередуем «яма внутри бара» и «закрытие бара»
        w2 = np.asarray(worst_curve)
        m = min(len(v), len(w2))
        mixed = np.empty(2 * m)
        mixed[0::2] = w2[:m]
        mixed[1::2] = v[:m]
        import metrics
        dd = metrics.max_drawdown(mixed)[0]
        days = (curve_t[-1] - curve_t[0]) / DAY if len(curve_t) > 1 else 1.0
        ret = v[-1] / self.start - 1.0
        return dict(curve=v, times=curve_t, ret=float(ret), maxdd=float(dd),
                    days=float(days),
                    mo=float((1 + ret) ** (30.0 / max(days, 1)) - 1.0)
                    if ret > -1 else -1.0,
                    cagr=float((1 + ret) ** (365.0 / max(days, 1)) - 1.0)
                    if ret > -1 else -1.0,
                    costs=float(cost_total), funding=float(fund_total),
                    turnover=float(turn_total), gross_hits=gross_hits)


def curve_stats(res, start_v=None):
    u"""Помесячная доходность и Шарп по дневным приращениям кривой."""
    v = res["curve"]
    t = np.asarray(res["times"], dtype=np.int64)
    r = np.diff(v) / np.maximum(v[:-1], 1e-9)
    sd = float(r.std(ddof=1)) if len(r) > 2 else 0.0
    per_year = 365.0 / max((t[-1] - t[0]) / DAY / max(len(r), 1), 1e-9)
    sharpe = float(r.mean() / sd * np.sqrt(per_year)) if sd > 0 else 0.0
    import datetime as dt
    mo = {}
    for ts, x in zip(t[1:], r):
        d = dt.datetime.fromtimestamp(ts / 1000, dt.UTC)
        mo.setdefault((d.year, d.month), []).append(np.log1p(max(x, -0.999)))
    mv = np.array([np.expm1(sum(x)) for x in mo.values()])
    return dict(sharpe=sharpe, mo_med=float(np.median(mv)) if len(mv) else 0.0,
                mo_mean=float(mv.mean()) if len(mv) else 0.0,
                mo_pos=float((mv > 0).mean()) if len(mv) else 0.0,
                n_months=len(mv), vol_ann=float(sd * np.sqrt(per_year)),
                monthly=mv)


# --- механическая проверка причинности для правил-весов ---------------------

def refute_causal_w(make_weights, times, px, cut=0.6, seed=0):
    u"""Тот же приём, что engine.assert_causal, но для функции, выдающей веса.

    Портим ВСЁ после точки cut независимым случайным блужданием и требуем,
    чтобы веса до точки не изменились ни в одном разряде. Так ловится любое
    заглядывание: центрированные окна, нормировка по всей выборке, ранжирование
    «по итогам периода», подтягивание чужого ряда без сдвига.
    """
    rng = np.random.default_rng(seed)
    n = len(times)
    k = int(n * cut)
    a = make_weights(times, px)
    px2 = {}
    for s, p in px.items():
        q = {kk: np.array(vv, dtype=float) if kk != "t" else np.array(vv)
             for kk, vv in p.items()}
        m = n - k
        base = float(p["c"][k - 1])
        walk = base * np.exp(np.cumsum(rng.normal(0, 0.03, m)))
        q["o"][k:] = walk * (1 + rng.normal(0, 0.005, m))
        q["c"][k:] = walk * (1 + rng.normal(0, 0.005, m))
        q["h"][k:] = np.maximum(q["o"][k:], q["c"][k:]) * (1 + rng.uniform(0, .03, m))
        q["l"][k:] = np.minimum(q["o"][k:], q["c"][k:]) * (1 - rng.uniform(0, .03, m))
        q["v"][k:] = p["v"][k:] * rng.uniform(0.2, 5.0, m)
        px2[s] = q
    b = make_weights(times, px2)
    bad = []
    for s in sorted(a):
        x = np.asarray(a[s], dtype=float)[:k]
        y = np.asarray(b[s], dtype=float)[:k]
        d = ~np.isclose(x, y, rtol=1e-9, atol=1e-12, equal_nan=True)
        if d.any():
            bad.append("%s: %d расхождений, первое на баре %d из %d"
                       % (s, int(d.sum()), int(np.flatnonzero(d)[0]), k))
    if bad:
        raise AssertionError(u"ЗАГЛЯДЫВАНИЕ В БУДУЩЕЕ -> " + "; ".join(bad))
    return True


def in_split(times, split):
    t0, t1 = rdata.SPLITS[split]
    return (np.asarray(times) >= t0) & (np.asarray(times) < t1)
