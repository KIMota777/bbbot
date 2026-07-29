# -*- coding: utf-8 -*-
"""Красный флаг: доля циклов, закрытых ГЕНУИННЫМ убытком (pnl<0), для всех
5 монет на 4ч (и для сравнения — на 15m). Низкая доля при большом числе
сделок = стоп фактически никогда не тестировался (см. be_move + далёкий
sweep-стоп) — риск занижен бэктестом, плечи из лестницы доверять нельзя."""

import json
from collections import Counter

import evolution as ev
import evolution2 as e2
import evolution8 as e8
import ext_data as xd

pct5 = xd.fetch_daily_pct5()

for tag, interval, bpd in (("4h", "240", 6), ("15m", "15", 96)):
    with open(f"evolution8_{'4h' if tag=='4h' else '15m'}_final.json", encoding="utf-8") as fh:
        recs = json.load(fh)
    print(f"\n=== {tag} ===")
    for sym, rec in recs.items():
        g = rec["genome"]
        candles = ev.fetch(sym, interval, 1150)
        aux = e8.make_aux_builder(pct5, bpd)(sym, candles)
        pre = e2.prep(candles)
        filt = e8.make_filter8(g, aux)
        old_lev, old_bpd = e2.LEV, e2.BARS_PER_DAY
        e2.LEV = rec["rec_lev"]
        e2.BARS_PER_DAY = bpd
        try:
            events = []
            r = e2.run5(candles, pre, g, entry_filter=filt, events=events)
        finally:
            e2.LEV, e2.BARS_PER_DAY = old_lev, old_bpd
        closes = [e for e in events if e["type"] == "close"]
        reasons = Counter(e.get("reason", "?") for e in closes)
        losses = sum(1 for e in closes if e["pnl"] < 0)
        loss_pct = losses / len(closes) * 100 if closes else 0
        print(f"{sym:10} x{rec['rec_lev']:<3} сделок {len(closes):4} | "
              f"убыточных {losses:4} ({loss_pct:5.1f}%) | причины {dict(reasons)}")
