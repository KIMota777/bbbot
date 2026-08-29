# -*- coding: utf-8 -*-
"""Кривые капитала в ОБЩЕЙ шкале: все конфиги сравнимы между собой.

ЗАЧЕМ ОТДЕЛЬНЫЙ СБОРЩИК. Прежний build_pnl_curves.py строит кривую каждого
бота на ЕГО РОДНОМ плече и вместе с копеечными выходами. Для одной кривой это
законно — так бот и торговал бы. Но когда такие кривые лежат на одном холсте,
сравнение получается ложным сразу по двум причинам:

  * ПЛЕЧО. BTC нарисован на x15, SOL на x5. Кривая x15 круче просто потому,
    что ставка втрое больше, а не потому, что стратегия лучше.
  * КОПЕЕЧНЫЕ ВЫХОДЫ. Цикл, закрытый переносом стопа в безубыток, даёт ноль,
    но в кривой выглядит как сделка. У SOL таких 90%, у DOGE 87%. Их вклад
    надувает кривую там, где никакого дохода не было.

Здесь всё считается на ОДНОМ плече и с обнулением копеечных, поэтому кривые
можно класть рядом и сравнивать глазами. Числа получаются заметно скромнее
прежних — это не поломка, это снятая надбавка.

Что попадает на график:
  * пять запущенных ботов (режим final);
  * конфиги, прошедшие честную переоценку (уровень A и B) — сейчас SOL bear и
    LTC normal, они НЕ запущены;
  * граница холдоута отдельной меткой: слева период, на котором конфиги
    подбирались, справа — на котором проверялись.

Запуск: python build_pnl_common.py [--lev 5]
Результат: webapp/data/pnl_common.json
"""
import argparse
import json
import os

import adaptive_ltc as _al
import all_configs_honest as ach
import bots_honest as bh
import config
import evolution as ev
import evolution2 as e2
import evolution8 as e8
import ext_data as xd
from all_configs_honest import with_defaults

e2.BARS_PER_DAY = 96
OUT = os.path.join("webapp", "data", "pnl_common.json")
SLEEVE0 = 50.0                       # стартовый капитал каждой стратегии
COLORS = {
    "BTCUSDT": "#ff7a30", "ETHUSDT": "#c88cff", "SOLUSDT": "#46C186",
    "LTCUSDT": "#4aa8ff", "DOGEUSDT": "#f0b90b",
}
MAX_POINTS = 900


def load_tier_map():
    """Уровень каждого (символ, режим) из посчитанного тир-листа."""
    path = os.path.join("webapp", "data", "tierlist.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh).get("rows", [])
    out = {}
    for r in rows:
        if r.get("src") == "config.py":
            out[(r["sym"], r["tag"])] = r["tier"]
    # режимы с одинаковым геномом наследуют уровень друг у друга
    for sym, modes in config.SYMBOL_PARAMS.items():
        have = [m for m in modes
                if isinstance(modes[m], dict) and (sym, m) in out]
        for mode, prm in modes.items():
            if not isinstance(prm, dict) or (sym, mode) in out:
                continue
            for m in have:
                if modes[m] == prm:
                    out[(sym, mode)] = out[(sym, m)]
                    break
    return out


def run_halves(sym, g, lev, pct5):
    """Два прогона — обучающая половина и холдоут — ТЕМ ЖЕ способом, что и
    тир-лист. Возвращает события обеих половин и границу между ними.

    ПОЧЕМУ НЕ ОДНИМ ПРОГОНОМ ПО ВСЕЙ ИСТОРИИ. Так было сделано сначала, и
    числа разошлись с карточками до смены знака: у LTC normal тир-лист
    показывает обе половины в плюсе, а сплошной прогон дал -22.6%. Причина не
    в ошибке, а в том, что это разные вещи: при раздельных прогонах индикаторы
    пересчитываются на своей половине и состояние через границу не переносится,
    при сплошном — переносится, и последовательность сделок получается другой.

    График обязан говорить то же, что карточка рядом с ним. Поэтому здесь тот
    же раздельный способ, и база та же — e2.START, а не своя.
    """
    candles = ev.fetch(sym, "15", bh.DAYS)
    aux = e8.make_aux_builder(pct5, 96)(sym, candles)
    n = len(candles)
    h = int(n * bh.HOLD_FRAC)
    out = []
    for a, b in ((0, h), (h, n)):
        c = candles[a:b]
        filt = e8.make_filter8(
            g, dict((k, bh.slice_aux(v, a, b)) for k, v in aux.items()))
        # Гейт применяется ТЕМ ЖЕ определением, что и в оценке и в боевом боте.
        # Без этого конфиг с гейтом рисовался бы кривой конфига БЕЗ гейта — и
        # график молча показывал бы не то, что подписано. Так и случилось при
        # первой сборке: LTC final_g совпал с LTC final до последней точки.
        thr = float(g.get("vol_gate", 0.0) or 0.0)
        if thr > 0:
            filt = ach._gate_filter(filt, _al.regimes(c), thr)
        # Ряд настоящих ставок фандинга для этой половины. В файлах проекта
        # он в ПРОЦЕНТАХ — делим на 100. Без него график считался бы плоской
        # ставкой, а карточка рядом — настоящими, и подпись «график говорит то
        # же, что карточка» перестала бы быть правдой.
        _f = aux.get("fund")
        _f = ([(v / 100.0 if v is not None else 0.0)
               for v in bh.slice_aux(_f, a, b)] if _f is not None else None)
        evs = []
        bh.run_at(c, e2.prep(c), g, filt, lev, events=evs, funding=_f)
        out.append(evs)
    return out[0], out[1], int(candles[h][0]), int(candles[0][0])


def curve(evs, sleeve=None):
    """Кривая капитала с ОБНУЛЁННЫМИ копеечными выходами.

    Обнуление, а не выбрасывание: цикл был, время он занимал, и на графике
    между сделками должна остаться пауза, а не склейка. Просто его вклад в
    капитал равен нулю — чем он фактически и является.
    """
    sleeve = e2.START if sleeve is None else sleeve
    pts, eq, peak, dd = [], sleeve, sleeve, 0.0
    closes = [e for e in evs
              if e.get("type") == "close" and e.get("pnl") is not None]
    for e in closes:
        pnl = 0.0 if bh.is_tiny(e["pnl"]) else float(e["pnl"])
        eq += pnl
        peak = max(peak, eq)
        if peak > 0:
            dd = max(dd, (peak - eq) / peak)
        pts.append([int(e["t"]), round(eq, 4)])
    return pts, round(dd * 100, 1), len([e for e in closes
                                         if not bh.is_tiny(e["pnl"])])


def thin(points, cap=MAX_POINTS):
    """Прореживание для холста. Последняя точка сохраняется всегда."""
    if len(points) <= cap:
        return points
    step = len(points) / float(cap)
    out = [points[int(i * step)] for i in range(cap)]
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lev", type=float, default=5.0)
    a = ap.parse_args()

    pct5 = xd.fetch_daily_pct5()
    tiers = load_tier_map()

    # Кого рисуем: запущенные плюс ВСЕ прошедшие проверку, включая победителей
    # волн. Последние живут только в json-файлах отбора и в config.py их нет,
    # поэтому берутся из общего сборщика геномов, а не из конфига.
    import all_configs_honest as ach
    tier_full = {}
    for r in json.load(open(os.path.join("webapp", "data", "tierlist.json"),
                            encoding="utf-8"))["rows"]:
        tier_full[(r["sym"], r["src"], r["tag"])] = r["tier"]

    import evolution7 as _e7
    wanted = []
    seen_g = set()

    def _add(sym, mode, prm, kind, src="config.py"):
        """Добавить конфиг на график, если такой кривой ещё нет.

        Отсев по геному, а не по имени: у разных режимов геном бывает
        побайтово одинаковым, и тогда на холсте появились бы две линии там,
        где стратегия одна.
        """
        try:
            g = with_defaults(_e7.cfg_to_genome(prm, mode))
        except Exception:                          # noqa: BLE001
            return
        if prm.get("vol_gate"):
            g["vol_gate"] = float(prm["vol_gate"])
        key = json.dumps(g, sort_keys=True, default=str)
        if key in seen_g:
            return
        seen_g.add(key)
        star = "" if kind == "работает" else " ★"
        wanted.append(dict(sym=sym, mode=mode, src=src, g=g, kind=kind,
                           label="%s · %s%s" % (sym.replace("USDT", ""),
                                                mode, star)))

    # На график идут запущенные (final), конфиги с гейтом и конфиги с
    # раздвинутыми уровнями. Отставленные не идут: витрина их не предлагает,
    # и на графике им тоже нечего делать.
    _KIND = {"final": "работает", "normal_g": "с гейтом", "final_g": "с гейтом",
             "normal_w": "уровни раздвинуты", "final_w": "уровни раздвинуты",
             "bear_w": "уровни раздвинуты"}
    for sym, modes in config.SYMBOL_PARAMS.items():
        for mode, kind in _KIND.items():
            prm = modes.get(mode)
            if not isinstance(prm, dict):
                continue
            if hasattr(config, "is_retired") and config.is_retired(sym, mode):
                continue
            _add(sym, mode, prm, kind)

    # плюс победители волн, прошедшие переоценку
    for rec in ach.collect():
        if tier_full.get((rec["sym"], rec["src"], rec["tag"])) not in ("A", "B"):
            continue
        if rec["src"] == "config.py":
            continue
        key = json.dumps(with_defaults(rec["g"]), sort_keys=True, default=str)
        if key in seen_g:
            continue
        seen_g.add(key)
        wanted.append(dict(
            sym=rec["sym"], mode=rec["tag"], src=rec["src"],
            g=with_defaults(rec["g"]), kind="прошёл проверку",
            label="%s · %s ★" % (rec["sym"].replace("USDT", ""),
                                 rec["src"].replace("_winners", "")
                                 .replace("_final", ""))))

    series, hold_ts, first_ts = [], None, None
    for w in wanted:
        sym, mode, kind = w["sym"], w["mode"], w["kind"]
        if w["g"] is not None:
            g = with_defaults(w["g"])
        else:
            p = config.SYMBOL_PARAMS[sym][mode]
            g = with_defaults(__import__("evolution7").cfg_to_genome(p, mode))
        print("%s/%s (%s)..." % (sym, w["src"], kind))
        evs_tr, evs_ho, h_ts, f_ts = run_halves(sym, g, a.lev, pct5)
        hold_ts = hold_ts or h_ts
        first_ts = first_ts or f_ts
        # склейка: холдоут продолжается с того капитала, чем кончилось обучение
        pts_tr, dd_tr, n_tr = curve(evs_tr)
        start_ho = pts_tr[-1][1] if pts_tr else e2.START
        pts_ho, dd_ho, n_ho = curve(evs_ho, sleeve=start_ho)
        pts = pts_tr + pts_ho
        dd = max(dd_tr, dd_ho)
        n_real = min(n_tr, n_ho)
        if not pts:
            print("   сделок нет — пропуск")
            continue
        tier = tier_full.get((sym, w["src"], mode), "?")
        # Отсев по РЕЗУЛЬТАТУ, а не по геному. Геномы могут отличаться
        # округлением записи в config.py и парой безразличных генов, а кривая
        # получаться тождественной. Рисовать её дважды значит показывать две
        # стратегии там, где она одна; вместо этого второй записывается
        # псевдонимом к первой.
        sig = (round(pts[-1][1], 4), dd, n_real, len(pts))
        twin = next((x for x in series if x["_sig"] == sig), None)
        if twin is not None:
            twin.setdefault("same_as", []).append(w["label"])
            print("   кривая совпала с «%s» — записан псевдонимом"
                  % twin["label"])
            continue
        series.append(dict(
            _sig=sig,
            key="%s_%s_%s" % (sym, w["src"], mode),
            label=w["label"],
            color=COLORS.get(sym, "#aaaaaa"),
            group="live" if kind == "работает" else "passed",
            kind=kind, tier=tier,
            dashed=(kind != "работает"),
            final_usd=round(pts[-1][1], 2),
            final_pct=round((pts[-1][1] / e2.START - 1) * 100, 1),
            # доля холдоута отдельно: именно она проверочная
            hold_pct=round((pts[-1][1] / start_ho - 1) * 100, 1)
            if start_ho else 0.0,
            dd=dd, dd_train=dd_tr, dd_hold=dd_ho,
            trades=n_real, trades_train=n_tr, trades_hold=n_ho,
            points=thin(pts)))
        print("   итог %+.1f%% | просадка %.1f%% | настоящих сделок %d"
              % (series[-1]["final_pct"], dd, n_real))

    for x in series:
        x.pop("_sig", None)
    series.sort(key=lambda s: (s["group"] != "passed", -s["final_pct"]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dict(sleeve=e2.START, lev=a.lev, series=series,
                       holdout_ts=hold_ts, first_ts=first_ts), fh,
                  ensure_ascii=False)
    print("\nвсего кривых: %d, плечо x%g у всех, копеечные обнулены"
          % (len(series), a.lev))
    print("-> %s" % OUT)


if __name__ == "__main__":
    main()
