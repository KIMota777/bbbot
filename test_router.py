# -*- coding: utf-8 -*-
"""Проверка router_1h: эквивалентность движку, причинность, формат, политика.

Запуск: python test_router.py   (вывод — в test_router_out.txt при желании)

БЛОК 1. ЭКВИВАЛЕНТНОСТЬ. Роутер, которому дали ОДИН сетап и политику
        «ничего не запрещать» (контр-тренд разрешён с порогом 0, шторм
        игнорируется, кулдаун = личный ген сетапа), обязан дать ПОБИТОВО те
        же сделки, near-miss и счётчики, что se2.run_setup. Это проверяет
        весь цикл: занятость, кулдаун, построение сделки, диагностику.
БЛОК 2. ПРИЧИННОСТЬ (правило проекта: «префикс vs полная история»). Считаем
        контекст и предрасчёт ворот по ПРЕФИКСУ c1h[:k] и гоняем роутер на
        [0..k); сравниваем с прогоном на полной истории с тем же
        signal_range. Совпадение = решение бара i не зависит от баров > i.
БЛОК 3. ФОРМАТ. signal_stats.full_stats переваривает результат роутера без
        переделок; поля сделок на месте; trades_by_setup/switches сходятся.
БЛОК 4. ПОЛИТИКА. storm_policy=0 не даёт ни одной сделки в шторм;
        allow_counter=0 не даёт сделок вне таблицы PRIMARY; priority_mode
        0/1/2 выбирает ровно то, что обещает (перепроверяем по кандидатам).
"""

import sys
import time

import evolution as ev
import signal_engine2 as se2
import signal_stats as ss
import router_1h as rt

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [ок]    {name}" + (f" | {extra}" if extra else ""))
    else:
        FAIL += 1
        print(f"  [ПРОВАЛ] {name}" + (f" | {extra}" if extra else ""))


def same_rows(a, b, keys=None):
    """Списки словарей совпадают по указанным ключам (или по всем общим)."""
    if len(a) != len(b):
        return False, f"длины {len(a)} != {len(b)}"
    for k, (x, y) in enumerate(zip(a, b)):
        ks = keys or (set(x) & set(y))
        for f in ks:
            if x.get(f) != y.get(f):
                return False, f"строка {k}, поле {f}: {x.get(f)} != {y.get(f)}"
    return True, ""


def main():
    t_all = time.time()
    lev = 15
    print("=" * 78)
    print("ПРОВЕРКА router_1h")
    print("=" * 78)
    c1h = ev.fetch("BTCUSDT", "60", 1150)
    c15 = ev.fetch("BTCUSDT", "15", 1150)
    ts15 = [c[0] for c in c15]
    ctx = se2.prep_context(c1h, interval_min=60)
    n = len(c1h)
    genomes = rt.load_genomes()
    print(f"1ч-баров {n}, 15м {len(c15)}, плечо x{lev}")

    # ---------------------------------------------------------------- БЛОК 1
    print("\nБЛОК 1. Роутер с одним сетапом == se2.run_setup")
    for s in rt.SETUPS4:
        g = genomes[s]
        pol = dict(allow_counter=1, counter_min=0.0, storm_policy=2,
                   storm_scale=1.0, priority_mode=0,
                   router_cooldown=int(g["cooldown"]))
        pre1 = rt.prep_signals({s: g}, c1h, ctx, interval_min=60)
        r_rt = rt.run_router({s: g}, c1h, ctx, c15, ts15, lev, policy=pol,
                             collect_diag=True, pre=pre1)
        r_se = se2.run_setup(s, g, c1h, ctx, c15, ts15, lev,
                             collect_diag=True, interval_min=60)
        ok_tr, why_tr = same_rows(r_rt["trades"], r_se["trades"],
                                  keys=se2.TRADE_FIELDS)
        ok_nm, why_nm = same_rows(r_rt["near_misses"], r_se["near_misses"],
                                  keys=se2.NM_FIELDS)
        check(f"{s}: сделки побитово", ok_tr,
              why_tr or f"{len(r_se['trades'])} шт., баланс "
                        f"{r_rt['balance']:.4f} / {r_se['balance']:.4f}")
        check(f"{s}: near-miss побитово", ok_nm,
              why_nm or f"{len(r_se['near_misses'])} шт.")
        check(f"{s}: баланс/просадка/сигналы",
              abs(r_rt["balance"] - r_se["balance"]) < 1e-12
              and abs(r_rt["max_dd"] - r_se["max_dd"]) < 1e-12
              and r_rt["signals"] == r_se["signals"]
              and r_rt["blocked_busy"] == r_se["blocked_busy"]
              and r_rt["blocked_cooldown"] == r_se["blocked_cooldown"],
              f"сигналов {r_rt['signals']}/{r_se['signals']}, занят "
              f"{r_rt['blocked_busy']}/{r_se['blocked_busy']}, кулдаун "
              f"{r_rt['blocked_cooldown']}/{r_se['blocked_cooldown']}")
        check(f"{s}: reject_counts побитово",
              r_rt["reject_counts"] == r_se["reject_counts"],
              f"{sorted(r_rt['reject_counts'].items())[:3]}...")
        check(f"{s}: gate_solo побитово",
              r_rt["gate_solo"] == r_se["gate_solo"])
        check(f"{s}: bars_eval и monthly",
              r_rt["bars_eval"] == r_se["bars_eval"]
              and r_rt["monthly"] == r_se["monthly"],
              f"баров {r_rt['bars_eval']}/{r_se['bars_eval']}")

    # ---------------------------------------------------------------- БЛОК 2
    print("\nБЛОК 2. Причинность: префикс истории vs полная история")
    pre_full = rt.prep_signals(genomes, c1h, ctx, interval_min=60)
    for k in (12000, 18000, 24000, 27000):
        c_pref = c1h[:k]
        ctx_pref = se2.prep_context(c_pref, interval_min=60)
        pre_pref = rt.prep_signals(genomes, c_pref, ctx_pref, interval_min=60)
        for pol_name, pol in (("дефолт", None), ("наивная", rt.NAIVE_POLICY)):
            r_p = rt.run_router(genomes, c_pref, ctx_pref, c15, ts15, lev,
                                signal_range=(0, k), policy=pol,
                                collect_diag=False, pre=pre_pref)
            r_f = rt.run_router(genomes, c1h, ctx, c15, ts15, lev,
                                signal_range=(0, k), policy=pol,
                                collect_diag=False, pre=pre_full)
            ok_tr, why = same_rows(r_p["trades"], r_f["trades"],
                                   keys=tuple(se2.TRADE_FIELDS)
                                   + ("setup", "policy_reason", "strength"))
            check(f"срез {k}, политика {pol_name}: сделки совпали", ok_tr,
                  why or f"{len(r_f['trades'])} сделок, сигналов "
                         f"{r_f['signals']}")

    # ---------------------------------------------------------------- БЛОК 3
    print("\nБЛОК 3. Формат результата (signal_stats.full_stats без переделок)")
    hold = int(n * 0.72)
    seg = (hold, n)
    r = rt.run_router(genomes, c1h, ctx, c15, ts15, lev, signal_range=seg,
                      collect_diag=True, pre=pre_full)
    st = ss.full_stats(r, lev, c1h[r["signal_range"][0]][0])
    m = rt.metrics(r["trades"])
    check("full_stats отработал", isinstance(st, dict) and len(st) > 40,
          f"ключей {len(st)}")
    check("full_stats: n/wr/exp_r/pf совпали с metrics",
          st["n"] == m["n"] and abs(st["exp_r"] - m["exp_r"]) < 1e-9
          and st["pf"] == m["pf"],
          f"n={st['n']} wr={st['wr']} exp={st['exp_r']} pf={st['pf']}")
    miss = [f for f in se2.TRADE_FIELDS if r["trades"]
            and f not in r["trades"][0]]
    check("у сделки все поля TRADE_FIELDS", not miss, f"нет: {miss}")
    check("у сделки есть setup и policy_reason",
          all("setup" in t and "policy_reason" in t for t in r["trades"]))
    check("trades_by_setup сходится с числом сделок",
          sum(r["trades_by_setup"].values()) == len(r["trades"]))
    sw = sum(1 for a, b in zip(r["trades"], r["trades"][1:])
             if a["setup"] != b["setup"])
    check("switches посчитан верно", sw == r["switches"],
          f"{r['switches']} переключений")
    check("баланс = старт + сумма P&L",
          abs(r["balance"] - (se2.START + sum(t["pnl"] for t in r["trades"])))
          < 1e-9, f"{r['balance']:.4f}")
    check("near-miss в формате движка",
          all(all(f in x for f in ("ts", "gate", "hypo_r", "setup"))
              for x in r["near_misses"]), f"{len(r['near_misses'])} шт.")

    # ---------------------------------------------------------------- БЛОК 4
    print("\nБЛОК 4. Политика делает ровно то, что обещает")
    # 4.1 шторм
    p0 = dict(rt.DEFAULT_POLICY, storm_policy=0, allow_counter=1,
              counter_min=0.0, router_cooldown=0)
    r0 = rt.run_router(genomes, c1h, ctx, c15, ts15, lev, policy=p0,
                       collect_diag=False, pre=pre_full)
    check("storm_policy=0: ни одной сделки в шторм",
          not any(t["storm"] for t in r0["trades"]),
          f"{len(r0['trades'])} сделок, блок штормом {r0['blocked_storm']}")
    p1 = dict(p0, storm_policy=1)
    r1 = rt.run_router(genomes, c1h, ctx, c15, ts15, lev, policy=p1,
                       collect_diag=False, pre=pre_full)
    bad1 = [t for t in r1["trades"] if t["storm"] and t["storm_dir"]
            and ((t["storm_dir"] < 0 and t["side"] == "L")
                 or (t["storm_dir"] > 0 and t["side"] == "S"))]
    check("storm_policy=1: в шторм только по направлению шторма", not bad1,
          f"{len(r1['trades'])} сделок, против шторма {len(bad1)}")
    # 4.2 таблица режимов
    p2 = dict(rt.DEFAULT_POLICY, allow_counter=0, storm_policy=2,
              router_cooldown=0)
    r2 = rt.run_router(genomes, c1h, ctx, c15, ts15, lev, policy=p2,
                       collect_diag=False, pre=pre_full)
    bad2 = [t for t in r2["trades"] if t["setup"] not in rt.PRIMARY[t["regime"]]]
    check("allow_counter=0: сделки только из таблицы PRIMARY", not bad2,
          f"{len(r2['trades'])} сделок, нарушений {len(bad2)}")
    p3 = dict(p2, allow_counter=1, counter_min=0.0)
    r3 = rt.run_router(genomes, c1h, ctx, c15, ts15, lev, policy=p3,
                       collect_diag=False, pre=pre_full)
    n_ctr = sum(1 for t in r3["trades"]
                if t["setup"] not in rt.PRIMARY[t["regime"]])
    check("allow_counter=1: контр-трендовые сделки появились", n_ctr > 0,
          f"{n_ctr} контр-трендовых из {len(r3['trades'])}")
    # 4.3 приоритет
    for mode in (0, 1, 2):
        pm = dict(rt.DEFAULT_POLICY, priority_mode=mode, allow_counter=1,
                  counter_min=0.0, router_cooldown=0)
        rm = rt.run_router(genomes, c1h, ctx, c15, ts15, lev, policy=pm,
                           collect_diag=False, pre=pre_full)
        bad, multi = 0, 0
        for t in rm["trades"]:
            i = next((j for j in range(len(c1h))
                      if c1h[j][0] == t["signal_ts"]), None)
            if i is None:
                continue
            cands = [(s, pre_full["sig"][s][i],
                      pre_full["strength"].get((s, i), 0.0))
                     for s in rt.SETUPS4 if i in pre_full["sig"][s]]
            if len(cands) < 2:
                continue
            multi += 1
            if mode == 1:
                best = min(cands, key=lambda x: (x[1]["dist"],
                                                 rt.QUALITY_ORDER.index(x[0])))
            elif mode == 2:
                best = max(cands, key=lambda x: (x[2],
                                                 -rt.QUALITY_ORDER.index(x[0])))
            else:
                best = min(cands, key=lambda x: rt.QUALITY_ORDER.index(x[0]))
            if best[0] != t["setup"]:
                bad += 1
        check(f"priority_mode={mode} ({rt.PRIORITY_NAMES[mode]}): выбор верен",
              bad == 0, f"сделок {len(rm['trades'])}, из них с несколькими "
                        f"кандидатами {multi}, ошибок {bad}")

    print("\n" + "=" * 78)
    print(f"ИТОГ: пройдено {OK}, провалено {FAIL} | {time.time()-t_all:.0f}с")
    print("=" * 78)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
