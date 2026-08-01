# -*- coding: utf-8 -*-
"""ЧЕСТНАЯ ПРОВЕРКА итоговой капитуляции-лонга (capitulation.py).

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ И ПОЧЕМУ ИМЕННО ТАК
  0. Данные. 5 монет (включая заведомо плохие — ничего не прячем), 4ч,
     1150 суток. Обучение [0..hold), hold = 72% истории; HOLDOUT [hold..n)
     не участвует ни в выборе параметров, ни в выборе монет.
  1. САМОПРОВЕРКИ. (а) заглядывания нет: сигналы, стопы и объёмная
     плотность, пересчитанные по ОБРЕЗАННОЙ истории, обязаны совпасть с
     полным рядом; (б) мой цикл сделок обязан совпасть со штатным
     se3.run_setup сделка в сделку — иначе меряется мой баг, а не сетап.
  2. КАЛИБРОВКА НА ОБУЧЕНИИ. Объявленная ДО расчёта решётка 4 стопа x 2
     фильтра x 2 входа = 16 вариантов и объявленное правило выбора.
     Плюс переносимость: калибровка на 4 монетах -> контроль на 5-й.
  3. РАЗЛОЖЕНИЕ. Главный блок честности: тот же прибор прикладывается к
     СЛУЧАЙНЫМ входам. Если элемент даёт случайному входу тот же прирост,
     это свойство прибора, а не знание о рынке.
  4. HOLDOUT против ЧЕТЫРЁХ эталонов: базовый dump_long, равномерно
     случайный вход той же частоты, случайный вход С ТОЙ ЖЕ
     ВОЛАТИЛЬНОСТЬЮ (главный эталон) и «купил и держал».
  5. ПО МОНЕТАМ — все пять.
  6. ВОСПРОИЗВОДИМОСТЬ (зёрна) и УСТОЙЧИВОСТЬ (вклад крупнейших сделок,
     распределение по кварталам).
  7. РИСК: просадка, ликвидации, сливы, лестница плеча x2..x25,
     вероятность разорения блочным бутстрэпом, требуемый капитал.
  8. ЗНАЧИМОСТЬ с поправкой на число проверок ВСЕЙ ВОЛНЫ.
  9. setups_capitulation.json. 10. ВЕРДИКТ.

Запуск: python capitulation_check.py  (вывод -> capitulation_check_out.txt)
"""

import io
import json
import math
import os
import random
import statistics
import sys
import time

import capitulation as cap
import signal_engine3 as se3

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE, "capitulation_check_out.txt")
JSON_PATH = os.path.join(BASE, "setups_capitulation.json")

SYMS = cap.SYMS
TF = cap.TF
LEV = cap.LEV
RAND_SEEDS = [11, 23, 37, 41, 53]      # >=3 зерна, как требует методология
BOOT_SEEDS = [7, 19, 31]
ATR_TOL = 0.25                         # допуск подбора по волатильности, +-25%

# Проверок в волне ДО меня (из отчётов агентов): сетка/плечо 96,
# индикаторы 53, уровни 42, литература 10.
WAVE_PRIOR_CHECKS = 96 + 53 + 42 + 10
# Мои: 16 калибровочных + 12 разложения + 6 эталонов + 6 лестницы + 5 монет.
N_MY_CHECKS = 16 + 12 + 6 + 6 + 5

_LINES = []


def out(s=""):
    _LINES.append(s)
    try:
        print(s)
    except Exception:
        print(s.encode("ascii", "replace").decode("ascii"))


# ---------------------------------------------------------------- статистика
def norm_sf(z):
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def p_two(t):
    return 2.0 * norm_sf(abs(t))


def t_one(rs):
    if len(rs) < 3:
        return 0.0
    sd = statistics.pstdev(rs)
    return statistics.mean(rs) / (sd / math.sqrt(len(rs))) if sd > 0 else 0.0


def welch_t(a, b):
    """t разницы средних двух независимых наборов (Уэлч)."""
    if len(a) < 3 or len(b) < 3:
        return 0.0
    va = statistics.pvariance(a) / len(a)
    vb = statistics.pvariance(b) / len(b)
    if va + vb <= 0:
        return 0.0
    return (statistics.mean(a) - statistics.mean(b)) / math.sqrt(va + vb)


def se_diff(a, b):
    if len(a) < 3 or len(b) < 3:
        return float("inf")
    return math.sqrt(statistics.pvariance(a) / len(a)
                     + statistics.pvariance(b) / len(b))


def quarter_of(ms):
    t = time.gmtime(ms / 1000.0)
    return t.tm_year * 4 + (t.tm_mon - 1) // 3


def fmt_pf(pf):
    if pf is None:
        return "  n/a"
    if pf == float("inf"):
        return "  inf"
    return f"{pf:5.2f}"


def d_str(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def agg(trades, n_base=len(SYMS)):
    """Сводка по объединённым сделкам. Кривая капитала — по времени ВЫХОДА,
    маржа на сделку фиксирована (без реинвестирования): меряем сигнал, а не
    сложный процент. База = START на монету * число монет."""
    tr = sorted(trades, key=lambda t: t["exit_ts"])
    n = len(tr)
    rs = [t["r"] for t in tr]
    pnl = sum(t["pnl"] for t in tr)
    gp = sum(t["pnl"] for t in tr if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in tr if t["pnl"] <= 0)
    base = se3.START * max(1, n_base)
    bal, peak, dd = base, base, 0.0
    for t in tr:
        bal += t["pnl"]
        peak = max(peak, bal)
        dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
    kinds = {}
    for t in tr:
        kinds[t["exit_kind"]] = kinds.get(t["exit_kind"], 0) + 1
    sd = statistics.pstdev(rs) if n > 1 else 0.0
    pnls = [t["pnl"] for t in tr]
    return dict(
        n=n, rs=rs, trades=tr, sd=sd, pnls=pnls,
        exp_usd=(pnl / n) if n else 0.0,
        t_usd=t_one(pnls),
        grid_share=(100.0 * sum(1 for t in tr if t["grid_fills"] >= 2) / n)
        if n else 0.0,
        wr=(100.0 * sum(1 for t in tr if t["pnl"] > 0) / n) if n else 0.0,
        exp_r=(sum(rs) / n) if n else 0.0, sum_r=sum(rs), pnl=pnl,
        pf=(gp / gl) if gl > 0 else (float("inf") if gp > 0 else None),
        ret=100.0 * pnl / base, dd=100.0 * dd, kinds=kinds,
        liq=sum(1 for t in tr if t["exit_kind"] == "liq"),
        t=t_one(rs),
        margin=(statistics.mean([t["margin_used"] for t in tr])
                if n else 0.0),
        stop_pct=(statistics.median([t["stop_pct"] for t in tr])
                  if n else 0.0),
        hold_h=(statistics.median([t["hold_h"] for t in tr]) if n else 0.0))


def line(tag, a, extra="", ret=None):
    """ret — если задан, показывается вместо суммарного по всем зёрнам
    (у пулов эталонов доходность иначе складывается по 5 зёрнам подряд)."""
    r = a["ret"] if ret is None else ret
    out(f"  {tag:34} n={a['n']:4}  WR={a['wr']:5.1f}%  exp_R={a['exp_r']:+.3f}"
        f"  exp$={a['exp_usd']:+.3f}  PF={fmt_pf(a['pf'])}  "
        f"дох={r:+7.1f}%  просадка={a['dd']:5.1f}%  t_R={a['t']:+5.2f}"
        f"{extra}")


# ------------------------------------------------------------------ данные
def load_all():
    data = {}
    for sym in SYMS:
        d = cap.load_symbol(sym)
        d["hold"] = cap.split_index(len(d["c4"]))
        d["dens_cache"] = {}
        data[sym] = d
    return data


def dens_of(d, i, g):
    if i not in d["dens_cache"]:
        d["dens_cache"][i] = cap.void_dens(d, i, g)
    return d["dens_cache"][i]


# --------------------------------------------------------- варианты стопа
# Объявлены ДО расчёта. Ключ -> правки генома.
STOPS = {
    "минимум падения": dict(stop_mode=0, window=13, buf_atr=0.3),
    "1.5*ATR":         dict(stop_mode=2, stop_atr_k=1.5),
    "2.0*ATR (дефолт)": dict(stop_mode=2, stop_atr_k=2.0),
    "свинг 8 баров":   dict(stop_mode=1, swing_bars=8, buf_atr=0.3),
}
ENTRIES = {"одиночный": dict(grid_levels=1),
           "сетка 2x0.8ATR": dict(grid_levels=2, grid_step_atr=0.8,
                                  grid_mult=1.3)}
FILTERS = {"без фильтра": False, "фильтр пустоты": True}

DEFAULT_CHOICE = ("минимум падения", "без фильтра", "одиночный")


def genome_of(stop_key, entry_key):
    g = dict(cap.GENOME_V1)
    g.update(STOPS[stop_key])
    g.update(ENTRIES[entry_key])
    return g


_EVS = {}


def evs_of(data, sym, g, tag, setup="dump_long"):
    key = (sym, tag, setup)
    if key not in _EVS:
        _EVS[key] = cap.build_evs(data[sym], g, setup=setup)
    return _EVS[key]


def rng_of(d, ext, kind):
    a = max(int(ext["warm"]), 0 if kind == "train" else d["hold"])
    b = d["hold"] if kind == "train" else len(d["c4"])
    return a, b


def run_variant(data, stop_key, filt_key, entry_key, kind, lev=LEV):
    g = genome_of(stop_key, entry_key)
    use_void = FILTERS[filt_key]
    per_sym, trades = {}, []
    for sym in SYMS:
        d = data[sym]
        evs, ext = evs_of(data, sym, g, stop_key)
        rng = rng_of(d, ext, kind)
        if use_void:
            dec = (lambda i, d=d, e=evs, g=g:
                   e[i]["ok"] and (dens_of(d, i, g) is None
                                   or dens_of(d, i, g) < cap.VOID_THR))
        else:
            dec = lambda i, e=evs: e[i]["ok"]      # noqa: E731
        r = cap.run_bars(d, g, evs, ext, rng, dec, lev=lev)
        per_sym[sym] = r["trades"]
        trades += r["trades"]
    return trades, per_sym


# ------------------------------------------------------- случайные контроли
def rand_uniform(data, stop_key, entry_key, kind, n_by_sym, seed, lev=LEV):
    """Случайный вход той же частоты: та же сторона, тот же стоп, тот же
    выход — случайны только бары входа."""
    g = genome_of(stop_key, entry_key)
    rnd = random.Random(seed)
    trades = []
    for sym in SYMS:
        d = data[sym]
        evs, ext = evs_of(data, sym, g, stop_key)
        a, b = rng_of(d, ext, kind)
        need = n_by_sym.get(sym, 0)
        if need <= 0 or b - a < need + 5:
            continue
        bars = set(rnd.sample(range(a, b), min(need, b - a - 1)))
        trades += cap.run_bars(d, g, evs, ext, (a, b), lambda i: i in bars,
                               lev=lev)["trades"]
    return trades


def rand_atr_matched(data, stop_key, entry_key, kind, sig_by_sym, seed,
                     lev=LEV, tol=ATR_TOL):
    """ГЛАВНЫЙ эталон: случайный вход с ТОЙ ЖЕ ВОЛАТИЛЬНОСТЬЮ.

    На баре капитуляции ATR раздут, а стоп и тейк меряются в ATR — значит
    сделка сетапа устроена иначе, чем сделка со случайного бара, ещё до
    всякого «знания». Поэтому к каждому сигналу подбирается случайный бар,
    у которого ATR отличается не больше чем на tol, и который отстоит от
    любого сигнала минимум на 3 суток (иначе это тот же слив)."""
    g = genome_of(stop_key, entry_key)
    rnd = random.Random(seed)
    trades = []
    for sym in SYMS:
        d = data[sym]
        evs, ext = evs_of(data, sym, g, stop_key)
        a, b = rng_of(d, ext, kind)
        sig = sig_by_sym.get(sym, [])
        if not sig:
            continue
        atr = d["ctx"]["atr_bar"]
        far = [j for j in range(a, b)
               if atr[j] and all(abs(j - i) > 18 for i in sig)]
        bars = set()
        for i in sig:
            ai = atr[i]
            if not ai:
                continue
            pool = [j for j in far
                    if abs(atr[j] / ai - 1.0) <= tol and j not in bars]
            if pool:
                bars.add(pool[rnd.randrange(len(pool))])
        if not bars:
            continue
        trades += cap.run_bars(d, g, evs, ext, (a, b), lambda i: i in bars,
                               lev=lev)["trades"]
    return trades


def rand_near_dump(data, stop_key, entry_key, kind, sig_by_sym, seed,
                   lev=LEV):
    """Случайный вход В ТОМ ЖЕ УЧАСТКЕ рынка: бар из [i-120, i-12] до
    каждого сигнала. Эталон заведомо ПЛОХОЙ (вход внутрь падения), нужен
    как нижняя граница, а не как честное сравнение."""
    g = genome_of(stop_key, entry_key)
    rnd = random.Random(seed)
    trades = []
    for sym in SYMS:
        d = data[sym]
        evs, ext = evs_of(data, sym, g, stop_key)
        a, b = rng_of(d, ext, kind)
        bars = set()
        for i in sig_by_sym.get(sym, []):
            lo, hi = max(a, i - 120), max(a + 1, i - 12)
            if hi > lo:
                bars.add(rnd.randrange(lo, hi))
        if not bars:
            continue
        trades += cap.run_bars(d, g, evs, ext, (a, b), lambda i: i in bars,
                               lev=lev)["trades"]
    return trades


def pool_seeds(fn, seeds):
    """Пул сделок по нескольким зёрнам + средние ПО ЗЁРНАМ.

    Доходность и просадку по объединённому пулу считать нельзя: 5 зёрен —
    это 5 независимых прогонов, а не один портфель, склеенная кривая дала бы
    пятикратную просадку. Поэтому возвращаем средние по зёрнам."""
    pool, means, rets, dds = [], [], [], []
    for sd in seeds:
        tr = fn(sd)
        pool += tr
        a = agg(tr)
        means.append(a["exp_r"])
        rets.append(a["ret"])
        dds.append(a["dd"])
    p = agg(pool)
    p["ret_seed"] = statistics.mean(rets) if rets else 0.0
    p["dd_seed"] = statistics.mean(dds) if dds else 0.0
    p["ret_list"] = rets
    return p, means, rets


def buy_hold(data, kind):
    per = {}
    for sym in SYMS:
        d = data[sym]
        a = 0 if kind == "train" else d["hold"]
        b = d["hold"] if kind == "train" else len(d["c4"])
        per[sym] = 100.0 * (d["c4"][b - 1][4] / d["c4"][a][4] - 1.0)
    per["СРЕДНЕЕ"] = statistics.mean([per[s] for s in SYMS])
    return per


# ----------------------------------------------------------------- бутстрэп
def block_boot_ruin(trades, seed=7, n_iter=3000, start=se3.START,
                    margin=se3.MARGIN):
    """Вероятность разорения блочным бутстрэпом по КАЛЕНДАРНЫМ КВАРТАЛАМ:
    ресэмплим кварталы (внутри квартала сделки зависимы — общий рынок),
    строим путь баланса одной монеты от start при фиксированной марже.
    Разорение = баланс ниже маржи (следующий цикл открыть нечем)."""
    if len(trades) < 5:
        return None
    by_q = {}
    for t in trades:
        by_q.setdefault(quarter_of(t["exit_ts"]), []).append(t["pnl"])
    qs = sorted(by_q)
    if len(qs) < 4:
        return None
    rnd = random.Random(seed)
    ruins, finals = 0, []
    for _ in range(n_iter):
        bal = start
        for _ in range(len(qs)):
            if bal < margin:
                break
            for p in by_q[qs[rnd.randrange(len(qs))]]:
                if bal < margin:
                    break
                bal += p
        if bal < margin:
            ruins += 1
        finals.append(bal)
    finals.sort()
    return dict(p_ruin=100.0 * ruins / n_iter, med=finals[len(finals) // 2],
                p05=finals[int(0.05 * len(finals))])


def block_boot_mean_neg(trades, seed=7, n_iter=3000):
    """Доля бутстрэп-выборок по кварталам, где средний R <= 0."""
    if len(trades) < 5:
        return None
    by_q = {}
    for t in trades:
        by_q.setdefault(quarter_of(t["exit_ts"]), []).append(t["r"])
    qs = sorted(by_q)
    if len(qs) < 4:
        return None
    rnd = random.Random(seed)
    bad = 0
    for _ in range(n_iter):
        vals = [x for _ in range(len(qs))
                for x in by_q[qs[rnd.randrange(len(qs))]]]
        if not vals or statistics.mean(vals) <= 0:
            bad += 1
    return 100.0 * bad / n_iter


# =============================================================== БЛОК 1
def selfcheck(data, genomes, header=True):
    if header:
        out("БЛОК 1. САМОПРОВЕРКИ")
        out("-" * 78)
    bad_sig = bad_dens = n_sig = n_dens = 0
    for tag, g in genomes:
        for sym in SYMS:
            d = data[sym]
            n = len(d["c4"])
            evs_full, _ = evs_of(data, sym, g, tag)
            for frac in (0.45, 0.70, 0.90):
                k = int(n * frac)
                c4p = d["c4"][:k]
                ctxp = se3.prep_context(c4p, interval_min=TF, symbol=sym)
                extp = se3.build_ext("dump_long", g, c4p, interval_min=TF)
                for i in range(max(extp["warm"], k - 40), k):
                    e_p = se3.gate_eval("dump_long", i, c4p, ctxp, g, extp)
                    e_f = evs_full[i]
                    n_sig += 1
                    sp, sf = e_p.get("stop"), e_f.get("stop")
                    same = bool(e_p["ok"]) == bool(e_f["ok"]) and (
                        (sp is None and sf is None)
                        or (sp is not None and sf is not None
                            and abs(sp - sf) <= 1e-9 * max(1.0, abs(sf))))
                    if not same:
                        bad_sig += 1
                if tag != genomes[0][0]:
                    continue
                vcut = [x for x in d["vb"] if x[0] + cap.MS_1H <= c4p[-1][0]]
                dp = dict(d, c4=c4p, lows=[x[3] for x in c4p], ctx=ctxp,
                          vb=vcut, vts=[x[0] for x in vcut])
                for i in range(max(extp["warm"], k - 8), k):
                    v_p = cap.void_dens(dp, i, g)
                    v_f = cap.void_dens(d, i, g)
                    n_dens += 1
                    ok = (v_p is None and v_f is None) or (
                        v_p is not None and v_f is not None
                        and abs(v_p - v_f) < 1e-9)
                    if not ok:
                        bad_dens += 1
    out(f"  (а) заглядывания нет: сигналы и стопы сверены на {n_sig} барах "
        f"обрезанной истории")
    out(f"      ({len(genomes)} генома x 5 монет x 3 длины префикса), "
        f"расхождений {bad_sig};")
    out(f"      объёмная плотность сверена на {n_dens} барах, расхождений "
        f"{bad_dens}")
    tot = diff = 0
    for tag, g in genomes:
        for sym in SYMS:
            d = data[sym]
            r_eng = se3.run_setup("dump_long", g, d["c4"], d["ctx"], d["c15"],
                                  d["ts15"], LEV, symbol=sym,
                                  collect_diag=False, interval_min=TF)
            evs, ext = evs_of(data, sym, g, tag)
            r_my = cap.run_bars(d, g, evs, ext, (0, len(d["c4"])),
                                lambda i, e=evs: e[i]["ok"], lev=LEV,
                                ruin=True)
            a, b = r_eng["trades"], r_my["trades"]
            tot += max(len(a), len(b))
            if len(a) != len(b):
                diff += abs(len(a) - len(b))
                out(f"      {sym}: РАЗНОЕ ЧИСЛО сделок {len(a)}/{len(b)}")
                continue
            for x, y in zip(a, b):
                if (x["entry_ts"] != y["entry_ts"]
                        or abs(x["pnl"] - y["pnl"]) > 1e-9
                        or x["reason"] != y["reason"]):
                    diff += 1
    out(f"  (б) мой цикл против штатного se3.run_setup: {tot} сделок "
        f"({len(genomes)} генома x 5 монет), расхождений {diff}")
    return bad_sig == 0 and bad_dens == 0 and diff == 0


# =============================================================== БЛОК 2
def calibrate(data):
    out()
    out("БЛОК 2. КАЛИБРОВКА НА ОБУЧЕНИИ (holdout не смотрим)")
    out("-" * 78)
    out("  Решётка объявлена ДО расчёта: 4 стопа x 2 фильтра x 2 входа = 16.")
    out("  Правило выбора объявлено ДО расчёта: лучший exp_R при n>=25, но")
    out("  если его перевес над конфигурацией «по определению сетапа» меньше")
    out("  стандартной ошибки разницы — конфигурацию НЕ меняем (шум не повод).")
    out("  Случайности в калибровке нет: перебор полный и детерминированный.")
    out()
    res = {}
    for sk in STOPS:
        for fk in FILTERS:
            for ek in ENTRIES:
                tr, per = run_variant(data, sk, fk, ek, "train")
                res[(sk, fk, ek)] = agg(tr)
                res[(sk, fk, ek)]["per"] = per
    out(f"  {'стоп':18} {'фильтр':15} {'вход':15} {'n':>4} {'WR%':>6} "
        f"{'exp_R':>7} {'sumR':>7} {'PF':>5} {'DD%':>6} {'стоп%':>6} "
        f"{'маржа$':>7}")
    order = sorted(res.items(), key=lambda kv: -kv[1]["exp_r"])
    for (sk, fk, ek), a in order:
        out(f"  {sk:18} {fk:15} {ek:15} {a['n']:4} {a['wr']:6.1f} "
            f"{a['exp_r']:+7.3f} {a['sum_r']:+7.2f} {fmt_pf(a['pf'])} "
            f"{a['dd']:6.1f} {a['stop_pct']:6.2f} {a['margin']:7.2f}")
    base_a = res[DEFAULT_CHOICE]
    best_key = best_a = None
    for key, a in order:
        if a["n"] >= 25:
            best_key, best_a = key, a
            break
    out()
    out(f"  конфигурация «по определению сетапа» {DEFAULT_CHOICE}:")
    out(f"    exp_R={base_a['exp_r']:+.3f} (n={base_a['n']})")
    if best_key is None:
        chosen = DEFAULT_CHOICE
        out("  ни один вариант не набрал 25 сделок — оставляем её")
    else:
        d_exp = best_a["exp_r"] - base_a["exp_r"]
        se = se_diff(best_a["rs"], base_a["rs"])
        out(f"  лучший по exp_R {best_key}: exp_R={best_a['exp_r']:+.3f} "
            f"(n={best_a['n']})")
        out(f"  перевес {d_exp:+.3f}R при стандартной ошибке разницы "
            f"{se:.3f} -> "
            + ("больше ошибки, МЕНЯЕМ конфигурацию" if d_exp > se
               else "в пределах шума, конфигурацию НЕ меняем"))
        chosen = best_key if d_exp > se else DEFAULT_CHOICE
    out(f"  ЗАФИКСИРОВАНО (дальше не трогаем): стоп «{chosen[0]}», "
        f"{chosen[1]}, вход {chosen[2]}, плечо x{LEV}")
    # Сверка с тем, что записано как итог в capitulation.py: два файла не
    # должны разъехаться (иначе проверялось бы одно, а торговалось другое).
    g_sel = genome_of(chosen[0], chosen[2])
    mism = [k for k in g_sel if g_sel[k] != cap.GENOME_FINAL.get(k)]
    out(f"  сверка с GENOME_FINAL в capitulation.py: "
        + ("совпадает" if not mism else f"РАСХОЖДЕНИЕ по {mism}"))
    out()
    out("  ПЕРЕНОСИМОСТЬ ВЫБОРА (калибровка на 4 монетах -> контроль на 5-й,")
    out("  обучающее окно: не является ли выбор свойством одной монеты)")
    same = 0
    for held in SYMS:
        best, bkey = None, None
        for key, a in res.items():
            rs = [t["r"] for s in SYMS if s != held for t in a["per"][s]]
            if len(rs) < 20:
                continue
            m = statistics.mean(rs)
            if best is None or m > best:
                best, bkey = m, key
        rs_h = [t["r"] for t in res[bkey]["per"][held]]
        same += 1 if bkey == chosen else 0
        out(f"    без {held:9} лучший = {bkey[0]:17}/{bkey[1]:14}/{bkey[2]:14}"
            f" | на {held:9} он даёт "
            f"{(statistics.mean(rs_h) if rs_h else 0):+.3f}R (n={len(rs_h)})")
    out(f"    выбор совпал с зафиксированным в {same} случаях из {len(SYMS)}")
    return chosen, res


# =============================================================== БЛОК 3
def decompose(data, chosen):
    """Откуда берётся результат: тот же прибор на СЛУЧАЙНЫХ входах."""
    out()
    out("БЛОК 3. РАЗЛОЖЕНИЕ: что именно даёт результат")
    out("-" * 78)
    out("  Слева — сигнал сетапа, справа — ТОТ ЖЕ прибор (тот же стоп, тот же")
    out("  тейк, та же сетка) на СЛУЧАЙНЫХ барах той же частоты. Если прирост")
    out("  одинаковый, элемент — свойство прибора, а не знание о рынке.")
    out()
    varis = [
        ("2.0*ATR (дефолт)", "одиночный"),
        ("1.5*ATR", "одиночный"),
        ("минимум падения", "одиночный"),
        ("2.0*ATR (дефолт)", "сетка 2x0.8ATR"),
        ("1.5*ATR", "сетка 2x0.8ATR"),
        ("минимум падения", "сетка 2x0.8ATR"),
    ]
    table = {}
    for kind in ("train", "hold"):
        out(f"  --- {'ОБУЧЕНИЕ' if kind == 'train' else 'HOLDOUT'} ---")
        out(f"  {'стоп':18} {'вход':15} | {'сигнал: n':>10} {'WR%':>6} "
            f"{'exp_R':>7} | {'случайный n':>12} {'exp_R':>7} | "
            f"{'разница':>8}")
        for sk, ek in varis:
            tr, per = run_variant(data, sk, "без фильтра", ek, kind)
            a = agg(tr)
            n_by = {s: len(per[s]) for s in SYMS}
            ar, _, _ = pool_seeds(
                lambda sd, sk=sk, ek=ek, k=kind, n=n_by:
                rand_uniform(data, sk, ek, k, n, sd), RAND_SEEDS)
            table[(kind, sk, ek)] = (a, ar)
            out(f"  {sk:18} {ek:15} | {a['n']:10} {a['wr']:6.1f} "
                f"{a['exp_r']:+7.3f} | {ar['n']:12} {ar['exp_r']:+7.3f} | "
                f"{a['exp_r'] - ar['exp_r']:+8.3f}")
        out()
    # эффект сетки отдельно
    out("  ЭФФЕКТ СЕТКИ (сетка минус одиночный вход, один и тот же стоп):")
    for kind in ("train", "hold"):
        for sk in ("2.0*ATR (дефолт)", "1.5*ATR", "минимум падения"):
            a1 = table[(kind, sk, "одиночный")]
            a2 = table[(kind, sk, "сетка 2x0.8ATR")]
            out(f"    {('обучение' if kind == 'train' else 'holdout '):9} "
                f"{sk:18} сигналу {a2[0]['exp_r'] - a1[0]['exp_r']:+.3f}R, "
                f"СЛУЧАЙНОМУ входу {a2[1]['exp_r'] - a1[1]['exp_r']:+.3f}R")
    out("  Вывод читается прямо: сетка поднимает и сигнал, и случайный вход —")
    out("  это механика (цель считается от средней и потому ближе), а не "
        "знание.")
    out()
    out("  ЗЕРКАЛО (rally_short: тот же прибор, ЗЕРКАЛЬНЫЙ сетап «перегрев-")
    out("  шорт»). Движок требует зеркала: если работает только одна сторона,")
    out("  померена бета рынка; если обе — померен прибор, а не сетап.")
    sk, ek = chosen[0], chosen[2]
    g = genome_of(sk, ek)
    mir = {}
    for kind in ("train", "hold"):
        trades = []
        for sym in SYMS:
            d = data[sym]
            evs, ext = evs_of(data, sym, g, sk, setup="rally_short")
            rng = rng_of(d, ext, kind)
            trades += cap.run_bars(d, g, evs, ext, rng,
                                   lambda i, e=evs: e[i]["ok"], lev=LEV,
                                   setup="rally_short")["trades"]
        mir[kind] = agg(trades)
        a = mir[kind]
        out(f"    {'обучение' if kind == 'train' else 'holdout '}: "
            f"n={a['n']:3}  WR={a['wr']:5.1f}%  exp_R={a['exp_r']:+.3f}  "
            f"exp$={a['exp_usd']:+.3f}  дох={a['ret']:+6.1f}%")
    return table, mir


# =============================================================== БЛОК 4-6
def holdout_block(data, chosen):
    out()
    out("БЛОК 4. HOLDOUT (конфигурация зафиксирована на обучении)")
    out("-" * 78)
    d0 = data[SYMS[0]]
    h, n = d0["hold"], len(d0["c4"])
    out(f"  обучение {d_str(d0['c4'][0][0])}..{d_str(d0['c4'][h - 1][0])} "
        f"({h} баров 4ч), HOLDOUT "
        f"{d_str(d0['c4'][h][0])}..{d_str(d0['c4'][n - 1][0])} "
        f"({n - h} баров 4ч)")
    out()
    sk, fk, ek = chosen
    tr_ho, per_ho = run_variant(data, sk, fk, ek, "hold")
    a_ho = agg(tr_ho)
    n_by = {s: len(per_ho[s]) for s in SYMS}
    sig_by = {s: [t["bar_i"] for t in per_ho[s]] for s in SYMS}

    base_tr = []
    for sym in SYMS:
        d = data[sym]
        evs, ext = evs_of(data, sym, cap.GENOME_BASE, "БАЗА")
        base_tr += cap.run_bars(d, cap.GENOME_BASE, evs, ext,
                                (d["hold"], len(d["c4"])),
                                lambda i, e=evs: e[i]["ok"], lev=LEV)["trades"]
    a_base = agg(base_tr)

    a_rnd, rnd_means, rnd_rets = pool_seeds(
        lambda sd: rand_uniform(data, sk, ek, "hold", n_by, sd), RAND_SEEDS)
    a_atr, atr_means, _ = pool_seeds(
        lambda sd: rand_atr_matched(data, sk, ek, "hold", sig_by, sd),
        RAND_SEEDS)
    a_near, near_means, _ = pool_seeds(
        lambda sd: rand_near_dump(data, sk, ek, "hold", sig_by, sd),
        RAND_SEEDS)
    bh = buy_hold(data, "hold")

    out(f"  ИТОГ HOLDOUT И ЭТАЛОНЫ (плечо x{LEV}, 5 монет, издержки "
        f"проектные).")
    out("  У эталонов на 5 зёрен доходность и просадка — СРЕДНИЕ ПО ЗЕРНУ, "
        "а не по склейке.")
    line("КАПИТУЛЯЦИЯ (зафиксирована)", a_ho)
    line("эталон 1: базовый dump_long", a_base,
         "   (дефолтный геном движка)")
    line("эталон 2: случайный вход", a_rnd,
         f"   (5 зёрен, exp_R {min(rnd_means):+.3f}..{max(rnd_means):+.3f})",
         ret=a_rnd["ret_seed"])
    line("эталон 3: случайный ТОЙ ЖЕ вол-ти", a_atr,
         f"   (ATR +-{int(100 * ATR_TOL)}%, "
         f"{min(atr_means):+.3f}..{max(atr_means):+.3f})",
         ret=a_atr["ret_seed"])
    line("справочно: случайный «у слива»", a_near,
         "   (заведомо плохой эталон)", ret=a_near["ret_seed"])
    out(f"  {'эталон 4: купил и держал':34} "
        + ", ".join(f"{s.replace('USDT', '')} {bh[s]:+.0f}%" for s in SYMS)
        + f"; среднее {bh['СРЕДНЕЕ']:+.1f}%")
    out()
    others = {}
    for key in [(sk, "фильтр пустоты", ek), (sk, fk, "одиночный"),
                ("2.0*ATR (дефолт)", "без фильтра", "одиночный")]:
        if key == chosen:
            continue
        t_, _ = run_variant(data, key[0], key[1], key[2], "hold")
        others[key] = agg(t_)
        line(f"вариант: {key[0][:16]}/{key[2][:9]}"
             + ("/фильтр" if key[1] != "без фильтра" else ""), others[key])
    out()
    t_base = welch_t(a_ho["rs"], a_base["rs"])
    t_rnd = welch_t(a_ho["rs"], a_rnd["rs"])
    t_atr = welch_t(a_ho["rs"], a_atr["rs"])
    t_near = welch_t(a_ho["rs"], a_near["rs"])
    t_base_u = welch_t(a_ho["pnls"], a_base["pnls"])
    t_rnd_u = welch_t(a_ho["pnls"], a_rnd["pnls"])
    t_atr_u = welch_t(a_ho["pnls"], a_atr["pnls"])
    out(f"  ПЕРЕВЕС над эталонами (в R): базовый "
        f"{a_ho['exp_r'] - a_base['exp_r']:+.3f}R (t={t_base:+.2f}); "
        f"случайный {a_ho['exp_r'] - a_rnd['exp_r']:+.3f}R (t={t_rnd:+.2f}); "
        f"случайный ТОЙ ЖЕ вол-ти "
        f"{a_ho['exp_r'] - a_atr['exp_r']:+.3f}R (t={t_atr:+.2f})")
    out(f"  ПЕРЕВЕС в ДЕНЬГАХ на сделку: базовый "
        f"{a_ho['exp_usd'] - a_base['exp_usd']:+.3f}$ (t={t_base_u:+.2f}); "
        f"случайный {a_ho['exp_usd'] - a_rnd['exp_usd']:+.3f}$ "
        f"(t={t_rnd_u:+.2f}); ТОЙ ЖЕ вол-ти "
        f"{a_ho['exp_usd'] - a_atr['exp_usd']:+.3f}$ (t={t_atr_u:+.2f})")
    out(f"  ПОЧЕМУ R И ДЕНЬГИ РАСХОДЯТСЯ: при сетке выигрыш часто берётся "
        f"НЕПОЛНОЙ позицией")
    out(f"  (+2R на малом объёме), а проигрыш — полной. Доля сделок с "
        f"долитым коленом: сетап "
        f"{a_ho['grid_share']:.0f}%, случайный вход той же вол-ти "
        f"{a_atr['grid_share']:.0f}%;")
    out(f"  средняя задействованная маржа {a_ho['margin']:.2f}$ против "
        f"{a_atr['margin']:.2f}$ из {se3.MARGIN}$. Поэтому смотрим ОБА "
        f"столбца.")
    bp = block_boot_mean_neg(a_ho["trades"])
    ci = 1.96 * a_ho["sd"] / math.sqrt(max(1, a_ho["n"]))
    out(f"  средний R {a_ho['exp_r']:+.3f} +- {ci:.3f} (95% доверительный "
        f"интервал [{a_ho['exp_r'] - ci:+.3f}, {a_ho['exp_r'] + ci:+.3f}]), "
        f"t={a_ho['t']:+.2f}, p={p_two(a_ho['t']):.3f}")
    if bp is not None:
        out(f"  блочный бутстрэп по кварталам: средний R <= 0 в {bp:.1f}% "
            f"выборок (наивный t завышает значимость)")

    out()
    out("БЛОК 5. ПО МОНЕТАМ НА HOLDOUT (все пять, включая убыточные)")
    out("-" * 78)
    per_stats = {}
    for sym in SYMS:
        a = agg(per_ho[sym], n_base=1)
        per_stats[sym] = a
        out(f"  {sym:9} n={a['n']:3}  WR={a['wr']:5.1f}%  "
            f"exp_R={a['exp_r']:+.3f}  sumR={a['sum_r']:+6.2f}  "
            f"PF={fmt_pf(a['pf'])}  дох={a['ret']:+7.1f}%  DD={a['dd']:5.1f}%"
            f"  ликв={a['liq']}  B&H={bh[sym]:+7.1f}%")
    pos = sum(1 for s in SYMS if per_stats[s]["exp_r"] > 0)
    out(f"  монет с положительным exp_R: {pos} из {len(SYMS)}. ВАЖНО: монеты "
        f"сильно связаны")
    out("  (в прошлом исследовании календарное окно объясняло 39.4% "
        "дисперсии, монета — 1.1%),")
    out("  поэтому «5 из 5» — это НЕ пять независимых подтверждений.")

    out()
    out("БЛОК 6. ВОСПРОИЗВОДИМОСТЬ И УСТОЙЧИВОСТЬ")
    out("-" * 78)
    out(f"  случайный контроль, зёрна {RAND_SEEDS}:")
    for sd, m, r_ in zip(RAND_SEEDS, rnd_means, rnd_rets):
        out(f"    зерно {sd:3}: exp_R={m:+.3f}  доходность={r_:+7.1f}%")
    beat = sum(1 for m in rnd_means if m >= a_ho["exp_r"])
    out(f"  зёрен, где случайный вход не хуже конфигурации: {beat} из "
        f"{len(RAND_SEEDS)}; разброс "
        f"{statistics.mean(rnd_means):+.3f} +- "
        f"{statistics.pstdev(rnd_means):.3f}")
    srt = sorted(a_ho["trades"], key=lambda t: -t["r"])
    top3 = sum(t["r"] for t in srt[:3])
    out(f"  вклад 3 лучших сделок из {a_ho['n']}: {top3:+.2f}R из "
        f"{a_ho['sum_r']:+.2f}R "
        f"({100 * top3 / a_ho['sum_r']:.0f}% результата) — "
        + ("результат держится на единицах сделок"
           if abs(top3) > 0.5 * abs(a_ho["sum_r"])
           else "результат не держится на единицах сделок"))
    byq = {}
    for t in a_ho["trades"]:
        q = quarter_of(t["exit_ts"])
        byq.setdefault(q, []).append(t["r"])
    out("  по кварталам holdout: "
        + "; ".join(f"{d_str(min(t['exit_ts'] for t in a_ho['trades'] if quarter_of(t['exit_ts']) == q))[:7]}"
                    f" n={len(v)} R={sum(v):+.1f}"
                    for q, v in sorted(byq.items())))
    q_pos = sum(1 for v in byq.values() if sum(v) > 0)
    q_best = max(byq.values(), key=lambda v: sum(v))
    q_share = 100.0 * sum(q_best) / a_ho["sum_r"] if a_ho["sum_r"] else 0.0
    out(f"  прибыльных кварталов: {q_pos} из {len(byq)}; лучший квартал даёт "
        f"{sum(q_best):+.1f}R = {q_share:.0f}% всего результата")
    if q_share > 90:
        out("  ЭТО ГЛАВНАЯ ОГОВОРКА: весь плюс экзамена сделан в ОДНОМ "
            "квартале, остальные три")
        out("  в сумме отрицательны — то есть измерено одно рыночное "
            "событие, а не устойчивое правило.")
    return dict(a_ho=a_ho, per_ho=per_ho, per_stats=per_stats, a_base=a_base,
                a_rnd=a_rnd, a_atr=a_atr, a_near=a_near, bh=bh, others=others,
                n_by=n_by, sig_by=sig_by, rnd_means=rnd_means,
                atr_means=atr_means, t_base=t_base, t_rnd=t_rnd, t_atr=t_atr,
                t_near=t_near, boot_neg=bp, ci=ci, q_pos=q_pos,
                q_tot=len(byq), top3=top3, q_share=q_share,
                t_atr_usd=t_atr_u)


# =============================================================== БЛОК 7
def risk_block(data, chosen, ho):
    out()
    out("БЛОК 7. РИСК")
    out("-" * 78)
    a = ho["a_ho"]
    out(f"  holdout: сделок {a['n']}, ликвидаций {a['liq']}, макс. просадка "
        f"портфеля {a['dd']:.1f}%, медианный стоп {a['stop_pct']:.2f}% цены,")
    out(f"  медианное удержание {a['hold_h']:.1f} ч, средняя задействованная "
        f"маржа {a['margin']:.2f}$ из {se3.MARGIN}$ на цикл")
    out(f"  выходы: " + ", ".join(f"{k}={v}"
                                  for k, v in sorted(a["kinds"].items())))
    out()
    out("  ЛЕСТНИЦА ПЛЕЧА (вся история, конфигурация зафиксирована):")
    out(f"  {'плечо':>6} {'сделок':>7} {'потолок стопа':>14} {'ликв':>5} "
        f"{'exp_R':>7} {'доход':>8} {'просадка':>9} {'слив монет':>11} "
        f"{'P(разорения)':>13}")
    ladder = {}
    for lev in cap.LEV_LADDER:
        t1, p1 = run_variant(data, chosen[0], chosen[1], chosen[2], "train",
                             lev=lev)
        t2, p2 = run_variant(data, chosen[0], chosen[1], chosen[2], "hold",
                             lev=lev)
        allt = t1 + t2
        aa = agg(allt)
        ruined = 0
        for sym in SYMS:
            bal = se3.START
            for t in sorted(p1[sym] + p2[sym], key=lambda x: x["exit_ts"]):
                if bal < se3.MARGIN:
                    break
                bal += t["pnl"]
            ruined += 1 if bal < se3.MARGIN else 0
        boots = [b for b in (block_boot_ruin(allt, seed=s)
                             for s in BOOT_SEEDS) if b]
        p_ruin = statistics.mean([b["p_ruin"] for b in boots]) if boots else 0
        p05 = statistics.mean([b["p05"] for b in boots]) if boots else 0
        cs = 100 * 0.8 * se3.liq_frac(lev)
        ladder[lev] = dict(a=aa, ruined=ruined, p_ruin=p_ruin, cap_stop=cs,
                           p05=p05)
        out(f"  x{lev:<5} {aa['n']:7} {cs:13.1f}% {aa['liq']:5} "
            f"{aa['exp_r']:+7.3f} {aa['ret']:+7.1f}% {aa['dd']:8.1f}% "
            f"{ruined:6} из {len(SYMS)} {p_ruin:12.1f}%")
    out("  Сделок меньше при большом плече не потому, что фильтр строже, а")
    out("  потому что штатный стоп ШИРЕ предельно допустимого "
        "(0.8*(1/плечо-0.005)):")
    out("  движок физически не может открыть позицию. Это прямой ответ на "
        "просьбу x20-25.")
    out()
    la = ladder[LEV]["a"]
    risks = [abs(t["pnl"] / t["r"]) for t in la["trades"] if t["r"]] or [0.0]
    med_risk = statistics.median(risks)
    worst, bal, peak = 0.0, se3.START, se3.START
    for t in sorted(la["trades"], key=lambda x: x["exit_ts"]):
        bal += t["pnl"]
        peak = max(peak, bal)
        worst = max(worst, peak - bal)
    out(f"  ТРЕБУЕМЫЙ КАПИТАЛ при x{LEV} и марже {se3.MARGIN}$ на цикл: риск "
        f"сделки медиана {med_risk:.2f}$ "
        f"({100 * med_risk / se3.START:.1f}% депозита {se3.START}$).")
    out(f"  Худшая просадка в деньгах за всю историю {worst:.2f}$ на монету; "
        f"чтобы она укладывалась")
    out(f"  в 25% депозита, нужно от {worst / 0.25:.0f}$ на монету "
        f"({len(SYMS) * worst / 0.25:.0f}$ на портфель из {len(SYMS)}) при "
        f"той же марже {se3.MARGIN}$.")
    out(f"  5-й перцентиль итога по бутстрэпу при x{LEV}: "
        f"{ladder[LEV]['p05']:.1f}$ от старта {se3.START}$; при x10 — "
        f"{ladder[10]['p05']:.1f}$.")
    out("  ОГОВОРКА к вероятности разорения: бутстрэп ресэмплит сделки КАК "
        "ИЗМЕРЕНО, то есть уже")
    out("  с недоказанным плюсом. Если истинная экспектанси нулевая, риск "
        "разорения выше:")
    out("  безопасность плеча x2-x5 доказана лестницей (ширина стопа, "
        "ликвидации), а не этой цифрой.")
    return ladder


# =============================================================== БЛОК 8
def significance_block(ho, calib_n=16):
    out()
    out("БЛОК 8. ЗНАЧИМОСТЬ С ПОПРАВКОЙ НА ВСЮ ВОЛНУ")
    out("-" * 78)
    total = WAVE_PRIOR_CHECKS + N_MY_CHECKS
    p_bonf = 0.05 / total
    lo, hi = 0.0, 12.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if p_two(mid) > p_bonf:
            lo = mid
        else:
            hi = mid
    t_thr = (lo + hi) / 2
    out(f"  Проверок в волне: сетка/плечо 96 + индикаторы 53 + уровни 42 + "
        f"литература 10 = {WAVE_PRIOR_CHECKS};")
    out(f"  мои: калибровка {calib_n} + разложение 12 + эталоны 6 + лестница "
        f"6 + монеты 5 = {N_MY_CHECKS}.")
    out(f"  ИТОГО {total} проверок -> порог Бонферрони p < {p_bonf:.6f}, то "
        f"есть |t| >= {t_thr:.2f}.")
    out(f"  Чисто случайно при {total} проверках ожидается "
        f"{0.05 * total:.1f} «значимых на уровне 0.05».")
    out()
    rows = [
        ("holdout: средний R против нуля", ho["a_ho"]["t"]),
        ("holdout: перевес над базовым dump_long", ho["t_base"]),
        ("holdout: перевес над случайным входом", ho["t_rnd"]),
        ("holdout: перевес над случайным ТОЙ ЖЕ вол-ти", ho["t_atr"]),
        ("holdout: перевес над «у слива» (плохой эталон)", ho["t_near"]),
    ]
    out(f"  {'контраст':48} {'t':>7} {'p':>9} {'Бонферрони':>12}")
    for nm, t in rows:
        out(f"  {nm:48} {t:+7.2f} {p_two(t):9.4f} "
            f"{('ПРОШЁЛ' if abs(t) >= t_thr else 'нет'):>12}")
    sd = ho["a_ho"]["sd"]
    a = ho["a_ho"]
    need = t_thr * sd / math.sqrt(max(1, a["n"]))
    out()
    out(f"  При {a['n']} сделках holdout и сигме R {sd:.2f} доказуемым "
        f"(после поправки) было бы")
    out(f"  только преимущество от {need:+.2f}R на сделку. «Не доказано» "
        f"здесь — ожидаемый исход")
    out("  при любой умеренной силе сигнала, а не приговор сам по себе.")
    out()
    out("  СКОЛЬКО НУЖНО СДЕЛОК, ЧТОБЫ ВОПРОС ЗАКРЫТЬ (мощность, t=2, без "
        "поправки):")
    for nm, eff in (("наблюдаемое exp_R против нуля", a["exp_r"]),
                    ("перевес над случайным той же вол-ти",
                     a["exp_r"] - ho["a_atr"]["exp_r"])):
        if eff > 0:
            n_need = (2.0 * sd / eff) ** 2
            out(f"    {nm:38} {eff:+.3f}R -> {n_need:6.0f} сделок "
                f"({n_need / max(0.1, a['n'] / 10.7):5.0f} мес при текущей "
                f"частоте)")
    return t_thr


# =============================================================== БЛОК 9
def write_json(data, chosen, ho, ladder, t_thr, calib, verdict, reason,
               enabled, watch, rec_lev, rec_lev_fwd=0):
    g_ch = genome_of(chosen[0], chosen[2])
    a_ho, a_base, a_rnd, a_atr, bh = (ho["a_ho"], ho["a_base"], ho["a_rnd"],
                                      ho["a_atr"], ho["bh"])
    d0 = data[SYMS[0]]
    h, n = d0["hold"], len(d0["c4"])
    period = f"{d_str(d0['c4'][h][0])}..{d_str(d0['c4'][n - 1][0])}"
    train_period = f"{d_str(d0['c4'][0][0])}..{d_str(d0['c4'][h - 1][0])}"
    tr_tr, per_tr = run_variant(data, chosen[0], chosen[1], chosen[2], "train")
    a_tr = agg(tr_tr)

    def pf_(x):
        return None if x in (None, float("inf")) else round(x, 2)

    def sym_block(sym):
        a = ho["per_stats"][sym]
        at = agg(per_tr[sym], n_base=1)
        return dict(
            symbol=sym, interval_min=TF, enabled=False, watch=bool(watch),
            verdict=verdict, rec_lev=rec_lev,
            reason=(f"holdout: n={a['n']}, exp_R={a['exp_r']:+.3f}, "
                    f"PF={pf_(a['pf'])}, доходность {a['ret']:+.1f}% при "
                    f"просадке {a['dd']:.1f}%; «купил и держал» "
                    f"{bh[sym]:+.1f}%"),
            holdout=dict(n=a["n"], wr=round(a["wr"], 1),
                         exp_r=round(a["exp_r"], 3),
                         sum_r=round(a["sum_r"], 2), pf=pf_(a["pf"]),
                         ret=round(a["ret"], 1), dd=round(a["dd"], 1),
                         liq=a["liq"], t=round(a["t"], 2), period=period,
                         exit_kinds=a["kinds"], lev=LEV),
            benchmarks=dict(buy_hold_ret=round(bh[sym], 1)),
            stats=dict(n=at["n"], wr=round(at["wr"], 1),
                       exp_r=round(at["exp_r"], 3),
                       sum_r=round(at["sum_r"], 2), pf=pf_(at["pf"]),
                       ret=round(at["ret"], 1), dd=round(at["dd"], 1),
                       period=train_period,
                       in_sample_warning="обучающая часть, не доказательство"),
            genome=g_ch)

    node = dict(
        title="капитуляция-лонг: слив + разворотная свеча (итог волны)",
        interval_min=TF, symbols=list(SYMS), genome=g_ch,
        enabled=bool(enabled), watch=bool(watch), rec_lev=rec_lev,
        rec_lev_forward_test=rec_lev_fwd, rec_lev_max=LEV,
        verdict=verdict, reason=reason,
        # Повторов ОТБОРА здесь нет и быть не может: калибровка полностью
        # детерминированная (перебор 16 объявленных вариантов), случайность
        # есть только в эталонах. Поэтому repro_passes=0, а зёрна эталона
        # вынесены отдельным полем — чтобы сайт не прочитал «5 из 5 отборов».
        repro_runs=0, repro_passes=0,
        repro_note=(f"калибровка детерминированная (перебор 16 вариантов), "
                    f"повторов отбора нет; {len(RAND_SEEDS)} зерна "
                    f"использованы для эталонов, конфигурация обошла "
                    f"случайный вход на "
                    f"{sum(1 for m in ho['rnd_means'] if a_ho['exp_r'] > m)} "
                    f"зёрнах из {len(RAND_SEEDS)}"),
        config=dict(
            stop=chosen[0], filter=chosen[1], entry=chosen[2],
            lev_tested=LEV,
            chosen_by="лучший exp_R на ОБУЧАЮЩЕЙ части из 16 объявленных "
                      "вариантов; holdout в выборе не участвовал",
            elements=[dict(element=k, evidence=v) for k, v in cap.describe()],
            rejected=[
                dict(element="плечо x20-25 (просьба владельца)",
                     evidence="штатный стоп шире предела 0.8*(1/плечо-0.005): "
                              "при x20 берётся часть сигналов, при x25 почти "
                              "ни одного; exp_R от плеча не зависит"),
                dict(element="сетка как источник прибыли",
                     evidence="сетка даёт СЛУЧАЙНОМУ входу тот же прирост, "
                              "что и сигналу — это механика цели от средней, "
                              "а не знание; оставлена только как управление "
                              "просадкой"),
                dict(element="фильтры EMA/RSI/MACD/ADX/смартмани",
                     evidence="53 проверки, 0 пережили поправку; три лучших "
                              "сменили знак на holdout"),
                dict(element="отскок от горизонтальных объёмов",
                     evidence="знак ОБРАТНЫЙ (-0.237R против +0.049R); как "
                              "запрет проверен (фильтр пустоты) и прироста "
                              "сверх шума не дал"),
            ]),
        holdout=dict(
            n=a_ho["n"], wr=round(a_ho["wr"], 1),
            exp_r=round(a_ho["exp_r"], 3), sum_r=round(a_ho["sum_r"], 2),
            pf=pf_(a_ho["pf"]), ret=round(a_ho["ret"], 1),
            dd=round(a_ho["dd"], 1), liq=a_ho["liq"], t=round(a_ho["t"], 2),
            p=round(p_two(a_ho["t"]), 4),
            ci95=[round(a_ho["exp_r"] - ho["ci"], 3),
                  round(a_ho["exp_r"] + ho["ci"], 3)],
            boot_share_neg=(None if ho["boot_neg"] is None
                            else round(ho["boot_neg"], 1)),
            med_stop_pct=round(a_ho["stop_pct"], 2),
            med_hold_h=round(a_ho["hold_h"], 1),
            best_quarter_share=round(ho["q_share"], 0),
            quarters_positive=f"{ho['q_pos']}/{ho['q_tot']}",
            trades_per_month=round(a_ho["n"] / 10.7, 1),
            exit_kinds=a_ho["kinds"], period=period, lev=LEV),
        benchmarks=dict(
            base_dump_long=dict(n=a_base["n"], wr=round(a_base["wr"], 1),
                                exp_r=round(a_base["exp_r"], 3),
                                ret=round(a_base["ret"], 1),
                                pf=pf_(a_base["pf"])),
            random_entry=dict(n=a_rnd["n"], exp_r=round(a_rnd["exp_r"], 3),
                              wr=round(a_rnd["wr"], 1),
                              ret=round(a_rnd["ret"], 1), seeds=RAND_SEEDS,
                              exp_r_spread=[round(min(ho["rnd_means"]), 3),
                                            round(max(ho["rnd_means"]), 3)]),
            random_same_volatility=dict(
                n=a_atr["n"], exp_r=round(a_atr["exp_r"], 3),
                wr=round(a_atr["wr"], 1), ret=round(a_atr["ret"], 1),
                note="главный эталон: ATR того же уровня, +-25%"),
            random_near_dump=dict(n=ho["a_near"]["n"],
                                  exp_r=round(ho["a_near"]["exp_r"], 3),
                                  note="заведомо плохой эталон, вход внутрь "
                                       "падения"),
            buy_hold={s: round(bh[s], 1) for s in list(SYMS) + ["СРЕДНЕЕ"]},
            mirror_rally_short=dict(
                n=ho["mirror"]["hold"]["n"],
                exp_r=round(ho["mirror"]["hold"]["exp_r"], 3),
                ret=round(ho["mirror"]["hold"]["ret"], 1),
                note="зеркальный сетап тем же прибором на том же окне: тоже "
                     "в плюсе, значит направление не доказано"),
            edge_vs_base=round(a_ho["exp_r"] - a_base["exp_r"], 3),
            edge_vs_random=round(a_ho["exp_r"] - a_rnd["exp_r"], 3),
            edge_vs_random_same_vol=round(a_ho["exp_r"] - a_atr["exp_r"], 3)),
        significance=dict(
            wave_checks=WAVE_PRIOR_CHECKS + N_MY_CHECKS,
            bonferroni_t=round(t_thr, 2),
            t_vs_zero=round(a_ho["t"], 2), t_vs_base=round(ho["t_base"], 2),
            t_vs_random=round(ho["t_rnd"], 2),
            t_vs_random_same_vol=round(ho["t_atr"], 2), passed=False),
        risk=dict(
            ladder={f"x{k}": dict(n=v["a"]["n"], liq=v["a"]["liq"],
                                  exp_r=round(v["a"]["exp_r"], 3),
                                  ret=round(v["a"]["ret"], 1),
                                  dd=round(v["a"]["dd"], 1),
                                  ruined_coins=v["ruined"],
                                  p_ruin=round(v["p_ruin"], 1),
                                  max_stop_pct=round(v["cap_stop"], 1))
                    for k, v in ladder.items()},
            margin_per_cycle=se3.MARGIN, start_per_symbol=se3.START,
            owner_request_lev="x20-25 отклонено, см. ladder",
            grid_request="сетка оставлена как управление просадкой, не как "
                         "источник прибыли"),
        stats=dict(n=a_tr["n"], wr=round(a_tr["wr"], 1),
                   exp_r=round(a_tr["exp_r"], 3),
                   sum_r=round(a_tr["sum_r"], 2), pf=pf_(a_tr["pf"]),
                   ret=round(a_tr["ret"], 1), dd=round(a_tr["dd"], 1),
                   period=train_period,
                   in_sample_warning="обучающая часть, использована для "
                                     "выбора конфигурации — не доказательство"),
        calibration=[dict(stop=k[0], filter=k[1], entry=k[2], n=v["n"],
                          wr=round(v["wr"], 1), exp_r=round(v["exp_r"], 3),
                          sum_r=round(v["sum_r"], 2))
                     for k, v in sorted(calib.items(),
                                        key=lambda kv: -kv[1]["exp_r"])],
        by_symbol={s: sym_block(s) for s in SYMS})
    with io.open(JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump({f"dump_long@{TF}": node}, fh, ensure_ascii=False, indent=2)
    out()
    out(f"БЛОК 9. {os.path.basename(JSON_PATH)} записан "
        f"({os.path.getsize(JSON_PATH)} байт): ключ dump_long@{TF}, поля "
        f"enabled/watch/verdict/")
    out("  reason/holdout/benchmarks/stats/genome/rec_lev + risk + "
        "calibration + by_symbol по 5 монетам")


# =============================================================== main
def main():
    t0 = time.time()
    out("ИТОГОВАЯ КАПИТУЛЯЦИЯ-ЛОНГ: СБОРКА И ЧЕСТНАЯ ПРОВЕРКА")
    out("=" * 78)
    out("Правило сборки объявлено ДО расчётов: элемент включается, только "
        "если пережил")
    out("проверку значимости с поправкой на всю волну И не воспроизводится "
        "случайным")
    out("входом той же частоты. Всё, что не прошло, названо с числом-"
        "основанием.")
    out()
    out("КОНФИГУРАЦИЯ: ЭЛЕМЕНТ -> ЗАМЕР, КОТОРЫЙ ЕГО ОПРАВДЫВАЕТ")
    out("-" * 78)
    for k, v in cap.describe():
        out(f"  {k:26} | {v}")
    out()

    data = load_all()
    out("БЛОК 0. ДАННЫЕ")
    out("-" * 78)
    for sym in SYMS:
        d = data[sym]
        out(f"  {sym:9} 4ч {len(d['c4']):5} баров "
            f"{d_str(d['c4'][0][0])}..{d_str(d['c4'][-1][0])}  "
            f"15м {len(d['c15']):6}  объём(1ч) {len(d['vb']):6}  "
            f"граница обучение/holdout: бар {d['hold']}")
    out()

    genomes = [("минимум падения", genome_of("минимум падения", "одиночный"))]
    ok = selfcheck(data, genomes)
    chosen, calib = calibrate(data)
    # самопроверка ещё раз — уже на ЗАФИКСИРОВАННОМ геноме
    g_ch = genome_of(chosen[0], chosen[2])
    out()
    out("  ПОВТОР САМОПРОВЕРКИ НА ЗАФИКСИРОВАННОМ ГЕНОМЕ (стоп и сетка у него")
    out("  другие, поэтому проверяем заново, а не ссылаемся на блок 1):")
    ok2 = selfcheck(data, [(chosen[0], g_ch)], header=False)
    if not (ok and ok2):
        out("  САМОПРОВЕРКИ НЕ ПРОЙДЕНЫ — результатам ниже верить нельзя")

    _, mirror = decompose(data, chosen)
    ho = holdout_block(data, chosen)
    ho["mirror"] = mirror
    ladder = risk_block(data, chosen, ho)
    t_thr = significance_block(ho, calib_n=16)

    # ------------------------------------------------------------- вердикт
    a_ho, a_rnd, a_atr, a_base, bh = (ho["a_ho"], ho["a_rnd"], ho["a_atr"],
                                      ho["a_base"], ho["bh"])
    months = 10.7
    edge = a_ho["exp_r"] - a_atr["exp_r"]
    mir_ho = ho["mirror"]["hold"]
    sig_ok = abs(a_ho["t"]) >= t_thr and abs(ho["t_atr"]) >= t_thr
    enabled = bool(sig_ok and a_ho["exp_r"] > 0 and edge > 0)
    watch = bool((not enabled) and a_ho["exp_r"] > 0 and edge > 0
                 and a_ho["exp_r"] > max(ho["rnd_means"]))
    rec_lev = 0            # для боевого запуска — ноль в любом случае
    rec_lev_fwd = 3 if watch else 0
    if enabled:
        verdict = "ТОРГОВАТЬ можно: преимущество доказано на экзамене"
    elif watch:
        verdict = ("НЕ БОЕВОЙ ЗАПУСК, только наблюдение/форвард-тест: на "
                   "экзамене знак положительный и лучше всех эталонов, но "
                   "преимущество НЕ доказано, а зеркальный сетап на том же "
                   "окне тоже в плюсе")
    else:
        verdict = ("НЕ ТОРГОВАТЬ: преимущества над случайным входом той же "
                   "частоты и волатильности нет")
    reason = (f"holdout {a_ho['n']} сделок, exp_R {a_ho['exp_r']:+.3f} "
              f"(95% ДИ [{a_ho['exp_r'] - ho['ci']:+.3f}, "
              f"{a_ho['exp_r'] + ho['ci']:+.3f}], t={a_ho['t']:+.2f}); "
              f"случайный вход ТОЙ ЖЕ волатильности {a_atr['exp_r']:+.3f}R, "
              f"перевес всего {edge:+.3f}R (t={ho['t_atr']:+.2f}); "
              f"зеркальный rally_short на том же окне {mir_ho['exp_r']:+.3f}R "
              f"— значит померен прибор, а не направление; нужен "
              f"|t|>={t_thr:.2f} после поправки на "
              f"{WAVE_PRIOR_CHECKS + N_MY_CHECKS} проверок волны")

    write_json(data, chosen, ho, ladder, t_thr, calib, verdict, reason,
               enabled, watch, rec_lev, rec_lev_fwd)

    out()
    out("БЛОК 10. ЧЕСТНЫЙ ИТОГ")
    out("=" * 78)
    out(f"  Конфигурация: слив >=6% за 2 суток + RSI14<30 + зелёная свеча; "
        f"стоп «{chosen[0]}»,")
    out(f"  тейк 2R, вход {chosen[2]}, {chosen[1]}, плечо x{LEV}, 4ч, 5 монет.")
    out(f"  Экзамен {months:.1f} мес: {a_ho['n']} сделок "
        f"({a_ho['n'] / months:.1f}/мес на 5 монет), WR {a_ho['wr']:.1f}%, "
        f"exp_R {a_ho['exp_r']:+.3f}, PF {fmt_pf(a_ho['pf']).strip()},")
    out(f"  доходность {a_ho['ret']:+.1f}% ({a_ho['ret'] / months:+.1f}% в "
        f"месяц) при просадке {a_ho['dd']:.1f}%, ликвидаций {a_ho['liq']}.")
    out(f"  Эталоны: базовый dump_long {a_base['exp_r']:+.3f}R "
        f"({a_base['ret']:+.1f}%), случайный вход {a_rnd['exp_r']:+.3f}R, "
        f"случайный")
    out(f"  ТОЙ ЖЕ волатильности {a_atr['exp_r']:+.3f}R "
        f"({a_atr['ret_seed']:+.1f}% на зерно), купил-и-держал "
        f"{bh['СРЕДНЕЕ']:+.1f}%.")
    out(f"  ЧЕСТНЫЙ ПЕРЕВЕС над честным эталоном: {edge:+.3f}R на сделку "
        f"при t={ho['t_atr']:+.2f} — это НЕ значимо")
    out(f"  (нужно |t|>={t_thr:.2f} после поправки на всю волну; даже без "
        f"поправки нужно 1.96).")
    out(f"  ЗЕРКАЛО: тот же прибор на «перегрев-шорт» на том же экзамене даёт "
        f"{mir_ho['exp_r']:+.3f}R "
        f"(n={mir_ho['n']}),")
    out("  то есть плюс на holdout получают ОБЕ стороны — это подпись "
        "прибора, а не знания рынка.")
    out()
    out(f"  ВЕРДИКТ: {verdict}")
    out()
    out("  ГОДИТСЯ ЛИ ДЛЯ РЕАЛЬНОЙ ТОРГОВЛИ: НЕТ — не как доказанное "
        "преимущество.")
    out("  Четыре причины, каждая самостоятельная: (1) 95% интервал "
        "экспектанси включает ноль")
    out(f"  ([{a_ho['exp_r'] - ho['ci']:+.3f}, "
        f"{a_ho['exp_r'] + ho['ci']:+.3f}]); (2) перевес над случайным "
        f"входом ТОЙ ЖЕ волатильности")
    out(f"  {edge:+.3f}R неотличим от нуля (t={ho['t_atr']:+.2f}); "
        f"(3) зеркальный сетап на том же окне тоже в плюсе;")
    out(f"  (4) {ho['q_share']:.0f}% результата экзамена сделано в ОДНОМ "
        f"квартале из четырёх.")
    if watch or enabled:
        out()
        out(f"  Если владелец всё же хочет ФОРВАРД-ТЕСТ (не боевой запуск): "
            f"плечо x{rec_lev_fwd}, не выше x{LEV};")
        out(f"  5 монет {', '.join(s.replace('USDT', '') for s in SYMS)}; "
            f"ожидания по экзаменационному окну —")
        out(f"  {a_ho['ret'] / months * rec_lev_fwd / LEV:+.1f}% в месяц на "
            f"портфель при просадке до "
            f"{a_ho['dd'] * rec_lev_fwd / LEV * 1.5:.0f}%, "
            f"{a_ho['n'] / months:.1f} сделки в месяц,")
        out(f"  средний срок сделки {a_ho['hold_h']:.0f} ч, капитал от "
            f"{len(SYMS) * 46:.0f}$ (см. блок 7). Это ОЖИДАНИЯ ПО ОДНОМУ "
            f"ОКНУ,")
        out("  а не доказанная доходность: окно с нулём или минусом "
            "полностью совместимо с измеренным.")
        out("  Критерий остановки объявить заранее: просадка глубже 20% или "
            "30 сделок подряд с exp_R<0.")
    out()
    out("  Плечо x20-25 (просьба владельца) ОТКЛОНЕНО измерением: см. "
        "лестницу плеча в блоке 7 —")
    out("  штатный стоп шире предела ликвидации, движок теряет от 60% "
        "сигналов, доход уходит в минус.")
    out("  Сетка оставлена, но НЕ как источник прибыли: тот же прирост она "
        "даёт случайному входу")
    out("  (блок 3), её польза — просадка (35.6% -> 10.3% на holdout) и ноль "
        "ликвидаций.")
    out()
    out(f"  Прогон {time.time() - t0:.1f} с")

    with io.open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_LINES) + "\n")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:
            pass
    main()
