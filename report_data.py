# -*- coding: utf-8 -*-
"""Данные для отчёта: текущие лучшие конфиги через честный движок v2.
Год 15m, помесячная детализация. Вывод в report_data.json."""

import json
import statistics

import evolution as ev
import evolution2 as e2

CONFIGS = {
    "DOGEUSDT": dict(rsi_idx=2, rsi_os=35, zone_l=0.28, zone_s=0.28, window=518,
                     step=0.011, levels=3, mult=1.8, tp=0.038, sweep=0.020,
                     max_bars=205, cooldown=0, knife=0.0, be_move=0),
    "LTCUSDT": dict(rsi_idx=2, rsi_os=34, zone_l=0.25, zone_s=0.25, window=377,
                    step=0.008, levels=3, mult=1.2, tp=0.020, sweep=0.020,
                    max_bars=160, cooldown=0, knife=0.0, be_move=0),
    "BTCUSDT": dict(rsi_idx=2, rsi_os=25, zone_l=0.25, zone_s=0.25, window=400,
                    step=0.015, levels=3, mult=1.5, tp=0.020, sweep=0.020,
                    max_bars=192, cooldown=0, knife=0.0, be_move=0),
    "ETHUSDT": dict(rsi_idx=2, rsi_os=28, zone_l=0.51, zone_s=0.51, window=199,
                    step=0.030, levels=2, mult=1.6, tp=0.014, sweep=0.023,
                    max_bars=70, cooldown=18, knife=3.3, be_move=0),
    "SOLUSDT": dict(rsi_idx=2, rsi_os=30, zone_l=0.25, zone_s=0.25, window=400,
                    step=0.010, levels=3, mult=1.5, tp=0.020, sweep=0.020,
                    max_bars=192, cooldown=0, knife=0.0, be_move=0),
}
ONLY_SHORT = {"ETHUSDT"}


def short_filter(side, i):
    return side if side == "S" else None


out = {}
for sym, g in CONFIGS.items():
    candles = ev.fetch(sym, "15", 365)
    pre = e2.prep(candles)
    filt = short_filter if sym in ONLY_SHORT else None
    r = e2.run5(candles, pre, g, entry_filter=filt)
    st = e2.stats(r)
    n_months = max(1, int(r["months"]))
    monthly = [round(r["monthly"].get(m, 0.0) / e2.START * 100, 2)
               for m in range(n_months)]
    closes = [c[4] for c in candles]
    out[sym] = dict(
        ret_year=round((r["balance"] / e2.START - 1) * 100, 1),
        monthly=monthly,
        med=round(st["med"], 2), p25=round(st["p25"], 2),
        pos_share=round(st["pos_share"] * 100),
        wr=round(r["wins"] / r["trades"] * 100, 1) if r["trades"] else 0,
        dd=round(r["max_dd"] * 100, 1),
        trades=r["trades"], tpm=round(st["tpm"], 1),
        hold_h=round(st["avg_hold"] * 15 / 60, 1),
        hold_last=(closes[-1] / closes[0] - 1) * 100,
        ruined=r["ruined"],
    )
    print(sym, out[sym]["ret_year"], "monthly:", monthly)

with open("report_data.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2)
print("OK -> report_data.json")
