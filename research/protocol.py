# -*- coding: utf-8 -*-
"""Протокол отбора: скользящая проверка вперёд, плато параметров, портфель.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Главный источник самообмана в этой работе — не движок
и не стратегия, а ПОРЯДОК ДЕЙСТВИЙ. Можно посчитать всё честно и всё равно
получить мираж, если параметры выбраны по тем же данным, на которых потом
меряется результат. Поэтому правила отбора вынесены сюда и одинаковы для всех
семейств.

ТРИ ПРАВИЛА

1. ПАРАМЕТРЫ ВЫБИРАЮТСЯ ТОЛЬКО ПО ПРОШЛОМУ. Окно обучения кончается там, где
   начинается окно проверки, и они не пересекаются ни на бар.

2. ВЫБИРАЕТСЯ ПЛАТО, А НЕ ПИК. Оценка сочетания параметров сглаживается по его
   соседям в сетке. Одиночный пик посреди провала — это почти всегда шум:
   стоит рынку сместиться, и параметр съезжает с пика. Плато переживает
   смещение. Эта замена одна убирает изрядную часть подгонки.

3. ОКНА НАРЕЗАЮТСЯ ИЗ ОДНОГО ПРОГОНА. Стратегия прогоняется по всей истории
   один раз, а результат окна получается пересчётом сделок, попавших в окно.
   Так можно, потому что размер позиции пропорционален капиталу: доля
   капитала, которую даёт сделка, не зависит от того, с какой суммы начали.
   Это не приближение, а тождество — и оно ускоряет перебор в десятки раз.
"""
import numpy as np

import metrics
import rdata

DAY = rdata.DAY_MS


class Tape:
    """Сделки как доли капитала — представление, не зависящее от суммы счёта.

    eq_low — худший плавающий капитал за время удержания, в долях капитала до
    сделки. Без него просадка считалась бы только по закрытиям, а закрывают
    счёт как раз внутри сделки.
    """

    # Только четыре ряда. Причины выхода, MAE/MFE и стороны в ленте не
    # хранятся намеренно: при переборе в памяти живут разом сотни лент, и
    # список питоновских строк на каждую сделку съедал больше, чем все числа
    # вместе взятые. Всё это доступно из полного прогона, когда он нужен —
    # у финалистов, а не у двадцати шести тысяч кандидатов.
    __slots__ = ("t_in", "t_out", "ret", "eq_low")

    def __init__(self, res):
        tr = res.trades
        n = len(tr)
        self.t_in = np.fromiter((t.t_in for t in tr), np.int64, n)
        self.t_out = np.fromiter((t.t_out for t in tr), np.int64, n)
        ret = np.empty(n)
        low = np.empty(n)
        for k, t in enumerate(tr):
            before = t.equity_after - t.pnl
            ret[k] = t.pnl / before if before > 1e-9 else -1.0
            low[k] = min(t.eq_low, 1.0 + ret[k])
        self.ret = ret
        self.eq_low = low

    def window(self, t0, t1):
        return np.flatnonzero((self.t_in >= t0) & (self.t_in < t1))


def replay(tape, t0, t1, cap_loss=True):
    """Метрики окна: во что превратился бы капитал на этих сделках.

    cap_loss — не давать капиталу уйти ниже нуля: доля -1.0 означает потерю
    всего, и дальше торговать нечем. Без этого ограничения серия крупных
    минусов даёт отрицательный капитал и бессмысленные проценты.
    """
    idx = tape.window(t0, t1)
    days = max((t1 - t0) / DAY, 1.0)
    if not len(idx):
        return dict(trades=0, ret=0.0, cagr=0.0, maxdd=0.0, sharpe=0.0,
                    wr=0.0, pf=0.0, mo_med=0.0, mo_mean=0.0, days=days,
                    ruined=False, score=-9.9)
    r = tape.ret[idx]
    lo = tape.eq_low[idx]
    eq = 1.0
    curve = [1.0]
    ruined = False
    for k in range(len(r)):
        curve.append(eq * lo[k])          # яма внутри сделки
        eq *= (1.0 + r[k])
        if cap_loss and eq <= 1e-9:
            eq = 0.0
            ruined = True
            curve.append(0.0)
            break
        curve.append(eq)
    v = np.array(curve)
    dd = metrics.max_drawdown(v)[0]
    ret = eq - 1.0
    cagr = (eq ** (365.0 / days) - 1.0) if eq > 0 else -1.0

    # месячная доходность — по календарным месяцам выхода из сделки
    mo = {}
    e = 1.0
    for k in range(len(r)):
        key = int((tape.t_out[idx[k]] - rdata.HIST_START_MS) // (30 * DAY))
        mo[key] = mo.get(key, 0.0) + np.log1p(max(r[k], -0.999))
        e *= (1 + r[k])
    mvals = np.array([np.expm1(x) for x in mo.values()]) if mo else np.array([])

    wins = r[r > 0]
    loss = r[r <= 0]
    pf = float(wins.sum() / abs(loss.sum())) if len(loss) and loss.sum() < 0 \
        else (9.99 if len(wins) else 0.0)
    sd = float(r.std(ddof=1)) if len(r) > 2 else 0.0
    # Шарп по сделкам, приведённый к году через их фактическую частоту.
    # Это грубее дневного Шарпа, но здесь он нужен только для СРАВНЕНИЯ
    # сочетаний между собой, а не для отчёта.
    per_year = len(r) / (days / 365.0)
    sharpe = (float(r.mean()) / sd * np.sqrt(per_year)) if sd > 0 else 0.0
    return dict(
        trades=len(r), ret=float(ret), cagr=float(cagr), maxdd=float(dd),
        sharpe=float(sharpe), wr=float((r > 0).mean()), pf=float(min(pf, 9.99)),
        mo_med=float(np.median(mvals)) if len(mvals) else 0.0,
        mo_mean=float(mvals.mean()) if len(mvals) else 0.0,
        days=days, ruined=ruined,
        score=objective(ret, dd, len(r), days, ruined))


def objective(ret, dd, n_trades, days, ruined):
    """Целевая функция отбора. Считается ТОЛЬКО на окне обучения.

    Что в ней заложено и почему:
      * доходность делится на просадку — цель задана как доход при ограничении
        риска, а не доход сам по себе;
      * просадка глубже 20%% штрафуется резко: это граница из задания, и
        стратегия, которая её нарушает, не годится ни при какой доходности;
      * мало сделок — меньше доверия: результат из десяти сделок неотличим от
        везения, поэтому оценка гасится множителем по числу сделок;
      * слив обнуляет всё.
    Никакого «максимального дохода» здесь нет намеренно.
    """
    if ruined or n_trades < 5:
        return -9.9
    per_year = n_trades / max(days / 365.0, 1e-6)
    ann = (1.0 + ret) ** (365.0 / max(days, 1.0)) - 1.0 if ret > -1 else -1.0
    d = max(dd, 0.02)
    s = ann / d
    if dd > 0.20:
        s -= 4.0 * (dd - 0.20) / 0.20          # за нарушение потолка риска
    conf = min(1.0, per_year / 12.0) ** 0.5    # доверие к числу наблюдений
    return float(s * conf)


# --- плато вместо пика ------------------------------------------------------

def neighbours(combo, grid, keys):
    """Соседи сочетания: сдвиг на один шаг по одной оси сетки."""
    out = []
    for k in keys:
        vals = grid[k]
        try:
            i = vals.index(combo[k])
        except ValueError:
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                c = dict(combo)
                c[k] = vals[j]
                out.append(c)
    return out


def plateau_scores(rows, grid):
    """Сгладить оценку по соседям. rows: [(combo, score), ...] -> [(combo, s)].

    Сглаженная оценка = среднее собственной оценки и оценок соседей, причём
    собственная берётся с двойным весом. Пик, окружённый провалом, оседает;
    ровная область сохраняет оценку. Именно это и есть требование «искать
    устойчивую область параметров, а не лучший параметр».
    """
    keys = sorted(grid)
    index = {tuple(c[k] for k in keys): s for c, s in rows}
    out = []
    for combo, s in rows:
        vals = [s, s]
        for nb in neighbours(combo, grid, keys):
            key = tuple(nb[k] for k in keys)
            if key in index:
                vals.append(index[key])
        out.append((combo, float(np.mean(vals)), s, len(vals) - 2))
    return out


# --- скользящая проверка вперёд --------------------------------------------

def folds(t_start, t_end, is_days=360, oos_days=90, anchored=False):
    """Окна вида «учимся на прошлом, проверяемся на следующем куске».

    anchored=True — окно обучения растёт от начала истории (так делают, когда
    считают, что рынок в целом однороден). anchored=False — окно обучения
    фиксированной длины и едет вперёд (так делают, когда считают, что рынок
    меняется). Крипта скорее второе, поэтому по умолчанию скользящее, но обе
    схемы прогоняются: расхождение между ними само по себе диагноз.
    """
    out = []
    cur = t_start + is_days * DAY
    while cur + oos_days * DAY <= t_end:
        a = t_start if anchored else cur - is_days * DAY
        out.append((a, cur, cur, cur + oos_days * DAY))
        cur += oos_days * DAY
    return out


def walk_forward(tapes, grid, is_days=360, oos_days=90, anchored=False,
                 t_start=None, t_end=None, min_trades=5):
    """Полная проверка вперёд по одному активу.

    tapes: {ключ_сочетания: Tape}. На каждом окне выбираем сочетание с лучшей
    СГЛАЖЕННОЙ оценкой по обучению и записываем его результат на проверке.
    Возвращает список окон и склеенную кривую внеобучающих кусков.
    """
    t_start = t_start or rdata.HIST_START_MS
    t_end = t_end or rdata.VAL_END_MS
    keys = sorted(grid)
    ff = folds(t_start, t_end, is_days, oos_days, anchored)
    out = []
    for (a, b, c, d) in ff:
        rows = []
        for ck, tape in tapes.items():
            m = replay(tape, a, b)
            if m["trades"] >= min_trades:
                rows.append((dict(zip(keys, ck)), m["score"]))
        if not rows:
            out.append(dict(is_from=a, is_to=b, oos_from=c, oos_to=d,
                            chosen=None, oos=None))
            continue
        sm = plateau_scores(rows, grid)
        sm.sort(key=lambda x: -x[1])
        best = sm[0][0]
        bk = tuple(best[k] for k in keys)
        oos = replay(tapes[bk], c, d)
        out.append(dict(is_from=a, is_to=b, oos_from=c, oos_to=d,
                        chosen=best, chosen_key=bk, is_score=sm[0][2],
                        smooth=sm[0][1], oos=oos,
                        n_candidates=len(rows)))
    return out


def stitch(wf, tapes):
    """Склеить внеобучающие куски окон в одну кривую капитала.

    Это и есть главный результат протокола: доходность, которую стратегия
    показала бы, если бы её параметры каждый раз выбирались только по прошлому.
    Всё остальное — вспомогательные числа.
    """
    eq = 1.0
    curve = [1.0]
    times = []
    n_tr = 0
    for f in wf:
        if not f.get("chosen"):
            continue
        tape = tapes[f["chosen_key"]]
        idx = tape.window(f["oos_from"], f["oos_to"])
        for k in idx:
            curve.append(eq * tape.eq_low[k])
            eq *= (1 + tape.ret[k])
            curve.append(max(eq, 0.0))
            times.append(int(tape.t_out[k]))
            n_tr += 1
            if eq <= 1e-9:
                break
        if eq <= 1e-9:
            break
    v = np.array(curve)
    dd = metrics.max_drawdown(v)[0]
    days = sum((f["oos_to"] - f["oos_from"]) / DAY for f in wf
               if f.get("chosen"))
    ret = eq - 1.0
    return dict(trades=n_tr, ret=float(ret), maxdd=float(dd),
                days=days, curve=v, times=times,
                cagr=float(eq ** (365.0 / max(days, 1)) - 1.0) if eq > 0
                else -1.0,
                mo=float((1 + ret) ** (30.0 / max(days, 1)) - 1.0)
                if ret > -1 else -1.0)
