"""Метки адресов (биржи, мосты, команды) из wsa_labels.csv в корне bbbot — задаёт пользователь.

Встроенного списка нет сознательно: ошибочная метка «это биржа» исказила бы
профиль сильнее, чем её отсутствие.
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

from ..config import ROOT


@lru_cache(maxsize=1)
def load(path: str | None = None) -> dict[str, dict]:
    p = Path(path) if path else ROOT / "wsa_labels.csv"
    out: dict[str, dict] = {}
    if not p.exists():
        return out
    with open(p, encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().startswith("#") or len(row) < 2:
                continue
            addr = row[0].strip()
            out[addr] = {"label": row[1].strip(), "type": (row[2].strip().lower() if len(row) > 2 else "other")}
            out[addr.lower()] = out[addr]
    return out


def of(address: str | None) -> dict | None:
    if not address:
        return None
    m = load()
    return m.get(address) or m.get(address.lower())
