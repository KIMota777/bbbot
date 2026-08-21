# -*- coding: utf-8 -*-
"""ЛИНЗА «31 СДЕЛКА»: что такое +28.5% на фоне ШУМА ОТБОРА.

Мысль простая. Бутстрэп показал: разброс итога по 31 сделке огромен, ошибка
среднего по сделке почти равна самому среднему. Значит любой перебор конфигов
будет вытаскивать наверх не перевес, а удачную реализацию шума. Проверяем это
прямо: берём СЛУЧАЙНЫЕ геномы ядра (никем не подобранные, все ворота выключены),
гоняем их на ТОМ ЖЕ холдауте на том же плече x5 и смотрим, где среди них стоит
+28.5%. Если случайный перебор из пары сотен геномов регулярно даёт столько же —
находка это не перевес, а место в хвосте распределения шума.

Заодно: когда именно пришли те 31 сделка (не собрались ли они в одном месяце)
и в какую сторону торговали.
"""
import json
import sys
import time

import numpy as np

import bots_honest as bh
import archive_honest as ah
import config
import evolution2 as e2
import evolution8 as e8
import ext_data as xd

N_RAND = 250
SEED = 4242


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pct5 = xd.fetch_daily_pct5()
    e2.BARS_PER_DAY = 96
    p = config.SYMBOL_PARAMS["BTCUSDT"]["normal"]
    d = ah.build("BTCUSDT", "normal", p, pct5)
    s = d["hold"]
    aux_h = {k: bh.slice_aux(v, d["hold_i"], d["n"]) for k, v in d["aux"].items()}

    # --- когда и куда торговал сам конфиг --------------------------------
    evs = []
    bh.run_at(s["candles"], s["pre"], d["g"], s["filt"], 5, events=evs)
    rows, side = [], None
    for e in evs:
        if e["type"] == "entry":
            side = e["side"]
        elif e["type"] == "close":
            rows.append((e["t"], side, e["pnl"], e.get("reason")))
            side = None
    print("СДЕЛКИ ПО ВРЕМЕНИ (BTC normal x5, холдоут)")
    print("%-12s %-4s %8s  %s" % ("закрытие", "сторона", "PnL$", "причина"))
    for t, sd_, pnl, r in rows:
        print("%-12s %-4s %+8.3f  %s" % (bh.fmt_day(t), sd_ or "?", pnl, r))
    by_m, by_side = {}, {}
    for t, sd_, pnl, r in rows:
        m = bh.fmt_day(t)[:7]
        by_m[m] = by_m.get(m, [0, 0.0])
        by_m[m][0] += 1
        by_m[m][1] += pnl
        by_side[sd_] = by_side.get(sd_, [0, 0.0])
        by_side[sd_][0] += 1
        by_side[sd_][1] += pnl
    print("\nПО МЕСЯЦАМ (сделок / PnL$):")
    for m in sorted(by_m):
        print("   %s  %2d  %+7.3f" % (m, by_m[m][0], by_m[m][1]))
    print("ПО СТОРОНАМ:")
    for k, v in by_side.items():
        print("   %-4s %2d сделок  %+7.3f$" % (k, v[0], v[1]))

    # --- НУЛЕВОЕ РАСПРЕДЕЛЕНИЕ: случайные геномы -------------------------
    print("\nСЛУЧАЙНЫЕ ГЕНОМЫ ЯДРА на том же холдауте, плечо x5 (%d штук)" % N_RAND)
    rng = np.random.default_rng(SEED)
    res = []
    t0 = time.time()
    for i in range(N_RAND):
        g = bh.rand_core(e8.GENES8, rng)
        try:
            ev2 = []
            r2 = bh.run_at(s["candles"], s["pre"], g,
                           e8.make_filter8(g, aux_h), 5, events=ev2)
            m = bh.summarize(r2, ev2, s["months"])
            pn = [e["pnl"] for e in ev2 if e["type"] == "close"]
            res.append(dict(trades=m["trades"], comp=m["comp"], dd=m["dd"],
                            comp_nt=bh.ret_no_tiny(pn)))
        except Exception:                              # noqa: BLE001
            continue
        if (i + 1) % 25 == 0:
            print("   ...%d/%d, %.0fс" % (i + 1, N_RAND, time.time() - t0))
            sys.stdout.flush()

    comps = np.array([r["comp"] for r in res if r["trades"] > 0])
    trs = np.array([r["trades"] for r in res if r["trades"] > 0])
    print("\nвсего пригодных геномов: %d (из %d)" % (len(comps), N_RAND))
    print("итог на холдауте: медиана %+.1f%%, сред %+.1f%%, sd %.1f%%"
          % (np.median(comps), comps.mean(), comps.std()))
    print("перцентили 50/75/90/95/99: " +
          " ".join("%+.1f%%" % np.percentile(comps, q) for q in (50, 75, 90, 95, 99)))
    print("лучший случайный: %+.1f%%   доля случайных >= +28.5%%: %.1f%%"
          % (comps.max(), (comps >= 28.5).mean() * 100))

    # то же, но только среди геномов с сопоставимой редкостью сделок:
    # у находки 31 сделка, сравнивать её честно с такими же «редкими»
    sel = (trs >= 15) & (trs <= 60)
    cs = comps[sel]
    if len(cs) > 4:
        print("\nтолько геномы с 15..60 сделками (как у находки): %d штук" % len(cs))
        print("   медиана %+.1f%%, sd %.1f%%, лучший %+.1f%%, доля >= +28.5%%: %.1f%%"
              % (np.median(cs), cs.std(), cs.max(), (cs >= 28.5).mean() * 100))
    print("\nсвязь редкости и итога: корреляция(сделки, итог) = %.2f"
          % np.corrcoef(trs, comps)[0, 1])

    with open("refute_btc_null.json", "w", encoding="utf-8") as fh:
        json.dump(dict(res=res, trades_side={k: v for k, v in by_side.items()},
                       by_month={k: v for k, v in by_m.items()}),
                  fh, ensure_ascii=False)
    print("\nсохранено: refute_btc_null.json")


if __name__ == "__main__":
    main()
