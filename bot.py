# -*- coding: utf-8 -*-
"""Торговый бот Bybit: линейные фьючерсы, стратегия SMA-кроссовер.

Логика:
  - берём закрытые свечи SYMBOL/INTERVAL;
  - считаем SMA_FAST и SMA_SLOW;
  - быстрая пересекла медленную снизу вверх  -> закрыть шорт, открыть лонг;
  - быстрая пересекла медленную сверху вниз -> закрыть лонг, открыть шорт;
  - при входе сразу ставятся стоп-лосс и тейк-профит.

Запуск:  python bot.py
Остановка: Ctrl+C (открытая позиция при этом НЕ закрывается).
"""

import logging
import time
from decimal import Decimal, ROUND_HALF_UP

from pybit.unified_trading import HTTP

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("bybit-bot")


class SmaBot:
    def __init__(self):
        self.session = HTTP(
            testnet=config.TESTNET,
            api_key=config.API_KEY,
            api_secret=config.API_SECRET,
        )
        self.qty_step, self.min_qty = self._instrument_filters()
        self.last_candle_ts = 0  # ts последней обработанной закрытой свечи

    # ---------- Биржевые хелперы ----------

    def _instrument_filters(self):
        info = self.session.get_instruments_info(
            category="linear", symbol=config.SYMBOL
        )
        item = info["result"]["list"][0]
        f = item["lotSizeFilter"]
        self.tick_size = item["priceFilter"]["tickSize"]  # напр. "0.00001"
        return float(f["qtyStep"]), float(f["minOrderQty"])

    def _round_qty(self, qty: float) -> float:
        step = self.qty_step
        qty = int(qty / step) * step
        # убираем хвосты плавающей точки
        return float(f"{qty:.10f}".rstrip("0").rstrip("."))

    def _fmt_price(self, price: float) -> str:
        """Цена, ПРИТЯНУТАЯ к сетке тика инструмента.

        Считать одни знаки после точки мало: у BTC tickSize "0.10", у SOL
        "0.010" — по числу знаков вышло бы 2 и 3 знака, и цена 60123.45 прошла
        бы форматирование, но кратной шагу не была бы. Биржа такой ордер
        отклоняет (10001 Invalid price), то есть стоп и тейк просто не встают.
        """
        tick = Decimal(self.tick_size)
        q = (Decimal(str(price)) / tick).quantize(
            Decimal(1), rounding=ROUND_HALF_UP) * tick
        return f"{q:.{max(0, -tick.as_tuple().exponent)}f}"

    def get_closed_candles(self, limit: int = 200):
        """Список закрытых свечей от старой к новой: [(ts, close), ...]."""
        r = self.session.get_kline(
            category="linear",
            symbol=config.SYMBOL,
            interval=config.INTERVAL,
            limit=limit,
        )
        rows = r["result"]["list"]  # приходят от новой к старой
        rows = rows[::-1]
        # последняя строка — текущая (незакрытая) свеча, отбрасываем
        rows = rows[:-1]
        return [(int(x[0]), float(x[4])) for x in rows]

    def get_position(self):
        """Возвращает ('Buy'|'Sell'|None, size)."""
        r = self.session.get_positions(category="linear", symbol=config.SYMBOL)
        for p in r["result"]["list"]:
            size = float(p["size"] or 0)
            if size > 0:
                return p["side"], size
        return None, 0.0

    def set_leverage(self):
        if config.DRY_RUN:
            # плечо меняет настройку СЧЁТА: в бумажном режиме к бирже не лезем
            log.info("[DRY_RUN] плечо не меняется")
            return
        try:
            self.session.set_leverage(
                category="linear",
                symbol=config.SYMBOL,
                buyLeverage=str(config.LEVERAGE),
                sellLeverage=str(config.LEVERAGE),
            )
            log.info("Плечо установлено: x%s", config.LEVERAGE)
        except Exception as e:
            # ErrCode 110043 = leverage not modified — это ок
            if "110043" in str(e):
                log.info("Плечо уже x%s", config.LEVERAGE)
            else:
                raise

    # ---------- Торговые действия ----------

    def close_position(self, side: str, size: float):
        opposite = "Sell" if side == "Buy" else "Buy"
        log.info("Закрываю позицию %s, размер %s", side, size)
        if config.DRY_RUN:
            log.info("[DRY_RUN] ордер не отправлен")
            return
        self.session.place_order(
            category="linear",
            symbol=config.SYMBOL,
            side=opposite,
            orderType="Market",
            qty=str(size),
            reduceOnly=True,
        )

    def open_position(self, side: str, price: float):
        qty = self._round_qty(config.MARGIN_USDT * config.LEVERAGE / price)
        if qty < self.min_qty:
            log.warning(
                "Расчётный объём %s меньше минимального %s — сделка пропущена. "
                "Увеличь MARGIN_USDT.", qty, self.min_qty,
            )
            return
        if side == "Buy":
            sl = price * (1 - config.STOP_LOSS_PCT / 100)
            tp = price * (1 + config.TAKE_PROFIT_PCT / 100)
        else:
            sl = price * (1 + config.STOP_LOSS_PCT / 100)
            tp = price * (1 - config.TAKE_PROFIT_PCT / 100)

        log.info(
            "Открываю %s %s %s по ~%s | SL %s | TP %s",
            side, qty, config.SYMBOL,
            self._fmt_price(price), self._fmt_price(sl), self._fmt_price(tp),
        )
        if config.DRY_RUN:
            log.info("[DRY_RUN] ордер не отправлен")
            return
        self.session.place_order(
            category="linear",
            symbol=config.SYMBOL,
            side=side,
            orderType="Market",
            qty=str(qty),
            stopLoss=self._fmt_price(sl),
            takeProfit=self._fmt_price(tp),
        )

    # ---------- Стратегия ----------

    @staticmethod
    def sma(values, period):
        return sum(values[-period:]) / period

    def check_signal(self):
        candles = self.get_closed_candles()
        ts, _ = candles[-1]
        if ts == self.last_candle_ts:
            return  # новая свеча ещё не закрылась
        self.last_candle_ts = ts

        closes = [c for _, c in candles]
        if len(closes) < config.SMA_SLOW + 1:
            log.warning("Мало свечей для расчёта SMA")
            return

        fast_now = self.sma(closes, config.SMA_FAST)
        slow_now = self.sma(closes, config.SMA_SLOW)
        fast_prev = self.sma(closes[:-1], config.SMA_FAST)
        slow_prev = self.sma(closes[:-1], config.SMA_SLOW)
        price = closes[-1]

        log.info(
            "Свеча закрыта. Цена %s | SMA%d %.6g | SMA%d %.6g",
            self._fmt_price(price), config.SMA_FAST, fast_now, config.SMA_SLOW, slow_now,
        )

        cross_up = fast_prev <= slow_prev and fast_now > slow_now
        cross_down = fast_prev >= slow_prev and fast_now < slow_now
        if not cross_up and not cross_down:
            return

        target = "Buy" if cross_up else "Sell"
        log.info(">>> СИГНАЛ: %s (кроссовер %s)", target, "вверх" if cross_up else "вниз")

        side, size = self.get_position()
        if side == target:
            log.info("Позиция %s уже открыта — ничего не делаю", side)
            return
        if side is not None:
            self.close_position(side, size)
        self.open_position(target, price)

    # ---------- Главный цикл ----------

    def run(self):
        log.info(
            "Старт бота: %s %sm | SMA %d/%d | плечо x%d | маржа %s USDT | %s | DRY_RUN=%s",
            config.SYMBOL, config.INTERVAL, config.SMA_FAST, config.SMA_SLOW,
            config.LEVERAGE, config.MARGIN_USDT,
            "TESTNET" if config.TESTNET else "MAINNET", config.DRY_RUN,
        )
        if not config.DRY_RUN:
            if not config.API_KEY or not config.API_SECRET:
                raise SystemExit(
                    "Не заданы BYBIT_API_KEY / BYBIT_API_SECRET в переменных окружения"
                )
            self.set_leverage()

        while True:
            try:
                self.check_signal()
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("Ошибка в цикле, повтор через %s c", config.POLL_SECONDS)
            time.sleep(config.POLL_SECONDS)


if __name__ == "__main__":
    try:
        SmaBot().run()
    except KeyboardInterrupt:
        log.info("Остановлено пользователем")
