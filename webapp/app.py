# -*- coding: utf-8 -*-
"""Локальный сайт управления ботами: список, страницы ботов, график со сделками.

Запуск:  python webapp/app.py   (из папки bybit_bot)
Открыть: http://127.0.0.1:8000
"""

import json
import os
import re
import sys
import threading
import time

from flask import Flask, jsonify, render_template

# Консоль Windows — cp866, и в ней нет ни тире «—», ни «ёлочек», ни эмодзи,
# которыми полны наши сообщения. Без этой поправки print падает с
# UnicodeEncodeError и убивает поток прогрева вместе с сообщением о нём.
# Поправка стоит на уровне модуля, а не в блоке __main__: сайт запускают и
# через «flask run», и через waitress/другой сервер — там __main__ чужой,
# а печатаем мы (прогрев, ошибки биржи) всё так же из этого модуля.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

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
BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# предпосчёт build_analytics.py / build_pnl_curves.py. Объявлен здесь, а не
# ниже по файлу: список ботов на главной тоже читает эту папку, и адрес папки
# должен быть один на весь модуль.
FINAL_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Клиент биржи. Dev-сервер обслуживает запросы в разных потоках, а один общий
# клиент между потоками делить небезопасно — поэтому у каждого потока свой.
BYBIT_TIMEOUT = 5     # секунд на ОДИН запрос к бирже (по умолчанию pybit — 10)
FETCH_BUDGET = 20     # секунд на всю догрузку истории в одном обработчике
_local = threading.local()


def bybit():
    """Клиент Bybit текущего потока (создаётся при первом обращении)."""
    s = getattr(_local, "session", None)
    if s is None:
        s = HTTP(testnet=False, timeout=BYBIT_TIMEOUT)
        _local.session = s
    return s


MODE_NAMES = {"normal": "Обычный", "turbo": "Турбо", "bear": "Медвежий",
              "final": "Финальный"}
ARCHIVE_MODES = ("normal", "bear", "turbo")

# --- проверка параметров маршрутов ---
# Параметры из URL подставляются в имена файлов, поэтому пропускаем только
# известные монеты/режимы и ключи без разделителей пути. На Windows
# разделителем считается и обратный слэш, а Flask его в параметре не режет —
# без проверки /api/analytics/..\..\..\report3y читал бы файл вне webapp/data.
VALID_MODES = frozenset(MODE_NAMES)
RE_KEY = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def valid_symbol(symbol):
    return symbol in config.SYMBOL_PARAMS


def valid_mode(mode):
    return mode in VALID_MODES


def safe_path(directory, filename):
    """Абсолютный путь внутри directory или None, если имя уводит наружу
    (../, ..\\, абсолютный путь, другой диск)."""
    base = os.path.realpath(directory)
    full = os.path.realpath(os.path.join(base, filename))
    try:
        if os.path.commonpath([base, full]) != base:
            return None
    except ValueError:      # разные диски — точно наружу
        return None
    return full


def bot_log_path(symbol, mode):
    """Путь к логу бота или None, если такой пары монета/режим нет."""
    if not valid_symbol(symbol) or not valid_mode(mode):
        return None
    return safe_path(BOT_DIR, f"bot_{symbol}_{mode}.log")

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
        stats="3.2г x10 (до 14.08.2026): −45.1% | просадка 53.7% закрытая / "
              "60.4% с плавающей | WR 92.5% | PF 0.92 | 1272 сделки — УБЫТОЧЕН",
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
        stats="3.2г x5 (до 14.08.2026): +59.2% | просадка 29.4% закрытая / "
              "30.8% с плавающей | WR 84.6% | PF 1.26 | 506 сделок | v12",
        about="Конфиг v12, найденный после исправления be_move и модели "
              "ликвидации. Отбор сам выключил be_move и взял гены подвижной "
              "сетки v10: веса колен дышат с волатильностью, лёгкий трейлинг. "
              "Торгует во всех режимах рынка (regime_gate=0). ВАЖНО: заявленные "
              "при отборе +99.7% и просадка 17.4% не подтвердились — после "
              "правок движка (внутрисвечной тейк, тейк как рыночный выход, "
              "плавающая просадка) итог +59.2% при просадке около 30%. "
              "Сам конфиг отбирался по протоколу с утечкой walk-forward и "
              "заново не переотбирался.",
        usage="Главный бот LTC. Плечо x5. Запуск: python bot_rsi.py LTC"),
    ("BTCUSDT", "final"): dict(
        title="BTC",
        stats="3.2г x15 (до 14.08.2026): +223.6% | просадка 25.0% закрытая / "
              "25.5% с плавающей | WR 70.2% | PF 2.5 | 57 сделок",
        about="Самый редкий бот портфеля: ~18 сделок в год. Обе стороны, гейт "
              "в bull, фильтр тренда SMA20/400 + Aroon. ВАЖНО про цифры: "
              "просадка выросла с заявленных 9.5% до 25% — прежняя метрика не "
              "учитывала плавающий убыток внутри цикла, а плечо x15 выбиралось "
              "именно по ней (правило «просадка ≤20%»), которому этот конфиг "
              "теперь не удовлетворяет. Рост доходности относительно прежних "
              "+148.3% НЕ ОБЪЯСНЁН и требует прогона на том же окне: правки "
              "движка могут делать результат только хуже. На 57 сделках за "
              "3.2 года любая оценка статистически хрупкая.",
        usage="Главный бот BTC. Плечо x15 — самое высокое в портфеле, оправдано "
              "устойчиво низкой просадкой. Запуск: python bot_rsi.py BTC"),
    ("ETHUSDT", "final"): dict(
        title="ETH",
        stats="3.2г x5 (до 14.08.2026): +20.6% | просадка 23.5% закрытая / "
              "23.9% с плавающей | WR 86.6% | PF 1.22 | 239 сделок",
        about="Единственный финал БЕЗ регионного гейта — торгует всегда, во "
              "всех режимах рынка. Не изменился с самой первой честной "
              "эволюции (v2): четыре последующие волны отбора не нашли, чем "
              "его улучшить — хороший знак устойчивости, а не застоя.",
        usage="Главный бот ETH. Плечо x5 — не поднимать, просадка растёт "
              "быстрее всех в портфеле. Запуск: python bot_rsi.py ETH"),
    ("SOLUSDT", "final"): dict(
        title="SOL",
        stats="3.2г x5 (до 14.08.2026): −60.1% | просадка 62.5% | WR 95.1% | "
              "PF 0.73 | 958 сделок — СЧЁТ СЛИТ на 958-й сделке",
        about="Единственный НОВЫЙ конфиг, который v7 честно нашла впервые "
              "(обошла прежний лучший результат на всех 3 экзаменах). Торгует "
              "всегда (без гейта). Aroon перекошен (aroon_short_min=99) — "
              "формально обе стороны разрешены, но шорты на практике почти "
              "не проходят фильтр.",
        usage="Главный бот SOL. Плечо x5. Запуск: python bot_rsi.py SOL"),
}


def load_analytics(key):
    """Предпосчёт build_analytics.py (webapp/data/analytics_<key>.json) или {}.

    Сайт ничего не считает сам: числа обязаны совпадать со страницей /pnl.
    Битый или недописанный файл (идёт пересборка) — не повод ронять страницу.
    """
    path = safe_path(FINAL_DATA_DIR, f"analytics_{key}.json")
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def ruined_flag(data):
    """Слит ли счёт на этой стратегии.

    Признак ставит пересчёт бэктеста: прогон обрывается, когда капитала не
    хватает на убыток. Ключ принимаем и из stats, и из корня файла — самый
    важный для владельца факт не должен потеряться из-за того, куда именно
    его положил движок.
    """
    if not isinstance(data, dict):
        return False
    stats = data.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    if data.get("ruined") or stats.get("ruined"):
        return True
    # страховка на случай, если признак не проставлен: капитал в нуле или
    # минусе — это слив по определению, каким бы ключом его ни называли
    end = stats.get("final_usd")
    return isinstance(end, (int, float)) and end <= 0


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
            reinvest, ruined = "", False
            dd, dd_float = None, None
            if mode == "final":
                data = load_analytics(f"bot_{sym}")
                ruined = ruined_flag(data)
                a = data.get("stats") or {}
                if a:
                    sign = "+" if a["final_pct"] >= 0 else ""
                    reinvest = (f"💰 с реинвестом: $50 → ${a['final_usd']} "
                                f"({sign}{a['final_pct']}%)")
                    # обе просадки: закрытая (по завершённым сделкам) занижена,
                    # именно по ней когда-то выбиралось плечо
                    dd, dd_float = a.get("max_dd"), a.get("max_dd_float")
            bots.append(dict(
                symbol=sym, coin=sym.replace("USDT", ""), mode=mode,
                mode_name=MODE_NAMES.get(mode, mode), lev=p.get("lev", 5),
                interval=iv, tf_label=("4ч" if iv == "240" else f"{iv}m"),
                ver=VER.get((sym, mode), ""),
                title=meta.get("title", f"{sym.replace('USDT','')} — {mode}"),
                stats=meta.get("stats", ""), reinvest=reinvest, active=active,
                ruined=ruined, dd=dd, dd_float=dd_float,
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
_btc_lock = threading.Lock()
_btc_ready = threading.Event()
_btc_loading = False


def _btc_load():
    """Качает 3.2 года истории BTC (4ч и 15м). При холодном кэше это минуты,
    поэтому только в фоне — обработчик столько ждать не должен."""
    global _btc_loading
    try:
        import evolution as ev
        import signal_engine as se
        c4 = ev.fetch("BTCUSDT", "240", 1150)
        c15 = ev.fetch("BTCUSDT", "15", 1150)
        _btc_sig_data.update(
            c4=c4, c15=c15, ts15=[c[0] for c in c15],
            ctx=se.prep_context(c4))
        _btc_ready.set()
    except Exception as e:
        print("История BTC не загрузилась:", e)
        with _btc_lock:              # пусть следующий запрос попробует снова
            _btc_loading = False


def _btc_signal_data(wait=FETCH_BUDGET):
    """BTC 4ч/15м серии и контекст для страниц сетапов — или None, если данные
    ещё греются: ждём не дольше wait секунд и отдаём страницу без графика."""
    global _btc_loading
    if _btc_ready.is_set():
        return _btc_sig_data
    with _btc_lock:
        if not _btc_loading:
            _btc_loading = True
            threading.Thread(target=_btc_load, daemon=True).start()
    return _btc_sig_data if _btc_ready.wait(wait) else None


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
    # то же, что у ботов: слив счёта и плавающая просадка — из предпосчёта,
    # прямо в разметку, не полагаясь на JS
    data = load_analytics(f"sig_{name}")
    an = data.get("stats") or {}
    return render_template("signal.html", name=name,
                           title=RU_SETUPS.get(name, name), rec=rec,
                           ruined=ruined_flag(data), dd=an.get("max_dd"),
                           dd_float=an.get("max_dd_float"),
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
    if d is None:                       # история ещё качается — не держим страницу
        return jsonify(dict(candles=[], trades=[], active=None, warming=True,
                            note="Данные греются, обновите страницу через минуту")), 503
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
    if not RE_KEY.match(key):
        return jsonify(dict(available=False, error="недопустимый ключ")), 404
    path = safe_path(FINAL_DATA_DIR, f"analytics_{key}.json")
    if path is None:
        return jsonify(dict(available=False, error="недопустимый ключ")), 404
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
    # слив счёта и просадки рисуем прямо в разметке, а не только в карточке
    # аналитики на JS: если скрипт не отработал, владелец всё равно обязан
    # увидеть, что счёт слит
    data = load_analytics(f"bot_{symbol}") if mode == "final" else {}
    an = data.get("stats") or {}
    return render_template(
        "bot.html", symbol=symbol, coin=symbol.replace("USDT", ""),
        mode=mode, mode_name=MODE_NAMES.get(mode, mode), lev=p.get("lev", 5),
        interval_min=int(interval),
        tf_label=("4ч" if interval == "240" else f"{interval}m"),
        ver=VER.get((symbol, mode), ""),
        title=meta.get("title", f"{symbol} {mode}"),
        stats=meta.get("stats", ""), about=meta.get("about", ""),
        usage=meta.get("usage", ""), core=STRATEGY_CORE,
        ruined=ruined_flag(data), dd=an.get("max_dd"),
        dd_float=an.get("max_dd_float"), trades=an.get("trades"),
        params=p, dry_run=config.DRY_RUN)


CANDLE_DAYS = 90    # окно графика ("с мая" с запасом)
RAW_DAYS = 130      # + тёплый старт симуляции (окна до 877 свечей, EMA, режим)
_raw_cache = {}     # (symbol,interval) -> (fetched_at, candles, ttl_сек)


def bot_interval(symbol, mode):
    p = config.SYMBOL_PARAMS.get(symbol, {}).get(mode) or {}
    return str(p.get("interval", "15"))


def get_raw_candles(symbol, interval="15"):
    """[[ts,o,h,l,c]...] за RAW_DAYS дней на заданном таймфрейме. Кэш:
    память + json на диске (10 мин). Один источник для графика и симуляции.

    Догрузка ограничена бюджетом FETCH_BUDGET: страница не должна ждать, пока
    биржа молчит (20 запросов по 10 секунд — это до 200 секунд ожидания).
    Если бюджет вышел или биржа не ответила, отдаём то, что успели скачать
    (иногда пусто), неполное на диск не пишем и в памяти держим недолго."""
    if not valid_symbol(symbol) or not str(interval).isdigit():
        return []
    key = (symbol, interval)
    now = time.time()
    cached = _raw_cache.get(key)
    if cached and now - cached[0] < cached[2]:
        return cached[1]
    disk = safe_path(os.path.dirname(os.path.abspath(__file__)),
                     f"cache_{symbol}_{interval}.json")
    if disk and os.path.exists(disk) and now - os.path.getmtime(disk) < 600:
        with open(disk) as fh:
            data = json.load(fh)
        _raw_cache[key] = (now, data, 600)
        return data
    start = int((now - RAW_DAYS * 86400) * 1000)
    deadline = now + FETCH_BUDGET
    out, cursor, full = [], int(now * 1000), False
    for _ in range(20):
        if time.time() > deadline:   # бюджет вышел — работаем с тем, что есть
            print(f"Bybit: не уложились в {FETCH_BUDGET}с по {symbol}/{interval}м,"
                  f" отдаём частичные данные ({len(out)} свечей)")
            break
        try:
            r = bybit().get_kline(category="linear", symbol=symbol,
                                  interval=interval, limit=1000, end=cursor)
            rows = r["result"]["list"]
        except Exception as e:       # биржа молчит/таймаут — не роняем страницу
            print(f"Bybit не ответил по {symbol}/{interval}м: {e}")
            break
        if not rows:
            full = True
            break
        out = rows[::-1] + out
        oldest = int(rows[-1][0])
        if oldest <= start or oldest >= cursor:
            full = True
            break
        cursor = oldest - 1
    else:
        full = True                  # 20 запросов — штатный предел, как и раньше
    data = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])]
            for x in out if int(x[0]) >= start]
    if full and disk:                # на диск — только полную историю
        with open(disk, "w") as fh:
            json.dump(data, fh)
    _raw_cache[key] = (now, data, 600 if full else 60)
    return data


@app.route("/api/candles/<symbol>/<mode>")
def api_candles(symbol, mode):
    if not valid_symbol(symbol) or not valid_mode(mode):
        return jsonify([]), 404
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
    if not candles:   # биржа не ответила — пустой результат не кэшируем
        return dict(events=[], n=0, wins=0, total_pnl=0,
                    note="данные греются, обновите страницу через минуту")
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
    if not valid_symbol(symbol) or not valid_mode(mode):
        return jsonify(dict(events=[], n=0, wins=0, total_pnl=0,
                            note="нет такого бота")), 404
    return jsonify(build_sim(symbol, mode))


_finalstats_cache = {}


@app.route("/api/finalstats/<symbol>")
def api_finalstats(symbol):
    """Помесячная (любое окно за 3.2г) и погодовая (3 года) статистика
    финального бота — из предпосчитанного finalize_final_bots.py."""
    if not valid_symbol(symbol):
        return jsonify(dict(available=False, error="нет такой монеты")), 404
    cached = _finalstats_cache.get(symbol)
    path = safe_path(FINAL_DATA_DIR, f"final_{symbol}.json")
    if path is None:
        return jsonify(dict(available=False, error="нет такой монеты")), 404
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
    log_file = bot_log_path(symbol, mode)
    if log_file is None:
        return jsonify(dict(error="нет такого бота")), 404
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
    log_file = bot_log_path(symbol, mode)
    if log_file is None:
        return jsonify(dict(active=False, tail=[], error="нет такого бота")), 404
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
    threading.Thread(target=_warmup, daemon=True).start()
    # порт через переменную окружения — чтобы поднять вторую копию для проверки,
    # не выключая ту, что уже работает на 8000
    app.run(host="127.0.0.1", port=int(os.environ.get("SITE_PORT", "8000")),
            debug=False)
