# -*- coding: utf-8 -*-
"""Честная оценка кандидата отбора: хвост, вырожденность, контроль.

ЗАЧЕМ ЭТОТ ФАЙЛ ВООБЩЕ ПОЯВИЛСЯ

Балл экзамена в движке — `evolution2.oos_score` = p25 + 0.5 x медиана
месячных доходностей. Обе величины описывают СЕРЕДИНУ распределения: одна
катастрофическая просадка внутри месяца сдвигает медиану на один месяц из
десяти и почти не трогает балл. Для сетки усреднения это ровно слепое пятно:
grid_ruin.py измерил, что счёт убивает не один обвал (максимум потери за цикл
ограничен ликвидацией: -0.96R = -24% капитала при марже 25%), а СЕРИЯ мелких
минусов и редкие крупные убытки. Стратегия с красивым винрейтом 48-53% и
«маленькими» убытками может иметь отрицательное матожидание, которое на
200+ циклах в год гарантированно съедает счёт.

Поэтому приёмка волны не имеет права опираться на одно число. Здесь собраны:
  * ворота по ХВОСТУ      — худший месяц, худшая сделка, ликвидации,
                            плавающая просадка, риск руина (методика
                            grid_ruin.py: P(слив за год) < 5% и просадка
                            p95 < 30%);
  * ворота по ВЫРОЖДЕННОСТИ — минимум сделок, минимум сделок в месяц,
                            минимальная экспозиция. Конфиг, который почти не
                            торгует, выигрывает у убыточной базы просто
                            потому, что «ноль больше минуса» — так волна v11
                            выдала SOL вердикт adopt при 36 сделках и -14.5%
                            за 3.2 года;
  * инструменты честной оценки из REVIEW.md — медиана соседей (узкий пик =
    подгонка), контроль случайными геномами (эталон — не ноль, а случайный
    вход) и поправка на множественность проверок (Бонферрони).

ЧЕГО ЭТОТ ФАЙЛ НЕ ДЕЛАЕТ. Он не переотбирает и не реабилитирует конфиги,
которые уже стоят в config.py: они получены по СТАРОЙ схеме walk-forward с
утечкой, и никакие ворота задним числом этого не исправляют. Честные конфиги
получаются только новым прогоном отбора на исправленном протоколе.

ГРАНИЦЫ ПРИМЕНИМОСТИ ЗАМЕРА РИСКА. evolution2.run5 считает счёт $20 с
ФИКСИРОВАННОЙ маржой $5 и обрывает прогон, как только баланс упал ниже маржи.
Значит набор сделок обрезан ровно на катастрофе, и бутстрап по нему завышает
частоту катастроф. Полный (необрезанный) набор циклов восстанавливается
симулятором grid_ruin.sim_cycle по входам прогона на x1 — там оценка риска
точнее. Здесь бутстрап нужен как ВОРОТА («это точно опасно»), а не как
итоговая цифра риска.
"""

import random
import statistics

import evolution2 as e2

try:
    import config
    FRAC = getattr(config, "MARGIN_FRACTION", 0.25)
except Exception:            # pragma: no cover — на случай запуска без config
    FRAC = 0.25

# --- пороги ворот по хвосту --------------------------------------------------
# Все пороги — решение владельца, а не свойство рынка, поэтому они собраны
# здесь одним блоком и объяснены. Менять их ПОСЛЕ прогона под результат нельзя:
# это ровно тот самый самообман, ради которого написан весь файл.

WORST_MONTH_MIN = -10.0   # %: DD_CAP волн v10/v12 = 20% на всю историю,
                          # значит один месяц не должен съедать её половину
WORST_TRADE_MIN_R = -0.60  # R: ликвидация стоит -0.96R (grid_ruin, разд. 2);
                          # порог держит худшую сделку заметно ниже неё
MAX_LIQ = 0               # ликвидаций на экзамене быть не должно вообще
DD_FLOAT_MAX = 25.0       # %: плавающая просадка всегда хуже просадки по
                          # закрытым сделкам, поэтому потолок чуть выше 20%
RUIN_MAX = 5.0            # %: P(слив за год) — критерий «безопасного плеча»
DD95_MAX = 30.0           # %: просадка p95 — второй критерий оттуда же
                          # (grid_ruin.py, раздел 4)

# --- пороги ворот по вырожденности -------------------------------------------
MIN_TRADES = 20           # на экзаменационном окне (~10.8 мес)
MIN_TPM = 1.0             # сделок в месяц — та же граница, что в
                          # evolution12.fitness12 (<1 сделки в месяц = штраф)
MIN_EXPOSURE = 0.02       # доля баров в рынке: конфиг, стоящий вне рынка
                          # 98% времени, «выигрывает» отсутствием риска,
                          # а не преимуществом

# --- параметры бутстрапа риска (как в grid_ruin.py) --------------------------
RUIN_LEVEL = 0.20         # «слив» = капитал ниже 20% от старта
NBOOT = 800               # меньше, чем 2000 в grid_ruin: здесь это ворота,
                          # а не отчётная цифра
BLOCK = (10, 20)          # блочный бутстрап сохраняет СЕРИИ убытков
MIN_RS_FOR_BOOT = 10
SEED = 20260814

# --- параметры инструментов честной оценки -----------------------------------
NEIGH_K = 8               # сколько соседей смотреть
NEIGH_SCALE = 0.08        # сдвиг гена: доля его диапазона
RANDOM_K = 30             # сколько случайных геномов в контроле
MIN_CONTROL_TRADES = 10   # сделок на окне, ниже которых случайный геном не
                          # является «случайным входом»: он вообще не входит,
                          # и его нулевой балл меряет отказ от торговли.
                          # Половина от MIN_TRADES: контрольному геному
                          # позволено быть вдвое ленивее кандидата, но не
                          # бездействовать. ЗАМЕР (LTC, окно 8.4 мес,
                          # пространство генов v12): из 30 случайных геномов
                          # 27 не делают НИ ОДНОЙ сделки, максимум у
                          # остальных — 5. То есть в этом пространстве
                          # контроль случайными геномами чаще всего собрать
                          # нельзя; правильный ответ на это — сказать
                          # «контроля нет», а не печатать медиану +0.000,
                          # которая означает «не торговать вовсе»
MAX_DRAW_FACTOR = 20      # потолок бросков на один нужный геном: если из
                          # 20*k случайных геномов торгуют единицы, контроль
                          # не молчит, а сообщает, что он слабый


# ------------------------------------------------------------------ замеры

def run_with_events(candles, pre, g, filt, lev):
    """Прогон движка с журналом событий (нужен для ПОСДЕЛОЧНОГО хвоста).

    Плечо ставится на время прогона и возвращается обратно: e2.LEV —
    глобальная переменная модуля, и забытое значение молча испортит все
    последующие замеры.
    """
    evs = []
    old, e2.LEV = e2.LEV, lev
    try:
        r = e2.run5(candles, pre, g, entry_filter=filt, events=evs)
    finally:
        e2.LEV = old
    return r, evs


def trade_rs(evs):
    """R каждой сделки = pnl / фиксированная маржа (та же шкала, что в
    grid_ruin.py, поэтому пороги оттуда применимы напрямую)."""
    return [e["pnl"] / e2.MARGIN for e in evs if e["type"] == "close"]


def monthly_pct(r):
    """Месячные доходности в % депозита. Месяцы без сделок = 0, как в
    e2.stats: иначе «худший месяц» считался бы только по торговавшим месяцам
    и выглядел лучше, чем есть."""
    n_months = max(1, int(r["months"]))
    return [(r["monthly"].get(m, 0.0) / e2.START) * 100
            for m in range(n_months)]


def ruin_bootstrap(rs, cycles_per_year, seed=SEED, nboot=NBOOT, block=BLOCK):
    """Блочный бутстрап года торговли по фактическим R сделок.

    Модель капитала как в grid_ruin.py: маржа цикла = FRAC капитала, поэтому
    eq *= (1 + FRAC*R). Блочный, а не IID: IID разрушает автокорреляцию и
    занижает риск, а убивает счёт именно СЕРИЯ убытков.
    """
    n = len(rs)
    if n < MIN_RS_FOR_BOOT or cycles_per_year < 1:
        return None
    rng = random.Random(seed)
    ruin, dds = 0, []
    for _ in range(nboot):
        seq = []
        while len(seq) < cycles_per_year:
            L = rng.randint(block[0], block[1])
            s = rng.randrange(n)
            seq.extend(rs[(s + j) % n] for j in range(L))
        seq = seq[:cycles_per_year]
        eq, peak, dd = 1.0, 1.0, 0.0
        for x in seq:
            eq *= (1 + FRAC * x)
            peak = max(peak, eq)
            dd = max(dd, (peak - eq) / peak)
            if eq <= RUIN_LEVEL:
                ruin += 1
                break
        dds.append(dd)
    dds.sort()
    i = int(0.95 * (len(dds) - 1))
    return dict(ruin=ruin / nboot * 100, dd95=dds[i] * 100)


def measure(candles, pre, g, filt, lev, tag=""):
    """Полный замер конфига на ОДНОМ окне истории.

    Окно обязано быть экзаменационным (нетронутым отбором) — иначе все цифры
    ниже описывают подгонку, а не риск.
    """
    r, evs = run_with_events(candles, pre, g, filt, lev)
    st = e2.stats(r)
    rs = trade_rs(evs)
    mon = monthly_pct(r)
    months = max(1e-9, r["months"])
    liq = sum(1 for e in evs if e["type"] == "close" and e.get("liq"))
    # экспозиция: доля баров, проведённых в позиции. Позиция всегда одна,
    # поэтому суммы удержаний не перекрываются.
    expo = (sum(r["hold"]) / len(candles)) if candles else 0.0
    cpy = int(round(r["trades"] / months * 12)) if r["trades"] else 0
    boot = ruin_bootstrap(rs, cpy) if cpy >= 1 else None
    # max_dd_float добавляет движок (соседняя правка аудита). Если ключа нет —
    # честно отдаём None и печатаем предупреждение, а не подставляем просадку
    # по закрытым сделкам вместо плавающей: это разные величины.
    ddf = r.get("max_dd_float")
    return dict(
        tag=tag, lev=lev,
        score=e2.oos_score(r),
        trades=r["trades"], wins=r["wins"],
        ret_pct=(r["balance"] / e2.START - 1) * 100,
        dd_closed=r["max_dd"] * 100,
        dd_float=(ddf * 100 if ddf is not None else None),
        ruined=r["ruined"], liq=liq,
        months=r["months"], tpm=st["tpm"], med=st["med"], p25=st["p25"],
        exposure=expo, cycles_per_year=cpy,
        worst_month=(min(mon) if mon else 0.0),
        worst_trade_R=(min(rs) if rs else 0.0),
        loss_share=((r["trades"] - r["wins"]) / r["trades"] * 100
                    if r["trades"] else 0.0),
        ruin=(boot["ruin"] if boot else None),
        dd95=(boot["dd95"] if boot else None),
        rs=rs)


# ------------------------------------------------------------------ ворота

def degenerate_reasons(m):
    """Вырожденное решение: конфиг «побеждает» тем, что не торгует.

    Именно так волна v11 приняла SOL: cand_oos 0.00 против базы -3.24, то
    есть ноль оказался лучше минуса. Ноль — это не преимущество, это отказ
    от игры.
    """
    out = []
    if m["trades"] < MIN_TRADES:
        out.append(f"сделок на экзамене {m['trades']} < {MIN_TRADES} "
                   f"(вырожденный конфиг)")
    if m["tpm"] < MIN_TPM:
        out.append(f"сделок в месяц {m['tpm']:.2f} < {MIN_TPM} "
                   f"(конфиг почти не торгует)")
    if m["exposure"] < MIN_EXPOSURE:
        out.append(f"экспозиция {m['exposure']*100:.2f}% баров < "
                   f"{MIN_EXPOSURE*100:.0f}% (риск не принимался)")
    return out


def tail_reasons(m):
    """Ворота по хвосту — то, чего не видит oos_score."""
    out = []
    if m["ruined"]:
        out.append("СЛИВ депозита на экзамене")
    if m["worst_month"] < WORST_MONTH_MIN:
        out.append(f"худший месяц {m['worst_month']:+.1f}% < "
                   f"{WORST_MONTH_MIN:.0f}%")
    if m["worst_trade_R"] < WORST_TRADE_MIN_R:
        out.append(f"худшая сделка {m['worst_trade_R']:+.2f}R < "
                   f"{WORST_TRADE_MIN_R:.2f}R")
    if m["liq"] > MAX_LIQ:
        out.append(f"ликвидаций {m['liq']} > {MAX_LIQ}")
    if m["dd_float"] is not None and m["dd_float"] > DD_FLOAT_MAX:
        out.append(f"плавающая просадка {m['dd_float']:.1f}% > "
                   f"{DD_FLOAT_MAX:.0f}%")
    if m["ruin"] is not None and m["ruin"] > RUIN_MAX:
        out.append(f"риск слива за год {m['ruin']:.1f}% > {RUIN_MAX:.0f}%")
    if m["dd95"] is not None and m["dd95"] > DD95_MAX:
        out.append(f"просадка p95 {m['dd95']:.1f}% > {DD95_MAX:.0f}%")
    return out


def warnings_of(m):
    """Чего мы НЕ измерили. Печатается рядом с вердиктом всегда: молчание об
    неизмеренном риске — то же враньё, что и заниженная цифра."""
    out = []
    if m["dd_float"] is None:
        out.append("плавающая просадка НЕ ИЗМЕРЕНА: движок не отдаёт "
                   "max_dd_float, просадка считается только по закрытым "
                   "сделкам и потому занижена (grid_ruin.py, раздел 7)")
    if m["ruin"] is None:
        out.append(f"риск руина НЕ ИЗМЕРЕН: сделок {m['trades']} — на такой "
                   f"выборке блочный бутстрап ничего не меряет")
    if 0 < m["trades"] < 60:
        out.append(f"выборка мала ({m['trades']} сделок): «не доказано» и "
                   f"«доказано, что нет» — разные утверждения (REVIEW, "
                   f"урок 8)")
    return out


def verdict(m):
    """(принят?, причины отказа, предупреждения) по одному экзаменационному
    замеру. Сам балл экзамена сюда НЕ входит: сравнение кандидата с базой —
    дело вызывающей волны, здесь только ворота, которые балл не ловит."""
    reasons = degenerate_reasons(m) + tail_reasons(m)
    return (not reasons), reasons, warnings_of(m)


def describe(m):
    """Одна строка для лога волны."""
    ddf = "н/д" if m["dd_float"] is None else f"{m['dd_float']:.1f}%"
    ruin = "н/д" if m["ruin"] is None else f"{m['ruin']:.1f}%"
    dd95 = "н/д" if m["dd95"] is None else f"{m['dd95']:.1f}%"
    return (f"балл {m['score']:+.3f} | итог {m['ret_pct']:+.2f}% | "
            f"сделок {m['trades']} ({m['tpm']:.2f}/мес) | "
            f"экспозиция {m['exposure']*100:.1f}% | "
            f"худший месяц {m['worst_month']:+.1f}% | "
            f"худшая сделка {m['worst_trade_R']:+.2f}R | "
            f"ликв. {m['liq']} | DD закр. {m['dd_closed']:.1f}% | "
            f"DD плав. {ddf} | руин/год {ruin} | DDp95 {dd95}")


# ------------------------------- инструменты честной оценки (REVIEW.md)

def neighbour_median(g, genes, clamp, evaluate, k=NEIGH_K, scale=NEIGH_SCALE,
                     seed=SEED):
    """Медиана соседей: балл не самого генома, а его окрестности.

    Зачем. Отобранный геном стоит на вершине, найденной перебором, и его
    собственный балл почти всегда завышен. Если вершина узкая — соседи
    проваливаются, и это подгонка. В REVIEW.md этим приёмом цифра DOGE
    ужимается с +38.3% (экзамен) до +27.1% (медиана соседей).

    ДИСКРЕТНЫЕ ГЕНЫ. Сдвиг sigma = scale*(hi-lo) для целого гена с узким
    диапазоном после округления даёт РОВНО НОЛЬ: у levels (2..4), be_move
    (0..1), direction (0..2), regime_gate, pattern_gate, oi_gate, adx_gate,
    grid_mode sigma <= 0.16, и «соседи» отличались только непрерывными генами —
    то есть переключатели стратегии не проверялись вообще. Теперь целый ген,
    чей округлённый сдвиг обнулился, всё равно шагает на +-1 с вероятностью
    sigma (для узкого диапазона это и есть честная доля возмущений: у
    двухзначного гена sigma=0.08 -> 8% соседей его переключают).
    """
    rng = random.Random(seed)
    out, moved = [], 0
    for _ in range(k):
        nb, changed = dict(g), 0
        for key, (lo, hi, is_int) in genes.items():
            sigma = scale * (hi - lo)
            d = rng.gauss(0, sigma)
            if is_int:
                d = int(round(d))
                if d == 0 and hi > lo and rng.random() < min(1.0, sigma):
                    d = rng.choice((-1, 1))
                if d:
                    changed += 1
            nb[key] = nb[key] + d
        moved += changed
        out.append(evaluate(clamp(nb)))
    return dict(median=statistics.median(out), worst=min(out), best=max(out),
                k=k, vals=out, int_moves=moved)


def random_control(rand_g, evaluate, cand_score, k=RANDOM_K, seed=SEED,
                   trades_of=None, min_trades=MIN_CONTROL_TRADES):
    """Контроль случайными геномами: эталон — не ноль, а случайный вход.

    Если случайные геномы набирают тот же балл, значит мы измерили свойство
    окна истории, а не знание о рынке (REVIEW.md, урок 3; nullcheck.py
    измерил, что случайный геном проходит порог отбора в 1.4% случаев).

    ПОЧЕМУ ЗДЕСЬ ФИЛЬТР. Случайный геном сплошь и рядом не торгует вовсе: из
    30 бросков 11 давали 0 сделок, у 14 балл выходил ровно 0.000, медиана
    контроля — +0.000. Такой контроль отвечал на вопрос «лучше ли кандидат,
    чем НЕ ТОРГОВАТЬ», а спрашивать надо «лучше ли он СЛУЧАЙНОГО ВХОДА».
    Ноль в контроле вдобавок бьёт убыточного кандидата и делает вывод
    бессмысленным в обе стороны. Поэтому вырожденные геномы (меньше
    min_trades сделок на окне) отбраковываются и добираются новыми бросками;
    сколько выброшено — печатается, молчать об этом нельзя.

    trades_of(g) -> число сделок генома на том же окне. Если его не передали,
    фильтровать нечем: контроль считается по-старому и честно помечается
    предупреждением.
    """
    rng_state = random.getstate()
    random.seed(seed)                 # rand_g из e4.ga_tools берёт глобальный
    vals, rejected, draws, warns = [], 0, 0, []   # random — фиксируем ради
    try:                                          # повторимости
        if trades_of is None:
            warns.append("контроль НЕ ОЧИЩЕН от неторгующих геномов: "
                         "вызывающая волна не передала счётчик сделок, "
                         "поэтому медиана контроля может означать "
                         "«не торговать вовсе», а не «случайный вход»")
            vals = [evaluate(rand_g()) for _ in range(k)]
        else:
            while len(vals) < k and draws < k * MAX_DRAW_FACTOR:
                g = rand_g()
                draws += 1
                if trades_of(g) < min_trades:
                    rejected += 1
                    continue
                vals.append(evaluate(g))
            if len(vals) < k:
                warns.append(
                    f"торгующих случайных геномов набралось только "
                    f"{len(vals)} из {k} за {draws} бросков "
                    f"({rejected} не торговали): контроль слабый, "
                    f"вывод по нему делать нельзя")
    finally:
        random.setstate(rng_state)
    if not vals:                      # совсем не из чего считать
        return dict(k=0, share=0.0, median=0.0, best=0.0, beat=0,
                    rejected=rejected, draws=draws,
                    warns=warns + ["ни одного торгующего случайного генома — "
                                   "контроля НЕТ"])
    beat = sum(1 for v in vals if v >= cand_score)
    return dict(k=len(vals), share=beat / len(vals) * 100,
                median=statistics.median(vals), best=max(vals), beat=beat,
                rejected=rejected, draws=draws, warns=warns)


def bonferroni(tried, alpha=0.05):
    """Поправка на множественность: сколько проверок сделано — во столько раз
    строже должен быть порог. Счётчик попыток обязан быть НАСТОЯЩИМ: в волне
    отбора это все оценённые геномы, а не число монет (REVIEW.md, урок 4)."""
    tried = max(1, int(tried))
    a = alpha / tried
    try:
        from statistics import NormalDist
        t = NormalDist().inv_cdf(1 - a / 2)
    except Exception:                 # pragma: no cover
        t = float("nan")
    return dict(tried=tried, alpha=a, t_needed=t,
                expected_false=alpha * tried)
