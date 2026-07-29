# -*- coding: utf-8 -*-
"""Считает полную статистику финальных ботов (config.SYMBOL_PARAMS[sym]
['final']) на честном движке: помесячно (окна по 30 дней от начала истории)
и по годам (последние 3 отрезка). Кладёт в webapp/data/final_<SYM>.json —
сайт читает готовое, не пересчитывает при каждом заходе на страницу.

Мультитаймфреймово: у каждого бота свой config['final']['interval'] (по
умолчанию "15"), Y (свечей в году) и bars_per_day считаются от него, а не
захардкожены под 15m — иначе для 4ч-бота "год" был бы посчитан 35040
свечами (это ~2 года на 4ч, не 1).

Использует инфраструктуру evolution8 (make_filter8 + аудитор SMC) — строгий
надмножество evolution7: для ботов без SMC-генов (ob/fvg/structure_gate=0)
поведение идентично старому e7.make_filter7.

Запуск: python finalize_final_bots.py   (после того как config.py обновлён
победителями очередной волны).
"""

import json
import os
import time

import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

OUT_DIR = os.path.join("webapp", "data")


def month_windows(candles, r, events):
    """[(start_ts, end_ts, ret_usdt, ret_pct, trades, wins), ...] по 30-дн
    окнам от начала истории — реальное время, от таймфрейма не зависит."""
    t0 = candles[0][0]
    n_months = max(1, int(r["months"]))
    trades_per = {}
    for e in events:
        if e["type"] != "close":
            continue
        idx = (e["t"] - t0) // e2.MONTH_MS
        b = trades_per.setdefault(idx, [0, 0])
        b[0] += 1
        if e["pnl"] > 0:
            b[1] += 1
    out = []
    for m in range(n_months):
        start = t0 + m * e2.MONTH_MS
        end = start + e2.MONTH_MS
        pnl = r["monthly"].get(m, 0.0)
        tr, wins = trades_per.get(m, [0, 0])
        out.append(dict(
            start=time.strftime("%Y-%m-%d", time.gmtime(start / 1000)),
            end=time.strftime("%Y-%m-%d", time.gmtime(min(end, candles[-1][0] + 1) / 1000)),
            ret_usdt=round(pnl, 3), ret_pct=round(pnl / e2.START * 100, 2),
            trades=tr, wins=wins))
    return out


def year_segments(candles, aux_full, g, filt_fn, bars_per_day):
    """3 годовых отрезка — честный движок заново на каждом (не сумма
    месяцев, чтобы просадка/WR считались корректно внутри своего окна).
    Y (свечей в году) считается от bars_per_day — верно для любого ТФ."""
    Y = 365 * bars_per_day
    n = len(candles)
    bounds = [(max(0, n - 3 * Y), max(0, n - 2 * Y)),
              (max(0, n - 2 * Y), max(0, n - Y)),
              (max(0, n - Y), n)]

    def _slice(v, a, b):
        if isinstance(v, tuple):
            return tuple(_slice(x, a, b) for x in v)
        if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
            return [_slice(x, a, b) for x in v]
        return v[a:b]

    out = []
    for (a, b) in bounds:
        seg = candles[a:b]
        if len(seg) < min(5000, Y // 2):
            continue
        aux_seg = {k: _slice(v, a, b) for k, v in aux_full.items()}
        pre_seg = e2.prep(seg)
        r = e2.run5(seg, pre_seg, g, entry_filter=filt_fn(g, aux_seg))
        wr = r["wins"] / r["trades"] * 100 if r["trades"] else 0
        out.append(dict(
            period=f"{time.strftime('%Y-%m', time.gmtime(seg[0][0]/1000))}"
                   f"..{time.strftime('%Y-%m', time.gmtime(seg[-1][0]/1000))}",
            ret=round((r["balance"] / e2.START - 1) * 100, 1),
            trades=r["trades"], wr=round(wr, 1),
            dd=round(r["max_dd"] * 100, 1), ruined=r["ruined"]))
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    pct5 = xd.fetch_daily_pct5()
    for sym, modes in config.SYMBOL_PARAMS.items():
        p = modes.get("final")
        if not p:
            continue
        interval = str(p.get("interval", "15"))
        bars_per_day = max(4, 1440 // int(interval))
        days = p.get("days", 1150)
        print(f"{sym}: считаю финальную статистику ({interval}m, "
              f"{bars_per_day} бар/сутки)...")

        g = e7.cfg_to_genome(p, "final")
        for k, v in e8.OFF8.items():
            g.setdefault(k, v)
        candles = ev.fetch(sym, interval, days)
        aux_builder = e8.make_aux_builder(pct5, bars_per_day)
        aux = aux_builder(sym, candles)
        pre = e2.prep(candles)
        filt = e8.make_filter8(g, aux)

        old_lev, old_bpd = e2.LEV, e2.BARS_PER_DAY
        e2.LEV = p.get("lev", 5)
        e2.BARS_PER_DAY = bars_per_day
        try:
            events = []
            r = e2.run5(candles, pre, g, entry_filter=filt, events=events)
            years = year_segments(candles, aux, g, e8.make_filter8, bars_per_day)
        finally:
            e2.LEV, e2.BARS_PER_DAY = old_lev, old_bpd

        st = e2.stats(r)
        months = month_windows(candles, r, events)
        data = dict(
            symbol=sym, lev=p.get("lev", 5), interval=interval,
            period_start=time.strftime("%Y-%m-%d", time.gmtime(candles[0][0] / 1000)),
            period_end=time.strftime("%Y-%m-%d", time.gmtime(candles[-1][0] / 1000)),
            overall=dict(
                ret=round((r["balance"] / e2.START - 1) * 100, 1),
                trades=r["trades"],
                wr=round(r["wins"] / r["trades"] * 100, 1) if r["trades"] else 0,
                dd=round(r["max_dd"] * 100, 1),
                med=round(st["med"], 2), p25=round(st["p25"], 2),
                pos_share=round(st["pos_share"] * 100), ruined=r["ruined"]),
            years=years, months=months,
            generated_at=time.strftime("%Y-%m-%d %H:%M"))
        path = os.path.join(OUT_DIR, f"final_{sym}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        print(f"  -> {path}: {data['overall']['ret']:+.1f}% за весь период, "
              f"{len(months)} мес. окон, {len(years)} годовых отрезков")


if __name__ == "__main__":
    main()
