# -*- coding: utf-8 -*-
"""ПОПЫТКА СЛОМАТЬ НАХОДКУ «BTC normal x5 лучше боевого BTC final x15».

Шаг 1 — воспроизведение и первые дешёвые проверки:
  * повторяю цифры архива (сделок 31, холдаут +28.5%, просадка 9.3%);
  * смотрю, из чего сложился плюс: список всех сделок холдаута по датам
    и вклад каждой. Если весь итог сидит в одной-двух сделках, «перевес» —
    это не свойство стратегии, а одно везение;
  * снимаю подозрение на подмену: сравниваю не «конфиг против конфига», а
    равные плечи. Боевой BTC стоит на x15, архивный на x5 — просадка 22.4%
    против 9.3% может быть целиком следствием плеча, а не качества генома.
"""
import sys
import time

import archive_honest as ah
import bots_honest as bh
import config
import evolution2 as e2
import ext_data as xd

e2.BARS_PER_DAY = 96


def hold_run(d, lev, tag):
    s = d["hold"]
    evs = []
    r = bh.run_at(s["candles"], s["pre"], d["g"], s["filt"], lev, events=evs)
    m = bh.summarize(r, evs, s["months"])
    tiny = bh.cycle_metrics(evs, s["candles"], d["g"], s["months"])
    pnls = [e["pnl"] for e in evs if e["type"] == "close"]
    print("  %-26s сделок %4d | итог(фикс) %+7.1f%% | реинвест %+7.1f%% | "
          "б/копеек %+7.1f%% | просадка %5.1f%% | копеечных %3.0f%% | слив %s"
          % (tag, m["trades"], m["ret"], m["comp"], bh.ret_no_tiny(pnls),
             m["dd"], tiny["tiny_share"], r["ruined"]))
    sys.stdout.flush()
    return m, tiny, evs


def main():
    t0 = time.time()
    pct5 = xd.fetch_daily_pct5()
    print("готовлю данные...")
    dn = ah.build("BTCUSDT", "normal", config.SYMBOL_PARAMS["BTCUSDT"]["normal"],
                  pct5)
    df = ah.build("BTCUSDT", "final", config.SYMBOL_PARAMS["BTCUSDT"]["final"],
                  pct5)
    print("подготовка заняла %.1f с; холдаут %d свечей, %.1f мес"
          % (time.time() - t0, len(dn["hold"]["candles"]), dn["hold"]["months"]))
    print("геном normal:", {k: dn["g"][k] for k in
                            ("rsi_idx", "rsi_os", "zone_l", "zone_s", "window",
                             "step", "levels", "mult", "tp", "sweep",
                             "max_bars", "cooldown", "knife", "be_move")})

    print("\n1. ВОСПРОИЗВЕДЕНИЕ И РАВНЫЕ ПЛЕЧИ (всё на холдауте)")
    tr = time.time()
    m_n5, tiny_n5, evs_n5 = hold_run(dn, 5, "BTC normal x5 (находка)")
    print("     один прогон холдаута = %.2f с" % (time.time() - tr))
    hold_run(dn, 15, "BTC normal x15")
    hold_run(df, 15, "BTC final x15 (боевой)")
    hold_run(df, 5, "BTC final x5")

    print("\n2. ИЗ ЧЕГО СЛОЖИЛСЯ ПЛЮС: все сделки холдаута BTC normal x5")
    rows = [(e["t"], e["pnl"], e.get("reason")) for e in evs_n5
            if e["type"] == "close"]
    tot = sum(p for _, p, _ in rows)
    for ts, p, why in rows:
        print("     %s  %+8.3f$  (%5.1f%% итога)  %s"
              % (bh.fmt_day(ts), p, p / tot * 100 if tot else 0, why))
    srt = sorted(rows, key=lambda x: -x[1])
    print("     итого %+.3f$ = %+.1f%% от базы $20" % (tot, tot / e2.START * 100))
    for k in (1, 2, 3):
        rest = tot - sum(p for _, p, _ in srt[:k])
        print("     без %d лучш. сделок: %+.3f$ = %+.1f%%"
              % (k, rest, rest / e2.START * 100))
    print("\nвсего %.1f с" % (time.time() - t0))


main()
