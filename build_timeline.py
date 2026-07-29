# -*- coding: utf-8 -*-
"""Собирает webapp/data/evolution_timeline.json из уже посчитанных файлов
трёх этапов исследования — ничего заново не считает, только сводит:
  report3y.json          -> W1, W2, W3, FINAL(=v4/v5) на 3.2 годах
  evolution6_winners.json -> v6 (медвежий специалист), берём ту же ступень
                              лестницы плечей, что записана в config.py
  webapp/data/final_*.json -> v7 (текущий финал), уже посчитан с точными
                              значениями из config.py
"""

import json

import config

with open("report3y.json", encoding="utf-8") as fh:
    r3y = json.load(fh)
with open("evolution6_winners.json", encoding="utf-8") as fh:
    v6 = json.load(fh)

WAVE_LABELS = {
    "W1": "Первый подбор (1 год)",
    "W2": "Эволюция + направление (1 год)",
    "W3": "Глубокая эволюция v2 (2 года)",
    "FINAL": "v4/v5: киты, макро, индикаторы (3.2г)",
    "v6": "v6: медвежий специалист (гейт по режиму)",
    "v7": "v7: единый отбор — направление+режим+паттерны как гены",
    "v8": "v8: + Smart Money Concepts, проверка 15m/4ч",
}
WAVE_DESC = {
    "W1": "Первые вручную подобранные и эволюцией-1 параметры RSI-сетки.",
    "W2": "Генетика на годе данных + пробы направления сделок (лонг/шорт/оба).",
    "W3": "Честный движок, 2 года истории, walk-forward из 3 экзаменов.",
    "FINAL": "Funding, открытый интерес, S&P/DXY/золото, EMA/MA/Aroon — как гены с тумблером «выкл», 3.2 года.",
    "v6": "Торгует только в подтверждённом bear/боковике; в bull стоит в стороне.",
    "v7": "Направление, режимный гейт и фильтр классических паттернов — гены ОДНОГО генома, не отдельные волны. Один финальный бот на монету.",
    "v8": "Ордер-блоки/FVG/структура рынка как гены + отдельный прогон на 4ч. SMC не прошли экзамен нигде; 4ч отклонён — доля настоящих убытков там 0-2.5% (артефакт be_move на грубых барах, не реальная безопасность). Единственное принятое изменение — новый, честно проверенный конфиг LTC на 15m.",
}
# волна, которой принадлежит ТЕКУЩИЙ финальный конфиг (последняя точка графика)
FINAL_WAVE = {"DOGEUSDT": "v7", "LTCUSDT": "v8", "BTCUSDT": "v7",
             "ETHUSDT": "v7", "SOLUSDT": "v7"}

out = {}
for sym in config.SYMBOL_PARAMS:
    waves = []
    src = r3y.get(sym, {}).get("waves", {})
    for key in ("W1", "W2", "W3", "FINAL"):
        w = src.get(key)
        if not w:
            continue
        waves.append(dict(key=key, label=WAVE_LABELS[key], desc=WAVE_DESC[key],
                          ret=w["ret"], dd=w["dd"], wr=w["wr"],
                          trades=w["trades"], ruined=w["ruined"], lev=5))

    v6rec = v6.get(sym)
    if v6rec:
        chosen_lev = config.SYMBOL_PARAMS[sym]["bear"]["lev"]
        row = next((x for x in v6rec["ladder"] if x["lev"] == chosen_lev),
                   v6rec["ladder"][0])
        waves.append(dict(key="v6", label=WAVE_LABELS["v6"], desc=WAVE_DESC["v6"],
                          ret=row["ret"], dd=row["dd"], wr=None,
                          trades=None, ruined=row["ruined"], lev=chosen_lev,
                          adopted=v6rec["adopt"]))

    try:
        with open(f"webapp/data/final_{sym}.json", encoding="utf-8") as fh:
            fin = json.load(fh)
        ov = fin["overall"]
        fw = FINAL_WAVE.get(sym, "v7")
        waves.append(dict(key=fw, label=WAVE_LABELS[fw], desc=WAVE_DESC[fw],
                          ret=ov["ret"], dd=ov["dd"], wr=ov["wr"],
                          trades=ov["trades"], ruined=ov["ruined"],
                          lev=fin["lev"]))
    except FileNotFoundError:
        pass

    out[sym] = dict(period=src.get("period", ""), waves=waves)

with open("webapp/data/evolution_timeline.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2)

for sym, d in out.items():
    print(sym, [f"{w['key']}:{w['ret']:+.0f}%" for w in d["waves"]])
print("\n-> webapp/data/evolution_timeline.json")
