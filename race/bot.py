"""One tick of the race. Run every 5 minutes by GitHub Actions. Pretend money only; no broker logins, no real orders."""
import os, sys, time, json, shutil
from datetime import timedelta
from .util import HERE, load, save, now_ts, et, market_open, Health
from .market import Live, Replay, is_coin
from .news import News
from .ai import AI
from .alerts import Alerts
from .lanes import Runner, new_lane, value

DATA = os.environ.get("RACE_DATA", os.path.join(os.path.dirname(HERE), "data"))
BRIEF_EVENTS = ("CPI", "Non-Farm", "Nonfarm", "Federal Funds", "FOMC", "Unemployment Rate", "PCE", "Retail Sales")


def next_up():
    n = et()
    def nxt(hour, minute, weekdays):
        for d in range(0, 8):
            c = (n + timedelta(days=d)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            if c > n and c.weekday() in weekdays:
                return c
    items = [(n + timedelta(minutes=5), "Every 5 minutes", "Price and exit checks, all lanes"),
             (nxt(8, 30, range(5)), None, "Morning brief sets the risk mode"),
             (nxt(9, 0, range(7)), None, "Bitcoin trend check (lane D)"),
             (nxt(10, 0, range(5)), None, "Lane C scans for breakouts, hourly until 3:00 PM"),
             (nxt(15, 45, range(5)), None, "Trend check for lanes A and B"),
             (nxt(17, 0, range(7)), None, "Daily recap alert"),
             (nxt(9, 0, [6]), None, "Weekly coin rotation (lane E)"),
             (nxt(10, 0, [0]), None, "Weekly rotations (lanes A, B and F)")]
    out = []
    for when, label, what in items:
        if label is None:
            days = (when.date() - n.date()).days
            day = "Today" if days == 0 else "Tomorrow" if days == 1 else when.strftime("%a")
            label = f"{day} {when.strftime('%-I:%M %p')} ET"
        out.append({"when": label, "what": what, "ts": when.timestamp()})
    return out


def main():
    t0 = time.time()
    cfg = load(os.path.join(HERE, "config.json"), None)
    os.makedirs(DATA, exist_ok=True)
    st = load(os.path.join(DATA, "state.json"), None)
    cache = load(os.path.join(DATA, "cache.json"), {})
    log = load(os.path.join(DATA, "alerts.json"), [])
    feed = load(os.path.join(DATA, "feed.json"), [])
    health = Health(load(os.path.join(DATA, "health.json"), {}).get("sources"))
    mood = load(os.path.join(DATA, "markets.json"), {}).get("mood", {})

    replay = os.environ.get("RACE_REPLAY")
    if replay:
        global _REPLAY
        if "_REPLAY" not in globals():
            _REPLAY = Replay(replay)
        market = _REPLAY
    else:
        market = Live(health, cache)
    news = News(health, cache, feed)
    ai = AI(cfg, health, cache)
    alerts = Alerts(cfg, health, log)
    log[:] = [a for a in log if alerts.wanted(a.get("lane"), a.get("kind"))]
    for a in log:
        if a.get("status") == "open" and now_ts() - a["ts"] > 86400:
            a["status"] = "expired"

    restart = st is not None and cfg.get("race_id") and st.get("race_id") != cfg["race_id"]
    if restart:
        arch = os.path.join(DATA, "archive", st.get("race_id") or "first-race")
        os.makedirs(arch, exist_ok=True)
        for f in ("state.json", "alerts.json", "feed.json", "race.json"):
            if os.path.exists(os.path.join(DATA, f)):
                shutil.copy(os.path.join(DATA, f), os.path.join(arch, f))
        st = None
        log.clear(); feed.clear(); news.seen = set()
    if st is None and not restart and os.environ.get("GITHUB_ACTIONS") and not os.environ.get("RACE_NEW_OK"):
        raise SystemExit("state.json is missing but this is not a first run. Refusing to reset the race.")
    if st is None:
        st = {"start": now_ts(), "lanes": {k: new_lane(cfg["start_cash"]) for k in cfg["lanes"]},
              "bench": {k: new_lane(cfg["start_cash"]) for k in cfg["benchmarks"]}, "tables": {}, "risk_mode": {},
              "race_id": cfg.get("race_id")}
        real = ", ".join(cfg["alert_lanes"]) if cfg["alert_mode"] == "signal" else "none"
        alerts.add("ALL", "info", "", "The $100 Race has started",
                   f"Real-money signals: lane {real}. The other lanes run pretend money for comparison.")
        news.system("Race", "Race started. Every lane has $100 in pretend cash.")
    st.setdefault("tables", {})
    end = st["start"] + cfg["race_days"] * 86400
    finished = now_ts() >= end

    rot, coins, swing = cfg["rotation_universe"], cfg["coins"], cfg["swing_universe"]
    stocks = sorted(set(s for s in rot + swing if not is_coin(s)))
    daily = market.daily(sorted(set(rot + coins + swing + ["SPY"])))
    hourly = market.hourly(swing)
    quotes = market.quotes(sorted(set(stocks + coins)))
    runner = Runner(cfg, st, market, news, ai, alerts)

    if not replay:
        held = sorted(set(s for L in st["lanes"].values() for s in L["pos"] if not is_coin(s)))
        news.refresh(sorted(set(held + [s for s in rot if not is_coin(s)] + swing)), coins)
        n = et()
        if n.weekday() < 5 and 8 <= n.hour < 17 and news.due("alphavantage", 240):
            news.alpha_vantage(sorted(set([s for s in rot if not is_coin(s)] + held))[:12])
            news.alpha_vantage([f"CRYPTO:{c}" for c in coins])
        news.sec_filings(sorted(set(held + list(st.get("tables", {}).get("swing", {}))))[:16])
        mood = news.mood(mood)

    # morning brief and risk mode
    n = et(); day = n.strftime("%Y-%m-%d")
    if n.weekday() < 5 and (n.hour, n.minute) >= (8, 30) and st["risk_mode"].get("date") != day:
        events = news.big_event_today()
        big = [e for e in events if any(k.lower() in e["title"].lower() for k in BRIEF_EVENTS)]
        vix = (mood.get("vix") or {}).get("value", 0)
        res = ai.brief(mood, events, [f for f in feed if f["kind"] != "system"]) if not replay else None
        if res and res.get("mode") in ("normal", "careful"):
            mode, text, by = res["mode"], res.get("brief", ""), "AI brief"
        else:
            mode = "careful" if big or vix >= 30 else "normal"
            text = (f"Big event today: {', '.join(e['title'] for e in big)}." if big else "No major US event today.") + \
                   (f" VIX is {vix}." if vix else "")
            by = "Rules brief"
        st["risk_mode"] = {"date": day, "mode": mode, "text": text, "by": by}
        alerts.add("ALL", "brief", "", f"Morning brief: {mode} mode", text, push=not replay)
        news.system("Morning brief", f"{text} Lanes B and C are in {mode} mode today.")

    if not finished:
        runner.rotation(daily, quotes)
        runner.aggressive(daily, quotes)
        runner.swing(hourly, quotes)
        runner.coins(daily, quotes)
        runner.benchmarks(quotes)
    elif not st.get("finished"):
        st["finished"] = now_ts()
        best = max(st["lanes"], key=lambda k: value(st["lanes"][k], quotes))
        alerts.add("ALL", "recap", "", "The $100 Race is finished",
                   f"Leader: Lane {best} at ${value(st['lanes'][best], quotes):.2f}. Open the dashboard for the full scorecard.", urgent=True)

    # history, sampled every 15 minutes
    for group in ("lanes", "bench"):
        for k, L in st[group].items():
            v = round(value(L, quotes), 4)
            if not L["history"] or now_ts() - L["history"][-1][0] >= 900 or L["history"][-1][1] != v and len(L["trades"]) and L["trades"][-1]["closed"] >= now_ts() - 60:
                L["history"].append([round(now_ts()), v])

    # daily recap
    if (n.hour >= cfg["recap_hour_et"]) and st.get("recap_day") != day and not replay and now_ts() - st["start"] > 6 * 3600:
        st["recap_day"] = day
        parts = []
        for k, L in st["lanes"].items():
            if cfg["lanes"][k].get("shadow"):
                continue
            prev = next((v for ts, v in reversed(L["history"]) if ts <= now_ts() - 86400), cfg["start_cash"])
            parts.append((k, value(L, quotes) - prev))
        today_trades = sum(1 for L in st["lanes"].values() for t in L["trades"] if t["closed"] >= now_ts() - 86400)
        blocked = sum(1 for a in log if a["kind"] == "blocked" and a["ts"] >= now_ts() - 86400)
        lead = max(st["lanes"], key=lambda k: value(st["lanes"][k], quotes) if not cfg["lanes"][k].get("shadow") else -1)
        total = sum(v for _, v in parts)
        alerts.add("ALL", "recap", "", f"Daily recap: {'+' if total >= 0 else '-'}${abs(total):.2f} across lanes",
                   f"{today_trades} closed trades, {blocked} blocked by news. Leader: Lane {lead}.", urgent=True)
    alerts.flush_held()
    if not replay:
        alerts.sync_status()

    # outputs for the dashboard
    def lane_out(k, L, meta):
        v = value(L, quotes)
        pos = []
        for s, p in L["pos"].items():
            now_px = quotes.get(s, {}).get("last", p["entry"])
            pos.append({"symbol": s, "value": round(p["qty"] * now_px, 2), "cost": round(p["cost"], 2), "entry": p["entry"],
                        "now": now_px, "pnl": round(p["qty"] * now_px - p["cost"], 2), "stop": p.get("stop"),
                        "stop_label": p.get("stop_label", ""), "tp": p.get("tp"), "why": p.get("why", ""), "since": p["t0"]})
        return {"id": k, "name": meta["name"], "color": meta["color"], "shadow": meta.get("shadow", False),
                "value": round(v, 2), "cash": round(L["cash"], 2), "pnl": round(v - cfg["start_cash"], 2),
                "fees": round(L["fees"], 2), "blocked": L["blocked"], "trades": L["trades"][-60:],
                "trade_count": len(L["trades"]), "positions": pos, "history": L["history"]}
    watch = runner.on_deck(quotes)
    out = {"title": cfg["title"], "start": st["start"], "end": end, "now": now_ts(), "finished": bool(st.get("finished")),
           "alert_mode": cfg["alert_mode"], "risk_mode": st["risk_mode"], "ai": cache.get("ai", {}),
           "ai_cap": cfg["ai_monthly_cap_usd"], "lanes": [lane_out(k, L, cfg["lanes"][k]) for k, L in st["lanes"].items()],
           "bench": [lane_out(k, L, cfg["benchmarks"][k]) for k, L in st["bench"].items()], "next_up": next_up()}
    groups = {"Funds and Bitcoin (lanes A, B)": ("rotation", rot), "Coins (lanes D, E)": ("coins", coins),
              "Stocks and funds (lane C)": ("swing", swing)}
    mk = []
    for g, (tk, syms) in groups.items():
        tab = st["tables"].get(tk, {})
        for s in syms:
            q = quotes.get(s); t = tab.get(s, {})
            rows = daily.get(s) or []
            prev = rows[-1][1] if rows else None
            if not q:
                continue
            mk.append({"group": g, "symbol": s, "price": q["last"], "src": q["src"],
                       "change": round((q["last"] / prev - 1) * 100, 2) if prev else None,
                       "trend_up": t.get("trend_up", (t.get("close", 0) > t.get("sma", 1e18)) if t else None),
                       "rank": t.get("rank"), "mom": round(t["mom"] * 100, 1) if "mom" in t else None})
    for g in groups:
        rows = [r for r in mk if r["group"] == g and r["mom"] is not None]
        if g.startswith("Stocks"):
            for i, r in enumerate(sorted(rows, key=lambda r: -r["mom"])):
                r["rank"] = i + 1
    save(os.path.join(DATA, "state.json"), st)
    save(os.path.join(DATA, "race.json"), out)
    save(os.path.join(DATA, "markets.json"), {"rows": mk, "mood": mood, "updated": now_ts()})
    save(os.path.join(DATA, "watch.json"), watch)
    save(os.path.join(DATA, "alerts.json"), log)
    save(os.path.join(DATA, "feed.json"), feed)
    save(os.path.join(DATA, "cache.json"), cache)
    save(os.path.join(DATA, "health.json"), {"sources": health.s, "tick_seconds": round(time.time() - t0, 1),
                                             "updated": now_ts(), "market_open": market_open(), "replay": bool(replay)})
    pub = {k: v for k, v in cfg.items() if k not in ("form_url",)}
    save(os.path.join(DATA, "config.json"), pub)
    print(json.dumps({l["id"]: l["value"] for l in out["lanes"] + out["bench"]}), f"{time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
