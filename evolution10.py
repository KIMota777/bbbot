# -*- coding: utf-8 -*-
"""v10: генетический отбор параметров 6 сигнальных сетапов BTC на новом
движке signal_engine2 (геном GENES2, 24 гена), честный walk-forward из
3 экзаменов (e4.fold_bounds_3y), затем лестница плечей x10/x15/x20.

Чем это лечит болезни волны v9:
  1) РЕДКОСТЬ СДЕЛОК. В v9 фитнес допускал 10 сделок, поэтому GA спокойно
     загонял пороги в крайности (RSI<20, кулдаун 18 баров) — получалось 15
     сделок за 3.2 года. Здесь фитнес движка требует минимум 25 сделок на
     трейне (иначе -1.0) и мягко штрафует редкость множителем
     min(1, сделок_в_месяц / 1.2).
  2) ЕДИНСТВЕННЫЙ СПОСОБ СТАВИТЬ СТОП. В GENES2 есть stop_mode (4 способа),
     поэтому GA может выбрать стоп, который физически пролезает в stop_cap.
  3) ЗАПРЕТ ТОРГОВЛИ ПРОТИВ ТРЕНДА. Гены counter_rsi_shift/counter_zone_mult/
     with_rsi_shift дают режимную ЛОГИКУ (строже/мягче), а не запрет; полный
     запрет остался опцией (reg_bull/reg_range/reg_bear). Скрипт печатает
     режимную раскладку победителя — видно, ЧТО именно выбрала эволюция.
  4) "ПОВЕЗЛО НА 3 СДЕЛКАХ". На OOS-отбор наложен ЖЁСТКИЙ порог: минимум
     MIN_OOS_TRADES=12 сделок суммарно по трём экзаменам, иначе кандидат
     отбрасывается. (В движке пол малой выборки теперь FIT_FLOOR = -1000+n,
     то есть бездействие оценивается ХУЖЕ любой торговли — раньше сентинел
     -1.0 был лучше реальных оценок -5..-10 и бездействие выигрывало.)

Методология (та же, что во всех волнах, ничего не подкручено):
  - отбор идёт на плече GA_LEV=15 (рабочий диапазон владельца 15-20);
  - GA работает ТОЛЬКО на трейне фолда (signal_range=(0, b)), победители
    сравниваются ТОЛЬКО на OOS-отрезках (signal_range=(b, e));
  - фитнес/oos_score — из signal_engine2, здесь НЕ переопределяются;
  - ВАЖНО про пол 25 сделок. fitness() ниже пола возвращает FIT_FLOOR+n
    (-1000+n) — метку "статистики нет", которая заведомо хуже любой реальной
    оценки. Раньше меткой было -1.0, и она оказывалась ЛУЧШЕ честных -5..-10:
    сортировка по фитнесу тогда выбирала БЕЗДЕЙСТВИЕ (в smoke-прогоне элита
    набивалась геномами с 10-23 сделками, а семя с 54 сделками вылетало) —
    ровно та болезнь, которую владелец просил вылечить. Двухключевое
    ранжирование ниже страхует и на новом поле:
      1) геном, добравший 25 сделок, всегда выше не добравшего;
      2) внутри "добравших" — по fitness() как есть;
      3) при РАВНОМ фитнесе — по числу сделок (у p25/med много точных ничьих
         на 0.00: месяцы без закрытых сделок дают ровно ноль, и таких геномов
         десятки; при ничьей берём тот, где статистики больше, а не тот, что
         случайно раньше в популяции). У "не добравших" это же правило тянет
         популяцию к порогу 25 сделок.
    Сама функция фитнеса не тронута, меняется только порядок отбора;
  - по той же причине на OOS-отборе кандидаты со средним OOS в пределах
    EPS_OOS от лучшего считаются неразличимыми, и среди них берётся самый
    активный (больше OOS-сделок = больше доверия);
  - лестница плечей считается на ПОЛНОМ периоде (трейн+OOS) — это оценка
    риска размера позиции, а не второй экзамен; честная оценка — oos_mean.

Быстрый прогон (smoke) через переменные окружения:
  GA_POP=8 GA_GENS=2 GA_SETUPS=range_long,pump_short python evolution10.py
Полный прогон: python evolution10.py
Переменные: GA_POP, GA_GENS, GA_ELITE, GA_SETUPS, GA_SEED, GA_OUT, GA_DAYS.
"""

import json
import os
import random
import sys
import time

import evolution as ev
import evolution4 as e4
import signal_engine2 as se2

try:                      # чтобы русский вывод не падал при перенаправлении
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


POP = _env_int("GA_POP", 56)
GENS = _env_int("GA_GENS", 24)
ELITE = _env_int("GA_ELITE", 7)
SEED = _env_int("GA_SEED", 47)
DAYS = _env_int("GA_DAYS", 1150)
OUT = os.environ.get("GA_OUT") or "evolution10_winners.json"

LEVS = [10, 15, 20]
GA_LEV = 15                # плечо, на котором идёт отбор
MIN_OOS_TRADES = 12        # жёсткий пол сделок на OOS (сумма трёх экзаменов)
CAND_PER_FOLD = 5          # сколько уникальных лучших геномов брать с фолда
EPS_OOS = 0.05             # разница средних OOS, которую считаем ничьей
FIT_MIN_TRADES = 25        # пол фитнеса движка (для диагностики поколений)
TOURN = 3                  # размер турнира при выборе родителей

POP = max(4, POP)
GENS = max(1, GENS)
ELITE = max(1, min(ELITE, POP - 1))

_ENV_SETUPS = [s.strip() for s in
               os.environ.get("GA_SETUPS", "").split(",") if s.strip()]
SETUPS_RUN = _ENV_SETUPS or list(se2.SETUPS)
_bad = [s for s in SETUPS_RUN if s not in se2.SETUPS]
if _bad:
    raise SystemExit(f"GA_SETUPS: неизвестные сетапы {_bad}; "
                     f"допустимы {list(se2.SETUPS)}")

SMOKE = (POP, GENS) != (56, 24) or len(SETUPS_RUN) != len(se2.SETUPS)

SETUP_WHAT = {
    "range_long": "лонг от низа диапазона при перепроданности",
    "range_short": "шорт от верха диапазона при перекупленности",
    "sweep_long": "лонг после прокола старого минимума и возврата над ним",
    "sweep_short": "шорт после прокола старого максимума и возврата под него",
    "dump_long": "лонг после падения на drop_frac за drop_days (разворотная свеча)",
    "pump_short": "шорт после роста на drop_frac за drop_days (разворотная свеча)",
}

# какие гены реально влияют на конкретный сетап/геном (остальные - шум,
# и владельцу важно не читать смысл в неиспользуемых числах)
def gene_active(setup, g, key):
    is_sweep = setup.startswith("sweep")
    is_move = setup.split("_")[0] in ("dump", "pump")
    is_range = setup.startswith("range")
    mode = int(g["stop_mode"])
    if key in ("window", "stop_cap", "stop_mode", "hold_days", "cooldown",
               "reg_bull", "reg_range", "reg_bear", "entry_mode"):
        return True
    if key == "rsi_idx":
        return not is_sweep          # у sweep RSI не ворото (только прогрев)
    if key in ("rsi_os", "counter_rsi_shift", "with_rsi_shift"):
        return is_range or is_move
    if key in ("zone", "counter_zone_mult"):
        return is_range
    if key in ("poke_atr", "age"):
        return is_sweep
    if key in ("drop_frac", "drop_days"):
        return is_move
    if key == "buf_atr":
        return mode in (0, 1, 3)
    if key == "swing_bars":
        return mode == 1
    if key == "stop_atr_k":
        return mode == 2
    if key in ("retest_atr", "retest_bars"):
        return int(g["entry_mode"]) == 1
    return True


GENE_GROUPS = (
    ("индикаторы/зона", ("rsi_idx", "window", "zone", "rsi_os")),
    ("стоп", ("stop_mode", "swing_bars", "stop_atr_k", "buf_atr", "stop_cap")),
    ("профиль сетапа", ("poke_atr", "age", "drop_frac", "drop_days")),
    ("ведение", ("hold_days", "cooldown")),
    ("режимы", ("reg_bull", "reg_range", "reg_bear", "counter_rsi_shift",
                "counter_zone_mult", "with_rsi_shift")),
    ("вход", ("entry_mode", "retest_atr", "retest_bars")),
)


def gkey(g):
    """Ключ генома для кэша фитнеса."""
    return tuple(g[k] if se2.GENES2[k][2] else round(g[k], 4)
                 for k in se2.GENES2)


def fmt_gene(setup, g, k):
    lo, hi, is_int = se2.GENES2[k]
    v = int(g[k]) if is_int else float(g[k])
    if k == "rsi_idx":
        txt = f"{v} (RSI{se2.RSI_SET[v]})"
    elif k == "stop_mode":
        txt = f"{v} ({['за границей диапазона', 'за свингом', 'k*ATR', 'за фитилём'][v]})"
    elif k == "entry_mode":
        txt = f"{v} ({'по рынку' if v == 0 else 'лимитка на ретесте'})"
    elif k in ("reg_bull", "reg_range", "reg_bear"):
        txt = "разрешён" if v else "ЗАПРЕЩЁН"
    elif k == "stop_cap":
        txt = f"{v*100:.2f}%"
    elif k == "drop_frac":
        txt = f"{v*100:.2f}%"
    elif is_int:
        txt = str(v)
    else:
        txt = f"{v:.3f}"
    return txt + ("" if gene_active(setup, g, k) else "  [не используется]")


def sec(t0):
    return f"{time.time() - t0:.0f}с"


# ------------------------------------------------------------------- GA
def rank_key(row):
    """Ключ сортировки популяции: сначала прошедшие пол 25 сделок (по фитнесу),
    затем не прошедшие (по числу сделок). См. пояснение в шапке модуля."""
    f, n, _g = row
    return (1 if n >= FIT_MIN_TRADES else 0, f if n >= FIT_MIN_TRADES else 0.0,
            n)


def evolve_setup(setup, data, fold, prefix):
    """GA на трейне фолда. Возвращает список (фитнес, сделок, геном),
    отсортированный по rank_key (лучшие первыми)."""
    c4, ctx, c15, ts15 = data
    rand_g, clamp, mutate, cross = e4.ga_tools(se2.GENES2)
    a, b = fold
    cache = {}
    t0 = time.time()

    def eval_g(g):
        key = gkey(g)
        got = cache.get(key)
        if got is None:
            r = se2.run_setup(setup, g, c4, ctx, c15, ts15, GA_LEV,
                              signal_range=(a, b), collect_diag=False)
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
    alt["entry_mode"] = 1              # оба режима входа должны быть в семенах
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
            alive = sum(1 for f, n, _ in scored if n >= FIT_MIN_TRADES)
            mark = "" if scored[0][1] >= FIT_MIN_TRADES else "  (пол не пройден!)"
            print(f"    {prefix} поколение {gen+1:2}: фитнес {scored[0][0]:+7.2f} | "
                  f"сделок {scored[0][1]:3} | с полом {FIT_MIN_TRADES}+ сделок "
                  f"{alive:2}/{POP} | кэш {len(cache)} | {sec(t0)}{mark}")
    return scored


def oos_agg(setup, g, data, folds):
    """Оценка генома на трёх OOS-отрезках: (средний, по фолдам, сделок по
    фолдам, сделок всего)."""
    c4, ctx, c15, ts15 = data
    sc, ns = [], []
    for (_a, b, e_) in folds:
        r = se2.run_setup(setup, g, c4, ctx, c15, ts15, GA_LEV,
                          signal_range=(b, e_), collect_diag=False)
        sc.append(se2.oos_score(r))
        ns.append(len(r["trades"]))
    return sum(sc) / len(sc), sc, ns, sum(ns)


def train_fits(setup, g, data, folds):
    c4, ctx, c15, ts15 = data
    out = []
    for (a, b, _e) in folds:
        r = se2.run_setup(setup, g, c4, ctx, c15, ts15, GA_LEV,
                          signal_range=(a, b), collect_diag=False)
        out.append((round(se2.fitness(r), 2), len(r["trades"])))
    return out


# --------------------------------------------------------------- печать
def print_regime_block(setup, g):
    side = se2.setup_side(setup)
    is_long = side == "L"
    print("  РЕЖИМНАЯ РАСКЛАДКА (что эволюция выбрала про торговлю против тренда):")
    print(f"    режимы: bull {'разрешён' if g['reg_bull'] else 'ЗАПРЕЩЁН'} | "
          f"range {'разрешён' if g['reg_range'] else 'ЗАПРЕЩЁН'} | "
          f"bear {'разрешён' if g['reg_bear'] else 'ЗАПРЕЩЁН'}")
    counter = "bear" if is_long else "bull"
    withtr = "bull" if is_long else "bear"
    print(f"    против тренда (для {side} это {counter}): RSI строже на "
          f"{int(g['counter_rsi_shift'])} п., зона уже x{g['counter_zone_mult']:.2f}")
    print(f"    по тренду     (для {side} это {withtr}): RSI мягче на "
          f"{int(g['with_rsi_shift'])} п.")
    print("    итоговые пороги: " + " | ".join(
        f"{se2.REGIME_NAMES[q]}: {_thr_row(setup, q, g)['cmp']}"
        for q in (0, 1, 2)))
    print(f"    стоп: {fmt_gene(setup, g, 'stop_mode')} | вход: "
          f"{fmt_gene(setup, g, 'entry_mode')}")


def print_genome(setup, g):
    print("  ГЕНОМ-ПОБЕДИТЕЛЬ:")
    for title, keys in GENE_GROUPS:
        parts = [f"{k}={fmt_gene(setup, g, k)}" for k in keys]
        print(f"    {title:16} " + "; ".join(parts))


def _thr_row(setup, reg, g):
    """Пороги режима и в шкале движка, и в виде фактического сравнения."""
    rsi_thr, zone_thr = se2.effective_thresholds(setup, reg, g)
    is_long = se2.setup_side(setup) == "L"
    allowed = bool(se2.regime_allowed(reg, g))
    rsi_cmp = round(rsi_thr if is_long else 100 - rsi_thr, 1)
    zone_cmp = round(zone_thr if is_long else 1 - zone_thr, 3)
    if not allowed:
        txt = "режим запрещён"
    elif setup.startswith("sweep"):
        txt = f"прокол>={float(g['poke_atr']):.2f}ATR (RSI/зона не ворота)"
    elif is_long:
        txt = f"RSI<={rsi_cmp:.0f} и зона<={zone_cmp:.2f}"
    else:
        txt = f"RSI>={rsi_cmp:.0f} и зона>={zone_cmp:.2f}"
    return dict(allowed=allowed, rsi=round(rsi_thr, 1),
                zone=round(zone_thr, 3), rsi_cmp=rsi_cmp, zone_cmp=zone_cmp,
                cmp=txt)


def by_regime(trades):
    out = {}
    for reg in (0, 1, 2):
        sub = [t for t in trades if t["regime"] == reg]
        if not sub:
            out[se2.REGIME_NAMES[reg]] = dict(n=0, wr=0.0, avg_r=0.0, pnl=0.0)
            continue
        w = sum(1 for t in sub if t["pnl"] > 0)
        out[se2.REGIME_NAMES[reg]] = dict(
            n=len(sub), wr=round(w / len(sub) * 100, 1),
            avg_r=round(sum(t["r"] for t in sub) / len(sub), 3),
            pnl=round(sum(t["pnl"] for t in sub), 2))
    return out


def print_full_stats(setup, r, st):
    bars = max(1, r["bars_eval"])
    top = sorted(r["reject_counts"].items(), key=lambda x: -x[1])[:5]
    print(f"  СТАТИСТИКА полного периода на x{GA_LEV}: сигналов {r['signals']} "
          f"по режимам {{bull {r['pass_by_regime'][0]}, range {r['pass_by_regime'][1]}, "
          f"bear {r['pass_by_regime'][2]}}} | съедено занятостью "
          f"{r['blocked_busy']}, кулдауном {r['blocked_cooldown']}"
          + (f", после слива {r['blocked_ruined']}" if r["blocked_ruined"] else ""))
    print(f"    сделок {st['n']} ({st['tpm']:.2f}/мес) | WR {st['wr']}% | "
          f"итог {st['ret']:+.1f}% | DD {st['dd']}% | "
          f"PF {st['pf'] if st['pf'] is not None else '-'} | "
          f"ср.R {st['exp_r']:+.3f} | удерж {st['avg_hold_h']:.1f}ч")
    print(f"    выходы {st['reasons']} | из выбитых стопом дошло бы до тейка "
          f"позже {st['stop_then_tp']}/{st['n_stop']} | MAE ср {st['avg_mae_r']}R, "
          f"MFE ср {st['avg_mfe_r']}R, после тейка ещё "
          f"{st['avg_mfe_after_tp_r']}R")
    brg = by_regime(r["trades"])
    print("    по режимам: " + " | ".join(
        f"{k} {v['n']} сд., WR {v['wr']}%, ср.R {v['avg_r']:+.2f}"
        for k, v in brg.items()))
    print("    топ причин отказа: " + " | ".join(
        f"{k} {v} ({v/bars*100:.1f}%)" for k, v in top))
    nm = r["near_misses"]
    if nm:
        pos = sum(1 for x in nm if x["hypo_r"] > 0)
        best = max(nm, key=lambda x: x["hypo_r"])
        print(f"    near-miss {len(nm)}: плюсовых гипотетически {pos} "
              f"({pos/len(nm)*100:.0f}%), средний R {st['near_avg_r']:+.3f}; "
              f"лучший: {best['gate']} нужно {best['need']} было {best['got']} "
              f"-> R {best['hypo_r']:+.2f} ({best['hypo_reason']})")
    return brg, top


# ---------------------------------------------------------------- main
def main():
    t_all = time.time()
    random.seed(SEED)
    c4 = ev.fetch("BTCUSDT", "240", DAYS)
    c15 = ev.fetch("BTCUSDT", "15", DAYS)
    ts15 = [c[0] for c in c15]
    ctx = se2.prep_context(c4)
    data = (c4, ctx, c15, ts15)
    folds = e4.fold_bounds_3y(len(c4))
    reg = ctx["regime"]

    def d(ts):
        return time.strftime("%Y-%m-%d", time.gmtime(ts / 1000))

    def months(nbars):
        return nbars * se2.MS_4H / se2.MONTH_MS

    print("=" * 78)
    print("evolution10: GA по сетапам на движке signal_engine2 (геном GENES2)")
    print("=" * 78)
    print(f"данные BTCUSDT: 4ч-баров {len(c4)} ({d(c4[0][0])} .. {d(c4[-1][0])}), "
          f"15м-баров {len(c15)}")
    print(f"режимы истории: bull {reg.count(0)*100//len(reg)}%, "
          f"range {reg.count(1)*100//len(reg)}%, bear {reg.count(2)*100//len(reg)}%")
    print(f"GA: POP={POP} GENS={GENS} ELITE={ELITE} сид={SEED} | "
          f"отбор на плече x{GA_LEV} | лестница {LEVS}")
    print(f"фитнес: signal_engine2.fitness — основа exp_r*10 + ровность по "
          f"месяцам, ВЫЧИТАЕМЫЙ штраф за редкость (норма "
          f"{se2.TPM_NORM} сделок/мес), пол {FIT_MIN_TRADES} сделок на трейне "
          f"(иначе {se2.FIT_FLOOR:.0f}+n)")
    print(f"OOS-отбор: лучший средний oos_score трёх экзаменов, ЖЁСТКИЙ порог "
          f">= {MIN_OOS_TRADES} сделок суммарно на OOS")
    print("walk-forward фолды (e4.fold_bounds_3y):")
    for fi, (a, b, e_) in enumerate(folds):
        print(f"  экзамен {fi+1}: трейн бары [{a}..{b}) {months(b-a):4.1f} мес "
              f"({d(c4[a][0])}..{d(c4[b][0])}) -> OOS [{b}..{e_}) "
              f"{months(e_-b):4.1f} мес ({d(c4[b][0])}..{d(c4[min(e_,len(c4)-1)][0])})")
    print(f"сетапы в прогоне: {', '.join(SETUPS_RUN)}")
    if SMOKE:
        print("!!! SMOKE-РЕЖИМ (POP/GENS/сетапы урезаны): результат черновой, "
              "в JSON будет пометка smoke=true. Полный прогон: python evolution10.py")
    print(f"вывод: {OUT}")

    results = {}
    for si, setup in enumerate(SETUPS_RUN):
        t_s = time.time()
        print("\n" + "=" * 78)
        print(f"{setup}  ({si+1}/{len(SETUPS_RUN)})  сторона "
              f"{se2.setup_side(setup)}: {SETUP_WHAT[setup]}")
        print("=" * 78)

        g_base = e4.ga_tools(se2.GENES2)[1](dict(se2.DEFAULTS2))
        bm, bsc, bns, btot = oos_agg(setup, g_base, data, folds)
        print(f"  БАЗА (нейтральное семя DEFAULTS2): средний OOS {bm:+.2f} | "
              f"{['%+.2f' % s for s in bsc]} | OOS-сделок {btot} {tuple(bns)}")

        candidates, seen, unqual_folds = [], set(), []
        for fi, (a, b, e_) in enumerate(folds):
            print(f"  -- экзамен {fi+1}: GA на трейне [{a}..{b}) --")
            scored = evolve_setup(setup, data, (a, b), f"{setup[:11]}-f{fi+1}")
            # в кандидаты идут ТОЛЬКО геномы, прошедшие пол 25 сделок на
            # трейне: иначе на OOS можно случайно вытащить геном, который
            # фитнес вообще не оценивал (метка -1.0)
            pool = [x for x in scored if x[1] >= FIT_MIN_TRADES]
            if not pool:
                unqual_folds.append(fi + 1)
                pool = scored
                print(f"    ВНИМАНИЕ: на фолде {fi+1} ни один геном не добрал "
                      f"{FIT_MIN_TRADES} сделок — беру лучших без пола")
            taken = 0
            for f_, n_, g in pool:
                key = gkey(g)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append((g, fi + 1, f_, n_))
                taken += 1
                if taken >= CAND_PER_FOLD:
                    break

        print(f"  кандидатов на OOS-экзамены: {len(candidates)} "
              f"(до {CAND_PER_FOLD} уникальных лучших с каждого фолда, "
              f"только прошедшие пол {FIT_MIN_TRADES} сделок на трейне)")
        rows = []
        for (g, src, f_, n_) in candidates:
            m, sc, ns, tot = oos_agg(setup, g, data, folds)
            rows.append(dict(g=g, src=src, m=m, sc=sc, ns=ns, tot=tot,
                             tr_fit=f_, tr_n=n_))
        good = [x for x in rows if x["tot"] >= MIN_OOS_TRADES]
        floor_ok = bool(good)
        print(f"  порог {MIN_OOS_TRADES} OOS-сделок прошли {len(good)} из {len(rows)}")
        for x in sorted(rows, key=lambda z: (-z["m"], -z["tot"]))[:5]:
            print(f"    кандидат с фолда {x['src']}: OOS ср {x['m']:+7.2f} "
                  f"{['%+.2f' % s for s in x['sc']]} | OOS-сделок {x['tot']:3} "
                  f"{tuple(x['ns'])} | трейн {x['tr_fit']:+.2f}/{x['tr_n']} сд."
                  f"{'' if x['tot'] >= MIN_OOS_TRADES else '  <- пол не пройден'}")
        if floor_ok:
            # средние OOS в пределах EPS_OOS считаем неразличимыми (плато 0.00
            # у p25/med встречается сплошь) и берём среди них самый активный
            good.sort(key=lambda x: (-x["m"], -x["tot"]))
            tie = [x for x in good if x["m"] >= good[0]["m"] - EPS_OOS]
            tie.sort(key=lambda x: (-x["tot"], -x["m"]))
            best = tie[0]
            if len(tie) > 1:
                print(f"  ничья по среднему OOS (в пределах {EPS_OOS}): "
                      f"{len(tie)} кандидатов, беру самый активный "
                      f"({best['tot']} OOS-сделок)")
        else:
            # порог не прошёл никто: средний OOS тут нельзя сравнивать (при <3
            # сделках движок отдаёт 0.0, т.е. бездействие "лучше" убытка),
            # поэтому берём самый активный геном - только чтобы заполнить
            # конфиг, доверять ему нельзя
            rows.sort(key=lambda x: (-x["tot"], -x["m"]))
            best = rows[0]
            print(f"  !!! НИ ОДИН кандидат не набрал {MIN_OOS_TRADES} сделок на OOS. "
                  f"Беру самый активный ({best['tot']} сделок) ТОЛЬКО чтобы "
                  f"заполнить конфиг: доверять нельзя, сетап помечен "
                  f"oos_floor_ok=false, caution=true, плечо принудительно x10")
        g_win = best["g"]
        m, sc, ns, tot = best["m"], best["sc"], best["ns"], best["tot"]
        print(f"  ЛУЧШИЙ (родом с фолда {best['src']}, там фитнес "
              f"{best['tr_fit']:+.2f} на {best['tr_n']} сделках): средний OOS "
              f"{m:+.2f} | по экзаменам {['%+.2f' % s for s in sc]} | "
              f"OOS-сделок {tot} {tuple(ns)}")
        zero = [i + 1 for i, x in enumerate(ns) if x < 3]
        if zero:
            print(f"  внимание: на экзаменах {zero} сделок <3, там oos_score "
                  f"= пол {se2.FIT_FLOOR:.0f}+n (статистики нет) — средний OOS "
                  f"по такому кандидату не показателен")
        tf = train_fits(setup, g_win, data, folds)
        print("  фитнес победителя на трейнах: " + " | ".join(
            f"ф{i+1} {f:+.2f} ({n} сд.)" for i, (f, n) in enumerate(tf)))

        print_genome(setup, g_win)
        print_regime_block(setup, g_win)

        ladder, base_r, base_st = [], None, None
        print(f"  ЛЕСТНИЦА ПЛЕЧЕЙ (полный период {months(len(c4)):.1f} мес, "
              f"трейн+OOS — оценка риска, не экзамен):")
        for lev in LEVS:
            r = se2.run_setup(setup, g_win, c4, ctx, c15, ts15, lev,
                              collect_diag=True)
            st = se2.stats(r)
            ladder.append(dict(
                lev=lev, ret=st["ret"], dd=st["dd"], n=st["n"], wr=st["wr"],
                avg_hold_h=st["avg_hold_h"], med=round(st["med"], 2),
                p25=round(st["p25"], 2), pos_share=st["pos_share"],
                exp_r=st["exp_r"], pf=st["pf"], tpm=round(st["tpm"], 2),
                ruined=r["ruined"]))
            print(f"    x{lev:<3} | итог {st['ret']:+8.1f}% | DD {st['dd']:5.1f}% | "
                  f"{st['n']:3} сделок ({st['tpm']:.2f}/мес) | WR {st['wr']:5.1f}% | "
                  f"удерж {st['avg_hold_h']:6.1f}ч | ср.R {st['exp_r']:+.3f}"
                  f"{'  СЛИВ' if r['ruined'] else ''}")
            if lev == GA_LEV:
                base_r, base_st = r, st

        rec, why = None, ""
        for row in ladder[::-1]:            # 20 -> 15 -> 10
            if (row["lev"] >= 15 and row["dd"] <= 25 and not row["ruined"]
                    and row["ret"] > 0):
                rec = row["lev"]
                break
        if rec is None:
            rec, why = 10, "на x15/x20 просадка >25%, минус или слив"
        if not floor_ok:                    # доверия нет - максимум x10
            rec, why = 10, "провален пол OOS-сделок, статистики нет"
        if best["tr_n"] < FIT_MIN_TRADES:
            rec, why = 10, (f"победитель не добрал пол {FIT_MIN_TRADES} сделок "
                            f"на трейне ({best['tr_n']})")
        caution = bool(why)
        print(f"    -> рекомендованное плечо: x{rec}"
              + (f"  (ОСТОРОЖНО: {why})" if caution else ""))

        brg, top = print_full_stats(setup, base_r, base_st)

        results[setup] = dict(
            genome=g_win, oos_mean=m, oos_folds=sc, oos_fold_trades=ns,
            n_oos_trades=tot, oos_floor_ok=floor_ok,
            oos_candidates=len(rows), oos_candidates_passed=len(good),
            cand_from_fold=best["src"], cand_train_fit=best["tr_fit"],
            cand_train_n=best["tr_n"],
            train_floor_failed_folds=unqual_folds,
            base_oos_mean=bm, base_oos_folds=bsc, base_n_oos_trades=btot,
            beats_base=bool(m > bm and floor_ok),
            ladder=ladder, rec_lev=rec, caution=caution, caution_why=why,
            train_fitness=[dict(fit=f, n=n) for f, n in tf],
            regime=dict(
                reg_bull=int(g_win["reg_bull"]), reg_range=int(g_win["reg_range"]),
                reg_bear=int(g_win["reg_bear"]),
                counter_rsi_shift=int(g_win["counter_rsi_shift"]),
                counter_zone_mult=round(float(g_win["counter_zone_mult"]), 3),
                with_rsi_shift=int(g_win["with_rsi_shift"]),
                stop_mode=int(g_win["stop_mode"]),
                entry_mode=int(g_win["entry_mode"]),
                # rsi/zone - "как в движке" (шкала перепроданности), а
                # rsi_cmp/zone_cmp/cmp - как реально сравнивает gate_eval
                # (для шортов RSI>=100-rsi и зона>=1-zone)
                thresholds={se2.REGIME_NAMES[q]: _thr_row(setup, q, g_win)
                            for q in (0, 1, 2)},
                signals_by_regime={se2.REGIME_NAMES[q]: base_r["pass_by_regime"][q]
                                   for q in (0, 1, 2)},
                trades_by_regime=brg),
            full=dict(
                lev=GA_LEV, n=base_st["n"], wr=base_st["wr"], ret=base_st["ret"],
                dd=base_st["dd"], tpm=round(base_st["tpm"], 3),
                exp_r=base_st["exp_r"], pf=base_st["pf"],
                avg_hold_h=base_st["avg_hold_h"], reasons=base_st["reasons"],
                stop_then_tp=base_st["stop_then_tp"], n_stop=base_st["n_stop"],
                avg_mae_r=base_st["avg_mae_r"], avg_mfe_r=base_st["avg_mfe_r"],
                avg_mfe_after_tp_r=base_st["avg_mfe_after_tp_r"],
                signals=base_r["signals"], blocked_busy=base_r["blocked_busy"],
                blocked_cooldown=base_r["blocked_cooldown"],
                blocked_ruined=base_r["blocked_ruined"],
                bars_eval=base_r["bars_eval"],
                n_near=base_st["n_near"], near_avg_r=base_st["near_avg_r"],
                top_rejects=[[k, v] for k, v in top],
                gate_solo=base_r["gate_solo"]),
            ga=dict(pop=POP, gens=GENS, elite=ELITE, seed=SEED, lev=GA_LEV,
                    cand_per_fold=CAND_PER_FOLD,
                    min_oos_trades=MIN_OOS_TRADES, days=DAYS),
            folds=[list(f) for f in folds], smoke=SMOKE,
            elapsed_s=round(time.time() - t_s, 1),
        )
        print(f"  время сетапа: {sec(t_s)}")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2, default=float)

    print("\n" + "=" * 78)
    print("ИТОГОВАЯ ТАБЛИЦА (режимы: b=bull, r=range, e=bear разрешены; "
          "прочерк = запрещён эволюцией)")
    print(f"{'сетап':13} {'OOS ср':>8} {'OOS сд':>7} {'плечо':>6} "
          f"{'итог%':>8} {'DD%':>6} {'сдел':>5} {'WR%':>6} {'уд.ч':>7} режимы")
    for name, rec_ in results.items():
        row = next(x for x in rec_["ladder"] if x["lev"] == rec_["rec_lev"])
        rg = rec_["regime"]
        flags = "".join(["b" if rg["reg_bull"] else "-",
                         "r" if rg["reg_range"] else "-",
                         "e" if rg["reg_bear"] else "-"])
        print(f"{name:13} {rec_['oos_mean']:+8.2f} {rec_['n_oos_trades']:7} "
              f"{'x'+str(rec_['rec_lev']):>6} {row['ret']:+8.1f} {row['dd']:6.1f} "
              f"{row['n']:5} {row['wr']:6.1f} {row['avg_hold_h']:7.1f} "
              f"{flags} стоп{rg['stop_mode']} вход{rg['entry_mode']}"
              f"{' ОСТОРОЖНО' if rec_['caution'] else ''}")
    print("GA против нейтрального семени DEFAULTS2 (средний OOS): " + " | ".join(
        f"{n} {r_['oos_mean']:+.2f} vs {r_['base_oos_mean']:+.2f} "
        f"({'лучше' if r_['beats_base'] else 'НЕ лучше'})"
        for n, r_ in results.items()))
    print(f"\nитоги в {OUT} | всего {sec(t_all)}"
          + ("  (SMOKE — черновик!)" if SMOKE else ""))


if __name__ == "__main__":
    main()
