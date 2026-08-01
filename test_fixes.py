# -*- coding: utf-8 -*-
"""Проверка исправлений аудита: фитнес больше не награждает редкость,
сентинел не выигрывает у торговли, ликвидация = реальный порог Bybit,
лимит стопа не протекает, гипотетика near-miss не берёт шумовые стопы."""

import evolution as ev
import signal_engine2 as se2

c4 = ev.fetch("BTCUSDT", "240", 1150)
c15 = ev.fetch("BTCUSDT", "15", 1150)
ts15 = [c[0] for c in c15]
ctx = se2.prep_context(c4)

print("=== 1. Порог ликвидации ===")
for lev in (10, 15, 20):
    print(f"  x{lev}: {se2.liq_frac(lev)*100:.3f}%  "
          f"(было MM/lev = {se2.MM/lev*100:.3f}%)")

print("\n=== 2. Фитнес: редкость больше не выигрывает ===")
# синтетика: одна и та же база, разная частота
class R:
    def __init__(self, n, exp_r, months, ruined=False):
        self.d = dict(
            trades=[dict(pnl=exp_r*1.0, r=exp_r, mae_r=1.0, mfe_r=1.0,
                         reason="tp" if exp_r > 0 else "stop", hold_h=10,
                         would_hit_tp_later=False, mfe_after_tp_r=0.0,
                         entry_ts=i*3600000, exit_ts=i*3600000+3600000)
                    for i in range(n)],
            monthly={i: exp_r for i in range(int(months))},
            balance=se2.START + exp_r*n, max_dd=0.1, ruined=ruined,
            months=months, near_misses=[])
    def __getitem__(self, k): return self.d[k]
    def get(self, k, d=None): return self.d.get(k, d)

for n, months in ((60, 37), (30, 37), (26, 37)):
    r = R(n, 0.2, months)
    print(f"  n={n:3} ({n/months:.2f}/мес), exp_r=+0.2 -> fitness "
          f"{se2.fitness(r):+.3f}")
print(f"  n=10 (ниже пола) -> fitness {se2.fitness(R(10, 0.5, 37)):+.1f} "
      f"(должно быть около {se2.FIT_FLOOR:.0f}, т.е. хуже любой торговли)")
print(f"  n=40, exp_r=-0.2 -> fitness {se2.fitness(R(40, -0.2, 37)):+.3f} "
      f"(убыточный хуже пола? {se2.fitness(R(40, -0.2, 37)) > se2.FIT_FLOOR})")

print("\n=== 3. Прогон 6 сетапов на дефолтном геноме (санити) ===")
tot_tr = tot_nm = 0
for s in se2.SETUPS:
    g = dict(se2.DEFAULTS2)
    r = se2.run_setup(s, g, c4, ctx, c15, ts15, 15)
    st = se2.stats(r)
    tot_tr += st["n"]; tot_nm += st["n_near"]
    liq = st["reasons"].get("liq", 0)
    print(f"  {s:12} сделок {st['n']:4} | WR {st['wr']:5.1f}% | exp_r "
          f"{st['exp_r']:+.3f} | tpm {st['tpm']:.2f} (актив. {st['tpm_active']:.2f})"
          f" | ликвидаций {liq} | near {st['n_near']:4} | fitness {se2.fitness(r):+.2f}")
print(f"  ИТОГО сделок {tot_tr}, near-miss {tot_nm}")

print("\n=== 4. Лимит стопа не протекает ===")
worst = 0.0
for s in se2.SETUPS:
    for lev in (15, 20):
        g = dict(se2.DEFAULTS2)
        r = se2.run_setup(s, g, c4, ctx, c15, ts15, lev)
        for t in r["trades"]:
            d = abs(t["entry"] - t["stop"]) / t["entry"]
            cap = min(g["stop_cap"], 0.8 * se2.liq_frac(lev))
            worst = max(worst, d / cap)
print(f"  макс (ширина стопа / лимит) по всем сделкам: {worst:.4f} "
      f"(должно быть <= 1.0)")

print("\n=== 5. Гипотетика near-miss: стопы не уже MIN_STOP ===")
bad = 0; tot = 0
for s in se2.SETUPS:
    r = se2.run_setup(s, dict(se2.DEFAULTS2), c4, ctx, c15, ts15, 20)
    for nm in r["near_misses"]:
        if nm.get("hypo_reason") in ("tp", "stop", "timeout", "liq"):
            tot += 1
            if nm["dist"] < se2.MIN_STOP:
                bad += 1
print(f"  гипотетик всего {tot}, из них с dist < MIN_STOP: {bad} (должно 0)")
