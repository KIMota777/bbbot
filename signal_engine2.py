# -*- coding: utf-8 -*-
"""Движок сигналов v2.2: среднесрочные сетапы на BTC с честной механикой.

Что нового в v2.2 (заказ владельца):

I) ШТОРМОВОЙ ФИЛЬТР («не торговать на невыгодном рынке»). prep_context
   считает по барам своего ТФ причинные ряды рыночного стресса: ret_1d
   (ход за 24ч), ret_7d (за 7 суток), atr_rank (перцентиль дневного ATR
   среди последних 90 суток), vol_spike (дневной диапазон / медиана за 30
   суток), atr_d_pct (тот же atr_d, но в процентах). Ворото GATE_STORM
   («шторм») проверяется ПЕРЕД профильными условиями сетапа и умеет три
   режима (ген storm_mode): 0 — не торговать в шторм вообще, 1 — не
   торговать ПРОТИВ шторма (в обвале запрещены лонги, в ракете шорты),
   2 — торговать ТОЛЬКО в шторм (гипотезу «лучшие сделки на панике»
   проверяет GA, а не вера). Все дневные/недельные величины считаются
   СКОЛЬЗЯЩИМИ ОКНАМИ ПО БАРАМ СВОЕГО ТФ (1440/interval_min баров в сутках)
   — отдельные дневные свечи не подтягиваются, рассинхрона нет.
   В семени storm_gate=0: поведение старых геномов не меняется ни на бит.
II) ХУК ПРЕДИКТИВНОГО СКОРИНГА. bar_features(...) отдаёт словарь строго
   причинных числовых признаков бара (список — в SCORE_FEATURES), а
   run_setup(..., score_fn=..., score_min=...) после ВСЕХ ворот спрашивает
   у модели оценку 0..1 и не открывает сделку при score < порога: отказ
   идёт в reject_counts["скоринг"] и в near_misses с gate="скоринг" вместе
   с ЧЕСТНОЙ гипотетикой (видно, что именно отфильтровали). Саму модель
   движок не обучает и не знает — это инфраструктура.
III) market_context(c4, ctx, i) — краткая сводка состояния рынка для
   живого помощника и сайта («сейчас шторм: BTC -9.4% за сутки»).
IV) cooldown_ok/cooldown_ms/signal_ts — ЕДИНАЯ трактовка кулдауна для
   движка и советника: отсчёт от ЗАКРЫТИЯ сигнального бара.

Что нового в v2.1 (заказ владельца после честной валидации evolution11):

А) МУЛЬТИ-ТАЙМФРЕЙМ. Движок больше не зашит на 4ч: prep_context(c4,
   interval_min=240) и run_setup(..., interval_min=...) работают с любым
   интервалом сигнальных баров (240=4ч, 60=1ч). Внутри всё масштабируется:
   длительность бара, кулдаун, дневной ATR (баров в сутках = 1440/interval),
   момент входа (первая 15м-свеча после ЗАКРЫТИЯ бара своего ТФ).
   ГЕНЫ ЕДИНИЦ НЕ МЕНЯЮТ: window/cooldown — в барах своего ТФ, hold_days —
   в днях, retest_bars — в 15м-барах. GA сам подбирает значения под ТФ.
Б) СОСТАВ СЕТАПОВ. Четыре провалившихся на holdout сетапа (range_long,
   range_short, sweep_short, pump_short) УДАЛЕНЫ из SETUPS (их ворота
   оставлены как legacy — старые конфиги можно прогнать через ALL_SETUPS).
   Добавлены два шортовых:
     bounce_short — «нож -> откат»: обвал за drop_days >= drop_frac пробил
       лоу диапазона window (сломанный уровень); цена отскочила от лоу ножа
       минимум на bounce_min_atr дневных ATR, но восстановила не больше
       bounce_max_frac падения и не поднялась над сломанный уровень
       (+допуск LEVEL_TOL_ATR); RSI поднялся над rsi_os (перепроданность
       снята) — шортим откат (dead-cat bounce) с целью ретеста лоу.
     rally_short — тренд-шорт: рост за drop_days >= drop_frac, RSI выше
       100-rsi_thr, разворотная свеча (c<o). Режимная логика ЦЕНТРАЛЬНА:
       в bear это шорт ПО тренду (пороги мягче на with_rsi_shift), в bull —
       ПРОТИВ (строже на counter_rsi_shift). Плюс тренд-фильтр из ботов:
       ma_gate=1 -> шорт только под SMA(ma_len) баров своего ТФ.
В) ВОРОТА ИЗ БОТОВ (у финальных ботов прошли walk-forward):
     fund_gate/fund_thr — funding-гейт: шорт только при funding >= fund_thr
       (лонгисты платят — перегрев подтверждён), лонг зеркально при
       funding <= -fund_thr. Ставка — последнего НАЧАВШЕГОСЯ 8ч-периода
       (на Bybit она фиксируется в начале периода, лукахеда нет).
     aroon_gate/aroon_n/aroon_thr — Aroon-гейт: для шорта AroonDown(n) >=
       aroon_thr (свежий минимум — падение живо), для лонга зеркально Up.
   Все новые ворота ("funding", "aroon", "тренд MA", "отскок") попадают в
   reject_counts/gate_solo и участвуют в near-miss как обычные.
Г) ФИТНЕС НА КАЧЕСТВО. Владелец явно попросил меньше сделок ради винрейта
   («можешь снизить с 90 до 60»): TPM_NORM 1.2 -> 0.7, MIN_TRADES_FIT
   25 -> 20, бонус за винрейт над реальным безубытком 1:3 (~32% с
   издержками), штраф за просадку глубже 25%.

Отличия v2 от signal_engine.py (v1) — это ответы на жалобы владельца:

1) ГИБКИЕ СТОПЫ (ген stop_mode). В v1 стоп ставился ТОЛЬКО за границей
   многодневного диапазона, из-за чего медианная ширина стопа была 9.8% при
   лимите 2.6% — 95% сетапов физически не пролезали в риск. Теперь 4 способа:
     0 = за границей окна диапазона (как v1, самый широкий),
     1 = за свинг-экстремумом последних swing_bars 4ч-баров,
     2 = stop_atr_k * дневной ATR от входа,
     3 = за фитилём сигнального бара + буфер.
2) РЕЖИМНАЯ ЛОГИКА вместо запрета. Лонг в медвежьем рынке РАЗРЕШЁН, но
   условия строже (RSI глубже на counter_rsi_shift, зона уже в
   counter_zone_mult раз); по тренду — мягче на with_rsi_shift.
   Полный запрет режима остаётся возможным (reg_bull/reg_range/reg_bear=0).
3) NEAR-MISS: бары, где провалено РОВНО ОДНО ворото и близко к порогу,
   отдаются наружу вместе с ЧЕСТНОЙ гипотетикой (та же simulate_trade),
   но никак не влияют на баланс/сделки.
4) БОГАТЫЕ МЕТРИКИ сделки: MAE/MFE в долях риска R, время до них,
   would_hit_tp_later (дошло бы до тейка, если выбило стопом),
   mfe_after_tp_r (сколько R прошло бы после тейка).
5) ВХОД ЛИМИТКОЙ НА РЕТЕСТЕ (entry_mode=1): цена лучше на retest_atr*ATR,
   ждём retest_bars 15м-баров; не налилось — сигнал отменён (не сделка).

Неизменные правила честности:
  - сигнал ищется на ЗАКРЫТОМ 4ч-баре, на баре i доступны только данные <= i;
  - вход не раньше open первой 15м-свечи ПОСЛЕ закрытия сигнального бара;
  - стоп : тейк = 1 : 3 жёстко (RR=3.0), переноса в безубыток НЕТ;
  - стоп и тейк в одной 15м-свече -> засчитывается СТОП (консервативно);
  - ширина стопа ограничена stop_cap (плечо 15-20: широкий стоп = ликвидация
    раньше стопа), лимит проверяется как отдельное ворото;
  - комиссии/фандинг/проскальзывание — константы evolution2.

Публичный API (на него опираются другие агенты, менять нельзя):
  SETUPS, RR, MIN_STOP, START, MARGIN, RSI_SET, GENES2, DEFAULTS2,
  GATE_* / REJ_* (имена ворот и причин отказа), TRADE_FIELDS, NM_FIELDS,
  prep_context, effective_thresholds, regime_allowed, build_ext, gate_eval,
  simulate_trade, run_setup, stats, fitness, oos_score,
  atr_daily_frac, rolling_extremes_lagged, trailing_extremes, calc_ema,
  setup_side, REGIME_NAMES.
Добавлено в v2.1 (тоже публичное):
  LEGACY_SETUPS, ALL_SETUPS, SETUP_TITLES, bar_ms_of, bars_per_day,
  calc_sma, calc_aroon, funding_on_bars, GATE_BOUNCE/GATE_FUND/GATE_AROON/
  GATE_MA, параметр interval_min у prep_context/build_ext/run_setup.
Добавлено в v2.2 (тоже публичное):
  GATE_STORM, REJ_SCORE, TOL_STORM, SCORE_FEATURES,
  rolling_rank, rolling_median, pct_change_bars, daily_range_frac,
  storm_state, market_context, bar_features,
  cooldown_ms, signal_ts, cooldown_ok,
  ряды prep_context: ret_1d, ret_7d, atr_d_pct, atr_rank, vol_spike,
        day_range,
  ключи build_ext: win_lo, win_hi,
  гены: storm_gate, storm_day, storm_week, storm_atr_rank, storm_mode,
        score_gate, score_min,
  параметры run_setup: score_fn, score_min; ключ результата blocked_score.
"""

import bisect
from collections import deque

import evolution as ev
import evolution2 as e2
import evolution6 as e6
import ext_data as xd

# ---------------------------------------------------------------- константы
TAKER, MAKER = e2.TAKER, e2.MAKER
SLIP = e2.SLIP
FUND_8H = e2.FUND_8H
MM = e2.MM
MONTH_MS = e2.MONTH_MS
RSI_SET = e2.RSI_SET              # [7, 10, 14, 21]
MMR = 0.005                       # поддерживающая маржа Bybit (~0.5%)


def liq_frac(lev):
    """Доля неблагоприятного хода до ликвидации на изолированной марже:
    1/плечо минус поддерживающая маржа. x15 -> 6.17%, x20 -> 4.50%."""
    return max(0.001, 1.0 / lev - MMR)

RR = 3.0                          # тейк = 3 x стоп, жёстко
MIN_STOP = 0.005                  # стоп уже 0.5% — шум, не берём
START = 20.0                      # старт баланса бэктеста, USDT
MARGIN = 5.0                      # маржа на сделку, USDT

MS_4H = 4 * 3600 * 1000
BARS_DAY_4H = 6                   # 6 четырёхчасовых баров = сутки
BARS_DAY_15 = 96                  # 96 пятнадцатиминуток = сутки
MS_15M = 15 * 60 * 1000
MS_8H = 8 * 3600 * 1000           # период funding на Bybit


def bar_ms_of(interval_min):
    """Длительность сигнального бара в мс (240 -> 4ч, 60 -> 1ч)."""
    return int(interval_min) * 60 * 1000


def bars_per_day(interval_min):
    """Сколько баров этого ТФ в сутках (240 -> 6, 60 -> 24)."""
    return max(1, 1440 // int(interval_min))


# Действующие сетапы (владелец: 4 провалившихся на holdout убраны,
# добавлены два шортовых). Именно этот список гоняют GA и билдеры.
SETUPS = ["sweep_long", "dump_long", "bounce_short", "rally_short"]

# Убранные сетапы: ворота в gate_eval физически оставлены (старые конфиги
# из json можно прогнать), но ни GA, ни билдеры их больше не трогают.
LEGACY_SETUPS = ("range_long", "range_short", "sweep_short", "pump_short")
ALL_SETUPS = tuple(SETUPS) + LEGACY_SETUPS

SETUP_TITLES = {
    "sweep_long": "лонг после прокола старого минимума и возврата",
    "dump_long": "лонг после ножа с разворотной свечой",
    "bounce_short": "шорт отката после ножа (dead-cat bounce)",
    "rally_short": "тренд-шорт перегретого отскока/роста",
}

REGIME_NAMES = {0: "bull", 1: "range", 2: "bear"}

# откуда берётся стоп (ген stop_mode) — для логов помощника и подписей
STOP_MODE_NAMES = {
    0: "за границей диапазона",
    1: "за свинг-экстремумом",
    2: "k x ATR от входа",
    3: "за фитилём сигнальной свечи",
}

# имена ворот (стабильные ключи для reject_counts и графиков)
GATE_DATA = "данные"
GATE_REGIME = "режим"
GATE_ZONE = "зона"
GATE_RSI = "RSI"
GATE_POKE = "глубина прокола"
GATE_RECLAIM = "возврат за уровень"
GATE_MOVE = "размер движения"
GATE_CANDLE = "разворотная свеча"
GATE_STOP = "ширина стопа"
GATE_BOUNCE = "отскок"            # bounce_short: отскок от лоу ножа
GATE_FUND = "funding"             # ворото из ботов: ставка финансирования
GATE_AROON = "aroon"              # ворото из ботов: свежесть экстремума
GATE_MA = "тренд MA"              # rally_short: цена под SMA(ma_len)
GATE_STORM = "шторм"              # v2.2: рыночный стресс (день/неделя/ATR)
GATE_NAMES = (GATE_DATA, GATE_REGIME, GATE_ZONE, GATE_RSI, GATE_POKE,
              GATE_RECLAIM, GATE_MOVE, GATE_CANDLE, GATE_STOP,
              GATE_BOUNCE, GATE_FUND, GATE_AROON, GATE_MA, GATE_STORM)

# причины отказа на стадии исполнения (не ворота бара)
REJ_RETEST = "ретест не налился"
REJ_NO15M = "нет 15м-данных"
REJ_ENTRY_STOP = "стоп вне лимита на фактическом входе"
REJ_SCORE = "скоринг"             # v2.2: модель дала оценку ниже score_min
REJ_NAMES = (REJ_RETEST, REJ_NO15M, REJ_ENTRY_STOP, REJ_SCORE)

# допуски "почти прошло" для near-miss (в единицах соответствующего порога)
TOL_ZONE = 0.30        # зона: перебор до 30% порога
TOL_RSI = 5.0          # RSI: 5 пунктов
TOL_MOVE = 0.30        # размер движения: недобор до 30% порога
TOL_POKE = 0.40        # глубина прокола: недобор до 40% порога
TOL_STOP = 0.35        # ширина стопа: перебор до 35% cap
TOL_BOUNCE = 0.35      # отскок от лоу ножа: недобор до 35% порога
TOL_FUND = 0.010       # funding: не хватило до 0.010%/8ч
TOL_AROON = 10.0       # aroon: не хватило до 10 пунктов (шкала 0..100)
TOL_MA = 0.010         # тренд MA: цена в пределах 1% от SMA
TOL_STORM = 0.15       # шторм: сила в пределах 15% от порога (см. storm_state)
LEVEL_TOL_ATR = 0.25   # bounce_short: допуск над сломанным уровнем, дн. ATR

# ------------------------------------------------------------------- гены
# имя -> (min, max, is_int)
GENES2 = {
    "rsi_idx":           (0, 3, True),
    "window":            (30, 200, True),
    "zone":              (0.08, 0.45, False),
    "rsi_os":            (18, 45, True),
    "buf_atr":           (0.1, 1.5, False),
    "stop_mode":         (0, 3, True),
    "swing_bars":        (3, 20, True),
    "stop_atr_k":        (0.8, 3.5, False),
    "stop_cap":          (0.008, 0.035, False),
    "poke_atr":          (0.1, 1.2, False),
    "age":               (2, 30, True),
    "drop_frac":         (0.02, 0.14, False),
    "drop_days":         (1, 4, True),
    "hold_days":         (2, 10, True),
    "cooldown":          (0, 12, True),
    "reg_bull":          (0, 1, True),
    "reg_range":         (0, 1, True),
    "reg_bear":          (0, 1, True),
    "counter_rsi_shift": (0, 15, True),
    "counter_zone_mult": (0.4, 1.0, False),
    "with_rsi_shift":    (0, 10, True),
    "entry_mode":        (0, 1, True),
    "retest_atr":        (0.1, 1.0, False),
    "retest_bars":       (4, 48, True),
    # --- v2.1: bounce_short (нож -> откат) ---
    "bounce_min_atr":    (0.5, 3.0, False),   # мин. отскок от лоу ножа, дн.ATR
    "bounce_max_frac":   (0.3, 0.9, False),   # потолок отката: доля пути назад
                                              # от лоу ножа к сломанному уровню
    # --- v2.1: rally_short (тренд-фильтр из ботов) ---
    "ma_gate":           (0, 1, True),        # 1 -> шорт только под SMA(ma_len)
    "ma_len":            (50, 400, True),     # период SMA в барах своего ТФ
    # --- v2.1: ворота из ботов (walk-forward у ботов прошли) ---
    "fund_gate":         (0, 1, True),        # 1 -> проверять funding
    "fund_thr":          (0.0, 0.06, False),  # порог, %/8ч (шорт: fund>=thr)
    "aroon_gate":        (0, 1, True),        # 1 -> проверять Aroon
    "aroon_n":           (10, 50, True),      # период Aroon в барах своего ТФ
    "aroon_thr":         (0, 100, True),      # шорт: AroonDown>=thr; лонг: Up
    # --- v2.2: штормовой фильтр («не торговать на невыгодном рынке») ---
    "storm_gate":        (0, 1, True),        # 1 -> проверять шторм
    "storm_day":         (0.03, 0.20, False), # |ход за 24ч| выше = шторм
    "storm_week":        (0.06, 0.40, False), # |ход за 7 суток| выше = шторм
    "storm_atr_rank":    (0.5, 1.0, False),   # перцентиль ATR выше = шторм
    "storm_mode":        (0, 2, True),        # 0 блок всего, 1 блок против
                                              # шторма, 2 торговать ТОЛЬКО
                                              # в шторм (гипотеза «паника»)
    # --- v2.2: хук предиктивного скоринга (действует только со score_fn) ---
    "score_gate":        (0, 1, True),        # 1 -> применять модель
    "score_min":         (0.3, 0.8, False),   # порог оценки 0..1
}

# Разумный дефолт (он же нейтральное семя для GA): стоп за свингом суток
# (stop_mode=1), все режимы разрешены, против-тренд строже, вход по рынку.
# Значения подобраны так, чтобы КАЖДЫЙ сетап давал живую статистику
# (в 2.2-7.5 раза больше сделок, чем у победителей v1) и ни одно ворото не
# резало >95% баров в одиночку:
#   window=60 (10 дней) — окно уровней; на окне 90+ прокол уровня для
#     sweep_long настолько редок, что одно ворото режет >95% баров;
#   stop_cap=2.4% — при плече 15 это ~9% старта риска на сделку;
#   drop_frac=3.3% за 3 дня — на 4.7% "движений" почти не остаётся;
#   poke_atr=0.2, age=6 — прокол хотя бы на 0.2 дневного ATR.
# Это НЕЙТРАЛЬНОЕ семя, а не рабочий конфиг: один общий геном на все 6
# сетапов заведомо компромисс (у range_* WR ниже безубытка 27% для 1:3).
# Тонкая настройка под каждый сетап — работа GA (walk-forward), не дефолтов.
DEFAULTS2 = dict(
    rsi_idx=2, window=60, zone=0.28, rsi_os=40, buf_atr=0.4,
    stop_mode=1, swing_bars=8, stop_atr_k=1.5, stop_cap=0.024,
    poke_atr=0.2, age=6, drop_frac=0.033, drop_days=3,
    hold_days=8, cooldown=3,
    reg_bull=1, reg_range=1, reg_bear=1,
    counter_rsi_shift=6, counter_zone_mult=0.7, with_rsi_shift=4,
    entry_mode=0, retest_atr=0.35, retest_bars=16,
    # v2.1. Ворота из ботов в семени ВЫКЛЮЧЕНЫ (0): семя обязано торговать,
    # а старые геномы из json (без этих генов) — вести себя как раньше;
    # включать ворота и подбирать пороги — работа GA. Пороги-заготовки
    # (fund_thr и т.п.) осмысленные, чтобы включение гена сразу было живым.
    bounce_min_atr=0.8, bounce_max_frac=0.75,
    ma_gate=0, ma_len=200,
    fund_gate=0, fund_thr=0.005,
    aroon_gate=0, aroon_n=25, aroon_thr=30,
    # v2.2. Шторм и скоринг в семени ВЫКЛЮЧЕНЫ (storm_gate=0, score_gate=0):
    # семя обязано вести себя ровно как до v2.2, а старые геномы из json (без
    # этих генов) — давать побитово те же сделки. Пороги-заготовки взяты
    # осмысленными, чтобы включение гена сразу давало живой фильтр:
    # 7% за сутки и 15% за неделю по BTC — это уже событие, а не шум.
    storm_gate=0, storm_day=0.07, storm_week=0.15, storm_atr_rank=0.85,
    storm_mode=0,
    score_gate=0, score_min=0.5,
)


def _gg(g, key):
    """Ген с запасным значением из DEFAULTS2: старые геномы (сохранённые до
    v2.1, без новых генов) обязаны работать без изменений поведения."""
    v = g.get(key)
    return DEFAULTS2[key] if v is None else v

TRADE_FIELDS = (
    "entry_ts", "exit_ts", "side", "entry", "stop", "tp", "pnl", "r", "reason",
    "hold_h", "mae_r", "mfe_r", "time_to_mae_h", "time_to_mfe_h",
    "would_hit_tp_later", "mfe_after_tp_r", "regime", "rsi", "zone_pos",
    "atr_pct", "stop_pct", "range_lo", "range_hi", "entry_mode",
    # дополнительные (не обязательные для чтения) поля:
    "signal_ts", "entry_ref", "swing_lo", "swing_hi", "stop_mode",
    "rsi_thr", "zone_thr", "bars_waited",
)

NM_FIELDS = (
    "ts", "side", "gate", "got", "need", "margin", "entry", "stop", "tp",
    "dist", "hypo_r", "hypo_reason", "hypo_mfe_r", "regime", "rsi",
    "zone_pos", "range_lo", "range_hi",
    # дополнительные:
    "hypo_pnl", "hypo_mae_r", "atr_pct", "stop_pct", "rsi_thr", "zone_thr",
)


def setup_side(setup):
    """'L' для лонговых сетапов, 'S' для шортовых."""
    return "L" if setup.endswith("long") else "S"


# --------------------------------------------------------------- индикаторы
def atr_daily_frac(c4, n=BARS_DAY_4H):
    """«Дневной» ATR как доля цены: Wilder-ATR по барам серии с периодом
    n = число баров ТФ в сутках (4ч -> 6, 1ч -> 24). На быстром ТФ значение
    закономерно меньше (средний ход одного бара) — пороги в ATR-единицах
    (poke_atr, bounce_min_atr, stop_atr_k...) на 1ч сжимаются сами собой."""
    m = len(c4)
    out = [None] * m
    if m < 2:
        return out
    prev_c = c4[0][4]
    val, trs = None, []
    for i in range(1, m):
        h, l, c = c4[i][2], c4[i][3], c4[i][4]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        prev_c = c
        if val is None:
            trs.append(tr)
            if len(trs) == n:
                val = sum(trs) / n
        else:
            val = (val * (n - 1) + tr) / n
        if val is not None and c:
            out[i] = val / c
    return out


def rolling_extremes_lagged(c4, window, age):
    """(lows[i], highs[i]) — экстремумы окна из `window` баров, которое
    ЗАКАНЧИВАЕТСЯ за `age` баров до i. age=1 -> окно [i-window .. i-1]
    (текущий бар не входит, заглядывания вперёд нет). Для sweep-сетапов
    age>1: уровень должен быть "старым", тогда прокол — сбор ликвидности."""
    m = len(c4)
    lows = [None] * m
    highs = [None] * m
    age = max(1, int(age))
    dq_min, dq_max = deque(), deque()
    for j in range(m):
        while dq_min and c4[dq_min[-1]][3] >= c4[j][3]:
            dq_min.pop()
        dq_min.append(j)
        while dq_max and c4[dq_max[-1]][2] <= c4[j][2]:
            dq_max.pop()
        dq_max.append(j)
        while dq_min[0] <= j - window:
            dq_min.popleft()
        while dq_max[0] <= j - window:
            dq_max.popleft()
        i = j + age                     # окно [j-window+1 .. j] обслуживает i
        if i < m and j >= window - 1:
            lows[i] = c4[dq_min[0]][3]
            highs[i] = c4[dq_max[0]][2]
    return lows, highs


def trailing_extremes(c4, n):
    """(lo[i], hi[i]) — экстремумы последних n баров ВКЛЮЧАЯ бар i.
    Это структура, уже известная на закрытии i (для stop_mode=1)."""
    m = len(c4)
    n = max(1, int(n))
    lo = [None] * m
    hi = [None] * m
    dq_min, dq_max = deque(), deque()
    for i in range(m):
        while dq_min and c4[dq_min[-1]][3] >= c4[i][3]:
            dq_min.pop()
        dq_min.append(i)
        while dq_max and c4[dq_max[-1]][2] <= c4[i][2]:
            dq_max.pop()
        dq_max.append(i)
        while dq_min[0] <= i - n:
            dq_min.popleft()
        while dq_max[0] <= i - n:
            dq_max.popleft()
        lo[i] = c4[dq_min[0]][3]
        hi[i] = c4[dq_max[0]][2]
    return lo, hi


def calc_ema(vals, period):
    """EMA по списку значений; None пока данных меньше period."""
    out = [None] * len(vals)
    if len(vals) < period or period < 1:
        return out
    k = 2.0 / (period + 1)
    s = sum(vals[:period]) / period
    out[period - 1] = s
    for i in range(period, len(vals)):
        s = vals[i] * k + s * (1 - k)
        out[i] = s
    return out


def calc_sma(vals, period):
    """Простая скользящая средняя; None пока данных меньше period."""
    out = [None] * len(vals)
    period = max(1, int(period))
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= period:
            s -= vals[i - period]
        if i >= period - 1:
            out[i] = s / period
    return out


def calc_aroon(c4, n):
    """(aroon_up, aroon_dn) за n баров: 100 -> экстремум на текущем баре,
    0 -> экстремум n-1 баров назад. Формула стандартная (как в ботах e5),
    только данные <= i."""
    n = max(2, int(n))
    m = len(c4)
    up = [None] * m
    dn = [None] * m
    dq_max, dq_min = deque(), deque()
    for i in range(m):
        h, l = c4[i][2], c4[i][3]
        while dq_max and c4[dq_max[-1]][2] <= h:
            dq_max.pop()
        dq_max.append(i)
        while dq_min and c4[dq_min[-1]][3] >= l:
            dq_min.pop()
        dq_min.append(i)
        while dq_max[0] <= i - n:
            dq_max.popleft()
        while dq_min[0] <= i - n:
            dq_min.popleft()
        if i >= n - 1:
            up[i] = (n - (i - dq_max[0])) / n * 100.0
            dn[i] = (n - (i - dq_min[0])) / n * 100.0
    return up, dn


def funding_on_bars(c4, bar_ms, series):
    """Ставка funding (%/8ч) на КАЖДЫЙ бар серии — последняя, УЖЕ
    РАССЧИТАННАЯ к моменту закрытия бара (единственная, которая реально
    известна трейдеру в этот момент).

    series — [(ts_ms расчёта, ставка_%за8ч)] из xd.fetch_funding, где ts —
    момент РАСЧЁТА (списания) ставки.

    Берём последнюю УЖЕ РАССЧИТАННУЮ на момент закрытия бара ставку.
    Прежняя версия брала ставку следующего расчёта («ставка периода известна
    с его начала») — это неверно: ставка формируется по премиум-индексу за
    предшествующие 8 часов и на момент закрытия бара ещё не существует.
    Аудит намерил заглядывание до 8ч на 178 из 200 последних 4ч-баров и
    расхождение решения ворота funding на 22% баров — то есть отбор мог бы
    получить бесплатное преимущество, недоступное в реальной торговле.
    """
    if not series:
        return [None] * len(c4)
    ts_list = [t for t, _ in series]
    vals = [v for _, v in series]
    out = [None] * len(c4)
    for i, c in enumerate(c4):
        tc = c[0] + bar_ms                  # момент закрытия бара
        j = bisect.bisect_right(ts_list, tc)
        if j > 0:
            out[i] = vals[j - 1]            # последняя рассчитанная к этому моменту
    return out


# -------------------------------------------- v2.2: ряды рыночного стресса
# Все ряды ниже строго причинные: значение на баре i считается по данным
# <= i. Единица окна — БАР СВОЕГО ТФ, число баров в сутках = 1440/interval_min,
# поэтому «сутки»/«неделя» одинаковы по смыслу на 4ч и на 1ч, а отдельные
# дневные свечи не подтягиваются (нет рассинхрона таймфреймов).
MIN_RANK_N = 10        # меньше 10 значений в окне — перцентиль не считаем


def pct_change_bars(closes, k):
    """Относительное изменение закрытия за k баров назад: c[i]/c[i-k]-1.
    Первые k баров — 0.0 («хода ещё не набралось»), не None: эти бары всё
    равно попадают в прогрев и не торгуются, зато потребителям (скоринг,
    сайт) не нужно защищаться от None."""
    k = max(1, int(k))
    out = [0.0] * len(closes)
    for i in range(k, len(closes)):
        base = closes[i - k]
        out[i] = (closes[i] / base - 1.0) if base else 0.0
    return out


def rolling_rank(vals, win, min_n=MIN_RANK_N):
    """Перцентиль vals[i] среди последних win значений ВКЛЮЧАЯ i: доля
    значений окна, которые <= текущего (0..1). None там, где значения нет
    или в окне меньше min_n чисел. Заглядывания вперёд нет — окно всегда
    заканчивается на i."""
    n = len(vals)
    out = [None] * n
    win = max(2, int(win))
    buf = deque()          # (индекс, значение) — значения окна по возрасту
    srt = []               # они же в отсортированном виде (bisect)
    for i, v in enumerate(vals):
        if v is not None:
            buf.append((i, v))
            bisect.insort(srt, v)
        while buf and buf[0][0] <= i - win:
            old = buf.popleft()[1]
            del srt[bisect.bisect_left(srt, old)]
        if v is not None and len(srt) >= min_n:
            out[i] = bisect.bisect_right(srt, v) / len(srt)
    return out


def rolling_median(vals, win, min_n=MIN_RANK_N):
    """Медиана последних win значений включая i (None при нехватке данных)."""
    n = len(vals)
    out = [None] * n
    win = max(2, int(win))
    buf = deque()
    srt = []
    for i, v in enumerate(vals):
        if v is not None:
            buf.append((i, v))
            bisect.insort(srt, v)
        while buf and buf[0][0] <= i - win:
            old = buf.popleft()[1]
            del srt[bisect.bisect_left(srt, old)]
        m = len(srt)
        if m >= min_n:
            out[i] = (srt[m // 2] if m % 2
                      else 0.5 * (srt[m // 2 - 1] + srt[m // 2]))
    return out


def daily_range_frac(c4, n_day):
    """«Дневной» диапазон как доля цены: (макс. хай - мин. лоу) последних
    n_day баров ВКЛЮЧАЯ i, делённый на закрытие i. Только данные <= i."""
    lo, hi = trailing_extremes(c4, n_day)
    out = [0.0] * len(c4)
    for i, c in enumerate(c4):
        px = c[4]
        if px and lo[i] is not None and hi[i] is not None:
            out[i] = (hi[i] - lo[i]) / px
    return out


_FUND_MEMO = {}                             # символ -> series (кэш на процесс)


def _load_funding(symbol):
    """Ряд funding из кэша ext_data; при недоступности — None (ворота
    funding тогда честно валятся как 'нет данных', движок не падает)."""
    if symbol in _FUND_MEMO:
        return _FUND_MEMO[symbol]
    try:
        series = xd.fetch_funding(symbol)
    except Exception:
        series = None
    _FUND_MEMO[symbol] = series
    return series


def prep_context(c4, interval_min=240, symbol="BTCUSDT"):
    """Общий контекст по серии сигнального ТФ (не зависит от генов).

    interval_min — интервал баров серии в минутах (240=4ч, 60=1ч);
                   старый вызов prep_context(c4) означает 4ч, как раньше.
    closes  — закрытия баров ТФ
    rsi     — {период: [RSI]} для всех периодов RSI_SET
    atr_d   — ДНЕВНОЙ ATR как ДОЛЯ цены (0.025 = 2.5%); период сглаживания
              = число баров ТФ в сутках (1440/interval_min)
    regime  — 0 bull / 1 range / 2 bear на каждый бар (e6.calc_regime,
              режим бара определяется ВЧЕРАШНИМ днём — без заглядывания)
    ema50/ema200 — EMA по 50 и 200 барам ТФ (для графика и контекста)
    fund    — funding %/8ч на закрытии каждого бара (см. funding_on_bars)
    interval_min / bar_ms / bars_day — параметры ТФ (их читает run_setup)

    v2.2 — ряды рыночного стресса (для штормового ворота, скоринга и сайта),
    все причинные, окна — в барах своего ТФ:
    ret_1d    — ход цены за последние сутки (1440/interval_min баров)
    ret_7d    — ход за 7 суток
    atr_d_pct — atr_d в процентах (None там же, где None у atr_d)
    atr_rank  — перцентиль текущего дневного ATR среди последних 90 суток
                (0..1; None в самом начале истории, где меньше 10 значений)
    vol_spike — дневной диапазон / его медиана за 30 суток (1.0 = обычный день)
    day_range — сам дневной диапазон (макс.хай-мин.лоу за сутки) как доля цены
    """
    interval_min = int(interval_min)
    closes = [c[4] for c in c4]
    n_day = bars_per_day(interval_min)
    atr_d = atr_daily_frac(c4, n=n_day)
    day_rng = daily_range_frac(c4, n_day)
    med30 = rolling_median(day_rng, 30 * n_day)
    vol_spike = [1.0] * len(c4)
    for i, m in enumerate(med30):
        if m:
            vol_spike[i] = day_rng[i] / m
    return dict(
        closes=closes,
        rsi={p: e2.bg.calc_rsi(closes, p) for p in RSI_SET},
        atr_d=atr_d,
        regime=e6.calc_regime(c4),
        ema50=calc_ema(closes, 50),
        ema200=calc_ema(closes, 200),
        fund=funding_on_bars(c4, bar_ms_of(interval_min),
                             _load_funding(symbol)),
        interval_min=interval_min,
        bar_ms=bar_ms_of(interval_min),
        bars_day=n_day,
        ret_1d=pct_change_bars(closes, n_day),
        ret_7d=pct_change_bars(closes, 7 * n_day),
        atr_d_pct=[None if a is None else a * 100.0 for a in atr_d],
        atr_rank=rolling_rank(atr_d, 90 * n_day),
        vol_spike=vol_spike,
        day_range=day_rng,
    )


# ---------------------------------------------------- v2.2: состояние рынка
def _ctx_num(ctx, key, i, default=0.0):
    """Число из ряда контекста с защитой от отсутствующего ряда/None:
    старый ctx (собранный до v2.2 или вручную) не должен ронять движок."""
    arr = ctx.get(key) if isinstance(ctx, dict) else None
    if arr is None or i < 0 or i >= len(arr) or arr[i] is None:
        return default
    return float(arr[i])


def _storm_calc(ctx, i, g):
    """Быстрый расчёт шторма: КОРТЕЖ, а не словарь.
    Зовётся на КАЖДОМ баре из gate_eval (GA делает тысячи прогонов), поэтому
    без лишних выделений памяти; словарную обёртку см. в storm_state.
    -> (storm, dir, strength, ret_1d, ret_7d, atr_rank, vol_spike)"""
    # ряды и гены разбираем ВРУЧНУЮ (без _ctx_num/_gg): это горячий путь,
    # вызовы функций тут стоят GA лишних процентов времени
    ar = ctx.get("ret_1d")
    r1 = (ar[i] or 0.0) if (ar is not None and 0 <= i < len(ar)) else 0.0
    aw = ctx.get("ret_7d")
    r7 = (aw[i] or 0.0) if (aw is not None and 0 <= i < len(aw)) else 0.0
    aa = ctx.get("atr_rank")
    rank = (aa[i] or 0.0) if (aa is not None and 0 <= i < len(aa)) else 0.0
    av = ctx.get("vol_spike")
    vs = (av[i] if (av is not None and 0 <= i < len(av)
                    and av[i] is not None) else 1.0)
    v = g.get("storm_day")
    thr_d = (DEFAULTS2["storm_day"] if v is None else v) or 1e-9
    v = g.get("storm_week")
    thr_w = (DEFAULTS2["storm_week"] if v is None else v) or 1e-9
    v = g.get("storm_atr_rank")
    thr_a = (DEFAULTS2["storm_atr_rank"] if v is None else v) or 1e-9
    s_d = (r1 if r1 >= 0 else -r1) / thr_d
    s_w = (r7 if r7 >= 0 else -r7) / thr_w
    s_a = rank / thr_a
    strength = s_d if s_d > s_w else s_w
    if s_a > strength:
        strength = s_a
    if strength > 1.0:
        base = r1 if s_d > 1.0 else (r7 if s_w > 1.0 else (r1 or r7))
        return (True, (1 if base > 0 else (-1 if base < 0 else 0)), strength,
                r1, r7, rank, vs)
    return (False, 0, strength, r1, r7, rank, vs)


def storm_state(ctx, i, g=None):
    """Состояние «шторма» на баре i по порогам генома g (без g — DEFAULTS2).

    Шторм — это невыгодный для среднесрочных сетапов рынок: слишком быстрый
    ход за сутки/неделю или аномально высокая волатильность. Три условия
    сведены в ОДНО число strength (сила в долях порога), чтобы у ворота был
    числовой запас и работал near-miss:
        strength = max(|ret_1d|/storm_day, |ret_7d|/storm_week,
                       atr_rank/storm_atr_rank),  шторм <=> strength > 1.
    Направление dir: -1 обвал, +1 ракета, 0 (не шторм / ход нулевой).
    Берётся у того условия, которое шторм и вызвало (сутки важнее недели);
    если шторм объявлен только по atr_rank — по знаку суточного хода.
    Нет данных (начало истории) -> ret=0, atr_rank=0 -> шторма нет.
    Только данные <= i (все ряды причинные, см. prep_context).
    """
    st, d, strength, r1, r7, rank, vs = _storm_calc(ctx, i, g or DEFAULTS2)
    return dict(storm=st, dir=d, strength=strength, ret_1d=r1, ret_7d=r7,
                atr_rank=rank, vol_spike=vs,
                atr_pct=_ctx_num(ctx, "atr_d_pct", i))


def market_context(c4, ctx, i, g=None):
    """Короткая сводка состояния рынка на баре i — для живого помощника
    («сейчас шторм: BTC -9.4% за сутки, входы заблокированы») и для плашки
    на сайте. Пороги шторма — из генома g (без g — DEFAULTS2).

    {"regime": 0/1/2, "regime_name": "bull|range|bear", "ret_1d", "ret_7d",
     "atr_rank", "storm": bool, "storm_dir": +1/-1/0, "atr_pct"}
    Дополнительно (не ломая обязательный состав): vol_spike, storm_strength,
    price, ts, bar_close_ts.
    """
    n = len(c4)
    if n:
        i = int(i)
        if i < 0:
            i += n
        i = min(max(i, 0), n - 1)
    st = storm_state(ctx, i, g)
    reg_arr = ctx.get("regime") if isinstance(ctx, dict) else None
    reg = int(reg_arr[i]) if reg_arr and i < len(reg_arr) else 1
    bar_ms = (int(ctx.get("bar_ms") or MS_4H) if isinstance(ctx, dict)
              else MS_4H)
    return dict(
        regime=reg, regime_name=REGIME_NAMES.get(reg, "?"),
        ret_1d=round(st["ret_1d"], 5), ret_7d=round(st["ret_7d"], 5),
        atr_rank=round(st["atr_rank"], 4), storm=bool(st["storm"]),
        storm_dir=int(st["dir"]), atr_pct=round(st["atr_pct"], 3),
        vol_spike=round(st["vol_spike"], 3),
        storm_strength=round(st["strength"], 3),
        price=(c4[i][4] if n else None), ts=(c4[i][0] if n else None),
        bar_close_ts=(c4[i][0] + bar_ms if n else None),
    )


# ------------------------------------------------------------ режимная логика
def regime_allowed(regime, g):
    """Разрешён ли режим рынка генами reg_bull/reg_range/reg_bear."""
    if regime == 0:
        return bool(g["reg_bull"])
    if regime == 1:
        return bool(g["reg_range"])
    return bool(g["reg_bear"])


def effective_thresholds(setup, regime, g):
    """(rsi_thr, zone_thr) с поправкой на режим рынка.

    Лонговый сетап: bear = ПРОТИВ тренда -> строже (RSI глубже на
    counter_rsi_shift, зона уже в counter_zone_mult раз); bull = ПО тренду ->
    мягче (RSI выше на with_rsi_shift); range = базовые пороги.
    Шортовый сетап: зеркально (bull = против тренда, bear = по тренду).
    Порог всегда выражен в "лонговой" шкале перепроданности; для шортов
    сравнение идёт с (100 - rsi_thr) и с зоной, отсчитанной СВЕРХУ.

    ОСОБЫЙ СЛУЧАЙ bounce_short: там ворото RSI обратное — RSI должен
    ПОДНЯТЬСЯ выше порога (перепроданность снята), поэтому "строже" =
    порог ВЫШЕ: в bull (против тренда) rsi_thr += counter_rsi_shift,
    в bear (по тренду) rsi_thr -= with_rsi_shift. Слот zone_thr для
    bounce_short несёт ЭФФЕКТИВНЫЙ bounce_max_frac (потолок отката):
    в bull он умножается на counter_zone_mult (откат допущен меньший)."""
    if setup == "bounce_short":
        counter = regime == 0                 # bull: шорт против тренда
        with_trend = regime == 2              # bear: нож по тренду
        rsi_thr = float(g["rsi_os"])
        zone_thr = float(_gg(g, "bounce_max_frac"))
        if counter:
            rsi_thr += float(g["counter_rsi_shift"])
            zone_thr *= float(g["counter_zone_mult"])
        elif with_trend:
            rsi_thr -= float(g["with_rsi_shift"])
        rsi_thr = min(max(rsi_thr, 3.0), 70.0)
        zone_thr = min(max(zone_thr, 0.01), 0.95)
        return rsi_thr, zone_thr
    is_long = setup.endswith("long")
    counter = (regime == 2) if is_long else (regime == 0)
    with_trend = (regime == 0) if is_long else (regime == 2)
    rsi_thr = float(g["rsi_os"])
    zone_thr = float(g["zone"])
    if counter:
        rsi_thr -= float(g["counter_rsi_shift"])
        zone_thr *= float(g["counter_zone_mult"])
    elif with_trend:
        rsi_thr += float(g["with_rsi_shift"])
    rsi_thr = min(max(rsi_thr, 3.0), 60.0)
    zone_thr = min(max(zone_thr, 0.01), 0.95)
    return rsi_thr, zone_thr


# ------------------------------------------------------------------- ворота
def _gate(name, got, need, margin, tol):
    """Ворото с числовым запасом. margin>=0 — прошло; margin<0 — недобор.
    near=True, если провалено, но не хватило меньше tol."""
    ok = margin >= 0
    return dict(name=name, ok=bool(ok), got=float(got), need=float(need),
                margin=float(margin),
                near=bool((not ok) and margin >= -abs(tol)))


def _gate_bool(name, ok, got=0.0, need=1.0):
    """Бинарное ворото (режим, направление свечи) — 'почти' не бывает."""
    return dict(name=name, ok=bool(ok), got=float(got), need=float(need),
                margin=0.0 if ok else -1.0, near=False)


def build_ext(setup, g, c4, interval_min=240):
    """Предрасчёт уровней под конкретный геном и сетап.
    lows/highs — границы диапазона окна window; лаг: age для sweep,
    длительность ножа (drop_days в барах ТФ) для bounce_short — уровень
    берётся ДО ножа (это и есть сломанный уровень), иначе 1;
    swing_lo/swing_hi — экстремумы последних swing_bars баров (stop_mode=1);
    knife_lo — лоу последних drop_days дней (дно ножа, bounce_short);
    sma — SMA(ma_len) закрытий (rally_short при ma_gate=1);
    aroon_up/aroon_dn — Aroon(aroon_n) (любой сетап при aroon_gate=1);
    win_lo/win_hi (v2.2) — экстремумы последних window баров ВКЛЮЧАЯ i
    (в отличие от lows/highs они без лага): нужны признакам range_pos и
    ret_since_extreme в bar_features, на ворота не влияют."""
    d_bars = int(g["drop_days"]) * bars_per_day(interval_min)
    if setup.startswith("sweep"):
        lag = int(g["age"])
    elif setup == "bounce_short":
        lag = d_bars
    else:
        lag = 1
    lows, highs = rolling_extremes_lagged(c4, int(g["window"]), lag)
    sw_lo, sw_hi = trailing_extremes(c4, int(g["swing_bars"]))
    win_lo, win_hi = trailing_extremes(c4, int(g["window"]))
    out = dict(lows=lows, highs=highs, swing_lo=sw_lo, swing_hi=sw_hi,
               lag=lag, window=int(g["window"]),
               swing_bars=int(g["swing_bars"]), d_bars=d_bars,
               knife_lo=None, sma=None, aroon_up=None, aroon_dn=None,
               win_lo=win_lo, win_hi=win_hi)
    if setup == "bounce_short":
        out["knife_lo"] = trailing_extremes(c4, d_bars)[0]
    if setup == "rally_short" and int(_gg(g, "ma_gate")):
        out["sma"] = calc_sma([c[4] for c in c4], int(_gg(g, "ma_len")))
    if int(_gg(g, "aroon_gate")):
        up, dn = calc_aroon(c4, int(_gg(g, "aroon_n")))
        out["aroon_up"], out["aroon_dn"] = up, dn
    return out


def _norm_ext(ext):
    """Совместимость: принимаем и dict из build_ext, и пару (lows, highs)."""
    if isinstance(ext, dict):
        return ext
    lows, highs = ext[0], ext[1]
    return dict(lows=lows, highs=highs, swing_lo=None, swing_hi=None, lag=1)


def _stop_price(side, i, c4, ctx, g, ext, ref):
    """Цена стопа по выбранному stop_mode. Использует только данные <= i."""
    sgn = 1 if side == "L" else -1
    atr = ctx["atr_d"][i]
    buf = float(g["buf_atr"]) * atr * ref
    mode = int(g["stop_mode"])
    _, o, h, l, c = c4[i]
    if mode == 0:                                   # за границей диапазона
        lo, hi = ext["lows"][i], ext["highs"][i]
        return (min(lo, l) - buf) if sgn == 1 else (max(hi, h) + buf)
    if mode == 1:                                   # за свингом swing_bars
        arr = ext.get("swing_lo") if sgn == 1 else ext.get("swing_hi")
        if arr is not None and arr[i] is not None:
            s = arr[i]
        else:                                       # запасной путь без ext
            n = max(1, int(g["swing_bars"]))
            seg = c4[max(0, i - n + 1):i + 1]
            s = min(x[3] for x in seg) if sgn == 1 else max(x[2] for x in seg)
        return (s - buf) if sgn == 1 else (s + buf)
    if mode == 2:                                   # k * дневной ATR от входа
        return ref * (1 - sgn * float(g["stop_atr_k"]) * atr)
    return (l - buf) if sgn == 1 else (h + buf)     # 3 = за фитилём бара


def gate_eval(setup, i, c4, ctx, g, ext):
    """Оценка всех ворот сетапа на ЗАКРЫТИИ бара i сигнального ТФ
    (какого именно — движок берёт из ctx["interval_min"], по умолчанию 4ч).

    Возвращает словарь с полным разбором: список ворот с числовыми запасами,
    цены входа/стопа/тейка, диагностика. ok=True -> сигнал есть.
    near_miss=True -> провалено ровно одно ворото и не хватило чуть-чуть."""
    ext = _norm_ext(ext)
    side = setup_side(setup)
    sgn = 1 if side == "L" else -1
    period = RSI_SET[int(g["rsi_idx"])]
    rsi_arr = ctx["rsi"][period]
    rsi = rsi_arr[i] if i < len(rsi_arr) else None
    atr = ctx["atr_d"][i]
    lo, hi = ext["lows"][i], ext["highs"][i]
    reg = ctx["regime"][i]
    s_on, s_dir, s_str, s_r1, s_r7, s_rank, s_vol = _storm_calc(ctx, i, g)
    diag = dict(rsi=rsi, zone_pos=None, atr=atr, regime=reg,
                range_lo=lo, range_hi=hi, rsi_thr=None, zone_thr=None,
                rsi_period=period, swing_lo=None, swing_hi=None,
                poke=None, move=None, stop_mode=int(g["stop_mode"]),
                bounce=None, knife_lo=None, fund=None, aroon=None, ma=None,
                # v2.2: рыночный стресс кладём ВСЕГДА (даже при storm_gate=0)
                # — сайт рисует контекст любого бара, а не только штормового
                ret_1d=s_r1, ret_7d=s_r7, atr_rank=s_rank, storm=s_on,
                storm_dir=s_dir, storm_strength=s_str, vol_spike=s_vol)
    out = dict(side=side, entry_ref=None, stop=None, tp=None, dist=None,
               gates=[], ok=False, n_failed=0, first_fail=None,
               near_miss=False, diag=diag)

    bars_day = int(ctx.get("bars_day", BARS_DAY_4H))
    d_bars = int(g["drop_days"]) * bars_day
    need_hist = (d_bars if setup.split("_")[0] in
                 ("dump", "pump", "bounce", "rally") else 1)
    if (rsi is None or atr is None or atr <= 0 or lo is None or hi is None
            or hi <= lo or i < need_hist or i >= len(c4)):
        out["gates"].append(_gate_bool(GATE_DATA, False))
        out["n_failed"] = 1
        out["first_fail"] = GATE_DATA
        return out

    ts, o, h, l, c = c4[i]
    rsi_thr, zone_thr = effective_thresholds(setup, reg, g)
    zone_pos = (c - lo) / (hi - lo)
    diag["zone_pos"] = zone_pos
    diag["rsi_thr"] = rsi_thr
    diag["zone_thr"] = zone_thr
    if ext.get("swing_lo") is not None:
        diag["swing_lo"] = ext["swing_lo"][i]
        diag["swing_hi"] = ext["swing_hi"][i]
    gates = [_gate_bool(GATE_REGIME, regime_allowed(reg, g), reg, 1.0)]

    # --- v2.2: ШТОРМ. Владелец: «при очень сильных движениях на дневке или
    # недельке отключать бота, чтобы он не торговал на невыгодном рынке».
    # Ворото стоит ПЕРЕД профильными условиями сетапа: это отказ от рынка
    # целиком, а не деталь сетапа. При storm_gate=0 (семя и все старые
    # геномы) ворото не добавляется вовсе — поведение прежнее.
    if int(_gg(g, "storm_gate")):
        mode = int(_gg(g, "storm_mode"))
        strength = s_str
        if mode == 2:
            # торговать ТОЛЬКО в шторм: сила должна ПРЕВЫШАТЬ порог
            gates.append(_gate(GATE_STORM, strength, 1.0, strength - 1.0,
                               TOL_STORM))
        else:
            against = s_on and ((s_dir < 0 and sgn == 1)
                                or (s_dir > 0 and sgn == -1))
            if mode == 1 and not against:
                # блокируем только вход ПРОТИВ шторма: по шторму и вне
                # шторма ворото пройдено (числового запаса тут нет)
                gates.append(_gate_bool(GATE_STORM, True, strength, 1.0))
            else:
                # mode 0 — любой вход в шторм запрещён; mode 1 — вход против
                gates.append(_gate(GATE_STORM, strength, 1.0, 1.0 - strength,
                                   TOL_STORM))

    if setup == "range_long":
        gates.append(_gate(GATE_ZONE, zone_pos, zone_thr, zone_thr - zone_pos,
                           TOL_ZONE * zone_thr))
        gates.append(_gate(GATE_RSI, rsi, rsi_thr, rsi_thr - rsi, TOL_RSI))
    elif setup == "range_short":
        need_z = 1.0 - zone_thr
        gates.append(_gate(GATE_ZONE, zone_pos, need_z, zone_pos - need_z,
                           TOL_ZONE * zone_thr))
        need_r = 100.0 - rsi_thr
        gates.append(_gate(GATE_RSI, rsi, need_r, rsi - need_r, TOL_RSI))
    elif setup == "sweep_long":
        poke = (lo - l) / (atr * c)          # глубина прокола в дневных ATR
        diag["poke"] = poke
        gates.append(_gate(GATE_POKE, poke, float(g["poke_atr"]),
                           poke - float(g["poke_atr"]),
                           TOL_POKE * float(g["poke_atr"])))
        gates.append(_gate_bool(GATE_RECLAIM, c > lo, (c - lo) / c, 0.0))
    elif setup == "sweep_short":
        poke = (h - hi) / (atr * c)
        diag["poke"] = poke
        gates.append(_gate(GATE_POKE, poke, float(g["poke_atr"]),
                           poke - float(g["poke_atr"]),
                           TOL_POKE * float(g["poke_atr"])))
        gates.append(_gate_bool(GATE_RECLAIM, c < hi, (hi - c) / c, 0.0))
    elif setup == "dump_long":
        base = ctx["closes"][i - d_bars]
        move = (base - c) / base
        diag["move"] = move
        gates.append(_gate(GATE_MOVE, move, float(g["drop_frac"]),
                           move - float(g["drop_frac"]),
                           TOL_MOVE * float(g["drop_frac"])))
        gates.append(_gate_bool(GATE_CANDLE, c > o, (c - o) / o, 0.0))
        gates.append(_gate(GATE_RSI, rsi, rsi_thr, rsi_thr - rsi, TOL_RSI))
    elif setup == "pump_short":
        base = ctx["closes"][i - d_bars]
        move = (c - base) / base
        diag["move"] = move
        gates.append(_gate(GATE_MOVE, move, float(g["drop_frac"]),
                           move - float(g["drop_frac"]),
                           TOL_MOVE * float(g["drop_frac"])))
        gates.append(_gate_bool(GATE_CANDLE, c < o, (o - c) / o, 0.0))
        need_r = 100.0 - rsi_thr
        gates.append(_gate(GATE_RSI, rsi, need_r, rsi - need_r, TOL_RSI))
    elif setup == "bounce_short":
        # «Нож -> откат»: обвал за drop_days пробил лоу диапазона (lo здесь —
        # уровень ДО ножа: окно window с лагом d_bars, см. build_ext), цена
        # отскочила от дна ножа, но откат ограничен ДВАЖДЫ: долей
        # восстановления падения (bounce_max_frac) и сломанным уровнем
        # (не выше lo + допуск) — шортим откат (dead-cat bounce).
        base = ctx["closes"][i - d_bars]
        k_arr = ext.get("knife_lo")
        knife_lo = (k_arr[i] if k_arr is not None else
                    min(x[3] for x in c4[max(0, i - d_bars + 1):i + 1]))
        diag["knife_lo"] = knife_lo
        drop = (base - knife_lo) / base          # глубина ножа за drop_days
        diag["move"] = drop
        gates.append(_gate(GATE_MOVE, drop, float(g["drop_frac"]),
                           drop - float(g["drop_frac"]),
                           TOL_MOVE * float(g["drop_frac"])))
        atr_abs = atr * c
        bounce = (c - knife_lo) / atr_abs        # отскок от дна, в дневных ATR
        diag["bounce"] = bounce
        b_min = float(_gg(g, "bounce_min_atr"))
        gates.append(_gate(GATE_BOUNCE, bounce, b_min, bounce - b_min,
                           TOL_BOUNCE * b_min))
        # потолок отката №1: восстановлено не больше zone_thr (эффективный
        # bounce_max_frac — см. effective_thresholds) от ПАДЕНИЯ base->дно
        fall = base - knife_lo
        if fall > 0:
            rec_frac = (c - knife_lo) / fall     # доля восстановления падения
            diag["zone_pos"] = rec_frac          # в паре с zone_thr для UI
            gates.append(_gate(GATE_ZONE, rec_frac, zone_thr,
                               zone_thr - rec_frac, TOL_ZONE * zone_thr))
        else:
            gates.append(_gate_bool(GATE_ZONE, False, 0.0, 1.0))
        # потолок отката №2: не выше СЛОМАННОГО уровня (+допуск 0.25 ATR);
        # если нож уровень не пробивал (дно >= lo) — ретеста сломанного
        # уровня не существует, ворото валится без "почти".
        # Имя ворота то же, что у sweep («возврат за уровень»): отказ здесь
        # означает именно «цена вернулась НАД сломанный уровень».
        if knife_lo < lo:
            head = (lo + LEVEL_TOL_ATR * atr_abs - c) / atr_abs
            gates.append(_gate(GATE_RECLAIM, head, 0.0, head, LEVEL_TOL_ATR))
        else:
            gates.append(_gate_bool(GATE_RECLAIM, False, 0.0, 1.0))
        # перепроданность СНЯТА: RSI поднялся над порогом (строже в bull)
        gates.append(_gate(GATE_RSI, rsi, rsi_thr, rsi - rsi_thr, TOL_RSI))
    elif setup == "rally_short":
        # Тренд-шорт: перегретый рост за drop_days. Режим ЦЕНТРАЛЕН: в bear
        # это по тренду (RSI-порог мягче), в bull — против (строже); сама
        # раскладка порогов сделана в effective_thresholds (стандартный
        # шортовый путь: сравнение с 100 - rsi_thr).
        base = ctx["closes"][i - d_bars]
        move = (c - base) / base
        diag["move"] = move
        gates.append(_gate(GATE_MOVE, move, float(g["drop_frac"]),
                           move - float(g["drop_frac"]),
                           TOL_MOVE * float(g["drop_frac"])))
        gates.append(_gate_bool(GATE_CANDLE, c < o, (o - c) / o, 0.0))
        need_r = 100.0 - rsi_thr
        gates.append(_gate(GATE_RSI, rsi, need_r, rsi - need_r, TOL_RSI))
        if int(_gg(g, "ma_gate")):
            # тренд-фильтр из ботов: шорт только под SMA(ma_len) своего ТФ
            sma_arr = ext.get("sma")
            sma = sma_arr[i] if sma_arr is not None else None
            diag["ma"] = sma
            if sma is None or sma <= 0:
                gates.append(_gate_bool(GATE_MA, False))
            else:
                below = (sma - c) / sma          # >0 — цена под SMA
                gates.append(_gate(GATE_MA, below, 0.0, below, TOL_MA))
    else:
        raise ValueError(f"неизвестный сетап: {setup}")

    # --- ворота из ботов: funding и Aroon (общие для всех сетапов) ---
    if int(_gg(g, "fund_gate")):
        fund_arr = ctx.get("fund")
        rate = (fund_arr[i] if fund_arr is not None and i < len(fund_arr)
                else None)
        diag["fund"] = rate
        f_thr = float(_gg(g, "fund_thr"))
        if rate is None:
            gates.append(_gate_bool(GATE_FUND, False))
        elif sgn == 1:
            # лонг: шортисты платят -> funding <= -fund_thr
            gates.append(_gate(GATE_FUND, rate, -f_thr, -f_thr - rate,
                               TOL_FUND))
        else:
            # шорт: лонгисты платят -> funding >= fund_thr
            gates.append(_gate(GATE_FUND, rate, f_thr, rate - f_thr,
                               TOL_FUND))
    if int(_gg(g, "aroon_gate")):
        arr = ext.get("aroon_up") if sgn == 1 else ext.get("aroon_dn")
        a_val = arr[i] if arr is not None else None
        diag["aroon"] = a_val
        a_thr = float(_gg(g, "aroon_thr"))
        if a_val is None:
            gates.append(_gate_bool(GATE_AROON, False))
        else:
            gates.append(_gate(GATE_AROON, a_val, a_thr, a_val - a_thr,
                               TOL_AROON))

    # --- стоп и его ширина (жёсткий лимит риска под плечо 15-20) ---
    ref = c
    stop_px = _stop_price(side, i, c4, ctx, g, ext, ref)
    dist = sgn * (ref - stop_px) / ref
    cap = float(g["stop_cap"])
    if dist <= 0:
        gates.append(_gate(GATE_STOP, dist, MIN_STOP, dist - MIN_STOP,
                           0.5 * MIN_STOP))
    elif dist > cap:
        gates.append(_gate(GATE_STOP, dist, cap, cap - dist, TOL_STOP * cap))
    elif dist < MIN_STOP:
        gates.append(_gate(GATE_STOP, dist, MIN_STOP, dist - MIN_STOP,
                           0.5 * MIN_STOP))
    else:
        gates.append(_gate(GATE_STOP, dist, cap,
                           min(cap - dist, dist - MIN_STOP), TOL_STOP * cap))
    out["entry_ref"] = ref
    out["stop"] = stop_px
    out["dist"] = dist if dist > 0 else None
    out["tp"] = ref * (1 + sgn * RR * dist) if dist > 0 else None

    failed = [q for q in gates if not q["ok"]]
    out["gates"] = gates
    out["n_failed"] = len(failed)
    out["first_fail"] = failed[0]["name"] if failed else None
    out["ok"] = not failed
    out["near_miss"] = bool(len(failed) == 1 and failed[0]["near"])
    return out


# -------------------------------------------- v2.2: признаки для скоринга
# Полный и СТАБИЛЬНЫЙ состав признаков бара (латиница). Модель обучает другой
# агент; движок гарантирует лишь причинность (<= i) и отсутствие None/NaN.
SCORE_FEATURES = (
    "side",               # 1 лонг / -1 шорт
    "regime",             # 0 bull / 1 range / 2 bear
    "reg_bull", "reg_range", "reg_bear",   # то же one-hot (0/1)
    "rsi",                # RSI выбранного геномом периода на закрытии i
    "rsi_thr",            # действующий порог RSI (с поправкой на режим)
    "dist_to_thr",        # запас по RSI в пунктах, >0 = условие с запасом
    "zone_pos",           # положение закрытия в диапазоне сетапа [lo..hi]
    "range_pos",          # положение в окне window (экстремумы включая i)
    "atr_pct",            # дневной ATR, % цены
    "atr_rank",           # перцентиль ATR за 90 суток, 0..1
    "vol_spike",          # дневной диапазон / медиана за 30 суток
    "ret_1d", "ret_7d",   # ход цены за сутки / за 7 суток (доли)
    "ret_since_extreme",  # ход от экстремума окна window (лонг: от лоу)
    "storm",              # 1 = шторм по порогам генома
    "ema50_dist", "ema200_dist",   # (c - EMA)/EMA
    "ema_slope50",        # наклон EMA50 за сутки, доля
    "funding",            # ставка %/8ч на закрытии бара
    "aroon_up", "aroon_dn",        # 0..100
    "body_frac",          # |c-o| / (h-l)
    "upper_wick_frac", "lower_wick_frac",
    "bars_since_signal",  # баров с прошлого сигнала (999 = неизвестно/давно)
    "hour_utc", "dow",    # час UTC и день недели (0=пн) на ЗАКРЫТИИ бара
    "stop_pct",           # планируемая ширина стопа, %
)


def _fin(x, default=0.0):
    """Число или дефолт: None/NaN/inf в признаки попадать не должны."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float(default)
    if v != v or v == float("inf") or v == float("-inf"):
        return float(default)
    return v


def bar_features(setup, i, c4, ctx, g, ext, bars_since_signal=None):
    """Признаки бара i для предиктивной модели («предугадывание похода
    цены»). Движок модель не обучает и не знает — он только отдаёт признаки
    и умеет спросить оценку (run_setup(score_fn=...)); это инфраструктура.

    ВСЕ признаки строго причинные: считаются по данным <= i (закрытие бара
    i уже известно, будущего нет). Состав ключей — SCORE_FEATURES, значения
    всегда числа: где данных нет (прогрев индикаторов), стоит нейтральный
    дефолт (0.0, для zone_pos/range_pos — 0.5), чтобы модель не падала.

    bars_since_signal — сколько баров прошло с предыдущего сигнала этого
    сетапа; run_setup передаёт его сам, при прямом вызове можно не задавать
    (тогда 999 = «давно/неизвестно»).
    """
    ext = _norm_ext(ext)
    side = setup_side(setup)
    sgn = 1 if side == "L" else -1
    n = len(c4)
    i = min(max(int(i), 0), max(0, n - 1))
    ts, o, h, l, c = c4[i]
    is_d = isinstance(ctx, dict)
    bar_ms = int(ctx.get("bar_ms") or MS_4H) if is_d else MS_4H
    n_day = int(ctx.get("bars_day") or BARS_DAY_4H) if is_d else BARS_DAY_4H

    period = RSI_SET[int(g["rsi_idx"])]
    rsi_arr = ctx["rsi"][period]
    rsi = _fin(rsi_arr[i] if i < len(rsi_arr) else None, 50.0)
    reg_arr = ctx.get("regime")
    reg = int(reg_arr[i]) if reg_arr and i < len(reg_arr) else 1
    rsi_thr, _zone_thr = effective_thresholds(setup, reg, g)
    if setup == "bounce_short":
        dist_to_thr = rsi - rsi_thr                 # RSI должен быть ВЫШЕ
    elif side == "S":
        dist_to_thr = rsi - (100.0 - rsi_thr)       # перекупленность
    else:
        dist_to_thr = rsi_thr - rsi                 # перепроданность

    atr = ctx["atr_d"][i] if i < len(ctx["atr_d"]) else None
    stm = storm_state(ctx, i, g)

    # зона сетапа (с лагом, как в воротах) и окно window (без лага)
    lo, hi = ext["lows"][i], ext["highs"][i]
    zone_pos = ((c - lo) / (hi - lo)
                if lo is not None and hi is not None and hi > lo else 0.5)
    w_lo_arr, w_hi_arr = ext.get("win_lo"), ext.get("win_hi")
    if w_lo_arr is not None and w_hi_arr is not None:
        w_lo, w_hi = w_lo_arr[i], w_hi_arr[i]
    else:                                   # ext без v2.2-полей: считаем сами
        seg = c4[max(0, i - int(g["window"]) + 1):i + 1]
        w_lo, w_hi = min(x[3] for x in seg), max(x[2] for x in seg)
    range_pos = ((c - w_lo) / (w_hi - w_lo)
                 if w_hi is not None and w_lo is not None and w_hi > w_lo
                 else 0.5)
    # лонг мерит ход от лоу окна, шорт — от хая окна
    ref_ext = w_lo if sgn == 1 else w_hi
    ret_since_extreme = (c - ref_ext) / ref_ext if ref_ext else 0.0

    ema50 = ctx.get("ema50") or []
    ema200 = ctx.get("ema200") or []
    e50 = ema50[i] if i < len(ema50) else None
    e200 = ema200[i] if i < len(ema200) else None
    e50_prev = ema50[i - n_day] if i - n_day >= 0 and i < len(ema50) else None
    slope = ((e50 - e50_prev) / e50_prev
             if e50 is not None and e50_prev else 0.0)

    # Aroon: если геном его не включал, ext-массивов нет — считаем локально
    # по последним aroon_n барам (тот же смысл, только данные <= i)
    a_up_arr, a_dn_arr = ext.get("aroon_up"), ext.get("aroon_dn")
    if (a_up_arr is not None and a_dn_arr is not None
            and a_up_arr[i] is not None):
        a_up, a_dn = a_up_arr[i], a_dn_arr[i]
    else:
        nn = max(2, int(_gg(g, "aroon_n")))
        seg = c4[max(0, i - nn + 1):i + 1]
        m = len(seg)
        i_hi = max(range(m), key=lambda k: (seg[k][2], k))
        i_lo = min(range(m), key=lambda k: (seg[k][3], -k))
        a_up, a_dn = (i_hi + 1) / m * 100.0, (i_lo + 1) / m * 100.0

    rng = h - l
    body = abs(c - o) / rng if rng > 0 else 0.0
    up_wick = (h - max(o, c)) / rng if rng > 0 else 0.0
    dn_wick = (min(o, c) - l) / rng if rng > 0 else 0.0

    # планируемая ширина стопа (тот же расчёт, что в воротах); на прогреве,
    # где нужных уровней ещё нет, честно отдаём 0.0 вместо падения
    no_levels = int(g["stop_mode"]) == 0 and (lo is None or hi is None)
    if atr is None or atr <= 0 or no_levels:
        stop_pct = 0.0
    else:
        stop_px = _stop_price(side, i, c4, ctx, g, ext, c)
        stop_pct = sgn * (c - stop_px) / c * 100.0 if c else 0.0

    ts_close = ts + bar_ms                  # сигнал существует с закрытия
    bs = (999.0 if bars_since_signal is None
          else float(min(999, max(0, int(bars_since_signal)))))
    return dict(
        side=float(sgn), regime=float(reg),
        reg_bull=1.0 if reg == 0 else 0.0,
        reg_range=1.0 if reg == 1 else 0.0,
        reg_bear=1.0 if reg == 2 else 0.0,
        rsi=_fin(rsi, 50.0), rsi_thr=_fin(rsi_thr, 50.0),
        dist_to_thr=_fin(dist_to_thr),
        zone_pos=_fin(zone_pos, 0.5), range_pos=_fin(range_pos, 0.5),
        atr_pct=_fin(None if atr is None else atr * 100.0),
        atr_rank=_fin(stm["atr_rank"]), vol_spike=_fin(stm["vol_spike"], 1.0),
        ret_1d=_fin(stm["ret_1d"]), ret_7d=_fin(stm["ret_7d"]),
        ret_since_extreme=_fin(ret_since_extreme),
        storm=1.0 if stm["storm"] else 0.0,
        ema50_dist=_fin((c - e50) / e50 if e50 else None),
        ema200_dist=_fin((c - e200) / e200 if e200 else None),
        ema_slope50=_fin(slope),
        funding=_fin(_ctx_num(ctx, "fund", i)),
        aroon_up=_fin(a_up, 50.0), aroon_dn=_fin(a_dn, 50.0),
        body_frac=_fin(body), upper_wick_frac=_fin(up_wick),
        lower_wick_frac=_fin(dn_wick),
        bars_since_signal=bs,
        hour_utc=float((ts_close // 3600000) % 24),
        dow=float((ts_close // 86400000 + 3) % 7),
        stop_pct=_fin(stop_pct),
    )


# ------------------------------------------------- v2.2: кулдаун (единая база)
def cooldown_ms(g, interval_min=240):
    """Длительность кулдауна в мс: ген cooldown задан в БАРАХ СВОЕГО ТФ."""
    return int(_gg(g, "cooldown")) * bar_ms_of(interval_min)


def signal_ts(bar_ts, interval_min=240):
    """Момент, когда сигнал бара становится известен, — ЗАКРЫТИЕ бара
    (открытие бара сигналом не является: на нём данных бара ещё нет)."""
    return int(bar_ts) + bar_ms_of(interval_min)


def cooldown_ok(bar_ts, last_exit_ts, g, interval_min=240):
    """Прошёл ли кулдаун к моменту сигнала бара bar_ts (bar_ts — ОТКРЫТИЕ
    бара, как во всех сериях движка). ЕДИНАЯ трактовка для бэктеста и
    живого помощника: сигнал существует с ЗАКРЫТИЯ бара, значит и кулдаун
    сравнивается с закрытием, а не с открытием. Сравнение с открытием
    (как раньше в советнике) отбрасывает лишний бар и живой помощник
    молчит там, где бэктест торговал."""
    if not last_exit_ts:
        return True
    return (signal_ts(bar_ts, interval_min)
            >= int(last_exit_ts) + cooldown_ms(g, interval_min))


# ---------------------------------------------------------------- исполнение
def simulate_trade(side, entry_i15, entry_px, stop_px, tp_px, c15, lev,
                   hold_bars15):
    """Ведёт сделку по 15м-свечам от бара entry_i15.

    entry_px — намеченная цена входа (open 15м-бара или лимит ретеста);
    фактический фил берётся с проскальзыванием в худшую сторону, комиссия
    входа — тейкерская (консервативно, даже для лимитки).
    Стоп и тейк в одной свече -> СТОП. Ликвидация проверяется ПЕРЕД стопом,
    только если цена ликвидации ближе к входу, чем стоп. Порог ликвидации —
    (1/плечо - MMR), как на изолированной марже Bybit: прежний вариант
    MM/lev (MM=0.95) ставил ликвидацию ДАЛЬШЕ реальной и терял в убытке 5%
    маржи, то есть ошибался в оптимистичную сторону — недопустимо для
    модуля, чья задача держать стоп внутри ликвидации.

    MAE/MFE — максимальные ходы против/в пользу позиции в долях риска R,
    где R = |фил - стоп|. would_hit_tp_later — если выбило стопом, дошло бы
    до тейка до конца окна удержания. mfe_after_tp_r — если тейк, сколько
    ЕЩЁ R прошло бы после тейка до конца окна."""
    sgn = 1 if side == "L" else -1
    fill = entry_px * (1 + sgn * SLIP)
    qty = MARGIN * lev / fill
    fees = qty * fill * TAKER
    risk_px = abs(fill - stop_px)
    if risk_px <= 0:
        risk_px = fill * MIN_STOP
    liq_px = fill * (1 - sgn * liq_frac(lev))
    liq_closer = (liq_px > stop_px) if sgn == 1 else (liq_px < stop_px)
    end_i = min(entry_i15 + int(hold_bars15), len(c15) - 1)

    mae = mfe = 0.0
    i_mae = i_mfe = entry_i15
    pnl, exit_i, reason = None, end_i, "timeout"

    for k in range(entry_i15, end_i + 1):
        _, o_, h_, l_, c_ = c15[k]
        fees += qty * c_ * FUND_8H / 32          # фандинг: 8ч = 32 бара по 15м
        adv = (fill - l_) if sgn == 1 else (h_ - fill)
        fav = (h_ - fill) if sgn == 1 else (fill - l_)
        if adv > mae:
            mae, i_mae = adv, k
        if fav > mfe:
            mfe, i_mfe = fav, k
        hit_liq = (l_ <= liq_px) if sgn == 1 else (h_ >= liq_px)
        hit_stop = (l_ <= stop_px) if sgn == 1 else (h_ >= stop_px)
        hit_tp = (h_ >= tp_px) if sgn == 1 else (l_ <= tp_px)
        if liq_closer and hit_liq:
            pnl, exit_i, reason = -MARGIN - fees, k, "liq"
            break
        if hit_stop:
            px = stop_px * (1 - sgn * SLIP)
            pnl = sgn * (px - fill) * qty - qty * px * TAKER - fees
            exit_i, reason = k, "stop"
            break
        if hit_liq:
            pnl, exit_i, reason = -MARGIN - fees, k, "liq"
            break
        if hit_tp:
            pnl = sgn * (tp_px - fill) * qty - qty * tp_px * MAKER - fees
            exit_i, reason = k, "tp"
            break
    if pnl is None:
        px = c15[end_i][4] * (1 - sgn * SLIP)
        pnl = sgn * (px - fill) * qty - qty * px * TAKER - fees
        exit_i, reason = end_i, "timeout"

    would_later, mfe_after = False, 0.0
    if reason in ("stop", "liq"):
        for k in range(exit_i + 1, end_i + 1):
            _, o_, h_, l_, c_ = c15[k]
            if (h_ >= tp_px) if sgn == 1 else (l_ <= tp_px):
                would_later = True
                break
    elif reason == "tp":
        best = mfe
        for k in range(exit_i + 1, end_i + 1):
            _, o_, h_, l_, c_ = c15[k]
            fav = (h_ - fill) if sgn == 1 else (fill - l_)
            if fav > best:
                best = fav
        mfe_after = max(0.0, best / risk_px - RR)

    return dict(pnl=pnl, exit_i15=exit_i, reason=reason,
                mae_r=round(max(0.0, mae) / risk_px, 3),
                mfe_r=round(max(0.0, mfe) / risk_px, 3),
                time_to_mae_h=round((i_mae - entry_i15) * 0.25, 2),
                time_to_mfe_h=round((i_mfe - entry_i15) * 0.25, 2),
                would_hit_tp_later=bool(would_later),
                mfe_after_tp_r=round(mfe_after, 3),
                fill=fill, risk_px=risk_px, qty=qty)


def _plan_entry(side, ts4, entry_ref, atr, g, c15, ts15, bar_ms=MS_4H):
    """Куда и по какой цене входим после закрытия сигнального бара
    (bar_ms — длительность бара его ТФ; сигнал существует только с закрытия).
    Возвращает (entry_i15, entry_px, bars_waited) либо (None, причина, None).
    entry_mode=0 — по open первой 15м-свечи после закрытия сигнального бара.
    entry_mode=1 — лимитка лучше на retest_atr*ATR, ждём retest_bars
    15М-БАРОВ (ген в 15м-барах на любом ТФ — окно ожидания физическое)."""
    close_ts = ts4 + bar_ms
    j = bisect.bisect_left(ts15, close_ts)
    # Найденная свеча обязана быть ИМЕННО следующей после закрытия сигнала.
    # Без этой проверки дырка в 15м-данных (или 15м-серия, начинающаяся позже
    # 4ч-серии) заставила бы bisect молча вернуть бар, отстоящий на часы или
    # дни, и сделка исполнилась бы по цене из будущего вообще без отказа.
    if j >= len(c15) - 2 or ts15[j] - close_ts > MS_15M:
        return None, REJ_NO15M, None
    if int(g["entry_mode"]) == 0:
        return j, c15[j][1], 0
    sgn = 1 if side == "L" else -1
    limit = entry_ref * (1 - sgn * float(g["retest_atr"]) * atr)
    end = min(j + int(g["retest_bars"]), len(c15) - 1)
    for k in range(j, end + 1):
        _, o_, h_, l_, c_ = c15[k]
        if sgn == 1:
            if o_ <= limit:                  # открылись уже ниже лимита
                return k, o_, k - j
            if l_ <= limit:
                return k, limit, k - j
        else:
            if o_ >= limit:
                return k, o_, k - j
            if h_ >= limit:
                return k, limit, k - j
    return None, REJ_RETEST, None


def _make_trade(setup, ev_, i, c4, ctx, g, c15, ts15, lev, hypo=False,
                bar_ms=MS_4H):
    """Общий путь сделки (реальной и гипотетической near-miss).
    Возвращает (trade_dict, None) или (None, причина отказа).
    hypo=True — считаем "а что было бы, если бы вошли": ВЕРХНИЙ лимит
    ширины стопа не проверяем (near-miss по ширине стопа его как раз и не
    прошёл), но нижний (MIN_STOP) и границу ликвидации проверяем всё равно:
    без MIN_STOP в гипотетику попадали сделки с шумовым стопом 0.3%, у
    которых фиксированные издержки съедали до -1.4R и односторонне
    занижали средний near-miss; без границы ликвидации гипотетика считалась
    бы там, где позицию реально ликвидировало бы раньше стопа."""
    side = ev_["side"]
    sgn = 1 if side == "L" else -1
    ts4 = c4[i][0]
    atr = ctx["atr_d"][i]
    stop_px = ev_["stop"]
    plan = _plan_entry(side, ts4, ev_["entry_ref"], atr, g, c15, ts15,
                       bar_ms=bar_ms)
    if plan[0] is None:
        return None, plan[1]
    entry_i15, entry_px, waited = plan
    dist = sgn * (entry_px - stop_px) / entry_px
    # Лимит риска на ФАКТИЧЕСКОМ входе: прежний допуск cap*1.25 позволял
    # стопу дорасти до 4.4% при cap 3.5% и плече x20 — это уже вплотную к
    # реальной ликвидации (4.5%), то есть стоп мог оказаться за ней.
    # Теперь жёстко: не шире cap и не дальше 80% пути до ликвидации.
    hard_cap = 0.8 * liq_frac(lev)
    cap = hard_cap if hypo else min(float(g["stop_cap"]), hard_cap)
    if dist <= 0 or dist < MIN_STOP or dist > cap:
        return None, REJ_ENTRY_STOP
    tp_px = entry_px * (1 + sgn * RR * dist)
    sim = simulate_trade(side, entry_i15, entry_px, stop_px, tp_px, c15, lev,
                         int(g["hold_days"]) * BARS_DAY_15)
    entry_ts = ts15[entry_i15]
    exit_ts = ts15[sim["exit_i15"]]
    risk_usd = MARGIN * lev * dist
    d = ev_["diag"]
    tr = dict(
        entry_ts=entry_ts, exit_ts=exit_ts, side=side,
        entry=round(entry_px, 2), stop=round(stop_px, 2), tp=round(tp_px, 2),
        pnl=round(sim["pnl"], 4),
        r=round(sim["pnl"] / risk_usd, 3) if risk_usd else 0.0,
        reason=sim["reason"],
        hold_h=round((exit_ts - entry_ts) / 3600000.0, 2),
        mae_r=sim["mae_r"], mfe_r=sim["mfe_r"],
        time_to_mae_h=sim["time_to_mae_h"], time_to_mfe_h=sim["time_to_mfe_h"],
        would_hit_tp_later=sim["would_hit_tp_later"],
        mfe_after_tp_r=sim["mfe_after_tp_r"],
        regime=d["regime"],
        rsi=round(d["rsi"], 1) if d["rsi"] is not None else None,
        zone_pos=round(d["zone_pos"], 3) if d["zone_pos"] is not None else None,
        atr_pct=round(atr * 100, 3),
        stop_pct=round(dist * 100, 3),
        range_lo=round(d["range_lo"], 2) if d["range_lo"] else None,
        range_hi=round(d["range_hi"], 2) if d["range_hi"] else None,
        entry_mode=int(g["entry_mode"]),
        signal_ts=ts4, entry_ref=round(ev_["entry_ref"], 2),
        swing_lo=round(d["swing_lo"], 2) if d.get("swing_lo") else None,
        swing_hi=round(d["swing_hi"], 2) if d.get("swing_hi") else None,
        stop_mode=int(g["stop_mode"]),
        rsi_thr=round(d["rsi_thr"], 1) if d["rsi_thr"] is not None else None,
        zone_thr=round(d["zone_thr"], 3) if d["zone_thr"] is not None else None,
        bars_waited=waited, setup=setup,
    )
    return tr, None


# -------------------------------------------------------------- полный прогон
def run_setup(setup, g, c4, ctx, c15, ts15, lev, signal_range=None,
              collect_diag=True, interval_min=None, score_fn=None,
              score_min=None):
    """Прогон сетапа по всей истории.

    interval_min — ТФ сигнальных баров в минутах (240=4ч, 60=1ч).
    None (по умолчанию) — берётся из ctx["interval_min"] (его пишет
    prep_context), а без него 240: все старые вызовы работают как раньше.
    Если задан и НЕ совпадает с ctx — это ошибка вызова (контекст считан
    на другом ТФ), падаем громко, а не считаем тихо мусор.

    signal_range=(a, b): сигналы берутся только из баров ТФ [a, b);
    сделки при этом могут закрываться позже b (walk-forward).
    Одна позиция на сетап; занятость и кулдаун считаются отдельно.
    near_misses считаются гипотетически той же simulate_trade и НИКАК не
    влияют на balance/trades/monthly.

    v2.2, ПРЕДИКТИВНЫЙ СКОРИНГ (score_fn/score_min):
    score_fn(features) -> оценка 0..1 вызывается ПОСЛЕ всех ворот и после
    проверок занятости/кулдауна (бар, который и так не торгуется, модель не
    беспокоим). features — bar_features(...). Если оценка ниже порога,
    сделка НЕ открывается: отказ идёт в reject_counts["скоринг"] (REJ_SCORE)
    и в blocked_score, а бар попадает в near_misses с gate="скоринг" вместе
    с ЧЕСТНОЙ гипотетикой — видно, что именно модель отфильтровала (для
    near-miss скоринга гипотетика считается ОБЫЧНЫМ путём сделки, hypo=False:
    все ворота пройдены, значит это ровно та сделка, от которой отказались).
    Порог: аргумент score_min, если задан, иначе ген score_min при
    score_gate=1. Без score_fn гены скоринга не действуют вообще.
    Внимание: отказ модели меняет занятость/кулдаун последующих баров,
    поэтому сумма «сделки + гипотетика near-miss» не обязана совпадать с
    прогоном без модели — это не ошибка, а тот же эффект, что у ворот."""
    if setup not in ALL_SETUPS:
        raise ValueError(f"неизвестный сетап: {setup}")
    ctx_iv = ctx.get("interval_min") if isinstance(ctx, dict) else None
    if interval_min is None:
        interval_min = int(ctx_iv or 240)
    else:
        interval_min = int(interval_min)
        if ctx_iv is not None and int(ctx_iv) != interval_min:
            raise ValueError(
                f"interval_min={interval_min} не совпадает с контекстом "
                f"(ctx['interval_min']={ctx_iv}): prep_context и run_setup "
                f"должны звать один и тот же ТФ")
    bar_ms = bar_ms_of(interval_min)
    ext = build_ext(setup, g, c4, interval_min=interval_min)
    n = len(c4)
    a, b = signal_range or (0, n)
    b = min(b, n)
    a = max(int(a), int(g["window"]) + int(ext["lag"]) + 40,
            RSI_SET[int(g["rsi_idx"])] + 2)
    # прогрев доп. индикаторов v2.1: пока SMA/Aroon не насчитаны, их ворота
    # валились бы как отказ — честнее просто начать позже
    if setup == "rally_short" and int(_gg(g, "ma_gate")):
        a = max(a, int(_gg(g, "ma_len")) + 2)
    if int(_gg(g, "aroon_gate")):
        a = max(a, int(_gg(g, "aroon_n")) + 2)

    # порог скоринга: явный аргумент важнее гена; ген действует только при
    # score_gate=1 и только если модель вообще передана
    score_thr = None
    if score_fn is not None:
        if score_min is not None:
            score_thr = float(score_min)
        elif int(_gg(g, "score_gate")):
            score_thr = float(_gg(g, "score_min"))

    balance, peak, max_dd = START, START, 0.0
    trades, near_misses = [], []
    reject_counts, gate_solo = {}, {}
    monthly = {}
    blocked_busy = blocked_cooldown = blocked_ruined = blocked_score = 0
    signals = 0
    pass_by_regime = {0: 0, 1: 0, 2: 0}
    bars_eval = 0
    busy_until_ts, last_exit_ts = 0, 0
    last_sig_i = None
    ruined = False
    t0 = c4[a][0] if a < n else c4[-1][0]

    for i in range(a, b):
        ts = c4[i][0]
        sig_ts = signal_ts(ts, interval_min)   # сигнал есть с ЗАКРЫТИЯ бара
        cool_ok = cooldown_ok(ts, last_exit_ts, g, interval_min)
        ev_ = gate_eval(setup, i, c4, ctx, g, ext)
        bars_eval += 1
        if collect_diag:
            for q in ev_["gates"]:
                if not q["ok"]:
                    gate_solo[q["name"]] = gate_solo.get(q["name"], 0) + 1
        free = sig_ts >= busy_until_ts and cool_ok

        if not ev_["ok"]:
            key = ev_["first_fail"]
            reject_counts[key] = reject_counts.get(key, 0) + 1
            if collect_diag and ev_["near_miss"] and free:
                q = next(x for x in ev_["gates"] if not x["ok"])
                hypo, why = _make_trade(setup, ev_, i, c4, ctx, g, c15, ts15,
                                        lev, hypo=True, bar_ms=bar_ms)
                d = ev_["diag"]
                near_misses.append(dict(
                    ts=ts, side=ev_["side"], gate=q["name"],
                    got=round(q["got"], 4), need=round(q["need"], 4),
                    margin=round(q["margin"], 4),
                    entry=round(ev_["entry_ref"], 2),
                    stop=round(ev_["stop"], 2) if ev_["stop"] else None,
                    tp=round(ev_["tp"], 2) if ev_["tp"] else None,
                    dist=round(ev_["dist"], 5) if ev_["dist"] else None,
                    hypo_r=hypo["r"] if hypo else 0.0,
                    hypo_reason=hypo["reason"] if hypo else why,
                    hypo_mfe_r=hypo["mfe_r"] if hypo else 0.0,
                    hypo_mae_r=hypo["mae_r"] if hypo else 0.0,
                    hypo_pnl=hypo["pnl"] if hypo else 0.0,
                    regime=d["regime"],
                    rsi=round(d["rsi"], 1) if d["rsi"] is not None else None,
                    zone_pos=round(d["zone_pos"], 3)
                    if d["zone_pos"] is not None else None,
                    range_lo=round(d["range_lo"], 2) if d["range_lo"] else None,
                    range_hi=round(d["range_hi"], 2) if d["range_hi"] else None,
                    atr_pct=round(d["atr"] * 100, 3) if d["atr"] else None,
                    stop_pct=round(ev_["dist"] * 100, 3)
                    if ev_["dist"] else None,
                    rsi_thr=round(d["rsi_thr"], 1)
                    if d["rsi_thr"] is not None else None,
                    zone_thr=round(d["zone_thr"], 3)
                    if d["zone_thr"] is not None else None,
                ))
            continue

        signals += 1
        pass_by_regime[ev_["diag"]["regime"]] += 1
        bars_since = (i - last_sig_i) if last_sig_i is not None else None
        last_sig_i = i
        if ruined:
            # после слива сделок больше нет, но диагностику баров продолжаем
            # собирать: владельцу нужна полная статистика, а не обрезанная
            blocked_ruined += 1
            continue
        if sig_ts < busy_until_ts:
            blocked_busy += 1
            continue
        if not cool_ok:
            blocked_cooldown += 1
            continue

        # --- v2.2: слово предиктивной модели (если она передана) ---
        score = None
        if score_thr is not None:
            feats = bar_features(setup, i, c4, ctx, g, ext,
                                 bars_since_signal=bars_since)
            score = float(score_fn(feats))
            if score < score_thr:
                blocked_score += 1
                reject_counts[REJ_SCORE] = reject_counts.get(REJ_SCORE, 0) + 1
                if collect_diag:
                    # гипотетика ОБЫЧНЫМ путём (hypo=False): ворота пройдены,
                    # значит это ровно та сделка, от которой отказались
                    hypo, why = _make_trade(setup, ev_, i, c4, ctx, g, c15,
                                            ts15, lev, bar_ms=bar_ms)
                    d = ev_["diag"]
                    near_misses.append(dict(
                        ts=ts, side=ev_["side"], gate=REJ_SCORE,
                        got=round(score, 4), need=round(score_thr, 4),
                        margin=round(score - score_thr, 4),
                        entry=round(ev_["entry_ref"], 2),
                        stop=round(ev_["stop"], 2) if ev_["stop"] else None,
                        tp=round(ev_["tp"], 2) if ev_["tp"] else None,
                        dist=round(ev_["dist"], 5) if ev_["dist"] else None,
                        hypo_r=hypo["r"] if hypo else 0.0,
                        hypo_reason=hypo["reason"] if hypo else why,
                        hypo_mfe_r=hypo["mfe_r"] if hypo else 0.0,
                        hypo_mae_r=hypo["mae_r"] if hypo else 0.0,
                        hypo_pnl=hypo["pnl"] if hypo else 0.0,
                        regime=d["regime"],
                        rsi=round(d["rsi"], 1)
                        if d["rsi"] is not None else None,
                        zone_pos=round(d["zone_pos"], 3)
                        if d["zone_pos"] is not None else None,
                        range_lo=round(d["range_lo"], 2)
                        if d["range_lo"] else None,
                        range_hi=round(d["range_hi"], 2)
                        if d["range_hi"] else None,
                        atr_pct=round(d["atr"] * 100, 3) if d["atr"] else None,
                        stop_pct=round(ev_["dist"] * 100, 3)
                        if ev_["dist"] else None,
                        rsi_thr=round(d["rsi_thr"], 1)
                        if d["rsi_thr"] is not None else None,
                        zone_thr=round(d["zone_thr"], 3)
                        if d["zone_thr"] is not None else None,
                        score=round(score, 4),
                    ))
                continue

        tr, why = _make_trade(setup, ev_, i, c4, ctx, g, c15, ts15, lev,
                              bar_ms=bar_ms)
        if tr is None:
            reject_counts[why] = reject_counts.get(why, 0) + 1
            if why == REJ_NO15M:
                break
            continue
        if score is not None:
            tr["score"] = round(score, 4)   # необязательное поле сделки

        balance += tr["pnl"]
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak)
        m_idx = int((tr["exit_ts"] - t0) // MONTH_MS)
        monthly[m_idx] = monthly.get(m_idx, 0.0) + tr["pnl"]
        trades.append(tr)
        busy_until_ts = tr["exit_ts"]
        last_exit_ts = tr["exit_ts"]   # кулдаун считает cooldown_ok
        if balance < MARGIN:
            ruined = True          # маржи на следующую сделку уже не хватает

    last_ts = c4[min(max(a, b - 1), n - 1)][0] if n else t0
    return dict(balance=balance, trades=trades, near_misses=near_misses,
                reject_counts=reject_counts, blocked_busy=blocked_busy,
                blocked_cooldown=blocked_cooldown,
                blocked_ruined=blocked_ruined, monthly=monthly,
                max_dd=max_dd, ruined=ruined,
                months=max(1e-9, (last_ts - t0) / MONTH_MS),
                setup=setup, lev=lev, signals=signals,
                pass_by_regime=pass_by_regime, gate_solo=gate_solo,
                bars_eval=bars_eval, signal_range=(a, b),
                interval_min=interval_min, blocked_score=blocked_score,
                score_min=score_thr)


# ------------------------------------------------------------------ метрики
def stats(r):
    """Как в signal_engine.py плюс метрики "что пошло не так"."""
    # последний месяц НЕ терять: сделки закрываются после последнего
    # сигнального бара, поэтому в monthly бывает индекс == int(months)
    n_months = max(1, int(r["months"]),
                   (max(r["monthly"]) + 1) if r["monthly"] else 0)
    rets = [(r["monthly"].get(m, 0.0) / START) * 100 for m in range(n_months)]
    med = sorted(rets)[len(rets) // 2] if rets else 0.0
    p25 = ev.percentile(rets, 0.25)
    tr = r["trades"]
    n = len(tr)
    wins = sum(1 for t in tr if t["pnl"] > 0)
    holds = [t["hold_h"] for t in tr]
    reasons = {}
    for t in tr:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    gross_p = sum(t["pnl"] for t in tr if t["pnl"] > 0)
    gross_l = -sum(t["pnl"] for t in tr if t["pnl"] < 0)
    stopped = [t for t in tr if t["reason"] == "stop"]
    took = [t for t in tr if t["reason"] == "tp"]
    return dict(
        med=med, p25=p25, n=n, wins=wins,
        wr=round(wins / n * 100, 1) if n else 0.0,
        tpm=n / n_months,
        ret=round((r["balance"] / START - 1) * 100, 1),
        dd=round(r["max_dd"] * 100, 1),
        avg_hold_h=round(sum(holds) / n, 1) if n else 0.0,
        pos_share=round(sum(1 for x in rets if x > 0) / len(rets) * 100)
        if rets else 0,
        exp_r=round(sum(t["r"] for t in tr) / n, 3) if n else 0.0,
        pf=round(gross_p / gross_l, 2) if gross_l > 0 else None,
        avg_mae_r=round(sum(t["mae_r"] for t in tr) / n, 3) if n else 0.0,
        avg_mfe_r=round(sum(t["mfe_r"] for t in tr) / n, 3) if n else 0.0,
        reasons=reasons,
        stop_then_tp=sum(1 for t in stopped if t["would_hit_tp_later"]),
        n_stop=len(stopped), n_tp=len(took),
        avg_mfe_after_tp_r=round(sum(t["mfe_after_tp_r"] for t in took)
                                 / len(took), 3) if took else 0.0,
        n_near=len(r.get("near_misses", [])),
        near_avg_r=round(sum(x["hypo_r"] for x in r["near_misses"])
                         / len(r["near_misses"]), 3)
        if r.get("near_misses") else 0.0,
        # частота по ФАКТИЧЕСКОМУ периоду торговли (после слива фикс-базы
        # сделки прекращаются, а календарь идёт — tpm по календарю занижает
        # частоту в разы и даёт ложное "редкие сделки")
        tpm_active=round(n / max(0.5, (tr[-1]["exit_ts"] - tr[0]["entry_ts"])
                                 / MONTH_MS), 2) if n >= 2 else 0.0,
    )


# Пол отбора: должен быть НИЖЕ любой достижимой реальной оценки, иначе
# "не торговать вообще" выигрывает у торгующего генома с отрицательным
# счётом (реальные значения доходили до -10).
FIT_FLOOR = -1000.0
# v2.1: владелец явно попросил КАЧЕСТВО вместо частоты («для улучшения
# винрейта можешь снизить с 90 сделок до 60»), поэтому:
MIN_TRADES_FIT = 20       # статистический пол выборки на трейне (было 25)
TPM_NORM = 0.7            # желаемая частота, сделок в месяц (было 1.2)
# Фактический безубыточный винрейт при 1:3 с издержками (комиссии, слип,
# фандинг): теоретический 25%, реальный ~32% — выше него платим бонус.
WR_BREAKEVEN = 32.0
DD_SOFT_CAP = 25.0        # просадка глубже 25% — штраф (капитал фикс-базы)


def fitness(r):
    """Фитнес для GA — с v2.1 смещён на КАЧЕСТВО сделки, не частоту.

    Ранжирует по expectancy в R (корреляция с фактическим итогом 0.93
    против 0.47 у месячных перцентилей: при 1-2 сделках в месяц больше
    трети календарных месяцев пусты, поэтому медиана/перцентиль месяца
    у большинства геномов ровно 0 и не несут информации).
    Стабильность добавляется слагаемым, редкость — ВЫЧИТАЕМЫМ штрафом
    (множитель на отрицательную базу награждал редкость — нельзя), но с
    v2.1 норма частоты 0.7/мес: полсотни сделок в год достаточно, ниже
    штраф растёт мягко. Новое: бонус за винрейт НАД реальным безубытком
    1:3 (~32% с издержками) и штраф за просадку глубже 25%."""
    st = stats(r)
    if st["n"] < MIN_TRADES_FIT:
        return FIT_FLOOR + st["n"]      # среди безнадёжных лучше тот, кто торговал
    f = st["exp_r"] * 10.0              # основа: сколько R приносит сделка
    f += 0.15 * (st["p25"] + 0.5 * st["med"])   # бонус за ровность по месяцам
    f -= 8.0 * max(0.0, 1.0 - st["tpm"] / TPM_NORM)   # штраф за редкость
    f += 0.08 * max(0.0, st["wr"] - WR_BREAKEVEN)     # бонус за винрейт
    f -= 0.1 * max(0.0, st["dd"] - DD_SOFT_CAP)       # штраф за просадку
    if st["pf"] is not None and st["pf"] < 1.0:
        f -= 2.0 * (1.0 - st["pf"])     # убыточным — дополнительный минус
    if r["ruined"]:
        f -= 50.0
    return f


def oos_score(r):
    """Оценка на экзамене (OOS). Тот же принцип: сентинел ниже реальных
    значений, чтобы бездействие не выигрывало у торговли; с v2.1 — те же
    качественные слагаемые (винрейт/просадка), что и в fitness."""
    st = stats(r)
    if st["n"] < 3:
        return FIT_FLOOR + st["n"]
    f = st["exp_r"] * 10.0
    f += 0.15 * (st["p25"] + 0.5 * st["med"])
    f += 0.08 * max(0.0, st["wr"] - WR_BREAKEVEN)
    f -= 0.1 * max(0.0, st["dd"] - DD_SOFT_CAP)
    if r["ruined"]:
        f -= 100.0
    return f
