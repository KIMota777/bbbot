# -*- coding: utf-8 -*-
"""Общий кэш для скриптов refute_btc_*: свечи BTC и полный aux.

Строится один раз (около семи секунд) и складывается рядом в pickle, чтобы
четыре скрипта не пересчитывали SMC/паттерны/индикаторы заново. Содержимое
ровно то же, что строит bots_honest/archive_honest:
    ev.fetch("BTCUSDT", "15", 1150) + e8.make_aux_builder(pct5, 96).
Файл — рабочий, его можно удалить в любой момент: пересоберётся сам.
"""
import os
import pickle

PATH = "refute_btc_cache.pkl"


def load():
    if os.path.exists(PATH):
        with open(PATH, "rb") as fh:
            return pickle.load(fh)
    import bots_honest as bh
    import evolution as ev
    import evolution2 as e2
    import evolution8 as e8
    import ext_data as xd
    e2.BARS_PER_DAY = 96
    candles = ev.fetch("BTCUSDT", "15", bh.DAYS)
    aux = e8.make_aux_builder(xd.fetch_daily_pct5(), 96)("BTCUSDT", candles)
    with open(PATH, "wb") as fh:
        pickle.dump((candles, aux), fh)
    return candles, aux
