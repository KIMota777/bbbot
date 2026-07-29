# -*- coding: utf-8 -*-
"""Стресс-тест новостного канала: во что он обходится «на полной тяге».

Задача. Новостные коэффициенты нельзя отобрать walk-forward: архива крипто-
новостей за 3.2 года у нас нет. Но можно ответить на другой, не менее важный
вопрос — СКОЛЬКО СТОИТ ОШИБКА. Для этого канал включается на максимум и
держится там всю историю, как если бы фон был предельным всегда:

    тейк   ×1.30 и ×0.70   (потолки MAX_TP_STRETCH / MAX_TP_SHRINK)
    стоп   поджат на 35%   (потолок MAX_SL_TIGHTEN)

Это верхняя граница вреда. Если стратегия переживает постоянно включённый
канал — включать его по реальным новостям безопасно даже при неверных
оценках. Если не переживает — канал опасен сам по себе, независимо от того,
насколько хорошо Claude читает новости.

Проверка полезна ещё и тем, что не зависит от чьего-либо мнения о новостях:
считается на тех же 3.2 годах тем же движком, что и всё остальное.

Запуск: python news_stress.py
"""

import json

import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd
import gridlib
import news_state

DAYS = 1150

SCENARIOS = [
    ("база (канал выключен)", dict()),
    (f"тейк растянут ×{1 + news_state.MAX_TP_STRETCH:.2f} (попутный фон)",
     dict(news_tp_mult=1 + news_state.MAX_TP_STRETCH)),
    (f"тейк ужат ×{1 - news_state.MAX_TP_SHRINK:.2f} (встречный фон)",
     dict(news_tp_mult=1 - news_state.MAX_TP_SHRINK)),
    (f"стоп поджат на {news_state.MAX_SL_TIGHTEN:.0%}",
     dict(news_sl_frac=news_state.MAX_SL_TIGHTEN)),
    ("худший случай: тейк ужат И стоп поджат",
     dict(news_tp_mult=1 - news_state.MAX_TP_SHRINK,
          news_sl_frac=news_state.MAX_SL_TIGHTEN)),
]


def main():
    pct5 = xd.fetch_daily_pct5()
    aux_builder = e8.make_aux_builder(pct5, 96)
    out = {}

    for sym, modes in config.SYMBOL_PARAMS.items():
        p = modes["final"]
        lev = p["lev"]
        candles = ev.fetch(sym, "15", DAYS)
        pre = e2.prep(candles)
        aux = aux_builder(sym, candles)
        base_g = e7.cfg_to_genome(p, "final")
        for k, v in e8.OFF8.items():
            base_g.setdefault(k, v)
        base_g.update(gridlib.OFF10)
        filt = e8.make_filter8(base_g, aux)

        print(f"\n=== {sym} x{lev} ===")
        rows = {}
        for label, extra in SCENARIOS:
            g = dict(base_g)
            g.update(extra)
            old, e2.LEV = e2.LEV, lev
            try:
                r = e2.run5(candles, pre, g, entry_filter=filt)
            finally:
                e2.LEV = old
            ret = (r["balance"] / e2.START - 1) * 100
            losses = r["trades"] - r["wins"]
            rows[label] = dict(ret=round(ret, 2), dd=round(r["max_dd"] * 100, 2),
                               trades=r["trades"], losses=losses,
                               ruined=r["ruined"])
            base = rows.get("база (канал выключен)")
            delta = f"{ret - base['ret']:+7.2f} пп" if base and label != \
                "база (канал выключен)" else "        —"
            print(f"  {label:<45} {ret:+8.2f}% ({delta}) | DD {r['max_dd']*100:5.2f}% "
                  f"| убыточных {losses:4d} из {r['trades']}")
        out[sym] = rows

    with open("news_stress.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2, default=float)

    print("\n\n=== ВЫВОД ===")
    for sym, rows in out.items():
        base = rows["база (канал выключен)"]
        worst = min(r["ret"] for r in rows.values())
        worst_dd = max(r["dd"] for r in rows.values())
        print(f"{sym:<9} база {base['ret']:+8.2f}% / DD {base['dd']:5.2f}%  ->  "
              f"худший сценарий {worst:+8.2f}% / DD {worst_dd:5.2f}%")
    print("\nПодробности в news_stress.json")


if __name__ == "__main__":
    main()
