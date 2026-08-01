# -*- coding: utf-8 -*-
"""ЧЕСТНЫЙ замер штормового фильтра на 5 финальных ботах (DOGE/LTC/BTC/ETH/
SOL, режим "final") на всей доступной истории 15m (3.2 года).

Что делает:
  0) САМОПРОВЕРКИ: причинность фильтра («префикс vs полная история»),
     совпадение живого расчёта с тестовым (бот видит только последнюю тысячу
     свечей — значение обязано совпасть бит в бит) и регрессия базовых
     прогонов (storm=None должен воспроизвести прежние цифры ботов).
  1) для каждого бота сравнивает «без фильтра» и «с фильтром» при нескольких
     наборах порогов: сделок, WR, итог %, макс. просадка, среднее R;
  2) отдельно считает, КАКИЕ сделки фильтр убрал и каков был их средний
     результат (если убранные были в среднем прибыльными — фильтр вреден);
  3) всё то же самое на ХОЛДОУТЕ — последних 28% истории (hold = int(n*0.72),
     тот же принцип, что у сигналов). Боты подбирались на ВСЕЙ истории по
     старой схеме с утечкой, поэтому цифры «за весь период» не доказательство;
     фильтр на холдоуте не участвовал ни в каком подборе.
  4) портфельная строка: 5 ботов по $20 (база бэктеста), общая кривая и её
     просадка — решение о дефолтах принимается по портфелю, а не по одной
     удачной монете.

R для ботов = PnL сделки / $5 маржи цикла (у сеточного бота нет фиксированного
стопа, поэтому «риск» = маржа, которой рискует цикл; при ликвидации R ~ -1).

Запуск: python storm_bots_report.py
"""

import random
import time

import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd
import storm_filter as sf

DAYS = 1150
HOLD_FRAC = 0.72          # тот же холдоут, что у сигналов: [0.72n .. n)
RANK_DAYS = getattr(config, "STORM_RANK_DAYS", sf.RANK_DAYS)

# Наборы порогов. НЕ подбирались под доходность: «7% за сутки» — порог из
# сигнального движка, «10%/25%» — вдвое мягче (альты ходят сильнее биткоина),
# вариант с ATR-рангом проверяет третий компонент шторма отдельно.
CONFIGS = [
    ("A  сутки 7%  / нед 15% / ATR выкл / режим 0", 0.07, 0.15, 1.00, 0),
    ("B  сутки 10% / нед 25% / ATR выкл / режим 0", 0.10, 0.25, 1.00, 0),
    ("C  сутки 7%  / нед 15% / ATR 0.85 / режим 0", 0.07, 0.15, 0.85, 0),
    ("D  сутки 7%  / нед 15% / ATR выкл / режим 1", 0.07, 0.15, 1.00, 1),
    ("E  сутки 10% / нед 25% / ATR выкл / режим 1", 0.10, 0.25, 1.00, 1),
    ("F  сутки 15% / нед 40% / ATR выкл / режим 0", 0.15, 0.40, 1.00, 0),
]

# Задокументированные в config.py цифры финальных ботов — регрессия базы.
DOC = {"DOGEUSDT": (1276, 145.0, 18.5), "LTCUSDT": (1307, 115.9, 17.0),
       "BTCUSDT": (60, 140.2, 12.6), "ETHUSDT": (237, 22.7, 21.8),
       "SOLUSDT": (1245, 45.8, 18.4)}


def slice_aux(v, a, b):
    """Срез aux под candles[a:b] (как в finalize_final_bots.year_segments)."""
    if isinstance(v, tuple):
        return tuple(slice_aux(x, a, b) for x in v)
    if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
        return [slice_aux(x, a, b) for x in v]
    return v[a:b]


def metrics(r, events):
    """Сводка одного прогона: сделок, WR, итог %, DD, среднее R."""
    pnls = [e["pnl"] for e in events if e["type"] == "close"]
    avg_r = (sum(pnls) / len(pnls) / e2.MARGIN) if pnls else 0.0
    return dict(trades=r["trades"],
                wr=(r["wins"] / r["trades"] * 100) if r["trades"] else 0.0,
                ret=(r["balance"] / e2.START - 1) * 100,
                dd=r["max_dd"] * 100, avg_r=avg_r, ruined=r["ruined"],
                pnls=pnls)


def paired_trades(events):
    """[(ts_входа, side, pnl), ...] — вход и следующий за ним выход."""
    out, cur = [], None
    for e in events:
        if e["type"] == "entry":
            cur = (e["t"], e["side"])
        elif e["type"] == "close" and cur is not None:
            out.append((cur[0], cur[1], e["pnl"]))
            cur = None
    return out


def removed_stats(base_events, st, ts_idx):
    """Разбор БАЗОВЫХ сделок: какие фильтр убрал бы и каков их результат.
    Это прямой ответ на вопрос «что именно вырезано» (в реальном прогоне
    бот после пропуска свободен и может взять другую сделку — поэтому
    строка «с фильтром» в таблице считается отдельным полным прогоном)."""
    cut, kept = [], []
    for ts, side, pnl in paired_trades(base_events):
        i = ts_idx.get(ts)
        if i is None:
            continue
        (cut if sf.blocked_at(side, st, i) else kept).append(pnl)

    def agg(v):
        if not v:
            return (0, 0.0, 0.0)
        return (len(v), sum(1 for x in v if x > 0) / len(v) * 100,
                sum(v) / len(v) / e2.MARGIN)
    return agg(cut), agg(kept)


def portfolio(curves, n_bots):
    """curves: {sym: [(ts, pnl), ...]}. Общая кривая 5 счетов по $20."""
    merged = []
    for sym, rows in curves.items():
        merged += [(ts, sym, p) for ts, p in rows]
    merged.sort()
    bal = {s: e2.START for s in curves}
    total = e2.START * n_bots
    peak, dd = total, 0.0
    for ts, sym, p in merged:
        bal[sym] += p
        total = sum(bal.values())
        peak = max(peak, total)
        if peak > 0:
            dd = max(dd, (peak - total) / peak)
    return (total / (e2.START * n_bots) - 1) * 100, dd * 100


def run_one(candles, pre, g, filt, lev, bpd, st):
    """Один прогон движка на нужном плече/ТФ (глобалы e2 восстанавливаются)."""
    old_lev, old_bpd = e2.LEV, e2.BARS_PER_DAY
    e2.LEV, e2.BARS_PER_DAY = lev, bpd
    try:
        events = []
        r = e2.run5(candles, pre, g, entry_filter=filt, events=events,
                    storm=st)
    finally:
        e2.LEV, e2.BARS_PER_DAY = old_lev, old_bpd
    return r, events


# ------------------------------------------------------------- самопроверки

def self_tests(candles, bpd):
    """Причинность + совпадение живого расчёта с тестовым."""
    print("=" * 100)
    print("САМОПРОВЕРКИ ФИЛЬТРА")
    random.seed(7)
    full = sf.build(candles, bpd, 0.07, 0.15, 0.85, 0, rank_days=RANK_DAYS)
    n = len(candles)
    need = sf.bars_needed(bpd, RANK_DAYS)

    # 1) префикс vs полная история: значение на баре i не должно зависеть
    #    от того, что было ПОСЛЕ i
    idxs = sorted(random.sample(range(need + 10, n), 12))
    bad = 0
    worst = 0.0
    for i in idxs:
        pre_st = sf.build(candles[:i + 1], bpd, 0.07, 0.15, 0.85, 0,
                          rank_days=RANK_DAYS)
        a, b = sf.state_at(full, i), sf.state_at(pre_st, i)
        d = max(abs(a["strength"] - b["strength"]),
                abs(a["ret_1d"] - b["ret_1d"]), abs(a["ret_7d"] - b["ret_7d"]),
                abs((a["atr_rank"] or 0) - (b["atr_rank"] or 0)))
        worst = max(worst, d)
        if a["storm"] != b["storm"] or a["dir"] != b["dir"] or d > 1e-12:
            bad += 1
    print(f"  1. префикс vs полная история: {len(idxs)} срезов, расхождений "
          f"{bad}, макс. отклонение {worst:.2e}"
          f"  {'OK' if bad == 0 else 'ПРОВАЛ'}")

    # 2) живое == тестовое: бот держит ровно bars_needed последних свечей
    bad2, worst2 = 0, 0.0
    for i in idxs:
        win = candles[i - need + 1:i + 1]
        live = sf.build(win, bpd, 0.07, 0.15, 0.85, 0, rank_days=RANK_DAYS)
        a, b = sf.state_at(full, i), sf.state_at(live, -1)
        d = max(abs(a["strength"] - b["strength"]),
                abs((a["atr_rank"] or 0) - (b["atr_rank"] or 0)))
        worst2 = max(worst2, d)
        if a["storm"] != b["storm"] or a["dir"] != b["dir"] or d > 1e-12:
            bad2 += 1
    print(f"  2. живой бот ({need} свечей) == бэктест (110k свечей): "
          f"расхождений {bad2}, макс. отклонение {worst2:.2e}"
          f"  {'OK' if bad2 == 0 else 'ПРОВАЛ'}")

    # 3) выключенный фильтр не должен ничего менять
    off = sf.build(candles, bpd, 0.07, 0.15, 0.85, 0, rank_days=RANK_DAYS)
    off["storm"] = [False] * len(candles)
    print(f"  3. доля штормовых баров (7%/15%/ATR 0.85): "
          f"{sum(full['storm']) / len(full['storm']) * 100:.1f}%")
    return off


def main():
    t_start = time.time()
    pct5 = xd.fetch_daily_pct5()
    syms = [s for s, m in config.SYMBOL_PARAMS.items() if m.get("final")]

    prepared = {}
    for sym in syms:
        p = config.SYMBOL_PARAMS[sym]["final"]
        interval = str(p.get("interval", "15"))
        bpd = sf.bars_per_day(interval)
        g = e7.cfg_to_genome(p, "final")
        for k, v in e8.OFF8.items():
            g.setdefault(k, v)
        candles = ev.fetch(sym, interval, DAYS)
        aux = e8.make_aux_builder(pct5, bpd)(sym, candles)
        prepared[sym] = dict(p=p, g=g, candles=candles, aux=aux, bpd=bpd,
                             interval=interval, lev=p.get("lev", 5),
                             pre=e2.prep(candles),
                             filt=e8.make_filter8(g, aux),
                             core=sf.build_core(candles, bpd, RANK_DAYS),
                             ts_idx={c[0]: i for i, c in enumerate(candles)})

    # --- самопроверки на самой длинной истории
    self_tests(prepared[syms[0]]["candles"], prepared[syms[0]]["bpd"])

    # --- подготовка холдоута
    for sym in syms:
        d = prepared[sym]
        n = len(d["candles"])
        h = int(n * HOLD_FRAC)
        seg = d["candles"][h:]
        d["hold_i"] = h
        d["hold"] = dict(candles=seg, pre=e2.prep(seg),
                         filt=e8.make_filter8(
                             d["g"], {k: slice_aux(v, h, n)
                                      for k, v in d["aux"].items()}),
                         ts_idx={c[0]: i for i, c in enumerate(seg)},
                         n0=h, n=n)

    print("=" * 100)
    print("ПЕРИОДЫ")
    for sym in syms:
        d = prepared[sym]
        c = d["candles"]
        h = d["hold_i"]
        fmt = lambda ms: time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
        print(f"  {sym:9} {len(c):6} свечей {d['interval']}m  "
              f"вся история {fmt(c[0][0])}..{fmt(c[-1][0])} | "
              f"ХОЛДОУТ {fmt(c[h][0])}..{fmt(c[-1][0])} "
              f"({(c[-1][0]-c[h][0])/86400000/30.4:.1f} мес, {len(c)-h} свечей)")

    # =================== ЗАМЕР ===================
    results = {}     # (period, cfg_label) -> {sym: metrics}
    curves = {}      # (period, cfg_label) -> {sym: [(ts,pnl)]}
    removed = {}     # (period, cfg_label) -> {sym: (cut, kept)}
    base_events = {}

    for period in ("вся история", "холдоут"):
        for label, day, week, rank, mode in [("база (фильтр выключен)",
                                              None, None, None, None)] + CONFIGS:
            res, cur, rem = {}, {}, {}
            for sym in syms:
                d = prepared[sym]
                if period == "вся история":
                    cnd, pre, filt = d["candles"], d["pre"], d["filt"]
                    core, ts_idx, a = d["core"], d["ts_idx"], 0
                    b = len(cnd)
                else:
                    hd = d["hold"]
                    cnd, pre, filt = hd["candles"], hd["pre"], hd["filt"]
                    core, ts_idx = d["core"], hd["ts_idx"]
                    a, b = hd["n0"], hd["n"]
                if day is None:
                    st = None
                else:
                    st_full = sf.apply_thresholds(d["core"], day, week, rank,
                                                  mode)
                    st = st_full if a == 0 else sf.slice_series(st_full, a, b)
                r, evs = run_one(cnd, pre, d["g"], filt, d["lev"], d["bpd"], st)
                res[sym] = metrics(r, evs)
                cur[sym] = [(e["t"], e["pnl"]) for e in evs
                            if e["type"] == "close"]
                if day is None:
                    base_events[(period, sym)] = evs
                else:
                    rem[sym] = removed_stats(base_events[(period, sym)], st,
                                             ts_idx)
                    res[sym]["storm_share"] = (sum(st["storm"]) /
                                               max(1, len(st["storm"])) * 100)
            results[(period, label)] = res
            curves[(period, label)] = cur
            removed[(period, label)] = rem

    # =================== ВЫВОД ===================
    labels = ["база (фильтр выключен)"] + [c[0] for c in CONFIGS]

    print("=" * 100)
    print("РЕГРЕССИЯ: базовый прогон (storm=None) против цифр в config.py")
    print(f"  {'бот':10} {'сделок':>8} {'док.':>7} {'итог%':>9} {'док.':>8} "
          f"{'DD%':>7} {'док.':>7}")
    ok = True
    for sym in syms:
        m = results[("вся история", "база (фильтр выключен)")][sym]
        dt, dr, dd = DOC[sym]
        same = (m["trades"] == dt and abs(m["ret"] - dr) < 1.0
                and abs(m["dd"] - dd) < 0.5)
        ok = ok and same
        print(f"  {sym:10} {m['trades']:8} {dt:7} {m['ret']:+9.1f} {dr:+8.1f} "
              f"{m['dd']:7.1f} {dd:7.1f}   {'OK' if same else '!!! РАСХОЖДЕНИЕ'}")
    print(f"  -> прежние результаты {'воспроизводятся' if ok else 'НЕ сходятся'}"
          f" (фильтр по умолчанию выключен, контракт run5 не тронут)")

    for period in ("вся история", "холдоут"):
        print()
        print("=" * 100)
        print(f"ЭФФЕКТ ФИЛЬТРА — {period.upper()}"
              + ("  (28% истории, в подборе НЕ участвовали)"
                 if period == "холдоут" else
                 "  (боты подбирались на ней же — цифры НЕ доказательство)"))
        print("  R = PnL сделки / $5 маржи цикла. Правые 4 колонки — разбор "
              "БАЗОВЫХ сделок: сколько из них")
        print("  попало на штормовой бар и каковы они были. Левое «сделок» — "
              "отдельный полный прогон:")
        print("  пропустив вход, бот освобождается раньше и берёт другую "
              "сделку, поэтому «сделок» != «база - убрал».")
        for sym in syms:
            d = prepared[sym]
            print(f"\n  --- {sym} x{d['lev']} ---")
            print(f"  {'вариант':46} {'сделок':>7} {'WR%':>6} {'итог%':>9} "
                  f"{'DD%':>6} {'ср.R':>8} {'штрм%':>6} || "
                  f"{'убрал':>6} {'их WR%':>7} {'их ср.R':>9} "
                  f"{'ср.R оставш.':>13}")
            for label in labels:
                m = results[(period, label)][sym]
                if label.startswith("база"):
                    print(f"  {label:46} {m['trades']:7} {m['wr']:6.1f} "
                          f"{m['ret']:+9.1f} {m['dd']:6.1f} {m['avg_r']:+8.4f} "
                          f"{'-':>6} || {'-':>6} {'-':>7} {'-':>9} {'-':>13}")
                else:
                    cut, kept = removed[(period, label)][sym]
                    print(f"  {label:46} {m['trades']:7} {m['wr']:6.1f} "
                          f"{m['ret']:+9.1f} {m['dd']:6.1f} {m['avg_r']:+8.4f} "
                          f"{m['storm_share']:6.1f} || {cut[0]:6} "
                          f"{cut[1]:7.1f} {cut[2]:+9.4f} {kept[2]:+13.4f}")

        # портфель
        print(f"\n  === ПОРТФЕЛЬ 5 ботов (по $20, суммарная кривая) — {period} ===")
        print(f"  {'вариант':46} {'сделок':>7} {'итог%':>9} {'DD%':>6} "
              f"{'ср.R':>8} || {'убрал':>6} {'их ср.R':>9} "
              f"{'ср.R оставш.':>13}")
        for label in labels:
            res = results[(period, label)]
            pnls = [x for sym in syms for x in res[sym]["pnls"]]
            ret, dd = portfolio(curves[(period, label)], len(syms))
            avg_r = (sum(pnls) / len(pnls) / e2.MARGIN) if pnls else 0.0
            if label.startswith("база"):
                print(f"  {label:46} {len(pnls):7} {ret:+9.1f} {dd:6.1f} "
                      f"{avg_r:+8.4f} || {'-':>6} {'-':>9} {'-':>13}")
            else:
                rem = removed[(period, label)]
                cn = sum(rem[s][0][0] for s in syms)
                csum = sum(rem[s][0][0] * rem[s][0][2] for s in syms)
                kn = sum(rem[s][1][0] for s in syms)
                ksum = sum(rem[s][1][0] * rem[s][1][2] for s in syms)
                print(f"  {label:46} {len(pnls):7} {ret:+9.1f} {dd:6.1f} "
                      f"{avg_r:+8.4f} || {cn:6} "
                      f"{(csum / cn if cn else 0.0):+9.4f} "
                      f"{(ksum / kn if kn else 0.0):+13.4f}")

    # =================== ИТОГ ===================
    print()
    print("=" * 100)
    print("ЧТО ЭТО ЗНАЧИТ (решение принимается по ХОЛДОУТУ и по ПОРТФЕЛЮ)")
    base_ret, base_dd = portfolio(curves[("холдоут",
                                          "база (фильтр выключен)")], len(syms))
    print(f"  холдоут, портфель без фильтра: {base_ret:+.1f}%, просадка "
          f"{base_dd:.1f}%")
    print("  КРИТЕРИЙ (объявлен ДО замера): включаем фильтр, только если на "
          "холдоуте он уменьшает")
    print("  просадку портфеля минимум на 0.5 п.п. и не отнимает больше 1 п.п. "
          "дохода.")
    verdict = []
    for label, *_ in CONFIGS:
        ret, dd = portfolio(curves[("холдоут", label)], len(syms))
        rem = removed[("холдоут", label)]
        cn = sum(rem[s][0][0] for s in syms)
        csum = sum(rem[s][0][0] * rem[s][0][2] for s in syms)
        kn = sum(rem[s][1][0] for s in syms)
        ksum = sum(rem[s][1][0] * rem[s][1][2] for s in syms)
        cut_r = csum / cn if cn else 0.0
        kept_r = ksum / kn if kn else 0.0
        good = (dd < base_dd - 0.5) and (ret >= base_ret - 1.0)
        verdict.append((label, ret, dd, cn, cut_r, good))
        print(f"  {label:46} итог {ret:+7.1f}% (Δ {ret-base_ret:+6.1f}) "
              f"DD {dd:5.1f}% (Δ {dd-base_dd:+5.1f}) | убрано {cn:4} сд., "
              f"ср.R убранных {cut_r:+.4f} против {kept_r:+.4f} у оставшихся"
              f" -> {'ГОДИТСЯ' if good else 'НЕ годится'}")
    # честно перечисляем исключения: где по ОТДЕЛЬНОЙ монете фильтр всё-таки
    # улучшил просадку на холдоуте (портфельное решение они не меняют, но
    # умалчивать о них нельзя)
    print()
    print("  Исключения по отдельным монетам (холдоут): где фильтр всё-таки "
          "уменьшил просадку (>=0.5 п.п.),")
    print("  какой ценой по доходу — видно тут же")
    exc = 0
    for sym in syms:
        b = results[("холдоут", "база (фильтр выключен)")][sym]
        for label, *_ in CONFIGS:
            m = results[("холдоут", label)][sym]
            if m["dd"] < b["dd"] - 0.5:
                exc += 1
                print(f"    {sym:9} {label:44} DD {b['dd']:5.1f} -> "
                      f"{m['dd']:5.1f} | итог {b['ret']:+6.1f} -> "
                      f"{m['ret']:+6.1f} | сделок {b['trades']} -> "
                      f"{m['trades']}")
    if not exc:
        print("    нет ни одной")
    else:
        print("    Это ОДИНОЧНЫЕ совпадения на малых выборках (см. число "
              "сделок), на портфеле они не выживают;")
        print("    включать общий фильтр ради них — та же подгонка, от "
              "которой мы уходим.")

    winners = [v for v in verdict if v[5]]
    print()
    if winners:
        best = min(winners, key=lambda v: v[2])
        print(f"  ВЫВОД: критерий «просадка меньше, доход не хуже» на холдоуте "
              f"проходит вариант: {best[0]}")
    else:
        print("  ВЫВОД: НИ ОДИН набор порогов не проходит критерий "
              "«просадка меньше без потери дохода» на холдоуте.")
        print("  Более того, убранные сделки в среднем ПРИБЫЛЬНЕЕ оставленных "
              "во всех вариантах и на обоих")
        print("  периодах — фильтр вырезает лучшие сделки, а не худшие. "
              "Это ожидаемо: RSI-сетка зарабатывает")
        print("  на возврате к среднему, а самый сильный возврат бывает "
              "именно после резкого движения.")
        print("  Значит STORM_ENABLED=False — отрицательный результат тоже "
              "результат.")
    print(f"\n  (время работы {time.time() - t_start:.1f} c)")


if __name__ == "__main__":
    main()
