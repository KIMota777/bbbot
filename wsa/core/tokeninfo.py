"""Справочник токенов: рынок (DexScreener), возраст, сектор, тикер на CEX, держатели.

Всё кэшируется в таблице tokens с разными сроками годности: рынок — минуты,
листинги — сутки, сектор — месяц.
"""
from __future__ import annotations

import logging

from ..chains import evm as evm_chain
from ..chains import solana as sol_chain
from ..sources.coingecko import sector_of
from ..sources.dexscreener import snapshot_from_pair
from ..util import DAY, now

log = logging.getLogger("wsa.tokens")

MARKET_TTL = 15 * 60
CEX_TTL = DAY
SECTOR_TTL = 30 * DAY
HOLDERS_TTL = 30 * 60


class TokenInfo:
    def __init__(self, ctx):
        self.ctx = ctx
        self._mem: dict[tuple, dict] = {}

    # ---------- рыночные данные ----------

    def _native_meta(self, chain: str) -> dict:
        from .pricing import native_symbol
        sym = native_symbol(self.ctx, chain)
        return {"chain": chain, "address": sol_chain.SOL if chain == "solana" else evm_chain.NATIVE,
                "symbol": sym, "name": sym, "created_at": 1_500_000_000, "sector": "L1",
                "best_pair": None, "liquidity_usd": None, "mc": None, "fdv": None, "price_usd": None}

    def meta(self, chain: str, token: str, max_age: int = MARKET_TTL) -> dict:
        if token in (sol_chain.SOL, evm_chain.NATIVE):
            return self._native_meta(chain)
        key = (chain, token)
        m = self._mem.get(key)
        if m and now() - (m.get("updated_at") or 0) <= max_age:
            return m
        m = self.ctx.db.token(chain, token)
        if not m or now() - (m.get("updated_at") or 0) > max_age:
            self.refresh(chain, [token])
            m = self.ctx.db.token(chain, token) or {"chain": chain, "address": token}
        self._mem[key] = m
        return m

    def refresh(self, chain: str, tokens: list[str]) -> dict[str, dict]:
        """Пакетное обновление рынка по DexScreener (30 токенов за запрос)."""
        tokens = [t for t in dict.fromkeys(tokens) if t not in (sol_chain.SOL, evm_chain.NATIVE)]
        if not tokens:
            return {}
        try:
            pairs = self.ctx.ds.tokens(chain, tokens, ttl=MARKET_TTL)
        except Exception as e:
            log.warning("DexScreener недоступен: %s", e)
            return {}
        out = {}
        for t in tokens:
            p = pairs.get(t)
            if not p:
                # у мёртвых/спам-токенов пар нет — запоминаем, чтобы не спрашивать снова
                self.ctx.db.upsert_token(chain, t, updated_at=now())
                continue
            s = snapshot_from_pair(p)
            prev = self.ctx.db.token(chain, t) or {}
            created = prev.get("created_at")
            if not created or (s["pair_created"] and s["pair_created"] < created):
                created = s["pair_created"]
            fields = dict(symbol=s["symbol"], name=s["name"], best_pair=s["pair"], dex=s["dex"],
                          liquidity_usd=s["liquidity"], mc=s["mc"], fdv=s["fdv"], price_usd=s["price"],
                          vol24=s["vol24"], created_at=created, updated_at=now())
            self.ctx.db.upsert_token(chain, t, **fields)
            self._mem.pop((chain, t), None)
            out[t] = s
            self.ctx.db.execute(
                "INSERT OR IGNORE INTO token_snapshots(chain, address, ts, price, mc, fdv, liquidity, vol24,"
                " chg_1h, chg_24h, buys_24h, sells_24h, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (chain, t, now() // 300 * 300, s["price"], s["mc"], s["fdv"], s["liquidity"], s["vol24"],
                 s["chg_1h"], s["chg_24h"], s["buys_24h"], s["sells_24h"], "dexscreener"))
        return out

    def created_at(self, chain: str, token: str) -> int | None:
        """Возраст токена: самый ранний пул по всем DEX (дороже, считается один раз)."""
        m = self.meta(chain, token, max_age=10 * 365 * DAY)
        if token in (sol_chain.SOL, evm_chain.NATIVE):
            return m["created_at"]
        if m.get("_age_checked"):
            return m.get("created_at")
        try:
            pairs = self.ctx.ds.token_pairs(chain, token)
            ts = [int(p["pairCreatedAt"] / 1000) for p in pairs if p.get("pairCreatedAt")]
            if ts:
                self.ctx.db.upsert_token(chain, token, created_at=min(ts + ([m["created_at"]] if m.get("created_at") else [])))
        except Exception as e:
            log.debug("token_pairs %s: %s", token[:8], e)
        m = self.ctx.db.token(chain, token) or m
        m["_age_checked"] = True
        self._mem[(chain, token)] = m
        return m.get("created_at")

    # ---------- CEX ----------

    def cex_map(self, chain: str, token: str, which: str = "trade") -> dict | None:
        """Тикер токена на CEX (торговой или референсной) с проверкой цены."""
        cex = self.ctx.cex if which == "trade" else self.ctx.ref
        tag = f"{cex.id}:{cex.market}"
        if token in (sol_chain.SOL, evm_chain.NATIVE):
            from .pricing import native_symbol
            sym = native_symbol(self.ctx, chain)
            symbol = f"{sym}/USDT" if cex.market == "spot" else f"{sym}/USDT:USDT"
            return {"exchange": cex.id, "market": cex.market, "symbol": symbol, "multiplier": 1.0,
                    "listed_since": 1_500_000_000}
        m = self.meta(chain, token, max_age=10 * 365 * DAY)
        cexd = m.get("cex") or {}
        hit = cexd.get(tag)
        if hit is not None and now() - (hit.get("checked") or 0) < CEX_TTL:
            return hit if hit.get("symbol") else None
        m = self.meta(chain, token)  # свежая цена нужна для сверки
        res = None
        if m.get("symbol") and m.get("price_usd"):
            try:
                r = cex.resolve(m["symbol"], m["price_usd"], tol=self.ctx.cfg.cex.price_match_tolerance)
            except Exception as e:
                log.debug("resolve %s: %s", m.get("symbol"), e)
                r = None
            if r:
                res = {"exchange": cex.id, "market": cex.market, "symbol": r["symbol"],
                       "multiplier": r["multiplier"], "listed_since": cex.listed_since(r["symbol"])}
        cexd[tag] = {**(res or {}), "checked": now()}
        self.ctx.db.upsert_token(chain, token, cex=cexd, cex_updated_at=now())
        self._mem.pop((chain, token), None)
        return res

    def listed_on_cex_at(self, chain: str, token: str, ts: int, which: str = "trade") -> bool:
        m = self.cex_map(chain, token, which)
        return bool(m and m.get("listed_since") and m["listed_since"] <= ts)

    # ---------- сектор и держатели ----------

    def sector(self, chain: str, token: str) -> str:
        if token in (sol_chain.SOL, evm_chain.NATIVE):
            return "L1"
        m = self.meta(chain, token, max_age=10 * 365 * DAY)
        if m.get("sector") and now() - (m.get("cg_updated_at") or 0) < SECTOR_TTL:
            return m["sector"]
        cats: list[str] = []
        cg_id = None
        try:
            cg = self.ctx.cg.by_contract(chain, token)
            if cg:
                cats = cg["categories"]
                cg_id = cg["id"]
        except Exception as e:
            log.debug("coingecko %s: %s", token[:8], e)
        if not cats:
            try:
                info = self.ctx.gt.token_info(chain, token)
                cats = info.get("categories") or []
            except Exception:
                pass
        sec = sector_of(cats)
        if sec in ("Unknown", "Other") and m.get("symbol", "").endswith(("INU", "DOGE", "CAT", "PEPE")):
            sec = "Meme"
        if sec == "Unknown" and m.get("created_at") and now() - m["created_at"] < 90 * DAY:
            # без категорий, молодой, только на DEX — почти всегда мем
            sec = "Meme?"
        self.ctx.db.upsert_token(chain, token, sector=sec, categories=cats, coingecko_id=cg_id, cg_updated_at=now())
        self._mem.pop((chain, token), None)
        return sec

    def holders(self, chain: str, token: str, max_age: int = HOLDERS_TTL) -> dict:
        """Держатели и концентрация (доля топ-10) — снимок GeckoTerminal."""
        if token in (sol_chain.SOL, evm_chain.NATIVE):
            return {}
        m = self.meta(chain, token, max_age=10 * 365 * DAY)
        if m.get("holders") and now() - (m.get("holders_updated_at") or 0) < max_age:
            return {"holders": m["holders"], "top10_pct": m.get("top10_pct")}
        try:
            info = self.ctx.gt.token_info(chain, token)
        except Exception as e:
            log.debug("GT info %s: %s", token[:8], e)
            return {}
        h = info.get("holders") or {}
        cnt = h.get("count")
        top10 = (h.get("distribution_percentage") or {}).get("top_10")
        top10 = float(top10) / 100 if top10 is not None else None
        self.ctx.db.upsert_token(chain, token, holders=cnt, top10_pct=top10, holders_updated_at=now())
        self.ctx.db.execute(
            "INSERT OR IGNORE INTO token_snapshots(chain, address, ts, holders, top10_pct, source) VALUES(?,?,?,?,?,?)",
            (chain, token, now() // 300 * 300 + 1, cnt, top10, "geckoterminal"))
        self._mem.pop((chain, token), None)
        return {"holders": cnt, "top10_pct": top10}

    def holder_growth(self, chain: str, token: str, ts: int, window: int = DAY) -> float | None:
        """Рост держателей за окно по нашим же снимкам (копятся трекером)."""
        rows = self.ctx.db.query(
            "SELECT ts, holders FROM token_snapshots WHERE chain=? AND address=? AND holders IS NOT NULL"
            " AND ts BETWEEN ? AND ? ORDER BY ts", (chain, token, ts - window - 3 * 3600, ts))
        if len(rows) < 2:
            return None
        first, last = rows[0], rows[-1]
        if last["ts"] - first["ts"] < window / 2 or not first["holders"]:
            return None
        return last["holders"] / first["holders"] - 1
