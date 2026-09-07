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

import math
import json
import logging
import os
import sys
import tempfile
import time
import urllib.parse
import uuid
import urllib.request
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from pybit.unified_trading import HTTP

import config
import gridlib
import indicators
import patterns
import smc
import trend_gate

# Ставки издержек — те же, что в честном бэктесте (evolution2), чтобы
# виртуальный DRY_RUN не расходился с симуляцией. Константы дублируются
# намеренно: живой бот не должен тянуть тяжёлый модуль эволюции.
TAKER = 0.00055     # тейкерская комиссия Bybit
MAKER = 0.0002      # мейкерская (лимитные колена сетки и тейк-профит)
SLIP = 0.0003       # проскальзывание рыночного исполнения
FUND_8H = 0.0001    # фандинг за 8 часов удержания
# Ликвидация — те же величины, что в движке (evolution2.MMR / LIQ_LOSS).
# MMR: биржа закрывает позицию не когда убыток съел всю маржу, а когда
# свободных средств осталось меньше maintenance margin. LIQ_LOSS=1.0: остаток
# после ликвидации съедает ликвидационная комиссия — теряется вся маржа цикла.
MMR = 0.005
LIQ_LOSS = 1.0

# Консоль Windows по умолчанию живёт не в UTF-8 (cp866/cp1251): русский лог в
# ней либо превращается в кракозябры, либо роняет процесс UnicodeEncodeError'ом
# прямо в момент, когда надо читать сообщение об аварии. Файл лога уже пишется
# в utf-8 — приводим к нему же и консоль: сперва кодовую страницу самого окна
# (иначе правильные utf-8 байты нарисуются мусором), потом потоки Python.
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:       # noqa: BLE001 — вывод не повод не торговать
        pass
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass


class Notifier:
    """Канал «владельцу» в Telegram.

    Молчит и не падает, если TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID не заданы:
    отсутствие уведомлений — не повод останавливать торговлю. По той же
    причине сетевые ошибки Telegram гасятся в предупреждение.

    Ключ + min_gap защищают от спама: события вроде «биржа не отвечает»
    повторяются каждые POLL_SECONDS, и без глушилки чат станет нечитаемым
    ровно тогда, когда его надо читать внимательнее всего.
    """

    API = "https://api.telegram.org/bot{}/sendMessage"

    def __init__(self, prefix, log):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.prefix = prefix
        self.log = log
        self._sent = {}          # ключ события -> monotonic последней отправки
        if not (self.token and self.chat):
            self.log.info("Telegram-уведомления выключены "
                          "(нет TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")

    def send(self, text, key=None, min_gap=None):
        if key is not None:
            gap = config.NOTIFY_MIN_GAP_SEC if min_gap is None else min_gap
            now = time.monotonic()
            if gap and now - self._sent.get(key, -1e9) < gap:
                return False
            self._sent[key] = now
        if not (self.token and self.chat):
            return False
        try:
            data = urllib.parse.urlencode(
                {"chat_id": self.chat, "text": f"{self.prefix} {text}",
                 "disable_web_page_preview": "true"}).encode()
            with urllib.request.urlopen(
                    urllib.request.Request(self.API.format(self.token), data=data),
                    timeout=10) as resp:
                resp.read()
            return True
        except Exception as e:      # noqa: BLE001 — канал связи не критичен
            self.log.warning("Telegram недоступен: %s", e)
            return False


class _EntryGate:
    """Файл-замок в STATE_DIR: один вход на счёт за раз (см. RsiGridBot.entry_gate)."""

    def __init__(self, bot):
        self.bot = bot
        self.held = False
        # Метка ВЛАДЕЛЬЦА замка. Снимать файл «по пути» нельзя: бот A мог
        # держать замок дольше LOCK_STALE_SEC (зависший запрос к бирже), бот B
        # снял его как протухший и взял свой, — и тогда A на выходе удалял бы
        # ЧУЖОЙ замок, оставляя счёт без защиты ровно в момент входа B.
        # uuid, а не только pid: pid операционная система переиспользует.
        self.token = f"{os.getpid()} {bot.symbol} {bot.mode} {uuid.uuid4().hex}"

    def __enter__(self):
        if config.DRY_RUN:
            return self        # бумажные боты не делят один кошелёк
        path = self.bot._lock_path()
        deadline = time.monotonic() + self.bot.LOCK_WAIT_SEC
        while True:
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                # O_EXCL — атомарное «создать, если нет» и на Windows, и на Linux
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, self.token.encode())
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(path)
                except OSError:
                    age = 0
                if age > self.bot.LOCK_STALE_SEC:
                    self.bot.log.warning("Замок входа брошен (%.0f c) — снимаю", age)
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    self.bot.log.warning(
                        "Замок входа занят дольше %d c — вхожу без очереди "
                        "(потолок маржи может быть посчитан по устаревшим "
                        "данным)", self.bot.LOCK_WAIT_SEC)
                    return self
                time.sleep(0.2)
            except OSError as e:
                self.bot.log.warning("Замок входа недоступен (%s) — вхожу "
                                     "без очереди", e)
                return self

    def __exit__(self, *exc):
        if self.held:
            path = self.bot._lock_path()
            try:
                with open(path, "rb") as fh:
                    owner = fh.read().decode("utf-8", "replace")
            except OSError:
                owner = None        # замка уже нет — снимать нечего
            if owner == self.token:
                try:
                    os.remove(path)
                except OSError:
                    pass
            elif owner is not None:
                # замок успел смениться, пока мы держались: файл уже НЕ наш.
                # Проверка «прочитать и сравнить» не атомарна — между чтением и
                # remove владелец теоретически может смениться ещё раз, — но
                # окно сузилось с «всё время удержания» до одного системного
                # вызова, а протухание всё равно снимет чужой брошенный замок.
                self.bot.log.warning("Замок входа принадлежит уже другому "
                                     "процессу (%s) — не снимаю", owner)
            self.held = False
        return False


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
        self.notifier = Notifier(f"[{symbol} {mode}]", self.log)

        self.session = HTTP(testnet=config.TESTNET,
                            api_key=config.API_KEY, api_secret=config.API_SECRET)
        info = self.session.get_instruments_info(
            category="linear", symbol=symbol)["result"]["list"][0]
        f = info["lotSizeFilter"]
        self.qty_step = Decimal(str(f["qtyStep"]))
        self.min_qty = float(f["minOrderQty"])
        # минимальный НОМИНАЛ заявки (у всех пяти монет 5 USDT). Проверять
        # только minOrderQty мало: 0.001 BTC проходит по количеству, но заявка
        # на 1 USDT будет отклонена биржей — и бот считал бы себя в позиции.
        self.min_notional = float(f.get("minNotionalValue") or 0)
        # Шаг цены хранится как Decimal, а не как «число знаков после точки».
        # tickSize у BTC = "0.10", у SOL = "0.010": по числу знаков получалось
        # бы 2 и 3 знака, и цена 60123.45 (или 142.155) прошла бы форматирование,
        # но НЕ кратна шагу — биржа отклоняет такой ордер (10001 Invalid price).
        self.tick = Decimal(str(info["priceFilter"]["tickSize"]))
        self.price_dec = max(0, -self.tick.as_tuple().exponent)

        # таймфрейм — атрибут КОНКРЕТНОГО бота, не глобальная константа: в
        # портфеле могут одновременно жить боты на 15m и на 4ч (Bybit: "240").
        # ATR "суток" считается от РЕАЛЬНОГО таймфрейма бота, иначе на 4ч
        # 96 баров были бы 16 днями вместо суток — фильтр ножа и паттерны
        # сравнивали бы движение с ложным окном волатильности.
        self.interval = str(self.p.get("interval", config.INTERVAL))
        self.interval_ms = int(self.interval) * 60 * 1000
        # сколько свечей бота укладывается в интервал фандинга (8ч). Считается
        # от РЕАЛЬНОГО таймфрейма, а не от константы движка (там 32 = 8ч в
        # 15m): на 4ч-боте фандинг иначе начислялся бы в 16 раз чаще.
        self.bars_8h = max(1, 8 * 60 // int(self.interval))
        self.atr_period = self.p.get("atr_period", max(4, 1440 // int(self.interval)))

        levels = self.p.get("levels", config.GRID_LEVELS)
        mult = self.p.get("mult", config.GRID_MULT)
        self.levels = levels
        self.mult = mult
        # веса колен считает gridlib — тот же код, что и движок отбора
        # (evolution2.run5). Раньше бот считал их сам, [mult**k], и ген
        # grid_w_atr_k из боевого конфига LTC был для него невидим: отбор
        # проверял сетку, которая дышит с волатильностью, а торговала бы
        # статическая. Базовые веса (k=0) тождественны прежней формуле.
        self.leg_weights = gridlib.leg_weights(mult, levels, None, None, 0.0)
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
        # ADX-гейт (v12): вход запрещён, пока сила тренда выше порога.
        # Считается indicators.calc_adx — тем же кодом, что в отборе.
        self.adx_gate = self.p.get("adx_gate", 0)
        self.adx_n = self.p.get("adx_n", 14)
        self.adx_max = self.p.get("adx_max", 30.0)
        # --- подвижная сетка и подвижные SL/TP (волна v10) ---
        # Значения по умолчанию — gridlib.OFF10, то есть прежнее поведение.
        # Формулы берутся из gridlib, общего с бэктест-движком: если бот
        # посчитает сетку иначе, чем evolution2.run5, то отбор проверял одну
        # стратегию, а деньгами торговала бы другая.
        self.grid_mode = self.p.get("grid_mode", gridlib.OFF10["grid_mode"])
        self.grid_span = self.p.get("grid_span", gridlib.OFF10["grid_span"])
        self.grid_spread = self.p.get("grid_spread", gridlib.OFF10["grid_spread"])
        self.grid_atr_k = self.p.get("grid_atr_k", gridlib.OFF10["grid_atr_k"])
        self.grid_w_atr_k = self.p.get("grid_w_atr_k", gridlib.OFF10["grid_w_atr_k"])
        self.grid_retune = self.p.get("grid_retune", gridlib.OFF10["grid_retune"])
        self.tp_atr_k = self.p.get("tp_atr_k", gridlib.OFF10["tp_atr_k"])
        self.trail_k = self.p.get("trail_k", gridlib.OFF10["trail_k"])
        self.trail_start = self.p.get("trail_start", gridlib.OFF10["trail_start"])
        # список генов, которым нужна «норма» ATR, обязан совпадать с движком
        # (evolution2.run5): забытый ген молча умирает — vol_factor вернёт 1.0
        self.needs_atr_ref = bool(self.grid_atr_k or self.tp_atr_k
                                  or self.grid_w_atr_k)
        # Новостей здесь НЕТ и быть не должно: фьючерсная сетка торгует
        # ровно то, что проверяет бэктест, а историю новостей движок отбора
        # воспроизводить не умеет. Сам новостной фон реализован (news_mcp.py,
        # newsfeed.py) и виден на витрине, но НИ К ОДНОМУ торгующему боту не
        # подключён — спотового бота в проекте нет.
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
        # Гейт по собственной волатильности. 0 = ВЫКЛЮЧЕН, и тогда поведение
        # бота не меняется ни на бит — та же договорённость, что у OFF7/OFF8.
        # Значение 0.5 означает «торговать только когда текущая волатильность
        # не ниже медианы своих последних VOL_GATE_LOOK баров».
        #
        # Зачем: проверка на 15 конфигах показала, что такой запрет надёжно
        # снижает просадку (12 улучшений из 15, знаковый критерий p=0.018), но
        # НЕ добавляет дохода (8 из 15 — монетка). Это инструмент управления
        # риском, а не источник прибыли, и включать его надо с этим пониманием.
        self.vol_gate = float(self.p.get("vol_gate", 0.0))
        self.vol_gate_look = int(self.p.get("vol_gate_look", 500))
        self.vol_gate_win = int(self.p.get("vol_gate_win", 96))
        # фильтр графических паттернов: 0=выкл, 1=требовать совпадение,
        # 2=запрещать только явное противоречие (мягкий вариант)
        self.pattern_gate = self.p.get("pattern_gate", 0)
        self._regime_cache = (0, None)  # (day, "bull"|"range"|"bear")
        # Гейт по дневной средней (trend_gate.py): ключа нет = выключен.
        self.trend_days = int(self.p.get("trend_days", 0) or 0)
        self._trend_cache = (0, 0)      # (day, режим); сбой НЕ кэшируется
        self._macro_needed = (self.spx_long_min > -900 or
                              self.dxy_long_max < 900 or
                              self.gold_long_max < 900)
        self._macro_cache = (0, None)  # (day_ts, dict)

        self.last_candle_ts = 0
        self.pos = None
        # какое расхождение с биржей уже разобрано: (сторона, размер, время
        # открытия). Нужно, чтобы одна и та же ручная позиция не ставила
        # 24-часовую паузу заново на КАЖДОМ цикле (см. handle_orphan).
        self.orphan_key = None
        self.consec_losses = 0
        self.paused_until = 0     # kill switch, unix ms
        self.cooldown_until = 0   # турбо: пауза после стопа, unix ms
        self.halted = False       # стоп по просадке: снимается только руками
        self.peak_balance = self.virtual_balance   # пик капитала для просадки
        self.day_key = self._day_key()
        self.day_start_balance = self.virtual_balance
        self.day_pnl = 0.0
        self.api_errors = 0        # подряд идущие ошибки цикла
        self.state_file = os.path.join(
            config.STATE_DIR, f"botstate_{self.symbol}_{self.mode}.json")
        self.load_state()

    # ---------- состояние между запусками ----------

    @staticmethod
    def _day_key():
        """Календарные сутки UTC — общая точка отсчёта для дневного лимита."""
        return int(time.time()) // 86400

    def load_state(self):
        """Капитал, пауза kill switch, серия убытков, дневной лимит и ОТКРЫТАЯ
        позиция должны переживать перезапуск: иначе после рестарта виртуальный
        капитал возвращается к стартовому, 24-часовая пауза после трёх убытков
        снимается досрочно (ровно та защита, ради которой она вводилась), а
        позиция на бирже остаётся без ведения — без стопа, тейка и таймаута."""
        # первый ли это запуск: в реале капитал такого бота ещё не известен и
        # его надо взять с кошелька, а не с бумажного START_BALANCE
        self._fresh_state = True
        try:
            with open(self.state_file, encoding="utf-8") as fh:
                st = json.load(fh)
        except FileNotFoundError:
            return              # обычный первый запуск — это не авария
        except (OSError, ValueError) as e:
            # Файл ЕСТЬ, но не читается: сорванная запись, полный диск, чужая
            # правка. Молчаливый выход отсюда неотличим от первого запуска —
            # и тогда рестарт снимает 24-часовую паузу, стоп по просадке и
            # ведение позиции ровно в момент, когда защита нужнее всего.
            # Поэтому: кричим, сохраняем испорченный файл для разбора и
            # запрещаем НОВЫЕ входы (halted). Открытую позицию это не бросает:
            # её подхватит сверка с биржей, а снять запрет может человек,
            # убрав "halted" из файла состояния.
            self.log.error("ФАЙЛ СОСТОЯНИЯ ИСПОРЧЕН (%s): %s. Новые входы "
                           "запрещены до разбора человеком.",
                           self.state_file, e)
            bad = f"{self.state_file}.bad-{int(time.time())}"
            try:
                os.replace(self.state_file, bad)
                self.log.error("Испорченный файл сохранён как %s", bad)
            except OSError as e2:
                self.log.error("Не удалось отложить испорченный файл: %s", e2)
            self.notifier.send(
                f"файл состояния испорчен ({e}) — новые входы запрещены, "
                f"копия: {bad}", key="state_broken")
            self.halted = True
            return
        self._fresh_state = False
        self.virtual_balance = float(st.get("virtual_balance",
                                            self.virtual_balance))
        self.consec_losses = int(st.get("consec_losses", 0))
        self.paused_until = int(st.get("paused_until", 0))
        self.cooldown_until = int(st.get("cooldown_until", 0))
        self.halted = bool(st.get("halted", False))
        self.peak_balance = float(st.get("peak_balance", self.virtual_balance))
        self.day_key = int(st.get("day_key", self.day_key))
        self.day_start_balance = float(st.get("day_start_balance",
                                              self.virtual_balance))
        self.day_pnl = float(st.get("day_pnl", 0.0))
        # последняя ОБРАБОТАННАЯ закрытая свеча: без неё рестарт заново
        # прогоняет ту же свечу (в DRY_RUN — второй раз исполняет сетку и
        # искажает кривую капитала, в реале — заново дёргает вход/лимиты)
        self.last_candle_ts = int(st.get("last_candle_ts", 0))
        self.orphan_key = st.get("orphan_key")
        pos = st.get("pos")
        if pos:
            # кортежи после JSON становятся списками — возвращаем как было,
            # иначе распаковка `ap, aq = ...` начнёт зависеть от источника
            pos["fills"] = [tuple(x) for x in pos.get("fills", [])]
            pos["adds"] = [tuple(x) for x in pos.get("adds", [])]
            self.pos = pos
        left = (self.paused_until - time.time() * 1000) / 3600000
        self.log.info("Состояние восстановлено: капитал $%.2f, убытков подряд "
                      "%d%s%s%s", self.virtual_balance, self.consec_losses,
                      f", пауза ещё {left:.1f}ч" if left > 0 else "",
                      ", ОСТАНОВЛЕН по просадке" if self.halted else "",
                      f", позиция {self.pos['side']}" if self.pos else "")

    def save_state(self):
        """Запись АТОМАРНАЯ: временный файл рядом + os.replace.

        Обычная запись «на месте» оставляет обрезанный JSON, если процесс
        умрёт (или сервер перезагрузят) между открытием файла и сбросом
        буфера. Разбирать такой файл нечем — бот стартует с чистого листа и
        теряет ведение живой позиции. os.replace на одном томе атомарен и на
        Windows, и на Linux."""
        data = dict(virtual_balance=round(self.virtual_balance, 6),
                    consec_losses=self.consec_losses,
                    paused_until=self.paused_until,
                    cooldown_until=self.cooldown_until,
                    halted=self.halted,
                    peak_balance=round(self.peak_balance, 6),
                    day_key=self.day_key,
                    day_start_balance=round(self.day_start_balance, 6),
                    day_pnl=round(self.day_pnl, 6),
                    last_candle_ts=self.last_candle_ts,
                    orphan_key=self.orphan_key,
                    pos=self.pos,
                    saved_ts=int(time.time() * 1000))
        try:
            d = os.path.dirname(self.state_file) or "."
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".botstate-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.state_file)
            except Exception:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise
        except OSError as e:
            self.log.warning("не удалось сохранить состояние: %s", e)

    # ---------- утилиты ----------

    def rq(self, qty):
        """Количество, округлённое ВНИЗ к шагу лота (Decimal: 0.3/0.1 в float
        даёт 2.9999… и обычный int() отрезал бы целое колено)."""
        q = (Decimal(str(qty)) / self.qty_step).to_integral_value(
            rounding=ROUND_FLOOR) * self.qty_step
        return float(q)

    def rp(self, price):
        """Цена, ПРИТЯНУТАЯ к сетке тика и уже готовая к отправке на биржу.

        Форматирования по числу знаков мало: у BTC tickSize "0.10", у SOL
        "0.010" — цена обязана быть кратна шагу, а не просто иметь нужное
        число знаков после точки. Округляем к ближайшему узлу сетки (сдвиг
        не больше половины тика, для стопа и тейка это доли базисного пункта).
        """
        q = (Decimal(str(price)) / self.tick).quantize(
            Decimal(1), rounding=ROUND_HALF_UP) * self.tick
        return f"{q:.{self.price_dec}f}"

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
        if self.adx_gate:
            need = max(need, 4 * self.adx_n + 20)   # прогрев ADX ~2n + запас
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

    def liq_price(self):
        """Цена ликвидации текущей позиции — формула движка (evolution2.liq_price).

        Условие одно: свободных средств осталось ровно на maintenance margin.
        Базу для самого maintenance margin берём БОЛЬШУЮ из двух (по цене входа
        для лонга, по текущей для шорта) — так порог наступает не позже
        биржевого в обе стороны; вывод формул см. в evolution2.liq_price.

        Занятая маржа считается не из весов колен, а из номинала: биржа держит
        под позицией ровно notional/lev, поэтому mused/q == avg/lev тождественно
        и округление объёмов к шагу лота на цену ликвидации не влияет.
        """
        avg, _ = self.avg_entry()
        sgn = 1 if self.pos["side"] == "L" else -1
        d = avg / self.p["lev"]
        if sgn == 1:
            return avg - d + MMR * avg
        return (avg + d) / (1.0 + MMR)

    def stop_price(self):
        if self.turbo:  # короткий стоп от средней
            avg, _ = self.avg_entry()
            sgn = 1 if self.pos["side"] == "L" else -1
            return avg * (1 - sgn * self.p["stop_k"] * self.pos["atr0"])
        return self.pos["stop"]   # за уровнем либо подтянутый трейлингом

    # ---------- биржа ----------

    def set_leverage(self):
        if config.DRY_RUN:
            # плечо — настройка СЧЁТА, а не ордер: в бумажном режиме менять её
            # нельзя. Вызов и так стоит под проверкой в run(), но защита должна
            # быть в самой функции — иначе любой новый вызов станет дырой
            return
        try:
            self.session.set_leverage(category="linear", symbol=self.symbol,
                                      buyLeverage=str(self.p["lev"]),
                                      sellLeverage=str(self.p["lev"]))
        except Exception as e:
            if "110043" not in str(e):
                raise

    def exchange_position_full(self):
        """Сырая позиция с биржи или None. При сверке нужны не только сторона
        и размер, но и stopLoss/takeProfit/createdTime — по ним восстанавливается
        ведение позиции, которую бот «проспал»."""
        r = self.session.get_positions(category="linear", symbol=self.symbol)
        for pp in r["result"]["list"]:
            if float(pp["size"] or 0) > 0:
                return pp
        return None

    def exchange_position(self):
        pp = self.exchange_position_full()
        if not pp:
            return None, 0.0, 0.0
        return pp["side"], float(pp["size"]), float(pp["avgPrice"])

    def check_position_mode(self):
        """positionIdx=0, которым бот шлёт все ордера, существует только в
        одностороннем режиме (one-way). На хедж-счёте позиции живут под
        индексами 1 и 2, и КАЖДЫЙ ордер отлетает с 10001 — бот бесконечно
        крутит цикл ошибок, не понимая, что дело в настройке счёта."""
        r = self.session.get_positions(category="linear", symbol=self.symbol)
        idx = {int(p.get("positionIdx", 0) or 0) for p in r["result"]["list"]}
        if idx - {0}:
            raise SystemExit(
                f"{self.symbol}: счёт в режиме хеджирования (positionIdx "
                f"{sorted(idx)}), а бот торгует только в одностороннем. "
                f"Bybit -> Настройки позиции -> Режим позиции -> "
                f"«Односторонний», затем перезапусти бота.")

    def wallet_snapshot(self):
        """(equity, занятая маржа) кошелька либо None при ошибке.

        Занятая маржа нужна не «для отчёта»: она включает позиции СОСЕДНИХ
        ботов на том же счёте, и только по ней видно, влезает ли ещё один
        вход в общий потолок."""
        try:
            acc = self.session.get_wallet_balance(
                accountType="UNIFIED")["result"]["list"][0]
            equity = float(acc["totalEquity"])
            used = float(acc.get("totalInitialMargin") or 0)
            return equity, used
        except Exception as e:      # noqa: BLE001
            # Молчаливый фолбэк на START_BALANCE — как раз тот случай, когда
            # бот считает капитал по бумажке и может заявить объём, которого
            # на счёте нет. Об этом обязаны узнать.
            self.log.warning("Кошелёк недоступен (%s) — считаю капитал "
                             "по последнему известному значению", e)
            self.notifier.send(f"кошелёк недоступен: {e}", key="wallet")
            return None

    def cancel_own_orders(self, reason=""):
        """Снять ТОЛЬКО свои лимитки сетки — по сохранённым orderId.

        cancel_all_orders снёс бы и ручные ордера владельца по этой монете.
        Точечная отмена дешёвая: колен максимум два-три, их id известны с
        момента постановки.

        Признак «свои id известны» — НАЛИЧИЕ ключа add_ids, а не непустой
        список. Проверять на непустоту нельзя: функция сама опустошает список
        после отмены, и второй же вызов (перестановка сетки, потом закрытие
        цикла) вырождался бы в глобальный сброс — то есть сносил бы ручные
        ордера владельца. Пустой список означает ровно то, что написано:
        своих лимиток на бирже нет и отменять нечего. Глобальный сброс
        остаётся только для состояния, записанного старой версией бота, где
        ключа add_ids ещё не существовало.
        """
        if config.DRY_RUN:
            return
        pos = self.pos or {}
        if "add_ids" in pos:
            for oid in list(pos.get("add_ids") or []):
                try:
                    self.session.cancel_order(category="linear",
                                              symbol=self.symbol, orderId=oid)
                except Exception as e:      # noqa: BLE001
                    # 110001 = ордера уже нет (исполнился или снят биржей)
                    if "110001" not in str(e):
                        self.log.warning("Не снялась лимитка %s: %s", oid, e)
            pos["add_ids"] = []
            return
        try:
            self.session.cancel_all_orders(category="linear", symbol=self.symbol)
            if reason:
                self.log.info("Сняты все ордера по %s (%s): свои id неизвестны",
                              self.symbol, reason)
        except Exception as e:      # noqa: BLE001
            self.log.warning("Не удалось снять ордера: %s", e)

    # ---------- внешние сигналы ----------

    # Возвращается, когда СПРОСИТЬ не удалось. Это не то же самое, что
    # «биржа не знает ставку»: там ответ None, и бэктест на None фильтр не
    # применяет. Сбой связи бэктест не моделирует вовсе, поэтому вести себя
    # как «фильтра нет» здесь нельзя — см. entry_allowed.
    FUND_UNKNOWN = object()

    def get_funding(self):
        """Последняя РАСЧЁТНАЯ ставка фандинга в % за 8ч.

        ЗДЕСЬ БЫЛА ПОДМЕНА ВЕЛИЧИНЫ. Стояло get_tickers -> fundingRate, а это
        ПРЕДСТОЯЩАЯ ставка следующего расчёта: она ещё меняется до момента
        списания. Бэктест же смотрит историю расчётов
        (ext_data.fetch_funding -> step_lookup) и берёт ПОСЛЕДНЮЮ УЖЕ
        СОСТОЯВШУЮСЯ ставку на момент бара. Две разные величины на одном и
        том же пороге дают разные решения: по LTCUSDT/final_wf они расходятся
        на 19.9% баров обучающей половины и на 38.4% баров проверочной, а итог
        холдоута падает с +56.61% до +40.63%.

        Поэтому здесь тот же источник, что у бэктеста: история расчётов.
        Предстоящую ставку брать нельзя не потому, что она хуже, а потому что
        по ней не посчитано ни одно число на витрине.

        Ставки у Bybit — доли; в проекте фандинг хранится в ПРОЦЕНТАХ, и
        пороги конфигов тоже в процентах, поэтому x100.
        """
        try:
            r = self.session.get_funding_rate_history(
                category="linear", symbol=self.symbol, limit=1)
            rows = r["result"]["list"]
            if not rows:
                return None
            return float(rows[0]["fundingRate"]) * 100
        except Exception as e:      # noqa: BLE001
            self.log.warning("Не удалось получить фандинг по %s: %s",
                             self.symbol, e)
            return self.FUND_UNKNOWN

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

    def get_trend_regime(self):
        """Режим для гейта trend_days: +1 рост, -1 падение, 0 неизвестно.

        Считается ТЕМ ЖЕ trend_gate.regime_last, что и бэктест, по закрытым
        дневным свечам, один раз в день. Сбой запроса даёт 0 и НЕ кэшируется:
        при 0 вход запрещён (фейл-клоуз, как у фандинга), а следующий бар
        спросит биржу снова.
        """
        import time as _t
        day = int(_t.time()) // 86400
        if self._trend_cache[0] == day:
            return self._trend_cache[1]
        try:
            r = self.session.get_kline(category="linear", symbol=self.symbol,
                                       interval="D", limit=self.trend_days + 5)
            rows = r["result"]["list"][::-1][:-1]   # закрытые дни, от старых
            closes = [float(x[4]) for x in rows]
            regime = trend_gate.regime_last(closes, self.trend_days)
        except Exception as e:      # noqa: BLE001
            self.log.warning("Гейт тренда: дневные свечи недоступны (%s) — "
                             "режим неизвестен, вход запрещён", e)
            return 0
        if regime != self._trend_cache[1]:
            self.log.info("Режим по SMA%d: %s", self.trend_days,
                          {1: "рост", -1: "падение", 0: "неизвестно"}[regime])
        self._trend_cache = (day, regime)
        return regime

    def vol_rank(self, cs):
        """Место текущей волатильности среди своих последних баров, 0..1.

        Считается ТОЛЬКО по закрытым барам: cs[-1] — последняя закрытая свеча.
        Перцентиль, а не абсолютное значение, потому что абсолютная
        волатильность несравнима между монетами и между эпохами: 2% в день у
        DOGE и у BTC значат разное, и 2% в 2023 и в 2026 тоже.

        None, если баров не хватает — тогда гейт не применяется. Отсутствие
        данных не повод запрещать вход: запрет по незнанию вырезал бы начало
        каждой сессии.
        """
        need = self.vol_gate_look + self.vol_gate_win + 2
        if len(cs) < need:
            return None
        closes = [float(c[4]) for c in cs[-need:]]
        rets = []
        for i in range(1, len(closes)):
            a, b = closes[i - 1], closes[i]
            if a > 0 and b > 0:
                rets.append(math.log(b / a))
            else:
                rets.append(0.0)
        w = self.vol_gate_win
        if len(rets) < w + self.vol_gate_look:
            return None
        vols = []
        for i in range(w, len(rets) + 1):
            seg = rets[i - w:i]
            vols.append(math.sqrt(sum(x * x for x in seg) / w))
        if len(vols) < 2:
            return None
        cur = vols[-1]
        hist = vols[-(self.vol_gate_look + 1):]
        return sum(1 for v in hist if v <= cur) / float(len(hist))

    def entry_allowed(self, side, cs):
        """Внешние фильтры входа. True = вход разрешён."""
        # Гейт по волатильности — до всех сетевых запросов: если вход и так
        # запрещён, дёргать биржу за funding и OI незачем.
        if self.vol_gate > 0:
            vr = self.vol_rank(cs)
            if vr is not None and vr < self.vol_gate:
                self.log.info("Гейт волатильности: %.2f < %.2f — вход отменён",
                              vr, self.vol_gate)
                return False
        if self.adx_gate:
            adx = indicators.calc_adx(cs[-(4 * self.adx_n + 20):], self.adx_n)[-1]
            if adx is not None and adx > self.adx_max:
                self.log.info("Фильтр ADX: %.1f > %.1f (тренд слишком силён) "
                              "— вход отменён", adx, self.adx_max)
                return False
        if self.trend_days > 0:
            r = self.get_trend_regime()
            if r == 0 or (side == "S" and r > 0) or (side == "L" and r < 0):
                self.log.info("Гейт тренда SMA%d: режим «%s» — %s отменён",
                              self.trend_days,
                              {1: "рост", -1: "падение", 0: "неизвестен"}[r],
                              "шорт" if side == "S" else "лонг")
                return False
        f = self.get_funding() if (self.fund_long_max < 900 or
                                   self.fund_short_min > -900) else None
        if f is self.FUND_UNKNOWN:
            # ФЕЙЛ-КЛОУЗ. Раньше сбой запроса возвращал None, проверка стояла
            # под «если значение есть», и бот молча ВХОДИЛ там, где гейт мог
            # бы не пустить. Пропуск входа стоит ноль, вход вслепую — деньги.
            self.log.warning("Фильтр funding: ставка неизвестна (сбой запроса)"
                             " — вход отменён")
            return False
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

    # Замок «очередь на вход» между процессами одного счёта.
    LOCK_WAIT_SEC = 15      # сколько ждать чужой вход, прежде чем идти без очереди
    LOCK_STALE_SEC = 60     # после этого замок считается брошенным (процесс убит)

    def _lock_path(self):
        return os.path.join(config.STATE_DIR, "entry.lock")

    def entry_gate(self):
        """Контекст «я сейчас вхожу»: выстраивает боты одного счёта в очередь.

        Потолок общей маржи (margin_allowed) считается по ответу биржи, а
        занятая маржа там появляется только ПОСЛЕ исполнения ордера. Пять
        ботов, стартовавших одновременно, спрашивают кошелёк в одну и ту же
        секунду, все видят used=0 и все проходят проверку — потолка как будто
        нет. Замок пропускает их по одному: следующий спрашивает кошелёк уже
        после того, как предыдущий вошёл.

        Замок НИКОГДА не останавливает торговлю навсегда: не дождались за
        LOCK_WAIT_SEC, файл протух (процесс убит с замком в руках) или ФС не
        даёт его создать — идём дальше с предупреждением. Перебор маржи в
        редкой гонке дешевле, чем портфель, застрявший на файле-замке.
        """
        return _EntryGate(self)

    def account_state(self, max_age=60):
        """(equity, занятая маржа) с кэшем: в DRY_RUN — виртуальный капитал.

        Кэш на минуту, потому что и вход, и риск-лимиты спрашивают одно и то
        же, а лимит запросов к приватному API общий на весь портфель ботов.
        """
        if config.DRY_RUN:
            return self.virtual_balance, 0.0
        now = time.monotonic()
        if self._equity_cache[1] is None or now - self._equity_cache[0] > max_age:
            snap = self.wallet_snapshot()
            if snap is None:
                if self._equity_cache[1] is None:
                    # первое же обращение и оно неудачное: считать не по чему
                    self.log.warning("Капитал неизвестен — беру START_BALANCE "
                                     "$%.2f как заглушку", config.START_BALANCE)
                    snap = (config.START_BALANCE, 0.0)
                else:
                    snap = self._equity_cache[1]
            self._equity_cache = (now, snap)
        return self._equity_cache[1]

    def margin_total(self):
        """Маржа на цикл. "fixed" — константа (как в бэктестах, линейный
        рост); "percent" — доля текущего капитала (реинвест). Капитал:
        DRY_RUN — виртуальный (старт config.START_BALANCE), реал — equity
        кошелька (кэш 60 сек)."""
        if config.MARGIN_MODE != "percent":
            return config.MARGIN_USDT
        equity, _ = self.account_state()
        return max(1.0, equity * config.MARGIN_FRACTION)

    def margin_allowed(self, want):
        """Влезает ли заявка на маржу `want` в общий потолок счёта.

        Корень проблемы: при MARGIN_MODE="percent" каждый бот берёт долю от
        ОБЩЕГО equity, и пять ботов по 25% просят 125% капитала. Комментарий
        в config этого не остановит — останавливает эта проверка.

        Считаем по потенциалу цикла (маржа ВСЕХ колен, а не только первого):
        колена доливаются автоматически, и если места под них нет, позиция
        останется без сетки — то есть без того, ради чего вход и делался.
        Возвращает (можно ли, текст причины).
        """
        # СВЕЖИЙ ответ биржи, а не минутный кэш: соседний бот мог войти
        # секунду назад, и по кэшу его маржа ещё «не занята». Кэш экономит
        # запросы в рутине, но перед входом экономить на этом нельзя.
        equity, used = self.account_state(max_age=0)
        cap = equity * config.MAX_TOTAL_MARGIN_FRACTION
        if used + want > cap:
            return False, (f"занято ${used:.2f} + заявка ${want:.2f} > "
                           f"потолка ${cap:.2f} "
                           f"({config.MAX_TOTAL_MARGIN_FRACTION:.0%} от "
                           f"${equity:.2f})")
        return True, ""

    def leg_margins(self, m_total, atr_now=None, atr_ref=None):
        """Маржа по коленам. Веса берёт gridlib.leg_weights — тот же код и те
        же данные (ATR ряда gridlib и его «норма»), что у движка отбора, иначе
        ген grid_w_atr_k боевого конфига LTC работал бы только в бэктесте."""
        w = gridlib.leg_weights(self.mult, self.levels, atr_now, atr_ref,
                                self.grid_w_atr_k)
        return w, [m_total * x for x in w]

    def order_ok(self, qty, price, what):
        """Пройдёт ли заявка минимумы биржи (количество И номинал)."""
        if qty < self.min_qty:
            self.log.warning("%s: объём %s < минимума %s — пропущено "
                             "(мало маржи)", what, qty, self.min_qty)
            return False
        if self.min_notional and qty * price < self.min_notional:
            self.log.warning("%s: номинал %.2f USDT < минимума %.2f — "
                             "пропущено (биржа отклонит заявку)",
                             what, qty * price, self.min_notional)
            return False
        return True

    def enter(self, side, price, r_low, r_high, atr, atr_now=None, atr_ref=None):
        sgn = 1 if side == "L" else -1
        if config.DRY_RUN:
            # Бумажный вход исполняется по той же цене, что и в движке отбора
            # (evolution2: px = c*(1+sgn*SLIP)) — рыночный ордер по закрытию
            # свечи не бывает бесплатным. Без этого DRY_RUN систематически
            # рисует результат лучше того, что проверял бэктест, и расхождение
            # «бумага против биржи» замечалось бы уже на реальных деньгах.
            price = price * (1 + sgn * SLIP)
        # Проверка потолка маржи и сам рыночный вход идут ПОД ЗАМКОМ: иначе
        # соседние боты того же счёта считают потолок по одному и тому же
        # «ещё не занято» (см. entry_gate).
        with self.entry_gate():
            m_total = self.margin_total()
            ok, why = self.margin_allowed(m_total)
            if not ok:
                # заведомо отклоняемый ордер отправлять нельзя: биржа ответит
                # ошибкой, а бот получит серию «непонятных» сбоев вместо причины
                self.log.warning("Вход пропущен — не влезает в потолок маржи: %s",
                                 why)
                self.notifier.send(f"вход пропущен, потолок маржи: {why}",
                                   key="margin_cap")
                return
            weights, leg_margins = self.leg_margins(m_total, atr_now, atr_ref)
            qty0 = self.rq(leg_margins[0] * self.p["lev"] / price)
            if not self.order_ok(qty0, price, "Вход"):
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

            # Позиция ФИКСИРУЕТСЯ ТОЛЬКО ПОСЛЕ успешного ордера. Раньше self.pos
            # присваивался до place_order: если биржа отклоняла заявку или рвалась
            # связь, бот считал себя в рынке, вёл несуществующую позицию, тянул ей
            # стопы и в конце «закрывал» её с выдуманным PnL. Пока ордер не ушёл,
            # позиция живёт в локальной переменной и при любой ошибке исчезает.
            # adds пока ПУСТ намеренно: колена ещё не выставлены, а в состояние
            # можно писать только то, что реально есть на бирже.
            pos = dict(side=side, fills=[(price, qty0)], stop=stop, adds=[],
                       opened_ts=int(time.time() * 1000), atr0=atr,
                       tp_eff=tp_eff, vol_k=vol_k, entry_px=price, best=price,
                       leg_w=weights, add_ids=[],
                       # накопленные издержки цикла: комиссия входа, дальше
                       # добавляются мейкерские комиссии колен и фандинг
                       fees=qty0 * price * TAKER)
            self.pos = pos          # tp_price/stop_price читают self.pos
            self._last_pushed = (None, None)
            tp = self.tp_price()
            sl = self.stop_price()

            if not config.DRY_RUN:
                order_side = "Buy" if side == "L" else "Sell"
                try:
                    r = self.session.place_order(
                        category="linear", symbol=self.symbol, side=order_side,
                        orderType="Market", qty=str(qty0),
                        stopLoss=self.rp(sl), takeProfit=self.rp(tp))
                    if int(r.get("retCode", 0)) != 0:
                        raise RuntimeError(f"retCode {r.get('retCode')}: "
                                           f"{r.get('retMsg')}")
                except Exception as e:      # noqa: BLE001
                    self.pos = None         # откат: цикла не было
                    self.log.exception("ВХОД НЕ СОСТОЯЛСЯ (ордер отклонён)")
                    self.notifier.send(f"вход не состоялся: {e}", key="entry_fail")
                    return
            else:
                self.log.info("[DRY_RUN] ордера не отправлены")

            # ПОЗИЦИЯ УЖЕ НА БИРЖЕ — записываем состояние немедленно, до
            # постановки колен. Обрыв процесса между входом и коленами иначе
            # оставлял бы позицию, которой в состоянии нет: следующий старт
            # видит «чужую» позицию и при CLOSE_UNEXPECTED_POSITION закрывает
            # её по рынку. Колена допишутся вторым сохранением ниже.
            self.save_state()

        self.log.info(">>> ВХОД %s x%d: %s по ~%s | стоп %s | тейк %s | ATR %.2f%%",
                      "LONG" if side == "L" else "SHORT", self.p["lev"],
                      qty0, self.rp(price), self.rp(sl), self.rp(tp), atr * 100)
        for i, (ap, aq) in enumerate(adds):
            self.log.info("    сетка %d: лимитка %s по %s", i + 2, aq, self.rp(ap))

        if not config.DRY_RUN:
            order_side = "Buy" if side == "L" else "Sell"
            placed = []
            for ap, aq in adds:
                if not self.order_ok(aq, ap, "Колено сетки"):
                    continue        # выставить нельзя — и в ведении его быть не должно
                try:
                    r = self.session.place_order(
                        category="linear", symbol=self.symbol, side=order_side,
                        orderType="Limit", qty=str(aq), price=self.rp(ap))
                    pos["add_ids"].append(r["result"]["orderId"])
                    placed.append((ap, aq))
                except Exception as e:      # noqa: BLE001
                    # позиция уже открыта и защищена стопом — вход не
                    # отменяем, но колено, которого нет на бирже, выкидываем
                    # из ведения: иначе бот ждал бы доливку, которой не будет
                    self.log.exception("Колено сетки по %s не выставлено",
                                       self.rp(ap))
                    self.notifier.send(f"колено сетки не выставлено: {e}",
                                       key="leg_fail")
            pos["adds"] = placed
        else:
            # в DRY_RUN колена, которые биржа не приняла бы, тоже не живут:
            # виртуальный результат обязан совпадать с исполнимым
            pos["adds"] = [(ap, aq) for ap, aq in adds
                           if self.order_ok(aq, ap, "Колено сетки")]

        self.save_state()       # позиция должна пережить перезапуск
        self.notifier.send(
            f"вход {'LONG' if side == 'L' else 'SHORT'} x{self.p['lev']} "
            f"{qty0} по {self.rp(price)}, стоп {self.rp(sl)}, тейк {self.rp(tp)}")

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
        # веса колен зафиксированы на входе — как в движке (pos["m_k"]):
        # пересчитывать их по сегодняшней волатильности значило бы менять
        # раскладку маржи внутри уже открытого цикла
        w = self.pos.get("leg_w") or self.leg_weights
        leg_margins = [m_total * x for x in w]
        fresh_adds = [(p, self.rq(leg_margins[k0 + j] * self.p["lev"] / p))
                      for j, p in enumerate(fresh)]
        self.log.info("Сетка переставлена: %s -> %s",
                      [self.rp(x) for x in old],
                      [self.rp(x) for x, _ in fresh_adds])
        if not config.DRY_RUN:
            self.cancel_own_orders("перестановка сетки")
            order_side = "Buy" if self.pos["side"] == "L" else "Sell"
            placed = []
            for ap, aq in fresh_adds:
                if not self.order_ok(aq, ap, "Колено сетки"):
                    continue
                try:
                    r = self.session.place_order(
                        category="linear", symbol=self.symbol, side=order_side,
                        orderType="Limit", qty=str(aq), price=self.rp(ap))
                    self.pos["add_ids"].append(r["result"]["orderId"])
                    placed.append((ap, aq))
                except Exception as e:      # noqa: BLE001
                    self.log.exception("Колено сетки по %s не выставлено",
                                       self.rp(ap))
                    self.notifier.send(f"колено сетки не выставлено: {e}",
                                       key="leg_fail")
            fresh_adds = placed
        else:
            fresh_adds = [(ap, aq) for ap, aq in fresh_adds
                          if self.order_ok(aq, ap, "Колено сетки")]
        self.pos["adds"] = fresh_adds
        self.save_state()

    def roll_day(self):
        """Перевод дневного счётчика на новые сутки UTC.

        Вызывается и по свече, и по завершении цикла: без этого день, в
        котором не было ни одной сделки, не сбрасывал бы дневной убыток и
        лимит «съедал» бы уже следующие сутки."""
        today = self._day_key()
        if today != self.day_key:
            self.day_key = today
            self.day_start_balance = self.virtual_balance
            self.day_pnl = 0.0

    def unrealized(self, price):
        """Плавающий PnL открытой позиции по текущей цене, USDT.

        Считается по той же средней и тому же объёму, что и реальный выход:
        (цена - средняя) * объём для лонга и наоборот для шорта. Издержки
        будущего выхода сюда не входят — они станут известны при закрытии,
        и завышать минус заранее не нужно.
        """
        if not self.pos:
            return 0.0
        avg, qty = self.avg_entry()
        sgn = 1 if self.pos["side"] == "L" else -1
        return sgn * (price - avg) * qty

    def float_risk_check(self, price):
        """Следит за плавающим минусом ОТКРЫТОЙ позиции, а не только за
        закрытыми циклами.

        Зачем отдельно от risk_limits: капитал бота (virtual_balance) меняется
        только в finish_cycle, поэтому позиция может уйти глубоко в минус, а
        стоп по просадке и дневной лимит этого не заметят вовсе. Биржа же
        ликвидирует именно по плавающему минусу.

        По умолчанию это только громкое предупреждение: порог остановки
        (FLOAT_DD_HALT_FRACTION) выключен, потому что общий порог просадки
        подбирался под закрытую метрику. Владелец включает его осознанно.
        """
        if not self.pos or self.virtual_balance <= 0:
            return
        pnl = self.unrealized(price)
        if pnl >= 0:
            return
        frac = -pnl / self.virtual_balance
        halt_at = config.FLOAT_DD_HALT_FRACTION
        if halt_at > 0 and frac >= halt_at and not self.halted:
            self.halted = True
            msg = (f"плавающий минус {pnl:+.2f} USDT = {frac:.1%} капитала "
                   f"при пороге {halt_at:.0%} — новые входы ОСТАНОВЛЕНЫ "
                   f"(открытая позиция продолжает вестись)")
            self.log.error("СТОП ПО ПЛАВАЮЩЕЙ ПРОСАДКЕ: %s", msg)
            self.notifier.send(msg, key="float_halt")
            self.save_state()
            return
        alert_at = config.FLOAT_DD_ALERT_FRACTION
        if alert_at > 0 and frac >= alert_at:
            self.log.warning("ПЛАВАЮЩИЙ МИНУС: %+.2f USDT = %.1f%% капитала "
                             "(средняя %s, цена %s)", pnl, frac * 100,
                             self.rp(self.avg_entry()[0]), self.rp(price))
            self.notifier.send(
                f"плавающий минус {pnl:+.2f} USDT = {frac:.1%} капитала",
                key="float_dd", min_gap=config.FLOAT_DD_ALERT_GAP_SEC)

    def risk_limits(self):
        """Дневной лимит потерь и стоп по просадке — после каждого цикла.

        Kill switch по серии убытков не ловит ни один провальный день (три
        подряд убытка могут стоить меньше, чем один большой), ни медленное
        сползание капитала месяцами. Считаем от кривой капитала САМОГО бота:
        в DRY_RUN она начинается с START_BALANCE, в реале — с equity кошелька
        на первом запуске (см. seed_capital), и оба варианта переживают
        перезапуск в файле состояния."""
        now = int(time.time() * 1000)
        limit = config.DAILY_LOSS_LIMIT_FRACTION * self.day_start_balance
        if limit > 0 and self.day_pnl <= -limit and now >= self.paused_until:
            self.paused_until = now + config.DAILY_LOSS_PAUSE_HOURS * 3600 * 1000
            msg = (f"дневной лимит: {self.day_pnl:+.2f} USDT за сутки при "
                   f"пороге -{limit:.2f} — пауза "
                   f"{config.DAILY_LOSS_PAUSE_HOURS}ч")
            self.log.warning("ДНЕВНОЙ ЛИМИТ ПОТЕРЬ: %s", msg)
            self.notifier.send(msg, key="daily_loss")
        self.peak_balance = max(self.peak_balance, self.virtual_balance)
        floor = self.peak_balance * (1 - config.MAX_DRAWDOWN_FRACTION)
        if not self.halted and self.peak_balance > 0 and self.virtual_balance < floor:
            self.halted = True
            dd = 1 - self.virtual_balance / self.peak_balance
            msg = (f"просадка {dd:.1%} от пика ${self.peak_balance:.2f} — "
                   f"торговля ОСТАНОВЛЕНА, снимается только руками")
            self.log.error("СТОП ПО ПРОСАДКЕ: %s", msg)
            self.notifier.send(msg, key="drawdown")

    def finish_cycle(self, pnl, reason, kind=""):
        """Закрытие цикла. kind: stop|tp|timeout|exchange — от него зависит
        кулдаун (в движке evolution2 он ставится ТОЛЬКО после выбивания
        стопом/ликвидацией, а не после любого убытка)."""
        self.roll_day()
        self.virtual_balance += pnl
        self.day_pnl += pnl
        self.log.info("<<< ЦИКЛ ЗАВЕРШЁН (%s): PnL ~%+.3f USDT | капитал ~%.2f",
                      reason, pnl, self.virtual_balance)
        self.pos = None
        now = int(time.time() * 1000)
        # правило кулдауна = правило движка (evolution2.py:270): пауза после
        # выхода по стопу, независимо от знака PnL (с переносом в безубыток
        # стоп может выбить и в плюс), и НЕ ставится после тейка/таймаута.
        # Закрытие биржей различить нельзя — там ориентируемся на убыток.
        stopped = kind == "stop" or (kind == "exchange" and pnl < 0)
        if self.cooldown_bars and stopped:
            self.cooldown_until = now + self.cooldown_bars * self.interval_ms
        if pnl < 0:
            self.consec_losses += 1
            if self.consec_losses >= config.MAX_CONSEC_LOSSES:
                self.paused_until = now + config.KILL_PAUSE_HOURS * 3600 * 1000
                self.log.warning("KILL SWITCH: %d убытков подряд. Пауза до %s",
                                 self.consec_losses,
                                 time.strftime("%d.%m %H:%M",
                                               time.localtime(self.paused_until / 1000)))
                self.notifier.send(
                    f"kill switch: {self.consec_losses} убытков подряд, пауза "
                    f"{config.KILL_PAUSE_HOURS}ч", key="kill")
                self.consec_losses = 0
        else:
            self.consec_losses = 0
        self.risk_limits()
        # состояние обязано пережить перезапуск ИМЕННО в этот момент: иначе
        # рестарт сразу после убыточного цикла снимает и паузу, и счётчик
        self.save_state()

    def paper_close(self, price, reason, kind, taker=True):
        """Закрытие с издержками по модели бэктеста (evolution2.close_pos):
        рыночный выход двигает цену на SLIP и стоит TAKER, лимитный — MAKER,
        сверху накопленные за цикл комиссии колен и фандинг."""
        p = self.pos
        sgn = 1 if p["side"] == "L" else -1
        avg, qty = self.avg_entry()
        px = price * (1 - sgn * SLIP) if taker else price
        fee = qty * px * (TAKER if taker else MAKER)
        pnl = sgn * (px - avg) * qty - fee - p.get("fees", 0.0)
        self.finish_cycle(pnl, reason, kind)

    def paper_liquidate(self):
        """Ликвидация в бумажном режиме — модель движка (evolution2.close_pos,
        ветка liq=True): теряется ВСЯ маржа цикла плюс уже начисленные за цикл
        издержки. Выходить «по цене ликвидации» нельзя: остаток маржи на этом
        уровне забирает ликвидационная комиссия биржи.

        kind="stop": в движке кулдаун ставится и после ликвидации тоже
        (evolution2.py, ветка killed), а не только после стопа."""
        avg, qty = self.avg_entry()
        mused = avg * qty / self.p["lev"]
        pnl = -mused * LIQ_LOSS - self.pos.get("fees", 0.0)
        self.finish_cycle(pnl, "[DRY_RUN] ЛИКВИДАЦИЯ", "stop")

    def close_market(self, reason, price):
        qty = self.avg_entry()[1]
        if not config.DRY_RUN:
            # только свои лимитки: cancel_all снёс бы и ручные ордера владельца
            self.cancel_own_orders(reason)
            side = "Sell" if self.pos["side"] == "L" else "Buy"
            self.session.place_order(category="linear", symbol=self.symbol,
                                     side=side, orderType="Market",
                                     qty=str(self.rq(qty)), reduceOnly=True)
        # выход рыночным ордером — тейкерский, с проскальзыванием
        self.paper_close(price, reason, "timeout")

    # ---------- DRY_RUN: виртуальное исполнение ----------

    def paper_fill(self, candle):
        """Виртуальное исполнение свечи — по правилам движка отбора.

        Порядок событий внутри свечи и издержки повторяют evolution2.run5:
        бумажный результат должен совпадать с тем, что проверял бэктест,
        иначе DRY_RUN проверяет не ту стратегию, которая пойдёт в реал.

        Про тейк в свече с доливкой (evolution2.py, комментарий у tp_exec).
        Порядок движения цены внутри свечи по OHLC неизвестен, поэтому оба
        конца берутся в невыгодную сторону: СРАБАТЫВАНИЕ проверяется по
        tp_pre (дальний тейк, который стоял на бирже до доливки), а ИСПОЛНЕНИЕ
        считается по tp_exec (ближний, переехавший вслед за средней). Ловить
        срабатывание по пересчитанному tp_exec нельзя — это выдумывает выходы,
        которых у бота не было: замер движка даёт DOGE +116% вместо −38%.
        """
        _, o, h, l, c = candle
        p = self.pos
        sgn = 1 if p["side"] == "L" else -1
        q_pre = sum(f[1] for f in p["fills"])
        # фандинг за свечу удержания (0.01% за 8ч), как в движке
        p["fees"] = p.get("fees", 0.0) + q_pre * c * FUND_8H / self.bars_8h
        # стоп фиксируем на начало свечи: доливка внутри неё сдвинула бы
        # среднюю и вместе с ней турбо-стоп — движок так не умеет, и брать
        # более выгодный стоп задним числом нельзя
        stop = self.stop_price()
        tp_pre = self.tp_price()
        hit_tp = (h >= tp_pre) if sgn == 1 else (l <= tp_pre)
        legs_filled = False
        # Цена идёт по неблагоприятной стороне и встречает уровни ПО ПОРЯДКУ —
        # ровно как в движке (evolution2.run5). Опасных уровней два: свой стоп
        # и цена ликвидации. При высоком плече ликвидация оказывается БЛИЖЕ
        # стопа, и свеча может достать её, не коснувшись стопа: бумажный цикл
        # без этой проверки переживал бы свечу, на которой биржа закрыла бы
        # позицию с потерей всей маржи. Колено, лежащее ДО обоих уровней,
        # успевает исполниться и отодвигает ликвидацию (доливка добавляет
        # маржи) — поэтому p_liq пересчитывается на каждом витке.
        while True:
            p_liq = self.liq_price()
            danger = max(p_liq, stop) if sgn == 1 else min(p_liq, stop)
            nxt = p["adds"][0][0] if p["adds"] else None
            leg_first = nxt is not None and ((nxt > danger) if sgn == 1
                                             else (nxt < danger))
            leg_reached = nxt is not None and ((l <= nxt) if sgn == 1
                                               else (h >= nxt))
            if leg_first and leg_reached:
                ap, aq = p["adds"].pop(0)
                p["fees"] += aq * ap * MAKER
                p["fills"].append((ap, aq))
                legs_filled = True
                self.log.info("[DRY_RUN] сетка исполнилась: %s по %s",
                              aq, self.rp(ap))
                continue
            if (l <= danger) if sgn == 1 else (h >= danger):
                if (p_liq >= stop) if sgn == 1 else (p_liq <= stop):
                    self.log.error("[DRY_RUN] ЛИКВИДАЦИЯ по %s (стоп %s "
                                   "стоял дальше)", self.rp(p_liq),
                                   self.rp(stop))
                    self.paper_liquidate()
                else:
                    self.paper_close(stop, "[DRY_RUN] СТОП", "stop")
                return
            break
        if legs_filled:
            # тейк стоит на бирже от ТЕКУЩЕЙ средней: как только колено
            # исполнилось, он переехал вниз (для лонга) вместе с ней. Выход
            # по старому тейку был бы прибылью из воздуха.
            self.push_sl_tp()
            tp_exec = self.tp_price()
            # hit_tp НЕ пересчитываем: срабатывание остаётся по tp_pre, как в
            # движке. Пересчёт (проверка по ближнему tp_exec) — то самое
            # ослабление, которое evolution2 прямо запрещает.
        else:
            tp_exec = tp_pre
        if hit_tp:
            # тейк бот держит через set_trading_stop, то есть рыночным по
            # триггеру — значит тейкерская комиссия и проскальзывание
            self.paper_close(tp_exec, "[DRY_RUN] ТЕЙК", "tp")
            return
        # остальные колена (стоп в этой свече не задет)
        while p["adds"]:
            ap, aq = p["adds"][0]
            if (l <= ap if sgn == 1 else h >= ap):
                p["fees"] += aq * ap * MAKER
                p["fills"].append((ap, aq))
                p["adds"].pop(0)
                self.log.info("[DRY_RUN] сетка исполнилась: %s по %s", aq, self.rp(ap))
                self.push_sl_tp()
            else:
                break

    # ---------- реал: синхронизация с биржей ----------

    def handle_orphan(self, pp, why):
        """Позиция на бирже, которой бот не ведёт.

        Такая позиция опаснее любого убытка: у неё нет ни стопа, ни тейка, ни
        таймаута — она может ехать до ликвидации, пока бот считает, что он вне
        рынка. По умолчанию закрываем по рынку (CLOSE_UNEXPECTED_POSITION) и
        встаём на паузу: причину расхождения должен посмотреть человек.
        """
        size = float(pp["size"] or 0)
        pos_side = pp["side"]
        # Ту же самую чужую позицию разбираем ОДИН раз. При
        # CLOSE_UNEXPECTED_POSITION=False она никуда не девается, sync_exchange
        # видит её каждый цикл — и без этой проверки пауза продлевалась бы на
        # 24 часа каждые POLL_SECONDS, то есть ручная позиция владельца
        # останавливала бы бота навсегда.
        # ВАЖНО: метка ставится только после УДАВШЕГОСЯ разбора (см. ниже).
        # Если закрытие сорвалось, ключ остаётся пустым, и следующий цикл
        # попробует снова: чужая позиция без стопа опаснее лишней записи в лог.
        key = [pos_side, size, str(pp.get("createdTime") or "")]
        if self.orphan_key == key:
            if self.pos is not None:
                # то же расхождение после перезапуска: ведение снимаем и
                # записываем это на диск, иначе следующий старт снова
                # прочитает позицию, которой у нас нет
                self.pos = None
                self.save_state()
            return
        self.log.error("РАСХОЖДЕНИЕ С БИРЖЕЙ: %s — %s %s по %s",
                       why, pos_side, size, pp.get("avgPrice"))
        self.notifier.send(f"расхождение с биржей: {why} ({pos_side} {size})",
                           key="orphan")
        resolved = True     # разобрались ли мы с расхождением ОКОНЧАТЕЛЬНО
        if config.CLOSE_UNEXPECTED_POSITION and not config.DRY_RUN:
            try:
                self.session.cancel_all_orders(category="linear",
                                               symbol=self.symbol)
                self.session.place_order(
                    category="linear", symbol=self.symbol,
                    side=("Sell" if pos_side == "Buy" else "Buy"),
                    orderType="Market", qty=str(self.rq(size)),
                    reduceOnly=True)
                self.log.warning("Неожиданная позиция закрыта по рынку")
                self.notifier.send("неожиданная позиция закрыта по рынку",
                                   key="orphan_closed")
            except Exception as e:      # noqa: BLE001
                # Закрыть не вышло (биржа не ответила, отклонила заявку...).
                # Позиция ЖИВА и по-прежнему без стопа — считать расхождение
                # разобранным нельзя: метку не ставим, и следующая сверка
                # повторит попытку. Повторы шумят в логе, зато голая позиция не
                # остаётся висеть до тех пор, пока её не заметит человек.
                resolved = False
                self.log.exception("Не удалось закрыть неожиданную позицию — "
                                   "повторю на следующей сверке")
                self.notifier.send(f"НЕ УДАЛОСЬ закрыть позицию: {e}",
                                   key="orphan_fail")
        self.orphan_key = key if resolved else None
        self.pos = None
        # пауза, а не остановка: причина может быть безобидной (ручная сделка),
        # но торговать вслепую до разбора нельзя
        self.paused_until = max(
            self.paused_until,
            int(time.time() * 1000) + config.ORPHAN_PAUSE_HOURS * 3600 * 1000)
        self.log.warning("Пауза %d ч после расхождения",
                         config.ORPHAN_PAUSE_HOURS)
        self.save_state()

    def seed_capital(self):
        """Первый запуск в реале: капитал бота = equity кошелька.

        Дневной лимит и стоп по просадке считаются в долях капитала. Если
        оставить бумажный START_BALANCE, на счёте в $500 «дневной лимит 10%»
        означал бы $5 — защита, которая не сработает никогда."""
        if not self._fresh_state:
            return
        snap = self.wallet_snapshot()
        if snap is None:
            return
        self.virtual_balance = snap[0]
        self.peak_balance = snap[0]
        self.day_start_balance = snap[0]
        self.log.info("Первый запуск: капитал взят с кошелька — $%.2f", snap[0])
        self.save_state()

    def reconcile_start(self):
        """Сверка с биржей ДО первой свечи.

        Бот мог быть выключен минуту, а мог сутки: за это время позиция могла
        закрыться, дорасти коленами сетки или появиться там, где её быть не
        должно. Пока сверки не было, любое ведение — догадка.
        В DRY_RUN сверять нечего: бумажная позиция на бирже не существует.
        """
        if config.DRY_RUN:
            return
        self.check_position_mode()      # one-way/hedge: иначе все ордера в 10001
        pp = self.exchange_position_full()
        if self.pos and not pp:
            self.cancel_own_orders("позиция закрылась, пока бот не работал")
            pnl = self._last_closed_pnl(self.pos["opened_ts"])
            if pnl is None:
                self.log.warning("Позиция из состояния на бирже не найдена, "
                                 "сделки в истории тоже нет. Цикл не засчитан.")
                self.notifier.send("позиция из состояния не найдена на бирже — "
                                   "цикл не засчитан", key="reconcile")
                self.pos = None
                self.save_state()
            else:
                self.finish_cycle(pnl, "закрыта биржей, пока бот не работал",
                                  "exchange")
            return
        if self.pos and pp:
            want = "Buy" if self.pos["side"] == "L" else "Sell"
            if pp["side"] != want:
                self.handle_orphan(pp, f"на бирже {pp['side']}, "
                                       f"в ведении {want}")
                return
            filled = sum(f[1] for f in self.pos["fills"])
            size = float(pp["size"])
            self.log.info("Сверка: позиция подтверждена (%s %s, в ведении %s)",
                          pp["side"], size, filled)
            if size > filled * 1.001:
                self.sync_exchange()    # доехали колена, пока бота не было
            # SL/TP выставляем принудительно: пока бота не было, биржа могла
            # их не иметь вовсе (например, ордера снимали руками)
            if self.pos:
                self.push_sl_tp(force=True)
            return
        if pp:
            self.handle_orphan(pp, "позиция на бирже, а ведения нет")
            return
        # ни позиции, ни ведения — но лимитки могли пережить закрытие цикла
        if config.CLOSE_UNEXPECTED_POSITION:
            try:
                self.session.cancel_all_orders(category="linear",
                                               symbol=self.symbol)
                self.log.info("Сверка: позиции нет, висячие ордера по %s сняты",
                              self.symbol)
            except Exception as e:      # noqa: BLE001
                self.log.warning("Не удалось снять ордера при сверке: %s", e)

    def sync_exchange(self):
        pp = self.exchange_position_full()   # один запрос на цикл, не два
        size = float(pp["size"]) if pp else 0.0
        if not self.pos:
            # Позиции в ведении нет. Если на бирже она есть — это почти всегда
            # исполнившееся колено сетки УЖЕ ПОСЛЕ того, как биржа закрыла
            # цикл по SL/TP: голая позиция без стопа и тейка, ровно тот
            # сценарий, ради которого лимитки и надо снимать сразу.
            if pp:
                self.handle_orphan(pp, "позиция на бирже без ведения ботом")
            elif self.orphan_key is not None:
                # чужой позиции больше нет: следующее расхождение — уже другое
                # событие, и паузу за него ставить надо заново
                self.orphan_key = None
                self.save_state()
            return
        want = "Buy" if self.pos["side"] == "L" else "Sell"
        if pp and pp["side"] != want:
            # Сторона расходится с ведением. Дальше нельзя ничего: SL/TP мы
            # тянули бы для лонга, а на бирже шорт, и reduceOnly на таймауте
            # ушёл бы НЕ В ТУ сторону (то есть увеличил бы позицию, а не
            # закрыл). В reconcile_start эта проверка была, в рабочем цикле —
            # нет, хотя сторона может разойтись и на ходу (ручная сделка).
            self.handle_orphan(pp, f"на бирже {pp['side']}, в ведении {want}")
            return
        if size == 0:
            seen = self.pos.get("closed_seen_ts")
            if seen is None:
                seen = self.pos["closed_seen_ts"] = int(time.time() * 1000)
                # снимаем свои лимитки ПЕРВЫМ делом и ровно один раз: пока они
                # висят, любая может открыть позицию без стопа, но повторять
                # отмену каждые POLL_SECONDS незачем — это уже слепой сброс
                self.cancel_own_orders("позиция закрыта биржей")
            pnl = self._last_closed_pnl(self.pos["opened_ts"])
            if pnl is None:
                waited = (time.time() * 1000 - seen) / 1000
                if waited < config.CLOSED_PNL_GRACE_SEC:
                    # сделка ещё не проявилась в истории биржи — ждём. Взять
                    # сейчас «ноль» значило бы записать несуществующий цикл.
                    self.log.info("Позиция закрыта биржей, жду сделку в "
                                  "истории (%.0f c из %d)", waited,
                                  config.CLOSED_PNL_GRACE_SEC)
                    self.save_state()
                    return
                self.log.warning(
                    "Позиция закрыта биржей, но сделки в истории нет за %d c. "
                    "Цикл НЕ засчитан: счётчик убытков, дневной лимит и "
                    "капитал не трогаю — иначе выдуманный ноль обнулил бы "
                    "kill switch", config.CLOSED_PNL_GRACE_SEC)
                self.notifier.send("позиция закрыта биржей, PnL не найден — "
                                   "цикл не засчитан", key="pnl_missing")
                self.pos = None
                self.save_state()
                return
            self.finish_cycle(pnl, "SL/TP биржей", "exchange")
        elif size > 0:
            if self.pos.pop("closed_seen_ts", None) is not None:
                # позиция снова видна на бирже: пока шло ожидание PnL,
                # исполнилась лимитка сетки. Метку ожидания надо снять, иначе
                # бот навсегда остался бы «в окне закрытия» и перестал вести
                # живую позицию (ни стопа, ни тейка, ни таймаута)
                self.log.warning("Позиция снова открыта на бирже (%s) — "
                                 "ожидание закрытого PnL отменено", size)
                self.save_state()
            filled = sum(f[1] for f in self.pos["fills"])
            if size > filled * 1.001:
                while self.pos["adds"] and size > filled * 1.001:
                    ap, aq = self.pos["adds"].pop(0)
                    self.pos["fills"].append((ap, aq))
                    filled += aq
                self.log.info("Сетка исполнилась (размер %s)", size)
                self.push_sl_tp()
                # доливка меняет среднюю, стоп и тейк: рестарт без этой записи
                # вёл бы позицию по устаревшему составу колен
                self.save_state()

    def _last_closed_pnl(self, opened_ts):
        """Реализованный PnL ИМЕННО ЭТОГО цикла, либо None, если биржа его ещё
        не показала.

        Раньше бралась просто последняя запись истории (limit=1 без фильтра).
        Она могла принадлежать прошлому циклу, соседнему боту или ручной
        сделке владельца, а при пустом ответе функция возвращала 0.0 —
        «безубыточный цикл». Любой из этих вариантов обнулял серию убытков и
        снимал kill switch ровно тогда, когда он и должен был сработать.

        Теперь берём только записи, закрытые ПОСЛЕ открытия нашей позиции, и
        суммируем их: биржа закрывает частями (колено сетки и остаток — разные
        строки истории). Ничего не нашли — честно возвращаем None, чтобы
        вызывающий подождал (CLOSED_PNL_GRACE_SEC) и не выдумывал ноль.

        Одного времени мало: владелец может торговать этой же монетой руками с
        того же счёта, и его сделка попала бы в PnL нашего цикла. Поэтому
        сверяем ещё сторону закрытия (в closed-pnl поле side — сторона
        ЗАКРЫВАЮЩЕГО ордера, для лонга это Sell) и среднюю цену входа
        (avgEntryPrice): у чужой сделки она своя. Объём проверяем суммой
        closedSize — она не должна заметно превышать наш размер.

        Окно запроса: Bybit отдаёт максимум 7 суток и, если передать только
        startTime, вернёт 7 суток ОТ него. Позиция может висеть дольше
        (max_bars до 288 свечей плюс выключенный бот), и тогда сегодняшнее
        закрытие в ответ просто не попадало. Спрашиваем последние 7 суток
        назад от «сейчас» — закрытие всегда свежее запроса.
        """
        now = int(time.time() * 1000)
        week = 7 * 24 * 3600 * 1000
        try:
            r = self.session.get_closed_pnl(
                category="linear", symbol=self.symbol,
                startTime=max(int(opened_ts), now - week + 60000),
                endTime=now, limit=50)
            rows = r["result"]["list"]
        except Exception as e:      # noqa: BLE001
            self.log.warning("История сделок недоступна: %s", e)
            return None
        want_close = "Sell" if self.pos["side"] == "L" else "Buy"
        avg_own, qty_own = self.avg_entry()
        # потолок объёма — позиция ПЛЮС ещё не исполненные колена: колено могло
        # доехать и выбиться стопом между двумя опросами, и такой цикл честно
        # наш. Сверх этой суммы биржа закрыть наше уже не могла.
        qty_max = qty_own + sum(a[1] for a in self.pos.get("adds") or [])
        total, found, size_sum, skipped = 0.0, 0, 0.0, 0
        for row in rows:
            closed_at = int(row.get("updatedTime")
                            or row.get("createdTime") or 0)
            if closed_at < opened_ts:
                continue        # чужая (более ранняя) сделка — не наша
            if row.get("side") and row["side"] != want_close:
                skipped += 1    # закрытие в другую сторону — не наш цикл
                continue
            entry_px = float(row.get("avgEntryPrice") or 0)
            # 2% — запас на проскальзывание входа и на то, что в ведении цена
            # записана по закрытию свечи, а биржа исполнила чуть иначе
            if entry_px and abs(entry_px / avg_own - 1) > 0.02:
                skipped += 1
                continue
            total += float(row["closedPnl"])
            size_sum += float(row.get("closedSize") or 0)
            found += 1
        if not found:
            if skipped:
                self.log.warning("В истории биржи есть %d закрытых сделок по "
                                 "%s, но ни одна не похожа на наш цикл "
                                 "(сторона/цена входа) — цикл не засчитан",
                                 skipped, self.symbol)
            return None
        if size_sum > qty_max * 1.05:
            # закрыто больше, чем мы вообще могли вести: в выборку попала
            # чужая сделка. Выдуманный PnL хуже незасчитанного цикла.
            self.log.warning("PnL цикла отброшен: закрыто %s при нашем "
                             "максимуме %s — в истории есть чужие сделки",
                             size_sum, qty_max)
            return None
        self.log.info("PnL цикла по истории биржи: %+.4f USDT (%d записей, "
                      "закрыто %s при позиции %s)", total, found, size_sum,
                      qty_own)
        return total

    # ---------- обработка свечи ----------

    def on_new_candle(self, cs):
        self.roll_day()     # сутки могли смениться и без единой сделки
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
        # плавающий минус проверяем ДО ведения позиции: закрытие по стопу или
        # таймауту ниже сделает pos=None, и предупредить будет уже не о чем
        self.float_risk_check(c)

        if self.pos:
            if self.pos.get("closed_seen_ts"):
                # Биржа УЖЕ закрыла эту позицию, идёт только ожидание её PnL в
                # истории (CLOSED_PNL_GRACE_SEC). Вести её нельзя ничем:
                # set_trading_stop ушёл бы по несуществующей позиции, а таймаут
                # ниже отправил бы cancel_all + reduceOnly и записал выдуманный
                # PnL за цикл, которого уже нет. Ждём молча — окно закроет
                # sync_exchange, засчитав цикл или отбросив его.
                return
            if config.DRY_RUN:
                self.paper_fill(cs[-1])
            if self.pos and self.be_move and not self.pos.get("be_done"):
                # стоп в безубыток, когда прошли полпути к тейку
                avg, _ = self.avg_entry()
                sgn = 1 if self.pos["side"] == "L" else -1
                # полпути к ФАКТИЧЕСКОМУ тейку цикла (tp_eff), а не к базовому
                # p["tp"]: при tp_atr_k>0 тейк умножен на множитель
                # волатильности, зафиксированный на входе, и порог безубытка в
                # движке (evolution2.run5) считается именно от него. Пока такого
                # конфига в бою нет, но расхождение бот/движок — мина, которая
                # ждёт первого же генома с be_move=1 и tp_atr_k>0.
                trig = avg * (1 + sgn * self.pos.get("tp_eff", self.p["tp"]) * 0.5)
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
            if self.pos:
                # позиция изменилась (доливка, перенос стопа) — состояние
                # должно пережить перезапуск в том же виде
                self.save_state()
                return
            if not config.DRY_RUN:
                # Цикл закрылся прямо здесь (таймаут -> close_market): рыночный
                # reduceOnly только что ушёл на биржу и ещё не подтверждён.
                # Входить тем же опросом нельзя — новый ордер столкнулся бы с
                # неисполненным закрытием. Ждём следующего опроса: sync_exchange
                # сверит факт, и вход пойдёт уже с подтверждённым «мы вне рынка».
                return
            # DRY_RUN: бумажный цикл закрылся НА ЭТОЙ свече, и дальше идёт
            # обычная проверка входа — так же, как в движке (evolution2.run5:
            # `if pos: continue`, то есть бар пропускается ТОЛЬКО пока позиция
            # жива). Раньше здесь стоял безусловный return, и бумага не могла
            # войти на баре закрытия предыдущего цикла — движок мог. Это давало
            # бумаге меньше сделок, чем проверял отбор. Кулдаун после стопа,
            # kill switch и стоп по просадке ниже никуда не делись: их только
            # что выставил finish_cycle, и проверки стоят прямо под этой строкой.

        now = int(time.time() * 1000)
        if self.halted:
            # стоп по просадке снимается только руками (halted в файле
            # состояния): ведение открытой позиции выше по коду продолжается,
            # запрещены только НОВЫЕ входы
            return
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
        if self.adx_gate:
            parts.append(f"ADX({self.adx_n})<={self.adx_max:g}")
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
        # файл состояния заводим сразу: если процесс упадёт на первом же цикле,
        # читать при следующем запуске будет уже что
        self.save_state()
        if not config.DRY_RUN:
            if not config.API_KEY or not config.API_SECRET:
                raise SystemExit("Нет BYBIT_API_KEY / BYBIT_API_SECRET")
            self.set_leverage()
            self.seed_capital()
            self.reconcile_start()      # ведение = то, что реально на бирже
        while True:
            try:
                # сверка КАЖДЫЙ цикл, а не только при открытой позиции: без
                # позиции опасность обратная — лимитка сетки, пережившая
                # закрытие, открывает голую позицию без стопа, и заметить это
                # можно только спросив биржу
                if not config.DRY_RUN:
                    self.sync_exchange()
                cs = self.candles()
                # строго НОВЕЕ обработанной: last_candle_ts переживает
                # перезапуск, и "!=" повторно прогнал бы ту же свечу, если
                # состояние оказалось впереди биржи
                if cs and cs[-1][0] > self.last_candle_ts:
                    self.last_candle_ts = cs[-1][0]
                    self.on_new_candle(cs)
                    # какая свеча уже отработана — тоже часть состояния:
                    # без этого рестарт исполняет её второй раз (в DRY_RUN
                    # это лишние доливки и кривая капитала «из воздуха»)
                    self.save_state()
                self.api_errors = 0
            except KeyboardInterrupt:
                raise
            except Exception:
                self.api_errors += 1
                self.log.exception("Ошибка цикла (%d подряд), повтор через %s c",
                                   self.api_errors, config.POLL_SECONDS)
                # серия ошибок = бот ослеп: позиция на бирже живёт, а ведения
                # фактически нет. Об этом надо узнать сразу, а не из отчёта.
                if self.api_errors >= config.API_ERROR_STREAK:
                    self.notifier.send(
                        f"{self.api_errors} ошибок цикла подряд — бот не видит "
                        f"биржу", key="api_errors")
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
        extra = ""
        if (sym, mode) in getattr(config, "LEGACY_UNVERIFIABLE", {}):
            extra = (
                f"\n'{mode}' вынесен из рабочих конфигов: он описан "
                f"набором параметров старого поколения, который нынешний "
                f"движок не принимает, поэтому проверить его нечем. "
                f"Сохранён в config.LEGACY_UNVERIFIABLE.")
        raise SystemExit(
            f"Режим '{mode}' недоступен для {sym}. Доступно: {opts}{extra}")

    # ОТСТАВЛЕННЫЙ КОНФИГ НЕ СТАРТУЕТ БЕЗ ЯВНОГО СОГЛАСИЯ.
    # Раньше витрина писала «снят с витрины», а бот на том же конфиге спокойно
    # запускался: пометка жила только на сайте. Теперь она означает то, что
    # написано. Запрет снимается флагом --run-retired — это не защита от
    # владельца, а защита от того, чтобы отставленный конфиг попал в торговлю
    # случайно, по строке из старой шпаргалки.
    reason = (config.retire_reason(sym, mode)
              if hasattr(config, "retire_reason") else None)
    if reason and "--run-retired" not in sys.argv:
        raise SystemExit(
            f"Конфиг {sym}/{mode} отставлен (уровень {reason[0]}): "
            f"{reason[1]}.\n"
            f"Он не прошёл честную переоценку и убран с витрины. Если "
            f"запуск всё-таки нужен, добавьте --run-retired.")

    bot = RsiGridBot(sym, mode)
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.log.info("Остановлено. Открытая позиция и лимитки остаются на бирже!")


if __name__ == "__main__":
    main()
