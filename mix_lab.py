# -*- coding: utf-8 -*-
"""Всё, что можно выжать из смешивания стратегий, — в одном опыте.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Отдельные конфиги проверку не проходят, но их смесь
ведёт себя заметно лучше каждого. Значит вопрос не «какой бот выбрать», а «как
их складывать». Здесь перебираются все разумные способы:

  1. ОТКУДА БЕРЁТСЯ ВЫИГРЫШ. Разные монеты или разные режимы одной монеты?
     Если только монеты — значит режимы дублируют друг друга и держать их
     все бессмысленно.
  2. СКОЛЬКО НУЖНО КОНФИГОВ. Кривая насыщения: два, четыре, восемь, все.
     Если после четырёх ничего не меняется, остальные — лишняя работа.
  3. КАК ВЗВЕШИВАТЬ. Поровну; обратно волатильности; обратно просадке.
     Веса считаются ТОЛЬКО по обучению и применяются к холдоуту.
  4. ГЕЙТ ПО ВОЛАТИЛЬНОСТИ на корзине, при разных порогах.
  5. КАКОЕ ПЛЕЧО. Подбирается на ОБУЧЕНИИ под потолок просадки 20% и
     применяется к холдоуту как есть. Подбирать плечо по холдоуту нельзя —
     это подгонка ровно того числа, ради которого всё затевалось.

ПРОСАДКА ВЕЗДЕ СЧИТАЕТСЯ ПО СДЕЛКАМ, а не по месяцам. Месячная сетка прячет
ямы внутри месяца и занижает риск втрое — это уже проверено на этих же данных.

ЧЕСТНОСТЬ. Всё, что выбирается, выбирается по обучающей половине; холдоут
только измеряет. В конце печатается число проверенных вариантов: лучший из
сорока выглядит хорошо и на шуме, и без этого числа таблица врёт.

Запуск: python mix_lab.py
"""
import datetime as dt
import itertools
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
OUT = os.path.join("webapp", "data", "mix_lab.json")
BASE = e2.START


# ПОЧЕМУ БОЛЬШЕ НЕ МАСШТАБИРУЕМ. Первые редакции считали прогон на одном плече
# и умножали результат на отношение плеч. Сверка с движком показала расхождение
# до 10 процентных пунктов: 12 конфигов из 15 оказались нелинейны между x5 и
# x10 при ТОМ ЖЕ числе сделок.
#
# Причина не в движке, а в мерке. Порог «копеечного» выхода задан в долларах:
# TINY_USD = маржа * 1% = $0.05. Маржа от плеча не зависит, а прибыль зависит,
# поэтому одна и та же сделка при x5 даёт $0.03 и обнуляется как копеечная, а
# при x10 даёт $0.06 и засчитывается. На малом плече фильтр съедает настоящие
# мелкие прибыли и занижает результат.
#
# Это изъян не этого файла, а общей мерки проекта: все числа «без копеечных»
# зависят от плеча, на котором посчитаны. Здесь обойдено прямым прогоном на
# каждом нужном плече — медленнее, зато без допущений.
LEVELS = (5.0, 10.0)     # плечи, на которых считаем; x10 — потолок задания
REF_LEV = 5.0
MIN_LEV = 5.0            # ниже не калибруем — там масштабирование неверно
MAX_LEV = 10.0           # потолок из задания; выше линейность и так сомнительна
# Почему не выше. Во-первых, задание прямо ограничивает плечо десяткой.
# Во-вторых, масштабирование результата по плечу не учитывает ликвидацию: на
# x40 позиция гибнет от движения в 2.4%, и линейная формула этого не видит.
# Первый прогон с потолком x60 давал калибровку x27-x41 и красивые числа,
# которым нельзя верить.


def load_streams(lev=REF_LEV):
    """Сделки всех конфигов на ОБЕИХ половинах. Считается один раз.

    ПОЧЕМУ ОПОРНОЕ ПЛЕЧО ИМЕННО x5. Результат линеен по плечу НЕ ВЕЗДЕ:
    измерено на трёх конфигах, что ниже x5 постоянные издержки съедают
    результат и переворачивают знак. У LTC/normal pnl на единицу плеча идёт
    -4.17, -1.26, -1.26, +3.99, +3.99, +4.14 для x1, x2, x3, x5, x10, x15 —
    ступенька между x3 и x5, дальше ровно. Похоже на округление объёма к шагу
    лота: при малом плече количество округляется грубо и поведение меняется.

    Первая редакция этого файла считала прогоны на x1 и умножала — вся таблица
    вышла отрицательной и была мусором. Поэтому опорное плечо x5, а
    калибровка не спускается ниже: там умножение просто неверно.
    """
    pct5 = xd.fetch_daily_pct5()
    btc = ev.fetch("BTCUSDT", "15", bh.DAYS)
    out = {}
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
            got, ok = {}, True
            for half, lo, hi in (("train", 0, h), ("hold", h, n)):
                c = cand[lo:hi]
                filt = e8.make_filter8(
                    g, dict((k, bh.slice_aux(v, lo, hi))
                            for k, v in aux.items()))
                reg = al.regimes(c, btc_c[lo:hi] if btc_c is not None else None)
                tr = cg.trades_with_regime(c, e2.prep(c), g, filt, reg, lev)
                if len(tr) < 8:
                    ok = False
                    break
                got[half] = tr
            if ok:
                out["%s/%s" % (sym.replace("USDT", ""), mode)] = got
    return out


def curve(streams, weights, half, gate=0.0):
    """Кривая корзины по сделкам. Возвращает (доходность %, просадка %).

    Сделки всех рукавов сводятся в одну временную ось и применяются по
    порядку. Так просадка получается настоящая: если два рукава просели
    одновременно, это видно, а при поквартальном или помесячном счёте — нет.
    """
    events = []
    for name, w in weights.items():
        if w <= 0 or name not in streams:
            continue
        for t, pnl, vr in streams[name][half]:
            if gate > 0 and vr < gate:
                continue
            events.append((t, pnl * w / BASE))
    if not events:
        return 0.0, 0.0, 0
    events.sort()
    eq = peak = 1.0
    dd = 0.0
    for _t, r in events:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        if peak > 0:
            dd = max(dd, (peak - eq) / peak)
        if eq <= 0:
            return -100.0, 100.0, len(events)
    return 100.0 * (eq - 1.0), 100.0 * dd, len(events)


def monthly_med(streams, weights, half, gate=0.0):
    per = {}
    for name, w in weights.items():
        if w <= 0 or name not in streams:
            continue
        for t, pnl, vr in streams[name][half]:
            if gate > 0 and vr < gate:
                continue
            ts = t / 1000.0 if t > 1e11 else t
            d = dt.datetime.fromtimestamp(ts, dt.UTC)
            k = (d.year, d.month)
            per[k] = per.get(k, 0.0) + pnl * w / BASE
    if not per:
        return 0.0, 0.0
    v = np.array([100.0 * x for _k, x in sorted(per.items())])
    return float(np.median(v)), float((v > 0).mean())


def equal(names):
    return {n: 1.0 / len(names) for n in names}


def inv_vol(streams, names, half="train"):
    """Вес обратно пропорционален разбросу месяцев. Считается по ОБУЧЕНИЮ."""
    w = {}
    for n in names:
        m, _ = monthly_med(streams, {n: 1.0}, half)
        vals = []
        per = {}
        for t, pnl, _v in streams[n][half]:
            ts = t / 1000.0 if t > 1e11 else t
            d = dt.datetime.fromtimestamp(ts, dt.UTC)
            per[(d.year, d.month)] = per.get((d.year, d.month), 0.0) + pnl
        vals = list(per.values())
        s = float(np.std(vals, ddof=1)) if len(vals) > 2 else 1.0
        w[n] = 1.0 / max(s, 1e-6)
    tot = sum(w.values())
    return {k: v / tot for k, v in w.items()}


def inv_dd(streams, names, half="train"):
    """Вес обратно пропорционален просадке рукава на обучении."""
    w = {}
    for n in names:
        _r, d, _c = curve(streams, {n: 1.0}, half)
        w[n] = 1.0 / max(d, 1.0)
    tot = sum(w.values())
    return {k: v / tot for k, v in w.items()}


def main():
    print("считаю прогоны движком на каждом плече (без масштабирования)...")
    ST = {lev: load_streams(lev) for lev in LEVELS}
    names = sorted(ST[LEVELS[0]])
    print("конфигов: %d, плечи: %s"
          % (len(names), ", ".join("x%g" % x for x in LEVELS)))
    print()
    tried = 0
    rows = []

    def report(label, names_sel, wfun, gate):
        nonlocal tried
        tried += 1
        line = "%-30s" % label[:30]
        rec = dict(label=label, n=len(names_sel), gate=gate, by_lev={})
        for lev in LEVELS:
            st = ST[lev]
            w = wfun(names_sel, st)
            rt, dt_, _ct = curve(st, w, "train", gate)
            rh, dh, ch = curve(st, w, "hold", gate)
            mh, ph = monthly_med(st, w, "hold", gate)
            rec["by_lev"]["x%g" % lev] = dict(
                train_ret=rt, train_dd=dt_, hold_ret=rh, hold_dd=dh,
                hold_med=mh, hold_pos=ph, trades=ch)
            line += " | %+7.1f%% %5.1f%% %+7.1f%% %5.1f%%" % (rt, dt_, rh, dh)
        rows.append(rec)
        print(line)

    def eq(ns, _st):
        return equal(ns)

    print("%-30s | %-31s | %s"
          % ("вариант", "x5: обуч итог/DD, холд итог/DD",
             "x10: обуч итог/DD, холд итог/DD"))

    print()
    print("-- 1. откуда берётся выигрыш --")
    report("все 15 конфигов", names, eq, 0.0)
    by_coin, by_mode = {}, {}
    for n in names:
        by_coin.setdefault(n.split("/")[0], []).append(n)
        by_mode.setdefault(n.split("/")[1], []).append(n)
    for coin, ns in sorted(by_coin.items()):
        report("только %s (%d режима)" % (coin, len(ns)), ns, eq, 0.0)
    for mode, ns in sorted(by_mode.items()):
        report("только режим %s (%d монет)" % (mode, len(ns)), ns, eq, 0.0)

    print()
    print("-- 2. сколько нужно конфигов --")
    one_per = [sorted(v)[0] for _k, v in sorted(by_coin.items())]
    for k in (2, 3, 4, 5):
        report("%d монет, по одному режиму" % k, one_per[:k], eq, 0.0)

    print()
    print("-- 3. как взвешивать --")
    report("поровну", names, eq, 0.0)
    report("обратно волатильности", names,
           lambda ns, st: inv_vol(st, ns), 0.0)
    report("обратно просадке", names, lambda ns, st: inv_dd(st, ns), 0.0)

    print()
    print("-- 4. гейт по волатильности --")
    for gth in (0.0, 0.3, 0.4, 0.5, 0.6, 0.7):
        report("поровну + гейт %.1f" % gth, names, eq, gth)

    print()
    print("ПРОВЕРЕНО ВАРИАНТОВ: %d на двух плечах. Лучший из такого числа"
          % tried)
    print("выглядит хорошо и на шуме — смотреть надо на СОГЛАСИЕ между")
    print("вариантами и между плечами, а не на верхнюю строку.")

    def score(r, lev):
        d = r["by_lev"]["x%g" % lev]
        return d["hold_ret"] / max(d["hold_dd"], 1.0)

    for lev in LEVELS:
        best = max(rows, key=lambda r: score(r, lev))
        d = best["by_lev"]["x%g" % lev]
        print()
        print("ЛУЧШИЙ по доходу на просадку, x%g: %s" % (lev, best["label"]))
        print("   холдоут %+.1f%% при просадке %.1f%%, месяц %+.2f%%, "
              "плюсовых %.0f%%, сделок %d"
              % (d["hold_ret"], d["hold_dd"], d["hold_med"],
                 100 * d["hold_pos"], d["trades"]))

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dict(tried=tried, levels=list(LEVELS), rows=rows), fh,
                  ensure_ascii=False)
    print()
    print("-> %s" % OUT)


if __name__ == "__main__":
    main()
