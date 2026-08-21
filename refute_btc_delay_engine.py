# -*- coding: utf-8 -*-
"""ЗАДЕРЖКА ИСПОЛНЕНИЯ ВХОДА — тот же движок evolution2.run5, а не новый.

Движок переписывать нельзя и не нужно: я беру ЕГО ЖЕ исходник, вставляю в
него три куска (отложенный вход) и выполняю получившийся текст в пространстве
имён самого evolution2. Все константы (TAKER/MAKER/SLIP/FUND_8H/LEV/MARGIN)
остаются общими с модулем, поэтому подкрутка издержек действует и здесь.

Смысл задержки: сигнал сработал на закрытии свечи i, но ордер исполняется
только на закрытии свечи i+D. Всё остальное — как в бою: цена входа берётся
по свече исполнения (с тем же проскальзыванием), стоп ставится в момент
открытия по окну на баре исполнения, сетка строится от фактической цены.
При D=0 функция обязана совпасть с run5 бит в бит — это проверяется.
"""
import inspect

import evolution2 as e2

SRC = inspect.getsource(e2.run5)

# 1) сигнатура: добавлен параметр задержки
A_OLD = "def run5(candles, pre, g, entry_filter=None, events=None, storm=None):"
A_NEW = ("def run5_delay(candles, pre, g, entry_filter=None, events=None, "
         "storm=None, delay=0):")

# 2) память об отложенном сигнале
B_OLD = "    pos = None\n    cooldown_until = 0\n"
B_NEW = "    pos = None\n    cooldown_until = 0\n    pending = None\n"

# 3) исполнение отложенного входа — до разбора нового сигнала.
#    Код входа тот же, что ниже в оригинале, только берётся свеча исполнения.
C_OLD = "        if i < cooldown_until:\n            continue\n"
C_NEW = """        if i < cooldown_until:
            continue
        if pending is not None:
            if i >= pending[0]:
                side = pending[1]
                pending = None
                sgn = 1 if side == "L" else -1
                px = c * (1 + sgn * SLIP)
                q0 = m_k[0] * LEV / px
                stop = (rlow[i] * (1 - g["sweep"]) if side == "L"
                        else rhigh[i] * (1 + g["sweep"]))
                adds = []
                ap = px
                for k in range(1, g["levels"]):
                    ap = ap * (1 - sgn * g["step"])
                    adds.append((ap, m_k[k] * LEV / ap))
                pos = dict(side=side, fills=[(px, q0)], stop=stop, adds=adds,
                           opened_i=i, fees=q0 * px * TAKER, be_done=False)
                if events is not None:
                    events.append(dict(t=ts, type="entry", side=side,
                                       price=px))
            continue
"""

# 4) сам сигнал при включённой задержке вход не открывает, а ставит в очередь
D_OLD = """        if not side:
            continue
        sgn = 1 if side == "L" else -1
"""
D_NEW = """        if not side:
            continue
        if delay:
            pending = (i + delay, side)
            continue
        sgn = 1 if side == "L" else -1
"""

src = SRC
for old, new in ((A_OLD, A_NEW), (B_OLD, B_NEW), (C_OLD, C_NEW), (D_OLD, D_NEW)):
    assert src.count(old) == 1, "не найден кусок исходника: " + old[:40]
    src = src.replace(old, new)

exec(compile(src, "<run5_delay>", "exec"), e2.__dict__)
run5_delay = e2.run5_delay


def run_delay(candles, pre, g, filt, lev, delay, events=None):
    """Прогон с задержкой на нужном плече (глобал LEV возвращается назад)."""
    old = e2.LEV
    e2.LEV = lev
    try:
        return run5_delay(candles, pre, g, entry_filter=filt, events=events,
                          delay=delay)
    finally:
        e2.LEV = old
