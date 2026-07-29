# -*- coding: utf-8 -*-
"""v10: подвижная сетка набора позиции + подвижные стоп и тейк.

Чем эта волна отличается от предыдущих. В v4–v8 эволюция крутила ВЕСЬ геном
сразу, и выигрыш нельзя было приписать конкретному механизму. Здесь боевой
конфиг монеты ЗАМОРОЖЕН, ищутся только новые гены сетки и SL/TP. Тогда ответ
на вопрос «стала ли сетка лучше» получается чистым: всё остальное совпадает.

Новые гены (OFF-значения = gridlib.OFF10 = нынешнее поведение):
  grid_mode    0 шаг в % цены (как сейчас) | 1 раскладка по пути до стопа
  grid_span    при mode=1: доля пути «вход->стоп» под последнее колено
  grid_spread  1.0 равномерно | <1 колена гуще у входа | >1 гуще у стопа
  grid_atr_k   0 шаг не зависит от волатильности | 1 полностью следует за ней
  grid_retune  0 лимитки стоят | 1 переставлять только дальше | 2 свободно
  tp_atr_k     0 тейк фиксирован | >0 тейк растёт с волатильностью
  trail_k      0 трейлинга нет | >0 доля пройденного хода, которую фиксируем
  trail_start  с какой доли пути до тейка включается трейлинг

Почему стартовая популяция обязательно содержит OFF10: тогда GA физически не
может вернуть на train результат хуже нынешнего — нынешний всегда в наборе.

ПРАВИЛО ПРИНЯТИЯ (жёстче обычного, потому что пользователь потребовал явно
«не убыточнее текущей»). Кандидат принимается, только если ОДНОВРЕМЕННО:
  1) средний OOS выше базы более чем на MIN_EDGE (не шум);
  2) ни один из трёх экзаменов не просел ниже базы больше чем на FOLD_TOL;
  3) итог за все 3.2 года на боевом плече не ниже базового;
  4) просадка не выросла больше чем на DD_TOL.
Провал любого пункта -> монета остаётся на прежней сетке, и это честно
записывается в evolution10_winners.json.

Запуск: python evolution10.py            (все монеты)
        python evolution10.py BTCUSDT    (одна монета)
"""

import json
import random
import statistics
import sys
import time

import config
import evolution as ev
import evolution2 as e2
import evolution4 as e4
import evolution7 as e7
import evolution8 as e8
import ext_data as xd
import gridlib

random.seed(1010)

DAYS = 1150
POP, GENS, ELITE = 40, 20, 5
LEVS = [5, 8, 10, 12, 15]
DD_CAP = 0.20

MIN_EDGE = 0.50     # насколько средний OOS должен обойти базу
FOLD_TOL = 0.25     # допустимая просадка отдельного экзамена
DD_TOL = 1.05       # просадка не более чем на 5% относительно базовой
MIN_LOSS_SHARE = 1.0  # %: конфиг, который почти не теряет, — сигнал не о
                      # гениальности, а о дыре в движке (урок волны v8)

GENES10 = {
    "grid_mode":   (0, 1, True),
    "grid_span":   (0.30, 0.95, False),
    "grid_spread": (0.60, 1.60, False),
    "grid_atr_k":  (0.0, 1.0, False),
    "grid_w_atr_k": (-1.0, 1.0, False),
    "grid_retune": (0, 2, True),
    "tp_atr_k":    (0.0, 1.0, False),
    "trail_k":     (0.0, 0.90, False),
    "trail_start": (0.25, 0.90, False),
}


def base_genome(sym):
    """Боевой финальный конфиг монеты в генах движка + выключённая адаптация."""
    g = e7.cfg_to_genome(config.SYMBOL_PARAMS[sym]["final"], "final")
    for k, v in e8.OFF8.items():
        g.setdefault(k, v)
    g.update(gridlib.OFF10)
    return g


def with_genes(base, sub):
    """Замороженный геном + значения новых генов."""
    g = dict(base)
    g.update(sub)
    return g


def clamp10(sub):
    out = {}
    for k, (lo, hi, is_int) in GENES10.items():
        v = min(hi, max(lo, sub.get(k, gridlib.OFF10[k])))
        out[k] = int(round(v)) if is_int else v
    return out


def rand10():
    return clamp10({k: random.uniform(lo, hi)
                    for k, (lo, hi, _) in GENES10.items()})


def mutate10(sub):
    out = dict(sub)
    for k, (lo, hi, _) in GENES10.items():
        if random.random() < 0.30:
            out[k] = out[k] + random.gauss(0, 0.18 * (hi - lo))
    return clamp10(out)


def cross10(a, b):
    return clamp10({k: (a[k] if random.random() < 0.5 else b[k])
                    for k in GENES10})


def slice_aux(aux, b, e):
    """Нарезка aux под сегмент — та же логика, что в evolution4."""
    def cut(v):
        if isinstance(v, tuple):
            return tuple(cut(x) for x in v)
        if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
            return [cut(x) for x in v]
        return v[b:e]
    return {k: cut(v) for k, v in aux.items()}


def evolve(base, candles, pre, aux, prefix, lev):
    cache = {}

    def score(sub):
        key = tuple(round(sub[k], 4) if not GENES10[k][2] else sub[k]
                    for k in GENES10)
        if key not in cache:
            g = with_genes(base, sub)
            old, e2.LEV = e2.LEV, lev      # та же шкала, что у экзаменов и денег
            try:
                r = e2.run5(candles, pre, g,
                            entry_filter=e8.make_filter8(g, aux))
            finally:
                e2.LEV = old
            cache[key] = e2.fitness(r)
        return cache[key]

    # OFF10 в стартовой популяции — гарантия, что нынешнее поведение всегда
    # участвует в отборе и GA не может «потерять» его случайно
    pop = [clamp10(dict(gridlib.OFF10)),
           clamp10(dict(gridlib.OFF10, grid_mode=1)),
           clamp10(dict(gridlib.OFF10, trail_k=0.5, trail_start=0.5)),
           clamp10(dict(gridlib.OFF10, grid_atr_k=0.5))]
    while len(pop) < POP:
        pop.append(rand10())
    scored = sorted(((score(s), s) for s in pop), key=lambda x: -x[0])
    for gen in range(GENS):
        new = [s for _, s in scored[:ELITE]]
        while len(new) < POP:
            if random.random() < 0.3:
                new.append(mutate10(scored[random.randrange(ELITE)][1]))
            else:
                a = max(random.sample(scored, 3), key=lambda x: x[0])[1]
                b = max(random.sample(scored, 3), key=lambda x: x[0])[1]
                new.append(mutate10(cross10(a, b)))
        scored = sorted(((score(s), s) for s in new), key=lambda x: -x[0])
        if (gen + 1) % 5 == 0:
            print(f"  {prefix} поколение {gen+1:2}: best {scored[0][0]:+.3f}",
                  flush=True)
    return scored


def full_run(base, sub, candles, pre, aux, lev):
    g = with_genes(base, sub)
    old, e2.LEV = e2.LEV, lev
    try:
        r = e2.run5(candles, pre, g, entry_filter=e8.make_filter8(g, aux))
    finally:
        e2.LEV = old
    st = e2.stats(r)
    # доля убыточных сделок — та самая диагностика, которой в v8 был пойман
    # мираж на 4ч: конфиг, который «почти никогда не теряет», не гениален,
    # а упирается в дыру движка. Ниже она же участвует в правиле принятия.
    loss_share = ((r["trades"] - r["wins"]) / r["trades"]) if r["trades"] else 0
    return dict(ret=round((r["balance"] / e2.START - 1) * 100, 2),
                dd=round(r["max_dd"] * 100, 2), trades=r["trades"],
                wr=round(r["wins"] / r["trades"] * 100, 1) if r["trades"] else 0,
                loss_share=round(loss_share * 100, 2),
                med=round(st["med"], 3), ruined=r["ruined"])


def ladder(base, sub, candles, pre, aux):
    out = []
    for lev in LEVS:
        row = full_run(base, sub, candles, pre, aux, lev)
        row["lev"] = lev
        out.append(row)
    return out


def pick_lev(rows):
    best = rows[0]["lev"]
    for row in rows:
        if row["dd"] <= DD_CAP * 100 and not row["ruined"]:
            best = row["lev"]
    return best


def run_symbol(sym, aux_builder):
    print(f"\n================ {sym} (v10, подвижная сетка) ================",
          flush=True)
    candles = ev.fetch(sym, "15", DAYS)
    aux = aux_builder(sym, candles)
    pre_full = e2.prep(candles)
    base = base_genome(sym)
    lev = config.SYMBOL_PARAMS[sym]["final"]["lev"]
    folds = e4.fold_bounds_3y(len(candles))

    oos = []
    for (a, b, e) in folds:
        seg = candles[b:e]
        oos.append((seg, e2.prep(seg), slice_aux(aux, b, e)))

    def agg(sub):
        """Три экзамена OOS — НА БОЕВОМ ПЛЕЧЕ монеты.

        Прежние волны считали экзамены на плече по умолчанию (x5), а итог —
        на боевом. Для BTC (x15) из-за этого шкала экзамена оказывалась втрое
        мельче денежной: OOS-оценки выходили порядка 0.0-0.1, и порог MIN_EDGE
        был для них недостижим в принципе. Решение о принятии обязано
        приниматься на той же шкале, на которой считаются деньги.
        """
        g = with_genes(base, sub)
        old, e2.LEV = e2.LEV, lev
        try:
            sc = [e2.oos_score(e2.run5(seg, pre_s, g,
                                       entry_filter=e8.make_filter8(g, aux_s)))
                  for seg, pre_s, aux_s in oos]
        finally:
            e2.LEV = old
        return sum(sc) / len(sc), sc

    off = clamp10(dict(gridlib.OFF10))
    base_mean, base_sc = agg(off)
    base_full = full_run(base, off, candles, pre_full, aux, lev)
    print(f"БАЗА (нынешняя сетка): OOS {base_mean:+.3f} "
          f"{['%+.3f' % s for s in base_sc]} | 3.2г x{lev} {base_full['ret']:+.2f}% "
          f"| DD {base_full['dd']:.2f}% | сделок {base_full['trades']} "
          f"| убыточных {base_full['loss_share']:.2f}%", flush=True)

    cands = [off]
    for fi, (a, b, e) in enumerate(folds):
        train = candles[a:b]
        scored = evolve(base, train, e2.prep(train), slice_aux(aux, a, b),
                        f"{sym[:3]}-f{fi+1}", lev)
        seen = set()
        for _, sub in scored:
            key = tuple(sub[k] for k in GENES10)
            if key in seen:
                continue
            seen.add(key)
            cands.append(sub)
            if len(seen) == 3:
                break

    best = None
    for sub in cands:
        m, sc = agg(sub)
        if best is None or m > best[0]:
            best = (m, sc, sub)
    mean, sc, sub = best
    cand_full = full_run(base, sub, candles, pre_full, aux, lev)

    reasons = []
    if mean <= base_mean + MIN_EDGE:
        reasons.append(f"отрыв по OOS {mean-base_mean:+.3f} <= {MIN_EDGE}")
    if any(c < b_ - FOLD_TOL for c, b_ in zip(sc, base_sc)):
        reasons.append("один из экзаменов просел ниже базы")
    if cand_full["ret"] < base_full["ret"]:
        reasons.append(f"итог 3.2г {cand_full['ret']:+.2f}% < базового "
                       f"{base_full['ret']:+.2f}%")
    if cand_full["dd"] > base_full["dd"] * DD_TOL:
        reasons.append(f"просадка {cand_full['dd']:.2f}% > "
                       f"{base_full['dd']*DD_TOL:.2f}%")
    if cand_full["loss_share"] < MIN_LOSS_SHARE:
        reasons.append(
            f"подозрительно мало убыточных сделок ({cand_full['loss_share']:.2f}% "
            f"< {MIN_LOSS_SHARE}%) — так выглядит не гениальный конфиг, "
            f"а дыра в движке")
    adopt = not reasons

    print(f"ЛУЧШИЙ КАНДИДАТ: OOS {mean:+.3f} {['%+.3f' % s for s in sc]} | "
          f"3.2г x{lev} {cand_full['ret']:+.2f}% | DD {cand_full['dd']:.2f}% | "
          f"сделок {cand_full['trades']} "
          f"| убыточных {cand_full['loss_share']:.2f}%", flush=True)
    print(f"  гены: { {k: (round(v, 4) if isinstance(v, float) else v) for k, v in sub.items()} }")
    print(f"  ПРИНЯТ: {'ДА' if adopt else 'нет — ' + '; '.join(reasons)}",
          flush=True)

    chosen = sub if adopt else off
    lad = ladder(base, chosen, candles, pre_full, aux)
    rec_lev = pick_lev(lad)
    print(f"  лестница плечей: "
          f"{[(r['lev'], r['ret'], r['dd']) for r in lad]} -> x{rec_lev}",
          flush=True)

    return dict(genes=sub, adopt=adopt, reject_reasons=reasons,
                base_oos=round(base_mean, 4), cand_oos=round(mean, 4),
                base_folds=[round(x, 4) for x in base_sc],
                cand_folds=[round(x, 4) for x in sc],
                base_full=base_full, cand_full=cand_full,
                lev=lev, ladder=lad, rec_lev=rec_lev, chosen=chosen)


def main():
    syms = sys.argv[1:] or list(config.SYMBOL_PARAMS)
    pct5 = xd.fetch_daily_pct5()
    aux_builder = e8.make_aux_builder(pct5, 96)
    t0 = time.time()
    out = {}
    for sym in syms:
        out[sym] = run_symbol(sym, aux_builder)
    with open("evolution10_winners.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2, default=float)

    print(f"\n\n=== ИТОГ v10 (за {time.time()-t0:.0f}с) ===")
    for sym, r in out.items():
        mark = "ПРИНЯТА" if r["adopt"] else "отклонена"
        print(f"{sym:<9} {mark:<10} база {r['base_full']['ret']:+8.2f}% -> "
              f"кандидат {r['cand_full']['ret']:+8.2f}% | "
              f"DD {r['base_full']['dd']:.1f}% -> {r['cand_full']['dd']:.1f}%")
        if not r["adopt"]:
            print(f"          причина: {'; '.join(r['reject_reasons'])}")
    print("\nИтоги в evolution10_winners.json")


if __name__ == "__main__":
    main()
