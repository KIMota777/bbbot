"""Разбор транзакции Solana в «ноги» сделок кошелька.

Источник истины — изменение балансов кошелька (до/после), а не инструкции:
так одинаково разбираются Jupiter, Raydium, Orca, Pump.fun и любые терминалы.

Правила:
- нативный SOL и wSOL — один актив «SOL»;
- комиссия сети и рента за созданные/закрытые токен-счета из SOL-ноги убираются;
- ранги: стейблкоин (0) < SOL (1) < остальные токены (2). В свопе актив
  с большим рангом — то, чем торгуют, с меньшим — чем платят. Поэтому
  SOL→USDC — продажа SOL, а SOL→BONK — покупка BONK за SOL;
- токен↔токен (BONK→WIF) — две ноги: продажа BONK и покупка WIF,
  оценка в USD по рыночной цене любой из сторон.
"""
from __future__ import annotations

from collections import defaultdict

WSOL = "So11111111111111111111111111111111111111112"
SOL = "SOL"  # псевдо-адрес нативного SOL

STABLES = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB": "USD1",
    "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo": "PYUSD",
}

# ликвидные стейкинг-токены: по сути SOL, в аналитику сделок не идут
LSTS = {
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "mSOL",
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": "JitoSOL",
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1": "bSOL",
    "jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v": "jupSOL",
    "5oVNBeEEQvYi1cX3ir8Dx5n1P7pdxydbGF2X4TxVusJm": "INF",
}

# известные программы: DEX, агрегаторы, терминалы. Остальное во внешних
# инструкциях свопа — вероятно, собственный контракт бота.
KNOWN_PROGRAMS = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "Jupiter v4",
    "DCA265Vj8a9CEuX1eb1LWRnDT7uK6q1xMipnNyatn23M": "Jupiter DCA",
    "jupoNjAxXgZ4rjzxzPMP4oxduvQsQtZzyknqvzYNrNu": "Jupiter Limit",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium AMM",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "Raydium CPMM",
    "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj": "Raydium LaunchLab",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora DLMM",
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": "Meteora AMM",
    "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG": "Meteora DAMM v2",
    "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN": "Meteora DBC",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "Pump.fun",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "PumpSwap",
    "PhoeNiXZ8ByJGLkxNfZRnkUfjvmTYqK3Vx1ifh1r7i5": "Phoenix",
    "opnb2LAfJYbRMAHHvqjCwQxanZn7ReEHp1k81EohpZb": "OpenBook",
    "2wT8Yq49kHgDzXuPxZSaeLaH1qbmGXtEyPy64bL7aD3c": "Lifinity",
    "6m2CDdhRgxpH4WjvdzxAYbGxwdGUz5MziiL5jek2kBma": "OKX DEX",
    "BSfD6SHZigAfDWSjzD5Q41jw8LmKwtmjskPH9XW1mrRW": "Photon",
    "MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG": "Moonshot",
}
SYSTEM_PROGRAMS = {
    "11111111111111111111111111111111",
    "ComputeBudget111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
    "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo",
}

RENT_TOKEN_ACCOUNT = 2_039_280  # лампорты ренты за 165-байтный токен-счёт
SOL_DUST = 0.005  # меньше — чаевые Jito/рента/пыль, не нога сделки


def rank(asset: str) -> int:
    if asset in STABLES:
        return 0
    if asset == SOL:
        return 1
    return 2


def is_quote(asset: str) -> bool:
    return rank(asset) < 2


def is_excluded(asset: str) -> bool:
    """Активы, которые не анализируются как сделки (стейблы, LST)."""
    return asset in STABLES or asset in LSTS


def _keys(tx: dict) -> list[str]:
    msg = (tx.get("transaction") or {}).get("message") or {}
    out = []
    for k in msg.get("accountKeys") or []:
        out.append(k["pubkey"] if isinstance(k, dict) else k)
    # для не-jsonParsed кодировок адреса из таблиц поиска лежат отдельно
    la = (tx.get("meta") or {}).get("loadedAddresses") or {}
    if la and len(out) < len((tx.get("meta") or {}).get("preBalances") or []):
        out += la.get("writable", []) + la.get("readonly", [])
    return out


def balance_deltas(tx: dict, wallet: str) -> tuple[dict[str, float], dict]:
    """Изменение балансов кошелька по активам (SOL уже с учётом wSOL/комиссии/ренты)."""
    meta = tx.get("meta") or {}
    keys = _keys(tx)
    info = {"fee_payer": keys[0] if keys else None, "fee": meta.get("fee", 0) / 1e9}
    lamports = 0
    if wallet in keys:
        i = keys.index(wallet)
        pre_b, post_b = meta.get("preBalances") or [], meta.get("postBalances") or []
        if i < len(pre_b) and i < len(post_b):
            lamports = post_b[i] - pre_b[i]
        if i == 0:
            lamports += meta.get("fee", 0)

    pre = {b["accountIndex"]: b for b in meta.get("preTokenBalances") or [] if b.get("owner") == wallet}
    post = {b["accountIndex"]: b for b in meta.get("postTokenBalances") or [] if b.get("owner") == wallet}
    raw: dict[str, int] = defaultdict(int)
    dec: dict[str, int] = {}
    for idx in set(pre) | set(post):
        b_pre, b_post = pre.get(idx), post.get(idx)
        ref = b_post or b_pre
        mint = ref["mint"]
        dec[mint] = int(ref["uiTokenAmount"]["decimals"])
        a_pre = int(b_pre["uiTokenAmount"]["amount"]) if b_pre else 0
        a_post = int(b_post["uiTokenAmount"]["amount"]) if b_post else 0
        raw[mint] += a_post - a_pre

    if info["fee_payer"] == wallet:
        created = len(set(post) - set(pre))
        closed = len(set(pre) - set(post))
        lamports += (created - closed) * RENT_TOKEN_ACCOUNT

    deltas: dict[str, float] = {}
    sol = lamports + raw.pop(WSOL, 0)
    dec.pop(WSOL, None)
    if abs(sol) / 1e9 >= SOL_DUST:
        deltas[SOL] = sol / 1e9
    for mint, r in raw.items():
        if r:
            deltas[mint] = r / (10 ** dec.get(mint, 0))
    info["token_accounts_created"] = len(set(post) - set(pre))
    return deltas, info


def _counterparty(tx: dict, wallet: str, asset: str, sign: int) -> str | None:
    """Кто отдал/получил актив в переводе: владелец с противоположным изменением."""
    meta = tx.get("meta") or {}
    if asset == SOL:
        keys = _keys(tx)
        best, best_d = None, 0
        for i, k in enumerate(keys):
            if k == wallet or i >= len(meta.get("preBalances") or []):
                continue
            d = meta["postBalances"][i] - meta["preBalances"][i]
            if d * sign < 0 and abs(d) > abs(best_d):
                best, best_d = k, d
        return best
    ch: dict[str, int] = defaultdict(int)
    for b in meta.get("preTokenBalances") or []:
        if b.get("mint") == asset and b.get("owner") and b["owner"] != wallet:
            ch[b["owner"]] -= int(b["uiTokenAmount"]["amount"])
    for b in meta.get("postTokenBalances") or []:
        if b.get("mint") == asset and b.get("owner") and b["owner"] != wallet:
            ch[b["owner"]] += int(b["uiTokenAmount"]["amount"])
    cands = [(o, d) for o, d in ch.items() if d * sign < 0]
    return max(cands, key=lambda x: abs(x[1]))[0] if cands else None


def outer_programs(tx: dict) -> list[str]:
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = _keys(tx)
    out = []
    for ins in msg.get("instructions") or []:
        pid = ins.get("programId")
        if pid is None and "programIdIndex" in ins and ins["programIdIndex"] < len(keys):
            pid = keys[ins["programIdIndex"]]
        if pid and pid not in SYSTEM_PROGRAMS:
            out.append(pid)
    return list(dict.fromkeys(out))


def legs_from_deltas(deltas: dict[str, float]) -> list[dict]:
    """Ноги сделки из изменений балансов (без USD — их ставит оценщик)."""
    neg = [(a, -q) for a, q in deltas.items() if q < 0]
    pos = [(a, q) for a, q in deltas.items() if q > 0]
    legs: list[dict] = []
    if neg and pos:
        if len(neg) == 1 and len(pos) == 1:
            (a, qa), (b, qb) = neg[0], pos[0]
            ra, rb = rank(a), rank(b)
            if ra < rb:
                legs.append({"token": b, "side": "buy", "qty": qb, "quote": a, "quote_qty": qa})
            elif ra > rb:
                legs.append({"token": a, "side": "sell", "qty": qa, "quote": b, "quote_qty": qb})
            elif ra == 2:
                legs.append({"token": a, "side": "sell", "qty": qa, "quote": b, "quote_qty": qb})
                legs.append({"token": b, "side": "buy", "qty": qb, "quote": a, "quote_qty": qa})
            # стейбл↔стейбл — не сделка
            return legs
        toks = [(a, q) for a, q in deltas.items() if rank(a) == 2]
        if toks:
            # сложный своп (несколько токенов разом): каждую ногу оценим по рынку
            quotes_paid = [(a, -q) for a, q in deltas.items() if rank(a) < 2 and q < 0]
            quotes_got = [(a, q) for a, q in deltas.items() if rank(a) < 2 and q > 0]
            for a, q in toks:
                side = "buy" if q > 0 else "sell"
                qs = quotes_paid if side == "buy" else quotes_got
                if len(toks) == 1 and len(qs) == 1:
                    legs.append({"token": a, "side": side, "qty": abs(q), "quote": qs[0][0], "quote_qty": qs[0][1]})
                else:
                    legs.append({"token": a, "side": side, "qty": abs(q), "quote": None, "quote_qty": None})
            return legs
        # только SOL и стейблы: сделка с SOL против суммы стейблов
        if SOL in deltas:
            q = deltas[SOL]
            st = sum(v for a, v in deltas.items() if rank(a) == 0 and v * q < 0)
            if st:
                side = "buy" if q > 0 else "sell"
                legs.append({"token": SOL, "side": side, "qty": abs(q), "quote": "USD", "quote_qty": abs(st)})
        return legs
    for a, q in pos:
        legs.append({"token": a, "side": "tin", "qty": q, "quote": None, "quote_qty": None})
    for a, q in neg:
        legs.append({"token": a, "side": "tout", "qty": q, "quote": None, "quote_qty": None})
    return legs


def parse_tx(tx: dict, wallet: str) -> dict:
    """Транзакция -> {sig, ts, slot, kind, legs, programs, fee}.

    kind: swap | transfer | failed | other
    """
    sig = ((tx.get("transaction") or {}).get("signatures") or [None])[0]
    meta = tx.get("meta") or {}
    base = {"sig": sig, "ts": tx.get("blockTime") or 0, "slot": tx.get("slot"), "legs": [],
            "programs": outer_programs(tx), "fee": meta.get("fee", 0) / 1e9}
    if meta.get("err") is not None:
        return {**base, "kind": "failed"}
    deltas, info = balance_deltas(tx, wallet)
    legs = legs_from_deltas(deltas)
    for i, l in enumerate(legs):
        l["idx"] = i
        l["sig"] = sig
        l["ts"] = base["ts"]
        l["slot"] = base["slot"]
        if l["side"] in ("tin", "tout"):
            l["counterparty"] = _counterparty(tx, wallet, l["token"], 1 if l["side"] == "tin" else -1)
    if not legs:
        kind = "other"
    elif any(l["side"] in ("buy", "sell") for l in legs):
        kind = "swap"
    else:
        kind = "transfer"
    return {**base, "kind": kind, "legs": legs, "signer": info["fee_payer"] == wallet}
