"""Контейнер сервисов: конфиг, база, HTTP и ленивые клиенты источников."""
from __future__ import annotations

import logging
from functools import cached_property

from .config import Cfg, load_config
from .db import DB
from .http import Http


class Ctx:
    def __init__(self, cfg: Cfg | None = None):
        self.cfg = cfg or load_config()
        logging.basicConfig(level=getattr(logging, str(self.cfg.general.log_level).upper(), logging.INFO),
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
        self.db = DB(self.cfg.general.db_path)
        self.http = Http(self.db)
        self._evm: dict = {}

    @cached_property
    def rpc(self):
        from .sources.solana_rpc import SolanaRpc
        c = self.cfg.solana
        return SolanaRpc(self.http, list(c.rpc_urls), c.rps_per_url, c.workers)

    @cached_property
    def ds(self):
        from .sources.dexscreener import DexScreener
        return DexScreener(self.http)

    @cached_property
    def gt(self):
        from .sources.geckoterminal import GeckoTerminal
        return GeckoTerminal(self.http)

    @cached_property
    def cg(self):
        from .sources.coingecko import CoinGecko
        return CoinGecko(self.http)

    @cached_property
    def birdeye(self):
        from .sources.birdeye import Birdeye
        return Birdeye(self.http)

    @cached_property
    def cex(self):
        """Публичные данные торговой биржи (рынки, тикеры, свечи)."""
        from .sources.cex import Cex
        c = self.cfg.cex
        return Cex(c.exchange, c.market, c.quote, self.db)

    @cached_property
    def ref(self):
        """Референсная биржа для исторических цен (спот)."""
        from .sources.cex import Cex
        c = self.cfg.cex
        if c.reference_exchange == c.exchange and c.market == "spot":
            return self.cex
        return Cex(c.reference_exchange, "spot", "USDT", self.db)

    def evm(self, chain: str):
        if chain not in self._evm:
            from .sources.evm import EvmExplorer
            self._evm[chain] = EvmExplorer(self.http, chain, self.cfg.evm.chains[chain])
        return self._evm[chain]

    @cached_property
    def pricer(self):
        from .core.pricing import Pricer
        return Pricer(self)

    @cached_property
    def tokens(self):
        from .core.tokeninfo import TokenInfo
        return TokenInfo(self)
