"""Восстановление позиций: FIFO-лоты -> полные циклы (round trips) и реализованный PnL.

Цикл открывается первой покупкой при нулевой позиции и закрывается, когда
остаток падает до пыли (≤2% от максимума позиции). Несколько покупок внутри
цикла — набор позиции (DCA/пирамида), несколько продаж — частичный выход.

Честность учёта:
- продажа без покупки в истории (эйрдроп, перевод, история за окном выборки)
  не даёт PnL — эта часть считается «несопоставленной» и помечается;
- входящий перевод получает базу по рыночной цене на момент получения
  (иначе раздачи команде выглядят как 100% прибыль);
- исходящий перевод снимает лоты по себестоимости без PnL (кошелёк мог
  продать на CEX — результат неизвестен, поэтому ноль, а не выдумка).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

DUST_SHARE = 0.02
DUST_USD = 1.0


@dataclass
class Lot:
    qty: float
    cost: float
    ts: int
    known: bool = True


@dataclass
class Fill:
    ts: int
    sig: str
    idx: int
    side: str  # buy|sell|tin|tout
    qty: float
    usd: float | None
    price: float | None
    matched_qty: float = 0.0
    matched_cost: float = 0.0
    pnl: float | None = None
    hold: float | None = None  # средневзвешенное удержание проданной части, сек
    gain: float | None = None  # цена продажи к средней цене покупки - 1
    pos_before: float = 0.0  # позиция до операции (кол-во)


@dataclass
class RoundTrip:
    chain: str
    wallet: str
    token: str
    open_ts: int
    close_ts: int | None = None
    status: str = "open"
    fills: list[Fill] = field(default_factory=list)
    cost: float = 0.0  # сумма покупок с известной базой
    proceeds: float = 0.0  # выручка сопоставленных продаж
    matched_cost: float = 0.0  # себестоимость проданной части
    realized: float = 0.0
    qty: float = 0.0
    max_qty: float = 0.0
    open_cost: float = 0.0
    peak_cost: float = 0.0
    unmatched_qty: float = 0.0
    unmatched_usd: float = 0.0
    tin_qty: float = 0.0
    tout_qty: float = 0.0
    exit_kind: str | None = None
    _hold_num: float = 0.0
    _hold_den: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.token}:{self.open_ts}"

    @property
    def buys(self) -> list[Fill]:
        return [f for f in self.fills if f.side == "buy"]

    @property
    def sells(self) -> list[Fill]:
        return [f for f in self.fills if f.side == "sell"]

    @property
    def n_buys(self) -> int:
        return len(self.buys)

    @property
    def n_sells(self) -> int:
        return len(self.sells)

    @property
    def pnl_pct(self) -> float | None:
        return self.realized / self.matched_cost if self.matched_cost > 0 else None

    @property
    def hold_sec(self) -> float | None:
        return self._hold_num / self._hold_den if self._hold_den > 0 else None

    @property
    def duration(self) -> int | None:
        return (self.close_ts - self.open_ts) if self.close_ts else None

    @property
    def valid(self) -> bool:
        """Годится для статистики: закрыт продажами и в основном сопоставлен."""
        if self.status != "closed" or self.matched_cost <= 0:
            return False
        total = self.matched_cost + self.unmatched_usd
        return self.unmatched_usd <= 0.5 * total

    @property
    def build(self) -> str:
        """Как набиралась позиция: single | dca_down | pyramid | dca."""
        b = self.buys
        if len(b) <= 1:
            return "single"
        ups = downs = 0
        for prev, cur in zip(b, b[1:]):
            if not prev.price or not cur.price:
                continue
            if cur.price > prev.price * 1.03:
                ups += 1
            elif cur.price < prev.price * 0.97:
                downs += 1
        if downs > ups:
            return "dca_down"
        if ups > downs:
            return "pyramid"
        return "dca"

    def avg_entry(self) -> float | None:
        b = [f for f in self.buys if f.usd]
        q = sum(f.qty for f in b)
        return sum(f.usd for f in b) / q if q else None


class _TokenState:
    __slots__ = ("lots", "dust", "rt")

    def __init__(self):
        self.lots: deque[Lot] = deque()
        self.dust: deque[Lot] = deque()
        self.rt: RoundTrip | None = None

    def qty(self) -> float:
        return sum(l.qty for l in self.lots)

    def known_cost(self) -> float:
        return sum(l.cost for l in self.lots if l.known)


def _consume(lots: deque, qty: float, ts: int):
    """Снимает qty с лотов FIFO. -> (known_qty, known_cost, unknown_qty, hold_num, остаток)."""
    known_q = known_c = unknown_q = hold = 0.0
    rest = qty
    while rest > 1e-12 and lots:
        lot = lots[0]
        take = min(lot.qty, rest)
        frac = take / lot.qty if lot.qty > 0 else 1.0
        if lot.known:
            known_q += take
            known_c += lot.cost * frac
            hold += take * (ts - lot.ts)
        else:
            unknown_q += take
        lot.cost -= lot.cost * frac
        lot.qty -= take
        rest -= take
        if lot.qty <= 1e-12:
            lots.popleft()
    return known_q, known_c, unknown_q, hold, max(rest, 0.0)


def build_round_trips(chain: str, wallet: str, legs: list[dict]) -> tuple[list[RoundTrip], dict]:
    """legs — ноги одного кошелька (любые токены), уже оценённые в USD."""
    states: dict[str, _TokenState] = {}
    trips: list[RoundTrip] = []
    orphan = {"sell_usd": 0.0, "sell_qty_events": 0}
    for l in sorted(legs, key=lambda x: (x["ts"], x["sig"], x["idx"])):
        side, qty, usd = l["side"], float(l["qty"]), l.get("usd")
        if qty <= 0:
            continue
        st = states.setdefault(l["token"], _TokenState())
        price = usd / qty if usd else None
        fill = Fill(ts=l["ts"], sig=l["sig"], idx=l["idx"], side=side, qty=qty, usd=usd, price=price,
                    pos_before=st.qty())

        if side in ("buy", "tin"):
            if side == "tin" and usd is None and st.rt is None:
                # неоценённый приход без позиции — спам/эйрдроп мусора, игнорируем
                continue
            if st.rt is None:
                st.rt = RoundTrip(chain=chain, wallet=wallet, token=l["token"], open_ts=l["ts"])
                trips.append(st.rt)
                st.dust.clear()
            rt = st.rt
            known = usd is not None
            st.lots.append(Lot(qty, usd or 0.0, l["ts"], known))
            if side == "buy" and known:
                rt.cost += usd
            if side == "tin":
                rt.tin_qty += qty
            rt.qty = st.qty()
            rt.max_qty = max(rt.max_qty, rt.qty)
            rt.open_cost = st.known_cost()
            rt.peak_cost = max(rt.peak_cost, rt.open_cost)
            rt.fills.append(fill)
            continue

        # продажа или исходящий перевод
        rt = st.rt
        if rt is None:
            # продажа остатков-пыли или вне истории
            _consume(st.dust, qty, l["ts"])
            if side == "sell" and usd:
                orphan["sell_usd"] += usd
                orphan["sell_qty_events"] += 1
            continue
        kq, kc, uq, hold, rest = _consume(st.lots, qty, l["ts"])
        if rest > 0:
            _, _, uq2, _, rest = _consume(st.dust, rest, l["ts"])
            uq += uq2
        unmatched = uq + rest
        if side == "sell":
            proceeds = (usd * kq / qty) if (usd and qty) else 0.0
            pnl = proceeds - kc if usd else None
            fill.matched_qty, fill.matched_cost = kq, kc
            fill.pnl = pnl
            fill.hold = hold / kq if kq > 0 else None
            fill.gain = (proceeds / kc - 1) if kc > 0 and usd else None
            if usd:
                rt.proceeds += proceeds
                rt.matched_cost += kc
                rt.realized += pnl or 0.0
                rt._hold_num += hold
                rt._hold_den += kq
            rt.unmatched_qty += unmatched
            if usd and unmatched:
                rt.unmatched_usd += usd * unmatched / qty
        else:
            rt.tout_qty += qty
        rt.fills.append(fill)
        rt.qty = st.qty()
        rt.open_cost = st.known_cost()
        remaining_value = rt.qty * (price or (rt.open_cost / rt.qty if rt.qty else 0))
        if rt.qty <= rt.max_qty * DUST_SHARE or remaining_value < DUST_USD:
            rt.status = "closed"
            rt.close_ts = l["ts"]
            n_sells = rt.n_sells
            if n_sells == 0:
                rt.exit_kind = "transfer"
            elif rt.tout_qty > 0.5 * rt.max_qty:
                rt.exit_kind = "transfer"
            else:
                rt.exit_kind = "full" if n_sells == 1 else "partial"
            st.dust = st.lots
            st.lots = deque()
            st.rt = None
    return trips, orphan


def open_positions(trips: list[RoundTrip]) -> list[RoundTrip]:
    return [t for t in trips if t.status == "open" and t.qty > 0]
