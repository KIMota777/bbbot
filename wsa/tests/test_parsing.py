"""Разбор транзакций в ноги сделок: Solana и EVM."""
import unittest

from helpers import POOL, RENT_TOKEN_ACCOUNT, TOKEN, TOKEN2, USDC, W, WSOL, sol_tx, tb

from wsa.chains import evm
from wsa.chains.solana import SOL, legs_from_deltas, parse_tx


class SolanaParse(unittest.TestCase):
    def test_buy_token_with_sol_fee_added_back(self):
        # кошелёк отдал 1 SOL + комиссию, получил 1000 токенов
        tx = sol_tx("s1", [10_000_000_000, 5_000_000_000], [10_000_000_000 - 1_000_000_000 - 5000, 6_000_000_000],
                    [tb(2, TOKEN, W, 0)], [tb(2, TOKEN, W, 1000_000000)], keys=[W, POOL, "ata"])
        p = parse_tx(tx, W)
        self.assertEqual(p["kind"], "swap")
        self.assertEqual(len(p["legs"]), 1)
        l = p["legs"][0]
        self.assertEqual((l["token"], l["side"]), (TOKEN, "buy"))
        self.assertAlmostEqual(l["qty"], 1000.0)
        self.assertEqual(l["quote"], SOL)
        self.assertAlmostEqual(l["quote_qty"], 1.0, places=9)  # комиссия не попала в цену

    def test_sell_token_for_usdc_account_closed_rent_ignored(self):
        # продал все токены за USDC, счёт токена закрыт — рента вернулась кошельку
        pre = [tb(2, TOKEN, W, 500_000000), tb(3, USDC, W, 10_000000)]
        post = [tb(3, USDC, W, 260_000000)]
        tx = sol_tx("s2", [1_000_000_000, 0, RENT_TOKEN_ACCOUNT, 0],
                    [1_000_000_000 + RENT_TOKEN_ACCOUNT - 5000, 0, 0, 0], pre, post, keys=[W, POOL, "ataX", "ataU"])
        p = parse_tx(tx, W)
        self.assertEqual([(l["token"], l["side"]) for l in p["legs"]], [(TOKEN, "sell")])
        self.assertAlmostEqual(p["legs"][0]["quote_qty"], 250.0)

    def test_sol_to_usdc_is_a_sol_sale(self):
        # своп на паре SOL/USDC: SOL — торгуемый актив, USDC — котировка
        pre = [tb(2, WSOL, W, 2_000_000_000, 9), tb(3, USDC, W, 0)]
        post = [tb(2, WSOL, W, 0, 9), tb(3, USDC, W, 227_000000)]
        tx = sol_tx("s3", [100_000_000, 0, 0, 0], [100_000_000 - 5000, 0, 0, 0], pre, post, keys=[W, POOL, "w", "u"])
        p = parse_tx(tx, W)
        self.assertEqual(len(p["legs"]), 1)
        self.assertEqual((p["legs"][0]["token"], p["legs"][0]["side"]), (SOL, "sell"))
        self.assertAlmostEqual(p["legs"][0]["qty"], 2.0)
        self.assertAlmostEqual(p["legs"][0]["quote_qty"], 227.0)

    def test_wrap_swap_unwrap_in_one_tx_counts_native_once(self):
        # wSOL-счёт создан и закрыт внутри транзакции: в балансах его нет, SOL-нога = нативная разница
        tx = sol_tx("s4", [5_000_000_000, 0], [5_000_000_000 - 2_000_000_000 - 5000, 0],
                    [tb(2, TOKEN, W, 0)], [tb(2, TOKEN, W, 7_000000)], keys=[W, POOL, "ata"])
        p = parse_tx(tx, W)
        self.assertAlmostEqual(p["legs"][0]["quote_qty"], 2.0)

    def test_token_to_token_gives_two_legs(self):
        pre = [tb(2, TOKEN, W, 100_000000), tb(3, TOKEN2, W, 0)]
        post = [tb(2, TOKEN, W, 0), tb(3, TOKEN2, W, 50_000000)]
        tx = sol_tx("s5", [1_000_000_000, 0, 0, 0], [1_000_000_000 - 5000, 0, 0, 0], pre, post, keys=[W, POOL, "a", "b"])
        p = parse_tx(tx, W)
        self.assertEqual(sorted((l["token"], l["side"]) for l in p["legs"]), sorted([(TOKEN, "sell"), (TOKEN2, "buy")]))

    def test_incoming_transfer_has_counterparty(self):
        pre = [tb(2, TOKEN, W, 0), tb(3, TOKEN, "Sender", 900_000000)]
        post = [tb(2, TOKEN, W, 400_000000), tb(3, TOKEN, "Sender", 500_000000)]
        tx = sol_tx("s6", [1_000_000_000, 0, 0, 0], [1_000_000_000, 0, 0, 0], pre, post,
                    keys=["Sender", POOL, "a", "b"])  # платит комиссию отправитель
        p = parse_tx(tx, W)
        self.assertEqual(p["kind"], "transfer")
        self.assertEqual((p["legs"][0]["side"], p["legs"][0]["counterparty"]), ("tin", "Sender"))

    def test_failed_tx(self):
        tx = sol_tx("s7", [1, 0], [1, 0], [], [], err={"InstructionError": [0, "Custom"]})
        self.assertEqual(parse_tx(tx, W)["kind"], "failed")

    def test_stable_to_stable_is_not_a_trade(self):
        self.assertEqual(legs_from_deltas({USDC: -100.0, "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": 99.9}), [])


class EvmGroup(unittest.TestCase):
    W = "0xabc0000000000000000000000000000000000001"
    ROUTER = "0xrouter00000000000000000000000000000000000"
    TOK = "0xtoken0000000000000000000000000000000000001"

    def test_buy_with_eth_and_sell_to_eth_via_internal(self):
        txlist = [{"hash": "0x1", "timeStamp": "100", "blockNumber": "10", "from": self.W, "to": self.ROUTER,
                   "value": str(10 ** 18), "isError": "0"},
                  {"hash": "0x2", "timeStamp": "200", "blockNumber": "11", "from": self.W, "to": self.ROUTER,
                   "value": "0", "isError": "0"}]
        tokentx = [{"hash": "0x1", "timeStamp": "100", "blockNumber": "10", "from": self.ROUTER, "to": self.W,
                    "contractAddress": self.TOK, "value": str(5 * 10 ** 20), "tokenDecimal": "18", "tokenSymbol": "TOK"},
                   {"hash": "0x2", "timeStamp": "200", "blockNumber": "11", "from": self.W, "to": self.ROUTER,
                    "contractAddress": self.TOK, "value": str(5 * 10 ** 20), "tokenDecimal": "18", "tokenSymbol": "TOK"}]
        internal = [{"hash": "0x2", "timeStamp": "200", "blockNumber": "11", "from": self.ROUTER, "to": self.W,
                     "value": str(12 * 10 ** 17), "isError": "0"}]
        groups = evm.group_transfers("ethereum", self.W, tokentx, txlist, internal)
        kinds = [evm.legs_from_group("ethereum", g) for g in groups]
        (k1, l1), (k2, l2) = kinds
        self.assertEqual((k1, l1[0]["side"], l1[0]["quote"]), ("swap", "buy", evm.NATIVE))
        self.assertAlmostEqual(l1[0]["qty"], 500.0)
        self.assertAlmostEqual(l1[0]["quote_qty"], 1.0)
        self.assertEqual((k2, l2[0]["side"]), ("swap", "sell"))
        self.assertAlmostEqual(l2[0]["quote_qty"], 1.2)

    def test_weth_merges_with_native(self):
        weth = evm.WRAPPED["base"]
        tokentx = [{"hash": "0x3", "timeStamp": "1", "blockNumber": "1", "from": self.W, "to": self.ROUTER,
                    "contractAddress": weth, "value": str(10 ** 18), "tokenDecimal": "18", "tokenSymbol": "WETH"},
                   {"hash": "0x3", "timeStamp": "1", "blockNumber": "1", "from": self.ROUTER, "to": self.W,
                    "contractAddress": self.TOK, "value": str(10 ** 18), "tokenDecimal": "18", "tokenSymbol": "TOK"}]
        g = evm.group_transfers("base", self.W, tokentx, [], [])[0]
        kind, legs = evm.legs_from_group("base", g)
        self.assertEqual((legs[0]["side"], legs[0]["quote"]), ("buy", evm.NATIVE))


if __name__ == "__main__":
    unittest.main()
