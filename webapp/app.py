# -*- coding: utf-8 -*-
"""Локальный сайт управления ботами: список, страницы ботов, график со сделками.

Запуск:  python webapp/app.py   (из папки bybit_bot)
Открыть: http://127.0.0.1:8000
"""

import calendar
import json
import os
import re
import sys
import time

from flask import (Flask, jsonify, redirect, render_template, request,
                   send_file)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import evolution2 as e2  # noqa: E402
import evolution5 as e5  # noqa: E402
import evolution6 as e6  # noqa: E402
import evolution7 as e7  # noqa: E402
import evolution8 as e8  # noqa: E402
import ext_data as xd  # noqa: E402
import patterns as pt  # noqa: E402
import signal_engine2 as se2  # noqa: E402
from pybit.unified_trading import HTTP  # noqa: E402

try:                                   # движок v3 — только для страниц /v3
    import signal_engine3 as se3       # noqa: E402
except Exception as _e:                # noqa: BLE001
    se3 = None
    print("signal_engine3 не импортирован (страницы /v3 без имён сетапов):", _e)

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
    ("DOGEUSDT", "final"): "v7", ("LTCUSDT", "final"): "v8",
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
    # ВАЖНО: строка stats у финальных ботов — ИСТОРИЧЕСКАЯ, обучающий период
    # целиком (in-sample). На страницу и карточку она крупно не выводится:
    # подзаголовок собирается из webapp/data/bots_honest.json (honest_stats_line),
    # чтобы цифры META, карточек, страниц и JSON не могли разойтись.
    ("DOGEUSDT", "final"): dict(
        title="DOGE",
        stats="история 3.2г (2023-05-30..2026-07-23, in-sample) x10: +145.0% | "
              "DD 18.5% | 1276 сделок — обучающий период, не доказательство",
        about="Итог v7 — единого отбора, где направление, режимный гейт и "
              "фильтр классических паттернов были ГЕНАМИ одного генома. "
              "Победил тот же конфиг, что и медвежий специалист v6: обе "
              "стороны сделки (direction=0), но торгует ТОЛЬКО в bear/боковике "
              "— в подтверждённом bull стоит в стороне. Паттерны (двойные "
              "вершины, голова-плечи, треугольники…) экзамен не прошли.",
        usage="Плечо x10 выбрано правилом «макс. плечо с просадкой ≤20%» на "
              "полной истории 3.2г. Честная проверка: вердикт ЧАСТИЧНО (3/4) — "
              "на холдоуте (10.7 мес) бот дал +38.3%, но НЕОБУЧЕННАЯ сетка из "
              "шапки config.py на том же куске дала +50.3%, то есть тонкая "
              "настройка денег не добавила. Запуск: python bot_rsi.py DOGE"),
    ("LTCUSDT", "final"): dict(
        title="LTC",
        stats="история 3.2г (in-sample) x5: +115.9% | DD 17.0% | 1307 сделок | "
              "v8 — обучающий период, не доказательство",
        about="Единственный бот, улучшенный волной v8 (геном + Smart Money "
              "Concepts как гены). SMC не прошли экзамен и здесь, но сам "
              "поиск нашёл более сильный базовый конфиг: торгует во ВСЕХ "
              "режимах рынка (regime_gate=0, был 1) — почти удвоил доход "
              "при том же риске НА ОБУЧАЮЩЕМ ПЕРИОДЕ. Доля настоящих "
              "убыточных сделок 3.9% — там же.",
        usage="Плечо x5. Честная проверка НЕ ПРОЙДЕНА (2/4): на холдоуте "
              "(10.7 мес) +6.5% против +24.6% у необученной сетки, медиана "
              "90 соседних конфигов всего +2.4% при 56% прибыльных соседей — "
              "это шпиль, а не плато. Издержки съедают 65% валовой прибыли. "
              "Запуск: python bot_rsi.py LTC"),
    ("BTCUSDT", "final"): dict(
        title="BTC",
        stats="история 3.2г (in-sample) x15: +140.2% | DD 12.6% | 60 сделок — "
              "обучающий период, не доказательство",
        about="На обучающем периоде — самый низкорисковый бот портфеля: "
              "просадка растёт с 5.4% (x5) до 12.6% (x15), редкость точных "
              "входов (~19/год) почти не усиливается плечом. Обе стороны, "
              "гейт в bull, фильтр тренда SMA20/400 + Aroon. Обратная сторона "
              "этой редкости — проверить бота нечем.",
        usage="НЕ ЗАПУСКАТЬ. Честная проверка провалена (1/4): на холдоуте "
              "−1.7% и всего 14 сделок за 10.7 мес (статистически пустая "
              "выборка — тут не доказать ни плюс, ни минус), издержки съели "
              "148% валовой прибыли, медиана соседних конфигов −11.0%. "
              "Правило выбора плеча по холдоуту даёт «не запускать» на всех "
              "плечах x5..x15."),
    ("ETHUSDT", "final"): dict(
        title="ETH",
        stats="история 3.2г (in-sample) x5: +22.7% | DD 21.8% | 237 сделок — "
              "обучающий период, не доказательство",
        about="Единственный финал БЕЗ регионного гейта — торгует всегда, во "
              "всех режимах рынка. Не изменился с самой первой честной "
              "эволюции (v2): четыре последующие волны отбора не нашли, чем "
              "его улучшить — хороший знак устойчивости, а не застоя.",
        usage="Главный бот ETH. Плечо x5. Единственный бот, ПОДТВЕРЖДЁННЫЙ по "
              "всем 4 пунктам: обучение (−2.2%) хуже холдоута (+23.5%) — "
              "признак того, что подгонки под обучающий период не было. "
              "Честная оценка скромная: +7.9% за 10.7 мес (+0.74%/мес). "
              "Запуск: python bot_rsi.py ETH"),
    ("SOLUSDT", "final"): dict(
        title="SOL",
        stats="история 3.2г (in-sample) x5: +45.8% | DD 18.4% | 1245 сделок — "
              "обучающий период, не доказательство",
        about="Единственный НОВЫЙ конфиг, который v7 честно нашла впервые "
              "(обошла прежний лучший результат на всех 3 экзаменах). Торгует "
              "всегда (без гейта). Aroon перекошен (aroon_short_min=99) — "
              "формально обе стороны разрешены, но шорты на практике почти "
              "не проходят фильтр.",
        usage="Главный бот SOL. Плечо x5. ПОДТВЕРЖДЁН (4/4), но честная оценка "
              "мала: медиана соседних конфигов +2.7% за 10.7 мес "
              "(+0.26%/мес) при 61% прибыльных соседей, издержки съедают "
              "71.5% валовой прибыли. Запуск: python bot_rsi.py SOL"),
}


# --------------- ЧЕСТНЫЕ ЦИФРЫ БОТОВ (build_bot_honest_data.py) ---------------
# Единственный источник правды для всего, что показывается крупно: холдоут,
# устойчивость параметров, бенчмарки, издержки, вердикт. Строки со статистикой
# на карточках и страницах собираются ИЗ НЕГО, чтобы числа нигде не разъезжались.

HONEST_PATH = os.path.join(BOT_DIR, "webapp", "data", "bots_honest.json")
_honest_cache = {}


def load_honest():
    """webapp/data/bots_honest.json с кэшем по mtime (пересчёт —
    python build_bot_honest_data.py)."""
    mtime = os.path.getmtime(HONEST_PATH) if os.path.exists(HONEST_PATH) else None
    if _honest_cache.get("mtime") != mtime:
        _honest_cache["mtime"] = mtime
        _honest_cache["data"] = _read_json(HONEST_PATH) or {}
    return _honest_cache["data"]


def honest_bot(symbol):
    return (load_honest().get("bots") or {}).get(symbol)


@app.template_filter("trades_n")
def trades_n(n):
    """«268 сделок», «62 сделки», «21 сделка» — чтобы текст читался по-русски."""
    n = int(n or 0)
    tail = n % 100
    if 11 <= tail <= 14:
        word = "сделок"
    elif n % 10 == 1:
        word = "сделка"
    elif n % 10 in (2, 3, 4):
        word = "сделки"
    else:
        word = "сделок"
    return f"{n} {word}"


def honest_stats_line(h, hd):
    """Подзаголовок бота: сперва честная оценка, обучение — мелким шрифтом
    и с оговоркой. Собирается из JSON, вручную нигде не дублируется."""
    if not h:
        return None
    p = (hd.get("periods") or {})
    hm = (p.get("holdout") or {}).get("months", "?")
    tm = (p.get("train") or {}).get("months", "?")
    return (f"холдоут {hm} мес: {h['holdout']['ret']:+.1f}% на x{h['lev']} "
            f"({trades_n(h['n_trades']['holdout'])}) · честная оценка "
            f"{h['robust']['median']:+.1f}% = {h['robust']['per_month']:+.2f}%/мес · "
            f"вердикт: {h['verdict']['status'].lower()} {h['verdict']['score']} · "
            f"обучение {tm} мес: {h['train']['ret']:+.1f}% — не доказательство")


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
            reinvest, an, honest = "", None, None
            if mode == "final":
                a = _read_json(os.path.join(BOT_DIR, "webapp", "data",
                                            f"analytics_bot_{sym}.json")) or {}
                st = a.get("stats") or {}
                honest = honest_bot(sym)
                if st.get("final_usd") is not None:
                    sign = "+" if st.get("final_pct", 0) >= 0 else ""
                    reinvest = (f"💰 полная история с реинвестом (in-sample): "
                                f"$50 → ${st['final_usd']} "
                                f"({sign}{st['final_pct']}%)")
                    an = dict(final_usd=st.get("final_usd"),
                              final_pct=st.get("final_pct"),
                              dd=st.get("max_dd"), wr=st.get("wr"),
                              trades=st.get("trades"),
                              pf=st.get("profit_factor"),
                              months_pos=st.get("months_pos"),
                              months_total=st.get("months_total"),
                              spark=_spark(_dedupe_pairs(a.get("equity"))))
            bots.append(dict(
                symbol=sym, coin=sym.replace("USDT", ""), mode=mode,
                mode_name=MODE_NAMES.get(mode, mode), lev=p.get("lev", 5),
                interval=iv, tf_label=("4ч" if iv == "240" else f"{iv}m"),
                ver=VER.get((sym, mode), ""),
                title=meta.get("title", f"{sym.replace('USDT','')} — {mode}"),
                stats=meta.get("stats", ""), reinvest=reinvest, an=an,
                honest=honest,
                active=active, has_log=os.path.exists(log_file)))
    return bots


def _spark(pairs, n=56):
    """Спарклайн кривой капитала: (ts, value) -> точки polyline в поле 100x26."""
    vals = [v for _, v in (pairs or [])]
    if len(vals) < 2:
        return None
    step = max(1, len(vals) // n)
    sample = vals[::step]
    if sample[-1] != vals[-1]:
        sample.append(vals[-1])
    lo, hi = min(sample), max(sample)
    rng = (hi - lo) or 1.0
    m = len(sample) - 1
    pts = " ".join("%.1f,%.1f" % (i / m * 100.0, 25.0 - (v - lo) / rng * 24.0)
                   for i, v in enumerate(sample))
    return dict(pts=pts, up=sample[-1] >= sample[0])


def final_bot_list():
    return list_bots(("final",))


def archive_bot_list():
    return list_bots(ARCHIVE_MODES)


@app.route("/")
def index():
    """Главная. Крупно — честная проверка (холдоут + медиана соседей),
    обучающий период уходит в мелкий серый текст."""
    finals = final_bot_list()
    hd = load_honest()
    ans = [b["an"] for b in finals if b.get("an")]
    summary = None
    if hd.get("bots"):
        s, pf = hd["summary"], hd["portfolio"]
        summary = dict(
            n=s["n"], active=sum(1 for b in finals if b["active"]),
            confirmed=s["confirmed"], partial=s["partial"], failed=s["failed"],
            hold_ret=pf["holdout"]["ret"], hold_dd=pf["holdout"]["dd"],
            train_ret=pf["train"]["ret"], train_dd=pf["train"]["dd"],
            avg_robust=s["avg_robust"], per_month=s["avg_per_month"],
            hold_months=hd["periods"]["holdout"]["months"],
            hold_start=hd["periods"]["holdout"]["start"],
            hold_end=hd["periods"]["holdout"]["end"],
            train_months=hd["periods"]["train"]["months"],
            train_start=hd["periods"]["train"]["start"],
            train_end=hd["periods"]["train"]["end"],
            # in-sample для мелкой строки: те самые $50 -> $X за всю историю
            insample_start=round(50.0 * len(ans), 2) if ans else None,
            insample_end=round(sum(x["final_usd"] or 0.0 for x in ans), 2)
            if ans else None,
            trades=sum(x["trades"] or 0 for x in ans) if ans else None)
    return render_template("index.html", bots=finals, dry_run=config.DRY_RUN,
                           has_final=bool(finals), summary=summary, honest=hd)


FAVICON = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<rect width='32' height='32' rx='7' fill='#131722'/>"
    "<rect x='7' y='10' width='5' height='13' rx='1' fill='#26a69a'/>"
    "<rect x='9' y='6' width='1' height='21' fill='#26a69a'/>"
    "<rect x='19' y='7' width='5' height='12' rx='1' fill='#ef5350'/>"
    "<rect x='21' y='4' width='1' height='22' fill='#ef5350'/></svg>")


@app.route("/favicon.ico")
def favicon():
    """Иконка вкладки: свечи в палитре TradingView (без внешних файлов)."""
    return app.response_class(FAVICON, mimetype="image/svg+xml")


@app.route("/archive")
def archive():
    return render_template("archive.html", bots=archive_bot_list(),
                           dry_run=config.DRY_RUN)


# --- сигнальные сетапы v2: действующие 4 (владелец убрал range_long,
# range_short, sweep_short, pump_short — на сайте их больше нет) ---
ACTIVE_SETUPS = list(se2.SETUPS)
TF_LABELS = {240: "4ч", 60: "1ч"}

RU_SETUPS = {
    "sweep_long": "Ложный пробой низа (сбор ликвидности): лонг",
    "dump_long": "Капитуляция: лонг после сильного падения",
    "bounce_short": "Нож -> откат: шорт отскока после обвала",
    "rally_short": "Тренд-шорт: продажа отскока по тренду",
}

# мини-описание человеческим языком: когда входит и где стоп
SETUP_DESCR = {
    "sweep_long": "Цена прокалывает старый минимум (собирает стопы) и тем же "
                  "баром возвращается выше уровня. Вход — лонг после "
                  "возврата; стоп — за фитилём прокола, тейк всегда 3 стопа.",
    "dump_long": "Резкое падение за несколько дней заканчивается разворотной "
                 "свечой при перепроданном RSI — капитуляция продавцов. "
                 "Вход — лонг на развороте; стоп — за лоу ножа.",
    "bounce_short": "После обвала цена отскакивает вверх (dead-cat bounce), "
                    "но остаётся под сломанным уровнем и не выкупает большую "
                    "часть падения. Вход — шорт отката; стоп — над зоной "
                    "отскока. В бычьем режиме пороги строже, в медвежьем "
                    "мягче.",
    "rally_short": "Быстрый рост выдыхается: перегретый RSI и первая красная "
                   "свеча. В медвежьем режиме это шорт отскока ПО тренду "
                   "(пороги мягче), в бычьем — контртренд только при "
                   "экстремальном перегреве (строже). Стоп — над вершиной "
                   "роста.",
}


def tf_label(iv):
    return TF_LABELS.get(iv, f"{iv}м")


# --- какие ворота сетап РЕАЛЬНО применяет (разбор se2.gate_eval) -----------
# Порядок и состав списка повторяют ветки gate_eval: сначала режим, затем
# шторм (если storm_gate=1), затем профильные ворота сетапа, затем общие
# funding/aroon и в конце ширина стопа. Всё, чего в списке нет, на вход
# НЕ влияет — сайт такие вещи рисует только «справочно».
REG_RU3 = ("бычий", "боковик", "медвежий")
STORM_MODE_RU = {
    0: "любой вход в шторм запрещён",
    1: "запрещён только вход ПРОТИВ шторма",
    2: "торгуем ТОЛЬКО в шторм",
}


def _gv(g, key):
    """Значение гена с фолбэком на DEFAULTS2 (старые геномы без новых генов)."""
    v = g.get(key) if isinstance(g, dict) else None
    return se2.DEFAULTS2.get(key) if v is None else v


def setup_gates(base, g):
    """[(ворото, что проверяет, детали с числами генома)] в порядке движка."""
    g = g or {}
    pct = lambda v: f"{float(v) * 100:.2f}%"          # noqa: E731
    period = se2.RSI_SET[int(_gv(g, "rsi_idx"))]
    regs = [REG_RU3[i] for i, k in enumerate(("reg_bull", "reg_range",
                                              "reg_bear")) if int(_gv(g, k))]
    out = [dict(
        name=se2.GATE_REGIME, what="торгуем только в разрешённых режимах рынка",
        detail=("разрешено: " + ", ".join(regs) if regs else "все режимы запрещены")
               + " · режим бара считается по ВЧЕРАШНЕМУ дню (SMA100 дней "
                 "и ход за 30 дней)",
        val=", ".join(regs) or "—")]
    if int(_gv(g, "storm_gate")):
        mode = int(_gv(g, "storm_mode"))
        out.append(dict(
            name=se2.GATE_STORM, what=STORM_MODE_RU.get(mode, "шторм"),
            detail=f"шторм = ход за сутки > {pct(_gv(g, 'storm_day'))} или за "
                   f"неделю > {pct(_gv(g, 'storm_week'))} или ранг дневного "
                   f"ATR > {float(_gv(g, 'storm_atr_rank')):.2f}",
            val=f"режим {mode}"))
    if base == "sweep_long":
        out += [
            dict(name=se2.GATE_POKE,
                 what="лоу бара прокалывает минимум окна",
                 detail=f"окно {int(_gv(g, 'window'))} баров ТФ, уровень взят с "
                        f"лагом {int(_gv(g, 'age'))} баров; глубина прокола "
                        f"≥ {float(_gv(g, 'poke_atr')):.3f} дневных ATR",
                 val=f"≥ {float(_gv(g, 'poke_atr')):.3f} ATR"),
            dict(name=se2.GATE_RECLAIM,
                 what="закрытие вернулось ВЫШЕ пробитого минимума",
                 detail="прокол был выкуплен тем же баром — это и есть сбор "
                        "ликвидности", val="close > уровня"),
        ]
    elif base == "dump_long":
        out += [
            dict(name=se2.GATE_MOVE, what="падение за несколько суток",
                 detail=f"закрытие ниже закрытия {int(_gv(g, 'drop_days'))} "
                        f"суток назад минимум на {pct(_gv(g, 'drop_frac'))}",
                 val=f"≥ {pct(_gv(g, 'drop_frac'))}"),
            dict(name=se2.GATE_CANDLE, what="разворотная (зелёная) свеча",
                 detail="close > open сигнального бара", val="close > open"),
            dict(name=se2.GATE_RSI, what="перепроданность",
                 detail=f"RSI({period}) ≤ порога {float(_gv(g, 'rsi_os')):.0f}; "
                        f"в bull (по тренду) мягче на "
                        f"{float(_gv(g, 'with_rsi_shift')):.0f}, в bear "
                        f"(против тренда) строже на "
                        f"{float(_gv(g, 'counter_rsi_shift')):.0f}",
                 val=f"RSI({period}) ≤ {float(_gv(g, 'rsi_os')):.0f}"),
        ]
    elif base == "bounce_short":
        out += [
            dict(name=se2.GATE_MOVE, what="глубина ножа",
                 detail=f"от закрытия {int(_gv(g, 'drop_days'))} суток назад до "
                        f"дна ножа ≥ {pct(_gv(g, 'drop_frac'))}",
                 val=f"≥ {pct(_gv(g, 'drop_frac'))}"),
            dict(name=se2.GATE_BOUNCE, what="цена уже отскочила от дна",
                 detail=f"отскок от лоу ножа ≥ "
                        f"{float(_gv(g, 'bounce_min_atr')):.2f} дневных ATR",
                 val=f"≥ {float(_gv(g, 'bounce_min_atr')):.2f} ATR"),
            dict(name=se2.GATE_ZONE, what="откат ещё не выкупил падение",
                 detail=f"восстановлено не больше "
                        f"{pct(_gv(g, 'bounce_max_frac'))} падения (в bull — "
                        f"строже, ×{float(_gv(g, 'counter_zone_mult')):.2f}). "
                        f"ЭТО НЕ позиция в диапазоне — это доля восстановления",
                 val=f"≤ {pct(_gv(g, 'bounce_max_frac'))} падения"),
            dict(name=se2.GATE_RECLAIM,
                 what="цена НЕ вернулась над сломанный уровень",
                 detail="close ≤ уровень до ножа + 0.25 дневных ATR; если нож "
                        "уровень не пробивал — ворото валится",
                 val="close ≤ уровня"),
            dict(name=se2.GATE_RSI, what="перепроданность СНЯТА",
                 detail=f"RSI({period}) поднялся ВЫШЕ "
                        f"{float(_gv(g, 'rsi_os')):.0f}; в bull (против тренда) "
                        f"порог выше на {float(_gv(g, 'counter_rsi_shift')):.0f}, "
                        f"в bear (по тренду) ниже на "
                        f"{float(_gv(g, 'with_rsi_shift')):.0f}",
                 val=f"RSI({period}) ≥ {float(_gv(g, 'rsi_os')):.0f}"),
        ]
    elif base == "rally_short":
        out += [
            dict(name=se2.GATE_MOVE, what="рост за несколько суток",
                 detail=f"закрытие выше закрытия {int(_gv(g, 'drop_days'))} "
                        f"суток назад минимум на {pct(_gv(g, 'drop_frac'))}",
                 val=f"≥ {pct(_gv(g, 'drop_frac'))}"),
            dict(name=se2.GATE_CANDLE, what="первая красная свеча",
                 detail="close < open сигнального бара", val="close < open"),
            dict(name=se2.GATE_RSI, what="перегрев",
                 detail=f"RSI({period}) ≥ {100 - float(_gv(g, 'rsi_os')):.0f} "
                        f"(зеркало порога {float(_gv(g, 'rsi_os')):.0f}); в bear "
                        f"(по тренду) мягче на "
                        f"{float(_gv(g, 'with_rsi_shift')):.0f}, в bull "
                        f"(против тренда) строже на "
                        f"{float(_gv(g, 'counter_rsi_shift')):.0f}",
                 val=f"RSI({period}) ≥ {100 - float(_gv(g, 'rsi_os')):.0f}"),
        ]
        if int(_gv(g, "ma_gate")):
            out.append(dict(
                name=se2.GATE_MA, what="шорт только под скользящей средней",
                detail=f"close < SMA({int(_gv(g, 'ma_len'))} баров ТФ)",
                val=f"SMA {int(_gv(g, 'ma_len'))}"))
    if int(_gv(g, "fund_gate")):
        thr = float(_gv(g, "fund_thr"))
        side_txt = ("funding ≤ −%.4f%%/8ч (шортисты платят)" % thr
                    if base.endswith("long")
                    else "funding ≥ %.4f%%/8ч (лонгисты платят)" % thr)
        out.append(dict(name=se2.GATE_FUND, what="ставка финансирования",
                        detail=side_txt, val=f"{thr:.4f}%"))
    if int(_gv(g, "aroon_gate")):
        out.append(dict(
            name=se2.GATE_AROON, what="свежесть экстремума (Aroon)",
            detail=f"Aroon({int(_gv(g, 'aroon_n'))}) "
                   f"{'Up' if base.endswith('long') else 'Down'} ≥ "
                   f"{float(_gv(g, 'aroon_thr')):.0f}",
            val=f"≥ {float(_gv(g, 'aroon_thr')):.0f}"))
    out.append(dict(
        name=se2.GATE_STOP, what="ширина стопа влезает в лимит риска",
        detail=f"{se2.MIN_STOP * 100:.1f}% ≤ расстояние до стопа ≤ "
               f"{pct(_gv(g, 'stop_cap'))} · стоп ставится способом "
               f"«{se2.STOP_MODE_NAMES.get(int(_gv(g, 'stop_mode')), '?')}»",
        val=f"≤ {pct(_gv(g, 'stop_cap'))}"))
    for i, q in enumerate(out, 1):
        q["n"] = i
    return out


def setup_refs(base, g):
    """Что рисуется на графике «справочно» и НА ВХОД НЕ ВЛИЯЕТ."""
    g = g or {}
    period = se2.RSI_SET[int(_gv(g, "rsi_idx"))]
    refs = []
    if base == "sweep_long":
        refs.append(dict(name=f"RSI({period})",
                         detail="у sweep_long RSI воротом НЕ является — вход "
                                "определяют прокол и возврат за уровень"))
    refs.append(dict(
        name="зона входа (полоса у границы диапазона)",
        detail=("для bounce_short ворото «зона» сравнивает ДОЛЮ "
                "ВОССТАНОВЛЕНИЯ падения, а не положение цены в диапазоне — "
                "нарисованная полоса это лишь контекст уровня")
        if base == "bounce_short" else
        "положение цены в диапазоне у этого сетапа воротом не является"))
    if base in ("dump_long", "rally_short"):
        refs.append(dict(name="границы диапазона (окно уровней)",
                         detail="сетап от них не отталкивается — показаны для "
                                "контекста"))
    return refs


CHART_LAYERS = {          # какие слои графика реально относятся к воротам
    "sweep_long": dict(rsi=False, zone=False, level=True),
    "dump_long": dict(rsi=True, zone=False, level=False),
    "bounce_short": dict(rsi=True, zone=False, level=True),
    "rally_short": dict(rsi=True, zone=False, level=False),
}


# --- состояние рынка «сейчас» (движок v2.2) --------------------------------
MARKET_TF = 240
_market_cache = {}        # tf -> (посчитано_в, данные)


def _market_candles(iv=MARKET_TF):
    """Свечи BTC своего ТФ: полная история с диска (режиму нужны 100 дней,
    рангу волатильности — 90) + свежий хвост с биржи. Незакрытый бар
    отбрасывается: на баре i только данные <= i, никакого заглядывания."""
    import evolution as ev
    base = ev.fetch("BTCUSDT", str(iv), 1150)
    live_err = None
    try:
        live = get_raw_candles("BTCUSDT", str(iv))
    except Exception as e:      # нет сети — работаем на кэше истории
        live, live_err = [], str(e)
    out = list(base)
    if live:
        last = out[-1][0] if out else 0
        out += [c for c in live if c[0] > last]
    edge = int(time.time() * 1000) - iv * 60_000
    out = [c for c in out if c[0] <= edge]
    return out, live_err


@app.route("/api/market_now")
def api_market_now():
    """Состояние рынка BTC на ПОСЛЕДНЕМ ЗАКРЫТОМ баре 4ч: режим, ход за
    сутки и неделю, ранг волатильности, признак шторма (se2.market_context,
    пороги — необученное семя DEFAULTS2). Кэш 60 секунд."""
    iv = MARKET_TF
    cached = _market_cache.get(iv)
    if cached and time.time() - cached[0] < 60:
        return jsonify(cached[1])
    try:
        c4, live_err = _market_candles(iv)
        if not c4:
            raise RuntimeError("нет свечей BTC")
        ctx = se2.prep_context(c4, interval_min=iv)
        mc = se2.market_context(c4, ctx, len(c4) - 1)
    except Exception as e:
        print("market_now не посчитан:", e)
        return jsonify(dict(available=False, error=str(e)))
    age_min = int((time.time() * 1000 - (mc.get("bar_close_ts") or 0)) / 60000)
    data = dict(
        mc, available=True, interval_min=iv, tf_label=tf_label(iv),
        regime_ru=REG_RU3[int(mc.get("regime", 1))],
        bars=len(c4), age_min=max(age_min, 0), stale=age_min > 2 * iv,
        live_error=live_err,
        thr=dict(day=se2.DEFAULTS2["storm_day"],
                 week=se2.DEFAULTS2["storm_week"],
                 atr_rank=se2.DEFAULTS2["storm_atr_rank"]),
        note="пороги шторма — необученное семя DEFAULTS2; ни один торговый "
             "сетап пока не включает штормовое ворото",
    )
    _market_cache[iv] = (time.time(), data)
    return jsonify(data)


def split_sig_key(key, rec=None):
    """Ключ конфига -> (сетап, ТФ мин). "sweep_long@60" -> ("sweep_long", 60);
    ключ без @ — ТФ из поля interval_min, иначе 240 (старый формат)."""
    if "@" in key:
        base, _, tf = key.rpartition("@")
        if tf.isdigit():
            return base, int(tf)
    iv = 240
    if isinstance(rec, dict):
        try:
            iv = int(rec.get("interval_min") or 240)
        except (TypeError, ValueError):
            iv = 240
    return key, iv


def _load_state():
    """signals.json помощника; активные сигналы приводим к полным ключам."""
    state = dict(active={}, history=[], capital={})
    p2 = os.path.join(FINAL_DATA_DIR, "signals.json")
    if os.path.exists(p2):
        try:
            with open(p2, encoding="utf-8") as fh:
                state = json.load(fh)
        except Exception as e:
            print("signals.json не прочитан:", e)
    act = {}
    for k, v in (state.get("active") or {}).items():
        base, iv = split_sig_key(k, v)
        act[f"{base}@{iv}"] = v
    state["active"] = act
    return state


def variant_status(rec, act):
    """(слово, css-класс) статуса варианта сетапа."""
    if act:
        return "СИГНАЛ", "sig"
    if rec.get("enabled"):
        return "ожидание", "wait"
    if rec.get("watch"):
        return "наблюдение", "watch"
    return "не подтверждён", "off"


@app.route("/signals")
def signals_page():
    setups = load_signal_setups()          # {полный ключ -> rec}
    state = _load_state()
    active = state.get("active", {})
    capitals = state.get("capital", {}) or {}
    hist = sorted(state.get("history", []),
                  key=lambda h: h.get("exit_ts", 0), reverse=True)[:40]
    for h in hist:
        b, iv = split_sig_key(str(h.get("setup", "")), h)
        h["_title"] = RU_SETUPS.get(b, b)
        h["_tf"] = tf_label(iv)

    # группировка по сетапу; внутри — варианты по ТФ (4ч, потом 1ч)
    prio = {"sig": 0, "wait": 1, "watch": 2, "off": 3}
    groups = []
    for base in ACTIVE_SETUPS:
        variants = []
        for key, rec in setups.items():
            if rec["_base"] != base:
                continue
            act = active.get(key)
            word, cls = variant_status(rec, act)
            variants.append(dict(
                key=key, iv=rec["_iv"], tf=tf_label(rec["_iv"]), rec=rec,
                act=act, status=word, status_cls=cls,
                capital=capitals.get(key, capitals.get(base)),
                holdout=rec.get("holdout") or {},
                bench=rec.get("benchmark") or {},
                stats=rec.get("stats") or {}))
        variants.sort(key=lambda v: -v["iv"])
        best = min((v["status_cls"] for v in variants),
                   key=lambda c: prio.get(c, 9), default=None)
        word = next((v["status"] for v in variants if v["status_cls"] == best),
                    "ждёт отбора")
        groups.append(dict(
            base=base, title=RU_SETUPS.get(base, base),
            descr=SETUP_DESCR.get(base, ""),
            long=base.endswith("long"),
            variants=variants,
            status=word, status_cls=best or "none",
            has_signal=any(v["act"] for v in variants)))

    # плоский список карточек-строк для сегментированного фильтра
    cards = []
    for grp in groups:
        for v in grp["variants"]:
            cards.append(dict(v, base=grp["base"], title=grp["title"],
                              descr=grp["descr"], long=grp["long"]))
    cards.sort(key=lambda c: (prio.get(c["status_cls"], 9), -c["iv"],
                              c["base"]))

    # "работает" = свежий heartbeat (лог пишется только на события);
    # старый лог оставлен как фолбэк для запуска дореформенного помощника
    hb = os.path.join(FINAL_DATA_DIR, "advisor_heartbeat.txt")
    adv_log = os.path.join(BOT_DIR, "advisor.log")
    running = any(os.path.exists(p) and time.time() - os.path.getmtime(p) < 180
                  for p in (hb, adv_log))
    n_watch = sum(1 for r in setups.values()
                  if r.get("watch") and not r.get("enabled"))
    n_on = sum(1 for r in setups.values() if r.get("enabled"))
    n_off = sum(1 for r in setups.values()
                if not r.get("enabled") and not r.get("watch"))
    n_storm = sum(1 for r in setups.values()
                  if int(_gv(r.get("genome") or {}, "storm_gate")))
    storm_txt = (f"Ворото «шторм» включено у {n_storm} из {len(setups)} "
                 f"вариантов — их входы в шторм ограничены."
                 if n_storm else
                 "Штормовое ворото в движке ЕСТЬ, но ни один вариант сетапа "
                 "его пока не включает (storm_gate=0): входы НЕ блокируются, "
                 "плашка — контекст, а не запрет.")
    return render_template("signals.html", groups=groups, cards=cards,
                           history=hist, storm_gate=bool(n_storm),
                           storm_gate_txt=storm_txt,
                           running=running, n_watch=n_watch, n_on=n_on,
                           n_off=n_off,
                           n_active=len(active), n_setups=len(setups),
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


def raw_signal_setups():
    """Сырые конфиги как в файле (актуальные — signal_setups2.json)."""
    for fname in ("signal_setups2.json", "signal_setups.json"):
        p = os.path.join(BOT_DIR, fname)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
    return {}


def raw_signal_setups_v1():
    """Конфиги СТАРОГО движка (signal_setups.json): ключи без @, геном v1.
    Отдельная функция нужна, потому что страница /signal_v1 и её API считают
    сетап движком signal_engine (v1) — геном v2 туда подсовывать нельзя."""
    p = os.path.join(BOT_DIR, "signal_setups.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def load_signal_setups():
    """Конфиги действующих сетапов, нормализованные к полным ключам
    "<сетап>@<ТФ>" (старый ключ без @ = @240). Legacy-сетапы отброшены.
    В каждом rec служебные поля: _base, _iv, interval_min."""
    raw = raw_signal_setups()
    out = {}
    for key, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        base, iv = split_sig_key(key, rec)
        if base not in ACTIVE_SETUPS:
            continue
        out[f"{base}@{iv}"] = dict(rec, _base=base, _iv=iv, interval_min=iv)
    # стабильный порядок: по списку сетапов, внутри — 4ч затем 1ч
    order = [f"{s}@{iv}" for s in ACTIVE_SETUPS for iv in (240, 60)]
    return {k: out[k] for k in order if k in out} | \
           {k: v for k, v in out.items() if k not in order}


@app.route("/signal/<name>")
def signal_page(name):
    """Страница сетапа v2. Имя — полный ключ "<сетап>@<ТФ>"; старые адреса
    без @ редиректятся на @240 (или на @60, если 4ч-варианта нет)."""
    setups = load_signal_setups()
    if name not in setups:
        if "@" not in name:
            for cand in (f"{name}@240", f"{name}@60"):
                if cand in setups:
                    return redirect(f"/signal/{cand}")
        return "Нет такого сетапа", 404
    rec = setups[name]
    base, iv = rec["_base"], rec["_iv"]
    v1_ok = (iv == 240 and base in ("sweep_long", "dump_long") and
             os.path.exists(os.path.join(BOT_DIR, "signal_setups.json")))
    act = load_active_signal(name)
    word, cls = variant_status(rec, act)
    g = rec.get("genome") or {}
    # пилюли переключения ТФ: только реально существующие варианты сетапа
    tfs = [dict(iv=v, key=f"{base}@{v}", label=tf_label(v), cur=(v == iv))
           for v in (240, 60) if f"{base}@{v}" in setups]
    return render_template("signal2.html", name=name, base=base,
                           interval_min=iv, tf_label=tf_label(iv),
                           title=RU_SETUPS.get(base, base),
                           descr=SETUP_DESCR.get(base, ""),
                           rec=rec, st=rec.get("stats", {}), v1_ok=v1_ok,
                           holdout=rec.get("holdout") or {},
                           bench=rec.get("benchmark") or {},
                           inner=rec.get("inner_oos") or {},
                           ladder=rec.get("ladder") or [],
                           status=word, status_cls=cls, act=act, tfs=tfs,
                           storm_gate=bool(int(_gv(g, "storm_gate"))),
                           storm_gate_txt=(
                               "У этого варианта ворото «шторм» ВКЛЮЧЕНО: "
                               + STORM_MODE_RU.get(int(_gv(g, "storm_mode")), "")
                               if int(_gv(g, "storm_gate")) else
                               "У этого варианта ворото «шторм» выключено "
                               "(storm_gate=0) — входы не блокируются, плашка "
                               "показана как контекст рынка."),
                           gates=setup_gates(base, g), refs=setup_refs(base, g),
                           layers=CHART_LAYERS.get(base, dict(
                               rsi=True, zone=True, level=True)),
                           fmt_ts=lambda ms: time.strftime(
                               "%d.%m %H:%M", time.localtime(ms / 1000)))


@app.route("/signal_v1/<name>")
def signal_page_v1(name):
    """Старая страница сетапа (движок v1) — оставлена для сравнения.
    Понимает и полный ключ с @ (берёт базовое имя)."""
    base, _ = split_sig_key(name)
    rec = raw_signal_setups_v1().get(base)
    if not rec:
        return "Нет такого сетапа", 404
    return render_template("signal.html", name=base,
                           title=RU_SETUPS.get(base, base), rec=rec,
                           st=rec.get("stats", {}))


# --- аналитика сетапов v2 (предпосчёт build_signal_analytics2.py) ---

_signal2_cache = {}        # name -> (mtime, data)
_sig_candles_cache = {}    # "btc4h" -> [{time,open,high,low,close}...]


def load_active_signal(name):
    """Живой активный сигнал сетапа из webapp/data/signals.json (или None).
    Понимает и полный ключ "<сетап>@<ТФ>", и старый ключ без @."""
    act = _load_state().get("active", {})
    if name in act:
        return act[name]
    base, iv = split_sig_key(name)
    return act.get(f"{base}@{iv}")


def _signal2_paths(name):
    """Кандидаты файлов предпосчёта для имени (с @ и без — фолбэк)."""
    cands = [name]
    if "@" not in name:
        cands = [f"{name}@240", name]           # старый адрес -> новый файл
    elif name.endswith("@240"):
        cands = [name, name[: -len("@240")]]    # новый адрес -> старый файл
    return [os.path.join(FINAL_DATA_DIR, f"signal2_{c}.json") for c in cands]


@app.route("/api/signal2/<name>")
def api_signal2(name):
    """Аналитика сетапа v2 из webapp/data/signal2_<имя>.json (кэш по mtime).
    Имя — полный ключ "<сетап>@<ТФ>"; без @ означает @240 (фолбэк на старые
    файлы без @ тоже работает). Если предпосчёта нет — available=false."""
    path = mtime = None
    for p in _signal2_paths(name):
        if os.path.exists(p):
            path, mtime = p, os.path.getmtime(p)
            break
    base, iv = split_sig_key(name)
    if mtime is None:
        return jsonify(dict(available=False, name=name, setup=base,
                            interval_min=iv, tf_label=tf_label(iv),
                            hint="данные не посчитаны, запусти "
                                 "build_signal_analytics2.py",
                            active=load_active_signal(name)))
    cached = _signal2_cache.get(path)
    if not (cached and cached[0] == mtime):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            print(f"{os.path.basename(path)} не прочитан:", e)
            return jsonify(dict(available=False, name=name, setup=base,
                                interval_min=iv, tf_label=tf_label(iv),
                                hint=f"файл предпосчёта повреждён: {e}",
                                active=load_active_signal(name)))
        if not isinstance(data, dict):
            return jsonify(dict(available=False, name=name, setup=base,
                                interval_min=iv, tf_label=tf_label(iv),
                                hint="файл предпосчёта в неизвестном формате",
                                active=load_active_signal(name)))
        data["available"] = True
        # старые файлы (до мульти-ТФ) не содержат этих полей
        data.setdefault("setup", base)
        data.setdefault("interval_min", iv)
        data.setdefault("tf_label", tf_label(iv))
        cached = (mtime, data)
        _signal2_cache[path] = cached
    out = dict(cached[1])
    out["active"] = load_active_signal(name)
    out["built_at"] = int(mtime)
    return jsonify(out)


@app.route("/api/signal_candles")
def api_signal_candles():
    """Свечи за всю историю (1150 дней) для графиков сетапов.

    ?interval=15|60|240|D — таймфрейм отображения (сигналы считаются на
    своём ТФ, но смотреть их можно на любом, как в терминале).
    ?symbol=BTCUSDT — инструмент. Кэш в памяти по паре (символ, ТФ)."""
    iv = str(request.args.get("interval", "240"))
    sym = str(request.args.get("symbol", "BTCUSDT")).upper()
    if iv not in ("15", "60", "240", "D"):
        return jsonify([]), 400
    if not sym.endswith("USDT") or len(sym) > 12:
        return jsonify([]), 400
    key = f"{sym}@{iv}"
    cached = _sig_candles_cache.get(key)
    if cached is not None:
        return jsonify(cached)
    try:
        import evolution as ev
        cc = ev.fetch(sym, iv, 1150)
    except Exception as e:
        print(f"свечи {sym} {iv} не загружены:", e)
        return jsonify([])
    data = [dict(time=c[0] // 1000, open=c[1], high=c[2], low=c[3],
                 close=c[4]) for c in cc]
    _sig_candles_cache[key] = data
    return jsonify(data)


@app.route("/api/signal_chart/<name>")
def api_signal_chart(name):
    """4ч свечи BTC (полные 3.2г) + все сделки бэктеста сетапа + активный
    сигнал, если есть (старая страница v1; имя с @ приводится к базовому)."""
    name, _ = split_sig_key(name)
    cached = _sig_chart_cache.get(name)
    if cached and time.time() - cached[0] < 1800:
        return jsonify(cached[1])
    rec = raw_signal_setups_v1().get(name)
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
    data = dict(candles=candles, trades=trades,
                active=load_active_signal(name))
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


@app.route("/api/bots_honest")
def api_bots_honest():
    """Честные метрики 5 финальных ботов (build_bot_honest_data.py) —
    для плиток и подписей на /pnl."""
    d = load_honest()
    if not d.get("bots"):
        return jsonify(dict(available=False))
    out = dict(d)
    out["available"] = True
    return jsonify(out)


@app.route("/api/pnl_curves")
def api_pnl_curves():
    p = os.path.join(FINAL_DATA_DIR, "pnl_curves.json")
    if not os.path.exists(p):
        return jsonify(dict(series=[]))
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)

    # Граница обучающего периода и честные итоги ботов — чтобы на графике
    # было видно, ГДЕ кривая нарисована подгонкой, а где начинается экзамен.
    # Без этого крупные проценты справа читаются как реальный результат.
    hp = os.path.join(FINAL_DATA_DIR, "bots_honest.json")
    if os.path.exists(hp):
        try:
            with open(hp, encoding="utf-8") as fh:
                hon = json.load(fh)
            per = (hon.get("periods") or {}).get("holdout") or {}
            start = per.get("start")
            if start:
                data["holdout_from"] = int(time.mktime(
                    time.strptime(start, "%Y-%m-%d")))
                data["holdout_note"] = (
                    f"слева — обучающий период (параметры подбирались здесь), "
                    f"справа — экзамен на невиданных данных "
                    f"({start}..{per.get('end', '')})")
            honest = {}
            for sym, b in (hon.get("bots") or {}).items():
                ho = b.get("holdout") or {}
                rb = b.get("robust") or {}
                honest[f"bot_{sym}"] = dict(
                    holdout_pct=ho.get("ret"),
                    honest_pct=rb.get("median"),
                    per_month=rb.get("per_month"),
                    verdict=(b.get("verdict") or {}).get("status"))
            if honest:
                data["honest"] = honest
        except (OSError, ValueError, KeyError, TypeError) as e:
            print("bots_honest.json не подмешан:", e)
    return jsonify(data)


# ------------------- ПРИБЫЛЬ ПО МЕСЯЦАМ (единый источник) -------------------
# Ключи: bot_<SYM> (готовый предпосчёт analytics_bot_*.json),
#        sig_<сетап>@<ТФ> (stats.by_month из signal2_*.json),
#        любая серия pnl_curves (pf_cons, pf_reb, bench_spx...) — считаем
#        помесячную доходность из уже готовой кривой капитала.
# Точки с одинаковым временем схлопываются: дубликат ts роняет график.

def _norm_ts(v):
    """И миллисекунды, и секунды -> секунды."""
    try:
        t = int(v)
    except (TypeError, ValueError):
        return None
    if t <= 0:
        return None
    return t // 1000 if t > 100000000000 else t


def _month_key(ts):
    t = time.gmtime(ts)
    return (t.tm_year, t.tm_mon)


def _month_start(ym):
    return calendar.timegm((ym[0], ym[1], 1, 0, 0, 0, 0, 0, 0))


def _dedupe_pairs(pairs):
    """[[ts, значение], ...] -> отсортированный ряд без дубликатов времени."""
    out = {}
    for p in pairs or []:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        ts = _norm_ts(p[0])
        if ts is None:
            continue
        try:
            out[ts] = float(p[1])
        except (TypeError, ValueError):
            continue
    return [(t, out[t]) for t in sorted(out)]


def _read_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        print(f"{os.path.basename(path)} не прочитан:", e)
        return None


def _months_from_pairs(pairs):
    """Готовый помесячный ряд [[ts, %], ...] -> список месяцев.

    Источники считают месяц 30.44-дневной корзиной, поэтому две корзины
    иногда попадают в один календарный месяц. Раньше вторая ЗАТИРАЛА первую,
    и до 20% доходности молча пропадало с графика — теперь складываем.
    """
    out = {}
    for ts, v in _dedupe_pairs(pairs):
        ym = _month_key(ts)
        if ym in out:
            out[ym]["pct"] = round(out[ym]["pct"] + v, 2)
            out[ym]["merged"] = out[ym].get("merged", 1) + 1
        else:
            out[ym] = dict(ts=_month_start(ym),
                           label="%04d-%02d" % ym, pct=round(v, 2))
    return [out[k] for k in sorted(out)]


def _months_from_curve(points):
    """Кривая капитала -> доходность каждого месяца (конец месяца к концу
    предыдущего; для первого месяца — к его первой точке)."""
    pts = _dedupe_pairs(points)
    if not pts:
        return []
    order, last, first = [], {}, {}
    for ts, v in pts:
        ym = _month_key(ts)
        if ym not in last:
            order.append(ym)
            first[ym] = v
        last[ym] = v
    months, prev = [], None
    for ym in order:
        base = prev if prev not in (None, 0) else first[ym]
        end = last[ym]
        pct = ((end / base - 1) * 100.0) if base else 0.0
        months.append(dict(ts=_month_start(ym), label="%04d-%02d" % ym,
                           pct=round(pct, 2), usd=round(end - base, 2),
                           equity=round(end, 2)))
        prev = end
    return months


def _month_summary(months):
    if not months:
        return dict(pos=0, neg=0, flat=0, total=0, avg=0.0)
    vals = [m.get("pct") or 0.0 for m in months]
    pos = sum(1 for v in vals if v > 0)
    neg = sum(1 for v in vals if v < 0)
    best = max(months, key=lambda m: m.get("pct") or 0.0)
    worst = min(months, key=lambda m: m.get("pct") or 0.0)
    traded = [m for m in months if m.get("n") is None or m.get("n")]
    return dict(
        pos=pos, neg=neg, flat=len(months) - pos - neg, total=len(months),
        traded=len(traded), avg=round(sum(vals) / len(vals), 2),
        best=dict(label=best["label"], pct=best.get("pct") or 0.0),
        worst=dict(label=worst["label"], pct=worst.get("pct") or 0.0),
        sum_usd=round(sum(m.get("usd") or 0.0 for m in months), 2))


def _merge_month(out, ym, pct, usd=0.0, n=0, wins=0, label=None):
    """Складываем показатели в календарный месяц.

    Источники нарезают месяцы 30.44-дневными корзинами, и две корзины могут
    попасть в один календарный месяц. Перезапись (как было раньше) молча
    съедала до 20% доходности графика — поэтому только сложение.
    """
    if ym in out:
        o = out[ym]
        o["pct"] = round(o["pct"] + pct, 2)
        o["usd"] = round(o.get("usd", 0.0) + usd, 2)
        o["n"] = o.get("n", 0) + n
        o["wins"] = o.get("wins", 0) + wins
        o["merged"] = o.get("merged", 1) + 1
    else:
        out[ym] = dict(ts=_month_start(ym),
                       label=label or "%04d-%02d" % ym,
                       pct=round(pct, 2), usd=round(usd, 2), n=n, wins=wins)


def _months_from_by_month(bm):
    """stats.by_month (сетапы) -> месяцы для графика; корзины одного
    календарного месяца суммируются."""
    out = {}
    for m in bm or []:
        ts = _norm_ts(m.get("ts"))
        if ts is None:
            continue
        _merge_month(out, _month_key(ts),
                     float(m.get("pct") or 0.0),
                     float(m.get("pnl_usd") or 0.0),
                     int(m.get("n") or 0), int(m.get("wins") or 0),
                     m.get("label"))
    return [out[k] for k in sorted(out)]


def _months_for_key(key):
    """key -> (месяцы, источник)."""
    if key.startswith("v3_"):      # сетап нового отбора: v3_<сетап>@<монета>@<ТФ>
        d = _read_json(os.path.join(FINAL_DATA_DIR, f"{key}.json"))
        if d:
            months = _months_from_by_month((d.get("stats") or {}).get("by_month"))
            if months:
                return months, "setup_v3"
        return [], ""
    if key.startswith("an_"):      # любой файл предпосчёта analytics_*.json
        d = _read_json(os.path.join(FINAL_DATA_DIR, f"analytics_{key[3:]}.json"))
        if d:
            return _months_from_pairs(d.get("monthly")), "analytics"
        return [], ""
    if key.startswith("bot_"):
        d = _read_json(os.path.join(FINAL_DATA_DIR, f"analytics_{key}.json"))
        if d:
            return _months_from_pairs(d.get("monthly")), "analytics"
    if key.startswith("sig_"):
        for path in _signal2_paths(key[4:]):
            d = _read_json(path)
            if not d:
                continue
            bm = (d.get("stats") or {}).get("by_month")
            if isinstance(bm, list) and bm:
                out = {}
                for m in bm:
                    ts = _norm_ts(m.get("ts"))
                    if ts is None:
                        continue
                    _merge_month(out, _month_key(ts),
                                 float(m.get("pct") or 0.0),
                                 float(m.get("pnl_usd") or 0.0),
                                 int(m.get("n") or 0), int(m.get("wins") or 0),
                                 m.get("label"))
                return [out[k] for k in sorted(out)], "setup"
            months = _months_from_pairs(d.get("monthly")
                                        or (d.get("stats") or {}).get("monthly"))
            if months:
                return months, "setup"
    curves = _read_json(os.path.join(FINAL_DATA_DIR, "pnl_curves.json")) or {}
    for s in curves.get("series", []):
        if s.get("key") == key:
            return _months_from_curve(s.get("points")), "curve"
    return [], ""


@app.route("/api/monthly/<path:key>")
def api_monthly(key):
    """Прибыль по месяцам для бота / сетапа / портфельной кривой."""
    months, src = _months_for_key(key)
    if not months:
        return jsonify(dict(available=False, key=key,
                            hint="помесячных данных для этого ключа нет"))
    return jsonify(dict(available=True, key=key, source=src, months=months,
                        summary=_month_summary(months)))


# ===================== ОТБОР v3 (движок signal_engine3) =====================
# Конфиги: setups_v3.json (пишет finalize_v3.py), страницы: v3_<ключ>.json
# (пишет build_v3_analytics.py). Ключ — "<сетап>@<МОНЕТА>@<ТФ>".
# Пока отбор не запускался, страницы показывают заглушку, а не падают.

V3_FILE = "setups_v3.json"
V3_STATUS = {"on": ("торговый", "badge--on"),
             "watch": ("наблюдение", "badge--watch"),
             "off": ("не подтверждён", "badge--off")}
_v3_cache = {}          # путь -> (mtime, данные)
_v3_candles_cache = {}  # (монета, ТФ) -> список свечей


def v3_tf_label(iv):
    iv = int(iv)
    return f"{iv // 60}ч" if iv % 60 == 0 else f"{iv}м"


def v3_split_key(key, rec=None):
    """«<сетап>@<МОНЕТА>@<ТФ>» -> (сетап, монета, ТФ)."""
    rec = rec or {}
    parts = str(key).split("@")
    setup, symbol, iv = parts[0], None, None
    for p in parts[1:]:
        if p.isdigit():
            iv = int(p)
        elif p:
            symbol = p.upper()
    setup = str(rec.get("setup") or setup)
    symbol = str(rec.get("symbol") or symbol or "BTCUSDT").upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    try:
        iv = int(rec.get("interval_min") or iv or 240)
    except (TypeError, ValueError):
        iv = 240
    return setup, symbol, iv


def load_v3_setups():
    """(конфиги, мета, есть ли файл). Служебный ключ __meta__ отделён."""
    path = os.path.join(BOT_DIR, V3_FILE)
    raw = _read_json(path)
    if not isinstance(raw, dict):
        return {}, {}, False
    meta = raw.get("__meta__") if isinstance(raw.get("__meta__"), dict) else {}
    out = {}
    for key, rec in raw.items():
        if str(key).startswith("__") or not isinstance(rec, dict):
            continue
        setup, symbol, iv = v3_split_key(key, rec)
        out[key] = dict(rec, _setup=setup, _symbol=symbol, _iv=iv,
                        _tf=v3_tf_label(iv))
    return out, meta, True


def v3_status(rec):
    if rec.get("enabled"):
        return V3_STATUS["on"]
    if rec.get("watch"):
        return V3_STATUS["watch"]
    return V3_STATUS["off"]


def v3_card(key, rec):
    """Строка списка /v3 (всё, что рисуется, считается здесь, не в шаблоне)."""
    word, cls = v3_status(rec)
    h = rec.get("holdout") or {}
    tr = rec.get("transfer") or {}
    checks = rec.get("checks") or []
    return dict(
        key=key, setup=rec["_setup"], symbol=rec["_symbol"], iv=rec["_iv"],
        tf=rec["_tf"], coin=rec.get("coin") or rec["_symbol"].replace("USDT", ""),
        title=rec.get("title") or rec["_setup"],
        long=bool(rec.get("long", str(rec["_setup"]).endswith("long"))),
        lev=rec.get("rec_lev") or 10,
        status=word, status_cls=cls,
        filt=("on" if rec.get("enabled") else
              ("watch" if rec.get("watch") else "off")),
        holdout=h, verdict=rec.get("verdict") or "", reason=rec.get("reason") or "",
        checks=checks,
        n_ok=rec.get("n_checks_ok",
                     sum(1 for c in checks if c.get("ok"))),
        n_checks=len(checks) or 5,
        transfer_ok=tr.get("ok_coins"), transfer_n=tr.get("tested"),
        repro=rec.get("repro") or {},
        stats=rec.get("stats") or {},
        grid=rec.get("grid") or {},
        has_page=os.path.exists(os.path.join(FINAL_DATA_DIR, f"v3_{key}.json")))


@app.route("/v3")
def v3_page():
    """Список сетапов нового отбора (движок v3, мультимонетность)."""
    setups, meta, exists = load_v3_setups()
    cards = [v3_card(k, r) for k, r in setups.items()]
    prio = {"on": 0, "watch": 1, "off": 2}
    cards.sort(key=lambda c: (prio.get(c["filt"], 9),
                              -((c["holdout"] or {}).get("exp_r") or -9)))
    n_on = sum(1 for c in cards if c["filt"] == "on")
    n_w = sum(1 for c in cards if c["filt"] == "watch")
    n_off = len(cards) - n_on - n_w
    n_pages = sum(1 for c in cards if c["has_page"])
    coins = sorted({c["coin"] for c in cards})
    built = meta.get("built_at")
    built_txt = (time.strftime("%d.%m %H:%M", time.localtime(built))
                 if isinstance(built, (int, float)) and built else "")
    return render_template("v3.html", cards=cards, meta=meta, exists=exists,
                           n_on=n_on, n_watch=n_w, n_off=n_off,
                           n_pages=n_pages, coins=coins, built_txt=built_txt,
                           setups3=list(se3.SETUPS3) if se3 else [],
                           titles3=(se3.SETUP_TITLES3 if se3 else {}))


@app.route("/v3/<path:key>")
def v3_setup_page(key):
    """Страница одного сетапа v3: график, сделки, статистика, месяцы."""
    setups, meta, exists = load_v3_setups()
    rec = setups.get(key)
    if rec is None:
        if not exists:                 # отбор ещё не запускался — заглушка
            return render_template("v3_setup.html", key=key, rec=None,
                                   card=None, exists=False, meta={})
        return "Нет такого сетапа v3", 404
    return render_template("v3_setup.html", key=key, rec=rec,
                           card=v3_card(key, rec), exists=True, meta=meta,
                           mirror=rec.get("mirror_setup"))


@app.route("/api/v3/<path:key>")
def api_v3(key):
    """Аналитика сетапа v3 из webapp/data/v3_<ключ>.json (кэш по mtime)."""
    setup, symbol, iv = v3_split_key(key)
    path = os.path.join(FINAL_DATA_DIR, f"v3_{key}.json")
    mtime = os.path.getmtime(path) if os.path.exists(path) else None
    if mtime is None:
        return jsonify(dict(available=False, key=key, setup=setup,
                            symbol=symbol, interval_min=iv,
                            tf_label=v3_tf_label(iv),
                            hint="данные не посчитаны, запусти "
                                 "build_v3_analytics.py"))
    cached = _v3_cache.get(path)
    if not (cached and cached[0] == mtime):
        data = _read_json(path)
        if not isinstance(data, dict):
            return jsonify(dict(available=False, key=key,
                                hint="файл предпосчёта повреждён"))
        data["available"] = True
        cached = (mtime, data)
        _v3_cache[path] = cached
    out = dict(cached[1])
    out["built_at"] = int(mtime)
    return jsonify(out)


@app.route("/api/v3_candles")
def api_v3_candles():
    """Свечи монеты для графика v3: ?symbol=SOLUSDT&interval=240.
    Только из локальной истории — сеть со страницы не дёргаем."""
    symbol = str(request.args.get("symbol", "BTCUSDT")).upper()
    iv = str(request.args.get("interval", "240"))
    if not re.fullmatch(r"[A-Z0-9]{2,20}USDT", symbol) or not iv.isdigit():
        return jsonify([]), 400
    key = (symbol, iv)
    if key in _v3_candles_cache:
        return jsonify(_v3_candles_cache[key])
    path = os.path.join(BOT_DIR, f"history_{symbol}_{iv}m_1150d.json")
    if not os.path.exists(path):
        return jsonify([]), 404
    cc = _read_json(path) or []
    data = [dict(time=c[0] // 1000, open=c[1], high=c[2], low=c[3],
                 close=c[4]) for c in cc if isinstance(c, list) and len(c) >= 5]
    _v3_candles_cache[key] = data
    return jsonify(data)


@app.route("/evolution")
def evolution_page():
    path = os.path.join(FINAL_DATA_DIR, "evolution_timeline.json")
    timeline = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            timeline = json.load(fh)
    coins = {sym: sym.replace("USDT", "") for sym in config.SYMBOL_PARAMS}
    return render_template("evolution.html", timeline=timeline, coins=coins)


# ------------------------------------------------------------------ /review
# Ревью исследования — большой документ REVIEW.md в корне проекта. Страница
# рендерит его на лету, чтобы текст жил в одном месте: правится .md — меняется
# страница. Конвертер свой (внешних зависимостей у сайта нет) и понимает ровно
# то подмножество markdown, которым написан документ: заголовки, таблицы,
# списки, блоки кода, ``код``, **жирный**, *курсив*, --- и абзацы.

_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_MD_ITALIC = re.compile(r"(?<!\*)\*([^*\n][^*]*)\*(?!\*)", re.S)
_MD_TABLE_SEP = re.compile(r"^\|[\s:|-]+\|\s*$")


def _md_esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _md_inline(s):
    """Строчная разметка. Код вырезается ПЕРВЫМ и подменяется плейсхолдером,
    иначе `f *= x` внутри кода съедается курсивом."""
    s = _md_esc(s)
    box = []

    def stash(m):
        box.append(m.group(1))
        return "\x00%d\x00" % (len(box) - 1)

    s = _MD_INLINE_CODE.sub(stash, s)
    s = _MD_BOLD.sub(r"<b>\1</b>", s)
    s = _MD_ITALIC.sub(r"<i>\1</i>", s)
    for i, code in enumerate(box):
        s = s.replace("\x00%d\x00" % i, "<code>%s</code>" % code)
    return s


def _md_slug(n):
    return "rv%d" % n


def _md_to_html(text):
    """markdown -> (html, оглавление [(уровень, текст, id)])."""
    lines = text.replace("\r\n", "\n").split("\n")
    out, toc = [], []
    i, n_head = 0, 0
    while i < len(lines):
        ln = lines[i]

        # --- блок кода ```
        if ln.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(_md_esc(lines[i]))
                i += 1
            i += 1
            out.append("<pre class='rv-code'><code>%s</code></pre>"
                       % "\n".join(buf))
            continue

        # --- пустая строка / горизонтальная линия
        if not ln.strip():
            i += 1
            continue
        if ln.strip() in ("---", "***", "___"):
            out.append("<hr class='rv-hr'>")
            i += 1
            continue

        # --- заголовок
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            lvl = len(m.group(1))
            n_head += 1
            sid = _md_slug(n_head)
            txt = m.group(2).strip()
            toc.append((lvl, re.sub(r"[*`]", "", txt), sid))
            out.append("<h%d id='%s' class='rv-h%d'>%s</h%d>"
                       % (min(lvl + 1, 6), sid, lvl, _md_inline(txt),
                          min(lvl + 1, 6)))
            i += 1
            continue

        # --- таблица: строка на |, а следующая — разделитель |---|---|
        if (ln.startswith("|") and i + 1 < len(lines)
                and _MD_TABLE_SEP.match(lines[i + 1])):
            def cells(row):
                return [c.strip() for c in row.strip().strip("|").split("|")]
            head = cells(ln)
            i += 2
            body = []
            while i < len(lines) and lines[i].startswith("|"):
                body.append(cells(lines[i]))
                i += 1
            html = ["<div class='tbl-wrap'><table class='tbl rv-tbl'><thead><tr>"]
            html += ["<th>%s</th>" % _md_inline(c) for c in head]
            html.append("</tr></thead><tbody>")
            for row in body:
                html.append("<tr>")
                html += ["<td>%s</td>" % _md_inline(c) for c in row]
                html.append("</tr>")
            html.append("</tbody></table></div>")
            out.append("".join(html))
            continue

        # --- список (маркированный или нумерованный), пункт может занимать
        #     несколько строк: продолжение — строка с отступом
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", ln)
        if m:
            tag = "ol" if m.group(2)[:1].isdigit() else "ul"
            items, cur = [], None
            while i < len(lines):
                mm = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
                if mm:
                    if cur is not None:
                        items.append(cur)
                    cur = mm.group(3).strip()
                    i += 1
                elif (lines[i].startswith(" ") and lines[i].strip()
                      and cur is not None):
                    cur += " " + lines[i].strip()
                    i += 1
                else:
                    break
            if cur is not None:
                items.append(cur)
            out.append("<%s class='rv-list'>%s</%s>"
                       % (tag, "".join("<li>%s</li>" % _md_inline(x)
                                       for x in items), tag))
            continue

        # --- абзац
        buf = []
        while i < len(lines) and lines[i].strip():
            s = lines[i]
            if (s.startswith("#") or s.startswith("```")
                    or s.strip() in ("---", "***", "___")
                    or re.match(r"^(\s*)([-*]|\d+\.)\s+", s)
                    or (s.startswith("|") and i + 1 < len(lines)
                        and _MD_TABLE_SEP.match(lines[i + 1]))):
                break
            buf.append(s.strip())
            i += 1
        if buf:
            out.append("<p class='rv-p'>%s</p>" % _md_inline(" ".join(buf)))
    return "\n".join(out), toc


@app.route("/review")
def review_page():
    path = os.path.join(BOT_DIR, "REVIEW.md")
    if not os.path.exists(path):
        return render_template("review.html", body=None, toc=[], meta={})
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    body, toc = _md_to_html(text)
    meta = dict(
        chars=len(text),
        lines=text.count("\n") + 1,
        kb=round(os.path.getsize(path) / 1024),
        mtime=time.strftime("%Y-%m-%d %H:%M",
                            time.localtime(os.path.getmtime(path))),
        sections=sum(1 for lvl, _t, _s in toc if lvl == 2),
    )
    return render_template("review.html", body=body, toc=toc, meta=meta)


@app.route("/bot/<symbol>/<mode>")
def bot_page(symbol, mode):
    p = config.SYMBOL_PARAMS.get(symbol, {}).get(mode)
    if not p:
        return "Нет такого бота", 404
    meta = META.get((symbol, mode), {})
    interval = p.get("interval", "15")
    hd = load_honest() if mode == "final" else {}
    h = honest_bot(symbol) if mode == "final" else None
    return render_template(
        "bot.html", symbol=symbol, coin=symbol.replace("USDT", ""),
        mode=mode, mode_name=MODE_NAMES.get(mode, mode), lev=p.get("lev", 5),
        interval_min=int(interval),
        # ТФ отображения (сам бот всегда торгует на своём) и граница экзамена
        view_tfs=bot_view_tfs(symbol),
        holdout_start=((hd.get("periods") or {}).get("holdout") or {}).get("start"),
        tf_label=("4ч" if interval == "240" else f"{interval}m"),
        ver=VER.get((symbol, mode), ""),
        title=meta.get("title", f"{symbol} {mode}"),
        # подзаголовок: честные цифры, если они посчитаны; иначе — исторические
        stats=(honest_stats_line(h, hd) or meta.get("stats", "")),
        stats_hist=meta.get("stats", ""),
        honest=h, hmeta=hd, about=meta.get("about", ""),
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
    aux = e8.make_aux_builder(pct5, bars_per_day)(symbol, candles)
    filt = e8.make_filter8(g, aux)
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


# --- ПОЛНАЯ история для графика страницы бота -------------------------------
# Свечи и сделки за все 3.2 года: без них на графике не видно ни границы
# обучение/экзамен, ни того, что сделок у бота больше тысячи. Оба маршрута
# читают ТОЛЬКО локальные файлы — со страницы сеть не дёргаем.
VIEW_TFS = (("15", "15м"), ("60", "1ч"), ("240", "4ч"), ("D", "1д"))
_bot_trades_cache = {}     # symbol -> (mtime, данные)


def hist_path(symbol, iv):
    return os.path.join(BOT_DIR, f"history_{symbol}_{iv}m_1150d.json")


def bot_view_tfs(symbol):
    """ТФ отображения, для которых история уже лежит на диске."""
    return [dict(iv=iv, label=lab) for iv, lab in VIEW_TFS
            if os.path.exists(hist_path(symbol, iv))]


@app.route("/api/bot_candles/<symbol>")
def api_bot_candles(symbol):
    """Свечи монеты за 1150 дней: ?interval=15|60|240|D.

    Отдаём файл истории КАК ЕСТЬ ([[ts_мс,o,h,l,c], ...]): 110 тысяч
    15-минутных свечей в виде объектов — это лишние 4 МБ на каждый запрос и
    заметная пауза в браузере, а TVK.bars() одинаково понимает и массивы."""
    symbol = str(symbol).upper()
    iv = str(request.args.get("interval", "15"))
    if not re.fullmatch(r"[A-Z0-9]{2,20}USDT", symbol):
        return jsonify([]), 400
    if iv not in [x[0] for x in VIEW_TFS]:
        return jsonify([]), 400
    path = hist_path(symbol, iv)
    if not os.path.exists(path):
        return jsonify([]), 404
    return send_file(path, mimetype="application/json",
                     conditional=True, max_age=600)


@app.route("/api/bot_trades/<symbol>")
def api_bot_trades(symbol):
    """Циклы бота за всю историю (предпосчёт build_analytics.py): вход,
    доливки сетки, средняя цена, стоп/тейк, выход, PnL."""
    symbol = str(symbol).upper()
    if not re.fullmatch(r"[A-Z0-9]{2,20}USDT", symbol):
        return jsonify(dict(available=False)), 400
    path = os.path.join(FINAL_DATA_DIR, f"bot_trades_{symbol}.json")
    mtime = os.path.getmtime(path) if os.path.exists(path) else None
    if mtime is None:
        return jsonify(dict(available=False, trades=[],
                            hint="нет предпосчёта — запусти "
                                 "python build_analytics.py bots"))
    cached = _bot_trades_cache.get(symbol)
    if not (cached and cached[0] == mtime):
        data = _read_json(path) or {}
        data["available"] = bool(data.get("trades"))
        cached = (mtime, data)
        _bot_trades_cache[symbol] = cached
    return jsonify(cached[1])


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


# ===================================================== конструктор сетапов
# Страница /builder: пользователь собирает идею мышкой, движок signal_builder
# гоняет её через тот же честный экзамен, что и весь проект. Здесь только
# маршруты — вся математика в signal_builder.py, ни одной проверки тут нет.
import threading  # noqa: E402

try:
    import signal_builder as sbld  # noqa: E402
except Exception as _e:            # noqa: BLE001
    sbld = None
    print("signal_builder не импортирован (страница /builder отключена):", _e)

USER_SIGNALS_PATH = os.path.join(FINAL_DATA_DIR, "user_signals.json")

# Проверка занимает 1-30 с и держит модульные кэши signal_builder. Flask
# многопоточный, поэтому два одновременных прогона портили бы друг другу
# LRU-кэш рядов и просто соревновались бы за процессор. Пускаем по одному.
_builder_lock = threading.Lock()
_builder_atr_cache = {}

# Готовые примеры «с чего начать». Последний — заведомо бессмысленный вход
# по времени: он нужен, чтобы владелец увидел, как выглядит «не подтверждён»,
# и не считал такой вердикт поломкой страницы.
BUILDER_PRESETS = [
    dict(
        id="capitulation",
        name="Капитуляция",
        about="Резкое падение + перепроданность + первая зелёная свеча. "
              "Классика проекта: на holdout выглядела прилично, экзамен не "
              "прошла.",
        spec=dict(
            symbol="BTCUSDT", interval="240", side="long",
            entry=[dict(kind="drop", pct=6.0, days=2.0),
                   dict(kind="rsi", period=14, op="<", value=30.0),
                   dict(kind="candle", dir="green", body_min=0.0)],
            exit=dict(stop=dict(type="atr", value=2.0),
                      tp=dict(type="r", value=2.0),
                      be_after_r=0.0, timeout_days=30, max_stop_pct=6.0),
            grid=dict(levels=1, step_atr=1.0, mult=1.5),
            lev=5, cooldown_bars=0)),
    dict(
        id="breakout",
        name="Пробой на объёме",
        about="Закрытие выше максимума 20 дней, объём выше обычного, цена "
              "над EMA200. Стоп 2.5 ATR, цель 3R.",
        spec=dict(
            symbol="ETHUSDT", interval="240", side="long",
            entry=[dict(kind="breakout", days=20.0, dir="high"),
                   dict(kind="volume", mult=1.5, window=50),
                   dict(kind="ema_price", period=200, op="above")],
            exit=dict(stop=dict(type="atr", value=2.5),
                      tp=dict(type="r", value=3.0),
                      be_after_r=0.0, timeout_days=20, max_stop_pct=7.0),
            grid=dict(levels=1, step_atr=1.0, mult=1.5),
            lev=5, cooldown_bars=0)),
    dict(
        id="pullback",
        name="Откат в тренде",
        about="EMA50 выше EMA200, RSI провалился под 45, цена у свинг-минимума. "
              "Стоп за свингом, безубыток после 1R, сетка из двух колен.",
        spec=dict(
            symbol="SOLUSDT", interval="240", side="long",
            entry=[dict(kind="ema", fast=50, slow=200, op="above"),
                   dict(kind="rsi", period=14, op="<", value=45.0),
                   dict(kind="level_dist", level="swing_low", max_atr=1.0,
                        pivot=5, window=120)],
            exit=dict(stop=dict(type="swing", value=10.0, buf_atr=0.3),
                      tp=dict(type="r", value=2.0),
                      be_after_r=1.0, timeout_days=25, max_stop_pct=8.0),
            grid=dict(levels=2, step_atr=1.0, mult=1.5),
            lev=5, cooldown_bars=0)),
    dict(
        id="garbage",
        name="Заведомо мусор",
        about="Вход просто по часу и дню недели — никакой причины. Держим "
              "как эталон: так выглядит идея, которой нет.",
        spec=dict(
            symbol="BTCUSDT", interval="240", side="long",
            entry=[dict(kind="hour", **{"from": 8, "to": 20}),
                   dict(kind="weekday", days=[0, 1, 2, 3, 4])],
            exit=dict(stop=dict(type="atr", value=2.0),
                      tp=dict(type="r", value=2.0),
                      be_after_r=0.0, timeout_days=30, max_stop_pct=6.0),
            grid=dict(levels=1, step_atr=1.0, mult=1.5),
            lev=5, cooldown_bars=0)),
]


def _builder_display_intervals():
    """Какие ТФ можно ПОКАЗАТЬ на графике (файлы истории лежат локально;
    сеть со страницы не дёргаем)."""
    out = {}
    for sym in (getattr(sbld, "SYMBOLS", None) or list(config.SYMBOL_PARAMS)):
        ivs = []
        for iv in ("15", "60", "240", "D"):
            if os.path.exists(os.path.join(
                    BOT_DIR, f"history_{sym}_{iv}m_1150d.json")):
                ivs.append(iv)
        if ivs:
            out[sym] = ivs
    return out


def _read_user_signals():
    d = _read_json(USER_SIGNALS_PATH)
    if not isinstance(d, dict) or not isinstance(d.get("items"), list):
        return dict(items=[])
    return d


def _write_user_signals(d):
    tmp = USER_SIGNALS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, USER_SIGNALS_PATH)


def _builder_exit_px(t):
    """Цена выхода сделки: движок её не хранит (стоп мог переехать в
    безубыток или ползти трейлингом), поэтому восстанавливаем — тейк и стоп
    по своим ценам, остальное из R и фактического риска. Нужна только для
    пунктира «вход -> выход» на графике."""
    try:
        kind = t.get("exit_kind")
        if kind == "tp" and t.get("tp_target"):
            return float(t["tp_target"])
        if kind == "stop":
            return float(t["stop"])
        avg = float(t.get("avg_entry") or t["entry"])
        risk = abs(avg - float(t["stop"]))
        sgn = 1.0 if t.get("side") == "L" else -1.0
        return avg + sgn * float(t.get("r") or 0.0) * risk
    except Exception:                        # noqa: BLE001
        return None


def _builder_trades(run, part):
    """Сделки прогона в компактном виде для графика и таблицы."""
    out = []
    for t in run["trades"]:
        out.append(dict(
            part=part, entry_ts=t["entry_ts"], exit_ts=t["exit_ts"],
            side=t["side"], entry=t["entry"], avg_entry=t.get("avg_entry"),
            stop=t["stop"], tp=t.get("tp_target"),
            exit_px=_builder_exit_px(t),
            pnl=t["pnl"], r=t["r"], reason=t["reason"],
            exit_kind=t.get("exit_kind"), hold_h=t["hold_h"],
            grid_fills=t.get("grid_fills"), stop_pct=t.get("stop_pct"),
            regime=t.get("regime"), rsi=t.get("rsi"),
            atr_pct=t.get("atr_pct"), mae_r=t.get("mae_r"),
            mfe_r=t.get("mfe_r"), be_moved=t.get("be_moved")))
    return out


@app.route("/builder")
def builder_page():
    schema = sbld.describe() if sbld else None
    return render_template(
        "builder.html", schema=schema,
        presets=BUILDER_PRESETS, display_intervals=_builder_display_intervals(),
        saved=_read_user_signals()["items"],
        engine_ok=bool(sbld),
        engine_err=None if sbld else "signal_builder.py не импортировался — "
                                     "смотри лог сервера")


@app.route("/api/builder/schema")
def api_builder_schema():
    if not sbld:
        return jsonify(dict(ok=False, error="движок конструктора недоступен")), 503
    return jsonify(dict(ok=True, schema=sbld.describe(),
                        presets=BUILDER_PRESETS,
                        display_intervals=_builder_display_intervals()))


@app.route("/api/builder/atr")
def api_builder_atr():
    """Типичная ширина ATR в процентах цены — чтобы страница могла ЧЕСТНО
    показать, во сколько процентов превратится стоп «2 ATR» и упрётся ли он
    в ликвидацию. ATR берём тот же, которым считает движок."""
    if not sbld:
        return jsonify(dict(ok=False, error="движок конструктора недоступен")), 503
    sym = str(request.args.get("symbol", "BTCUSDT")).upper()
    iv = str(request.args.get("interval", "240"))
    key = (sym, iv)
    if key in _builder_atr_cache:
        return jsonify(_builder_atr_cache[key])
    if not _builder_lock.acquire(timeout=90):
        return jsonify(dict(ok=False, busy=True,
                            error="идёт другая проверка")), 503
    try:
        ser = sbld.load_series(sym, iv)
        vals = [x for x in ser["ctx"]["atr_bar"] if x]
        if not vals:
            return jsonify(dict(ok=False, error="нет ATR")), 404
        vals.sort()
        out = dict(ok=True, symbol=sym, interval=iv, bars=ser["n"],
                   atr_pct=round(vals[len(vals) // 2] * 100, 3),
                   atr_p10=round(sbld._pct(vals, 0.10) * 100, 3),
                   atr_p90=round(sbld._pct(vals, 0.90) * 100, 3))
    except sbld.SpecError as e:
        return jsonify(dict(ok=False, error=str(e))), 400
    except Exception as e:                   # noqa: BLE001
        return jsonify(dict(ok=False, error=f"{type(e).__name__}: {e}")), 500
    finally:
        _builder_lock.release()
    _builder_atr_cache[key] = out
    return jsonify(out)


@app.route("/api/builder/candles")
def api_builder_candles():
    """Свечи для графика конструктора — только из локальных файлов истории."""
    sym = str(request.args.get("symbol", "BTCUSDT")).upper()
    iv = str(request.args.get("interval", "240"))
    if not re.fullmatch(r"[A-Z0-9]{2,20}USDT", sym) or iv not in ("15", "60",
                                                                 "240", "D"):
        return jsonify(dict(ok=False, error="плохие параметры")), 400
    key = ("bld", sym, iv)
    if key in _v3_candles_cache:
        return jsonify(_v3_candles_cache[key])
    path = os.path.join(BOT_DIR, f"history_{sym}_{iv}m_1150d.json")
    if not os.path.exists(path):
        return jsonify(dict(ok=False,
                            error=f"нет локальной истории {sym} {iv}")), 404
    cc = _read_json(path) or []
    data = [dict(time=c[0] // 1000, open=c[1], high=c[2], low=c[3],
                 close=c[4]) for c in cc if isinstance(c, list) and len(c) >= 5]
    _v3_candles_cache[key] = data
    return jsonify(data)


@app.route("/api/builder/run", methods=["POST"])
def api_builder_run():
    """Честная проверка собранного сетапа. Тело: {"spec": {...},
    "tried": N}. Ответ: {"ok": true, "result": {...}, "trades": [...]}
    либо {"ok": false, "error": "текст по-русски"}."""
    if not sbld:
        return jsonify(dict(ok=False,
                            error="движок конструктора недоступен: "
                                  "signal_builder.py не импортировался")), 503
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("spec"), dict):
        return jsonify(dict(ok=False,
                            error="ожидался JSON вида "
                                  "{\"spec\": {...}, \"tried\": 1}")), 400
    spec = body["spec"]
    try:
        tried = max(1, min(10000, int(body.get("tried") or 1)))
    except Exception:                        # noqa: BLE001
        tried = 1
    if not _builder_lock.acquire(blocking=False):
        return jsonify(dict(ok=False, busy=True,
                            error="прямо сейчас считается другая проверка — "
                                  "дождитесь её окончания")), 429
    t0 = time.time()
    try:
        norm, warn = sbld.validate(spec)
        res = sbld.evaluate(norm, tried=tried)
        norm_run = dict(res["spec"])
        norm_run["_normalized"] = True
        trades = (_builder_trades(sbld.build_run(norm_run, part="train"),
                                  "train")
                  + _builder_trades(sbld.build_run(norm_run, part="holdout"),
                                    "holdout"))
        res["trades"] = trades
        res["validate_warnings"] = warn
        return jsonify(dict(ok=True, result=res,
                            elapsed=round(time.time() - t0, 2)))
    except sbld.SpecError as e:
        return jsonify(dict(ok=False, error=str(e))), 400
    except MemoryError:
        return jsonify(dict(ok=False,
                            error="не хватило памяти — попробуйте ТФ 4ч "
                                  "вместо 15м")), 500
    except Exception as e:                   # noqa: BLE001
        import traceback
        traceback.print_exc()
        return jsonify(dict(ok=False,
                            error=f"движок споткнулся: {type(e).__name__}: "
                                  f"{e}")), 500
    finally:
        _builder_lock.release()


@app.route("/api/builder/saved", methods=["GET", "POST"])
def api_builder_saved():
    """GET — список сохранённых сетапов, POST — сохранить/перезаписать."""
    if request.method == "GET":
        return jsonify(dict(ok=True, items=_read_user_signals()["items"]))
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("spec"), dict):
        return jsonify(dict(ok=False, error="нужен объект со spec")), 400
    name = str(body.get("name") or "").strip()[:80] or "без имени"
    if sbld:                                 # мусор не храним
        try:
            sbld.validate(body["spec"])
        except sbld.SpecError as e:
            return jsonify(dict(ok=False,
                                error=f"сетап не сохранён: {e}")), 400
    d = _read_user_signals()
    sid = str(body.get("id") or "").strip()
    now = int(time.time() * 1000)
    item = dict(id=sid or f"u{now}", name=name, spec=body["spec"],
                verdict=body.get("verdict"), summary=body.get("summary"),
                tried=body.get("tried"), created=now, updated=now)
    items = d["items"]
    for i, it in enumerate(items):
        if it.get("id") == item["id"]:
            item["created"] = it.get("created", now)
            items[i] = item
            break
    else:
        items.insert(0, item)
    d["items"] = items[:100]
    _write_user_signals(d)
    return jsonify(dict(ok=True, items=d["items"], id=item["id"]))


@app.route("/api/builder/saved/<sid>", methods=["DELETE"])
def api_builder_saved_delete(sid):
    d = _read_user_signals()
    before = len(d["items"])
    d["items"] = [it for it in d["items"] if it.get("id") != sid]
    if len(d["items"]) == before:
        return jsonify(dict(ok=False, error="такого сетапа нет",
                            items=d["items"])), 404
    _write_user_signals(d)
    return jsonify(dict(ok=True, items=d["items"]))


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
