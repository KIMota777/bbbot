"""Исполнение ордеров: бумажный счёт и реальная биржа через ccxt.

Режимы:
- paper   — сделки симулируются по живому стакану биржи (ask/bid + проскальзывание,
            комиссия тейкера), деньги не нужны, ключи не нужны;
- demo    — демо-счёт Bybit (ключи демо-аккаунта), реальные ордера на демо-деньги;
- testnet — тестовая сеть биржи (отдельные ключи testnet);
- live    — реальные деньги. Включается только явным флагом --live и
            подтверждением в консоли.

Ключи — только из окружения: BYBIT_API_KEY / BYBIT_API_SECRET (как в bbbot),
для других бирж <EXCHANGE>_API_KEY / _API_SECRET / _API_PASSWORD.
Права ключа: только торговля, БЕЗ вывода средств.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ..config import exchange_keys
from ..sources.cex import Cex
from ..util import now

log = logging.getLogger("wsa.broker")


@dataclass
class Fill:
    qty: float
    price: float
    fee_usd: float
    order_id: str | None = None
    raw: dict | None = None


class PaperBroker:
    mode = "paper"

    def __init__(self, ctx, cex: Cex):
        self.ctx = ctx
        self.cex = cex
        self.fee = ctx.cfg.backtest.taker_fee
        self.slip = ctx.cfg.backtest.slippage
        self.key = "paper:cash"
        if ctx.db.kv_get(self.key) is None:
            ctx.db.kv_set(self.key, float(ctx.cfg.trading.paper_equity))

    def cash(self) -> float:
        return float(self.ctx.db.kv_get(self.key, 0.0))

    def _set_cash(self, v: float) -> None:
        self.ctx.db.kv_set(self.key, v)

    def quote(self, symbol: str) -> tuple[float | None, float | None]:
        try:
            return self.cex.order_book_top(symbol)
        except Exception as e:
            log.warning("стакан %s недоступен: %s", symbol, e)
            p = self.cex.last_price(symbol)
            return p, p

    def buy_usd(self, symbol: str, usd: float) -> Fill | None:
        bid, ask = self.quote(symbol)
        if not ask:
            return None
        px = ask * (1 + self.slip)
        qty = usd / px
        fee = usd * self.fee
        if usd + fee > self.cash():
            return None
        self._set_cash(self.cash() - usd - fee)
        return Fill(qty, px, fee, f"paper-{now()}")

    def sell_qty(self, symbol: str, qty: float) -> Fill | None:
        bid, ask = self.quote(symbol)
        if not bid:
            return None
        px = bid * (1 - self.slip)
        usd = qty * px
        fee = usd * self.fee
        self._set_cash(self.cash() + usd - fee)
        return Fill(qty, px, fee, f"paper-{now()}")

    def place_stop(self, symbol: str, qty: float, stop: float) -> str | None:
        return None  # бумажный стоп ведёт движок

    def cancel(self, symbol: str, order_id: str | None) -> None:
        return None

    def equity(self, open_positions: list[dict]) -> float:
        eq = self.cash()
        for p in open_positions:
            px = self.cex.last_price(p["symbol"]) or p["entry_price"]
            eq += (p["qty_open"] or 0) * px
        return eq


class CcxtBroker:
    """Реальная биржа. Каждое действие логируется в таблицу orders."""

    def __init__(self, ctx, mode: str):
        c = ctx.cfg.cex
        keys = exchange_keys(c.exchange)
        if not keys.get("apiKey") or not keys.get("secret"):
            raise RuntimeError(f"нет ключей: задайте {c.exchange.upper()}_API_KEY и {c.exchange.upper()}_API_SECRET "
                               "в окружении или в .env (права — только торговля, без вывода)")
        self.ctx = ctx
        self.mode = mode
        self.cex = Cex(c.exchange, c.market, c.quote, ctx.db, keys=keys, mode=mode)
        self.ex = self.cex.ex
        self.market = c.market
        self.quote_ccy = c.quote
        self.leverage = ctx.cfg.trading.leverage
        self._lev_set: set[str] = set()

    def _ensure_leverage(self, symbol: str) -> None:
        """Перпетуалы: явное плечо (по умолчанию 1×), чтобы размер от риска = реальная маржа."""
        if self.market == "spot" or symbol in self._lev_set:
            return
        try:
            self.ex.set_leverage(self.leverage, symbol)
        except Exception as e:  # Bybit отвечает ошибкой, если плечо уже такое — это нормально
            log.debug("set_leverage %s: %s", symbol, e)
        self._lev_set.add(symbol)

    def check(self) -> dict:
        """Проверка ключа без торговли: баланс и (для Bybit) права ключа."""
        out = {"exchange": self.cex.id, "mode": self.mode}
        bal = self.ex.fetch_balance()
        out["free_quote"] = (bal.get("free") or {}).get(self.quote_ccy)
        out["total_quote"] = (bal.get("total") or {}).get(self.quote_ccy)
        if self.cex.id == "bybit":
            try:
                info = self.ex.privateGetV5UserQueryApi()
                perms = (info.get("result") or {}).get("permissions") or {}
                out["permissions"] = perms
                wallet = perms.get("Wallet") or []
                out["withdraw_enabled"] = any("Withdraw" in x for x in wallet)
            except Exception as e:
                out["permissions_error"] = str(e)
        return out

    def quote(self, symbol: str):
        return self.cex.order_book_top(symbol)

    def _log(self, symbol, side, typ, qty, price, res, purpose, position_id=None):
        self.ctx.db.execute(
            "INSERT INTO orders(position_id, ts, mode, exchange, symbol, side, type, qty, price, filled, avg_price,"
            " fee_usd, status, exch_id, purpose, raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (position_id, now(), self.mode, self.cex.id, symbol, side, typ, qty, price,
             (res or {}).get("filled"), (res or {}).get("average"),
             ((res or {}).get("fee") or {}).get("cost"), (res or {}).get("status"), (res or {}).get("id"),
             purpose, json.dumps(res, default=str)[:4000] if res else None))

    def _fill_from(self, res: dict, fallback_price: float) -> Fill:
        oid = res.get("id")
        try:
            if oid and not res.get("filled"):
                res = self.ex.fetch_order(oid, res.get("symbol")) or res
        except Exception:
            pass
        filled = float(res.get("filled") or res.get("amount") or 0)
        avg = float(res.get("average") or res.get("price") or fallback_price)
        fee = res.get("fee") or {}
        fee_usd = float(fee.get("cost") or 0) * (avg if fee.get("currency") not in (self.quote_ccy, None) else 1)
        return Fill(filled, avg, fee_usd, oid, res)

    def buy_usd(self, symbol: str, usd: float) -> Fill | None:
        bid, ask = self.quote(symbol)
        if not ask:
            return None
        self.cex.markets()
        self._ensure_leverage(symbol)
        if self.market == "spot" and self.ex.has.get("createMarketBuyOrderWithCost"):
            res = self.ex.create_market_buy_order_with_cost(symbol, usd)
        else:
            qty = float(self.ex.amount_to_precision(symbol, usd / ask))
            res = self.ex.create_order(symbol, "market", "buy", qty)
        self._log(symbol, "buy", "market", None, ask, res, "entry")
        return self._fill_from(res, ask)

    def sell_qty(self, symbol: str, qty: float) -> Fill | None:
        bid, _ = self.quote(symbol)
        if self.market == "spot":
            # на споте комиссия покупки списывается в базовой монете: на счёте
            # чуть меньше купленного, продаём не больше свободного остатка
            base = self.cex.markets()[symbol]["base"]
            free = float((self.ex.fetch_balance().get("free") or {}).get(base) or 0)
            qty = min(qty, free)
        q = float(self.ex.amount_to_precision(symbol, qty))
        params = {"reduceOnly": True} if self.market != "spot" else {}
        res = self.ex.create_order(symbol, "market", "sell", q, None, params)
        self._log(symbol, "sell", "market", q, bid, res, "exit")
        return self._fill_from(res, bid or 0)

    def place_stop(self, symbol: str, qty: float, stop: float) -> str | None:
        """Страховочный стоп на бирже: если бот упадёт, позиция не останется без стопа."""
        try:
            q = float(self.ex.amount_to_precision(symbol, qty))
            px = float(self.ex.price_to_precision(symbol, stop))
            params = {"stopLossPrice": px}
            if self.market != "spot":
                params["reduceOnly"] = True
            res = self.ex.create_order(symbol, "market", "sell", q, None, params)
            self._log(symbol, "sell", "stop", q, px, res, "stop")
            return res.get("id")
        except Exception as e:
            log.warning("биржевой стоп %s не выставлен (%s) — стоп ведёт бот", symbol, e)
            return None

    def cancel(self, symbol: str, order_id: str | None) -> None:
        if not order_id:
            return
        try:
            # условный (стоп) ордер: ccxt понимает trigger (новое имя) и stop (старое)
            self.ex.cancel_order(order_id, symbol, params={"trigger": True, "stop": True})
        except Exception as e:
            log.info("отмена ордера %s: %s", order_id, e)

    def equity(self, open_positions: list[dict]) -> float:
        bal = self.ex.fetch_balance()
        total = float((bal.get("total") or {}).get(self.quote_ccy) or 0)
        if self.market == "spot":
            for p in open_positions:
                px = self.cex.last_price(p["symbol"]) or p["entry_price"]
                total += (p["qty_open"] or 0) * px
        return total


def make_broker(ctx, mode: str):
    if mode == "paper":
        c = ctx.cfg.cex
        return PaperBroker(ctx, ctx.cex if ctx.cex.market == c.market else Cex(c.exchange, c.market, c.quote, ctx.db))
    if mode not in ("demo", "testnet", "live"):
        raise ValueError(f"неизвестный режим {mode}")
    return CcxtBroker(ctx, mode)
