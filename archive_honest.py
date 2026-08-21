# -*- coding: utf-8 -*-
"""ЧЕСТНАЯ ПЕРЕПРОВЕРКА АРХИВНЫХ БОТОВ — той же методологией, что и пятёрки.

Зачем отдельный скрипт. bots_honest.py меряет только режим "final" (пять
рабочих ботов). В архиве лежат тринадцать отвергнутых конфигов режимов
normal / bear / turbo, и вопрос «а нет ли там чего-то лучше» законный: их
отвергали прежними волнами отбора, по прежним критериям и на прежнем движке.

Здесь ничего не подбирается — только меряется, и ровно тем же способом:
  * тот же раскол истории: первые 72% — обучение, остаток — холдоут;
  * то же плечо, что записано у конфига;
  * та же устойчивость: 90 возмущений генома по +-10% на числовых генах,
    прогон каждого на холдауте, медиана и доля положительных.

ГЛАВНАЯ ОГОВОРКА ТА ЖЕ, что у финалистов, и она здесь даже сильнее.
Холдоут не является неприкосновенным: волны отбора видели всю историю.
Положительный результат на нём почти ничего не доказывает; отрицательный
доказывает многое. А архивные конфиги вдобавок уже проиграли один отбор —
если какой-то из них теперь окажется наверху, это скорее говорит о шуме в
сравнении, чем о том, что его зря отвергли.
"""
import sys
import time

import numpy as np

import bots_honest as bh
import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

MODES = ("normal", "bear", "turbo", "final")


def build(sym, mode, p, pct5):
    g = e7.cfg_to_genome(p, mode)
    for k, v in e8.OFF8.items():
        g.setdefault(k, v)
    candles = ev.fetch(sym, "15", bh.DAYS)
    aux = e8.make_aux_builder(pct5, 96)(sym, candles)
    n = len(candles)
    h = int(n * bh.HOLD_FRAC)
    ho_c = candles[h:]
    months = (ho_c[-1][0] - ho_c[0][0]) / (30 * 86400000)
    return dict(
        g=g, lev=p.get("lev", 5), candles=candles, aux=aux, n=n, hold_i=h,
        hold=dict(candles=ho_c, pre=e2.prep(ho_c), months=months,
                  filt=e8.make_filter8(
                      g, {k: bh.slice_aux(v, h, n) for k, v in aux.items()})))


def evaluate(sym, mode, p, pct5, n_pert=90, seed=7):
    d = build(sym, mode, p, pct5)
    s = d["hold"]
    evs = []
    r = bh.run_at(s["candles"], s["pre"], d["g"], s["filt"], d["lev"],
                  events=evs)
    m = bh.summarize(r, evs, s["months"])
    # Тот же счёт без копеечных выходов. Перенос стопа в безубыток закрывает
    # цикл у цены входа: формально «прибыльная сделка», фактически ноль. Такие
    # выходы раздувают винрейт до 95-98% и делают сравнение с честными
    # конфигами бессмысленным, поэтому меряем обе величины.
    tiny = bh.cycle_metrics(evs, s["candles"], d["g"], s["months"])
    pnls = [e["pnl"] for e in evs if e["type"] == "close"]
    comp_nt = bh.ret_no_tiny(pnls)
    # устойчивость: те же +-10% по числовым генам, что у финалистов
    rng = np.random.default_rng(seed)
    # genes — не список имён, а спецификация границ (e8.GENES8): perturb
    # отдаёт её в e4.ga_tools, который зажимает возмущённый геном в
    # допустимые пределы. Ровно то же, чем пользуется bots_honest.
    genes = e8.GENES8
    rets, rets_nt = [], []
    for _ in range(n_pert):
        gp = bh.perturb(d["g"], genes, rng, bh.PERT)
        try:
            ev2 = []
            r2 = bh.run_at(s["candles"], s["pre"], gp,
                           e8.make_filter8(gp, {k: bh.slice_aux(v, d["hold_i"],
                                                                d["n"])
                                                for k, v in d["aux"].items()}),
                           d["lev"], events=ev2)
            rets.append(bh.summarize(r2, ev2, s["months"])["comp"])
            p2 = [e["pnl"] for e in ev2 if e["type"] == "close"]
            rets_nt.append(bh.ret_no_tiny(p2))
        except Exception:                          # noqa: BLE001
            continue
    a = np.array(rets, dtype=float) if rets else np.array([0.0])
    ant = np.array(rets_nt, dtype=float) if rets_nt else np.array([0.0])
    return dict(symbol=sym, mode=mode, lev=d["lev"],
                trades=m.get("trades", 0), wr=m.get("wr", 0.0),
                comp=m.get("comp", 0.0), dd=m.get("dd", 0.0),
                per_month=m.get("per_month", 0.0),
                comp_nt=float(comp_nt),
                tiny_share=float((tiny or {}).get("tiny_share", 0.0)),
                wr_ex_tiny=float((tiny or {}).get("wr_ex_tiny", 0.0)),
                rob_med=float(np.median(a)), rob_p10=float(np.percentile(a, 10)),
                share_pos=float((a > 0).mean() * 100.0),
                rob_med_nt=float(np.median(ant)),
                share_pos_nt=float((ant > 0).mean() * 100.0),
                n_pert=len(rets))


def main():
    print("ЧЕСТНАЯ ПЕРЕПРОВЕРКА АРХИВА — " + time.strftime("%Y-%m-%d %H:%M"))
    print("тот же раскол 72/28, то же плечо конфига, те же 90 возмущений +-10%")
    print("холдоут: последние 28%% истории (у финалистов он же)\n")
    pct5 = xd.fetch_daily_pct5()
    e2.BARS_PER_DAY = 96
    rows, skipped = [], []
    for sym, modes in config.SYMBOL_PARAMS.items():
        for mode in MODES:
            p = modes.get(mode)
            if not p:
                continue
            if mode == "turbo" or "step" not in p:
                skipped.append((sym, mode,
                                "другая механика (stop_k/tp_k), этим движком "
                                "не считается"))
                continue
            try:
                rows.append(evaluate(sym, mode, p, pct5))
                print("  посчитан %-9s %-7s" % (sym, mode))
                sys.stdout.flush()
            except Exception as exc:               # noqa: BLE001
                skipped.append((sym, mode, str(exc)[:70]))
                print("  ПРОПУЩЕН %-9s %-7s: %s" % (sym, mode, str(exc)[:60]))

    rows.sort(key=lambda r: -r["rob_med_nt"])
    print("\nАРХИВ НА ХОЛДАУТЕ, отсортировано по устойчивости")
    print("%-6s %-6s %4s %6s %8s %8s %7s %6s %8s %9s %6s"
          % ("монета", "режим", "плечо", "сдел", "холдаут", "б/копеек",
             "просад", "копеек", "ВР б/коп", "робаст б/к", "доля+"))
    for r in rows:
        print("%-6s %-6s %4s %6d %+7.1f%% %+7.1f%% %6.1f%% %5.0f%% %7.1f%% "
              "%+8.1f%% %5.0f%%"
              % (r["symbol"].replace("USDT", ""), r["mode"], "x%d" % r["lev"],
                 r["trades"], r["comp"], r["comp_nt"], r["dd"],
                 r["tiny_share"], r["wr_ex_tiny"], r["rob_med_nt"],
                 r["share_pos_nt"]))
    if skipped:
        print("\nне удалось посчитать:")
        for s, m, e in skipped:
            print("   %-9s %-7s %s" % (s, m, e))

    import json
    with open("webapp/data/archive_honest.json", "w", encoding="utf-8") as fh:
        json.dump(dict(generated=time.strftime("%Y-%m-%d %H:%M"), rows=rows,
                       skipped=skipped, hold_frac=bh.HOLD_FRAC,
                       pert=bh.PERT), fh, ensure_ascii=False)
    print("\nсохранено: webapp/data/archive_honest.json")


if __name__ == "__main__":
    main()
