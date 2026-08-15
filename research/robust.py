# -*- coding: utf-8 -*-
"""Проверки на прочность: стресс, возмущение параметров, возмущение данных.

Хорошая стратегия отличается от подогнанной не величиной прибыли, а тем, что
с ней происходит при небольшом ухудшении условий. Подогнанная рассыпается от
любого сдвига, потому что жила ровно в той точке, где ей повезло.

Здесь три независимых способа её толкнуть:

  ИЗДЕРЖКИ. Комиссия и проскальзывание умножаются. Если стратегия жива при
  базовых издержках и мертва при полуторных, она не жива — она на границе, а
  реальная биржа стоит дороже бэктеста всегда.

  ПАРАМЕТРЫ. Сдвиг каждого параметра на шаг сетки в обе стороны. Разброс
  результата по соседям важнее самого результата: узкий пик означает, что
  найдено свойство выборки, а не свойство рынка.

  ДАННЫЕ. Сдвиг начала и конца выборки, лёгкий шум в ценах, случайное
  ухудшение исполнения. Проверяет, не держится ли результат на нескольких
  конкретных днях.
"""
import numpy as np

import engine
import metrics
import protocol
import rdata

STRESS = [
    ("база", 1.0, 1.0),
    ("стресс: издержки x1.5, проскальзывание x2", 1.5, 2.0),
    ("предел: издержки x2, проскальзывание x3", 2.0, 3.0),
]


def run_one(st, p, bars, cfg=None, t0=None, t1=None):
    cfg = cfg or engine.Cfg(risk_frac=0.01, max_lev=10.0)
    sig = st.build(bars, p)
    res = engine.run(bars, sig, cfg)
    tape = protocol.Tape(res)
    t0 = t0 if t0 is not None else int(bars.t[0])
    t1 = t1 if t1 is not None else int(bars.t[-1]) + 1
    m = protocol.replay(tape, t0, t1)
    m["_res"] = res
    return m


def cost_stress(st, p, bars, t0=None, t1=None):
    """Как меняется результат при удорожании исполнения."""
    out = []
    for name, fee_k, slip_k in STRESS:
        cfg = engine.Cfg(risk_frac=0.01, max_lev=10.0,
                         fee=rdata.TAKER_FEE * fee_k, slip_mult=slip_k)
        m = run_one(st, p, bars, cfg, t0, t1)
        out.append((name, m["ret"], m["maxdd"], m["trades"], m["mo_med"]))
    return out


def param_perturb(st, p, bars, t0=None, t1=None, max_nb=24):
    """Соседи по сетке: тот же прогон при сдвиге одного параметра на шаг.

    Возвращает список (что сдвинули, доходность). Разброс этого списка и есть
    честная мера того, насколько результат держится на конкретной точке.
    """
    keys = sorted(st.grid)
    base = run_one(st, p, bars, None, t0, t1)["ret"]
    rows = [("исходные", base)]
    nb = protocol.neighbours(p, st.grid, keys)
    if len(nb) > max_nb:
        idx = np.linspace(0, len(nb) - 1, max_nb).astype(int)
        nb = [nb[i] for i in idx]
    for c in nb:
        diff = [k for k in keys if c[k] != p[k]]
        tag = "%s: %s->%s" % (diff[0], p[diff[0]], c[diff[0]]) if diff else "?"
        try:
            rows.append((tag, run_one(st, c, bars, None, t0, t1)["ret"]))
        except Exception:                          # noqa: BLE001
            rows.append((tag, float("nan")))
    vals = np.array([r for _, r in rows[1:]], dtype=np.float64)
    vals = vals[np.isfinite(vals)]
    return dict(base=base, rows=rows,
                median=float(np.median(vals)) if len(vals) else 0.0,
                worst=float(vals.min()) if len(vals) else 0.0,
                share_positive=float((vals > 0).mean()) if len(vals) else 0.0,
                spread=float(vals.max() - vals.min()) if len(vals) else 0.0)


def data_perturb(st, p, bars, n_trials=12, seed=0, noise=0.0008,
                 t0=None, t1=None):
    """Шум в ценах и сдвиг границ выборки.

    Шум ставится в закрытие и тени, но НЕ ломает порядок o<=h, l<=o: иначе
    получились бы невозможные бары, и проверка мерила бы устойчивость к
    невозможному. Величина шума — доли процента, порядка обычного спреда.
    """
    rng = np.random.default_rng(seed)
    out = []
    for k in range(n_trials):
        b2 = rdata.Bars.__new__(rdata.Bars)
        b2.symbol, b2.tf = bars.symbol, bars.tf
        b2.t = bars.t.copy()
        n = len(bars.t)
        e = rng.normal(0, noise, n)
        b2.o = bars.o * (1 + e)
        b2.c = bars.c * (1 + rng.normal(0, noise, n))
        b2.h = np.maximum(np.maximum(b2.o, b2.c), bars.h * (1 + noise))
        b2.l = np.minimum(np.minimum(b2.o, b2.c), bars.l * (1 - noise))
        b2.v = bars.v * (1 + rng.normal(0, 0.05, n))
        b2.turnover = b2.c * b2.v
        try:
            out.append(run_one(st, p, b2, None, t0, t1)["ret"])
        except Exception:                          # noqa: BLE001
            continue
    a = np.array(out, dtype=np.float64)
    a = a[np.isfinite(a)]
    return dict(n=len(a), median=float(np.median(a)) if len(a) else 0.0,
                p5=float(np.percentile(a, 5)) if len(a) else 0.0,
                p95=float(np.percentile(a, 95)) if len(a) else 0.0,
                share_positive=float((a > 0).mean()) if len(a) else 0.0)


def window_shift(st, p, bars, t0, t1, shifts_days=(0, 15, 30, 45, 60, -15, -30)):
    """Сдвиг начала выборки. Результат, держащийся на дате старта, — не результат."""
    out = []
    for s in shifts_days:
        a = t0 + s * rdata.DAY_MS
        if a >= t1 - 90 * rdata.DAY_MS:
            continue
        m = run_one(st, p, bars, None, a, t1)
        out.append((s, m["ret"], m["maxdd"], m["trades"]))
    vals = np.array([r for _, r, _, _ in out]) if out else np.array([])
    return dict(rows=out,
                median=float(np.median(vals)) if len(vals) else 0.0,
                share_positive=float((vals > 0).mean()) if len(vals) else 0.0)


def profit_concentration(res):
    """Что останется, если убрать лучшие сделки.

    Если почти вся прибыль сделана двумя сделками, стратегия не зарабатывает —
    она однажды угадала. Пропустить эти две сделки в жизни проще простого:
    отключился интернет, не хватило маржи, была пауза после убытка.
    """
    pnl = np.array([t.pnl for t in res.trades])
    if len(pnl) < 10:
        return {}
    srt = np.sort(pnl)[::-1]
    total = float(pnl.sum())
    out = {"итого": total}
    for q in (1, 3, 5, 10):
        k = max(1, int(round(len(srt) * q / 100.0)))
        out["без топ-%d%%" % q] = float(srt[k:].sum())
    out["доля топ-5%"] = (float(srt[:max(1, len(srt) // 20)].sum()) / total
                          if total > 0 else 0.0)
    return out


def yearly(res):
    """Разбивка по годам: где стратегия работает, а где нет."""
    import datetime as dt
    by = {}
    eq = {}
    for t in res.trades:
        y = dt.datetime.fromtimestamp(t.t_out / 1000, dt.UTC).year
        before = t.equity_after - t.pnl
        by.setdefault(y, []).append(t.pnl / before if before > 1e-9 else -1.0)
    for y, rs in sorted(by.items()):
        e = 1.0
        for r in rs:
            e *= (1 + r)
        eq[y] = dict(ret=e - 1.0, trades=len(rs),
                     wr=float(np.mean([1 if r > 0 else 0 for r in rs])))
    return eq


def regime_split(res, bars, adx_n=14, adx_thr=25, vol_n=200):
    """Результат по режимам рынка: тренд/боковик, тихо/штормит.

    Режим определяется на баре ВХОДА и только по прошлому — иначе разбивка
    сама станет заглядыванием в будущее и покажет, что стратегия «работает в
    тренде», хотя она просто знала, что тренд случится.
    """
    import ind
    a = ind.adx(bars.h, bars.l, bars.c, adx_n)[0]
    rv = ind.realized_vol(bars.c, 96)
    rank = ind.rolling_rank(np.nan_to_num(rv, nan=0.0), vol_n)
    out = {}
    for t in res.trades:
        i = t.i_in
        if i >= len(a):
            continue
        trend = "тренд" if (np.isfinite(a[i]) and a[i] >= adx_thr) else "боковик"
        vol = "штормит" if (np.isfinite(rank[i]) and rank[i] >= 0.7) else \
              ("тихо" if (np.isfinite(rank[i]) and rank[i] <= 0.3) else "норма")
        key = trend + " / " + vol
        before = t.equity_after - t.pnl
        out.setdefault(key, []).append(t.pnl / before if before > 1e-9 else -1)
    res_out = {}
    for k, rs in sorted(out.items()):
        e = 1.0
        for r in rs:
            e *= (1 + r)
        res_out[k] = dict(ret=e - 1.0, trades=len(rs),
                          wr=float(np.mean([1 if r > 0 else 0 for r in rs])))
    return res_out
