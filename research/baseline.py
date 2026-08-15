# -*- coding: utf-8 -*-
"""Опорные линии: с чем сравнивать всё, что найдёт исследование.

Без опорной линии любая цифра бессмысленна. Их три:

1. КУПИЛ И ДЕРЖУ. Самая честная и самая неудобная. Стратегия, которая за три
   года заработала меньше, чем принесло простое владение монетой, не окупает
   ни риска, ни работы, ни комиссий.

2. НЫНЕШНИЕ БОТЫ. Их конфиги отбирались на ВСЕЙ доступной истории, поэтому
   для них чистого внеобучающего куска не существует вовсе — включая те 170
   дней, которые для нового исследования закрыты. Сравнивать их результат с
   внеобучающим результатом новых стратегий НЕЛЬЗЯ: это сравнение экзамена с
   ответами и экзамена без. Числа приводятся, чтобы видеть масштаб, и всегда
   с этой оговоркой.

3. ЦЕЛЬ ЗАДАНИЯ: +4-5%% в месяц при просадке не глубже 20%%. Пересчитано в
   годовые: рост капитала в 1.6-1.8 раза за год.
"""
import datetime as dt
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(DIR)
for p in (DIR, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import metrics   # noqa: E402
import rdata     # noqa: E402

TARGET_MO = 0.045
NAMES = {"train": "обучение", "val": "проверка", "test": "ЭКЗАМЕН (закрыт)",
         "trainval": "обучение+проверка", "all": "вся история"}


def d(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d")


def buy_hold():
    print("\n1. КУПИЛ И ДЕРЖУ (без плеча, комиссия входа и выхода учтена)")
    print("%-6s %-18s %9s %9s %9s %8s"
          % ("монета", "период", "итог", "в месяц", "просадка", "дней"))
    rows = []
    for sym in rdata.SYMBOLS:
        b = rdata.load_bars(sym, "60")
        for split in ("train", "val", "test", "all"):
            t0, t1 = rdata.SPLITS[split]
            m = (b.t >= t0) & (b.t < t1)
            if m.sum() < 10:
                continue
            c = b.c[m]
            fee = 2 * rdata.TAKER_FEE
            ret = (c[-1] / c[0]) * (1 - fee) - 1.0
            days = (int(b.t[m][-1]) - int(b.t[m][0])) / rdata.DAY_MS
            dd = metrics.max_drawdown(c)[0]
            mo = (1 + ret) ** (30.0 / max(days, 1)) - 1.0
            rows.append((sym, split, ret, mo, dd, days))
            print("%-6s %-18s %+8.1f%% %+8.2f%% %8.1f%% %8.0f"
                  % (sym.replace("USDT", ""), NAMES[split], 100 * ret,
                     100 * mo, 100 * dd, days))
    return rows


def live_bots():
    print("\n2. НЫНЕШНИЕ БОТЫ (конфиги отбирались на всей истории — "
          "чистого экзамена у них нет)")
    # боевой движок читает свои json по относительным путям — ему нужен
    # корень проекта как текущая папка, иначе он не найдёт файлы отбора
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        import build_pnl_curves as bpc
        import config
        import ext_data as xd
    except Exception as exc:                       # noqa: BLE001
        os.chdir(cwd)
        print("   не удалось загрузить боевой движок: %s" % exc)
        return []
    try:
        pct5 = xd.fetch_daily_pct5()
    except Exception:                              # noqa: BLE001
        pct5 = {}
    print("%-6s %-18s %9s %9s %9s %7s"
          % ("монета", "период", "итог", "в месяц", "просадка", "сделок"))
    rows = []
    for sym, modes in config.SYMBOL_PARAMS.items():
        p = modes.get("final")
        if not p:
            continue
        try:
            _t0, _pnls, meta = bpc.bot_pnls(sym, p, pct5)
        except Exception as exc:                   # noqa: BLE001
            print("%-6s  прогон не удался: %s" % (sym, exc))
            continue
        evs = [e for e in meta.get("events", []) if e.get("type") == "close"]
        if not evs:
            continue
        # боевой движок помечает события СЕКУНДАМИ, а границы выборки здесь в
        # миллисекундах — без приведения ни одна сделка не попадает в окно
        arr = sorted((int(e["t"]) * 1000, float(e.get("pnl") or 0.0))
                     for e in evs)
        for split in ("train", "val", "test", "all"):
            a, z = rdata.SPLITS[split]
            sel = [(t, x) for t, x in arr if a <= t < z]
            if len(sel) < 2:
                continue
            # база фиксированная, как в отборе: маржа $5 при капитале $20
            eq = 20.0
            curve = [eq]
            for _t, x in sel:
                eq += x
                curve.append(max(eq, 0.0))
            v = np.array(curve)
            ret = v[-1] / v[0] - 1.0
            days = (sel[-1][0] - sel[0][0]) / rdata.DAY_MS
            dd = metrics.max_drawdown(v)[0]
            mo = (1 + ret) ** (30.0 / max(days, 1)) - 1.0 if ret > -1 else -1.0
            rows.append((sym, split, ret, mo, dd, len(sel)))
            print("%-6s %-18s %+8.1f%% %+8.2f%% %8.1f%% %7d"
                  % (sym.replace("USDT", ""), NAMES[split], 100 * ret,
                     100 * mo, 100 * dd, len(sel)))
    os.chdir(cwd)
    return rows


def target():
    print("\n3. ЦЕЛЬ ЗАДАНИЯ, пересчитанная в понятные величины")
    for mo in (0.02, 0.03, 0.04, 0.05):
        yr = (1 + mo) ** 12 - 1
        print("   %+.0f%% в месяц  ->  %+.0f%% за год (капитал x%.2f)"
              % (100 * mo, 100 * yr, 1 + yr))
    print("   при потолке просадки 20%% и плеча x10")


if __name__ == "__main__":
    print("Границы выборки")
    for k in ("train", "val", "test"):
        a, z = rdata.SPLITS[k]
        print("   %-18s %s .. %s  (%d дней)"
              % (NAMES[k], d(a), d(z), (z - a) / rdata.DAY_MS))
    buy_hold()
    live_bots()
    target()
