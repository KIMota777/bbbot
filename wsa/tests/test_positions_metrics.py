"""FIFO-циклы, PnL, метрики, фильтры и балл."""
import unittest

from helpers import TOKEN, TOKEN2, leg

from wsa.config import load_config
from wsa.core.metrics import compute_metrics, max_drawdown
from wsa.core.positions import build_round_trips
from wsa.core.scoring import flags_for, score_wallet
from wsa.util import DAY, HOUR, wilson_lb

CFG = load_config("/nonexistent.toml")


class RoundTrips(unittest.TestCase):
    def test_single_buy_sell(self):
        trips, orphan = build_round_trips("solana", "w", [leg(0, "buy", TOKEN, 100, 1000), leg(HOUR, "sell", TOKEN, 100, 1500)])
        t = trips[0]
        self.assertEqual((t.status, t.exit_kind, t.build), ("closed", "full", "single"))
        self.assertAlmostEqual(t.realized, 500)
        self.assertAlmostEqual(t.pnl_pct, 0.5)
        self.assertAlmostEqual(t.hold_sec, HOUR)

    def test_dca_down_fifo_and_partial_exit(self):
        legs = [leg(0, "buy", TOKEN, 100, 1000), leg(10, "buy", TOKEN, 100, 800),  # 10 -> 8: добор вниз
                leg(20, "sell", TOKEN, 100, 1200), leg(30, "sell", TOKEN, 100, 1200)]
        t = build_round_trips("solana", "w", legs)[0][0]
        self.assertEqual(t.build, "dca_down")
        self.assertEqual(t.exit_kind, "partial")
        # FIFO: первая продажа закрывает лот по 10 (+200), вторая — по 8 (+400)
        self.assertAlmostEqual(t.sells[0].pnl, 200)
        self.assertAlmostEqual(t.sells[1].pnl, 400)
        self.assertAlmostEqual(t.realized, 600)

    def test_sell_without_buy_is_not_profit(self):
        trips, orphan = build_round_trips("solana", "w", [leg(0, "sell", TOKEN, 100, 5000)])
        self.assertEqual(trips, [])
        self.assertAlmostEqual(orphan["sell_usd"], 5000)

    def test_unknown_basis_part_is_unmatched(self):
        legs = [leg(0, "buy", TOKEN, 10, 100), leg(5, "tin", TOKEN, 90, None), leg(10, "sell", TOKEN, 100, 2000)]
        t = build_round_trips("solana", "w", legs)[0][0]
        self.assertAlmostEqual(t.matched_cost, 100)
        self.assertAlmostEqual(t.realized, 200 - 100)  # в PnL только сопоставленные 10 токенов
        self.assertAlmostEqual(t.unmatched_qty, 90)
        self.assertFalse(t.valid)  # 90% продажи без базы — цикл не для статистики

    def test_transfer_out_closes_without_pnl(self):
        legs = [leg(0, "buy", TOKEN, 100, 1000), leg(10, "tout", TOKEN, 100, None)]
        t = build_round_trips("solana", "w", legs)[0][0]
        self.assertEqual((t.status, t.exit_kind, t.realized), ("closed", "transfer", 0.0))
        self.assertFalse(t.valid)

    def test_dust_closes_trip(self):
        legs = [leg(0, "buy", TOKEN, 100, 1000), leg(10, "sell", TOKEN, 99, 1100)]
        t = build_round_trips("solana", "w", legs)[0][0]
        self.assertEqual(t.status, "closed")


class Metrics(unittest.TestCase):
    def make(self, pnls, hold=DAY, tokens=None):
        legs, ts = [], 1_700_000_000
        for i, p in enumerate(pnls):
            tok = (tokens or [TOKEN])[i % len(tokens or [TOKEN])]
            legs.append(leg(ts, "buy", tok, 100, 1000, sig=f"b{i}"))
            legs.append(leg(ts + hold, "sell", tok, 100, 1000 + p, sig=f"s{i}"))
            ts += hold + HOUR
        trips, orphan = build_round_trips("solana", "w", legs)
        return compute_metrics(trips, legs, ts + DAY, {}, orphan, {"sigs_per_day": 5, "cex_volume_share": 0.9})

    def test_basic_stats(self):
        m = self.make([100, -50, 200, -50, 100], tokens=[TOKEN, TOKEN2])
        self.assertEqual(m["closed"], 5)
        self.assertAlmostEqual(m["win_rate"], 0.6)
        self.assertAlmostEqual(m["profit_factor"], 400 / 100)
        self.assertAlmostEqual(m["realized_pnl"], 300)
        self.assertLess(m["win_rate_lb"], 0.6)

    def test_drawdown(self):
        self.assertAlmostEqual(max_drawdown([100, 120, 90, 130]), 0.25)

    def test_wilson_small_sample_is_humble(self):
        self.assertLess(wilson_lb(3, 3), 0.5)
        self.assertGreater(wilson_lb(80, 100), 0.7)

    def test_hft_is_hard_flag_and_zero_score(self):
        m = self.make([5] * 20, hold=30)
        f = flags_for(m, CFG)
        self.assertIn("hft_hold", f)
        self.assertEqual(score_wallet(m, f, CFG)[0], 0.0)

    def test_one_hit_wonder_is_penalized(self):
        good = self.make([100, 80, 120, 90, -40, 110, 95, 105, -30, 100, 90, 85, 100, -20], tokens=[TOKEN, TOKEN2, "T3", "T4"])
        lucky = self.make([5000] + [-10] * 13, tokens=[TOKEN, TOKEN2, "T3", "T4"])
        fg, fl = flags_for(good, CFG), flags_for(lucky, CFG)
        self.assertIn("one_hit", fl)
        self.assertNotIn("one_hit", fg)
        self.assertGreater(score_wallet(good, fg, CFG)[0], score_wallet(lucky, fl, CFG)[0])


if __name__ == "__main__":
    unittest.main()
