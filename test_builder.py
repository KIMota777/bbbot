# -*- coding: utf-8 -*-
"""ПРОВЕРКА КОНСТРУКТОРА СИГНАЛОВ (signal_builder.py).

Пять разделов — ровно то, без чего конструктору нельзя верить:

  1. ПРИЧИННОСТЬ (нет заглядывания вперёд). Для КАЖДОГО типа условия маска
     входов считается дважды: на полной истории и на её ПРЕФИКСЕ (первые 60%
     баров, контекст пересчитан с нуля). Значения до точки обрезки обязаны
     совпасть бит в бит. Дополнительно то же самое проверяется сквозь весь
     конвейер — по фактическим сделкам. Это главный тест: одно заглядывание
     превращает любую «находку» в самообман.

  2. СВЕРКА С ДВИЖКОМ. spec, повторяющий сетап dump_long с геномом DEFAULTS3
     (и отдельно breakout_long), обязан дать те же сделки, что
     signal_engine3.run_setup — вход в вход, выход в выход, цент в цент. Если
     бы конструктор считал сделки сам, его цифры разошлись бы с остальным
     проектом и сравнивать их было бы не с чем.

  3. ВАЛИДАЦИЯ. Кривые spec должны давать понятную ошибку по-русски, а не
     падение движка (страница сайта не имеет права падать от опечатки).

  4. EVALUATE. Честная проверка на четырёх идеях, включая заведомо мусорную
     (вход по часу и дню недели) и «идею», которая на самом деле — бета рынка
     (шорт по тренду в медвежий период). Показываются вердикты целиком.

  5. ВРЕМЯ. Один вызов evaluate обязан укладываться в 10-30 секунд.

Запуск: python test_builder.py     (вывод дублируется в test_builder_out.txt)
"""

import json
import sys
import time

import signal_builder as sb
import signal_engine3 as se3
import signal_stats as ss

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


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


FAILS = []


def check(name, ok, detail=""):
    tag = "OK  " if ok else "ПРОВАЛ"
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)
    return ok


def hr(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# --------------------------------------------------------------------------
# 1. ПРИЧИННОСТЬ
# --------------------------------------------------------------------------
# По одному представителю каждого типа условия (у level_dist — все три вида
# уровня, они считаются принципиально по-разному).
CAUSAL_CASES = [
    {"kind": "rsi", "period": 14, "op": "<", "value": 35},
    {"kind": "drop", "pct": 5, "days": 3},
    {"kind": "rise", "pct": 5, "days": 3},
    {"kind": "ema", "fast": 20, "slow": 100, "op": "cross_up"},
    {"kind": "ema", "fast": 20, "slow": 100, "op": "above"},
    {"kind": "ema_price", "period": 200, "op": "above"},
    {"kind": "macd", "fast": 12, "slow": 26, "signal": 9, "cond": "cross_up"},
    {"kind": "macd", "fast": 12, "slow": 26, "signal": 9, "cond": "hist_up"},
    {"kind": "breakout", "days": 20, "dir": "high"},
    {"kind": "breakout", "days": 10, "dir": "low"},
    {"kind": "candle", "dir": "green", "body_min": 0.4},
    {"kind": "regime", "allow": ["bull", "range"]},
    {"kind": "atr_rank", "op": ">", "value": 0.6},
    {"kind": "volume", "mult": 1.5, "window": 50},
    {"kind": "funding", "op": "<", "value": 0.005},
    {"kind": "level_dist", "level": "swing_low", "max_atr": 1.0, "pivot": 5},
    {"kind": "level_dist", "level": "round", "max_atr": 0.5},
    {"kind": "level_dist", "level": "volume_poc", "max_atr": 1.0,
     "window": 120},
    {"kind": "divergence", "period": 40, "dir": "bull", "rsi_period": 14},
    {"kind": "divergence", "period": 40, "dir": "bear"},
    {"kind": "hour", "from": 8, "to": 20},
    {"kind": "weekday", "days": [0, 1, 2, 3, 4]},
]


def test_causality(symbol="BTCUSDT", interval="240", cut=0.60):
    hr("1. ПРИЧИННОСТЬ: маска на префиксе истории == маска на полной истории")
    full = sb.load_series(symbol, interval)
    n = full["n"]
    k = int(n * cut)
    ts_cut = full["c4"][k - 1][0] + full["bar_ms"]
    c4p = full["c4"][:k]
    c15p = [c for c in full["c15"] if c[0] < ts_cut]
    t0 = time.time()
    pref = sb.series_from_candles(symbol, interval, c4p, c15p)
    print(f"  {symbol} {interval}м: полная история {n} баров, префикс {k} "
          f"({time.strftime('%Y-%m-%d', time.gmtime(ts_cut / 1000))}), "
          f"контекст префикса пересчитан за {time.time() - t0:.1f}с")
    print(f"  типов условий в языке: {len(sb.COND_DEFS)}, проверяем "
          f"{len(CAUSAL_CASES)} вариантов\n")
    covered = set()
    for cond in CAUSAL_CASES:
        spec = dict(sb.EXAMPLE_SPEC)
        spec["symbol"], spec["interval"] = symbol, interval
        spec["entry"] = [cond]
        norm, _ = sb.validate(spec)
        mf, wf, pf_ = sb.entry_mask(norm, full)
        mp, wp, pp_ = sb.entry_mask(norm, pref)
        diff = [i for i in range(k) if mf[i] != mp[i]]
        covered.add(cond["kind"])
        check(f"{pp_[0]['label']:34} входов на префиксе {sum(mp):5}",
              not diff and wf == wp,
              "" if not diff else f"расхождений {len(diff)}, первое на баре "
                                  f"{diff[0]}")
    check("покрыты все типы условий",
          covered == set(sb.COND_DEFS),
          f"не проверены: {sorted(set(sb.COND_DEFS) - covered)}")

    # --- сквозная проверка: те же СДЕЛКИ, а не только маска ---
    spec = {"symbol": symbol, "interval": interval, "side": "long",
            "entry": [{"kind": "rsi", "period": 14, "op": "<", "value": 35},
                      {"kind": "breakout", "days": 10, "dir": "low"},
                      {"kind": "volume", "mult": 1.2, "window": 40}],
            "exit": {"stop": {"type": "swing", "value": 10, "buf_atr": 0.3},
                     "tp": {"type": "atr", "value": 3.0}, "timeout_days": 5},
            "grid": {"levels": 2, "step_atr": 0.8, "mult": 1.4},
            "lev": 5, "cooldown_bars": 4}
    rf = sb.build_run(spec, ser=full, collect_diag=False)
    rp = sb.build_run(spec, ser=pref, collect_diag=False)
    key = lambda t: (t["entry_ts"], t["exit_ts"], round(t["pnl"], 9))
    af = [key(t) for t in rf["trades"] if t["exit_ts"] < ts_cut]
    ap = [key(t) for t in rp["trades"] if t["exit_ts"] < ts_cut]
    check(f"сделки до точки обрезки совпадают ({len(ap)} шт)", af == ap,
          "" if af == ap else f"полная {len(af)} vs префикс {len(ap)}")


# --------------------------------------------------------------------------
# 2. СВЕРКА С ДВИЖКОМ
# --------------------------------------------------------------------------
def _cmp_trades(a, b):
    fa = [(t["entry_ts"], t["exit_ts"], round(t["pnl"], 9), t["r"],
           t["reason"], t["grid_fills"]) for t in a]
    fb = [(t["entry_ts"], t["exit_ts"], round(t["pnl"], 9), t["r"],
           t["reason"], t["grid_fills"]) for t in b]
    return fa == fb, fa, fb


def test_engine_match(symbol="BTCUSDT", interval="240", lev=5):
    hr("2. СВЕРКА: spec конструктора == signal_engine3.run_setup")
    ser = sb.load_series(symbol, interval)
    g = se3.default_genome()
    print(f"  геном DEFAULTS3: слив {g['drop_frac']:.0%} за {g['drop_days']}д, "
          f"RSI{se3.RSI_SET[g['rsi_idx']]}<{g['rsi_os']}, зелёная свеча, стоп "
          f"{g['stop_atr_k']}*ATR (cap {g['stop_cap']:.0%}), тейк "
          f"{g['tp_r']}R, таймаут {g['hold_days']}д, сетка выкл")

    cases = [
        ("dump_long", [{"kind": "drop", "pct": g["drop_frac"] * 100,
                        "days": g["drop_days"]},
                       {"kind": "rsi", "period": se3.RSI_SET[g["rsi_idx"]],
                        "op": "<", "value": g["rsi_os"]},
                       {"kind": "candle", "dir": "green", "body_min": 0.0}],
         "long"),
        ("rally_short", [{"kind": "rise", "pct": g["drop_frac"] * 100,
                          "days": g["drop_days"]},
                         {"kind": "rsi", "period": se3.RSI_SET[g["rsi_idx"]],
                          "op": ">", "value": 100 - g["rsi_os"]},
                         {"kind": "candle", "dir": "red", "body_min": 0.0}],
         "short"),
        ("breakout_long", [{"kind": "breakout", "days": g["break_days"],
                            "dir": "high"}], "long"),
        ("breakout_short", [{"kind": "breakout", "days": g["break_days"],
                             "dir": "low"}], "short"),
    ]
    for setup, entry, side in cases:
        ext = se3.build_ext(setup, g, ser["c4"], interval_min=int(interval))
        warm = ext["warm"]
        r3 = se3.run_setup(setup, g, ser["c4"], ser["ctx"], ser["c15"],
                           ser["ts15"], lev, symbol=symbol,
                           signal_range=(warm, ser["n"]),
                           interval_min=int(interval))
        spec = {"symbol": symbol, "interval": interval, "side": side,
                "entry": entry,
                "exit": {"stop": {"type": "atr", "value": g["stop_atr_k"]},
                         "tp": {"type": "r", "value": g["tp_r"]},
                         "timeout_days": g["hold_days"],
                         "max_stop_pct": g["stop_cap"] * 100},
                "grid": {"levels": g["grid_levels"],
                         "step_atr": g["grid_step_atr"],
                         "mult": g["grid_mult"]},
                "lev": lev, "cooldown_bars": g["cooldown"]}
        rb = sb.build_run(spec, signal_range=(warm, ser["n"]))
        same, fa, fb = _cmp_trades(r3["trades"], rb["trades"])
        check(f"{setup:15} сделок движок {len(fa):3} / конструктор {len(fb):3}",
              same and len(fa) > 0,
              "" if same else f"первое расхождение: "
                              f"{next((x for x, y in zip(fa, fb) if x != y), None)}")
        if setup == "dump_long" and same:
            s3 = ss.full_stats(r3, lev, t0_ms=ser["c4"][warm][0])
            sb_ = ss.full_stats(rb, lev, t0_ms=rb["t0_ms"])
            check("signal_stats.full_stats читает результат конструктора",
                  (s3["n"], s3["exp_r"], s3["pf"], s3["dd_pct"])
                  == (sb_["n"], sb_["exp_r"], sb_["pf"], sb_["dd_pct"]),
                  f"n={sb_['n']} exp_r={sb_['exp_r']} PF={sb_['pf']} "
                  f"DD={sb_['dd_pct']}%")

    # сетка усреднения тоже идёт ЧУЖОЙ механикой — сверяем с движком
    g2 = se3.default_genome(grid_levels=3, grid_step_atr=0.7, grid_mult=1.4,
                            be_after_r=0.5, tp_mode=1, tp_atr=2.5)
    ext = se3.build_ext("dump_long", g2, ser["c4"], interval_min=int(interval))
    r3 = se3.run_setup("dump_long", g2, ser["c4"], ser["ctx"], ser["c15"],
                       ser["ts15"], lev, symbol=symbol,
                       signal_range=(ext["warm"], ser["n"]),
                       interval_min=int(interval))
    spec = {"symbol": symbol, "interval": interval, "side": "long",
            "entry": [{"kind": "drop", "pct": g2["drop_frac"] * 100,
                       "days": g2["drop_days"]},
                      {"kind": "rsi", "period": 14, "op": "<",
                       "value": g2["rsi_os"]},
                      {"kind": "candle", "dir": "green", "body_min": 0.0}],
            "exit": {"stop": {"type": "atr", "value": g2["stop_atr_k"]},
                     "tp": {"type": "atr", "value": g2["tp_atr"]},
                     "be_after_r": g2["be_after_r"],
                     "timeout_days": g2["hold_days"],
                     "max_stop_pct": g2["stop_cap"] * 100},
            "grid": {"levels": 3, "step_atr": 0.7, "mult": 1.4},
            "lev": lev, "cooldown_bars": 0}
    rb = sb.build_run(spec, signal_range=(ext["warm"], ser["n"]))
    same, fa, fb = _cmp_trades(r3["trades"], rb["trades"])
    check(f"сетка 3 колена + безубыток + тейк в ATR ({len(fa)} сделок, "
          f"средний налив {rb['avg_grid_fills']})", same and len(fa) > 0)

    # трейлинг-выход (tp_mode=2) — тот же ряд трейлинга, что у движка
    g3 = se3.default_genome(tp_mode=2, trail_bars=30)
    ext = se3.build_ext("dump_long", g3, ser["c4"], interval_min=int(interval))
    r3 = se3.run_setup("dump_long", g3, ser["c4"], ser["ctx"], ser["c15"],
                       ser["ts15"], lev, symbol=symbol,
                       signal_range=(ext["warm"], ser["n"]),
                       interval_min=int(interval))
    spec["exit"] = {"stop": {"type": "atr", "value": g3["stop_atr_k"]},
                    "tp": {"type": "r", "value": 2.0},
                    "trail": {"bars": 30},
                    "timeout_days": g3["hold_days"],
                    "max_stop_pct": g3["stop_cap"] * 100}
    spec["grid"] = {"levels": 1, "step_atr": 1.0, "mult": 1.5}
    rb = sb.build_run(spec, signal_range=(ext["warm"], ser["n"]))
    same, fa, fb = _cmp_trades(r3["trades"], rb["trades"])
    check(f"трейлинг за 30 барами ({len(fa)} сделок, выходов трейлингом "
          f"{rb['trail_exits']})", same and len(fa) > 0)


# --------------------------------------------------------------------------
# 3. ВАЛИДАЦИЯ
# --------------------------------------------------------------------------
BAD_SPECS = [
    ("неизвестное условие",
     {"entry": [{"kind": "moon_phase", "value": 3}]}),
    ("неизвестный параметр условия",
     {"entry": [{"kind": "rsi", "period": 14, "op": "<", "value": 30,
                 "colour": "red"}]}),
    ("значение вне диапазона",
     {"entry": [{"kind": "rsi", "period": 14, "op": "<", "value": 300}]}),
    ("недопустимое значение перечисления",
     {"entry": [{"kind": "rsi", "period": 14, "op": "~", "value": 30}]}),
    ("строка вместо числа",
     {"entry": [{"kind": "rsi", "period": "четырнадцать", "op": "<",
                 "value": 30}]}),
    ("пустой список условий", {"entry": []}),
    ("entry не список", {"entry": {"kind": "rsi"}}),
    ("условие без ключа kind", {"entry": [{"period": 14, "value": 30}]}),
    ("нет стопа", {"entry": [{"kind": "rsi", "period": 14, "op": "<",
                              "value": 30}], "exit": {"tp": {"type": "r",
                                                             "value": 2}}}),
    ("неизвестное поле верхнего уровня",
     {"entry": [{"kind": "rsi"}], "magic": 1}),
    ("неизвестное поле exit",
     {"entry": [{"kind": "rsi"}],
      "exit": {"stop": {"type": "atr", "value": 2}, "profit": 3}}),
    ("несуществующая монета", {"symbol": "PEPEUSDT",
                               "entry": [{"kind": "rsi"}]}),
    ("нет такого ТФ у монеты", {"symbol": "DOGEUSDT", "interval": "60",
                                "entry": [{"kind": "rsi"}]}),
    ("день недели вне 0..6",
     {"entry": [{"kind": "weekday", "days": [0, 9]}]}),
    ("EMA fast >= slow",
     {"entry": [{"kind": "ema", "fast": 200, "slow": 50, "op": "above"}]}),
    ("плечо вне диапазона", {"lev": 100, "entry": [{"kind": "rsi"}]}),
]


def test_validation():
    hr("3. ВАЛИДАЦИЯ: кривой spec -> понятная ошибка, а не падение")
    base = {"symbol": "BTCUSDT", "interval": "240", "side": "long",
            "exit": {"stop": {"type": "atr", "value": 2.0}}}
    for name, patch in BAD_SPECS:
        spec = dict(base)
        spec.update(patch)
        try:
            sb.validate(spec)
            check(name, False, "ошибки НЕ БЫЛО — spec принят молча")
        except sb.SpecError as e:
            check(name, True, str(e)[:96])
        except Exception as e:                       # noqa: BLE001
            check(name, False, f"не SpecError, а {type(e).__name__}: {e}")
    # обратная сторона: корректные формы обязаны приниматься
    good = [
        ("вложенная форма условия",
         {"entry": [{"rsi": {"period": 14, "op": "<", "value": 30}}]}),
        ("ключ type вместо kind",
         {"entry": [{"type": "rsi", "period": 14, "op": "<", "value": 30}]}),
        ("параметры по умолчанию", {"entry": [{"kind": "macd"}]}),
        ("интервал числом", {"interval": 240, "entry": [{"kind": "rsi"}]}),
        ("level_dist: старое имя kind во вложенной форме",
         {"entry": [{"level_dist": {"kind": "round", "max_atr": 0.5}}]}),
    ]
    for name, patch in good:
        spec = dict(base)
        spec.update(patch)
        try:
            norm, w = sb.validate(spec)
            check(name, True, f"ок ({norm['entry'][0]['kind']})")
        except Exception as e:                       # noqa: BLE001
            check(name, False, f"{type(e).__name__}: {e}")
    # предупреждения (не ошибки)
    spec = dict(base)
    spec["entry"] = [{"kind": "rsi", "period": 14, "op": "<", "value": 30}]
    spec["exit"] = {"stop": {"type": "pct", "value": 5.0},
                    "tp": {"type": "r", "value": 2.0},
                    "trail": {"bars": 20}, "max_stop_pct": 12.0}
    spec["lev"] = 20
    norm, w = sb.validate(spec)
    check("предупреждения вместо ошибок (trail+tp, плечо x20 против стопа)",
          len(w) == 2, " | ".join(x[:70] for x in w))


# --------------------------------------------------------------------------
# 4. EVALUATE
# --------------------------------------------------------------------------
IDEAS = [
    ("капитуляция-лонг BTC (классика проекта)",
     {"symbol": "BTCUSDT", "interval": "240", "side": "long",
      "entry": [{"kind": "drop", "pct": 6, "days": 2},
                {"kind": "rsi", "period": 14, "op": "<", "value": 30},
                {"kind": "candle", "dir": "green", "body_min": 0.0}],
      "exit": {"stop": {"type": "atr", "value": 2.0},
               "tp": {"type": "r", "value": 2.0}, "timeout_days": 30},
      "grid": {"levels": 1, "step_atr": 1.0, "mult": 1.5},
      "lev": 5, "cooldown_bars": 0}),
    ("ЗАВЕДОМО МУСОР: вход по часу и дню недели, без рыночной логики",
     {"symbol": "SOLUSDT", "interval": "240", "side": "long",
      "entry": [{"kind": "hour", "from": 12, "to": 16},
                {"kind": "weekday", "days": [1, 3]}],
      "exit": {"stop": {"type": "atr", "value": 2.0},
               "tp": {"type": "r", "value": 1.5}, "timeout_days": 3},
      "grid": {"levels": 1, "step_atr": 1.0, "mult": 1.5},
      "lev": 5, "cooldown_bars": 0, "tried_variants": 40}),
    ("шорт по тренду ETH (проверка: не бета ли это медвежьего рынка)",
     {"symbol": "ETHUSDT", "interval": "240", "side": "short",
      "entry": [{"kind": "ema_price", "period": 200, "op": "below"},
                {"kind": "rsi", "period": 14, "op": ">", "value": 55},
                {"kind": "candle", "dir": "red", "body_min": 0.3}],
      "exit": {"stop": {"type": "atr", "value": 2.0},
               "tp": {"type": "r", "value": 2.0}, "timeout_days": 5},
      "grid": {"levels": 1, "step_atr": 1.0, "mult": 1.5},
      "lev": 5, "cooldown_bars": 2}),
    ("сетка на откате к POC + объём (DOGE 15м, всё вместе)",
     {"symbol": "DOGEUSDT", "interval": "15", "side": "long",
      "entry": [{"kind": "level_dist", "level": "volume_poc", "max_atr": 0.7,
                 "window": 200},
                {"kind": "volume", "mult": 1.5, "window": 60},
                {"kind": "rsi", "period": 14, "op": "<", "value": 40},
                {"kind": "regime", "allow": ["bull", "range"]}],
      "exit": {"stop": {"type": "swing", "value": 12, "buf_atr": 0.3},
               "tp": {"type": "pct", "value": 1.2}, "be_after_r": 0.8,
               "timeout_days": 2},
      "grid": {"levels": 3, "step_atr": 0.6, "mult": 1.4},
      "lev": 8, "cooldown_bars": 12, "tried_variants": 12}),
]


def test_evaluate():
    hr("4. EVALUATE: честная проверка идей (в том числе заведомо мусорной)")
    times = []
    for name, spec in IDEAS:
        print(f"\n--- {name}")
        t0 = time.time()
        res = sb.evaluate(spec)
        el = time.time() - t0
        times.append((name, el, res))
        sb.print_report(res)
        print(f"  время: {el:.1f}с")
        check(f"evaluate отработал: {name[:44]}",
              res["verdict"]["status"] in ("работает", "наблюдение",
                                           "не подтверждён"),
              res["verdict"]["status"])
        check("результат сериализуется в JSON",
              isinstance(json.dumps(res, ensure_ascii=False), str))
    # мусорная идея обязана быть отвергнута
    trash = times[1][2]
    check("мусорная идея (час+день недели) НЕ подтверждена",
          trash["verdict"]["status"] != "работает",
          f"вердикт: {trash['verdict']['status']}, сделок на холдоуте "
          f"{trash['holdout']['n']}")
    return times


# --------------------------------------------------------------------------
# 5. ВРЕМЯ И ПРОЧЕЕ
# --------------------------------------------------------------------------
def test_speed(times):
    hr("5. ВРЕМЯ: один вызов evaluate должен укладываться в 10-30 секунд")
    for name, el, res in times:
        d = res["data"]
        print(f"  {el:6.1f}с  {d['symbol']:9} {d['interval']:>3}м  "
              f"{d['bars']:6} баров, условий {len(res['spec']['entry'])}, "
              f"сделок train/holdout {res['train']['n']}/{res['holdout']['n']}"
              f"  — {name[:40]}")
    worst = max(t for _, t, _ in times)
    check(f"худший вызов {worst:.1f}с", worst <= 30.0)
    t0 = time.time()
    sb.build_run(IDEAS[0][1])
    check(f"один build_run на прогретом кэше: {time.time() - t0:.2f}с",
          time.time() - t0 <= 3.0)
    t0 = time.time()
    d = sb.describe()
    check(f"describe() отдаёт контракт за {time.time() - t0:.2f}с: "
          f"{len(d['conditions'])} условий, "
          f"{sum(len(v['params']) for v in d['conditions'].values())} параметров",
          isinstance(json.dumps(d, ensure_ascii=False), str))


def test_sanity():
    hr("6. КОНТРОЛЬНЫЕ СВОЙСТВА")
    spec = dict(IDEAS[0][1])
    r = sb.build_run(spec)
    # near-miss не влияют на баланс
    bal = sb.START + sum(t["pnl"] for t in r["trades"])
    check("баланс = старт + сумма сделок (near-miss ничего не двигают)",
          abs(bal - r["balance"]) < 1e-9,
          f"{r['balance']:.6f} vs {bal:.6f}, near-miss {len(r['near_misses'])}")
    # стоп всегда внутри ликвидации
    lev = spec["lev"]
    lf = sb.se2.liq_frac(lev)
    worst = max((t["stop_pct"] / 100 for t in r["trades"]), default=0)
    check(f"стоп не выходит за 80% пути до ликвидации (x{lev}: "
          f"{worst * 100:.2f}% при пределе {0.8 * lf * 100:.2f}%)",
          worst <= 0.8 * lf + 1e-12)
    # одна позиция за раз
    ok = all(r["trades"][i]["entry_ts"] >= r["trades"][i - 1]["exit_ts"]
             for i in range(1, len(r["trades"])))
    check("позиции не перекрываются", ok)
    # train + holdout = все сделки (нет дырок и дублей на границе)
    rt = sb.build_run(spec, part="train")
    rh = sb.build_run(spec, part="holdout")
    ser = sb.load_series(spec["symbol"], spec["interval"])
    b_tr = ser["c4"][ser["hold_i"]][0]
    n_all = len([t for t in r["trades"] if t["signal_ts"] < b_tr])
    check(f"train ({len(rt['trades'])}) + holdout ({len(rh['trades'])}) "
          f"согласованы с общим прогоном ({len(r['trades'])})",
          len(rt["trades"]) == n_all)
    # перестановка условий не меняет результат (И коммутативно)
    spec2 = json.loads(json.dumps(spec))
    spec2["entry"] = list(reversed(spec2["entry"]))
    r2 = sb.build_run(spec2)
    same, _, _ = _cmp_trades(r["trades"], r2["trades"])
    check("порядок условий не влияет на сделки", same)
    # издержки реально считаются (без них результат другой)
    ev = sb.evaluate(spec, n_ctrl=5, n_pert=5)
    c = ev["costs"]
    check(f"издержки измерены: {c['cost_usd']:+.2f}$ "
          f"({c['share_of_gross_profit_pct']}% валовой прибыли)",
          c["cost_usd"] > 0)


def main():
    t0 = time.time()
    print("ПРОВЕРКА КОНСТРУКТОРА СИГНАЛОВ signal_builder.py")
    print(f"данные: {sb.available_data()}")
    print(f"пороги вердикта: сделок>={sb.MIN_TRADES}, exp_r>{sb.MIN_EXP_R}, "
          f"PF>={sb.MIN_PF}, доля зёрен контроля не хуже <{sb.MAX_CTRL_SHARE}, "
          f"устойчивость>={sb.MIN_ROBUST_POS}")
    test_causality()
    test_engine_match()
    test_validation()
    times = test_evaluate()
    test_speed(times)
    test_sanity()
    hr("ИТОГ")
    if FAILS:
        print(f"ПРОВАЛОВ: {len(FAILS)}")
        for f in FAILS:
            print(f"  - {f}")
    else:
        print("все проверки пройдены")
    print(f"общее время: {time.time() - t0:.1f}с")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.stdout = Tee("test_builder_out.txt")
    code = main()
    sys.stdout.flush()
    sys.exit(code)
