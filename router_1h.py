# -*- coding: utf-8 -*-
"""Мета-бот на часовике: один бот, четыре сетапа, переключение по рынку.

ЗАКАЗ ВЛАДЕЛЬЦА (дословно): «сделай бота который объединяет все 4 вариации
на часовике, чтобы они переключались автоматом от ситуации на рынке».

ЧТО ЭТО. На каждом ЗАКРЫТОМ 1ч-баре роутер смотрит ситуацию на рынке
(режим bull/range/bear + шторм + запас по воротам) и решает, какой из
четырёх сетапов сейчас уместен. Торгует только им, максимум ОДНА позиция
одновременно, кулдаун и плечо общие — это именно ОДИН бот, а не четыре.

ЧТО РОУТЕР НЕ ДЕЛАЕТ (сознательно, чтобы не наплодить степеней свободы):
он НЕ переоптимизирует сетапы. Геномы четырёх сетапов берутся готовыми из
evolution12_winners.json (ключи "<сетап>@60") и не трогаются. Роутер
подбирает ТОЛЬКО политику переключения (6 генов, см. ROUTER_GENES).

ПОЛИТИКА = ЯВНАЯ ТАБЛИЦА «режим рынка -> уместные сетапы» (PRIMARY):
    bull  (0): sweep_long, dump_long        — лонги по тренду
    range (1): sweep_long, bounce_short     — оба «от границ» диапазона
    bear  (2): bounce_short, rally_short    — шорты по тренду
Остальные два сетапа в каждом режиме считаются КОНТР-ТРЕНДОВЫМИ: в bull это
шорты (нужен экстремальный перегрев), в bear — лонги (нужна капитуляция),
в range — сетапы «за движением». Они разрешены только при allow_counter=1 и
только если сигнал прошёл ворота с запасом не меньше counter_min (запас
считает signal_strength — «насколько с запасом пройдены ворота»).
Шторм (сильный ход за сутки/неделю или аномальный ATR — те же пороги, что у
se2.storm_state, домноженные на ген storm_scale) обрабатывает storm_policy:
    0 — в шторм не торговать вообще,
    1 — торговать только ПО направлению шторма (обвал -> только шорты),
    2 — шторм игнорировать.
При нескольких сигналах на одном баре выбор делает priority_mode:
    0 — фиксированный порядок качества сетапов (QUALITY_ORDER),
    1 — меньший риск: у кого уже стоп (ev["dist"]),
    2 — сила сигнала: наибольший запас по воротам (gates[].margin).

ЧЕСТНОСТЬ (те же правила, что в signal_engine2):
  - на баре i используются только данные <= i; gate_eval/prep_context
    причинные, поэтому предрасчёт сигналов по всей истории (prep_signals) и
    последующий прогон по отрезку дают ровно то же, что прогон по отрезку
    (проверено префикс-тестом, см. test_router.py);
  - решение принимается на ЗАКРЫТИИ 1ч-бара, вход — open первой 15м-свечи
    после закрытия (всё исполнение делает signal_engine2);
  - если выбранный сетап не налился лимиткой на ретесте, роутер НЕ берёт
    вместо него другой сетап того же бара: факт «не налилось» известен
    только через часы, подмена была бы заглядыванием в будущее;
  - стоп:тейк 1:3, безубытка нет, стоп+тейк в одной свече = стоп — всё это
    внутри signal_engine2, роутер механику не меняет;
  - кулдаун роутера ОБЩИЙ (ген router_cooldown, в 1ч-барах) и заменяет
    личные гены cooldown сетапов: один бот — один кулдаун.

ФОРМАТ РЕЗУЛЬТАТА run_router — тот же, что у se2.run_setup (balance, trades,
near_misses, reject_counts, monthly, max_dd, ruined, months, signals, ...),
поэтому signal_stats.full_stats и билдер сайта работают без переделок.
Добавлено: у каждой сделки поля "setup" (кто дал сделку) и "policy_reason"
(почему выбран), у результата — trades_by_setup, signals_by_setup, switches,
blocked_policy, blocked_storm, policy.

Запуск как скрипта: python router_1h.py
  печатает прогон дефолтной политики на holdout и таблицу
  «роутер vs каждый одиночный сетап» (holdout evolution12: последние 28%).
"""

import json
import os
import sys
import time

import evolution as ev
import signal_engine2 as se2

try:                      # русский вывод не должен падать при перенаправлении
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TF = 60                                   # роутер живёт на часовике
SETUPS4 = ("sweep_long", "dump_long", "bounce_short", "rally_short")
WINNERS_FILE = "evolution12_winners.json"

# Таблица «режим -> сетапы, уместные по ситуации» (ядро политики).
PRIMARY = {
    0: ("sweep_long", "dump_long"),       # bull: лонги по тренду
    1: ("sweep_long", "bounce_short"),    # range: оба «от границ»
    2: ("bounce_short", "rally_short"),   # bear: шорты по тренду
}

# Фиксированный порядок качества для priority_mode=0. ВАЖНО ДЛЯ ЧЕСТНОСТИ:
# порядок взят по exp_r ВНУТРЕННИХ OOS из evolution12_winners.json
# (rally 1.293 > dump 0.729 > bounce 0.116 > sweep 0.113) — это отрезки
# ОБУЧАЮЩЕЙ части [0..hold), holdout в этот порядок не заглядывал.
QUALITY_ORDER = ("rally_short", "dump_long", "bounce_short", "sweep_long")

# ------------------------------------------------------------- гены политики
# имя -> (min, max, is_int); ровно эти 6 чисел подбирает evolution13
ROUTER_GENES = {
    "allow_counter":   (0, 1, True),      # 1 -> контр-трендовые сетапы можно
    "counter_min":     (0.0, 1.5, False), # требуемый запас ворот для них
    "storm_policy":    (0, 2, True),      # 0 стоп / 1 по шторму / 2 игнор
    "storm_scale":     (0.6, 2.0, False), # множитель порогов шторма
    # ЗАМЕРЕНО, чтобы не создавать иллюзий: за 3 года истории сигналы двух и
    # более сетапов совпали на ОДНОМ баре всего 8 раз (3 из них на holdout),
    # поэтому priority_mode почти ни на что не влияет — переключение делают
    # таблица режимов и занятость единственной позиции, а не этот ген.
    "priority_mode":   (0, 2, True),      # чем разрешать одновременные сигналы
    "router_cooldown": (0, 24, True),     # общий кулдаун, 1ч-баров
}

# НЕОБУЧЕННАЯ политика (бенчмарк «политика из головы, без GA»):
# строгая таблица, шторм не трогаем (блокировка — гипотеза, а не факт),
# приоритет по качеству, кулдаун 8 баров = медиана личных cooldown четырёх
# победителей @60 (12, 6, 8, 9).
DEFAULT_POLICY = dict(allow_counter=0, counter_min=0.30, storm_policy=2,
                      storm_scale=1.0, priority_mode=0, router_cooldown=8)

# НАИВНЫЙ роутер (бенчмарк «все 4 сетапа без политики»): режимной таблицы
# нет вовсе (разрешено всё, порог запаса 0), шторм игнорируется, кулдауна
# нет — берём первый сработавший сигнал, ничьи на одном баре разрешает
# фиксированный порядок.
NAIVE_POLICY = dict(allow_counter=1, counter_min=0.0, storm_policy=2,
                    storm_scale=1.0, priority_mode=0, router_cooldown=0)

PRIORITY_NAMES = {0: "порядок качества", 1: "меньший риск",
                  2: "сила сигнала"}
STORM_POLICY_NAMES = {0: "в шторм не торгуем", 1: "только по шторму",
                      2: "шторм игнорируем"}

REJ_POLICY = "политика режима"     # сигнал был, но сетап не уместен сейчас
REJ_STORM = "политика шторма"      # сигнал был, но шторм запретил
REJ_NAMES_ROUTER = (REJ_POLICY, REJ_STORM)

# Нормировка запаса ворот для signal_strength: у разных ворот разные
# единицы, поэтому запас переводится в «доли характерного масштаба».
# Ворота с ненулевым порогом нормируются на сам порог (относительный запас).
_STRENGTH_NORM = {
    se2.GATE_RSI: 10.0,        # пункты RSI
    se2.GATE_AROON: 10.0,      # пункты шкалы 0..100
    se2.GATE_FUND: 0.010,      # %/8ч
    se2.GATE_MA: 0.010,        # доля цены
    se2.GATE_RECLAIM: 0.25,    # дневные ATR (допуск над сломанным уровнем)
    se2.GATE_STORM: 1.0,       # уже в долях порога
}


# ------------------------------------------------------------------ геномы
def load_genomes(path=WINNERS_FILE, tf=TF, setups=SETUPS4):
    """Геномы четырёх сетапов из evolution12_winners.json (ключи "<s>@<tf>").

    Роутер их НЕ меняет и НЕ переоптимизирует: это фиксированные «моторы»,
    подобранные и проверенные отдельной волной (evolution12)."""
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    out = {}
    for s in setups:
        key = f"{s}@{tf}"
        if key not in data:
            raise SystemExit(f"{path}: нет ключа {key}")
        row = data[key]
        if int(row.get("interval_min", tf)) != int(tf):
            raise SystemExit(f"{key}: interval_min={row.get('interval_min')} "
                             f"!= {tf}")
        out[s] = dict(row["genome"])
    return out


def winners_meta(path=WINNERS_FILE, tf=TF, setups=SETUPS4):
    """Справка по победителям @tf: рекомендованное плечо, вердикт, holdout —
    для печати «роутер vs одиночные сетапы» (цифры holdout берутся ЗАНОВО
    прогоном, из json нужны только пометки)."""
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return {s: data[f"{s}@{tf}"] for s in setups if f"{s}@{tf}" in data}


def clamp_policy(policy=None):
    """Политика с дефолтами и зажатыми границами (как e4.ga_tools.clamp)."""
    p = dict(DEFAULT_POLICY)
    if policy:
        for k, v in policy.items():
            if k in ROUTER_GENES:
                p[k] = v
    out = {}
    for k, (lo, hi, is_int) in ROUTER_GENES.items():
        v = p[k]
        v = min(hi, max(lo, v))
        out[k] = int(round(v)) if is_int else float(v)
    return out


def policy_key(policy):
    """Ключ политики для кэшей GA."""
    p = clamp_policy(policy)
    return tuple(p[k] if ROUTER_GENES[k][2] else round(p[k], 4)
                 for k in ROUTER_GENES)


def describe_policy(policy):
    """Однострочное описание политики по-русски (для логов и JSON)."""
    p = clamp_policy(policy)
    ctr = (f"контр-тренд ВКЛ (запас >= {p['counter_min']:.2f})"
           if p["allow_counter"] else "контр-тренд ЗАПРЕЩЁН")
    return (f"{ctr}; шторм: {STORM_POLICY_NAMES[p['storm_policy']]} "
            f"(пороги x{p['storm_scale']:.2f}); приоритет: "
            f"{PRIORITY_NAMES[p['priority_mode']]}; кулдаун "
            f"{p['router_cooldown']} баров")


# --------------------------------------------------------- запас по воротам
def signal_strength(ev_):
    """«Насколько с запасом пройдены ворота» — минимум нормированных запасов
    (принцип слабого звена). Бинарные ворота (режим, направление свечи) в
    расчёт не идут: у них запас всегда ровно 0.0 и «почти» не бывает.

    Только пройденные ворота и только данные бара i — величина причинная.
    Возвращает >= 0; 0.0 означает «числовых ворот нет / запаса нет»."""
    best = None
    for q in ev_.get("gates", ()):
        if not q["ok"]:
            return 0.0
        m = q["margin"]
        if m == 0.0:                      # бинарное ворото (_gate_bool)
            continue
        need = abs(q["need"])
        if need > 1e-12:
            rel = m / need
        else:
            rel = m / _STRENGTH_NORM.get(q["name"], 1.0)
        if best is None or rel < best:
            best = rel
    return max(0.0, best if best is not None else 0.0)


# ------------------------------------------------------- предрасчёт сигналов
def _warmup(setup, g, ext):
    """Первый бар, с которого сетап вообще можно оценивать (дословно та же
    формула, что в se2.run_setup: прогрев окна, RSI, SMA, Aroon)."""
    a = max(int(g["window"]) + int(ext["lag"]) + 40,
            se2.RSI_SET[int(g["rsi_idx"])] + 2)
    if setup == "rally_short" and int(g.get("ma_gate") or 0):
        a = max(a, int(g.get("ma_len") or 200) + 2)
    if int(g.get("aroon_gate") or 0):
        a = max(a, int(g.get("aroon_n") or 25) + 2)
    return a


def prep_signals(genomes, c1h, ctx, interval_min=TF, verbose=False):
    """Предрасчёт ворот всех четырёх сетапов по всей серии — ОДИН раз.

    Ворота зависят только от (сетап, бар, геном) и НЕ зависят от политики,
    поэтому GA роутера потом лишь переигрывает готовые сигналы разными
    политиками (полный прогон политики — миллисекунды вместо секунд).
    Причинность не страдает: gate_eval(i) и так смотрит только данные <= i,
    предрасчёт ничего не переносит из будущего в прошлое.

    Возвращает структуру для run_router(pre=...):
      sig[s]   — {бар: разбор ворот} там, где ВСЕ ворота пройдены
      near[s]  — {бар: разбор} там, где провалено ровно одно ворото «почти»
      fails[s] — на каждый бар кортеж имён проваленных ворот (для reject_counts
                 и gate_solo по любому отрезку)
      strength — {(s, бар): запас по воротам}
      ext/warm — предрасчёт уровней и прогрев каждого сетапа
    """
    n = len(c1h)
    iv = int(interval_min)
    pre = dict(interval_min=iv, n=n, setups=tuple(genomes.keys()),
               genomes={s: dict(g) for s, g in genomes.items()},
               ext={}, warm={}, sig={}, near={}, fails={}, strength={},
               hypo={}, rej_cache={})
    for s, g in genomes.items():
        t0 = time.time()
        ext = se2.build_ext(s, g, c1h, interval_min=iv)
        a_s = _warmup(s, g, ext)
        sig, near = {}, {}
        fails = [()] * n
        for i in range(a_s, n):
            e_ = se2.gate_eval(s, i, c1h, ctx, g, ext)
            if e_["ok"]:
                sig[i] = e_
                pre["strength"][(s, i)] = signal_strength(e_)
            else:
                fails[i] = tuple(q["name"] for q in e_["gates"]
                                 if not q["ok"])
                if e_["near_miss"]:
                    near[i] = e_
        pre["ext"][s], pre["warm"][s] = ext, a_s
        pre["sig"][s], pre["near"][s], pre["fails"][s] = sig, near, fails
        if verbose:
            print(f"    {s:13} сигналов {len(sig):5} | near {len(near):5} | "
                  f"прогрев с бара {a_s:5} | {time.time()-t0:.1f}с")
    return pre


def _rejects_for_range(pre, a, b):
    """Счётчики ворот (reject_counts по первому провалу и gate_solo) на
    отрезке [a,b) — сумма по четырём сетапам. Не зависят от политики,
    поэтому кэшируются по отрезку."""
    key = (a, b)
    got = pre["rej_cache"].get(key)
    if got is not None:
        return dict(got[0]), dict(got[1])
    rc, solo = {}, {}
    for s in pre["setups"]:
        fails = pre["fails"][s]
        a_s = max(a, pre["warm"][s])
        for i in range(a_s, b):
            f = fails[i]
            if not f:
                continue
            rc[f[0]] = rc.get(f[0], 0) + 1
            for nm in f:
                solo[nm] = solo.get(nm, 0) + 1
    pre["rej_cache"][key] = (rc, solo)
    return dict(rc), dict(solo)


# --------------------------------------------------------- решение политики
def _storm_at(ctx, i, pol):
    """Состояние шторма с порогами политики (пороги семени x storm_scale).
    Формула и ряды — целиком из se2.storm_state, роутер их не переизобретает."""
    k = pol["storm_scale"]
    g = dict(storm_day=se2.DEFAULTS2["storm_day"] * k,
             storm_week=se2.DEFAULTS2["storm_week"] * k,
             storm_atr_rank=min(1.0, se2.DEFAULTS2["storm_atr_rank"] * k))
    return se2.storm_state(ctx, i, g)


def pick_setup(cands, reg, storm_on, storm_dir, pol):
    """Кто торгует на этом баре.

    cands — [(сетап, разбор ворот, запас)] по всем сетапам, чьи ворота
    пройдены. Возвращает (выбранный_или_None, причина, код_отказа):
    код_отказа — REJ_STORM / REJ_POLICY / None."""
    if not cands:
        return None, "", None
    # 1) шторм — это отказ от рынка целиком, он идёт первым
    alive = cands
    if storm_on and pol["storm_policy"] == 0:
        return None, "", REJ_STORM
    if storm_on and pol["storm_policy"] == 1 and storm_dir:
        need = "S" if storm_dir < 0 else "L"
        alive = [c for c in cands if c[1]["side"] == need]
        if not alive:
            return None, "", REJ_STORM
    # 2) режимная таблица + контр-трендовое исключение
    prim = PRIMARY[reg]
    keep = []
    for s, e_, stg in alive:
        if s in prim:
            keep.append((s, e_, stg, True))
        elif pol["allow_counter"] and stg >= pol["counter_min"]:
            keep.append((s, e_, stg, False))
    if not keep:
        return None, "", REJ_POLICY
    # 3) приоритет при одновременных сигналах
    mode = pol["priority_mode"]
    if mode == 1:                                   # меньший риск
        keep.sort(key=lambda x: (x[1]["dist"], QUALITY_ORDER.index(x[0])))
    elif mode == 2:                                 # сила сигнала
        keep.sort(key=lambda x: (-x[2], QUALITY_ORDER.index(x[0])))
    else:                                           # порядок качества
        keep.sort(key=lambda x: QUALITY_ORDER.index(x[0]))
    s, e_, stg, is_prim = keep[0]
    why = (f"{se2.REGIME_NAMES[reg]}"
           + (f"/шторм{'-' if storm_dir < 0 else '+' if storm_dir > 0 else ''}"
              if storm_on else "")
           + f": {s} " + ("по таблице режима" if is_prim
                          else f"контр-тренд, запас {stg:.2f}")
           + f"; выбор — {PRIORITY_NAMES[mode]}"
           + (f"; кандидатов {len(keep)}" if len(keep) > 1 else ""))
    return (s, e_, stg), why, None


# ------------------------------------------------------------- полный прогон
def run_router(genomes, c1h, ctx, c15, ts15, lev, signal_range=None,
               policy=None, collect_diag=True, pre=None, interval_min=None):
    """Прогон мета-бота. Формат результата — как у se2.run_setup.

    genomes — {сетап: геном} (load_genomes); policy — гены политики
    (clamp_policy подставит дефолты); pre — предрасчёт prep_signals (если
    не дать, посчитается внутри: удобно для разового вызова, дорого для GA).
    signal_range=(a,b): сигналы берутся только из баров [a,b); сделка,
    открытая внутри отрезка, доживает своё за его границей (walk-forward)."""
    ctx_iv = ctx.get("interval_min") if isinstance(ctx, dict) else None
    iv = int(interval_min or ctx_iv or TF)
    if ctx_iv is not None and int(ctx_iv) != iv:
        raise ValueError(f"interval_min={iv} не совпадает с контекстом "
                         f"(ctx['interval_min']={ctx_iv})")
    pol = clamp_policy(policy)
    if pre is None:
        pre = prep_signals(genomes, c1h, ctx, interval_min=iv)
    bar_ms = se2.bar_ms_of(iv)
    n = len(c1h)
    a_req, b = signal_range or (0, n)
    b = min(int(b), n)
    setups = tuple(pre["setups"])
    starts = {s: max(int(a_req), pre["warm"][s]) for s in setups}
    a = min(starts.values()) if setups else int(a_req)
    a = min(a, b)

    # бары, на которых вообще есть что решать (сигнал или near-miss)
    marks = set()
    for s in setups:
        lo = starts[s]
        marks.update(i for i in pre["sig"][s] if lo <= i < b)
        if collect_diag:
            marks.update(i for i in pre["near"][s] if lo <= i < b)
    bars = sorted(marks)

    balance, peak, max_dd = se2.START, se2.START, 0.0
    trades, near_misses = [], []
    exec_rejects = {}          # отказы стадии исполнения (ретест/нет 15м/стоп)
    monthly = {}
    blocked_busy = blocked_cooldown = blocked_ruined = 0
    blocked_policy = blocked_storm = 0
    signals = 0
    sig_by_setup = {s: 0 for s in setups}
    tr_by_setup = {s: 0 for s in setups}
    pass_by_regime = {0: 0, 1: 0, 2: 0}
    busy_until_ts, last_exit_ts = 0, 0
    ruined = False
    switches, last_setup = 0, None
    cool_ms = int(pol["router_cooldown"]) * bar_ms
    t0 = c1h[a][0] if a < n else c1h[-1][0]
    stop_all = False
    stop_bar = b               # где оборвался прогон (кончились 15м-данные)

    for i in bars:
        ts = c1h[i][0]
        sig_ts = ts + bar_ms                # сигнал существует с ЗАКРЫТИЯ бара
        reg = ctx["regime"][i]
        stm = _storm_at(ctx, i, pol)
        s_on, s_dir = bool(stm["storm"]), int(stm["dir"])
        cool_ok = (not last_exit_ts) or sig_ts >= last_exit_ts + cool_ms
        free = sig_ts >= busy_until_ts and cool_ok

        # --- near-miss: те же правила, что в движке (бар свободен + политика
        # этот сетап на этом баре вообще допускала) ---
        if collect_diag and free:
            for s in setups:
                e_ = pre["near"][s].get(i)
                if e_ is None or i < starts[s]:
                    continue
                probe = [(s, e_, 1e9)]      # запас для «почти» не определён:
                # ворота провалены, поэтому контр-трендовый порог не мешаем
                got, _why, _rej = pick_setup(probe, reg, s_on, s_dir, pol)
                if got is None:
                    continue
                near_misses.append(_near_row(s, e_, i, c1h, ctx, genomes[s],
                                             c15, ts15, lev, bar_ms, pre))

        cands = []
        for s in setups:
            if i < starts[s]:
                continue
            e_ = pre["sig"][s].get(i)
            if e_ is not None:
                cands.append((s, e_, pre["strength"].get((s, i), 0.0)))
        if not cands:
            continue
        signals += 1
        pass_by_regime[reg] += 1
        for s, _e, _st in cands:
            sig_by_setup[s] += 1
        if ruined:
            blocked_ruined += 1
            continue
        if sig_ts < busy_until_ts:
            blocked_busy += 1
            continue
        if not cool_ok:
            blocked_cooldown += 1
            continue

        got, why, rej = pick_setup(cands, reg, s_on, s_dir, pol)
        if got is None:
            if rej == REJ_STORM:
                blocked_storm += 1
            else:
                blocked_policy += 1
            exec_rejects[rej] = exec_rejects.get(rej, 0) + 1
            continue
        s, e_, stg = got
        g = genomes[s]
        tr, bad = se2._make_trade(s, e_, i, c1h, ctx, g, c15, ts15, lev,
                                  bar_ms=bar_ms)
        if tr is None:
            exec_rejects[bad] = exec_rejects.get(bad, 0) + 1
            if bad == se2.REJ_NO15M:
                stop_all, stop_bar = True, i   # 15м кончились — истории нет
                break
            continue
        tr["setup"] = s
        tr["policy_reason"] = why
        tr["strength"] = round(stg, 4)
        tr["regime_name"] = se2.REGIME_NAMES.get(reg, "?")
        tr["storm"] = bool(s_on)
        tr["storm_dir"] = s_dir
        balance += tr["pnl"]
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak)
        m_idx = int((tr["exit_ts"] - t0) // se2.MONTH_MS)
        monthly[m_idx] = monthly.get(m_idx, 0.0) + tr["pnl"]
        trades.append(tr)
        tr_by_setup[s] += 1
        if last_setup is not None and s != last_setup:
            switches += 1
        last_setup = s
        busy_until_ts = tr["exit_ts"]
        last_exit_ts = tr["exit_ts"]
        if balance < se2.MARGIN:
            ruined = True

    # Счётчики ворот — по фактически пройденным барам: если прогон оборвался
    # на нехватке 15м-данных, бары после обрыва движок тоже не считает.
    reject_counts, gate_solo = _rejects_for_range(pre, a, stop_bar)
    for k, v in exec_rejects.items():
        reject_counts[k] = reject_counts.get(k, 0) + v
    last_ts = c1h[min(max(a, b - 1), n - 1)][0] if n else t0
    return dict(
        balance=balance, trades=trades, near_misses=near_misses,
        reject_counts=reject_counts, blocked_busy=blocked_busy,
        blocked_cooldown=blocked_cooldown, blocked_ruined=blocked_ruined,
        monthly=monthly, max_dd=max_dd, ruined=ruined,
        months=max(1e-9, (last_ts - t0) / se2.MONTH_MS),
        setup="router@%d" % iv, lev=lev, signals=signals,
        pass_by_regime=pass_by_regime, gate_solo=gate_solo,
        bars_eval=max(0, stop_bar - a + (1 if stop_all else 0)),
        signal_range=(a, b), interval_min=iv,
        blocked_score=0, score_min=None,
        # --- роутерное ---
        policy=pol, policy_text=describe_policy(pol),
        trades_by_setup=tr_by_setup, signals_by_setup=sig_by_setup,
        switches=switches, blocked_policy=blocked_policy,
        blocked_storm=blocked_storm, truncated_no15m=stop_all)


def _near_row(s, e_, i, c1h, ctx, g, c15, ts15, lev, bar_ms, pre):
    """Строка near-miss в формате движка (+ поле setup). Гипотетика считается
    той же se2._make_trade с hypo=True и кэшируется: она не зависит ни от
    политики, ни от отрезка."""
    key = (s, i, lev)
    got = pre["hypo"].get(key)
    if got is None:
        got = se2._make_trade(s, e_, i, c1h, ctx, g, c15, ts15, lev,
                              hypo=True, bar_ms=bar_ms)
        pre["hypo"][key] = got
    hypo, why = got
    q = next(x for x in e_["gates"] if not x["ok"])
    d = e_["diag"]
    return dict(
        ts=c1h[i][0], side=e_["side"], gate=q["name"], setup=s,
        got=round(q["got"], 4), need=round(q["need"], 4),
        margin=round(q["margin"], 4),
        entry=round(e_["entry_ref"], 2),
        stop=round(e_["stop"], 2) if e_["stop"] else None,
        tp=round(e_["tp"], 2) if e_["tp"] else None,
        dist=round(e_["dist"], 5) if e_["dist"] else None,
        hypo_r=hypo["r"] if hypo else 0.0,
        hypo_reason=hypo["reason"] if hypo else why,
        hypo_mfe_r=hypo["mfe_r"] if hypo else 0.0,
        hypo_mae_r=hypo["mae_r"] if hypo else 0.0,
        hypo_pnl=hypo["pnl"] if hypo else 0.0,
        regime=d["regime"],
        rsi=round(d["rsi"], 1) if d["rsi"] is not None else None,
        zone_pos=round(d["zone_pos"], 3) if d["zone_pos"] is not None else None,
        range_lo=round(d["range_lo"], 2) if d["range_lo"] else None,
        range_hi=round(d["range_hi"], 2) if d["range_hi"] else None,
        atr_pct=round(d["atr"] * 100, 3) if d["atr"] else None,
        stop_pct=round(e_["dist"] * 100, 3) if e_["dist"] else None,
        rsi_thr=round(d["rsi_thr"], 1) if d["rsi_thr"] is not None else None,
        zone_thr=round(d["zone_thr"], 3) if d["zone_thr"] is not None else None,
    )


# ------------------------------------------------------------------ метрики
def metrics(trades, start=None):
    """Сводка по списку сделок (та же арифметика, что в evolution11/12.pooled,
    чтобы цифры роутера и одиночных сетапов считались ОДИНАКОВО)."""
    start = se2.START if start is None else start
    n = len(trades)
    if not n:
        return dict(n=0, wr=0.0, exp_r=0.0, sum_r=0.0, pf=None, dd=0.0,
                    ret=0.0, tp=0, stop=0)
    wins = [t for t in trades if t["pnl"] > 0]
    gp = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    bal, peak, dd = start, start, 0.0
    for t in sorted(trades, key=lambda x: x["exit_ts"]):
        bal += t["pnl"]
        peak = max(peak, bal)
        dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
    return dict(
        n=n, wr=round(len(wins) / n * 100, 1),
        exp_r=round(sum(t["r"] for t in trades) / n, 3),
        sum_r=round(sum(t["r"] for t in trades), 2),
        pf=round(gp / gl, 2) if gl > 0 else None,
        dd=round(dd * 100, 1), ret=round((bal / start - 1) * 100, 1),
        tp=sum(1 for t in trades if t["reason"] == "tp"),
        stop=sum(1 for t in trades if t["reason"] == "stop"))


def solo_trades(setup, g, c1h, ctx, c15, ts15, lev, seg, interval_min=TF):
    """Сделки ОДИНОЧНОГО сетапа на отрезке — движком, без роутера
    (бенчмарк «а нужен ли роутер вообще»)."""
    r = se2.run_setup(setup, g, c1h, ctx, c15, ts15, lev, signal_range=seg,
                      collect_diag=False, interval_min=interval_min)
    return r["trades"]


# --------------------------------------------------------------------- main
def main():
    """Демонстрация: дефолтная политика на holdout + таблица «роутер vs
    каждый одиночный сетап». Holdout — тот же, что в evolution12
    (последние 28% 1ч-истории), его не видели ни GA сетапов, ни отбор."""
    days = int(os.environ.get("GA_DAYS", "1150"))
    lev = int(os.environ.get("R_LEV", "15"))
    hold_frac = float(os.environ.get("GA_HOLD", "0.28"))
    t_all = time.time()
    print("=" * 78)
    print("router_1h: один часовой бот из четырёх сетапов "
          "(переключение по ситуации на рынке)")
    print("=" * 78)
    c1h = ev.fetch("BTCUSDT", str(TF), days)
    c15 = ev.fetch("BTCUSDT", "15", days)
    ts15 = [c[0] for c in c15]
    ctx = se2.prep_context(c1h, interval_min=TF)
    n = len(c1h)
    hold = int(n * (1 - hold_frac))
    seg = (hold, n)

    def d(ms):
        return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))

    print(f"1ч-баров {n} ({d(c1h[0][0])}..{d(c1h[-1][0])}), 15м {len(c15)} "
          f"(до {d(c15[-1][0])})")
    print(f"HOLDOUT [{hold}..{n}) = {(n-hold)*TF/1440/30.44:.1f} мес "
          f"({d(c1h[hold][0])}..{d(c1h[-1][0])}), цена "
          f"{c1h[hold][4]:.0f} -> {c1h[-1][4]:.0f} "
          f"({(c1h[-1][4]/c1h[hold][4]-1)*100:+.1f}%) | плечо x{lev}")
    genomes = load_genomes()
    print("геномы сетапов: evolution12_winners.json @60 (роутер их НЕ трогает)")
    print("предрасчёт ворот:")
    pre = prep_signals(genomes, c1h, ctx, interval_min=TF, verbose=True)

    print(f"\nПОЛИТИКА (необученная, дефолтная): {describe_policy(None)}")
    r = run_router(genomes, c1h, ctx, c15, ts15, lev, signal_range=seg,
                   policy=None, collect_diag=True, pre=pre)
    m = metrics(r["trades"])
    print(f"РОУТЕР на holdout: {m['n']} сделок | WR {m['wr']}% | exp "
          f"{m['exp_r']:+.3f}R | PF {m['pf']} | сумма {m['sum_r']:+.2f}R | "
          f"итог {m['ret']:+.1f}% | DD {m['dd']}% | tp/stop {m['tp']}/{m['stop']}")
    print(f"  сделок по сетапам: " + ", ".join(
        f"{s} {r['trades_by_setup'][s]}" for s in SETUPS4)
        + f" | переключений {r['switches']}")
    print(f"  сигналов {r['signals']}, из них: занят {r['blocked_busy']}, "
          f"кулдаун {r['blocked_cooldown']}, политика {r['blocked_policy']}, "
          f"шторм {r['blocked_storm']}")

    print("\nТАБЛИЦА: роутер vs каждый одиночный сетап (тот же holdout, "
          f"то же плечо x{lev})")
    print(f"{'кто':22} {'сдел':>5} {'WR%':>6} {'exp R':>7} {'PF':>6} "
          f"{'сумма R':>8} {'итог%':>8} {'DD%':>6}")
    rows = [("РОУТЕР (дефолт)", m)]
    for s in SETUPS4:
        tr = solo_trades(s, genomes[s], c1h, ctx, c15, ts15, lev, seg)
        rows.append((s, metrics(tr)))
    naive = run_router(genomes, c1h, ctx, c15, ts15, lev, signal_range=seg,
                       policy=NAIVE_POLICY, collect_diag=False, pre=pre)
    rows.append(("наивный роутер", metrics(naive["trades"])))
    for name, q in rows:
        print(f"{name:22} {q['n']:5} {q['wr']:6.1f} {q['exp_r']:+7.3f} "
              f"{str(q['pf']):>6} {q['sum_r']:+8.2f} {q['ret']:+8.1f} "
              f"{q['dd']:6.1f}")
    best = max((q for nm, q in rows[1:5] if q["n"] >= 8),
               key=lambda x: x["exp_r"], default=None)
    if best:
        print(f"\nлучший одиночный сетап (n>=8) по exp_r: {best['exp_r']:+.3f}R "
              f"-> преимущество роутера {m['exp_r'] - best['exp_r']:+.3f}R")
    print(f"\nЭто ДЕФОЛТНАЯ (необученная) политика. Честный отбор политики и "
          f"вердикт — evolution13.py")
    print(f"всего {time.time()-t_all:.0f}с")


if __name__ == "__main__":
    main()
