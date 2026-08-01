# -*- coding: utf-8 -*-
"""Честное сравнение старых сигналов (v1, отбор v9) и новых (v2, отбор v11)
на ОДНОМ И ТОМ ЖЕ неприкосновенном holdout — последних 10.6 месяцах, которых
не видел ни один из отборов.

Почему так: прежние цифры v1 (+108%, +194%) считались на всей истории,
включая период, на котором конфиг и подбирался. Сравнивать их с новыми
цифрами напрямую нельзя. Здесь оба поколения оцениваются на данных, которых
не видели ОБА, и на одном плече — тогда разница означает именно качество
стратегии, а не разницу условий.

Метрики exp_r / WR / PF / число сделок от плеча не зависят; итог % и просадка
считаются на общем плече COMMON_LEV.
"""

import json

import evolution as ev
import signal_engine as se1
import signal_engine2 as se2

COMMON_LEV = 10
HOLD_FRAC = 0.28


def metrics(trades, engine_start):
    n = len(trades)
    if not n:
        return dict(n=0, wr=0.0, exp_r=0.0, sum_r=0.0, pf=None, ret=0.0, dd=0.0)
    wins = [t for t in trades if t["pnl"] > 0]
    gp = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    bal, peak, dd = engine_start, engine_start, 0.0
    for t in sorted(trades, key=lambda x: x["exit_ts"]):
        bal += t["pnl"]
        peak = max(peak, bal)
        dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
    return dict(
        n=n, wr=round(len(wins) / n * 100, 1),
        exp_r=round(sum(t["r"] for t in trades) / n, 3),
        sum_r=round(sum(t["r"] for t in trades), 2),
        pf=round(gp / gl, 2) if gl > 0 else None,
        ret=round((bal / engine_start - 1) * 100, 1),
        dd=round(dd * 100, 1))


def main():
    c4 = ev.fetch("BTCUSDT", "240", 1150)
    c15 = ev.fetch("BTCUSDT", "15", 1150)
    ts15 = [c[0] for c in c15]
    n = len(c4)
    hold = int(n * (1 - HOLD_FRAC))
    seg = (hold, n)

    ctx1 = se1.prep_context(c4)
    ctx2 = se2.prep_context(c4)

    with open("signal_setups.json", encoding="utf-8") as fh:
        old = json.load(fh)
    with open("signal_setups2.json", encoding="utf-8") as fh:
        new = json.load(fh)

    import time
    d = lambda ms: time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
    print(f"HOLDOUT: бары [{hold}..{n}) = {d(c4[hold][0])}..{d(c4[-1][0])} "
          f"(10.6 мес, BTC {(c4[-1][4]/c4[hold][4]-1)*100:+.1f}%)")
    print(f"оба поколения на плече x{COMMON_LEV}; ни один отбор этих данных не видел\n")

    hdr = (f"{'сетап':13} {'поколение':10} {'сдел':>5} {'WR%':>6} {'exp R':>7} "
           f"{'PF':>5} {'сумма R':>8} {'итог%':>7} {'DD%':>6}")
    print(hdr)
    print("-" * len(hdr))

    tot = {"v1": [], "v2": []}
    for name in se2.SETUPS:
        rows = []
        if name in old:
            g1 = old[name]["genome"]
            r1 = se1.run_setup(name, g1, c4, ctx1, c15, ts15, COMMON_LEV,
                               signal_range=seg)
            m1 = metrics(r1["trades"], se1.START)
            tot["v1"] += r1["trades"]
            rows.append(("v1 (старый)", m1))
        if name in new:
            g2 = new[name]["genome"]
            r2 = se2.run_setup(name, g2, c4, ctx2, c15, ts15, COMMON_LEV,
                               signal_range=seg, collect_diag=False)
            m2 = metrics(r2["trades"], se2.START)
            tot["v2"] += r2["trades"]
            rows.append(("v2 (новый) ", m2))
        for label, m in rows:
            print(f"{name:13} {label:10} {m['n']:5} {m['wr']:6.1f} "
                  f"{m['exp_r']:+7.3f} {str(m['pf']):>5} {m['sum_r']:+8.2f} "
                  f"{m['ret']:+7.1f} {m['dd']:6.1f}")
        print()

    print("=" * len(hdr))
    print("ВСЕ СЕТАПЫ ВМЕСТЕ (сумма сделок всех шести):")
    for k, label in (("v1", "v1 (старый)"), ("v2", "v2 (новый) ")):
        m = metrics(tot[k], se2.START)
        print(f"{'ИТОГО':13} {label:10} {m['n']:5} {m['wr']:6.1f} "
              f"{m['exp_r']:+7.3f} {str(m['pf']):>5} {m['sum_r']:+8.2f} "
              f"{m['ret']:+7.1f} {m['dd']:6.1f}")


if __name__ == "__main__":
    main()
