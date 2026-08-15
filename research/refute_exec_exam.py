# -*- coding: utf-8 -*-
"""ЭКЗАМЕН. Один прогон принятого решения. Запускается последним.

Решение зафиксировано в refute_exec_spec.py ДО этого файла и после него не
меняется. Экзаменационная выборка здесь читается впервые и только для того,
чтобы посчитать итог — не для выбора чего бы то ни было.

Считается ДВЕ вещи одним и тем же конвейером:
  * база (маркет + ATR-стоп) — то, что автор вынес на экзамен;
  * решение (половина на 1.5R + трейл по остатку).
Сравнение между ними внутреннее, поэтому оно не зависит от того, какой кусок
экзамена покрыт окнами отбора.

ПОКРЫТИЕ ЭКЗАМЕНА. Окна проверки вперёд нарезаются шагом 90 дней, и последнее
окно, целиком помещающееся в историю, кончается раньше конца экзамена. Поэтому
считаются оба варианта: как у автора (часть экзамена остаётся без сделок) и с
продлённой сеткой окон, покрывающей все 169 дней. Продление НЕ добавляет
знания о будущем: параметры каждого окна по-прежнему выбираются по данным
строго раньше него.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import portfolio            # noqa: E402
import rdata                # noqa: E402
import refute_exec_engine as xe   # noqa: E402
import refute_exec_lib as L  # noqa: E402
import universe             # noqa: E402

OUT = os.path.join(DIR, "out")
SPEC = os.path.join(OUT, "refute_exec_spec.json")


def window(per_arm, t0, t1):
    out = {}
    for k, trs in per_arm.items():
        sel = [t for t in trs if t0 <= t["t_in"] < t1]
        if sel:
            out[k] = sel
    return out


def describe(tag, trades, k):
    if not trades:
        print("%-30s сделок нет" % tag)
        return None
    c = portfolio.combine(trades, risk_each=k)
    mo = portfolio.monthly_from_curve(c["times"], c["curve"])
    mv = np.array([m[1] for m in mo]) if mo else np.array([])
    print("%-30s итог %+8.2f%% | месяц %+6.2f%% | просадка %5.1f%% | "
          "сделок %4d | дней %3.0f"
          % (tag, 100 * c["ret"], 100 * c["mo"], 100 * c["maxdd"],
             c["trades"], c["days"]))
    return dict(ret=c["ret"], mo=c["mo"], maxdd=c["maxdd"],
                trades=c["trades"], days=c["days"],
                mo_med=float(np.median(mv)) if len(mv) else 0.0,
                months=[[list(a), float(b)] for a, b in mo])


def main():
    with open(SPEC, encoding="utf-8") as fh:
        spec = json.load(fh)
    reg, _ = universe.load()
    te0, te1 = rdata.SPLITS["test"]
    tv0, tv1 = rdata.SPLITS["trainval"]
    print("=" * 78)
    print("ЭКЗАМЕН НАПРАВЛЕНИЯ «ДЕЛО В ИСПОЛНЕНИИ». Один прогон, без правок.")
    print("=" * 78)
    print("Экзамен: %.0f дней, до этой минуты не читался.\n"
          % ((te1 - te0) / rdata.DAY_MS))

    res = {}
    for cover, t_end in (("как у автора", rdata.HIST_END_MS + 1),
                         ("продлённая сетка окон",
                          rdata.HIST_END_MS + 1 + 90 * rdata.DAY_MS)):
        print("--- покрытие окон: %s" % cover)
        res[cover] = {}
        for name, s in spec.items():
            r = L.ensemble(reg, xe.XCfg(**s["kwargs"]),
                           t0=rdata.HIST_START_MS, t1=t_end, allow_test=True)
            k = s["risk_k"]
            a = describe("%-22s обуч+пров" % name,
                         window(r["per_arm"], tv0, tv1), k)
            b = describe("%-22s ЭКЗАМЕН" % name,
                         window(r["per_arm"], te0, te1), k)
            res[cover][name] = dict(risk_k=k, trainval=a, test=b)
        print("")

    print("ГЛАВНОЕ СРАВНЕНИЕ: помогло ли исполнение на экзамене")
    for cover in res:
        a = res[cover].get("база (FINAL_SPEC)", {}).get("test")
        b = res[cover].get("половина 1.5R + трейл", {}).get("test")
        if a and b:
            print("   %-24s база %+6.2f%%/мес (просадка %4.1f%%) -> "
                  "решение %+6.2f%%/мес (просадка %4.1f%%)"
                  % (cover, 100 * a["mo"], 100 * a["maxdd"],
                     100 * b["mo"], 100 * b["maxdd"]))

    with open(os.path.join(OUT, "refute_exec_exam.json"), "w",
              encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, default=float, indent=1)
    print("\nсохранено -> out/refute_exec_exam.json")
    print("Экзамен состоялся. Конфигурация после этого не правится.")


if __name__ == "__main__":
    main()
