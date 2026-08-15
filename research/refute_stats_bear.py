# -*- coding: utf-8 -*-
"""ЭКЗАМЕН ИЛИ МЕДВЕДЬ: что именно провалилось на 169 днях.

Автор объясняет провал экзамена тем, что отбор не предсказывает будущее.
Есть другое объяснение, которое он назвал, но не проверил числом: экзамен
пришёлся на падающий рынок, а обучение — на растущий.

Эти два объяснения различимы. Внутри обучения+проверки есть свои падающие
куски. Если те же самые кандидаты теряли и на них, то провал экзамена — это
свойство РЫНОЧНОГО РЕЖИМА, а не свойство отбора, и «связь -0.17» тут ни при
чём: она измеряет не то.

КАК СЧИТАЕТСЯ. По обучающе-проверочной части катится окно ровно той же длины,
что экзамен — 169 дней, шаг 7 дней. На каждом окне считаются:
    * доходность кандидата в месяц (сделки уже внеобучающие: параметры на
      каждом куске выбраны только по прошлому, это кэш refute_stats_cache);
    * поведение самого рынка — доходность BTC «купил и держи» за то же окно.

Получается распределение «что показал бы экзамен, если бы его назначили на
другую дату». С ним и сравнивается настоящий экзамен.

ПРО ЗАПРЕТ. Экзаменационные сделки здесь не пересчитываются: берутся уже
опубликованные автором числа из out/post_mortem.json и out/blind_test.json.
Доходность BTC за экзаменационный период считается один раз и используется
ТОЛЬКО как описание рынка — по ней ничего не выбирается.
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
WIN_DAYS = 169          # ровно длина экзамена
STEP_DAYS = 7
RISK_K = 0.43           # доля риска финальной конфигурации


def btc_window(t0, t1):
    """Что делал сам рынок на окне: доходность BTC и его просадка."""
    b = rdata.load_bars("BTCUSDT", "240")
    m = (b.t >= t0) & (b.t < t1)
    if m.sum() < 10:
        return None
    c = b.c[m]
    peak = np.maximum.accumulate(c)
    dd = float(((peak - c) / peak).max())
    return dict(ret=float(c[-1] / c[0] - 1.0), dd=dd)


def window_metrics(trades, t0, t1, risk_each):
    sel = {}
    for sym, trs in trades.items():
        s = [t for t in trs if t0 <= t["t_in"] < t1]
        if s:
            sel[sym] = s
    if not sel or sum(len(v) for v in sel.values()) < 10:
        return None
    c = portfolio.combine(sel, risk_each=risk_each)
    days = max((t1 - t0) / DAY, 1.0)
    ret = c["ret"]
    return dict(mo=float((1 + ret) ** (30.0 / days) - 1.0) if ret > -1 else -1.0,
                dd=c["maxdd"], trades=c["trades"])


def main():
    print("=" * 78)
    print("ЭКЗАМЕН ИЛИ МЕДВЕДЬ: 169-ДНЕВНЫЕ ОКНА ВНУТРИ ОБУЧЕНИЯ")
    print("=" * 78)

    raw = C.load()
    by_name = C.as_trades(raw)
    ens = frontier.merge([by_name[n] for n in C.ENSEMBLE if n in by_name])

    # границы, где вообще есть внеобучающие сделки
    all_t = [t["t_in"] for trs in ens.values() for t in trs]
    t_lo, t_hi = min(all_t), max(all_t)
    print("внеобучающие сделки обучения+проверки: %.0f дней"
          % ((t_hi - t_lo) / DAY))

    wins = []
    s = t_lo
    while s + WIN_DAYS * DAY <= t_hi:
        e = s + WIN_DAYS * DAY
        m = window_metrics(ens, s, e, RISK_K)
        bt = btc_window(s, e)
        if m and bt:
            wins.append(dict(t0=s, t1=e, mo=m["mo"], dd=m["dd"],
                             trades=m["trades"], btc=bt["ret"], btc_dd=bt["dd"]))
        s += STEP_DAYS * DAY
    print("окон длиной %d дней с шагом %d: %d\n" % (WIN_DAYS, STEP_DAYS, len(wins)))

    # --- настоящий экзамен: числа автора, ничего не пересчитывается --------
    with open(os.path.join(OUT, "blind_test.json"), encoding="utf-8") as fh:
        bt_json = json.load(fh)
    te = bt_json["result"]["test"]
    ex_mo = float(te["mo"])
    ex_dd = float(te["maxdd"])
    te0, te1 = rdata.SPLITS["test"]
    ex_btc = btc_window(te0, te1)

    print("РЫНОК НА ЭКЗАМЕНЕ (описание, по нему ничего не выбирается)")
    print("   BTC за 169 дней экзамена: %+.1f%%, просадка внутри окна %.1f%%"
          % (100 * ex_btc["ret"], 100 * ex_btc["dd"]))
    b = np.array([w["btc"] for w in wins])
    print("   BTC на окнах обучения: медиана %+.1f%%, худшее %+.1f%%, "
          "лучшее %+.1f%%" % (100 * np.median(b), 100 * b.min(), 100 * b.max()))
    q = float((b <= ex_btc["ret"]).mean())
    print("   экзаменационный рынок хуже %.0f%% окон обучения" % (100 * (1 - q)))
    if q < 0.1:
        print("   ВАЖНО: такого рынка в обучении практически НЕ БЫЛО.")
        print("   Значит экзамен проверял стратегию на режиме, которого она")
        print("   ни разу не видела при отборе. Это не проверка отбора.")

    # --- зависимость ансамбля от рынка -------------------------------------
    mo = np.array([w["mo"] for w in wins])
    print("\nАНСАМБЛЬ НА 169-ДНЕВНЫХ ОКНАХ ОБУЧЕНИЯ (риск %.2f, как в спеке)"
          % RISK_K)
    print("   в месяц: медиана %+.2f%%, худшее %+.2f%%, лучшее %+.2f%%"
          % (100 * np.median(mo), 100 * mo.min(), 100 * mo.max()))
    print("   окон в минусе: %d из %d (%.0f%%)"
          % (int((mo < 0).sum()), len(mo), 100 * (mo < 0).mean()))
    print("   настоящий экзамен: %+.2f%% в месяц — это %.0f-й перцентиль"
          % (100 * ex_mo, 100 * (mo <= ex_mo).mean()))
    n_indep = ((t_hi - t_lo) / DAY) / WIN_DAYS
    print("   ВАЖНАЯ ОГОВОРКА: эти %d окон перекрываются. Непересекающихся"
          % len(mo))
    print("   окон длиной %d дней в обучении помещается всего %.1f — то есть"
          % (WIN_DAYS, n_indep))
    print("   «худшее из 53» на деле означает «худшее из %.0f независимых»."
          % n_indep)
    r_mkt = float(np.corrcoef(b, mo)[0][1])
    print("\n   связь «доходность BTC за окно» -> «доходность ансамбля»: %+.2f"
          % r_mkt)
    k = np.polyfit(b, mo, 1)
    print("   наклон %.2f: каждые 10%% движения BTC двигают месяц ансамбля"
          % k[0])
    print("   на %.2f п.п. Ансамбль НЕ нейтрален к рынку." % (10 * k[0]))
    pred = k[0] * ex_btc["ret"] + k[1]
    print("   при рынке экзамена (%+.1f%% BTC) этот наклон предсказывает"
          % (100 * ex_btc["ret"]))
    print("   %+.2f%% в месяц. Фактически вышло %+.2f%%."
          % (100 * pred, 100 * ex_mo))
    print("   То есть %.0f%% провала объясняется одним рынком, без всякого"
          % (100 * min(1.0, max(0.0, (np.median(mo) - pred) /
                                max(np.median(mo) - ex_mo, 1e-9)))))
    print("   разговора о том, работает отбор или нет.")

    # --- худшие окна: что было бы, назначь экзамен на них -------------------
    order = np.argsort(b)
    print("\nПЯТЬ САМЫХ МЕДВЕЖЬИХ ОКОН ВНУТРИ ОБУЧЕНИЯ")
    print("   %-24s %8s %10s %10s" % ("окно", "BTC", "ансамбль", "просадка"))
    import datetime as dt

    def d(ms):
        return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d")
    worst = []
    for i in order[:5]:
        w = wins[i]
        worst.append(w)
        print("   %-24s %+7.1f%% %+9.2f%% %9.1f%%"
              % ("%s..%s" % (d(w["t0"]), d(w["t1"])), 100 * w["btc"],
                 100 * w["mo"], 100 * w["dd"]))
    print("   %-24s %+7.1f%% %+9.2f%% %9.1f%%   <- НАСТОЯЩИЙ ЭКЗАМЕН"
          % ("%s..%s" % (d(te0), d(te1)), 100 * ex_btc["ret"],
             100 * ex_mo, 100 * ex_dd))
    wm = np.array([w["mo"] for w in worst])
    print("\n   на медвежьих окнах ОБУЧЕНИЯ ансамбль давал медиану %+.2f%%/мес"
          % (100 * np.median(wm)))
    print("   на медвежьем ЭКЗАМЕНЕ он дал %+.2f%%/мес — разница %.2f п.п."
          % (100 * ex_mo, 100 * (ex_mo - np.median(wm))))

    # --- то же по каждому кандидату ----------------------------------------
    print("\nКАЖДЫЙ КАНДИДАТ: медвежьи окна обучения против экзамена")
    print("   Медвежье окно = 169 дней, где BTC в минусе. Риск 1% на рукав,")
    print("   как в post_mortem автора.")
    with open(os.path.join(OUT, "post_mortem.json"), encoding="utf-8") as fh:
        pm = {r["name"]: r for r in json.load(fh)}
    bear_idx = [i for i, w in enumerate(wins) if w["btc"] < 0]
    print("   медвежьих окон внутри обучения: %d из %d"
          % (len(bear_idx), len(wins)))
    rows = []
    print("\n   %-18s %10s %10s %10s %9s" % ("кандидат", "всё обучение",
                                             "медв.окна", "ЭКЗАМЕН",
                                             "разница"))
    for name in C.NAMES:
        if name not in by_name or name not in pm:
            continue
        tr = by_name[name]
        allm = window_metrics(tr, wins[0]["t0"], wins[-1]["t1"], 1.0)
        bear = []
        for i in bear_idx:
            m = window_metrics(tr, wins[i]["t0"], wins[i]["t1"], 1.0)
            if m:
                bear.append(m["mo"])
        if not bear:
            continue
        bm = float(np.median(bear))
        ex = pm[name]["test"]["mo"]
        rows.append(dict(name=name, all=allm["mo"] if allm else 0.0,
                         bear=bm, exam=ex))
        print("   %-18s %+9.2f%% %+9.2f%% %+9.2f%% %+8.2f п.п."
              % (name, 100 * (allm["mo"] if allm else 0), 100 * bm,
                 100 * ex, 100 * (ex - bm)))

    ba = np.array([r["bear"] for r in rows])
    ea = np.array([r["exam"] for r in rows])
    aa = np.array([r["all"] for r in rows])
    print("\n   медиана по 20 кандидатам: всё обучение %+.2f%%, медвежьи окна"
          % (100 * np.median(aa)))
    print("   %+.2f%%, экзамен %+.2f%%" % (100 * np.median(ba), 100 * np.median(ea)))
    print("   в плюсе: всё обучение %d, медвежьи окна %d, экзамен %d (из %d)"
          % (int((aa > 0).sum()), int((ba > 0).sum()), int((ea > 0).sum()),
             len(rows)))

    r_bear_exam = float(np.corrcoef(ba, ea)[0][1])
    r_all_exam = float(np.corrcoef(aa, ea)[0][1])
    print("\n   СВЯЗЬ С ЭКЗАМЕНОМ:")
    print("      по всему обучению целиком: %+.2f  (это и есть число автора)"
          % r_all_exam)
    print("      по МЕДВЕЖЬИМ окнам обучения: %+.2f" % r_bear_exam)
    if r_bear_exam > r_all_exam + 0.15:
        print("      Связь заметно ВЫШЕ, если сравнивать сопоставимые режимы.")
        print("      Отбор не «не предсказывает будущее» — он предсказывает")
        print("      поведение в ТОМ ЖЕ режиме, а экзамен был в другом.")
        print("      ЧЕСТНАЯ ОГОВОРКА: медвежьих окон внутри обучения %d, и"
              % len(bear_idx))
        print("      они перекрываются — это фактически ОДИН отрезок рынка.")
        print("      Поэтому +%.2f это подсказка, а не доказательство:"
              % r_bear_exam)
        print("      при 20 точках ошибка связи 0.24, и обе величины (%+.2f и"
              % r_all_exam)
        print("      %+.2f) лежат внутри общего интервала неопределённости."
              % r_bear_exam)
    else:
        print("      Заметной разницы нет: сопоставимость режимов связь не")
        print("      спасает.")

    out = dict(win_days=WIN_DAYS, n_windows=len(wins),
               ens_mo_med=float(np.median(mo)), ens_mo_min=float(mo.min()),
               ens_mo_max=float(mo.max()), exam_mo=ex_mo,
               exam_pctile=float((mo <= ex_mo).mean()),
               btc_exam=ex_btc["ret"], btc_med=float(np.median(b)),
               btc_min=float(b.min()), corr_mkt=r_mkt, slope=float(k[0]),
               pred_exam=float(pred), r_all_exam=r_all_exam,
               r_bear_exam=r_bear_exam, per_name=rows,
               windows=[dict(t0=w["t0"], t1=w["t1"], mo=w["mo"], btc=w["btc"])
                        for w in wins])
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "refute_stats_bear.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, default=float)
    print("\nсохранено -> out/refute_stats_bear.json")


if __name__ == "__main__":
    main()
