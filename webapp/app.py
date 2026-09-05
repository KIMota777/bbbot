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

from flask import Flask, jsonify, render_template, request

# Консоль Windows — cp866, и в ней нет ни тире «—», ни «ёлочек», ни эмодзи,
# которыми полны наши сообщения. Без этой поправки print падает с
# UnicodeEncodeError и убивает поток прогрева вместе с сообщением о нём.
# Поправка стоит на уровне модуля, а не в блоке __main__: сайт запускают и
# через «flask run», и через waitress/другой сервер — там __main__ чужой,
# а печатаем мы (прогрев, ошибки биржи) всё так же из этого модуля.
# line_buffering=True — потому что сайт запускают с перенаправлением вывода в
# файл (webapp/srv*.log в .gitignore), а при перенаправлении поток становится
# блочным: сообщения «битый файл данных ...» копятся в буфере килобайтами и до
# лога не доезжают, пока сервер работает. Именно тогда они и нужны.
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import bots_honest as bh  # noqa: E402
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
              "final": "Финальный",
    "normal_g": "обычный + гейт",
    "final_g": "финальный + гейт",
    "normal_w": "обычный, уровни раздвинуты",
    "final_w": "финальный, уровни раздвинуты",
    "bear_w": "медвежий, уровни раздвинуты",
    "final_wf": "уровни раздвинуты + мягкий гейт шорта",
}

# Все режимы из конфига, по порядку. Нужен именно полный список: разделы
# витрины отбирают по УРОВНЮ, а не по имени режима, и режим, забытый в
# перечислении, молча исчез бы с сайта, оставаясь при этом в конфиге.
# set обязателен: одно и то же имя режима есть у нескольких монет, а
# list_bots перебирает переданные имена внутри каждой монеты — с дублями в
# списке одна и та же карточка появилась бы на витрине по нескольку раз.
ALL_MODES = tuple(sorted(set(
    m for modes in config.SYMBOL_PARAMS.values() for m in modes
    if isinstance(modes[m], dict))))
ARCHIVE_MODES = ("normal", "bear", "turbo")

# --- проверка параметров маршрутов ---
# Параметры из URL подставляются в имена файлов, поэтому пропускаем только
# известные монеты/режимы и ключи без разделителей пути. На Windows
# разделителем считается и обратный слэш, а Flask его в параметре не режет —
# без проверки /api/analytics/..\..\..\report3y читал бы файл вне webapp/data.
MODESTATS_DIR = os.path.join(FINAL_DATA_DIR, "modestats")

VALID_MODES = frozenset(
    # Собирается ИЗ КОНФИГА, а не перечисляется руками: иначе каждый
    # новый режим (например с гейтом волатильности) молча получал бы 404
    # на свои свечи, а страница при этом открывалась бы — график пустой,
    # причина не видна.
    m for modes in config.SYMBOL_PARAMS.values() for m in modes
    if isinstance(modes[m], dict))
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
        usage="Плечо x10 выбрано правилом «наибольшее плечо с просадкой ≤20%», "
              "но правило считалось по ЗАНИЖЕННОЙ метрике — без плавающего "
              "убытка внутри цикла. По честной просадке (53.7% закрытая / "
              "60.4% плавающая) x10 этому правилу не удовлетворяет втрое. "
              "К реальным деньгам не подпускать. Запуск: python bot_rsi.py DOGE"),
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
              "движка могут делать результат только хуже. И главное: итог "
              "держится на горстке сделок — без одной лучшей он +184.5%, без "
              "трёх лучших +120.0%. На 57 сделках за 3.2 года это не «лучший "
              "бот портфеля», а выборка, где несколько удачных входов решают "
              "всё; повторяемость такого результата ничем не подтверждена.",
        usage="Плечо x15 — самое высокое в портфеле. Основанием была «устойчиво "
              "низкая просадка», но она считалась без плавающего убытка: честная "
              "просадка 25.0% закрытая / 25.5% плавающая, то есть правилу ≤20% "
              "конфиг не удовлетворяет. Отдельно про устойчивость: почти весь "
              "результат сделан в первой половине окна (+209.7% против +4.5%), "
              "а сдвиг порога RSI на единицу меняет итог с +113% на +261%. "
              "Запуск: python bot_rsi.py BTC"),
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


def read_json(path, default=None):
    """Прочитать JSON, не роняя страницу на битом или недописанном файле.

    Данные сайта пересобираются скриптами build_*.py: пока идёт запись, файл
    может быть валидным JSON с неполным содержимым или обрывком. Пользователь
    в этот момент видит устаревшие или пустые данные — это неприятно, но
    несравнимо лучше, чем 500 на всех страницах во время пересчёта.
    """
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:      # noqa: BLE001
        print(f"битый файл данных {os.path.basename(str(path))}: {e}")
        return default


def is_num(v):
    """Число, которым можно считать и которое можно форматировать.

    bool исключён нарочно: True прошёл бы как 1 и нарисовался бы как «1%».

    Целые в JSON не ограничены разрядностью: строка из четырёхсот цифр —
    законный int, но во float он не переводится, и «%+.1f» от него падает
    OverflowError. Такое число ничем не лучше строки: показать его как деньги
    всё равно нельзя. Проверяем переводимость прямо здесь, чтобы дальше по
    коду об этом можно было не помнить.
    """
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        f = float(v)
    except OverflowError:
        return False
    # NaN и бесконечность — законные литералы для json.load, но не числа для
    # показа. Хуже того, jsonify выводит их как NaN/Infinity, а браузерный
    # JSON.parse такого не принимает: график молча остаётся пустым, и никто
    # не понимает почему. Отсекаем на входе, а не после отрисовки.
    # (f != f — проверка на NaN без импорта math: NaN не равен сам себе.)
    if f != f or f in (float("inf"), float("-inf")):
        return False
    return True


def num(v, default=None):
    """Значение, если это число, иначе default.

    Правило всего файла: в шаблон уходит уже ПРИГОДНОЕ значение. Проверять
    тип в шаблоне нельзя — Jinja не умеет отказываться на полпути, и первая же
    строка «%.2f» от списка кладёт всю страницу.
    """
    return v if is_num(v) else default


def cell(v, dash="—"):
    """Значение для ячейки таблицы: скаляр — как есть, остальное — прочерк.

    Файлы данных пишут другие программы, и на месте цены может оказаться
    список или словарь. Падения от этого не будет — Jinja нарисует «{'a': 1}»,
    — но владелец читает эту таблицу как отчёт о деньгах, и мусор в ней хуже
    честного прочерка. bool тоже прочерк: True в колонке цены нарисуется как
    «True», и это не цена.
    """
    if v is None or isinstance(v, bool) or not isinstance(v, (str, int, float)):
        return dash
    return v


def fmt_ts(ms, dash="—"):
    """Время из миллисекунд или прочерк.

    Проверки «это число» тут мало: 1e20 и −1 — числа, но time.localtime на них
    падает (OverflowError, а на Windows ещё и OSError на отрицательных), и
    падает не где-нибудь, а внутри отрисовки КАЖДОЙ строки истории.
    """
    if not is_num(ms):
        return dash
    try:
        return time.strftime("%d.%m %H:%M", time.localtime(ms / 1000))
    except (ValueError, OSError, OverflowError):
        return dash


def stats_block(data):
    """Раздел stats предпосчёта — словарём, чем бы он ни оказался в файле.

    Тот же класс дефекта, что и «или пустой словарь» выше: конструкция
    data.get("stats") or {} ловит None и пустоту, но пропускает валидный JSON
    не того типа. Недописанный analytics_*.json вполне может отдать
    stats:"" или stats:[], и следующее же обращение .get() роняет страницу.
    """
    st = data.get("stats") if isinstance(data, dict) else None
    return st if isinstance(st, dict) else {}


def load_analytics(key):
    """Предпосчёт build_analytics.py (webapp/data/analytics_<key>.json) или {}.

    Сайт ничего не считает сам: числа обязаны совпадать со страницей /pnl.
    Битый или недописанный файл (идёт пересборка) — не повод ронять страницу.
    """
    data = read_json(safe_path(FINAL_DATA_DIR, f"analytics_{key}.json"), {})
    return data if isinstance(data, dict) else {}


def ruined_flag(data):
    """Слит ли счёт на этой стратегии.

    Признак ставит пересчёт бэктеста: прогон обрывается, когда капитала не
    хватает на убыток. Ключ принимаем и из stats, и из корня файла — самый
    важный для владельца факт не должен потеряться из-за того, куда именно
    его положил движок.
    """
    if not isinstance(data, dict):
        return False
    stats = stats_block(data)
    if data.get("ruined") or stats.get("ruined"):
        return True
    # страховка на случай, если признак не проставлен: капитал в нуле или
    # минусе — это слив по определению, каким бы ключом его ни называли
    end = stats.get("final_usd")
    if is_num(end):
        return end <= 0
    # NaN отдельно: is_num его больше не пропускает, а сравнение с нулём для
    # него всегда ложно — раньше счёт с испорченным капиталом молча считался
    # рабочим. Это ровно тот признак, ради которого всё и затевалось, поэтому
    # неизвестность здесь трактуется как повод предупредить, а не промолчать.
    return isinstance(end, float) and end != end



# ---- уровни честной переоценки (tierlist.py) --------------------------------
# Уровень отвечает не «сколько заработал», а «что про конфиг можно утверждать»:
# держится ли он на ОБЕИХ половинах истории, хватает ли настоящих (некопеечных)
# сделок, укладывается ли просадка в 20%. Все конфиги считаны на ОДНОМ плече
# x5 — иначе просадка меряет размер ставки, а не качество стратегии.
TIER_NAMES = {
    "A": "держится на обеих половинах",
    "B": "держится, но сделок мало",
    "C": "одна половина из двух — как монетка",
    "D": "не держится нигде",
    "F": "живёт на копеечных выходах",
}
TIER_RANK = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}


def load_tiers():
    """(символ, режим) -> уровень и числа общей шкалы. Пусто, если не считано."""
    path = os.path.join(FINAL_DATA_DIR, "tierlist.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:                              # noqa: BLE001
        return {}
    out = {}
    for r in d.get("rows", []):
        if r.get("src") != "config.py":
            continue
        out[(r["sym"], r["tag"])] = dict(
            tier=r["tier"], why=r["why"],
            train=round(r["train"]["comp"], 1), hold=round(r["hold"]["comp"], 1),
            rob_train=round(r["train"]["rob"], 1),
            rob_hold=round(r["hold"]["rob"], 1),
            # ПРОСАДКА НА КАРТОЧКЕ — ПЛАВАЮЩАЯ. Закрытая (только по
            # завершённым сделкам) занижала риск флагмана в 2.6 раза: 3.8%
            # против 9.7% на холдоуте. Закрытая остаётся рядом, для сравнения.
            dd=round(max(r["train"].get("dd_float", r["train"]["dd"]),
                         r["hold"].get("dd_float", r["hold"]["dd"])), 1),
            dd_closed=round(max(r["train"]["dd"], r["hold"]["dd"]), 1),
            real=min(r["train"]["real"]["n"], r["hold"]["real"]["n"]),
            tiny=round(max(r["train"]["tiny_share"], r["hold"]["tiny_share"])),
            p=round(max(r["train"]["real"]["p"], r["hold"]["real"]["p"]), 3),
            # Разбор конкретного конфига (пока он есть только у LTC/final_wf).
            # Здесь лежит то, чего не видно по уровню: просадка в деньгах,
            # значимость против случайного момента входа, сравнение с простым
            # шортом и результат на строго невиданном отрезке.
            audit=r.get("audit"),
            rob_method=r.get("rob_method"),
            name=TIER_NAMES.get(r["tier"], ""))
    # Режимы с ОДИНАКОВЫМ геномом наследуют уровень друг у друга. При расчёте
    # дубли схлопываются, и уровень записывается только на один из них: у ETH
    # final побайтово совпадает с normal, поэтому final остался бы без уровня
    # и выглядел бы непроверенным, хотя проверен.
    for sym, modes in config.SYMBOL_PARAMS.items():
        have = [m for m in modes
                if isinstance(modes[m], dict) and (sym, m) in out]
        for mode, prm in modes.items():
            if not isinstance(prm, dict) or (sym, mode) in out:
                continue
            for m in have:
                if modes[m] == prm:
                    out[(sym, mode)] = dict(out[(sym, m)], same_as=m)
                    break
    return out


def passed_bots():
    """Конфиги, прошедшие честную переоценку: уровень A или B.

    Это НЕ «самые доходные». Уровень A значит «нечему возразить»: держится на
    обеих половинах, настоящих сделок хватает, просадка в норме. Ни один не
    значим после поправки на число проверенных, и на витрине это сказано.
    """
    out = [b for b in list_bots(ALL_MODES)
           if b.get("tier") and b["tier"]["tier"] in ("A", "B")]
    out.sort(key=lambda b: (TIER_RANK[b["tier"]["tier"]],
                            -b["tier"]["rob_hold"]))
    return out


def gated_bots():
    """Конфиги с включённым гейтом волатильности — новые.

    Показываются отдельно: это не «лучшие», а те, у кого гейт заметно снизил
    просадку. Уровень у них свой и на витрине виден как есть.
    """
    return [b for b in list_bots(("normal_g", "final_g", "bear_g"))
            if b.get("gated")
            and not (b.get("tier") and b["tier"]["tier"] in ("A", "B"))]


def retired_running():
    """Отставленные конфиги, которые ВСЁ ЕЩЁ запущены. Пустой список — норма."""
    return [b for b in list_bots(("final",), include_retired=True)
            if b.get("retired") and b.get("active")]


def failed_bots():
    """Всё остальное, включая работающее. Прячем с витрины, но не удаляем.

    Спрятать работающего бота нельзя: тогда сайт показывал бы красивую
    картину, пока в фоне крутится провалившее проверку. Поэтому провалившиеся
    уходят вниз с честной пометкой, а не исчезают.
    """
    out = [b for b in list_bots(("final",))
           if not (b.get("tier") and b["tier"]["tier"] in ("A", "B"))]
    out.sort(key=lambda b: TIER_RANK.get(
        (b.get("tier") or {}).get("tier", "D"), 3))
    return out


def list_bots(modes, include_retired=False):
    """Боты для витрины. Отставленные по умолчанию не выдаются.

    include_retired=True нужен одному месту — предупреждению «отставлен, но
    запущен»: скрыть работающего бота нельзя, иначе сайт покажет чистую
    картину при работающем в фоне провалившем проверку.
    """
    bots = []
    tiers = load_tiers()
    for sym, sym_modes in config.SYMBOL_PARAMS.items():
        for mode in modes:
            p = sym_modes.get(mode)
            if not p:
                continue
            retired = config.retire_reason(sym, mode)                 if hasattr(config, "retire_reason") else None
            if retired and not include_retired:
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
                a = stats_block(data)
                # Ключей может не быть: пересборка данных идёт в несколько
                # проходов, и между ними файл — валидный JSON с неполным stats.
                # Раньше здесь стоял прямой доступ a["final_pct"], и главная
                # страница падала в 500 ровно в момент пересчёта.
                # Проверяем ещё и тип: pct из недописанного файла может быть
                # строкой, а сравнение pct >= 0 со строкой — тот же 500.
                pct, usd = a.get("final_pct"), a.get("final_usd")
                # оба числа проверяем одинаково: раньше usd проверялся только
                # на «не None», и список в этом поле печатался как сумма денег
                if is_num(pct) and is_num(usd):
                    sign = "+" if pct >= 0 else ""
                    reinvest = (f"💰 с реинвестом: $50 → ${usd:.2f} "
                                f"({sign}{pct}%)")
                if a:
                    # обе просадки: закрытая (по завершённым сделкам) занижена,
                    # именно по ней когда-то выбиралось плечо
                    dd, dd_float = a.get("max_dd"), a.get("max_dd_float")
            bots.append(dict(
                tier=tiers.get((sym, mode)),
                retired=retired,
                gated=bool(p.get("vol_gate")),
                widened=mode.endswith("_w"),
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
            # index и heat шаблон форматирует («%+.2f») и сравнивает с порогом.
            # Фон собирает news_state из файла новостей — то есть значение
            # приходит извне, и не числом оно роняет ГЛАВНУЮ страницу. Свой
            # try/except внизу этого не ловит: падение происходит позже, уже
            # в отрисовке шаблона.
            index, heat = num(bg.get("index")), num(bg.get("heat"))
            if index is None or heat is None:
                continue
            rows.append(dict(
                coin=sym.replace("USDT", ""), index=index,
                heat=heat, n=cell(bg.get("n"), 0), stale=bg.get("stale"),
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
                           passed=passed_bots(), failed=failed_bots(),
                           gated=gated_bots(), retired_live=retired_running(),
                           has_final=bool(finals), news=news_panel())


@app.route("/archive")
def archive():
    return render_template("archive.html", bots=archive_bot_list(),
                           dry_run=config.DRY_RUN)


# Полоса возврата для страниц, которые отдаются готовым файлом, а не шаблоном.
# Своя разметка и свои стили прямо в атрибутах: отчёт исследования светлый и
# несёт собственный CSS, где есть правила и для nav, и для a. Классы сайта туда
# тащить нельзя — подерутся; а inline-стиль перебивает таблицу стилей документа.
_BACK_BAR = (
    '<div style="background:#11151c;padding:10px 16px;font:14px/1.4 system-ui,'
    'Segoe UI,Arial,sans-serif;display:flex;flex-wrap:wrap;gap:6px 18px;'
    'align-items:center">'
    '<a href="/" style="color:#9fc0e8;text-decoration:none;font-weight:600">'
    '&larr; Боты</a>'
    '<a href="/signals" style="color:#8a929d;text-decoration:none">Сигналы BTC</a>'
    '<a href="/pnl" style="color:#8a929d;text-decoration:none">PnL-график</a>'
    '<a href="/evolution" style="color:#8a929d;text-decoration:none">Эволюция</a>'
    '<a href="/archive" style="color:#8a929d;text-decoration:none">Архив</a>'
    '<span style="color:#e8e8e8;font-weight:600">Исследование</span>'
    '</div>'
)


@app.route("/research")
def research_report():
    """Отчёт исследования — отдаётся с диска как есть.

    Файл собирается отдельно (research/ОТЧЁТ.html) и лежит рядом с кодом,
    которым он посчитан. Отдаём его прямо с сайта, а не ссылкой наружу, чтобы
    рядом с обещаниями доходности всегда лежала проверка этих обещаний — и
    чтобы она не пропала вместе с внешней ссылкой.
    """
    path = os.path.join(BOT_DIR, "research", "ОТЧЁТ.html")
    if not os.path.exists(path):
        return ("Отчёт ещё не собран: нет research/ОТЧЁТ.html", 404,
                {"Content-Type": "text/plain; charset=utf-8"})
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    if "<!doctype" in body[:200].lower():
        # даже у готового документа должна быть дорога назад на сайт
        body = re.sub(r"(<body[^>]*>)",
                      lambda m: m.group(1) + _BACK_BAR,
                      body, count=1, flags=re.I)
        return body, 200, {"Content-Type": "text/html; charset=utf-8"}
    # Файл написан как содержимое страницы без обёртки: он начинается сразу с
    # <title> и <style>. Просто завернуть его в <body> нельзя — заголовок
    # окажется в теле, и вкладка останется без имени. Поэтому вынимаем голову
    # и кладём куда положено.
    head = ""
    for tag in ("title", "style"):
        for m in re.finditer(r"<%s\b.*?</%s>" % (tag, tag), body,
                             re.S | re.I):
            head += m.group(0)
        body = re.sub(r"<%s\b.*?</%s>" % (tag, tag), "", body, flags=re.S | re.I)
    # значок вкладки как у остальных страниц: без него браузер стучится в
    # несуществующий /favicon.ico и пишет 404 в консоль
    fav = ("<link rel=\"icon\" href=\"data:image/svg+xml,"
           "%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 16 16"
           "%27%3E%3Ctext y=%2714%27 font-size=%2714%27%3E%F0%9F%94%AC%3C"
           "/text%3E%3C/svg%3E\">")
    page = ("<!doctype html><html lang=\"ru\"><head>"
            "<meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,"
            " initial-scale=1\">" + fav + head + "</head><body>" + _BACK_BAR
            + body.strip() + "</body></html>")
    return page, 200, {"Content-Type": "text/html; charset=utf-8"}


RU_SETUPS = {
    "range_long": "Боковик: лонг от нижней границы",
    "range_short": "Боковик: шорт от верхней границы",
    "sweep_long": "Ложный пробой низа: лонг",
    "sweep_short": "Ложный пробой верха: шорт",
    "dump_long": "Капитуляция: лонг после обвала",
    "pump_short": "Перегрев: шорт после вертикального роста",
}


def setup_numbers(rec, data):
    """Числа сетапа из ОДНОГО источника.

    Источников два, и они не совпадают: signal_setups.json хранит результат
    отбора (фиксированная маржа $5), webapp/data/analytics_sig_*.json —
    пересчёт честным движком с реинвестом. На pump_short это давало «DD 21.4%»
    в одной строке и «просадка закрытая 44.6%» строкой выше: два разных числа
    про одно и то же, и владелец не мог понять, какому верить. Главным берём
    предпосчёт — он новее и считается ровно тем же кодом, что карточка
    аналитики ниже на странице. Строка отбора остаётся только там, где
    предпосчёта ещё нет, и тогда подписана явно.
    """
    # оба раздела берём через проверку типа: и signal_setups.json, и
    # analytics_*.json переписываются на ходу, и «stats» в них может оказаться
    # чем угодно — обращение .get() к строке роняет обе страницы сетапов
    st = rec.get("stats") if isinstance(rec, dict) else None
    st = st if isinstance(st, dict) else {}
    a = stats_block(data)
    # Здесь всё до единого — числа (доход, сделки, винрейт, просадки, плечо),
    # и шаблон рисует их как «{{ st.ret }}» с запасным «?». Значит «?» обязано
    # появляться и тогда, когда в файле на месте доходности лежит список или
    # строка, а не только когда ключа нет вовсе.
    def one(src, **kw):
        return dict(src=src, **{k: num(v) for k, v in kw.items()})
    if a.get("final_pct") is not None:
        return one("реинвест от $50, маржа 25% капитала (предпосчёт)",
                   ret=a.get("final_pct"), n=a.get("trades"), wr=a.get("wr"),
                   dd=a.get("max_dd"), dd_float=a.get("max_dd_float"),
                   hold=a.get("avg_hold_h"), lev=a.get("lev"))
    return one("числа отбора, фиксированная маржа $5 — предпосчёт "
               "по этому сетапу ещё не собран",
               ret=st.get("ret"), n=st.get("n"), wr=st.get("wr"),
               dd=st.get("dd"), dd_float=None,
               hold=st.get("avg_hold_h"), lev=rec.get("rec_lev"))


def clean_history(rows):
    """Строки истории сигналов, ГОТОВЫЕ к печати: все числа уже отформатированы.

    signals.json пишет живой advisor.py, и обрывок записи даёт валидный JSON
    со строкой там, где ждём число. Такая строка роняла страницу дважды:
    сначала сортировка по exit_ts (сравнить строку с числом нельзя), потом
    формат «%+.1f» у R. Показать её всё равно нечем, поэтому пропускаем и
    говорим об этом в лог — молча терять данные хуже, чем шумно.

    Дальше по строке шаблон делал ещё три вещи, каждая из которых умела
    падать, и ни одну эта проверка не закрывала:
      * ru.get(h.setup, h.setup) — setup уходил КЛЮЧОМ словаря названий, а
        список и словарь ключом быть не могут (TypeError: unhashable);
      * «%+.2f» от pnl_usd — а pnl_usd со значением None здесь ПРОПУСКАЛСЯ
        нарочно: ключ есть, значение None, формат падает;
      * fmt_ts(h.exit_ts) — is_num проходит и 1e20, а localtime на нём падает.
    Поэтому отсюда наружу выходит строка, в которой печатать уже нечего:
    и текст, и класс цвета посчитаны здесь, в коде.
    """
    if not isinstance(rows, list):
        return []
    out, dropped = [], 0
    for h in rows:
        if not isinstance(h, dict):
            dropped += 1
            continue
        exit_ts, r = h.get("exit_ts"), h.get("r")
        cap, pnl = h.get("capital_after"), h.get("pnl_usd")
        if not (is_num(exit_ts) and is_num(r)):
            dropped += 1
            continue
        if not (cap is None or is_num(cap)) or not (pnl is None or is_num(pnl)):
            dropped += 1
            continue
        # setup — ключ словаря RU_SETUPS. Нехешируемое (список, словарь) сюда
        # пускать нельзя даже ради «показать как есть».
        setup = h.get("setup")
        setup_ru = (RU_SETUPS.get(setup, setup)
                    if isinstance(setup, str) else "—")
        outcome = cell(h.get("outcome"))
        out.append(dict(
            ts_str=fmt_ts(exit_ts), setup_ru=setup_ru,
            side=cell(h.get("side")), entry=cell(h.get("entry")),
            stop=cell(h.get("stop")), tp=cell(h.get("tp")),
            outcome=outcome,
            outcome_cls=("pos" if outcome == "ТЕЙК" else
                         "neg" if outcome == "СТОП" else ""),
            # ноль — это ноль, а не убыток: красить его красным и ставить «+»
            # значит показывать сделку в безубыток как проигранную
            r_str="%+.1f" % r,
            r_cls=("pos" if r > 0 else "neg" if r < 0 else ""),
            # None у pnl_usd — это «не записано», а не ноль: рисуем прочерк
            # без цвета, а не «+0.00» зелёным
            pnl_str=("—" if pnl is None else "%+.2f" % pnl),
            pnl_cls=("" if pnl is None else
                     "pos" if pnl > 0 else "neg" if pnl < 0 else ""),
            # капитал РОВНО ноль — это слив, а не «не записано»: через «not cap»
            # оба случая рисовались одинаковым прочерком, и самый важный для
            # владельца исход исчезал из таблицы
            cap_str=("$%.2f" % cap if is_num(cap) else "—"),
            hold_h=cell(h.get("hold_h")), exit_ts=exit_ts))
    if dropped:
        print(f"signals.json: пропущено строк истории без чисел: {dropped}")
    return out


def clean_active(act):
    """Активные сигналы, приведённые к виду, который шаблон точно нарисует.

    Проверки signal_ts/hold_until мало: у сигнала с маржой шаблон считал
    risk_1r * 3 и форматировал результат. risk_1r никто не проверял — и
    сигнал с текстовым или отсутствующим риском (обычное дело, если advisor.py
    как раз дописывает запись) клал всю страницу сигналов. Считаем цель здесь:
    в шаблоне арифметике не место.
    """
    if not isinstance(act, dict):
        return {}
    out = {}
    for k, v in act.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        sig_ts, hold_until = v.get("signal_ts"), v.get("hold_until")
        if not (is_num(sig_ts) and is_num(hold_until)):
            continue
        margin, risk = num(v.get("margin")), num(v.get("risk_1r"))
        # блок про деньги показываем, только если ОБА числа на месте: половина
        # («маржа $5, риск —») хуже, чем его отсутствие
        if margin is None or risk is None:
            margin = risk = risk3 = None
        else:
            risk3 = round(risk * 3, 2)
        out[k] = dict(
            entry=cell(v.get("entry")), stop=cell(v.get("stop")),
            stop_pct=cell(v.get("stop_pct")), tp=cell(v.get("tp")),
            tp_pct=cell(v.get("tp_pct")), lev=cell(v.get("lev")),
            margin=margin, risk_1r=risk, risk_3r=risk3,
            signal_str=fmt_ts(sig_ts), hold_str=fmt_ts(hold_until))
    return out


@app.route("/signals")
def signals_page():
    # оба файла читаем через read_json: страница со списком сетапов не должна
    # падать в 500, пока advisor.py дописывает signals.json
    setups, status = load_signal_setups()
    state = read_json(os.path.join(FINAL_DATA_DIR, "signals.json"), {})
    if not isinstance(state, dict):     # обрывок записи мог дать список/число
        state = {}
    # признак слива и числа — те же, что на странице сетапа. Раньше ruined
    # уходил в шаблон только из signal_page(), и в списке слитый сетап
    # выглядел ровно как рабочий.
    stats, ruined = {}, {}
    for nm, rec in setups.items():
        data = load_analytics(f"sig_{nm}")
        stats[nm] = setup_numbers(rec, data)
        ruined[nm] = ruined_flag(data)
    # обе половины файла приводим к готовому виду в КОДЕ: шаблон только
    # печатает то, что ему дали, и не умеет падать ни на одном значении
    act = clean_active(state.get("active"))
    hist = sorted(clean_history(state.get("history")),
                  key=lambda h: h["exit_ts"], reverse=True)[:40]
    # капитал сетапа шаблон форматировал как «%.2f» и умножал на 0.25 — теперь
    # обе строки считаются здесь, из значения, которое точно число
    raw_caps = state.get("capital")
    raw_caps = raw_caps if isinstance(raw_caps, dict) else {}
    caps = {}
    for nm in setups:
        c = num(raw_caps.get(nm), 50.0)
        caps[nm] = dict(cap="%.2f" % c, next="%.2f" % (c * 0.25))
    adv_log = os.path.join(BOT_DIR, "advisor.log")
    running = (os.path.exists(adv_log) and
               time.time() - os.path.getmtime(adv_log) < 180)
    return render_template("signals.html", setups=setups, ru=RU_SETUPS,
                           active=act, history=hist,
                           capitals=caps,
                           running=running, stats=stats, ruined=ruined,
                           # честная причина пустого списка: «отбор не
                           # запускали» и «файл повреждён» — разные новости
                           setups_note="" if setups else setups_unavailable(status),
                           # ни один сетап не прошёл приёмку — это состояние
                           # портфеля, а не мелочь в карточке: говорим вверху
                           none_passed=bool(setups) and not any(
                               r.get("enabled", True) for r in setups.values()))


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
    """(сетапы, состояние файла: "ok" | "missing" | "bad").

    Состояние нужно, чтобы отличить три РАЗНЫЕ новости: «файл читается»,
    «файла нет вовсе» и «файл сейчас переписывается или повреждён». Раньше
    последние две сливались в одну, и на свежей копии, где отбор ни разу не
    запускали, сайт рассказывал про повреждение файла, которого никогда не
    существовало.

    Значения проверяем по одному, а не только верхний уровень. Проверки
    isinstance(data, dict) мало: обрывок записи даёт валидный JSON, где вместо
    словаря параметров лежит строка или число, — а дальше и обработчик, и
    шаблон обращаются к сетапу как к словарю, и обе страницы падают в 500.
    """
    path = os.path.join(BOT_DIR, "signal_setups.json")
    if not os.path.exists(path):
        return {}, "missing"
    data = read_json(path)
    if not isinstance(data, dict):
        return {}, "bad"
    setups = {k: v for k, v in data.items()
              if isinstance(k, str) and isinstance(v, dict)}
    if len(setups) != len(data):
        print("signal_setups.json: пропущены сетапы, параметры которых не словарь")
    return setups, "ok"


def setups_unavailable(status):
    """Честный текст вместо «нет такого сетапа», когда сетап-то есть.

    Пока evolution9.py переписывает signal_setups.json, ссылка со страницы
    сигналов вела в 404 «Нет такого сетапа» — а это враньё: сетап никуда не
    девался, просто файл в этот миг нечитаем. Владелец решил бы, что страница
    разбора пропала навсегда, и полез бы её искать.

    Но и обратное враньё не годится: на свежей копии файла нет вообще, и
    рассказ про «переписывается или повреждён» посылает искать поломку там,
    где её нет. Причин три — и текстов тоже три.
    """
    if status == "missing":
        return ("Параметров сетапов ещё нет: файл signal_setups.json не "
                "создан — отбор (evolution9.py) на этой копии ни разу не "
                "запускали. Это не поломка: запусти отбор, и страница "
                "заработает.")
    if status == "bad":
        return ("Параметры сетапов сейчас недоступны: файл signal_setups.json "
                "переписывается отбором или повреждён. Сетап никуда не делся — "
                "обновите страницу через минуту.")
    return ("Файл signal_setups.json прочитан, но этого сетапа в нём нет: "
            "последний отбор его не сохранил. Список доступных сетапов — "
            "на странице сигналов.")


# Гены, которыми движок НАРЕЗАЕТ списки: целое неотрицательное и ничего
# другого. Дробное окно или отрицательный возраст уровня — это не «другая
# стратегия», это TypeError в глубине signal_engine.
GENOME_INT = ("rsi_idx", "window", "age", "drop_days", "hold_days")
# Гены-множители: годится любое число.
GENOME_NUM = ("zone", "rsi_os", "buf_atr", "stop_cap", "poke_atr",
              "drop_frac", "cooldown")


def genome_problem(g, n_rsi):
    """Человеческое описание того, чем геном не годен, или "" если годен.

    Возвращаем именно текст, а не False: он уходит на страницу и в лог, и по
    нему сразу видно, какой ген испорчен, — иначе владелец видит «график не
    рисуется» и не знает, куда смотреть.
    """
    if not isinstance(g, dict):
        return "это не набор параметров"
    for k in GENOME_INT:
        v = g.get(k)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            return f"ген {k} должен быть целым неотрицательным, а он {v!r}"
    if g["rsi_idx"] >= n_rsi:           # номер периода RSI — индекс в списке
        return f"ген rsi_idx={g['rsi_idx']} за пределами списка периодов RSI"
    if g["window"] < 1 or g["hold_days"] < 1:
        return "окно уровней и срок удержания не могут быть нулевыми"
    for k in GENOME_NUM:
        if not is_num(g.get(k)):
            return f"ген {k} должен быть числом, а он {g.get(k)!r}"
    return ""


@app.route("/signal/<name>")
def signal_page(name):
    setups, status = load_signal_setups()
    rec = setups.get(name)
    if not rec:
        # 404 оставляем только для действительно несуществующего адреса
        if status != "ok" or name in RU_SETUPS:
            return setups_unavailable(status), 503
        return "Нет такого сетапа", 404
    # то же, что у ботов: слив счёта и просадки — из предпосчёта, прямо в
    # разметку, не полагаясь на JS. Все числа шапки идут одним набором из
    # setup_numbers, чтобы просадка в плашке и в строке бэктеста совпадали.
    data = load_analytics(f"sig_{name}")
    return render_template("signal.html", name=name,
                           title=RU_SETUPS.get(name, name), rec=rec,
                           ruined=ruined_flag(data),
                           st=setup_numbers(rec, data),
                           enabled=rec.get("enabled", True),
                           note=rec.get("note", ""))


@app.route("/api/signal_chart/<name>")
def api_signal_chart(name):
    """4ч свечи BTC (полные 3.2г) + все сделки бэктеста сетапа + активный
    сигнал, если есть."""
    cached = _sig_chart_cache.get(name)
    if cached and time.time() - cached[0] < 1800:
        return jsonify(cached[1])
    setups, status = load_signal_setups()
    rec = setups.get(name)
    if not rec:
        if status != "ok" or name in RU_SETUPS:
            return jsonify(dict(candles=[], trades=[], active=None, warming=True,
                                note=setups_unavailable(status))), 503
        return jsonify(dict(error="нет сетапа")), 404
    lev = rec.get("rec_lev", 15)
    if not is_num(lev):                 # плечо уходит в арифметику движка
        lev = 15
    import signal_engine as se
    # Геном — единственное, без чего движок не запустится. Проверки
    # isinstance(genome, dict) МАЛО: движок берёт из него window, age,
    # hold_days как длины (ими нарезают списки — нужен целый неотрицательный),
    # rsi_idx как номер периода RSI, остальное — как множители. Пустой словарь
    # давал KeyError, строка вместо числа — TypeError, и оба прилетали
    # пятисоткой на график сетапа.
    bad = genome_problem(rec.get("genome"), len(se.RSI_SET))
    if bad:
        return jsonify(dict(candles=[], trades=[], active=None, warming=True,
                            note=f"Параметры сетапа «{name}» негодны: {bad}. "
                                 "Файл signal_setups.json переписывается "
                                 "отбором или повреждён.")), 503
    d = _btc_signal_data()
    if d is None:                       # история ещё качается — не держим страницу
        return jsonify(dict(candles=[], trades=[], active=None, warming=True,
                            note="Данные греются, обновите страницу через минуту")), 503
    try:
        r = se.run_setup(name, rec["genome"], d["c4"], d["ctx"], d["c15"],
                         d["ts15"], lev)
    except Exception as e:              # noqa: BLE001 — страховка поверх проверки
        # Проверка выше знает про имена генов, но не про их сочетания: набор
        # значений, каждое из которых по отдельности законно, всё равно может
        # завести движок в тупик. График — не то место, ради которого стоит
        # ронять страницу целиком.
        print(f"движок не смог посчитать сетап {name}: {e!r}")
        return jsonify(dict(candles=[], trades=[], active=None, warming=True,
                            note=f"Движок не смог посчитать сетап «{name}» "
                                 "на текущих параметрах — смотри консоль "
                                 "сайта.")), 503
    candles = [dict(time=c[0] // 1000, open=c[1], high=c[2], low=c[3],
                    close=c[4]) for c in d["c4"]]
    trades = [dict(entry_ts=t["entry_ts"] // 1000, exit_ts=t["exit_ts"] // 1000,
                   side=t["side"], entry=t["entry"], stop=t["stop"],
                   tp=t["tp"], pnl=t["pnl"], r=t["r"], reason=t["reason"],
                   hold_h=t["hold_h"]) for t in r["trades"]]
    # signals.json переписывает живой advisor.py: в момент записи файл бывает
    # оборванным, и график сетапа не должен из-за этого отдавать 500
    state = read_json(os.path.join(FINAL_DATA_DIR, "signals.json"), {})
    act_all = state.get("active") if isinstance(state, dict) else None
    active = act_all.get(name) if isinstance(act_all, dict) else None
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
    data = read_json(path)
    if not isinstance(data, dict):
        return jsonify(dict(available=False, error="файл данных повреждён"))
    data["available"] = True
    _analytics_cache[key] = (mtime, data)
    return jsonify(data)


@app.route("/api/pnl_curves")
def api_pnl_curves():
    p = os.path.join(FINAL_DATA_DIR, "pnl_curves.json")
    data = read_json(p)
    # тот же класс: «или пустой ответ» не спасает от валидного JSON не того
    # типа. Страница /pnl перебирает data.series — без списка там ничего не
    # нарисуется, поэтому отдаём честно пустой набор кривых
    if not isinstance(data, dict) or not isinstance(data.get("series"), list):
        return jsonify(dict(series=[]))
    return jsonify(data)


@app.route("/api/pnl_common")
def api_pnl_common():
    """Кривые в ОБЩЕЙ шкале: одно плечо x5 у всех, копеечные выходы обнулены.

    Отдельная ручка, а не замена прежней: прежние кривые отвечают на вопрос
    «сколько бы заработал этот бот на своём плече», эти — на вопрос «какой из
    конфигов лучше». Смешивать их в одном ответе нельзя, числа несравнимы.
    """
    path = os.path.join(FINAL_DATA_DIR, "pnl_common.json")
    if not os.path.exists(path):
        return jsonify(dict(series=[], note="не собрано: "
                                            "python build_pnl_common.py"))
    with open(path, encoding="utf-8") as fh:
        return jsonify(json.load(fh))


@app.route("/evolution")
def evolution_page():
    path = os.path.join(FINAL_DATA_DIR, "evolution_timeline.json")
    raw = read_json(path, {})
    # «read_json(...) or {}» ловило None и пустоту, но НЕ ловило валидный JSON
    # не того типа: список/строка/число проходили дальше, и шаблон падал в 500
    # на timeline.get(). Проверяем тип, а не «не пусто».
    # То же на уровень ниже: волну шаблон рисует как число (форматирует ret и
    # сравнивает его с нулём), поэтому берём только те волны, у которых ret
    # действительно число — иначе страница снова 500, только глубже.
    timeline = {}
    if isinstance(raw, dict):
        for sym, d in raw.items():
            if not isinstance(d, dict) or not isinstance(d.get("waves"), list):
                continue
            waves = [w for w in d["waves"]
                     if isinstance(w, dict) and is_num(w.get("ret"))]
            if waves:
                timeline[sym] = dict(d, waves=waves)
    coins = {sym: sym.replace("USDT", "") for sym in config.SYMBOL_PARAMS}
    return render_template("evolution.html", timeline=timeline, coins=coins)


@app.route("/bot/<symbol>/<mode>")
def bot_page(symbol, mode):
    p = config.SYMBOL_PARAMS.get(symbol, {}).get(mode)
    if not p:
        # Режим, вынесенный из рабочих конфигов, отвечает объяснением, а не
        # голым 404: по старой ссылке человек должен узнать, куда делся бот и
        # почему, иначе исчезновение выглядит как поломка сайта.
        legacy = getattr(config, "LEGACY_UNVERIFIABLE", {}).get((symbol, mode))
        if legacy:
            return render_template("gone.html", symbol=symbol,
                                   coin=symbol.replace("USDT", ""),
                                   mode=mode, prm=legacy), 410
        return "Нет такого бота", 404
    meta = META.get((symbol, mode), {})
    interval = p.get("interval", "15")
    # слив счёта и просадки рисуем прямо в разметке, а не только в карточке
    # аналитики на JS: если скрипт не отработал, владелец всё равно обязан
    # увидеть, что счёт слит
    data = load_analytics(f"bot_{symbol}") if mode == "final" else {}
    an = stats_block(data)
    return render_template(
        "bot.html", symbol=symbol, coin=symbol.replace("USDT", ""),
        # Уровень общей шкалы и признак отставки. Страница снятого бота
        # открывается по прямой ссылке, и без пометки она показывала бы старые
        # числа так, будто бот в строю.
        tier=load_tiers().get((symbol, mode)),
        retired=(config.retire_reason(symbol, mode)
                 if hasattr(config, "retire_reason") else None),
        # Расширенный вариант и его исходник: страница открывается по прямой
        # ссылке, и без этой пары непонятно, чем «final_w» отличается от
        # «final» и почему у них разные числа.
        widened=(mode.endswith("_w") and mode[:-2] or None),
        # Настоящий архивный режим — только normal/bear/turbo. Режимы с
        # гейтом и с раздвинутыми уровнями появились уже после починки движка,
        # и плашка «числа посчитаны старым движком» на них лгала бы.
        archive=(mode in ARCHIVE_MODES),
        mode=mode, mode_name=MODE_NAMES.get(mode, mode), lev=p.get("lev", 5),
        interval_min=int(interval),
        tf_label=("4ч" if interval == "240" else f"{interval}m"),
        ver=VER.get((symbol, mode), ""),
        title=meta.get("title", f"{symbol} {mode}"),
        stats=meta.get("stats", ""), about=meta.get("about", ""),
        usage=meta.get("usage", ""), core=STRATEGY_CORE,
        ruined=ruined_flag(data), dd=an.get("max_dd"),
        dd_float=an.get("max_dd_float"), trades=an.get("trades"),
        # таймфреймы для переключателя над графиком; свой (боевой) помечаем,
        # чтобы было видно, на каком бот на самом деле принимает решения
        chart_tfs=[dict(tf=t, label=CHART_TF_LABELS[t], own=(t == interval))
                   for t in CHART_TFS],
        params=p, dry_run=config.DRY_RUN)


CANDLE_DAYS = 90    # окно графика ("с мая" с запасом)
RAW_DAYS = 130      # + тёплый старт симуляции (окна до 877 свечей, EMA, режим)

# Таймфреймы, которые можно выбрать на графике бота: значение — во сколько раз
# шире брать окно, чтобы на экране осталось сопоставимое число свечей. На 15m
# 90 дней — это 8640 баров, а на 4ч столько же дней дадут всего 540, и график
# выглядит обрубком. Ключи — минуты строкой, как их понимает Bybit и как их
# проверяет get_raw_candles (там стоит isdigit).
#
# У 4ч коэффициент подобран так, чтобы окно накрывало ВСЮ историю проверки:
# 90 * 13 = 1170 дней при истории в 1150. До этого стояло 8, то есть 720 дней,
# и на графике LTC/final_w было видно 147 закрытий из 289 — половина работы
# бота просто не помещалась в окно, и это читалось как «бот мало торгует».
# 1170 дней на 4ч это ~7000 баров: библиотека тянет столько без труда.
CHART_TFS = {"5": 0.35, "15": 1, "60": 3, "240": 13}
CHART_TF_LABELS = {"5": "5м", "15": "15м", "60": "1ч", "240": "4ч"}
_raw_cache = {}     # (symbol,interval) -> (fetched_at, candles, ttl_сек)
# Запись кэша на диск — по одному потоку за раз. На Windows переименование
# поверх файла, который в тот же миг переименовывает сосед, даёт «отказано в
# доступе», и в лог сыплется «кэш не сохранён» на ровном месте.
_disk_lock = threading.Lock()


def save_disk_cache(disk, data, symbol, interval):
    """Записать кэш свечей атомарно. True — записан, False — не вышло.

    Пишем во временный файл и переименовываем: смена имени атомарна, так что
    убитый посреди записи процесс оставляет либо прежний целый кэш, либо
    ничего — но никогда обрывок. Это лечит причину, а не симптом: чтение выше
    обрывок переживёт, но лучше его вообще не создавать.

    С ОБЩИМ именем «путь + .tmp» атомарности не выходило: dev-сервер
    многопоточный, рядом крутится фоновый прогрев, и несколько потоков писали
    в ОДИН временный файл одновременно — а чужой os.remove в обработчике
    ошибки сносил временный файл соседа. На стенде (8 потоков, 10 раундов)
    кэш после такой гонки не оставался целым НИ РАЗУ. Поэтому имя временного
    файла своё у каждого потока И каждого процесса.

    Замок _disk_lock общий, но только внутри ЭТОГО процесса, и другого замка
    у нас нет: вторая копия сайта (проверочная на своём порту рядом с рабочей)
    про него не знает. На Windows переименование поверх файла, который сосед в
    тот же миг переименовывает, даёт «отказано в доступе». Данные при этом
    целы — остаётся вариант соседа, — вся беда в шуме: в лог сыпалось «кэш не
    сохранён» там, где кэш на самом деле есть. Отсюда повторы: чужое
    переименование длится миллисекунды. Функция вынесена отдельно ещё и
    затем, чтобы это можно было проверить запуском нескольких процессов, а не
    только пообещать в комментарии.
    """
    tmp = f"{disk}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with _disk_lock:
            with open(tmp, "w") as fh:
                json.dump(data, fh)
            for attempt in range(5):
                try:
                    os.replace(tmp, disk)
                    return True
                except PermissionError:   # сосед переименовывает прямо сейчас
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))
    except OSError as e:             # нет места/прав — работаем без кэша
        print(f"кэш свечей {symbol}/{interval}м не сохранён: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass
    return False


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
        # Этот кэш пишет сам сайт, и до правки ниже он писался не атомарно:
        # процесс, убитый Ctrl+C посреди json.dump (обычный способ остановить
        # start_site.bat), оставлял файл, оборванный на середине. Прямой
        # json.load на таком обрывке валил /api/candles и /api/sim в 500 —
        # и не разово, а все 10 минут, пока обрывок считается свежим.
        # Теперь непрочитанный кэш просто игнорируем и качаем историю заново.
        data = read_json(disk)
        # список — ещё не свечи: дальше каждую строку сравнивают с временем и
        # считают по ней симуляцию, поэтому проверяем и форму строки
        if isinstance(data, list) and all(
                isinstance(c, list) and len(c) == 5 and all(is_num(x) for x in c)
                for c in data):
            _raw_cache[key] = (now, data, 600)
            return data
    # окно качаем под таймфрейм: на крупных барах те же 130 дней дали бы
    # несколько сотен свечей, и график был бы обрубком (см. CHART_TFS)
    raw_days = RAW_DAYS * CHART_TFS.get(str(interval), 1)
    start = int((now - raw_days * 86400) * 1000)
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
        save_disk_cache(disk, data, symbol, interval)
    _raw_cache[key] = (now, data, 600 if full else 60)
    return data


@app.route("/api/candles/<symbol>/<mode>")
def api_candles(symbol, mode):
    if not valid_symbol(symbol) or not valid_mode(mode):
        return jsonify([]), 404
    # Таймфрейм графика можно сменить с витрины (?tf=), не трогая таймфрейм
    # САМОГО бота: на 15-минутных барах трёхлетнюю историю глазами не окинуть,
    # а на 4-часовых видно форму движения. Сделки при этом остаются теми же —
    # меняется только сетка, на которую их кладут (маркер снапится к бару).
    # Значение проверяем по белому списку: строка из URL уходит в имя файла
    # кэша и в запрос к бирже.
    tf = request.args.get("tf") or bot_interval(symbol, mode)
    if tf not in CHART_TFS:
        tf = bot_interval(symbol, mode)
    # на крупных барах окно шире: 60 дней 4-часовых баров — это всего 360 свечей
    days = CANDLE_DAYS * CHART_TFS[tf]
    raw = get_raw_candles(symbol, tf)
    start = (time.time() - days * 86400) * 1000
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
    # ГЕН ГЕЙТА ВОЛАТИЛЬНОСТИ ЕДЕТ ВМЕСТЕ С ГЕНОМОМ.
    # e7.cfg_to_genome его не переносит, и симуляция на странице бота считалась
    # так, будто гейта нет: число сделок завышалось в 2.7 раза, PnL окна в 2.6.
    # Это ЧЕТВЁРТОЕ место того же дефекта — обещание «одно определение гейта в
    # трёх местах» не выполнялось. Закреплено тестом в test_configs.py.
    if p.get("vol_gate"):
        g["vol_gate"] = float(p["vol_gate"])
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
    # Гейт применяется ТЕМ ЖЕ определением, что в оценке и в боевом боте.
    # Без этого симуляция рисовала бы конфиг БЕЗ гейта под подписью конфига
    # С гейтом — то же самое уже случалось на графике общей шкалы.
    _thr = float(g.get("vol_gate", 0.0) or 0.0)
    if _thr > 0:
        import adaptive_ltc as _al
        import all_configs_honest as _ach
        filt = _ach._gate_filter(filt, _al.regimes(candles), _thr)
    events = []
    old_lev, old_bpd = e2.LEV, e2.BARS_PER_DAY
    e2.LEV = p.get("lev", 5)
    e2.BARS_PER_DAY = bars_per_day
    try:
        # Ряд настоящих ставок фандинга — тот же, что у карточки уровня.
        # Без него симуляция на этой же странице считалась бы плоской ставкой,
        # и два блока одной страницы говорили бы разное.
        e2.run5(candles, pre, g, entry_filter=filt, events=events,
                funding=bh.funding_series(aux))
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


@app.route("/api/events/<symbol>")
def api_events(symbol):
    """Сделки бота за ВСЮ историю (предпосчёт build_analytics.py).

    Симуляция на лету (/api/sim) считает только последние 130 дней — на
    графике за два года из 57 сделок BTC было видно две, и это читалось как
    «бот не торгует». Здесь лежит полный список событий того же самого
    прогона, которым посчитаны все числа на странице.
    """
    if not valid_symbol(symbol):
        return jsonify(dict(events=[], note="нет такой монеты")), 404
    path = safe_path(FINAL_DATA_DIR, f"bot_events_{symbol}.json")
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        return jsonify(dict(events=[], note="история сделок не пересчитана"))
    evs = [e for e in data["events"]
           if isinstance(e, dict) and is_num(e.get("t"))]
    return jsonify(dict(events=evs, trades=data.get("trades"),
                        ruined=bool(data.get("ruined"))))


_finalstats_cache = {}


@app.route("/api/modestats/<symbol>/<mode>")
def api_modestats(symbol, mode):
    """Помесячная и погодовая статистика КОНКРЕТНОГО режима (build_modestats).

    Отдельный маршрут появился после ошибки: /api/finalstats/<монета> имеет
    ключом только монету, и страницы всех нефинальных режимов показывали
    цифры финального бота той же монеты как свои. Здесь ключ — монета И
    режим, поэтому подставить чужое нечем.

    Числа посчитаны тем же способом, что и уровень на карточке: плечо x5,
    копеечные выходы обнулены, половины истории порознь. Поэтому сумма
    месяцев обучения совпадает с числом «обучение», а холдоута — с «холдоут».
    """
    if not valid_symbol(symbol) or not valid_mode(mode):
        return jsonify(dict(available=False, error="нет такого бота")), 404
    path = safe_path(MODESTATS_DIR, f"{symbol}_{mode}.json")
    if path is None or not os.path.exists(path):
        # Отсутствие файла — не ошибка сервера, а факт про конфиг: он либо не
        # выражается нынешним геномом (turbo), либо не дал ни одной сделки.
        # Страница обязана сказать это словами, а не показать пустоту.
        return jsonify(dict(available=False,
                            error="для этого режима разбора нет"))
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:                              # noqa: BLE001
        return jsonify(dict(available=False, error="файл разбора повреждён"))
    d["available"] = True
    return jsonify(d)


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
    data = read_json(path)
    if not isinstance(data, dict):
        return jsonify(dict(available=False, error="файл данных повреждён"))
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
