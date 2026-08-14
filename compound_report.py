# -*- coding: utf-8 -*-
"""Что даёт реинвест (компаундинг) и портфель — на ТЕХ ЖЕ сделках, что на
странице /pnl, без новых допущений.

Читает webapp/data/pnl_curves.json — файл, который строит build_pnl_curves.py.
Там лежат УЖЕ скомпаундированные кривые капитала в долларах: каждая стратегия
стартует с $50, маржа сделки — 25% текущего капитала, то есть

    капитал *= (1 + pnl_сделки / 20)        # 20 = база бэктеста ($20 / $5 маржи)

Поэтому PnL отдельной сделки восстанавливается из кривой точно, обратной
формулой: pnl = 20 * (капитал_после / капитал_до - 1). Это и даёт колонку
«линейно» — сколько было бы при фиксированной марже $5 без реинвеста.

Честные оговорки:
  1. Кривые в файле прорежены до MAX_POINTS точек. У прореженной серии между
     соседними точками несколько сделок, восстановить их PnL нельзя — в такой
     строке вместо числа будет «н/д», и придётся поднять MAX_POINTS в
     build_pnl_curves.py и пересобрать файл.
  2. Если капитал стратегии дошёл до нуля (счёт слит), обратной формулы тоже
     нет: делить на ноль нечем. Это ДРУГАЯ причина «н/д», и в сноске она
     называется своим именем — раньше обе сводились к рассказу про
     прореживание, и владелец читал про MAX_POINTS там, где на самом деле
     стратегия слила счёт.
  3. Капитал в файле округлён до цента, поэтому «линейно» точно до десятых
     долей процента — на порядок точнее, чем сам бэктест.

Портфельные строки и просадки берутся из файла как есть: это ровно те числа,
которые видит владелец на сайте, второй раз их пересчитывать незачем.
Просадок две: закрытая (по завершённым сделкам) и плавающая, с переоценкой
ещё открытых циклов. Плечо когда-то выбиралось по закрытой — она ниже, —
поэтому показываем обе.
"""

import json
import os
import sys
import time

# Путь берём от САМОГО СКРИПТА, а не от текущей папки: отчёт запускают и
# двойным щелчком, и из другого каталога, и тогда относительный
# webapp/data/... указывал в никуда — «нет файла с кривыми» на месте, где
# файл есть. Та же болезнь, что лечили в start_site.bat через %~dp0.
HERE = os.path.dirname(os.path.abspath(__file__))
CURVES = os.path.join(HERE, "webapp", "data", "pnl_curves.json")
BT_BASE = 20.0     # база бэктеста: $20 капитала, $5 маржи — как в build_pnl_curves.py
MAX_POINTS = 1600  # то же прореживание, что в build_pnl_curves.py
SERIES_KEYS = ("key", "label", "group", "points", "final_usd", "final_pct", "dd")


def die(msg):
    """Лучше честно молчать, чем показать красивые, но неверные числа."""
    print(f"НЕ СЧИТАЮ: {msg}")
    print(f"Файл: {CURVES}")
    print(f"Собрать заново: python {os.path.join(HERE, 'build_pnl_curves.py')}")
    sys.exit(1)


def load():
    if not os.path.exists(CURVES):
        die("нет файла с кривыми")
    with open(CURVES, encoding="utf-8") as fh:
        data = json.load(fh)
    series = data.get("series")
    if not series:
        die("в файле нет ни одной серии")
    for s in series:
        missing = [k for k in SERIES_KEYS if k not in s]
        if missing:
            die(f"серия {s.get('key', '?')} без полей {', '.join(missing)} — "
                f"формат файла разошёлся с этим отчётом")
        if len(s["points"]) < 2:
            die(f"в серии {s['key']} меньше двух точек")
    if not data.get("sleeve"):
        die("не указан стартовый капитал стратегии (sleeve)")
    return data


THIN = "thin"    # кривая прорежена: сделки слиплись
ZERO = "zero"    # капитал дошёл до нуля: обратной формулы нет


def restore_pnls(s):
    """PnL сделок в масштабе бэктеста ($5 маржи).

    Возвращает (список, None) либо (None, причина). Причин ровно две, и они
    РАЗНЫЕ — одна про формат файла, вторая про слитый счёт; сноска внизу
    отчёта обязана называть ту, которая случилась на самом деле.
    """
    pts = s["points"]
    if len(pts) >= MAX_POINTS:      # прореживание отдаёт ровно MAX_POINTS(+1)
        return None, THIN
    if min(v for _, v in pts) <= 0:  # обнуление капитала — деления не будет
        return None, ZERO
    return [BT_BASE * (pts[i][1] / pts[i - 1][1] - 1)
            for i in range(1, len(pts))], None


def main():
    data = load()
    sleeve = float(data["sleeve"])
    series = data["series"]
    by_key = {s["key"]: s for s in series}
    strategies = [s for s in series if s["group"] in ("bot", "signal")]
    if not strategies:
        die("в файле нет ни одной стратегии (группы bot/signal)")

    print(f"Реинвест и портфель по данным {CURVES}")
    print("собран: " + time.strftime("%d.%m.%Y %H:%M",
                                     time.localtime(os.path.getmtime(CURVES))))
    print(f"Старт каждой стратегии ${sleeve:.0f}, маржа сделки — 25% капитала "
          f"(в бэктесте ${BT_BASE:.0f} и $5).\n")

    def ddf(s):
        """Плавающая просадка. None — честное «н/д»: у линии с ежемесячным
        ребалансом внутримесячной переоценки не существует."""
        v = s.get("dd_float")
        return "н/д" if v is None else f"{v:.1f}%"

    head = (f"{'Стратегия':44} {'Сделок':>7} {'Линейно':>10} "
            f"{'С реинвестом':>13} {'DD закр.':>9} {'DD плав.':>9}")
    print(head)
    print("-" * len(head))

    all_pnls, restored_all = [], True
    why = {}          # label -> причина отказа (THIN | ZERO)
    ruined = []       # стратегии со слитым счётом — их надо назвать отдельно
    for s in strategies:
        pnls, reason = restore_pnls(s)
        if s.get("ruined") or reason == ZERO:
            ruined.append(s["label"])
        if pnls is None:
            restored_all = False
            why[s["label"]] = reason
            trades, lin = "н/д", "н/д"
        else:
            all_pnls += pnls
            trades = str(len(pnls))
            lin = f"{sum(pnls) / BT_BASE * 100:+.1f}%"
        # Пометку ставим ПОСЛЕ названия, внутри того же поля шириной 44.
        # Раньше она клеилась спереди — и название слитой стратегии уезжало
        # вправо на шесть знаков, колонка «Стратегия» переставала читаться
        # сверху вниз ровно на самой важной строке отчёта.
        mark = " [СЛИТ]" if s["label"] in ruined else ""
        print(f"{s['label'] + mark:44} {trades:>7} {lin:>10} "
              f"{s['final_pct']:>+12.1f}% {s['dd']:>8.1f}% {ddf(s):>9}")

    print("-" * len(head))
    n = len(strategies)
    for key in ("pf_cons", "pf_reb"):
        s = by_key.get(key)
        if not s:
            continue
        # линейно для портфеля: все сделки на общей базе n x $20 без реинвеста
        lin = (f"{sum(all_pnls) / (n * BT_BASE) * 100:+.1f}%"
               if restored_all else "н/д")
        print(f"{s['label']:44} {'':>7} {lin:>10} "
              f"{s['final_pct']:>+12.1f}% {s['dd']:>8.1f}% {ddf(s):>9}")

    # Сноска называет РЕАЛЬНУЮ причину каждого «н/д». Прореживание лечится
    # настройкой, слитый счёт не лечится ничем — путать их нельзя.
    thin = [k for k, v in why.items() if v == THIN]
    zero = [k for k, v in why.items() if v == ZERO]
    if thin:
        print(f"\n«н/д» у: {', '.join(thin)} — кривая прорежена до {MAX_POINTS} "
              "точек, отдельные сделки в ней слиплись и линейный вариант "
              "восстановить нельзя. Поднять MAX_POINTS в build_pnl_curves.py "
              "и пересобрать файл.")
    if zero:
        print(f"\n«н/д» у: {', '.join(zero)} — капитал стратегии дошёл до нуля: "
              "счёт слит, восстанавливать сделки делением на ноль нечем. "
              "Это не настройка отчёта, а результат стратегии.")
    if ruined:
        print(f"\n⛔ СЧЁТ СЛИТ: {', '.join(ruined)}. Прогон обрывается — "
              "капитала перестало хватать на очередной убыток. Доходность "
              "таких строк относится к участку ДО слива и ничего не значит.")
    if not restored_all:
        print("\nПортфельная колонка «линейно» тоже «н/д»: без сделок хотя бы "
              "одной стратегии общая сумма была бы неполной.")

    # --- во что превращается портфель ---
    reb = by_key.get("pf_reb")
    if not reb:
        print("\nПортфельной линии pf_reb в файле нет — итог по портфелю "
              "не считаю.")
        return
    t0 = min(s["points"][0][0] for s in strategies)
    t1 = max(s["points"][-1][0] for s in strategies)
    years = (t1 - t0) / 365.25 / 86400
    total0 = float(data.get("total") or sleeve * n)
    mult = reb["final_usd"] / total0
    print(f"\nПериод {years:.1f} года, {n} стратегий. "
          f"Портфель с ребалансом: ${total0:.0f} -> ${reb['final_usd']:.0f}.")
    if years <= 0 or mult <= 0:
        print("Годовую ставку не считаю: капитал ушёл в ноль или период пуст.")
        return
    print(f"Годовая ставка портфеля (CAGR): {(mult ** (1 / years) - 1) * 100:+.1f}%")
    print("Тот же процент на другом капитале (пропорция точная, "
          "проскальзывание на больших объёмах — нет):")
    for cap0 in (100, 500, 1000):
        print(f"  ${cap0} -> ${cap0 * mult:.0f} за {years:.1f}г "
              f"({cap0 * (mult - 1):+.0f}$)")


if __name__ == "__main__":
    # Консоль Windows — cp866, тире «—» и «ёлочек» в ней нет: без поправки
    # print падает с UnicodeEncodeError прямо посреди таблицы.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
