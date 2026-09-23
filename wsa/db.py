"""SQLite-хранилище: кошельки, сырые ноги сделок, анализ, сигналы, позиции.

Одна база на всё — так проще переносить и бэкапить. WAL позволяет читать
отчёт, пока трекер пишет.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .util import now

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
  chain TEXT NOT NULL,
  address TEXT NOT NULL,
  label TEXT,
  source TEXT,
  status TEXT NOT NULL DEFAULT 'candidate',   -- candidate|bot|scanned|rejected|watch
  added_at INTEGER NOT NULL,
  seen_trades INTEGER DEFAULT 0,
  seen_volume_usd REAL DEFAULT 0,
  seen_pools TEXT,
  score REAL,
  scanned_at INTEGER,
  scan_depth TEXT,
  note TEXT,
  PRIMARY KEY (chain, address)
);

CREATE TABLE IF NOT EXISTS sync_state (
  chain TEXT NOT NULL,
  address TEXT NOT NULL,
  newest_sig TEXT, newest_ts INTEGER,
  oldest_sig TEXT, oldest_ts INTEGER,
  newest_block INTEGER,
  sig_count INTEGER DEFAULT 0,
  backfill_complete INTEGER DEFAULT 0,
  sigs_per_day REAL,
  updated_at INTEGER,
  PRIMARY KEY (chain, address)
);

CREATE TABLE IF NOT EXISTS seen_tx (
  chain TEXT NOT NULL, address TEXT NOT NULL, sig TEXT NOT NULL,
  ts INTEGER, kind TEXT,
  PRIMARY KEY (chain, address, sig)
);

CREATE TABLE IF NOT EXISTS legs (
  chain TEXT NOT NULL, wallet TEXT NOT NULL, sig TEXT NOT NULL, idx INTEGER NOT NULL,
  ts INTEGER NOT NULL, slot INTEGER,
  token TEXT NOT NULL,
  side TEXT NOT NULL,              -- buy|sell|tin|tout
  qty REAL NOT NULL,
  quote TEXT, quote_qty REAL,
  usd REAL, price REAL,
  valued_by TEXT,                  -- quote|market|none
  counterparty TEXT,
  source TEXT,                     -- backfill|live
  PRIMARY KEY (chain, wallet, sig, idx)
);
CREATE INDEX IF NOT EXISTS legs_wallet_ts ON legs(chain, wallet, ts);
CREATE INDEX IF NOT EXISTS legs_token_ts ON legs(chain, token, ts);

CREATE TABLE IF NOT EXISTS tokens (
  chain TEXT NOT NULL, address TEXT NOT NULL,
  symbol TEXT, name TEXT,
  created_at INTEGER,
  best_pair TEXT, dex TEXT,
  liquidity_usd REAL, mc REAL, fdv REAL, price_usd REAL, vol24 REAL,
  sector TEXT, categories TEXT, coingecko_id TEXT,
  cex TEXT,                        -- json: {exchange: {symbol, multiplier, listed_since}}
  holders INTEGER, top10_pct REAL,
  updated_at INTEGER, cg_updated_at INTEGER, cex_updated_at INTEGER, holders_updated_at INTEGER,
  PRIMARY KEY (chain, address)
);

CREATE TABLE IF NOT EXISTS token_snapshots (
  chain TEXT NOT NULL, address TEXT NOT NULL, ts INTEGER NOT NULL,
  price REAL, mc REAL, fdv REAL, liquidity REAL, vol24 REAL,
  holders INTEGER, top10_pct REAL, chg_1h REAL, chg_24h REAL,
  buys_24h INTEGER, sells_24h INTEGER, source TEXT,
  PRIMARY KEY (chain, address, ts)
);

CREATE TABLE IF NOT EXISTS ohlcv (
  src TEXT NOT NULL, symbol TEXT NOT NULL, tf TEXT NOT NULL, ts INTEGER NOT NULL,
  o REAL, h REAL, l REAL, c REAL, v REAL,
  PRIMARY KEY (src, symbol, tf, ts)
);
CREATE TABLE IF NOT EXISTS ohlcv_cover (
  src TEXT NOT NULL, symbol TEXT NOT NULL, tf TEXT NOT NULL,
  start_ts INTEGER NOT NULL, end_ts INTEGER NOT NULL,
  PRIMARY KEY (src, symbol, tf, start_ts)
);

CREATE TABLE IF NOT EXISTS round_trips (
  chain TEXT NOT NULL, wallet TEXT NOT NULL, rt_key TEXT NOT NULL,
  token TEXT, open_ts INTEGER, close_ts INTEGER, status TEXT,
  n_buys INTEGER, n_sells INTEGER, cost REAL, proceeds REAL, pnl REAL, pnl_pct REAL,
  hold_sec REAL, max_cost REAL, entry_type TEXT, build TEXT, exit_kind TEXT,
  unmatched_qty REAL, detail TEXT,
  PRIMARY KEY (chain, wallet, rt_key)
);

CREATE TABLE IF NOT EXISTS analysis (
  chain TEXT NOT NULL, address TEXT NOT NULL, computed_at INTEGER,
  score REAL, metrics TEXT, flags TEXT, profile TEXT,
  PRIMARY KEY (chain, address)
);

CREATE TABLE IF NOT EXISTS entries (
  chain TEXT NOT NULL, wallet TEXT NOT NULL, sig TEXT NOT NULL, idx INTEGER NOT NULL,
  token TEXT, ts INTEGER, usd REAL, price REAL,
  rt_key TEXT, buy_index INTEGER,
  entry_type TEXT, features TEXT, fwd TEXT, outcome TEXT,
  price_src TEXT, computed_at INTEGER,
  PRIMARY KEY (chain, wallet, sig, idx)
);
CREATE INDEX IF NOT EXISTS entries_token_ts ON entries(chain, token, ts);

CREATE TABLE IF NOT EXISTS universe (
  chain TEXT NOT NULL, token TEXT NOT NULL,
  symbol TEXT, exchange TEXT, cex_symbol TEXT, multiplier REAL,
  best_pair TEXT, liquidity_usd REAL, vol24 REAL, cex_turnover_24h REAL,
  updated_at INTEGER,
  PRIMARY KEY (chain, token, exchange)
);

CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER, kind TEXT,
  chain TEXT, wallet TEXT, token TEXT, sig TEXT,
  exchange TEXT, symbol TEXT, side TEXT,
  rule TEXT, score REAL, features TEXT,
  status TEXT, reason TEXT
);

CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT, exchange TEXT, market TEXT, symbol TEXT,
  chain TEXT, token TEXT, leader TEXT,
  signal_id INTEGER, opened_at INTEGER, closed_at INTEGER,
  qty REAL, qty_open REAL, entry_price REAL, cost_usd REAL,
  sl REAL, tps TEXT, time_stop INTEGER, peak_price REAL,
  realized_usd REAL DEFAULT 0, fees_usd REAL DEFAULT 0,
  status TEXT, exit_reason TEXT
);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  position_id INTEGER, ts INTEGER, mode TEXT, exchange TEXT, symbol TEXT,
  side TEXT, type TEXT, qty REAL, price REAL, filled REAL, avg_price REAL, fee_usd REAL,
  status TEXT, exch_id TEXT, purpose TEXT, raw TEXT
);

CREATE TABLE IF NOT EXISTS equity (
  ts INTEGER NOT NULL, mode TEXT NOT NULL, equity REAL,
  PRIMARY KEY (ts, mode)
);

CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS http_cache (k TEXT PRIMARY KEY, ts INTEGER, body TEXT);
"""


class DB:
    """Тонкая обёртка над sqlite3 с блокировкой: трекер пишет из нескольких потоков."""

    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=60)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- базовые операции ----------

    def execute(self, sql: str, params: Iterable = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def executemany(self, sql: str, rows: Iterable[Iterable]) -> None:
        with self._lock:
            self.conn.executemany(sql, [tuple(r) for r in rows])
            self.conn.commit()

    def query(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    def one(self, sql: str, params: Iterable = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    @contextmanager
    def tx(self):
        with self._lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    # ---------- kv / кэш ----------

    def kv_get(self, k: str, default: Any = None) -> Any:
        r = self.one("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(r["v"]) if r else default

    def kv_set(self, k: str, v: Any) -> None:
        self.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?, ?)", (k, json.dumps(v)))

    def cache_get(self, k: str, ttl: int) -> Any:
        r = self.one("SELECT ts, body FROM http_cache WHERE k=?", (k,))
        if not r or (ttl and now() - r["ts"] > ttl):
            return None
        return json.loads(r["body"])

    def cache_set(self, k: str, body: Any) -> None:
        self.execute("INSERT OR REPLACE INTO http_cache(k, ts, body) VALUES(?, ?, ?)",
                     (k, now(), json.dumps(body)))

    # ---------- кошельки ----------

    def upsert_candidate(self, chain: str, address: str, source: str, pool: str | None = None,
                         trades: int = 0, volume: float = 0.0, label: str | None = None) -> bool:
        """Добавляет кандидата или копит статистику встреч. True — если кошелёк новый."""
        with self._lock:
            r = self.conn.execute("SELECT seen_pools FROM wallets WHERE chain=? AND address=?",
                                  (chain, address)).fetchone()
            if r is None:
                pools = [pool] if pool else []
                self.conn.execute(
                    "INSERT INTO wallets(chain, address, label, source, status, added_at, seen_trades,"
                    " seen_volume_usd, seen_pools) VALUES(?,?,?,?,?,?,?,?,?)",
                    (chain, address, label, source, "candidate", now(), trades, volume, json.dumps(pools)))
                self.conn.commit()
                return True
            pools = json.loads(r["seen_pools"] or "[]")
            if pool and pool not in pools:
                pools.append(pool)
            self.conn.execute(
                "UPDATE wallets SET seen_trades=seen_trades+?, seen_volume_usd=seen_volume_usd+?,"
                " seen_pools=?, label=COALESCE(?, label) WHERE chain=? AND address=?",
                (trades, volume, json.dumps(pools), label, chain, address))
            self.conn.commit()
            return False

    def set_wallet(self, chain: str, address: str, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE wallets SET {cols} WHERE chain=? AND address=?",
                     (*fields.values(), chain, address))

    def wallet(self, chain: str, address: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM wallets WHERE chain=? AND address=?", (chain, address))

    def find_wallet(self, address: str) -> sqlite3.Row | None:
        """Кошелёк по адресу без указания сети (или по уникальному префиксу)."""
        r = self.one("SELECT * FROM wallets WHERE address=?", (address,))
        if r:
            return r
        rows = self.query("SELECT * FROM wallets WHERE address LIKE ?", (address + "%",))
        return rows[0] if len(rows) == 1 else None

    def wallets(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.query("SELECT * FROM wallets WHERE status=? ORDER BY score DESC NULLS LAST", (status,))
        return self.query("SELECT * FROM wallets ORDER BY score DESC NULLS LAST")

    # ---------- синхронизация ----------

    def sync(self, chain: str, address: str) -> dict:
        r = self.one("SELECT * FROM sync_state WHERE chain=? AND address=?", (chain, address))
        return dict(r) if r else {}

    def set_sync(self, chain: str, address: str, **fields) -> None:
        with self._lock:
            exists = self.conn.execute("SELECT 1 FROM sync_state WHERE chain=? AND address=?",
                                       (chain, address)).fetchone()
            fields["updated_at"] = now()
            if exists:
                cols = ", ".join(f"{k}=?" for k in fields)
                self.conn.execute(f"UPDATE sync_state SET {cols} WHERE chain=? AND address=?",
                                  (*fields.values(), chain, address))
            else:
                cols = ", ".join(["chain", "address", *fields.keys()])
                qs = ", ".join("?" * (len(fields) + 2))
                self.conn.execute(f"INSERT INTO sync_state({cols}) VALUES({qs})",
                                  (chain, address, *fields.values()))
            self.conn.commit()

    def seen_sigs(self, chain: str, address: str) -> set[str]:
        return {r["sig"] for r in self.query("SELECT sig FROM seen_tx WHERE chain=? AND address=?",
                                             (chain, address))}

    def mark_seen(self, chain: str, address: str, items: list[tuple[str, int, str]]) -> None:
        # REPLACE: транзакция из очереди повторов ('retry') получает свой настоящий тип
        self.executemany("INSERT OR REPLACE INTO seen_tx(chain, address, sig, ts, kind) VALUES(?,?,?,?,?)",
                         [(chain, address, s, ts, k) for s, ts, k in items])

    def retry_sigs(self, chain: str, address: str) -> list[dict]:
        """Подписи, которые не удалось загрузить в прошлый раз (RPC отказал)."""
        return [{"signature": r["sig"], "blockTime": r["ts"]} for r in self.query(
            "SELECT sig, ts FROM seen_tx WHERE chain=? AND address=? AND kind='retry'", (chain, address))]

    # ---------- ноги сделок ----------

    def save_legs(self, chain: str, wallet: str, legs: list[dict], source: str) -> int:
        rows = [(chain, wallet, l["sig"], l["idx"], l["ts"], l.get("slot"), l["token"], l["side"],
                 l["qty"], l.get("quote"), l.get("quote_qty"), l.get("usd"), l.get("price"),
                 l.get("valued_by"), l.get("counterparty"), source) for l in legs]
        self.executemany(
            "INSERT OR REPLACE INTO legs(chain, wallet, sig, idx, ts, slot, token, side, qty, quote,"
            " quote_qty, usd, price, valued_by, counterparty, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)
        return len(rows)

    def legs(self, chain: str, wallet: str) -> list[dict]:
        return [dict(r) for r in self.query(
            "SELECT * FROM legs WHERE chain=? AND wallet=? ORDER BY ts, sig, idx", (chain, wallet))]

    # ---------- токены ----------

    def token(self, chain: str, address: str) -> dict | None:
        r = self.one("SELECT * FROM tokens WHERE chain=? AND address=?", (chain, address))
        if not r:
            return None
        d = dict(r)
        d["cex"] = json.loads(d["cex"]) if d.get("cex") else {}
        d["categories"] = json.loads(d["categories"]) if d.get("categories") else []
        return d

    def upsert_token(self, chain: str, address: str, **fields) -> None:
        for k in ("cex", "categories"):
            if k in fields and not isinstance(fields[k], str) and fields[k] is not None:
                fields[k] = json.dumps(fields[k])
        with self._lock:
            exists = self.conn.execute("SELECT 1 FROM tokens WHERE chain=? AND address=?",
                                       (chain, address)).fetchone()
            if exists:
                if fields:
                    cols = ", ".join(f"{k}=?" for k in fields)
                    self.conn.execute(f"UPDATE tokens SET {cols} WHERE chain=? AND address=?",
                                      (*fields.values(), chain, address))
            else:
                cols = ", ".join(["chain", "address", *fields.keys()])
                qs = ", ".join("?" * (len(fields) + 2))
                self.conn.execute(f"INSERT INTO tokens({cols}) VALUES({qs})", (chain, address, *fields.values()))
            self.conn.commit()

    # ---------- анализ ----------

    def save_analysis(self, chain: str, address: str, score: float | None, metrics: dict,
                      flags: dict, profile: dict | None) -> None:
        self.execute(
            "INSERT OR REPLACE INTO analysis(chain, address, computed_at, score, metrics, flags, profile)"
            " VALUES(?,?,?,?,?,?,?)",
            (chain, address, now(), score, json.dumps(metrics), json.dumps(flags),
             json.dumps(profile) if profile is not None else None))

    def analysis(self, chain: str, address: str) -> dict | None:
        r = self.one("SELECT * FROM analysis WHERE chain=? AND address=?", (chain, address))
        if not r:
            return None
        d = dict(r)
        for k in ("metrics", "flags", "profile"):
            d[k] = json.loads(d[k]) if d.get(k) else None
        return d
