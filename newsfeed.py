# -*- coding: utf-8 -*-
"""Загрузка новостного фона из открытых RSS-лент.

Здесь НЕТ оценок и весов — только честная выгрузка заголовков и то, что можно
посчитать детерминированно (время, домен, категории ленты, упоминания монет,
подтверждение независимыми источниками). Веса присваивает Claude через
MCP-сервер (news_mcp.py) по правилам docs/NEWS_STRATEGY.md.

Почему разделено: вес — это суждение, и его должна ставить модель, видевшая
текст. Но всё, что можно посчитать без суждения, считает код — тогда Claude
не может «придумать» подтверждение из трёх источников, которого не было.

Сеть: используем requests (в нём CA-сертификаты certifi). Голый urllib на
macOS с python.org падает на CERTIFICATE_VERIFY_FAILED — проверено.

Проверка вручную:  python newsfeed.py
"""

import email.utils
import hashlib
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

import requests

UA = "bbbot-newsfeed/1.0 (+https://github.com/KIMota777/bbbot)"
TIMEOUT = 20

# Доверие к источнику (0..1) — множитель S в формуле веса, см.
# docs/NEWS_STRATEGY.md. Ставится по редакционной репутации и доле
# первичных материалов, а не по «нравится/не нравится».
SOURCES = {
    "coindesk": dict(
        title="CoinDesk", domain="coindesk.com", trust=0.90,
        url="https://www.coindesk.com/arc/outboundfeeds/rss/"),
    "theblock": dict(
        title="The Block", domain="theblock.co", trust=0.90,
        url="https://www.theblock.co/rss.xml"),
    "cointelegraph": dict(
        title="Cointelegraph", domain="cointelegraph.com", trust=0.75,
        url="https://cointelegraph.com/rss"),
    "decrypt": dict(
        title="Decrypt", domain="decrypt.co", trust=0.70,
        url="https://decrypt.co/feed"),
}

# Домены, которым разрешено попадать в состояние фона. Ссылка не из этого
# списка = запись отклоняется сервером. Так текст новости не может подсунуть
# боту произвольный источник.
ALLOWED_DOMAINS = frozenset(s["domain"] for s in SOURCES.values())


def match_allowed_domain(url):
    """Домен белого списка, которому ПРИНАДЛЕЖИТ ссылка, иначе None.

    Сверяется разобранный hostname, а не подстрока. Проверка подстрокой
    (`d in url`) пропускала что угодно, где домен встречается хоть где-то:

        https://evil.io/?ref=coindesk.com      подстрока в параметре
        https://theblock.com.attacker.io/      подстрока в чужом домене
        https://coindesk.com@evil.io/x         подстрока в userinfo
        https://evil.io/coindesk.co/статья     подстрока в пути

    Все четыре ведут на посторонний сервер, и запись с такой ссылкой
    попадала в фон как «из доверенного издания». Здесь совпадением
    считается только точный домен или его поддомен, и только по http(s):
    схемы вроде javascript: и data: отбрасываются вместе с ними.
    """
    try:
        parts = urllib.parse.urlsplit(str(url).strip())
        if parts.scheme.lower() not in ("http", "https"):
            return None
        host = parts.hostname          # уже без userinfo, порта и регистра
    except ValueError:                 # битые скобки IPv6, нечисловой порт
        return None
    if not host:
        return None
    host = host.strip(".")             # "coindesk.com." — тот же хост
    for d in sorted(ALLOWED_DOMAINS):
        if host == d or host.endswith("." + d):
            return d
    return None


# Прямое упоминание монеты в заголовке. Регистронезависимо, по границам слов.
SYMBOL_PATTERNS = {
    "BTCUSDT": r"\b(btc|bitcoin)\b",
    "ETHUSDT": r"\b(eth|ether|ethereum)\b",
    "LTCUSDT": r"\b(ltc|litecoin)\b",
    "DOGEUSDT": r"\b(doge|dogecoin)\b",
    "SOLUSDT": r"\b(sol|solana)\b",
}
_SYM_RE = {s: re.compile(p, re.I) for s, p in SYMBOL_PATTERNS.items()}

# Слова, по которым новость считается общерыночной (влияет на все монеты
# через бету к BTC, даже если конкретная монета не названа).
MARKET_WIDE_RE = re.compile(
    r"\b(crypto|cryptocurrency|digital asset|market|sec\b|cftc|regulat|"
    r"etf|fed\b|fomc|cpi|inflation|rate cut|rate hike|tariff|"
    r"stablecoin|exchange hack|liquidat)\w*", re.I)


def _text(el):
    return (el.text or "").strip() if el is not None else ""


def _parse_ts(raw):
    """RFC-822 pubDate -> unix ms UTC. None, если не разобрали."""
    if not raw:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def _norm_title(title):
    """Ключ для склейки одной новости из разных лент: только буквы и цифры."""
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def item_id(url, title):
    return hashlib.sha1(f"{url}|{_norm_title(title)}".encode()).hexdigest()[:16]


def detect_symbols(text):
    """Монеты, ПРЯМО названные в тексте. Пусто = новость общерыночная."""
    return sorted(s for s, rx in _SYM_RE.items() if rx.search(text))


def is_market_wide(text):
    return bool(MARKET_WIDE_RE.search(text))


def parse_feed(raw, source):
    """RSS 2.0 или Atom -> список нормализованных записей."""
    root = ET.fromstring(raw)
    nodes = root.findall(".//item")
    atom = False
    if not nodes:
        nodes = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        atom = True
    meta = SOURCES[source]
    out = []
    for n in nodes:
        if atom:
            title = _text(n.find("{http://www.w3.org/2005/Atom}title"))
            link_el = n.find("{http://www.w3.org/2005/Atom}link")
            link = (link_el.get("href") if link_el is not None else "") or ""
            ts = _parse_ts(_text(n.find("{http://www.w3.org/2005/Atom}updated")))
            cats = [c.get("term") or "" for c in
                    n.findall("{http://www.w3.org/2005/Atom}category")]
        else:
            title = _text(n.find("title"))
            link = _text(n.find("link"))
            ts = _parse_ts(_text(n.find("pubDate")))
            cats = [_text(c) for c in n.findall("category")]
        if not title or not link:
            continue
        cats = [c for c in cats if c]
        out.append(dict(
            id=item_id(link, title), source=source, source_title=meta["title"],
            domain=meta["domain"], trust=meta["trust"], title=title, url=link,
            published_ms=ts, age_h=(None if ts is None else
                                    round((time.time() * 1000 - ts) / 3600000, 2)),
            feed_categories=cats,
            mentions=detect_symbols(f"{title} {' '.join(cats)}"),
            market_wide=is_market_wide(f"{title} {' '.join(cats)}")))
    return out


def fetch_source(source, limit=40):
    meta = SOURCES[source]
    r = requests.get(meta["url"], timeout=TIMEOUT, headers={"User-Agent": UA})
    r.raise_for_status()
    return parse_feed(r.content, source)[:limit]


def fetch_all(sources=None, limit_per_source=40, max_age_h=48):
    """Свежие заголовки со всех лент, склеенные по одинаковым новостям.

    Склейка даёт поле confirmations — сколько НЕЗАВИСИМЫХ изданий написали об
    одном и том же. Это считает код, а не модель: подтверждение — фактическое
    множество источников, а не оценка.

    Возвращает (items, errors): ошибки лент не роняют выгрузку целиком.
    """
    names = list(sources or SOURCES)
    merged, errors = {}, {}
    for name in names:
        # имя ленты приходит от модели через MCP-инструмент, так что оно
        # может оказаться и не строкой: это тот же «неизвестный источник»,
        # а не повод уронить выгрузку целиком (нехешируемое имя вроде списка
        # роняло и проверку `not in`, и запись в errors — нашёл фаззинг)
        if not isinstance(name, str) or name not in SOURCES:
            errors[str(name)[:60]] = "неизвестный источник"
            continue
        try:
            items = fetch_source(name, limit_per_source)
        except Exception as e:                       # noqa: BLE001 — лента
            errors[name] = f"{type(e).__name__}: {e}"  # может лежать, это норма
            continue
        for it in items:
            if it["age_h"] is not None and it["age_h"] > max_age_h:
                continue
            key = _norm_title(it["title"])
            cur = merged.get(key)
            if cur is None:
                it["confirmations"] = 1
                it["confirmed_by"] = [it["source"]]
                merged[key] = it
                continue
            if it["source"] not in cur["confirmed_by"]:
                cur["confirmed_by"].append(it["source"])
                cur["confirmations"] = len(cur["confirmed_by"])
            # оставляем публикацию самого авторитетного издания
            if it["trust"] > cur["trust"]:
                keep = dict(it)
                keep["confirmations"] = cur["confirmations"]
                keep["confirmed_by"] = cur["confirmed_by"]
                merged[key] = keep
    items = sorted(merged.values(),
                   key=lambda x: x["published_ms"] or 0, reverse=True)
    return items, errors


def main():
    items, errors = fetch_all()
    print(f"Загружено {len(items)} новостей из {len(SOURCES)} лент")
    if errors:
        print("Ошибки лент:", errors)
    for it in items[:15]:
        mark = "*" if it["confirmations"] > 1 else " "
        syms = ",".join(s.replace("USDT", "") for s in it["mentions"]) or (
            "рынок" if it["market_wide"] else "-")
        print(f"{mark} [{it['source']:<13}] {it['age_h']:>5.1f}ч "
              f"{syms:<12} {it['title'][:78]}")


if __name__ == "__main__":
    main()
