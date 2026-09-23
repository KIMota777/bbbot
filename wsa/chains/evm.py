"""Сборка свопов EVM из переводов: ERC-20 + нативный ETH, сгруппированные по хэшу.

Те же правила рангов, что и в Solana: стейбл (0) < ETH/WETH (1) < токен (2).
WETH сливается с нативным активом, как wSOL на Solana. Стейблы определяются
только по адресу контракта: «USDT» от мошенников с тем же тикером — не стейбл.
"""
from __future__ import annotations

from collections import defaultdict

NATIVE = "NATIVE"  # псевдо-адрес нативного актива сети (ETH/BNB/POL)

WRAPPED = {
    "ethereum": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    "base": "0x4200000000000000000000000000000000000006",
    "optimism": "0x4200000000000000000000000000000000000006",
    "arbitrum": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
    "polygon": "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270",
    "bsc": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
}

STABLES = {
    "ethereum": {
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
        "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
        "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
        "0x4c9edd5852cd905f086c759e8383e09bff1e68b3": "USDe",
        "0x6c3ea9036406852006290770bedfcaba0e23a0e8": "PYUSD",
        "0xdc035d45d973e3ec169d2276ddab16f1e407384f": "USDS",
    },
    "base": {
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",
        "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca": "USDbC",
        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb": "DAI",
    },
    "arbitrum": {
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831": "USDC",
        "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8": "USDC.e",
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": "USDT",
        "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1": "DAI",
    },
    "optimism": {
        "0x0b2c639c533813f4aa9d7837caf62653d097ff85": "USDC",
        "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58": "USDT",
        "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1": "DAI",
    },
    "polygon": {
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": "USDC",
        "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": "USDC.e",
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": "USDT",
    },
    "bsc": {
        "0x55d398326f99059ff775485246999027b3197955": "USDT",
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "USDC",
        "0xe9e7cea3dedca5984780bafc599bd69add087d56": "BUSD",
    },
}


def rank(chain: str, asset: str) -> int:
    if asset in STABLES.get(chain, {}):
        return 0
    if asset == NATIVE:
        return 1
    return 2


def is_excluded(chain: str, asset: str) -> bool:
    return asset in STABLES.get(chain, {})


def group_transfers(chain: str, wallet: str, tokentx: list[dict], txlist: list[dict],
                    internal: list[dict]) -> list[dict]:
    """Список «транзакций» {sig, ts, block, deltas, symbols, signer} по хэшу."""
    w = wallet.lower()
    wrapped = WRAPPED.get(chain)
    groups: dict[str, dict] = {}

    def g(row) -> dict:
        h = row["hash"].lower()
        if h not in groups:
            groups[h] = {"sig": h, "ts": int(row.get("timeStamp") or 0), "block": int(row.get("blockNumber") or 0),
                         "deltas": defaultdict(float), "symbols": {}, "signer": False, "failed": False}
        return groups[h]

    for r in txlist:
        grp = g(r)
        if str(r.get("isError", "0")) == "1":
            grp["failed"] = True
            continue
        v = int(r.get("value") or 0) / 1e18
        if r.get("from", "").lower() == w:
            grp["signer"] = True
            grp["deltas"][NATIVE] -= v
        if r.get("to", "").lower() == w:
            grp["deltas"][NATIVE] += v
    for r in internal:
        if str(r.get("isError", "0")) == "1":
            continue
        grp = g(r)
        v = int(r.get("value") or 0) / 1e18
        if r.get("to", "").lower() == w:
            grp["deltas"][NATIVE] += v
        if r.get("from", "").lower() == w:
            grp["deltas"][NATIVE] -= v
    for r in tokentx:
        grp = g(r)
        c = r.get("contractAddress", "").lower()
        try:
            dec = int(r.get("tokenDecimal") or 0)
        except ValueError:
            dec = 0
        v = int(r.get("value") or 0) / (10 ** dec)
        asset = NATIVE if c == wrapped else c
        if asset != NATIVE:
            grp["symbols"][asset] = r.get("tokenSymbol")
        if r.get("to", "").lower() == w:
            grp["deltas"][asset] += v
        if r.get("from", "").lower() == w:
            grp["deltas"][asset] -= v
    out = []
    for grp in groups.values():
        grp["deltas"] = {a: d for a, d in grp["deltas"].items() if abs(d) > (1e-6 if a == NATIVE else 0)}
        out.append(grp)
    return sorted(out, key=lambda x: (x["ts"], x["sig"]))


def legs_from_group(chain: str, grp: dict) -> tuple[str, list[dict]]:
    """Тот же разбор изменений балансов, что и для Solana, с рангами сети."""
    if grp["failed"]:
        return "failed", []
    deltas = grp["deltas"]
    neg = [(a, -q) for a, q in deltas.items() if q < 0]
    pos = [(a, q) for a, q in deltas.items() if q > 0]
    legs: list[dict] = []
    if neg and pos:
        if len(neg) == 1 and len(pos) == 1:
            (a, qa), (b, qb) = neg[0], pos[0]
            ra, rb = rank(chain, a), rank(chain, b)
            if ra < rb:
                legs.append({"token": b, "side": "buy", "qty": qb, "quote": a, "quote_qty": qa})
            elif ra > rb:
                legs.append({"token": a, "side": "sell", "qty": qa, "quote": b, "quote_qty": qb})
            elif ra == 2:
                legs.append({"token": a, "side": "sell", "qty": qa, "quote": b, "quote_qty": qb})
                legs.append({"token": b, "side": "buy", "qty": qb, "quote": a, "quote_qty": qa})
        else:
            toks = [(a, q) for a, q in deltas.items() if rank(chain, a) == 2]
            for a, q in toks:
                legs.append({"token": a, "side": "buy" if q > 0 else "sell", "qty": abs(q),
                             "quote": None, "quote_qty": None})
            if not toks and NATIVE in deltas:
                q = deltas[NATIVE]
                st = sum(v for a, v in deltas.items() if rank(chain, a) == 0 and v * q < 0)
                if st:
                    legs.append({"token": NATIVE, "side": "buy" if q > 0 else "sell", "qty": abs(q),
                                 "quote": "USD", "quote_qty": abs(st)})
    else:
        for a, q in pos:
            legs.append({"token": a, "side": "tin", "qty": q, "quote": None, "quote_qty": None})
        for a, q in neg:
            legs.append({"token": a, "side": "tout", "qty": q, "quote": None, "quote_qty": None})
    for i, l in enumerate(legs):
        l.update(idx=i, sig=grp["sig"], ts=grp["ts"], slot=grp["block"])
    if not legs:
        return "other", legs
    return ("swap" if any(l["side"] in ("buy", "sell") for l in legs) else "transfer"), legs
