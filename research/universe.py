# -*- coding: utf-8 -*-
"""Сбор каталога: подтягивает все модули strat_*.py и наполняет strat.REG.

Отдельным файлом, чтобы добавление семейства не требовало правки чужого кода:
положил research/strat_что_то.py — он подхватился. Модуль, который падает при
импорте, не роняет остальные, но и молчать о нём нельзя: список сломанных
печатается и возвращается вызывающему.
"""
import glob
import importlib
import os
import sys
import traceback

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import strat  # noqa: E402

BROKEN = {}


def load(verbose=False):
    before = set(strat.REG)
    for path in sorted(glob.glob(os.path.join(DIR, "strat_*.py"))):
        name = os.path.splitext(os.path.basename(path))[0]
        if name in sys.modules:
            continue
        try:
            importlib.import_module(name)
        except Exception:                      # noqa: BLE001
            BROKEN[name] = traceback.format_exc(limit=3)
            if verbose:
                print("НЕ ЗАГРУЗИЛСЯ %s:\n%s" % (name, BROKEN[name]))
    added = sorted(set(strat.REG) - before)
    return strat.REG, added


def overlays():
    try:
        return importlib.import_module("overlay")
    except Exception:                          # noqa: BLE001
        BROKEN["overlay"] = traceback.format_exc(limit=3)
        return None


def summary():
    reg, _ = load()
    fam = {}
    total = 0
    for name, s in sorted(reg.items()):
        n = s.n_combos()
        total += n
        fam.setdefault(s.family, []).append((name, n))
    return fam, total


if __name__ == "__main__":
    fam, total = summary()
    print("%-14s %-18s %s" % ("семейство", "стратегия", "сочетаний"))
    for f in sorted(fam):
        for name, n in sorted(fam[f]):
            print("%-14s %-18s %6d" % (f, name, n))
    print("\nвсего стратегий: %d, сочетаний параметров: %d"
          % (sum(len(v) for v in fam.values()), total))
    if BROKEN:
        print("\nне загрузились: %s" % ", ".join(sorted(BROKEN)))
        for k, v in BROKEN.items():
            print("--- %s\n%s" % (k, v))
