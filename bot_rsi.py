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

import json
import logging
import os
import sys
import time

from pybit.unified_trading import HTTP

import config
import patterns
import smc
import storm_filter as sf

# Ставки издержек — те же, что в честном бэктесте (evolution2), чтобы
# виртуальный DRY_RUN не расходился с симуляцией. Константы дублируются
# намеренно: живой бот не должен тянуть тяжёлый модуль эволюции.
TAKER = 0.00055     # тейкерская комиссия Bybit
MAKER = 0.0002      # мейкерская (лимитные тейк-профиты)
SLIP = 0.0003       # проскальзывание рыночного исполнения
FUND_8H = 0.0001    # фандинг за 8 часов удержания


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

        # --- штормовой фильтр (общая секция STORM в config.py) ---
        # Баров в сутках — по РЕАЛЬНОМУ ТФ бота, та же формула, что в
        # бэктесте (evolution8/finalize_final_bots), иначе «сутки» разъедутся.
        self.bars_day = sf.bars_per_day(self.interval)
        self.storm_on = bool(getattr(config, "STORM_ENABLED", False))
        self.storm_mode = int(getattr(config, "STORM_MODE", 0))
        self.storm_rank_days = int(getattr(config, "STORM_RANK_DAYS",
                                           sf.RANK_DAYS))
        self.storm_bars = sf.bars_needed(self.bars_day, self.storm_rank_days)
        self._storm = None            # состояние на последней закрытой свече

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
        self.state_file = os.path.join(
            "webapp", "data", f"botstate_{self.symbol}_{self.mode}.json")
        self.load_state()

    # ---------- состояние между запусками ----------

    def load_state(self):
        """Капитал, пауза kill switch и серия убытков должны переживать
        перезапуск: иначе после рестарта виртуальный капитал возвращается к
        стартовому, а 24-часовая пауза после трёх убытков снимается досрочно —
        ровно та защита, ради которой она и вводилась."""
        try:
            with open(self.state_file, encoding="utf-8") as fh:
                st = json.load(fh)
        except (OSError, ValueError):
            return
        self.virtual_balance = float(st.get("virtual_balance",
                                            self.virtual_balance))
        self.consec_losses = int(st.get("consec_losses", 0))
        self.paused_until = int(st.get("paused_until", 0))
        self.cooldown_until = int(st.get("cooldown_until", 0))
        left = (self.paused_until - time.time() * 1000) / 3600000
        self.log.info("Состояние восстановлено: капитал $%.2f, убытков подряд "
                      "%d%s", self.virtual_balance, self.consec_losses,
                      f", пауза ещё {left:.1f}ч" if left > 0 else "")

    def save_state(self):
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as fh:
                json.dump(dict(virtual_balance=round(self.virtual_balance, 4),
                               consec_losses=self.consec_losses,
                               paused_until=self.paused_until,
                               cooldown_until=self.cooldown_until,
                               saved_ts=int(time.time() * 1000)),
                          fh, ensure_ascii=False)
        except OSError as e:
            self.log.warning("не удалось сохранить состояние: %s", e)

    # ---------- утилиты ----------

    def rq(self, qty):
        q = int(qty / self.qty_step) * self.qty_step
        return float(f"{q:.10f}".rstrip("0").rstrip("."))

    def rp(self, price):
        return f"{price:.{self.price_dec}f}"

    def candles(self):
        # storm_bars просим ВСЕГДА, даже когда фильтр выключен: запрос всё
        # равно один (меняется только limit), зато состояние шторма видно в
        # логе с первой же свечи и его можно проверить, не дожидаясь обвала.
        need = max(self.window,
                   self.masl if self.ma_mode else 0,
                   self.ema_n if self.ema_mode else 0,
                   self.aroon_n,
                   self.storm_bars,
                   300 if (self.pattern_gate or self.ob_gate or
                          self.fvg_gate or self.structure_mode) else 0)
        limit = min(need + self.atr_period + 10, 1000)
        r = self.session.get_kline(category="linear", symbol=self.symbol,
                                   interval=self.interval, limit=limit)
        rows = r["result"]["list"][::-1][:-1]
        return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]))
                for x in rows]

    def avg_entry(self):
        q = sum(f[1] for f in self.pos["fills"])
        return sum(p * qn for p, qn in self.pos["fills"]) / q, q

    def tp_price(self):
        avg, _ = self.avg_entry()
        sgn = 1 if self.pos["side"] == "L" else -1
        if self.turbo:
            return avg * (1 + sgn * self.p["tp_k"] * self.pos["atr0"])
        return avg * (1 + sgn * self.p["tp"])

    def stop_price(self):
        if self.turbo:  # короткий стоп от средней
            avg, _ = self.avg_entry()
            sgn = 1 if self.pos["side"] == "L" else -1
            return avg * (1 - sgn * self.p["stop_k"] * self.pos["atr0"])
        return self.pos["stop"]  # за уровнем, фиксированный

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

    def storm_state(self, cs):
        """Состояние «шторма» на последней ЗАКРЫТОЙ свече: ход за 24ч и за 7
        суток плюс ранг волатильности — всё по СВОИМ свечам, которые уже
        пришли в candles() (дополнительных запросов к бирже нет).

        Считает storm_filter — тот же модуль, что вызывает бэктест
        evolution2.run5(storm=...) на тех же свечах, поэтому живое решение и
        тестовое совпадают. None = свечей пока мало (шторм не объявляем)."""
        if len(cs) < self.storm_bars:
            return None
        st = sf.from_config(cs, self.bars_day, config)
        return sf.state_at(st, len(cs) - 1)

    def entry_allowed(self, side, cs):
        """Внешние фильтры входа. True = вход разрешён."""
        # ШТОРМ — первым: это отказ от рынка целиком, а не деталь стратегии.
        # Стоит до запросов funding/OI/макро — в шторм они и не понадобятся.
        if self.storm_on and self._storm and sf.blocked_state(side, self._storm):
            self.log.info("%s", sf.log_line(self.symbol, self._storm, True))
            return False
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

    def push_sl_tp(self):
        tp, sl = self.tp_price(), self.stop_price()
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

    def enter(self, side, price, r_low, r_high, atr):
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
        adds = []
        ap = price
        for k in range(1, self.levels):
            ap = ap * (1 - sgn * step)
            adds.append((ap, self.rq(leg_margins[k] * self.p["lev"] / ap)))

        self.pos = dict(side=side, fills=[(price, qty0)], stop=stop, adds=adds,
                        opened_ts=int(time.time() * 1000), atr0=atr)
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
        self.save_state()   # капитал/пауза/серия переживают перезапуск

    def close_market(self, reason, price):
        avg, qty = self.avg_entry()
        sgn = 1 if self.pos["side"] == "L" else -1
        pnl = sgn * (price - avg) * qty
        if config.DRY_RUN:      # закрытие по рынку = тейкерские издержки
            pnl -= self.paper_costs(avg, qty, True)
        if not config.DRY_RUN:
            self.session.cancel_all_orders(category="linear", symbol=self.symbol)
            side = "Sell" if self.pos["side"] == "L" else "Buy"
            self.session.place_order(category="linear", symbol=self.symbol,
                                     side=side, orderType="Market",
                                     qty=str(self.rq(qty)), reduceOnly=True)
        self.finish_cycle(pnl, reason)

    # ---------- DRY_RUN: виртуальное исполнение ----------

    def paper_costs(self, avg, qty, taker_exit):
        """Издержки виртуального цикла в долларах — те же ставки, что в
        честном бэктесте (evolution2.run5). Без них DRY_RUN завышал результат:
        у SOL комиссии за цикл ($0.0146) вдвое больше чистой прибыли ($0.0074),
        а при MARGIN_MODE='percent' эта фиктивная прибыль ещё и
        реинвестируется, так что ошибка росла экспоненциально."""
        notional = abs(avg * qty)
        entry_fee = notional * (TAKER + SLIP)
        exit_fee = notional * ((TAKER + SLIP) if taker_exit else MAKER)
        opened = self.pos.get("opened_ts", int(time.time() * 1000))
        hours = max(0.0, (time.time() * 1000 - opened) / 3600000.0)
        funding = notional * FUND_8H * (hours / 8.0)
        return entry_fee + exit_fee + funding

    def paper_fill(self, candle):
        _, o, h, l, c = candle
        p = self.pos
        sgn = 1 if p["side"] == "L" else -1
        stop, tp = self.stop_price(), self.tp_price()
        avg, qty = self.avg_entry()
        if (l <= stop if sgn == 1 else h >= stop):
            gross = sgn * (stop - avg) * qty
            self.finish_cycle(gross - self.paper_costs(avg, qty, True),
                              "[DRY_RUN] СТОП")
            return
        if (h >= tp if sgn == 1 else l <= tp):
            gross = sgn * (tp - avg) * qty
            self.finish_cycle(gross - self.paper_costs(avg, qty, False),
                              "[DRY_RUN] ТЕЙК")
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
        # состояние шторма считаем на КАЖДОЙ свече и пишем в лог всегда (даже
        # при STORM_ENABLED=False) — так видно, что фильтр посчитал бы, и
        # почему он сработал или не сработал. Само решение — в entry_allowed.
        self._storm = self.storm_state(cs)
        self.log.info("%s", sf.log_line(self.symbol, self._storm, self.storm_on))

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
                    be = avg * (1 + sgn * 0.0015)
                    better = (be > self.pos["stop"] if sgn == 1
                              else be < self.pos["stop"])
                    if better:
                        self.pos["stop"] = be
                        self.log.info("Безубыток: стоп перенесён на %s", self.rp(be))
                        self.push_sl_tp()
                    self.pos["be_done"] = True
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
            self.enter(side, c, r_low, r_high, atr)

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
        return " | ".join(parts)

    def storm_desc(self):
        if not self.storm_on:
            return ("выключен (config.STORM_ENABLED=False) — состояние всё "
                    "равно считается и пишется в лог")
        return ("ВКЛЮЧЕН, режим %d (%s): сутки >%.0f%%, неделя >%.0f%%, "
                "ранг ATR >%.2f за %d сут." % (
                    self.storm_mode,
                    "не входить против шторма" if self.storm_mode == 1
                    else "в шторм не открывать новые циклы",
                    config.STORM_DAY_PCT * 100, config.STORM_WEEK_PCT * 100,
                    config.STORM_ATR_RANK, self.storm_rank_days))

    def run(self):
        margin_desc = (f"{config.MARGIN_FRACTION*100:.0f}% капитала (реинвест)"
                       if config.MARGIN_MODE == "percent"
                       else f"{config.MARGIN_USDT} USDT (фикс)")
        self.log.info("Старт: %s %s | режим %s x%d | маржа %s | %s | DRY_RUN=%s",
                      self.symbol, self.interval,
                      self.describe(), self.p["lev"], margin_desc,
                      "TESTNET" if config.TESTNET else "MAINNET", config.DRY_RUN)
        self.log.info("Параметры: %s", self.p)
        self.log.info("Штормовой фильтр: %s", self.storm_desc())
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
