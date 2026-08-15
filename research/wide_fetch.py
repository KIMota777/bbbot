# -*- coding: utf-8 -*-
"""Широкая выборка: дневные свечи по всем бессрочным контрактам Bybit.

ЗАЧЕМ. Главная поломка прежнего исследования — не метод, а нехватка
наблюдений: три года по пяти сильно связанным монетам это фактически один
отрезок истории. Триста монет за те же три года дают несравнимо больше
независимых наблюдений, и вопрос «есть ли эффект» становится разрешимым.

ЧЕСТНОЕ ПРЕДУПРЕЖДЕНИЕ О СМЕЩЕНИИ ВЫЖИВШИХ. Bybit отдаёт только те контракты,
которые торгуются СЕЙЧАС. Делистнутых в списке нет, а делистят проигравших.
Значит любая выборка отсюда состоит из тех, кто дожил, и результат по ней
завышен. Насколько — измерить нельзя, данных о мёртвых нет вовсе. Это
записано здесь, а не в примечании мелким шрифтом, потому что забыть об этом
означает получить красивый и неверный ответ.

Куда смещает конкретно:
  * длинная сторона завышена — покупаем среди тех, кто выжил;
  * короткая сторона ЗАНИЖЕНА — самые прибыльные шорты (монеты, ушедшие в
    ноль и снятые с торгов) в выборке отсутствуют.
Для стратегии «покупаем сильные, продаём слабые» знак суммарного смещения не
очевиден, но по опыту литературы он положительный.

Результат: research/data_wide/daily_<SYMBOL>.npy — [ts,o,h,l,c,v,turnover]
плюс research/data_wide/universe.json со сводкой.
"""
import json
import os
import sys
import time
import urllib.request

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(DIR, "data_wide")
BASE = "https://api.bybit.com/v5/market"
HIST_END_MS = 1786716000000          # тот же конец, что у основной истории
MIN_DAYS = 400                       # меньше — статистики всё равно нет
MIN_TURNOVER = 2_000_000             # медианный дневной оборот, $


def api(path, **kw):
    q = "&".join("%s=%s" % (k, v) for k, v in kw.items())
    url = "%s/%s?%s" % (BASE, path, q)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=30) as fh:
                d = json.loads(fh.read())
            if d.get("retCode") != 0:
                raise RuntimeError(d.get("retMsg"))
            return d["result"]
        except Exception as exc:                    # noqa: BLE001
            if attempt == 4:
                raise
            time.sleep(1.5 * (attempt + 1))
    return {}


def universe():
    r = api("instruments-info", category="linear", limit=1000)
    out = []
    for x in r.get("list", []):
        if x.get("quoteCoin") != "USDT" or x.get("status") != "Trading":
            continue
        out.append(dict(symbol=x["symbol"], launch=int(x["launchTime"])))
    return sorted(out, key=lambda x: x["launch"])


def daily(symbol, end_ms=HIST_END_MS, max_bars=2000):
    """Дневные свечи назад от end_ms. Одного запроса хватает на 1000 дней."""
    rows = {}
    cursor = end_ms
    while len(rows) < max_bars:
        r = api("kline", category="linear", symbol=symbol, interval="D",
                limit=1000, end=cursor)
        lst = r.get("list", [])
        if not lst:
            break
        for x in lst:
            rows[int(x[0])] = [int(x[0]), float(x[1]), float(x[2]),
                               float(x[3]), float(x[4]), float(x[5]),
                               float(x[6])]
        oldest = min(int(x[0]) for x in lst)
        if oldest >= cursor:
            break
        cursor = oldest - 1
        if len(lst) < 1000:
            break
        time.sleep(0.08)
    return [rows[k] for k in sorted(rows)]


def main():
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    uni = universe()
    print("контрактов торгуется: %d" % len(uni))
    meta, kept, skipped = [], 0, 0
    t0 = time.time()
    for i, u in enumerate(uni, 1):
        sym = u["symbol"]
        path = os.path.join(OUT, "daily_%s.npy" % sym)
        if os.path.exists(path):
            arr = np.load(path)
        else:
            try:
                rows = daily(sym)
            except Exception as exc:                # noqa: BLE001
                print("  %-14s ошибка: %s" % (sym, exc))
                continue
            arr = np.array(rows, dtype=np.float64) if rows else np.zeros((0, 7))
            np.save(path, arr)
            time.sleep(0.05)
        if len(arr) < MIN_DAYS:
            skipped += 1
            continue
        med_turn = float(np.median(arr[:, 6])) if len(arr) else 0.0
        if med_turn < MIN_TURNOVER:
            skipped += 1
            continue
        kept += 1
        meta.append(dict(symbol=sym, days=int(len(arr)),
                         first=int(arr[0][0]), last=int(arr[-1][0]),
                         median_turnover=med_turn,
                         launch=u["launch"]))
        if i % 50 == 0:
            print("  %d/%d  оставлено %d  %.0fs"
                  % (i, len(uni), kept, time.time() - t0))
            sys.stdout.flush()

    meta.sort(key=lambda m: -m["median_turnover"])
    with open(os.path.join(OUT, "universe.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(kept=meta, min_days=MIN_DAYS,
                       min_turnover=MIN_TURNOVER,
                       note="только торгующиеся сейчас — смещение выживших"),
                  fh, ensure_ascii=False)
    print("\nотобрано %d монет (>= %d дней, оборот >= $%s/день), пропущено %d"
          % (kept, MIN_DAYS, f"{MIN_TURNOVER:,}", skipped))
    if meta:
        d = np.array([m["days"] for m in meta])
        print("история: медиана %d дней, максимум %d" % (np.median(d), d.max()))
        print("оборот: медиана $%.1f млн/день"
              % (np.median([m["median_turnover"] for m in meta]) / 1e6))


if __name__ == "__main__":
    main()
