# -*- coding: utf-8 -*-
"""v12: честный GA-отбор ВОСЬМИ сигналов — 4 сетапа x 2 ТФ (4ч и 1ч).

Задание владельца после движка v2.1 (мульти-ТФ + два новых шортовых сетапа):
прогнать через ЧЕСТНУЮ вложенную валидацию все действующие сетапы
(se2.SETUPS: sweep_long, dump_long, bounce_short, rally_short) на двух
таймфреймах сигнального бара — 240 мин (4ч) и 60 мин (1ч). Итог — 8
независимых вердиктов с ключами "<setup>@<tf>" в evolution12_winners.json.

Методология СТРОГО как в evolution11 (вложенная / nested валидация):
  [0 .. HOLD)  — обучение И отбор кандидатов; внутри — anchored фолды
                 (трейн всегда [0, b)); их взаимное загрязнение допустимо,
                 потому что влияет только на ВЫБОР кандидата;
  [HOLD .. n)  — HOLDOUT (~28% истории, hold = 72%): эти бары НЕ видит ни
                 GA, ни выбор победителя — только финальная оценка. Граница
                 hold считается в барах СВОЕГО ТФ (исполнение всегда на 15м).
  Выбор победителя: кандидаты со всех фолдов, ранжирование по exp_r
  ОБЪЕДИНЁННЫХ сделок внутренних OOS-отрезков (при n>=10, иначе -99 и
  добор по числу сделок). Бенчмарк — необученное семя DEFAULTS2 на том же
  holdout (edge_exp_r = exp_r победителя - exp_r семени). Лестница плечей
  [10, 15, 20] — тоже на holdout. Вердикт passed: n>=8, exp_r>0, PF>=1.2,
  sum_r>0, edge>0.

Отличия от evolution11 — НЕ методологии, а синхронизация с движком v2.1:
  - пол сделок трейна = se2.MIN_TRADES_FIT (20; в evolution10 локально 25 —
    владелец явно разрешил меньше сделок ради винрейта);
  - печать генома своя: в GENES2 теперь 33 гена (bounce_*, ma_*, fund_*,
    aroon_*), GENE_GROUPS/gene_active в evolution10 их не знают;
  - rec_lev по заказу: максимальное плечо лестницы с DD<=25% И итогом >0
    (включая x10); нет такого — x10 с пометкой caution;
  - кэш прогонов по gkey обязателен: 1ч — 27.6 тыс. баров, прогон дорогой.

Запуск полный:  python evolution12.py
Smoke-прогон:   GA_POP=8 GA_GENS=2 GA_SETUPS=rally_short GA_TFS=240,60 \
                python evolution12.py
Переменные окружения: GA_POP, GA_GENS, GA_ELITE, GA_SEED, GA_SETUPS,
GA_TFS ("240,60"), GA_HOLD (доля holdout, 0.28), GA_DAYS, GA_OUT.
"""

import json
import os
import random
import sys
import time

import evolution as ev
import evolution4 as e4
import signal_engine2 as se2

try:                      # русский вывод не должен падать при перенаправлении
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


POP = max(4, _env_int("GA_POP", 56))
GENS = max(1, _env_int("GA_GENS", 24))
ELITE = max(1, min(_env_int("GA_ELITE", 7), POP - 1))
SEED = _env_int("GA_SEED", 47)
DAYS = _env_int("GA_DAYS", 1150)
OUT = os.environ.get("GA_OUT") or "evolution12_winners.json"
HOLD_FRAC = float(os.environ.get("GA_HOLD", "0.28"))   # доля holdout

LEVS = [10, 15, 20]        # лестница плечей (рабочий диапазон владельца)
GA_LEV = 15                # плечо, на котором идёт отбор
CAND_PER_FOLD = 5          # уникальных лучших геномов с каждого фолда
MIN_HOLD_TRADES = 8        # меньше сделок на holdout — статистики нет
MIN_INNER_TRADES = 10      # меньше на внутренних OOS — exp_r не сравниваем
INNER_FOLDS = 3            # как в evolution11 (фактически даёт 2 живых фолда)
FIT_MIN = se2.MIN_TRADES_FIT   # пол сделок трейна — движковый (20)
TOURN = 3                  # размер турнира при выборе родителей

TF_NAMES = {240: "4ч", 60: "1ч"}

_env_setups = [s.strip() for s in
               os.environ.get("GA_SETUPS", "").split(",") if s.strip()]
SETUPS_RUN = _env_setups or list(se2.SETUPS)
_bad = [s for s in SETUPS_RUN if s not in se2.SETUPS]
if _bad:
    raise SystemExit(f"GA_SETUPS: неизвестные сетапы {_bad}; "
                     f"допустимы {list(se2.SETUPS)}")

_env_tfs = [s.strip() for s in
            os.environ.get("GA_TFS", "").split(",") if s.strip()]
TFS_RUN = [int(x) for x in _env_tfs] if _env_tfs else list(TF_NAMES)
_badtf = [t for t in TFS_RUN if t not in TF_NAMES]
if _badtf:
    raise SystemExit(f"GA_TFS: неизвестные ТФ {_badtf}; допустимы 240,60")

SMOKE = ((POP, GENS) != (56, 24) or len(SETUPS_RUN) != len(se2.SETUPS)
         or len(TFS_RUN) != len(TF_NAMES))


def gkey(g):
    """Ключ генома для кэшей (все 33 гена GENES2, порядок словаря движка)."""
    return tuple(g[k] if se2.GENES2[k][2] else round(g[k], 4)
                 for k in se2.GENES2)


def inner_folds(hold_start):
    """anchored фолды ВНУТРИ обучающей части: трейн [0,b), OOS [b,e).
    Дословно evolution11.inner_folds (третий фолд с хвостом <=3 бара
    отбрасывается фильтром >100 — живых фолдов два)."""
    step = hold_start // (INNER_FOLDS + 1)
    out = []
    for k in range(INNER_FOLDS):
        b = step * (k + 2)
        e_ = min(hold_start, b + step)
        if e_ - b > 100:
            out.append((0, b, e_))
    return out


# ------------------------------------------------------------ прогоны с кэшем
def run_trades(setup, data, g, seg, lev, memo):
    """Сделки одного отрезка с кэшем по (gkey, плечо, отрезок): победитель и
    семя гоняются на одних и тех же отрезках несколько раз (внутренние OOS,
    holdout, лестница), а 1ч-прогон дорогой."""
    c_sig, ctx, c15, ts15, tf = data
    key = (gkey(g), lev, seg)
    tr = memo.get(key)
    if tr is None:
        r = se2.run_setup(setup, g, c_sig, ctx, c15, ts15, lev,
                          signal_range=seg, collect_diag=False,
                          interval_min=tf)
        tr = r["trades"]
        memo[key] = tr
    return tr


def pooled(setup, data, g, segments, lev, memo):
    """Сделки со всех отрезков вместе + метрики (как в evolution11.pooled)."""
    tr = []
    for seg in segments:
        tr += run_trades(setup, data, g, tuple(seg), lev, memo)
    n = len(tr)
    if not n:
        return dict(n=0, wr=0.0, exp_r=0.0, sum_r=0.0, pf=None, dd=0.0,
                    ret=0.0, tp=0, stop=0)
    wins = [t for t in tr if t["pnl"] > 0]
    gp = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in tr if t["pnl"] < 0)
    bal, peak, dd = se2.START, se2.START, 0.0
    for t in sorted(tr, key=lambda x: x["exit_ts"]):
        bal += t["pnl"]
        peak = max(peak, bal)
        dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
    return dict(
        n=n, wr=round(len(wins) / n * 100, 1),
        exp_r=round(sum(t["r"] for t in tr) / n, 3),
        sum_r=round(sum(t["r"] for t in tr), 2),
        pf=round(gp / gl, 2) if gl > 0 else None,
        dd=round(dd * 100, 1),
        ret=round((bal / se2.START - 1) * 100, 1),
        tp=sum(1 for t in tr if t["reason"] == "tp"),
        stop=sum(1 for t in tr if t["reason"] == "stop"))


# ------------------------------------------------------------------------- GA
def rank_key(row):
    """Сначала добравшие пол FIT_MIN сделок (между собой — по фитнесу),
    затем не добравшие (по числу сделок) — схема evolution10, но пол
    движковый (20, не локальный 25)."""
    f, n, _g = row
    ok = n >= FIT_MIN
    return (1 if ok else 0, f if ok else 0.0, n)


def evolve_setup(setup, data, fold, prefix):
    """GA на трейне фолда. Возвращает список (фитнес, сделок, геном),
    лучшие первыми. Кэш оценок по gkey обязателен (1ч дорогой)."""
    c_sig, ctx, c15, ts15, tf = data
    rand_g, clamp, mutate, cross = e4.ga_tools(se2.GENES2)
    a, b = fold
    cache = {}
    t0 = time.time()

    def eval_g(g):
        key = gkey(g)
        got = cache.get(key)
        if got is None:
            r = se2.run_setup(setup, g, c_sig, ctx, c15, ts15, GA_LEV,
                              signal_range=(a, b), collect_diag=False,
                              interval_min=tf)
            got = (se2.fitness(r), len(r["trades"]))
            cache[key] = got
        return got

    def score(pop):
        out = []
        for g in pop:
            f, n = eval_g(g)
            out.append((f, n, g))
        return sorted(out, key=rank_key, reverse=True)

    seeds = [clamp(dict(se2.DEFAULTS2))]
    alt = dict(se2.DEFAULTS2)
    alt["entry_mode"] = 1              # оба режима входа обязаны быть в семенах
    seeds.append(clamp(alt))
    pop = [dict(s) for s in seeds]
    while len(pop) < POP:
        pop.append(rand_g())
    scored = score(pop)
    step = max(1, GENS // 6)
    for gen in range(GENS):
        new = [g for _, _, g in scored[:ELITE]]
        while len(new) < POP:
            if random.random() < 0.3:
                new.append(mutate(scored[random.randrange(ELITE)][2]))
            else:
                k = min(TOURN, len(scored))
                p1 = max(random.sample(scored, k), key=rank_key)[2]
                p2 = max(random.sample(scored, k), key=rank_key)[2]
                new.append(mutate(cross(p1, p2)))
        scored = score(new)
        if (gen + 1) % step == 0 or gen == GENS - 1:
            alive = sum(1 for _f, n, _ in scored if n >= FIT_MIN)
            mark = "" if scored[0][1] >= FIT_MIN else "  (пол не пройден!)"
            print(f"    {prefix} поколение {gen+1:2}: фитнес "
                  f"{scored[0][0]:+7.2f} | сделок {scored[0][1]:3} | с полом "
                  f"{FIT_MIN}+ {alive:2}/{POP} | кэш {len(cache)} | "
                  f"{time.time()-t0:.0f}с{mark}")
    return scored


# ------------------------------------------------- печать генома (33 гена)
def gene_active12(setup, g, key):
    """Влияет ли ген на сетап/геном (v2.1: сетапы bounce/rally и гены из
    ботов; неиспользуемые числа владельцу читать не нужно)."""
    is_sweep = setup.startswith("sweep")
    fam = setup.split("_")[0]
    is_move = fam in ("dump", "pump", "rally")   # движение + RSI + свеча
    is_bounce = setup == "bounce_short"
    is_range = setup.startswith("range")
    mode = int(g["stop_mode"])
    if key in ("window", "stop_cap", "stop_mode", "hold_days", "cooldown",
               "reg_bull", "reg_range", "reg_bear", "entry_mode",
               "fund_gate", "aroon_gate", "storm_gate", "score_gate"):
        return True
    # параметры шторма/скоринга читаемы только при включённом гейте
    if key in ("storm_day", "storm_week", "storm_atr_rank", "storm_mode"):
        return int(g.get("storm_gate", 0)) == 1
    if key == "score_min":
        return int(g.get("score_gate", 0)) == 1
    if key == "rsi_idx":
        return not is_sweep          # у sweep RSI не ворото
    if key in ("rsi_os", "counter_rsi_shift", "with_rsi_shift"):
        return is_range or is_move or is_bounce
    if key == "zone":
        return is_range              # у bounce потолок отката — bounce_max_frac
    if key == "counter_zone_mult":
        return is_range or is_bounce
    if key in ("poke_atr", "age"):
        return is_sweep
    if key in ("drop_frac", "drop_days"):
        return is_move or is_bounce
    if key == "buf_atr":
        return mode in (0, 1, 3)
    if key == "swing_bars":
        return mode == 1
    if key == "stop_atr_k":
        return mode == 2
    if key in ("retest_atr", "retest_bars"):
        return int(g["entry_mode"]) == 1
    if key in ("bounce_min_atr", "bounce_max_frac"):
        return is_bounce
    if key == "ma_gate":
        return setup == "rally_short"
    if key == "ma_len":
        return setup == "rally_short" and int(g["ma_gate"]) == 1
    if key == "fund_thr":
        return int(g["fund_gate"]) == 1
    if key in ("aroon_n", "aroon_thr"):
        return int(g["aroon_gate"]) == 1
    return True


GENE_GROUPS12 = (
    ("индикаторы/зона", ("rsi_idx", "window", "zone", "rsi_os")),
    ("стоп", ("stop_mode", "swing_bars", "stop_atr_k", "buf_atr", "stop_cap")),
    ("профиль сетапа", ("poke_atr", "age", "drop_frac", "drop_days",
                        "bounce_min_atr", "bounce_max_frac")),
    ("ведение", ("hold_days", "cooldown")),
    ("режимы", ("reg_bull", "reg_range", "reg_bear", "counter_rsi_shift",
                "counter_zone_mult", "with_rsi_shift")),
    ("вход", ("entry_mode", "retest_atr", "retest_bars")),
    ("ворота из ботов", ("ma_gate", "ma_len", "fund_gate", "fund_thr",
                         "aroon_gate", "aroon_n", "aroon_thr")),
    ("шторм", ("storm_gate", "storm_day", "storm_week", "storm_atr_rank",
               "storm_mode")),
    ("скоринг", ("score_gate", "score_min")),
)
_covered = {k for _t, ks in GENE_GROUPS12 for k in ks}
_missing = set(se2.GENES2) - _covered
_extra = _covered - set(se2.GENES2)
assert not _missing and not _extra, (
    f"печать генома отстала от GENES2: не описаны {sorted(_missing)}, "
    f"лишние {sorted(_extra)}")


def fmt_gene12(setup, g, k):
    _lo, _hi, is_int = se2.GENES2[k]
    v = int(g[k]) if is_int else float(g[k])
    if k == "rsi_idx":
        txt = f"{v} (RSI{se2.RSI_SET[v]})"
    elif k == "stop_mode":
        txt = f"{v} ({se2.STOP_MODE_NAMES[v]})"
    elif k == "entry_mode":
        txt = f"{v} ({'по рынку' if v == 0 else 'лимитка на ретесте'})"
    elif k in ("reg_bull", "reg_range", "reg_bear"):
        txt = "разрешён" if v else "ЗАПРЕЩЁН"
    elif k in ("ma_gate", "fund_gate", "aroon_gate"):
        txt = "ВКЛ" if v else "выкл"
    elif k in ("stop_cap", "drop_frac"):
        txt = f"{v*100:.2f}%"
    elif k == "fund_thr":
        txt = f"{v:.3f}%/8ч"
    elif k == "bounce_min_atr":
        txt = f"{v:.2f} дн.ATR"
    elif k == "bounce_max_frac":
        txt = f"{v*100:.0f}% падения"
    elif is_int:
        txt = str(v)
    else:
        txt = f"{v:.3f}"
    return txt + ("" if gene_active12(setup, g, k) else "  [не используется]")


def print_genome(setup, g):
    print("  ГЕНОМ-ПОБЕДИТЕЛЬ:")
    for title, keys in GENE_GROUPS12:
        parts = [f"{k}={fmt_gene12(setup, g, k)}" for k in keys]
        print(f"    {title:16} " + "; ".join(parts))


def thr_cmp(setup, reg, g):
    """Фактическое условие сетапа в режиме reg — так, как его сравнивает
    gate_eval (у bounce_short RSI-ворото обратное, зона = потолок отката)."""
    if not se2.regime_allowed(reg, g):
        return "запрещён"
    rsi_thr, zone_thr = se2.effective_thresholds(setup, reg, g)
    if setup == "bounce_short":
        return (f"RSI>={rsi_thr:.0f} (перепроданность снята), "
                f"откат<={zone_thr*100:.0f}% падения")
    if setup.startswith("sweep"):
        return f"прокол>={float(g['poke_atr']):.2f} дн.ATR (RSI не ворото)"
    if setup.endswith("long"):
        return f"RSI<={rsi_thr:.0f}"
    return f"RSI>={100 - rsi_thr:.0f}"


# ------------------------------------------------------------------- main
def d(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def mon(bars, tf):
    """Число баров ТФ -> месяцы."""
    return bars * tf / 1440.0 / 30.44


def main():
    t_all = time.time()
    random.seed(SEED)
    print("=" * 78)
    print("evolution12: честный GA-отбор 8 сигналов — 4 сетапа x 2 ТФ")
    print("(вложенная валидация: holdout не видят ни GA, ни выбор победителя)")
    print("=" * 78)
    c15 = ev.fetch("BTCUSDT", "15", DAYS)
    ts15 = [c[0] for c in c15]
    print(f"BTCUSDT: 15м-баров {len(c15)} (исполнение всех ТФ на 15м)")
    dsets = {}
    for tf in TFS_RUN:
        c_sig = ev.fetch("BTCUSDT", str(tf), DAYS)
        ctx = se2.prep_context(c_sig, interval_min=tf)
        n = len(c_sig)
        hold = int(n * (1 - HOLD_FRAC))
        folds = inner_folds(hold)
        dsets[tf] = dict(data=(c_sig, ctx, c15, ts15, tf), n=n, hold=hold,
                         folds=folds)
        reg_h = ctx["regime"][hold:]
        print(f"ТФ {TF_NAMES[tf]} ({tf}м): баров {n} "
              f"({d(c_sig[0][0])}..{d(c_sig[-1][0])})")
        print(f"  обучение+отбор [0..{hold}) = {mon(hold, tf):.1f} мес | "
              f"HOLDOUT [{hold}..{n}) = {mon(n - hold, tf):.1f} мес "
              f"({d(c_sig[hold][0])}..{d(c_sig[-1][0])})")
        print(f"  режимы holdout: bull {reg_h.count(0)*100//len(reg_h)}%, "
              f"range {reg_h.count(1)*100//len(reg_h)}%, "
              f"bear {reg_h.count(2)*100//len(reg_h)}% | цена "
              f"{c_sig[hold][4]:.0f} -> {c_sig[-1][4]:.0f} "
              f"({(c_sig[-1][4]/c_sig[hold][4]-1)*100:+.1f}%)")
        for i, (a, b, e_) in enumerate(folds):
            print(f"  фолд {i+1}: трейн [{a}..{b}) {mon(b - a, tf):.1f} мес "
                  f"-> внутр.OOS [{b}..{e_}) {mon(e_ - b, tf):.1f} мес")
    print(f"GA: POP={POP} GENS={GENS} ELITE={ELITE} сид={SEED} | отбор на "
          f"x{GA_LEV} | сетапы: {', '.join(SETUPS_RUN)} | ТФ: "
          f"{', '.join(TF_NAMES[t] for t in TFS_RUN)}")
    print(f"пол трейна {FIT_MIN} сделок (se2.MIN_TRADES_FIT); победитель — по "
          f"exp_r объединённых внутренних OOS при n>={MIN_INNER_TRADES}")
    if SMOKE:
        print("!!! SMOKE-РЕЖИМ (POP/GENS/сетапы/ТФ урезаны): результат "
              "черновой, в JSON пометка smoke=true. Полный прогон: "
              "python evolution12.py")
    print(f"вывод: {OUT}")

    combos = [(s, tf) for s in SETUPS_RUN for tf in TFS_RUN]
    results = {}
    for ci, (setup, tf) in enumerate(combos):
        t0 = time.time()
        ds = dsets[tf]
        data, n, hold, folds = ds["data"], ds["n"], ds["hold"], ds["folds"]
        name = f"{setup}@{tf}"
        print("\n" + "=" * 78)
        print(f"{name}  ({ci+1}/{len(combos)})  {TF_NAMES[tf]}: "
              f"{se2.SETUP_TITLES[setup]}")
        print("=" * 78)

        # 1) GA на трейнах внутренних фолдов -> пул кандидатов
        cands = []
        for fi, (a, b, e_) in enumerate(folds):
            print(f"  -- фолд {fi+1}: GA на трейне [{a}..{b}) --")
            scored = evolve_setup(setup, data, (a, b),
                                  f"{setup[:10]}-f{fi+1}@{tf}")
            seen = set()
            for fit, n_tr, g in scored:
                k = gkey(g)
                if k in seen:
                    continue
                seen.add(k)
                cands.append(dict(g=g, src=fi, fit=fit, train_n=n_tr))
                if len(seen) >= CAND_PER_FOLD:
                    break
        print(f"  кандидатов: {len(cands)}")

        # 2) выбор победителя ТОЛЬКО по внутренним OOS (holdout не трогаем!)
        memo = {}
        inner_segs = [(b, e_) for (_a, b, e_) in folds]
        ranked = []
        for cnd in cands:
            p = pooled(setup, data, cnd["g"], inner_segs, GA_LEV, memo)
            ranked.append((p["exp_r"] if p["n"] >= MIN_INNER_TRADES else -99,
                           p["n"], p, cnd))
        ranked.sort(key=lambda x: (-x[0], -x[1]))
        _sc, _n, inner_p, best = ranked[0]
        g_win = best["g"]
        print(f"  ВЫБРАН (фолд {best['src']+1}, фитнес трейна "
              f"{best['fit']:+.2f} на {best['train_n']} сделках): внутр.OOS "
              f"{inner_p['n']} сделок, exp {inner_p['exp_r']:+.3f}R, "
              f"PF {inner_p['pf']}, WR {inner_p['wr']}%")
        if inner_p["n"] < MIN_INNER_TRADES:
            print(f"  внимание: ни у одного кандидата нет "
                  f"{MIN_INNER_TRADES} сделок на внутренних OOS — выбор "
                  f"фактически по числу сделок, статистики мало")

        # 3) ЧЕСТНЫЙ ЭКЗАМЕН на holdout + бенчмарк необученного семени
        hold_seg = [(hold, n)]
        h = pooled(setup, data, g_win, hold_seg, GA_LEV, memo)
        base = pooled(setup, data, dict(se2.DEFAULTS2), hold_seg, GA_LEV,
                      memo)
        print("  --- HOLDOUT (эти данные не видели ни GA, ни отбор) ---")
        print(f"    победитель: {h['n']:3} сделок | WR {h['wr']:5.1f}% | exp "
              f"{h['exp_r']:+.3f}R | PF {h['pf']} | сумма {h['sum_r']:+.2f}R "
              f"| итог {h['ret']:+.1f}% | DD {h['dd']}% | "
              f"tp/stop {h['tp']}/{h['stop']}")
        print(f"    семя DEFAULTS2: {base['n']:3} сделок | WR "
              f"{base['wr']:5.1f}% | exp {base['exp_r']:+.3f}R | PF "
              f"{base['pf']} | итог {base['ret']:+.1f}%")
        edge = round(h["exp_r"] - base["exp_r"], 3)
        print(f"    преимущество отбора над семенем: {edge:+.3f}R/сделку "
              f"({'есть' if edge > 0 else 'НЕТ'})")

        # 4) лестница плечей — на HOLDOUT (честно, не на полном периоде)
        ladder = []
        print("  лестница плечей на holdout:")
        for lev in LEVS:
            pl = pooled(setup, data, g_win, hold_seg, lev, memo)
            ladder.append(dict(lev=lev, ret=pl["ret"], dd=pl["dd"],
                               n=pl["n"], wr=pl["wr"], exp_r=pl["exp_r"],
                               pf=pl["pf"]))
            print(f"    x{lev:<3} итог {pl['ret']:+7.1f}% | DD "
                  f"{pl['dd']:5.1f}% | {pl['n']:3} сделок | WR "
                  f"{pl['wr']:5.1f}% | PF {pl['pf']}")
        rec = None
        for row in ladder[::-1]:               # 20 -> 15 -> 10
            if row["dd"] <= 25 and row["ret"] > 0:
                rec = row["lev"]
                break
        caution = rec is None
        if rec is None:
            rec = 10
        print(f"    -> рекомендованное плечо: x{rec}"
              + (" (осторожно: ни одно плечо не даёт DD<=25% с плюсом)"
                 if caution else ""))

        # 5) вердикт (критерии evolution11). PF=None означает ноль убыточных
        #    сделок: при живой выборке с плюсом это «бесконечный» PF, а не
        #    провал порога.
        pf_ok = ((h["pf"] >= 1.2) if h["pf"] is not None
                 else (h["n"] > 0 and h["exp_r"] > 0))
        ok = (h["n"] >= MIN_HOLD_TRADES and h["exp_r"] > 0 and pf_ok
              and h["sum_r"] > 0 and edge > 0)
        why = []
        if h["n"] < MIN_HOLD_TRADES:
            why.append(f"на holdout всего {h['n']} сделок < {MIN_HOLD_TRADES}")
        if h["exp_r"] <= 0:
            why.append(f"holdout: expectancy {h['exp_r']:+.3f}R не > 0")
        if not pf_ok:
            why.append(f"holdout: PF {h['pf']} < 1.2")
        if h["sum_r"] <= 0:
            why.append(f"holdout: сумма {h['sum_r']:+.2f}R не > 0")
        if edge <= 0:
            why.append(f"нет преимущества над необученным семенем "
                       f"({edge:+.3f}R)")
        print(f"  ВЕРДИКТ: {'ПРОШЁЛ честный экзамен' if ok else 'НЕ ПРОШЁЛ'}"
              + ("" if ok else " — " + "; ".join(why)))

        print_genome(setup, g_win)
        print("  режимные пороги: " + " | ".join(
            f"{se2.REGIME_NAMES[q]}: {thr_cmp(setup, q, g_win)}"
            for q in (0, 1, 2)))

        results[name] = dict(
            genome=g_win, interval_min=tf, rec_lev=rec, caution=caution,
            passed=bool(ok), fail_reasons=why, holdout=h,
            holdout_benchmark=base, edge_exp_r=edge, inner_oos=inner_p,
            ladder=ladder, src_fold=best["src"] + 1,
            train_fitness=round(best["fit"], 2), train_n=best["train_n"],
            hold_start_bar=hold, n_bars=n, smoke=SMOKE,
            ga=dict(pop=POP, gens=GENS, elite=ELITE, seed=SEED, lev=GA_LEV,
                    days=DAYS, hold_frac=HOLD_FRAC,
                    folds=[list(f) for f in folds]),
            elapsed_s=round(time.time() - t0, 1))
        print(f"  время: {time.time()-t0:.0f}с")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2, default=float)

    print("\n" + "=" * 78)
    print("ИТОГ ЧЕСТНОГО ЭКЗАМЕНА (holdout, никем не виденный)")
    print("=" * 78)
    print(f"{'сигнал':18} {'сдел':>5} {'WR%':>6} {'exp R':>7} {'PF':>5} "
          f"{'итог%':>8} {'DD%':>6} {'плечо':>6} {'семя exp':>9} {'edge':>7}"
          f"  вердикт")
    for nm, r in results.items():
        h, b = r["holdout"], r["holdout_benchmark"]
        print(f"{nm:18} {h['n']:5} {h['wr']:6.1f} {h['exp_r']:+7.3f} "
              f"{str(h['pf']):>5} {h['ret']:+8.1f} {h['dd']:6.1f} "
              f"{'x'+str(r['rec_lev']):>6} {b['exp_r']:+9.3f} "
              f"{r['edge_exp_r']:+7.3f}  "
              f"{'ПРОШЁЛ' if r['passed'] else 'нет'}"
              f"{' (осторожно)' if r['caution'] else ''}")
    n_ok = sum(1 for r in results.values() if r["passed"])
    print(f"\nпрошли честный экзамен: {n_ok} из {len(results)}")
    print(f"итоги в {OUT} | всего {time.time()-t_all:.0f}с"
          + ("  (SMOKE — черновик!)" if SMOKE else ""))


if __name__ == "__main__":
    main()
