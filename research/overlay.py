# -*- coding: utf-8 -*-
"""Накладки: фильтры поверх уже готового сигнала.

Здесь нет ни одной стратегии, и в strat.REG ничего не регистрируется. Это
инструмент для другого вопроса.

ЗАЧЕМ ОНИ НУЖНЫ. Когда фильтр вшит внутрь стратегии, отделить его вклад от
вклада самого сигнала уже нельзя: улучшилось — и непонятно, сигнал стал умнее
или просто отсеклись плохие часы. Наладка живёт снаружи и берёт готовый
Signals: прогоняем стратегию с накладкой и без, разница и есть цена фильтра.
Тот же фильтр можно навесить на пять разных семейств и увидеть, работает ли он
вообще или помогал одной-единственной стратегии, под которую его и подбирали.

ЧТО НАКЛАДКА ДЕЛАЕТ И ЧЕГО НЕ ДЕЛАЕТ

  * Гасит вход: entry[i] становится нулём там, где условие не выполнено.
  * Может уменьшить размер (size_k) — этим занимается btc_vol_size.
  * НИКОГДА не трогает exit, stop, tp и trail. Фильтр, который умеет запирать
    в позиции, — уже не фильтр, а вторая стратегия, и мерить его вклад
    отдельно бессмысленно. Запрет входа не мешает выйти.
  * Возвращает НОВЫЙ объект Signals. Исходный остаётся нетронутым, накладки
    можно вешать одна на другую и сравнивать порядок применения.

ПРАВИЛО «НЕТ ДАННЫХ — НЕТ ФИЛЬТРА». Везде, где значение ещё не известно
(прогрев индикатора, открытый интерес до октября 2024, фандинг до первой
публикации), накладка обязана пропускать вход. Соблазн трактовать неизвестность
как запрет очень велик и очень опасен: фильтр по ОИ, запрещающий всё до октября
2024, просто вырежет первую половину выборки. На графике это выглядит как
улучшение стратегии, а на деле это выбор куска истории задним числом.

ГДЕ ЗДЕСЬ ПРЯЧЕТСЯ БУДУЩЕЕ. Накладки — самое опасное место всего каталога,
потому что они читают ЧУЖИЕ ряды: старший таймфрейм, соседнюю монету, фандинг,
открытый интерес. Каждый из них помечен своим временем, и каждый легко взять на
шаг раньше, чем он стал известен.
  * Старший таймфрейм — только через rdata.htf_aligned. Значение 4ч-бара с
    меткой 12:00 известно в 16:00, не раньше. Ошибка на один бар дарит четыре
    часа бесплатного знания и рисует любую кривую прибыльной.
  * BTC на том же таймфрейме — по моменту закрытия бара, а не по номеру строки.
    Метки у наших файлов совпадают, но опираться на совпадение нельзя: стоит
    одному ряду потерять свечу, и весь ряд поедет на бар вперёд.
  * Фандинг и ОИ — только через rdata.as_of, то есть последнее ОПУБЛИКОВАННОЕ
    значение. Ставку, помеченную 08:00, в 08:00 ещё не знают.

Механическая проверка engine.assert_causal портит цены и этим ловит утечку
внутри сигнала, но чужие файлы она не трогает — значит, утечку через 4ч-бары
или ОИ она не увидит. Поэтому в самопроверке есть второй, свой опыт:
внешние ряды портятся после точки среза прямо в кэше rdata, и проверяется, что
сигнал ДО среза не дрогнул.
"""
import itertools

import numpy as np

import ind
import rdata
from engine import Signals

HOUR_MS = 3_600_000
DAY_MS = 86_400_000

# Часовые окна UTC для накладки session: (с какого часа, до какого, не включая).
SESSIONS = {
    "all": (0, 24),          # контрольная точка: окна нет
    "asia": (0, 8),          # азиатская сессия
    "europe": (7, 15),       # европейское утро и день
    "us": (13, 21),          # американская сессия
    "us_pm": (15, 23),       # вторая половина американского дня
    "overlap": (13, 17),     # час пик: Европа и США торгуют одновременно
}


# --- служебное --------------------------------------------------------------

def _clone(sig):
    """Копия сигнала. Накладка обязана вернуть новый объект, а не править чужой.

    Иначе сравнение «с фильтром и без» невозможно: исходный Signals уже испорчен
    к моменту, когда до него дойдёт очередь.
    """
    n = len(sig.entry)
    out = Signals(n)
    out.entry = np.array(sig.entry, dtype=np.int8, copy=True)
    out.exit = np.array(sig.exit, dtype=bool, copy=True)
    out.stop = np.array(sig.stop, dtype=np.float64, copy=True)
    out.tp = np.array(sig.tp, dtype=np.float64, copy=True)
    out.trail = np.array(sig.trail, dtype=np.float64, copy=True)
    out.size_k = np.array(sig.size_k, dtype=np.float64, copy=True)
    out.meta = dict(sig.meta)
    out.meta["overlays"] = list(sig.meta.get("overlays", []))
    return out


def _record(out, name, p, before, known):
    """Оставить в meta след: что навесили, сколько знали, сколько осталось."""
    after = int((out.entry != 0).sum())
    out.meta["overlays"].append({
        "name": name, "p": dict(p),
        "entries_before": int(before), "entries_after": after,
        "known": float(np.mean(known)) if len(known) else 0.0,
    })
    return out


def _gate(out, allow, name, p, known):
    """Погасить входы там, где фильтр не разрешает. Всё остальное не трогаем."""
    before = int((out.entry != 0).sum())
    out.entry = np.where(allow, out.entry, 0).astype(np.int8)
    return _record(out, name, p, before, known)


def _sign_band(rel, tol):
    """Знак отклонения с мёртвой зоной ±tol. NaN остаётся NaN.

    Мёртвая зона нужна не для красоты: около самой средней цена пересекает её
    десятки раз за сутки, и фильтр без зоны превращается в генератор шума —
    разрешает и запрещает вход на соседних барах.
    """
    ok = np.isfinite(rel)
    out = np.full(len(rel), np.nan)
    r = np.where(ok, rel, 0.0)
    out[ok] = np.where(r[ok] > tol, 1.0, np.where(r[ok] < -tol, -1.0, 0.0))
    return out


def _vol_rank(c, vol_n, rank_n):
    """Перцентиль текущей волатильности внутри последних rank_n баров.

    Абсолютный порог волатильности не переносится ни между монетами, ни между
    годами: 1% в час у BTC в 2023-м и в 2025-м — это разные режимы рынка.
    Перцентиль внутри скользящего окна сравнивает рынок с самим собой недавним,
    и этот вопрос осмыслен всегда.

    Непрогретый участок помечается NaN явно и по формуле, а не по факту: если
    искать первый годный бар просмотром всего массива, ответ начнёт зависеть от
    того, что лежит в его конце.
    """
    rv = ind.realized_vol(c, int(vol_n))
    r = np.asarray(ind.rolling_rank(np.nan_to_num(rv, nan=0.0), int(rank_n)),
                   dtype=np.float64).copy()
    dead = int(vol_n) + int(rank_n) - 2      # окно ранга ещё задевает прогрев
    r[:min(len(r), dead)] = np.nan
    r[~np.isfinite(rv)] = np.nan
    return r


def _align_by_close(bars, src, values):
    """Ряд соседнего инструмента — на нашу сетку, ПО МОМЕНТУ ЗАКРЫТИЯ БАРА.

    Не по номеру строки. Совпадение меток у наших файлов — приятный факт, а не
    закон: одна пропущенная свеча у соседа, и весь ряд поедет на бар вперёд,
    подарив стратегии знание будущего ровно там, где его труднее всего заметить.
    Берём последний бар соседа, закрывшийся не позже закрытия нашего.

    Возвращает (значения, доля баров с ТОЧНЫМ совпадением меток) — вторая
    величина нужна, чтобы совпадение сеток проверять, а не предполагать.
    """
    my_close = bars.t + bars.bar_ms()
    src_close = src.t + src.bar_ms()
    idx = np.searchsorted(src_close, my_close, "right") - 1
    out = np.full(len(bars.t), np.nan)
    ok = idx >= 0
    out[ok] = np.asarray(values, dtype=np.float64)[idx[ok]]
    hit = 0.0
    if ok.any():
        same = src.t[idx[ok]] == bars.t[ok]
        hit = float(same.sum()) / len(bars.t)
    return out, hit


def _load(fn, *a):
    """Внешний ряд, которого может не быть на диске. Нет файла — нет фильтра."""
    try:
        return fn(*a)
    except (FileNotFoundError, OSError, ValueError):
        return None


# --- 1. Режим по ADX --------------------------------------------------------

def _ov_regime_adx(bars, out, p):
    """Гипотеза: у пробоя и у возврата к средней разные дома.

    ADX меряет не направление, а СВЯЗНОСТЬ хода: высокий ADX означает, что цена
    идёт в одну сторону не отдельными рывками, а подряд. Пробойной идее нужен
    именно такой рынок — иначе выход за границу канала гаснет через два бара, и
    остаются только комиссия и проскальзывание. Возврату к средней нужен
    обратный рынок: в боковике отклонение от середины и правда возвращается, а
    в тренде «перепроданность» держится неделями и убивает контр-трендовый вход.

    mode='trend' — пускать только при ADX не ниже порога (для пробоев);
    mode='flat'  — только при ADX не выше порога (для возврата к средней).
    """
    a, _pdi, _ndi = ind.adx(bars.h, bars.l, bars.c, int(p.get("adx_n", 14)))
    known = np.isfinite(a)
    thr = float(p.get("thr", 20))
    x = np.where(known, a, 0.0)
    ok = (x >= thr) if p.get("mode", "trend") == "trend" else (x <= thr)
    return _gate(out, ~known | ok, "regime_adx", p, known)


# --- 2. Режим по волатильности ---------------------------------------------

def _ov_regime_vol(bars, out, p):
    """Гипотеза: у стратегии есть полоса волатильности, в которой она живёт.

    Слишком тихий рынок — ход не покрывает круговых издержек (0.16% от объёма),
    и статистически верный сигнал всё равно уходит в минус. Слишком бурный —
    стоп из ATR разъезжается, проскальзывание растёт, а движения перестают
    доводиться до конца: их обрывают каскады ликвидаций в обе стороны.
    Разумно ожидать, что заработок сидит в средней части распределения, а не на
    его хвостах.

    Полоса задаётся перцентилями lo..hi. Сочетание lo=0, hi=1 оставлено в сетке
    намеренно: это контрольная точка «фильтра нет», с которой сравниваются
    остальные. Без неё легко принять за пользу фильтра обычный шум.
    """
    r = _vol_rank(bars.c, p.get("vol_n", 96), p.get("rank_n", 500))
    known = np.isfinite(r)
    lo, hi = float(p.get("lo", 0.0)), float(p.get("hi", 1.0))
    x = np.where(known, r, 0.0)
    ok = (x >= lo) & (x <= hi)
    return _gate(out, ~known | ok, "regime_vol", p, known)


# --- 3. Направление старшего таймфрейма ------------------------------------

def _ov_htf_trend(bars, out, p):
    """Гипотеза: против четырёхчасового тренда торговать дороже, чем по нему.

    Крупные деньги двигают цену не за один час, и их след виден на старшем
    масштабе как устойчивое направление. Вход против него платит дважды: он
    чаще выбивается по стопу и реже доходит до тейка, потому что откат в
    сторону сделки заканчивается раньше, чем тренд успевает развернуться.

    ЗДЕСЬ ЖИВЁТ САМАЯ ДОРОГАЯ ОШИБКА ВСЕГО КАТАЛОГА. 4ч-бар с меткой 12:00
    закрывается в 16:00, и до 16:00 его закрытие неизвестно. Взять его на
    часовом баре 12:00 — значит знать будущее на четыре часа вперёд; такой
    фильтр «улучшает» что угодно, включая подбрасывание монеты. Поэтому
    выравнивание идёт ТОЛЬКО через rdata.htf_aligned, который берёт последний
    старший бар, закрывшийся строго раньше начала младшего.

    mode='price' — закрытие старшего бара выше/ниже своей EMA;
    mode='slope' — сама EMA растёт или падает (медленнее, но спокойнее: цена
    дёргает среднюю, средняя себя — нет). Наклон меряется не за один бар, а за
    пятую часть длины средней: за один бар EMA-100 сдвигается на сотые доли
    процента, и любой осмысленный порог мёртвой зоны погасил бы вообще всё.
    """
    hb = _load(rdata.load_bars, bars.symbol, "240")
    if hb is None:
        return _record(out, "htf_trend", p, int((out.entry != 0).sum()),
                       np.zeros(len(bars.t)))
    ema_n = int(p.get("ema_n", 50))
    e = ind.ema(hb.c, ema_n)
    if p.get("mode", "price") == "price":
        base, ref = hb.c, e
    else:
        lag = max(1, ema_n // 5)
        ref = np.concatenate([np.full(lag, np.nan), e[:-lag]])
        base = e
    ok = np.isfinite(base) & np.isfinite(ref) & (ref > 0)
    rel = np.where(ok, base / np.where(ok, ref, 1.0) - 1.0, np.nan)
    dirn_htf = _sign_band(rel, float(p.get("tol", 0.0)))

    dirn = rdata.htf_aligned(bars, hb, dirn_htf)
    known = np.isfinite(dirn)
    d = np.where(known, dirn, 0.0)
    ok_bar = d == out.entry.astype(np.float64)      # 0 в мёртвой зоне = запрет
    return _gate(out, ~known | ok_bar, "htf_trend", p, known)


# --- 4. Режим биткоина для альткоина ---------------------------------------

def _ov_btc_regime(bars, out, p):
    """Гипотеза: альткоин — это BTC с плечом, и своего мнения у него мало.

    В криптовалюте почти вся дисперсия отдельной монеты объясняется общим
    движением рынка, а рынок — это биткоин. Собственный сигнал на SOL или DOGE
    может быть верным по форме, но если BTC в это время едет вниз, лонг по
    альту доедет только до стопа. Фильтр разрешает вход только по направлению
    биткоина того же таймфрейма.

    Выравнивание — по моменту закрытия бара BTC, а не по номеру строки: доля
    точных совпадений меток считается и кладётся в meta, чтобы совпадение сеток
    было проверенным фактом, а не общим убеждением.

    Для самого BTCUSDT фильтр вырождается в трендовый фильтр на собственных
    данных — это не ошибка, а полезная нижняя граница: столько же даёт та же
    идея без всякой межмонетной связи.
    """
    src = _load(rdata.load_bars, "BTCUSDT", str(bars.tf))
    if src is None:
        return _record(out, "btc_regime", p, int((out.entry != 0).sum()),
                       np.zeros(len(bars.t)))
    n = int(p.get("n", 100))
    if p.get("mode", "ema") == "ema":
        e = ind.ema(src.c, n)
        ok = np.isfinite(e) & (e > 0)
        rel = np.where(ok, src.c / np.where(ok, e, 1.0) - 1.0, np.nan)
    else:
        rel = ind.roc(src.c, n)                     # ход за n баров, в долях
    dirn_src = _sign_band(rel, float(p.get("tol", 0.0)))

    dirn, hit = _align_by_close(bars, src, dirn_src)
    known = np.isfinite(dirn)
    d = np.where(known, dirn, 0.0)
    ok_bar = d == out.entry.astype(np.float64)
    _gate(out, ~known | ok_bar, "btc_regime", p, known)
    out.meta["overlays"][-1]["exact_ts"] = hit
    return out


# --- 5. Размер против волатильности биткоина -------------------------------

def _ov_btc_vol_size(bars, out, p):
    """Гипотеза: в шторм на BTC ошибаются все, и дорожает не риск, а его цена.

    Когда волатильность биткоина уходит в верхний перцентиль, растёт не только
    размах — рвутся обычные связи: проскальзывание шире, стопы собираются
    целыми гроздьями, корреляция всех монет уезжает к единице, и портфель, ещё
    вчера разнесённый по инструментам, ведёт себя как одна позиция. Разумная
    реакция — не отказаться от сделки, а войти меньшим объёмом.

    Поэтому накладка НЕ гасит вход, а умножает size_k. Разница принципиальная:
    запрет меняет набор сделок и делает сравнение «с фильтром и без» сравнением
    двух разных стратегий; множитель оставляет тот же набор и меряет ровно одно
    — стоило ли в эти дни рисковать меньше.
    """
    src = _load(rdata.load_bars, "BTCUSDT", str(bars.tf))
    if src is None:
        return _record(out, "btc_vol_size", p, int((out.entry != 0).sum()),
                       np.zeros(len(bars.t)))
    r_src = _vol_rank(src.c, p.get("vol_n", 96), p.get("rank_n", 500))
    r, hit = _align_by_close(bars, src, r_src)
    known = np.isfinite(r)
    hot = known & (np.where(known, r, 0.0) >= float(p.get("thr", 0.8)))
    k = float(p.get("k", 0.5))
    before = int((out.entry != 0).sum())
    out.size_k = out.size_k * np.where(hot, k, 1.0)
    _record(out, "btc_vol_size", p, before, known)
    out.meta["overlays"][-1]["exact_ts"] = hit
    out.meta["overlays"][-1]["shrunk"] = float(np.mean(hot))
    return out


# --- 6. Фильтр по фандингу --------------------------------------------------

def _ov_funding_filter(bars, out, p):
    """Гипотеза: дорогой фандинг — это счётчик перегретой толпы.

    Ставка фандинга держит цену бессрочного контракта у спота, и платит та
    сторона, которой больше. Стабильно высокая положительная ставка означает
    буквально следующее: лонгов с плечом набилось столько, что они согласны
    платить за право стоять. Такая позиция стоит дважды — прямыми выплатами
    каждые восемь часов и тем, что именно её стопы становятся ближайшим
    топливом для сноса вниз. Зеркально для глубоко отрицательной ставки и
    шортов. Значит: запрещаем лонги при слишком дорогом фандинге и шорты при
    слишком отрицательном.

    ЕДИНИЦЫ. В funding_*.json ставка лежит в ПРОЦЕНТАХ за 8 часов (ext_data
    умножает ответ биржи на 100), поэтому нейтральный уровень Bybit 0.01% —
    это число 0.01, а не 0.0001. Пороги сетки заданы в тех же процентах.

    КАК ЧИТАТЬ РЕЗУЛЬТАТ ЭТОЙ НАКЛАДКИ — с осторожностью. Движок берёт ставку
    из того же файла как ДОЛЮ (rdata.funding_paid умножает объём на неё
    напрямую), то есть считает 0.01% как 1%. На обучающей выборке из-за этого
    выходит около 0.33% объёма за девятичасовую сделку вместо примерно 0.012%
    по настоящей ставке — расход завышен примерно на два порядка. Значит любой
    фильтр, уводящий от дорогого фандинга, будет выглядеть блестяще по причине,
    не имеющей отношения к рынку. Пока расхождение единиц не разобрано, вклад
    этой накладки надо смотреть при Cfg(funding=False) — тогда она меряет то,
    ради чего написана: перегрев толпы как признак, а не уход от платежа.

    Значение берётся только опубликованное: rdata.as_of отдаёт последнюю
    ставку, помеченную СТРОГО раньше закрытия нашего бара. Ставку, помеченную
    08:00, в 08:00 ещё никто не знает.
    """
    ser = _load(rdata.load_funding, bars.symbol)
    if ser is None:
        return _record(out, "funding_filter", p, int((out.entry != 0).sum()),
                       np.zeros(len(bars.t)))
    ft, fv = ser
    sm = int(p.get("smooth", 1))
    v = fv if sm <= 1 else ind.sma(fv, sm)   # среднее последних выплат, причинно
    f = rdata.as_of(ft, v, bars.t + bars.bar_ms())
    known = np.isfinite(f)
    x = np.where(known, f, 0.0)
    hi = float(p.get("hi", 0.02))
    long_ok = x <= hi
    short_ok = x >= -hi
    e = out.entry
    ok = np.where(e > 0, long_ok, np.where(e < 0, short_ok, True))
    return _gate(out, ~known | ok, "funding_filter", p, known)


# --- 7. Фильтр по открытому интересу ---------------------------------------

def _ov_oi_filter(bars, out, p):
    """Гипотеза: цена говорит, КУДА, а открытый интерес — на чьи деньги.

    Открытый интерес — число живых контрактов. Он растёт, когда открываются
    НОВЫЕ позиции, и падает, когда старые закрываются. Отсюда четыре случая, и
    смысл у них разный, хотя цена в двух парах ведёт себя одинаково:

      цена вверх + ОИ вверх   — приходят новые покупатели, ход обеспечен свежими
                                деньгами; у движения есть чем продолжаться;
      цена вверх + ОИ вниз    — закрываются шорты, топлива нет; рост кончится,
                                как только закончатся выкупающие;
      цена вниз + ОИ вверх    — открываются новые шорты, давление настоящее;
      цена вниз + ОИ вниз     — вылетают лонги (фиксация и ликвидации); падение
                                чаще упирается в дно, чем продолжается.

    combo выбирает, какие из четырёх клеток пускают вход:
      'new_money' — только на новых деньгах: лонг при цене вверх и ОИ вверх,
                    шорт при цене вниз и ОИ вверх (ставка на продолжение);
      'unwind'    — только на закрытии чужих позиций (ставка на исчерпание);
      'oi_up'     — любой вход, лишь бы ОИ рос;
      'oi_down'   — любой вход, лишь бы ОИ падал.

    ИСТОРИЯ ОИ КОРОЧЕ ИСТОРИИ ЦЕН: она начинается около октября 2024, то есть
    примерно с двух третей обучающей выборки. Там, где значения нет, накладка
    ОБЯЗАНА пропускать вход. Иначе фильтр молча вырежет первую половину
    истории, кривая станет короче и красивее, и это примут за пользу фильтра, а
    не за выбор удобного куска. Доля баров с известным ОИ кладётся в meta —
    читать результат без неё нельзя.
    """
    ser = _load(rdata.load_oi, bars.symbol)
    if ser is None:
        return _record(out, "oi_filter", p, int((out.entry != 0).sum()),
                       np.zeros(len(bars.t)))
    ot, ov = ser
    bar = bars.bar_ms()
    at = bars.t + bar
    now = rdata.as_of(ot, ov, at)
    back = int(p.get("oi_n", 24))
    prev = rdata.as_of(ot, ov, at - back * bar)
    good = np.isfinite(now) & np.isfinite(prev) & (prev > 0)
    doi = np.where(good, now / np.where(good, prev, 1.0) - 1.0, np.nan)
    dpx = ind.roc(bars.c, int(p.get("px_n", 12)))

    # Нулевое изменение — это «нет информации», а не «ОИ падает». На таймфрейме
    # мельче часа два соседних запроса попадают в одну и ту же точку ОИ, и без
    # этой оговорки фильтр запрещал бы всё подряд по чисто технической причине.
    ou = np.isfinite(doi) & (doi > 0)
    od = np.isfinite(doi) & (doi < 0)
    pu = np.isfinite(dpx) & (dpx > 0)
    pd = np.isfinite(dpx) & (dpx < 0)
    combo = p.get("combo", "new_money")
    e = out.entry

    if combo in ("oi_up", "oi_down"):
        known = ou | od
        ok = ou if combo == "oi_up" else od
    else:
        known = (ou | od) & (pu | pd)
        if combo == "new_money":
            ok = np.where(e > 0, pu & ou, np.where(e < 0, pd & ou, True))
        else:                                        # 'unwind'
            ok = np.where(e > 0, pu & od, np.where(e < 0, pd & od, True))
    return _gate(out, ~known | ok, "oi_filter", p, known)


# --- 8. Торговая сессия -----------------------------------------------------

def _ov_session(bars, out, p):
    """Гипотеза: сутки на крипторынке неоднородны, хотя биржа не закрывается.

    Ликвидность приходит вместе с людьми: азиатское утро, европейский день,
    американское открытие. В часы пересменки книга тоньше, спред шире, а
    «пробой» чаще оказывается выносом стопов на пустом стакане — то есть ровно
    тем сигналом, который стратегия принимает за правду и оплачивает. Выходные
    — отдельный случай: институциональные деньги стоят, и движения выходного
    дня часто отыгрываются обратно к утру понедельника.

    ЧАС БЕРЁТСЯ ТОТ, В КОТОРЫЙ ПОЗИЦИЯ ОТКРОЕТСЯ. Движок исполняет сигнал бара
    i на открытии бара i+1, значит окно надо мерить по началу бара i+1, иначе
    вся сетка сессий уедет на час. Будущего здесь нет: календарь известен
    заранее, время бара не зависит от цен.
    """
    open_t = bars.t + bars.bar_ms()          # момент фактического входа
    hour = ((open_t // HOUR_MS) % 24).astype(np.int64)
    # 1 января 1970 — четверг, поэтому при нумерации с понедельника сдвиг +3
    wd = ((open_t // DAY_MS) + 3) % 7
    h0, h1 = SESSIONS[p.get("window", "all")]
    ok = (hour >= h0) & (hour < h1)
    w = p.get("wknd", "all")
    if w == "weekdays":
        ok = ok & (wd < 5)
    elif w == "weekends":
        ok = ok & (wd >= 5)
    return _gate(out, ok, "session", p, np.ones(len(bars.t), dtype=bool))


# --- реестр накладок --------------------------------------------------------

OVERLAYS = {
    "regime_adx": {
        "fn": _ov_regime_adx, "kind": "entry",
        "grid": {"adx_n": [14, 20], "thr": [15, 20, 25, 30],
                 "mode": ["trend", "flat"]},
        "note": "пробою нужен связный ход, возврату к средней — боковик",
    },
    "regime_vol": {
        "fn": _ov_regime_vol, "kind": "entry",
        "grid": {"vol_n": [24, 96], "rank_n": [250, 500],
                 "lo": [0.0, 0.2, 0.4], "hi": [0.6, 0.8, 1.0]},
        "note": "у стратегии есть полоса волатильности, вне её она платит зря",
    },
    "htf_trend": {
        "fn": _ov_htf_trend, "kind": "entry",
        "grid": {"ema_n": [20, 50, 100], "mode": ["price", "slope"],
                 "tol": [0.0, 0.002, 0.005]},
        "note": "вход против 4ч-тренда стоит дороже, чем приносит",
    },
    "btc_regime": {
        "fn": _ov_btc_regime, "kind": "entry",
        "grid": {"n": [24, 50, 100, 200], "mode": ["ema", "roc"],
                 "tol": [0.0, 0.003, 0.01]},
        "note": "альткоин почти не имеет своего мнения против биткоина",
    },
    "btc_vol_size": {
        "fn": _ov_btc_vol_size, "kind": "size",
        "grid": {"vol_n": [24, 96], "rank_n": [250, 500],
                 "thr": [0.7, 0.8, 0.9], "k": [0.3, 0.5, 0.7]},
        "note": "в шторм на BTC рвутся связи — входить тем же объёмом дорого",
    },
    "funding_filter": {
        "fn": _ov_funding_filter, "kind": "entry",
        "grid": {"hi": [0.01, 0.02, 0.035, 0.06], "smooth": [1, 3, 9]},
        "note": "дорогой фандинг = перегретая толпа на той же стороне",
    },
    "oi_filter": {
        "fn": _ov_oi_filter, "kind": "entry",
        "grid": {"combo": ["new_money", "unwind", "oi_up", "oi_down"],
                 "oi_n": [8, 24, 72], "px_n": [4, 12]},
        "note": "открытый интерес отличает новые деньги от закрытия старых",
    },
    "session": {
        "fn": _ov_session, "kind": "entry",
        "grid": {"window": list(SESSIONS), "wknd": ["all", "weekdays",
                                                    "weekends"]},
        "note": "ликвидность приходит с людьми, а не равномерно по часам",
    },
}


def apply_overlay(bars, sig, name, p):
    """Навесить накладку name на готовый сигнал. Возвращает НОВЫЙ Signals.

    Исходный объект не меняется — накладки можно вешать по очереди и сравнивать
    порядок:  s2 = apply_overlay(b, apply_overlay(b, s, 'htf_trend', p1),
                                 'session', p2)
    """
    if name not in OVERLAYS:
        raise KeyError("нет накладки %r; есть: %s"
                       % (name, ", ".join(sorted(OVERLAYS))))
    out = _clone(sig)
    OVERLAYS[name]["fn"](bars, out, dict(p or {}))
    return out


def combos(name):
    """Все сочетания сетки накладки, в устойчивом порядке."""
    g = OVERLAYS[name]["grid"]
    keys = sorted(g)
    for vals in itertools.product(*[g[k] for k in keys]):
        yield dict(zip(keys, vals))


def n_combos(name):
    n = 1
    for v in OVERLAYS[name]["grid"].values():
        n *= len(v)
    return n


# --- проверка причинности по ВНЕШНИМ рядам ---------------------------------

def _corrupt_external(cut_ms, seed=0):
    """Испортить всё, что накладки читают с диска, ПОСЛЕ момента cut_ms.

    engine.assert_causal портит только те бары, которые ему передали, и этого
    достаточно для стратегии, но не для накладки: 4ч-свечи, свечи BTC, фандинг
    и открытый интерес приходят из отдельных файлов и остаются нетронутыми.
    Значит утечку через них та проверка не увидит — сигнал совпадёт по причине,
    не имеющей отношения к правильности.

    Поэтому подменяем содержимое кэша rdata: прошлое оставляем как есть, будущее
    заменяем шумом. Если хоть один вход до среза изменится — накладка читала
    ещё не опубликованное значение. Файлы на диске при этом не трогаются, кэш
    восстанавливается вызывающей стороной.
    """
    rng = np.random.default_rng(seed)
    saved = {}
    for key, val in list(rdata._CACHE.items()):
        if key[0] == "bars":
            b = val
            k = int(np.searchsorted(b.t, cut_ms, "left"))
            if k >= len(b.t):
                continue
            b2 = rdata.Bars.__new__(rdata.Bars)
            b2.symbol, b2.tf = b.symbol, b.tf
            b2.t = b.t.copy()
            for f in ("o", "h", "l", "c", "v", "turnover"):
                setattr(b2, f, getattr(b, f).copy())
            m = len(b.t) - k
            sh = rng.uniform(0.5, 1.8, m)
            b2.o[k:] *= sh
            b2.c[k:] *= sh
            b2.h[k:] = np.maximum(b2.o[k:], b2.c[k:]) * 1.01
            b2.l[k:] = np.minimum(b2.o[k:], b2.c[k:]) * 0.99
            b2.v[k:] *= rng.uniform(0.2, 5.0, m)
            b2.turnover[k:] = b2.c[k:] * b2.v[k:]
            saved[key] = val
            rdata._CACHE[key] = b2
        elif key[0] in ("fund", "oi"):
            st, sv = val
            k = int(np.searchsorted(st, cut_ms, "left"))
            if k >= len(st):
                continue
            v2 = sv.copy()
            m = len(st) - k
            v2[k:] = v2[k:] * rng.uniform(0.2, 4.0, m) + rng.uniform(-0.05,
                                                                    0.05, m)
            saved[key] = val
            rdata._CACHE[key] = (st, v2)
    return saved


def assert_causal_external(build, bars, cut=0.6, seed=0):
    """То же доказательство, но для чужих рядов: испортить будущее в файлах.

    build(bars) -> Signals. Сравниваются все поля Signals на барах ДО среза.
    """
    k = int(len(bars.t) * cut)
    cut_ms = int(bars.t[k])
    a = build(bars)
    saved = _corrupt_external(cut_ms, seed)
    if not saved:
        raise AssertionError("нечего портить: внешние ряды не загружены")
    try:
        b = build(bars)
    finally:
        rdata._CACHE.update(saved)
    bad = []
    for name in ("entry", "exit", "stop", "tp", "trail", "size_k"):
        x = np.asarray(getattr(a, name), dtype=np.float64)[:k]
        y = np.asarray(getattr(b, name), dtype=np.float64)[:k]
        d = ~np.isclose(x, y, rtol=1e-9, atol=1e-12, equal_nan=True)
        if d.any():
            bad.append("%s: %d расхождений, первое на баре %d из %d"
                       % (name, int(d.sum()), int(np.flatnonzero(d)[0]), k))
    if bad:
        raise AssertionError("ВНЕШНИЙ РЯД ИЗ БУДУЩЕГО -> " + "; ".join(bad))
    return True


# --- самопроверка -----------------------------------------------------------

if __name__ == "__main__":
    import sys
    import time

    import engine
    import metrics
    import strat

    FAIL = []

    def say(s=""):
        sys.stdout.write(s + "\n")

    BASE_P = dict(n=55, exit_n=20, stop_atr=3.0, atr_n=14, trail_atr=0.0)
    BASE = strat.REG["donchian"]

    def build_base(b):
        return BASE.build(b, BASE_P)

    def build_with(name, p):
        def fn(b):
            return apply_overlay(b, BASE.build(b, BASE_P), name, p)
        return fn

    def sample(name, cap=24, seed=0):
        """Все сочетания, а если их больше cap — cap случайных, но одних и тех же."""
        all_c = list(combos(name))
        if len(all_c) <= cap:
            return all_c
        idx = np.random.default_rng(seed).choice(len(all_c), cap, replace=False)
        return [all_c[i] for i in sorted(idx)]

    t0, t1 = rdata.SPLITS["train"]
    bars, _ = rdata.load_bars("BTCUSDT", "60").slice(t0, t1)
    alt, _ = rdata.load_bars("SOLUSDT", "60").slice(t0, t1)
    base_sig = build_base(bars)
    n_base = int((base_sig.entry != 0).sum())

    say("НАКЛАДКИ — самопроверка")
    say("данные: BTCUSDT 1ч, train, %d баров; альткоин для сверки: SOLUSDT"
        % len(bars.t))
    say("основа: donchian n=55 exit_n=20 stop_atr=3.0 — %d входных сигналов"
        % n_base)
    say("всего накладок: %d, сочетаний в сумме: %d"
        % (len(OVERLAYS), sum(n_combos(k) for k in OVERLAYS)))
    say()

    # 1. причинность сигнала вместе с накладкой -----------------------------
    say("1. ПРИЧИННОСТЬ: engine.assert_causal на «сигнал + накладка»")
    say("   (портятся цены после 60% выборки, сверяются сигналы до среза)")
    for name in sorted(OVERLAYS):
        cs = sample(name)
        tic = time.time()
        errs = []
        for p in cs:
            try:
                engine.assert_causal(build_with(name, p), bars)
            except AssertionError as exc:
                errs.append("%s: %s" % (p, str(exc)[:90]))
        ok = not errs
        if not ok:
            FAIL.append("причинность %s" % name)
        say("   %s %-15s %2d/%2d сочетаний%s   (%.1f с)"
            % ("+" if ok else "!", name, len(cs) - len(errs), len(cs),
               "" if ok else "  <-- " + errs[0], time.time() - tic))
    say()

    # 2. причинность по внешним файлам --------------------------------------
    say("2. ПРИЧИННОСТЬ ВНЕШНИХ РЯДОВ: 4ч-свечи, BTC, фандинг и ОИ")
    say("   портятся в кэше rdata после среза; вход до среза обязан не дрогнуть")
    ext = ["htf_trend", "btc_regime", "btc_vol_size", "funding_filter",
           "oi_filter"]
    # прогреваем кэш, иначе портить будет нечего
    for s in ("BTCUSDT", "SOLUSDT"):
        rdata.load_bars(s, "60")
        rdata.load_bars(s, "240")
        rdata.load_funding(s)
        rdata.load_oi(s)
    for name in ext:
        errs = []
        for p in sample(name, cap=12, seed=0):
            for b in (bars, alt):
                try:
                    assert_causal_external(build_with(name, p), b)
                except AssertionError as exc:
                    errs.append("%s %s: %s" % (b.symbol, p, str(exc)[:80]))
        ok = not errs
        if not ok:
            FAIL.append("внешняя причинность %s" % name)
        say("   %s %-15s%s" % ("+" if ok else "!", name,
                               "" if ok else "  <-- " + errs[0]))
    say()

    # 3. сколько входов остаётся --------------------------------------------
    say("3. СКОЛЬКО ВХОДОВ ОСТАЁТСЯ (BTCUSDT, от %d)" % n_base)
    say("   %-15s %-34s %s" % ("накладка", "разброс по всей сетке",
                               "показательное сочетание"))
    demo_p = {
        "regime_adx": dict(adx_n=14, thr=25, mode="trend"),
        "regime_vol": dict(vol_n=96, rank_n=500, lo=0.2, hi=0.8),
        "htf_trend": dict(ema_n=50, mode="price", tol=0.002),
        "btc_regime": dict(n=100, mode="ema", tol=0.003),
        "btc_vol_size": dict(vol_n=96, rank_n=500, thr=0.8, k=0.5),
        "funding_filter": dict(hi=0.02, smooth=3),
        "oi_filter": dict(combo="new_money", oi_n=24, px_n=12),
        "session": dict(window="us", wknd="weekdays"),
    }
    for name in sorted(OVERLAYS):
        kind = OVERLAYS[name]["kind"]
        vals = []
        for p in sample(name):
            s = apply_overlay(bars, base_sig, name, p)
            m = s.meta["overlays"][-1]
            vals.append(m["entries_after"] if kind == "entry"
                        else 100.0 * m.get("shrunk", 0.0))
        s = apply_overlay(bars, base_sig, name, demo_p[name])
        m = s.meta["overlays"][-1]
        if kind == "entry":
            rng_s = ("от %4d (%3.0f%%) до %4d (%3.0f%%)"
                     % (min(vals), 100.0 * min(vals) / n_base,
                        max(vals), 100.0 * max(vals) / n_base))
            got = ("%s -> %d (%.0f%%), знали %.0f%% баров"
                   % (demo_p[name], m["entries_after"],
                      100.0 * m["entries_after"] / n_base, 100 * m["known"]))
        else:
            rng_s = "размер урезан на %.0f..%.0f%% баров" % (min(vals),
                                                            max(vals))
            got = ("%s -> урезано %.0f%% баров, средний size_k %.2f"
                   % (demo_p[name], 100 * m["shrunk"], float(s.size_k.mean())))
        say("   %-15s %-34s %s" % (name, rng_s, got))
        if kind == "entry" and min(vals) > n_base:
            FAIL.append("накладка %s ДОБАВИЛА входов" % name)
    say()

    # проверка обещаний интерфейса ------------------------------------------
    say("4. ЧТО НАКЛАДКА ОБЯЗАНА СОБЛЮДАТЬ")
    s = apply_overlay(bars, base_sig, "htf_trend", demo_p["htf_trend"])
    check = [
        ("исходный сигнал не тронут",
         int((build_base(bars).entry != 0).sum()) == n_base
         and int((base_sig.entry != 0).sum()) == n_base),
        ("возвращён новый объект", s is not base_sig
         and s.entry is not base_sig.entry),
        ("выходы, стоп и тейк не изменены",
         bool((s.exit == base_sig.exit).all())
         and bool((s.stop == base_sig.stop).all())
         and bool((s.tp == base_sig.tp).all())),
        ("входы только гасятся, знак не меняется",
         bool(((s.entry == 0) | (s.entry == base_sig.entry)).all())),
        ("накладки складываются",
         len(apply_overlay(bars, s, "session",
                           demo_p["session"]).meta["overlays"]) == 2),
    ]
    # ОИ до октября 2024 неизвестен — там фильтра быть не должно
    oi_t = rdata.load_oi("BTCUSDT")[0][0]
    s_oi = apply_overlay(bars, base_sig, "oi_filter", demo_p["oi_filter"])
    early = bars.t + bars.bar_ms() <= oi_t
    check.append(("нет ОИ — нет фильтра (%.0f%% train до октября 2024)"
                  % (100.0 * early.mean()),
                  bool((s_oi.entry[early] == base_sig.entry[early]).all())))
    for label, ok in check:
        if not ok:
            FAIL.append(label)
        say("   %s %s" % ("+" if ok else "!", label))
    say()

    # 5. показательные прогоны ----------------------------------------------
    say("5. ПОКАЗАТЕЛЬНЫЕ ПРОГОНЫ (train, BTCUSDT 1ч, риск 1%, издержки на месте)")
    trials = sum(n_combos(k) for k in OVERLAYS)
    cfg = engine.Cfg()
    runs = [("donchian без накладок", None, None)]
    for name in ("regime_adx", "htf_trend", "session", "oi_filter"):
        runs.append(("+ " + name, name, demo_p[name]))
    for label, name, p in runs:
        sg = base_sig if name is None else apply_overlay(bars, base_sig, name, p)
        res = engine.run(bars, sg, cfg)
        say("   " + metrics.brief(metrics.summarize(res, n_trials=trials,
                                                    label=label)))
    say()

    # Из чего сложен убыток основы. Без этой строки любой вывод про накладки
    # рискует оказаться выводом про статью расходов, а не про рынок.
    r0 = engine.run(bars, base_sig, cfg)
    pnl0 = sum(t.pnl for t in r0.trades)
    fee0 = sum(t.fees for t in r0.trades)
    fund0 = sum(t.funding for t in r0.trades)
    hold0 = float(np.mean([t.i_out - t.i_in for t in r0.trades]))
    noti0 = float(np.mean([t.notional for t in r0.trades]))
    say("   из чего сложен итог основы: движение цены %+.0f, комиссии -%.0f, "
        "фандинг -%.0f" % (pnl0 + fee0 + fund0, fee0, fund0))
    say("   ВНИМАНИЕ: фандинг выходит %.3f%% объёма за %.0f ч удержания, а по "
        "ставке из файла" % (100 * fund0 / len(r0.trades) / noti0, hold0))
    say("   (медиана 0.0067%% за 8 ч) должно быть около %.3f%%. Расхождение в "
        "единицах:" % (0.0067 * hold0 / 8))
    say("   в funding_*.json ставка в ПРОЦЕНТАХ, а rdata.funding_paid берёт её "
        "как ДОЛЮ.")
    say("   Пока это не разобрано, накладку funding_filter надо мерить при "
        "Cfg(funding=False).")
    say()
    say("   для сверки — та же накладка на альткоине:")
    alt_sig = BASE.build(alt, BASE_P)
    for label, name, p in [("SOL без накладок", None, None),
                           ("SOL + btc_regime", "btc_regime",
                            demo_p["btc_regime"]),
                           ("SOL + btc_vol_size", "btc_vol_size",
                            demo_p["btc_vol_size"])]:
        sg = alt_sig if name is None else apply_overlay(alt, alt_sig, name, p)
        res = engine.run(alt, sg, cfg)
        say("   " + metrics.brief(metrics.summarize(res, n_trials=trials,
                                                    label=label)))
    say()

    if FAIL:
        say("ПРОВАЛЕНО: %d" % len(FAIL))
        for f in FAIL:
            say("   - " + f)
    else:
        say("ВСЁ ЗЕЛЕНО: причинность держится, накладки только гасят входы.")
    sys.exit(1 if FAIL else 0)
