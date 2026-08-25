# -*- coding: utf-8 -*-
"""ЧЕСТНАЯ ПЕРЕПРОВЕРКА ПЯТИ ФИНАЛЬНЫХ БОТОВ (DOGE/LTC/BTC/ETH/SOL, режим
"final") — той же методологией, которой проверяются сигналы.

Зачем: цифры в config.py и на сайте (+145% DOGE, +140% BTC и т.д.) получены
на данных, которые сами же участвовали в подборе конфигов. Верить им нельзя.
Здесь считается, что от ботов остаётся, когда мерить честно.

Что делает скрипт (ничего не подбирает — только меряет):

  0. САМОПРОВЕРКИ.
     а) регрессия: прогон на всей истории обязан воспроизвести цифры,
        записанные в config.py (иначе вся дальнейшая арифметика — мусор);
     б) причинность (нет заглядывания вперёд): «префикс vs полная история» —
        aux ПЕРЕСЧИТЫВАЕТСЯ на обрезанной истории, множество точек входа до
        точки обрезки обязано совпасть бит в бит.

  1. РАЗДЕЛЕНИЕ ИСТОРИИ: [0..hold) = обучающая часть, [hold..n) = ХОЛДОУТ,
     hold = int(n * 0.72) — ровно как у сигналов (evolution12/finalize_signals4).
     Прогон ТЕКУЩИХ конфигов отдельно на каждой части, на «своём» плече бота.

     ВАЖНАЯ ОГОВОРКА, которую скрипт печатает и считает численно: для ботов
     этот холдоут НЕ является неприкосновенным. Конфиги отбирались волнами
     v4..v8 на ВСЕЙ истории 1150 дней, walk-forward e4.fold_bounds_3y, где
     третий экзамен (OOS) — это последние 8.4 месяца, т.е. кусок холдоута;
     плечо выбиралось по просадке на полных 3.2 годах. Поэтому «холдоут» тут
     — это в лучшем случае «часть, которую подбор видел меньше остальных».
     Положительный результат на нём почти ничего не доказывает; отрицательный
     — доказывает многое.

  2. БЕНЧМАРКИ на том же холдоуте:
     а) «купил монету и держал» (1x, без плеча);
     б) НЕОБУЧЕННЫЙ конфиг — ручные дефолты RSI-сетки из шапки config.py
        (RSI 14/30, окно 400, шаг 1%, 3 колена, x1.5, TP 4%, sweep 2%,
        зона 0.25, таймаут 192), все внешние ворота выключены. Один и тот же
        для всех пяти монет, никем не подбирался;
     в) СЛУЧАЙНЫЕ геномы ядра стратегии (200 шт. на монету, ворота выключены)
        — нулевое распределение «что даёт просто сетка со случайными
        параметрами». Отобранный конфиг обязан быть заметно правее него.

  3. УСТОЙЧИВОСТЬ ПАРАМЕТРОВ (замена теста зёрен: конфиги подбирались давно
     и разными волнами, повторить их отбор с другим зерном нечем).
     30 случайных возмущений генома: каждый ЧИСЛОВОЙ ген независимо
     умножается на (1 + U[-10%, +10%]), переключатели и индексы наборов не
     трогаются. Прогон на холдоуте. Логика: настоящая закономерность —
     это плато (соседи дают похожий результат), подгонка — это шпиль
     (соседи разваливаются, а сам конфиг стоит на самом верху).
     Делается на ТРЁХ зёрнах, чтобы вывод не зависел от зерна.

  4. ИЗДЕРЖКИ: комиссии (taker 0.055% / maker 0.02%), проскальзывание 0.03%
     и фандинг 0.01%/8ч считаются внутри evolution2.run5. Скрипт меряет их
     вклад, временно обнуляя соответствующие глобалы модуля (файл движка не
     трогается) и сравнивая с обычным прогоном.

  5. ПЛЕЧО: лестница x5..x15 на ХОЛДОУТЕ + рекомендация по объективному
     правилу (наибольшее плечо с просадкой <= 20%), а не по полному периоду.

  6. КОПЕЕЧНЫЕ ВЫХОДЫ (артефакт безубытка). Перенос стопа в безубыток
     (ген be_move=1) закрывает цикл у средней цены +0.15%: формально плюс,
     фактически ноль. Из-за этого винрейт ботов доходит до 98%, а часть из
     них держится ТОЛЬКО на таких выходах. Скрипт считает долю циклов «в
     ноль» (|PnL| < 1% маржи цикла = $0.05), честный винрейт без них и
     главное — каким был бы итог, если копеечные выходы обнулить. Это
     ПЯТЫЙ критерий вердикта: бот, у которого без копеек остаётся минус,
     подтверждённым считаться не может.

Все прогоны — evolution2.run5 (тот же движок, что у боевых цифр), конфиг ->
геном через evolution7.cfg_to_genome + evolution8.OFF8, фильтр входа
evolution8.make_filter8 на полном aux (funding/OI/SPX/DXY/золото/EMA/MA/
Aroon/режим/паттерны/SMC).

Запуск: python bots_honest.py
Вывод дублируется в bots_honest_out.txt.
"""

import random
import statistics
import sys
import time

import config
import evolution as ev
import evolution2 as e2
import evolution4 as e4
import evolution5 as e5
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

DAYS = 1150
HOLD_FRAC = 0.72          # тот же холдоут, что у сигналов
LEVS = [5, 8, 10, 12, 15]
DD_CAP = 20.0             # правило выбора плеча: просадка <= 20%
N_PERT = 30               # возмущений генома на одно зерно
PERT = 0.10               # +-10% по числовым генам
PERT_SEEDS = [101, 202, 303]
N_RAND = 200              # случайных геномов ядра на монету
RAND_SEED = 2026
SLEEVE = 50.0             # стартовый капитал стратегии на сайте (/pnl)
BT_BASE = e2.START        # $20 базы бэктеста, маржа цикла $5 = 25%

# --- КОПЕЕЧНЫЕ ВЫХОДЫ («в ноль») -------------------------------------------
# Перенос стопа в безубыток (ген be_move=1) закрывает цикл ровно у средней
# цены + 0.15%: формально плюс, фактически ноль. Такие выходы раздувают
# винрейт до 95-98% и подписываются на графике как «+0.001$ победа».
# ПОРОГ: цикл считается закрытым «в ноль», если |PnL| меньше 1% маржи цикла.
# Маржа цикла $5 (e2.MARGIN) -> порог $0.05. Почему 1%: движок берёт с цикла
# около 0.1% на комиссии+слиппедж, то есть $0.05 — это примерно ПОЛОВИНА
# издержек одного цикла; всё, что меньше, деньгами не является ни при каком
# способе счёта. Порог односторонним не делаем: копеечный минус — такой же
# «ноль», как копеечный плюс.
TINY_FRAC = 0.01
TINY_USD = e2.MARGIN * TINY_FRAC      # $0.05 при марже цикла $5
BE_PAD = 0.0015                       # надбавка безубытка в evolution2.run5

# Цифры, задокументированные в config.py (сделок, итог %, DD %) — регрессия.
DOC = {"DOGEUSDT": (1276, 145.0, 18.5), "LTCUSDT": (1307, 115.9, 17.0),
       "BTCUSDT": (60, 140.2, 12.6), "ETHUSDT": (237, 22.7, 21.8),
       "SOLUSDT": (1245, 45.8, 18.4)}

# Гены, которые возмущаются в тесте устойчивости (числовые величины).
NUMERIC_GENES = ["rsi_os", "zone_l", "zone_s", "window", "step", "mult", "tp",
                 "sweep", "max_bars", "cooldown", "knife",
                 "fund_long_max", "fund_short_min", "spx_long_min",
                 "dxy_long_max", "gold_long_max",
                 "aroon_long_min", "aroon_short_min"]
# Не возмущаются: переключатели (0/1/2), индексы наборов и число колен сетки —
# у них нет «на 10% больше», сдвиг такого гена это уже ДРУГАЯ стратегия.
DISCRETE_GENES = ["rsi_idx", "levels", "be_move", "oi_gate", "ema_mode",
                  "ema_n_idx", "ma_mode", "masf_idx", "masl_idx", "aroon_idx",
                  "direction", "regime_gate", "pattern_gate", "ob_gate",
                  "fvg_gate", "structure_mode"]

# Необученный «разумный» конфиг — ручные дефолты из шапки config.py
# (секция «Стратегия RSI-сетка + уровни»), одинаковые для всех монет.
DEFAULT_CFG = dict(rsi_period=config.RSI_PERIOD, rsi_os=config.RSI_OS,
                   zone_l=config.ZONE, zone_s=config.ZONE,
                   window=config.RANGE_WINDOW, step=config.GRID_STEP,
                   levels=config.GRID_LEVELS, mult=config.GRID_MULT,
                   tp=config.TP_PCT, sweep=config.SWEEP_BUF,
                   max_bars=config.MAX_BARS, cooldown=0, knife=0.0)


class Tee:
    """Печать одновременно в консоль и в файл отчёта."""

    def __init__(self, path):
        self.out = sys.stdout
        self.fh = open(path, "w", encoding="utf-8")

    def write(self, s):
        self.out.write(s)
        self.fh.write(s)

    def flush(self):
        self.out.flush()
        self.fh.flush()


def slice_aux(v, a, b):
    """Срез aux под candles[a:b] (списки, списки списков, кортежи)."""
    if isinstance(v, tuple):
        return tuple(slice_aux(x, a, b) for x in v)
    if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
        return [slice_aux(x, a, b) for x in v]
    return v[a:b]


def _run5_takes_storm():
    """Умеет ли движок этой ветки принимать параметр storm.

    Файл переносится между ветками, а ветки разошлись: на одной run5 знает про
    storm, на другой нет. Проверять подписью, а не try/except по TypeError:
    TypeError может прилететь и из глубины прогона, и тогда мы молча
    пересчитали бы всё без storm, решив, что движок старый.
    """
    import inspect
    try:
        return "storm" in inspect.signature(e2.run5).parameters
    except (TypeError, ValueError):                # noqa: BLE001
        return False


_RUN5_STORM = None


def run_at(candles, pre, g, filt, lev, storm=None, events=None):
    """Прогон на заданном плече; глобалы e2 восстанавливаются."""
    global _RUN5_STORM
    if _RUN5_STORM is None:
        _RUN5_STORM = _run5_takes_storm()
    old = e2.LEV
    e2.LEV = lev
    try:
        if _RUN5_STORM:
            return e2.run5(candles, pre, g, entry_filter=filt, events=events,
                           storm=storm)
        return e2.run5(candles, pre, g, entry_filter=filt, events=events)
    finally:
        e2.LEV = old


def compound_pct(pnls):
    """Итог с реинвестом (модель сайта /pnl): маржа = 25% капитала."""
    eq = SLEEVE
    for p in pnls:
        eq *= (1 + p / BT_BASE)
    return (eq / SLEEVE - 1) * 100


def summarize(r, events, months):
    """Сводка прогона: сделки, WR, итог (фикс. маржа и реинвест), DD, R."""
    pnls = [e["pnl"] for e in events if e["type"] == "close"]
    liqs = sum(1 for e in events if e["type"] == "close" and e["liq"])
    st = e2.stats(r)
    ret = (r["balance"] / e2.START - 1) * 100
    return dict(trades=r["trades"],
                wr=(r["wins"] / r["trades"] * 100) if r["trades"] else 0.0,
                ret=ret, comp=compound_pct(pnls),
                per_month=ret / months if months else 0.0,
                dd=r["max_dd"] * 100, ruined=r["ruined"], liqs=liqs,
                pos_share=st["pos_share"] * 100, med=st["med"],
                avg_r=(sum(pnls) / len(pnls) / e2.MARGIN) if pnls else 0.0,
                pnl_usd=r["balance"] - e2.START, pnls=pnls)


# ------------------------------------------- копеечные выходы и безубыток

def is_tiny(pnl):
    """Цикл закрылся «в ноль»: |PnL| меньше 1% маржи цикла (см. TINY_USD)."""
    return abs(pnl) < TINY_USD


def is_be_exit(reason, exit_price, stop0):
    """Цикл закрыт ПЕРЕНЕСЁННЫМ стопом (безубыток)?

    Определение точное, а не по косвенным признакам: в evolution2.run5 стоп
    ставится один раз при входе (за экстремумом окна с запасом sweep) и
    двигается ровно в одном месте — в блоке be_move (стоп -> средняя+0.15%).
    Значит «вышли по стопу, но цена выхода НЕ равна стопу входа» = безубыток.
    stop0 — стоп входа; допуск 1e-6 относительный, потому что в JSON цены
    округлены до 7 значащих цифр (перенос двигает стоп на проценты, а не на
    миллионные доли, спутать нельзя).
    """
    if reason != "stop" or not stop0:
        return False
    return abs(exit_price - stop0) > 1e-6 * abs(stop0)


def mark_cycles(events, candles=None, g=None):
    """События движка -> список циклов с пометками tiny/be.

    [{t, pnl, reason, tiny, be}], где be:
      * False       — ген be_move=0, переносить стоп движку нечем;
      * точно       — если переданы candles и g: стоп входа восстанавливается
                      из rolling_extremes(window) и сравнивается с ценой выхода
                      (см. is_be_exit);
      * ПРИБЛИЖЁННО — если candles/g не переданы: «причина стоп и PnL >= 0».
                      Приближение занижает счёт (безубыток с минусом после
                      комиссий в него не попадает) и завышает (обычный стоп,
                      закрытый в ноль доливками, попадает зря). На пяти ботах
                      расхождение с точным счётом 4..31 цикл из 60..1307.
    Метод возвращается вторым значением: "off" | "exact" | "approx".
    """
    be_on = bool(g and g.get("be_move"))
    exact = be_on and candles is not None and g is not None
    method = "off" if not be_on else ("exact" if exact else "approx")
    if exact:
        rlow, rhigh = ev.rolling_extremes(candles, g["window"])
        idx = {c[0]: i for i, c in enumerate(candles)}
    out, side, bar = [], None, None
    for e in events:
        if e["type"] == "entry":
            side, bar = e["side"], e["t"]
        elif e["type"] == "close":
            reason = e.get("reason") or "?"
            if not be_on:
                be = False
            elif exact:
                i = idx.get(bar)
                if i is None:
                    be = None
                else:
                    stop0 = (rlow[i] * (1 - g["sweep"]) if side == "L"
                             else rhigh[i] * (1 + g["sweep"]))
                    be = is_be_exit(reason, e["price"], stop0)
            else:
                be = reason == "stop" and e["pnl"] >= 0
            out.append(dict(t=e["t"], pnl=e["pnl"], reason=reason,
                            tiny=is_tiny(e["pnl"]), be=be))
            side = bar = None
    return out, method


def ret_no_tiny(pnls, base=None):
    """Итог % на базе $20, если КОПЕЕЧНЫЕ выходы считать нулём.

    Ключевая цифра проверки на артефакт: остаётся ли бот в плюсе, когда из
    результата убрать всё, что закрылось «в ноль».
    """
    base = base or e2.START
    return sum(p for p in pnls if not is_tiny(p)) / base * 100


def tiny_stats(cycles, method="exact", months=None):
    """Метрики копеечных выходов по размеченным циклам (mark_cycles).

    Считает раздельно: сколько циклов «в ноль», каким был бы итог без них,
    честный винрейт и асимметрию средних. Всё в долларах маржи $5 / процентах
    от базы бэктеста $20 — сопоставимо с остальными цифрами отчёта.
    """
    cycles = [c for c in cycles if c.get("pnl") is not None]
    n = len(cycles)
    pnl = [c["pnl"] for c in cycles]
    tiny = [c["pnl"] for c in cycles if c["tiny"]]
    rw = [c["pnl"] for c in cycles if not c["tiny"] and c["pnl"] > 0]
    rl = [c["pnl"] for c in cycles if not c["tiny"] and c["pnl"] < 0]
    loss_all = [p for p in pnl if p < 0]
    be_n = sum(1 for c in cycles if c.get("be"))
    s_tiny, s_rw, s_rl = sum(tiny), sum(rw), sum(rl)
    total = sum(pnl)
    without = total - s_tiny
    gross = s_tiny + s_rw            # вся валовая прибыль (плюсы любой величины)
    sh = lambda k: round(k / n * 100, 1) if n else 0.0
    return dict(
        n=n, tiny_n=len(tiny), tiny_share=sh(len(tiny)),
        tiny_win_n=sum(1 for p in tiny if p > 0),
        tiny_loss_n=sum(1 for p in tiny if p < 0),
        real_win_n=len(rw), real_loss_n=len(rl), loss_n=len(loss_all),
        wr=round(sum(1 for p in pnl if p > 0) / n * 100, 1) if n else 0.0,
        # честный винрейт: РЕАЛЬНЫЕ плюсы против ВСЕХ убытков (копеечный плюс
        # победой не считается, копеечный минус из знаменателя не выкидываем —
        # так консервативнее)
        wr_honest=(round(len(rw) / (len(rw) + len(loss_all)) * 100, 1)
                   if rw or loss_all else 0.0),
        # то же, но копеечные убраны с обеих сторон — для полноты картины
        wr_ex_tiny=(round(len(rw) / (len(rw) + len(rl)) * 100, 1)
                    if rw or rl else 0.0),
        pnl_usd=round(total, 2),
        pnl_split=dict(tiny=round(s_tiny, 2), wins=round(s_rw, 2),
                       losses=round(s_rl, 2)),
        pnl_without_tiny=round(without, 2),
        ret=round(total / e2.START * 100, 1),
        ret_without_tiny=round(without / e2.START * 100, 1),
        per_month=(round(total / e2.START * 100 / months, 2) if months else None),
        per_month_without_tiny=(round(without / e2.START * 100 / months, 2)
                                if months else None),
        # какую долю ВСЕЙ валовой прибыли принесли копеечные выходы
        tiny_profit_share=(round(s_tiny / gross * 100, 1) if gross > 1e-9
                           else None),
        # какую долю ЧИСТОГО итога сделали копейки (>= 100% = без них минус);
        # None, если итог и так неположительный — доля там бессмысленна
        tiny_net_share=(round(s_tiny / total * 100, 1) if total > 1e-9
                        else None),
        be_n=be_n, be_share=sh(be_n), be_method=method,
        avg_win_real=round(s_rw / len(rw), 3) if rw else 0.0,
        avg_loss=round(s_rl / len(rl), 3) if rl else 0.0,
        avg_tiny=round(s_tiny / len(tiny), 4) if tiny else 0.0,
        # прямой ответ: бот держится на артефакте, если итог плюсовой, а без
        # копеечных выходов — минусовой
        holds_on_artifact=bool(total > 0 and without <= 0))


def cycle_metrics(events, candles=None, g=None, months=None):
    """mark_cycles + tiny_stats одним вызовом (обычный способ применения)."""
    cyc, method = mark_cycles(events, candles, g)
    return tiny_stats(cyc, method, months)


def portfolio(curves, n_bots):
    """Суммарная кривая n счетов по $20: итог % и просадка %."""
    merged = sorted((ts, sym, p) for sym, rows in curves.items()
                    for ts, p in rows)
    bal = {s: e2.START for s in curves}
    total = e2.START * n_bots
    peak, dd = total, 0.0
    for ts, sym, p in merged:
        bal[sym] += p
        total = sum(bal.values())
        peak = max(peak, total)
        if peak > 0:
            dd = max(dd, (peak - total) / peak)
    return (total / (e2.START * n_bots) - 1) * 100, dd * 100


def perturb(g, genes, rng, frac):
    """Возмущение числовых генов на +-frac, переключатели не трогаем."""
    out = dict(g)
    for k in NUMERIC_GENES:
        if k in out:
            out[k] = out[k] * (1 + rng.uniform(-frac, frac))
    _, clamp, _, _ = e4.ga_tools(genes)
    fixed = clamp(out)
    for k in DISCRETE_GENES:            # clamp мог округлить — вернём как было
        if k in g:
            fixed[k] = g[k]
    return fixed


def rand_core(genes, rng):
    """Случайный геном ЯДРА стратегии (RSI-сетка), все ворота выключены."""
    g = {}
    for k, (lo, hi, is_int) in genes.items():
        v = rng.uniform(lo, hi)
        g[k] = int(round(v)) if is_int else v
    g.update(e8.OFF8)                    # SMC выкл
    g.update(e4.OFF4)                    # funding/OI/макро выкл
    g.update(e5.OFF5)                    # EMA/MA/Aroon выкл (+ индексы наборов)
    g.update(e7.OFF7)                    # обе стороны, без режимного гейта
    return g


def pct(vals, q):
    """Перцентиль (линейная интерполяция)."""
    if not vals:
        return 0.0
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    x = q * (len(s) - 1)
    i = int(x)
    return s[i] + (s[min(i + 1, len(s) - 1)] - s[i]) * (x - i)


def fmt_day(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


# --------------------------------------------------------------- самопроверки

def check_regression(prep):
    print("=" * 108)
    print("САМОПРОВЕРКА 1. РЕГРЕССИЯ: прогон на всей истории против цифр "
          "в config.py")
    print(f"  {'бот':10} {'сделок':>8} {'док.':>7} {'итог%':>9} {'док.':>8} "
          f"{'DD%':>7} {'док.':>7}   вердикт")
    ok = True
    for sym, d in prep.items():
        m = d["full"]
        dt, dr, dd = DOC[sym]
        same = (m["trades"] == dt and abs(m["ret"] - dr) < 1.0
                and abs(m["dd"] - dd) < 0.5)
        ok = ok and same
        print(f"  {sym:10} {m['trades']:8} {dt:7} {m['ret']:+9.1f} {dr:+8.1f} "
              f"{m['dd']:7.1f} {dd:7.1f}   {'OK' if same else 'РАСХОЖДЕНИЕ'}")
    print(f"  -> харнесс {'воспроизводит' if ok else 'НЕ воспроизводит'} "
          f"боевые цифры ботов, дальше можно верить арифметике")
    return ok


def check_causality(prep, pct5):
    """«Префикс vs полная история»: aux пересчитывается на обрезанных свечах,
    точки входа до обрезки обязаны совпасть. Ловит любое заглядывание вперёд
    в индикаторах, воротах, паттернах и SMC."""
    print()
    print("=" * 108)
    print("САМОПРОВЕРКА 2. ПРИЧИННОСТЬ (нет заглядывания вперёд): "
          "«префикс vs полная история»")
    print("  aux (funding/OI/макро/EMA/MA/Aroon/режим/паттерны/SMC) считается "
          "ЗАНОВО на обрезанной истории;")
    print("  множество точек входа до точки обрезки обязано совпасть бит в бит")
    allok = True
    for sym, d in prep.items():
        n = len(d["candles"])
        cut = d["hold_i"]                       # обрезаем ровно на старте холдоута
        sub = d["candles"][:cut]
        aux_re = e8.make_aux_builder(pct5, 96)(sym, sub)
        ev_full, ev_pre = [], []
        run_at(d["candles"], d["pre"], d["g"], d["filt"], d["lev"],
               events=ev_full)
        run_at(sub, e2.prep(sub), d["g"], e8.make_filter8(d["g"], aux_re),
               d["lev"], events=ev_pre)
        a = set(e["t"] for e in ev_full if e["type"] == "entry"
                and e["t"] < d["candles"][cut][0])
        b = set(e["t"] for e in ev_pre if e["type"] == "entry")
        same = a == b
        allok = allok and same
        print(f"  {sym:10} обрезка на баре {cut} ({fmt_day(d['candles'][cut][0])})"
              f" | входов в полной {len(a):5} | в префиксе {len(b):5} | "
              f"расхождений {len(a ^ b):4}  "
              f"{'OK' if same else '!!! ЗАГЛЯДЫВАНИЕ ВПЕРЁД'}")
    print(f"  -> {'заглядывания вперёд нет' if allok else 'ЕСТЬ УТЕЧКА БУДУЩЕГО'}")
    return allok


def check_split(prep):
    """Сделки полного прогона должны примерно раскладываться на обучение +
    холдоут. Расхождение возможно только из-за прогрева окна в начале
    сегмента (первые max(window,98) баров холдоута бот молчит) — если оно
    большое, значит разрезание истории само по себе меняет поведение и
    сравнивать части нельзя."""
    print()
    print("=" * 108)
    print("САМОПРОВЕРКА 3. КОРРЕКТНОСТЬ РАЗРЕЗА: сделки полного прогона "
          "против суммы двух частей")
    print(f"  {'бот':10} {'полный':>8} {'обучение':>9} {'холдоут':>8} "
          f"{'сумма':>7} {'разница':>8}   (разница = прогрев окна в начале "
          f"холдоута)")
    for sym, d in prep.items():
        f_, a, b = (d["full"]["trades"], d["train"]["m"]["trades"],
                    d["hold"]["m"]["trades"])
        print(f"  {sym:10} {f_:8} {a:9} {b:8} {a+b:7} {a+b-f_:+8} "
              f"  окно {d['g']['window']} баров = {d['g']['window']/96:.1f} сут")


def report_bounds(prep):
    """Гены, упёршиеся в границу пространства поиска. Это диагностика:
    оптимум на границе почти всегда значит, что фитнес рос в сторону, которую
    диапазон обрезал, — параметр выбран не «по смыслу», а «до упора»."""
    print()
    print("=" * 108)
    print("ДИАГНОСТИКА КОНФИГОВ: гены, упёршиеся в границу диапазона поиска "
          "(evolution8.GENES8)")
    print("  Ген на границе = эволюция тянула его «до упора»; такой параметр "
          "не является внутренним оптимумом,")
    print("  и половина его окрестности в тесте устойчивости физически "
          "недостижима (обрезается clamp).")
    print("  ВАЖНО не спутать: у части генов значение на границе означает "
          "просто «ворота выключены» (fund_long_max=0.06,")
    print("  spx_long_min=-6, aroon_*_min=0 и т.п. — это OFF-умолчания "
          "evolution4/5). Такие помечены [выкл] и НЕ считаются")
    print("  подгонкой. Считаются только настоящие рабочие параметры.")
    off_vals = dict(e4.OFF4)
    off_vals.update(e5.OFF5)
    for sym, d in prep.items():
        hits, offs = [], 0
        for k in NUMERIC_GENES:
            if k not in d["g"]:
                continue
            lo, hi, _ = e8.GENES8[k]
            rngv = hi - lo
            v = d["g"][k]
            on_edge = abs(v - lo) <= 0.01 * rngv or abs(v - hi) <= 0.01 * rngv
            if not on_edge:
                continue
            if k in off_vals and abs(v - off_vals[k]) <= 1e-9:
                offs += 1
                continue
            edge = "мин" if abs(v - lo) <= 0.01 * rngv else "МАКС"
            hits.append(f"{k}={v:g} ({edge} "
                        f"{lo if edge == 'мин' else hi:g})")
        print(f"  {sym:10} РАБОЧИХ на границе: {len(hits)} "
              f"(+{offs} шт [выкл], не в счёт)")
        print(f"  {'':10}   " + ("; ".join(hits) if hits
                                 else "нет — все рабочие параметры внутри "
                                      "диапазона"))


def print_contamination(n):
    """Численно: какая доля холдоута участвовала в подборе конфигов."""
    folds = e4.fold_bounds_3y(n)
    h = int(n * HOLD_FRAC)
    a, b, e = folds[-1]
    overlap = max(0, e - max(b, h)) / (n - h) * 100
    print()
    print("=" * 108)
    print("НАСКОЛЬКО ЭТОТ ХОЛДОУТ ЧЕСТЕН (считаем, а не декларируем)")
    print(f"  холдоут = бары [{h}..{n}) — последние {(n-h)/n*100:.0f}% истории")
    print(f"  третий экзамен отбора (e4.fold_bounds_3y) = бары [{b}..{e}) — "
          f"он ЖЕ является OOS-критерием, по которому")
    print(f"  волны v4..v8 выбирали победителя. Пересечение с холдоутом: "
          f"{overlap:.0f}% его баров.")
    print("  Плюс: плечо каждого бота выбрано по просадке на ПОЛНЫХ 3.2 годах "
          "(холдоут внутри).")
    print("  ВЫВОД: неприкосновенного холдоута у ботов НЕТ и получить его "
          "уже неоткуда — данные закончились.")
    print("  Значит: плюс на холдоуте почти ничего не доказывает (подбор его "
          "видел), а минус — доказывает")
    print("  много. Главным доказательством становится не он, а тест "
          "устойчивости параметров и сравнение")
    print("  со случайными геномами — их подбор не видел по построению.")


# --------------------------------------------------------------------- отчёт

def main():
    t_start = time.time()
    sys.stdout = Tee("bots_honest_out.txt")
    print("ЧЕСТНАЯ ПЕРЕПРОВЕРКА 5 ФИНАЛЬНЫХ БОТОВ — "
          + time.strftime("%Y-%m-%d %H:%M"))
    print("движок evolution2.run5 (комиссии/слиппедж/фандинг/ликвидации "
          "внутри), 15m, 1150 дней")

    pct5 = xd.fetch_daily_pct5()
    syms = [s for s, m in config.SYMBOL_PARAMS.items() if m.get("final")]
    e2.BARS_PER_DAY = 96          # все финальные боты на 15m

    prep = {}
    for sym in syms:
        p = config.SYMBOL_PARAMS[sym]["final"]
        g = e7.cfg_to_genome(p, "final")
        for k, v in e8.OFF8.items():
            g.setdefault(k, v)
        candles = ev.fetch(sym, "15", DAYS)
        aux = e8.make_aux_builder(pct5, 96)(sym, candles)
        n = len(candles)
        h = int(n * HOLD_FRAC)
        tr_c, ho_c = candles[:h], candles[h:]
        prep[sym] = dict(
            p=p, g=g, lev=p.get("lev", 5), candles=candles, aux=aux,
            n=n, hold_i=h, pre=e2.prep(candles),
            filt=e8.make_filter8(g, aux),
            train=dict(candles=tr_c, pre=e2.prep(tr_c),
                       filt=e8.make_filter8(
                           g, {k: slice_aux(v, 0, h) for k, v in aux.items()}),
                       months=(tr_c[-1][0] - tr_c[0][0]) / (30 * 86400000)),
            hold=dict(candles=ho_c, pre=e2.prep(ho_c),
                      filt=e8.make_filter8(
                          g, {k: slice_aux(v, h, n) for k, v in aux.items()}),
                      months=(ho_c[-1][0] - ho_c[0][0]) / (30 * 86400000)))

    # базовые прогоны
    for sym, d in prep.items():
        evs = []
        r = run_at(d["candles"], d["pre"], d["g"], d["filt"], d["lev"],
                   events=evs)
        months_full = ((d["candles"][-1][0] - d["candles"][0][0])
                       / (30 * 86400000))
        d["full"] = summarize(r, evs, months_full)
        d["full_tiny"] = cycle_metrics(evs, d["candles"], d["g"], months_full)
        for part in ("train", "hold"):
            s = d[part]
            evs = []
            r = run_at(s["candles"], s["pre"], d["g"], s["filt"], d["lev"],
                       events=evs)
            s["m"] = summarize(r, evs, s["months"])
            s["tiny"] = cycle_metrics(evs, s["candles"], d["g"], s["months"])
            s["curve"] = [(e["t"], e["pnl"]) for e in evs
                          if e["type"] == "close"]
            # та же кривая с обнулёнными копеечными выходами — для портфеля
            s["curve_nt"] = [(t, 0.0 if is_tiny(p) else p)
                             for t, p in s["curve"]]
        # необученный конфиг — на ОБЕИХ частях, на плече бота
        gd = e7.cfg_to_genome(DEFAULT_CFG, "final")
        for k, v in e8.OFF8.items():
            gd.setdefault(k, v)
        d["gd"] = gd
        for part, a, b in (("train", 0, d["hold_i"]),
                           ("hold", d["hold_i"], d["n"])):
            s = d[part]
            s["filt_def"] = e8.make_filter8(gd, {k: slice_aux(v, a, b)
                                                 for k, v in d["aux"].items()})
            evs = []
            r = run_at(s["candles"], s["pre"], gd, s["filt_def"],
                       d["lev"], events=evs)
            s["def_m"] = summarize(r, evs, s["months"])
            s["def_curve"] = [(e["t"], e["pnl"]) for e in evs
                              if e["type"] == "close"]
        # тот же необученный конфиг на x5 (его «родное» плечо в config.py) —
        # снимает вопрос «а он проиграл просто потому, что мы дали ему x15?»
        evs = []
        r = run_at(d["hold"]["candles"], d["hold"]["pre"], gd,
                   d["hold"]["filt_def"], 5, events=evs)
        d["def_x5"] = summarize(r, evs, d["hold"]["months"])
        # и на всей истории, чтобы видеть его настоящую цену
        evs = []
        r = run_at(d["candles"], d["pre"], gd,
                   e8.make_filter8(gd, d["aux"]), d["lev"], events=evs)
        d["def_full"] = summarize(
            r, evs, (d["candles"][-1][0] - d["candles"][0][0])
            / (30 * 86400000))

    print()
    print("=" * 108)
    print("ПЕРИОДЫ")
    for sym, d in prep.items():
        c, h = d["candles"], d["hold_i"]
        print(f"  {sym:10} {d['n']:6} свечей 15m | ОБУЧЕНИЕ "
              f"{fmt_day(c[0][0])}..{fmt_day(c[h-1][0])} "
              f"({d['train']['months']:.1f} мес) | ХОЛДОУТ "
              f"{fmt_day(c[h][0])}..{fmt_day(c[-1][0])} "
              f"({d['hold']['months']:.1f} мес)")
    print("  Что происходило на холдоуте с самими монетами (пик->дно внутри "
          "периода — глубина обвала):")
    for sym, d in prep.items():
        seg = d["hold"]["candles"]
        peak, worst = seg[0][2], 0.0
        for _, o, hi, lo, cl in seg:
            peak = max(peak, hi)
            worst = max(worst, (peak - lo) / peak)
        print(f"    {sym:10} цена {seg[0][4]:>10.4f} -> {seg[-1][4]:>10.4f} "
              f"({(seg[-1][4]/seg[0][4]-1)*100:+7.1f}%) | максимальный обвал "
              f"внутри периода {worst*100:.1f}%")

    ok1 = check_regression(prep)
    ok2 = check_causality(prep, pct5)
    check_split(prep)
    print_contamination(prep[syms[0]]["n"])
    report_bounds(prep)

    # ---------------------------------------------------- 1. обучение/холдоут
    print()
    print("=" * 108)
    print("1. ГЛАВНАЯ ТАБЛИЦА: ТЕКУЩИЕ конфиги на обучающей части и на "
          "холдоуте (каждый на СВОЁМ плече)")
    print("   итог% — фикс. маржа $5 от $20 (как в config.py); "
          "реинв.% — с реинвестом 25% капитала (как на сайте /pnl)")
    tm = prep[syms[0]]["train"]["months"]
    hm = prep[syms[0]]["hold"]["months"]
    print(f"  {'бот':10} {'пл.':>4} | "
          f"{f'ОБУЧЕНИЕ ({tm:.1f} мес, подбор ВИДЕЛ)':>50}"
          f" | {f'ХОЛДОУТ ({hm:.1f} мес)':>50}")
    print(f"  {'':10} {'':>4} | {'сд.':>5} {'WR%':>6} {'итог%':>8} "
          f"{'реинв.%':>9} {'%/мес':>7} {'DD%':>6} | {'сд.':>5} {'WR%':>6} "
          f"{'итог%':>8} {'реинв.%':>9} {'%/мес':>7} {'DD%':>6}")
    for sym, d in prep.items():
        a, b = d["train"]["m"], d["hold"]["m"]
        print(f"  {sym:10} x{d['lev']:<3} | {a['trades']:5} {a['wr']:6.1f} "
              f"{a['ret']:+8.1f} {a['comp']:+9.1f} {a['per_month']:+7.2f} "
              f"{a['dd']:6.1f} | {b['trades']:5} {b['wr']:6.1f} "
              f"{b['ret']:+8.1f} {b['comp']:+9.1f} {b['per_month']:+7.2f} "
              f"{b['dd']:6.1f}")
    for part, title in (("train", "ОБУЧЕНИЕ"), ("hold", "ХОЛДОУТ")):
        ret, dd = portfolio({s: prep[s][part]["curve"] for s in syms},
                            len(syms))
        dret, ddd = portfolio({s: prep[s][part]["def_curve"] for s in syms},
                              len(syms))
        nret, ndd = portfolio({s: prep[s][part]["curve_nt"] for s in syms},
                              len(syms))
        print(f"  ПОРТФЕЛЬ 5 ботов по $20, {title:9}: итог {ret:+7.1f}% | "
              f"просадка {dd:5.1f}%   || тот же портфель на НЕОБУЧЕННОМ "
              f"конфиге: {dret:+7.1f}% | просадка {ddd:5.1f}%")
        print(f"  {'':10} {'':9}  БЕЗ КОПЕЕЧНЫХ ВЫХОДОВ (раздел 6): "
              f"{nret:+7.1f}% | просадка {ndd:5.1f}%")

    # ------------------------------------------------------------ 2. бенчмарки
    print()
    print("=" * 108)
    print("2. БЕНЧМАРКИ НА ТОМ ЖЕ ХОЛДОУТЕ")
    print("   холд монеты — купил на старте холдоута и держал, БЕЗ плеча;")
    print("   необуч. — ручные дефолты RSI-сетки из шапки config.py "
          "(RSI 14/30, окно 400, шаг 1%, 3 колена, TP 4%, sweep 2%, зона 0.25),")
    print("   все внешние ворота выключены, один и тот же для всех монет, "
          "на ТОМ ЖЕ плече, что и бот; показан и на обучении, и на холдоуте;")
    print("   случ. — 200 случайных геномов ядра стратегии (ворота выкл) "
          "на том же плече и том же холдоуте.")
    rng = random.Random(RAND_SEED)
    print("   «!» после числа = депозит слит (balance < маржи цикла), прогон "
          "остановлен досрочно — число ниже реального дна.")
    print(f"  {'бот':10} {'пл.':>4} | {'бот обуч.':>10} {'бот холд.':>10} | "
          f"{'необуч.3.2г':>12} {'необуч.обуч.':>13} {'необуч.холд.':>13} "
          f"{'сд.':>5} {'необуч.x5':>10} | "
          f"{'холд монеты':>12} | {'случ.медиана':>13} {'p90':>7} "
          f"{'доля>0':>7} {'ранг бота':>10}")
    for sym, d in prep.items():
        s = d["hold"]
        # (а) холд монеты
        hodl = (s["candles"][-1][4] / s["candles"][0][4] - 1) * 100
        # (б) необученный конфиг (посчитан выше на обеих частях)
        d["bench_default"] = s["def_m"]["ret"]
        d["bench_default_tr"] = s["def_m"]["trades"]
        d["bench_default_dd"] = s["def_m"]["dd"]
        # (в) случайные геномы ядра
        rets = []
        for _ in range(N_RAND):
            gr = rand_core(e2.GENES, rng)
            rr = run_at(s["candles"], s["pre"], gr,
                        e8.make_filter8(gr,
                                        {k: slice_aux(v, d["hold_i"], d["n"])
                                         for k, v in d["aux"].items()}),
                        d["lev"])
            rets.append((rr["balance"] / e2.START - 1) * 100)
        d["rand"] = rets
        rank = sum(1 for x in rets if x < s["m"]["ret"]) / len(rets) * 100
        d["rand_rank"] = rank
        rn = lambda m, w: f"{m['ret']:+.1f}{'!' if m['ruined'] else ''}".rjust(w)
        print(f"  {sym:10} x{d['lev']:<3} | {d['train']['m']['ret']:+10.1f} "
              f"{s['m']['ret']:+10.1f} | {rn(d['def_full'], 12)} "
              f"{rn(d['train']['def_m'], 13)} {rn(s['def_m'], 13)} "
              f"{s['def_m']['trades']:5} {rn(d['def_x5'], 10)} | "
              f"{hodl:+12.1f} | {statistics.median(rets):+13.1f} "
              f"{pct(rets, 0.9):+7.1f} "
              f"{sum(1 for x in rets if x > 0)/len(rets)*100:6.0f}% "
              f"{rank:9.0f}%")
        d["hodl"] = hodl
    print("  «ранг бота» = сколько процентов случайных геномов бот обошёл "
          "на холдоуте (50% = не лучше монетки).")
    print("  Смысл столбцов «необуч.»: если ручной конфиг, который НИКТО не "
          "подбирал, идёт вровень или лучше —")
    print("  значит вся многоволновая эволюция на этой монете не добавила "
          "денег, а только подогнала числа.")
    print("  Читать их ОСТОРОЖНО: на 3.2 годах необученный конфиг сливает "
          "депозит на всех монетах, и его хороший")
    print("  результат на холдоуте — это свойство ИМЕННО этого куска рынка "
          "(сплошной медвежий тренд, где сетка")
    print("  в мелкий тейк идеальна), а не доказательство его качества. "
          "Но и обратное верно: если наш подобранный")
    print("  бот на этом же куске проигрывает грубой сетке — значит его "
          "тонкая настройка на этом куске бесполезна.")

    # --------------------------------------------------------- 3. устойчивость
    print()
    print("=" * 108)
    print("3. УСТОЙЧИВОСТЬ ПАРАМЕТРОВ: 30 возмущений генома +-10% по числовым "
          "генам, прогон на ХОЛДОУТЕ")
    print("   возмущаются: " + ", ".join(NUMERIC_GENES))
    print("   НЕ возмущаются (переключатели/индексы/число колен): "
          + ", ".join(DISCRETE_GENES))
    print("   плато (медиана близко к факту, разброс узкий, доля>0 высокая) = "
          "похоже на закономерность;")
    print("   шпиль (факт сильно выше медианы соседей, доля>0 низкая) = "
          "похоже на подгонку под отрезок.")
    print(f"  {'бот':10} {'факт%':>8} | {'зерно':>6} {'медиана%':>9} "
          f"{'p10%':>8} {'p90%':>8} {'доля>0':>7} {'ранг факта':>11}")
    for sym, d in prep.items():
        s = d["hold"]
        aux_h = {k: slice_aux(v, d["hold_i"], d["n"])
                 for k, v in d["aux"].items()}
        allv, allnt = [], []
        rows = []
        for seed in PERT_SEEDS:
            rg = random.Random(seed)
            vals = []
            for _ in range(N_PERT):
                gp = perturb(d["g"], e8.GENES8, rg, PERT)
                evs = []
                rr = run_at(s["candles"], s["pre"], gp,
                            e8.make_filter8(gp, aux_h), d["lev"], events=evs)
                vals.append((rr["balance"] / e2.START - 1) * 100)
                # тот же сосед, но копеечные выходы обнулены (см. раздел 6)
                allnt.append(ret_no_tiny([e["pnl"] for e in evs
                                          if e["type"] == "close"]))
            allv += vals
            rows.append((seed, vals))
        d["pert"] = allv
        d["pert_no_tiny"] = allnt
        first = True
        for seed, vals in rows:
            rank = sum(1 for x in vals if x < s["m"]["ret"]) / len(vals) * 100
            head = f"  {sym:10} {s['m']['ret']:+8.1f} |" if first else \
                   f"  {'':10} {'':8} |"
            first = False
            print(f"{head} {seed:6} {statistics.median(vals):+9.1f} "
                  f"{pct(vals, 0.1):+8.1f} {pct(vals, 0.9):+8.1f} "
                  f"{sum(1 for x in vals if x > 0)/len(vals)*100:6.0f}% "
                  f"{rank:10.0f}%")
        rank = sum(1 for x in allv if x < s["m"]["ret"]) / len(allv) * 100
        d["pert_rank"] = rank
        print(f"  {'':10} {'':8} | {'ВСЕ 90':>6} "
              f"{statistics.median(allv):+9.1f} {pct(allv, 0.1):+8.1f} "
              f"{pct(allv, 0.9):+8.1f} "
              f"{sum(1 for x in allv if x > 0)/len(allv)*100:6.0f}% "
              f"{rank:10.0f}%")
    print("  Читать так: «медиана 90» — это доход ТИПИЧНОГО соседа конфига. "
          "Именно её, а не «факт», честно")
    print("  ожидать от бота в будущем: сам конфиг стоит там, где стоит, "
          "потому что его туда поставил подбор")
    print("  на этих же данных, а завтрашний рынок сдвинет оптимум на "
          "величину порядка тех же +-10%.")
    print("  Ранг факта, близкий к 100% — прямой признак шпиля: конфиг лучше "
          "почти всех своих соседей.")

    # ------------------------------------------------------------- 4. издержки
    print()
    print("=" * 108)
    print("4. ИЗДЕРЖКИ (комиссии taker 0.055%/maker 0.02%, слиппедж 0.03%, "
          "фандинг 0.01%/8ч)")
    print("   меряется обнулением соответствующих глобалов evolution2 на "
          "время прогона (файл движка не тронут);")
    print("   вклады считаются каждый отдельно и не обязаны складываться в "
          "сумму — путь сделок меняется.")
    print(f"  {'бот':10} {'период':10} {'PnL нетто$':>11} {'PnL брутто$':>12} "
          f"{'съедено$':>10} {'доля брутто':>12} || {'комис.$':>9} "
          f"{'слиппедж$':>10} {'фандинг$':>10}")
    for sym, d in prep.items():
        for part, title in (("train", "обучение"), ("hold", "холдоут")):
            s = d[part]
            base = s["m"]["pnl_usd"]
            variants = {}
            for tag, patch in (("gross", dict(TAKER=0, MAKER=0, SLIP=0,
                                              FUND_8H=0)),
                               ("nofee", dict(TAKER=0, MAKER=0)),
                               ("noslip", dict(SLIP=0)),
                               ("nofund", dict(FUND_8H=0))):
                old = {k: getattr(e2, k) for k in patch}
                for k, v in patch.items():
                    setattr(e2, k, v)
                try:
                    rr = run_at(s["candles"], s["pre"], d["g"], s["filt"],
                                d["lev"])
                finally:
                    for k, v in old.items():
                        setattr(e2, k, v)
                variants[tag] = rr["balance"] - e2.START
            gross = variants["gross"]
            eaten = gross - base
            share = (eaten / gross * 100) if abs(gross) > 1e-9 else float("nan")
            mark = ("  <- издержки съели ВСЮ валовую прибыль"
                    if share > 100 else "")
            print(f"  {sym:10} {title:10} {base:+11.2f} {gross:+12.2f} "
                  f"{eaten:10.2f} {share:11.1f}% || "
                  f"{variants['nofee']-base:9.2f} "
                  f"{variants['noslip']-base:10.2f} "
                  f"{variants['nofund']-base:10.2f}{mark}")
            if part == "hold":
                d["cost_share"] = share
    print("  Отрицательное число в колонке = обнуление этой издержки сделало "
          "результат ХУЖЕ. Это не парадокс:")
    print("  слиппедж сдвигает цену входа, из-за чего дальше берётся другой "
          "набор сделок. Значит вклад отдельной")
    print("  издержки на таком числе сделок сравним с шумом перестановки "
          "сделок; надёжна только колонка «съедено».")

    # ---------------------------------------------------------------- 5. плечо
    print()
    print("=" * 108)
    print("5. ЛЕСТНИЦА ПЛЕЧЕЙ НА ХОЛДОУТЕ (не на полном периоде!) и "
          "рекомендация")
    print("   правило то же, что применялось раньше: наибольшее плечо с "
          "просадкой <= 20%; плюс требование итог > 0")
    print(f"  {'бот':10} " + " ".join(f"{'x'+str(l):>16}" for l in LEVS)
          + f" {'рекоменд.':>12}")
    print(f"  {'':10} " + " ".join(f"{'итог% / DD%':>16}" for l in LEVS))
    for sym, d in prep.items():
        s = d["hold"]
        ladder, cells = [], []
        for lev in LEVS:
            rr = run_at(s["candles"], s["pre"], d["g"], s["filt"], lev)
            ret = (rr["balance"] / e2.START - 1) * 100
            dd = rr["max_dd"] * 100
            ladder.append((lev, ret, dd, rr["ruined"]))
            cells.append(f"{ret:+7.1f}/{dd:5.1f}"
                         + ("!" if rr["ruined"] else " "))
        good = [x for x in ladder if x[2] <= DD_CAP and x[1] > 0 and not x[3]]
        rec = f"x{good[-1][0]}" if good else "НЕ ЗАПУСКАТЬ"
        d["rec_lev"] = rec
        d["ladder"] = ladder
        print(f"  {sym:10} " + " ".join(f"{c:>16}" for c in cells)
              + f" {rec:>12}")

    # ------------------------------------------------- 6. копеечные выходы
    print()
    print("=" * 108)
    print("6. КОПЕЕЧНЫЕ ВЫХОДЫ («в ноль») — АРТЕФАКТ ПЕРЕНОСА СТОПА В "
          "БЕЗУБЫТОК")
    print(f"   Порог: |PnL| < {TINY_FRAC*100:.0f}% маржи цикла = "
          f"${TINY_USD:.2f} при марже ${e2.MARGIN:.0f}. Это примерно половина "
          f"издержек одного цикла,")
    print("   то есть меньше порога — не деньги ни при каком способе счёта. "
          "Перенос стопа в безубыток (ген be_move=1)")
    print("   закрывает цикл у средней цены +0.15%: формально «прибыльная "
          "сделка», фактически ноль. Такие выходы")
    print("   раздувают винрейт и на графике подписаны как «+0.001$ победа».")
    print("   «безуб.%» — доля циклов, закрытых ПЕРЕНЕСЁННЫМ стопом; счёт "
          "точный (стоп входа восстановлен из окна),")
    print("   а не по косвенным признакам. «без копеек» = итог, если "
          "копеечные выходы считать нулём.")
    print(f"  {'бот':10} {'период':9} {'сд.':>6} {'WR%':>6} {'ЧЕСТНЫЙ WR%':>12} "
          f"{'копееч.%':>9} {'безуб.%':>8} | {'копейки$':>9} {'реальн.+$':>10} "
          f"{'убытки$':>9} {'ИТОГО$':>8} {'БЕЗ КОПЕЕК$':>12} "
          f"{'ср.плюс$':>9} {'ср.минус$':>10}")
    for sym, d in prep.items():
        for part, title in (("train", "обучение"), ("hold", "ХОЛДОУТ")):
            t = d[part]["tiny"]
            mark = "  <- ДЕРЖИТСЯ НА АРТЕФАКТЕ" if t["holds_on_artifact"] else ""
            print(f"  {sym:10} {title:9} {t['n']:6} {t['wr']:6.1f} "
                  f"{t['wr_honest']:12.1f} {t['tiny_share']:9.1f} "
                  f"{t['be_share']:8.1f} | {t['pnl_split']['tiny']:+9.2f} "
                  f"{t['pnl_split']['wins']:+10.2f} "
                  f"{t['pnl_split']['losses']:+9.2f} {t['pnl_usd']:+8.2f} "
                  f"{t['pnl_without_tiny']:+12.2f} {t['avg_win_real']:+9.3f} "
                  f"{t['avg_loss']:+10.3f}{mark}")
    print("  ДОЛЯ КОПЕЕК В ИТОГЕ (сколько процентов результата сделаны "
          "«нулями»; «-» = итог и так неположительный):")
    for sym, d in prep.items():
        cells = []
        for part, title in (("train", "обучение"), ("hold", "холдоут")):
            v = d[part]["tiny"]["tiny_net_share"]
            cells.append(f"{title} {('-' if v is None else f'{v:.0f}%'):>5}")
        f = d["full_tiny"]["tiny_net_share"]
        print(f"    {sym:10} " + " | ".join(cells)
              + f" | вся история {('-' if f is None else f'{f:.0f}%'):>5}"
              + ("   <- итог сделан копейками" if f is not None and f >= 50
                 else ""))
    print("  ЧЕСТНЫЙ WR = реальные плюсы против ВСЕХ убытков (копеечный плюс "
          "победой не считается).")
    print("  Асимметрия: если средний реальный плюс заметно меньше среднего "
          "убытка, бот живёт на частоте мелких")
    print("  выигрышей — а её и создаёт безубыток. У ETH ген be_move=0, и его "
          "копеечные — это мелкие тейки:")
    print("  артефакт шире одного гена, поэтому мерим ПОРОГОМ, а не признаком "
          "«вышли по безубытку».")

    # -------------------------------------------------------------- 7. вердикт
    print()
    print("=" * 108)
    print("7. ВЕРДИКТЫ ПО КАЖДОМУ БОТУ")
    print("   КРИТЕРИЙ ПОДТВЕРЖДЕНИЯ (объявлен до подсчёта, все 5 пунктов):")
    print("     (1) на холдоуте бот в плюсе;")
    print("     (2) обходит необученный конфиг на том же плече;")
    print("     (3) обходит медиану 90 своих возмущённых соседей (доля "
          "прибыльных соседей >= 60%) —")
    print("         то есть стоит на плато, а не на шпиле;")
    print("     (4) обходит медиану 200 случайных геномов ядра (ранг >= 75%);")
    print("     (5) НОВЫЙ: остаётся в плюсе на холдоуте, если копеечные выходы "
          "обнулить (проверка на артефакт).")
    verdicts = {}
    for sym, d in prep.items():
        s = d["hold"]
        c1 = s["m"]["ret"] > 0
        c2 = s["m"]["ret"] > d["bench_default"]
        med_p = statistics.median(d["pert"])
        share_p = sum(1 for x in d["pert"] if x > 0) / len(d["pert"]) * 100
        c3 = s["m"]["ret"] >= med_p and share_p >= 60
        med_r = statistics.median(d["rand"])
        c4 = d["rand_rank"] >= 75
        c5 = s["tiny"]["pnl_without_tiny"] > 0
        okn_old = sum([c1, c2, c3, c4])
        okn = okn_old + int(c5)
        status_old = ("ПОДТВЕРЖДЁН" if okn_old == 4 else
                      "ЧАСТИЧНО" if okn_old == 3 else "НЕ ПОДТВЕРЖДЁН")
        status = ("ПОДТВЕРЖДЁН" if okn == 5 else
                  "ЧАСТИЧНО" if okn == 4 else "НЕ ПОДТВЕРЖДЁН")
        verdicts[sym] = (status, okn, c1, c2, c3, c4, c5, status_old, okn_old)
        print()
        print(f"  --- {sym} x{d['lev']} : {status} ({okn}/5) "
              f"[было {status_old} ({okn_old}/4)] ---")
        print(f"      обучение {d['train']['m']['ret']:+.1f}% -> холдоут "
              f"{s['m']['ret']:+.1f}% (реинв. {s['m']['comp']:+.1f}%), "
              f"сделок {s['m']['trades']}, WR {s['m']['wr']:.1f}%, "
              f"DD {s['m']['dd']:.1f}%, ликвидаций {s['m']['liqs']}")
        print(f"      (1) плюс на холдоуте .................. "
              f"{'ДА' if c1 else 'НЕТ'}  ({s['m']['ret']:+.1f}%)")
        print(f"      (2) лучше необученного конфига ....... "
              f"{'ДА' if c2 else 'НЕТ'}  (бот {s['m']['ret']:+.1f}% против "
              f"{d['bench_default']:+.1f}%, сделок {d['bench_default_tr']})")
        print(f"      (3) плато, а не шпиль ................ "
              f"{'ДА' if c3 else 'НЕТ'}  (медиана соседей {med_p:+.1f}%, "
              f"прибыльных соседей {share_p:.0f}%, ранг факта "
              f"{d['pert_rank']:.0f}%)")
        print(f"      (4) лучше случайных геномов .......... "
              f"{'ДА' if c4 else 'НЕТ'}  (медиана случайных {med_r:+.1f}%, "
              f"ранг бота {d['rand_rank']:.0f}%)")
        print(f"      (5) держится не на копейках .......... "
              f"{'ДА' if c5 else 'НЕТ'}  (без копеечных выходов "
              f"{s['tiny']['pnl_without_tiny']:+.2f}$ = "
              f"{s['tiny']['ret_without_tiny']:+.1f}% против "
              f"{s['m']['ret']:+.1f}%; копеечных "
              f"{s['tiny']['tiny_share']:.0f}% циклов, честный WR "
              f"{s['tiny']['wr_honest']:.1f}% вместо {s['tiny']['wr']:.1f}%)")
        print(f"      холд монеты за тот же период {d['hodl']:+.1f}% | "
              f"издержки съели {d['cost_share']:.1f}% валовой прибыли | "
              f"плечо по холдоуту: {d['rec_lev']}")
        if s["m"]["trades"] < 30:
            print(f"      ВНИМАНИЕ: всего {s['m']['trades']} сделок на "
                  f"холдоуте — на такой выборке ЛЮБОЙ вывод (и плюс, и минус) "
                  f"статистически пуст.")
        d["med_pert"] = med_p

    print()
    print("=" * 108)
    print("ИТОГО")
    n_ok = sum(1 for v in verdicts.values() if v[0] == "ПОДТВЕРЖДЁН")
    n_part = sum(1 for v in verdicts.values() if v[0] == "ЧАСТИЧНО")
    print(f"  подтверждено {n_ok} из {len(verdicts)}, частично {n_part}, "
          f"не подтверждено {len(verdicts)-n_ok-n_part}")
    print()
    print(f"  {'бот':10} {'вердикт':16} {'САЙТ /pnl':>10} {'config.py':>10} "
          f"{'обучение':>9} {'ХОЛДОУТ':>9} {'ЧЕСТНАЯ ОЦЕНКА':>15} {'/мес':>7} "
          f"{'плечо':>13}")
    print(f"  {'':10} {'':16} {'3.2г реинв':>10} {'3.2г фикс':>10} "
          f"{f'{tm:.1f}м, %':>9} {f'{hm:.1f}м, %':>9} "
          f"{'медиана сосед.':>15} {'%':>7} {'по холдоуту':>13}")
    for sym, v in verdicts.items():
        d = prep[sym]
        print(f"  {sym:10} {v[0]:16} {d['full']['comp']:+10.1f} "
              f"{DOC[sym][1]:+10.1f} "
              f"{d['train']['m']['ret']:+9.1f} {d['hold']['m']['ret']:+9.1f} "
              f"{d['med_pert']:+15.1f} "
              f"{d['med_pert']/d['hold']['months']:+7.2f} "
              f"{d['rec_lev']:>13}")
    tot_hold = sum(d["hold"]["m"]["ret"] for d in prep.values()) / len(prep)
    tot_med = sum(d["med_pert"] for d in prep.values()) / len(prep)
    print(f"  {'СРЕДНЕЕ':10} {'':16} {'':10} {'':10} {'':9} {tot_hold:+9.1f} "
          f"{tot_med:+15.1f} {tot_med/prep[syms[0]]['hold']['months']:+7.2f}")
    print("  «САЙТ /pnl» — те самые цифры вида +301%/+240%: это тот же прогон "
          "с реинвестом 25% капитала.")
    print("  Столбцы слева направо — это и есть ответ на вопрос «что от ботов "
          "остаётся»: чем правее, тем честнее.")
    print()
    print("  «ЧЕСТНАЯ ОЦЕНКА» = медиана 90 соседних конфигов на холдоуте. "
          "Это то, чего разумно ждать от бота,")
    print("  когда рынок сдвинет оптимум: сам конфиг стоит в точке, "
          "выбранной подбором ПО ЭТИМ ЖЕ данным.")

    # ------------------------------------- пересмотр вердиктов и оценок
    print()
    print("=" * 108)
    print("ПЕРЕСМОТР С УЧЁТОМ КОПЕЕЧНЫХ ВЫХОДОВ: было -> стало")
    print("  «честная оценка» пересчитана дважды: как раньше (медиана 90 "
          "соседей) и КОНСЕРВАТИВНО — та же медиана,")
    print("  но у каждого соседа копеечные выходы обнулены. Решения "
          "принимаются по второй колонке.")
    print(f"  {'бот':10} {'вердикт БЫЛО':>18} {'вердикт СТАЛО':>18} | "
          f"{'холдоут%':>9} {'холдоут БЕЗ КОПЕЕК%':>20} | "
          f"{'честно (было)%':>15} {'честно БЕЗ КОПЕЕК%':>19} {'/мес':>7} | "
          f"{'копееч.%':>9}")
    for sym, v in verdicts.items():
        d = prep[sym]
        t = d["hold"]["tiny"]
        nt = statistics.median(d["pert_no_tiny"])
        print(f"  {sym:10} {v[7] + ' ' + str(v[8]) + '/4':>18} "
              f"{v[0] + ' ' + str(v[1]) + '/5':>18} | "
              f"{d['hold']['m']['ret']:+9.1f} {t['ret_without_tiny']:+20.1f} | "
              f"{d['med_pert']:+15.1f} {nt:+19.1f} "
              f"{nt/d['hold']['months']:+7.2f} | {t['tiny_share']:9.1f}")
    avg_nt = sum(statistics.median(prep[s]["pert_no_tiny"])
                 for s in syms) / len(syms)
    print(f"  {'СРЕДНЕЕ':10} {'':18} {'':18} | {'':9} {'':20} | "
          f"{tot_med:+15.1f} {avg_nt:+19.1f} "
          f"{avg_nt/prep[syms[0]]['hold']['months']:+7.2f}")
    print("  Бот, у которого «холдоут БЕЗ КОПЕЕК» отрицателен, зарабатывал "
          "не торговлей, а безубытком: его плюс")
    print("  сложен из выходов размером в сотые доли доллара, которые "
          "исчезают при любых реальных издержках.")
    print(f"  самопроверки: регрессия {'OK' if ok1 else 'ПРОВАЛ'}, "
          f"причинность {'OK' if ok2 else 'ПРОВАЛ'}")
    print(f"  (время работы {time.time() - t_start:.1f} c)")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
