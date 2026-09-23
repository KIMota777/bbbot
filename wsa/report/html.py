"""HTML-отчёт: рейтинг кошельков, профили, стратегия, бэктест, сигналы и позиции.

Один самодостаточный файл: стили и графики (SVG) внутри, без внешних
скриптов — открывается локально двойным кликом.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from ..core.features import ENTRY_RU
from ..util import fmt_dur, fmt_pct, fmt_ts, fmt_usd, now, short_addr

CSS = """
:root{
  --bg:#f3f6f5;--surface:#ffffff;--sunk:#eaf0ee;--ink:#14201c;--muted:#5b6a64;--line:#d9e1de;
  --accent:#1d6b86;--accent-soft:#dcecf2;--pos:#1c7a4c;--pos-soft:#dff1e7;--neg:#b03636;--neg-soft:#f7e1e1;
  --warn:#9a6300;--warn-soft:#f6ecd6;--chart:#1d6b86;--chart-2:#8aa39a;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --sans:"IBM Plex Sans",-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
  --cond:"IBM Plex Sans Condensed","IBM Plex Sans",-apple-system,"Segoe UI",Arial,sans-serif;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  color-scheme:dark;--bg:#0e1412;--surface:#141c19;--sunk:#101714;--ink:#e2ebe7;--muted:#94a39d;--line:#25322d;
  --accent:#63b4d0;--accent-soft:#16303a;--pos:#4fc58c;--pos-soft:#15301f;--neg:#f07e7e;--neg-soft:#3a1c1c;
  --warn:#e1ab43;--warn-soft:#3a2d12;--chart:#63b4d0;--chart-2:#50635c;}}
:root[data-theme="dark"]{
  color-scheme:dark;--bg:#0e1412;--surface:#141c19;--sunk:#101714;--ink:#e2ebe7;--muted:#94a39d;--line:#25322d;
  --accent:#63b4d0;--accent-soft:#16303a;--pos:#4fc58c;--pos-soft:#15301f;--neg:#f07e7e;--neg-soft:#3a1c1c;
  --warn:#e1ab43;--warn-soft:#3a2d12;--chart:#63b4d0;--chart-2:#50635c;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding-inline:20px;padding-block:0 48px}
header.top{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:var(--bg);border-bottom:1px solid var(--line)}
header.top .wrap{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:baseline;padding-block:12px}
.brand{font:600 17px/1.2 var(--cond);letter-spacing:.01em}
.brand small{font:400 12px var(--mono);color:var(--muted);margin-left:8px}
nav{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:13px}
nav a{color:var(--muted);text-decoration:none}
nav a:hover,nav a:focus-visible{color:var(--accent)}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
h1{font:600 26px/1.2 var(--cond);margin:28px 0 6px;text-wrap:balance}
h2{font:600 19px/1.25 var(--cond);margin:40px 0 12px;text-wrap:balance;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
h2 .hint{font:400 13px var(--sans);color:var(--muted)}
h3{font:600 15px/1.3 var(--cond);margin:0 0 8px}
p.lead{color:var(--muted);max-width:72ch;margin:0 0 8px}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:6px;overflow:hidden;margin-top:16px}
.strip div{background:var(--surface);padding:12px 14px}
.strip b{display:block;font:600 20px/1.2 var(--mono);font-variant-numeric:tabular-nums}
.strip span{font-size:12px;color:var(--muted);letter-spacing:.02em}
.box{background:var(--surface);border:1px solid var(--line);border-radius:6px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}
th{font:500 11px var(--sans);text-transform:uppercase;letter-spacing:.06em;color:var(--muted);text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);white-space:nowrap;background:var(--sunk)}
td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;font-family:var(--mono);white-space:nowrap}
.mono{font-family:var(--mono);font-size:12.5px}
.pos{color:var(--pos)}.neg{color:var(--neg)}.muted{color:var(--muted)}
.chip{display:inline-block;font:500 11px var(--sans);padding:1px 7px;border-radius:999px;margin:1px 3px 1px 0;white-space:nowrap;background:var(--sunk);color:var(--muted);border:1px solid var(--line)}
.chip.hard{background:var(--neg-soft);color:var(--neg);border-color:transparent}
.chip.soft{background:var(--warn-soft);color:var(--warn);border-color:transparent}
.chip.ok{background:var(--pos-soft);color:var(--pos);border-color:transparent}
.chip.acc{background:var(--accent-soft);color:var(--accent);border-color:transparent}
.score{display:flex;align-items:center;gap:8px;min-width:92px}
.score i{flex:1;height:6px;border-radius:3px;background:var(--sunk);position:relative;overflow:hidden}
.score i::after{content:"";position:absolute;inset:0 auto 0 0;width:var(--w);background:var(--accent)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
.card{padding:16px}
.card .addr{font:500 12.5px var(--mono);word-break:break-all;color:var(--muted)}
.kv{display:grid;grid-template-columns:repeat(3,1fr);gap:8px 12px;margin:12px 0}
.kv div span{display:block;font-size:11px;color:var(--muted);letter-spacing:.03em}
.kv div b{font:600 14px var(--mono);font-variant-numeric:tabular-nums}
.bars{display:grid;gap:5px;margin:10px 0}
.bar{display:grid;grid-template-columns:120px 1fr 44px;gap:8px;align-items:center;font-size:12px}
.bar i{height:8px;border-radius:2px;background:var(--sunk);position:relative;overflow:hidden}
.bar i::after{content:"";position:absolute;inset:0 auto 0 0;width:var(--w);background:var(--chart)}
.bar em{font:normal 12px var(--mono);text-align:right;color:var(--muted)}
.detect{border-top:1px solid var(--line);margin-top:12px;padding-top:10px;font-size:13px;max-width:70ch}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.pad{padding:14px 16px}
svg text{fill:var(--muted);font:11px var(--mono)}
.note{font-size:12.5px;color:var(--muted);max-width:80ch}
footer{margin-top:48px;border-top:1px solid var(--line);padding-top:14px;color:var(--muted);font-size:12px}
@media (max-width:520px){.kv{grid-template-columns:repeat(2,1fr)}.bar{grid-template-columns:96px 1fr 40px}h1{font-size:22px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""

FLAG_RU = {"bot_frequency": "бот", "bot_swaps": "бот", "hft_hold": "HFT", "mev": "MEV", "custom_program": "свой контракт",
           "low_sample": "мало сделок", "one_hit": "одна монета", "unmatched": "продажи без покупок",
           "short_history": "короткая история", "cex_irrelevant": "не для CEX", "unprofitable": "убыток"}


def e(x) -> str:
    return html.escape("" if x is None else str(x))


def cls_sign(x) -> str:
    if x is None:
        return "muted"
    return "pos" if x > 0 else ("neg" if x < 0 else "")


def chips(flags: dict) -> str:
    out = []
    for k, v in (flags or {}).items():
        out.append(f'<span class="chip {"hard" if v.get("level") == "hard" else "soft"}" title="{e(v.get("msg"))}">'
                   f'{e(FLAG_RU.get(k, k))}</span>')
    return "".join(out) or '<span class="chip ok">чисто</span>'


def spark_equity(points: list[tuple[int, float]], w: int = 560, h: int = 170) -> str:
    """Кривая капитала с сеткой, подписями и выделенной последней точкой."""
    if len(points) < 2:
        return '<p class="note">Недостаточно сделок для кривой.</p>'
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    lo, hi = min(ys), max(ys)
    if hi - lo < 1e-9:
        hi = lo + 1
    pad_l, pad_r, pad_t, pad_b = 52, 12, 10, 24
    def X(x):
        return pad_l + (x - xs[0]) / max(1, xs[-1] - xs[0]) * (w - pad_l - pad_r)
    def Y(y):
        return pad_t + (hi - y) / (hi - lo) * (h - pad_t - pad_b)
    pts = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in points)
    area = f"{X(xs[0]):.1f},{h - pad_b} " + pts + f" {X(xs[-1]):.1f},{h - pad_b}"
    grid = []
    for k in range(4):
        v = lo + (hi - lo) * k / 3
        yy = Y(v)
        grid.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="var(--line)" stroke-width="1"/>'
                    f'<text x="{pad_l - 6}" y="{yy + 4:.1f}" text-anchor="end">{v / points[0][1] - 1:+.0%}</text>')
    labels = (f'<text x="{pad_l}" y="{h - 6}">{fmt_ts(xs[0], False)}</text>'
              f'<text x="{w - pad_r}" y="{h - 6}" text-anchor="end">{fmt_ts(xs[-1], False)}</text>')
    end = f'<circle cx="{X(xs[-1]):.1f}" cy="{Y(ys[-1]):.1f}" r="3.5" fill="var(--chart)"/>'
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Кривая капитала">{"".join(grid)}'
            f'<polygon points="{area}" fill="var(--accent-soft)"/>'
            f'<polyline points="{pts}" fill="none" stroke="var(--chart)" stroke-width="2"/>{end}{labels}</svg>')


def histogram(values: list[float], marker: float | None, w: int = 560, h: int = 150) -> str:
    """Распределение результатов случайных входов и где стратегия."""
    if not values:
        return ""
    lo, hi = min(values + ([marker] if marker is not None else [])), max(values + ([marker] if marker is not None else []))
    if hi - lo < 1e-9:
        hi = lo + 0.01
    nb = 24
    counts = [0] * nb
    for v in values:
        counts[min(nb - 1, int((v - lo) / (hi - lo) * nb))] += 1
    mx = max(counts)
    pad_l, pad_b, pad_t = 10, 24, 10
    bw = (w - 2 * pad_l) / nb
    bars = "".join(
        f'<rect x="{pad_l + i * bw + 1:.1f}" y="{h - pad_b - c / mx * (h - pad_b - pad_t):.1f}" width="{bw - 2:.1f}"'
        f' height="{c / mx * (h - pad_b - pad_t):.1f}" fill="var(--chart-2)"/>' for i, c in enumerate(counts) if c)
    mk = ""
    if marker is not None:
        xm = pad_l + (marker - lo) / (hi - lo) * (w - 2 * pad_l)
        mk = (f'<line x1="{xm:.1f}" x2="{xm:.1f}" y1="{pad_t}" y2="{h - pad_b}" stroke="var(--chart)" stroke-width="2.5"/>'
              f'<text x="{min(max(xm, 40), w - 40):.1f}" y="{pad_t + 10}" text-anchor="middle" style="fill:var(--chart)">стратегия</text>')
    axis = (f'<text x="{pad_l}" y="{h - 6}">{lo:+.0%}</text>'
            f'<text x="{w - pad_l}" y="{h - 6}" text-anchor="end">{hi:+.0%}</text>')
    return f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Случайные входы">{bars}{mk}{axis}</svg>'


def bars(items: list[tuple[str, float, str]]) -> str:
    if not items:
        return ""
    mx = max((v for _, v, _ in items), default=1) or 1
    return '<div class="bars">' + "".join(
        f'<div class="bar"><span>{e(k)}</span><i style="--w:{max(2, v / mx * 100):.0f}%"></i><em>{e(lbl)}</em></div>'
        for k, v, lbl in items) + "</div>"


def wallet_table(rows) -> str:
    body = []
    for r in rows:
        m = json.loads(r["metrics"] or "{}")
        fl = json.loads(r["flags"] or "{}")
        pf = m.get("profit_factor")
        sc = r["score"] or 0
        body.append(
            f'<tr><td class="mono" title="{e(r["address"])}">{e(short_addr(r["address"], 5))}</td>'
            f'<td>{e(r["label"] or "")}<span class="chip {"acc" if r["status"] == "watch" else ""}">{e(r["status"])}</span></td>'
            f'<td><div class="score"><i style="--w:{sc:.0f}%"></i><span class="mono">{sc:.0f}</span></div></td>'
            f'<td class="num">{m.get("closed", 0)}</td><td class="num">{fmt_pct(m.get("win_rate"))}</td>'
            f'<td class="num">{f"{pf:.2f}" if pf is not None else "—"}</td>'
            f'<td class="num {cls_sign(m.get("realized_pnl"))}">{fmt_usd(m.get("realized_pnl"), signed=True)}</td>'
            f'<td class="num {cls_sign(m.get("roi_capital"))}">{fmt_pct(m.get("roi_capital"), signed=True)}</td>'
            f'<td class="num">{fmt_pct(m.get("max_dd_norm"))}</td>'
            f'<td class="num">{fmt_dur((m.get("hold") or {}).get("p50"))}</td>'
            f'<td class="num">{fmt_pct(m.get("cex_volume_share"))}</td><td>{chips(fl)}</td></tr>')
    if not body:
        return '<p class="note">Пока нет отсканированных кошельков.</p>'
    return ('<div class="box scroll"><table><thead><tr><th>Кошелёк</th><th>Метка / статус</th><th>Балл</th>'
            '<th class="num">Сделок</th><th class="num">Win rate</th><th class="num">PF</th><th class="num">PnL</th>'
            '<th class="num">ROI</th><th class="num">Просадка</th><th class="num">Удержание</th><th class="num">На CEX</th>'
            '<th>Флаги</th></tr></thead><tbody>' + "".join(body) + "</tbody></table></div>")


def profile_card(addr: str, score, flags: dict, prof: dict, m: dict) -> str:
    pe, st, sel, en, ex, af = (prof.get(k, {}) for k in ("performance", "style", "selection", "entry", "exit", "after_entry"))
    kv = [("PnL", fmt_usd(m.get("realized_pnl"), signed=True)), ("Win rate", fmt_pct(m.get("win_rate"))),
          ("Profit factor", f"{m['profit_factor']:.2f}" if m.get("profit_factor") is not None else "—"),
          ("Просадка", fmt_pct(m.get("max_dd_norm"))), ("Удержание", fmt_dur(st.get("hold_p50"))),
          ("Сделок/мес", f"{(st.get('trades_per_month') or 0):.0f}")]
    kvh = "".join(f"<div><span>{e(k)}</span><b>{e(v)}</b></div>" for k, v in kv)
    types = [(ENTRY_RU.get(t, t), s, f"{s:.0%}") for t, s in (en.get("types") or {}).items()]
    secs = [(k, v, f"{v:.0%}") for k, v in list((sel.get("sectors") or {}).items())[:5]]
    fwd = []
    for h in ("1h", "24h", "3d", "7d"):
        s = af.get(h) or {}
        if s.get("n"):
            fwd.append((f"через {h}", abs(s["p50"]), f"{s['p50']:+.1%}"))
    return (f'<article class="box card"><h3>{e(st.get("label", "—")).capitalize()} · балл {e(score)}</h3>'
            f'<div class="addr">{e(addr)}</div><div>{chips(flags)}</div><div class="kv">{kvh}</div>'
            f'<div class="grid2" style="gap:10px"><div><span class="note">Тип входа</span>{bars(types)}</div>'
            f'<div><span class="note">Сектора</span>{bars(secs)}</div></div>'
            f'<span class="note">Медиана цены после входа</span>{bars(fwd)}'
            f'<div class="detect">{e(prof.get("detected_strategy", ""))}</div></article>')


def build_report(ctx, out: Path) -> Path:
    db = ctx.db
    counts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM wallets GROUP BY status")}
    rows = db.query("SELECT w.*, a.metrics, a.flags FROM wallets w LEFT JOIN analysis a ON a.chain=w.chain AND"
                    " a.address=w.address WHERE w.scanned_at IS NOT NULL AND w.status!='bot'"
                    " ORDER BY w.score DESC NULLS LAST LIMIT 40")
    bots = db.query("SELECT w.address, w.note, a.flags FROM wallets w LEFT JOIN analysis a ON a.chain=w.chain AND"
                    " a.address=w.address WHERE w.status='bot' ORDER BY w.scanned_at DESC LIMIT 12")
    watch = db.query("SELECT w.*, a.metrics, a.flags, a.profile FROM wallets w JOIN analysis a ON a.chain=w.chain AND"
                     " a.address=w.address WHERE a.profile IS NOT NULL ORDER BY (w.status='watch') DESC, w.score DESC LIMIT 8")
    strat_p = Path(ctx.cfg.strategy.path)
    strat = json.loads(strat_p.read_text(encoding="utf-8")) if strat_p.exists() else None
    bt = db.kv_get("backtest:last")
    sigs = db.query("SELECT * FROM signals ORDER BY id DESC LIMIT 40")
    pos = db.query("SELECT * FROM positions ORDER BY id DESC LIMIT 30")
    legs_n = db.one("SELECT COUNT(*) n FROM legs")["n"]
    tx_n = db.one("SELECT COUNT(*) n FROM seen_tx")["n"]

    parts = ['<meta charset="utf-8"><title>Радар умных кошельков</title>',
             '<link rel="preconnect" href="https://fonts.googleapis.com">'
             '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&'
             'family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">',
             f"<style>{CSS}</style>",
             '<header class="top"><div class="wrap"><span class="brand">Радар умных кошельков'
             f'<small>{e(ctx.cfg.cex.exchange)} {e(ctx.cfg.cex.market)} · {fmt_ts(now())} UTC</small></span>'
             '<nav><a href="#wallets">Кошельки</a><a href="#profiles">Профили</a><a href="#strategy">Стратегия</a>'
             '<a href="#backtest">Бэктест</a><a href="#signals">Сигналы</a><a href="#bots">Отсеяно</a></nav></div></header>',
             '<main class="wrap">',
             '<h1>Кого копировать и по каким правилам входить на CEX</h1>',
             '<p class="lead">Кошельки DEX, прошедшие фильтры на ботов, MEV и «одну удачную монету», их профили и сводная '
             'стратегия группы, проверенная на отложенных данных и на свечах биржи. Все цифры — история, а не обещание.</p>']
    strat_state = ("принята" if strat and strat.get("accepted") else ("не принята" if strat else "не построена"))
    strip = [("в наблюдении", counts.get("watch", 0)), ("отсканировано", counts.get("scanned", 0)),
             ("кандидатов", counts.get("candidate", 0)), ("отсеяно ботов", counts.get("bot", 0)),
             ("транзакций разобрано", tx_n), ("ног сделок", legs_n), ("стратегия", strat_state)]
    parts.append('<section class="strip">' + "".join(f"<div><b>{e(v)}</b><span>{e(k)}</span></div>" for k, v in strip) + "</section>")

    parts.append('<h2 id="wallets">Рейтинг кошельков <span class="hint">балл 0–100: прибыль, качество, риск, '
                 'устойчивость по окнам, выборка, разнообразие, доля оборота в токенах с листингом на CEX</span></h2>')
    parts.append(wallet_table(rows))

    parts.append('<h2 id="profiles">Профили стратегий <span class="hint">как кошелёк выбирает активы и момент входа</span></h2>')
    if watch:
        parts.append('<div class="cards">' + "".join(
            profile_card(r["address"], r["score"], json.loads(r["flags"] or "{}"), json.loads(r["profile"]),
                         json.loads(r["metrics"] or "{}")) for r in watch) + "</div>")
    else:
        parts.append('<p class="note">Профили строятся командой <span class="mono">wsa profile &lt;адрес&gt;</span>'
                     ' или автоматически при сборке стратегии.</p>')

    parts.append('<h2 id="strategy">Сводная стратегия</h2>')
    if strat:
        badge = '<span class="chip ok">принята</span>' if strat.get("accepted") else '<span class="chip hard">не принята — только наблюдение</span>'
        parts.append(f'<div class="box pad"><p>{badge} горизонт <b>{e(strat["horizon"])}</b>, группа из '
                     f'{len(strat["cohort"])} кошельков ({e(strat.get("entities"))} независимых владельцев), построена '
                     f'{fmt_ts(strat["built_at"])}</p><p class="note">{e(strat.get("summary_ru"))}</p></div>')
        rules = strat["wallet_trigger"]["rules"] + strat["scanner"]["rules"]
        if rules:
            body = "".join(
                f'<tr><td class="mono">{e(r["id"])}</td><td>{"<span class=\'chip ok\'>принято</span>" if r["accepted"] else "<span class=\'chip\'>нет</span>"}</td>'
                f'<td>{e(r["text"])}</td><td class="num">{r["train"].get("n")}</td>'
                f'<td class="num {cls_sign(r["train"].get("mean"))}">{fmt_pct(r["train"].get("mean"), signed=True)}</td>'
                f'<td class="num">{r["test"].get("n", 0)}</td>'
                f'<td class="num {cls_sign(r["test"].get("mean"))}">{fmt_pct(r["test"].get("mean"), signed=True)}</td>'
                f'<td class="num">{fmt_pct(r["test"].get("hit"))}</td><td class="note">{e("" if r["accepted"] else r["reason"])}</td></tr>'
                for r in rules)
            parts.append('<h3 style="margin-top:18px">Правила: найдены на первых 70% истории, проверены на последних 30%</h3>'
                         '<div class="box scroll"><table><thead><tr><th>id</th><th></th><th>Условие</th>'
                         '<th class="num">n обуч.</th><th class="num">обучение</th><th class="num">n пров.</th>'
                         '<th class="num">проверка</th><th class="num">прибыльных</th><th>Почему не принято</th></tr></thead><tbody>'
                         + body + "</tbody></table></div>")
        sig = strat.get("signature") or []
        if sig:
            body = "".join(
                f'<tr><td>{e(x["name"])}</td><td class="num">{e(x.get("entries_txt"))}</td>'
                f'<td class="num">{e(x.get("baseline_txt"))}</td><td class="num">{x["effect"]:+.2f}</td></tr>' for x in sig[:14])
            parts.append('<h3 style="margin-top:18px">Подпись отбора: чем моменты входа отличаются от случайных моментов в тех же токенах</h3>'
                         '<div class="box scroll"><table><thead><tr><th>Признак</th><th class="num">у входов</th>'
                         '<th class="num">случайно</th><th class="num">отличие</th></tr></thead><tbody>' + body + "</tbody></table></div>"
                         '<p class="note">Отличие — вероятность, что у входа признак больше, чем у случайного момента, минус 0.5 '
                         '(для долей — разница долей). ±0.1 и сильнее — устойчивая привычка группы.</p>')
        ex = strat["exits"]
        parts.append(f'<p class="note">Выходы: стоп {fmt_pct(ex["sl"])}, тейки '
                     + ", ".join(f"{fmt_pct(t, signed=True)} на {f:.0%} позиции" for t, f in ex["tp"])
                     + f', тайм-стоп {ex["time_stop_sec"] // 3600} ч; после первого тейка стоп в безубыток и трейлинг.</p>')
    else:
        parts.append('<p class="note">Стратегия ещё не собрана: <span class="mono">wsa strategy build</span> после периода наблюдения.</p>')

    parts.append('<h2 id="backtest">Бэктест на свечах CEX <span class="hint">только проверочный период, комиссии и '
                 'проскальзывание включены, стоп раньше тейка внутри свечи</span></h2>')
    if bt and bt.get("summary"):
        sm, rb = bt["summary"], bt.get("random") or {}
        parts.append('<div class="grid2"><div class="box pad"><h3>Капитал</h3>'
                     + spark_equity([tuple(p) for p in bt.get("curve", [])]) + '</div><div class="box pad"><h3>Против случайных входов</h3>'
                     + histogram(rb.get("all") or [], sm.get("total_return"))
                     + f'<p class="note">Стратегия лучше {fmt_pct(bt.get("beats_random_share"))} из {rb.get("trials", 0)} прогонов '
                       f'со случайным временем входа в те же токены (медиана случайных {fmt_pct(rb.get("median"), signed=True)}).</p></div></div>')
        parts.append('<section class="strip">' + "".join(f"<div><b>{e(v)}</b><span>{e(k)}</span></div>" for k, v in [
            ("сделок", sm.get("n")), ("прибыльных", fmt_pct(sm.get("win_rate"))), ("средняя", fmt_pct(sm.get("avg_pct"), signed=True)),
            ("итог", fmt_pct(sm.get("total_return"), signed=True)), ("макс. просадка", fmt_pct(sm.get("max_dd"))),
            ("на сделку", fmt_pct(sm.get("alloc_per_trade")))]) + "</section>")
    else:
        parts.append('<p class="note">Бэктест ещё не запускался: <span class="mono">wsa backtest</span>.</p>')

    parts.append('<h2 id="signals">Сигналы и позиции</h2>')
    if sigs:
        body = "".join(
            f'<tr><td class="mono">{fmt_ts(s["ts"])}</td><td>{e(s["kind"])}</td><td class="mono">{e(short_addr(s["wallet"] or "—"))}</td>'
            f'<td class="mono">{e(s["symbol"] or "—")}</td><td class="mono">{e(s["rule"] or "")}</td>'
            f'<td><span class="chip {"ok" if s["status"] == "executed" else ("acc" if s["status"] in ("new", "observed") else "")}">{e(s["status"])}</span></td>'
            f'<td class="note">{e(s["reason"])}</td></tr>' for s in sigs)
        parts.append('<div class="box scroll"><table><thead><tr><th>Время</th><th>Источник</th><th>Лидер</th><th>Тикер</th>'
                     '<th>Правило</th><th>Статус</th><th>Причина</th></tr></thead><tbody>' + body + "</tbody></table></div>")
    else:
        parts.append('<p class="note">Сигналов пока нет — они появятся, когда трекер (<span class="mono">wsa track</span>) увидит покупки лидеров.</p>')
    if pos:
        body = "".join(
            f'<tr><td class="mono">{p["id"]}</td><td>{e(p["mode"])}</td><td class="mono">{e(p["symbol"])}</td>'
            f'<td class="mono">{fmt_ts(p["opened_at"])}</td><td class="num">{p["entry_price"]:.6g}</td>'
            f'<td class="num {cls_sign((p["realized_usd"] or 0) - (p["fees_usd"] or 0))}">{fmt_usd((p["realized_usd"] or 0) - (p["fees_usd"] or 0), signed=True)}</td>'
            f'<td>{e(p["status"])}</td><td class="note">{e(p["exit_reason"] or "")}</td></tr>' for p in pos)
        parts.append('<h3 style="margin-top:18px">Позиции</h3><div class="box scroll"><table><thead><tr><th>#</th><th>Режим</th>'
                     '<th>Тикер</th><th>Открыта</th><th class="num">Вход</th><th class="num">Результат</th><th>Статус</th>'
                     '<th>Выход</th></tr></thead><tbody>' + body + "</tbody></table></div>")

    parts.append('<h2 id="bots">Отсеяно фильтрами <span class="hint">за такими кошельками нельзя следовать с CEX</span></h2>')
    if bots:
        body = "".join(
            f'<tr><td class="mono">{e(short_addr(b["address"], 6))}</td><td>{chips(json.loads(b["flags"] or "{}"))}</td>'
            f'<td class="note">{e("; ".join(v["msg"] for v in json.loads(b["flags"] or "{}").values()) or b["note"])}</td></tr>'
            for b in bots)
        parts.append('<div class="box scroll"><table><thead><tr><th>Кошелёк</th><th>Флаги</th><th>Почему</th></tr></thead>'
                     '<tbody>' + body + "</tbody></table></div>")
    parts.append('<footer>Данные: DexScreener, GeckoTerminal, Solana RPC, Blockscout/Etherscan, CoinGecko, свечи '
                 f'{e(ctx.cfg.cex.reference_exchange)} через ccxt. Не является финансовой рекомендацией: прошлые результаты '
                 'кошельков и бэктеста не гарантируют будущих.</footer></main>')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    return out
