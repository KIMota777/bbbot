# -*- coding: utf-8 -*-
"""Собирает signal_setups.json из evolution9_winners.json: параметры-победители,
статистика на рекомендованном плече, и флаг enabled — сигналить будем только
сетапами, прошедшими walk-forward экзамен и прибыльными на полном периоде.
Проваленные остаются в файле с пометкой — сайт покажет их честно как
«не прошёл отбор».

Приёмка ПЕРЕСЧИТЫВАЕТСЯ здесь заново, а не берётся из файла победителей.
Причина: в v9 балл экзаменационного окна с n<3 был 0.0, то есть «ничья», и
сетап мог пройти отбор по ОДНОМУ информативному окну из трёх (так range_long
получил средний OOS 1.89 на фолдах [0.0, 5.68, 0.0] при 15 сделках за 3.2 года
и был включён на плече x20). Пустое окно — не ничья, а прогул экзамена,
поэтому:
  * окно, где сделок меньше se.OOS_MIN_TRADES, получает штраф se.OOS_THIN_PENALTY;
  * сетап с хотя бы одним таким окном не включается вообще, каким бы ни был
    средний балл — экзамен считается несданным, а не сданным на одну треть.

Приёмка САМОЙ v9 (поля adopt/reject_reasons) при этом НЕ игнорируется: волна
проверяет победителя на своём экзаменационном окне (отрыв от базы, число
сделок, слив, просадка, худший месяц), и если она сказала «не принят», сетап
не включается, что бы ни показал пересчёт здесь. Раньше эти поля не читал
никто, и ворота, добавленные в v9, ни на что не влияли. Файл прошлой волны
(08.2026) их не содержит — тогда об этом печатается предупреждение: приёмка
волны к этим сетапам НЕ ПРИМЕНЯЛАСЬ, а не «пройдена».

Запуск: python finalize_signals.py
"""

import json

import evolution as ev
import evolution4 as e4
import signal_engine as se

ENABLE_MIN_OOS = 0.5
OOS_LEV = 15   # то же плечо, на котором walk-forward считала evolution9 (GA_LEV)

# имена вынесены в константы, чтобы проверочный прогон мог подсунуть свой файл
# победителей и писать результат мимо рабочей папки
WINNERS_FILE = "evolution9_winners.json"
SETUPS_FILE = "signal_setups.json"

NOTES = {
    "range_long": "Боковик у нижней границы: сделок мало, статистика тонкая",
    "range_short": "Боковик у верхней границы: WR ниже порога безубытка для R:R 1:3",
    "sweep_long": "Ложные пробои низа на BTC в этой формализации не отрабатывают",
    "sweep_short": "Около нуля на полном периоде при высокой просадке",
    "dump_long": "Прибыль полного периода не подтверждается вне обучения",
    "pump_short": "Самый частый сетап: удержания стабильные, около 2 суток",
}


def main():
    c4 = ev.fetch("BTCUSDT", "240", 1150)
    c15 = ev.fetch("BTCUSDT", "15", 1150)
    ts15 = [c[0] for c in c15]
    ctx = se.prep_context(c4)
    folds = e4.fold_bounds_3y(len(c4))
    print(f"BTC: 4ч {len(c4)} свечей, 15м {len(c15)}; экзаменационные окна "
          f"{[(b, e) for _, b, e in folds]}")
    print(f"Правило приёмки: средний OOS >= {ENABLE_MIN_OOS}, в КАЖДОМ окне "
          f">= {se.OOS_MIN_TRADES} сделок (иначе штраф {se.OOS_THIN_PENALTY} "
          f"и незачёт), доход полного периода > 0, без слива\n")

    with open(WINNERS_FILE, encoding="utf-8") as fh:
        winners = json.load(fh)

    # поля приёмки v9 появились в третьем круге: у файла прошлой волны их нет,
    # и молчаливо считать такие сетапы принятыми нельзя
    no_gate = [n for n, r in winners.items() if "adopt" not in r]
    if no_gate:
        print(f"ВНИМАНИЕ: в {WINNERS_FILE} нет поля adopt у "
              f"{len(no_gate)} сетапов ({', '.join(no_gate)}) — файл записан до "
              f"появления приёмки v9. Ворота волны к ним НЕ ПРИМЕНЯЛИСЬ; "
              f"включение решается только пересчётом ниже\n")

    out = {}
    for name, rec in winners.items():
        row = next(x for x in rec["ladder"] if x["lev"] == rec["rec_lev"])
        scores, counts = [], []
        for (_a, b, e_) in folds:
            r = se.run_setup(name, rec["genome"], c4, ctx, c15, ts15, OOS_LEV,
                             signal_range=(b, e_))
            scores.append(round(se.oos_score(r), 2))
            counts.append(len(r["trades"]))
        oos_mean = sum(scores) / len(scores)
        thin = [i + 1 for i, n in enumerate(counts) if n < se.OOS_MIN_TRADES]
        # вердикт волны: adopt=False означает, что сетап провалил ворота v9 на
        # её собственном экзаменационном окне. Пересчёт здесь строже по одним
        # правилам и мягче по другим (он не смотрит ни слив, ни просадку, ни
        # худший месяц экзамена), поэтому отказ волны не отменяется, а
        # добавляется к отказам: ворота складываются, а не заменяют друг друга.
        v9_adopt = rec.get("adopt")
        v9_reasons = rec.get("reject_reasons", [])
        enabled = (oos_mean >= ENABLE_MIN_OOS and not thin
                   and row["ret"] > 0 and not row["ruined"]
                   and v9_adopt is not False)

        why = []
        if v9_adopt is False:
            why.append("приёмка v9 не пройдена"
                       + (": " + "; ".join(v9_reasons) if v9_reasons else ""))
        if thin:
            why.append("экзамен не сдан: в окн%s %s сделок меньше %d"
                       % ("е" if len(thin) == 1 else "ах",
                          ",".join(str(i) for i in thin), se.OOS_MIN_TRADES))
        if oos_mean < ENABLE_MIN_OOS:
            why.append("средний OOS %+.2f ниже порога %.2f"
                       % (oos_mean, ENABLE_MIN_OOS))
        if row["ret"] <= 0:
            why.append("полный период в минусе (%+.1f%%)" % row["ret"])
        if row["ruined"]:
            why.append("слив депозита на рекомендованном плече")
        note = NOTES.get(name, "")
        if why:
            note = (note + ". " if note else "") + "Отключён — " + "; ".join(why)

        out[name] = dict(
            genome=rec["genome"], rec_lev=rec["rec_lev"],
            oos_mean=round(oos_mean, 2), oos_folds=scores,
            oos_fold_n=counts, oos_min_trades=se.OOS_MIN_TRADES,
            # oos_folds волны — ЧЕСТНЫЕ окна победителя (от окна его обучения и
            # до конца), их бывает и два, и одно. Раньше сумма делилась здесь на
            # жёсткую тройку, и при коротком списке средний балл выходил
            # заниженным. Номера окон волна кладёт рядом; у старого файла их
            # нет — тогда считаем, что список идёт с конца.
            oos_folds_v9=[round(x, 2) for x in rec["oos_folds"]],
            oos_windows_v9=rec.get(
                "oos_folds_windows",
                list(range(len(folds) - len(rec["oos_folds"]) + 1,
                           len(folds) + 1))),
            v9_adopt=v9_adopt, v9_reject_reasons=v9_reasons,
            enabled=enabled, caution=rec.get("caution", False),
            note=note,
            stats=dict(ret=row["ret"], dd=row["dd"], n=row["n"], wr=row["wr"],
                       avg_hold_h=row["avg_hold_h"], med=row["med"],
                       p25=row["p25"], pos_share=row["pos_share"]),
            ladder=rec["ladder"])

    with open(SETUPS_FILE, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    for name, rec in out.items():
        status = ("ВКЛЮЧЁН x" + str(rec["rec_lev"])) if rec["enabled"] \
            else "отключён "
        v9 = rec["oos_folds_v9"]
        print(f"{name:12} | {status} | OOS {rec['oos_mean']:+6.2f} "
              f"(было {sum(v9)/len(v9):+6.2f} по окнам "
              f"{','.join(str(w) for w in rec['oos_windows_v9'])}) | "
              f"сделок по окнам {rec['oos_fold_n']} | "
              f"{rec['stats']['ret']:+7.1f}% | {rec['note'][:70]}")
    print(f"\n-> {SETUPS_FILE}")


if __name__ == "__main__":
    main()
