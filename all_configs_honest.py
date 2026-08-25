# -*- coding: utf-8 -*-
"""Честное сравнение ВСЕХ сохранившихся конфигов — по правилам, пережившим критику.

ПОЧЕМУ ПРЕЖНЕЕ СРАВНЕНИЕ БЫЛО НЕВЕРНЫМ. Разбор архива сравнивал конфиги на их
собственных плечах и только на холдоуте. Состязательная проверка показала, что
так меряется не качество:

  * ПЛЕЧО. Просадка при x15 и при x5 несравнима — это размер ставки, а не
    свойство стратегии. Здесь ВСЕ конфиги считаются на одном плече.
  * ОДНА ПОЛОВИНА. Устойчивость мерили только на холдоуте, то есть ровно там,
    где конфиг и так хорош. На обучающей половине картина переворачивалась:
    у победителя медиана соседей уходила в минус при собственном плюсе —
    признак шпиля подгонки. Здесь обе половины считаются всегда.
  * ПОРЯДОК ПО ХОЛДОУТУ АНТИПРЕДСКАЗАТЕЛЕН. Ранговая связь «обучение ->
    холдоут» по пятнадцати боевым конфигам равна -0.68: кто хуже на первой
    половине, тот лучше на второй. Значит выбирать лучшего по холдоуту — почти
    то же, что бросать монетку. Поэтому итоговый порядок здесь строится по
    ХУДШЕЙ из двух половин: конфиг обязан быть приличным на обеих, а не
    блистать на одной.

Собираются: боевые конфиги из config.py и все геномы из файлов волн отбора
evolution*_winners.json и *_final.json. Одинаковые геномы схлопываются.

Запуск: python all_configs_honest.py [--lev 5] [--pert 40] [--jobs 10]
Результат: webapp/data/all_configs_honest.json
"""
import argparse
import glob
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

import bots_honest as bh
import config
import evolution as ev
import evolution2 as e2
import evolution4 as e4
import evolution5 as e5
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

e2.BARS_PER_DAY = 96
OUT = os.path.join("webapp", "data", "all_configs_honest.json")



def with_defaults(g):
    """Добить недостающие гены значениями «выключено».

    Геномы разных волн знают разный набор генов: каждая волна добавляла свои
    механизмы, а фильтр спрашивает их безусловно. Ген, которого в геноме нет,
    роняет прогон с KeyError — из-за этого сначала не считались боевые конфиги
    (не хватало ob_gate), а потом ранние волны (ema_n_idx, regime_gate).

    Наборы OFF берутся из самих волн, а не переписываются здесь: каждый из них
    означает «механизм выключен, поведение прежнее, без потери бита». Порядок
    тот же, что в bots_honest.rand_core, чтобы поздние наборы перекрывали
    ранние одинаково.
    """
    out = dict(g)
    for off in (e4.OFF4, e5.OFF5, e7.OFF7, e8.OFF8):
        for k, v in off.items():
            out.setdefault(k, v)
    return out


def collect():
    """Все геномы, какие сохранились, с пометкой откуда."""
    out = []
    for sym, modes in config.SYMBOL_PARAMS.items():
        for mode, p in modes.items():
            if not isinstance(p, dict):
                continue
            try:
                g = e7.cfg_to_genome(p, mode)
            except Exception:                      # noqa: BLE001
                continue
            out.append(dict(src="config.py", tag=mode, sym=sym,
                            g=with_defaults(g), own_lev=p.get("lev", 5)))
    for f in sorted(glob.glob("evolution*_winners.json") +
                    glob.glob("evolution*_final.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:                          # noqa: BLE001
            continue
        if not isinstance(d, dict):
            continue
        for key, v in d.items():
            if not isinstance(v, dict) or "genome" not in v:
                continue
            g = v["genome"]
            if not isinstance(g, dict) or "rsi_os" not in g:
                continue
            sym = key if key.endswith("USDT") else str(v.get("symbol", ""))
            if not sym.endswith("USDT"):
                continue
            out.append(dict(src=f.replace(".json", ""), tag="волна",
                            sym=sym, g=with_defaults(g),
                            own_lev=v.get("rec_lev", v.get("lev", 5))))
    # Схлопываем одинаковые геномы: волны часто переносят конфиг без изменений,
    # и без этого один и тот же бот занял бы полтаблицы, создавая видимость
    # согласия там, где это одна и та же запись.
    seen, uniq = {}, []
    for r in out:
        k = (r["sym"], json.dumps(r["g"], sort_keys=True, default=str))
        if k in seen:
            seen[k]["also"].append("%s/%s" % (r["src"], r["tag"]))
            continue
        r["also"] = []
        seen[k] = r
        uniq.append(r)
    return uniq


def halves(sym, g, pct5):
    """Обе половины истории с готовыми фильтрами. Раскол — как у bots_honest."""
    candles = ev.fetch(sym, "15", bh.DAYS)
    aux = e8.make_aux_builder(pct5, 96)(sym, candles)
    n = len(candles)
    h = int(n * bh.HOLD_FRAC)
    out = {}
    for name, a, b in (("train", 0, h), ("hold", h, n)):
        c = candles[a:b]
        out[name] = dict(
            candles=c, pre=e2.prep(c),
            months=(c[-1][0] - c[0][0]) / (30 * 86400000),
            filt=e8.make_filter8(
                g, dict((k, bh.slice_aux(v, a, b)) for k, v in aux.items())))
    return out


def one_half(part, g, lev):
    """Прогон на половине. Итог — по кривой с ОБНУЛЁННЫМИ копеечными выходами.

    ЗДЕСЬ БЫЛА ТРЕТЬЯ ОШИБКА, и она молчаливая. Раньше тут стояло
    tiny.get("comp_nt", m["comp"]) — но ключа comp_nt в tiny_stats нет вовсе,
    и .get() тихо отдавал запасное значение, то есть СЫРОЙ итог вместе с
    копеечными выходами. У DOGE, где 84% циклов закрываются в ноль переносом
    стопа в безубыток, это завышало результат впятеро: +60.2% вместо +11.8%.
    Именно этот артефакт ревью проекта назвало фикцией, и он же тихо вернулся.

    Правильный способ — тот, которым пользуется сам bots_honest: строим кривую
    закрытий, обнуляем в ней копеечные (is_tiny), и считаем итог и просадку по
    ней через portfolio(). Так итог сложный (капитал переоценивается), а не
    суммой на фиксированной базе, и сопоставим между конфигами.

    Урок общий: .get() с запасным значением на ключе, которого может не быть, —
    это не защита, а способ получить неверное число без единой жалобы.
    """
    evs = []
    r = bh.run_at(part["candles"], part["pre"], g, part["filt"], lev,
                  events=evs)
    m = bh.summarize(r, evs, part["months"])
    tiny = bh.cycle_metrics(evs, part["candles"], g, part["months"])
    curve_nt = [(e["t"], 0.0 if bh.is_tiny(e["pnl"]) else e["pnl"])
                for e in evs if e["type"] == "close" and e["pnl"] is not None]
    comp_nt, dd_nt = bh.portfolio({"one": curve_nt}, 1)
    return dict(comp=float(comp_nt), dd=float(dd_nt),
                comp_raw=float(m.get("comp", 0.0)),
                trades=int(m.get("trades", 0)),
                tiny_share=float(tiny.get("tiny_share", 0.0)),
                on_artifact=bool(tiny.get("holds_on_artifact", False)),
                wr_honest=float(tiny.get("wr_honest", 0.0)),
                months=float(part["months"]))


def perturb(g, rng, k=0.10):
    """Сосед генома — ЧЕРЕЗ ШТАТНУЮ ФУНКЦИЮ ПРОЕКТА, а не самодельную.

    Своя реализация здесь была ошибкой, и поучительной. Она двигала числа, но
    не зажимала их в допустимые границы (clamp), из-за чего часть соседей
    оказывалась за пределами области определения генома. Такие соседи либо
    падают, либо считаются по бессмысленным значениям — и в обоих случаях
    медиана по ним говорит не об устойчивости конфига, а о качестве генератора
    соседей.

    bh.perturb делает три вещи, каждая из которых нужна: возмущает только
    числовые гены, зажимает результат в границы, объявленные в наборе генов, и
    возвращает дискретные переключатели ровно как были.
    """
    return bh.perturb(g, e8.GENES8, rng, k)


def task(job):
    idx, rec, lev, n_pert = job
    try:
        pct5 = xd.fetch_daily_pct5()
        hs = halves(rec["sym"], rec["g"], pct5)
        res = {}
        for name in ("train", "hold"):
            res[name] = one_half(hs[name], rec["g"], lev)
        # Устойчивость на ОБЕИХ половинах — главная поправка. Считать её только
        # там, где конфиг хорош, значит мерить не устойчивость, а везение.
        rng = np.random.default_rng(7)
        neigh = [perturb(rec["g"], rng) for _ in range(n_pert)]
        for name in ("train", "hold"):
            vals = []
            for ng in neigh:
                try:
                    vals.append(one_half(hs[name], ng, lev)["comp"])
                except Exception:                  # noqa: BLE001
                    continue
            res[name]["rob"] = float(np.median(vals)) if vals else 0.0
            res[name]["rob_n"] = len(vals)
        return dict(idx=idx, ok=True, train=res["train"], hold=res["hold"])
    except Exception as exc:                       # noqa: BLE001
        return dict(idx=idx, ok=False, err=str(exc)[:140])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lev", type=float, default=5.0)
    ap.add_argument("--pert", type=int, default=40)
    ap.add_argument("--jobs", type=int,
                    default=max(1, (os.cpu_count() or 4) - 3))
    a = ap.parse_args()

    recs = collect()
    print("собрано уникальных геномов: %d" % len(recs))
    by_sym = {}
    for r in recs:
        by_sym[r["sym"]] = by_sym.get(r["sym"], 0) + 1
    print("по монетам: %s"
          % ", ".join("%s %d" % (k.replace("USDT", ""), v)
                      for k, v in sorted(by_sym.items())))
    print("плечо для всех: x%g | возмущений на половину: %d | процессов: %d"
          % (a.lev, a.pert, a.jobs))
    print("прогонов всего: %d\n" % (len(recs) * 2 * (1 + a.pert)))
    sys.stdout.flush()

    xd.fetch_daily_pct5()          # прогреть кэш до разветвления процессов
    jobs = [(i, r, a.lev, a.pert) for i, r in enumerate(recs)]
    t0 = time.time()
    done = {}
    with Pool(a.jobs, maxtasksperchild=4) as pool:
        for k, r in enumerate(pool.imap_unordered(task, jobs), 1):
            done[r["idx"]] = r
            if k % 5 == 0 or k == len(jobs):
                print("  %d/%d  %.0fs" % (k, len(jobs), time.time() - t0))
                sys.stdout.flush()

    rows, failed, errs = [], 0, {}
    for i, rec in enumerate(recs):
        r = done.get(i)
        if not r or not r.get("ok"):
            failed += 1
            e = (r or {}).get("err", "нет результата")
            errs[e] = errs.get(e, 0) + 1
            continue
        tr, ho = r["train"], r["hold"]
        rows.append(dict(
            sym=rec["sym"], src=rec["src"], tag=rec["tag"],
            own_lev=rec["own_lev"], also=len(rec["also"]),
            train=tr, hold=ho,
            worst=min(tr["comp"], ho["comp"]),
            worst_rob=min(tr["rob"], ho["rob"]),
            genome=rec["g"]))
    # Конфиг без единой сделки — не «устойчивый ноль», а неработающий конфиг.
    # В таблице он выглядел бы приличнее убыточных, что явно неверно.
    dead = [x for x in rows if x["train"]["trades"] + x["hold"]["trades"] == 0]
    rows = [x for x in rows if x not in dead]
    rows.sort(key=lambda x: -x["worst_rob"])
    if not os.path.isdir(os.path.dirname(OUT)):
        os.makedirs(os.path.dirname(OUT))
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dict(lev=a.lev, pert=a.pert, rows=rows, failed=failed,
                       generated=time.strftime("%Y-%m-%d %H:%M")),
                  fh, ensure_ascii=False)

    print("\nВСЕ КОНФИГИ НА ОДНОМ ПЛЕЧЕ x%g, ОБЕ ПОЛОВИНЫ ИСТОРИИ" % a.lev)
    print("Порядок — по ХУДШЕЙ половине устойчивости: конфиг обязан быть")
    print("приличным на обеих, а не блистать на одной.\n")
    print("%-3s %-5s %-24s %9s %9s %9s %9s %7s"
          % ("№", "мон", "откуда", "обуч", "холд", "уст.обуч", "уст.холд",
             "сдел"))
    for n, x in enumerate(rows[:30], 1):
        print("%-3d %-5s %-24s %+8.1f%% %+8.1f%% %+8.1f%% %+8.1f%% %7d"
              % (n, x["sym"].replace("USDT", ""),
                 ("%s/%s" % (x["src"], x["tag"]))[:24],
                 x["train"]["comp"], x["hold"]["comp"],
                 x["train"]["rob"], x["hold"]["rob"],
                 x["hold"]["trades"]))
    good = [x for x in rows if x["worst_rob"] > 0]
    print("\nКонфигов, у которых ОБЕ половины устойчивости в плюсе: %d из %d"
          % (len(good), len(rows)))
    if failed:
        print("не посчитались: %d" % failed)
        for e, n in sorted(errs.items(), key=lambda x: -x[1])[:6]:
            print("   %3d x  %s" % (n, e))
    if dead:
        print("отсеяно неработающих (ноль сделок): %d" % len(dead))
    print("-> %s" % OUT)


if __name__ == "__main__":
    main()
