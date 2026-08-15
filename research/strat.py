# -*- coding: utf-8 -*-
"""Реестр стратегий и опорные семейства.

Стратегия — это функция build(bars, p) -> Signals. Ей запрещено знать про
капитал, плечо и комиссии: она отвечает только на вопрос «куда и где стоп».
Размер позиции считает движок от риска. Разделение не косметическое — иначе
доходность стратегии можно поднять плечом и принять это за улучшение сигнала.

Каждое семейство обязано:
  * проходить engine.assert_causal (механическая проверка на будущее);
  * задавать стоп в долях цены (иначе размер позиции неопределён);
  * иметь СЕТКУ параметров, а не одну точку — ищем плато, а не пик.

Регистрация: REG[name] = Strategy(build, grid, family, note).
"""
import itertools

import numpy as np

import ind
from engine import Signals

REG = {}


class Strategy:
    __slots__ = ("name", "build", "grid", "family", "note")

    def __init__(self, name, build, grid, family, note=""):
        self.name = name
        self.build = build
        self.grid = grid
        self.family = family
        self.note = note

    def combos(self):
        keys = sorted(self.grid)
        for vals in itertools.product(*[self.grid[k] for k in keys]):
            yield dict(zip(keys, vals))

    def n_combos(self):
        n = 1
        for v in self.grid.values():
            n *= len(v)
        return n


def register(name, family, grid, note=""):
    def deco(fn):
        REG[name] = Strategy(name, fn, grid, family, note or (fn.__doc__ or ""))
        return fn
    return deco


def atr_stop(bars, p, sig, default=0.02):
    """Стоп в долях цены из ATR. Общая деталь почти всех семейств.

    Стоп в процентах цены одинаков для спокойного и штормового рынка, и это
    ошибка в обе стороны: в шторм он выбивается шумом, в штиль стоит слишком
    далеко и делает риск на сделку непредставительным. Через ATR ширина стопа
    сама подстраивается под текущую волатильность.
    """
    a = ind.atr(bars.h, bars.l, bars.c, int(p.get("atr_n", 14)))
    d = p.get("stop_atr", 2.0) * a / np.maximum(bars.c, 1e-12)
    d = np.where(np.isfinite(d) & (d > 0), d, default)
    return np.clip(d, 0.002, 0.25)


def finish(bars, p, sig, entry, exit_=None):
    """Общий хвост: стоп, тейк, трейл, нормировка размера по волатильности."""
    sig.entry = entry.astype(np.int8)
    if exit_ is not None:
        sig.exit = exit_.astype(bool)
    sig.stop = atr_stop(bars, p, sig)
    rr = p.get("rr", 0.0)
    sig.tp = sig.stop * rr if rr else np.zeros(len(bars.t))
    tk = p.get("trail_atr", 0.0)
    if tk:
        a = ind.atr(bars.h, bars.l, bars.c, int(p.get("atr_n", 14)))
        tr = tk * a / np.maximum(bars.c, 1e-12)
        sig.trail = np.where(np.isfinite(tr), np.clip(tr, 0.002, 0.25), 0.0)
    if p.get("voltarget", 0):
        # нормировка размера под целевую волатильность: в спокойном рынке
        # позиция крупнее, в штормовом мельче, риск в долларах ровнее
        rv = ind.realized_vol(bars.c, int(p.get("vol_n", 96)))
        k = np.where(rv > 0, p["voltarget"] / (rv * np.sqrt(96)), 1.0)
        sig.size_k = np.clip(np.nan_to_num(k, nan=1.0), 0.3, 2.5)
    return sig


# --- 1. Пробой Дончиана (механика черепах) ---------------------------------

@register("donchian", "breakout", {
    "n": [20, 30, 40, 55, 80, 120],
    "exit_n": [10, 20, 30],
    "stop_atr": [2.0, 3.0, 4.0],
    "atr_n": [14],
    "trail_atr": [0.0, 3.0],
}, "пробой канала за n баров, выход по обратному каналу")
def s_donchian(bars, p):
    """Классика Денниса: вход на пробое экстремума окна, выход по обратному.

    Гипотеза не в индикаторе, а в свойстве рынка: пробой уровня, который долго
    держался, запускает вынос стопов и вход опоздавших, и движение
    продолжается дольше, чем предполагает случайное блуждание.
    """
    n = len(bars.t)
    hi, lo = ind.donchian(bars.h, bars.l, int(p["n"]))
    xh, xl = ind.donchian(bars.h, bars.l, int(p["exit_n"]))
    e = np.zeros(n, dtype=np.int8)
    e[bars.c > hi] = 1
    e[bars.c < lo] = -1
    ex = ((bars.c < xl) | (bars.c > xh))
    return finish(bars, p, Signals(n), e, ex)


# --- 2. Пересечение скользящих ---------------------------------------------

@register("ema_cross", "trend", {
    "fast": [10, 20, 30, 50],
    "slow": [50, 100, 150, 200],
    "stop_atr": [2.0, 3.0, 4.0],
    "atr_n": [14],
    "trail_atr": [0.0, 4.0],
}, "быстрая EMA относительно медленной")
def s_ema_cross(bars, p):
    """Направление тренда по взаимному положению двух средних."""
    n = len(bars.t)
    if p["fast"] >= p["slow"]:
        return Signals(n)                      # вырожденная пара — пропуск
    f = ind.ema(bars.c, int(p["fast"]))
    s = ind.ema(bars.c, int(p["slow"]))
    up = f > s
    e = np.zeros(n, dtype=np.int8)
    # входим на СМЕНЕ состояния, а не на каждом баре: иначе один и тот же
    # тренд переоткрывается после каждого выхода по стопу
    ch = np.zeros(n, dtype=bool)
    ch[1:] = up[1:] != up[:-1]
    ok = ch & ~np.isnan(f) & ~np.isnan(s)
    e[ok & up] = 1
    e[ok & ~up] = -1
    return finish(bars, p, Signals(n), e)


# --- 3. Возврат к средней по RSI (родная идея проекта) ---------------------

@register("rsi_mr", "meanrev", {
    "rsi_n": [7, 14, 21],
    "os": [20, 25, 30, 35],
    "rr": [1.0, 1.5, 2.0],
    "stop_atr": [1.5, 2.0, 3.0],
    "atr_n": [14],
}, "перепроданность/перекупленность по RSI, выход по R-кратному тейку")
def s_rsi_mr(bars, p):
    """То, на чём построены нынешние боты, но в честной обвязке.

    Здесь эта идея сравнивается с остальными на равных: те же издержки, тот же
    размер от риска, тот же протокол окон. Задача — понять, есть ли в ней edge
    сама по себе, или прежний результат держался на подборе.
    """
    n = len(bars.t)
    r = ind.rsi(bars.c, int(p["rsi_n"]))
    e = np.zeros(n, dtype=np.int8)
    prev = np.concatenate([[np.nan], r[:-1]])
    e[(r < p["os"]) & (prev >= p["os"])] = 1
    e[(r > 100 - p["os"]) & (prev <= 100 - p["os"])] = -1
    return finish(bars, p, Signals(n), e)
