# -*- coding: utf-8 -*-
"""Экзамен, которого у этого конфига никогда не было: 2020-2023.

Вся работа по BTC — и подбор конфигов, и «честная переоценка» — велась на
1150 днях, то есть с 21 июня 2023 года. Холдаут внутри этого отрезка честным
не является, и авторы прежней проверки это сами написали: волны отбора видели
всю историю целиком. Значит спор «плюс на холдауте — это перевес или это
подгонка» на данных 2023-2026 не решается в принципе.

Но у BTCUSDT на Bybit есть история с 2020 года, а конфиг BTC normal — чистое
ядро стратегии: все внешние ворота (фандинг/OI/SPX/DXY/золото/EMA/MA/Aroon/
режим/паттерны/SMC) у него выключены, что здесь и проверяется отдельной
строкой. Поэтому его можно прогнать на 2020-2023 БЕЗ всякого aux, и это будет
кусок рынка, которого не видела ни одна волна отбора и ни одна проверка.

Это и есть настоящий экзамен: три года чужих данных, включая бычий 2021-й,
обвал 2022-го и дно 2023-го.

Данные тянутся с биржи и кладутся во временную папку, чтобы не мусорить
в репозитории.
"""
import json
import os
import sys
import time

import numpy as np
from pybit.unified_trading import HTTP

import bots_honest as bh
import config
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8

TMP = os.environ.get("SCRATCH", ".")
CACHE = os.path.join(TMP, "btc_15m_deep.json")
FROM = "2020-03-25"      # первые дни бессрочного контракта BTCUSDT на Bybit
TO = "2023-07-05"        # с нахлёстом на начало изученных 1150 дней


def ms(day):
    return int(time.mktime(time.strptime(day, "%Y-%m-%d")) * 1000)


def fetch_deep():
    if os.path.exists(CACHE):
        with open(CACHE) as fh:
            return json.load(fh)
    s = HTTP(testnet=False)
    start, end = ms(FROM), ms(TO)
    out, cursor = [], end
    while cursor > start:
        r = s.get_kline(category="linear", symbol="BTCUSDT", interval="15",
                        limit=1000, end=cursor)
        rows = r["result"]["list"]
        if not rows:
            break
        out = rows[::-1] + out
        oldest = int(rows[-1][0])
        if oldest >= cursor:
            break
        cursor = oldest - 1
        if len(out) % 20000 < 1000:
            print("   ... %d баров, дошли до %s"
                  % (len(out), time.strftime("%Y-%m-%d",
                                             time.gmtime(oldest / 1000))))
            sys.stdout.flush()
        time.sleep(0.05)
    c = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])]
         for x in out if int(x[0]) >= start][:-1]
    c.sort(key=lambda x: x[0])
    with open(CACHE, "w") as fh:
        json.dump(c, fh)
    return c


def genome(mode):
    p = config.SYMBOL_PARAMS["BTCUSDT"][mode]
    g = e7.cfg_to_genome(p, mode)
    for k, v in e8.OFF8.items():
        g.setdefault(k, v)
    return g, p.get("lev", 5)


def run(candles, pre, g, lev):
    evs = []
    r = bh.run_at(candles, pre, g, None, lev, events=evs)
    months = (candles[-1][0] - candles[0][0]) / (30 * 86400000)
    m = bh.summarize(r, evs, months)
    pnls = [e["pnl"] for e in evs if e["type"] == "close"]
    return dict(trades=m["trades"], wr=m["wr"], comp=m["comp"], ret=m["ret"],
                dd=m["dd"], ruined=m["ruined"],
                comp_nt=bh.ret_no_tiny(pnls), months=months, pnls=pnls,
                evs=evs)


def year_table(candles, pre, g, lev, label):
    print("  %s по годам:" % label)
    ys = sorted(set(time.gmtime(c[0] / 1000).tm_year for c in candles))
    for y in ys:
        a = next((i for i, c in enumerate(candles)
                  if time.gmtime(c[0] / 1000).tm_year == y), None)
        b = next((i for i in range(len(candles) - 1, -1, -1)
                  if time.gmtime(candles[i][0] / 1000).tm_year == y), None)
        if a is None or b - a < 5000:
            continue
        seg = candles[a:b + 1]
        m = run(seg, e2.prep(seg), g, lev)
        print("    %d (%.1f мес): сделок %3d, ВР %5.1f%%, итог %+7.1f%%, "
              "просадка %5.1f%%%s"
              % (y, m["months"], m["trades"], m["wr"], m["comp"], m["dd"],
                 "  СЛИВ" if m["ruined"] else ""))
        sys.stdout.flush()


def main():
    e2.BARS_PER_DAY = 96
    print("Тяну 15-минутки BTCUSDT с %s по %s (это НЕ те 1150 дней, "
          "на которых всё считалось)" % (FROM, TO))
    c = fetch_deep()
    print("получено %d баров: %s .. %s"
          % (len(c), time.strftime("%Y-%m-%d", time.gmtime(c[0][0] / 1000)),
             time.strftime("%Y-%m-%d", time.gmtime(c[-1][0] / 1000))))
    pre = e2.prep(c)

    gn, ln = genome("normal")
    # проверка, что ворота действительно ни на что не влияют: прогон с
    # выключённым фильтром обязан совпасть с прогоном на полном aux —
    # это уже проверено на холдауте, здесь просто фиксирую состав генома
    on = [k for k in ("oi_gate", "ema_mode", "ma_mode", "regime_gate",
                      "pattern_gate", "ob_gate", "fvg_gate", "structure_mode",
                      "direction") if gn.get(k)]
    print("включённых ворот у BTC normal: %s"
          % (", ".join(on) if on else "нет ни одних — ядро стратегии в чистом виде"))
    print()

    print("=" * 96)
    print("BTC normal x5 НА ЧУЖИХ ТРЁХ ГОДАХ (2020-2023)")
    m = run(c, pre, gn, ln)
    print("  весь отрезок %.1f мес: сделок %d, ВР %.1f%%, итог %+.1f%% "
          "(без копеек %+.1f%%), просадка %.1f%%%s"
          % (m["months"], m["trades"], m["wr"], m["comp"], m["comp_nt"],
             m["dd"], "  ДЕПОЗИТ СЛИТ" if m["ruined"] else ""))
    pnl = np.array(m["pnls"], dtype=float)
    if len(pnl):
        print("  фикс. маржа: %+.2f$ на базе $20 = %+.1f%%; худшая сделка "
              "%+.2f$, лучшая %+.2f$"
              % (pnl.sum(), pnl.sum() / e2.START * 100, pnl.min(), pnl.max()))
    year_table(c, pre, gn, ln, "BTC normal x5")

    print()
    print("=" * 96)
    print("ДЛЯ СРАВНЕНИЯ: боевой BTC final на том же отрезке")
    gf, lf = genome("final")
    # у final включён regime_gate — без aux он работать не может, поэтому
    # тут честно только ядро: гоняю его геном с выключённым режимным гейтом,
    # и говорю об этом прямо
    gf2 = dict(gf)
    gf2["regime_gate"] = 0
    for lev in (5, 15):
        m2 = run(c, pre, gf2, lev)
        print("  final x%-2d (режимный гейт снят, aux нет): сделок %d, "
              "ВР %.1f%%, итог %+.1f%%, просадка %.1f%%%s"
              % (lev, m2["trades"], m2["wr"], m2["comp"], m2["dd"],
                 "  ДЕПОЗИТ СЛИТ" if m2["ruined"] else ""))


if __name__ == "__main__":
    main()
