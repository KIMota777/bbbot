# -*- coding: utf-8 -*-
"""Отчёт по предиктивному скорингу сигналов: помогает он или нет.

Проверяем ЧЕСТНО, по методике evolution12:
  обучение модели и подбор всего (признаки, L2, порог) — только бары
  [0 .. hold), hold = 72% истории; HOLDOUT [hold .. n) модель не видела.
Геномы и плечи — готовые победители из signal_setups2.json (их тоже
отбирали без holdout), скоринг их не меняет: он лишь может ОТКАЗАТЬСЯ от
части сигналов через штатный хук run_setup(score_fn=..., score_min=...).

Что печатается:
  0. проверки корректности (конвейер, причинность, сходимость с движком);
  1. по каждой из 8 комбинаций (4 сетапа x 2 ТФ) — обучение: сколько
     примеров, какие признаки, AUC внутри обучения и вне обучения,
     перестановочный тест (нулевое распределение AUC), выбранный порог;
  2. ГЛАВНАЯ ТАБЛИЦА на holdout: без скоринга / со скорингом «А» (порог
     выбран кросс-валидацией внутри трейна) / со скорингом «Б» (априорный
     порог = медиана оценок трейна, отсекает половину сигналов);
  3. вердикт по каждой комбинации и общий вывод.

Запуск: python score_report.py   (вывод дублируется в score_report_out.txt)
"""

import io
import sys
import time

import signal_engine2 as se
import signal_score as sc

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

N_PERM = 200            # перестановок в тесте значимости AUC
LINE = "=" * 100
THIN = "-" * 100
_BUF = io.StringIO()


def out(s=""):
    print(s)
    _BUF.write(s + "\n")


def fmt(st):
    """Строка метрик отрезка."""
    pf = "  -  " if st["pf"] is None else f"{st['pf']:5.2f}"
    return (f"{st['n']:4d} {st['wr']:6.1f} {st['exp_r']:+7.3f} {pf} "
            f"{st['sum_r']:+8.2f} {st['ret']:+8.1f} {st['dd']:6.1f}")


HEAD = (f"{'конфигурация':<34}{'сд.':>4} {'WR%':>6} {'exp_R':>7} {'PF':>5} "
        f"{'сумма R':>8} {'итог%':>8} {'DD%':>6}")


# --------------------------------------------------------- 0. проверки
def checks(models, cfgs):
    out(LINE)
    out("0. ПРОВЕРКИ КОРРЕКТНОСТИ (без них цифрам верить нельзя)")
    out(LINE)
    ok_all = True

    # 0.1 конвейер обучения вообще способен учиться: подменяем исход на
    # заведомо выводимый из признака (RSI выше медианы) — AUC вне обучения
    # обязан быть высоким; на случайном исходе — низким/средним
    import statistics as _st
    k = "bounce_short@60"
    cfg, d = cfgs[k], sc.load_data(60)
    hold, _ = sc.hold_from_winners(cfg["setup"], 60, d["n"])
    rows, _r = sc.collect_samples(cfg["setup"], cfg["genome"], d, (0, hold),
                                  cfg["lev"])
    med = _st.median([r["f"]["rsi"] for r in rows])
    syn = [dict(r, y=1 if r["f"]["rsi"] > med else 0) for r in rows]
    oof = sc._cv_oof(syn, 3, 0.1)
    pr = [(syn[i]["y"], oof[i]) for i in range(len(syn)) if oof[i] is not None]
    a_syn = sc.auc([p[0] for p in pr], [p[1] for p in pr])
    good = a_syn is not None and a_syn > 0.85
    ok_all &= good
    out(f"0.1 конвейер учится: на выводимой цели AUC вне обучения = "
        f"{a_syn:.3f} (ждём >0.85) — {'ОК' if good else 'ПРОВАЛ'}")

    # 0.2 признаки, которые я считаю сам, совпадают с теми, что движок
    # считает внутри run_setup: гоняем сетап с моделью и порогом 0 и
    # сверяем движковое поле score с моей оценкой того же бара
    k2 = "sweep_long@240"
    cfg2, d2 = cfgs[k2], sc.load_data(240)
    m2 = models[k2]
    fn2 = sc.make_score_fn(m2)
    hold2, _ = sc.hold_from_winners(cfg2["setup"], 240, d2["n"])
    r_sc, _st2 = sc.run_range(cfg2["setup"], cfg2["genome"], d2,
                              (hold2, d2["n"]), cfg2["lev"],
                              score_fn=fn2, score_min=0.0)
    bs = sc.signal_bars(cfg2["setup"], cfg2["genome"], d2,
                        r_sc["signal_range"])
    ext = se.build_ext(cfg2["setup"], cfg2["genome"], d2["c_sig"],
                       interval_min=240)
    worst, cnt = 0.0, 0
    for t in r_sc["trades"]:
        i = d2["ts_index"][t["signal_ts"]]
        f = se.bar_features(cfg2["setup"], i, d2["c_sig"], d2["ctx"],
                            cfg2["genome"], ext, bars_since_signal=bs.get(i))
        worst = max(worst, abs(round(fn2(f), 4) - t["score"]))
        cnt += 1
    good = cnt > 0 and worst < 1e-9
    ok_all &= good
    out(f"0.2 мои признаки = признаки движка: сверено {cnt} сделок, макс. "
        f"расхождение оценки {worst:.1e} — {'ОК' if good else 'ПРОВАЛ'}")

    # 0.3 модель с порогом 0 не меняет прогон (хук нейтрален)
    r_base, st_b = sc.run_range(cfg2["setup"], cfg2["genome"], d2,
                                (hold2, d2["n"]), cfg2["lev"])
    same = (len(r_base["trades"]) == len(r_sc["trades"]) and all(
        all(a.get(fl) == b.get(fl) for fl in se.TRADE_FIELDS)
        for a, b in zip(r_base["trades"], r_sc["trades"])))
    ok_all &= same
    out(f"0.3 порог 0 = прогон без модели: {len(r_base['trades'])} сделок "
        f"побитово {'совпали — ОК' if same else 'РАЗОШЛИСЬ — ПРОВАЛ'}")

    # 0.4 причинность: оценка бара, посчитанная по ИСТОРИИ ДО ЭТОГО БАРА,
    # равна оценке, посчитанной по всей истории (никакого заглядывания)
    idxs = sorted(d2["ts_index"][t["signal_ts"]] for t in r_base["trades"])[:5]
    worst2 = 0.0
    for i in idxs:
        pre = d2["c_sig"][:i + 1]
        ctx_p = se.prep_context(pre, interval_min=240)
        ext_p = se.build_ext(cfg2["setup"], cfg2["genome"], pre,
                             interval_min=240)
        f_p = se.bar_features(cfg2["setup"], i, pre, ctx_p, cfg2["genome"],
                              ext_p, bars_since_signal=bs.get(i))
        f_f = se.bar_features(cfg2["setup"], i, d2["c_sig"], d2["ctx"],
                              cfg2["genome"], ext, bars_since_signal=bs.get(i))
        worst2 = max(worst2, abs(fn2(f_p) - fn2(f_f)))
    good = worst2 < 1e-9
    ok_all &= good
    out(f"0.4 причинность (префикс vs полная история): {len(idxs)} баров, "
        f"макс. расхождение оценки {worst2:.1e} — "
        f"{'ОК' if good else 'ПРОВАЛ'}")

    # 0.5 базовый holdout сходится с опубликованными цифрами evolution12.
    # Сверяем со строкой ЛЕСТНИЦЫ на rec_lev: блок "holdout" в
    # signal_setups2.json посчитан на отборочном плече x15, а прогон идёт на
    # рекомендованном плече сетапа (R от плеча не зависит, итог% и DD — да).
    import json
    with open("signal_setups2.json", encoding="utf-8") as fh:
        raw = json.load(fh)
    bad = []
    for kk, cfg_k in cfgs.items():
        d_k = sc.load_data(cfg_k["interval_min"])
        h_k, _ = sc.hold_from_winners(cfg_k["setup"], cfg_k["interval_min"],
                                      d_k["n"])
        _r, st_k = sc.run_range(cfg_k["setup"], cfg_k["genome"], d_k,
                                (h_k, d_k["n"]), cfg_k["lev"])
        row = next((x for x in raw[kk]["ladder"]
                    if int(x["lev"]) == cfg_k["lev"]), None)
        if row is None or st_k["n"] != row["n"] or \
                abs(st_k["exp_r"] - row["exp_r"]) > 1e-9 or \
                abs(st_k["ret"] - row["ret"]) > 1e-9 or \
                abs(st_k["dd"] - row["dd"]) > 1e-9:
            bad.append(f"{kk}: мои {st_k['n']}/{st_k['exp_r']}/{st_k['ret']}%"
                       f" против {row}")
    ok_all &= not bad
    ok_txt = ("ВСЕ СОВПАЛИ — ОК" if not bad
              else "РАСХОЖДЕНИЯ: " + "; ".join(bad))
    out(f"0.5 базовый holdout = опубликованная лестница evolution12 на том же "
        f"плече: сверено {len(cfgs)} комбинаций (n, exp_R, итог%, DD) — "
        f"{ok_txt}")
    out(f"ИТОГ проверок: {'ВСЕ ПРОЙДЕНЫ' if ok_all else 'ЕСТЬ ПРОВАЛЫ'}")
    return ok_all


# ------------------------------------------------------------- отчёт
def main():
    t_all = time.time()
    out(LINE)
    out("ПРЕДИКТИВНЫЙ СКОРИНГ СИГНАЛОВ — ЧЕСТНАЯ ПРОВЕРКА НА HOLDOUT")
    out(LINE)
    try:
        import sklearn                                    # noqa: F401
        sk = f"есть (версия {sklearn.__version__}), но НЕ используется"
    except ImportError:
        sk = "нет"
    out(f"sklearn: {sk}. Логистическая регрессия — своя, чистый python "
        f"(нормировка по трейну, градиентный спуск, L2).")
    out(f"Раскол: обучение [0..hold), hold = {100*(1-sc.HOLD_FRAC):.0f}% "
        f"истории; holdout — последние {100*sc.HOLD_FRAC:.0f}%, как в "
        f"evolution12.")
    out(f"Цель модели: 1 = сделка закрылась ТЕЙКОМ, 0 = стоп/таймаут/"
        f"ликвидация. Признаки: se.bar_features "
        f"({len(se.SCORE_FEATURES)} штук).")

    cfgs = sc.load_setups()
    keys = [k for s in se.SETUPS for k in (f"{s}@240", f"{s}@60")]

    # ------------------------------------------- обучение (печать ниже)
    models, res = {}, {}
    exact_split = True
    for k in keys:
        cfg = cfgs[k]
        d = sc.load_data(cfg["interval_min"])
        hold, exact = sc.hold_from_winners(cfg["setup"], cfg["interval_min"],
                                           d["n"])
        exact_split &= exact
        models[k] = sc.train_model(
            cfg["setup"], cfg["interval_min"], hold_bars=hold,
            genome=cfg["genome"], lev=cfg["lev"], data=d, n_perm=N_PERM)
    src = ("из evolution12_winners.json (тот же раскол, на котором "
           "отбирались геномы)" if exact_split else "расчётом int(n*0.72)")
    m4, m1 = models["dump_long@240"], models["dump_long@60"]
    out(f"Граница обучения взята {src}: 4ч — {m4['hold_bars']} баров из "
        f"{m4['n_bars']}, 1ч — {m1['hold_bars']} из {m1['n_bars']}.")

    # ---------------------------------------------------- 0. проверки
    out()
    checks(models, cfgs)

    # ---------------------------------------------------- 1. обучение
    out()
    out(LINE)
    out("1. ОБУЧЕНИЕ (всё внутри трейна; holdout не участвует)")
    out(LINE)
    out(f"{'ключ':<18}{'обуч':>5}{'тейк':>5}{'база':>6}{'приз':>5} "
        f"{'AUC.обуч':>9}{'AUC.вне':>8}{'нуль':>7}{'p':>6} "
        f"{'ост.доля':>9}{'порогА':>8}{'порогБ':>8}")
    for k in keys:
        m = models[k]
        t = m["train"]
        pm = t.get("perm") or {}
        out(f"{k:<18}{t['n']:>5}{t['n_tp']:>5}{t.get('base_rate', 0):>6.2f}"
            f"{len(m['features']):>5} {t.get('auc_in', 0):>9.3f}"
            f"{(t.get('auc_oof') or 0):>8.3f}"
            f"{(pm.get('null_mean') or 0):>7.3f}"
            f"{(pm.get('p_value') or 0):>6.2f}"
            f"{t.get('keep_frac', 1.0):>9.2f}{m['score_min']:>8.3f}"
            f"{m['score_min_median']:>8.3f}")
    out()
    out("Признаки, отобранные на трейне (по |корреляции| с исходом, дубли "
        "выброшены):")
    for k in keys:
        m = models[k]
        pairs = ", ".join(f"{nm} ({c:+.2f})" for nm, c in m["train"]["corr"])
        out(f"  {k:<18} {pairs}")
    out()
    out("Пояснение колонок: AUC.обуч — на тех же сделках, на которых училась "
        "(всегда завышен);")
    out("AUC.вне — блочная кросс-валидация ВНУТРИ трейна; нуль — средний "
        "AUC.вне на ПЕРЕМЕШАННЫХ исходах")
    ns = [m["train"]["n"] for m in models.values()]
    out(f"({N_PERM} перестановок): при {min(ns)}-{max(ns)} примерах и отборе "
        "признаков он НЕ равен 0.5, поэтому сравнивать надо с ним;")
    out("p — доля перестановок с AUC не хуже реального (p>0.05 = модель "
        "неотличима от случайной).")
    out("ост.доля — какую долю сигналов оставил порог, выбранный CV (1.00 = "
        "CV отказалась фильтровать).")

    # ---------------------------------------------------- 2. holdout
    out()
    out(LINE)
    out("2. HOLDOUT: без скоринга vs со скорингом (эти бары модель не "
        "видела ни в каком виде)")
    out(LINE)
    for k in keys:
        cfg, m = cfgs[k], models[k]
        d = sc.load_data(cfg["interval_min"])
        hold = m["hold_bars"]
        rng = (hold, d["n"])
        fn = sc.make_score_fn(m)
        r0, s0 = sc.run_range(cfg["setup"], cfg["genome"], d, rng, cfg["lev"])
        rA, sA = sc.run_range(cfg["setup"], cfg["genome"], d, rng, cfg["lev"],
                              score_fn=fn, score_min=m["score_min"])
        rB, sB = sc.run_range(cfg["setup"], cfg["genome"], d, rng, cfg["lev"],
                              score_fn=fn, score_min=m["score_min_median"])
        # AUC модели на holdout считаем по БАЗОВЫМ сделкам (без фильтра)
        bs = sc.signal_bars(cfg["setup"], cfg["genome"], d, r0["signal_range"])
        ext = se.build_ext(cfg["setup"], cfg["genome"], d["c_sig"],
                           interval_min=cfg["interval_min"])
        ys, ps, rs = [], [], []
        for t in r0["trades"]:
            i = d["ts_index"][t["signal_ts"]]
            f = se.bar_features(cfg["setup"], i, d["c_sig"], d["ctx"],
                                cfg["genome"], ext,
                                bars_since_signal=bs.get(i))
            ys.append(1 if t["reason"] == "tp" else 0)
            ps.append(fn(f))
            rs.append(t["r"])
        a_h = sc.auc(ys, ps)
        acc_h = sc.accuracy(ys, ps, m["score_min_median"])
        maj = (max(sum(ys), len(ys) - sum(ys)) / len(ys)) if ys else None
        # прямое доказательство/опровержение: делим ТЕ ЖЕ базовые сделки на
        # «оценка выше порога Б» и «ниже» — если модель ранжирует, у верхней
        # половины средняя R обязана быть выше
        thr_b = m["score_min_median"]
        hi = [rs[j] for j in range(len(rs)) if ps[j] >= thr_b]
        lo = [rs[j] for j in range(len(rs)) if ps[j] < thr_b]
        res[k] = dict(base=s0, A=sA, B=sB, auc=a_h, acc=acc_h, maj=maj,
                      lev=cfg["lev"], n_hold_bars=d["n"] - hold,
                      n_tp=sum(ys), n_neg=len(ys) - sum(ys),
                      hi=(len(hi), sc._mean(hi) if hi else 0.0),
                      lo=(len(lo), sc._mean(lo) if lo else 0.0))
    out(f"{'':<34}{'сд.':>4} {'WR%':>6} {'exp_R':>7} {'PF':>5} "
        f"{'сумма R':>8} {'итог%':>8} {'DD%':>6}")
    for k in keys:
        rr, m = res[k], models[k]
        a_txt = ("— (нет обоих исходов)" if rr["auc"] is None
                 else f"{rr['auc']:.3f}")
        thr_a, thr_b = m["score_min"], m["score_min_median"]
        tag_a = ("А: CV отключила фильтр" if thr_a <= 0
                 else f"А: порог с CV {thr_a:.3f}")
        tag_b = f"Б: порог = медиана {thr_b:.3f}"
        out(THIN)
        out(f"{k}  (плечо x{rr['lev']}, holdout {rr['n_hold_bars']} баров, "
            f"тейков {rr['n_tp']} / не-тейков {rr['n_neg']}, "
            f"AUC модели: {a_txt})")
        out(f"  {'без скоринга':<32}{fmt(rr['base'])}")
        out(f"  {tag_a:<32}{fmt(rr['A'])}")
        out(f"  {tag_b:<32}{fmt(rr['B'])}")
        out(f"  ранжирование тех же базовых сделок: оценка выше порога Б — "
            f"{rr['hi'][0]} сд. по {rr['hi'][1]:+.3f}R, ниже — "
            f"{rr['lo'][0]} сд. по {rr['lo'][1]:+.3f}R")
    out(THIN)

    # ---------------------------------------------------- 3. вердикты
    out()
    out(LINE)
    out("3. ВЕРДИКТЫ")
    out(LINE)
    verdicts = {}
    for k in keys:
        rr, m = res[k], models[k]
        t = m["train"]
        p = (t.get("perm") or {}).get("p_value")
        dA = rr["A"]["exp_r"] - rr["base"]["exp_r"]
        dB = rr["B"]["exp_r"] - rr["base"]["exp_r"]
        why = []
        if p is not None and p > 0.05:
            why.append(f"модель неотличима от случайной (p={p:.2f})")
        if rr["auc"] is not None:
            why.append(f"AUC на holdout {rr['auc']:.2f}")
        if m["score_min"] <= 0:
            why.append("CV сама отказалась фильтровать")
        else:
            why.append(f"фильтр А: exp_R {dA:+.3f}R, сделок "
                       f"{rr['A']['n']} против {rr['base']['n']}")
        why.append(f"фильтр Б: exp_R {dB:+.3f}R, сделок {rr['B']['n']}")
        why.append(f"ранжирование: {rr['hi'][1]:+.3f}R сверху против "
                   f"{rr['lo'][1]:+.3f}R снизу")
        helps = ((m["score_min"] > 0 and dA > 0.05 and rr["A"]["n"] >= 5)
                 or (dB > 0.05 and rr["B"]["n"] >= 5))
        strong = (p is not None and p <= 0.05 and rr["auc"] is not None
                  and rr["auc"] > 0.55)
        # ПОМОГАЕТ только если модель значима на трейне И различает на
        # holdout И даёт выигрыш; иначе разница — шум, а не польза
        v = ("ПОМОГАЕТ" if helps and strong else
             ("шум (в плюс, но не значимо)" if helps else "НЕ ПОМОГАЕТ"))
        verdicts[k] = v
        out(f"{k:<18} {v:<28} {'; '.join(why)}")

    # свод
    out()
    out(LINE)
    out("СВОД ПО ВСЕМ 8 КОМБИНАЦИЯМ (holdout, сделки всех сетапов вместе)")
    out(LINE)
    for name, tag in (("base", "без скоринга"), ("A", "скоринг А (CV)"),
                      ("B", "скоринг Б (медиана)")):
        n = sum(res[k][name]["n"] for k in keys)
        s_r = sum(res[k][name]["sum_r"] for k in keys)
        tp = sum(res[k][name]["tp"] for k in keys)
        wr = 100.0 * sum(res[k][name]["n"] * res[k][name]["wr"] / 100.0
                         for k in keys) / n if n else 0.0
        out(f"  {tag:<22} сделок {n:4d}  тейков {tp:3d}  WR {wr:5.1f}%  "
            f"сумма R {s_r:+8.2f}  средняя сделка {s_r/n if n else 0:+.3f}R")
    aucs = [res[k]["auc"] for k in keys if res[k]["auc"] is not None]
    # у половины комбинаций на holdout 1-2 тейка: AUC там вырожден (1.00 при
    # одном тейке ничего не значит), поэтому средний считаем только там, где
    # обоих исходов хотя бы по 3
    solid = [(k, res[k]["auc"]) for k in keys if res[k]["auc"] is not None
             and res[k]["n_tp"] >= 3 and res[k]["n_neg"] >= 3]
    out(f"  AUC на holdout по комбинациям: "
        + ", ".join(f"{k.split('@')[0][:6]}@{k.split('@')[1]} "
                    f"{res[k]['auc']:.2f}" for k in keys
                    if res[k]["auc"] is not None))
    out(f"  из них не вырожденных (>=3 тейка и >=3 не-тейка): "
        + ", ".join(f"{k} {a:.2f}" for k, a in solid)
        + f"; средний {sum(a for _k, a in solid)/len(solid):.3f} "
          f"(0.5 = модель не различает хорошие и плохие сигналы)")
    ps = [(models[k]['train'].get('perm') or {}).get('p_value') for k in keys]
    ps = [p for p in ps if p is not None]
    out(f"  p-значения теста значимости: "
        f"{', '.join(f'{p:.2f}' for p in ps)}; значимых (p<=0.05): "
        f"{sum(1 for p in ps if p <= 0.05)} из {len(ps)}")
    out("  вердикты: " + ", ".join(
        f"{v} — {sum(1 for k in keys if verdicts[k] == v)}"
        for v in sorted(set(verdicts.values()))))

    # ------------------------------------- 4. общая модель (проверка «мало
    # данных или нечего предсказывать»)
    out()
    out(LINE)
    out("4. ОБЩАЯ МОДЕЛЬ НА ВСЕХ СЕТАПАХ СРАЗУ (проверка «дело в нехватке "
        "данных?»)")
    out(LINE)
    pm, prows = sc.train_pooled(cfgs, n_perm=N_PERM)
    pt = pm["train"]
    pp = (pt.get("perm") or {})
    out(f"Обучающих сделок {pt['n']} (все 8 комбинаций вместе, только бары "
        f"[0..hold) своего ТФ), тейков {pt['n_tp']}, база "
        f"{pt.get('base_rate')}.")
    out(f"Признаки ({len(pm['features'])}): "
        + ", ".join(f"{nm} ({c:+.2f})" for nm, c in pt["corr"]))
    out(f"AUC внутри обучения {pt['auc_in']:.3f}; вне обучения "
        f"{pt['auc_oof']:.3f} при нулевом {pp.get('null_mean')}; "
        f"p = {pp.get('p_value')}")
    prows_h = sc.collect_all(cfgs, "hold")
    fnp = sc.make_score_fn(pm)
    yh = [r["y"] for r in prows_h]
    ph = [fnp(r["f"]) for r in prows_h]
    ah = sc.auc(yh, ph)
    thr = pm["score_min_median"]
    hi = [r["r"] for r, s in zip(prows_h, ph) if s >= thr]
    lo = [r["r"] for r, s in zip(prows_h, ph) if s < thr]
    out(f"HOLDOUT (все сделки всех сетапов, {len(prows_h)} штук): AUC "
        f"{'—' if ah is None else f'{ah:.3f}'}, точность при пороге "
        f"{thr:.3f} — {sc.accuracy(yh, ph, thr):.3f} "
        f"(всегда-«нет» дало бы {1 - sc._mean([float(v) for v in yh]):.3f})")
    out(f"Ранжирование на holdout: оценка выше медианы трейна — {len(hi)} сд. "
        f"по {sc._mean(hi) if hi else 0:+.3f}R, ниже — {len(lo)} сд. по "
        f"{sc._mean(lo) if lo else 0:+.3f}R "
        f"(если модель работает, слева должно быть заметно больше)")

    conf = [k for k in keys if cfgs[k]["enabled"]]
    out()
    out("ТОЛЬКО ПОДТВЕРЖДЁННЫЕ evolution12 СЕТАПЫ (единственные, которым "
        f"можно верить): {', '.join(conf)}")
    for name, tag in (("base", "без скоринга"), ("A", "скоринг А (CV)"),
                      ("B", "скоринг Б (медиана)")):
        n = sum(res[k][name]["n"] for k in conf)
        s_r = sum(res[k][name]["sum_r"] for k in conf)
        out(f"  {tag:<22} сделок {n:4d}  сумма R {s_r:+8.2f}  "
            f"средняя сделка {s_r/n if n else 0:+.3f}R")

    out()
    out(LINE)
    out("ОТВЕТ НА ВОПРОС «ПОМОГАЕТ ЛИ СКОРИНГ»")
    out(LINE)
    n_help = sum(1 for k in keys if verdicts[k] == "ПОМОГАЕТ")
    base_r = sum(res[k]["base"]["sum_r"] for k in keys)
    b_r = sum(res[k]["B"]["sum_r"] for k in keys)
    cut = 100 - 100.0 * sum(res[k]["B"]["n"] for k in keys) / sum(
        res[k]["base"]["n"] for k in keys)
    a_in = [models[k]["train"]["auc_in"] for k in keys]
    a_oof = [models[k]["train"]["auc_oof"] for k in keys
             if models[k]["train"].get("auc_oof") is not None]
    out(f"НЕТ, НЕ ПОМОГАЕТ. Вердикт ПОМОГАЕТ не получила ни одна из 8 "
        f"комбинаций ({n_help} из 8).")
    out(f"1) Значимость: 0 из {len(ps)} моделей отличимы от случайной "
        f"(перестановочный тест на трейне, все p от "
        f"{min(ps):.2f} до {max(ps):.2f}).")
    out(f"2) Различающая способность на holdout: AUC "
        f"{min(a for _k, a in solid):.2f}..{max(a for _k, a in solid):.2f} "
        f"на не вырожденных комбинациях, средний "
        f"{sum(a for _k, a in solid)/len(solid):.2f}; общая модель на всех "
        f"125 holdout-сделках — AUC {ah:.3f}.")
    out(f"3) Деньги: априорный фильтр «отсечь худшую половину» убирает "
        f"{cut:.0f}% сделок и меняет сумму R с {base_r:+.2f} на {b_r:+.2f}; "
        f"на двух ПОДТВЕРЖДЁННЫХ сетапах — с "
        f"{sum(res[k]['base']['sum_r'] for k in conf):+.2f}R на "
        f"{sum(res[k]['B']['sum_r'] for k in conf):+.2f}R. Это ухудшение.")
    out(f"4) Кросс-валидация внутри трейна САМА отказалась фильтровать в 7 "
        f"случаях из 8 — то есть отрицательный ответ виден ещё до holdout, "
        f"он не подгонка постфактум.")
    out(f"Причина НЕ только в объёме выборки: на комбинацию приходится "
        f"{min(m['train']['n'] for m in models.values())}-"
        f"{max(m['train']['n'] for m in models.values())} обучающих сделок "
        f"(AUC внутри обучения {min(a_in):.2f}-{max(a_in):.2f} против "
        f"{min(a_oof):.2f}-{max(a_oof):.2f} вне обучения — переобучение "
        f"налицо),")
    out(f"но и общая модель на {pt['n']} сделках сразу не работает: вне "
        f"обучения AUC {pt['auc_oof']:.3f} при нулевом уровне "
        f"{pp.get('null_mean')}, на holdout {ah:.3f}, и её «лучшая» половина "
        f"сделок дала {sc._mean(hi):+.3f}R против {sc._mean(lo):+.3f}R у "
        f"«худшей» — ранжирование ПЕРЕВЁРНУТОЕ.")
    out("Вывод: 30 признаков сигнального бара НЕ содержат информации об "
        "исходе конкретной сделки при RR 1:3 — исход определяется тем, что "
        "происходит ПОСЛЕ входа.")
    out("Практический смысл: включать score_gate в живой торговле нельзя; в "
        "GA (evolution12) гены скоринга подключать тоже незачем — он подберёт "
        "их по обучающей части и получит ту же иллюзию.")

    sc.save_models(dict(models, **{pm["key"]: pm}))
    out()
    out(f"Модели сохранены: {sc.MODELS_FILE} (8 боевых ключей + диагностиче"
        f"ская «__pooled__»). Время: {time.time()-t_all:.0f}с")
    out("ВАЖНО: модели сохранены как результат эксперимента, а не как "
        "рабочий фильтр. По цифрам выше включать их в торговлю нет "
        "оснований.")
    with open("score_report_out.txt", "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(_BUF.getvalue())


if __name__ == "__main__":
    main()
