"""Торговый движок: вход по сигналу, ведение позиции, выходы.

Выходы (параметры из strategy.json -> exits):
- стоп sl от цены входа; на бирже дублируется страховочным стоп-ордером (demo/testnet/live);
- лесенка тейков tp: [[+x%, доля], ...]; после первого тейка стоп в безубыток,
  дальше трейлинг на половине пути от входа до максимума;
- тайм-стоп: позиция закрывается по истечении горизонта стратегии;
- выход вслед за лидером: если кошелёк, по сигналу которого вошли, продал
  ≥ leader_sell_fraction своей позиции — закрываемся.
"""
from __future__ import annotations

import json
import logging

from ..util import fmt_pct, fmt_usd, now
from .broker import make_broker
from .risk import Risk
from .signals import record

log = logging.getLogger("wsa.engine")


class Engine:
    def __init__(self, ctx, strategy, mode: str = "paper"):
        self.ctx = ctx
        self.strategy = strategy
        self.mode = mode
        self.broker = make_broker(ctx, mode)
        self.risk = Risk(ctx.db, ctx.cfg, mode)
        self.c = ctx.cfg.trading
        self._last_equity_ts = 0

    # ---------- позиции ----------

    def open_positions(self) -> list[dict]:
        rows = self.ctx.db.query("SELECT * FROM positions WHERE mode=? AND status='open'", (self.mode,))
        out = []
        for r in rows:
            d = dict(r)
            d["tps"] = json.loads(d["tps"] or "[]")
            out.append(d)
        return out

    def equity(self) -> float:
        return self.broker.equity(self.open_positions())

    # ---------- вход ----------

    def on_signal(self, sig: dict) -> tuple[str, str]:
        """Возвращает (статус, причина). Сигнал пишется в таблицу signals в любом случае."""
        sym = sig.get("symbol")
        if not sym:
            return self._reject(sig, "нет тикера CEX")
        if now() - (sig.get("ts") or now()) > self.c.signal_max_age_sec:
            return self._reject(sig, "сигнал устарел", status="expired")
        opens = self.open_positions()
        equity = self.broker.equity(opens)
        ok, why = self.risk.can_open(sym, equity, opens)
        if not ok:
            return self._reject(sig, why)
        bid, ask = self.broker.quote(sym)
        if not bid or not ask:
            return self._reject(sig, "нет котировок")
        spread = (ask - bid) / ((ask + bid) / 2)
        if spread > self.c.max_spread:
            return self._reject(sig, f"спред {spread:.2%} > {self.c.max_spread:.2%}")
        lp = sig.get("leader_price")
        mult = sig.get("multiplier") or 1.0
        if lp and ask / mult > lp * (1 + self.c.max_chase):
            return self._reject(sig, f"цена ушла от цены лидера на {ask / mult / lp - 1:+.1%} — не догоняем")
        ex = self.strategy.exits
        usd = self.risk.size_usd(equity, ex["sl"], opens)
        min_cost = self._min_cost(sym)
        if usd < max(min_cost, 5.0):
            return self._reject(sig, f"размер {fmt_usd(usd)} меньше минимального ордера")
        fill = self.broker.buy_usd(sym, usd)
        if not fill or not fill.qty:
            return self._reject(sig, "ордер не исполнился")
        entry = fill.price
        sl = entry * (1 + ex["sl"])
        tps = [{"px": entry * (1 + t), "frac": f, "done": False} for t, f in ex["tp"]]
        pid = self.ctx.db.execute(
            "INSERT INTO positions(mode, exchange, market, symbol, chain, token, leader, signal_id, opened_at,"
            " qty, qty_open, entry_price, cost_usd, sl, tps, time_stop, peak_price, realized_usd, fees_usd, status)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.mode, self.ctx.cfg.cex.exchange, self.ctx.cfg.cex.market, sym, sig.get("chain"), sig.get("token"),
             sig.get("wallet"), None, now(), fill.qty, fill.qty, entry, fill.qty * entry, sl, json.dumps(tps),
             now() + int(ex["time_stop_sec"]), entry, 0.0, fill.fee_usd, "open")).lastrowid
        sid = record(self.ctx, sig, "executed", f"позиция #{pid}: {fmt_usd(fill.qty * entry)} по {entry:.6g}")
        self.ctx.db.execute("UPDATE positions SET signal_id=? WHERE id=?", (sid, pid))
        stop_id = self.broker.place_stop(sym, fill.qty, sl)
        if stop_id:
            self.ctx.db.kv_set(f"stop:{pid}", stop_id)
        log.info("[%s] ВХОД %s %s на %s по %.6g, стоп %.6g (%s)", self.mode, sym, sig.get("rule") or "",
                 fmt_usd(fill.qty * entry), entry, sl, sig.get("kind"))
        return "executed", f"#{pid}"

    def _min_cost(self, sym: str) -> float:
        try:
            m = self.broker.cex.markets()[sym]
            return float(((m.get("limits") or {}).get("cost") or {}).get("min") or 0)
        except Exception:
            return 0.0

    def _reject(self, sig: dict, reason: str, status: str = "rejected") -> tuple[str, str]:
        record(self.ctx, sig, status, reason)
        log.info("[%s] сигнал %s %s отклонён: %s", self.mode, sig.get("symbol"), sig.get("kind"), reason)
        return status, reason

    # ---------- выходы ----------

    def on_leader_sell(self, wallet: str, token: str, frac_sold: float) -> None:
        if not self.strategy.exits.get("exit_on_leader_sell"):
            return
        if frac_sold < self.strategy.exits.get("leader_sell_fraction", 0.5):
            return
        for p in self.open_positions():
            if p["leader"] == wallet and p["token"] == token:
                self.close(p, p["qty_open"], f"лидер продал {frac_sold:.0%}")

    def close(self, p: dict, qty: float, reason: str) -> None:
        qty = min(qty, p["qty_open"] or 0)
        if qty <= 0:
            return
        stop_id = self.ctx.db.kv_get(f"stop:{p['id']}")
        if stop_id:
            self.broker.cancel(p["symbol"], stop_id)
            self.ctx.db.kv_set(f"stop:{p['id']}", None)
        fill = self.broker.sell_qty(p["symbol"], qty)
        if not fill:
            log.warning("не удалось продать %s", p["symbol"])
            return
        # realized_usd — валовый результат по цене, fees_usd — все комиссии (вход + выходы);
        # чистый итог позиции = realized_usd - fees_usd
        pnl = (fill.price - p["entry_price"]) * fill.qty
        left = (p["qty_open"] or 0) - fill.qty
        realized = (p["realized_usd"] or 0) + pnl
        fees = (p["fees_usd"] or 0) + fill.fee_usd
        closed = left <= (p["qty"] or 0) * 1e-6
        self.ctx.db.execute(
            "UPDATE positions SET qty_open=?, realized_usd=?, fees_usd=?, status=?, closed_at=?, exit_reason=? WHERE id=?",
            (0.0 if closed else left, realized, fees, "closed" if closed else "open",
             now() if closed else None, reason if closed else p.get("exit_reason"), p["id"]))
        log.info("[%s] ВЫХОД %s %s: %.6g × %.6g, PnL %s (%s)", self.mode, p["symbol"],
                 "полностью" if closed else "частично", fill.qty, fill.price,
                 fmt_usd(pnl - fill.fee_usd, signed=True), reason)
        if closed:
            self.risk.on_close(realized - fees, self.equity())
        elif stop_id:
            new_stop = self.broker.place_stop(p["symbol"], left, p["sl"])
            if new_stop:
                self.ctx.db.kv_set(f"stop:{p['id']}", new_stop)

    def manage(self) -> None:
        """Проверка всех открытых позиций по текущей цене."""
        for p in self.open_positions():
            try:
                bid, ask = self.broker.quote(p["symbol"])
            except Exception as e:
                log.warning("котировка %s: %s", p["symbol"], e)
                continue
            if not bid:
                continue
            px = bid
            peak = max(p["peak_price"] or p["entry_price"], px)
            sl = p["sl"]
            tps = p["tps"]
            if px <= sl:
                self.close(p, p["qty_open"], "стоп" if not any(t["done"] for t in tps) else "трейлинг")
                continue
            sold = False
            for t in tps:
                if not t["done"] and px >= t["px"]:
                    qty = min(p["qty_open"], p["qty"] * t["frac"])
                    t["done"] = True
                    self.ctx.db.execute("UPDATE positions SET tps=? WHERE id=?", (json.dumps(tps), p["id"]))
                    self.close(p, qty, f"тейк {fmt_pct(t['px'] / p['entry_price'] - 1, signed=True)}")
                    sold = True
                    break
            if sold:
                continue
            if now() >= (p["time_stop"] or 0):
                self.close(p, p["qty_open"], "тайм-стоп")
                continue
            if any(t["done"] for t in tps) and self.strategy.exits.get("trail_after_tp1"):
                fee = self.ctx.cfg.backtest.taker_fee
                sl = max(sl, p["entry_price"] * (1 + 2 * fee), p["entry_price"] + 0.5 * (peak - p["entry_price"]))
                sl = min(sl, px * 0.999)
            self.ctx.db.execute("UPDATE positions SET peak_price=?, sl=? WHERE id=?", (peak, sl, p["id"]))
        if now() - self._last_equity_ts >= 300:
            self._last_equity_ts = now()
            try:
                self.ctx.db.execute("INSERT OR REPLACE INTO equity(ts, mode, equity) VALUES(?,?,?)",
                                    (now() // 60 * 60, self.mode, self.equity()))
            except Exception as e:
                log.debug("equity: %s", e)

    def status(self) -> dict:
        opens = self.open_positions()
        closed = self.ctx.db.query("SELECT * FROM positions WHERE mode=? AND status='closed' ORDER BY closed_at DESC",
                                   (self.mode,))
        pnl = sum((r["realized_usd"] or 0) - (r["fees_usd"] or 0) for r in closed)
        wins = sum(1 for r in closed if (r["realized_usd"] or 0) - (r["fees_usd"] or 0) > 0)
        return {"mode": self.mode, "equity": self.equity(), "open": opens, "closed": len(closed),
                "realized": pnl, "win_rate": wins / len(closed) if closed else None, "risk": self.risk.state()}
