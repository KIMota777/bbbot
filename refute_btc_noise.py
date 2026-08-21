# -*- coding: utf-8 -*-
"""ПОПЫТКА СЛОМАТЬ НАХОДКУ, ЧАСТЬ 2: шум, хвост убытков и точка раскола.

Блок 5. ЧТО ЗНАЧАТ 31 СДЕЛКА. Считаем не «сколько заработал», а «отличим ли
  этот заработок от нуля»: средний результат сделки в долях маржи, его
  стандартная ошибка, t и бутстрэп итога. Там же — прямое сравнение с final:
  разность средних по сделке и её ошибка.

Блок 6. НУЛЕВОЕ РАСПРЕДЕЛЕНИЕ. 250 СЛУЧАЙНЫХ геномов ядра сетки на том же
  холдоуте и том же плече x5. Если случайная сетка на медвежьем куске в
  среднем тоже в плюсе, то +28.5% — свойство КУСКА РЫНКА, а не конфига.
  Заодно это честная цена выбора «лучшего из архива»: тринадцать конфигов
  ранжировали по холдауту, а лучший из тринадцати — это уже отбор ПО ЭКЗАМЕНУ.

Блок 7. ХВОСТ. У этой стратегии выигрыш ограничен тейком, а убыток — нет
  (стоп ставится за границей окна в 877 свечей). Профиль «много мелких побед,
  редкая большая потеря» на 31 сделке выглядит блестяще ровно до первой
  потери. Смотрим распределение убытков на всей истории и считаем, сколько
  «крупных» потерь пришлось на обучение и сколько на холдоут.

Блок 8. ТОЧКА РАСКОЛА. 0.72 — соглашение, а не закон природы. Гоняем оба
  конфига на холдоутах от 0.55 до 0.88. Настоящий перевес виден при любой
  разумной точке раскола; артефакт живёт в узком окне.

Блок 9. УСТОЙЧИВОСТЬ ПРИ РАВНОМ ПЛЕЧЕ. Те же 90 возмущений +-10%, но оба
  конфига на x5 и не только на холдоуте, но и на обучении.
"""
import random as pyrand
import sys
import time

import numpy as np

import bots_honest as bh
import refute_btc_cache as cache
import config
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8


def genome(mode):
    p = config.SYMBOL_PARAMS["BTCUSDT"][mode]
    g = e7.cfg_to_genome(p, mode)
    for k, v in e8.OFF8.items():
        g.setdefault(k, v)
    return g, p.get("lev", 5)


def seg(candles, aux, a, b):
    c = candles[a:b]
    return dict(candles=c, pre=e2.prep(c),
                aux={k: bh.slice_aux(v, a, b) for k, v in aux.items()},
                months=(c[-1][0] - c[0][0]) / (30 * 86400000))


def run(s, g, lev):
    evs = []
    r = bh.run_at(s["candles"], s["pre"], g, e8.make_filter8(g, s["aux"]),
                  lev, events=evs)
    m = bh.summarize(r, evs, s["months"])
    m["evs"] = evs
    return m


def main():
    tee = bh.Tee("refute_btc_noise_out.txt")
    old, sys.stdout = sys.stdout, tee
    try:
        body()
    finally:
        sys.stdout = old


def body():
    print("ПОПЫТКА ОПРОВЕРЖЕНИЯ, ЧАСТЬ 2 — " + time.strftime("%Y-%m-%d %H:%M"))
    candles, aux = cache.load()
    e2.BARS_PER_DAY = 96
    n = len(candles)
    h = int(n * bh.HOLD_FRAC)
    gn, _ = genome("normal")
    gf, _ = genome("final")
    hold = seg(candles, aux, h, n)
    train = seg(candles, aux, 0, h)
    full = seg(candles, aux, 0, n)

    mn = run(hold, gn, 5)
    mf = run(hold, gf, 5)

    # ---- 5. отличим ли плюс от нуля ------------------------------------
    print("\n" + "=" * 100)
    print("БЛОК 5. 31 СДЕЛКА: ОТЛИЧИМ ЛИ ПЛЮС ОТ НУЛЯ (холдоут, оба на x5)")
    rng = np.random.default_rng(20260821)

    def stat(m, name):
        r = np.array(m["pnls"]) / e2.MARGIN      # результат в долях маржи
        se = r.std(ddof=1) / np.sqrt(len(r))
        t = r.mean() / se if se else 0.0
        boot = np.array([rng.choice(r, len(r), replace=True).sum()
                         for _ in range(20000)]) / e2.START * 100
        print("  %-7s сделок %2d | средняя %+0.4f R | сигма %0.3f | "
              "t = %+0.2f | итог %+6.1f%%"
              % (name, len(r), r.mean(), r.std(ddof=1), t,
                 r.sum() / e2.START * 100))
        print("          бутстрэп итога: 5%% %+6.1f%% | медиана %+6.1f%% | "
              "95%% %+6.1f%% | доля прогонов в минус %.0f%%"
              % (np.percentile(boot, 5), np.median(boot),
                 np.percentile(boot, 95), (boot < 0).mean() * 100))
        return r

    rn = stat(mn, "normal")
    rf = stat(mf, "final")
    d = rn.mean() - rf.mean()
    sed = np.sqrt(rn.var(ddof=1) / len(rn) + rf.var(ddof=1) / len(rf))
    print("  разность средних normal-final: %+0.4f R, ошибка %0.4f, "
          "t = %+0.2f  -> %s"
          % (d, sed, d / sed,
             "значимо" if abs(d / sed) > 2 else "НЕ значимо (в пределах шума)"))

    # ---- 6. нулевое распределение --------------------------------------
    print("\n" + "=" * 100)
    print("БЛОК 6. 250 СЛУЧАЙНЫХ ГЕНОМОВ ЯДРА НА ТОМ ЖЕ ХОЛДОУТЕ, x5")

    def nulls(s, tag):
        pr = pyrand.Random(2026)
        vals = []
        for _ in range(250):
            g = bh.rand_core(e2.GENES, pr)
            for k, v in e8.OFF8.items():
                g.setdefault(k, v)
            try:
                vals.append(run(s, g, 5)["comp"])
            except Exception:                      # noqa: BLE001
                continue
        a = np.array(vals)
        print("  %-9s посчитано %d | медиана %+7.1f%% | доля в плюсе %3.0f%% "
              "| 75%% %+7.1f%% | 90%% %+7.1f%% | макс %+7.1f%%"
              % (tag, len(a), np.median(a), (a > 0).mean() * 100,
                 np.percentile(a, 75), np.percentile(a, 90), a.max()))
        return a

    a = nulls(hold, "холдоут")
    print("  доля случайных, которые НЕ ХУЖЕ normal (%+.1f%%): %.1f%%"
          % (mn["comp"], (a >= mn["comp"]).mean() * 100))
    print("  доля случайных, которые НЕ ХУЖЕ final  (%+.1f%%): %.1f%%"
          % (mf["comp"], (a >= mf["comp"]).mean() * 100))
    nulls(train, "обучение")

    # ---- 7. хвост убытков ----------------------------------------------
    print("\n" + "=" * 100)
    print("БЛОК 7. ХВОСТ УБЫТКОВ (вся история, x5)")
    split_t = candles[h][0]
    for name, g in (("normal", gn), ("final", gf)):
        m = run(full, g, 5)
        p = np.array(m["pnls"])
        loss = np.sort(p[p < 0])
        win = p[p > 0]
        print("  %-7s сделок %3d | лучшая %+0.2f$ | худшая %+0.2f$ | "
              "потерь тяжелее -1$: %d | тяжелее -2$: %d"
              % (name, len(p), win.max() if len(win) else 0.0,
                 loss.min() if len(loss) else 0.0,
                 int((p < -1).sum()), int((p < -2).sum())))
        print("          пять худших: %s"
              % ", ".join("%+0.2f$" % x for x in loss[:5]))
        ts = [e["t"] for e in m["evs"] if e["type"] == "close"]
        big = [(t, x) for t, x in zip(ts, p) if x < -1]
        print("          из них на обучении %d, на холдоуте %d"
              % (sum(1 for t, _ in big if t < split_t),
                 sum(1 for t, _ in big if t >= split_t)))

    # ---- 8. точка раскола ----------------------------------------------
    print("\n" + "=" * 100)
    print("БЛОК 8. ПЕРЕВЕС ПРОТИВ ТОЧКИ РАСКОЛА (оба на x5)")
    print("  %6s %12s %10s %6s %10s %6s"
          % ("доля", "начало", "normal", "сдел", "final", "сдел"))
    for frac in (0.55, 0.60, 0.65, 0.72, 0.78, 0.84, 0.88):
        k = int(n * frac)
        s = seg(candles, aux, k, n)
        a1 = run(s, gn, 5)
        b1 = run(s, gf, 5)
        print("  %6.2f %12s %+9.1f%% %6d %+9.1f%% %6d"
              % (frac, bh.fmt_day(s["candles"][0][0]), a1["comp"],
                 a1["trades"], b1["comp"], b1["trades"]))
        sys.stdout.flush()

    # ---- 9. устойчивость при равном плече -------------------------------
    print("\n" + "=" * 100)
    print("БЛОК 9. 90 ВОЗМУЩЕНИЙ +-10%, ОБА НА x5, ОБА КУСКА")
    for part, s in (("холдоут", hold), ("обучение", train)):
        for name, g in (("normal", gn), ("final", gf)):
            rg = np.random.default_rng(7)
            out = []
            for _ in range(90):
                gp = bh.perturb(g, e8.GENES8, rg, bh.PERT)
                try:
                    out.append(run(s, gp, 5)["comp"])
                except Exception:                  # noqa: BLE001
                    continue
            v = np.array(out)
            print("  %-8s %-7s медиана %+7.1f%% | 10%% %+7.1f%% | "
                  "90%% %+7.1f%% | доля+ %3.0f%% | сам конфиг %+7.1f%%"
                  % (part, name, np.median(v), np.percentile(v, 10),
                     np.percentile(v, 90), (v > 0).mean() * 100,
                     run(s, g, 5)["comp"]))
            sys.stdout.flush()


if __name__ == "__main__":
    main()
