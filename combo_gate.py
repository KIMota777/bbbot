# -*- coding: utf-8 -*-
"""Соединяем то, что подтвердилось: корзину и гейт по режиму. Плюс плавный размер.

ЧТО ПОДТВЕРДИЛОСЬ ПО ОТДЕЛЬНОСТИ:
  корзина из многих конфигов резко снижает разброс месяцев (82% плюсовых на
    обучении против качелей +-17% у одиночных), но не добавляет дохода;
  гейт по волатильности надёжно снижает просадку (12 улучшений из 15,
    знаковый критерий p=0.018), но не добавляет дохода.
Оба работают с РИСКОМ. Логично сложить их и посмотреть, складывается ли эффект.

ТРЕТЬЯ ПРОВЕРКА — ПЛАВНЫЙ РАЗМЕР вместо «включить-выключить». У жёсткого гейта
есть очевидный изъян: он выбрасывает 54% сделок, и часть выигрыша по просадке —
просто от того, что бот меньше в рынке. Плавная версия не выбрасывает ничего, а
масштабирует ставку по режиму: тихо — меньше, штормит — больше. Тогда выигрыш
по риску должен остаться, а потери дохода от простоя не будет.

ПРИБЛИЖЕНИЕ, которое здесь сделано и о котором надо помнить: результат сделки
масштабируется линейно вместе с размером позиции. Это верно, пока позиция не
доходит до ликвидации; на множителях 0.4-1.6 и при плече x5 такое поведение
ожидаемо, но точную проверку даст только прогон движка с переменной ставкой.

Ничего не подбирается: порог гейта 0.5 взят из прошлого опыта, множители
плавной версии заданы заранее. Корзина — ВСЕ конфиги без отбора.

Запуск: python combo_gate.py
"""
import datetime as dt
import json
import os

import numpy as np

import adaptive_ltc as al
import all_configs_honest as ach
import bots_honest as bh
import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

e2.BARS_PER_DAY = 96
OUT = os.path.join("webapp", "data", "combo_gate.json")


def trades_with_regime(candles, pre, g, filt, reg, lev=5.0):
    """Сделки с меткой режима НА МОМЕНТ ВХОДА.

    Именно входа, а не выхода: решение о размере принимается, когда позиция
    открывается. Взять режим на выходе значило бы дать боту знание о том, чем
    сделка кончится.
    """
    evs = []
    bh.run_at(candles, pre, g, filt, lev, events=evs)
    t_index = {int(c[0]): i for i, c in enumerate(candles)}
    out, open_i = [], None
    for e in evs:
        if e["type"] == "entry":
            open_i = t_index.get(int(e["t"]))
        elif e["type"] == "close" and e.get("pnl") is not None:
            pnl = 0.0 if bh.is_tiny(e["pnl"]) else float(e["pnl"])
            v = reg["volrank"][open_i] if open_i is not None and \
                open_i < len(reg["volrank"]) else np.nan
            out.append((int(e["t"]), pnl, float(v) if np.isfinite(v) else 0.5))
            open_i = None
    return out


VARIANTS = {
    "база": lambda v: 1.0,
    "жёсткий гейт": lambda v: 1.0 if v >= 0.5 else 0.0,
    "плавно 0.4-1.6": lambda v: 0.4 + 1.2 * v,
    "плавно 0.6-1.4": lambda v: 0.6 + 0.8 * v,
}


def monthly(trades, mult, base=None):
    base = e2.START if base is None else base
    per = {}
    for ts, pnl, v in trades:
        ts_s = ts / 1000.0 if ts > 1e11 else ts
        d = dt.datetime.fromtimestamp(ts_s, dt.UTC)
        k = (d.year, d.month)
        per[k] = per.get(k, 0.0) + pnl * mult(v)
    return {k: 100.0 * x / base for k, x in sorted(per.items())}


def basket(series_list):
    """Корзина: в каждом месяце среднее по тем конфигам, что в нём торговали."""
    allm = set()
    for s in series_list:
        allm |= set(s)
    out = {}
    for k in sorted(allm):
        vals = [s[k] for s in series_list if k in s]
        if len(vals) >= 5:
            out[k] = float(np.mean(vals))
    return out


def stats_of(m):
    v = np.array(list(m.values())) if m else np.array([])
    if not len(v):
        return dict(n=0, med=0.0, std=0.0, pos=0.0, dd=0.0, total=0.0)
    eq, peak, dd = 100.0, 100.0, 0.0
    for x in v:
        eq *= (1 + x / 100.0)
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    return dict(n=len(v), med=float(np.median(v)), std=float(v.std(ddof=1)),
                pos=float((v > 0).mean()), dd=100.0 * dd,
                total=eq - 100.0)


def main():
    pct5 = xd.fetch_daily_pct5()
    btc = ev.fetch("BTCUSDT", "15", bh.DAYS)
    per_variant = {k: {"train": [], "hold": []} for k in VARIANTS}
    used = 0

    for sym, modes in config.SYMBOL_PARAMS.items():
        cand = ev.fetch(sym, "15", bh.DAYS)
        btc_c = np.asarray(btc, dtype=np.float64)[:, 4] \
            if len(btc) == len(cand) else None
        aux = e8.make_aux_builder(pct5, 96)(sym, cand)
        n = len(cand)
        h = int(n * bh.HOLD_FRAC)
        for mode, p in modes.items():
            if not isinstance(p, dict):
                continue
            try:
                g = ach.with_defaults(e7.cfg_to_genome(p, mode))
            except Exception:                      # noqa: BLE001
                continue
            ok = True
            got = {}
            for half, lo, hi in (("train", 0, h), ("hold", h, n)):
                c = cand[lo:hi]
                filt = e8.make_filter8(
                    g, dict((k, bh.slice_aux(v, lo, hi))
                            for k, v in aux.items()))
                reg = al.regimes(c, btc_c[lo:hi] if btc_c is not None else None)
                tr = trades_with_regime(c, e2.prep(c), g, filt, reg)
                if len(tr) < 8:
                    ok = False
                    break
                got[half] = tr
            if not ok:
                continue
            used += 1
            for vname, mult in VARIANTS.items():
                for half in ("train", "hold"):
                    per_variant[vname][half].append(monthly(got[half], mult))

    print("конфигов в корзине: %d (без отбора — все, у кого хватает сделок)\n"
          % used)
    print("%-16s | %-30s | %s" % ("вариант", "ОБУЧЕНИЕ", "ХОЛДОУТ"))
    print("%-16s | %7s %6s %6s %6s | %7s %6s %6s %6s"
          % ("", "медиана", "разбр", "плюс%", "просад",
             "медиана", "разбр", "плюс%", "просад"))
    rows = {}
    for vname in VARIANTS:
        t = stats_of(basket(per_variant[vname]["train"]))
        ho = stats_of(basket(per_variant[vname]["hold"]))
        rows[vname] = dict(train=t, hold=ho)
        print("%-16s | %+6.2f%% %6.2f %5.0f%% %5.1f%% | %+6.2f%% %6.2f %5.0f%% %5.1f%%"
              % (vname, t["med"], t["std"], 100 * t["pos"], t["dd"],
                 ho["med"], ho["std"], 100 * ho["pos"], ho["dd"]))

    b = rows["база"]["hold"]
    print("\nСРАВНЕНИЕ С КОРЗИНОЙ БЕЗ НАКЛАДКИ (холдоут):")
    for vname in VARIANTS:
        if vname == "база":
            continue
        h = rows[vname]["hold"]
        dmed = h["med"] - b["med"]
        ddd = h["dd"] - b["dd"]
        print("  %-16s медиана %+.2f п.п., просадка %+.1f п.п., "
              "доход/просадка %.2f -> %.2f"
              % (vname, dmed, ddd,
                 b["med"] / max(b["dd"], 0.5), h["med"] / max(h["dd"], 0.5)))

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dict(used=used, rows=rows), fh, ensure_ascii=False)
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    main()
