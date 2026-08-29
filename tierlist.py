# -*- coding: utf-8 -*-
"""Тир-лист всех сохранившихся конфигов: и архивных, и работающих сейчас.

ЧЕМ ЭТОТ СПИСОК ОТЛИЧАЕТСЯ ОТ ОБЫЧНОГО РЕЙТИНГА. Обычный рейтинг ставит всех в
ряд по доходности и создаёт впечатление, будто первый заметно лучше третьего.
Здесь ни один конфиг не отличим от нуля после поправки на число попыток,
поэтому ряд был бы враньём. Вместо него — уровни, и попадание на уровень
определяется тем, ЧТО ПРО КОНФИГ ВООБЩЕ МОЖНО УТВЕРЖДАТЬ:

  A — держится на обеих половинах истории И сделок хватает, чтобы это не было
      случайностью, И просадка укладывается в 20%;
  B — держится на обеих половинах, но проверить нечем: настоящих сделок мало
      либо просадка вышла за 20%;
  C — держится на одной половине из двух. Это ровно то, что даёт монетка;
  D — не держится ни на одной;
  F — держится ТОЛЬКО на копеечных выходах: убери прибыль на безубытках, и
      конфиг уходит в минус. Такой результат — свойство тумблера be_move, а не
      стратегии.

НАСТОЯЩИЕ СДЕЛКИ — не то же, что сделки. Цикл, закрытый переносом стопа в
безубыток, формально «прибыльная сделка», фактически ноль. У DOGE таких 84-87%.
Здесь считаются только некопеечные: именно они несут информацию, и именно по их
числу видно, можно ли вообще что-то утверждать.

Запуск: python tierlist.py
"""
import json
import os
import sys

import numpy as np
from scipy import stats

import all_configs_honest as ach
import bots_honest as bh
import ext_data as xd

IN = os.path.join("webapp", "data", "all_configs_honest.json")
OUT = os.path.join("webapp", "data", "tierlist.json")

MIN_REAL = 40          # меньше — утверждать нечего
MAX_DD = 20.0          # потолок просадки, как в правиле выбора плеча


def real_trades(sym, g, part, lev=5.0):
    """Некопеечные сделки половины: их число, средняя и достоверность."""
    evs = []
    bh.run_at(part["candles"], part["pre"], g, part["filt"], lev, events=evs,
              funding=part.get("funding"))
    p = [e["pnl"] for e in evs
         if e["type"] == "close" and e["pnl"] is not None
         and not bh.is_tiny(e["pnl"])]
    if len(p) < 3:
        return dict(n=len(p), mean=0.0, t=0.0, p=1.0)
    a = np.array(p)
    se = a.std(ddof=1) / np.sqrt(len(a))
    t = float(a.mean() / se) if se > 0 else 0.0
    return dict(n=len(a), mean=float(a.mean()), t=t,
                p=float(2 * (1 - stats.norm.cdf(abs(t)))))


def tier_of(r):
    """Уровень и причина — причина важнее уровня."""
    tr, ho = r["train"], r["hold"]
    if tr.get("on_artifact") or ho.get("on_artifact"):
        return "F", "живёт на копеечных выходах"
    both_rob = tr["rob"] > 0 and ho["rob"] > 0
    n_real = min(tr["real"]["n"], ho["real"]["n"])
    dd = max(tr["dd"], ho["dd"])
    if both_rob:
        if n_real >= MIN_REAL and dd <= MAX_DD:
            return "A", "обе половины держатся, сделок хватает, просадка в норме"
        if n_real < MIN_REAL:
            return "B", "обе половины держатся, но настоящих сделок %d" % n_real
        return "B", "обе половины держатся, но просадка %.0f%%" % dd
    if tr["rob"] > 0 or ho["rob"] > 0:
        return "C", "держится только на одной половине из двух"
    return "D", "не держится ни на одной половине"


def main():
    with open(IN, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data["rows"]
    pct5 = xd.fetch_daily_pct5()
    recs = {(x["sym"], x["src"], x["tag"]): x for x in ach.collect()}
    live = set()
    import config
    for sym, modes in config.SYMBOL_PARAMS.items():
        if "final" in modes:
            live.add((sym, "config.py", "final"))

    print("считаю настоящие сделки по %d конфигам..." % len(rows))
    for i, r in enumerate(rows, 1):
        key = (r["sym"], r["src"], r["tag"])
        rec = recs.get(key)
        if not rec:
            r["train"]["real"] = r["hold"]["real"] = dict(n=0, mean=0, t=0, p=1)
            continue
        hs = ach.halves(r["sym"], rec["g"], pct5)
        for half in ("train", "hold"):
            r[half]["real"] = real_trades(r["sym"], rec["g"], hs[half])
        r["live"] = key in live
        if i % 10 == 0:
            print("  %d/%d" % (i, len(rows)))
            sys.stdout.flush()

    for r in rows:
        r["tier"], r["why"] = tier_of(r)

    order = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
    rows.sort(key=lambda r: (order[r["tier"]], -r["worst_rob"]))
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dict(rows=rows, min_real=MIN_REAL, max_dd=MAX_DD,
                       generated=data.get("generated")), fh,
                  ensure_ascii=False)

    names = {"A": "A — можно что-то утверждать",
             "B": "B — держится, но проверить нечем",
             "C": "C — одна половина из двух, то есть монетка",
             "D": "D — не держится нигде",
             "F": "F — живёт на копеечных выходах"}
    print()
    for t in ("A", "B", "C", "D", "F"):
        grp = [r for r in rows if r["tier"] == t]
        print("=" * 78)
        print("%s   (%d конфигов)" % (names[t], len(grp)))
        print("=" * 78)
        if not grp:
            print("  пусто\n")
            continue
        print("%-5s %-26s %8s %8s %7s %7s %6s %7s"
              % ("мон", "откуда", "обуч", "холд", "ус.об", "ус.хол",
                 "сдел", "просад"))
        for r in grp[:14]:
            mark = " <-- РАБОТАЕТ СЕЙЧАС" if r.get("live") else ""
            print("%-5s %-26s %+7.1f%% %+7.1f%% %+6.1f%% %+6.1f%% %6d %6.1f%%%s"
                  % (r["sym"].replace("USDT", ""),
                     ("%s/%s" % (r["src"], r["tag"]))[:26],
                     r["train"]["comp"], r["hold"]["comp"],
                     r["train"]["rob"], r["hold"]["rob"],
                     min(r["train"]["real"]["n"], r["hold"]["real"]["n"]),
                     max(r["train"]["dd"], r["hold"]["dd"]), mark))
        if len(grp) > 14:
            print("  ... и ещё %d" % (len(grp) - 14))
        print()

    print("=" * 78)
    print("ГДЕ СЕЙЧАС РАБОТАЮЩИЕ БОТЫ")
    print("=" * 78)
    for r in rows:
        if r.get("live"):
            print("  %-5s уровень %s — %s" % (r["sym"].replace("USDT", ""),
                                              r["tier"], r["why"]))
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    main()
