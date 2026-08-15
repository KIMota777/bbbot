# -*- coding: utf-8 -*-
"""Портфель: одна стратегия на всех монетах сразу, с общим капиталом.

ПОЧЕМУ ИМЕННО ТАК, А НЕ «ВЗЯТЬ ЛУЧШУЮ ПАРУ». Перебор дал 610 пар
(стратегия, монета, таймфрейм). Лучшая из 610 хороша уже потому, что она
лучшая из 610 — это отбор, а не свойство. Взять её и торговать значит почти
наверняка получить в жизни заметно меньше.

Портфель устроен иначе: берётся стратегия, которая показала согласие МЕЖДУ
активами, и торгуется на всех пяти одновременно, с общим капиталом и равной
долей риска. Тогда результат не зависит от того, угадали ли мы монету. Он
хуже лучшей пары — и именно поэтому ему можно верить.

ЧТО СЧИТАЕТСЯ ТОЧНО, А ЧТО ПРИБЛИЖЁННО. Доля капитала на сделку берётся от
капитала НА МОМЕНТ ВХОДА, прибыль начисляется в момент выхода. Это точно.
Приближение одно: одновременные позиции по разным монетам считаются
независимыми по марже, тогда как биржа держит общий счёт. Пока суммарное
плечо не упирается в потолок, разницы нет; сколько раз оно упиралось,
считается и печатается отдельно.
"""
import numpy as np

import engine
import metrics
import protocol
import rdata

WARMUP = 600      # баров на прогрев индикаторов перед началом окна


def oos_trades(st, symbol, tf, is_days=360, oos_days=90, anchored=False,
               t_start=None, t_end=None, risk=0.01, max_lev=10.0,
               slip_mult=1.0, fee=None):
    """Сделки ТОЛЬКО из внеобучающих кусков, с параметрами, выбранными по прошлому.

    Возвращает (список сделок, окна). Каждая сделка — доля капитала, поэтому
    её можно масштабировать под любую долю риска в портфеле.
    """
    t0 = t_start if t_start is not None else rdata.SPLITS["trainval"][0]
    t1 = t_end if t_end is not None else rdata.SPLITS["trainval"][1]
    bars = rdata.load_bars(symbol, tf)
    sub, off = bars.slice(t0, t1, warmup=WARMUP)
    cfg = engine.Cfg(risk_frac=risk, max_lev=max_lev, slip_mult=slip_mult,
                     fee=fee if fee is not None else rdata.TAKER_FEE)
    keys = sorted(st.grid)
    tapes = {}
    for p in st.combos():
        try:
            sig = st.build(sub, p)
        except Exception:                          # noqa: BLE001
            continue
        if int((sig.entry != 0).sum()) == 0:
            continue
        res = engine.run(sub, sig, cfg, start_i=off)
        if len(res.trades) < 3:
            continue
        tapes[tuple(p[k] for k in keys)] = protocol.Tape(res)
    if not tapes:
        return [], []
    wf = protocol.walk_forward(tapes, st.grid, is_days, oos_days,
                               anchored=anchored, t_start=t0, t_end=t1)
    out = []
    for f in wf:
        if not f.get("chosen"):
            continue
        tape = tapes[f["chosen_key"]]
        for k in tape.window(f["oos_from"], f["oos_to"]):
            out.append(dict(symbol=symbol, t_in=int(tape.t_in[k]),
                            t_out=int(tape.t_out[k]), ret=float(tape.ret[k]),
                            low=float(tape.eq_low[k]), params=f["chosen"]))
    out.sort(key=lambda x: x["t_in"])
    return out, wf


def combine(trades_by_symbol, risk_each=1.0, start=10000.0, max_concurrent=None):
    """Свести сделки разных монет в одну кривую общего капитала.

    risk_each — во сколько раз доля риска на монету отличается от той, с
    которой считались сделки. Доходность сделки линейна по доле риска, пока
    не упирается в потолок плеча, поэтому масштабирование законно; случаи
    упора считаются отдельно и печатаются.
    """
    events = []
    for sym, trs in trades_by_symbol.items():
        for t in trs:
            events.append((t["t_in"], 0, sym, t))     # 0 — открытие
            events.append((t["t_out"], 1, sym, t))    # 1 — закрытие
    events.sort(key=lambda e: (e[0], e[1]))

    eq = start
    peak = eq
    open_pos = {}
    curve_t, curve_v = [], []
    n_conc, max_conc = 0, 0
    capped = 0
    for ts, kind, sym, t in events:
        if kind == 0:
            base = eq
            if max_concurrent and n_conc >= max_concurrent:
                capped += 1
                continue
            open_pos[id(t)] = base
            n_conc += 1
            max_conc = max(max_conc, n_conc)
            # яма внутри сделки — по худшему плавающему капиталу
            curve_t.append(ts)
            curve_v.append(eq + base * risk_each * (t["low"] - 1.0))
        else:
            base = open_pos.pop(id(t), None)
            if base is None:
                continue
            n_conc -= 1
            eq += base * risk_each * t["ret"]
            eq = max(eq, 0.0)
            curve_t.append(ts)
            curve_v.append(eq)
            peak = max(peak, eq)
            if eq <= 0:
                break
    v = np.array([start] + curve_v) if curve_v else np.array([start])
    days = ((max(curve_t) - min(curve_t)) / rdata.DAY_MS) if curve_t else 1.0
    ret = v[-1] / start - 1.0
    dd = metrics.max_drawdown(v)[0]
    n = sum(len(x) for x in trades_by_symbol.values())
    return dict(
        final=float(v[-1]), ret=float(ret), maxdd=float(dd), trades=n,
        days=days, curve=v, times=curve_t,
        max_concurrent=max_conc, capped=capped,
        mo=float((1 + ret) ** (30.0 / max(days, 1)) - 1.0) if ret > -1 else -1,
        cagr=float((1 + ret) ** (365.0 / max(days, 1)) - 1.0) if ret > -1
        else -1.0)


def monthly_from_curve(times, v):
    """Помесячная доходность портфеля по кривой капитала."""
    import datetime as dt
    if len(times) < 2:
        return []
    out, cur, start_v, prev = [], None, v[0], v[0]
    for ts, val in zip(times, v[1:]):
        d = dt.datetime.fromtimestamp(ts / 1000, dt.UTC)
        key = (d.year, d.month)
        if cur is None:
            cur, start_v = key, prev
        elif key != cur:
            out.append((cur, (prev / start_v - 1.0) if start_v > 0 else 0.0))
            cur, start_v = key, prev
        prev = val
    if cur is not None:
        out.append((cur, (prev / start_v - 1.0) if start_v > 0 else 0.0))
    return out


def monte_carlo_portfolio(trades_by_symbol, risk_each=1.0, start=10000.0,
                          n_sims=10000, seed=0, block=5):
    """Блочный бутстрэп по сделкам портфеля.

    Блоками, а не по одной: убытки идут сериями (рынок неделями против
    стратегии), и перемешивание сделок поодиночке рвёт эти серии, занижая
    просадку — то есть отвечает не на тот вопрос, ради которого всё считалось.
    """
    rets = []
    lows = []
    for trs in trades_by_symbol.values():
        for t in trs:
            rets.append(t["ret"] * risk_each)
            lows.append(1.0 + (t["low"] - 1.0) * risk_each)
    r = np.array(rets)
    lo = np.array(lows)
    n = len(r)
    if n < 20:
        return {}
    rng = np.random.default_rng(seed)
    nb = max(1, n // block)
    dds = np.empty(n_sims)
    fins = np.empty(n_sims)
    ruin = 0
    for s in range(n_sims):
        st = rng.integers(0, max(1, n - block), nb)
        idx = np.concatenate([np.arange(i, i + block) for i in st])[:n]
        idx = np.clip(idx, 0, n - 1)
        eq = start
        peak = start
        worst = 0.0
        for k in idx:
            dip = eq * lo[k]
            if peak > 0:
                worst = max(worst, (peak - dip) / peak)
            eq *= (1.0 + r[k])
            if eq <= 0:
                eq = 0.0
                break
            peak = max(peak, eq)
            worst = max(worst, (peak - eq) / peak)
        dds[s] = worst
        fins[s] = eq
        if eq <= 0:
            ruin += 1
    rr = fins / start - 1.0
    return dict(sims=n_sims,
                dd_p50=float(np.percentile(dds, 50)),
                dd_p95=float(np.percentile(dds, 95)),
                dd_p99=float(np.percentile(dds, 99)),
                p_dd20=float((dds > 0.20).mean()),
                p_dd30=float((dds > 0.30).mean()),
                ret_p5=float(np.percentile(rr, 5)),
                ret_p50=float(np.percentile(rr, 50)),
                ret_p95=float(np.percentile(rr, 95)),
                p_neg=float((rr < 0).mean()), p_ruin=ruin / n_sims)
