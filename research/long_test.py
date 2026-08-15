# -*- coding: utf-8 -*-
"""Самая чистая проверка из возможных: правила без единого подобранного числа.

ПОЧЕМУ ЗДЕСЬ НЕ НУЖЕН ЗАКРЫТЫЙ ЭКЗАМЕН. Всё предыдущее исследование делило
выборку на части, потому что параметры выбирались по данным — а значит их надо
было проверять на других данных. Здесь параметров, выбранных по данным, НЕТ
ВОВСЕ: окна 30/60/90/120/180 дней и каналы 20/55 взяты из литературы
семидесятых годов и не подбирались. Переобучать нечего, поэтому вся доступная
история — законная проверка, и её можно использовать целиком.

Это принципиально меняет количество наблюдений:
  прежде  3.15 года × 5 монет, параметры подобраны  -> ответить нельзя
  теперь  до 5.5 лет × 180 монет, ничего не подобрано

ЧТО СЧИТАЕТСЯ. Импульс по времени: держим длинную позицию в монете, если она
выросла за последние L дней, короткую — если упала. Равные доли по всем
монетам, пересмотр раз в неделю. Итог по КАЖДОМУ окну и среднее по всем,
без выбора лучшего.

ДОСТОВЕРНОСТЬ считается блочным бутстрэпом по времени (блоки по 8 недель,
чтобы сохранить сцепление соседних недель) и перестановочным тестом — сдвигом
весов по кругу относительно цен. Сдвиг сохраняет всё: среднюю долю в рынке,
оборот, инерцию самих весов, — и рушит только их привязку к конкретному
моменту. Если настоящий результат не отличается от сдвинутых, значит правило
не знает о рынке ничего.

СМЕЩЕНИЕ ВЫЖИВШИХ остаётся: делистнутых контрактов Bybit не отдаёт.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import rdata      # noqa: E402
import wide_xsec as wx  # noqa: E402

LOOKBACKS = [30, 60, 90, 120, 180]
REBAL = 7
CAPITAL = 100_000


def load_full_panel(min_days=400):
    """Панель по ВСЕЙ доступной истории, без обрезки нашим окном."""
    meta = wx.load_universe()
    series = {}
    for m in meta:
        p = os.path.join(wx.WIDE, "daily_%s.npy" % m["symbol"])
        if not os.path.exists(p):
            continue
        a = np.load(p)
        if len(a) >= min_days:
            series[m["symbol"]] = a
    days = sorted(set().union(*[set(a[:, 0].astype(np.int64))
                                for a in series.values()]))
    idx = {d: i for i, d in enumerate(days)}
    syms = sorted(series)
    close = np.full((len(days), len(syms)), np.nan)
    turn = np.full((len(days), len(syms)), np.nan)
    for j, s in enumerate(syms):
        for row in series[s]:
            i = idx.get(int(row[0]))
            if i is not None:
                close[i, j] = row[4]
                turn[i, j] = row[6]
    return dict(days=np.array(days, dtype=np.int64), syms=syms,
                close=close, turnover=turn)


def weekly_returns(panel, lookback, shift=0):
    """Недельная доходность правила. shift — сдвиг весов для проверки."""
    days, close, turn = panel["days"], panel["close"], panel["turnover"]
    n, m = close.shape
    ws, rs = [], []
    for i in range(lookback + 1, n - REBAL, REBAL):
        past, now = close[i - lookback, :], close[i, :]
        ok = np.isfinite(past) & np.isfinite(now) & (past > 0)
        if ok.sum() < 20:
            continue
        r = np.full(m, np.nan)
        r[ok] = now[ok] / past[ok] - 1.0
        w = np.zeros(m)
        v = np.flatnonzero(ok)
        w[v] = np.sign(r[v]) / max(len(v), 1)
        j = min(i + REBAL, n - 1)
        fwd = np.zeros(m)
        okf = np.isfinite(close[j, :]) & np.isfinite(close[i, :]) & (close[i, :] > 0)
        fwd[okf] = close[j, okf] / close[i, okf] - 1.0
        ws.append((w, np.nan_to_num(turn[i, :], nan=1e9), fwd))
    if not ws:
        return np.array([])
    # сдвиг весов по кругу: тот же набор позиций, но не в свой момент
    k = len(ws)
    out = np.empty(k)
    prev = np.zeros(len(panel["syms"]))
    for t in range(k):
        w = ws[(t + shift) % k][0]
        tv, fwd = ws[t][1], ws[t][2]
        trade = np.abs(w - prev)
        sl = wx.slippage(trade * CAPITAL, tv)
        out[t] = float(np.sum(w * fwd) - np.sum(trade * (wx.TAKER + sl)))
        prev = w
    return out


def block_bootstrap(r, n_sims=5000, block=8, seed=0):
    rng = np.random.default_rng(seed)
    n = len(r)
    nb = max(1, n // block)
    means = np.empty(n_sims)
    for s in range(n_sims):
        st = rng.integers(0, max(1, n - block), nb)
        idx = np.concatenate([np.arange(i, i + block) for i in st])[:n]
        means[s] = float(r[np.clip(idx, 0, n - 1)].mean())
    return means


def main():
    panel = load_full_panel()
    days = panel["days"]
    import datetime as dt
    d0 = dt.datetime.fromtimestamp(int(days[0]) / 1000, dt.UTC).date()
    d1 = dt.datetime.fromtimestamp(int(days[-1]) / 1000, dt.UTC).date()
    yrs = (int(days[-1]) - int(days[0])) / rdata.DAY_MS / 365.0
    print("ВСЯ ДОСТУПНАЯ ИСТОРИЯ: %s .. %s (%.1f года), монет %d"
          % (d0, d1, yrs, len(panel["syms"])))
    print("Параметров, подобранных по данным, нет — поэтому вся история")
    print("является законной проверкой, и делить её не требуется.\n")

    print("%-8s %9s %9s %8s %10s %9s %9s"
          % ("окно", "в месяц", "за период", "Шарп", "просадка",
             "недель", "p (сдвиг)"))
    all_r = []
    for L in LOOKBACKS:
        r = weekly_returns(panel, L)
        if len(r) < 30:
            continue
        all_r.append(r)
        eq = np.cumprod(1 + r)
        peak = np.maximum.accumulate(eq)
        dd = float(np.max((peak - eq) / peak))
        mo = float((1 + r.mean()) ** (30.0 / REBAL) - 1)
        sh = (float(r.mean()) / float(r.std(ddof=1)) * np.sqrt(365.0 / REBAL)
              if r.std(ddof=1) > 0 else 0.0)
        # перестановочный тест: 199 круговых сдвигов
        shifts = []
        for s in range(1, 200):
            rs = weekly_returns(panel, L, shift=s * 7)
            if len(rs) == len(r):
                shifts.append(float(rs.mean()))
        p = (1 + sum(1 for x in shifts if x >= r.mean())) / (1 + len(shifts)) \
            if shifts else float("nan")
        print("%-8d %+8.2f%% %+8.1f%% %8.2f %9.1f%% %9d %9.3f"
              % (L, 100 * mo, 100 * (eq[-1] - 1), sh, 100 * dd, len(r), p))

    if not all_r:
        return
    k = min(len(x) for x in all_r)
    comb = np.mean([x[-k:] for x in all_r], axis=0)
    eq = np.cumprod(1 + comb)
    peak = np.maximum.accumulate(eq)
    dd = float(np.max((peak - eq) / peak))
    mo = float((1 + comb.mean()) ** (30.0 / REBAL) - 1)
    sh = float(comb.mean()) / float(comb.std(ddof=1)) * np.sqrt(365.0 / REBAL)
    print("\nСОСТАВНОЕ ПРАВИЛО (среднее всех окон, ничего не выбрано):")
    print("   в месяц %+.2f%% | за период %+.1f%% | Шарп %.2f | просадка %.1f%%"
          % (100 * mo, 100 * (eq[-1] - 1), sh, 100 * dd))

    means = block_bootstrap(comb)
    lo, hi = np.percentile(means, [2.5, 97.5])
    print("   бутстрэп 95%%: от %+.3f%% до %+.3f%% за неделю; ниже нуля %.0f%% прогонов"
          % (100 * lo, 100 * hi, 100 * (means < 0).mean()))
    print("   в месяц это от %+.2f%% до %+.2f%%"
          % (100 * ((1 + lo) ** (30.0 / REBAL) - 1),
             100 * ((1 + hi) ** (30.0 / REBAL) - 1)))

    print("\nВЫВОД")
    if (means < 0).mean() > 0.05:
        print("Интервал накрывает ноль. Даже на %.1f года и %d монетах, при"
              % (yrs, len(panel["syms"])))
        print("правилах без единого подобранного числа, отличить доход от нуля")
        print("не удаётся. Это уже не про метод отбора — отбирать здесь нечего.")
    else:
        print("Интервал НЕ накрывает ноль — это первый результат в работе,")
        print("который выдерживает проверку. Требует отдельного разбора.")


if __name__ == "__main__":
    main()
