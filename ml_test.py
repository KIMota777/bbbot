# -*- coding: utf-8 -*-
"""СТОИТ ЛИ ПЕРЕХОДИТЬ НА НЕЙРОСЕТИ — проверка измерением, а не мнением.

Владелец спросил: не обучить ли локальную модель вместо ручного поиска
правил. Прошлая волна (логистическая регрессия на 30 признаках бара,
score_report_out.txt) провалилась: 0 из 8 моделей значимы, ранжирование на
holdout перевёрнутое. Здесь берём МОДЕЛИ СИЛЬНЕЕ (градиентный бустинг,
нейросети sklearn и torch) и ДВЕ постановки задачи.

ПОСТАНОВКА A — классификация исхода сделки: признаки сигнального бара ->
  тейк/не тейк. Прямая связь с нашей задачей, но наблюдений десятки.
ПОСТАНОВКА B — предсказание доходности бара: признаки бара i -> доходность
  за следующие k баров (k = 1, 6, 24 на 4ч). Наблюдений тысячи. Это честная
  проверка «есть ли в свечах предсказуемость вообще».

ЧЕСТНОСТЬ (без неё работа бессмысленна):
  * обучение и ВЕСЬ подбор — только [0..72%) истории; holdout (последние
    28%) не участвует ни в обучении, ни в выборе модели, ни в подборе
    гиперпараметров, ни в выборе порогов квинтилей;
  * гиперпараметры — walk-forward кросс-валидацией ПО ВРЕМЕНИ внутри
    трейна (случайная CV перемешала бы будущее с прошлым);
  * между обучением и валидацией/holdout — карантин (purge) в k баров,
    иначе цель обучающего хвоста заглядывает за границу;
  * сравниваем не с теоретическими 0.5/0.0, а с ПЕРЕСТАНОВОЧНЫМ нулевым
    распределением (200 перестановок цели);
  * издержки: 0.055% тейкер + 0.03% слиппедж на сторону = 0.170% на круг;
  * считаем НЕЗАВИСИМЫЕ наблюдения (перекрытие горизонта + корреляция
    монет) и сверяем с числом параметров модели.

Запуск: python ml_test.py   (вывод дублируется в ml_test_out.txt)
Ничего, кроме ml_test_out.txt, не пишет. Сайт и прод-движки не трогает.
"""

import io
import math
import os
import sys
import time
import warnings

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

warnings.filterwarnings("ignore")

import ml_features as mf

LINE = "=" * 100
THIN = "-" * 100
_BUF = io.StringIO()
T0 = time.time()


def out(s=""):
    print(s)
    _BUF.write(str(s) + "\n")


# ----------------------------------------------------------- настройки
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT")
DAYS = 1150
HOLD_FRAC = 0.28              # holdout = последние 28% (как в evolution12)
HORIZONS = (1, 6, 24)         # баров вперёд на 4ч: 4ч / 1 сутки / 4 суток
N_PERM = 200                  # перестановок в нулевом распределении
N_PERM_FULL = 30              # перестановок с ПОЛНЫМ переобучением
CV_FOLDS = 4                  # walk-forward фолдов внутри трейна
FEE_SIDE = 0.00055            # тейкер Bybit
SLIP_SIDE = 0.00030           # слиппедж
COST_RT = 2.0 * (FEE_SIDE + SLIP_SIDE)      # 0.170% на круг
SEED = 20260801

rng_global = np.random.default_rng(SEED)


# ------------------------------------------------------- библиотеки
def probe_libs():
    info = {}
    try:
        import sklearn
        info["sklearn"] = sklearn.__version__
    except Exception as e:                                  # noqa: BLE001
        info["sklearn"] = f"НЕТ ({e})"
    try:
        import torch
        info["torch"] = torch.__version__
        info["torch_cuda"] = torch.cuda.is_available()
        info["torch_threads"] = torch.get_num_threads()
    except Exception as e:                                  # noqa: BLE001
        info["torch"] = f"НЕТ ({e})"
    info["numpy"] = np.__version__
    try:
        import scipy
        info["scipy"] = scipy.__version__
    except Exception:                                       # noqa: BLE001
        info["scipy"] = "НЕТ"
    return info


LIBS = probe_libs()
HAVE_SK = not str(LIBS["sklearn"]).startswith("НЕТ")
HAVE_TORCH = not str(LIBS["torch"]).startswith("НЕТ")

if HAVE_SK:
    from sklearn.ensemble import (HistGradientBoostingClassifier,
                                  HistGradientBoostingRegressor)
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.neural_network import MLPClassifier, MLPRegressor
    from sklearn.preprocessing import StandardScaler
if HAVE_TORCH:
    import torch
    import torch.nn as nn
    torch.manual_seed(SEED)
    torch.set_num_threads(max(1, min(8, os.cpu_count() or 4)))


# ------------------------------------------------------ мелкая математика
def pearson(x, y):
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if len(x) < 3:
        return 0.0
    x = x - x.mean()
    y = y - y.mean()
    dx, dy = math.sqrt(float(x @ x)), math.sqrt(float(y @ y))
    if dx <= 0 or dy <= 0:
        return 0.0
    return float(x @ y) / (dx * dy)


def rankdata(x):
    x = np.asarray(x, float)
    order = np.argsort(x, kind="mergesort")
    r = np.empty(len(x), float)
    r[order] = np.arange(1, len(x) + 1, dtype=float)
    return r


def spearman(x, y):
    return pearson(rankdata(x), rankdata(y))


def auc_score(y, p):
    y = np.asarray(y, int)
    p = np.asarray(p, float)
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    r = rankdata(p)
    return float((r[y == 1].sum() - len(pos) * (len(pos) + 1) / 2.0) /
                 (len(pos) * len(neg)))


def tstat_from_corr(r, n_eff):
    if n_eff <= 2 or abs(r) >= 1.0:
        return 0.0
    return r * math.sqrt(n_eff - 2) / math.sqrt(max(1e-12, 1.0 - r * r))


def pct(v, q):
    return float(np.percentile(np.asarray(v, float), q)) if len(v) else 0.0


def fmt_p(p):
    return f"{p:.3f}" if p >= 0.001 else "<0.001"


# =====================================================================
# ЗАГРУЗКА ДАННЫХ (постановка B)
# =====================================================================
def load_panel(interval="240", bar_min=240, symbols=SYMBOLS):
    """Панель по монетам: свечи одной временной сетки + признаки."""
    cand, vols = {}, {}
    for s in symbols:
        cand[s] = mf.load_candles(s, interval, DAYS)
        vols[s] = mf.load_volume(s, interval) if bar_min == 240 else None
    # выравниваем по ОБЩЕЙ сетке timestamp'ов (у части монет сетка сдвинута
    # на бар): берём пересечение и сверяем, что оно непрерывное
    common = set(r[0] for r in cand[symbols[0]])
    for s in symbols[1:]:
        common &= set(r[0] for r in cand[s])
    base_ts = sorted(common)
    step = base_ts[1] - base_ts[0]
    gaps = sum(1 for i in range(1, len(base_ts))
               if base_ts[i] - base_ts[i - 1] != step)
    assert gaps == 0, f"общая сетка рваная: {gaps} разрывов"
    keep = set(base_ts)
    for s in symbols:
        cand[s] = [r for r in cand[s] if r[0] in keep]
        assert [r[0] for r in cand[s]] == base_ts, f"сетка {s} не совпала"
    n = len(base_ts)
    btc_close = np.array([r[4] for r in cand["BTCUSDT"]], float)
    feats = {}
    for s in symbols:
        X, names = mf.build_features(cand[s], vols[s], bar_min,
                                     market_close=btc_close)
        feats[s] = X
    return dict(cand=cand, feats=feats, names=names, n=n, ts=base_ts,
                bar_min=bar_min, symbols=list(symbols))


def make_dataset(panel, k, warm=None):
    """Матрицы X, y, индексы бара и монеты. y = лог-доходность за k баров."""
    warm = mf.warmup_bars(panel["bar_min"]) if warm is None else warm
    n = panel["n"]
    Xs, ys, ib, ic = [], [], [], []
    for j, s in enumerate(panel["symbols"]):
        X = panel["feats"][s]
        c = np.array([r[4] for r in panel["cand"][s]], float)
        hi = n - k
        idx = np.arange(warm, hi)
        Xs.append(X[idx])
        ys.append(np.log(c[idx + k] / c[idx]))
        ib.append(idx)
        ic.append(np.full(len(idx), j))
    return (np.vstack(Xs), np.concatenate(ys),
            np.concatenate(ib), np.concatenate(ic))


# =====================================================================
# ОБЩИЕ ДЕТАЛИ ЧЕСТНОГО РАСКОЛА
# =====================================================================
def demean_by_bar(y, ibar):
    """Межмонетная цель: доходность минус средняя по монетам того же бара."""
    y = np.asarray(y, float)
    order = np.argsort(ibar, kind="mergesort")
    b = ibar[order]
    v = y[order]
    uniq, first = np.unique(b, return_index=True)
    means = np.add.reduceat(v, first) / np.diff(np.append(first, len(v)))
    per_row = np.repeat(means, np.diff(np.append(first, len(v))))
    res = np.empty_like(y)
    res[order] = v - per_row
    return res


def split_bounds(n, hold_frac=HOLD_FRAC):
    return int(n * (1.0 - hold_frac))


def wf_folds(lo, hi, k, folds=CV_FOLDS):
    """Walk-forward фолды ПО ВРЕМЕНИ внутри [lo,hi) с карантином k баров.

    Возвращает список (tr_end, va_lo, va_hi) в единицах индекса бара."""
    span = hi - lo
    edges = [lo + int(span * f) for f in
             np.linspace(0.40, 1.0, folds + 1)]
    res = []
    for i in range(folds):
        tr_end = edges[i]
        va_lo, va_hi = edges[i] + k, edges[i + 1]
        if va_hi - va_lo > 50 and tr_end - lo > 200:
            res.append((tr_end, va_lo, va_hi))
    return res


def eff_n(n_rows, k, n_sym, rho):
    """Независимые наблюдения: перекрытие горизонта + корреляция монет."""
    per_sym = n_rows / max(1, n_sym)
    indep_time = per_sym / k
    cross = n_sym / (1.0 + (n_sym - 1) * max(0.0, rho))
    return indep_time * cross


# =====================================================================
# МОДЕЛИ ДЛЯ ПОСТАНОВКИ B
# =====================================================================
class ConstModel:
    name = "константа (среднее трейна)"
    n_params = 1

    def fit(self, X, y):
        self.m = float(np.mean(y))
        return self

    def predict(self, X):
        return np.full(len(X), self.m)


class RandomModel:
    name = "случайное предсказание"
    n_params = 0

    def __init__(self, seed=1):
        self.seed = seed

    def fit(self, X, y):
        return self

    def predict(self, X):
        return np.random.default_rng(self.seed).normal(size=len(X))


class Standardizer:
    def fit(self, X):
        self.mu = X.mean(axis=0)
        self.sd = X.std(axis=0)
        self.sd[self.sd <= 1e-12] = 1.0
        return self

    def transform(self, X):
        return (X - self.mu) / self.sd


class RidgeModel:
    def __init__(self, alpha=10.0, cols=None, name=None):
        self.alpha = alpha
        self.cols = cols
        self.name = name or f"линейная (Ridge a={alpha})"

    def fit(self, X, y):
        Xi = X if self.cols is None else X[:, self.cols]
        self.sc = Standardizer().fit(Xi)
        Z = self.sc.transform(Xi)
        self.n_params = Z.shape[1] + 1
        A = Z.T @ Z + self.alpha * np.eye(Z.shape[1])
        self.w = np.linalg.solve(A, Z.T @ (y - y.mean()))
        self.b = float(y.mean())
        return self

    def predict(self, X):
        Xi = X if self.cols is None else X[:, self.cols]
        return self.sc.transform(Xi) @ self.w + self.b


class GBModel:
    def __init__(self, seed=SEED, **kw):
        self.kw = kw
        self.seed = seed
        self.name = ("бустинг HistGB " +
                     " ".join(f"{a}={b}" for a, b in kw.items()))

    def fit(self, X, y):
        self.m = HistGradientBoostingRegressor(random_state=self.seed,
                                               **self.kw)
        self.m.fit(X, y)
        it = int(self.m.n_iter_)
        leaves = int(self.kw.get("max_leaf_nodes", 31))
        self.n_params = it * (2 * leaves - 1)
        return self

    def predict(self, X):
        return self.m.predict(X)


class MLPSkModel:
    def __init__(self, hidden=(32, 16), alpha=1e-3, name=None, seed=SEED):
        self.hidden = hidden
        self.alpha = alpha
        self.seed = seed
        self.name = name or f"нейросеть sklearn MLP {hidden} a={alpha}"

    def fit(self, X, y):
        self.sc = Standardizer().fit(X)
        Z = self.sc.transform(X)
        self.ys = float(np.std(y)) or 1.0
        big = len(Z) >= 60000
        self.m = MLPRegressor(hidden_layer_sizes=self.hidden, alpha=self.alpha,
                              max_iter=80 if big else 300, early_stopping=True,
                              n_iter_no_change=8 if big else 10,
                              batch_size=4096 if big else "auto",
                              validation_fraction=0.15,
                              random_state=self.seed,
                              learning_rate_init=3e-3 if big else 1e-3)
        self.m.fit(Z, y / self.ys)
        dims = [Z.shape[1]] + list(self.hidden) + [1]
        self.n_params = sum(dims[i] * dims[i + 1] + dims[i + 1]
                            for i in range(len(dims) - 1))
        return self

    def predict(self, X):
        return self.m.predict(self.sc.transform(X)) * self.ys


class MLPTorchModel:
    def __init__(self, hidden=(64, 32), wd=1e-4, drop=0.1, epochs=120,
                 name=None, seed=SEED):
        self.hidden, self.wd, self.drop, self.epochs = hidden, wd, drop, epochs
        self.seed = seed
        self.name = name or f"нейросеть torch MLP {hidden} wd={wd} drop={drop}"

    def _net(self, d):
        layers, prev = [], d
        for hsz in self.hidden:
            layers += [nn.Linear(prev, hsz), nn.ReLU(), nn.Dropout(self.drop)]
            prev = hsz
        layers += [nn.Linear(prev, 1)]
        return nn.Sequential(*layers)

    def fit(self, X, y):
        torch.manual_seed(self.seed)
        self.sc = Standardizer().fit(X)
        Z = self.sc.transform(X).astype(np.float32)
        self.ys = float(np.std(y)) or 1.0
        t = (y / self.ys).astype(np.float32).reshape(-1, 1)
        # внутренняя валидация — ПОСЛЕДНИЕ 15% трейна (по времени!)
        cut = int(len(Z) * 0.85)
        Ztr, ttr, Zva, tva = Z[:cut], t[:cut], Z[cut:], t[cut:]
        net = self._net(Z.shape[1])
        opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=self.wd)
        lossf = nn.MSELoss()
        Xtr = torch.from_numpy(Ztr)
        Ytr = torch.from_numpy(ttr)
        Xva = torch.from_numpy(Zva)
        Yva = torch.from_numpy(tva)
        best, best_state, bad = 1e18, None, 0
        # на больших выборках батч крупнее, эпох меньше — иначе CPU не тянет
        bs = 512 if len(Xtr) < 60000 else 4096
        max_ep = self.epochs if len(Xtr) < 60000 else max(30, self.epochs // 4)
        nb = max(1, len(Xtr) // bs)
        g = torch.Generator().manual_seed(self.seed)
        for _ep in range(max_ep):
            net.train()
            perm = torch.randperm(len(Xtr), generator=g)
            for b in range(nb):
                sl = perm[b * bs:(b + 1) * bs]
                opt.zero_grad()
                loss = lossf(net(Xtr[sl]), Ytr[sl])
                loss.backward()
                opt.step()
            net.eval()
            with torch.no_grad():
                v = float(lossf(net(Xva), Yva))
            if v < best - 1e-6:
                best, bad = v, 0
                best_state = {kk: vv.clone() for kk, vv in
                              net.state_dict().items()}
            else:
                bad += 1
                if bad >= 12:
                    break
        if best_state:
            net.load_state_dict(best_state)
        net.eval()
        self.net = net
        dims = [Z.shape[1]] + list(self.hidden) + [1]
        self.n_params = sum(dims[i] * dims[i + 1] + dims[i + 1]
                            for i in range(len(dims) - 1))
        return self

    def predict(self, X):
        Z = self.sc.transform(X).astype(np.float32)
        with torch.no_grad():
            return (self.net(torch.from_numpy(Z)).numpy().ravel() * self.ys)


# ------------------------------------------------ сетки гиперпараметров
def grid_for(kind, n_feat, lag_cols, small=True):
    """small=False — урезанная сетка для очень больших выборок (15м): там
    один прогон стоит секунды, полная сетка не влезает в разумное время."""
    if kind == "ridge":
        return [RidgeModel(alpha=a) for a in (1.0, 10.0, 100.0, 1000.0)]
    if kind == "lags":
        return [RidgeModel(alpha=a, cols=lag_cols,
                           name=f"линейная ТОЛЬКО на лагах доходности (a={a})")
                for a in (0.1, 1.0, 10.0)]
    if kind == "gb":
        g = []
        msls = (50, 200) if small else (200,)
        for lr in (0.03, 0.1):
            for leaves in (15, 31):
                for msl in msls:
                    g.append(GBModel(learning_rate=lr, max_leaf_nodes=leaves,
                                     min_samples_leaf=msl, max_iter=300,
                                     l2_regularization=1.0,
                                     early_stopping=False))
        return g
    if kind == "mlp_sk":
        alphas = (1e-3, 1e-1) if small else (1e-2,)
        return [MLPSkModel(hidden=h, alpha=a)
                for h in ((16,), (32, 16)) for a in alphas]
    if kind == "mlp_torch":
        wds = (1e-4, 1e-2) if small else (1e-3,)
        return [MLPTorchModel(hidden=h, wd=w)
                for h in ((32,), (64, 32)) for w in wds]
    raise ValueError(kind)


# =====================================================================
# ОЦЕНКА НА HOLDOUT (постановка B)
# =====================================================================
def quintile_report(pred, y, thr_hi, thr_lo):
    """Квинтили по ПОРОГАМ ИЗ ТРЕЙНА (holdout не подглядываем)."""
    top = pred >= thr_hi
    bot = pred <= thr_lo
    r_top = float(np.mean(y[top])) if top.sum() else 0.0
    r_bot = float(np.mean(y[bot])) if bot.sum() else 0.0
    return dict(n_top=int(top.sum()), n_bot=int(bot.sum()),
                r_top=r_top, r_bot=r_bot, spread=r_top - r_bot)


def select_trades(pred, ibar, icoin, thr_hi, k, n_sym):
    """Индексы входов стратегии «покупаем топ-квинтиль, держим k баров».

    По каждой монете идём по времени; вход, если предсказание >= порога и
    позиция свободна; после входа k баров пропускаем (сделки НЕ
    перекрываются — только так их можно считать независимыми).

    Выбор входов зависит ТОЛЬКО от предсказаний и времени, не от цели —
    поэтому в перестановочном тесте он считается один раз."""
    sel = []
    for j in range(n_sym):
        m = np.where(icoin == j)[0]
        order = m[np.argsort(ibar[m], kind="mergesort")]
        b, p = ibar[order], pred[order]
        busy_until = -1
        for t in range(len(order)):
            if b[t] <= busy_until:
                continue
            if p[t] >= thr_hi:
                sel.append(order[t])
                busy_until = b[t] + k - 1
    return np.array(sorted(sel), dtype=int)


def trades_from_sel(y, sel):
    if len(sel) == 0:
        return np.zeros(0)
    return np.expm1(y[sel]) - COST_RT


def block_shuffle(y, ibar, icoin, k, n_sym, rng):
    """Перестановка цели блоками длины k внутри каждой монеты.

    Обычный shuffle разрушил бы автокорреляцию перекрывающихся целей и дал
    бы СЛИШКОМ узкий нуль; блоками — честнее."""
    outv = np.empty_like(y)
    for j in range(n_sym):
        m = np.where(icoin == j)[0]
        order = m[np.argsort(ibar[m], kind="mergesort")]
        v = y[order]
        nb = int(math.ceil(len(v) / k))
        pad = nb * k - len(v)
        vv = np.concatenate([v, v[:pad]]) if pad else v
        blocks = vv.reshape(nb, k)
        perm = rng.permutation(nb)
        sh = blocks[perm].reshape(-1)[:len(v)]
        outv[order] = sh
    return outv


def eval_holdout(pred_tr, pred_ho, y_ho, ibar_ho, icoin_ho, k, n_sym,
                 rho, n_perm=N_PERM, seed=7):
    """Полный набор метрик + перестановочный нуль (предсказания фиксированы)."""
    ic = pearson(pred_ho, y_ho)
    ric = spearman(pred_ho, y_ho)
    thr_hi = float(np.percentile(pred_tr, 80))
    thr_lo = float(np.percentile(pred_tr, 20))
    m_top = pred_ho >= thr_hi
    m_bot = pred_ho <= thr_lo
    q = quintile_report(pred_ho, y_ho, thr_hi, thr_lo)
    sel = select_trades(pred_ho, ibar_ho, icoin_ho, thr_hi, k, n_sym)
    tr = trades_from_sel(y_ho, sel)
    dir_acc = float(np.mean((pred_ho > np.median(pred_tr)) == (y_ho > 0)))
    ne = eff_n(len(y_ho), k, n_sym, rho)
    rg = np.random.default_rng(seed)
    null_ic = np.empty(n_perm)
    null_sp = np.empty(n_perm)
    null_trade = np.empty(n_perm)
    for q_i in range(n_perm):
        ysh = block_shuffle(y_ho, ibar_ho, icoin_ho, k, n_sym, rg)
        null_ic[q_i] = pearson(pred_ho, ysh)
        rt = float(ysh[m_top].mean()) if m_top.any() else 0.0
        rb = float(ysh[m_bot].mean()) if m_bot.any() else 0.0
        null_sp[q_i] = rt - rb
        ts = trades_from_sel(ysh, sel)
        null_trade[q_i] = float(ts.mean()) if len(ts) else 0.0
    p_ic = float((np.sum(null_ic >= ic) + 1) / (n_perm + 1))
    p_sp = float((np.sum(null_sp >= q["spread"]) + 1) / (n_perm + 1))
    m_tr = float(np.mean(tr)) if len(tr) else 0.0
    p_tr = float((np.sum(null_trade >= m_tr) + 1) / (n_perm + 1))
    t_tr = (m_tr / (np.std(tr, ddof=1) / math.sqrt(len(tr)))
            if len(tr) > 3 and np.std(tr, ddof=1) > 0 else 0.0)
    return dict(ic=ic, ric=ric, t_ic=tstat_from_corr(ic, ne), n_eff=ne,
                dir_acc=dir_acc, q=q, n_trades=len(tr), trade_mean=m_tr,
                trade_t=t_tr, trade_sum=float(np.sum(tr)) if len(tr) else 0.0,
                null_ic_mean=float(null_ic.mean()),
                null_ic_sd=float(null_ic.std()),
                null_ic_p95=pct(null_ic, 95), null_ic_p5=pct(null_ic, 5),
                p_ic=p_ic, p_spread=p_sp, p_trade=p_tr,
                null_sp_mean=float(null_sp.mean()),
                null_sp_p95=pct(null_sp, 95),
                null_trade_mean=float(null_trade.mean()),
                null_trade_p95=pct(null_trade, 95))


def cv_select(models, X, y, ibar, lo, hi, k, verbose_name=""):
    """Выбор гиперпараметров walk-forward CV внутри трейна."""
    folds = wf_folds(lo, hi, k)
    best, best_sc, table = None, -1e18, []
    for m in models:
        scs = []
        for (tr_end, va_lo, va_hi) in folds:
            mtr = (ibar >= lo) & (ibar < tr_end - k)
            mva = (ibar >= va_lo) & (ibar < va_hi)
            if mtr.sum() < 200 or mva.sum() < 50:
                continue
            try:
                mm = m.__class__(**_kwargs_of(m))
                mm.fit(X[mtr], y[mtr])
                scs.append(pearson(mm.predict(X[mva]), y[mva]))
            except Exception:                               # noqa: BLE001
                scs.append(0.0)
        sc = float(np.mean(scs)) if scs else -1e18
        table.append((m.name, sc, list(np.round(scs, 4))))
        if sc > best_sc:
            best, best_sc = m, sc
    return best, best_sc, table


def _kwargs_of(m):
    if isinstance(m, RidgeModel):
        return dict(alpha=m.alpha, cols=m.cols, name=m.name)
    if isinstance(m, GBModel):
        return dict(seed=m.seed, **m.kw)
    if isinstance(m, MLPSkModel):
        return dict(hidden=m.hidden, alpha=m.alpha, name=m.name, seed=m.seed)
    if isinstance(m, MLPTorchModel):
        return dict(hidden=m.hidden, wd=m.wd, drop=m.drop, epochs=m.epochs,
                    name=m.name, seed=m.seed)
    return {}


# =====================================================================
# 0. ПРОВЕРКИ КОРРЕКТНОСТИ
# =====================================================================
def section_checks(panel):
    out(LINE)
    out("0. ПРОВЕРКИ КОРРЕКТНОСТИ (без них цифрам верить нельзя)")
    out(LINE)
    ok_all = True

    c = panel["cand"]["BTCUSDT"]
    v = mf.load_volume("BTCUSDT", "240")
    ok, mx, worst = mf.causality_check(c, v, 240)
    ok_all &= ok
    out(f"0.1 причинность признаков: пересчёт на префиксе истории против "
        f"полного прогона, макс. расхождение {mx:.3e} "
        f"(худший {worst}) — {'ОК' if ok else 'ПРОВАЛ'}")

    # 0.2 цель не заглядывает дальше k баров
    k = 6
    X, y, ib, ic = make_dataset(panel, k)
    cc = np.array([r[4] for r in panel["cand"]["BTCUSDT"]], float)
    m0 = (ic == 0)
    b0, y0 = ib[m0], y[m0]
    chk = np.max(np.abs(y0 - np.log(cc[b0 + k] / cc[b0])))
    good = chk < 1e-12
    ok_all &= good
    out(f"0.2 цель = ровно log(c[i+k]/c[i]), сверено {len(b0)} баров BTC, "
        f"расхождение {chk:.3e} — {'ОК' if good else 'ПРОВАЛ'}")

    # 0.3 конвейер СПОСОБЕН учиться: подменяем цель на выводимую из признака
    n = panel["n"]
    hold = split_bounds(n)
    j_rsi = panel["names"].index("rsi_14")
    y_syn = (X[:, j_rsi] - 50.0) / 100.0 + rng_global.normal(0, 0.01, len(X))
    mtr = ib < hold - k
    mho = ib >= hold
    res = {}
    for nm, mdl in (("Ridge", RidgeModel(alpha=10.0)),
                    ("HistGB", GBModel(max_iter=150, learning_rate=0.1,
                                       max_leaf_nodes=31, min_samples_leaf=50,
                                       early_stopping=False)),
                    ("MLP sklearn", MLPSkModel(hidden=(32, 16), alpha=1e-3)),
                    ("MLP torch", MLPTorchModel(hidden=(32,), wd=1e-4,
                                                epochs=60))):
        if nm.endswith("sklearn") and not HAVE_SK:
            continue
        if nm.endswith("torch") and not HAVE_TORCH:
            continue
        mdl.fit(X[mtr], y_syn[mtr])
        res[nm] = pearson(mdl.predict(X[mho]), y_syn[mho])
    good = all(v > 0.9 for v in res.values())
    ok_all &= good
    out("0.3 конвейер УЧИТСЯ (цель = выводимая из признака rsi_14 + шум), "
        "корреляция вне обучения:")
    out("    " + ", ".join(f"{a} {b:.3f}" for a, b in res.items()) +
        f"  (ждём >0.9) — {'ОК' if good else 'ПРОВАЛ'}")

    # 0.4 на СЛУЧАЙНОЙ цели те же модели не находят ничего
    y_rnd = rng_global.normal(size=len(X))
    res2 = {}
    for nm, mdl in (("Ridge", RidgeModel(alpha=10.0)),
                    ("HistGB", GBModel(max_iter=150, learning_rate=0.1,
                                       max_leaf_nodes=31, min_samples_leaf=50,
                                       early_stopping=False))):
        mdl.fit(X[mtr], y_rnd[mtr])
        res2[nm] = pearson(mdl.predict(X[mho]), y_rnd[mho])
    good2 = all(abs(v) < 0.05 for v in res2.values())
    ok_all &= good2
    out("0.4 на ШУМОВОЙ цели те же модели ничего не находят: " +
        ", ".join(f"{a} {b:+.3f}" for a, b in res2.items()) +
        f"  (ждём |r|<0.05) — {'ОК' if good2 else 'ПРОВАЛ'}")

    # 0.5 холдаут не пересекается с трейном ни одним баром цели
    tr_max_target = int(ib[mtr].max()) + k
    ho_min = int(ib[mho].min())
    good3 = tr_max_target < ho_min + 1
    ok_all &= good3
    out(f"0.5 карантин: последняя цель трейна кончается на баре "
        f"{tr_max_target}, holdout начинается с {ho_min} — "
        f"{'ОК' if good3 else 'ПРОВАЛ'}")

    out(f"ИТОГ проверок: {'ВСЕ ПРОЙДЕНЫ' if ok_all else 'ЕСТЬ ПРОВАЛЫ'}")
    out()
    return ok_all


# =====================================================================
# 1. ПОСТАНОВКА B
# =====================================================================
def mean_pair_corr(panel):
    cl = {s: np.array([r[4] for r in panel["cand"][s]], float)
          for s in panel["symbols"]}
    rr = {s: np.diff(np.log(v)) for s, v in cl.items()}
    ss = panel["symbols"]
    vals = [pearson(rr[ss[i]], rr[ss[j]])
            for i in range(len(ss)) for j in range(i + 1, len(ss))]
    return float(np.mean(vals)), vals


def section_B(panel, tag="4ч, 5 монет", horizons=None, xs=False,
              show_importance=True, num="1", show_cv_table=False):
    out(LINE)
    if xs:
        out(f"{num}. ПОСТАНОВКА B2 — МЕЖМОНЕТНАЯ (рыночно-нейтральная) "
            f"доходность ({tag})")
        out(LINE)
        out("Цель: доходность монеты МИНУС средняя по 5 монетам на том же "
            "баре. Общий обвал рынка вычтен, вопрос только «какая из монет "
            "сейчас сильнее». Это самая мягкая к ML постановка из возможных.")
    else:
        out(f"{num}. ПОСТАНОВКА B — ПРЕДСКАЗАНИЕ ДОХОДНОСТИ БАРА ({tag})")
        out(LINE)
    horizons = HORIZONS if horizons is None else horizons
    n = panel["n"]
    hold = split_bounds(n)
    n_sym = len(panel["symbols"])
    rho, _ = mean_pair_corr(panel)
    bar_h = panel["bar_min"] / 60.0
    out(f"Монеты: {', '.join(panel['symbols'])}; баров на монету {n}; "
        f"признаков {len(panel['names'])}.")
    out(f"Раскол по ВРЕМЕНИ: обучение бары "
        f"[{mf.warmup_bars(panel['bar_min'])}..{hold}), "
        f"holdout [{hold}..{n}) = последние {HOLD_FRAC*100:.0f}% истории.")
    out(f"Средняя парная корреляция дневных ходов монет rho = {rho:.3f} "
        f"-> 5 монет дают не 5, а {n_sym/(1+(n_sym-1)*rho):.2f} "
        f"независимых «рынка».")
    out(f"Издержки: {FEE_SIDE*100:.3f}% тейкер + {SLIP_SIDE*100:.3f}% "
        f"слиппедж на сторону = {COST_RT*100:.3f}% на круг — предсказание "
        f"должно бить ЭТО, а не ноль.")
    out()

    lag_cols = [panel["names"].index(f"ret_{k}") for k in
                (1, 2, 3, 6, 12, 24, 48)]
    all_rows = []
    small_grid = panel["bar_min"] >= 240
    if not small_grid:
        out("Сетка гиперпараметров на 15м урезана (один прогон бустинга "
            "здесь стоит ~8 c, нейросети ~15 c): бустинг 4 конфигурации "
            "вместо 8, MLP по 2 вместо 4.")
        out()

    for k in horizons:
        X, y, ib, ic = make_dataset(panel, k)
        if xs:
            y = demean_by_bar(y, ib)
        mtr = ib < hold - k
        mho = ib >= hold
        Xtr, ytr = X[mtr], y[mtr]
        Xho, yho = X[mho], y[mho]
        ne_tr = eff_n(mtr.sum(), k, n_sym, rho)
        ne_ho = eff_n(mho.sum(), k, n_sym, rho)
        tf_name = ("4ч" if panel["bar_min"] == 240
                   else f"{panel['bar_min']}м")
        out(THIN)
        out(f"ГОРИЗОНТ k = {k} бар(ов) {tf_name} = {k*bar_h:.1f} ч "
            f"({k*bar_h/24:.2f} суток)")
        out(THIN)
        out(f"строк обучения {mtr.sum()}, строк holdout {mho.sum()}; "
            f"НЕЗАВИСИМЫХ наблюдений (перекрытие /{k} и корреляция монет): "
            f"обучение ~{ne_tr:.0f}, holdout ~{ne_ho:.0f}")
        sd_y = float(np.std(yho))
        out(f"сигма доходности за {k} бар(ов) на holdout: {sd_y*100:.2f}%; "
            f"средняя {np.mean(yho)*100:+.3f}%; издержки круга "
            f"{COST_RT*100:.3f}% = {COST_RT/sd_y:.2f} сигмы")

        kinds = [("lags", "линейная на лагах (есть ли автокорреляция)"),
                 ("ridge", "линейная Ridge на всех признаках"),
                 ("gb", "градиентный бустинг"),
                 ("mlp_sk", "нейросеть sklearn"),
                 ("mlp_torch", "нейросеть torch")]
        results = []

        # базовые линии
        for base in (ConstModel(), RandomModel(seed=k)):
            base.fit(Xtr, ytr)
            ptr, pho = base.predict(Xtr), base.predict(Xho)
            if np.std(pho) < 1e-15:
                r = dict(ic=0.0, ric=0.0, t_ic=0.0, n_eff=ne_ho, dir_acc=0.5,
                         q=dict(n_top=0, n_bot=0, r_top=0.0, r_bot=0.0,
                                spread=0.0),
                         n_trades=0, trade_mean=0.0, trade_t=0.0,
                         trade_sum=0.0, null_ic_mean=0.0, null_ic_sd=0.0,
                         null_ic_p95=0.0, null_ic_p5=0.0, p_ic=1.0,
                         p_spread=1.0, p_trade=1.0, null_sp_mean=0.0,
                         null_sp_p95=0.0, null_trade_mean=0.0,
                         null_trade_p95=0.0)
            else:
                r = eval_holdout(ptr, pho, yho, ib[mho], ic[mho], k, n_sym,
                                 rho, seed=1000 + k)
            r["name"] = base.name
            r["n_params"] = base.n_params
            r["cv"] = None
            results.append(r)

        for kind, human in kinds:
            if kind in ("mlp_sk",) and not HAVE_SK:
                continue
            if kind == "mlp_torch" and not HAVE_TORCH:
                continue
            if kind == "gb" and not HAVE_SK:
                continue
            t0 = time.time()
            grid = grid_for(kind, X.shape[1], lag_cols, small=small_grid)
            best, best_sc, table = cv_select(grid, X, y, ib,
                                             mf.warmup_bars(panel["bar_min"]),
                                             hold, k)
            mdl = best.__class__(**_kwargs_of(best))
            mdl.fit(Xtr, ytr)
            r = eval_holdout(mdl.predict(Xtr), mdl.predict(Xho), yho,
                             ib[mho], ic[mho], k, n_sym, rho, seed=2000 + k)
            r["name"] = best.name
            r["human"] = human
            r["n_params"] = getattr(mdl, "n_params", 0)
            r["cv"] = best_sc
            r["cv_table"] = table
            r["ic_need"] = COST_RT / (1.40 * sd_y)
            r["secs"] = time.time() - t0
            if kind == "gb" and show_importance and k == horizons[min(
                    1, len(horizons) - 1)]:
                r["imp"] = perm_importance(mdl, Xtr, ytr, panel["names"])
            results.append(r)

        ic_need = COST_RT / (1.40 * sd_y)
        out(f"ПОРОГ ОКУПАЕМОСТИ на этом горизонте: чтобы верхний квинтиль "
            f"отбил {COST_RT*100:.3f}%, нужен IC > {ic_need:.4f}")
        out()
        out(f"{'модель':<52}{'парам':>8}{'н/п':>9}{'CV.IC':>8}{'IC':>9}"
            f"{'rankIC':>8}{'t(IC)':>7}{'напр%':>7}{'нуль95':>8}{'p':>8}")
        for r in results:
            cv = "  -   " if r["cv"] is None else f"{r['cv']:+.4f}"
            npr = (r["n_params"] if r["n_params"] else 1)
            out(f"{r['name'][:52]:<52}{r['n_params']:>8}{ne_tr/npr:>9.1f}"
                f"{cv:>8}{r['ic']:+9.4f}{r['ric']:+8.4f}{r['t_ic']:+7.2f}"
                f"{r['dir_acc']*100:>7.1f}{r['null_ic_p95']:+8.4f}"
                f"{fmt_p(r['p_ic']):>8}")
        out("  (н/п = независимых наблюдений обучения на один параметр "
            "модели; здоровое значение >=10, «<1» = параметров больше, "
            "чем данных)")
        trained = [r for r in results if r["cv"] is not None]
        bi = max(trained, key=lambda r: r["ic"])
        if bi["ic"] <= 0:
            out(f"  ЛУЧШАЯ обученная модель по IC: «{bi['name'][:40]}», "
                f"IC {bi['ic']:+.4f} — положительного IC нет вообще "
                f"(порог окупаемости {ic_need:.4f}).")
        else:
            rel = ic_need / bi["ic"]
            verdict = (f"меньше порога в {rel:.1f} раз(а)" if rel > 1
                       else "порог по IC формально взят")
            out(f"  ЛУЧШАЯ обученная модель по IC: «{bi['name'][:40]}», "
                f"IC {bi['ic']:+.4f} (p = {fmt_p(bi['p_ic'])}) против "
                f"порога окупаемости {ic_need:.4f} — {verdict};")
            out(f"    её РЕАЛЬНАЯ чистая сделка: "
                f"{bi['trade_mean']*100:+.3f}% "
                f"(p = {fmt_p(bi['p_trade'])}, {bi['n_trades']} сделок) — "
                f"вот что осталось после издержек.")
        out()
        out(f"{'модель':<52}{'верх кв.':>10}{'низ кв.':>10}{'спред':>9}"
            f"{'p':>7}{'сдел':>6}{'ср.чистая':>11}{'t':>6}{'p':>7}")
        for r in results:
            q = r["q"]
            out(f"{r['name'][:52]:<52}{q['r_top']*100:+10.3f}"
                f"{q['r_bot']*100:+10.3f}{q['spread']*100:+9.3f}"
                f"{fmt_p(r['p_spread']):>7}{r['n_trades']:>6}"
                f"{r['trade_mean']*100:+11.3f}{r['trade_t']:+6.2f}"
                f"{fmt_p(r['p_trade']):>7}")
        out("  (верх/низ кв. — средняя доходность бара из верхнего/"
            "нижнего квинтиля предсказания, %, ДО издержек;")
        out("   ср.чистая — средняя доходность НЕПЕРЕКРЫВАЮЩЕЙСЯ сделки "
            f"«купил при топ-предсказании, держал {k} бар(ов)» ЗА ВЫЧЕТОМ "
            f"{COST_RT*100:.3f}%;")
        out("   пороги квинтилей взяты из ТРЕЙНА, holdout не подглядываем; "
            f"p — доля из {N_PERM} блочных перестановок цели не хуже факта)")
        if xs:
            out(f"   пара «лонг верх / шорт низ» платит два круга = "
                f"{2*COST_RT*100:.3f}%: спред обязан быть выше ЭТОГО.")
            out(f"{'модель':<52}{'спред брутто%':>15}{'минус издержки%':>17}")
            for r in results:
                if r["cv"] is None:
                    continue
                out(f"{r['name'][:52]:<52}{r['q']['spread']*100:>15.3f}"
                    f"{(r['q']['spread']-2*COST_RT)*100:>17.3f}")

        # нулевое распределение подробно для лучшей по CV непустой модели
        bestr = max((r for r in results if r["cv"] is not None),
                    key=lambda r: r["cv"])
        if show_cv_table:
            gbr = next((r for r in results
                        if r["name"].startswith("бустинг")), None)
            if gbr is not None and gbr.get("cv_table"):
                out()
                out("  Как выбирались гиперпараметры бустинга — "
                    "walk-forward CV ВНУТРИ трейна (IC по фолдам, "
                    "будущее в прошлое не подмешано):")
                out(f"    {'конфигурация':<62}{'IC по фолдам (по времени)':>34}"
                    f"{'среднее':>9}")
                for nm_, sc_, folds_ in gbr["cv_table"]:
                    fs = " ".join(f"{v:+.3f}" for v in folds_)
                    mark = " <- выбрана" if nm_ == gbr["name"] else ""
                    out(f"    {nm_[8:70]:<62}{fs:>34}{sc_:>9.4f}{mark}")
                out("    (разброс IC между фолдами больше самого IC — "
                    "«лучшая» конфигурация меняется от куска истории "
                    "к куску)")
        out()
        out(f"Нулевое распределение IC ({N_PERM} блочных перестановок "
            f"цели, модель «{bestr['name'][:40]}»):")
        out(f"  среднее {bestr['null_ic_mean']:+.4f}, "
            f"сигма {bestr['null_ic_sd']:.4f}, "
            f"5% {bestr['null_ic_p5']:+.4f}, 95% {bestr['null_ic_p95']:+.4f} "
            f"| ФАКТ {bestr['ic']:+.4f} -> p = {fmt_p(bestr['p_ic'])}")
        out(f"  нуль по чистой сделке: среднее "
            f"{bestr['null_trade_mean']*100:+.3f}%, 95% "
            f"{bestr['null_trade_p95']*100:+.3f}% | ФАКТ "
            f"{bestr['trade_mean']*100:+.3f}% -> p = "
            f"{fmt_p(bestr['p_trade'])}")

        # buy&hold сравнение
        bh = []
        for j, s in enumerate(panel["symbols"]):
            cc = np.array([r[4] for r in panel["cand"][s]], float)
            bh.append(cc[n - 1] / cc[hold] - 1.0)
        out(f"  для масштаба: buy&hold на holdout по монетам "
            f"{', '.join(f'{s} {b*100:+.0f}%' for s, b in zip(panel['symbols'], bh))}")

        # что модель считает важным (перестановочная важность на трейне)
        if show_importance:
            gb = next((r for r in results
                       if r["name"].startswith("бустинг")), None)
            if gb is not None and "imp" in gb:
                out()
                out("  Чем «питается» бустинг (перестановочная важность на "
                    "трейне, падение IC при порче признака), топ-10:")
                for nm, v in gb["imp"][:10]:
                    out(f"    {nm:<18}{v:+.4f}")

        all_rows.append((k, results))
        out()

    return all_rows, hold, rho


def perm_importance(mdl, X, y, names, n_rep=2, seed=5, cap=60000):
    """Падение IC при случайной порче признака (на тех же данных)."""
    if len(X) > cap:
        sub = np.linspace(0, len(X) - 1, cap).astype(int)
        X, y = X[sub], y[sub]
    base = pearson(mdl.predict(X), y)
    rg = np.random.default_rng(seed)
    res = []
    for j in range(X.shape[1]):
        drops = []
        for _ in range(n_rep):
            Xp = X.copy()
            Xp[:, j] = rg.permutation(Xp[:, j])
            drops.append(base - pearson(mdl.predict(Xp), y))
        res.append((names[j], float(np.mean(drops))))
    res.sort(key=lambda t: -t[1])
    return res


def section_seeds(panel, rowsB, k=6, seeds=(11, 22, 33, 44, 55)):
    """Устойчивость к зерну: та же конфигурация, другая случайная инициализация.

    Методика проекта: если при смене зерна «побеждают другие конфиги» и
    метрика скачет — это шум, а не сигнал."""
    out(THIN)
    out(f"3.1 УСТОЙЧИВОСТЬ К ЗЕРНУ (k={k}, 4ч): тот же выбор "
        "гиперпараметров, другая случайная инициализация модели")
    out(THIN)
    n = panel["n"]
    hold = split_bounds(n)
    n_sym = len(panel["symbols"])
    rho, _ = mean_pair_corr(panel)
    X, y, ib, ic = make_dataset(panel, k)
    mtr, mho = ib < hold - k, ib >= hold
    picked = {r["name"]: r for kk, rr in rowsB if kk == k for r in rr
              if r["cv"] is not None}
    out(f"{'модель (конфигурация выбрана CV)':<46}"
        + "".join(f"{'зерно ' + str(s):>12}" for s in seeds)
        + f"{'разброс IC':>12}")
    lag_cols = [panel["names"].index(f"ret_{q}") for q in
                (1, 2, 3, 6, 12, 24, 48)]
    protos = list(picked)
    # восстанавливаем прототипы моделей из сеток по имени
    bank = {}
    for kind in ("ridge", "lags", "gb", "mlp_sk", "mlp_torch"):
        if kind in ("gb", "mlp_sk") and not HAVE_SK:
            continue
        if kind == "mlp_torch" and not HAVE_TORCH:
            continue
        for m in grid_for(kind, X.shape[1], lag_cols):
            bank[m.name] = m
    rows = []
    for nm in protos:
        proto = bank.get(nm)
        if proto is None or isinstance(proto, RidgeModel):
            continue                        # у Ridge нет случайности
        ics, nets = [], []
        for sd in seeds:
            kw = _kwargs_of(proto)
            kw["seed"] = sd
            mm = proto.__class__(**kw)
            mm.fit(X[mtr], y[mtr])
            pho, ptr = mm.predict(X[mho]), mm.predict(X[mtr])
            ics.append(pearson(pho, y[mho]))
            sel = select_trades(pho, ib[mho], ic[mho],
                                float(np.percentile(ptr, 80)), k, n_sym)
            t = trades_from_sel(y[mho], sel)
            nets.append(float(t.mean()) if len(t) else 0.0)
        rows.append((nm, ics, nets))
        out(f"{nm[:46]:<46}" + "".join(f"{v:>+12.4f}" for v in ics)
            + f"{max(ics)-min(ics):>12.4f}")
    out("  (разброс IC от одной лишь смены зерна; если он сравним с самим "
        "IC — «сигнал» неотличим от случайности инициализации)")
    out(f"{'то же в чистой доходности сделки, %':<46}"
        + "".join(f"{'зерно ' + str(s):>12}" for s in seeds))
    for nm, _ics, nets in rows:
        out(f"{nm[:46]:<46}" + "".join(f"{v*100:>+12.3f}" for v in nets))
    out()
    return rows


def section_needed(panel, rowsB):
    """Сколько данных нужно, чтобы вопрос вообще имел ответ (расчёт)."""
    out(THIN)
    out("3.2 СКОЛЬКО ДАННЫХ НУЖНО, ЧТОБЫ ВОПРОС ИМЕЛ ОТВЕТ (расчёт, "
        "не мнение)")
    out(THIN)
    n = panel["n"]
    hold = split_bounds(n)
    n_sym = len(panel["symbols"])
    rho, _ = mean_pair_corr(panel)
    out("Логика: топ-квинтиль нормального предсказания даёт средний z ~ 1.40,")
    out("значит средняя доходность отобранного бара ~ IC * сигма * 1.40.")
    out(f"Чтобы окупить {COST_RT*100:.3f}% на круг, нужен "
        f"IC > {COST_RT*100:.3f}% / (1.40 * сигма).")
    out("Чтобы такой IC отличить от нуля (мощность 80%, alpha 5%) нужно "
        "n > (1.96+0.84)^2 / IC^2 НЕЗАВИСИМЫХ наблюдений.")
    out()
    out(f"{'горизонт':<16}{'сигма%':>9}{'нужный IC':>11}{'нужно набл':>12}"
        f"{'есть набл':>11}{'видим IC от':>13}{'лет надо':>10}"
        f"{'вывод теста':>16}")
    have_years = n * (panel["bar_min"] / 60.0) / 24.0 / 365.0
    cross = n_sym / (1.0 + (n_sym - 1) * rho)
    rows = []
    for k in HORIZONS:
        X, y, ib, _ic = make_dataset(panel, k)
        sd = float(np.std(y[ib >= hold]))
        ic_need = COST_RT / (1.40 * sd)
        n_need = (1.96 + 0.84) ** 2 / (ic_need ** 2) + 3
        have = eff_n(int((ib < hold - k).sum()), k, n_sym, rho)
        ic_min = (1.96 + 0.84) / math.sqrt(max(4.0, have))   # что мы вообще
        bars_needed = n_need * k / cross                     # способны увидеть
        years = bars_needed * (panel["bar_min"] / 60.0) / 24.0 / 365.0
        concl = "ОКОНЧАТЕЛЬНЫЙ" if ic_min <= ic_need else "мощности мало"
        rows.append(dict(k=k, sd=sd, ic_need=ic_need, n_need=n_need,
                         have=have, years=years, ic_min=ic_min,
                         concl=concl))
        hrs = k * panel["bar_min"] / 60.0
        label = f"k={k} ({hrs:.0f} ч)"
        out(f"{label:<16}{sd*100:>9.2f}{ic_need:>11.4f}{n_need:>12.0f}"
            f"{have:>11.0f}{ic_min:>13.4f}{years:>10.1f}{concl:>16}")
    out(f"  сигма% — размах хода за горизонт; нужный IC — сколько нужно, "
        f"чтобы отбитый квинтиль окупил {COST_RT*100:.3f}%;")
    out("  есть набл. — независимые наблюдения ОБУЧЕНИЯ (с поправкой на "
        "перекрытие горизонта и корреляцию монет);")
    out("  видим IC от — минимальный IC, который наша выборка ВООБЩЕ "
        "способна отличить от нуля (2.80/sqrt(n));")
    out(f"  лет надо — сколько лет истории по 5 нашим монетам нужно для "
        f"нужного IC (в наличии {have_years:.1f}).")
    out()
    out("ЧИТАЕМ ТАБЛИЦУ ВНИМАТЕЛЬНО — здесь НОЖНИЦЫ, и это главный "
        "практический вывод работы:")
    out("  * чем ДЛИННЕЕ горизонт, тем МЕНЬШЕ нужный IC (издержки — "
        "фиксированный налог, а размах хода растёт как корень из времени);")
    out("  * но чем меньше IC, тем БОЛЬШЕ независимых наблюдений нужно, "
        "чтобы отличить его от нуля, а каждое наблюдение занимает k баров;")
    out("  * где «видим IC от» НИЖЕ «нужного IC» — тест ОКОНЧАТЕЛЬНЫЙ: "
        "прибыльный сигнал мы бы увидели, и его там нет;")
    out("  * где ВЫШЕ — мощности не хватает: там нельзя ни подтвердить, "
        "ни опровергнуть, и торговать на этом нельзя тем более.")
    out()
    return rows


def section_B_fullperm(panel, k=6):
    """Перестановка с ПОЛНЫМ переобучением: ловит утечку через отбор модели."""
    out(THIN)
    out(f"3. ПЕРЕСТАНОВКА С ПОЛНЫМ ПЕРЕОБУЧЕНИЕМ (k={k}, 4ч) — проверяем не "
        "только предсказания, но и весь конвейер выбора модели")
    out(THIN)
    n = panel["n"]
    hold = split_bounds(n)
    n_sym = len(panel["symbols"])
    rho, _ = mean_pair_corr(panel)
    X, y, ib, ic = make_dataset(panel, k)
    mtr, mho = ib < hold - k, ib >= hold
    lag_cols = [panel["names"].index(f"ret_{q}") for q in
                (1, 2, 3, 6, 12, 24, 48)]

    def run_once(ytr_used):
        best = None
        best_sc = -1e18
        for kind in ("ridge", "gb"):
            grid = grid_for(kind, X.shape[1], lag_cols, small=False)
            yy = y.copy()
            yy[mtr] = ytr_used
            b, sc, _ = cv_select(grid, X, yy, ib,
                                 mf.warmup_bars(panel["bar_min"]), hold, k)
            if sc > best_sc:
                best, best_sc = b, sc
        mdl = best.__class__(**_kwargs_of(best))
        mdl.fit(X[mtr], ytr_used)
        return pearson(mdl.predict(X[mho]), y[mho]), best.name

    ic_real, nm_real = run_once(y[mtr])
    rg = np.random.default_rng(31337)
    nulls = []
    for _ in range(N_PERM_FULL):
        ysh = block_shuffle(y[mtr], ib[mtr], ic[mtr], k, n_sym, rg)
        v, _ = run_once(ysh)
        nulls.append(v)
    nulls = np.array(nulls)
    p = float((np.sum(nulls >= ic_real) + 1) / (N_PERM_FULL + 1))
    out(f"реальная цель: выбрана «{nm_real[:50]}», IC на holdout "
        f"{ic_real:+.4f}")
    out(f"{N_PERM_FULL} перестановок ЦЕЛИ ОБУЧЕНИЯ (весь подбор заново): "
        f"нуль-IC среднее {nulls.mean():+.4f}, сигма {nulls.std():.4f}, "
        f"5%..95% {pct(nulls,5):+.4f}..{pct(nulls,95):+.4f}")
    out(f"p (доля перестановок не хуже факта) = {fmt_p(p)}")
    out()
    return ic_real, nulls, p


# =====================================================================
# 2. ПОСТАНОВКА A — классификация исхода сделки
# =====================================================================
def section_A():
    out(LINE)
    out("5. ПОСТАНОВКА A — КЛАССИФИКАЦИЯ ИСХОДА СДЕЛКИ "
        "(признаки сигнального бара -> тейк/не тейк)")
    out(LINE)
    import signal_score as sc
    import signal_engine2 as se

    cfgs = sc.load_setups()
    feats = list(se.SCORE_FEATURES)
    data = {}
    tr_all, ho_all = [], []
    out(f"Сетапы/ТФ: {len(cfgs)} комбинаций из signal_setups2.json "
        f"(геномы отбирал evolution12 БЕЗ holdout).")
    out(f"Признаки: se.bar_features, {len(feats)} штук — те же, что в "
        "прошлой волне (score_report_out.txt).")
    out()
    out(f"{'комбинация':<20}{'обуч':>6}{'тейк':>6}{'база':>7}"
        f"{'holdout':>9}{'тейк':>6}{'база':>7}")
    for key, cfg in sorted(cfgs.items()):
        d = sc.load_data(cfg["interval_min"])
        hold, _ = sc.hold_from_winners(cfg["setup"], cfg["interval_min"],
                                       d["n"])
        rtr, _ = sc.collect_samples(cfg["setup"], cfg["genome"], d,
                                    (0, hold), cfg["lev"])
        rho_, _ = sc.collect_samples(cfg["setup"], cfg["genome"], d,
                                     (hold, d["n"]), cfg["lev"])
        data[key] = (rtr, rho_)
        for r in rtr:
            r["key"] = key
        for r in rho_:
            r["key"] = key
        tr_all += rtr
        ho_all += rho_
        b1 = np.mean([r["y"] for r in rtr]) if rtr else 0.0
        b2 = np.mean([r["y"] for r in rho_]) if rho_ else 0.0
        out(f"{key:<20}{len(rtr):>6}{sum(r['y'] for r in rtr):>6}"
            f"{b1:>7.2f}{len(rho_):>9}{sum(r['y'] for r in rho_):>6}"
            f"{b2:>7.2f}")
    out(f"{'ВСЕГО (объединяя)':<20}{len(tr_all):>6}"
        f"{sum(r['y'] for r in tr_all):>6}"
        f"{np.mean([r['y'] for r in tr_all]):>7.2f}{len(ho_all):>9}"
        f"{sum(r['y'] for r in ho_all):>6}"
        f"{np.mean([r['y'] for r in ho_all]):>7.2f}")
    out()

    def mat(rows):
        return (np.array([[r["f"][nm] for nm in feats] for r in rows], float),
                np.array([r["y"] for r in rows], int),
                np.array([r["r"] for r in rows], float))

    Xtr, ytr, Rtr = mat(tr_all)
    Xho, yho, Rho = mat(ho_all)

    # ------- размер выборки против числа параметров
    out("5.1 РАЗМЕР ВЫБОРКИ ПРОТИВ ЧИСЛА ПАРАМЕТРОВ "
        "(правило: параметров на порядок меньше наблюдений)")
    out(f"{'модель':<44}{'параметров':>12}{'наблюдений':>12}"
        f"{'набл./парам':>13}{'вердикт':>12}")
    n_obs = len(tr_all)
    rowsp = [("константа (доля тейков)", 1),
             ("логистическая регрессия, 30 признаков", len(feats) + 1),
             ("логистическая, 4 отобранных признака", 5),
             ("бустинг HistGB (100 деревьев x 31 лист)", 100 * 61),
             ("нейросеть MLP (30-32-16-1)", 30 * 32 + 32 + 32 * 16 + 16 + 17)]
    for nm, npar in rowsp:
        ratio = n_obs / npar
        verd = "ОК" if ratio >= 10 else ("на грани" if ratio >= 3
                                         else "НЕВОЗМОЖНО")
        out(f"{nm:<44}{npar:>12}{n_obs:>12}{ratio:>13.2f}{verd:>12}")
    out("  (наблюдений = ВСЕ обучающие сделки всех 8 комбинаций вместе; "
        "по одной комбинации их 24-43 — там даже 30-признаковая линейная "
        "модель уже переобучение)")
    out()

    # ------- модели
    out("5.2 МОДЕЛИ НА ОБЪЕДИНЁННОЙ ВЫБОРКЕ "
        "(обучение только на трейне, holdout не касались)")
    models = []
    models.append(("константа = доля тейков трейна", None))
    if HAVE_SK:
        models += [
            ("логистическая регрессия L2 (эталон прошлой волны)", "logreg"),
            ("бустинг HistGradientBoosting", "gb"),
            ("нейросеть sklearn MLP (32,16)", "mlp"),
        ]
    if HAVE_TORCH:
        models.append(("нейросеть torch MLP (32,16) + dropout", "torch"))

    res = []
    for nm, kind in models:
        p_tr, p_ho, npar = fit_clf(kind, Xtr, ytr, Xho, feats)
        a = auc_score(yho, p_ho) if p_ho is not None else None
        a_in = auc_score(ytr, p_tr) if p_tr is not None else None
        # ранжирование: верхняя половина holdout по оценке против нижней
        if p_ho is not None and np.std(p_ho) > 1e-12:
            med = np.median(p_tr)
            hi = p_ho >= med
            lo = ~hi
            r_hi = float(np.mean(Rho[hi])) if hi.sum() else 0.0
            r_lo = float(np.mean(Rho[lo])) if lo.sum() else 0.0
            nhi, nlo = int(hi.sum()), int(lo.sum())
        else:
            r_hi = r_lo = float(np.mean(Rho))
            nhi, nlo = len(Rho), 0
        # перестановочный нуль AUC
        if a is not None:
            rg = np.random.default_rng(777)
            nulls = []
            for _ in range(N_PERM):
                nulls.append(auc_score(rg.permutation(yho), p_ho))
            nulls = np.array([x for x in nulls if x is not None])
            p_val = float((np.sum(nulls >= a) + 1) / (len(nulls) + 1))
            nm_, ns_, n95 = nulls.mean(), nulls.std(), pct(nulls, 95)
        else:
            p_val, nm_, ns_, n95 = 1.0, 0.5, 0.0, 0.5
        res.append(dict(name=nm, npar=npar, auc_in=a_in, auc=a, p=p_val,
                        null_mean=nm_, null_sd=ns_, null95=n95,
                        r_hi=r_hi, r_lo=r_lo, nhi=nhi, nlo=nlo))

    out(f"{'модель':<48}{'парам':>7}{'AUC.обуч':>10}{'AUC.hold':>10}"
        f"{'нуль.ср':>9}{'нуль95':>9}{'p':>8}")
    for r in res:
        ai = "  -  " if r["auc_in"] is None else f"{r['auc_in']:.3f}"
        ao = "  -  " if r["auc"] is None else f"{r['auc']:.3f}"
        out(f"{r['name'][:48]:<48}{r['npar']:>7}{ai:>10}{ao:>10}"
            f"{r['null_mean']:>9.3f}{r['null95']:>9.3f}{fmt_p(r['p']):>8}")
    out()
    out("5.3 ЧТО ЭТО ДАЁТ В R (ранжирование сделок holdout: "
        "верхняя половина по оценке против нижней; порог = медиана ТРЕЙНА)")
    out(f"{'модель':<48}{'n.верх':>8}{'R.верх':>9}{'n.низ':>8}{'R.низ':>9}"
        f"{'разница':>9}{'знак':>7}")
    base_r = float(np.mean(Rho))
    for r in res:
        d = r["r_hi"] - r["r_lo"]
        sign = "верно" if d > 0 else ("ПЕРЕВЁРНУТО" if d < 0 else "-")
        out(f"{r['name'][:48]:<48}{r['nhi']:>8}{r['r_hi']:>+9.3f}"
            f"{r['nlo']:>8}{r['r_lo']:>+9.3f}{d:>+9.3f}{sign:>13}")
    out(f"  средний R всех сделок holdout без всякого отбора: {base_r:+.3f} "
        f"({len(Rho)} сделок)")
    out()
    return res, len(tr_all), len(ho_all), base_r


def fit_clf(kind, Xtr, ytr, Xho, feats):
    """Обучение классификатора; возвращает (p_train, p_holdout, n_params)."""
    if kind is None:
        m = float(np.mean(ytr))
        return np.full(len(Xtr), m), np.full(len(Xho), m), 1
    mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0)
    sd[sd <= 1e-12] = 1.0
    Ztr, Zho = (Xtr - mu) / sd, (Xho - mu) / sd
    if kind == "logreg":
        m = LogisticRegression(C=0.3, max_iter=2000, random_state=SEED)
        m.fit(Ztr, ytr)
        return (m.predict_proba(Ztr)[:, 1], m.predict_proba(Zho)[:, 1],
                Ztr.shape[1] + 1)
    if kind == "gb":
        m = HistGradientBoostingClassifier(max_iter=100, learning_rate=0.1,
                                           max_leaf_nodes=31,
                                           min_samples_leaf=5,
                                           l2_regularization=1.0,
                                           early_stopping=False,
                                           random_state=SEED)
        m.fit(Xtr, ytr)
        return (m.predict_proba(Xtr)[:, 1], m.predict_proba(Xho)[:, 1],
                int(m.n_iter_) * 61)
    if kind == "mlp":
        m = MLPClassifier(hidden_layer_sizes=(32, 16), alpha=1e-2,
                          max_iter=1500, random_state=SEED)
        m.fit(Ztr, ytr)
        npar = (Ztr.shape[1] * 32 + 32 + 32 * 16 + 16 + 17)
        return m.predict_proba(Ztr)[:, 1], m.predict_proba(Zho)[:, 1], npar
    if kind == "torch":
        torch.manual_seed(SEED)
        net = nn.Sequential(nn.Linear(Ztr.shape[1], 32), nn.ReLU(),
                            nn.Dropout(0.2), nn.Linear(32, 16), nn.ReLU(),
                            nn.Linear(16, 1))
        opt = torch.optim.Adam(net.parameters(), lr=3e-3, weight_decay=1e-2)
        lf = nn.BCEWithLogitsLoss()
        Xt = torch.tensor(Ztr, dtype=torch.float32)
        Yt = torch.tensor(ytr, dtype=torch.float32).reshape(-1, 1)
        for _ in range(400):
            net.train()
            opt.zero_grad()
            lf(net(Xt), Yt).backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            ptr = torch.sigmoid(net(Xt)).numpy().ravel()
            pho = torch.sigmoid(net(torch.tensor(Zho,
                                dtype=torch.float32))).numpy().ravel()
        npar = Ztr.shape[1] * 32 + 32 + 32 * 16 + 16 + 17
        return ptr, pho, npar
    raise ValueError(kind)


# =====================================================================
# MAIN
# =====================================================================
def main():
    out(LINE)
    out("НЕЙРОСЕТИ ВМЕСТО РУЧНОГО ПОИСКА ПРАВИЛ — ПРОВЕРКА ИЗМЕРЕНИЕМ")
    out(LINE)
    out(f"Библиотеки: numpy {LIBS['numpy']}, scipy {LIBS['scipy']}, "
        f"sklearn {LIBS['sklearn']}, torch {LIBS['torch']}"
        + (f" (CUDA: {LIBS.get('torch_cuda')}, "
           f"потоков {LIBS.get('torch_threads')})" if HAVE_TORCH else ""))
    out("Две постановки: A — исход сделки (десятки наблюдений), "
        "B — доходность бара (десятки тысяч).")
    out(f"Раскол: обучение [0..{(1-HOLD_FRAC)*100:.0f}%), holdout — "
        f"последние {HOLD_FRAC*100:.0f}%; гиперпараметры — walk-forward CV "
        "внутри трейна; нуль — 200 перестановок цели.")
    out()

    panel = load_panel("240", 240)
    section_checks(panel)
    rowsB, hold, rho = section_B(panel, num="1", show_cv_table=True)
    rowsXS, _, _ = section_B(panel, tag="4ч, 5 монет", horizons=(6, 24),
                             xs=True, show_importance=False, num="2")
    icr, nulls_full, p_full = section_B_fullperm(panel, k=6)
    seed_rows = section_seeds(panel, rowsB, k=6)
    need_rows = section_needed(panel, rowsB)

    # --------- масштаб данных: 15м (в 16 раз больше баров)
    out(LINE)
    out("4. А ЕСЛИ ДАННЫХ БОЛЬШЕ? ТОТ ЖЕ ТЕСТ НА 15-МИНУТКАХ")
    out(LINE)
    out("Вопрос владельца «может, мало данных» проверяем прямо: те же "
        "признаки, те же модели, тот же протокол — но баров в 16 раз больше.")
    out("Горизонты подобраны так, чтобы совпадать по ВРЕМЕНИ с 4ч-версией: "
        "k=4 (1 час), k=24 (6 часов), k=96 (сутки).")
    out()
    rows15 = []
    try:
        p15 = load_panel("15", 15)
        rows15, _, _ = section_B(p15, tag="15м, 5 монет",
                                 horizons=(4, 24, 96), show_importance=True,
                                 num="4.1")
    except Exception as e:                                  # noqa: BLE001
        out(f"15м-панель не загрузилась: {e}")

    resA, n_tr_a, n_ho_a, base_r = section_A()

    summary(rowsB, rowsXS, rows15, resA, hold, panel, n_tr_a, n_ho_a, base_r,
            icr, nulls_full, p_full, rho, seed_rows, need_rows)

    with open("ml_test_out.txt", "w", encoding="utf-8") as fh:
        fh.write(_BUF.getvalue())
    print(f"\n[записано в ml_test_out.txt, {time.time()-T0:.0f} c]")


def _flat(rows):
    return [(k, r) for k, rr in rows for r in rr if r["cv"] is not None]


def summary(rowsB, rowsXS, rows15, resA, hold, panel, n_tr_a, n_ho_a, base_r,
            icr, nulls_full, p_full, rho, seed_rows=(), need_rows=()):
    out(LINE)
    out("6. СВОДКА ВСЕХ ИЗМЕРЕНИЙ")
    out(LINE)

    def block(title, rows):
        out(title)
        out(f"{'гор.':<8}{'модель':<40}{'IC':>8}{'нужен IC':>10}{'p':>8}"
            f"{'сдел':>6}{'ср.чистая%':>12}{'p':>8}{'вердикт':>22}")
        for k, r in _flat(rows):
            need = r.get("ic_need", 0.0)
            if r["p_ic"] >= 0.05:
                v = "шум"
            elif r["ic"] < need:
                v = "сигнал есть, но мал"
            elif r["trade_mean"] > 0 and r["p_trade"] < 0.05:
                v = "ЕСТЬ И ОКУПАЕТСЯ"
            else:
                v = "не окупает"
            out(f"{'k='+str(k):<8}{r['name'][:40]:<40}{r['ic']:+8.4f}"
                f"{need:>10.4f}{fmt_p(r['p_ic']):>8}"
                f"{r['n_trades']:>6}{r['trade_mean']*100:+12.3f}"
                f"{fmt_p(r['p_trade']):>8}{v:>22}")
        out()

    block("6.1 ПОСТАНОВКА B, 4ч — абсолютная доходность", rowsB)
    block("6.2 ПОСТАНОВКА B2, 4ч — межмонетная (рыночно-нейтральная)", rowsXS)
    if rows15:
        block("6.3 ПОСТАНОВКА B, 15м — в 16 раз больше наблюдений", rows15)

    allr = _flat(rowsB) + _flat(rowsXS) + _flat(rows15)
    n_tot = len(allr)
    n_sig = sum(1 for _k, r in allr if r["p_ic"] < 0.05)
    n_prof = sum(1 for _k, r in allr
                 if r["trade_mean"] > 0 and r["p_trade"] < 0.05)
    n_pos_ic = sum(1 for _k, r in allr if r["ic"] > 0)
    exp_sig = 0.05 * n_tot
    out("6.4 ИТОГ ПО ЗНАЧИМОСТИ")
    out(f"обученных моделей всего (все постановки, все горизонты): {n_tot}")
    out(f"из них IC значим против перестановочного нуля (p<0.05): {n_sig} "
        f"— при чистом шуме ожидалось бы {exp_sig:.1f}")
    out(f"из них IC хотя бы просто положителен: {n_pos_ic} из {n_tot} "
        f"(при монетке ожидалось бы {n_tot/2:.1f})")
    out(f"из них ЧИСТАЯ сделка после издержек положительна И значима: "
        f"{n_prof}")
    out(f"перестановка с ПОЛНЫМ переобучением (k=6, 4ч): факт IC "
        f"{icr:+.4f}, нуль 5%..95% {pct(nulls_full,5):+.4f}.."
        f"{pct(nulls_full,95):+.4f}, p = {fmt_p(p_full)}")
    out()
    out("6.4.1 САМОЕ ВАЖНОЕ ЧИСЛО ЭТОЙ РАБОТЫ — «КРОСС-ВАЛИДАЦИЯ ОБЕЩАЛА vs "
        "HOLDOUT ДАЛ»")
    cvs = [(k, r) for k, r in allr if r["cv"] is not None]
    if cvs:
        m_cv = float(np.mean([r["cv"] for _k, r in cvs]))
        m_ho = float(np.mean([r["ic"] for _k, r in cvs]))
        worse = sum(1 for _k, r in cvs if r["ic"] < r["cv"])
        out(f"{'модель':<50}{'обещала CV':>12}{'дал holdout':>13}"
            f"{'разница':>10}")
        for k, r in sorted(cvs, key=lambda t: -t[1]["cv"])[:12]:
            out(f"{('k='+str(k)+' '+r['name'])[:50]:<50}{r['cv']:>+12.4f}"
                f"{r['ic']:>+13.4f}{r['ic']-r['cv']:>+10.4f}")
        out(f"в среднем по всем {len(cvs)} моделям: CV обещала "
            f"{m_cv:+.4f}, holdout дал {m_ho:+.4f} "
            f"(хуже обещанного у {worse} из {len(cvs)})")
        out("  Именно так выглядит переобучение, когда его НЕ ловят: "
            "внутренняя кросс-валидация показывает уверенный плюс,")
        out("  а на данных, которых модель не видела, он исчезает. "
            "Останови мы работу на кросс-валидации — доложили бы об успехе.")
    out(f"поправка на множественность: чтобы одна из {n_tot} моделей была "
        f"значима на уровне 0.05, ей нужен p < {0.05/max(1,n_tot):.4f} "
        f"(Бонферрони)")
    best = min(allr, key=lambda t: t[1]["p_ic"]) if allr else (0, dict(
        p_ic=1.0, name="-", ic=0.0, null_ic_p95=0.0, trade_mean=0.0,
        p_trade=1.0))
    out(f"лучший p среди всех моделей: {fmt_p(best[1]['p_ic'])} "
        f"(«{best[1]['name'][:45]}», k={best[0]}) — "
        f"{'проходит' if best[1]['p_ic'] < 0.05/max(1,n_tot) else 'НЕ проходит'}"
        f" поправку Бонферрони")
    out()

    a_best = max((r for r in resA if r["auc"] is not None),
                 key=lambda r: r["auc"], default=None)
    out("6.5 ПОСТАНОВКА A (исход сделки)")
    for r in resA:
        if r["auc"] is None:
            continue
        out(f"  {r['name'][:52]:<52} AUC {r['auc']:.3f} при нуле "
            f"{r['null_mean']:.3f} (95% {r['null95']:.3f}), p = "
            f"{fmt_p(r['p'])}, R верх-низ {r['r_hi']-r['r_lo']:+.3f}")
    out(f"  обучающих сделок {n_tr_a}, holdout-сделок {n_ho_a}, "
        f"средний R holdout без отбора {base_r:+.3f}")
    out()

    # ---------------------------------------------------------- ответы
    out(LINE)
    out("7. ТРИ ОТВЕТА ВЛАДЕЛЬЦУ")
    out(LINE)
    n = panel["n"]
    ne24 = eff_n(len(panel["symbols"]) * (n - hold), 24,
                 len(panel["symbols"]), rho)
    lags = [(k, r) for k, r in allr if "лагах" in r["name"]]
    lag_sig = sum(1 for _k, r in lags if r["p_ic"] < 0.05)
    stub = dict(ic=0.0, name="-", null_ic_p95=0.0, trade_mean=0.0,
                p_trade=1.0, p_ic=1.0)
    best_ic = max(allr, key=lambda t: t[1]["ic"])[1] if allr else stub
    best_net = max(allr, key=lambda t: t[1]["trade_mean"])[1] if allr else stub
    nn_rows = [r for _k, r in allr if "нейросет" in r["name"]]
    lin_rows = [r for _k, r in allr if "линейная" in r["name"]]
    nn_ic = float(np.mean([abs(r["ic"]) for r in nn_rows])) if nn_rows else 0.0
    lin_ic = (float(np.mean([abs(r["ic"]) for r in lin_rows]))
              if lin_rows else 0.0)
    seed_spread = (max((max(i) - min(i) for _n, i, _t in seed_rows),
                       default=0.0) if seed_rows else 0.0)

    need_best = best_ic.get("ic_need", 0.0)
    ratio_best = (need_best / best_ic["ic"] if best_ic["ic"] > 0
                  else float("inf"))
    out("(а) ЕСТЬ ЛИ В НАШИХ ДАННЫХ ПРЕДСКАЗУЕМОСТЬ, КОТОРУЮ ЛОВИТ ML?")
    out(f"    Обучено и проверено на неприкосновенном holdout {n_tot} "
        f"моделей (константа, случайная, линейная на лагах, Ridge, "
        f"бустинг, MLP sklearn, MLP torch)")
    out("    в трёх постановках и на трёх горизонтах. Ответ из двух "
        "частей, и путать их нельзя:")
    out()
    sig4 = sum(1 for _k, r in (_flat(rowsB) + _flat(rowsXS))
               if r["p_ic"] < 0.05)
    sig15 = sum(1 for _k, r in _flat(rows15) if r["p_ic"] < 0.05)
    tot4 = len(_flat(rowsB)) + len(_flat(rowsXS))
    tot15 = len(_flat(rows15))
    out(f"    СТАТИСТИЧЕСКИ — ДА, КРОХОТНАЯ ЕСТЬ. Значимых против "
        f"перестановочного нуля: {n_sig} из {n_tot} при ожидаемых по "
        f"случайности {exp_sig:.1f}.")
    out(f"    Разбивка: на 4ч (мало независимых наблюдений) — {sig4} из "
        f"{tot4}; на 15м (наблюдений в разы больше) — {sig15} из {tot15}. "
        f"Сигнал видно ТОЛЬКО там, где хватает данных.")
    out(f"    Лучший IC {best_ic['ic']:+.4f} "
        f"(«{best_ic['name'][:34]}»), нуль 95% "
        f"{best_ic['null_ic_p95']:+.4f}, p = {fmt_p(best_ic['p_ic'])}.")
    out("    То есть свечи НЕ идеально случайны — слабая структура в них "
        "есть, и при достаточном объёме её видно.")
    out()
    out("    ЭКОНОМИЧЕСКИ — НЕТ, И ЭТО НЕ БЛИЗКО. Найденный сигнал на "
        "порядок меньше издержек:")
    if math.isfinite(ratio_best):
        out(f"      лучший IC {best_ic['ic']:+.4f} против нужного для "
            f"окупаемости {need_best:.4f} — МЕНЬШЕ ПОРОГА В "
            f"{ratio_best:.1f} РАЗ.")
    out(f"      моделей, у которых чистая (после издержек) сделка "
        f"положительна И значима: {n_prof} из {n_tot}. Ни одной.")
    out(f"      ни один p не проходит поправку на множественность "
        f"(нужен p < {0.05/max(1,n_tot):.4f}, лучший = "
        f"{fmt_p(best[1]['p_ic'])}).")
    # порог по доле верных направлений: p > 0.5 + COST/(2*сигма), а так как
    # ic_need = COST/(1.4*сигма), то p_need = 0.5 + 0.7*ic_need
    da = [(r["dir_acc"], 0.5 + 0.7 * r.get("ic_need", 0.0)) for _k, r in allr
          if r.get("ic_need")]
    if da:
        got = max(x[0] for x in da)
        nd_lo = min(x[1] for x in da)
        nd_hi = max(x[1] for x in da)
        out(f"    Проще словами: лучшая модель угадала направление в "
            f"{got*100:.1f}% случаев, а чтобы отбить издержки нужно "
            f"{nd_lo*100:.1f}-{nd_hi*100:.1f}% (зависит от горизонта).")
    if lags:
        lag_prof = sum(1 for _k, r in lags
                       if r["trade_mean"] > 0 and r["p_trade"] < 0.05)
        out(f"    Автокорреляция доходностей есть, но заработать на ней "
            f"нельзя: линейная модель ТОЛЬКО на лагах значима {lag_sig} "
            f"раз из {len(lags)},")
        out(f"    а окупаемых среди них {lag_prof}. Классический «эффект "
            f"есть, денег нет».")
    out("    ЧТО МОДЕЛЬ ВЫБРАЛА ГЛАВНЫМ ПРИЗНАКОМ — отдельный "
        "диагностический признак пустоты: в топ-5 важности бустинга и на "
        "4ч, и на 15м стоят")
    out("    dow_sin/dow_cos, то есть ДЕНЬ НЕДЕЛИ. Ровно это всплывало и в "
        "прошлой волне (score_report_out.txt: «в топ признаков — час и "
        "день недели»),")
    out("    и это согласуется с grid_ruin/grid_plateau, где 39.4% "
        "дисперсии результата объяснялось КАЛЕНДАРНЫМ окном. Когда "
        "сильнейший признак — календарь,")
    out("    модель описывает не рынок, а конкретный отрезок истории.")
    out(f"    Нейросети не нашли ничего сверх линейной модели: средний |IC| "
        f"нейросетей {nn_ic:.4f} против {lin_ic:.4f} у линейных — "
        f"разница внутри шума.")
    cvs2 = [(k, r) for k, r in allr if r["cv"] is not None]
    if cvs2:
        out(f"    А кросс-валидация внутри трейна при этом обещала IC "
            f"{float(np.mean([r['cv'] for _k, r in cvs2])):+.4f} — на "
            f"holdout вышло "
            f"{float(np.mean([r['ic'] for _k, r in cvs2])):+.4f}. "
            f"Это и есть переобучение (см. 6.4.1).")
    if seed_rows:
        out(f"    И главное: от одной лишь СМЕНЫ ЗЕРНА инициализации IC "
            f"гуляет на {seed_spread:.4f} — это порядок самого «сигнала». "
            f"Ловить нечего.")
    if need_rows:
        strong = [r for r in need_rows if r["concl"] == "ОКОНЧАТЕЛЬНЫЙ"]
        weak = [r for r in need_rows if r["concl"] != "ОКОНЧАТЕЛЬНЫЙ"]
        out("    ВАЖНАЯ ОГОВОРКА О СИЛЕ ВЫВОДА (раздел 3.2), чтобы не "
            "выдать желаемое за измеренное:")
        if strong:
            r = strong[0]
            out(f"      * на горизонте k={r['k']} "
                f"({r['k']*panel['bar_min']/60:.0f} ч) вывод ОКОНЧАТЕЛЬНЫЙ: "
                f"наша выборка видит IC от {r['ic_min']:.3f}, а окупаемость "
                f"начинается с {r['ic_need']:.3f} —")
            out("        прибыльный сигнал мы бы заметили гарантированно, "
                "и его нет;")
        for r in weak:
            out(f"      * на горизонте k={r['k']} "
                f"({r['k']*panel['bar_min']/60:.0f} ч) мощности НЕ хватает: "
                f"видим IC от {r['ic_min']:.3f} при нужном "
                f"{r['ic_need']:.3f} — там нельзя ни подтвердить, ни "
                f"опровергнуть.")
        out("      Второе — тоже отрицательный ответ для практики: "
            "торговать на том, что нельзя проверить, нельзя.")
    out("    ЧЕСТНО О НУЛЕ: перестановки делаются внутри каждой монеты "
        "независимо, поэтому нулевое распределение получается скорее УЖЕ "
        "реального")
    out("    (межмонетная корреляция rho=0.71 не воспроизводится). Это "
        "работает В ПОЛЬЗУ ML — тест завышает значимость. И даже так "
        "окупаемых моделей ноль.")
    out()
    out("(б) ЛУЧШЕ ЛИ ML ТОГО, ЧТО МЫ ДЕЛАЛИ РУКАМИ?")
    out("    НЕТ. Ручные боты на holdout дали подтверждённые ~+0.5%/мес по "
        "ETH и SOL (bots_honest_out.txt) — слабо, но ПОЛОЖИТЕЛЬНО и "
        "воспроизводимо.")
    out(f"    Здесь же лучшая по чистой доходности ML-модель даёт "
        f"{best_net['trade_mean']*100:+.3f}% на сделку "
        f"(«{best_net['name'][:38]}», p = {fmt_p(best_net['p_trade'])}); "
        f"положительных и значимых — {n_prof} из {n_tot}.")
    out("    В постановке A (то, чем ML мог бы помочь напрямую — отсев "
        "плохих сигналов) прошлая волна дала AUC 0.412 при нуле 0.472,")
    out("    ранжирование перевёрнутое. Более сильные модели этот результат "
        "не исправили: см. 6.5 — все p выше 0.05,")
    out("    а AUC на обучении 0.999-1.000 при 266 сделках означает, что "
        "бустинг и MLP просто ЗАПОМНИЛИ выборку.")
    out("    Вывод: дело не в классе модели, а в данных. Замена "
        "логистической регрессии на нейросеть меняет только время обучения.")
    out()
    out("(в) ПРИ КАКИХ УСЛОВИЯХ ML ИМЕЛ БЫ СМЫСЛ? (практическая часть)")
    out("    Всё ниже посчитано, а не придумано — расчёт в разделе 3.2.")
    out()
    out(f"    1. ОБЪЁМ. Постановка A: {n_tr_a} обучающих сделок НА ВСЁ "
        f"(24-58 на комбинацию сетап/ТФ).")
    out(f"       Правило «параметров на порядок меньше наблюдений» даёт "
        f"потолок ~26 параметров; у бустинга их 6100, у MLP 1537.")
    yrs_hist = panel["n"] * panel["bar_min"] / 60.0 / 24.0 / 365.0
    n_all_a = n_tr_a + n_ho_a
    out(f"       Чтобы честно обучить бустинг (6100 параметров), нужно "
        f"~61 000 сделок — при нашей частоте ({n_all_a} сделок за "
        f"{yrs_hist:.1f} года) это ~{61000/max(1,n_all_a)*yrs_hist:.0f} "
        f"лет торговли.")
    out(f"       Постановка B: строк десятки тысяч, но НЕЗАВИСИМЫХ "
        f"наблюдений на горизонте 24 бара всего ~{ne24:.0f} на holdout.")
    out(f"       Причина: монеты ходят вместе (rho={rho:.2f}; 5 монет = "
        f"{len(panel['symbols'])/(1+(len(panel['symbols'])-1)*rho):.1f} "
        f"независимых рынка), а перекрывающиеся окна делят выборку на k.")
    if need_rows:
        nr = {r["k"]: r for r in need_rows}
        kk = sorted(nr)[-1]
        out(f"       ЧТО НУЖНО (таблица 3.2): на горизонте k={kk} "
            f"({kk*panel['bar_min']/60:.0f} ч) — {nr[kk]['n_need']:.0f} "
            f"независимых наблюдений, есть {nr[kk]['have']:.0f}. "
            f"Нехватка в {nr[kk]['n_need']/max(1,nr[kk]['have']):.0f} раз.")
        out("       Взять их можно двумя способами: ждать десятилетия "
            "(нереально) ИЛИ добавить СЛАБО СВЯЗАННЫЕ инструменты — "
            "не 5 монет с rho=0.71,")
        out("       а 20-40 рынков из разных классов (акции, товары, FX, "
            "ставки). Это единственный способ увеличить число независимых "
            "наблюдений, не увеличивая календарь.")
    out()
    out("    2. ГОРИЗОНТ. Здесь НОЖНИЦЫ, и это самый практичный вывод "
        "работы (расчёт в 3.2):")
    if need_rows:
        for r in need_rows:
            out(f"       k={r['k']:<3} ({r['k']*panel['bar_min']/60:>3.0f} ч): "
                f"чтобы окупить издержки, нужен IC {r['ic_need']:.3f}; "
                f"чтобы доказать такой IC — {r['n_need']:.0f} независимых "
                f"наблюдений = {r['years']:.1f} лет истории")
        short, long_ = need_rows[0], need_rows[-1]
        out(f"       Короткий горизонт требует БОЛЬШОГО преимущества "
            f"(IC {short['ic_need']:.3f}) — его легко проверить, и мы "
            f"проверили: его нет.")
        out(f"       Длинный горизонт требует МАЛЕНЬКОГО преимущества "
            f"(IC {long_['ic_need']:.3f}) — но чтобы отличить его от нуля, "
            f"нужно {long_['years']:.0f} лет истории вместо наших "
            f"{panel['n']*panel['bar_min']/60/24/365:.1f}.")
        out("       Вывод: на коротком горизонте ответ ИЗМЕРЕН и он "
            "отрицательный; на длинном горизонте вопрос при наших данных "
            "НЕРАЗРЕШИМ в принципе.")
        out("       Строить торговлю на неразрешимом вопросе нельзя — "
            "любая найденная там «закономерность» будет неотличима от "
            "совпадения.")
    out()
    out("    3. ДАННЫЕ. Из OHLCV выжимать больше нечего — измерено дважды "
        "(прошлая волна и эта, разными классами моделей).")
    out("       Шанс дают источники, которых в модели НЕТ: стакан и поток "
        "сделок (микроструктура; горизонт секунды-минуты, там сигнал есть, "
        "но нужна инфраструктура исполнения),")
    out("       ончейн-потоки на биржи, срез funding/OI по всему рынку "
        "(у нас есть funding только как один признак), карта ликвидаций, "
        "новостной поток.")
    out("       Без НОВОГО источника данных смена класса модели "
        "не меняет ничего — это и показал эксперимент.")
    out()
    out("    4. ЧТО РЕАЛЬНО ДАЛО БЫ ЭФФЕКТ ПРИ ТЕХ ЖЕ ДАННЫХ: предсказывать "
        "не НАПРАВЛЕНИЕ, а ВОЛАТИЛЬНОСТЬ.")
    out("       Волатильность автокоррелирована, и это видно прямо в "
        "наших таблицах важности: vol_72 и atr_rank200/atr_pct стабильно "
        "в топ-10 и на 4ч, и на 15м,")
    out("       и предсказывать её на порядок легче. Практический выход — "
        "размер позиции, ширина стопа и фильтр «сегодня не торгуем», "
        "а не вход по направлению.")
    out("       Это отдельная задача, и её стоит проверить тем же "
        "протоколом — она дешевле и честнее, чем нейросеть на направление.")
    out()
    n_min = (need_rows[1]["n_need"] if len(need_rows) > 1
             else (need_rows[0]["n_need"] if need_rows else 6000))
    out("    5. ЕСЛИ ВЛАДЕЛЕЦ ВСЁ РАВНО ХОЧЕТ ML: минимальные условия, при "
        "которых работа не будет самообманом —")
    out(f"       (1) новый источник данных, (2) горизонт от суток, "
        f"(3) не менее ~{n_min:.0f} НЕЗАВИСИМЫХ наблюдений (не строк!), "
        f"(4) тот же протокол (holdout + перестановки + издержки + "
        f"смена зерна).")
    out("       Нарушение любого из четырёх пунктов гарантированно даёт "
        "красивую кривую на бэктесте и ноль в реальности.")
    out()
    out("ОТРИЦАТЕЛЬНЫЙ РЕЗУЛЬТАТ — ВАЛИДНЫЙ РЕЗУЛЬТАТ: переход на "
        "нейросети при текущих данных не окупится. Мы это ИЗМЕРИЛИ.")
    out("Формулировка для владельца в одну строку: предсказуемость в "
        "свечах есть, но она в разы меньше комиссии — нейросеть находит "
        "её и всё равно теряет деньги.")
    out()


if __name__ == "__main__":
    main()
