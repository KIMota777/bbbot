# -*- coding: utf-8 -*-
"""Финализация отбора v3 (движок signal_engine3): evolution14_winners.json
-> setups_v3.json — единственный файл, который читают сайт и билдер страниц.

ЗАЧЕМ ТАК СТРОГО. Двенадцать волн отбора давали красивые цифры, которые не
выживали проверку: случайный геном проходит порог в 1.4% случаев, отобранный
в 25%, НО при смене случайного зерна побеждают каждый раз ДРУГИЕ конфиги.
Значит генетика ловит параметры, случайно подошедшие к отрезку. Поэтому
торговый статус здесь выдаётся только тому, кто прошёл ВСЕ проверки:

  A. воспроизводимость — подтверждён минимум в MIN_REPRO независимых прогонах
     отбора (разные зёрна, файлы winners_v3_seed*.json либо блок repro);
  B. holdout-порог — на невиданных данных n>=HOLD_MIN_N, exp_r>0, сумма R>0,
     PF>=HOLD_MIN_PF и |t|>=HOLD_MIN_T (без t-статистики «плюс» — это шум);
  C. преимущество над НЕОБУЧЕННЫМ семенем (edge по expectancy > 0);
  D. переносимость — тот же геном на holdout-окне даёт exp_r>0 хотя бы на
     одной ДРУГОЙ монете (иначе подогнано под одну серию цен);
  E. обе половины истории — exp_r>0 и на обучающей части тоже. Иначе плюс
     объясняется направлением рынка (holdout 2025-09..2026-07 — сплошной
     медведь, шортовая сторона там зарабатывает без всякого преимущества).

Не прошёл — статус «наблюдение» (если на holdout хоть что-то положительное)
или «не подтверждён», и в поле reason печатается фактическая причина.
Отрицательный результат — валидный результат.

Проверку D скрипт умеет считать САМ (прогон генома на других монетах в том же
временном окне holdout), если GA не передал блок transfer. Это единственная
арифметика, которую финализатор делает сам; всё остальное — нормализация и
проверка того, что посчитал отбор.

--------------------------------------------------------------------------
КОНТРАКТ ВХОДА: evolution14_winners.json  (что обязан записать GA v14)
--------------------------------------------------------------------------
{
  "<сетап>@<МОНЕТА>@<ТФ>": {          # напр. "breakout_short@BTCUSDT@240"
    "setup": "breakout_short",        # можно не писать — возьмётся из ключа
    "symbol": "BTCUSDT",              # можно не писать — возьмётся из ключа
    "interval_min": 240,              # можно не писать — возьмётся из ключа
    "genome": { ... 41 ген signal_engine3.GENES3 ... },   # ОБЯЗАТЕЛЬНО
    "rec_lev": 10,                    # рекомендованное плечо
    "passed": true,                   # прошёл ли ЭТОТ прогон честный экзамен
    "fail_reasons": ["..."],          # почему не прошёл (список строк)
    "holdout":  {"n":53,"wr":50.9,"exp_r":0.477,"sum_r":25.3,"pf":1.9,
                 "ret":134.9,"dd":21.0,"t":2.28,"p":0.0225,
                 "tp":27,"stop":24,"liq":0,"timeout":2,
                 "from_ts":1757200000000,"to_ts":1785000000000,
                 "bars":[4967,6899]},          # ts в мс; bars — индексы баров
    "train":    { ... те же поля на обучающей части ... },   # для проверки E
    "holdout_benchmark": { ... те же поля, НЕОБУЧЕННОЕ семя ... },
    "mirror":   { ... те же поля, ЗЕРКАЛЬНЫЙ сетап на holdout ... },
    "random":   {"exp_r_median":-0.02,"rank_pct":87,"n_random":200},
    "edge_exp_r": 0.12,               # exp_r конфига минус exp_r семени
    "inner_oos": { ... вложенная валидация внутри обучающей части ... },
    "transfer": {"ETHUSDT": {"n":31,"wr":45.2,"exp_r":0.21,"ret":18.4,
                             "dd":9.1,"pf":1.4}, ...},   # можно не писать
    "ladder":   [{"lev":5,"ret":..,"dd":..,"n":..,"wr":..,"exp_r":..,"pf":..}],
    "repro":    {"seeds_passed":2,"of":3,"seeds":[101,202,303]},  # либо файлы
    "hold_start_bar": 4967, "n_bars": 6899,
    "seed": 47, "smoke": false, "ga": {"pop":..,"gens":..,"seed":..}
  }, ...
}
Минимум, без которого запись отбрасывается: ключ + genome.
Чего нет — не выдумывается: проверка считается НЕ пройденной с причиной
«GA не передал ...», и статус будет «наблюдение», а не «торговый».

--------------------------------------------------------------------------
ЗАПУСК
--------------------------------------------------------------------------
  python finalize_v3.py                       # прод: winners -> setups_v3.json
  python finalize_v3.py --winners X --out Y   # проверка на заглушке
  python finalize_v3.py --no-transfer --no-stats     # быстро, без прогонов
"""

import argparse
import glob
import json
import math
import os
import sys
import time

try:                                   # русский вывод в консоль Windows
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import evolution as ev
import signal_engine3 as se3
import signal_stats as ss

WINNERS = "evolution14_winners.json"
OUT = "setups_v3.json"
SEED_GLOB = "winners_v3_seed*.json"     # независимые прогоны отбора
DAYS = 1150                             # глубина истории проекта
DEF_LEV = 10

# ------------------------------------------------------------------ пороги
# Объявлены ДО подсчёта и вынесены наверх, чтобы порог нельзя было подобрать
# под ответ незаметной правкой внутри функции.
MIN_REPRO = 2          # подтверждений в независимых прогонах отбора
MIN_RUNS = 2           # меньше прогонов — воспроизводимость НЕ проверена
HOLD_MIN_N = 15        # меньше сделок на holdout — выводов нет
HOLD_MIN_PF = 1.2
HOLD_MIN_T = 2.0       # |t| expectancy: 2 сигмы, иначе это шум
TRANSFER_MIN_N = 10    # столько сделок нужно, чтобы монета считалась проверенной
WATCH_MIN_N = 8        # ниже — даже «наблюдение» не даём

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT"]

# поля блока результата (holdout / train / бенчмарки) — нормализуем к ним
BLOCK_NUM = ("n", "wr", "exp_r", "sum_r", "pf", "ret", "dd", "t", "p",
             "tp", "stop", "liq", "timeout", "avg_grid_fills")
BLOCK_TS = ("from_ts", "to_ts")


def tf_label(iv):
    """240 -> «4ч», 60 -> «1ч», 15 -> «15м»."""
    iv = int(iv)
    if iv % 60 == 0:
        return f"{iv // 60}ч"
    return f"{iv}м"


def coin(symbol):
    """BTCUSDT -> BTC (для бейджа на сайте)."""
    return str(symbol).replace("USDT", "")


def split_key(key, rec=None):
    """Ключ -> (сетап, монета, ТФ). Формат «<сетап>@<МОНЕТА>@<ТФ>».

    Терпим и к неполным ключам: недостающее берём из полей записи, монету по
    умолчанию считаем BTCUSDT (исторический дефолт проекта), ТФ — 240."""
    rec = rec or {}
    parts = str(key).split("@")
    setup = parts[0]
    symbol, iv = None, None
    for p in parts[1:]:
        if p.isdigit():
            iv = int(p)
        elif p:
            symbol = p.upper()
    setup = str(rec.get("setup") or setup)
    symbol = str(rec.get("symbol") or symbol or "BTCUSDT").upper()
    try:
        iv = int(rec.get("interval_min") or iv or 240)
    except (TypeError, ValueError):
        iv = 240
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    return setup, symbol, iv


def full_key(setup, symbol, iv):
    return f"{setup}@{symbol}@{int(iv)}"


# ------------------------------------------------------------- нормализация
def _f(v, nd=None):
    """Число или None (строки/None/мусор не роняют финализацию)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return round(x, nd) if nd is not None else x


def _i(v):
    x = _f(v)
    return None if x is None else int(round(x))


def norm_block(d):
    """Блок результата (holdout/train/бенчмарк) -> нормализованный словарь.
    Отсутствующие поля остаются None — они НЕ выдумываются."""
    if not isinstance(d, dict):
        return None
    out = {k: _f(d.get(k), 4) for k in BLOCK_NUM}
    for k in ("n", "tp", "stop", "liq", "timeout"):
        out[k] = _i(d.get(k))
    for k in BLOCK_TS:
        out[k] = _i(d.get(k))
    bars = d.get("bars")
    if isinstance(bars, (list, tuple)) and len(bars) == 2:
        out["bars"] = [_i(bars[0]), _i(bars[1])]
    if d.get("period"):
        out["period"] = str(d["period"])
    return out


def norm_genome(src):
    """Полный геном v3: DEFAULTS3 + значения записи, зажатые в границы GENES3.
    Возвращает (геном, список зажатых генов, список неизвестных генов)."""
    g = dict(se3.DEFAULTS3)
    src = src if isinstance(src, dict) else {}
    clamped, unknown = [], []
    for k, v in src.items():
        if k not in se3.GENES3:
            unknown.append(k)
            continue
        lo, hi, is_int = se3.GENES3[k]
        val = _f(v)
        if val is None:
            continue
        val = int(round(val)) if is_int else float(val)
        fixed = min(max(val, lo), hi)
        if is_int:
            fixed = int(fixed)
        if fixed != val:
            clamped.append(f"{k}: {val} -> {fixed}")
        g[k] = fixed
    return g, clamped, unknown


def on_bound(g):
    """Гены, упёршиеся в границу пространства поиска. Оптимум на границе —
    почти всегда признак, что фитнес рос в сторону, которую диапазон обрезал:
    параметр выбран «до упора», а не по смыслу.

    Не считаем находкой: переключатели и режимы (is_int с диапазоном <=3 —
    там «граница» это просто одно из значений) и значения, совпавшие с
    нейтральным семенем (их никто не подбирал)."""
    out = []
    for k, (lo, hi, is_int) in se3.GENES3.items():
        v = g.get(k)
        if v is None or lo == hi:
            continue
        if is_int and (hi - lo) <= 3:
            continue                        # переключатель/режим, не диапазон
        if se3.DEFAULTS3.get(k) == v:
            continue                        # значение семени — не результат
        if is_int and (int(v) == int(lo) or int(v) == int(hi)):
            out.append(f"{k}={v}")
        elif not is_int and (abs(v - lo) < 1e-9 or abs(v - hi) < 1e-9):
            out.append(f"{k}={round(v, 4)}")
    return out


# ------------------------------------------------------ воспроизводимость
def repro_counts(pattern=SEED_GLOB):
    """Сколько независимых прогонов отбора подтвердили каждый ключ.

    Считаем по файлам winners_v3_seed*.json: у каждого своё случайное зерно.
    Ключ нормализуем — прогоны могут писать «сетап@МОНЕТА@ТФ» в любом
    регистре монеты."""
    counts, total, files = {}, 0, []
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        total += 1
        files.append(os.path.basename(path))
        for key, rec in data.items():
            if key.startswith("__") or not isinstance(rec, dict):
                continue
            k = full_key(*split_key(key, rec))
            counts[k] = counts.get(k, 0) + (1 if rec.get("passed") else 0)
    return counts, total, files


# ---------------------------------------------------------------- данные
class Data:
    """Ленивый доступ к свечам и контексту. Кэш — по (монета, ТФ), чтобы
    прогон переносимости не перечитывал 15м-историю на каждый ключ."""

    def __init__(self, days=DAYS):
        self.days = days
        self._c = {}
        self._ctx = {}
        self._m15 = {}

    @staticmethod
    def cache_path(symbol, iv, days=DAYS):
        return f"history_{symbol}_{iv}m_{days}d.json"

    def has(self, symbol, iv):
        """Есть ли история локально (без похода в сеть)."""
        return (os.path.exists(self.cache_path(symbol, iv, self.days))
                and os.path.exists(self.cache_path(symbol, 15, self.days)))

    def bars(self, symbol, iv):
        k = (symbol, int(iv))
        if k not in self._c:
            self._c[k] = ev.fetch(symbol, str(int(iv)), self.days)
        return self._c[k]

    def m15(self, symbol):
        if symbol not in self._m15:
            c15 = ev.fetch(symbol, "15", self.days)
            self._m15[symbol] = (c15, [c[0] for c in c15])
        return self._m15[symbol]

    def ctx(self, symbol, iv):
        k = (symbol, int(iv))
        if k not in self._ctx:
            self._ctx[k] = se3.prep_context(self.bars(symbol, iv),
                                            interval_min=int(iv), symbol=symbol)
        return self._ctx[k]


def bars_in_window(c4, t_from, t_to):
    """Индексы баров, попадающих во временное окно [t_from, t_to] (мс).
    Так holdout переносится на другую монету честно — по ВРЕМЕНИ, а не по
    номеру бара (у монет разное число баров)."""
    a = b = None
    for i, c in enumerate(c4):
        if a is None and c[0] >= t_from:
            a = i
        if c[0] <= t_to:
            b = i
    if a is None:
        return None
    return a, min(len(c4), (b if b is not None else len(c4) - 1) + 1)


def t_and_p(rs):
    """t-статистика и двусторонняя p для среднего R (нормальное приближение).

    Без неё «плюс» ничего не значит: при 10 сделках и разбросе 1R средний
    +0.3R — обычный шум. signal_stats значимость не считает, поэтому считаем
    здесь, тем же способом, что и в отчётах проекта."""
    n = len(rs)
    if n < 3:
        return None, None
    m = sum(rs) / n
    var = sum((x - m) ** 2 for x in rs) / (n - 1)
    sd = var ** 0.5
    if sd <= 0:
        return None, None
    t = m / (sd / n ** 0.5)
    return round(t, 3), round(math.erfc(abs(t) / math.sqrt(2)), 4)


def run_block(setup, g, lev, symbol, iv, data, rng=None):
    """Прогон сетапа на монете (опц. в окне баров) -> блок как у GA."""
    c4 = data.bars(symbol, iv)
    ctx = data.ctx(symbol, iv)
    c15, ts15 = data.m15(symbol)
    r = se3.run_setup(setup, g, c4, ctx, c15, ts15, lev, symbol=symbol,
                      signal_range=rng, collect_diag=False, interval_min=iv)
    a = r["signal_range"][0]
    st = ss.full_stats(r, lev, t0_ms=c4[a][0] if a < len(c4) else None)
    o = st.get("outcomes") or {}
    t, p = t_and_p([x["r"] for x in r["trades"]])
    return dict(
        n=st["n"], wr=st["wr"], exp_r=st["exp_r"], sum_r=st["sum_r"],
        pf=st["pf"], ret=st["ret_pct"], dd=st["dd_pct"], t=t, p=p,
        tp=(o.get("tp") or {}).get("n"), stop=(o.get("stop") or {}).get("n"),
        liq=(o.get("liq") or {}).get("n"),
        timeout=(o.get("timeout") or {}).get("n"),
        avg_grid_fills=r.get("avg_grid_fills"),
        from_ts=c4[a][0] if a < len(c4) else None,
        to_ts=c4[min(r["signal_range"][1], len(c4)) - 1][0],
        bars=[r["signal_range"][0], r["signal_range"][1]])


def holdout_window(rec, hold, setup, symbol, iv, data):
    """Временное окно holdout в мс: сперва из явных from_ts/to_ts, затем из
    hold_start_bar по свечам монеты. Не определилось — (None, None)."""
    t_from = hold.get("from_ts") if hold else None
    t_to = hold.get("to_ts") if hold else None
    if t_from and t_to:
        return int(t_from), int(t_to)
    hs = _i(rec.get("hold_start_bar"))
    if hs is None and hold and hold.get("bars"):
        hs = hold["bars"][0]
    if hs is None or not data.has(symbol, iv):
        return None, None
    c4 = data.bars(symbol, iv)
    if not (0 <= hs < len(c4)):
        return None, None
    return int(c4[hs][0]), int(c4[-1][0])


def calc_transfer(setup, g, lev, symbol, iv, t_from, t_to, data, quiet=False):
    """Переносимость: тот же геном на ДРУГИХ монетах в том же окне holdout.

    Окно то же самое, значит для чужой монеты это тоже невиданные данные, а
    геном к ней вообще не подбирался. Если данных монеты нет локально —
    честно пишем «нет истории», а не пропускаем молча."""
    out = {}
    for sym in COINS:
        if sym == symbol:
            continue
        if not data.has(sym, iv):
            out[sym] = dict(n=None, note="нет локальной истории")
            continue
        c4 = data.bars(sym, iv)
        rng = bars_in_window(c4, t_from, t_to)
        if not rng:
            out[sym] = dict(n=None, note="окно holdout вне истории монеты")
            continue
        try:
            blk = run_block(setup, g, lev, sym, iv, data, rng=rng)
        except Exception as e:                       # noqa: BLE001
            out[sym] = dict(n=None, note=f"ошибка прогона: {e}")
            continue
        blk["ok"] = bool(blk["n"] and blk["n"] >= TRANSFER_MIN_N
                         and (blk["exp_r"] or 0) > 0)
        out[sym] = blk
        if not quiet:
            print(f"    перенос на {coin(sym):5}: {blk['n']:3} сделок, "
                  f"exp {blk['exp_r']:+.3f}R, итог {blk['ret']:+7.1f}%"
                  + ("  ✓" if blk["ok"] else ""))
    return out


# ---------------------------------------------------------------- проверки
def check_repro(rec, key, counts, n_runs):
    """A. Подтверждение в независимых прогонах отбора."""
    blk = rec.get("repro") if isinstance(rec.get("repro"), dict) else None
    if blk and _i(blk.get("of")):
        passed, of = _i(blk.get("seeds_passed")) or 0, _i(blk.get("of"))
        src = "блок repro"
    else:
        passed, of, src = counts.get(key, 0), n_runs, SEED_GLOB
    info = dict(seeds_passed=passed, of=of, source=src)
    if isinstance(blk, dict) and blk.get("seeds"):
        info["seeds"] = blk["seeds"]
    if not of or of < MIN_RUNS:
        return False, (f"воспроизводимость не проверена: независимых прогонов "
                       f"{of or 0}, нужно {MIN_RUNS}+"), info
    if passed < MIN_REPRO:
        return False, (f"подтверждён лишь в {passed} из {of} независимых "
                       f"прогонов отбора (нужно {MIN_REPRO}+)"), info
    return True, f"подтверждён в {passed} из {of} прогонов", info


def check_holdout(h):
    """B. Порог на невиданных данных."""
    if not h or h.get("n") is None:
        return False, "GA не передал результат на holdout"
    bad = []
    if h.get("recheck_mismatch"):
        rc = h.get("recheck") or {}
        bad.append(f"числа GA не воспроизвелись движком: пересчёт даёт "
                   f"{rc.get('n')} сделок и exp {rc.get('exp_r')}R")
    if h["n"] < HOLD_MIN_N:
        bad.append(f"на holdout {h['n']} сделок < {HOLD_MIN_N}")
    if (h.get("exp_r") or 0) <= 0:
        bad.append(f"expectancy {h.get('exp_r')}R не > 0")
    if (h.get("sum_r") or 0) <= 0:
        bad.append(f"сумма {h.get('sum_r')}R не > 0")
    if h.get("pf") is None or h["pf"] < HOLD_MIN_PF:
        bad.append(f"PF {h.get('pf')} < {HOLD_MIN_PF}")
    if h.get("t") is None:
        bad.append("t-статистика не посчитана (плюс без неё — шум)")
    elif abs(h["t"]) < HOLD_MIN_T:
        bad.append(f"|t| {abs(h['t']):.2f} < {HOLD_MIN_T}")
    return (not bad), ("; ".join(bad) if bad else "порог holdout взят")


def check_edge(rec, h, bench):
    """C. Преимущество над необученным семенем."""
    edge = _f(rec.get("edge_exp_r"), 3)
    if edge is None and h and bench and h.get("exp_r") is not None \
            and bench.get("exp_r") is not None:
        edge = round(h["exp_r"] - bench["exp_r"], 3)
    if edge is None:
        return False, "нет сравнения с необученным семенем", None
    if edge <= 0:
        return False, f"нет преимущества над необученным семенем ({edge:+.3f}R)", edge
    return True, f"преимущество над семенем {edge:+.3f}R", edge


def check_transfer(tr):
    """D. Перенос хотя бы на одну другую монету."""
    if not tr:
        return False, "переносимость на другие монеты не проверена", 0, 0
    tested = sum(1 for v in tr.values() if v.get("n") is not None)
    ok = sum(1 for v in tr.values() if v.get("ok"))
    if not tested:
        return False, "переносимость не проверена (нет данных монет)", 0, 0
    if not ok:
        return False, (f"не переносится: из {tested} других монет ни одна не "
                       f"даёт плюс (нужно >=1 при {TRANSFER_MIN_N}+ сделках)"), ok, tested
    return True, f"переносится на {ok} из {tested} монет", ok, tested


def check_halves(train, h):
    """E. Плюс на ОБЕИХ половинах истории (иначе это ставка на направление)."""
    if not train or train.get("exp_r") is None:
        return False, "GA не передал результат на обучающей части"
    if train["exp_r"] <= 0:
        return False, (f"на обучающей части expectancy {train['exp_r']:+.3f}R "
                       f"не > 0 — плюс только на одной половине истории")
    if h and h.get("exp_r") is not None and h["exp_r"] <= 0:
        return False, "на holdout expectancy не > 0"
    return True, "плюс на обеих половинах истории"


# ------------------------------------------------------------------ сборка
def build_one(key, rec, counts, n_runs, data, do_transfer, do_stats):
    setup, symbol, iv = split_key(key, rec)
    k = full_key(setup, symbol, iv)
    if setup not in se3.SETUPS3:
        return k, None, f"неизвестный сетап «{setup}» — запись пропущена"
    g, clamped, unknown = norm_genome(rec.get("genome"))
    if not isinstance(rec.get("genome"), dict) or not rec["genome"]:
        return k, None, "в записи нет генома — запись пропущена"
    lev = _i(rec.get("rec_lev")) or DEF_LEV

    h = norm_block(rec.get("holdout"))
    train = norm_block(rec.get("train") or rec.get("train_block"))
    bench = norm_block(rec.get("holdout_benchmark") or rec.get("benchmark"))
    mirror = norm_block(rec.get("mirror") or rec.get("mirror_holdout"))
    inner = norm_block(rec.get("inner_oos"))

    t_from, t_to = holdout_window(rec, h, setup, symbol, iv, data)

    # --- пересчёт holdout своим движком: сверка чисел GA и заполнение того,
    #     чего GA не посчитал (t-статистики). Стоит 0.1 с, а ловит и ошибки
    #     отбора, и «плюс без значимости».
    recheck = None
    if t_from and t_to and data.has(symbol, iv):
        try:
            c4 = data.bars(symbol, iv)
            rng = bars_in_window(c4, t_from, t_to)
            if rng:
                recheck = run_block(setup, g, lev, symbol, iv, data, rng=rng)
        except Exception as e:                       # noqa: BLE001
            print(f"  пересчёт holdout не удался: {e}")
    if recheck:
        if not h:
            h = dict(recheck, source="finalize_v3")
            print(f"  holdout GA не передан — посчитан здесь: "
                  f"{h['n']} сделок, exp {h['exp_r']:+.3f}R, t {h['t']}")
        else:
            d_n = (h.get("n") or 0) - (recheck.get("n") or 0)
            d_e = round((h.get("exp_r") or 0) - (recheck.get("exp_r") or 0), 3)
            same = abs(d_n) <= 1 and abs(d_e) <= 0.01
            h["recheck"] = dict(n=recheck["n"], exp_r=recheck["exp_r"],
                                ret=recheck["ret"], t=recheck["t"],
                                d_n=d_n, d_exp_r=d_e, same=same)
            if same:
                # числа GA воспроизвелись — можно дополнить тем, чего в них нет
                for fld in ("t", "p"):
                    if h.get(fld) is None and recheck.get(fld) is not None:
                        h[fld] = recheck[fld]
                        h["t_source"] = "finalize_v3"
            else:
                h["recheck_mismatch"] = True
                print(f"  ВНИМАНИЕ: пересчёт holdout расходится с GA — "
                      f"сделок {recheck['n']} против {h.get('n')}, exp "
                      f"{recheck['exp_r']:+.3f}R против {h.get('exp_r')}R. "
                      f"Числа GA не подтверждены, торговый статус закрыт.")

    # --- обучающая часть: без неё проверка «обе половины» невозможна.
    #     Если GA её не передал, считаем сами по барам ДО начала holdout.
    if not train and t_from and data.has(symbol, iv):
        try:
            c4 = data.bars(symbol, iv)
            a_hold = bars_in_window(c4, t_from, t_to or c4[-1][0])
            if a_hold and a_hold[0] > 0:
                train = run_block(setup, g, lev, symbol, iv, data,
                                  rng=(0, a_hold[0]))
                train["source"] = "finalize_v3"
                print(f"  обучающая часть GA не передана — посчитана здесь: "
                      f"{train['n']} сделок, exp {train['exp_r']:+.3f}R")
        except Exception as e:                       # noqa: BLE001
            print(f"  обучающая часть не посчитана: {e}")

    # человекочитаемый период экзамена для сайта
    if h and not h.get("period"):
        f_ts, to_ts = h.get("from_ts") or t_from, h.get("to_ts") or t_to
        if f_ts and to_ts:
            fmt = lambda ms: time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
            h["period"] = f"{fmt(f_ts)}..{fmt(to_ts)}"

    # --- переносимость: берём у GA либо считаем сами ---
    tr_src = "ga"
    tr = {}
    raw_tr = rec.get("transfer")
    if isinstance(raw_tr, dict) and raw_tr:
        for sym, blk in raw_tr.items():
            sym = str(sym).upper()
            if not sym.endswith("USDT"):
                sym += "USDT"
            b = norm_block(blk) or dict(n=None)
            b["ok"] = bool(b.get("n") and b["n"] >= TRANSFER_MIN_N
                           and (b.get("exp_r") or 0) > 0)
            tr[sym] = b
    if not tr and do_transfer:
        if t_from and t_to:
            print(f"  переносимость (окно holdout "
                  f"{time.strftime('%Y-%m-%d', time.gmtime(t_from / 1000))}.."
                  f"{time.strftime('%Y-%m-%d', time.gmtime(t_to / 1000))}):")
            tr = calc_transfer(setup, g, lev, symbol, iv, t_from, t_to, data)
            tr_src = "finalize_v3"
        else:
            print("  переносимость не считается: окно holdout не определено "
                  "(нет from_ts/to_ts и hold_start_bar)")

    # --- пять проверок ---
    ok_a, txt_a, repro = check_repro(rec, k, counts, n_runs)
    ok_b, txt_b = check_holdout(h)
    ok_c, txt_c, edge = check_edge(rec, h, bench)
    ok_d, txt_d, n_ok, n_tested = check_transfer(tr)
    ok_e, txt_e = check_halves(train, h)
    checks = [dict(code="repro", name="воспроизводимость", ok=ok_a, txt=txt_a),
              dict(code="holdout", name="порог на holdout", ok=ok_b, txt=txt_b),
              dict(code="edge", name="лучше необученного семени", ok=ok_c,
                   txt=txt_c),
              dict(code="transfer", name="переносимость на монеты", ok=ok_d,
                   txt=txt_d),
              dict(code="halves", name="обе половины истории", ok=ok_e,
                   txt=txt_e)]
    failed = [c for c in checks if not c["ok"]]
    enabled = not failed
    watch = (not enabled and bool(h) and (h.get("exp_r") or 0) > 0
             and (h.get("n") or 0) >= WATCH_MIN_N)
    if enabled:
        verdict = "прошёл все пять проверок — торговый сетап"
    elif watch:
        verdict = ("наблюдение: на невиданных данных положителен, но полный "
                   "порог надёжности не взят")
    else:
        verdict = "не подтверждён на невиданных данных"
    reason = "; ".join(c["txt"] for c in failed)

    # --- статистика по всей истории (справочно, включает обучение) ---
    stats = None
    if do_stats and data.has(symbol, iv):
        try:
            blk = run_block(setup, g, lev, symbol, iv, data)
            blk["in_sample_warning"] = ("включает обучающий период — "
                                        "не доказательство")
            stats = blk
        except Exception as e:                       # noqa: BLE001
            print(f"  статистика по всей истории не посчитана: {e}")

    lad = [x for x in (rec.get("ladder") or []) if isinstance(x, dict)]
    lad_row = next((x for x in lad if _i(x.get("lev")) == lev), None)
    out = dict(
        key=k, setup=setup, symbol=symbol, coin=coin(symbol),
        interval_min=iv, tf_label=tf_label(iv),
        title=se3.SETUP_TITLES3.get(setup, setup),
        side=se3.setup_side(setup), long=setup.endswith("long"),
        mirror_setup=se3.MIRROR.get(setup),
        genome=g, genome_clamped=clamped, genome_unknown=unknown,
        genes_on_bound=on_bound(g),
        rec_lev=lev, enabled=enabled, watch=watch,
        verdict=verdict, reason=reason,
        checks=checks, n_checks_ok=len(checks) - len(failed),
        holdout=h, train=train, inner_oos=inner,
        benchmarks=dict(seed=bench, mirror=mirror,
                        random=(rec.get("random")
                                if isinstance(rec.get("random"), dict) else None),
                        edge_exp_r=edge),
        repro=repro,
        transfer=dict(source=tr_src, coins=tr, ok_coins=n_ok, tested=n_tested,
                      from_ts=t_from, to_ts=t_to, min_n=TRANSFER_MIN_N),
        ladder=lad, ladder_row=lad_row,
        stats=stats,
        grid=dict(levels=int(g["grid_levels"]),
                  step_atr=round(float(g["grid_step_atr"]), 3),
                  mult=round(float(g["grid_mult"]), 3)),
        exitcfg=dict(mode=int(g["tp_mode"]), tp_r=round(float(g["tp_r"]), 2),
                     tp_atr=round(float(g["tp_atr"]), 2),
                     trail_bars=int(g["trail_bars"]),
                     be_after_r=round(float(g["be_after_r"]), 2)),
        ga=rec.get("ga") if isinstance(rec.get("ga"), dict) else None,
        passed_once=bool(rec.get("passed")),
        fail_reasons=[str(x) for x in (rec.get("fail_reasons") or [])],
        caution=bool(rec.get("caution", True)),
    )
    return k, out, None


CONTRACT_EXAMPLE = {
    "breakout_short@BTCUSDT@240": {
        "setup": "breakout_short", "symbol": "BTCUSDT", "interval_min": 240,
        "genome": "<словарь генов signal_engine3.GENES3 — ОБЯЗАТЕЛЬНО>",
        "rec_lev": 10, "passed": True, "fail_reasons": [],
        "holdout": dict(n=53, wr=50.9, exp_r=0.477, sum_r=25.3, pf=1.9,
                        ret=134.9, dd=21.0, t=2.28, p=0.0225,
                        tp=27, stop=24, liq=0, timeout=2,
                        from_ts=1757203200000, to_ts=1785000000000,
                        bars=[4967, 6899]),
        "train": dict(n=175, wr=41.7, exp_r=0.2, sum_r=35.0, pf=1.4,
                      ret=128.8, dd=18.0, t=2.6),
        "holdout_benchmark": dict(n=49, wr=44.9, exp_r=0.21, ret=61.0),
        "mirror": dict(n=49, wr=20.4, exp_r=-0.43, ret=-77.2),
        "random": dict(exp_r_median=-0.02, rank_pct=87, n_random=200),
        "edge_exp_r": 0.267,
        "inner_oos": dict(n=21, wr=38.1, exp_r=0.281, pf=1.88),
        "transfer": {"ETHUSDT": dict(n=31, wr=45.2, exp_r=0.21, ret=18.4,
                                     dd=9.1, pf=1.4)},
        "ladder": [dict(lev=5, ret=60.0, dd=10.0, n=53, wr=50.9, exp_r=0.477,
                        pf=1.9)],
        "repro": dict(seeds_passed=2, of=3, seeds=[101, 202, 303]),
        "hold_start_bar": 4967, "n_bars": 6899, "seed": 47, "smoke": False,
        "ga": dict(pop=56, gens=24, elite=7, seed=47, lev=15, days=1150),
    }
}


def print_contract():
    """Что именно обязан записать GA v14 (шпаргалка для evolution14.py)."""
    print("КОНТРАКТ evolution14_winners.json — ключ «<сетап>@<МОНЕТА>@<ТФ>»:")
    print(json.dumps(CONTRACT_EXAMPLE, ensure_ascii=False, indent=2))
    print("\nМинимум: ключ + genome. Чего нет — не выдумывается: соответствующая\n"
          "проверка считается непройденной, и статус будет «наблюдение».\n"
          "holdout.t обязателен для торгового статуса (или дайте bars/from_ts —\n"
          "тогда finalize_v3 пересчитает holdout сам и посчитает t).\n"
          "Воспроизводимость: либо блок repro, либо файлы "
          f"{SEED_GLOB} от разных зёрен.")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Финализация отбора v3: winners -> setups_v3.json")
    ap.add_argument("--contract", action="store_true",
                    help="напечатать контракт входного файла и выйти")
    ap.add_argument("--winners", default=WINNERS, help="файл победителей GA")
    ap.add_argument("--out", default=OUT, help="куда писать конфиги сайта")
    ap.add_argument("--seeds", default=SEED_GLOB,
                    help="маска файлов независимых прогонов отбора")
    ap.add_argument("--no-transfer", action="store_true",
                    help="не считать переносимость самим финализатором")
    ap.add_argument("--no-stats", action="store_true",
                    help="не считать статистику по всей истории")
    ap.add_argument("--days", type=int, default=DAYS, help="глубина истории")
    a = ap.parse_args(argv)
    if a.contract:
        print_contract()
        return 0

    if not os.path.exists(a.winners):
        print(f"отбор ещё не запускался: файла {a.winners} нет.\n"
              f"Сначала прогони GA v14, затем повтори эту команду.")
        return 2
    with open(a.winners, encoding="utf-8") as fh:
        win = json.load(fh)
    if not isinstance(win, dict) or not win:
        print(f"{a.winners}: пустой или не словарь — считать нечего")
        return 2
    smoke = [k for k, v in win.items()
             if isinstance(v, dict) and v.get("smoke")]
    if smoke:
        print(f"ВНИМАНИЕ: {len(smoke)} записей от smoke-прогона "
              f"(поле smoke=true) — это не результат полного отбора.")

    counts, n_runs, files = repro_counts(a.seeds)
    print(f"воспроизводимость: независимых прогонов отбора {n_runs}"
          + (f" ({', '.join(files)})" if files else f" — файлов {a.seeds} нет")
          + f", порог для торгового статуса — {MIN_REPRO}+\n")

    data = Data(days=a.days)
    out, skipped = {}, []
    keys = [k for k in win if not str(k).startswith("__")]
    for key in sorted(keys):
        rec = win[key]
        if not isinstance(rec, dict):
            skipped.append((key, "запись не словарь"))
            continue
        print(f"{'=' * 78}\n{key}")
        k, data_rec, err = build_one(key, rec, counts, n_runs, data,
                                     not a.no_transfer, not a.no_stats)
        if err:
            skipped.append((key, err))
            print(f"  ПРОПУЩЕНО: {err}")
            continue
        out[k] = data_rec
        h = data_rec["holdout"] or {}
        mark = ("ТОРГОВЫЙ  " if data_rec["enabled"] else
                ("НАБЛЮДЕНИЕ" if data_rec["watch"] else "отключён  "))
        print(f"  {mark} {data_rec['n_checks_ok']}/5 проверок  "
              f"x{data_rec['rec_lev']} | HOLDOUT: "
              f"{h.get('n', '?')} сделок, WR {h.get('wr', '?')}%, "
              f"exp {h.get('exp_r', '?')}R, PF {h.get('pf', '?')}, "
              f"итог {h.get('ret', '?')}%, t {h.get('t', '?')}")
        for c in data_rec["checks"]:
            print(f"    [{'v' if c['ok'] else 'x'}] {c['name']}: {c['txt']}")
        if data_rec["genes_on_bound"]:
            print(f"    гены на границе диапазона: "
                  f"{', '.join(data_rec['genes_on_bound'])}")
        if data_rec["genome_clamped"]:
            print(f"    зажаты в границы: {', '.join(data_rec['genome_clamped'])}")

    n_on = sum(1 for v in out.values() if v["enabled"])
    n_w = sum(1 for v in out.values() if v["watch"])
    meta = dict(
        built_at=int(time.time()), engine="signal_engine3",
        winners_file=os.path.basename(a.winners), n=len(out),
        n_enabled=n_on, n_watch=n_w,
        repro_runs=n_runs, repro_files=files,
        thresholds=dict(MIN_REPRO=MIN_REPRO, MIN_RUNS=MIN_RUNS,
                        HOLD_MIN_N=HOLD_MIN_N, HOLD_MIN_PF=HOLD_MIN_PF,
                        HOLD_MIN_T=HOLD_MIN_T,
                        TRANSFER_MIN_N=TRANSFER_MIN_N,
                        WATCH_MIN_N=WATCH_MIN_N),
        checks=["воспроизводимость 2+ независимых прогонов",
                "порог на holdout (n, exp_r, сумма R, PF, |t|)",
                "лучше необученного семени",
                "переносимость хотя бы на одну другую монету",
                "плюс на обеих половинах истории"],
        skipped=[dict(key=k, why=w) for k, w in skipped],
        costs=dict(taker=se3.TAKER, maker=se3.MAKER, slip=se3.SLIP,
                   fund_8h=se3.FUND_8H))
    out["__meta__"] = meta
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"\n{'=' * 78}\n-> {a.out}: сетапов {len(out) - 1}, торговых {n_on}, "
          f"наблюдение {n_w}, отключено {len(out) - 1 - n_on - n_w}"
          + (f", пропущено {len(skipped)}" if skipped else ""))
    if not n_on:
        print("торговых сетапов нет — это валидный результат, а не сбой: "
              "ни один конфиг не прошёл все пять проверок.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
