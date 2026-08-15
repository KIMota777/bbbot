# -*- coding: utf-8 -*-
"""Движок исполнения — попытка опровергнуть вывод через ИСПОЛНЕНИЕ, а не сигнал.

Гипотеза направления: сигналы не безнадёжны, их убивает способ входа и выхода.
Автор везде входил маркетом по открытию следующего бара и держал ATR-стоп.
Здесь тот же самый набор сигналов (ни одна строчка стратегий не тронута)
прогоняется через другие способы исполнения:

  * лимитный вход на откат к закрытию сигнального бара, мейкерская комиссия;
  * частичная фиксация половины на 1R, остаток под трейлом;
  * безубыток после 1R;
  * выход по времени вместо стопа (с пересчётом размера под другой стоп);
  * сдвиг исполнения на 1-2 бара вперёд — проверка, что сигнал вообще живёт.

ЧЕСТНОСТЬ ЛИМИТНОГО ВХОДА. Заявка считается исполненной, только если цена
прошла за уровень УВЕРЕННО — на limit_pen (по умолчанию 0.05%) глубже. Это
грубая замена очереди в стакане. И это ВСЁ РАВНО ОПТИМИСТИЧНО: настоящая
очередь на 4-часовом баре может не дойти и при более глубоком проколе, а
заявка на откат систематически не исполняется именно там, где цена уехала без
нас — то есть в лучших сделках. Отбор сделок лимитом смещён в пользу тех, где
цена вернулась, а значит в пользу возвратных движений. Ни один результат
отсюда нельзя считать достижимым живьём без проверки на стакане.

Проверка на тождество: при entry="market", delay=0 и выключенных надстройках
этот движок обязан давать ТУ ЖЕ сделку в ту же цену, что engine.run. Тест —
refute_exec_parity().
"""
import numpy as np

import engine
import rdata

FLAT, LONG, SHORT = 0, 1, -1


class XCfg:
    """Правила исполнения. Отдельно от Cfg (риск) и от стратегии (направление)."""

    __slots__ = ("entry", "delay", "limit_life", "limit_pen", "maker_fee",
                 "partial_R", "partial_frac", "be_R", "trail_after_partial",
                 "time_bars", "stop_mult", "trail_always", "limit_off")

    def __init__(self, entry="market", delay=0, limit_life=3,
                 limit_pen=0.0005, maker_fee=rdata.MAKER_FEE,
                 partial_R=0.0, partial_frac=0.5, be_R=0.0,
                 trail_after_partial=0.0, time_bars=0, stop_mult=1.0,
                 trail_always=0.0, limit_off=0.0):
        # Насколько ЛУЧШЕ закрытия сигнального бара стоит заявка, в долях
        # ширины стопа. Ноль означает «на самом закрытии» — а это на непрерывном
        # рынке почти всегда уже маркетабельно: открытие следующего бара
        # совпадает с закрытием предыдущего, и заявка исполняется тейкером по
        # рынку. Без ненулевого отступа никакого «лимитного входа» нет вовсе,
        # есть переименованный маркет. Проверено: при limit_off=0 залив 100%,
        # мейкерских исполнений 0, числа совпадают с маркетом до знака.
        self.limit_off = limit_off
        self.entry = entry                       # "market" | "limit"
        self.delay = delay                       # доп. баров задержки
        self.limit_life = limit_life             # сколько баров живёт заявка
        self.limit_pen = limit_pen               # насколько глубже надо пройти
        self.maker_fee = maker_fee
        self.partial_R = partial_R               # частичная фиксация на N R
        self.partial_frac = partial_frac
        self.be_R = be_R                         # безубыток после N R
        self.trail_after_partial = trail_after_partial   # в долях стопа
        self.time_bars = time_bars               # выход по времени, баров
        self.stop_mult = stop_mult               # множитель ширины стопа
        self.trail_always = trail_always         # трейл сразу, в долях стопа

    def tag(self):
        p = ["%s" % self.entry]
        if self.delay:
            p.append("delay%d" % self.delay)
        if self.entry == "limit":
            p.append("off%.2fR/life%d/pen%.2f%%"
                     % (self.limit_off, self.limit_life,
                        100 * self.limit_pen))
        if self.partial_R:
            p.append("part%.0f%%@%.1fR" % (100 * self.partial_frac,
                                           self.partial_R))
        if self.be_R:
            p.append("be@%.1fR" % self.be_R)
        if self.trail_after_partial:
            p.append("trail%.1fS" % self.trail_after_partial)
        if self.trail_always:
            p.append("TRAIL%.1fS" % self.trail_always)
        if self.time_bars:
            p.append("time%d" % self.time_bars)
        if self.stop_mult != 1.0:
            p.append("stopx%.1f" % self.stop_mult)
        return " ".join(p)


def run(bars, sig, cfg, x, start_i=0, symbol=None):
    """Прогон с настраиваемым исполнением. Причинность та же, что в engine.run.

    Сигнал бара k превращается в приказ, который может быть исполнен НЕ РАНЬШЕ
    бара k+1+delay. Стратегия физически не может войти по цене своего сигнала.
    """
    sym = symbol or bars.symbol
    slip = rdata.SLIP_BPS.get(sym, 0.0005) * cfg.slip_mult
    maint = rdata.MAINT_MARGIN.get(sym, 0.005)
    o, h, l, c, t = bars.o, bars.h, bars.l, bars.c, bars.t
    n = len(t)
    bar_ms = bars.bar_ms()

    equity = cfg.start_equity
    peak = equity
    cash = equity
    trades = []
    eq_i, eq_v = [], []
    pos = None
    order = None            # dict(side, k, first_i, last_i, level)
    pending_exit = False
    halted = False
    n_placed = 0
    n_filled = 0
    n_maker = 0

    ent = sig.entry
    # прыжок через плоский участок: пока нет позиции и нет живой заявки,
    # капитал не меняется и промежуточные бары не несут информации.
    # Заявка ставится только на баре сигнала, поэтому прыгать можно ровно до
    # следующего сигнального бара. Это только скорость — результат тождественен.
    has_sig = np.flatnonzero(ent != 0)

    i = start_i
    while i < n:
        # --- (а) выход по сигналу прошлого бара --------------------------
        if pending_exit and pos is not None:
            ref = {}
            _close(pos, o[i] * (1 - pos["side"] * slip), i, int(t[i]),
                   "сигнал", trades, ref, cfg=cfg, sym=sym)
            cash += ref["realized"]
            equity = cash
            eq_i.append(i)
            eq_v.append(equity)
            pos = None
            pending_exit = False

        # --- (б) попытка исполнить заявку --------------------------------
        if order is not None and pos is None and not halted:
            if i > order["last_i"]:
                order = None                     # протухла, не исполнилась
            elif i >= order["first_i"]:
                fill = _try_fill(order, i, o, h, l, slip, x)
                if fill is not None:
                    px, taker = fill
                    k = order["k"]
                    side = order["side"]
                    stop_frac = float(sig.stop[k]) * x.stop_mult
                    if stop_frac > 0 and np.isfinite(stop_frac):
                        risk = cfg.risk_frac * float(sig.size_k[k])
                        if cfg.dd_scale:
                            dd_now = (peak - equity) / peak if peak > 0 else 0.
                            for thr, mult in cfg.dd_scale:
                                if dd_now >= thr:
                                    risk *= mult
                        qty = (equity * risk) / (px * stop_frac)
                        notional = qty * px
                        cap = equity * cfg.max_lev
                        if notional > cap:
                            qty = cap / px
                            notional = cap
                        if notional > 0:
                            fee_rate = cfg.fee if taker else x.maker_fee
                            fee = notional * fee_rate
                            lev = notional / equity
                            trail0 = (x.trail_always * stop_frac
                                      if x.trail_always else
                                      float(sig.trail[k]))
                            pos = {
                                "side": side, "qty": qty, "avg": px, "i_in": i,
                                "t_in": int(t[i]), "fees": fee, "funding": 0.0,
                                "sl": px * (1 - side * stop_frac),
                                "tp": px * (1 + side * float(sig.tp[k]))
                                      if sig.tp[k] > 0 else 0.0,
                                "trail": trail0,
                                "ext": px, "lev": lev,
                                "liq": _liq(side, px, max(lev, 1e-9), maint),
                                "mae": 0.0, "mfe": 0.0, "notional": notional,
                                "eq_before": equity, "eq_low": equity,
                                "R": stop_frac, "part_done": False,
                                "be_done": False, "qty0": qty,
                                "realized_part": 0.0,
                            }
                            cash -= fee
                            equity = cash
                            n_filled += 1
                            if not taker:
                                n_maker += 1
                    order = None

        # --- (в) жизнь позиции внутри бара -------------------------------
        if pos is not None:
            s = pos["side"]
            hit = None
            px_out = None
            if (s > 0 and o[i] <= pos["sl"]) or (s < 0 and o[i] >= pos["sl"]):
                if i > pos["i_in"]:
                    gapped_liq = (o[i] <= pos["liq"]) if s > 0 \
                        else (o[i] >= pos["liq"])
                    if gapped_liq:
                        hit, px_out = "ликвидация", pos["liq"]
                    else:
                        hit, px_out = "разрыв", o[i] * (1 - s * slip)
            if hit is None:
                liq_hit = (l[i] <= pos["liq"]) if s > 0 else (h[i] >= pos["liq"])
                sl_hit = (l[i] <= pos["sl"]) if s > 0 else (h[i] >= pos["sl"])
                tp_hit = (pos["tp"] > 0 and
                          ((h[i] >= pos["tp"]) if s > 0 else (l[i] <= pos["tp"])))
                sl_first = sl_hit and (not liq_hit or
                                       (pos["sl"] >= pos["liq"] if s > 0
                                        else pos["sl"] <= pos["liq"]))
                if sl_first:
                    hit, px_out = "стоп", pos["sl"] * (1 - s * slip)
                elif liq_hit:
                    hit, px_out = "ликвидация", pos["liq"]
                elif tp_hit:
                    hit, px_out = "тейк", pos["tp"] * (1 - s * slip)

            adverse = (l[i] - pos["avg"]) if s > 0 else (pos["avg"] - h[i])
            favor = (h[i] - pos["avg"]) if s > 0 else (pos["avg"] - l[i])
            pos["mae"] = min(pos["mae"], adverse / pos["avg"])
            pos["mfe"] = max(pos["mfe"], favor / pos["avg"])
            worst_eq = cash + adverse * pos["qty"] - pos["funding"]
            if worst_eq < pos["eq_low"]:
                pos["eq_low"] = worst_eq

            # частичная фиксация: только если стоп в этом баре НЕ сработал
            if hit is None and x.partial_R and not pos["part_done"]:
                tgt = pos["avg"] * (1 + s * x.partial_R * pos["R"])
                reached = (h[i] >= tgt) if s > 0 else (l[i] <= tgt)
                if reached:
                    q = pos["qty"] * x.partial_frac
                    px_p = tgt * (1 - s * slip)
                    fee_p = q * px_p * cfg.fee
                    gross = s * (px_p - pos["avg"]) * q
                    cash += gross - fee_p
                    pos["realized_part"] += gross - fee_p
                    pos["fees"] += fee_p
                    pos["qty"] -= q
                    pos["notional"] = pos["qty"] * pos["avg"]
                    pos["part_done"] = True
                    if x.trail_after_partial:
                        pos["trail"] = x.trail_after_partial * pos["R"]
                    equity = cash + s * (c[i] - pos["avg"]) * pos["qty"] \
                        - pos["funding"]

            if hit is None and x.time_bars and i - pos["i_in"] >= x.time_bars:
                hit, px_out = "время", c[i] * (1 - s * slip)
            if hit is None and cfg.max_bars and i - pos["i_in"] >= cfg.max_bars:
                hit, px_out = "время", c[i] * (1 - s * slip)

            if hit is not None:
                ref = {}
                _close(pos, px_out, i, int(t[i]) + bar_ms, hit, trades, ref,
                       liq=(hit == "ликвидация"), cfg=cfg, sym=sym)
                cash += ref["realized"]
                equity = cash
                eq_i.append(i)
                eq_v.append(equity)
                pos = None
                if equity <= 0:
                    return _finish(trades, eq_i, eq_v, cfg, bars, t, True,
                                   n_placed, n_filled, n_maker)
            else:
                # безубыток после be_R — по итогам бара, не внутри него
                if x.be_R and not pos["be_done"] and \
                        pos["mfe"] >= x.be_R * pos["R"]:
                    be = pos["avg"] * (1 + s * cfg.fee * 2)
                    pos["sl"] = max(pos["sl"], be) if s > 0 \
                        else min(pos["sl"], be)
                    pos["be_done"] = True
                if pos["trail"] > 0:
                    pos["ext"] = max(pos["ext"], c[i]) if s > 0 \
                        else min(pos["ext"], c[i])
                    new_sl = pos["ext"] * (1 - s * pos["trail"])
                    pos["sl"] = max(pos["sl"], new_sl) if s > 0 \
                        else min(pos["sl"], new_sl)
                unreal = s * (c[i] - pos["avg"]) * pos["qty"]
                equity = cash + unreal - pos["funding"]
                eq_i.append(i)
                eq_v.append(equity)

        if equity > peak:
            peak = equity
        if cfg.dd_stop and peak > 0 and (peak - equity) / peak >= cfg.dd_stop:
            halted = True

        # --- (г) сигнал текущего бара -> заявка --------------------------
        if i + 1 < n:
            if pos is not None:
                side_exit = (sig.exit_long[i] if pos["side"] > 0
                             else sig.exit_short[i])
                pending_exit = bool(sig.exit[i]) or bool(side_exit) or \
                    (ent[i] != 0 and ent[i] != pos["side"])
                if ent[i] != 0 and ent[i] != pos["side"] and \
                        _allowed(cfg, ent[i]):
                    order = _place(x, int(ent[i]), i, c[i], n,
                                   float(sig.stop[i]) * x.stop_mult)
                    n_placed += 1
            elif not halted and ent[i] != 0 and _allowed(cfg, ent[i]):
                order = _place(x, int(ent[i]), i, c[i], n,
                               float(sig.stop[i]) * x.stop_mult)
                n_placed += 1

        if pos is None and order is None and not pending_exit:
            j = int(np.searchsorted(has_sig, i, "right"))
            if j >= len(has_sig):
                break
            i = int(has_sig[j])
            continue
        i += 1

    if pos is not None:
        ref = {}
        _close(pos, c[n - 1] * (1 - pos["side"] * slip), n - 1,
               int(t[n - 1]) + bar_ms, "конец", trades, ref, cfg=cfg, sym=sym)
        cash += ref["realized"]
        equity = cash
        eq_i.append(n - 1)
        eq_v.append(equity)
    return _finish(trades, eq_i, eq_v, cfg, bars, t, equity <= 0,
                   n_placed, n_filled, n_maker)


def _place(x, side, k, level, n, stop_frac=0.0):
    """Заявка по сигналу бара k. Для лимита уровень отодвигается В НАШУ ПОЛЬЗУ.

    Отступ задан в долях ширины стопа, а не в процентах цены: иначе один и тот
    же отступ означал бы разное для BTC и DOGE и для спокойного и бурного
    рынка. 0.25R — это «подожду четверть риска отката».
    """
    first = k + 1 + x.delay
    if first >= n:
        return None
    lv = float(level)
    if x.entry == "limit":
        last = min(first + x.limit_life - 1, n - 1)
        if x.limit_off and stop_frac > 0 and np.isfinite(stop_frac):
            lv *= (1 - side * x.limit_off * stop_frac)
    else:
        last = first
    return dict(side=side, k=k, first_i=first, last_i=last, level=lv)


def _try_fill(order, i, o, h, l, slip, x):
    """(цена, тейкер?) или None. Лимит исполняется только при уверенном проколе."""
    s = order["side"]
    if x.entry != "limit":
        return (o[i] * (1 + s * slip), True)
    L = order["level"]
    if s > 0:
        if o[i] <= L:                       # заявка уже маркетабельна
            return (o[i] * (1 + slip), True)
        if l[i] <= L * (1 - x.limit_pen):   # цена прошла УВЕРЕННО за уровень
            return (L, False)
    else:
        if o[i] >= L:
            return (o[i] * (1 - slip), True)
        if h[i] >= L * (1 + x.limit_pen):
            return (L, False)
    return None


def _liq(side, avg, lev, maint):
    room = 1.0 / lev - maint
    if room <= 0:
        room = 1e-6
    return avg * (1 - room) if side > 0 else avg * (1 + room)


def _allowed(cfg, side):
    return (side > 0 and cfg.allow_long) or (side < 0 and cfg.allow_short)


def _close(pos, px_out, i, t_out, reason, trades, ref, liq=False, cfg=None,
           sym=None):
    s = pos["side"]
    qty = pos["qty"]
    fee_out = qty * px_out * (cfg.fee if cfg else rdata.TAKER_FEE)
    fund = 0.0
    if cfg is None or cfg.funding:
        fund = rdata.funding_paid(sym or "BTCUSDT", pos["t_in"], t_out,
                                  pos["notional"], s)
    gross = s * (px_out - pos["avg"]) * qty
    if liq:
        gross = -pos["notional"] / max(pos["lev"], 1e-9)
        fee_out = 0.0
    # частично зафиксированное уже лежит в cash; в pnl сделки его учитываем,
    # но из ref["realized"] вычитаем, чтобы не начислить дважды
    part = pos.get("realized_part", 0.0)
    pnl = gross - fee_out - pos["fees"] - fund + part
    ref["realized"] = pnl + pos["fees"] - part
    trades.append(engine.Trade(
        side=s, i_in=pos["i_in"], i_out=i, t_in=pos["t_in"], t_out=t_out,
        px_in=pos["avg"], px_out=px_out, qty=qty, notional=pos["notional"],
        pnl=pnl, fees=pos["fees"] + fee_out, funding=fund, reason=reason,
        mae=pos["mae"], mfe=pos["mfe"], equity_after=0.0,
        eq_low=min(pos.get("eq_low", pos["eq_before"]), pos["eq_before"] + pnl)
        / max(pos["eq_before"], 1e-9)))


def _finish(trades, eq_i, eq_v, cfg, bars, t, ruined, n_placed=0, n_filled=0,
            n_maker=0):
    eq_i = [0] + list(eq_i)
    eq_v = [cfg.start_equity] + list(eq_v)
    eq_t = t[np.array(eq_i, dtype=np.int64)] if eq_i else np.array([], np.int64)
    eq = np.array(eq_v, dtype=np.float64) if eq_v else np.array([], np.float64)
    final = float(eq[-1]) if len(eq) else cfg.start_equity
    run_eq = cfg.start_equity
    for tr in trades:
        run_eq += tr.pnl
        tr.equity_after = run_eq
    r = engine.Result(trades=trades, eq_t=eq_t, eq_v=eq, bars=len(t),
                      start_equity=cfg.start_equity, final_equity=final,
                      ruined=bool(ruined or final <= 0), symbol=bars.symbol,
                      tf=bars.tf)
    r.note = "placed=%d filled=%d maker=%d" % (n_placed, n_filled, n_maker)
    return r
