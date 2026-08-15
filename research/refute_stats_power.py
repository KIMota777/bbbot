# -*- coding: utf-8 -*-
"""МОЩНОСТЬ ПРОТОКОЛА: увидел бы он перевес, если бы перевес БЫЛ?

Отрицательный вывод бывает двух разных сортов, и их всё время путают:

    (а) «мы проверили и перевеса нет»;
    (б) «наш прибор не различил бы перевес такого размера, даже если бы он
         был» — то есть проверки фактически не было.

Различить их можно только одним способом: подсунуть протоколу ВЫДУМАННУЮ
стратегию с ИЗВЕСТНЫМ перевесом и посмотреть, найдёт ли он его.

ЧТО ИМЕННО ПОДСОВЫВАЕТСЯ. Семейство из 36 сочетаний параметров (сетка 6x6),
как у настоящей стратегии. Сделки идут по всей истории с той же частотой и
тем же разбросом, что у кандидатов автора (разброс 1.4% на сделку,
300 сделок в год). Соседние сочетания сетки СВЯЗАНЫ между собой — их
случайная удача сглажена по сетке, как у настоящих стратегий, где соседние
параметры дают почти одни и те же сделки. Без этой связанности проверка была
бы нечестно лёгкой: выбор плато ловил бы плато там, где его нет.

Дальше эта выдумка проходит ВЕСЬ протокол автора без единой поблажки:
    * тот же класс Tape и та же функция replay;
    * та же скользящая проверка вперёд 360/90 (protocol.walk_forward);
    * тот же выбор ПЛАТО, а не пика (protocol.plateau_scores);
    * та же склейка внеобучающих кусков (protocol.stitch);
    * тот же закрытый экзамен: параметры выбираются по 360 дням, кончающимся
      строго до начала экзамена, и применяются к 169 дням экзамена.

Настоящие рыночные данные здесь не используются вовсе — ни обучающие, ни
экзаменационные. Используются только ГРАНИЦЫ дат, чтобы длины окон совпали
с авторскими. Поэтому запрет на экзаменационную выборку не нарушается.

ЧЕТЫРЕ УСТРОЙСТВА ПЕРЕВЕСА:
    «ровный»   — перевес есть у всех сочетаний сетки (щедрый случай);
    «плато»    — перевес есть у куска сетки 3x3, у остальных ноль (реализм);
    «пик»      — перевес есть ровно у одного сочетания (случай, который
                 выбор плато обязан ПРОПУСТИТЬ — и это правильно);
    «пусто»    — перевеса нет ни у кого (контроль ложной тревоги).
"""
import json
import os
import sys
import time

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import protocol   # noqa: E402
import rdata      # noqa: E402

OUT = os.path.join(DIR, "out")
DAY = rdata.DAY_MS

# --- параметры выдуманной стратегии (взяты из наблюдённых у автора) ---------
SD_TRADE = 0.014          # разброс доходности сделки, доли капитала
TRADES_YEAR = 300         # сделок в год
RHO_MARKET = 0.35         # доля общей рыночной удачи в разбросе сделки
SMOOTH = 1.0              # сглаживание случайности по сетке (связь соседей)
GRID = {"a": list(range(6)), "b": list(range(6))}
KEYS = sorted(GRID)
PLATEAU = [(i, j) for i in (2, 3, 4) for j in (2, 3, 4)]
PEAK = [(3, 3)]

T0 = rdata.HIST_START_MS
T_VAL = rdata.VAL_END_MS          # конец обучения+проверки
T1 = rdata.HIST_END_MS + 1
EXAM_DAYS = (T1 - T_VAL) / DAY
IS_DAYS, OOS_DAYS = 360, 90


def make_tapes(rng, mu_map):
    """Ленты сделок для всех сочетаний сетки. mu_map: (i,j) -> перевес."""
    days = (T1 - T0) / DAY
    n = int(TRADES_YEAR * days / 365.0)
    t_in = np.sort(rng.uniform(T0, T1 - 2 * DAY, n)).astype(np.int64)
    t_out = t_in + int(0.7 * DAY)          # удержание меньше суток

    na, nb = len(GRID["a"]), len(GRID["b"])
    # общая рыночная удача — одна на все сочетания
    mkt = rng.normal(0.0, 1.0, n)
    # своя случайность, сглаженная по сетке: соседи похожи друг на друга
    e = rng.normal(0.0, 1.0, (na, nb, n))
    if SMOOTH > 0:
        pad = np.pad(e, ((1, 1), (1, 1), (0, 0)), mode="edge")
        e = (4 * e + pad[:-2, 1:-1] + pad[2:, 1:-1]
             + pad[1:-1, :-2] + pad[1:-1, 2:]) / np.sqrt(4 ** 2 + 4)
    w = np.sqrt(1.0 - RHO_MARKET ** 2)
    tapes = {}
    for i in range(na):
        for j in range(nb):
            r = mu_map.get((i, j), 0.0) + SD_TRADE * (
                RHO_MARKET * mkt + w * e[i, j])
            tp = protocol.Tape.__new__(protocol.Tape)
            tp.t_in = t_in
            tp.t_out = t_out
            tp.ret = r
            tp.eq_low = 1.0 + np.minimum(r * 1.25, 0.0)
            tapes[(GRID["a"][i], GRID["b"][j])] = tp
    return tapes


def mu_for(kind, mu0):
    if kind == "ровный":
        return {(i, j): mu0 for i in range(6) for j in range(6)}
    if kind == "плато":
        return {k: mu0 for k in PLATEAU}
    if kind == "пик":
        return {k: mu0 for k in PEAK}
    return {}


def one_run(rng, kind, mu0):
    """Один прогон всего протокола. Возвращает результат склейки и экзамена."""
    tapes = make_tapes(rng, mu_for(kind, mu0))
    wf = protocol.walk_forward(tapes, GRID, IS_DAYS, OOS_DAYS,
                               t_start=T0, t_end=T_VAL)
    st = protocol.stitch(wf, tapes)

    # --- экзамен: параметры выбираются по 360 дням ДО его начала -----------
    a, b = T_VAL - IS_DAYS * DAY, T_VAL
    rows = []
    for ck, tp in tapes.items():
        m = protocol.replay(tp, a, b)
        if m["trades"] >= 5:
            rows.append((dict(zip(KEYS, ck)), m["score"]))
    sm = protocol.plateau_scores(rows, GRID)
    sm.sort(key=lambda x: -x[1])
    best = sm[0][0]
    bk = tuple(best[k] for k in KEYS)
    ex = protocol.replay(tapes[bk], T_VAL, T1)

    good = set(mu_for(kind, 1.0)) if kind in ("плато", "пик") else None
    chosen_ok = (bk in good) if good else None
    # сколько раз протокол выбирал «правильную» область на обучающих окнах
    wf_ok = None
    if good:
        picks = [f["chosen_key"] for f in wf if f.get("chosen_key")]
        wf_ok = float(np.mean([p in good for p in picks])) if picks else 0.0

    days_st = max(st["days"], 1.0)
    return dict(
        tv_mo=st["mo"], tv_dd=st["maxdd"], tv_trades=st["trades"],
        ex_mo=float((1 + ex["ret"]) ** (30.0 / EXAM_DAYS) - 1.0)
        if ex["ret"] > -1 else -1.0,
        ex_dd=ex["maxdd"], ex_trades=ex["trades"],
        chosen_ok=chosen_ok, wf_ok=wf_ok, days=days_st)


def summarize(runs):
    tv = np.array([r["tv_mo"] for r in runs])
    ex = np.array([r["ex_mo"] for r in runs])
    out = dict(
        n=len(runs),
        tv_med=float(np.median(tv)), ex_med=float(np.median(ex)),
        tv_pos=float((tv > 0).mean()), ex_pos=float((ex > 0).mean()),
        ex_p2=float((ex > 0.02).mean()),
        ex_lo=float(np.percentile(ex, 5)), ex_hi=float(np.percentile(ex, 95)),
        ex_trades=float(np.mean([r["ex_trades"] for r in runs])),
        corr=float(np.corrcoef(tv, ex)[0][1]) if len(runs) > 3 else 0.0)
    ok = [r["wf_ok"] for r in runs if r["wf_ok"] is not None]
    if ok:
        out["wf_ok"] = float(np.mean(ok))
        out["exam_pick_ok"] = float(np.mean(
            [r["chosen_ok"] for r in runs if r["chosen_ok"] is not None]))
    return out


def main():
    n_sim = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    print("=" * 78)
    print("МОЩНОСТЬ ПРОТОКОЛА: НАШЁЛ БЫ ОН ПЕРЕВЕС, ЕСЛИ БЫ ПЕРЕВЕС БЫЛ")
    print("=" * 78)
    print("выдуманная стратегия: разброс сделки %.1f%%, %d сделок в год,"
          % (100 * SD_TRADE, TRADES_YEAR))
    print("сетка %dx%d, окна %d/%d, экзамен %.0f дней, прогонов на клетку %d"
          % (6, 6, IS_DAYS, OOS_DAYS, EXAM_DAYS, n_sim))

    # --- что вообще способен различить экзамен: чистая арифметика ----------
    n_ex = TRADES_YEAR * EXAM_DAYS / 365.0
    se = SD_TRADE / np.sqrt(n_ex)
    mde95 = 1.96 * se
    mde80 = (1.96 + 0.84) * se
    per_month = TRADES_YEAR / 12.0
    print("\nАРИФМЕТИКА ЭКЗАМЕНА (ещё до всякого моделирования)")
    print("   за %.0f дней набирается %.0f сделок, ошибка средней сделки %.3f%%"
          % (EXAM_DAYS, n_ex, 100 * se))
    print("   чтобы просто ОТЛИЧИТЬ перевес от нуля (95%%), нужно >= %.3f%% "
          "на сделку" % (100 * mde95))
    print("   чтобы находить его НАДЁЖНО (мощность 80%%), нужно >= %.3f%%"
          % (100 * mde80))
    print("   в пересчёте на месяц это %+.1f%% и %+.1f%% соответственно"
          % (100 * mde95 * per_month, 100 * mde80 * per_month))
    print("   ЗАДАНИЕ ПРОСИЛО +4..5% в месяц, автор получил +2.9% —")
    print("   и то и другое НИЖЕ порога различимости на 169 днях.")

    # --- сколько дней нужно на разные перевесы -----------------------------
    print("\n   сколько дней экзамена нужно на разные перевесы (мощность 80%%):")
    for mu in (0.001, 0.002, 0.003, 0.005):
        n_need = ((1.96 + 0.84) * SD_TRADE / mu) ** 2
        d_need = n_need / TRADES_YEAR * 365
        print("      %+.1f%% на сделку (%+.1f%%/мес): %5.0f сделок = %5.0f дней"
              " = %.1f года" % (100 * mu, 100 * mu * per_month, n_need,
                                d_need, d_need / 365))

    # --- моделирование -----------------------------------------------------
    cells = []
    for kind in ("пусто", "ровный", "плато", "пик"):
        levels = (0.0,) if kind == "пусто" else (0.001, 0.002, 0.003)
        for mu0 in levels:
            cells.append((kind, mu0))

    print("\nПРОГОН ЧЕРЕЗ ВЕСЬ ПРОТОКОЛ АВТОРА")
    print("%-8s %-9s | %-22s | %-30s" % ("устрой", "перевес",
                                         "обучение (склейка)", "ЭКЗАМЕН 169 дней"))
    print("%-8s %-9s | %8s %8s | %8s %8s %11s"
          % ("ство", "на сделку", "мес", "в плюсе", "мес", "в плюсе", "нашёл область"))
    results = {}
    t_start = time.time()
    for kind, mu0 in cells:
        rng = np.random.default_rng(hash((kind, int(mu0 * 1e6))) % (2 ** 31))
        runs = [one_run(rng, kind, mu0) for _ in range(n_sim)]
        s = summarize(runs)
        results["%s|%.4f" % (kind, mu0)] = s
        pick = ("%.0f%%" % (100 * s["wf_ok"])) if "wf_ok" in s else "-"
        print("%-8s %+8.1f%% | %+7.2f%% %7.0f%% | %+7.2f%% %7.0f%% %11s"
              % (kind, 100 * mu0, 100 * s["tv_med"], 100 * s["tv_pos"],
                 100 * s["ex_med"], 100 * s["ex_pos"], pick))
    print("   (время %.0f с)" % (time.time() - t_start))

    # --- главный вывод -----------------------------------------------------
    print("\nЧТО ИЗ ЭТОГО СЛЕДУЕТ")
    z = results["пусто|0.0000"]
    print("   БЕЗ всякого перевеса протокол показывает на обучении %+.2f%% в"
          % (100 * z["tv_med"]))
    print("   месяц и оказывается в плюсе на обучении в %.0f%% прогонов."
          % (100 * z["tv_pos"]))
    print("   То есть положительная склейка сама по себе НИЧЕГО не значит.")
    a = results.get("плато|0.0030")
    if a:
        print("\n   С НАСТОЯЩИМ перевесом +0.3%% на сделку (%+.0f%% в месяц —"
              % (100 * 0.003 * per_month))
        print("   вчетверо больше цели задания) протокол находит нужную область")
        print("   сетки в %.0f%% окон и выходит в плюс на экзамене в %.0f%% "
              "прогонов." % (100 * a.get("wf_ok", 0), 100 * a["ex_pos"]))
    b = results.get("плато|0.0010")
    if b:
        print("\n   С перевесом +0.1%% на сделку (%+.1f%% в месяц — примерно то,"
              % (100 * 0.001 * per_month))
        print("   что автор наблюдал у лучших кандидатов) протокол выходит в")
        print("   плюс на экзамене лишь в %.0f%% прогонов — то есть проваливает"
              % (100 * b["ex_pos"]))
        print("   экзамен в %.0f%% случаев ПРИ ЗАВЕДОМО НАСТОЯЩЕМ перевесе."
              % (100 * (1 - b["ex_pos"])))
        print("   Провал на экзамене при таком перевесе — обычное дело, а не")
        print("   доказательство отсутствия перевеса.")

    print("\n   СВЯЗЬ «обучение -> экзамен» в самой модели, где перевес ЕСТЬ")
    print("   и известен точно:")
    for k in ("пусто|0.0000", "плато|0.0010", "плато|0.0030", "ровный|0.0030"):
        if k in results:
            print("      %-16s %+.2f" % (k.replace("|", " "), results[k]["corr"]))
    print("   даже при заведомо настоящем перевесе связь между результатом на")
    print("   обучении и на экзамене мала — потому что оба измерения шумные.")

    out = dict(sd_trade=SD_TRADE, trades_year=TRADES_YEAR,
               exam_days=EXAM_DAYS, n_exam_trades=n_ex,
               mde95_trade=mde95, mde80_trade=mde80,
               mde95_month=mde95 * per_month, mde80_month=mde80 * per_month,
               n_sim=n_sim, cells=results)
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "refute_stats_power.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, default=float)
    print("\nсохранено -> out/refute_stats_power.json")


if __name__ == "__main__":
    main()
