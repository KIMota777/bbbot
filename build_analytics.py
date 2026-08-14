# -*- coding: utf-8 -*-
"""Считает аналитику для страниц ботов и сигналов:
  - кривая капитала с реинвестом от $50 (маржа = 25% капитала, как в отборе);
  - кривая просадки (% от пика капитала);
  - помесячные бары доходности (% на фикс-базе $20 — сопоставимо со всеми
    прежними отчётами);
  - статистика "убытки к доходам": profit factor, средняя прибыльная /
    убыточная сделка, лучшая/худшая, макс. серии, лучший/худший месяц.

Для 5 финальных ботов и рабочих сигнальных сетапов.
Результат: webapp/data/analytics_<key>.json (key: bot_<SYM> | sig_<name>).
Переиспользует функции build_pnl_curves — данные гарантированно совпадают
со страницей /pnl.
"""

import json
import os
from collections import defaultdict

import build_pnl_curves as bpc
import config
import evolution as ev
import ext_data as xd
import signal_engine as se

OUT_DIR = os.path.join("webapp", "data")
SLEEVE0 = 50.0
BT_BASE = 20.0
MONTH = 30 * 86400


def analyze(t0, pnls, extra=None):
    """pnls: [(ts_сек, pnl_$5маржи, худшая_плавающая_точка_цикла)].

    Третье поле приходит из движка (событие close, ключ `worst`) и всегда
    <= min(0, pnl). Оно даёт вторую кривую просадки — с переоценкой ОТКРЫТЫХ
    циклов. Прежние ключи (`drawdown`, `stats.max_dd`) считаются ровно как
    раньше: их читает сайт, и менять смысл уже опубликованной метрики нельзя.

    `stats.max_dd_float` здесь — то же, что build_pnl_curves.dd_float_closed_peak:
    дно плавающее, а ПИК берётся только по закрытым сделкам. Это НЕ движковая
    evolution2.run5.max_dd_mtm, и различий сразу два: там пик поднимает и
    нереализованная прибыль (здесь поднимать нечем — из событий сделок
    известна только ХУДШАЯ точка цикла), и там нет реинвеста (фиксированная
    база $20 против растущего капитала от $50). Поэтому числа расходятся в
    обе стороны (LTC 30.8% здесь против 25.3% в движке, DOGE 60.4% против
    73.9%) и сравнивать их между собой нельзя — движковый лежит отдельно, в
    `stats.engine`.
    """
    eq, peak = SLEEVE0, SLEEVE0
    equity = [[t0, round(SLEEVE0, 2)]]
    raw_equity = [SLEEVE0]       # тот же капитал без округления — для просадки
    drawdown = [[t0, 0.0]]
    drawdown_float = [[t0, 0.0]]
    max_dd_float = 0.0
    monthly = defaultdict(float)
    wins, losses = [], []
    flat = 0
    streak, max_win_streak, max_loss_streak = 0, 0, 0

    for ts, pnl, worst in pnls:
        # худшая точка цикла наступает ДО его закрытия, поэтому меряется от
        # пика, накопленного предыдущими сделками
        dd_low = (peak - eq * (1 + worst / BT_BASE)) / peak
        eq *= (1 + pnl / BT_BASE)
        peak = max(peak, eq)
        max_dd_float = max(max_dd_float, dd_low)
        equity.append([ts, round(eq, 2)])
        raw_equity.append(eq)
        drawdown.append([ts, round(-(peak - eq) / peak * 100, 2)])
        drawdown_float.append([ts, round(-max(dd_low, (peak - eq) / peak) * 100, 2)])
        monthly[(ts - t0) // MONTH] += pnl
        # Разбор ИСЧЕРПЫВАЮЩИЙ: wins + losses + flat == trades всегда.
        # Раньше третьей корзины не было, и сделка ровно в ноль не попадала
        # никуда: у SOL выходило 911 + 46 = 957 при 958 сделках. Ноль тут
        # берётся не с потолка — движок округляет pnl события до 4 знаков
        # (сотая доля цента), и цикл, вынесенный стопом в безубыток, может
        # дать |pnl| < 0.00005. Такие циклы есть и у SOL, и у DOGE — по одному.
        if pnl > 0:
            wins.append(pnl)
            streak = streak + 1 if streak >= 0 else 1
            max_win_streak = max(max_win_streak, streak)
        elif pnl < 0:
            losses.append(pnl)
            streak = streak - 1 if streak <= 0 else -1
            max_loss_streak = max(max_loss_streak, -streak)
        else:
            # Ни в прибыльные, ни в убыточные: серию не рвём и не продолжаем —
            # у сделки нет знака, а выдумывать его нельзя.
            flat += 1

    n_months = max(monthly) + 1 if monthly else 1
    monthly_pts = []
    for m in range(n_months):
        pct = monthly.get(m, 0.0) / BT_BASE * 100
        monthly_pts.append([t0 + m * MONTH, round(pct, 2)])
    month_vals = [p[1] for p in monthly_pts]

    gross_p, gross_l = sum(wins), -sum(losses)
    stats = dict(
        final_usd=round(eq, 2),
        final_pct=round((eq / SLEEVE0 - 1) * 100, 1),
        max_dd=round(max((-d[1] for d in drawdown), default=0), 1),
        max_dd_float=round(max_dd_float * 100, 1),
        trades=len(pnls), wins=len(wins), losses=len(losses),
        wr=round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
        profit_factor=round(gross_p / gross_l, 2) if gross_l > 0 else None,
        gross_profit=round(gross_p, 2), gross_loss=round(gross_l, 2),
        avg_win=round(gross_p / len(wins), 3) if wins else 0,
        avg_loss=round(-gross_l / len(losses), 3) if losses else 0,
        best_trade=round(max(wins), 3) if wins else 0,
        worst_trade=round(min(losses), 3) if losses else 0,
        max_win_streak=max_win_streak, max_loss_streak=max_loss_streak,
        best_month=round(max(month_vals), 2) if month_vals else 0,
        worst_month=round(min(month_vals), 2) if month_vals else 0,
        months_pos=sum(1 for v in month_vals if v > 0),
        months_total=len(month_vals))
    if extra:
        stats.update(extra)
    return dict(equity=bpc.downsample(equity),
                drawdown=bpc.downsample(drawdown),
                drawdown_float=bpc.downsample(drawdown_float),
                monthly=monthly_pts, stats=stats)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    pct5 = xd.fetch_daily_pct5()

    for sym, modes in config.SYMBOL_PARAMS.items():
        p = modes.get("final")
        if not p:
            continue
        print(f"bot_{sym}...")
        t0, pnls, meta = bpc.bot_pnls(sym, p, pct5)
        if meta["ruined"]:
            print(f"  ВНИМАНИЕ: счёт слит на {meta['ruined_trade']}-й сделке")
        # Признак слива обязан доехать до файла: движок обрывает прогон, когда
        # капитала не хватает на очередной цикл, и всё, что ниже (доходность,
        # winrate, месяцы), относится только к участку ДО слива. Сайт читает
        # его из stats.ruined (webapp/app.py: ruined_flag).
        data = analyze(t0, pnls, extra=dict(
            lev=p.get("lev", 5), ruined=meta["ruined"],
            ruined_trade=meta["ruined_trade"], ruined_ts=meta["ruined_ts"],
            # Итог САМОГО движка — отдельным гнездом, чтобы его нельзя было
            # перепутать с числами выше: там реинвест от $50, здесь
            # фиксированная база $20 и маржа $5, как в отборе. На оборванном
            # прогоне разница особенно велика (SOL: -79.6% против -60.1%).
            engine=dict(base_usd=20.0, reinvest=False,
                        ret_pct=meta["ret_flat"],
                        max_dd=meta["dd_closed_flat"],
                        max_dd_mtm=meta["max_dd_mtm"],
                        trades=meta["trades"], wins=meta["wins"])))
        with open(os.path.join(OUT_DIR, f"analytics_bot_{sym}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)

    with open("signal_setups.json", encoding="utf-8") as fh:
        setups = json.load(fh)
    c4 = ev.fetch("BTCUSDT", "240", 1150)
    c15 = ev.fetch("BTCUSDT", "15", 1150)
    ts15 = [c[0] for c in c15]
    ctx = se.prep_context(c4)
    for name, rec in setups.items():
        if not rec.get("enabled"):
            continue
        print(f"sig_{name}...")
        r = se.run_setup(name, rec["genome"], c4, ctx, c15, ts15,
                         rec.get("rec_lev", 15))
        t0 = c4[0][0] // 1000
        # у сигналов внутрицикловой переоценки нет — см. bpc.signal_pnls
        pnls = [(t["exit_ts"] // 1000, t["pnl"], min(t["pnl"], 0.0))
                for t in r["trades"]]
        reasons = defaultdict(int)
        holds = []
        for t in r["trades"]:
            reasons[t["reason"]] += 1
            holds.append(t["hold_h"])
        # у сигнального движка признака слива нет — ставим False явно, чтобы
        # отсутствие ключа не читалось как «не проверяли»
        extra = dict(lev=rec.get("rec_lev", 15), ruined=False,
                     n_tp=reasons.get("tp", 0), n_stop=reasons.get("stop", 0),
                     n_timeout=reasons.get("timeout", 0),
                     n_liq=reasons.get("liq", 0),
                     avg_hold_h=round(sum(holds) / len(holds), 1) if holds else 0)
        data = analyze(t0, pnls, extra=extra)
        with open(os.path.join(OUT_DIR, f"analytics_sig_{name}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)

    print("-> webapp/data/analytics_*.json")


if __name__ == "__main__":
    main()
