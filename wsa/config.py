"""Конфиг анализатора кошельков: значения по умолчанию + wsa.toml + ключи из окружения/.env.

Пакет живёт внутри bbbot: корень — корень репозитория, данные — data/wsa/
(в .gitignore), настройки — wsa.toml в корне (необязателен). Ключи API никогда
не хранятся в wsa.toml — только в переменных окружения или локальном .env,
как и у ботов bbbot (BYBIT_API_KEY / BYBIT_API_SECRET).
"""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "general": {
        "db_path": "data/wsa/wsa.db",
        "log_level": "INFO",
    },
    "solana": {
        # публичные RPC без ключа; при HELIUS_API_KEY в окружении Helius RPC
        # встаёт первым автоматически
        "rpc_urls": [
            "https://api.mainnet-beta.solana.com",
            "https://solana-rpc.publicnode.com",
        ],
        "rps_per_url": 3.0,  # для неизвестных эндпоинтов; у известных свои лимиты
        "workers": 3,
        # предфильтр ботов ДО загрузки транзакций: считаем по списку подписей.
        # Порог с запасом: у людей бывает много спам-переводов; реальные боты
        # в пуле SOL/USDC — от 70 000 транзакций в сутки
        "bot_sigs_per_day": 1500,
    },
    "evm": {
        # сети EVM: имя -> chainid / сеть GeckoTerminal / нативный актив / blockscout
        "chains": {
            "ethereum": {"chainid": 1, "gt": "eth", "native": "ETH", "blockscout": "https://eth.blockscout.com"},
            "base": {"chainid": 8453, "gt": "base", "native": "ETH", "blockscout": "https://base.blockscout.com"},
            "arbitrum": {"chainid": 42161, "gt": "arbitrum", "native": "ETH", "blockscout": "https://arbitrum.blockscout.com"},
            "optimism": {"chainid": 10, "gt": "optimism", "native": "ETH", "blockscout": "https://explorer.optimism.io"},
            "polygon": {"chainid": 137, "gt": "polygon_pos", "native": "POL", "blockscout": "https://polygon.blockscout.com"},
            "bsc": {"chainid": 56, "gt": "bsc", "native": "BNB", "blockscout": ""},
        },
    },
    "scan": {
        # quick — первичный отсев по последним транзакциям, deep — полная история
        "quick_max_tx": 250,
        "deep_max_tx": 4000,
        "deep_days": 180,
        "min_leg_usd": 5.0,  # мельче — пыль/спам, в анализ не идёт
    },
    "discovery": {
        "min_trade_usd": 1000,
        "interval_sec": 60,
        "minutes": 20,
        "universe_min_dex_vol": 100_000,
    },
    "filters": {
        "min_closed_trades": 12,
        "max_swaps_per_day": 60,
        "min_median_hold_sec": 300,
        "max_same_slot_share": 0.2,
        "max_top1_pnl_share": 0.6,
        "max_unmatched_share": 0.35,
        "min_history_days": 21,
        "min_cex_volume_share": 0.25,
    },
    "scoring": {
        "w_profit": 0.22,
        "w_quality": 0.22,
        "w_risk": 0.14,
        "w_consistency": 0.16,
        "w_sample": 0.10,
        "w_diversity": 0.06,
        "w_cex": 0.10,
    },
    "context": {
        "tf": "1h",  # свечи для контекста входа и форвард-доходностей
        "quote_tf": "5m",  # свечи SOL/ETH для пересчёта сделок в USD
        "early_age_days": 7,
        "momentum_ret_24h": 0.05,
        "momentum_ret_7d": 0.15,
        "breakout_vol_ratio": 1.5,
        "meanrev_dd_7d": -0.15,
        "meanrev_ret_24h": -0.08,
        "dex_ohlcv": True,  # догружать свечи DEX (GeckoTerminal) для токенов без CEX
    },
    "cex": {
        "exchange": "bybit",
        "market": "spot",  # spot | linear
        "quote": "USDT",
        "min_turnover_24h_usd": 1_000_000,
        "price_match_tolerance": 0.05,  # цена DEX vs CEX: иначе это другой токен с тем же тикером
        "reference_exchange": "bybit",  # откуда брать исторические цены
    },
    "strategy": {
        "horizons": ["6h", "24h", "3d", "7d"],
        "train_frac": 0.7,
        "min_support": 15,
        "max_rule_len": 2,
        "fee_roundtrip": 0.003,  # комиссии + проскальзывание туда-обратно
        "baseline_per_token": 40,  # случайных моментов на токен для контрольной группы
        "acceptance": {
            # правила приёмки задаются ДО прогона и не подгоняются под результат
            "min_oos_trades": 12,
            "min_oos_mean": 0.0,
            "min_oos_hit": 0.5,
            "max_degradation": 0.75,
        },
        "path": "data/wsa/strategy.json",
    },
    "backtest": {
        "latency_sec": 60,
        "taker_fee": 0.001,
        "slippage": 0.001,
        "random_trials": 200,
    },
    "tracking": {
        "poll_sec": 20,
        "snapshot_holders_min": 30,
        "rescore_hours": 24,
    },
    "trading": {
        "mode": "paper",  # paper | demo | testnet | live — live только явным флагом
        "paper_equity": 1000.0,
        "risk_per_trade": 0.01,
        "max_position_pct": 0.10,
        "max_open_positions": 5,
        "max_total_exposure": 0.5,
        "daily_loss_limit": 0.03,
        "max_drawdown_halt": 0.15,
        "max_consecutive_losses": 4,
        "pause_hours_after_losses": 12,
        "cooldown_hours": 12,
        "min_consensus": 1,
        "min_wallet_score": 55,
        "exit_on_leader_sell": True,
        "leader_sell_fraction": 0.5,
        "max_spread": 0.004,
        "max_chase": 0.03,  # не входить, если CEX уже на 3% выше цены лидера
        "leverage": 1,  # только для market = linear: плечо перпетуала
        "signal_max_age_sec": 300,
        "manage_sec": 10,
    },
    "scanner": {
        "interval_min": 15,
        "max_symbols": 150,
    },
}

SECRET_ENV = [
    "HELIUS_API_KEY",
    "BIRDEYE_API_KEY",
    "BLOCKSCOUT_API_KEY",
    "ETHERSCAN_API_KEY",
    "COINGECKO_API_KEY",
]


class Cfg(dict):
    """dict с доступом через точку: cfg.trading.mode."""

    def __getattr__(self, item):
        try:
            v = self[item]
        except KeyError as e:
            raise AttributeError(item) from e
        return v

    def __setattr__(self, key, value):
        self[key] = value


def _wrap(d):
    if isinstance(d, dict):
        return Cfg({k: _wrap(v) for k, v in d.items()})
    if isinstance(d, list):
        return [_wrap(x) for x in d]
    return d


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_dotenv(path: Path) -> None:
    """Минимальный разбор .env: KEY=VALUE, переменные окружения важнее файла."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def secret(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or None


def exchange_keys(exchange: str) -> dict:
    """Ключи биржи из окружения: BYBIT_API_KEY / BYBIT_API_SECRET (как в bbbot)."""
    p = exchange.upper()
    out = {}
    if secret(f"{p}_API_KEY"):
        out["apiKey"] = secret(f"{p}_API_KEY")
    if secret(f"{p}_API_SECRET"):
        out["secret"] = secret(f"{p}_API_SECRET")
    if secret(f"{p}_API_PASSWORD"):
        out["password"] = secret(f"{p}_API_PASSWORD")
    return out


def load_config(path: str | os.PathLike | None = None) -> Cfg:
    load_dotenv(ROOT / ".env")
    data = copy.deepcopy(DEFAULTS)
    p = Path(path) if path else ROOT / "wsa.toml"
    if p.exists():
        with open(p, "rb") as f:
            data = deep_merge(data, tomllib.load(f))
    cfg = _wrap(data)
    # относительные пути — от корня проекта, а не от текущей папки
    for sect, key in (("general", "db_path"), ("strategy", "path")):
        v = Path(cfg[sect][key])
        if not v.is_absolute():
            cfg[sect][key] = str(ROOT / v)
    helius = secret("HELIUS_API_KEY")
    if helius:
        url = f"https://mainnet.helius-rpc.com/?api-key={helius}"
        if url not in cfg.solana.rpc_urls:
            cfg.solana.rpc_urls = [url] + list(cfg.solana.rpc_urls)
    return cfg
