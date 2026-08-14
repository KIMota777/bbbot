# -*- coding: utf-8 -*-
"""Эволюция v4: биржевые прокси китов (funding, OI) + фонда/золото.
Данные: 1150 дней (~3.2 года) 15m. Walk-forward: 3 фолда
(train 18м -> OOS 6м; 24 -> 6; 30 -> 8).

ВНИМАНИЕ (правка 08.2026). Здесь же живёт общий харнесс walk-forward для всех
поздних волн (v5-v12). В нём была утечка: кандидат оценивался на всех трёх
OOS-окнах, включая те, что лежат внутри его собственной обучающей выборки, и
победитель выбирался по тому же числу, по которому потом шла приёмка.
Схема исправлена (см. LEAKY_WALKFORWARD ниже). Вторая правка того же круга:
ПЛЕЧО. Отбор шёл на e2.LEV (x5), а в конфиг уходило плечо до x15, выбранное
после приёмки, — то есть балл экзамена и ворота риска описывали стратегию,
которой не торгуют. Теперь плечо выбирается ДО оценки (лестница по обучающей
части, ПЛАВАЮЩАЯ просадка, choose_leverage), на нём считаются фитнес,
валидация, экзамен и ворота, и ровно оно уходит дальше.

Правки действуют ТОЛЬКО НА БУДУЩИЕ прогоны: конфиги, которые сейчас стоят в
config.py, отобраны по старой схеме, и никакая правка протокола их задним
числом не оправдывает — чтобы получить честные конфиги, нужен полный переотбор
(часы счёта).

Новые гены (все с состоянием "выкл"):
  fund_long_max  (-0.05..0.06): лонг только при funding <= порога
                 (отрицательный funding = шорты платят = сигнал дна). 0.06=выкл.
  fund_short_min (-0.06..0.05): шорт только при funding >= порога. -0.06=выкл.
  oi_gate        (0..2): 1 = лонг при падающем OI за 24ч / шорт при растущем
                 (делевередж); 2 = наоборот; 0 = выкл.
  spx_long_min   (-6..0): лонг запрещён, если S&P за 5д упал ниже порога. -6=выкл.
  dxy_long_max   (0..4):  лонг запрещён, если DXY за 5д вырос выше порога. 4=выкл.
  gold_long_max  (0..6):  лонг запрещён, если золото за 5д выросло выше порога
                 (бегство в защиту). 6=выкл.
"""

import json
import random
import time

import evolution as ev
import evolution2 as e2
import ext_data as xd
import honest_eval as he

random.seed(45)

SYMBOLS = ["DOGEUSDT", "LTCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
POP, GENS, ELITE = 40, 16, 5
DAYS = 1150
MIN_EDGE = 0.5          # осмысленный отрыв от базы на экзамене, не шум
EVALS = [0]             # сколько геномов реально оценено за прогон монеты.
                        # Нужен поправке на множественность: чем шире перебор,
                        # тем выше планка для «находки». Считать по числу монет
                        # или волн — самообман (REVIEW.md, урок 4).

GENES4 = dict(e2.GENES)
GENES4.update({
    "fund_long_max":  (-0.05, 0.06, False),
    "fund_short_min": (-0.06, 0.05, False),
    "oi_gate":        (0, 2, True),
    "spx_long_min":   (-6.0, 0.0, False),
    "dxy_long_max":   (0.0, 4.0, False),
    "gold_long_max":  (0.0, 6.0, False),
})
OFF4 = dict(fund_long_max=0.06, fund_short_min=-0.06, oi_gate=0,
            spx_long_min=-6.0, dxy_long_max=4.0, gold_long_max=6.0)

# действующие конфиги (после v2/v3) в генах движка
CURRENT = {
    "DOGEUSDT": dict(rsi_idx=2, rsi_os=35, zone_l=0.28, zone_s=0.28, window=518,
                     step=0.011, levels=3, mult=1.8, tp=0.038, sweep=0.020,
                     max_bars=205, cooldown=0, knife=0.0, be_move=0),
    "LTCUSDT": dict(rsi_idx=2, rsi_os=34, zone_l=0.25, zone_s=0.25, window=377,
                    step=0.008, levels=3, mult=1.2, tp=0.020, sweep=0.020,
                    max_bars=160, cooldown=0, knife=0.0, be_move=0),
    "BTCUSDT": dict(rsi_idx=2, rsi_os=23, zone_l=0.40, zone_s=0.18, window=877,
                    step=0.004, levels=2, mult=2.0, tp=0.021, sweep=0.020,
                    max_bars=243, cooldown=30, knife=3.5, be_move=0),
    "ETHUSDT": dict(rsi_idx=2, rsi_os=25, zone_l=0.50, zone_s=0.41, window=724,
                    step=0.007, levels=3, mult=1.6, tp=0.010, sweep=0.025,
                    max_bars=139, cooldown=48, knife=3.5, be_move=0),
    "SOLUSDT": dict(rsi_idx=2, rsi_os=30, zone_l=0.25, zone_s=0.25, window=400,
                    step=0.010, levels=3, mult=1.5, tp=0.020, sweep=0.020,
                    max_bars=192, cooldown=0, knife=0.0, be_move=0),
}


def make_filter4(g, aux):
    fund, oi_chg = aux["fund"], aux["oi_chg"]
    spx5, dxy5, gold5 = aux["spx5"], aux["dxy5"], aux["gold5"]

    def f(side, i):
        v = fund[i]
        if v is not None:
            if side == "L" and v > g["fund_long_max"]:
                return None
            if side == "S" and v < g["fund_short_min"]:
                return None
        o = oi_chg[i]
        if o is not None and g["oi_gate"]:
            fall = o < 0
            ok_long = fall if g["oi_gate"] == 1 else not fall
            if side == "L" and not ok_long:
                return None
            if side == "S" and ok_long:
                return None
        if side == "L":
            s = spx5[i]
            if s is not None and s < g["spx_long_min"]:
                return None
            d = dxy5[i]
            if d is not None and d > g["dxy_long_max"]:
                return None
            au = gold5[i]
            if au is not None and au > g["gold_long_max"]:
                return None
        return side

    return f


def ga_tools(genes):
    def rand_g():
        return {k: (int(round(random.uniform(lo, hi))) if ii
                    else random.uniform(lo, hi))
                for k, (lo, hi, ii) in genes.items()}

    def clamp(g):
        return {k: (int(round(min(hi, max(lo, g[k])))) if ii
                    else min(hi, max(lo, g[k])))
                for k, (lo, hi, ii) in genes.items()}

    def mutate(g):
        out = dict(g)
        for k, (lo, hi, ii) in genes.items():
            if random.random() < 0.25:
                out[k] = out[k] + random.gauss(0, 0.15 * (hi - lo))
        return clamp(out)

    def cross(a, b):
        return clamp({k: (a[k] if random.random() < 0.5 else b[k])
                      for k in genes})
    return rand_g, clamp, mutate, cross


def evolve_ext(candles, pre, aux, seeds, genes, make_f, prefix, lev=None):
    """lev — плечо, на котором считается фитнес. По умолчанию e2.LEV (x5).

    Плечо здесь не косметика: и фитнес, и балл экзамена, и ворота риска обязаны
    считаться на ОДНОМ плече — том, которое потом уходит в конфиг. Раньше отбор
    шёл на x5, а торговали до x15, и цифры описывали другую стратегию.
    """
    rand_g, clamp, mutate, cross = ga_tools(genes)
    cache = {}

    def eval_g(g):
        key = tuple(round(g[k], 4) if not genes[k][2] else g[k] for k in genes)
        if key not in cache:
            old, e2.LEV = e2.LEV, (lev or e2.LEV)
            try:
                r = e2.run5(candles, pre, g, entry_filter=make_f(g, aux))
            finally:
                e2.LEV = old
            cache[key] = e2.fitness(r)
            EVALS[0] += 1
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


def fold_bounds_3y(n):
    """train 18м/24м/30м -> OOS 6/6/8м при ~38.4м данных."""
    total_m = 38.4
    return [(0, int(n * 18 / total_m), int(n * 24 / total_m)),
            (0, int(n * 24 / total_m), int(n * 30 / total_m)),
            (0, int(n * 30 / total_m), n)]


# --------------------------------------------------------- честный walk-forward
#
# Что было не так. Фолды anchored: обучение всегда начинается с нуля, поэтому
# train фолда 3 — это [0..30м), и окна 18-24 и 24-30 лежат ВНУТРИ него. Старый
# харнесс оценивал каждого кандидата на всех трёх окнах и брал argmax по
# среднему: для позднего кандидата два «экзамена» из трёх были экзаменом по
# собственному конспекту. И то же самое среднее решало, принимать ли конфиг, —
# одно число служило и выбором, и проверкой выбора. Проверяется так не
# стратегия, а везучесть перебора.
#
# Как теперь. Роли окон разведены:
#   * ВАЛИДАЦИЯ — первое окно строго ПОСЛЕ обучающего периода кандидата (для
#     фолда fi это ровно fi-е OOS-окно). Только по нему выбирается победитель,
#     и не по абсолютному баллу, а по отрыву от базы на ТОМ ЖЕ окне: окна
#     разной длины и разной сложности, абсолютные баллы между ними несравнимы;
#   * ЭКЗАМЕН — последнее окно. В выборе не участвует вообще, поэтому кандидаты
#     последнего фолда из гонки выбывают: их валидацией и было бы это окно, и
#     экзаменовать их оказалось бы нечем, кроме будущего. Это осознанная плата
#     — треть кандидатов в обмен на то, что цифра приёмки означает то, что
#     написано.
#
# Кандидатов последнего фолда не выбрасываем молча: их число печатается и
# попадает в результат (skipped), иначе разница с прошлыми волнами выглядела бы
# необъяснимой.
LEAKY_WALKFORWARD = False   # True = историческая НАРЕЗКА окон с утечкой.
                            # Флаг воспроизводит ровно одно: как выбирался
                            # победитель (все три окна каждому кандидату,
                            # argmax по среднему). ПРОШЛУЮ ВОЛНУ ЦЕЛИКОМ ОН НЕ
                            # ПОВТОРЯЕТ и не обещает: ворота honest_eval,
                            # лестница плеча по обучающей части, выбор плеча до
                            # оценки и отсев базы из кандидатов работают в
                            # ОБЕИХ ветках. Значит числа с флагом не совпадут с
                            # *_winners.json до 08.2026 — те файлы остаются
                            # единственным артефактом старых волн.
                            # Для отбора боевых конфигов НЕ ИСПОЛЬЗОВАТЬ.


def exam_window_index(n_folds):
    """Индекс экзаменационного (последнего) OOS-окна."""
    return n_folds - 1


def train_end_bar(n):
    """Первый бар экзаменационного окна = конец всего, что можно трогать.

    Всё, что левее, — обучение и валидация; всё, что правее, — экзамен. Функция
    нужна не только генетике: по ней же режется история для выбора ПЛЕЧА (см.
    train_slice).
    """
    folds = fold_bounds_3y(n)
    return folds[exam_window_index(len(folds))][1]


def train_slice(candles):
    """Часть истории, доступная отбору (без экзаменационного окна).

    Зачем отдельная функция. Плечо в волнах v7/v11/v12 выбиралось лестницей
    x5..x15 на ПОЛНОЙ истории — то есть риск-параметр подгонялся в том числе по
    экзаменационному окну, а потом на этом же окне сдавался экзамен. Утечка
    ровно та же, что и в walk-forward, только через другую дверь. Лестница для
    ВЫБОРА плеча считается здесь, на обучающей части; на полной истории её
    можно только ПОКАЗАТЬ, уже после того как плечо выбрано.
    """
    return candles[:train_end_bar(len(candles))]


# ------------------------------------------------------------ выбор плеча
#
# Плечо — такой же подгоняемый параметр, как ген, и вдобавок множитель риска.
# Две ошибки протокола, которые здесь закрыты:
#   * лестница считалась по ЗАКРЫТОЙ просадке (r["max_dd"]). Она не видит
#     переоценки открытой сетки, а именно недоливка по открытой позиции и
#     убивает счёт. Берём max_dd_float;
#   * когда порог не проходила НИ ОДНА ступень, функция молча возвращала
#     минимальное плечо, и «рекомендация x5» выглядела так же, как честно
#     прошедшая проверку. Теперь такой случай помечается ok=False и словами.
LEVS = [5, 8, 10, 12, 15]   # та же лестница, что в v7/v10/v11/v12
DD_CAP = 0.20               # порог просадки (решение владельца, не подгонять)


def ladder_rows(candles, pre, g, filt, levs=None):
    """Лестница плечей по одному куску истории. Плавающая просадка кладётся
    рядом с закрытой, чтобы выбор плеча смотрел на ту, что больше."""
    out = []
    for lev in (levs or LEVS):
        old, e2.LEV = e2.LEV, lev
        try:
            r = e2.run5(candles, pre, g, entry_filter=filt)
        finally:
            e2.LEV = old
        ddf = r.get("max_dd_float")
        out.append(dict(lev=lev,
                        ret=round((r["balance"] / e2.START - 1) * 100, 2),
                        dd=round(r["max_dd"] * 100, 2),
                        dd_float=(round(ddf * 100, 2)
                                  if ddf is not None else None),
                        ruined=r["ruined"], trades=r["trades"]))
    return out


def choose_leverage(rows, dd_cap=DD_CAP):
    """Наибольшее плечо с ПЛАВАЮЩЕЙ просадкой <= dd_cap и без слива.

    Возвращает dict(lev, ok, warns): ok=False означает «порог не прошла ни
    одна ступень, плечо выдано как минимально возможное». Молчать об этом
    нельзя — рекомендация без проверки и рекомендация после проверки должны
    выглядеть по-разному.
    """
    cap = dd_cap * 100
    warns, best, ok = [], rows[0]["lev"], False
    if not any(r.get("trades") for r in rows):
        # конфиг без сделок даёт просадку 0% на любой ступени и «проходит»
        # порог на x15 — плечо для того, кто не торгует, не значит ничего
        return dict(lev=rows[0]["lev"], ok=False, rows=rows,
                    warns=["конфиг не торгует на обучающей части: лестница "
                           "плечей пуста (просадка 0% на всех ступенях), "
                           "плечо НЕ ВЫБРАНО, выдано минимальное"])
    if any(r.get("dd_float") is None for r in rows):
        warns.append("плавающая просадка НЕ ИЗМЕРЕНА (движок не отдал "
                     "max_dd_float): плечо выбрано по просадке ЗАКРЫТЫХ "
                     "сделок, а она заведомо занижена")
    for row in rows:
        dd = row["dd_float"] if row.get("dd_float") is not None else row["dd"]
        if dd <= cap and not row.get("ruined"):
            best, ok = row["lev"], True
    if not ok:
        worst = "/".join(
            f"x{r['lev']}:"
            f"{(r['dd_float'] if r.get('dd_float') is not None else r['dd']):.1f}%"
            for r in rows)
        warns.append(f"ни одна ступень не укладывается в просадку "
                     f"{cap:.0f}% ({worst}) — выдано минимальное плечо "
                     f"x{best}, но это НЕ подтверждённая рекомендация")
    return dict(lev=best, ok=ok, warns=warns, rows=rows)


def choose_winner(cands, base_sc, score_on, leaky=None, log=print):
    """Победитель по честному walk-forward.

    cands    — [(fi, g)]: номер фолда, на котором геном обучался, и сам геном;
    base_sc  — баллы базового генома по всем OOS-окнам (нужны для нормировки:
               сравниваем не баллы разных окон, а отрывы от базы на своём);
    score_on — score_on(g, wi) -> балл генома g на OOS-окне wi.

    Возвращает dict: победитель, окно валидации, окно экзамена и оба балла.
    Балл валидации и балл экзамена — РАЗНЫЕ числа с разных окон; принимающая
    сторона обязана смотреть на второе.
    """
    if leaky is None:
        leaky = LEAKY_WALKFORWARD
    n = len(base_sc)
    exam = exam_window_index(n)
    if leaky:
        # историческая схема: все окна всем кандидатам, argmax по среднему
        best = None
        for fi, g in cands:
            sc = [score_on(g, wi) for wi in range(n)]
            m = sum(sc) / n
            if best is None or m > best[0]:
                best = (m, sc, g, fi)
        m, sc, g, fi = best
        log("  ВНИМАНИЕ: включена историческая НАРЕЗКА окон С УТЕЧКОЙ — "
            "кандидат оценивается в том числе на окнах внутри собственного "
            "обучения. Это не проверка стратегии. И это НЕ полное "
            "воспроизведение прошлых волн: ворота honest_eval, выбор плеча по "
            "обучающей части и отсев базы из кандидатов работают и здесь, "
            "поэтому числа со старыми *_winners.json не совпадут")
        return dict(genome=g, train_fold=fi, leaky=True, val_window=None,
                    exam_window=exam, folds=sc, val_score=m, val_edge=None,
                    exam_score=m, base_exam=sum(base_sc) / n, skipped=0,
                    considered=len(cands))
    pool = [(fi, g) for fi, g in cands if fi < exam]
    skipped = len(cands) - len(pool)
    if not pool:
        raise ValueError("нет кандидатов, у которых экзаменационное окно лежит "
                         "за пределами обучения — отбирать не из чего")
    best = None
    for fi, g in pool:
        edge = score_on(g, fi) - base_sc[fi]
        if best is None or edge > best[0]:
            best = (edge, fi, g)
    edge, fi, g = best
    exam_score = score_on(g, exam)
    log(f"  выбор по валидации: окно {fi+1} (обучение фолда {fi+1}), "
        f"отрыв от базы {edge:+.3f}; кандидатов в гонке {len(pool)}, "
        f"отброшено как неэкзаменуемых {skipped}")
    return dict(genome=g, train_fold=fi, leaky=False, val_window=fi,
                exam_window=exam, val_edge=edge,
                val_score=edge + base_sc[fi], exam_score=exam_score,
                base_exam=base_sc[exam], skipped=skipped,
                considered=len(cands))


def honesty_report(g, genes, clamp, rand_g, evaluate, cand_score, log=print,
                   trades_of=None):
    """Диагностика подгонки на экзаменационном окне (инструменты REVIEW.md).

    Это НЕ ворота: пороги для них владелец не задавал, а придумывать их после
    прогона — тот самый самообман, против которого весь этот файл. Но и читать
    балл экзамена в одиночку нельзя: победитель стоит на вершине, найденной
    перебором из EVALS[0] попыток, и три вопроса к нему обязательны.
      * узкая ли вершина — медиана соседей;
      * лучше ли он случайного входа — контроль случайными геномами;
      * какой планки требует объём перебора — поправка Бонферрони.
    """
    nb = he.neighbour_median(g, genes, clamp, evaluate)
    rc = he.random_control(rand_g, evaluate, cand_score, trades_of=trades_of)
    bf = he.bonferroni(EVALS[0])
    log(f"  соседи (k={nb['k']}): медиана {nb['median']:+.3f}, худший "
        f"{nb['worst']:+.3f} при балле кандидата {cand_score:+.3f} — "
        f"провал соседей означает узкий пик, то есть подгонку")
    log(f"  случайные геномы (k={rc['k']}): медиана {rc['median']:+.3f}, "
        f"лучший {rc['best']:+.3f}; кандидата повторили или побили "
        f"{rc['beat']} из {rc['k']} ({rc['share']:.0f}%)"
        + (f"; отброшено неторгующих {rc['rejected']}" if rc.get("rejected")
           else ""))
    for w in rc.get("warns", []):
        log(f"    контроль: {w}")
    log(f"  перебор: оценено геномов {bf['tried']}; при alpha=0.05 случайных "
        f"«находок» ожидается {bf['expected_false']:.0f}, честный порог на "
        f"одну проверку alpha={bf['alpha']:.2e}")
    return dict(neighbour_median=nb["median"], neighbour_worst=nb["worst"],
                random_median=rc["median"], random_best=rc["best"],
                random_beat_share=rc["share"], random_k=rc["k"],
                random_rejected=rc.get("rejected", 0),
                random_warns=rc.get("warns", []),
                genomes_tried=bf["tried"],
                expected_false=bf["expected_false"])


def gate_report(m, reasons, warns, log=print):
    """Печать ворот honest_eval рядом с вердиктом.

    Молчание о неизмеренном риске — такая же неправда, как заниженная цифра,
    поэтому предупреждения печатаются всегда, а не только при отказе.
    """
    log(f"  экзамен: {he.describe(m)}")
    for r in reasons:
        log(f"    ВОРОТА: {r}")
    for w in warns:
        log(f"    не измерено: {w}")


def run_version(genes, off, make_f, aux_builder, tag, base_src,
                interval="15", symbols=None, days=None):
    """interval/days: таймфрейм и глубина истории (по умолчанию 15m/3.2г —
    не менять существующим вызовам). e2.BARS_PER_DAY переключается на время
    прогона под фактический таймфрейм и восстанавливается в finally, чтобы
    ATR "суток" и штраф за долгое удержание в fitness() считались верно."""
    results = {}
    bars_per_day = max(4, 1440 // int(interval))
    old_bpd = e2.BARS_PER_DAY
    e2.BARS_PER_DAY = bars_per_day
    try:
        results = _run_version_body(genes, off, make_f, aux_builder, tag,
                                    base_src, interval, symbols or SYMBOLS,
                                    days or DAYS)
    finally:
        e2.BARS_PER_DAY = old_bpd
    return results


def _run_version_body(genes, off, make_f, aux_builder, tag, base_src,
                      interval, symbols, days):
    results = {}
    for sym in symbols:
        print(f"\n================ {sym} ({tag}, {interval}m) ================")
        candles = ev.fetch(sym, interval, days)
        n = len(candles)
        print(f"Свечей: {n}")
        folds = fold_bounds_3y(n)
        aux_full = aux_builder(sym, candles)

        def _slice(v, b_, e_):
            if isinstance(v, tuple):
                return tuple(_slice(x, b_, e_) for x in v)
            if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
                return [_slice(x, b_, e_) for x in v]
            return v[b_:e_]

        def slice_aux(a, b_, e_):
            return {k: _slice(v, b_, e_) for k, v in aux_full.items()}

        oos = []
        for (a, b, e) in folds:
            seg = candles[b:e]
            oos.append((seg, e2.prep(seg), slice_aux(aux_full, b, e)))

        base = dict(base_src[sym])
        for k, v in off.items():
            base.setdefault(k, v)
        rand_g, clamp, _, _ = ga_tools(genes)
        base = clamp(base)
        EVALS[0] = 0          # счётчик перебора — свой на каждую монету

        # --- плечо отбора -----------------------------------------------------
        # Раньше весь отбор шёл на e2.LEV (x5), а в конфиг уходило плечо до x15,
        # выбранное ПОСЛЕ приёмки: и балл экзамена, и ворота риска описывали
        # стратегию, которой не торгуют. Теперь плечо известно ДО оценки: оно
        # берётся лестницей по обучающей части на базовом геноме (база — то
        # единственное, что известно до отбора), и на нём считается всё —
        # фитнес GA, валидация, экзамен. После выбора победителя плечо
        # уточняется по его собственной лестнице, и экзамен пересчитывается.
        cut = train_end_bar(n)
        train_all = candles[:cut]
        pre_train_all = e2.prep(train_all)
        aux_train_all = slice_aux(aux_full, 0, cut)

        def lev_pick_for(g, why):
            rows = ladder_rows(train_all, pre_train_all, g,
                               make_f(g, aux_train_all))
            ch = choose_leverage(rows)
            dd_txt = "/".join(
                f"x{r['lev']}:"
                + ("н/д" if r["dd_float"] is None else f"{r['dd_float']:.1f}%")
                for r in rows)
            print(f"  плечо по обучающей части ({why}): x{ch['lev']} "
                  f"| плав. просадка {dd_txt}")
            for w in ch["warns"]:
                print(f"    ВНИМАНИЕ: {w}")
            return ch

        lev_ch = lev_pick_for(base, "база")
        lev_sel = lev_ch["lev"]

        def score_on(g, wi, lev=None):
            """Балл генома на ОДНОМ OOS-окне и на ЗАДАННОМ плече.

            Раньше здесь была agg() по всем трём окнам сразу — она смешивала
            обучение с экзаменом; и плечо было чужое (x5 вместо боевого).
            """
            seg, pre_seg, aux_seg = oos[wi]
            old, e2.LEV = e2.LEV, (lev or lev_sel)
            try:
                r = e2.run5(seg, pre_seg, g, entry_filter=make_f(g, aux_seg))
            finally:
                e2.LEV = old
            return e2.oos_score(r)

        base_sc = [score_on(base, wi) for wi in range(len(oos))]
        base_mean = sum(base_sc) / len(base_sc)
        print(f"БАЗА (x{lev_sel}): OOS по окнам "
              f"{['%+.2f' % s for s in base_sc]} "
              f"(среднее {base_mean:+.2f}, экзамен = окно "
              f"{exam_window_index(len(oos))+1}: {base_sc[-1]:+.2f})")

        base_key = tuple(base[k] for k in genes)
        cand = []
        for fi, (a, b, e) in enumerate(folds):
            train = candles[a:b]
            scored = evolve_ext(train, e2.prep(train), slice_aux(aux_full, a, b),
                                [base], genes, make_f, f"{sym[:3]}-f{fi+1}",
                                lev=lev_sel)
            seen = set()
            for f_, g in scored:
                key = tuple(g[k] for k in genes)
                # База подсаживается семенем в популяцию каждого фолда — иначе
                # GA стартовал бы с нуля. Но КАНДИДАТОМ она быть не может: база
                # отобрана старой схемой по всей истории, включая экзамен, и
                # «победа базы над базой» ничего не значит. Элитизм донёс бы её
                # до финала как обычного кандидата — здесь она отсеивается.
                if key == base_key or key in seen:
                    continue
                seen.add(key)
                # фолд запоминается вместе с геномом: без него потом не
                # узнать, какие окна для этого кандидата уже «просмотрены»
                cand.append((fi, g))
                if len(seen) == 3:
                    break

        pick = choose_winner(cand, base_sc, score_on)
        g_win = pick["genome"]

        # Плечо победителя: его собственная лестница по обучающей части. Если
        # оно отличается от плеча отбора, экзамен и база на экзамене
        # ПЕРЕСЧИТЫВАЮТСЯ — приёмка обязана стоять на той же шкале, что уходит
        # в конфиг, иначе сравниваем одну стратегию, а торгуем другой.
        lev_win = lev_pick_for(g_win, "победитель")
        lev_final = lev_win["lev"]
        m_sel, base_sel = pick["exam_score"], pick["base_exam"]
        if lev_final != lev_sel:
            m = score_on(g_win, pick["exam_window"], lev_final)
            base_exam = score_on(base, pick["exam_window"], lev_final)
            print(f"  плечо уточнено x{lev_sel} -> x{lev_final}: экзамен "
                  f"пересчитан {m_sel:+.2f} -> {m:+.2f}, база "
                  f"{base_sel:+.2f} -> {base_exam:+.2f}")
        else:
            m, base_exam = m_sel, base_sel
        edge_ok = m > base_exam + MIN_EDGE

        # Ворота honest_eval на экзаменационном окне: балл p25+0.5*медиана слеп
        # и к хвосту (одна катастрофа внутри месяца его почти не двигает), и к
        # вырожденности (конфиг без сделок даёт ровно 0.00 и обыгрывает любую
        # убыточную базу). Плечо здесь — lev_final, то самое, что уходит в
        # конфиг: на x5 ворота меряли бы риск чужой стратегии.
        ex_seg, ex_pre, ex_aux = oos[pick["exam_window"]]
        mm = he.measure(ex_seg, ex_pre, g_win, make_f(g_win, ex_aux), lev_final,
                        tag=f"{sym}/{tag}")
        gates_ok, reasons, warns = he.verdict(mm)
        warns = list(warns) + lev_win["warns"]
        if not lev_win["ok"]:
            reasons = ["плечо не подтверждено: ни одна ступень лестницы не "
                       "укладывается в просадку "
                       f"{DD_CAP*100:.0f}%"] + list(reasons)
            gates_ok = False
        if not edge_ok:
            reasons = [f"отрыв на экзамене {m - base_exam:+.2f} <= {MIN_EDGE}"
                       ] + reasons
        adopt = bool(edge_ok and gates_ok)
        ext_used = {k: g_win[k] for k in off}
        print(f"ЛУЧШИЙ {tag}: экзамен {m:+.2f} против базы {base_exam:+.2f} "
              f"(плечо x{lev_final}) | принят: {'ДА' if adopt else 'нет'}")
        gate_report(mm, reasons, warns)

        # соседи и случайный контроль — на том же окне и том же плече
        _ex_cache = {}

        def _exam_run(gg):
            key = tuple(gg[k] for k in genes)
            if key not in _ex_cache:
                old, e2.LEV = e2.LEV, lev_final
                try:
                    r = e2.run5(ex_seg, ex_pre, gg,
                                entry_filter=make_f(gg, ex_aux))
                finally:
                    e2.LEV = old
                _ex_cache[key] = (e2.oos_score(r), r["trades"])
            return _ex_cache[key]

        honesty = honesty_report(g_win, genes, clamp, rand_g,
                                 lambda gg: _exam_run(gg)[0], m,
                                 trades_of=lambda gg: _exam_run(gg)[1])
        print(f"  внешние гены: {ext_used}")
        results[sym] = dict(base_oos=base_exam, cand_oos=m, adopt=adopt,
                            genome=g_win, base_genome=base,
                            # base_oos/cand_oos — теперь баллы ЭКЗАМЕНАЦИОННОГО
                            # окна, а не среднее по трём (в прошлых волнах было
                            # среднее). Поля ниже не дают перепутать одно с
                            # другим при чтении старых и новых json рядом.
                            protocol=("leaky-3window-mean" if pick["leaky"]
                                      else "honest-val-then-exam"),
                            base_folds=base_sc, base_mean_all=base_mean,
                            val_window=pick["val_window"],
                            val_score=pick["val_score"],
                            val_edge=pick["val_edge"],
                            exam_window=pick["exam_window"],
                            train_fold=pick["train_fold"],
                            skipped_candidates=pick["skipped"],
                            # плечо: на нём считались фитнес, валидация,
                            # экзамен и ворота — и ровно оно уходит в конфиг
                            lev=lev_final, lev_sel=lev_sel,
                            lev_confirmed=lev_win["ok"],
                            lev_picked_on="train",
                            ladder_train=lev_win["rows"],
                            exam_score_at_lev_sel=m_sel,
                            reject_reasons=reasons, warnings=warns,
                            honesty=honesty,
                            exam_measure={k: v for k, v in mm.items()
                                          if k != "rs"})
    with open(f"{tag}_winners.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2, default=float)
    print(f"\nИтоги в {tag}_winners.json")
    return results


def main():
    pct5 = xd.fetch_daily_pct5()
    print("SPX/DXY/gold: дневные ряды загружены")

    def aux_builder(sym, candles):
        funding = xd.fetch_funding(sym, DAYS + 50)
        oi = xd.fetch_oi(sym)
        cov_f = sum(1 for _ in funding)
        print(f"  {sym}: funding {cov_f} точек, OI {len(oi)} часов")
        return xd.build_aux4(candles, funding, oi, pct5)

    run_version(GENES4, OFF4, make_filter4, aux_builder, "evolution4", CURRENT)


if __name__ == "__main__":
    main()
