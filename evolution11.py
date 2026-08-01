# -*- coding: utf-8 -*-
"""v11: ЧЕСТНЫЙ отбор сигнальных сетапов — вложенная валидация.

ЗАЧЕМ ЭТА ВОЛНА. Аудит нашёл системную ошибку в v10 (и в прежних волнах
ботов v4-v8, где применялась та же схема): фолды e4.fold_bounds_3y —
"anchored", трейн всегда [0, b), поэтому OOS-отрезок раннего фолда лежит
ВНУТРИ обучающей выборки позднего. Кандидаты набирались со всех фолдов и
оценивались на всех трёх OOS-отрезках -> геном, обученный на [0..5389),
получал два "экзамена" из трёх на своих же обучающих данных.
Эмпирика: 5 из 6 победителей v10 пришли с самого загрязнённого фолда, и при
честной оценке (только отрезки ПОСЛЕ трейна) все шесть уходили из плюса в
минус (например range_long: +0.430R -> -1.236R).

КАК ЗДЕСЬ. Вложенная (nested) валидация:
  [0 .. HOLD)            — обучение И отбор кандидатов (внутренние фолды);
  [HOLD .. n)            — HOLDOUT: неприкосновенный кусок, который НЕ видит
                           ни GA, ни выбор победителя. Только финальная оценка.
Внутри обучающей части — те же anchored фолды (там взаимное загрязнение
допустимо: оно влияет лишь на ВЫБОР кандидата, а честность итоговой оценки
обеспечивает holdout, которого не касался никто).
Дополнительно считается БЕНЧМАРК: необученное семя DEFAULTS2 на том же
holdout. Если победитель не лучше семени — отбор ничего не дал, и это будет
видно.

Плечо тоже выбирается по holdout (в v10 лестница считалась на полном периоде,
т.е. частично in-sample).

Запуск: python evolution11.py
Переменные окружения: GA_POP, GA_GENS, GA_SETUPS, GA_HOLD (доля holdout).
"""

import json
import os
import time

import evolution as ev
import evolution10 as e10
import signal_engine2 as se2

HOLD_FRAC = float(os.environ.get("GA_HOLD", "0.28"))   # доля истории в holdout
INNER_FOLDS = 3
LEVS = e10.LEVS
GA_LEV = e10.GA_LEV
CAND_PER_FOLD = e10.CAND_PER_FOLD
MIN_HOLD_TRADES = 8       # меньше — статистики на holdout нет
DAYS = e10.DAYS


def inner_folds(hold_start):
    """anchored фолды ВНУТРИ обучающей части: трейн [0,b), OOS [b,e)."""
    step = hold_start // (INNER_FOLDS + 1)
    out = []
    for k in range(INNER_FOLDS):
        b = step * (k + 2)
        e_ = min(hold_start, b + step)
        if e_ - b > 100:
            out.append((0, b, e_))
    return out


def pooled(setup, g, data, segments, lev):
    """Сделки со всех отрезков вместе + метрики. segments: [(a,b), ...]"""
    c4, ctx, c15, ts15 = data
    tr = []
    for (a, b) in segments:
        r = se2.run_setup(setup, g, c4, ctx, c15, ts15, lev,
                          signal_range=(a, b), collect_diag=False)
        tr += r["trades"]
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


def main():
    t_all = time.time()
    setups = os.environ.get("GA_SETUPS")
    setups = [s.strip() for s in setups.split(",")] if setups else se2.SETUPS

    c4 = ev.fetch("BTCUSDT", "240", DAYS)
    c15 = ev.fetch("BTCUSDT", "15", DAYS)
    ts15 = [c[0] for c in c15]
    ctx = se2.prep_context(c4)
    data = (c4, ctx, c15, ts15)
    n = len(c4)
    hold = int(n * (1 - HOLD_FRAC))
    folds = inner_folds(hold)

    def d(ms):
        return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))

    def mon(bars):
        return bars * 4 / 24 / 30.44

    print("=" * 78)
    print("evolution11: ЧЕСТНЫЙ отбор (вложенная валидация, holdout не виден "
          "ни GA, ни отбору)")
    print("=" * 78)
    print(f"BTCUSDT 4ч-баров {n} ({d(c4[0][0])} .. {d(c4[-1][0])}), 15м {len(c15)}")
    print(f"ОБУЧЕНИЕ+ОТБОР: бары [0..{hold}) = {mon(hold):.1f} мес "
          f"({d(c4[0][0])}..{d(c4[hold][0])})")
    print(f"HOLDOUT (честный экзамен): [{hold}..{n}) = {mon(n-hold):.1f} мес "
          f"({d(c4[hold][0])}..{d(c4[-1][0])})")
    reg_h = ctx["regime"][hold:]
    print(f"  режимы holdout: bull {reg_h.count(0)*100//len(reg_h)}%, "
          f"range {reg_h.count(1)*100//len(reg_h)}%, "
          f"bear {reg_h.count(2)*100//len(reg_h)}% | цена "
          f"{c4[hold][4]:.0f} -> {c4[-1][4]:.0f} "
          f"({(c4[-1][4]/c4[hold][4]-1)*100:+.1f}%)")
    print("внутренние фолды (только для ВЫБОРА кандидата):")
    for i, (a, b, e_) in enumerate(folds):
        print(f"  фолд {i+1}: трейн [{a}..{b}) {mon(b-a):.1f} мес -> "
              f"внутр.OOS [{b}..{e_}) {mon(e_-b):.1f} мес")
    print(f"GA: POP={e10.POP} GENS={e10.GENS} ELITE={e10.ELITE} | "
          f"отбор на x{GA_LEV} | сетапы: {', '.join(setups)}")

    results = {}
    for si, setup in enumerate(setups):
        t0 = time.time()
        print("\n" + "=" * 78)
        print(f"{setup}  ({si+1}/{len(setups)})")
        print("=" * 78)

        # 1) GA на трейнах внутренних фолдов -> пул кандидатов
        cands = []
        for fi, (a, b, e_) in enumerate(folds):
            # evolve_setup отдаёт тройки (фитнес, сделок, геном), лучшие первыми
            scored = e10.evolve_setup(setup, data, (a, b), f"{setup[:10]}-f{fi+1}")
            seen = set()
            for fit, n_tr, g in scored:
                k = e10.gkey(g)
                if k in seen:
                    continue
                seen.add(k)
                cands.append(dict(g=g, src=fi, fit=fit, train_n=n_tr))
                if len(seen) >= CAND_PER_FOLD:
                    break
        print(f"  кандидатов: {len(cands)}")

        # 2) выбор победителя ТОЛЬКО по внутренним OOS (holdout не трогаем!)
        inner_segs = [(b, e_) for (_a, b, e_) in folds]
        ranked = []
        for c in cands:
            p = pooled(setup, c["g"], data, inner_segs, GA_LEV)
            ranked.append((p["exp_r"] if p["n"] >= 10 else -99, p["n"], p, c))
        ranked.sort(key=lambda x: (-x[0], -x[1]))
        best_score, _, inner_p, best = ranked[0]
        g_win = best["g"]
        print(f"  ВЫБРАН (фолд {best['src']+1}, фитнес трейна {best['fit']:+.2f} "
              f"на {best['train_n']} сделках): внутр.OOS {inner_p['n']} сделок, "
              f"exp {inner_p['exp_r']:+.3f}R, PF {inner_p['pf']}, WR {inner_p['wr']}%")

        # 3) ЧЕСТНЫЙ ЭКЗАМЕН на holdout + бенчмарк необученного семени
        hold_seg = [(hold, n)]
        h = pooled(setup, g_win, data, hold_seg, GA_LEV)
        base = pooled(setup, dict(se2.DEFAULTS2), data, hold_seg, GA_LEV)
        print(f"  --- HOLDOUT (эти данные не видели ни GA, ни отбор) ---")
        print(f"    победитель: {h['n']:3} сделок | WR {h['wr']:5.1f}% | "
              f"exp {h['exp_r']:+.3f}R | PF {h['pf']} | сумма {h['sum_r']:+.2f}R "
              f"| итог {h['ret']:+.1f}% | DD {h['dd']}% | tp/stop {h['tp']}/{h['stop']}")
        print(f"    семя DEFAULTS2: {base['n']:3} сделок | WR {base['wr']:5.1f}% | "
              f"exp {base['exp_r']:+.3f}R | PF {base['pf']} | итог {base['ret']:+.1f}%")
        edge = h["exp_r"] - base["exp_r"]
        print(f"    преимущество отбора над семенем: {edge:+.3f}R/сделку "
              f"({'есть' if edge > 0 else 'НЕТ'})")

        # 4) лестница плечей — на HOLDOUT (честно), не на полном периоде
        ladder = []
        print(f"  лестница плечей на holdout:")
        for lev in LEVS:
            pl = pooled(setup, g_win, data, hold_seg, lev)
            ladder.append(dict(lev=lev, ret=pl["ret"], dd=pl["dd"], n=pl["n"],
                               wr=pl["wr"], exp_r=pl["exp_r"], pf=pl["pf"]))
            print(f"    x{lev:<3} итог {pl['ret']:+7.1f}% | DD {pl['dd']:5.1f}% | "
                  f"{pl['n']:3} сделок | WR {pl['wr']:5.1f}% | PF {pl['pf']}")
        rec = None
        for row in ladder[::-1]:
            if row["lev"] >= 15 and row["dd"] <= 25 and row["ret"] > 0:
                rec = row["lev"]
                break
        caution = rec is None
        if rec is None:
            rec = 10
        print(f"    -> рекомендованное плечо: x{rec}"
              f"{' (осторожно: на 15-20 просадка/убыток)' if caution else ''}")

        # 5) вердикт
        ok = (h["n"] >= MIN_HOLD_TRADES and h["exp_r"] > 0
              and (h["pf"] or 0) >= 1.2 and h["sum_r"] > 0 and edge > 0)
        why = []
        if h["n"] < MIN_HOLD_TRADES:
            why.append(f"на holdout всего {h['n']} сделок < {MIN_HOLD_TRADES}")
        if h["exp_r"] <= 0:
            why.append(f"holdout: expectancy {h['exp_r']:+.3f}R не > 0")
        if (h["pf"] or 0) < 1.2:
            why.append(f"holdout: PF {h['pf']} < 1.2")
        if h["sum_r"] <= 0:
            why.append(f"holdout: сумма {h['sum_r']:+.2f}R не > 0")
        if edge <= 0:
            why.append(f"нет преимущества над необученным семенем ({edge:+.3f}R)")
        print(f"  ВЕРДИКТ: {'ПРОШЁЛ честный экзамен' if ok else 'НЕ ПРОШЁЛ'}"
              + ("" if ok else " — " + "; ".join(why)))

        e10.print_genome(setup, g_win)
        results[setup] = dict(
            genome=g_win, rec_lev=rec, caution=caution, passed=bool(ok),
            fail_reasons=why, holdout=h, holdout_benchmark=base,
            edge_exp_r=round(edge, 3), inner_oos=inner_p, ladder=ladder,
            src_fold=best["src"] + 1, train_fitness=round(best["fit"], 2),
            hold_start_bar=hold, n_bars=n)
        print(f"  время: {time.time()-t0:.0f}с")

    with open("evolution11_winners.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2, default=float)

    print("\n" + "=" * 78)
    print("ИТОГ ЧЕСТНОГО ЭКЗАМЕНА (holdout, никем не виденный)")
    print("=" * 78)
    print(f"{'сетап':13} {'сдел':>5} {'WR%':>6} {'exp R':>7} {'PF':>5} "
          f"{'итог%':>8} {'DD%':>6} {'семя exp':>9} {'edge':>7}  вердикт")
    for s, r in results.items():
        h, b = r["holdout"], r["holdout_benchmark"]
        print(f"{s:13} {h['n']:5} {h['wr']:6.1f} {h['exp_r']:+7.3f} "
              f"{str(h['pf']):>5} {h['ret']:+8.1f} {h['dd']:6.1f} "
              f"{b['exp_r']:+9.3f} {r['edge_exp_r']:+7.3f}  "
              f"{'ПРОШЁЛ' if r['passed'] else 'нет'}")
    n_ok = sum(1 for r in results.values() if r["passed"])
    print(f"\nпрошли честный экзамен: {n_ok} из {len(results)}")
    print(f"итоги в evolution11_winners.json | всего {time.time()-t_all:.0f}с")


if __name__ == "__main__":
    main()
