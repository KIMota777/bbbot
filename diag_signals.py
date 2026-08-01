# -*- coding: utf-8 -*-
"""Диагностика: почему у рабочих сетапов так мало сделок.

Для КАЖДОГО закрытого 4ч-бара проверяем ворота сетапа по очереди и считаем,
какое из них чаще всего убивает сигнал; отдельно собираем "почти прошло"
(провалено ровно одно условие, и близко к порогу). Плюс — сколько сигналов
терялось из-за занятости/кулдауна, и распределение по режимам рынка.
"""

import json
from collections import Counter, defaultdict

import evolution as ev
import signal_engine as se

with open("signal_setups.json", encoding="utf-8") as fh:
    setups = json.load(fh)

c4 = ev.fetch("BTCUSDT", "240", 1150)
c15 = ev.fetch("BTCUSDT", "15", 1150)
ts15 = [c[0] for c in c15]
ctx = se.prep_context(c4)
REG = {0: "bull", 1: "range", 2: "bear"}
print(f"4ч-баров: {len(c4)} | режимы: "
      f"bull {ctx['regime'].count(0)}, range {ctx['regime'].count(1)}, "
      f"bear {ctx['regime'].count(2)}")

for name in ("range_long", "pump_short"):
    rec = setups[name]
    g = rec["genome"]
    lev = rec.get("rec_lev", 15)
    side = "L" if name.endswith("long") else "S"
    sgn = 1 if side == "L" else -1
    rsi_arr = ctx["rsi"][se.RSI_SET[g["rsi_idx"]]]
    ext = se.rolling_extremes_lagged(
        c4, g["window"], g["age"] if name.startswith("sweep") else 1)

    fail = Counter()
    near = Counter()
    pass_by_regime = Counter()
    zone_vals, rsi_vals = [], []
    stop_dists = []
    full_pass = []          # бары, прошедшие ВСЕ ворота (без учёта занятости)

    start = max(g["window"] + g["age"] + 40, 60)
    for i in range(start, len(c4)):
        ts, o, h, l, c = c4[i]
        rsi = rsi_arr[i]
        atr = ctx["atr_d"][i]
        lo, hi = ext[0][i], ext[1][i]
        if rsi is None or atr is None or lo is None or hi is None or hi <= lo:
            fail["0_нет данных"] += 1
            continue
        buf = g["buf_atr"] * atr * c
        gates = []   # (имя, прошло?, "почти?" )

        if name == "range_long":
            reg = ctx["regime"][i]
            gates.append(("1_режим≠боковик", reg == 1, False))
            zpos = (c - lo) / (hi - lo)
            zone_vals.append(zpos)
            gates.append((f"2_зона {zpos:.2f}>{g['zone']:.2f}",
                          zpos < g["zone"], zpos < g["zone"] * 1.6))
            rsi_vals.append(rsi)
            gates.append((f"3_RSI {rsi:.0f}>{g['rsi_os']}",
                          rsi < g["rsi_os"], rsi < g["rsi_os"] + 6))
            stop_px = min(lo, l) - buf
        else:  # pump_short
            d = g["drop_days"] * 6
            if i < d:
                fail["0_нет данных"] += 1
                continue
            rise = (c - ctx["closes"][i - d]) / ctx["closes"][i - d]
            gates.append((f"1_рост {rise*100:.1f}%<{g['drop_frac']*100:.1f}%",
                          rise >= g["drop_frac"],
                          rise >= g["drop_frac"] * 0.7))
            gates.append(("2_свеча не красная", c < o, False))
            rsi_vals.append(rsi)
            gates.append((f"3_RSI {rsi:.0f}<{100-g['rsi_os']}",
                          rsi > 100 - g["rsi_os"], rsi > 100 - g["rsi_os"] - 6))
            stop_px = max(hi, h) + buf

        dist = sgn * (c - stop_px) / c
        stop_dists.append(dist)
        gates.append((f"4_стоп {dist*100:.1f}% вне [{se.MIN_STOP*100:.1f};"
                      f"{g['stop_cap']*100:.1f}]",
                      se.MIN_STOP <= dist <= g["stop_cap"],
                      se.MIN_STOP * 0.7 <= dist <= g["stop_cap"] * 1.5))

        failed = [nm for nm, ok, _ in gates if not ok]
        if not failed:
            full_pass.append(i)
            pass_by_regime[REG[ctx["regime"][i]]] += 1
            continue
        # какое ворото убило первым (по порядку)
        fail[failed[0].split("_")[0] + "_" + failed[0].split("_", 1)[1].split(" ")[0]] += 1
        if len(failed) == 1:
            only = failed[0]
            almost = next(a for nm, ok, a in gates if nm == only)
            if almost:
                near[only.split("_")[0] + "_" + only.split("_", 1)[1].split(" ")[0]] += 1

    r = se.run_setup(name, g, c4, ctx, c15, ts15, lev)
    print(f"\n{'='*70}\n{name}: сделок в бэктесте {len(r['trades'])}, "
          f"баров прошло все ворота {len(full_pass)}")
    print(f"  потеряно из-за занятости/кулдауна: "
          f"{len(full_pass) - len(r['trades'])}")
    print(f"  прошедшие по режимам: {dict(pass_by_regime)}")
    print("  --- что убивает сигнал (первое непройденное ворото) ---")
    for k, v in fail.most_common():
        print(f"    {k:26} {v:6}  ({v/len(c4)*100:5.1f}% баров)")
    print("  --- 'почти прошло' (провалено ровно одно, близко к порогу) ---")
    for k, v in near.most_common():
        print(f"    {k:26} {v:6}")
    if zone_vals:
        zs = sorted(zone_vals)
        print(f"  зона: медиана {zs[len(zs)//2]:.2f}, "
              f"10-й перцентиль {zs[len(zs)//10]:.2f}, порог {g['zone']:.2f}")
    if rsi_vals:
        rs = sorted(rsi_vals)
        print(f"  RSI: медиана {rs[len(rs)//2]:.0f}, "
              f"10-й перц. {rs[len(rs)//10]:.0f}, 90-й {rs[9*len(rs)//10]:.0f}, "
              f"порог {g['rsi_os'] if name.endswith('long') else 100-g['rsi_os']}")
    if stop_dists:
        ss = sorted(stop_dists)
        print(f"  ширина стопа: медиана {ss[len(ss)//2]*100:.1f}%, "
              f"порог cap {g['stop_cap']*100:.1f}%, "
              f"доля шире cap {sum(1 for x in stop_dists if x > g['stop_cap'])/len(stop_dists)*100:.0f}%")
    print(f"  геном: {json.dumps(g, ensure_ascii=False)}")
