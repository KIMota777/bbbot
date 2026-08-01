# -*- coding: utf-8 -*-
"""ЧЕСТНЫЕ ЦИФРЫ ПЯТИ ФИНАЛЬНЫХ БОТОВ ДЛЯ САЙТА -> webapp/data/bots_honest.json.

Зачем: на сайте у ботов крупно висели цифры ОБУЧАЮЩЕГО периода (+300.9% DOGE,
+239.5% BTC) — та самая подача, от которой мы ушли в сигналах. Здесь считается
то, что можно показывать крупно: холдоут, устойчивость параметров, бенчмарки,
издержки и вердикт по объявленному заранее критерию.

Логика измерений тут НЕ дублируется. Всё меряют функции bots_honest.py
(эталон честной перепроверки): run_at / summarize / perturb / rand_core /
portfolio / compound_pct / slice_aux / pct + его же константы (холдоут 28%,
90 возмущений +-10%, 200 случайных геномов, лестница плечей, DEFAULT_CFG).
Этот файл — только сборка их результатов в JSON для страниц сайта.

Самопроверки берутся оттуда же (bots_honest.check_regression /
check_causality), их итог кладётся в JSON: если регрессия или причинность
провалились, показывать цифры на сайте нельзя.

КОПЕЕЧНЫЕ ВЫХОДЫ (добавлено после разбора артефакта безубытка). Перенос стопа
в безубыток закрывает цикл «в ноль» (+0.001$), но формально это победа — из-за
этого винрейт ботов доходил до 98%, а у части из них весь итог состоял из
таких выходов. В JSON появились:
  bots.<SYM>.tiny.{train,holdout,full} — tiny_n/tiny_share, wr_honest,
      pnl_split, pnl_without_tiny, be_n/be_share, avg_win_real/avg_loss,
      holds_on_artifact;
  bots.<SYM>.robust_no_tiny — КОНСЕРВАТИВНАЯ честная оценка (медиана 90
      соседей, у каждого копеечные выходы обнулены);
  bots.<SYM>.verdict.{score,n_ok,status_old,score_old,n_ok_old,changed} —
      пятый критерий и то, что было до него;
  portfolio.{train,holdout}_no_tiny, summary.*_no_tiny, tiny_rule.
Меряет всё bots_honest (cycle_metrics / ret_no_tiny) — логика в одном месте.

Запуск: python build_bot_honest_data.py
"""

import json
import os
import random
import re
import statistics
import sys
import time

import bots_honest as bh
import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

try:                                   # консоль Windows в cp1251 — не мешаем
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                      # noqa: BLE001
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "webapp", "data", "bots_honest.json")
REF = os.path.join(ROOT, "bots_honest_out.txt")   # отчёт-эталон для сверки

# Оговорка одна на всех — печатается на каждой карточке и странице бота.
CAVEAT = (
    "Холдоут у ботов ЧАСТИЧНО загрязнён: 78% его баров были OOS-критерием, "
    "по которому волны отбора v4–v8 выбирали победителя, а плечо каждого бота "
    "подбиралось по просадке на полных 3.2 годах (холдоут внутри). "
    "Неприкосновенного холдоута у ботов нет и взять его уже неоткуда — данные "
    "кончились. Поэтому ПЛЮС на холдоуте почти ничего не доказывает, а МИНУС "
    "доказывает много. Главные аргументы — устойчивость параметров и сравнение "
    "со случайными геномами: их подбор не видел по построению."
)

CRITERIA = [
    "на холдоуте бот в плюсе",
    "обходит необученный конфиг на том же плече",
    "стоит на плато, а не на шпиле (>= медианы 90 соседей, прибыльных >= 60%)",
    "обходит медиану 200 случайных геномов ядра (ранг >= 75%)",
    "остаётся в плюсе, если копеечные выходы обнулить (проверка на артефакт)",
]

# Пояснение к пятому критерию — печатается на странице рядом с цифрами.
TINY_RULE = (
    f"Копеечный выход («в ноль») — цикл, закрытый с |PnL| меньше "
    f"{bh.TINY_FRAC * 100:.0f}% маржи цикла, то есть меньше ${bh.TINY_USD:.2f} "
    f"при марже ${e2.MARGIN:.0f}. Такие выходы делает перенос стопа в "
    f"безубыток (ген be_move=1): цикл закрывается у средней цены +0.15% — "
    f"формально «прибыльная сделка», фактически ноль. Они раздувают винрейт "
    f"до 95-98% и на графике подписаны как «+0.001$». Порог $0.05 — это "
    f"примерно половина издержек одного цикла: меньше него деньгами не "
    f"является ни при каком способе счёта. Копеечные минусы считаются так же, "
    f"как копеечные плюсы: это тоже «ноль»."
)

STATUS_KEY = {"ПОДТВЕРЖДЁН": "ok", "ЧАСТИЧНО": "part", "НЕ ПОДТВЕРЖДЁН": "no"}


def trades_n(n):
    """«268 сделок», «164 сделки», «21 сделка» — тексты вердикта читает человек."""
    n = int(n or 0)
    if 11 <= n % 100 <= 14:
        return f"{n} сделок"
    return f"{n} " + {1: "сделка", 2: "сделки", 3: "сделки",
                      4: "сделки"}.get(n % 10, "сделок")


def m_json(m):
    """Сводка прогона -> компактный словарь для JSON (без списка pnl)."""
    return dict(trades=m["trades"], wr=round(m["wr"], 1),
                ret=round(m["ret"], 1), comp=round(m["comp"], 1),
                per_month=round(m["per_month"], 2), dd=round(m["dd"], 1),
                liqs=m["liqs"], pnl_usd=round(m["pnl_usd"], 2),
                ruined=bool(m["ruined"]))


def build_prep(pct5, syms):
    """Подготовка данных ровно как в bots_honest.main(): геном из config.py,
    свечи 15m за 1150 дней, aux на полной истории и его срезы под обе части."""
    prep = {}
    for sym in syms:
        p = config.SYMBOL_PARAMS[sym]["final"]
        g = e7.cfg_to_genome(p, "final")
        for k, v in e8.OFF8.items():
            g.setdefault(k, v)
        candles = ev.fetch(sym, "15", bh.DAYS)
        aux = e8.make_aux_builder(pct5, 96)(sym, candles)
        n = len(candles)
        h = int(n * bh.HOLD_FRAC)
        tr_c, ho_c = candles[:h], candles[h:]
        prep[sym] = dict(
            p=p, g=g, lev=p.get("lev", 5), candles=candles, aux=aux,
            n=n, hold_i=h, pre=e2.prep(candles),
            filt=e8.make_filter8(g, aux),
            train=dict(candles=tr_c, pre=e2.prep(tr_c),
                       filt=e8.make_filter8(
                           g, {k: bh.slice_aux(v, 0, h) for k, v in aux.items()}),
                       months=(tr_c[-1][0] - tr_c[0][0]) / (30 * 86400000)),
            hold=dict(candles=ho_c, pre=e2.prep(ho_c),
                      filt=e8.make_filter8(
                          g, {k: bh.slice_aux(v, h, n) for k, v in aux.items()}),
                      months=(ho_c[-1][0] - ho_c[0][0]) / (30 * 86400000)))
    return prep


def base_runs(prep):
    """Прогоны текущего конфига (полная история / обучение / холдоут) и
    необученного конфига DEFAULT_CFG — как в bots_honest.main().

    Заодно считаются метрики копеечных выходов (bots_honest.cycle_metrics):
    сколько циклов закрылось «в ноль» и каким был бы итог без них."""
    for sym, d in prep.items():
        evs = []
        r = bh.run_at(d["candles"], d["pre"], d["g"], d["filt"], d["lev"],
                      events=evs)
        months_full = (d["candles"][-1][0] - d["candles"][0][0]) / (30 * 86400000)
        d["full"] = bh.summarize(r, evs, months_full)
        d["full_tiny"] = bh.cycle_metrics(evs, d["candles"], d["g"], months_full)
        for part in ("train", "hold"):
            s = d[part]
            evs = []
            r = bh.run_at(s["candles"], s["pre"], d["g"], s["filt"], d["lev"],
                          events=evs)
            s["m"] = bh.summarize(r, evs, s["months"])
            s["tiny"] = bh.cycle_metrics(evs, s["candles"], d["g"], s["months"])
            s["curve"] = [(e["t"], e["pnl"]) for e in evs if e["type"] == "close"]
            # кривая с обнулёнными копеечными выходами — консервативный портфель
            s["curve_nt"] = [(t, 0.0 if bh.is_tiny(p) else p)
                             for t, p in s["curve"]]
        gd = e7.cfg_to_genome(bh.DEFAULT_CFG, "final")
        for k, v in e8.OFF8.items():
            gd.setdefault(k, v)
        d["gd"] = gd
        for part, a, b in (("train", 0, d["hold_i"]), ("hold", d["hold_i"], d["n"])):
            s = d[part]
            s["filt_def"] = e8.make_filter8(
                gd, {k: bh.slice_aux(v, a, b) for k, v in d["aux"].items()})
            evs = []
            r = bh.run_at(s["candles"], s["pre"], gd, s["filt_def"], d["lev"],
                          events=evs)
            s["def_m"] = bh.summarize(r, evs, s["months"])
            s["def_curve"] = [(e["t"], e["pnl"]) for e in evs
                              if e["type"] == "close"]
        d["def_x5"] = bh.summarize(
            *_run_ev(d["hold"]["candles"], d["hold"]["pre"], gd,
                     d["hold"]["filt_def"], 5), d["hold"]["months"])
        d["def_full"] = bh.summarize(
            *_run_ev(d["candles"], d["pre"], gd, e8.make_filter8(gd, d["aux"]),
                     d["lev"]),
            (d["candles"][-1][0] - d["candles"][0][0]) / (30 * 86400000))


def _run_ev(candles, pre, g, filt, lev):
    evs = []
    r = bh.run_at(candles, pre, g, filt, lev, events=evs)
    return r, evs


def coin_hold(seg):
    """Что было с самой монетой на отрезке: итог и глубина обвала пик->дно."""
    peak, worst = seg[0][2], 0.0
    for _, o, hi, lo, cl in seg:
        peak = max(peak, hi)
        worst = max(worst, (peak - lo) / peak)
    return (seg[-1][4] / seg[0][4] - 1) * 100, worst * 100


def bench_random(d, rng):
    """200 случайных геномов ядра стратегии на холдоуте (bots_honest.rand_core)."""
    aux_h = {k: bh.slice_aux(v, d["hold_i"], d["n"]) for k, v in d["aux"].items()}
    rets = []
    for _ in range(bh.N_RAND):
        gr = bh.rand_core(e2.GENES, rng)
        rr = bh.run_at(d["hold"]["candles"], d["hold"]["pre"], gr,
                       e8.make_filter8(gr, aux_h), d["lev"])
        rets.append((rr["balance"] / e2.START - 1) * 100)
    return rets


def robustness(d):
    """90 возмущений генома +-10% (3 зерна x 30) на холдоуте.

    Каждый сосед считается ДВАЖДЫ: как есть и с обнулёнными копеечными
    выходами. Вторая версия и есть консервативная «честная оценка»: она не
    обманывается переносом стопа в безубыток."""
    aux_h = {k: bh.slice_aux(v, d["hold_i"], d["n"]) for k, v in d["aux"].items()}
    fact = d["hold"]["m"]["ret"]
    allv, allnt, by_seed = [], [], []
    for seed in bh.PERT_SEEDS:
        rg = random.Random(seed)
        vals = []
        for _ in range(bh.N_PERT):
            gp = bh.perturb(d["g"], e8.GENES8, rg, bh.PERT)
            evs = []
            rr = bh.run_at(d["hold"]["candles"], d["hold"]["pre"], gp,
                           e8.make_filter8(gp, aux_h), d["lev"], events=evs)
            vals.append((rr["balance"] / e2.START - 1) * 100)
            allnt.append(bh.ret_no_tiny([e["pnl"] for e in evs
                                         if e["type"] == "close"]))
        allv += vals
        by_seed.append(dict(seed=seed, median=round(statistics.median(vals), 1),
                            p10=round(bh.pct(vals, 0.1), 1),
                            p90=round(bh.pct(vals, 0.9), 1),
                            share_pos=round(sum(1 for x in vals if x > 0)
                                            / len(vals) * 100),
                            rank=round(sum(1 for x in vals if x < fact)
                                       / len(vals) * 100)))
    return allv, allnt, by_seed


def costs(d, part):
    """Вклад издержек: обнуляем глобалы evolution2 на время прогона."""
    s = d[part]
    base = s["m"]["pnl_usd"]
    out = {}
    for tag, patch in (("gross", dict(TAKER=0, MAKER=0, SLIP=0, FUND_8H=0)),
                       ("nofee", dict(TAKER=0, MAKER=0)),
                       ("noslip", dict(SLIP=0)), ("nofund", dict(FUND_8H=0))):
        old = {k: getattr(e2, k) for k in patch}
        for k, v in patch.items():
            setattr(e2, k, v)
        try:
            rr = bh.run_at(s["candles"], s["pre"], d["g"], s["filt"], d["lev"])
        finally:
            for k, v in old.items():
                setattr(e2, k, v)
        out[tag] = rr["balance"] - e2.START
    gross = out["gross"]
    eaten = gross - base
    share = (eaten / gross * 100) if abs(gross) > 1e-9 else None
    return dict(net=round(base, 2), gross=round(gross, 2), eaten=round(eaten, 2),
                share=(round(share, 1) if share is not None else None),
                fees=round(out["nofee"] - base, 2),
                slip=round(out["noslip"] - base, 2),
                funding=round(out["nofund"] - base, 2))


def ladder(d):
    """Лестница плечей на ХОЛДОУТЕ + рекомендация (правило bots_honest)."""
    rows = []
    for lev in bh.LEVS:
        # фильтр строго холдоутный: aux адресуется НОМЕРОМ бара, полный aux на
        # обрезанных свечах молча даёт другие сделки
        rr = bh.run_at(d["hold"]["candles"], d["hold"]["pre"], d["g"],
                       d["hold"]["filt"], lev)
        rows.append(dict(lev=lev, ret=round((rr["balance"] / e2.START - 1) * 100, 1),
                         dd=round(rr["max_dd"] * 100, 1), ruined=bool(rr["ruined"])))
    good = [x for x in rows if x["dd"] <= bh.DD_CAP and x["ret"] > 0
            and not x["ruined"]]
    return rows, (f"x{good[-1]['lev']}" if good else None)


def verdict(d, rob, rand_rets, rand_rank):
    """Критерий bots_honest: 5 пунктов, объявлены до подсчёта.

    Пятый добавлен после того, как выяснилось, что часть «прибыли» ботов —
    копеечные выходы по безубытку. Возвращается и СТАРЫЙ счёт (N/4), чтобы
    на странице было видно, что именно изменилось."""
    ret = d["hold"]["m"]["ret"]
    t = d["hold"]["tiny"]
    med_p = statistics.median(rob)
    share_p = sum(1 for x in rob if x > 0) / len(rob) * 100
    med_r = statistics.median(rand_rets)
    c1 = ret > 0
    c2 = ret > d["hold"]["def_m"]["ret"]
    c3 = ret >= med_p and share_p >= 60
    c4 = rand_rank >= 75
    c5 = t["pnl_without_tiny"] > 0
    okn_old = sum([c1, c2, c3, c4])
    okn = okn_old + int(c5)
    status_old = ("ПОДТВЕРЖДЁН" if okn_old == 4 else
                  "ЧАСТИЧНО" if okn_old == 3 else "НЕ ПОДТВЕРЖДЁН")
    status = ("ПОДТВЕРЖДЁН" if okn == 5 else
              "ЧАСТИЧНО" if okn == 4 else "НЕ ПОДТВЕРЖДЁН")
    reasons = [
        dict(ok=bool(c1), title=CRITERIA[0], detail=f"холдоут {ret:+.1f}%"),
        dict(ok=bool(c2), title=CRITERIA[1],
             detail=(f"бот {ret:+.1f}% против {d['hold']['def_m']['ret']:+.1f}% "
                     f"у необученной сетки "
                     f"({trades_n(d['hold']['def_m']['trades'])})")),
        dict(ok=bool(c3), title=CRITERIA[2],
             detail=(f"медиана 90 соседей {med_p:+.1f}%, прибыльных соседей "
                     f"{share_p:.0f}%")),
        dict(ok=bool(c4), title=CRITERIA[3],
             detail=(f"медиана случайных {med_r:+.1f}%, бот обошёл "
                     f"{rand_rank:.0f}% из {len(rand_rets)}")),
        dict(ok=bool(c5), title=CRITERIA[4],
             detail=(f"без копеечных выходов {t['ret_without_tiny']:+.1f}% "
                     f"против {ret:+.1f}%; копеечных "
                     f"{t['tiny_share']:.0f}% циклов, честный винрейт "
                     f"{t['wr_honest']:.1f}% вместо {t['wr']:.1f}%")),
    ]
    return status, okn, reasons, status_old, okn_old


def notes_for(d, rob, cost_hold):
    """Особые случаи, которые нельзя замалчивать. kind: risk — плохая новость,
    good — редкая хорошая (страница красит их по-разному)."""
    out = []
    n = d["hold"]["m"]["trades"]
    t, tf = d["hold"]["tiny"], d["full_tiny"]
    if t["holds_on_artifact"]:
        out.append(dict(kind="risk", text=(
            f"ДЕРЖИТСЯ НА АРТЕФАКТЕ: {t['tiny_share']:.0f}% циклов закрылись "
            f"«в ноль» (|PnL| < ${bh.TINY_USD:.2f}) — это перенос стопа в "
            f"безубыток. Если считать такие выходы нулём, холдоут превращается "
            f"из {t['ret']:+.1f}% в {t['ret_without_tiny']:+.1f}%. Прибыли "
            f"как таковой у бота нет.")))
    elif t["tiny_share"] >= 50:
        out.append(dict(kind="risk", text=(
            f"Больше половины циклов ({t['tiny_share']:.0f}%) закрываются "
            f"«в ноль» — винрейт {t['wr']:.1f}% декоративный, честный "
            f"{t['wr_honest']:.1f}%. Плюс держится, но реальный доход "
            f"холдоута — {t['ret_without_tiny']:+.1f}%, а не "
            f"{t['ret']:+.1f}%.")))
    if tf["holds_on_artifact"]:
        out.append(dict(kind="risk", text=(
            f"За все {d['full']['trades']} циклов 3.2 лет копеечные выходы "
            f"дали {tf['pnl_split']['tiny']:+.2f}$ из итоговых "
            f"{tf['pnl_usd']:+.2f}$: без них весь период "
            f"{tf['pnl_without_tiny']:+.2f}$.")))
    elif tf["tiny_net_share"] is not None and tf["tiny_net_share"] >= 50:
        out.append(dict(kind="risk", text=(
            f"Итог сделан копейками: за 3.2 года копеечные выходы дали "
            f"{tf['pnl_split']['tiny']:+.2f}$ из итоговых "
            f"{tf['pnl_usd']:+.2f}$ — это {tf['tiny_net_share']:.0f}% всего "
            f"результата. Реальная торговля за весь период дала "
            f"{tf['pnl_without_tiny']:+.2f}$ "
            f"({tf['ret_without_tiny']:+.1f}% от базы $20).")))
    if (t["avg_loss"] and abs(t["avg_loss"]) > 5 * max(t["avg_win_real"], 1e-9)):
        out.append(dict(kind="risk", text=(
            f"Асимметрия: средний реальный плюс {t['avg_win_real']:+.3f}$ "
            f"против среднего убытка {t['avg_loss']:+.3f}$ — один минус "
            f"съедает {abs(t['avg_loss'])/t['avg_win_real']:.0f} выигрышей.")))
    if n < 30:
        out.append(dict(kind="risk", text=(
            f"Всего {trades_n(n)} за {d['hold']['months']:.1f} мес — на такой "
            f"выборке ЛЮБОЙ вывод (и плюс, и минус) статистически пуст: "
            f"подтвердить такого бота невозможно в принципе.")))
    if d["hold"]["m"]["ret"] <= d["hold"]["def_m"]["ret"]:
        out.append(dict(kind="risk", text=(
            f"Проигрывает НЕОБУЧЕННОЙ сетке из шапки config.py: "
            f"{d['hold']['m']['ret']:+.1f}% против "
            f"{d['hold']['def_m']['ret']:+.1f}% — значит тонкая настройка "
            f"на этом куске рынка денег не добавила.")))
    if d["train"]["m"]["ret"] < d["hold"]["m"]["ret"]:
        out.append(dict(kind="good", text=(
            f"Обучение ({d['train']['m']['ret']:+.1f}%) ХУЖЕ холдоута "
            f"({d['hold']['m']['ret']:+.1f}%) — редкий хороший знак: "
            f"подгонки под обучающий период не было.")))
    if cost_hold["share"] is not None and cost_hold["share"] > 100:
        out.append(dict(kind="risk", text=(
            f"Издержки съели {cost_hold['share']:.0f}% валовой прибыли — "
            f"то есть всю её целиком; торговля кормит биржу, а не счёт.")))
    if statistics.median(rob) < 0:
        out.append(dict(kind="risk", text=(
            "Медиана соседних конфигов отрицательная: типичный сосед "
            "этого генома теряет деньги.")))
    return out


def check_against_reference(bots):
    """Сверка с отчётом bots_honest_out.txt (если он есть рядом): холдоут и
    медиана соседей обязаны совпасть — иначе где-то разъехались данные."""
    if not os.path.exists(REF):
        return None
    txt = open(REF, encoding="utf-8").read()
    rows = re.findall(r"^  ([A-Z]+USDT)\s+(?:ПОДТВЕРЖДЁН|ЧАСТИЧНО|НЕ ПОДТВЕРЖДЁН)"
                      r"\s+([+-][\d.]+)\s+([+-][\d.]+)\s+([+-][\d.]+)"
                      r"\s+([+-][\d.]+)\s+([+-][\d.]+)\s+[+-][\d.]+\s+(.+?)\s*$",
                      txt, re.M)
    if not rows:
        return None
    bad = []
    for sym, site, doc, tr, ho, med, lev in rows:
        b = bots.get(sym)
        if not b:
            continue
        for name, ref, got in (("холдоут", float(ho), b["holdout"]["ret"]),
                               ("медиана", float(med), b["robust"]["median"]),
                               ("обучение", float(tr), b["train"]["ret"]),
                               ("сайт /pnl", float(site), b["site_pnl_pct"]),
                               ("config.py", float(doc), b["config_doc_pct"])):
            if abs(ref - got) > 0.051:
                bad.append(f"{sym} {name}: эталон {ref:+.1f}, посчитано {got:+.1f}")
        got_lev = b["lev_by_holdout"]["rec"] or "НЕ ЗАПУСКАТЬ"
        if got_lev != lev:
            bad.append(f"{sym} плечо по холдоуту: эталон {lev}, посчитано {got_lev}")
    return bad


def main():
    t0 = time.time()
    pct5 = xd.fetch_daily_pct5()
    syms = [s for s, m in config.SYMBOL_PARAMS.items() if m.get("final")]
    e2.BARS_PER_DAY = 96                      # все финальные боты на 15m

    print("build_bot_honest_data: считаю честные метрики "
          f"{len(syms)} ботов ({', '.join(syms)})")
    prep = build_prep(pct5, syms)
    base_runs(prep)

    ok_reg = bh.check_regression(prep)        # цифры config.py воспроизводятся?
    ok_cau = bh.check_causality(prep, pct5)   # нет заглядывания вперёд?

    rng = random.Random(bh.RAND_SEED)         # один поток на все монеты — как в
    bots = {}                                 # bots_honest, иначе числа разъедутся
    for sym in syms:
        d = prep[sym]
        print(f"  {sym}: бенчмарки и устойчивость…", flush=True)
        rand_rets = bench_random(d, rng)
        rand_rank = sum(1 for x in rand_rets if x < d["hold"]["m"]["ret"]) \
            / len(rand_rets) * 100
        rob, rob_nt, by_seed = robustness(d)
        c_tr, c_ho = costs(d, "train"), costs(d, "hold")
        lad, rec = ladder(d)
        status, okn, reasons, status_old, okn_old = verdict(
            d, rob, rand_rets, rand_rank)
        med_p = statistics.median(rob)
        med_nt = statistics.median(rob_nt)
        d["med_nt"] = med_nt
        hodl, worst = coin_hold(d["hold"]["candles"])
        hm = d["hold"]["months"]
        bots[sym] = dict(
            symbol=sym, coin=sym.replace("USDT", ""), lev=d["lev"],
            n_trades=dict(full=d["full"]["trades"],
                          train=d["train"]["m"]["trades"],
                          holdout=d["hold"]["m"]["trades"]),
            site_pnl_pct=round(d["full"]["comp"], 1),   # то, что было крупно
            config_doc_pct=bh.DOC[sym][1],
            full=m_json(d["full"]),
            train=m_json(d["train"]["m"]),
            holdout=m_json(d["hold"]["m"]),
            robust=dict(median=round(med_p, 1), p10=round(bh.pct(rob, 0.1), 1),
                        p90=round(bh.pct(rob, 0.9), 1),
                        share_pos=round(sum(1 for x in rob if x > 0)
                                        / len(rob) * 100),
                        n=len(rob), pert_pct=int(bh.PERT * 100),
                        per_month=round(med_p / hm, 2),
                        rank_fact=round(sum(1 for x in rob
                                            if x < d["hold"]["m"]["ret"])
                                        / len(rob) * 100),
                        by_seed=by_seed),
            # та же медиана 90 соседей, но у каждого соседа копеечные выходы
            # обнулены — КОНСЕРВАТИВНАЯ честная оценка, по ней решения
            robust_no_tiny=dict(
                median=round(med_nt, 1), p10=round(bh.pct(rob_nt, 0.1), 1),
                p90=round(bh.pct(rob_nt, 0.9), 1),
                share_pos=round(sum(1 for x in rob_nt if x > 0)
                                / len(rob_nt) * 100),
                n=len(rob_nt), per_month=round(med_nt / hm, 2),
                rank_fact=round(sum(1 for x in rob_nt
                                    if x < d["hold"]["tiny"]["ret_without_tiny"])
                                / len(rob_nt) * 100)),
            # копеечные выходы: доля, честный винрейт, итог без них
            tiny=dict(train=d["train"]["tiny"], holdout=d["hold"]["tiny"],
                      full=d["full_tiny"]),
            benchmarks=dict(
                hodl=dict(ret=round(hodl, 1), max_fall=round(worst, 1)),
                untrained=dict(holdout=round(d["hold"]["def_m"]["ret"], 1),
                               train=round(d["train"]["def_m"]["ret"], 1),
                               full=round(d["def_full"]["ret"], 1),
                               x5=round(d["def_x5"]["ret"], 1),
                               trades=d["hold"]["def_m"]["trades"],
                               dd=round(d["hold"]["def_m"]["dd"], 1),
                               ruined=bool(d["hold"]["def_m"]["ruined"])),
                random=dict(median=round(statistics.median(rand_rets), 1),
                            p90=round(bh.pct(rand_rets, 0.9), 1),
                            share_pos=round(sum(1 for x in rand_rets if x > 0)
                                            / len(rand_rets) * 100),
                            rank=round(rand_rank), n=len(rand_rets))),
            costs=dict(train=c_tr, holdout=c_ho),
            verdict=dict(status=status, key=STATUS_KEY[status],
                         score=f"{okn}/5", n_ok=okn, n_crit=len(CRITERIA),
                         reasons=reasons, notes=notes_for(d, rob, c_ho),
                         # что было до пятого критерия — для таблицы пересмотра
                         status_old=status_old, key_old=STATUS_KEY[status_old],
                         score_old=f"{okn_old}/4", n_ok_old=okn_old,
                         changed=bool(status != status_old)),
            lev_by_holdout=dict(rec=rec, ladder=lad, dd_cap=bh.DD_CAP),
            caveat=CAVEAT)

    tr_ret, tr_dd = bh.portfolio({s: prep[s]["train"]["curve"] for s in syms},
                                 len(syms))
    ho_ret, ho_dd = bh.portfolio({s: prep[s]["hold"]["curve"] for s in syms},
                                 len(syms))
    dtr, dtr_dd = bh.portfolio({s: prep[s]["train"]["def_curve"] for s in syms},
                               len(syms))
    dho, dho_dd = bh.portfolio({s: prep[s]["hold"]["def_curve"] for s in syms},
                               len(syms))
    ntr, ntr_dd = bh.portfolio({s: prep[s]["train"]["curve_nt"] for s in syms},
                               len(syms))
    nho, nho_dd = bh.portfolio({s: prep[s]["hold"]["curve_nt"] for s in syms},
                               len(syms))
    d0 = prep[syms[0]]
    c = d0["candles"]
    n_ok = sum(1 for b in bots.values() if b["verdict"]["key"] == "ok")
    n_part = sum(1 for b in bots.values() if b["verdict"]["key"] == "part")
    n_ok_old = sum(1 for b in bots.values() if b["verdict"]["key_old"] == "ok")
    n_art = sum(1 for b in bots.values()
                if b["tiny"]["holdout"]["holds_on_artifact"])
    avg_hold = sum(b["holdout"]["ret"] for b in bots.values()) / len(bots)
    avg_med = sum(b["robust"]["median"] for b in bots.values()) / len(bots)
    avg_med_nt = sum(b["robust_no_tiny"]["median"]
                     for b in bots.values()) / len(bots)
    avg_hold_nt = sum(b["tiny"]["holdout"]["ret_without_tiny"]
                      for b in bots.values()) / len(bots)

    data = dict(
        generated=time.strftime("%Y-%m-%d %H:%M"),
        source="bots_honest.py (те же функции), движок evolution2.run5",
        engine=("комиссии taker 0.055%/maker 0.02%, слиппедж 0.03%, "
                "фандинг 0.01%/8ч и ликвидации — внутри прогона; 15m, "
                f"{bh.DAYS} дней"),
        caveat=CAVEAT, criteria=CRITERIA,
        selfcheck=dict(regression=bool(ok_reg), causality=bool(ok_cau)),
        tiny_rule=dict(
            thr_usd=round(bh.TINY_USD, 2), frac=bh.TINY_FRAC,
            margin=e2.MARGIN, text=TINY_RULE,
            be_note=("Признак «закрыт переносом стопа» (be) считается точно: "
                     "в движке стоп ставится один раз при входе и двигается "
                     "только безубытком, поэтому «вышли по стопу, но не по "
                     "тому стопу, что стоял при входе» = безубыток. У ETH "
                     "ген be_move=0 — там копеечные выходы дают мелкие тейки, "
                     "поэтому решает ПОРОГ, а не признак be.")),
        method=dict(
            holdout_frac=round(1 - bh.HOLD_FRAC, 2), n_pert=len(bh.PERT_SEEDS)
            * bh.N_PERT, pert=bh.PERT, n_rand=bh.N_RAND,
            robust=("«честная оценка» = медиана 90 возмущений генома +-10% "
                    "по числовым генам на холдоуте: доход ТИПИЧНОГО соседа "
                    "конфига, а не самой точки, которую поставил подбор"),
            robust_no_tiny=("КОНСЕРВАТИВНАЯ честная оценка (robust_no_tiny): "
                            "та же медиана 90 соседей, но у каждого соседа "
                            "копеечные выходы обнулены. Решения принимаются "
                            "по ней — она не обманывается безубытком"),
            fixed=("итог% — фиксированная маржа $5 от $20 (как в config.py); "
                   "реинв.% — с реинвестом 25% капитала (как на /pnl)")),
        periods=dict(
            train=dict(start=bh.fmt_day(c[0][0]),
                       end=bh.fmt_day(c[d0["hold_i"] - 1][0]),
                       months=round(d0["train"]["months"], 1)),
            holdout=dict(start=bh.fmt_day(c[d0["hold_i"]][0]),
                         end=bh.fmt_day(c[-1][0]),
                         months=round(d0["hold"]["months"], 1)),
            full=dict(start=bh.fmt_day(c[0][0]), end=bh.fmt_day(c[-1][0]),
                      months=round((c[-1][0] - c[0][0]) / (30 * 86400000), 1)),
            market="холдоут — сплошной медвежий рынок, монеты -41…-67%"),
        portfolio=dict(
            train=dict(ret=round(tr_ret, 1), dd=round(tr_dd, 1)),
            holdout=dict(ret=round(ho_ret, 1), dd=round(ho_dd, 1)),
            untrained_train=dict(ret=round(dtr, 1), dd=round(dtr_dd, 1)),
            untrained_holdout=dict(ret=round(dho, 1), dd=round(dho_dd, 1)),
            train_no_tiny=dict(ret=round(ntr, 1), dd=round(ntr_dd, 1)),
            holdout_no_tiny=dict(ret=round(nho, 1), dd=round(nho_dd, 1)),
            note="5 счетов по $20, фиксированная маржа $5"),
        summary=dict(n=len(bots), confirmed=n_ok, partial=n_part,
                     failed=len(bots) - n_ok - n_part,
                     confirmed_old=n_ok_old, on_artifact=n_art,
                     avg_holdout=round(avg_hold, 1), avg_robust=round(avg_med, 1),
                     avg_per_month=round(avg_med / d0["hold"]["months"], 2),
                     avg_holdout_no_tiny=round(avg_hold_nt, 1),
                     avg_robust_no_tiny=round(avg_med_nt, 1),
                     avg_per_month_no_tiny=round(avg_med_nt
                                                 / d0["hold"]["months"], 2)),
        # порядок ботов как в config.py: словарь через jsonify отдаётся
        # с отсортированными ключами, а на сайте порядок должен совпадать
        order=list(syms),
        bots=bots)

    diff = check_against_reference(bots)
    data["selfcheck"]["matches_reference"] = (None if diff is None else not diff)
    if diff:
        print("  ВНИМАНИЕ, расхождение с bots_honest_out.txt:")
        for x in diff:
            print("    " + x)
    elif diff == []:
        print("  сверка с bots_honest_out.txt: совпало по всем ботам")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)

    # ------------------------------------------------------------- сводка
    print()
    print("=" * 104)
    print(f"ЗАПИСАНО: {OUT}  ({os.path.getsize(OUT)} байт)")
    print(f"периоды: обучение {data['periods']['train']['start']}.."
          f"{data['periods']['train']['end']} "
          f"({data['periods']['train']['months']} мес) | ХОЛДОУТ "
          f"{data['periods']['holdout']['start']}.."
          f"{data['periods']['holdout']['end']} "
          f"({data['periods']['holdout']['months']} мес)")
    mref = data["selfcheck"]["matches_reference"]
    print(f"самопроверки: регрессия {'OK' if ok_reg else 'ПРОВАЛ'}, "
          f"причинность {'OK' if ok_cau else 'ПРОВАЛ'}, сверка с "
          f"bots_honest_out.txt: "
          f"{'нет файла' if mref is None else ('OK' if mref else 'РАСХОЖДЕНИЕ')}")
    print()
    hdr = (f"  {'бот':10} {'вердикт':16} {'сайт /pnl':>10} {'обучение':>9} "
           f"{'ХОЛДОУТ':>9} {'ЧЕСТНО':>8} {'%/мес':>7} {'p10..p90':>16} "
           f"{'>0':>5} {'сд.':>5} {'необуч.':>8} {'случ.ранг':>10} "
           f"{'издержки':>9} {'плечо':>7}")
    print(hdr)
    for sym, b in bots.items():
        print(f"  {sym:10} {b['verdict']['status'] + ' ' + b['verdict']['score']:16} "
              f"{b['site_pnl_pct']:+10.1f} {b['train']['ret']:+9.1f} "
              f"{b['holdout']['ret']:+9.1f} {b['robust']['median']:+8.1f} "
              f"{b['robust']['per_month']:+7.2f} "
              f"{b['robust']['p10']:+7.1f}..{b['robust']['p90']:<8.1f}"
              f"{b['robust']['share_pos']:4}% {b['n_trades']['holdout']:5} "
              f"{b['benchmarks']['untrained']['holdout']:+8.1f} "
              f"{b['benchmarks']['random']['rank']:9}% "
              f"{(b['costs']['holdout']['share'] or 0):8.0f}% "
              f"{(b['lev_by_holdout']['rec'] or 'НЕ ЗАПУСКАТЬ'):>7}")
    s = data["summary"]
    print(f"  {'СРЕДНЕЕ':10} {'':16} {'':10} {'':9} {s['avg_holdout']:+9.1f} "
          f"{s['avg_robust']:+8.1f} {s['avg_per_month']:+7.2f}")
    print(f"  портфель 5 ботов: обучение {data['portfolio']['train']['ret']:+.1f}% "
          f"(DD {data['portfolio']['train']['dd']:.1f}%) -> ХОЛДОУТ "
          f"{data['portfolio']['holdout']['ret']:+.1f}% "
          f"(DD {data['portfolio']['holdout']['dd']:.1f}%)")
    print(f"  тот же портфель БЕЗ КОПЕЕЧНЫХ ВЫХОДОВ: обучение "
          f"{data['portfolio']['train_no_tiny']['ret']:+.1f}% "
          f"(DD {data['portfolio']['train_no_tiny']['dd']:.1f}%) -> ХОЛДОУТ "
          f"{data['portfolio']['holdout_no_tiny']['ret']:+.1f}% "
          f"(DD {data['portfolio']['holdout_no_tiny']['dd']:.1f}%)")
    print(f"  подтверждено {s['confirmed']} из {s['n']}, частично {s['partial']}, "
          f"не подтверждено {s['failed']} "
          f"(до пятого критерия было подтверждено {s['confirmed_old']}; "
          f"на артефакте держатся {s['on_artifact']})")

    # -------------------------------------------- пересмотр: было -> стало
    print()
    print(f"ПЕРЕСМОТР ВЕРДИКТОВ (порог копеечного выхода ${bh.TINY_USD:.2f} = "
          f"{bh.TINY_FRAC*100:.0f}% маржи цикла)")
    print(f"  {'бот':10} {'БЫЛО':>18} {'СТАЛО':>18} | {'копееч.%':>9} "
          f"{'безуб.%':>8} {'WR%':>6} {'честный WR%':>12} | {'холдоут%':>9} "
          f"{'БЕЗ КОПЕЕК%':>12} | {'честно было%':>13} {'честно БЕЗ%':>12} "
          f"{'/мес':>7}")
    for sym, b in bots.items():
        t, v = b["tiny"]["holdout"], b["verdict"]
        print(f"  {sym:10} {v['status_old'] + ' ' + v['score_old']:>18} "
              f"{v['status'] + ' ' + v['score']:>18} | {t['tiny_share']:9.1f} "
              f"{t['be_share']:8.1f} {t['wr']:6.1f} {t['wr_honest']:12.1f} | "
              f"{t['ret']:+9.1f} {t['ret_without_tiny']:+12.1f} | "
              f"{b['robust']['median']:+13.1f} "
              f"{b['robust_no_tiny']['median']:+12.1f} "
              f"{b['robust_no_tiny']['per_month']:+7.2f}")
    print(f"  {'СРЕДНЕЕ':10} {'':18} {'':18} | {'':9} {'':8} {'':6} {'':12} | "
          f"{s['avg_holdout']:+9.1f} {s['avg_holdout_no_tiny']:+12.1f} | "
          f"{s['avg_robust']:+13.1f} {s['avg_robust_no_tiny']:+12.1f} "
          f"{s['avg_per_month_no_tiny']:+7.2f}")
    print("  «честно БЕЗ%» — медиана 90 соседних конфигов, у каждого из "
          "которых копеечные выходы обнулены.")
    print("  Это самая консервативная цифра доходности, которая у нас есть; "
          "решения принимать по ней.")
    print()
    for sym, b in bots.items():
        for note in b["verdict"]["notes"]:
            print(f"  {'+' if note['kind'] == 'good' else '!'} {sym}: "
                  f"{note['text']}")
    print(f"\n  (время работы {time.time() - t0:.1f} c)")


if __name__ == "__main__":
    main()
