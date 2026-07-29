# -*- coding: utf-8 -*-
"""v7: единый отбор на 3.2 годах — направление, режимный гейт и паттерны
как гены одного генома (а не отдельные волны). Отвечает сразу на несколько
вопросов: лучше ли торговать в обе стороны или в одну (direction — ген,
эволюция выбирает сама), нужен ли медвежий гейт (regime_gate), помогают ли
классические паттерны (pattern_gate).

Новые гены поверх v6 (=v5 GENES5):
  direction    (0=обе стороны, 1=только лонг, 2=только шорт)
  regime_gate  (0=торговать всегда, 1=только bear/боковик — как в v6)
  pattern_gate (0=выкл, 1=требовать совпадающий паттерн, 2=требовать
                отсутствие противоположного — мягкий вариант)

Итог — ОДИН лучший конфиг на монету (не отдельно normal/bear). После отбора
конфига прогоняется лестница плечей x5..x15 на полных 3.2 годах.

Использует общий генетический харнесс evolution4.run_version (тот же, что
использовался в v4/v5/v6) — экономит код, walk-forward та же (3 экзамена).
"""

import json
import time

import evolution as ev
import evolution2 as e2
import evolution4 as e4
import evolution5 as e5
import evolution6 as e6
import ext_data as xd
import gridlib
import patterns as pt

LEVS = [5, 8, 10, 12, 15]
DD_CAP = 0.20  # рекомендуемое плечо = наибольшее с просадкой <= 20%

GENES7 = dict(e5.GENES5)
GENES7.update({
    "direction": (0, 2, True),
    "regime_gate": (0, 1, True),
    "pattern_gate": (0, 2, True),
})
OFF7 = dict(direction=0, regime_gate=0, pattern_gate=0)


def make_filter7(g, aux):
    base_f = e5.make_filter5(g, aux)
    regime = aux.get("regime")
    pat_bull, pat_bear = aux.get("pat_bull"), aux.get("pat_bear")

    def f(side, i):
        if g["regime_gate"] and regime is not None and regime[i] == 0:
            return None
        side = base_f(side, i)
        if not side:
            return None
        if g["direction"] == 1 and side != "L":
            return None
        if g["direction"] == 2 and side != "S":
            return None
        if g["pattern_gate"] and pat_bull is not None:
            if g["pattern_gate"] == 1:
                if side == "L" and not pat_bull[i]:
                    return None
                if side == "S" and not pat_bear[i]:
                    return None
            else:  # 2: мягкий — запрещаем только явное противоречие
                if side == "L" and pat_bear[i] and not pat_bull[i]:
                    return None
                if side == "S" and pat_bull[i] and not pat_bear[i]:
                    return None
        return side

    return f


def build_base_src():
    with open("evolution6_winners.json", encoding="utf-8") as fh:
        v6 = json.load(fh)
    base = {}
    for sym, rec in v6.items():
        g = dict(rec["genome"])
        g.setdefault("direction", 0)
        g["regime_gate"] = 1 if rec["adopt"] else 0
        g.setdefault("pattern_gate", 0)
        base[sym] = g
    return base


def nearest_idx(val, options):
    return min(range(len(options)), key=lambda i: abs(options[i] - val))


def cfg_to_genome(p, mode):
    """Параметры бота из config.SYMBOL_PARAMS -> геном движка (make_filter7).
    Недостающие ключи = фильтр выключен. Дефолты direction/regime_gate — те
    же правила, что в bot_rsi.py (используется и там, и на сайте, и в
    скриптах помесячной/годовой статистики — единый источник истины).

    Гены подвижной сетки (v10) переносятся сюда же: иначе сайт и отчёты
    рисовали бы бота со старой сеткой, пока живой торгует новой. Ключи
    новостной реакции (news_*) НЕ переносятся сознательно — движок не умеет
    воспроизводить историю новостей, поэтому при ненулевых коэффициентах
    бэктест перестаёт описывать бота. Это расхождение не молчаливое:
    news_divergence() ниже возвращает список таких монет, а bot_rsi.py
    предупреждает о нём в логе при старте.
    """
    direction = p.get("direction")
    if direction is None:
        direction = {"B": 0, "L": 1, "S": 2}.get(p.get("dir", "B"), 0)
    g = dict(
        rsi_os=p["rsi_os"],
        zone_l=p.get("zone_l", p.get("zone", 0.25)),
        zone_s=p.get("zone_s", p.get("zone", 0.25)),
        window=p.get("window", 400), step=p["step"],
        levels=p.get("levels", 3), mult=p.get("mult", 1.5),
        tp=p["tp"], sweep=p["sweep"], max_bars=p.get("max_bars", 192),
        cooldown=p.get("cooldown", 0), knife=p.get("knife", 0.0),
        be_move=p.get("be_move", 0),
        fund_long_max=p.get("fund_long_max", 0.06),
        fund_short_min=p.get("fund_short_min", -0.06),
        oi_gate=p.get("oi_gate", 0),
        spx_long_min=p.get("spx_long_min", -6.0),
        dxy_long_max=p.get("dxy_long_max", 4.0),
        gold_long_max=p.get("gold_long_max", 6.0),
        ema_mode=p.get("ema_mode", 0),
        ema_n_idx=nearest_idx(p.get("ema_n", 200), e5.EMA_SET),
        ma_mode=p.get("ma_mode", 0),
        masf_idx=nearest_idx(p.get("masf", 20), e5.MAF_SET),
        masl_idx=nearest_idx(p.get("masl", 200), e5.MAS_SET),
        aroon_idx=nearest_idx(p.get("aroon_n", 25), e5.ARN_SET),
        aroon_long_min=p.get("aroon_long_min", 0),
        aroon_short_min=p.get("aroon_short_min", 0),
        direction=direction,
        regime_gate=p.get("regime_gate", 1 if mode == "bear" else 0),
        pattern_gate=p.get("pattern_gate", 0),
        rsi_idx=nearest_idx(p.get("rsi_period", 14), e2.RSI_SET))
    for k, v in gridlib.OFF10.items():
        g[k] = p.get(k, v)
    return g


NEWS_KEYS = ("news_tp_k", "news_sl_k", "news_heat_max", "news_index_min")


def news_divergence(params=None):
    """Монеты, у которых новостная реакция включена, а бэктест её не видит.

    Пустой список = бэктест, сайт и отчёты описывают ровно тех ботов, что
    торгуют. Непустой = цифры на сайте относятся к другой стратегии, и это
    надо знать до того, как принимать по ним решения.
    """
    import config
    out = []
    for sym, modes in (params or config.SYMBOL_PARAMS).items():
        for mode, p in modes.items():
            if p and any(p.get(k, 0) for k in NEWS_KEYS):
                out.append(f"{sym}/{mode}")
    return out


def build_aux_full(sym, candles, pct5):
    """Полный aux (funding/OI/макро/EMA/MA/Aroon/режим/паттерны) для сайта и
    finalize-скриптов — тот же набор, что использует эволюция v7."""
    funding = xd.fetch_funding(sym, e4.DAYS + 50)
    oi = xd.fetch_oi(sym)
    aux = xd.build_aux4(candles, funding, oi, pct5)
    closes = [c[4] for c in candles]
    aux["closes"] = closes
    aux["ema"] = [e5.calc_ema(closes, x) for x in e5.EMA_SET]
    aux["smaf"] = [e5.calc_sma(closes, x) for x in e5.MAF_SET]
    aux["smas"] = [e5.calc_sma(closes, x) for x in e5.MAS_SET]
    aux["aroon"] = [e5.calc_aroon(candles, x) for x in e5.ARN_SET]
    aux["regime"] = e6.calc_regime(candles)
    aux["pat_bull"], aux["pat_bear"] = pt.compute_pattern_signals(candles)
    return aux


def pick_leverage(ladder):
    best = ladder[0]
    for row in ladder:
        if row["dd"] <= DD_CAP * 100:
            best = row
    return best["lev"]


def main():
    pct5 = xd.fetch_daily_pct5()
    print("SPX/DXY/gold: OK")
    base_src = build_base_src()

    def aux_builder(sym, candles):
        funding = xd.fetch_funding(sym, e4.DAYS + 50)
        oi = xd.fetch_oi(sym)
        aux = xd.build_aux4(candles, funding, oi, pct5)
        closes = [c[4] for c in candles]
        aux["closes"] = closes
        aux["ema"] = [e5.calc_ema(closes, x) for x in e5.EMA_SET]
        aux["smaf"] = [e5.calc_sma(closes, x) for x in e5.MAF_SET]
        aux["smas"] = [e5.calc_sma(closes, x) for x in e5.MAS_SET]
        aux["aroon"] = [e5.calc_aroon(candles, x) for x in e5.ARN_SET]
        aux["regime"] = e6.calc_regime(candles)
        t0 = time.time()
        bull, bear = pt.compute_pattern_signals(candles)
        aux["pat_bull"], aux["pat_bear"] = bull, bear
        print(f"  {sym}: паттерны посчитаны за {time.time()-t0:.2f}с")
        return aux

    results = e4.run_version(GENES7, OFF7, make_filter7, aux_builder,
                             "evolution7", base_src)

    print("\n=== Лестница плечей на финальных конфигах (3.2 года) ===")
    final = {}
    for sym, rec in results.items():
        g = rec["genome"] if rec["adopt"] else rec["base_genome"]
        candles = ev.fetch(sym, "15", e4.DAYS)
        aux = aux_builder(sym, candles)
        pre = e2.prep(candles)
        filt = make_filter7(g, aux)
        print(f"\n{sym}: direction={g['direction']} regime_gate={g['regime_gate']} "
              f"pattern_gate={g['pattern_gate']}")
        ladder = []
        for lev in LEVS:
            old = e2.LEV
            e2.LEV = lev
            try:
                r = e2.run5(candles, pre, g, entry_filter=filt)
            finally:
                e2.LEV = old
            ret = (r["balance"] / e2.START - 1) * 100
            st = e2.stats(r)
            print(f"  x{lev:<3} | {ret:+8.1f}% | мед.мес {st['med']:+5.2f}% | "
                  f"DD {r['max_dd']*100:5.1f}% | слив {'ДА' if r['ruined'] else 'нет'} "
                  f"| сделок {r['trades']}")
            ladder.append(dict(lev=lev, ret=round(ret, 1), med=round(st["med"], 2),
                               dd=round(r["max_dd"] * 100, 1), ruined=r["ruined"],
                               trades=r["trades"]))
        rec_lev = pick_leverage(ladder)
        print(f"  -> рекомендованное плечо: x{rec_lev}")
        final[sym] = dict(genome=g, ladder=ladder, rec_lev=rec_lev,
                          base_oos=rec["base_oos"], cand_oos=rec["cand_oos"],
                          adopt=rec["adopt"])

    with open("evolution7_final.json", "w", encoding="utf-8") as fh:
        json.dump(final, fh, ensure_ascii=False, indent=2, default=float)
    print("\nИтоги в evolution7_final.json")


if __name__ == "__main__":
    main()
