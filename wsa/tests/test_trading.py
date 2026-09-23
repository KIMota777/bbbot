"""Риск-менеджер и полный цикл бумажной сделки на фейковой бирже."""
import unittest

from helpers import FakeCtx

from wsa.core.strategy import Strategy
from wsa.trading.broker import Fill
from wsa.trading.engine import Engine
from wsa.trading.risk import Risk
from wsa.util import now


class FakeBroker:
    """Бумажный брокер с ценой, которую двигает тест."""

    def __init__(self, cash=1000.0, fee=0.001):
        self.cash = cash
        self.px = 1.0
        self.fee = fee
        self.cex = self
        self.orders = []

    def markets(self):
        return {"TOK/USDT": {"limits": {"cost": {"min": 5}}}}

    def quote(self, symbol):
        return self.px * 0.9995, self.px * 1.0005

    def buy_usd(self, symbol, usd):
        px = self.px * 1.0005
        self.cash -= usd * (1 + self.fee)
        self.orders.append(("buy", usd))
        return Fill(usd / px, px, usd * self.fee)

    def sell_qty(self, symbol, qty):
        px = self.px * 0.9995
        self.cash += qty * px * (1 - self.fee)
        self.orders.append(("sell", qty))
        return Fill(qty, px, qty * px * self.fee)

    def place_stop(self, *a):
        return None

    def cancel(self, *a):
        return None

    def equity(self, opens):
        return self.cash + sum(p["qty_open"] * self.px for p in opens)


STRAT = Strategy({"exits": {"sl": -0.05, "tp": [[0.05, 0.5], [0.12, 0.5]], "time_stop_sec": 86400,
                            "trail_after_tp1": True, "exit_on_leader_sell": True, "leader_sell_fraction": 0.5}})


def make_engine():
    ctx = FakeCtx()
    eng = Engine.__new__(Engine)
    eng.ctx, eng.strategy, eng.mode = ctx, STRAT, "paper"
    eng.broker = FakeBroker()
    eng.risk = Risk(ctx.db, ctx.cfg, "paper")
    eng.c = ctx.cfg.trading
    eng._last_equity_ts = 0
    return ctx, eng


def sig(**kw):
    return {"kind": "wallet", "symbol": "TOK/USDT", "token": "T", "chain": "solana", "wallet": "leader",
            "ts": now(), "leader_price": 1.0, **kw}


class RiskRules(unittest.TestCase):
    def test_size_by_risk_and_cap(self):
        ctx = FakeCtx()
        r = Risk(ctx.db, ctx.cfg, "paper")
        # 1% риска при стопе 5% = 20% капитала, но потолок позиции 10%
        self.assertAlmostEqual(r.size_usd(1000, -0.05, []), 100)
        # стоп 20% -> 1%/20% = 5% капитала
        self.assertAlmostEqual(r.size_usd(1000, -0.20, []), 50)

    def test_daily_loss_blocks_entries(self):
        ctx = FakeCtx()
        r = Risk(ctx.db, ctx.cfg, "paper")
        self.assertTrue(r.can_open("A", 1000, [])[0])
        r.on_close(-40, 960)  # −4% при лимите 3%
        ok, why = r.can_open("A", 960, [])
        self.assertFalse(ok)
        self.assertIn("дневной", why)

    def test_drawdown_halt_needs_manual_reset(self):
        ctx = FakeCtx()
        r = Risk(ctx.db, ctx.cfg, "paper")
        r.can_open("A", 1000, [])
        ok, why = r.can_open("A", 800, [])  # −20% от пика
        self.assertFalse(ok)
        self.assertTrue(r.state()["halted"])
        r.reset()
        self.assertTrue(r.can_open("A", 800, [])[0])

    def test_consecutive_losses_pause(self):
        ctx = FakeCtx()
        r = Risk(ctx.db, ctx.cfg, "paper")
        for _ in range(ctx.cfg.trading.max_consecutive_losses):
            r.on_close(-1, 1000)
        self.assertGreater(r.state()["paused_until"], now())


class EngineFlow(unittest.TestCase):
    def test_full_cycle_take_then_trail(self):
        ctx, eng = make_engine()
        st, _ = eng.on_signal(sig())
        self.assertEqual(st, "executed")
        p = eng.open_positions()[0]
        self.assertAlmostEqual(p["cost_usd"], 100, delta=1)
        eng.broker.px = 1.06  # первый тейк +5%
        eng.manage()
        p = eng.open_positions()[0]
        self.assertTrue(p["tps"][0]["done"])
        self.assertLess(p["qty_open"], p["qty"])
        eng.manage()  # стоп подтянулся в плюс
        p = eng.open_positions()[0]
        self.assertGreater(p["sl"], p["entry_price"])
        eng.broker.px = p["sl"] * 0.999  # откат — трейлинг
        eng.manage()
        self.assertEqual(eng.open_positions(), [])
        r = ctx.db.one("SELECT * FROM positions")
        self.assertEqual(r["exit_reason"], "трейлинг")
        self.assertGreater(r["realized_usd"] - r["fees_usd"], 0)
        # деньги сходятся: итог позиции = изменение кэша фейкового брокера
        self.assertAlmostEqual(r["realized_usd"] - r["fees_usd"], eng.broker.cash - 1000, delta=0.05)

    def test_stop_loss(self):
        ctx, eng = make_engine()
        eng.on_signal(sig())
        eng.broker.px = 0.94
        eng.manage()
        r = ctx.db.one("SELECT * FROM positions")
        self.assertEqual((r["status"], r["exit_reason"]), ("closed", "стоп"))
        self.assertLess(r["realized_usd"] - r["fees_usd"], 0)

    def test_leader_sell_exit(self):
        ctx, eng = make_engine()
        eng.on_signal(sig())
        eng.on_leader_sell("leader", "T", 0.3)  # продал мало — остаёмся
        self.assertEqual(len(eng.open_positions()), 1)
        eng.on_leader_sell("leader", "T", 0.8)
        self.assertEqual(eng.open_positions(), [])

    def test_chasing_and_stale_signals_rejected(self):
        ctx, eng = make_engine()
        eng.broker.px = 1.10  # CEX уже на 10% выше цены лидера
        self.assertEqual(eng.on_signal(sig())[0], "rejected")
        self.assertEqual(eng.on_signal(sig(ts=now() - 3600))[0], "expired")
        self.assertEqual(ctx.db.one("SELECT COUNT(*) n FROM positions")["n"], 0)
        self.assertEqual(ctx.db.one("SELECT COUNT(*) n FROM signals")["n"], 2)

    def test_one_position_per_symbol(self):
        ctx, eng = make_engine()
        self.assertEqual(eng.on_signal(sig())[0], "executed")
        st, why = eng.on_signal(sig())
        self.assertEqual(st, "rejected")


if __name__ == "__main__":
    unittest.main()
