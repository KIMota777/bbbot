# -*- coding: utf-8 -*-
"""Адаптивный LTC: можно ли уменьшить просадку, отключаясь в плохом режиме.

ИДЕЯ ВЛАДЕЛЬЦА. Бот теряет не равномерно, а кусками: есть месяцы, где механика
работает, и месяцы, где рынок для неё враждебен. Если научиться распознавать
второе и в такие периоды не торговать (или торговать другим режимом), просадка
должна упасть, а кривая стать ровнее.

Идея верная по смыслу. Проверяется здесь на LTC — единственном конфиге, который
держится на обеих половинах истории.

КАК ПРОВЕРЯЕТСЯ ЧЕСТНО. Правило выбирается ТОЛЬКО по обучающей половине, а
результат меряется ТОЛЬКО на холдоуте. Иначе получится то же, чем кончились все
прошлые заходы: правило подобрано по тем данным, на которых показывается.
Рядом печатается число проверенных правил — лучшее из двадцати выглядит хорошо
и на шуме.

ЧТО ЗА РЕЖИМЫ. Все считаются ТОЛЬКО по прошлым барам, иначе гейт знал бы
будущее и «отключался» ровно перед убытком, чего в жизни не бывает:
  adx      — сила тренда. Механика возврата к средней должна страдать в
             сильном тренде: цена не возвращается, а уходит;
  vol      — своя волатильность в перцентиле последних 500 баров. В шторм
             стопы выбивает шумом;
  btc      — направление биткоина: альткоин против рынка ходит плохо;
  dd       — собственная просадка: после серии убытков уменьшать ставку.

Гейт навешивается поверх штатного фильтра конфига, а не заменяет его:
entry_filter(side, i) -> side | None, и мы возвращаем None там, где режим
запрещает вход.

Запуск: python adaptive_ltc.py [--sym LTCUSDT] [--mode normal]
"""
import argparse
import json
import os

import numpy as np

import all_configs_honest as ach
import bots_honest as bh
import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

e2.BARS_PER_DAY = 96
OUT = os.path.join("webapp", "data", "adaptive_ltc.json")


def wilder(x, n):
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    v = float(np.mean(x[:n]))
    out[n - 1] = v
    for i in range(n, len(x)):
        v = v + (x[i] - v) / n
        out[i] = v
    return out


def regimes(candles, btc_close=None):
    """Ряды режима по барам. Значение на баре i известно к его закрытию."""
    a = np.asarray(candles, dtype=np.float64)
    h, l, c = a[:, 2], a[:, 3], a[:, 4]
    n = len(c)
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    up = np.diff(h, prepend=h[0])
    dn = -np.diff(l, prepend=l[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr = wilder(tr, 14)
    pdi = 100.0 * wilder(pdm, 14) / np.where(atr > 0, atr, np.nan)
    ndi = 100.0 * wilder(ndm, 14) / np.where(atr > 0, atr, np.nan)
    dx = 100.0 * np.abs(pdi - ndi) / np.where(pdi + ndi > 0, pdi + ndi, np.nan)
    adx = wilder(np.nan_to_num(dx), 14)

    # волатильность в перцентиле своего прошлого — сравнимо между периодами
    r = np.zeros(n)
    r[1:] = np.log(np.maximum(c[1:], 1e-12) / np.maximum(c[:-1], 1e-12))
    win = 96
    rv = np.full(n, np.nan)
    if n > win:
        cs = np.cumsum(r ** 2)
        rv[win:] = np.sqrt((cs[win:] - cs[:-win]) / win)
    rank = np.full(n, np.nan)
    look = 500
    for i in range(look, n):
        w = rv[i - look:i + 1]
        w = w[np.isfinite(w)]
        if len(w) > 50 and np.isfinite(rv[i]):
            rank[i] = float((w <= rv[i]).mean())

    btc_up = np.full(n, np.nan)
    if btc_close is not None and len(btc_close) == n:
        ma = np.full(n, np.nan)
        k = 480
        if n > k:
            cs = np.concatenate([[0.0], np.cumsum(btc_close)])
            ma[k - 1:] = (cs[k:] - cs[:-k]) / k
        btc_up = np.where(np.isfinite(ma), (btc_close > ma).astype(float),
                          np.nan)
    return dict(adx=adx, volrank=rank, btc_up=btc_up)


def gated(filt, reg, rule):
    """Штатный фильтр плюс запрет по режиму. None = вход запрещён."""
    kind, thr = rule
    if kind == "нет":
        return filt

    def f(side, i):
        s = filt(side, i) if filt else side
        if s is None:
            return None
        if kind == "adx_below":
            v = reg["adx"][i]
            return s if (not np.isfinite(v) or v < thr) else None
        if kind == "adx_above":
            v = reg["adx"][i]
            return s if (not np.isfinite(v) or v > thr) else None
        if kind == "vol_below":
            v = reg["volrank"][i]
            return s if (not np.isfinite(v) or v < thr) else None
        if kind == "vol_above":
            v = reg["volrank"][i]
            return s if (not np.isfinite(v) or v > thr) else None
        if kind == "btc_long_only":
            # Сторона приходит СТРОКОЙ ('L'/'S'), а не числом — движок так её
            # и хранит. Сравнение с числом молча падало бы на любом другом
            # правиле, здесь падает громко, и это лучше.
            v = reg["btc_up"][i]
            if not np.isfinite(v):
                return s
            is_long = (s == "L") if isinstance(s, str) else (s > 0)
            return s if is_long == (v > 0.5) else None
        return s
    return f


RULES = [("нет", 0)]
RULES += [("adx_below", t) for t in (20, 25, 30, 35)]
RULES += [("adx_above", t) for t in (15, 20, 25)]
RULES += [("vol_below", t) for t in (0.5, 0.7, 0.85)]
RULES += [("vol_above", t) for t in (0.15, 0.3, 0.5)]
RULES += [("btc_long_only", 0)]


def measure(evs, base=None):
    base = e2.START if base is None else base
    eq, peak, dd = base, base, 0.0
    n = 0
    for e in evs:
        if e.get("type") != "close" or e.get("pnl") is None:
            continue
        pnl = 0.0 if bh.is_tiny(e["pnl"]) else float(e["pnl"])
        if pnl:
            n += 1
        eq += pnl
        peak = max(peak, eq)
        if peak > 0:
            dd = max(dd, (peak - eq) / peak)
    return dict(ret=100.0 * (eq / base - 1), dd=100.0 * dd, trades=n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym", default="LTCUSDT")
    ap.add_argument("--mode", default="normal")
    ap.add_argument("--lev", type=float, default=5.0)
    a = ap.parse_args()

    pct5 = xd.fetch_daily_pct5()
    p = config.SYMBOL_PARAMS[a.sym][a.mode]
    g = ach.with_defaults(e7.cfg_to_genome(p, a.mode))
    candles = ev.fetch(a.sym, "15", bh.DAYS)
    btc = ev.fetch("BTCUSDT", "15", bh.DAYS)
    btc_c = np.asarray(btc, dtype=np.float64)[:, 4] \
        if len(btc) == len(candles) else None
    aux = e8.make_aux_builder(pct5, 96)(a.sym, candles)
    n = len(candles)
    h = int(n * bh.HOLD_FRAC)

    print("%s/%s, плечо x%g, баров %d, граница холдоута %d"
          % (a.sym, a.mode, a.lev, n, h))
    print("правил к проверке: %d\n" % len(RULES))

    res = {}
    for half, lo, hi in (("train", 0, h), ("hold", h, n)):
        c = candles[lo:hi]
        pre = e2.prep(c)
        base_filt = e8.make_filter8(
            g, dict((k, bh.slice_aux(v, lo, hi)) for k, v in aux.items()))
        reg = regimes(c, btc_c[lo:hi] if btc_c is not None else None)
        for rule in RULES:
            evs = []
            bh.run_at(c, pre, g, gated(base_filt, reg, rule), a.lev,
                      events=evs)
            res.setdefault(rule, {})[half] = measure(evs)

    base = res[("нет", 0)]
    print("%-18s | %-28s | %s" % ("правило", "ОБУЧЕНИЕ (по нему выбираем)",
                                  "ХОЛДОУТ (по нему судим)"))
    print("%-18s | %8s %7s %6s | %8s %7s %6s"
          % ("", "итог", "просад", "сдел", "итог", "просад", "сдел"))
    rows = []
    for rule in RULES:
        t, ho = res[rule]["train"], res[rule]["hold"]
        rows.append((rule, t, ho))
        mark = " <- без гейта" if rule[0] == "нет" else ""
        print("%-18s | %+7.1f%% %6.1f%% %6d | %+7.1f%% %6.1f%% %6d%s"
              % ("%s %s" % rule if rule[0] != "нет" else "нет гейта",
                 t["ret"], t["dd"], t["trades"],
                 ho["ret"], ho["dd"], ho["trades"], mark))

    # выбор ТОЛЬКО по обучению: доход на единицу просадки
    def score(m):
        return m["ret"] / max(m["dd"], 1.0)
    cand = [r for r in rows if r[1]["trades"] >= 20]
    cand.sort(key=lambda r: -score(r[1]))
    best = cand[0]
    print("\nВЫБРАНО ПО ОБУЧЕНИЮ: %s %s (доход/просадка %.2f)"
          % (best[0][0], best[0][1], score(best[1])))
    print("  на обучении: %+.1f%% при просадке %.1f%%"
          % (best[1]["ret"], best[1]["dd"]))
    print("  НА ХОЛДОУТЕ: %+.1f%% при просадке %.1f%%"
          % (best[2]["ret"], best[2]["dd"]))
    print("  без гейта  : %+.1f%% при просадке %.1f%%"
          % (base["hold"]["ret"], base["hold"]["dd"]))
    better_ret = best[2]["ret"] > base["hold"]["ret"]
    better_dd = best[2]["dd"] < base["hold"]["dd"]
    print("\nВЫВОД: на холдоуте гейт %s по доходу и %s по просадке."
          % ("лучше" if better_ret else "ХУЖЕ",
             "лучше" if better_dd else "ХУЖЕ"))
    if not (better_ret or better_dd):
        print("То есть отбор правила по прошлому снова не перенёсся.")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dict(sym=a.sym, mode=a.mode, lev=a.lev,
                       rules=[dict(rule=list(r), train=t, hold=ho)
                              for r, t, ho in rows],
                       chosen=list(best[0])), fh, ensure_ascii=False)
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    main()
