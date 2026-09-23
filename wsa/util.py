"""Мелкие общие помощники: время, статистика, форматирование."""
from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone
from typing import Iterable, Sequence

MIN = 60
HOUR = 3600
DAY = 86400
WEEK = 7 * DAY

_DUR_UNITS = {"s": 1, "m": MIN, "h": HOUR, "d": DAY, "w": WEEK}


def now() -> int:
    return int(time.time())


def parse_dur(s: str | int | float) -> int:
    """'15m' -> 900, '24h' -> 86400, '3d' -> 259200; число — уже секунды."""
    if isinstance(s, (int, float)):
        return int(s)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhdw])\s*", s)
    if not m:
        raise ValueError(f"не понимаю длительность: {s!r}")
    return int(float(m.group(1)) * _DUR_UNITS[m.group(2)])


def fmt_ts(ts: int | float | None, with_time: bool = True) -> str:
    if not ts:
        return "—"
    d = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return d.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")


def fmt_dur(sec: float | None) -> str:
    """Короткая человекочитаемая длительность: 45с, 17м, 4.2ч, 3.1д."""
    if sec is None or (isinstance(sec, float) and math.isnan(sec)):
        return "—"
    sec = float(sec)
    if sec < MIN:
        return f"{sec:.0f}с"
    if sec < HOUR:
        return f"{sec / MIN:.0f}м"
    if sec < DAY:
        return f"{sec / HOUR:.1f}ч"
    return f"{sec / DAY:.1f}д"


def fmt_usd(x: float | None, signed: bool = False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    sign = ""
    if x < 0:
        sign = "−"
    elif signed and x > 0:
        sign = "+"
    a = abs(x)
    if a >= 1e9:
        s = f"${a / 1e9:.2f}B"
    elif a >= 1e6:
        s = f"${a / 1e6:.2f}M"
    elif a >= 1e3:
        s = f"${a / 1e3:.1f}k"
    else:
        s = f"${a:.2f}" if a < 100 else f"${a:.0f}"
    return sign + s


def fmt_pct(x: float | None, signed: bool = False, digits: int = 1) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    s = f"{x * 100:.{digits}f}%"
    if signed and x > 0:
        s = "+" + s
    return s.replace("-", "−")


def fmt_num(x: float | None, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—" if x is None or math.isnan(x) else "∞"
    return f"{x:.{digits}f}"


def short_addr(a: str, n: int = 4) -> str:
    return a if len(a) <= 2 * n + 1 else f"{a[:n]}…{a[-n:]}"


# ---------- статистика (без numpy: объёмы маленькие, зависимостей меньше) ----------

def clean(xs: Iterable[float | None]) -> list[float]:
    out = []
    for x in xs:
        if x is None:
            continue
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            continue
        out.append(float(x))
    return out


def mean(xs: Iterable[float | None]) -> float | None:
    v = clean(xs)
    return sum(v) / len(v) if v else None


def quantile(xs: Iterable[float | None], q: float) -> float | None:
    """Квантиль с линейной интерполяцией (как numpy по умолчанию)."""
    v = sorted(clean(xs))
    if not v:
        return None
    if len(v) == 1:
        return v[0]
    pos = (len(v) - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def median(xs: Iterable[float | None]) -> float | None:
    return quantile(xs, 0.5)


def quantiles(xs: Iterable[float | None], qs: Sequence[float] = (0.25, 0.5, 0.75, 0.9)) -> dict:
    v = clean(xs)
    return {f"p{int(q * 100)}": quantile(v, q) for q in qs}


def stdev(xs: Iterable[float | None]) -> float | None:
    v = clean(xs)
    if len(v) < 2:
        return None
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def tstat(xs: Iterable[float | None]) -> float | None:
    """t-статистика среднего против нуля: насколько доходность отличима от шума."""
    v = clean(xs)
    if len(v) < 3:
        return None
    s = stdev(v)
    if not s:
        return None
    return (sum(v) / len(v)) / (s / math.sqrt(len(v)))


def wilson_lb(k: int, n: int, z: float = 1.96) -> float:
    """Нижняя граница доверительного интервала Уилсона для доли k/n.

    При 3 сделках из 3 выигрышных даёт ~0.44, а не 1.0 — так малая выборка
    не выглядит гениальной.
    """
    if n <= 0:
        return 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / denom)


def safe_div(a: float | None, b: float | None, default: float | None = 0.0) -> float | None:
    if a is None or b is None or b == 0:
        return default
    return a / b


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def squash(x: float, scale: float) -> float:
    """Плавно отображает [0, ∞) в [0, 1): x=scale -> ~0.76."""
    if x <= 0:
        return 0.0
    return math.tanh(x / scale)


def share(part: float, whole: float) -> float:
    return part / whole if whole else 0.0


def chunks(seq: Sequence, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def table(rows: list[list], headers: list[str], align: str | None = None) -> str:
    """Простая текстовая таблица: align — строка из 'l'/'r' по колонкам."""
    cells = [[str(h) for h in headers]] + [["" if c is None else str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    align = align or "l" * len(headers)
    out = []
    for n, r in enumerate(cells):
        parts = [(c.rjust(w) if align[i] == "r" else c.ljust(w)) for i, (c, w) in enumerate(zip(r, widths))]
        out.append("  ".join(parts).rstrip())
        if n == 0:
            out.append("  ".join("─" * w for w in widths))
    return "\n".join(out)
