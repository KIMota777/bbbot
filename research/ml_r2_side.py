# -*- coding: utf-8 -*-
u"""Из чего сделана прибавка: из отбора входов или из ставки на сторону.

ЗАЧЕМ ЭТО РЕШАЮЩЕ. Обучение и проверка (июнь 2023 — февраль 2026) пришлись
преимущественно на растущий рынок, а закрытый экзамен — на падающий. Модель,
которая всего лишь выучила «лонги лучше шортов», покажет ровно такую же
красивую прибавку вне обучения — и развернётся в минус ровно там, где её
собираются применять. Отличить одно от другого можно, не открывая экзамена.

РАЗЛОЖЕНИЕ. Прибавку можно точно разбить на два слагаемых:

   прибавка = (сдвиг доли сторон) + (отбор ВНУТРИ стороны)

   первое — насколько изменилась доля лонгов среди оставленных, умноженная на
            разницу средних доходностей сторон. Это ставка на направление
            рынка, и она живёт ровно до разворота;
   второе — насколько лучше стали входы ВНУТРИ лонгов и внутри шортов
            по отдельности. Это и есть отбор входов, ради которого всё
            затевалось; он не зависит от того, куда пойдёт рынок.

ПРОВЕРКА НА ПАДАЮЩЕМ РЫНКЕ БЕЗ ЭКЗАМЕНА. Внутри обучения и проверки хватает
падающих участков. Признак btc_above (цена BTC выше своей EMA200, известен на
баре входа) делит все внеобучающие сделки на «рынок растёт» и «рынок падает»,
и прибавка считается в каждой половине отдельно. Если она есть только там,
где рынок рос, — это ставка на рост, и на экзамене её ждёт то же, что ждало
всю систему.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import ml_r2_lib as L   # noqa: E402
import ml_r2_null as N  # noqa: E402
import ml_r2_pool as P  # noqa: E402

OUT = os.path.join(DIR, "out")

CASES = [
    (u"ансамбль 1ч+4ч", "pool", u"бустинг", u"|доход|"),
    (u"ансамбль 1ч+4ч", "pool", u"логрег", u"|доход|"),
    (u"только 4ч", "tf4", u"логрег", u"|доход|"),
    (u"только 4ч", "tf4", u"бустинг", u"|доход|"),
]


def decompose(ret, cfg, side, keep):
    u"""Точное разложение прибавки на «сдвиг сторон» и «отбор внутри стороны»."""
    mix, sel, wts = [], [], []
    for c in np.unique(cfg):
        s = cfg == c
        if s.sum() < 50 or (s & keep).sum() < 20:
            continue
        r, sd, k = ret[s], side[s], keep[s]
        m_all, m_keep, p_all, p_keep = {}, {}, {}, {}
        ok = True
        for v in (1, -1):
            g = (sd > 0) if v > 0 else (sd <= 0)
            if g.sum() < 10 or (g & k).sum() < 5:
                ok = False
                break
            m_all[v] = float(r[g].mean())
            m_keep[v] = float(r[g & k].mean())
            p_all[v] = float(g.mean())
            p_keep[v] = float((g & k).sum()) / max(int(k.sum()), 1)
        if not ok:
            continue
        mix.append(sum((p_keep[v] - p_all[v]) * m_all[v] for v in (1, -1)))
        sel.append(sum(p_keep[v] * (m_keep[v] - m_all[v]) for v in (1, -1)))
        wts.append(int(s.sum()))
    if not wts:
        return float("nan"), float("nan")
    return (float(np.average(mix, weights=wts)),
            float(np.average(sel, weights=wts)))


def main():
    d = L.pooled(P.RULES4)
    folds = P.purged_folds(d["t_in"], d["t_out"])
    m4 = np.array([c.endswith("|240") for c in d["cfg"]])
    d4 = {k: (v[m4] if isinstance(v, np.ndarray) else v) for k, v in d.items()}
    d4["names"] = d["names"]
    f4 = P.purged_folds(d4["t_in"], d4["t_out"], min_train=300)
    sets = {"pool": (d, folds), "tf4": (d4, f4)}
    j_above = d["names"].index("btc_above")

    out = []
    print(u"1. РАЗЛОЖЕНИЕ ПРИБАВКИ: СТАВКА НА СТОРОНУ ПРОТИВ ОТБОРА ВХОДОВ\n")
    print(u"   %-16s %-8s %-9s %9s %9s %9s %7s"
          % (u"выборка", u"модель", u"порог", u"прибавка", u"из них",
             u"из них", u"длинных"))
    print(u"   %-16s %-8s %-9s %9s %9s %9s %7s"
          % ("", "", "", u"всего", u"стороны", u"отбора", u"стало"))
    for tag, key, mk, wk in CASES:
        dd, ff = sets[key]
        km, kt, rr, cc, fid, sd = N.keep_masks(dd, ff, mk, wk, False)
        for ptag, keep in ((u"медиана", km), (u"верх 30%", kt)):
            real = N.dmean_in(rr, cc, keep)
            mix, sel = decompose(rr, cc, sd, keep)
            print(u"   %-16s %-8s %-9s %+8.4f%% %+8.4f%% %+8.4f%% %6.0f%%"
                  % (tag, mk, ptag, 100 * real, 100 * mix, 100 * sel,
                     100 * (sd[keep] > 0).mean()))
            out.append(dict(set=tag, model=mk, thr=ptag, real=real,
                            mix=mix, sel=sel,
                            long_keep=float((sd[keep] > 0).mean()),
                            long_all=float((sd > 0).mean())))
    v = np.array([r["mix"] for r in out])
    s = np.array([r["sel"] for r in out])
    print(u"\n   в среднем: ставка на сторону %+.4f%%, отбор входов %+.4f%%"
          % (100 * v.mean(), 100 * s.mean()))
    print(u"   доля прибавки, объяснённая одной лишь стороной: %.0f%%"
          % (100 * v.mean() / max(v.mean() + s.mean(), 1e-12)))

    print(u"\n\n2. ТА ЖЕ ПРИБАВКА ОТДЕЛЬНО НА РАСТУЩЕМ И ПАДАЮЩЕМ РЫНКЕ")
    print(u"   Деление по btc_above: цена BTC выше своей EMA200 на баре входа.")
    print(u"   Экзамен не открывается — падающие участки берутся из обучения")
    print(u"   и проверки, их там достаточно.\n")
    print(u"   %-16s %-8s %-9s | %7s %9s | %7s %9s"
          % (u"выборка", u"модель", u"порог", u"входов", u"рынок рос",
             u"входов", u"рынок падал"))
    for tag, key, mk, wk in CASES:
        dd, ff = sets[key]
        km, kt, rr, cc, fid, sd = N.keep_masks(dd, ff, mk, wk, False)
        te = np.concatenate([f["test"] for f in ff])
        above = dd["X"][te, j_above]
        for ptag, keep in ((u"медиана", km), (u"верх 30%", kt)):
            cells = []
            for g in (above > 0.5, above <= 0.5):
                g = g & np.isfinite(above)
                if g.sum() < 100:
                    cells.append((int(g.sum()), float("nan")))
                    continue
                cells.append((int(g.sum()),
                              N.dmean_in(rr[g], cc[g], keep[g])))
            print(u"   %-16s %-8s %-9s | %7d %+8.4f%% | %7d %+8.4f%%"
                  % (tag, mk, ptag, cells[0][0], 100 * cells[0][1],
                     cells[1][0], 100 * cells[1][1]))
            out.append(dict(set=tag, model=mk, thr=ptag,
                            up_n=cells[0][0], up=cells[0][1],
                            down_n=cells[1][0], down=cells[1][1]))
    up = np.array([r["up"] for r in out if "up" in r])
    dn = np.array([r["down"] for r in out if "down" in r])
    up, dn = up[np.isfinite(up)], dn[np.isfinite(dn)]
    print(u"\n   в среднем: на растущем %+.4f%%, на падающем %+.4f%%"
          % (100 * up.mean(), 100 * dn.mean()))
    print(u"   положительных на падающем: %d из %d"
          % (int((dn > 0).sum()), len(dn)))

    print(u"\n\n3. ТО ЖЕ ДЛЯ ОДНОМЕРНЫХ ФИЛЬТРОВ, ВЫДЕРЖАВШИХ ПРАВИЛЬНЫЙ НОЛЬ")
    print(u"   (ml_r2_block: streak, s_ema200_d, s_di_diff, xs_rv_med)")
    print(u"   «сторона» — сколько прибавки объясняется сдвигом доли лонгов.\n")
    print(u"   %-16s %-12s %9s %9s %9s | %9s %9s"
          % (u"выборка", u"признак", u"прибавка", u"стороны", u"отбора",
             u"рынок рос", u"рынок пал"))
    d10 = L.pooled(P.RULES10)
    f10 = P.purged_folds(d10["t_in"], d10["t_out"])
    sets2 = [(u"ансамбль 1ч+4ч", d, folds,
              ["streak", "perf_last", "s_ema200_d"]),
             (u"только 4ч", d4, f4, ["xs_rv_med", "streak"]),
             (u"семейства 1ч+4ч", d10, f10,
              ["streak", "s_di_diff", "xs_rv_med"])]
    import ml_r2_simple as S
    for tag, dd, ff, feats in sets2:
        te = np.concatenate([f["test"] for f in ff])
        above = dd["X"][te, j_above]
        side = dd["X"][te, dd["names"].index("side")]
        for fname in feats:
            r = S.one_feature(dd, ff, dd["names"].index(fname))
            if r is None:
                continue
            keep, rr, cc = r
            real = N.dmean_in(rr, cc, keep)
            mix, sel = decompose(rr, cc, side, keep)
            cells = []
            for g in (above > 0.5, above <= 0.5):
                g = g & np.isfinite(above)
                cells.append(N.dmean_in(rr[g], cc[g], keep[g])
                             if g.sum() >= 100 else float("nan"))
            print(u"   %-16s %-12s %+8.4f%% %+8.4f%% %+8.4f%% | %+8.4f%% "
                  u"%+8.4f%%" % (tag, fname, 100 * real, 100 * mix,
                                 100 * sel, 100 * cells[0], 100 * cells[1]))
            out.append(dict(set=tag, feat=fname, real=real, mix=mix, sel=sel,
                            up=cells[0], down=cells[1]))

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "ml_r2_side.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, default=float)
    print(u"\nсохранено -> out/ml_r2_side.json")


if __name__ == "__main__":
    main()
