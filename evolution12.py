# -*- coding: utf-8 -*-
"""v12: поиск прибыльных конфигов для DOGE/LTC/SOL на исправленном движке.

Контекст. После закрытия дефектов движка (be_move по хаю бара + пропуск
ликвидации, см. коммит 9b9357a) выяснилось, что DOGE/LTC/SOL никогда не были
прибыльными: их плюс держался на выходах по стопам, которые биржа не приняла
бы. Переотбор v11 на прежнем наборе генов прибыли не нашёл. BTC и ETH
прибыльны как есть и в этой волне НЕ участвуют — их не трогаем.

Что нового в пространстве поиска v12 (поверх GENES8):

1. ADX-гейт (НОВЫЙ индикатор, indicators.calc_adx). Стратегия — возврат к
   средней; её убытки концентрируются там, где диапазон ломается в тренд, а
   силу тренда прежние индикаторы не меряют (MA/EMA — направление, Aroon —
   свежесть экстремума). Гены: adx_gate (0/1), adx_idx (период из ADX_SET),
   adx_max — вход запрещён, пока ADX выше порога.

2. Гены подвижной сетки v10 (gridlib). В v10 они проиграли БАЗЕ, которая, как
   теперь известно, была накачана дырой движка — честного шанса у них не
   было. На честном движке трейлинг и адаптация к волатильности могут быть
   именно тем, чего не хватало: они сокращают время удержания против тренда.

Урок первого прогона (важно). Запуск через общий харнесс e4.run_version
выродился: у всех трёх монет «лучшим кандидатом» стал конфиг, который НЕ
ТОРГУЕТ ВООБЩЕ (0 сделок -> OOS ровно 0.00, а любая убыточная база хуже
нуля). Прежним волнам это не грозило — их базы были прибыльны, и нулевой
конфиг им проигрывал. Поэтому здесь свой GA с fitness, который штрафует
отказ от торговли: меньше 1 сделки в месяц — большой минус с градиентом в
сторону торговли, фолд без сделок на экзамене — тоже штраф.

Правило принятия в конфиг (задано ДО прогона, менять под результат нельзя):
  1) итог 3.2г на выбранном плече ПОЛОЖИТЕЛЬНЫЙ;
  2) просадка <= 20%, слива нет;
  3) средний OOS > 0 и > базового + 0.5;
  4) худший из трёх экзаменов > -0.5;
  5) доля убыточных сделок >= 1% (урок v8/v10: почти нулевые убытки — дыра);
  6) сделок >= 60 (иначе статистика — шум).
Не прошёл — монета честно остаётся убыточной, без натяжек.

Запуск: python evolution12.py            (все три монеты)
        python evolution12.py SOLUSDT    (одна)
"""

import json
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
import indicators

SYMS = ["DOGEUSDT", "LTCUSDT", "SOLUSDT"]
DAYS = 1150
LEVS = [5, 8, 10, 12, 15]
DD_CAP = 20.0
ADX_SET = [7, 14, 21, 28]

MIN_EDGE = 0.5
FOLD_FLOOR = -0.5
MIN_LOSS_SHARE = 1.0
MIN_TRADES = 60

GENES12 = dict(e8.GENES8)
GENES12.update({
    "adx_gate":     (0, 1, True),
    "adx_idx":      (0, len(ADX_SET) - 1, True),
    "adx_max":      (10.0, 45.0, False),
    "grid_mode":    (0, 1, True),
    "grid_span":    (0.30, 0.95, False),
    "grid_spread":  (0.60, 1.60, False),
    "grid_atr_k":   (0.0, 1.0, False),
    "grid_w_atr_k": (-1.0, 1.0, False),
    "grid_retune":  (0, 2, True),
    "tp_atr_k":     (0.0, 1.0, False),
    "trail_k":      (0.0, 0.90, False),
    "trail_start":  (0.25, 0.90, False),
})
OFF12 = dict(e8.OFF8)
OFF12.update(adx_gate=0, adx_idx=1, adx_max=30.0)
OFF12.update(gridlib.OFF10)


def make_filter12(g, aux):
    """Фильтр v8 + ADX-гейт. При adx_gate=0 тождественен make_filter8."""
    base = e8.make_filter8(g, aux)
    adx_all = aux.get("adx")

    def f(side, i):
        if g.get("adx_gate") and adx_all is not None:
            v = adx_all[int(g.get("adx_idx", 1))][i]
            if v is not None and v > g.get("adx_max", 30.0):
                return None
        return base(side, i)

    return f


def make_aux_builder(pct5, bars_per_day):
    """aux v8 + ряды ADX по всем периодам из ADX_SET."""
    b8 = e8.make_aux_builder(pct5, bars_per_day)

    def builder(sym, candles):
        aux = b8(sym, candles)
        t0 = time.time()
        aux["adx"] = [indicators.calc_adx(candles, n) for n in ADX_SET]
        print(f"  {sym}: ADX посчитан за {time.time()-t0:.2f}с")
        return aux

    return builder


def genome_to_cfg(g, lev):
    """Геном движка -> dict для config.SYMBOL_PARAMS. Обратное отображение к
    cfg_to_genome; тождество roundtrip проверяется при принятии конфига —
    это гарантия, что живой бот исполняет ровно то, что сдало экзамен."""
    import evolution5 as e5
    p = dict(
        lev=lev,
        rsi_period=e2.RSI_SET[g["rsi_idx"]], rsi_os=g["rsi_os"],
        zone_l=round(g["zone_l"], 6), zone_s=round(g["zone_s"], 6),
        window=g["window"], step=round(g["step"], 7),
        levels=g["levels"], mult=round(g["mult"], 6),
        tp=round(g["tp"], 7), sweep=round(g["sweep"], 6),
        max_bars=g["max_bars"], cooldown=g["cooldown"],
        knife=round(g["knife"], 6), be_move=g["be_move"],
        fund_long_max=round(g["fund_long_max"], 7),
        fund_short_min=round(g["fund_short_min"], 7),
        oi_gate=g["oi_gate"],
        spx_long_min=round(g["spx_long_min"], 6),
        dxy_long_max=round(g["dxy_long_max"], 6),
        gold_long_max=round(g["gold_long_max"], 6),
        ema_mode=g["ema_mode"], ema_n=e5.EMA_SET[g["ema_n_idx"]],
        ma_mode=g["ma_mode"], masf=e5.MAF_SET[g["masf_idx"]],
        masl=e5.MAS_SET[g["masl_idx"]],
        aroon_n=e5.ARN_SET[g["aroon_idx"]],
        aroon_long_min=g["aroon_long_min"],
        aroon_short_min=g["aroon_short_min"],
        direction=g["direction"], regime_gate=g["regime_gate"],
        pattern_gate=g["pattern_gate"],
        ob_gate=g["ob_gate"], fvg_gate=g["fvg_gate"],
        structure_mode=g["structure_mode"])
    if g.get("adx_gate"):
        p.update(adx_gate=1, adx_n=ADX_SET[int(g["adx_idx"])],
                 adx_max=round(g["adx_max"], 4))
    for k in gridlib.OFF10:
        if g.get(k, gridlib.OFF10[k]) != gridlib.OFF10[k]:
            v = g[k]
            p[k] = round(v, 6) if isinstance(v, float) else v
    return p


def full_run(g, candles, pre, aux, lev, events=None):
    filt = make_filter12(g, aux)
    old, e2.LEV = e2.LEV, lev
    try:
        r = e2.run5(candles, pre, g, entry_filter=filt, events=events)
    finally:
        e2.LEV = old
    n, w = r["trades"], r["wins"]
    return dict(ret=round((r["balance"] / e2.START - 1) * 100, 2),
                dd=round(r["max_dd"] * 100, 2), trades=n,
                wr=round(w / n * 100, 1) if n else 0.0,
                loss_share=round((n - w) / n * 100, 2) if n else 0.0,
                ruined=r["ruined"])


POP, GENS, ELITE = 48, 24, 6


def fitness12(r):
    """Как e2.fitness, но с жёстким штрафом за отказ от торговли: конфиг с
    <1 сделкой в месяц не может выиграть у убыточной базы просто потому,
    что «ноль больше минуса». Градиент по tpm подталкивает GA обратно к
    торгующим конфигам, а не в мёртвую зону."""
    st = e2.stats(r)
    if st["tpm"] < 1.0:
        return -100.0 + st["tpm"] * 20.0
    f = (st["p25"] + 0.5 * st["med"]) * min(1.0, st["tpm"] / 6.0)
    f *= e2.BARS_PER_DAY / (e2.BARS_PER_DAY + st["avg_hold"])
    if r["ruined"]:
        f -= 50
    return f


def oos_score12(r):
    """Оценка экзамена: фолд без торговли — штраф, а не нейтральный ноль."""
    if r["trades"] == 0:
        return -5.0
    return e2.oos_score(r)


def slice_aux(aux, b, e):
    def cut(v):
        if isinstance(v, tuple):
            return tuple(cut(x) for x in v)
        if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
            return [cut(x) for x in v]
        return v[b:e]
    return {k: cut(v) for k, v in aux.items()}


def evolve(base, candles, pre, aux, prefix):
    import random
    rand_g, clamp, mutate, cross = e4.ga_tools(GENES12)
    cache = {}

    def score(g):
        key = tuple(round(g[k], 4) if not GENES12[k][2] else g[k]
                    for k in GENES12)
        if key not in cache:
            r = e2.run5(candles, pre, g, entry_filter=make_filter12(g, aux))
            cache[key] = fitness12(r)
        return cache[key]

    # осмысленные семена: база; база с ADX-гейтом; с трейлингом; без be_move
    seeds = [dict(base),
             dict(base, adx_gate=1, adx_idx=1, adx_max=25.0),
             dict(base, adx_gate=1, adx_idx=2, adx_max=20.0),
             dict(base, trail_k=0.5, trail_start=0.5),
             dict(base, be_move=1 - base.get("be_move", 0))]
    pop = [clamp(s) for s in seeds]
    while len(pop) < POP:
        pop.append(rand_g())
    scored = sorted(((score(g), g) for g in pop), key=lambda x: -x[0])
    for gen in range(GENS):
        new = [g for _, g in scored[:ELITE]]
        while len(new) < POP:
            if random.random() < 0.3:
                new.append(mutate(scored[random.randrange(ELITE)][1]))
            else:
                a = max(random.sample(scored, 3), key=lambda x: x[0])[1]
                b = max(random.sample(scored, 3), key=lambda x: x[0])[1]
                new.append(mutate(cross(a, b)))
        scored = sorted(((score(g), g) for g in new), key=lambda x: -x[0])
        if (gen + 1) % 6 == 0:
            print(f"  {prefix} поколение {gen+1:2}: best {scored[0][0]:+.3f}",
                  flush=True)
    return scored


def run_symbol(sym, aux_builder):
    print(f"\n================ {sym} (v12, свой GA) ================", flush=True)
    candles = ev.fetch(sym, "15", DAYS)
    aux = aux_builder(sym, candles)
    pre_full = e2.prep(candles)
    base = e7.cfg_to_genome(config.SYMBOL_PARAMS[sym]["final"], "final")
    for k, v in OFF12.items():
        base.setdefault(k, v)
    _, clamp, _, _ = e4.ga_tools(GENES12)
    base = clamp(base)
    folds = e4.fold_bounds_3y(len(candles))

    oos = [(candles[b:e], e2.prep(candles[b:e]), slice_aux(aux, b, e))
           for (a, b, e) in folds]

    def agg(g):
        sc = [oos_score12(e2.run5(seg, pre_s, g,
                                  entry_filter=make_filter12(g, aux_s)))
              for seg, pre_s, aux_s in oos]
        return sum(sc) / len(sc), sc

    base_mean, base_sc = agg(base)
    print(f"БАЗА: OOS {base_mean:+.3f} {['%+.2f' % s for s in base_sc]}",
          flush=True)

    cands = []
    for fi, (a, b, e) in enumerate(folds):
        train = candles[a:b]
        scored = evolve(base, train, e2.prep(train), slice_aux(aux, a, b),
                        f"{sym[:3]}-f{fi+1}")
        seen = set()
        for _, g in scored:
            key = tuple(g[k] for k in GENES12)
            if key not in seen:
                seen.add(key)
                cands.append(g)
            if len(seen) == 3:
                break

    best = None
    for g in cands:
        m, sc = agg(g)
        if best is None or m > best[0]:
            best = (m, sc, g)
    mean, sc, g_win = best
    print(f"ЛУЧШИЙ: OOS {mean:+.3f} {['%+.2f' % s for s in sc]}", flush=True)
    return dict(base_oos=base_mean, cand_oos=mean, cand_folds=sc,
                genome=g_win, base_genome=base,
                adopt=mean > base_mean + MIN_EDGE)


def main():
    syms = sys.argv[1:] or SYMS
    pct5 = xd.fetch_daily_pct5()
    aux_builder = make_aux_builder(pct5, 96)

    t0 = time.time()
    results = {sym: run_symbol(sym, aux_builder) for sym in syms}

    final = {}
    for sym, rec in results.items():
        candles = ev.fetch(sym, "15", DAYS)
        pre = e2.prep(candles)
        aux = aux_builder(sym, candles)
        g = rec["genome"] if rec["adopt"] else rec["base_genome"]

        ladder = []
        for lev in LEVS:
            row = full_run(g, candles, pre, aux, lev)
            row["lev"] = lev
            ladder.append(row)
        ok_rows = [r for r in ladder
                   if r["dd"] <= DD_CAP and not r["ruined"] and r["ret"] > 0]
        pick = max(ok_rows, key=lambda r: r["ret"]) if ok_rows else \
            max(ladder, key=lambda r: r["ret"])

        reasons = []
        if pick["ret"] <= 0:
            reasons.append(f"итог {pick['ret']:+.2f}% <= 0")
        if pick["dd"] > DD_CAP or pick["ruined"]:
            reasons.append(f"просадка {pick['dd']:.1f}% > {DD_CAP:.0f}%"
                           + (" (слив)" if pick["ruined"] else ""))
        if rec["cand_oos"] <= 0 or rec["cand_oos"] <= rec["base_oos"] + MIN_EDGE:
            reasons.append(f"OOS {rec['cand_oos']:+.2f} не даёт отрыва от базы "
                           f"{rec['base_oos']:+.2f}")
        if min(rec.get("cand_folds", [0])) < FOLD_FLOOR:
            reasons.append(f"худший экзамен {min(rec['cand_folds']):+.2f} < "
                           f"{FOLD_FLOOR}")
        if pick["loss_share"] < MIN_LOSS_SHARE:
            reasons.append("подозрительно мало убыточных сделок")
        if pick["trades"] < MIN_TRADES:
            reasons.append(f"сделок {pick['trades']} < {MIN_TRADES}")
        accept = not reasons

        print(f"\n{sym}: x{pick['lev']} {pick['ret']:+.2f}% | DD {pick['dd']:.1f}% "
              f"| WR {pick['wr']:.1f}% | сделок {pick['trades']} | "
              f"ПРИНЯТ: {'ДА' if accept else 'нет — ' + '; '.join(reasons)}")
        adx_used = g.get("adx_gate", 0)
        print(f"  ADX-гейт: {'ВКЛЮЧЁН, n=' + str(ADX_SET[int(g.get('adx_idx',1))]) + ', порог %.1f' % g.get('adx_max',30) if adx_used else 'эволюция не взяла'}"
              f" | грид-гены: { {k: round(g.get(k,v),3) if isinstance(g.get(k,v),float) else g.get(k,v) for k,v in gridlib.OFF10.items() if g.get(k,v)!=v} or 'все OFF'}")
        final[sym] = dict(genome=g, accept=accept, reject_reasons=reasons,
                          pick=pick, ladder=ladder,
                          base_oos=rec["base_oos"], cand_oos=rec["cand_oos"],
                          cfg=genome_to_cfg(g, pick["lev"]) if accept else None)

    with open("evolution12_final.json", "w", encoding="utf-8") as fh:
        json.dump(final, fh, ensure_ascii=False, indent=2, default=float)
    print(f"\n=== ИТОГ v12 за {time.time()-t0:.0f}с — evolution12_final.json ===")
    for sym, r in final.items():
        p = r["pick"]
        print(f"{sym:<9} {'ПРИНЯТ' if r['accept'] else 'отклонён':<9} "
              f"x{p['lev']:<3} {p['ret']:+9.2f}% | DD {p['dd']:5.1f}% | "
              f"WR {p['wr']:5.1f}% | сделок {p['trades']}")


if __name__ == "__main__":
    main()
