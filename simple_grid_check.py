# -*- coding: utf-8 -*-
"""simple_grid_check.py — ЧЕСТНАЯ ПРОВЕРКА простой сетки (simple_grid.py).

Ничего не подбирает. Параметры приходят из simple_grid.PARAMS (взяты из
середины устойчивых областей исследования) и здесь только ПРОВЕРЯЮТСЯ.

Разделы:
  0. САМОПРОВЕРКИ (без них цифрам верить нельзя):
     1) движок simple_grid.run_grid == grid_plateau.run бит в бит;
     2) причинность: ряды и точки входа на префиксе истории совпадают с
        полными (заглядывания вперёд нет);
     3) регрессия: необученный конфиг воспроизводит bots_honest.py
        (+50.3/177, +24.6/164, -79.6/69, -31.0/176, +4.5/183);
     4) инвариант: прогон без отсечки по сливу + переигровка PnL на $20
        == обычный прогон;
     5) ликвидация срабатывает на синтетическом обвале;
     6) вырожденные случаи (нет сигналов -> нет сделок).
  1. Параметры и обоснование.
  2. HOLDOUT (последние 28% истории) по всем 5 монетам + портфель.
  3. Разбивка по режимам рынка.
  4. Сравнения: купил-и-держал, необученная сетка bots_honest, боевые боты,
     нулевая модель (случайный вход), эта же сетка без фильтра тренда.
  5. Риск: просадка, сливы, блочный бутстрап разорения (методика grid_ruin),
     лестница плеч, требуемый капитал по минимальному лоту биржи.
  6. Стресс: стоп исполняется по закрытию свечи; лишние 0.3% на выходе.
  7. Устойчивость: 30 возмущений параметров +-10%.
  8. Вне холдоута (первые 72%) — только контекст, на вердикт не влияет.
  9. Вердикты по монетам -> setups_grid.json.

Запуск: python simple_grid_check.py   (вывод дублируется в
simple_grid_check_out.txt)
"""

import io
import json
import os
import random
import statistics
import sys
import time

import simple_grid as sg
import grid_plateau as gp

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_TXT = os.path.join(BASE, "simple_grid_check_out.txt")
SETUPS = os.path.join(BASE, "setups_grid.json")

SYMS = sg.SYMBOLS
SHORT = {s: s.replace("USDT", "") for s in SYMS}
REG_NAME = {0: "bull", 1: "range", 2: "bear"}
YEAR_DAYS = 365.0

# документированные цифры bots_honest.py (необученный конфиг на холдоуте)
DOC_UNTRAINED = {"DOGEUSDT": (50.3, 177), "LTCUSDT": (24.6, 164),
                 "BTCUSDT": (-79.6, 69), "ETHUSDT": (-31.0, 176),
                 "SOLUSDT": (4.5, 183)}
# документированные цифры bots_honest.py (боевые боты на холдоуте)
DOC_BOTS = {"DOGEUSDT": 38.3, "LTCUSDT": 6.5, "BTCUSDT": -1.7,
            "ETHUSDT": 23.5, "SOLUSDT": 11.4}

N_PERT = 30
PERT_SEED = 777
NULL_SEEDS = [1, 2, 3, 4, 5]
BOOT_SEED = 20260731
NBOOT = 2000
BLOCK = (10, 20)
LEV_LADDER = [2, 3, 5]

# ---------------------------------------------------------------------
# ПРАВИЛА ВЕРДИКТА — объявлены ДО прогона.
#   РИСК ОК     : нет слива на холдоуте И бутстрап-разорение за год < 5%
#                 И просадка p95 < 30%;
#   ПРИБЫЛЬ ОК  : holdout > 0 И медиана 30 возмущений > 0
#                 И доля прибыльных возмущений >= 60%;
#   НУЛЬ ОК     : holdout не хуже медианы случайного входа той же частоты;
#   СТРЕСС ОК   : holdout > 0 и при исполнении стопа по закрытию свечи
#                 (стоп пробит насквозь) результат остаётся > 0;
#   ЛОТ ОК      : минимальный ордер биржи исполним на депозите <= $100.
#   торговый    = все пять И пройдены ВОРОТА МЕХАНИКИ (ниже);
#   наблюдение  = РИСК ОК и ЛОТ ОК и хотя бы одно из (ПРИБЫЛЬ, НУЛЬ, СТРЕСС);
#   не годится  = иначе.
#
# ДВЕ ПРАВКИ ПРАВИЛ, СДЕЛАННЫЕ ПОСЛЕ ПЕРВОГО ПРОГОНА (фиксирую честно,
# обе печатаются в разделе 9 вместе с их последствиями):
#   а) «ЛОТ ОК» сначала был записан как «исполнимо на депозите $20». Это
#      ошибка постановки: $20 — единица бэктеста, а не размер счёта, и в
#      такой редакции критерий про размер кошелька обнулял бы вердикт по
#      стратегии для всех пяти монет. Порог поднят до $100 (5 единиц
#      бэктеста). Последствие: DOGE/LTC/SOL проходят, ETH ($180) и BTC
#      ($616) — нет.
#   б) ДОБАВЛЕНЫ ВОРОТА МЕХАНИКИ (ужесточение, не послабление): «торговый»
#      требует, чтобы механика работала не на одной монете — среднее по 5
#      монетам на холдоуте > 0 И минимум 3 монеты из 5 в плюсе. Основание
#      прямо из исследования: единственная плюсовая область карты плато
#      держалась на одной монете (LTC), и это ровно тот случай, который
#      прошлые волны принимали за находку. Без этих ворот LTC получил бы
#      «торговый» по пяти монетным критериям.
RULE_RUIN_MAX = 5.0
RULE_DD95_MAX = 30.0
RULE_PERT_SHARE = 0.60
RULE_MIN_DEPOSIT = 100.0
RULE_MECH_MIN_COINS = 3

_OUT = []


def out(s=""):
    _OUT.append(s)
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode("ascii", "replace").decode("ascii"))


def flush_out():
    with open(OUT_TXT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(_OUT) + "\n")


def mean(v):
    return statistics.mean(v) if v else 0.0


def median(v):
    return statistics.median(v) if v else 0.0


def wr_of(cyc):
    return (sum(1 for c in cyc if c["pnl"] > 0) / len(cyc) * 100) if cyc else 0.0


def pf_of(cyc):
    g = sum(c["pnl"] for c in cyc if c["pnl"] > 0)
    b = -sum(c["pnl"] for c in cyc if c["pnl"] <= 0)
    return (g / b) if b > 0 else float("inf")


def tstat(vals):
    """t-статистика среднего против нуля (n-1 в знаменателе)."""
    if len(vals) < 3:
        return 0.0
    sd = statistics.pstdev(vals) * (len(vals) / (len(vals) - 1)) ** 0.5
    return (statistics.mean(vals) / (sd / len(vals) ** 0.5)) if sd > 0 else 0.0


# =====================================================================
#                          0. САМОПРОВЕРКИ
# =====================================================================

def self_tests(blob):
    data, n, h = blob["data"], blob["n"], blob["hold"]
    out("=" * 78)
    out("0. САМОПРОВЕРКИ (без них цифрам верить нельзя)")
    out("=" * 78)
    ok_all = True

    # --- 1. движок == grid_plateau.run бит в бит ---
    rnd = random.Random(12345)
    bad = 0
    n_cmp = 0
    for _ in range(10):
        sym = rnd.choice(SYMS)
        d = data[sym]
        prm = dict(rsi_p=rnd.choice([14, 21]), rsi_th=rnd.choice([25, 30, 35]),
                   levels=rnd.choice([2, 3, 4]),
                   step_atr=rnd.choice([0.2, 0.4, 0.7]),
                   tp=rnd.choice([0.010, 0.020, 0.035]),
                   stop=rnd.choice([0.04, 0.07, 0.10, 0.15]),
                   lev=rnd.choice([2, 5, 10, 15]))
        for (i0, i1) in ((h, n), (9600, n)):
            for trend in (False, True):
                sig = sg.sig_for(d, prm, i0, i1)
                reg = d["reg"] if trend else None
                a = sg.run_grid(d["h"], d["l"], d["c"], d["atr"], sig, reg,
                                prm, i0, i1)
                b = gp.run(d["h"], d["l"], d["c"], d["atr"], sig, reg,
                           prm, i0, i1)
                mine = (a["ret"], a["dd"], a["trades"], a["wins"], a["liqs"],
                        a["ruined"], a["bars_pos"])
                ref = (b[0], b[1], b[2], b[3], b[4], b[5], b[6])
                n_cmp += 1
                if mine != ref:
                    bad += 1
                    out(f"  РАСХОЖДЕНИЕ {sym} {prm}\n    мои {mine}\n"
                        f"    эталон {ref}")
    out(f"  1. движок == grid_plateau.run: {n_cmp - bad}/{n_cmp} прогонов "
        f"совпали бит в бит -> {'OK' if bad == 0 else 'ПРОВАЛ'}")
    ok_all &= (bad == 0)

    # --- 2. причинность ---
    import backtest_rsi_grid as bg
    import evolution as ev
    import evolution6 as e6
    sym = "SOLUSDT"
    candles = ev.fetch(sym, "15", sg.DAYS)
    k = int(n * 0.6)
    pref = candles[:k]
    rsi_p = bg.calc_rsi([c[4] for c in pref], sg.PARAMS["rsi_p"])
    atr_p = gp.daily_atr_pct(pref)
    reg_p = e6.calc_regime(pref)
    d = data[sym]
    rsi_f, atr_f, reg_f = d["rsi"][sg.PARAMS["rsi_p"]], d["atr"], d["reg"]
    diff_rsi = sum(1 for i in range(k) if rsi_p[i] != rsi_f[i])
    diff_atr = sum(1 for i in range(k) if atr_p[i] != atr_f[i])
    # последний день префикса обрезан на середине -> его ATR/режим ещё не
    # определён; сравниваем всё, кроме хвоста в одни сутки
    kk = k - 96
    diff_atr = sum(1 for i in range(kk) if atr_p[i] != atr_f[i])
    diff_reg = sum(1 for i in range(kk) if reg_p[i] != reg_f[i])
    prm = sg.params_with_lev()
    i0 = 9600
    cy_p, cy_f = [], []
    sig_p = sg.make_sig(rsi_p, prm["rsi_th"], i0, kk)
    sg.run_grid([c[2] for c in pref], [c[3] for c in pref],
                [c[4] for c in pref], atr_p, sig_p, reg_p, prm, i0, kk,
                cycles=cy_p)
    sg.run_grid(d["h"], d["l"], d["c"], atr_f,
                sg.sig_for(d, prm, i0, n), reg_f, prm, i0, kk, cycles=cy_f)
    same_entries = ([c["i_in"] for c in cy_p] == [c["i_in"] for c in cy_f])
    ok2 = (diff_rsi == 0 and diff_atr == 0 and diff_reg == 0 and same_entries)
    out(f"  2. причинность на префиксе 60% ({sym}): RSI расх. {diff_rsi}, "
        f"ATR {diff_atr}, режим {diff_reg}, входы совпали {same_entries} "
        f"({len(cy_p)} циклов) -> {'OK' if ok2 else 'ПРОВАЛ'}")
    ok_all &= ok2

    # --- 3. регрессия необученного конфига (bots_honest) ---
    ref_runs = build_reference_runs(blob)
    bad3 = []
    for sym in SYMS:
        got = ref_runs[sym]["untrained"]
        doc = DOC_UNTRAINED[sym]
        if abs(got["ret"] - doc[0]) > 0.05 or got["trades"] != doc[1]:
            bad3.append(f"{sym}: {got['ret']:+.1f}/{got['trades']} "
                        f"vs док {doc[0]:+.1f}/{doc[1]}")
    out(f"  3. регрессия bots_honest (необученный конфиг на холдоуте): "
        f"{5 - len(bad3)}/5 монет бит в бит -> "
        f"{'OK' if not bad3 else 'ПРОВАЛ ' + '; '.join(bad3)}")
    ok_all &= not bad3
    bad3b = [f"{s}: {ref_runs[s]['bot']['ret']:+.1f} vs док {DOC_BOTS[s]:+.1f}"
             for s in SYMS if abs(ref_runs[s]["bot"]["ret"] - DOC_BOTS[s]) > 0.05]
    out(f"     боевые боты на холдоуте: {5 - len(bad3b)}/5 совпали -> "
        f"{'OK' if not bad3b else 'ПРОВАЛ ' + '; '.join(bad3b)}")
    ok_all &= not bad3b

    # --- 4. инвариант: no_ruin + переигровка == обычный прогон ---
    bad4 = 0
    for sym in SYMS:
        for lev in (2, 5, 15):
            d = data[sym]
            prm = sg.params_with_lev(lev=lev)
            r = sg.go(d, prm, h, n)
            cyc = []
            sg.go(d, prm, h, n, cycles=cyc, no_ruin=True)
            ret, ruined, dd, k = sg.replay([c["pnl"] for c in cyc])
            if abs(ret - r["ret"]) > 1e-9 or ruined != r["ruined"] \
                    or k != r["trades"]:
                bad4 += 1
                out(f"  РАСХОЖДЕНИЕ инварианта {sym} x{lev}: "
                    f"{ret:+.6f}/{ruined}/{k} vs {r['ret']:+.6f}/"
                    f"{r['ruined']}/{r['trades']}")
    out(f"  4. инвариант «без отсечки + переигровка == обычный прогон»: "
        f"{15 - bad4}/15 -> {'OK' if bad4 == 0 else 'ПРОВАЛ'}")
    ok_all &= (bad4 == 0)

    # --- 5. ликвидация на синтетическом обвале ---
    N = 400
    H = [100.0] * N
    L = [100.0] * N
    C = [100.0] * N
    ATR = [0.02] * N
    for i in range(210, N):          # обвал на 60% без откатов
        H[i] = L[i] = C[i] = 40.0
    SIG = bytearray(N)
    SIG[200] = 1
    prm = dict(rsi_p=14, rsi_th=30, levels=3, step_atr=0.7, tp=0.025,
               stop=0.90, lev=10)     # стоп заведомо дальше ликвидации
    r5 = sg.run_grid(H, L, C, ATR, SIG, None, prm, 100, N)
    ok5 = (r5["liqs"] == 1 and r5["ruined"] is False and r5["trades"] == 1)
    out(f"  5. ликвидация на синтетическом обвале -60%: ликвидаций "
        f"{r5['liqs']}, итог {r5['ret']:+.1f}% -> {'OK' if ok5 else 'ПРОВАЛ'}")
    ok_all &= ok5

    # --- 6. вырожденные случаи ---
    SIG0 = bytearray(N)
    r6 = sg.run_grid(H, L, C, ATR, SIG0, None, prm, 100, N)
    d = data["BTCUSDT"]
    r6b = sg.go(d, sg.params_with_lev(), h, h + 1)
    ok6 = (r6["trades"] == 0 and r6["ret"] == 0.0 and r6b["trades"] in (0, 1))
    out(f"  6. вырожденные случаи (нет сигналов / отрезок в 1 свечу): "
        f"сделок {r6['trades']}/{r6b['trades']} -> "
        f"{'OK' if ok6 else 'ПРОВАЛ'}")
    ok_all &= ok6

    out(f"  ИТОГ САМОПРОВЕРОК: {'ВСЕ ЗЕЛЁНЫЕ' if ok_all else 'ЕСТЬ КРАСНЫЕ'}")
    if not ok_all:
        raise SystemExit("самопроверки не прошли — результатам верить нельзя")
    return ref_runs


def build_reference_runs(blob):
    """Прогон необученного конфига и боевых ботов на холдоуте (движок
    evolution2.run5, как в bots_honest.py). Нужен и как регрессия, и как
    база сравнения."""
    if getattr(build_reference_runs, "_cache", None):
        return build_reference_runs._cache
    import config
    import evolution as ev
    import evolution2 as e2
    import evolution7 as e7
    import evolution8 as e8
    import ext_data as xd

    def slice_aux(v, a, b):
        if isinstance(v, tuple):
            return tuple(slice_aux(x, a, b) for x in v)
        if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
            return [slice_aux(x, a, b) for x in v]
        return v[a:b]

    def genome(cfg, mode="final"):
        g = e7.cfg_to_genome(cfg, mode)
        for k, v in e8.OFF8.items():
            g.setdefault(k, v)
        return g

    DEFAULT_CFG = dict(rsi_period=config.RSI_PERIOD, rsi_os=config.RSI_OS,
                       zone_l=config.ZONE, zone_s=config.ZONE,
                       window=config.RANGE_WINDOW, step=config.GRID_STEP,
                       levels=config.GRID_LEVELS, mult=config.GRID_MULT,
                       tp=config.TP_PCT, sweep=config.SWEEP_BUF,
                       max_bars=config.MAX_BARS, cooldown=0, knife=0.0)
    real_stdout = sys.stdout
    sys.stdout = io.StringIO()          # заглушаем болтовню сборки aux
    try:
        pct5 = xd.fetch_daily_pct5()
        ab = e8.make_aux_builder(pct5, 96)
        res = {}
        for sym in SYMS:
            candles = ev.fetch(sym, "15", sg.DAYS)
            aux = ab(sym, candles)
            n = len(candles)
            h = int(n * sg.HOLD_FRAC)
            ho = candles[h:]
            pre = e2.prep(ho)
            aux_h = {k: slice_aux(v, h, n) for k, v in aux.items()}
            lev = config.SYMBOL_PARAMS[sym]["final"].get("lev", 5)
            row = {}
            for tag, cfg, lv in (("untrained", DEFAULT_CFG, lev),
                                 ("untrained_x2", DEFAULT_CFG, 2),
                                 ("bot", config.SYMBOL_PARAMS[sym]["final"], lev),
                                 ("bot_x2", config.SYMBOL_PARAMS[sym]["final"], 2)):
                g = genome(cfg)
                old = e2.LEV
                e2.LEV = lv
                try:
                    r = e2.run5(ho, pre, g, entry_filter=e8.make_filter8(g, aux_h))
                finally:
                    e2.LEV = old
                row[tag] = dict(ret=(r["balance"] / e2.START - 1) * 100,
                                dd=r["max_dd"] * 100, trades=r["trades"],
                                wins=r["wins"], ruined=bool(r["ruined"]),
                                lev=lv)
            res[sym] = row
    finally:
        sys.stdout = real_stdout
    build_reference_runs._cache = res
    return res


# =====================================================================
#                       вспомогательные расчёты
# =====================================================================

def coin_run(d, i0, i1, prm=None, trend=None, **kw):
    prm = sg.params_with_lev() if prm is None else prm
    cyc = []
    r = sg.go(d, prm, i0, i1, trend=trend, cycles=cyc, **kw)
    r["cycles"] = cyc
    return r


def buy_hold(d, i0, i1):
    c0, c1 = d["c"][i0], d["c"][i1 - 1]
    peak, dd = d["c"][i0], 0.0
    for i in range(i0, i1):
        p = d["c"][i]
        peak = max(peak, p)
        dd = max(dd, (peak - p) / peak)
    return (c1 / c0 - 1) * 100, dd * 100


def perturb(prm, rnd):
    p = dict(prm)
    for k in sg.PARAM_ORDER:
        v = prm[k] * rnd.uniform(0.9, 1.1)
        if k in ("rsi_p", "rsi_th", "levels"):
            v = int(round(v))
        p[k] = v
    p["rsi_p"] = min(sg.RSI_SET, key=lambda x: abs(x - p["rsi_p"]))
    p["levels"] = max(1, min(4, p["levels"]))
    p["lev"] = prm["lev"]
    return p


# =====================================================================
#                                MAIN
# =====================================================================

def main():
    t_start = time.time()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    blob = sg.prepare_data()
    data, n, h = blob["data"], blob["n"], blob["hold"]
    d0 = data[SYMS[0]]
    hold_days = (d0["t"][-1] - d0["t"][h]) / sg.DAY_MS
    hold_years = hold_days / YEAR_DAYS
    period = f"{sg.fmt_date(d0['t'][h])}..{sg.fmt_date(d0['t'][-1])}"

    ref_runs = self_tests(blob)

    # ---------------- 1. параметры ----------------
    out("")
    out("=" * 78)
    out("1. ПАРАМЕТРЫ (6 шт) — каждое значение из СЕРЕДИНЫ устойчивой области")
    out("=" * 78)
    just = {
        "rsi_p": "середина сетки {7,14,21}; чувствительность 0.48 при шуме "
                 "1.13 — параметр почти не решает, «лучшее» 21 держится на "
                 "одной монете (LTC) = подгонка",
        "rsi_th": "середина сетки {25,30,35}; чувствительность 0.44 (шум "
                  "1.13); шорт зеркально по 100-th=70",
        "levels": "середина {2,3,4}; плато выживаемости: больше колен = "
                  "меньше сливов; по нотионалу кривая на 3 плоская "
                  "(-0.151% за цикл против -0.165% на 4)",
        "step_atr": "коробка карты 0.4-0.7 и анатомия «1-2 ATR лучше всего» "
                    "тянут в одну сторону; 0.7 = середина объединённой "
                    "области, а не пик (2.0)",
        "tp": "коробка карты 2.0-3.5%, середина ~2.75 -> 2.5%; фикс-4% "
              "анатомии не доезжает на BTC (два дневных диапазона)",
        "stop": "коробка карты 7-10%, середина 8.5 -> 8%; самый "
                "НЕчувствительный параметр карты (0.30 при шуме 1.13)",
    }
    for k in sg.PARAM_ORDER:
        out(f"  {k:9} = {sg.PARAMS[k]:<7} {just[k]}")
    out(f"  {'lev':9} = {sg.LEV:<7} НЕ параметр: риск-отчёт даёт «слив<5% и "
        f"DD p95<30%» только на x2")
    out(f"  {'mult':9} = {sg.MULT:<7} НЕ параметр: зафиксирован в карте плато")
    out(f"  фильтр тренда: {'ВКЛ' if sg.USE_TREND_FILTER else 'ВЫКЛ'} — "
        f"единственный элемент, расширяющий область прибыльности без "
        f"настройки")
    out("     (карта: доля прибыльных ячеек 23.2%->29.3%, комбинаций с "
        "медианой>0 2.2%->6.4%,")
    out("      сливов 46.0%->31.4%, при x5 18.6%->7.4%; улучшает 81.8% "
        "комбинаций.")
    out("      Оговорка: случайному входу он помогает ТАК ЖЕ (+14.6 п.п. "
        "против +8.4) —")
    out("      это не «RSI+режим», а просто «не стой против тренда».)")
    out("  ЧЕГО НЕТ СОЗНАТЕЛЬНО: таймаута, кулдауна, зон, фильтра ножа, "
        "безубытка,")
    out("  реинвеста, подбора монеты под параметры, штормового фильтра.")
    out("")
    out("  ОГОВОРКА О ЧИСТОТЕ HOLDOUT: параметры не оптимизировались, но "
        "карта плато")
    out("  измерялась в том числе на этом отрезке. Неприкосновенность "
        "частичная:")
    out("  защита здесь — не «мы не смотрели», а «мы взяли середину сетки, "
        "а не пик».")
    out("")
    out("  ПРАВИЛО ДОПУСКА МОНЕТЫ: его НЕТ, и это не лень.")
    out("   - анатомия искала предсказывающую метрику инструмента и не "
        "нашла: в торгуемой")
    out("     постановке (метрика ПРЕДЫДУЩЕГО окна, n=185) лучший кандидат "
        "AC1 даёт")
    out("     t=+1.1, и это лучший из шести после просмотра всех шести;")
    out("   - панель «монета x календарное окно» (195 наблюдений): монета "
        "объясняет 1.1%")
    out("     дисперсии, календарное окно — 39.4%. Монета как инструмент "
        "почти ничего")
    out("     не решает, поэтому отбирать монету «под сетку» нечем.")
    out("  Следствие: гоняем ВСЕ пять монет, включая заведомо плохие, а "
        "допуск решают")
    out("  риск и воспроизводимость (раздел 9). Само правило «бери монету, "
        "которая")
    out("  работала раньше» проверено отдельно в разделе 8.")

    # ---------------- 2. holdout ----------------
    out("")
    out("=" * 78)
    out(f"2. HOLDOUT {period} ({hold_days:.0f} дней = "
        f"{(n-h)/n*100:.0f}% истории), плечо x{sg.LEV}")
    out("=" * 78)
    hold = {}
    for sym in SYMS:
        hold[sym] = coin_run(data[sym], h, n)
    out(f"{'монета':7} {'итог%':>7} {'DD%':>6} {'цикл':>5} {'WR%':>6} "
        f"{'ср.R':>8} {'сум.R':>7} {'PF':>6} {'тейк':>5} {'стоп':>5} "
        f"{'ликв':>5} {'мес%':>7} {'t(ср.R)':>8}")
    for sym in SYMS:
        r = hold[sym]
        cyc = r["cycles"]
        rs = [c["R"] for c in cyc]
        tp_n = sum(1 for c in cyc if c["reason"] == "tp")
        st_n = sum(1 for c in cyc if c["reason"] == "stop")
        pf = pf_of(cyc)
        out(f"{SHORT[sym]:7} {r['ret']:+7.1f} {r['dd']:6.1f} "
            f"{r['trades']:5} {wr_of(cyc):6.1f} {mean(rs):+8.4f} "
            f"{sum(rs):+7.2f} {pf:6.2f} {tp_n:5} {st_n:5} {r['liqs']:5} "
            f"{r['ret']/(hold_days/30):+7.2f} {tstat(rs):+8.2f}"
            f"{'  СЛИВ' if r['ruined'] else ''}")
    rets = [hold[s]["ret"] for s in SYMS]
    out(f"{'СРЕДНЕЕ':7} {mean(rets):+7.1f}   (равновзвешенно по 5 монетам, "
        f"каждая на своём счёте $20)")
    # портфель одной кривой: все циклы 5 монет по времени выхода, старт $100
    allc = sorted([c for s in SYMS for c in hold[s]["cycles"]],
                  key=lambda c: c["t_out"])
    eq = peak = 100.0
    pdd = 0.0
    for c in allc:
        eq += c["pnl"]
        peak = max(peak, eq)
        pdd = max(pdd, (peak - eq) / peak)
    out(f"{'ПОРТФЕЛЬ':7} {(eq/100-1)*100:+7.1f} {pdd*100:6.1f}   "
        f"(одна кривая: 5 монет x $20 = $100, {len(allc)} циклов, "
        f"маржа до 5x25%=125% капитала — на реальном счёте часть входов "
        f"не влезет)")

    # помесячно
    out("")
    out("  по месяцам холдоута (итог % от депозита монеты):")
    months = sorted({int((c["t_out"] - d0["t"][h]) // sg.MONTH_MS)
                     for s in SYMS for c in hold[s]["cycles"]})
    head = "  " + "монета ".ljust(7) + " ".join(f"м{m+1:<4}" for m in months)
    out(head)
    for sym in SYMS:
        by = {}
        for c in hold[sym]["cycles"]:
            m = int((c["t_out"] - d0["t"][h]) // sg.MONTH_MS)
            by[m] = by.get(m, 0.0) + c["pnl"]
        row = " ".join(f"{by.get(m,0.0)/sg.START*100:+5.1f}" for m in months)
        out(f"  {SHORT[sym]:7}{row}")
    pos_m = []
    for sym in SYMS:
        by = {}
        for c in hold[sym]["cycles"]:
            m = int((c["t_out"] - d0["t"][h]) // sg.MONTH_MS)
            by[m] = by.get(m, 0.0) + c["pnl"]
        pos_m += [1 if by.get(m, 0.0) > 0 else 0 for m in months]
    out(f"  доля прибыльных месяце-монет: {mean(pos_m)*100:.0f}% "
        f"({sum(pos_m)}/{len(pos_m)})")

    # ---------------- 3. режимы ----------------
    out("")
    out("=" * 78)
    out("3. РАЗБИВКА ПО РЕЖИМАМ РЫНКА (режим на входе, evolution6.calc_regime)")
    out("=" * 78)
    out("  С фильтром тренда лонги в bear и шорты в bull запрещены —")
    out("  поэтому в bull остаются лонги, в bear шорты, в range обе стороны.")
    out(f"{'монета':7} " + " ".join(f"{REG_NAME[g]:>22}" for g in (0, 1, 2)))
    out(f"{'':7} " + " ".join(f"{'n / ср.R / >0%':>22}" for _ in (0, 1, 2)))
    agg = {0: [], 1: [], 2: []}
    for sym in SYMS:
        cells = []
        for g in (0, 1, 2):
            rs = [c["R"] for c in hold[sym]["cycles"] if c["reg"] == g]
            agg[g] += rs
            if rs:
                cells.append(f"{len(rs):4} /{mean(rs):+7.4f} /"
                             f"{sum(1 for x in rs if x>0)/len(rs)*100:4.0f}%")
            else:
                cells.append(f"{'—':>22}")
        out(f"{SHORT[sym]:7} " + " ".join(f"{x:>22}" for x in cells))
    out(f"{'ВСЕ':7} " + " ".join(
        f"{(str(len(agg[g])) + ' /' + f'{mean(agg[g]):+7.4f}' + ' /' + f'{sum(1 for x in agg[g] if x>0)/max(1,len(agg[g]))*100:4.0f}%'):>22}"
        for g in (0, 1, 2)))
    out("  (гипотеза волны «сетка живёт в боковике, умирает в тренде против»:")
    out("   анатомия её опровергла на полной истории — худшим режимом был "
        "боковик;")
    out("   здесь смотрим, что даёт этот конфиг на holdout.)")

    # --- откуда деньги: сторона сделки (механика сетки или ставка на падение) ---
    out("")
    out("  ОТКУДА ДЕНЬГИ: разбивка по СТОРОНЕ (holdout — сплошной медведь,")
    out("  BTC -40.7%, DOGE -67.4%, SOL -62.5%, ETH -56.1%, LTC -57.8%)")
    out(f"{'монета':7} {'лонгов':>7} {'ср.R':>9} {'PnL$':>7} | "
        f"{'шортов':>7} {'ср.R':>9} {'PnL$':>7}   отрезок")
    side_stat = {}
    for tag, src, i0, i1 in (("holdout", hold, h, n), ("вне холдоута", None,
                                                       9600, h)):
        for sym in SYMS:
            if src is None:
                cy = coin_run(data[sym], i0, i1)["cycles"]
            else:
                cy = src[sym]["cycles"]
            lo = [c for c in cy if c["side"] == 1]
            sh = [c for c in cy if c["side"] == -1]
            if tag == "holdout":
                side_stat[sym] = dict(
                    n_long=len(lo), n_short=len(sh),
                    r_long=mean([c["R"] for c in lo]),
                    r_short=mean([c["R"] for c in sh]),
                    pnl_long=sum(c["pnl"] for c in lo),
                    pnl_short=sum(c["pnl"] for c in sh))
            out(f"{SHORT[sym]:7} {len(lo):7} "
                f"{mean([c['R'] for c in lo]):+9.4f} "
                f"{sum(c['pnl'] for c in lo):+7.2f} | {len(sh):7} "
                f"{mean([c['R'] for c in sh]):+9.4f} "
                f"{sum(c['pnl'] for c in sh):+7.2f}   {tag}")
        out("")
    lp = sum(1 for s in SYMS if side_stat[s]["pnl_long"] > 0)
    sp = sum(1 for s in SYMS if side_stat[s]["pnl_short"] > 0)
    out(f"  На холдоуте лонги в плюсе у {lp} монет из 5, шорты — у {sp} из 5.")
    out("  Весь плюс сделан ШОРТАМИ в сплошном медвежьем рынке; лонги "
        "потеряли на всех пяти.")
    out("  Вне холдоута (рынок смешанный) картина обратная — шорты в плюсе "
        "лишь у LTC.")
    out("  Это значит, что источник результата на холдоуте — НАПРАВЛЕНИЕ "
        "рынка,")
    out("  а не механика сетки; такой результат не переносится на рынок "
        "другого знака.")

    # --- где живёт убыток: глубина залитой сетки (сверка с анатомией) ---
    out("")
    out("  ГЛУБИНА ЗАЛИТОЙ СЕТКИ на выходе (анатомия: весь убыток живёт на "
        "полной сетке)")
    out(f"{'глубина':9} {'циклов':>7} {'WR%':>7} {'ср.R':>9} {'сумма $':>9}")
    for dep in (1, 2, 3):
        cy = [c for s in SYMS for c in hold[s]["cycles"] if c["depth"] == dep]
        if cy:
            out(f"{dep:<9} {len(cy):7} {wr_of(cy):7.1f} "
                f"{mean([c['R'] for c in cy]):+9.4f} "
                f"{sum(c['pnl'] for c in cy):+9.2f}")

    # ---------------- 4. сравнения ----------------
    out("")
    out("=" * 78)
    out("4. С ЧЕМ СРАВНИВАЕМ (тот же холдоут, тот же движок издержек)")
    out("=" * 78)
    out(f"{'монета':7} {'простая':>9} {'купил+':>8} {'необуч.':>9} "
        f"{'необуч':>8} {'бот':>9} {'бот':>8} {'случ.вход':>10} "
        f"{'без фильтра':>12}")
    out(f"{'':7} {'сетка x2':>9} {'держал':>8} {'родн.плечо':>9} "
        f"{'x2':>8} {'родн.':>9} {'x2':>8} {'медиана 5':>10} "
        f"{'тренда x2':>12}")
    cmp_rows = {}
    for sym in SYMS:
        bh, bh_dd = buy_hold(data[sym], h, n)
        nulls = []
        for sd in NULL_SEEDS:
            rn = coin_run(data[sym], h, n, kind="null", seed=sd)
            nulls.append(rn["ret"])
        nofilt = coin_run(data[sym], h, n, trend=False)
        rr = ref_runs[sym]
        cmp_rows[sym] = dict(
            grid=hold[sym]["ret"], bh=bh, bh_dd=bh_dd,
            untrained=rr["untrained"]["ret"], untrained_lev=rr["untrained"]["lev"],
            untrained_x2=rr["untrained_x2"]["ret"],
            bot=rr["bot"]["ret"], bot_lev=rr["bot"]["lev"],
            bot_x2=rr["bot_x2"]["ret"],
            null_med=median(nulls), null_all=nulls,
            nofilter=nofilt["ret"], nofilter_dd=nofilt["dd"],
            nofilter_ruined=nofilt["ruined"])
        c = cmp_rows[sym]
        out(f"{SHORT[sym]:7} {c['grid']:+9.1f} {c['bh']:+8.1f} "
            f"{c['untrained']:+9.1f} {c['untrained_x2']:+8.1f} "
            f"{c['bot']:+9.1f} {c['bot_x2']:+8.1f} {c['null_med']:+10.1f} "
            f"{c['nofilter']:+12.1f}")
    out(f"{'СРЕДН.':7} "
        f"{mean([cmp_rows[s]['grid'] for s in SYMS]):+9.1f} "
        f"{mean([cmp_rows[s]['bh'] for s in SYMS]):+8.1f} "
        f"{mean([cmp_rows[s]['untrained'] for s in SYMS]):+9.1f} "
        f"{mean([cmp_rows[s]['untrained_x2'] for s in SYMS]):+8.1f} "
        f"{mean([cmp_rows[s]['bot'] for s in SYMS]):+9.1f} "
        f"{mean([cmp_rows[s]['bot_x2'] for s in SYMS]):+8.1f} "
        f"{mean([cmp_rows[s]['null_med'] for s in SYMS]):+10.1f} "
        f"{mean([cmp_rows[s]['nofilter'] for s in SYMS]):+12.1f}")
    out("  необуч. родн.плечо = DOGE x10, LTC x5, BTC x15, ETH x5, SOL x5 "
        "(как в bots_honest);")
    out("  «бот» = боевой конфиг из config.SYMBOL_PARAMS (подтверждены были "
        "ETH и SOL).")
    out("  случайный вход: столько же входов и та же доля сторон, что у "
        "RSI, 5 зёрен:")
    for sym in SYMS:
        v = cmp_rows[sym]["null_all"]
        better = sum(1 for x in v if x > cmp_rows[sym]["grid"])
        out(f"    {SHORT[sym]:6} " + " ".join(f"{x:+7.1f}" for x in v) +
            f"   случайных лучше RSI: {better}/5")
    n_better = sum(1 for s in SYMS
                   if cmp_rows[s]["null_med"] > cmp_rows[s]["grid"])
    out(f"  ИТОГ нулевой модели: медиана случайного входа лучше нашей "
        f"на {n_better} монетах из 5.")
    n_filt = sum(1 for s in SYMS
                 if cmp_rows[s]["grid"] > cmp_rows[s]["nofilter"])
    out(f"  ВКЛАД ФИЛЬТРА ТРЕНДА на холдоуте: помог на {n_filt} монетах из "
        f"5; средний эффект "
        f"{mean([cmp_rows[s]['grid']-cmp_rows[s]['nofilter'] for s in SYMS]):+.1f} "
        f"п.п.")

    # ---------------- 5. риск ----------------
    out("")
    out("=" * 78)
    out("5. РИСК (методика grid_ruin: блочный бутстрап циклов, "
        f"{NBOOT} траекторий,")
    out("   блок 10-20 циклов, капитал x(1+0.25*R), «разорение» = ниже 20% "
        "старта)")
    out("=" * 78)
    risk = {}
    out(f"{'монета':7} {'циклов/год':>11} {'слив за год %':>13} "
        f"{'DD p95 %':>9} {'DD p50 %':>9} {'медиана итога за год':>21}")
    for sym in SYMS:
        cyc = []
        sg.go(data[sym], sg.params_with_lev(), h, n, cycles=cyc, no_ruin=True)
        rs = [c["R"] for c in cyc]
        per_year = int(round(len(rs) / hold_years))
        # зерно детерминированное: hash() строк рандомизирован между запусками
        rnd = random.Random(BOOT_SEED + SYMS.index(sym) * 17 + sg.LEV)
        b = sg.bootstrap(rs, per_year, rnd, block=BLOCK, nboot=NBOOT)
        risk[sym] = dict(boot=b, per_year=per_year, rs=rs)
        out(f"{SHORT[sym]:7} {per_year:11} {b['ruin']:13.1f} "
            f"{b['dd95']:9.1f} {b['dd50']:9.1f} {(b['p50']-1)*100:+21.1f}%")
    out("  (выборка = циклы холдоута, БЕЗ отсечки по сливу — иначе выборка "
        "обрезается на катастрофе)")
    out("")
    out("  ЛЕСТНИЦА ПЛЕЧ (тот же конфиг, холдоут; «слив/DD p95» из бутстрапа):")
    out(f"{'монета':7} " + " ".join(f"{'x'+str(lv):>21}" for lv in LEV_LADDER))
    out(f"{'':7} " + " ".join(f"{'итог% слив% DDp95%':>21}"
                              for _ in LEV_LADDER))
    ladder = {}
    for sym in SYMS:
        cells = []
        ladder[sym] = {}
        for lv in LEV_LADDER:
            prm = sg.params_with_lev(lev=lv)
            r = coin_run(data[sym], h, n, prm=prm)
            cyc = []
            sg.go(data[sym], prm, h, n, cycles=cyc, no_ruin=True)
            rs = [c["R"] for c in cyc]
            rnd = random.Random(BOOT_SEED + SYMS.index(sym) * 17 + lv)
            b = sg.bootstrap(rs, int(round(len(rs) / hold_years)), rnd,
                             block=BLOCK, nboot=NBOOT)
            ladder[sym][lv] = dict(ret=r["ret"], dd=r["dd"], ruin=b["ruin"],
                                   dd95=b["dd95"], ruined=r["ruined"])
            cells.append(f"{r['ret']:+7.1f} {b['ruin']:5.1f} {b['dd95']:6.1f}")
        out(f"{SHORT[sym]:7} " + " ".join(f"{x:>21}" for x in cells))
    out("  Риск-правило проекта: торговать можно, если слив за год < 5% И "
        "DD p95 < 30%.")

    # требуемый капитал
    out("")
    out("  ТРЕБУЕМЫЙ КАПИТАЛ (минимальный ордер биржи против первого колена):")
    with open(os.path.join(BASE, "instr_info.json"), encoding="utf-8") as fh:
        instr = json.load(fh)
    w = [sg.MULT ** k for k in range(sg.PARAMS["levels"])]
    frac0 = w[0] / sum(w)
    lot_ok, need_dep_by = {}, {}
    for sym in SYMS:
        px = data[sym]["c"][-1]
        info = instr[sym]
        min_not = max(info["minQty"] * px, info["minVal"])
        # нотионал первого колена = маржа_цикла(25%) * доля_колена * плечо
        need_dep = min_not / (0.25 * frac0 * sg.LEV)
        need_dep_by[sym] = need_dep
        lot_ok[sym] = need_dep <= RULE_MIN_DEPOSIT
        out(f"    {SHORT[sym]:6} мин.ордер ${min_not:7.2f} -> нужен депозит "
            f"${need_dep:8.0f}  (порог ${RULE_MIN_DEPOSIT:.0f}: "
            f"{'проходит' if lot_ok[sym] else 'НЕ ПРОХОДИТ'}; "
            f"на $20 неисполнимо у всех пяти)")
    out("    (grid_ruin считал то же самое и получил $66-$853 — тот же "
        "порядок; разница")
    out("     от цены на дату расчёта и запаса. Вывод общий: сетка по BTC на "
        "малом счёте")
    out("     физически неисполнима — минимальный лот 0.001 BTC.)")

    # ---------------- 6. стресс ----------------
    out("")
    out("=" * 78)
    out("6. СТРЕСС ИСПОЛНЕНИЯ (движок исполняет стоп РОВНО по цене стопа —")
    out("   на обвале так не бывает; анатомия на этом развернула все 5 монет)")
    out("=" * 78)
    out(f"{'монета':7} {'базово':>8} {'стоп по закр.':>14} {'стопов':>7} "
        f"{'из них пробито':>15} {'+0.3% просk.':>13} {'+1.0% просk.':>13}")
    stress = {}
    for sym in SYMS:
        base_c = []
        sg.go(data[sym], sg.params_with_lev(), h, n, cycles=base_c,
              no_ruin=True)
        cl_c = []
        sg.go(data[sym], sg.params_with_lev(), h, n, cycles=cl_c,
              no_ruin=True, stop_exec="close")
        pierced = sum(1 for a, b in zip(base_c, cl_c)
                      if abs(a["pnl"] - b["pnl"]) > 1e-12)
        n_stop = sum(1 for c in base_c if c["reason"] == "stop")
        r_cl = coin_run(data[sym], h, n, stop_exec="close")
        r_s3 = coin_run(data[sym], h, n, extra_slip=0.003)
        r_s10 = coin_run(data[sym], h, n, extra_slip=0.010)
        stress[sym] = dict(close=r_cl["ret"], slip3=r_s3["ret"],
                           slip10=r_s10["ret"], pierced=pierced, n_stop=n_stop)
        out(f"{SHORT[sym]:7} {hold[sym]['ret']:+8.1f} {r_cl['ret']:+14.1f} "
            f"{n_stop:7} {pierced:15} {r_s3['ret']:+13.1f} "
            f"{r_s10['ret']:+13.1f}")
    out(f"{'СРЕДН.':7} {mean([hold[s]['ret'] for s in SYMS]):+8.1f} "
        f"{mean([stress[s]['close'] for s in SYMS]):+14.1f} "
        f"{'':7} {'':15} {mean([stress[s]['slip3'] for s in SYMS]):+13.1f} "
        f"{mean([stress[s]['slip10'] for s in SYMS]):+13.1f}")

    # ---------------- 7. устойчивость ----------------
    out("")
    out("=" * 78)
    out(f"7. УСТОЙЧИВОСТЬ: {N_PERT} возмущений всех 6 параметров +-10% "
        f"(зерно {PERT_SEED})")
    out("=" * 78)
    rnd = random.Random(PERT_SEED)
    pert_params = [perturb(sg.params_with_lev(), rnd) for _ in range(N_PERT)]
    pert = {}
    for sym in SYMS:
        vals = []
        ruins = 0
        for p in pert_params:
            r = coin_run(data[sym], h, n, prm=p)
            vals.append(r["ret"])
            ruins += 1 if r["ruined"] else 0
        base_pos = sum(1 for v in vals if v < hold[sym]["ret"]) / len(vals)
        pert[sym] = dict(vals=vals, share=sum(1 for v in vals if v > 0) / len(vals),
                         med=median(vals), ruins=ruins,
                         p10=sg.pct(vals, 0.10), p90=sg.pct(vals, 0.90),
                         base_pos=base_pos)
        out(f"  {SHORT[sym]:6} медиана {pert[sym]['med']:+7.1f}%  "
            f"прибыльных {pert[sym]['share']*100:5.1f}%  "
            f"p10 {pert[sym]['p10']:+7.1f}%  p90 {pert[sym]['p90']:+7.1f}%  "
            f"сливов {ruins}  (база {hold[sym]['ret']:+.1f}% = перцентиль "
            f"{base_pos*100:3.0f} своего облака)")
    port_vals = [mean([pert[s]["vals"][k] for s in SYMS])
                 for k in range(N_PERT)]
    port_share = sum(1 for v in port_vals if v > 0) / len(port_vals)
    out(f"  ПОРТФЕЛЬ (среднее 5 монет): медиана {median(port_vals):+.1f}%, "
        f"прибыльных {port_share*100:.0f}%, "
        f"p10 {sg.pct(port_vals,0.10):+.1f}%, p90 {sg.pct(port_vals,0.90):+.1f}%")
    hi = [SHORT[s] for s in SYMS if pert[s]["base_pos"] >= 0.8]
    out(f"  Перцентиль базы в СВОЁМ облаке >=80 у: "
        f"{', '.join(hi) if hi else 'нет'} — там выбранная «середина» "
        f"оказалась")
    out("  локальным везением: сдвиг любого параметра на 10% ухудшает "
        "результат.")
    out("  Отдельно: +-10% на целом числе колен не двигает его (3 -> 3), "
        "поэтому +-1 колено:")
    for delta in (-1, 1):
        p = sg.params_with_lev()
        p["levels"] = max(1, p["levels"] + delta)
        vals = [coin_run(data[s], h, n, prm=p)["ret"] for s in SYMS]
        out(f"    колен {p['levels']}: " +
            " ".join(f"{SHORT[s]} {v:+6.1f}%" for s, v in zip(SYMS, vals)) +
            f"   среднее {mean(vals):+.1f}%")

    # ---------------- 8. вне холдоута ----------------
    out("")
    out("=" * 78)
    out("8. ВНЕ ХОЛДОУТА (первые 72% истории) — контекст, на вердикт не "
        "влияет")
    out("=" * 78)
    train_days = (d0["t"][h] - d0["t"][9600]) / sg.DAY_MS
    out(f"   отрезок {sg.fmt_date(d0['t'][9600])}..{sg.fmt_date(d0['t'][h])} "
        f"({train_days:.0f} дней)")
    train = {}
    for sym in SYMS:
        r = coin_run(data[sym], 9600, h)
        train[sym] = r
        out(f"  {SHORT[sym]:6} {r['ret']:+8.1f}%  DD {r['dd']:5.1f}%  "
            f"циклов {r['trades']:4}  WR {wr_of(r['cycles']):5.1f}%  "
            f"ср.R {mean([c['R'] for c in r['cycles']]):+.4f}"
            f"{'  СЛИВ' if r['ruined'] else ''}")
    out(f"  среднее по 5 монетам: {mean([train[s]['ret'] for s in SYMS]):+.1f}%")
    # переносится ли порядок монет
    tr_rank = sorted(SYMS, key=lambda s: -train[s]["ret"])
    ho_rank = sorted(SYMS, key=lambda s: -hold[s]["ret"])
    out(f"  порядок монет вне холдоута: "
        f"{' > '.join(SHORT[s] for s in tr_rank)}")
    out(f"  порядок монет на холдоуте:  "
        f"{' > '.join(SHORT[s] for s in ho_rank)}")
    xs = [tr_rank.index(s) for s in SYMS]
    ys = [ho_rank.index(s) for s in SYMS]
    mx, my = mean(xs), mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = (sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)) ** 0.5
    rho = num / den if den else 0.0
    out(f"  ранговая корреляция порядка монет: rho = {rho:+.2f} (n=5) — "
        f"это и есть проверка правила «выбирай монету по прошлому»")
    top2 = tr_rank[:2]
    bot2 = tr_rank[-2:]
    out(f"  если бы допускали ТОЛЬКО 2 лучшие монеты прошлого "
        f"({SHORT[top2[0]]}, {SHORT[top2[1]]}): "
        f"на холдоуте {mean([hold[s]['ret'] for s in top2]):+.1f}%")
    out(f"  две худшие прошлого ({SHORT[bot2[0]]}, {SHORT[bot2[1]]}): "
        f"на холдоуте {mean([hold[s]['ret'] for s in bot2]):+.1f}%")
    out("  Разница в нужную сторону, НО это одно наблюдение из одного "
        "разбиения при n=5;")
    out("  панель анатомии (195 наблюдений) даёт монете 1.1% дисперсии. "
        "Как правило допуска")
    out("  это не годится — на таком объёме доказательств правило "
        "неотличимо от совпадения.")

    # ---------------- 9. вердикты ----------------
    out("")
    out("=" * 78)
    out("9. ВЕРДИКТЫ (правила — в шапке файла)")
    out("=" * 78)
    out("  Две правки правил после первого прогона, обе фиксирую явно:")
    out("   а) «лот исполним на $20» -> «нужный депозит <= $100». $20 — это")
    out("      единица бэктеста, а не размер счёта; в первой редакции "
        "критерий")
    out("      про размер кошелька обнулял вердикт по СТРАТЕГИИ у всех пяти.")
    out("      Последствие: DOGE/LTC/SOL проходят, ETH ($180) и BTC ($616) "
        "нет.")
    out("   б) добавлены ВОРОТА МЕХАНИКИ (ужесточение): «торговый» требует,")
    out("      чтобы среднее по 5 монетам было > 0 И минимум 3 монеты из 5 "
        "в плюсе.")
    out("      Основание — исследование: плюсовая область карты держалась на")
    out("      ОДНОЙ монете, и это ровно тот случай, который прошлые волны")
    out("      принимали за находку. Без этих ворот LTC получил бы "
        "«торговый».")
    n_pos = sum(1 for s in SYMS if hold[s]["ret"] > 0)
    mech_ok = (mean(rets) > 0 and n_pos >= RULE_MECH_MIN_COINS)
    out(f"  ВОРОТА МЕХАНИКИ: среднее по 5 монетам {mean(rets):+.1f}% "
        f"(нужно >0), монет в плюсе {n_pos}/5 (нужно >={RULE_MECH_MIN_COINS}) "
        f"-> {'ПРОЙДЕНЫ' if mech_ok else 'НЕ ПРОЙДЕНЫ'}")
    out("")
    setups = {}
    for sym in SYMS:
        r = hold[sym]
        cyc = r["cycles"]
        b = risk[sym]["boot"]
        risk_ok = (not r["ruined"] and b["ruin"] < RULE_RUIN_MAX
                   and b["dd95"] < RULE_DD95_MAX)
        profit_ok = (r["ret"] > 0 and pert[sym]["med"] > 0
                     and pert[sym]["share"] >= RULE_PERT_SHARE)
        null_ok = r["ret"] >= cmp_rows[sym]["null_med"]
        stress_ok = (r["ret"] > 0 and stress[sym]["close"] > 0)
        lot = lot_ok[sym]
        flags = dict(risk=risk_ok, profit=profit_ok, null=null_ok,
                     stress=stress_ok, lot=lot, mechanics=mech_ok)
        if all(flags.values()):
            verdict, enabled, watch = "торговый", True, False
        elif risk_ok and lot and (profit_ok or null_ok or stress_ok):
            verdict, enabled, watch = "наблюдение", False, True
        else:
            verdict, enabled, watch = "не годится", False, False
        why = []
        why.append(f"holdout {r['ret']:+.1f}% за {hold_days/30:.1f} мес")
        why.append("слив" if r["ruined"] else "без слива")
        why.append(f"разорение за год {b['ruin']:.1f}%"
                   + ("" if b["ruin"] < RULE_RUIN_MAX else " > 5% ПОРОГ"))
        why.append(f"DD p95 {b['dd95']:.0f}%"
                   + ("" if b["dd95"] < RULE_DD95_MAX else " > 30% ПОРОГ"))
        why.append(f"возмущений в плюс {pert[sym]['share']*100:.0f}% "
                   f"(медиана {pert[sym]['med']:+.1f}%)")
        why.append(("не хуже" if null_ok else "ХУЖЕ") +
                   f" случайного входа ({cmp_rows[sym]['null_med']:+.1f}%)")
        why.append(("переживает" if stress_ok else "НЕ переживает") +
                   f" стоп по закрытию ({stress[sym]['close']:+.1f}%)")
        why.append(f"t(ср.R)={tstat([c['R'] for c in cyc]):+.2f}")
        why.append(f"нужен депозит ${need_dep_by[sym]:.0f}")
        if not mech_ok:
            why.append("ворота механики не пройдены (край есть не на всех "
                       "монетах)")
        reason = "; ".join(why)
        out(f"  {SHORT[sym]:6} {verdict:11} [риск {'+' if risk_ok else '-'}]"
            f"[прибыль {'+' if profit_ok else '-'}]"
            f"[нуль {'+' if null_ok else '-'}]"
            f"[стресс {'+' if stress_ok else '-'}]"
            f"[лот {'+' if lot else '-'}]"
            f"[механика {'+' if mech_ok else '-'}]")
        out(f"         {reason}")
        rs = [c["R"] for c in cyc]
        setups[sym] = dict(
            title=f"простая сетка усреднения {SHORT[sym]} (RSI+фильтр тренда)",
            symbol=sym, interval_min=15,
            params={k: sg.PARAMS[k] for k in sg.PARAM_ORDER},
            lev=sg.LEV, mult=sg.MULT, trend_filter=sg.USE_TREND_FILTER,
            margin_fraction=0.25,
            enabled=enabled, watch=watch, verdict=verdict, reason=reason,
            flags=flags,
            holdout=dict(n=r["trades"], wr=round(wr_of(cyc), 1),
                         exp_r=round(mean(rs), 4), pf=round(pf_of(cyc), 2),
                         sum_r=round(sum(rs), 2), ret=round(r["ret"], 1),
                         dd=round(r["dd"], 1),
                         t_exp_r=round(tstat(rs), 2),
                         tp=sum(1 for c in cyc if c["reason"] == "tp"),
                         stop=sum(1 for c in cyc if c["reason"] == "stop"),
                         liq=r["liqs"], ruined=bool(r["ruined"]),
                         months=round(hold_days / 30, 1), period=period,
                         by_side=dict(
                             n_long=side_stat[sym]["n_long"],
                             exp_r_long=round(side_stat[sym]["r_long"], 4),
                             pnl_long=round(side_stat[sym]["pnl_long"], 2),
                             n_short=side_stat[sym]["n_short"],
                             exp_r_short=round(side_stat[sym]["r_short"], 4),
                             pnl_short=round(side_stat[sym]["pnl_short"], 2)),
                         by_regime={REG_NAME[g]: dict(
                             n=sum(1 for c in cyc if c["reg"] == g),
                             exp_r=round(mean([c["R"] for c in cyc
                                               if c["reg"] == g]), 4))
                             for g in (0, 1, 2)}),
            risk=dict(ruin_year_pct=round(b["ruin"], 1),
                      dd_p95=round(b["dd95"], 1), dd_p50=round(b["dd50"], 1),
                      cycles_per_year=risk[sym]["per_year"],
                      med_year_ret=round((b["p50"] - 1) * 100, 1),
                      ruins_holdout=int(bool(r["ruined"])),
                      min_deposit_usd=round(need_dep_by[sym]),
                      ladder={f"x{lv}": dict(
                          ret=round(ladder[sym][lv]["ret"], 1),
                          dd=round(ladder[sym][lv]["dd"], 1),
                          ruin_year_pct=round(ladder[sym][lv]["ruin"], 1),
                          dd_p95=round(ladder[sym][lv]["dd95"], 1))
                          for lv in LEV_LADDER},
                      min_order_ok=lot),
            stress=dict(stop_at_close=round(stress[sym]["close"], 1),
                        slip_03=round(stress[sym]["slip3"], 1),
                        slip_10=round(stress[sym]["slip10"], 1),
                        stops=stress[sym]["n_stop"],
                        stops_pierced=stress[sym]["pierced"]),
            robustness=dict(n=N_PERT, share_pos=round(pert[sym]["share"], 3),
                            median=round(pert[sym]["med"], 1),
                            p10=round(pert[sym]["p10"], 1),
                            p90=round(pert[sym]["p90"], 1),
                            ruins=pert[sym]["ruins"],
                            base_percentile=round(pert[sym]["base_pos"] * 100)),
            benchmark=dict(buy_hold=round(cmp_rows[sym]["bh"], 1),
                           buy_hold_dd=round(cmp_rows[sym]["bh_dd"], 1),
                           untrained_grid=round(cmp_rows[sym]["untrained"], 1),
                           untrained_grid_lev=cmp_rows[sym]["untrained_lev"],
                           untrained_grid_x2=round(cmp_rows[sym]["untrained_x2"], 1),
                           bot=round(cmp_rows[sym]["bot"], 1),
                           bot_lev=cmp_rows[sym]["bot_lev"],
                           bot_x2=round(cmp_rows[sym]["bot_x2"], 1),
                           random_entry_median=round(cmp_rows[sym]["null_med"], 1),
                           no_trend_filter=round(cmp_rows[sym]["nofilter"], 1)),
            stats=dict(train_ret=round(train[sym]["ret"], 1),
                       train_n=train[sym]["trades"],
                       train_wr=round(wr_of(train[sym]["cycles"]), 1),
                       train_period=f"{sg.fmt_date(d0['t'][9600])}.."
                                    f"{sg.fmt_date(d0['t'][h])}",
                       in_sample_warning=False),
        )
    meta = dict(
        schema="setups_grid/1 (поля сетапа — как в signal_setups2.json)",
        generated=time.strftime("%Y-%m-%d %H:%M:%S"),
        source="simple_grid.py / simple_grid_check.py",
        holdout=period, holdout_days=round(hold_days),
        holdout_frac=round((n - h) / n, 3),
        params={k: sg.PARAMS[k] for k in sg.PARAM_ORDER},
        lev=sg.LEV, trend_filter=sg.USE_TREND_FILTER,
        rules=("торговый = риск(нет слива, разорение<5%/год, DD p95<30%) И "
               "прибыль(holdout>0, медиана возмущений>0, >=60% возмущений в "
               "плюс) И не хуже случайного входа И переживает стоп по "
               "закрытию свечи И нужный депозит <= $100 И ворота механики "
               "(среднее по 5 монетам>0 и >=3 монет в плюсе)"),
        mechanics_gate=dict(passed=bool(mech_ok),
                            mean_coin_ret=round(mean(rets), 1),
                            coins_positive=n_pos),
        portfolio=dict(ret=round((eq / 100 - 1) * 100, 1), dd=round(pdd * 100, 1),
                       cycles=len(allc),
                       mean_coin_ret=round(mean(rets), 1),
                       pert_median=round(median(port_vals), 1),
                       pert_share_pos=round(port_share, 3)),
        note=("Параметры не оптимизировались: взяты из середины устойчивых "
              "областей grid_plateau/grid_anatomy/grid_ruin. Карта плато "
              "измерялась в том числе на этом холдоуте, поэтому его "
              "неприкосновенность частичная."),
    )
    with open(SETUPS, "w", encoding="utf-8") as fh:
        json.dump(dict(meta=meta, setups=setups), fh, ensure_ascii=False,
                  indent=2)
    out("")
    out(f"  Результат записан в {SETUPS}")
    n_trade = sum(1 for s in SYMS if setups[s]["enabled"])
    n_watch = sum(1 for s in SYMS if setups[s]["watch"])
    out(f"  торговых {n_trade}, наблюдение {n_watch}, "
        f"не годится {5 - n_trade - n_watch}")

    # ---------------- 10. итог ----------------
    out("")
    out("=" * 78)
    out("10. ЧЕСТНЫЙ ИТОГ")
    out("=" * 78)
    out(f"  1. Холдоут 10.7 мес, плечо x2: среднее по 5 монетам "
        f"{mean(rets):+.1f}%, в плюсе {n_pos} монеты")
    out(f"     из 5, портфельная кривая {(eq/100-1)*100:+.1f}% при просадке "
        f"{pdd*100:.1f}%. Это НЕ доход,")
    out("     это ноль минус издержки. Торговать нечего.")
    out(f"  2. Ни один средний R не отличим от нуля: t от "
        f"{min(tstat([c['R'] for c in hold[s]['cycles']]) for s in SYMS):+.2f} "
        f"до {max(tstat([c['R'] for c in hold[s]['cycles']]) for s in SYMS):+.2f}")
    out("     при 67-123 циклах. Лучшая монета (LTC, +18.4%) даёт t=+1.79 — "
        "это не находка,")
    out("     а тот самый «единственный выживший инструмент» из карты плато.")
    out("  3. Источник плюса на холдоуте — шорты в сплошном медвежьем рынке "
        "(лонги в минусе")
    out("     на всех 5 монетах). Вне холдоута те же шорты в минусе на 4 из "
        "5. Это бета к")
    out("     направлению рынка, а не механика сетки.")
    out(f"  4. Вне холдоута (728 дней, рынок смешанный) — "
        f"{mean([train[s]['ret'] for s in SYMS]):+.1f}% в среднем, все монеты")
    out("     кроме LTC в минусе. Тот же конфиг, другой период — другой знак.")
    out(f"  5. Возмущения +-10%: портфель прибылен в "
        f"{port_share*100:.0f}% случаев, медиана "
        f"{median(port_vals):+.1f}%.")
    out("     У DOGE/ETH/LTC база сидит на 83-93 перцентиле своего же облака "
        "— выбранная")
    out("     «середина плато» на деле оказалась локальным везением.")
    out(f"  6. Стресс исполнения (стоп по закрытию свечи, как на реальном "
        f"обвале): среднее")
    out(f"     {mean([hold[s]['ret'] for s in SYMS]):+.1f}% -> "
        f"{mean([stress[s]['close'] for s in SYMS]):+.1f}%; лишние 0.3% "
        f"проскальзывания -> "
        f"{mean([stress[s]['slip3'] for s in SYMS]):+.1f}%. Запаса над")
    out("     издержками нет — плюс держится на идеализированном исполнении.")
    n_ruin0 = sum(1 for s in SYMS if risk[s]["boot"]["ruin"] < RULE_RUIN_MAX)
    n_dd_ok = sum(1 for s in SYMS if risk[s]["boot"]["dd95"] < RULE_DD95_MAX)
    out("  7. Что подтвердилось из исследования: снижение плеча до x2 "
        "убирает сливы")
    out(f"     (0 сливов на холдоуте, бутстрап-разорение <5% у {n_ruin0} "
        f"монет из 5; но просадка")
    out(f"     p95 укладывается в 30% лишь у {n_dd_ok} из 5), а фильтр "
        f"тренда помогает")
    out(f"     ({mean([cmp_rows[s]['grid']-cmp_rows[s]['nofilter'] for s in SYMS]):+.1f}"
        f" п.п. в среднем, {n_filt} монеты из 5). Оба вывода "
        f"воспроизвелись —")
    out("     но оба про ВЫЖИВАЕМОСТЬ, а не про доход.")
    out("  ВЕРДИКТ: для реальной торговли НЕ ГОДИТСЯ. Простая сетка на "
        "выживших элементах")
    out("  исследования даёт ноль до издержек и минус после них; ни одна "
        "монета не проходит")
    out("  полный набор ворот. LTC и DOGE оставлены в режиме наблюдения "
        "(watch), торговых нет.")
    out("")
    out(f"  прогон {time.time()-t_start:.0f}с")
    flush_out()


if __name__ == "__main__":
    main()
