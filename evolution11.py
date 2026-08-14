# -*- coding: utf-8 -*-
"""v11: переотбор конфигов на ИСПРАВЛЕННОМ движке.

Зачем понадобился. В движке нашлись два дефекта, оба делали бэктест
оптимистичнее реальности:

1. be_move ставил стоп в безубыток по ХАЮ бара. Если бар успевал уйти обратно
   ниже безубытка, стоп оказывался ВЫШЕ текущей цены — такой ордер биржа для
   лонга просто не примет. А движок потом честно «исполнял» по нему выход на
   следующем баре, засчитывая прибыль, которой не было. Замер на боевых
   конфигах: у DOGE так происходило в 21% срабатываний, у LTC 20%, у SOL 25%,
   у BTC 0% (у него тейк крупный, бар редко успевает вернуться).

2. Ликвидация проверялась только внутри ветки «задет стоп». При высоком плече
   цена ликвидации оказывается БЛИЖЕ стопа, и свеча могла дойти до ликвидации,
   не коснувшись стопа, — движок пропускал её и считал позицию живой.

После исправления прежние конфиги на 3.2 годах дают: DOGE -8.3%, LTC -33.3%,
SOL -51.3%, ETH +29.3%, BTC +148.3%. То есть DOGE/LTC/SOL отбирались под
артефакт, и их параметры нужно искать заново — на движке, который не врёт.

Метод тот же, что в v7/v8: полный геном как гены, общий harness
evolution4.run_version. Но сам протокол с тех пор исправлен, и описывать его
по-старому нельзя:
  * walk-forward больше не «3 экзамена». Кандидат оценивается только на окнах
    строго после своего обучения, победитель выбирается по окну валидации, а
    приёмка смотрит на отдельное экзаменационное окно (e4.choose_winner);
  * плечо x5..x15 выбирается не «затем», а ДО оценки — лестницей по ОБУЧАЮЩЕЙ
    части и по ПЛАВАЮЩЕЙ просадке (e4.choose_leverage); на выбранном плече
    считаются фитнес, экзамен и ворота honest_eval. Прежде отбор шёл на x5, а в
    конфиг уходило плечо до x15, подобранное по всей истории.

ОТЗЫВ ВЕРДИКТА ПО SOL (правка протокола, 08.2026). В evolution11_final.json
у SOLUSDT записано adopt: true при cand_oos = 0.00 против базы -3.24. Ноль там
означал не «конфиг хорош», а «конфиг почти не торгует»: 36 сделок за 3.2 года и
-14.5% итога. Ноль оказался больше минуса — и вырожденное решение прошло как
победа. Сам json НЕ переписан: это артефакт прогона, и подчищать историю
нельзя. Но вердикт по SOL из той волны недействителен, в config.py его брать
запрещено, а приёмка теперь физически так не может: honest_eval.degenerate_
reasons валит кандидата по минимуму сделок, сделок в месяц и экспозиции, и то
же самое проверяет tail_reasons по хвостовому риску.

Запуск: python evolution11.py
"""

import json
import time

import config
import evolution as ev
import evolution2 as e2
import evolution4 as e4
import evolution7 as e7
import evolution8 as e8
import ext_data as xd

DAYS = 1150
LEVS = [5, 8, 10, 12, 15]
DD_CAP = 0.20


def build_base_src():
    """Стартовые геномы — нынешние боевые конфиги (чтобы отбор начинал не
    с нуля, а с того, что уже есть, и мог их только улучшить)."""
    base = {}
    for sym, modes in config.SYMBOL_PARAMS.items():
        p = modes.get("final")
        if not p:
            continue
        g = e7.cfg_to_genome(p, "final")
        for k, v in e8.OFF8.items():
            g.setdefault(k, v)
        base[sym] = g
    return base


def ladder_for(sym, g, candles, pre, aux):
    filt = e8.make_filter8(g, aux)
    out = []
    for lev in LEVS:
        old, e2.LEV = e2.LEV, lev
        try:
            r = e2.run5(candles, pre, g, entry_filter=filt)
        finally:
            e2.LEV = old
        st = e2.stats(r)
        ddf = r.get("max_dd_float")
        out.append(dict(lev=lev, ret=round((r["balance"] / e2.START - 1) * 100, 1),
                        med=round(st["med"], 2), dd=round(r["max_dd"] * 100, 1),
                        # плавающая просадка — та, по которой и выбирается
                        # плечо: закрытая не видит переоценки открытой сетки
                        dd_float=(round(ddf * 100, 1)
                                  if ddf is not None else None),
                        ruined=r["ruined"], trades=r["trades"],
                        wr=round(r["wins"] / r["trades"] * 100, 1) if r["trades"] else 0))
    return out


def pick_lev(rows):
    """Наибольшее плечо с просадкой <= DD_CAP и без слива.

    rows обязаны быть лестницей по ОБУЧАЮЩЕЙ части (e4.train_slice): раньше
    сюда приходила лестница за все 3.2 года, то есть плечо подбиралось в том
    числе по экзаменационному окну — а потом на нём же сдавался экзамен.

    Проверка одна на все волны — e4.choose_leverage: плавающая просадка,
    дисквалификация слива и явное предупреждение, когда порог не проходит ни
    одна ступень (раньше в этом случае молча возвращалось минимальное плечо).
    """
    return e4.choose_leverage(rows, DD_CAP)["lev"]


REVOKED = {
    # монета -> почему вердикт прошлой волны недействителен. Печатается при
    # каждом запуске: файл evolution11_final.json остаётся как есть, а вот
    # молча пользоваться записанным в нём «adopt» нельзя.
    "SOLUSDT": "вердикт волны 08.2026 ОТОЗВАН: adopt:true при cand_oos 0.00 — "
               "это был вырожденный конфиг (36 сделок, -14.5% за 3.2г), "
               "а не преимущество",
}


def main():
    for sym, why in REVOKED.items():
        print(f"!!! {sym}: {why}")
    pct5 = xd.fetch_daily_pct5()
    aux_builder = e8.make_aux_builder(pct5, 96)
    base_src = build_base_src()
    t0 = time.time()

    results = e4.run_version(e8.GENES8, e8.OFF8, e8.make_filter8, aux_builder,
                             "evolution11", base_src, interval="15", days=DAYS)

    final = {}
    print("\n=== Лестница плечей на исправленном движке ===")
    for sym, rec in results.items():
        g = rec["genome"] if rec["adopt"] else rec["base_genome"]
        candles = ev.fetch(sym, "15", DAYS)
        pre = e2.prep(candles)
        aux = aux_builder(sym, candles)
        # Плечо. У принятого кандидата оно уже выбрано ВНУТРИ отбора: на нём
        # считались фитнес, экзамен и ворота риска. Подменять его здесь нельзя —
        # в конфиг ушло бы плечо, на котором конфиг ничего не сдавал. Для
        # отклонённого кандидата в дело идёт база, и её плечо выбирается тут,
        # по обучающей части. Полная лестница — только отчёт.
        train = e4.train_slice(candles)
        lad_train = ladder_for(sym, g, train, e2.prep(train), aux)
        if rec["adopt"]:
            lev, lev_ok, lev_warns = rec["lev"], rec["lev_confirmed"], []
        else:
            ch = e4.choose_leverage(lad_train, DD_CAP)
            lev, lev_ok, lev_warns = ch["lev"], ch["ok"], ch["warns"]
        for w in lev_warns:
            print(f"  ВНИМАНИЕ: {w}")
        lad = ladder_for(sym, g, candles, pre, aux)
        row = next(x for x in lad if x["lev"] == lev)
        print(f"\n{sym}: принят новый конфиг: {'ДА' if rec['adopt'] else 'нет'}"
              f" | плечо x{lev}"
              f"{'' if lev_ok else ' (НЕ ПОДТВЕРЖДЕНО просадкой)'}")
        for x in lad:
            mark = " <-" if x["lev"] == lev else ""
            ddf = "н/д" if x["dd_float"] is None else f"{x['dd_float']:5.1f}%"
            print(f"  x{x['lev']:<3} {x['ret']:+9.1f}% | DD закр {x['dd']:5.1f}%"
                  f" | DD плав {ddf} | "
                  f"WR {x['wr']:5.1f}% | сделок {x['trades']:5}"
                  f"{' СЛИВ' if x['ruined'] else ''}{mark}")
        final[sym] = dict(genome=g, ladder=lad, ladder_train=lad_train,
                          rec_lev=lev, lev_picked_on="train", best=row,
                          lev_confirmed=lev_ok, lev_warnings=lev_warns,
                          exam_lev=rec["lev"],
                          adopt=rec["adopt"], base_oos=rec["base_oos"],
                          cand_oos=rec["cand_oos"],
                          reject_reasons=rec.get("reject_reasons", []),
                          warnings=rec.get("warnings", []))

    # Итог пишется в файл С МЕТКОЙ ВРЕМЕНИ, а evolution11_final.json не
    # трогается. Причина простая: на него ссылается отзыв вердикта по SOL выше
    # (REVOKED), и там сказано «сам json НЕ переписан: это артефакт прогона».
    # Прежний код открывал ровно его на запись — первый же запуск уничтожил бы
    # доказательство, на которое ссылается текст.
    out_name = f"evolution11_final_{time.strftime('%Y%m%d-%H%M%S')}.json"
    with open(out_name, "w", encoding="utf-8") as fh:
        json.dump(final, fh, ensure_ascii=False, indent=2, default=float)

    print(f"\n\n=== ИТОГ v11 (за {time.time()-t0:.0f}с) ===")
    for sym, r in final.items():
        b = r["best"]
        print(f"{sym:<9} x{r['rec_lev']:<3} {b['ret']:+9.1f}% | DD {b['dd']:5.1f}% | "
              f"WR {b['wr']:5.1f}% | сделок {b['trades']:5} | "
              f"новый конфиг: {'ДА' if r['adopt'] else 'нет'}")
    print(f"\nИтоги в {out_name} (evolution11_final.json — артефакт волны "
          f"08.2026, не переписывается)")


if __name__ == "__main__":
    main()
