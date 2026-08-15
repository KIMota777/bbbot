# -*- coding: utf-8 -*-
"""Машинное обучение там, где у него впервые есть основание.

ПОЧЕМУ ПРЕЖНЯЯ ПОПЫТКА БЫЛА ОБРЕЧЕНА. Мета-разметка над правилами каталога
училась на нескольких сотнях сделок по пяти монетам. Модель с тридцатью
признаками на такой выборке не может ничего, кроме как выучить шум — и именно
это она и показала: прибавка +0.0101% на вход при собственной ошибке 0.0104%.

ЧТО ИЗМЕНИЛОСЬ. Появилась широкая выборка: 180 монет вместо пяти. Наблюдение
здесь — это не «сделка», а пара (монета, неделя), и таких пар набирается
порядка двадцати пяти тысяч вместо пятисот. Это первый раз за всю работу,
когда размер выборки хотя бы соответствует сложности модели.

ЗАДАЧА СТАВИТСЯ ПОПЕРЕЧНО, а не «вверх или вниз». Модель предсказывает не
доходность монеты, а её место ОТНОСИТЕЛЬНО остальных на той же неделе. Это
принципиально: общий ход рынка вычитается и не может быть выучен как ложный
сигнал, а именно он и губит модели, обученные предсказывать абсолютное
движение (они выучивают «крипта росла» и умирают, когда она перестаёт).

ЧЕСТНОСТЬ ОБЕСПЕЧЕНА ТРЕМЯ ВЕЩАМИ:
  * разбиение только по времени, с зазором в горизонт цели — иначе неделя из
    обучения перекрывается с неделей из проверки и ответ протекает;
  * сравнение с простой опорной линией (импульс за 30 дней). Модель обязана
    её побить, иначе она не нужна;
  * перестановка меток: те же признаки, перемешанные ответы. Показывает,
    сколько «качества» даёт сама процедура на заведомо пустых данных.

НЕЙРОННУЮ СЕТЬ ЗДЕСЬ НЕ БЕРЁМ, и это не лень. На табличных задачах такого
размера градиентный бустинг устойчиво не хуже сети, а переобучается заметно
меньше. Сеть имеет смысл там, где есть структура, которую бустинг не видит:
стакан заявок, последовательность сделок внутри дня. Этих данных у нас нет.
"""
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import rdata          # noqa: E402
import wide_xsec as wx  # noqa: E402
import long_test as lt  # noqa: E402

from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: E402
from sklearn.linear_model import Ridge  # noqa: E402
from scipy import stats  # noqa: E402

HORIZON = 7          # цель — доходность за следующую неделю
STEP = 7             # шаг между наблюдениями: без нахлёста
GAP = 2              # зазор между обучением и проверкой, в шагах


def build_dataset(panel):
    """Признаки и цель по парам (монета, неделя). Всё считается по прошлому."""
    days, close, turn = panel["days"], panel["close"], panel["turnover"]
    n, m = close.shape
    rows, ys, ts, syms = [], [], [], []
    for i in range(200, n - HORIZON, STEP):
        c_now = close[i, :]
        ok = np.isfinite(c_now) & (c_now > 0)
        if ok.sum() < 30:
            continue
        f = {}
        for L in (7, 14, 30, 60, 90, 180):
            past = close[i - L, :]
            with np.errstate(invalid="ignore", divide="ignore"):
                f["mom%d" % L] = np.where(np.isfinite(past) & (past > 0),
                                          c_now / past - 1.0, np.nan)
        win = close[max(0, i - 30):i + 1, :]
        lr = np.diff(np.log(np.where(win > 0, win, np.nan)), axis=0)
        f["vol30"] = np.nanstd(lr, axis=0)
        f["dd30"] = np.array([
            (np.nanmax(win[:, j]) - c_now[j]) / np.nanmax(win[:, j])
            if np.isfinite(np.nanmax(win[:, j])) and np.nanmax(win[:, j]) > 0
            else np.nan for j in range(m)])
        ma = np.nanmean(close[max(0, i - 50):i + 1, :], axis=0)
        f["dist_ma"] = np.where((ma > 0) & (f["vol30"] > 0),
                                (c_now / ma - 1.0) / (f["vol30"] * np.sqrt(50)),
                                np.nan)
        tw = turn[max(0, i - 30):i + 1, :]
        f["turn_log"] = np.log10(np.maximum(np.nanmedian(tw, axis=0), 1.0))
        f["turn_ratio"] = np.where(np.nanmedian(tw, axis=0) > 0,
                                   turn[i, :] / np.nanmedian(tw, axis=0),
                                   np.nan)
        # возраст инструмента: сколько дней у него уже есть цена
        f["age"] = np.array([np.sum(np.isfinite(close[:i + 1, j]))
                             for j in range(m)], dtype=float)
        # связь с биткоином за 60 дней
        try:
            b = panel["syms"].index("BTCUSDT")
            bw = np.diff(np.log(np.maximum(close[i - 60:i + 1, b], 1e-9)))
            cw = np.diff(np.log(np.maximum(close[i - 60:i + 1, :], 1e-9)), axis=0)
            sd = np.nanstd(cw, axis=0) * np.nanstd(bw)
            cov = np.nanmean((cw - np.nanmean(cw, axis=0)) *
                             (bw - np.nanmean(bw))[:, None], axis=0)
            f["btc_corr"] = np.where(sd > 0, cov / sd, np.nan)
        except ValueError:
            f["btc_corr"] = np.full(m, np.nan)

        fwd_c = close[i + HORIZON, :]
        with np.errstate(invalid="ignore", divide="ignore"):
            y = np.where(np.isfinite(fwd_c) & (c_now > 0),
                         fwd_c / c_now - 1.0, np.nan)
        good = ok & np.isfinite(y)
        for k in f:
            good &= np.isfinite(f[k])
        idx = np.flatnonzero(good)
        if len(idx) < 25:
            continue
        # ПОПЕРЕЧНАЯ ПОСТАНОВКА: вычитаем среднее недели у цели и нормируем
        # признаки внутри недели. Общий ход рынка уходит и не может быть
        # выучен как сигнал.
        yy = y[idx] - np.mean(y[idx])
        block = np.column_stack([_rank(f[k][idx]) for k in sorted(f)])
        rows.append(block)
        ys.append(yy)
        ts.append(np.full(len(idx), int(days[i])))
        syms.append(idx)
    if not rows:
        return None
    return dict(X=np.vstack(rows), y=np.concatenate(ys),
                t=np.concatenate(ts), names=sorted(f))


def _rank(v):
    """Место внутри недели, приведённое к отрезку от -1 до 1."""
    r = stats.rankdata(v)
    return 2.0 * (r - 1) / max(len(r) - 1, 1) - 1.0


def walk(ds, model_fn, folds=5):
    """Разбиение только по времени, с зазором. Возвращает предсказания."""
    ut = np.unique(ds["t"])
    n = len(ut)
    edges = np.linspace(int(n * 0.4), n, folds + 1).astype(int)
    preds = np.full(len(ds["y"]), np.nan)
    for k in range(folds):
        a, b = edges[k], edges[k + 1]
        if b <= a:
            continue
        tr_t = ut[:max(0, a - GAP)]          # зазор: цель длиной в горизонт
        te_t = ut[a:b]
        tr = np.isin(ds["t"], tr_t)
        te = np.isin(ds["t"], te_t)
        if tr.sum() < 2000 or te.sum() < 200:
            continue
        mdl = model_fn()
        mdl.fit(ds["X"][tr], ds["y"][tr])
        preds[te] = mdl.predict(ds["X"][te])
    return preds


def rank_ic(pred, y, t):
    """Связь предсказанного места с фактическим, по неделям."""
    out = []
    for tt in np.unique(t):
        m = (t == tt) & np.isfinite(pred)
        if m.sum() < 10:
            continue
        out.append(stats.spearmanr(pred[m], y[m]).statistic)
    a = np.array([x for x in out if np.isfinite(x)])
    return a


def portfolio(pred, y, t, top=0.2, cost=0.0025):
    """Доход портфеля из предсказаний: верхние в лонг, нижние в шорт."""
    rets = []
    for tt in np.unique(t):
        m = (t == tt) & np.isfinite(pred)
        if m.sum() < 20:
            continue
        p, yy = pred[m], y[m]
        k = max(1, int(len(p) * top))
        o = np.argsort(p)
        rets.append(float(np.mean(yy[o[-k:]]) - np.mean(yy[o[:k]])) / 2.0
                    - cost)
    return np.array(rets)


def main():
    print("Собираю панель по широкой выборке...")
    panel = lt.load_full_panel()
    print("монет %d, дней %d" % (len(panel["syms"]), len(panel["days"])))
    ds = build_dataset(panel)
    if ds is None:
        print("не удалось собрать наблюдения")
        return
    print("наблюдений (монета × неделя): %d, признаков %d, недель %d"
          % (len(ds["y"]), ds["X"].shape[1], len(np.unique(ds["t"]))))
    print("признаки: %s\n" % ", ".join(ds["names"]))

    MODELS = [
        ("опорная линия: импульс 30д", None),
        ("линейная (Ridge)", lambda: Ridge(alpha=10.0)),
        ("бустинг", lambda: HistGradientBoostingRegressor(
            max_depth=3, max_iter=200, learning_rate=0.05,
            l2_regularization=1.0, early_stopping=True, random_state=0)),
    ]

    print("%-28s %10s %10s %10s %10s"
          % ("модель", "связь IC", "ошибка", "в месяц", "недель+"))
    results = {}
    j_mom = ds["names"].index("mom30")
    for label, fn in MODELS:
        pred = ds["X"][:, j_mom] if fn is None else walk(ds, fn)
        ic = rank_ic(pred, ds["y"], ds["t"])
        pr = portfolio(pred, ds["y"], ds["t"])
        mo = float((1 + pr.mean()) ** (30.0 / 7) - 1) if len(pr) else 0.0
        se = float(ic.std(ddof=1) / np.sqrt(len(ic))) if len(ic) > 2 else 0.0
        results[label] = (ic, pr)
        print("%-28s %+9.4f %10.4f %+9.2f%% %9.0f%%"
              % (label, float(ic.mean()) if len(ic) else 0.0, se,
                 100 * mo, 100 * (pr > 0).mean() if len(pr) else 0))

    print("\nПЕРЕСТАНОВКА МЕТОК (те же признаки, ответы перемешаны внутри недели)")
    print("Показывает, сколько «качества» даёт сама процедура на пустых данных.")
    rng = np.random.default_rng(0)
    shuf = ds["y"].copy()
    for tt in np.unique(ds["t"]):
        m = ds["t"] == tt
        shuf[m] = rng.permutation(shuf[m])
    ds_sh = dict(ds, y=shuf)
    pred = walk(ds_sh, lambda: HistGradientBoostingRegressor(
        max_depth=3, max_iter=200, learning_rate=0.05,
        l2_regularization=1.0, early_stopping=True, random_state=0))
    ic_sh = rank_ic(pred, ds_sh["y"], ds_sh["t"])
    print("   бустинг на перемешанных ответах: связь %+.4f (ошибка %.4f)"
          % (float(ic_sh.mean()), float(ic_sh.std(ddof=1) / np.sqrt(len(ic_sh)))))

    print("\nВЫВОД")
    ic_b = results["бустинг"][0]
    ic_m = results["опорная линия: импульс 30д"][0]
    t_stat = (ic_b.mean() / (ic_b.std(ddof=1) / np.sqrt(len(ic_b)))
              if len(ic_b) > 2 and ic_b.std(ddof=1) > 0 else 0.0)
    beats = float(np.mean(ic_b - ic_m[:len(ic_b)] > 0)) if len(ic_m) else 0
    print("   связь бустинга с фактом: %+.4f, t = %.2f" % (ic_b.mean(), t_stat))
    print("   бьёт опорную линию в %.0f%% недель" % (100 * beats))
    if abs(t_stat) < 2.0:
        print("   Связь неотличима от нуля. Модель не научилась ничему, что")
        print("   переносится на новые недели.")
    else:
        print("   Связь значима — требует отдельного разбора и проверки на")
        print("   издержки и вместимость, прежде чем чему-либо верить.")


if __name__ == "__main__":
    main()
