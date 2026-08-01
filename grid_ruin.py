# -*- coding: utf-8 -*-
"""ХВОСТОВОЙ РИСК СЕТКИ УСРЕДНЕНИЯ: чем платят за красивый винрейт.

Сетка даёт много мелких плюсов и редкие большие минусы. Владелец собирается
торговать на реальные деньги, поэтому здесь считается ТОЛЬКО риск: до какого
падения доживает сетка, с какой вероятностью она сливает депозит за год,
какое плечо безопасно, сколько нужно капитала и за сколько месяцев умирает
убыточная конфигурация. Доходность приводится лишь как контекст.

МОДЕЛЬ КАПИТАЛА (как в config.py: MARGIN_MODE="percent",
MARGIN_FRACTION=0.25): маржа одного цикла = 25% капитала. PnL цикла движка
ЛИНЕЕН по марже (объёмы q пропорциональны m_k, ликвидация тоже:
pnl = -mused*MM - fees), поэтому R = pnl/маржа — инвариант к размеру счёта,
а капитал растёт мультипликативно: eq *= (1 + 0.25*R). Это ровно та же
формула, что в bots_honest.compound_pct (eq *= 1 + pnl/$20 при марже $5).

ОПРЕДЕЛЕНИЯ РИСКА:
  «слив»    — капитал упал ниже 20% от старта (при фикс. доле 25% счёт не
              обнуляется математически, но -80% при 25%-й марже — это уже
              нерабочий счёт: минимальный ордер биржи перестаёт помещаться,
              см. раздел 5);
  «-50%»    — половина капитала (практический стоп-аут);
  просадка  — по мультипликативной кривой капитала (сопоставимо с холдом).

ВАЖНАЯ МЕТОДИЧЕСКАЯ ДЕТАЛЬ. evolution2.run5 считает счёт $20 с ФИКСИРОВАННОЙ
маржой $5 и обрывает прогон, как только баланс упал ниже маржи (ruined). На
высоком плече это обрезает выборку циклов ровно на катастрофе и завышает
частоту катастроф в бутстрапе. Но последовательность входов и БАРЫ выходов от
плеча не зависят вообще (вход — RSI/зона/нож; выход — стоп/тейк/таймаут по
ценам; ликвидация срабатывает в том же баре, что и стоп) — это проверяется в
самопроверке 0.3. Поэтому полный, необрезанный набор циклов на любом плече
восстанавливается симулятором sim_cycle по входам прогона на x1. Так и
сделано: все распределения считаются на ПОЛНЫХ наборах, что соответствует
процентной марже, при которой счёт не «упирается в $5».

ЧТО ДЕЛАЕТ СКРИПТ
  0. Самопроверки: симулятор цикла == движок; скользящий минимум вперёд ==
     брутфорс; входы не зависят от плеча.
  1. Худшие безоткатные движения на истории 5 монет (1150 дней, 15m):
     структурная граница «до какого падения доживает сетка», сравнение
     расстояния до стопа с расстоянием до ликвидации на фактических входах
     и судьба сетки, вошедшей ровно на вершине худшего обвала.
  2. Распределение фактических циклов: хвост, ликвидации.
  3. Бутстрап 2000 траекторий по году: IID-перемешивание (разрушает
     автокорреляцию) И БЛОЧНЫЙ бутстрап блоками 10..20 циклов (сохраняет
     серии убытков — честнее). Три набора циклов: необученная сетка на всей
     истории, боевой конфиг на всей истории (подбор это ВИДЕЛ) и боевой
     конфиг только на холдоуте.
  4. Безопасное плечо: P(слив за год) < 5% И просадка p95 < 30%.
  5. Требуемый стартовый капитал: минимальный ордер Bybit (с биржи, кэш в
     instr_info.json) плюс запас на худшую просадку.
  6. Время до разорения: бутстрап на 5 лет + фактические траектории.
  7. Сетка против «купил и держал» по просадке на тех же периодах.
  8. Стресс исполнения: движок закрывает по стопу РОВНО по цене стопа;
     пересчёт тех же циклов с проскальзыванием выхода 0.3% и 1.0%.
  9. Пять ботов сразу: сколько маржи занято одновременно.

Никакого lookahead: поиск худших эпизодов (1.1) — описание истории, а не
правило входа; вход в стресс-сценарии ставится ВРУЧНУЮ на вершину и так и
подписан. Прогоны стратегии — обычный evolution2.run5, причинность которого
проверена в bots_honest.py.

Запуск: python grid_ruin.py   (вывод дублируется в grid_ruin_out.txt)
"""

import json
import math
import os
import random
import statistics
import sys
import time
from collections import deque

import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

DAYS = 1150
HOLD_FRAC = 0.72                       # тот же холдоут, что в bots_honest.py
SYMS = ["DOGEUSDT", "LTCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
LEVS = [2, 3, 5, 8, 10, 15]
FRAC = config.MARGIN_FRACTION          # 0.25 — доля капитала на цикл
RUIN = 0.20                            # «слив» = ниже 20% старта
HALF = 0.50
NBOOT = 2000
NBOOT_LONG = 1000                      # бутстрап на 5 лет (раздел 6)
BLOCK = (10, 20)
SEED = 20260731
YEAR_MS = 365 * 86400 * 1000
MONTH_MS = 30 * 86400 * 1000

TAG_DEF = "НЕОБУЧЕННАЯ СЕТКА, вся история (3.15 г)"
TAG_BOT = "БОЕВОЙ КОНФИГ БОТА, вся история (подбор ЭТО ВИДЕЛ — оптимистично)"
TAG_BOTH = "БОЕВОЙ КОНФИГ БОТА, только холдоут (10.7 мес)"

# Необученный «разумный» конфиг — ручные дефолты из шапки config.py.
# Тот же, что в bots_honest.py (там он на холдоуте обогнал 3 из 5 ботов).
DEFAULT_CFG = dict(rsi_period=config.RSI_PERIOD, rsi_os=config.RSI_OS,
                   zone_l=config.ZONE, zone_s=config.ZONE,
                   window=config.RANGE_WINDOW, step=config.GRID_STEP,
                   levels=config.GRID_LEVELS, mult=config.GRID_MULT,
                   tp=config.TP_PCT, sweep=config.SWEEP_BUF,
                   max_bars=config.MAX_BARS, cooldown=0, knife=0.0)


class Tee:
    def __init__(self, path):
        self.out = sys.stdout
        self.fh = open(path, "w", encoding="utf-8")

    def write(self, s):
        self.out.write(s)
        self.fh.write(s)

    def flush(self):
        self.out.flush()
        self.fh.flush()


def fmt_day(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def pct(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    x = q * (len(s) - 1)
    i = int(x)
    return s[i] + (s[min(i + 1, len(s) - 1)] - s[i]) * (x - i)


def slice_aux(v, a, b):
    """Срез aux под candles[a:b] (как в bots_honest.py)."""
    if isinstance(v, tuple):
        return tuple(slice_aux(x, a, b) for x in v)
    if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
        return [slice_aux(x, a, b) for x in v]
    return v[a:b]


def core_genome(cfg):
    g = e7.cfg_to_genome(cfg, "final")
    for k, v in e8.OFF8.items():
        g.setdefault(k, v)
    return g


def run_at(candles, pre, g, filt, lev, events=None):
    old = e2.LEV
    e2.LEV = lev
    try:
        return e2.run5(candles, pre, g, entry_filter=filt, events=events)
    finally:
        e2.LEV = old


# ------------------------------------------------- 0. симулятор одного цикла

def sim_cycle(candles, g, i0, side, lev, ext, slip_exit=None):
    """Один цикл сетки по механике e2.run5, вход принудительно на баре i0.

    Копия блока сопровождения позиции из run5 (доливки до стопа, ликвидация,
    безубыток, таймаут, funding, комиссии); эквивалентность движку
    проверяется в selfcheck_sim() на всех фактических циклах.
    ext = (rlow, rhigh, rsi) — предпосчитанные ряды под геном g.
    slip_exit — отдельное проскальзывание ВЫХОДА (стресс раздела 8);
    None = как в движке. Цена ВХОДА всегда по e2.SLIP, поэтому набор циклов
    при стрессе не меняется — меняются только цены исполнения выходов.
    """
    rlow, rhigh, rsi = ext
    M = e2.MARGIN
    se = e2.SLIP if slip_exit is None else slip_exit
    weights = [g["mult"] ** k for k in range(g["levels"])]
    m_k = [M * w / sum(weights) for w in weights]
    sgn = 1 if side == "L" else -1
    c0 = candles[i0][4]
    px = c0 * (1 + sgn * e2.SLIP)
    q0 = m_k[0] * lev / px
    stop = (rlow[i0] * (1 - g["sweep"]) if side == "L"
            else rhigh[i0] * (1 + g["sweep"]))
    adds, ap = [], px
    for k in range(1, g["levels"]):
        ap = ap * (1 - sgn * g["step"])
        adds.append((ap, m_k[k] * lev / ap))
    pos = dict(fills=[(px, q0)], stop=stop, adds=adds,
               fees=q0 * px * e2.TAKER, be_done=False)
    stop0 = stop
    worst = 0.0                       # худший ход цены против позиции, доли

    def close(exit_price, i, taker_exit, liq=False, reason="?"):
        q = sum(f[1] for f in pos["fills"])
        avg = sum(fp * fq for fp, fq in pos["fills"]) / q
        if liq:
            mused = sum(m_k[k] for k in range(len(pos["fills"])))
            pnl = -mused * e2.MM - pos["fees"]
        else:
            p = exit_price * (1 - sgn * se) if taker_exit else exit_price
            fee = q * p * (e2.TAKER if taker_exit else e2.MAKER)
            pnl = sgn * (p - avg) * q - fee - pos["fees"]
        return dict(R=pnl / M, pnl=pnl, bars=i - i0, reason=reason, liq=liq,
                    fills=len(pos["fills"]), worst=worst, side=side,
                    stop_dist=abs(px - stop0) / px, t=candles[i][0])

    for i in range(i0 + 1, len(candles)):
        ts, o, h, l, c = candles[i]
        q_pre = sum(f[1] for f in pos["fills"])
        avg_pre = sum(fp * fq for fp, fq in pos["fills"]) / q_pre
        worst = max(worst, (px - l) / px if sgn == 1 else (h - px) / px)
        pos["fees"] += q_pre * c * e2.FUND_8H / e2.BARS_8H
        stop = pos["stop"]
        tp_pre = avg_pre * (1 + sgn * g["tp"])
        adverse = (l <= stop) if sgn == 1 else (h >= stop)
        hit_tp = (h >= tp_pre) if sgn == 1 else (l <= tp_pre)
        if adverse:
            while pos["adds"]:
                a_p, a_q = pos["adds"][0]
                reachable = (l <= a_p) if sgn == 1 else (h >= a_p)
                above_stop = (a_p > stop) if sgn == 1 else (a_p < stop)
                if reachable and above_stop:
                    pos["fees"] += a_q * a_p * e2.MAKER
                    pos["fills"].append((a_p, a_q))
                    pos["adds"].pop(0)
                else:
                    break
            q = sum(f[1] for f in pos["fills"])
            avg = sum(fp * fq for fp, fq in pos["fills"]) / q
            mused = sum(m_k[k] for k in range(len(pos["fills"])))
            p_liq = avg - sgn * (mused * e2.MM) / q
            liq_first = (p_liq >= stop) if sgn == 1 else (p_liq <= stop)
            liq_hit = (l <= p_liq) if sgn == 1 else (h >= p_liq)
            if liq_first and liq_hit:
                return close(p_liq, i, True, liq=True, reason="liq")
            return close(stop, i, True, reason="stop")
        if hit_tp:
            return close(tp_pre, i, False, reason="tp")
        while pos["adds"]:
            a_p, a_q = pos["adds"][0]
            if (l <= a_p) if sgn == 1 else (h >= a_p):
                pos["fees"] += a_q * a_p * e2.MAKER
                pos["fills"].append((a_p, a_q))
                pos["adds"].pop(0)
            else:
                break
        if g["be_move"] and not pos["be_done"]:
            q = sum(f[1] for f in pos["fills"])
            avg = sum(fp * fq for fp, fq in pos["fills"]) / q
            trig = avg * (1 + sgn * g["tp"] * 0.5)
            if (h >= trig) if sgn == 1 else (l <= trig):
                be = avg * (1 + sgn * 0.0015)
                better = (be > pos["stop"]) if sgn == 1 else (be < pos["stop"])
                if better:
                    pos["stop"] = be
                pos["be_done"] = True
        if i - i0 > g["max_bars"]:
            r = rsi[i]
            if r is not None and (r >= 50 if sgn == 1 else r <= 50):
                return close(c, i, True, reason="timeout")
    return close(candles[-1][4], len(candles) - 1, True, reason="end")


def make_ext(candles, pre, g):
    rlow, rhigh = ev.rolling_extremes(candles, g["window"])
    return (rlow, rhigh, pre["rsi"][e2.RSI_SET[g["rsi_idx"]]])


def entries_of(candles, pre, g, filt, lev):
    """Индексы и стороны фактических входов прогона + PnL цикла движка."""
    evs = []
    run_at(candles, pre, g, filt, lev, events=evs)
    idx = {c[0]: i for i, c in enumerate(candles)}
    out, cur = [], None
    for e in evs:
        if e["type"] == "entry":
            cur = e
        elif e["type"] == "close" and cur:
            out.append((idx[cur["t"]], cur["side"], e["pnl"]))
            cur = None
    return out


def cycles_at(candles, g, ext, lev, ents, slip_exit=None):
    """Полный (необрезанный) набор циклов на плече lev по входам с x1."""
    return [sim_cycle(candles, g, i0, side, lev, ext, slip_exit)
            for i0, side, _ in ents]


def selfcheck_sim(candles, pre, g, ext, lev):
    bad, worst = 0, 0.0
    pairs = entries_of(candles, pre, g, None, lev)
    for i0, side, pnl in pairs:
        d = abs(sim_cycle(candles, g, i0, side, lev, ext)["pnl"] - pnl)
        worst = max(worst, d)
        if d > 5e-4:                    # pnl в events округлён до 4 знаков
            bad += 1
    return len(pairs), bad, worst


def selfcheck_lev(candles, pre, g, base):
    """Входы не зависят от плеча: список входов на плече L обязан быть
    ПРЕФИКСОМ списка на x1 (различие — только обрыв прогона на сливе)."""
    ok = True
    got = []
    for lev in (2, 5, 10, 15, 20):
        e = [(i, s) for i, s, _ in entries_of(candles, pre, g, None, lev)]
        same = e == [(i, s) for i, s, _ in base[:len(e)]]
        ok = ok and same
        got.append((lev, len(e), same))
    return ok, got


# ------------------------------------------- 1. худшие безоткатные движения

def fwd_min(lows, H):
    """out[i] = min(lows[i+1..i+H]). Скользящий минимум справа налево: дек
    хранит индексы по возрастанию слева направо, значения убывают слева
    направо, минимум окна — правый конец (он же первым устаревает)."""
    n = len(lows)
    out = [None] * n
    dq = deque()
    for i in range(n - 2, -1, -1):
        j = i + 1
        while dq and lows[dq[0]] >= lows[j]:
            dq.popleft()
        dq.appendleft(j)
        while dq[-1] > i + H:
            dq.pop()
        out[i] = lows[dq[-1]]
    return out


def check_fwd_min():
    rng = random.Random(7)
    for _ in range(300):
        n, H = rng.randint(3, 40), rng.randint(1, 10)
        a = [rng.randint(0, 20) for _ in range(n)]
        got = fwd_min(a, H)
        for i in range(n - 1):
            if got[i] != min(a[i + 1:i + 1 + H]):
                return False
    return True


def worst_episodes(candles, H, top=3):
    """Топ непересекающихся падений close[i] -> минимум за следующие H баров."""
    closes = [c[4] for c in candles]
    fm = fwd_min([c[3] for c in candles], H)
    drops = sorted((fm[i] / closes[i] - 1, i) for i in range(len(candles) - 1))
    picked = []
    for d, i in drops:
        if all(abs(i - j) > H for _, j in picked):
            picked.append((d, i))
            if len(picked) >= top:
                break
    return picked


def liq_drop(lev, levels, step, mult):
    """Структурная граница лонговой сетки БЕЗ стопа: падение от цены входа
    до ЛИКВИДАЦИИ (формула ликвидации движка). -> (падение%, залитых колен)."""
    M = e2.MARGIN
    w = [mult ** k for k in range(levels)]
    m_k = [M * x / sum(w) for x in w]
    p0 = 100.0
    fills = [(p0, m_k[0] * lev / p0)]
    for j in range(1, levels + 1):
        q = sum(f[1] for f in fills)
        avg = sum(a * b for a, b in fills) / q
        mused = sum(m_k[k] for k in range(len(fills)))
        p_liq = avg - (mused * e2.MM) / q
        nxt = p0 * (1 - step) ** j if j < levels else -1e9
        if p_liq > nxt:
            return (p_liq / p0 - 1) * 100, len(fills)
        fills.append((nxt, m_k[j] * lev / nxt))
    raise AssertionError("недостижимо")


# ---------------------------------------------------------- 2-3. бутстрап

def equity_path(rs):
    eq, peak, dd = 1.0, 1.0, 0.0
    curve = [1.0]
    for r in rs:
        eq *= (1 + FRAC * r)
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
        curve.append(eq)
    return eq, dd, curve


def bootstrap(rs, n_path, rng, block=None, nboot=NBOOT):
    n = len(rs)
    if n < 10 or n_path < 1:
        return None
    ruin, half, finals, dds, tt = 0, 0, [], [], []
    for _ in range(nboot):
        if block is None:
            seq = [rs[rng.randrange(n)] for _ in range(n_path)]
        else:
            seq = []
            while len(seq) < n_path:
                L = rng.randint(block[0], block[1])
                s = rng.randrange(n)
                seq.extend(rs[(s + j) % n] for j in range(L))
            seq = seq[:n_path]
        eq, peak, dd = 1.0, 1.0, 0.0
        hit_r = hit_h = None
        for k, r in enumerate(seq):
            eq *= (1 + FRAC * r)
            if eq > peak:
                peak = eq
            d = (peak - eq) / peak
            if d > dd:
                dd = d
            if hit_h is None and eq <= HALF:
                hit_h = k + 1
            if hit_r is None and eq <= RUIN:
                hit_r = k + 1
                break                   # дальше траектория бессмысленна
        finals.append(eq)
        dds.append(dd)
        if hit_r:
            ruin += 1
            tt.append(hit_r)
        if hit_h:
            half += 1
    return dict(ruin=ruin / nboot * 100, half=half / nboot * 100,
                p5=pct(finals, 0.05), p50=pct(finals, 0.50),
                p95=pct(finals, 0.95), dd95=pct(dds, 0.95) * 100,
                dd50=pct(dds, 0.50) * 100,
                tt=(statistics.median(tt) if tt else None))


# ---------------------------------------------------- 5. минимальный ордер

def instrument_info():
    path = "instr_info.json"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    from pybit.unified_trading import HTTP
    s = HTTP(testnet=False)
    out = {}
    for sym in SYMS:
        r = s.get_instruments_info(category="linear", symbol=sym)
        x = r["result"]["list"][0]
        out[sym] = dict(minQty=float(x["lotSizeFilter"]["minOrderQty"]),
                        qtyStep=float(x["lotSizeFilter"]["qtyStep"]),
                        minVal=float(x["lotSizeFilter"].get(
                            "minNotionalValue", 5)),
                        maxLev=float(x["leverageFilter"]["maxLeverage"]))
        time.sleep(0.15)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    return out


def main():
    t_start = time.time()
    sys.stdout = Tee("grid_ruin_out.txt")
    print("ХВОСТОВОЙ РИСК СЕТКИ УСРЕДНЕНИЯ — " + time.strftime("%Y-%m-%d %H:%M"))
    print("движок evolution2.run5 (комиссии taker 0.055%/maker 0.02%, "
          "слиппедж 0.03%, фандинг 0.01%/8ч, ликвидации), 15m, 1150 дней")
    print(f"модель капитала: маржа цикла = {FRAC*100:.0f}% капитала "
          f"(config.MARGIN_FRACTION), eq *= (1 + {FRAC}*R), R = pnl/маржа")
    print(f"«слив» = капитал ниже {RUIN*100:.0f}% от старта; "
          f"«-50%» = ниже {HALF*100:.0f}% (оба порога — по кривой капитала)")

    e2.BARS_PER_DAY = 96
    pct5 = xd.fetch_daily_pct5()
    aux_builder = e8.make_aux_builder(pct5, 96)

    D = {}
    for sym in SYMS:
        candles = ev.fetch(sym, "15", DAYS)
        pre = e2.prep(candles)
        aux = aux_builder(sym, candles)
        gd = core_genome(DEFAULT_CFG)
        gb = core_genome(config.SYMBOL_PARAMS[sym]["final"])
        n = len(candles)
        h = int(n * HOLD_FRAC)
        ho, ho_pre = candles[h:], e2.prep(candles[h:])
        ho_filt = e8.make_filter8(gb, {k: slice_aux(v, h, n)
                                       for k, v in aux.items()})
        d = dict(candles=candles, pre=pre, g_def=gd, g_bot=gb,
                 filt_bot=e8.make_filter8(gb, aux),
                 lev_bot=config.SYMBOL_PARAMS[sym]["final"].get("lev", 5),
                 ho_candles=ho, ho_pre=ho_pre, ho_filt=ho_filt,
                 ext_def=make_ext(candles, pre, gd),
                 ext_bot=make_ext(candles, pre, gb),
                 ext_both=make_ext(ho, ho_pre, gb),
                 years=(candles[-1][0] - candles[0][0]) / YEAR_MS,
                 ho_years=(ho[-1][0] - ho[0][0]) / YEAR_MS)
        d["ent_def"] = entries_of(candles, pre, gd, None, 1)
        d["ent_bot"] = entries_of(candles, pre, gb, d["filt_bot"], 1)
        d["ent_both"] = entries_of(ho, ho_pre, gb, ho_filt, 1)
        D[sym] = d
    c0 = D[SYMS[0]]
    print(f"\nпериод: {fmt_day(c0['candles'][0][0])}.."
          f"{fmt_day(c0['candles'][-1][0])} ({c0['years']:.2f} года); "
          f"холдоут {fmt_day(c0['ho_candles'][0][0])}.."
          f"{fmt_day(c0['ho_candles'][-1][0])} "
          f"({c0['ho_years']*12:.1f} мес)")

    # ------------------------------------------------------- 0. самопроверки
    print()
    print("=" * 112)
    print("0. САМОПРОВЕРКИ")
    print("  0.1 симулятор цикла против движка (все фактические циклы "
          "необученной сетки, x5):")
    ok = True
    for sym in SYMS:
        d = D[sym]
        n, bad, worst = selfcheck_sim(d["candles"], d["pre"], d["g_def"],
                                      d["ext_def"], 5)
        ok = ok and bad == 0
        print(f"      {sym:10} циклов {n:5} | расхождений {bad:3} | "
              f"макс. |dPnL| {worst:.2e} $  {'OK' if bad == 0 else 'ПЛОХО'}")
    print(f"      -> симулятор {'эквивалентен' if ok else 'НЕ эквивалентен'} "
          f"движку")
    print(f"  0.2 скользящий минимум вперёд против брутфорса (300 случайных "
          f"массивов): {'OK' if check_fwd_min() else 'ПЛОХО'}")
    print("  0.3 входы НЕ зависят от плеча (список входов на плече L — "
          "префикс списка на x1; разница только обрыв прогона на сливе):")
    okl = True
    for sym in SYMS:
        d = D[sym]
        good, got = selfcheck_lev(d["candles"], d["pre"], d["g_def"],
                                  d["ent_def"])
        okl = okl and good
        print(f"      {sym:10} x1: {len(d['ent_def']):4} циклов | "
              + "  ".join(f"x{l}: {n:4} {'OK' if s else 'РАСХОЖД.'}"
                          for l, n, s in got))
    print(f"      -> {'подтверждено' if okl else 'НЕ подтверждено'}: полный "
          f"набор циклов на любом плече восстанавливается по входам с x1")

    # ---------------------------------------- 1. худшие исторические обвалы
    print()
    print("=" * 112)
    print("1.1 ХУДШИЕ БЕЗОТКАТНЫЕ ПАДЕНИЯ НА ИСТОРИИ (close -> минимум за "
          "следующие H баров), топ-3 непересекающихся эпизода")
    print("    это ОПИСАНИЕ ИСТОРИИ, а не правило входа: цифры нужны как "
          "стресс-сценарий для лонговой сетки")
    print(f"  {'монета':10} {'горизонт':>9} | {'дата':>10} {'падение%':>9} | "
          f"{'дата':>10} {'падение%':>9} | {'дата':>10} {'падение%':>9}")
    EPI = {}
    for sym in SYMS:
        d = D[sym]
        for H, name in ((96, "1 день"), (288, "3 дня"), (672, "7 дней"),
                        (2880, "30 дней")):
            eps = worst_episodes(d["candles"], H, top=3)
            if H == 288:
                EPI[sym] = eps
            cells = "".join(f" | {fmt_day(d['candles'][i][0]):>10} "
                            f"{dr*100:>9.1f}" for dr, i in eps)
            print(f"  {sym:10} {name:>9}{cells}")
        print()

    print("1.2 СТРУКТУРНАЯ ГРАНИЦА: падение от цены входа до ЛИКВИДАЦИИ "
          "лонговой сетки, стоп отключён (шаг 1%, множитель 1.5)")
    print("    в скобках — сколько колен успевает залиться до ликвидации")
    print(f"  {'колен':>6} | " + " | ".join(f"{'x'+str(l):>13}"
                                            for l in (3, 5, 10, 15, 20)))
    for levels in (1, 2, 3, 4):
        cells = []
        for lev in (3, 5, 10, 15, 20):
            dr, nf = liq_drop(lev, levels, 0.01, 1.5)
            cells.append(f"{dr:8.1f}% ({nf})")
        print(f"  {levels:>6} | " + " | ".join(cells))
    print("    колена почти НЕ отодвигают ликвидацию (шаг сетки в разы меньше "
          "расстояния до неё): её задаёт ПЛЕЧО, а колена лишь увеличивают "
          "занятую маржу")
    print("    боевые конфиги ботов (их собственные шаг/множитель/колена):")
    for sym in SYMS:
        g = D[sym]["g_bot"]
        cells = [f"x{lev}: {liq_drop(lev, g['levels'], g['step'], g['mult'])[0]:6.1f}%"
                 for lev in (3, 5, 10, 15, 20)]
        print(f"      {sym:10} колен {g['levels']} шаг {g['step']*100:.2f}% "
              f"множ {g['mult']:.2f} | " + "  ".join(cells))

    print()
    print("1.3 ЧТО СПАСАЕТ СЕТКУ: расстояние до СТОПА на фактических входах "
          "против расстояния до ликвидации")
    print("    доля входов, где ЛИКВИДАЦИЯ БЛИЖЕ стопа = доля циклов, в "
          "которых стоп уже не защищает")
    print(f"  {'монета':10} | {'стоп: медиана':>13} {'p90':>7} {'макс':>7} | "
          + " | ".join(f"{'x'+str(l):>17}" for l in (5, 10, 15, 20)))
    for sym in SYMS:
        d = D[sym]
        g = d["g_def"]
        dists = [sim_cycle(d["candles"], g, i0, side, 5,
                           d["ext_def"])["stop_dist"] * 100
                 for i0, side, _ in d["ent_def"]]
        cells = []
        for lev in (5, 10, 15, 20):
            ld = abs(liq_drop(lev, g["levels"], g["step"], g["mult"])[0])
            share = sum(1 for x in dists if x > ld) / len(dists) * 100
            cells.append(f"{ld:5.1f}% -> {share:5.1f}%")
        print(f"  {sym:10} | {statistics.median(dists):12.1f}% "
              f"{pct(dists, 0.90):6.1f}% {max(dists):6.1f}% | "
              + " | ".join(f"{c:>17}" for c in cells))

    print()
    print("1.4 СТРЕСС: лонговая сетка вошла НА ВЕРШИНЕ худшего 3-дневного "
          "обвала (полная механика движка: доливки, стоп, ликвидация)")
    print("    R = pnl/маржа; «%к» = изменение капитала при марже 25%; "
          "ЛИКВ = ликвидация")
    for sym in SYMS:
        d = D[sym]
        dr, i0 = EPI[sym][0]
        print(f"  {sym:10} обвал {dr*100:.1f}% от {fmt_day(d['candles'][i0][0])}"
              f" (цена входа {d['candles'][i0][4]:.4f})")
        for levels in (1, 2, 3, 4):
            g = dict(d["g_def"])
            g["levels"] = levels
            ext = d["ext_def"]          # окно/RSI те же, колена не влияют
            cells = []
            for lev in (3, 5, 10, 15):
                s = sim_cycle(d["candles"], g, i0, "L", lev, ext)
                tag = "ЛИКВ" if s["liq"] else s["reason"]
                cells.append(f"x{lev:<2} R{s['R']:+5.2f} "
                             f"{s['R']*FRAC*100:+6.1f}%к {tag:<7}")
            print(f"      колен {levels}: " + " | ".join(cells))

    # ------------------------------------------- 2. фактические циклы, хвост
    print()
    print("=" * 112)
    print("2. ФАКТИЧЕСКИЕ ЦИКЛЫ НА ВСЕЙ ИСТОРИИ (необученная сетка, полный "
          "набор без обрезки) — как выглядит хвост")
    print(f"  {'монета':10} {'пл.':>4} {'циклов':>7} {'WR%':>6} {'сред.R':>7} "
          f"{'p5 R':>7} {'p1 R':>7} {'мин.R':>7} {'ликв.':>6} {'стоп%':>6} "
          f"{'худший цикл':>12} {'итог кап.%':>11}")
    CYC = {}
    for sym in SYMS:
        d = D[sym]
        for lev in (3, 5, 10, 15, 20):
            cyc = cycles_at(d["candles"], d["g_def"], d["ext_def"], lev,
                            d["ent_def"])
            CYC[(sym, lev)] = cyc
            rs = [c["R"] for c in cyc]
            liq = sum(1 for c in cyc if c["liq"])
            stops = sum(1 for c in cyc if c["reason"] == "stop")
            wr = sum(1 for x in rs if x > 0) / len(rs) * 100
            eq, _, _ = equity_path(rs)
            print(f"  {sym:10} x{lev:<3} {len(rs):7} {wr:6.1f} "
                  f"{statistics.mean(rs):+7.3f} {pct(rs, 0.05):+7.2f} "
                  f"{pct(rs, 0.01):+7.2f} {min(rs):+7.2f} {liq:6} "
                  f"{stops/len(rs)*100:6.1f} {min(rs)*FRAC*100:+11.1f}% "
                  f"{(eq-1)*100:+11.1f}")
    print("  худший цикл — в % капитала при марже 25%; максимум потерь за один "
          "цикл ограничен ликвидацией: -0.96 R = -24% капитала")

    # --------------------------------------------------------- 3. бутстрап
    print()
    print("=" * 112)
    print(f"3. БУТСТРАП {NBOOT} ТРАЕКТОРИЙ ПО ГОДУ ТОРГОВЛИ на фактических "
          f"циклах (IID и блочный, блок {BLOCK[0]}..{BLOCK[1]} циклов)")
    print("   IID разрушает автокорреляцию (оптимистично), блочный сохраняет "
          "серии убытков (честнее) — смотреть на БЛОЧНЫЙ")
    print("   p5/p50/p95 — капитал в конце года в долях от старта "
          "(1.00 = остались при своих)")
    RISK = {}
    sets = [(TAG_DEF, "g_def", "ext_def", "ent_def", "candles", "years"),
            (TAG_BOT, "g_bot", "ext_bot", "ent_bot", "candles", "years"),
            (TAG_BOTH, "g_bot", "ext_both", "ent_both", "ho_candles",
             "ho_years")]
    for tag, gkey, xkey, ekey, ckey, ykey in sets:
        print(f"\n  --- {tag} ---")
        print(f"  {'монета':10} {'пл.':>4} {'цикл/год':>9} {'n':>5} | "
              f"{'IID слив%':>10} | {'БЛОК слив%':>11} {'-50%':>6} "
              f"{'p5':>6} {'p50':>6} {'p95':>6} {'DDp50%':>7} {'DDp95%':>7}")
        for sym in SYMS:
            d = D[sym]
            ents = d[ekey]
            cpy = max(3, int(round(len(ents) / d[ykey])))
            for lev in LEVS:
                cyc = cycles_at(d[ckey], d[gkey], d[xkey], lev, ents)
                rs = [c["R"] for c in cyc]
                if len(rs) < 10:
                    print(f"  {sym:10} x{lev:<3} {'':>9} {len(rs):5} | "
                          f"мало циклов — бутстрап не считаем")
                    continue
                bi = bootstrap(rs, cpy, random.Random(SEED + lev), block=None)
                bb = bootstrap(rs, cpy, random.Random(SEED + lev), block=BLOCK)
                RISK[(tag, sym, lev)] = dict(bb=bb, bi=bi, cpy=cpy, rs=rs,
                                             mean=statistics.mean(rs))
                thin = "!" if len(rs) < 4 * BLOCK[1] else " "
                print(f"  {sym:10} x{lev:<3} {cpy:9} {len(rs):5}{thin}| "
                      f"{bi['ruin']:10.1f} | {bb['ruin']:11.1f} "
                      f"{bb['half']:6.1f} {bb['p5']:6.2f} {bb['p50']:6.2f} "
                      f"{bb['p95']:6.2f} {bb['dd50']:7.1f} {bb['dd95']:7.1f}")
    print(f"\n  ! = циклов меньше {4*BLOCK[1]}: блок 10..20 сопоставим с "
          f"объёмом выборки, блочный бутстрап на таких данных почти ничего "
          f"не измеряет")
    print("  ВНИМАНИЕ: блок «боевой конфиг, вся история» — ИН-СЭМПЛ. "
          "Конфиги подбирались на этих же данных, его цифры риска ЗАНИЖЕНЫ.")
    print("  Честный ориентир для боевых конфигов — блок «только холдоут», и "
          "даже он не неприкосновенный (см. оговорку в bots_honest.py).")

    print()
    print("3b. ЗАПРОШЕННАЯ СВОДКА: МОНЕТА x ПЛЕЧО -> риск слива за год % / "
          "просадка p95 % (блочный бутстрап)")
    for tag in (TAG_DEF, TAG_BOTH):
        print(f"\n  --- {tag} ---")
        print(f"  {'монета':10} | " + " | ".join(f"{'x'+str(l):>13}"
                                                 for l in LEVS))
        for sym in SYMS:
            cells = []
            for lev in LEVS:
                b = RISK.get((tag, sym, lev))
                cells.append(f"{b['bb']['ruin']:5.1f}% /{b['bb']['dd95']:5.1f}%"
                             if b else f"{'нет данных':>13}")
            print(f"  {sym:10} | " + " | ".join(cells))

    # -------------------------------------------------- 4. безопасное плечо
    print()
    print("=" * 112)
    print("4. БЕЗОПАСНОЕ ПЛЕЧО: максимальное, при котором P(слив за год) < 5% "
          "И просадка p95 < 30% (блочный бутстрап)")
    SAFE = {}
    for tag, _, _, _, _, _ in sets:
        print(f"\n  --- {tag} ---")
        print(f"  {'монета':10} | {'безоп. плечо':>13} {'слив%':>7} "
              f"{'DDp95%':>7} {'мед.итог':>9} | причина отказа")
        for sym in SYMS:
            best = None
            for lev in LEVS:
                b = RISK.get((tag, sym, lev))
                if b and b["bb"]["ruin"] < 5.0 and b["bb"]["dd95"] < 30.0:
                    best = (lev, b["bb"])
            SAFE[(tag, sym)] = best
            if best:
                lev, b = best
                print(f"  {sym:10} | {'x'+str(lev):>13} {b['ruin']:7.1f} "
                      f"{b['dd95']:7.1f} {b['p50']:9.2f} |")
            else:
                b = RISK.get((tag, sym, LEVS[0]))
                why = (f"даже x{LEVS[0]}: слив {b['bb']['ruin']:.1f}%, "
                       f"DDp95 {b['bb']['dd95']:.1f}%" if b else "нет данных")
                print(f"  {sym:10} | {'НЕТ':>13} {'':>7} {'':>7} {'':>9} | {why}")

    # ------------------------------------------------- 5. требуемый капитал
    print()
    print("=" * 112)
    print("5. СКОЛЬКО НУЖНО КАПИТАЛА (маржа цикла = 25% капитала)")
    info = instrument_info()
    print("   минимум ордера Bybit (linear, взят с биржи): "
          + ", ".join(f"{s[:-4]} {info[s]['minQty']:g} шт/${info[s]['minVal']:g}"
                      for s in SYMS))
    print("   самое МЕЛКОЕ колено сетки обязано проходить минимум биржи; "
          "при множителе > 1 это ПЕРВОЕ колено")
    print(f"  {'монета':10} {'цена':>10} {'плечо':>6} | {'мин.ордер$':>11} "
          f"{'мин.капитал$':>13} | {'худшая ист.DD%':>15} {'DDp95%':>7} | "
          f"{'нужно $':>9} {'с запасом x2':>13}")
    for sym in SYMS:
        d = D[sym]
        px = d["candles"][-1][4]
        g = d["g_def"]
        w = [g["mult"] ** k for k in range(g["levels"])]
        share0 = w[0] / sum(w)
        need_not = max(info[sym]["minVal"], info[sym]["minQty"] * px)
        safe = SAFE[(TAG_DEF, sym)]
        lev = safe[0] if safe else LEVS[0]
        cap_min = need_not / (share0 * lev * FRAC)
        cyc = CYC.get((sym, lev)) or cycles_at(d["candles"], g, d["ext_def"],
                                               lev, d["ent_def"])
        _, dd_hist, _ = equity_path([c["R"] for c in cyc])
        k = (TAG_DEF, sym, lev)
        dd95 = RISK[k]["bb"]["dd95"] / 100 if k in RISK else dd_hist
        dd_use = max(dd_hist, dd95)
        cap_need = cap_min / max(0.05, 1 - dd_use)
        print(f"  {sym:10} {px:10.4f} {'x'+str(lev):>6} | {need_not:11.1f} "
              f"{cap_min:13.0f} | {dd_hist*100:15.1f} {dd95*100:7.1f} | "
              f"{cap_need:9.0f} {cap_need*2:13.0f}")
    print("   мин.капитал = ордер минимального колена / (доля 1-го колена x "
          "плечо x 0.25)")
    print("   «нужно» = мин.капитал / (1 - худшая просадка): после худшей "
          "просадки счёт обязан ВСЁ ЕЩЁ уметь выставить минимальный ордер,")
    print("   иначе бот молча перестанет торговать в самой нижней точке "
          "просадки и не отыграется")

    # -------------------------------------------------- 6. время до слива
    print()
    print("=" * 112)
    print(f"6. ВРЕМЯ ДО РАЗОРЕНИЯ: блочный бутстрап на 5 ЛЕТ вперёд "
          f"({NBOOT_LONG} траекторий, необученная сетка)")
    print(f"  {'монета':10} {'пл.':>4} {'сред.R':>8} {'слив за 5 лет%':>15} "
          f"{'медиана мес. до -80%':>21} {'мес. по дрейфу':>16} "
          f"{'капитал p50 ч/з 5 лет':>22}")
    for sym in SYMS:
        for lev in (3, 5, 10, 15):
            k = (TAG_DEF, sym, lev)
            if k not in RISK:
                continue
            R = RISK[k]
            cpy = R["cpy"]
            b5 = bootstrap(R["rs"], cpy * 5, random.Random(SEED + lev),
                           block=BLOCK, nboot=NBOOT_LONG)
            drift = statistics.mean([math.log(max(1e-6, 1 + FRAC * x))
                                     for x in R["rs"]])
            dtxt = (f"{(math.log(RUIN)/drift)/cpy*12:16.1f}" if drift < -1e-9
                    else f"{'дрейф >= 0':>16}")
            tt = (f"{b5['tt']/cpy*12:21.1f}" if b5["tt"] else f"{'-':>21}")
            print(f"  {sym:10} x{lev:<3} {R['mean']:+8.3f} {b5['ruin']:15.1f} "
                  f"{tt} {dtxt} {b5['p50']:22.2f}")
    print("\n  ФАКТИЧЕСКИЕ ТРАЕКТОРИИ (необученная сетка) — то, что уже "
          "случилось на истории, без всякого бутстрапа:")
    for sym in SYMS:
        d = D[sym]
        for lev in (5, 15):
            cyc = CYC[(sym, lev)]
            rs = [c["R"] for c in cyc]
            eq, dd, curve = equity_path(rs)
            t0 = d["candles"][0][0]
            marks = {}
            for c, e in zip(cyc, curve[1:]):
                marks[int((c["t"] - t0) // MONTH_MS)] = e
            line = "  ".join(f"{m:>2}м:{marks[m]*100:4.0f}%"
                             for m in sorted(marks)
                             if m % 6 == 0 or m == max(marks))
            print(f"    {sym:10} x{lev:<3} итог {eq*100:6.1f}% от старта, "
                  f"просадка {dd*100:5.1f}%, циклов {len(rs):4}, "
                  f"ликвидаций {sum(1 for c in cyc if c['liq']):3}")
            print(f"    {'':10}     " + line)

    # ------------------------------------------------------- 7. против холда
    print()
    print("=" * 112)
    print("7. СЕТКА ПРОТИВ «КУПИЛ И ДЕРЖАЛ» ПО РИСКУ (тот же период 3.15 г)")
    print(f"  {'монета':10} | {'ХОЛД 1x итог%':>14} {'DD%':>7} | "
          f"{'СЕТКА x3 итог%':>15} {'DD%':>7} | {'СЕТКА x5 итог%':>15} "
          f"{'DD%':>7} | {'СЕТКА x10 итог%':>16} {'DD%':>7}")
    for sym in SYMS:
        d = D[sym]
        cl = [c[4] for c in d["candles"]]
        peak, hdd = d["candles"][0][2], 0.0
        for c in d["candles"]:
            peak = max(peak, c[2])
            hdd = max(hdd, (peak - c[3]) / peak)
        cells = []
        for lev in (3, 5, 10):
            eq, dd, _ = equity_path([c["R"] for c in CYC[(sym, lev)]])
            cells.append(f"{(eq-1)*100:15.1f} {dd*100:7.1f}")
        print(f"  {sym:10} | {(cl[-1]/cl[0]-1)*100:14.1f} {hdd*100:7.1f} | "
              + " | ".join(cells))
    print("   холд — по внутридневным экстремумам (high->low), сетка — по "
          "кривой капитала между циклами; сравнение ЗАВЫШЕНО в пользу сетки:")
    print("   её просадка меряется только в моменты закрытия циклов, "
          "плавающий убыток открытой сетки в неё не попадает")

    # --------------------------------------------------- 8. стресс-слиппедж
    print()
    print("=" * 112)
    print("8. СТРЕСС ИСПОЛНЕНИЯ: движок закрывает по стопу РОВНО по цене "
          "стопа. В обвале так не бывает.")
    print("   Те же самые циклы (вход и бары выхода не меняются), "
          "проскальзывание ВЫХОДА 0.03% -> 0.3% -> 1.0%:")
    print(f"  {'монета':10} {'пл.':>4} | {'итог кап.% норма':>17} "
          f"{'слип 0.3%':>11} {'слип 1.0%':>11} | {'DD норма%':>10} "
          f"{'DD 0.3%':>9} {'DD 1.0%':>9}")
    for sym in SYMS:
        d = D[sym]
        for lev in (5, 10):
            res = {}
            for se in (None, 0.003, 0.01):
                rs = [c["R"] for c in cycles_at(d["candles"], d["g_def"],
                                                d["ext_def"], lev,
                                                d["ent_def"], slip_exit=se)]
                eq, dd, _ = equity_path(rs)
                res[se] = ((eq - 1) * 100, dd * 100)
            print(f"  {sym:10} x{lev:<3} | {res[None][0]:17.1f} "
                  f"{res[0.003][0]:11.1f} {res[0.01][0]:11.1f} | "
                  f"{res[None][1]:10.1f} {res[0.003][1]:9.1f} "
                  f"{res[0.01][1]:9.1f}")

    # ------------------------------------------- 9. пять ботов одновременно
    print()
    print("=" * 112)
    print("9. ПЯТЬ БОТОВ СРАЗУ: сколько маржи занято одновременно "
          "(config.MARGIN_FRACTION=0.25 на КАЖДОГО бота)")
    marks = []
    for sym in SYMS:
        d = D[sym]
        for (i0, side, _), c in zip(d["ent_def"], CYC[(sym, 5)]):
            marks.append((d["candles"][i0][0], 1))
            marks.append((c["t"], -1))
    marks.sort()
    cnt, mx, hist = 0, 0, {}
    prev_t = marks[0][0]
    for t, dlt in marks:
        hist[cnt] = hist.get(cnt, 0) + (t - prev_t)
        prev_t = t
        cnt += dlt
        mx = max(mx, cnt)
    total = sum(hist.values()) or 1
    print(f"  максимум одновременно открытых сеток: {mx} из 5")
    print("  доля времени с N открытыми сетками: " + "  ".join(
        f"{n}: {hist.get(n, 0)/total*100:.1f}%" for n in range(0, 6)))
    used = sum(hist.get(n, 0) * n for n in range(6)) / total
    print(f"  средняя занятая маржа при 25% на бота: {used*25:.0f}% капитала, "
          f"пик {mx*25}%")
    print(f"  при пике {mx*25}% > 100% биржа откажет в части ордеров: пять "
          f"ботов с MARGIN_FRACTION=0.25 каждый физически не помещаются в "
          f"счёт.")
    print("  Либо доля должна быть 0.25/5 = 0.05 (и тогда доходность делится "
          "на 5), либо часть сигналов будет молча пропускаться —")
    print("  и тогда реальная кривая не совпадёт ни с одним бэктестом.")

    # ---------------------------------------------------------- 10. вывод
    print()
    print("=" * 112)
    print("10. ВЫВОД: ПРИЕМЛЕМ ЛИ РИСК")
    print()
    print("  А. ХВОСТ ОДНОГО ЦИКЛА ограничен, и это единственная хорошая "
          "новость. Максимум потерь за цикл — ликвидация:")
    print("     -0.96 R = -24% капитала при марже 25%. Сетка НЕ обнуляет счёт "
          "одним движением: её убивает СЕРИЯ, а не один обвал.")
    print("     Причина — стоп: медиана расстояния до стопа ~3% от входа, а "
          "до ликвидации на x5 — 20%, на x10 — 10.6% (раздел 1.3),")
    print("     поэтому до x10 стоп почти всегда срабатывает раньше. "
          "Ликвидации появляются с x15 (DOGE, SOL) и учащаются на x20.")
    print()
    print("  Б. РИСК РАЗОРЕНИЯ СОЗДАЁТ НЕ ХВОСТ, А ОТРИЦАТЕЛЬНОЕ "
          "МАТОЖИДАНИЕ. У необученной сетки средний R < 0 на ВСЕХ пяти")
    print("     монетах и всех плечах (раздел 2). При 200-250 циклах в год "
          "даже -0.005 R за цикл — это сложный процент вниз:")
    for sym in SYMS:
        k = (TAG_DEF, sym, 5)
        print(f"       {sym:10} x5: сред.R {RISK[k]['mean']:+.4f}, "
              f"{RISK[k]['cpy']} циклов/год, факт за 3.15 года: капитал "
              f"{equity_path(RISK[k]['rs'])[0]*100:.1f}% от старта")
    print()
    print("  В. БЕЗОПАСНОГО ПЛЕЧА ПОЧТИ НЕТ. Порог «слив < 5% и просадка p95 "
          "< 30%» необученная сетка проходит только на x2")
    print("     и только на LTC и BTC; на DOGE/ETH/SOL — ни на одном плече. "
          "x5 уже даёт просадку p95 около 50-75%,")
    print("     x10 — вероятность слива 1-41% за ГОД, x15 — 17-75%.")
    print()
    print("  Г. ЦИФРЫ БОЕВЫХ КОНФИГОВ ВЫГЛЯДЯТ БЕЗОПАСНО, НО ИМ НЕЛЬЗЯ "
          "ВЕРИТЬ КАК ОЦЕНКЕ РИСКА. Они получены на данных,")
    print("     где сами и подбирались; на холдоуте выборка 14-318 циклов, "
          "по BTC вообще 14 — блочный бутстрап на ней бессмыслен.")
    print("     Совпадение «риск нулевой на всех плечах» — типичный признак "
          "того, что мерим подгонку, а не свойство стратегии.")
    print()
    print("  Д. ИЗДЕРЖКИ УБИВАЮТ РАНЬШЕ РЫНКА. Раздел 8: ухудшение исполнения "
          "выхода с 0.03% до 0.3% превращает -42% в -83%")
    print("     (LTC x5). При 200 циклах в год каждые 0.1 п.п. "
          "проскальзывания стоят примерно плечо x 0.1% x число циклов —")
    print("     на x5 это ~10% капитала в год. Запаса над издержками у "
          "стратегии нет вообще.")
    print()
    print("  Е. ПО РИСКУ СЕТКА НЕ ЛУЧШЕ ХОЛДА. Раздел 7: просадка сетки на x5 "
          "(58-89%) сопоставима с просадкой холда (54-86%),")
    print("     при этом холд на BTC дал +135%, а сетка -46%. Обещание "
          "«сетка сглаживает» на этих данных не подтверждается —")
    print("     и это ещё оптимистично: просадка сетки меряется только между "
          "циклами, плавающий убыток в неё не входит.")
    print()
    print("  Ж. КАПИТАЛ. Минимальный ордер биржи задаёт нижнюю границу счёта: "
          "$66 (LTC) ... $853 (BTC) на x2 с запасом на")
    print("     худшую просадку; вдвое больше — чтобы после просадки бот не "
          "перестал торговать. Для BTC на малом счёте сетка")
    print("     физически неисполнима: минимальный лот 0.001 BTC = $65 "
          "нотионала на самое мелкое колено.")
    print()
    print("  З. ПЯТЬ БОТОВ СРАЗУ НЕ ПОМЕЩАЮТСЯ: пик одновременной маржи 125% "
          "капитала (раздел 9). Либо доля 0.05 на бота,")
    print("     либо часть входов молча не исполнится — и реальная кривая не "
          "совпадёт ни с одним бэктестом.")
    print()
    print("  ИТОГ: риск НЕ приемлем в текущем виде. Не потому, что сетка "
          "взрывается — она как раз умирает медленно, серией мелких")
    print("  минусов, и это опаснее: красивый винрейт 48-53% и «маленькие» "
          "убытки маскируют отрицательное матожидание, которое")
    print("  на дистанции 200+ циклов в год гарантированно съедает счёт. "
          "Торговать этим на реальные деньги можно только если")
    print("  найдено ПОЛОЖИТЕЛЬНОЕ матожидание вне периода подбора; до тех "
          "пор любое плечо выше x2 — это выбор скорости потери,")
    print("  а не размера дохода. Ограничения этой оценки: R считались на "
          "ОДНОМ периоде в 3.15 года (два бычьих года + медвежий")
    print("  2026-й), стационарность распределения циклов не проверялась, "
          "исполнение стопа в движке оптимистично (см. раздел 8).")

    print()
    print("=" * 112)
    print(f"готово за {time.time()-t_start:.0f}с")


if __name__ == "__main__":
    main()
