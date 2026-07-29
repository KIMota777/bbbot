# -*- coding: utf-8 -*-
"""v8: геном v7 + Smart Money Concepts (ордер-блоки, FVG, структура
BOS/CHoCH) как гены, прогнанные ОТДЕЛЬНО на 15m и на 4ч (Bybit: "240") —
чтобы честно сравнить таймфреймы, а не гадать. Паттерны с чарт-шита (v7)
тоже участвуют заново — на новом таймфрейме геометрия свечей другая,
прежний отрицательный результат мог не воспроизвестись.

Новые гены поверх v7 (=GENES7):
  ob_gate         (0..2): 0 выкл, 1 требовать совпадающий ордер-блок,
                          2 запрещать только явное противоречие
  fvg_gate        (0..2): то же для Fair Value Gap
  structure_mode  (0..2): 0 выкл, 1 торговать ПО структуре (BOS/CHoCH),
                          2 торговать ПРОТИВ

Методология подтверждения: walk-forward из 3 экзаменов (та же fold_bounds_3y),
как во всех предыдущих волнах. Инфраструктура — общий харнесс e4.run_version,
теперь параметризованный по interval/days (правки в evolution4.py).

Запуск: python evolution8.py
"""

import json
import time

import config
import evolution as ev
import evolution2 as e2
import evolution4 as e4
import evolution5 as e5
import evolution6 as e6
import evolution7 as e7
import ext_data as xd
import patterns as pt
import smc

DAYS = 1150
LEVS = [5, 8, 10, 12, 15]
DD_CAP = 0.20

GENES8 = dict(e7.GENES7)
GENES8.update({
    "ob_gate": (0, 2, True),
    "fvg_gate": (0, 2, True),
    "structure_mode": (0, 2, True),
})
OFF8 = dict(ob_gate=0, fvg_gate=0, structure_mode=0)


def make_filter8(g, aux):
    base_f = e7.make_filter7(g, aux)
    ob_bull, ob_bear = aux.get("ob_bull"), aux.get("ob_bear")
    fvg_bull, fvg_bear = aux.get("fvg_bull"), aux.get("fvg_bear")
    structure = aux.get("structure")

    def f(side, i):
        side = base_f(side, i)
        if not side:
            return None
        if g["ob_gate"] and ob_bull is not None:
            if g["ob_gate"] == 1:
                if side == "L" and not ob_bull[i]:
                    return None
                if side == "S" and not ob_bear[i]:
                    return None
            else:
                if side == "L" and ob_bear[i] and not ob_bull[i]:
                    return None
                if side == "S" and ob_bull[i] and not ob_bear[i]:
                    return None
        if g["fvg_gate"] and fvg_bull is not None:
            if g["fvg_gate"] == 1:
                if side == "L" and not fvg_bull[i]:
                    return None
                if side == "S" and not fvg_bear[i]:
                    return None
            else:
                if side == "L" and fvg_bear[i] and not fvg_bull[i]:
                    return None
                if side == "S" and fvg_bull[i] and not fvg_bear[i]:
                    return None
        if g["structure_mode"] and structure is not None:
            bias = structure[i]
            want_long = (bias > 0) if g["structure_mode"] == 1 else (bias < 0)
            want_short = (bias < 0) if g["structure_mode"] == 1 else (bias > 0)
            if side == "L" and not want_long:
                return None
            if side == "S" and not want_short:
                return None
        return side

    return f


def make_aux_builder(pct5, bars_per_day):
    def aux_builder(sym, candles):
        aux = e7.build_aux_full(sym, candles, pct5)
        # переcчитываем паттерны с верным bars_per_day для этого таймфрейма
        aux["pat_bull"], aux["pat_bear"] = pt.compute_pattern_signals(
            candles, bars_per_day=bars_per_day)
        t0 = time.time()
        aux["ob_bull"], aux["ob_bear"] = smc.find_order_blocks(
            candles, bars_per_day=bars_per_day)
        aux["fvg_bull"], aux["fvg_bear"] = smc.find_fvg(
            candles, bars_per_day=bars_per_day)
        aux["structure"] = smc.structure_bias(candles)
        print(f"  {sym}: SMC посчитан за {time.time()-t0:.2f}с")
        return aux
    return aux_builder


def build_base_src():
    """Стартовый геном = текущий финал v7 (config.SYMBOL_PARAMS[sym]['final']),
    + новые гены выключены (пусть эволюция сама решит, включать ли SMC)."""
    base = {}
    for sym, modes in config.SYMBOL_PARAMS.items():
        p = modes.get("final")
        if not p:
            continue
        g = e7.cfg_to_genome(p, "final")
        g.update(OFF8)
        base[sym] = g
    return base


def pick_leverage(ladder):
    best = ladder[0]
    for row in ladder:
        if row["dd"] <= DD_CAP * 100:
            best = row
    return best["lev"]


def ladder_for(sym, g, candles, aux, interval):
    pre = e2.prep(candles)
    filt = make_filter8(g, aux)
    old_bpd = e2.BARS_PER_DAY
    e2.BARS_PER_DAY = max(4, 1440 // int(interval))
    ladder = []
    try:
        for lev in LEVS:
            old_lev = e2.LEV
            e2.LEV = lev
            try:
                r = e2.run5(candles, pre, g, entry_filter=filt)
            finally:
                e2.LEV = old_lev
            ret = (r["balance"] / e2.START - 1) * 100
            st = e2.stats(r)
            ladder.append(dict(lev=lev, ret=round(ret, 1), med=round(st["med"], 2),
                               dd=round(r["max_dd"] * 100, 1), ruined=r["ruined"],
                               trades=r["trades"]))
    finally:
        e2.BARS_PER_DAY = old_bpd
    return ladder


def run_timeframe(interval, tag, base_src, pct5):
    bars_per_day = max(4, 1440 // int(interval))
    aux_builder = make_aux_builder(pct5, bars_per_day)
    results = e4.run_version(GENES8, OFF8, make_filter8, aux_builder, tag,
                             base_src, interval=interval, days=DAYS)
    final = {}
    for sym, rec in results.items():
        g = rec["genome"] if rec["adopt"] else rec["base_genome"]
        candles = ev.fetch(sym, interval, DAYS)
        aux = aux_builder(sym, candles)
        ladder = ladder_for(sym, g, candles, aux, interval)
        rec_lev = pick_leverage(ladder)
        final[sym] = dict(genome=g, ladder=ladder, rec_lev=rec_lev,
                          base_oos=rec["base_oos"], cand_oos=rec["cand_oos"],
                          adopt=rec["adopt"], interval=interval)
    with open(f"{tag}_final.json", "w", encoding="utf-8") as fh:
        json.dump(final, fh, ensure_ascii=False, indent=2, default=float)
    return final


def main():
    pct5 = xd.fetch_daily_pct5()
    base_src = build_base_src()

    print("\n########## 15m ##########")
    r15 = run_timeframe("15", "evolution8_15m", base_src, pct5)

    print("\n########## 4h (240) ##########")
    r4h = run_timeframe("240", "evolution8_4h", base_src, pct5)

    print("\n\n=== СРАВНЕНИЕ ТАЙМФРЕЙМОВ (по итогу 3.2г на рекоменд. плече) ===")
    winners = {}
    for sym in base_src:
        a, b = r15[sym], r4h[sym]
        a_ret = a["ladder"][[x["lev"] for x in a["ladder"]].index(a["rec_lev"])]["ret"]
        b_ret = b["ladder"][[x["lev"] for x in b["ladder"]].index(b["rec_lev"])]["ret"]
        win_tf, win_rec = ("15m", a) if a_ret >= b_ret else ("4h", b)
        g = a["genome"] if win_tf == "15m" else b["genome"]
        print(f"{sym}: 15m x{a['rec_lev']} {a_ret:+.1f}%  vs  "
              f"4h x{b['rec_lev']} {b_ret:+.1f}%  -> {win_tf}")
        print(f"   гены победителя: pattern={g['pattern_gate']} ob={g['ob_gate']} "
              f"fvg={g['fvg_gate']} structure={g['structure_mode']} ema={g['ema_mode']} "
              f"ma={g['ma_mode']} direction={g['direction']} regime_gate={g['regime_gate']}")
        winners[sym] = dict(timeframe=win_tf, ret=max(a_ret, b_ret),
                            rec=win_rec, genome=g)

    with open("evolution8_winners.json", "w", encoding="utf-8") as fh:
        json.dump(winners, fh, ensure_ascii=False, indent=2, default=float)
    print("\nИтоги в evolution8_winners.json")


if __name__ == "__main__":
    main()
