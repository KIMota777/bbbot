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
  1) балл на ЭКЗАМЕНАЦИОННОМ окне выше базы более чем на MIN_EDGE (не шум);
  2) на своём окне валидации кандидат не ниже базы больше чем на FOLD_TOL;
  3) итог за все 3.2 года на боевом плече не ниже базового;
  4) просадка не выросла больше чем на DD_TOL;
  5) пройдены ворота honest_eval — хвост (худший месяц, худшая сделка,
     ликвидации, плавающая просадка, риск руина) и вырожденность (минимум
     сделок, сделок в месяц, экспозиции).
Провал любого пункта -> монета остаётся на прежней сетке, и это честно
записывается в evolution10_winners.json.

Правка 08.2026: пункты 1-2 раньше считались по СРЕДНЕМУ трёх OOS-окон, из
которых для позднего кандидата два лежали внутри его обучения, и то же среднее
решало приёмку. Теперь выбор идёт по окну валидации, приёмка — по отдельному
экзаменационному окну (e4.choose_winner), а рекомендация по плечу считается
только на обучающей части и по ПЛАВАЮЩЕЙ просадке.

Правка второго круга: нынешняя сетка убрана из списка кандидатов. Пока она
стояла там наравне с остальными, её отрыв от самой себя (ноль) был нижней
границей отрыва победителя, val_edge никогда не уходил в минус, и пункт 2 не
мог сработать ни при каких данных.

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
import honest_eval as he

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
    ddf = r.get("max_dd_float")
    return dict(ret=round((r["balance"] / e2.START - 1) * 100, 2),
                dd=round(r["max_dd"] * 100, 2),
                # плавающая просадка: по ней выбирается плечо, потому что
                # закрытая не видит переоценки открытой сетки
                dd_float=(round(ddf * 100, 2) if ddf is not None else None),
                trades=r["trades"],
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
    """Наибольшее плечо с просадкой <= DD_CAP и без слива.

    rows — лестница по ОБУЧАЮЩЕЙ части (e4.train_slice). Раньше сюда шла
    лестница за все 3.2 года: рекомендация по плечу смотрела в окно, которое
    считается экзаменационным, и переставала быть проверяемой.

    Само правило — общее для всех волн (e4.choose_leverage): плавающая
    просадка, слив дисквалифицирует ступень, и если порог не проходит НИ ОДНА
    ступень, это говорится словами, а не прячется за молчаливым x5.
    """
    return e4.choose_leverage(rows, DD_CAP)["lev"]


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

    def score_on(sub, wi):
        """Балл одного OOS-окна — НА БОЕВОМ ПЛЕЧЕ монеты.

        Прежние волны считали экзамены на плече по умолчанию (x5), а итог —
        на боевом. Для BTC (x15) из-за этого шкала экзамена оказывалась втрое
        мельче денежной: OOS-оценки выходили порядка 0.0-0.1, и порог MIN_EDGE
        был для них недостижим в принципе. Решение о принятии обязано
        приниматься на той же шкале, на которой считаются деньги.

        Окна больше не усредняются скопом: кандидату дозволены только те, что
        лежат строго после его обучения (см. e4.choose_winner).
        """
        g = with_genes(base, sub)
        seg, pre_s, aux_s = oos[wi]
        old, e2.LEV = e2.LEV, lev
        try:
            r = e2.run5(seg, pre_s, g, entry_filter=e8.make_filter8(g, aux_s))
        finally:
            e2.LEV = old
        return e2.oos_score(r)

    off = clamp10(dict(gridlib.OFF10))
    base_sc = [score_on(off, wi) for wi in range(len(oos))]
    base_mean = sum(base_sc) / len(base_sc)
    exam_i = e4.exam_window_index(len(folds))
    base_full = full_run(base, off, candles, pre_full, aux, lev)
    print(f"БАЗА (нынешняя сетка): OOS по окнам "
          f"{['%+.3f' % s for s in base_sc]} (экзамен {base_sc[exam_i]:+.3f}) "
          f"| 3.2г x{lev} {base_full['ret']:+.2f}% "
          f"| DD {base_full['dd']:.2f}% | сделок {base_full['trades']} "
          f"| убыточных {base_full['loss_share']:.2f}%", flush=True)

    # Нынешняя сетка (off) в гонке НЕ участвует. Раньше она стояла здесь как
    # cands=[(0, off)] — «она не обучалась ни на чём, поэтому честна на любом
    # окне». Но она же служит БАЗОЙ, от которой считается отрыв: её отрыв от
    # самой себя на окне 1 тождественно равен нулю, поэтому победитель всегда
    # имел val_edge >= 0, и правило приёмки 2 («на своём окне валидации
    # кандидат ниже базы больше чем на FOLD_TOL») не могло сработать физически
    # ни разу. База — точка отсчёта, а не соперник.
    cands = []
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
            cands.append((fi, sub))
            if len(seen) == 3:
                break

    pick = e4.choose_winner(cands, base_sc, score_on)
    sub = pick["genome"]
    mean, sc = pick["exam_score"], [pick["exam_score"]]
    cand_full = full_run(base, sub, candles, pre_full, aux, lev)

    # ворота honest_eval на экзаменационном окне и боевом плече монеты
    ex_seg, ex_pre, ex_aux = oos[pick["exam_window"]]
    g_win = with_genes(base, sub)
    mm = he.measure(ex_seg, ex_pre, g_win, e8.make_filter8(g_win, ex_aux), lev,
                    tag=f"{sym}/v10")
    gates_ok, gate_reasons, warns = he.verdict(mm)

    reasons = list(gate_reasons)
    if mean <= base_sc[exam_i] + MIN_EDGE:
        reasons.append(f"отрыв на экзамене {mean-base_sc[exam_i]:+.3f} "
                       f"<= {MIN_EDGE}")
    if pick["val_edge"] is not None and pick["val_edge"] < -FOLD_TOL:
        reasons.append(f"на своём окне валидации кандидат ниже базы "
                       f"({pick['val_edge']:+.3f} < -{FOLD_TOL})")
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

    print(f"ЛУЧШИЙ КАНДИДАТ: экзамен {mean:+.3f} против базы "
          f"{base_sc[exam_i]:+.3f} | 3.2г x{lev} {cand_full['ret']:+.2f}% | "
          f"DD {cand_full['dd']:.2f}% | сделок {cand_full['trades']} "
          f"| убыточных {cand_full['loss_share']:.2f}%", flush=True)
    e4.gate_report(mm, gate_reasons, warns)
    print(f"  гены: { {k: (round(v, 4) if isinstance(v, float) else v) for k, v in sub.items()} }")
    print(f"  ПРИНЯТ: {'ДА' if adopt else 'нет — ' + '; '.join(reasons)}",
          flush=True)

    chosen = sub if adopt else off
    # рекомендация по плечу — по обучающей части; полная лестница только отчёт
    train_c = e4.train_slice(candles)
    lad_train = ladder(base, chosen, train_c, e2.prep(train_c), aux)
    lev_ch = e4.choose_leverage(lad_train, DD_CAP)
    rec_lev = lev_ch["lev"]
    lad = ladder(base, chosen, candles, pre_full, aux)
    print(f"  лестница плечей (обучение, DD плав.): "
          f"{[(r['lev'], r['ret'], r['dd_float']) for r in lad_train]} "
          f"-> x{rec_lev}"
          f"{'' if lev_ch['ok'] else ' (НЕ ПОДТВЕРЖДЕНО просадкой)'}",
          flush=True)
    for w in lev_ch["warns"]:
        print(f"    ВНИМАНИЕ: {w}", flush=True)
    # Экзамен, ворота и итог 3.2г в этой волне считались на БОЕВОМ плече монеты
    # (config), потому что волна меняет только гены сетки, а плечо в конфиге
    # остаётся прежним. Если лестница рекомендует другое — об этом надо сказать
    # вслух: цифры приёмки описывают x{lev}, а не x{rec_lev}.
    if rec_lev != lev:
        print(f"    ВНИМАНИЕ: приёмка считалась на боевом плече x{lev}, а "
              f"лестница по обучающей части рекомендует x{rec_lev} — "
              f"рекомендация НЕ проверена экзаменом и воротами", flush=True)

    return dict(genes=sub, adopt=adopt, reject_reasons=reasons,
                warnings=warns,
                exam_measure={k: v for k, v in mm.items() if k != "rs"},
                protocol=("leaky-3window-mean" if pick["leaky"]
                          else "honest-val-then-exam"),
                base_oos=round(base_sc[exam_i], 4), cand_oos=round(mean, 4),
                base_mean_all=round(base_mean, 4),
                base_folds=[round(x, 4) for x in base_sc],
                cand_folds=[round(x, 4) for x in sc],
                exam_window=pick["exam_window"], val_window=pick["val_window"],
                val_edge=pick["val_edge"], train_fold=pick["train_fold"],
                skipped_candidates=pick["skipped"],
                base_full=base_full, cand_full=cand_full,
                lev=lev, ladder=lad, ladder_train=lad_train,
                rec_lev=rec_lev, lev_picked_on="train",
                lev_confirmed=lev_ch["ok"], lev_warnings=lev_ch["warns"],
                exam_lev=lev, chosen=chosen)


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
