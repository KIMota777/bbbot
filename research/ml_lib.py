# -*- coding: utf-8 -*-
u"""Мета-разметка: общий слой (признаки, сделки, разбиение с зазором).

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Не «найдёт ли модель новую стратегию» — это была бы
та же подгонка, только с большим числом ручек. Проверяется гораздо более
скромное утверждение Лопеса де Прадо: входы базового правила остаются ровно
теми же, а модель отвечает на ОДИН двоичный вопрос — окажется ли конкретный
вход прибыльным. Если модель этого не умеет, никакая надстройка над правилом
не поможет; если умеет — выигрыш обязан быть виден как «то же правило, но с
фильтром лучше, чем без фильтра», на данных, которых модель не видела.

ТРИ МЕСТА, ГДЕ ТАКАЯ ПРОВЕРКА ОБЫЧНО ЛЖЁТ, И ЧТО С НИМИ СДЕЛАНО

1. ПРИЗНАК ЗНАЕТ БУДУЩЕЕ. Матрица признаков строится теми же причинными
   функциями ind.*, что и стратегии, и берётся на баре СИГНАЛА (i_in - 1) —
   том самом, после закрытия которого выдаётся приказ. Проверка не на глаз:
   assert_features_causal портит будущее шумом и требует, чтобы ни одно
   значение до среза не шевельнулось.

2. СДЕЛКИ ПЕРЕТЕКАЮТ ИЗ ОБУЧЕНИЯ В ПРОВЕРКУ. Сделка живёт часами и днями;
   если обучающая сделка закрылась уже внутри проверочного куска, её исход
   определён тем же движением рынка, что и исход проверочных сделок. Поэтому
   между кусками ставится зазор не меньше самого долгого удержания, а
   обучающие сделки, чей выход попал в зазор или дальше, выбрасываются
   (purge), плюс дополнительный карантин после конца обучения (embargo).

3. ПОРОГ ФИЛЬТРА ПОДБИРАЕТСЯ ПО ПРОВЕРКЕ. Порог берётся только из обучающего
   куска (0.5 и медиана обучающих вероятностей) и переносится на проверочный
   без единого взгляда на его исход.

ВЫБОР ПАРАМЕТРОВ БАЗОВОГО ПРАВИЛА. Берётся СРЕДИННОЕ сочетание сетки —
детерминированно, до всякого счёта: keys сортируются, из каждого списка
берётся элемент с номером len//2. Это не «лучшее» сочетание и не может им
быть: его никто не выбирал по результату. Отдельно считается разброс по
восьми случайным сочетаниям с фиксированным зерном — чтобы вывод не держался
на одной точке сетки.
"""
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import engine    # noqa: E402
import ind       # noqa: E402
import rdata     # noqa: E402
import universe  # noqa: E402

WARMUP = 600

FEATS = [
    "atr_pct", "atr_rank", "adx", "di_diff", "rsi", "ema50_d", "ema200_d",
    "relvol", "rv", "bbw", "ret1", "ret6", "ret24", "don_hi_d", "don_lo_d",
    "hour_sin", "hour_cos", "dow", "funding", "funding3", "btc_ret24",
    "btc_above", "btc_rv", "side", "s_ema50_d", "s_ema200_d", "s_ret24",
    "s_btc_ret24", "s_rsi", "s_di_diff",
]

_REG = None


def registry():
    global _REG
    if _REG is None:
        _REG, _ = universe.load()
    return _REG


def center_combo(st):
    u"""Срединное сочетание сетки. Детерминированно, без единого взгляда в счёт."""
    keys = sorted(st.grid)
    return {k: st.grid[k][len(st.grid[k]) // 2] for k in keys}


def random_combos(st, k=8, seed=17):
    rng = np.random.default_rng(seed)
    keys = sorted(st.grid)
    seen, out = set(), []
    for _ in range(200):
        p = {kk: st.grid[kk][int(rng.integers(len(st.grid[kk])))] for kk in keys}
        sig = tuple(p[kk] for kk in keys)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(p)
        if len(out) >= k:
            break
    return out


# --- признаки ---------------------------------------------------------------

def _btc_aligned(bars, tf):
    u"""Ряды BTC, приведённые к сетке символа БЕЗ заглядывания.

    Бар BTC с той же меткой времени закрывается в тот же момент, что и бар
    символа, поэтому берётся последний бар BTC, начавшийся не позже: индекс
    searchsorted(..., 'right') - 1. Для самого BTC это тождественно.
    """
    b = rdata.load_bars("BTCUSDT", tf)
    idx = np.searchsorted(b.t, bars.t, "right") - 1
    ok = idx >= 0
    idx = np.clip(idx, 0, len(b.t) - 1)
    per24 = max(1, int(round(24 * 60 / int(tf))))
    c = b.c
    prev = np.concatenate([np.full(per24, np.nan), c[:-per24]])
    ret24 = c / np.where(np.isfinite(prev) & (prev > 0), prev, np.nan) - 1.0
    e200 = ind.ema(c, 200)
    above = np.where(np.isfinite(e200), (c > e200).astype(float), np.nan)
    rv = ind.realized_vol(c, 96)

    def take(v):
        out = np.full(len(bars.t), np.nan)
        out[ok] = v[idx[ok]]
        return out

    return take(ret24), take(above), take(rv)


def build_features(bars, tf):
    u"""Матрица признаков по барам. Значение строки i известно к закрытию бара i."""
    n = len(bars.t)
    c, h, l, v = bars.c, bars.h, bars.l, bars.v
    a = ind.atr(h, l, c, 14)
    atr_pct = a / np.maximum(c, 1e-12)
    atr_rank = ind.rolling_rank(atr_pct, 200)
    adx_v, pdi, ndi = ind.adx(h, l, c, 14)
    adx = adx_v
    di_diff = pdi - ndi
    rsi = ind.rsi(c, 14)
    e50, e200 = ind.ema(c, 50), ind.ema(c, 200)
    denom = np.where(np.isfinite(a) & (a > 0), a, np.nan)
    ema50_d = (c - e50) / denom
    ema200_d = (c - e200) / denom
    vsma = ind.sma(v, 96)
    relvol = v / np.where(np.isfinite(vsma) & (vsma > 0), vsma, np.nan)
    rv = ind.realized_vol(c, 96)
    bbw = ind.bbands(c, 20, 2.0)[3]

    def ret_n(k):
        prev = np.concatenate([np.full(k, np.nan), c[:-k]])
        return c / np.where(np.isfinite(prev) & (prev > 0), prev, np.nan) - 1.0

    per24 = max(1, int(round(24 * 60 / int(tf))))
    ret1, ret6, ret24 = ret_n(1), ret_n(max(1, per24 // 4)), ret_n(per24)
    dhi, dlo = ind.donchian(h, l, 50)
    don_hi_d = (dhi - c) / denom
    don_lo_d = (c - dlo) / denom

    bar_ms = bars.bar_ms()
    # час и день недели берутся по МОМЕНТУ ИСПОЛНЕНИЯ (открытие следующего
    # бара), потому что именно он и есть время сделки
    tt = (bars.t + bar_ms) / 1000.0
    hod = (tt // 3600) % 24
    dow = ((tt // 86400) + 4) % 7          # 1970-01-01 — четверг
    hour_sin = np.sin(2 * np.pi * hod / 24.0)
    hour_cos = np.cos(2 * np.pi * hod / 24.0)

    ft, fv = rdata.load_funding(bars.symbol)
    funding = rdata.as_of(ft, fv, bars.t + bar_ms)
    idx = np.searchsorted(ft, bars.t + bar_ms, "left") - 1
    f3 = np.full(n, np.nan)
    ok3 = idx >= 2
    if ok3.any():
        cs = np.concatenate([[0.0], np.cumsum(fv)])
        j = idx[ok3]
        f3[ok3] = cs[j + 1] - cs[j - 2]

    btc_ret24, btc_above, btc_rv = _btc_aligned(bars, tf)

    cols = dict(
        atr_pct=atr_pct, atr_rank=atr_rank, adx=adx, rsi=rsi,
        ema50_d=ema50_d, ema200_d=ema200_d, relvol=relvol, rv=rv, bbw=bbw,
        ret1=ret1, ret6=ret6, ret24=ret24, don_hi_d=don_hi_d,
        don_lo_d=don_lo_d, hour_sin=hour_sin, hour_cos=hour_cos, dow=dow,
        funding=funding, funding3=f3, btc_ret24=btc_ret24,
        btc_above=btc_above, btc_rv=btc_rv, di_diff=di_diff,
    )
    return cols


def assert_features_causal(tf="60", symbol="BTCUSDT", cut=0.6, seed=0):
    u"""Та же механическая проверка, что у стратегий, но для матрицы признаков.

    Будущее после среза заменяется своим случайным блужданием; ни один признак
    до среза не имеет права измениться. Ловит скрытую нормировку по всей
    выборке, центрированные окна и сдвиг не в ту сторону.
    """
    bars = rdata.load_bars(symbol, tf)
    a = build_features(bars, tf)
    rng = np.random.default_rng(seed)
    k = int(len(bars.t) * cut)
    m = len(bars.t) - k
    b2 = rdata.Bars.__new__(rdata.Bars)
    b2.symbol, b2.tf = bars.symbol, bars.tf
    b2.t = bars.t.copy()
    for f in ("o", "h", "l", "c", "v", "turnover"):
        setattr(b2, f, getattr(bars, f).copy())
    base = float(bars.c[k - 1])
    walk = base * np.exp(np.cumsum(rng.normal(0, 0.01, m)))
    b2.o[k:] = walk * (1 + rng.normal(0, 0.002, m))
    b2.c[k:] = walk * (1 + rng.normal(0, 0.002, m))
    b2.h[k:] = np.maximum(b2.o[k:], b2.c[k:]) * (1 + rng.uniform(0, .02, m))
    b2.l[k:] = np.minimum(b2.o[k:], b2.c[k:]) * (1 - rng.uniform(0, .02, m))
    b2.v[k:] = bars.v[k:] * rng.uniform(0.2, 5.0, m)
    b2.turnover[k:] = b2.c[k:] * b2.v[k:]
    b = build_features(b2, tf)
    bad = []
    for name in sorted(a):
        x, y = a[name][:k], b[name][:k]
        d = ~np.isclose(x, y, rtol=1e-9, atol=1e-12, equal_nan=True)
        if d.any():
            bad.append("%s: %d, первое на %d" % (name, int(d.sum()),
                                                 int(np.flatnonzero(d)[0])))
    if bad:
        raise AssertionError(u"ПРИЗНАК ЗНАЕТ БУДУЩЕЕ -> " + "; ".join(bad))
    return True


# --- сделки базового правила ------------------------------------------------

def rule_trades(name, params, symbol, tf, t0, t1, risk=0.01, causal=True):
    u"""Все сделки правила на окне и признаки на баре сигнала (i_in - 1).

    Возвращает (X, y, info). y — «сделка закрылась в плюсе». info несёт время
    входа/выхода, доходность в долях капитала и символ.
    """
    st = registry()[name]
    bars = rdata.load_bars(symbol, tf)
    sub, off = bars.slice(t0, t1, warmup=WARMUP)
    if causal:
        engine.assert_causal(lambda bb: st.build(bb, params), sub)
    sig = st.build(sub, params)
    if int((sig.entry != 0).sum()) == 0:
        return None
    res = engine.run(sub, sig, engine.Cfg(risk_frac=risk), start_i=off)
    if not res.trades:
        return None
    cols = build_features(sub, tf)
    rows, ys, info = [], [], []
    for tr in res.trades:
        k = tr.i_in - 1
        if k < off - 1 or k < 0:
            continue
        before = tr.equity_after - tr.pnl
        if before <= 1e-9:
            continue
        ret = tr.pnl / before
        vec = [cols[f][k] for f in FEATS if f in cols]
        vec = vec + [float(tr.side),
                     float(tr.side) * cols["ema50_d"][k],
                     float(tr.side) * cols["ema200_d"][k],
                     float(tr.side) * cols["ret24"][k],
                     float(tr.side) * cols["btc_ret24"][k],
                     float(tr.side) * (cols["rsi"][k] - 50.0),
                     float(tr.side) * cols["di_diff"][k]]
        rows.append(vec)
        ys.append(1 if ret > 0 else 0)
        info.append((int(tr.t_in), int(tr.t_out), float(ret), symbol))
    if not rows:
        return None
    X = np.array(rows, dtype=np.float64)
    y = np.array(ys, dtype=np.int8)
    return X, y, info


def dataset(name, params, tf, t0, t1, symbols=None, causal_first=True):
    u"""Сделки правила по всем монетам, слитые в одну таблицу и упорядоченные по времени."""
    symbols = symbols or rdata.SYMBOLS
    Xs, ys, infos = [], [], []
    for i, sym in enumerate(symbols):
        r = rule_trades(name, params, sym, tf, t0, t1,
                        causal=(causal_first and i == 0))
        if r is None:
            continue
        Xs.append(r[0])
        ys.append(r[1])
        infos.extend(r[2])
    if not Xs:
        return None
    X = np.vstack(Xs)
    y = np.concatenate(ys)
    t_in = np.array([a[0] for a in infos], dtype=np.int64)
    t_out = np.array([a[1] for a in infos], dtype=np.int64)
    ret = np.array([a[2] for a in infos], dtype=np.float64)
    sym = np.array([a[3] for a in infos])
    o = np.argsort(t_in, kind="stable")
    return dict(X=X[o], y=y[o], t_in=t_in[o], t_out=t_out[o], ret=ret[o],
                sym=sym[o], names=list(FEATS))


# --- разбиение по времени с зазором ----------------------------------------

def purged_folds(t_in, t_out, t0, t1, n_folds=5, min_train=250):
    u"""Расширяющееся окно обучения, проверка на следующем куске, зазор между ними.

    Зазор равен САМОМУ ДОЛГОМУ удержанию в выборке — если сделка обучения
    закрылась после начала зазора, её исход уже определён тем же движением
    рынка, что и исходы проверочных сделок, и модель фактически подсматривает.
    """
    hold = (t_out - t_in)
    embargo = int(hold.max()) if len(hold) else 0
    edges = np.linspace(t0, t1, n_folds + 2)
    folds = []
    for k in range(1, n_folds + 1):
        te0, te1 = edges[k], edges[k + 1]
        tr = (t_out <= te0 - embargo)
        te = (t_in >= te0) & (t_in < te1)
        if int(tr.sum()) < min_train or int(te.sum()) < 20:
            continue
        folds.append(dict(train=np.flatnonzero(tr), test=np.flatnonzero(te),
                          te0=float(te0), te1=float(te1),
                          embargo_days=embargo / rdata.DAY_MS))
    return folds


def compound(ret):
    u"""Во что превратился бы капитал на этой последовательности сделок."""
    eq = 1.0
    for r in ret:
        eq *= (1.0 + r)
        if eq <= 0:
            return 0.0
    return eq


def monthly_rate(ret, t_in, t_out):
    if len(ret) == 0:
        return 0.0
    days = (float(t_out.max()) - float(t_in.min())) / rdata.DAY_MS
    eq = compound(ret)
    if eq <= 0 or days <= 0:
        return -1.0
    return eq ** (30.0 / days) - 1.0
