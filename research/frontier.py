# -*- coding: utf-8 -*-
"""Граница риска: сколько даёт стратегия, ужатая ровно до просадки 20%.

ЗАЧЕМ. Сравнивать стратегии по доходности бессмысленно, пока у них разная
просадка: любую доходность можно поднять, увеличив долю риска, и вместе с ней
поднимется просадка. Поэтому все кандидаты приводятся к ОДНОЙ просадке — той,
что задана ограничением, — и только после этого сравниваются по доходности.

Так вопрос задания превращается в проверяемый: «какая доходность достижима,
если просадка не должна выходить за 20%». Ответ на него может оказаться
+1% в месяц — и это будет честный ответ, а не неудача.

КАК СЧИТАЕТСЯ. Доля риска умножается на k, доходность каждой сделки умножается
на k, и подбирается такое k, при котором просадка равна цели. Приближение:
при больших k упирается потолок плеча и связь перестаёт быть линейной, поэтому
k ограничено сверху тройкой, а случаи упора отмечаются.

ДВЕ ЦЕЛИ. По истории — просадка на той единственной дорожке, которая случилась.
По Монте-Карло (95-й перцентиль) — просадка, глубже которой уходит лишь каждый
двадцатый возможный порядок сделок. Вторая честнее: история дала нам один
порядок из многих, и он мог быть удачным.
"""
import numpy as np

import metrics
import portfolio


def scale_curve(trades_by_symbol, k, start=10000.0):
    """Кривая капитала при доле риска, умноженной на k."""
    return portfolio.combine(trades_by_symbol, risk_each=k, start=start)


def find_k(trades_by_symbol, target_dd=0.20, use_mc=False, lo=0.05, hi=3.0,
           iters=22, n_sims=3000):
    """Подобрать долю риска под заданную просадку. Двоичный поиск.

    Просадка растёт по k монотонно (больше риск — глубже яма), поэтому поиск
    корректен. Если даже при минимальном k просадка выше цели, стратегия
    непригодна ни при каком риске — так и возвращается.
    """
    def dd_of(k):
        if use_mc:
            mc = portfolio.monte_carlo_portfolio(trades_by_symbol, risk_each=k,
                                                 n_sims=n_sims)
            return mc.get("dd_p95", 1.0) if mc else 1.0
        return scale_curve(trades_by_symbol, k)["maxdd"]

    if dd_of(lo) > target_dd:
        return None, dd_of(lo)
    a, b = lo, hi
    if dd_of(hi) <= target_dd:
        return hi, dd_of(hi)
    for _ in range(iters):
        m = 0.5 * (a + b)
        if dd_of(m) <= target_dd:
            a = m
        else:
            b = m
    return a, dd_of(a)


def merge(list_of_trade_dicts, weights=None):
    """Слить сделки нескольких стратегий в один портфель.

    Ключ — пара (монета, стратегия): иначе сделки разных стратегий по одной
    монете затрут друг друга. Веса делят риск между стратегиями.
    """
    out = {}
    n = len(list_of_trade_dicts)
    w = weights or [1.0 / n] * n
    for i, d in enumerate(list_of_trade_dicts):
        for sym, trs in d.items():
            key = "%s#%d" % (sym, i)
            out[key] = [dict(t, ret=t["ret"] * w[i] * n,
                             low=1.0 + (t["low"] - 1.0) * w[i] * n)
                        for t in trs]
    return out


def correlation(trade_dicts, names, bucket_days=7):
    """Корреляция кривых стратегий по недельной доходности.

    Складывать в портфель имеет смысл слабо связанные вещи. Если две стратегии
    ходят вместе, их сумма — это одна ставка двойного размера, а не
    диверсификация.
    """
    import rdata
    series = []
    for d in trade_dicts:
        buckets = {}
        for trs in d.values():
            for t in trs:
                b = int(t["t_out"] // (bucket_days * rdata.DAY_MS))
                buckets[b] = buckets.get(b, 0.0) + t["ret"]
        series.append(buckets)
    keys = sorted(set().union(*[set(s) for s in series])) if series else []
    m = np.array([[s.get(k, 0.0) for k in keys] for s in series])
    if m.shape[1] < 5:
        return None, keys
    return np.corrcoef(m), keys
