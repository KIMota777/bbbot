# -*- coding: utf-8 -*-
"""Демонстрация: явный оракул проходит штатные проверки причинности.

Стратегия donchian, у которой ОСТАВЛЕНЫ только те входы, что окажутся
правильными на следующем баре. Это чистое заглядывание в будущее на один бар.
"""
import numpy as np

import engine
import overlay
import rdata
import universe

REG, _ = universe.load()
BASE = REG["donchian"]
P = dict(n=55, exit_n=20, stop_atr=3.0, atr_n=14, trail_atr=0.0)


def _ok(fn):
    try:
        fn()
        return True
    except AssertionError:
        return False


def oracle(bars):
    s = BASE.build(bars, P)
    e = np.array(s.entry)
    fut = np.zeros(len(e))
    fut[:-1] = bars.c[1:] / bars.c[:-1] - 1.0
    good = ((e > 0) & (fut > 0)) | ((e < 0) & (fut < 0))
    s.entry = np.where(good, e, 0).astype(np.int8)
    return s


def htf_leak(shift):
    """Накладка, читающая 4ч-бар ПО МЕТКЕ НАЧАЛА (то есть ещё не закрытый)."""
    def build(b):
        sig = overlay._clone(BASE.build(b, P))
        hb = rdata.load_bars(b.symbol, "240")
        e = np.sign(np.diff(hb.c, prepend=hb.c[0]))
        idx = np.clip(np.searchsorted(hb.t, b.t, "right") - 1 + shift,
                      0, len(e) - 1)
        d = e[idx]
        sig.entry = np.where(d == sig.entry, sig.entry, 0).astype(np.int8)
        return sig
    return build


def main():
    b = rdata.load_bars("BTCUSDT", "60")
    print("A. engine.assert_causal против оракула «знаю следующий бар»")
    caught = [s for s in range(20)
              if not _ok(lambda: engine.assert_causal(oracle, b, seed=s))]
    print("   штатный cut=0.6, seed 0..19: поймано %d из 20 %s"
          % (len(caught), caught))
    cuts = [x / 100.0 for x in range(25, 96)]
    hit = [c for c in cuts
           if not _ok(lambda c=c: engine.assert_causal(oracle, b, cut=c,
                                                       seed=0))]
    print("   seed=0, срез от 0.25 до 0.95 шагом 0.01: поймано %d из %d %s"
          % (len(hit), len(cuts), hit))

    print("\nB. та же стратегия под мощной проверкой (_audit_power)")
    import _audit_power as AP
    bad, k = AP.probe(oracle, b)
    print("   точек проверки=%d, расхождений=%d -> утечка видна" % (k, len(bad)))

    print("\nC. overlay.assert_causal_external против чтения незакрытого 4ч-бара")
    for s in ("BTCUSDT", "SOLUSDT"):
        for tf in ("60", "240"):
            rdata.load_bars(s, tf)
        rdata.load_funding(s)
        rdata.load_oi(s)
    sol = rdata.load_bars("SOLUSDT", "60")
    for sh in (0, 1, 2, 3, 5, 10):
        try:
            overlay.assert_causal_external(htf_leak(sh), sol, seed=0)
            r = "ПРОПУСТИЛ"
        except AssertionError:
            r = "поймал"
        print("   знание вперёд на %2d старших баров (%3d ч) -> %s"
              % (sh, sh * 4, r))


if __name__ == "__main__":
    main()
