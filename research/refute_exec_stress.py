# -*- coding: utf-8 -*-
"""Три способа убить находку про частичную фиксацию. Экзамен закрыт.

Находка: половина позиции на 1.5R с трейлом по остатку улучшает доход на
единицу просадки у 17 из 20 независимых стратегий. Знаковый критерий дал
p=0.003, но НА НЕГО НЕЛЬЗЯ ОПИРАТЬСЯ КАК ЕСТЬ: двадцать стратегий торгуют одни
и те же пять монет на одном и том же отрезке времени, их результаты связаны
общим рынком, и настоящее число независимых наблюдений заметно меньше двадцати.

Здесь три проверки, каждая может находку похоронить.

1. БЛОЧНЫЙ БУТСТРЭП ПО ВРЕМЕНИ. Берём помесячную РАЗНИЦУ доходности между
   схемой и базой на общем портфеле и пересобираем историю блоками по три
   месяца. Если ноль попадает в интервал — перевес не измерен.

2. СДВИГ ЧАСА ВХОДА. Если преимущество схемы держится и при входе на бар-два
   позже, оно про сопровождение позиции. Если рассыпается — оно про то же
   самое везение с ценой входа, что и у базы.

3. УТРОЕННЫЕ ИЗДЕРЖКИ. Частичная фиксация добавляет третью ногу комиссии на
   каждой сделке. При тройном проскальзывании и комиссии схема с лишней ногой
   обязана страдать сильнее базы — если она этого не делает, что-то посчитано
   неверно.
"""
import json
import os
import sys
import time

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import engine               # noqa: E402
import portfolio            # noqa: E402
import rdata                # noqa: E402
import refute_exec_engine as xe   # noqa: E402
import refute_exec_lib as L  # noqa: E402
import refute_exec_risk as R  # noqa: E402
import universe             # noqa: E402

OUT = os.path.join(DIR, "out")
NAMES = ["c_willr", "bos", "vol_spike", "supertrend", "c_rsi", "c_mfi",
         "c_cci", "c_bb", "macd", "atr_break", "bb_break", "vol_adj_mom",
         "mom_continuation", "vol_expansion", "rsi_mr", "ema_dist",
         "false_break", "keltner_mr", "donchian", "tsmom"]

PART = dict(partial_R=1.5, partial_frac=0.5, trail_after_partial=1.0)


def monthly(per_arm, k):
    c = portfolio.combine(per_arm, risk_each=k)
    mo = portfolio.monthly_from_curve(c["times"], c["curve"])
    return dict(mo), c


def paired_months(a_arm, b_arm):
    """Помесячная разница доходности двух схем при просадке 20% у каждой."""
    ka = R.calibrate(a_arm)
    kb = R.calibrate(b_arm)
    ma, ca = monthly(a_arm, ka)
    mb, cb = monthly(b_arm, kb)
    keys = sorted(set(ma) & set(mb))
    d = np.array([mb[x] - ma[x] for x in keys])
    return d, keys, ca, cb


def block_boot(d, block=3, n=20000, seed=7):
    """Блочный бутстрэп средней разницы. Блоки — потому что месяцы связаны."""
    rng = np.random.default_rng(seed)
    n_obs = len(d)
    nb = max(1, n_obs // block)
    out = np.empty(n)
    for s in range(n):
        st = rng.integers(0, max(1, n_obs - block + 1), nb)
        idx = np.concatenate([np.arange(i, i + block) for i in st])[:n_obs]
        out[s] = d[np.clip(idx, 0, n_obs - 1)].mean()
    return out


def main():
    reg, _ = universe.load()
    res = {}

    print("1) БЛОЧНЫЙ БУТСТРЭП ПО ВРЕМЕНИ, ансамбль из четырёх правил")
    base = L.ensemble(reg, xe.XCfg())
    part = L.ensemble(reg, xe.XCfg(**PART))
    d, keys, ca, cb = paired_months(base["per_arm"], part["per_arm"])
    bs = block_boot(d)
    lo, hi = np.percentile(bs, [2.5, 97.5])
    print("   месяцев сравнено: %d" % len(d))
    print("   средняя разница в месяц: %+.2f п.п." % (100 * d.mean()))
    print("   доверительный интервал 95%%: от %+.2f до %+.2f п.п."
          % (100 * lo, 100 * hi))
    print("   доля прогонов, где разница положительна: %.0f%%"
          % (100 * (bs > 0).mean()))
    print("   %s" % ("ноль НЕ накрыт — перевес измерен" if lo > 0
                     else "ноль накрыт — перевес НЕ измерен"))
    res["boot"] = dict(n=len(d), mean=float(d.mean()), lo=float(lo),
                       hi=float(hi), p_pos=float((bs > 0).mean()))

    print("\n   то же на всех двадцати стратегиях сразу (один общий портфель)")
    ba, pa = {}, {}
    for name in NAMES:
        if name not in reg:
            continue
        r1 = L.ensemble(reg, xe.XCfg(), members=[name])
        r2 = L.ensemble(reg, xe.XCfg(**PART), members=[name])
        if r1:
            ba.update(r1["per_arm"])
        if r2:
            pa.update(r2["per_arm"])
    d2, _, _, _ = paired_months(ba, pa)
    bs2 = block_boot(d2)
    lo2, hi2 = np.percentile(bs2, [2.5, 97.5])
    print("   месяцев %d, разница %+.2f п.п., интервал от %+.2f до %+.2f"
          % (len(d2), 100 * d2.mean(), 100 * lo2, 100 * hi2))
    res["boot20"] = dict(n=len(d2), mean=float(d2.mean()), lo=float(lo2),
                         hi=float(hi2), p_pos=float((bs2 > 0).mean()))

    print("\n2) СДВИГ ЧАСА ВХОДА: держится ли преимущество схемы")
    print("%-14s %12s %12s %10s" % ("сдвиг", "база", "половина 1.5R", "разница"))
    res["delay"] = {}
    for dl in (0, 1, 2, 3):
        a = L.ensemble(reg, xe.XCfg(delay=dl))
        b = L.ensemble(reg, xe.XCfg(delay=dl, **PART))
        ka, kb = R.calibrate(a["per_arm"]), R.calibrate(b["per_arm"])
        ca = portfolio.combine(a["per_arm"], risk_each=ka)
        cb = portfolio.combine(b["per_arm"], risk_each=kb)
        res["delay"][dl] = dict(base=ca["mo"], part=cb["mo"])
        print("%-14s %+11.2f%% %+11.2f%% %+9.2f" %
              ("+%d бара" % dl, 100 * ca["mo"], 100 * cb["mo"],
               100 * (cb["mo"] - ca["mo"])))

    print("\n3) УТРОЕННЫЕ ИЗДЕРЖКИ (комиссия x3, проскальзывание x3)")
    print("%-24s %12s %12s" % ("схема", "обычные", "утроенные"))
    res["cost"] = {}
    for sname, kw in (("маркет+стоп", {}), ("половина 1.5R+трейл", PART)):
        norm = L.ensemble(reg, xe.XCfg(**kw))
        hard = L.ensemble(reg, xe.XCfg(**kw),
                          cfg=engine.Cfg(risk_frac=0.01, max_lev=10.0,
                                         fee=3 * rdata.TAKER_FEE,
                                         slip_mult=3.0))
        cn = portfolio.combine(norm["per_arm"],
                               risk_each=R.calibrate(norm["per_arm"]))
        ch = portfolio.combine(hard["per_arm"],
                               risk_each=R.calibrate(hard["per_arm"]))
        res["cost"][sname] = dict(normal=cn["mo"], hard=ch["mo"])
        print("%-24s %+11.2f%% %+11.2f%%"
              % (sname, 100 * cn["mo"], 100 * ch["mo"]))

    with open(os.path.join(OUT, "refute_exec_stress.json"), "w",
              encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, default=float, indent=1)
    print("\nсохранено -> out/refute_exec_stress.json")


if __name__ == "__main__":
    main()
