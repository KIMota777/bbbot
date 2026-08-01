# -*- coding: utf-8 -*-
"""Тест движка signal_engine2 v2.2 на РЕАЛЬНЫХ данных BTC (ТФ 240 и 60).

Проверяем ровно то, что добавлено в v2.2, и ровно то, что могло сломаться:

  БЛОК 1. Штормовой фильтр: сколько баров он метит штормом при разных
          порогах, сколько сделок режет, какая доля сделок приходилась на
          штормовые бары и КАКОЙ У НИХ БЫЛ СРЕДНИЙ R. Это ключевая цифра:
          хуже R в шторм -> фильтр обоснован, лучше -> полезнее storm_mode=2.
  БЛОК 2. Отсутствие заглядывания в будущее у НОВЫХ рядов и ворот:
          prep_context/gate_eval/bar_features на ПРЕФИКСЕ истории обязаны
          дать то же самое, что на полной истории (>=100 срезов на ТФ).
  БЛОК 3. bar_features: все ключи SCORE_FEATURES, ни одного None/NaN,
          значения в разумных границах.
  БЛОК 4. Хук скоринга: фиктивная модель (score=1 при RSI<50) меняет число
          сделок предсказуемо, near-miss с gate="скоринг" появляются,
          баланс сходится с суммой P&L.
  БЛОК 5. Регрессия: старые геномы (без новых генов) дают ПОБИТОВО те же
          сделки. Эталон — webapp/data/signal2_*.json, посчитанные ДО v2.2.
  БЛОК 6. Кулдаун (единая трактовка от ЗАКРЫТИЯ бара) и market_context.

Запуск: python test_engine4.py  (вывод длинный — удобно в файл)
"""

import json
import math
import os
import sys
import time

import evolution as ev
import signal_engine2 as se

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

SYMBOL = "BTCUSDT"
DAYS = 1150
TFS = (240, 60)
TF_NAME = {240: "4ч", 60: "1ч"}
HOLD_FRAC = 0.28          # как в evolution12: holdout = последние 28% баров
SETUPS_FILE = "signal_setups2.json"
DATA_DIR = os.path.join("webapp", "data")

OK, BAD = 0, 0


def check(cond, text, detail=""):
    """Проверка с печатью. Возвращает cond (чтобы можно было ветвиться)."""
    global OK, BAD
    if cond:
        OK += 1
        print(f"  [ок]    {text}" + (f" | {detail}" if detail else ""))
    else:
        BAD += 1
        print(f"  [ПРОВАЛ] {text}" + (f" | {detail}" if detail else ""))
    return bool(cond)


def head(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def make_genome(src):
    """DEFAULTS2 + значения конфига, зажатые в границы GENES2 (та же логика,
    что в build_signal_analytics2.make_genome)."""
    g = dict(se.DEFAULTS2)
    for k, v in (src or {}).items():
        if k not in se.GENES2:
            continue
        lo, hi, is_int = se.GENES2[k]
        val = int(round(v)) if is_int else float(v)
        val = min(max(val, lo), hi)
        g[k] = int(val) if is_int else float(val)
    return g


def avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


# --------------------------------------------------------------------- данные
t_start = time.time()
head("ДАННЫЕ")
c15 = ev.fetch(SYMBOL, "15", DAYS)
ts15 = [c[0] for c in c15]
BARS, CTX, HOLD = {}, {}, {}
for tf in TFS:
    cc = ev.fetch(SYMBOL, str(tf), DAYS)
    t0 = time.time()
    CTX[tf] = se.prep_context(cc, interval_min=tf)
    BARS[tf] = cc
    HOLD[tf] = int(len(cc) * (1 - HOLD_FRAC))
    print(f"{TF_NAME[tf]} ({tf}м): баров {len(cc)}, prep_context "
          f"{time.time() - t0:.2f}с, holdout с бара {HOLD[tf]} "
          f"({time.strftime('%Y-%m-%d', time.gmtime(cc[HOLD[tf]][0] // 1000))}"
          f")")
print(f"15м-баров {len(c15)} (исполнение всех ТФ на 15м)")

with open(SETUPS_FILE, encoding="utf-8") as fh:
    RAW = json.load(fh)
CFG = {}
for key, rec in RAW.items():
    if not isinstance(rec, dict):
        continue
    base, _, tf_s = key.rpartition("@")
    if not tf_s.isdigit() or base not in se.SETUPS:
        continue
    tf = int(tf_s)
    if tf not in TFS:
        continue
    CFG[key] = dict(setup=base, tf=tf, g=make_genome(rec.get("genome")),
                    lev=int(rec.get("rec_lev") or 15),
                    raw_genome=rec.get("genome") or {})
KEYS = sorted(CFG)
print(f"конфигов из {SETUPS_FILE}: {len(KEYS)} ({', '.join(KEYS)})")


# ============================================================ БЛОК 1: ШТОРМ
head("БЛОК 1. ШТОРМОВОЙ ФИЛЬТР: сколько метит и какой R в шторм")

print("\n1.1 Доля баров, помеченных штормом (пороги по одному, весь период)")
print(f"{'ТФ':4} {'условие':28} {'баров-шторм':>12} {'доля':>7}")
for tf in TFS:
    ctx, n = CTX[tf], len(BARS[tf])
    variants = [("|ход за сутки| > 5%", dict(storm_day=0.05, storm_week=9.0,
                                             storm_atr_rank=9.0)),
                ("|ход за сутки| > 7% (семя)", dict(storm_day=0.07,
                                                    storm_week=9.0,
                                                    storm_atr_rank=9.0)),
                ("|ход за сутки| > 10%", dict(storm_day=0.10, storm_week=9.0,
                                              storm_atr_rank=9.0)),
                ("|ход за 7 суток| > 10%", dict(storm_day=9.0,
                                                storm_week=0.10,
                                                storm_atr_rank=9.0)),
                ("|ход за 7 суток| > 15% (семя)", dict(storm_day=9.0,
                                                       storm_week=0.15,
                                                       storm_atr_rank=9.0)),
                ("|ход за 7 суток| > 25%", dict(storm_day=9.0,
                                                storm_week=0.25,
                                                storm_atr_rank=9.0)),
                ("перцентиль ATR > 0.85 (семя)", dict(storm_day=9.0,
                                                      storm_week=9.0,
                                                      storm_atr_rank=0.85)),
                ("перцентиль ATR > 0.95", dict(storm_day=9.0, storm_week=9.0,
                                               storm_atr_rank=0.95)),
                ("ВСЕ ТРИ семени (7%/15%/0.85)", dict(storm_day=0.07,
                                                      storm_week=0.15,
                                                      storm_atr_rank=0.85))]
    for name, over in variants:
        g = dict(se.DEFAULTS2, **over)
        cnt = sum(1 for i in range(n) if se.storm_state(ctx, i, g)["storm"])
        print(f"{TF_NAME[tf]:4} {name:28} {cnt:12} {cnt / n * 100:6.1f}%")

print("\n1.2 R сделок НА ШТОРМОВЫХ барах против остальных "
      "(геномы победителей evolution12, шторм ВЫКЛЮЧЕН — только разметка)")
print("ВНИМАНИЕ: весь период = обучающий, это НЕ доказательство; строка "
      "holdout считается по невиданному отбором хвосту (28% истории).")
print(f"\n{'ключ':20} {'период':8} {'сделок':>7} {'шторм':>6} {'доля':>6} "
      f"{'R шторм':>8} {'R вне':>8} {'разница':>8}")
STORM_G = dict(se.DEFAULTS2)          # пороги семени: 7% / 15% / 0.85
BASE_RUNS = {}
pool = {tf: dict(st=[], no=[]) for tf in TFS}
pool_hold = {tf: dict(st=[], no=[]) for tf in TFS}
for key in KEYS:
    cf = CFG[key]
    tf, c4, ctx = cf["tf"], BARS[cf["tf"]], CTX[cf["tf"]]
    r = se.run_setup(cf["setup"], cf["g"], c4, ctx, c15, ts15, cf["lev"],
                     interval_min=tf)
    BASE_RUNS[key] = r
    idx_of = {c[0]: i for i, c in enumerate(c4)}
    rows = []
    for t in r["trades"]:
        i = idx_of.get(t["signal_ts"])
        if i is None:
            continue
        rows.append((i, t["r"], se.storm_state(ctx, i, STORM_G)["storm"]))
    for label, sel in (("весь", rows),
                       ("holdout", [x for x in rows if x[0] >= HOLD[tf]])):
        st = [r_ for _i, r_, s in sel if s]
        no = [r_ for _i, r_, s in sel if not s]
        if label == "весь":
            pool[tf]["st"] += st
            pool[tf]["no"] += no
        else:
            pool_hold[tf]["st"] += st
            pool_hold[tf]["no"] += no
        n = len(sel)
        share = len(st) / n * 100 if n else 0.0
        d = (avg(st) - avg(no)) if (st and no) else 0.0
        print(f"{key:20} {label:8} {n:7} {len(st):6} {share:5.1f}% "
              f"{avg(st):+8.3f} {avg(no):+8.3f} "
              f"{d:+8.3f}" + ("" if (st and no) else "   (мало данных)"))
print()
for tf in TFS:
    for label, p in (("весь период", pool[tf]), ("holdout", pool_hold[tf])):
        st, no = p["st"], p["no"]
        n = len(st) + len(no)
        print(f"ИТОГО {TF_NAME[tf]:3} {label:12}: сделок {n:4}, в шторм "
              f"{len(st):3} ({len(st) / n * 100 if n else 0:4.1f}%), "
              f"R шторм {avg(st):+.3f} против R вне {avg(no):+.3f} "
              f"-> разница {avg(st) - avg(no):+.3f}")
all_st = [x for tf in TFS for x in pool[tf]["st"]]
all_no = [x for tf in TFS for x in pool[tf]["no"]]
print(f"ИТОГО ОБА ТФ весь период: сделок {len(all_st) + len(all_no)}, "
      f"в шторм {len(all_st)} , R шторм {avg(all_st):+.3f} против "
      f"R вне {avg(all_no):+.3f} -> разница {avg(all_st) - avg(all_no):+.3f}")

# Честная оговорка: bounce_short по построению срабатывает ПОСЛЕ обвала,
# то есть штормовых баров у него заведомо много. Смотрим и без него.
per_setup = {}
for key in KEYS:
    cf, ctx = CFG[key], CTX[CFG[key]["tf"]]
    idx_of = {c[0]: i for i, c in enumerate(BARS[cf["tf"]])}
    d = per_setup.setdefault(cf["setup"], dict(st=[], no=[]))
    for t in BASE_RUNS[key]["trades"]:
        i = idx_of.get(t["signal_ts"])
        if i is None:
            continue
        d["st" if se.storm_state(ctx, i, STORM_G)["storm"] else "no"].append(
            t["r"])
print("\nпо сетапам (оба ТФ вместе, весь период):")
for s in se.SETUPS:
    d = per_setup.get(s)
    if not d:
        continue
    print(f"  {s:14} шторм {len(d['st']):3} сделок R {avg(d['st']):+.3f} | "
          f"вне {len(d['no']):3} сделок R {avg(d['no']):+.3f} | разница "
          f"{avg(d['st']) - avg(d['no']):+.3f}")
ex_st = [x for s, d in per_setup.items() if s != "bounce_short"
         for x in d["st"]]
ex_no = [x for s, d in per_setup.items() if s != "bounce_short"
         for x in d["no"]]
print(f"  БЕЗ bounce_short: шторм {len(ex_st)} сделок R {avg(ex_st):+.3f} | "
      f"вне {len(ex_no)} сделок R {avg(ex_no):+.3f} | разница "
      f"{avg(ex_st) - avg(ex_no):+.3f}")

print("\n1.3 Что делает включённый фильтр (storm_gate=1) с прогоном")
print(f"{'ключ':20} {'режим':26} {'сделок':>7} {'exp_R':>7} {'WR%':>6} "
      f"{'итог%':>8} {'DD%':>6}")
MODES = {0: "0: не торговать в шторм", 1: "1: не против шторма",
         2: "2: ТОЛЬКО в шторм"}
storm_runs = {}
for key in KEYS:
    cf = CFG[key]
    tf, c4, ctx = cf["tf"], BARS[cf["tf"]], CTX[cf["tf"]]
    st0 = se.stats(BASE_RUNS[key])
    print(f"{key:20} {'фильтр выключен (база)':26} {st0['n']:7} "
          f"{st0['exp_r']:+7.3f} {st0['wr']:6.1f} {st0['ret']:8.1f} "
          f"{st0['dd']:6.1f}")
    for mode, name in MODES.items():
        g = dict(cf["g"], storm_gate=1, storm_mode=mode,
                 storm_day=STORM_G["storm_day"],
                 storm_week=STORM_G["storm_week"],
                 storm_atr_rank=STORM_G["storm_atr_rank"])
        r = se.run_setup(cf["setup"], g, c4, ctx, c15, ts15, cf["lev"],
                         interval_min=tf)
        storm_runs[(key, mode)] = r
        s = se.stats(r)
        print(f"{'':20} {name:26} {s['n']:7} {s['exp_r']:+7.3f} "
              f"{s['wr']:6.1f} {s['ret']:8.1f} {s['dd']:6.1f}")

print("\n1.4 Проверки механики штормового ворота")
k0 = KEYS[0]
cf0 = CFG[k0]
r_off = BASE_RUNS[k0]
r_m0 = storm_runs[(k0, 0)]
check(se.GATE_STORM in se.GATE_NAMES, "GATE_STORM есть в GATE_NAMES",
      se.GATE_STORM)
check(r_m0["reject_counts"].get(se.GATE_STORM, 0) > 0
      or r_m0["gate_solo"].get(se.GATE_STORM, 0) > 0,
      "ворото шторма попадает в reject_counts/gate_solo",
      f"reject={r_m0['reject_counts'].get(se.GATE_STORM, 0)}, "
      f"solo={r_m0['gate_solo'].get(se.GATE_STORM, 0)}")
lost = {}
for key in KEYS:
    base_ts = {t["signal_ts"] for t in BASE_RUNS[key]["trades"]}
    m0_ts = {t["signal_ts"] for t in storm_runs[(key, 0)]["trades"]}
    lost[key] = len(base_ts - m0_ts)
check(sum(lost.values()) > 0, "режим 0 реально режет сделки",
      ", ".join(f"{k}: -{v}" for k, v in lost.items()))
nm_storm = sum(1 for x in r_m0["near_misses"] if x["gate"] == se.GATE_STORM)
print(f"  (для справки) near-miss по шторму у {k0}: {nm_storm}")
g_off = dict(cf0["g"], storm_gate=0)
r_zero = se.run_setup(cf0["setup"], g_off, BARS[cf0["tf"]], CTX[cf0["tf"]],
                      c15, ts15, cf0["lev"], interval_min=cf0["tf"])
check(json.dumps(r_zero["trades"], sort_keys=True)
      == json.dumps(r_off["trades"], sort_keys=True),
      "storm_gate=0 не меняет ни одной сделки")


# =================================================== БЛОК 2: НЕТ ЛУКАХЕДА
head("БЛОК 2. ЗАГЛЯДЫВАНИЕ В БУДУЩЕЕ: префикс истории против полной")
print("Считаем контекст и ворота на срезе c4[:k] и сравниваем значения на "
      "баре k-1 со значениями на том же баре, посчитанными по ВСЕЙ истории.")
NEW_ARRAYS = ("ret_1d", "ret_7d", "atr_rank", "vol_spike", "atr_d_pct",
              "day_range", "atr_d")
N_CUTS = 120
for tf in TFS:
    c4, ctx_full, n = BARS[tf], CTX[tf], len(BARS[tf])
    lo_cut = max(1000, int(n * 0.35))
    cuts = [lo_cut + round(i * (n - lo_cut - 1) / (N_CUTS - 1))
            for i in range(N_CUTS)]
    bad_arr, bad_gate, bad_feat = [], [], []
    worst = 0.0
    setups_cycle = list(se.SETUPS)
    for j, k in enumerate(cuts):
        pre = c4[:k]
        ctx_p = se.prep_context(pre, interval_min=tf)
        i = k - 1
        for name in NEW_ARRAYS:
            a, b = ctx_p[name][i], ctx_full[name][i]
            if a is None or b is None:
                if a is not b:
                    bad_arr.append((k, name, a, b))
                continue
            d = abs(a - b)
            worst = max(worst, d)
            if d > 1e-12:
                bad_arr.append((k, name, a, b))
        setup = setups_cycle[j % len(setups_cycle)]
        key = f"{setup}@{tf}"
        g = dict(CFG[key]["g"] if key in CFG else se.DEFAULTS2,
                 storm_gate=1, storm_mode=j % 3)
        ext_p = se.build_ext(setup, g, pre, interval_min=tf)
        ext_f = se.build_ext(setup, g, c4, interval_min=tf)
        e_p = se.gate_eval(setup, i, pre, ctx_p, g, ext_p)
        e_f = se.gate_eval(setup, i, c4, ctx_full, g, ext_f)
        sig_p = json.dumps([e_p["ok"], e_p["first_fail"], e_p["n_failed"],
                            e_p["stop"], e_p["tp"], e_p["dist"],
                            e_p["gates"], e_p["diag"]], sort_keys=True)
        sig_f = json.dumps([e_f["ok"], e_f["first_fail"], e_f["n_failed"],
                            e_f["stop"], e_f["tp"], e_f["dist"],
                            e_f["gates"], e_f["diag"]], sort_keys=True)
        if sig_p != sig_f:
            bad_gate.append((k, setup))
        f_p = se.bar_features(setup, i, pre, ctx_p, g, ext_p)
        f_f = se.bar_features(setup, i, c4, ctx_full, g, ext_f)
        for kk in se.SCORE_FEATURES:
            if abs(f_p[kk] - f_f[kk]) > 1e-12:
                bad_feat.append((k, setup, kk, f_p[kk], f_f[kk]))
    check(not bad_arr, f"{TF_NAME[tf]}: новые ряды prep_context совпали на "
                       f"{N_CUTS} срезах", f"макс. расхождение {worst:.2e}")
    check(not bad_gate, f"{TF_NAME[tf]}: gate_eval (со штормом, все режимы) "
                        f"совпал на {N_CUTS} срезах",
          "" if not bad_gate else str(bad_gate[:3]))
    check(not bad_feat, f"{TF_NAME[tf]}: bar_features совпали на {N_CUTS} "
                        f"срезах", "" if not bad_feat else str(bad_feat[:3]))

print("\n2.2 Независимый пересчёт новых рядов «в лоб» (без быстрых окон):")
for tf in TFS:
    c4, ctx, n = BARS[tf], CTX[tf], len(BARS[tf])
    nd = se.bars_per_day(tf)
    closes = ctx["closes"]
    atr = ctx["atr_d"]
    bad = []
    idxs = [int(n * 0.4) + round(k * (n * 0.59) / 59) for k in range(60)]
    for i in idxs:
        # ход за сутки/неделю
        for name, k in (("ret_1d", nd), ("ret_7d", 7 * nd)):
            want = closes[i] / closes[i - k] - 1.0
            if abs(ctx[name][i] - want) > 1e-12:
                bad.append((i, name, ctx[name][i], want))
        # перцентиль ATR среди последних 90 суток
        w = [x for x in atr[max(0, i - 90 * nd + 1):i + 1] if x is not None]
        want = sum(1 for x in w if x <= atr[i]) / len(w)
        if abs(ctx["atr_rank"][i] - want) > 1e-12:
            bad.append((i, "atr_rank", ctx["atr_rank"][i], want))
        # дневной диапазон и его медиана за 30 суток
        rngs = []
        for j in range(max(0, i - 30 * nd + 1), i + 1):
            seg = c4[max(0, j - nd + 1):j + 1]
            rngs.append((max(x[2] for x in seg) - min(x[3] for x in seg))
                        / closes[j])
        srt = sorted(rngs)
        m = len(srt)
        med = srt[m // 2] if m % 2 else 0.5 * (srt[m // 2 - 1] + srt[m // 2])
        want = rngs[-1] / med if med else 1.0
        if abs(ctx["vol_spike"][i] - want) > 1e-9:
            bad.append((i, "vol_spike", ctx["vol_spike"][i], want))
    check(not bad, f"{TF_NAME[tf]}: быстрые окна = наивный пересчёт на "
                   f"{len(idxs)} барах", "" if not bad else str(bad[:3]))


# ================================================== БЛОК 3: BAR_FEATURES
head("БЛОК 3. BAR_FEATURES: состав, типы, границы")
BOUNDS = {
    "side": (-1, 1), "regime": (0, 2), "reg_bull": (0, 1), "reg_range": (0, 1),
    "reg_bear": (0, 1), "rsi": (0, 100), "rsi_thr": (0, 100),
    "dist_to_thr": (-100, 100), "zone_pos": (-10, 10), "range_pos": (0, 1),
    "atr_pct": (0, 50), "atr_rank": (0, 1), "vol_spike": (0, 50),
    "ret_1d": (-0.9, 5), "ret_7d": (-0.9, 10),
    "ret_since_extreme": (-0.95, 10),
    "storm": (0, 1), "ema50_dist": (-0.9, 5), "ema200_dist": (-0.9, 10),
    "ema_slope50": (-0.5, 0.5), "funding": (-1, 1), "aroon_up": (0, 100),
    "aroon_dn": (0, 100), "body_frac": (0, 1), "upper_wick_frac": (0, 1),
    "lower_wick_frac": (0, 1), "bars_since_signal": (0, 999),
    "hour_utc": (0, 23), "dow": (0, 6), "stop_pct": (0, 60),
}
check(set(BOUNDS) == set(se.SCORE_FEATURES),
      "границы описаны для всех признаков SCORE_FEATURES",
      f"{len(se.SCORE_FEATURES)} признаков")
rng = {k: [float("inf"), float("-inf")] for k in se.SCORE_FEATURES}
n_calls = 0
bad_keys = bad_val = bad_bound = 0
examples = []
for key in KEYS:
    cf = CFG[key]
    c4, ctx, tf = BARS[cf["tf"]], CTX[cf["tf"]], cf["tf"]
    g = dict(cf["g"], storm_gate=1)
    ext = se.build_ext(cf["setup"], g, c4, interval_min=tf)
    a = int(g["window"]) + int(ext["lag"]) + 40
    step = max(1, (len(c4) - a) // 400)
    for i in range(a, len(c4), step):
        f = se.bar_features(cf["setup"], i, c4, ctx, g, ext,
                            bars_since_signal=(i % 50))
        n_calls += 1
        if set(f) != set(se.SCORE_FEATURES):
            bad_keys += 1
            continue
        for kk, v in f.items():
            if v is None or not isinstance(v, float) or math.isnan(v) \
                    or math.isinf(v):
                bad_val += 1
                if len(examples) < 5:
                    examples.append((key, i, kk, v))
                continue
            lo, hi = BOUNDS[kk]
            if v < lo or v > hi:
                bad_bound += 1
                if len(examples) < 5:
                    examples.append((key, i, kk, v))
            rng[kk][0] = min(rng[kk][0], v)
            rng[kk][1] = max(rng[kk][1], v)
check(bad_keys == 0, f"состав ключей совпал на всех {n_calls} вызовах")
check(bad_val == 0, "нет None/NaN/inf ни в одном признаке",
      "" if not examples else str(examples[:3]))
check(bad_bound == 0, "все значения в объявленных границах",
      "" if not examples else str(examples[:3]))
# прогрев: признаки должны считаться и там, где уровней ещё нет
crashed = []
for key in KEYS:
    cf = CFG[key]
    c4, ctx, tf = BARS[cf["tf"]], CTX[cf["tf"]], cf["tf"]
    for mode in range(4):
        g = dict(cf["g"], storm_gate=1, stop_mode=mode)
        ext = se.build_ext(cf["setup"], g, c4, interval_min=tf)
        for i in (0, 1, 5, 30, 100):
            try:
                f = se.bar_features(cf["setup"], i, c4, ctx, g, ext)
                if set(f) != set(se.SCORE_FEATURES) or any(
                        v is None or math.isnan(v) for v in f.values()):
                    crashed.append((key, mode, i, "плохое значение"))
            except Exception as e:                     # noqa: BLE001
                crashed.append((key, mode, i, f"{type(e).__name__}: {e}"))
check(not crashed, "bar_features не падает на прогреве (все stop_mode)",
      "" if not crashed else str(crashed[:3]))

print("\nфактические диапазоны признаков (для агента, обучающего модель):")
for kk in se.SCORE_FEATURES:
    lo, hi = rng[kk]
    print(f"  {kk:20} [{lo:+10.4f} .. {hi:+10.4f}]   границы теста "
          f"[{BOUNDS[kk][0]} .. {BOUNDS[kk][1]}]")


# ==================================================== БЛОК 4: ХУК СКОРИНГА
head("БЛОК 4. ХУК ПРЕДИКТИВНОГО СКОРИНГА (фиктивная модель)")


def score_low_rsi(f):
    """Фиктивная модель: 1.0 если RSI < 50, иначе 0.0."""
    return 1.0 if f["rsi"] < 50.0 else 0.0


def score_high_rsi(f):
    return 0.0 if f["rsi"] < 50.0 else 1.0


print(f"{'ключ':20} {'прогон':26} {'сделок':>7} {'near«скоринг»':>13} "
      f"{'exp_R':>7} {'итог%':>8}")
for key in KEYS:
    cf = CFG[key]
    c4, ctx, tf = BARS[cf["tf"]], CTX[cf["tf"]], cf["tf"]
    base = BASE_RUNS[key]
    r_lo = se.run_setup(cf["setup"], cf["g"], c4, ctx, c15, ts15, cf["lev"],
                        interval_min=tf, score_fn=score_low_rsi, score_min=0.5)
    r_hi = se.run_setup(cf["setup"], cf["g"], c4, ctx, c15, ts15, cf["lev"],
                        interval_min=tf, score_fn=score_high_rsi,
                        score_min=0.5)
    for name, r in (("без модели (база)", base),
                    ("модель: только RSI<50", r_lo),
                    ("модель: только RSI>=50", r_hi)):
        s = se.stats(r)
        nm = sum(1 for x in r["near_misses"] if x["gate"] == se.REJ_SCORE)
        print(f"{key if name.startswith('без') else '':20} {name:26} "
              f"{s['n']:7} {nm:13} {s['exp_r']:+7.3f} {s['ret']:8.1f}")
    # предсказуемость: в прогоне «только RSI<50» не должно быть НИ ОДНОЙ
    # сделки с RSI >= 50 на сигнальном баре
    bad = [t for t in r_lo["trades"] if t["rsi"] is not None and t["rsi"] > 50]
    check(not bad, f"{key}: все сделки прошли фильтр RSI<50",
          f"нарушений {len(bad)}")
    check(len(r_lo["trades"]) <= len(base["trades"]),
          f"{key}: сделок не больше, чем в базе",
          f"{len(r_lo['trades'])} <= {len(base['trades'])}")
    nm = [x for x in r_lo["near_misses"] if x["gate"] == se.REJ_SCORE]
    check(r_lo["blocked_score"] == r_lo["reject_counts"].get(se.REJ_SCORE, 0)
          == len(nm),
          f"{key}: отказы модели = reject_counts = near-miss «скоринг»",
          f"{r_lo['blocked_score']}")
    bal = round(se.START + sum(t["pnl"] for t in r_lo["trades"]), 6)
    check(abs(bal - round(r_lo["balance"], 6)) < 1e-6,
          f"{key}: баланс = старт + сумма P&L", f"{bal} vs {r_lo['balance']}")
    if nm:
        check(all(x["margin"] < 0 for x in nm),
              f"{key}: у near-miss «скоринг» запас отрицательный")

print("\n4.2 Гены скоринга")
cf = CFG[KEYS[0]]
c4, ctx, tf = BARS[cf["tf"]], CTX[cf["tf"]], cf["tf"]
r_arg = se.run_setup(cf["setup"], cf["g"], c4, ctx, c15, ts15, cf["lev"],
                     interval_min=tf, score_fn=score_low_rsi, score_min=0.5)
g_gene = dict(cf["g"], score_gate=1, score_min=0.5)
r_gene = se.run_setup(cf["setup"], g_gene, c4, ctx, c15, ts15, cf["lev"],
                      interval_min=tf, score_fn=score_low_rsi)
check(json.dumps(r_gene["trades"], sort_keys=True)
      == json.dumps(r_arg["trades"], sort_keys=True),
      "ген score_min/score_gate=1 = явный аргумент score_min")
g_off = dict(cf["g"], score_gate=0)
r_geneoff = se.run_setup(cf["setup"], g_off, c4, ctx, c15, ts15, cf["lev"],
                         interval_min=tf, score_fn=score_low_rsi)
check(json.dumps(r_geneoff["trades"], sort_keys=True)
      == json.dumps(BASE_RUNS[KEYS[0]]["trades"], sort_keys=True),
      "score_gate=0 без аргумента: модель не спрашивают вовсе")
r_nofn = se.run_setup(cf["setup"], g_gene, c4, ctx, c15, ts15, cf["lev"],
                      interval_min=tf)
check(json.dumps(r_nofn["trades"], sort_keys=True)
      == json.dumps(BASE_RUNS[KEYS[0]]["trades"], sort_keys=True),
      "без score_fn гены скоринга не действуют")
check(all("score" in t for t in r_arg["trades"]),
      "у сделок прогона с моделью есть поле score",
      f"сделок {len(r_arg['trades'])}")
check(all("score" not in t for t in BASE_RUNS[KEYS[0]]["trades"]),
      "без модели поля score нет (старый формат сделки не изменился)")


# ================================================== БЛОК 5: РЕГРЕССИЯ v2.1
head("БЛОК 5. РЕГРЕССИЯ: старые геномы -> побитово те же сделки")
print("Эталон: webapp/data/signal2_*.json, посчитанные движком ДО v2.2 "
      "(теми же геномами, на тех же кэшах свечей).")
SKIP = {"idx", "entry_ts_s", "exit_ts_s", "signal_ts_s", "regime_name"}
for key in KEYS:
    path = os.path.join(DATA_DIR, f"signal2_{key}.json")
    if not os.path.exists(path):
        print(f"  (нет эталона {path} — пропуск)")
        continue
    with open(path, encoding="utf-8") as fh:
        ref = json.load(fh)
    cf = CFG[key]
    g_old = make_genome(ref.get("genome"))
    new_in_ref = [k for k in ("storm_gate", "storm_day", "storm_week",
                              "storm_atr_rank", "storm_mode", "score_gate",
                              "score_min") if k in (ref.get("genome") or {})]
    lev = int(ref.get("lev") or cf["lev"])
    tf = int(ref.get("interval_min") or cf["tf"])
    r = se.run_setup(cf["setup"], g_old, BARS[tf], CTX[tf], c15, ts15, lev,
                     interval_min=tf)
    got = [{k: v for k, v in t.items() if k not in SKIP} for t in r["trades"]]
    exp = [{k: v for k, v in t.items() if k not in SKIP}
           for t in ref.get("trades", [])]
    same = json.dumps(got, sort_keys=True) == json.dumps(exp, sort_keys=True)
    check(same, f"{key}: сделки совпали с эталоном v2.1",
          f"{len(got)} шт., новых генов в эталонном геноме: "
          f"{new_in_ref or 'нет'}")
    if not same:
        for a, b in zip(got, exp):
            if a != b:
                print(f"    первое расхождение: {a}\n                 против "
                      f"{b}")
                break
    gnm_ref = [{k: v for k, v in x.items()
                if k not in ("ts_s", "regime_name")}
               for x in ref.get("near_misses", [])]
    check(json.dumps(r["near_misses"], sort_keys=True)
          == json.dumps(gnm_ref, sort_keys=True),
          f"{key}: near-miss совпали с эталоном",
          f"{len(r['near_misses'])} шт.")

print("\n5.2 Геном БЕЗ новых генов = геном с дефолтами новых генов")
for key in KEYS:
    cf = CFG[key]
    tf = cf["tf"]
    g_bare = {k: v for k, v in cf["g"].items()
              if k not in ("storm_gate", "storm_day", "storm_week",
                           "storm_atr_rank", "storm_mode", "score_gate",
                           "score_min")}
    r_bare = se.run_setup(cf["setup"], g_bare, BARS[tf], CTX[tf], c15, ts15,
                          cf["lev"], interval_min=tf)
    check(json.dumps(r_bare["trades"], sort_keys=True)
          == json.dumps(BASE_RUNS[key]["trades"], sort_keys=True),
          f"{key}: геном без 7 новых генов даёт те же сделки",
          f"{len(r_bare['trades'])} шт.")


# ============================================ БЛОК 6: КУЛДАУН И КОНТЕКСТ
head("БЛОК 6. КУЛДАУН (единая трактовка) И MARKET_CONTEXT")
g = dict(se.DEFAULTS2, cooldown=3)
bar = 240
bms = se.bar_ms_of(bar)
exit_ts = 1_700_000_000_000
check(se.cooldown_ms(g, bar) == 3 * bms, "cooldown_ms = cooldown * бар ТФ")
check(se.signal_ts(exit_ts, bar) == exit_ts + bms,
      "signal_ts = открытие + длительность бара (ЗАКРЫТИЕ)")
check(se.cooldown_ok(exit_ts + 2 * bms, exit_ts, g, bar) is True,
      "бар, ЗАКРЫВШИЙСЯ ровно через 3 бара после выхода, разрешён")
check(se.cooldown_ok(exit_ts + 1 * bms, exit_ts, g, bar) is False,
      "бар раньше — запрещён")
check(se.cooldown_ok(exit_ts, 0, g, bar) is True, "без сделок кулдауна нет")
diff = 0
for key in KEYS:
    cf = CFG[key]
    tf = cf["tf"]
    cd = se.cooldown_ms(cf["g"], tf)
    last = 0
    for t in BASE_RUNS[key]["trades"]:
        if last:
            ts = t["signal_ts"]
            by_close = se.signal_ts(ts, tf) >= last + cd
            by_open = ts >= last + cd
            diff += int(by_close != by_open)
        last = t["exit_ts"]
print(f"  сделок, где трактовка «от открытия» (как сейчас в советнике) "
      f"отличается от «от закрытия» (движок): {diff}")
print("  -> советнику advisor.py следует звать se.cooldown_ok(bar_ts, "
      "last_exit, g, iv) вместо сравнения ОТКРЫТИЯ бара с last_exit+cool_ms")

print("\n6.2 market_context на последнем закрытом баре")
for tf in TFS:
    mc = se.market_context(BARS[tf], CTX[tf], len(BARS[tf]) - 1, se.DEFAULTS2)
    need = ("regime", "regime_name", "ret_1d", "ret_7d", "atr_rank", "storm",
            "storm_dir", "atr_pct")
    check(all(k in mc for k in need), f"{TF_NAME[tf]}: все обязательные ключи",
          ", ".join(need))
    print(f"  {TF_NAME[tf]}: рынок {mc['regime_name']}, сутки "
          f"{mc['ret_1d'] * 100:+.2f}%, неделя {mc['ret_7d'] * 100:+.2f}%, "
          f"перцентиль ATR {mc['atr_rank']:.4f}, ATR {mc['atr_pct']:.2f}%, "
          f"vol_spike {mc['vol_spike']:.2f}, "
          f"шторм: {'ДА' if mc['storm'] else 'нет'} "
          f"(направление {mc['storm_dir']:+d})")
# исторический пример шторма — чтобы видеть, что плашка не всегда пустая
tf = 240
best_i = max(range(len(BARS[tf])), key=lambda i: abs(CTX[tf]["ret_1d"][i]))
mc = se.market_context(BARS[tf], CTX[tf], best_i, se.DEFAULTS2)
print(f"  сильнейший суточный ход в истории 4ч: "
      f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(mc['ts'] / 1000))} "
      f"{mc['ret_1d'] * 100:+.2f}% за сутки, неделя "
      f"{mc['ret_7d'] * 100:+.2f}%, шторм: "
      f"{'ДА' if mc['storm'] else 'нет'}, направление {mc['storm_dir']:+d}")
check(mc["storm"] is True, "сильнейший суточный ход помечен штормом")

head("ИТОГ")
print(f"проверок пройдено: {OK}, провалено: {BAD}, "
      f"время {time.time() - t_start:.1f}с")
print("СТАТУС: " + ("ВСЁ ОК" if BAD == 0 else f"ЕСТЬ ПРОВАЛЫ ({BAD})"))
sys.exit(1 if BAD else 0)
