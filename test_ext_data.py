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


def check(cond, name):
    assert cond, f"ПРОВАЛ: {name}"
    OK[0] += 1
    print(f"  ok  {name}")


def kill_network(xd):
    """Любое обращение к источнику = отказ источника."""
    fake_yf = types.ModuleType("yfinance")

    def dead_download(*a, **k):
        raise RuntimeError("сеть отключена в тесте")

    fake_yf.download = dead_download
    sys.modules["yfinance"] = fake_yf

    class DeadHTTP:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            def boom(*a, **k):
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
    os.remove("daily_pct5.json")
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

    candles = [[i * 900000, 1.0, 1.0, 1.0, 1.0] for i in range(200)]
    aux = xd.build_aux4(candles, [], [], xd.fetch_daily_pct5())
    check(len(aux["fund"]) == 200 and all(v is None for v in aux["gold5"]),
          "build_aux4 на пустых рядах: длина цела, значения None")

    os.chdir(REPO)
    shutil.rmtree(work, ignore_errors=True)

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
