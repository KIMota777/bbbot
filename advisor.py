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

Учёт исходов идёт по той же модели исполнения, что и бэктест-движок
(signal_engine): вход по open 15м-свечи после закрытия сигнального 4ч-бара со
слиппеджем, комиссии тейкер/мейкер, фандинг за каждую 15м-свечу удержания.
Поэтому публичная история сетапа сопоставима с их бэктестом, а не красивее его.

Запуск: python advisor.py
"""

import json
import logging
import os
import tempfile
import time

from pybit.unified_trading import HTTP

import signal_engine as se

POLL = 60
STATE_FILE = os.path.join("webapp", "data", "signals.json")
SETUPS_FILE = "signal_setups.json"
SETUP_CAPITAL0 = 50.0    # стартовый виртуальный капитал каждого сетапа
MARGIN_FRACTION = 0.25   # маржа сигнала = доля текущего капитала сетапа
RR = 3.0                 # тейк = 3R (стоп:тейк 1:3)
M15_MS = 15 * 60 * 1000
BAR4_MS = 4 * 3600 * 1000
DAY_MS = 86400000
MAX_15M = 1000           # предел одного запроса свечей к бирже

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
        self.state = self.load_state()
        # реинвест: виртуальный капитал каждого сетапа (миграция старого файла)
        self.state.setdefault("capital", {})
        for name in self.setups:
            self.state["capital"].setdefault(name, SETUP_CAPITAL0)
        self.last_c4_ts = 0
        self._regime_cache = (0, None)
        self.save()  # чтобы сайт сразу видел капиталы сетапов

    @staticmethod
    def load_state():
        """Читаем состояние так, чтобы битый файл не убивал помощника насмерть.

        Файл пишет только этот процесс, но выключение питания посреди записи
        оставляло обрезанный JSON, и следующий старт падал на json.load, то есть
        помощник молча не работал до ручного вмешательства. Битый файл
        отодвигаем в сторону (для разбора) и начинаем с пустого состояния.
        """
        empty = dict(active={}, history=[], capital={})
        if not os.path.exists(STATE_FILE):
            return empty
        try:
            with open(STATE_FILE, encoding="utf-8") as fh:
                state = json.load(fh)
            if not isinstance(state, dict) or not isinstance(
                    state.get("active", {}), dict):
                raise ValueError("не похоже на состояние помощника")
        except Exception as e:
            bad = "%s.broken-%d" % (STATE_FILE, int(time.time()))
            try:
                os.replace(STATE_FILE, bad)
            except OSError:
                bad = "(переименовать не удалось)"
            log.error("Состояние повреждено (%s). Файл отложен в %s, "
                      "начинаем с чистого листа — история сетапов потеряна.",
                      e, bad)
            return empty
        for key, val in empty.items():
            state.setdefault(key, val)
        return state

    # ---------- данные ----------

    def fetch_4h(self, limit=280):
        r = self.session.get_kline(category="linear", symbol="BTCUSDT",
                                   interval="240", limit=limit)
        rows = r["result"]["list"][::-1][:-1]  # только закрытые
        return [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])]
                for x in rows]

    def fetch_last_15m(self, limit=8):
        limit = max(2, min(MAX_15M, int(limit)))
        r = self.session.get_kline(category="linear", symbol="BTCUSDT",
                                   interval="15", limit=limit)
        rows = r["result"]["list"][::-1]
        return [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])]
                for x in rows]

    def daily_regime(self):
        """0 bull / 1 range / 2 bear по закрытым дневным свечам, кэш на день.

        None = режим неизвестен. Раньше любая ошибка API давала "range" и
        кэшировалась на СУТКИ: одна секундная сетевая неудача на весь день
        разрешала сетапы боковика в любом рынке. Неизвестность теперь не
        кэшируется и ничего не разрешает — только видна в логе.
        """
        day = int(time.time()) // 86400
        if self._regime_cache[0] == day and self._regime_cache[1] is not None:
            return self._regime_cache[1]
        try:
            r = self.session.get_kline(category="linear", symbol="BTCUSDT",
                                       interval="D", limit=140)
            closes = [float(x[4]) for x in r["result"]["list"][::-1][:-1]]
            if len(closes) < 101:
                log.warning("Режим рынка: дневных свечей всего %d (нужно 101 "
                            "на SMA100) — режим НЕИЗВЕСТЕН, сетапы боковика "
                            "не сигналят", len(closes))
                return None
            c = closes[-1]
            sma100 = sum(closes[-100:]) / 100
            chg30 = c / closes[-31] - 1
            regime = (2 if c < sma100 and chg30 < -0.05
                      else 0 if c > sma100 and chg30 > 0.05 else 1)
        except Exception as e:
            log.warning("Режим рынка не получен (%s) — считаем НЕИЗВЕСТНЫМ: "
                        "сетапы боковика не сигналят, повтор на следующем "
                        "4ч-баре", e)
            return None
        self._regime_cache = (day, regime)
        return regime

    def save(self):
        """Атомарная запись: сайт и следующий старт не должны увидеть половину
        файла. Пишем во временный рядом и подменяем одним os.replace."""
        d = os.path.dirname(STATE_FILE)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".signals-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, STATE_FILE)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ---------- сигналы ----------

    def check_new_signals(self, c4):
        """На новом закрытом 4ч-баре гоняем 6 детекторов."""
        ctx = se.prep_context(c4)
        # живой режим берём из дневных свечей (истории 4ч не хватает на SMA100д);
        # -1 = неизвестен: детекторы боковика требуют ровно 1 и промолчат сами
        regime_now = self.daily_regime()
        ctx["regime"] = [-1 if regime_now is None else regime_now] * len(c4)
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
            risk_1r = round(margin * lev * dist, 2)  # номинальный 1R, без издержек
            # честная оценка исходов по модели движка: вход маркетом со
            # слиппеджем, стоп маркетом, тейк лимиткой. Фандинг здесь не учтён
            # (срок удержания заранее неизвестен) — он ещё чуть срежет обе цифры
            fill_est = se.entry_fill(ref_px, sgn)
            qty_est = margin * lev / fill_est
            est_stop = se.trade_pnl(sgn, fill_est, se.exit_fill(stop_px, sgn),
                                    qty_est, True)
            est_tp = se.trade_pnl(sgn, fill_est, tp_px, qty_est, False)
            entry = dict(
                setup=name, side="LONG" if sgn == 1 else "SHORT",
                signal_ts=int(time.time() * 1000), bar_ts=c4[i][0],
                entry=round(ref_px, 1), stop=round(stop_px, 1),
                tp=round(tp_px, 1), stop_pct=round(dist * 100, 2),
                tp_pct=round(se.RR * dist * 100, 2), lev=lev,
                margin=margin, risk_1r=risk_1r,
                capital_at_entry=round(capital, 2),
                hold_days=g["hold_days"],
                est_stop_usd=round(est_stop, 2), est_tp_usd=round(est_tp, 2),
                est_stop_r=round(se.r_multiple(est_stop, margin, lev, dist), 2),
                est_tp_r=round(se.r_multiple(est_tp, margin, lev, dist), 2),
                hold_until=int(time.time() * 1000) + g["hold_days"] * DAY_MS)
            self.state["active"][name] = entry
            st = rec.get("stats", {})
            log.info("=" * 60)
            log.info("СИГНАЛ [%s]", RU_NAMES[name])
            log.info("Вход: ~%s | Стоп: %s (-%.2f%%) | Тейк: %s (+%.2f%%, R:R 1:3)",
                     entry["entry"], entry["stop"], entry["stop_pct"],
                     entry["tp"], entry["tp_pct"])
            log.info("Маржа: $%.2f (25%% капитала сетапа $%.2f, реинвест) | "
                     "Плечо: x%d", margin, capital, lev)
            log.info("С издержками: стоп %+.2f$ (%+.2fR) | тейк %+.2f$ (%+.2fR) "
                     "— номинал 1:%.0f, но комиссии и проскальзывание съедают "
                     "часть тейка и добавляют к стопу",
                     est_stop, entry["est_stop_r"], est_tp, entry["est_tp_r"], RR)
            log.info("Таймаут: %d дн. | Бэктест сетапа: WR %s%%, %s сделок за 3.2г",
                     g["hold_days"], st.get("wr", "?"), st.get("n", "?"))
            log.info("=" * 60)
            self.save()

    def fill_entry(self, name, s, c15):
        """Вход по модели движка: open первой 15м-свечи ПОСЛЕ закрытия
        сигнального 4ч-бара плюс проскальзывание. Раньше учёт брал закрытие
        4ч-бара, то есть цену, по которой войти было уже нельзя."""
        sgn = 1 if s["side"] == "LONG" else -1
        first_ts = s["bar_ts"] + BAR4_MS
        bar = next((b for b in c15 if b[0] >= first_ts), None)
        if bar is None:
            return False                      # свеча входа ещё не началась
        if bar[0] >= first_ts + M15_MS:
            # свечу входа не достали (долгий простой) — вести сделку честно
            # уже нельзя, лучше признать пропуск, чем считать по чужой цене
            self.close_signal(name, s, outcome="ПРОПУЩЕН", pnl=0.0,
                              exit_ts=int(time.time() * 1000), exit_px=None,
                              note="свеча входа потеряна: простой процесса")
            log.warning("[%s] сигнал пропущен: 15м-свеча входа (%d) недоступна",
                        RU_NAMES.get(name, name), first_ts)
            return False
        s["fill_ts"] = bar[0]
        s["fill_px"] = round(se.entry_fill(bar[1], sgn), 2)
        s["qty"] = s["margin"] * s["lev"] / s["fill_px"]
        s["liq_px"] = round(se.liq_price(s["fill_px"], sgn, s["lev"]), 2)
        s["fund_usd"] = 0.0
        s["seen_ts"] = 0
        s["hold_until"] = bar[0] + s.get("hold_days", 6) * DAY_MS
        log.info("[%s] вход исполнен: %.2f (open 15м-свечи + слиппедж; план был "
                 "%.1f) | ликвидация при %.2f",
                 RU_NAMES.get(name, name), s["fill_px"], s["entry"],
                 s["liq_px"])
        return True

    def close_signal(self, name, s, outcome, pnl, exit_ts, exit_px, note=""):
        """Закрываем сигнал и двигаем виртуальный капитал сетапа на ФАКТИЧЕСКИЙ
        результат с издержками (а не на номинальные -1R / +3R)."""
        dist = s["stop_pct"] / 100
        r_mult = round(se.r_multiple(pnl, s["margin"], s["lev"], dist), 2)
        pnl_usd = round(pnl, 2)
        cap_before = self.state["capital"].get(name, SETUP_CAPITAL0)
        self.state["capital"][name] = round(cap_before + pnl_usd, 2)
        rec = dict(s, exit_ts=exit_ts, outcome=outcome, r=r_mult,
                   pnl_usd=pnl_usd, exit_px=exit_px,
                   fees_usd=round(s.get("fund_usd", 0.0), 4),
                   capital_after=self.state["capital"][name],
                   hold_h=round((exit_ts - s.get("fill_ts", s["signal_ts"]))
                                / 3600000, 1))
        if note:
            rec["note"] = note
        self.state["history"].append(rec)
        self.state["active"].pop(name, None)
        log.info("ИСХОД [%s]: %s (%+.2fR = %+.2f$, удержание %.1fч) | "
                 "капитал сетапа: $%.2f%s",
                 RU_NAMES.get(name, name), outcome, r_mult, pnl_usd,
                 rec["hold_h"], rec["capital_after"],
                 " | " + note if note else "")

    def track_active(self):
        """Ведём активные сигналы по 15м-свечам ОТ МОМЕНТА ВХОДА до исхода.

        Раньше смотрели экстремумы последних четырёх свечей: окно захватывало
        бары до входа (стоп «срабатывал» по цене, которой при открытой позиции
        не было), а простой процесса дольше часа молча терял экстремумы. Теперь
        каждая свеча от входа обрабатывается ровно один раз и по порядку — как
        в бэктесте, — а глубина запроса подстраивается под пропущенное время.
        """
        if not self.state["active"]:
            return
        now = int(time.time() * 1000)
        need = 4
        for s in self.state["active"].values():
            base = s.get("seen_ts") or s.get("fill_ts") or \
                s.get("bar_ts", now) + BAR4_MS
            need = max(need, int((now - base) // M15_MS) + 3)
        c15 = self.fetch_last_15m(limit=need)
        if not c15:
            return
        if need > MAX_15M:
            log.warning("Простой дольше %d 15м-свечей: часть истории "
                        "недоступна, исходы могут быть посчитаны по неполным "
                        "данным", MAX_15M)
        changed = False
        for name, s in list(self.state["active"].items()):
            if not s.get("fill_px"):
                if not self.fill_entry(name, s, c15):
                    if name not in self.state["active"]:
                        changed = True   # сигнал закрыт как пропущенный
                    continue
                changed = True
            sgn = 1 if s["side"] == "LONG" else -1
            # только ЗАКРЫТЫЕ свечи от входа, которых ещё не видели
            bars = [b for b in c15
                    if b[0] >= s["fill_ts"] and b[0] > s.get("seen_ts", 0)
                    and b[0] + M15_MS <= now]
            if bars and s.get("seen_ts") and bars[0][0] > s["seen_ts"] + M15_MS:
                log.warning("[%s] пропуск в 15м-истории: %d..%d — исход считаем "
                            "по доступным свечам", RU_NAMES.get(name, name),
                            s["seen_ts"], bars[0][0])
                s["gap_bars"] = (bars[0][0] - s["seen_ts"]) // M15_MS - 1
            for b in bars:
                _, o, h, l, c = b
                # фандинг капает за каждую свечу удержания — как в движке
                s["fund_usd"] = s.get("fund_usd", 0.0) + \
                    se.funding_step(s["qty"], c)
                s["seen_ts"] = b[0]
                changed = True
                hit_liq = (l <= s["liq_px"]) if sgn == 1 else (h >= s["liq_px"])
                hit_stop = (l <= s["stop"]) if sgn == 1 else (h >= s["stop"])
                hit_tp = (h >= s["tp"]) if sgn == 1 else (l <= s["tp"])
                outcome = exit_px = None
                if hit_liq and (not hit_stop
                                or (sgn == 1 and s["liq_px"] >= s["stop"])
                                or (sgn == -1 and s["liq_px"] <= s["stop"])):
                    outcome, exit_px = "ЛИКВИДАЦИЯ", s["liq_px"]
                    pnl = se.liq_pnl(s["margin"], s["qty"], s["fill_px"],
                                     s["fund_usd"])
                elif hit_stop:  # стоп и тейк в одной свече -> консервативно стоп
                    outcome = "СТОП"
                    exit_px = se.exit_fill(s["stop"], sgn)
                    pnl = se.trade_pnl(sgn, s["fill_px"], exit_px, s["qty"],
                                       True, s["fund_usd"])
                elif hit_tp:
                    outcome = "ТЕЙК"       # тейк стоит лимиткой — комиссия мейкера
                    exit_px = s["tp"]
                    pnl = se.trade_pnl(sgn, s["fill_px"], exit_px, s["qty"],
                                       False, s["fund_usd"])
                elif b[0] >= s["hold_until"]:
                    outcome = "ТАЙМАУТ"
                    exit_px = se.exit_fill(c, sgn)
                    pnl = se.trade_pnl(sgn, s["fill_px"], exit_px, s["qty"],
                                       True, s["fund_usd"])
                if outcome:
                    s["fund_usd"] = round(s["fund_usd"], 6)
                    self.close_signal(name, s, outcome, pnl,
                                      exit_ts=b[0] + M15_MS,
                                      exit_px=round(exit_px, 2))
                    break
        if changed:
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
