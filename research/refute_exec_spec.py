# -*- coding: utf-8 -*-
"""РЕШЕНИЕ, ЗАФИКСИРОВАННОЕ ДО ЭКЗАМЕНА. Считается только на обучении+проверке.

Здесь не проверяется ничего нового. Здесь записывается, что именно будет
вынесено на экзамен, и почему — чтобы после экзамена нельзя было передумать.

ЧТО МЕНЯЕТСЯ ПО СРАВНЕНИЮ С FINAL_SPEC.md
  Ровно одно: способ сопровождения позиции. Половина объёма фиксируется на
  1.5R, остаток ведётся трейлом шириной в один стоп. Всё остальное —
  состав ансамбля, монеты, таймфрейм, окна отбора, издержки, вход маркетом по
  открытию следующего бара — остаётся нетронутым.

ЧТО ПРОВЕРЕНО И ОТВЕРГНУТО, И ПОЭТОМУ НЕ ПОПАЛО В РЕШЕНИЕ
  * лимитный вход на откат: хуже базы при любом отступе и любом сроке жизни
    заявки, потому что не исполняется именно в тех сделках, где цена ушла;
  * безубыток после 1R: хуже базы;
  * выход по времени вместо стопа: не лучше частичной фиксации;
  * управление просадкой (снижение риска, остановка торговли): на проверочном
    куске делает результат хуже во всех вариантах лестницы.

ПОЧЕМУ ДОЛЯ РИСКА СЧИТАЕТСЯ ТЕМ ЖЕ СПОСОБОМ, ЧТО У АВТОРА
  Двоичный поиск по 95-му перцентилю блочного бутстрэпа до просадки 20%. Взять
  другой способ значило бы поменять сразу две вещи и не понять, какая
  подействовала. Совпадение полученной для базы доли с авторскими 0.43% —
  проверка, что весь конвейер воспроизводит исходную работу.
"""
import json
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import portfolio            # noqa: E402
import refute_exec_engine as xe   # noqa: E402
import refute_exec_lib as L  # noqa: E402
import universe             # noqa: E402

OUT = os.path.join(DIR, "out")

SCHEMES = {
    "база (FINAL_SPEC)": dict(),
    "половина 1.5R + трейл": dict(partial_R=1.5, partial_frac=0.5,
                                  trail_after_partial=1.0),
}


def calib_mc(per_arm, target=0.20, n_sims=4000):
    """Доля риска, при которой 95-й перцентиль бутстрэпной просадки = target."""
    lo, hi = 0.05, 3.0
    for _ in range(14):
        mid = 0.5 * (lo + hi)
        mc = portfolio.monte_carlo_portfolio(per_arm, risk_each=mid,
                                             n_sims=n_sims, seed=0)
        if not mc:
            return mid, {}
        if mc["dd_p95"] > target:
            hi = mid
        else:
            lo = mid
    k = 0.5 * (lo + hi)
    return k, portfolio.monte_carlo_portfolio(per_arm, risk_each=k,
                                              n_sims=n_sims, seed=0)


def main():
    reg, _ = universe.load()
    spec = {}
    print("КАЛИБРОВКА ДОЛИ РИСКА (обучение+проверка, экзамен закрыт)")
    print("Цель: 95-й перцентиль бутстрэпной просадки = 20%%\n")
    for name, kw in SCHEMES.items():
        r = L.ensemble(reg, xe.XCfg(**kw))
        k, mc = calib_mc(r["per_arm"])
        c = portfolio.combine(r["per_arm"], risk_each=k)
        spec[name] = dict(risk_k=k, risk_pct=0.01 * k, mo=c["mo"],
                          maxdd=c["maxdd"], trades=c["trades"],
                          dd_p95=mc.get("dd_p95"), kwargs=kw)
        print("%-24s риск %.2f%%  месяц %+6.2f%%  просадка ист. %5.1f%%  "
              "бутстрэп p95 %5.1f%%"
              % (name, 100 * spec[name]["risk_pct"], 100 * c["mo"],
                 100 * c["maxdd"], 100 * mc.get("dd_p95", 0)))
    print("\nУ автора для базы: риск 0.43%%, месяц +2.88%%, просадка 18.2%%.")
    print("Если строка «база» совпала с этим — конвейер воспроизводит работу.")

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "refute_exec_spec.json"), "w",
              encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, default=float, indent=1)
    print("\nЗАФИКСИРОВАНО -> out/refute_exec_spec.json")
    print("После этого файла экзамен запускается ровно один раз.")


if __name__ == "__main__":
    main()
