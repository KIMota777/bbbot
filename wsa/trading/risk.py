"""Риск-менеджмент: размер позиции, лимиты, аварийная остановка.

Стоп-краны (состояние в таблице kv, переживает перезапуск):
- дневной убыток больше daily_loss_limit — новых входов до конца суток (UTC) нет;
- просадка от пика капитала больше max_drawdown_halt — полная остановка
  до ручного сброса (`wsa trade reset`);
- серия из max_consecutive_losses убытков подряд — пауза pause_hours_after_losses.
Сигналы умеют только открывать позиции в этих рамках; снять ограничения
может лишь человек командой.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..util import HOUR, now


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


class Risk:
    def __init__(self, db, cfg, mode: str):
        self.db = db
        self.c = cfg.trading
        self.mode = mode
        self.key = f"risk:{mode}"

    # ---------- состояние ----------

    def state(self) -> dict:
        st = self.db.kv_get(self.key, None) or {}
        st.setdefault("halted", False)
        st.setdefault("halt_reason", None)
        st.setdefault("paused_until", 0)
        st.setdefault("peak_equity", None)
        st.setdefault("consecutive_losses", 0)
        st.setdefault("day", _day(now()))
        st.setdefault("day_start_equity", None)
        st.setdefault("day_pnl", 0.0)
        if st["day"] != _day(now()):
            st["day"], st["day_pnl"], st["day_start_equity"] = _day(now()), 0.0, None
        return st

    def save(self, st: dict) -> None:
        self.db.kv_set(self.key, st)

    def reset(self) -> None:
        st = self.state()
        st.update(halted=False, halt_reason=None, paused_until=0, consecutive_losses=0, peak_equity=None)
        self.save(st)

    # ---------- проверки ----------

    def can_open(self, symbol: str, equity: float, open_positions: list[dict]) -> tuple[bool, str]:
        st = self.state()
        if st["day_start_equity"] is None:
            st["day_start_equity"] = equity
        if st["peak_equity"] is None or equity > st["peak_equity"]:
            st["peak_equity"] = equity
        self.save(st)
        if st["halted"]:
            return False, f"остановлено: {st['halt_reason']}"
        if st["paused_until"] > now():
            return False, f"пауза после серии убытков до {datetime.fromtimestamp(st['paused_until'], tz=timezone.utc):%H:%M} UTC"
        if st["day_start_equity"] and st["day_pnl"] <= -self.c.daily_loss_limit * st["day_start_equity"]:
            return False, "дневной лимит убытка исчерпан"
        if st["peak_equity"] and equity <= st["peak_equity"] * (1 - self.c.max_drawdown_halt):
            st.update(halted=True, halt_reason=f"просадка ≥ {self.c.max_drawdown_halt:.0%} от пика")
            self.save(st)
            return False, st["halt_reason"]
        if len(open_positions) >= self.c.max_open_positions:
            return False, f"уже {len(open_positions)} позиций (максимум {self.c.max_open_positions})"
        if any(p["symbol"] == symbol for p in open_positions):
            return False, "по этому тикеру уже есть позиция"
        exposure = sum(p["cost_usd"] or 0 for p in open_positions)
        if equity > 0 and exposure / equity >= self.c.max_total_exposure:
            return False, f"суммарная экспозиция {exposure / equity:.0%} ≥ {self.c.max_total_exposure:.0%}"
        last = self.db.one("SELECT MAX(COALESCE(closed_at, opened_at)) t FROM positions WHERE mode=? AND symbol=?",
                           (self.mode, symbol))
        if last and last["t"] and now() - last["t"] < self.c.cooldown_hours * HOUR:
            return False, f"пауза по тикеру {self.c.cooldown_hours} ч после прошлой сделки"
        return True, "ok"

    def size_usd(self, equity: float, sl_pct: float, open_positions: list[dict]) -> float:
        """Размер: риск на сделку / расстояние до стопа, но не больше доли капитала."""
        sl = abs(sl_pct) or 0.1
        by_risk = equity * self.c.risk_per_trade / sl
        cap = equity * self.c.max_position_pct
        exposure = sum(p["cost_usd"] or 0 for p in open_positions)
        room = max(0.0, equity * self.c.max_total_exposure - exposure)
        return max(0.0, min(by_risk, cap, room))

    # ---------- события ----------

    def on_close(self, pnl_usd: float, equity: float) -> None:
        st = self.state()
        st["day_pnl"] = st.get("day_pnl", 0.0) + pnl_usd
        if pnl_usd < 0:
            st["consecutive_losses"] = st.get("consecutive_losses", 0) + 1
            if st["consecutive_losses"] >= self.c.max_consecutive_losses:
                st["paused_until"] = now() + int(self.c.pause_hours_after_losses * HOUR)
                st["consecutive_losses"] = 0
        else:
            st["consecutive_losses"] = 0
        if st["peak_equity"] is None or equity > st["peak_equity"]:
            st["peak_equity"] = equity
        self.save(st)
