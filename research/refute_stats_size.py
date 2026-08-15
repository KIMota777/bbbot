# -*- coding: utf-8 -*-
"""СКОЛЬКО ДАННЫХ НУЖНО, ЧТОБЫ ВООБЩЕ ЧТО-ТО УТВЕРЖДАТЬ.

Вопрос не «есть ли перевес», а «сколько истории надо, чтобы отличить перевес
+3%% в месяц от нуля». Ответ считается из наблюдаемого разброса месячной
доходности самого ансамбля — то есть из чисел автора, а не из предположений.

Второй вопрос здесь же и более важный: отличается ли ВООБЩЕ результат экзамена
(-3.36%% в месяц) от результата обучения (+3.28%%)? Если нет, то фраза «отбор
не работает» опирается на разницу, которой не видно за шумом.

Экзаменационные сделки здесь не пересчитываются. Берутся уже опубликованные
автором помесячные числа из out/blind_test.json.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import frontier            # noqa: E402
import portfolio           # noqa: E402
import rdata               # noqa: E402
import refute_stats_cache as C   # noqa: E402

RISK_K = 0.43


def ensemble_months():
    """Помесячная доходность ансамбля на обучении+проверке (из своего кэша)."""
    raw = C.load()
    per = [C.as_trades(raw, [n])[n] for n in C.ENSEMBLE if n in raw]
    merged = frontier.merge(per)
    c = portfolio.combine(merged, risk_each=RISK_K)
    mo = portfolio.monthly_from_curve(c["times"], c["curve"])
    return c, [(k, v) for k, v in mo]


def n_months_needed(sd_log, target_log, z=1.96, power_z=0.0):
    """Сколько месяцев нужно, чтобы среднее отличалось от нуля."""
    return ((z + power_z) * sd_log / target_log) ** 2


def main():
    print("=" * 78)
    print("СКОЛЬКО ИСТОРИИ НУЖНО. Разброс берётся из результатов автора.")
    print("=" * 78)

    c, mo = ensemble_months()
    v = np.array([x[1] for x in mo])
    lv = np.log1p(np.clip(v, -0.99, None))
    n = len(v)
    sd = float(lv.std(ddof=1))
    mean = float(lv.mean())
    print("\nАНСАМБЛЬ НА ОБУЧЕНИИ+ПРОВЕРКЕ (пересчитано, экзамен не читался)")
    print("   месяцев %d, среднее %+.2f%% (лог %+.4f), медиана %+.2f%%"
          % (n, 100 * v.mean(), mean, 100 * np.median(v)))
    print("   разброс месяца: %.2f%% (лог-сигма %.4f)" % (100 * v.std(ddof=1), sd))
    se = sd / np.sqrt(n)
    print("   ошибка среднего: %.2f%% в месяц -> 95%% интервал [%+.2f%%; %+.2f%%]"
          % (100 * se, 100 * (np.expm1(mean - 1.96 * se)),
             100 * (np.expm1(mean + 1.96 * se))))
    print("   t = %.2f  (для «перевес больше нуля» нужно t>1.96)" % (mean / se))

    # --- сколько месяцев нужно на +3% ---------------------------------------
    print("\nСКОЛЬКО МЕСЯЦЕВ НАДО, ЧТОБЫ ОТЛИЧИТЬ ПЕРЕВЕС ОТ НУЛЯ")
    print("   при этом самом разбросе (лог-сигма %.4f в месяц)" % sd)
    print("   %-10s %-12s %-14s %-14s" % ("перевес", "только 95%",
                                          "95% + мощность", "с поправкой"))
    print("   %-10s %-12s %-14s %-14s" % ("в месяц", "(z=1.96)", "80% (z+0.84)",
                                          "на 26560 попыток"))
    # поправка на множественность: порог значимости для лучшего из 26560
    z_multi = float(-np.sqrt(2) * _erfinv(2 * (0.05 / 26560) - 1))
    for tgt in (0.01, 0.02, 0.03, 0.05):
        t = np.log1p(tgt)
        a = n_months_needed(sd, t, 1.96)
        b = n_months_needed(sd, t, 1.96, 0.84)
        d = n_months_needed(sd, t, z_multi, 0.84)
        print("   %-10s %-12s %-14s %-14s"
              % ("+%.0f%%" % (100 * tgt),
                 "%.1f лет" % (a / 12), "%.1f лет" % (b / 12),
                 "%.0f лет" % (d / 12)))
    print("   (порог для лучшего из 26560 попыток: z = %.2f)" % z_multi)

    # --- что вообще способен сказать экзамен на 169 днях --------------------
    with open(os.path.join(DIR, "out", "blind_test.json"), encoding="utf-8") as fh:
        bt = json.load(fh)
    te = bt["result"]["test"]
    tv = bt["result"]["trainval"]
    tem = np.array([m[1] for m in te["months"]])
    ltem = np.log1p(np.clip(tem, -0.99, None))
    n_te = len(tem)
    print("\nЧТО МОЖЕТ И ЧЕГО НЕ МОЖЕТ ЭКЗАМЕН НА 169 ДНЯХ (%.1f месяца)"
          % (169 / 30.4))
    se_te = sd / np.sqrt(n_te)
    print("   при разбросе обучения ошибка среднего за %d месяцев: %.2f%%"
          % (n_te, 100 * se_te))
    print("   то есть экзамен различает только перевес крупнее %.1f%% в месяц"
          % (100 * np.expm1(1.96 * se_te)))
    print("   наблюдённый разброс месяца на экзамене: %.2f%%"
          % (100 * tem.std(ddof=1)))
    se_te2 = float(ltem.std(ddof=1)) / np.sqrt(n_te)
    m_te = float(ltem.mean())
    print("   экзамен: среднее %+.2f%% в месяц, 95%% интервал [%+.2f%%; %+.2f%%]"
          % (100 * np.expm1(m_te), 100 * np.expm1(m_te - 1.96 * se_te2),
             100 * np.expm1(m_te + 1.96 * se_te2)))

    # --- главный тест: отличается ли экзамен от обучения --------------------
    print("\nГЛАВНАЯ ПРОВЕРКА: ОТЛИЧАЕТСЯ ЛИ ЭКЗАМЕН ОТ ОБУЧЕНИЯ ВООБЩЕ")
    sd_te = float(ltem.std(ddof=1))
    se_diff = np.sqrt(sd ** 2 / n + sd_te ** 2 / n_te)
    diff = mean - m_te
    t_stat = diff / se_diff
    df = _welch_df(sd, n, sd_te, n_te)
    p = 2 * (1 - _tcdf(abs(t_stat), df))
    print("   обучение %+.2f%%/мес (n=%d), экзамен %+.2f%%/мес (n=%d)"
          % (100 * np.expm1(mean), n, 100 * np.expm1(m_te), n_te))
    print("   разница %.2f п.п., ошибка разницы %.2f п.п."
          % (100 * (np.expm1(mean) - np.expm1(m_te)), 100 * se_diff))
    print("   t = %.2f, степеней свободы %.1f, p = %.3f" % (t_stat, df, p))
    if p > 0.05:
        print("   ВЫВОД: разница НЕ значима. Экзамен не опроверг обучение —")
        print("   он просто ничего не смог сказать. Утверждение «отбор не")
        print("   работает» этими данными не доказано; доказано лишь, что")
        print("   данных не хватает, чтобы его проверить.")
    else:
        print("   ВЫВОД: разница значима, вывод автора устоял.")

    # то же самое, но перестановкой (без предположения о нормальности)
    rng = np.random.default_rng(7)
    pool = np.concatenate([lv, ltem])
    obs = abs(lv.mean() - ltem.mean())
    cnt = 0
    for _ in range(20000):
        rng.shuffle(pool)
        if abs(pool[:n].mean() - pool[n:].mean()) >= obs:
            cnt += 1
    print("   перестановочный тест (20000 перемешиваний): p = %.3f"
          % (cnt / 20000))

    out = dict(n_months_tv=n, sd_log=sd, mean_log=mean,
               t_tv=mean / se, n_months_test=n_te, mean_log_test=m_te,
               t_diff=t_stat, p_diff=p, p_perm=cnt / 20000,
               years_3pct_95=n_months_needed(sd, np.log1p(0.03), 1.96) / 12,
               years_3pct_power80=n_months_needed(sd, np.log1p(0.03), 1.96,
                                                  0.84) / 12,
               years_3pct_multi=n_months_needed(sd, np.log1p(0.03), z_multi,
                                                0.84) / 12)
    with open(os.path.join(DIR, "out", "refute_stats_size.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, default=float)
    print("\nсохранено -> out/refute_stats_size.json")


# --- маленькая статистика без scipy -----------------------------------------

def _erfinv(y):
    """Обратная функция ошибок, ряд Ньютона поверх приближения."""
    a = 0.147
    ln = np.log(1 - y * y)
    t1 = 2 / (np.pi * a) + ln / 2
    x = np.sign(y) * np.sqrt(np.sqrt(t1 * t1 - ln / a) - t1)
    for _ in range(4):
        err = _erf(x) - y
        x -= err / (2 / np.sqrt(np.pi) * np.exp(-x * x))
    return x


def _erf(x):
    t = 1.0 / (1.0 + 0.3275911 * abs(x))
    y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
              - 0.284496736) * t + 0.254829592) * t * np.exp(-x * x)
    return np.sign(x) * y


def _tcdf(t, df):
    """Функция распределения Стьюдента через неполную бета-функцию."""
    x = df / (df + t * t)
    ib = _betainc(df / 2.0, 0.5, x)
    return 1 - 0.5 * ib if t > 0 else 0.5 * ib


def _betainc(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = _lgamma(a) + _lgamma(b) - _lgamma(a + b)
    front = np.exp(np.log(x) * a + np.log(1 - x) * b - lbeta) / a
    f, c, d = 1.0, 1.0, 0.0
    for i in range(0, 300):
        m = i // 2
        if i == 0:
            num = 1.0
        elif i % 2 == 0:
            num = (m * (b - m) * x) / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            num = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + num * d
        d = 1e-30 if abs(d) < 1e-30 else d
        d = 1.0 / d
        c = 1.0 + num / c
        c = 1e-30 if abs(c) < 1e-30 else c
        f *= c * d
        if abs(1 - c * d) < 1e-10:
            break
    r = front * (f - 1)
    return r if x < (a + 1) / (a + b + 2) else 1 - _betainc(b, a, 1 - x)


def _lgamma(z):
    import math
    return math.lgamma(z)


def _welch_df(s1, n1, s2, n2):
    a, b = s1 ** 2 / n1, s2 ** 2 / n2
    return (a + b) ** 2 / (a ** 2 / (n1 - 1) + b ** 2 / (n2 - 1))


if __name__ == "__main__":
    main()
