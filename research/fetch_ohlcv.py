# -*- coding: utf-8 -*-
"""Докачка истории с ОБЪЁМОМ.

Существующие history_*.json хранят только [ts,o,h,l,c] — объёма нет вовсе.
Без него непроверяемы целые классы гипотез (volume confirmation, OBV, VWAP,
relative volume), поэтому исследование начинается с данных, а не со стратегий.

Формат на выходе: [ts_ms, open, high, low, close, volume, turnover].
turnover нужен для честного VWAP: sum(turnover)/sum(volume) — это реальная
средневзвешенная цена, а не (h+l+c)/3, которое лишь её приближает.

Bybit отдаёт максимум 1000 свечей за запрос и идёт назад от `end`.
"""
import json
import os
import sys
import time
import urllib.request

BASE = "https://api.bybit.com/v5/market/kline"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def fetch_chunk(symbol, interval, end_ms, tries=5):
    url = ("%s?category=linear&symbol=%s&interval=%s&limit=1000&end=%d"
           % (BASE, symbol, interval, end_ms))
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=25) as fh:
                d = json.loads(fh.read())
            if d.get("retCode") != 0:
                raise RuntimeError(d.get("retMsg"))
            return d["result"]["list"]
        except Exception as exc:                     # noqa: BLE001
            if attempt == tries - 1:
                raise
            print("   повтор %d: %s" % (attempt + 1, exc))
            time.sleep(2 * (attempt + 1))
    return []


def fetch_all(symbol, interval, start_ms, end_ms):
    """Идём назад от end до start. Bybit отдаёт список свежая->старая."""
    rows, cursor, empty = {}, end_ms, 0
    while cursor > start_ms:
        chunk = fetch_chunk(symbol, interval, cursor)
        if not chunk:
            empty += 1
            if empty >= 3:
                break
            cursor -= 1000 * 60 * int(interval)
            continue
        empty = 0
        for r in chunk:
            ts = int(r[0])
            if ts < start_ms:
                continue
            # v5 отдаёт start свечи; open/high/low/close/volume/turnover
            rows[ts] = [ts, float(r[1]), float(r[2]), float(r[3]),
                        float(r[4]), float(r[5]), float(r[6])]
        oldest = min(int(r[0]) for r in chunk)
        if oldest >= cursor:                         # нет прогресса — выходим
            break
        cursor = oldest - 1
        time.sleep(0.12)
    return [rows[k] for k in sorted(rows)]


def main():
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    # тот же горизонт, что у имеющейся истории, чтобы результаты сравнивались
    ref = json.load(open("d:/bbbot-fix/history_BTCUSDT_15m_1150d.json"))
    start_ms, end_ms = ref[0][0], ref[-1][0]
    print("горизонт: %s .. %s" % (start_ms, end_ms))
    for sym in SYMBOLS:
        for interval in ("15", "60", "240"):
            path = os.path.join(OUT, "ohlcv_%s_%s.json" % (sym, interval))
            if os.path.exists(path):
                have = json.load(open(path))
                print("%-9s %-4s уже есть: %d свечей" % (sym, interval,
                                                         len(have)))
                continue
            t0 = time.time()
            rows = fetch_all(sym, interval, start_ms, end_ms)
            with open(path, "w") as fh:
                json.dump(rows, fh, separators=(",", ":"))
            print("%-9s %-4s %6d свечей  %.0fs  %s .. %s"
                  % (sym, interval, len(rows), time.time() - t0,
                     rows[0][0] if rows else "-", rows[-1][0] if rows else "-"))
            sys.stdout.flush()


if __name__ == "__main__":
    main()
