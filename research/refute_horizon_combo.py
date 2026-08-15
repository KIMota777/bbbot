# -*- coding: utf-8 -*-
u"""Составное правило дневного горизонта — БЕЗ ВСЯКОГО ОТБОРА.

Что показала refute_horizon_ts: на обучении (бычий рынок) любое трендовое
правило проигрывает простому «держи», а на проверке (рынок падал) «держи»
теряет 25%, тогда как часть правил зарабатывает. Соблазн — взять из таблицы
проверки лучшее правило. Это ровно та ошибка, из-за которой провалился автор:
из девяти строк выбрать лучшую и назвать это находкой.

Поэтому здесь берётся СРЕДНЕЕ ВСЕХ ДЕВЯТИ правил. Ни одно не выбрано, ни одно
не выброшено. Такой состав нельзя было подогнать: он определён до счёта.

ТРИ ВОПРОСА, НА КОТОРЫЕ НАДО ОТВЕТИТЬ ЧЕСТНО

1. Отличается ли результат от «просто держать»? Считается бета к рынку и
   остаток (альфа) — доход, не объяснимый удержанием.
2. Отличается ли он от нуля при таком сроке? Считается доверительный интервал
   блочным бутстрэпом и, отдельно, ПЕРЕСТАНОВОЧНЫЙ тест: веса сдвигаются по
   кругу относительно цен. Сдвинутые веса сохраняют и свою инерцию, и среднюю
   длинную долю (то есть весь заработок от роста рынка), но теряют привязку ко
   времени. Если настоящий результат не выделяется на фоне сдвинутых — вся
   прибыль была рыночным дрейфом, а не предсказанием.
3. Держится ли ответ на обучении и на проверке ОДИНАКОВО, или это опять
   «хорошо там, плохо тут».
"""
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import rdata                     # noqa: E402
import refute_horizon_lib as L   # noqa: E402
import refute_horizon_ts as TS   # noqa: E402

TREND = [r for r in TS.RULES if r[0] != u"ВСЕГДА ЛОНГ"]


def composite_dir(q, _=None):
    u"""Среднее направление девяти канонических правил: число в [-1, +1].

    Не голосование большинством и не «лучшее из»: простое среднее. Когда
    правила согласны — вес полный, когда спорят — позиция мельче. Это и есть
    то, что делает управляющий фьючерсами, и в этом нет ни одного подобранного
    числа.
    """
    ds = [fn(q, arg) for _n, fn, arg in TREND]
    return np.mean(np.vstack(ds), axis=0)


def build_composite(side=0):
    return TS.make_weights(composite_dir, None, side=side)


def bench_long():
    return TS.make_weights(TS.dir_always_long, 0)


def daily_returns(res):
    v = res["curve"]
    return np.diff(v) / np.maximum(v[:-1], 1e-9)


def block_boot(r, n=5000, block=10, seed=0):
    u"""Блочный бутстрэп среднего дневного дохода. Блоками — потому что убытки
    идут сериями, и разрыв серий занижает разброс."""
    rng = np.random.default_rng(seed)
    m = len(r)
    nb = max(1, m // block)
    out = np.empty(n)
    for i in range(n):
        st = rng.integers(0, max(1, m - block), nb)
        idx = np.concatenate([np.arange(s, s + block) for s in st])[:m]
        out[i] = r[np.clip(idx, 0, m - 1)].mean()
    return out


def rotate_null(build, split, n=400, seed=0):
    u"""Перестановочный тест: сдвиг весов по кругу относительно цен.

    Сдвиг сохраняет инерцию весов, среднюю длинную долю и оборот — то есть всё,
    чем можно заработать НЕ предсказывая. Разрушается только совпадение веса с
    будущим движением именно этой монеты. Доля сдвинутых прогонов, побивших
    настоящий, и есть вероятность получить такой результат без предсказания.
    """
    t, px = L.panel(rdata.SYMBOLS, "D")
    w_all = build(t, px)
    m = np.flatnonzero(L.in_split(t, split))
    t2 = t[m]
    px2 = {s: {k: v[m] for k, v in q.items() if k != "t"} for s, q in px.items()}
    w2 = {s: w_all[s][m] for s in w_all}
    real = L.WSim(t2, px2, w2).run()["ret"]
    rng = np.random.default_rng(seed)
    n2 = len(t2)
    out = np.empty(n)
    for i in range(n):
        k = int(rng.integers(20, n2 - 20))
        ws = {s: np.roll(w2[s], k) for s in w2}
        out[i] = L.WSim(t2, px2, ws).run()["ret"]
    return real, out


def report(name, r, bench=None):
    line = (u"%-22s %+8.1f%% %+8.2f%% %8.1f%% %7.2f %7.0f%%"
            % (name, 100 * r["ret"], 100 * r["mo"], 100 * r["maxdd"],
               r["sharpe"], 100 * r["mo_pos"]))
    if bench is not None:
        a = daily_returns(r)
        b = daily_returns(bench)
        k = min(len(a), len(b))
        c = float(np.corrcoef(a[:k], b[:k])[0][1]) if k > 5 else 0.0
        beta = c * a[:k].std() / max(b[:k].std(), 1e-12)
        alpha = float((a[:k] - beta * b[:k]).mean()) * 365
        line += u"  %+6.2f %+8.1f%%" % (beta, 100 * alpha)
    print(line)


def main():
    print(u"СОСТАВНОЕ ПРАВИЛО = среднее девяти канонических трендовых правил")
    print(u"на дневных барах пяти монет. Ничего не выбрано по результату.\n")
    t, px = L.panel(rdata.SYMBOLS, "D")
    L.refute_causal_w(build_composite(), t, px)
    L.refute_causal_w(build_composite(+1), t, px)
    L.refute_causal_w(build_composite(-1), t, px)
    print(u"причинность составного правила (обе стороны, лонг, шорт): пройдена\n")

    hdr = (u"%-22s %9s %9s %9s %7s %8s  %6s %9s"
           % (u"", u"итог", u"в месяц", u"просадка", u"Шарп", u"плюс мес",
              u"бета", u"альфа/год"))
    for split, title in (("train", u"ОБУЧЕНИЕ 740 дней"),
                         ("val", u"ПРОВЕРКА 244 дня"),
                         ("trainval", u"ОБУЧЕНИЕ+ПРОВЕРКА 980 дней")):
        print(u"=== %s ===" % title)
        print(hdr)
        bench = TS.run_on(split, bench_long())
        report(u"«просто держать»", bench)
        for nm, side in ((u"составное, обе стороны", 0),
                         (u"составное, только лонг", +1),
                         (u"составное, только шорт", -1)):
            r = TS.run_on(split, build_composite(side))
            report(nm, r, bench)
        print()

    # --- достоверность на обучении+проверке ---------------------------------
    print(u"ДОСТОВЕРНО ЛИ ЭТО (обучение+проверка, экзамен закрыт)\n")
    r = TS.run_on("trainval", build_composite())
    a = daily_returns(r)
    bs = block_boot(a)
    lo, hi = np.percentile(bs, [2.5, 97.5])
    print(u"средний дневной доход %+.4f%%, доверительный интервал 95%%: "
          u"%+.4f%% .. %+.4f%%" % (100 * a.mean(), 100 * lo, 100 * hi))
    print(u"в месяц это %+.2f%% при границах %+.2f%% .. %+.2f%%"
          % (100 * ((1 + a.mean()) ** 30 - 1), 100 * ((1 + lo) ** 30 - 1),
             100 * ((1 + hi) ** 30 - 1)))
    print(u"доля бутстрэп-прогонов ниже нуля: %.1f%%" % (100 * (bs < 0).mean()))

    real, null = rotate_null(build_composite(), "trainval", n=300)
    print(u"\nперестановочный тест (300 сдвигов весов по кругу):")
    print(u"   настоящий итог %+.1f%%, сдвинутые: медиана %+.1f%%, "
          u"95-й перцентиль %+.1f%%"
          % (100 * real, 100 * np.median(null), 100 * np.percentile(null, 95)))
    print(u"   доля сдвигов, побивших настоящий: %.1f%%  "
          u"(это и есть вероятность такого результата БЕЗ предсказания)"
          % (100 * (null >= real).mean()))

    # --- по монетам ---------------------------------------------------------
    print(u"\nПО МОНЕТАМ (обучение+проверка), чтобы видеть, не держится ли всё")
    print(u"на одной удачной монете:")
    for s in rdata.SYMBOLS:
        rr = TS.run_on("trainval", build_composite(), symbols=[s])
        print(u"   %-9s %+8.1f%%  в месяц %+6.2f%%  просадка %5.1f%%  Шарп %5.2f"
              % (s, 100 * rr["ret"], 100 * rr["mo"], 100 * rr["maxdd"],
                 rr["sharpe"]))


if __name__ == "__main__":
    main()
