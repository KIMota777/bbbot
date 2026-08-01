# -*- coding: utf-8 -*-
"""Максимальная статистика по сетапу v2 — ответ на вопрос «что пошло не так».

Модуль НИЧЕГО не моделирует и не досчитывает по свечам: он только агрегирует
то, что уже вернул signal_engine2.run_setup (сделки с MAE/MFE, near-miss с
гипотетикой, счётчики ворот и блокировок). Никаких новых допущений.

Главная функция: full_stats(r, lev, t0_ms=None) -> dict

Блоки результата (подробный перечень ключей — в конце файла, KEYS_DOC):
  база          — сделки, WR, expectancy в R и в $, PF, gross P/L, средние и
                  медианные прибыльная/убыточная, лучшая/худшая, макс. серии,
                  суммарный R, порог безубыточного WR;
  reinvest      — реинвест-капитал от $50 при марже 25% капитала (как на
                  сайте: eq *= 1 + pnl/START, где MARGIN = 25% от START),
                  кривые equity [[ts,usd]] / drawdown [[ts,pct<=0]] / monthly;
  outcomes      — tp/stop/liq/timeout: штук, %, вклад в R и в $;
  stop_quality  — MAE_R у ПРИБЫЛЬНЫХ (можно ли стоп сузить), MFE_R у
                  УБЫТОЧНЫХ (сколько было в плюсе перед стопом), доля стопов с
                  would_hit_tp_later (стоп был слишком тесным) и суммарный
                  недополученный R;
  tp_quality    — mfe_after_tp_r у тейков: не жадничаем ли с RR 1:3;
  timing        — удержание для tp и stop отдельно, время до MAE/MFE,
                  гистограмма удержаний;
  by_regime     — bull/range/bear: сделок, WR, expectancy, вклад в итог
                  (как сетап ведёт себя в «чуждом» рынке);
  by_hour / by_weekday / by_month — время входа (UTC) и доход по месяцам;
  r_hist        — распределение R по бинам;
  significance  — статистическая значимость: t-статистика и p для expectancy
                  R, 95% ДИ Уилсона для WR и флаг «ДИ накрывает порог
                  безубытка» (проценты без значимости обманывают);
  costs         — издержки (комиссии + фандинг + проскальзывание): в $, в R и
                  в долях результата, СТРОГО из данных сделки, без домыслов;
  near          — сводка near-miss: сколько, по какому вороту, сумма и средний
                  гипотетический R, сколько были бы плюсовыми, и отдельное
                  предупреждение, если near-miss в среднем УБЫТОЧНЫ (тогда
                  строгость порогов оправдана);
  rejects       — reject_counts / gate_solo / занятость / кулдаун с процентами;
  warnings      — готовые текстовые выводы «что пошло не так» (по-русски),
                  включая честные оговорки о малой выборке и плюсы.

ЧЕСТНОСТЬ (принципы, которые нельзя нарушать при правках):
  - порог безубыточного WR не считается по 1-2 сделкам: при малой выборке
    берётся теория R:R 1:3 с поправкой на измеренные издержки, и это явно
    помечается в wr_breakeven_reliable / wr_breakeven_note;
  - проценты вклада не нормируются на почти нулевой итог (иначе вклад
    режима «+361%»), нормировка — на сумму модулей вкладов;
  - частота сделок для предупреждений берётся по ФАКТИЧЕСКОМУ периоду
    торговли (tpm_active), а не по календарю;
  - «упущенные R» из-за тесного стопа сопровождаются оговоркой, что стоп
    расширить нельзя (он уже занимает N% пути до ликвидации);
  - если метрику нельзя посчитать строго — она не додумывается, а
    возвращается None с пояснением в *_note.
"""

import datetime
import math

import evolution as ev
import signal_engine2 as se2

START = se2.START            # 20.0 — фикс-база бэктеста
MARGIN = se2.MARGIN          # 5.0  — маржа на сделку (25% от START)
RR = se2.RR                  # 3.0
MONTH_MS = se2.MONTH_MS
MONTH_SEC = se2.MONTH_MS // 1000
TAKER = se2.TAKER            # 0.055% — тейкерская комиссия
MAKER = se2.MAKER            # 0.02%  — мейкерская (выход по тейку)
SLIP = se2.SLIP              # 0.03%  — проскальзывание
FUND_8H = se2.FUND_8H        # 0.01% за 8ч — фандинг
TPM_NORM = se2.TPM_NORM      # 1.2 — желаемая частота сделок в месяц

SLEEVE0 = 50.0               # старт реинвест-капитала, как на сайте
NEAR_STOP_R = 0.8            # MAE >= 0.8R — прибыльная почти цепляла стоп
IN_PROFIT_R = 1.0            # MFE >= 1.0R — убыточная была заметно в плюсе

BE_MIN_SIDE = 5              # минимум прибыльных И убыточных для эмпирического
                             # порога безубытка (иначе теория + издержки)
Z95 = 1.959964              # квантиль нормального распределения для 95% ДИ
ALPHA = 0.05                 # уровень значимости
SIG_MIN_N = 5                # меньше сделок — значимость не проверяем
CONTRIB_FLAT_RATIO = 0.25    # |итог| < 25% суммы модулей вкладов -> итог
                             # «плоский», проценты вклада надо пояснять
COSTS_MIN_GROSS = 1.0        # |итог без издержек| ниже -> доля издержек от
                             # итога не считается (взрывается)
SAME_BAR_EPS = 0.003         # допуск на округление mfe_r (3 знака)

WD_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

# бины распределения R (from <= r < to, последний бин включительно)
R_BINS = (
    (-1e18, -1.0, "хуже -1R"), (-1.0, -0.5, "-1..-0.5R"),
    (-0.5, 0.0, "-0.5..0R"), (0.0, 0.5, "0..0.5R"), (0.5, 1.0, "0.5..1R"),
    (1.0, 1.5, "1..1.5R"), (1.5, 2.0, "1.5..2R"), (2.0, 2.5, "2..2.5R"),
    (2.5, 3.0, "2.5..3R"), (3.0, 1e18, "3R+"),
)
HOLD_BINS = (
    (0.0, 6.0, "0-6ч"), (6.0, 12.0, "6-12ч"), (12.0, 24.0, "12-24ч"),
    (24.0, 48.0, "1-2дн"), (48.0, 96.0, "2-4дн"), (96.0, 168.0, "4-7дн"),
    (168.0, 1e18, "7дн+"),
)
TP_EXTRA_BINS = (
    (0.0, 0.25, "0..0.25R"), (0.25, 0.5, "0.25..0.5R"), (0.5, 1.0, "0.5..1R"),
    (1.0, 2.0, "1..2R"), (2.0, 3.0, "2..3R"), (3.0, 5.0, "3..5R"),
    (5.0, 1e18, "5R+"),
)


# ------------------------------------------------------------- мелкие хелперы
def _avg(xs, nd=3):
    return round(sum(xs) / len(xs), nd) if xs else 0.0


def _med(xs, nd=3):
    if not xs:
        return 0.0
    s = sorted(xs)
    m = len(s) // 2
    v = s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0
    return round(v, nd)


def _perc(xs, p, nd=3):
    return round(ev.percentile(xs, p), nd) if xs else 0.0


def _share(a, b, nd=1):
    """a от b в процентах."""
    return round(a / b * 100, nd) if b else 0.0


def _hist(vals, bins):
    """Гистограмма по бинам bins=((from,to,label),...) -> список словарей."""
    out = []
    n = len(vals)
    for lo, hi, label in bins:
        if hi >= 1e17:
            k = sum(1 for v in vals if v >= lo)
        else:
            k = sum(1 for v in vals if lo <= v < hi)
        out.append(dict(label=label, lo=None if lo <= -1e17 else round(lo, 3),
                        hi=None if hi >= 1e17 else round(hi, 3),
                        n=k, share_pct=_share(k, n)))
    return out


def thin(points, max_points=3000):
    """Прореживание серии [[ts,v],...] РОВНО до max_points точек (первая и
    последняя сохраняются). Используется билдером аналитики.

    Перед прореживанием схлопываем точки с одинаковым временем: библиотека
    графиков на дубликате ts падает и обрывает весь скрипт страницы — так
    однажды «сломался» PnL-график (две сделки закрылись в одну секунду).
    """
    ded = []
    for p in points:
        if ded and ded[-1][0] == p[0]:
            ded[-1] = p
        else:
            ded.append(p)
    points = ded
    if len(points) <= max_points or max_points < 2:
        return points
    step = len(points) / float(max_points - 1)
    out = [points[int(i * step)] for i in range(max_points - 1)]
    out.append(points[-1])
    return out


def _streaks(pnls):
    """(макс. серия побед, макс. серия убытков)."""
    best_w = best_l = cur = 0
    for p in pnls:
        if p > 0:
            cur = cur + 1 if cur > 0 else 1
            best_w = max(best_w, cur)
        elif p < 0:
            cur = cur - 1 if cur < 0 else -1
            best_l = max(best_l, -cur)
        else:
            cur = 0
    return best_w, best_l


def _base_ts_ms(r, t0_ms):
    """Начало периода в мс. Если не передано — берём самый ранний бар, о
    котором есть свидетельство (сигнал сделки или near-miss)."""
    if t0_ms:
        return int(t0_ms)
    cand = [t["signal_ts"] for t in r["trades"] if t.get("signal_ts")]
    cand += [x["ts"] for x in r.get("near_misses", []) if x.get("ts")]
    cand += [t["entry_ts"] for t in r["trades"]]
    return int(min(cand)) if cand else 0


# ----------------------------------------------------------- реинвест-капитал
def _reinvest(trades, t0_s):
    """Кривая капитала с реинвестом от $50: маржа = 25% капитала, поэтому
    множитель сделки = 1 + pnl/START (pnl посчитан на марже 25% от START).
    Возвращает (equity, drawdown, stats-словарь)."""
    eq = peak = SLEEVE0
    equity = [[t0_s, round(SLEEVE0, 2)]]
    drawdown = [[t0_s, 0.0]]
    max_dd = 0.0
    for t in trades:
        ts = t["exit_ts"] // 1000
        eq *= (1 + t["pnl"] / START)
        eq = max(eq, 0.0)
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        equity.append([ts, round(eq, 2)])
        drawdown.append([ts, -round(dd, 2)])
    st = dict(start_usd=SLEEVE0, final_usd=round(eq, 2),
              final_pct=round((eq / SLEEVE0 - 1) * 100, 1),
              max_dd_pct=round(max_dd, 1),
              mult=round(eq / SLEEVE0, 2))
    return equity, drawdown, st


# --------------------------------------------------------------- полная сводка
def full_stats(r, lev, t0_ms=None):
    """Вся статистика по результату signal_engine2.run_setup.

    r      — словарь из run_setup (нужны trades, near_misses, reject_counts,
             gate_solo, blocked_*, signals, bars_eval, months, balance,
             max_dd, ruined, pass_by_regime);
    lev    — плечо, с которым считался прогон (для риска в $);
    t0_ms  — начало периода в мс (по умолчанию выводится из данных). Если
             передать c4[signal_range[0]][0], помесячная разбивка совпадёт
             с r["monthly"] из движка бит-в-бит.
    """
    tr = list(r["trades"])
    nm = list(r.get("near_misses", []))
    n = len(tr)
    t0_ms = _base_ts_ms(r, t0_ms)
    t0_s = t0_ms // 1000

    rs = [t["r"] for t in tr]
    pnls = [t["pnl"] for t in tr]
    win_t = [t for t in tr if t["pnl"] > 0]
    los_t = [t for t in tr if t["pnl"] < 0]
    gross_p = sum(t["pnl"] for t in win_t)
    gross_l = -sum(t["pnl"] for t in los_t)
    win_r = [t["r"] for t in win_t]
    los_r = [t["r"] for t in los_t]
    max_w, max_l = _streaks(pnls)
    stop_pcts = [t["stop_pct"] for t in tr if t.get("stop_pct") is not None]
    atr_pcts = [t["atr_pct"] for t in tr if t.get("atr_pct") is not None]

    # порог безубыточного WR: по ФАКТИЧЕСКИМ средним (учитывает комиссии, слип,
    # таймауты — то есть реальную асимметрию сделок этого сетапа) и чистая
    # теория для RR 1:3 без издержек
    be_rr = round(100.0 / (1.0 + RR), 1)
    if win_r and los_r:
        aw, al = _avg(win_r), abs(_avg(los_r))
        be = round(al / (aw + al) * 100, 1) if (aw + al) else be_rr
    else:
        be = be_rr

    out = dict(
        # ---------------------------------------------------------- база
        setup=r.get("setup"), lev=lev, ruined=bool(r["ruined"]),
        months=round(r["months"], 2),
        n=n, wins=len(win_t), losses=len(los_t),
        wr=_share(len(win_t), n), wr_breakeven=be, wr_breakeven_rr=be_rr,
        tpm=round(n / max(1, int(r["months"])), 2),
        sum_r=round(sum(rs), 2), exp_r=_avg(rs), exp_usd=_avg(pnls, 4),
        pnl_usd=round(sum(pnls), 3),
        pf=round(gross_p / gross_l, 2) if gross_l > 0 else None,
        gross_profit=round(gross_p, 3), gross_loss=round(gross_l, 3),
        avg_win_r=_avg(win_r), med_win_r=_med(win_r),
        avg_loss_r=_avg(los_r), med_loss_r=_med(los_r),
        avg_win_usd=_avg([t["pnl"] for t in win_t], 4),
        avg_loss_usd=_avg([t["pnl"] for t in los_t], 4),
        best_r=round(max(rs), 3) if rs else 0.0,
        worst_r=round(min(rs), 3) if rs else 0.0,
        best_usd=round(max(pnls), 3) if pnls else 0.0,
        worst_usd=round(min(pnls), 3) if pnls else 0.0,
        max_win_streak=max_w, max_loss_streak=max_l,
        start_usd=START, margin_usd=MARGIN,
        balance_end=round(r["balance"], 3),
        ret_pct=round((r["balance"] / START - 1) * 100, 1),
        dd_pct=round(r["max_dd"] * 100, 1),
        avg_stop_pct=_avg(stop_pcts), med_stop_pct=_med(stop_pcts),
        avg_atr_pct=_avg(atr_pcts),
        avg_risk_usd=round(MARGIN * lev * _avg(stop_pcts, 5) / 100, 3),
        period_start=t0_s,
        period_end=(max(t["exit_ts"] for t in tr) // 1000) if tr else t0_s,
    )

    # -------------------------------------------------- реинвест и кривые
    equity, drawdown, reinv = _reinvest(tr, t0_s)
    out["reinvest"] = reinv
    out["equity"] = equity
    out["drawdown"] = drawdown

    # ------------------------------------------------ помесячная разбивка
    by_m = {}
    for t in tr:
        m = int((t["exit_ts"] - t0_ms) // se2.MONTH_MS)
        rec = by_m.setdefault(m, dict(n=0, pnl=0.0, sum_r=0.0, wins=0))
        rec["n"] += 1
        rec["pnl"] += t["pnl"]
        rec["sum_r"] += t["r"]
        rec["wins"] += 1 if t["pnl"] > 0 else 0
    n_months = max(1, int(r["months"]))
    if by_m:
        n_months = max(n_months, max(by_m) + 1)
    monthly, by_month = [], []
    for m in range(n_months):
        rec = by_m.get(m) or dict(n=0, pnl=0.0, sum_r=0.0, wins=0)
        ts = t0_s + m * MONTH_SEC
        pct = rec["pnl"] / START * 100
        monthly.append([ts, round(pct, 2)])
        by_month.append(dict(
            ts=ts, idx=m,
            label=datetime.datetime.fromtimestamp(
                ts, datetime.timezone.utc).strftime("%Y-%m"),
            n=rec["n"], wins=rec["wins"], pnl_usd=round(rec["pnl"], 3),
            pct=round(pct, 2), sum_r=round(rec["sum_r"], 2)))
    m_vals = [p[1] for p in monthly]
    out["monthly"] = monthly
    out["by_month"] = by_month
    out["reinvest"].update(
        best_month_pct=round(max(m_vals), 2) if m_vals else 0.0,
        worst_month_pct=round(min(m_vals), 2) if m_vals else 0.0,
        months_pos=sum(1 for v in m_vals if v > 0),
        months_neg=sum(1 for v in m_vals if v < 0),
        months_total=len(m_vals),
        months_pos_pct=_share(sum(1 for v in m_vals if v > 0), len(m_vals)))

    # -------------------------------------------------------------- исходы
    outcomes = {}
    for why in ("tp", "stop", "liq", "timeout"):
        sub = [t for t in tr if t["reason"] == why]
        outcomes[why] = dict(
            n=len(sub), share_pct=_share(len(sub), n),
            sum_r=round(sum(t["r"] for t in sub), 2),
            avg_r=_avg([t["r"] for t in sub]),
            sum_usd=round(sum(t["pnl"] for t in sub), 3),
            wins=sum(1 for t in sub if t["pnl"] > 0))
    out["outcomes"] = outcomes

    stopped = [t for t in tr if t["reason"] in ("stop", "liq")]
    took = [t for t in tr if t["reason"] == "tp"]
    timed = [t for t in tr if t["reason"] == "timeout"]

    # ------------------------------------------- КАЧЕСТВО СТОПА (главное)
    mae_w = [t["mae_r"] for t in win_t]
    mae_l = [t["mae_r"] for t in los_t]
    mfe_l = [t["mfe_r"] for t in los_t]
    late = [t for t in stopped if t["would_hit_tp_later"]]
    lost_r = sum(RR - t["r"] for t in late)     # недополученный R (3R вместо -1R)
    out["stop_quality"] = dict(
        # насколько близко к стопу подходили ПРИБЫЛЬНЫЕ сделки
        mae_wins_avg=_avg(mae_w), mae_wins_med=_med(mae_w),
        mae_wins_p75=_perc(mae_w, 0.75), mae_wins_p90=_perc(mae_w, 0.90),
        mae_wins_max=round(max(mae_w), 3) if mae_w else 0.0,
        wins_near_stop_n=sum(1 for v in mae_w if v >= NEAR_STOP_R),
        wins_near_stop_share=_share(sum(1 for v in mae_w if v >= NEAR_STOP_R),
                                    len(mae_w)),
        near_stop_thr_r=NEAR_STOP_R,
        # запас, который реально нужен прибыльным сделкам (в долях стопа)
        stop_headroom_r=round(1.0 - _perc(mae_w, 0.90), 3) if mae_w else 0.0,
        mae_losses_avg=_avg(mae_l),
        # сколько было в плюсе перед стопом у УБЫТОЧНЫХ
        mfe_losses_avg=_avg(mfe_l), mfe_losses_med=_med(mfe_l),
        mfe_losses_p90=_perc(mfe_l, 0.90),
        losses_in_profit_n=sum(1 for v in mfe_l if v >= IN_PROFIT_R),
        losses_in_profit_share=_share(
            sum(1 for v in mfe_l if v >= IN_PROFIT_R), len(mfe_l)),
        losses_in_profit_2r_n=sum(1 for v in mfe_l if v >= 2.0),
        in_profit_thr_r=IN_PROFIT_R,
        # стоп был слишком тесным: выбило, а потом дошло бы до тейка
        n_stop=len(stopped), n_tp=len(took), n_timeout=len(timed),
        stop_then_tp_n=len(late),
        stop_then_tp_share=_share(len(late), len(stopped)),
        stop_then_tp_lost_r=round(lost_r, 2),
        stop_then_tp_avg_lost_r=_avg([RR - t["r"] for t in late], 2),
        stop_then_tp_lost_usd=round(
            sum((RR - t["r"]) * MARGIN * lev * t["stop_pct"] / 100
                for t in late), 2),
        mae_hist_wins=_hist(mae_w, ((0.0, 0.25, "0..0.25R"),
                                    (0.25, 0.5, "0.25..0.5R"),
                                    (0.5, 0.75, "0.5..0.75R"),
                                    (0.75, 0.9, "0.75..0.9R"),
                                    (0.9, 1e18, "0.9R+"))),
        mfe_hist_losses=_hist(mfe_l, ((0.0, 0.25, "0..0.25R"),
                                      (0.25, 0.5, "0.25..0.5R"),
                                      (0.5, 1.0, "0.5..1R"),
                                      (1.0, 2.0, "1..2R"),
                                      (2.0, 1e18, "2R+"))),
    )

    # ------------------------------------------------------ КАЧЕСТВО ТЕЙКА
    extra = [t["mfe_after_tp_r"] for t in took]
    out["tp_quality"] = dict(
        n_tp=len(took),
        extra_avg_r=_avg(extra), extra_med_r=_med(extra),
        extra_p90_r=_perc(extra, 0.90),
        extra_max_r=round(max(extra), 3) if extra else 0.0,
        extra_sum_r=round(sum(extra), 2),
        tp_with_extra_1r_n=sum(1 for v in extra if v >= 1.0),
        tp_with_extra_1r_share=_share(sum(1 for v in extra if v >= 1.0),
                                      len(extra)),
        tp_with_extra_3r_n=sum(1 for v in extra if v >= 3.0),
        tp_no_extra_n=sum(1 for v in extra if v < 0.25),
        tp_no_extra_share=_share(sum(1 for v in extra if v < 0.25), len(extra)),
        extra_hist=_hist(extra, TP_EXTRA_BINS),
        # сколько R дал бы тот же набор тейков при RR = 3 + среднее продолжение
        potential_rr=round(RR + _avg(extra), 2) if extra else RR,
    )

    # ---------------------------------------------------------------- время
    def _hold(sub):
        return [t["hold_h"] for t in sub]

    out["timing"] = dict(
        hold_avg_h=_avg(_hold(tr), 1), hold_med_h=_med(_hold(tr), 1),
        hold_min_h=round(min(_hold(tr)), 1) if tr else 0.0,
        hold_max_h=round(max(_hold(tr)), 1) if tr else 0.0,
        hold_tp_avg_h=_avg(_hold(took), 1), hold_tp_med_h=_med(_hold(took), 1),
        hold_stop_avg_h=_avg(_hold(stopped), 1),
        hold_stop_med_h=_med(_hold(stopped), 1),
        hold_timeout_avg_h=_avg(_hold(timed), 1),
        time_to_mae_avg_h=_avg([t["time_to_mae_h"] for t in tr], 1),
        time_to_mae_med_h=_med([t["time_to_mae_h"] for t in tr], 1),
        time_to_mfe_avg_h=_avg([t["time_to_mfe_h"] for t in tr], 1),
        time_to_mfe_med_h=_med([t["time_to_mfe_h"] for t in tr], 1),
        time_to_mae_wins_avg_h=_avg([t["time_to_mae_h"] for t in win_t], 1),
        time_to_mfe_losses_avg_h=_avg([t["time_to_mfe_h"] for t in los_t], 1),
        hold_hist=_hist(_hold(tr), HOLD_BINS),
        hold_hist_tp=_hist(_hold(took), HOLD_BINS),
        hold_hist_stop=_hist(_hold(stopped), HOLD_BINS),
        bars_waited_avg=_avg([t.get("bars_waited") or 0 for t in tr], 2),
    )

    # ------------------------------------------------------- по режимам рынка
    pass_reg = r.get("pass_by_regime") or {}
    nm_reg = {}
    for x in nm:
        nm_reg[x["regime"]] = nm_reg.get(x["regime"], 0) + 1
    by_regime = {}
    total_pnl = sum(pnls)
    is_long = (r.get("setup") or "").endswith("long")
    for code, name in se2.REGIME_NAMES.items():
        if code == 1:
            mode = "range"                  # боковик — базовые пороги
        elif (code == 2) == is_long:
            mode = "counter"                # лонг в bear / шорт в bull
        else:
            mode = "with"                   # по тренду
        sub = [t for t in tr if t["regime"] == code]
        sr = [t["r"] for t in sub]
        sp = [t["pnl"] for t in sub]
        by_regime[name] = dict(
            regime=code, n=len(sub), wins=sum(1 for t in sub if t["pnl"] > 0),
            wr=_share(sum(1 for t in sub if t["pnl"] > 0), len(sub)),
            share_trades_pct=_share(len(sub), n),
            exp_r=_avg(sr), sum_r=round(sum(sr), 2),
            sum_usd=round(sum(sp), 3),
            ret_pct=round(sum(sp) / START * 100, 1),
            contrib_pct=round(sum(sp) / abs(total_pnl) * 100, 1)
            if total_pnl else 0.0,
            avg_mae_r=_avg([t["mae_r"] for t in sub]),
            avg_mfe_r=_avg([t["mfe_r"] for t in sub]),
            avg_hold_h=_avg([t["hold_h"] for t in sub], 1),
            n_tp=sum(1 for t in sub if t["reason"] == "tp"),
            n_stop=sum(1 for t in sub if t["reason"] in ("stop", "liq")),
            signals=pass_reg.get(code, 0),
            near=nm_reg.get(code, 0),
            trend_mode=mode, counter_trend=bool(mode == "counter"),
        )
    out["by_regime"] = by_regime

    # ------------------------------------------------------ по часам и дням
    hours = [dict(hour=h, n=0, wins=0, sum_r=0.0, sum_usd=0.0)
             for h in range(24)]
    wds = [dict(wd=i, name=WD_NAMES[i], n=0, wins=0, sum_r=0.0, sum_usd=0.0)
           for i in range(7)]
    for t in tr:
        dt = datetime.datetime.fromtimestamp(t["entry_ts"] / 1000.0,
                                             datetime.timezone.utc)
        for rec in (hours[dt.hour], wds[dt.weekday()]):
            rec["n"] += 1
            rec["wins"] += 1 if t["pnl"] > 0 else 0
            rec["sum_r"] += t["r"]
            rec["sum_usd"] += t["pnl"]
    for rec in hours + wds:
        rec["wr"] = _share(rec["wins"], rec["n"])
        rec["avg_r"] = round(rec["sum_r"] / rec["n"], 3) if rec["n"] else 0.0
        rec["sum_r"] = round(rec["sum_r"], 2)
        rec["sum_usd"] = round(rec["sum_usd"], 3)
    out["by_hour"] = hours
    out["by_weekday"] = wds

    # -------------------------------------------------------- распределение R
    out["r_hist"] = _hist(rs, R_BINS)

    # ------------------------------------------------------------- near-miss
    # гипотетика есть не всегда (например, конец 15м-серии) — считаем только
    # те near-miss, где сделка реально досчиталась до выхода
    hypo_ok = [x for x in nm if x["hypo_reason"] in ("tp", "stop", "liq",
                                                     "timeout")]
    hypo_ids = {id(x) for x in hypo_ok}
    nm_r = [x["hypo_r"] for x in hypo_ok]
    nm_wins = sum(1 for v in nm_r if v > 0)
    by_gate = {}
    for x in nm:
        rec = by_gate.setdefault(x["gate"], dict(
            gate=x["gate"], n=0, n_hypo=0, sum_r=0.0, wins=0,
            sum_margin=0.0, sum_need=0.0, sum_got=0.0))
        rec["n"] += 1
        rec["sum_margin"] += x["margin"]
        rec["sum_need"] += x["need"]
        rec["sum_got"] += x["got"]
        if id(x) in hypo_ids:
            rec["n_hypo"] += 1
            rec["sum_r"] += x["hypo_r"]
            rec["wins"] += 1 if x["hypo_r"] > 0 else 0
    gates_list = []
    for rec in by_gate.values():
        k = rec["n"]
        gates_list.append(dict(
            gate=rec["gate"], n=k, n_hypo=rec["n_hypo"],
            sum_r=round(rec["sum_r"], 2),
            avg_r=round(rec["sum_r"] / rec["n_hypo"], 3) if rec["n_hypo"]
            else 0.0,
            wins=rec["wins"], win_share=_share(rec["wins"], rec["n_hypo"]),
            avg_margin=round(rec["sum_margin"] / k, 4),
            avg_need=round(rec["sum_need"] / k, 4),
            avg_got=round(rec["sum_got"] / k, 4)))
    gates_list.sort(key=lambda z: -z["n"])
    top = sorted(hypo_ok, key=lambda z: -z["hypo_r"])

    def _nm_row(x):
        return dict(ts=x["ts"], ts_s=x["ts"] // 1000, gate=x["gate"],
                    got=x["got"], need=x["need"], margin=x["margin"],
                    hypo_r=x["hypo_r"], hypo_reason=x["hypo_reason"],
                    hypo_mfe_r=x["hypo_mfe_r"], regime=x["regime"],
                    regime_name=se2.REGIME_NAMES.get(x["regime"]),
                    rsi=x["rsi"], zone_pos=x["zone_pos"],
                    stop_pct=x.get("stop_pct"))

    nm_reasons = {}
    for x in nm:
        nm_reasons[x["hypo_reason"]] = nm_reasons.get(x["hypo_reason"], 0) + 1
    avg_nm_r = _avg(nm_r)
    out["near"] = dict(
        n=len(nm), n_hypo=len(hypo_ok),
        sum_r=round(sum(nm_r), 2), avg_r=avg_nm_r,
        med_r=_med(nm_r),
        wins=nm_wins, win_share=_share(nm_wins, len(hypo_ok)),
        sum_usd=round(sum(x["hypo_pnl"] for x in hypo_ok), 3),
        by_gate=gates_list,
        reasons=nm_reasons,
        best=[_nm_row(x) for x in top[:5]],
        worst=[_nm_row(x) for x in top[-3:][::-1]] if top else [],
        strict_justified=bool(avg_nm_r <= 0),
        verdict=("строгость порогов ОПРАВДАНА: near-miss в среднем "
                 f"{avg_nm_r:+.2f}R — пропущенные сетапы были убыточны"
                 if avg_nm_r <= 0 else
                 f"строгость порогов стоила {sum(nm_r):+.1f}R "
                 f"({avg_nm_r:+.2f}R на сетап на {len(hypo_ok)} случаях) — "
                 "пороги можно ослабить") if nm else "near-miss не найдено",
    )

    # --------------------------------------------------------------- отказы
    rc = dict(r["reject_counts"])
    bars = max(1, r.get("bars_eval") or 1)
    sig = r.get("signals") or 0
    gate_rows = [dict(name=k, n=v, share_bars_pct=_share(v, bars))
                 for k, v in rc.items() if k in se2.GATE_NAMES]
    gate_rows.sort(key=lambda z: -z["n"])
    exec_rows = [dict(name=k, n=v, share_signals_pct=_share(v, sig))
                 for k, v in rc.items() if k not in se2.GATE_NAMES]
    exec_rows.sort(key=lambda z: -z["n"])
    solo_rows = [dict(name=k, n=v, share_bars_pct=_share(v, bars))
                 for k, v in (r.get("gate_solo") or {}).items()]
    solo_rows.sort(key=lambda z: -z["n"])
    out["rejects"] = dict(
        bars_eval=r.get("bars_eval", 0), signals=sig, executed=n,
        signal_yield_pct=_share(n, sig),
        gates=gate_rows, gate_solo=solo_rows, exec=exec_rows,
        blocked_busy=r.get("blocked_busy", 0),
        blocked_busy_pct=_share(r.get("blocked_busy", 0), sig),
        blocked_cooldown=r.get("blocked_cooldown", 0),
        blocked_cooldown_pct=_share(r.get("blocked_cooldown", 0), sig),
        blocked_ruined=r.get("blocked_ruined", 0),
        blocked_ruined_pct=_share(r.get("blocked_ruined", 0), sig),
        top_gate=gate_rows[0]["name"] if gate_rows else None,
        top_gate_share_pct=gate_rows[0]["share_bars_pct"] if gate_rows else 0.0,
        worst_solo_gate=solo_rows[0]["name"] if solo_rows else None,
        worst_solo_share_pct=solo_rows[0]["share_bars_pct"] if solo_rows
        else 0.0,
    )

    out["warnings"] = _warnings(out, r)
    return out


# ------------------------------------------------- текстовые выводы «что не так»
def _warnings(s, r):
    """Готовые выводы по-русски. Только из уже посчитанных агрегатов."""
    w = []
    sq, tq, nr, rj = s["stop_quality"], s["tp_quality"], s["near"], s["rejects"]
    if s["n"] == 0:
        return ["Сделок нет: смотри блок rejects — какое ворото всё срезало."]
    if s["n"] < 25:
        w.append(f"Статистика тонкая: {s['n']} сделок (< 25) — выводы "
                 f"неустойчивы.")
    if s["tpm"] < 1.2:
        w.append(f"Редкие сделки: {s['tpm']:.2f} в месяц (норма фитнеса 1.2) — "
                 f"из {rj['signals']} сигналов исполнено {s['n']}.")
    if s["wr"] < s["wr_breakeven"]:
        w.append(f"WR {s['wr']}% НИЖЕ порога безубытка {s['wr_breakeven']}% "
                 f"(по фактическим средним {s['avg_win_r']}R прибыльной / "
                 f"{s['avg_loss_r']}R убыточной; чистая теория RR 1:{RR:.0f} — "
                 f"{s['wr_breakeven_rr']}%) — сетап математически убыточен.")
    if sq["stop_then_tp_share"] >= 30 and sq["stop_then_tp_n"] >= 3:
        w.append(f"Стоп слишком тесный: {sq['stop_then_tp_n']} из "
                 f"{sq['n_stop']} стопов ({sq['stop_then_tp_share']}%) дошли "
                 f"бы до тейка в окне удержания, недополучено "
                 f"{sq['stop_then_tp_lost_r']:.1f}R.")
    if sq["mae_wins_avg"] >= 0.7 and s["wins"] >= 3:
        w.append(f"Сужать стоп нельзя: прибыльные сделки в среднем уходили "
                 f"против позиции на {sq['mae_wins_avg']}R "
                 f"(90-й перцентиль {sq['mae_wins_p90']}R).")
    elif sq["mae_wins_p90"] <= 0.5 and s["wins"] >= 5:
        w.append(f"Стоп можно сузить: 90% прибыльных не проходили против "
                 f"позиции дальше {sq['mae_wins_p90']}R — запас "
                 f"{sq['stop_headroom_r']}R висит зря.")
    if sq["losses_in_profit_share"] >= 40 and sq["losses_in_profit_n"] >= 3:
        w.append(f"Отдаём прибыль: {sq['losses_in_profit_n']} убыточных "
                 f"({sq['losses_in_profit_share']}%) были в плюсе на "
                 f"{IN_PROFIT_R}R+ до стопа (в среднем "
                 f"{sq['mfe_losses_avg']}R).")
    if tq["n_tp"] >= 3 and tq["extra_avg_r"] >= 1.0:
        w.append(f"Тейк 1:{RR:.0f} рубит тренды: после тейка проходило ещё "
                 f"{tq['extra_avg_r']}R в среднем (медиана "
                 f"{tq['extra_med_r']}R, максимум {tq['extra_max_r']}R).")
    if tq["n_tp"] >= 3 and tq["tp_no_extra_share"] >= 60:
        w.append(f"Тейк на месте: у {tq['tp_no_extra_share']}% тейков цена "
                 f"дальше почти не шла (< 0.25R).")
    if s["outcomes"]["liq"]["n"]:
        w.append(f"ЛИКВИДАЦИИ: {s['outcomes']['liq']['n']} — стоп шире, чем "
                 f"позволяет плечо x{s['lev']}, ужимай stop_cap.")
    if r["ruined"]:
        w.append(f"Счёт слит: после слива пропущено {rj['blocked_ruined']} "
                 f"сигналов (в статистику они не входят).")
    if nr["n"] and not nr["strict_justified"]:
        w.append(f"Пороги строги: {nr['n']} near-miss дали бы "
                 f"{nr['sum_r']:+.1f}R ({nr['avg_r']:+.2f}R на сетап).")
    elif nr["n"] and nr["strict_justified"]:
        w.append(f"Строгость оправдана: {nr['n']} near-miss в среднем "
                 f"{nr['avg_r']:+.2f}R — ослаблять пороги нельзя.")
    if rj["blocked_busy_pct"] >= 25:
        w.append(f"Занятость съела {rj['blocked_busy']} сигналов "
                 f"({rj['blocked_busy_pct']}%) — одна позиция на сетап.")
    if rj["blocked_cooldown_pct"] >= 20:
        w.append(f"Кулдаун съел {rj['blocked_cooldown']} сигналов "
                 f"({rj['blocked_cooldown_pct']}%) — уменьшай cooldown.")
    if rj["worst_solo_share_pct"] >= 80:
        w.append(f"Ворото «{rj['worst_solo_gate']}» в одиночку режет "
                 f"{rj['worst_solo_share_pct']}% баров — главный ограничитель "
                 f"числа сделок.")
    tags = {"counter": "против тренда", "with": "по тренду",
            "range": "боковик"}
    for name, rec in s["by_regime"].items():
        if rec["n"] >= 5 and rec["exp_r"] <= -0.3:
            tag = tags.get(rec["trend_mode"], "")
            w.append(f"Режим {name} ({tag}) убыточен: {rec['n']} сделок, "
                     f"WR {rec['wr']}%, {rec['exp_r']:+.2f}R на сделку "
                     f"({rec['sum_usd']:+.2f}$ вклад) — стоит запретить или "
                     f"ужесточить.")
    for rec in rj["exec"]:
        if rec["share_signals_pct"] >= 20:
            w.append(f"Исполнение теряет сигналы: «{rec['name']}» "
                     f"{rec['n']} раз ({rec['share_signals_pct']}% сигналов).")
    return w


# --------------------------------------------------------- перечень ключей
KEYS_DOC = {
    "база": ["setup", "lev", "ruined", "months", "n", "wins", "losses", "wr",
             "wr_breakeven", "wr_breakeven_rr", "tpm", "sum_r", "exp_r",
             "exp_usd", "pnl_usd",
             "pf", "gross_profit", "gross_loss", "avg_win_r", "med_win_r",
             "avg_loss_r", "med_loss_r", "avg_win_usd", "avg_loss_usd",
             "best_r", "worst_r", "best_usd", "worst_usd", "max_win_streak",
             "max_loss_streak", "start_usd", "margin_usd", "balance_end",
             "ret_pct", "dd_pct", "avg_stop_pct", "med_stop_pct",
             "avg_atr_pct", "avg_risk_usd", "period_start", "period_end"],
    "кривые": ["reinvest", "equity", "drawdown", "monthly", "by_month"],
    "исходы": ["outcomes"],
    "качество": ["stop_quality", "tp_quality"],
    "время": ["timing", "by_hour", "by_weekday"],
    "срезы": ["by_regime", "r_hist"],
    "диагностика": ["near", "rejects", "warnings"],
}


def print_report(s, prefix=""):
    """Короткий текстовый отчёт в консоль (для проверки и логов)."""
    p = prefix
    print(f"{p}сделок {s['n']} | WR {s['wr']}% (безубыток {s['wr_breakeven']}%)"
          f" | {s['exp_r']:+.3f}R/сд | сумма {s['sum_r']:+.1f}R | PF {s['pf']}")
    print(f"{p}итог фикс-база {s['ret_pct']:+.1f}% (DD {s['dd_pct']}%) | "
          f"реинвест ${s['reinvest']['final_usd']} из ${SLEEVE0} "
          f"({s['reinvest']['final_pct']:+.1f}%, DD "
          f"{s['reinvest']['max_dd_pct']}%) | месяцев плюсовых "
          f"{s['reinvest']['months_pos']}/{s['reinvest']['months_total']}")
    o = s["outcomes"]
    print(f"{p}исходы: tp {o['tp']['n']} ({o['tp']['sum_r']:+.1f}R) | "
          f"stop {o['stop']['n']} ({o['stop']['sum_r']:+.1f}R) | "
          f"liq {o['liq']['n']} | timeout {o['timeout']['n']} "
          f"({o['timeout']['sum_r']:+.1f}R)")
    sq = s["stop_quality"]
    print(f"{p}стоп: MAE прибыльных ср {sq['mae_wins_avg']}R "
          f"мед {sq['mae_wins_med']}R p90 {sq['mae_wins_p90']}R | "
          f"MFE убыточных ср {sq['mfe_losses_avg']}R | тесных стопов "
          f"{sq['stop_then_tp_n']}/{sq['n_stop']} "
          f"({sq['stop_then_tp_share']}%, -{sq['stop_then_tp_lost_r']:.1f}R)")
    tq = s["tp_quality"]
    print(f"{p}тейк: после TP ещё ср {tq['extra_avg_r']}R мед "
          f"{tq['extra_med_r']}R макс {tq['extra_max_r']}R | "
          f"с продолжением >=1R {tq['tp_with_extra_1r_n']}/{tq['n_tp']}")
    t = s["timing"]
    print(f"{p}время: удержание ср {t['hold_avg_h']}ч (tp {t['hold_tp_avg_h']}ч,"
          f" stop {t['hold_stop_avg_h']}ч) | до MAE {t['time_to_mae_avg_h']}ч, "
          f"до MFE {t['time_to_mfe_avg_h']}ч")
    lbl = {"counter": "против", "with": "по тренду", "range": "боковик"}
    for name, rec in s["by_regime"].items():
        tag = lbl.get(rec["trend_mode"], "")
        print(f"{p}  {name:5} ({tag:9}): сдел {rec['n']:3} WR {rec['wr']:5.1f}%"
              f" {rec['exp_r']:+.2f}R вклад {rec['sum_usd']:+7.2f}$ "
              f"сигналов {rec['signals']:4} near {rec['near']:4}")
    nr = s["near"]
    print(f"{p}near-miss {nr['n']} (гипо {nr['n_hypo']}): {nr['sum_r']:+.1f}R, "
          f"ср {nr['avg_r']:+.2f}R, плюсовых {nr['wins']} "
          f"({nr['win_share']}%) -> {nr['verdict']}")
    rj = s["rejects"]
    print(f"{p}бары {rj['bars_eval']} -> сигналы {rj['signals']} -> сделки "
          f"{rj['executed']} ({rj['signal_yield_pct']}%) | busy "
          f"{rj['blocked_busy']} | cool {rj['blocked_cooldown']} | ruined "
          f"{rj['blocked_ruined']}")
    if rj["gates"]:
        top = " | ".join(f"{x['name']} {x['share_bars_pct']}%"
                         for x in rj["gates"][:4])
        print(f"{p}первое непройденное ворото: {top}")
    for line in s["warnings"]:
        print(f"{p}  ! {line}")
