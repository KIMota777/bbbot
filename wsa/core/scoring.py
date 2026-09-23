"""Фильтры (боты, MEV, инсайдеры, «одна удачная монета») и итоговый балл 0–100.

Жёсткие флаги (hard) — кошелёк исключается: за ним нельзя следовать в
принципе (арбитраж, сэндвичи, HFT — их сделки не повторить с CEX).
Мягкие (soft) — штраф к баллу и пометка в карточке.
"""
from __future__ import annotations

from ..util import clamp, squash


def flags_for(m: dict, cfg) -> dict:
    f = cfg.filters
    out: dict[str, dict] = {}

    def hard(k, msg):
        out[k] = {"level": "hard", "msg": msg}

    def soft(k, msg):
        out[k] = {"level": "soft", "msg": msg}

    spd = m.get("sigs_per_day")
    if spd and spd > cfg.solana.bot_sigs_per_day:
        hard("bot_frequency", f"{spd:.0f} транзакций в сутки — бот")
    if (m.get("swaps_per_day") or 0) > f.max_swaps_per_day and m.get("n_swaps", 0) > 50:
        hard("bot_swaps", f"{m['swaps_per_day']:.0f} свопов в активный день — бот/скальпер")
    hold = (m.get("hold") or {}).get("p50")
    if hold is not None and hold < f.min_median_hold_sec and m.get("closed", 0) >= 10:
        hard("hft_hold", f"медианное удержание {hold:.0f}с — HFT/арбитраж")
    if (m.get("fast_share") or 0) > f.max_same_slot_share and m.get("closed", 0) >= 10:
        hard("mev", f"{m['fast_share']:.0%} циклов закрыты за ≤10с — MEV/сэндвичи")
    if (m.get("custom_program_share") or 0) > 0.6 and m.get("n_swaps", 0) >= 20:
        soft("custom_program", f"{m['custom_program_share']:.0%} свопов через неизвестный контракт — возможно, бот")
    if m.get("closed", 0) < f.min_closed_trades:
        soft("low_sample", f"мало закрытых сделок: {m.get('closed', 0)}")
    share = m.get("top1_token_share")
    if share is not None and share > f.max_top1_pnl_share and (m.get("realized_pnl") or 0) > 0:
        soft("one_hit", f"{share:.0%} прибыли — один токен")
    if (m.get("unmatched_share") or 0) > f.max_unmatched_share:
        soft("unmatched", f"{m['unmatched_share']:.0%} продаж без покупки в истории — эйрдроп/инсайд/перевод")
    if (m.get("history_days") or 0) < f.min_history_days:
        soft("short_history", f"история {m.get('history_days', 0):.0f} дн.")
    cs = m.get("cex_volume_share")
    if cs is not None and cs < f.min_cex_volume_share:
        soft("cex_irrelevant", f"лишь {cs:.0%} оборота в токенах с листингом на CEX")
    if (m.get("realized_pnl") or 0) <= 0:
        soft("unprofitable", "реализованный PnL ≤ 0")
    return out


def score_wallet(m: dict, flags: dict, cfg) -> tuple[float, dict]:
    """Итоговый балл и его составляющие (каждая 0..1)."""
    w = cfg.scoring
    roi = min(m.get("roi_capital") or 0.0, 10.0)
    realized = m.get("realized_pnl") or 0.0
    pf = m.get("profit_factor") or 0.0
    wr_lb = m.get("win_rate_lb") or 0.0
    ts = m.get("tstat_pct") or 0.0
    comp = {
        "profit": 0.5 * squash(max(roi, 0), 1.0) + 0.5 * squash(max(realized, 0), 25_000),
        "quality": 0.4 * squash(max(min(pf, 10) - 1, 0), 1.5)
                   + 0.35 * clamp((wr_lb - 0.3) / 0.4, 0, 1)
                   + 0.25 * squash(max(ts, 0), 2.5),
        "risk": 1 - clamp((m.get("max_dd_norm") or 0) / 0.4, 0, 1),
        "consistency": 0.6 * (m.get("positive_windows_share") or 0) + 0.4 * (m.get("positive_months_share") or 0),
        "sample": squash(m.get("closed", 0), 40),
        "diversity": 0.5 * squash(m.get("profitable_tokens", 0), 8) + 0.5 * (1 - clamp(m.get("top1_token_share") or 0, 0, 1)),
        "cex": clamp(m.get("cex_volume_share") or 0, 0, 1),
    }
    total = sum(comp[k] * w[f"w_{k}"] for k in comp)
    score = 100 * total / sum(w[f"w_{k}"] for k in comp)
    if any(v["level"] == "hard" for v in flags.values()):
        score = 0.0
    else:
        penalties = {"low_sample": 0.7, "one_hit": 0.75, "unmatched": 0.8, "short_history": 0.85,
                     "custom_program": 0.85, "cex_irrelevant": 0.8, "unprofitable": 0.35}
        for k, mult in penalties.items():
            if k in flags:
                score *= mult
    return round(score, 1), comp
