# -*- coding: utf-8 -*-
"""v14: честный отбор на движке v3 — 6 сетапов x 5 монет x ТФ, со ВСТРОЕННОЙ
воспроизводимостью.

ЗАЧЕМ ЭТОТ ФАЙЛ. Двенадцать волн отбора давали красивые цифры, которые не
выживали проверку. Измерено: случайный геном проходит наш порог в 1.4%
случаев, отобранный — в 25% (значит отбор что-то находит), НО при смене
случайного зерна побеждают КАЖДЫЙ РАЗ ДРУГИЕ конфиги, пересечение пустое.
Это и есть подпись подгонки: GA находит параметры, случайно подошедшие к
отрезку, а не закономерность. evolution12 ловил это ПОСЛЕ прогона (руками,
сравнением winners_seed*.json). Здесь проверка встроена ВНУТРЬ: ни один
результат не может получить торговый статус, не показав себя на нескольких
независимых зёрнах.

ЧЕТЫРЕ НЕЗАВИСИМЫХ ЗАЩИТЫ ОТ САМООБМАНА
  1. HOLDOUT. [0..hold) — обучение И выбор кандидата (hold = 72% истории);
     [hold..n) — экзамен, которого не видят ни GA, ни отбор. Соблюдение
     проверяется не на честном слове: страж HoldoutGuard физически валит
     прогон, если в фазе обучения движку подсунут отрезок за границей hold
     (счётчики печатаются в конце).
  2. ВОСПРОИЗВОДИМОСТЬ. Каждая связка (сетап, монета, ТФ) отбирается
     НЕЗАВИСИМО с GA_SEEDS зёрнами (по умолчанию 3). Торговый статус даётся
     только тому, что прошло holdout-порог минимум в 2 зёрнах из 3. Печатается
     матрица «зерно x конфиг» и мера согласия геномов между зёрнами: если
     зёрна сходятся к разным конфигам (расстояние близко к случайному), это
     прямо написано — найдена не закономерность, а посадка на отрезок.
  3. ПЕРЕНОСИМОСТЬ. Геном, отобранный на монете A, гоняется на holdout монет
     B..E. Настоящая закономерность обязана работать не только там, где её
     нашли. Печатается матрица переносимости (строка = где отобран).
  4. БЕТА-КОНТРОЛЬ. На том же holdout считаются: необученное семя DEFAULTS3,
     два фикс-варианта из исследования идей (трейлинг-выход B3 и фильтр
     направления C1), «купил и держал» — и ЗЕРКАЛО сетапа с тем же геномом.
     Если сетап зарабатывает в ту сторону, куда шёл рынок, а зеркало теряет,
     ставится метка beta_suspect: это направленная ставка, а не преимущество.

ПОРОГ ПРОХОЖДЕНИЯ (заранее объявлен, под ответ не подбирается)
  holdout: сделок >= 25, exp_r > 0, PF >= 1.2, DD <= 30%, edge > 0 против
  ЛУЧШЕГО из R-бенчмарков; плюс воспроизводимость >= 2 зёрен из 3.
  Отрицательный результат — валидный результат и пишется прямо.

ЧТО ГДЕ СЧИТАЕТСЯ (важно, чтобы не подогнать сам критерий)
  - выбор кандидата внутри зерна — по exp_r объединённых ВНУТРЕННИХ OOS;
  - выбор представительного генома среди зёрен — тоже по внутренним OOS
    (выбирать по holdout нельзя: это была бы подгонка того же рода);
  - rec_lev (рекомендованное плечо) — по внутренним OOS;
  - лестница плечей ПЕЧАТАЕТСЯ и по holdout, но на выбор ничего не влияет.

ЗАПУСК
  полный:  python evolution14.py
           (6 сетапов x 5 монет x 3 зерна = 90 независимых отборов; замерено
           ~100-115 с на «связка-зерно» при POP=48 GENS=20 -> ~2.5-3 часа)
  smoke:   set GA_POP=8 & set GA_GENS=2 & set GA_SETUPS=breakout_long &
           set GA_SYMBOLS=BTCUSDT,ETHUSDT & python evolution14.py
  окружение: GA_POP, GA_GENS, GA_ELITE, GA_SEEDS ("11,23,47"), GA_SYMBOLS,
  GA_SETUPS, GA_TFS ("240" или "240,60"), GA_DAYS, GA_HOLD, GA_LEV, GA_OUT,
  GA_REPRO, GA_TRANSFER (0 — не считать матрицу переносимости).
  Зерно GA берётся как (зерно + CRC32 имени связки), поэтому один и тот же
  (сетап, монета, ТФ, зерно) воспроизводится и в smoke, и в полном прогоне,
  независимо от порядка и состава списка связок.

Результат: evolution14_winners.json, ключи "<setup>@<монета>@<ТФ>".
"""

import bisect
import json
import math
import os
import random
import sys
import time
import zlib

import evolution as ev
import evolution4 as e4
import signal_engine3 as se3

try:                      # русский вывод не должен падать при перенаправлении
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_list(name):
    return [s.strip() for s in os.environ.get(name, "").split(",")
            if s.strip()]


# ------------------------------------------------------------- настройки
POP = max(4, _env_int("GA_POP", 48))
GENS = max(1, _env_int("GA_GENS", 20))
ELITE = max(1, min(_env_int("GA_ELITE", 6), POP - 1))
DAYS = _env_int("GA_DAYS", 1150)
OUT = os.environ.get("GA_OUT") or "evolution14_winners.json"
HOLD_FRAC = float(os.environ.get("GA_HOLD", "0.28"))   # доля holdout
GA_LEV = _env_int("GA_LEV", 10)        # плечо, на котором идёт отбор
DO_TRANSFER = _env_int("GA_TRANSFER", 1)

SEEDS = [int(x) for x in _env_list("GA_SEEDS")] or [11, 23, 47]
REPRO_NEED = _env_int("GA_REPRO", 2 if len(SEEDS) >= 3 else len(SEEDS))
REPRO_NEED = max(1, min(REPRO_NEED, len(SEEDS)))

ALL_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT"]
SYMBOLS = _env_list("GA_SYMBOLS") or list(ALL_SYMBOLS)
SETUPS_RUN = _env_list("GA_SETUPS") or list(se3.SETUPS3)
_bad = [s for s in SETUPS_RUN if s not in se3.SETUPS3]
if _bad:
    raise SystemExit(f"GA_SETUPS: неизвестные сетапы {_bad}; "
                     f"допустимы {list(se3.SETUPS3)}")
TF_NAMES = {240: "4ч", 60: "1ч"}
TFS_RUN = [int(x) for x in _env_list("GA_TFS")] or [240]
_badtf = [t for t in TFS_RUN if t not in TF_NAMES]
if _badtf:
    raise SystemExit(f"GA_TFS: неизвестные ТФ {_badtf}; допустимы 240, 60")

# --- пороги отбора (объявлены ДО прогона) ---
MIN_HOLD_TRADES = 25       # сетка даёт больше сделок — пол выше, чем в v12
MIN_EXP_R = 0.0            # exp_r строго больше нуля
PF_MIN = 1.2
DD_MAX = 30.0
MIN_INNER_TRADES = 10      # меньше — exp_r внутренних OOS не сравниваем
MIN_BENCH_TRADES = 10      # бенчмарк с меньшей выборкой не поднимает планку
MIN_TRANSFER_TRADES = 10   # столько сделок нужно, чтобы засчитать перенос

LEVS = [5, 10, 15, 20]     # лестница плечей
CAND_PER_FOLD = 5          # уникальных лучших геномов с каждого фолда
INNER_FOLDS = 3            # даёт 2 живых фолда (третий — хвост, отбрасывается)
TOURN = 3                  # размер турнира при выборе родителей
FIT_MIN = se3.MIN_TRADES_FIT   # пол сделок трейна — движковый (25)

SMOKE = ((POP, GENS) != (48, 20) or len(SETUPS_RUN) != len(se3.SETUPS3)
         or len(SYMBOLS) != len(ALL_SYMBOLS) or len(SEEDS) < 3)


# --------------------------------------------------------- страж holdout
class HoldoutGuard:
    """Физическая гарантия, что holdout не участвует в обучении и отборе.

    Любой прогон движка идёт через него: в фазе train отрезок с правой
    границей за hold роняет прогон с ошибкой. Так «holdout не виден» —
    не обещание в комментарии, а проверяемое свойство кода."""

    def __init__(self):
        self.phase = "train"
        self.hold = None
        self.tag = ""
        self.max_b = 0
        self.n_train = 0
        self.n_exam = 0

    def arm(self, hold, tag):
        self.hold, self.tag = int(hold), tag
        self.phase = "train"

    def check(self, seg):
        a, b = int(seg[0]), int(seg[1])
        if self.phase == "train":
            self.n_train += 1
            if self.hold is not None and b > self.hold:
                raise AssertionError(
                    f"УТЕЧКА HOLDOUT [{self.tag}]: в фазе обучения запрошен "
                    f"отрезок [{a}..{b}), а граница holdout = {self.hold}")
            self.max_b = max(self.max_b, b)
        else:
            self.n_exam += 1


GUARD = HoldoutGuard()
MEMO = {}          # (символ, ТФ, геном, плечо, отрезок) -> сделки


def set_phase(p):
    GUARD.phase = p


# ------------------------------------------------------------------ данные
def agg_from_15(c15, minutes):
    """Сборка баров ТФ из 15м-свечей (когда нет кэша и нет сети).

    Группа = floor(ts / шаг). Неполные группы на краях отбрасываются, иначе
    последний бар «текущий» и его закрытие — заглядывание вперёд."""
    step = minutes * 60 * 1000
    need = minutes // 15
    out, cur, key = [], None, None
    for ts, o, h, l, c in c15:
        k = ts // step
        if k != key:
            if cur is not None and cur[5] == need:
                out.append(cur[:5])
            cur, key = [k * step, o, h, l, c, 1], k
        else:
            cur[2] = max(cur[2], h)
            cur[3] = min(cur[3], l)
            cur[4] = c
            cur[5] += 1
    if cur is not None and cur[5] == need:
        out.append(cur[:5])
    return out


def load_tf(symbol, tf, days, c15):
    """Бары сигнального ТФ: кэш проекта, если он есть, иначе сборка из 15м.
    Сеть не дёргаем — прогон должен работать офлайн и одинаково."""
    cache = f"history_{symbol}_{tf}m_{days}d.json"
    if os.path.exists(cache):
        return ev.fetch(symbol, str(tf), days), "кэш"
    return agg_from_15(c15, tf), "из 15м"


def gkey(g):
    """Ключ генома для кэшей (все гены GENES3, порядок словаря движка)."""
    return tuple(int(g[k]) if se3.GENES3[k][2] else round(float(g[k]), 4)
                 for k in se3.GENES3)


def inner_folds(hold_start):
    """anchored фолды ВНУТРИ обучающей части: трейн [0,b), внутр.OOS [b,e).
    Схема evolution11/12 — хвост короче 100 баров отбрасывается."""
    step = hold_start // (INNER_FOLDS + 1)
    out = []
    for k in range(INNER_FOLDS):
        b = step * (k + 2)
        e_ = min(hold_start, b + step)
        if e_ - b > 100:
            out.append((0, b, e_))
    return out


# --------------------------------------------------------------- прогоны
def run_trades(setup, ds, g, seg, lev):
    """Сделки одного отрезка с кэшем: победитель и бенчмарки гоняются по
    одним и тем же отрезкам много раз (внутр.OOS, holdout, лестница)."""
    seg = (int(seg[0]), int(seg[1]))
    GUARD.check(seg)
    # tag, а не symbol: у урезанного датасета самопроверки монета та же, но
    # данные другие — общий кэш дал бы «совпадение» там, где ничего не считали
    key = (ds.get("tag", ds["symbol"]), ds["tf"], gkey(g), lev, seg, setup)
    tr = MEMO.get(key)
    if tr is None:
        r = se3.run_setup(setup, g, ds["c_sig"], ds["ctx"], ds["c15"],
                          ds["ts15"], lev, symbol=ds["symbol"],
                          signal_range=seg, collect_diag=False,
                          interval_min=ds["tf"])
        tr = r["trades"]
        MEMO[key] = tr
    return tr


def tstat(vals):
    """t-статистика среднего (нулевая гипотеза: среднее = 0)."""
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    var = sum((x - m) ** 2 for x in vals) / (n - 1)
    if var <= 0:
        return 0.0
    return m / math.sqrt(var / n)


def pooled(setup, ds, g, segments, lev):
    """Метрики по объединённым сделкам нескольких отрезков."""
    tr = []
    for seg in segments:
        tr += run_trades(setup, ds, g, seg, lev)
    n = len(tr)
    if not n:
        return dict(n=0, wr=0.0, exp_r=0.0, sum_r=0.0, pf=None, dd=0.0,
                    ret=0.0, t=0.0, tp=0, stop=0, liq=0, be=0, trail=0,
                    grid=0.0, be_no_stop=0)
    wins = [t for t in tr if t["pnl"] > 0]
    gp = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in tr if t["pnl"] < 0)
    bal, peak, dd = se3.START, se3.START, 0.0
    for t in sorted(tr, key=lambda x: x["exit_ts"]):
        bal += t["pnl"]
        peak = max(peak, bal)
        dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
    be_ex = [t for t in tr if t["be_exit"]]
    return dict(
        n=n, wr=round(len(wins) / n * 100, 1),
        exp_r=round(sum(t["r"] for t in tr) / n, 3),
        sum_r=round(sum(t["r"] for t in tr), 2),
        pf=round(gp / gl, 2) if gl > 0 else None,
        dd=round(dd * 100, 1),
        ret=round((bal / se3.START - 1) * 100, 1),
        t=round(tstat([t["r"] for t in tr]), 2),
        tp=sum(1 for t in tr if t["exit_kind"] == "tp"),
        stop=sum(1 for t in tr if t["exit_kind"] == "stop"),
        liq=sum(1 for t in tr if t["exit_kind"] == "liq"),
        be=len(be_ex),
        be_no_stop=sum(1 for t in be_ex if not t["orig_stop_touched"]),
        trail=sum(1 for t in tr if t["exit_kind"] == "trail"),
        grid=round(sum(t["grid_fills"] for t in tr) / n, 2))


def verdict(h, edge):
    """Порог holdout. PF=None означает ноль убыточных сделок — при живой
    выборке с плюсом это «бесконечный» PF, а не провал."""
    pf_ok = ((h["pf"] >= PF_MIN) if h["pf"] is not None
             else (h["n"] > 0 and h["exp_r"] > 0))
    why = []
    if h["n"] < MIN_HOLD_TRADES:
        why.append(f"сделок {h['n']} < {MIN_HOLD_TRADES}")
    if h["exp_r"] <= MIN_EXP_R:
        why.append(f"exp_r {h['exp_r']:+.3f} не > 0")
    if not pf_ok:
        why.append(f"PF {h['pf']} < {PF_MIN}")
    if h["dd"] > DD_MAX:
        why.append(f"DD {h['dd']}% > {DD_MAX}%")
    if edge <= 0:
        why.append(f"нет преимущества над бенчмарком ({edge:+.3f}R)")
    ok = (h["n"] >= MIN_HOLD_TRADES and h["exp_r"] > MIN_EXP_R and pf_ok
          and h["dd"] <= DD_MAX and edge > 0)
    return bool(ok), why


# ------------------------------------------------------------------- GA
def rank_key(row):
    """Сначала добравшие пол FIT_MIN сделок (между собой — по фитнесу),
    затем не добравшие (по числу сделок)."""
    f, n, _g = row
    ok = n >= FIT_MIN
    return (1 if ok else 0, f if ok else 0.0, n)


def ga_seeds(clamp):
    """Стартовые геномы популяции: необученное семя и по одному варианту на
    каждую НОВУЮ способность движка v3 (сетка, трейлинг, фильтр тренда) —
    иначе GA почти не пробует их включать."""
    out = [clamp(se3.default_genome())]
    out.append(clamp(se3.default_genome(grid_levels=2, grid_step_atr=1.0,
                                        grid_mult=1.5)))
    out.append(clamp(se3.default_genome(tp_mode=2, trail_bars=60)))
    out.append(clamp(se3.default_genome(trend_gate=1, trend_len=200)))
    return out


def evolve(setup, ds, fold, prefix):
    """GA на трейне фолда: (фитнес, сделок, геном), лучшие первыми."""
    rand_g, clamp, mutate, cross = e4.ga_tools(se3.GENES3)
    a, b = fold
    cache = {}
    t0 = time.time()

    def eval_g(g):
        key = gkey(g)
        got = cache.get(key)
        if got is None:
            GUARD.check((a, b))
            r = se3.run_setup(setup, g, ds["c_sig"], ds["ctx"], ds["c15"],
                              ds["ts15"], GA_LEV, symbol=ds["symbol"],
                              signal_range=(a, b), collect_diag=False,
                              interval_min=ds["tf"])
            got = (se3.fitness(r), len(r["trades"]))
            cache[key] = got
        return got

    def score(pop):
        return sorted(((eval_g(g)[0], eval_g(g)[1], g) for g in pop),
                      key=rank_key, reverse=True)

    pop = [dict(s) for s in ga_seeds(clamp)]
    while len(pop) < POP:
        pop.append(rand_g())
    scored = score(pop)
    step = max(1, GENS // 4)
    for gen in range(GENS):
        new = [g for _f, _n, g in scored[:ELITE]]
        while len(new) < POP:
            if random.random() < 0.3:
                new.append(mutate(scored[random.randrange(ELITE)][2]))
            else:
                k = min(TOURN, len(scored))
                p1 = max(random.sample(scored, k), key=rank_key)[2]
                p2 = max(random.sample(scored, k), key=rank_key)[2]
                new.append(mutate(cross(p1, p2)))
        scored = score(new)
        if (gen + 1) % step == 0 or gen == GENS - 1:
            alive = sum(1 for _f, n, _ in scored if n >= FIT_MIN)
            mark = "" if scored[0][1] >= FIT_MIN else "  (пол не пройден!)"
            print(f"      {prefix} поколение {gen+1:2}: фитнес "
                  f"{scored[0][0]:+8.2f} | сделок {scored[0][1]:3} | с полом "
                  f"{FIT_MIN}+ {alive:2}/{POP} | кэш {len(cache):4} | "
                  f"{time.time()-t0:.0f}с{mark}")
    return scored


# ------------------------------------------------------- печать генома
GENE_GROUPS14 = (
    ("общее", ("rsi_idx", "window", "cooldown", "hold_days")),
    ("направление", ("trend_gate", "trend_len")),
    ("пробой", ("break_days", "break_buf_atr")),
    ("пулбэк", ("pull_slow", "pull_fast", "pull_max_atr")),
    ("слив/перегрев", ("drop_frac", "drop_days", "rsi_os", "need_candle")),
    ("режимы", ("reg_bull", "reg_range", "reg_bear")),
    ("стоп", ("stop_mode", "swing_bars", "stop_atr_k", "buf_atr", "stop_cap")),
    ("сетка", ("grid_levels", "grid_step_atr", "grid_mult")),
    ("выход", ("tp_mode", "tp_r", "tp_atr", "trail_bars", "be_after_r")),
    ("шторм", ("storm_gate", "storm_day", "storm_week", "storm_atr_rank",
               "storm_mode")),
    ("фандинг/aroon", ("fund_gate", "fund_thr", "aroon_gate", "aroon_n",
                       "aroon_thr")),
)
_covered = {k for _t, ks in GENE_GROUPS14 for k in ks}
assert not (set(se3.GENES3) - _covered) and not (_covered - set(se3.GENES3)), (
    f"печать генома отстала от GENES3: не описаны "
    f"{sorted(set(se3.GENES3) - _covered)}, лишние "
    f"{sorted(_covered - set(se3.GENES3))}")

STOP_MODES = ("окно", "свинг", "k*ATR", "фитиль")
TP_MODES = ("tp_r*риск", "tp_atr*ATR", "трейлинг")


def gene_active(setup, g, key):
    """Влияет ли ген на поведение этого сетапа с этим геномом."""
    fam = setup.split("_")[0]
    mode = int(g["stop_mode"])
    tpm = int(g["tp_mode"])
    if key in ("break_days", "break_buf_atr"):
        return fam == "breakout"
    if key in ("pull_slow", "pull_fast", "pull_max_atr"):
        return fam == "pullback"
    if key in ("drop_frac", "drop_days", "rsi_os", "rsi_idx"):
        return fam in ("dump", "rally")
    if key == "need_candle":
        return fam != "breakout"
    if key == "trend_len":
        return int(g["trend_gate"]) == 1
    if key == "buf_atr":
        return mode in (0, 1, 3)
    if key == "swing_bars":
        return mode == 1
    if key == "stop_atr_k":
        return mode == 2
    if key in ("grid_step_atr", "grid_mult"):
        return int(g["grid_levels"]) > 1
    if key == "tp_r":
        return tpm == 0
    if key == "tp_atr":
        return tpm == 1
    if key == "trail_bars":
        return tpm == 2
    if key in ("storm_day", "storm_week", "storm_atr_rank", "storm_mode"):
        return int(g["storm_gate"]) == 1
    if key == "fund_thr":
        return int(g["fund_gate"]) == 1
    if key in ("aroon_n", "aroon_thr"):
        return int(g["aroon_gate"]) == 1
    return True


def fmt_gene(setup, g, k):
    _lo, _hi, is_int = se3.GENES3[k]
    v = int(g[k]) if is_int else float(g[k])
    if k == "rsi_idx":
        txt = f"{v} (RSI{se3.RSI_SET[v]})"
    elif k == "stop_mode":
        txt = f"{v} ({STOP_MODES[v]})"
    elif k == "tp_mode":
        txt = f"{v} ({TP_MODES[v]})"
    elif k in ("reg_bull", "reg_range", "reg_bear"):
        txt = "разрешён" if v else "ЗАПРЕЩЁН"
    elif k in ("trend_gate", "need_candle", "storm_gate", "fund_gate",
               "aroon_gate"):
        txt = "ВКЛ" if v else "выкл"
    elif k in ("stop_cap", "drop_frac"):
        txt = f"{v*100:.2f}%"
    elif k == "fund_thr":
        txt = f"{v:.3f}%/8ч"
    elif is_int:
        txt = str(v)
    else:
        txt = f"{v:.3f}"
    return txt + ("" if gene_active(setup, g, k) else " [не исп.]")


def print_genome(setup, g, pad="    "):
    for title, keys in GENE_GROUPS14:
        parts = [f"{k}={fmt_gene(setup, g, k)}" for k in keys]
        print(f"{pad}{title:14} " + "; ".join(parts))


def genome_sig(g):
    """Компактная подпись генома для матрицы «зерно x конфиг»."""
    return (f"br{int(g['break_days'])}/{g['break_buf_atr']:.2f} "
            f"pl{int(g['pull_slow'])}/{int(g['pull_fast'])} "
            f"dr{g['drop_frac']*100:.1f}%/{int(g['drop_days'])}д/"
            f"rsi{int(g['rsi_os'])} "
            f"st{int(g['stop_mode'])}:{g['stop_atr_k']:.1f}/"
            f"{g['stop_cap']*100:.1f}% "
            f"gr{int(g['grid_levels'])}x{g['grid_step_atr']:.1f}/"
            f"{g['grid_mult']:.1f} "
            f"tp{int(g['tp_mode'])}:{g['tp_r']:.1f}/{g['tp_atr']:.1f}/"
            f"{int(g['trail_bars'])} "
            f"be{g['be_after_r']:.1f} tg{int(g['trend_gate'])} "
            f"hd{int(g['hold_days'])} cd{int(g['cooldown'])}")


def gene_dist(a, b):
    """Нормированное расстояние между геномами: среднее по генам
    |a-b|/(max-min). 0 — идентичны, ~случайная пара — см. REF_DIST."""
    s = 0.0
    for k, (lo, hi, _ii) in se3.GENES3.items():
        rng = hi - lo
        s += abs(float(a[k]) - float(b[k])) / rng if rng else 0.0
    return s / len(se3.GENES3)


def ref_dist(n=400, seed=7):
    """Эталон: среднее расстояние между СЛУЧАЙНЫМИ геномами. Если зёрна
    расходятся примерно на столько же — согласия нет вообще."""
    st = random.getstate()
    random.seed(seed)
    rand_g, _c, _m, _x = e4.ga_tools(se3.GENES3)
    s = sum(gene_dist(rand_g(), rand_g()) for _ in range(n)) / n
    random.setstate(st)
    return s


def agree_genes(gs):
    """Гены, по которым ВСЕ отобранные геномы согласны (целые — точно,
    дробные — в пределах 2% диапазона)."""
    out = []
    for k, (lo, hi, is_int) in se3.GENES3.items():
        vals = [g[k] for g in gs]
        if is_int:
            if len(set(int(v) for v in vals)) == 1:
                out.append(f"{k}={int(vals[0])}")
        elif max(vals) - min(vals) <= 0.02 * (hi - lo):
            out.append(f"{k}={float(vals[0]):.3f}")
    return out


# -------------------------------------------------- проверка на заглядывание
def selfcheck_prefix(setup, ds, g, b):
    """«Префикс против полной истории»: сделки на отрезке [0..b), посчитанные
    по ПОЛНЫМ массивам, обязаны совпасть со сделками на массивах, УРЕЗАННЫХ
    по бару b. Проверяет причинность контекста (SMA/ATR/режим/трейлинг) и
    правильность моей сборки данных, а не только движка."""
    c_sig, c15, ts15, tf, sym = (ds["c_sig"], ds["c15"], ds["ts15"],
                                 ds["tf"], ds["symbol"])
    full = run_trades(setup, ds, g, (0, b), GA_LEV)
    cut_ts = c_sig[b][0]
    k15 = bisect.bisect_left(ts15, cut_ts)
    ds_p = dict(symbol=sym, tag=f"{sym}~префикс{b}", tf=tf, c_sig=c_sig[:b],
                c15=c15[:k15], ts15=ts15[:k15],
                ctx=se3.prep_context(c_sig[:b], tf, sym))
    part = run_trades(setup, ds_p, g, (0, b), GA_LEV)
    last = ds_p["ts15"][-1]
    kk = 0
    while kk < len(part) and part[kk]["exit_ts"] < last:
        kk += 1
    bad = 0
    for x, y in zip(part[:kk], full[:kk]):
        if (x["entry_ts"] != y["entry_ts"] or x["exit_ts"] != y["exit_ts"]
                or abs(x["pnl"] - y["pnl"]) > 1e-9
                or abs(x["stop"] - y["stop"]) > 1e-9):
            bad += 1
    return kk, bad, len(full)


# ------------------------------------------------------------------ отбор
def select_one_seed(setup, ds, seed, prefix):
    """Полный независимый отбор ОДНИМ зерном: GA на трейнах внутренних
    фолдов -> пул кандидатов -> победитель по внутренним OOS. Holdout здесь
    не трогается вообще (страж это гарантирует)."""
    name = f"{setup}@{ds['symbol']}@{ds['tf']}"
    random.seed(seed + zlib.crc32(name.encode("utf-8")))
    set_phase("train")
    GUARD.arm(ds["hold"], f"{name} зерно {seed}")
    cands, seen_all = [], set()
    for fi, (a, b, _e) in enumerate(ds["folds"]):
        scored = evolve(setup, ds, (a, b), f"{prefix}ф{fi+1}")
        seen = set()
        for fit, n_tr, g in scored:
            k = gkey(g)
            if k in seen:
                continue
            seen.add(k)
            if k not in seen_all:
                seen_all.add(k)
                cands.append(dict(g=g, src=fi + 1, fit=fit, train_n=n_tr))
            if len(seen) >= CAND_PER_FOLD:
                break
    inner_segs = [(b, e_) for (_a, b, e_) in ds["folds"]]
    ranked = []
    for cnd in cands:
        p = pooled(setup, ds, cnd["g"], inner_segs, GA_LEV)
        ranked.append((p["exp_r"] if p["n"] >= MIN_INNER_TRADES else -99.0,
                       p["n"], p, cnd))
    ranked.sort(key=lambda x: (-x[0], -x[1]))
    _sc, _n, inner, best = ranked[0]
    # rec_lev — по ВНУТРЕННИМ OOS (по holdout нельзя: это подгонка)
    lad_in = []
    for lev in LEVS:
        p = pooled(setup, ds, best["g"], inner_segs, lev)
        lad_in.append(dict(lev=lev, ret=p["ret"], dd=p["dd"], n=p["n"],
                           wr=p["wr"], exp_r=p["exp_r"], pf=p["pf"]))
    rec = None
    for row in lad_in[::-1]:
        if row["dd"] <= DD_MAX and row["ret"] > 0:
            rec = row["lev"]
            break
    caution = rec is None
    if rec is None:
        rec = LEVS[0]
    return dict(seed=seed, genome=best["g"], src_fold=best["src"],
                train_fitness=round(best["fit"], 2), train_n=best["train_n"],
                inner=inner, n_cands=len(cands), rec_lev=rec,
                caution=caution, ladder_inner=lad_in)


def benchmarks(setup, ds):
    """Бенчмарки holdout: необученное семя и два фикс-варианта из
    исследования идей (единственные два блока, за которыми ИЗМЕРЕНА
    информация), плюс «купил и держал» в сторону сетапа."""
    seg = [(ds["hold"], ds["n"])]
    out = {}
    for tag, g in (("семя DEFAULTS3", se3.default_genome()),
                   ("семя+трейлинг (B3)", se3.default_genome(tp_mode=2,
                                                             trail_bars=60)),
                   ("семя+фильтр тренда (C1)",
                    se3.default_genome(trend_gate=1, trend_len=200))):
        out[tag] = pooled(setup, ds, g, seg, GA_LEV)
    c = ds["c_sig"]
    bh = (c[-1][4] / c[ds["hold"]][4] - 1) * 100.0
    out["bh_ret"] = round(bh, 1)
    out["bh_dir_ret"] = round(bh if se3.setup_side(setup) == "L" else -bh, 1)
    live = [(t, v["exp_r"]) for t, v in out.items()
            if isinstance(v, dict) and v["n"] >= MIN_BENCH_TRADES]
    if live:
        best_tag, best_exp = max(live, key=lambda x: x[1])
    else:
        best_tag, best_exp = "семя DEFAULTS3", out["семя DEFAULTS3"]["exp_r"]
    out["_best_tag"], out["_best_exp"] = best_tag, best_exp
    return out


def run_combo(setup, ds):
    """Связка (сетап, монета, ТФ): отбор всеми зёрнами, экзамен, матрица
    «зерно x конфиг», бенчмарки, зеркало, вердикт по воспроизводимости."""
    t0 = time.time()
    name = f"{setup}@{ds['symbol']}@{ds['tf']}"
    MEMO.clear()
    print("\n" + "=" * 100)
    print(f"{name}   {se3.SETUP_TITLES3[setup]}   ({TF_NAMES[ds['tf']]}, "
          f"holdout [{ds['hold']}..{ds['n']}))")
    print("=" * 100)

    seeds_out = []
    for seed in SEEDS:
        print(f"  -- зерно {seed} --")
        pref = f"{setup[:9]}@{ds['symbol'][:3]}/{seed}/"
        row = select_one_seed(setup, ds, seed, pref)
        seeds_out.append(row)

    # --- экзамен: сюда holdout попадает ВПЕРВЫЕ ---
    set_phase("exam")
    bench = benchmarks(setup, ds)
    hold_seg = [(ds["hold"], ds["n"])]
    for row in seeds_out:
        h = pooled(setup, ds, row["genome"], hold_seg, GA_LEV)
        row["holdout"] = h
        row["edge_exp_r"] = round(h["exp_r"] - bench["_best_exp"], 3)
        ok, why = verdict(h, row["edge_exp_r"])
        row["passed"], row["fail_reasons"] = ok, why
        row["ladder_holdout"] = [
            dict(lev=lev, **{k: v for k, v in
                             pooled(setup, ds, row["genome"], hold_seg,
                                    lev).items()
                             if k in ("ret", "dd", "n", "wr", "exp_r", "pf")})
            for lev in LEVS]

    n_pass = sum(1 for r in seeds_out if r["passed"])
    repro_ok = n_pass >= REPRO_NEED

    # --- матрица «зерно x конфиг» ---
    print(f"  МАТРИЦА «ЗЕРНО x КОНФИГ» (порог: сделок>={MIN_HOLD_TRADES}, "
          f"exp_r>0, PF>={PF_MIN}, DD<={DD_MAX:.0f}%, edge>0)")
    print(f"  {'зерно':>6} | {'внутр.OOS':>16} | {'holdout: сдел':>13} "
          f"{'WR%':>5} {'exp_r':>7} {'t':>5} {'PF':>5} {'DD%':>5} "
          f"{'итог%':>7} {'edge':>7} | порог")
    for row in seeds_out:
        i_, h = row["inner"], row["holdout"]
        print(f"  {row['seed']:6} | {i_['n']:5} шт {i_['exp_r']:+7.3f}R | "
              f"{h['n']:13} {h['wr']:5.1f} {h['exp_r']:+7.3f} {h['t']:+5.2f} "
              f"{str(h['pf']):>5} {h['dd']:5.1f} {h['ret']:+7.1f} "
              f"{row['edge_exp_r']:+7.3f} | "
              f"{'ДА' if row['passed'] else 'нет'}")
    for row in seeds_out:
        print(f"    геном зерна {row['seed']:>3} (фолд {row['src_fold']}, "
              f"фитнес трейна {row['train_fitness']:+.2f} на "
              f"{row['train_n']} сделках): {genome_sig(row['genome'])}")
    gs = [r["genome"] for r in seeds_out]
    if len(gs) >= 2:
        pairs = [gene_dist(gs[i], gs[j])
                 for i in range(len(gs)) for j in range(i + 1, len(gs))]
        spread = sum(pairs) / len(pairs)
        same = agree_genes(gs)
        note = ("зёрна сошлись" if spread < 0.5 * REF_DIST
                else "СОГЛАСИЯ НЕТ: зёрна разошлись почти как случайные")
        print(f"  СОГЛАСИЕ ЗЁРЕН: среднее расстояние геномов {spread:.3f} "
              f"(у случайных пар {REF_DIST:.3f}) -> {note}")
        print(f"    совпало генов у всех зёрен: {len(same)} из "
              f"{len(se3.GENES3)}" + (": " + ", ".join(same[:12])
                                      if same else ""))
    else:
        spread, same = 0.0, []

    # --- представитель: по ВНУТРЕННИМ OOS среди ВСЕХ зёрен ---
    # Раньше пул сначала фильтровался по r["passed"], а passed присваивается
    # ПО HOLDOUT — то есть представитель (его геном, плечо и отчётные
    # holdout-числа) отбирался с оглядкой на экзамен, и отчёт смещался вверх,
    # хотя печаталось «не по holdout». Это ровно тот класс самообмана, ради
    # которого построена вся проверка, поэтому фильтр убран.
    rep = max(seeds_out, key=lambda r: (r["inner"]["exp_r"], r["inner"]["n"]))
    print(f"  ПРЕДСТАВИТЕЛЬ: зерно {rep['seed']} (выбран по внутренним OOS "
          f"{rep['inner']['exp_r']:+.3f}R на {rep['inner']['n']} сделках — "
          f"НЕ по holdout)")

    # --- бенчмарки и бета-контроль ---
    print("  БЕНЧМАРКИ на holdout:")
    for tag in ("семя DEFAULTS3", "семя+трейлинг (B3)",
                "семя+фильтр тренда (C1)"):
        b_ = bench[tag]
        print(f"    {tag:26} {b_['n']:4} сделок | WR {b_['wr']:5.1f}% | exp "
              f"{b_['exp_r']:+.3f}R | PF {str(b_['pf']):>5} | итог "
              f"{b_['ret']:+7.1f}%")
    print(f"    {'купил и держал':26} итог {bench['bh_ret']:+7.1f}% | в "
          f"сторону сетапа {bench['bh_dir_ret']:+.1f}%")
    print(f"    планка edge: {bench['_best_tag']} "
          f"({bench['_best_exp']:+.3f}R)")

    mirror = se3.MIRROR[setup]
    mir = pooled(mirror, ds, rep["genome"], hold_seg, GA_LEV)
    h_rep = rep["holdout"]
    beta = bool(h_rep["exp_r"] > 0 and bench["bh_dir_ret"] > 0
                and mir["exp_r"] <= 0)
    print(f"  ЗЕРКАЛО {mirror} с тем же геномом: {mir['n']} сделок, exp "
          f"{mir['exp_r']:+.3f}R, итог {mir['ret']:+.1f}%"
          + ("   -> ПОДОЗРЕНИЕ НА БЕТУ: сетап зарабатывает туда, куда шёл "
             "рынок, а зеркало теряет" if beta else ""))

    print("  ЛЕСТНИЦА ПЛЕЧЕЙ представителя (holdout — только для сведения):")
    for row in rep["ladder_holdout"]:
        print(f"    x{row['lev']:<3} итог {row['ret']:+8.1f}% | DD "
              f"{row['dd']:5.1f}% | {row['n']:3} сделок | WR "
              f"{row['wr']:5.1f}% | PF {str(row['pf']):>5}")
    print(f"    rec_lev = x{rep['rec_lev']} (посчитан по ВНУТРЕННИМ OOS)"
          + ("  (осторожно: ни одно плечо не дало DD<=30% с плюсом)"
             if rep["caution"] else ""))

    ok_txt = ("ПРОШЁЛ" if repro_ok else "НЕ ПРОШЁЛ")
    print(f"  ВЕРДИКТ: {ok_txt} — порог holdout выдержали {n_pass} зёрен из "
          f"{len(SEEDS)} (нужно {REPRO_NEED})")
    if not repro_ok:
        seen_why = []
        for row in seeds_out:
            for w in row["fail_reasons"]:
                if w.split()[0] not in [x.split()[0] for x in seen_why]:
                    seen_why.append(w)
        print("    причины: " + "; ".join(seen_why))
    print("  ГЕНОМ ПРЕДСТАВИТЕЛЯ:")
    print_genome(setup, rep["genome"])
    set_phase("train")

    return dict(
        setup=setup, symbol=ds["symbol"], interval_min=ds["tf"],
        tf_name=TF_NAMES[ds["tf"]], genome=rep["genome"],
        rep_seed=rep["seed"], passed=bool(repro_ok),
        repro_passed=n_pass, repro_need=REPRO_NEED, n_seeds=len(SEEDS),
        seeds=[dict(seed=r["seed"], genome=r["genome"], inner=r["inner"],
                    holdout=r["holdout"], edge_exp_r=r["edge_exp_r"],
                    passed=r["passed"], fail_reasons=r["fail_reasons"],
                    src_fold=r["src_fold"], train_fitness=r["train_fitness"],
                    train_n=r["train_n"], rec_lev=r["rec_lev"],
                    caution=r["caution"], ladder_inner=r["ladder_inner"],
                    ladder_holdout=r["ladder_holdout"],
                    genome_sig=genome_sig(r["genome"]))
               for r in seeds_out],
        genome_spread=round(spread, 3),
        genome_spread_random=round(REF_DIST, 3),
        genes_agreed=same, holdout=h_rep, inner_oos=rep["inner"],
        benchmarks={k: v for k, v in bench.items() if not k.startswith("_")},
        best_bench=bench["_best_tag"], edge_exp_r=rep["edge_exp_r"],
        mirror_setup=mirror, mirror_holdout=mir, beta_suspect=beta,
        rec_lev=rep["rec_lev"], caution=rep["caution"],
        ladder=rep["ladder_holdout"], hold_start_bar=ds["hold"],
        n_bars=ds["n"], smoke=SMOKE,
        ga=dict(pop=POP, gens=GENS, elite=ELITE, seeds=SEEDS, lev=GA_LEV,
                days=DAYS, hold_frac=HOLD_FRAC,
                folds=[list(f) for f in ds["folds"]]),
        elapsed_s=round(time.time() - t0, 1))


def transfer_matrix(setup, tf, dsets, records):
    """Матрица переносимости: геном, отобранный на монете A, — на holdout
    монет B..E. Самый сильный тест на подгонку: закономерность обязана
    работать не только там, где её нашли."""
    syms = [s for s in SYMBOLS if f"{setup}@{s}@{tf}" in records]
    if len(syms) < 2:
        return
    set_phase("exam")
    print(f"\n  ПЕРЕНОСИМОСТЬ {setup} ({TF_NAMES[tf]}), holdout, плечо "
          f"x{GA_LEV}: exp_r (сделок). Строка = где геном отобран, "
          f"* = своя монета")
    head = "  {:<12}".format("отобран на") + "".join(
        f"{s[:-4]:>15}" for s in syms) + "   чужих с +"
    print(head)
    for a in syms:
        rec = records[f"{setup}@{a}@{tf}"]
        g = rec["genome"]
        cells, pos, tot = [], 0, 0
        row_out = {}
        for b in syms:
            p = pooled(setup, dsets[(b, tf)], g,
                       [(dsets[(b, tf)]["hold"], dsets[(b, tf)]["n"])], GA_LEV)
            row_out[b] = dict(n=p["n"], exp_r=p["exp_r"], wr=p["wr"],
                              pf=p["pf"], ret=p["ret"])
            mark = "*" if b == a else " "
            cells.append(f"{p['exp_r']:+7.3f}({p['n']:3}){mark}")
            if b != a:
                tot += 1
                if p["n"] >= MIN_TRANSFER_TRADES and p["exp_r"] > 0:
                    pos += 1
        rec["transfer"] = row_out
        rec["transfer_pos"] = pos
        rec["transfer_total"] = tot
        print("  {:<12}".format(a[:-4]) + "".join(f"{c:>15}" for c in cells)
              + f"   {pos}/{tot}")
    print(f"    (перенос засчитан при сделок>={MIN_TRANSFER_TRADES} и "
          f"exp_r>0; своя монета в счёт не идёт)")
    set_phase("train")


# ------------------------------------------------------------------- main
def d(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def mon(bars, tf):
    return bars * tf / 1440.0 / 30.44


REF_DIST = ref_dist()


def main():
    t_all = time.time()
    print("=" * 100)
    print("evolution14: честный отбор на движке v3 (сетка, свободный RR, "
          "мультимонета) со встроенной воспроизводимостью")
    print("holdout не видят ни GA, ни отбор (страж роняет прогон при "
          "нарушении); статус — только при 2 зёрнах из 3")
    print("=" * 100)

    dsets = {}
    for sym in SYMBOLS:
        c15 = ev.fetch(sym, "15", DAYS)
        ts15 = [c[0] for c in c15]
        for tf in TFS_RUN:
            c_sig, src = load_tf(sym, tf, DAYS, c15)
            ctx = se3.prep_context(c_sig, tf, sym)
            n = len(c_sig)
            hold = int(n * (1 - HOLD_FRAC))
            folds = inner_folds(hold)
            dsets[(sym, tf)] = dict(symbol=sym, tf=tf, c_sig=c_sig, ctx=ctx,
                                    c15=c15, ts15=ts15, n=n, hold=hold,
                                    folds=folds)
            reg_h = ctx["regime"][hold:]
            bh = (c_sig[-1][4] / c_sig[hold][4] - 1) * 100
            print(f"{sym} {TF_NAMES[tf]} ({src}): баров {n} "
                  f"({d(c_sig[0][0])}..{d(c_sig[-1][0])}) | 15м {len(c15)} | "
                  f"обучение [0..{hold}) {mon(hold, tf):.1f} мес | HOLDOUT "
                  f"[{hold}..{n}) {mon(n - hold, tf):.1f} мес "
                  f"({d(c_sig[hold][0])}..{d(c_sig[-1][0])}) B&H {bh:+.1f}%")
            if sym == SYMBOLS[0]:
                print(f"   режимы holdout: bull "
                      f"{reg_h.count(0)*100//len(reg_h)}%, range "
                      f"{reg_h.count(1)*100//len(reg_h)}%, bear "
                      f"{reg_h.count(2)*100//len(reg_h)}% | фолды: "
                      + "; ".join(f"трейн[0..{b}) -> OOS[{b}..{e_})"
                                  for _a, b, e_ in folds))

    print(f"\nGA: POP={POP} GENS={GENS} ELITE={ELITE} | зёрна {SEEDS} "
          f"(нужно {REPRO_NEED} из {len(SEEDS)}) | отбор на x{GA_LEV} | "
          f"пол трейна {FIT_MIN} сделок")
    print(f"сетапы: {', '.join(SETUPS_RUN)}")
    print(f"монеты: {', '.join(SYMBOLS)} | ТФ: "
          f"{', '.join(TF_NAMES[t] for t in TFS_RUN)} | связок "
          f"{len(SETUPS_RUN)*len(SYMBOLS)*len(TFS_RUN)}")
    print(f"порог holdout: сделок>={MIN_HOLD_TRADES}, exp_r>0, PF>={PF_MIN}, "
          f"DD<={DD_MAX:.0f}%, edge>0 против лучшего бенчмарка")
    if SMOKE:
        print("!!! SMOKE-РЕЖИМ (POP/GENS/сетапы/монеты/зёрна урезаны): "
              "результат черновой, в JSON пометка smoke=true")
    print(f"вывод: {OUT}")

    # --- самопроверка 1: страж holdout не декоративный ---
    probe = HoldoutGuard()
    probe.arm(100, "проверка стража")
    fired = False
    try:
        probe.check((0, 101))
    except AssertionError:
        fired = True
    probe.check((0, 100))          # ровно до границы — законно
    if not fired:
        raise SystemExit("страж holdout НЕ сработал — защита не работает")
    print("\nСАМОПРОВЕРКА стража holdout: отрезок [0..101) при границе 100 в "
          "фазе обучения отклонён, [0..100) пропущен — защита рабочая")

    # --- самопроверка 2: нет заглядывания в МОЕЙ сборке данных ---
    print("САМОПРОВЕРКА «префикс против полной истории» "
          "(сделки на [0..b) не должны зависеть от баров после b):")
    set_phase("exam")          # проверка гоняет отрезки вне фазы обучения
    tot_cmp = tot_bad = 0
    for sym in SYMBOLS[:2]:
        for tf in TFS_RUN:
            ds = dsets[(sym, tf)]
            for setup, g in (("breakout_long", se3.default_genome()),
                             ("pullback_short",
                              se3.default_genome(grid_levels=3, tp_mode=2,
                                                 trail_bars=40,
                                                 be_after_r=0.5))):
                k, bad, nf = selfcheck_prefix(setup, ds, g,
                                              int(ds["n"] * 0.6))
                tot_cmp += k
                tot_bad += bad
                print(f"  {sym} {TF_NAMES[tf]} {setup:15} сверено сделок "
                      f"{k:3} (из {nf}) — расхождений {bad}")
    if tot_bad:
        raise SystemExit(f"ЗАГЛЯДЫВАНИЕ: {tot_bad} расхождений из {tot_cmp}")
    print(f"  ИТОГ: сверено {tot_cmp} сделок, расхождений 0 — данные и "
          f"контекст причинные")
    set_phase("train")
    MEMO.clear()

    records = {}
    for setup in SETUPS_RUN:
        for tf in TFS_RUN:
            for sym in SYMBOLS:
                rec = run_combo(setup, dsets[(sym, tf)])
                records[f"{setup}@{sym}@{tf}"] = rec
            if DO_TRANSFER:
                transfer_matrix(setup, tf, dsets, records)

    meta = dict(
        smoke=SMOKE, pop=POP, gens=GENS, elite=ELITE, seeds=SEEDS,
        repro_need=REPRO_NEED, ga_lev=GA_LEV, levs=LEVS, days=DAYS,
        hold_frac=HOLD_FRAC, symbols=SYMBOLS, setups=SETUPS_RUN, tfs=TFS_RUN,
        thresholds=dict(min_trades=MIN_HOLD_TRADES, exp_r=">0", pf=PF_MIN,
                        dd_max=DD_MAX, edge=">0", repro=REPRO_NEED),
        guard=dict(train_runs=GUARD.n_train, exam_runs=GUARD.n_exam,
                   max_train_bar=GUARD.max_b),
        engine="signal_engine3", elapsed_s=round(time.time() - t_all, 1))
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dict(_meta=meta, **records), fh, ensure_ascii=False,
                  indent=2, default=float)

    # ------------------------------------------------------------- итоги
    print("\n" + "=" * 100)
    print("ИТОГ ЧЕСТНОГО ЭКЗАМЕНА (holdout, никем не виденный; "
          "статус — только при воспроизводимости)")
    print("=" * 100)
    print(f"{'связка':34} {'сдел':>5} {'WR%':>6} {'exp R':>7} {'t':>5} "
          f"{'PF':>5} {'DD%':>6} {'итог%':>8} {'edge':>7} {'зёрен':>6} "
          f"{'перенос':>8}  вердикт")
    for nm, r in records.items():
        h = r["holdout"]
        tr = (f"{r.get('transfer_pos', 0)}/{r.get('transfer_total', 0)}"
              if "transfer_pos" in r else "-")
        print(f"{nm:34} {h['n']:5} {h['wr']:6.1f} {h['exp_r']:+7.3f} "
              f"{h['t']:+5.2f} {str(h['pf']):>5} {h['dd']:6.1f} "
              f"{h['ret']:+8.1f} {r['edge_exp_r']:+7.3f} "
              f"{r['repro_passed']}/{r['n_seeds']:<4} {tr:>8}  "
              f"{'ПРОШЁЛ' if r['passed'] else 'нет'}"
              f"{' [бета?]' if r['beta_suspect'] else ''}")
    n_ok = sum(1 for r in records.values() if r["passed"])
    n_any = sum(1 for r in records.values() if r["repro_passed"] > 0)
    spreads = [r["genome_spread"] for r in records.values()]
    print(f"\nпрошли (воспроизводимо, {REPRO_NEED}+ зёрен): {n_ok} из "
          f"{len(records)}; хотя бы одним зерном: {n_any}")
    if spreads and len(SEEDS) >= 2:
        avg = sum(spreads) / len(spreads)
        print(f"согласие зёрен по геномам: среднее расстояние {avg:.3f} при "
              f"случайном {REF_DIST:.3f} — "
              + ("зёрна находят РАЗНЫЕ конфиги (подпись подгонки)"
                 if avg > 0.5 * REF_DIST
                 else "зёрна сходятся к похожим конфигам"))
    else:
        print("согласие зёрен по геномам: не измеряется (зерно всего одно)")
    beta_n = sum(1 for r in records.values() if r["beta_suspect"])
    print(f"помечено подозрением на бету: {beta_n} из {len(records)}")
    print(f"страж holdout: прогонов в обучении {GUARD.n_train}, максимальная "
          f"правая граница обучающего отрезка {GUARD.max_b} (граница holdout "
          f"своя у каждой связки); прогонов экзамена {GUARD.n_exam}")
    print(f"итоги в {OUT} | всего {time.time()-t_all:.0f}с"
          + ("  (SMOKE — черновик!)" if SMOKE else ""))


if __name__ == "__main__":
    main()
