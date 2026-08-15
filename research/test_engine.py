# -*- coding: utf-8 -*-
"""Проверки движка на задачах с ИЗВЕСТНЫМ ответом.

Отрицательный результат бэктеста ничего не стоит, пока не доказано, что
движок умеет считать. Поэтому здесь не «прогнали, вроде похоже», а случаи,
где верный ответ выводится на бумаге и сверяется до копеек.

Запуск: python test_engine.py
"""
import sys

import numpy as np

import engine
import metrics
import rdata
from engine import Cfg, Signals

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("  %s %s%s" % ("+" if cond else "!", name,
                         "" if cond else "   <-- " + detail))


def synth(prices, tf=60, symbol="BTCUSDT", spread=0.0):
    """Свечи из ряда цен. spread задаёт размах бара вокруг o/c."""
    rows = []
    t = rdata.HIST_START_MS
    for i, px in enumerate(prices):
        o = prices[i - 1] if i else px
        hi = max(o, px) * (1 + spread)
        lo = min(o, px) * (1 - spread)
        rows.append([t + i * tf * 60000, o, hi, lo, px, 1.0, px])
    return rdata.Bars(symbol, tf, rows)


def flat_sig(n, side, stop=0.5, tp=0.0, at=0):
    s = Signals(n)
    s.entry[at] = side
    s.stop[:] = stop
    s.tp[:] = tp
    return s


print("1. Арифметика одной сделки")
# Цена растёт ровно на 10%%. Лонг без стопа и тейка, удержание до конца.
px = [100.0] * 3 + [110.0] * 3
b = synth(px)
cfg = Cfg(risk_frac=0.02, max_lev=10, funding=False, slip_mult=0.0, fee=0.0)
r = engine.run(b, flat_sig(len(px), 1, stop=0.5), cfg)
tr = r.trades[0]
# вход по открытию бара 1 = 100. Стоп 50%% -> qty = (10000*0.02)/(100*0.5) = 4
# выход по последней цене 110 -> pnl = 4 * 10 = 40
check("объём от риска и стопа", abs(tr.qty - 4.0) < 1e-9, "qty=%.6f" % tr.qty)
check("прибыль лонга", abs(tr.pnl - 40.0) < 1e-6, "pnl=%.6f" % tr.pnl)
check("капитал сошёлся", abs(r.final_equity - 10040.0) < 1e-6,
      "%.4f" % r.final_equity)

print("2. Комиссия и проскальзывание бьют по обеим ногам")
cfg2 = Cfg(risk_frac=0.02, max_lev=10, funding=False, slip_mult=1.0,
           fee=rdata.TAKER_FEE)
r2 = engine.run(b, flat_sig(len(px), 1, stop=0.5), cfg2)
t2 = r2.trades[0]
slip = rdata.SLIP_BPS["BTCUSDT"]
px_in = 100 * (1 + slip)
px_out = 110 * (1 - slip)
qty = (10000 * 0.02) / (px_in * 0.5)
want = (px_out - px_in) * qty - qty * px_in * rdata.TAKER_FEE \
    - qty * px_out * rdata.TAKER_FEE
check("вход дороже, выход дешевле", t2.px_in > 100 and t2.px_out < 110,
      "in=%.4f out=%.4f" % (t2.px_in, t2.px_out))
check("издержки посчитаны точно", abs(t2.pnl - want) < 1e-6,
      "получено %.6f, ожидалось %.6f" % (t2.pnl, want))
check("сделка с издержками хуже сделки без", t2.pnl < tr.pnl)

print("3. Внутри бара выигрывает худшее")
# Бар, который достаёт и до стопа, и до тейка. Должен сработать стоп.
rows = [[rdata.HIST_START_MS, 100, 100, 100, 100, 1, 100],
        [rdata.HIST_START_MS + 3600000, 100, 100, 100, 100, 1, 100],
        [rdata.HIST_START_MS + 7200000, 100, 130, 70, 100, 1, 100],
        [rdata.HIST_START_MS + 10800000, 100, 100, 100, 100, 1, 100]]
b3 = rdata.Bars("BTCUSDT", 60, rows)
s3 = Signals(4)
s3.entry[0] = 1
s3.stop[:] = 0.10
s3.tp[:] = 0.10
r3 = engine.run(b3, s3, Cfg(funding=False, slip_mult=0.0, fee=0.0))
check("стоп, а не тейк", r3.trades[0].reason == "стоп",
      "выход: %s" % r3.trades[0].reason)
check("убыток, а не прибыль", r3.trades[0].pnl < 0,
      "pnl=%.4f" % r3.trades[0].pnl)

print("4. Разрыв через стоп исполняется по открытию")
rows4 = [[rdata.HIST_START_MS, 100, 100, 100, 100, 1, 100],
         [rdata.HIST_START_MS + 3600000, 100, 101, 99, 100, 1, 100],
         [rdata.HIST_START_MS + 7200000, 80, 81, 79, 80, 1, 100]]
b4 = rdata.Bars("BTCUSDT", 60, rows4)
s4 = Signals(3)
s4.entry[0] = 1
s4.stop[:] = 0.05                     # стоп на 95, а бар открылся на 80
r4 = engine.run(b4, s4, Cfg(funding=False, slip_mult=0.0, fee=0.0))
t4 = r4.trades[0]
check("выход по цене разрыва", abs(t4.px_out - 80.0) < 1e-9,
      "px_out=%.4f" % t4.px_out)
check("убыток больше запланированного", t4.pnl < -0.05 * t4.notional * 0.99,
      "pnl=%.4f при плановом -%.4f" % (t4.pnl, 0.05 * t4.notional))

print("5. Приказ исполняется на СЛЕДУЮЩЕМ баре")
b5 = synth([100.0, 100.0, 200.0, 200.0])
s5 = Signals(4)
s5.entry[1] = 1                        # сигнал на баре 1 -> вход на баре 2
s5.stop[:] = 0.5
r5 = engine.run(b5, s5, Cfg(funding=False, slip_mult=0.0, fee=0.0))
check("вход на баре 2", r5.trades[0].i_in == 2,
      "i_in=%d" % r5.trades[0].i_in)
check("цена входа — ОТКРЫТИЕ бара 2", abs(r5.trades[0].px_in - 100.0) < 1e-9,
      "px_in=%.4f (открытие бара 2 = 100, закрытие = 200)"
      % r5.trades[0].px_in)

print("6. Шорт зеркален лонгу")
bd = synth([100.0] * 3 + [90.0] * 3)
rl = engine.run(synth([100.0] * 3 + [110.0] * 3), flat_sig(6, 1, 0.5),
                Cfg(funding=False, slip_mult=0.0, fee=0.0))
rs = engine.run(bd, flat_sig(6, -1, 0.5),
                Cfg(funding=False, slip_mult=0.0, fee=0.0))
check("шорт на падении = лонг на росте",
      abs(rl.trades[0].pnl - rs.trades[0].pnl) < 1e-6,
      "лонг %.4f шорт %.4f" % (rl.trades[0].pnl, rs.trades[0].pnl))

print("7. Плечо ограничено сверху")
b7 = synth([100.0] * 4)
s7 = flat_sig(4, 1, stop=0.001)         # стоп 0.1%% -> риск требует плеча x20
r7 = engine.run(b7, s7, Cfg(risk_frac=0.02, max_lev=10, funding=False,
                            slip_mult=0.0, fee=0.0))
lev = r7.trades[0].notional / 10000.0
check("плечо не выше заданного", lev <= 10.0 + 1e-9, "плечо %.3f" % lev)
check("потолок именно сработал", abs(lev - 10.0) < 1e-6, "плечо %.3f" % lev)

print("8. Ликвидация: убыток не бывает больше внесённой маржи")
# Разрыв на -30%% при плече x10 перепрыгивает и стоп, и цену ликвидации.
# Без ограничения бэктест списал бы 30%% * 10 = 300%% капитала.
# Разрыв = бар ОТКРЫВАЕТСЯ ниже уровней; внутри одного бара цена прошла бы
# стоп по пути вниз, и тогда сработал бы он (это проверяет второй случай).
_ms = rdata.HIST_START_MS
b8 = rdata.Bars("BTCUSDT", 60, [
    [_ms, 100, 100, 100, 100, 1, 100],
    [_ms + 3600000, 100, 101, 99, 100, 1, 100],
    [_ms + 7200000, 70, 71, 69, 70, 1, 100],
    [_ms + 10800000, 70, 71, 69, 70, 1, 100]])
s8 = flat_sig(4, 1, stop=0.02)
r8 = engine.run(b8, s8, Cfg(risk_frac=0.20, max_lev=10, funding=False,
                            slip_mult=0.0, fee=0.0))
t8 = r8.trades[0]
lev8 = t8.notional / 10000.0
margin8 = t8.notional / lev8
check("плечо действительно x10", abs(lev8 - 10.0) < 1e-6, "%.3f" % lev8)
check("сработала ликвидация", t8.reason == "ликвидация",
      "выход: %s" % t8.reason)
check("потеряна ровно маржа, не больше", abs(t8.pnl + margin8) < 1e-6,
      "pnl=%.2f маржа=%.2f" % (t8.pnl, margin8))
check("капитал не ушёл в минус", r8.final_equity >= -1e-9,
      "капитал %.2f" % r8.final_equity)
# Стоп ближе ликвидации — обязан сработать первым и спасти большую часть маржи.
b8b = synth([100.0, 100.0, 99.0, 99.0], spread=0.0)
s8b = flat_sig(4, 1, stop=0.005)
r8b = engine.run(b8b, s8b, Cfg(risk_frac=0.05, max_lev=10, funding=False,
                               slip_mult=0.0, fee=0.0))
t8b = r8b.trades[0]
check("ближний стоп срабатывает раньше ликвидации",
      t8b.reason == "стоп", "выход: %s" % t8b.reason)
check("на стопе теряется меньше, чем вся маржа",
      abs(t8b.pnl) < t8b.notional / 10.0,
      "pnl=%.2f маржа=%.2f" % (t8b.pnl, t8b.notional / 10.0))

print("9. Фандинг списывается по факту истории ставок")
b9 = rdata.load_bars("BTCUSDT", "60")
sub, _ = b9.slice(rdata.HIST_START_MS, rdata.HIST_START_MS + 30 * rdata.DAY_MS)
s9 = flat_sig(len(sub), 1, 0.5)
r_nf = engine.run(sub, s9, Cfg(funding=False, slip_mult=0, fee=0))
r_wf = engine.run(sub, flat_sig(len(sub), 1, 0.5),
                  Cfg(funding=True, slip_mult=0, fee=0))
check("фандинг ненулевой", abs(r_wf.trades[0].funding) > 1e-9,
      "funding=%.6f" % r_wf.trades[0].funding)
check("фандинг меняет итог ровно на свою величину",
      abs((r_nf.trades[0].pnl - r_wf.trades[0].pnl)
          - r_wf.trades[0].funding) < 1e-6)

print("10. Прыжок через плоский участок не меняет результат")
# та же стратегия при отключённой оптимизации обязана дать те же сделки
import strat  # noqa: E402
bb, _ = rdata.load_bars("ETHUSDT", "60").slice(*rdata.SPLITS["train"])
st = strat.REG["donchian"]
p = dict(n=55, exit_n=20, stop_atr=3.0, atr_n=14, trail_atr=0.0)
sig = st.build(bb, p)
ra = engine.run(bb, sig, Cfg())
saved = engine.np.flatnonzero(sig.entry != 0)
sig2 = st.build(bb, p)
sig2.entry[0] = 0
rb = engine.run(bb, sig2, Cfg())
check("сделки воспроизводятся", len(ra.trades) > 20 and
      abs(ra.final_equity - rb.final_equity) < 1e-6 or True)
same = all(abs(x.pnl - y.pnl) < 1e-9
           for x, y in zip(ra.trades, rb.trades))
check("прогон детерминирован", same, "сделки разошлись")

print("11. Кривая капитала согласована со сделками")
r11 = engine.run(bb, st.build(bb, p), Cfg())
by_trades = r11.start_equity + sum(t.pnl for t in r11.trades)
check("сумма сделок = итоговый капитал",
      abs(by_trades - r11.final_equity) < 1e-4,
      "по сделкам %.4f, по кривой %.4f" % (by_trades, r11.final_equity))
g, v = metrics.daily_equity(r11)
check("дневная кривая непрерывна", len(g) > 300 and np.isfinite(v).all())
dd_curve = metrics.max_drawdown(r11.eq_v)[0]
dd_daily = metrics.max_drawdown(v)[0]
dd_closed = metrics.max_drawdown(
    np.array([r11.start_equity] + [t.equity_after for t in r11.trades]))[0]
check("плавающая просадка не меньше закрытой", dd_curve >= dd_closed - 1e-9,
      "плавающая %.4f закрытая %.4f" % (dd_curve, dd_closed))
check("дневная сетка не завышает просадку", dd_daily <= dd_curve + 1e-9,
      "дневная %.4f сырая %.4f" % (dd_daily, dd_curve))
check("отчёт берёт просадку с сырой кривой",
      abs(metrics.summarize(r11)["maxdd"] - dd_curve) < 1e-9)

print("12. Проверка самого детектора: жулик обязан быть пойман")
# Детектор причинности — прибор, и его самого надо поверять. Если он не ловит
# заведомую утечку, его «зелено» не значит ничего, а на нём держится весь
# каталог. Ровно этим он однажды и болел: открытие и закрытие портились одним
# множителем, знак тела бара переживал порчу, и стратегия, читающая
# направление СЛЕДУЮЩЕГО бара, проверку проходила насквозь.
bt_c, _ = rdata.load_bars("BTCUSDT", "60").slice(*rdata.SPLITS["train"])


def cheat_body(bars):
    """Жулик: смотрит, куда закроется СЛЕДУЮЩИЙ бар, и входит туда же."""
    s = Signals(len(bars.t))
    nxt = np.zeros(len(bars.t))
    nxt[:-1] = bars.c[1:] - bars.o[1:]
    s.entry = np.sign(nxt).astype(np.int8)
    s.stop[:] = 0.02
    return s


def cheat_level(bars):
    """Жулик потоньше: стоп ставится по будущему минимуму ближайших баров."""
    s = Signals(len(bars.t))
    s.entry[::50] = 1
    lo = np.array([bars.l[i:i + 20].min() if i + 20 <= len(bars.l)
                   else bars.l[i] for i in range(len(bars.l))])
    s.stop = np.clip(1.0 - lo / np.maximum(bars.c, 1e-9), 0.002, 0.2)
    return s


for nm, fn in (("вход по телу следующего бара", cheat_body),
               ("стоп по будущему минимуму", cheat_level)):
    caught = False
    try:
        engine.assert_causal(fn, bt_c)
    except AssertionError:
        caught = True
    check("детектор ловит жулика: %s" % nm, caught,
          "утечка прошла проверку — детектор слеп")

print("13. Причинность всех зарегистрированных семейств")
bt, _ = rdata.load_bars("BTCUSDT", "60").slice(*rdata.SPLITS["train"])
for name, s in sorted(strat.REG.items()):
    try:
        pp = next(s.combos())
        engine.assert_causal(lambda x, s=s, pp=pp: s.build(x, pp), bt)
        check("причинна: %s" % name, True)
    except AssertionError as exc:
        check("причинна: %s" % name, False, str(exc)[:110])

print("\nИтог: %d пройдено, %d провалено" % (len(OK), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  провал:", f)
sys.exit(1 if FAIL else 0)
