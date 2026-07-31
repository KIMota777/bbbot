# -*- coding: utf-8 -*-
"""Торговый бот Bybit: RSI + сетка + уровни ликвидности. Мультимонетный.

Запуск (один процесс = одна монета):
    python bot_rsi.py DOGE          # обычный режим x5
    python bot_rsi.py ETH turbo     # турбо x20 (если разрешён для монеты)

Режимы:
  normal (x5): стоп "обманный" — за границей диапазона (за зоной сбора
    стопов), параметры в процентах, свои для каждой монеты (config.SYMBOL_PARAMS).
  turbo (x20): вход только на экстремумах (глубокий RSI + крайние 15%
    диапазона), стоп короткий от средней цены в долях ATR, фильтр
    "падающего ножа", кулдаун после стопа.

Kill switch: MAX_CONSEC_LOSSES убыточных циклов подряд -> пауза KILL_PAUSE_HOURS.

В DRY_RUN бот ведёт виртуальную позицию по тем же правилам и пишет лог.
"""

import logging
import sys
import time

from pybit.unified_trading import HTTP

import config
import gridlib
import patterns
import smc


def calc_rsi(closes, period):
    if len(closes) < period + 1:
        return None
    gain = loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gain += max(d, 0)
        loss += max(-d, 0)
    ag, al = gain / period, loss / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def calc_atr_pct(candles, n):
    """ATR за n свечей как доля цены. candles: [(ts,o,h,l,c), ...]"""
    if len(candles) < n + 2:
        return None
    trs = []
    prev_c = candles[0][4]
    for _, o, h, l, c in candles[1:]:
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
        prev_c = c
    atr = sum(trs[-n:]) / n
    return atr / candles[-1][4]


class RsiGridBot:
    def __init__(self, symbol, mode):
        self.symbol = symbol
        self.mode = mode          # "normal" | "turbo" | "bear" | "final" | ...
        self.turbo = mode == "turbo"
        sp = config.SYMBOL_PARAMS[symbol]
        self.p = sp.get(mode)
        if self.p is None:
            raise SystemExit(f"Режим {mode} для {symbol} недоступен "
                             f"(запрещён или не настроен в config).")
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(f"bot_{symbol}_{mode}.log", encoding="utf-8"),
            ],
        )
        self.log = logging.getLogger(f"{symbol}-{mode}")

        self.session = HTTP(testnet=config.TESTNET,
                            api_key=config.API_KEY, api_secret=config.API_SECRET)
        info = self.session.get_instruments_info(
            category="linear", symbol=symbol)["result"]["list"][0]
        f = info["lotSizeFilter"]
        self.qty_step = float(f["qtyStep"])
        self.min_qty = float(f["minOrderQty"])
        tick = info["priceFilter"]["tickSize"]
        self.price_dec = len(tick.split(".")[1]) if "." in tick else 0

        # таймфрейм — атрибут КОНКРЕТНОГО бота, не глобальная константа: в
        # портфеле могут одновременно жить боты на 15m и на 4ч (Bybit: "240").
        # ATR "суток" считается от РЕАЛЬНОГО таймфрейма бота, иначе на 4ч
        # 96 баров были бы 16 днями вместо суток — фильтр ножа и паттерны
        # сравнивали бы движение с ложным окном волатильности.
        self.interval = str(self.p.get("interval", config.INTERVAL))
        self.interval_ms = int(self.interval) * 60 * 1000
        self.atr_period = self.p.get("atr_period", max(4, 1440 // int(self.interval)))

        levels = self.p.get("levels", config.GRID_LEVELS)
        mult = self.p.get("mult", config.GRID_MULT)
        w = [mult ** k for k in range(levels)]
        self.leg_weights = [x / sum(w) for x in w]
        self.levels = levels
        # реинвест: виртуальный капитал в DRY_RUN (в реале — кошелёк биржи)
        self.virtual_balance = config.START_BALANCE
        self._equity_cache = (0.0, None)  # (моно-время, значение)
        self.window = self.p.get("window", config.RANGE_WINDOW)
        self.max_bars = self.p.get("max_bars", config.MAX_BARS)
        self.knife = self.p.get("knife", config.KNIFE_K if self.turbo else 0.0)
        self.cooldown_bars = self.p.get(
            "cooldown", config.TURBO_COOLDOWN if self.turbo else 0)
        self.rsi_period = self.p.get("rsi_period", config.RSI_PERIOD)
        self.zone_l = self.p.get("zone_l", self.p.get("zone", config.ZONE))
        self.zone_s = self.p.get("zone_s", self.p.get("zone", config.ZONE))
        self.be_move = bool(self.p.get("be_move", 0)) and not self.turbo
        # внешние фильтры (отсутствие ключа = фильтр выключен)
        self.fund_long_max = self.p.get("fund_long_max", 999)
        self.fund_short_min = self.p.get("fund_short_min", -999)
        self.oi_gate = self.p.get("oi_gate", 0)
        self.spx_long_min = self.p.get("spx_long_min", -999)
        self.dxy_long_max = self.p.get("dxy_long_max", 999)
        self.gold_long_max = self.p.get("gold_long_max", 999)
        self.aroon_n = self.p.get("aroon_n", 0)
        self.aroon_long_min = self.p.get("aroon_long_min", 0)
        self.aroon_short_min = self.p.get("aroon_short_min", 0)
        self.ema_mode = self.p.get("ema_mode", 0)
        self.ema_n = self.p.get("ema_n", 200)
        self.ma_mode = self.p.get("ma_mode", 0)
        self.masf = self.p.get("masf", 20)
        self.masl = self.p.get("masl", 200)
        # Smart Money Concepts: 0=выкл, 1=требовать совпадение,
        # 2=запрещать только явное противоречие (как pattern_gate)
        self.ob_gate = self.p.get("ob_gate", 0)
        self.fvg_gate = self.p.get("fvg_gate", 0)
        self.structure_mode = self.p.get("structure_mode", 0)  # 1=по структуре,2=против
        # --- подвижная сетка и подвижные SL/TP (волна v10) ---
        # Значения по умолчанию — gridlib.OFF10, то есть прежнее поведение.
        # Формулы берутся из gridlib, общего с бэктест-движком: если бот
        # посчитает сетку иначе, чем evolution2.run5, то отбор проверял одну
        # стратегию, а деньгами торговала бы другая.
        self.grid_mode = self.p.get("grid_mode", gridlib.OFF10["grid_mode"])
        self.grid_span = self.p.get("grid_span", gridlib.OFF10["grid_span"])
        self.grid_spread = self.p.get("grid_spread", gridlib.OFF10["grid_spread"])
        self.grid_atr_k = self.p.get("grid_atr_k", gridlib.OFF10["grid_atr_k"])
        self.grid_retune = self.p.get("grid_retune", gridlib.OFF10["grid_retune"])
        self.tp_atr_k = self.p.get("tp_atr_k", gridlib.OFF10["tp_atr_k"])
        self.trail_k = self.p.get("trail_k", gridlib.OFF10["trail_k"])
        self.trail_start = self.p.get("trail_start", gridlib.OFF10["trail_start"])
        self.needs_atr_ref = bool(self.grid_atr_k or self.tp_atr_k)
        # Новостей здесь НЕТ и быть не должно: фьючерсная сетка торгует
        # ровно то, что проверяет бэктест. Новостной фон подключён к
        # спотовому боту (bot_spot.py) — см. docs/NEWS_STRATEGY.md.
        self._last_pushed = (None, None)   # чтобы не слать один и тот же SL/TP
        # направление: новый числовой ген direction (0=обе/1=лонг/2=шорт)
        # приоритетнее старого строкового dir ("B"/"L"/"S"), которым были
        # заданы ранние архивные конфиги
        direction = self.p.get("direction")
        if direction is None:
            direction = {"B": 0, "L": 1, "S": 2}.get(self.p.get("dir", "B"), 0)
        self.direction = direction
        # режимный гейт: торговать только в bear/боковике (0=всегда, 1=гейт).
        # По умолчанию включён у режима "bear" ради обратной совместимости
        # со старыми архивными bear-ботами, которые гейтили снаружи генома.
        self.regime_gate = self.p.get("regime_gate", 1 if mode == "bear" else 0)
        # фильтр графических паттернов: 0=выкл, 1=требовать совпадение,
        # 2=запрещать только явное противоречие (мягкий вариант)
        self.pattern_gate = self.p.get("pattern_gate", 0)
        self._regime_cache = (0, None)  # (day, "bull"|"range"|"bear")
        self._macro_needed = (self.spx_long_min > -900 or
                              self.dxy_long_max < 900 or
                              self.gold_long_max < 900)
        self._macro_cache = (0, None)  # (day_ts, dict)

        self.last_candle_ts = 0
        self.pos = None
        self.consec_losses = 0
        self.paused_until = 0     # kill switch, unix ms
        self.cooldown_until = 0   # турбо: пауза после стопа, unix ms

    # ---------- утилиты ----------

    def rq(self, qty):
        q = int(qty / self.qty_step) * self.qty_step
        return float(f"{q:.10f}".rstrip("0").rstrip("."))

    def rp(self, price):
        return f"{price:.{self.price_dec}f}"

    def candles(self):
        need = max(self.window,
                   self.masl if self.ma_mode else 0,
                   self.ema_n if self.ema_mode else 0,
                   self.aroon_n,
                   300 if (self.pattern_gate or self.ob_gate or
                          self.fvg_gate or self.structure_mode) else 0)
        if self.needs_atr_ref:
            # «норму» волатильности бот обязан считать по тому же окну, что и
            # бэктест, иначе один и тот же геном даст разные сетки
            need = max(need, gridlib.ATR_REF_BARS + self.atr_period)
        total = need + self.atr_period + 10
        rows = []                      # от новых к старым
        cursor = None
        while len(rows) < total:       # Bybit отдаёт максимум 1000 за запрос
            kw = dict(category="linear", symbol=self.symbol,
                      interval=self.interval,
                      limit=min(1000, total - len(rows)))
            if cursor is not None:
                kw["end"] = cursor
            page = self.session.get_kline(**kw)["result"]["list"]
            if not page:
                break
            rows += page
            oldest = int(page[-1][0])
            if cursor is not None and oldest >= cursor:
                break
            cursor = oldest - 1
        rows = rows[::-1][:-1]         # хронологически, без незакрытой свечи
        return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]))
                for x in rows]

    def atr_norm(self, cs):
        """(ATR сейчас, «норма» ATR) для множителей волатильности.

        Оба числа считаются рядом gridlib.atr_series — тем же, что и в
        бэктесте. Нож-фильтр при этом продолжает пользоваться собственным
        ATR бота: его поведение менять нельзя, конфиги отбирались с ним.
        """
        if not self.needs_atr_ref:
            return None, None
        series = gridlib.atr_series(cs, self.atr_period)
        ref = gridlib.rolling_mean(series, gridlib.ATR_REF_BARS)
        return series[-1], ref[-1]

    def avg_entry(self):
        q = sum(f[1] for f in self.pos["fills"])
        return sum(p * qn for p, qn in self.pos["fills"]) / q, q

    def tp_price(self):
        avg, _ = self.avg_entry()
        sgn = 1 if self.pos["side"] == "L" else -1
        if self.turbo:
            return avg * (1 + sgn * self.p["tp_k"] * self.pos["atr0"])
        return avg * (1 + sgn * self.pos.get("tp_eff", self.p["tp"]))

    def stop_price(self):
        if self.turbo:  # короткий стоп от средней
            avg, _ = self.avg_entry()
            sgn = 1 if self.pos["side"] == "L" else -1
            return avg * (1 - sgn * self.p["stop_k"] * self.pos["atr0"])
        return self.pos["stop"]   # за уровнем либо подтянутый трейлингом

    # ---------- биржа ----------

    def set_leverage(self):
        try:
            self.session.set_leverage(category="linear", symbol=self.symbol,
                                      buyLeverage=str(self.p["lev"]),
                                      sellLeverage=str(self.p["lev"]))
        except Exception as e:
            if "110043" not in str(e):
                raise

    def exchange_position(self):
        r = self.session.get_positions(category="linear", symbol=self.symbol)
        for pp in r["result"]["list"]:
            size = float(pp["size"] or 0)
            if size > 0:
                return pp["side"], size, float(pp["avgPrice"])
        return None, 0.0, 0.0

    # ---------- внешние сигналы ----------

    def get_funding(self):
        """Текущий funding rate в % за 8ч, либо None."""
        try:
            r = self.session.get_tickers(category="linear", symbol=self.symbol)
            return float(r["result"]["list"][0]["fundingRate"]) * 100
        except Exception:
            return None

    def get_oi_chg24(self):
        """Изменение открытого интереса за 24ч в %, либо None."""
        try:
            r = self.session.get_open_interest(
                category="linear", symbol=self.symbol,
                intervalTime="1h", limit=25)
            rows = r["result"]["list"]  # от новой к старой
            if len(rows) < 25:
                return None
            now, prev = float(rows[0]["openInterest"]), float(rows[24]["openInterest"])
            return (now / prev - 1) * 100 if prev else None
        except Exception:
            return None

    def get_macro(self):
        """{'spx':..,'dxy':..,'gold':..} — 5-дневные изменения %, либо None.
        Кэш на сутки; при недоступности данных фильтры пропускают вход."""
        if not self._macro_needed:
            return None
        import os
        import time as _t
        day = int(_t.time()) // 86400
        if self._macro_cache[0] == day:
            return self._macro_cache[1]
        try:
            cache_file = "daily_pct5.json"
            if (os.path.exists(cache_file) and
                    _t.time() - os.path.getmtime(cache_file) > 86400):
                os.remove(cache_file)  # обновляем раз в сутки
            import ext_data
            pct5 = ext_data.fetch_daily_pct5()
            now_day = int(_t.time()) // 86400 * 86400
            out = {}
            for k in ("spx", "dxy", "gold"):
                v = None
                for back in range(7):
                    v = pct5[k].get(now_day - back * 86400)
                    if v is not None:
                        break
                out[k] = v
            self._macro_cache = (day, out)
            return out
        except Exception:
            self.log.warning("Макро-данные недоступны — фильтры пропущены")
            self._macro_cache = (day, None)
            return None

    def get_regime(self):
        """Режим рынка по ЗАКРЫТЫМ дневным свечам: bull / range / bear.
        bear = цена < SMA100д и изменение за 30д < -5%; bull — зеркально."""
        import time as _t
        day = int(_t.time()) // 86400
        if self._regime_cache[0] == day:
            return self._regime_cache[1]
        try:
            r = self.session.get_kline(category="linear", symbol=self.symbol,
                                       interval="D", limit=140)
            rows = r["result"]["list"][::-1][:-1]  # закрытые дни, от старых
            closes = [float(x[4]) for x in rows]
            if len(closes) < 101:
                regime = "range"
            else:
                c = closes[-1]
                sma100 = sum(closes[-100:]) / 100
                chg30 = c / closes[-31] - 1
                if c < sma100 and chg30 < -0.05:
                    regime = "bear"
                elif c > sma100 and chg30 > 0.05:
                    regime = "bull"
                else:
                    regime = "range"
        except Exception:
            regime = "range"  # при сбое данных не блокируем
        if regime != self._regime_cache[1]:
            self.log.info("Режим рынка: %s", regime)
        self._regime_cache = (day, regime)
        return regime

    def entry_allowed(self, side, cs):
        """Внешние фильтры входа. True = вход разрешён."""
        f = self.get_funding() if (self.fund_long_max < 900 or
                                   self.fund_short_min > -900) else None
        if f is not None:
            if side == "L" and f > self.fund_long_max:
                self.log.info("Фильтр funding: %.4f%% > %.4f%% — лонг отменён",
                              f, self.fund_long_max)
                return False
            if side == "S" and f < self.fund_short_min:
                self.log.info("Фильтр funding: %.4f%% < %.4f%% — шорт отменён",
                              f, self.fund_short_min)
                return False
        if self.oi_gate:
            o = self.get_oi_chg24()
            if o is not None:
                fall = o < 0
                ok_long = fall if self.oi_gate == 1 else not fall
                if (side == "L" and not ok_long) or (side == "S" and ok_long):
                    self.log.info("Фильтр OI (%.2f%% за 24ч) — вход отменён", o)
                    return False
        if side == "L" and self._macro_needed:
            m = self.get_macro()
            if m:
                if m["spx"] is not None and m["spx"] < self.spx_long_min:
                    self.log.info("Фильтр SPX %.2f%% — лонг отменён", m["spx"])
                    return False
                if m["dxy"] is not None and m["dxy"] > self.dxy_long_max:
                    self.log.info("Фильтр DXY %.2f%% — лонг отменён", m["dxy"])
                    return False
                if m["gold"] is not None and m["gold"] > self.gold_long_max:
                    self.log.info("Фильтр золота %.2f%% — лонг отменён", m["gold"])
                    return False
        closes = [x[4] for x in cs]
        if self.ema_mode and len(closes) >= self.ema_n:
            e_ = closes[0]
            a = 2 / (self.ema_n + 1)
            for c_ in closes[1:]:
                e_ = c_ * a + e_ * (1 - a)
            above = closes[-1] > e_
            want_long = above if self.ema_mode == 1 else not above
            if (side == "L" and not want_long) or (side == "S" and want_long):
                self.log.info("Фильтр EMA%d — вход отменён", self.ema_n)
                return False
        if self.ma_mode and len(closes) >= self.masl:
            f_ = sum(closes[-self.masf:]) / self.masf
            s_ = sum(closes[-self.masl:]) / self.masl
            upt = f_ > s_
            want_long = upt if self.ma_mode == 1 else not upt
            if (side == "L" and not want_long) or (side == "S" and want_long):
                self.log.info("Фильтр SMA%d/%d — вход отменён", self.masf, self.masl)
                return False
        if self.aroon_n:
            n = self.aroon_n
            if len(cs) >= n:
                win = cs[-n:]
                hi_i = max(range(n), key=lambda k: win[k][2])
                lo_i = min(range(n), key=lambda k: win[k][3])
                a_up = (hi_i + 1) / n * 100
                a_dn = (lo_i + 1) / n * 100
                if side == "L" and self.aroon_long_min and a_dn < self.aroon_long_min:
                    self.log.info("Фильтр Aroon (down %.0f) — лонг отменён", a_dn)
                    return False
                if side == "S" and self.aroon_short_min and a_up < self.aroon_short_min:
                    self.log.info("Фильтр Aroon (up %.0f) — шорт отменён", a_up)
                    return False
        if self.pattern_gate and len(cs) >= 60:
            bull, bear = patterns.compute_pattern_signals(
                cs, bars_per_day=self.atr_period)
            i = len(cs) - 1
            if self.pattern_gate == 1:
                if side == "L" and not bull[i]:
                    self.log.info("Фильтр паттернов: бычьего паттерна нет — лонг отменён")
                    return False
                if side == "S" and not bear[i]:
                    self.log.info("Фильтр паттернов: медвежьего паттерна нет — шорт отменён")
                    return False
            else:  # 2: мягкий — запрет только при явном противоречии
                if side == "L" and bear[i] and not bull[i]:
                    self.log.info("Фильтр паттернов: активен медвежий паттерн — лонг отменён")
                    return False
                if side == "S" and bull[i] and not bear[i]:
                    self.log.info("Фильтр паттернов: активен бычий паттерн — шорт отменён")
                    return False
        if self.ob_gate and len(cs) >= 60:
            ob_bull, ob_bear = smc.find_order_blocks(cs, bars_per_day=self.atr_period)
            i = len(cs) - 1
            if self.ob_gate == 1:
                if side == "L" and not ob_bull[i]:
                    self.log.info("Фильтр ордер-блоков: нет бычьего OB — лонг отменён")
                    return False
                if side == "S" and not ob_bear[i]:
                    self.log.info("Фильтр ордер-блоков: нет медвежьего OB — шорт отменён")
                    return False
            else:
                if side == "L" and ob_bear[i] and not ob_bull[i]:
                    return False
                if side == "S" and ob_bull[i] and not ob_bear[i]:
                    return False
        if self.fvg_gate and len(cs) >= 60:
            fvg_bull, fvg_bear = smc.find_fvg(cs, bars_per_day=self.atr_period)
            i = len(cs) - 1
            if self.fvg_gate == 1:
                if side == "L" and not fvg_bull[i]:
                    self.log.info("Фильтр FVG: нет бычьего гэпа — лонг отменён")
                    return False
                if side == "S" and not fvg_bear[i]:
                    self.log.info("Фильтр FVG: нет медвежьего гэпа — шорт отменён")
                    return False
            else:
                if side == "L" and fvg_bear[i] and not fvg_bull[i]:
                    return False
                if side == "S" and fvg_bull[i] and not fvg_bear[i]:
                    return False
        if self.structure_mode and len(cs) >= 60:
            bias = smc.structure_bias(cs)[-1]
            want_long = (bias > 0) if self.structure_mode == 1 else (bias < 0)
            want_short = (bias < 0) if self.structure_mode == 1 else (bias > 0)
            if side == "L" and not want_long:
                self.log.info("Фильтр структуры (bias=%d) — лонг отменён", bias)
                return False
            if side == "S" and not want_short:
                self.log.info("Фильтр структуры (bias=%d) — шорт отменён", bias)
                return False
        return True

    def push_sl_tp(self, force=False):
        tp, sl = self.tp_price(), self.stop_price()
        key = (self.rp(sl), self.rp(tp))
        if not force and key == self._last_pushed:
            return          # ничего не изменилось — не дёргаем биржу впустую
        self._last_pushed = key
        self.log.info("SL/TP обновлены: стоп %s, тейк %s", self.rp(sl), self.rp(tp))
        if not config.DRY_RUN:
            self.session.set_trading_stop(category="linear", symbol=self.symbol,
                                          stopLoss=self.rp(sl),
                                          takeProfit=self.rp(tp), positionIdx=0)

    # ---------- цикл сделки ----------

    def margin_total(self):
        """Маржа на цикл. "fixed" — константа (как в бэктестах, линейный
        рост); "percent" — доля текущего капитала (реинвест). Капитал:
        DRY_RUN — виртуальный (старт config.START_BALANCE), реал — equity
        кошелька (кэш 60 сек)."""
        if config.MARGIN_MODE != "percent":
            return config.MARGIN_USDT
        if config.DRY_RUN:
            equity = self.virtual_balance
        else:
            now = time.monotonic()
            if self._equity_cache[1] is None or now - self._equity_cache[0] > 60:
                try:
                    r = self.session.get_wallet_balance(accountType="UNIFIED")
                    equity = float(r["result"]["list"][0]["totalEquity"])
                except Exception:
                    equity = self._equity_cache[1] or config.START_BALANCE
                self._equity_cache = (now, equity)
            equity = self._equity_cache[1]
        return max(1.0, equity * config.MARGIN_FRACTION)

    def enter(self, side, price, r_low, r_high, atr, atr_now=None, atr_ref=None):
        sgn = 1 if side == "L" else -1
        m_total = self.margin_total()
        leg_margins = [m_total * w for w in self.leg_weights]
        qty0 = self.rq(leg_margins[0] * self.p["lev"] / price)
        if qty0 < self.min_qty:
            self.log.warning("Объём %s < минимума %s — вход пропущен (мало маржи)",
                             qty0, self.min_qty)
            return
        step = (config.TURBO_STEP_K * atr) if self.turbo else self.p["step"]
        if self.turbo:
            stop = price * (1 - sgn * self.p["stop_k"] * atr)
        else:
            stop = (r_low * (1 - self.p["sweep"]) if side == "L"
                    else r_high * (1 + self.p["sweep"]))
        # множители волатильности фиксируются на входе и дальше не меняются —
        # так же, как в движке (иначе тейк ездил бы задним числом)
        vol_k = gridlib.vol_factor(atr_now, atr_ref, self.grid_atr_k)
        tp_eff = (self.p["tp"] * gridlib.tp_factor(atr_now, atr_ref,
                                                   self.tp_atr_k)
                  if not self.turbo else 0.0)
        prices = gridlib.grid_prices(price, stop, sgn, self.levels, step,
                                     self.grid_mode, self.grid_span,
                                     self.grid_spread, vol_k)
        adds = [(ap, self.rq(leg_margins[k + 1] * self.p["lev"] / ap))
                for k, ap in enumerate(prices)]

        self.pos = dict(side=side, fills=[(price, qty0)], stop=stop, adds=adds,
                        opened_ts=int(time.time() * 1000), atr0=atr,
                        tp_eff=tp_eff, vol_k=vol_k, entry_px=price, best=price)
        self._last_pushed = (None, None)
        tp = self.tp_price()
        self.log.info(">>> ВХОД %s x%d: %s по ~%s | стоп %s | тейк %s | ATR %.2f%%",
                      "LONG" if side == "L" else "SHORT", self.p["lev"],
                      qty0, self.rp(price), self.rp(self.stop_price()),
                      self.rp(tp), atr * 100)
        for i, (ap, aq) in enumerate(adds):
            self.log.info("    сетка %d: лимитка %s по %s", i + 2, aq, self.rp(ap))

        if not config.DRY_RUN:
            order_side = "Buy" if side == "L" else "Sell"
            self.session.place_order(
                category="linear", symbol=self.symbol, side=order_side,
                orderType="Market", qty=str(qty0),
                stopLoss=self.rp(self.stop_price()), takeProfit=self.rp(tp))
            for ap, aq in adds:
                if aq >= self.min_qty:
                    self.session.place_order(
                        category="linear", symbol=self.symbol, side=order_side,
                        orderType="Limit", qty=str(aq), price=self.rp(ap))
        else:
            self.log.info("[DRY_RUN] ордера не отправлены")

    def retune_grid(self, atr_now=None, atr_ref=None):
        """Перевыставить неисполненные лимитки сетки под текущую обстановку.

        Что именно «едет». Стоп привязан к границе диапазона и сдвигается с
        каждым баром, а волатильность меняется — значит меняется и то, где
        колена должны стоять. Выставленные при входе лимитки этого не знают.
        Политика (ген grid_retune) задаёт, можно ли двигать колено ближе к
        цене (2) или только дальше от неё (1, консервативно).

        Множитель волатильности берём ТЕКУЩИЙ, как и движок: с входным
        множителем при grid_mode=0 пересчёт не менял бы ничего.
        """
        sgn = 1 if self.pos["side"] == "L" else -1
        n_left = len(self.pos["adds"])
        k0 = self.levels - n_left
        vol_now = gridlib.vol_factor(atr_now, atr_ref, self.grid_atr_k)
        fresh = gridlib.retune_prices(
            self.pos["entry_px"], self.pos["stop"], sgn, n_left, self.levels,
            self.p["step"], self.grid_mode, self.grid_span, self.grid_spread,
            vol_now, [a[0] for a in self.pos["adds"]],
            self.grid_retune)
        old = [a[0] for a in self.pos["adds"]]
        # дёргаем биржу только при заметном сдвиге: перевыставлять лимитки
        # на каждой свече ради сотых процента — лишний риск и лишние запросы
        if all(abs(n - o) / o < 0.0005 for n, o in zip(fresh, old)):
            return
        m_total = self.margin_total()
        leg_margins = [m_total * w for w in self.leg_weights]
        self.pos["adds"] = [
            (p, self.rq(leg_margins[k0 + j] * self.p["lev"] / p))
            for j, p in enumerate(fresh)]
        self.log.info("Сетка переставлена: %s -> %s",
                      [self.rp(x) for x in old],
                      [self.rp(x) for x, _ in self.pos["adds"]])
        if not config.DRY_RUN:
            self.session.cancel_all_orders(category="linear", symbol=self.symbol)
            order_side = "Buy" if self.pos["side"] == "L" else "Sell"
            for ap, aq in self.pos["adds"]:
                if aq >= self.min_qty:
                    self.session.place_order(
                        category="linear", symbol=self.symbol, side=order_side,
                        orderType="Limit", qty=str(aq), price=self.rp(ap))

    def finish_cycle(self, pnl, reason):
        self.virtual_balance += pnl
        self.log.info("<<< ЦИКЛ ЗАВЕРШЁН (%s): PnL ~%+.3f USDT | капитал ~%.2f",
                      reason, pnl, self.virtual_balance)
        self.pos = None
        now = int(time.time() * 1000)
        if pnl < 0:
            self.consec_losses += 1
            if self.cooldown_bars:
                self.cooldown_until = now + self.cooldown_bars * self.interval_ms
            if self.consec_losses >= config.MAX_CONSEC_LOSSES:
                self.paused_until = now + config.KILL_PAUSE_HOURS * 3600 * 1000
                self.log.warning("KILL SWITCH: %d убытков подряд. Пауза до %s",
                                 self.consec_losses,
                                 time.strftime("%d.%m %H:%M",
                                               time.localtime(self.paused_until / 1000)))
                self.consec_losses = 0
        else:
            self.consec_losses = 0

    def close_market(self, reason, price):
        avg, qty = self.avg_entry()
        sgn = 1 if self.pos["side"] == "L" else -1
        pnl = sgn * (price - avg) * qty
        if not config.DRY_RUN:
            self.session.cancel_all_orders(category="linear", symbol=self.symbol)
            side = "Sell" if self.pos["side"] == "L" else "Buy"
            self.session.place_order(category="linear", symbol=self.symbol,
                                     side=side, orderType="Market",
                                     qty=str(self.rq(qty)), reduceOnly=True)
        self.finish_cycle(pnl, reason)

    # ---------- DRY_RUN: виртуальное исполнение ----------

    def paper_fill(self, candle):
        _, o, h, l, c = candle
        p = self.pos
        sgn = 1 if p["side"] == "L" else -1
        stop, tp = self.stop_price(), self.tp_price()
        avg, qty = self.avg_entry()
        if (l <= stop if sgn == 1 else h >= stop):
            self.finish_cycle(sgn * (stop - avg) * qty, "[DRY_RUN] СТОП")
            return
        if (h >= tp if sgn == 1 else l <= tp):
            self.finish_cycle(sgn * (tp - avg) * qty, "[DRY_RUN] ТЕЙК")
            return
        while p["adds"]:
            ap, aq = p["adds"][0]
            if (l <= ap if sgn == 1 else h >= ap):
                p["fills"].append((ap, aq))
                p["adds"].pop(0)
                self.log.info("[DRY_RUN] сетка исполнилась: %s по %s", aq, self.rp(ap))
                self.push_sl_tp()
            else:
                break

    # ---------- реал: синхронизация с биржей ----------

    def sync_exchange(self):
        side, size, avg = self.exchange_position()
        if self.pos and size == 0:
            self.session.cancel_all_orders(category="linear", symbol=self.symbol)
            pnl = self._last_closed_pnl()
            self.finish_cycle(pnl, "SL/TP биржей")
        elif self.pos and size > 0:
            filled = sum(f[1] for f in self.pos["fills"])
            if size > filled * 1.001:
                while self.pos["adds"] and size > filled * 1.001:
                    ap, aq = self.pos["adds"].pop(0)
                    self.pos["fills"].append((ap, aq))
                    filled += aq
                self.log.info("Сетка исполнилась (размер %s)", size)
                self.push_sl_tp()

    def _last_closed_pnl(self):
        try:
            r = self.session.get_closed_pnl(category="linear",
                                            symbol=self.symbol, limit=1)
            return float(r["result"]["list"][0]["closedPnl"])
        except Exception:
            return 0.0

    # ---------- обработка свечи ----------

    def on_new_candle(self, cs):
        closes = [x[4] for x in cs]
        c = closes[-1]
        rsi_now = calc_rsi(closes[-(self.rsi_period + 60):], self.rsi_period)
        rsi_prev = calc_rsi(closes[-(self.rsi_period + 61):-1], self.rsi_period)
        r_low = min(x[3] for x in cs[-self.window:])
        r_high = max(x[2] for x in cs[-self.window:])
        atr = calc_atr_pct(cs, self.atr_period)
        rng = r_high - r_low
        if not rng or rsi_now is None or rsi_prev is None or not atr:
            return
        zone_pos = (c - r_low) / rng
        self.log.info("Свеча: %s | RSI %.1f | зона %.0f%% | ATR %.2f%%",
                      self.rp(c), rsi_now, zone_pos * 100, atr * 100)

        if self.pos:
            if config.DRY_RUN:
                self.paper_fill(cs[-1])
            if self.pos and self.be_move and not self.pos.get("be_done"):
                # стоп в безубыток, когда прошли полпути к тейку
                avg, _ = self.avg_entry()
                sgn = 1 if self.pos["side"] == "L" else -1
                trig = avg * (1 + sgn * self.p["tp"] * 0.5)
                hi, lo = cs[-1][2], cs[-1][3]
                if (hi >= trig if sgn == 1 else lo <= trig):
                    # gridlib.breakeven_stop — та же формула, что в движке,
                    # включая запрет ставить стоп за уже пройденной ценой
                    # (биржа такой ордер не примет)
                    new_stop, done = gridlib.breakeven_stop(
                        avg, self.pos["stop"], sgn, c)
                    if done and new_stop != self.pos["stop"]:
                        self.pos["stop"] = new_stop
                        self.log.info("Безубыток: стоп перенесён на %s",
                                      self.rp(new_stop))
                        self.push_sl_tp()
                    elif not done:
                        self.log.info("Безубыток отложен: закрытие %s не дошло "
                                      "до безубытка — стоп туда не выставить",
                                      self.rp(c))
                    self.pos["be_done"] = done
            # подвижный стоп: считаем по ЗАКРЫТИЯМ и не заводим стоп выгоднее
            # закрытия текущего бара — ровно как в движке (см. gridlib.trail_stop)
            if self.pos and self.trail_k:
                sgn = 1 if self.pos["side"] == "L" else -1
                self.pos["best"] = (max(self.pos["best"], c) if sgn == 1
                                    else min(self.pos["best"], c))
                avg, _ = self.avg_entry()
                new_stop = gridlib.trail_stop(
                    avg, self.pos["best"], self.pos["stop"], sgn,
                    avg * self.pos["tp_eff"], self.trail_k, self.trail_start,
                    bound=c)
                if new_stop != self.pos["stop"]:
                    self.pos["stop"] = new_stop
                    self.log.info("Трейлинг: стоп подтянут на %s",
                                  self.rp(new_stop))
                    self.push_sl_tp()
            # подвижная сетка: неисполненные колена пересчитываются от
            # сегодняшнего стопа и перевыставляются на бирже
            if self.pos and self.grid_retune and self.pos["adds"]:
                self.retune_grid(*self.atr_norm(cs))
            if self.pos:
                bars = (int(time.time() * 1000) - self.pos["opened_ts"]) // self.interval_ms
                sgn = 1 if self.pos["side"] == "L" else -1
                if bars > self.max_bars and (
                        rsi_now >= 50 if sgn == 1 else rsi_now <= 50):
                    self.close_market("таймаут", c)
            return

        now = int(time.time() * 1000)
        if now < self.paused_until:
            return
        if now < self.cooldown_until:
            return
        if self.regime_gate and self.get_regime() == "bull":
            return  # гейт: в бычьем рынке не торгует (медвежий/боковой профиль)

        rsi_os = self.p["rsi_os"]
        side = None
        if rsi_prev >= rsi_os and rsi_now < rsi_os and zone_pos < self.zone_l:
            side = "L"
        elif (rsi_prev <= 100 - rsi_os and rsi_now > 100 - rsi_os
              and zone_pos > 1 - self.zone_s):
            side = "S"
        if side:
            if self.direction == 1 and side != "L":
                side = None
            elif self.direction == 2 and side != "S":
                side = None
        if side and self.knife > 0.05:  # фильтр падающего ножа
            move = (closes[-9] - c) / c
            if side == "L" and move > self.knife * atr:
                self.log.info("Нож-фильтр: падение %.1f%% > %.1f%% — вход отменён",
                              move * 100, self.knife * atr * 100)
                side = None
            elif side == "S" and -move > self.knife * atr:
                side = None
        if side and not self.entry_allowed(side, cs):
            side = None
        if side:
            atr_now, atr_ref = self.atr_norm(cs)
            self.enter(side, c, r_low, r_high, atr, atr_now, atr_ref)

    def describe(self):
        parts = [f"'{self.mode}'"]
        if self.turbo:
            parts.append("ТУРБО x20")
        if self.regime_gate:
            parts.append("гейт: в bull не торгует")
        if self.direction == 1:
            parts.append("только лонг")
        elif self.direction == 2:
            parts.append("только шорт")
        if self.pattern_gate:
            parts.append(f"фильтр паттернов (режим {self.pattern_gate})")
        if self.ob_gate:
            parts.append(f"ордер-блоки (режим {self.ob_gate})")
        if self.fvg_gate:
            parts.append(f"FVG (режим {self.fvg_gate})")
        if self.structure_mode:
            parts.append("по структуре" if self.structure_mode == 1 else "против структуры")
        if self.grid_mode:
            parts.append(f"сетка по пути до стопа (span {self.grid_span:.2f})")
        if self.grid_atr_k:
            parts.append(f"шаг сетки от волатильности (k {self.grid_atr_k:.2f})")
        if self.grid_retune:
            parts.append(f"сетка перевыставляется (режим {self.grid_retune})")
        if self.tp_atr_k:
            parts.append(f"тейк от волатильности (k {self.tp_atr_k:.2f})")
        if self.trail_k:
            parts.append(f"трейлинг {self.trail_k:.2f} с {self.trail_start:.2f}")
        return " | ".join(parts)

    def run(self):
        margin_desc = (f"{config.MARGIN_FRACTION*100:.0f}% капитала (реинвест)"
                       if config.MARGIN_MODE == "percent"
                       else f"{config.MARGIN_USDT} USDT (фикс)")
        self.log.info("Старт: %s %s | режим %s x%d | маржа %s | %s | DRY_RUN=%s",
                      self.symbol, self.interval,
                      self.describe(), self.p["lev"], margin_desc,
                      "TESTNET" if config.TESTNET else "MAINNET", config.DRY_RUN)
        self.log.info("Параметры: %s", self.p)
        if not config.DRY_RUN:
            if not config.API_KEY or not config.API_SECRET:
                raise SystemExit("Нет BYBIT_API_KEY / BYBIT_API_SECRET")
            self.set_leverage()
        while True:
            try:
                if not config.DRY_RUN and self.pos:
                    self.sync_exchange()
                cs = self.candles()
                if cs and cs[-1][0] != self.last_candle_ts:
                    self.last_candle_ts = cs[-1][0]
                    self.on_new_candle(cs)
            except KeyboardInterrupt:
                raise
            except Exception:
                self.log.exception("Ошибка цикла, повтор через %s c",
                                   config.POLL_SECONDS)
            time.sleep(config.POLL_SECONDS)


def main():
    if len(sys.argv) < 2:
        names = ", ".join(s.replace("USDT", "") for s in config.SYMBOL_PARAMS)
        raise SystemExit(f"Использование: python bot_rsi.py <монета> [режим]\n"
                         f"Монеты: {names}\n"
                         f"Режим по умолчанию: final (если выбран отбором v7), "
                         f"иначе normal. Явно: normal|bear|turbo|final")
    sym = sys.argv[1].upper()
    if not sym.endswith("USDT"):
        sym += "USDT"
    if sym not in config.SYMBOL_PARAMS:
        raise SystemExit(f"Нет параметров для {sym} (см. config.SYMBOL_PARAMS)")
    available = config.SYMBOL_PARAMS[sym]
    if len(sys.argv) > 2:
        mode = sys.argv[2].lower()
    else:
        mode = "final" if available.get("final") else "normal"
    if not available.get(mode):
        opts = ", ".join(k for k, v in available.items() if v)
        raise SystemExit(f"Режим '{mode}' недоступен для {sym}. Доступно: {opts}")
    bot = RsiGridBot(sym, mode)
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.log.info("Остановлено. Открытая позиция и лимитки остаются на бирже!")


if __name__ == "__main__":
    main()
