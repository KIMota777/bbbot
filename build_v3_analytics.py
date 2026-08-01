# -*- coding: utf-8 -*-
"""Аналитика страниц для отбора v3 (движок signal_engine3, мультимонетность).

Что делает: по каждому сетапу из setups_v3.json прогоняет signal_engine3 по
ВСЕЙ истории его монеты и складывает всё, что нужно странице /v3/<ключ>:

  trades        — сделки с полями сетки (grid_fills, avg_entry, exit_kind) и
                  ВОССТАНОВЛЕННЫМИ точками доливок (для маркеров на графике);
  near_misses   — «почти сигналы» с гипотетикой (как в v2);
  stats         — полная статистика через signal_stats.full_stats;
  series        — ряды для графика: ТОЛЬКО те, что реально являются воротами
                  этого сетапа (уровень Дончиана, SMA тренда/отката, порог
                  слива, RSI с порогом), плюс явно помеченные «справочно»
                  (EMA, RSI там, где он не ворото) и линия стопа;
  equity/drawdown/monthly — кривые из full_stats.

Файлы: webapp/data/v3_<сетап>@<МОНЕТА>@<ТФ>.json, ts В СЕКУНДАХ, точки серий
дедуплицированы по времени (дубликат ts роняет график целиком) и прорежены
до MAX_SERIES_POINTS.

Пока отбор не запускался (нет setups_v3.json) — печатает это и выходит с
кодом 0: страницы сайта показывают заглушку, а не падают.

ЗАПУСК
  python build_v3_analytics.py
  python build_v3_analytics.py --setups X --out-dir Y --only key1,key2
"""

import argparse
import bisect
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import evolution as ev
import signal_engine3 as se3
import signal_stats as ss

SETUPS = "setups_v3.json"
OUT_DIR = os.path.join("webapp", "data")
DAYS = 1150
MAX_SERIES_POINTS = 3000
DEF_LEV = 10

REG_RU = {0: "бычий", 1: "боковик", 2: "медвежий"}

# подписи рядов графика: ключ -> (название, роль, цвет-подсказка)
# роль: «ворото» — от него зависит вход; «стоп»/«выход» — сопровождение;
# «справочно» — контекст, на сигнал не влияет.
SERIES_META = {
    "level":      ("уровень пробоя (Дончиан)", "ворото"),
    "level_trig": ("порог входа (уровень + буфер)", "ворото"),
    "sma_slow":   ("SMA тренда", "ворото"),
    "sma_fast":   ("SMA отката", "ворото"),
    "sma_trend":  ("SMA фильтра направления", "ворото"),
    "move_lvl":   ("порог движения (слив/рост)", "ворото"),
    "rsi":        ("RSI", "ворото"),
    "rsi_thr":    ("порог RSI", "ворото"),
    "trail":      ("трейлинг-стоп", "выход"),
    "stop_line":  ("стоп, если войти на этом баре", "стоп"),
    "ema50":      ("EMA 50", "справочно"),
    "ema200":     ("EMA 200", "справочно"),
}


def tf_label(iv):
    iv = int(iv)
    return f"{iv // 60}ч" if iv % 60 == 0 else f"{iv}м"


def split_key(key, rec=None):
    """«<сетап>@<МОНЕТА>@<ТФ>» -> (сетап, монета, ТФ)."""
    rec = rec or {}
    parts = str(key).split("@")
    setup, symbol, iv = parts[0], None, None
    for p in parts[1:]:
        if p.isdigit():
            iv = int(p)
        elif p:
            symbol = p.upper()
    setup = str(rec.get("setup") or setup)
    symbol = str(rec.get("symbol") or symbol or "BTCUSDT").upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    try:
        iv = int(rec.get("interval_min") or iv or 240)
    except (TypeError, ValueError):
        iv = 240
    return setup, symbol, iv


def dedup(points):
    """[[ts, значение]] -> отсортированный ряд БЕЗ дубликатов времени.

    Библиотека графиков падает на двух точках с одинаковым ts и обрывает
    скрипт страницы целиком, поэтому дедупликация обязательна для всех рядов,
    а не «на всякий случай»."""
    out = {}
    for p in points:
        if p is None or len(p) < 2 or p[1] is None:
            continue
        try:
            ts = int(p[0])
            v = float(p[1])
        except (TypeError, ValueError):
            continue
        if v != v:                                # NaN
            continue
        out[ts] = v
    return [[t, out[t]] for t in sorted(out)]


def thin(points, max_points=MAX_SERIES_POINTS):
    return ss.thin(dedup(points), max_points)


# --------------------------------------------------------------- геном/конфиг
def make_genome(rec):
    """Полный геном v3: DEFAULTS3 + значения конфига, зажатые в границы."""
    g = dict(se3.DEFAULTS3)
    src = rec.get("genome") or {}
    if not src and any(k in rec for k in se3.GENES3):
        src = rec                                  # конфиг = сам геном
    clamped = []
    for k, v in src.items():
        if k not in se3.GENES3:
            continue
        lo, hi, is_int = se3.GENES3[k]
        try:
            val = int(round(float(v))) if is_int else float(v)
        except (TypeError, ValueError):
            continue
        fixed = min(max(val, lo), hi)
        if is_int:
            fixed = int(fixed)
        if fixed != val:
            clamped.append(f"{k}: {val} -> {fixed}")
        g[k] = fixed
    return g, clamped


# ------------------------------------------------------------------- доливки
def grid_adds(tr, g, atr_b, c15, ts15, lev):
    """Восстановленные точки доливок сетки для маркеров графика.

    Движок хранит только ЧИСЛО налившихся колен (grid_fills), а не их время.
    Цены колен детерминированы (se3.plan_grid — та же функция, что зовёт сам
    движок), поэтому время находим первым касанием цены по 15м-барам в порядке
    колен — ровно так их исполняет simulate_grid_trade.

    Возвращает (точки, средняя цена по восстановленным коленам). Средняя
    сверяется с avg_entry движка: совпало — реконструкция ТА ЖЕ, что была на
    самом деле, а не похожая."""
    side = tr["side"]
    sgn = 1 if side == "L" else -1
    levels = int(g["grid_levels"])
    fills = int(tr.get("grid_fills") or 1)
    need = fills - 1
    if levels <= 1 or need <= 0 or not atr_b:
        return [], None
    fill0 = tr["entry"] * (1 + sgn * se3.SLIP)
    q0, adds, m_k = se3.plan_grid(side, fill0, levels,
                                  float(g["grid_step_atr"]) * atr_b,
                                  float(g["grid_mult"]), lev)
    i0 = bisect.bisect_left(ts15, tr["entry_ts"])
    i1 = bisect.bisect_left(ts15, tr["exit_ts"])
    out, k = [], 0
    for i in range(i0, min(i1, len(c15) - 1) + 1):
        lo, hi = c15[i][3], c15[i][2]
        while k < need and k < len(adds):
            px, qty = adds[k]
            if (lo <= px) if sgn == 1 else (hi >= px):
                out.append(dict(ts=ts15[i] // 1000, px=round(px, 8),
                                lvl=k + 2, margin=round(m_k[k + 1], 3)))
                k += 1
            else:
                break
        if k >= need:
            break
    # средняя цена позиции по плану сетки (для сверки с движком)
    qs = q0 * fill0
    qq = q0
    for j in range(min(fills - 1, len(adds))):
        px, qty = adds[j]
        qs += px * qty
        qq += qty
    return out, (qs / qq if qq else None)


# --------------------------------------------------------------------- серии
def build_series(setup, g, c4, ctx, ext, iv, i_from):
    """Ряды графика в барах СВОЕГО ТФ (ts в секундах).

    Кладём только то, что имеет смысл для этого сетапа: у пробоя — уровень
    Дончиана, у пулбэка — две SMA, у слива/перегрева — ценовой порог движения
    и RSI. Остальное помечено «справочно» в SERIES_META, чтобы страница
    не выдавала контекст за ворото."""
    sgn = 1 if setup.endswith("long") else -1
    period = se3.RSI_SET[int(g["rsi_idx"])]
    rsi_arr = ctx["rsi"][period]
    keys = ["rsi", "ema50", "ema200", "stop_line"]
    if setup.startswith("breakout"):
        keys += ["level"]
        if float(g["break_buf_atr"]) > 0:
            keys += ["level_trig"]
    elif setup.startswith("pullback"):
        keys += ["sma_slow", "sma_fast"]
    else:
        keys += ["move_lvl", "rsi_thr"]
    if int(g["trend_gate"]):
        keys += ["sma_trend"]
    if int(g["tp_mode"]) == 2:
        keys += ["trail"]
    out = {k: [] for k in keys}

    d_bars = ext["d_bars"]
    don = ext.get("don_hi") if sgn == 1 else ext.get("don_lo")
    trail_arr = ext.get("trail_lo") if sgn == 1 else ext.get("trail_hi")
    closes = ctx["closes"]
    for i in range(i_from, len(c4)):
        ts = c4[i][0] // 1000
        c = c4[i][4]
        atr_b = ctx["atr_bar"][i] if i < len(ctx["atr_bar"]) else None
        if i < len(rsi_arr) and rsi_arr[i] is not None:
            out["rsi"].append([ts, round(rsi_arr[i], 2)])
        if ctx["ema50"][i] is not None:
            out["ema50"].append([ts, ctx["ema50"][i]])
        if ctx["ema200"][i] is not None:
            out["ema200"].append([ts, ctx["ema200"][i]])
        if atr_b:
            # линия стопа = ровно то, что вернул бы движок при входе здесь
            try:
                out["stop_line"].append(
                    [ts, se3._stop_price(se3.setup_side(setup), i, c4, ctx, g,
                                         ext, c)])
            except Exception:                      # noqa: BLE001
                pass
        if "level" in out and don is not None and don[i] is not None:
            out["level"].append([ts, don[i]])
            if "level_trig" in out and atr_b:
                buf = float(g["break_buf_atr"]) * atr_b * c
                out["level_trig"].append([ts, don[i] + sgn * buf])
        if "sma_slow" in out and ext.get("sma_slow") and ext["sma_slow"][i]:
            out["sma_slow"].append([ts, ext["sma_slow"][i]])
        if "sma_fast" in out and ext.get("sma_fast") and ext["sma_fast"][i]:
            out["sma_fast"].append([ts, ext["sma_fast"][i]])
        if "sma_trend" in out and ext.get("sma_trend") and ext["sma_trend"][i]:
            out["sma_trend"].append([ts, ext["sma_trend"][i]])
        if "move_lvl" in out and i >= d_bars:
            base = closes[i - d_bars]
            out["move_lvl"].append([ts, base * (1 - sgn * float(g["drop_frac"]))])
        if "rsi_thr" in out:
            thr = float(g["rsi_os"])
            out["rsi_thr"].append([ts, thr if sgn == 1 else 100.0 - thr])
        if "trail" in out and trail_arr is not None and trail_arr[i] is not None:
            out["trail"].append([ts, trail_arr[i]])
    return {k: thin(v) for k, v in out.items()}


def series_meta(keys, setup, g):
    """Описание рядов для легенды страницы (имя + роль)."""
    meta = {}
    for k in keys:
        name, role = SERIES_META.get(k, (k, "справочно"))
        if k == "rsi" and not (setup.startswith("dump")
                               or setup.startswith("rally")):
            role = "справочно"                     # RSI не ворото этого сетапа
        if k == "level":
            name = f"уровень Дончиана, {int(g['break_days'])} сут"
        if k == "sma_slow":
            name = f"SMA {int(g['pull_slow'])} (тренд)"
        if k == "sma_fast":
            name = f"SMA {int(g['pull_fast'])} (откат)"
        if k == "sma_trend":
            name = f"SMA {int(g['trend_len'])} (направление)"
        if k == "move_lvl":
            name = (f"порог движения {float(g['drop_frac']) * 100:.1f}% за "
                    f"{int(g['drop_days'])} сут")
        if k == "trail":
            name = f"трейлинг по {int(g['trail_bars'])} барам"
        if k == "rsi":
            name = f"RSI {se3.RSI_SET[int(g['rsi_idx'])]}"
        meta[k] = dict(name=name, role=role)
    return meta


# -------------------------------------------------------------------- сборка
class Data:
    """Кэш свечей и контекста по (монета, ТФ)."""

    def __init__(self, days=DAYS):
        self.days = days
        self._c, self._ctx, self._m15 = {}, {}, {}

    def path(self, symbol, iv):
        return f"history_{symbol}_{int(iv)}m_{self.days}d.json"

    def has(self, symbol, iv):
        return (os.path.exists(self.path(symbol, iv))
                and os.path.exists(self.path(symbol, 15)))

    def bars(self, symbol, iv):
        k = (symbol, int(iv))
        if k not in self._c:
            self._c[k] = ev.fetch(symbol, str(int(iv)), self.days)
        return self._c[k]

    def m15(self, symbol):
        if symbol not in self._m15:
            c15 = ev.fetch(symbol, "15", self.days)
            self._m15[symbol] = (c15, [c[0] for c in c15])
        return self._m15[symbol]

    def ctx(self, symbol, iv):
        k = (symbol, int(iv))
        if k not in self._ctx:
            self._ctx[k] = se3.prep_context(self.bars(symbol, iv),
                                            interval_min=int(iv), symbol=symbol)
        return self._ctx[k]


def build_one(key, rec, data):
    setup, symbol, iv = split_key(key, rec)
    g, clamped = make_genome(rec)
    lev = int(rec.get("rec_lev") or rec.get("lev") or DEF_LEV)
    if clamped:
        print(f"  гены зажаты в границы GENES3: {', '.join(clamped)}")
    c4 = data.bars(symbol, iv)
    ctx = data.ctx(symbol, iv)
    c15, ts15 = data.m15(symbol)
    t0 = time.time()
    r = se3.run_setup(setup, g, c4, ctx, c15, ts15, lev, symbol=symbol,
                      interval_min=iv, collect_diag=True)
    a, b = r["signal_range"]
    st = ss.full_stats(r, lev, t0_ms=c4[a][0])
    ext = se3.build_ext(setup, g, c4, interval_min=iv)
    bar_i = {c[0]: i for i, c in enumerate(c4)}       # ts бара -> индекс

    trades, bad_adds, bad_avg, n_grid = [], 0, 0, 0
    for k, t in enumerate(r["trades"], 1):
        row = dict(t)
        row["idx"] = k
        row["entry_ts_s"] = t["entry_ts"] // 1000
        row["exit_ts_s"] = t["exit_ts"] // 1000
        row["signal_ts_s"] = t["signal_ts"] // 1000
        row["regime_name"] = REG_RU.get(t.get("regime"))
        i_sig = bar_i.get(t["signal_ts"])
        atr_b = (ctx["atr_bar"][i_sig] if i_sig is not None
                 and i_sig < len(ctx["atr_bar"]) else None)
        row["adds"], avg_chk = grid_adds(t, g, atr_b, c15, ts15, lev)
        if int(t.get("grid_fills") or 1) > 1:
            n_grid += 1
            if len(row["adds"]) != int(t["grid_fills"]) - 1:
                bad_adds += 1
            # сверка со средней ценой самого движка: реконструкция обязана
            # совпасть до чисел с плавающей точкой, иначе маркеры врут
            if (avg_chk is None or t.get("avg_entry") is None
                    or abs(avg_chk - t["avg_entry"]) > 1e-9 * max(1.0, avg_chk)):
                bad_avg += 1
        trades.append(row)
    near = []
    for x in r["near_misses"]:
        row = dict(x)
        row["ts_s"] = x["ts"] // 1000
        row["regime_name"] = REG_RU.get(x.get("regime"))
        near.append(row)

    i_from = int(ext["warm"])
    ser = build_series(setup, g, c4, ctx, ext, iv, i_from)
    bar_ms = se3.bar_ms_of(iv)
    hold = rec.get("holdout") or {}
    data_out = dict(
        name=key, key=key, setup=setup, symbol=symbol,
        coin=symbol.replace("USDT", ""), interval_min=iv, tf_label=tf_label(iv),
        title=rec.get("title") or se3.SETUP_TITLES3.get(setup, setup),
        side=se3.setup_side(setup), long=setup.endswith("long"),
        mirror_setup=se3.MIRROR.get(setup),
        lev=lev, genome=g, genome_clamped=clamped,
        period_start=c4[a][0] // 1000,
        period_end=(c4[min(b, len(c4)) - 1][0] + bar_ms) // 1000,
        warm_bar=i_from, warm_ts=c4[min(i_from, len(c4) - 1)][0] // 1000,
        holdout_from=(int(hold["from_ts"]) // 1000
                      if hold.get("from_ts") else None),
        holdout_to=(int(hold["to_ts"]) // 1000 if hold.get("to_ts") else None),
        status=dict(enabled=bool(rec.get("enabled")),
                    watch=bool(rec.get("watch")),
                    verdict=rec.get("verdict"), reason=rec.get("reason"),
                    checks=rec.get("checks") or []),
        trades=trades, near_misses=near, stats=st,
        series=ser, series_meta=series_meta(list(ser), setup, g),
        equity=st["equity"], drawdown=st["drawdown"], monthly=st["monthly"],
        grid=dict(levels=int(g["grid_levels"]),
                  step_atr=round(float(g["grid_step_atr"]), 3),
                  mult=round(float(g["grid_mult"]), 3),
                  avg_fills=r.get("avg_grid_fills"),
                  hist={str(k2): v for k2, v in
                        (r.get("grid_fill_hist") or {}).items()},
                  total=r.get("grid_fills")),
        exitcfg=dict(mode=int(g["tp_mode"]), tp_r=round(float(g["tp_r"]), 2),
                     tp_atr=round(float(g["tp_atr"]), 2),
                     trail_bars=int(g["trail_bars"]),
                     be_after_r=round(float(g["be_after_r"]), 2)),
        engine=dict(signals=r["signals"], bars_eval=r["bars_eval"],
                    blocked_busy=r["blocked_busy"],
                    blocked_cooldown=r["blocked_cooldown"],
                    blocked_ruined=r["blocked_ruined"],
                    ruined=bool(r["ruined"]), balance=round(r["balance"], 3),
                    be_moved=r["be_moved"], be_hit=r["be_hit"],
                    be_hit_share=r["be_hit_share"], be_no_stop=r["be_no_stop"],
                    trail_exits=r["trail_exits"],
                    avg_entry=r["avg_entry"],
                    reject_counts=r["reject_counts"], gate_solo=r["gate_solo"],
                    costs=dict(taker=se3.TAKER, maker=se3.MAKER,
                               slip=se3.SLIP, fund_8h=se3.FUND_8H)),
        built_at=int(time.time()), engine_name="signal_engine3",
    )
    print(f"  прогон {time.time() - t0:.1f} с: сделок {len(trades)}, "
          f"near-miss {len(near)}, сигналов {r['signals']}, "
          f"колен в среднем {r['avg_grid_fills']}")
    if n_grid:
        print(f"  доливки: сделок с сеткой {n_grid}, расхождений по числу "
              f"колен {bad_adds}, по средней цене {bad_avg}"
              + ("  (реконструкция совпала с движком)"
                 if not (bad_adds or bad_avg) else "  — МАРКЕРЫ НЕТОЧНЫ"))
    return data_out, bad_adds + bad_avg


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Аналитика страниц сетапов v3 (signal_engine3)")
    ap.add_argument("--setups", default=SETUPS, help="конфиги отбора v3")
    ap.add_argument("--out-dir", default=OUT_DIR, help="куда писать JSON страниц")
    ap.add_argument("--only", default="", help="считать только эти ключи (через запятую)")
    ap.add_argument("--days", type=int, default=DAYS)
    a = ap.parse_args(argv)

    if not os.path.exists(a.setups):
        print(f"отбор ещё не запускался: файла {a.setups} нет.\n"
              f"Сначала: python evolution14.py, потом python finalize_v3.py.")
        return 0
    with open(a.setups, encoding="utf-8") as fh:
        raw = json.load(fh)
    keys = [k for k in raw if not str(k).startswith("__")
            and isinstance(raw[k], dict)]
    if a.only:
        want = {x.strip() for x in a.only.split(",") if x.strip()}
        keys = [k for k in keys if k in want]
    if not keys:
        print(f"{a.setups}: подходящих записей нет — считать нечего")
        return 0
    os.makedirs(a.out_dir, exist_ok=True)

    data = Data(days=a.days)
    written, problems = [], 0
    for key in sorted(keys):
        rec = raw[key]
        setup, symbol, iv = split_key(key, rec)
        print(f"\n{'=' * 78}\n{key}  ({se3.SETUP_TITLES3.get(setup, setup)}, "
              f"{symbol}, ТФ {tf_label(iv)})")
        if setup not in se3.SETUPS3:
            print(f"  ПРОПУЩЕНО: неизвестный сетап «{setup}»")
            problems += 1
            continue
        if not data.has(symbol, iv):
            print(f"  ПРОПУЩЕНО: нет локальной истории "
                  f"{data.path(symbol, iv)} / {data.path(symbol, 15)}")
            problems += 1
            continue
        try:
            d, bad = build_one(key, rec, data)
        except Exception as e:                       # noqa: BLE001
            print(f"  ОШИБКА прогона: {type(e).__name__}: {e}")
            problems += 1
            continue
        problems += 1 if bad else 0
        path = os.path.join(a.out_dir, f"v3_{key}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
        written.append((key, path, d))
        ss.print_report(d["stats"], prefix="  ")
        print(f"  -> {path} ({os.path.getsize(path) / 1024:.0f} КБ)")

    # ------------------------------------------------ проверка структуры файлов
    print(f"\n{'=' * 78}\nСТРУКТУРА JSON (проверка чтением с диска)")
    need_top = ["name", "key", "setup", "symbol", "coin", "interval_min",
                "tf_label", "title", "side", "long", "mirror_setup", "lev",
                "genome", "genome_clamped", "period_start", "period_end",
                "warm_bar", "warm_ts", "holdout_from", "holdout_to", "status",
                "trades", "near_misses", "stats", "series", "series_meta",
                "equity", "drawdown", "monthly", "grid", "exitcfg", "engine",
                "built_at", "engine_name"]
    ok = True
    for name, path, _ in written:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        miss = [k for k in need_top if k not in d]
        extra = [k for k in d if k not in need_top]
        # дубликаты времени и «миллисекунды вместо секунд» — самые частые
        # причины, по которым график молча исчезает
        dups, ms, unsorted_ = [], [], []
        for k, v in d["series"].items():
            tss = [p[0] for p in v]
            if len(set(tss)) != len(tss):
                dups.append(k)
            if tss and max(tss) > 4e9:
                ms.append(k)
            if tss != sorted(tss):
                unsorted_.append(k)
        big = [k for k, v in d["series"].items() if len(v) > MAX_SERIES_POINTS]
        n_adds = sum(len(t["adds"]) for t in d["trades"])
        if miss or extra or dups or ms or unsorted_ or big:
            ok = False
            print(f"  {name}: ПРОБЛЕМА miss={miss} extra={extra} "
                  f"дубли_ts={dups} мс_вместо_сек={ms} не_отсортировано="
                  f"{unsorted_} слишком_много_точек={big}")
        print(f"  {name}: сделок {len(d['trades'])} (доливок {n_adds}), "
              f"near-miss {len(d['near_misses'])}, месяцев {len(d['monthly'])}, "
              f"equity {len(d['equity'])}, рядов {len(d['series'])}")
        print("    ряды: " + ", ".join(
            f"{k}={len(v)}[{d['series_meta'][k]['role']}]"
            for k, v in d["series"].items()))
    if written:
        d = written[0][2]
        print(f"\nверхний уровень ({len(need_top)}): {need_top}")
        print(f"поля trade ({len(d['trades'][0]) if d['trades'] else 0}): "
              f"{sorted(d['trades'][0].keys()) if d['trades'] else '—'}")
        print(f"ключи stats ({len(d['stats'])})")
    print(f"\nготово: файлов {len(written)}"
          + (f", проблем {problems}" if problems else ""))
    print("ВЫВОД: структура " + ("КОРРЕКТНА" if ok else "НАРУШЕНА"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
