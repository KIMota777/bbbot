# -*- coding: utf-8 -*-
"""Строит кривые капитала для страницы /pnl — реальная картина С РЕИНВЕСТОМ.

Модель (по условию владельца): у КАЖДОЙ стратегии свой стартовый капитал
$50. Сколько всего линий и, значит, каков старт портфеля — считается по
ФАКТИЧЕСКОМУ составу (total0 = SLEEVE0 * n), а не пишется числом: сигнальные
сетапы включаются и выключаются приёмкой, и на сегодня не включён ни один,
поэтому линий пять (5 ботов) и старт $250, а не $350, как было при двух
рабочих сетапах.
Маржа сделки = 25% текущего капитала стратегии — та же доля, что в отборе
($5 от $20), поэтому риск на сделку в % не меняется, а PnL масштабируется
точно (объём/комиссии/фандинг/ликвидация линейны от маржи):
    капитал *= (1 + pnl_сделки / 20)

Портфель — две линии от total0:
  - «без ребаланса»: сумма независимых капиталов всех линий;
  - «ежемесячный ребаланс»: капитал общий, в конце месяца прибыль всех
    распределяется на всех (r_мес = суммарный PnL / (n x $20-база)).
    Даёт бонус диверсификации; умножает и ошибку бэктеста — смотри обе.

Результат: webapp/data/pnl_curves.json. Пересчёт после смены конфигов.
"""

import json
import os
from collections import defaultdict

import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import evolution12 as e12
import ext_data as xd
import signal_engine as se

OUT = os.path.join("webapp", "data", "pnl_curves.json")
MAX_POINTS = 1600
SLEEVE0 = 50.0       # стартовый капитал КАЖДОЙ стратегии
BT_BASE = 20.0       # база бэктеста: $20 капитала, $5 маржи (25%)
MONTH = 30 * 86400

COLORS = {
    "DOGEUSDT": "#f0b90b", "LTCUSDT": "#4aa8ff", "BTCUSDT": "#ff7a30",
    "ETHUSDT": "#c88cff", "SOLUSDT": "#46C186",
    "pump_short": "#e056a2", "range_long": "#7ee0d2",
}
# бенчмарки "купил на $50 и держал" (yfinance, дневные закрытия)
BENCHMARKS = [
    ("bench_gold", "Золото — холд $50", "GC=F", "#d4af37"),
    ("bench_silver", "Серебро — холд $50", "SI=F", "#a8b0bd"),
    ("bench_spx", "S&P 500 — холд $50", "^GSPC", "#6f8ef2"),
]
RU_SETUPS = {
    "range_long": "Сигналы: лонг от низа боковика",
    "pump_short": "Сигналы: шорт после пампа",
}


def downsample(points):
    if len(points) <= MAX_POINTS:
        return points
    step = len(points) / MAX_POINTS
    out = [points[int(i * step)] for i in range(MAX_POINTS)]
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def bot_pnls(sym, p, pct5):
    """(t0, [(ts_сек, pnl_в_$5-масштабе, худшая_плавающая_точка_цикла)], meta).

    Третье поле — то, сколько цикл показывал в минусе на экране в худший
    момент удержания (движок пишет его в событие close как `worst`, всегда
    <= min(0, pnl)). Без него просадка считалась только по ЗАКРЫТЫМ сделкам,
    и цикл, неделю просидевший на -90% маржи и вышедший в плюс по тейку, в
    неё не попадал вообще — а именно по просадке выбиралось плечо.

    meta несёт признак СЛИВА: движок обрывает прогон, как только капитала не
    хватает на очередной цикл (balance < MARGIN), и дальше сделок просто нет.
    Без этого признака страница показывала «-60.1%» по оборванному прогону —
    то есть самый важный для владельца факт терялся по дороге к сайту.
    """
    interval = str(p.get("interval", "15"))
    bars_per_day = max(4, 1440 // int(interval))
    g = e7.cfg_to_genome(p, "final")
    for k, v in e8.OFF8.items():
        g.setdefault(k, v)
    candles = ev.fetch(sym, interval, 1150)
    aux = e12.make_aux_builder(pct5, bars_per_day)(sym, candles)
    pre = e2.prep(candles)
    filt = e12.make_filter12(g, aux)
    old_lev, old_bpd = e2.LEV, e2.BARS_PER_DAY
    e2.LEV = p.get("lev", 5)
    e2.BARS_PER_DAY = bars_per_day
    try:
        events = []
        r = e2.run5(candles, pre, g, entry_filter=filt, events=events)
    finally:
        e2.LEV, e2.BARS_PER_DAY = old_lev, old_bpd
    start_ts = candles[0][0] // 1000
    pnls = [(e_["t"] // 1000, e_["pnl"], e_.get("worst", 0.0))
            for e_ in events if e_["type"] == "close"]
    # Рядом с признаком слива — итог САМОГО движка, на фиксированной базе $20
    # и без реинвеста. Он не равен тому, что показывает сайт (там реинвест от
    # $50), и расходятся они сильнее всего как раз на оборванных прогонах,
    # поэтому оба числа печатаются рядом и ни одно не выдаётся за другое.
    meta = dict(ruined=bool(r["ruined"]),
                ruined_trade=r.get("ruined_trade"),
                ruined_ts=(r["ruined_ts"] // 1000
                           if r.get("ruined_ts") else None),
                max_dd_mtm=round(r["max_dd_mtm"] * 100, 1),
                ret_flat=round((r["balance"] / e2.START - 1) * 100, 1),
                dd_closed_flat=round(r["max_dd"] * 100, 1),
                trades=r["trades"], wins=r["wins"],
                # Полный список событий за все 3.2 года — для маркеров на
                # графике. Раньше сайт рисовал сделки из симуляции, которую
                # считал на лету и только за последние 130 дней: на графике
                # за два года было видно два цикла из пятидесяти семи, и это
                # читалось как «бот не торгует», хотя он просто торговал
                # раньше. Прогон здесь и так идёт по всей истории — событиям
                # достаточно не пропасть.
                events=[dict(t=e_["t"] // 1000, type=e_["type"],
                             side=e_.get("side"),
                             pnl=(round(e_["pnl"], 4)
                                  if e_.get("pnl") is not None else None))
                        for e_ in events])
    return start_ts, pnls, meta


def signal_pnls(name, rec, c4, ctx, c15, ts15):
    # signal_engine не отдаёт внутрицикловую переоценку, поэтому «худшая
    # точка» у сигналов равна результату сделки: их плавающая просадка не
    # занижена только для убыточных сделок, а для прибыльных неизвестна.
    # Врать нулём в другую сторону нельзя — поэтому берём min(pnl, 0).
    r = se.run_setup(name, rec["genome"], c4, ctx, c15, ts15,
                     rec.get("rec_lev", 15))
    start_ts = c4[0][0] // 1000
    pnls = [(t["exit_ts"] // 1000, t["pnl"], min(t["pnl"], 0.0))
            for t in r["trades"]]
    # у сигнального движка своего признака слива нет — не выдумываем
    return start_ts, pnls, dict(ruined=False, ruined_trade=None,
                                ruined_ts=None, max_dd_mtm=None)


def dd_closed(values):
    """Просадка по кривой ЗАКРЫТОГО капитала, в процентах от пика.

    ЕДИНСТВЕННЫЙ источник истины для этой метрики: её же зовёт
    build_analytics (stats.max_dd). Раньше здесь считалось по точкам кривой,
    уже округлённым до центов (round(eq, 2)), а в аналитике — по сырому
    капиталу, и LTC расходился между двумя файлами: 29.3% на /pnl против
    29.4% в карточке бота. Округление до центов на капитале $50-$80 съедает
    как раз десятую долю процента просадки, поэтому считаем по СЫРЫМ
    значениям, а округляем один раз — здесь, в конце.
    """
    peak, dd = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            dd = max(dd, (peak - v) / peak)
    return round(dd * 100, 1)


def dd_float_closed_peak(pnls):
    """Просадка стратегии: пик по ЗАКРЫТОМУ капиталу, дно — плавающее.

    От dd_of отличается ровно одним: перед тем как записать результат сделки,
    капитал проседает до eq*(1+worst/BT_BASE) — эту точку кривая по закрытым
    сделкам не видит никогда. Так как worst <= min(0, pnl), результат всегда
    >= dd_of по той же линии.

    ИМЯ ВАЖНО. Это НЕ evolution2.run5.max_dd_mtm, и различий сразу два:
      * там пик тоже плавающий — его поднимает нереализованная прибыль
        открытого цикла. Здесь так нельзя: из событий сделок известна только
        ХУДШАЯ точка цикла (`worst`), лучшая не известна, поднимать пик нечем;
      * здесь РЕИНВЕСТ (капитал растёт от $50), а в движке фиксированная база
        $20 и маржа $5.
    Поэтому числа расходятся в обе стороны (LTC 30.8% здесь против 25.3% в
    движке, DOGE 60.4% против 73.9%) и сравнивать их между собой — например
    «на сайте просадка меньше, чем в отборе» — бессмысленно.
    """
    eq = peak = SLEEVE0
    dd = 0.0
    for _, pnl, worst in pnls:
        if peak > 0:
            dd = max(dd, (peak - eq * (1 + worst / BT_BASE)) / peak)
        eq *= (1 + pnl / BT_BASE)
        peak = max(peak, eq)
    return round(dd * 100, 1)


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    pct5 = xd.fetch_daily_pct5()

    streams = []  # (key, label, color, group, start_ts, [(ts, pnl, worst)], meta)
    for sym, modes in config.SYMBOL_PARAMS.items():
        p = modes.get("final")
        if not p:
            continue
        print(f"{sym}: сделки бота (x{p.get('lev', 5)})...")
        st, pnls, meta = bot_pnls(sym, p, pct5)
        print(f"  движок (база $20, без реинвеста): {meta['ret_flat']:+.1f}% | "
              f"DD закр. {meta['dd_closed_flat']}% / mtm {meta['max_dd_mtm']}% | "
              f"сделок {meta['trades']} | WR "
              f"{meta['wins'] / meta['trades'] * 100 if meta['trades'] else 0:.1f}%")
        if meta["ruined"]:
            print(f"  ВНИМАНИЕ: счёт слит на {meta['ruined_trade']}-й сделке — "
                  f"прогон оборван, дальше сделок нет")
        streams.append((f"bot_{sym}",
                        f"{sym.replace('USDT','')} — бот x{p.get('lev', 5)}",
                        COLORS.get(sym, "#aaaaaa"), "bot", st, pnls, meta))

    with open("signal_setups.json", encoding="utf-8") as fh:
        setups = json.load(fh)
    c4 = ev.fetch("BTCUSDT", "240", 1150)
    c15 = ev.fetch("BTCUSDT", "15", 1150)
    ts15 = [c[0] for c in c15]
    ctx = se.prep_context(c4)
    for name, rec in setups.items():
        if not rec.get("enabled"):
            continue
        print(f"{name}: сделки сигналов (x{rec.get('rec_lev', 15)})...")
        st, pnls, meta = signal_pnls(name, rec, c4, ctx, c15, ts15)
        streams.append((f"sig_{name}",
                        f"{RU_SETUPS.get(name, name)} x{rec.get('rec_lev', 15)}",
                        COLORS.get(name, "#dddddd"), "signal", st, pnls, meta))

    n = len(streams)
    t0 = min(s[4] for s in streams)
    total0 = SLEEVE0 * n

    series = []
    for key, label, color, group, st, pnls, meta in streams:
        eq = SLEEVE0
        pts = [[t0, round(SLEEVE0, 2)]]
        raw = [SLEEVE0]          # тот же капитал БЕЗ округления — для просадки
        for ts, pnl, _worst in pnls:
            eq *= (1 + pnl / BT_BASE)
            pts.append([ts, round(eq, 2)])
            raw.append(eq)
        series.append(dict(
            key=key, label=label, group=group, color=color,
            final_usd=round(eq, 2),
            final_pct=round((eq / SLEEVE0 - 1) * 100, 1),
            # dd — прежний ключ (просадка по закрытым сделкам), его читает
            # сайт; dd_float — с плавающим ДНОМ, но пиком по закрытым сделкам
            # (см. dd_float_closed_peak: это не движковая max_dd_mtm)
            dd=dd_closed(raw), dd_float=dd_float_closed_peak(pnls),
            # СЛИВ: прогон оборван движком, кривая после этой точки не
            # существует. Линия без этого признака выглядит как обычный минус
            ruined=meta["ruined"], ruined_trade=meta["ruined_trade"],
            ruined_ts=meta["ruined_ts"],
            points=downsample(pts)))

    # события всех стратегий по времени — для портфельных линий
    merged = []
    for key, label, color, group, st, pnls, meta in streams:
        for ts, pnl, worst in pnls:
            merged.append((ts, key, pnl, worst))
    merged.sort()

    # портфель без ребаланса: сумма независимых капиталов
    sleeves = {s[0]: SLEEVE0 for s in streams}
    pts_cons = [[t0, round(total0, 2)]]
    raw_cons = [total0]
    peak_pf, dd_pf_float = total0, 0.0
    # Точку ставим ОДНУ на отметку времени, а не одну на событие. Две стратегии
    # закрывают цикл на одном 15-минутном баре регулярно (события всех рукавов
    # слиты в общий ряд), и раньше это давало две точки с одинаковым ts.
    # Кривую это не искажало, но библиотеке графиков нужен строго возрастающий
    # ряд: на дубле она падала внутри своего цикла перерисовки и гасила ВЕСЬ
    # холст — долларовый график /pnl не рисовался вовсе. Внутри отметки времени
    # события применяем по очереди (просадка считается по каждому), а в ряд
    # отдаём итог после последнего из них.
    # имя n занято: это число стратегий, оно нужно ниже в формуле ребаланса
    i, n_ev = 0, len(merged)
    while i < n_ev:
        ts = merged[i][0]
        while i < n_ev and merged[i][0] == ts:
            _ts, key, pnl, worst = merged[i]
            # плавающая просадка портфеля: в момент худшей точки цикла остальные
            # рукава стоят столько же, поэтому достаточно подменить один
            low = sum(sleeves.values()) - sleeves[key] \
                + sleeves[key] * (1 + worst / BT_BASE)
            dd_pf_float = max(dd_pf_float, (peak_pf - low) / peak_pf)
            sleeves[key] *= (1 + pnl / BT_BASE)
            peak_pf = max(peak_pf, sum(sleeves.values()))
            i += 1
        total_now = sum(sleeves.values())
        pts_cons.append([ts, round(total_now, 2)])
        raw_cons.append(total_now)
    # Подпись портфеля считается из ФАКТИЧЕСКОГО состава. Была зашита строкой
    # «ПОРТФЕЛЬ $350» с тех пор, как линий было семь; сигнальные сетапы
    # выключены приёмкой, старт стал $250 — и таблица на /pnl спорила с
    # соседней строкой отчёта («Портфель с ребалансом: $250 -> $296»).
    pf_name = f"ПОРТФЕЛЬ ${total0:.0f}"
    series.append(dict(
        key="pf_cons", label=f"{pf_name} — без ребаланса",
        group="portfolio", color="#e8e6df",
        final_usd=pts_cons[-1][1],
        final_pct=round((pts_cons[-1][1] / total0 - 1) * 100, 1),
        dd=dd_closed(raw_cons), dd_float=round(dd_pf_float * 100, 1),
        # у портфеля своего слива нет: слитый рукав просто перестаёт давать
        # сделки, признак показывается на ЕГО линии
        ruined=False, ruined_trade=None, ruined_ts=None,
        points=downsample(pts_cons)))

    # портфель с ежемесячным ребалансом: r_мес = сумм. PnL / (n x $20-база)
    monthly = defaultdict(float)
    for ts, key, pnl, _worst in merged:
        monthly[ts // MONTH] += pnl
    eq = total0
    pts_reb = [[t0, round(eq, 2)]]
    raw_reb = [total0]
    for m in sorted(monthly):
        eq *= (1 + monthly[m] / (n * BT_BASE))
        pts_reb.append([(m + 1) * MONTH, round(eq, 2)])
        raw_reb.append(eq)
    series.append(dict(
        key="pf_reb", label=f"{pf_name} — ежемесячный ребаланс",
        group="portfolio", color="#E3A83E",
        final_usd=pts_reb[-1][1],
        final_pct=round((pts_reb[-1][1] / total0 - 1) * 100, 1),
        # dd_float тут честно None: линия построена по МЕСЯЧНЫМ суммам, и
        # внутримесячную переоценку отдельных циклов к ней не привязать —
        # выдумывать число вместо признания «не считаем» нельзя
        dd=dd_closed(raw_reb), dd_float=None,
        ruined=False, ruined_trade=None, ruined_ts=None,
        points=downsample(pts_reb)))

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
            raw_b = [SLEEVE0 * c / base for _ts, c in rows]
            series.append(dict(
                key=key, label=label, group="bench", color=color,
                final_usd=pts[-1][1],
                final_pct=round((pts[-1][1] / SLEEVE0 - 1) * 100, 1),
                # холд считается по дневным закрытиям — это уже переоценка
                # рынком, отдельной «плавающей» просадки у него нет
                dd=dd_closed(raw_b), dd_float=dd_closed(raw_b),
                ruined=False, ruined_trade=None, ruined_ts=None,
                points=downsample(pts)))
    except Exception as e:
        print(f"  бенчмарки пропущены: {e}")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dict(sleeve=SLEEVE0, total=total0, series=series),
                  fh, ensure_ascii=False)
    for s in series:
        ddf = "н/д" if s.get("dd_float") is None else f"{s['dd_float']}%"
        ruin = (f"  СЛИВ на {s['ruined_trade']}-й сделке"
                if s.get("ruined") else "")
        print(f"  {s['label']:44} ${s['final_usd']:>9.2f} "
              f"({s['final_pct']:+8.1f}%) DD закр. {s['dd']}% / плав. {ddf}"
              f"{ruin}")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
