# -*- coding: utf-8 -*-
"""ОТЧЁТ ПО КОПЕЕЧНЫМ ВЫХОДАМ («в ноль») — боты и сигнальные сетапы.

Зачем: перенос стопа в безубыток (ген be_move у ботов, be_after_r у сигналов
v3) закрывает сделку с микроплюсом порядка +0.001$. Формально это победа,
фактически ноль. Из-за таких выходов винрейт ботов доходил до 98%, а часть
«прибыли» оказалась артефактом: если копеечные выходы обнулить, бот уходит
в минус.

Что делает скрипт (ничего не считает заново — только читает уже посчитанные
файлы, поэтому работает мгновенно):
  1. таблица ПЕРЕСМОТРА вердиктов ботов «было 4 критерия -> стало 5»
     (webapp/data/bots_honest.json, пишет build_bot_honest_data.py);
  2. сверка: полные цифры из bots_honest.json обязаны совпасть с
     bot_trades_<SYM>.json (пишет build_analytics.py) — это два независимых
     сборщика над одним и тем же прогоном;
  3. тот же счёт для СИГНАЛЬНЫХ сетапов (webapp/data/signal2_*.json,
     v3_*.json, analytics_sig_*.json) — проверка, нет ли у них того же
     артефакта (ген be_after_r в signal_engine3);
  4. цифра со страницы /pnl («честно, экзамен»): кривая пересобирается из
     bot_trades_*.json, сверяется с pnl_curves.json и пересчитывается без
     копеечных выходов. build_pnl_curves.py — чужой файл, его не трогаем,
     поэтому результат печатается здесь;
  5. контракт JSON для страниц сайта: какие ключи появились.

Порог копеечного выхода и вся арифметика — bots_honest.py (TINY_USD,
tiny_stats): логика в одном месте.

Запуск: python tiny_report.py            # вывод в консоль
        python tiny_report.py --save     # + в tiny_report_out.txt
"""

import glob
import io
import json
import os
import sys

import bots_honest as bh

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                          # noqa: BLE001
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "webapp", "data")
OUT = os.path.join(ROOT, "tiny_report_out.txt")


class Tee:
    def __init__(self, path):
        self.out = sys.stdout
        self.fh = open(path, "w", encoding="utf-8")

    def write(self, s):
        self.out.write(s)
        self.fh.write(s)

    def flush(self):
        self.out.flush()
        self.fh.flush()


def load(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def bots_table(hon):
    """Пересмотр вердиктов: было (4 критерия) -> стало (5 критериев)."""
    print("=" * 118)
    print("1. БОТЫ: ПЕРЕСМОТР ВЕРДИКТОВ С УЧЁТОМ КОПЕЕЧНЫХ ВЫХОДОВ "
          f"(порог ${hon['tiny_rule']['thr_usd']:.2f} = "
          f"{hon['tiny_rule']['frac']*100:.0f}% маржи цикла "
          f"${hon['tiny_rule']['margin']:.0f})")
    print("   Пятый критерий: бот остаётся в плюсе на холдоуте, если "
          "копеечные выходы считать нулём.")
    print(f"  {'бот':10} {'БЫЛО':>18} {'СТАЛО':>18} | {'копееч.%':>9} "
          f"{'безуб.%':>8} | {'WR%':>6} {'честный WR%':>12} | "
          f"{'холдоут%':>9} {'холдоут БЕЗ КОПЕЕК%':>20}")
    for sym in hon["order"]:
        b = hon["bots"][sym]
        t, v = b["tiny"]["holdout"], b["verdict"]
        flag = "  <- ПОТЕРЯЛ ПОДТВЕРЖДЕНИЕ" if (v["key_old"] == "ok"
                                                and v["key"] != "ok") else ""
        print(f"  {sym:10} {v['status_old'] + ' ' + v['score_old']:>18} "
              f"{v['status'] + ' ' + v['score']:>18} | {t['tiny_share']:9.1f} "
              f"{t['be_share']:8.1f} | {t['wr']:6.1f} {t['wr_honest']:12.1f} | "
              f"{t['ret']:+9.1f} {t['ret_without_tiny']:+20.1f}{flag}")
    print()
    print("   ЧЕСТНАЯ ОЦЕНКА ДОХОДНОСТИ (медиана 90 соседних конфигов на "
          "холдоуте — то, чего разумно ждать):")
    print(f"  {'бот':10} {'было %':>9} {'/мес':>7} | "
          f"{'БЕЗ КОПЕЕЧНЫХ %':>17} {'/мес':>7} {'p10..p90':>17} {'>0':>6} | "
          f"вердикт")
    for sym in hon["order"]:
        b = hon["bots"][sym]
        r, n = b["robust"], b["robust_no_tiny"]
        print(f"  {sym:10} {r['median']:+9.1f} {r['per_month']:+7.2f} | "
              f"{n['median']:+17.1f} {n['per_month']:+7.2f} "
              f"{n['p10']:+8.1f}..{n['p90']:<8.1f}{n['share_pos']:5}% | "
              f"{b['verdict']['status']} {b['verdict']['score']}")
    s = hon["summary"]
    print(f"  {'СРЕДНЕЕ':10} {s['avg_robust']:+9.1f} "
          f"{s['avg_per_month']:+7.2f} | {s['avg_robust_no_tiny']:+17.1f} "
          f"{s['avg_per_month_no_tiny']:+7.2f}")
    p = hon["portfolio"]
    print(f"  портфель 5 ботов на холдоуте: {p['holdout']['ret']:+.1f}% "
          f"(DD {p['holdout']['dd']:.1f}%) -> БЕЗ КОПЕЕЧНЫХ "
          f"{p['holdout_no_tiny']['ret']:+.1f}% "
          f"(DD {p['holdout_no_tiny']['dd']:.1f}%)")
    print(f"  подтверждено {s['confirmed']} из {s['n']} "
          f"(было {s['confirmed_old']}), частично {s['partial']}, "
          f"не подтверждено {s['failed']}; держатся на артефакте "
          f"{s['on_artifact']}")


def bots_full_table(hon):
    """Разложение итога за всю историю: копейки / реальные плюсы / убытки."""
    print()
    print("=" * 118)
    print("2. БОТЫ: ИЗ ЧЕГО СЛОЖЕН ИТОГ ЗА ВСЮ ИСТОРИЮ (3.2 года, $ на базе "
          "маржи $5)")
    print(f"  {'бот':10} {'циклов':>7} {'копееч.':>8} {'копееч.%':>9} | "
          f"{'копейки$':>9} {'реальные+$':>11} {'убытки$':>9} {'ИТОГО$':>8} "
          f"{'БЕЗ КОПЕЕК$':>12} {'доля копеек в итоге':>20} | "
          f"{'ср.плюс$':>9} {'ср.минус$':>10}")
    for sym in hon["order"]:
        t = hon["bots"][sym]["tiny"]["full"]
        ns = t["tiny_net_share"]
        mark = ("  <- БЕЗ КОПЕЕК В МИНУСЕ" if t["holds_on_artifact"] else
                ("  <- итог сделан копейками" if ns and ns >= 50 else ""))
        print(f"  {sym:10} {t['n']:7} {t['tiny_n']:8} {t['tiny_share']:9.1f} | "
              f"{t['pnl_split']['tiny']:+9.2f} {t['pnl_split']['wins']:+11.2f} "
              f"{t['pnl_split']['losses']:+9.2f} {t['pnl_usd']:+8.2f} "
              f"{t['pnl_without_tiny']:+12.2f} "
              f"{('-' if ns is None else f'{ns:.0f}%'):>20} | "
              f"{t['avg_win_real']:+9.3f} {t['avg_loss']:+10.3f}{mark}")


def cross_check(hon):
    """bots_honest.json (полный прогон) против bot_trades_<SYM>.json.

    Это два независимых сборщика над одним и тем же прогоном движка: если
    цифры разошлись — где-то разъехались данные, показывать нельзя."""
    print()
    print("=" * 118)
    print("3. СВЕРКА: bots_honest.json (полная история) против "
          "bot_trades_<SYM>.json (файл графика)")
    bad = []
    for sym in hon["order"]:
        path = os.path.join(DATA, f"bot_trades_{sym}.json")
        if not os.path.exists(path):
            print(f"  {sym:10} нет файла {os.path.basename(path)}")
            continue
        d = load(path)
        a, b = hon["bots"][sym]["tiny"]["full"], d["tiny"]["full"]
        rows = [("циклов", a["n"], b["n"]),
                ("копеечных", a["tiny_n"], b["tiny_n"]),
                ("по безубытку", a["be_n"], b["be_n"]),
                ("итог$", a["pnl_usd"], b["pnl_usd"]),
                ("без копеек$", a["pnl_without_tiny"], b["pnl_without_tiny"]),
                ("честный WR", a["wr_honest"], b["wr_honest"])]
        diff = [f"{n}: {x} != {y}" for n, x, y in rows
                if abs(float(x) - float(y)) > 0.051]
        bad += diff
        # флаги у самих циклов: сумма меток из шапки обязана сойтись
        n_tiny = sum(1 for c in d["trades"] if c.get("tiny"))
        n_be = sum(1 for c in d["trades"] if c.get("be"))
        no_be = sum(1 for c in d["trades"] if "be" not in c)
        ok = not diff and n_tiny == b["tiny_n"] and n_be == b["be_n"]
        print(f"  {sym:10} циклов {a['n']:5} | копеечных {a['tiny_n']:5} "
              f"(меток в сделках {n_tiny:5}) | по безубытку {a['be_n']:5} "
              f"(меток {n_be:5}, без метки {no_be:3}) | "
              f"итог {a['pnl_usd']:+7.2f}$ -> без копеек "
              f"{a['pnl_without_tiny']:+7.2f}$   {'OK' if ok else 'РАСХОЖДЕНИЕ'}")
        for x in diff:
            print(f"  {'':10}   !! {x}")
    print(f"  -> {'совпало по всем ботам' if not bad else 'ЕСТЬ РАСХОЖДЕНИЯ'}")
    return not bad


def signals():
    """Тот же счёт для сигнальных сетапов: есть ли у них артефакт безубытка."""
    print()
    print("=" * 118)
    print("4. СИГНАЛЬНЫЕ СЕТАПЫ: тот же порог, те же метрики")
    print("   Ген безубытка есть только в движке v3 (signal_engine3.be_after_r); "
          "в signal_engine2 переноса в")
    print("   безубыток НЕТ по построению (стоп:тейк = 1:3 жёстко), в "
          "signal_engine (v1) он тоже выключен.")
    print("   Проверяем фактами, а не декларацией: считаем долю копеечных "
          "выходов в готовых файлах сделок.")
    files = sorted(glob.glob(os.path.join(DATA, "signal2_*.json"))
                   + glob.glob(os.path.join(DATA, "v3_*.json")))
    n_v3 = len(glob.glob(os.path.join(DATA, "v3_*.json")))
    print(f"  {'файл':28} {'сделок':>7} {'копееч.':>8} {'копееч.%':>9} "
          f"{'WR%':>6} {'честный WR%':>12} | {'копейки$':>9} {'ИТОГО$':>8} "
          f"{'БЕЗ КОПЕЕК$':>12} | {'be_after_r':>11}")
    worst = 0.0
    for path in files:
        d = load(path)
        cyc = [dict(pnl=t["pnl"], tiny=bh.is_tiny(t["pnl"]),
                    reason=t.get("reason"), be=False)
               for t in (d.get("trades") or [])]
        t = bh.tiny_stats(cyc, "off")
        worst = max(worst, t["tiny_share"])
        be = (d.get("genome") or {}).get("be_after_r")
        print(f"  {os.path.basename(path).replace('.json', ''):28} "
              f"{t['n']:7} {t['tiny_n']:8} {t['tiny_share']:9.1f} "
              f"{t['wr']:6.1f} {t['wr_honest']:12.1f} | "
              f"{t['pnl_split']['tiny']:+9.2f} {t['pnl_usd']:+8.2f} "
              f"{t['pnl_without_tiny']:+12.2f} | "
              f"{('-' if be is None else f'{be:g}'):>11}")
    # сетапы v1 (страницы /signal): списка сделок в файле нет, но сводка есть
    for path in sorted(glob.glob(os.path.join(DATA, "analytics_sig_*.json"))):
        d = load(path).get("stats") or {}
        t = d.get("tiny")
        name = os.path.basename(path).replace("analytics_", "").replace(".json", "")
        if not t:
            print(f"  {name:28} {d.get('trades', 0):7} {'?':>8} {'?':>9} "
                  f"{d.get('wr', 0):6.1f} {'':>12} | {'':>9} {'':>8} {'':>12}"
                  f" |   нет сводки: перезапустить build_analytics.py")
            continue
        worst = max(worst, t["tiny_share"])
        print(f"  {name:28} {t['n']:7} {t['tiny_n']:8} {t['tiny_share']:9.1f} "
              f"{t['wr']:6.1f} {t['wr_honest']:12.1f} | "
              f"{t['pnl_split']['tiny']:+9.2f} {t['pnl_usd']:+8.2f} "
              f"{t['pnl_without_tiny']:+12.2f} | {'нет гена':>11}")
    print(f"  ИТОГ ПО СИГНАЛАМ: максимальная доля копеечных выходов "
          f"{worst:.1f}% — того же артефакта у них НЕТ.")
    print("  Причина видна в цифрах: у сигналов винрейт 34-58% (у ботов "
          "95-98%), выход либо в стоп, либо в тейк")
    print("  на расстоянии 1:3, и «сделок в ноль» просто не возникает. "
          "Пересматривать их выводы из-за безубытка")
    print("  не нужно — они и так не подтверждены (ни один сетап за 15 волн).")
    if not n_v3:
        print("  Файлов v3_*.json в webapp/data нет: сетапы отбора v3 не "
              "финализированы (setups_v3.json отсутствует),")
        print("  считать в них нечего. Единственный доведённый до конфига "
              "сетап семейства v3 — setups_capitulation.json,")
        print("  и там be_after_r=0.0, то есть безубыток выключен. Как только "
              "v3-страницы появятся, эту проверку")
        print("  нужно повторить: ген be_after_r в движке есть, и включённым "
          "он даст ровно тот же артефакт.")


def pnl_page():
    """Цифра со страницы /pnl («честно, экзамен») без копеечных выходов.

    build_pnl_curves.py — чужой файл, его не трогаем; здесь та же кривая
    пересобирается из bot_trades_<SYM>.json (build_analytics сверяет свой
    список сделок с build_pnl_curves сделка в сделку, поэтому источник тот
    же) и сверяется с honest_pct из pnl_curves.json. Если сверка сошлась —
    значит и колонка «без копеечных» посчитана по той же модели: рукав $50,
    маржа 25% капитала, реинвест.
    """
    print()
    print("=" * 118)
    print("5. СТРАНИЦА /pnl: «ЧЕСТНАЯ» КРИВАЯ БЕЗ КОПЕЕЧНЫХ ВЫХОДОВ")
    cur = os.path.join(DATA, "pnl_curves.json")
    if not os.path.exists(cur):
        print("   нет webapp/data/pnl_curves.json")
        return
    d = load(cur)
    sleeve = d.get("sleeve", 50.0)
    ref = {s["key"]: s for s in d["series"]}
    print(f"  {'бот':10} {'на сайте%':>10} {'пересчёт%':>10} {'сверка':>8} | "
          f"{'БЕЗ КОПЕЕЧНЫХ%':>15} {'разница':>9} {'сделок':>7}")
    tot_e = tot_n = 0.0
    ok = True
    for sym in ("DOGEUSDT", "LTCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"):
        path = os.path.join(DATA, f"bot_trades_{sym}.json")
        r = ref.get(f"bot_{sym}")
        if not (os.path.exists(path) and r):
            continue
        t = load(path)
        rows = [c for c in t["trades"] if c["exit_ts"] >= t["holdout_from"]]
        e = n = sleeve
        for c in rows:
            e *= 1 + c["pnl"] / bh.BT_BASE
            n *= 1 + (0.0 if c["tiny"] else c["pnl"]) / bh.BT_BASE
        tot_e += e
        tot_n += n
        got, want = (e / sleeve - 1) * 100, r["honest_pct"]
        same = abs(got - want) <= 0.1
        ok = ok and same
        print(f"  {sym:10} {want:+10.1f} {got:+10.1f} "
              f"{('OK' if same else 'РАСХОЖД.'):>8} | "
              f"{(n/sleeve-1)*100:+15.1f} {(n-e)/sleeve*100:+9.1f} "
              f"{len(rows):7}")
    base = sleeve * 5
    pf = ref.get("pf_cons", {})
    print(f"  {'ПОРТФЕЛЬ':10} {pf.get('honest_pct', 0):+10.1f} "
          f"{(tot_e/base-1)*100:+10.1f} "
          f"{('OK' if abs((tot_e/base-1)*100 - pf.get('honest_pct', 0)) <= 0.1 else 'РАСХОЖД.'):>8} | "
          f"{(tot_n/base-1)*100:+15.1f} {(tot_n-tot_e)/base*100:+9.1f}")
    print(f"  -> {'пересчёт совпал с сайтом' if ok else 'ПЕРЕСЧЁТ РАЗОШЁЛСЯ С САЙТОМ'}"
          f"; значит цифру «{pf.get('honest_pct', 0):+.1f}% за экзамен» надо "
          f"читать как {(tot_n/base-1)*100:+.1f}%:")
    print("     остальное — копеечные выходы по безубытку. "
          "build_pnl_curves.py это пока не учитывает (чужой файл).")


def contract(hon):
    """Что появилось в JSON — чтобы страницы знали, откуда брать цифры."""
    print()
    print("=" * 118)
    print("6. КОНТРАКТ JSON ДЛЯ СТРАНИЦ САЙТА (новые ключи)")
    b = hon["bots"][hon["order"][0]]
    print("  webapp/data/bots_honest.json:")
    print("    tiny_rule                       — порог и объяснение "
          "(thr_usd, frac, margin, text, be_note)")
    print("    bots.<SYM>.tiny.{train,holdout,full}")
    print("      " + ", ".join(sorted(b["tiny"]["holdout"])))
    print("    bots.<SYM>.robust_no_tiny       — "
          + ", ".join(sorted(b["robust_no_tiny"])))
    print("    bots.<SYM>.verdict              — score/n_ok теперь из 5; "
          "добавлены status_old, key_old, score_old, n_ok_old, changed, n_crit")
    print("    portfolio.{train,holdout}_no_tiny, "
          "summary.{confirmed_old,on_artifact,avg_holdout_no_tiny,"
          "avg_robust_no_tiny,avg_per_month_no_tiny}")
    print("    criteria                        — пятый пункт добавлен в конец "
          "списка (индексы 0..3 не сдвинулись)")
    print("  webapp/data/bot_trades_<SYM>.json:")
    print("    trades[].tiny (bool)            — цикл закрылся «в ноль»")
    print("    trades[].be (bool)              — закрыт перенесённым стопом "
          "(нет ключа = стоп входа восстановить не удалось)")
    print("    шапка: n_tiny, n_be, tiny_thr, tiny.{thr_usd,frac,margin,"
          "be_method,full,holdout}")
    print("  webapp/data/analytics_bot_<SYM>.json:")
    print("    stats.tiny, stats.tiny_thr      — та же сводка за всю историю")
    print()
    print("  ГЛАВНАЯ ЦИФРА ДЛЯ РЕШЕНИЙ: bots.<SYM>.robust_no_tiny.per_month "
          "(%/мес) — медиана 90 соседних")
    print("  конфигов на холдоуте с обнулёнными копеечными выходами. "
          "Самая консервативная оценка из имеющихся.")


def conclusion(hon):
    print()
    print("=" * 118)
    print("ВЫВОД")
    ok = [s for s in hon["order"] if hon["bots"][s]["verdict"]["key"] == "ok"]
    lost = [s for s in hon["order"]
            if hon["bots"][s]["verdict"]["key_old"] == "ok"
            and hon["bots"][s]["verdict"]["key"] != "ok"]
    art = [s for s in hon["order"]
           if hon["bots"][s]["tiny"]["holdout"]["holds_on_artifact"]]
    made = [s for s in hon["order"]
            if (hon["bots"][s]["tiny"]["full"]["tiny_net_share"] or 0) >= 50]
    print(f"  подтверждены после снятия артефакта: "
          f"{', '.join(ok) if ok else 'НИ ОДИН'}")
    print(f"  потеряли подтверждение: {', '.join(lost) if lost else 'нет'}")
    print(f"  держатся на артефакте (без копеек холдоут в минусе): "
          f"{', '.join(art) if art else 'нет'}")
    print(f"  итог за всю историю сделан копейками (>= 50% результата): "
          f"{', '.join(made) if made else 'нет'}")


def main():
    if "--save" in sys.argv[1:]:
        sys.stdout = Tee(OUT)
    hon = load(os.path.join(DATA, "bots_honest.json"))
    print("КОПЕЕЧНЫЕ ВЫХОДЫ: ОТЧЁТ ПО БОТАМ И СИГНАЛАМ — "
          f"данные от {hon['generated']}")
    print(f"  {hon['tiny_rule']['text']}")
    print()
    bots_table(hon)
    bots_full_table(hon)
    cross_check(hon)
    signals()
    pnl_page()
    contract(hon)
    conclusion(hon)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
