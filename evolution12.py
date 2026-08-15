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
  3) балл экзаменационного окна > 0 и > базового + 0.5 (было «средний OOS»:
     среднее трёх окон и было той самой утечкой, см. пункт 7 ниже);
  4) худшее из ЧЕСТНЫХ окон кандидата > -0.5. Честные — это окно валидации и
     все последующие, включая экзаменационное; окна, лежащие внутри обучения
     кандидата, сюда не входят. Формулировка «худший из трёх экзаменов»
     относилась к протекавшей схеме, а в починенной успела выродиться в список
     из одного элемента — того самого балла, который уже проверен правилом 3,
     то есть правило не могло сработать вовсе;
  5) доля убыточных сделок >= 1% (урок v8/v10: почти нулевые убытки — дыра);
  6) сделок >= 60 (иначе статистика — шум).
Не прошёл — монета честно остаётся убыточной, без натяжек.

Правки протокола (08.2026), после аудита:
  7) walk-forward больше не течёт: кандидат оценивается только на окнах СТРОГО
     после своего обучения, победитель выбирается по окну валидации, а приёмка
     смотрит на другое, экзаменационное окно (e4.choose_winner);
  8) плечо выбирается лестницей по ОБУЧАЮЩЕЙ части истории (а не по всей) и по
     ПЛАВАЮЩЕЙ просадке; выбирается ДО оценки, и на нём считаются фитнес,
     валидация, экзамен и ворота. Раньше баллы считались на x5, а лестница и
     ворота — на выбранном плече (у SOL это x10): проверялась одна стратегия,
     а запускалась другая;
  9) добавлены ворота honest_eval — хвост (худший месяц, худшая сделка,
     ликвидации, плавающая просадка, риск руина) и вырожденность (минимум
     сделок, сделок в месяц, экспозиции). Пункты 1-6 их не ловили.
Третий круг:
 10) ВЫБОР победителя пересчитывается на плече, которое уходит в конфиг:
     плечо отбора бралось по базовому геному, а у победителя своё, и раньше
     пересчитывался только экзамен — отбирался конфиг, оптимальный для одного
     плеча, а торговал он другим. Фитнес GA остаётся на плече отбора
     сознательно: он решает, кого предложить, а не чем мерить (см. run_symbol);
 11) вырожденные кандидаты выбывают ДО argmax (e4.choose_winner): штрафы
     fitness12/oos_score12 действовали внутри GA и на баллах окон, а сам выбор
     их не спрашивал;
 12) evolution12_final.json прошлого прогона больше не затирается
     (e4.save_artifact).
Всё это действует на БУДУЩИЕ прогоны: конфиги в config.py отобраны старой,
протекавшей схемой, и переотбор — отдельная работа на часы счёта.

Запуск: python evolution12.py            (все три монеты)
        python evolution12.py SOLUSDT    (одна)
"""

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
    ddf = r.get("max_dd_float")
    return dict(ret=round((r["balance"] / e2.START - 1) * 100, 2),
                dd=round(r["max_dd"] * 100, 2),
                # плавающая просадка: именно по ней выбирается плечо —
                # закрытая не видит переоценки открытой сетки и занижена
                dd_float=(round(ddf * 100, 2) if ddf is not None else None),
                trades=n,
                wr=round(w / n * 100, 1) if n else 0.0,
                loss_share=round((n - w) / n * 100, 2) if n else 0.0,
                ruined=r["ruined"])


def pick_lev12(rows, dd_cap=DD_CAP):
    """Плечо волны v12: из ступеней, укладывающихся в просадку и прибыльных, —
    самая доходная. Возвращает (lev, ok, warns).

    Два места, где раньше было молчание:
      * просадка бралась закрытая (r["max_dd"]) — теперь плавающая;
      * если НИ ОДНА ступень не проходила порог, молча бралась самая доходная
        из непрошедших, и «рекомендация» выглядела как проверенная. Теперь
        ok=False и предупреждение словами.
    """
    warns = []
    if any(r.get("dd_float") is None for r in rows):
        warns.append("плавающая просадка НЕ ИЗМЕРЕНА (движок не отдал "
                     "max_dd_float): плечо выбрано по просадке ЗАКРЫТЫХ "
                     "сделок, она заведомо занижена")

    def dd_of(r):
        return r["dd_float"] if r.get("dd_float") is not None else r["dd"]

    ok_rows = [r for r in rows
               if dd_of(r) <= dd_cap and not r["ruined"] and r["ret"] > 0]
    if ok_rows:
        return dict(lev=max(ok_rows, key=lambda r: r["ret"])["lev"], ok=True,
                    warns=warns, rows=rows)
    worst = "/".join(f"x{r['lev']}:{dd_of(r):.1f}%/{r['ret']:+.1f}%"
                     for r in rows)
    warns.append(f"ни одна ступень не проходит порог (просадка <= {dd_cap:.0f}%"
                 f" и итог > 0): {worst} — выдана самая доходная из "
                 f"непрошедших, это НЕ подтверждённая рекомендация")
    return dict(lev=max(rows, key=lambda r: r["ret"])["lev"], ok=False,
                warns=warns, rows=rows)


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


def evolve(base, candles, pre, aux, prefix, lev=None):
    """lev — плечо, на котором считается фитнес. Оно же идёт в экзамен, ворота
    и конфиг: считать отбор на x5, а торговать на x10 — значит проверять не ту
    стратегию, которую запускаешь."""
    import random
    rand_g, clamp, mutate, cross = e4.ga_tools(GENES12)
    cache = {}

    def score(g):
        key = tuple(round(g[k], 4) if not GENES12[k][2] else g[k]
                    for k in GENES12)
        if key not in cache:
            old, e2.LEV = e2.LEV, (lev or e2.LEV)
            try:
                r = e2.run5(candles, pre, g, entry_filter=make_filter12(g, aux))
            finally:
                e2.LEV = old
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

    # --- плечо, на котором идёт ВСЁ: фитнес, валидация, экзамен, ворота ------
    # Раньше отбор и экзамен считались на e2.LEV (x5), а лестница приёмки и
    # ворота — на выбранном плече (у SOL это x10). Сравнивали одну стратегию, а
    # запускали другую. Теперь плечо выбирается ДО оценки — по базовому геному
    # на обучающей части, — а после выбора победителя уточняется по его
    # собственной лестнице, и все баллы пересчитываются на нём.
    cut = e4.train_end_bar(len(candles))
    train_all, pre_train = candles[:cut], e2.prep(candles[:cut])
    aux_train = slice_aux(aux, 0, cut)

    def lev_for(g, why):
        rows = []
        for lev in LEVS:
            row = full_run(g, train_all, pre_train, aux_train, lev)
            row["lev"] = lev
            rows.append(row)
        ch = pick_lev12(rows)
        print(f"  плечо по обучающей части ({why}): x{ch['lev']} | "
              + "/".join(f"x{r['lev']}:"
                         + ("н/д" if r["dd_float"] is None
                            else f"{r['dd_float']:.1f}%")
                         + f"/{r['ret']:+.1f}%" for r in rows), flush=True)
        for w in ch["warns"]:
            print(f"    ВНИМАНИЕ: {w}", flush=True)
        return ch

    lev_ch = lev_for(base, "база")
    lev_sel = lev_ch["lev"]

    _win_cache = {}

    def _run_win(g, wi, lev):
        """Прогон одного окна на одном плече -> (балл, сделки), с памятью.

        Память нужна для однозначности: выбор победителя пересчитывается на
        нескольких плечах (см. ниже), и один геном на одном окне обязан давать
        один и тот же балл независимо от порядка вызовов.
        """
        key = (tuple(g[k] for k in GENES12), wi, lev)
        if key not in _win_cache:
            seg, pre_s, aux_s = oos[wi]
            old, e2.LEV = e2.LEV, lev
            try:
                r = e2.run5(seg, pre_s, g, entry_filter=make_filter12(g, aux_s))
            finally:
                e2.LEV = old
            _win_cache[key] = (oos_score12(r), r["trades"])
        return _win_cache[key]

    def score_on(g, wi, lev=None):
        """Балл одного окна на ЗАДАННОМ плече. Раньше здесь была agg() по всем
        трём окнам сразу — та же утечка, что в общем харнессе e4 (для кандидата
        позднего фолда первые окна лежат внутри его обучающей выборки), и вдобавок
        плечо было чужое: x5 против боевого."""
        return _run_win(g, wi, lev or lev_sel)[0]

    def trades_on(g, wi, lev=None):
        """Сделки кандидата на окне — вход для отсева вырожденных геномов.

        В этой волне вырожденность уже ловили дважды (fitness12 и oos_score12
        штрафуют отказ от торговли), но оба штрафа действуют внутри GA и на
        баллах окон, а сам ВЫБОР победителя argmax'ом их не спрашивал.
        """
        return _run_win(g, wi, lev or lev_sel)[1]

    base_sc = [score_on(base, wi) for wi in range(len(oos))]
    base_mean = sum(base_sc) / len(base_sc)
    print(f"БАЗА (x{lev_sel}): OOS по окнам {['%+.2f' % s for s in base_sc]} "
          f"(среднее {base_mean:+.3f}, экзамен {base_sc[-1]:+.3f})", flush=True)

    base_key = tuple(base[k] for k in GENES12)
    cands = []
    for fi, (a, b, e) in enumerate(folds):
        train = candles[a:b]
        scored = evolve(base, train, e2.prep(train), slice_aux(aux, a, b),
                        f"{sym[:3]}-f{fi+1}", lev=lev_sel)
        seen = set()
        for _, g in scored:
            key = tuple(g[k] for k in GENES12)
            # база — точка отсчёта, а не соперник: она отобрана старой схемой
            # по всей истории, включая экзамен, и «победа базы над базой»
            # ничего не доказывает
            if key == base_key or key in seen:
                continue
            seen.add(key)
            cands.append((fi, g))       # фолд нужен, чтобы знать, какие окна
            if len(seen) == 3:          # для этого кандидата уже «просмотрены»
                break

    # --- выбор победителя на плече, которое уйдёт в конфиг -------------------
    # Плечо отбора lev_sel взято по БАЗОВОМУ геному, а у победителя своё. Пока
    # выбор делался один раз на lev_sel, отбирался конфиг, оптимальный для
    # одного плеча, а торговал он другим. Ищем неподвижную точку: выбор ->
    # плечо победителя -> при расхождении перевыбор на нём (баллы базы тоже
    # пересчитываются, иначе отрывы считаются на разных шкалах).
    # Фитнес GA остаётся на lev_sel: он решает, КОГО предложили, а не ЧЕМ его
    # меряют; перезапуск генетики на каждом плече — часы счёта и петля без
    # конца (новый пул -> новый победитель -> новое плечо).
    # lev_cur — плечо, на котором ФАКТИЧЕСКИ выбран нынешний pick, и на
    # последнем проходе его двигать нельзя: перевыбора уже не будет. Пока
    # обновление стояло в конце каждого прохода, после цикла lev_cur всегда
    # совпадал с плечом победителя, lev_settled выходил True при любом исходе,
    # а пересчёт экзамена на новом плече не срабатывал никогда — в json уезжали
    # баллы, посчитанные на ПРЕДЫДУЩЕМ плече, под видом баллов боевого.
    lev_cur, base_cur, pick, lev_win = lev_sel, base_sc, None, None
    for attempt in range(e4.LEV_PASSES):
        pick = e4.choose_winner(
            cands, base_cur,
            lambda g, wi, _l=lev_cur: score_on(g, wi, _l),
            trades_on=lambda g, wi, _l=lev_cur: trades_on(g, wi, _l))
        lev_win = lev_for(pick["genome"], f"победитель, проход {attempt+1}")
        if lev_win["lev"] == lev_cur:
            break
        print(f"  плечо победителя x{lev_win['lev']} != плеча выбора "
              f"x{lev_cur} — ПЕРЕВЫБОР победителя на x{lev_win['lev']}",
              flush=True)
        if attempt + 1 == e4.LEV_PASSES:
            break              # проходы кончились: pick выбран на lev_cur,
                               # расхождение с lev_win видно ниже
        lev_cur = lev_win["lev"]
        base_cur = [score_on(base, wi, lev_cur) for wi in range(len(oos))]
    g_win = pick["genome"]
    lev = lev_win["lev"]
    lev_settled = lev == lev_cur
    if not lev_settled:
        print(f"  ВНИМАНИЕ: плечо не устоялось за {e4.LEV_PASSES} прох. "
              f"(выбор шёл на x{lev_cur}, лестница победителя даёт x{lev}); "
              f"экзамен, ворота и конфиг считаются на x{lev}, но ВЫБИРАЛСЯ "
              f"кандидат на другом плече", flush=True)
    if lev != lev_cur:
        # пересчёт по ТОЙ ЖЕ метрике, которой выбирался победитель: в ветке с
        # утечкой это среднее по трём окнам, а не балл одного экзаменационного
        exam, base_exam = e4.exam_scores(pick, g_win, base, score_on, lev,
                                         len(oos))
        print(f"  плечо уточнено x{lev_cur} -> x{lev}: экзамен "
              f"{pick['exam_score']:+.3f} -> {exam:+.3f}, база "
              f"{pick['base_exam']:+.3f} -> {base_exam:+.3f}", flush=True)
    else:
        exam, base_exam = pick["exam_score"], pick["base_exam"]
    base_sc_final = (base_cur if lev == lev_cur
                     else [score_on(base, wi, lev) for wi in range(len(oos))])

    # Окна, честные для победителя: его валидационное и все последующие, включая
    # экзаменационное. Раньше сюда клался ОДИН элемент (сам балл экзамена), из-за
    # чего правило приёмки 4 («худший из экзаменов > -0.5») дублировало правило 3
    # и не могло добавить ни одной причины отказа.
    fi_win = pick["train_fold"]
    cand_folds = [score_on(g_win, wi, lev)
                  for wi in range(fi_win, len(oos))]
    print(f"ЛУЧШИЙ (x{lev}): экзамен {exam:+.3f} против базы {base_exam:+.3f} "
          f"(валидация окно "
          f"{'-' if pick['val_window'] is None else pick['val_window'] + 1}: "
          f"{pick['val_score']:+.3f}); честные окна кандидата "
          f"{['%+.2f' % s for s in cand_folds]}", flush=True)
    return dict(base_oos=base_exam, cand_oos=exam,
                cand_folds=cand_folds,
                base_folds=base_sc_final,
                base_mean_all=sum(base_sc_final) / len(base_sc_final),
                base_folds_at_lev_sel=base_sc, base_mean_at_lev_sel=base_mean,
                exam_window=pick["exam_window"], val_window=pick["val_window"],
                val_score=pick["val_score"], val_edge=pick["val_edge"],
                train_fold=pick["train_fold"],
                skipped_candidates=pick["skipped"],
                degenerate_candidates=pick["degenerate"],
                all_candidates_degenerate=pick["all_degenerate"],
                metric=pick["metric"],
                lev_val=lev_cur, lev_settled=lev_settled,
                lev_fitness_on=lev_sel,
                lev=lev, lev_sel=lev_sel, lev_confirmed=lev_win["ok"],
                lev_warnings=lev_win["warns"], ladder_train=lev_win["rows"],
                base_ladder_train=lev_ch["rows"],
                protocol=("leaky-3window-mean" if pick["leaky"]
                          else "honest-val-then-exam"),
                genome=g_win, base_genome=base,
                adopt=exam > base_exam + MIN_EDGE)


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

        # Плечо. Для принятого кандидата оно выбрано ВНУТРИ run_symbol — на нём
        # считались фитнес, валидация и экзамен, и ровно оно уходит в конфиг.
        # Для отклонённого в дело идёт база, и берётся её лестница (тоже по
        # обучающей части). Полная история — только отчёт.
        train = e4.train_slice(candles)
        ladder_train = (rec["ladder_train"] if rec["adopt"]
                        else rec["base_ladder_train"])
        lev_sel12 = (dict(lev=rec["lev"], ok=rec["lev_confirmed"],
                          warns=rec["lev_warnings"])
                     if rec["adopt"] else pick_lev12(ladder_train))
        lev_pick = lev_sel12["lev"]
        for w in lev_sel12["warns"]:
            print(f"  ВНИМАНИЕ: {w}")
        # полная лестница — только отчёт, уже ПОСЛЕ выбора плеча
        ladder = []
        for lev in LEVS:
            row = full_run(g, candles, pre, aux, lev)
            row["lev"] = lev
            ladder.append(row)
        pick = next(r for r in ladder if r["lev"] == lev_pick)

        # Ворота honest_eval на экзаменационном окне, на ВЫБРАННОМ плече:
        # правила 1-6 ниже говорят про итог и просадку по закрытым сделкам и
        # слепы к хвосту (худший месяц, худшая сделка, ликвидации, риск руина)
        # и к вырожденности (конфиг, который не торгует).
        folds = e4.fold_bounds_3y(len(candles))
        eb, ee = folds[e4.exam_window_index(len(folds))][1:]
        ex = candles[eb:ee]
        mm = he.measure(ex, e2.prep(ex), g,
                        make_filter12(g, slice_aux(aux, eb, ee)), lev_pick,
                        tag=f"{sym}/v12")
        gates_ok, gate_reasons, warns = he.verdict(mm)

        reasons = list(gate_reasons)
        if pick["ret"] <= 0:
            reasons.append(f"итог {pick['ret']:+.2f}% <= 0")
        if pick["dd"] > DD_CAP or pick["ruined"]:
            reasons.append(f"просадка {pick['dd']:.1f}% > {DD_CAP:.0f}%"
                           + (" (слив)" if pick["ruined"] else ""))
        if rec["cand_oos"] <= 0 or rec["cand_oos"] <= rec["base_oos"] + MIN_EDGE:
            reasons.append(f"OOS {rec['cand_oos']:+.2f} не даёт отрыва от базы "
                           f"{rec['base_oos']:+.2f}")
        # правило 4 смотрит на ВСЕ честные окна кандидата (валидационное и все
        # последующие), а не на одно экзаменационное: одно число уже проверено
        # правилом 3, и дублировать его — значит иметь мёртвое правило
        cand_folds = rec.get("cand_folds") or [0.0]
        if min(cand_folds) < FOLD_FLOOR:
            reasons.append(f"худшее из честных окон {min(cand_folds):+.2f} < "
                           f"{FOLD_FLOOR} (окна {['%+.2f' % s for s in cand_folds]})")
        if pick["loss_share"] < MIN_LOSS_SHARE:
            reasons.append("подозрительно мало убыточных сделок")
        if pick["trades"] < MIN_TRADES:
            reasons.append(f"сделок {pick['trades']} < {MIN_TRADES}")
        if not lev_sel12["ok"]:
            reasons.append(f"плечо x{lev_pick} не подтверждено: ни одна "
                           f"ступень лестницы не проходит порог просадки")
        warns = list(warns) + lev_sel12["warns"]
        accept = not reasons

        print(f"\n{sym}: x{pick['lev']} {pick['ret']:+.2f}% | DD {pick['dd']:.1f}% "
              f"| WR {pick['wr']:.1f}% | сделок {pick['trades']} | "
              f"ПРИНЯТ: {'ДА' if accept else 'нет — ' + '; '.join(reasons)}")
        print(f"  плечо x{lev_pick} выбрано по обучающей части "
              f"({len(train)} баров из {len(candles)})"
              f"{'' if lev_sel12['ok'] else ' — НЕ ПОДТВЕРЖДЕНО просадкой'}; "
              f"на нём же считались фитнес, валидация, экзамен и ворота")
        e4.gate_report(mm, gate_reasons, warns)
        adx_used = g.get("adx_gate", 0)
        print(f"  ADX-гейт: {'ВКЛЮЧЁН, n=' + str(ADX_SET[int(g.get('adx_idx',1))]) + ', порог %.1f' % g.get('adx_max',30) if adx_used else 'эволюция не взяла'}"
              f" | грид-гены: { {k: round(g.get(k,v),3) if isinstance(g.get(k,v),float) else g.get(k,v) for k,v in gridlib.OFF10.items() if g.get(k,v)!=v} or 'все OFF'}")
        final[sym] = dict(genome=g, accept=accept, reject_reasons=reasons,
                          warnings=warns, exam_measure={k: v for k, v in
                                                        mm.items() if k != "rs"},
                          pick=pick, ladder=ladder, ladder_train=ladder_train,
                          lev_picked_on="train", lev=lev_pick,
                          lev_confirmed=lev_sel12["ok"],
                          # экзамен и ворота считались на этом же плече
                          exam_lev=lev_pick,
                          cand_folds=rec.get("cand_folds"),
                          protocol=rec.get("protocol"),
                          base_oos=rec["base_oos"], cand_oos=rec["cand_oos"],
                          cfg=genome_to_cfg(g, pick["lev"]) if accept else None)

    # артефакт прошлого прогона не переписывается (e4.save_artifact)
    out_name = e4.save_artifact("evolution12_final.json", final)
    print(f"\n=== ИТОГ v12 за {time.time()-t0:.0f}с — {out_name} ===")
    for sym, r in final.items():
        p = r["pick"]
        print(f"{sym:<9} {'ПРИНЯТ' if r['accept'] else 'отклонён':<9} "
              f"x{p['lev']:<3} {p['ret']:+9.2f}% | DD {p['dd']:5.1f}% | "
              f"WR {p['wr']:5.1f}% | сделок {p['trades']}")


if __name__ == "__main__":
    main()
