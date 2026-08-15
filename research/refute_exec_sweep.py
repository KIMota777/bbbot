# -*- coding: utf-8 -*-
"""Перебор способов исполнения на ОБУЧЕНИИ+ПРОВЕРКЕ. Экзамен не читается.

Один и тот же ансамбль (c_willr, bos, vol_spike, supertrend на пяти монетах,
4 часа), один и тот же протокол отбора параметров, один и тот же риск. Меняется
ТОЛЬКО способ входа, выхода и сопровождения позиции.

Вопрос ровно один: существует ли способ исполнения, при котором внеобучающий
результат МЕНЯЕТСЯ КАЧЕСТВЕННО, а не на шум.
"""
import json
import os
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import rdata                # noqa: E402
import refute_exec_lib as L  # noqa: E402
import universe             # noqa: E402

OUT = os.path.join(DIR, "out")


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    reg, _ = universe.load()
    V = L.variants()
    if only:
        V = {k: v for k, v in V.items() if only in k}
    print("ИСПОЛНЕНИЕ ПРОТИВ СИГНАЛА. Ансамбль из FINAL_SPEC, риск 1%% на рукав.")
    print("Окно: обучение+проверка (%d дней). Экзамен закрыт.\n"
          % ((rdata.VAL_END_MS - rdata.HIST_START_MS) // rdata.DAY_MS))
    rows = {}
    for name, x in V.items():
        t0 = time.time()
        r = L.ensemble(reg, x)
        rows[name] = None if r is None else {
            k: v for k, v in r.items()
            if k not in ("curve", "times", "per_arm")}
        print("%s   [%.0fс]" % (L.fmt(name, r), time.time() - t0))
        sys.stdout.flush()
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    tag = "_" + only if only else ""
    with open(os.path.join(OUT, "refute_exec_sweep%s.json" % tag), "w",
              encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, default=float, indent=1)
    print("\nсохранено -> out/refute_exec_sweep%s.json" % tag)


if __name__ == "__main__":
    main()
