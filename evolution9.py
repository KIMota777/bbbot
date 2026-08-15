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

ПРИЁМКА (третий круг). Победителя гонки волна теперь не выдаёт за результат:
у него считаются отрыв от базы, сделки, слив, просадка и худший месяц на
экзаменационном окне, и в json уходит adopt с причинами отказа. До этой правки
правила приёмки у волны не было ВООБЩЕ — сетап range_long попал в победители с
экзаменом -2.09 против базы -1.71 (хуже базы), и ничто этому не мешало.
Вырожденные кандидаты (меньше se.OOS_MIN_TRADES сделок на своём окне
валидации) выбывают ещё до argmax.

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

import random

import evolution as ev
import evolution4 as e4
import honest_eval as he
import signal_engine as se

random.seed(47)

POP, GENS, ELITE = 48, 20, 6
LEVS = [10, 15, 20]
GA_LEV = 15  # плечо, на котором идёт отбор (пользовательский диапазон 15-20)

# --- ПРАВИЛО ПРИЁМКИ (третий круг, 08.2026) ---------------------------------
# До сих пор у этой волны его не было вообще: choose_winner возвращал лучшего
# кандидата, его балл печатался и писался в json — и всё. В инструментированном
# прогоне так и вышло: сетап range_long попал в победители с экзаменом -2.09
# против базы -1.71, то есть ХУЖЕ базы, и ничто этому не мешало. Пороги ниже
# взяты не с потолка — каждый уже существовал в проекте:
MIN_EDGE = e4.MIN_EDGE          # 0.5 — тот же осмысленный отрыв, что в
                                # ботовых волнах: меньше — шум перебора
MIN_EXAM_TRADES = se.OOS_MIN_TRADES   # 5 — порог самого движка сигналов:
                                # «меньше сделок в окне — окно не экзамен,
                                # а прогул» (signal_engine.oos_score)
DD_CAP9 = 25.0                  # % — тот же потолок просадки, по которому эта
                                # волна и раньше рекомендовала плечо x15/x20
WORST_MONTH_MIN = he.WORST_MONTH_MIN  # -10% — порог владельца из honest_eval
# Чего здесь СОЗНАТЕЛЬНО нет и почему:
#   * ворот honest_eval целиком: они меряют сеточный движок e2 (R сделки =
#     pnl/маржа, экспозиция по барам, блочный бутстрап руина). Сигнальные
#     сетапы живут в signal_engine с фиксированным R:R 1:3 и редкими входами —
#     часть порогов там просто не имеет смысла (MIN_TPM=1 сделка в месяц
#     отсекла бы вообще все сетапы, WORST_TRADE_MIN_R=-0.60R невозможен при
#     стопе ровно в -1R). Поэтому взяты только те ворота, которые переносятся
#     честно: вырожденность по числу сделок, слив, просадка, худший месяц;
#   * плеча: rec_lev считается по ПОЛНОЙ истории и остаётся справкой, а не
#     рекомендацией (см. докстроку модуля). Экзамен и ворота считаются на
#     GA_LEV — том же плече, на котором шёл отбор.

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

        _win_cache = {}

        def _run_win(g, wi):
            """Прогон сетапа на ОДНОМ OOS-окне -> (балл, число сделок, r).

            Здесь был свой протекающий харнесс — agg(g) по всем трём окнам с
            argmax по среднему. Фолды anchored, поэтому для кандидата третьего
            фолда два «экзамена» из трёх лежали внутри его обучения, и то же
            среднее решало, что печатать как результат волны. v9 общий харнесс
            e4.run_version не использует, так что правка e4 её не чинила —
            цикл разведён здесь (правка второго круга, 08.2026).
            """
            key = (tuple(g[k] for k in GENES9), wi)
            if key not in _win_cache:
                a_, b_, e_ = folds[wi]
                r = se.run_setup(setup, g, c4, ctx, c15, ts15, GA_LEV,
                                 signal_range=(b_, e_))
                _win_cache[key] = (se.oos_score(r), len(r["trades"]), r)
            return _win_cache[key]

        def score_on(g, wi):
            return _run_win(g, wi)[0]

        def trades_on(g, wi):
            """Сделок на окне — вход для отсева вырожденных кандидатов.

            se.oos_score штрафует пустое окно (OOS_THIN_PENALTY), но штраф
            получает и кандидат, и база, а сравниваются они отрывом: сетап,
            который на своём окне валидации не сделал ни одной сделки,
            «выигрывает» ровно тем, что не торговал.
            """
            return _run_win(g, wi)[1]

        # база = дефолтные параметры сетапа (тот же геном, которым засеяна
        # популяция): без неё не с чем сравнивать отрывы на разных окнах
        base_g = dict(se.DEFAULTS)
        base_sc = [score_on(base_g, wi) for wi in range(len(folds))]
        print(f"БАЗА {setup}: OOS по окнам {['%+.2f' % s for s in base_sc]} "
              f"(экзамен = окно {e4.exam_window_index(len(folds))+1}: "
              f"{base_sc[-1]:+.2f})")

        # min_trades: порог самого движка сигналов (5), а не e4.MIN_VAL_TRADES
        # (10). Тот порог откалиброван на сеточный движок, где сделок сотни; у
        # редких сигнальных сетапов 10 сделок за полугодовое окно — уже почти
        # потолок, и общий порог выбросил бы из гонки всех, включая здоровых.
        pick = e4.choose_winner(candidates, base_sc, score_on,
                                trades_on=trades_on,
                                min_trades=MIN_EXAM_TRADES)
        g_win = pick["genome"]
        m = pick["exam_score"]
        # честные окна кандидата: валидационное и все последующие
        sc = [score_on(g_win, wi) for wi in range(pick["train_fold"],
                                                  len(folds))]
        print(f"ЛУЧШИЙ {setup}: экзамен {m:+.2f} против базы "
              f"{pick['base_exam']:+.2f} | честные окна кандидата "
              f"{['%+.2f' % s for s in sc]}")

        # --- ПРИЁМКА: победитель гонки != принятый конфиг --------------------
        # Раньше этих строк не было вовсе, и любой победитель уезжал в json как
        # результат волны — включая того, кто на экзамене ХУЖЕ базы. Меряем на
        # том же прогоне экзаменационного окна, по которому считался балл
        # (_run_win отдаёт третьим элементом сам r), и на том же плече GA_LEV.
        _, exam_n, exam_r = _run_win(g_win, pick["exam_window"])
        exam_dd = exam_r["max_dd"] * 100
        n_months = max(1, int(exam_r["months"]))
        worst_month = min([(exam_r["monthly"].get(mo, 0.0) / se.START) * 100
                           for mo in range(n_months)] or [0.0])
        edge = m - pick["base_exam"]
        reasons = []
        if edge <= MIN_EDGE:
            reasons.append(f"отрыв на экзамене {edge:+.2f} <= {MIN_EDGE}")
        if exam_n < MIN_EXAM_TRADES:
            reasons.append(f"сделок на экзамене {exam_n} < {MIN_EXAM_TRADES}: "
                           f"окно не экзамен, а прогул")
        if exam_r["ruined"]:
            reasons.append("СЧЁТ СЛИТ на экзаменационном окне")
        if exam_dd > DD_CAP9:
            reasons.append(f"просадка на экзамене {exam_dd:.1f}% > {DD_CAP9}%")
        if worst_month < WORST_MONTH_MIN:
            reasons.append(f"худший месяц экзамена {worst_month:+.1f}% < "
                           f"{WORST_MONTH_MIN:.0f}%")
        if pick["all_degenerate"]:
            reasons.append("ВСЕ кандидаты вырождены: гонку выиграл отказ от "
                           "торговли, а не сетап")
        adopt = not reasons
        print(f"  экзамен x{GA_LEV}: сделок {exam_n}, DD {exam_dd:.1f}%, "
              f"худший месяц {worst_month:+.1f}%, "
              f"слив {'ДА' if exam_r['ruined'] else 'нет'}")
        print(f"  ПРИНЯТ: {'ДА' if adopt else 'нет — ' + '; '.join(reasons)}")

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
        # oos_folds — ЧЕСТНЫЕ окна кандидата, и их не всегда три: список идёт от
        # окна обучения победителя до конца, то есть при train_fold=1 в нём два
        # элемента, при train_fold=2 — один. Потребитель, делящий сумму на 3,
        # занизит средний балл, поэтому рядом кладётся oos_folds_windows —
        # номера окон (1-based), которые в списке лежат.
        results[setup] = dict(genome=g_win, oos_mean=m, oos_folds=sc,
                              oos_folds_windows=list(
                                  range(pick["train_fold"] + 1,
                                        len(folds) + 1)),
                              # adopt/reject_reasons: до третьего круга полей
                              # не было — читающий не мог отличить победителя
                              # гонки от конфига, который эту гонку заслужил
                              adopt=adopt, reject_reasons=reasons,
                              exam_lev=GA_LEV, exam_trades=exam_n,
                              exam_dd=round(exam_dd, 1),
                              exam_worst_month=round(worst_month, 1),
                              exam_ruined=exam_r["ruined"],
                              exam_edge=round(edge, 3),
                              degenerate_candidates=pick["degenerate"],
                              all_candidates_degenerate=pick["all_degenerate"],
                              metric=pick["metric"],
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

    # save_artifact, а не открытый на запись json.dump: файл прошлой волны —
    # единственное доказательство того, как отбирались нынешние сетапы.
    # (Заодно снят отказ по NameError: import json из этого файла убран.)
    out = e4.save_artifact("evolution9_winners.json", results)
    print(f"\nИтоги в {out}")


if __name__ == "__main__":
    main()
