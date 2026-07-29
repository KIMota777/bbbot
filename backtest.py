# -*- coding: utf-8 -*-
"""Бэктест стратегии SMA-кроссовер на исторических данных Bybit.

Скачивает свечи (публичный API, ключи не нужны), симулирует ту же логику,
что и bot.py: кроссовер -> вход по рынку на закрытии свечи, SL/TP,
разворот по встречному сигналу. Учитывает комиссию тейкера с обеих сторон.

Запуск: python backtest.py
"""

import time

from pybit.unified_trading import HTTP

import config

DAYS = 365               # глубина истории
TAKER_FEE = 0.00055      # комиссия тейкера Bybit 0.055%
START_BALANCE = 7.4      # стартовый депозит USDT (твой текущий)
LEVERAGES = [3, 15, 20]  # какие плечи сравнить


def fetch_history(session, days):
    """Скачивает закрытые свечи за `days` дней: [(ts, open, high, low, close), ...]"""
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    out = []
    cursor = end
    while cursor > start:
        r = session.get_kline(
            category="linear", symbol=config.SYMBOL,
            interval=config.INTERVAL, limit=1000, end=cursor,
        )
        rows = r["result"]["list"]  # от новой к старой
        if not rows:
            break
        out = rows[::-1] + out
        oldest = int(rows[-1][0])
        if oldest >= cursor:
            break
        cursor = oldest - 1
        time.sleep(0.15)
    candles = [
        (int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]))
        for x in out if int(x[0]) >= start
    ]
    # выбрасываем последнюю (незакрытую) свечу
    return candles[:-1]


def sma(vals, n, i):
    return sum(vals[i - n + 1: i + 1]) / n


def run_backtest(candles, leverage, margin, balance0):
    closes = [c[4] for c in candles]
    balance = balance0
    peak = balance0
    max_dd = 0.0
    pos = None  # dict: side, entry, qty, sl, tp
    trades = wins = 0
    liq_pct = 1.0 / leverage  # грубая оценка движения цены до ликвидации

    def open_pos(side, price):
        nonlocal balance
        qty = margin * leverage / price
        fee = qty * price * TAKER_FEE
        balance -= fee
        if side == "Buy":
            sl = price * (1 - config.STOP_LOSS_PCT / 100)
            tp = price * (1 + config.TAKE_PROFIT_PCT / 100)
        else:
            sl = price * (1 + config.STOP_LOSS_PCT / 100)
            tp = price * (1 - config.TAKE_PROFIT_PCT / 100)
        return {"side": side, "entry": price, "qty": qty, "sl": sl, "tp": tp}

    def close_pos(p, price):
        nonlocal balance, trades, wins, peak, max_dd
        direction = 1 if p["side"] == "Buy" else -1
        pnl = direction * (price - p["entry"]) * p["qty"]
        fee = p["qty"] * price * TAKER_FEE
        balance += pnl - fee
        trades += 1
        if pnl - fee > 0:
            wins += 1
        peak = max(peak, balance)
        max_dd = max(max_dd, (peak - balance) / peak if peak > 0 else 0)

    slow = config.SMA_SLOW
    for i in range(slow + 1, len(candles)):
        ts, o, h, l, c = candles[i]

        # 1) внутри свечи: ликвидация / SL / TP (худший исход первым)
        if pos:
            e = pos["entry"]
            if pos["side"] == "Buy":
                if l <= e * (1 - liq_pct):          # ликвидация
                    balance -= pos["qty"] * e * liq_pct
                    trades += 1
                    peak = max(peak, balance)
                    max_dd = max(max_dd, (peak - balance) / peak if peak > 0 else 0)
                    pos = None
                elif l <= pos["sl"]:
                    close_pos(pos, pos["sl"]); pos = None
                elif h >= pos["tp"]:
                    close_pos(pos, pos["tp"]); pos = None
            else:
                if h >= e * (1 + liq_pct):
                    balance -= pos["qty"] * e * liq_pct
                    trades += 1
                    peak = max(peak, balance)
                    max_dd = max(max_dd, (peak - balance) / peak if peak > 0 else 0)
                    pos = None
                elif h >= pos["sl"]:
                    close_pos(pos, pos["sl"]); pos = None
                elif l <= pos["tp"]:
                    close_pos(pos, pos["tp"]); pos = None

        if balance < margin:  # депозит кончился
            return dict(balance=balance, trades=trades, wins=wins,
                        max_dd=max_dd, ruined=True,
                        ruined_at=candles[i][0])

        # 2) сигнал на закрытии свечи (как в bot.py)
        f_now, s_now = sma(closes, config.SMA_FAST, i), sma(closes, slow, i)
        f_prev, s_prev = sma(closes, config.SMA_FAST, i - 1), sma(closes, slow, i - 1)
        cross_up = f_prev <= s_prev and f_now > s_now
        cross_down = f_prev >= s_prev and f_now < s_now
        if not cross_up and not cross_down:
            continue
        target = "Buy" if cross_up else "Sell"
        if pos and pos["side"] == target:
            continue
        if pos:
            close_pos(pos, c)
            pos = None
        if balance >= margin:
            pos = open_pos(target, c)

    if pos:
        close_pos(pos, closes[-1])
    return dict(balance=balance, trades=trades, wins=wins,
                max_dd=max_dd, ruined=False, ruined_at=None)


def main():
    session = HTTP(testnet=False)
    print(f"Скачиваю историю {config.SYMBOL} {config.INTERVAL}m за {DAYS} дней...")
    candles = fetch_history(session, DAYS)
    print(f"Свечей: {len(candles)} "
          f"(с {time.strftime('%Y-%m-%d', time.localtime(candles[0][0]/1000))} "
          f"по {time.strftime('%Y-%m-%d', time.localtime(candles[-1][0]/1000))})\n")

    first, last = candles[0][4], candles[-1][4]
    hold_ret = (last / first - 1) * 100
    print(f"Buy & hold DOGE за период: {hold_ret:+.1f}% "
          f"(цена {first:.5f} -> {last:.5f})\n")

    print(f"Параметры: SMA {config.SMA_FAST}/{config.SMA_SLOW}, "
          f"SL {config.STOP_LOSS_PCT}%, TP {config.TAKE_PROFIT_PCT}%, "
          f"маржа {config.MARGIN_USDT} USDT, депозит {START_BALANCE} USDT, "
          f"комиссия {TAKER_FEE*100:.3f}% за сторону\n")

    hdr = f"{'Плечо':>6} | {'Итог USDT':>10} | {'Доход %':>9} | {'Сделок':>6} | {'Winrate':>7} | {'MaxDD':>6} | Слив"
    print(hdr)
    print("-" * len(hdr))
    for lev in LEVERAGES:
        r = run_backtest(candles, lev, config.MARGIN_USDT, START_BALANCE)
        ret = (r["balance"] / START_BALANCE - 1) * 100
        wr = r["wins"] / r["trades"] * 100 if r["trades"] else 0
        ruined = ("ДА, " + time.strftime("%Y-%m-%d", time.localtime(r["ruined_at"] / 1000))
                  if r["ruined"] else "нет")
        print(f"{('x' + str(lev)):>6} | {r['balance']:>10.2f} | {ret:>+8.1f}% | "
              f"{r['trades']:>6} | {wr:>6.1f}% | {r['max_dd']*100:>5.1f}% | {ruined}")


if __name__ == "__main__":
    main()
