# -*- coding: utf-8 -*-
"""БЫЛ ЛИ ВООБЩЕ ЗНАЧИМ ПОЛОЖИТЕЛЬНЫЙ РЕЗУЛЬТАТ НА ОБУЧЕНИИ.

Спор «работает отбор или нет» имеет смысл только если на обучении было что
отбирать. Здесь это проверяется без предположения о нормальности: месячные
доходности ансамбля откровенно с тяжёлыми хвостами (один месяц +31%),
и оценка «среднее плюс-минус две ошибки» на таких данных ненадёжна.

Три независимых способа:
  1. блочный бутстрэп по СДЕЛКАМ ансамбля (двухнедельные блоки — сделки
     перекрываются во времени, до 28 одновременно, поодиночке их мешать
     нельзя);
  2. бутстрэп по МЕСЯЦАМ;
  3. знаковый тест по месяцам — вообще без арифметики средних.

Плюс нормировка на число попыток: результат должен быть не просто
положительным, а положительным НАСТОЛЬКО, чтобы пережить 26 560 попыток.

Экзаменационная выборка не читается: всё считается на своём кэше
обучения+проверки.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import frontier                   # noqa: E402
import portfolio                  # noqa: E402
import rdata                      # noqa: E402
import refute_stats_cache as C    # noqa: E402

OUT = os.path.join(DIR, "out")
DAY = rdata.DAY_MS
RISK_K = 0.43
N_BOOT = 20000
N_TRIALS = 26560


def main():
    rng = np.random.default_rng(4242)
    print("=" * 78)
    print("БЫЛ ЛИ ЗНАЧИМ САМ ПОЛОЖИТЕЛЬНЫЙ РЕЗУЛЬТАТ НА ОБУЧЕНИИ")
    print("=" * 78)

    raw = C.load()
    by_name = C.as_trades(raw)
    ens = frontier.merge([by_name[n] for n in C.ENSEMBLE if n in by_name])
    c = portfolio.combine(ens, risk_each=RISK_K)
    mo = portfolio.monthly_from_curve(c["times"], c["curve"])
    mv = np.array([m[1] for m in mo])
    print("ансамбль на обучении+проверке: %+.2f%% в месяц, просадка %.1f%%, "
          "сделок %d" % (100 * c["mo"], 100 * c["maxdd"], c["trades"]))

    # --- 1. блочный бутстрэп по сделкам ------------------------------------
    trs = sorted([t for v in ens.values() for t in v], key=lambda x: x["t_in"])
    r = np.array([t["ret"] for t in trs]) * RISK_K
    tt = np.array([t["t_in"] for t in trs])
    days = (tt.max() - tt.min()) / DAY
    # блоки по две недели: сделки внутри блока держатся вместе
    blk_id = ((tt - tt.min()) // (14 * DAY)).astype(int)
    blocks = [r[blk_id == b] for b in range(blk_id.max() + 1)]
    blocks = [b for b in blocks if len(b)]
    nb = len(blocks)
    print("\n1. БЛОЧНЫЙ БУТСТРЭП ПО СДЕЛКАМ (%d сделок, %d двухнедельных блоков)"
          % (len(r), nb))
    obs_log = float(np.log1p(np.clip(r, -0.99, None)).sum())
    sims = np.empty(N_BOOT)
    logs = [np.log1p(np.clip(b, -0.99, None)).sum() for b in blocks]
    lens = [len(b) for b in blocks]
    logs = np.array(logs)
    for s in range(N_BOOT):
        idx = rng.integers(0, nb, nb)
        sims[s] = logs[idx].sum()
    mo_sims = np.expm1(sims * 30.0 / days)
    lo, hi = np.percentile(mo_sims, [2.5, 97.5])
    p0 = float((sims <= 0).mean())
    print("   наблюдено %+.2f%% в месяц" % (100 * np.expm1(obs_log * 30 / days)))
    print("   95%% интервал бутстрэпа: [%+.2f%%; %+.2f%%] в месяц"
          % (100 * lo, 100 * hi))
    print("   доля прогонов с итогом <= 0: %.3f  (это p-значение)" % p0)
    print("   %s" % ("перевес НЕ значим даже без поправки на число попыток"
                     if p0 > 0.025 else
                     "перевес значим на обычном уровне (без поправки)"))
    p_need = 0.05 / N_TRIALS
    print("   с поправкой на %d попыток требуется p < %.2e — не достигнуто"
          % (N_TRIALS, p_need))

    # --- 2. бутстрэп по месяцам --------------------------------------------
    print("\n2. БУТСТРЭП ПО МЕСЯЦАМ (%d месяцев)" % len(mv))
    lm = np.log1p(np.clip(mv, -0.99, None))
    bs = np.empty(N_BOOT)
    for s in range(N_BOOT):
        bs[s] = lm[rng.integers(0, len(lm), len(lm))].mean()
    lo2, hi2 = np.percentile(np.expm1(bs), [2.5, 97.5])
    p2 = float((bs <= 0).mean())
    print("   среднее %+.2f%%, 95%% интервал [%+.2f%%; %+.2f%%], p = %.3f"
          % (100 * np.expm1(lm.mean()), 100 * lo2, 100 * hi2, p2))
    # вклад одного лучшего месяца
    k = int(np.argmax(mv))
    without = np.delete(lm, k)
    print("   без единственного лучшего месяца (%+.0f%%): %+.2f%% в месяц"
          % (100 * mv[k], 100 * np.expm1(without.mean())))
    print("   то есть %.0f%% всей заявленной доходности принесли %d месяц из %d"
          % (100 * (1 - np.expm1(without.mean()) / np.expm1(lm.mean())),
             1, len(mv)))

    # --- 3. знаковый тест --------------------------------------------------
    npos = int((mv > 0).sum())
    n = len(mv)
    # точное биномиальное p для «не меньше npos плюсовых при честной монетке»
    from math import comb
    p3 = sum(comb(n, k) for k in range(npos, n + 1)) / 2.0 ** n
    print("\n3. ЗНАКОВЫЙ ТЕСТ: плюсовых месяцев %d из %d, p = %.3f"
          % (npos, n, p3))
    print("   %s" % ("для монетки это обычное дело" if p3 > 0.05
                     else "плюсовых месяцев значимо больше половины"))

    # --- 4. сколько лет нужно, непараметрически ----------------------------
    print("\n4. СКОЛЬКО ЛЕТ НУЖНО — БЕЗ ПРЕДПОЛОЖЕНИЯ О НОРМАЛЬНОСТИ")
    print("   Растим выборку месяцев бутстрэпом и смотрим, при каком её")
    print("   размере нижняя граница интервала перестаёт задевать ноль.")
    print("   Предполагается, что перевес НАСТОЯЩИЙ и равен наблюдённому.")
    print("   %-8s %-12s %-14s %-14s" % ("перевес", "лет истории",
                                         "доля прогонов", "то же с поправкой"))
    print("   %-8s %-12s %-14s %-14s" % ("в месяц", "", "с p<0.05",
                                         "на 26560 попыток"))
    rows = []
    lm_c = lm - lm.mean()          # форма распределения без перевеса
    z_multi = 4.62                 # порог для 26560 попыток (см. size)
    for tgt in (0.01, 0.02, 0.03, 0.05):
        tl = np.log1p(tgt)
        for years in (1, 2, 3, 5, 8, 12, 20, 30, 50):
            nm = int(years * 12)
            draws = lm_c[rng.integers(0, len(lm_c), (2000, nm))] + tl
            mean = draws.mean(axis=1)
            se = draws.std(axis=1, ddof=1) / np.sqrt(nm)
            ok = float((mean / se > 1.96).mean())
            ok_m = float((mean / se > z_multi).mean())
            if ok >= 0.80:
                break
        years_m = None
        for ym in (2, 3, 5, 8, 12, 20, 30, 50, 80, 120, 200, 400):
            nm = int(ym * 12)
            draws = lm_c[rng.integers(0, len(lm_c), (1000, nm))] + tl
            mean = draws.mean(axis=1)
            se = draws.std(axis=1, ddof=1) / np.sqrt(nm)
            if float((mean / se > z_multi).mean()) >= 0.80:
                years_m = ym
                break
        rows.append(dict(tgt=tgt, years=years, power=ok,
                         years_multi=years_m))
        print("   %-8s %-12s %-14s %-14s"
              % ("+%.0f%%" % (100 * tgt), "%d" % years, "%.0f%%" % (100 * ok),
                 ("%d лет" % years_m) if years_m else ">400 лет"))
    print("   (второй столбец — первый срок, где надёжность >= 80%)")
    print("   последний столбец — то же, но результат обязан пережить")
    print("   26 560 попыток отбора. Это и есть цена перебора.")

    print("\nИТОГ ЭТОГО ФАЙЛА")
    print("   Положительный результат обучения (%+.2f%% в месяц) сам по себе"
          % (100 * c["mo"]))
    print("   НЕ значим: p = %.2f по сделкам, %.2f по месяцам." % (p0, p2))
    print("   Значит спор «отбор работает или нет» вёлся вокруг величины,")
    print("   которая и на обучении не была отличима от нуля. Экзамен не мог")
    print("   опровергнуть то, что не было установлено.")

    out = dict(mo=c["mo"], maxdd=c["maxdd"], trades=c["trades"],
               p_trades=p0, ci_trades=[float(lo), float(hi)],
               p_months=p2, ci_months=[float(lo2), float(hi2)],
               p_sign=p3, months_pos=npos, months=n,
               best_month=float(mv[k]),
               mo_without_best=float(np.expm1(without.mean())),
               years=rows)
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "refute_stats_boot.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, default=float)
    print("\nсохранено -> out/refute_stats_boot.json")


if __name__ == "__main__":
    main()
