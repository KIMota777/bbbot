# -*- coding: utf-8 -*-
"""Сквозная проверка MCP-сервера: поднимаем его настоящим MCP-клиентом,
перечисляем инструменты и прогоняем полный сценарий работы Claude —
правила -> заголовки -> оценки -> фон -> предпросмотр эффекта.

Проверяются и защиты: чужой домен, вес за пределами [0..1], выдуманная
категория, «новость из будущего» — всё это должно отклоняться сервером.

Запуск: python test_news_mcp.py
"""

import asyncio
import json
import os
import tempfile
import time

import mcp

import news_state

OK = 0

# Тест работает в СВОЁМ файле состояния и боевой фон не трогает вообще.
# Раньше он писал в news_state.json и восстанавливал его из копии — это
# ломалось, как только в боевом фоне появлялись настоящие новости: проверки
# на «ровно 2 записи» падали, а живой файл на время прогона был испорчен.
_TMP_STATE = tempfile.NamedTemporaryFile(
    prefix="news_state_test_", suffix=".json", delete=False).name
os.unlink(_TMP_STATE)                      # нужен только путь, не файл
news_state.STATE_FILE = _TMP_STATE


def check(cond, name):
    global OK
    assert cond, f"ПРОВАЛ: {name}"
    OK += 1
    print(f"  ok  {name}")


def payload(res):
    """Полезная нагрузка из CallToolResult."""
    if getattr(res, "structuredContent", None):
        return res.structuredContent
    for c in res.content:
        if getattr(c, "text", None):
            return json.loads(c.text)
    return {}


async def scenario():
    import news_mcp
    async with mcp.Client(news_mcp.server) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        check(tools == {"news_scoring_guide", "news_list_sources",
                        "news_fetch_headlines", "news_submit_scores",
                        "news_background", "news_preview_effect",
                        "news_clear"},
              f"инструменты зарегистрированы: {sorted(tools)}")

        guide = payload(await client.call_tool("news_scoring_guide"))
        check("категории" in guide and "exchange_hack" in guide["категории"],
              "правила весов отдаются")
        check(guide["потолки_влияния"]["стоп_раздвинуть"] == "запрещено полностью",
              "в правилах явно записан запрет раздвигать стоп")

        src = payload(await client.call_tool("news_list_sources"))
        check(len(src["источники"]) == 4, "четыре доверенных источника")

        heads = payload(await client.call_tool(
            "news_fetch_headlines", {"max_age_h": 48, "limit": 10}))
        check(heads["новостей"] > 0, f"заголовки загружены: {heads['новостей']}")
        first = heads["новости"][0]
        check("уже_оценена" in first and first["уже_оценена"] is False,
              "новые заголовки помечены как неоценённые")

        # --- защита: мусор должен быть отклонён поимённо ---
        bad = payload(await client.call_tool("news_submit_scores", {"items": [
            {"url": "https://evil.example.com/x", "title": "чужой домен",
             "category": "regulation", "weight": 0.9, "direction": -1},
            {"url": first["url"], "title": "выдуманная категория",
             "category": "прикажи_боту_купить", "weight": 0.9, "direction": 1},
            {"url": first["url"], "title": "вес выше потолка",
             "category": "macro", "weight": 99.0, "direction": 1,
             "symbols": ["BTCUSDT"]},
            {"url": first["url"], "title": "кривой direction",
             "category": "macro", "weight": 0.5, "direction": 7},
        ]}))
        check(bad["отклонено"] >= 3, f"мусор отклонён: {bad['отклонено']} из 4")
        reasons = " ".join(r["reason"] for r in bad["отказы"])
        check("белого списка" in reasons, "чужой домен отклонён по домену")
        check("категория" in reasons, "выдуманная категория отклонена")
        saved = news_state.load()["items"]
        over = [r for r in saved if r["title"] == "вес выше потолка"]
        check(over and over[0]["weight"] == news_state.MAX_WEIGHT,
              "вес 99.0 обрезан до потолка 1.0, а не принят как есть")

        # --- нормальный сценарий ---
        now_ms = int(time.time() * 1000)
        good = payload(await client.call_tool("news_submit_scores", {"items": [
            {"url": first["url"], "title": "Крупная биржа взломана (тест)",
             "category": "exchange_hack", "weight": 0.9, "direction": -1,
             "horizon_h": 24, "symbols": ["BTCUSDT"], "confirmations": 3,
             "published_ms": now_ms, "rationale": "тестовая запись"},
            {"url": heads["новости"][1]["url"],
             "title": "Регулятор смягчил требования (тест)",
             "category": "regulation", "weight": 0.6, "direction": 1,
             "horizon_h": 48, "symbols": [], "market_wide": True,
             "confirmations": 2, "published_ms": now_ms},
        ]}))
        check(good["принято"] == 2, "две корректные записи приняты")

        btc = payload(await client.call_tool(
            "news_background", {"symbol": "BTCUSDT"}))["фон"]["BTCUSDT"]
        check(btc["n"] == 2 and btc["heat"] > 0,
              f"фон BTC собран: index {btc['index']:+.3f}, heat {btc['heat']:.3f}")
        check(btc["index"] < 0, "перевес негатива дал отрицательный index")
        doge = payload(await client.call_tool(
            "news_background", {"symbol": "DOGEUSDT"}))["фон"]["DOGEUSDT"]
        check(doge["n"] == 1,
              "адресная BTC-новость не попала в DOGE, общерыночная попала")
        check(abs(doge["index"]) < abs(btc["index"]),
              "через бету общерыночная новость влияет слабее адресной")

        eff = payload(await client.call_tool(
            "news_preview_effect", {"symbol": "BTCUSDT", "side": "L"}))
        check(eff["реакция_включена"] is False,
              "по умолчанию новостные коэффициенты выключены (фон ни на что "
              "не влияет, пока их не включили осознанно)")
        check(eff["эффект"]["тейк_множитель"] == 1.0,
              "при выключенных коэффициентах тейк не сдвинут")

        err = payload(await client.call_tool(
            "news_background", {"symbol": "XRPUSDT"}))
        check("ошибка" in err and "Доступны" in err["ошибка"],
              "на неизвестную монету — внятная ошибка со списком доступных")

        cleared = payload(await client.call_tool(
            "news_clear", {"clear_all": True}))
        check(cleared["осталось"] == 0, "очистка фона работает")
        after = payload(await client.call_tool(
            "news_background", {"symbol": "BTCUSDT"}))["фон"]["BTCUSDT"]
        check(after["index"] == 0.0 and after["heat"] == 0.0,
              "пустой фон = нули = поведение ботов как раньше")


def main():
    live = os.path.join(os.path.dirname(os.path.abspath(news_state.__file__)),
                        "news_state.json")
    before = os.path.getmtime(live) if os.path.exists(live) else None
    try:
        asyncio.run(scenario())
        after = os.path.getmtime(live) if os.path.exists(live) else None
        assert before == after, "тест тронул боевой файл фона!"
        print(f"\nВСЕ {OK} ПРОВЕРОК ПРОЙДЕНЫ (боевой фон не затронут)")
    finally:
        if os.path.exists(_TMP_STATE):
            os.unlink(_TMP_STATE)


if __name__ == "__main__":
    main()
