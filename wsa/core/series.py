"""Ценовой ряд (свечи) с быстрым поиском по времени."""
from __future__ import annotations

from bisect import bisect_left, bisect_right


class Series:
    """Свечи [ts, o, h, l, c, v] по возрастанию ts. step — длина свечи в секундах."""

    def __init__(self, bars: list[list[float]], step: int, src: str = ""):
        self.bars = bars
        self.step = step
        self.src = src
        self.ts = [int(b[0]) for b in bars]

    def __len__(self):
        return len(self.bars)

    @property
    def start(self) -> int | None:
        return self.ts[0] if self.ts else None

    @property
    def end(self) -> int | None:
        return self.ts[-1] + self.step if self.ts else None

    def covers(self, ts: int) -> bool:
        return bool(self.ts) and self.ts[0] <= ts < self.ts[-1] + self.step

    def idx_at(self, ts: int) -> int | None:
        """Индекс свечи, внутри которой ts (или последней до ts)."""
        i = bisect_right(self.ts, ts) - 1
        return i if i >= 0 else None

    def price_at(self, ts: int, max_gap: int | None = None) -> float | None:
        """Цена на момент ts: закрытие свечи, в которую попадает ts.

        Внутри свечи точнее нельзя; max_gap не даёт взять цену из свечи,
        которая была слишком давно (дыра в торгах).
        """
        i = self.idx_at(ts)
        if i is None:
            return None
        if max_gap is not None and ts - self.ts[i] > max_gap:
            return None
        return self.bars[i][4]

    def open_after(self, ts: int) -> tuple[int, float] | None:
        """Открытие первой свечи, начавшейся строго после ts (вход с задержкой)."""
        i = bisect_right(self.ts, ts)
        if i >= len(self.ts):
            return None
        return self.ts[i], self.bars[i][1]

    def window(self, t0: int, t1: int) -> list[list[float]]:
        """Свечи, начавшиеся в [t0, t1)."""
        i = bisect_left(self.ts, t0)
        j = bisect_left(self.ts, t1)
        return self.bars[i:j]

    def close_before(self, ts: int) -> float | None:
        """Закрытие последней свечи, полностью закрытой к моменту ts."""
        i = bisect_right(self.ts, ts - self.step) - 1
        return self.bars[i][4] if i >= 0 else None
