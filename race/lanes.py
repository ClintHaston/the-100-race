"""The trading rules. Each lane mirrors a rule set that was backtested on two years of prices."""
import os
from datetime import timedelta
from .util import now_ts, et, sma, market_open
from .market import is_coin
from .news import red_flag

LOOK_D, SMA_D = 60, 50           # rotation and coin rules: 60-day momentum, 50-day trend line
C_BPD, C_ATR, C_K, C_M, C_HOLD, C_SLOTS = 7, 14, 3.0, 5.0, 72 * 3600, 3


# ---------------- accounting ----------------
def new_lane(cash):
    return {"cash": cash, "pos": {}, "history": [], "trades": [], "fees": 0.0, "blocked": 0,
            "day": {}, "sched": {}, "notes": []}


def fee_rate(cfg, lane_id, sym):
    if not is_coin(sym):
        return cfg["fees"]["stock_spread"]
    return cfg["fees"]["robinhood_crypto"] if lane_id == "E_RH" else cfg["fees"]["kraken_limit"]


def value(lane, quotes):
    return lane["cash"] + sum(p["qty"] * quotes.get(s, {}).get("last", p["entry"]) for s, p in lane["pos"].items())


def buy(cfg, lid, lane, sym, dollars, q, why, stop=None, tp=None, atr=None):
    f = fee_rate(cfg, lid, sym)
    price = q["ask"]
    lane["pos"][sym] = {"qty": dollars * (1 - f) / price, "entry": price, "t0": now_ts(), "cost": dollars,
                        "stop": stop, "tp": tp, "peak": price, "atr": atr, "why": why}
    lane["cash"] -= dollars
    lane["fees"] += dollars * f
    return price


def sell(cfg, lid, lane, sym, q, reason):
    p = lane["pos"].pop(sym)
    f = fee_rate(cfg, lid, sym)
    gross = p["qty"] * q["bid"]
    net = gross * (1 - f)
    lane["cash"] += net
    lane["fees"] += gross * f
    t = {"symbol": sym, "opened": p["t0"], "closed": now_ts(), "entry": p["entry"], "exit": q["bid"],
         "cost": round(p["cost"], 4), "proceeds": round(net, 4), "pnl": round(net - p["cost"], 4), "reason": reason, "why": p["why"]}
    lane["trades"].append(t)
    return t


# ---------------- signals ----------------
def aligned_closes(daily, universe):
    """Rotations that mix funds and coins line coins up to stock-market trading days (as in the backtest)."""
    spy_dates = [d for d, _ in daily.get("SPY", [])]
    out = {}
    for s in universe:
        rows = daily.get(s, [])
        if is_coin(s) and spy_dates:
            m = dict(rows); last = None; seq = []
            for d in spy_dates:
                last = m.get(d, last)
                if last is not None:
                    seq.append(last)
            out[s] = seq
        else:
            out[s] = [c for _, c in rows]
    return out


def momentum_table(closes, look=LOOK_D):
    rows = {}
    for s, c in closes.items():
        if len(c) <= look or len(c) < SMA_D:
            continue
        ma = sma(c, SMA_D)
        rows[s] = {"mom": c[-1] / c[-1 - look] - 1, "ma": ma, "close": c[-1], "anchor": c[-1 - look],
                   "trend_up": c[-1] > ma}
    ranked = sorted(rows, key=lambda s: -rows[s]["mom"])
    for i, s in enumerate(ranked):
        rows[s]["rank"] = i + 1
    return rows


def pick_top(table, top):
    return [s for s in sorted(table, key=lambda s: -table[s]["mom"]) if table[s]["mom"] > 0 and table[s]["trend_up"]][:top]


def swing_table(hourly):
    out = {}
    for s, bars in hourly.items():
        if len(bars) < 8 * C_BPD + 2:
            continue
        c = [b[4] for b in bars]; h = [b[2] for b in bars]; l = [b[3] for b in bars]
        tr = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(len(bars) - C_ATR, len(bars))]
        out[s] = {"close": c[-1], "atr": sum(tr) / C_ATR, "sma": sma(c, 8 * C_BPD), "mom": c[-1] / c[-1 - 7 * C_BPD] - 1,
                  "hi2d": max(h[-1 - 2 * C_BPD:-1]), "bar": bars[-1][0]}
    return out


# ---------------- lane runners ----------------
class Runner:
    def __init__(self, cfg, state, market, news, ai, alerts):
        self.cfg, self.st, self.m, self.news, self.ai, self.al = cfg, state, market, news, ai, alerts
        self.watch = []

    def lane(self, lid):
        return self.st["lanes"][lid]

    def note_trade(self, lid, kind, sym, dollars, price, why, stop=None, tp=None, reason=None, pnl=None):
        disp = lid.replace("_RH", " RH")
        mode = self.cfg["alert_mode"] if lid in self.cfg["alert_lanes"] else "report"
        if kind == "buy":
            title = (f"Lane {disp}: pretend buy {sym}, ${dollars:.2f}" if mode == "report" else f"Lane {disp}: rules say buy {sym} with ${dollars:.2f}")
            detail = f"{why}. Filled at ${price:,.2f}."
        else:
            title = (f"Lane {disp}: pretend sell {sym}" if mode == "report" else f"Lane {disp}: rules say sell {sym}")
            detail = f"{reason}. Filled at ${price:,.2f}, result {'+' if pnl >= 0 else '-'}${abs(pnl):.2f}."
        shadow = self.cfg["lanes"][lid].get("shadow")
        if not shadow:
            self.al.add(lid, kind, sym, title, detail, dollars=dollars, stop=stop, tp=tp, price=price)
        msg = title.split(": ", 1)[1]
        self.news.system(f"Lane {disp}", msg[:1].upper() + msg[1:] + ". " + detail)

    def news_ok(self, lid, sym, stock):
        """News check for lanes B and C. Returns (ok, reason)."""
        if not self.cfg["lanes"][lid]["news_check"]:
            return True, ""
        replay = bool(os.environ.get("RACE_REPLAY"))
        if stock and not replay and len(self.news.headlines_for(sym)) < 3:
            self.news.google_news(sym)
        heads = self.news.headlines_for(sym)
        for hd in heads:
            flag = red_flag(hd["text"])
            if flag:
                return False, f"headline mentions '{flag}': {hd['text'][:90]}"
        if stock and lid == "C" and not replay and self.news.earnings_soon(sym):
            return False, "earnings report due within 2 days"
        res = self.ai.veto(sym, heads, self.news.big_event_today())
        if res and res[0]:
            return False, res[1] or "AI news check flagged a problem"
        return True, ""

    def block(self, lid, sym, reason):
        self.lane(lid)["blocked"] += 1
        self.al.add(lid, "blocked", sym, f"Lane {lid}: blocked buy {sym}", f"News check stopped it: {reason}.")
        self.news.system("News check", f"Lane {lid} blocked {sym}: {reason}")

    def careful(self):
        rm = self.st.get("risk_mode", {})
        return rm.get("date") == et().strftime("%Y-%m-%d") and rm.get("mode") == "careful"

    # ---- lanes A and B: weekly fund and Bitcoin rotation with a daily trend exit
    def rotation(self, daily, quotes):
        uni = self.cfg["rotation_universe"]
        table = momentum_table(aligned_closes(daily, uni))
        self.st["tables"]["rotation"] = table
        if len(table) < 5:
            return
        now = et()
        week = now.strftime("%G-%V")
        for lid in ("A", "B"):
            L = self.lane(lid); sc = L["sched"]
            if market_open() and now.hour >= 10 and sc.get("rot_week") != week:
                if lid == "B" and self.careful():
                    if sc.get("rot_wait") != now.strftime("%Y-%m-%d"):
                        sc["rot_wait"] = now.strftime("%Y-%m-%d")
                        self.news.system("Lane B", "Weekly rotation waiting a day: careful mode is on for today's big event")
                else:
                    self._rebalance(lid, L, table, quotes, top=2, why_fmt="Top 2 on 60-day momentum, above its 50-day average")
                    sc["rot_week"] = week
            if market_open() and (now.hour, now.minute) >= (15, 45) and sc.get("trend_day") != now.strftime("%Y-%m-%d"):
                sc["trend_day"] = now.strftime("%Y-%m-%d")
                for s in list(L["pos"]):
                    if s in quotes and s in table and quotes[s]["last"] < table[s]["ma"]:
                        t = sell(self.cfg, lid, L, s, quotes[s], "Fell below its 50-day average")
                        self.note_trade(lid, "sell", s, None, t["exit"], None, reason=t["reason"], pnl=t["pnl"])
        for lid in ("A", "B"):
            for s, p in self.lane(lid)["pos"].items():
                if s in table:
                    p["stop"] = round(table[s]["ma"], 2); p["stop_label"] = "trend line"

    def _rebalance(self, lid, L, table, quotes, top, why_fmt):
        keep = pick_top(table, top)
        for s in list(L["pos"]):
            if s not in keep and s in quotes:
                t = sell(self.cfg, lid, L, s, quotes[s], "Dropped out of the top picks")
                self.note_trade(lid, "sell", s, None, t["exit"], None, reason=t["reason"], pnl=t["pnl"])
        new = [s for s in keep if s not in L["pos"] and s in quotes]
        if not new:
            return
        equity = value(L, quotes)
        for s in new:
            ok, reason = self.news_ok(lid, s, not is_coin(s))
            if not ok:
                self.block(lid, s, reason)
                continue
            dollars = min(equity / top, L["cash"])
            if dollars < 1:
                break
            daily_exit = lid in ("A", "B")
            review = "checked every trading day" if lid in ("F", "G") else "reviewed each Sunday"
            px = buy(self.cfg, lid, L, s, dollars, quotes[s], f"{why_fmt} (ranked #{table[s]['rank']})",
                     stop=round(table[s]["ma"], 2) if daily_exit else None)
            if daily_exit:
                L["pos"][s]["stop_label"] = "trend line"
            self.note_trade(lid, "buy", s, dollars, px, L["pos"][s]["why"],
                            stop=f"${table[s]['ma']:,.2f} trend line" if daily_exit else f"none, {review}", tp="none")

    # ---- lanes F and G: aggressive momentum across funds, single stocks and coins
    def aggressive(self, daily, quotes):
        uni = sorted(set(self.cfg["rotation_universe"] + self.cfg["swing_universe"] + self.cfg["coins"]))
        closes = aligned_closes(daily, uni)
        for lid, key in (("F", "aggressive"), ("G", "all_in")):
            if lid not in self.st["lanes"] or key not in self.cfg:
                continue
            a = self.cfg[key]
            table = momentum_table(closes, look=a["lookback_days"])
            self.st["tables"][key] = table
            L = self.lane(lid); now = et()
            # Checked every trading day (backtest: held up far better in the latest year than Mondays only).
            day_key = now.strftime("%Y-%m-%d") if a.get("check") == "daily" else now.strftime("%G-%V")
            if len(table) >= 10 and market_open() and now.hour >= 10 and L["sched"].get("week") != day_key:
                L["sched"]["week"] = day_key
                self._rebalance(lid, L, table, quotes, top=a["top"],
                                why_fmt=f"Top {a['top']} of {len(table)} on {a['lookback_days']}-day momentum")

    # ---- lane C: hourly breakout swing trades in stocks and funds
    def swing(self, hourly, quotes):
        L = self.lane("C"); sc = L["sched"]
        table = swing_table(hourly)
        self.st["tables"]["swing"] = table
        if not market_open():
            return
        for s in list(L["pos"]):
            if s not in quotes:
                continue
            p, px = L["pos"][s], quotes[s]["last"]
            p["peak"] = max(p["peak"], px)
            p["stop"] = round(max(p["stop"], p["peak"] - C_K * p["atr"]), 4)
            reason = ("Stop-loss hit" if px <= p["stop"] else "Take-profit hit" if px >= p["tp"]
                      else "72-hour time limit" if now_ts() - p["t0"] >= C_HOLD else None)
            if reason:
                t = sell(self.cfg, "C", L, s, quotes[s], reason)
                self.note_trade("C", "sell", s, None, t["exit"], None, reason=reason, pnl=t["pnl"])
        now = et(); day = now.strftime("%Y-%m-%d")
        if L["day"].get("date") != day:
            L["day"] = {"date": day, "start": value(L, quotes)}
        spy = table.get("SPY")
        newest = max((v["bar"] for v in table.values()), default=0)
        if not (10 <= now.hour < 15) or sc.get("last_bar") == newest:
            return
        sc["last_bar"] = newest
        if value(L, quotes) < L["day"]["start"] * 0.97:
            if sc.get("halt") != day:
                sc["halt"] = day
                self.news.system("Lane C", "Down 3% today, no new buys until tomorrow")
            return
        if self.careful() or not spy or spy["close"] <= spy["sma"]:
            return
        slots = C_SLOTS - len(L["pos"])
        if slots <= 0:
            return
        cands = sorted([s for s, v in table.items() if s not in L["pos"] and s in quotes and v["bar"] == newest
                        and v["close"] > v["hi2d"] and v["mom"] > 0 and v["close"] > v["sma"]], key=lambda s: -table[s]["mom"])
        equity = value(L, quotes)
        for s in cands:
            if slots <= 0:
                break
            ok, reason = self.news_ok("C", s, True)
            if not ok:
                self.block("C", s, reason)
                continue
            dollars = min(equity / C_SLOTS, L["cash"])
            if dollars < 1:
                break
            v = table[s]; px = quotes[s]["ask"]
            stop, tp = round(px - C_K * v["atr"], 2), round(px + C_M * v["atr"], 2)
            buy(self.cfg, "C", L, s, dollars, quotes[s], "Broke its 2-day high with rising momentum", stop=stop, tp=tp, atr=v["atr"])
            self.note_trade("C", "buy", s, dollars, px, "Broke its 2-day high with rising momentum", stop=f"${stop:,.2f}", tp=f"${tp:,.2f}")
            slots -= 1

    # ---- lanes D, E and E_RH: coins
    def coins(self, daily, quotes):
        table = momentum_table({s: [c for _, c in daily.get(s, [])] for s in self.cfg["coins"]})
        self.st["tables"]["coins"] = table
        now = et(); day = now.strftime("%Y-%m-%d")
        if now.hour < 9:
            return
        L = self.lane("D")
        b = table.get("BTC")
        if b and L["sched"].get("day") != day and "BTC" in quotes:
            L["sched"]["day"] = day
            ok = b["mom"] > 0 and b["trend_up"]
            if ok and "BTC" not in L["pos"]:
                px = buy(self.cfg, "D", L, "BTC", L["cash"], quotes["BTC"], "Above its 50-day average with rising 60-day momentum", stop=round(b["ma"], 2))
                self.note_trade("D", "buy", "BTC", L["pos"]["BTC"]["cost"], px, L["pos"]["BTC"]["why"], stop=f"${b['ma']:,.0f} trend line", tp="none")
            elif not ok and "BTC" in L["pos"]:
                t = sell(self.cfg, "D", L, "BTC", quotes["BTC"], "Trend turned down")
                self.note_trade("D", "sell", "BTC", None, t["exit"], None, reason=t["reason"], pnl=t["pnl"])
        if "BTC" in L["pos"] and b:
            L["pos"]["BTC"]["stop"] = round(b["ma"], 2); L["pos"]["BTC"]["stop_label"] = "trend line"
        for lid in ("E", "E_RH"):
            E = self.lane(lid)
            sunday = (now - timedelta(days=(now.weekday() + 1) % 7)).strftime("%Y-%m-%d")
            first = not E["sched"].get("week")
            if len(table) >= 6 and (first or now.weekday() == 6) and E["sched"].get("week") != sunday:
                E["sched"].update(week=sunday, day=day)
                self._rebalance(lid, E, table, quotes, top=2, why_fmt="Top 2 of 8 coins on 60-day momentum, above its 50-day average")

    # ---- benchmarks
    def benchmarks(self, quotes):
        for sym, B in self.st["bench"].items():
            if not B["pos"] and B["cash"] > 0 and sym in quotes and (is_coin(sym) or market_open()):
                buy(self.cfg, "bench", B, sym, B["cash"], quotes[sym], "Benchmark: buy and hold")

    # ---- on deck list
    def on_deck(self, quotes):
        out = []
        rot = self.st["tables"].get("rotation", {})
        top = pick_top(rot, 2)
        held_ab = set(self.lane("A")["pos"]) | set(self.lane("B")["pos"])
        for s, v in rot.items():
            if s in held_ab:
                continue
            px = quotes.get(s, {}).get("last", v["close"])
            if s in top:
                out.append({"symbol": s, "lanes": "A, B", "need": "Qualifies now", "level": "Joins at next weekly rotation", "away": 0.0})
            elif not v["trend_up"]:
                out.append({"symbol": s, "lanes": "A, B", "need": "Close back above 50-day average", "level": f"${v['ma']:,.2f}", "away": max(0.0, (v["ma"] / px - 1) * 100)})
            elif v["mom"] <= 0:
                out.append({"symbol": s, "lanes": "A, B", "need": "60-day momentum back above zero", "level": f"${v['anchor']:,.2f}", "away": max(0.0, (v["anchor"] / px - 1) * 100)})
            else:
                out.append({"symbol": s, "lanes": "A, B", "need": f"Climb from rank #{v['rank']} into the top 2", "level": "Weekly rotation", "away": None})
        sw = self.st["tables"].get("swing", {})
        spy_ok = sw.get("SPY") and sw["SPY"]["close"] > sw["SPY"]["sma"]
        for s, v in sw.items():
            if s in self.lane("C")["pos"] or v["mom"] <= 0 or v["close"] <= v["sma"]:
                continue
            px = quotes.get(s, {}).get("last", v["close"])
            out.append({"symbol": s, "lanes": "C", "need": "Break above 2-day high" + ("" if spy_ok else " (paused: SPY below its trend)"),
                        "level": f"${v['hi2d']:,.2f}", "away": max(0.0, (v["hi2d"] / px - 1) * 100)})
        coins = self.st["tables"].get("coins", {})
        ctop = pick_top(coins, 2)
        held_e = set(self.lane("E")["pos"])
        for s, v in coins.items():
            if s in held_e:
                continue
            px = quotes.get(s, {}).get("last", v["close"])
            if s in ctop:
                out.append({"symbol": s, "lanes": "E", "need": f"Qualifies now, ranked #{v['rank']}", "level": "Joins at Sunday rotation", "away": 0.0})
            elif not v["trend_up"] and v["mom"] > 0:
                out.append({"symbol": s, "lanes": "E", "need": "Close back above 50-day average", "level": f"${v['ma']:,.4g}", "away": max(0.0, (v["ma"] / px - 1) * 100)})
        agg = self.st["tables"].get("aggressive", {})
        if agg and "F" in self.st["lanes"]:
            top = pick_top(agg, self.cfg["aggressive"]["top"])
            held = set(self.lane("F")["pos"])
            ranked = [s for s in sorted(agg, key=lambda s: -agg[s]["mom"]) if s not in held and agg[s]["mom"] > 0][:4]
            f_out = []
            for s in ranked:
                ready = s in top
                f_out.append({"symbol": s, "lanes": "F", "away": 0.0 if ready else None,
                            "need": (f"Ranked #{agg[s]['rank']} of {len(agg)}, joins at the next daily check" if ready
                                     else f"Ranked #{agg[s]['rank']} of {len(agg)}, up {agg[s]['mom'] * 100:.0f}% in 20 days"),
                            "level": "Daily check"})
        b = coins.get("BTC")
        if b and "BTC" not in self.lane("D")["pos"]:
            px = quotes.get("BTC", {}).get("last", b["close"])
            out.append({"symbol": "BTC", "lanes": "D", "need": "Close back above 50-day average", "level": f"${b['ma']:,.0f}", "away": max(0.0, (b["ma"] / px - 1) * 100)})
        out.sort(key=lambda x: (x["away"] is None, x["away"] if x["away"] is not None else 0))
        return out[:10] + (f_out if agg and "F" in self.st["lanes"] else [])
