# -*- coding: utf-8 -*-
"""Помесячная и погодовая статистика ДЛЯ КАЖДОГО режима, а не только final.

ЗАЧЕМ. Блок «Статистика за 3.2 года» на странице бота брался из
/api/finalstats/<монета> — предпосчёт `finalize_final_bots.py`, у которого
ключ только монета, без режима. Поэтому у всех нефинальных режимов страница
показывала цифры финального бота той же монеты как свои. Это убрано, но взамен
у новых конфигов не осталось ничего, и правильный ответ — не прятать блок, а
посчитать его для каждого режима отдельно.

КАК СЧИТАЕТСЯ. Тем же способом, что и уровень на карточке, и это главное:
  * плечо x5 у всех, чтобы режимы были сравнимы между собой;
  * копеечные выходы обнулены (|PnL| < 1% маржи цикла) — иначе в статистику
    попадают циклы, которые заплатили комиссию и не сдвинулись;
  * обе половины истории считаются ПОРОЗНЬ, каждая от своих ста процентов.

Последнее делает разложение точным. Итог половины в этом проекте — сумма PnL,
делённая на стартовый капитал, а не сложный процент. Значит доход месяца это
просто его доля той же суммы, и сумма месяцев обучения даёт в точности число
«обучение» на карточке, а сумма месяцев холдоута — число «холдоут». Никакой
третьей арифметики на странице не появляется.

ЧЕГО ЗДЕСЬ НЕТ. Это по-прежнему история, а не обещание: months помечены, какая
половина обучающая, а какая проверочная, и на странице это видно цветом.

Запуск: python build_modestats.py
"""
import datetime as dt
import json
import os
import sys

import all_configs_honest as ach
import bots_honest as bh
import config
import evolution2 as e2
import evolution7 as e7
import ext_data as xd

e2.BARS_PER_DAY = 96
OUT_DIR = os.path.join("webapp", "data", "modestats")
LEV = 5.0


def month_key(ts):
    d = dt.datetime.fromtimestamp((ts / 1000.0 if ts > 1e11 else ts), dt.UTC)
    return "%04d-%02d" % (d.year, d.month)


def year_key(ts):
    d = dt.datetime.fromtimestamp((ts / 1000.0 if ts > 1e11 else ts), dt.UTC)
    return "%04d" % d.year


def half_stats(part, g, lev):
    """Разбор одной половины: месяцы, годы, итог, просадка, винрейт.

    Копеечные циклы обнуляются, но НЕ выбрасываются из счёта сделок отдельной
    строкой: их доля показывается рядом, потому что «сделок 300, из них 250
    ни о чём» и «сделок 50» — разные вещи, и вторая честнее только если
    первую видно.
    """
    evs = []
    bh.run_at(part["candles"], part["pre"], g, part["filt"], lev, events=evs)
    closes = [e for e in evs if e["type"] == "close" and e["pnl"] is not None]
    if not closes:
        return None

    months, years = {}, {}
    bal = e2.START
    peak, dd = bal, 0.0
    n_real = n_win = n_tiny = 0
    for e in sorted(closes, key=lambda x: x["t"]):
        tiny = bh.is_tiny(e["pnl"])
        pnl = 0.0 if tiny else float(e["pnl"])
        if tiny:
            n_tiny += 1
        else:
            n_real += 1
            if pnl > 0:
                n_win += 1
        bal += pnl
        peak = max(peak, bal)
        if peak > 0:
            dd = max(dd, (peak - bal) / peak)
        for bucket, key in ((months, month_key(e["t"])),
                            (years, year_key(e["t"]))):
            b = bucket.setdefault(key, dict(pnl=0.0, real=0, win=0, tiny=0,
                                            bal_lo=bal, bal_hi=bal))
            b["pnl"] += pnl
            b["tiny"] += 1 if tiny else 0
            if not tiny:
                b["real"] += 1
                b["win"] += 1 if pnl > 0 else 0
            b["bal_lo"] = min(b["bal_lo"], bal)
            b["bal_hi"] = max(b["bal_hi"], bal)

    def fmt(bucket):
        out = []
        for k in sorted(bucket):
            b = bucket[k]
            out.append(dict(
                period=k,
                ret=round(100.0 * b["pnl"] / e2.START, 2),
                trades=b["real"], tiny=b["tiny"],
                wr=round(100.0 * b["win"] / b["real"], 1) if b["real"] else None))
        return out

    return dict(
        ret=round(100.0 * (bal - e2.START) / e2.START, 2),
        dd=round(100.0 * dd, 2),
        trades=n_real, tiny=n_tiny,
        tiny_share=round(100.0 * n_tiny / max(n_real + n_tiny, 1), 1),
        wr=round(100.0 * n_win / n_real, 1) if n_real else None,
        months=fmt(months), years=fmt(years))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    pct5 = xd.fetch_daily_pct5()
    done = failed = 0
    # Список падений копится и печатается в конце. Молчаливый except здесь
    # уже однажды съел 62 отказа из 63, и отчёт бодро посчитался по одному
    # конфигу — так что теперь провал виден и он не «ноль сделок».
    problems = []

    for sym, modes in config.SYMBOL_PARAMS.items():
        for mode, p in modes.items():
            if not isinstance(p, dict):
                continue
            try:
                g = ach.with_defaults(e7.cfg_to_genome(p, mode))
                if p.get("vol_gate"):
                    g["vol_gate"] = float(p["vol_gate"])
                parts = ach.halves(sym, g, pct5)
                res = {}
                for half in ("train", "hold"):
                    res[half] = half_stats(parts[half], g, LEV)
                if res["train"] is None and res["hold"] is None:
                    problems.append("%s/%s: ни одной сделки" % (sym, mode))
                    failed += 1
                    continue
                blob = dict(
                    symbol=sym, mode=mode, lev=LEV,
                    own_lev=p.get("lev", 5),
                    retired=bool(config.retire_reason(sym, mode))
                    if hasattr(config, "retire_reason") else False,
                    train=res["train"], hold=res["hold"],
                    generated=dt.datetime.now(dt.UTC).strftime(
                        "%Y-%m-%d %H:%M UTC"))
                with open(os.path.join(OUT_DIR, "%s_%s.json" % (sym, mode)),
                          "w", encoding="utf-8") as fh:
                    json.dump(blob, fh, ensure_ascii=False)
                t = res["train"] or {}
                h = res["hold"] or {}
                print("%-8s %-10s обуч %+7.1f%% (%2d мес) | холд %+7.1f%% "
                      "(%2d мес) | сделок %3d/%3d | копеек %.0f%%"
                      % (sym.replace("USDT", ""), mode,
                         t.get("ret", 0.0), len(t.get("months", [])),
                         h.get("ret", 0.0), len(h.get("months", [])),
                         t.get("trades", 0), h.get("trades", 0),
                         max(t.get("tiny_share", 0), h.get("tiny_share", 0))))
                done += 1
            except Exception as exc:               # noqa: BLE001
                problems.append("%s/%s: %s: %s"
                                % (sym, mode, type(exc).__name__, exc))
                failed += 1

    print("\nпосчитано: %d, не вышло: %d" % (done, failed))
    for pr in problems:
        print("  ! %s" % pr)
    print("-> %s" % OUT_DIR)
    return 1 if failed and not done else 0


if __name__ == "__main__":
    sys.exit(main())
