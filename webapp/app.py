# -*- coding: utf-8 -*-
"""Локальный сайт управления ботами: список, страницы ботов, график со сделками.

Запуск:  python webapp/app.py   (из папки bybit_bot)
Открыть: http://127.0.0.1:8000
"""

import json
import os
import re
import sys
import time

from flask import Flask, jsonify, render_template

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import evolution2 as e2  # noqa: E402
import evolution5 as e5  # noqa: E402
import evolution6 as e6  # noqa: E402
import evolution7 as e7  # noqa: E402
import evolution8 as e8
import evolution12 as e12  # noqa: E402
import ext_data as xd  # noqa: E402
import patterns as pt  # noqa: E402
from pybit.unified_trading import HTTP  # noqa: E402

app = Flask(__name__)
session = HTTP(testnet=False)
BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODE_NAMES = {"normal": "Обычный", "turbo": "Турбо", "bear": "Медвежий",
              "final": "Финальный"}
ARCHIVE_MODES = ("normal", "bear", "turbo")

# волна отбора, которой принадлежит конфиг (v2 — глубокая эволюция 2г,
# v4 — киты/макро 3.2г, v5 — индикаторы 3.2г, v6 — медвежьи специалисты,
# v7 — единый отбор: направление + режим + паттерны, финальные 5 ботов)
VER = {
    ("DOGEUSDT", "normal"): "v4", ("DOGEUSDT", "bear"): "v6",
    ("LTCUSDT", "normal"): "v5", ("LTCUSDT", "bear"): "v6",
    ("LTCUSDT", "turbo"): "турбо-иссл.",
    ("BTCUSDT", "normal"): "v2", ("BTCUSDT", "bear"): "v6",
    ("BTCUSDT", "turbo"): "турбо-иссл.",
    ("ETHUSDT", "normal"): "v2", ("ETHUSDT", "bear"): "v6",
    ("ETHUSDT", "turbo"): "турбо-иссл.",
    ("SOLUSDT", "normal"): "v5", ("SOLUSDT", "bear"): "v6",
    ("DOGEUSDT", "final"): "v7", ("LTCUSDT", "final"): "v12",
    ("BTCUSDT", "final"): "v7", ("ETHUSDT", "final"): "v7",
    ("SOLUSDT", "final"): "v7",
}

# --- описания ботов ---

STRATEGY_CORE = (
    "Ядро стратегии: вход по RSI у границы диапазона (перепроданность у "
    "нижней границы — лонг, перекупленность у верхней — шорт), докупка "
    "лимитной сеткой, тейк от средней цены, «обманный» стоп за уровнем — "
    "вне зоны, где собирают стопы. Kill switch: 3 убыточных цикла подряд — "
    "пауза 24 часа."
)

META = {
    ("DOGEUSDT", "normal"): dict(
        title="DOGE — универсал",
        stats="3.2 года: +65% | медиана месяца +2.3% | 76% месяцев в плюсе | WR 97% | просадка 10.8%",
        about="Скальперский профиль (тейк 0.8%, ~1775 сделок за 3.2г) с макро-фильтрами: "
              "лонг запрещён при растущем DXY (>0.93%/5д) и дорожающем золоте (риск-офф), "
              "фильтры funding и открытого интереса. RSI(7), сетка 3 колена.",
        usage="Основной бот DOGE на все режимы рынка. Плечо x5, маржа $5. "
              "Запуск: python bot_rsi.py DOGE"),
    ("DOGEUSDT", "bear"): dict(
        title="DOGE — медвежий специалист",
        stats="3.2г (только bear/боковик): +145% на x10 | медиана bear-месяца +6.5% | DD 18.5%",
        about="Торгует только когда вчерашняя цена ниже SMA100 дней и 30-дневное "
              "изменение < −5% (или боковик); в бычьем рынке стоит в стороне. "
              "Благодаря этому переживает даже x15 без слива. Aroon-фильтр: лонг "
              "только после свежего минимума.",
        usage="Включать сейчас (рынок медвежий) параллельно с обычным или вместо него. "
              "Плечо x10. Запуск: python bot_rsi.py DOGE bear"),
    ("LTCUSDT", "normal"): dict(
        title="LTC — флагман",
        stats="3.2 года: +118% | медиана месяца +4.3% | 84% месяцев в плюсе | WR 96% | просадка 16.2%",
        about="Лучший бот портфеля. Жёсткие макро-фильтры: лонг только при "
              "funding ≤ 0 (шорты платят) и спокойном S&P 500 (падение менее "
              "0.8% за 5 дней блокирует лонги). RSI(7), сетка 2 колена, "
              "безубыток на полпути к тейку.",
        usage="Первый кандидат на реальные деньги после обкатки. Плечо x5. "
              "Запуск: python bot_rsi.py LTC"),
    ("LTCUSDT", "bear"): dict(
        title="LTC — медвежий специалист",
        stats="3.2г (bear/боковик): +91% на x8 | медиана bear-месяца +5.9% | DD 25.3%",
        about="Тот же характер, что у обычного LTC, но торгует только в медвежьем "
              "рынке и боковике, с повышенным плечом x8.",
        usage="Для текущего медвежьего рынка. Запуск: python bot_rsi.py LTC bear"),
    ("LTCUSDT", "turbo"): dict(
        title="LTC — турбо (экспер.)",
        stats="Старые тесты (2 года): +9.5% — на грани; DD умеренная",
        about="Режим x20 с коротким стопом 2.5×ATR от средней. На LTC работает "
              "на грани окупаемости — оставлен как эксперимент.",
        usage="Не рекомендуется. Если очень хочется: python bot_rsi.py LTC turbo"),
    ("BTCUSDT", "normal"): dict(
        title="BTC — снайпер",
        stats="3.2 года: +44% | медиана месяца +2.1% | 63% месяцев в плюсе | WR 69% | просадка 13.8%",
        about="Сверхселективный: ~33 сделки в год. Огромное окно уровней (877 "
              "свечей ≈ 9 дней), асимметричные зоны — шорт только от верхних 18% "
              "диапазона. Без макро-фильтров (они экзамен не прошли).",
        usage="Спокойный бот с редкими сделками — хорош как стабилизатор портфеля. "
              "Плечо x5. Запуск: python bot_rsi.py BTC"),
    ("BTCUSDT", "bear"): dict(
        title="BTC — медвежий король",
        stats="3.2г (bear/боковик): +112% на x12 | медиана +4.0% | DD всего 10.2%",
        about="Лучшее сочетание дохода и просадки во всём портфеле: даже на x15 "
              "просадка лишь 12.6%. SMA20/400-фильтр тренда, Aroon, фильтры "
              "funding/OI/S&P/DXY/золота.",
        usage="Главная рекомендация для медвежьего рынка. Плечо x12. "
              "Запуск: python bot_rsi.py BTC bear"),
    ("BTCUSDT", "turbo"): dict(
        title="BTC — турбо (экспер.)",
        stats="Старые тесты (2 года): +11% | оба полугодия в плюсе",
        about="x20 с коротким стопом. Скромный доход — обычный режим сильнее.",
        usage="Не рекомендуется. Запуск: python bot_rsi.py BTC turbo"),
    ("ETHUSDT", "normal"): dict(
        title="ETH — аккуратист",
        stats="3.2 года: +23% | медиана месяца +1.9% | 71% месяцев в плюсе | WR 87% | просадка 21.8%",
        about="Асимметричные зоны (лонг до 50% диапазона, шорт от 59%), близкий "
              "тейк 1%, длинный кулдаун после стопа. Внешние фильтры экзамен "
              "не прошли — торгует чистой ценой.",
        usage="Скромный, но стабильный. Плечо x5, выше НЕ поднимать (просадка "
              "растёт быстрее всех). Запуск: python bot_rsi.py ETH"),
    ("ETHUSDT", "bear"): dict(
        title="ETH — медвежий гейт",
        stats="3.2г (bear/боковик): +17.5% на x5 | DD 24.8%",
        about="Специалист не превзошёл обычный конфиг — это тот же ETH-универсал, "
              "но с запретом торговли в бычьем рынке. Плечо не поднимать: уже "
              "на x8 просадка 38%.",
        usage="Только x5. Запуск: python bot_rsi.py ETH bear"),
    ("ETHUSDT", "turbo"): dict(
        title="ETH — турбо",
        stats="Старые тесты (2 года): +58% | WR 78% | DD 11.4% — лучший турбо",
        about="Единственная монета, где x20 оправдан: чистые экстремумы, короткий "
              "стоп 2.5×ATR почти не задевается. Вход только при RSI≤25 в крайних "
              "15% диапазона, нож-фильтр, кулдаун 8 часов.",
        usage="Для опытных, малой маржой, после обкатки обычных. "
              "Запуск: python bot_rsi.py ETH turbo"),
    ("SOLUSDT", "normal"): dict(
        title="SOL — рабочая лошадка",
        stats="3.2 года: +61% | медиана месяца +2.0% | 82% месяцев в плюсе | WR 98% | просадка 8.2%",
        about="Скальпер (тейк 0.8%) с фильтрами funding/OI/S&P и жёстким Aroon: "
              "лонг только сразу после свежего минимума (AroonDown ≥ 91) — "
              "покупаем именно дно выноса, а не середину падения.",
        usage="Стабильный середняк с низкой просадкой. Плечо x5. "
              "Запуск: python bot_rsi.py SOL"),
    ("SOLUSDT", "bear"): dict(
        title="SOL — медвежий специалист",
        stats="3.2г (bear/боковик): +50% на x8 | медиана +4.9% | DD 24.4%",
        about="Медвежья версия с RSI(10) и плечом x8. Aroon-фильтры на обе стороны.",
        usage="Для медвежьего рынка. Запуск: python bot_rsi.py SOL bear"),

    # --- v7: финальные боты (главная страница) ---
    ("DOGEUSDT", "final"): dict(
        title="DOGE",
        stats="3.2г x10: -8.3% | DD 47.9% | WR 92.4% | PF 0.98 — УБЫТОЧЕН",
        about="Итог v7 — единого отбора, где направление, режимный гейт и "
              "фильтр классических паттернов были ГЕНАМИ одного генома. "
              "Победил тот же конфиг, что и медвежий специалист v6: обе "
              "стороны сделки (direction=0), но торгует ТОЛЬКО в bear/боковике "
              "— в подтверждённом bull стоит в стороне. Паттерны (двойные "
              "вершины, голова-плечи, треугольники…) экзамен не прошли.",
        usage="Главный бот DOGE. Плечо x10 (объективное правило: макс. плечо "
              "с просадкой ≤20% на 3.2г). Запуск: python bot_rsi.py DOGE"),
    ("LTCUSDT", "final"): dict(
        title="LTC",
        stats="3.2г x5: +99.7% | DD 17.4% | WR 84.8% | 521 сделка | v12",
        about="Первый конфиг, найденный на ЧЕСТНОМ движке (v12, после исправления be_move и модели ликвидации; прежний v8-конфиг на честном движке давал -33%). Отбор сам выключил be_move и взял гены подвижной сетки v10: веса колен дышат с волатильностью, лёгкий трейлинг. Все 3 экзамена в плюс. Доля настоящих убыточных сделок 15.2%. Прежнее описание (v8): улучшен волной v8 (геном + Smart Money "
              "Concepts как гены). SMC не прошли экзамен и здесь, но сам "
              "поиск нашёл более сильный базовый конфиг: torgует во ВСЕХ "
              "режимах рынка (regime_gate=0, был 1) — почти удвоил доход "
              "при том же риске. Доля настоящих убыточных сделок 3.9% — "
              "проверено отдельно, не совпадение.",
        usage="Главный бот LTC. Плечо x5. Запуск: python bot_rsi.py LTC"),
    ("BTCUSDT", "final"): dict(
        title="BTC",
        stats="3.2г x15: +148.3% | DD 9.5% | WR 71.2% | PF 2.67",
        about="Самый низкорисковый бот портфеля: просадка растёт с 5.4% (x5) "
              "до всего 12.6% (x15) — редкость точных входов (~19/год) почти "
              "не усиливается плечом. Обе стороны, гейт в bull, фильтр тренда "
              "SMA20/400 + Aroon.",
        usage="Главный бот BTC. Плечо x15 — самое высокое в портфеле, оправдано "
              "устойчиво низкой просадкой. Запуск: python bot_rsi.py BTC"),
    ("ETHUSDT", "final"): dict(
        title="ETH",
        stats="3.2г x5: +29.3% | DD 21.8% | WR 86.6% | PF 1.30",
        about="Единственный финал БЕЗ регионного гейта — торгует всегда, во "
              "всех режимах рынка. Не изменился с самой первой честной "
              "эволюции (v2): четыре последующие волны отбора не нашли, чем "
              "его улучшить — хороший знак устойчивости, а не застоя.",
        usage="Главный бот ETH. Плечо x5 — не поднимать, просадка растёт "
              "быстрее всех в портфеле. Запуск: python bot_rsi.py ETH"),
    ("SOLUSDT", "final"): dict(
        title="SOL",
        stats="3.2г x5: -51.3% | DD 66.2% | WR 95.0% | PF 0.85 — УБЫТОЧЕН",
        about="Единственный НОВЫЙ конфиг, который v7 честно нашла впервые "
              "(обошла прежний лучший результат на всех 3 экзаменах). Торгует "
              "всегда (без гейта). Aroon перекошен (aroon_short_min=99) — "
              "формально обе стороны разрешены, но шорты на практике почти "
              "не проходят фильтр.",
        usage="Главный бот SOL. Плечо x5. Запуск: python bot_rsi.py SOL"),
}


def list_bots(modes):
    bots = []
    for sym, sym_modes in config.SYMBOL_PARAMS.items():
        for mode in modes:
            p = sym_modes.get(mode)
            if not p:
                continue
            meta = META.get((sym, mode), {})
            log_file = os.path.join(BOT_DIR, f"bot_{sym}_{mode}.log")
            active = (os.path.exists(log_file) and
                      time.time() - os.path.getmtime(log_file) < 180)
            iv = p.get("interval", "15")
            reinvest = ""
            if mode == "final":
                apath = os.path.join(BOT_DIR, "webapp", "data",
                                     f"analytics_bot_{sym}.json")
                try:
                    with open(apath, encoding="utf-8") as fh:
                        a = json.load(fh)["stats"]
                    sign = "+" if a["final_pct"] >= 0 else ""
                    reinvest = (f"💰 с реинвестом: $50 → ${a['final_usd']} "
                                f"({sign}{a['final_pct']}%)")
                except Exception:
                    pass
            bots.append(dict(
                symbol=sym, coin=sym.replace("USDT", ""), mode=mode,
                mode_name=MODE_NAMES.get(mode, mode), lev=p.get("lev", 5),
                interval=iv, tf_label=("4ч" if iv == "240" else f"{iv}m"),
                ver=VER.get((sym, mode), ""),
                title=meta.get("title", f"{sym.replace('USDT','')} — {mode}"),
                stats=meta.get("stats", ""), reinvest=reinvest, active=active,
                has_log=os.path.exists(log_file)))
    return bots


def final_bot_list():
    return list_bots(("final",))


def archive_bot_list():
    return list_bots(ARCHIVE_MODES)


def news_panel():
    """Новостной фон для главной: что сейчас видят боты и влияет ли это.

    Показываем даже когда фон выключен: «фон есть, но ни на что не влияет» —
    это важное состояние, о котором лучше знать явно, чем догадываться.
    """
    import news_state
    try:
        rows = []
        for sym in config.SYMBOL_PARAMS:
            if sym not in news_state.BETA:
                continue
            bg = news_state.background(sym)
            p = config.SYMBOL_PARAMS[sym].get("final") or {}
            rows.append(dict(
                coin=sym.replace("USDT", ""), index=bg["index"],
                heat=bg["heat"], n=bg["n"], stale=bg["stale"],
                active=bool(p.get("news_tp_k") or p.get("news_sl_k") or
                            p.get("news_heat_max") or p.get("news_index_min"))))
        return dict(rows=rows, any_active=any(r["active"] for r in rows),
                    any_news=any(r["n"] for r in rows))
    except Exception:
        return dict(rows=[], any_active=False, any_news=False)


@app.route("/")
def index():
    finals = final_bot_list()
    return render_template("index.html", bots=finals, dry_run=config.DRY_RUN,
                           has_final=bool(finals), news=news_panel())


@app.route("/archive")
def archive():
    return render_template("archive.html", bots=archive_bot_list(),
                           dry_run=config.DRY_RUN)


RU_SETUPS = {
    "range_long": "Боковик: лонг от нижней границы",
    "range_short": "Боковик: шорт от верхней границы",
    "sweep_long": "Ложный пробой низа: лонг",
    "sweep_short": "Ложный пробой верха: шорт",
    "dump_long": "Капитуляция: лонг после обвала",
    "pump_short": "Перегрев: шорт после вертикального роста",
}


@app.route("/signals")
def signals_page():
    setups = {}
    p = os.path.join(BOT_DIR, "signal_setups.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            setups = json.load(fh)
    state = dict(active={}, history=[])
    p2 = os.path.join(FINAL_DATA_DIR, "signals.json")
    if os.path.exists(p2):
        with open(p2, encoding="utf-8") as fh:
            state = json.load(fh)
    hist = sorted(state.get("history", []),
                  key=lambda h: h.get("exit_ts", 0), reverse=True)[:40]
    adv_log = os.path.join(BOT_DIR, "advisor.log")
    running = (os.path.exists(adv_log) and
               time.time() - os.path.getmtime(adv_log) < 180)
    return render_template("signals.html", setups=setups, ru=RU_SETUPS,
                           active=state.get("active", {}), history=hist,
                           capitals=state.get("capital", {}),
                           running=running,
                           fmt_ts=lambda ms: time.strftime(
                               "%d.%m %H:%M", time.localtime(ms / 1000)))


_sig_chart_cache = {}
_btc_sig_data = {}


def _btc_signal_data():
    """Ленивая загрузка BTC 4ч/15м серий и контекста для страниц сетапов."""
    if not _btc_sig_data:
        import evolution as ev
        import signal_engine as se
        c4 = ev.fetch("BTCUSDT", "240", 1150)
        c15 = ev.fetch("BTCUSDT", "15", 1150)
        _btc_sig_data.update(
            c4=c4, c15=c15, ts15=[c[0] for c in c15],
            ctx=se.prep_context(c4))
    return _btc_sig_data


def load_signal_setups():
    p = os.path.join(BOT_DIR, "signal_setups.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


@app.route("/signal/<name>")
def signal_page(name):
    setups = load_signal_setups()
    rec = setups.get(name)
    if not rec:
        return "Нет такого сетапа", 404
    return render_template("signal.html", name=name,
                           title=RU_SETUPS.get(name, name), rec=rec,
                           st=rec.get("stats", {}))


@app.route("/api/signal_chart/<name>")
def api_signal_chart(name):
    """4ч свечи BTC (полные 3.2г) + все сделки бэктеста сетапа + активный
    сигнал, если есть."""
    cached = _sig_chart_cache.get(name)
    if cached and time.time() - cached[0] < 1800:
        return jsonify(cached[1])
    setups = load_signal_setups()
    rec = setups.get(name)
    if not rec:
        return jsonify(dict(error="нет сетапа")), 404
    import signal_engine as se
    d = _btc_signal_data()
    r = se.run_setup(name, rec["genome"], d["c4"], d["ctx"], d["c15"],
                     d["ts15"], rec.get("rec_lev", 15))
    candles = [dict(time=c[0] // 1000, open=c[1], high=c[2], low=c[3],
                    close=c[4]) for c in d["c4"]]
    trades = [dict(entry_ts=t["entry_ts"] // 1000, exit_ts=t["exit_ts"] // 1000,
                   side=t["side"], entry=t["entry"], stop=t["stop"],
                   tp=t["tp"], pnl=t["pnl"], r=t["r"], reason=t["reason"],
                   hold_h=t["hold_h"]) for t in r["trades"]]
    active = None
    p2 = os.path.join(FINAL_DATA_DIR, "signals.json")
    if os.path.exists(p2):
        with open(p2, encoding="utf-8") as fh:
            active = json.load(fh).get("active", {}).get(name)
    data = dict(candles=candles, trades=trades, active=active)
    _sig_chart_cache[name] = (time.time(), data)
    return jsonify(data)


@app.route("/pnl")
def pnl_page():
    return render_template("pnl.html")


_analytics_cache = {}


@app.route("/api/analytics/<key>")
def api_analytics(key):
    """Аналитика бота/сигнала (key: bot_<SYM> | sig_<name>) из предпосчёта
    build_analytics.py."""
    path = os.path.join(FINAL_DATA_DIR, f"analytics_{key}.json")
    mtime = os.path.getmtime(path) if os.path.exists(path) else None
    cached = _analytics_cache.get(key)
    if cached and cached[0] == mtime:
        return jsonify(cached[1])
    if mtime is None:
        return jsonify(dict(available=False))
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data["available"] = True
    _analytics_cache[key] = (mtime, data)
    return jsonify(data)


@app.route("/api/pnl_curves")
def api_pnl_curves():
    p = os.path.join(FINAL_DATA_DIR, "pnl_curves.json")
    if not os.path.exists(p):
        return jsonify(dict(series=[]))
    with open(p, encoding="utf-8") as fh:
        return jsonify(json.load(fh))


@app.route("/evolution")
def evolution_page():
    path = os.path.join(FINAL_DATA_DIR, "evolution_timeline.json")
    timeline = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            timeline = json.load(fh)
    coins = {sym: sym.replace("USDT", "") for sym in config.SYMBOL_PARAMS}
    return render_template("evolution.html", timeline=timeline, coins=coins)


@app.route("/bot/<symbol>/<mode>")
def bot_page(symbol, mode):
    p = config.SYMBOL_PARAMS.get(symbol, {}).get(mode)
    if not p:
        return "Нет такого бота", 404
    meta = META.get((symbol, mode), {})
    interval = p.get("interval", "15")
    return render_template(
        "bot.html", symbol=symbol, coin=symbol.replace("USDT", ""),
        mode=mode, mode_name=MODE_NAMES.get(mode, mode), lev=p.get("lev", 5),
        interval_min=int(interval),
        tf_label=("4ч" if interval == "240" else f"{interval}m"),
        ver=VER.get((symbol, mode), ""),
        title=meta.get("title", f"{symbol} {mode}"),
        stats=meta.get("stats", ""), about=meta.get("about", ""),
        usage=meta.get("usage", ""), core=STRATEGY_CORE,
        params=p, dry_run=config.DRY_RUN)


CANDLE_DAYS = 90    # окно графика ("с мая" с запасом)
RAW_DAYS = 130      # + тёплый старт симуляции (окна до 877 свечей, EMA, режим)
_raw_cache = {}     # (symbol,interval) -> (fetched_at, candles)


def bot_interval(symbol, mode):
    p = config.SYMBOL_PARAMS.get(symbol, {}).get(mode) or {}
    return str(p.get("interval", "15"))


def get_raw_candles(symbol, interval="15"):
    """[[ts,o,h,l,c]...] за RAW_DAYS дней на заданном таймфрейме. Кэш:
    память + json на диске (10 мин). Один источник для графика и симуляции."""
    key = (symbol, interval)
    now = time.time()
    cached = _raw_cache.get(key)
    if cached and now - cached[0] < 600:
        return cached[1]
    disk = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        f"cache_{symbol}_{interval}.json")
    if os.path.exists(disk) and now - os.path.getmtime(disk) < 600:
        with open(disk) as fh:
            data = json.load(fh)
        _raw_cache[key] = (now, data)
        return data
    start = int((now - RAW_DAYS * 86400) * 1000)
    out, cursor = [], int(now * 1000)
    for _ in range(20):
        r = session.get_kline(category="linear", symbol=symbol,
                              interval=interval, limit=1000, end=cursor)
        rows = r["result"]["list"]
        if not rows:
            break
        out = rows[::-1] + out
        oldest = int(rows[-1][0])
        if oldest <= start or oldest >= cursor:
            break
        cursor = oldest - 1
    data = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])]
            for x in out if int(x[0]) >= start]
    with open(disk, "w") as fh:
        json.dump(data, fh)
    _raw_cache[key] = (now, data)
    return data


@app.route("/api/candles/<symbol>/<mode>")
def api_candles(symbol, mode):
    raw = get_raw_candles(symbol, bot_interval(symbol, mode))
    start = (time.time() - CANDLE_DAYS * 86400) * 1000
    return jsonify([
        dict(time=c[0] // 1000, open=c[1], high=c[2], low=c[3], close=c[4])
        for c in raw if c[0] >= start])


# --- симуляция стратегии на периоде графика (честный движок) ---
# Один и тот же путь для архивных (normal/bear/turbo) и финальных (final)
# ботов: e7.cfg_to_genome воспроизводит правила по умолчанию bot_rsi.py,
# e8.make_filter8 — тот же фильтр (funding/OI/макро/EMA/MA/Aroon/направление/
# режимный гейт/паттерны/SMC), которым живой бот на самом деле торгует.
# e8 — строгий надмножество e7 (для ботов без SMC-генов ведёт себя идентично).
def cfg_to_genome(p, mode):
    g = e7.cfg_to_genome(p, mode)
    for k, v in e8.OFF8.items():
        g.setdefault(k, v)
    return g


_sim_cache = {}


def build_sim(symbol, mode):
    """Симуляция стратегии бота на периоде графика; кэш 30 мин."""
    key = (symbol, mode)
    cached = _sim_cache.get(key)
    if cached and time.time() - cached[0] < 1800:
        return cached[1]
    p = config.SYMBOL_PARAMS.get(symbol, {}).get(mode)
    if not p or mode == "turbo":  # турбо-движок отдельный, симуляцию не строим
        return dict(events=[], n=0, wins=0, total_pnl=0,
                    note="симуляция недоступна")
    interval = bot_interval(symbol, mode)
    bars_per_day = max(4, 1440 // int(interval))
    g = cfg_to_genome(p, mode)
    candles = get_raw_candles(symbol, interval)
    pre = e2.prep(candles)
    pct5 = xd.fetch_daily_pct5()
    aux = e12.make_aux_builder(pct5, bars_per_day)(symbol, candles)
    filt = e12.make_filter12(g, aux)
    events = []
    old_lev, old_bpd = e2.LEV, e2.BARS_PER_DAY
    e2.LEV = p.get("lev", 5)
    e2.BARS_PER_DAY = bars_per_day
    try:
        e2.run5(candles, pre, g, entry_filter=filt, events=events)
    finally:
        e2.LEV, e2.BARS_PER_DAY = old_lev, old_bpd
    chart_start = int(time.time()) - CANDLE_DAYS * 86400
    evs = []
    for e in events:
        t = e["t"] // 1000
        if t < chart_start:
            continue
        e2_ = dict(e)
        e2_["t"] = t
        evs.append(e2_)
    closes_ev = [e for e in evs if e["type"] == "close"]
    total = round(sum(e["pnl"] for e in closes_ev), 3)
    wins = sum(1 for e in closes_ev if e["pnl"] > 0)
    data = dict(events=evs, n=len(closes_ev), wins=wins, total_pnl=total)
    _sim_cache[key] = (time.time(), data)
    return data


@app.route("/api/sim/<symbol>/<mode>")
def api_sim(symbol, mode):
    return jsonify(build_sim(symbol, mode))


_finalstats_cache = {}
FINAL_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


@app.route("/api/finalstats/<symbol>")
def api_finalstats(symbol):
    """Помесячная (любое окно за 3.2г) и погодовая (3 года) статистика
    финального бота — из предпосчитанного finalize_final_bots.py."""
    cached = _finalstats_cache.get(symbol)
    path = os.path.join(FINAL_DATA_DIR, f"final_{symbol}.json")
    mtime = os.path.getmtime(path) if os.path.exists(path) else None
    if cached and cached[0] == mtime:
        return jsonify(cached[1])
    if mtime is None:
        return jsonify(dict(available=False))
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data["available"] = True
    _finalstats_cache[symbol] = (mtime, data)
    return jsonify(data)


LOG_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
RE_ENTRY = re.compile(r">>> ВХОД (LONG|SHORT)[^~]*~([\d.]+)")
RE_ADD = re.compile(r"сетка исполнилась[:\s]+([\d.]+) по ([\d.]+)")
RE_CLOSE = re.compile(r"<<< (?:ЦИКЛ ЗАВЕРШЁН|ЗАКРЫТИЕ|\[DRY_RUN\] (?:СТОП|ТЕЙК))"
                      r".*?PnL ~([+-]?[\d.]+)")


def parse_ts(line):
    m = LOG_TS.match(line)
    if not m:
        return None
    return int(time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")))


@app.route("/api/trades/<symbol>/<mode>")
def api_trades(symbol, mode):
    log_file = os.path.join(BOT_DIR, f"bot_{symbol}_{mode}.log")
    events, cycles = [], []
    if os.path.exists(log_file):
        with open(log_file, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                ts = parse_ts(line)
                if ts is None:
                    continue
                m = RE_ENTRY.search(line)
                if m:
                    events.append(dict(time=ts, type="entry",
                                       side=m.group(1),
                                       price=float(m.group(2))))
                    continue
                m = RE_ADD.search(line)
                if m:
                    events.append(dict(time=ts, type="add",
                                       qty=float(m.group(1)),
                                       price=float(m.group(2))))
                    continue
                m = RE_CLOSE.search(line)
                if m:
                    pnl = float(m.group(1))
                    events.append(dict(time=ts, type="close", pnl=pnl))
                    cycles.append(dict(time=ts, pnl=pnl))
    total = sum(c["pnl"] for c in cycles)
    wins = sum(1 for c in cycles if c["pnl"] > 0)
    return jsonify(dict(events=events, cycles=cycles[-50:],
                        total_pnl=round(total, 3), n=len(cycles), wins=wins))


@app.route("/api/status/<symbol>/<mode>")
def api_status(symbol, mode):
    log_file = os.path.join(BOT_DIR, f"bot_{symbol}_{mode}.log")
    tail = []
    if os.path.exists(log_file):
        with open(log_file, encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-15:]
        active = time.time() - os.path.getmtime(log_file) < 180
    else:
        active = False
    return jsonify(dict(active=active, tail=tail))


def _warmup():
    """Фоновый прогрев кэшей: свечи всех монет + симуляции всех ботов,
    чтобы страницы открывались мгновенно."""
    try:
        for sym, modes in config.SYMBOL_PARAMS.items():
            seen_intervals = set()
            for mode in ("normal", "bear", "final"):
                if modes.get(mode):
                    iv = bot_interval(sym, mode)
                    if iv not in seen_intervals:
                        get_raw_candles(sym, iv)
                        seen_intervals.add(iv)
                    build_sim(sym, mode)
        print("Прогрев кэшей завершён — страницы будут открываться быстро")
    except Exception as e:
        print("Прогрев прерван:", e)


if __name__ == "__main__":
    import threading
    threading.Thread(target=_warmup, daemon=True).start()
    app.run(host="127.0.0.1", port=8000, debug=False)
