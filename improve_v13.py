# -*- coding: utf-8 -*-
"""v13: попытка выжать больше из BTC и LTC. Честный протокол, правила приёмки
объявлены ДО прогона.

ЗАЧЕМ ЭТОТ ФАЙЛ. Владелец попросил попробовать улучшить два боевых конфига —
BTCUSDT и LTCUSDT. Это ПОПЫТКА, а не обещание: волны v1-v12 уже перебрали на
этих же данных десятки тысяч геномов, и вероятный исход короткого прогона —
«ничего лучше не найдено». Такой исход здесь считается нормальным результатом
и записывается как результат, а не как неудача, которую надо чинить снижением
порогов.

ЧЕГО ЭТОТ ФАЙЛ НЕ ДЕЛАЕТ:
  * не трогает config.py и существующие конфиги. Даже если кандидат пройдёт
    все ворота, он попадёт только в артефакт improve_v13_final.json с готовым
    dict для config — решение о замене принимает владелец. Действующие боты
    сохраняются в любом случае, это его прямое указание;
  * не переписывает артефакты прошлых волн (пишем через e4.save_artifact,
    который существующий файл не затирает);
  * не заводит собственного движка и собственной нарезки окон. Всё считается
    ТЕМ ЖЕ кодом, что и v12 после починки утечки: e12.run_symbol (выбор по
    валидации, экзамен строго после обучения, плечо выбирается по обучающей
    части и на нём же считаются фитнес/валидация/экзамен/ворота) и honest_eval
    (хвост, вырожденность, бутстрап риска руина, соседи, случайный контроль,
    Бонферрони). Своя здесь только ДЛИНА перебора и ПРАВИЛА ПРИЁМКИ — и те
    берутся у v12 по ссылке, а не копией (см. ниже).

ПОЧЕМУ ПОРОГИ ВЗЯТЫ ССЫЛКАМИ НА e12/honest_eval, А НЕ ЧИСЛАМИ. Скопированное
число можно молча поправить под результат — это ровно тот самообман, ради
которого написан honest_eval. Ссылка на чужую константу поправить нельзя:
чтобы смягчить порог, придётся править файл, который мне трогать запрещено, и
это будет видно в diff. Единственные СВОИ пороги — два дополнительных правила
честности (R8, R9), и они делают приёмку СТРОЖЕ, а не мягче.

БЮДЖЕТ И РАЗМЕР ПЕРЕБОРА. Замер скорости движка перед прогоном: одна оценка
генома на самом длинном обучающем куске (86 249 баров) — около 95 мс на
случайном геноме и до ~300 мс на торгующем. Отсюда POP=64, GENS=32, ELITE=8:
до 64 + 32*(64-8) = 1856 оценок на фолд, 3 фолда на монету, 2 монеты — порядка
11 тысяч геномов и ~40 минут счёта. Для сравнения, v12 гоняла POP=48, GENS=24
(до 1056 оценок на фолд), то есть перебор здесь шире примерно в 1.8 раза.

СЕМЯ СЛУЧАЙНОСТИ. random.seed(13) — намеренно ДРУГОЕ, чем у прошлых волн
(evolution.py seed 42, evolution2 43, evolution4 45). Иначе GA пошёл бы по той
же траектории и переискал уже перебранное. Это единственный способ дать
короткому прогону хоть какой-то шанс найти новое: не больше поколений на том
же пути, а другой путь.

ЧТО ИМЕННО НОВОГО МЫ ПРОБУЕМ ДЛЯ КАЖДОЙ МОНЕТЫ:
  * BTCUSDT — его боевой конфиг родом из волн v6/v7 (медвежий специалист с
    regime_gate=1). В пространстве генов v12 (ADX-гейт + подвижная сетка +
    трейлинг, 46 генов) BTC не отбирался НИ РАЗУ: v12 гоняла DOGE/LTC/SOL.
    Это единственное честное основание надеяться, что здесь есть что искать;
  * LTCUSDT — его конфиг как раз из v12, то есть то же пространство и тот же
    движок. Здесь мы повторяем поиск с другим семенем и вдвое более широким
    перебором. Шанс найти лучше — маленький, и он должен быть маленьким:
    если бы нашлось легко, это означало бы, что балл шумит, а не что конфиг
    плох.

ИЗВЕСТНОЕ ПОСЛЕДСТВИЕ ЭТОГО ФАЙЛА ДЛЯ ТЕСТОВ — сказать вслух обязательно.
test_ext_data.py считает по исходникам продакшен-вызовы fetch_daily_pct5 и
требует, чтобы их было РОВНО 16: столько записано в шапке ext_data.py и в
докстроке самой функции. Этот файл зовёт её в main() и становится
СЕМНАДЦАТЫМ вызывающим — проверка падает. Сторож сработал ВЕРНО: он ровно для
того и написан, чтобы число в шапке не протухло молча.

Обойти его через getattr(xd, "fetch_daily_pct5")() было бы легко и было бы
враньём: сторож ловит появление нового вызывающего, а вызывающий появился.
Правка нужна в двух ЧУЖИХ файлах — «16» на «17» в ext_data.py (шапка модуля и
докстрока функции) и в test_ext_data.py, — и делает её владелец. Ничьё
поведение при этом не менялось, и содержательная половина той же проверки —
«ни один продакшен-вызов не передаёт max_age_s» — по-прежнему выполняется:
здесь функция зовётся без аргумента, как и у всех шестнадцати остальных.
"""

import os
import random
import sys
import time
import tempfile

# Рабочий каталог ставим ДО импорта модулей проекта, а не в main(). Причина
# конкретная: evolution6 читает evolution5_winners.json прямо на импорте, а
# evolution.fetch и ext_data ищут кэши истории по ОТНОСИТЕЛЬНЫМ именам. Запуск
# по абсолютному пути из чужого каталога падал бы на импорте, а в лучшем случае
# полез бы в сеть за 1150 днями свечей заново.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import config                      # noqa: E402
import evolution as ev             # noqa: E402
import evolution2 as e2            # noqa: E402
import evolution4 as e4            # noqa: E402
import evolution12 as e12          # noqa: E402
import ext_data as xd              # noqa: E402
import honest_eval as he           # noqa: E402

SYMS = ["BTCUSDT", "LTCUSDT"]

# Текстовая копия лога — рядом с временными файлами, а не в репозитории: в репо
# из этой волны кладётся ровно один артефакт improve_v13_final.json, и лог
# лежит ВНУТРИ него. Копия нужна только чтобы читать ход прогона, пока он идёт.
LOG_COPY = os.environ.get("V13_LOG",
                          os.path.join(tempfile.gettempdir(),
                                       "improve_v13_log.txt"))

# --- бюджет перебора (обоснование — в модульной докстроке) -------------------
POP, GENS, ELITE = 64, 32, 8
SEED = 13

# --- сколько геномов оценено: вход поправки на множественность ---------------
# Считаем РАЗНЫЕ геномы, а не вызовы движка: один и тот же геном, прогнанный на
# трёх окнах, — это одна проверка гипотезы, а не три. Счётчик обязан быть
# настоящим (REVIEW.md, урок 4): чем шире перебор, тем выше планка для
# «находки», и занижение счётчика — это тихое смягчение приёмки.
GENOMES = set()


# =============================================================================
# ПРАВИЛА ПРИЁМКИ. Объявлены ЗДЕСЬ, в коде, ДО прогона; печатаются в лог первой
# строкой ещё до загрузки данных — чтобы по логу было видно, что они не
# подгонялись под результат. Смягчению не подлежат: кандидат либо проходит все,
# либо не проходит.
#
# R1-R7 — правила волны v12, взятые ССЫЛКАМИ на её константы.
# R8-R9 — добавлены здесь и делают приёмку СТРОЖЕ.
# Плюс ворота honest_eval.verdict (хвост + вырожденность + бутстрап руина) —
# они не нумеруются, потому что это не «ещё одно правило», а обязательный
# минимум, ниже которого разговор о доходности не имеет смысла.
# =============================================================================
RULES = [
    ("R1", f"итог на полной истории на выбранном плече > 0"),
    ("R2", f"просадка на полной истории <= {e12.DD_CAP:.0f}% и без слива"),
    ("R3", f"балл экзамена > 0 и > балла базы + {e12.MIN_EDGE} "
           f"(экзамен — окно СТРОГО ПОСЛЕ обучения кандидата)"),
    ("R4", f"худшее из ЧЕСТНЫХ окон кандидата (валидационное и все после "
           f"него) >= {e12.FOLD_FLOOR}"),
    ("R5", f"доля убыточных сделок >= {e12.MIN_LOSS_SHARE}% "
           f"(ноль убытков = ошибка замера, а не грааль)"),
    ("R6", f"сделок на полной истории >= {e12.MIN_TRADES}"),
    ("R7", "плечо ПОДТВЕРЖДЕНО лестницей по обучающей части "
           "(есть ступень с плавающей просадкой в пороге и итогом > 0)"),
    ("R8", f"медиана СОСЕДЕЙ кандидата на экзамене > балла базы + "
           f"{e12.MIN_EDGE} (узкий пик = подгонка: если преимущество исчезает "
           f"при сдвиге генов, оно принадлежит перебору, а не стратегии)"),
    ("R9", f"контроль случайными геномами собран (>= {he.MIN_CONTROL_TRADES} "
           f"торгующих) И кандидата повторили меньше {10.0}% из них "
           f"(не собрался — правило НЕ выполнено: недоказанное превосходство "
           f"над случайным входом засчитывать нельзя)"),
    ("ВОРОТА", "honest_eval.verdict: худший месяц, худшая сделка, ликвидации, "
               "плавающая просадка, риск руина и просадка p95 по блочному "
               "бутстрапу, минимум сделок/сделок в месяц/экспозиции"),
]
R9_MAX_BEAT_SHARE = 10.0     # %: свой порог, объявлен до прогона


def announce():
    """Печать правил приёмки ДО любых вычислений."""
    log("=" * 78)
    log("v13: попытка улучшить BTC и LTC. ПРАВИЛА ПРИЁМКИ, объявленные ДО "
        "прогона:")
    for code, text in RULES:
        log(f"  {code}: {text}")
    log("Кандидат принимается, только если выполнены ВСЕ правила. "
        "Ни одно из них после прогона не смягчается.")
    log(f"Перебор: POP={POP}, GENS={GENS}, ELITE={ELITE}, seed={SEED}, "
        f"монеты {SYMS}, история {e12.DAYS} дней 15m.")
    log("Действующие боты сохраняются в любом случае: этот прогон ничего не "
        "меняет в config.py, он только пишет отчёт.")
    log("=" * 78)


# ------------------------------------------------------------------ лог

_LOG = []


class Tee:
    """Копия stdout в память. Нужна не для красоты: ход прогона печатает не
    этот файл, а e12.run_symbol и honest_eval обычным print. Без перехвата в
    артефакт попали бы только мои итоговые строки, а поколения GA, лестницы
    плеча и предупреждения ворот — то, по чему только и можно проверить, как
    шёл отбор, — остались бы в консоли и пропали.

    Ошибка кодировки консоли (Windows, cp866) гасится: она не повод потерять
    прогон, а текст в памяти и в артефакте всё равно полный.
    """

    def __init__(self, real):
        self.real = real

    def write(self, s):
        if s:
            _LOG.append(s)
        try:
            self.real.write(s)
        except UnicodeEncodeError:
            self.real.write(s.encode("ascii", "replace").decode())

    def flush(self):
        try:
            self.real.flush()
        except Exception:
            pass


def log(*a):
    print(" ".join(str(x) for x in a), flush=True)


def dump_log():
    """Сброс копии лога на диск по ходу прогона: прогон идёт десятки минут, и
    если он упадёт на второй монете, отчёт по первой должен уцелеть."""
    try:
        with open(LOG_COPY, "w", encoding="utf-8") as fh:
            fh.write("".join(_LOG))
    except Exception:
        pass


# ------------------------------------------------------- счётчик геномов

def counting_run5(orig):
    """Обёртка движка, считающая РАЗНЫЕ геномы.

    Почему обёртка, а не счётчик внутри GA: геномы гоняет не только генетика, но
    и лестница плеча, и окна валидации/экзамена. Всё это — часть ПОИСКА
    победителя, и Бонферрони обязан видеть весь поиск целиком, иначе планка для
    «находки» окажется заниженной ровно на ту часть перебора, о которой мы
    промолчали.

    Считаем РАЗНЫЕ геномы, а не вызовы: один геном, прогнанный на трёх окнах, —
    это одна проверенная гипотеза, а не три.

    Счётчик снимается (замораживается) сразу после отбора — см. main. Прогоны
    соседей и случайного контроля идут ПОСЛЕ и в него не попадают: это проверка
    уже выбранного кандидата, а не поиск нового.
    """
    def run5(candles, pre, g, **kw):
        try:
            GENOMES.add(tuple(sorted(
                (k, round(v, 6) if isinstance(v, float) else v)
                for k, v in g.items())))
        except Exception:           # геном непривычной формы — не роняем прогон
            pass
        return orig(candles, pre, g, **kw)
    return run5


# ------------------------------------------------------------- приёмка

def judge(sym, rec, pick, mm, honesty, lev_ok):
    """Проверка правил R1-R9 и ворот. Возвращает (принят, [строки отчёта]).

    Каждое правило отчитывается отдельной строкой с фактическим числом: читателю
    должно быть видно не только «не прошёл», но и НАСКОЛЬКО и ПО КАКОМУ пункту.
    """
    base_exam = rec["base_oos"]
    cand_folds = rec.get("cand_folds") or [0.0]
    gates_ok, gate_reasons, _ = he.verdict(mm)
    checks = []

    checks.append(("R1", pick["ret"] > 0, f"итог {pick['ret']:+.2f}%"))
    checks.append(("R2", pick["dd"] <= e12.DD_CAP and not pick["ruined"],
                   f"просадка {pick['dd']:.1f}%"
                   + (" СЛИВ" if pick["ruined"] else "")))
    checks.append(("R3",
                   rec["cand_oos"] > 0
                   and rec["cand_oos"] > base_exam + e12.MIN_EDGE,
                   f"экзамен {rec['cand_oos']:+.3f} против базы "
                   f"{base_exam:+.3f} (отрыв "
                   f"{rec['cand_oos'] - base_exam:+.3f})"))
    checks.append(("R4", min(cand_folds) >= e12.FOLD_FLOOR,
                   f"честные окна {['%+.2f' % s for s in cand_folds]}, "
                   f"худшее {min(cand_folds):+.2f}"))
    checks.append(("R5", pick["loss_share"] >= e12.MIN_LOSS_SHARE,
                   f"убыточных сделок {pick['loss_share']:.2f}%"))
    checks.append(("R6", pick["trades"] >= e12.MIN_TRADES,
                   f"сделок {pick['trades']}"))
    checks.append(("R7", bool(lev_ok), f"плечо x{pick['lev']}"
                   + ("" if lev_ok else " НЕ подтверждено лестницей")))
    checks.append(("R8", honesty["neighbour_median"] > base_exam + e12.MIN_EDGE,
                   f"медиана соседей {honesty['neighbour_median']:+.3f} "
                   f"(худший {honesty['neighbour_worst']:+.3f}) против базы "
                   f"{base_exam:+.3f}"))
    ctrl_ok = (honesty["random_k"] >= he.MIN_CONTROL_TRADES
               and honesty["random_beat_share"] < R9_MAX_BEAT_SHARE)
    checks.append(("R9", ctrl_ok,
                   f"торгующих случайных геномов {honesty['random_k']}, "
                   f"медиана {honesty['random_median']:+.3f}, повторили или "
                   f"побили кандидата {honesty['random_beat_share']:.0f}%"))
    checks.append(("ВОРОТА", gates_ok,
                   "; ".join(gate_reasons) if gate_reasons else "все пройдены"))

    lines = [f"  {code}: {'ДА ' if ok else 'НЕТ'} — {txt}"
             for code, ok, txt in checks]
    return all(ok for _, ok, _ in checks), lines, checks


def base_reference(sym, rec, candles, pre, aux):
    """СПРАВКА: как выглядит ДЕЙСТВУЮЩИЙ конфиг на тех же самых правилах.

    Зачем она нужна. Вывод «улучшения не найдено» читается как «нынешние боты
    хороши», а это разные утверждения. Правила R1/R2/R5/R6 и ворота honest_eval
    можно применить и к базе — она не кандидат, ей не с чем сравниваться
    (R3/R4/R8/R9 про отрыв от базы и про подгонку перебором, для самой базы они
    бессмысленны), но её итог, просадку, число сделок и хвост измерить можно.
    Если база тех же правил не проходит, честный отчёт обязан это сказать:
    «лучше не нашли» и «нынешнее хорошо» — не одно и то же.

    Плечо берётся ТО, КОТОРОЕ СТОИТ В config.py, а не выбранное лестницей: бот
    в бою торгует именно им, и справка должна описывать живого бота.
    """
    g = rec["base_genome"]
    lev = int(config.SYMBOL_PARAMS[sym]["final"].get("lev", 5))
    pick = e12.full_run(g, candles, pre, aux, lev)
    pick["lev"] = lev
    folds = e4.fold_bounds_3y(len(candles))
    eb, ee = folds[e4.exam_window_index(len(folds))][1:]
    ex = candles[eb:ee]
    mm = he.measure(ex, e2.prep(ex), g,
                    e12.make_filter12(g, e12.slice_aux(aux, eb, ee)), lev,
                    tag=f"{sym}/база")
    gates_ok, gate_reasons, gate_warns = he.verdict(mm)
    checks = [
        ("R1", pick["ret"] > 0, f"итог {pick['ret']:+.2f}%"),
        ("R2", pick["dd"] <= e12.DD_CAP and not pick["ruined"],
         f"просадка {pick['dd']:.1f}%" + (" СЛИВ" if pick["ruined"] else "")),
        ("R5", pick["loss_share"] >= e12.MIN_LOSS_SHARE,
         f"убыточных сделок {pick['loss_share']:.2f}%"),
        ("R6", pick["trades"] >= e12.MIN_TRADES, f"сделок {pick['trades']}"),
        ("ВОРОТА", gates_ok,
         "; ".join(gate_reasons) if gate_reasons else "все пройдены"),
    ]
    return dict(lev=lev, pick_full_history=pick,
                exam_measure={k: v for k, v in mm.items() if k != "rs"},
                exam_describe=he.describe(mm),
                checks=[dict(rule=c, ok=bool(o), fact=t) for c, o, t in checks],
                gate_warnings=gate_warns,
                note=("справка, а не отбор: действующий конфиг не менялся и "
                      "не переотбирался"))


# ------------------------------------------------------------------ прогон

def main():
    t_start = time.time()
    orig_stdout, sys.stdout = sys.stdout, Tee(sys.stdout)
    announce()

    random.seed(SEED)
    # длину перебора задаём мы, всё остальное в v12 остаётся как есть
    e12.POP, e12.GENS, e12.ELITE = POP, GENS, ELITE

    orig_run5 = e2.run5
    e2.run5 = counting_run5(orig_run5)
    try:
        pct5 = xd.fetch_daily_pct5()
        aux_builder = e12.make_aux_builder(pct5, 96)
        rand_g, clamp, _, _ = e4.ga_tools(e12.GENES12)

        out = {}
        for sym in SYMS:
            t_sym = time.time()
            GENOMES.clear()          # перебор считается СВОЙ на каждую монету
            rec = e12.run_symbol(sym, aux_builder)
            # Счётчик перебора замораживается ЗДЕСЬ, сразу после отбора.
            # Дальше движок ещё много раз позовут соседи и случайный контроль,
            # но это не поиск победителя, а его проверка: класть их в
            # Бонферрони — значит считать проверками гипотезы, которые мы
            # никогда не собирались принимать.
            tried = len(GENOMES)

            candles = ev.fetch(sym, "15", e12.DAYS)
            pre = e2.prep(candles)
            aux = aux_builder(sym, candles)
            g = rec["genome"]
            lev = rec["lev"]

            # полная история — только ОТЧЁТ (плечо уже выбрано по обучающей
            # части внутри run_symbol), но правила R1/R2/R5/R6 v12 считает
            # именно по ней, поэтому цифра нужна
            pick = e12.full_run(g, candles, pre, aux, lev)
            pick["lev"] = lev

            # экзаменационное окно: то же, на котором run_symbol считал балл
            folds = e4.fold_bounds_3y(len(candles))
            eb, ee = folds[e4.exam_window_index(len(folds))][1:]
            ex = candles[eb:ee]
            pre_ex = e2.prep(ex)
            aux_ex = e12.slice_aux(aux, eb, ee)
            mm = he.measure(ex, pre_ex, g, e12.make_filter12(g, aux_ex), lev,
                            tag=f"{sym}/v13")

            # --- инструменты честной оценки на ТОМ ЖЕ окне и ТОМ ЖЕ плече ---
            def evaluate(gg, _s=ex, _p=pre_ex, _a=aux_ex, _l=lev):
                old, e2.LEV = e2.LEV, _l
                try:
                    r = e2.run5(_s, _p, gg,
                                entry_filter=e12.make_filter12(gg, _a))
                finally:
                    e2.LEV = old
                return e12.oos_score12(r)

            def trades_of(gg, _s=ex, _p=pre_ex, _a=aux_ex, _l=lev):
                old, e2.LEV = e2.LEV, _l
                try:
                    r = e2.run5(_s, _p, gg,
                                entry_filter=e12.make_filter12(gg, _a))
                finally:
                    e2.LEV = old
                return r["trades"]

            nb = he.neighbour_median(g, e12.GENES12, clamp, evaluate)
            rc = he.random_control(rand_g, evaluate, rec["cand_oos"],
                                   trades_of=trades_of)
            bf = he.bonferroni(tried)
            honesty = dict(neighbour_median=nb["median"],
                           neighbour_worst=nb["worst"],
                           neighbour_best=nb["best"], neighbour_k=nb["k"],
                           int_moves=nb.get("int_moves", 0),
                           int_changed=nb.get("int_changed", 0),
                           random_k=rc["k"], random_median=rc["median"],
                           random_best=rc["best"],
                           random_beat_share=rc["share"],
                           random_rejected=rc.get("rejected", 0),
                           random_warns=rc.get("warns", []),
                           genomes_tried=bf["tried"],
                           expected_false=bf["expected_false"],
                           alpha_needed=bf["alpha"])

            accept, lines, checks = judge(sym, rec, pick, mm, honesty,
                                          rec["lev_confirmed"])
            ref = base_reference(sym, rec, candles, pre, aux)

            log(f"\n--- {sym}: приёмка v13 ---")
            log(f"  экзамен: {he.describe(mm)}")
            for w in he.verdict(mm)[2]:
                log(f"    не измерено: {w}")
            log(f"  соседи (k={nb['k']}): медиана {nb['median']:+.3f}, "
                f"худший {nb['worst']:+.3f}, лучший {nb['best']:+.3f}; "
                f"дискретных генов сдвинуто {nb.get('int_changed', 0)} "
                f"(попыток {nb.get('int_moves', 0)})")
            log(f"  случайные геномы: торгующих {rc['k']}, медиана "
                f"{rc['median']:+.3f}, лучший {rc['best']:+.3f}, повторили "
                f"или побили кандидата {rc['beat']} ({rc['share']:.0f}%), "
                f"отброшено неторгующих {rc.get('rejected', 0)}")
            for w in rc.get("warns", []):
                log(f"    контроль: {w}")
            log(f"  перебор: РАЗНЫХ геномов оценено {bf['tried']}; при "
                f"alpha=0.05 случайных «находок» ожидается "
                f"{bf['expected_false']:.0f}, честный порог на одну проверку "
                f"alpha={bf['alpha']:.2e}")
            for ln in lines:
                log(ln)
            log(f"  ИТОГ {sym}: {'ПРИНЯТ' if accept else 'НЕ ПРИНЯТ'} "
                f"(счёт монеты {time.time()-t_sym:.0f}с)")
            log(f"  СПРАВКА — действующий конфиг из config.py (x{ref['lev']}) "
                f"на тех же правилах, где они к нему применимы:")
            log(f"    экзамен: {ref['exam_describe']}")
            for c in ref["checks"]:
                log(f"    {c['rule']}: {'ДА ' if c['ok'] else 'НЕТ'} — "
                    f"{c['fact']}")
            log("    (это справка, а не переотбор: боевой конфиг не менялся)")

            out[sym] = dict(
                accept=accept,
                checks=[dict(rule=c, ok=bool(o), fact=t) for c, o, t in checks],
                rules_declared=[dict(rule=c, text=t) for c, t in RULES],
                protocol=rec.get("protocol"), metric=rec.get("metric"),
                pop=POP, gens=GENS, elite=ELITE, seed=SEED,
                genomes_tried=tried,
                base_reference=ref,
                base_oos=rec["base_oos"], cand_oos=rec["cand_oos"],
                cand_folds=rec.get("cand_folds"),
                base_folds=rec.get("base_folds"),
                val_window=rec.get("val_window"),
                val_score=rec.get("val_score"), val_edge=rec.get("val_edge"),
                exam_window=rec.get("exam_window"),
                train_fold=rec.get("train_fold"),
                skipped_candidates=rec.get("skipped_candidates"),
                degenerate_candidates=rec.get("degenerate_candidates"),
                all_candidates_degenerate=rec.get("all_candidates_degenerate"),
                lev=lev, lev_sel=rec.get("lev_sel"),
                lev_val=rec.get("lev_val"),
                lev_settled=rec.get("lev_settled"),
                lev_confirmed=rec.get("lev_confirmed"),
                lev_warnings=rec.get("lev_warnings"),
                ladder_train=rec.get("ladder_train"),
                pick_full_history=pick,
                exam_measure={k: v for k, v in mm.items() if k != "rs"},
                honesty=honesty,
                genome=g, base_genome=rec.get("base_genome"),
                # cfg заполняется ТОЛЬКО у принятого кандидата и всё равно
                # никуда не подставляется автоматически: замена боевого бота —
                # решение владельца, а не скрипта
                cfg=(e12.genome_to_cfg(g, lev) if accept else None),
                seconds=round(time.time() - t_sym, 1))
            dump_log()

        out["_meta"] = dict(
            wave="improve_v13", symbols=SYMS, seed=SEED,
            pop=POP, gens=GENS, elite=ELITE, days=e12.DAYS, interval="15",
            rules=[dict(rule=c, text=t) for c, t in RULES],
            r9_max_beat_share=R9_MAX_BEAT_SHARE,
            thresholds_from=dict(
                MIN_EDGE=e12.MIN_EDGE, FOLD_FLOOR=e12.FOLD_FLOOR,
                MIN_LOSS_SHARE=e12.MIN_LOSS_SHARE, MIN_TRADES=e12.MIN_TRADES,
                DD_CAP=e12.DD_CAP,
                he_WORST_MONTH_MIN=he.WORST_MONTH_MIN,
                he_WORST_TRADE_MIN_R=he.WORST_TRADE_MIN_R,
                he_DD_FLOAT_MAX=he.DD_FLOAT_MAX, he_RUIN_MAX=he.RUIN_MAX,
                he_DD95_MAX=he.DD95_MAX, he_MIN_TRADES=he.MIN_TRADES),
            seconds=round(time.time() - t_start, 1),
            accepted=[s for s in SYMS if out[s]["accept"]],
            note=("Правила объявлены в коде до прогона и не менялись. "
                  "Действующие конфиги остаются в силе независимо от исхода."))

        log(f"\n=== ИТОГ v13 за {time.time()-t_start:.0f}с ===")
        for sym in SYMS:
            r = out[sym]
            failed = [c["rule"] for c in r["checks"] if not c["ok"]]
            log(f"{sym:<9} {'ПРИНЯТ' if r['accept'] else 'не принят':<10} "
                f"x{r['lev']:<3} экзамен {r['cand_oos']:+.3f} против базы "
                f"{r['base_oos']:+.3f} | геномов {r['genomes_tried']} | "
                f"провалено: {', '.join(failed) if failed else '—'}")
        if not out["_meta"]["accepted"]:
            log("УЛУЧШЕНИЯ НЕ НАЙДЕНО. Это нормальный результат: боевые "
                "конфиги остаются прежними.")
        # лог кладём ВНУТРЬ артефакта: отдельный текстовый файл легко потерять
        # или подправить, а тут он лежит рядом с числами, которые описывает
        out["_meta"]["log"] = "".join(_LOG)
        name = e4.save_artifact("improve_v13_final.json", out, log=log)
        log(f"артефакт: {name}")
    finally:
        e2.run5 = orig_run5
        sys.stdout = orig_stdout
        try:
            with open(LOG_COPY, "w", encoding="utf-8") as fh:
                fh.write("".join(_LOG))
        except Exception as exc:     # лог не должен ронять прогон
            print(f"копия лога не записана: {exc}")


if __name__ == "__main__":
    sys.exit(main())
