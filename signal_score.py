# -*- coding: utf-8 -*-
"""Предиктивный скоринг качества сигнала — «предугадывание похода цены».

Идея: не каждый формально валидный сигнал одинаково хорош. По признакам
сигнального бара (signal_engine2.bar_features, 30 штук) обучаем простую
модель вероятности «сделка закроется тейком» и отсекаем слабые сигналы
через штатный хук движка run_setup(score_fn=..., score_min=...).

ЧЕСТНОСТЬ (главное; без этого работа бессмысленна):
  - обучение и ВЕСЬ подбор (признаки, L2, порог) — только на барах
    [0 .. hold), hold = 72% истории, ровно как в evolution12;
  - HOLDOUT [hold .. n) не участвует ни в обучении, ни в отборе признаков,
    ни в выборе порога — только в финальной оценке;
  - геномы берутся готовые из signal_setups2.json (их отбирал evolution12
    тоже без holdout), скоринг ничего в них не меняет;
  - порог никогда не подбирается по holdout: конфигурация «А» выбирает
    долю отсева блочной кросс-валидацией ВНУТРИ трейна, конфигурация «Б» —
    априорное правило «медиана оценок трейна» (отсев ровно половины).

Никаких внешних библиотек: логистическая регрессия написана здесь на чистом
python (нормализация по статистикам трейна, полный градиентный спуск,
L2-регуляризация). sklearn в системе есть, но модуль от него не зависит —
модель должна считаться на любой машине, где крутится советник.

Публичный API:
  load_data(interval_min)                — свечи + контекст (с кэшем)
  hold_bars_of(n)                        — граница обучения (72% истории)
  load_setups()                          — геномы/плечи из signal_setups2.json
  collect_samples(setup, g, d, rng, lev) — примеры (сделки + признаки)
  train_model(setup, interval_min, hold_bars=None, ...) -> модель (dict)
  make_score_fn(model)                   -> callable(features) -> 0..1
  predict(model, features)               -> 0..1
  save_models(models) / load_models()    — signal_score_models.json
  pooled_stats(trades)                   — n/wr/exp_r/pf/sum_r/ret/dd/tp/stop
  auc(y, p), accuracy(y, p, thr)         — вменяемость модели

Запуск отчёта: python score_report.py
"""

import json
import math
import os

import evolution as ev
import signal_engine2 as se

# ------------------------------------------------------------------ настройки
MODELS_FILE = "signal_score_models.json"
SETUPS_FILE = "signal_setups2.json"
WINNERS_FILE = "evolution12_winners.json"
SYMBOL = "BTCUSDT"
DAYS = 1150
HOLD_FRAC = 0.28          # как в evolution12: holdout = последние 28% баров

MAX_FEATURES = 8          # потолок числа признаков (30 сделок = переобучение)
TRADES_PER_FEATURE = 10   # 1 признак на 10 обучающих сделок
MIN_TRAIN_TRADES = 15     # меньше — модель не обучаем вообще
CORR_MAX = 0.85           # выкидываем признак-дубль (коллинеарность)

L2_GRID = (0.03, 0.1, 0.3, 1.0)   # сила регуляризации, выбирается по OOF
GD_ITERS = 2000                   # шагов градиентного спуска
GD_LR = 0.6                       # шаг (признаки нормированы)
GD_EPS = 1e-7                     # ранняя остановка по норме градиента

CV_FOLDS = 5                      # блочная кросс-валидация внутри трейна
KEEP_GRID = (1.0, 0.9, 0.8, 0.7, 0.6)   # доля оставляемых сигналов
MIN_KEEP_TRADES = 10              # меньше сделок после отсева не рассматриваем
MIN_GAIN_R = 0.10                 # фильтр включаем, только если на CV он даёт
                                  # хотя бы +0.10R к средней сделке

TF_NAMES = {240: "4ч", 60: "1ч"}

_DATA = {}          # кэш (символ, ТФ, дней) -> данные
_C15 = {}           # кэш 15м-свечей


# ------------------------------------------------------------------- данные
def load_data(interval_min, days=DAYS, symbol=SYMBOL):
    """Свечи сигнального ТФ + контекст движка + 15м-исполнение (с кэшем).

    Возвращает dict: c_sig, ctx, c15, ts15, interval_min, n, ts_index."""
    iv = int(interval_min)
    key = (symbol, iv, int(days))
    d = _DATA.get(key)
    if d is not None:
        return d
    ck = (symbol, int(days))
    if ck not in _C15:
        c15 = ev.fetch(symbol, "15", days)
        _C15[ck] = (c15, [c[0] for c in c15])
    c15, ts15 = _C15[ck]
    c_sig = ev.fetch(symbol, str(iv), days)
    ctx = se.prep_context(c_sig, interval_min=iv, symbol=symbol)
    d = dict(c_sig=c_sig, ctx=ctx, c15=c15, ts15=ts15, interval_min=iv,
             n=len(c_sig), ts_index={c[0]: i for i, c in enumerate(c_sig)},
             symbol=symbol, days=int(days))
    _DATA[key] = d
    return d


def hold_bars_of(n, hold_frac=HOLD_FRAC):
    """Граница обучающей части в барах своего ТФ (дословно как evolution12)."""
    return int(int(n) * (1.0 - float(hold_frac)))


def key_of(setup, interval_min):
    return f"{setup}@{int(interval_min)}"


def load_setups(path=SETUPS_FILE):
    """Геномы и плечи победителей: ключ "<сетап>@<ТФ>" -> dict.

    Источник — signal_setups2.json (результат честного отбора evolution12).
    Геном там 33-генный (до v2.2); движок сам подставит дефолты новых генов,
    поэтому поведение сетапа не меняется."""
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    out = {}
    for k, rec in raw.items():
        setup, _, tf = k.rpartition("@")
        if not setup:
            setup, tf = k, str(rec.get("interval_min") or 240)
        if setup not in se.SETUPS:
            continue
        iv = int(rec.get("interval_min") or tf or 240)
        out[key_of(setup, iv)] = dict(
            setup=setup, interval_min=iv, genome=rec["genome"],
            lev=int(rec.get("rec_lev") or 15),
            enabled=bool(rec.get("enabled")),
            verdict=rec.get("verdict"), title=rec.get("title"))
    return out


def hold_from_winners(setup, interval_min, n, path=WINNERS_FILE):
    """Граница обучения из evolution12_winners.json, если длина истории та же.

    Гарантирует БУКВАЛЬНО тот же раскол, на котором отбирались геномы. Если
    файла нет или история изменилась — считаем сами (int(n*0.72))."""
    calc = hold_bars_of(n)
    try:
        with open(path, encoding="utf-8") as fh:
            w = json.load(fh)
        rec = w.get(key_of(setup, interval_min))
        if rec and int(rec.get("n_bars") or 0) == int(n):
            return int(rec["hold_start_bar"]), True
    except (OSError, ValueError, KeyError):
        pass
    return calc, False


# ------------------------------------------------- сбор обучающих примеров
def signal_bars(setup, genome, d, rng):
    """{индекс бара: баров с прошлого сигнала} для всех баров-сигналов.

    Повторяет ровно тот счётчик, что ведёт run_setup: last_sig_i двигается на
    КАЖДОМ баре, прошедшем ворота (даже если сделка потом заблокирована
    занятостью/кулдауном). Границы диапазона берём из результата самого
    движка (r["signal_range"]), поэтому прогрев не дублируем.
    Нужно затем, чтобы признак bars_since_signal при обучении был точно тот
    же, что движок подставит при живом вызове score_fn."""
    a, b = int(rng[0]), int(rng[1])
    c4, ctx = d["c_sig"], d["ctx"]
    ext = se.build_ext(setup, genome, c4, interval_min=d["interval_min"])
    out, last = {}, None
    for i in range(a, b):
        if se.gate_eval(setup, i, c4, ctx, genome, ext)["ok"]:
            out[i] = None if last is None else (i - last)
            last = i
    return out


def collect_samples(setup, genome, d, rng, lev):
    """Обучающие примеры: по каждой сделке отрезка — признаки её сигнального
    бара и исход (1 = тейк, 0 = стоп/таймаут/ликвидация).

    Возвращает (rows, r), где r — полный результат run_setup отрезка."""
    c4, ctx, c15, ts15 = d["c_sig"], d["ctx"], d["c15"], d["ts15"]
    iv = d["interval_min"]
    r = se.run_setup(setup, genome, c4, ctx, c15, ts15, lev,
                     signal_range=tuple(rng), collect_diag=False,
                     interval_min=iv)
    a, b = r["signal_range"]
    bs = signal_bars(setup, genome, d, (a, b))
    ext = se.build_ext(setup, genome, c4, interval_min=iv)
    idx = d["ts_index"]
    rows = []
    for t in r["trades"]:
        i = idx[t["signal_ts"]]
        f = se.bar_features(setup, i, c4, ctx, genome, ext,
                            bars_since_signal=bs.get(i))
        rows.append(dict(i=i, ts=t["signal_ts"], f=f,
                         y=1 if t["reason"] == "tp" else 0,
                         r=float(t["r"]), reason=t["reason"]))
    return rows, r


# ------------------------------------------------------- маленькая математика
def _mean(v):
    return sum(v) / len(v) if v else 0.0


def _std(v, mu=None):
    if len(v) < 2:
        return 0.0
    mu = _mean(v) if mu is None else mu
    return math.sqrt(max(0.0, sum((x - mu) ** 2 for x in v) / (len(v) - 1)))


def _pearson(x, y):
    n = len(x)
    if n < 3:
        return 0.0
    mx, my = _mean(x), _mean(y)
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx <= 0 or sy <= 0:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def _sig(z):
    if z < -35.0:
        return 1e-15
    if z > 35.0:
        return 1.0 - 1e-15
    return 1.0 / (1.0 + math.exp(-z))


def auc(y, p):
    """ROC-AUC (доля правильно упорядоченных пар, ничьи = 0.5).
    None, если один из классов пуст."""
    pos = [pi for pi, yi in zip(p, y) if yi == 1]
    neg = [pi for pi, yi in zip(p, y) if yi == 0]
    if not pos or not neg:
        return None
    good = 0.0
    for a in pos:
        for b in neg:
            good += 1.0 if a > b else (0.5 if a == b else 0.0)
    return good / (len(pos) * len(neg))


def accuracy(y, p, thr=0.5):
    """Доля верных предсказаний при пороге thr."""
    if not y:
        return None
    return sum(1 for yi, pi in zip(y, p)
               if (1 if pi >= thr else 0) == yi) / len(y)


def logloss(y, p):
    if not y:
        return None
    s = 0.0
    for yi, pi in zip(y, p):
        pi = min(max(float(pi), 1e-12), 1 - 1e-12)
        s -= math.log(pi) if yi == 1 else math.log(1 - pi)
    return s / len(y)


def quantile(sorted_vals, q):
    """Простой перцентиль по отсортированному списку (без интерполяции)."""
    if not sorted_vals:
        return 0.0
    k = int(round(q * (len(sorted_vals) - 1)))
    return sorted_vals[min(max(k, 0), len(sorted_vals) - 1)]


# ------------------------------------------------- логистическая регрессия
def fit_logreg(X, y, l2=0.1, iters=GD_ITERS, lr=GD_LR):
    """Полный градиентный спуск по логистической функции потерь.

    X — уже НОРМИРОВАННЫЕ признаки (нормировка по статистикам трейна),
    l2 — L2-штраф (свободный член не штрафуется). Детерминированно:
    старт из нулей, без случайности."""
    n = len(X)
    m = len(X[0]) if n else 0
    w = [0.0] * m
    b = 0.0
    if not n or not m:
        return w, b
    for _ in range(iters):
        gw = [0.0] * m
        gb = 0.0
        for xi, yi in zip(X, y):
            z = b
            for j in range(m):
                z += w[j] * xi[j]
            dz = _sig(z) - yi
            gb += dz
            for j in range(m):
                gw[j] += dz * xi[j]
        gb /= n
        big = abs(gb)
        for j in range(m):
            gw[j] = gw[j] / n + l2 * w[j]
            big = max(big, abs(gw[j]))
        b -= lr * gb
        for j in range(m):
            w[j] -= lr * gw[j]
        if big < GD_EPS:
            break
    return w, b


def _norm_stats(rows, feats):
    """Среднее и СКО признаков по ПОДВЫБОРКЕ (только трейн соответствующего
    шага): нулевое СКО заменяем на 1.0, чтобы не делить на ноль."""
    mu, sd = [], []
    for name in feats:
        col = [r["f"][name] for r in rows]
        m = _mean(col)
        s = _std(col, m)
        mu.append(m)
        sd.append(s if s > 1e-12 else 1.0)
    return mu, sd


def _matrix(rows, feats, mu, sd):
    return [[(r["f"][n] - mu[j]) / sd[j] for j, n in enumerate(feats)]
            for r in rows]


def select_features(rows, k, names=None):
    """Отбор признаков ТОЛЬКО по трейну: сортировка по |корреляции| с исходом,
    жадный набор с выбрасыванием дублей (|corr| между признаками > CORR_MAX).
    Постоянные признаки (СКО = 0) отбрасываются сразу."""
    names = list(names or se.SCORE_FEATURES)
    y = [float(r["y"]) for r in rows]
    cand = []
    cols = {}
    for nm in names:
        col = [r["f"].get(nm, 0.0) for r in rows]
        if _std(col) <= 1e-12:
            continue
        cols[nm] = col
        cand.append((abs(_pearson(col, y)), nm))
    cand.sort(key=lambda t: (-t[0], t[1]))
    out = []
    for c, nm in cand:
        if len(out) >= k:
            break
        if any(abs(_pearson(cols[nm], cols[o])) > CORR_MAX for o in out):
            continue
        out.append(nm)
    return out, {nm: round(c, 3) for c, nm in cand}


def _blocks(n, k):
    """Непрерывные блоки по времени (сделки идут по возрастанию времени):
    случайное перемешивание для рядов нечестно — соседние сделки похожи."""
    k = max(2, min(int(k), n))
    edges = [round(n * i / k) for i in range(k + 1)]
    return [(edges[i], edges[i + 1]) for i in range(k)
            if edges[i + 1] > edges[i]]


def _cv_oof(rows, k_feats, l2, folds=CV_FOLDS):
    """Оценки вне обучения (out-of-fold) блочной CV ВНУТРИ трейна.

    Внутри каждого шага заново делается и отбор признаков, и нормировка, и
    обучение — только по своей части трейна. Так оценка не приукрашена
    отбором признаков «по всем данным»."""
    n = len(rows)
    oof = [None] * n
    for a, b in _blocks(n, folds):
        tr = rows[:a] + rows[b:]
        if len(tr) < max(8, k_feats * 3):
            continue
        if len({r["y"] for r in tr}) < 2:
            continue
        feats, _ = select_features(tr, k_feats)
        if not feats:
            continue
        mu, sd = _norm_stats(tr, feats)
        w, bb = fit_logreg(_matrix(tr, feats, mu, sd),
                           [float(r["y"]) for r in tr], l2=l2)
        for idx in range(a, b):
            f = rows[idx]["f"]
            z = bb + sum(w[j] * (f.get(nm, mu[j]) - mu[j]) / sd[j]
                         for j, nm in enumerate(feats))
            oof[idx] = _sig(z)
    return oof


def _pick_keep_frac(rows, oof):
    """Выбор доли оставляемых сигналов по OOF-оценкам трейна.

    Для каждой доли из KEEP_GRID берём порог = соответствующий перцентиль
    OOF-оценок и считаем среднее R оставшихся сделок. Побеждает лучшая
    средняя R; фильтр включается, только если выигрыш у «оставить всё» не
    меньше MIN_GAIN_R и оставшихся сделок хватает. HOLDOUT здесь не
    участвует ни в каком виде."""
    idx = [i for i, s in enumerate(oof) if s is not None]
    base = [rows[i]["r"] for i in idx]
    rep = dict(n_oof=len(idx), grid=[],
               exp_r_all=round(_mean(base), 3) if base else 0.0)
    if len(idx) < MIN_KEEP_TRADES:
        rep["note"] = "OOF-оценок мало — фильтр выключен"
        return 1.0, rep
    scores = sorted(oof[i] for i in idx)
    best_f, best_exp = 1.0, rep["exp_r_all"]
    for f in KEEP_GRID:
        thr = 0.0 if f >= 1.0 else quantile(scores, 1.0 - f)
        kept = [rows[i]["r"] for i in idx if oof[i] >= thr]
        if not kept:
            continue
        e = _mean(kept)
        rep["grid"].append(dict(keep=f, thr=round(thr, 4), n=len(kept),
                                exp_r=round(e, 3),
                                wr=round(100.0 * sum(
                                    1 for i in idx
                                    if oof[i] >= thr and rows[i]["y"] == 1)
                                    / len(kept), 1)))
        if f >= 1.0 or len(kept) < MIN_KEEP_TRADES:
            continue
        if e > best_exp + (0.0 if best_f < 1.0 else MIN_GAIN_R):
            best_f, best_exp = f, e
    rep["exp_r_kept"] = round(best_exp, 3)
    return best_f, rep


def permutation_auc(rows, k_feats, l2, n_perm=200, seed=7):
    """Нулевое распределение AUC вне обучения (перестановочный тест).

    Зачем: при 25-45 примерах и отборе признаков ВНУТРИ каждого шага CV
    «случайная» модель даёт AUC вне обучения НЕ 0.5, а заметно меньше —
    найденная на своей части выборки случайная связь за её пределами
    переворачивается. Сравнивать реальный AUC с 0.5 в такой схеме
    некорректно, сравнивать надо с этим распределением.

    Возвращает (список AUC на перемешанных исходах, среднее, p-значение
    считает вызывающий)."""
    import random as _r
    rnd = _r.Random(seed)
    y0 = [r["y"] for r in rows]
    out = []
    for _ in range(int(n_perm)):
        y = y0[:]
        rnd.shuffle(y)
        perm = [dict(rows[i], y=y[i]) for i in range(len(rows))]
        oof = _cv_oof(perm, k_feats, l2)
        pairs = [(perm[i]["y"], oof[i]) for i in range(len(perm))
                 if oof[i] is not None]
        if not pairs:
            continue
        a_ = auc([p[0] for p in pairs], [p[1] for p in pairs])
        if a_ is not None:
            out.append(a_)
    return out, (_mean(out) if out else None)


# ---------------------------------------------------------------- обучение
def train_model(setup, interval_min, hold_bars=None, genome=None, lev=None,
                data=None, max_features=MAX_FEATURES, verbose=False,
                n_perm=0):
    """Обучить модель качества сигнала на барах [0 .. hold_bars).

    setup / interval_min — сетап и ТФ сигнального бара (240 или 60);
    hold_bars — граница обучения (None -> из evolution12_winners.json или
    int(n*0.72)); genome / lev — конфиг сетапа (None -> из
    signal_setups2.json); data — результат load_data (None -> загрузим).

    Возвращает модель (обычный dict, сериализуемый в JSON) с полями:
      features / mu / sd / w / b   — сама модель (оценка = сигмоида),
      score_min                    — порог конфигурации «А» (CV),
      score_min_median             — порог конфигурации «Б» (медиана трейна),
      train{...}                   — размер выборки, AUC, отобранные признаки.
    Если обучающих сделок меньше MIN_TRAIN_TRADES, возвращается модель с
    ok=False (её score_fn всегда отдаёт 1.0 — фильтр не действует)."""
    iv = int(interval_min)
    d = data or load_data(iv)
    if genome is None or lev is None:
        cfg = load_setups()[key_of(setup, iv)]
        genome = genome or cfg["genome"]
        lev = lev or cfg["lev"]
    if hold_bars is None:
        hold_bars, _exact = hold_from_winners(setup, iv, d["n"])
    hold_bars = int(hold_bars)

    rows, run = collect_samples(setup, genome, d, (0, hold_bars), lev)
    n = len(rows)
    model = dict(key=key_of(setup, iv), setup=setup, interval_min=iv,
                 lev=int(lev), hold_bars=hold_bars, n_bars=d["n"],
                 features=[], mu=[], sd=[], w=[], b=0.0, l2=None,
                 score_min=0.0, score_min_median=0.0, ok=False,
                 train=dict(n=n, n_tp=sum(r["y"] for r in rows)))
    if n < MIN_TRAIN_TRADES or len({r["y"] for r in rows}) < 2:
        model["train"]["note"] = (
            f"обучающих сделок {n} (< {MIN_TRAIN_TRADES}) или один класс — "
            f"модель не обучалась")
        return model

    k_feats = max(2, min(int(max_features), n // TRADES_PER_FEATURE))

    # --- сила регуляризации: по OOF-логлоссу внутри трейна ---
    best_l2, best_ll, l2_rep = L2_GRID[0], None, []
    oof_best = None
    for l2 in L2_GRID:
        oof = _cv_oof(rows, k_feats, l2)
        pairs = [(rows[i]["y"], oof[i]) for i in range(n)
                 if oof[i] is not None]
        if not pairs:
            continue
        ll = logloss([p[0] for p in pairs], [p[1] for p in pairs])
        a_ = auc([p[0] for p in pairs], [p[1] for p in pairs])
        l2_rep.append(dict(l2=l2, logloss=round(ll, 4),
                           auc=None if a_ is None else round(a_, 3)))
        if best_ll is None or ll < best_ll:
            best_l2, best_ll, oof_best = l2, ll, oof
    if oof_best is None:                       # CV не собралась (мало данных)
        best_l2, oof_best = L2_GRID[1], [None] * n

    # --- финальная модель: отбор признаков и обучение на ВСЁМ трейне ---
    feats, corr = select_features(rows, k_feats)
    mu, sd = _norm_stats(rows, feats)
    w, b = fit_logreg(_matrix(rows, feats, mu, sd),
                      [float(r["y"]) for r in rows], l2=best_l2)
    model.update(features=feats, mu=[round(x, 8) for x in mu],
                 sd=[round(x, 8) for x in sd], w=[round(x, 6) for x in w],
                 b=round(b, 6), l2=best_l2, ok=True)

    # --- пороги: доля отсева с CV (А) и априорная медиана (Б) ---
    keep_f, keep_rep = _pick_keep_frac(rows, oof_best)
    fn = make_score_fn(model)
    ins = sorted(fn(r["f"]) for r in rows)
    model["score_min"] = 0.0 if keep_f >= 1.0 else round(
        quantile(ins, 1.0 - keep_f), 4)
    model["score_min_median"] = round(quantile(ins, 0.5), 4)

    p_ins = [fn(r["f"]) for r in rows]
    y = [r["y"] for r in rows]
    oof_pairs = [(y[i], oof_best[i]) for i in range(n)
                 if oof_best[i] is not None]
    a_oof = (auc([p[0] for p in oof_pairs], [p[1] for p in oof_pairs])
             if oof_pairs else None)
    model["train"].update(
        base_rate=round(_mean([float(v) for v in y]), 3),
        exp_r=round(_mean([r["r"] for r in rows]), 3),
        k_features=k_feats, features=feats,
        corr=[[nm, corr.get(nm)] for nm in feats],
        corr_all=sorted(corr.items(), key=lambda t: -abs(t[1]))[:12],
        l2_grid=l2_rep, l2=best_l2,
        auc_in=round(auc(y, p_ins), 3) if auc(y, p_ins) is not None else None,
        acc_in=round(accuracy(y, p_ins, model["score_min_median"]), 3),
        auc_oof=None if a_oof is None else round(a_oof, 3),
        keep_frac=keep_f, keep=keep_rep,
        signals=run["signals"], reasons_tp=sum(1 for r in rows if r["y"] == 1))
    if n_perm and a_oof is not None:
        null, null_mean = permutation_auc(rows, k_feats, best_l2, n_perm)
        p = ((sum(1 for x in null if x >= a_oof) + 1.0) / (len(null) + 1.0)
             if null else None)
        model["train"]["perm"] = dict(
            n=len(null), null_mean=None if null_mean is None else round(
                null_mean, 3),
            null_hi=round(sorted(null)[int(0.95 * (len(null) - 1))], 3)
            if null else None,
            p_value=None if p is None else round(p, 3))
    if verbose:
        print(f"  {model['key']}: сделок трейна {n}, тейков "
              f"{model['train']['n_tp']}, признаков {len(feats)}: {feats}")
    return model


# ------------------------------------------------------------- применение
def predict(model, features):
    """Оценка 0..1 по словарю признаков (пропущенный признак = среднее
    трейна, то есть нейтральный вклад)."""
    if not model.get("ok"):
        return 1.0
    feats, mu = model["features"], model["mu"]
    sd, w = model["sd"], model["w"]
    z = float(model["b"])
    for j, nm in enumerate(feats):
        v = features.get(nm)
        v = mu[j] if v is None else float(v)
        z += w[j] * (v - mu[j]) / sd[j]
    return _sig(z)


def make_score_fn(model):
    """callable(features) -> 0..1 для run_setup(score_fn=...).

    Замыкание держит только числа — модель можно грузить из json и звать в
    живом советнике. Необученная модель (ok=False) всегда отдаёт 1.0, то
    есть ничего не фильтрует."""
    if not model.get("ok"):
        return lambda f: 1.0
    feats = list(model["features"])
    mu = [float(x) for x in model["mu"]]
    sd = [float(x) if abs(float(x)) > 1e-12 else 1.0 for x in model["sd"]]
    w = [float(x) for x in model["w"]]
    b = float(model["b"])

    def score_fn(features):
        z = b
        for j in range(len(feats)):
            v = features.get(feats[j])
            v = mu[j] if v is None else float(v)
            z += w[j] * (v - mu[j]) / sd[j]
        return _sig(z)
    return score_fn


# ------------------------------------------------------- сохранение/загрузка
def save_models(models, path=MODELS_FILE):
    """Сохранить модели в signal_score_models.json (UTF-8 без BOM)."""
    payload = dict(version=1, hold_frac=HOLD_FRAC, symbol=SYMBOL, days=DAYS,
                   models=models)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    return path


def load_models(path=MODELS_FILE):
    """Модели из json: ключ "<сетап>@<ТФ>" -> модель."""
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload.get("models", payload)


# ------------------------------------------------------------------ метрики
def pooled_stats(trades):
    """Метрики по списку сделок — дословно как evolution12.pooled, чтобы
    цифры сходились с уже опубликованными holdout-результатами."""
    n = len(trades)
    if not n:
        return dict(n=0, wr=0.0, exp_r=0.0, sum_r=0.0, pf=None, dd=0.0,
                    ret=0.0, tp=0, stop=0)
    wins = [t for t in trades if t["pnl"] > 0]
    gp = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    bal, peak, dd = se.START, se.START, 0.0
    for t in sorted(trades, key=lambda x: x["exit_ts"]):
        bal += t["pnl"]
        peak = max(peak, bal)
        dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
    return dict(
        n=n, wr=round(len(wins) / n * 100, 1),
        exp_r=round(sum(t["r"] for t in trades) / n, 3),
        sum_r=round(sum(t["r"] for t in trades), 2),
        pf=round(gp / gl, 2) if gl > 0 else None,
        dd=round(dd * 100, 1),
        ret=round((bal / se.START - 1) * 100, 1),
        tp=sum(1 for t in trades if t["reason"] == "tp"),
        stop=sum(1 for t in trades if t["reason"] == "stop"))


def run_range(setup, genome, d, rng, lev, score_fn=None, score_min=None):
    """Прогон отрезка (сделки + метрики). score_fn/score_min — штатный хук
    движка: модель спрашивается после всех ворот."""
    r = se.run_setup(setup, genome, d["c_sig"], d["ctx"], d["c15"], d["ts15"],
                     lev, signal_range=tuple(rng), collect_diag=False,
                     interval_min=d["interval_min"], score_fn=score_fn,
                     score_min=score_min)
    st = pooled_stats(r["trades"])
    st["blocked_score"] = r.get("blocked_score", 0)
    return r, st


def collect_all(cfgs=None, part="train"):
    """Примеры со ВСЕХ (сетап, ТФ) вместе, отсортированные по времени.

    part="train" — бары [0..hold), part="hold" — [hold..n). Нужно для общей
    (объединённой) модели: по каждому сетапу обучающих сделок 24-43, вместе
    их в разы больше, и это единственный способ проверить, дело в нехватке
    данных или в том, что признаки бара исход просто не предсказывают."""
    cfgs = cfgs or load_setups()
    rows = []
    for k in sorted(cfgs):
        cfg = cfgs[k]
        d = load_data(cfg["interval_min"])
        hold, _ = hold_from_winners(cfg["setup"], cfg["interval_min"], d["n"])
        rng = (0, hold) if part == "train" else (hold, d["n"])
        rs, _r = collect_samples(cfg["setup"], cfg["genome"], d, rng,
                                 cfg["lev"])
        for r in rs:
            r["key"] = k
        rows += rs
    rows.sort(key=lambda r: r["ts"])
    return rows


def train_pooled(cfgs=None, max_features=MAX_FEATURES, n_perm=0):
    """Общая модель на объединённой выборке всех восьми комбинаций.

    Те же правила честности: только бары [0..hold) каждого ТФ."""
    rows = collect_all(cfgs, "train")
    n = len(rows)
    model = dict(key="__pooled__", setup="*", interval_min=0, lev=0,
                 hold_bars=0, n_bars=0, features=[], mu=[], sd=[], w=[],
                 b=0.0, l2=None, score_min=0.0, score_min_median=0.0,
                 ok=False, train=dict(n=n, n_tp=sum(r["y"] for r in rows)))
    if n < MIN_TRAIN_TRADES or len({r["y"] for r in rows}) < 2:
        return model, rows
    k_feats = max(2, min(int(max_features), n // TRADES_PER_FEATURE))
    best_l2, best_ll, oof_best = L2_GRID[0], None, None
    for l2 in L2_GRID:
        oof = _cv_oof(rows, k_feats, l2)
        pr = [(rows[i]["y"], oof[i]) for i in range(n) if oof[i] is not None]
        if not pr:
            continue
        ll = logloss([p[0] for p in pr], [p[1] for p in pr])
        if best_ll is None or ll < best_ll:
            best_l2, best_ll, oof_best = l2, ll, oof
    if oof_best is None:
        best_l2, oof_best = L2_GRID[1], [None] * n
    feats, corr = select_features(rows, k_feats)
    mu, sd = _norm_stats(rows, feats)
    w, b = fit_logreg(_matrix(rows, feats, mu, sd),
                      [float(r["y"]) for r in rows], l2=best_l2)
    model.update(features=feats, mu=[round(x, 8) for x in mu],
                 sd=[round(x, 8) for x in sd], w=[round(x, 6) for x in w],
                 b=round(b, 6), l2=best_l2, ok=True)
    keep_f, keep_rep = _pick_keep_frac(rows, oof_best)
    fn = make_score_fn(model)
    ins = sorted(fn(r["f"]) for r in rows)
    model["score_min"] = 0.0 if keep_f >= 1.0 else round(
        quantile(ins, 1.0 - keep_f), 4)
    model["score_min_median"] = round(quantile(ins, 0.5), 4)
    y = [r["y"] for r in rows]
    p_ins = [fn(r["f"]) for r in rows]
    pr = [(y[i], oof_best[i]) for i in range(n) if oof_best[i] is not None]
    a_oof = (auc([p[0] for p in pr], [p[1] for p in pr]) if pr else None)
    model["train"].update(
        base_rate=round(_mean([float(v) for v in y]), 3),
        exp_r=round(_mean([r["r"] for r in rows]), 3), k_features=k_feats,
        features=feats, corr=[[nm, corr.get(nm)] for nm in feats], l2=best_l2,
        auc_in=round(auc(y, p_ins), 3), auc_oof=None if a_oof is None
        else round(a_oof, 3), keep_frac=keep_f, keep=keep_rep)
    if n_perm and a_oof is not None:
        null, nm_ = permutation_auc(rows, k_feats, best_l2, n_perm)
        p = ((sum(1 for x in null if x >= a_oof) + 1.0) / (len(null) + 1.0)
             if null else None)
        model["train"]["perm"] = dict(
            n=len(null), null_mean=None if nm_ is None else round(nm_, 3),
            p_value=None if p is None else round(p, 3))
    return model, rows


def train_all(keys=None, verbose=True):
    """Обучить модели для всех (сетап, ТФ) из signal_setups2.json."""
    cfgs = load_setups()
    out = {}
    for k in (keys or sorted(cfgs)):
        cfg = cfgs[k]
        out[k] = train_model(cfg["setup"], cfg["interval_min"],
                             genome=cfg["genome"], lev=cfg["lev"],
                             verbose=verbose)
    return out


if __name__ == "__main__":       # быстрая проверка модуля без отчёта
    ms = train_all()
    save_models(ms)
    for k, m in ms.items():
        t = m["train"]
        print(f"{k:20s} обуч.сделок {t['n']:3d} тейков {t['n_tp']:3d} "
              f"AUC(вне обуч.) {t.get('auc_oof')} порог {m['score_min']}")
    print(f"сохранено: {MODELS_FILE}")
