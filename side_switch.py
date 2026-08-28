# -*- coding: utf-8 -*-
"""Лонг и шорт как две стратегии: есть ли переход, который видно заранее.

ИДЕЯ ВЛАДЕЛЬЦА. Бот умеет и лонг, и шорт, но рынок не бывает нейтральным: в
росте зарабатывает одна сторона, в падении другая. Если научиться распознавать
переход, можно держать включённой ту сторону, для которой сейчас время.

Идея верная по смыслу, и прежняя проверка её косвенно подтвердила: на бычьем
обучении лонг давал Шарп 1.31, а шорт -0.84; на медвежьей проверке ровно
наоборот. То есть стороны действительно разные, и вопрос только один — можно
ли узнать текущий режим ЗАРАНЕЕ, а не задним числом.

ЧТО ЗДЕСЬ ДЕЛАЕТСЯ
  1. Каждая сделка помечается стороной и признаком рынка НА МОМЕНТ ВХОДА.
  2. Считается доходность отдельно лонгов и отдельно шортов, на обеих
     половинах истории.
  3. Проверяются правила переключения — все причинные, то есть считаемые по
     прошлым барам: цена BTC относительно длинной средней, направление по
     разности DI, знак доходности BTC за прошлые сутки и неделю.
  4. Правило выбирается ТОЛЬКО по обучающей половине, мерится ТОЛЬКО на
     холдоуте. Иначе получится подгонка ровно того числа, ради которого всё
     затевалось.

ГЛАВНАЯ ЛОВУШКА, которую тут легко не заметить. Если правило переключения
совпадает с направлением рынка, а обучающая половина была бычьей, то «лонг в
росте» выучит просто «всегда лонг» — и на медвежьем холдоуте развалится. Чтобы
это увидеть, рядом печатается доля времени, которую правило держит каждую
сторону: если она близка к 100/0, правило не переключает, а выбирает.

Запуск: python side_switch.py
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
OUT = os.path.join("webapp", "data", "side_switch.json")
LEV = 10.0


def marked(candles, pre, g, filt, reg, lev, btc_c):
    """Сделки со стороной и признаками рынка на момент ВХОДА."""
    evs = []
    bh.run_at(candles, pre, g, filt, lev, events=evs)
    idx = {int(c[0]): i for i, c in enumerate(candles)}
    n = len(candles)
    # признаки рынка по прошлым барам
    ma_long = np.full(n, np.nan)
    if btc_c is not None and len(btc_c) == n:
        k = 2880                                  # 30 суток на 15м
        if n > k:
            cs = np.concatenate([[0.0], np.cumsum(btc_c)])
            ma_long[k - 1:] = (cs[k:] - cs[:-k]) / k
        d1 = np.full(n, np.nan)
        d7 = np.full(n, np.nan)
        d1[96:] = btc_c[96:] / btc_c[:-96] - 1
        d7[672:] = btc_c[672:] / btc_c[:-672] - 1
    else:
        d1 = d7 = np.full(n, np.nan)

    out, oi, side = [], None, None
    for e in evs:
        if e["type"] == "entry":
            oi = idx.get(int(e["t"]))
            side = e.get("side")
        elif e["type"] == "close" and e.get("pnl") is not None:
            pnl = 0.0 if bh.is_tiny(e["pnl"]) else float(e["pnl"])
            if oi is not None and oi < n:
                above = (btc_c[oi] > ma_long[oi]) if (
                    btc_c is not None and len(btc_c) == n
                    and np.isfinite(ma_long[oi])) else np.nan
                out.append(dict(t=int(e["t"]), pnl=pnl, side=side,
                                vr=float(reg["volrank"][oi])
                                if np.isfinite(reg["volrank"][oi]) else 0.5,
                                above=float(above) if above == above else np.nan,
                                d1=float(d1[oi]) if np.isfinite(d1[oi]) else np.nan,
                                d7=float(d7[oi]) if np.isfinite(d7[oi]) else np.nan))
            oi, side = None, None
    return out


RULES = {
    "обе стороны": lambda tr: True,
    "только лонг": lambda tr: tr["side"] == "L",
    "только шорт": lambda tr: tr["side"] == "S",
    "по средней 30д": lambda tr: (
        tr["side"] == "L" if tr["above"] == 1 else tr["side"] == "S")
    if tr["above"] == tr["above"] else True,
    "по средней, наоборот": lambda tr: (
        tr["side"] == "S" if tr["above"] == 1 else tr["side"] == "L")
    if tr["above"] == tr["above"] else True,
    "по неделе BTC": lambda tr: (
        tr["side"] == "L" if tr["d7"] > 0 else tr["side"] == "S")
    if tr["d7"] == tr["d7"] else True,
    "по суткам BTC": lambda tr: (
        tr["side"] == "L" if tr["d1"] > 0 else tr["side"] == "S")
    if tr["d1"] == tr["d1"] else True,
}


def curve(streams, half, rule):
    ev_list = []
    names = sorted(streams)
    w = 1.0 / len(names)
    n_l = n_s = 0
    for n in names:
        for tr in streams[n][half]:
            if not rule(tr):
                continue
            if tr["side"] == "L":
                n_l += 1
            else:
                n_s += 1
            ev_list.append((tr["t"], tr["pnl"] * w / e2.START))
    if len(ev_list) < 10:
        return None
    ev_list.sort()
    eq = peak = 1.0
    dd = 0.0
    per = {}
    for t, r in ev_list:
        eq *= (1 + r)
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
        d = dt.datetime.fromtimestamp(
            (t / 1000.0 if t > 1e11 else t), dt.UTC)
        per[(d.year, d.month)] = per.get((d.year, d.month), 0.0) + r
    mv = np.array([100.0 * x for _k, x in sorted(per.items())])
    tot = n_l + n_s
    return dict(ret=100.0 * (eq - 1), dd=100.0 * dd, trades=tot,
                med=float(np.median(mv)) if len(mv) else 0.0,
                pos=float((mv > 0).mean()) if len(mv) else 0.0,
                long_share=n_l / tot if tot else 0.0)


def main():
    pct5 = xd.fetch_daily_pct5()
    btc_all = ev.fetch("BTCUSDT", "15", bh.DAYS)
    print("считаю прогоны со стороной сделки...")
    streams = {}
    for sym, modes in config.SYMBOL_PARAMS.items():
        cand = ev.fetch(sym, "15", bh.DAYS)
        btc_c = np.asarray(btc_all, dtype=np.float64)[:, 4] \
            if len(btc_all) == len(cand) else None
        aux = e8.make_aux_builder(pct5, 96)(sym, cand)
        n = len(cand)
        h = int(n * bh.HOLD_FRAC)
        for mode, p in modes.items():
            if not isinstance(p, dict) or mode.endswith("_g"):
                continue
            try:
                g = ach.with_defaults(e7.cfg_to_genome(p, mode))
            except Exception:                      # noqa: BLE001
                continue
            got, ok = {}, True
            for half, lo, hi in (("train", 0, h), ("hold", h, n)):
                c = cand[lo:hi]
                filt = e8.make_filter8(
                    g, dict((k, bh.slice_aux(v, lo, hi))
                            for k, v in aux.items()))
                reg = al.regimes(c, btc_c[lo:hi] if btc_c is not None else None)
                tr = marked(c, e2.prep(c), g, filt, reg, LEV,
                            btc_c[lo:hi] if btc_c is not None else None)
                if len(tr) < 8:
                    ok = False
                    break
                got[half] = tr
            if ok:
                streams["%s/%s" % (sym.replace("USDT", ""), mode)] = got
    print("конфигов: %d\n" % len(streams))

    print("%-22s | %-25s | %s" % ("правило", "ОБУЧЕНИЕ", "ХОЛДОУТ"))
    print("%-22s | %8s %6s %6s | %8s %6s %6s %6s"
          % ("", "итог", "просад", "лонгов", "итог", "просад", "лонгов",
             "месяц"))
    rows = {}
    for name, rule in RULES.items():
        t = curve(streams, "train", rule)
        h = curve(streams, "hold", rule)
        if not t or not h:
            continue
        rows[name] = dict(train=t, hold=h)
        print("%-22s | %+7.1f%% %5.1f%% %5.0f%% | %+7.1f%% %5.1f%% %5.0f%% %+6.2f%%"
              % (name, t["ret"], t["dd"], 100 * t["long_share"],
                 h["ret"], h["dd"], 100 * h["long_share"], h["med"]))

    both = rows.get("обе стороны")
    print("\nВЫБОР ПО ОБУЧЕНИЮ, ПРОВЕРКА НА ХОЛДОУТЕ")
    cand_rules = {k: v for k, v in rows.items() if k != "обе стороны"}
    if cand_rules and both:
        best = max(cand_rules, key=lambda k: rows[k]["train"]["ret"]
                   / max(rows[k]["train"]["dd"], 1.0))
        b = rows[best]
        print("  лучшее по обучению: %s" % best)
        print("    на обучении  %+.1f%% при просадке %.1f%%"
              % (b["train"]["ret"], b["train"]["dd"]))
        print("    НА ХОЛДОУТЕ  %+.1f%% при просадке %.1f%%"
              % (b["hold"]["ret"], b["hold"]["dd"]))
        print("    обе стороны  %+.1f%% при просадке %.1f%%"
              % (both["hold"]["ret"], both["hold"]["dd"]))
        better = b["hold"]["ret"] > both["hold"]["ret"]
        print("  вывод: переключение %s обеих сторон на холдоуте"
              % ("ЛУЧШЕ" if better else "хуже"))
        ls = b["hold"]["long_share"]
        if ls > 0.9 or ls < 0.1:
            print("  ВНИМАНИЕ: правило держит одну сторону %.0f%% времени —"
                  % (100 * max(ls, 1 - ls)))
            print("  это не переключение, а выбор стороны, то есть ставка на режим")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False)
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    main()
