# -*- coding: utf-8 -*-
"""Движок сигналов v3: свободный RR, сетка усреднения, мультимонетность.

ЗАЧЕМ ЭТОТ ФАЙЛ. signal_engine2 (в проде, НЕ ТРОГАЕМ) держал два жёстких
ограничения, из-за которых проект буксовал:
  1) RR = 1:3 ЖЁСТКО — требовало винрейта выше 33%, а сетапы давали 30-40%,
     то есть работали на самой грани безубытка;
  2) ОДИН ВХОД — усреднение было запрещено, хотя проверенная механика сетки
     уже есть в evolution2.run5 (боты).
Владелец снял оба: «нужно чтобы просто мы находили точки входа по трендам,
пробоям, сливам или сильном росте, можно для лучшего вина набирать сеткой,
но в принципе главное прибыль». Здесь RR — ГЕН (tp_mode/tp_r/tp_atr/трейлинг),
сетка — ГЕН (grid_levels/grid_step_atr/grid_mult), а сетапы работают на любой
монете (все пороги — в долях цены и в ATR, ни одного абсолютного числа).

ЧЕСТНОСТЬ ПРОИСХОЖДЕНИЯ СЕТАПОВ (это важнее кода). Исследование сырого
преимущества (idea_test.py, 5 монет x 3 периода, БЕЗ подбора параметров)
проверило ровно те идеи, которые заказаны здесь, и НИ ОДНА не прошла заранее
объявленный порог (t>=2.0 И >=10/15 прибыльных комбинаций И медианный
exp_r>0):
    A капитуляция-лонг  — преимущества нет, хуже случайного входа (-0.215R);
    B пробой-лонг       — преимущества нет; лучший вариант B3 (+499%) живёт
                          только на растущем рынке: П3 (медведь) -112%, а его
                          зеркало в шорт на П3 +74% — это трендовая бета;
    C тренд-пулбэк-шорт — преимущества нет (ср. R -0.008), НО фильтр
                          направления дал единственный номинально значимый
                          вклад над случайным входом (+0.148R, p=0.030);
    D перегрев-шорт     — преимущество ОТРИЦАТЕЛЬНОЕ и устойчивое (t=-2.53);
    E сетка контр-тренд — преимущества нет: WR 82-88% и 2/15 прибыльных
                          комбинаций, сетка меняет ФОРМУ распределения, а не
                          матожидание.
Поэтому сетапы здесь реализованы как ЗАКАЗАНО (пробой, тренд-пулбэк,
капитуляция, перегрев), но НИ ОДИН из них не объявляется работающим: движок
даёт им честную площадку и обязательные зеркала (breakout_short, pullback_long),
чтобы отличить преимущество от беты рынка. Единственные два блока, за
которыми измерена информация, вынесены в отдельные гены и доступны любому
сетапу: ФИЛЬТР НАПРАВЛЕНИЯ (trend_gate) и ТРЕЙЛИНГ-ВЫХОД (tp_mode=2).
Отрицательный результат — валидный результат: если дефолтный геном на
holdout даёт минус, это надо писать прямо, а не подкручивать.

ЧТО ВНУТРИ
  Сетапы (SETUPS3), у каждого есть зеркало — иначе результат неотличим от
  направленной ставки на рынок:
    breakout_long / breakout_short — пробой уровня Дончиана за break_days
        суток (закрытие выше максимума / ниже минимума окна, окно
        заканчивается на i-1, текущий бар в него не входит);
    pullback_long / pullback_short — тренд-пулбэк: цена по нужную сторону
        SMA(pull_slow), откат к SMA(pull_fast) фитилём и возврат закрытием,
        разворотная свеча;
    dump_long — капитуляция-лонг: падение >= drop_frac за drop_days суток,
        RSI ниже rsi_os, зелёная свеча;
    rally_short — перегрев-шорт: рост >= drop_frac за drop_days суток,
        RSI выше 100-rsi_os, красная свеча.
  Сетка усреднения (grid_levels 1-4): колена на step = grid_step_atr * ATR
    вниз/вверх от входа, объём колена растёт в grid_mult раз. ШАГ В ATR, а
    не в процентах — поэтому один и тот же геном осмыслен и на BTC, и на
    DOGE. Механика исполнения внутри свечи — консервативная, как в
    evolution2.run5: сначала доливки (только те, что ВЫШЕ стопа), потом стоп;
    тейк проверяется по средней ДО доливок этой свечи; ликвидация считается
    по СРЕДНЕЙ цене и СУММАРНОМУ объёму. При grid_levels=1 поведение
    совпадает с одиночным входом signal_engine2 бит в бит (тест это меряет).
  Свободный выход (tp_mode): 0 — тейк = tp_r * риск от средней; 1 — тейк =
    tp_atr * ATR от средней; 2 — трейлинг за экстремумом trail_bars баров
    своего ТФ (подтягивается ТОЛЬКО по закрытым барам ТФ — без заглядывания).
  Безубыток (be_after_r): 0 — выключен. ОСТОРОЖНО: в прошлом безубыток создал
    артефакт «стоп ни разу не тестировался», поэтому движок ОБЯЗАТЕЛЬНО
    считает be_hit (сколько сделок закрыто перенесённым стопом) и be_no_stop
    (из них — сколько НИ РАЗУ не тестировали исходный стоп в окне удержания).
    Большая доля be_no_stop = безубыток рисует винрейт, а не зарабатывает.

ЕДИНИЦЫ И МАСШТАБИРОВАНИЕ (мультимонетность)
  atr_bar  — ATR(atr_len) баров СВОЕГО ТФ в долях цены. В нём меряются
             stop_atr_k, grid_step_atr, tp_atr, break_buf_atr, pull_max_atr.
  atr_d    — «дневной» ATR в долях цены (для штормового ворота и контекста).
  Все окна (window, break_days, pull_*, trail_bars, aroon_n) — в барах или
  сутках своего ТФ, ни одного абсолютного значения цены. Тик/шаг цены для
  бэктеста роли не играют.

ПРАВИЛА ЧЕСТНОСТИ (те же, что в v2 — нарушение = брак)
  - сигнал ищется на ЗАКРЫТОМ баре своего ТФ, на баре i доступны только
    данные <= i;
  - вход не раньше open первой 15м-свечи ПОСЛЕ закрытия сигнального бара;
  - стоп и тейк в одной 15м-свече -> засчитывается СТОП;
  - ширина стопа от средней <= min(stop_cap, 0.8 * liq_frac(lev)); доливки
    идут только между входом и стопом, поэтому по мере налива сетки
    расстояние «средняя -> стоп» только СОКРАЩАЕТСЯ, и стоп остаётся внутри
    ликвидации (тест это проверяет явно);
  - издержки проектные: TAKER 0.055%, MAKER 0.02%, SLIP 0.03%, FUND 0.01%/8ч.

ЕДИНИЦА РИСКА R. R = MARGIN * lev * dist0, где dist0 — ширина НАЧАЛЬНОГО
стопа от первого фила. Это фиксированная ex-ante величина, не зависящая от
того, сколько колен налилось: сделка одним коленом при стопе даёт около -1R
только при grid_levels=1, а полная сетка при стопе — заметно больше по модулю.
Так и должно быть: сетка увеличивает риск, и метрика обязана это показывать,
а не прятать (если бы R считался по фактической средней, любой стоп по
определению давал бы -1R и разница между сеткой и одиночным входом исчезла).

ПУБЛИЧНЫЙ API
  SETUPS3, SETUP_TITLES3, GENES3, DEFAULTS3, GATE_NAMES3, REJ_NAMES3,
  TRADE_FIELDS3, NM_FIELDS3, RESULT_FIELDS3,
  liq_frac, setup_side, default_genome, gene_bounds,
  prep_context, build_ext, gate_eval, plan_grid, simulate_grid_trade,
  trail_series_15, run_setup, stats, fitness, oos_score,
  START, MARGIN, MIN_STOP, TAKER, MAKER, SLIP, FUND_8H, MMR, BE_PAD,
  FIT_FLOOR, MIN_TRADES_FIT, MONTH_MS.
Результат run_setup совместим с signal_stats.full_stats (те же ключи, что у
signal_engine2.run_setup) и дополнен полями grid_fills / avg_entry / be_hit.

Запуск проверок: python test_engine3_v3.py
"""

import bisect

import evolution as ev
import evolution2 as e2
import signal_engine2 as se2

# ------------------------------------------------------------- константы
# Издержки и базовые величины берутся из проекта (не переопределяются здесь,
# чтобы v3 нельзя было случайно сделать «дешевле» реальности).
TAKER, MAKER = e2.TAKER, e2.MAKER      # 0.055% / 0.02%
SLIP = e2.SLIP                         # 0.03% проскальзывание маркет-ордера
FUND_8H = e2.FUND_8H                   # 0.01% за 8 часов удержания
MONTH_MS = e2.MONTH_MS
RSI_SET = e2.RSI_SET                   # [7, 10, 14, 21]
MMR = se2.MMR                          # поддерживающая маржа Bybit (~0.5%)
liq_frac = se2.liq_frac                # 1/плечо - MMR (доля хода до ликвидации)

START = 20.0                           # стартовая база бэктеста, USDT
MARGIN = 5.0                           # маржа на ЦИКЛ (вся сетка), USDT
MIN_STOP = 0.005                       # стоп уже 0.5% — шум, не берём
BE_PAD = 0.0015                        # надбавка безубытка (комиссии+слип)
ATR_LEN = 14                           # период ATR баров своего ТФ

BARS_DAY_15 = 96                       # 15м-баров в сутках
MS_15M = 15 * 60 * 1000
MS_4H = 4 * 3600 * 1000

bar_ms_of = se2.bar_ms_of              # длительность бара ТФ в мс
bars_per_day = se2.bars_per_day        # баров ТФ в сутках

# ---------------------------------------------------------------- сетапы
SETUPS3 = [
    "breakout_long", "breakout_short",
    "pullback_long", "pullback_short",
    "dump_long", "rally_short",
]

SETUP_TITLES3 = {
    "breakout_long": "пробой максимума Дончиана вверх",
    "breakout_short": "пробой минимума Дончиана вниз (зеркало пробоя)",
    "pullback_long": "тренд-пулбэк вверх: откат к средней в росте",
    "pullback_short": "тренд-пулбэк вниз: откат к средней в падении",
    "dump_long": "капитуляция-лонг: слив + разворотная свеча",
    "rally_short": "перегрев-шорт: сильный рост + разворотная свеча",
}

# Пары «сетап -> его зеркало». Любой результат обязан проверяться зеркалом:
# если работает только одна сторона, померена бета рынка, а не преимущество.
MIRROR = {
    "breakout_long": "breakout_short", "breakout_short": "breakout_long",
    "pullback_long": "pullback_short", "pullback_short": "pullback_long",
    "dump_long": "rally_short", "rally_short": "dump_long",
}

REGIME_NAMES = se2.REGIME_NAMES

# ------------------------------------------------------------------ ворота
# Имена ворот — стабильные ключи для reject_counts, gate_solo и графиков.
# Часть переиспользована из v2 (те же строки), чтобы сайт и signal_stats
# узнавали их без правок.
GATE_DATA = se2.GATE_DATA              # "данные"
GATE_REGIME = se2.GATE_REGIME          # "режим"
GATE_RSI = se2.GATE_RSI                # "RSI"
GATE_MOVE = se2.GATE_MOVE              # "размер движения"
GATE_CANDLE = se2.GATE_CANDLE          # "разворотная свеча"
GATE_STOP = se2.GATE_STOP              # "ширина стопа"
GATE_FUND = se2.GATE_FUND              # "funding"
GATE_AROON = se2.GATE_AROON            # "aroon"
GATE_STORM = se2.GATE_STORM            # "шторм"
GATE_TREND = "направление тренда"      # v3: фильтр направления (SMA trend_len)
GATE_BREAK = "пробой уровня"           # v3: Дончиан
GATE_PULLBACK = "откат к средней"      # v3: тренд-пулбэк

GATE_NAMES3 = tuple(se2.GATE_NAMES) + (GATE_TREND, GATE_BREAK, GATE_PULLBACK)

# причины отказа на стадии исполнения (не ворота бара)
REJ_NO15M = se2.REJ_NO15M              # "нет 15м-данных"
REJ_ENTRY_STOP = se2.REJ_ENTRY_STOP    # "стоп вне лимита на фактическом входе"
REJ_NAMES3 = (REJ_NO15M, REJ_ENTRY_STOP)

# допуски «почти прошло» для near-miss (в единицах своего порога)
TOL_RSI = 5.0          # RSI: 5 пунктов
TOL_MOVE = 0.30        # размер движения: недобор до 30% порога
TOL_STOP = 0.35        # ширина стопа: перебор до 35% cap
TOL_BREAK = 0.50       # пробой: недобор до 0.5 ATR
TOL_PULL = 0.50        # откат: недобор до 0.5 ATR
TOL_TREND = 0.010      # тренд: цена в пределах 1% от SMA
TOL_FUND = 0.010       # funding: не хватило до 0.010%/8ч
TOL_AROON = 10.0       # aroon: не хватило до 10 пунктов
TOL_STORM = se2.TOL_STORM

# -------------------------------------------------------------------- гены
# имя -> (min, max, is_int). Формат совместим с evolution4.ga_tools(genes).
GENES3 = {
    # --- общее ---
    "rsi_idx":        (0, 3, True),        # индекс периода в RSI_SET
    "window":         (20, 200, True),     # окно диапазона (бары ТФ): зона+стоп
    "cooldown":       (0, 24, True),       # пауза после выхода, бары ТФ
    "hold_days":      (1, 60, True),       # предельное удержание, сутки
    # --- фильтр направления (единственный измеренно информативный блок) ---
    "trend_gate":     (0, 1, True),        # 1 -> лонг только над SMA, шорт под
    "trend_len":      (20, 400, True),     # период SMA фильтра, бары ТФ
    # --- пробой (Дончиан) ---
    "break_days":     (3, 60, True),       # окно уровня, сутки
    "break_buf_atr":  (0.0, 1.0, False),   # насколько закрытие ЗА уровнем, ATR
    # --- тренд-пулбэк ---
    "pull_slow":      (50, 400, True),     # медленная SMA (тренд), бары ТФ
    "pull_fast":      (5, 60, True),       # быстрая SMA (уровень отката)
    "pull_max_atr":   (0.2, 4.0, False),   # максимум удаления закрытия от
                                           # быстрой SMA после отбоя, ATR
    # --- капитуляция / перегрев ---
    "drop_frac":      (0.02, 0.25, False), # размер движения за drop_days
    "drop_days":      (1, 5, True),        # окно движения, сутки
    "rsi_os":         (15, 45, True),      # лонг: RSI<rsi_os; шорт: >100-rsi_os
    "need_candle":    (0, 1, True),        # требовать разворотную свечу
    # --- режимы рынка ---
    "reg_bull":       (0, 1, True),
    "reg_range":      (0, 1, True),
    "reg_bear":       (0, 1, True),
    # --- стоп ---
    "stop_mode":      (0, 3, True),        # 0 окно, 1 свинг, 2 k*ATR, 3 фитиль
    "swing_bars":     (3, 30, True),       # свинг для stop_mode=1, бары ТФ
    "stop_atr_k":     (0.5, 4.0, False),   # k для stop_mode=2, ATR бара
    "buf_atr":        (0.0, 1.5, False),   # буфер за уровнем/свингом, ATR бара
    "stop_cap":       (0.008, 0.150, False),  # потолок ширины стопа, доля цены
    # --- сетка усреднения ---
    "grid_levels":    (1, 4, True),        # 1 = обычный одиночный вход
    "grid_step_atr":  (0.3, 2.0, False),   # шаг колена в ATR бара
    "grid_mult":      (1.0, 2.5, False),   # рост объёма колена
    # --- выход ---
    "tp_mode":        (0, 2, True),        # 0 tp_r*риск, 1 tp_atr*ATR, 2 трейл
    "tp_r":           (0.5, 4.0, False),   # RR: теперь ГЕН, а не константа 3.0
    "tp_atr":         (0.5, 8.0, False),   # тейк в ATR бара (tp_mode=1)
    "trail_bars":     (3, 120, True),      # окно трейлинга, бары ТФ (tp_mode=2)
    "be_after_r":     (0.0, 2.0, False),   # 0 = выкл; иначе безубыток после X R
    # --- ворота-фильтры проекта (в семени выключены) ---
    "storm_gate":     (0, 1, True),
    "storm_day":      (0.03, 0.20, False),
    "storm_week":     (0.06, 0.40, False),
    "storm_atr_rank": (0.5, 1.0, False),
    "storm_mode":     (0, 2, True),
    "fund_gate":      (0, 1, True),
    "fund_thr":       (0.0, 0.06, False),
    "aroon_gate":     (0, 1, True),
    "aroon_n":        (10, 50, True),
    "aroon_thr":      (0, 100, True),
}

# НЕЙТРАЛЬНОЕ СЕМЯ. Значения взяты «из учебника» и совпадают с фиксированными
# параметрами исследования идей (idea_test.py), чтобы дефолтный прогон был
# честной ОТПРАВНОЙ ТОЧКОЙ, а не результатом подбора:
#   Дончиан 20 суток, SMA200/SMA20 для тренд-пулбэка, слив/рост 6% за 2 суток,
#   RSI14 30/70, стоп 2*ATR, тейк 2R, трейлинг 10 суток (60 баров на 4ч).
# Сетка в семени ВЫКЛЮЧЕНА (grid_levels=1) и безубыток тоже (be_after_r=0):
# семя обязано вести себя как одиночный вход, а включение — работа GA.
DEFAULTS3 = dict(
    rsi_idx=2, window=60, cooldown=0, hold_days=30,
    trend_gate=0, trend_len=200,
    break_days=20, break_buf_atr=0.0,
    pull_slow=200, pull_fast=20, pull_max_atr=1.5,
    drop_frac=0.06, drop_days=2, rsi_os=30, need_candle=1,
    reg_bull=1, reg_range=1, reg_bear=1,
    stop_mode=2, swing_bars=8, stop_atr_k=2.0, buf_atr=0.3, stop_cap=0.06,
    grid_levels=1, grid_step_atr=1.0, grid_mult=1.5,
    tp_mode=0, tp_r=2.0, tp_atr=3.0, trail_bars=60, be_after_r=0.0,
    storm_gate=0, storm_day=0.07, storm_week=0.15, storm_atr_rank=0.85,
    storm_mode=0,
    fund_gate=0, fund_thr=0.005,
    aroon_gate=0, aroon_n=25, aroon_thr=30,
)


def _gg(g, key):
    """Ген со значением по умолчанию: неполный геном (например, сохранённый
    старой версией) обязан работать, а не падать."""
    v = g.get(key)
    return DEFAULTS3[key] if v is None else v


def default_genome(**over):
    """Копия нейтрального семени с точечными правками."""
    g = dict(DEFAULTS3)
    g.update(over)
    return g


def gene_bounds():
    """Границы генов (для GA и для проверки «упёрся в границу»)."""
    return dict(GENES3)


def setup_side(setup):
    """'L' для лонговых сетапов, 'S' для шортовых."""
    return "L" if setup.endswith("long") else "S"


# Поля сделки. Первый блок — как в signal_engine2 (совместимость с
# signal_stats.full_stats и страницами сайта), второй — новое в v3.
TRADE_FIELDS3 = (
    "entry_ts", "exit_ts", "side", "entry", "stop", "tp", "pnl", "r", "reason",
    "hold_h", "mae_r", "mfe_r", "time_to_mae_h", "time_to_mfe_h",
    "would_hit_tp_later", "mfe_after_tp_r", "regime", "rsi", "zone_pos",
    "atr_pct", "stop_pct", "range_lo", "range_hi", "entry_mode",
    "signal_ts", "entry_ref", "swing_lo", "swing_hi", "stop_mode",
    "rsi_thr", "zone_thr", "bars_waited", "setup",
    # --- v3 ---
    "grid_fills",        # сколько колен налилось (1 = только первый вход)
    "avg_entry",         # средняя цена позиции на выходе
    "margin_used",       # сколько маржи цикла реально задействовано, USDT
    "exit_kind",         # tp|stop|be|trail|liq|timeout (детальнее reason)
    "be_moved",          # стоп переносился в безубыток
    "be_exit",           # выход произошёл ИМЕННО по перенесённому стопу
    "orig_stop_touched",  # исходный стоп тестировался в окне удержания
    "tp_mode", "tp_target",
)

NM_FIELDS3 = (
    "ts", "side", "gate", "got", "need", "margin", "entry", "stop", "tp",
    "dist", "hypo_r", "hypo_reason", "hypo_mfe_r", "regime", "rsi",
    "zone_pos", "range_lo", "range_hi",
    "hypo_pnl", "hypo_mae_r", "atr_pct", "stop_pct", "rsi_thr", "zone_thr",
    "hypo_grid_fills",
)

RESULT_FIELDS3 = (
    "balance", "trades", "near_misses", "reject_counts", "blocked_busy",
    "blocked_cooldown", "blocked_ruined", "monthly", "max_dd", "ruined",
    "months", "setup", "lev", "signals", "pass_by_regime", "gate_solo",
    "bars_eval", "signal_range", "interval_min", "symbol",
    # --- v3 ---
    "grid_fills", "grid_fill_hist", "avg_grid_fills", "avg_entry",
    "be_hit", "be_hit_share", "be_moved", "be_no_stop", "trail_exits",
)


# --------------------------------------------------------------- контекст
def prep_context(c4, interval_min=240, symbol="BTCUSDT", atr_len=ATR_LEN):
    """Контекст серии сигнального ТФ (не зависит от генов).

    Базу берём у se2.prep_context (тот же RSI, дневной ATR, режим, EMA,
    funding, ряды шторма — переиспользуем проверенный код, а не копируем) и
    добавляем то, что нужно v3:
      opens    — открытия баров (разворотная свеча);
      atr_bar  — ATR(atr_len) баров СВОЕГО ТФ в долях цены. Именно он —
                 единица измерения стопа, шага сетки и тейка: дневной ATR на
                 4ч-баре в 2-3 раза крупнее и делает шаг сетки нерабочим.
      symbol   — монета (для отчётов и проверки, что контекст «тот самый»).
    Все ряды причинные: значение на баре i считается по данным <= i.
    """
    ctx = se2.prep_context(c4, interval_min=interval_min, symbol=symbol)
    ctx["opens"] = [c[1] for c in c4]
    ctx["highs"] = [c[2] for c in c4]
    ctx["lows"] = [c[3] for c in c4]
    ctx["atr_bar"] = ev.calc_atr_pct(c4, n=int(atr_len))
    ctx["atr_len"] = int(atr_len)
    ctx["symbol"] = symbol
    return ctx


def build_ext(setup, g, c4, interval_min=240):
    """Предрасчёт рядов под конкретный геном и сетап (только данные <= i).

    don_lo/don_hi — экстремумы окна break_days суток, ЗАКАНЧИВАЮЩЕГОСЯ на
        i-1 (текущий бар в уровень не входит — иначе «пробой» тавтологичен);
    win_lo/win_hi — экстремумы последних window баров ВКЛЮЧАЯ i (зона, стоп
        режима 0);
    swing_lo/swing_hi — экстремумы последних swing_bars баров (стоп режима 1);
    sma_slow/sma_fast — SMA(pull_slow)/SMA(pull_fast) для тренд-пулбэка;
    sma_trend — SMA(trend_len) для ворота направления (trend_gate=1);
    trail_lo/trail_hi — экстремумы последних trail_bars баров (трейлинг);
    aroon_up/aroon_dn — Aroon(aroon_n) при aroon_gate=1.
    Ключ warm — с какого бара геном вообще имеет право торговать (прогрев
    самого длинного ряда): раньше него ворота валились бы «нет данных».
    """
    n_day = bars_per_day(interval_min)
    win = max(2, int(_gg(g, "window")))
    swing = max(1, int(_gg(g, "swing_bars")))
    don_bars = max(2, int(_gg(g, "break_days")) * n_day)
    out = dict(window=win, swing_bars=swing, don_bars=don_bars,
               d_bars=max(1, int(_gg(g, "drop_days")) * n_day),
               n_day=n_day, don_lo=None, don_hi=None, sma_slow=None,
               sma_fast=None, sma_trend=None, trail_lo=None, trail_hi=None,
               aroon_up=None, aroon_dn=None)
    win_lo, win_hi = se2.trailing_extremes(c4, win)
    sw_lo, sw_hi = se2.trailing_extremes(c4, swing)
    out["win_lo"], out["win_hi"] = win_lo, win_hi
    out["swing_lo"], out["swing_hi"] = sw_lo, sw_hi
    warm = max(win, swing, int(RSI_SET[int(_gg(g, "rsi_idx"))]) + 2,
               int(_gg(g, "drop_days")) * n_day + 1, ATR_LEN + 2)

    closes = [c[4] for c in c4]
    if setup.startswith("breakout"):
        lo, hi = se2.rolling_extremes_lagged(c4, don_bars, 1)
        out["don_lo"], out["don_hi"] = lo, hi
        warm = max(warm, don_bars + 2)
    if setup.startswith("pullback"):
        slow = max(2, int(_gg(g, "pull_slow")))
        fast = max(2, int(_gg(g, "pull_fast")))
        out["sma_slow"] = se2.calc_sma(closes, slow)
        out["sma_fast"] = se2.calc_sma(closes, fast)
        warm = max(warm, slow + 2)
    if int(_gg(g, "trend_gate")):
        t_len = max(2, int(_gg(g, "trend_len")))
        out["sma_trend"] = se2.calc_sma(closes, t_len)
        warm = max(warm, t_len + 2)
    if int(_gg(g, "tp_mode")) == 2:
        t_lo, t_hi = se2.trailing_extremes(c4, max(2, int(_gg(g, "trail_bars"))))
        out["trail_lo"], out["trail_hi"] = t_lo, t_hi
    if int(_gg(g, "aroon_gate")):
        a_n = max(2, int(_gg(g, "aroon_n")))
        up, dn = se2.calc_aroon(c4, a_n)
        out["aroon_up"], out["aroon_dn"] = up, dn
        warm = max(warm, a_n + 2)
    out["warm"] = int(warm)
    return out


# ------------------------------------------------------------------ ворота
def _gate(name, got, need, margin, tol):
    """Ворото с числовым запасом: margin>=0 — прошло, margin<0 — недобор.
    near=True, если провалено, но не хватило меньше tol (для near-miss)."""
    ok = margin >= 0
    return dict(name=name, ok=bool(ok), got=float(got), need=float(need),
                margin=float(margin),
                near=bool((not ok) and margin >= -abs(tol)))


def _gate_bool(name, ok, got=0.0, need=1.0):
    """Бинарное ворото (режим, направление свечи) — «почти» не бывает."""
    return dict(name=name, ok=bool(ok), got=float(got), need=float(need),
                margin=0.0 if ok else -1.0, near=False)


def regime_allowed(regime, g):
    """Разрешён ли режим рынка генами reg_bull/reg_range/reg_bear."""
    if regime == 0:
        return bool(_gg(g, "reg_bull"))
    if regime == 1:
        return bool(_gg(g, "reg_range"))
    return bool(_gg(g, "reg_bear"))


def _stop_price(side, i, c4, ctx, g, ext, ref):
    """Цена стопа по stop_mode. Только данные <= i. Буфер и k — в ATR БАРА."""
    sgn = 1 if side == "L" else -1
    atr_abs = ctx["atr_bar"][i] * ref
    buf = float(_gg(g, "buf_atr")) * atr_abs
    mode = int(_gg(g, "stop_mode"))
    _, o, h, l, c = c4[i]
    if mode == 0:                                   # за границей окна window
        lo, hi = ext["win_lo"][i], ext["win_hi"][i]
        return (min(lo, l) - buf) if sgn == 1 else (max(hi, h) + buf)
    if mode == 1:                                   # за свингом swing_bars
        s = ext["swing_lo"][i] if sgn == 1 else ext["swing_hi"][i]
        return (s - buf) if sgn == 1 else (s + buf)
    if mode == 2:                                   # k * ATR бара от входа
        return ref * (1 - sgn * float(_gg(g, "stop_atr_k"))
                      * ctx["atr_bar"][i])
    return (l - buf) if sgn == 1 else (h + buf)     # 3 = за фитилём бара


def gate_eval(setup, i, c4, ctx, g, ext):
    """Оценка всех ворот сетапа на ЗАКРЫТИИ бара i своего ТФ.

    Возвращает разбор: список ворот с числовыми запасами, цены входа/стопа,
    диагностику. ok=True -> сигнал есть; near_miss=True -> провалено ровно
    одно ворото и не хватило чуть-чуть (для честной гипотетики)."""
    if setup not in SETUPS3:
        raise ValueError(f"неизвестный сетап: {setup}")
    side = setup_side(setup)
    sgn = 1 if side == "L" else -1
    period = RSI_SET[int(_gg(g, "rsi_idx"))]
    rsi_arr = ctx["rsi"][period]
    rsi = rsi_arr[i] if i < len(rsi_arr) else None
    atr_b = ctx["atr_bar"][i] if i < len(ctx["atr_bar"]) else None
    atr_d = ctx["atr_d"][i] if i < len(ctx["atr_d"]) else None
    reg = ctx["regime"][i]
    s_on, s_dir, s_str, s_r1, s_r7, s_rank, s_vol = se2._storm_calc(ctx, i, g)
    lo, hi = ext["win_lo"][i], ext["win_hi"][i]
    diag = dict(rsi=rsi, zone_pos=None, atr=atr_d, atr_bar=atr_b, regime=reg,
                range_lo=lo, range_hi=hi, rsi_thr=None, zone_thr=None,
                rsi_period=period, swing_lo=None, swing_hi=None,
                move=None, level=None, break_dist=None, pull_dist=None,
                sma_fast=None, sma_slow=None, sma_trend=None,
                fund=None, aroon=None, stop_mode=int(_gg(g, "stop_mode")),
                ret_1d=s_r1, ret_7d=s_r7, atr_rank=s_rank, storm=s_on,
                storm_dir=s_dir, storm_strength=s_str, vol_spike=s_vol)
    out = dict(side=side, entry_ref=None, stop=None, tp=None, dist=None,
               gates=[], ok=False, n_failed=0, first_fail=None,
               near_miss=False, diag=diag, setup=setup)

    d_bars = ext["d_bars"]
    if (rsi is None or atr_b is None or atr_b <= 0 or atr_d is None
            or lo is None or hi is None or hi <= lo
            or i < max(ext["warm"], d_bars) or i >= len(c4)):
        out["gates"].append(_gate_bool(GATE_DATA, False))
        out["n_failed"] = 1
        out["first_fail"] = GATE_DATA
        return out

    ts, o, h, l, c = c4[i]
    atr_abs = atr_b * c
    diag["zone_pos"] = (c - lo) / (hi - lo)
    gates = [_gate_bool(GATE_REGIME, regime_allowed(reg, g), reg, 1.0)]

    # --- шторм (ворото проекта, ген по умолчанию выключен) ---
    if int(_gg(g, "storm_gate")):
        mode = int(_gg(g, "storm_mode"))
        if mode == 2:                     # торговать ТОЛЬКО в шторм
            gates.append(_gate(GATE_STORM, s_str, 1.0, s_str - 1.0, TOL_STORM))
        else:
            against = s_on and ((s_dir < 0 and sgn == 1)
                                or (s_dir > 0 and sgn == -1))
            if mode == 1 and not against:
                gates.append(_gate_bool(GATE_STORM, True, s_str, 1.0))
            else:
                gates.append(_gate(GATE_STORM, s_str, 1.0, 1.0 - s_str,
                                   TOL_STORM))

    # --- фильтр направления: единственный блок с измеренным вкладом ---
    if int(_gg(g, "trend_gate")):
        arr = ext.get("sma_trend")
        sma_t = arr[i] if arr is not None else None
        diag["sma_trend"] = sma_t
        if not sma_t:
            gates.append(_gate_bool(GATE_TREND, False))
        else:
            rel = sgn * (c - sma_t) / sma_t     # >0 — цена по нужную сторону
            gates.append(_gate(GATE_TREND, rel, 0.0, rel, TOL_TREND))

    # ------------------------------------------------------ профиль сетапа
    if setup.startswith("breakout"):
        arr = ext["don_hi"] if sgn == 1 else ext["don_lo"]
        lvl = arr[i] if arr is not None else None
        diag["level"] = lvl
        if lvl is None:
            gates.append(_gate_bool(GATE_BREAK, False))
        else:
            need_buf = float(_gg(g, "break_buf_atr")) * atr_abs
            over = sgn * (c - lvl) - need_buf          # в цене
            diag["break_dist"] = over / atr_abs
            gates.append(_gate(GATE_BREAK, over / atr_abs, 0.0,
                               over / atr_abs, TOL_BREAK))
    elif setup.startswith("pullback"):
        s_slow = ext["sma_slow"][i] if ext.get("sma_slow") else None
        s_fast = ext["sma_fast"][i] if ext.get("sma_fast") else None
        diag["sma_slow"], diag["sma_fast"] = s_slow, s_fast
        if not s_slow or not s_fast:
            gates.append(_gate_bool(GATE_TREND, False))
            gates.append(_gate_bool(GATE_PULLBACK, False))
        else:
            # 1) тренд: закрытие по нужную сторону медленной SMA
            rel = sgn * (c - s_slow) / s_slow
            gates.append(_gate(GATE_TREND, rel, 0.0, rel, TOL_TREND))
            # 2) откат: фитиль коснулся быстрой SMA, а закрытие вернулось
            #    на сторону тренда и не убежало дальше pull_max_atr * ATR
            touched = (l <= s_fast) if sgn == 1 else (h >= s_fast)
            back = sgn * (c - s_fast)               # >0 — закрылись «за» SMA
            far = back / atr_abs
            diag["pull_dist"] = far
            max_far = float(_gg(g, "pull_max_atr"))
            if not touched:
                miss = (sgn * (s_fast - l) if sgn == 1
                        else sgn * (s_fast - h))    # <0: не дотянулись
                gates.append(_gate(GATE_PULLBACK, miss / atr_abs, 0.0,
                                   miss / atr_abs, TOL_PULL))
            elif back <= 0:
                gates.append(_gate(GATE_PULLBACK, far, 0.0, far, TOL_PULL))
            else:
                gates.append(_gate(GATE_PULLBACK, far, max_far,
                                   max_far - far, TOL_PULL))
    else:                                   # dump_long / rally_short
        base = ctx["closes"][i - d_bars]
        move = (base - c) / base if sgn == 1 else (c - base) / base
        diag["move"] = move
        need = float(_gg(g, "drop_frac"))
        gates.append(_gate(GATE_MOVE, move, need, move - need, TOL_MOVE * need))
        r_thr = float(_gg(g, "rsi_os"))
        diag["rsi_thr"] = r_thr if sgn == 1 else 100.0 - r_thr
        if sgn == 1:
            gates.append(_gate(GATE_RSI, rsi, r_thr, r_thr - rsi, TOL_RSI))
        else:
            need_r = 100.0 - r_thr
            gates.append(_gate(GATE_RSI, rsi, need_r, rsi - need_r, TOL_RSI))

    # разворотная свеча (нужна сливу/перегреву и опционально пулбэку)
    if int(_gg(g, "need_candle")) and not setup.startswith("breakout"):
        okc = (c > o) if sgn == 1 else (c < o)
        gates.append(_gate_bool(GATE_CANDLE, okc, sgn * (c - o) / o, 0.0))

    # --- ворота проекта: funding и Aroon (в семени выключены) ---
    if int(_gg(g, "fund_gate")):
        fund_arr = ctx.get("fund")
        rate = (fund_arr[i] if fund_arr is not None and i < len(fund_arr)
                else None)
        diag["fund"] = rate
        f_thr = float(_gg(g, "fund_thr"))
        if rate is None:
            gates.append(_gate_bool(GATE_FUND, False))
        elif sgn == 1:
            gates.append(_gate(GATE_FUND, rate, -f_thr, -f_thr - rate,
                               TOL_FUND))
        else:
            gates.append(_gate(GATE_FUND, rate, f_thr, rate - f_thr, TOL_FUND))
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

    # --- стоп и жёсткий лимит его ширины ---
    ref = c
    stop_px = _stop_price(side, i, c4, ctx, g, ext, ref)
    dist = sgn * (ref - stop_px) / ref
    cap = float(_gg(g, "stop_cap"))
    if ext.get("swing_lo") is not None:
        diag["swing_lo"] = ext["swing_lo"][i]
        diag["swing_hi"] = ext["swing_hi"][i]
    if dist <= 0 or dist < MIN_STOP:
        gates.append(_gate(GATE_STOP, dist, MIN_STOP, dist - MIN_STOP,
                           0.5 * MIN_STOP))
    elif dist > cap:
        gates.append(_gate(GATE_STOP, dist, cap, cap - dist, TOL_STOP * cap))
    else:
        gates.append(_gate(GATE_STOP, dist, cap,
                           min(cap - dist, dist - MIN_STOP), TOL_STOP * cap))

    out["entry_ref"] = ref
    out["stop"] = stop_px
    out["dist"] = dist if dist > 0 else None
    # ориентировочный тейк для карточки сигнала (реальный считается от средней)
    if dist > 0:
        if int(_gg(g, "tp_mode")) == 0:
            out["tp"] = ref * (1 + sgn * float(_gg(g, "tp_r")) * dist)
        elif int(_gg(g, "tp_mode")) == 1:
            out["tp"] = ref * (1 + sgn * float(_gg(g, "tp_atr")) * atr_b)
        else:
            out["tp"] = None

    failed = [q for q in gates if not q["ok"]]
    out["gates"] = gates
    out["n_failed"] = len(failed)
    out["first_fail"] = failed[0]["name"] if failed else None
    out["ok"] = not failed
    out["near_miss"] = bool(len(failed) == 1 and failed[0]["near"])
    return out


# ------------------------------------------------------------------ сетка
def plan_grid(side, fill0, levels, step_frac, mult, lev, margin=MARGIN):
    """План сетки: (объём первого колена, [(цена, объём) остальных колен],
    [маржа каждого колена]).

    Маржа ЦИКЛА фиксирована (margin): веса mult^k нормируются так, чтобы их
    сумма равнялась margin. Поэтому сетка не увеличивает задействованный
    капитал, а перераспределяет его по цене — ровно как в evolution2.run5.
    Цены колен считаются последовательным умножением (ap *= 1 - sgn*step),
    как в run5, а шаг задан в ATR, поэтому он одинаково осмыслен на BTC и
    на DOGE."""
    sgn = 1 if side == "L" else -1
    levels = max(1, min(4, int(levels)))
    w = [float(mult) ** k for k in range(levels)]
    sw = sum(w)
    m_k = [margin * x / sw for x in w]
    q0 = m_k[0] * lev / fill0
    adds = []
    ap = fill0
    for k in range(1, levels):
        ap = ap * (1 - sgn * float(step_frac))
        adds.append((ap, m_k[k] * lev / ap))
    return q0, adds, m_k


def trail_series_15(c4, bar_ms, ts15, trail_arr):
    """Значение трейлинг-уровня для КАЖДОГО 15м-бара: экстремум последних
    trail_bars баров ТФ, известный на момент НАЧАЛА этого 15м-бара.

    Бар ТФ j считается закрытым, если c4[j][0] + bar_ms <= ts15[k]. Значит
    ни один 15м-бар не видит незакрытый бар своего ТФ — заглядывания нет
    (ровно тот же приём, что в idea_test.py, где трейлинг обновлялся только
    по закрытым 4ч-барам)."""
    out = [None] * len(ts15)
    j = -1
    n4 = len(c4)
    for k, t in enumerate(ts15):
        while j + 1 < n4 and c4[j + 1][0] + bar_ms <= t:
            j += 1
        if j >= 0 and trail_arr is not None and trail_arr[j] is not None:
            out[k] = trail_arr[j]
    return out


def simulate_grid_trade(side, entry_i15, entry_px, stop_px, c15, lev,
                        hold_bars15, levels=1, step_frac=0.0, mult=1.0,
                        tp_mode=0, tp_r=2.0, tp_frac=0.0, trail15=None,
                        be_after_r=0.0, margin=MARGIN, be_pad=BE_PAD):
    """Ведёт ОДИН цикл (вход + сетка + сопровождение) по 15м-свечам.

    Порядок внутри свечи — консервативный, как в evolution2.run5:
      1) фандинг за свечу по объёму ДО доливок;
      2) обновление трейлинг-стопа по ПОСЛЕДНЕМУ ЗАКРЫТОМУ бару ТФ;
      3) если свеча дошла до стопа: сначала исполняются лимитки сетки,
         лежащие ВЫШЕ стопа (по пути цены), и стопится уже УВЕЛИЧЕННАЯ
         позиция; ликвидация проверяется по СРЕДНЕЙ и СУММАРНОМУ объёму и
         только если она ближе стопа;
      4) иначе тейк — по средней ДО доливок этой свечи;
      5) иначе доливки, потом безубыток, потом таймаут.
    Стоп и тейк в одной свече -> СТОП (пункт 3 стоит раньше пункта 4).

    Тейк (tp_mode): 0 — от средней на tp_r * |средняя - ИСХОДНЫЙ стоп|
    (риск меряется до исходного стопа, иначе перенос в безубыток дёргал бы
    цель); 1 — от средней на tp_frac (доля цены, обычно tp_atr * ATR);
    2 — трейлинг: цели нет, стоп подтягивается за экстремумом.

    Возвращает словарь с pnl/r/причиной, MAE/MFE в долях НАЧАЛЬНОГО риска,
    и полями сетки/безубытка: grid_fills, avg_entry, margin_used, be_moved,
    be_exit, orig_stop_touched."""
    sgn = 1 if side == "L" else -1
    fill0 = entry_px * (1 + sgn * SLIP)
    q0, adds, m_k = plan_grid(side, fill0, levels, step_frac, mult, lev,
                              margin=margin)
    fills = [(fill0, q0)]
    fees = q0 * fill0 * TAKER              # вход — тейкер (консервативно)
    stop0 = float(stop_px)
    risk_px = abs(fill0 - stop0)
    wrong_side = (stop0 >= fill0) if sgn == 1 else (stop0 <= fill0)
    if wrong_side or risk_px < fill0 * MIN_STOP:
        risk_px = fill0 * MIN_STOP
        stop0 = fill0 - sgn * risk_px
    stop = stop0
    stop_src = "stop"
    risk_usd = margin * lev * (risk_px / fill0)

    end_i = min(int(entry_i15) + int(hold_bars15), len(c15) - 1)
    mae = mfe = 0.0
    i_mae = i_mfe = int(entry_i15)
    pnl, exit_i, reason, exit_kind = None, end_i, "timeout", "timeout"
    be_done = be_moved = False
    last_tp = None
    tp_exec = None

    for k in range(int(entry_i15), end_i + 1):
        _, o_, h_, l_, c_ = c15[k]
        q_pre = 0.0
        s_pre = 0.0
        for fp, fq in fills:
            q_pre += fq
            s_pre += fp * fq
        avg_pre = s_pre / q_pre
        fees += q_pre * c_ * FUND_8H / 32           # 8ч = 32 бара по 15м

        adv = (fill0 - l_) if sgn == 1 else (h_ - fill0)
        fav = (h_ - fill0) if sgn == 1 else (fill0 - l_)
        if adv > mae:
            mae, i_mae = adv, k
        if fav > mfe:
            mfe, i_mfe = fav, k

        # --- трейлинг: только по закрытым барам ТФ и только «в свою сторону»
        if int(tp_mode) == 2 and trail15 is not None:
            lvl = trail15[k] if k < len(trail15) else None
            if lvl is not None:
                better = (lvl > stop) if sgn == 1 else (lvl < stop)
                sane = (lvl < o_) if sgn == 1 else (lvl > o_)
                if better and sane:
                    stop = lvl
                    stop_src = "trail"

        # --- цель тейка по средней ДО доливок этой свечи ---
        tp_pre = None
        if int(tp_mode) == 0:
            tp_pre = avg_pre + sgn * float(tp_r) * abs(avg_pre - stop0)
        elif int(tp_mode) == 1:
            tp_pre = avg_pre * (1 + sgn * float(tp_frac))
        last_tp = tp_pre

        adverse = (l_ <= stop) if sgn == 1 else (h_ >= stop)
        hit_tp = tp_pre is not None and ((h_ >= tp_pre) if sgn == 1
                                         else (l_ <= tp_pre))

        if adverse:
            # по пути к стопу исполняются лимитки сетки ВЫШЕ стопа
            while adds:
                apx, aq = adds[0]
                reach = (l_ <= apx) if sgn == 1 else (h_ >= apx)
                above = (apx > stop) if sgn == 1 else (apx < stop)
                if reach and above:
                    fees += aq * apx * MAKER
                    fills.append((apx, aq))
                    adds.pop(0)
                else:
                    break
            q = 0.0
            s = 0.0
            for fp, fq in fills:
                q += fq
                s += fp * fq
            avg = s / q
            m_used = sum(m_k[:len(fills)])
            # ликвидация изолированной маржи: убыток съел маржу минус
            # поддерживающую. Для одного колена это ровно fill*(1/lev - MMR),
            # как в signal_engine2.simulate_trade.
            liq_dist = max(0.0, m_used / q - MMR * avg)
            liq_px = avg - sgn * liq_dist
            liq_first = (liq_px >= stop) if sgn == 1 else (liq_px <= stop)
            liq_hit = (l_ <= liq_px) if sgn == 1 else (h_ >= liq_px)
            if liq_first and liq_hit:
                pnl = -m_used - fees
                exit_i, reason, exit_kind = k, "liq", "liq"
                break
            px = stop * (1 - sgn * SLIP)
            pnl = sgn * (px - avg) * q - q * px * TAKER - fees
            exit_i, reason, exit_kind = k, "stop", stop_src
            break

        # Ликвидация БЕЗ касания стопа возможна только если стоп стоит ЗА
        # ликвидацией. Движок такого не допускает (ширина стопа ограничена
        # 0.8*liq_frac, а доливки только СОКРАЩАЮТ путь «средняя -> стоп»),
        # но simulate_grid_trade — публичная функция: при прямом вызове с
        # плохим стопом молча отдать тейк вместо ликвидации нельзя.
        m_used_pre = sum(m_k[:len(fills)])
        liq_pre = avg_pre - sgn * max(0.0, m_used_pre / q_pre - MMR * avg_pre)
        if (l_ <= liq_pre) if sgn == 1 else (h_ >= liq_pre):
            pnl = -m_used_pre - fees
            exit_i, reason, exit_kind = k, "liq", "liq"
            break

        if hit_tp:
            pnl = (sgn * (tp_pre - avg_pre) * q_pre
                   - q_pre * tp_pre * MAKER - fees)
            exit_i, reason, exit_kind = k, "tp", "tp"
            tp_exec = tp_pre
            break

        # --- обычные доливки (стопа в этой свече не было) ---
        while adds:
            apx, aq = adds[0]
            if (l_ <= apx) if sgn == 1 else (h_ >= apx):
                fees += aq * apx * MAKER
                fills.append((apx, aq))
                adds.pop(0)
            else:
                break

        # --- безубыток после be_after_r риска (от средней) ---
        if float(be_after_r) > 0 and not be_done:
            q = 0.0
            s = 0.0
            for fp, fq in fills:
                q += fq
                s += fp * fq
            avg = s / q
            trig = avg + sgn * float(be_after_r) * abs(avg - stop0)
            if (h_ >= trig) if sgn == 1 else (l_ <= trig):
                be = avg * (1 + sgn * be_pad)
                better = (be > stop) if sgn == 1 else (be < stop)
                if better:
                    stop = be
                    stop_src = "be"
                    be_moved = True
                be_done = True

    if pnl is None:                                  # таймаут окна удержания
        q = 0.0
        s = 0.0
        for fp, fq in fills:
            q += fq
            s += fp * fq
        avg = s / q
        px = c15[end_i][4] * (1 - sgn * SLIP)
        pnl = sgn * (px - avg) * q - q * px * TAKER - fees
        exit_i, reason, exit_kind = end_i, "timeout", "timeout"

    q_fin = 0.0
    s_fin = 0.0
    for fp, fq in fills:
        q_fin += fq
        s_fin += fp * fq
    avg_fin = s_fin / q_fin
    m_used_fin = sum(m_k[:len(fills)])

    # --- тестировался ли ИСХОДНЫЙ стоп в окне удержания ---
    # Нужно для честной оценки безубытка/трейлинга: если стоп ни разу не
    # тестировался, «спасённые» сделки — артефакт, а не заслуга.
    orig_touched = False
    if be_moved or exit_kind == "trail":
        for k in range(int(entry_i15), end_i + 1):
            _, o_, h_, l_, c_ = c15[k]
            if (l_ <= stop0) if sgn == 1 else (h_ >= stop0):
                orig_touched = True
                break
    elif exit_kind in ("stop", "liq"):
        orig_touched = True

    # --- дошло бы до тейка после стопа / сколько прошло после тейка ---
    would_later, mfe_after = False, 0.0
    if reason in ("stop", "liq") and last_tp is not None:
        for k in range(exit_i + 1, end_i + 1):
            _, o_, h_, l_, c_ = c15[k]
            if (h_ >= last_tp) if sgn == 1 else (l_ <= last_tp):
                would_later = True
                break
    elif reason == "tp" and tp_exec is not None:
        best = mfe
        for k in range(exit_i + 1, end_i + 1):
            _, o_, h_, l_, c_ = c15[k]
            f2 = (h_ - fill0) if sgn == 1 else (fill0 - l_)
            if f2 > best:
                best = f2
        reached_r = sgn * (tp_exec - fill0) / risk_px
        mfe_after = max(0.0, best / risk_px - reached_r)

    # R = ФАКТИЧЕСКИЙ риск позиции: объём на выходе x расстояние от средней
    # до стопа. Прежде риск считался от ПОЛНОЙ маржи (margin*lev*dist), хотя
    # при сетке первое колено получает лишь её часть (при 4 коленах и
    # множителе 1.5 — 0.615$ из 5$). Из-за этого тейк, поставленный на 2R,
    # записывался как +0.54R, и фитнес награждал сетку просто за уменьшение
    # позиции, а не за качество входа. Одиночный вход (grid_levels=1) даёт
    # ровно прежнее значение, поэтому старые результаты не меняются.
    risk_act = abs(q_fin * (avg_fin - stop)) if q_fin else 0.0
    if risk_act <= 0:
        risk_act = risk_usd
    return dict(
        pnl=pnl, r=(pnl / risk_act if risk_act else 0.0),
        exit_i15=exit_i, reason=reason, exit_kind=exit_kind,
        mae_r=round(max(0.0, mae) / risk_px, 3),
        mfe_r=round(max(0.0, mfe) / risk_px, 3),
        time_to_mae_h=round((i_mae - int(entry_i15)) * 0.25, 2),
        time_to_mfe_h=round((i_mfe - int(entry_i15)) * 0.25, 2),
        would_hit_tp_later=bool(would_later),
        mfe_after_tp_r=round(mfe_after, 3),
        fill=fill0, avg_entry=avg_fin, qty=q_fin, risk_px=risk_px,
        risk_usd=round(risk_act, 6), risk_usd_planned=risk_usd,
        grid_fills=len(fills), margin_used=m_used_fin,
        stop_final=stop, stop_init=stop0, be_moved=bool(be_moved),
        be_exit=bool(exit_kind == "be"), orig_stop_touched=bool(orig_touched),
        tp_target=(last_tp if int(tp_mode) != 2 else None),
        fees_note="taker вход/стоп/таймаут, maker доливки и тейк, фандинг 8ч",
    )


# ------------------------------------------------------------- исполнение
def _plan_entry(ts4, c15, ts15, bar_ms):
    """Вход по ОТКРЫТИЮ первой 15м-свечи ПОСЛЕ закрытия сигнального бара.
    (entry_i15, entry_px) либо (None, причина). Проверка «свеча именно
    следующая» обязательна: без неё дырка в 15м-данных дала бы вход по цене
    из будущего вообще без отказа."""
    close_ts = int(ts4) + int(bar_ms)
    j = bisect.bisect_left(ts15, close_ts)
    if j >= len(c15) - 2 or ts15[j] - close_ts > MS_15M:
        return None, REJ_NO15M
    return j, c15[j][1]


def _make_trade(setup, ev_, i, c4, ctx, g, c15, ts15, lev, hypo=False,
                bar_ms=MS_4H, trail15=None):
    """Общий путь сделки (реальной и гипотетической near-miss).
    hypo=True — «а что было бы, если бы вошли»: ВЕРХНИЙ лимит ширины стопа не
    проверяем (near-miss по нему как раз и не прошёл), но нижний (MIN_STOP) и
    границу ликвидации проверяем всё равно."""
    side = ev_["side"]
    sgn = 1 if side == "L" else -1
    ts4 = c4[i][0]
    atr_b = ctx["atr_bar"][i]
    stop_px = ev_["stop"]
    j, entry_px = _plan_entry(ts4, c15, ts15, bar_ms)
    if j is None:
        return None, entry_px
    dist = sgn * (entry_px - stop_px) / entry_px
    hard_cap = 0.8 * liq_frac(lev)
    cap = hard_cap if hypo else min(float(_gg(g, "stop_cap")), hard_cap)
    if dist <= 0 or dist < MIN_STOP or dist > cap:
        return None, REJ_ENTRY_STOP

    sim = simulate_grid_trade(
        side, j, entry_px, stop_px, c15, lev,
        int(_gg(g, "hold_days")) * BARS_DAY_15,
        levels=int(_gg(g, "grid_levels")),
        step_frac=float(_gg(g, "grid_step_atr")) * atr_b,
        mult=float(_gg(g, "grid_mult")),
        tp_mode=int(_gg(g, "tp_mode")), tp_r=float(_gg(g, "tp_r")),
        tp_frac=float(_gg(g, "tp_atr")) * atr_b,
        trail15=trail15, be_after_r=float(_gg(g, "be_after_r")))

    entry_ts = ts15[j]
    exit_ts = ts15[sim["exit_i15"]]
    d = ev_["diag"]
    tr = dict(
        entry_ts=entry_ts, exit_ts=exit_ts, side=side,
        # цены НЕ округляем: v2 округлял до 2 знаков «под BTC», а для DOGE
        # (цена ~0.1) это уничтожило бы значение — движок мультимонетный
        entry=entry_px, stop=stop_px, tp=sim["tp_target"],
        pnl=round(sim["pnl"], 6), r=round(sim["r"], 3), reason=sim["reason"],
        hold_h=round((exit_ts - entry_ts) / 3600000.0, 2),
        mae_r=sim["mae_r"], mfe_r=sim["mfe_r"],
        time_to_mae_h=sim["time_to_mae_h"], time_to_mfe_h=sim["time_to_mfe_h"],
        would_hit_tp_later=sim["would_hit_tp_later"],
        mfe_after_tp_r=sim["mfe_after_tp_r"],
        regime=d["regime"],
        rsi=round(d["rsi"], 1) if d["rsi"] is not None else None,
        zone_pos=round(d["zone_pos"], 3) if d["zone_pos"] is not None else None,
        atr_pct=round(d["atr"] * 100, 3) if d["atr"] else None,
        stop_pct=round(dist * 100, 3),
        range_lo=d["range_lo"], range_hi=d["range_hi"],
        entry_mode=0, signal_ts=ts4, entry_ref=ev_["entry_ref"],
        swing_lo=d.get("swing_lo"), swing_hi=d.get("swing_hi"),
        stop_mode=int(_gg(g, "stop_mode")),
        rsi_thr=d.get("rsi_thr"), zone_thr=d.get("zone_thr"),
        bars_waited=0, setup=setup,
        # --- v3 ---
        grid_fills=sim["grid_fills"], avg_entry=sim["avg_entry"],
        margin_used=round(sim["margin_used"], 4), exit_kind=sim["exit_kind"],
        be_moved=sim["be_moved"], be_exit=sim["be_exit"],
        orig_stop_touched=sim["orig_stop_touched"],
        tp_mode=int(_gg(g, "tp_mode")), tp_target=sim["tp_target"],
        atr_bar_pct=round(atr_b * 100, 4),
    )
    return tr, None


def _nm_row(ev_, i, c4, hypo, why, q):
    """Строка near-miss (формат — как в signal_engine2, чтобы signal_stats и
    страницы сайта читали её без правок)."""
    d = ev_["diag"]
    ts = c4[i][0]
    return dict(
        ts=ts, side=ev_["side"], gate=q["name"],
        got=round(q["got"], 4), need=round(q["need"], 4),
        margin=round(q["margin"], 4),
        entry=ev_["entry_ref"], stop=ev_["stop"], tp=ev_["tp"],
        dist=round(ev_["dist"], 5) if ev_["dist"] else None,
        hypo_r=hypo["r"] if hypo else 0.0,
        hypo_reason=hypo["reason"] if hypo else why,
        hypo_mfe_r=hypo["mfe_r"] if hypo else 0.0,
        hypo_mae_r=hypo["mae_r"] if hypo else 0.0,
        hypo_pnl=hypo["pnl"] if hypo else 0.0,
        hypo_grid_fills=hypo["grid_fills"] if hypo else 0,
        regime=d["regime"],
        rsi=round(d["rsi"], 1) if d["rsi"] is not None else None,
        zone_pos=round(d["zone_pos"], 3) if d["zone_pos"] is not None else None,
        range_lo=d["range_lo"], range_hi=d["range_hi"],
        atr_pct=round(d["atr"] * 100, 3) if d["atr"] else None,
        stop_pct=round(ev_["dist"] * 100, 3) if ev_["dist"] else None,
        rsi_thr=d.get("rsi_thr"), zone_thr=d.get("zone_thr"),
    )


def run_setup(setup, g, c4, ctx, c15, ts15, lev, symbol=None,
              signal_range=None, collect_diag=True, interval_min=None):
    """Прогон сетапа по всей истории на одной монете.

    symbol — монета (только для отчётности; вся арифметика в долях цены,
        поэтому движок работает на любой монете без правок). Если контекст
        собран по другой монете — падаем громко, а не считаем тихо мусор.
    signal_range=(a, b): сигналы берутся только из баров ТФ [a, b); сделки
        при этом могут закрываться позже b (walk-forward и holdout).
    Одна позиция на сетап; занятость и кулдаун считаются отдельно.
    near_misses считаются той же механикой и НИКАК не влияют на баланс.
    """
    if setup not in SETUPS3:
        raise ValueError(f"неизвестный сетап: {setup}")
    ctx_iv = ctx.get("interval_min") if isinstance(ctx, dict) else None
    if interval_min is None:
        interval_min = int(ctx_iv or 240)
    else:
        interval_min = int(interval_min)
        if ctx_iv is not None and int(ctx_iv) != interval_min:
            raise ValueError(
                f"interval_min={interval_min} не совпадает с контекстом "
                f"({ctx_iv}): prep_context и run_setup должны звать один ТФ")
    if symbol is None:
        symbol = ctx.get("symbol", "?")
    elif ctx.get("symbol") not in (None, "?", symbol):
        raise ValueError(f"контекст собран по {ctx.get('symbol')}, "
                         f"а прогон запрошен по {symbol}")
    bar_ms = bar_ms_of(interval_min)
    ext = build_ext(setup, g, c4, interval_min=interval_min)
    n = len(c4)
    a, b = signal_range or (0, n)
    b = min(int(b), n)
    a = max(int(a), int(ext["warm"]))

    # Трейлинг-ряд по 15м-барам считаем ОДИН раз на прогон (а не на сделку).
    trail15 = None
    if int(_gg(g, "tp_mode")) == 2:
        arr = ext["trail_lo"] if setup_side(setup) == "L" else ext["trail_hi"]
        trail15 = trail_series_15(c4, bar_ms, ts15, arr)

    balance, peak, max_dd = START, START, 0.0
    trades, near_misses = [], []
    reject_counts, gate_solo = {}, {}
    monthly = {}
    blocked_busy = blocked_cooldown = blocked_ruined = 0
    signals = 0
    pass_by_regime = {0: 0, 1: 0, 2: 0}
    bars_eval = 0
    busy_until_ts, last_exit_ts = 0, 0
    ruined = False
    cool_ms = int(_gg(g, "cooldown")) * bar_ms
    t0 = c4[a][0] if a < n else c4[-1][0]

    for i in range(a, b):
        ts = c4[i][0]
        sig_ts = ts + bar_ms                    # сигнал есть с ЗАКРЫТИЯ бара
        cool_ok = (not last_exit_ts) or (sig_ts >= last_exit_ts + cool_ms)
        ev_ = gate_eval(setup, i, c4, ctx, g, ext)
        bars_eval += 1
        if collect_diag:
            # имена считаем УНИКАЛЬНО на бар: у пулбэка тренд проверяется и
            # профилем сетапа, и (если включён) общим ворото trend_gate —
            # без дедупликации один бар дал бы два отказа с одним именем и
            # диагностика показала бы больше отказов, чем было баров
            seen = set()
            for q in ev_["gates"]:
                if not q["ok"] and q["name"] not in seen:
                    seen.add(q["name"])
                    gate_solo[q["name"]] = gate_solo.get(q["name"], 0) + 1
        free = sig_ts >= busy_until_ts and cool_ok

        if not ev_["ok"]:
            key = ev_["first_fail"]
            reject_counts[key] = reject_counts.get(key, 0) + 1
            if collect_diag and ev_["near_miss"] and free:
                q = next(x for x in ev_["gates"] if not x["ok"])
                hypo, why = _make_trade(setup, ev_, i, c4, ctx, g, c15, ts15,
                                        lev, hypo=True, bar_ms=bar_ms,
                                        trail15=trail15)
                near_misses.append(_nm_row(ev_, i, c4, hypo, why, q))
            continue

        signals += 1
        pass_by_regime[ev_["diag"]["regime"]] += 1
        if ruined:
            blocked_ruined += 1
            continue
        if sig_ts < busy_until_ts:
            blocked_busy += 1
            continue
        if not cool_ok:
            blocked_cooldown += 1
            continue

        tr, why = _make_trade(setup, ev_, i, c4, ctx, g, c15, ts15, lev,
                              bar_ms=bar_ms, trail15=trail15)
        if tr is None:
            reject_counts[why] = reject_counts.get(why, 0) + 1
            if why == REJ_NO15M:
                break
            continue

        balance += tr["pnl"]
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak)
        m_idx = int((tr["exit_ts"] - t0) // MONTH_MS)
        monthly[m_idx] = monthly.get(m_idx, 0.0) + tr["pnl"]
        trades.append(tr)
        busy_until_ts = tr["exit_ts"]
        last_exit_ts = tr["exit_ts"]
        if balance < MARGIN:
            ruined = True          # маржи на следующий цикл уже не хватает

    last_ts = c4[min(max(a, b - 1), n - 1)][0] if n else t0
    fills_hist = {}
    for t in trades:
        fills_hist[t["grid_fills"]] = fills_hist.get(t["grid_fills"], 0) + 1
    n_tr = len(trades)
    be_exits = [t for t in trades if t["be_exit"]]
    return dict(
        balance=balance, trades=trades, near_misses=near_misses,
        reject_counts=reject_counts, blocked_busy=blocked_busy,
        blocked_cooldown=blocked_cooldown, blocked_ruined=blocked_ruined,
        monthly=monthly, max_dd=max_dd, ruined=ruined,
        months=max(1e-9, (last_ts - t0) / MONTH_MS),
        setup=setup, lev=lev, signals=signals,
        pass_by_regime=pass_by_regime, gate_solo=gate_solo,
        bars_eval=bars_eval, signal_range=(a, b),
        interval_min=interval_min, symbol=symbol,
        # --- v3: сетка и безубыток ---
        grid_fills=sum(t["grid_fills"] for t in trades),
        grid_fill_hist=fills_hist,
        avg_grid_fills=(round(sum(t["grid_fills"] for t in trades) / n_tr, 3)
                        if n_tr else 0.0),
        avg_entry=(round(sum(t["avg_entry"] for t in trades) / n_tr, 6)
                   if n_tr else None),
        be_moved=sum(1 for t in trades if t["be_moved"]),
        be_hit=len(be_exits),
        be_hit_share=(round(len(be_exits) / n_tr * 100, 1) if n_tr else 0.0),
        be_no_stop=sum(1 for t in be_exits if not t["orig_stop_touched"]),
        trail_exits=sum(1 for t in trades if t["exit_kind"] == "trail"),
    )


# ------------------------------------------------------------------ метрики
def stats(r):
    """Агрегаты прогона (тот же состав, что у signal_engine2.stats, плюс
    поля сетки и безубытка)."""
    n_months = max(1, int(r["months"]),
                   (max(r["monthly"]) + 1) if r["monthly"] else 0)
    rets = [(r["monthly"].get(m, 0.0) / START) * 100 for m in range(n_months)]
    med = sorted(rets)[len(rets) // 2] if rets else 0.0
    p25 = ev.percentile(rets, 0.25)
    tr = r["trades"]
    n = len(tr)
    wins = sum(1 for t in tr if t["pnl"] > 0)
    reasons = {}
    kinds = {}
    for t in tr:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
        kinds[t["exit_kind"]] = kinds.get(t["exit_kind"], 0) + 1
    gross_p = sum(t["pnl"] for t in tr if t["pnl"] > 0)
    gross_l = -sum(t["pnl"] for t in tr if t["pnl"] < 0)
    stopped = [t for t in tr if t["reason"] == "stop"]
    took = [t for t in tr if t["reason"] == "tp"]
    be_exits = [t for t in tr if t["be_exit"]]
    return dict(
        med=med, p25=p25, n=n, wins=wins,
        wr=round(wins / n * 100, 1) if n else 0.0,
        tpm=n / n_months,
        ret=round((r["balance"] / START - 1) * 100, 1),
        dd=round(r["max_dd"] * 100, 1),
        avg_hold_h=round(sum(t["hold_h"] for t in tr) / n, 1) if n else 0.0,
        pos_share=round(sum(1 for x in rets if x > 0) / len(rets) * 100)
        if rets else 0,
        exp_r=round(sum(t["r"] for t in tr) / n, 3) if n else 0.0,
        exp_usd=round(sum(t["pnl"] for t in tr) / n, 4) if n else 0.0,
        pf=round(gross_p / gross_l, 2) if gross_l > 0 else None,
        avg_mae_r=round(sum(t["mae_r"] for t in tr) / n, 3) if n else 0.0,
        avg_mfe_r=round(sum(t["mfe_r"] for t in tr) / n, 3) if n else 0.0,
        reasons=reasons, exit_kinds=kinds,
        n_stop=len(stopped), n_tp=len(took),
        stop_then_tp=sum(1 for t in stopped if t["would_hit_tp_later"]),
        n_near=len(r.get("near_misses", [])),
        near_avg_r=round(sum(x["hypo_r"] for x in r["near_misses"])
                         / len(r["near_misses"]), 3)
        if r.get("near_misses") else 0.0,
        tpm_active=round(n / max(0.5, (tr[-1]["exit_ts"] - tr[0]["entry_ts"])
                                 / MONTH_MS), 2) if n >= 2 else 0.0,
        # --- v3 ---
        avg_grid_fills=r.get("avg_grid_fills", 0.0),
        grid_full_share=(round(sum(1 for t in tr if t["grid_fills"] >= 2)
                               / n * 100, 1) if n else 0.0),
        be_hit=len(be_exits),
        be_hit_share=round(len(be_exits) / n * 100, 1) if n else 0.0,
        be_no_stop=sum(1 for t in be_exits if not t["orig_stop_touched"]),
        be_no_stop_share=(round(sum(1 for t in be_exits
                                    if not t["orig_stop_touched"])
                                / len(be_exits) * 100, 1) if be_exits else 0.0),
        trail_exits=r.get("trail_exits", 0),
        avg_r_win=round(sum(t["r"] for t in tr if t["pnl"] > 0)
                        / wins, 3) if wins else 0.0,
        avg_r_loss=round(sum(t["r"] for t in tr if t["pnl"] <= 0)
                         / (n - wins), 3) if n - wins else 0.0,
    )


# Пол отбора: должен быть НИЖЕ любой достижимой реальной оценки, иначе «не
# торговать вообще» выигрывает у торгующего генома с отрицательным счётом.
# Метка «мало данных» = FIT_FLOOR + n: среди безнадёжных лучше тот, кто хотя
# бы торговал, но ЛЮБАЯ реальная оценка всё равно выше любой метки.
FIT_FLOOR = -1000.0
MIN_TRADES_FIT = 25       # пол выборки: меньше 25 сделок — выводов нет
TPM_CAP = 8.0             # выше 8 сделок в месяц частота больше не поощряется
WR_TARGET = 50.0          # бонус за винрейт платим ВЫШЕ 50%
DD_SOFT_CAP = 25.0        # просадка глубже 25% — штраф
WR_BONUS = 0.06           # цена одного пункта винрейта над целью
DD_PEN = 0.20             # цена одного пункта просадки над потолком


def fitness(r):
    """Фитнес v3 под цель владельца «главное прибыль».

    Основа — ОЖИДАЕМЫЙ ДОХОД В МЕСЯЦ в единицах риска: exp_r * сделок в месяц.
    Именно это, а не отдельно взятое качество сделки, и есть прибыль: сетап с
    +0.5R раз в квартал беднее сетапа с +0.1R десять раз в месяц. Частота
    ограничена сверху TPM_CAP (иначе побеждает скальпинг-шум), а на
    отрицательном exp_r множитель работает как штраф — частый убыточный
    сетап хуже редкого убыточного, и это правильно.
    Дальше: бонус за винрейт над 50% (владелец просил winrate), штраф за
    просадку глубже 25% и за слив. Пол по числу сделок — 25."""
    st = stats(r)
    if st["n"] < MIN_TRADES_FIT:
        return FIT_FLOOR + st["n"]
    f = st["exp_r"] * min(st["tpm"], TPM_CAP) * 10.0
    f += WR_BONUS * max(0.0, st["wr"] - WR_TARGET)
    f -= DD_PEN * max(0.0, st["dd"] - DD_SOFT_CAP)
    if st["pf"] is not None and st["pf"] < 1.0:
        f -= 2.0 * (1.0 - st["pf"])
    if r["ruined"]:
        f -= 50.0
    return f


def oos_score(r):
    """Оценка на экзамене (OOS/holdout). Тот же принцип и тот же сентинел,
    но пол выборки мягче: на коротком экзамене 25 сделок часто не набирается,
    а бездействие всё равно не должно выигрывать у торговли."""
    st = stats(r)
    if st["n"] < 5:
        return FIT_FLOOR + st["n"]
    f = st["exp_r"] * min(st["tpm"], TPM_CAP) * 10.0
    f += WR_BONUS * max(0.0, st["wr"] - WR_TARGET)
    f -= DD_PEN * max(0.0, st["dd"] - DD_SOFT_CAP)
    if r["ruined"]:
        f -= 100.0
    return f
