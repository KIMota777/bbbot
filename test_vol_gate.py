# -*- coding: utf-8 -*-
"""Проверки гейта волатильности в боевом боте.

ГЛАВНАЯ ИЗ НИХ — ПЕРВАЯ: при vol_gate = 0 поведение бота обязано совпасть с
прежним ПОБИТОВО. Это та же договорённость, что у OFF7/OFF8 и gridlib.OFF10:
новый ген в выключенном положении не меняет ничего. Без такой проверки любая
новая настройка тихо сдвигает результаты всех прежних конфигов, и понять это
задним числом почти невозможно.

Остальные проверяют сам расчёт на данных с известным ответом.

Запуск: python test_vol_gate.py
"""
import math
import sys

import numpy as np

import bot_rsi

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("+" if cond else "!", name,
                         "" if cond else "   <-- " + detail))


class Fake(bot_rsi.RsiGridBot):
    """Бот без биржи и без логов: нужен только расчёт vol_rank."""

    def __init__(self, gate=0.0, look=100, win=20):
        import logging
        self.p = {"vol_gate": gate, "vol_gate_look": look, "vol_gate_win": win}
        self.vol_gate = float(gate)
        self.vol_gate_look = int(look)
        self.vol_gate_win = int(win)
        self.log = logging.getLogger("test")
        self.log.addHandler(logging.NullHandler())
        # Поля, которые entry_allowed читает у настоящего бота, собираются
        # ИЗ САМОГО МЕТОДА, а не перечисляются руками. Две ветки проекта
        # читают разные наборы, и ручной список означал бы падение по одному
        # полю за прогон при каждом переносе. Значения — «фильтр выключен»,
        # чтобы проверялся только гейт волатильности.
        import inspect
        import re as _re
        body = inspect.getsource(bot_rsi.RsiGridBot.entry_allowed)
        methods = {"get_funding", "get_oi_chg24", "get_macro", "vol_rank",
                   "log", "p"}
        SPECIAL = {"symbol": "TESTUSDT", "_storm": None,
                   "fund_long_max": 999.0, "fund_short_min": -999.0,
                   "spx_long_min": -999.0, "dxy_long_max": 999.0,
                   "gold_long_max": 999.0, "aroon_long_min": -999,
                   "aroon_short_min": -999, "ema_n": 200, "masf": 20,
                   "masl": 400, "atr_period": 14}
        for attr in sorted(set(_re.findall(r"self\.([a-z_][a-z0-9_]*)", body))):
            if attr in methods or hasattr(self, attr):
                continue
            setattr(self, attr, SPECIAL.get(attr, 0))

    # сетевые запросы в тесте запрещены: если фильтр их дёрнет, тест упадёт
    # громко, а не тихо сходит в интернет
    def get_funding(self):
        raise AssertionError("get_funding не должен вызываться при "
                             "выключенных фильтрах")

    def get_oi_chg24(self):
        raise AssertionError("get_oi_chg24 не должен вызываться")

    def get_macro(self):
        raise AssertionError("get_macro не должен вызываться")


def candles(prices):
    return [[i * 900000, p, p, p, p, 1.0] for i, p in enumerate(prices)]


print("1. Выключенный гейт не меняет поведение")
b = Fake(gate=0.0)
check("vol_gate = 0 означает выключен", b.vol_gate == 0.0)
# при выключенном гейте vol_rank даже не вызывается — проверяем через
# entry_allowed, подсунув заведомо низкую волатильность
rng = np.random.default_rng(0)
flat = list(100 + np.cumsum(rng.normal(0, 0.001, 400)))
cs = candles(flat)
called = {"n": 0}
orig = Fake.vol_rank


def spy(self, c):
    called["n"] += 1
    return orig(self, c)


Fake.vol_rank = spy
b2 = Fake(gate=0.0)
try:
    res = bot_rsi.RsiGridBot.entry_allowed(b2, "L", cs)
    check("при выключенном гейте vol_rank не считается", called["n"] == 0,
          "вызван %d раз" % called["n"])
except Exception as exc:                           # noqa: BLE001
    check("entry_allowed отработал", False, str(exc)[:80])
Fake.vol_rank = orig

print("\n2. Расчёт места волатильности")
b = Fake(gate=0.5, look=100, win=20)
check("мало баров -> None, а не запрет", b.vol_rank(candles([100] * 50)) is None)

# ряд, где волатильность РАСТЁТ к концу: место должно быть высоким
rng = np.random.default_rng(1)
calm = list(100 + np.cumsum(rng.normal(0, 0.01, 300)))
# шторм КОРОТКИЙ относительно окна сравнения (look=100): если он длиннее,
# он сам становится нормой, и место выходит средним. Это не изъян расчёта, а
# свойство перцентиля — и оно важно для выбора vol_gate_look в бою.
storm = list(calm[-1] + np.cumsum(rng.normal(0, 0.5, 12)))
vr_storm = b.vol_rank(candles(calm + storm))
check("в шторм место высокое", vr_storm is not None and vr_storm > 0.9,
      "получено %s" % vr_storm)

calm2 = list(100 + np.cumsum(rng.normal(0, 0.5, 300)))
quiet = list(calm2[-1] + np.cumsum(rng.normal(0, 0.005, 12)))
vr_quiet = b.vol_rank(candles(calm2 + quiet))
check("в штиль место низкое", vr_quiet is not None and vr_quiet < 0.2,
      "получено %s" % vr_quiet)
check("место всегда в пределах 0..1",
      all(0.0 <= v <= 1.0 for v in (vr_storm, vr_quiet)))

print("\n3. Гейт запрещает вход в штиле и пропускает в шторм")
for gate_v, series, want, label in (
        (0.5, calm2 + quiet, False, "штиль при пороге 0.5 -> запрет"),
        (0.5, calm + storm, True, "шторм при пороге 0.5 -> разрешён"),
        (0.99, calm + storm, True, "шторм при пороге 0.99 -> разрешён")):
    bb = Fake(gate=gate_v, look=100, win=20)
    got = bot_rsi.RsiGridBot.entry_allowed(bb, "L", candles(series))
    check(label, bool(got) == want, "получено %s" % got)


print("\n4. Последний закрытый бар учитывается")
# Ряд подобран так, чтобы место НЕ упиралось в потолок. На штормовом ряду все
# варианты дают ровно 1.0, и проверка ничего не различает — это была ошибка
# первой редакции теста: насыщенная величина не может показать разницу.
rng2 = np.random.default_rng(7)
mid = list(100 + np.cumsum(rng2.normal(0, 0.05, 400)))
b = Fake(gate=0.5, look=100, win=20)
v_base = b.vol_rank(candles(mid))
check("место не в потолке — проверка способна различать",
      v_base is not None and 0.05 < v_base < 0.95, "получено %s" % v_base)
v_up = b.vol_rank(candles(mid + [mid[-1] * 1.03]))
check("заметное движение поднимает место", v_up > v_base,
      "было %.3f, стало %.3f" % (v_base, v_up))
v_same = b.vol_rank(candles(mid))
check("повторный расчёт на тех же данных даёт то же", v_base == v_same)
v_trim = b.vol_rank(candles(mid[:-1]))
check("ответ на коротком ряду отличается от длинного", v_trim != v_base)

print("\nИтог: %d пройдено, %d провалено" % (len(OK), len(BAD)))
for x in BAD:
    print("   провал:", x)
sys.exit(1 if BAD else 0)
