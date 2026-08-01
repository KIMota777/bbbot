# -*- coding: utf-8 -*-
"""Разбор отклонённых сигналов (серые квадратики на графике).

Вопрос владельца: «много сделок откидывается, хотя много потенциальных».
Отвечаем цифрами: по каждому ВОРОТУ смотрим, что было бы, если бы мы всё-таки
вошли (гипотетика посчитана тем же движком), и сравниваем со средним R
реальных сделок сетапа.

Если у ворота гипотетический R ХУЖЕ реальных сделок — строгость оправдана.
Если ЛУЧШЕ и наблюдений достаточно — порог этого ворота стоит ослабить, и
это прямая подсказка, что отдать генетике на следующем круге.
Читает готовые webapp/data/signal2_*.json — движок не импортируется.
"""

import glob
import json
import os
from collections import defaultdict

MIN_N = 5   # меньше наблюдений — статистики нет, только справочно


def main():
    files = sorted(glob.glob(os.path.join("webapp", "data", "signal2_*.json")))
    if not files:
        print("нет данных: запусти build_signal_analytics2.py")
        return

    total_hint = []
    for path in files:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        name = d.get("name") or os.path.basename(path)[8:-5]
        tf = d.get("tf_label") or ""
        trades = d.get("trades", [])
        nms = d.get("near_misses", [])
        if not trades:
            continue
        real_r = sum(t["r"] for t in trades) / len(trades)
        real_wr = sum(1 for t in trades if t["r"] > 0) / len(trades) * 100

        print("=" * 78)
        print(f"{d.get('title', name)}  [{name} {tf}]")
        print(f"  РЕАЛЬНЫЕ сделки: {len(trades)}, средний R {real_r:+.3f}, "
              f"WR {real_wr:.1f}%")
        print(f"  ОТКЛОНЕНО (near-miss): {len(nms)} — во столько раз больше "
              f"сделок: x{len(nms)/max(1,len(trades)):.1f}")

        by_gate = defaultdict(list)
        for nm in nms:
            if nm.get("hypo_reason") in ("tp", "stop", "timeout", "liq"):
                by_gate[nm.get("gate", "?")].append(nm)
        if not by_gate:
            print("  (гипотетика не посчитана)")
            continue

        print(f"  {'ворото':22} {'шт':>4} {'ср.R':>7} {'WR%':>6} "
              f"{'сумма R':>8}  вердикт")
        rows = []
        for gate, items in by_gate.items():
            rs = [x["hypo_r"] for x in items]
            avg = sum(rs) / len(rs)
            wr = sum(1 for r in rs if r > 0) / len(rs) * 100
            rows.append((gate, len(rs), avg, wr, sum(rs)))
        for gate, n, avg, wr, tot in sorted(rows, key=lambda x: -x[1]):
            if n < MIN_N:
                verd = "мало наблюдений"
            elif avg > real_r:
                verd = "ЛУЧШЕ реальных -> порог строг, стоит ослабить"
                total_hint.append((name, tf, gate, n, avg, real_r))
            elif avg > 0:
                verd = "в плюсе, но хуже реальных"
            else:
                verd = "в минусе -> строгость оправдана"
            print(f"  {gate:22} {n:4} {avg:+7.3f} {wr:6.1f} {tot:+8.1f}  {verd}")

        allr = [x["hypo_r"] for items in by_gate.values() for x in items]
        avg_all = sum(allr) / len(allr)
        print(f"  ИТОГО по всем отклонённым: {len(allr)} шт, средний R "
              f"{avg_all:+.3f} против {real_r:+.3f} у реальных -> "
              f"{'фильтрация ПОЛЕЗНА' if avg_all < real_r else 'фильтрация СПОРНА'}")

    print("\n" + "=" * 78)
    print("ПОДСКАЗКИ ДЛЯ СЛЕДУЮЩЕГО ОТБОРА (ворота, где отклонённые оказались")
    print("лучше реальных сделок — кандидаты на ослабление порога):")
    if not total_hint:
        print("  нет: все ворота отсекают в среднем то, что хуже взятых сделок")
    for name, tf, gate, n, avg, real in sorted(total_hint, key=lambda x: -x[4]):
        print(f"  {name} {tf}: ворото «{gate}» — {n} шт, гипо R {avg:+.3f} "
              f"против {real:+.3f} у реальных")


if __name__ == "__main__":
    main()
