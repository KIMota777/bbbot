# -*- coding: utf-8 -*-
"""Сигнальный помощник BTC: НЕ торгует сам — говорит, где заходить.

Каждую минуту проверяет 6 среднесрочных сетапов (параметры — победители
генетики v9 из signal_setups.json). Когда сетап срабатывает, печатает и
пишет в лог конкретный план сделки:

    СИГНАЛ [Ложный пробой низа: лонг]
    Вход:  ~63 250   Стоп: 61 900 (-2.1%)   Тейк: 67 300 (+6.4%, R:R 1:3)
    Плечо: x15   Таймаут: 6 дней   (сетап в бэктесте: WR 34%, +87% за 3.2г)

Активные сигналы ведутся до исхода (стоп/тейк/таймаут) и складываются в
историю. Состояние — webapp/data/signals.json, его же показывает сайт
(страница /signals).

Запуск: python advisor.py
"""

import json
import logging
import os
import time

from pybit.unified_trading import HTTP

import signal_engine as se

POLL = 60
STATE_FILE = os.path.join("webapp", "data", "signals.json")
SETUPS_FILE = "signal_setups.json"
SETUP_CAPITAL0 = 50.0    # стартовый виртуальный капитал каждого сетапа
MARGIN_FRACTION = 0.25   # маржа сигнала = доля текущего капитала сетапа
RR = 3.0                 # тейк = 3R (стоп:тейк 1:3)

RU_NAMES = {
    "range_long": "Боковик: лонг от нижней границы",
    "range_short": "Боковик: шорт от верхней границы",
    "sweep_long": "Ложный пробой низа (сбор ликвидности): лонг",
    "sweep_short": "Ложный пробой верха (сбор ликвидности): шорт",
    "dump_long": "Капитуляция: лонг после сильного падения",
    "pump_short": "Перегрев: шорт после вертикального роста",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(),
              logging.FileHandler("advisor.log", encoding="utf-8")])
log = logging.getLogger("advisor")


class Advisor:
    def __init__(self):
        with open(SETUPS_FILE, encoding="utf-8") as fh:
            self.setups = json.load(fh)
        self.session = HTTP(testnet=False)
        self.state = dict(active={}, history=[], capital={})
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, encoding="utf-8") as fh:
                self.state = json.load(fh)
        # реинвест: виртуальный капитал каждого сетапа (миграция старого файла)
        self.state.setdefault("capital", {})
        for name in self.setups:
            self.state["capital"].setdefault(name, SETUP_CAPITAL0)
        self.last_c4_ts = 0
        self._regime_cache = (0, 1)
        self.save()  # чтобы сайт сразу видел капиталы сетапов

    # ---------- данные ----------

    def fetch_4h(self, limit=280):
        r = self.session.get_kline(category="linear", symbol="BTCUSDT",
                                   interval="240", limit=limit)
        rows = r["result"]["list"][::-1][:-1]  # только закрытые
        return [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])]
                for x in rows]

    def fetch_last_15m(self, limit=8):
        r = self.session.get_kline(category="linear", symbol="BTCUSDT",
                                   interval="15", limit=limit)
        rows = r["result"]["list"][::-1]
        return [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])]
                for x in rows]

    def daily_regime(self):
        """0 bull / 1 range / 2 bear по закрытым дневным свечам, кэш на день."""
        day = int(time.time()) // 86400
        if self._regime_cache[0] == day:
            return self._regime_cache[1]
        try:
            r = self.session.get_kline(category="linear", symbol="BTCUSDT",
                                       interval="D", limit=140)
            closes = [float(x[4]) for x in r["result"]["list"][::-1][:-1]]
            if len(closes) < 101:
                regime = 1
            else:
                c = closes[-1]
                sma100 = sum(closes[-100:]) / 100
                chg30 = c / closes[-31] - 1
                regime = (2 if c < sma100 and chg30 < -0.05
                          else 0 if c > sma100 and chg30 > 0.05 else 1)
        except Exception:
            regime = 1
        self._regime_cache = (day, regime)
        return regime

    def save(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False, indent=2)

    # ---------- сигналы ----------

    def check_new_signals(self, c4):
        """На новом закрытом 4ч-баре гоняем 6 детекторов."""
        ctx = se.prep_context(c4)
        # живой режим берём из дневных свечей (истории 4ч не хватает на SMA100д)
        regime_now = self.daily_regime()
        ctx["regime"] = [regime_now] * len(c4)
        i = len(c4) - 1
        for name, rec in self.setups.items():
            if not rec.get("enabled", True):
                continue  # сетап провалил walk-forward — не советуем
            if name in self.state["active"]:
                continue
            g = rec["genome"]
            cool_ms = g["cooldown"] * 4 * 3600 * 1000
            last_exit = max((h["exit_ts"] for h in self.state["history"]
                             if h["setup"] == name), default=0)
            if c4[i][0] < last_exit + cool_ms:
                continue
            ext = se.rolling_extremes_lagged(
                c4, g["window"], g["age"] if name.startswith("sweep") else 1)
            sig = se.detect(name, i, c4, ctx, g, ext)
            if not sig:
                continue
            ref_px, stop_px = sig
            sgn = 1 if name.endswith("long") else -1
            dist = sgn * (ref_px - stop_px) / ref_px
            if dist < se.MIN_STOP or dist > g["stop_cap"]:
                continue
            tp_px = ref_px * (1 + sgn * se.RR * dist)
            lev = rec.get("rec_lev", 15)
            capital = self.state["capital"].get(name, SETUP_CAPITAL0)
            margin = round(capital * MARGIN_FRACTION, 2)
            risk_1r = round(margin * lev * dist, 2)  # убыток при стопе
            entry = dict(
                setup=name, side="LONG" if sgn == 1 else "SHORT",
                signal_ts=int(time.time() * 1000), bar_ts=c4[i][0],
                entry=round(ref_px, 1), stop=round(stop_px, 1),
                tp=round(tp_px, 1), stop_pct=round(dist * 100, 2),
                tp_pct=round(se.RR * dist * 100, 2), lev=lev,
                margin=margin, risk_1r=risk_1r,
                capital_at_entry=round(capital, 2),
                hold_until=int(time.time() * 1000) + g["hold_days"] * 86400000)
            self.state["active"][name] = entry
            st = rec.get("stats", {})
            log.info("=" * 60)
            log.info("СИГНАЛ [%s]", RU_NAMES[name])
            log.info("Вход: ~%s | Стоп: %s (-%.2f%%) | Тейк: %s (+%.2f%%, R:R 1:3)",
                     entry["entry"], entry["stop"], entry["stop_pct"],
                     entry["tp"], entry["tp_pct"])
            log.info("Маржа: $%.2f (25%% капитала сетапа $%.2f, реинвест) | "
                     "Плечо: x%d", margin, capital, lev)
            log.info("Риск: -$%.2f при стопе | Цель: +$%.2f при тейке (3R)",
                     risk_1r, risk_1r * RR)
            log.info("Таймаут: %d дн. | Бэктест сетапа: WR %s%%, %s сделок за 3.2г",
                     g["hold_days"], st.get("wr", "?"), st.get("n", "?"))
            log.info("=" * 60)
            self.save()

    def track_active(self):
        """Ведём активные сигналы по последним 15м-свечам до исхода."""
        if not self.state["active"]:
            return
        c15 = self.fetch_last_15m()
        if not c15:
            return
        last = c15[-1]
        now = int(time.time() * 1000)
        done = []
        for name, s in self.state["active"].items():
            sgn = 1 if s["side"] == "LONG" else -1
            hi = max(x[2] for x in c15[-4:])
            lo = min(x[3] for x in c15[-4:])
            outcome = None
            if (lo <= s["stop"]) if sgn == 1 else (hi >= s["stop"]):
                outcome, r_mult = "СТОП", -1.0
            elif (hi >= s["tp"]) if sgn == 1 else (lo <= s["tp"]):
                outcome, r_mult = "ТЕЙК", se.RR
            elif now > s["hold_until"]:
                px = last[4]
                move = sgn * (px - s["entry"]) / s["entry"]
                r_mult = round(move / (s["stop_pct"] / 100), 2)
                outcome = "ТАЙМАУТ"
            if outcome:
                risk = s.get("risk_1r", 0)
                pnl_usd = round(r_mult * risk, 2)
                cap_before = self.state["capital"].get(name, SETUP_CAPITAL0)
                self.state["capital"][name] = round(cap_before + pnl_usd, 2)
                rec = dict(s, exit_ts=now, outcome=outcome, r=r_mult,
                           pnl_usd=pnl_usd,
                           capital_after=self.state["capital"][name],
                           hold_h=round((now - s["signal_ts"]) / 3600000, 1))
                self.state["history"].append(rec)
                done.append(name)
                log.info("ИСХОД [%s]: %s (%+.1fR = %+.2f$, удержание %.1fч) | "
                         "капитал сетапа: $%.2f",
                         RU_NAMES[name], outcome, r_mult, pnl_usd,
                         rec["hold_h"], rec["capital_after"])
        for name in done:
            del self.state["active"][name]
        if done:
            self.save()

    def run(self):
        log.info("Помощник запущен: 6 сетапов BTC, сигналы на закрытии 4ч-бара. "
                 "Активных: %d", len(self.state["active"]))
        while True:
            try:
                self.track_active()
                c4 = self.fetch_4h()
                if c4 and c4[-1][0] != self.last_c4_ts:
                    self.last_c4_ts = c4[-1][0]
                    self.check_new_signals(c4)
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("Ошибка цикла")
            time.sleep(POLL)


if __name__ == "__main__":
    try:
        Advisor().run()
    except KeyboardInterrupt:
        log.info("Помощник остановлен.")
