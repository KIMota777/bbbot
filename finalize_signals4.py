# -*- coding: utf-8 -*-
"""Финализация сигналов v3 по итогам evolution12 (честная вложенная
валидация, 4 сетапа x 2 ТФ = 8 сигналов).

Правила те же, что в finalize_signals3:
  enabled = True только если сигнал ПРОШЁЛ честный экзамен на holdout
            (n>=8, exp_r>0, PF>=1.2, sum_r>0, преимущество над семенем);
  watch   = True (режим наблюдения, не рекомендация) если на holdout
            exp_r > 0 И edge > 0 И n >= 8, но полный порог не взят;
  иначе   — отключён и на сайте показывается честная причина.

Пишет signal_setups2.json в мульти-ТФ формате: ключи "<сетап>@240"/"<сетап>@60"
(контракт сайта/advisor). Статистика по всей истории (in-sample) — только
справочно, с явной пометкой.
"""

import glob
import json
import os
import time

import evolution as ev
import signal_engine2 as se2
import signal_stats as ss

MIN_REPRO = 2   # сколько независимых прогонов должны подтвердить сетап


def repro_counts():
    """Сколько раз каждый конфиг прошёл честный экзамен в НЕЗАВИСИМЫХ
    прогонах отбора (разные случайные зёрна, файлы winners_seed*.json).

    Зачем: измерено, что случайный геном проходит наш порог лишь в 1.4%
    случаев — то есть отбор действительно что-то находит. Но при смене
    зерна побеждают КАЖДЫЙ РАЗ ДРУГИЕ конфиги (три прогона — три разных
    победителя, пересечение пустое). Значит порог проходит удачно
    вытянутый геном, а не работающая стратегия. Поэтому торговый статус
    выдаём только тому, кто подтвердился минимум в MIN_REPRO прогонах.
    """
    counts, total = {}, 0
    for path in sorted(glob.glob("winners_seed*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        total += 1
        for key, rec in data.items():
            counts[key] = counts.get(key, 0) + (1 if rec.get("passed") else 0)
    return counts, total


def main():
    with open("evolution12_winners.json", encoding="utf-8") as fh:
        win = json.load(fh)
    if any(rec.get("smoke") for rec in win.values()):
        print("ВНИМАНИЕ: файл от smoke-прогона! Сначала полный: python evolution12.py")
        return

    repro, n_runs = repro_counts()
    if n_runs:
        print(f"воспроизводимость: {n_runs} независимых прогонов отбора, "
              f"порог для торгового статуса — подтверждение в {MIN_REPRO}+\n")

    data = {}   # interval -> (c4, ctx, c15, ts15)
    c15 = ev.fetch("BTCUSDT", "15", 1150)
    ts15 = [c[0] for c in c15]

    def get_data(iv):
        if iv not in data:
            cc = ev.fetch("BTCUSDT", str(iv), 1150)
            data[iv] = (cc, se2.prep_context(cc, interval_min=iv))
        return data[iv]

    out = {}
    for key, rec in sorted(win.items()):
        setup = key.split("@")[0]
        iv = int(rec.get("interval_min") or key.split("@")[1])
        g = {k: rec["genome"][k] for k in se2.GENES2 if k in rec["genome"]}
        lev = rec.get("rec_lev", 10)
        h = rec["holdout"]
        b = rec["holdout_benchmark"]
        edge = rec["edge_exp_r"]

        # строка лестницы плечей, соответствующая рекомендованному плечу
        lad_row = next((row for row in rec.get("ladder", [])
                        if int(row.get("lev", 0)) == int(lev)), {})
        cc, ctx = get_data(iv)
        hs = rec.get("hold_start_bar")
        d = lambda ms: time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
        period = (f"{d(cc[hs][0])}..{d(cc[-1][0])}" if hs else "holdout")

        # вся история — справочно (включает обучающий период!)
        r_all = se2.run_setup(setup, g, cc, ctx, c15, ts15, lev,
                              collect_diag=False)
        st = ss.full_stats(r_all, lev, t0_ms=cc[0][0])

        passed_once = bool(rec.get("passed"))
        n_repro = repro.get(key, 0)
        # торговый статус — только при подтверждении в нескольких
        # независимых прогонах отбора
        passed = passed_once and (n_runs < MIN_REPRO or n_repro >= MIN_REPRO)
        watch = (not passed and h["exp_r"] > 0 and edge > 0 and h["n"] >= 8)
        if passed:
            verdict = "прошёл честный экзамен — торговый сигнал"
        elif passed_once and n_runs >= MIN_REPRO:
            verdict = (f"экзамен пройден лишь в {n_repro} из {n_runs} "
                       f"независимых прогонов — не воспроизводится")
        elif watch:
            verdict = ("наблюдение: на невиданных данных положителен, но "
                       "порог надёжности не пройден")
        else:
            verdict = "не подтверждён на невиданных данных"

        out[key] = dict(
            title=se2.SETUP_TITLES.get(setup, setup),
            interval_min=iv, genome=g, rec_lev=lev,
            enabled=passed, watch=watch, verdict=verdict,
            repro_passes=n_repro, repro_runs=n_runs,
            reason=(("; ".join(rec.get("fail_reasons", []))) if not passed_once
                    else (f"экзамен пройден лишь в {n_repro} из {n_runs} "
                          f"независимых прогонов отбора — результат не "
                          f"воспроизводится, торговать нельзя"
                          if not passed else "")),
            # ВАЖНО: блок holdout из evolution12 посчитан на ОТБОРОЧНОМ плече
            # GA_LEV=15, а рекомендуем мы rec_lev. Итог и просадка от плеча
            # зависят линейно, поэтому берём их из строки лестницы для
            # rec_lev — иначе подпись «итог x20» показывает цифру для x15
            # (аудит намерил расхождение в 7 конфигах из 8).
            holdout=dict(n=h["n"], wr=h["wr"], exp_r=h["exp_r"], pf=h["pf"],
                         sum_r=h["sum_r"],
                         ret=lad_row.get("ret", h["ret"]),
                         dd=lad_row.get("dd", h["dd"]),
                         ret_at_ga_lev=h["ret"], dd_at_ga_lev=h["dd"],
                         ga_lev=15,
                         tp=h["tp"], stop=h["stop"], period=period),
            benchmark=dict(n=b["n"], wr=b["wr"], exp_r=b["exp_r"],
                           pf=b["pf"], ret=b["ret"]),
            edge_exp_r=edge,
            inner_oos=rec.get("inner_oos"), ladder=rec.get("ladder", []),
            caution=rec.get("caution", True), src_fold=rec.get("src_fold"),
            stats=dict(
                n=st["n"], wr=st["wr"], wr_breakeven=st["wr_breakeven"],
                exp_r=st["exp_r"], pf=st["pf"], ret=st["ret_pct"],
                dd=st["dd_pct"], avg_hold_h=st["timing"]["hold_avg_h"],
                tpm=st["tpm"],
                in_sample_warning="включает обучающий период — не доказательство"),
        )

        tf = "4ч" if iv == 240 else "1ч"
        mark = ("ТОРГОВЫЙ  " if passed else
                ("НАБЛЮДЕНИЕ" if watch else "отключён  "))
        if n_runs:
            mark += f" [{n_repro}/{n_runs}]"
        print(f"{key:18} {tf} {mark} x{lev:<2} | HOLDOUT: {h['n']:3} сделок, "
              f"WR {h['wr']:5.1f}%, exp {h['exp_r']:+.3f}R, PF {h['pf']}, "
              f"итог {h['ret']:+6.1f}%, DD {h['dd']:5.1f}% | edge {edge:+.3f}R")
        if not passed:
            # печатаем ФАКТИЧЕСКУЮ причину из конфига: она включает провал
            # воспроизводимости, которого нет в fail_reasons одного прогона
            print(f"{'':21}причина: {out[key]['reason'] or '—'}")

    with open("signal_setups2.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    n_on = sum(1 for v in out.values() if v["enabled"])
    n_w = sum(1 for v in out.values() if v["watch"])
    print(f"\n-> signal_setups2.json: торговых {n_on}, наблюдение {n_w}, "
          f"всего {len(out)}")


if __name__ == "__main__":
    main()
