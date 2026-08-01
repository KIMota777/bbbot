# -*- coding: utf-8 -*-
"""Сигнальный помощник BTC: НЕ торгует сам — говорит, где заходить.

Следит за сетапами движка v2 (signal_engine2) на ДВУХ таймфреймах:
конфиги "<сетап>@240" проверяются на закрытии каждого 4ч-бара,
"<сетап>@60" — на закрытии каждого часового. Старые ключи без @ означают
@240 (обратная совместимость). Когда сетап срабатывает, печатает и пишет
в лог план сделки:

    СИГНАЛ [Ложный пробой низа: лонг · 4ч]
    Вход:  ~63 250   Стоп: 61 900 (-2.1%)   Тейк: 67 300 (+6.4%, R:R 1:3)
    Плечо: x15   Таймаут: 6 дней   (честный экзамен: WR 34%, +0.23R/сделку)

Активные сигналы ведутся до исхода (стоп/тейк/таймаут) по 15м-свечам и
складываются в историю. Состояние — webapp/data/signals.json (ключи
active/history/capital — по полному ключу сетапа с @), его же показывает
сайт (страница /signals).

Запуск: python advisor.py
"""

import json
import logging
import os
import time

from pybit.unified_trading import HTTP

import signal_engine2 as se

POLL = 60
STATE_FILE = os.path.join("webapp", "data", "signals.json")
SETUPS_FILE = "signal_setups2.json"
SETUP_CAPITAL0 = 50.0    # стартовый виртуальный капитал каждого сетапа
MARGIN_FRACTION = 0.25   # маржа сигнала = доля текущего капитала сетапа
RR = 3.0                 # тейк = 3R (стоп:тейк 1:3)

# сколько закрытых баров тянуть с биржи: хватает на window<=200,
# ma_len<=400, aroon_n<=50, EMA200 и лаг age
BARS_FETCH = {240: 520, 60: 640}

RU_NAMES = {
    "sweep_long": "Ложный пробой низа (сбор ликвидности): лонг",
    "dump_long": "Капитуляция: лонг после сильного падения",
    "bounce_short": "Нож -> откат: шорт отскока после обвала",
    "rally_short": "Тренд-шорт: продажа отскока по тренду",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(),
              logging.FileHandler("advisor.log", encoding="utf-8")])
log = logging.getLogger("advisor")


def split_key(key, rec=None):
    """"sweep_long@60" -> ("sweep_long", 60); ключ без @ -> interval_min
    конфига, иначе 240 (старый формат)."""
    if "@" in key:
        base, _, tf = key.rpartition("@")
        if tf.isdigit():
            return base, int(tf)
    iv = 240
    if isinstance(rec, dict):
        try:
            iv = int(rec.get("interval_min") or 240)
        except (TypeError, ValueError):
            iv = 240
    return key, iv


def tf_label(iv):
    return "4ч" if iv == 240 else ("1ч" if iv == 60 else f"{iv}м")


class Advisor:
    def __init__(self):
        with open(SETUPS_FILE, encoding="utf-8") as fh:
            raw = json.load(fh)
        # нормализация: полный ключ "<сетап>@<ТФ>"; legacy-сетапы движка
        # (не в se.SETUPS) не отслеживаем — владелец их убрал
        self.setups = {}
        for key, rec in raw.items():
            if not isinstance(rec, dict):
                continue
            base, iv = split_key(key, rec)
            if base not in se.SETUPS:
                continue
            self.setups[f"{base}@{iv}"] = dict(rec, interval_min=iv)
        self.session = HTTP(testnet=False)
        self.state = dict(active={}, history=[], capital={})
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, encoding="utf-8") as fh:
                self.state = json.load(fh)
        self._migrate_state_keys()
        # реинвест: виртуальный капитал каждого сетапа
        self.state.setdefault("capital", {})
        for key in self.setups:
            self.state["capital"].setdefault(key, SETUP_CAPITAL0)
        # какие ТФ реально нужны (по watch/enabled конфигам)
        self.tfs = sorted(
            {split_key(k)[1] for k, r in self.setups.items()
             if r.get("enabled", False) or r.get("watch", False)},
            reverse=True)
        self.last_ts = {iv: 0 for iv in self.tfs}
        self._regime_cache = (0, 1)
        self.save()  # чтобы сайт сразу видел капиталы сетапов

    def _migrate_state_keys(self):
        """Старый signals.json (ключи без @) -> полные ключи с @240.

        Переименовываем только то, что стало действующим конфигом; чужие
        (legacy) ключи не трогаем — история не портится."""
        for field in ("active", "capital"):
            d = self.state.get(field)
            if not isinstance(d, dict):
                continue
            for old in [k for k in d if "@" not in k]:
                new = f"{old}@240"
                if new in self.setups and new not in d:
                    d[new] = d.pop(old)
        for h in self.state.get("history", []):
            s = h.get("setup")
            if isinstance(s, str) and "@" not in s and f"{s}@240" in self.setups:
                h["setup"] = f"{s}@240"

    # ---------- имена ----------

    def title(self, key):
        base, iv = split_key(key, self.setups.get(key))
        name = (self.setups.get(key, {}).get("title")
                or RU_NAMES.get(base, base))
        return f"{name} · {tf_label(iv)}"

    # ---------- данные ----------

    def fetch_bars(self, iv):
        """Закрытые бары ТФ iv (мин) с биржи, старые -> новые."""
        r = self.session.get_kline(category="linear", symbol="BTCUSDT",
                                   interval=str(iv),
                                   limit=BARS_FETCH.get(iv, 520))
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

    def check_new_signals(self, cands, iv):
        """На новом закрытом баре ТФ iv гоняем детекторы сетапов этого ТФ."""
        bar_ms = se.bar_ms_of(iv)
        ctx = se.prep_context(cands, interval_min=iv)
        # живой режим берём из дневных свечей (короткой истории ТФ не хватает
        # на SMA100 дней)
        regime_now = self.daily_regime()
        ctx["regime"] = [regime_now] * len(cands)
        i = len(cands) - 1
        for key, rec in self.setups.items():
            base, key_iv = split_key(key, rec)
            if key_iv != iv:
                continue
            # enabled — подтверждённый торговый сетап (сейчас таких нет);
            # watch — режим НАБЛЮДЕНИЯ: показываем, но не рекомендуем.
            if not (rec.get("enabled", False) or rec.get("watch", False)):
                continue
            if key in self.state["active"]:
                continue
            g = rec["genome"]
            last_exit = max((h["exit_ts"] for h in self.state["history"]
                             if h["setup"] == key), default=0)
            # кулдаун считаем ТОЙ ЖЕ функцией, что и движок (от ЗАКРЫТИЯ
            # бара): раньше здесь бралось открытие, и помощник молчал там,
            # где бэктест торговал — расхождение до 13 сделок из 78
            if not se.cooldown_ok(cands[i][0], last_exit, g, iv):
                continue
            ext = se.build_ext(base, g, cands, interval_min=iv)
            ev_ = se.gate_eval(base, i, cands, ctx, g, ext)
            if not ev_["ok"]:
                continue
            ref_px, stop_px = ev_["entry_ref"], ev_["stop"]
            sgn = 1 if ev_["side"] == "L" else -1
            dist = ev_["dist"]
            tp_px = ev_["tp"]
            lev = rec.get("rec_lev", 15)
            # лимит риска: стоп не дальше 80% пути до ликвидации на этом плече
            if dist > 0.8 * se.liq_frac(lev):
                continue
            capital = self.state["capital"].get(key, SETUP_CAPITAL0)
            margin = round(capital * MARGIN_FRACTION, 2)
            risk_1r = round(margin * lev * dist, 2)  # убыток при стопе
            d = ev_["diag"]
            reg_name = se.REGIME_NAMES.get(regime_now, "?")
            entry_mode = int(g.get("entry_mode", 0))
            # Лимитка на ретесте: стратегия входит НЕ по цене сигнала, а на
            # откате в свою пользу, и ОТМЕНЯЕТ сигнал, если налив не случился
            # за retest_bars 15м-баров. Раньше помощник считал сделку сразу
            # активной по цене сигнала — по замеру аудита 47% сигналов не
            # должны были становиться сделками вовсе, а остальные брались по
            # худшей цене.
            atr_now = ev_["diag"].get("atr") or 0.0
            limit_px = ref_px
            pending_until = 0
            if entry_mode == 1:
                limit_px = ref_px * (1 - sgn * float(g.get("retest_atr", 0))
                                     * atr_now)
                pending_until = (cands[i][0] + bar_ms
                                 + int(g.get("retest_bars", 0)) * 15 * 60000)
                # стоп остаётся структурным, значит от лучшей цены входа
                # риск меньше, а тейк ближе — пересчитываем от лимита
                dist = sgn * (limit_px - stop_px) / limit_px
                if dist <= 0 or dist > 0.8 * se.liq_frac(lev):
                    continue
                tp_px = limit_px * (1 + sgn * se.RR * dist)
                risk_1r = round(margin * lev * dist, 2)
            entry = dict(
                setup=key, base=base, interval_min=iv, tf=tf_label(iv),
                side="LONG" if sgn == 1 else "SHORT",
                signal_ts=int(time.time() * 1000), bar_ts=cands[i][0],
                status=("ждём лимитку" if entry_mode == 1 else "в позиции"),
                pending=(entry_mode == 1), pending_until=pending_until,
                limit_px=round(limit_px, 1),
                entry=round(limit_px if entry_mode == 1 else ref_px, 1),
                signal_px=round(ref_px, 1),
                stop=round(stop_px, 1),
                tp=round(tp_px, 1), stop_pct=round(dist * 100, 2),
                tp_pct=round(se.RR * dist * 100, 2), lev=lev,
                margin=margin, risk_1r=risk_1r,
                capital_at_entry=round(capital, 2),
                regime=regime_now, regime_name=reg_name,
                rsi=round(d.get("rsi") or 0, 1),
                rsi_thr=round(d.get("rsi_thr") or 0, 1),
                zone_pos=round(d.get("zone_pos") or 0, 3),
                zone_thr=round(d.get("zone_thr") or 0, 3),
                range_lo=round(d.get("range_lo") or 0, 1),
                range_hi=round(d.get("range_hi") or 0, 1),
                stop_mode=int(g.get("stop_mode", 0)), entry_mode=entry_mode,
                hold_days=int(g["hold_days"]),
                filled_ts=int(time.time() * 1000),
                # для лимитки таймаут отсчитывается от фактического налива —
                # он переставляется в track_active, здесь просто верхняя рамка
                hold_until=int(time.time() * 1000) + g["hold_days"] * 86400000)
            entry["watch_only"] = not rec.get("enabled", False)
            self.state["active"][key] = entry
            st = rec.get("stats", {})
            h = rec.get("holdout", {})
            log.info("=" * 64)
            if entry["watch_only"]:
                log.info("НАБЛЮДЕНИЕ (НЕ рекомендация!) [%s]", self.title(key))
                log.info("Сетап НЕ подтверждён на невиданных данных: %s",
                         rec.get("reason", "порог не пройден"))
            else:
                log.info("СИГНАЛ [%s]", self.title(key))
            log.info("Сигнальный бар: %s, закрыт %s", tf_label(iv),
                     time.strftime("%d.%m %H:%M",
                                   time.localtime((cands[i][0] + bar_ms)
                                                  / 1000)))
            if entry_mode == 1:
                log.info("Вход: ЛИМИТКОЙ на ретесте ~%s (лучше цены сигнала на "
                         "%.2f ATR), ждать до %d 15м-свечей",
                         entry["entry"], g.get("retest_atr", 0),
                         int(g.get("retest_bars", 0)))
            else:
                log.info("Вход: ПО РЫНКУ ~%s", entry["entry"])
            log.info("Стоп: %s (-%.2f%%, %s) | Тейк: %s (+%.2f%%, R:R 1:3)",
                     entry["stop"], entry["stop_pct"],
                     se.STOP_MODE_NAMES.get(entry["stop_mode"], "?"),
                     entry["tp"], entry["tp_pct"])
            log.info("Маржа: $%.2f (25%% капитала сетапа $%.2f, реинвест) | "
                     "Плечо: x%d | ликвидация при -%.2f%%",
                     margin, capital, lev, se.liq_frac(lev) * 100)
            log.info("Риск: -$%.2f при стопе | Цель: +$%.2f при тейке (3R)",
                     risk_1r, risk_1r * RR)
            log.info("Контекст: рынок %s | RSI %.1f (порог %.1f) | цена в "
                     "диапазоне %.0f%% [%s .. %s]",
                     reg_name, entry["rsi"], entry["rsi_thr"],
                     entry["zone_pos"] * 100, entry["range_lo"],
                     entry["range_hi"])
            log.info("Таймаут: %d дн.", g["hold_days"])
            if h:
                log.info("ЧЕСТНЫЙ ЭКЗАМЕН (данные, не виденные отбором): "
                         "%s сделок, WR %s%%, %s R/сделку, PF %s, итог %s%%",
                         h.get("n"), h.get("wr"), h.get("exp_r"),
                         h.get("pf"), h.get("ret"))
            log.info("На обучающем периоде (НЕ доказательство): %s сделок, "
                     "WR %s%%, PF %s, итог %s%%",
                     st.get("n", "?"), st.get("wr", "?"), st.get("pf", "?"),
                     st.get("ret", "?"))
            log.info("=" * 64)
            self.save()

    def cost_r(self, s, taker):
        """Издержки сделки в долях риска R — те же ставки, что в бэктесте.

        Вход всегда тейкер + проскальзывание; выход по стопу — тоже тейкер со
        слипом, по тейку — мейкер без слипа. Фандинг — за фактическое время в
        позиции. Делим на риск (маржа x плечо x ширина стопа), потому что R
        считается именно от него.
        """
        dist = max(1e-9, s.get("stop_pct", 1.0) / 100.0)
        notional_r = 1.0 / dist          # ноционал в единицах риска
        fees = (se.TAKER + se.SLIP) + ((se.TAKER + se.SLIP) if taker
                                       else se.MAKER)
        hours = max(0.0, (int(time.time() * 1000)
                          - s.get("filled_ts", s["signal_ts"])) / 3600000.0)
        fund = se.FUND_8H * (hours / 8.0)
        return (fees + fund) * notional_r

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
        cancelled = []
        for key, s in self.state["active"].items():
            sgn = 1 if s["side"] == "LONG" else -1
            hi = max(x[2] for x in c15[-4:])
            lo = min(x[3] for x in c15[-4:])

            # ЖДЁМ ЛИМИТКУ (entry_mode=1): пока цена не откатилась к лимиту,
            # позиции НЕТ. Не дождались за отведённые бары — сигнал отменён
            # (в историю не идёт: сделки не было).
            if s.get("pending"):
                filled = (lo <= s["limit_px"]) if sgn == 1 else (hi >= s["limit_px"])
                if filled:
                    s["pending"] = False
                    s["status"] = "в позиции"
                    s["filled_ts"] = now
                    s["hold_until"] = now + int(
                        s.get("hold_days", 6)) * 86400000
                    log.info("[%s] лимитка налилась по %s — позиция открыта",
                             self.title(key), s["limit_px"])
                    continue
                if s.get("pending_until") and now > s["pending_until"]:
                    cancelled.append(key)
                    log.info("[%s] ретест не налился за отведённое время — "
                             "сигнал ОТМЕНЁН (сделки не было)", self.title(key))
                continue

            outcome = None
            # Издержки: тейк исполняется лимиткой (мейкер), стоп — по рынку
            # с проскальзыванием, плюс фандинг за удержание. Раньше здесь
            # стояли идеальные -1.0R и +3.0R, что завышало результат на
            # 21-25% (по замеру аудита) и раздувало виртуальный капитал.
            if (lo <= s["stop"]) if sgn == 1 else (hi >= s["stop"]):
                outcome, r_mult = "СТОП", -(1.0 + self.cost_r(s, taker=True))
            elif (hi >= s["tp"]) if sgn == 1 else (lo <= s["tp"]):
                outcome, r_mult = "ТЕЙК", se.RR - self.cost_r(s, taker=False)
            elif now > s["hold_until"]:
                px = last[4]
                move = sgn * (px - s["entry"]) / s["entry"]
                r_mult = round(move / (s["stop_pct"] / 100), 2)
                outcome = "ТАЙМАУТ"
            if outcome:
                risk = s.get("risk_1r", 0)
                pnl_usd = round(r_mult * risk, 2)
                cap_before = self.state["capital"].get(key, SETUP_CAPITAL0)
                self.state["capital"][key] = round(cap_before + pnl_usd, 2)
                rec = dict(s, exit_ts=now, outcome=outcome, r=r_mult,
                           pnl_usd=pnl_usd,
                           capital_after=self.state["capital"][key],
                           hold_h=round((now - s["signal_ts"]) / 3600000, 1))
                self.state["history"].append(rec)
                done.append(key)
                log.info("ИСХОД [%s]: %s (%+.1fR = %+.2f$, удержание %.1fч) | "
                         "капитал сетапа: $%.2f",
                         self.title(key), outcome, r_mult, pnl_usd,
                         rec["hold_h"], rec["capital_after"])
        for key in done + cancelled:
            del self.state["active"][key]
        if done or cancelled:
            self.save()

    def run(self):
        n_watch = sum(1 for r in self.setups.values()
                      if r.get("watch") and not r.get("enabled"))
        n_on = sum(1 for r in self.setups.values() if r.get("enabled"))
        per_tf = {iv: sum(1 for k, r in self.setups.items()
                          if split_key(k)[1] == iv and
                          (r.get("enabled") or r.get("watch")))
                  for iv in self.tfs}
        log.info("Помощник запущен: конфигов %d, отслеживается %s | "
                 "торговых %d, наблюдение %d | активных: %d",
                 len(self.setups),
                 ", ".join(f"{tf_label(iv)}: {n}" for iv, n in per_tf.items())
                 or "нечего (нет watch/enabled)",
                 n_on, n_watch, len(self.state["active"]))
        hb = os.path.join("webapp", "data", "advisor_heartbeat.txt")
        while True:
            try:
                self.track_active()
                for iv in self.tfs:
                    cands = self.fetch_bars(iv)
                    if cands and cands[-1][0] != self.last_ts[iv]:
                        self.last_ts[iv] = cands[-1][0]
                        self.check_new_signals(cands, iv)
                # heartbeat: сайт судит "работает ли помощник" по mtime этого
                # файла (лог пишется только на события и для этого не годится)
                with open(hb, "w", encoding="utf-8") as fh:
                    fh.write(str(int(time.time())))
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
