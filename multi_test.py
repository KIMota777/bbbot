# -*- coding: utf-8 -*-
"""Прогон стратегии RSI-сетка на нескольких монетах.

Для каждой монеты:
  1) текущие параметры из config.py (чемпион с DOGE) + проверка на полугодиях;
  2) мини-перебор (54 комбинации) — есть ли вообще рабочие параметры
     под эту монету (лучшая по худшему полугодию).
"""

import itertools

import backtest_rsi_grid as bg
import config
import experiment as ex

SYMBOLS = ["DOGEUSDT", "LTCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]

CURRENT = dict(os=config.RSI_OS, step=config.GRID_STEP, levels=config.GRID_LEVELS,
               tp=config.TP_PCT, sweep=config.SWEEP_BUF, zone=config.ZONE,
               window=config.RANGE_WINDOW, mult=config.GRID_MULT,
               partial=False, trend=None)

SWEEP = list(itertools.product(
    [25, 30, 35], [0.01, 0.015, 0.025], [0.02, 0.03, 0.04], [0.02, 0.03]))


def ret(r):
    return (r["balance"] / bg.START_BALANCE - 1) * 100


def test_symbol(sym):
    config.SYMBOL = sym
    bg.CACHE = f"history_{sym}.json"
    candles = bg.fetch_history()
    closes = [c[4] for c in candles]
    hold = (closes[-1] / closes[0] - 1) * 100
    rsi, ext, ema = ex.prep(candles)
    half = len(candles) // 2
    c1, c2 = candles[:half], candles[half:]
    pr1, pr2 = ex.prep(c1), ex.prep(c2)

    def full_and_halves(p):
        rf = ex.run2(candles, rsi, ext, ema, p)
        r1 = ex.run2(c1, *pr1, p)
        r2 = ex.run2(c2, *pr2, p)
        return rf, ret(r1), ret(r2)

    rf, h1, h2 = full_and_halves(CURRENT)
    wr = rf["wins"] / rf["trades"] * 100 if rf["trades"] else 0
    print(f"\n=== {sym}  (buy&hold {hold:+.1f}%, свечей {len(candles)}) ===")
    print(f"Текущий конфиг: год {ret(rf):+7.1f}% | WR {wr:4.1f}% | "
          f"DD {rf['max_dd']*100:4.1f}% | слив {'ДА' if rf['ruined'] else 'нет'} | "
          f"полугодия {h1:+.1f}% / {h2:+.1f}%")

    best = None
    for os_, step, tp, sw in SWEEP:
        p = dict(CURRENT, os=os_, step=step, tp=tp, sweep=sw)
        rf2, a, b = full_and_halves(p)
        worst = min(a, b)
        if best is None or worst > best[1]:
            best = (p, worst, rf2, a, b)
    p, worst, rf2, a, b = best
    wr2 = rf2["wins"] / rf2["trades"] * 100 if rf2["trades"] else 0
    print(f"Лучший подбор:  год {ret(rf2):+7.1f}% | WR {wr2:4.1f}% | "
          f"DD {rf2['max_dd']*100:4.1f}% | полугодия {a:+.1f}% / {b:+.1f}%")
    print(f"   параметры: RSI {p['os']}, шаг {p['step']*100:.1f}%, "
          f"TP {p['tp']*100:.1f}%, буфер {p['sweep']*100:.1f}%")


for s in SYMBOLS:
    test_symbol(s)
