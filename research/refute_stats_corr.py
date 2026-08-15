# -*- coding: utf-8 -*-
"""СВЯЗЬ -0.17: несёт ли она вообще какую-нибудь информацию.

Автор считает главным результатом работы число -0.17 — связь между тем, как
кандидат выступил на обучении, и тем, как он выступил на экзамене. Из него
делается вывод «способ отбора не предсказывает будущее».

Здесь это число проверяется ровно одним способом — сравнением с тем, что
даёт ЧИСТЫЙ ШУМ. Вопрос простой: если бы у всех двадцати кандидатов перевес
был одинаковый (то есть отличать их было бы НЕЧЕМ), какие значения связи мы
видели бы просто по случайности?

Три расчёта:

  1. Формула. Для двадцати точек связь -0.17 проверяется как обычная
     корреляция: t = r*sqrt(n-2)/sqrt(1-r^2). Плюс доверительный интервал
     через преобразование Фишера — он показывает, какие ИСТИННЫЕ значения
     связи совместимы с наблюдённым.

  2. Искусственные наборы из настоящих данных. Берутся помесячные доходности
     двадцати кандидатов на обучении+проверке (свой кэш, экзамен не
     читается), у каждого вычитается его собственное среднее — теперь ни у
     кого нет перевеса ПО ПОСТРОЕНИЮ. Дальше месяцы пересобираются блоками
     ОДНОВРЕМЕННО у всех двадцати, чтобы сохранить их взаимную связанность
     (все торгуют одни и те же пять монет, и их удачные месяцы совпадают).
     Из каждой пересборки нарезается «обучение» и «экзамен» той же длины, что
     у автора, и считается связь. Двести и более таких наборов дают
     распределение связи ПРИ ЗАВЕДОМОМ ОТСУТСТВИИ РАЗЛИЧИЙ.

  3. Обратная проверка — мощность. Если бы различия между кандидатами были
     НАСТОЯЩИМИ и ровно такими по размаху, как показало обучение, какую связь
     мы увидели бы на экзамене длиной 169 дней? Если и в этом случае -0.17
     попадается часто, то число не различает «отбор работает» и «отбор не
     работает» вовсе, и опираться на него нельзя ни в ту, ни в другую сторону.

Экзаменационная выборка здесь не читается и не пересчитывается. Берутся
только уже опубликованные автором итоговые числа из out/post_mortem.json.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import portfolio                  # noqa: E402
import rdata                      # noqa: E402
import refute_stats_cache as C    # noqa: E402

OUT = os.path.join(DIR, "out")
N_SIM = 4000          # искусственных наборов (задание просило двести)
BLOCK = 2             # месяцев в блоке при пересборке
SEED = 20260815


# --- вспомогательная статистика без scipy -----------------------------------

def fisher_ci(r, n, conf=0.95):
    z = np.arctanh(r)
    se = 1.0 / np.sqrt(n - 3)
    q = 1.959963985 if abs(conf - 0.95) < 1e-9 else 2.575829304
    return float(np.tanh(z - q * se)), float(np.tanh(z + q * se))


def r_to_t(r, n):
    return float(r * np.sqrt(n - 2) / np.sqrt(max(1 - r * r, 1e-12)))


def monthly_series(raw, names):
    """Помесячная доходность каждого кандидата на обучении+проверке.

    Кандидат — стратегия на всех пяти монетах с равной долей риска, ровно как
    считает автор в post_mortem (базовый риск 1% на рукав).
    """
    per = {}
    for name in names:
        if name not in raw:
            continue
        tr = C.as_trades(raw, [name])[name]
        c = portfolio.combine(tr, risk_each=1.0)
        mo = portfolio.monthly_from_curve(c["times"], c["curve"])
        per[name] = {k: v for k, v in mo}
    keys = sorted(set().union(*[set(v) for v in per.values()]))
    mat = np.array([[per[n].get(k, 0.0) for k in keys] for n in per])
    return list(per), keys, mat


def main():
    rng = np.random.default_rng(SEED)
    print("=" * 78)
    print("СВЯЗЬ -0.17 ПРОТИВ ЧИСТОГО ШУМА")
    print("=" * 78)

    with open(os.path.join(OUT, "post_mortem.json"), encoding="utf-8") as fh:
        pm = json.load(fh)
    names_pm = [r["name"] for r in pm]
    tv_obs = np.array([r["tv"]["mo"] for r in pm])
    te_obs = np.array([r["test"]["mo"] for r in pm])
    n = len(pm)
    r_obs = float(np.corrcoef(tv_obs, te_obs)[0][1])

    # ---------------------------------------------------------------- 1 ----
    print("\n1. ЧТО ГОВОРИТ САМА ФОРМУЛА (%d кандидатов)" % n)
    print("   наблюдённая связь: %+.3f" % r_obs)
    t = r_to_t(r_obs, n)
    lo, hi = fisher_ci(r_obs, n)
    print("   t = %+.2f (для значимости нужно |t| > 2.10 при %d ст.св.)"
          % (t, n - 2))
    print("   95%% интервал для ИСТИННОЙ связи: [%+.3f; %+.3f]" % (lo, hi))
    print("   то есть данные одинаково совместимы и с сильной ОТРИЦАТЕЛЬНОЙ")
    print("   связью %+.2f, и с заметной ПОЛОЖИТЕЛЬНОЙ связью %+.2f." % (lo, hi))
    print("   Стандартная ошибка связи при 20 точках: %.3f — почти полторы"
          % (1.0 / np.sqrt(n - 3)))
    print("   величины самой связи. Такой прибор не измеряет, а гадает.")

    # ---------------------------------------------------------------- 2 ----
    print("\n2. ДВЕСТИ+ ИСКУССТВЕННЫХ НАБОРОВ БЕЗ ВСЯКОГО ПЕРЕВЕСА")
    raw = C.load()
    names, months, mat = monthly_series(raw, C.NAMES)
    print("   помесячные ряды: %d кандидатов x %d месяцев (обучение+проверка)"
          % mat.shape)
    lmat = np.log1p(np.clip(mat, -0.99, None))
    # у каждого вычитаем его собственное среднее: перевеса нет ни у кого
    lnull = lmat - lmat.mean(axis=1, keepdims=True)
    cc = np.corrcoef(lnull)
    off = cc[~np.eye(len(cc), dtype=bool)]
    print("   средняя попарная связанность кандидатов между собой: %+.2f"
          % off.mean())
    print("   (они торгуют одни и те же пять монет, поэтому независимых")
    print("    наблюдений здесь МЕНЬШЕ двадцати — связь гуляет ещё сильнее)")

    n_tr = 18            # месяцев в «обучении», как у автора
    n_te = 6             # месяцев в «экзамене» (169 дней = 5.6 месяца)
    m = mat.shape[1]
    rs = np.empty(N_SIM)
    for s in range(N_SIM):
        need = n_tr + n_te
        cols = []
        while len(cols) < need:
            st = int(rng.integers(0, max(1, m - BLOCK)))
            cols.extend(range(st, min(st + BLOCK, m)))
        cols = np.array(cols[:need])
        blk = lnull[:, cols]
        a = blk[:, :n_tr].mean(axis=1)
        b = blk[:, n_tr:].mean(axis=1)
        rs[s] = np.corrcoef(a, b)[0][1]
    p_two = float((np.abs(rs) >= abs(r_obs)).mean())
    p_one = float((rs <= r_obs).mean())
    print("\n   распределение связи при ЗАВЕДОМОМ отсутствии различий:")
    print("      среднее %+.3f, разброс %.3f" % (rs.mean(), rs.std(ddof=1)))
    print("      середина 90%%: [%+.3f; %+.3f]"
          % (np.percentile(rs, 5), np.percentile(rs, 95)))
    print("      середина 50%%: [%+.3f; %+.3f]"
          % (np.percentile(rs, 25), np.percentile(rs, 75)))
    print("   доля наборов, где чистый шум дал связь НЕ ЛУЧШЕ -0.17: %.1f%%"
          % (100 * p_one))
    print("   доля наборов, где шум дал связь такую же по величине: %.1f%%"
          % (100 * p_two))
    if p_one > 0.05:
        print("   ВЫВОД: -0.17 — рядовое значение для чистого шума. Оно НЕ")
        print("   свидетельствует о том, что отбор вреден или бесполезен;")
        print("   оно свидетельствует о том, что 20 точек ничего не мерят.")

    # ---------------------------------------------------------------- 3 ----
    print("\n3. А ЕСЛИ БЫ РАЗЛИЧИЯ БЫЛИ НАСТОЯЩИМИ? (мощность)")
    # разброс месяца у каждого кандидата — из его собственного ряда
    sd_i = lmat.std(axis=1, ddof=1)
    sd_mean = float(sd_i.mean())
    # наблюдённый размах результатов на обучении между кандидатами
    tau_obs = float(np.log1p(np.clip(tv_obs, -0.99, None)).std(ddof=1))
    se_tr = sd_mean / np.sqrt(n_tr)
    se_te = sd_mean / np.sqrt(n_te)
    tau_true = float(np.sqrt(max(tau_obs ** 2 - se_tr ** 2, 1e-8)))
    print("   разброс месяца у одного кандидата: %.2f%% (лог %.4f)"
          % (100 * np.expm1(sd_mean), sd_mean))
    print("   размах результатов между кандидатами на обучении: %.2f%%/мес"
          % (100 * np.expm1(tau_obs)))
    print("   ошибка оценки за 18 мес: %.2f%%/мес, за 6 мес: %.2f%%/мес"
          % (100 * np.expm1(se_tr), 100 * np.expm1(se_te)))
    print("   значит на НАСТОЯЩИЕ различия остаётся не больше %.2f%%/мес"
          % (100 * np.expm1(tau_true)))

    print("\n   какую связь дал бы экзамен, если бы различия были настоящими:")
    print("   %-34s %-9s %-9s %-18s" % ("предположение о различиях",
                                        "ожидаемая", "разброс",
                                        "как часто <= -0.17"))
    rows3 = []
    for label, tau in (("настоящих различий НЕТ", 0.0),
                       ("настоящие = четверть наблюдённых", 0.25 * tau_true),
                       ("настоящие = половина наблюдённых", 0.5 * tau_true),
                       ("ВСЁ наблюдённое — настоящее", tau_true),
                       ("различия ВДВОЕ крупнее", 2.0 * tau_true)):
        sim = np.empty(N_SIM)
        for s in range(N_SIM):
            mu = rng.normal(0.0, tau, n) if tau > 0 else np.zeros(n)
            a = mu + rng.normal(0.0, se_tr, n)
            b = mu + rng.normal(0.0, se_te, n)
            sim[s] = np.corrcoef(a, b)[0][1]
        frac = float((sim <= r_obs).mean())
        rho = tau ** 2 / np.sqrt((tau ** 2 + se_tr ** 2) *
                                 (tau ** 2 + se_te ** 2)) if tau > 0 else 0.0
        print("   %-34s %+8.2f  %8.3f  %14.1f%%"
              % (label, rho, sim.std(ddof=1), 100 * frac))
        rows3.append(dict(label=label, tau=tau, rho=rho,
                          sd=float(sim.std(ddof=1)), frac=frac))

    print("\n   ЧИТАТЬ ТАК: даже если бы весь разброс результатов на обучении")
    print("   был настоящим умением, ожидаемая связь на экзамене составила бы")
    print("   всего %+.2f, а её собственный разброс — около %.2f."
          % (rows3[3]["rho"], rows3[3]["sd"]))
    print("   Значение -0.17 в этом случае выпадает в %.0f%% попыток."
          % (100 * rows3[3]["frac"]))

    # ------------------------------------------------------- ранговая ------
    ra = np.argsort(np.argsort(tv_obs)) + 1.0
    rb = np.argsort(np.argsort(te_obs)) + 1.0
    r_sp = float(np.corrcoef(ra, rb)[0][1])
    print("\n   для полноты — та же связь по местам, а не по числам: %+.3f"
          % r_sp)
    lo_s, hi_s = fisher_ci(r_sp, n)
    print("   её 95%% интервал: [%+.3f; %+.3f] — тот же приговор"
          % (lo_s, hi_s))

    # --- сколько кандидатов нужно, чтобы связь вообще что-то мерила --------
    print("\n4. СКОЛЬКО КАНДИДАТОВ НУЖНО, ЧТОБЫ ЭТА СВЯЗЬ ЧТО-ТО ЗНАЧИЛА")
    rho_true = rows3[3]["rho"]
    if rho_true > 0.01:
        z = np.arctanh(rho_true)
        n_need = (1.96 + 0.84) ** 2 / z ** 2 + 3
        print("   при ожидаемой настоящей связи %+.2f нужно %.0f кандидатов,"
              % (rho_true, n_need))
        print("   чтобы отличить её от нуля с уверенностью 95%% и мощностью 80%%.")
        print("   У автора их 20 — то есть в %.0f раз меньше нужного."
              % (n_need / n))
    else:
        print("   ожидаемая настоящая связь неотличима от нуля при любом n")

    # ---------------------------------------------------------------- 5 ----
    print("\n5. «ИЗ ДВАДЦАТИ В ПЛЮСЕ ТОЛЬКО ТРИ» — насколько это редкость")
    print("   Это НЕ двадцать независимых монеток: все двадцать торгуют одни")
    print("   и те же пять монет в одни и те же 169 дней. Считаем честно —")
    print("   пересобираем месяцы одновременно у всех двадцати.")
    cnt = np.empty(N_SIM, dtype=int)
    for s in range(N_SIM):
        cols = []
        while len(cols) < n_te:
            st = int(rng.integers(0, max(1, m - BLOCK)))
            cols.extend(range(st, min(st + BLOCK, m)))
        blk = lnull[:, np.array(cols[:n_te])]
        cnt[s] = int((blk.mean(axis=1) > 0).sum())
    n_pos_obs = int((te_obs > 0).sum())
    print("   при ЗАВЕДОМОМ отсутствии перевеса у всех: в плюсе бывает")
    print("      медиана %d из 20, середина 90%%: от %d до %d"
          % (int(np.median(cnt)), int(np.percentile(cnt, 5)),
             int(np.percentile(cnt, 95))))
    p_cnt = float((cnt <= n_pos_obs).mean())
    print("   наблюдалось %d из 20; чистый шум даёт %d и меньше в %.0f%% "
          "случаев" % (n_pos_obs, n_pos_obs, 100 * p_cnt))
    if p_cnt > 0.05:
        print("   То есть «в плюсе только три» — тоже не улика.")
    else:
        print("   Это уже редковато для чистого шума: %.1f%%." % (100 * p_cnt))
    print("   (для сравнения: если бы двадцать были НЕЗАВИСИМЫ, 3 и меньше")
    print("    из 20 при честной монетке случалось бы в 0.1%% случаев —")
    print("    именно эта подмена и делает вывод убедительнее, чем он есть)")

    out = dict(r_obs=r_obs, t=t, ci=[lo, hi], spearman=r_sp,
               n_pos_obs=n_pos_obs, p_count=p_cnt,
               cnt_med=float(np.median(cnt)),
               null_mean=float(rs.mean()), null_sd=float(rs.std(ddof=1)),
               null_p_one=p_one, null_p_two=p_two,
               sd_month_log=sd_mean, tau_obs=tau_obs, tau_true=tau_true,
               se_tr=se_tr, se_te=se_te, power=rows3,
               cross_corr=float(off.mean()))
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "refute_stats_corr.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, default=float)
    print("\nсохранено -> out/refute_stats_corr.json")


if __name__ == "__main__":
    main()
