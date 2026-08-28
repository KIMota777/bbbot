# -*- coding: utf-8 -*-
"""Разбор убыточных месяцев: что у них общего и можно ли это знать заранее.

ЗАЧЕМ. Корзина конфигов теряет не ровным слоем, а кусками: большинство месяцев
в плюсе, а несколько уводят итог. Если у убыточных месяцев есть общая черта,
ВИДНАЯ ЗАРАНЕЕ, её можно превратить в правило «в такой месяц не торговать» —
и убрать самую дорогую часть просадки, не трогая остальное.

ГЛАВНОЕ ОГРАНИЧЕНИЕ, из-за которого тут легко себя обмануть. Признак месяца
должен быть известен НА ЕГО НАЧАЛО. Взять волатильность за сам месяц и
обнаружить, что убыточные месяцы были штормовыми, — бесполезно: в первый день
месяца этого не знают. Поэтому все признаки считаются по данным, кончающимся
ДО первого дня месяца.

ВТОРОЕ ОГРАНИЧЕНИЕ, которое надо назвать вслух: месяцев мало. На обучении их
около 28, на холдоуте 12. Любое правило, найденное на 28 наблюдениях, слабое
по построению, и проверка на 12 не может его подтвердить. Поэтому здесь не
ищется лучший порог — проверяется, есть ли вообще СВЯЗЬ, и переносится ли её
знак на холдоут.

Запуск: python bad_months.py
"""
import datetime as dt
import json
import os

import numpy as np

import adaptive_ltc as al
import all_configs_honest as ach
import bots_honest as bh
import combo_gate as cg
import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

e2.BARS_PER_DAY = 96
OUT = os.path.join("webapp", "data", "bad_months.json")
LEV = 10.0


def month_key(ts_ms):
    d = dt.datetime.fromtimestamp(ts_ms / 1000.0, dt.UTC)
    return (d.year, d.month)


def month_start_ms(key):
    return int(dt.datetime(key[0], key[1], 1, tzinfo=dt.UTC).timestamp() * 1000)


def basket_months(streams, half, gate=0.0):
    """Доходность корзины по календарным месяцам, равные доли."""
    names = sorted(streams)
    w = 1.0 / len(names)
    per = {}
    for n in names:
        for t, pnl, vr in streams[n][half]:
            if gate > 0 and vr < gate:
                continue
            per.setdefault(month_key(t), 0.0)
            per[month_key(t)] += 100.0 * pnl * w / e2.START
    return dict(sorted(per.items()))


def market_features(btc, keys):
    """Признаки рынка НА НАЧАЛО каждого месяца. Только прошлые бары.

    Срез делается строго до первого дня месяца: индекс ищется по метке
    времени, и последний включённый бар закрылся ДО начала месяца.
    """
    a = np.asarray(btc, dtype=np.float64)
    ts, close = a[:, 0], a[:, 4]
    out = {}
    for k in keys:
        cut = month_start_ms(k)
        i = int(np.searchsorted(ts, cut, "left"))
        if i < 3000:                      # мало истории до этого месяца
            continue
        c = close[:i]
        # доходность биткоина за прошлый месяц и квартал
        m1 = c[-1] / c[-2880] - 1 if len(c) > 2880 else np.nan     # 30 дней
        m3 = c[-1] / c[-8640] - 1 if len(c) > 8640 else np.nan     # 90 дней
        # реализованная волатильность за прошлый месяц
        r = np.diff(np.log(np.maximum(c[-2880:], 1e-12)))
        rv = float(np.std(r)) * np.sqrt(96 * 365) if len(r) > 100 else np.nan
        # положение относительно длинной средней
        ma = float(np.mean(c[-8640:])) if len(c) > 8640 else np.nan
        pos = c[-1] / ma - 1 if ma == ma and ma > 0 else np.nan
        out[k] = dict(btc_m1=float(m1), btc_m3=float(m3), vol=float(rv),
                      vs_ma=float(pos))
    return out


def corr(x, y):
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 6:
        return float("nan")
    if x[m].std() == 0 or y[m].std() == 0:
        return float("nan")
    return float(np.corrcoef(x[m], y[m])[0][1])


def main():
    pct5 = xd.fetch_daily_pct5()
    btc_all = ev.fetch("BTCUSDT", "15", bh.DAYS)

    print("считаю прогоны...")
    streams = {}
    for sym, modes in config.SYMBOL_PARAMS.items():
        cand = ev.fetch(sym, "15", bh.DAYS)
        btc_c = np.asarray(btc_all, dtype=np.float64)[:, 4] \
            if len(btc_all) == len(cand) else None
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
            got, ok = {}, True
            for half, lo, hi in (("train", 0, h), ("hold", h, n)):
                c = cand[lo:hi]
                filt = e8.make_filter8(
                    g, dict((k, bh.slice_aux(v, lo, hi))
                            for k, v in aux.items()))
                reg = al.regimes(c, btc_c[lo:hi] if btc_c is not None else None)
                tr = cg.trades_with_regime(c, e2.prep(c), g, filt, reg, LEV)
                if len(tr) < 8:
                    ok = False
                    break
                got[half] = tr
            if ok:
                streams["%s/%s" % (sym.replace("USDT", ""), mode)] = got
    print("конфигов: %d\n" % len(streams))

    res = {}
    for half in ("train", "hold"):
        mo = basket_months(streams, half)
        feats = market_features(btc_all, list(mo))
        keys = [k for k in mo if k in feats]
        rets = [mo[k] for k in keys]
        res[half] = dict(months=[(list(k), mo[k]) for k in keys],
                         feats={str(k): feats[k] for k in keys})

        bad = [k for k in keys if mo[k] < 0]
        good = [k for k in keys if mo[k] >= 0]
        print("=" * 74)
        print("%s: месяцев %d, убыточных %d (%.0f%%)"
              % ("ОБУЧЕНИЕ" if half == "train" else "ХОЛДОУТ",
                 len(keys), len(bad), 100 * len(bad) / max(len(keys), 1)))
        print("=" * 74)
        if bad:
            print("убыточные: %s"
                  % ", ".join("%04d-%02d (%+.1f%%)" % (k[0], k[1], mo[k])
                              for k in bad))
        print()
        print("%-24s %10s %10s %8s" % ("признак на начало месяца", "в плюсовые",
                                       "в убыточные", "связь"))
        for fname, label in (("btc_m1", "BTC за прошлый месяц"),
                             ("btc_m3", "BTC за прошлый квартал"),
                             ("vol", "волатильность BTC"),
                             ("vs_ma", "BTC против средней")):
            gv = [feats[k][fname] for k in good if np.isfinite(feats[k][fname])]
            bv = [feats[k][fname] for k in bad if np.isfinite(feats[k][fname])]
            c = corr([feats[k][fname] for k in keys], rets)
            if not gv or not bv:
                continue
            print("%-24s %+9.1f%% %+9.1f%% %+8.2f"
                  % (label, 100 * np.mean(gv), 100 * np.mean(bv), c))
        print()

    # переносится ли знак связи с обучения на холдоут
    print("=" * 74)
    print("ПЕРЕНОСИТСЯ ЛИ СВЯЗЬ")
    print("=" * 74)
    print("%-24s %10s %10s  %s" % ("признак", "обучение", "холдоут", "вывод"))
    for fname, label in (("btc_m1", "BTC за прошлый месяц"),
                         ("btc_m3", "BTC за прошлый квартал"),
                         ("vol", "волатильность BTC"),
                         ("vs_ma", "BTC против средней")):
        cs = {}
        for half in ("train", "hold"):
            keys = [tuple(k) for k, _v in res[half]["months"]]
            rets = [v for _k, v in res[half]["months"]]
            fv = [res[half]["feats"][str(tuple(k))][fname] for k in keys]
            cs[half] = corr(fv, rets)
        same = (np.isfinite(cs["train"]) and np.isfinite(cs["hold"])
                and np.sign(cs["train"]) == np.sign(cs["hold"]))
        print("%-24s %+9.2f %+9.2f  %s"
              % (label, cs["train"], cs["hold"],
                 "знак совпал" if same else "знак НЕ совпал"))

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False)
    print("\nМесяцев мало: около 28 на обучении и 12 на холдоуте. Связь,")
    print("найденная на таком числе, слабая по построению — совпадение знака")
    print("на холдоуте это подсказка, а не подтверждение.")
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    main()
