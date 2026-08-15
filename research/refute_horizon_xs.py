# -*- coding: utf-8 -*-
u"""ГИПОТЕЗЫ 2 и 3: собрать перевес ИНАЧЕ, а не искать другой сигнал.

2. ПОПЕРЕЧНЫЙ ИМПУЛЬС. Каждую неделю покупать сильнейшую монету и продавать
   слабейшую в равном объёме. Это утверждение совсем другого рода, чем всё в
   каталоге автора: не «рынок пойдёт вверх», а «эта монета обгонит ту». Оно
   рыночно-нейтрально, и бычий период не может его подкрасить.

3. РАВНОВЕСНЫЙ ПОРТФЕЛЬ С НОРМИРОВКОЙ ПО ВОЛАТИЛЬНОСТИ. Доля монеты обратна
   её волатильности, пересчёт раз в неделю. Проверяется не доходность, а
   риск-доходность против «купил и держу равными долями».

Пять монет для поперечного импульса — мало: это одна пара «лучшая против
худшей», и разброс оценки огромен. Это ограничение названо здесь, а не
спрятано: отрицательный ответ на пяти монетах не закрывает вопрос совсем,
но и положительный на пяти монетах ничего бы не доказал.
"""
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import rdata                     # noqa: E402
import refute_horizon_lib as L   # noqa: E402
import refute_horizon_ts as TS   # noqa: E402


def xs_weights(look_days, n_side=1, gross=1.0, hold=7):
    u"""Поперечный импульс: ранжируем монеты по доходности за look_days.

    Позиция пересматривается раз в hold дней (по умолчанию неделя): в
    промежутке веса не трогаем, иначе оборот съест всё. Ранг считается по
    закрытию бара i и действует с открытия i+1.
    """
    def build(times, px):
        syms = sorted(px)
        n = len(times)
        rets = np.full((len(syms), n), np.nan)
        for j, s in enumerate(syms):
            c = px[s]["c"]
            if n > look_days:
                rets[j, look_days:] = c[look_days:] / c[:-look_days] - 1.0
        w = np.zeros((len(syms), n))
        cur = np.zeros(len(syms))
        for i in range(n):
            if i % hold == 0:
                col = rets[:, i]
                if np.isfinite(col).sum() == len(syms):
                    order = np.argsort(col)
                    cur = np.zeros(len(syms))
                    for k in range(n_side):
                        cur[order[-(k + 1)]] = +gross / (2.0 * n_side)
                        cur[order[k]] = -gross / (2.0 * n_side)
            w[:, i] = cur
        return {s: w[j] for j, s in enumerate(syms)}
    return build


def riskparity_weights(vol_n=30, gross=1.0, hold=7, long_only=True):
    u"""Доля монеты обратна её волатильности, пересчёт раз в hold дней."""
    def build(times, px):
        syms = sorted(px)
        n = len(times)
        iv = np.zeros((len(syms), n))
        for j, s in enumerate(syms):
            v = TS.realized_vol(px[s]["c"], vol_n)
            iv[j] = np.where(np.isfinite(v) & (v > 0), 1.0 / np.maximum(v, 1e-6), 0.0)
        w = np.zeros((len(syms), n))
        cur = np.zeros(len(syms))
        for i in range(n):
            if i % hold == 0:
                col = iv[:, i]
                ssum = col.sum()
                cur = (col / ssum * gross) if ssum > 0 else np.zeros(len(syms))
            w[:, i] = cur
        return {s: w[j] for j, s in enumerate(syms)}
    return build


def equal_weights(gross=1.0, hold=7):
    def build(times, px):
        syms = sorted(px)
        n = len(times)
        w = np.zeros((len(syms), n))
        w[:, :] = gross / len(syms)
        return {s: w[j] for j, s in enumerate(syms)}
    return build


def show(title, rows):
    print(u"=== %s ===" % title)
    print(u"%-30s %9s %9s %9s %7s %8s"
          % (u"", u"итог", u"в месяц", u"просадка", u"Шарп", u"плюс мес"))
    for name, r in rows:
        if r is None:
            print(u"%-30s  —" % name)
            continue
        print(u"%-30s %+8.1f%% %+8.2f%% %8.1f%% %7.2f %7.0f%%"
              % (name, 100 * r["ret"], 100 * r["mo"], 100 * r["maxdd"],
                 r["sharpe"], 100 * r["mo_pos"]))
    print()


def main():
    t, px = L.panel(rdata.SYMBOLS, "D")
    for look in (7, 14, 30, 60, 90):
        L.refute_causal_w(xs_weights(look), t, px)
    L.refute_causal_w(riskparity_weights(), t, px)
    print(u"причинность всех правил этого файла проверена порчей будущего\n")

    print(u"ГИПОТЕЗА 2: ПОПЕРЕЧНЫЙ ИМПУЛЬС")
    print(u"каждую неделю лонг сильнейшей монеты и шорт слабейшей, "
          u"объём по 50%% капитала\n")
    for split, title in (("train", u"ОБУЧЕНИЕ"), ("val", u"ПРОВЕРКА"),
                         ("trainval", u"ОБУЧЕНИЕ+ПРОВЕРКА")):
        rows = []
        for look in (7, 14, 30, 60, 90):
            rows.append((u"ранг по доходности за %dд" % look,
                         TS.run_on(split, xs_weights(look))))
        rows.append((u"тот же ранг, знак наоборот",
                     TS.run_on(split, xs_weights(30, gross=-1.0))))
        show(title, rows)

    print(u"ГИПОТЕЗА 3: ПОРТФЕЛЬ С НОРМИРОВКОЙ ПО ВОЛАТИЛЬНОСТИ")
    print(u"доля монеты обратна её волатильности против равных долей, "
          u"пересчёт раз в неделю\n")
    for split, title in (("train", u"ОБУЧЕНИЕ"), ("val", u"ПРОВЕРКА"),
                         ("trainval", u"ОБУЧЕНИЕ+ПРОВЕРКА")):
        rows = [
            (u"равные доли (купил и держу)", TS.run_on(split, equal_weights())),
            (u"обратно волатильности", TS.run_on(split, riskparity_weights())),
        ]
        show(title, rows)

    # риск-доходность в чистом виде: доход на единицу просадки
    print(u"ДОХОД НА ЕДИНИЦУ ПРОСАДКИ (обучение+проверка)")
    for name, b in ((u"равные доли", equal_weights()),
                    (u"обратно волатильности", riskparity_weights())):
        r = TS.run_on("trainval", b)
        print(u"   %-24s %.2f  (Шарп %.2f, волатильность %.0f%% годовых)"
              % (name, r["cagr"] / max(r["maxdd"], 1e-9), r["sharpe"],
                 100 * r["vol_ann"]))


if __name__ == "__main__":
    main()
