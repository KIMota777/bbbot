# -*- coding: utf-8 -*-
"""Метрики, Монте-Карло и поправка на множественную проверку.

Три вещи, которые здесь считаются иначе, чем в прежнем отчёте проекта, и из-за
которых цифры не совпадут со старыми:

1. ПРОСАДКА — по кривой капитала, переоценённой на каждом баре удержания, а не
   по списку закрытых сделок.

2. ШАРП — по дневной доходности капитала, а не по сделкам. Шарп «по сделкам»
   зависит от того, сколько сделок стратегия успела сделать, и растёт от
   дробления одной сделки на две.

3. ДЕФЛИРОВАННЫЙ ШАРП — главная поправка всего исследования. Если перебрать
   тысячу стратегий, лучшая из них покажет высокий Шарп даже на чистом шуме:
   максимум из N случайных величин смещён вверх примерно на sqrt(2*ln N)
   стандартных отклонений. Deflated Sharpe (Bailey, Lopez de Prado, 2014)
   отвечает на вопрос «какова вероятность, что истинный Шарп больше нуля,
   если знать, сколько попыток было сделано». Без этой поправки любой
   результат перебора — иллюзия.
"""
import datetime as dt
import math

import numpy as np
from scipy import stats

DAY_MS = 86_400_000
YEAR_D = 365.0


def daily_equity(res):
    """Разреженную кривую капитала — на дневную сетку, ступенькой вперёд.

    Между отмеченными точками капитал не менялся (позиции не было), поэтому
    перенос последнего известного значения не сглаживает, а восстанавливает.
    """
    if len(res.eq_t) < 2:
        return np.array([]), np.array([])
    t0, t1 = int(res.eq_t[0]), int(res.eq_t[-1])
    grid = np.arange(t0 - t0 % DAY_MS, t1 + DAY_MS, DAY_MS, dtype=np.int64)
    idx = np.searchsorted(res.eq_t, grid, "right") - 1
    idx = np.clip(idx, 0, len(res.eq_v) - 1)
    return grid, res.eq_v[idx]


def max_drawdown(v):
    if len(v) < 2:
        return 0.0, 0, 0
    peak = np.maximum.accumulate(v)
    dd = np.where(peak > 0, (peak - v) / peak, 0.0)
    i = int(np.argmax(dd))
    j = int(np.argmax(v[:i + 1])) if i else 0
    return float(dd[i]), j, i


def recovery_bars(v, i_trough):
    """Сколько точек понадобилось, чтобы вернуться к прежней вершине.

    -1 означает «не вернулась до конца выборки» — это важнее самой глубины:
    просадка, из которой стратегия не вышла, не является просадкой, она
    является убытком.
    """
    if i_trough >= len(v) - 1:
        return -1
    prev_peak = float(np.max(v[:i_trough + 1]))
    after = np.flatnonzero(v[i_trough:] >= prev_peak)
    return int(after[0]) if len(after) else -1


def monthly_table(res):
    g, v = daily_equity(res)
    if not len(g):
        return []
    out, cur, start_v, prev_v = [], None, v[0], v[0]
    for ts, val in zip(g, v):
        d = dt.datetime.utcfromtimestamp(ts / 1000)
        key = (d.year, d.month)
        if cur is None:
            cur, start_v = key, val
        elif key != cur:
            out.append((cur, (prev_v / start_v - 1) if start_v > 0 else 0.0))
            cur, start_v = key, prev_v
        prev_v = val
    if cur is not None:
        out.append((cur, (prev_v / start_v - 1) if start_v > 0 else 0.0))
    return out


def summarize(res, n_trials=1, label=""):
    """Полный набор чисел по одному прогону."""
    tr = res.trades
    g, v = daily_equity(res)
    days = max(1.0, (int(res.eq_t[-1]) - int(res.eq_t[0])) / DAY_MS) \
        if len(res.eq_t) > 1 else 1.0
    ret = res.final_equity / res.start_equity - 1.0
    cagr = ((res.final_equity / res.start_equity) ** (YEAR_D / days) - 1.0) \
        if res.final_equity > 0 and res.start_equity > 0 else -1.0

    if len(v) > 2:
        r = np.diff(v) / np.maximum(v[:-1], 1e-9)
        r = r[np.isfinite(r)]
    else:
        r = np.array([])
    sd = float(r.std(ddof=1)) if len(r) > 2 else 0.0
    mu = float(r.mean()) if len(r) else 0.0
    sharpe = (mu / sd * math.sqrt(YEAR_D)) if sd > 0 else 0.0
    dn = r[r < 0]
    dsd = float(dn.std(ddof=1)) if len(dn) > 2 else 0.0
    sortino = (mu / dsd * math.sqrt(YEAR_D)) if dsd > 0 else 0.0

    # Просадка — по СЫРОЙ кривой, а не по дневной сетке. Дневная сетка
    # оставляет от каждого дня последнюю точку и стирает внутридневную яму:
    # так плавающая просадка выходила МЕНЬШЕ закрытой, чего не бывает.
    dd, _i_peak, i_tr = max_drawdown(res.eq_v)
    rec_pts = recovery_bars(res.eq_v, i_tr)
    rec = -1
    if rec_pts >= 0 and len(res.eq_t) > i_tr + rec_pts:
        rec = int((int(res.eq_t[i_tr + rec_pts]) - int(res.eq_t[i_tr]))
                  / DAY_MS)

    pnls = np.array([t.pnl for t in tr]) if tr else np.array([])
    wins = pnls[pnls > 0]
    loss = pnls[pnls <= 0]
    pf = (float(wins.sum()) / abs(float(loss.sum()))) if len(loss) and \
        loss.sum() != 0 else (float("inf") if len(wins) else 0.0)
    months = monthly_table(res)
    mvals = np.array([m[1] for m in months]) if months else np.array([])

    conc = {}
    if len(pnls):
        srt = np.sort(pnls)[::-1]
        tot = float(pnls.sum())
        for q in (1, 5, 10):
            k = max(1, int(len(srt) * q / 100))
            conc["top%d" % q] = (float(srt[:k].sum()) / tot) if tot > 0 else 0.0
        k5 = max(1, int(len(srt) * 5 / 100))
        conc["ex_top5_total"] = float(srt[k5:].sum())

    streak = worst = 0
    for p in pnls:
        streak = streak + 1 if p <= 0 else 0
        worst = max(worst, streak)

    hold = [t.i_out - t.i_in for t in tr]
    return {
        "label": label, "symbol": res.symbol, "tf": res.tf,
        "trades": len(tr), "ruined": res.ruined,
        "ret": ret, "cagr": cagr, "days": days,
        "final": res.final_equity,
        "sharpe": sharpe, "sortino": sortino,
        "maxdd": dd, "recovery_days": rec,
        "wr": (float((pnls > 0).mean()) if len(pnls) else 0.0),
        "pf": pf,
        "expectancy": float(pnls.mean()) if len(pnls) else 0.0,
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(loss.mean()) if len(loss) else 0.0,
        "payoff": (float(wins.mean()) / abs(float(loss.mean())))
                  if len(wins) and len(loss) and loss.mean() != 0 else 0.0,
        "skew": float(stats.skew(pnls)) if len(pnls) > 3 else 0.0,
        "kurt": float(stats.kurtosis(pnls)) if len(pnls) > 3 else 0.0,
        "avg_hold": float(np.mean(hold)) if hold else 0.0,
        "mo_mean": float(mvals.mean()) if len(mvals) else 0.0,
        "mo_median": float(np.median(mvals)) if len(mvals) else 0.0,
        "mo_std": float(mvals.std(ddof=1)) if len(mvals) > 1 else 0.0,
        "mo_pos": float((mvals > 0).mean()) if len(mvals) else 0.0,
        "mo_worst": float(mvals.min()) if len(mvals) else 0.0,
        "mo_best": float(mvals.max()) if len(mvals) else 0.0,
        "months": len(mvals),
        "worst_streak": worst,
        "conc": conc,
        "fees": float(sum(t.fees for t in tr)),
        "funding": float(sum(t.funding for t in tr)),
        "dsr": deflated_sharpe(r, sharpe, n_trials),
        "n_trials": n_trials,
    }


def deflated_sharpe(daily_ret, sharpe, n_trials):
    """Вероятность, что истинный Шарп > 0, с поправкой на число попыток.

    Порог, который надо перебить, — это ожидаемый максимум Шарпа среди
    n_trials попыток на чистом шуме. Если наблюдённый Шарп ниже этого порога,
    результат объясняется перебором, а не стратегией. Хвосты распределения
    сделок учитываются через асимметрию и эксцесс: у стратегии с редкими
    большими убытками тот же Шарп значит меньше.
    """
    n = len(daily_ret)
    if n < 30 or not np.isfinite(sharpe) or sharpe == 0:
        return 0.0
    g = float(stats.skew(daily_ret))
    k = float(stats.kurtosis(daily_ret, fisher=False))
    m = max(1, int(n_trials))
    e = 0.5772156649
    if m > 1:
        z1 = stats.norm.ppf(1 - 1.0 / m)
        z2 = stats.norm.ppf(1 - 1.0 / (m * math.e))
        sr0 = (1 - e) * z1 + e * z2            # ожидаемый максимум на шуме
    else:
        sr0 = 0.0
    sr_d = sharpe / math.sqrt(YEAR_D)          # обратно к дневному масштабу
    sr0_d = sr0 / math.sqrt(n - 1) if n > 1 else 0.0
    denom = math.sqrt(max(1e-12, 1 - g * sr_d + (k - 1) / 4.0 * sr_d ** 2))
    z = (sr_d - sr0_d) * math.sqrt(n - 1) / denom
    return float(stats.norm.cdf(z))


def monte_carlo(res, n_sims=10000, seed=0, block=1):
    """Бутстрэп по сделкам: во что превращается кривая при другом порядке.

    Блочный бутстрэп (block>1) сохраняет сцепление соседних сделок — серии
    убытков в трендовом рынке идут подряд, и разрушать их значит занижать
    просадку. block=1 — обычный, независимый.
    """
    pnls = np.array([t.pnl for t in res.trades])
    if len(pnls) < 5:
        return {}
    rng = np.random.default_rng(seed)
    n = len(pnls)
    start = res.start_equity
    finals = np.empty(n_sims)
    dds = np.empty(n_sims)
    ruins = 0
    nb = max(1, n // block)
    for s in range(n_sims):
        if block > 1:
            starts = rng.integers(0, max(1, n - block), nb)
            seq = np.concatenate([pnls[i:i + block] for i in starts])[:n]
        else:
            seq = pnls[rng.integers(0, n, n)]
        eq = start + np.cumsum(seq)
        peak = np.maximum.accumulate(np.concatenate([[start], eq]))
        cur = np.concatenate([[start], eq])
        d = np.where(peak > 0, (peak - cur) / peak, 1.0)
        dds[s] = d.max()
        finals[s] = eq[-1]
        if (cur <= 0).any():
            ruins += 1
    yrs = max(res.eq_t[-1] - res.eq_t[0], 1) / DAY_MS / YEAR_D
    rets = finals / start - 1.0
    ann = np.sign(1 + rets) * np.abs(1 + rets) ** (1 / max(yrs, 1e-6)) - 1
    return {
        "sims": n_sims,
        "ret_p5": float(np.percentile(rets, 5)),
        "ret_p25": float(np.percentile(rets, 25)),
        "ret_p50": float(np.percentile(rets, 50)),
        "ret_p75": float(np.percentile(rets, 75)),
        "ret_p95": float(np.percentile(rets, 95)),
        "dd_p50": float(np.percentile(dds, 50)),
        "dd_p95": float(np.percentile(dds, 95)),
        "dd_p99": float(np.percentile(dds, 99)),
        "p_dd_over_20": float((dds > 0.20).mean()),
        "p_dd_over_30": float((dds > 0.30).mean()),
        "p_neg": float((rets < 0).mean()),
        "p_ruin": ruins / n_sims,
        "ann_p5": float(np.percentile(ann, 5)),
        "ann_p50": float(np.percentile(ann, 50)),
    }


def fmt_pct(x):
    return "—" if x is None or not np.isfinite(x) else "%+.1f%%" % (100 * x)


def brief(s):
    return ("%-22s %-9s %3sм | сд %4d | итог %8s | мес %6s | DD %5s | "
            "Ш %5.2f | PF %4.2f | WR %4.0f%%"
            % (s["label"][:22], s["symbol"].replace("USDT", ""), s["tf"],
               s["trades"], fmt_pct(s["ret"]), fmt_pct(s["mo_median"]),
               fmt_pct(s["maxdd"]), s["sharpe"],
               min(s["pf"], 99.9), 100 * s["wr"]))
