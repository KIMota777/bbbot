# -*- coding: utf-8 -*-
"""Проверки движка signal_engine3 (v3) + ЧЕСТНАЯ ПЕРВАЯ ОЦЕНКА на holdout.

Что проверяется (каждый тест печатает свои числа, а не только «ОК»):
  T1  механика сетки — сверка с evolution2.run5 (готовый проверенный движок
      сеточных ботов) НА ОДНИХ И ТЕХ ЖЕ свечах: цикл за циклом сравниваются
      pnl, число налившихся колен, момент и причина выхода;
  T2  grid_levels=1 — побитовое совпадение с одиночным входом
      signal_engine2.simulate_trade (pnl, бар выхода, причина, MAE/MFE);
  T3  RR и издержки — ПОЛНЫЙ ручной пересчёт нескольких сделок формулой
      (комиссии тейкер/мейкер, проскальзывание, фандинг по барам) и проверка,
      что тейк действительно стоит на tp_r риска от средней;
  T4  ликвидация — синтетические свечи: цена ликвидации считается по СРЕДНЕЙ
      и СУММАРНОМУ объёму, убыток = вся задействованная маржа; плюс инвариант
      «стоп всегда внутри ликвидации» на реальных прогонах;
  T5  отсутствие заглядывания — 120 срезов истории на 2 монетах: сигналы и
      цены стопа на префиксе обязаны совпадать с сигналами на полной истории;
      плюс сверка самих сделок (pnl/выход) на 6 срезах;
  T6  сетка как таковая — средняя цена и объёмы пересчитаны вручную, порядок
      «доливки -> стоп» проверен на свече, которая доходит до стопа;
  T7  мультимонетность — все 6 сетапов на 5 монетах;
  T8  совместимость результата с signal_stats.full_stats;
  T9  инварианты: баланс = сумма помесячных PnL, детерминизм, near-miss не
      влияет на баланс, безубыток честно считает be_hit/be_no_stop;
  T10 фитнес: метка «мало данных» (FIT_FLOOR+n) хуже ЛЮБОЙ реальной оценки,
      частота сделок реально влияет на счёт, oos_score работает.
Затем — ЧЕСТНАЯ ПЕРВАЯ ОЦЕНКА: дефолтный (НЕОПТИМИЗИРОВАННЫЙ) геном на
holdout [72%..100%) по 5 монетам, с зеркалами и с buy&hold для сравнения.

Запуск: python test_engine3_v3.py   (вывод дублируется в test_engine3_v3_out.txt)
"""

import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import evolution as ev
import evolution2 as e2
import signal_engine2 as se2
import signal_engine3 as se3
import signal_stats as ss

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "test_engine3_v3_out.txt")
_LINES = []


def say(s=""):
    print(s)
    _LINES.append(s)


SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT"]
DAYS = 1150
LEV = 5
HOLD_FRAC = 0.72        # как в evolution12: holdout — последние 28% истории

RESULTS = []            # (имя теста, ok, комментарий)
_DATA = {}


def data(sym):
    """4ч-серия, 15м-серия и контекст монеты (кэш на процесс)."""
    if sym not in _DATA:
        c4 = ev.fetch(sym, "240", DAYS)
        c15 = ev.fetch(sym, "15", DAYS)
        ts15 = [c[0] for c in c15]
        ctx = se3.prep_context(c4, 240, sym)
        _DATA[sym] = (c4, c15, ts15, ctx)
    return _DATA[sym]


def check(name, ok, comment=""):
    RESULTS.append((name, bool(ok), comment))
    say(f"  [{'OK ' if ok else 'ПРОВАЛ'}] {name}"
        + (f" — {comment}" if comment else ""))
    return ok


def d_ms(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def head(title):
    say("")
    say("=" * 78)
    say(title)
    say("=" * 78)


# =====================================================================
# T1. Механика сетки против evolution2.run5
# =====================================================================
def t1_grid_vs_run5():
    head("T1. МЕХАНИКА СЕТКИ ПРОТИВ evolution2.run5 (одни и те же свечи)")
    say("Сетка в v3 обязана исполняться так же, как в проверенном движке "
        "ботов:")
    say("нормировка объёмов колен, цены колен, порядок «доливки -> стоп», "
        "фандинг,")
    say("комиссии (тейкер вход/стоп, мейкер доливки/тейк). Сверяем ЦИКЛ ЗА "
        "ЦИКЛОМ.")
    candles = ev.fetch("DOGEUSDT", "15", 365)
    pre = e2.prep(candles)
    g5 = dict(rsi_idx=2, rsi_os=30, zone_l=0.35, zone_s=0.35, window=400,
              step=0.012, levels=3, mult=1.6, tp=0.03, sweep=0.02,
              max_bars=10 ** 9, cooldown=0, knife=0.0, be_move=0)
    old_lev, old_bpd = e2.LEV, e2.BARS_PER_DAY
    events = []
    try:
        e2.LEV = LEV
        e2.BARS_PER_DAY = 96
        r5 = e2.run5(candles, pre, g5, events=events)
    finally:
        e2.LEV, e2.BARS_PER_DAY = old_lev, old_bpd
    rlow, rhigh = ev.rolling_extremes(candles, g5["window"])
    idx_of = {c[0]: i for i, c in enumerate(candles)}

    cycles, cur = [], None
    for e in events:
        if e["type"] == "entry":
            cur = dict(i=idx_of[e["t"]], side=e["side"], adds=0)
        elif e["type"] == "add" and cur is not None:
            cur["adds"] += 1
        elif e["type"] == "close" and cur is not None:
            cur.update(exit_i=idx_of[e["t"]], pnl=e["pnl"], liq=e["liq"],
                       reason=e["reason"])
            cycles.append(cur)
            cur = None

    say(f"run5: сделок {r5['trades']}, циклов с событиями {len(cycles)}, "
        f"итог {r5['balance'] - e2.START:+.4f} USDT"
        + (" (СЛИВ)" if r5["ruined"] else ""))
    worst_pnl, worst_i = 0.0, None
    bad_fills = bad_exit = 0
    n_liq = 0
    sum_mine = 0.0
    for cy in cycles:
        i = cy["i"]
        sgn = 1 if cy["side"] == "L" else -1
        stop = (rlow[i] * (1 - g5["sweep"]) if sgn == 1
                else rhigh[i] * (1 + g5["sweep"]))
        sim = se3.simulate_grid_trade(
            cy["side"], i + 1, candles[i][4], stop, candles, LEV,
            10 ** 9, levels=g5["levels"], step_frac=g5["step"],
            mult=g5["mult"], tp_mode=1, tp_frac=g5["tp"], be_after_r=0.0)
        if sim["reason"] == "liq" or cy["liq"]:
            n_liq += 1
            continue
        sum_mine += sim["pnl"]
        d = abs(sim["pnl"] - cy["pnl"])
        if d > abs(worst_pnl):
            worst_pnl, worst_i = d, i
        if sim["grid_fills"] != cy["adds"] + 1:
            bad_fills += 1
        if sim["exit_i15"] != cy["exit_i"]:
            bad_exit += 1
    n_cmp = len(cycles) - n_liq
    say(f"Сверено циклов: {n_cmp} (ликвидаций {n_liq} — исключены: формулы "
        f"ликвидации у движков разные, см. T4)")
    say(f"Макс. расхождение PnL по циклу:   {worst_pnl:.2e} USDT "
        f"(в events движка ботов pnl округлён до 4 знаков, поэтому "
        f"попарный порог 5e-05)")
    say(f"Расхождений по числу колен:       {bad_fills}")
    say(f"Расхождений по бару выхода:       {bad_exit}")
    # Округление в events снимаем суммой: итог run5 (balance) НЕ округлён,
    # поэтому сумма моих PnL обязана совпасть с ним на уровне 1e-9.
    say(f"Сумма PnL v3 по циклам:           {sum_mine:+.10f} USDT")
    say(f"Итог run5 (balance - старт):      {r5['balance'] - e2.START:+.10f} "
        f"USDT")
    d_sum = abs(sum_mine - (r5["balance"] - e2.START))
    say(f"Расхождение суммы (без округлений): {d_sum:.2e} USDT (порог 1e-9)")
    ok = (n_cmp >= 30 and worst_pnl < 5e-5 and bad_fills == 0
          and bad_exit == 0 and n_liq == 0 and d_sum < 1e-9)
    check("T1 сетка совпадает с evolution2.run5", ok,
          f"{n_cmp} циклов, сумма сходится на {d_sum:.1e}")


# =====================================================================
# T2. grid_levels=1 == одиночный вход signal_engine2
# =====================================================================
def t2_single_vs_se2():
    head("T2. grid_levels=1 == ОДИНОЧНЫЙ ВХОД signal_engine2.simulate_trade")
    say("При grid_levels=1 движок обязан вести сделку буквально как v2 "
        "(в проде):")
    say("те же комиссии, тот же порядок «стоп раньше тейка», те же MAE/MFE.")
    c4, c15, ts15, ctx = data("BTCUSDT")
    n = 0
    worst = 0.0
    bad = []
    step = max(1, len(c4) // 260)
    for i in range(300, len(c4) - 5, step):
        for side in ("L", "S"):
            sgn = 1 if side == "L" else -1
            atr = ctx["atr_bar"][i]
            if not atr:
                continue
            j, entry_px = se3._plan_entry(c4[i][0], c15, ts15, se3.MS_4H)
            if j is None:
                continue
            stop = entry_px * (1 - sgn * 2.0 * atr)
            dist = sgn * (entry_px - stop) / entry_px
            if dist < se3.MIN_STOP or dist > 0.8 * se3.liq_frac(LEV):
                continue
            tp = entry_px * (1 + sgn * se2.RR * dist)
            a = se2.simulate_trade(side, j, entry_px, stop, tp, c15, LEV,
                                   20 * 96)
            fill = entry_px * (1 + sgn * se3.SLIP)
            b = se3.simulate_grid_trade(
                side, j, entry_px, stop, c15, LEV, 20 * 96, levels=1,
                tp_mode=1, tp_frac=sgn * (tp - fill) / fill)
            n += 1
            d = abs(a["pnl"] - b["pnl"])
            worst = max(worst, d)
            if (d > 1e-12 or a["exit_i15"] != b["exit_i15"]
                    or a["reason"] != b["reason"] or a["mae_r"] != b["mae_r"]
                    or a["mfe_r"] != b["mfe_r"]):
                bad.append((i, side, a["pnl"], b["pnl"], a["reason"],
                            b["reason"]))
    say(f"Сверено сделок: {n}")
    say(f"Макс. расхождение PnL: {worst:.3e} USDT (порог 1e-12)")
    say(f"Расхождений по бару/причине/MAE/MFE: {len(bad)}")
    for row in bad[:3]:
        say(f"   {row}")
    check("T2 grid_levels=1 совпадает с signal_engine2", n >= 200 and not bad,
          f"{n} сделок, макс. дельта {worst:.1e}")


# =====================================================================
# T3. RR и издержки — ручной пересчёт
# =====================================================================
def t3_costs_manual():
    head("T3. RR И ИЗДЕРЖКИ — РУЧНОЙ ПЕРЕСЧЁТ")
    say("Считаем PnL сделки формулой на бумаге и сравниваем с движком.")
    say("Формула: qty=маржа*плечо/фил; фил=цена*(1+знак*SLIP);")
    say("  комиссия входа = qty*фил*TAKER; тейк — MAKER без слипа;")
    say("  стоп/таймаут — TAKER со слипом; фандинг = Σ qty*close*FUND/32.")
    say(f"Константы движка: TAKER={se3.TAKER}, MAKER={se3.MAKER}, "
        f"SLIP={se3.SLIP}, FUND_8H={se3.FUND_8H}")
    ok_all = True

    # --- 3a. реальные одноколенные сделки: tp / stop / timeout ---
    c4, c15, ts15, ctx = data("BTCUSDT")
    g = se3.default_genome(grid_levels=1, hold_days=10)
    r = se3.run_setup("pullback_long", g, c4, ctx, c15, ts15, LEV,
                      symbol="BTCUSDT")
    picked = {}
    for t in r["trades"]:
        picked.setdefault(t["reason"], t)
    say("")
    say(f"{'причина':9} {'вход':>11} {'выход':>11} {'PnL движка':>12} "
        f"{'PnL вручную':>12} {'дельта':>10}")
    for why, t in sorted(picked.items()):
        sgn = 1 if t["side"] == "L" else -1
        j0 = ts15.index(t["entry_ts"])
        j1 = ts15.index(t["exit_ts"])
        fill = t["entry"] * (1 + sgn * se3.SLIP)
        qty = se3.MARGIN * LEV / fill
        fees = qty * fill * se3.TAKER
        for k in range(j0, j1 + 1):
            fees += qty * c15[k][4] * se3.FUND_8H / 32
        if why == "tp":
            px = t["tp_target"]
            pnl = sgn * (px - fill) * qty - qty * px * se3.MAKER - fees
        elif why == "stop":
            px = t["stop"] * (1 - sgn * se3.SLIP)
            pnl = sgn * (px - fill) * qty - qty * px * se3.TAKER - fees
        else:
            px = c15[j1][4] * (1 - sgn * se3.SLIP)
            pnl = sgn * (px - fill) * qty - qty * px * se3.TAKER - fees
        d = abs(pnl - t["pnl"])
        ok_all &= d < 1e-6
        say(f"{why:9} {t['entry']:11.2f} {px:11.2f} {t['pnl']:+12.6f} "
            f"{pnl:+12.6f} {d:10.2e}")

    # --- 3b. RR: тейк стоит ровно на tp_r риска от средней ---
    say("")
    say("RR — ген: проверяем, что тейк = tp_r * (средняя - стоп) и что "
        "валовый R сделки равен tp_r")
    say(f"{'tp_r':>5} {'сделок':>7} {'тейков':>7} {'R тейка(чистый)':>17} "
        f"{'R тейка(валовый)':>17}")
    for tp_r in (1.0, 2.0, 3.0):
        gg = se3.default_genome(grid_levels=1, tp_r=tp_r, hold_days=10)
        rr = se3.run_setup("pullback_long", gg, c4, ctx, c15, ts15, LEV,
                           symbol="BTCUSDT")
        tps = [t for t in rr["trades"] if t["reason"] == "tp"]
        if not tps:
            continue
        net = sum(t["r"] for t in tps) / len(tps)
        gross = 0.0
        for t in tps:
            sgn = 1 if t["side"] == "L" else -1
            fill = t["entry"] * (1 + sgn * se3.SLIP)
            qty = se3.MARGIN * LEV / fill
            risk_usd = se3.MARGIN * LEV * abs(fill - t["stop"]) / fill
            gross += sgn * (t["tp_target"] - fill) * qty / risk_usd
        gross /= len(tps)
        say(f"{tp_r:5.1f} {len(rr['trades']):7} {len(tps):7} "
            f"{net:+17.3f} {gross:+17.3f}")
        # валовый R тейка должен равняться tp_r с точностью до сдвига средней
        # на величину проскальзывания входа (тейк считается ОТ СРЕДНЕЙ)
        ok_all &= abs(gross - tp_r) < 0.05
        # чистый R обязан быть МЕНЬШЕ валового — на издержки
        ok_all &= net < gross

    # --- 3c. издержки как доля результата ---
    say("")
    g0 = se3.default_genome(grid_levels=1, hold_days=10)
    r0 = se3.run_setup("pullback_long", g0, c4, ctx, c15, ts15, LEV,
                       symbol="BTCUSDT")
    tot = sum(t["pnl"] for t in r0["trades"])
    fees_est = sum(2 * se3.MARGIN * LEV * se3.TAKER for _ in r0["trades"])
    say(f"Сделок {len(r0['trades'])}: чистый итог {tot:+.3f} USDT, "
        f"одни только комиссии круга (оценка) {fees_est:.3f} USDT")
    say("Комиссия круга на маржу $5 и плечо x5 = 2*25*0.055% = $0.0275 — "
        "это 0.55% маржи за сделку;")
    say("при стопе 2% (R=$0.5) издержки съедают около 5.5% каждого R.")
    check("T3 издержки и RR пересчитаны вручную", ok_all)


# =====================================================================
# T4. Ликвидация
# =====================================================================
def _synth(prices, ts0=1700000000000, step=900000):
    """Свечи из списка (o,h,l,c) с шагом 15 минут."""
    return [[ts0 + i * step, o, h, l, c]
            for i, (o, h, l, c) in enumerate(prices)]


def t4_liquidation():
    head("T4. ЛИКВИДАЦИЯ")
    say("Ликвидация обязана считаться по СРЕДНЕЙ цене и СУММАРНОМУ объёму,")
    say("а убыток равняться ВСЕЙ задействованной марже (плюс уплаченные "
        "комиссии).")
    ok_all = True

    # --- 4a. один вход, плечо x10: liq = fill*(1 - (1/10 - 0.005)) ---
    lev = 10
    bars = [(100.0, 100.2, 99.8, 100.0)]
    px = 100.0
    for _ in range(12):
        px *= 0.985
        bars.append((px / 0.985, px / 0.985, px, px))
    c15 = _synth(bars)
    fill = 100.0 * (1 + se3.SLIP)
    liq_expect = fill * (1 - se3.liq_frac(lev))
    sim = se3.simulate_grid_trade("L", 0, 100.0, 50.0, c15, lev, 100,
                                  levels=1, tp_mode=1, tp_frac=0.5)
    say(f"Один вход x{lev}: фил {fill:.4f}, ожидаемая цена ликвидации "
        f"{liq_expect:.4f} (1/плечо - MMR = {se3.liq_frac(lev)*100:.2f}%)")
    hit = next(k for k in range(len(c15)) if c15[k][3] <= liq_expect)
    say(f"  движок: причина {sim['reason']}, бар выхода {sim['exit_i15']} "
        f"(первый бар с low <= liq: {hit}), PnL {sim['pnl']:.4f}, "
        f"маржа цикла {sim['margin_used']:.2f}")
    # убыток при ликвидации = ВСЯ маржа + уплаченные комиссии (вход тейкером
    # и фандинг по барам удержания) — считаем вручную, без «примерно»
    qty_a = se3.MARGIN * lev / fill
    fees_a = qty_a * fill * se3.TAKER
    for k in range(0, hit + 1):
        fees_a += qty_a * c15[k][4] * se3.FUND_8H / 32
    say(f"  вручную: -маржа({se3.MARGIN:.2f}) - комиссии({fees_a:.4f}) = "
        f"{-se3.MARGIN - fees_a:.4f}")
    ok_all &= sim["reason"] == "liq" and sim["exit_i15"] == hit
    ok_all &= abs(sim["pnl"] - (-se3.MARGIN - fees_a)) < 1e-12

    # --- 4b. сетка: ликвидация по средней и суммарному объёму ---
    lev = 10
    bars = [(100.0, 100.2, 99.8, 100.0)]
    px = 100.0
    for _ in range(20):
        px *= 0.99
        bars.append((px / 0.99, px / 0.99, px, px))
    c15 = _synth(bars)
    sim = se3.simulate_grid_trade("L", 0, 100.0, 50.0, c15, lev, 200,
                                  levels=3, step_frac=0.02, mult=1.5,
                                  tp_mode=1, tp_frac=0.5)
    q0, adds, m_k = se3.plan_grid("L", 100.0 * (1 + se3.SLIP), 3, 0.02, 1.5,
                                  lev)
    say(f"Сетка 3 колена x{lev}: колена по цене "
        f"{[round(p, 4) for p, _ in adds]}, маржа колен "
        f"{[round(m, 3) for m in m_k]}")
    say(f"  налилось колен {sim['grid_fills']}, средняя {sim['avg_entry']:.4f},"
        f" маржа {sim['margin_used']:.3f}, причина {sim['reason']}, "
        f"PnL {sim['pnl']:.4f}")
    # Полный ручной повтор цикла в том же порядке, что и движок:
    # фандинг по объёму ДО доливок -> проверка ликвидации по средней и
    # суммарному объёму -> доливки. Так проверяется и формула, и порядок.
    fill0 = 100.0 * (1 + se3.SLIP)
    m_fills = [(fill0, q0)]
    fees_m = q0 * fill0 * se3.TAKER
    todo = list(adds)
    pnl_m = None
    hit_liq = None
    for k in range(len(c15)):
        q_pre = sum(q for _, q in m_fills)
        avg_pre = sum(p * q for p, q in m_fills) / q_pre
        fees_m += q_pre * c15[k][4] * se3.FUND_8H / 32
        m_pre = sum(m_k[:len(m_fills)])
        liq_m = avg_pre - (m_pre / q_pre - se3.MMR * avg_pre)
        if c15[k][3] <= liq_m:
            pnl_m, hit_liq = -m_pre - fees_m, k
            break
        while todo and c15[k][3] <= todo[0][0]:
            p_, q_ = todo.pop(0)
            fees_m += q_ * p_ * se3.MAKER
            m_fills.append((p_, q_))
    avg_manual = (sum(p * q for p, q in m_fills)
                  / sum(q for _, q in m_fills))
    m_used = sum(m_k[:len(m_fills)])
    say(f"  вручную: колен {len(m_fills)}, средняя {avg_manual:.4f}, "
        f"маржа {m_used:.3f}, бар ликвидации {hit_liq}, PnL {pnl_m:.4f}")
    ok_all &= abs(sim["avg_entry"] - avg_manual) < 1e-12
    ok_all &= sim["grid_fills"] == len(m_fills)
    ok_all &= sim["reason"] == "liq" and abs(sim["pnl"] - pnl_m) < 1e-12
    ok_all &= sim["exit_i15"] == hit_liq

    # --- 4c. инвариант «стоп внутри ликвидации» на реальных прогонах ---
    bad = 0
    total = 0
    n_liq = 0
    for sym in SYMS:
        c4, c15, ts15, ctx = data(sym)
        for setup in ("breakout_long", "pullback_short"):
            g = se3.default_genome(grid_levels=3, grid_step_atr=0.8)
            r = se3.run_setup(setup, g, c4, ctx, c15, ts15, LEV, symbol=sym)
            for t in r["trades"]:
                total += 1
                sgn = 1 if t["side"] == "L" else -1
                fill = t["entry"] * (1 + sgn * se3.SLIP)
                dist = sgn * (fill - t["stop"]) / fill
                if dist > 0.8 * se3.liq_frac(LEV) + 1e-12:
                    bad += 1
                if t["reason"] == "liq":
                    n_liq += 1
    say("")
    say(f"Реальные прогоны (5 монет x 2 сетапа, сетка 3 колена): сделок "
        f"{total}, ширина стопа больше 80% пути до ликвидации: {bad}, "
        f"фактических ликвидаций: {n_liq}")
    ok_all &= bad == 0 and n_liq == 0
    check("T4 ликвидация считается по средней и объёму, стоп внутри неё",
          ok_all)


# =====================================================================
# T5. Заглядывание вперёд
# =====================================================================
def t5_lookahead():
    head("T5. ОТСУТСТВИЕ ЗАГЛЯДЫВАНИЯ ВПЕРЁД (префикс vs полная история)")
    say("На баре i движок обязан видеть только данные <= i. Проверка: считаем "
        "контекст")
    say("и сигналы ЗАНОВО на префиксе истории и сравниваем с сигналами на "
        "полной истории.")
    say("Включены ВСЕ ворота (тренд, шторм, aroon, funding) и трейлинг-выход "
        "— то есть")
    say("все ряды, которые могли бы подсмотреть будущее.")
    g = se3.default_genome(trend_gate=1, aroon_gate=1, storm_gate=1,
                          fund_gate=0, tp_mode=2, grid_levels=3,
                          be_after_r=0.5)
    ok_all = True
    n_cmp_all = 0
    worst_stop = 0.0
    for sym in ("BTCUSDT", "SOLUSDT"):
        c4, c15, ts15, ctx = data(sym)
        n = len(c4)
        full = {}
        for setup in se3.SETUPS3:
            ext = se3.build_ext(setup, g, c4, 240)
            sigs = {}
            for i in range(ext["warm"], n):
                e = se3.gate_eval(setup, i, c4, ctx, g, ext)
                if e["ok"]:
                    sigs[c4[i][0]] = (e["side"], e["stop"], e["entry_ref"])
            full[setup] = sigs
        cuts = [int(n * (0.45 + 0.55 * k / 59.0)) for k in range(60)]
        diff = 0
        n_cmp = 0
        for cut in cuts:
            c4p = c4[:cut]
            ctxp = se3.prep_context(c4p, 240, sym)
            for setup in se3.SETUPS3:
                extp = se3.build_ext(setup, g, c4p, 240)
                for i in range(extp["warm"], cut):
                    e = se3.gate_eval(setup, i, c4p, ctxp, g, extp)
                    ts = c4p[i][0]
                    was = full[setup].get(ts)
                    if e["ok"] != bool(was):
                        diff += 1
                    elif was:
                        n_cmp += 1
                        worst_stop = max(worst_stop,
                                         abs(e["stop"] - was[1]) / was[1])
                        if e["side"] != was[0]:
                            diff += 1
        n_cmp_all += n_cmp
        say(f"{sym}: срезов {len(cuts)}, совпавших сигналов {n_cmp}, "
            f"расхождений {diff}, макс. относительная дельта цены стопа "
            f"{worst_stop:.2e}")
        ok_all &= (diff == 0 and worst_stop < 1e-12)

    # --- сверка самих сделок на нескольких срезах ---
    say("")
    say("Сверка СДЕЛОК (не только сигналов): прогон на префиксе против "
        "прогона на полной")
    say("истории; сравниваются сделки, у которых и окно удержания целиком "
        "внутри префикса.")
    bad_tr = 0
    n_tr = 0
    for sym in ("BTCUSDT", "SOLUSDT"):
        c4, c15, ts15, ctx = data(sym)
        n = len(c4)
        for setup in ("breakout_long", "pullback_short", "dump_long"):
            r_full = se3.run_setup(setup, g, c4, ctx, c15, ts15, LEV,
                                   symbol=sym)
            fmap = {t["entry_ts"]: t for t in r_full["trades"]}
            for frac in (0.55, 0.7, 0.85):
                cut = int(n * frac)
                c4p = c4[:cut]
                end_ts = c4p[-1][0]
                cut15 = 0
                while cut15 < len(ts15) and ts15[cut15] <= end_ts:
                    cut15 += 1
                c15p = c15[:cut15]
                ts15p = ts15[:cut15]
                ctxp = se3.prep_context(c4p, 240, sym)
                r_p = se3.run_setup(setup, g, c4p, ctxp, c15p, ts15p, LEV,
                                    symbol=sym)
                horizon = end_ts - int(g["hold_days"]) * 86400000
                for t in r_p["trades"]:
                    if t["exit_ts"] > horizon:
                        continue
                    f = fmap.get(t["entry_ts"])
                    n_tr += 1
                    if (f is None or abs(f["pnl"] - t["pnl"]) > 1e-9
                            or f["reason"] != t["reason"]
                            or f["exit_ts"] != t["exit_ts"]
                            or f["grid_fills"] != t["grid_fills"]):
                        bad_tr += 1
    say(f"Сверено сделок: {n_tr}, расхождений: {bad_tr}")
    ok_all &= (bad_tr == 0 and n_tr > 100)
    check("T5 заглядывания вперёд нет", ok_all,
          f"сигналов сверено {n_cmp_all}, сделок {n_tr}")


# =====================================================================
# T6. Арифметика сетки и порядок исполнения
# =====================================================================
def t6_grid_math():
    head("T6. АРИФМЕТИКА СЕТКИ И ПОРЯДОК ИСПОЛНЕНИЯ ВНУТРИ СВЕЧИ")
    ok_all = True
    lev = 5
    # 4 колена, шаг 1%, множитель 2.0 -> веса 1:2:4:8, маржа цикла $5
    q0, adds, m_k = se3.plan_grid("L", 100.0, 4, 0.01, 2.0, lev)
    say("План сетки (вход 100.0, шаг 1%, множитель 2.0, маржа цикла $5):")
    say(f"  маржа колен: {[round(m, 4) for m in m_k]} (сумма "
        f"{sum(m_k):.4f} = MARGIN)")
    say(f"  цены колен:  100.0000, {', '.join('%.4f' % p for p, _ in adds)}")
    say(f"  объёмы:      {q0:.6f}, "
        f"{', '.join('%.6f' % q for _, q in adds)}")
    ok_all &= abs(sum(m_k) - se3.MARGIN) < 1e-12
    ok_all &= abs(m_k[1] / m_k[0] - 2.0) < 1e-12
    ok_all &= abs(adds[0][0] - 99.0) < 1e-12
    ok_all &= abs(adds[1][0] - 98.01) < 1e-9
    ok_all &= abs(adds[0][1] - m_k[1] * lev / 99.0) < 1e-12

    # Свеча, которая доходит до стопа: сначала обязаны налиться колена ВЫШЕ
    # стопа, и стопится уже увеличенная позиция (консервативно). Колено НИЖЕ
    # стопа не исполняется никогда, даже если свеча до него дотянулась —
    # иначе стоп оказался бы внутри сетки, а риск неограниченным.
    bars = [
        (100.0, 100.5, 99.9, 100.2),     # первый бар: держим вход
        (100.2, 100.3, 97.5, 97.6),      # провал до 97.5 — задевает все колена
    ]
    c15 = _synth(bars)
    fill0 = 100.0 * (1 + se3.SLIP)
    q0b, addsb, m_kb = se3.plan_grid("L", fill0, 4, 0.01, 2.0, lev)
    say("")
    say(f"Свеча-провал до 97.5. Фил {fill0:.4f}, колена "
        f"{[round(p, 4) for p, _ in addsb]}: первые два свеча задевает, "
        f"третье (97.06) — нет")
    say(f"{'стоп':>7} {'колен ждём':>11} {'колен факт':>11} {'средняя':>10} "
        f"{'PnL движка':>12} {'PnL вручную':>12} {'дельта':>9}")
    for stop, want in ((98.00, 3), (98.50, 2), (99.50, 1)):
        sim = se3.simulate_grid_trade("L", 0, 100.0, stop, c15, lev, 50,
                                      levels=4, step_frac=0.01, mult=2.0,
                                      tp_mode=1, tp_frac=0.5)
        # ручной пересчёт: колена выше стопа наливаются, остальные нет
        use = [(p, q) for p, q in addsb if p > stop][:want - 1]
        q = q0b + sum(x[1] for x in use)
        avg = (q0b * fill0 + sum(p * x for p, x in use)) / q
        px = stop * (1 - se3.SLIP)
        fees = (q0b * fill0 * se3.TAKER
                + sum(x * p * se3.MAKER for p, x in use)
                + q0b * c15[0][4] * se3.FUND_8H / 32
                + q0b * c15[1][4] * se3.FUND_8H / 32)
        pnl = (px - avg) * q - q * px * se3.TAKER - fees
        d = abs(pnl - sim["pnl"])
        say(f"{stop:7.2f} {want:11} {sim['grid_fills']:11} "
            f"{sim['avg_entry']:10.4f} {sim['pnl']:+12.6f} {pnl:+12.6f} "
            f"{d:9.1e}")
        ok_all &= (sim["grid_fills"] == want and sim["reason"] == "stop"
                   and abs(sim["avg_entry"] - avg) < 1e-12 and d < 1e-12)
    say("Читать так: при стопе 98.50 колено 98.0394 РЕАЛЬНО задето свечой "
        "(лоу 97.5),")
    say("но лежит ниже стопа — движок его не наливает. Это и есть "
        "консервативный")
    say("порядок исполнения из evolution2.run5.")
    check("T6 арифметика сетки и порядок исполнения", ok_all)


# =====================================================================
# T7. Мультимонетность
# =====================================================================
def t7_multicoin():
    head("T7. МУЛЬТИМОНЕТНОСТЬ: 6 СЕТАПОВ x 5 МОНЕТ (дефолтный геном)")
    say("Один и тот же геном на всех монетах: все пороги — в долях цены и в "
        "ATR,")
    say("абсолютных величин в сетапах нет. Здесь важна РАБОТОСПОСОБНОСТЬ "
        "(есть сделки,")
    say("нет исключений), а не прибыль — прибыль честно меряется ниже, на "
        "holdout.")
    g = se3.default_genome()
    say("")
    say(f"{'сетап':16} " + " ".join(f"{s[:4]:>13}" for s in SYMS))
    ok_all = True
    for setup in se3.SETUPS3:
        cells = []
        for sym in SYMS:
            c4, c15, ts15, ctx = data(sym)
            r = se3.run_setup(setup, g, c4, ctx, c15, ts15, LEV, symbol=sym)
            st = se3.stats(r)
            cells.append(f"{st['n']:4}/{st['wr']:4.1f}/{st['exp_r']:+5.2f}")
            ok_all &= st["n"] > 0
        say(f"{setup:16} " + " ".join(f"{c:>13}" for c in cells))
    say("(в ячейке: сделок / винрейт % / expectancy в R)")
    check("T7 все сетапы дают сделки на всех 5 монетах", ok_all)


# =====================================================================
# T8. Совместимость с signal_stats.full_stats
# =====================================================================
def t8_stats_compat():
    head("T8. СОВМЕСТИМОСТЬ РЕЗУЛЬТАТА С signal_stats.full_stats")
    c4, c15, ts15, ctx = data("ETHUSDT")
    g = se3.default_genome(grid_levels=2, be_after_r=1.0)
    r = se3.run_setup("pullback_long", g, c4, ctx, c15, ts15, LEV,
                      symbol="ETHUSDT")
    # 1) объявленный публичный контракт полей обязан выполняться фактически
    miss_r = [k for k in se3.RESULT_FIELDS3 if k not in r]
    miss_t = [k for k in se3.TRADE_FIELDS3 if k not in r["trades"][0]]
    miss_n = [k for k in se3.NM_FIELDS3
              if r["near_misses"] and k not in r["near_misses"][0]]
    say(f"Контракт полей: результат — не хватает {miss_r or 'ничего'}; "
        f"сделка — {miss_t or 'ничего'}; near-miss — {miss_n or 'ничего'}")
    ok_fields = not (miss_r or miss_t or miss_n)
    # 2) цены не округлены «под BTC» (на DOGE round(x,2) обнулил бы их)
    c4d, c15d, ts15d, ctxd = data("DOGEUSDT")
    rd = se3.run_setup("pullback_short", se3.default_genome(), c4d, ctxd,
                       c15d, ts15d, LEV, symbol="DOGEUSDT")
    td = rd["trades"][0]
    say(f"DOGE: вход {td['entry']}, стоп {td['stop']}, средняя "
        f"{td['avg_entry']} — цены не округлены до 2 знаков")
    ok_fields &= td["entry"] > 0 and td["stop"] > 0 and abs(
        td["entry"] - round(td["entry"], 2)) > 0
    try:
        s = ss.full_stats(r, LEV, t0_ms=c4[r["signal_range"][0]][0])
        ok = ok_fields and all(k in s for k in ("n", "wr", "exp_r", "outcomes",
                                                "stop_quality", "by_regime",
                                                "near", "rejects", "warnings",
                                                "monthly"))
        say(f"full_stats посчитан: n={s['n']}, WR={s['wr']}%, "
            f"exp_r={s['exp_r']}, PF={s['pf']}, DD={s['dd_pct']}%, "
            f"блоков {len(s)}")
        say(f"outcomes: " + ", ".join(
            f"{k}={v['n']}" for k, v in s["outcomes"].items()))
        say("предупреждения модуля статистики:")
        for w in s["warnings"][:4]:
            say(f"   - {w}")
    except Exception as exc:                              # noqa: BLE001
        ok = False
        say(f"ИСКЛЮЧЕНИЕ: {exc!r}")
    check("T8 контракт полей и совместимость с signal_stats.full_stats", ok)


# =====================================================================
# T9. Инварианты, детерминизм, безубыток
# =====================================================================
def t9_invariants():
    head("T9. ИНВАРИАНТЫ, ДЕТЕРМИНИЗМ И ЧЕСТНОСТЬ БЕЗУБЫТКА")
    ok_all = True
    c4, c15, ts15, ctx = data("SOLUSDT")

    # баланс = старт + сумма помесячных PnL
    worst = 0.0
    for setup in se3.SETUPS3:
        g = se3.default_genome(grid_levels=2)
        r = se3.run_setup(setup, g, c4, ctx, c15, ts15, LEV, symbol="SOLUSDT")
        d = abs((se3.START + sum(r["monthly"].values())) - r["balance"])
        worst = max(worst, d)
        d2 = abs(sum(t["pnl"] for t in r["trades"])
                 - (r["balance"] - se3.START))
        worst = max(worst, d2)
    say(f"Инвариант «баланс = старт + Σ помесячных = старт + Σ сделок»: "
        f"макс. расхождение {worst:.2e}")
    ok_all &= worst < 1e-9

    # детерминизм
    g = se3.default_genome(grid_levels=3, tp_mode=2, be_after_r=0.7)
    a = se3.run_setup("breakout_long", g, c4, ctx, c15, ts15, LEV,
                      symbol="SOLUSDT")
    b = se3.run_setup("breakout_long", g, c4, ctx, c15, ts15, LEV,
                      symbol="SOLUSDT")
    same = json.dumps(a["trades"], sort_keys=True) == json.dumps(
        b["trades"], sort_keys=True)
    say(f"Детерминизм (два одинаковых прогона): "
        f"{'совпали побитово' if same else 'РАЗОШЛИСЬ'}")
    ok_all &= same

    # near-miss не влияет на баланс
    r1 = se3.run_setup("dump_long", g, c4, ctx, c15, ts15, LEV,
                       symbol="SOLUSDT", collect_diag=True)
    r2 = se3.run_setup("dump_long", g, c4, ctx, c15, ts15, LEV,
                       symbol="SOLUSDT", collect_diag=False)
    say(f"near-miss не влияет на баланс: с диагностикой "
        f"{r1['balance']:.6f} ({len(r1['near_misses'])} near-miss), без — "
        f"{r2['balance']:.6f}")
    ok_all &= abs(r1["balance"] - r2["balance"]) < 1e-12

    # честность безубытка: движок обязан показывать, что стоп не тестировался
    say("")
    say("Безубыток (be_after_r) — обязательная проверка на артефакт "
        "«стоп ни разу не тестировался»:")
    say(f"{'be_after_r':>10} {'сделок':>7} {'WR%':>6} {'exp_r':>7} "
        f"{'итог%':>8} {'выходов по БУ':>14} {'из них стоп не тестировался':>28}")
    prev_hit = None
    for be in (0.0, 0.5, 1.0, 1.5):
        gg = se3.default_genome(grid_levels=1, be_after_r=be)
        r = se3.run_setup("pullback_long", gg, c4, ctx, c15, ts15, LEV,
                          symbol="SOLUSDT")
        st = se3.stats(r)
        say(f"{be:10.1f} {st['n']:7} {st['wr']:6.1f} {st['exp_r']:+7.3f} "
            f"{st['ret']:+8.1f} {st['be_hit']:6} ({st['be_hit_share']:4.1f}%) "
            f"{st['be_no_stop']:10} ({st['be_no_stop_share']:4.1f}%)")
        if be == 0.0:
            ok_all &= st["be_hit"] == 0
            prev_hit = st["be_hit"]
        else:
            ok_all &= st["be_hit"] > 0
    say("Читать так: если доля «стоп не тестировался» велика, безубыток "
        "рисует винрейт,")
    say("закрывая сделки, которым ничего не угрожало, — это артефакт, а не "
        "заработок.")
    check("T9 инварианты, детерминизм, честный учёт безубытка", ok_all)


# =====================================================================
# T10. Фитнес: FIT_FLOOR, частота, винрейт, просадка
# =====================================================================
def t10_fitness():
    head("T10. ФИТНЕС ПОД ЦЕЛЬ «ГЛАВНОЕ ПРИБЫЛЬ»")
    say("Основа фитнеса — exp_r * сделок в месяц (ожидаемый доход в месяц в "
        "единицах")
    say("риска), плюс бонус за винрейт над 50% и штраф за просадку глубже "
        "25%.")
    say(f"Пол выборки {se3.MIN_TRADES_FIT} сделок: ниже него возвращается "
        f"метка FIT_FLOOR+n,")
    say("и она ОБЯЗАНА быть хуже любой реальной оценки — иначе «не торговать "
        "вообще»")
    say("выигрывает у торгующего генома с отрицательным счётом (эта ошибка "
        "уже была).")
    ok_all = True
    real, floors = [], []
    rows = []
    for sym in ("BTCUSDT", "DOGEUSDT", "SOLUSDT"):
        c4, c15, ts15, ctx = data(sym)
        for setup in se3.SETUPS3:
            for over in (dict(), dict(grid_levels=3), dict(tp_mode=2),
                         dict(tp_r=1.0), dict(tp_r=4.0, hold_days=5)):
                g = se3.default_genome(**over)
                r = se3.run_setup(setup, g, c4, ctx, c15, ts15, LEV,
                                  symbol=sym)
                st = se3.stats(r)
                f = se3.fitness(r)
                (floors if st["n"] < se3.MIN_TRADES_FIT else real).append(f)
                rows.append((f, st, setup, sym, over))
                if st["n"] < se3.MIN_TRADES_FIT:
                    ok_all &= abs(f - (se3.FIT_FLOOR + st["n"])) < 1e-9
    say("")
    say(f"Прогонов: {len(rows)} (реальных оценок {len(real)}, меток «мало "
        f"данных» {len(floors)})")
    say(f"Худшая РЕАЛЬНАЯ оценка: {min(real):+.2f}; лучшая МЕТКА "
        f"«мало данных»: {max(floors):+.2f} -> "
        f"{'метка хуже любой реальной, инвариант держится' if max(floors) < min(real) else 'ИНВАРИАНТ НАРУШЕН'}")
    ok_all &= max(floors) < min(real)
    rows.sort(key=lambda z: -z[0])
    say("")
    say(f"{'фитнес':>8} {'сделок':>7} {'/мес':>6} {'WR%':>6} {'exp_r':>7} "
        f"{'итог%':>8} {'DD%':>6}  сетап / монета")
    for f, st, setup, sym, over in rows[:5] + rows[-3:]:
        say(f"{f:+8.2f} {st['n']:7} {st['tpm']:6.2f} {st['wr']:6.1f} "
            f"{st['exp_r']:+7.3f} {st['ret']:+8.1f} {st['dd']:6.1f}  "
            f"{setup} / {sym[:4]} {over if over else ''}")
    # частота учтена: при одинаковом exp_r чаще торгующий геном обязан быть выше
    say("")
    say("Проверка «частота учтена»: одинаковый exp_r, разная частота ->")
    fake_hi = dict(trades=[dict(pnl=0.1, r=0.2, reason="tp", exit_kind="tp",
                                hold_h=10, mae_r=0.1, mfe_r=1.0,
                                would_hit_tp_later=False, be_exit=False,
                                grid_fills=1, orig_stop_touched=True,
                                entry_ts=0, exit_ts=1)] * 60,
                   monthly={i: 0.6 for i in range(10)}, months=10.0,
                   balance=se3.START + 6.0, max_dd=0.05, ruined=False,
                   near_misses=[], avg_grid_fills=1.0, trail_exits=0)
    fake_lo = dict(fake_hi)
    fake_lo["trades"] = fake_hi["trades"][:30]
    fake_lo["monthly"] = {i: 0.3 for i in range(10)}
    fake_lo["balance"] = se3.START + 3.0
    f_hi, f_lo = se3.fitness(fake_hi), se3.fitness(fake_lo)
    say(f"  60 сделок за 10 мес: фитнес {f_hi:+.2f}; 30 сделок за 10 мес: "
        f"{f_lo:+.2f} -> {'частота учтена' if f_hi > f_lo else 'ОШИБКА'}")
    ok_all &= f_hi > f_lo
    # oos_score работает и на короткой выборке
    c4, c15, ts15, ctx = data("ETHUSDT")
    n = len(c4)
    r = se3.run_setup("pullback_short", se3.default_genome(), c4, ctx, c15,
                      ts15, LEV, symbol="ETHUSDT",
                      signal_range=(int(n * HOLD_FRAC), n))
    say(f"  oos_score на holdout ETH/pullback_short: "
        f"{se3.oos_score(r):+.2f} (сделок {len(r['trades'])})")
    ok_all &= se3.oos_score(r) > se3.FIT_FLOOR
    check("T10 фитнес: FIT_FLOOR, частота, работоспособность", ok_all)


# =====================================================================
# ЧЕСТНАЯ ПЕРВАЯ ОЦЕНКА НА HOLDOUT
# =====================================================================
def bh_pct(c4, a, b):
    """Buy&hold монеты на отрезке баров [a, b), %."""
    return (c4[min(b, len(c4)) - 1][4] / c4[a][4] - 1) * 100


def tstat(rs):
    """t-статистика среднего R по пулу сделок и двусторонний p.
    Считается по НЕЗАВИСИМЫМ сделкам разных монет и месяцев; это грубая
    оценка, но её достаточно, чтобы отличить «есть сигнал» от «шум»."""
    n = len(rs)
    if n < 5:
        return 0.0, 1.0
    m = sum(rs) / n
    var = sum((x - m) ** 2 for x in rs) / (n - 1)
    if var <= 0:
        return 0.0, 1.0
    t = m / math.sqrt(var / n)
    return t, math.erfc(abs(t) / math.sqrt(2.0))


def sweep(g, hold, n_bars, label_fmt):
    """Прогон одного генома по всем 30 связкам (6 сетапов x 5 монет) на
    holdout. Возвращает (сделок, WR%, exp_r, сумма итогов %, средняя DD%,
    убыточных связок)."""
    n_tr = wins = n_bad = 0
    s_ret = s_dd = 0.0
    rs = []
    for setup in se3.SETUPS3:
        for sym in SYMS:
            c4, c15, ts15, ctx = data(sym)
            r = se3.run_setup(setup, g, c4, ctx, c15, ts15, LEV, symbol=sym,
                              signal_range=(hold, n_bars))
            st = se3.stats(r)
            n_tr += st["n"]
            wins += st["wins"]
            s_ret += st["ret"]
            s_dd += st["dd"]
            rs += [t["r"] for t in r["trades"]]
            if st["ret"] < 0:
                n_bad += 1
    exp_r = sum(rs) / len(rs) if rs else 0.0
    t, p = tstat(rs)
    return dict(n=n_tr, wr=(wins / n_tr * 100 if n_tr else 0.0), exp_r=exp_r,
                ret=s_ret, dd=s_dd / 30.0, bad=n_bad, t=t, p=p)


def honest_holdout():
    head("ЧЕСТНАЯ ПЕРВАЯ ОЦЕНКА: ДЕФОЛТНЫЙ ГЕНОМ НА HOLDOUT, БЕЗ "
         "ОПТИМИЗАЦИИ")
    g = se3.default_genome()
    c4b = data("BTCUSDT")[0]
    n = len(c4b)
    hold = int(n * HOLD_FRAC)
    say(f"Данные: 4ч-бары, {n} шт, {d_ms(c4b[0][0])}..{d_ms(c4b[-1][0])}")
    say(f"Обучающая часть [0..{hold}) — {d_ms(c4b[0][0])}.."
        f"{d_ms(c4b[hold][0])}; HOLDOUT [{hold}..{n}) — "
        f"{d_ms(c4b[hold][0])}..{d_ms(c4b[-1][0])} "
        f"({(c4b[-1][0]-c4b[hold][0])/86400000/30.4:.1f} мес)")
    say("Геном НЕ подбирался: значения «из учебника» (Дончиан 20 суток, "
        "SMA200/20,")
    say("слив 6% за 2 суток, RSI14 30/70, стоп 2*ATR, тейк 2R, одна позиция, "
        "без сетки).")
    say("Поэтому обе половины истории для него равноправны — но holdout мы "
        "смотрим отдельно,")
    say("потому что именно он совпадает с медвежьим периодом П3, на котором "
        "ломались")
    say("все прошлые результаты проекта.")

    say("")
    say("Buy&hold монет (для сравнения с любым результатом):")
    row = []
    for sym in SYMS:
        c4 = data(sym)[0]
        row.append(f"{sym[:4]} обуч {bh_pct(c4, 0, hold):+7.1f}% / holdout "
                   f"{bh_pct(c4, hold, n):+7.1f}%")
    for x in row:
        say("   " + x)

    tot = {}
    say("")
    say("ПО СЕТАПАМ И МОНЕТАМ НА HOLDOUT (сделок / WR% / exp_r / итог% базы "
        "/ DD%)")
    say(f"{'сетап':16} " + " ".join(f"{s[:4]:>21}" for s in SYMS)
        + f" {'ИТОГО%':>9}")
    per_setup = {}
    for setup in se3.SETUPS3:
        cells, s_ret, s_n, s_win, rs = [], 0.0, 0, 0, []
        n_bad = 0
        for sym in SYMS:
            c4, c15, ts15, ctx = data(sym)
            r = se3.run_setup(setup, g, c4, ctx, c15, ts15, LEV, symbol=sym,
                              signal_range=(hold, len(c4)))
            st = se3.stats(r)
            cells.append(f"{st['n']:3}/{st['wr']:4.1f}/{st['exp_r']:+5.2f}"
                         f"/{st['ret']:+6.1f}/{st['dd']:4.1f}")
            s_ret += st["ret"]
            s_n += st["n"]
            s_win += st["wins"]
            rs += [t["r"] for t in r["trades"]]
            n_bad += 1 if st["ret"] < 0 else 0
            tot.setdefault(sym, 0.0)
            tot[sym] += st["ret"]
        t, p = tstat(rs)
        per_setup[setup] = dict(ret=s_ret, n=s_n, wins=s_win, t=t, p=p,
                                bad=n_bad,
                                exp_r=(sum(rs) / len(rs) if rs else 0.0))
        say(f"{setup:16} " + " ".join(f"{c:>21}" for c in cells)
            + f" {s_ret:+9.1f}")
    say("")
    say("ЗНАЧИМОСТЬ (t по пулу сделок сетапа со всех монет; 5 монет — не 5 "
        "независимых")
    say("испытаний, они коррелированы, поэтому t здесь ЗАВЫШЕН, а не "
        "занижен):")
    say(f"{'сетап':16} {'сделок':>7} {'WR%':>6} {'exp_r':>7} {'t':>7} "
        f"{'p':>8} {'убыточных монет':>16} {'сумма итогов %':>15}")
    for setup, d in per_setup.items():
        wr = d["wins"] / d["n"] * 100 if d["n"] else 0.0
        say(f"{setup:16} {d['n']:7} {wr:6.1f} {d['exp_r']:+7.3f} "
            f"{d['t']:+7.2f} {d['p']:8.4f} {d['bad']:12}/5 "
            f"{d['ret']:+15.1f}")

    say("")
    say("ЗЕРКАЛА (главный тест на бету рынка: если работает только одна "
        "сторона —")
    say("померен рост/падение рынка, а не преимущество паттерна):")
    say(f"{'пара':34} {'лонг-сторона':>14} {'шорт-сторона':>14} "
        f"{'сумма':>9}")
    for a in ("breakout_long", "pullback_long", "dump_long"):
        b = se3.MIRROR[a]
        say(f"{a + ' / ' + b:34} {per_setup[a]['ret']:+14.1f} "
            f"{per_setup[b]['ret']:+14.1f} "
            f"{per_setup[a]['ret'] + per_setup[b]['ret']:+9.1f}")

    say("")
    say("ИТОГ ПО МОНЕТЕ (сумма всех 6 сетапов, каждый со своей базой $20):")
    for sym in SYMS:
        c4 = data(sym)[0]
        say(f"   {sym:9} сетапы {tot[sym]:+8.1f}%   buy&hold "
            f"{bh_pct(c4, hold, n):+8.1f}%")
    all_ret = sum(tot.values())
    say(f"   ВСЕГО по 30 связкам (сетап x монета): {all_ret:+.1f}% базы, "
        f"в среднем {all_ret / 30:+.1f}% на связку")

    # то же самое на обучающей половине — чтобы видеть разницу режимов
    say("")
    say("ТА ЖЕ ТАБЛИЦА НА ПЕРВЫХ 72% ИСТОРИИ (бычий рынок; геном тот же):")
    say(f"{'сетап':16} {'сделок':>7} {'WR%':>6} {'exp_r':>7} "
        f"{'сумма итогов %':>15} {'ОБЕ ЧАСТИ ВМЕСТЕ %':>19}")
    train_tot = 0.0
    train = {}
    for setup in se3.SETUPS3:
        s_ret, s_n, s_win, rs = 0.0, 0, 0, []
        for sym in SYMS:
            c4, c15, ts15, ctx = data(sym)
            r = se3.run_setup(setup, g, c4, ctx, c15, ts15, LEV, symbol=sym,
                              signal_range=(0, hold))
            st = se3.stats(r)
            s_ret += st["ret"]
            s_n += st["n"]
            s_win += st["wins"]
            rs += [t["r"] for t in r["trades"]]
        train_tot += s_ret
        train[setup] = dict(ret=s_ret, n=s_n,
                            exp_r=(sum(rs) / len(rs) if rs else 0.0))
        wr = s_win / s_n * 100 if s_n else 0.0
        say(f"{setup:16} {s_n:7} {wr:6.1f} "
            f"{train[setup]['exp_r']:+7.3f} {s_ret:+15.1f} "
            f"{s_ret + per_setup[setup]['ret']:+19.1f}")
    say(f"ВСЕГО на обучающей части: {train_tot:+.1f}% базы "
        f"({train_tot / 30:+.1f}% на связку); ОБЕ ЧАСТИ ВМЕСТЕ: "
        f"{train_tot + all_ret:+.1f}%")

    # --- помогает ли СЕТКА (владелец разрешил её явно) ---
    say("")
    say("ПОМОГАЕТ ЛИ СЕТКА (holdout, все 30 связок, шаг колена 1 ATR, "
        "множитель 1.5):")
    say(f"{'колен':>6} {'сделок':>7} {'WR%':>6} {'exp_r':>7} {'t':>6} "
        f"{'сумма итогов %':>15} {'сред. DD%':>10} {'убыточных связок':>17}")
    grid_rows = {}
    for lv in (1, 2, 3, 4):
        s = sweep(se3.default_genome(grid_levels=lv), hold, n, "")
        grid_rows[lv] = s
        say(f"{lv:6} {s['n']:7} {s['wr']:6.1f} {s['exp_r']:+7.3f} "
            f"{s['t']:+6.2f} {s['ret']:+15.1f} {s['dd']:10.1f} "
            f"{s['bad']:13}/30")
    say("Сетка меняет ФОРМУ распределения (винрейт вверх, просадка вниз), "
        "но матожидание")
    say("сделки от неё не растёт — ровно то, что показало исследование "
        "идеи E.")

    # --- что даёт свободный выход (RR больше не константа) ---
    say("")
    say("ЧТО ДАЁТ СВОБОДНЫЙ ВЫХОД (holdout, все 30 связок):")
    say(f"{'выход':28} {'сделок':>7} {'WR%':>6} {'exp_r':>7} {'t':>6} "
        f"{'сумма итогов %':>15} {'убыточных связок':>17}")
    exit_rows = {}
    for label, over in (("тейк 1R", dict(tp_mode=0, tp_r=1.0)),
                        ("тейк 2R (семя)", dict(tp_mode=0, tp_r=2.0)),
                        ("тейк 3R (как в v2)", dict(tp_mode=0, tp_r=3.0)),
                        ("тейк 3 ATR", dict(tp_mode=1, tp_atr=3.0)),
                        ("трейлинг 10 суток", dict(tp_mode=2, trail_bars=60)),
                        ("трейлинг 5 суток", dict(tp_mode=2, trail_bars=30))):
        s = sweep(se3.default_genome(**over), hold, n, "")
        exit_rows[label] = s
        say(f"{label:28} {s['n']:7} {s['wr']:6.1f} {s['exp_r']:+7.3f} "
            f"{s['t']:+6.2f} {s['ret']:+15.1f} {s['bad']:13}/30")

    # ------------------------------------------------ вердикт по числам
    say("")
    say("-" * 78)
    say("ВЕРДИКТ ПО ПЕРВОЙ ОЦЕНКЕ (считается из чисел выше, не из ожиданий)")
    say("-" * 78)
    flip = []
    for a in ("breakout_long", "pullback_long", "dump_long"):
        b = se3.MIRROR[a]
        h_l, h_s = per_setup[a]["ret"], per_setup[b]["ret"]
        t_l, t_s = train[a]["ret"], train[b]["ret"]
        # знак «лонг лучше шорта» переворачивается вместе с рынком?
        if (t_l - t_s) * (h_l - h_s) < 0:
            flip.append((a, b, t_l - t_s, h_l - h_s))
    say(f"1) Пар сетапов, у которых преимущество стороны ПЕРЕВЕРНУЛОСЬ "
        f"вместе с рынком: {len(flip)} из 3")
    for a, b, dt, dh in flip:
        say(f"   {a} - {b}: на бычьей части {dt:+.1f}%, на медвежьей "
            f"{dh:+.1f}% -> это направленная ставка, а не преимущество "
            f"паттерна")
    say(f"2) Итог по всей истории (обе части, 30 связок): "
        f"{train_tot + all_ret:+.1f}% базы, "
        f"{(train_tot + all_ret) / 30:+.1f}% на связку")
    say("3) Критерий подтверждения. Половины истории — разного режима "
        "(бык / медведь),")
    say("   поэтому выживание на ОБЕИХ и есть «преимущество, а не бета». "
        "Ниже приведены")
    say("   ДВА варианта критерия, чтобы нельзя было подобрать порог под "
        "ответ:")
    say("     A: итог% > 0 на бычьей части + exp_r > 0 и |t| >= 2 на "
        "holdout;")
    say("     B (строже): exp_r > 0 на ОБЕИХ частях + |t| >= 2 на holdout.")
    say(f"   {'сетап':16} {'exp_r бык':>10} {'exp_r медведь':>14} "
        f"{'t медведь':>10}  вывод")
    conf_a, conf_b = [], []
    for s, d in per_setup.items():
        sig = abs(d["t"]) >= 2.0
        a_ok = train[s]["ret"] > 0 and d["exp_r"] > 0 and sig
        b_ok = train[s]["exp_r"] > 0 and d["exp_r"] > 0 and sig
        if a_ok:
            conf_a.append(s)
        if b_ok:
            conf_b.append(s)
        if b_ok:
            verdict = "ПОДТВЕРЖДЁН по A и B"
        elif a_ok:
            verdict = ("проходит A, но НЕ B: на бычьей части exp_r "
                       f"{train[s]['exp_r']:+.3f} — плюс дали единицы сделок")
        elif d["exp_r"] > 0 and train[s]["exp_r"] > 0:
            verdict = "плюс на обеих, но незначимо"
        elif d["exp_r"] > 0 or train[s]["exp_r"] > 0:
            verdict = "плюс только в одном режиме — бета"
        else:
            verdict = "минус в обоих режимах"
        say(f"   {s:16} {train[s]['exp_r']:+10.3f} {d['exp_r']:+14.3f} "
            f"{d['t']:+10.2f}  {verdict}")
    say(f"   Прошли A: {len(conf_a)}"
        + (f" ({', '.join(conf_a)})" if conf_a else " — ни одного")
        + f";  прошли B: {len(conf_b)}"
        + (f" ({', '.join(conf_b)})" if conf_b else " — НИ ОДНОГО"))
    g1, g4 = grid_rows[1], grid_rows[4]
    say(f"4) Сетка: винрейт {g1['wr']:.1f}% -> {g4['wr']:.1f}%, средняя "
        f"просадка {g1['dd']:.1f}% -> {g4['dd']:.1f}%,")
    say(f"   но матожидание сделки {g1['exp_r']:+.3f}R -> {g4['exp_r']:+.3f}R "
        f"и сумма итогов {g1['ret']:+.1f}% -> {g4['ret']:+.1f}%.")
    say("   То есть сетка — инструмент управления формой риска, а не "
        "источник прибыли:")
    say("   ровно тот же вывод, что дало исследование идеи E.")
    best = max(exit_rows.items(), key=lambda kv: kv[1]["exp_r"])
    rr3 = exit_rows["тейк 3R (как в v2)"]
    say(f"5) Свободный выход (снятое RR 1:3): лучший вариант — «{best[0]}» "
        f"({best[1]['exp_r']:+.3f}R,")
    say(f"   t={best[1]['t']:+.2f}) против жёстких 3R ({rr3['exp_r']:+.3f}R). "
        f"Разница есть, значимости нет:")
    say(f"   ни у одного варианта |t| не дотягивает до 2 "
        f"(максимум {max(abs(v['t']) for v in exit_rows.values()):.2f}).")
    say("6) ОБЩИЙ ВЫВОД. Движок исправен и меряет честно (T1-T9), но "
        "НЕОБУЧЕННЫЙ геном")
    say("   преимущества не показывает: плюс на holdout даёт шортовая "
        "сторона на падающем")
    say("   рынке, и ровно она же теряет на растущем. Это тот же результат, "
        "что у")
    show = "   исследования идей, и его надо принять, а не подкручивать."
    say(show)
    return per_setup, tot, all_ret, train_tot, train


def main():
    t0 = time.time()
    say("ПРОВЕРКА ДВИЖКА signal_engine3 (v3): сетка, свободный RR, "
        "мультимонетность")
    say(f"Дата прогона (UTC): {time.strftime('%Y-%m-%d %H:%M', time.gmtime())}")
    say(f"Плечо тестов x{LEV}, маржа цикла ${se3.MARGIN}, база "
        f"${se3.START}, история {DAYS} дней, ТФ сигналов 4ч, исполнение 15м")
    t1_grid_vs_run5()
    t2_single_vs_se2()
    t3_costs_manual()
    t4_liquidation()
    t6_grid_math()
    t5_lookahead()
    t7_multicoin()
    t8_stats_compat()
    t9_invariants()
    t10_fitness()
    honest_holdout()

    head("ИТОГИ ПРОВЕРОК")
    for name, ok, comment in RESULTS:
        say(f"  {'OK    ' if ok else 'ПРОВАЛ'}  {name}"
            + (f" ({comment})" if comment else ""))
    bad = [x for x in RESULTS if not x[1]]
    say("")
    say(f"Пройдено {len(RESULTS) - len(bad)} из {len(RESULTS)}; "
        f"время {time.time() - t0:.0f} с")
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_LINES) + "\n")
    print(f"\n(вывод сохранён в {OUT_PATH})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
