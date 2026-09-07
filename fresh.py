# -*- coding: utf-8 -*-
"""Свечи с догрузкой хвоста — для всего, что обязано быть СВЕЖИМ.

evolution.fetch отдаёт файл кэша, если он существует, — без срока годности.
Для окна отбора это правильно: граница обучение/холдоут закреплена датой
(test_configs.test_split_boundary_pinned), и окно не должно ползти вслед за
календарём. Но у того же кэша есть потребители, которым нужна именно
свежесть: дневные закрытия для гейта тренда (режим «сегодня» зависит от
вчерашнего закрытия), отрезок «после витрины» на карточке бота. Из-за вечного
кэша витрина три недели стояла на данных 14.08, а гейт тренда на сайте через
сутки после выкладки начал бы считать режим по устаревшим дням.

Кэш тот же, но хвост догружается: если последний завершённый бар старше
max_age_s, у биржи запрашиваются бары после него (только завершённые), файл
переписывается атомарно. Голова окна не обрезается — окно только растёт; там,
где важна фиксированная дата начала, начало задаётся датой, а не числом дней.

При молчащей бирже отдаётся то, что есть в кэше: страница не должна падать
из-за сети, а устаревший ряд честнее пустого — потребитель видит дату
последнего бара и может её показать.

НИКОГДА не вызывать с days=1150 (окно отбора): его «свежесть» — не свойство,
а поломка всех чисел витрины, см. тест границы.
"""
import json
import os
import tempfile
import time

DAY = 86400000


def step_ms(interval):
    return DAY if str(interval).upper() == "D" else int(interval) * 60000


def cache_path(symbol, interval, days):
    return "history_%s_%sm_%dd.json" % (symbol, interval, days)


def fresh_candles(symbol, interval, days, max_age_s=900):
    """[[ts,o,h,l,c],...] — кэш evolution.fetch плюс догруженный хвост."""
    import evolution as ev
    rows = ev.fetch(symbol, interval, days)          # создаст кэш, если его нет
    if not rows:
        return rows
    step = step_ms(interval)
    now = int(time.time() * 1000)
    last_complete = now - step                        # старт последнего завершённого
    last = int(rows[-1][0])
    if last_complete - last <= max_age_s * 1000:
        return rows
    tail = fetch_tail(symbol, interval, last + step, last_complete)
    # завершённость проверяется ЗДЕСЬ, а не только в загрузчике: откуда бы ни
    # пришёл хвост, в кэш попадают лишь бары, начатые не позже последнего
    # завершённого — иначе незакрытый бар стал бы «историей» до утра
    tail = [c for c in tail if last < int(c[0]) <= last_complete]
    if not tail:
        return rows
    merged = rows + tail
    path = cache_path(symbol, interval, days)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=path + ".", suffix=".tmp",
                                   dir=os.path.dirname(os.path.abspath(path)))
        with os.fdopen(fd, "w") as fh:
            json.dump(merged, fh)
        os.replace(tmp, path)
    except Exception:                                 # noqa: BLE001
        # записать не вышло — отдаём свежее из памяти, кэш не портим
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return merged


def fetch_tail(symbol, interval, start_ms, last_complete_start):
    """Бары с start_ms по последний завершённый включительно, страницами вперёд.

    Bybit отдаёт список от новых к старым и не больше 1000 за запрос; окно
    каждой страницы задаётся явно (start и end), чтобы не зависеть от того,
    какую тысячу баров биржа выберет при одном только start.
    """
    from pybit.unified_trading import HTTP
    step = step_ms(interval)
    out = []
    try:
        s = HTTP(testnet=False)
        cur = int(start_ms)
        for _ in range(80):
            if cur > last_complete_start:
                break
            end = min(cur + 999 * step, last_complete_start)
            r = s.get_kline(category="linear", symbol=symbol, interval=interval,
                            start=cur, end=end, limit=1000)
            rows = r["result"]["list"][::-1]          # от старых к новым
            page = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])]
                    for x in rows if cur <= int(x[0]) <= last_complete_start]
            out += page
            cur = end + step
            time.sleep(0.1)
    except Exception:                                 # noqa: BLE001
        # биржа молчит или ответ не той формы — хвоста нет, кэш не портим
        return []
    seen, uniq = set(), []
    for c in out:
        if c[0] not in seen:
            seen.add(c[0])
            uniq.append(c)
    uniq.sort(key=lambda c: c[0])
    return uniq
