# -*- coding: utf-8 -*-
"""Пересчёт годовой/помесячной статистики BTC и ETH с конфигами v2."""
import evolution as ev
import evolution2 as e2

NEW = {
    "BTCUSDT": dict(rsi_idx=2, rsi_os=23, zone_l=0.40, zone_s=0.18, window=877,
                    step=0.004, levels=2, mult=2.0, tp=0.021, sweep=0.020,
                    max_bars=243, cooldown=30, knife=3.5, be_move=0),
    "ETHUSDT": dict(rsi_idx=2, rsi_os=25, zone_l=0.50, zone_s=0.41, window=724,
                    step=0.007, levels=3, mult=1.6, tp=0.010, sweep=0.025,
                    max_bars=139, cooldown=48, knife=3.5, be_move=0),
}
for sym, g in NEW.items():
    candles = ev.fetch(sym, "15", 730)[-35040:]
    r = e2.run5(candles, e2.prep(candles), g)
    st = e2.stats(r)
    n_months = max(1, int(r["months"]))
    monthly = [round(r["monthly"].get(m, 0.0) / e2.START * 100, 2)
               for m in range(n_months)]
    print(sym, "год:", round((r["balance"] / e2.START - 1) * 100, 1))
    print("  monthly:", monthly)
    print("  med", round(st["med"], 2), "p25", round(st["p25"], 2),
          "pos%", round(st["pos_share"] * 100), "wr",
          round(r["wins"] / r["trades"] * 100, 1) if r["trades"] else 0,
          "dd", round(r["max_dd"] * 100, 1), "trades", r["trades"],
          "tpm", round(st["tpm"], 1), "hold_h", round(st["avg_hold"] * 15 / 60, 1))
