"""Сквозная проверка: входы группы в базе -> сборка стратегии -> бэктест на свечах.

Синтетический рынок: в случайные моменты у токена всплеск объёма, после которого
цена растёт; кошельки группы покупают на всплесках (и иногда наугад). Система
должна найти это сама: подпись отбора — объём выше, чем в случайные моменты;
принятое правило — про объём; бэктест — лучше случайных входов.
"""
import json
import random
import unittest

from helpers import FakeCtx

from wsa.core.backtest import backtest_strategy
from wsa.core.features import classify, forward, price_features, regime
from wsa.core.series import Series
from wsa.core.strategy import build
from wsa.util import DAY, HOUR, now

N_TOKENS = 10
HOURS = 150 * 24


def make_market(seed=3):
    rng = random.Random(seed)
    start = (now() - HOURS * HOUR) // HOUR * HOUR
    series, spikes = {}, {}
    for k in range(N_TOKENS):
        p, bars, sp = 1.0, [], []
        boost = 0
        for i in range(HOURS):
            ts = start + i * HOUR
            vol = rng.uniform(800, 1200)
            if i > 200 and i < HOURS - 200 and rng.random() < 0.004:
                vol *= 6  # всплеск объёма...
                boost = 36  # ...и 36 часов роста после него
                sp.append(ts + HOUR)
            drift = 0.004 if boost > 0 else -0.0003
            boost = max(0, boost - 1)
            o = p
            c = p * (1 + drift + rng.gauss(0, 0.006))
            bars.append([ts, o, max(o, c) * 1.002, min(o, c) * 0.998, c, vol])
            p = c
        series[f"T{k}"] = Series(bars, HOUR, f"cex:fake:T{k}")
        spikes[f"T{k}"] = sp
    btc = Series([[start + i * HOUR, 1, 1, 1, 1, 1] for i in range(HOURS)], HOUR)
    return series, spikes, btc


class FakePricer:
    def __init__(self, series, btc):
        self.series, self.btc = series, btc

    def _cex_series(self, cex, symbol, tf, t0, t1):
        return self.btc

    def token_series(self, chain, tok, t0, t1, tf="1h", allow_dex=True):
        return self.series.get(tok, self.btc)

    def dex_series(self, *a, **k):
        return None


class FakeTokens:
    def cex_map(self, chain, token, which="trade"):
        return {"symbol": f"{token}/USDT", "listed_since": 1, "multiplier": 1.0}


class FakeCex:
    def __init__(self, series):
        self.series = series

    def ohlcv(self, symbol, tf, lo, hi):
        s = self.series[symbol.split("/")[0]]
        return [b for b in s.bars if lo <= b[0] <= hi]


def fill_entries(ctx, series, spikes, btc, wallets=("a", "b", "c", "d")):
    rng = random.Random(9)
    rows = []
    for tok, s in series.items():
        moments = [(ts, True) for ts in spikes[tok]]
        moments += [(rng.randint(s.start + 10 * DAY, s.end - 10 * DAY) // HOUR * HOUR + 1800, False) for _ in range(6)]
        for ts, informed in moments:
            w = rng.choice(wallets)
            price = s.close_before(ts + 1) if informed else s.close_before(ts)
            f = price_features(s, ts + 5, price)
            f.update(regime(btc, None, ts))
            f.update(token_age_days=400, sector=rng.choice(["Meme", "AI"]), cex_listed=True, cex_listed_now=True,
                     buy_index=1, buy_index_bucket="first", size_rel=1.0)
            f["entry_type"] = classify(f, ctx.cfg)
            rows.append(("solana", w, f"sig{tok}{ts}", 0, tok, ts + 5, 1000.0, price, f"{tok}:{ts}", 1, f["entry_type"],
                         json.dumps(f), json.dumps(forward(s, ts + 5, price)), json.dumps({}), s.src, now()))
    ctx.db.executemany("INSERT INTO entries(chain, wallet, sig, idx, token, ts, usd, price, rt_key, buy_index, entry_type,"
                       " features, fwd, outcome, price_src, computed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


class StrategyEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        series, spikes, btc = make_market()
        ctx = FakeCtx()
        ctx.pricer, ctx.tokens, ctx.cex, ctx.ref = FakePricer(series, btc), FakeTokens(), FakeCex(series), None
        ctx.cfg.strategy.min_support = 10
        ctx.cfg.strategy.acceptance.min_oos_trades = 6
        ctx.cfg.backtest.random_trials = 40
        cls.n = fill_entries(ctx, series, spikes, btc)
        cohort = [{"chain": "solana", "address": w, "score": 70, "hold_p50": DAY} for w in "abcd"]
        cls.ctx, cls.s = ctx, build(ctx, cohort, horizon="24h")

    def test_built_and_signature_finds_volume(self):
        s = self.s
        self.assertTrue(s["ok"], s.get("reason"))
        self.assertGreater(self.n, 60)
        top = {x["feature"]: x for x in s["signature"][:6]}
        self.assertIn("vol_ratio", top, "подпись отбора должна показать всплеск объёма")
        self.assertGreater(top["vol_ratio"]["effect"], 0.15)

    def test_rule_is_about_volume_and_accepted(self):
        acc = [r for r in self.s["wallet_trigger"]["rules"] if r["accepted"]]
        self.assertTrue(acc, [r["reason"] for r in self.s["wallet_trigger"]["rules"]])
        self.assertTrue(any(any(c["f"] in ("vol_ratio", "ret_1h", "ret_4h") for c in r["conds"]) for r in acc))
        self.assertTrue(self.s["summary_ru"])

    def test_backtest_beats_random(self):
        bt = backtest_strategy(self.ctx, self.s, only_test=False, tf="1h")
        self.assertGreater(bt["summary"]["n"], 5)
        self.assertGreater(bt["summary"]["total_return"], 0)
        self.assertGreater(bt["beats_random_share"], 0.7)


if __name__ == "__main__":
    unittest.main()
