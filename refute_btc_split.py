# -*- coding: utf-8 -*-
"""ПОПЫТКА СЛОМАТЬ НАХОДКУ «BTC normal x5 лучше боевого BTC final x15».

Линза: находка целиком живёт на одной произвольной цифре — HOLD_FRAC=0.72.
Если сдвинуть точку раскола истории, честный результат обязан шевелиться
слабо: настоящее преимущество конфига не знает, где мы провели черту.
Если же превосходство появляется и исчезает вместе с границей — значит мы
измерили не конфиг, а кусок рынка, который случайно попал в холдоут.

Что считается (ничего не подбирается):
  1. развёртка точки раскола 0.50..0.90 с шагом 0.01 для обоих конфигов
     (итог, просадка, сделки) — форма кривой важнее отдельных точек;
  2. семь опорных расколов из задания с полным набором честных осей,
     включая устойчивость (90 возмущений генома +-10%, копеечные обнулены);
  3. холдоут 0.72 пополам — где именно сидят деньги;
  4. помесячная раскладка и вклад крупнейших сделок;
  5. сравнение на РАВНОМ плече (обе конфигурации на x5 и на x15) — иначе
     сравниваются не стратегии, а разные размеры позиции.
"""
import sys
import time
from collections import OrderedDict

import numpy as np

import bots_honest as bh
import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

SYM = "BTCUSDT"
FRACS = [0.60, 0.65, 0.70, 0.72, 0.75, 0.80, 0.85]
N_PERT = 90
PERT_SEED = 7
NAN = float("nan")


class Tee:
    def __init__(self, path):
        self.out = sys.stdout
        self.fh = open(path, "w", encoding="utf-8")

    def write(self, s):
        self.out.write(s)
        self.fh.write(s)

    def flush(self):
        self.out.flush()
        self.fh.flush()


def genome(mode):
    g = e7.cfg_to_genome(config.SYMBOL_PARAMS[SYM][mode], mode)
    for k, v in e8.OFF8.items():
        g.setdefault(k, v)
    return g


def months_of(seg):
    return (seg[-1][0] - seg[0][0]) / (30 * 86400000)


def one(candles, aux, h, g, lev, want_events=False):
    """Прогон конфига на холдоуте [h..n) — как в archive_honest, но точка
    раскола передаётся снаружи."""
    n = len(candles)
    seg = candles[h:]
    pre = e2.prep(seg)
    filt = e8.make_filter8(g, {k: bh.slice_aux(v, h, n) for k, v in aux.items()})
    evs = []
    r = bh.run_at(seg, pre, g, filt, lev, events=evs)
    m = bh.summarize(r, evs, months_of(seg))
    pnls = [e["pnl"] for e in evs if e["type"] == "close"]
    m["ret_nt"] = bh.ret_no_tiny(pnls)
    m["tiny_share"] = (100.0 * sum(1 for p in pnls if bh.is_tiny(p)) / len(pnls)
                       if pnls else 0.0)
    if want_events:
        m["events"] = evs
        m["seg"] = seg
    return m


def robust(candles, aux, h, g, lev, n_pert=N_PERT, seed=PERT_SEED):
    """Те же 90 возмущений +-10%, что в archive_honest: медиана соседа с
    обнулёнными копейками и доля положительных соседей."""
    n = len(candles)
    seg = candles[h:]
    pre = e2.prep(seg)
    aux_h = {k: bh.slice_aux(v, h, n) for k, v in aux.items()}
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_pert):
        gp = bh.perturb(g, e8.GENES8, rng, bh.PERT)
        try:
            evs = []
            bh.run_at(seg, pre, gp, e8.make_filter8(gp, aux_h), lev, events=evs)
            vals.append(bh.ret_no_tiny([e["pnl"] for e in evs
                                        if e["type"] == "close"]))
        except Exception:                                    # noqa: BLE001
            continue
    a = np.array(vals, dtype=float) if vals else np.array([0.0])
    return float(np.median(a)), float((a > 0).mean() * 100.0), len(vals)


def day(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def month(ms):
    return time.strftime("%Y-%m", time.gmtime(ms / 1000))


def main():
    sys.stdout = Tee("refute_btc_split_out.txt")
    t_all = time.time()
    print("ЗАВИСИМОСТЬ НАХОДКИ ОТ ТОЧКИ РАСКОЛА ИСТОРИИ — "
          + time.strftime("%Y-%m-%d %H:%M"))
    pct5 = xd.fetch_daily_pct5()
    e2.BARS_PER_DAY = 96
    candles = ev.fetch(SYM, "15", bh.DAYS)
    aux = e8.make_aux_builder(pct5, 96)(SYM, candles)
    n = len(candles)
    cfgs = OrderedDict([("normal x5", (genome("normal"), 5)),
                        ("final x15", (genome("final"), 15))])
    print("история %d свечей 15m: %s .. %s"
          % (n, day(candles[0][0]), day(candles[-1][0])))

    # ------------------------------------------------- 1. непрерывная развёртка
    print()
    print("=" * 100)
    print("1. НЕПРЕРЫВНАЯ РАЗВЁРТКА ТОЧКИ РАСКОЛА 0.50..0.90 (шаг 0.01)")
    print("   итог% — реинвест (та же колонка, что в архивной таблице); "
          "в скобках сделок")
    print("  %7s %13s %5s | %17s %6s | %17s %6s | %11s"
          % ("раскол", "старт холд.", "мес", "normal x5", "DD%",
             "final x15", "DD%", "разрыв п.п."))
    sweep = []
    for k in range(50, 91):
        f = k / 100.0
        h = int(n * f)
        a = one(candles, aux, h, *cfgs["normal x5"])
        b = one(candles, aux, h, *cfgs["final x15"])
        sweep.append((f, a, b))
        print("  %7.2f %13s %5.1f | %+11.1f (%3d) %6.1f | %+11.1f (%3d) %6.1f "
              "| %+11.1f"
              % (f, day(candles[h][0]), months_of(candles[h:]),
                 a["comp"], a["trades"], a["dd"],
                 b["comp"], b["trades"], b["dd"], a["comp"] - b["comp"]))
    wins = sum(1 for f, a, b in sweep if a["comp"] > b["comp"])
    aplus = sum(1 for f, a, b in sweep if a["comp"] > 0)
    bplus = sum(1 for f, a, b in sweep if b["comp"] > 0)
    print("  ИТОГ развёртки (%d точек): normal обходит final в %d точках "
          "(%.0f%%); normal в плюсе в %d, final в плюсе в %d"
          % (len(sweep), wins, wins / len(sweep) * 100, aplus, bplus))
    med_a = float(np.median([a["comp"] for _, a, _ in sweep]))
    med_b = float(np.median([b["comp"] for _, _, b in sweep]))
    print("  медиана по всем точкам: normal %+.1f%%, final %+.1f%%"
          % (med_a, med_b))

    # ---------------------------------------------- 2. опорные расколы целиком
    print()
    print("=" * 100)
    print("2. СЕМЬ ОПОРНЫХ РАСКОЛОВ — ПОЛНЫЙ НАБОР ЧЕСТНЫХ ОСЕЙ")
    print("   робаст = медиана 90 возмущений генома +-10% на этом же холдоуте, "
          "копеечные выходы обнулены")
    print("  %10s %7s %5s %5s %9s %10s %8s %8s %8s %6s"
          % ("конфиг", "раскол", "мес", "сдел", "холдаут%", "б/копеек%",
             "просад%", "копееч%", "робаст%", "доля+"))
    table = {}
    for name, (g, lev) in cfgs.items():
        for f in FRACS:
            h = int(n * f)
            m = one(candles, aux, h, g, lev)
            rm, sp, nn = robust(candles, aux, h, g, lev)
            table[(name, f)] = (m, rm, sp)
            print("  %10s %7.2f %5.1f %5d %+9.1f %+10.1f %8.1f %8.0f %+8.1f "
                  "%5.0f%%"
                  % (name, f, months_of(candles[h:]), m["trades"], m["comp"],
                     m["ret_nt"], m["dd"], m["tiny_share"], rm, sp))
        print()
    print("  СВОДКА ПО РАЗРЫВУ (normal минус final), по каждой оси:")
    print("  %7s %13s %12s %14s %12s"
          % ("раскол", "холдаут п.п.", "робаст п.п.", "просадка п.п.",
             "кто лучше"))
    for f in FRACS:
        a, ra, sa = table[("normal x5", f)]
        b, rb, sb = table[("final x15", f)]
        who = "normal" if (a["comp"] > b["comp"] and ra > rb) else (
            "final" if (b["comp"] > a["comp"] and rb > ra) else "спор")
        print("  %7.2f %+13.1f %+12.1f %+14.1f %12s"
              % (f, a["comp"] - b["comp"], ra - rb, a["dd"] - b["dd"], who))

    # ------------------------------------------------------ 3. холдоут пополам
    print()
    print("=" * 100)
    print("3. ХОЛДОУТ 0.72 ПОПОЛАМ")
    h = int(n * 0.72)
    for name, (g, lev) in cfgs.items():
        m = one(candles, aux, h, g, lev, want_events=True)
        seg = m["seg"]
        mid_t = seg[len(seg) // 2][0]
        cl = [e for e in m["events"] if e["type"] == "close"]
        first = [e for e in cl if e["t"] < mid_t]
        second = [e for e in cl if e["t"] >= mid_t]
        print("  %s: холдоут %s..%s, середина %s, сделок %d"
              % (name, day(seg[0][0]), day(seg[-1][0]), day(mid_t), len(cl)))
        for tag, part in (("первая половина", first),
                          ("вторая половина", second)):
            s = sum(e["pnl"] for e in part)
            snt = sum(e["pnl"] for e in part if not bh.is_tiny(e["pnl"]))
            w = sum(1 for e in part if e["pnl"] > 0)
            print("     %s: сделок %3d, итог %+7.1f%% (без копеек %+7.1f%%), "
                  "плюсовых %d"
                  % (tag, len(part), s / e2.START * 100, snt / e2.START * 100,
                     w))
        half = len(seg) // 2
        p0, p1, p2 = seg[0][4], seg[half][4], seg[-1][4]
        print("     сама монета: %.0f -> %.0f (%+.1f%%) -> %.0f (%+.1f%%)"
              % (p0, p1, (p1 / p0 - 1) * 100, p2, (p2 / p1 - 1) * 100))

    # ----------------------------------------------------------- 4. помесячно
    print()
    print("=" * 100)
    print("4. ПОМЕСЯЧНО НА ХОЛДОУТЕ 0.72 (итог % от базы $20)")
    for name, (g, lev) in cfgs.items():
        m = one(candles, aux, h, g, lev, want_events=True)
        cl = [e for e in m["events"] if e["type"] == "close"]
        by = OrderedDict()
        for e in cl:
            a = by.setdefault(month(e["t"]), [0.0, 0, 0.0])
            a[0] += e["pnl"]
            a[1] += 1
            if not bh.is_tiny(e["pnl"]):
                a[2] += e["pnl"]
        print("  %s:" % name)
        pos = 0
        for k, (s, c, snt) in by.items():
            pos += 1 if s > 0 else 0
            print("     %s  сделок %3d  итог %+7.1f%%  без копеек %+7.1f%%"
                  % (k, c, s / e2.START * 100, snt / e2.START * 100))
        tot = sum(v[0] for v in by.values())
        best = max(by.items(), key=lambda kv: kv[1][0])
        print("     месяцев в плюсе %d из %d; итог %+.1f%%; лучший месяц %s "
              "%+.1f%% = %.0f%% всего итога"
              % (pos, len(by), tot / e2.START * 100, best[0],
                 best[1][0] / e2.START * 100,
                 (best[1][0] / tot * 100) if tot else NAN))
        pn = sorted((e["pnl"] for e in cl), reverse=True)
        for kk in (1, 2, 3):
            print("     топ-%d сделок дают %+.1f%% = %.0f%% итога"
                  % (kk, sum(pn[:kk]) / e2.START * 100,
                     (sum(pn[:kk]) / tot * 100) if tot else NAN))

    # ------------------------------------------------------- 5. равное плечо
    print()
    print("=" * 100)
    print("5. СРАВНЕНИЕ НА РАВНОМ ПЛЕЧЕ (холдоут 0.72)")
    print("   иначе сравнивается не стратегия, а размер позиции: x5 против x15")
    print("  %10s %6s %5s %9s %10s %8s %8s %6s"
          % ("конфиг", "плечо", "сдел", "холдаут%", "б/копеек%", "просад%",
             "робаст%", "доля+"))
    for name, (g, _) in cfgs.items():
        for lev in (5, 15):
            m = one(candles, aux, h, g, lev)
            rm, sp, _ = robust(candles, aux, h, g, lev)
            print("  %10s x%-5d %5d %+9.1f %+10.1f %8.1f %+8.1f %5.0f%%"
                  % (name, lev, m["trades"], m["comp"], m["ret_nt"], m["dd"],
                     rm, sp))
    print("\n(время работы %.0f c)" % (time.time() - t_all))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
