# -*- coding: utf-8 -*-
"""v11: переотбор конфигов на ИСПРАВЛЕННОМ движке.

Зачем понадобился. В движке нашлись два дефекта, оба делали бэктест
оптимистичнее реальности:

1. be_move ставил стоп в безубыток по ХАЮ бара. Если бар успевал уйти обратно
   ниже безубытка, стоп оказывался ВЫШЕ текущей цены — такой ордер биржа для
   лонга просто не примет. А движок потом честно «исполнял» по нему выход на
   следующем баре, засчитывая прибыль, которой не было. Замер на боевых
   конфигах: у DOGE так происходило в 21% срабатываний, у LTC 20%, у SOL 25%,
   у BTC 0% (у него тейк крупный, бар редко успевает вернуться).

2. Ликвидация проверялась только внутри ветки «задет стоп». При высоком плече
   цена ликвидации оказывается БЛИЖЕ стопа, и свеча могла дойти до ликвидации,
   не коснувшись стопа, — движок пропускал её и считал позицию живой.

После исправления прежние конфиги на 3.2 годах дают: DOGE -8.3%, LTC -33.3%,
SOL -51.3%, ETH +29.3%, BTC +148.3%. То есть DOGE/LTC/SOL отбирались под
артефакт, и их параметры нужно искать заново — на движке, который не врёт.

Метод тот же, что в v7/v8: полный геном как гены, walk-forward из 3 экзаменов
(harness evolution4.run_version), затем лестница плечей x5..x15 и выбор
наибольшего с просадкой <= 20%. Отличие только одно — движок другой.

Запуск: python evolution11.py
"""

import json
import time

import config
import evolution as ev
import evolution2 as e2
import evolution4 as e4
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

DAYS = 1150
LEVS = [5, 8, 10, 12, 15]
DD_CAP = 0.20


def build_base_src():
    """Стартовые геномы — нынешние боевые конфиги (чтобы отбор начинал не
    с нуля, а с того, что уже есть, и мог их только улучшить)."""
    base = {}
    for sym, modes in config.SYMBOL_PARAMS.items():
        p = modes.get("final")
        if not p:
            continue
        g = e7.cfg_to_genome(p, "final")
        for k, v in e8.OFF8.items():
            g.setdefault(k, v)
        base[sym] = g
    return base


def ladder_for(sym, g, candles, pre, aux):
    filt = e8.make_filter8(g, aux)
    out = []
    for lev in LEVS:
        old, e2.LEV = e2.LEV, lev
        try:
            r = e2.run5(candles, pre, g, entry_filter=filt)
        finally:
            e2.LEV = old
        st = e2.stats(r)
        out.append(dict(lev=lev, ret=round((r["balance"] / e2.START - 1) * 100, 1),
                        med=round(st["med"], 2), dd=round(r["max_dd"] * 100, 1),
                        ruined=r["ruined"], trades=r["trades"],
                        wr=round(r["wins"] / r["trades"] * 100, 1) if r["trades"] else 0))
    return out


def pick_lev(rows):
    best = rows[0]["lev"]
    for row in rows:
        if row["dd"] <= DD_CAP * 100 and not row["ruined"]:
            best = row["lev"]
    return best


def main():
    pct5 = xd.fetch_daily_pct5()
    aux_builder = e8.make_aux_builder(pct5, 96)
    base_src = build_base_src()
    t0 = time.time()

    results = e4.run_version(e8.GENES8, e8.OFF8, e8.make_filter8, aux_builder,
                             "evolution11", base_src, interval="15", days=DAYS)

    final = {}
    print("\n=== Лестница плечей на исправленном движке ===")
    for sym, rec in results.items():
        g = rec["genome"] if rec["adopt"] else rec["base_genome"]
        candles = ev.fetch(sym, "15", DAYS)
        pre = e2.prep(candles)
        aux = aux_builder(sym, candles)
        lad = ladder_for(sym, g, candles, pre, aux)
        lev = pick_lev(lad)
        row = next(x for x in lad if x["lev"] == lev)
        print(f"\n{sym}: принят новый конфиг: {'ДА' if rec['adopt'] else 'нет'}")
        for x in lad:
            mark = " <-" if x["lev"] == lev else ""
            print(f"  x{x['lev']:<3} {x['ret']:+9.1f}% | DD {x['dd']:5.1f}% | "
                  f"WR {x['wr']:5.1f}% | сделок {x['trades']:5}"
                  f"{' СЛИВ' if x['ruined'] else ''}{mark}")
        final[sym] = dict(genome=g, ladder=lad, rec_lev=lev, best=row,
                          adopt=rec["adopt"], base_oos=rec["base_oos"],
                          cand_oos=rec["cand_oos"])

    with open("evolution11_final.json", "w", encoding="utf-8") as fh:
        json.dump(final, fh, ensure_ascii=False, indent=2, default=float)

    print(f"\n\n=== ИТОГ v11 (за {time.time()-t0:.0f}с) ===")
    for sym, r in final.items():
        b = r["best"]
        print(f"{sym:<9} x{r['rec_lev']:<3} {b['ret']:+9.1f}% | DD {b['dd']:5.1f}% | "
              f"WR {b['wr']:5.1f}% | сделок {b['trades']:5} | "
              f"новый конфиг: {'ДА' if r['adopt'] else 'нет'}")
    print("\nИтоги в evolution11_final.json")


if __name__ == "__main__":
    main()
