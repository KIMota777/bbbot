"""Профиль стратегии кошелька (Wallet Strategy Profile) и текст «Detected Strategy».

Профиль отвечает на вопросы из ТЗ:
1) насколько кошелёк успешен (performance);
2) как торгует (style: частота, удержание, размер позиции);
3) какие активы выбирает (selection: MC, возраст, ликвидность, сектор, CEX);
4) в какой момент входит (entry: ранний/пробой/моментум/падение) и как
   набирает и сбрасывает позицию (building, exit);
5) что происходило с ценой после его входов (after_entry) — есть ли у него
   преимущество по времени входа, а не только удача.
"""
from __future__ import annotations

from collections import Counter

from ..util import DAY, HOUR, fmt_dur, fmt_pct, fmt_usd, mean, median, quantile, safe_div
from .features import ENTRY_RU, ENTRY_TYPES, FWD, summarize

STYLES = [
    (15 * 60, "scalper", "скальпер"),
    (DAY, "day_trader", "дей-трейдер"),
    (14 * DAY, "swing", "свинг-трейдер"),
    (90 * DAY, "position", "позиционный трейдер"),
    (float("inf"), "investor", "долгосрочный инвестор"),
]

MC_BUCKETS = [(1e6, "<$1M"), (10e6, "$1–10M"), (50e6, "$10–50M"), (200e6, "$50–200M"),
              (1e9, "$200M–1B"), (float("inf"), ">$1B")]
AGE_BUCKETS = [(1, "<1 дня"), (7, "1–7 дней"), (30, "7–30 дней"), (180, "1–6 мес."),
               (365, "6–12 мес."), (float("inf"), ">1 года")]


def style_of(median_hold: float | None) -> tuple[str, str]:
    if median_hold is None:
        return "unknown", "нет данных"
    for lim, code, ru in STYLES:
        if median_hold < lim:
            return code, ru
    return "investor", "долгосрочный инвестор"


def _bucket(v: float | None, buckets) -> str | None:
    if v is None:
        return None
    for lim, name in buckets:
        if v < lim:
            return name
    return buckets[-1][1]


def _shares(items: list[str]) -> dict[str, float]:
    c = Counter(i for i in items if i is not None)
    n = sum(c.values())
    return {k: v / n for k, v in c.most_common()} if n else {}


def _iqr(vals) -> tuple[float | None, float | None]:
    return quantile(vals, 0.25), quantile(vals, 0.75)


EXIT_BUCKETS = [(-1e9, -0.2, "<−20%"), (-0.2, -0.05, "−20…−5%"), (-0.05, 0.05, "−5…+5%"), (0.05, 0.2, "+5…+20%"),
                (0.2, 0.5, "+20…+50%"), (0.5, 1.0, "+50…+100%"), (1.0, 3.0, "+100…+300%"), (3.0, 1e18, ">+300%")]


def _gain_buckets(exits: list[dict]) -> dict[str, float]:
    """Распределение продаж по результату относительно средней цены покупки (по объёму)."""
    tot = sum(x["usd"] for x in exits if x.get("gain") is not None) or 0
    out = {}
    for lo, hi, name in EXIT_BUCKETS:
        v = sum(x["usd"] for x in exits if x.get("gain") is not None and lo <= x["gain"] < hi)
        if v:
            out[name] = v / tot
    return out


def _sector_timing(firsts: list[dict]) -> dict:
    """Покупал ли кошелёк сектор ДО сильного роста или уже ПОСЛЕ него."""
    by: dict[str, list[dict]] = {}
    for e in firsts:
        by.setdefault(e["features"].get("sector") or "Unknown", []).append(e)
    out = {}
    for sec, es in by.items():
        r7 = [e["features"].get("ret_7d") for e in es]
        before = [e for e in es if (e["features"].get("ret_7d") is not None and e["features"]["ret_7d"] < 0.05
                                    and (e["fwd"].get("max_30d") or 0) >= 0.3)]
        after = [e for e in es if (e["features"].get("ret_7d") or 0) >= 0.2]
        known = [e for e in es if e["features"].get("ret_7d") is not None]
        out[sec] = {"n": len(es), "ret_7d_before_p50": median(r7), "fwd_7d_p50": median([e["fwd"].get("7d") for e in es]),
                    "before_growth_share": safe_div(len(before), len(known), None),
                    "after_growth_share": safe_div(len(after), len(known), None)}
    return out


def _regime(firsts: list[dict]) -> dict:
    out = {}
    for name, cond in (("btc_up", lambda x: x > 0), ("btc_down", lambda x: x <= 0)):
        es = [e for e in firsts if e["features"].get("btc_ret_7d") is not None and cond(e["features"]["btc_ret_7d"])]
        out[name] = {"n": len(es), "fwd_24h_p50": median([e["fwd"].get("24h") for e in es]),
                     "rt_pnl_p50": median([e["outcome"].get("rt_pnl_pct") for e in es if e["outcome"].get("rt_valid")])}
    return out


def _flows(legs: list[dict] | None) -> dict:
    """Переводы кошелька: на биржи/с бирж (по labels.csv) и между кошельками."""
    from .labels import of as label_of
    out = {"to_cex": {"n": 0, "usd": 0.0}, "from_cex": {"n": 0, "usd": 0.0}, "to_other": 0, "from_other": 0,
           "cex_names": {}}
    for l in legs or []:
        if l["side"] not in ("tin", "tout"):
            continue
        lab = label_of(l.get("counterparty"))
        if lab and lab["type"] == "cex":
            k = "to_cex" if l["side"] == "tout" else "from_cex"
            out[k]["n"] += 1
            out[k]["usd"] += l.get("usd") or 0.0
            out["cex_names"][lab["label"]] = out["cex_names"].get(lab["label"], 0) + 1
        else:
            out["to_other" if l["side"] == "tout" else "from_other"] += 1
    return out


def build_profile(m: dict, trips, entries: list[dict], exits: list[dict], meta: dict, cfg,
                  legs: list[dict] | None = None) -> dict:
    valid = [t for t in trips if t.valid]
    firsts = [e for e in entries if e["buy_index"] == 1]
    ff = [e["features"] for e in firsts]

    # --- стиль ---
    hold = m.get("hold") or {}
    code, ru = style_of(hold.get("p50"))
    style = {
        "code": code, "label": ru,
        "trades_per_month": m.get("trades_per_month"), "swaps_per_day": m.get("swaps_per_day"),
        "hold_avg": hold.get("avg"), "hold_p25": hold.get("p25"), "hold_p50": hold.get("p50"),
        "hold_p75": hold.get("p75"), "hold_p90": hold.get("p90"),
        "avg_position": m.get("avg_position"), "median_position": m.get("median_position"),
        "max_concurrent": m.get("max_concurrent"),
        "position_pct_capital": median([e["features"].get("size_pct_capital") for e in firsts]),
    }

    # --- выбор активов ---
    mcs = [f.get("mc_entry") for f in ff]
    ages = [f.get("token_age_days") for f in ff]
    liqs = [f.get("liquidity_now") for f in ff]
    sectors = _shares([f.get("sector") for f in ff])
    cex_now = [bool(f.get("cex_listed_now")) for f in ff]
    cex_at = [bool(f.get("cex_listed")) for f in ff]
    pre_listing = [f for f in ff if f.get("cex_listed_now") and not f.get("cex_listed")]
    tok_count = Counter(e["token"] for e in firsts)
    top_tokens = []
    for tok, n in tok_count.most_common(8):
        mt = meta.get(tok) or {}
        pnl = sum(t.realized for t in valid if t.token == tok)
        top_tokens.append({"token": tok, "symbol": mt.get("symbol") or tok[:6], "entries": n, "pnl": pnl,
                           "hold_p50": median([t.hold_sec for t in valid if t.token == tok])})
    selection = {
        "mc_p25": _iqr(mcs)[0], "mc_p50": median(mcs), "mc_p75": _iqr(mcs)[1],
        "mc_buckets": _shares([_bucket(v, MC_BUCKETS) for v in mcs]),
        "age_p25": _iqr(ages)[0], "age_p50": median(ages), "age_p75": _iqr(ages)[1],
        "age_buckets": _shares([_bucket(v, AGE_BUCKETS) for v in ages]),
        "liq_p50": median(liqs),
        "sectors": sectors,
        "cex_listed_now_share": safe_div(sum(cex_now), len(cex_now), None),
        "cex_listed_at_entry_share": safe_div(sum(cex_at), len(cex_at), None),
        "pre_listing_entries": len(pre_listing),
        "n_tokens": len(tok_count), "top_tokens": top_tokens,
        "reentry_share": safe_div(sum(1 for f in ff if (f.get("prev_rts") or 0) > 0), len(ff), None),
        "sector_timing": _sector_timing(firsts),
    }

    # --- вход ---
    types = [e["entry_type"] for e in firsts]
    type_share = {t: s for t, s in _shares(types).items()}
    # результат по типам входа: что работает у этого кошелька лучше всего
    by_type = {}
    for t in ENTRY_TYPES:
        es = [e for e in firsts if e["entry_type"] == t]
        if not es:
            continue
        by_type[t] = {
            "n": len(es),
            "fwd_24h": summarize([e["fwd"].get("24h") for e in es]),
            "rt_pnl_pct": summarize([e["outcome"].get("rt_pnl_pct") for e in es if e["outcome"].get("rt_valid")]),
        }
    entry = {
        "types": type_share, "by_type": by_type,
        "ret_24h_p50": median([f.get("ret_24h") for f in ff]),
        "ret_7d_p50": median([f.get("ret_7d") for f in ff]),
        "vol_ratio_p50": median([f.get("vol_ratio") for f in ff]),
        "dd_high_7d_p50": median([f.get("dd_high_7d") for f in ff]),
        "rsi_p50": median([f.get("rsi_14") for f in ff]),
        "breakout_share": safe_div(sum(1 for f in ff if f.get("breakout_7d")), sum(1 for f in ff if "breakout_7d" in f), None),
        "btc_ret_7d_p50": median([f.get("btc_ret_7d") for f in ff]),
    }

    # --- набор позиции ---
    builds = _shares([t.build for t in valid])
    n_buys = [t.n_buys for t in valid]
    add_gaps = []
    for t in valid:
        b = t.buys
        add_gaps += [y.ts - x.ts for x, y in zip(b, b[1:])]
    building = {
        "single": builds.get("single", 0.0),
        "dca": 1 - builds.get("single", 0.0) if builds else None,
        "dca_down": builds.get("dca_down", 0.0), "pyramid": builds.get("pyramid", 0.0),
        "flat_dca": builds.get("dca", 0.0),
        "buys_p50": median(n_buys), "buys_p75": quantile(n_buys, 0.75),
        "add_gap_p50": median(add_gaps),
        "size_rel_add_p50": median([e["features"].get("size_rel") for e in entries if e["buy_index"] > 1]),
    }

    # --- выход ---
    kinds = _shares([t.exit_kind for t in valid])
    win_exits = [x for x in exits if x["rt_valid"] and (x["rt_pnl_pct"] or 0) > 0]
    loss_exits = [x for x in exits if x["rt_valid"] and (x["rt_pnl_pct"] or 0) <= 0]
    first_tp = [x["gain"] for x in win_exits if x["sell_index"] == 1 and x["gain"] is not None]
    first_frac = [x["frac"] for x in exits if x["sell_index"] == 1 and x["n_sells"] > 1 and x["frac"]]
    loss_final = [x["gain"] for x in loss_exits if x["is_last"] and x["gain"] is not None]
    into_strength = [x for x in exits if x.get("ret_24h_before") is not None]
    exit_ = {
        "full": kinds.get("full", 0.0), "partial": kinds.get("partial", 0.0), "transfer": kinds.get("transfer", 0.0),
        "sells_p50": median([t.n_sells for t in valid]),
        "first_tp_gain_p50": median(first_tp), "first_tp_gain_p25": quantile(first_tp, 0.25),
        "first_sell_fraction_p50": median(first_frac),
        "stop_loss_p50": median(loss_final), "stop_loss_p75": quantile(loss_final, 0.25),
        "win_gain_p50": median([t.pnl_pct for t in valid if t.pnl_pct and t.pnl_pct > 0]),
        "into_strength_share": safe_div(sum(1 for x in into_strength if x["ret_24h_before"] > 0), len(into_strength), None),
        "after_sell_24h_p50": median([x.get("after_24h") for x in exits]),
        "after_sell_7d_p50": median([x.get("after_7d") for x in exits]),
        "gain_buckets": _gain_buckets([x for x in exits if x["rt_valid"]]),
    }

    # --- после входа ---
    after = {h: summarize([e["fwd"].get(h) for e in firsts]) for h in FWD}
    mfe = [e["fwd"].get("mfe_7d") for e in firsts]
    mae = [e["fwd"].get("mae_7d") for e in firsts]
    after_entry = {
        **after,
        "mfe_7d_p50": median(mfe), "mae_7d_p50": median(mae),
        "big_move_share": safe_div(sum(1 for x in mfe if x is not None and x >= 0.3), sum(1 for x in mfe if x is not None), None),
        "priced_share": safe_div(sum(1 for e in firsts if e["price_src"] != "none"), len(firsts), None),
    }

    perf = {k: m.get(k) for k in ("realized_pnl", "unrealized_pnl", "roi_capital", "roi_trades", "win_rate",
                                  "win_rate_lb", "profit_factor", "max_dd_norm", "max_dd_capital", "closed",
                                  "expectancy_pct", "median_pnl_pct", "tstat_pct", "avg_win_pct", "avg_loss_pct")}
    prof = {"performance": perf, "style": style, "selection": selection, "entry": entry,
            "building": building, "exit": exit_, "after_entry": after_entry,
            "regime": _regime(firsts), "flows": _flows(legs),
            "windows": m.get("windows"), "n_entries": len(entries), "n_first_entries": len(firsts)}
    prof["detected_strategy"] = detect_text(prof)
    return prof


def _top(d: dict, n: int = 2, min_share: float = 0.15) -> list[tuple[str, float]]:
    return [(k, v) for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:n] if v >= min_share]


def _range_usd(a, b) -> str:
    if a is None or b is None:
        return "—"
    return f"{fmt_usd(a)}–{fmt_usd(b)}"


def _range_days(a, b) -> str:
    if a is None or b is None:
        return "—"

    def d(x):
        return f"{x:.0f}" if x >= 2 else f"{x:.1f}"

    return f"{d(a)}–{d(b)} дн."


def detect_text(p: dict) -> str:
    """Короткое описание стратегии на русском из доминирующих признаков."""
    st, sel, en, bl, ex, af = p["style"], p["selection"], p["entry"], p["building"], p["exit"], p["after_entry"]
    if not p.get("n_first_entries"):
        return "Недостаточно сделок для описания стратегии."
    parts = []
    parts.append(f"{st['label'].capitalize()}: медианное удержание {fmt_dur(st.get('hold_p50'))}"
                 f" (P25–P75: {fmt_dur(st.get('hold_p25'))}–{fmt_dur(st.get('hold_p75'))}),"
                 f" ~{(st.get('trades_per_month') or 0):.0f} сделок в месяц.")
    what = []
    if sel.get("mc_p25") is not None:
        what.append(f"капитализация на входе {_range_usd(sel['mc_p25'], sel['mc_p75'])}")
    if sel.get("age_p25") is not None:
        what.append(f"возраст токена {_range_days(sel['age_p25'], sel['age_p75'])}")
    secs = _top({k: v for k, v in sel.get("sectors", {}).items() if k not in ("Unknown",)})
    if secs:
        what.append("сектора: " + ", ".join(f"{k} {v:.0%}" for k, v in secs))
    if what:
        parts.append("Выбирает активы: " + "; ".join(what) + ".")
    if sel.get("cex_listed_at_entry_share") is not None:
        parts.append(f"В момент входа {sel['cex_listed_at_entry_share']:.0%} токенов уже торговались на CEX"
                     + (f", {sel['pre_listing_entries']} входов — до листинга." if sel.get("pre_listing_entries") else "."))
    types = _top({k: v for k, v in en["types"].items() if k not in ("unknown",)}, n=2, min_share=0.2)
    if types:
        parts.append("Чаще всего входит: " + ", ".join(f"{ENTRY_RU[k]} ({v:.0%})" for k, v in types)
                     + (f"; медиана изменения цены за 24ч до входа {fmt_pct(en['ret_24h_p50'], signed=True)}"
                        if en.get("ret_24h_p50") is not None else "")
                     + (f", объём к среднему ×{en['vol_ratio_p50']:.1f}" if en.get("vol_ratio_p50") else "") + ".")
    if bl.get("dca") is not None:
        if (bl["dca"] or 0) >= 0.5:
            how = "доборами вниз" if bl["dca_down"] >= bl["pyramid"] else "пирамидой по росту"
            parts.append(f"Набирает позицию в несколько покупок ({bl['dca']:.0%} сделок, чаще {how},"
                         f" медиана {bl.get('buys_p50') or 0:.0f} покупки).")
        else:
            parts.append(f"Входит обычно одной покупкой ({bl['single']:.0%}).")
    ex_bits = []
    if (ex.get("partial") or 0) >= 0.4:
        ex_bits.append(f"выходит частями ({ex['partial']:.0%})")
    elif ex.get("full") is not None:
        ex_bits.append(f"выходит целиком ({ex['full']:.0%})")
    if ex.get("first_tp_gain_p50") is not None:
        ex_bits.append(f"первая фиксация прибыли на {fmt_pct(ex['first_tp_gain_p50'], signed=True)} (медиана)")
    if ex.get("stop_loss_p50") is not None:
        ex_bits.append(f"убыточные сделки закрывает около {fmt_pct(ex['stop_loss_p50'])}")
    if ex_bits:
        parts.append("Выход: " + ", ".join(ex_bits) + ".")
    timing = [(k, v) for k, v in (sel.get("sector_timing") or {}).items() if v["n"] >= 5 and k not in ("Unknown",)]
    for k, v in sorted(timing, key=lambda kv: -kv[1]["n"])[:2]:
        if (v.get("before_growth_share") or 0) >= 0.35:
            parts.append(f"В секторе {k} чаще входит ДО роста: {v['before_growth_share']:.0%} входов — перед движением ≥+30%.")
        elif (v.get("after_growth_share") or 0) >= 0.5:
            parts.append(f"В секторе {k} обычно входит уже ПОСЛЕ роста (за 7д до входа ≥+20% в {v['after_growth_share']:.0%} случаев).")
    rg = p.get("regime") or {}
    up, dn = rg.get("btc_up") or {}, rg.get("btc_down") or {}
    if up.get("n", 0) >= 5 and dn.get("n", 0) >= 5 and up.get("fwd_24h_p50") is not None and dn.get("fwd_24h_p50") is not None:
        better = "растущем" if up["fwd_24h_p50"] > dn["fwd_24h_p50"] else "падающем"
        parts.append(f"Входы работают лучше при {better} BTC (24ч после входа: {fmt_pct(up['fwd_24h_p50'], signed=True)}"
                     f" при росте BTC против {fmt_pct(dn['fwd_24h_p50'], signed=True)} при падении).")
    fl = p.get("flows") or {}
    if (fl.get("to_cex") or {}).get("n"):
        parts.append(f"Выводит на биржи: {fl['to_cex']['n']} переводов на {fmt_usd(fl['to_cex']['usd'])}"
                     " — часть выходов могла быть на CEX, их результат не учтён.")
    a24, a7 = af.get("24h") or {}, af.get("7d") or {}
    if a24.get("n"):
        s = (f"После его входов цена через 24ч: медиана {fmt_pct(a24['p50'], signed=True)},"
             f" рост в {a24['pos_share']:.0%} случаев")
        if a7.get("n"):
            s += f"; через 7д медиана {fmt_pct(a7['p50'], signed=True)}"
        if af.get("big_move_share") is not None:
            s += f"; движение ≥+30% за неделю после {af['big_move_share']:.0%} входов"
        parts.append(s + ".")
    return " ".join(parts)


def render_card(address: str, score: float | None, flags: dict, prof: dict | None, m: dict) -> str:
    """Текстовая карточка кошелька в формате ТЗ."""
    L = []
    line = "━" * 38
    L.append(f"WALLET {address}")
    L.append(f"Балл: {score if score is not None else '—'}  |  стиль: {(prof or {}).get('style', {}).get('label', '—')}")
    if flags:
        L.append("Флаги: " + "; ".join(v["msg"] for v in flags.values()))
    L.append("")
    L.append("Performance")
    L.append(line)
    L.append(f"Realized PnL      {fmt_usd(m.get('realized_pnl'), signed=True)}")
    L.append(f"Unrealized PnL    {fmt_usd(m.get('unrealized_pnl'), signed=True)}")
    L.append(f"ROI (капитал)     {fmt_pct(m.get('roi_capital'), signed=True)}   ROI (сделки) {fmt_pct(m.get('roi_trades'), signed=True)}")
    L.append(f"Win Rate          {fmt_pct(m.get('win_rate'))}  (нижняя граница 95%: {fmt_pct(m.get('win_rate_lb'))})")
    pf = m.get("profit_factor")
    L.append(f"Profit Factor     {pf:.2f}" if pf is not None else "Profit Factor     —")
    L.append(f"Средняя прибыль   {fmt_pct(m.get('avg_win_pct'), signed=True)}   средний убыток {fmt_pct(m.get('avg_loss_pct'))}")
    L.append(f"Max Drawdown      {fmt_pct(m.get('max_dd_norm'))} (норм.)   {fmt_pct(m.get('max_dd_capital'))} (капитал)")
    L.append(f"Закрытых сделок   {m.get('closed', 0)}   открыто {m.get('open', 0)}   токенов {m.get('n_tokens', 0)}")
    L.append(f"История           {m.get('history_days', 0):.0f} дн.   объём {fmt_usd(m.get('volume_usd'))}")
    w = m.get("windows") or {}
    if w:
        L.append("Окна             " + "  ".join(
            f"{k}: {fmt_usd(v.get('pnl'), signed=True)}/{v.get('n', 0)}" for k, v in w.items()))
    if not prof:
        return "\n".join(L)
    st, sel, en, bl, ex, af = (prof[k] for k in ("style", "selection", "entry", "building", "exit", "after_entry"))
    L += ["", "Trading Style", line,
          f"Сделок в месяц    {(st.get('trades_per_month') or 0):.1f}",
          f"Удержание         среднее {fmt_dur(st.get('hold_avg'))}, медиана {fmt_dur(st.get('hold_p50'))}",
          f"                  P25 {fmt_dur(st.get('hold_p25'))} · P75 {fmt_dur(st.get('hold_p75'))} · P90 {fmt_dur(st.get('hold_p90'))}",
          f"Средняя позиция   {fmt_usd(st.get('avg_position'))} (медиана {fmt_usd(st.get('median_position'))},"
          f" ~{fmt_pct(st.get('position_pct_capital'))} капитала)",
          "", "Asset Selection", line,
          f"Капитализация     {_range_usd(sel.get('mc_p25'), sel.get('mc_p75'))} (медиана {fmt_usd(sel.get('mc_p50'))})",
          f"Возраст токена    {_range_days(sel.get('age_p25'), sel.get('age_p75'))}",
          f"Ликвидность пула  медиана {fmt_usd(sel.get('liq_p50'))}",
          "Сектора           " + ", ".join(f"{k} {v:.0%}" for k, v in list(sel.get("sectors", {}).items())[:4]),
          f"На CEX при входе  {fmt_pct(sel.get('cex_listed_at_entry_share'))}  (сейчас {fmt_pct(sel.get('cex_listed_now_share'))})",
          "Любимые токены    " + ", ".join(f"{t['symbol']}×{t['entries']} ({fmt_dur(t.get('hold_p50'))})"
                                           for t in sel.get("top_tokens", [])[:6]),
          "", "Entry Behavior", line]
    for t, s in en.get("types", {}).items():
        bt = en.get("by_type", {}).get(t, {})
        f24 = (bt.get("fwd_24h") or {}).get("p50")
        L.append(f"{ENTRY_RU.get(t, t):<18}{s:>5.0%}   цена через 24ч: {fmt_pct(f24, signed=True)}")
    L.append(f"До входа (медианы): 24ч {fmt_pct(en.get('ret_24h_p50'), signed=True)}, 7д {fmt_pct(en.get('ret_7d_p50'), signed=True)},"
             f" объём ×{(en.get('vol_ratio_p50') or 0):.2f}, от 7д-макс {fmt_pct(en.get('dd_high_7d_p50'))}")
    L += ["", "Position Building", line,
          f"Одна покупка      {fmt_pct(bl.get('single'))}",
          f"DCA               {fmt_pct(bl.get('dca'))}  (вниз {fmt_pct(bl.get('dca_down'))}, пирамида {fmt_pct(bl.get('pyramid'))})",
          f"Покупок в цикле   медиана {bl.get('buys_p50') or '—'}, интервал доборов {fmt_dur(bl.get('add_gap_p50'))}",
          "", "Exit Behavior", line,
          f"Полный выход      {fmt_pct(ex.get('full'))}",
          f"Частичный выход   {fmt_pct(ex.get('partial'))}   (перевод {fmt_pct(ex.get('transfer'))})",
          f"Первая фиксация   {fmt_pct(ex.get('first_tp_gain_p50'), signed=True)}, продаёт {fmt_pct(ex.get('first_sell_fraction_p50'))} позиции",
          f"Стоп (медиана)    {fmt_pct(ex.get('stop_loss_p50'))}",
          f"Цена после продажи: 24ч {fmt_pct(ex.get('after_sell_24h_p50'), signed=True)}, 7д {fmt_pct(ex.get('after_sell_7d_p50'), signed=True)}",
          "Результат продаж  " + ", ".join(f"{k} {v:.0%}" for k, v in (ex.get("gain_buckets") or {}).items()),
          "", "After Entry (что было с ценой)", line]
    for h in ("1h", "6h", "24h", "3d", "7d", "30d"):
        s = af.get(h) or {}
        if s.get("n"):
            L.append(f"{h:<5} медиана {fmt_pct(s['p50'], signed=True):>8}  среднее {fmt_pct(s['mean'], signed=True):>8}"
                     f"  рост {s['pos_share']:.0%}  (n={s['n']})")
    L.append(f"MFE 7д медиана {fmt_pct(af.get('mfe_7d_p50'), signed=True)}, MAE 7д {fmt_pct(af.get('mae_7d_p50'))},"
             f" ≥+30% за неделю: {fmt_pct(af.get('big_move_share'))}")
    st_rows = [(k, v) for k, v in (sel.get("sector_timing") or {}).items() if v["n"] >= 3]
    if st_rows:
        L += ["", "Sector Timing (до/после роста)", line]
        for k, v in sorted(st_rows, key=lambda kv: -kv[1]["n"])[:5]:
            L.append(f"{k:<14} входов {v['n']:>3}  7д до входа {fmt_pct(v.get('ret_7d_before_p50'), signed=True):>7}"
                     f"  7д после {fmt_pct(v.get('fwd_7d_p50'), signed=True):>7}  до роста {fmt_pct(v.get('before_growth_share'))}"
                     f"  после роста {fmt_pct(v.get('after_growth_share'))}")
    rg = prof.get("regime") or {}
    if rg:
        L += ["", "Market Regime", line]
        for k, ru in (("btc_up", "BTC растёт (7д)"), ("btc_down", "BTC падает (7д)")):
            v = rg.get(k) or {}
            L.append(f"{ru:<16} входов {v.get('n', 0):>3}  24ч после входа {fmt_pct(v.get('fwd_24h_p50'), signed=True):>7}"
                     f"  результат сделки {fmt_pct(v.get('rt_pnl_p50'), signed=True):>7}")
    fl = prof.get("flows") or {}
    if fl:
        L += ["", "Transfers", line,
              f"На CEX {fl['to_cex']['n']} ({fmt_usd(fl['to_cex']['usd'])}), с CEX {fl['from_cex']['n']}"
              f" ({fmt_usd(fl['from_cex']['usd'])}), другим кошелькам {fl['to_other']}, от других {fl['from_other']}"
              + ("" if fl.get("cex_names") else "  (метки бирж — в wsa_labels.csv)")]
    L += ["", "Detected Strategy", line, prof.get("detected_strategy", "")]
    return "\n".join(L)
