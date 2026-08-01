# -*- coding: utf-8 -*-
"""Тест движка v2.1 (signal_engine2) на РЕАЛЬНЫХ данных BTCUSDT.

Проверяется:
  1) оба ТФ (240=4ч и 60=1ч), все 4 сетапа SETUPS: сделки, WR, exp_r, DD,
     причины отказов, near-miss;
  2) механика: RR ровно 3, вход строго ПОСЛЕ закрытия сигнального бара
     своего ТФ, стороны/цены согласованы, кулдаун в барах своего ТФ;
  3) ворота funding / aroon / тренд MA реально режут (n с гейтом <= без,
     отказы с русскими именами появляются в reject_counts/gate_solo);
  4) funding на бар — ставка последнего НАЧАВШЕГОСЯ 8ч-периода (без
     лукахеда), Aroon не заглядывает вперёд;
  5) обратная совместимость: старые вызовы prep_context(c4)/run_setup(...)
     без interval_min, старые геномы БЕЗ новых генов, legacy-сетапы;
  6) фитнес v2.1: пол 20 сделок, норма 0.7 сд/мес, бонус за винрейт > 32%,
     штраф за просадку > 25% — формула сверяется пересчётом.

Запуск: python test_engine3.py
"""

import sys
import time

import evolution as ev
import signal_engine2 as se2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FAILS = []
N_OK = 0


def check(name, cond, detail=""):
    global N_OK
    if cond:
        N_OK += 1
    else:
        FAILS.append(f"{name}: {detail}")
        print(f"  !! ПРОВАЛ: {name} {detail}")


def d(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


print("=" * 78)
print("test_engine3: движок v2.1 — мульти-ТФ, новые сетапы, ворота из ботов")
print("=" * 78)

c4 = ev.fetch("BTCUSDT", "240", 1150)
c1 = ev.fetch("BTCUSDT", "60", 1150)
c15 = ev.fetch("BTCUSDT", "15", 1150)
ts15 = [c[0] for c in c15]
print(f"данные: 4ч {len(c4)} ({d(c4[0][0])}..{d(c4[-1][0])}), "
      f"1ч {len(c1)}, 15м {len(c15)}")

check("SETUPS = 4 сетапа",
      se2.SETUPS == ["sweep_long", "dump_long", "bounce_short", "rally_short"],
      f"got {se2.SETUPS}")
check("legacy убраны из SETUPS",
      all(s not in se2.SETUPS for s in se2.LEGACY_SETUPS))
check("новые гены в GENES2",
      all(k in se2.GENES2 for k in ("bounce_min_atr", "bounce_max_frac",
                                    "ma_gate", "ma_len", "fund_gate",
                                    "fund_thr", "aroon_gate", "aroon_n",
                                    "aroon_thr")))
check("новые гены в DEFAULTS2",
      all(k in se2.DEFAULTS2 for k in se2.GENES2))

ctx4 = se2.prep_context(c4)                       # старый вызов = 4ч
ctx1 = se2.prep_context(c1, interval_min=60)
check("ctx4.interval_min=240 (старый вызов)", ctx4["interval_min"] == 240)
check("ctx1.bars_day=24", ctx1["bars_day"] == 24)
f_cov4 = sum(1 for x in ctx4["fund"] if x is not None)
f_cov1 = sum(1 for x in ctx1["fund"] if x is not None)
print(f"funding покрытие: 4ч {f_cov4}/{len(c4)}, 1ч {f_cov1}/{len(c1)}")
check("funding покрывает >99% баров",
      f_cov4 > 0.99 * len(c4) and f_cov1 > 0.99 * len(c1))

# ---------------------------------------------------------------- 1+2: прогоны
print("\n--- Прогоны семени DEFAULTS2, оба ТФ, все сетапы (x15) " + "-" * 20)
results = {}
for iv, cc, ctx in ((240, c4, ctx4), (60, c1, ctx1)):
    bar_ms = se2.bar_ms_of(iv)
    print(f"\nТФ {iv} мин ({'4ч' if iv == 240 else '1ч'}):")
    print(f"{'сетап':13} {'сдел':>4} {'WR%':>6} {'exp R':>7} {'DD%':>6} "
          f"{'сд/мес':>6} {'near':>5}  топ-отказы")
    for s in se2.SETUPS:
        g = dict(se2.DEFAULTS2)
        r = se2.run_setup(s, g, cc, ctx, c15, ts15, 15, interval_min=iv)
        st = se2.stats(r)
        results[(iv, s)] = (r, st)
        rej = sorted(r["reject_counts"].items(), key=lambda x: -x[1])[:3]
        rej_s = ", ".join(f"{k} {v}" for k, v in rej)
        print(f"{s:13} {st['n']:4} {st['wr']:6.1f} {st['exp_r']:+7.3f} "
              f"{st['dd']:6.1f} {st['tpm']:6.2f} {st['n_near']:5}  {rej_s}")

        side = se2.setup_side(s)
        for t in r["trades"]:
            # RR жёстко 3: |tp-entry| == RR * |entry-stop| (цены в сделке
            # округлены до 2 знаков — допуск соответствующий)
            rr = abs(t["tp"] - t["entry"]) / max(1e-9, abs(t["entry"] - t["stop"]))
            if abs(rr - se2.RR) > 0.01:
                check(f"RR==3 {s}@{iv}", False, f"rr={rr:.4f} у {t['entry_ts']}")
                break
            # вход строго ПОСЛЕ закрытия сигнального бара своего ТФ
            if t["entry_ts"] < t["signal_ts"] + bar_ms:
                check(f"вход после закрытия {s}@{iv}", False,
                      f"entry {t['entry_ts']} < close {t['signal_ts'] + bar_ms}")
                break
            # стороны согласованы
            ok_side = (t["side"] == side and
                       ((side == "L" and t["stop"] < t["entry"] < t["tp"]) or
                        (side == "S" and t["tp"] < t["entry"] < t["stop"])))
            if not ok_side:
                check(f"стороны/цены {s}@{iv}", False, str(t["entry_ts"]))
                break
        else:
            check(f"механика сделок {s}@{iv} ({len(r['trades'])} сдел)", True)

        # кулдаун и занятость в единицах СВОЕГО ТФ
        cd_ms = int(g["cooldown"]) * bar_ms
        ok_cd = all(b["signal_ts"] + bar_ms >= a["exit_ts"] + cd_ms
                    for a, b in zip(r["trades"], r["trades"][1:]))
        check(f"кулдаун в барах ТФ {s}@{iv}", ok_cd)

# частота 1ч-версий разумна: сигналы есть, но не «каждый бар»
# (сигнальные бары кластеризуются — заняты позицией они схлопываются в
# одну сделку; частоту сделок дальше прижимает GA с TPM_NORM=0.7)
for s in se2.SETUPS:
    n1 = results[(60, s)][1]["n"]
    sig1 = results[(60, s)][0]["signals"]
    bars1 = results[(60, s)][0]["bars_eval"]
    check(f"1ч {s}: частота разумна", 5 <= n1 <= 400 and sig1 < 0.10 * bars1,
          f"n={n1}, signals={sig1}, bars={bars1}")

# ---------------------------------------------------------- 3: ворота из ботов
print("\n--- Ворота funding / aroon / тренд MA реально режут (4ч) " + "-" * 16)
gate_tests = [
    # (сетап, ген-переопределения, имя ворота в reject_counts)
    ("sweep_long", dict(fund_gate=1, fund_thr=0.005), se2.GATE_FUND),
    ("rally_short", dict(fund_gate=1, fund_thr=0.02), se2.GATE_FUND),
    ("sweep_long", dict(aroon_gate=1, aroon_n=25, aroon_thr=70), se2.GATE_AROON),
    ("bounce_short", dict(aroon_gate=1, aroon_n=25, aroon_thr=70), se2.GATE_AROON),
    ("rally_short", dict(ma_gate=1, ma_len=200), se2.GATE_MA),
]
for s, over, gname in gate_tests:
    g0 = dict(se2.DEFAULTS2)
    g1 = dict(se2.DEFAULTS2, **over)
    r0 = se2.run_setup(s, g0, c4, ctx4, c15, ts15, 15, collect_diag=False)
    r1 = se2.run_setup(s, g1, c4, ctx4, c15, ts15, 15)
    n_rej = r1["reject_counts"].get(gname, 0)
    n_solo = r1["gate_solo"].get(gname, 0)
    print(f"{s:13} +{gname:9}: сигналов {r0['signals']:4} -> {r1['signals']:4}, "
          f"сделок {len(r0['trades']):3} -> {len(r1['trades']):3}, "
          f"отказов по воротам {n_rej}, в gate_solo {n_solo}")
    check(f"ворото '{gname}' режет ({s})",
          r1["signals"] < r0["signals"] and n_solo > 0,
          f"signals {r0['signals']}->{r1['signals']}, solo={n_solo}")

# near-miss по новым воротам возможен (ищем хотя бы одно среди прогонов)
g_nm = dict(se2.DEFAULTS2, fund_gate=1, fund_thr=0.02)
r_nm = se2.run_setup("rally_short", g_nm, c4, ctx4, c15, ts15, 15)
nm_gates = {x["gate"] for x in r_nm["near_misses"]}
print("ворота в near-miss (rally_short + funding):", sorted(nm_gates))
check("новые ворота участвуют в near-miss", se2.GATE_FUND in nm_gates,
      f"got {nm_gates}")

# ------------------------------------------------- 4: честность funding/aroon
print("\n--- Отсутствие лукахеда " + "-" * 50)
series = se2._load_funding("BTCUSDT")
sf_ts = [t for t, _ in series]
sf_v = [v for _, v in series]
import bisect as _b
bad = 0
for i in range(0, len(c4), 7):
    got = ctx4["fund"][i]
    if got is None:
        continue
    close = c4[i][0] + se2.MS_4H
    j = _b.bisect_right(sf_ts, close)
    if j < len(sf_ts) and sf_ts[j] - close <= se2.MS_8H:
        want = sf_v[j]          # период начался (sf_ts[j]-8ч <= close) — ок
        started = sf_ts[j] - se2.MS_8H <= close
    else:
        want = sf_v[j - 1] if j else None
        started = True          # уже рассчитанная ставка — заведомо известна
    if got != want or not started:
        bad += 1
check("funding = ставка НАЧАВШЕГОСЯ периода (выборочно)", bad == 0,
      f"{bad} расхождений")

# Aroon: значение на баре i не меняется от добавления будущих баров
up_full, dn_full = se2.calc_aroon(c4, 25)
ok_ar = True
for i in (500, 2000, 5000):
    up_cut, dn_cut = se2.calc_aroon(c4[:i + 1], 25)
    if up_cut[i] != up_full[i] or dn_cut[i] != dn_full[i]:
        ok_ar = False
check("Aroon без заглядывания вперёд", ok_ar)

# SMA: то же самое
sma_full = se2.calc_sma([c[4] for c in c4], 200)
sma_cut = se2.calc_sma([c[4] for c in c4[:3001]], 200)
check("SMA без заглядывания вперёд", sma_full[3000] == sma_cut[3000])

# --------------------------------------------- 5: обратная совместимость API
print("\n--- Обратная совместимость " + "-" * 47)
# старый вызов run_setup без interval_min на 4ч-контексте
g = dict(se2.DEFAULTS2)
r_old = se2.run_setup("sweep_long", g, c4, ctx4, c15, ts15, 15,
                      collect_diag=False)
r_new = se2.run_setup("sweep_long", g, c4, ctx4, c15, ts15, 15,
                      collect_diag=False, interval_min=240)
check("старый вызов == interval_min=240",
      len(r_old["trades"]) == len(r_new["trades"]) and
      r_old["balance"] == r_new["balance"])

# старый геном БЕЗ новых генов (как в signal_setups2.json) работает и
# совпадает с новым геномом при выключенных воротах
g_old = {k: se2.DEFAULTS2[k] for k in list(se2.GENES2)[:24]}   # 24 старых гена
check("в старом геноме нет новых генов",
      "fund_gate" not in g_old and len(g_old) == 24)
r_g_old = se2.run_setup("dump_long", g_old, c4, ctx4, c15, ts15, 15,
                        collect_diag=False)
r_g_new = se2.run_setup("dump_long", dict(se2.DEFAULTS2), c4, ctx4, c15, ts15,
                        15, collect_diag=False)
check("старый геном == новый с выключенными воротами",
      [t["entry_ts"] for t in r_g_old["trades"]] ==
      [t["entry_ts"] for t in r_g_new["trades"]])

# legacy-сетап прогоняется (конфиги из json должны открываться)
r_leg = se2.run_setup("pump_short", g_old, c4, ctx4, c15, ts15, 15,
                      collect_diag=False)
check("legacy pump_short прогоняется", r_leg["setup"] == "pump_short",
      "")
print(f"legacy pump_short: {len(r_leg['trades'])} сделок (для сверки)")

# несовпадение ТФ вызова и контекста — громкая ошибка, а не тихий мусор
try:
    se2.run_setup("sweep_long", g, c1, ctx1, c15, ts15, 15, interval_min=240)
    check("несовпадение ТФ падает", False, "не упало")
except ValueError:
    check("несовпадение ТФ падает", True)

# build_ext по-старому (3 аргумента) работает
ext_old = se2.build_ext("sweep_long", g, c4)
check("build_ext(setup, g, c4) работает", ext_old["lows"] is not None)

# gate_eval на закрытом баре обоих ТФ отвечает полным разбором
ev4 = se2.gate_eval("bounce_short", len(c4) - 1, c4, ctx4, g,
                    se2.build_ext("bounce_short", g, c4, 240))
ev1 = se2.gate_eval("bounce_short", len(c1) - 1, c1, ctx1, g,
                    se2.build_ext("bounce_short", g, c1, 60))
check("gate_eval живой на обоих ТФ",
      ev4["gates"] and ev1["gates"] and "bounce" in ev4["diag"])

# --------------------------------------------------- 6: фитнес v2.1 (качество)
print("\n--- Фитнес на качество " + "-" * 51)
check("TPM_NORM == 0.7", se2.TPM_NORM == 0.7, f"got {se2.TPM_NORM}")
check("MIN_TRADES_FIT == 20", se2.MIN_TRADES_FIT == 20,
      f"got {se2.MIN_TRADES_FIT}")


def fake_r(n, wr_pct, months=20, dd=0.10, ruined=False):
    """Синтетический результат: n сделок, wr_pct% побед по +3R/-1R."""
    wins = int(round(n * wr_pct / 100.0))
    trades, ts = [], 0
    for k in range(n):
        win = k < wins
        rr = 3.0 if win else -1.05
        pnl = rr * 1.0
        trades.append(dict(pnl=pnl, r=rr, hold_h=12.0,
                           reason="tp" if win else "stop", mae_r=0.4,
                           mfe_r=3.0 if win else 0.5,
                           would_hit_tp_later=False, mfe_after_tp_r=0.0,
                           entry_ts=ts, exit_ts=ts + 3600000))
        ts += int(months * se2.MONTH_MS / max(1, n))
    monthly = {}
    for t in trades:
        m = int(t["exit_ts"] // se2.MONTH_MS)
        monthly[m] = monthly.get(m, 0.0) + t["pnl"]
    return dict(balance=se2.START + sum(t["pnl"] for t in trades),
                trades=trades, monthly=monthly, months=float(months),
                max_dd=dd, ruined=ruined, near_misses=[])


check("пол: 19 сделок -> FIT_FLOOR+19",
      se2.fitness(fake_r(19, 50)) == se2.FIT_FLOOR + 19)
check("20 сделок уже НЕ пол", se2.fitness(fake_r(20, 50)) > se2.FIT_FLOOR + 100)
f40 = se2.fitness(fake_r(40, 40))
f33 = se2.fitness(fake_r(40, 33))
check("выше винрейт (равное прочее) -> выше фитнес", f40 > f33,
      f"{f40:.2f} !> {f33:.2f}")
fdd10 = se2.fitness(fake_r(40, 40, dd=0.10))
fdd40 = se2.fitness(fake_r(40, 40, dd=0.40))
check("просадка 40% штрафуется против 10%",
      abs((fdd10 - fdd40) - 0.1 * 15.0) < 1e-6, f"{fdd10 - fdd40:.3f}")

# формула фитнеса — точная сверка пересчётом на РЕАЛЬНОМ прогоне
r_real, st_real = results[(240, "rally_short")]
expect = st_real["exp_r"] * 10.0
expect += 0.15 * (st_real["p25"] + 0.5 * st_real["med"])
expect -= 8.0 * max(0.0, 1.0 - st_real["tpm"] / se2.TPM_NORM)
expect += 0.08 * max(0.0, st_real["wr"] - 32.0)
expect -= 0.1 * max(0.0, st_real["dd"] - 25.0)
if st_real["pf"] is not None and st_real["pf"] < 1.0:
    expect -= 2.0 * (1.0 - st_real["pf"])
if r_real["ruined"]:
    expect -= 50.0
got_fit = se2.fitness(r_real)
check("формула fitness (реальный прогон)", abs(got_fit - expect) < 1e-9,
      f"{got_fit} != {expect}")
print(f"fitness(rally_short 4ч) = {got_fit:+.2f} (сверка с формулой сошлась)")

# oos_score живой и не ниже пола на реальном прогоне с n>=3
check("oos_score реального прогона выше пола",
      se2.oos_score(r_real) > se2.FIT_FLOOR + 200)

# ----------------------------------------------------------------- итог
print("\n" + "=" * 78)
if FAILS:
    print(f"ПРОВАЛЕНО {len(FAILS)} из {N_OK + len(FAILS)} проверок:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print(f"ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ: {N_OK} шт.")
