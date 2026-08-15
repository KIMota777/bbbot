# -*- coding: utf-8 -*-
u"""ГИПОТЕЗА 1: перевес живёт на ДНЕВНОМ горизонте, куда автор не заглядывал.

Автор считал 1ч и 4ч. Классический перевес управляемых фьючерсов (то самое
«time-series momentum», Moskowitz-Ooi-Pedersen) живёт на горизонте месяцев, и
там издержки почти не мешают: сделок единицы в год.

ЧТО ЗДЕСЬ ВАЖНО И ЧЕГО ЛЕГКО НЕ ЗАМЕТИТЬ. Параметры НЕ ПОДБИРАЮТСЯ. Взяты
канонические, известные из литературы до всякого счёта: импульс за 1, 3, 6
месяцев; Дончиан 20 и 55 дней; наклон средней. Ни одна цифра здесь не выбрана
по результату. Это и есть смысл проверки: если перевес на дневном горизонте
есть, он обязан быть виден БЕЗ отбора. Если он появляется только после отбора
лучшего из десяти — это тот же самый мираж, о котором говорит вывод автора.

ГЛАВНЫЙ КОНТРОЛЬ — «ВСЕГДА ЛОНГ». Обучающий период почти весь бычий. Любое
правило с перекосом в длинную сторону покажет прибыль просто потому, что рынок
рос. Поэтому рядом с каждым правилом печатается «всегда лонг» с той же
нормировкой по волатильности, и главный вопрос не «в плюсе ли правило», а
«лучше ли оно, чем просто держать».
"""
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import rdata                     # noqa: E402
import refute_horizon_lib as L   # noqa: E402

TARGET_VOL = 0.40      # целевая годовая волатильность портфеля, доли
VOL_N = 30             # окно оценки волатильности, дней
LEV_CAP = 3.0          # потолок веса на одну монету


def realized_vol(c, n):
    u"""Годовая волатильность по дневным логарифмическим приращениям.

    Значение на баре i считается по приращениям, ЗАКОНЧИВШИМСЯ на баре i, и
    используется для веса, который начнёт действовать на баре i+1.
    """
    r = np.zeros(len(c))
    r[1:] = np.log(c[1:] / c[:-1])
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    csum = np.cumsum(r)
    csum2 = np.cumsum(r * r)
    for i in range(n, len(c)):
        s = csum[i] - csum[i - n]
        s2 = csum2[i] - csum2[i - n]
        var = max(s2 / n - (s / n) ** 2, 1e-12)
        out[i] = np.sqrt(var * 365.0)
    return out


# --- правила направления (каждое возвращает -1/0/+1 по каждому бару) --------

def dir_tsmom(q, look):
    c = q["c"]
    d = np.zeros(len(c))
    if len(c) > look:
        d[look:] = np.sign(c[look:] / c[:-look] - 1.0)
    return d


def dir_donchian(q, n):
    u"""Пробой канала за n дней. Канал строится по барам СТРОГО ДО текущего:
    иначе сегодняшний максимум входит в собственный порог и пробой невозможен.
    """
    h, l, c = q["h"], q["l"], q["c"]
    m = len(c)
    d = np.zeros(m)
    hi = np.full(m, np.nan)
    lo = np.full(m, np.nan)
    for i in range(n, m):
        hi[i] = h[i - n:i].max()
        lo[i] = l[i - n:i].min()
    d[c > hi] = 1.0
    d[c < lo] = -1.0
    # состояние держится до противоположного пробоя (механика черепах)
    st = 0.0
    for i in range(m):
        if d[i] != 0:
            st = d[i]
        else:
            d[i] = st
    return d


def dir_maslope(q, n):
    u"""Наклон скользящей средней за n дней: средняя выше, чем n/4 дня назад."""
    c = q["c"]
    m = len(c)
    ma = np.full(m, np.nan)
    cs = np.concatenate([[0.0], np.cumsum(c)])
    ma[n - 1:] = (cs[n:] - cs[:-n]) / n
    k = max(1, n // 4)
    d = np.zeros(m)
    ok = np.zeros(m, dtype=bool)
    ok[n - 1 + k:] = True
    sl = np.full(m, np.nan)
    sl[k:] = ma[k:] - ma[:-k]
    d[ok & (sl > 0)] = 1.0
    d[ok & (sl < 0)] = -1.0
    return d


def dir_always_long(q, _):
    return np.ones(len(q["c"]))


RULES = [
    (u"импульс 30д",   dir_tsmom, 30),
    (u"импульс 60д",   dir_tsmom, 60),
    (u"импульс 90д",   dir_tsmom, 90),
    (u"импульс 120д",  dir_tsmom, 120),
    (u"импульс 180д",  dir_tsmom, 180),
    (u"Дончиан 20",    dir_donchian, 20),
    (u"Дончиан 55",    dir_donchian, 55),
    (u"наклон MA 50",  dir_maslope, 50),
    (u"наклон MA 100", dir_maslope, 100),
    (u"ВСЕГДА ЛОНГ",   dir_always_long, 0),
]


def make_weights(fn, arg, side=0):
    u"""Веса: направление правила, нормированное на волатильность монеты.

    side=+1 — только лонги, -1 — только шорты, 0 — обе стороны.
    """
    def build(times, px):
        out = {}
        syms = sorted(px)
        for s in syms:
            q = px[s]
            d = fn(q, arg)
            if side > 0:
                d = np.maximum(d, 0.0)
            elif side < 0:
                d = np.minimum(d, 0.0)
            v = realized_vol(q["c"], VOL_N)
            k = np.where(np.isfinite(v) & (v > 0), TARGET_VOL / np.maximum(v, 1e-6), 0.0)
            k = np.clip(k, 0.0, LEV_CAP)
            w = d * k / len(syms)
            out[s] = np.where(np.isfinite(w), w, 0.0)
        return out
    return build


def run_on(split, build, symbols=None, tf="D"):
    symbols = symbols or rdata.SYMBOLS
    t, px = L.panel(symbols, tf)
    w_all = build(t, px)                     # веса считаются по ВСЕЙ истории,
    m = L.in_split(t, split)                 # но берётся только нужный кусок:
    idx = np.flatnonzero(m)                  # правило причинно, значит вес на
    if len(idx) < 10:                        # баре i не зависит от того, где
        return None                          # мы нарезали окно
    t2 = t[idx]
    px2 = {s: {k: v[idx] for k, v in q.items() if k != "t"}
           for s, q in px.items()}
    w2 = {s: w_all[s][idx] for s in w_all}
    r = L.WSim(t2, px2, w2).run()
    r.update(L.curve_stats(r))
    return r


def main():
    t, px = L.panel(rdata.SYMBOLS, "D")
    print(u"Дневные бары, %d дней, %d монет. Целевая волатильность %.0f%% "
          u"годовых.\n" % (len(t), len(rdata.SYMBOLS), 100 * TARGET_VOL))

    # причинность — механически, для каждого правила
    for name, fn, arg in RULES:
        L.refute_causal_w(make_weights(fn, arg), t, px)
    print(u"Причинность всех %d правил проверена порчей будущего.\n" % len(RULES))

    for split, title in (("train", u"ОБУЧЕНИЕ (740 дней)"),
                         ("val", u"ПРОВЕРКА (244 дня, при отборе не читалась)")):
        print(u"=== %s ===" % title)
        print(u"%-16s %9s %9s %9s %9s %8s %8s"
              % (u"правило", u"итог", u"в месяц", u"медиана", u"просадка",
                 u"Шарп", u"оборот"))
        for name, fn, arg in RULES:
            r = run_on(split, make_weights(fn, arg))
            if r is None:
                continue
            print(u"%-16s %+8.1f%% %+8.2f%% %+8.2f%% %8.1f%% %8.2f %7.1fx"
                  % (name, 100 * r["ret"], 100 * r["mo"], 100 * r["mo_med"],
                     100 * r["maxdd"], r["sharpe"],
                     r["turnover"] / 10000.0))
        print()

    # сколько вообще можно различить на таком отрезке
    for split in ("train", "val"):
        d = (rdata.SPLITS[split][1] - rdata.SPLITS[split][0]) / L.DAY / 365.0
        print(u"На отрезке «%s» (%.2f года) ошибка оценки Шарпа около %.2f: "
              u"Шарп ниже %.2f от нуля неотличим."
              % (split, d, 1 / np.sqrt(d), 2 / np.sqrt(d)))


if __name__ == "__main__":
    main()
