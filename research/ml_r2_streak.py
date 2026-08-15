# -*- coding: utf-8 -*-
u"""Единственное, что выдержало всё: «серия» — собственная удачливость правила.

ЧТО ВЫЖИЛО. Из 117 проверенных одномерных фильтров (39 признаков на трёх
выборках) правильный ноль со сдвигом заодно и поправкой на число попыток
выдержали четыре. Три из них — про направление рынка или его волатильность
(s_ema200_d, s_di_diff, xs_rv_med), и первый же из них работает только на
растущем рынке, то есть повторяет судьбу всей системы. Четвёртый ведёт себя
иначе: `streak` — длина текущей серии выигрышей или проигрышей САМОГО ПРАВИЛА
перед этим входом. Он повторился на двух НЕПЕРЕСЕКАЮЩИХСЯ наборах правил и
работает и на растущем, и на падающем рынке.

Это ровно тот случай, ради которого работа и делалась, поэтому здесь он
проверяется отдельно и жёстче всего.

  1. НЕ ЗНАЕТ ЛИ ПРИЗНАК БУДУЩЕГО. Признак считается из исходов ПРЕДЫДУЩИХ
     сделок того же правила. Такие признаки — обычное место утечки: достаточно
     включить сделку, которая на момент входа ещё открыта, и её исход утечёт
     в признак. Проверка механическая: исходы всех сделок, закрывшихся после
     среза, заменяются случайными; ни одно значение признака до среза не имеет
     права измениться.

  2. КУДА СМОТРИТ ФИЛЬТР. Обучение само выбирает сторону порога. Печатается,
     какую именно: «после выигрышей» или «после проигрышей», и одинаково ли
     это в разных окнах.

  3. ЕСТЬ ЛИ УПОРЯДОЧИВАНИЕ. Средняя доходность входа по корзинам длины серии.
     Настоящая зависимость обязана быть монотонной, а не сидеть в одной корзине.

  4. ЖИВЁТ ЛИ ОНА НА ДРУГИХ СОЧЕТАНИЯХ ПАРАМЕТРОВ И НА ДЕНЬГАХ. Пять сочетаний
     параметров правил (срединное и четыре случайных) и месячная доходность
     4ч-среза с фильтром и без.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import ml_lib          # noqa: E402
import ml_r2_block as B  # noqa: E402
import ml_r2_lib as L   # noqa: E402
import ml_r2_null as N  # noqa: E402
import ml_r2_pool as P  # noqa: E402
import rdata            # noqa: E402

OUT = os.path.join(DIR, "out")


def assert_perf_causal(seed=0):
    u"""Порча исходов будущих сделок: признак до среза обязан не измениться."""
    t0, t1 = rdata.SPLITS["trainval"]
    bad = []
    for name in P.RULES4:
        p = ml_lib.center_combo(ml_lib.registry()[name])
        r = L.rule_trades2(name, p, "ETHUSDT", "240", t0, t1)
        if r is None:
            continue
        _, _, t_in, t_out, ret = r
        bar = rdata.load_bars("ETHUSDT", "240").bar_ms()
        A = L.perf_cols(t_in, t_out, ret, bar)
        order = np.argsort(t_out, kind="stable")
        k = int(len(order) * 0.6)
        rng = np.random.default_rng(seed)
        ret2 = ret.copy()
        ret2[order[k:]] = rng.normal(0, 0.05, len(order) - k)
        Bm = L.perf_cols(t_in, t_out, ret2, bar)
        # строка j опирается только на сделки, закрытые раньше t_in[j]-бар
        pos = np.searchsorted(np.sort(t_out), t_in - bar, "left")
        safe = pos <= k
        d = ~np.isclose(A[safe], Bm[safe], rtol=1e-9, atol=1e-12,
                        equal_nan=True)
        if d.any():
            bad.append("%s: %d значений" % (name, int(d.sum())))
    if bad:
        raise AssertionError(u"ПРИЗНАК СЕРИИ ЗНАЕТ БУДУЩЕЕ -> " +
                             "; ".join(bad))
    return True


def direction(d, folds, j):
    u"""Какую сторону порога выбрало обучение в каждом окне."""
    out = []
    X, ret = d["X"], d["ret"]
    for f in folds:
        tr = f["train"]
        v = X[tr, j]
        ok = np.isfinite(v)
        thr = float(np.median(v[ok]))
        hi = v >= thr
        m_hi = ret[tr][hi & ok].mean()
        m_lo = ret[tr][(~hi) & ok].mean()
        out.append((thr, u"после выигрышей" if m_hi >= m_lo
                    else u"после проигрышей", float(m_hi), float(m_lo)))
    return out


def main():
    print(u"1. ПРИЗНАК СЕРИИ НЕ ЗНАЕТ БУДУЩЕГО")
    assert_perf_causal()
    print(u"   порча исходов будущих сделок: значения до среза не "
          u"изменились ни разу\n")

    d = L.pooled(P.RULES4)
    folds = P.purged_folds(d["t_in"], d["t_out"])
    m4 = np.array([c.endswith("|240") for c in d["cfg"]])
    d4 = {k: (v[m4] if isinstance(v, np.ndarray) else v) for k, v in d.items()}
    d4["names"] = d["names"]
    f4 = P.purged_folds(d4["t_in"], d4["t_out"], min_train=300)
    d10 = L.pooled(P.RULES10)
    f10 = P.purged_folds(d10["t_in"], d10["t_out"])
    j = d["names"].index("streak")

    print(u"2. КУДА СМОТРИТ ФИЛЬТР В КАЖДОМ ОКНЕ")
    for tag, dd, ff in ((u"ансамбль 1ч+4ч", d, folds), (u"только 4ч", d4, f4),
                        (u"семейства 1ч+4ч", d10, f10)):
        dirs = direction(dd, ff, j)
        same = len({x[1] for x in dirs}) == 1
        print(u"   %-16s %s" % (tag, ", ".join(x[1] for x in dirs)))
        print(u"   %-16s порог по окнам: %s   %s"
              % ("", ", ".join("%+.1f" % x[0] for x in dirs),
                 u"(во всех окнах одинаково)" if same
                 else u"(В РАЗНЫХ ОКНАХ ПО-РАЗНОМУ — это шум)"))

    print(u"\n3. СРЕДНЯЯ ДОХОДНОСТЬ ВХОДА ПО ДЛИНЕ СЕРИИ (вне обучения)")
    print(u"   Серия — сколько подряд выигрышных (+) или проигрышных (-)")
    print(u"   сделок было у этого правила ПЕРЕД этим входом.\n")
    print(u"   %-16s %9s %9s %9s %9s %9s"
          % (u"выборка", u"<= -3", u"-2..-1", u"+1..+2", u">= +3", u"связь"))
    for tag, dd, ff in ((u"ансамбль 1ч+4ч", d, folds), (u"только 4ч", d4, f4),
                        (u"семейства 1ч+4ч", d10, f10)):
        te = np.concatenate([f["test"] for f in ff])
        v, r = dd["X"][te, j], dd["ret"][te]
        cells = []
        for lo, hi in ((-99, -2.5), (-2.5, 0), (0, 2.5), (2.5, 99)):
            s = np.isfinite(v) & (v > lo) & (v <= hi)
            cells.append(float(r[s].mean()) if s.sum() > 30 else float("nan"))
        ok = np.isfinite(v)
        ic = float(np.corrcoef(np.argsort(np.argsort(v[ok])),
                               np.argsort(np.argsort(r[ok])))[0, 1])
        print(u"   %-16s %+8.4f%% %+8.4f%% %+8.4f%% %+8.4f%% %+9.3f"
              % (tag, 100 * cells[0], 100 * cells[1], 100 * cells[2],
                 100 * cells[3], ic))

    print(u"\n4. ЖИВЁТ ЛИ НА ДРУГИХ СОЧЕТАНИЯХ ПАРАМЕТРОВ ПРАВИЛ (4ч)")
    print(u"   %-14s %7s %10s %10s %10s"
          % (u"сочетание", u"входов", u"прибавка", u"без,%мес", u"с,%мес"))
    vals = []
    for tag in (u"срединное", u"случайное 1", u"случайное 2", u"случайное 3",
                u"случайное 4"):
        combo = "center" if tag == u"срединное" else int(tag.split()[-1])
        dc = L.pooled(P.RULES4, tfs=("240",), combo=combo)
        if dc is None or len(dc["y"]) < 400:
            continue
        fc = P.purged_folds(dc["t_in"], dc["t_out"], min_train=300)
        import ml_r2_simple as S
        r = S.one_feature(dc, fc, dc["names"].index("streak"))
        if r is None:
            continue
        keep, ret, cfg = r
        te = np.concatenate([f["test"] for f in fc])
        ti, to = dc["t_in"][te], dc["t_out"][te]
        gain = N.dmean_in(ret, cfg, keep)
        vals.append(gain)
        print(u"   %-14s %7d %+9.4f%% %+9.2f%% %+9.2f%%"
              % (tag, len(dc["y"]), 100 * gain,
                 100 * ml_lib.monthly_rate(ret, ti, to),
                 100 * ml_lib.monthly_rate(ret[keep], ti[keep], to[keep])))
    v = np.array(vals)
    print(u"   положительных %d из %d, среднее %+.4f%% ± %.4f%%"
          % (int((v > 0).sum()), len(v), 100 * v.mean(),
             100 * v.std(ddof=1) / np.sqrt(len(v))))

    print(u"\n5. ПРАВИЛЬНЫЙ НОЛЬ ДЛЯ КАЖДОЙ ВЫБОРКИ ОТДЕЛЬНО")
    print(u"   (сдвиг заодно; поправка на 117 проверенных фильтров)\n")
    res = []
    for tag, dd, ff in ((u"ансамбль 1ч+4ч", d, folds), (u"только 4ч", d4, f4),
                        (u"семейства 1ч+4ч", d10, f10)):
        import ml_r2_simple as S
        r = S.one_feature(dd, ff, dd["names"].index("streak"))
        keep, ret, cfg = r
        res.append(B.report(u"streak / %s" % tag, ret, cfg, keep, 117))

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "ml_r2_streak.json"), "w",
              encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, default=float)
    print(u"\nсохранено -> out/ml_r2_streak.json")


if __name__ == "__main__":
    main()
