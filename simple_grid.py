# -*- coding: utf-8 -*-
"""simple_grid.py — ПРОСТАЯ сеточная стратегия из того, что ВЫЖИЛО.

Собрана НЕ подбором. Три исследования волны (grid_anatomy, grid_plateau,
grid_ruin) дали список того, что вообще устояло; здесь это собрано в один
скелет, а каждое значение взято из СЕРЕДИНЫ измеренной устойчивой области
(пик области — это подгонка, середина — это то, что переживает смещение).

СКЕЛЕТ (ничего лишнего):
  вход   : RSI(rsi_p) пересекает вниз rsi_th -> ЛОНГ,
           вверх 100-rsi_th -> ШОРТ;
  фильтр : не открывать лонг в медвежьем режиме и шорт в бычьем
           (evolution6.calc_regime: дневная SMA100 + 30-дневное изменение,
           режим ВЧЕРАШНЕГО дня — причинно);
  сетка  : levels колен, шаг = step_atr x дневной ATR(14д), объём x1.5;
  тейк   : tp % от СРЕДНЕЙ цены (лимитка, maker);
  стоп   : stop % от СРЕДНЕЙ цены (маркет, taker + проскальзывание);
  плечо  : lev.
Ни таймаута, ни кулдауна, ни зон, ни ножа, ни безубытка, ни реинвеста.

ЧЕСТНОСТЬ движка (скопирован с evolution2.run5 / grid_plateau.run, бит в
бит сверяется самопроверкой в simple_grid_check.py):
  TAKER 0.055%, MAKER 0.02%, проскальзывание 0.03%, funding 0.01%/8ч,
  депозит $20, маржа цикла $5 (25%, без реинвеста), ликвидация при MM=0.95
  от использованной маржи, слив = баланс < $5. Внутри свечи консервативный
  порядок: сначала лимитки-доливки по пути цены, стоп бьёт по УВЕЛИЧЕННОЙ
  позиции; тейк — по средней ДО доливок этой свечи; ликвидация имеет
  приоритет, если она ближе стопа. Просадка — по ПЛАВАЮЩЕМУ капиталу.

Проверки и вердикты — simple_grid_check.py (он же пишет setups_grid.json).
"""

import os
import pickle
import random
import statistics

import grid_plateau as gp          # только читаем: daily_atr_pct + эталонный run

BASE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.environ.get("SG_SCRATCH", BASE)
DATA_PKL = os.path.join(SCRATCH, "simple_grid_data.pkl")

SYMBOLS = ["DOGEUSDT", "LTCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
DAYS = 1150
HOLD_FRAC = 0.72                  # holdout = последние 28% истории
MONTH_MS = 30 * 86400 * 1000
DAY_MS = 86400 * 1000

# --- издержки и счёт (идентичны evolution2 и grid_plateau) ---
TAKER, MAKER = 0.00055, 0.0002
SLIP = 0.0003
FUND_PER_BAR = 0.0001 / 32
START, MARGIN, MM = 20.0, 5.0, 0.95
MULT = 1.5                        # множитель объёма колена: ФИКСИРОВАН в
                                  # карте плато, не подбирался

# ======================================================================
#                      ПАРАМЕТРЫ СТРАТЕГИИ (их 6)
# ======================================================================
# Считаются параметрами только те 6, что ниже. Плечо задано риск-отчётом
# (не подбиралось), множитель объёма 1.5 и доля маржи 25% взяты из
# исследования как есть.
PARAMS = dict(
    # 1) период RSI. Плато ПРИБЫЛЬНОСТИ по нему нет, чувствительность 0.48
    #    при уровне чистого шума 1.13 — то есть параметр почти ничего не
    #    решает. «Лучшее» значение карты (21) держится на одной монете
    #    (LTC) => это подгонка. Берём СЕРЕДИНУ сетки {7,14,21}.
    rsi_p=14,
    # 2) порог RSI. То же самое: чувствительность 0.44 (шум 1.13),
    #    «лучшее» 25 — на одной монете. Середина сетки {25,30,35}.
    #    Шорт зеркально по 100-30=70.
    rsi_th=30,
    # 3) колен. Плато ВЫЖИВАЕМОСТИ монотонно: больше колен -> меньше
    #    сливов. Анатомия на доллар нотионала: 1->4 колена дают
    #    -0.247/-0.179/-0.151/-0.165% за цикл, т.е. на 3 кривая плоская и
    #    дальше не улучшается. Середина сетки {2,3,4} = 3 совпадает с
    #    плоским участком.
    levels=3,
    # 4) шаг колена в дневных ATR. Обе работы тянут в одну сторону:
    #    устойчивая коробка карты 0.4-0.7, анатомия — лучший вариант из
    #    всех «шаг 1-2 ATR» (-0.26% против -0.57% за цикл). Берём 0.7 —
    #    середину ОБЪЕДИНЁННОЙ области (верх коробки = низ анатомии),
    #    а не 2.0 (это был бы пик анатомии).
    step_atr=0.7,
    # 5) тейк. Коробка карты 2.0-3.5% -> середина ~2.75%, берём 2.5%
    #    (округление в сторону меньшего = чаще закрывается). Анатомия:
    #    фиксированные 4% на BTC = два дневных диапазона, тейк не
    #    доезжает; 2.5% ~ 0.7-1.1 дневного ATR по всем пяти монетам.
    tp=0.025,
    # 6) стоп. Коробка карты 7-10% -> середина 8.5%, берём 8%. Это самый
    #    НЕчувствительный параметр карты (0.30 при шуме 1.13), поэтому
    #    точное значение неважно; важно лишь, что стоп ближе ликвидации
    #    (на x2 ликвидация в ~47% от средней — стоп сработает всегда).
    stop=0.08,
)

# Плечо — НЕ подбиралось. Риск-отчёт: «слив<5% И DD p95<30%» выполняется
# только при x2 (и то не на всех монетах). Любое плечо выше — выбор
# скорости потери, а не размера дохода.
LEV = 2

# Фильтр тренда ВКЛЮЧЁН. Основание — карта плато: он единственный элемент,
# который расширяет область прибыльности БЕЗ настройки: доля прибыльных
# ячеек 23.2%->29.3%, комбинаций с медианой>0 2.2%->6.4%, сливов
# 46.0%->31.4% (при x5 18.6%->7.4%), улучшает 81.8% всех комбинаций.
# Оговорка, которую нельзя прятать: ровно так же он помогает СЛУЧАЙНОМУ
# входу, т.е. это не «RSI + режим», а просто «не стой против тренда».
USE_TREND_FILTER = True

PARAM_ORDER = ["rsi_p", "rsi_th", "levels", "step_atr", "tp", "stop"]
RSI_SET = [12, 13, 14, 15, 16, 21]      # 14 + окрестность для возмущений


# ======================================================================
#                                ДАННЫЕ
# ======================================================================

def prepare_data(force=False):
    """Свечи + причинные ряды (RSI, дневной ATR, режим). Кэш в pkl."""
    if not force and os.path.exists(DATA_PKL):
        with open(DATA_PKL, "rb") as fh:
            return pickle.load(fh)
    import backtest_rsi_grid as bg
    import evolution as ev
    import evolution6 as e6
    data = {}
    n0 = None
    for sym in SYMBOLS:
        candles = ev.fetch(sym, "15", DAYS)
        if n0 is None:
            n0 = len(candles)
        assert len(candles) == n0, f"ряды разъехались: {sym}"
        gaps = sum(1 for i in range(1, len(candles))
                   if candles[i][0] - candles[i - 1][0] != 900000)
        assert gaps == 0, f"дыры в ряду {sym}: {gaps}"
        closes = [c[4] for c in candles]
        data[sym] = dict(
            h=[c[2] for c in candles], l=[c[3] for c in candles], c=closes,
            t=[c[0] for c in candles],
            atr=gp.daily_atr_pct(candles),
            reg=e6.calc_regime(candles),
            rsi={p: bg.calc_rsi(closes, p) for p in RSI_SET},
        )
    n = n0
    blob = dict(data=data, n=n, hold=int(n * HOLD_FRAC))
    with open(DATA_PKL, "wb") as fh:
        pickle.dump(blob, fh, protocol=4)
    return blob


# ======================================================================
#                                ДВИЖОК
# ======================================================================

def make_sig(rsi, th, i0, i1):
    """+1 лонг (RSI пересёк th вниз), 255 = шорт (пересёк 100-th вверх)."""
    sig = bytearray(i1)
    ob = 100 - th
    for i in range(max(i0, 1), i1):
        a, b = rsi[i - 1], rsi[i]
        if a is None or b is None:
            continue
        if a >= th > b:
            sig[i] = 1
        elif a <= ob < b:
            sig[i] = 255
    return sig


def make_null_sig(n_l, n_s, i0, i1, seed):
    """Нулевая модель: столько же входов и та же доля сторон, но моменты
    случайные. Проверяет, несёт ли информацию МОМЕНТ входа."""
    rnd = random.Random(seed)
    k = n_l + n_s
    sig = bytearray(i1)
    if k <= 0:
        return sig
    idx = rnd.sample(range(i0, i1), min(k, i1 - i0))
    for j, i in enumerate(idx):
        sig[i] = 1 if j < n_l else 255
    return sig


def run_grid(H, L, C, ATR, SIG, REG, prm, i0, i1, T=None, cycles=None,
             no_ruin=False, stop_exec="stop", extra_slip=0.0):
    """Прогон сетки на [i0, i1).

    REG=None — фильтр тренда выключен. cycles — список, куда пишутся циклы.
    no_ruin=True — не обрывать прогон при балансе ниже маржи (нужно, чтобы
      получить ПОЛНЫЙ набор циклов; иначе выборка обрезается ровно на
      катастрофе — эту ловушку нашёл grid_ruin).
    stop_exec="close" — стресс: если свеча ЗАКРЫЛАСЬ за стопом, исполняем
      стоп по закрытию (проверка «стоп пробит насквозь»).
    extra_slip — добавка к проскальзыванию на маркет-выходах (стресс).

    При cycles=None, no_ruin=False, stop_exec="stop", extra_slip=0 обязан
    совпадать с grid_plateau.run бит в бит (самопроверка 1)."""
    lev = prm["lev"]
    nlv = prm["levels"]
    tp = prm["tp"]
    slp = prm["stop"]
    katr = prm["step_atr"]
    w = [MULT ** k for k in range(nlv)]
    tw = sum(w)
    m_k = [MARGIN * x / tw for x in w]
    cum_m = [0.0] * (nlv + 1)
    for k in range(nlv):
        cum_m[k + 1] = cum_m[k] + m_k[k]

    balance = peak = START
    max_dd = 0.0
    trades = wins = liqs = 0
    bars_pos = 0
    ruined = False
    sgn = 0
    pq = pnot = pfees = 0.0
    pnf = 0
    padds = []
    open_i = 0
    open_reg = 1

    for i in range(i0, i1):
        if sgn == 0 and SIG[i] == 0:
            continue
        h, l, c = H[i], L[i], C[i]
        if sgn != 0:
            bars_pos += 1
            avg = pnot / pq
            pfees += pq * c * FUND_PER_BAR
            stop_px = avg * (1 - sgn * slp)
            tp_px = avg * (1 + sgn * tp)
            p_liq = avg - sgn * (cum_m[pnf] * MM) / pq
            exit_px = max(stop_px, p_liq) if sgn == 1 else min(stop_px, p_liq)
            adverse = (l <= exit_px) if sgn == 1 else (h >= exit_px)
            hit_tp = (h >= tp_px) if sgn == 1 else (l <= tp_px)
            pnl = None
            reason = None
            if adverse:
                while True:
                    avg = pnot / pq
                    stop_px = avg * (1 - sgn * slp)
                    mused = cum_m[pnf]
                    p_liq = avg - sgn * (mused * MM) / pq
                    is_liq = (p_liq > stop_px) if sgn == 1 else (p_liq < stop_px)
                    exit_px = (max(stop_px, p_liq) if sgn == 1
                               else min(stop_px, p_liq))
                    ex_reach = (l <= exit_px) if sgn == 1 else (h >= exit_px)
                    if padds:
                        ap, aq = padds[0]
                        a_reach = (l <= ap) if sgn == 1 else (h >= ap)
                        a_above = (ap > exit_px) if sgn == 1 else (ap < exit_px)
                        if a_reach and (a_above or not ex_reach):
                            pfees += aq * ap * MAKER
                            pq += aq
                            pnot += ap * aq
                            pnf += 1
                            padds.pop(0)
                            continue
                    if not ex_reach:
                        break
                    if is_liq:
                        pnl = -mused * MM - pfees
                        liqs += 1
                        reason = "liq"
                    else:
                        base_px = stop_px
                        if stop_exec == "close":
                            beyond = (c < stop_px) if sgn == 1 else (c > stop_px)
                            if beyond:
                                base_px = c
                        px = base_px * (1 - sgn * (SLIP + extra_slip))
                        pnl = sgn * (px - avg) * pq - pq * px * TAKER - pfees
                        reason = "stop"
                    break
            elif hit_tp:
                pnl = sgn * (tp_px - avg) * pq - pq * tp_px * MAKER - pfees
                reason = "tp"
            else:
                while padds:
                    ap, aq = padds[0]
                    if (l <= ap) if sgn == 1 else (h >= ap):
                        pfees += aq * ap * MAKER
                        pq += aq
                        pnot += ap * aq
                        pnf += 1
                        padds.pop(0)
                    else:
                        break
            if pnl is None:
                eq = balance + sgn * (c - pnot / pq) * pq - pfees
                if eq > peak:
                    peak = eq
                d = (peak - eq) / peak
                if d > max_dd:
                    max_dd = d
                continue
            balance += pnl
            trades += 1
            if pnl > 0:
                wins += 1
            if balance > peak:
                peak = balance
            d = (peak - balance) / peak
            if d > max_dd:
                max_dd = d
            if cycles is not None:
                cycles.append(dict(i_in=open_i, i_out=i, side=sgn, pnl=pnl,
                                   R=pnl / MARGIN, reason=reason, depth=pnf,
                                   bars=i - open_i, reg=open_reg,
                                   t_in=(T[open_i] if T else 0),
                                   t_out=(T[i] if T else 0)))
            sgn = 0
            padds = []
            if balance < MARGIN and not no_ruin:
                ruined = True
                break
        s = SIG[i]
        if s == 0:
            continue
        s = 1 if s == 1 else -1
        a = ATR[i]
        if not a:
            continue
        if REG is not None:
            rg = REG[i]
            if (s == 1 and rg == 2) or (s == -1 and rg == 0):
                continue          # сильный тренд против позиции — не входим
        sgn = s
        px = c * (1 + sgn * SLIP)
        step = katr * a
        q0 = m_k[0] * lev / px
        pq = q0
        pnot = px * q0
        pfees = q0 * px * TAKER
        pnf = 1
        padds = []
        open_i = i
        open_reg = REG[i] if REG is not None else (1)
        for k in range(1, nlv):
            ap = px * (1 - sgn * k * step)
            padds.append((ap, m_k[k] * lev / ap))

    if sgn != 0 and not ruined:
        c = C[i1 - 1]
        avg = pnot / pq
        px = c * (1 - sgn * SLIP)
        pnl = sgn * (px - avg) * pq - pq * px * TAKER - pfees
        balance += pnl
        trades += 1
        if pnl > 0:
            wins += 1
        if balance > peak:
            peak = balance
        d = (peak - balance) / peak
        if d > max_dd:
            max_dd = d
        if cycles is not None:
            cycles.append(dict(i_in=open_i, i_out=i1 - 1, side=sgn, pnl=pnl,
                               R=pnl / MARGIN, reason="eod", depth=pnf,
                               bars=i1 - 1 - open_i, reg=open_reg,
                               t_in=(T[open_i] if T else 0),
                               t_out=(T[i1 - 1] if T else 0)))
        if balance < MARGIN and not no_ruin:
            ruined = True
    return dict(ret=(balance / START - 1) * 100, dd=max_dd * 100,
                trades=trades, wins=wins, liqs=liqs, ruined=ruined,
                bars_pos=bars_pos, balance=balance)


# ======================================================================
#                         ОБВЯЗКА / МЕТРИКИ
# ======================================================================

def params_with_lev(prm=None, lev=None):
    p = dict(PARAMS if prm is None else prm)
    p["lev"] = LEV if lev is None else lev
    return p


def sig_for(d, prm, i0, i1, kind="rsi", seed=0):
    """Сигналы входа: RSI или нулевая модель того же объёма."""
    base = make_sig(d["rsi"][prm["rsi_p"]], prm["rsi_th"], i0, i1)
    if kind == "rsi":
        return base
    n_l = sum(1 for i in range(i0, i1) if base[i] == 1)
    n_s = sum(1 for i in range(i0, i1) if base[i] == 255)
    return make_null_sig(n_l, n_s, i0, i1, seed)


def go(d, prm, i0, i1, trend=None, kind="rsi", seed=0, **kw):
    """Один прогон стратегии на монете d, отрезок [i0,i1)."""
    trend = USE_TREND_FILTER if trend is None else trend
    sig = sig_for(d, prm, i0, i1, kind=kind, seed=seed)
    reg = d["reg"] if trend else None
    return run_grid(d["h"], d["l"], d["c"], d["atr"], sig, reg, prm, i0, i1,
                    T=d["t"], **kw)


def replay(pnls, start=START, margin=MARGIN):
    """Переигровка PnL циклов на счёте $20: итог, слив?, просадка, номер
    цикла, на котором счёт кончился."""
    bal = peak = start
    dd = 0.0
    for k, p in enumerate(pnls):
        bal += p
        peak = max(peak, bal)
        if peak > 0:
            dd = max(dd, (peak - bal) / peak)
        if bal < margin:
            return (bal / start - 1) * 100, True, dd * 100, k + 1
    return (bal / start - 1) * 100, False, dd * 100, len(pnls)


def pct(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def bootstrap(rs, n_path, rng, block=(10, 20), nboot=2000, frac=0.25,
              ruin=0.20, half=0.50):
    """Методика grid_ruin: блочный бутстрап результатов цикла (R = PnL/маржа),
    капитал умножается на (1 + frac*R). Возвращает вероятность разорения,
    p95 просадки и медиану времени до разорения (в циклах)."""
    n = len(rs)
    if n < 10 or n_path < 1:
        return None
    n_ruin = n_half = 0
    finals, dds, tt = [], [], []
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
        eq = peak = 1.0
        dd = 0.0
        hit_r = hit_h = None
        for k, r in enumerate(seq):
            eq *= (1 + frac * r)
            if eq > peak:
                peak = eq
            d = (peak - eq) / peak
            if d > dd:
                dd = d
            if hit_h is None and eq <= half:
                hit_h = k + 1
            if hit_r is None and eq <= ruin:
                hit_r = k + 1
                break
        finals.append(eq)
        dds.append(dd)
        if hit_r:
            n_ruin += 1
            tt.append(hit_r)
        if hit_h:
            n_half += 1
    return dict(ruin=n_ruin / nboot * 100, half=n_half / nboot * 100,
                p5=pct(finals, 0.05), p50=pct(finals, 0.50),
                p95=pct(finals, 0.95), dd95=pct(dds, 0.95) * 100,
                dd50=pct(dds, 0.50) * 100,
                tt=(statistics.median(tt) if tt else None))


def fmt_date(ms):
    import datetime
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def main():
    blob = prepare_data()
    data, n, h = blob["data"], blob["n"], blob["hold"]
    print("ПАРАМЕТРЫ ПРОСТОЙ СЕТКИ (6 шт) + плечо из риск-отчёта:")
    for k in PARAM_ORDER:
        print(f"  {k:9} = {PARAMS[k]}")
    print(f"  {'lev':9} = {LEV}   (не параметр: задано риск-отчётом)")
    print(f"  фильтр тренда: {'ВКЛ' if USE_TREND_FILTER else 'ВЫКЛ'}")
    d0 = data[SYMBOLS[0]]
    print(f"\nholdout: {fmt_date(d0['t'][h])} .. {fmt_date(d0['t'][-1])} "
          f"({(d0['t'][-1]-d0['t'][h])/DAY_MS:.0f} дней, "
          f"{(n-h)/n*100:.0f}% истории)")
    print(f"{'монета':9} {'итог%':>8} {'DD%':>7} {'циклов':>7} {'WR%':>6} "
          f"{'ср.R':>8}")
    for sym in SYMBOLS:
        cyc = []
        r = go(data[sym], params_with_lev(), h, n, cycles=cyc)
        wr = r["wins"] / r["trades"] * 100 if r["trades"] else 0
        avg = statistics.mean([c["R"] for c in cyc]) if cyc else 0
        print(f"{sym:9} {r['ret']:+8.1f} {r['dd']:7.1f} {r['trades']:7} "
              f"{wr:6.1f} {avg:+8.4f}"
              f"{'  СЛИВ' if r['ruined'] else ''}")


if __name__ == "__main__":
    main()
