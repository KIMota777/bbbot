# -*- coding: utf-8 -*-
"""Фандинг как источник дохода: рыночно-нейтральная позиция.

ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ ВСЕГО ОСТАЛЬНОГО В ИССЛЕДОВАНИИ. Здесь ничего не
предсказывается. Схема такая: короткая позиция в бессрочном контракте плюс
равная длинная в споте. Движение цены двум ногам взаимно гасится, а разница в
том, что держатель короткой стороны бессрочного контракта ПОЛУЧАЕТ ставку
фандинга, когда она положительна.

Ставка положительна почти всегда, и причина у этого не случайная: бессрочный
контракт удерживают у цены спота именно платежами между сторонами, а желающих
играть на повышение с плечом в крипте систематически больше. То есть доход
здесь — плата за то, что вы берёте на себя противоположную сторону чужого
плеча. Это внятное объяснение, а не «на истории получилось».

ЧТО ЗДЕСЬ УЧТЕНО ЧЕСТНО:
  * ставка приводится к долям (в файлах она в процентах — стократная ловушка);
  * отрицательные периоды не выбрасываются: когда ставка ниже нуля, схема
    платит, и это входит в результат;
  * издержки на ЧЕТЫРЕ ноги (открыть обе, закрыть обе) плюс проскальзывание;
  * доход считается на ПОЛНЫЙ вложенный капитал: под обе ноги нужны деньги,
    и делать вид, что позиция бесплатна, нельзя.

ЧЕГО ЗДЕСЬ НЕТ, и без этого выводы неполны:
  * расхождения цен спота и контракта (базис) — у крупных монет оно мало, но
    в шторм расширяется, и на нём теряют;
  * риска биржи: обе ноги лежат в одном месте;
  * риска ликвидации короткой ноги, если её вести с плечом. Ниже плечо
    считается равным единице, то есть самый безопасный вариант — и самый
    скромный по доходности.
"""
import datetime as dt
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import rdata  # noqa: E402

ROUND_TRIP_COST = 4 * rdata.TAKER_FEE      # открыть и закрыть две ноги


def carry_series(symbol):
    """Ставки фандинга в долях и моменты расчёта, внутри нашей истории."""
    t, v = rdata.load_funding(symbol)
    m = (t >= rdata.HIST_START_MS) & (t <= rdata.HIST_END_MS)
    return t[m], v[m]


def hold_always(symbol, slip=None):
    """Держать нейтральную позицию всё время, без всяких условий."""
    t, v = carry_series(symbol)
    if not len(t):
        return None
    slip = slip if slip is not None else rdata.SLIP_BPS.get(symbol, 0.0005)
    days = (int(t[-1]) - int(t[0])) / rdata.DAY_MS
    gross = float(v.sum())                      # доля от объёма позиции
    cost = ROUND_TRIP_COST + 2 * slip           # один вход и один выход
    net = gross - cost
    return dict(symbol=symbol, payments=len(v), days=days,
                gross=gross, cost=cost, net=net,
                ann=net * 365.0 / max(days, 1),
                neg_share=float((v < 0).mean()),
                worst_8h=float(v.min()), best_8h=float(v.max()),
                median_8h=float(np.median(v)))


def monthly(symbol):
    """Помесячный доход схемы — чтобы видеть, бывают ли убыточные месяцы."""
    t, v = carry_series(symbol)
    out = {}
    for ts, r in zip(t, v):
        d = dt.datetime.fromtimestamp(ts / 1000, dt.UTC)
        out.setdefault((d.year, d.month), 0.0)
        out[(d.year, d.month)] += float(r)
    return sorted(out.items())


def portfolio(symbols):
    """Равные доли по монетам. Общий доход на вложенный капитал."""
    per = {}
    for s in symbols:
        t, v = carry_series(s)
        for ts, r in zip(t, v):
            d = dt.datetime.fromtimestamp(ts / 1000, dt.UTC)
            key = (d.year, d.month)
            per.setdefault(key, {}).setdefault(s, 0.0)
            per[key][s] += float(r)
    rows = []
    for key in sorted(per):
        vals = list(per[key].values())
        rows.append((key, float(np.mean(vals))))
    return rows


def main():
    syms = rdata.SYMBOLS
    print("ФАНДИНГ КАК ДОХОД: короткий бессрочный контракт + длинный спот")
    print("Плечо 1, то есть под каждую сторону вложен полный капитал.")
    print("Издержки: %.3f%% на круг (четыре ноги) плюс проскальзывание.\n"
          % (100 * ROUND_TRIP_COST))

    print("%-6s %8s %9s %9s %9s %8s %9s"
          % ("монета", "выплат", "собрано", "издержки", "чисто", "годовых",
             "минусовых"))
    rows = []
    for s in syms:
        r = hold_always(s)
        if not r:
            continue
        rows.append(r)
        print("%-6s %8d %+8.2f%% %8.2f%% %+8.2f%% %+7.2f%% %8.0f%%"
              % (s.replace("USDT", ""), r["payments"], 100 * r["gross"],
                 100 * r["cost"], 100 * r["net"], 100 * r["ann"],
                 100 * r["neg_share"]))

    if rows:
        ann = np.array([r["ann"] for r in rows])
        print("\nПо пяти монетам: медиана %+.2f%% годовых, худшая %+.2f%%, "
              "лучшая %+.2f%%" % (100 * np.median(ann), 100 * ann.min(),
                                  100 * ann.max()))

    print("\n\nПОМЕСЯЧНО ПО ПОРТФЕЛЮ (равные доли, доход в долях объёма)")
    mp = portfolio(syms)
    vals = np.array([v for _, v in mp])
    print("месяцев %d | медиана %+.3f%% | минусовых %.0f%% | худший %+.3f%% | "
          "лучший %+.3f%%"
          % (len(vals), 100 * np.median(vals), 100 * (vals < 0).mean(),
             100 * vals.min(), 100 * vals.max()))
    print("\nгод-месяц   доход")
    for (y, m), v in mp:
        bar = "#" * max(0, int(round(v * 4000)))
        print("  %04d-%02d  %+7.3f%%  %s" % (y, m, 100 * v, bar))

    print("\n\nЧТО ЭТО ЗНАЧИТ")
    if rows:
        med = float(np.median([r["ann"] for r in rows]))
        print("Схема даёт порядка %+.1f%% годовых на вложенный капитал при"
              % (100 * med))
        print("почти нулевой зависимости от направления рынка. Это НЕ цель")
        print("задания (+4-5%% в месяц, то есть 60-80%% годовых) — меньше почти")
        print("на порядок. Но в отличие от всего остального в исследовании,")
        print("здесь понятно, ОТКУДА берётся доход, и не требуется угадывать.")
    print("\nЧего не хватает для решения о деньгах: расхождение цен спота и")
    print("контракта, риск биржи, риск ликвидации при плече выше единицы.")
    print("Первое можно посчитать, скачав спотовые цены; остальное считать")
    print("нечем, это решение владельца.")


if __name__ == "__main__":
    main()
