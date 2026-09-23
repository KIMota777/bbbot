"""Признаки входа (без заглядывания в будущее), классификация, поиск и приёмка правил."""
import random
import unittest

from helpers import bars_linear

from wsa.config import load_config
from wsa.core.backtest import simulate_trade
from wsa.core.features import classify, forward, price_features
from wsa.core.series import Series
from wsa.core.strategy import Rule, Cond, accept, add_consensus, dedupe, entity_map, mine, relationships
from wsa.util import DAY, HOUR

CFG = load_config("/nonexistent.toml")
T0 = 1_700_000_000 // HOUR * HOUR


class NoLookahead(unittest.TestCase):
    def test_features_ignore_the_current_bar(self):
        bars = bars_linear(T0, 400, HOUR, 1.0)
        ts = T0 + 300 * HOUR + 1800  # середина 300-й свечи
        # в текущей (ещё не закрытой) свече — гигантский памп; признаки его видеть не должны
        bars[300] = [bars[300][0], 1.0, 50.0, 1.0, 40.0, 1e9]
        f = price_features(Series(bars, HOUR), ts, 1.0)
        self.assertAlmostEqual(f["ret_24h"], 0.0, places=6)
        self.assertLess(f["vol_ratio"], 1.5)
        self.assertFalse(f["breakout_7d"])

    def test_forward_returns(self):
        bars = bars_linear(T0, 400, HOUR, 1.0, drift=0.01)
        s = Series(bars, HOUR)
        ts = T0 + 100 * HOUR
        fw = forward(s, ts, s.close_before(ts))
        self.assertAlmostEqual(fw["24h"], 1.01 ** 24 - 1, places=3)
        self.assertGreater(fw["mfe_7d"], fw["24h"])

    def test_classify(self):
        self.assertEqual(classify({"token_age_days": 2}, CFG), "early")
        self.assertEqual(classify({"token_age_days": 90, "ret_24h": 0.01, "breakout_7d": True, "vol_ratio": 2.5}, CFG), "breakout")
        self.assertEqual(classify({"token_age_days": 90, "ret_24h": 0.12}, CFG), "momentum")
        self.assertEqual(classify({"token_age_days": 90, "ret_24h": -0.02, "dd_high_7d": -0.3}, CFG), "mean_reversion")
        self.assertEqual(classify({"token_age_days": 90, "ret_24h": 0.0, "dd_high_7d": -0.05}, CFG), "neutral")
        self.assertEqual(classify({}, CFG), "unknown")


def synth_rows(n: int, seed: int = 1):
    """Входы, где работает только одно правило: объём ≥ 2× среднего -> рост, иначе падение."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        vr = rng.uniform(0.5, 4.0)
        noise = rng.gauss(0, 0.01)
        y = (0.06 if vr >= 2.0 else -0.04) + noise
        rows.append({"ts": T0 + i * 7 * HOUR, "token": f"T{i % 17}", "wallet": f"w{i % 3}",
                     "f": {"vol_ratio": vr, "ret_24h": rng.uniform(-0.2, 0.2), "sector": rng.choice(["Meme", "AI"])},
                     "fwd": {"24h": y}})
    return rows


class RuleMining(unittest.TestCase):
    def test_finds_true_rule_and_passes_oos(self):
        rows = synth_rows(300)
        train, test = rows[:210], rows[210:]
        rules, tested = mine(train, test, "24h", 0.003, ["vol_ratio", "ret_24h", "sector"], CFG)
        self.assertGreater(tested, 10)
        acc = [r for r in rules if r.accepted]
        self.assertTrue(acc, "истинное правило должно пройти проверку")
        self.assertTrue(any(c.f == "vol_ratio" and c.op == ">=" for c in acc[0].conds))

    def test_noise_does_not_pass(self):
        rng = random.Random(5)
        rows = [{"ts": T0 + i * HOUR, "token": f"T{i}", "wallet": "w", "fwd": {"24h": rng.gauss(-0.003, 0.05)},
                 "f": {"vol_ratio": rng.uniform(0, 3), "ret_24h": rng.uniform(-0.2, 0.2)}} for i in range(300)]
        rules, _ = mine(rows[:210], rows[210:], "24h", 0.003, ["vol_ratio", "ret_24h"], CFG)
        self.assertLessEqual(sum(1 for r in rules if r.accepted), 1)

    def test_acceptance_thresholds(self):
        acc = CFG.strategy.acceptance
        self.assertFalse(accept({"mean": 0.05}, {"n": 5, "mean": 0.05, "hit": 0.8}, acc)[0])
        self.assertFalse(accept({"mean": 0.05}, {"n": 30, "mean": -0.01, "hit": 0.6}, acc)[0])
        self.assertFalse(accept({"mean": 0.10}, {"n": 30, "mean": 0.01, "hit": 0.6}, acc)[0])  # деградация 90%
        self.assertTrue(accept({"mean": 0.05}, {"n": 30, "mean": 0.03, "hit": 0.6}, acc)[0])

    def test_rule_json_roundtrip(self):
        r = Rule([Cond("vol_ratio", ">=", 2.0), Cond("sector", "==", "Meme")], "w1")
        r2 = Rule.from_json(r.to_json())
        self.assertTrue(r2.match({"vol_ratio": 2.5, "sector": "Meme"}))
        self.assertFalse(r2.match({"vol_ratio": 2.5, "sector": "AI"}))
        self.assertFalse(r2.match({"sector": "Meme"}))  # нет признака — не совпало


class Cohort(unittest.TestCase):
    def test_same_owner_wallets_merge_and_consensus(self):
        rows = []
        for k in range(5):  # a и b всегда входят вместе — один владелец
            for w in ("a", "b"):
                rows.append({"wallet": w, "token": f"T{k}", "ts": T0 + k * DAY + (60 if w == "b" else 0), "f": {}})
        rows.append({"wallet": "c", "token": "T0", "ts": T0 + 600, "f": {}})
        rows.sort(key=lambda r: r["ts"])
        ents = entity_map(relationships(rows))
        self.assertEqual(ents["a"], ents["b"])
        self.assertNotEqual(ents["a"], ents["c"])
        add_consensus(rows, ents)
        c_row = next(r for r in rows if r["wallet"] == "c")
        self.assertEqual(c_row["f"]["consensus_24h"], 1)  # a и b — один голос
        self.assertEqual(len(dedupe(rows)), 5)


class HonestBacktest(unittest.TestCase):
    EX = {"sl": -0.05, "tp": [[0.05, 0.5], [0.10, 0.5]], "time_stop_sec": 10 * HOUR, "trail_after_tp1": True}

    def test_stop_before_take_in_same_bar(self):
        bars = [[T0, 100, 100, 100, 100, 1], [T0 + HOUR, 100, 120, 90, 110, 1]] + bars_linear(T0 + 2 * HOUR, 20, HOUR, 110)
        tr = simulate_trade(bars, HOUR, T0 + 10, self.EX, 60, 0.001, 0.0)
        self.assertEqual(tr.reason, "stop")
        self.assertLess(tr.pnl_pct, -0.05)

    def test_take_ladder_then_time_stop(self):
        bars = [[T0 + HOUR, 100, 101, 99.5, 100, 1], [T0 + 2 * HOUR, 100, 106, 99.8, 105, 1]] + \
               bars_linear(T0 + 3 * HOUR, 20, HOUR, 105)
        tr = simulate_trade(bars, HOUR, T0 + 10, self.EX, 60, 0.001, 0.0)
        reasons = [x[3] for x in tr.exits]
        self.assertEqual(reasons[0], "tp1")
        self.assertIn(reasons[-1], ("time", "trail"))
        self.assertGreater(tr.pnl_pct, 0)

    def test_gap_down_fills_at_open_not_stop(self):
        bars = [[T0 + HOUR, 100, 100, 100, 100, 1], [T0 + 2 * HOUR, 80, 81, 79, 80, 1]]
        tr = simulate_trade(bars, HOUR, T0 + 10, self.EX, 60, 0.0, 0.0)
        self.assertAlmostEqual(tr.exits[0][1], 80)  # гэп вниз — исполнение по открытию, а не по стопу 95


if __name__ == "__main__":
    unittest.main()
