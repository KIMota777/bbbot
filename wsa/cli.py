"""Командная строка: `./wsa.sh <команда>` (или `.venv/bin/python -m wsa <команда>`)."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import ROOT, load_config, secret
from .util import fmt_dur, fmt_pct, fmt_ts, fmt_usd, now, short_addr, table

log = logging.getLogger("wsa")


def _ctx(args):
    from .context import Ctx
    cfg = load_config(args.config)
    if getattr(args, "verbose", False):
        cfg.general.log_level = "DEBUG"
    ctx = Ctx(cfg)
    for noisy in ("urllib3", "ccxt"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return ctx


def _resolve_wallet(ctx, addr: str, chain: str | None = None):
    w = ctx.db.wallet(chain, addr) if chain else ctx.db.find_wallet(addr)
    if not w:
        sys.exit(f"кошелёк {addr} не найден в базе (добавьте: wsa import {addr})")
    return w


# ---------------- команды ----------------

def cmd_init(args):
    cfg_path = ROOT / "wsa.toml"
    if not cfg_path.exists():
        cfg_path.write_text((ROOT / "wsa" / "config.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
        print(f"создан {cfg_path}")
    env = ROOT / ".env"
    if not env.exists():
        env.write_text((ROOT / "wsa" / "env.example").read_text(encoding="utf-8"), encoding="utf-8")
        print(f"создан {env} — впишите ключи (необязательно)")
    ctx = _ctx(args)
    print(f"база: {ctx.cfg.general.db_path}")


def cmd_doctor(args):
    ctx = _ctx(args)
    rows = []

    def check(name, fn):
        try:
            rows.append([name, "ok", fn()])
        except Exception as e:
            rows.append([name, "ОШИБКА", str(e)[:90]])

    check("DexScreener", lambda: ctx.ds.pair("solana", "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE")["baseToken"]["symbol"] + "/USDC")
    check("GeckoTerminal", lambda: f"{len(ctx.gt.pool_trades('solana', 'Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE', 50000))} сделок ≥$50k за сутки")
    for ep in ctx.rpc.endpoints:
        check(f"RPC {ep.name}", lambda ep=ep: f"slot {ctx.http.post_json(ep.url, {'jsonrpc': '2.0', 'id': 1, 'method': 'getSlot'}, limiter_key=ep.key)['result']}")
    check(f"CEX {ctx.cfg.cex.exchange} {ctx.cfg.cex.market}", lambda: f"{len(ctx.cex.symbols())} рынков, SOL {ctx.cex.last_price('SOL/USDT' if ctx.cfg.cex.market == 'spot' else 'SOL/USDT:USDT')}")
    check("CoinGecko", lambda: ctx.http.get_json("https://api.coingecko.com/api/v3/ping").get("gecko_says", "ok"))
    print(table(rows, ["источник", "статус", "ответ"]))
    keys = [[k, "есть" if secret(k) else "—"] for k in ("HELIUS_API_KEY", "BIRDEYE_API_KEY", "BLOCKSCOUT_API_KEY",
                                                         "ETHERSCAN_API_KEY", "COINGECKO_API_KEY",
                                                         f"{ctx.cfg.cex.exchange.upper()}_API_KEY")]
    print()
    print(table(keys, ["ключ", "статус"]))
    if not secret("HELIUS_API_KEY"):
        print("\nСовет: бесплатный ключ Helius (helius.dev) ускоряет загрузку истории в 3–10 раз.")


def cmd_discover(args):
    from .pipeline import discover as d
    ctx = _ctx(args)
    if args.birdeye:
        print(d.from_birdeye(ctx, args.chain, args.birdeye, args.frame))
        return
    if args.gainers:
        print(d.from_birdeye_gainers(ctx, args.chain, args.period))
        return
    targets = list(args.targets)
    if args.universe:
        uni = d.universe_tokens(ctx, args.chain, args.universe_limit)
        print(f"вселенная DEX+CEX ({ctx.cfg.cex.exchange}): {len(uni)} токенов")
        for u in uni:
            print(f"  {u['symbol']:<10} {u['cex_symbol']:<16} ликв. {fmt_usd(u['liquidity_usd']):>9}  пул {u['best_pair']}")
        targets += [f"{args.chain}:{u['best_pair']}" for u in uni]
    if not targets:
        sys.exit("укажите ссылку DexScreener / адрес пула / --universe / --birdeye TOKEN")
    minutes = args.minutes if args.minutes is not None else ctx.cfg.discovery.minutes
    for t in targets:
        chain, pool, pair = d.resolve_pool(ctx, t)
        name = f"{pair['baseToken']['symbol']}/{pair['quoteToken']['symbol']} ({pair.get('dexId')})"
        print(f"\n{name}: пул {pool}, опрос {minutes} мин, сделки ≥ {fmt_usd(args.min_usd)}")

        def prog(r, w, t):
            print(f"\r  раунд {r}: кошельков {w}, сделок {t}", end="", flush=True)

        res = d.sample_pool(ctx, chain, pool, minutes, args.interval, args.min_usd, progress=prog)
        print(f"\n  итого: {res['wallets']} кошельков ({res['new']} новых), {res['trades']} сделок"
              f" за {fmt_dur(res['span_sec'])} реального времени")


def cmd_universe(args):
    from .pipeline.discover import universe_tokens
    ctx = _ctx(args)
    uni = universe_tokens(ctx, args.chain, args.limit)
    print(table([[u["symbol"], u["cex_symbol"], fmt_usd(u["liquidity_usd"]), fmt_usd(u["vol24"]),
                  fmt_usd(u["cex_turnover_24h"]), u["token"]] for u in uni],
                ["токен", "тикер CEX", "ликв. DEX", "объём DEX 24ч", "оборот CEX 24ч", "адрес"], "llrrrl"))


def cmd_import(args):
    from .pipeline.discover import import_wallets
    ctx = _ctx(args)
    addrs = []
    for t in args.targets:
        p = Path(t)
        if p.exists():
            addrs += [x.split()[0] for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        else:
            addrs.append(t)
    n = import_wallets(ctx, args.chain, addrs, label=args.label)
    print(f"добавлено новых: {n} из {len(addrs)}")


def cmd_scan(args):
    from .pipeline.scan import scan_wallet
    ctx = _ctx(args)
    if args.addresses:
        todo = [_resolve_wallet(ctx, a, args.chain if args.chain != "auto" else None) for a in args.addresses]
    else:
        # сначала похожие на людей: 1–2 пула и несколько сделок за выборку. Замер на
        # вселенной Bybit (сент. 2026): все кошельки из 3+ пулов оказались арбитражными
        # ботами (30 тыс.–1.3 млн транзакций в сутки) — арбитраж ходит по многим пулам
        order = {"human": "(json_array_length(COALESCE(seen_pools, '[]')) > 2), (seen_trades > 5),"
                          " seen_volume_usd DESC",
                 "volume": "seen_volume_usd DESC"}[args.order]
        q = f"SELECT * FROM wallets WHERE status=? ORDER BY {order} LIMIT ?"
        todo = ctx.db.query(q, (args.status, args.limit))
    if not todo:
        print("нет кошельков для скана")
        return
    for i, w in enumerate(todo, 1):
        t0 = now()

        def prog(d, n):
            print(f"\r  [{i}/{len(todo)}] {short_addr(w['address'])}: транзакций {d}/{n}", end="", flush=True)

        try:
            res = scan_wallet(ctx, w["chain"], w["address"], depth=args.depth, progress=prog)
        except Exception as e:
            print(f"\n  [{i}/{len(todo)}] {short_addr(w['address'])}: ошибка {e}")
            continue
        m = res.get("metrics") or {}
        fl = ",".join(k for k in (res.get("flags") or {}))
        if res["status"] == "bot":
            print(f"\r  [{i}/{len(todo)}] {short_addr(w['address'])}: БОТ ({res['sigs_per_day']:.0f} tx/сутки)   ")
        else:
            print(f"\r  [{i}/{len(todo)}] {short_addr(w['address'])}: балл {res['score']:>5}  сделок {m.get('closed', 0):>3}"
                  f"  PnL {fmt_usd(m.get('realized_pnl'), signed=True):>9}  WR {fmt_pct(m.get('win_rate')):>6}"
                  f"  CEX {fmt_pct(m.get('cex_volume_share')):>6}  {fl}  ({now() - t0}с)")


def _flag_short(flags: dict) -> str:
    names = {"bot_frequency": "бот", "bot_swaps": "бот", "hft_hold": "HFT", "mev": "MEV", "custom_program": "контракт",
             "low_sample": "мало", "one_hit": "1токен", "unmatched": "инсайд?", "short_history": "молодой",
             "cex_irrelevant": "неCEX", "unprofitable": "убыток"}
    return ",".join(dict.fromkeys(names.get(k, k) for k in flags))


def cmd_top(args):
    ctx = _ctx(args)
    q = "SELECT w.*, a.metrics, a.flags FROM wallets w LEFT JOIN analysis a ON a.chain=w.chain AND a.address=w.address"
    q += " WHERE w.scanned_at IS NOT NULL"
    if not args.all:
        q += " AND w.status NOT IN ('bot')"
    q += " ORDER BY w.score DESC NULLS LAST LIMIT ?"
    rows = []
    for i, r in enumerate(ctx.db.query(q, (args.n,)), 1):
        m = json.loads(r["metrics"] or "{}")
        fl = json.loads(r["flags"] or "{}")
        pf = m.get("profit_factor")
        rows.append([i, short_addr(r["address"], 5), r["label"] or "", r["status"], r["score"], m.get("closed"),
                     fmt_pct(m.get("win_rate")), ("∞" if pf >= 99 else f"{pf:.2f}") if pf is not None else "—",
                     fmt_usd(m.get("realized_pnl"), signed=True), fmt_pct(m.get("roi_capital"), signed=True),
                     fmt_pct(m.get("max_dd_norm")), fmt_dur((m.get("hold") or {}).get("p50")),
                     fmt_pct(m.get("cex_volume_share")), _flag_short(fl)])
    print(table(rows, ["#", "кошелёк", "метка", "статус", "балл", "сделок", "WR", "PF", "PnL", "ROI", "DD",
                       "удерж.", "CEX", "флаги"], "rllrrrrrrrrrrl"))
    tot = ctx.db.query("SELECT status, COUNT(*) n FROM wallets GROUP BY status")
    print("\nв базе: " + ", ".join(f"{r['status']} {r['n']}" for r in tot))


def cmd_profile(args):
    from .core.profile import render_card
    from .pipeline.enrich import enrich_wallet
    from .pipeline.scan import scan_wallet
    ctx = _ctx(args)
    w = _resolve_wallet(ctx, args.address)
    a = ctx.db.analysis(w["chain"], w["address"])
    if a is None:
        print("кошелёк ещё не сканировался — быстрый скан…")
        scan_wallet(ctx, w["chain"], w["address"], depth="quick")
        a = ctx.db.analysis(w["chain"], w["address"])
    if args.refresh or not (a or {}).get("profile"):
        print("обогащение сделок (цены до/после входа, сектор, листинг на CEX)…")
        enrich_wallet(ctx, w["chain"], w["address"])
        a = ctx.db.analysis(w["chain"], w["address"])
    if args.json:
        print(json.dumps(a, ensure_ascii=False, indent=1, default=str))
        return
    print(render_card(w["address"], a.get("score"), a.get("flags") or {}, a.get("profile"), a.get("metrics") or {}))


def cmd_watch(args):
    ctx = _ctx(args)
    if args.action == "list":
        rows = [[short_addr(w["address"], 6), w["chain"], w["label"] or "", w["score"]] for w in ctx.db.wallets("watch")]
        print(table(rows, ["кошелёк", "сеть", "метка", "балл"]) if rows else "список наблюдения пуст")
        return
    if args.action == "auto":
        rows = ctx.db.query("SELECT w.*, a.flags FROM wallets w JOIN analysis a ON a.chain=w.chain AND a.address=w.address"
                            " WHERE w.status IN ('scanned','watch') AND w.score >= ? ORDER BY w.score DESC LIMIT ?",
                            (args.min_score, args.top))
        n = 0
        for r in rows:
            fl = json.loads(r["flags"] or "{}")
            if any(v["level"] == "hard" for v in fl.values()):
                continue
            ctx.db.set_wallet(r["chain"], r["address"], status="watch")
            n += 1
        print(f"в наблюдении: +{n} (порог балла {args.min_score})")
        return
    for a in args.addresses:
        w = ctx.db.find_wallet(a)
        if not w:
            if args.action == "add":
                ctx.db.upsert_candidate(args.chain, a, source="manual", label=args.label)
                w = ctx.db.wallet(args.chain, a)
            else:
                print(f"{a}: нет в базе")
                continue
        if args.action == "add":
            ctx.db.set_wallet(w["chain"], w["address"], status="watch", **({"label": args.label} if args.label else {}))
            print(f"{short_addr(w['address'])}: в наблюдении")
        else:
            ctx.db.set_wallet(w["chain"], w["address"], status="scanned")
            print(f"{short_addr(w['address'])}: убран из наблюдения")


def _confirm_live(mode: str):
    if mode != "live":
        return
    if not sys.stdin.isatty():
        sys.exit("режим live требует подтверждения в интерактивной консоли")
    print("⚠ РЕАЛЬНЫЕ ДЕНЬГИ. Бот будет отправлять ордера на биржу по вашим ключам.")
    if input("Напечатайте LIVE для подтверждения: ").strip() != "LIVE":
        sys.exit("отменено")


def cmd_track(args):
    from .core.strategy import Strategy
    from .pipeline.track import run
    ctx = _ctx(args)
    strategy = Strategy.load(ctx.cfg.strategy.path)
    mode = args.trade
    _confirm_live(mode or "")
    run(ctx, strategy, mode, once=args.once, max_loops=args.loops)


def cmd_strategy(args):
    from .core.strategy import Strategy, build
    from .pipeline.enrich import enrich_wallet
    ctx = _ctx(args)
    if args.action == "show":
        s = Strategy.load(ctx.cfg.strategy.path)
        if not s:
            sys.exit("стратегия ещё не построена: wsa strategy build")
        _print_strategy(s.d)
        return
    cohort_rows = ctx.db.wallets("watch")
    if not cohort_rows:
        cohort_rows = [w for w in ctx.db.wallets("scanned") if (w["score"] or 0) >= ctx.cfg.trading.min_wallet_score]
        print(f"список наблюдения пуст — беру отсканированные с баллом ≥ {ctx.cfg.trading.min_wallet_score}: {len(cohort_rows)}")
    if not cohort_rows:
        sys.exit("нет кошельков для стратегии")
    cohort = []
    for w in cohort_rows:
        a = ctx.db.analysis(w["chain"], w["address"]) or {}
        stale = not a.get("profile") or args.refresh
        n_entries = ctx.db.one("SELECT COUNT(*) n FROM entries WHERE chain=? AND wallet=?", (w["chain"], w["address"]))["n"]
        if stale or not n_entries:
            print(f"  обогащаю {short_addr(w['address'])}…")
            enrich_wallet(ctx, w["chain"], w["address"])
            a = ctx.db.analysis(w["chain"], w["address"]) or {}
        m = a.get("metrics") or {}
        cohort.append({"chain": w["chain"], "address": w["address"], "score": w["score"],
                       "hold_p50": (m.get("hold") or {}).get("p50")})
    s = build(ctx, cohort, horizon=args.horizon, cex_only=not args.all_tokens)
    if not s.get("ok"):
        sys.exit(f"стратегия не построена: {s.get('reason')}")
    Strategy(s).save(ctx.cfg.strategy.path)
    _print_strategy(s)
    print(f"\nсохранено: {ctx.cfg.strategy.path}")


def _print_strategy(s: dict):
    print(f"Стратегия от {fmt_ts(s['built_at'])} | биржа {s['exchange']} {s['market']} | горизонт {s['horizon']}")
    print(f"Принята: {'ДА' if s.get('accepted') else 'НЕТ'}")
    print()
    print(s.get("summary_ru", ""))
    rules = s["wallet_trigger"]["rules"]
    if rules:
        print(f"\nПравила входа за кошельком (проверено {s['wallet_trigger']['n_tested']} вариантов):")
        rows = [[r["id"], "✓" if r["accepted"] else "✗", r["text"][:70], r["train"].get("n"),
                 fmt_pct(r["train"].get("mean"), signed=True), fmt_pct(r["test"].get("mean"), signed=True),
                 f"{(r['test'].get('hit') or 0):.0%}", r["test"].get("n"), "" if r["accepted"] else r["reason"][:40]]
                for r in rules]
        print(table(rows, ["id", "", "условие", "n обуч", "обуч", "проверка", "приб.", "n пров", "почему нет"], "lllrrrrrl"))
    sig = s.get("signature") or []
    if sig:
        print("\nПодпись отбора — чем моменты входа отличаются от случайных (сильнейшие отличия):")
        rows = [[x["name"], x.get("entries_txt", "—"), x.get("baseline_txt", "—"), f"{x['effect']:+.2f}"] for x in sig[:10]]
        print(table(rows, ["признак", "у входов", "случайно", "отличие"], "lrrr"))


def cmd_backtest(args):
    from .core.backtest import backtest_strategy
    from .core.strategy import Strategy
    ctx = _ctx(args)
    s = Strategy.load(ctx.cfg.strategy.path)
    if not s:
        sys.exit("нет стратегии: wsa strategy build")
    r = backtest_strategy(ctx, s.d, only_test=not args.all, tf=args.tf)
    ctx.db.kv_set("backtest:last", {k: v for k, v in r.items() if k != "trades"} | {"trades": r["trades"][-200:], "ts": now()})
    sm = r["summary"]
    print(f"Бэктест на свечах {ctx.cfg.cex.exchange} {args.tf} ({'только проверочный период' if not args.all else 'вся история'})")
    print(f"сигналов {r['signals']}, сделок {sm['n']}, прибыльных {fmt_pct(sm.get('win_rate'))}, "
          f"средняя {fmt_pct(sm.get('avg_pct'), signed=True)}, PF {sm.get('profit_factor') or 0:.2f}")
    print(f"итог {fmt_pct(sm.get('total_return'), signed=True)} при {fmt_pct(sm.get('alloc_per_trade'))} капитала на сделку,"
          f" макс. просадка {fmt_pct(sm.get('max_dd'))}, выходы: {sm.get('exit_reasons')}")
    rb = r.get("random") or {}
    if rb:
        print(f"случайные входы в те же токены ({rb['trials']} прогонов): медиана {fmt_pct(rb.get('median'), signed=True)},"
              f" 90-й перцентиль {fmt_pct(rb.get('p90'), signed=True)}")
        print(f"стратегия лучше {fmt_pct(r.get('beats_random_share'))} случайных прогонов")


def cmd_trade(args):
    from .core.strategy import Strategy
    from .trading.engine import Engine
    ctx = _ctx(args)
    mode = args.mode
    if args.action == "check":
        from .trading.broker import CcxtBroker
        b = CcxtBroker(ctx, mode if mode != "paper" else "live")
        info = b.check()
        print(json.dumps(info, ensure_ascii=False, indent=1, default=str))
        if info.get("withdraw_enabled"):
            print("⚠ У ключа есть право вывода средств — создайте ключ без него.")
        return
    if args.action == "test":
        # проверка связки с биржей: крошечная покупка -> страховочный стоп -> отмена -> продажа
        from .trading.broker import make_broker
        if mode == "paper":
            sys.exit("проверка имеет смысл на бирже: --mode demo (демо-счёт) или testnet; live — на свой риск")
        _confirm_live(mode)
        b = make_broker(ctx, mode)
        sym = args.symbol or ("SOL/USDT" if ctx.cfg.cex.market == "spot" else "SOL/USDT:USDT")
        print(f"{mode}: покупка {sym} на ${args.usd:.0f}…")
        f = b.buy_usd(sym, args.usd)
        print(f"  куплено {f.qty} по {f.price} (комиссия ${f.fee_usd:.4f}, ордер {f.order_id})")
        sid = b.place_stop(sym, f.qty, f.price * 0.9)
        print(f"  страховочный стоп −10%: {'выставлен ' + str(sid) if sid else 'не выставлен (стоп будет вести бот)'}")
        if sid:
            b.cancel(sym, sid)
            print("  стоп отменён")
        f2 = b.sell_qty(sym, f.qty)
        print(f"  продано {f2.qty} по {f2.price} (комиссия ${f2.fee_usd:.4f})")
        print(f"итог проверки: {(f2.price - f.price) * f2.qty - f.fee_usd - f2.fee_usd:+.4f} USDT — связка работает")
        return
    s = Strategy.load(ctx.cfg.strategy.path) or Strategy({"exits": {"sl": -0.1, "tp": [], "time_stop_sec": 86400}})
    if args.action == "reset":
        Engine(ctx, s, mode).risk.reset()
        print("стоп-краны сброшены")
        return
    _confirm_live(mode)
    eng = Engine(ctx, s, mode)
    if args.action == "close":
        for p in eng.open_positions():
            if not args.symbol or p["symbol"] == args.symbol:
                eng.close(p, p["qty_open"], "вручную")
        return
    st = eng.status()
    print(f"режим {st['mode']}: капитал {fmt_usd(st['equity'])}, закрыто сделок {st['closed']}, "
          f"результат {fmt_usd(st['realized'], signed=True)}, прибыльных {fmt_pct(st['win_rate'])}")
    r = st["risk"]
    if r.get("halted") or r.get("paused_until", 0) > now():
        print(f"⚠ стоп-кран: {r.get('halt_reason') or 'пауза'}")
    rows = [[p["id"], p["symbol"], fmt_ts(p["opened_at"]), f"{p['entry_price']:.6g}", f"{p['sl']:.6g}",
             f"{p['qty_open']:.6g}", short_addr(p["leader"] or "сканер")] for p in st["open"]]
    if rows:
        print(table(rows, ["#", "тикер", "вход", "цена", "стоп", "кол-во", "лидер"], "rlllrrl"))


def cmd_research(args):
    from .pipeline.research import run
    ctx = _ctx(args)
    run(ctx, loop=args.loop, cycles=args.cycles, scan_minutes=args.scan_minutes, deep_per_cycle=args.deep,
        min_score=args.min_score, auto_watch=not args.no_auto_watch, discover_hours=args.discover_hours)


def cmd_site(args):
    from .report.site import export
    ctx = _ctx(args)
    p = export(ctx, args.out)
    print(f"снимок для сайта: {p}")
    print("на crypt.burbey.ru он попадёт с коммитом этого файла (сервер пересоберёт сайт сам)")


def cmd_report(args):
    from .report.html import build_report
    ctx = _ctx(args)
    out = Path(args.out) if args.out else Path(ctx.cfg.general.db_path).parent / "report.html"
    build_report(ctx, out)
    print(f"отчёт: {out}")
    if args.open:
        import webbrowser
        webbrowser.open(out.as_uri())


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wsa", description="Wallet Strategy Analyzer: умные кошельки DEX -> стратегия для CEX")
    ap.add_argument("--config", help="путь к wsa.toml (по умолчанию — в корне bbbot)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="создать wsa.toml, .env и базу").set_defaults(fn=cmd_init)
    sub.add_parser("doctor", help="проверить доступ к источникам и ключи").set_defaults(fn=cmd_doctor)

    p = sub.add_parser("discover", help="найти кандидатов в пулах (ссылка DexScreener, пул, токен)")
    p.add_argument("targets", nargs="*")
    p.add_argument("--chain", default="solana")
    p.add_argument("--minutes", type=float)
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--min-usd", type=float, default=1000)
    p.add_argument("--universe", action="store_true", help="пулы всех токенов, которые есть и на DEX, и на нашей CEX")
    p.add_argument("--universe-limit", type=int, default=20)
    p.add_argument("--birdeye", metavar="TOKEN", help="топ трейдеров токена по PnL (нужен BIRDEYE_API_KEY)")
    p.add_argument("--frame", default="30d")
    p.add_argument("--gainers", action="store_true", help="лидеры сети по PnL (Birdeye)")
    p.add_argument("--period", default="30d")
    p.set_defaults(fn=cmd_discover)

    p = sub.add_parser("universe", help="токены, которые торгуются и на DEX, и на CEX")
    p.add_argument("--chain", default="solana")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_universe)

    p = sub.add_parser("import", help="добавить кошельки (адреса или файл, напр. из Top Traders DexScreener)")
    p.add_argument("targets", nargs="+")
    p.add_argument("--chain", default="solana")
    p.add_argument("--label")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("scan", help="загрузить историю, посчитать метрики и балл")
    p.add_argument("addresses", nargs="*")
    p.add_argument("--chain", default="auto")
    p.add_argument("--depth", choices=["quick", "deep"], default="quick")
    p.add_argument("--status", default="candidate")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--order", choices=["human", "volume"], default="human")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("top", help="рейтинг кошельков")
    p.add_argument("--n", type=int, default=25)
    p.add_argument("--all", action="store_true", help="включая ботов")
    p.set_defaults(fn=cmd_top)

    p = sub.add_parser("profile", help="карточка стратегии кошелька")
    p.add_argument("address")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_profile)

    p = sub.add_parser("watch", help="список наблюдения: add | rm | list | auto")
    p.add_argument("action", choices=["add", "rm", "list", "auto"])
    p.add_argument("addresses", nargs="*")
    p.add_argument("--chain", default="solana")
    p.add_argument("--label")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--min-score", type=float, default=55)
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("track", help="живое слежение (+ торговля с --trade)")
    p.add_argument("--trade", choices=["paper", "demo", "testnet", "live"])
    p.add_argument("--once", action="store_true")
    p.add_argument("--loops", type=int)
    p.set_defaults(fn=cmd_track)

    p = sub.add_parser("strategy", help="build | show — сводная стратегия группы")
    p.add_argument("action", choices=["build", "show"])
    p.add_argument("--horizon", default="auto", choices=["auto", "6h", "24h", "3d", "7d"])
    p.add_argument("--all-tokens", action="store_true", help="учитывать и токены без листинга на CEX")
    p.add_argument("--refresh", action="store_true", help="пересчитать обогащение кошельков")
    p.set_defaults(fn=cmd_strategy)

    p = sub.add_parser("backtest", help="бэктест стратегии на свечах CEX")
    p.add_argument("--tf", default="15m", choices=["15m", "1h"])
    p.add_argument("--all", action="store_true", help="вся история, а не только проверочный период")
    p.set_defaults(fn=cmd_backtest)

    p = sub.add_parser("trade", help="status | reset | close | check | test — управление торговлей")
    p.add_argument("action", choices=["status", "reset", "close", "check", "test"])
    p.add_argument("symbol", nargs="?")
    p.add_argument("--mode", default="paper", choices=["paper", "demo", "testnet", "live"])
    p.add_argument("--usd", type=float, default=10.0, help="сумма тестовой сделки (trade test)")
    p.set_defaults(fn=cmd_trade)

    p = sub.add_parser("research", help="автопилот: поиск -> скан -> профили -> наблюдение -> стратегия")
    p.add_argument("--loop", action="store_true", help="работать непрерывно")
    p.add_argument("--cycles", type=int)
    p.add_argument("--scan-minutes", type=float, help="бюджет времени на быстрый скан за цикл")
    p.add_argument("--deep", type=int, help="глубоких сканов за цикл")
    p.add_argument("--min-score", type=float)
    p.add_argument("--discover-hours", type=float)
    p.add_argument("--no-auto-watch", action="store_true", help="не добавлять в наблюдение автоматически")
    p.set_defaults(fn=cmd_research)

    p = sub.add_parser("site", help="снимок для страницы /wallets сайта (webapp/data/wallets.json)")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_site)

    p = sub.add_parser("report", help="HTML-отчёт")
    p.add_argument("--out")
    p.add_argument("--open", action="store_true")
    p.set_defaults(fn=cmd_report)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        print("\nостановлено")
    except RuntimeError as e:
        sys.exit(str(e))
