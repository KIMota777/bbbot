# -*- coding: utf-8 -*-
"""ЛИНЗА «31 СДЕЛКА — ЭТО НИЧТО».

Цель — не подтвердить находку (BTC normal x5, холдоут +28.5%), а проверить,
отличима ли она вообще от везения при таком числе наблюдений. Считаем:
  1) бутстрэп по сделкам: какой разброс итога даёт 31 сделка;
  2) порог чувствительности: какой перевес на сделку в принципе различим
     на 31 наблюдении при наблюдённом разбросе;
  3) что остаётся без топ-1, топ-2, топ-3 сделок;
  4) эталоны: «купил BTC и держал» за тот же холдоут и тот же конфиг на x1.
"""
import json
import sys
import time

import numpy as np

import bots_honest as bh
import archive_honest as ah
import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd


def holdout_run(sym, mode, lev=None):
    """Прогон конфига на холдауте; вернуть сделки, даты и сводку."""
    pct5 = xd.fetch_daily_pct5()
    e2.BARS_PER_DAY = 96
    p = config.SYMBOL_PARAMS[sym][mode]
    d = ah.build(sym, mode, p, pct5)
    if lev is not None:
        d["lev"] = lev
    s = d["hold"]
    evs = []
    r = bh.run_at(s["candles"], s["pre"], d["g"], s["filt"], d["lev"], events=evs)
    m = bh.summarize(r, evs, s["months"])
    closes = [e for e in evs if e["type"] == "close"]
    return dict(cfg=d, sum=m, closes=closes,
                pnls=[e["pnl"] for e in closes],
                t0=s["candles"][0][0], t1=s["candles"][-1][0],
                px0=s["candles"][0][4], px1=s["candles"][-1][4],
                months=s["months"], lev=d["lev"])


def comp(pnls):
    """Итог с реинвестом — ровно то, чем меряет отчёт (compound_pct)."""
    return bh.compound_pct(pnls)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    print("ЛИНЗА: 31 СДЕЛКА — " + time.strftime("%Y-%m-%d %H:%M"))
    out = {}

    a = holdout_run("BTCUSDT", "normal")
    pnls = np.array(a["pnls"], dtype=float)
    n = len(pnls)
    print("\nBTC normal x%d, холдоут %s .. %s (%.1f мес)"
          % (a["lev"], bh.fmt_day(a["t0"]), bh.fmt_day(a["t1"]), a["months"]))
    print("сделок %d, итог(реинвест) %+.1f%%, просадка %.1f%%"
          % (n, a["sum"]["comp"], a["sum"]["dd"]))
    print("PnL сделок ($, маржа цикла $%.0f):" % e2.MARGIN)
    print("  " + " ".join("%+.3f" % p for p in sorted(pnls, reverse=True)))

    # ---- 1. БУТСТРЭП ПО СДЕЛКАМ -------------------------------------------
    # Пересобираем те же 31 сделку с возвращением 20000 раз и каждый раз
    # считаем итог тем же способом (реинвест). Это отвечает на вопрос
    # «а если бы порядок и состав сделок сложились чуть иначе».
    rng = np.random.default_rng(20260821)
    B = 20000
    idx = rng.integers(0, n, size=(B, n))
    boot = np.array([comp(pnls[row]) for row in idx])
    q = np.percentile(boot, [2.5, 5, 25, 50, 75, 95, 97.5])
    print("\n1) БУТСТРЭП %d повторов по %d сделкам" % (B, n))
    print("   медиана %+.1f%%   ДИ95 [%+.1f%% .. %+.1f%%]   ДИ90 [%+.1f%% .. %+.1f%%]"
          % (q[3], q[0], q[6], q[1], q[5]))
    print("   квартили [%+.1f%% .. %+.1f%%]" % (q[2], q[4]))
    print("   доля повторов НИЖЕ НУЛЯ: %.1f%%" % ((boot < 0).mean() * 100))
    out["boot"] = dict(B=B, n=n, med=q[3], lo95=q[0], hi95=q[6],
                       lo90=q[1], hi90=q[5], share_neg=float((boot < 0).mean() * 100))

    # ---- 2. ПОРОГ ЧУВСТВИТЕЛЬНОСТИ ---------------------------------------
    # В долях маржи (R): сколько «зарабатывает» одна сделка и каков разброс.
    R = pnls / e2.MARGIN
    mu, sd = R.mean(), R.std(ddof=1)
    se = sd / np.sqrt(n)
    t = mu / se
    # Минимальный перевес, который проверка ОТЛИЧИТ от нуля: для значимости
    # (alpha=5%, двусторонний) нужно |mu| > 2.042*se; для нормальной мощности
    # (80%) — |mu| > (2.042+0.843)*se.
    mde_sig = 2.042 * se
    mde_pow = 2.885 * se
    print("\n2) ПОРОГ ЧУВСТВИТЕЛЬНОСТИ (перевес на сделку, в долях маржи)")
    print("   наблюдённый перевес  %+.4f R  (%.2f%% маржи за сделку)" % (mu, mu * 100))
    print("   разброс сделок sd    %.4f R   ошибка среднего %.4f R" % (sd, se))
    print("   t = %.2f  (для значимости нужно |t|>2.04)" % t)
    print("   различимо только начиная с %+.4f R (значимость)" % mde_sig)
    print("   и с %+.4f R, чтобы поймать его с вероятностью 80%%" % mde_pow)
    print("   наблюдённое / порог(мощность) = %.2f" % (mu / mde_pow))
    out["edge"] = dict(mu_R=float(mu), sd_R=float(sd), se=float(se), t=float(t),
                       mde_sig=float(mde_sig), mde_pow=float(mde_pow))

    # ---- 3. БЕЗ ЛУЧШИХ СДЕЛОК --------------------------------------------
    order = np.argsort(-pnls)
    print("\n3) ЧТО ОСТАЁТСЯ БЕЗ ЛУЧШИХ СДЕЛОК (реинвест / без реинвеста)")
    print("   все %2d сделок : %+7.1f%% / %+7.1f%%"
          % (n, comp(pnls), pnls.sum() / e2.START * 100))
    drops = []
    for k in (1, 2, 3, 4, 5):
        rest = np.delete(pnls, order[:k])
        c, f = comp(rest), rest.sum() / e2.START * 100
        print("   без топ-%d      : %+7.1f%% / %+7.1f%%   (выброшено $%.2f)"
              % (k, c, f, pnls[order[:k]].sum()))
        drops.append(dict(k=k, comp=float(c), flat=float(f),
                          removed_usd=float(pnls[order[:k]].sum())))
    # доля итога, которую делают три лучшие
    print("   три лучшие сделки дают $%.2f из $%.2f валового плюса (%.0f%%)"
          % (pnls[order[:3]].sum(), pnls[pnls > 0].sum(),
             pnls[order[:3]].sum() / pnls[pnls > 0].sum() * 100))
    out["drops"] = drops

    # ---- 4. ЭТАЛОНЫ -------------------------------------------------------
    bh_ret = (a["px1"] / a["px0"] - 1) * 100
    print("\n4) ЭТАЛОНЫ ЗА ТОТ ЖЕ ХОЛДОУТ")
    print("   купил BTC и держал (x1): %+.1f%%  (цена %.0f -> %.0f)"
          % (bh_ret, a["px0"], a["px1"]))
    b = holdout_run("BTCUSDT", "normal", lev=1)
    pn1 = np.array(b["pnls"], dtype=float)
    print("   тот же конфиг на x1    : %+.1f%% (реинвест), сделок %d, просадка %.1f%%"
          % (b["sum"]["comp"], len(pn1), b["sum"]["dd"]))
    out["bench"] = dict(buy_hold=float(bh_ret), x1_comp=float(b["sum"]["comp"]),
                        x1_trades=len(pn1), x1_dd=float(b["sum"]["dd"]),
                        x5_comp=float(a["sum"]["comp"]), x5_dd=float(a["sum"]["dd"]))

    # просадка «купил и держал» на тех же свечах — чтобы сравнить риск честно
    px = np.array([c[4] for c in a["cfg"]["hold"]["candles"]], dtype=float)
    peak = np.maximum.accumulate(px)
    bh_dd = float(((peak - px) / peak).max() * 100)
    print("   просадка «купил и держал»: %.1f%%   у конфига x5: %.1f%%"
          % (bh_dd, a["sum"]["dd"]))
    print("   доходность на единицу просадки: держал %.2f, конфиг x5 %.2f, конфиг x1 %.2f"
          % (bh_ret / bh_dd, a["sum"]["comp"] / a["sum"]["dd"],
             b["sum"]["comp"] / max(b["sum"]["dd"], 1e-9)))
    out["bench"]["buy_hold_dd"] = bh_dd

    with open("refute_btc_bootstrap.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    # сырые сделки — пригодятся дальше
    with open("refute_btc_trades.json", "w", encoding="utf-8") as fh:
        json.dump(dict(pnls=[float(x) for x in pnls],
                       ts=[e["t"] for e in a["closes"]],
                       reasons=[e.get("reason") for e in a["closes"]],
                       t0=a["t0"], t1=a["t1"], months=a["months"],
                       px0=a["px0"], px1=a["px1"]), fh, ensure_ascii=False)
    print("\nсохранено: refute_btc_bootstrap.json, refute_btc_trades.json")


if __name__ == "__main__":
    main()
