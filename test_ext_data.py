# -*- coding: utf-8 -*-
"""Проверка контракта ext_data: при мёртвом источнике модуль НЕ БРОСАЕТ.

Зачем отдельный тест. Шапка ext_data.py обещает вызывающему сильную вещь:
«ни одна fetch_* не бросает наружу». На это обещание опирается webapp/app.py
(там исключение = 500 на странице бота) и восемь скриптов бэктестов, которые
никакого try/except вокруг вызова не ставят. Обещание, которое никто не
проверяет запуском, живёт ровно до следующей правки: в прошлой редакции оно
уже нарушалось на преобразовании ключей `int(d)` — битый кэш daily_pct5.json
давал ValueError мимо всех обработчиков.

Сеть здесь не нужна и намеренно заглушена: «источник молчит» изображается
подменой yfinance и pybit.HTTP, а работа идёт во временной папке с копией
кэша, так что боевые json-файлы тест не трогает.

Запуск: python test_ext_data.py
"""

import contextlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import types

REPO = os.path.dirname(os.path.abspath(__file__))
OK = [0]
HITS = [0]      # сколько раз тест «сходил в источник» — считает kill_network


def check(cond, name):
    assert cond, f"ПРОВАЛ: {name}"
    OK[0] += 1
    print(f"  ok  {name}")


def kill_network(xd):
    """Любое обращение к источнику = отказ источника (и оно считается).

    Счётчик HITS нужен для проверки паузы повторных попыток: «данные
    вернулись» и «источник не дёргали» — разные утверждения, и второе можно
    проверить только счётом обращений.
    """
    fake_yf = types.ModuleType("yfinance")

    def dead_download(*a, **k):
        HITS[0] += 1
        raise RuntimeError("сеть отключена в тесте")

    fake_yf.download = dead_download
    sys.modules["yfinance"] = fake_yf

    class DeadHTTP:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            def boom(*a, **k):
                HITS[0] += 1
                raise RuntimeError("биржа недоступна в тесте")
            return boom

    xd.HTTP = DeadHTTP


def write_cache(raw):
    with open("daily_pct5.json", "w", encoding="utf-8") as fh:
        json.dump(raw, fh)


def main():
    work = tempfile.mkdtemp(prefix="test_ext_data_")
    src = os.path.join(REPO, "daily_pct5.json")
    if not os.path.exists(src):
        print("нет daily_pct5.json — тест пропущен")
        return
    shutil.copy(src, work)
    sys.path.insert(0, REPO)
    os.chdir(work)
    import ext_data as xd
    kill_network(xd)

    def retry_off():
        # пауза перед повторным обращением к источнику мешает проверять
        # несколько сценариев подряд — сбрасываем её между случаями
        xd._retry_after.clear()
        xd._last_reason.clear()

    good = xd.fetch_daily_pct5()
    check(xd.macro_available(good), "целый кэш читается: macro_available")
    check(all(isinstance(d, int) for d in good["spx"]),
          "ключи дней приведены к int")
    with open(src, encoding="utf-8") as fh:
        raw0 = json.load(fh)
    check(all(good["gold"][int(d)] == v for d, v in raw0["gold"].items()),
          "значения не изменились ни на бит")

    # дальше — формы кэша, на которых прежний код падал или упал бы
    bad = json.loads(json.dumps(raw0))
    bad["gold"]["не-число"] = 1.0
    write_cache(bad)
    try:
        {int(d): v for d, v in bad["gold"].items()}
        raise AssertionError("подготовка теста неверна: ValueError не случился")
    except ValueError:
        pass
    retry_off()
    res = xd.fetch_daily_pct5()
    check(res.available is False, "нечисловой ключ: пустой ряд, а не ValueError")
    check(dict(res) == {"spx": {}, "dxy": {}, "gold": {}},
          "нечисловой ключ: ключи рядов на месте, точек нет")
    check(os.path.exists("daily_pct5.json"), "негодный кэш не удалён молча")

    bad = json.loads(json.dumps(raw0))
    bad["spx"][next(iter(bad["spx"]))] = "не число"
    write_cache(bad)
    retry_off()
    check(xd.fetch_daily_pct5().available is False,
          "нечисловое значение: пустой ряд, а не исключение")

    bad = json.loads(json.dumps(raw0))
    bad["dxy"] = [1, 2, 3]
    write_cache(bad)
    retry_off()
    check(xd.fetch_daily_pct5().available is False,
          "список вместо словаря: пустой ряд, а не исключение")

    # страховка вокруг преобразования: форма, которую is_valid не видел
    orig = xd._fetch_series
    xd._fetch_series = lambda *a, **k: {"spx": {"x": 1.0}, "dxy": {}, "gold": {}}
    try:
        res = xd.fetch_daily_pct5()
        check(res.available is False and dict(res) == {"spx": {}, "dxy": {},
                                                       "gold": {}},
              "форма мимо is_valid перехвачена, наружу пустой ряд")
        # ключ float('inf'): int(inf) даёт OverflowError, а прежняя страховка
        # перечисляла только (AttributeError, TypeError, ValueError) — то есть
        # обещание «не бросаем наружу» на этой форме не выполнялось
        xd._fetch_series = lambda *a, **k: {"spx": {float("inf"): 1.0},
                                            "dxy": {}, "gold": {}}
        res = xd.fetch_daily_pct5()
        check(res.available is False,
              "ключ float('inf') (OverflowError) перехвачен, а не выброшен")
    finally:
        xd._fetch_series = orig

    # --- обновление кэша БЕЗ его удаления (max_age_s) ---
    # Прежний способ обновить суточные данные — удалить файл перед вызовом
    # (так делает bot_rsi.get_macro). Он несовместим с правилом «пустышку не
    # кэшируем»: если источник в этот момент молчит, годная копия уже стёрта,
    # и макро-фильтры выключаются на сутки. Проверяем оба конца.
    shutil.copy(src, work)
    retry_off()
    check(xd.macro_available(xd.fetch_daily_pct5(max_age_s=86400)),
          "свежий кэш при max_age_s отдаётся как есть")
    old = time.time() - 2 * 86400
    os.utime("daily_pct5.json", (old, old))
    retry_off()
    res = xd.fetch_daily_pct5(max_age_s=86400)
    check(xd.macro_available(res) and os.path.exists("daily_pct5.json"),
          "просроченный кэш + мёртвый источник: отдаются вчерашние данные, "
          "а не пустой ряд")
    retry_off()
    check(xd.macro_available(xd.fetch_daily_pct5()),
          "без max_age_s поведение прежнее: возраст кэша не проверяется")

    # --- пауза повторных попыток на пути max_age_s ---
    # Отдача просроченной копии с диска — это НЕ ответ источника. Пока это
    # считалось успехом, _fetch_series сбрасывал паузу, кэш оставался
    # просроченным, и каждый следующий вызов снова шёл в молчащий Yahoo:
    # замер до правки — 5 вызовов подряд дали 5 обращений к источнику, а на
    # сайте вызов приходится на каждый запрос страницы.
    os.utime("daily_pct5.json", (old, old))
    retry_off()
    HITS[0] = 0
    got = [xd.fetch_daily_pct5(max_age_s=86400) for _ in range(5)]
    check(HITS[0] == 1,
          f"пять вызовов на просроченном кэше — обращений к мёртвому "
          f"источнику {HITS[0]}, остальные держит пауза RETRY_PAUSE_S")
    check(all(xd.macro_available(g) for g in got),
          "и все пять всё равно получили данные с диска, а не пустой ряд")
    check(bool(xd._retry_after), "пауза действительно заведена, а не сброшена")
    os.remove("daily_pct5.json")
    check(xd.fetch_daily_pct5(max_age_s=86400).available is False,
          "пауза идёт, а копии на диске уже нет: пустой ряд с причиной")
    check(HITS[0] == 1, "и источник за время паузы больше не дёргали")

    retry_off()
    check(xd.fetch_daily_pct5().available is False,
          "а вот удалённый заранее кэш восстановить нечем — так и теряются "
          "макро-фильтры у нынешнего bot_rsi.get_macro")

    retry_off()
    check(xd.fetch_funding("BTCUSDT") == [], "fetch_funding при отказе: []")
    check(xd.fetch_oi("BTCUSDT") == [], "fetch_oi при отказе: []")
    check(not os.path.exists("funding_BTCUSDT.json")
          and not os.path.exists("oi_BTCUSDT.json"),
          "пустышка funding/OI на диск не записана")

    # --- ФОРМА кэша funding/OI, а не только «непусто» ---
    # Прежде здесь стоял is_valid=bool, и словарь вместо списка пар считался
    # годным: fetch_funding отдавал наружу словарь, а step_lookup на нём
    # бросал ValueError — то самое исключение, которого контракт модуля
    # обещает не допускать. Имя AAAUSDT выбрано так, чтобы файл шёл первым по
    # алфавиту (это же понадобится ниже, в проверке describe_cache).
    with open("funding_AAAUSDT.json", "w", encoding="utf-8") as fh:
        json.dump({"1677283200000": 0.01}, fh)
    retry_off()
    got = xd.fetch_funding("AAAUSDT")
    check(got == [], "кэш funding словарём вместо пар: пустой ряд, не словарь")
    check(xd.step_lookup(got)(0) is None,
          "и step_lookup на нём не бросает наружу")
    with open("oi_AAAUSDT.json", "w", encoding="utf-8") as fh:
        json.dump([[1729119600000, "не число"]], fh)
    retry_off()
    check(xd.fetch_oi("AAAUSDT") == [],
          "кэш OI со строкой вместо числа: пустой ряд")
    check(os.path.exists("funding_AAAUSDT.json")
          and os.path.exists("oi_AAAUSDT.json"),
          "негодные кэши funding/OI не удалены молча")

    candles = [[i * 900000, 1.0, 1.0, 1.0, 1.0] for i in range(200)]
    aux = xd.build_aux4(candles, [], [], xd.fetch_daily_pct5())
    check(len(aux["fund"]) == 200 and all(v is None for v in aux["gold5"]),
          "build_aux4 на пустых рядах: длина цела, значения None")

    # --- describe_cache: инструмент проверки утверждений не должен падать ---
    # `python ext_data.py` предлагается шапкой модуля как способ проверить
    # заявленную глубину рядов. До правки первый же кэш неожиданной формы
    # ронял его с KeyError: 0, и остальные файлы оставались неописанными.
    shutil.copy(os.path.join(REPO, "funding_BTCUSDT.json"), work)
    write_cache([1, 2, 3])          # и daily_pct5.json тоже негодной формы
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        xd.describe_cache()
    out = buf.getvalue()
    check("НЕГОДНАЯ ФОРМА" in out,
          "describe_cache: негодный кэш описан строкой, а не исключением")
    check("funding_BTCUSDT.json" in out and "3800 точек" in out,
          "и годный файл ПОСЛЕ негодного всё равно описан (отчёт не оборван)")
    check(out.count("НЕГОДНАЯ ФОРМА") == 3,
          f"негодными названы все три: funding-словарь, oi-строка и "
          f"daily_pct5 списком (найдено {out.count('НЕГОДНАЯ ФОРМА')})")

    # --- describe_cache обязан говорить и о ПРОПАЖЕ файлов ---
    # Инструмент зовут, чтобы увидеть, какие ряды есть. Пропажа daily_pct5.json
    # — самое важное, что он может сообщить: именно её устраивает bot_rsi.py,
    # удаляя кэш перед обновлением. Прежде при отсутствии файла не печаталось
    # ничего, и пустой отчёт читался как «всё в порядке».
    empty_dir = tempfile.mkdtemp(prefix="test_ext_data_empty_")
    os.chdir(empty_dir)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        xd.describe_cache()
    out = buf.getvalue()
    check("daily_pct5.json" in out and "НЕТ ФАЙЛА" in out,
          "describe_cache в пустой папке говорит, что daily_pct5.json нет")
    check("НЕТ ФАЙЛОВ" in out,
          "и что кэшей funding/OI тоже нет — а не молчит")
    os.chdir(work)
    shutil.rmtree(empty_dir, ignore_errors=True)

    # --- symbol, который невозможно превратить в строку ---
    # Имя файла кэша собиралось прямо из symbol (f"funding_{symbol}.json") ДО
    # входа в _fetch_series, то есть до всякой защиты. Объект с падающим
    # __str__ ронял fetch_* наружу мимо контракта «ни одна fetch_* не бросает».
    class BadStr:
        def __str__(self):
            raise ArithmeticError("падаю в __str__")

        __repr__ = __str__

    retry_off()
    check(xd.fetch_funding(BadStr()) == [] and xd.fetch_oi(BadStr()) == [],
          "symbol с падающим __str__: пустой ряд, а не ArithmeticError наружу")
    # symbol попадает В ИМЯ ФАЙЛА, поэтому разделители пути тоже не имя монеты
    check(xd.fetch_funding("../evil") == [] and xd.fetch_oi("a/b") == [],
          "symbol с разделителем пути отвергнут, кэш мимо папки не пишется")
    check(not os.path.exists(os.path.join(os.path.dirname(work), "evil.json")),
          "и никакого файла за пределами рабочей папки не появилось")

    # --- целое из сотен цифр: json такое кладёт совершенно законно ---
    # math.isfinite приводит его к float и бросает OverflowError. _is_series
    # зовут не только внутри общего try (fetch_*), но и напрямую — из
    # step_lookup и describe_cache, где ловить некому.
    huge = 10 ** 400
    check(xd._is_series([[huge, 1.0]]) is False,
          "_is_series на целом из 400 цифр: негоден, а не OverflowError")
    check(xd.step_lookup([[huge, 1.0]])(0) is None,
          "и step_lookup на таком ряде отвечает None, а не падает")
    check(xd.step_lookup({"1": 2})(0) is None,
          "step_lookup на кэше-словаре: None вместо ValueError при распаковке")
    check(xd.step_lookup([(1, 2.0), (3, 4.0)])("не время") is None,
          "step_lookup с несравнимым ts: None вместо TypeError из bisect")

    # --- build_aux4 переживает любой ряд ---
    # Его зовут восемь скриптов бэктеста без try, а ряды приходят и от fetch_*
    # (которые при молчащем источнике отдают пустышку), и прямо из json.
    aux = xd.build_aux4(candles, "не ряд", {"a": 1}, None, label=BadStr())
    check(len(aux["fund"]) == 200 and all(v is None for v in aux["spx5"]),
          "build_aux4 на мусорных рядах: длина цела, значения None")
    aux = xd.build_aux4([[0, 1.0], "мусор", [1800000, 1.0]], [], [],
                        xd.MacroSeries())
    check(len(aux["fund"]) == 3,
          "свеча-мусор внутри списка не обрывает разбор остальных")

    os.chdir(REPO)
    shutil.rmtree(work, ignore_errors=True)

    # --- утверждение шапки «max_age_s в бою не использует никто» ---
    # Проверки выше показывают, что путь max_age_s РАБОТАЕТ. Но шапка модуля
    # утверждает и другое: что в бою по нему никто не ходит. Такое утверждение
    # протухает молча, поэтому оно тоже считается запуском, а не принимается
    # на веру. Когда bot_rsi.py перейдёт на max_age_s, эта проверка упадёт —
    # и это правильно: значит пора переписать шапку ext_data.py.
    import re as _re
    pat = _re.compile(r"\bfetch_daily_pct5\s*\(([^)]*)\)")
    prod, with_arg = [], []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "venv")]
        for f in files:
            if not f.endswith(".py") or f == "ext_data.py" or \
                    f.startswith("test_"):
                continue
            with open(os.path.join(root, f), encoding="utf-8",
                      errors="replace") as fh:
                for line in fh:
                    for m in pat.finditer(line):
                        prod.append(f)
                        if m.group(1).strip():
                            with_arg.append(f)
    # Число берём ИЗ САМОЙ шапки ext_data.py, а не держим здесь копию: иначе
    # два числа живут в разных файлах и расходятся молча. Так проверка звучит
    # как «докстрока не врёт» — и падает ровно тогда, когда её пора обновить,
    # а не тогда, когда кто-то добавил в проект законный новый скрипт.
    with open(os.path.join(REPO, "ext_data.py"), encoding="utf-8") as fh:
        head = fh.read()
    claim = _re.search(r"(\d+)\s+продакшен-вызов", head)
    check(claim is not None,
          "в шапке ext_data.py есть утверждение о числе продакшен-вызовов")
    if claim:
        said = int(claim.group(1))
        check(len(prod) == said,
              f"шапка ext_data.py обещает {said} продакшен-вызовов "
              f"fetch_daily_pct5, а в исходниках их {len(prod)} "
              f"({sorted(set(prod))}) — обновите число в шапке")
    check(not with_arg,
          f"и ни один из них не передаёт max_age_s — путь прошлого круга в бою "
          f"не задействован (нашлись: {sorted(set(with_arg))})")
    with open(os.path.join(REPO, "bot_rsi.py"), encoding="utf-8") as fh:
        bot_src = fh.read()
    check("os.remove(cache_file)" in bot_src,
          "bot_rsi.get_macro по-прежнему УДАЛЯЕТ daily_pct5.json — то самое, "
          "от чего max_age_s должен избавить (правка ждёт своего круга)")

    # --- числа покрытия из шапки модуля: проверяем, а не верим ---
    # Пауза перед повтором обязана быть сброшена ИМЕННО ЗДЕСЬ: выше мы нарочно
    # уронили источник, и все три ряда остались помечены «не трогать 5 минут».
    # Без сброса замер шёл бы по пустым рядам и покрытие выходило нулевым —
    # проверка ниже падала, то есть числа шапки на деле никто не подтверждал.
    retry_off()
    hist = os.path.join(REPO, "history_BTCUSDT_15m_1150d.json")
    if os.path.exists(hist):
        import evolution4 as e4
        with open(hist) as fh:
            candles = json.load(fh)
        pct5 = xd.fetch_daily_pct5()
        aux = xd.build_aux4(candles, xd.fetch_funding("BTCUSDT", 1200),
                            xd.fetch_oi("BTCUSDT"), pct5)
        cov = xd.aux_coverage(aux)
        te = e4.train_end_bar(len(candles))
        tr = {k: sum(v is not None for v in aux[k][:te]) / te
              for k in ("oi_chg", "gold5")}
        check(all(round(cov[k], 3) == 1.0 for k in ("fund", "spx5", "dxy5")),
              "покрытие fund/spx5/dxy5 = 100%, как написано в шапке")
        check(abs(cov["oi_chg"] - 0.579) < 0.001
              and abs(cov["gold5"] - 0.428) < 0.001,
              f"покрытие окна: oi_chg {cov['oi_chg']:.1%}, "
              f"gold5 {cov['gold5']:.1%} — как написано в шапке")
        check(abs(tr["oi_chg"] - 0.461) < 0.001
              and abs(tr["gold5"] - 0.268) < 0.001,
              f"покрытие обучающей части: oi_chg {tr['oi_chg']:.1%}, "
              f"gold5 {tr['gold5']:.1%} — как написано в шапке")
        # экзамен важен отдельно: на нём оба ряда полны, и только поэтому
        # можно говорить, что экзамен эти гены вообще проверяет
        ex = {k: sum(v is not None for v in aux[k][te:]) / (len(candles) - te)
              for k in ("oi_chg", "gold5")}
        check(all(round(ex[k], 3) == 1.0 for k in ("oi_chg", "gold5")),
              f"на экзамене оба ряда полны: oi_chg {ex['oi_chg']:.1%}, "
              f"gold5 {ex['gold5']:.1%}")

    print(f"\nВСЕ {OK[0]} ПРОВЕРОК ПРОЙДЕНЫ (боевые кэши не тронуты)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    main()
