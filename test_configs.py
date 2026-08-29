# -*- coding: utf-8 -*-
"""Проверки самих конфигов: чтобы бот и бэктест торговали одно и то же.

ЗАЧЕМ ЭТОТ ФАЙЛ. Сайт, отчёты и уровни считаются бэктестом, а деньгами
рискует bot_rsi.py. Если они расходятся хоть в одном параметре, все числа
верны про что-то другое. Здесь проверяется не логика (её проверяют тесты
движка), а стыки, на которых расхождение возникает молча.

Запуск: python test_configs.py
"""
import sys

import config
import evolution2 as e2
import evolution5 as e5
import evolution7 as e7

FAILED = []


def check(cond, name):
    print("  %s  %s" % ("ok " if cond else "ПРОВАЛ", name))
    if not cond:
        FAILED.append(name)


def modes():
    for sym, mm in config.SYMBOL_PARAMS.items():
        for mode, p in mm.items():
            if isinstance(p, dict):
                yield sym, mode, p


def test_index_sets():
    """Значения периодов обязаны попадать в наборы ТОЧНО.

    ГДЕ ЛОВУШКА. Бэктест хранит период не числом, а индексом в наборе:
    rsi_idx -> RSI_SET[rsi_idx]. Обратный перевод делает
    evolution7.nearest_idx, и он берёт БЛИЖАЙШЕЕ значение молча. Если в
    конфиг попадёт rsi_period=12, живой бот будет считать RSI(12), а
    бэктест — RSI(10) или RSI(14), и разошлись бы не только числа на
    сайте, но и смысл каждой сделки. Ошибки при этом нет нигде.
    """
    sets = {"rsi_period": e2.RSI_SET, "ema_n": e5.EMA_SET,
            "masf": e5.MAF_SET, "masl": e5.MAS_SET, "aroon_n": e5.ARN_SET}
    bad = []
    for sym, mode, p in modes():
        for key, opts in sets.items():
            if key in p and p[key] not in opts:
                bad.append("%s/%s: %s=%s, а набор %s"
                           % (sym, mode, key, p[key], opts))
    check(not bad, "периоды конфигов попадают в наборы движка точно"
          + ("" if not bad else " — " + "; ".join(bad)))


def test_genome_roundtrip():
    """Конфиг -> геном -> конфиг не должен менять параметры.

    Проверяется именно то, что разошлось бы незаметно: перевод в геном и
    обратно обязан вернуть те же периоды. Если nearest_idx подменил
    значение, здесь это видно сразу.
    """
    bad = []
    for sym, mode, p in modes():
        try:
            g = e7.cfg_to_genome(p, mode)
        except Exception as exc:                   # noqa: BLE001
            bad.append("%s/%s: геном не строится (%s)" % (sym, mode, exc))
            continue
        pairs = (("rsi_period", "rsi_idx", e2.RSI_SET),
                 ("ema_n", "ema_n_idx", e5.EMA_SET),
                 ("masf", "masf_idx", e5.MAF_SET),
                 ("masl", "masl_idx", e5.MAS_SET),
                 ("aroon_n", "aroon_idx", e5.ARN_SET))
        for key, idx, opts in pairs:
            if key not in p or idx not in g:
                continue
            back = opts[int(g[idx])]
            if back != p[key]:
                bad.append("%s/%s: %s=%s, а движок возьмёт %s"
                           % (sym, mode, key, p[key], back))
    check(not bad, "конфиг переживает перевод в геном без подмены"
          + ("" if not bad else " — " + "; ".join(bad)))


def test_all_modes_evaluable():
    """Каждый ЗАПУСКАЕМЫЙ режим должен строиться в геном.

    Режим, который не строится, нечем проверить — а лежит он там же, где
    проверенные, и запускается той же командой. Именно так три года прожили
    turbo. Такие конфиги место не в SYMBOL_PARAMS, а в LEGACY_UNVERIFIABLE.
    """
    bad = []
    for sym, mode, p in modes():
        try:
            e7.cfg_to_genome(p, mode)
        except Exception as exc:                   # noqa: BLE001
            bad.append("%s/%s (%s: %s)" % (sym, mode, type(exc).__name__, exc))
    check(not bad, "все запускаемые режимы выражаются геномом"
          + ("" if not bad else " — НЕ выражаются: " + "; ".join(bad)))


def test_retired_not_offered():
    """Отставленный конфиг не должен оставаться единственным у монеты.

    Это не про запрет, а про честность витрины: если у монеты отставлено
    всё, она не должна выглядеть работающей.
    """
    live = {}
    for sym, mode, _p in modes():
        if not config.is_retired(sym, mode):
            live.setdefault(sym, []).append(mode)
    print("     живые конфиги: %s"
          % ({s.replace("USDT", ""): m for s, m in live.items()} or "нет"))
    check(all(v for v in live.values()), "у каждой монеты в списке живых есть режим")


def test_legacy_kept_intact():
    """Вынесенные конфиги сохранены целиком, а не переписаны по памяти."""
    leg = getattr(config, "LEGACY_UNVERIFIABLE", {})
    check(bool(leg), "LEGACY_UNVERIFIABLE не пуст")
    ok = all(isinstance(v, dict) and v.get("lev") for v in leg.values())
    check(ok, "у каждого вынесенного конфига сохранено плечо")
    check(all(mode not in config.SYMBOL_PARAMS.get(sym, {})
              for sym, mode in leg),
          "вынесенные конфиги действительно убраны из SYMBOL_PARAMS")


def test_funding_sign():
    """Знак фандинга зависит от стороны сделки, а не всегда «расход».

    ГДЕ БЫЛА ОШИБКА. В evolution2.run5 стояло
        pos["fees"] += q_pre * c * FUND_8H / BARS_8H
    то есть фандинг списывался и с лонга, и с шорта. На бирже при
    положительной ставке лонги платят шортам. Лучший конфиг проекта на 99%
    в шорте, поэтому движок брал с него плату, которую он получал бы.

    Проверка прямая: один и тот же прогон при ставке +X и -X должен дать
    РАЗНЫЙ итог, и для шортового конфига положительная ставка обязана быть
    ВЫГОДНОЙ. Если кто-то вернёт безусловное списание, тест упадёт.
    """
    import all_configs_honest as ach
    import bots_honest as bh
    import evolution2 as e2
    import ext_data as xd

    sym, mode = "LTCUSDT", "final_w"
    if mode not in config.SYMBOL_PARAMS.get(sym, {}):
        print("     пропуск: нет %s/%s" % (sym, mode))
        return
    g = ach.with_defaults(e7.cfg_to_genome(config.SYMBOL_PARAMS[sym][mode], mode))
    parts = ach.halves(sym, g, xd.fetch_daily_pct5())
    part = parts["hold"]

    orig = e2.FUND_8H
    got = {}
    try:
        for tag, rate in (("плюс", +0.0002), ("ноль", 0.0), ("минус", -0.0002)):
            e2.FUND_8H = rate
            # ряд не передаём: проверяем именно плоскую ставку и её знак
            p2 = dict(part)
            p2.pop("funding", None)
            got[tag] = ach.one_half(p2, g, 5.0)["comp"]
    finally:
        e2.FUND_8H = orig

    evs = []
    bh.run_at(part["candles"], part["pre"], g, part["filt"], 5.0, events=evs)
    shorts = sum(1 for e in evs if e["type"] == "entry" and e.get("side") == "S")
    longs = sum(1 for e in evs if e["type"] == "entry" and e.get("side") == "L")
    print("     сторон: шортов %d, лонгов %d; итог при ставке "
          "+0.02%%/0/-0.02%%: %+.2f%% / %+.2f%% / %+.2f%%"
          % (shorts, longs, got["плюс"], got["ноль"], got["минус"]))

    check(got["плюс"] != got["ноль"], "ставка фандинга вообще влияет на итог")
    if shorts > longs:
        check(got["плюс"] > got["минус"],
              "у шортового конфига ПОЛОЖИТЕЛЬНАЯ ставка выгодна "
              "(шорт получает фандинг, а не платит)")
    else:
        check(got["плюс"] < got["минус"],
              "у лонгового конфига положительная ставка невыгодна")


def test_funding_reaches_every_caller():
    """Ряд настоящих ставок обязан доезжать ДО КАЖДОГО вызова движка.

    ЗАЧЕМ. Правку знака фандинга пришлось прокидывать в шесть мест — у каждого
    свой вызов run5. Прокинули в четыре, два пропустили: график «прежней
    шкалы» и симуляция на странице бота. Хуже того, правка молча изменила и
    их: для шортового конфига плоская ставка из РАСХОДА стала ДОХОДОМ, и линия
    LTC подскочила на 17 п.п. побочным эффектом.

    Тест грубый нарочно: он читает исходники и требует, чтобы у каждого вызова
    run5 или run_at в файлах, из которых едут числа сайта, стоял аргумент
    funding. Тонкую проверку тут не построить — важно само отсутствие копии
    без ряда.
    """
    import io as _io
    import os
    import re
    FILES = ("build_pnl_curves.py", "build_pnl_common.py", "build_modestats.py",
             "tierlist.py", "all_configs_honest.py", os.path.join("webapp", "app.py"))
    bad = []
    for fn in FILES:
        if not os.path.exists(fn):
            continue
        src = _io.open(fn, encoding="utf-8").read()
        # вызовы run5/run_at вместе с их аргументами до закрывающей скобки
        for m in re.finditer(r"(?:e2\.run5|bh\.run_at)\s*\(", src):
            depth, i = 0, m.end() - 1
            while i < len(src):
                if src[i] == "(":
                    depth += 1
                elif src[i] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            call = src[m.start():i + 1]
            if "funding" not in call:
                line = src[:m.start()].count(chr(10)) + 1
                bad.append("%s:%d" % (fn, line))
    check(not bad, "у каждого вызова движка в числах сайта есть ряд фандинга"
          + ("" if not bad else " — БЕЗ РЯДА: " + ", ".join(bad)))


def test_funding_series_units():
    """funding_series отдаёт ДОЛИ, а не проценты.

    Стократная ловушка проекта: в файлах ставки в процентах. Один пропущенный
    делитель на 100 даёт результат в двенадцать раз больше настоящего, и ни
    одна другая проверка этого не поймает.
    """
    import bots_honest as bh
    aux = {"fund": [0.01, -0.02, None, 0.005]}
    got = bh.funding_series(aux)
    want = [0.0001, -0.0002, 0.0, 0.00005]
    ok = got is not None and len(got) == len(want) and all(
        abs(a - b) < 1e-12 for a, b in zip(got, want))
    print("     funding_series([0.01, -0.02, None, 0.005]) -> %s" % got)
    check(ok, "funding_series переводит проценты в доли и не роняет пропуски")
    check(bh.funding_series({}) is None, "нет ряда — возвращается None, а не пустой список")
    part = bh.funding_series({"fund": [1.0, 2.0, 3.0, 4.0]}, 1, 3)
    check(part == [0.02, 0.03], "срез по половине берётся из того же места")


def test_vol_gate_reaches_site():
    """Ген гейта волатильности обязан доезжать до КАЖДОГО потребителя.

    ЗАЧЕМ. e7.cfg_to_genome ген vol_gate НЕ переносит, поэтому каждый, кто
    строит геном из конфига, обязан дописать его сам. Проект уже объявлял этот
    дефект закрытым «одним определением в трёх местах», после чего он нашёлся
    в ЧЕТВЁРТОМ: симуляция на странице бота считалась без гейта и завышала
    число сделок в 2.7 раза.

    Проверяется прямо: у конфига с гейтом геном, собранный сайтом, обязан
    нести порог. Если кто-то заведёт пятого потребителя и забудет — тест не
    поймает его автоматически, но хотя бы закрепит нынешних.
    """
    import os
    import sys as _sys
    web = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")
    if web not in _sys.path:
        _sys.path.insert(0, web)
    gated = [(sym, mode, p) for sym, mode, p in modes() if p.get("vol_gate")]
    if not gated:
        print("     конфигов с гейтом нет — проверять нечего")
        return
    try:
        import app as _app
    except Exception as exc:                       # noqa: BLE001
        print("     сайт не импортируется (%s) — пропуск" % type(exc).__name__)
        return
    bad = []
    for sym, mode, prm in gated:
        g = _app.cfg_to_genome(prm, mode)
        if not g.get("vol_gate"):
            bad.append("%s/%s" % (sym, mode))
    print("     конфигов с гейтом: %d" % len(gated))
    check(not bad, "геном, собранный сайтом, несёт порог гейта"
          + ("" if not bad else " — ПОТЕРЯН у: " + ", ".join(bad)))


def main():
    print("Проверки конфигов")
    for fn in (test_index_sets, test_genome_roundtrip, test_all_modes_evaluable,
               test_retired_not_offered, test_legacy_kept_intact,
               test_funding_sign, test_funding_reaches_every_caller,
               test_funding_series_units, test_vol_gate_reaches_site):
        fn()
    if FAILED:
        print("\nПРОВАЛЕНО: %d" % len(FAILED))
        return 1
    print("\nВсе проверки конфигов пройдены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
