# -*- coding: utf-8 -*-
"""Состояние новостного фона: приём оценённых новостей и свёртка в два числа.

Контракт между MCP-сервером (пишет) и читателями фона:
    news_state.json  ->  для каждой монеты (index, heat)

СТАТУС НА СЕГОДНЯ: читатели — только сайт и предпросмотр в MCP. Ни один
торгующий бот сюда не заглядывает (bot_rsi.py фон не читает, ключей news_*
в config.py нет, спотового бота в репозитории нет). Функции влияния ниже
написаны и покрыты клампами заранее, но на реальные сделки не действуют.

    index ∈ [-1..+1] — НАПРАВЛЕННЫЙ фон: +1 всё позитивно, -1 всё негативно.
    heat  ∈ [ 0..+1] — НАКАЛ: сколько вообще значимого происходит, без знака.
                       Высокий heat при index≈0 = «шумно и непонятно».

Почему два числа, а не одно: направление и неопределённость влияют на торговлю
по-разному. Попутный фон — повод дать прибыли пробежать. Просто громкий фон —
повод не открываться вовсе, независимо от знака.

ГРАНИЦЫ ВЛИЯНИЯ (это модуль безопасности, а не только арифметики).
Текст новости — недоверенный вход: его пишут посторонние люди, и он может
содержать прямые указания модели. Поэтому фон физически не способен:
  - открыть позицию (вход по-прежнему требует RSI+зона+все прежние фильтры);
  - поднять плечо, маржу или отключить DRY_RUN;
  - раздвинуть стоп дальше исходного (фон умеет только ПОДТЯГИВАТЬ стоп).
Всё, что фон умеет: запретить вход, подтянуть стоп, сдвинуть тейк в пределах
жёстких клампов ниже. Максимум влияния ограничен константами MAX_*, а не
доверием к содержимому новости.

Протухание: записи живут не дольше своего горизонта, а весь файл — не дольше
STATE_MAX_AGE_H. Если Claude перестал обновлять фон, влияние затухает до нуля
само. «Замерший» фон опаснее отсутствующего.
"""

import json
import logging
import math
import os
import tempfile
import time

import newsfeed

log = logging.getLogger("news_state")

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "news_state.json")

# --- жёсткие потолки (менять осознанно: это границы влияния новостей) ---
MAX_ITEMS = 200          # больше записей сервер не хранит
MAX_WEIGHT = 1.0         # вес одной новости
MIN_HORIZON_H = 1.0      # горизонт действия новости
MAX_HORIZON_H = 72.0
STATE_MAX_AGE_H = 12.0   # файл старше — фон считается отсутствующим
SAT = 2.0                # «насыщение»: сумма эфф. весов, дающая |index| = 1
# ИЗВЕСТНАЯ ПРОБЛЕМА КАЛИБРОВКИ (замер 31.07.2026, обычный новостной день):
# у BTC сырая сумма весов вышла 2.99 при SAT=2.0 — heat упёрся в 1.000 и
# держался бы выше 0.75 около 15 часов. У альтов запас есть (0.45-0.59).
# Причина структурная: BTC собирает и адресные новости, и все общерыночные,
# а SAT одинаков для всех монет. Пока это не откалибровано по накопленной
# статистике, news_heat_max на BTC включать нельзя — он будет запрещать
# торговлю почти постоянно. Подробности — docs/NEWS_STRATEGY.md, раздел 6.

# Бета к BTC: насколько общерыночная новость (без прямого упоминания монеты)
# относится к этой монете. BTC=1.0 по определению; альты двигаются сильнее
# в накале, но общерыночный тон для них всё же слабее адресного.
BETA = {"BTCUSDT": 1.00, "ETHUSDT": 0.85, "SOLUSDT": 0.75,
        "LTCUSDT": 0.70, "DOGEUSDT": 0.65}
DEFAULT_BETA = 0.60

# --- потолки влияния на сделку ---
# ВАЖНО ПРО СТАТУС: ни один бот эти потолки сейчас не применяет. Фьючерсный
# bot_rsi.py новостной фон не читает вовсе, ключей news_* в config.py нет,
# спотового бота в репозитории не существует. Единственные потребители фона —
# сайт (панель на главной) и MCP-инструмент предпросмотра. Всё ниже — граница
# влияния НА БУДУЩЕЕ подключение, а не описание работающей торговли.
#
# Потолки были выставлены по стресс-тесту news_stress.py (канал включён на
# максимум все 3.2 года). Сам скрипт и его результаты удалены в коммите
# 9b9357a вместе с дефектом движка, на котором считались: базовые доходности
# того замера (DOGE +133.5%) оказались артефактом безубытка по хаю бара.
# Поэтому конкретные числа того стресс-теста больше ничего не подтверждают —
# см. docs/NEWS_STRATEGY.md, раздел 5. Сохранён только вывод, который от чисел
# не зависит: растянутый тейк держит позицию дольше и чаще доводит её до
# стопа, поэтому потолок растяжения (+15%) вдвое ниже потолка ужатия тейка
# (30%) и более чем вдвое ниже потолка поджатия стопа (35%) — ошибиться в
# сторону жадности дороже, чем в сторону осторожности. Формулировка сверена
# с docs/NEWS_STRATEGY.md, раздел 5: «вдвое ниже остальных» было неточно,
# потолки разные.
MAX_TP_STRETCH = 0.15    # тейк можно растянуть не более чем на +15%
MAX_TP_SHRINK = 0.30     # и ужать не более чем на -30%
MAX_SL_TIGHTEN = 0.35    # стоп можно подтянуть максимум на 35% пути к входу
                         # (раздвинуть стоп нельзя вообще — такой ветки в коде
                         # нет ни при каких значениях коэффициентов)

CATEGORIES = {
    # категория: (импакт 0..1, «знаковая ли» — бывает ли у неё явное направление)
    "regulation": (0.85, True),
    "etf_flows": (0.80, True),
    "exchange_hack": (0.95, True),
    "protocol_incident": (0.80, True),
    "macro": (0.75, True),
    "institutional": (0.60, True),
    "listing": (0.55, True),
    "upgrade_fork": (0.50, True),
    "onchain_flows": (0.45, True),
    "liquidations": (0.55, True),
    "partnership": (0.30, True),
    "market_report": (0.25, True),
    "opinion": (0.10, False),
    "price_analysis": (0.05, False),
    "other": (0.15, True),
}


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _now_ms():
    return int(time.time() * 1000)


def _num(v, default=0.0):
    """Конечное число из недоверенного json — либо значение по умолчанию.

    Записи фона лежат в обычном json на диске: его пишет MCP-сервер, но правят
    руками и читают все подряд. Строка вместо числа, NaN или отсутствующий
    ключ обязаны означать «этой новости как бы нет», а не исключение посреди
    свёртки фона.
    """
    try:
        v = float(v)
    except Exception:
        return default
    return v if math.isfinite(v) else default


def _text(v, limit=None, default=""):
    """Строка из недоверенного поля или значение по умолчанию.

    str() у чужого объекта может бросить что угодно (замер фаззера: объект с
    падающим __str__ роняет sanitize на самой первой строке), поэтому ни одно
    приведение к строке на границе недоверенного входа не делается голым.
    limit=None — не обрезать: длину полей записи режет сам sanitize там, где
    это меняет смысл, и лишняя обрезка сдвинула бы id уже принятых новостей.
    """
    try:
        s = str(v)
    except Exception:
        return default
    return s if limit is None else s[:limit]


_NOT_A_PATH = object()   # «это вообще не путь», в отличие от «путь по умолчанию»


def _as_path(path):
    """Путь к файлу состояния, STATE_FILE по умолчанию либо _NOT_A_PATH.

    Числа и bool путями НЕ считаются, и это не педантизм: open(1) открывает
    не файл, а ФАЙЛОВЫЙ ДЕСКРИПТОР. Замер фаззера 15.08.2026: load(1) и
    load(True) читали и затем ЗАКРЫВАЛИ стандартный вывод процесса — после
    такого вызова бот и MCP-сервер остаются немыми (os.write(1, b"") даёт
    OSError «Bad file descriptor»), причём сам вызов выглядел совершенно
    успешным и возвращал пустое состояние. bool — подкласс int, отдельной
    строки не требует: обоих отсекает одна проверка типа.

    Строка с NUL внутри — тоже не путь: os.path.exists молча даёт False, а
    open и os.replace на ней бросают ValueError.

    Путь-БАЙТЫ не принимается сознательно, хотя open его понимает: атомарная
    запись в save идёт через tempfile.mkstemp с текстовым prefix, и смешение
    байтового каталога с текстовым префиксом даёт TypeError «Can't mix bytes
    and non-bytes in path components» уже в момент записи. Принять байты в
    load и споткнуться о них в save — худший из вариантов: файл фона в этом
    месте только читался бы, но никогда не обновлялся. STATE_FILE и все
    вызывающие в репозитории — обычные строки.

    Пустое значение (None, "") означает «путь по умолчанию» — так вели себя
    все прежние вызывающие, и это поведение сохранено.
    """
    if path is None:
        return STATE_FILE
    if not isinstance(path, (str, os.PathLike)):
        return _NOT_A_PATH
    try:
        path = os.fspath(path)
    except Exception:
        return _NOT_A_PATH
    if not isinstance(path, str):      # __fspath__ вернул байты
        return _NOT_A_PATH
    if not path:
        return STATE_FILE
    if "\x00" in path:
        return _NOT_A_PATH
    return path


def empty_state():
    return dict(version=1, updated_ms=0, items=[])


def load(path=None):
    # путь разрешается в момент ВЫЗОВА, а не при импорте: иначе тест не может
    # подменить STATE_FILE и вынужден писать в боевой файл фона
    given = type(path).__name__
    path = _as_path(path)
    if path is _NOT_A_PATH:
        # намеренно НЕ подставляем STATE_FILE: вызов с негодным путём — это
        # ошибка вызывающего, и молча прочитать вместо него боевой файл фона
        # значит скрыть её. Печатаем ТИП аргумента, а не сам аргумент: у него
        # может падать __str__
        log.error("news_state.load: аргумент типа %s — это не путь к файлу; "
                  "фон считается отсутствующим", given)
        return empty_state()
    if not os.path.exists(path):
        return empty_state()
    try:
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        # ValueError покрывает и json.JSONDecodeError (его подкласс), и NUL
        # в имени файла, и прочий мусор в пути
        return empty_state()
    if not isinstance(st, dict) or "items" not in st:
        return empty_state()
    return st


def save(state, path=None):
    """Атомарная запись: бот может читать файл в любой момент.

    Возвращает записанное состояние либо None, если писать было нечего:
    состояние не словарь или путь — не путь. В этом случае файл НЕ ТРОГАЕТСЯ
    вовсе и в лог уходит ERROR. Так выбрано сознательно: испортить единственный
    файл фона (или уронить MCP-сервер посреди приёма оценок) хуже, чем не
    записать заведомо негодные данные, а строка ERROR не даёт этому пройти
    незаметно. Настоящие сбои записи — нет места на диске, нет прав — по-прежнему
    выходят наружу исключением: это не мусор в аргументах, а беда с диском, и
    молчать о ней нельзя.
    """
    path = _as_path(path)
    if path is _NOT_A_PATH or not isinstance(state, dict):
        log.error("news_state.save: состояние типа %s, путь типа %s — "
                  "не записываем ничего, файл фона остаётся прежним",
                  type(state).__name__,
                  "негодный" if path is _NOT_A_PATH else "ok")
        return None
    state["updated_ms"] = _now_ms()
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".news_state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except (TypeError, ValueError, RecursionError) as e:
        # состояние содержит то, что в json не кладётся (байты, чужой объект,
        # кольцевая ссылка). Это НЕ беда с диском, а негодные данные — реакция
        # та же, что на негодный тип аргумента выше: временный файл убираем,
        # боевой не трогаем, ERROR в лог. Прежний файл фона при этом цел:
        # os.replace до конца записи не доходит
        log.error("news_state.save: состояние не сериализуется в json "
                  "(%s) — файл фона оставлен прежним", type(e).__name__)
        if os.path.exists(tmp):
            os.unlink(tmp)
        return None
    except BaseException:
        # всё остальное — уже беда с диском (нет места, нет прав) или Ctrl+C:
        # такое обязано выходить наружу, а не превращаться в тихое «не записал»
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return state


def sanitize(rec):
    """Приводит запись от Claude к безопасному виду или отклоняет её.

    Возвращает (запись, None) либо (None, причина отказа). Сервер доверяет
    только СТРУКТУРЕ: домен из белого списка, вес в клампе, категория из
    словаря. Заголовок сохраняется как данные и никогда не интерпретируется.
    """
    if not isinstance(rec, dict):
        return None, "запись не объект"
    # ПОЧЕМУ except Exception, а не перечень типов, во всех преобразованиях
    # ниже. Запись приходит от языковой модели обычной JSON-строкой, и
    # json.loads умеет отдать значения, на которых int()/float()/str() бросают
    # НЕ TypeError и не ValueError: целое из 400 цифр даёт float() ->
    # OverflowError, Infinity даёт int() -> OverflowError. Перечень
    # «ожидаемых» типов уже один раз подвёл в ext_data ровно так же. Контракт
    # «мусор от модели превращается в отказ, а не в исключение» не может
    # держаться на полноте такого перечня. BaseException не ловится: Ctrl+C
    # должен работать.
    url = _text(rec.get("url", ""), default=None)
    title = _text(rec.get("title", ""), default=None)
    if url is None or title is None:
        return None, "url или title не приводится к строке"
    url, title = url.strip(), title.strip()
    if not url or not title:
        return None, "нет url или title"
    # домен берётся из разобранного hostname, а не поиском подстроки:
    # подстрока пропускала https://evil.io/?ref=coindesk.com и
    # https://theblock.com.attacker.io/ — см. newsfeed.match_allowed_domain
    # (разбор чужой строки обёрнут: newsfeed не наш модуль, и обещание «не
    # бросаем» не должно зависеть от того, все ли формы url там предусмотрены)
    try:
        domain = newsfeed.match_allowed_domain(url)
    except Exception:
        domain = None
    if domain is None:
        return None, f"домен вне белого списка: {url[:80]}"

    cat = _text(rec.get("category", "other"), default=None)
    if cat not in CATEGORIES:
        return None, f"неизвестная категория: {cat}"

    try:
        weight = float(rec.get("weight", 0.0))
    except Exception:
        return None, "вес не число"
    if not math.isfinite(weight):
        return None, "вес не число"
    weight = _clamp(weight, 0.0, MAX_WEIGHT)

    try:
        direction = int(rec.get("direction", 0))
    except Exception:
        return None, "direction не целое"
    if direction not in (-1, 0, 1):
        return None, "direction должен быть -1, 0 или 1"
    if not CATEGORIES[cat][1]:
        direction = 0          # у мнений и теханализа знака не бывает

    try:
        horizon = float(rec.get("horizon_h", 12.0))
    except Exception:
        horizon = 12.0
    # NaN проходил кламп НАСКВОЗЬ и попадал в запись: float("nan") не бросает,
    # а _clamp сравнивает — и оба сравнения с NaN ложны, так что возвращалось
    # само NaN. Достижимо от модели обычной JSON-строкой: json.loads принимает
    # литерал NaN. Дальше NaN-горизонт означал «запись протухла всегда»
    # (prune её выбрасывал, _freshness давал 0.0), то есть оценка модели молча
    # пропадала; в чужом читателе фона тот же NaN мог дать и обратное. Явная
    # проверка на конечность вместо надежды на кламп.
    if not math.isfinite(horizon):
        horizon = 12.0
    horizon = _clamp(horizon, MIN_HORIZON_H, MAX_HORIZON_H)

    syms = rec.get("symbols") or []
    if not isinstance(syms, list):
        return None, "symbols должен быть списком"
    try:
        syms = [s for s in (str(x).upper() for x in syms) if s in BETA]
    except Exception:
        return None, "symbols содержит нечитаемое значение"

    published = rec.get("published_ms")
    try:
        published = int(published) if published is not None else _now_ms()
    except Exception:
        published = _now_ms()
    # новость «из будущего» — почти всегда сбой парсинга даты; не доверяем
    published = min(published, _now_ms())

    try:
        conf = int(rec.get("confirmations", 1))
    except Exception:
        conf = 1
    conf = _clamp(conf, 1, len(newsfeed.SOURCES))

    try:
        market_wide = bool(rec.get("market_wide", not syms))
    except Exception:
        market_wide = not syms

    try:
        item_id = newsfeed.item_id(url, title)
    except Exception:
        return None, "не удалось посчитать id записи"

    return dict(
        id=item_id, url=url, title=title[:300],
        domain=domain, category=cat, weight=round(weight, 4),
        direction=direction, horizon_h=round(horizon, 2), symbols=syms,
        market_wide=market_wide,
        confirmations=conf, published_ms=published, scored_ms=_now_ms(),
        rationale=_text(rec.get("rationale", ""), limit=300)), None


def upsert(records, path=None):
    """Добавляет/обновляет оценённые новости. Возвращает (принято, отказы).

    Отказ — это всегда возвращаемое значение, а не исключение: на том конце
    MCP-инструмент, и он должен ответить модели «запись не принята и почему»,
    а не оборваться. Поэтому и негодный список записей, и негодный путь дают
    пустой приём с причиной в отказах.
    """
    path = _as_path(path)
    if path is _NOT_A_PATH:
        return [], [dict(record="", reason="негодный путь к файлу состояния")]
    st = load(path)
    by_id = {}
    # ключи берём только строковые: id из чужого или правленого файла может
    # оказаться списком, а список нельзя положить в ключ словаря — приём
    # новых новостей упал бы на разборе СТАРОГО файла
    try:
        old_items = list(st.get("items", []) or [])
    except Exception:
        old_items = []
    for r in old_items:
        if isinstance(r, dict) and isinstance(r.get("id"), str):
            by_id[r["id"]] = r
    accepted, rejected = [], []
    try:
        it = iter(records)
    except TypeError:
        return [], [dict(record="", reason="records не последовательность")]
    for rec in it:
        clean, why = sanitize(rec)
        if clean is None:
            rejected.append(dict(record=_text(rec, limit=160,
                                              default=f"<{type(rec).__name__}>"),
                                 reason=why))
            continue
        by_id[clean["id"]] = clean
        accepted.append(clean)
    # scored_ms берём через _num: в старом файле он мог оказаться строкой,
    # и тогда сортировка падала бы на сравнении str с int
    items = sorted(by_id.values(), key=lambda r: _num(r.get("scored_ms")),
                   reverse=True)
    st["items"] = prune(items)[:MAX_ITEMS]
    save(st, path)
    return accepted, rejected


def prune(items, now_ms=None):
    """Выбрасывает записи, у которых истёк собственный горизонт."""
    now = _num(now_ms, 0.0) or _now_ms()
    out = []
    try:
        it = iter(items)
    except TypeError:
        return out             # не последовательность — значит записей нет
    for r in it:
        try:
            age_h = (now - int(r["published_ms"])) / 3600000.0
            if age_h <= float(r["horizon_h"]):
                out.append(r)
        except Exception:
            # запись без нужных полей, с нечисловым или NaN-горизонтом —
            # это «новости нет», а не повод оборвать разбор остальных
            continue
    return out


def _freshness(age_h, horizon_h):
    """Экспоненциальное затухание с полураспадом = половина горизонта.

    В момент публикации 1.0, к концу горизонта ~0.25. Резкого обрыва нет —
    иначе бот дёргался бы на границе окна.
    """
    if age_h <= 0:
        return 1.0
    half = max(0.5, horizon_h / 2.0)
    return 0.5 ** (age_h / half)


def relevance(rec, symbol):
    """Насколько новость относится к этой монете: 1.0 — названа прямо,
    иначе бета к BTC для общерыночных, 0 — не относится.

    Запись приходит из json на диске, а symbol — от вызывающего. Обе стороны
    могут оказаться не тем, чем ожидается (замер фаззера: `symbol in
    rec["symbols"]` при symbols-числе давал TypeError, BETA.get на списке —
    тоже, потому что список нельзя искать в словаре). «Новость к монете не
    относится» — честный ответ на такой вход, исключение — нет.
    """
    if not isinstance(rec, dict):
        return 0.0
    syms = rec.get("symbols")
    try:
        if isinstance(syms, (list, tuple, set, dict)) and symbol in syms:
            return 1.0
    except TypeError:
        pass                   # symbol нехешируем — искать его в set/dict нечем
    if rec.get("market_wide"):
        try:
            return BETA.get(symbol, DEFAULT_BETA)
        except TypeError:
            return DEFAULT_BETA
    return 0.0


def background(symbol, path=None, now_ms=None):
    """Свёртка фона для монеты -> dict(index, heat, n, stale, top).

    index ∈ [-1..1], heat ∈ [0..1]. Если файла нет, он протух или пуст —
    возвращается нулевой фон: бот в этом случае работает ровно как раньше.
    """
    # ВСЕ значения записи читаются через _num/_text, а не по ключу напрямую.
    # Файл фона — обычный json на диске: его правят руками, копируют между
    # машинами и читает сайт на каждый запрос страницы. Отсутствующий ключ или
    # строка вместо веса обязаны означать «этой новости нет», а не KeyError на
    # странице (500) и не исключение в цикле сопровождения позиции.
    now = _num(now_ms, 0.0) or _now_ms()
    st = load(path)
    # int, а не float: это метка времени в мс, её показывают сайт и MCP
    updated = int(_num(st.get("updated_ms"), 0.0))
    stale = (now - updated) / 3600000.0 > STATE_MAX_AGE_H
    items = st.get("items")
    if stale or not items:
        return dict(index=0.0, heat=0.0, n=0, stale=bool(updated) and stale,
                    updated_ms=updated, top=[])

    signed = total = 0.0
    contrib = []
    for r in prune(items, now):
        if not isinstance(r, dict):
            continue
        rel = relevance(r, symbol)
        if rel <= 0:
            continue
        age_h = (now - _num(r.get("published_ms"))) / 3600000.0
        fresh = _freshness(age_h, _num(r.get("horizon_h"), 12.0))
        # подтверждение независимыми изданиями: +15% за каждое сверх первого,
        # но не более +30% — три источника уже не втрое убедительнее одного
        conf_k = 1.0 + min(0.30, 0.15 * (_num(r.get("confirmations"), 1.0) - 1))
        w_eff = _num(r.get("weight")) * rel * fresh * conf_k
        # именно `not (w_eff > 0)`, а не `w_eff <= 0`: NaN ложен в обоих
        # сравнениях, и при второй записи он проскочил бы в сумму фона
        if not (w_eff > 0):
            continue
        signed += _num(r.get("direction")) * w_eff
        total += w_eff
        contrib.append((w_eff, r))

    index = _clamp(signed / SAT, -1.0, 1.0)
    heat = _clamp(total / SAT, 0.0, 1.0)
    contrib.sort(key=lambda x: -x[0])
    # direction именно int: main() печатает его как "%+d", и float там даёт
    # ValueError. Прежде это был int из sanitize, и форма ответа background
    # для сайта и MCP не должна была измениться от одной лишь защиты чтения
    top = [dict(title=_text(r.get("title", ""), limit=120),
                category=_text(r.get("category", "")),
                direction=int(_num(r.get("direction"))),
                weight=_num(r.get("weight")),
                effective=round(w, 4), url=_text(r.get("url", "")))
           for w, r in contrib[:5]]
    return dict(index=round(index, 4), heat=round(heat, 4), n=len(contrib),
                stale=False, updated_ms=updated, top=top)


# --- влияние фона на сделку (единственная точка, где фон трогает торговлю) ---

def _bg_num(bg, key):
    """Число из фона или 0.0 — «фона нет» вместо исключения в пути сделки.

    Три функции ниже — единственное место, где фон трогает торговлю, и звать
    их предстоит из цикла сопровождения позиции. Сам фон приходит из json,
    который пишет MCP-сервер: неполная, чужая или недописанная запись должна
    означать ровно «влияния нет» — то же самое, что пустой фон, — а не
    KeyError посреди сопровождения открытой сделки. Фаззинг по типам ловил
    здесь и KeyError (bg без ключей), и TypeError (нечисловые значения).

    Коэффициенты k/heat_max/index_min намеренно НЕ подстраховываются: они
    приходят из config.py, то есть от нас самих, и опечатка в них должна
    падать громко, а не молча отключать реакцию.

    Перечень «ожидаемых» типов исключений здесь не годится, и это подтверждено
    запуском, а не предположением: float() на целом из 400 цифр даёт
    OverflowError, которого в прежнем перечне (AttributeError, TypeError,
    ValueError) не было, а такое целое json кладёт в файл фона совершенно
    законно. Ловится Exception целиком, BaseException — нет.
    """
    try:
        v = bg.get(key, 0.0)
    except Exception:
        return 0.0            # не словарь и вообще не отображение — фона нет
    return _num(v, 0.0)


def tp_multiplier(bg, side, k):
    """Множитель к тейку. k — сила реакции (0 = фон не влияет вообще).

    Попутный фон растягивает тейк (дать прибыли пробежать), встречный —
    ужимает (забрать раньше). Всё в пределах MAX_TP_STRETCH/MAX_TP_SHRINK.
    """
    if not k:
        return 1.0
    sgn = 1.0 if side == "L" else -1.0
    aligned = _clamp(_bg_num(bg, "index") * sgn,
                     -1.0, 1.0)                      # +1 попутный, -1 встречный
    if aligned >= 0:
        mult = 1.0 + k * MAX_TP_STRETCH * aligned
    else:
        mult = 1.0 + k * MAX_TP_SHRINK * aligned     # aligned<0 -> сжатие
    # Итог клампится так же, как в sl_tighten_fraction: k — это число из
    # config.py, а не из новости, и опечатка news_tp_k=5 не должна пробивать
    # задокументированные +15%/-30%. Без клампа k>1 растягивал тейк дальше
    # потолка (а k>3.3 при встречном фоне давал вовсе отрицательный тейк).
    return _clamp(mult, 1.0 - MAX_TP_SHRINK, 1.0 + MAX_TP_STRETCH)


def sl_tighten_fraction(bg, side, k):
    """Доля пути от стопа К ВХОДУ, на которую подтягиваем стоп (0..1).

    Только подтягивание: раздвинуть стоп фон не может — это увеличивало бы
    убыток по подсказке из недоверенного текста. Реагируем на встречный фон
    и на общий накал.
    """
    if not k:
        return 0.0
    sgn = 1.0 if side == "L" else -1.0
    against = max(0.0, -_clamp(_bg_num(bg, "index") * sgn, -1.0, 1.0))
    drive = max(against, 0.5 * _bg_num(bg, "heat"))
    return _clamp(k * MAX_SL_TIGHTEN * drive, 0.0, MAX_SL_TIGHTEN)


def entry_veto(bg, side, heat_max, index_min):
    """(вето?, причина). heat_max=0/index_min=0 -> фон входы не запрещает."""
    heat = _bg_num(bg, "heat")
    if heat_max and heat > heat_max:
        return True, f"накал новостей {heat:.2f} > {heat_max:.2f}"
    if index_min:
        sgn = 1.0 if side == "L" else -1.0
        aligned = _bg_num(bg, "index") * sgn
        if aligned < -abs(index_min):
            return True, (f"встречный новостной фон {aligned:+.2f} < "
                          f"{-abs(index_min):+.2f}")
    return False, ""


def main():
    for sym in BETA:
        bg = background(sym)
        print(f"{sym:<9} index {bg['index']:+.3f} | heat {bg['heat']:.3f} | "
              f"новостей {bg['n']}" + (" | ФОН ПРОТУХ" if bg["stale"] else ""))
        for t in bg["top"][:2]:
            print(f"           {t['direction']:+d} w={t['weight']:.2f} "
                  f"эфф {t['effective']:.3f} [{t['category']}] {t['title'][:70]}")


if __name__ == "__main__":
    main()
