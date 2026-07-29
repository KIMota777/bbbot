# -*- coding: utf-8 -*-
"""Финальные данные для отчёта: волны конфигов на одних и тех же 3.2 годах.

Волны:
  W1 — первый подбор RSI-сетки (до эволюции);
  W2 — эволюция v1 + направление (ETH short-only с фильтром);
  W3 — глубокая эволюция v2 (честный движок): BTC/ETH обновлены;
  W4/W5 — внешние сигналы и индикаторы (если приняты).

Для финальной волны — разбивка по трём годовым отрезкам и помесячная лента.
Вывод: report3y.json
"""

import json
import os
import time

import evolution as ev
import evolution2 as e2
import evolution4 as e4
import evolution5 as e5
import ext_data as xd

DAYS = 1150
Y = 35040  # свечей в году на 15m

W1 = {
    "DOGEUSDT": dict(rsi_os=30, zone_l=0.25, zone_s=0.25, window=400, step=0.010,
                     levels=3, mult=1.5, tp=0.040, sweep=0.020, max_bars=192),
    "LTCUSDT": dict(rsi_os=35, zone_l=0.25, zone_s=0.25, window=400, step=0.010,
                    levels=3, mult=1.5, tp=0.020, sweep=0.020, max_bars=192),
    "BTCUSDT": dict(rsi_os=25, zone_l=0.25, zone_s=0.25, window=400, step=0.015,
                    levels=3, mult=1.5, tp=0.020, sweep=0.020, max_bars=192),
    "ETHUSDT": dict(rsi_os=25, zone_l=0.25, zone_s=0.25, window=400, step=0.015,
                    levels=3, mult=1.5, tp=0.020, sweep=0.020, max_bars=192),
    "SOLUSDT": dict(rsi_os=30, zone_l=0.25, zone_s=0.25, window=400, step=0.010,
                    levels=3, mult=1.5, tp=0.020, sweep=0.020, max_bars=192),
}
W2 = {
    "DOGEUSDT": dict(rsi_os=35, zone_l=0.28, zone_s=0.28, window=518, step=0.011,
                     levels=3, mult=1.8, tp=0.038, sweep=0.020, max_bars=205),
    "LTCUSDT": dict(rsi_os=34, zone_l=0.25, zone_s=0.25, window=377, step=0.008,
                    levels=3, mult=1.2, tp=0.020, sweep=0.020, max_bars=160),
    "BTCUSDT": W1["BTCUSDT"],
    "ETHUSDT": dict(rsi_os=28, zone_l=0.51, zone_s=0.51, window=199, step=0.030,
                    levels=2, mult=1.6, tp=0.014, sweep=0.023, max_bars=70,
                    cooldown=18, knife=3.3),
    "SOLUSDT": W1["SOLUSDT"],
}
W3 = dict(e4.CURRENT)  # итог после v2/v3

DEFAULTS = dict(rsi_idx=2, cooldown=0, knife=0.0, be_move=0)


def to_genome(cfg):
    g = dict(DEFAULTS)
    g.update(cfg)
    return g


def seg_stats(r):
    st = e2.stats(r)
    return dict(ret=round((r["balance"] / e2.START - 1) * 100, 1),
                med=round(st["med"], 2), p25=round(st["p25"], 2),
                pos=round(st["pos_share"] * 100),
                wr=round(r["wins"] / r["trades"] * 100, 1) if r["trades"] else 0,
                dd=round(r["max_dd"] * 100, 1), trades=r["trades"],
                ruined=r["ruined"])


def eth_short_filter(side, i):
    return side if side == "S" else None


def main():
    final_src = None
    for tag in ("evolution5", "evolution4"):
        path = f"{tag}_winners.json"
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            final_src = (tag, data)
            break

    pct5 = xd.fetch_daily_pct5()
    out = {}
    for sym in e4.SYMBOLS:
        candles = ev.fetch(sym, "15", DAYS)
        n = len(candles)
        pre = e2.prep(candles)
        d0 = time.strftime("%Y-%m-%d", time.localtime(candles[0][0] / 1000))
        d1 = time.strftime("%Y-%m-%d", time.localtime(candles[-1][0] / 1000))

        rows = {}
        # W1..W3 — без внешних фильтров
        for tag, cfgs in (("W1", W1), ("W2", W2), ("W3", W3)):
            g = to_genome(cfgs[sym])
            filt = eth_short_filter if (tag == "W2" and sym == "ETHUSDT") else None
            r = e2.run5(candles, pre, g, entry_filter=filt)
            rows[tag] = seg_stats(r)

        # финал (после v4/v5), с его фильтрами
        if final_src:
            tag, data = final_src
            rec = data[sym]
            g_fin = rec["genome"] if rec["adopt"] else rec["base_genome"]
            genes = e5.GENES5 if tag == "evolution5" else e4.GENES4
            g_fin = {k: (int(round(v)) if k in genes and genes[k][2] else v)
                     for k, v in g_fin.items()}
            funding = xd.fetch_funding(sym, DAYS + 50)
            oi = xd.fetch_oi(sym)
            aux = xd.build_aux4(candles, funding, oi, pct5)
            if tag == "evolution5":
                closes = [c[4] for c in candles]
                aux["closes"] = closes
                aux["ema"] = [e5.calc_ema(closes, x) for x in e5.EMA_SET]
                aux["smaf"] = [e5.calc_sma(closes, x) for x in e5.MAF_SET]
                aux["smas"] = [e5.calc_sma(closes, x) for x in e5.MAS_SET]
                aux["aroon"] = [e5.calc_aroon(candles, x) for x in e5.ARN_SET]
                make_f = e5.make_filter5
            else:
                make_f = e4.make_filter4
            r = e2.run5(candles, pre, g_fin, entry_filter=make_f(g_fin, aux))
            rows["FINAL"] = seg_stats(r)
            rows["FINAL"]["genome"] = {k: (round(v, 4) if isinstance(v, float)
                                           else v) for k, v in g_fin.items()}

            # по годам (финальный конфиг)
            years = []
            bounds = [(0, n - 2 * Y), (n - 2 * Y, n - Y), (n - Y, n)]
            for (a, b) in bounds:
                seg = candles[a:b]
                if len(seg) < 10000:
                    continue
                pre_s = e2.prep(seg)

                def _sl(v):
                    if isinstance(v, tuple):
                        return tuple(_sl(x) for x in v)
                    if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
                        return [_sl(x) for x in v]
                    return v[a:b]
                aux_s = {k: _sl(v) for k, v in aux.items()}
                r_s = e2.run5(seg, pre_s, g_fin, entry_filter=make_f(g_fin, aux_s))
                ds = time.strftime("%Y-%m", time.localtime(seg[0][0] / 1000))
                de = time.strftime("%Y-%m", time.localtime(seg[-1][0] / 1000))
                years.append(dict(period=f"{ds}..{de}", **seg_stats(r_s)))
            rows["FINAL_years"] = years

            n_months = max(1, int(r["months"]))
            rows["FINAL_monthly"] = [
                round(r["monthly"].get(m, 0.0) / e2.START * 100, 2)
                for m in range(n_months)]

        out[sym] = dict(period=f"{d0}..{d1}", waves=rows)
        print(sym, {k: v.get("ret") for k, v in rows.items()
                    if isinstance(v, dict) and "ret" in v})

    with open("report3y.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("OK -> report3y.json")


if __name__ == "__main__":
    main()
