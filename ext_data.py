# -*- coding: utf-8 -*-
"""Внешние данные для v4: funding, открытый интерес, SPX, DXY, золото.
Всё кэшируется в json. Значения дневных рядов берутся С ЛАГОМ (вчерашний
клоуз) — без заглядывания в будущее.

ГЛУБИНА РЯДОВ НЕ ОДИНАКОВА, и это важно при чтении любых цифр отбора.
Замер кэшей на 14.08.2026 (`python ext_data.py`):

    funding  — с 2023-02-25 (3800 точек по 8ч), окно закрывает целиком;
    OI       — с 2024-10-16 (16000 часов ≈ 667 дней): дальше обрывается
               пагинация в fetch_oi (80 страниц × 200 точек);
    SPX/DXY  — с 2022-08-23 (yfinance period="4y"), окно закрывают целиком;
    золото   — с 2025-04-09: раньше на Bybit просто не было пары XAUTUSDT.

Замер покрытия на боевом окне (15m, 1150 дней = 3.15 года, 110399 свечей,
2023-06-21 -> 2026-08-14, замер 15.08.2026, все пять монет дали одно и то
же с точностью до десятой):

    ряд        всё окно   обучение+валидация   экзамен
    fund         100.0%               100.0%    100.0%
    spx5         100.0%               100.0%    100.0%
    dxy5         100.0%               100.0%    100.0%
    oi_chg        57.9%                46.1%    100.0%
    gold5         42.8%                26.8%    100.0%

Граница обучения и экзамена — бар 86249 (78.1%, 2025-12-06), по
evolution4.train_end_bar; отбор генов видел только то, что левее.

Отсюда: гены `oi_gate` и `gold_long_max` физически НЕ МОГЛИ отбираться
«на 3.2 годах истории». На той части окна, где отбор вообще происходил,
ряд OI был пуст на 53.9% свечей, а золото — на 73.2%, и соответствующий
фильтр там не применялся вовсе (в коде отбора это ветки `if o is not None`
и `if au is not None` внутри evolution4.make_filter4 — «данных нет» там
читается как «фильтр не возразил»). На экзаменационном окне оба ряда полны,
поэтому экзамен эти гены как раз проверяет — а вот выбирались они вслепую.
Чтобы это не оставалось незаметным, build_aux4 пишет в лог фактическое
покрытие каждого ряда (см. aux_coverage).

Все числа этой таблицы сверяет с кэшами test_ext_data.py — и сверяет теперь
на самом деле: до 15.08.2026 та проверка стояла после сценария «источник
мёртв», не сбрасывала паузу перед повтором и потому считала покрытие по
пустым рядам. Тест падал, числа никем не подтверждались. Пауза сброшена,
проверка проходит, замер повторён на всех пяти монетах — совпало до сотых.

КОНТРАКТ ПРИ НЕДОСТУПНОМ ИСТОЧНИКЕ (важно: ни одна fetch_* НЕ БРОСАЕТ наружу).
Отсутствие данных не должно выглядеть как «фильтр разрешил вход» — но и ронять
вызывающего оно не должно: fetch_* зовут 18 модулей репозитория (замер
15.08.2026 по grep), в том числе webapp/app.py, где необработанное исключение —
это 500 на странице. Пустой ответ источника: (1) НЕ кэшируется, чтобы пустышка
не замерзала на сутки; (2) пишет строку ERROR в лог; (3) возвращается ПУСТЫМ
рядом — у fetch_daily_pct5 это
MacroSeries с available=False. MacroUnavailable осталось внутренним сигналом
между _cache и fetch_* и за границу модуля не выходит.

«Источник недоступен» — это не только сеть. Кэш на диске правят руками и
удаляют соседние скрипты, поэтому неожиданная ФОРМА кэша (не тот тип ключа,
строка вместо числа, список вместо словаря) обязана давать ровно то же
самое — пустой ряд и ERROR, а не исключение. За это отвечают проверки формы
в is_valid каждого ряда (у funding и OI — _is_series: непустой список
числовых пар; у макро — своя is_valid внутри fetch_daily_pct5) плюс
страховка вокруг преобразования ключей в
fetch_daily_pct5. Страховка ловит Exception целиком: перечень «ожидаемых»
типов оставлял дыры (ключ float('inf') давал OverflowError мимо всех
обработчиков), а обещание «не бросаем» не может держаться на полноте
такого перечня. BaseException не ловится — Ctrl+C должен работать.

Наоборот тоже верно: годный кэш нельзя выбрасывать заранее. Обновлять ряд
следует через max_age_s (см. _cache), а не удаляя файл перед вызовом:
удаление и правило «пустышку не кэшируем» гасят друг друга, и попытка
обновиться в момент молчания источника оставляет фильтры вовсе без данных.
Отданная с диска просроченная копия при этом НЕ считается ответом источника:
пауза RETRY_PAUSE_S заводится и в этом случае, иначе просроченный кэш гнал бы
в молчащий источник по обращению на каждый запрос страницы сайта.

Что при этом происходит в бою, без прикрас: bot_rsi.py получит пустые ряды,
положит в свой суточный кэш None по всем трём макро-фильтрам (метод
RsiGridBot.get_macro) и до конца суток торгует БЕЗ них. Единственный
след — ERROR ext_data в логе бота за этот день. Остановить торговлю модуль внешних данных
не может и не пытается: он поставщик данных, а не риск-менеджер."""

import json
import logging
import math
import os
import tempfile
import time

from pybit.unified_trading import HTTP

log = logging.getLogger("ext_data")


class MacroUnavailable(RuntimeError):
    """ВНУТРЕННИЙ сигнал «ряд пуст»: от _cache и билдеров к fetch_*.

    Наружу не выходит. Так сделано намеренно: из 18 вызывающих исключение
    ловит РОВНО ОДИН (bot_rsi.py, метод get_macro) — остальные семнадцать,
    скрипты бэктестов и webapp/app.py, зовут fetch_* без всякого try и
    падали бы, причём на сайте это 500 на странице бота.
    Различать «данных нет» и «фильтр разрешил» вызывающему теперь позволяет
    возвращаемое значение (MacroSeries.available и пустой ряд), а не тип
    исключения.
    """


class MacroSeries(dict):
    """{'spx': {...}, 'dxy': {...}, 'gold': {...}} + признак доступности.

    Остаётся обычным dict, поэтому все прежние вызывающие продолжают писать
    pct5["spx"].get(day) и ничего про этот класс знать не обязаны. Разница
    одна: у пустышки available=False и в reason лежит причина. Тому, кто
    хочет отличить «фильтр отработал» от «данных не было вовсе», хватает
    этого атрибута — и не нужен try/except вокруг каждого вызова.
    """

    __slots__ = ("available", "reason")

    def __init__(self, mapping=None, available=True, reason=""):
        super().__init__(mapping if mapping is not None
                         else {"spx": {}, "dxy": {}, "gold": {}})
        self.available = available
        self.reason = reason


def macro_available(pct5):
    """True, если макро-ряды действительно получены, а не пустышка.

    Предикат обязан ОТВЕЧАТЬ, а не бросать: его зовут ровно затем, чтобы
    выяснить, годится ли объект в дело. Поэтому чужой тип (None из чьей-то
    переменной, список из битого кэша) — это честное «нет», а не
    AttributeError у вызывающего, который спрашивал про доступность данных.
    """
    if not isinstance(pct5, dict):
        return False
    return (bool(getattr(pct5, "available", True))
            and all(pct5.get(k) for k in ("spx", "dxy", "gold")))


def _read_json(path, attempts=5):
    """Чтение кэша, устойчивое к одновременной записи другим ботом.

    На Windows os.replace на стороне писателя может на миг сделать файл
    недоступным читателю (PermissionError) — 5 ботов делят один файл.
    Короткая пауза и повтор дешевле, чем разовый сбой фильтра.
    """
    for i in range(attempts):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (PermissionError, json.JSONDecodeError) as e:
            if i == attempts - 1:
                raise
            log.warning("Кэш %s занят или недочитан (%s) — повтор %d/%d",
                        path, type(e).__name__, i + 1, attempts - 1)
            time.sleep(0.15 * (i + 1))


def _write_json(path, data, attempts=5):
    """Атомарная запись кэша: сначала во временный файл, потом os.replace.

    Запись «на месте» рвалась пополам, если в этот момент читал другой бот,
    и оставляла битый json. os.replace подменяет файл целиком: читатель
    видит либо старую версию, либо новую. Сам replace на Windows тоже может
    упереться в открытый читателем файл — отсюда повторы.
    """
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + ".",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        for i in range(attempts):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if i == attempts - 1:
                    raise
                log.warning("Кэш %s занят другим ботом — повтор записи %d/%d",
                            path, i + 1, attempts - 1)
                time.sleep(0.15 * (i + 1))
    except BaseException:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def _disk_copy(path, is_valid=None):
    """Годные данные с диска БЕЗ единого обращения к источнику (или None).

    Нужна на время паузы повторных попыток: источник трогать нельзя, но
    лежащая рядом просроченная копия всё равно полезнее пустого ряда —
    пустота выключает фильтры целиком, а суточной давности макро нет.
    Читает молча: здесь любая беда с файлом означает ровно «запасного
    варианта нет», а громкую строку в лог пишет вызывающий.
    """
    if not os.path.exists(path):
        return None
    try:
        data = _read_json(path)
    except Exception:
        return None
    if is_valid is not None and not is_valid(data):
        return None
    return data


def _cache(path, builder, is_valid=None, max_age_s=None):
    """Кэш в json. is_valid — проверка «данные есть и они нужной ФОРМЫ».

    Возвращает ПАРУ (данные, причина_отката). Вторая половина пуста, когда
    данные настоящие: только что от источника или из ещё свежего кэша. Она
    непуста ровно в одном случае — источник не ответил и отдана прежняя
    копия с диска. Без этого признака _fetch_series принимал такой откат за
    успех и СБРАСЫВАЛ паузу повторных попыток: кэш-то остаётся просроченным,
    значит следующий же вызов снова шёл в молчащий источник, и пауза на
    пути max_age_s не работала вовсе (замер: 5 вызовов подряд = 5 обращений
    к мёртвому источнику).

    Пустой результат не кэшируется НИКОГДА: иначе один сбой источника
    замораживал бы пустышку на сутки. Уже лежащий пустой кэш тоже не
    принимается — он перестраивается с записью в лог.

    max_age_s — «данные старше этого пора обновить». По умолчанию None:
    кэш не протухает никогда, и все прежние вызывающие получают ровно
    прежнее поведение. Смысл параметра в том, чтобы обновление не требовало
    УДАЛЯТЬ файл заранее: удаление необратимо, и если источник в этот момент
    молчит, то вместо суточной давности данных не остаётся никаких. Поэтому
    просроченный, но годный кэш держится запасным вариантом и отдаётся, если
    источник не ответил. Староватое макро (дневные ряды!) заведомо полезнее
    пустоты: пустота выключает фильтры целиком.

    MacroUnavailable отсюда — внутренний сигнал для _fetch_series, который
    и превращает его в пустой ряд. Вызывать _cache напрямую, минуя
    _fetch_series, значит вернуть исключение вызывающим, которые к нему не
    готовы (сайт — 500).
    """
    stale = None
    if os.path.exists(path):
        data = _read_json(path)
        if is_valid is None or is_valid(data):
            age = time.time() - os.path.getmtime(path)
            if max_age_s is None or age <= max_age_s:
                return data, ""
            stale = data
        else:
            log.warning("Кэш %s пуст или неожиданной формы — перестраиваем, "
                        "а не используем как есть", path)
    try:
        data = builder()
    except Exception as e:
        if stale is None:
            raise
        # источник молчит, но на диске есть годные данные вчерашней свежести
        log.error("Ряд %s просрочен, а источник не ответил (%s) — работаем на "
                  "прежних данных с диска, а не выключаем фильтры", path, e)
        return stale, f"{type(e).__name__}: {e}"
    if is_valid is not None and not is_valid(data):
        if stale is not None:
            log.error("Ряд %s просрочен, а источник вернул пустые данные — "
                      "работаем на прежних данных с диска", path)
            return stale, "источник вернул пустые данные"
        # намеренно НЕ пишем файл: пусть следующий вызов попробует снова
        raise MacroUnavailable(
            f"источник вернул пустые данные для {path} — кэш не обновлён, "
            "зависящие фильтры НЕ ПРИМЕНЯЛИСЬ")
    _write_json(path, data)
    return data, ""


# Пауза перед повторным обращением к отказавшему источнику. Нужна из-за
# сайта: build_sim зовёт fetch_* на КАЖДЫЙ запрос страницы, и без паузы
# Yahoo/Bybit получали бы шквал обращений ровно тогда, когда они и так
# отвечают отказом. 5 минут — данные суточные, потерять от такой задержки
# нечего, а первая же попытка после паузы всё равно случится задолго до
# следующей смены дня.
# Пауза заводится на ЛЮБОЙ отказ источника, включая тот, после которого
# вызывающий всё же получил данные с диска (просроченный кэш как запасной
# вариант): «данные отданы» и «источник жив» — разные вещи, а просроченным
# кэш остаётся до первого удачного ответа, так что без паузы каждый вызов
# снова шёл бы в молчащий источник.
RETRY_PAUSE_S = 300
_retry_after = {}   # ключ ряда -> время, раньше которого источник не трогаем
_last_reason = {}   # ключ ряда -> почему он в прошлый раз не ответил


def _fetch_series(key, path, builder, is_valid, empty, max_age_s=None):
    """Общая обёртка fetch_*: вернуть данные ЛИБО пустой ряд, но не упасть.

    Единственное место, где «источник молчит» превращается из исключения в
    значение. Громкость обеспечивается логом (ERROR на каждый вызов, а при
    неожиданной ошибке — с трассировкой), а не падением вызывающего: падать
    здесь означало бы ронять страницу сайта и скрипты, которые к исключению
    не готовы.
    """
    now = time.time()
    wait = _retry_after.get(key, 0.0) - now
    if wait > 0:
        reason = _last_reason.get(key, "источник молчит")
        # пауза запрещает трогать ИСТОЧНИК, но не диск: если прежняя копия
        # ряда на месте, отдать её честнее, чем выключить фильтры на 5 минут
        held = _disk_copy(path, is_valid)
        if held is not None:
            log.error("Ряд %s: источник не отвечает (%s), следующая попытка "
                      "через %.0f с — отдаём прежние данные с диска",
                      key, reason, wait)
            return held
        log.error("Ряд %s НЕДОСТУПЕН (%s) — зависящие фильтры НЕ ПРИМЕНЯЮТСЯ; "
                  "следующая попытка источника через %.0f с", key, reason, wait)
        return empty(reason)
    try:
        data, fallback = _cache(path, builder, is_valid, max_age_s)
    except MacroUnavailable as e:
        reason = str(e)
    except Exception as e:
        # сеть, таймаут, смена формата ответа — для вызывающего это то же
        # самое «данных нет»; трассировку печатаем, чтобы сбой источника не
        # спутать с ошибкой в коде билдера
        log.exception("Ряд %s: сбой при загрузке", key)
        reason = f"{type(e).__name__}: {e}"
    else:
        if not fallback:
            _retry_after.pop(key, None)
            _last_reason.pop(key, None)
            return data
        # данные есть, но взяты с диска, потому что источник молчал: это НЕ
        # успех источника — заводим паузу, иначе следующий вызов (а на сайте
        # это следующий запрос страницы) снова пойдёт в тот же молчащий Yahoo
        _retry_after[key] = now + RETRY_PAUSE_S
        _last_reason[key] = fallback
        log.error("Ряд %s: источник не ответил (%s) — отданы прежние данные с "
                  "диска, следующая попытка не раньше чем через %d с",
                  key, fallback, RETRY_PAUSE_S)
        return data
    _retry_after[key] = now + RETRY_PAUSE_S
    _last_reason[key] = reason
    log.error("Ряд %s НЕДОСТУПЕН (%s) — зависящие фильтры НЕ ПРИМЕНЯЮТСЯ, "
              "кэш намеренно не обновлён", key, reason)
    return empty(reason)


def _is_series(rows):
    """Форма ряда funding/OI: непустой список числовых пар (ts, значение).

    Раньше здесь стоял просто bool, то есть проверялось только «непусто», а
    шапка модуля обещала проверку ФОРМЫ. Обещание было не пустой придиркой:
    funding_*.json и oi_*.json — обычные файлы на диске, их правят руками и
    перезаписывают соседние скрипты, а дальше ряд уходит в step_lookup и
    dict(oi). Замер на кэше-словаре {"1677283200000": 0.01}: bool считал его
    годным, fetch_funding возвращал словарь, и step_lookup бросал наружу
    ValueError («too many values to unpack») — ровно то исключение, которого
    контракт модуля обещает не допускать. Теперь такая форма читается как
    «кэш негоден, перестрой» — как и любой другой пустой ответ источника.

    NaN отсеивается намеренно: json.load его принимает (json.loads("[[NaN,1]]")
    проходит), а в bisect по ts он ломает порядок молча.

    Проверяется ВЕСЬ ряд, а не первая пара: битой обычно оказывается не
    начало файла. Это недорого — замер 15.08.2026 на боевых кэшах: 5.0 мс на
    oi_*.json (16000 точек) и 1.2 мс на funding_*.json (3800 точек) за одну
    проверку, против сетевой выгрузки в десятки секунд.
    """
    if not isinstance(rows, list) or not rows:
        return False
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) != 2:
            return False
        ts, val = r
        # bool — подкласс int, но ряд из True/False это не данные
        if isinstance(ts, bool) or isinstance(val, bool):
            return False
        if not isinstance(ts, (int, float)) or not isinstance(val, (int, float)):
            return False
        if not math.isfinite(ts) or not math.isfinite(val):
            return False
    return True


def fetch_funding(symbol, days=1200):
    """[(ts_ms, rate_%за8ч)] по возрастанию."""
    def build():
        s = HTTP(testnet=False)
        start = int(time.time() * 1000) - days * 86400 * 1000
        end = int(time.time() * 1000)
        out = []
        for _ in range(80):
            r = s.get_funding_rate_history(category="linear", symbol=symbol,
                                           limit=200, endTime=end)
            rows = r["result"]["list"]
            if not rows:
                break
            out += [(int(x["fundingRateTimestamp"]),
                     float(x["fundingRate"]) * 100) for x in rows]
            oldest = int(rows[-1]["fundingRateTimestamp"])
            if oldest <= start or oldest >= end:
                break
            end = oldest - 1
            time.sleep(0.12)
        return sorted(set(out))
    # пустой ответ биржи не кэшируем: файл кэша funding/oi не протухает
    # никогда, и одна неудачная выгрузка навсегда выключила бы фильтр.
    # Пустой список для build_aux4 значит «funding неизвестен» -> fund=None
    # на всех свечах, то есть фильтр не применяется, и это видно в логе
    # покрытия рядов
    return _fetch_series(f"funding {symbol}", f"funding_{symbol}.json",
                         build, _is_series, lambda why: [])


def fetch_oi(symbol):
    """[(ts_ms, oi)] 1h по возрастанию.

    Глубина — 80 страниц по 200 точек = 16000 часов ≈ 667 дней, дальше
    выборка просто обрывается. Фактически в кэшах данные с 2024-10-16
    (замер 15.08.2026) — это 57.9% свечей окна бэктеста в 1150 дней, а на
    той части окна, по которой шёл ОТБОР, всего 46.1%. На остальных свечах
    oi_chg = None и ген oi_gate не срабатывает ни разу: «отобран на 3.2
    годах» про него сказать нельзя. Сейчас ген включён у BTC, LTC и DOGE
    (config.SYMBOL_PARAMS[...]['final']['oi_gate']), у ETH и SOL — выкл.
    """
    def build():
        s = HTTP(testnet=False)
        out, cur = [], None
        for _ in range(80):
            kw = dict(category="linear", symbol=symbol,
                      intervalTime="1h", limit=200)
            if cur:
                kw["cursor"] = cur
            r = s.get_open_interest(**kw)
            rows = r["result"]["list"]
            if not rows:
                break
            out += [(int(x["timestamp"]), float(x["openInterest"]))
                    for x in rows]
            cur = r["result"].get("nextPageCursor")
            if not cur:
                break
            time.sleep(0.12)
        return sorted(set(out))
    return _fetch_series(f"OI {symbol}", f"oi_{symbol}.json",
                         build, _is_series, lambda why: [])


def fetch_daily_pct5(max_age_s=None):
    """{'spx': {day_ts: 5д-изм.%}, 'dxy': ..., 'gold': ...} на ВЧЕРАШНИХ данных.

    max_age_s — «кэш старше этого пора обновить» (None = не протухает,
    прежнее поведение всех вызывающих). Существует ради bot_rsi.py: тому
    нужны свежие суточные данные, и он добивался этого, УДАЛЯЯ daily_pct5.json
    перед вызовом. Удаление и защита «пустышку не кэшируем» друг друга
    гасят — единственная годная копия исчезает ровно тогда, когда источник
    молчит. С max_age_s обновление происходит без удаления, а если источник
    не ответил, отдаётся вчерашняя копия с диска (см. _cache).

    Глубина рядов разная: SPX и DXY — с 2022-08-23 (yfinance period="4y"),
    окно бэктеста закрывают полностью (100.0% свечей); золото — только с
    2025-04-09, когда пара XAUTUSDT появилась на Bybit. Ген gold_long_max
    поэтому работал на 42.8% свечей окна (492 дня из 1150), а на обучающей
    части, где он и выбирался, — всего на 26.8%. На остальных свечах он не
    применялся вовсе. Ген включён у BTC, DOGE и SOL; у LTC он на потолке
    6.0 (= выкл), у ETH макро-генов в финале нет.

    ИСКЛЮЧЕНИЙ НЕ БРОСАЕТ (см. контракт в шапке модуля). Если хоть один ряд
    пуст — а именно так выглядит сбой Yahoo: yf.download при ошибке или
    лимите отдаёт пустой DataFrame без исключения, — то в лог уходит ERROR,
    пустышка НЕ сохраняется в daily_pct5.json, а наружу возвращается
    MacroSeries с available=False и пустыми рядами.

    Для вызывающего это означает: pct5["spx"] и остальные ряды пусты, все
    макро-фильтры лонгов до восстановления источника НЕ ПРИМЕНЯЮТСЯ. Кому
    важно отличить это от «фильтр отработал» — смотрит macro_available(pct5).
    """
    def build():
        out = {}
        import yfinance as yf
        for key, tick in (("spx", "^GSPC"), ("dxy", "DX-Y.NYB")):
            df = yf.download(tick, period="4y", interval="1d", progress=False)
            # пустой ответ Yahoo — штатная ситуация (лимит, таймаут, смена
            # тикера), и её надо заметить здесь, а не растворить в None
            if df is None or df.empty or len(df) < 7:
                n = 0 if df is None else len(df)
                log.error("Yahoo не отдал ряд %s (%s): строк %d — "
                          "макро-фильтры лонгов НЕ ПРИМЕНЯЮТСЯ", key, tick, n)
                raise MacroUnavailable(f"пустой ответ Yahoo по {tick}")
            closes = [float(x) for x in df["Close"].values.ravel()]
            days = [int(t.timestamp()) for t in df.index]
            m = {}
            for i in range(6, len(closes)):
                # известно на день days[i]+1 и позже: изменение close[i-1]/close[i-6]
                val = (closes[i - 1] / closes[i - 6] - 1) * 100
                m[days[i]] = val
            out[key] = m
        s = HTTP(testnet=False)
        r = s.get_kline(category="linear", symbol="XAUTUSDT",
                        interval="D", limit=1000)
        rows = sorted((int(x[0]), float(x[4])) for x in r["result"]["list"])
        if len(rows) < 7:
            log.error("Bybit не отдал дневные свечи XAUTUSDT: строк %d — "
                      "фильтр золота НЕ ПРИМЕНЯЕТСЯ", len(rows))
            raise MacroUnavailable("пустой ответ Bybit по XAUTUSDT")
        m = {}
        for i in range(6, len(rows)):
            val = (rows[i - 1][1] / rows[i - 6][1] - 1) * 100
            m[rows[i][0] // 1000] = val
        out["gold"] = m
        return out

    def is_valid(raw):
        # Проверяется и наполнение, и ФОРМА. Ряд без точек = фильтр молча
        # выключен; такое не кэшируем. Форма важна не меньше: ниже ключи
        # уходят в int(), а daily_pct5.json — обычный файл на диске, который
        # правят руками и удаляют соседние скрипты. Нечисловой ключ или
        # список вместо словаря должны читаться как «кэш негоден, перестрой»,
        # а не как исключение из fetch_daily_pct5 (на сайте это 500).
        if not isinstance(raw, dict):
            return False
        for k in ("spx", "dxy", "gold"):
            m = raw.get(k)
            if not isinstance(m, dict) or not m:
                return False
            for d, v in m.items():
                try:
                    int(d)
                    float(v)
                except (TypeError, ValueError):
                    return False
        return True

    raw = _fetch_series(
        "макро spx/dxy/gold", "daily_pct5.json", build, is_valid,
        lambda why: MacroSeries(available=False, reason=why), max_age_s)
    if not getattr(raw, "available", True):
        return raw          # пустые ряды с причиной, ключи на месте
    try:
        return MacroSeries({k: {int(d): float(v) for d, v in m.items()}
                            for k, m in raw.items()})
    except Exception as e:
        # Страховка на случай, если ряд придёт мимо is_valid (например,
        # _fetch_series подменён в тесте или в кэш попала форма, которой мы
        # не предусмотрели). Контракт «fetch_* не бросает наружу» не должен
        # зависеть от того, всё ли предусмотрел is_valid.
        #
        # Ловим Exception целиком, а не перечень из трёх типов: перечень
        # обещание «не бросаем» не выполнял. Живой контрпример — ключ
        # float('inf') (int(inf) даёт OverflowError, которого в списке не
        # было), и любой объект с собственным __int__/__float__ может бросить
        # что угодно ещё. Контракт, выполняющийся только для заранее
        # перечисленных типов, — это не контракт. BaseException не трогаем
        # намеренно: Ctrl+C и SystemExit глушить нельзя.
        log.exception("Кэш daily_pct5.json неожиданной формы — "
                      "макро-фильтры НЕ ПРИМЕНЯЮТСЯ")
        return MacroSeries(
            available=False,
            reason=f"негодная форма кэша: {type(e).__name__}: {e}")


def step_lookup(series):
    """series [(ts,val)] -> функция ts->последнее известное значение."""
    import bisect
    ts_list = [t for t, _ in series]
    vals = [v for _, v in series]

    def get(ts):
        i = bisect.bisect_right(ts_list, ts) - 1
        return vals[i] if i >= 0 else None
    return get


def aux_coverage(aux):
    """{имя_ряда: доля свечей с данными} — на скольких барах фильтр вообще мог сработать.

    Нужно, чтобы «данных за период нет» не читалось как «фильтр разрешил».
    Ряд с покрытием 0.3 означает, что на 70% окна ген просто не работал, и
    его вклад в результат отбора — вклад константы, а не фильтра.
    """
    out = {}
    for k, arr in aux.items():
        if isinstance(arr, list) and arr:
            out[k] = sum(1 for v in arr if v is not None) / len(arr)
    return out


def build_aux4(candles, funding, oi, pct5, label=""):
    """Массивы v4-сигналов, выровненные по свечам.

    По окончании пишет в лог фактическое покрытие каждого ряда: OI и золото
    короче окна бэктеста (см. модульную докстроку), и без этой строки
    отсутствие данных выглядело бы как обычный «фильтр не сработал».
    """
    fget = step_lookup(funding) if funding else (lambda ts: None)
    oi_map = dict(oi)
    fund = [None] * len(candles)
    oi_chg = [None] * len(candles)
    spx5 = [None] * len(candles)
    dxy5 = [None] * len(candles)
    gold5 = [None] * len(candles)

    def daily(m, day):
        for back in range(6):
            v = m.get(day - back * 86400)
            if v is not None:
                return v
        return None

    for i, c in enumerate(candles):
        ts = c[0]
        fund[i] = fget(ts)
        hour = ts // 3600000 * 3600000
        now, prev = oi_map.get(hour), oi_map.get(hour - 24 * 3600000)
        if now and prev:
            oi_chg[i] = (now / prev - 1) * 100
        day = ts // 1000 // 86400 * 86400
        spx5[i] = daily(pct5["spx"], day)
        dxy5[i] = daily(pct5["dxy"], day)
        gold5[i] = daily(pct5["gold"], day)
    aux = dict(fund=fund, oi_chg=oi_chg, spx5=spx5, dxy5=dxy5, gold5=gold5)
    cov = aux_coverage(aux)
    thin = {k: v for k, v in cov.items() if v < 0.99}
    if thin:
        log.warning("Покрытие рядов%s: %s — на остальных свечах "
                    "соответствующие фильтры НЕ ПРИМЕНЯЛИСЬ",
                    f" ({label})" if label else "",
                    ", ".join(f"{k} {v:.0%}" for k, v in sorted(thin.items())))
    return aux


def describe_cache():
    """Фактическая глубина уже скачанных рядов (по файлам, без сети).

    Существует, чтобы утверждения о глубине данных в докстроках и в docs
    можно было проверить одной командой, а не принимать на веру:
        python ext_data.py

    Раз это инструмент ПРОВЕРКИ утверждений, он обязан пережить любой кэш,
    какой найдёт на диске: иначе первый же файл неожиданной формы обрывает
    отчёт, и остальные ряды остаются непроверенными. Замер до правки:
    кэш-словарь вместо списка пар ронял всю функцию с KeyError: 0 —
    строки по пяти годным файлам после него уже не печатались. Теперь
    негодный файл — это одна строка «НЕГОДНАЯ ФОРМА», ровно то, что о нём
    думает и is_valid соответствующего ряда.
    """
    import datetime
    import glob as _glob

    def day(ts):
        try:
            return datetime.datetime.fromtimestamp(
                ts, datetime.timezone.utc).date().isoformat()
        except Exception:
            # дата вне календаря — не повод обрывать отчёт по остальным рядам
            return f"?({ts})"

    def read(path):
        try:
            return _read_json(path), ""
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"

    for path in sorted(_glob.glob("funding_*.json") + _glob.glob("oi_*.json")):
        rows, err = read(path)
        if err:
            print(f"{path:<26} НЕ ЧИТАЕТСЯ: {err}")
            continue
        if not rows:
            print(f"{path:<26} ПУСТО")
            continue
        if not _is_series(rows):
            print(f"{path:<26} НЕГОДНАЯ ФОРМА — fetch_* перестроит этот кэш")
            continue
        print(f"{path:<26} {len(rows):>6} точек  "
              f"{day(rows[0][0] / 1000)} -> {day(rows[-1][0] / 1000)}")
    if os.path.exists("daily_pct5.json"):
        raw, err = read("daily_pct5.json")
        if err:
            print(f"{'daily_pct5.json':<26} НЕ ЧИТАЕТСЯ: {err}")
            return
        if not isinstance(raw, dict):
            print(f"{'daily_pct5.json':<26} НЕГОДНАЯ ФОРМА "
                  f"({type(raw).__name__} вместо словаря рядов)")
            return
        for key in ("spx", "dxy", "gold"):
            m = raw.get(key)
            if not isinstance(m, dict) or not m:
                print(f"daily_pct5.json [{key}]      ПУСТО — фильтр не применяется")
                continue
            try:
                days = sorted(int(d) for d in m)
            except Exception as e:
                print(f"daily_pct5.json [{key}]      НЕГОДНАЯ ФОРМА: "
                      f"{type(e).__name__} на ключе дня")
                continue
            print(f"daily_pct5.json [{key:<4}] {len(days):>6} дней   "
                  f"{day(days[0])} -> {day(days[-1])}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    describe_cache()
