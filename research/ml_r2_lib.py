# -*- coding: utf-8 -*-
u"""Вторая попытка мета-разметки: то, чего в первой не было.

ЗАЧЕМ ВТОРАЯ ПОПЫТКА. Первая (ml_lib/ml_meta/ml_control/ml_verdict) дала
уверенный ноль: прибавка +0.010% ± 0.010% на вход, AUC вне обучения 0.514.
Но у неё есть три места, где отрицательный ответ мог быть следствием
МЕТОДИКИ, а не рынка. Здесь закрывается каждое из трёх.

  1. МАЛО ДАННЫХ. Модель обучалась отдельно на каждой паре (правило,
     таймфрейм): от 392 до 5218 сделок, а с учётом расширяющегося окна первое
     окно обучения — вообще пара сотен. Тридцать признаков на двести
     наблюдений не выучат ничего, и «ноль» будет означать «мало данных».
     Здесь сделки СВАЛИВАЮТСЯ В ОДНУ КУЧУ по правилам, монетам и таймфреймам.
     Вопрос «прибылен ли этот вход» задаётся к обстановке, а не к правилу,
     поэтому такое объединение законно, и оно даёт десятки тысяч строк.

  2. НЕ ТЕ ПРИЗНАКИ. В первой попытке не было ни открытого интереса (файлы
     oi_*.json лежат рядом и не использовались ни разу), ни СОБСТВЕННОЙ
     НЕДАВНЕЙ УДАЧЛИВОСТИ ПРАВИЛА — а это классический признак мета-разметки:
     стратегии живут полосами, и «как это правило торговало последние десять
     сделок» известно на входе и ничего не стоит. Ещё не было поперечного
     среза: что делают остальные монеты в этот час.

  3. НЕ ТА МИШЕНЬ. Метка была «сделка закрылась в плюсе». У трендовых правил
     доля плюсовых 30%: они живут редкими большими выигрышами. Модель,
     поднявшая долю угаданных знаков, легко отрежет именно те хвосты, которые
     кормят кривую. Лопес де Прадо на этот случай прямо предписывает ВЕС
     НАБЛЮДЕНИЯ ПО ВЕЛИЧИНЕ ИСХОДА. Здесь считаются оба варианта: без весов и
     с весом |доходность|, а также вес уникальности (обратная плотность
     одновременно открытых сделок) — сделка, перекрытая десятью другими, не
     является десятью независимыми наблюдениями.

ЧТО НЕ МЕНЯЕТСЯ. Разбиение только по времени, зазор (purge+embargo) не меньше
самого долгого удержания, порог фильтра берётся исключительно из обучающего
куска, опорная линия — «брать все входы подряд», экзамен закрыт.

ПРИЗНАКИ ПРОВЕРЯЮТСЯ ПОРЧЕЙ БУДУЩЕГО так же, как в первой попытке: будущее
после среза заменяется случайным блужданием, и ни одно значение до среза не
имеет права шевельнуться (assert_extra_causal).
"""
import os
import sys
import warnings

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import engine   # noqa: E402
import ind      # noqa: E402
import ml_lib   # noqa: E402
import rdata    # noqa: E402

WARMUP = ml_lib.WARMUP

# признаки первой попытки (их порядок задан ml_lib.FEATS) + новые
EXTRA = [
    "oi_chg24", "oi_chg6", "oi_rank", "s_oi_chg24",
    "xs_rv_med", "xs_ret24_med", "xs_disp", "s_xs_ret24_med", "rel_str",
    "perf10", "perf20_wr", "perf_last", "gap_days", "streak",
]
# «личность» строки: правило, таймфрейм, монета. Отдельным списком, потому что
# половина опытов идёт БЕЗ них — иначе модель научится не отбирать входы, а
# выбирать правило с лучшим средним, что есть тот же перебор, только скрытый.
#
# ВНИМАНИЕ. Сюда напрашивается длительность сделки (t_out - t_in) как мера
# масштаба — и это чистое заглядывание в будущее: на входе неизвестно, сколько
# позиция проживёт. Признака нет и быть не может.
IDENT = ["tf_h", "rule_id", "sym_id"]

FEATS_ALL = list(ml_lib.FEATS) + EXTRA + IDENT

_XS = {}


# --- открытый интерес -------------------------------------------------------

def oi_cols(bars, tf):
    u"""Открытый интерес, известный к закрытию бара. Ни одной точки из будущего.

    rdata.as_of берёт последнее значение СТРОГО раньше момента; момент здесь —
    закрытие бара (t + длина бара), то есть ровно то, что известно, когда
    сигнал уже выдан, но приказ ещё не исполнен.
    """
    n = len(bars.t)
    try:
        ot, ov = rdata.load_oi(bars.symbol)
    except (OSError, ValueError):
        z = np.full(n, np.nan)
        return dict(oi_chg24=z, oi_chg6=z.copy(), oi_rank=z.copy())
    at = bars.t + bars.bar_ms()
    cur = rdata.as_of(ot, ov, at)
    per24 = max(1, int(round(24 * 60 / int(tf))))
    per6 = max(1, per24 // 4)

    def chg(k):
        prev = rdata.as_of(ot, ov, at - k * bars.bar_ms())
        ok = np.isfinite(prev) & (prev > 0)
        out = np.full(n, np.nan)
        out[ok] = cur[ok] / prev[ok] - 1.0
        return out

    return dict(oi_chg24=chg(per24), oi_chg6=chg(per6),
                oi_rank=ind.rolling_rank(cur, 200))


# --- поперечный срез по монетам --------------------------------------------

def _xs_pack(tf):
    u"""Ряды всех пяти монет на общей сетке таймфрейма (по времени НАЧАЛА бара).

    Считается один раз на таймфрейм и кладётся в кэш: пять загрузок на каждую
    конфигурацию — это минуты впустую.
    """
    if tf in _XS:
        return _XS[tf]
    per24 = max(1, int(round(24 * 60 / int(tf))))
    packs = []
    for sym in rdata.SYMBOLS:
        b = rdata.load_bars(sym, tf)
        c = b.c
        prev = np.concatenate([np.full(per24, np.nan), c[:-per24]])
        ret24 = c / np.where(np.isfinite(prev) & (prev > 0), prev, np.nan) - 1.0
        rv = ind.realized_vol(c, 96)
        packs.append((sym, b.t, ret24, rv))
    _XS[tf] = packs
    return packs


def xs_cols(bars, tf):
    u"""Что делают ОСТАЛЬНЫЕ монеты в этот же час.

    Выравнивание — последний бар другой монеты, начавшийся не позже нашего:
    метки времени общие, поэтому это тот же самый бар, закрывающийся в тот же
    момент. Заглядывания нет по той же причине, по какой его нет у BTC в
    первой попытке (ml_lib._btc_aligned).
    """
    n = len(bars.t)
    packs = _xs_pack(tf)
    R, V = [], []
    my_ret = None
    for sym, t, ret24, rv in packs:
        idx = np.searchsorted(t, bars.t, "right") - 1
        ok = idx >= 0
        idx = np.clip(idx, 0, len(t) - 1)
        a = np.full(n, np.nan)
        a[ok] = ret24[idx[ok]]
        b = np.full(n, np.nan)
        b[ok] = rv[idx[ok]]
        R.append(a)
        V.append(b)
        if sym == bars.symbol:
            my_ret = a
    R = np.vstack(R)
    V = np.vstack(V)
    # на самых первых барах истории ни у одной монеты ещё нет значения —
    # это законный NaN «сведений нет», а не ошибка, поэтому предупреждение
    # numpy здесь глушится осознанно и только здесь
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(R, axis=0)
        rvm = np.nanmedian(V, axis=0)
        disp = np.nanstd(R, axis=0)
    return dict(xs_rv_med=rvm, xs_ret24_med=med, xs_disp=disp,
                rel_str=(my_ret - med) if my_ret is not None
                else np.full(n, np.nan))


def build_extra(bars, tf):
    u"""Новые барные признаки одним словарём (без тех, что зависят от сделок)."""
    d = oi_cols(bars, tf)
    d.update(xs_cols(bars, tf))
    return d


def assert_extra_causal(tf="60", symbol="ETHUSDT", cut=0.6, seed=0):
    u"""Порча будущего: ни один новый признак до среза не имеет права измениться."""
    bars = rdata.load_bars(symbol, tf)
    a = build_extra(bars, tf)
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
    b = build_extra(b2, tf)
    bad = []
    for name in sorted(a):
        x, y = a[name][:k], b[name][:k]
        dif = ~np.isclose(x, y, rtol=1e-9, atol=1e-12, equal_nan=True)
        if dif.any():
            bad.append("%s: %d" % (name, int(dif.sum())))
    if bad:
        raise AssertionError(u"НОВЫЙ ПРИЗНАК ЗНАЕТ БУДУЩЕЕ -> " + "; ".join(bad))
    return True


# --- собственная недавняя удачливость правила -------------------------------

def perf_cols(t_in, t_out, ret, bar_ms):
    u"""Как это правило торговало ПЕРЕД этим входом. Только закрытые сделки.

    Для сделки j берутся только те сделки i, что ЗАКРЫЛИСЬ строго раньше, чем
    стал известен сигнал j (то есть раньше t_in[j] - длина бара). Открытая, но
    ещё не закрытая сделка своего исхода не знает, и включать её нельзя —
    именно так в такие признаки и протекает будущее.

    Возвращает: средняя доходность последних 10 закрытых, доля плюсовых среди
    последних 20, доходность последней закрытой, days с её закрытия, длина
    текущей серии одного знака.
    """
    n = len(t_in)
    out = np.full((n, 5), np.nan)
    order = np.argsort(t_out, kind="stable")
    ts = t_out[order]
    rs = ret[order]
    known = t_in - bar_ms
    pos = np.searchsorted(ts, known, "left")     # сколько закрыто строго раньше
    cs = np.concatenate([[0.0], np.cumsum(rs)])
    wins = (rs > 0).astype(np.float64)
    cw = np.concatenate([[0.0], np.cumsum(wins)])
    for j in range(n):
        k = int(pos[j])
        if k <= 0:
            continue
        a = max(0, k - 10)
        out[j, 0] = (cs[k] - cs[a]) / (k - a)
        b = max(0, k - 20)
        out[j, 1] = (cw[k] - cw[b]) / (k - b)
        out[j, 2] = rs[k - 1]
        out[j, 3] = (known[j] - ts[k - 1]) / float(rdata.DAY_MS)
        s, sign = 0, np.sign(rs[k - 1])
        i = k - 1
        while i >= 0 and np.sign(rs[i]) == sign and s < 20:
            s += 1
            i -= 1
        out[j, 4] = s * (1.0 if sign > 0 else -1.0)
    return out


# --- сделки одного правила с полным набором признаков -----------------------

def rule_trades2(name, params, symbol, tf, t0, t1, risk=0.01, causal=False):
    st = ml_lib.registry()[name]
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
    cols = ml_lib.build_features(sub, tf)
    ext = build_extra(sub, tf)
    rows, ys, info = [], [], []
    for tr in res.trades:
        k = tr.i_in - 1
        if k < off - 1 or k < 0:
            continue
        before = tr.equity_after - tr.pnl
        if before <= 1e-9:
            continue
        r = tr.pnl / before
        s = float(tr.side)
        vec = [cols[f][k] for f in ml_lib.FEATS if f in cols]
        vec += [s, s * cols["ema50_d"][k], s * cols["ema200_d"][k],
                s * cols["ret24"][k], s * cols["btc_ret24"][k],
                s * (cols["rsi"][k] - 50.0), s * cols["di_diff"][k]]
        vec += [ext["oi_chg24"][k], ext["oi_chg6"][k], ext["oi_rank"][k],
                s * ext["oi_chg24"][k], ext["xs_rv_med"][k],
                ext["xs_ret24_med"][k], ext["xs_disp"][k],
                s * ext["xs_ret24_med"][k], ext["rel_str"][k]]
        rows.append(vec)
        ys.append(1 if r > 0 else 0)
        info.append((int(tr.t_in), int(tr.t_out), float(r)))
    if not rows:
        return None
    X = np.array(rows, dtype=np.float64)
    t_in = np.array([a[0] for a in info], dtype=np.int64)
    t_out = np.array([a[1] for a in info], dtype=np.int64)
    ret = np.array([a[2] for a in info], dtype=np.float64)
    P = perf_cols(t_in, t_out, ret, sub.bar_ms())
    X = np.hstack([X, P])
    return X, np.array(ys, dtype=np.int8), t_in, t_out, ret


def pooled(rules, tfs=("60", "240"), split="trainval", symbols=None,
           combo="center"):
    u"""Одна большая таблица: все правила, монеты и таймфреймы вместе.

    Сочетание параметров правила — срединное по сетке (детерминированно, ни
    одно не выбрано по результату), либо случайное с заданным зерном.
    """
    t0, t1 = rdata.SPLITS[split]
    rdata.assert_no_test(split)
    symbols = symbols or rdata.SYMBOLS
    reg = ml_lib.registry()
    Xs, ys, tis, tos, rts, cfg, syms = [], [], [], [], [], [], []
    for ri, name in enumerate(rules):
        st = reg[name]
        if combo == "center":
            p = ml_lib.center_combo(st)
        else:
            p = ml_lib.random_combos(st, 1, seed=int(combo))[0]
        for tf in tfs:
            for si, sym in enumerate(symbols):
                r = rule_trades2(name, p, sym, tf, t0, t1)
                if r is None:
                    continue
                X, y, ti, to, rt = r
                ident = np.column_stack([
                    np.full(len(y), int(tf) / 60.0),
                    np.full(len(y), float(ri)),
                    np.full(len(y), float(si))])
                Xs.append(np.hstack([X, ident]))
                ys.append(y)
                tis.append(ti)
                tos.append(to)
                rts.append(rt)
                cfg.extend(["%s|%s" % (name, tf)] * len(y))
                syms.extend([sym] * len(y))
    if not Xs:
        return None
    X = np.vstack(Xs)
    y = np.concatenate(ys)
    t_in = np.concatenate(tis)
    t_out = np.concatenate(tos)
    ret = np.concatenate(rts)
    cfg = np.array(cfg)
    syms = np.array(syms)
    o = np.argsort(t_in, kind="stable")
    return dict(X=X[o], y=y[o], t_in=t_in[o], t_out=t_out[o], ret=ret[o],
                cfg=cfg[o], sym=syms[o], names=list(FEATS_ALL))


# --- веса наблюдений --------------------------------------------------------

def uniqueness(t_in, t_out):
    u"""Вес уникальности: 1 / (сколько сделок жило одновременно с этой).

    Двадцать сделок, открытых в один день и закрытых одним движением рынка, —
    это одно наблюдение, а не двадцать. Без такого веса модель считает, что
    данных у неё в разы больше, чем есть, и уверенно выучивает один-два эпизода.
    """
    edges = np.unique(np.concatenate([t_in, t_out]))
    conc = np.zeros(len(edges) + 1)
    idx0 = np.searchsorted(edges, t_in, "left")
    idx1 = np.searchsorted(edges, t_out, "left")
    for a, b in zip(idx0, idx1):
        conc[a:max(b, a + 1)] += 1.0
    w = np.empty(len(t_in))
    for j, (a, b) in enumerate(zip(idx0, idx1)):
        seg = conc[a:max(b, a + 1)]
        w[j] = float(np.mean(1.0 / np.maximum(seg, 1.0)))
    return w


def make_weights(kind, y, ret, t_in, t_out):
    if kind == u"нет":
        return np.ones(len(ret))
    if kind == u"|доход|":
        w = np.abs(ret)
        return w / max(w.mean(), 1e-12)
    if kind == u"уникальность":
        w = uniqueness(t_in, t_out)
        return w / max(w.mean(), 1e-12)
    if kind == u"|доход|*уник":
        w = np.abs(ret) * uniqueness(t_in, t_out)
        return w / max(w.mean(), 1e-12)
    raise ValueError(kind)


# --- окна с зазором ---------------------------------------------------------

def folds(d, n_folds=5, split="trainval", min_train=500):
    t0, t1 = rdata.SPLITS[split]
    return ml_lib.purged_folds(d["t_in"], d["t_out"], t0, t1,
                               n_folds=n_folds, min_train=min_train)
