# -*- coding: utf-8 -*-
"""Smoke-тест движка signal_engine2 на реальных данных BTC.

Проверяем на всей истории (1150 дней, 4ч-сигналы + 15м-исполнение):
  - для каждого из 6 сетапов с дефолтным геномом: сделки, WR, итог, DD,
    near-miss, топ-5 причин отказа, среднее MAE/MFE;
  - что ни одно ворото не отсекает >95% баров в одиночку;
  - что сделок стало заметно больше, чем у v1 (было 15 и 44);
  - механику: нет заглядывания вперёд, стоп+тейк в одной свече = стоп.
"""

import json

import evolution as ev
import signal_engine as se1
import signal_engine2 as se2

LEV = 15
DAYS = 1150

c4 = ev.fetch("BTCUSDT", "240", DAYS)
c15 = ev.fetch("BTCUSDT", "15", DAYS)
ts15 = [c[0] for c in c15]
ctx = se2.prep_context(c4)
reg = ctx["regime"]
print(f"4ч-баров: {len(c4)} | 15м-баров: {len(c15)}")
print(f"режимы: bull {reg.count(0)} ({reg.count(0)/len(reg)*100:.0f}%), "
      f"range {reg.count(1)} ({reg.count(1)/len(reg)*100:.0f}%), "
      f"bear {reg.count(2)} ({reg.count(2)/len(reg)*100:.0f}%)")
print(f"EMA50 готова с бара {next(i for i,x in enumerate(ctx['ema50']) if x)}, "
      f"EMA200 — с бара {next(i for i,x in enumerate(ctx['ema200']) if x)}")

g = dict(se2.DEFAULTS2)
print(f"\nДефолтный геном: {json.dumps(g, ensure_ascii=False)}")

# --- режимные пороги: демонстрация требования №2 -----------------------------
print("\n--- режимная логика (пороги RSI/зоны по режимам) ---")
print(f"{'сетап':13} {'bull':>16} {'range':>16} {'bear':>16}")
for s in se2.SETUPS:
    cells = []
    for rg in (0, 1, 2):
        rt, zt = se2.effective_thresholds(s, rg, g)
        shown = rt if s.endswith("long") else 100 - rt
        cells.append(f"RSI {shown:4.0f} зона {zt:.2f}")
    print(f"{s:13} " + " ".join(f"{x:>16}" for x in cells))

# --- основной прогон --------------------------------------------------------
res = {}
for mode in (0, 1):
    gm = dict(g, entry_mode=mode)
    print(f"\n{'='*78}\nВХОД: {'по рынку (open 15м после закрытия 4ч)' if mode==0 else 'лимитка на ретесте 0.35 ATR, ожидание 16 баров'}")
    print(f"{'сетап':13} {'сдел':>5} {'WR%':>6} {'итог%':>8} {'DD%':>6} "
          f"{'R/сд':>6} {'near':>5} {'MAE_R':>6} {'MFE_R':>6} {'сигн':>5} "
          f"{'busy':>5} {'cool':>5}")
    for s in se2.SETUPS:
        r = se2.run_setup(s, gm, c4, ctx, c15, ts15, LEV)
        st = se2.stats(r)
        res[(s, mode)] = (r, st)
        print(f"{s:13} {st['n']:5} {st['wr']:6.1f} {st['ret']:+8.1f} "
              f"{st['dd']:6.1f} {st['exp_r']:+6.2f} {st['n_near']:5} "
              f"{st['avg_mae_r']:6.2f} {st['avg_mfe_r']:6.2f} "
              f"{r['signals']:5} {r['blocked_busy']:5} "
              f"{r['blocked_cooldown']:5}")

# --- детальная диагностика по каждому сетапу (вход по рынку) -----------------
print(f"\n{'='*78}\nДЕТАЛИ (вход по рынку)")
worst_gate = 0.0
for s in se2.SETUPS:
    r, st = res[(s, 0)]
    n_bars = r["bars_eval"]
    print(f"\n--- {s} | баров оценено {n_bars} | сделок {st['n']} "
          f"({st['tpm']:.2f}/мес) | WR {st['wr']}% | итог {st['ret']:+.1f}% | "
          f"DD {st['dd']}% | PF {st['pf']}")
    print(f"    причины выхода: {st['reasons']} | из выбитых стопом дошло бы "
          f"до тейка позже: {st['stop_then_tp']}/{st['n_stop']}")
    print(f"    MAE ср {st['avg_mae_r']:.2f}R, MFE ср {st['avg_mfe_r']:.2f}R, "
          f"после тейка прошло бы ещё {st['avg_mfe_after_tp_r']:.2f}R")
    print(f"    сигналов всего {r['signals']} по режимам "
          f"{ {se2.REGIME_NAMES[k]: v for k, v in r['pass_by_regime'].items()} }"
          f", съедено занятостью {r['blocked_busy']}, "
          f"кулдауном {r['blocked_cooldown']}, "
          f"после слива {r['blocked_ruined']} (слив: "
          f"{'ДА' if r['ruined'] else 'нет'})")
    top = sorted(r["reject_counts"].items(), key=lambda x: -x[1])[:5]
    print("    топ-5 причин отказа (первое непройденное ворото):")
    for k, v in top:
        print(f"      {k:38} {v:6}  ({v/n_bars*100:5.1f}% баров)")
    print("    отсев каждым воротом в одиночку (лимит 95%):")
    for k, v in sorted(r["gate_solo"].items(), key=lambda x: -x[1]):
        share = v / n_bars * 100
        worst_gate = max(worst_gate, share)
        flag = "  <-- ПЕРЕБОР" if share > 95 else ""
        print(f"      {k:38} {v:6}  ({share:5.1f}%){flag}")
    nm = r["near_misses"]
    if nm:
        good = sum(1 for x in nm if x["hypo_r"] > 0)
        print(f"    near-miss {len(nm)}: гипотетически плюсовых {good} "
              f"({good/len(nm)*100:.0f}%), средний R "
              f"{sum(x['hypo_r'] for x in nm)/len(nm):+.2f}, "
              f"ворота {sorted({x['gate'] for x in nm})}")
        x = max(nm, key=lambda z: z["hypo_r"])
        print(f"    лучший near-miss: {x['gate']} нужно {x['need']} "
              f"было {x['got']} (недобор {x['margin']}) -> "
              f"гипо R {x['hypo_r']:+.2f} ({x['hypo_reason']})")

# --- сравнение с v1 ---------------------------------------------------------
print(f"\n{'='*78}\nСРАВНЕНИЕ С v1 (те же данные, геномы-победители из signal_setups.json)")
with open("signal_setups.json", encoding="utf-8") as fh:
    old = json.load(fh)
ctx1 = se1.prep_context(c4)
print(f"{'сетап':13} {'v1 сделок':>10} {'v2 сделок':>10} {'рост':>7} "
      f"{'v2 сигналов':>12}")
worst_ratio = 99.0
for s in se2.SETUPS:
    r1 = se1.run_setup(s, old[s]["genome"], c4, ctx1, c15, ts15, LEV)
    n1 = len(r1["trades"])
    n2 = res[(s, 0)][1]["n"]
    ratio = n2 / n1 if n1 else float("inf")
    worst_ratio = min(worst_ratio, ratio)
    print(f"{s:13} {n1:10} {n2:10} {ratio:6.1f}x {res[(s,0)][0]['signals']:12}")
print(f"худший прирост по сетапам: x{worst_ratio:.1f} "
      f"({'ОК, сделок заметно больше' if worst_ratio >= 2 else 'МАЛО'})")
print("ВАЖНО: один общий дефолтный геном на все 6 сетапов — компромисс; "
      "у range_long/range_short WR ниже\nбезубытка (~27% для 1:3), поэтому на "
      "базе START=20/MARGIN=5/x15 они сливают счёт и сделки\nобрываются на "
      "сливе. Задача движка — НАХОДИТЬ ситуации; отбор прибыльных порогов — "
      "работа GA.")

# --- проверки механики ------------------------------------------------------
print(f"\n{'='*78}\nПРОВЕРКИ МЕХАНИКИ")
bad_future = bad_order = bad_rr = 0
n_all = 0
for (s, mode), (r, st) in res.items():
    for t in r["trades"]:
        n_all += 1
        if t["entry_ts"] < t["signal_ts"] + se2.MS_4H:
            bad_future += 1
        if t["exit_ts"] < t["entry_ts"]:
            bad_order += 1
        rr = abs(t["tp"] - t["entry"]) / abs(t["entry"] - t["stop"])
        if abs(rr - se2.RR) > 0.02:
            bad_rr += 1
print(f"сделок всего {n_all} | вход раньше закрытия сигнального бара: "
      f"{bad_future} | выход раньше входа: {bad_order} | RR != 3.0: {bad_rr}")
print(f"максимальный одиночный отсев воротом: {worst_gate:.1f}% "
      f"({'ОК' if worst_gate <= 95 else 'ПЕРЕБОР'})")

# стоп и тейк в одной свече -> стоп (искусственная свеча)
fake = [[0, 100.0, 100.0, 100.0, 100.0],
        [0, 100.0, 110.0, 90.0, 100.0],
        [0, 100.0, 100.0, 100.0, 100.0]]
sim = se2.simulate_trade("L", 1, 100.0, 98.0, 106.0, fake, 10, 96)
print(f"стоп+тейк в одной 15м-свече -> reason={sim['reason']} "
      f"(ожидается stop), pnl={sim['pnl']:.3f}")
sim2 = se2.simulate_trade("S", 1, 100.0, 102.0, 94.0, fake, 10, 96)
print(f"то же для шорта -> reason={sim2['reason']} (ожидается stop)")

# ликвидация ближе стопа (плечо 50, стоп 5%)
fake2 = [[0, 100.0, 100.0, 100.0, 100.0],
         [0, 100.0, 100.0, 96.0, 97.0],
         [0, 97.0, 97.0, 97.0, 97.0]]
sim3 = se2.simulate_trade("L", 1, 100.0, 95.0, 115.0, fake2, 50, 96)
print(f"ликвидация ближе стопа (x50, стоп 5%) -> reason={sim3['reason']} "
      f"(ожидается liq), pnl={sim3['pnl']:.3f}")

# ширина стопа по режимам stop_mode
print("\nмедианная ширина стопа по stop_mode (range_long, все бары с данными):")
for sm in (0, 1, 2, 3):
    gg = dict(g, stop_mode=sm, stop_cap=0.035)
    ext = se2.build_ext("range_long", gg, c4)
    d = []
    for i in range(gg["window"] + 60, len(c4)):
        e_ = se2.gate_eval("range_long", i, c4, ctx, gg, ext)
        if e_["dist"]:
            d.append(e_["dist"])
    d.sort()
    okc = sum(1 for x in d if se2.MIN_STOP <= x <= 0.028) / len(d) * 100
    print(f"  mode {sm}: медиана {d[len(d)//2]*100:5.2f}%, "
          f"10-й перц {d[len(d)//10]*100:5.2f}%, 90-й {d[9*len(d)//10]*100:5.2f}%, "
          f"пролезает в cap 2.8%: {okc:4.1f}% баров")
