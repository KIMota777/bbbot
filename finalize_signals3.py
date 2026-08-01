# -*- coding: utf-8 -*-
"""Честная финализация сигнальных сетапов по итогам evolution11
(вложенная валидация с неприкосновенным holdout).

ГЛАВНОЕ: 0 из 6 сетапов прошли честный экзамен. Поэтому:
  enabled = False у ВСЕХ — помощник не выдаёт их как торговые рекомендации;
  watch   = True у тех, кто на holdout показал положительную expectancy И
            преимущество над необученным семенем — эти сетапы помощник
            показывает в режиме НАБЛЮДЕНИЯ (с явной пометкой), чтобы копить
            живую статистику и видеть их на графике, но не как сигнал "входи".

Почему не включаем даже лучший: sweep_long даёт на holdout PF 1.14 при 23
сделках — это статистически неотличимо от 1.0 (случайности). Преимущество
над семенем (+0.822R) обнадёживает, но одного тяжёлого 10-месячного отрезка
мало для вывода.

Пишет signal_setups2.json (тот же формат, что читают сайт и advisor).
"""

import json

import evolution as ev
import signal_engine2 as se2
import signal_stats as ss

RU = {
    "range_long": "Боковик: лонг от нижней границы",
    "range_short": "Боковик: шорт от верхней границы",
    "sweep_long": "Ложный пробой низа (сбор ликвидности): лонг",
    "sweep_short": "Ложный пробой верха (сбор ликвидности): шорт",
    "dump_long": "Капитуляция: лонг после сильного падения",
    "pump_short": "Перегрев: шорт после вертикального роста",
}


def main():
    with open("evolution11_winners.json", encoding="utf-8") as fh:
        win = json.load(fh)

    c4 = ev.fetch("BTCUSDT", "240", 1150)
    c15 = ev.fetch("BTCUSDT", "15", 1150)
    ts15 = [c[0] for c in c15]
    ctx = se2.prep_context(c4)

    out = {}
    for name in se2.SETUPS:
        rec = win.get(name)
        if not rec:
            continue
        g = {k: rec["genome"][k] for k in se2.GENES2 if k in rec["genome"]}
        lev = rec.get("rec_lev", 10)
        h = rec["holdout"]
        b = rec["holdout_benchmark"]
        edge = rec["edge_exp_r"]

        # статистика на ВСЕЙ истории — только для графиков и справки; она
        # включает обучающий период, поэтому не является доказательством
        r_all = se2.run_setup(name, g, c4, ctx, c15, ts15, lev)
        st = ss.full_stats(r_all, lev, t0_ms=c4[0][0])

        watch = (h["exp_r"] > 0 and edge > 0 and h["n"] >= 8)
        verdict = ("наблюдение: на невиданных данных положителен, но порог "
                   "надёжности не пройден" if watch else
                   "не подтверждён на невиданных данных")

        out[name] = dict(
            title=RU.get(name, name), genome=g, rec_lev=lev,
            enabled=False,          # торговых рекомендаций не даём ни по одному
            watch=watch,            # показывать ли в режиме наблюдения
            verdict=verdict,
            reason="; ".join(rec.get("fail_reasons", [])) or "порог не пройден",
            # ЧЕСТНЫЙ экзамен: данные, которых не видели ни GA, ни отбор
            holdout=dict(
                n=h["n"], wr=h["wr"], exp_r=h["exp_r"], pf=h["pf"],
                sum_r=h["sum_r"], ret=h["ret"], dd=h["dd"],
                tp=h["tp"], stop=h["stop"],
                period="2025-09-07..2026-07-26 (10.6 мес, BTC -41.8%, bear 38%)"),
            benchmark=dict(n=b["n"], wr=b["wr"], exp_r=b["exp_r"], pf=b["pf"],
                           ret=b["ret"]),
            edge_exp_r=edge,
            inner_oos=rec.get("inner_oos"),
            ladder=rec.get("ladder", []),
            caution=rec.get("caution", True),
            # справочно по всей истории (ВКЛЮЧАЕТ обучающий период!)
            stats=dict(
                n=st["n"], wr=st["wr"], wr_breakeven=st["wr_breakeven"],
                exp_r=st["exp_r"], pf=st["pf"], ret=st["ret_pct"],
                dd=st["dd_pct"], reinvest_usd=st["reinvest"]["final_usd"],
                reinvest_pct=st["reinvest"]["final_pct"],
                avg_hold_h=st["timing"]["hold_avg_h"], tpm=st["tpm"],
                by_regime={k: dict(n=v["n"], wr=v["wr"], exp_r=v["exp_r"])
                           for k, v in st["by_regime"].items()},
                stop_then_tp=st["stop_quality"]["stop_then_tp_n"],
                n_stop=st["outcomes"]["stop"]["n"],
                near_n=st["near"]["n"], near_avg_r=st["near"]["avg_r"],
                in_sample_warning="включает обучающий период — не доказательство"),
        )

        mark = "НАБЛЮДЕНИЕ" if watch else "отключён  "
        print(f"{name:12} {mark} x{lev:<2} | HOLDOUT: {h['n']:2} сделок, "
              f"WR {h['wr']:5.1f}%, exp {h['exp_r']:+.3f}R, PF {h['pf']}, "
              f"итог {h['ret']:+6.1f}%, DD {h['dd']:5.1f}% | семя exp "
              f"{b['exp_r']:+.3f}R | преимущество {edge:+.3f}R")
        print(f"             причина: {out[name]['reason']}")
        print(f"             вся история (in-sample, справочно): {st['n']} сделок, "
              f"итог {st['ret_pct']:+.1f}%, PF {st['pf']}")

    with open("signal_setups2.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    n_watch = sum(1 for v in out.values() if v["watch"])
    print(f"\n-> signal_setups2.json: торговых сетапов 0 из {len(out)}, "
          f"в режиме наблюдения {n_watch}")


if __name__ == "__main__":
    main()
