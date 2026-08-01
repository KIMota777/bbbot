# -*- coding: utf-8 -*-
"""Считает аналитику v2 по каждому сигнальному сетапу для сайта (мульти-ТФ).

Источник конфигов: signal_setups2.json. Формат ключей:
  "<сетап>@<ТФ>"  — например "sweep_long@240" или "bounce_short@60";
  старый ключ БЕЗ @ трактуется как @240 (обратная совместимость), либо ТФ
  берётся из поля interval_min конфига, если оно есть.
Пока signal_setups2.json нет — берём геномы v1 из signal_setups.json,
дополняем новыми генами из signal_engine2.DEFAULTS2 и помечаем
"provisional": true.

Считаются только действующие сетапы se2.SETUPS (sweep_long, dump_long,
bounce_short, rally_short) — конфиги убранных (range_*, sweep_short,
pump_short) молча пропускаются.

Для каждого конфига:
  - полный прогон signal_engine2.run_setup по всей истории BTC (сигналы на
    закрытых барах СВОЕГО ТФ, исполнение по 15м), с диагностикой;
  - вся статистика через signal_stats.full_stats;
  - серии для графика: границы диапазона, фактические границы зоны входа,
    RSI и его режимный порог, EMA50/EMA200 (всё в барах своего ТФ).

Результат: webapp/data/signal2_<сетап>@<ТФ>.json (UTF-8 без BOM),
ts — В СЕКУНДАХ. Запуск: python build_signal_analytics2.py
"""

import json
import os
import sys

import evolution as ev
import signal_engine2 as se2
import signal_stats as ss

OUT_DIR = os.path.join("webapp", "data")
SETUPS_V2 = "signal_setups2.json"
SETUPS_V1 = "signal_setups.json"
SYMBOL = "BTCUSDT"
DAYS = 1150
MAX_SERIES_POINTS = 3000
DEF_LEV = 15

# русские имена сетапов (единые для сайта и помощника)
RU_TITLES = {
    "sweep_long": "Ложный пробой низа (сбор ликвидности): лонг",
    "dump_long": "Капитуляция: лонг после сильного падения",
    "bounce_short": "Нож -> откат: шорт отскока после обвала",
    "rally_short": "Тренд-шорт: продажа отскока по тренду",
}


def tf_label(iv):
    """240 -> "4ч", 60 -> "1ч"."""
    return "4ч" if iv == 240 else ("1ч" if iv == 60 else f"{iv}м")


def split_key(key, rec=None):
    """Ключ конфига -> (сетап, ТФ в минутах).

    "sweep_long@60" -> ("sweep_long", 60). Ключ без @ — сначала смотрим
    поле interval_min конфига, иначе 240 (старый формат = 4ч)."""
    if "@" in key:
        base, _, tf = key.rpartition("@")
        if tf.isdigit():
            return base, int(tf)
    iv = 240
    if isinstance(rec, dict):
        try:
            iv = int(rec.get("interval_min") or 240)
        except (TypeError, ValueError):
            iv = 240
    return key, iv


# ------------------------------------------------------------------- конфиги
def load_setups():
    """(конфиги, provisional, имя файла).

    Конфиги нормализованы к полным ключам "<сетап>@<ТФ>", в каждом rec
    проставлен interval_min. Legacy-сетапы (не из se2.SETUPS) отброшены."""
    if os.path.exists(SETUPS_V2):
        src, provisional = SETUPS_V2, False
    else:
        src, provisional = SETUPS_V1, True
    with open(src, encoding="utf-8") as fh:
        raw = json.load(fh)
    out, dropped = {}, []
    for key, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        base, iv = split_key(key, rec)
        if base not in se2.SETUPS:
            dropped.append(key)
            continue
        out[f"{base}@{iv}"] = dict(rec, interval_min=iv)
    return out, provisional, src, dropped


def make_genome(rec):
    """Полный геном v2: DEFAULTS2 + значения из конфига, зажатые в границы
    GENES2. Возвращает (геном, список зажатых генов)."""
    g = dict(se2.DEFAULTS2)
    src = rec.get("genome") or {}
    if not src and any(k in rec for k in se2.GENES2):
        src = rec                          # конфиг = сам геном, без обёртки
    clamped = []
    for k, v in src.items():
        if k not in se2.GENES2:
            continue                       # ген v1, которого в v2 нет
        lo, hi, is_int = se2.GENES2[k]
        val = int(round(v)) if is_int else float(v)
        fixed = min(max(val, lo), hi)
        if is_int:
            fixed = int(fixed)
        if fixed != val:
            clamped.append(f"{k}: {v} -> {fixed}")
        g[k] = fixed
    return g, clamped


# --------------------------------------------------------------------- серии
def build_series(setup, g, c4, ctx, ext, i_from):
    """Серии для графика (ts в СЕКУНДАХ, значения на закрытии бара своего ТФ).

    range_lo/range_hi — границы окна диапазона (то, от чего отталкивается
      сетап; для sweep — уровень с лагом age);
    zone_lo/zone_hi   — ФАКТИЧЕСКАЯ зона разрешённого входа на баре, с учётом
      режимного zone_thr: для лонга снизу диапазона, для шорта — сверху.
      ВАЖНО: ворото зоны ОДНОСТОРОННЕЕ. Для лонга условие «zone_pos <=
      zone_thr», то есть значимая линия — zone_hi, а вход разрешён и НИЖЕ
      zone_lo (окно уровней лаговое, цена могла пробить старый минимум).
      Для шорта значимая линия — zone_lo, вход разрешён и выше zone_hi.
      Для sweep/dump/bounce/rally зона воротом не является — это только
      контекст уровня, от которого сетап отталкивается;
    rsi               — RSI выбранного геном периода;
    rsi_thr           — фактический порог RSI на баре (для шортов это уже
      100-rsi_thr, то есть уровень, с которым сравнивается RSI);
    ema50/ema200      — контекст тренда.
    """
    is_long = setup.endswith("long")
    period = se2.RSI_SET[int(g["rsi_idx"])]
    rsi_arr = ctx["rsi"][period]
    out = {k: [] for k in ("range_lo", "range_hi", "zone_lo", "zone_hi",
                           "rsi", "ema50", "ema200", "rsi_thr")}
    for i in range(i_from, len(c4)):
        ts = c4[i][0] // 1000
        reg = ctx["regime"][i]
        rsi_thr, zone_thr = se2.effective_thresholds(setup, reg, g)
        lo, hi = ext["lows"][i], ext["highs"][i]
        if lo is not None and hi is not None and hi > lo:
            width = (hi - lo) * zone_thr
            out["range_lo"].append([ts, round(lo, 2)])
            out["range_hi"].append([ts, round(hi, 2)])
            if is_long:
                out["zone_lo"].append([ts, round(lo, 2)])
                out["zone_hi"].append([ts, round(lo + width, 2)])
            else:
                out["zone_lo"].append([ts, round(hi - width, 2)])
                out["zone_hi"].append([ts, round(hi, 2)])
        if i < len(rsi_arr) and rsi_arr[i] is not None:
            out["rsi"].append([ts, round(rsi_arr[i], 2)])
            # Порог рисуем ровно в той шкале, в какой его сравнивает gate_eval.
            # У шортов "по перекупленности" (rally_short и legacy range/pump)
            # ворото сравнивает с 100-rsi_thr; у bounce_short ворото обратное —
            # там требуется RSI ВЫШЕ rsi_thr напрямую (отскок снял
            # перепроданность), поэтому зеркалить его нельзя: иначе на графике
            # все входы окажутся по "неправильную" сторону линии.
            mirror = (not is_long) and setup != "bounce_short"
            out["rsi_thr"].append(
                [ts, round(100.0 - rsi_thr if mirror else rsi_thr, 2)])
        if ctx["ema50"][i] is not None:
            out["ema50"].append([ts, round(ctx["ema50"][i], 2)])
        if ctx["ema200"][i] is not None:
            out["ema200"].append([ts, round(ctx["ema200"][i], 2)])
    return {k: ss.thin(v, MAX_SERIES_POINTS) for k, v in out.items()}


# -------------------------------------------------------------------- сборка
def build_one(key, setup, iv, rec, c4, ctx, c15, ts15, provisional):
    """key — полный ключ "<сетап>@<ТФ>", setup — базовое имя, iv — ТФ (мин)."""
    g, clamped = make_genome(rec)
    lev = int(rec.get("rec_lev") or rec.get("lev") or DEF_LEV)
    if clamped:
        print(f"  гены зажаты в границы GENES2: {', '.join(clamped)}")
    r = se2.run_setup(setup, g, c4, ctx, c15, ts15, lev, interval_min=iv)
    a, b = r["signal_range"]
    t0_ms = c4[a][0]
    st = ss.full_stats(r, lev, t0_ms=t0_ms)

    trades = []
    for k, t in enumerate(r["trades"], 1):
        row = dict(t)
        row["idx"] = k
        row["entry_ts_s"] = t["entry_ts"] // 1000
        row["exit_ts_s"] = t["exit_ts"] // 1000
        row["signal_ts_s"] = t["signal_ts"] // 1000
        row["regime_name"] = se2.REGIME_NAMES.get(t["regime"])
        # цена выхода — чтобы график мог соединить вход и выход линией.
        # Для тейка и стопа она известна точно; для таймаута/ликвидации
        # восстанавливаем из R (r = ход в долях риска), это визуально точно
        # и не требует менять прод-движок.
        sgn = 1 if t["side"] == "L" else -1
        if t["reason"] == "tp":
            row["exit_px"] = t["tp"]
        elif t["reason"] == "stop":
            row["exit_px"] = t["stop"]
        else:
            risk_px = abs(t["entry"] - t["stop"])
            row["exit_px"] = round(t["entry"] + sgn * t["r"] * risk_px, 2)
        trades.append(row)
    near = []
    for x in r["near_misses"]:
        row = dict(x)
        row["ts_s"] = x["ts"] // 1000
        row["regime_name"] = se2.REGIME_NAMES.get(x["regime"])
        near.append(row)

    ext = se2.build_ext(setup, g, c4, interval_min=iv)
    i_from = max(0, int(g["window"]) + int(ext["lag"]) - 1)
    bar_ms = se2.bar_ms_of(iv)
    data = dict(
        name=key, setup=setup, interval_min=iv, tf_label=tf_label(iv),
        title=RU_TITLES.get(setup, setup), lev=lev, genome=g,
        period_start=t0_ms // 1000,
        period_end=(c4[min(b, len(c4)) - 1][0] + bar_ms) // 1000,
        provisional=bool(provisional),
        trades=trades, near_misses=near, stats=st,
        series=build_series(setup, g, c4, ctx, ext, i_from),
        equity=st["equity"], drawdown=st["drawdown"], monthly=st["monthly"],
    )
    return data, r, st


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    setups, provisional, src, dropped = load_setups()
    print(f"конфиги: {src}" + ("  (signal_setups2.json ещё нет -> геномы v1 + "
                               "DEFAULTS2, provisional=true)" if provisional
                               else ""))
    if dropped:
        print(f"пропущены убранные сетапы: {dropped}")

    # порядок: по списку SETUPS, внутри сетапа 4ч затем 1ч, затем прочие ТФ
    order = [f"{s}@{iv}" for s in se2.SETUPS for iv in (240, 60)]
    names = [k for k in order if k in setups]
    names += [k for k in setups if k not in names]
    missing = [s for s in se2.SETUPS
               if not any(k.startswith(s + "@") for k in names)]
    if missing:
        print(f"нет в конфиге (пропущены): {missing}")
    if not names:
        print("считать нечего — ни одного конфига действующих сетапов")
        return 1

    c15 = ev.fetch(SYMBOL, "15", DAYS)
    ts15 = [c[0] for c in c15]
    tf_cache = {}                          # iv -> (свечи, контекст)

    def get_tf(iv):
        if iv not in tf_cache:
            cc = ev.fetch(SYMBOL, str(iv), DAYS)
            ctx = se2.prep_context(cc, interval_min=iv)
            tf_cache[iv] = (cc, ctx)
            print(f"{SYMBOL} {tf_label(iv)}: баров {len(cc)}, период "
                  f"{cc[0][0]//1000}..{(cc[-1][0]+se2.bar_ms_of(iv))//1000} "
                  f"(сек)")
        return tf_cache[iv]

    print(f"{SYMBOL}: 15м-баров {len(c15)}")
    written = []
    for key in names:
        rec = setups[key]
        setup, iv = split_key(key, rec)
        print(f"\n{'='*78}\n{key}  ({RU_TITLES.get(setup, setup)}, "
              f"ТФ {tf_label(iv)})")
        cc, ctx = get_tf(iv)
        data, r, st = build_one(key, setup, iv, rec, cc, ctx, c15, ts15,
                                provisional)
        path = os.path.join(OUT_DIR, f"signal2_{key}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        written.append((key, path, data))
        print(f"  геном: stop_mode={data['genome']['stop_mode']} "
              f"cap={data['genome']['stop_cap']:.4f} "
              f"entry_mode={data['genome']['entry_mode']} "
              f"cooldown={data['genome']['cooldown']} "
              f"hold_days={data['genome']['hold_days']} | плечо x{data['lev']}")
        ss.print_report(st, prefix="  ")
        print(f"  -> {path} ({os.path.getsize(path)/1024:.0f} КБ)")

    # ------------------------------------------------ подтверждение структуры
    print(f"\n{'='*78}\nСТРУКТУРА JSON (проверка чтением с диска)")
    need_top = ["name", "setup", "interval_min", "tf_label", "title", "lev",
                "genome", "period_start", "period_end", "provisional",
                "trades", "near_misses", "stats", "series", "equity",
                "drawdown", "monthly"]
    need_series = ["range_lo", "range_hi", "zone_lo", "zone_hi", "rsi",
                   "ema50", "ema200", "rsi_thr"]
    ok = True
    for name, path, _ in written:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        extra = [k for k in d if k not in need_top]
        miss = [k for k in need_top if k not in d]
        s_miss = [k for k in need_series if k not in d["series"]]
        s_extra = [k for k in d["series"] if k not in need_series]
        bad_pts = [k for k, v in d["series"].items()
                   if len(v) > MAX_SERIES_POINTS]
        bad_ts = [k for k, v in d["series"].items()
                  if v and v[0][0] > 4e9]          # секунды, не миллисекунды
        base, iv = split_key(name)
        bad_key = (d["setup"] != base or d["interval_min"] != iv)
        if miss or extra or s_miss or s_extra or bad_pts or bad_ts or bad_key:
            ok = False
            print(f"  {name}: ПРОБЛЕМА miss={miss} extra={extra} "
                  f"series_miss={s_miss} series_extra={s_extra} "
                  f"too_many={bad_pts} ms_instead_of_s={bad_ts} "
                  f"key_mismatch={bad_key}")
        sizes = " ".join(f"{k}={len(d['series'][k])}" for k in need_series)
        print(f"  {name}: ТФ {d['tf_label']} trades={len(d['trades'])} "
              f"near_misses={len(d['near_misses'])} "
              f"stats_keys={len(d['stats'])} equity={len(d['equity'])} "
              f"drawdown={len(d['drawdown'])} monthly={len(d['monthly'])} "
              f"provisional={d['provisional']}")
        print(f"    series: {sizes}")
    print(f"\nверхний уровень: {need_top}")
    if written:
        d = written[0][2]
        print(f"ключи stats ({len(d['stats'])}): "
              f"{sorted(d['stats'].keys())}")
        doc = [k for keys in ss.KEYS_DOC.values() for k in keys]
        undoc = [k for k in d["stats"] if k not in doc]
        stale = [k for k in doc if k not in d["stats"]]
        if undoc or stale:
            ok = False
            print(f"  KEYS_DOC расходится с фактом: не описаны {undoc}, "
                  f"лишние {stale}")
        for grp, keys in ss.KEYS_DOC.items():
            print(f"  {grp}: {keys}")
        print(f"\nполя trade ({len(d['trades'][0]) if d['trades'] else 0}): "
              f"{sorted(d['trades'][0].keys()) if d['trades'] else '—'}")
        print(f"поля near_miss "
              f"({len(d['near_misses'][0]) if d['near_misses'] else 0}): "
              f"{sorted(d['near_misses'][0].keys()) if d['near_misses'] else '—'}")
        print(f"пример точек: series.zone_lo[0]={d['series']['zone_lo'][:1]}, "
              f"equity[0]={d['equity'][:1]}, monthly[0]={d['monthly'][:1]}, "
              f"drawdown[-1]={d['drawdown'][-1:]}")
    print("\nВЫВОД: структура " + ("КОРРЕКТНА" if ok else "НАРУШЕНА"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
