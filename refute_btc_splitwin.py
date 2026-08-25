# -*- coding: utf-8 -*-
"""ПРОДОЛЖЕНИЕ ЛИНЗЫ «ПРОИЗВОЛЬНАЯ ГРАНИЦА» — теперь честно к самой линзе.

refute_btc_split.py показал: превосходство normal x5 над final x15 держится
при любом расколе 0.50..0.90. Но у этой проверки есть встроенная слабость,
которую надо назвать самому, а не ждать, пока назовут другие: сорок один
холдоут — это сорок один ХВОСТ одной и той же истории. Они вложены друг в
друга, делят между собой почти все сделки и все начинаются внутри одного
куска рынка (биткоин со 109 тысяч вниз). Согласие таких окон между собой
означает лишь то, что они посчитаны по одним и тем же сделкам.

Здесь та же мысль «граница произвольна» доводится до конца:
  1. СКОЛЬКО ОБЩЕГО у вложенных холдоутов — в сделках, а не на словах.
     Это цена доверия к результату «41 из 41».
  2. Произволен не только КОНЕЦ обучающей части, но и решение брать в
     холдоут именно ХВОСТ. Беру окно той же длины (28% истории) и катаю его
     по всей истории от начала до конца. Тогда в выборку попадает и бычий
     2023-2024, которого хвостовые холдоуты не видят вовсе.
  3. Связь разрыва с направлением рынка в окне: если преимущество normal
     появляется ровно там, где биткоин падает, находка — про рынок.
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
WIN = 1.0 - bh.HOLD_FRAC          # длина окна = 28% истории, как у холдоута
STEP = 0.02


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


def day(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def run_window(candles, aux, a, b, g, lev):
    seg = candles[a:b]
    evs = []
    r = bh.run_at(seg, e2.prep(seg), g,
                  e8.make_filter8(g, {k: bh.slice_aux(v, a, b)
                                      for k, v in aux.items()}),
                  lev, events=evs)
    pnls = [e["pnl"] for e in evs if e["type"] == "close"]
    ts = [e["t"] for e in evs if e["type"] == "close"]
    return dict(ret=(r["balance"] / e2.START - 1) * 100,
                dd=r["max_dd"] * 100, trades=r["trades"], pnls=pnls, ts=ts,
                ret_nt=bh.ret_no_tiny(pnls))


def main():
    sys.stdout = Tee("refute_btc_splitwin_out.txt")
    t0 = time.time()
    print("ЦЕНА СОГЛАСИЯ ВЛОЖЕННЫХ ХОЛДОУТОВ И ОКНО, КАТЯЩЕЕСЯ ПО ВСЕЙ "
          "ИСТОРИИ — " + time.strftime("%Y-%m-%d %H:%M"))
    pct5 = xd.fetch_daily_pct5()
    e2.BARS_PER_DAY = 96
    candles = ev.fetch(SYM, "15", bh.DAYS)
    aux = e8.make_aux_builder(pct5, 96)(SYM, candles)
    n = len(candles)
    cfgs = OrderedDict([("normal x5", (genome("normal"), 5)),
                        ("final x15", (genome("final"), 15))])

    # ------------------------------------------- 1. вложенность в числах
    print()
    print("=" * 104)
    print("1. НАСКОЛЬКО ВЛОЖЕНЫ ХОЛДОУТЫ РАЗНЫХ РАСКОЛОВ (общие сделки)")
    print("   считаю по normal x5: сколько сделок холдоута 0.85 уже входили "
          "в холдоут 0.60 и т.д.")
    g, lev = cfgs["normal x5"]
    base = run_window(candles, aux, int(n * 0.60), n, g, lev)
    print("  %7s %6s %14s %16s"
          % ("раскол", "сделок", "из них общих", "своих новых"))
    for f in (0.60, 0.65, 0.70, 0.72, 0.75, 0.80, 0.85):
        w = run_window(candles, aux, int(n * f), n, g, lev)
        common = len(set(w["ts"]) & set(base["ts"]))
        print("  %7.2f %6d %14d %16d"
              % (f, w["trades"], common, w["trades"] - common))
    print("  Читать так: холдоуты разных расколов состоят из ОДНИХ И ТЕХ ЖЕ "
          "сделок. «41 точка из 41» —")
    print("  это не 41 независимое свидетельство, а одно и то же измерение, "
          "показанное с разной обрезкой.")

    # -------------------------------------- 2. окно, катящееся по всей истории
    print()
    print("=" * 104)
    print("2. ОКНО ДЛИНОЙ 28%% ИСТОРИИ, КАТЯЩЕЕСЯ ОТ НАЧАЛА ДО КОНЦА "
          "(шаг %.0f%% истории)" % (STEP * 100))
    print("   хвостовые холдоуты не видят бычий 2023-2024 вовсе; здесь он "
          "входит в выборку")
    print("  %13s %13s %5s | %17s | %17s | %11s %10s"
          % ("начало окна", "конец окна", "мес", "normal x5", "final x15",
             "разрыв п.п.", "цена BTC"))
    rows = []
    k = 0.0
    while k + WIN <= 1.0 + 1e-9:
        a = int(n * k)
        b = int(n * (k + WIN))
        seg = candles[a:b]
        ra = run_window(candles, aux, a, b, *cfgs["normal x5"])
        rb = run_window(candles, aux, a, b, *cfgs["final x15"])
        move = (seg[-1][4] / seg[0][4] - 1) * 100
        rows.append((k, ra, rb, move))
        print("  %13s %13s %5.1f | %+10.1f%% (%3d) | %+10.1f%% (%3d) | "
              "%+11.1f %+9.1f%%"
              % (day(seg[0][0]), day(seg[-1][0]),
                 (seg[-1][0] - seg[0][0]) / (30 * 86400000),
                 ra["ret"], ra["trades"], rb["ret"], rb["trades"],
                 ra["ret"] - rb["ret"], move))
        k += STEP
    wins = sum(1 for _, ra, rb, _ in rows if ra["ret"] > rb["ret"])
    ap = sum(1 for _, ra, _, _ in rows if ra["ret"] > 0)
    bp = sum(1 for _, _, rb, _ in rows if rb["ret"] > 0)
    print("  ИТОГ: окон %d | normal впереди в %d (%.0f%%) | normal в плюсе "
          "в %d | final в плюсе в %d"
          % (len(rows), wins, wins / len(rows) * 100, ap, bp))
    up = [(ra, rb) for _, ra, rb, m in rows if m > 0]
    dn = [(ra, rb) for _, ra, rb, m in rows if m <= 0]
    for tag, sel in (("окна, где BTC РОС", up), ("окна, где BTC ПАДАЛ", dn)):
        if not sel:
            continue
        na = float(np.median([x[0]["ret"] for x in sel]))
        nb = float(np.median([x[1]["ret"] for x in sel]))
        w = sum(1 for x in sel if x[0]["ret"] > x[1]["ret"])
        print("  %-22s (%2d шт): медиана normal %+7.1f%% | медиана final "
              "%+7.1f%% | normal впереди в %d" % (tag, len(sel), na, nb, w))
    gap = np.array([ra["ret"] - rb["ret"] for _, ra, rb, _ in rows])
    mv = np.array([m for _, _, _, m in rows])
    if len(rows) > 2:
        print("  корреляция «разрыв normal-final» с ходом цены в окне: "
              "%+.2f (отрицательная = преимущество живёт в падении)"
              % float(np.corrcoef(gap, mv)[0, 1]))
    print("\n(время работы %.0f c)" % (time.time() - t0))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
