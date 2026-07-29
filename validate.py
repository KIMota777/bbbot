# -*- coding: utf-8 -*-
"""Проверка устойчивости лучших параметров RSI-сетки:
две половины года DOGE отдельно + BTCUSDT целиком."""

import backtest_rsi_grid as bg
import config

BEST = dict(rsi_os=30, grid_step=0.015, grid_levels=3,
            tp_pct=0.03, sweep=0.02, lev=5)


def test(candles, label):
    closes = [c[4] for c in candles]
    rsi = bg.calc_rsi(closes, bg.RSI_PERIOD)
    rlow, rhigh = bg.rolling_extremes(candles, bg.WINDOW)
    r = bg.run(candles, rsi, rlow, rhigh, **BEST)
    ret = (r["balance"] / bg.START_BALANCE - 1) * 100
    wr = r["wins"] / r["trades"] * 100 if r["trades"] else 0
    hold = (closes[-1] / closes[0] - 1) * 100
    print(f"{label:22} | доход {ret:+7.1f}% | сделок {r['trades']:4} | "
          f"WR {wr:5.1f}% | DD {r['max_dd']*100:5.1f}% | "
          f"слив: {'ДА' if r['ruined'] else 'нет'} | hold {hold:+.1f}%")


doge = bg.fetch_history()
half = len(doge) // 2
test(doge[:half], "DOGE 1-е полугодие")
test(doge[half:], "DOGE 2-е полугодие")

bg.CACHE = "history_btc.json"
config.SYMBOL = "BTCUSDT"
btc = bg.fetch_history()
test(btc, "BTC весь год")
