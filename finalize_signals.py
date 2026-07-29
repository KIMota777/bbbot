# -*- coding: utf-8 -*-
"""Собирает signal_setups.json из evolution9_winners.json: параметры-победители,
статистика на рекомендованном плече, и флаг enabled — сигналить будем только
сетапами, прошедшими walk-forward экзамен (OOS >= 0.5) и прибыльными на
полном периоде. Проваленные остаются в файле с пометкой — сайт покажет их
честно как «не прошёл отбор»."""

import json

ENABLE_MIN_OOS = 0.5

NOTES = {
    "range_long": "Мало сделок (15 за 3.2г) — статистика тонкая, размер поз. уменьшить",
    "range_short": "Провалил экзамен: WR 25% при пороге безубытка ~26% для R:R 1:3",
    "sweep_long": "Провалил экзамен: WR 13%, глубоко убыточен — ложные пробои низа на BTC в этой формализации не отрабатывают",
    "sweep_short": "Провалил экзамен: около нуля при высокой просадке",
    "dump_long": "Провалил walk-forward (OOS -4.9): прибыль полного периода — иллюзия подгонки",
    "pump_short": "Лучший сетап: 44 сделки, WR 43% при пороге ~26%, стабильные удержания ~2.3 дня",
}

with open("evolution9_winners.json", encoding="utf-8") as fh:
    winners = json.load(fh)

out = {}
for name, rec in winners.items():
    row = next(x for x in rec["ladder"] if x["lev"] == rec["rec_lev"])
    enabled = (rec["oos_mean"] >= ENABLE_MIN_OOS and row["ret"] > 0
               and not row["ruined"])
    out[name] = dict(
        genome=rec["genome"], rec_lev=rec["rec_lev"],
        oos_mean=round(rec["oos_mean"], 2), oos_folds=rec["oos_folds"],
        enabled=enabled, caution=rec.get("caution", False),
        note=NOTES.get(name, ""),
        stats=dict(ret=row["ret"], dd=row["dd"], n=row["n"], wr=row["wr"],
                   avg_hold_h=row["avg_hold_h"], med=row["med"],
                   p25=row["p25"], pos_share=row["pos_share"]),
        ladder=rec["ladder"])

with open("signal_setups.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2)

for name, rec in out.items():
    print(f"{name:12} | {'ВКЛЮЧЁН x' + str(rec['rec_lev']) if rec['enabled'] else 'отключён '} "
          f"| OOS {rec['oos_mean']:+.2f} | {rec['stats']['ret']:+.1f}% | {rec['note'][:60]}")
print("\n-> signal_setups.json")
