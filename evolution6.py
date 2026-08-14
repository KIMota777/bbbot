# -*- coding: utf-8 -*-
"""v6: специалист под медвежий рынок и боковики + лестница плечей.

Режим рынка (по дневным данным самой монеты, без заглядывания вперёд):
  bear  = цена < SMA100д И изменение за 30д < -5%
  bull  = цена > SMA100д И изменение за 30д > +5%
  range = всё остальное

Специалист: вход разрешён только в bear/range (в bull бот стоит в стороне).
Фитнес и экзамены считаются ТОЛЬКО по медвежьим/боковым месяцам.
База = финальные конфиги.

ПРОТОКОЛ ОТБОРА (правка 08.2026, второй круг). У этой волны свой харнесс, и
он тёк так же, как общий: кандидат оценивался на всех трёх OOS-окнах, включая
лежащие внутри его обучения, и argmax по среднему служил и выбором, и
приёмкой. Теперь победитель выбирается по окну валидации (первое окно после
его обучения), а приёмка смотрит на отдельное экзаменационное окно
(e4.choose_winner). Кандидаты последнего фолда из гонки выбывают — им нечем
сдавать экзамен, кроме будущего.

Лестница плечей x5..x15 на 3.2 годах осталась, но это ОТЧЁТ: плечо здесь не
рекомендуется, потому что полная история включает экзаменационное окно.
"""

import json
import random
import statistics

import evolution as ev
import evolution2 as e2
import evolution4 as e4
import evolution5 as e5
import ext_data as xd
import honest_eval as he

random.seed(46)
SYMBOLS = e4.SYMBOLS
POP, GENS, ELITE = 40, 16, 5
DAYS = 1150
LEVS = [5, 8, 10, 12, 15]

with open("evolution5_winners.json", encoding="utf-8") as fh:
    _v5 = json.load(fh)
with open("evolution4_winners.json", encoding="utf-8") as fh:
    _v4 = json.load(fh)


def final_genome(sym):
    if _v5[sym]["adopt"]:
        g = dict(_v5[sym]["genome"])
    elif _v4[sym]["adopt"]:
        g = dict(_v4[sym]["genome"])
        for k, v in e5.OFF5.items():
            g.setdefault(k, v)
    else:
        g = dict(_v4[sym]["base_genome"])
        for k, v in e5.OFF5.items():
            g.setdefault(k, v)
    return {k: (int(round(v)) if e5.GENES5[k][2] else float(v))
            for k, v in g.items() if k in e5.GENES5}


def calc_regime(candles):
    """0=bull 1=range 2=bear на каждую свечу (по дневным закрытиям)."""
    day_close = {}
    for ts, o, h, l, c in candles:
        day_close[ts // 86400000] = c  # последняя цена дня
    days = sorted(day_close)
    closes = [day_close[d] for d in days]
    reg_by_day = {}
    s = 0.0
    for i, d in enumerate(days):
        s += closes[i]
        if i >= 100:
            s -= closes[i - 100]
        if i < 100 or i < 30:
            reg_by_day[d] = 1
            continue
        sma100 = s / 100
        chg30 = closes[i] / closes[i - 30] - 1
        c = closes[i]
        if c < sma100 and chg30 < -0.05:
            reg_by_day[d] = 2
        elif c > sma100 and chg30 > 0.05:
            reg_by_day[d] = 0
        else:
            reg_by_day[d] = 1
    # режим текущей свечи = режим ВЧЕРАШНЕГО дня (без заглядывания)
    out = []
    for ts, *_ in candles:
        out.append(reg_by_day.get(ts // 86400000 - 1, 1))
    return out


def month_regimes(regime_slice):
    """Мажоритарный режим 30-дневных окон сегмента."""
    per_month = {}
    bars_month = 30 * 96
    for i, r in enumerate(regime_slice):
        per_month.setdefault(i // bars_month, []).append(r)
    return {m: max(set(v), key=v.count) for m, v in per_month.items()}


def bear_stats(r, mreg):
    """Метрики только по bear/range месяцам."""
    n_months = max(1, int(r["months"]))
    rets, n_active = [], 0
    for m in range(n_months):
        if mreg.get(m, 1) != 0:  # не bull
            rets.append(r["monthly"].get(m, 0.0) / e2.START * 100)
            n_active += 1
    if not rets:
        return dict(med=0, p25=0, pos=0, months=0)
    med = statistics.median(rets)
    p25 = ev.percentile(rets, 0.25)
    pos = sum(1 for x in rets if x > 0) / len(rets) * 100
    return dict(med=med, p25=p25, pos=pos, months=n_active)


def bear_score(r, mreg):
    st = bear_stats(r, mreg)
    return st["p25"] + 0.5 * st["med"] - (100 if r["ruined"] else 0)


def make_gate(regime_slice, inner):
    def f(side, i):
        if regime_slice[i] == 0:  # bull — не торгуем
            return None
        return inner(side, i)
    return f


def evolve6(candles, pre, aux, regime_slice, mreg, seeds, prefix):
    rand_g, clamp, mutate, cross = e4.ga_tools(e5.GENES5)
    cache = {}

    def eval_g(g):
        key = tuple(round(g[k], 4) if not e5.GENES5[k][2] else g[k]
                    for k in e5.GENES5)
        if key not in cache:
            filt = make_gate(regime_slice, e5.make_filter5(g, aux))
            r = e2.run5(candles, pre, g, entry_filter=filt)
            st = bear_stats(r, mreg)
            fit = (st["p25"] + 0.5 * st["med"]) - (50 if r["ruined"] else 0)
            cache[key] = fit
        return cache[key]

    pop = [clamp(dict(s)) for s in seeds]
    while len(pop) < POP:
        pop.append(rand_g())
    scored = sorted(((eval_g(g), g) for g in pop), key=lambda x: -x[0])
    for gen in range(GENS):
        new = [g for _, g in scored[:ELITE]]
        while len(new) < POP:
            if random.random() < 0.3:
                new.append(mutate(scored[random.randrange(ELITE)][1]))
            else:
                a = max(random.sample(scored, 3), key=lambda x: x[0])[1]
                b = max(random.sample(scored, 3), key=lambda x: x[0])[1]
                new.append(mutate(cross(a, b)))
        scored = sorted(((eval_g(g), g) for g in new), key=lambda x: -x[0])
        if (gen + 1) % 4 == 0:
            print(f"  {prefix} поколение {gen+1:2}: best {scored[0][0]:+.2f}")
    return scored


def main():
    pct5 = xd.fetch_daily_pct5()
    results = {}
    for sym in SYMBOLS:
        print(f"\n================ {sym} (bear-специалист) ================")
        candles = ev.fetch(sym, "15", DAYS)
        n = len(candles)
        regime = calc_regime(candles)
        share = {k: round(regime.count(k) / n * 100) for k in (0, 1, 2)}
        print(f"Режимы за 3.2г: bull {share[0]}% | range {share[1]}% | bear {share[2]}%")

        funding = xd.fetch_funding(sym, DAYS + 50)
        oi = xd.fetch_oi(sym)
        aux = xd.build_aux4(candles, funding, oi, pct5)
        closes = [c[4] for c in candles]
        aux["closes"] = closes
        aux["ema"] = [e5.calc_ema(closes, x) for x in e5.EMA_SET]
        aux["smaf"] = [e5.calc_sma(closes, x) for x in e5.MAF_SET]
        aux["smas"] = [e5.calc_sma(closes, x) for x in e5.MAS_SET]
        aux["aroon"] = [e5.calc_aroon(candles, x) for x in e5.ARN_SET]

        folds = e4.fold_bounds_3y(n)

        def _sl(v, b, e):
            if isinstance(v, tuple):
                return tuple(_sl(x, b, e) for x in v)
            if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
                return [_sl(x, b, e) for x in v]
            return v[b:e]

        segs = []
        for (a, b, e) in folds:
            seg = candles[b:e]
            reg_s = regime[b:e]
            segs.append((seg, e2.prep(seg),
                         {k: _sl(v, b, e) for k, v in aux.items()},
                         reg_s, month_regimes(reg_s)))

        base = final_genome(sym)

        # ЧЕСТНЫЙ WALK-FORWARD (правка второго круга, 08.2026).
        # Здесь был свой протекающий харнесс: agg(g) гоняла кандидата по ВСЕМ
        # трём OOS-окнам, включая те, что лежат внутри его собственного
        # обучения (фолды anchored: train фолда 3 — это [0..30м), а окна 18-24 и
        # 24-30 внутри него), и argmax по среднему решал И выбор, И приёмку —
        # одно число служило и вопросом, и ответом. v6 общий харнесс e4 не
        # использует, поэтому правка e4 её НЕ чинила: цикл нужно было развести
        # здесь. Теперь роли окон те же, что в e4.choose_winner: валидация —
        # первое окно после обучения кандидата, экзамен — последнее окно, в
        # выборе не участвует.
        def score_on(g, wi):
            seg, pre_s, aux_s, reg_s, mreg_s = segs[wi]
            filt = make_gate(reg_s, e5.make_filter5(g, aux_s))
            r = e2.run5(seg, pre_s, g, entry_filter=filt)
            return bear_score(r, mreg_s)

        base_sc = [score_on(base, wi) for wi in range(len(segs))]
        base_mean = sum(base_sc) / len(base_sc)
        print(f"БАЗА (FINAL + режимный гейт): bear/range-OOS по окнам "
              f"{['%+.2f' % s for s in base_sc]} (среднее {base_mean:+.2f}, "
              f"экзамен = окно {e4.exam_window_index(len(segs))+1}: "
              f"{base_sc[-1]:+.2f})")

        base_key = tuple(base[k] for k in e5.GENES5)
        cand = []
        for fi, (a, b, e) in enumerate(folds):
            train = candles[a:b]
            reg_t = regime[a:b]
            scored = evolve6(train, e2.prep(train),
                             {k: _sl(v, a, b) for k, v in aux.items()},
                             reg_t, month_regimes(reg_t), [base],
                             f"{sym[:3]}-f{fi+1}")
            seen = set()
            for f_, g in scored:
                key = tuple(g[k] for k in e5.GENES5)
                # база — точка отсчёта, а не соперник (её отрыв от себя = 0)
                if key == base_key or key in seen:
                    continue
                seen.add(key)
                # номер фолда обязателен: без него не узнать, какие окна для
                # этого кандидата уже «просмотрены» обучением
                cand.append((fi, g))
                if len(seen) == 3:
                    break

        pick = e4.choose_winner(cand, base_sc, score_on)
        g_win = pick["genome"]
        m, base_exam = pick["exam_score"], pick["base_exam"]
        sc = [score_on(g_win, wi) for wi in range(pick["train_fold"],
                                                  len(segs))]
        # Ворота honest_eval на экзаменационном окне. Без них отбор здесь
        # повторяет ошибку волны v11 по SOL: bear_score конфига, который не
        # торгует, равен ровно 0.00, а база на медвежьем окне почти всегда в
        # минусе — «ноль больше минуса» и вырожденный геном проходит как
        # победа. Плечо ворот = e2.LEV, то же, на котором шёл отбор: своё
        # плечо эта волна не выбирает и в конфиг не отдаёт (её выход — базовый
        # геном для v7), а лестница ниже — только отчёт.
        ex_seg, ex_pre, ex_aux, ex_reg, _ = segs[pick["exam_window"]]
        mm = he.measure(ex_seg, ex_pre, g_win,
                        make_gate(ex_reg, e5.make_filter5(g_win, ex_aux)),
                        e2.LEV, tag=f"{sym}/v6")
        gates_ok, reasons, warns = he.verdict(mm)
        edge_ok = m > base_exam + 0.5
        if not edge_ok:
            reasons = [f"отрыв на экзамене {m - base_exam:+.2f} <= 0.5"
                       ] + list(reasons)
        adopt = bool(edge_ok and gates_ok)
        print(f"ЛУЧШИЙ bear-конфиг: экзамен {m:+.2f} против базы "
              f"{base_exam:+.2f} | честные окна кандидата "
              f"{['%+.2f' % s for s in sc]} | "
              f"принят: {'ДА' if adopt else 'нет (остаётся FINAL+гейт)'}")
        e4.gate_report(mm, reasons, warns)
        g_use = g_win if adopt else base

        # --- лестница плечей на полных 3.2г (вход только bear/range) ---
        # Это ОТЧЁТ, а не выбор: плечо здесь не рекомендуется. Лестница по всей
        # истории включает экзаменационное окно, и выбирать по ней плечо —
        # такая же утечка, как выбирать по ней геном (в v7/v10/v11/v12 выбор
        # идёт по обучающей части, e4.choose_leverage).
        print("Плечо | Итог 3.2г | bear-мес мед | DD | Слив")
        ladder = []
        for lev in LEVS:
            old_lev, e2.LEV = e2.LEV, lev
            try:
                filt = make_gate(regime, e5.make_filter5(g_use, aux))
                r = e2.run5(candles, e2.prep(candles), g_use, entry_filter=filt)
            finally:
                e2.LEV = old_lev   # раньше здесь стояло e2.LEV = 5 «на глазок»
            mreg_full = month_regimes(regime)
            st = bear_stats(r, mreg_full)
            ret = (r["balance"] / e2.START - 1) * 100
            print(f"  x{lev:<3} | {ret:+8.1f}% | {st['med']:+5.2f}% | "
                  f"{r['max_dd']*100:4.1f}% | {'ДА' if r['ruined'] else 'нет'}")
            ladder.append(dict(lev=lev, ret=round(ret, 1),
                               med=round(st["med"], 2),
                               dd=round(r["max_dd"] * 100, 1),
                               ruined=r["ruined"]))

        # base_oos/cand_oos — баллы ЭКЗАМЕНАЦИОННОГО окна (в волне 08.2026 тут
        # лежало среднее по трём окнам); поля protocol/val_* не дают перепутать
        # старый json с новым при чтении их рядом
        results[sym] = dict(base_oos=base_exam, cand_oos=m, adopt=bool(adopt),
                            genome=g_use, ladder=ladder,
                            protocol=("leaky-3window-mean" if pick["leaky"]
                                      else "honest-val-then-exam"),
                            base_folds=base_sc, base_mean_all=base_mean,
                            cand_folds=sc,
                            val_window=pick["val_window"],
                            val_score=pick["val_score"],
                            val_edge=pick["val_edge"],
                            exam_window=pick["exam_window"],
                            train_fold=pick["train_fold"],
                            skipped_candidates=pick["skipped"],
                            lev_picked_on=None,   # v6 плечо не рекомендует
                            regime_share=share)
    with open("evolution6_winners.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2, default=float)
    print("\nИтоги в evolution6_winners.json")


if __name__ == "__main__":
    main()
