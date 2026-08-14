# -*- coding: utf-8 -*-
"""v9: генетический отбор параметров 6 сигнальных сетапов BTC
(signal_engine.py). Walk-forward, затем лестница плечей x10/x15/x20 на полных
3.2 годах.

ПРОТОКОЛ (правка 08.2026, второй круг). Было «walk-forward из 3 экзаменов»: на
деле кандидат оценивался на всех трёх OOS-окнах, включая лежащие внутри его
обучения, и среднее по ним служило и выбором победителя, и заявленным
результатом. Теперь победитель выбирается по окну валидации (первое окно после
его обучения), а печатается и записывается балл отдельного экзаменационного
окна (e4.choose_winner). Кандидаты последнего фолда выбывают — экзаменовать их
нечем, кроме будущего.

Плечо здесь по-прежнему рекомендуется по лестнице на ПОЛНОЙ истории, то есть
по окну, которое считается экзаменационным. Это известная незакрытая дыра
(сигнальные сетапы живут отдельно от ботовых волн, где выбор идёт по обучающей
части); пока она не закрыта, rec_lev из этого файла — справка, а не
рекомендация к запуску.

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
        base_key = tuple(se.DEFAULTS[k] for k in GENES9)
        for fi, (a, b, e_) in enumerate(folds):
            scored = evolve_setup(setup, c4, ctx, c15, ts15, (a, b),
                                  f"{setup[:10]}-f{fi+1}")
            seen = set()
            for f_, g in scored:
                key = tuple(g[k] for k in GENES9)
                # дефолт — точка отсчёта, а не соперник; фолд запоминается
                # вместе с геномом, иначе не узнать, какие окна для этого
                # кандидата уже «просмотрены» обучением
                if key == base_key or key in seen:
                    continue
                seen.add(key)
                candidates.append((fi, g))
                if len(seen) == 3:
                    break

        def score_on(g, wi):
            """Балл сетапа на ОДНОМ OOS-окне.

            Здесь был свой протекающий харнесс — agg(g) по всем трём окнам с
            argmax по среднему. Фолды anchored, поэтому для кандидата третьего
            фолда два «экзамена» из трёх лежали внутри его обучения, и то же
            среднее решало, что печатать как результат волны. v9 общий харнесс
            e4.run_version не использует, так что правка e4 её не чинила —
            цикл разведён здесь (правка второго круга, 08.2026).
            """
            a, b, e_ = folds[wi]
            r = se.run_setup(setup, g, c4, ctx, c15, ts15, GA_LEV,
                             signal_range=(b, e_))
            return se.oos_score(r)

        # база = дефолтные параметры сетапа (тот же геном, которым засеяна
        # популяция): без неё не с чем сравнивать отрывы на разных окнах
        base_g = dict(se.DEFAULTS)
        base_sc = [score_on(base_g, wi) for wi in range(len(folds))]
        print(f"БАЗА {setup}: OOS по окнам {['%+.2f' % s for s in base_sc]} "
              f"(экзамен = окно {e4.exam_window_index(len(folds))+1}: "
              f"{base_sc[-1]:+.2f})")

        pick = e4.choose_winner(candidates, base_sc, score_on)
        g_win = pick["genome"]
        m = pick["exam_score"]
        # честные окна кандидата: валидационное и все последующие
        sc = [score_on(g_win, wi) for wi in range(pick["train_fold"],
                                                  len(folds))]
        print(f"ЛУЧШИЙ {setup}: экзамен {m:+.2f} против базы "
              f"{pick['base_exam']:+.2f} | честные окна кандидата "
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

        # oos_mean больше НЕ среднее трёх окон: это балл экзаменационного окна
        # (в json волны 08.2026 под этим именем лежало среднее — поля protocol
        # и base_folds не дают перепутать их при чтении рядом)
        results[setup] = dict(genome=g_win, oos_mean=m, oos_folds=sc,
                              protocol=("leaky-3window-mean" if pick["leaky"]
                                        else "honest-val-then-exam"),
                              base_folds=base_sc, base_exam=pick["base_exam"],
                              val_window=pick["val_window"],
                              val_score=pick["val_score"],
                              val_edge=pick["val_edge"],
                              exam_window=pick["exam_window"],
                              train_fold=pick["train_fold"],
                              skipped_candidates=pick["skipped"],
                              ladder=ladder, rec_lev=rec, caution=caution)

    with open("evolution9_winners.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2, default=float)
    print("\nИтоги в evolution9_winners.json")


if __name__ == "__main__":
    main()
