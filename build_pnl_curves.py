# -*- coding: utf-8 -*-
"""Строит кривые капитала для страницы /pnl — реальная картина С РЕИНВЕСТОМ.

Модель (по условию владельца): у КАЖДОЙ стратегии свой стартовый капитал
$50 (5 ботов + 2 рабочих сигнальных сетапа = 7 линий, суммарно $350).
Маржа сделки = 25% текущего капитала стратегии — та же доля, что в отборе
($5 от $20), поэтому риск на сделку в % не меняется, а PnL масштабируется
точно (объём/комиссии/фандинг/ликвидация линейны от маржи):
    капитал *= (1 + pnl_сделки / 20)

Портфель — две линии от $350:
  - «без ребаланса»: сумма семи независимых капиталов;
  - «ежемесячный ребаланс»: капитал общий, в конце месяца прибыль всех
    распределяется на всех (r_мес = суммарный PnL / (7 x $20-база)).
    Даёт бонус диверсификации; умножает и ошибку бэктеста — смотри обе.

Результат: webapp/data/pnl_curves.json. Пересчёт после смены конфигов.
"""

import json
import os
import time
from collections import defaultdict

import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd
import signal_engine2 as se   # движок сигналов v2 (геномы v10)

OUT = os.path.join("webapp", "data", "pnl_curves.json")
MAX_POINTS = 1600
SLEEVE0 = 50.0       # стартовый капитал КАЖДОЙ стратегии
BT_BASE = 20.0       # база бэктеста: $20 капитала, $5 маржи (25%)
MONTH = 30 * 86400

COLORS = {
    "DOGEUSDT": "#f0b90b", "LTCUSDT": "#4aa8ff", "BTCUSDT": "#ff7a30",
    "ETHUSDT": "#c88cff", "SOLUSDT": "#46C186",
    # сигнальные сетапы (после отбора v10 их рабочих пять)
    "range_long": "#7ee0d2", "range_short": "#4fc3a1",
    "sweep_long": "#ffb86c", "sweep_short": "#ff79c6",
    "dump_long": "#8be9fd", "pump_short": "#e056a2",
    "bounce_short": "#e056a2", "rally_short": "#b48ead",
}
# бенчмарки "купил на $50 и держал" (yfinance, дневные закрытия)
BENCHMARKS = [
    ("bench_gold", "Золото — холд $50", "GC=F", "#d4af37"),
    ("bench_silver", "Серебро — холд $50", "SI=F", "#a8b0bd"),
    ("bench_spx", "S&P 500 — холд $50", "^GSPC", "#6f8ef2"),
]
RU_SETUPS = {
    "range_short": "Сигналы: шорт от верха боковика",
    "sweep_long": "Сигналы: ложный пробой низа",
    "sweep_short": "Сигналы: ложный пробой верха",
    "dump_long": "Сигналы: лонг после обвала",
    "bounce_short": "Сигналы: нож→откат, шорт",
    "rally_short": "Сигналы: тренд-шорт",
    "range_long": "Сигналы: лонг от низа боковика",
    "pump_short": "Сигналы: шорт после пампа",
}


def holdout_start_ts():
    """Момент начала честного экзамена (в секундах).

    Берём из bots_honest.json — там граница зафиксирована один раз и
    используется всеми проверками проекта; если файла нет, считаем как
    везде: последние 28% истории.
    """
    p = os.path.join("webapp", "data", "bots_honest.json")
    try:
        with open(p, encoding="utf-8") as fh:
            per = json.load(fh)["periods"]["holdout"]["start"]
        return int(time.mktime(time.strptime(per, "%Y-%m-%d")))
    except (OSError, ValueError, KeyError):
        c = ev.fetch("BTCUSDT", "240", 1150)
        return c[int(len(c) * 0.72)][0] // 1000


def dedup(points):
    """Схлопываем точки с ОДИНАКОВЫМ временем, оставляя последнее значение.

    Две сделки могут закрыться в одну и ту же секунду (особенно в
    портфельной линии, где потоки складываются) — и тогда в серии появляются
    два значения на один ts. Библиотека графиков на этом падает с ошибкой
    сортировки и обрывает ВЕСЬ скрипт страницы: график, легенда и плитки
    просто не отрисовываются. Поэтому дедупликация обязательна.
    """
    out = []
    for ts, v in points:
        if out and out[-1][0] == ts:
            out[-1] = [ts, v]
        else:
            out.append([ts, v])
    return out


def downsample(points):
    points = dedup(points)
    if len(points) <= MAX_POINTS:
        return points
    step = len(points) / MAX_POINTS
    out = [points[int(i * step)] for i in range(MAX_POINTS)]
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def bot_pnls(sym, p, pct5):
    """[(ts_сек, pnl_в_$5-масштабе), ...] по закрытым сделкам бота."""
    interval = str(p.get("interval", "15"))
    bars_per_day = max(4, 1440 // int(interval))
    g = e7.cfg_to_genome(p, "final")
    for k, v in e8.OFF8.items():
        g.setdefault(k, v)
    candles = ev.fetch(sym, interval, 1150)
    aux = e8.make_aux_builder(pct5, bars_per_day)(sym, candles)
    pre = e2.prep(candles)
    filt = e8.make_filter8(g, aux)
    old_lev, old_bpd = e2.LEV, e2.BARS_PER_DAY
    e2.LEV = p.get("lev", 5)
    e2.BARS_PER_DAY = bars_per_day
    try:
        events = []
        e2.run5(candles, pre, g, entry_filter=filt, events=events)
    finally:
        e2.LEV, e2.BARS_PER_DAY = old_lev, old_bpd
    start_ts = candles[0][0] // 1000
    return start_ts, [(e_["t"] // 1000, e_["pnl"]) for e_ in events
                      if e_["type"] == "close"]


def signal_pnls(key, rec, c15, ts15, ctx_cache):
    """key может быть мульти-ТФ ('bounce_short@60'); свечи и контекст — ТФ
    конфига."""
    setup = key.split("@")[0]
    iv = int(rec.get("interval_min") or (key.split("@")[1] if "@" in key
                                         else 240))
    if iv not in ctx_cache:
        cc = ev.fetch("BTCUSDT", str(iv), 1150)
        ctx_cache[iv] = (cc, se.prep_context(cc, interval_min=iv))
    cc, ctx = ctx_cache[iv]
    r = se.run_setup(setup, rec["genome"], cc, ctx, c15, ts15,
                     rec.get("rec_lev", 15), collect_diag=False)
    start_ts = cc[0][0] // 1000
    return start_ts, [(t["exit_ts"] // 1000, t["pnl"]) for t in r["trades"]]


def dd_of(points):
    peak, dd = points[0][1], 0.0
    for _, v in points:
        peak = max(peak, v)
        if peak > 0:
            dd = max(dd, (peak - v) / peak)
    return round(dd * 100, 1)


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    pct5 = xd.fetch_daily_pct5()

    # (key, label, color, group, start_ts, [(ts, pnl)], в_портфель)
    streams = []
    for sym, modes in config.SYMBOL_PARAMS.items():
        p = modes.get("final")
        if not p:
            continue
        print(f"{sym}: сделки бота (x{p.get('lev', 5)})...")
        st, pnls = bot_pnls(sym, p, pct5)
        streams.append((f"bot_{sym}",
                        f"{sym.replace('USDT','')} — бот x{p.get('lev', 5)}",
                        COLORS.get(sym, "#aaaaaa"), "bot", st, pnls, True))

    # сетапы v2 (отбор v10) если готовы, иначе старые v1
    setups_file = ("signal_setups2.json" if os.path.exists("signal_setups2.json")
                   else "signal_setups.json")
    print(f"сетапы: {setups_file}")
    with open(setups_file, encoding="utf-8") as fh:
        setups = json.load(fh)
    c15 = ev.fetch("BTCUSDT", "15", 1150)
    ts15 = [c[0] for c in c15]
    ctx_cache = {}
    # Сигналы показываем ВСЕ — для сравнения с ботами и бенчмарками, но
    # неподтверждённые рисуем пунктиром и НЕ включаем в портфель: портфельные
    # линии должны состоять только из того, что прошло проверку, иначе
    # «капитал портфеля» надувается тем, чему мы сами не верим.
    for key, rec in sorted(setups.items()):
        setup = key.split("@")[0]
        iv = int(rec.get("interval_min") or (key.split("@")[1] if "@" in key
                                             else 240))
        tf = "4ч" if iv == 240 else "1ч"
        enabled = bool(rec.get("enabled"))
        watch = bool(rec.get("watch"))
        mark = ("" if enabled else
                (" · наблюдение" if watch else " · не подтверждён"))
        print(f"{key}: сделки сигналов (x{rec.get('rec_lev', 15)}, {tf})"
              f"{mark}...")
        st, pnls = signal_pnls(key, rec, c15, ts15, ctx_cache)
        streams.append((f"sig_{key}",
                        f"{RU_SETUPS.get(setup, setup)} {tf} "
                        f"x{rec.get('rec_lev', 15)}{mark}",
                        COLORS.get(setup, "#dddddd"), "signal", st, pnls,
                        enabled))

    # в портфель идут боты и ПОДТВЕРЖДЁННЫЕ сигналы
    in_pf = [s for s in streams if s[3] == "bot" or s[6]]
    n = len(in_pf)
    t0 = min(s[4] for s in streams)
    total0 = SLEEVE0 * n

    # ГРАНИЦА ЭКЗАМЕНА: всё, что левее, — период, на котором параметры и
    # подбирались, поэтому кривая там нарисована задним числом. Строим ВТОРОЙ
    # набор точек, начинающийся ровно на границе: капитал снова стартует с
    # $50, и итог показывает, что стратегия заработала бы, если бы её
    # включили в момент окончания подбора. Это единственная цифра, которую
    # честно называть результатом.
    hold_ts = holdout_start_ts()
    print(f"граница экзамена: {time.strftime('%Y-%m-%d', time.gmtime(hold_ts))}")

    def curve(pnls, since=None):
        """(точки, итог$, итог%, просадка%) — капитал от $50 с момента since."""
        start = since if since is not None else t0
        eq = SLEEVE0
        pts = [[start, round(SLEEVE0, 2)]]
        for ts, pnl in pnls:
            if since is not None and ts < since:
                continue
            eq *= (1 + pnl / BT_BASE)
            pts.append([ts, round(eq, 2)])
        return (downsample(pts), round(eq, 2),
                round((eq / SLEEVE0 - 1) * 100, 1), dd_of(pts))

    series = []
    for key, label, color, group, st, pnls, pf in streams:
        pts, fin, fpct, dd = curve(pnls)
        h_pts, h_fin, h_fpct, h_dd = curve(pnls, since=hold_ts)
        n_hold = sum(1 for ts, _ in pnls if ts >= hold_ts)
        series.append(dict(
            key=key, label=label, group=group, color=color,
            final_usd=fin, final_pct=fpct, dd=dd, points=pts,
            # честная часть: только данные, которых подбор не видел
            honest_usd=h_fin, honest_pct=h_fpct, honest_dd=h_dd,
            honest_points=h_pts, honest_trades=n_hold,
            # пунктир = не прошло проверку, в портфель не входит
            dashed=(not pf), in_portfolio=pf))

    # события ПОДТВЕРЖДЁННЫХ стратегий по времени — для портфельных линий
    merged = []
    for key, label, color, group, st, pnls, pf in in_pf:
        for ts, pnl in pnls:
            merged.append((ts, key, pnl))
    merged.sort()

    # портфель без ребаланса: сумма независимых капиталов
    def pf_curve(since=None):
        start = since if since is not None else t0
        sl = {s[0]: SLEEVE0 for s in in_pf}
        pts = [[start, round(total0, 2)]]
        for ts, key, pnl in merged:
            if since is not None and ts < since:
                continue
            sl[key] *= (1 + pnl / BT_BASE)
            pts.append([ts, round(sum(sl.values()), 2)])
        return pts

    pts_cons = pf_curve()
    h_cons = pf_curve(since=hold_ts)
    series.append(dict(
        key="pf_cons", label=f"ПОРТФЕЛЬ ${total0:.0f} — без ребаланса",
        group="portfolio", color="#e8e6df",
        final_usd=pts_cons[-1][1],
        final_pct=round((pts_cons[-1][1] / total0 - 1) * 100, 1),
        dd=dd_of(pts_cons), points=downsample(pts_cons),
        honest_usd=h_cons[-1][1],
        honest_pct=round((h_cons[-1][1] / total0 - 1) * 100, 1),
        honest_dd=dd_of(h_cons), honest_points=downsample(h_cons)))

    # портфель с ежемесячным ребалансом: r_мес = сумм. PnL / (n x $20-база)
    monthly = defaultdict(float)
    for ts, key, pnl in merged:
        monthly[ts // MONTH] += pnl
    eq = total0
    def reb_curve(since=None):
        e = total0
        start = since if since is not None else t0
        pts = [[start, round(e, 2)]]
        for m in sorted(monthly):
            ts = (m + 1) * MONTH
            if since is not None and ts < since:
                continue
            e *= (1 + monthly[m] / (n * BT_BASE))
            pts.append([ts, round(e, 2)])
        return pts

    pts_reb = reb_curve()
    h_reb = reb_curve(since=hold_ts)
    series.append(dict(
        key="pf_reb", label=f"ПОРТФЕЛЬ ${total0:.0f} — ежемесячный ребаланс",
        group="portfolio", color="#E3A83E",
        final_usd=pts_reb[-1][1],
        final_pct=round((pts_reb[-1][1] / total0 - 1) * 100, 1),
        dd=dd_of(pts_reb), points=downsample(pts_reb),
        honest_usd=h_reb[-1][1],
        honest_pct=round((h_reb[-1][1] / total0 - 1) * 100, 1),
        honest_dd=dd_of(h_reb), honest_points=downsample(h_reb)))

    # бенчмарки: золото / серебро / S&P 500 как холд от $50 с той же даты
    try:
        import yfinance as yf
        for key, label, ticker, color in BENCHMARKS:
            df = yf.download(ticker, period="4y", interval="1d",
                             progress=False)
            closes = [float(x) for x in df["Close"].values.ravel()]
            times = [int(t.timestamp()) for t in df.index]
            rows = [(ts, c) for ts, c in zip(times, closes) if ts >= t0]
            if len(rows) < 50:
                print(f"  {label}: мало данных, пропущен")
                continue
            base = rows[0][1]
            pts = [[ts, round(SLEEVE0 * c / base, 2)] for ts, c in rows]
            # бенчмарк на честном отрезке считаем от той же границы, иначе
            # сравнение было бы нечестным: стратегия с сентября, а «купил и
            # держал» — с самого начала истории
            h_rows = [(ts, c) for ts, c in rows if ts >= hold_ts]
            h_pts = ([[ts, round(SLEEVE0 * c / h_rows[0][1], 2)]
                      for ts, c in h_rows] if h_rows else pts[-1:])
            series.append(dict(
                key=key, label=label, group="bench", color=color,
                final_usd=pts[-1][1],
                final_pct=round((pts[-1][1] / SLEEVE0 - 1) * 100, 1),
                dd=dd_of(pts), points=downsample(pts),
                honest_usd=h_pts[-1][1],
                honest_pct=round((h_pts[-1][1] / SLEEVE0 - 1) * 100, 1),
                honest_dd=dd_of(h_pts), honest_points=downsample(h_pts)))
    except Exception as e:
        print(f"  бенчмарки пропущены: {e}")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dict(sleeve=SLEEVE0, total=total0, series=series,
                       holdout_from=hold_ts),
                  fh, ensure_ascii=False)
    for s in series:
        print(f"  {s['label']:44} ${s['final_usd']:>9.2f} "
              f"({s['final_pct']:+8.1f}%) DD {s['dd']}%")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
