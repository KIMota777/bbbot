# -*- coding: utf-8 -*-
"""v13: ЧЕСТНЫЙ отбор политики часового мета-бота (router_1h).

ВОПРОС, НА КОТОРЫЙ ОТВЕЧАЕТ ЭТА ВОЛНА: даёт ли объединение четырёх сетапов
на часовике преимущество над ЛУЧШИМ ОДИНОЧНЫМ сетапом? Ответ должен быть
получен на данных, которых не видели ни GA, ни отбор, — иначе это не ответ.

МЕТОДОЛОГИЯ — дословно evolution11/evolution12 (вложенная валидация):
  [0 .. HOLD)  — обучение И выбор политики; внутри anchored фолды
                 (трейн [0,b) -> внутренний OOS [b,e));
  [HOLD .. n)  — HOLDOUT (28% истории): не видят ни GA, ни отбор, только
                 финальная оценка. Границы и фолды СОВПАДАЮТ с evolution12,
                 поэтому цифры роутера и одиночных сетапов сравнимы.
  Победитель — по exp_r ОБЪЕДИНЁННЫХ сделок внутренних OOS (при n>=10).

ЧТО ИМЕННО ПОДБИРАЕТСЯ: только 6 генов политики (router_1h.ROUTER_GENES).
Геномы четырёх сетапов взяты готовыми из evolution12_winners.json (@60) и
НЕ переоптимизируются: иначе к 6 генам политики добавилось бы 4x33 гена
сетапов, и любой «выигрыш» объяснялся бы подгонкой.

БЕНЧМАРКИ на том же holdout (без них вердикт бессмысленен):
  1) каждый одиночный сетап @60 своим геномом (движком, без роутера);
  2) «наивный роутер» — все 4 сетапа без политики (первый сработавший);
  3) «необученная политика» — router_1h.DEFAULT_POLICY.
ВЕРДИКТ passed: n>=8, exp_r>0, PF>=1.2, sum_r>0 И edge>0 против ЛУЧШЕГО из
бенчмарков (лучший считается среди тех, у кого на holdout >=8 сделок:
сравнивать expectancy с выборкой в 4 сделки — самообман).

ЧЕСТНАЯ ОГОВОРКА, которую нельзя опускать при чтении результата: геномы
сетапов evolution12 отбирались на ВСЕЙ обучающей части [0..hold), включая
внутренние OOS-отрезки, по которым здесь ранжируется политика. Holdout это
не пачкает (его не видел никто), но внутренние цифры оптимистичны.

Запуск полный:  python evolution13.py
Smoke-прогон:   GA_POP=6 GA_GENS=2 python evolution13.py
Переменные: GA_POP, GA_GENS, GA_ELITE, GA_SEED, GA_HOLD, GA_DAYS, GA_OUT,
            GA_LEV.
"""

import json
import os
import random
import sys
import time

import evolution as ev
import evolution4 as e4
import router_1h as rt
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


# POP/GENS/ELITE — ровно как в evolution12 (методология не должна отличаться
# ничем, кроме предмета отбора). Пространство здесь мелкое (6 генов), GA
# сходится задолго до 24-го поколения, но урезать его «потому что и так
# видно» нельзя: это уже подгонка процедуры под ожидаемый ответ.
POP_DEF, GENS_DEF = 56, 24
POP = max(4, _env_int("GA_POP", POP_DEF))
GENS = max(1, _env_int("GA_GENS", GENS_DEF))
ELITE = max(1, min(_env_int("GA_ELITE", 7), POP - 1))
SEED = _env_int("GA_SEED", 47)
DAYS = _env_int("GA_DAYS", 1150)
OUT = os.environ.get("GA_OUT") or "evolution13_winner.json"
HOLD_FRAC = float(os.environ.get("GA_HOLD", "0.28"))
GA_LEV = _env_int("GA_LEV", 15)    # плечо отбора (как в evolution12)

LEVS = [10, 15, 20]
CAND_PER_FOLD = 5
MIN_HOLD_TRADES = 8
MIN_INNER_TRADES = 10
INNER_FOLDS = 3
FIT_MIN = se2.MIN_TRADES_FIT
TOURN = 3
TF = rt.TF

SMOKE = (POP, GENS) != (POP_DEF, GENS_DEF)


def inner_folds(hold_start):
    """anchored фолды ВНУТРИ обучающей части (дословно evolution12)."""
    step = hold_start // (INNER_FOLDS + 1)
    out = []
    for k in range(INNER_FOLDS):
        b = step * (k + 2)
        e_ = min(hold_start, b + step)
        if e_ - b > 100:
            out.append((0, b, e_))
    return out


# ------------------------------------------------------------ прогоны с кэшем
class Runner:
    """Прогоны роутера с кэшем по (политика, плечо, отрезок).

    Предрасчёт ворот (prep_signals) делается ОДИН раз на всю историю: ворота
    от политики не зависят, поэтому GA переигрывает готовые сигналы —
    полный прогон политики стоит миллисекунды, а не секунды."""

    def __init__(self, genomes, c1h, ctx, c15, ts15):
        self.genomes, self.c1h, self.ctx = genomes, c1h, ctx
        self.c15, self.ts15 = c15, ts15
        t0 = time.time()
        self.pre = rt.prep_signals(genomes, c1h, ctx, interval_min=TF,
                                   verbose=True)
        self.prep_s = time.time() - t0
        self.memo = {}
        self.runs = 0

    def run(self, policy, seg, lev):
        key = (rt.policy_key(policy), lev, tuple(seg))
        got = self.memo.get(key)
        if got is None:
            got = rt.run_router(self.genomes, self.c1h, self.ctx, self.c15,
                                self.ts15, lev, signal_range=tuple(seg),
                                policy=policy, collect_diag=False,
                                pre=self.pre)
            self.memo[key] = got
            self.runs += 1
        return got

    def trades(self, policy, seg, lev):
        return self.run(policy, seg, lev)["trades"]

    def pooled(self, policy, segments, lev):
        tr = []
        for seg in segments:
            tr += self.trades(policy, seg, lev)
        return rt.metrics(tr)


# ------------------------------------------------------------------------- GA
def rank_key(row):
    """Пол сделок трейна как в evolution12: сначала добравшие FIT_MIN."""
    f, n, _g = row
    ok = n >= FIT_MIN
    return (1 if ok else 0, f if ok else 0.0, n)


def seed_policies():
    """Стартовые политики GA: обе крайности (строгая и наивная) и очевидные
    гипотезы владельца по шторму — GA обязан их увидеть, а не искать наугад."""
    seeds = [dict(rt.DEFAULT_POLICY), dict(rt.NAIVE_POLICY)]
    seeds.append(dict(rt.DEFAULT_POLICY, storm_policy=0))   # в шторм не торгуем
    seeds.append(dict(rt.DEFAULT_POLICY, storm_policy=1))   # только по шторму
    seeds.append(dict(rt.DEFAULT_POLICY, allow_counter=1, counter_min=0.5))
    seeds.append(dict(rt.NAIVE_POLICY, priority_mode=2, router_cooldown=6))
    return seeds


def evolve_policy(runner, fold, prefix):
    """GA по генам политики на трейне фолда -> [(фитнес, сделок, политика)]."""
    rand_g, clamp, mutate, cross = e4.ga_tools(rt.ROUTER_GENES)
    a, b = fold
    cache = {}
    t0 = time.time()

    def eval_g(g):
        key = rt.policy_key(g)
        got = cache.get(key)
        if got is None:
            r = runner.run(g, (a, b), GA_LEV)
            got = (se2.fitness(r), len(r["trades"]))
            cache[key] = got
        return got

    def score(pop):
        return sorted(((eval_g(g) + (g,)) for g in pop), key=rank_key,
                      reverse=True)

    pop = [clamp(dict(s)) for s in seed_policies()][:POP]
    while len(pop) < POP:
        pop.append(rand_g())
    scored = score(pop)
    step = max(1, GENS // 6)
    for gen in range(GENS):
        new = [g for _f, _n, g in scored[:ELITE]]
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


# ------------------------------------------------------------------- печать
def d(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def mon(bars, tf=TF):
    return bars * tf / 1440.0 / 30.44


def row(name, q, extra=""):
    print(f"{name:24} {q['n']:5} {q['wr']:6.1f} {q['exp_r']:+7.3f} "
          f"{str(q['pf']):>6} {q['sum_r']:+8.2f} {q['ret']:+8.1f} "
          f"{q['dd']:6.1f}  {extra}")


def head():
    print(f"{'кто':24} {'сдел':>5} {'WR%':>6} {'exp R':>7} {'PF':>6} "
          f"{'сумма R':>8} {'итог%':>8} {'DD%':>6}")


def by_setup_line(r):
    tb = r["trades_by_setup"]
    rs = {}
    for t in r["trades"]:
        rs[t["setup"]] = rs.get(t["setup"], 0.0) + t["r"]
    return " | ".join(f"{s}: {tb.get(s,0)} сд. {rs.get(s,0.0):+.2f}R"
                      for s in rt.SETUPS4)


# -------------------------------------------------------------------- main
def main():
    t_all = time.time()
    random.seed(SEED)
    print("=" * 78)
    print("evolution13: честный отбор ПОЛИТИКИ часового мета-бота router_1h")
    print("(вложенная валидация: holdout не видят ни GA, ни выбор политики)")
    print("=" * 78)
    c1h = ev.fetch("BTCUSDT", str(TF), DAYS)
    c15 = ev.fetch("BTCUSDT", "15", DAYS)
    ts15 = [c[0] for c in c15]
    ctx = se2.prep_context(c1h, interval_min=TF)
    n = len(c1h)
    hold = int(n * (1 - HOLD_FRAC))
    folds = inner_folds(hold)
    hold_seg = [(hold, n)]
    inner_segs = [(b, e_) for (_a, b, e_) in folds]

    print(f"BTCUSDT 1ч-баров {n} ({d(c1h[0][0])}..{d(c1h[-1][0])}), "
          f"15м {len(c15)} (до {d(c15[-1][0])})")
    print(f"обучение+отбор [0..{hold}) = {mon(hold):.1f} мес | HOLDOUT "
          f"[{hold}..{n}) = {mon(n-hold):.1f} мес "
          f"({d(c1h[hold][0])}..{d(c1h[-1][0])})")
    reg_h = ctx["regime"][hold:]
    print(f"  режимы holdout: bull {reg_h.count(0)*100//len(reg_h)}%, "
          f"range {reg_h.count(1)*100//len(reg_h)}%, "
          f"bear {reg_h.count(2)*100//len(reg_h)}% | цена "
          f"{c1h[hold][4]:.0f} -> {c1h[-1][4]:.0f} "
          f"({(c1h[-1][4]/c1h[hold][4]-1)*100:+.1f}%)")
    for i, (a, b, e_) in enumerate(folds):
        print(f"  фолд {i+1}: трейн [{a}..{b}) {mon(b-a):.1f} мес -> "
              f"внутр.OOS [{b}..{e_}) {mon(e_-b):.1f} мес")
    genomes = rt.load_genomes()
    meta = rt.winners_meta()
    print("геномы сетапов — evolution12_winners.json @60, НЕ "
          "переоптимизируются (роутер подбирает только политику):")
    for s in rt.SETUPS4:
        mrow = meta.get(s, {})
        print(f"    {s:13} вердикт evolution12: "
              f"{'ПРОШЁЛ' if mrow.get('passed') else 'не прошёл'} | "
              f"внутр.OOS exp {mrow.get('inner_oos',{}).get('exp_r')}R")
    print(f"GA: POP={POP} GENS={GENS} ELITE={ELITE} сид={SEED} | отбор на "
          f"x{GA_LEV} | гены политики: {', '.join(rt.ROUTER_GENES)}")
    if SMOKE:
        print("!!! SMOKE-РЕЖИМ (POP/GENS урезаны): результат черновой, в JSON "
              "пометка smoke=true. Полный прогон: python evolution13.py")
    print(f"вывод: {OUT}")

    print("\nпредрасчёт ворот четырёх сетапов (один раз на всю историю):")
    runner = Runner(genomes, c1h, ctx, c15, ts15)
    print(f"    предрасчёт занял {runner.prep_s:.1f}с")
    # Насколько вообще есть ЧТО переключать: сколько баров дают сигнал сразу
    # у нескольких сетапов (иначе «приоритет» — ген ни о чём).
    cnt = {}
    for s in rt.SETUPS4:
        for i in runner.pre["sig"][s]:
            cnt[i] = cnt.get(i, 0) + 1
    multi = sum(1 for v in cnt.values() if v > 1)
    multi_h = sum(1 for i, v in cnt.items() if v > 1 and i >= hold)
    print(f"    баров с сигналом хотя бы одного сетапа: {len(cnt)} "
          f"(из них с двумя и более: {multi}, на holdout {multi_h}) — "
          f"одновременные сигналы редки, основную работу делает таблица "
          f"режимов и занятость, а не ген приоритета")

    # 1) GA на трейнах внутренних фолдов -> пул кандидатов
    cands = []
    for fi, (a, b, e_) in enumerate(folds):
        print(f"\n-- фолд {fi+1}: GA политики на трейне [{a}..{b}) --")
        scored = evolve_policy(runner, (a, b), f"роутер-f{fi+1}")
        seen = set()
        for fit, n_tr, g in scored:
            k = rt.policy_key(g)
            if k in seen:
                continue
            seen.add(k)
            cands.append(dict(g=g, src=fi, fit=fit, train_n=n_tr))
            if len(seen) >= CAND_PER_FOLD:
                break
    print(f"\nкандидатов: {len(cands)}")

    # 2) выбор победителя ТОЛЬКО по внутренним OOS
    ranked = []
    for cnd in cands:
        p = runner.pooled(cnd["g"], inner_segs, GA_LEV)
        ranked.append((p["exp_r"] if p["n"] >= MIN_INNER_TRADES else -99,
                       p["n"], p, cnd))
    ranked.sort(key=lambda x: (-x[0], -x[1]))
    _sc, _n, inner_p, best = ranked[0]
    pol = rt.clamp_policy(best["g"])
    print(f"ВЫБРАНА политика (фолд {best['src']+1}, фитнес трейна "
          f"{best['fit']:+.2f} на {best['train_n']} сделках): внутр.OOS "
          f"{inner_p['n']} сделок, exp {inner_p['exp_r']:+.3f}R, "
          f"PF {inner_p['pf']}, WR {inner_p['wr']}%")
    print(f"  {rt.describe_policy(pol)}")
    print("  гены: " + ", ".join(f"{k}={pol[k]}" for k in rt.ROUTER_GENES))
    if inner_p["n"] < MIN_INNER_TRADES:
        print(f"  внимание: меньше {MIN_INNER_TRADES} сделок на внутренних "
              f"OOS — выбор фактически по числу сделок")

    # 3) ЧЕСТНЫЙ ЭКЗАМЕН на holdout + все бенчмарки
    r_hold = runner.run(pol, (hold, n), GA_LEV)
    h = rt.metrics(r_hold["trades"])
    print("\n" + "=" * 78)
    print("HOLDOUT (эти данные не видели ни GA, ни отбор политики)")
    print("=" * 78)
    head()
    row("РОУТЕР (отобран)", h)
    bench = {}
    solo_names = []
    for s in rt.SETUPS4:
        tr = rt.solo_trades(s, genomes[s], c1h, ctx, c15, ts15, GA_LEV,
                            (hold, n))
        bench[f"одиночный {s}"] = rt.metrics(tr)
        solo_names.append(f"одиночный {s}")
    r_naive = runner.run(rt.NAIVE_POLICY, (hold, n), GA_LEV)
    bench["наивный роутер"] = rt.metrics(r_naive["trades"])
    r_def = runner.run(rt.DEFAULT_POLICY, (hold, n), GA_LEV)
    bench["необученная политика"] = rt.metrics(r_def["trades"])
    for nm, q in bench.items():
        row(nm, q)

    # лучший бенчмарк — среди тех, у кого выборка не игрушечная
    live = {nm: q for nm, q in bench.items() if q["n"] >= MIN_HOLD_TRADES}
    pool = live or bench
    best_nm = max(pool, key=lambda k: pool[k]["exp_r"])
    best_q = pool[best_nm]
    edge = round(h["exp_r"] - best_q["exp_r"], 3)
    print(f"\nлучший бенчмарк (из тех, у кого >={MIN_HOLD_TRADES} сделок): "
          f"{best_nm} — exp {best_q['exp_r']:+.3f}R на {best_q['n']} сделках")
    print(f"ПРЕИМУЩЕСТВО РОУТЕРА: {edge:+.3f}R/сделку "
          f"({'есть' if edge > 0 else 'НЕТ'})")
    if live and len(live) < len(bench):
        skip = [f"{nm} ({bench[nm]['n']} сд.)" for nm in bench
                if nm not in live]
        print(f"  (в сравнение не взяты — мало сделок: {', '.join(skip)})")
    print(f"состав сделок роутера: {by_setup_line(r_hold)}")
    print(f"переключений активного сетапа: {r_hold['switches']} | сигналов "
          f"{r_hold['signals']} (занят {r_hold['blocked_busy']}, кулдаун "
          f"{r_hold['blocked_cooldown']}, политика {r_hold['blocked_policy']}, "
          f"шторм {r_hold['blocked_storm']})")

    # 4) лестница плечей — на holdout
    ladder = []
    print("\nлестница плечей на holdout:")
    for lev in LEVS:
        pl = rt.metrics(runner.trades(pol, (hold, n), lev))
        ladder.append(dict(lev=lev, ret=pl["ret"], dd=pl["dd"], n=pl["n"],
                           wr=pl["wr"], exp_r=pl["exp_r"], pf=pl["pf"]))
        print(f"  x{lev:<3} итог {pl['ret']:+7.1f}% | DD {pl['dd']:5.1f}% | "
              f"{pl['n']:3} сделок | WR {pl['wr']:5.1f}% | PF {pl['pf']}")
    rec = None
    for r_ in ladder[::-1]:
        if r_["dd"] <= 25 and r_["ret"] > 0:
            rec = r_["lev"]
            break
    caution = rec is None
    if rec is None:
        rec = 10
    print(f"  -> рекомендованное плечо: x{rec}"
          + (" (осторожно: ни одно плечо не даёт DD<=25% с плюсом)"
             if caution else ""))

    # 5) вердикт
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
        why.append(f"нет преимущества над лучшим бенчмарком «{best_nm}» "
                   f"({edge:+.3f}R)")
    print(f"\nВЕРДИКТ: {'ПРОШЁЛ честный экзамен' if ok else 'НЕ ПРОШЁЛ'}"
          + ("" if ok else " — " + "; ".join(why)))
    print("ОТВЕТ НА ГЛАВНЫЙ ВОПРОС: объединение четырёх сетапов "
          + ("ДАЁТ преимущество над лучшим одиночным сетапом на holdout."
             if edge > 0 and ok else
             "НЕ даёт подтверждённого преимущества над лучшим одиночным "
             "сетапом на holdout."))

    out = dict(
        policy_genome=pol, policy_text=rt.describe_policy(pol),
        holdout=h, benchmarks=bench, best_benchmark=best_nm, edge=edge,
        passed=bool(ok), fail_reasons=why, ladder=ladder, rec_lev=rec,
        caution=caution, trades_by_setup=r_hold["trades_by_setup"],
        r_by_setup={s: round(sum(t["r"] for t in r_hold["trades"]
                                 if t["setup"] == s), 3) for s in rt.SETUPS4},
        switches=r_hold["switches"], signals=r_hold["signals"],
        blocked=dict(busy=r_hold["blocked_busy"],
                     cooldown=r_hold["blocked_cooldown"],
                     policy=r_hold["blocked_policy"],
                     storm=r_hold["blocked_storm"]),
        inner_oos=inner_p, src_fold=best["src"] + 1,
        train_fitness=round(best["fit"], 2), train_n=best["train_n"],
        hold_start_bar=hold, n_bars=n, interval_min=TF, smoke=SMOKE,
        setup_genomes_from=rt.WINNERS_FILE,
        ga=dict(pop=POP, gens=GENS, elite=ELITE, seed=SEED, lev=GA_LEV,
                days=DAYS, hold_frac=HOLD_FRAC,
                folds=[list(f) for f in folds], runs=runner.runs),
        elapsed_s=round(time.time() - t_all, 1))
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2, default=float)
    print(f"\nитоги в {OUT} | прогонов роутера {runner.runs} | "
          f"всего {time.time()-t_all:.0f}с"
          + ("  (SMOKE — черновик!)" if SMOKE else ""))


if __name__ == "__main__":
    main()
