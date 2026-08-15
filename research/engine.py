# -*- coding: utf-8 -*-
"""Движок бэктеста, написанный заново под требование «сломать стратегию».

Прежний движок (evolution2.run5) остаётся нетронутым — он обслуживает боевых
ботов. Этот отдельный: его задача не воспроизвести прежние числа, а не дать
себя обмануть.

ЧЕМ ОН ОТЛИЧАЕТСЯ ОТ ПРЕЖНЕГО, ПО ПУНКТАМ

1. СИГНАЛ БАРА i ИСПОЛНЯЕТСЯ НА ОТКРЫТИИ БАРА i+1. Всегда, без исключений, и
   это свойство самого движка, а не дисциплины стратегии: массив сигналов
   сдвигается внутри run(). Стратегия физически не может войти по цене, из
   которой сама же и получила сигнал.

2. ВНУТРИ БАРА ВЫИГРЫВАЕТ ХУДШЕЕ. Если в одном баре достигнуты и стоп, и
   тейк, засчитывается стоп. Порядок внутри бара по OHLC неизвестен, и любое
   иное правило — это выбор выгодного исхода задним числом. Разрыв через стоп
   исполняется по открытию, а не по цене стопа: цены стопа в этот момент на
   рынке не было.

3. ЦЕНА ИСПОЛНЕНИЯ ХУЖЕ ЖЕЛАЕМОЙ. Проскальзывание сносит цену против нас на
   входе и на выходе; комиссия тейкера берётся с оборота на обеих ногах.

4. ФАНДИНГ ПЛАТИТСЯ ПО ФАКТИЧЕСКОЙ ИСТОРИИ СТАВОК, в моменты расчёта,
   попавшие внутрь удержания. Для позиций, живущих сутками, это не мелочь:
   при ставке 0.01% на 8 часов набегает ~11% годовых от объёма позиции.

5. РАЗМЕР ОТ РИСКА, А НЕ ОТ ПЛЕЧА. Объём считается от доли капитала, которой
   готовы рискнуть, и расстояния до стопа. Плечо — следствие и ограничение
   сверху, а не параметр доходности.

6. ПРОСАДКА СЧИТАЕТСЯ ПЛАВАЮЩАЯ. Капитал переоценивается на каждом баре
   удержания. Просадка по закрытым сделкам занижает риск ровно там, где он
   опаснее всего: у стратегии, которая пересиживает минус.

7. ЛИКВИДАЦИЯ МОДЕЛИРУЕТСЯ. Цена ликвидации считается от поддерживающей
   маржи биржи и проверяется по экстремуму бара, а не по закрытию.

ЧЕГО ЭТОТ ДВИЖОК НЕ УМЕЕТ (и поэтому его выводы ограничены)
  * Частичных исполнений и стакана нет: позиция входит целиком по одной цене.
  * Задержка исполнения моделируется грубо — сдвигом на бар и слippage'ем.
  * Лимитные входы считаются исполненными, если цена коснулась уровня. Это
    оптимистично: в реальности очередь могла не дойти. Поэтому лимитные входы
    помечаются в результате флагом optimistic_fill.
"""
import numpy as np

import rdata

FLAT, LONG, SHORT = 0, 1, -1


class Cfg:
    """Правила исполнения и риска. Отдельно от стратегии — намеренно.

    Стратегия отвечает на вопрос «куда», Cfg — «сколько и почём». Их смешение
    и есть главный способ получить красивый бэктест: тогда доходность можно
    поднять плечом, а выглядеть это будет как улучшение сигнала.
    """

    __slots__ = ("risk_frac", "max_lev", "fee", "slip_mult", "funding",
                 "maint", "start_equity", "max_bars", "allow_long",
                 "allow_short", "one_position", "dd_stop", "dd_scale")

    def __init__(self, risk_frac=0.01, max_lev=10.0, fee=rdata.TAKER_FEE,
                 slip_mult=1.0, funding=True, start_equity=10000.0,
                 max_bars=0, allow_long=True, allow_short=True,
                 dd_stop=0.0, dd_scale=None):
        self.risk_frac = risk_frac
        self.max_lev = max_lev
        self.fee = fee
        self.slip_mult = slip_mult
        self.funding = funding
        self.start_equity = start_equity
        self.max_bars = max_bars
        self.allow_long = allow_long
        self.allow_short = allow_short
        self.one_position = True
        self.dd_stop = dd_stop          # просадка, на которой торговля встаёт
        self.dd_scale = dd_scale        # [(порог, множитель риска), ...]


class Signals:
    """Что стратегия говорит про КАЖДЫЙ бар, зная только бары до него.

    entry[i]  : -1/0/+1 — желаемое направление, исполняется на открытии i+1
    exit[i]   : True — закрыть позицию на открытии i+1 (сигнал развернулся)
    stop[i]   : расстояние до стопа в долях цены на момент входа (>0)
    tp[i]     : расстояние до тейка в долях цены; 0 — тейка нет
    trail[i]  : ширина трейла в долях цены; 0 — трейла нет
    size_k[i] : множитель размера (для volatility targeting); 1.0 — обычный
    """

    __slots__ = ("entry", "exit", "stop", "tp", "trail", "size_k", "meta")

    def __init__(self, n, meta=None):
        self.entry = np.zeros(n, dtype=np.int8)
        self.exit = np.zeros(n, dtype=bool)
        self.stop = np.full(n, 0.02)
        self.tp = np.zeros(n)
        self.trail = np.zeros(n)
        self.size_k = np.ones(n)
        self.meta = meta or {}


class Trade:
    __slots__ = ("side", "i_in", "i_out", "t_in", "t_out", "px_in", "px_out",
                 "qty", "notional", "pnl", "fees", "funding", "reason",
                 "mae", "mfe", "equity_after", "eq_low")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


class Result:
    __slots__ = ("trades", "eq_t", "eq_v", "start_equity", "final_equity",
                 "ruined", "bars", "symbol", "tf", "optimistic_fill", "note")

    def __init__(self, **kw):
        self.optimistic_fill = False
        self.note = ""
        for k, v in kw.items():
            setattr(self, k, v)


def _liq_price(side, avg, lev, maint):
    """Цена ликвидации изолированной позиции.

    Позиция гибнет, когда собственные средства под неё падают до
    поддерживающей маржи. При плече L собственные средства — 1/L от объёма,
    поэтому запас хода до ликвидации равен (1/L - maint) в долях цены.
    Комиссия закрытия сюда не заложена — биржа закрывает раньше, так что
    оценка чуть оптимистична по цене и пессимистична по частоте.
    """
    room = 1.0 / lev - maint
    if room <= 0:
        room = 1e-6
    return avg * (1 - room) if side > 0 else avg * (1 + room)


def run(bars, sig, cfg, start_i=0, symbol=None):
    """Прогон. Возвращает Result со сделками и кривой капитала по барам.

    Устройство одного шага цикла — порядок важен и он не произвольный:
      (а) исполняем приказ, выданный на ПРОШЛОМ баре, по открытию текущего;
      (б) если в позиции — проверяем бар на стоп/тейк/ликвидацию;
      (в) переоцениваем капитал по закрытию бара;
      (г) читаем сигнал текущего бара — он станет приказом на следующий.
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
    eq_i, eq_v = [], []                 # разреженная кривая: бары с позицией
    pos = None                          # активная позиция
    pending = 0                         # приказ на следующий бар: -1/0/+1
    pending_exit = False
    halted = False

    ent = sig.entry
    # Индексы баров, где есть входной сигнал — чтобы не перебирать пустоту.
    # Пока позиции нет, капитал не меняется, и промежуточные бары не несут
    # информации. Это только скорость: результат тождественен полному циклу.
    has_sig = np.flatnonzero(ent != 0)

    i = start_i
    while i < n:
        # (а) исполнение приказа с прошлого бара -------------------------
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
        if pending != 0 and pos is None and not halted:
            side = pending
            k = i - 1                    # бар, породивший приказ
            stop_frac = float(sig.stop[k])
            if stop_frac <= 0 or not np.isfinite(stop_frac):
                pending = 0
            else:
                risk = cfg.risk_frac * float(sig.size_k[k])
                if cfg.dd_scale:
                    dd_now = (peak - equity) / peak if peak > 0 else 0.0
                    for thr, mult in cfg.dd_scale:
                        if dd_now >= thr:
                            risk *= mult
                px = o[i] * (1 + side * slip)
                qty = (equity * risk) / (px * stop_frac)
                notional = qty * px
                cap = equity * cfg.max_lev
                if notional > cap:                    # плечо — потолок
                    qty = cap / px
                    notional = cap
                if notional > 0:
                    fee = notional * cfg.fee
                    lev = notional / equity
                    pos = {
                        "side": side, "qty": qty, "avg": px, "i_in": i,
                        "t_in": int(t[i]), "fees": fee, "funding": 0.0,
                        "sl": px * (1 - side * stop_frac),
                        "tp": px * (1 + side * float(sig.tp[k]))
                              if sig.tp[k] > 0 else 0.0,
                        "trail": float(sig.trail[k]),
                        "ext": px, "lev": lev,
                        "liq": _liq_price(side, px, max(lev, 1e-9), maint),
                        "mae": 0.0, "mfe": 0.0, "notional": notional,
                        "eq_before": equity, "eq_low": equity,
                    }
                    cash -= fee
                    equity = cash
                pending = 0

        # (б) жизнь позиции внутри бара ----------------------------------
        if pos is not None:
            s = pos["side"]
            hit = None
            px_out = None
            # разрыв через стоп: цены стопа на рынке не было
            if (s > 0 and o[i] <= pos["sl"]) or (s < 0 and o[i] >= pos["sl"]):
                if i > pos["i_in"]:
                    # Если разрыв перепрыгнул и цену ликвидации, позиции уже
                    # не существует: биржа закрыла её по своей цене, и потерять
                    # больше маржи нельзя. Без этой ветки бэктест списывал бы
                    # с капитала весь ход разрыва, помноженный на плечо, —
                    # убыток крупнее внесённых денег, чего в жизни не бывает.
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
                # Стоп и ликвидация лежат по одну сторону от входа, и первым
                # срабатывает БЛИЖНИЙ: цена не может дойти до дальнего уровня,
                # не пройдя ближний. Считать ликвидацию раньше сработавшего
                # стопа — значит списывать всю маржу там, где биржа уже
                # закрыла бы позицию по стопу.
                sl_first = sl_hit and (not liq_hit or
                                       (pos["sl"] >= pos["liq"] if s > 0
                                        else pos["sl"] <= pos["liq"]))
                if sl_first:                     # стоп раньше тейка — всегда
                    hit, px_out = "стоп", pos["sl"] * (1 - s * slip)
                elif liq_hit:
                    hit, px_out = "ликвидация", pos["liq"]
                elif tp_hit:
                    hit, px_out = "тейк", pos["tp"] * (1 - s * slip)
            # худший и лучший ход внутри бара — для анализа выходов
            adverse = (l[i] - pos["avg"]) if s > 0 else (pos["avg"] - h[i])
            favor = (h[i] - pos["avg"]) if s > 0 else (pos["avg"] - l[i])
            pos["mae"] = min(pos["mae"], adverse / pos["avg"])
            pos["mfe"] = max(pos["mfe"], favor / pos["avg"])
            # худший плавающий капитал за время удержания — по экстремуму бара,
            # а не по закрытию: просадка случается внутри бара, и мерить её по
            # закрытиям значит не увидеть именно те ямы, из-за которых
            # закрывают счёт
            worst_eq = cash + adverse * pos["qty"] - pos["funding"]
            if worst_eq < pos["eq_low"]:
                pos["eq_low"] = worst_eq
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
                    return _finish(trades, eq_i, eq_v, cfg, bars, t, True)
            else:
                # трейл двигаем по ЗАКРЫТИЮ бара: внутри бара мы бы его не
                # увидели, а по экстремуму — подтянули бы стоп к цене, которой
                # в этот момент ещё не было.
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

        # (г) сигнал текущего бара -> приказ на следующий -----------------
        if i + 1 < n:
            if pos is not None:
                pending_exit = bool(sig.exit[i]) or \
                    (ent[i] != 0 and ent[i] != pos["side"])
                if ent[i] != 0 and ent[i] != pos["side"] and _allowed(cfg, ent[i]):
                    pending = int(ent[i])       # разворот: закрыть и открыть
            elif not halted and ent[i] != 0 and _allowed(cfg, ent[i]):
                pending = int(ent[i])

        # прыжок через плоский участок ------------------------------------
        if pos is None and pending == 0 and not pending_exit:
            k = int(np.searchsorted(has_sig, i, "right"))
            if k >= len(has_sig):
                break
            i = int(has_sig[k])
            continue
        i += 1

    if pos is not None:                  # незакрытая позиция — по последней цене
        ref = {}
        _close(pos, c[n - 1] * (1 - pos["side"] * slip), n - 1,
               int(t[n - 1]) + bar_ms, "конец", trades, ref, cfg=cfg, sym=sym)
        cash += ref["realized"]
        equity = cash
        eq_i.append(n - 1)
        eq_v.append(equity)
    return _finish(trades, eq_i, eq_v, cfg, bars, t, equity <= 0)


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
        # при ликвидации теряется вся маржа под позицию, не больше и не меньше
        gross = -pos["notional"] / max(pos["lev"], 1e-9)
        fee_out = 0.0
    pnl = gross - fee_out - pos["fees"] - fund
    ref["realized"] = pnl + pos["fees"]      # комиссия входа уже списана
    trades.append(Trade(
        side=s, i_in=pos["i_in"], i_out=i, t_in=pos["t_in"], t_out=t_out,
        px_in=pos["avg"], px_out=px_out, qty=qty, notional=pos["notional"],
        pnl=pnl, fees=pos["fees"] + fee_out, funding=fund, reason=reason,
        mae=pos["mae"], mfe=pos["mfe"], equity_after=0.0,
        eq_low=min(pos.get("eq_low", pos["eq_before"]), pos["eq_before"] + pnl)
        / max(pos["eq_before"], 1e-9)))


def _finish(trades, eq_i, eq_v, cfg, bars, t, ruined):
    # первая точка — стартовый капитал: без неё кривая начинается с первой
    # сделки, и период до неё выпадает из расчёта доходности и просадки
    eq_i = [0] + list(eq_i)
    eq_v = [cfg.start_equity] + list(eq_v)
    eq_t = t[np.array(eq_i, dtype=np.int64)] if eq_i else np.array([], np.int64)
    eq = np.array(eq_v, dtype=np.float64) if eq_v else np.array([], np.float64)
    final = float(eq[-1]) if len(eq) else cfg.start_equity
    run_eq = cfg.start_equity
    for tr in trades:                        # капитал после каждой сделки
        run_eq += tr.pnl
        tr.equity_after = run_eq
    return Result(trades=trades, eq_t=eq_t, eq_v=eq, bars=len(t),
                  start_equity=cfg.start_equity, final_equity=final,
                  ruined=bool(ruined or final <= 0), symbol=bars.symbol,
                  tf=bars.tf)


# --- механическая проверка причинности -------------------------------------

def assert_causal(build_signals, bars, cut=0.6, seed=0, tol=0.0):
    """Доказать, что сигнал не смотрит в будущее — не глазами, а опытом.

    Берём бары, портим ВСЁ после точки cut случайным шумом, пересчитываем
    сигналы и сравниваем с исходными на участке ДО точки. Если хоть одно
    значение сдвинулось — стратегия читает будущее. Так ловятся все виды
    утечки разом: центрированные окна, неверный shift, нормировка по всей
    выборке, подтягивание старшего таймфрейма без сдвига.

    Обзор кода такого не гарантирует: утечка чаще всего прячется в чужой
    функции, а не в той, что читаешь.
    """
    rng = np.random.default_rng(seed)
    k = int(len(bars.t) * cut)
    a = build_signals(bars)

    b2 = rdata.Bars.__new__(rdata.Bars)
    b2.symbol, b2.tf = bars.symbol, bars.tf
    b2.t = bars.t.copy()
    for f in ("o", "h", "l", "c", "v", "turnover"):
        setattr(b2, f, getattr(bars, f).copy())
    m = len(bars.t) - k
    shock = rng.uniform(0.5, 1.8, m)
    for f in ("o", "c"):
        getattr(b2, f)[k:] *= shock
    b2.h[k:] = np.maximum(b2.o[k:], b2.c[k:]) * (1 + rng.uniform(0, .02, m))
    b2.l[k:] = np.minimum(b2.o[k:], b2.c[k:]) * (1 - rng.uniform(0, .02, m))
    b2.v[k:] *= rng.uniform(0.2, 5.0, m)
    b2.turnover[k:] = b2.c[k:] * b2.v[k:]
    b = build_signals(b2)

    bad = []
    for name in ("entry", "exit", "stop", "tp", "trail", "size_k"):
        x = np.asarray(getattr(a, name), dtype=np.float64)[:k]
        y = np.asarray(getattr(b, name), dtype=np.float64)[:k]
        d = ~(np.isclose(x, y, rtol=1e-9, atol=1e-12, equal_nan=True))
        if d.sum() > tol * k:
            first = int(np.flatnonzero(d)[0])
            bad.append("%s: %d расхождений, первое на баре %d из %d"
                       % (name, int(d.sum()), first, k))
    if bad:
        raise AssertionError("ЗАГЛЯДЫВАНИЕ В БУДУЩЕЕ -> " + "; ".join(bad))
    return True
