# -*- coding: utf-8 -*-
"""Запуск любого скрипта проекта без окна терминала.

Зачем: фоновые процессы (сайт, помощник, боты) должны переживать закрытие
консоли и при этом не висеть гроздью окон на рабочем столе. pythonw.exe
окна не создаёт, но у него sys.stdout и sys.stderr равны None — а наши
скрипты пишут в консоль (logging.StreamHandler, print, лог Flask) и падают
на первой же строке вывода. Этот launcher подменяет потоки на файл и уже
затем запускает целевой скрипт как __main__ (важно: runpy сохраняет
__file__, от которого app.py вычисляет пути к шаблонам и данным).

Использование:  pythonw run_hidden.py <скрипт.py> [аргументы...]
Вывод:          logs/<имя скрипта>[_<первый аргумент>].log
"""

import os
import runpy
import sys

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    if len(sys.argv) < 2:
        raise SystemExit("укажи скрипт: pythonw run_hidden.py app.py [args]")
    script = sys.argv[1]
    args = sys.argv[2:]

    name = os.path.splitext(os.path.basename(script))[0]
    if args:
        name += "_" + "".join(ch for ch in args[0] if ch.isalnum())
    log_dir = os.path.join(BASE, "logs")
    os.makedirs(log_dir, exist_ok=True)
    stream = open(os.path.join(log_dir, name + ".log"), "a",
                  encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream

    os.chdir(BASE)
    path = script if os.path.isabs(script) else os.path.join(BASE, script)
    sys.argv = [path] + args
    try:
        runpy.run_path(path, run_name="__main__")
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
