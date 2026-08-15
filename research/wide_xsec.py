# -*- coding: utf-8 -*-
"""Широкая выборка: тот же вопрос, но на сотнях монет вместо пяти.

ЗАЧЕМ ИМЕННО ТАК. Прежнее исследование упёрлось не в метод, а в количество
наблюдений. Здесь ничего нового не изобретается: берутся канонические правила
без единого подобранного числа и применяются ко всем монетам сразу. Если
эффект есть, на трёхстах инструментах он должен проступить; если и там его нет,
вопрос закрыт куда убедительнее, чем на пяти.

ПРАВИЛА ОБЪЯВЛЕНЫ ЗАРАНЕЕ И НЕ ПОДБИРАЮТСЯ:
  * поперечный импульс — купить верхнюю часть списка по доходности за L дней,
    продать нижнюю, пересмотр раз в неделю. L из {7, 14, 30, 60, 90};
  * импульс по времени — купить всё, что выросло за L дней, продать всё, что
    упало, равными долями.
Решение принимается по СРЕДНЕМУ ВСЕХ L, а не по лучшему: выбор лучшего и есть
та ошибка, которая погубила прошлый заход.

ИЗДЕРЖКИ ЗАВИСЯТ ОТ РАЗМЕРА. Это главное отличие от наивных исследований по
широкой выборке. У монеты с оборотом 2 млн в день нельзя торговать так же
дёшево, как у биткоина: чем больше доля дневного оборота, тем хуже цена.
Поэтому проскальзывание считается от отношения размера позиции к обороту, и
результат приводится сразу для нескольких размеров счёта — потому что
стратегия, живая на десяти тысячах и мёртвая на миллионе, это стратегия с
очень ограниченной вместимостью, и знать об этом надо заранее.

СМЕЩЕНИЕ ВЫЖИВШИХ НЕ УСТРАНЕНО и устранить его нечем: Bybit не отдаёт
делистнутые контракты. Оно завышает результат длинной стороны и занижает
результат короткой.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import rdata  # noqa: E402

WIDE = os.path.join(DIR, "data_wide")
LOOKBACKS = [7, 14, 30, 60, 90]
REBALANCE_D = 7
DECILE = 0.20            # верхние и нижние 20% списка
TAKER = rdata.TAKER_FEE
BASE_SLIP = 0.0005       # даже в самом ликвидном стакане не бесплатно


def load_universe():
    path = os.path.join(WIDE, "universe.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["kept"]


def load_panel(meta, min_days=400):
    """Свести монеты в одну таблицу по общей сетке дат.

    Пропуски остаются NaN: монета, которой ещё не было, не должна попадать в
    список кандидатов задним числом. Это ровно то место, где в таких
    исследованиях чаще всего протекает будущее.
    """
    series = {}
    for m in meta:
        p = os.path.join(WIDE, "daily_%s.npy" % m["symbol"])
        if not os.path.exists(p):
            continue
        a = np.load(p)
        if len(a) < min_days:
            continue
        series[m["symbol"]] = a
    if not series:
        return None
    days = sorted(set().union(*[set(a[:, 0].astype(np.int64))
                                for a in series.values()]))
    days = [d for d in days if rdata.HIST_START_MS <= d <= rdata.HIST_END_MS]
    idx = {d: i for i, d in enumerate(days)}
    syms = sorted(series)
    n, m_ = len(days), len(syms)
    close = np.full((n, m_), np.nan)
    turn = np.full((n, m_), np.nan)
    for j, s in enumerate(syms):
        a = series[s]
        for row in a:
            i = idx.get(int(row[0]))
            if i is not None:
                close[i, j] = row[4]
                turn[i, j] = row[6]
    return dict(days=np.array(days, dtype=np.int64), syms=syms,
                close=close, turnover=turn)


def slippage(notional, turnover_usd):
    """Проскальзывание как функция доли дневного оборота.

    Модель грубая, но её направление верное и она консервативна: торгуя 1%
    дневного оборота, платим примерно 20 базисных пунктов сверху базовых пяти.
    Настоящая зависимость ближе к корню, здесь взята линейная — то есть
    крупные заявки наказываются сильнее, чем в жизни. Пусть лучше так.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(turnover_usd > 0, notional / turnover_usd, 1.0)
    return BASE_SLIP + 0.020 * np.nan_to_num(share, nan=1.0, posinf=1.0)


def run(panel, lookback, capital, mode="xsec", t0=None, t1=None):
    """Один прогон. Возвращает кривую капитала по датам пересмотра."""
    days, close, turn = panel["days"], panel["close"], panel["turnover"]
    n, m = close.shape
    t0 = t0 if t0 is not None else int(days[0])
    t1 = t1 if t1 is not None else int(days[-1]) + 1

    eq = 1.0
    curve, times = [1.0], [int(days[0])]
    prev_w = np.zeros(m)
    for i in range(lookback + 1, n - 1, REBALANCE_D):
        if not (t0 <= int(days[i]) < t1):
            prev_w = np.zeros(m)
            continue
        # доходность за окно, посчитанная ТОЛЬКО по прошлым барам
        past = close[i - lookback, :]
        now = close[i, :]
        ok = np.isfinite(past) & np.isfinite(now) & (past > 0)
        if ok.sum() < 20:
            continue
        r = np.full(m, np.nan)
        r[ok] = now[ok] / past[ok] - 1.0

        w = np.zeros(m)
        if mode == "xsec":
            valid = np.flatnonzero(ok)
            order = valid[np.argsort(r[valid])]
            k = max(1, int(len(order) * DECILE))
            longs, shorts = order[-k:], order[:k]
            w[longs] = 0.5 / k
            w[shorts] = -0.5 / k
        else:                                   # импульс по времени
            valid = np.flatnonzero(ok)
            s = np.sign(r[valid])
            if not np.any(s):
                continue
            w[valid] = s / max(len(valid), 1)

        # издержки за изменение позиции
        trade = np.abs(w - prev_w)
        notional = trade * capital
        sl = slippage(notional, np.nan_to_num(turn[i, :], nan=1e9))
        cost = float(np.sum(trade * (TAKER + sl)))

        # доход за следующий отрезок до пересмотра
        j = min(i + REBALANCE_D, n - 1)
        fwd = np.full(m, 0.0)
        okf = np.isfinite(close[j, :]) & np.isfinite(close[i, :]) & (close[i, :] > 0)
        fwd[okf] = close[j, okf] / close[i, okf] - 1.0
        gross = float(np.sum(w * fwd))

        eq *= (1.0 + gross - cost)
        prev_w = w
        curve.append(max(eq, 0.0))
        times.append(int(days[j]))
        if eq <= 0:
            break
    return np.array(curve), np.array(times)


def stats(curve, times):
    if len(curve) < 3:
        return None
    days = max((times[-1] - times[0]) / rdata.DAY_MS, 1)
    ret = curve[-1] - 1.0
    peak = np.maximum.accumulate(curve)
    dd = float(np.max(np.where(peak > 0, (peak - curve) / peak, 0)))
    r = np.diff(curve) / np.maximum(curve[:-1], 1e-9)
    sd = float(r.std(ddof=1)) if len(r) > 2 else 0.0
    per_year = 365.0 / REBALANCE_D
    return dict(ret=float(ret), maxdd=dd,
                mo=float((1 + ret) ** (30.0 / days) - 1) if ret > -1 else -1,
                sharpe=(float(r.mean()) / sd * np.sqrt(per_year)) if sd > 0 else 0.0,
                periods=len(r))


def main():
    meta = load_universe()
    print("монет в выборке: %d" % len(meta))
    panel = load_panel(meta)
    if not panel:
        print("нет данных — сначала wide_fetch.py")
        return
    print("сетка дат: %d дней, монет в таблице: %d"
          % (len(panel["days"]), len(panel["syms"])))
    print("НАПОМИНАНИЕ: только торгующиеся сейчас — смещение выживших не устранено.\n")

    for capital in (10_000, 100_000, 1_000_000):
        print("=" * 74)
        print("РАЗМЕР СЧЁТА $%s" % f"{capital:,}")
        print("=" * 74)
        for mode, label in (("xsec", "поперечный импульс (лонг сильных, шорт слабых)"),
                            ("tsmom", "импульс по времени (по знаку доходности)")):
            print("\n%s" % label)
            print("%-8s | %-30s | %-30s"
                  % ("окно", "обучение+проверка", "ЭКЗАМЕН"))
            print("%-8s | %8s %8s %6s | %8s %8s %6s"
                  % ("", "месяц", "просад", "Шарп", "месяц", "просад", "Шарп"))
            tv, te = [], []
            for L in LOOKBACKS:
                c1, t1_ = run(panel, L, capital, mode,
                              *rdata.SPLITS["trainval"])
                c2, t2_ = run(panel, L, capital, mode, *rdata.SPLITS["test"])
                s1, s2 = stats(c1, t1_), stats(c2, t2_)
                if not s1 or not s2:
                    continue
                tv.append(s1["mo"])
                te.append(s2["mo"])
                print("%-8d | %+7.2f%% %7.1f%% %6.2f | %+7.2f%% %7.1f%% %6.2f"
                      % (L, 100 * s1["mo"], 100 * s1["maxdd"], s1["sharpe"],
                         100 * s2["mo"], 100 * s2["maxdd"], s2["sharpe"]))
            if tv:
                print("%-8s | %+7.2f%% %16s | %+7.2f%%"
                      % ("СРЕДНЕЕ", 100 * np.mean(tv), "",
                         100 * np.mean(te)))
                print("   (решение принимается по среднему, а не по лучшему окну)")
        print()


if __name__ == "__main__":
    main()
