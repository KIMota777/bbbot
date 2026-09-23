"""Загрузка истории кошелька, разбор в сделки, метрики и балл.

Две глубины:
- quick — последние ~400 транзакций: дёшево отсеять ботов и пустышки;
- deep — до 180 дней / 4000 транзакций: для прошедших первичный отсев.
"""
from __future__ import annotations

import json
import logging

from ..chains import evm as evm_chain
from ..chains import solana as sol_chain
from ..core.metrics import compute_metrics
from ..core.positions import build_round_trips
from ..core.pricing import is_excluded, native_id
from ..core.scoring import flags_for, score_wallet
from ..util import DAY, now

log = logging.getLogger("wsa.scan")


# ---------------- Solana ----------------

def _sig_rate(page: list[dict]) -> float | None:
    """Транзакций в сутки по странице подписей (новые -> старые)."""
    ts = [s.get("blockTime") for s in page if s.get("blockTime")]
    if len(ts) < 20:
        return None
    span = max(ts[0] - ts[-1], 60)
    return len(ts) / (span / DAY)


def fetch_solana(ctx, address: str, max_tx: int, days: int, progress=None, new_only: bool = False,
                 source: str = "backfill") -> dict:
    """Догружает историю кошелька. Курсоры пишутся в базу только ПОСЛЕ обработки
    транзакций: прерванный скан повторится с того же места, а не потеряет кусок."""
    db, rpc, cfg = ctx.db, ctx.rpc, ctx.cfg
    chain = "solana"
    st = db.sync(chain, address)
    seen = db.seen_sigs(chain, address)
    collected: list[dict] = []
    cursor: dict = {}

    # 1) новое с прошлой синхронизации
    if st.get("newest_sig"):
        before = None
        for _ in range(50):
            page = rpc.signatures(address, before=before, until=st["newest_sig"], limit=1000)
            if not page:
                break
            collected += page
            before = page[-1]["signature"]
            if len(page) < 1000:
                break
    # 2) вглубь истории
    cutoff = now() - days * DAY
    rate = st.get("sigs_per_day")
    if not st.get("backfill_complete") and not new_only:
        before = st.get("oldest_sig")
        total = st.get("sig_count") or 0
        while total < max_tx:
            # страница подписей — один дешёвый запрос, берём всегда полную:
            # по ней видно частоту, и бота отсекаем ДО загрузки транзакций
            page = rpc.signatures(address, before=before, limit=1000)
            if not page:
                cursor["backfill_complete"] = 1
                break
            if rate is None:
                rate = _sig_rate(page)
                if rate and rate > cfg.solana.bot_sigs_per_day:
                    db.set_sync(chain, address, sigs_per_day=rate)
                    return {"bot": True, "sigs_per_day": rate, "fetched": 0}
            full_page = len(page) == 1000
            page = page[:max_tx - total]
            collected += page
            total += len(page)
            before = page[-1]["signature"]
            cursor.update(oldest_sig=before, oldest_ts=page[-1].get("blockTime"), sig_count=total)
            if (page[-1].get("blockTime") or 0) < cutoff or (not full_page and total < max_tx):
                cursor["backfill_complete"] = 1
                break
    if rate is None and collected:
        rate = _sig_rate(collected)
    newest = collected[0] if collected else None
    todo = [s for s in collected if s["signature"] not in seen and not s.get("err")
            and (s.get("blockTime") or now()) >= cutoff]
    todo += db.retry_sigs(chain, address)  # недогруженные в прошлый раз
    failed = [(s["signature"], s.get("blockTime") or 0, "failed") for s in collected
              if s.get("err") and s["signature"] not in seen]

    txs = rpc.transactions([s["signature"] for s in todo], progress=progress)
    legs, seen_rows = [], []
    missing = 0
    for s in todo:
        tx = txs.get(s["signature"])
        if not tx:
            missing += 1
            # в очередь повторов: курсор уже ушёл дальше, иначе транзакция потеряется
            seen_rows.append((s["signature"], s.get("blockTime") or 0, "retry"))
            continue
        p = sol_chain.parse_tx(tx, address)
        kind = p["kind"]
        if kind == "swap":
            custom = [pid for pid in p["programs"] if pid not in sol_chain.KNOWN_PROGRAMS]
            if custom:
                kind = "swap:custom"
        seen_rows.append((s["signature"], p["ts"], kind))
        legs += p["legs"]
    legs = ctx.pricer.value_legs(chain, legs)
    min_usd = cfg.scan.min_leg_usd
    legs = [l for l in legs if not (l["side"] in ("buy", "sell") and l.get("usd") is not None and l["usd"] < min_usd)]
    db.save_legs(chain, address, legs, source=source)
    db.mark_seen(chain, address, seen_rows + failed)
    upd = {"sigs_per_day": rate, **cursor}
    if newest and (not st.get("newest_ts") or (newest.get("blockTime") or 0) >= (st.get("newest_ts") or 0)):
        upd.update(newest_sig=newest["signature"], newest_ts=newest.get("blockTime"))
    if missing:
        log.info("%s: %d транзакций не загрузились — в очереди повторов до следующего скана", address[:8], missing)
    db.set_sync(chain, address, **upd)
    return {"bot": False, "sigs_per_day": rate, "fetched": len(todo), "legs": len(legs), "missing": missing,
            "new_legs": legs}


# ---------------- EVM ----------------

def fetch_evm(ctx, chain: str, address: str, max_items: int, days: int, progress=None,
              source: str = "backfill") -> dict:
    db = ctx.db
    ex = ctx.evm(chain)
    st = db.sync(chain, address)
    cutoff = now() - days * DAY
    start = (st.get("newest_block") or 0) + 1 if st.get("newest_block") else None
    # страница эксплорера — 1000 записей за ту же цену запроса; меньше брать нет смысла:
    # у EVM-кошельков последние записи часто забиты спам-токенами
    max_items = max(max_items, 1000)
    tok = ex.account_list("tokentx", address, cutoff, max_items, startblock=start)
    txl = ex.account_list("txlist", address, cutoff, max_items, startblock=start)
    itx = ex.account_list("txlistinternal", address, cutoff, max_items, startblock=start)
    rate = None
    if txl:
        ts = [int(r["timeStamp"]) for r in txl]
        span = max(max(ts) - min(ts), 60)
        rate = len(ts) / (span / DAY) if len(ts) >= 20 else None
    groups = evm_chain.group_transfers(chain, address, tok, txl, itx)
    seen = db.seen_sigs(chain, address)
    legs, seen_rows = [], []
    for g in groups:
        if g["sig"] in seen:
            continue
        kind, gl = evm_chain.legs_from_group(chain, g)
        seen_rows.append((g["sig"], g["ts"], kind))
        legs += gl
    legs = ctx.pricer.value_legs(chain, legs)
    min_usd = ctx.cfg.scan.min_leg_usd
    legs = [l for l in legs if not (l["side"] in ("buy", "sell") and l.get("usd") is not None and l["usd"] < min_usd)]
    db.save_legs(chain, address, legs, source=source)
    db.mark_seen(chain, address, seen_rows)
    blocks = [g["block"] for g in groups]
    db.set_sync(chain, address, sigs_per_day=rate, newest_block=max(blocks) if blocks else st.get("newest_block"),
                backfill_complete=1)
    return {"bot": False, "sigs_per_day": rate, "fetched": len(groups), "legs": len(legs), "new_legs": legs}


# ---------------- анализ ----------------

def trade_legs(ctx, chain: str, address: str) -> list[dict]:
    """Ноги для анализа: без стейблов (известных и «похожих»), LST и пыли."""
    return [l for l in ctx.db.legs(chain, address)
            if not is_excluded(chain, l["token"]) and not ctx.pricer.is_stable_like(chain, l["token"])]


def current_prices(ctx, chain: str, tokens: set[str]) -> dict[str, float]:
    out = {}
    nat = native_id(chain)
    rest = [t for t in tokens if t != nat]
    ctx.tokens.refresh(chain, rest)
    for t in rest:
        m = ctx.tokens.meta(chain, t, max_age=3600)
        if m and m.get("price_usd"):
            out[t] = m["price_usd"]
    if nat in tokens:
        from ..core.pricing import native_symbol
        p = ctx.ref.last_price(f"{native_symbol(ctx, chain)}/USDT")
        if p:
            out[nat] = p
    return out


def cex_volume_share(ctx, chain: str, legs: list[dict]) -> tuple[float | None, dict]:
    """Доля оборота кошелька в токенах, которые торгуются на нашей CEX."""
    vol: dict[str, float] = {}
    for l in legs:
        if l["side"] in ("buy", "sell") and l.get("usd"):
            vol[l["token"]] = vol.get(l["token"], 0.0) + l["usd"]
    if not vol:
        return None, {}
    total = sum(vol.values())
    listed = {}
    # проверяем токены по убыванию оборота: хвост из мелочи на долю почти не влияет
    acc = 0.0
    for t, v in sorted(vol.items(), key=lambda kv: -kv[1]):
        if acc > 0.97 * total and len(listed) > 0:
            break
        acc += v
        m = ctx.tokens.cex_map(chain, t, which="trade")
        if m:
            listed[t] = m["symbol"]
    return sum(vol[t] for t in listed) / total, listed


def analyze(ctx, chain: str, address: str, save: bool = True) -> dict:
    legs = trade_legs(ctx, chain, address)
    trips, orphan = build_round_trips(chain, address, legs)
    open_tokens = {t.token for t in trips if t.status == "open" and t.qty > 0}
    prices = current_prices(ctx, chain, open_tokens) if open_tokens else {}
    st = ctx.db.sync(chain, address)
    seen = ctx.db.query("SELECT kind, COUNT(*) n FROM seen_tx WHERE chain=? AND address=? GROUP BY kind",
                        (chain, address))
    kinds = {r["kind"]: r["n"] for r in seen}
    swaps_total = kinds.get("swap", 0) + kinds.get("swap:custom", 0)
    share, listed = cex_volume_share(ctx, chain, legs)
    sig_stats = {
        "sigs_per_day": st.get("sigs_per_day"),
        "custom_program_share": (kinds.get("swap:custom", 0) / swaps_total) if swaps_total else None,
        "cex_volume_share": share,
        "cex_tokens": listed,
        "tx_kinds": kinds,
    }
    m = compute_metrics(trips, legs, now(), prices, orphan, sig_stats)
    flags = flags_for(m, ctx.cfg)
    score, comp = score_wallet(m, flags, ctx.cfg)
    m["score_components"] = comp
    if save:
        prev = ctx.db.analysis(chain, address) or {}
        ctx.db.save_analysis(chain, address, score, m, flags, prev.get("profile"))
        save_round_trips(ctx, chain, address, trips)
        status = "bot" if any(v["level"] == "hard" for v in flags.values()) else None
        w = ctx.db.wallet(chain, address)
        fields = {"score": score, "scanned_at": now()}
        if status and (not w or w["status"] != "watch"):
            fields["status"] = status
        elif w and w["status"] in ("candidate", "bot"):
            fields["status"] = "scanned"
        ctx.db.set_wallet(chain, address, **fields)
    return {"metrics": m, "flags": flags, "score": score, "trips": trips}


def save_round_trips(ctx, chain: str, address: str, trips) -> None:
    rows = []
    for t in trips:
        detail = {
            "fills": [{"ts": f.ts, "side": f.side, "qty": f.qty, "usd": f.usd, "price": f.price,
                       "pnl": f.pnl, "gain": f.gain, "hold": f.hold, "sig": f.sig} for f in t.fills],
            "tin_qty": t.tin_qty, "tout_qty": t.tout_qty, "max_qty": t.max_qty, "qty": t.qty,
            "open_cost": t.open_cost,
        }
        rows.append((chain, address, t.key, t.token, t.open_ts, t.close_ts, t.status, t.n_buys, t.n_sells,
                     t.cost, t.proceeds, t.realized, t.pnl_pct, t.hold_sec, t.peak_cost, None, t.build,
                     t.exit_kind, t.unmatched_qty, json.dumps(detail)))
    with ctx.db.tx() as c:
        c.execute("DELETE FROM round_trips WHERE chain=? AND wallet=?", (chain, address))
        c.executemany(
            "INSERT INTO round_trips(chain, wallet, rt_key, token, open_ts, close_ts, status, n_buys, n_sells,"
            " cost, proceeds, pnl, pnl_pct, hold_sec, max_cost, entry_type, build, exit_kind, unmatched_qty, detail)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def scan_wallet(ctx, chain: str, address: str, depth: str = "quick", progress=None) -> dict:
    cfg = ctx.cfg.scan
    max_tx = cfg.quick_max_tx if depth == "quick" else cfg.deep_max_tx
    days = cfg.deep_days
    if ctx.db.wallet(chain, address) is None:
        ctx.db.upsert_candidate(chain, address, source="manual")
    if depth == "deep":
        # глубокий проход продолжает историю с места, где остановился быстрый
        ctx.db.set_sync(chain, address, backfill_complete=0)
    if chain == "solana":
        res = fetch_solana(ctx, address, max_tx, days, progress)
    else:
        res = fetch_evm(ctx, chain, address, max_tx, days, progress)
    if res.get("bot"):
        ctx.db.set_wallet(chain, address, status="bot", score=0.0, scanned_at=now(), scan_depth=depth,
                          note=f"{res['sigs_per_day']:.0f} tx/сутки")
        ctx.db.save_analysis(chain, address, 0.0, {"sigs_per_day": res["sigs_per_day"]},
                             {"bot_frequency": {"level": "hard", "msg": f"{res['sigs_per_day']:.0f} транзакций в сутки — бот"}},
                             None)
        return {"status": "bot", **res}
    out = analyze(ctx, chain, address)
    ctx.db.set_wallet(chain, address, scan_depth=depth)
    return {"status": "ok", **res, "score": out["score"], "flags": out["flags"], "metrics": out["metrics"]}
