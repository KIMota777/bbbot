# -*- coding: utf-8 -*-
"""Состояние новостного фона: приём оценённых новостей и свёртка в два числа.

Контракт между MCP-сервером (пишет) и ботом (читает):
    news_state.json  ->  для каждой монеты (index, heat)

    index ∈ [-1..+1] — НАПРАВЛЕННЫЙ фон: +1 всё позитивно, -1 всё негативно.
    heat  ∈ [ 0..+1] — НАКАЛ: сколько вообще значимого происходит, без знака.
                       Высокий heat при index≈0 = «шумно и непонятно».

Почему два числа, а не одно: направление и неопределённость влияют на торговлю
по-разному. Попутный фон — повод дать прибыли пробежать. Просто громкий фон —
повод не открываться вовсе, независимо от знака.

ГРАНИЦЫ ВЛИЯНИЯ (это модуль безопасности, а не только арифметики).
Текст новости — недоверенный вход: его пишут посторонние люди, и он может
содержать прямые указания модели. Поэтому фон физически не способен:
  - открыть позицию (вход по-прежнему требует RSI+зона+все прежние фильтры);
  - поднять плечо, маржу или отключить DRY_RUN;
  - раздвинуть стоп дальше исходного (фон умеет только ПОДТЯГИВАТЬ стоп).
Всё, что фон умеет: запретить вход, подтянуть стоп, сдвинуть тейк в пределах
жёстких клампов ниже. Максимум влияния ограничен константами MAX_*, а не
доверием к содержимому новости.

Протухание: записи живут не дольше своего горизонта, а весь файл — не дольше
STATE_MAX_AGE_H. Если Claude перестал обновлять фон, влияние затухает до нуля
само. «Замерший» фон опаснее отсутствующего.
"""

import json
import math
import os
import tempfile
import time

import newsfeed

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "news_state.json")

# --- жёсткие потолки (менять осознанно: это границы влияния новостей) ---
MAX_ITEMS = 200          # больше записей сервер не хранит
MAX_WEIGHT = 1.0         # вес одной новости
MIN_HORIZON_H = 1.0      # горизонт действия новости
MAX_HORIZON_H = 72.0
STATE_MAX_AGE_H = 12.0   # файл старше — фон считается отсутствующим
SAT = 2.0                # «насыщение»: сумма эфф. весов, дающая |index| = 1
# ИЗВЕСТНАЯ ПРОБЛЕМА КАЛИБРОВКИ (замер 31.07.2026, обычный новостной день):
# у BTC сырая сумма весов вышла 2.99 при SAT=2.0 — heat упёрся в 1.000 и
# держался бы выше 0.75 около 15 часов. У альтов запас есть (0.45-0.59).
# Причина структурная: BTC собирает и адресные новости, и все общерыночные,
# а SAT одинаков для всех монет. Пока это не откалибровано по накопленной
# статистике, news_heat_max на BTC включать нельзя — он будет запрещать
# торговлю почти постоянно. Подробности — docs/NEWS_STRATEGY.md, раздел 6.

# Бета к BTC: насколько общерыночная новость (без прямого упоминания монеты)
# относится к этой монете. BTC=1.0 по определению; альты двигаются сильнее
# в накале, но общерыночный тон для них всё же слабее адресного.
BETA = {"BTCUSDT": 1.00, "ETHUSDT": 0.85, "SOLUSDT": 0.75,
        "LTCUSDT": 0.70, "DOGEUSDT": 0.65}
DEFAULT_BETA = 0.60

# --- потолки влияния на сделку (используются ботом и движком) ---
# Потолки выставлены не «на глаз», а по стресс-тесту news_stress.py, где
# канал включается на максимум и держится там все 3.2 года. Замер показал
# резкую асимметрию: растянутый тейк — самый вредный из каналов (DOGE терял
# 84 пп при просадке 18.7% -> 35.9%, потому что позиция висит дольше и чаще
# доезжает до стопа), тогда как ужатый тейк и поджатый стоп в основном
# обменивают доходность на просадку. Поэтому потолок растяжения вдвое ниже
# остальных: ошибиться в сторону жадности дороже, чем в сторону осторожности.
MAX_TP_STRETCH = 0.15    # тейк можно растянуть не более чем на +15%
MAX_TP_SHRINK = 0.30     # и ужать не более чем на -30%
MAX_SL_TIGHTEN = 0.35    # стоп можно подтянуть максимум на 35% пути к входу
                         # (раздвинуть стоп нельзя вообще — такой ветки в коде
                         # нет ни при каких значениях коэффициентов)

CATEGORIES = {
    # категория: (импакт 0..1, «знаковая ли» — бывает ли у неё явное направление)
    "regulation": (0.85, True),
    "etf_flows": (0.80, True),
    "exchange_hack": (0.95, True),
    "protocol_incident": (0.80, True),
    "macro": (0.75, True),
    "institutional": (0.60, True),
    "listing": (0.55, True),
    "upgrade_fork": (0.50, True),
    "onchain_flows": (0.45, True),
    "liquidations": (0.55, True),
    "partnership": (0.30, True),
    "market_report": (0.25, True),
    "opinion": (0.10, False),
    "price_analysis": (0.05, False),
    "other": (0.15, True),
}


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _now_ms():
    return int(time.time() * 1000)


def empty_state():
    return dict(version=1, updated_ms=0, items=[])


def load(path=None):
    # путь разрешается в момент ВЫЗОВА, а не при импорте: иначе тест не может
    # подменить STATE_FILE и вынужден писать в боевой файл фона
    path = path or STATE_FILE
    if not os.path.exists(path):
        return empty_state()
    try:
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return empty_state()
    if not isinstance(st, dict) or "items" not in st:
        return empty_state()
    return st


def save(state, path=None):
    """Атомарная запись: бот может читать файл в любой момент."""
    path = path or STATE_FILE
    state["updated_ms"] = _now_ms()
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".news_state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return state


def sanitize(rec):
    """Приводит запись от Claude к безопасному виду или отклоняет её.

    Возвращает (запись, None) либо (None, причина отказа). Сервер доверяет
    только СТРУКТУРЕ: домен из белого списка, вес в клампе, категория из
    словаря. Заголовок сохраняется как данные и никогда не интерпретируется.
    """
    if not isinstance(rec, dict):
        return None, "запись не объект"
    url = str(rec.get("url", "")).strip()
    title = str(rec.get("title", "")).strip()
    if not url or not title:
        return None, "нет url или title"
    domain = next((d for d in newsfeed.ALLOWED_DOMAINS if d in url), None)
    if domain is None:
        return None, f"домен вне белого списка: {url[:80]}"

    cat = str(rec.get("category", "other"))
    if cat not in CATEGORIES:
        return None, f"неизвестная категория: {cat}"

    try:
        weight = float(rec.get("weight", 0.0))
    except (TypeError, ValueError):
        return None, "вес не число"
    if not math.isfinite(weight):
        return None, "вес не число"
    weight = _clamp(weight, 0.0, MAX_WEIGHT)

    try:
        direction = int(rec.get("direction", 0))
    except (TypeError, ValueError):
        return None, "direction не целое"
    if direction not in (-1, 0, 1):
        return None, "direction должен быть -1, 0 или 1"
    if not CATEGORIES[cat][1]:
        direction = 0          # у мнений и теханализа знака не бывает

    try:
        horizon = float(rec.get("horizon_h", 12.0))
    except (TypeError, ValueError):
        horizon = 12.0
    horizon = _clamp(horizon, MIN_HORIZON_H, MAX_HORIZON_H)

    syms = rec.get("symbols") or []
    if not isinstance(syms, list):
        return None, "symbols должен быть списком"
    syms = [s for s in (str(x).upper() for x in syms) if s in BETA]

    published = rec.get("published_ms")
    try:
        published = int(published) if published is not None else _now_ms()
    except (TypeError, ValueError):
        published = _now_ms()
    # новость «из будущего» — почти всегда сбой парсинга даты; не доверяем
    published = min(published, _now_ms())

    try:
        conf = int(rec.get("confirmations", 1))
    except (TypeError, ValueError):
        conf = 1
    conf = _clamp(conf, 1, len(newsfeed.SOURCES))

    return dict(
        id=newsfeed.item_id(url, title), url=url, title=title[:300],
        domain=domain, category=cat, weight=round(weight, 4),
        direction=direction, horizon_h=round(horizon, 2), symbols=syms,
        market_wide=bool(rec.get("market_wide", not syms)),
        confirmations=conf, published_ms=published, scored_ms=_now_ms(),
        rationale=str(rec.get("rationale", ""))[:300]), None


def upsert(records, path=None):
    """Добавляет/обновляет оценённые новости. Возвращает (принято, отказы)."""
    path = path or STATE_FILE
    st = load(path)
    by_id = {r["id"]: r for r in st.get("items", []) if isinstance(r, dict)
             and "id" in r}
    accepted, rejected = [], []
    for rec in records:
        clean, why = sanitize(rec)
        if clean is None:
            rejected.append(dict(record=str(rec)[:160], reason=why))
            continue
        by_id[clean["id"]] = clean
        accepted.append(clean)
    items = sorted(by_id.values(), key=lambda r: r["scored_ms"], reverse=True)
    st["items"] = prune(items)[:MAX_ITEMS]
    save(st, path)
    return accepted, rejected


def prune(items, now_ms=None):
    """Выбрасывает записи, у которых истёк собственный горизонт."""
    now = now_ms or _now_ms()
    out = []
    for r in items:
        try:
            age_h = (now - int(r["published_ms"])) / 3600000.0
            if age_h <= float(r["horizon_h"]):
                out.append(r)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _freshness(age_h, horizon_h):
    """Экспоненциальное затухание с полураспадом = половина горизонта.

    В момент публикации 1.0, к концу горизонта ~0.25. Резкого обрыва нет —
    иначе бот дёргался бы на границе окна.
    """
    if age_h <= 0:
        return 1.0
    half = max(0.5, horizon_h / 2.0)
    return 0.5 ** (age_h / half)


def relevance(rec, symbol):
    """Насколько новость относится к этой монете: 1.0 — названа прямо,
    иначе бета к BTC для общерыночных, 0 — не относится."""
    if symbol in rec.get("symbols", []):
        return 1.0
    if rec.get("market_wide"):
        return BETA.get(symbol, DEFAULT_BETA)
    return 0.0


def background(symbol, path=None, now_ms=None):
    """Свёртка фона для монеты -> dict(index, heat, n, stale, top).

    index ∈ [-1..1], heat ∈ [0..1]. Если файла нет, он протух или пуст —
    возвращается нулевой фон: бот в этом случае работает ровно как раньше.
    """
    now = now_ms or _now_ms()
    st = load(path or STATE_FILE)
    updated = st.get("updated_ms", 0) or 0
    stale = (now - updated) / 3600000.0 > STATE_MAX_AGE_H
    if stale or not st.get("items"):
        return dict(index=0.0, heat=0.0, n=0, stale=bool(updated) and stale,
                    updated_ms=updated, top=[])

    signed = total = 0.0
    contrib = []
    for r in prune(st["items"], now):
        rel = relevance(r, symbol)
        if rel <= 0:
            continue
        age_h = (now - r["published_ms"]) / 3600000.0
        fresh = _freshness(age_h, r["horizon_h"])
        # подтверждение независимыми изданиями: +15% за каждое сверх первого,
        # но не более +30% — три источника уже не втрое убедительнее одного
        conf_k = 1.0 + min(0.30, 0.15 * (r.get("confirmations", 1) - 1))
        w_eff = r["weight"] * rel * fresh * conf_k
        if w_eff <= 0:
            continue
        signed += r["direction"] * w_eff
        total += w_eff
        contrib.append((w_eff, r))

    index = _clamp(signed / SAT, -1.0, 1.0)
    heat = _clamp(total / SAT, 0.0, 1.0)
    contrib.sort(key=lambda x: -x[0])
    top = [dict(title=r["title"][:120], category=r["category"],
                direction=r["direction"], weight=r["weight"],
                effective=round(w, 4), url=r["url"])
           for w, r in contrib[:5]]
    return dict(index=round(index, 4), heat=round(heat, 4), n=len(contrib),
                stale=False, updated_ms=updated, top=top)


# --- влияние фона на сделку (единственная точка, где фон трогает торговлю) ---

def tp_multiplier(bg, side, k):
    """Множитель к тейку. k — сила реакции (0 = фон не влияет вообще).

    Попутный фон растягивает тейк (дать прибыли пробежать), встречный —
    ужимает (забрать раньше). Всё в пределах MAX_TP_STRETCH/MAX_TP_SHRINK.
    """
    if not k:
        return 1.0
    sgn = 1.0 if side == "L" else -1.0
    aligned = _clamp(bg["index"] * sgn, -1.0, 1.0)   # +1 попутный, -1 встречный
    if aligned >= 0:
        return 1.0 + k * MAX_TP_STRETCH * aligned
    return 1.0 + k * MAX_TP_SHRINK * aligned         # aligned<0 -> сжатие


def sl_tighten_fraction(bg, side, k):
    """Доля пути от стопа К ВХОДУ, на которую подтягиваем стоп (0..1).

    Только подтягивание: раздвинуть стоп фон не может — это увеличивало бы
    убыток по подсказке из недоверенного текста. Реагируем на встречный фон
    и на общий накал.
    """
    if not k:
        return 0.0
    sgn = 1.0 if side == "L" else -1.0
    against = max(0.0, -_clamp(bg["index"] * sgn, -1.0, 1.0))
    drive = max(against, 0.5 * bg["heat"])
    return _clamp(k * MAX_SL_TIGHTEN * drive, 0.0, MAX_SL_TIGHTEN)


def entry_veto(bg, side, heat_max, index_min):
    """(вето?, причина). heat_max=0/index_min=0 -> фон входы не запрещает."""
    if heat_max and bg["heat"] > heat_max:
        return True, f"накал новостей {bg['heat']:.2f} > {heat_max:.2f}"
    if index_min:
        sgn = 1.0 if side == "L" else -1.0
        aligned = bg["index"] * sgn
        if aligned < -abs(index_min):
            return True, (f"встречный новостной фон {aligned:+.2f} < "
                          f"{-abs(index_min):+.2f}")
    return False, ""


def main():
    for sym in BETA:
        bg = background(sym)
        print(f"{sym:<9} index {bg['index']:+.3f} | heat {bg['heat']:.3f} | "
              f"новостей {bg['n']}" + (" | ФОН ПРОТУХ" if bg["stale"] else ""))
        for t in bg["top"][:2]:
            print(f"           {t['direction']:+d} w={t['weight']:.2f} "
                  f"эфф {t['effective']:.3f} [{t['category']}] {t['title'][:70]}")


if __name__ == "__main__":
    main()
