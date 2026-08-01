# -*- coding: utf-8 -*-
"""Штормовой фильтр торговых ботов: «не открывать новые циклы, когда рынок
в экстремальном движении».

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Живой бот (bot_rsi.py) и движок бэктеста
(evolution2.run5) обязаны принимать РОВНО одно и то же решение, иначе
тестовые цифры перестают что-либо значить. Поэтому вся математика лежит
здесь, а оба вызывают её на одних и тех же свечах. Скопированной логики
нет ни в боте, ни в движке — только вызовы.

ЧТО СЧИТАЕТСЯ (всё причинно: на баре i используются только данные <= i):
  ret_1d    — изменение цены за сутки  = closes[i]/closes[i-bpd]   - 1
  ret_7d    — изменение цены за 7 суток = closes[i]/closes[i-7*bpd] - 1
  atr       — «суточный» ATR в долях цены: среднее последних bpd истинных
              диапазонов / close (bpd = баров в сутках = 1440/ТФ)
  atr_rank  — перцентиль текущего atr среди последних rank_days суток
              (0..1; 0.9 = волатильность выше, чем в 90% недавних баров)
  strength  = max(|ret_1d|/day_pct, |ret_7d|/week_pct, atr_rank/rank_thr)
  ШТОРМ <=> strength > 1. Одно число вместо трёх флагов: видно, НАСКОЛЬКО
  далеко зашли за порог, и порог одного условия можно выключить, задав его
  недостижимым (например atr_rank_thr = 1.0 — перцентиль никогда не больше 1).

  dir: -1 обвал, +1 ракета, 0 (не шторм). Берётся у условия, вызвавшего
  шторм (сутки важнее недели); если шторм объявлен только по волатильности —
  по знаку суточного хода.

РЕЖИМЫ (STORM_MODE в config.py):
  0 — в шторм не открывать НИКАКИЕ новые циклы;
  1 — запрещать только входы ПРОТИВ шторма (в обвале нельзя лонг, в
      ракете нельзя шорт).
  Уже открытая позиция НЕ трогается ни в одном режиме: сетка доливается,
  стоп/тейк/таймаут работают как обычно. Фильтр — только на вход.

ПОЧЕМУ ATR ПРОСТОЙ, А НЕ ПО УАЙЛДЕРУ. Сглаживание Уайлдера (как в
evolution.calc_atr_pct) имеет бесконечную память: его значение зависит от
всей истории с начала массива. Живой бот держит в руках лишь последнюю
тысячу свечей, поэтому его ATR никогда не совпал бы с бэктестовым в точности.
Простое среднее последних bpd диапазонов — конечная память: при наличии
bars_needed() баров живое значение СОВПАДАЕТ с бэктестовым бит в бит
(это проверяется в storm_bots_report.py, блок «живое == тестовое»).

Величины считаются скользящими окнами ПО СВЕЧАМ САМОГО БОТА (дневные свечи
отдельно не запрашиваются — лишних запросов к бирже нет и рассинхрона тоже).
Свечи: [(ts_ms, o, h, l, c), ...] по возрастанию времени, как везде в проекте.
"""

import bisect
from collections import deque

MIN_RANK_N = 10        # меньше 10 значений в окне — перцентиль не считаем
RANK_DAYS = 7          # окно ранга волатильности по умолчанию, суток


def bars_per_day(interval_min):
    """Баров в сутках для таймфрейма (та же формула, что в evolution8/
    finalize_final_bots: нижняя граница 4 бара, чтобы дневной ATR имел смысл
    и на крупных ТФ)."""
    return max(4, 1440 // int(interval_min))


def bars_needed(bpd, rank_days=RANK_DAYS):
    """Сколько закрытых баров нужно, чтобы шторм на ПОСЛЕДНЕМ баре считался
    полностью (и совпадал с бэктестом): недельный ход + окно ранга ATR,
    которому нужен ещё bpd баров прогрева."""
    bpd = int(bpd)
    return max(7 * bpd, bpd + int(rank_days) * bpd) + 1


def pct_change_bars(closes, k):
    """Относительное изменение за k баров назад; None там, где истории нет."""
    k = max(1, int(k))
    out = [None] * len(closes)
    for i in range(k, len(closes)):
        base = closes[i - k]
        out[i] = (closes[i] / base - 1.0) if base else 0.0
    return out


def atr_frac_series(candles, n):
    """«Суточный» ATR в долях цены: среднее последних n истинных диапазонов,
    делённое на close бара. None, пока n диапазонов не набралось.
    Память конечная (ровно n баров) — см. шапку модуля."""
    n = max(1, int(n))
    out = [None] * len(candles)
    if len(candles) < n + 1:
        return out
    trs = []
    prev_c = candles[0][4]
    acc = 0.0
    for i in range(1, len(candles)):
        _, o, h, l, c = candles[i]
        tr = max(h - prev_c, prev_c - l, h - l)
        prev_c = c
        trs.append(tr)
        acc += tr
        if len(trs) > n:
            acc -= trs[-n - 1]
        if len(trs) >= n and c:
            out[i] = (acc / n) / c
    return out


def rolling_rank(vals, win, min_n=MIN_RANK_N):
    """Перцентиль vals[i] среди последних win значений ВКЛЮЧАЯ i: доля
    значений окна, которые <= текущего (0..1). None там, где значения нет
    или в окне меньше min_n чисел. Смотрит только назад — заглядывания в
    будущее нет (проверяется сравнением «префикс vs полная история»)."""
    n = len(vals)
    out = [None] * n
    win = max(2, int(win))
    buf = deque()          # (индекс, значение) в порядке их появления
    srt = []               # те же значения, отсортированные (bisect)
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


def build_core(candles, bpd, rank_days=RANK_DAYS):
    """Ряды, не зависящие от порогов (считаются один раз на символ)."""
    bpd = max(1, int(bpd))
    closes = [c[4] for c in candles]
    atr = atr_frac_series(candles, bpd)
    return dict(
        ret_1d=pct_change_bars(closes, bpd),
        ret_7d=pct_change_bars(closes, 7 * bpd),
        atr=atr,
        atr_rank=rolling_rank(atr, int(rank_days) * bpd),
        bpd=bpd, rank_days=int(rank_days), n=len(candles))


def apply_thresholds(core, day_pct, week_pct, atr_rank_thr, mode):
    """core + пороги -> ряды storm/dir/strength и режим. Пороги задаются
    долями: 0.07 = 7%. atr_rank_thr=1.0 фактически выключает компонент
    волатильности (перцентиль никогда не превышает 1)."""
    thr_d = float(day_pct) or 1e-9
    thr_w = float(week_pct) or 1e-9
    thr_a = float(atr_rank_thr) or 1e-9
    r1s, r7s, rks = core["ret_1d"], core["ret_7d"], core["atr_rank"]
    n = core["n"]
    storm = [False] * n
    dirs = [0] * n
    stren = [0.0] * n
    for i in range(n):
        r1 = r1s[i] or 0.0
        r7 = r7s[i] or 0.0
        rk = rks[i] or 0.0
        s_d = (r1 if r1 >= 0 else -r1) / thr_d
        s_w = (r7 if r7 >= 0 else -r7) / thr_w
        s_a = rk / thr_a
        s = s_d if s_d > s_w else s_w
        if s_a > s:
            s = s_a
        stren[i] = s
        if s > 1.0:
            storm[i] = True
            base = r1 if s_d > 1.0 else (r7 if s_w > 1.0 else (r1 or r7))
            dirs[i] = 1 if base > 0 else (-1 if base < 0 else 0)
    out = dict(core)
    out.update(storm=storm, dir=dirs, strength=stren, mode=int(mode),
               day_pct=float(day_pct), week_pct=float(week_pct),
               atr_rank_thr=float(atr_rank_thr))
    return out


def build(candles, bpd, day_pct, week_pct, atr_rank_thr, mode,
          rank_days=RANK_DAYS):
    """Полный расчёт «с нуля» (используют и живой бот, и бэктест)."""
    return apply_thresholds(build_core(candles, bpd, rank_days),
                            day_pct, week_pct, atr_rank_thr, mode)


def from_config(candles, bpd, cfg):
    """Ряды по настройкам секции STORM из config.py."""
    return build(candles, bpd,
                 getattr(cfg, "STORM_DAY_PCT", 0.07),
                 getattr(cfg, "STORM_WEEK_PCT", 0.15),
                 getattr(cfg, "STORM_ATR_RANK", 1.0),
                 getattr(cfg, "STORM_MODE", 0),
                 rank_days=getattr(cfg, "STORM_RANK_DAYS", RANK_DAYS))


def slice_series(st, a, b):
    """Срез рядов под срез свечей candles[a:b] — значения остаются теми же,
    что были посчитаны на ПОЛНОЙ истории (это законно: каждое значение
    зависит только от данных <= своего бара). Нужно, чтобы у куска истории
    не было ложного «прогрева» в первую неделю."""
    out = dict(st)
    for k in ("ret_1d", "ret_7d", "atr", "atr_rank", "storm", "dir",
              "strength"):
        out[k] = st[k][a:b]
    out["n"] = len(out["storm"])
    return out


def state_at(st, i):
    """Состояние шторма на баре i одним словарём (для лога и отчётов)."""
    if i < 0:
        i += st["n"]
    return dict(storm=st["storm"][i], dir=st["dir"][i],
                strength=st["strength"][i],
                ret_1d=st["ret_1d"][i] or 0.0,
                ret_7d=st["ret_7d"][i] or 0.0,
                atr=st["atr"][i], atr_rank=st["atr_rank"][i],
                day_pct=st["day_pct"], week_pct=st["week_pct"],
                atr_rank_thr=st["atr_rank_thr"], mode=st["mode"],
                rank_days=st["rank_days"])


def blocked(side, storm_on, storm_dir, mode):
    """ЯДРО РЕШЕНИЯ. True = новый вход запрещён. Одна и та же функция
    вызывается из живого бота и из бэктеста — расходиться нечему.
    side: "L"/"S"."""
    if not storm_on:
        return False
    if int(mode) == 1:
        # запрещаем только вход ПРОТИВ шторма
        return (storm_dir < 0 and side == "L") or (storm_dir > 0 and side == "S")
    return True                      # режим 0: любой новый вход запрещён


def blocked_at(side, st, i):
    """То же решение по рядам на баре i (путь бэктеста)."""
    return blocked(side, st["storm"][i], st["dir"][i], st["mode"])


def blocked_state(side, s):
    """То же решение по словарю state_at (путь живого бота)."""
    return blocked(side, s["storm"], s["dir"], s["mode"])


def cause(symbol, s):
    """Короткая причина шторма для лога: «BTCUSDT -9.4% за сутки»."""
    r1, r7 = s["ret_1d"], s["ret_7d"]
    thr_d = s["day_pct"] or 1e-9
    thr_w = s["week_pct"] or 1e-9
    thr_a = s["atr_rank_thr"] or 1e-9
    if abs(r1) / thr_d > 1.0:
        return "%s %+.1f%% за сутки" % (symbol, r1 * 100)
    if abs(r7) / thr_w > 1.0:
        return "%s %+.1f%% за неделю" % (symbol, r7 * 100)
    rk = s["atr_rank"] or 0.0
    if rk / thr_a > 1.0:
        return ("%s волатильность выше, чем в %.0f%% баров за %d сут."
                % (symbol, rk * 100, s["rank_days"]))
    return "%s стресс %.2f от порога" % (symbol, s["strength"])


def log_line(symbol, s, enabled):
    """Строка состояния для лога бота (пишется на каждой новой свече, даже
    когда фильтр выключен — чтобы логика была видна и её можно было
    проверить, не дожидаясь настоящего шторма)."""
    if s is None:
        return "шторм: нет данных (мало свечей)"
    tail = ("сутки %+.1f%%, неделя %+.1f%%, ранг ATR %s"
            % (s["ret_1d"] * 100, s["ret_7d"] * 100,
               "н/д" if s["atr_rank"] is None else "%.2f" % s["atr_rank"]))
    if not enabled:
        state = "ДА" if s["storm"] else "нет"
        return ("шторм: %s (%s) | фильтр ВЫКЛЮЧЕН (config.STORM_ENABLED=False)"
                % (state, tail))
    if not s["storm"]:
        return "шторм: нет (%s) — вход разрешён" % tail
    if int(s["mode"]) == 1:
        who = "лонги" if s["dir"] < 0 else "шорты"
        return ("шторм: %s (%s) — режим 1: новые %s заблокированы"
                % (cause(symbol, s), tail, who))
    return ("шторм: %s (%s) — новые входы заблокированы"
            % (cause(symbol, s), tail))
