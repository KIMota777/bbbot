# -*- coding: utf-8 -*-
"""Догрузка ОБЪЁМА 4ч-свечей (в кэше проекта объёма нет: ev.fetch хранит
[ts,o,h,l,c]). Кладём в СВОЙ кэш cap_vol_{symbol}_{interval}.json, чужие
файлы не трогаем.  Формат: {ts_ms(str): base_volume}."""

import json
import os
import time

from pybit.unified_trading import HTTP

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT"]


def vol_path(symbol, interval):
    return f"cap_vol_{symbol}_{interval}.json"


def fetch_vol(symbol, interval="240", days=1160):
    """{ts_ms(str): объём в базовой монете}. Кэш — свой."""
    path = vol_path(symbol, interval)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    s = HTTP(testnet=False)
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    out, cursor = {}, end
    for _ in range(60):
        r = s.get_kline(category="linear", symbol=symbol, interval=interval,
                        limit=1000, end=cursor)
        rows = r["result"]["list"]
        if not rows:
            break
        for x in rows:
            out[str(int(x[0]))] = float(x[5])
        oldest = int(rows[-1][0])
        if oldest >= cursor or oldest <= start:
            break
        cursor = oldest - 1
        time.sleep(0.12)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    return out


if __name__ == "__main__":
    for sym in SYMS:
        v = fetch_vol(sym, "240")
        ts = sorted(int(k) for k in v)
        print(f"{sym:9} 4ч-объём: {len(v):6} баров  "
              f"{time.strftime('%Y-%m-%d', time.gmtime(ts[0] / 1000))}.."
              f"{time.strftime('%Y-%m-%d', time.gmtime(ts[-1] / 1000))}")
