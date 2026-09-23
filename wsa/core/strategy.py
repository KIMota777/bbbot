"""Сводная стратегия по группе отслеживаемых кошельков.

Порядок (всё честно, без подгонки под результат):
1. набор данных: входы кошельков группы с признаками на момент входа и
   доходностью через горизонт H за вычетом комиссий; одновременные входы
   разных кошельков в один токен схлопываются (иначе выборка раздута);
2. контрольная группа: случайные моменты в тех же токенах. Разница между
   входами и контролем = «подпись отбора» — что кошельки ищут перед покупкой;
3. правила (1–2 условия) ищутся на первых 70% истории, проверяются на
   последних 30%. Критерии приёмки заданы в конфиге ДО прогона;
4. выходы (стоп/тейки/тайм-стоп) калибруются по MAE/MFE обучающей части;
5. правила для сканера рынка (без кошелька-триггера) проверяются отдельно —
   на контрольной группе: работают ли условия сами по себе, без выбора кошелька.
"""
from __future__ import annotations

import json
import logging
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..util import DAY, HOUR, fmt_pct, mean, median, now, quantile, stdev
from .features import CATEGORICAL, FEATURE_RU, NUMERIC, forward, price_features, regime

log = logging.getLogger("wsa.strategy")

HORIZONS = {"6h": 6 * HOUR, "24h": DAY, "3d": 3 * DAY, "7d": 7 * DAY}
WALLET_ONLY = {"size_rel", "consensus_24h", "buy_index_bucket"}
MARKET_FEATURES = [f for f in NUMERIC if f not in WALLET_ONLY]


# ---------------- правила ----------------

@dataclass
class Cond:
    f: str
    op: str
    v: Any

    def test(self, feats: dict) -> bool:
        x = feats.get(self.f)
        if x is None:
            return False
        if self.op == ">=":
            return x >= self.v
        if self.op == "<=":
            return x <= self.v
        return x == self.v

    def text(self) -> str:
        name = FEATURE_RU.get(self.f, self.f)
        v = self.v
        if isinstance(v, bool):
            return f"{name}: {'да' if v else 'нет'}"
        if isinstance(v, str):
            return f"{name} = {v}"
        if self.f.startswith(("ret_", "dd_", "sma", "btc_", "native_", "holders_", "volatility")) or self.f == "top10_pct":
            vs = fmt_pct(v, signed=True)
        elif self.f in ("mc_entry", "fdv_entry", "liquidity_now"):
            vs = f"${v / 1e6:.1f}M"
        else:
            vs = f"{v:.2f}" if abs(v) < 100 else f"{v:.0f}"
        return f"{name} {'≥' if self.op == '>=' else '≤'} {vs}"

    def to_json(self) -> dict:
        return {"f": self.f, "op": self.op, "v": self.v}


@dataclass
class Rule:
    conds: list[Cond]
    id: str = ""
    train: dict = field(default_factory=dict)
    test: dict = field(default_factory=dict)
    accepted: bool = False
    reason: str = ""

    def match(self, feats: dict) -> bool:
        return all(c.test(feats) for c in self.conds)

    def text(self) -> str:
        return " И ".join(c.text() for c in self.conds) if self.conds else "все первые входы группы (без фильтра)"

    def to_json(self) -> dict:
        return {"id": self.id, "conds": [c.to_json() for c in self.conds], "text": self.text(),
                "train": self.train, "test": self.test, "accepted": self.accepted, "reason": self.reason}

    @staticmethod
    def from_json(d: dict) -> "Rule":
        r = Rule([Cond(c["f"], c["op"], c["v"]) for c in d["conds"]], d.get("id", ""))
        r.train, r.test, r.accepted, r.reason = d.get("train", {}), d.get("test", {}), d.get("accepted", False), d.get("reason", "")
        return r


def fmt_feature(feat: str, v) -> str:
    """Значение признака в человеческом виде: доли — в процентах, деньги — в $M."""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, str):
        return v
    if feat.startswith(("ret_", "dd_", "sma", "btc_", "native_", "holders_", "volatility")) or feat in ("top10_pct", "range_pos_7d"):
        return fmt_pct(v, signed=feat != "range_pos_7d" and feat != "top10_pct")
    if feat in ("mc_entry", "fdv_entry", "liquidity_now"):
        return f"${v / 1e6:.2f}M" if v < 1e9 else f"${v / 1e9:.2f}B"
    return f"{v:.2f}" if abs(v) < 100 else f"{v:.0f}"


def stats(ys: list[float]) -> dict:
    ys = [y for y in ys if y is not None]
    n = len(ys)
    if not n:
        return {"n": 0}
    m = sum(ys) / n
    sd = stdev(ys) if n > 1 else None
    t = (m / (sd / n ** 0.5)) if sd else None
    return {"n": n, "mean": m, "median": median(ys), "hit": sum(1 for y in ys if y > 0) / n, "t": t}


# ---------------- данные ----------------

def load_rows(ctx, cohort: list[tuple[str, str]], first_only: bool = True) -> list[dict]:
    rows = []
    for chain, addr in cohort:
        q = "SELECT * FROM entries WHERE chain=? AND wallet=?" + (" AND buy_index=1" if first_only else "")
        for r in ctx.db.query(q, (chain, addr)):
            f = json.loads(r["features"])
            rows.append({"chain": chain, "wallet": addr, "token": r["token"], "ts": r["ts"], "usd": r["usd"],
                         "price": r["price"], "buy_index": r["buy_index"], "entry_type": r["entry_type"],
                         "f": f, "fwd": json.loads(r["fwd"]), "outcome": json.loads(r["outcome"]),
                         "price_src": r["price_src"]})
    rows.sort(key=lambda x: x["ts"])
    return rows


def relationships(rows: list[dict], window: int = 600) -> dict:
    """Похожесть кошельков: общие токены и синхронные входы (≤10 мин).

    Два адреса, которые почти всегда входят вместе, — скорее всего один
    владелец. Для консенсуса их надо считать одним голосом.
    """
    by_w: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_w[r["wallet"]].append(r)
    ws = sorted(by_w)
    pairs = {}
    for i, a in enumerate(ws):
        ta = {r["token"] for r in by_w[a]}
        for b in ws[i + 1:]:
            tb = {r["token"] for r in by_w[b]}
            inter = ta & tb
            if not inter:
                continue
            co = 0
            for r in by_w[a]:
                if r["token"] in inter and any(abs(r["ts"] - s["ts"]) <= window and s["token"] == r["token"] for s in by_w[b]):
                    co += 1
            pairs[f"{a}|{b}"] = {"jaccard": len(inter) / len(ta | tb), "co_entries": co,
                                 "co_share": co / max(1, min(len(by_w[a]), len(by_w[b])))}
    return pairs


def entity_map(rels: dict, threshold: float = 0.5) -> dict[str, str]:
    """Кошелёк -> «сущность» (объединение часто входящих вместе адресов)."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for k, v in rels.items():
        a, b = k.split("|")
        find(a), find(b)
        if v["co_share"] >= threshold and v["co_entries"] >= 3:
            parent[find(a)] = find(b)
    return {w: find(w) for w in list(parent)}


def add_consensus(rows: list[dict], entities: dict[str, str], window: int = DAY) -> None:
    """consensus_24h: сколько других сущностей группы входили в токен за прошедшие 24ч."""
    by_tok: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_tok[r["token"]].append(r)
    for lst in by_tok.values():
        lst.sort(key=lambda x: x["ts"])
        for i, r in enumerate(lst):
            me = entities.get(r["wallet"], r["wallet"])
            others = {entities.get(s["wallet"], s["wallet"]) for s in lst[:i]
                      if r["ts"] - window <= s["ts"] <= r["ts"]} - {me}
            r["f"]["consensus_24h"] = len(others)


def dedupe(rows: list[dict], bucket: int = 6 * HOUR) -> list[dict]:
    """Один токен в одном 6-часовом окне = одна возможность, сколько бы кошельков ни вошло."""
    seen, out = set(), []
    for r in rows:
        k = (r["token"], r["ts"] // bucket)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def baseline(ctx, rows: list[dict], per_token: int, seed: int = 7) -> list[dict]:
    """Контрольная группа: случайные моменты в тех же токенах за тот же период."""
    rng = random.Random(seed)
    by_tok: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_tok[(r["chain"], r["token"])].append(r)
    out = []
    if not rows:
        return out
    T0 = min(r["ts"] for r in rows) - 8 * DAY
    btc = ctx.pricer._cex_series(ctx.ref, "BTC/USDT", "1h", T0, now())
    natives = {}
    for (chain, tok), lst in by_tok.items():
        t_lo = min(r["ts"] for r in lst) - 30 * DAY
        t_hi = max(r["ts"] for r in lst)
        s = ctx.pricer.token_series(chain, tok, t_lo - 31 * DAY, min(now(), t_hi + 31 * DAY), allow_dex=False)
        if s is None and any(r["price_src"].startswith("gt:") for r in lst):
            s = ctx.pricer.dex_series(chain, tok, t_lo, t_hi + 31 * DAY)
        if s is None or len(s) < 200:
            continue
        if chain not in natives:
            from .pricing import native_id
            natives[chain] = ctx.pricer.token_series(chain, native_id(chain), T0, now(), allow_dex=False)
        lo = max(t_lo, s.start + 8 * DAY)
        hi = min(t_hi, (s.end or now()) - 7 * DAY)
        if hi <= lo:
            continue
        proto = lst[0]["f"]
        for _ in range(per_token):
            ts = rng.randint(lo, hi)
            p = s.close_before(ts)
            if not p:
                continue
            f = price_features(s, ts, p)
            f.update(regime(btc, natives[chain], ts))
            for k in ("sector", "cex_listed_now"):
                f[k] = proto.get(k)
            f["liquidity_now"] = proto.get("liquidity_now")
            if proto.get("mc_entry") and lst[0]["price"]:
                f["mc_entry"] = proto["mc_entry"] * p / lst[0]["price"]
            if proto.get("token_age_days") is not None:
                f["token_age_days"] = proto["token_age_days"] + (ts - lst[0]["ts"]) / DAY
            if proto.get("days_since_listing") is not None:
                f["days_since_listing"] = proto["days_since_listing"] + (ts - lst[0]["ts"]) / DAY
                f["cex_listed"] = f["days_since_listing"] >= 0
            from .features import classify
            f["entry_type"] = classify(f, ctx.cfg)
            out.append({"chain": chain, "token": tok, "ts": ts, "price": p, "f": f,
                        "fwd": forward(s, ts, p), "wallet": None})
    out.sort(key=lambda x: x["ts"])
    return out


# ---------------- анализ ----------------

def y_of(r: dict, h: str, fee: float) -> float | None:
    v = r["fwd"].get(h)
    return None if v is None else v - fee


def signature(rows: list[dict], base: list[dict]) -> list[dict]:
    """Чем моменты входа отличаются от случайных: медианы и сила отличия.

    effect — вероятность, что признак у входа больше, чем у случайного момента,
    минус 0.5 (0 — нет отличия, ±0.5 — полное разделение).
    """
    out = []
    for feat in MARKET_FEATURES:
        a = [r["f"].get(feat) for r in rows if r["f"].get(feat) is not None]
        b = [r["f"].get(feat) for r in base if r["f"].get(feat) is not None]
        if len(a) < 10 or len(b) < 10:
            continue
        sb = sorted(b)
        import bisect
        gt = sum(bisect.bisect_left(sb, x) + 0.5 * (bisect.bisect_right(sb, x) - bisect.bisect_left(sb, x)) for x in a)
        effect = gt / (len(a) * len(b)) - 0.5
        out.append({"feature": feat, "name": FEATURE_RU.get(feat, feat), "entries_median": median(a),
                    "baseline_median": median(b), "effect": effect, "n": len(a),
                    "entries_txt": fmt_feature(feat, median(a)), "baseline_txt": fmt_feature(feat, median(b))})
    for feat in ("entry_type", "sector", "breakout_7d"):
        ca, cb = defaultdict(int), defaultdict(int)
        for r in rows:
            ca[r["f"].get(feat)] += 1
        for r in base:
            cb[r["f"].get(feat)] += 1
        na, nb = sum(ca.values()), sum(cb.values())
        for k in ca:
            if k is None or ca[k] < 5:
                continue
            sa, sbb = ca[k] / na, (cb.get(k, 0) / nb if nb else 0)
            out.append({"feature": f"{feat}={k}", "name": f"{FEATURE_RU.get(feat, feat)} = {k}",
                        "entries_share": sa, "baseline_share": sbb, "effect": sa - sbb, "n": ca[k],
                        "entries_txt": f"{sa:.0%}", "baseline_txt": f"{sbb:.0%}"})
    out.sort(key=lambda d: -abs(d["effect"]))
    return out


def univariate(rows: list[dict], h: str, fee: float, features: list[str]) -> list[dict]:
    out = []
    for feat in features:
        vals = [r["f"].get(feat) for r in rows if r["f"].get(feat) is not None and y_of(r, h, fee) is not None]
        if len(vals) < 25:
            continue
        edges = [quantile(vals, q) for q in (0.2, 0.4, 0.6, 0.8)]
        bins = []
        for i in range(5):
            lo = edges[i - 1] if i > 0 else None
            hi = edges[i] if i < 4 else None
            ys = [y_of(r, h, fee) for r in rows if r["f"].get(feat) is not None and y_of(r, h, fee) is not None
                  and (lo is None or r["f"][feat] > lo) and (hi is None or r["f"][feat] <= hi)]
            bins.append({"lo": lo, "hi": hi, **stats(ys)})
        out.append({"feature": feat, "name": FEATURE_RU.get(feat, feat), "bins": bins})
    for feat in CATEGORICAL:
        groups = defaultdict(list)
        for r in rows:
            y = y_of(r, h, fee)
            if y is not None and r["f"].get(feat) is not None:
                groups[r["f"][feat]].append(y)
        if len(groups) < 2:
            continue
        out.append({"feature": feat, "name": FEATURE_RU.get(feat, feat),
                    "cats": {str(k): stats(v) for k, v in groups.items() if len(v) >= 5}})
    return out


def candidate_conds(rows: list[dict], features: list[str], min_support: int) -> list[Cond]:
    conds = []
    for feat in features:
        vals = [r["f"].get(feat) for r in rows if isinstance(r["f"].get(feat), (int, float))
                and not isinstance(r["f"].get(feat), bool)]
        if len(vals) >= 2 * min_support:
            for q in (0.2, 0.35, 0.5, 0.65, 0.8):
                t = quantile(vals, q)
                if t is None:
                    continue
                t = float(f"{t:.4g}")
                conds.append(Cond(feat, ">=", t))
                conds.append(Cond(feat, "<=", t))
    for feat in [c for c in CATEGORICAL if c in features or c in ("entry_type", "sector", "cex_listed", "breakout_7d")]:
        cnt = defaultdict(int)
        for r in rows:
            v = r["f"].get(feat)
            if v is not None:
                cnt[v] += 1
        for v, n in cnt.items():
            if n >= min_support:
                conds.append(Cond(feat, "==", v))
    # уникальные по смыслу
    uniq = {}
    for c in conds:
        uniq[(c.f, c.op, json.dumps(c.v))] = c
    return list(uniq.values())


def eval_rule(rule: Rule, rows: list[dict], h: str, fee: float) -> dict:
    return stats([y_of(r, h, fee) for r in rows if rule.match(r["f"])])


def mine(train: list[dict], test: list[dict], h: str, fee: float, features: list[str], cfg,
         prefix: str = "r", base_mean: float | None = None) -> tuple[list[Rule], int]:
    """Правила из 1–2 условий. base_mean — средняя доходность всех входов на обучении:
    правило должно давать прирост над ней, иначе оно лишь пересказывает «брать всё»."""
    sc = cfg.strategy
    acc = sc.acceptance
    ms = sc.min_support
    floor = max(base_mean or 0.0, 0.0) * 1.1 + 0.002
    conds = candidate_conds(train, features, ms)
    singles = []
    for c in conds:
        st = stats([y_of(r, h, fee) for r in train if c.test(r["f"])])
        if st["n"] >= ms and st.get("t") is not None and st["mean"] > floor:
            singles.append((st["t"], c, st))
    singles.sort(key=lambda x: -x[0])
    top = singles[:40]
    cands: list[Rule] = [Rule([c], train=st) for _, c, st in top]
    tested = len(conds)
    if sc.max_rule_len >= 2:
        for i in range(len(top)):
            for j in range(i + 1, len(top)):
                (_, a, sa), (_, b, sb) = top[i], top[j]
                if a.f == b.f:
                    continue
                rule = Rule([a, b])
                st = eval_rule(rule, train, h, fee)
                tested += 1
                # бережливость: второе условие должно заметно улучшать каждое из двух
                # по отдельности, иначе это шум, подогнанный под обучающую выборку
                better = st.get("mean") is not None and st["mean"] >= 1.15 * max(sa["mean"], sb["mean"])
                if st["n"] >= ms and st.get("t") is not None and st["mean"] > 0 and better:
                    rule.train = st
                    cands.append(rule)
    cands.sort(key=lambda r: -(r.train.get("t") or 0))
    # убираем почти одинаковые правила (одно и то же подмножество входов)
    chosen: list[Rule] = []
    covered: list[set] = []
    for r in cands:
        idx = {i for i, row in enumerate(train) if r.match(row["f"])}
        if any(len(idx & c) / max(1, len(idx | c)) > 0.8 for c in covered):
            continue
        chosen.append(r)
        covered.append(idx)
        if len(chosen) >= 12:
            break
    for k, r in enumerate(chosen, 1):
        r.id = f"{prefix}{k}"
        r.test = eval_rule(r, test, h, fee)
        r.accepted, r.reason = accept(r.train, r.test, acc)
    return chosen, tested


def accept(tr: dict, te: dict, acc) -> tuple[bool, str]:
    if te.get("n", 0) < acc.min_oos_trades:
        return False, f"мало сделок на проверке: {te.get('n', 0)} < {acc.min_oos_trades}"
    if te["mean"] <= acc.min_oos_mean:
        return False, f"на проверке средняя доходность {fmt_pct(te['mean'], signed=True)} ≤ {fmt_pct(acc.min_oos_mean)}"
    if te["hit"] < acc.min_oos_hit:
        return False, f"на проверке доля прибыльных {te['hit']:.0%} < {acc.min_oos_hit:.0%}"
    if tr.get("mean") and tr["mean"] > 0 and 1 - te["mean"] / tr["mean"] > acc.max_degradation:
        return False, f"деградация {1 - te['mean'] / tr['mean']:.0%} > {acc.max_degradation:.0%}"
    return True, "принято"


def calibrate_exits(rows: list[dict], h: str, fee: float) -> dict:
    """Стоп — ниже типичной просадки выигрышных входов, тейки — по квантилям MFE.

    MFE/MAE берутся в пределах горизонта стратегии: тейк, до которого цена
    доходит лишь за неделю, бесполезен при тайм-стопе через сутки.
    """
    k_mfe, k_mae = f"mfe_{h}", f"mae_{h}"
    if not any(k_mfe in r["fwd"] for r in rows):
        k_mfe, k_mae = "mfe_7d", "mae_7d"
    wins = [r for r in rows if (y_of(r, h, fee) or 0) > 0]
    mae_w = [-(r["fwd"].get(k_mae) or 0) for r in wins if r["fwd"].get(k_mae) is not None]
    mfe = [r["fwd"].get(k_mfe) for r in rows if r["fwd"].get(k_mfe) is not None]
    sl = quantile(mae_w, 0.8) if mae_w else 0.1
    sl = min(0.25, max(0.03, sl or 0.1))
    tp1 = quantile(mfe, 0.4) if mfe else 0.08
    tp2 = quantile(mfe, 0.65) if mfe else 0.2
    tp1 = max(tp1 or 0.08, 3 * fee, 0.02)
    tp2 = max(tp2 or 0.2, tp1 * 1.5)
    return {"sl": -round(sl, 4), "tp": [[round(tp1, 4), 0.34], [round(tp2, 4), 0.33]],
            "time_stop_sec": HORIZONS[h], "trail_after_tp1": True,
            "basis": {"window": k_mfe, "mae_winners_p80": sl, "mfe_p40": tp1, "mfe_p65": tp2, "n": len(rows)}}


# ---------------- сборка ----------------

def choose_horizon(rows: list[dict], cohort_hold: float | None) -> str:
    if not cohort_hold:
        return "24h"
    return min(HORIZONS, key=lambda h: abs(HORIZONS[h] - cohort_hold))


def build(ctx, cohort: list[dict], horizon: str = "auto", cex_only: bool = True) -> dict:
    cfg = ctx.cfg
    sc = cfg.strategy
    fee = sc.fee_roundtrip
    pairs = [(c["chain"], c["address"]) for c in cohort]
    rows_all = load_rows(ctx, pairs)
    rels = relationships(rows_all)
    ents = entity_map(rels)
    add_consensus(rows_all, ents)
    rows = [r for r in rows_all if r["f"].get("cex_listed")] if cex_only else rows_all
    note = []
    if cex_only and len(rows) < 3 * sc.min_support:
        note.append(f"входов в токены с листингом на CEX мало ({len(rows)}), взяты все входы")
        rows = rows_all
    rows = dedupe(rows)
    holds = [c.get("hold_p50") for c in cohort if c.get("hold_p50")]
    h = choose_horizon(rows, median(holds)) if horizon == "auto" else horizon
    usable = [r for r in rows if y_of(r, h, fee) is not None]
    if len(usable) < 2 * sc.min_support:
        return {"ok": False, "reason": f"мало входов с известной доходностью через {h}: {len(usable)}",
                "n_rows": len(rows), "horizon": h}
    split = int(len(usable) * sc.train_frac)
    train, test = usable[:split], usable[split:]
    split_ts = test[0]["ts"] if test else now()

    base = baseline(ctx, rows, sc.baseline_per_token)
    sig = signature(rows, base)
    uni = univariate(train, h, fee, NUMERIC)

    all_train = stats([y_of(r, h, fee) for r in train])
    all_test = stats([y_of(r, h, fee) for r in test])
    # базовое правило «брать все первые входы группы» — с теми же критериями приёмки
    follow_all = Rule([], "w0", train=all_train, test=all_test)
    follow_all.accepted, follow_all.reason = accept(all_train, all_test, sc.acceptance)
    wallet_rules, n_w = mine(train, test, h, fee, NUMERIC, cfg, prefix="w", base_mean=all_train.get("mean"))
    wallet_rules = [follow_all] + wallet_rules
    base_usable = [r for r in base if y_of(r, h, fee) is not None]
    bsplit = [r for r in base_usable if r["ts"] < split_ts], [r for r in base_usable if r["ts"] >= split_ts]
    base_train_mean = stats([y_of(r, h, fee) for r in bsplit[0]]).get("mean")
    scan_rules, n_s = (mine(bsplit[0], bsplit[1], h, fee, MARKET_FEATURES, cfg, prefix="s", base_mean=base_train_mean)
                       if len(base_usable) > 100 else ([], 0))

    # если есть принятые правила-фильтры, базовое «брать всё» в торговле не нужно:
    # оно нужно только для сравнения (и как режим, если фильтры не прошли)
    filters = [r for r in wallet_rules[1:] if r.accepted]
    if filters:
        follow_all.accepted = False
        follow_all.reason = "есть принятые фильтры с приростом над «брать всё»" if follow_all.reason == "принято" else follow_all.reason
    accepted_w = [r for r in wallet_rules if r.accepted]
    accepted_s = [r for r in scan_rules if r.accepted]
    # итог по объединению принятых правил на проверочной части
    union_test = stats([y_of(r, h, fee) for r in test if any(x.match(r["f"]) for x in accepted_w)]) if accepted_w else {"n": 0}
    base_test = stats([y_of(r, h, fee) for r in bsplit[1]])
    matched_train = [r for r in train if any(x.match(r["f"]) for x in accepted_w)] or train
    exits = calibrate_exits(matched_train, h, fee)

    strategy = {
        "version": 1, "built_at": now(), "ok": True, "notes": note,
        "exchange": cfg.cex.exchange, "market": cfg.cex.market, "horizon": h, "fee_roundtrip": fee,
        "cohort": [{"chain": c["chain"], "address": c["address"], "score": c.get("score"),
                    "entity": ents.get(c["address"], c["address"])} for c in cohort],
        "entities": len(set(ents.get(c["address"], c["address"]) for c in cohort)),
        "relationships": {k: v for k, v in rels.items() if v["co_share"] >= 0.3},
        "data": {"rows": len(rows), "usable": len(usable), "train": len(train), "test": len(test),
                 "split_ts": split_ts, "baseline": len(base), "cex_only": cex_only},
        "performance": {"train_all": all_train, "test_all": all_test, "test_union": union_test,
                        "baseline_test": base_test,
                        "timing_edge_test": (all_test.get("mean") or 0) - (base_test.get("mean") or 0)
                        if all_test.get("n") and base_test.get("n") else None},
        "signature": sig[:25],
        "univariate": uni,
        "wallet_trigger": {
            "min_wallet_score": cfg.trading.min_wallet_score,
            "min_consensus": cfg.trading.min_consensus,
            "first_buy_only": True,
            "follow_all_if_no_rules": False,
            "rules": [r.to_json() for r in wallet_rules],
            "n_tested": n_w,
        },
        "scanner": {"enabled": bool(accepted_s), "rules": [r.to_json() for r in scan_rules], "n_tested": n_s},
        "exits": {**exits, "exit_on_leader_sell": cfg.trading.exit_on_leader_sell,
                  "leader_sell_fraction": cfg.trading.leader_sell_fraction},
        "sizing": {"risk_per_trade": cfg.trading.risk_per_trade, "max_position_pct": cfg.trading.max_position_pct},
        "acceptance": dict(sc.acceptance),
    }
    strategy["accepted"] = bool(accepted_w) or bool(accepted_s)
    strategy["summary_ru"] = summary_text(strategy)
    return strategy


def summary_text(s: dict) -> str:
    L = []
    p = s["performance"]
    L.append(f"Горизонт {s['horizon']}, комиссии туда-обратно {fmt_pct(s['fee_roundtrip'])}. "
             f"Входов: {s['data']['usable']} (обучение {s['data']['train']}, проверка {s['data']['test']}), "
             f"независимых владельцев в группе: {s['entities']}.")
    if p.get("test_all", {}).get("n"):
        L.append(f"Все входы группы на проверке: средняя {fmt_pct(p['test_all']['mean'], signed=True)}, "
                 f"прибыльных {p['test_all']['hit']:.0%}.")
    if p.get("timing_edge_test") is not None:
        L.append(f"Преимущество момента входа над случайным: {fmt_pct(p['timing_edge_test'], signed=True)} за {s['horizon']}.")
    acc_w = [r for r in s["wallet_trigger"]["rules"] if r["accepted"]]
    acc_s = [r for r in s["scanner"]["rules"] if r["accepted"]]
    if acc_w:
        L.append(f"Принято правил входа за кошельком: {len(acc_w)}. Лучшие: " + "; ".join(
            f"[{r['id']}] {r['text']} → проверка {fmt_pct(r['test']['mean'], signed=True)}, {r['test']['hit']:.0%} прибыльных, n={r['test']['n']}"
            for r in acc_w[:3]) + ".")
    else:
        L.append("Ни одно правило входа за кошельком не прошло проверку на отложенных данных — "
                 "стратегию нельзя включать на реальные деньги; копить данные дальше.")
    if acc_s:
        L.append("Правила сканера (без кошелька): " + "; ".join(f"[{r['id']}] {r['text']}" for r in acc_s[:3]) + ".")
    e = s["exits"]
    L.append(f"Выходы: стоп {fmt_pct(e['sl'])}, тейки " + ", ".join(f"{fmt_pct(t, signed=True)} ({f:.0%})" for t, f in e["tp"])
             + f", тайм-стоп {e['time_stop_sec'] // 3600} ч" + (", выход вслед за продажей лидера" if e.get("exit_on_leader_sell") else "") + ".")
    top = [x for x in s["signature"][:5] if abs(x["effect"]) >= 0.05]
    if top:
        L.append("Подпись отбора (чем входы отличаются от случайных моментов): " + "; ".join(
            f"{x['name']}: {x['entries_txt']} против {x['baseline_txt']}" for x in top) + ".")
    return " ".join(L)


class Strategy:
    def __init__(self, d: dict):
        self.d = d
        self.wallet_rules = [Rule.from_json(r) for r in d.get("wallet_trigger", {}).get("rules", []) if r.get("accepted")]
        self.scanner_rules = [Rule.from_json(r) for r in d.get("scanner", {}).get("rules", []) if r.get("accepted")]

    @property
    def exits(self) -> dict:
        return self.d["exits"]

    def match_wallet(self, feats: dict) -> Rule | None:
        for r in self.wallet_rules:
            if r.match(feats):
                return r
        return None

    def match_scanner(self, feats: dict) -> Rule | None:
        for r in self.scanner_rules:
            if r.match(feats):
                return r
        return None

    @staticmethod
    def load(path: str) -> "Strategy | None":
        p = Path(path)
        if not p.exists():
            return None
        return Strategy(json.loads(p.read_text(encoding="utf-8")))

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.d, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
