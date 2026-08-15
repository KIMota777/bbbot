# -*- coding: utf-8 -*-
"""Общая часть попытки опровергнуть вывод через ИСПОЛНЕНИЕ.

Здесь ровно то, чего не хватало в refute_exec_engine.py: обвязка, которая
прогоняет тот же самый протокол отбора (скользящая проверка вперёд, плато
вместо пика), но через настраиваемый движок исполнения.

ЧТО ЗДЕСЬ ВАЖНО НЕ ПЕРЕПУТАТЬ.

1. СИГНАЛЫ НЕ ТРОГАЮТСЯ. Ни одна строчка strat_*.py не меняется. Меняется
   только то, КАК приказ превращается в позицию и как она сопровождается.
   Если после этого результат меняется сильно — значит вывод исследования был
   про исполнение. Если не меняется — вывод был про сигнал.

2. ОТБОР ОСТАЁТСЯ ТЕМ ЖЕ. Параметры правила по-прежнему выбираются по
   сглаженной оценке на обучающем окне и применяются на следующем куске.
   ВАЖНО: оценка на обучающем окне считается ТЕМ ЖЕ способом исполнения, что и
   применение. Иначе получилось бы, что мы выбираем параметры под маркет, а
   торгуем лимитом, — это не сравнение исполнений, а каша.

3. ЭКЗАМЕН ЗАКРЫТ. По умолчанию всё режется по VAL_END_MS. Функция с чтением
   экзамена одна, называется прямо и требует явного флага.
"""
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import engine            # noqa: E402
import metrics           # noqa: E402
import portfolio         # noqa: E402
import protocol          # noqa: E402
import rdata             # noqa: E402
import refute_exec_engine as xe   # noqa: E402

WARMUP = 600
MEMBERS = ["c_willr", "bos", "vol_spike", "supertrend"]
TF = "240"

# Наборы способов исполнения. Имя -> XCfg. Базовый — ровно то, что делал автор.
def variants():
    V = {}
    V["база: маркет по открытию"] = xe.XCfg()
    # 1. лимитный вход: заявка на откат в НАШУ пользу, отступ в долях стопа
    V["лимит откат 0.25R, жизнь 3"] = xe.XCfg(entry="limit", limit_off=0.25,
                                              limit_life=3)
    V["лимит откат 0.5R, жизнь 3"] = xe.XCfg(entry="limit", limit_off=0.5,
                                             limit_life=3)
    V["лимит откат 0.25R, жизнь 6"] = xe.XCfg(entry="limit", limit_off=0.25,
                                              limit_life=6)
    V["лимит откат 0.5R, жизнь 12"] = xe.XCfg(entry="limit", limit_off=0.5,
                                              limit_life=12)
    V["лимит 0.25R, прокол 0.15%"] = xe.XCfg(entry="limit", limit_off=0.25,
                                             limit_life=3, limit_pen=0.0015)
    V["лимит 0.25R + половина на 1.5R"] = xe.XCfg(
        entry="limit", limit_off=0.25, limit_life=3,
        partial_R=1.5, partial_frac=0.5, trail_after_partial=1.0)
    # 2. частичная фиксация
    V["половина на 1R, остаток трейл"] = xe.XCfg(partial_R=1.0,
                                                 partial_frac=0.5,
                                                 trail_after_partial=1.0)
    V["половина на 1.5R, остаток трейл"] = xe.XCfg(partial_R=1.5,
                                                   partial_frac=0.5,
                                                   trail_after_partial=1.0)
    V["половина на 1R, без трейла"] = xe.XCfg(partial_R=1.0, partial_frac=0.5)
    # 3. безубыток и выход по времени
    V["безубыток после 1R"] = xe.XCfg(be_R=1.0)
    V["половина на 1R + безубыток"] = xe.XCfg(partial_R=1.0, be_R=1.0,
                                              trail_after_partial=1.0)
    V["выход по времени 6 баров"] = xe.XCfg(time_bars=6)
    V["выход по времени 12 баров"] = xe.XCfg(time_bars=12)
    V["выход по времени 30 баров"] = xe.XCfg(time_bars=30)
    V["стоп x2 + время 12"] = xe.XCfg(stop_mult=2.0, time_bars=12)
    V["стоп x3 + время 12"] = xe.XCfg(stop_mult=3.0, time_bars=12)
    V["стоп x0.6"] = xe.XCfg(stop_mult=0.6)
    V["стоп x1.5"] = xe.XCfg(stop_mult=1.5)
    V["трейл 1.5 стопа сразу"] = xe.XCfg(trail_always=1.5)
    V["трейл 3 стопа сразу"] = xe.XCfg(trail_always=3.0)
    # 5. сдвиг часа входа — проверка, что сигнал вообще живёт
    V["сдвиг входа на 1 бар"] = xe.XCfg(delay=1)
    V["сдвиг входа на 2 бара"] = xe.XCfg(delay=2)
    V["сдвиг входа на 3 бара"] = xe.XCfg(delay=3)
    return V


def build_tapes(st, sym, x, cfg, t0, t1):
    """Ленты сделок по всем сочетаниям параметров правила на одном активе."""
    bars = rdata.load_bars(sym, TF)
    sub, off = bars.slice(t0, t1, warmup=WARMUP)
    keys = sorted(st.grid)
    tapes = {}
    stat = dict(placed=0, filled=0, maker=0)
    for p in st.combos():
        try:
            sig = st.build(sub, p)
        except Exception:                          # noqa: BLE001
            continue
        if int((sig.entry != 0).sum()) == 0:
            continue
        res = xe.run(sub, sig, cfg, x, start_i=off)
        for part in res.note.split():
            k, v = part.split("=")
            stat[k] = stat.get(k, 0) + int(v)
        if len(res.trades) < 3:
            continue
        tapes[tuple(p[k] for k in keys)] = protocol.Tape(res)
    return tapes, stat


def oos_trades_x(st, sym, x, cfg, t0, t1, is_days=360, oos_days=90):
    """Сделки только из внеобучающих кусков — при данном способе исполнения."""
    tapes, stat = build_tapes(st, sym, x, cfg, t0, t1)
    if not tapes:
        return [], stat
    wf = protocol.walk_forward(tapes, st.grid, is_days, oos_days,
                               t_start=t0, t_end=t1)
    out = []
    for f in wf:
        if not f.get("chosen"):
            continue
        tape = tapes[f["chosen_key"]]
        for k in tape.window(f["oos_from"], f["oos_to"]):
            out.append(dict(symbol=sym, t_in=int(tape.t_in[k]),
                            t_out=int(tape.t_out[k]), ret=float(tape.ret[k]),
                            low=float(tape.eq_low[k])))
    out.sort(key=lambda z: z["t_in"])
    return out, stat


def ensemble(reg, x, risk=0.01, t0=None, t1=None, members=None,
             is_days=360, oos_days=90, cfg=None, allow_test=False):
    """Ансамбль: 4 правила x 5 монет, общий капитал, равная доля риска.

    Возвращает словарь с помесячной доходностью, просадкой, числом сделок и
    статистикой исполнения. Экзамен читается только при allow_test=True.
    """
    if t0 is None:
        t0 = rdata.SPLITS["trainval"][0]
    if t1 is None:
        t1 = rdata.SPLITS["trainval"][1]
    if not allow_test and t1 > rdata.VAL_END_MS:
        raise AssertionError("экзамен закрыт: t1 больше VAL_END_MS")
    cfg = cfg or engine.Cfg(risk_frac=risk, max_lev=10.0)
    members = members or MEMBERS
    all_trades = {}
    stat = dict(placed=0, filled=0, maker=0)
    per_arm = {}
    for name in members:
        st = reg[name]
        for sym in rdata.SYMBOLS:
            trs, s = oos_trades_x(st, sym, x, cfg, t0, t1, is_days, oos_days)
            for k, v in s.items():
                stat[k] = stat.get(k, 0) + v
            if trs:
                all_trades["%s|%s" % (name, sym)] = trs
                per_arm["%s|%s" % (name, sym)] = trs
    if not all_trades:
        return None
    c = portfolio.combine(all_trades, risk_each=1.0)
    mo = portfolio.monthly_from_curve(c["times"], c["curve"])
    mv = np.array([m[1] for m in mo]) if mo else np.array([])
    return dict(mo=c["mo"], mo_med=float(np.median(mv)) if len(mv) else 0.0,
                mo_pos=float((mv > 0).mean()) if len(mv) else 0.0,
                n_months=len(mv), maxdd=c["maxdd"], ret=c["ret"],
                trades=c["trades"], days=c["days"], stat=stat,
                curve=c["curve"], times=c["times"], per_arm=per_arm)


def fmt(name, r):
    if r is None:
        return "%-32s  нет сделок" % name
    fill = ""
    if r["stat"].get("placed"):
        fill = "  залив %3.0f%% мейкер %3.0f%%" % (
            100.0 * r["stat"]["filled"] / max(r["stat"]["placed"], 1),
            100.0 * r["stat"]["maker"] / max(r["stat"]["filled"], 1))
    return ("%-32s  месяц %+6.2f%%  медиана %+6.2f%%  просадка %5.1f%%  "
            "сделок %5d%s" % (name, 100 * r["mo"], 100 * r["mo_med"],
                              100 * r["maxdd"], r["trades"], fill))
