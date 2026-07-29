# -*- coding: utf-8 -*-
"""v9: генетический отбор параметров 6 сигнальных сетапов BTC
(signal_engine.py). Walk-forward из 3 экзаменов (как во всех волнах),
затем лестница плечей x10/x15/x20 на полных 3.2 годах.

Отличия методологии от ботовых волн:
  - НЕТ переноса в безубыток вообще (урок v8);
  - сигнал на 4ч, исполнение на 15м — грубость баров не прячет риск;
  - R:R жёстко 1:3, ширина стопа ограничена stop_cap (иначе сигнал
    пропускается — обязательное условие жизни на x15-20);
  - фитнес требует >=10 сделок на трейне (статистический пол).

Запуск: python evolution9.py
"""

import json
import random

import evolution as ev
import evolution4 as e4
import signal_engine as se

random.seed(47)

POP, GENS, ELITE = 48, 20, 6
LEVS = [10, 15, 20]
GA_LEV = 15  # плечо, на котором идёт отбор (пользовательский диапазон 15-20)

GENES9 = {
    "rsi_idx":   (0, 3, True),
    "window":    (30, 180, True),
    "zone":      (0.06, 0.30, False),
    "rsi_os":    (20, 42, True),
    "buf_atr":   (0.15, 1.2, False),
    "stop_cap":  (0.015, 0.035, False),
    "poke_atr":  (0.15, 1.2, False),
    "age":       (2, 30, True),
    "drop_frac": (0.03, 0.16, False),
    "drop_days": (1, 3, True),
    "hold_days": (4, 8, True),
    "cooldown":  (0, 30, True),
}


def evolve_setup(setup, c4, ctx, c15, ts15, fold, prefix):
    rand_g, clamp, mutate, cross = e4.ga_tools(GENES9)
    a, b = fold
    cache = {}

    def eval_g(g):
        key = tuple(round(g[k], 4) if not GENES9[k][2] else g[k] for k in GENES9)
        if key not in cache:
            r = se.run_setup(setup, g, c4, ctx, c15, ts15, GA_LEV,
                             signal_range=(a, b))
            cache[key] = se.fitness(r)
        return cache[key]

    pop = [clamp(dict(se.DEFAULTS))]
    while len(pop) < POP:
        pop.append(rand_g())
    scored = sorted(((eval_g(g), g) for g in pop), key=lambda x: -x[0])
    for gen in range(GENS):
        new = [g for _, g in scored[:ELITE]]
        while len(new) < POP:
            if random.random() < 0.3:
                new.append(mutate(scored[random.randrange(ELITE)][1]))
            else:
                p1 = max(random.sample(scored, 3), key=lambda x: x[0])[1]
                p2 = max(random.sample(scored, 3), key=lambda x: x[0])[1]
                new.append(mutate(cross(p1, p2)))
        scored = sorted(((eval_g(g), g) for g in new), key=lambda x: -x[0])
        if (gen + 1) % 5 == 0:
            print(f"  {prefix} поколение {gen+1:2}: best {scored[0][0]:+.2f}")
    return scored


def main():
    c4 = ev.fetch("BTCUSDT", "240", 1150)
    c15 = ev.fetch("BTCUSDT", "15", 1150)
    ts15 = [c[0] for c in c15]
    ctx = se.prep_context(c4)
    folds = e4.fold_bounds_3y(len(c4))
    print(f"BTC: 4ч {len(c4)} свечей, 15м {len(c15)}; фолды {folds}")

    results = {}
    for setup in se.SETUPS:
        print(f"\n================ {setup} ================")
        candidates = []
        for fi, (a, b, e_) in enumerate(folds):
            scored = evolve_setup(setup, c4, ctx, c15, ts15, (a, b),
                                  f"{setup[:10]}-f{fi+1}")
            seen = set()
            for f_, g in scored:
                key = tuple(g[k] for k in GENES9)
                if key not in seen:
                    seen.add(key)
                    candidates.append(g)
                if len(seen) == 3:
                    break

        def agg(g):
            sc = []
            for (a, b, e_) in folds:
                r = se.run_setup(setup, g, c4, ctx, c15, ts15, GA_LEV,
                                 signal_range=(b, e_))
                sc.append(se.oos_score(r))
            return sum(sc) / len(sc), sc

        best = None
        for g in candidates:
            m, sc = agg(g)
            if best is None or m > best[0]:
                best = (m, sc, g)
        m, sc, g_win = best
        print(f"ЛУЧШИЙ {setup}: средний OOS {m:+.2f} | "
              f"{['%+.2f' % s for s in sc]}")

        ladder = []
        for lev in LEVS:
            r = se.run_setup(setup, g_win, c4, ctx, c15, ts15, lev)
            st = se.stats(r)
            ret = (r["balance"] / se.START - 1) * 100
            ladder.append(dict(
                lev=lev, ret=round(ret, 1), dd=round(r["max_dd"] * 100, 1),
                n=st["n"], wr=st["wr"], avg_hold_h=st["avg_hold_h"],
                med=round(st["med"], 2), p25=round(st["p25"], 2),
                pos_share=st["pos_share"], ruined=r["ruined"]))
            print(f"  x{lev:<3} | {ret:+8.1f}% | DD {r['max_dd']*100:5.1f}% | "
                  f"{st['n']:3} сделок | WR {st['wr']:5.1f}% | "
                  f"удерж {st['avg_hold_h']:5.1f}ч"
                  f"{' СЛИВ' if r['ruined'] else ''}")

        # рекомендация плеча: максимум из 15/20 с DD<=25% и без слива;
        # если оба не проходят — x10 с пометкой "осторожно"
        rec = None
        for row in ladder[::-1]:  # 20 -> 15 -> 10
            if row["lev"] >= 15 and row["dd"] <= 25 and not row["ruined"] \
                    and row["ret"] > 0:
                rec = row["lev"]
                break
        caution = rec is None
        if rec is None:
            rec = 10
        print(f"  -> рекомендованное плечо: x{rec}"
              f"{' (осторожно: на 15-20 просадка велика)' if caution else ''}")

        results[setup] = dict(genome=g_win, oos_mean=m, oos_folds=sc,
                              ladder=ladder, rec_lev=rec, caution=caution)

    with open("evolution9_winners.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2, default=float)
    print("\nИтоги в evolution9_winners.json")


if __name__ == "__main__":
    main()
