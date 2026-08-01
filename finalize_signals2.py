# -*- coding: utf-8 -*-
"""Из evolution10_winners.json делает signal_setups2.json — рабочие конфиги
сигнальных сетапов v2 для живого помощника и сайта.

Сетап включается (enabled=true) только если прошёл ВСЕ фильтры честности:
  1) ОБЪЕДИНЁННАЯ статистика всех OOS-сделок (три экзамена вместе, 18-25
     сделок): expectancy > 0, PF >= MIN_OOS_PF, суммарный R > 0.
     Почему объединённая, а не "каждый экзамен в плюсе": на 6-месячном
     экзамене приходится 4-9 сделок, и одна серия стопов уводит фолд в
     глубокий минус чисто по статистике малой выборки. Требовать плюса от
     каждого фолда — значит отбирать по шуму. Объединение всех OOS-сделок
     даёт первую выборку, на которой оценка вообще осмысленна.
  2) средний OOS-скор >= ENABLE_MIN_OOS и минимум MIN_OOS_TRADES сделок;
  3) на полном периоде: положительный итог, PF >= MIN_PF, expectancy > 0,
     минимум MIN_TOTAL_TRADES сделок, без слива;
  4) WR выше фактического порога безубытка (с учётом издержек — при 1:3
     это ~29-32%, а не теоретические 25%).
Отдельно (не блокируя) считается caution: сколько экзаменов отрицательны и
насколько глубоко — это выводится на сайте как предупреждение о нестабильности.
Провалившие остаются в файле с enabled=false и текстом причины — сайт
показывает их честно, помощник по ним не сигналит.
"""

import json
import os

import evolution as ev
import evolution4 as e4
import signal_engine2 as se2
import signal_stats as ss

ENABLE_MIN_OOS = 0.0       # средний скор экзаменов не должен быть отрицательным
MIN_OOS_TRADES = 12
MIN_OOS_PF = 1.2           # PF на объединённой OOS-выборке
MIN_TOTAL_TRADES = 25
MIN_PF = 1.1
DEEP_FOLD = -8.0           # ниже этого фолд считаем "глубоко провальным"

RU = {
    "range_long": "Боковик: лонг от нижней границы",
    "range_short": "Боковик: шорт от верхней границы",
    "sweep_long": "Ложный пробой низа (сбор ликвидности): лонг",
    "sweep_short": "Ложный пробой верха (сбор ликвидности): шорт",
    "dump_long": "Капитуляция: лонг после сильного падения",
    "pump_short": "Перегрев: шорт после вертикального роста",
}


def main():
    with open("evolution10_winners.json", encoding="utf-8") as fh:
        winners = json.load(fh)

    c4 = ev.fetch("BTCUSDT", "240", 1150)
    c15 = ev.fetch("BTCUSDT", "15", 1150)
    ts15 = [c[0] for c in c15]
    ctx = se2.prep_context(c4)
    folds_b = e4.fold_bounds_3y(len(c4))

    def oos_pooled(name, g, lev):
        """Все сделки трёх OOS-отрезков вместе — единственная выборка, на
        которой оценка сетапа статистически осмысленна."""
        tr = []
        for (_a, b, e_) in folds_b:
            r = se2.run_setup(name, g, c4, ctx, c15, ts15, lev,
                              signal_range=(b, e_), collect_diag=False)
            tr += r["trades"]
        n = len(tr)
        if not n:
            return dict(n=0, wr=0.0, exp_r=0.0, sum_r=0.0, pf=None)
        wins = [t for t in tr if t["pnl"] > 0]
        gp = sum(t["pnl"] for t in wins)
        gl = -sum(t["pnl"] for t in tr if t["pnl"] < 0)
        return dict(
            n=n, wr=round(len(wins) / n * 100, 1),
            exp_r=round(sum(t["r"] for t in tr) / n, 3),
            sum_r=round(sum(t["r"] for t in tr), 2),
            pf=round(gp / gl, 2) if gl > 0 else None)

    out = {}
    for name in se2.SETUPS:
        rec = winners.get(name)
        if not rec:
            continue
        g = {k: rec["genome"][k] for k in se2.GENES2 if k in rec["genome"]}
        lev = rec.get("rec_lev", 15)
        r = se2.run_setup(name, g, c4, ctx, c15, ts15, lev)
        st = ss.full_stats(r, lev, t0_ms=c4[0][0])

        folds = rec.get("oos_folds", [])
        n_oos = rec.get("n_oos_trades", 0)
        pool = oos_pooled(name, g, lev)
        reasons = []
        if pool["n"] < MIN_OOS_TRADES:
            reasons.append(f"на OOS всего {pool['n']} сделок < {MIN_OOS_TRADES}")
        if pool["exp_r"] <= 0:
            reasons.append(f"OOS-выборка: expectancy {pool['exp_r']:+.3f}R не > 0")
        if pool["pf"] is None or pool["pf"] < MIN_OOS_PF:
            reasons.append(f"OOS-выборка: PF {pool['pf']} < {MIN_OOS_PF}")
        if pool["sum_r"] <= 0:
            reasons.append(f"OOS-выборка: суммарно {pool['sum_r']:+.2f}R не > 0")
        if rec.get("oos_mean", -99) < ENABLE_MIN_OOS:
            reasons.append(f"средний скор экзаменов {rec.get('oos_mean', 0):+.2f} "
                           f"< {ENABLE_MIN_OOS}")
        if st["n"] < MIN_TOTAL_TRADES:
            reasons.append(f"всего {st['n']} сделок < {MIN_TOTAL_TRADES}")
        if st["pnl_usd"] <= 0:
            reasons.append(f"итог {st['ret_pct']:+.1f}% не положителен")
        pf = st.get("pf")
        if pf is None or pf < MIN_PF:
            reasons.append(f"profit factor {pf} < {MIN_PF}")
        if st["exp_r"] <= 0:
            reasons.append(f"expectancy {st['exp_r']:+.3f}R не положительна")
        if st["wr"] < st["wr_breakeven"]:
            reasons.append(f"WR {st['wr']}% ниже порога безубытка "
                           f"{st['wr_breakeven']}%")
        if r["ruined"]:
            reasons.append("слив фикс-базы")

        enabled = not reasons
        # предупреждения о нестабильности (не блокируют, но выводятся)
        cautions = []
        neg = [i + 1 for i, f in enumerate(folds) if f < 0]
        if neg:
            cautions.append(f"экзамены в минусе: {neg} из {len(folds)}")
        if folds and min(folds) < DEEP_FOLD:
            cautions.append(f"худший экзамен глубоко провальный "
                            f"({min(folds):+.1f}) — выборка фолда мала "
                            f"(4-9 сделок), но нестабильность реальна")
        if st["stop_quality"]["stop_then_tp_share"] > 30:
            cautions.append(f"{st['stop_quality']['stop_then_tp_share']}% стопов "
                            f"потом дошли бы до тейка — стоп тесноват")

        out[name] = dict(
            title=RU.get(name, name), genome=g, rec_lev=lev,
            enabled=enabled,
            reason="" if enabled else "; ".join(reasons),
            cautions=cautions,
            oos_mean=rec.get("oos_mean"), oos_folds=folds,
            n_oos_trades=n_oos, oos_pooled=pool,
            caution=rec.get("caution", False),
            stats=dict(
                n=st["n"], wr=st["wr"], wr_breakeven=st["wr_breakeven"],
                exp_r=st["exp_r"], pf=pf, ret=st["ret_pct"],
                dd=st["dd_pct"], reinvest_usd=st["reinvest"]["final_usd"],
                reinvest_pct=st["reinvest"]["final_pct"],
                reinvest_dd=st["reinvest"]["max_dd_pct"],
                avg_hold_h=st["timing"]["hold_avg_h"],
                tpm=st["tpm"],
                tpm_active=se2.stats(r)["tpm_active"],
                by_regime={k: dict(n=v["n"], wr=v["wr"], exp_r=v["exp_r"])
                           for k, v in st["by_regime"].items()},
                stop_then_tp=st["stop_quality"]["stop_then_tp_n"],
                n_stop=st["outcomes"]["stop"]["n"],
                near_n=st["near"]["n"], near_avg_r=st["near"]["avg_r"],
                near_strict_justified=st["near"].get("strict_justified"),
            ),
            ladder=rec.get("ladder", []))

        mark = "ВКЛЮЧЁН " if enabled else "отключён"
        print(f"{name:12} {mark} x{lev:<2} | сделок {st['n']:3} | WR {st['wr']:5.1f}% "
              f"(безуб {st['wr_breakeven']:.1f}%) | exp {st['exp_r']:+.3f}R | "
              f"PF {pf} | итог {st['ret_pct']:+7.1f}% | реинвест "
              f"${st['reinvest']['final_usd']:6.2f}")
        print(f"             OOS-выборка (3 экзамена вместе): {pool['n']} сделок, "
              f"WR {pool['wr']}%, exp {pool['exp_r']:+.3f}R, PF {pool['pf']}, "
              f"сумма {pool['sum_r']:+.2f}R | скоры фолдов "
              f"{[round(f, 1) for f in folds]}")
        if reasons:
            print(f"             ОТКЛЮЧЁН: {'; '.join(reasons)}")
        for c in cautions:
            print(f"             ⚠ {c}")
        br = st["by_regime"]
        print(f"             по режимам: " + " | ".join(
            f"{k} n={v['n']} WR={v['wr']}% exp={v['exp_r']:+.2f}R"
            for k, v in br.items() if v["n"]))

    with open("signal_setups2.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    n_on = sum(1 for v in out.values() if v["enabled"])
    print(f"\n-> signal_setups2.json: включено {n_on} из {len(out)} сетапов")


if __name__ == "__main__":
    main()
