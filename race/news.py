"""News, mood and event data. Every source is optional; failures are recorded, never fatal."""
import os, re, time, html, urllib.parse, http.cookiejar, urllib.request, json
import xml.etree.ElementTree as ET_XML
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from .util import get, now_ts, et, uid, UA

NEG = set("""fraud probe investigation subpoena lawsuit sued charges indicted bankruptcy bankrupt default delist delisting
halt halted hack hacked exploit breach recall downgrade downgraded cut cuts slashes miss misses missed plunge plunges plummets
tumble tumbles sinks slump slumps warning warns restatement resign resigns layoffs weak weaker loss losses selloff crash
liquidation outflows ban banned sanction sanctions shortfall lowers lowered""".split())
POS = set("""beat beats surge surges soar soars jump jumps rally rallies record upgrade upgraded raises raised strong stronger
growth profit profits approval approved inflows partnership wins win expands expansion bullish outperform buyback""".split())
RED_FLAGS = ["fraud", "investigation", "probe", "subpoena", "sec charges", "indicted", "bankrupt", "delist", "halt",
             "hack", "exploit", "breach", "restatement", "accounting", "guidance cut", "cuts guidance", "lowers guidance",
             "recall", "default", "short seller", "short report"]


def score(text):
    words = re.findall(r"[a-z]+", text.lower())
    p = sum(w in POS for w in words); n = sum(w in NEG for w in words)
    if p == n:
        return "neutral", 0.0
    s = (p - n) / max(p + n, 1)
    return ("good" if s > 0 else "bad"), round(s, 2)


def red_flag(text):
    t = text.lower()
    return next((f for f in RED_FLAGS if f in t), None)


def _rss(url):
    body = get(url, raw=True)
    root = ET_XML.fromstring(body)
    items = []
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        try:
            ts = parsedate_to_datetime(pub).timestamp() if pub else now_ts()
        except Exception:
            ts = now_ts()
        items.append({"title": title, "url": link, "ts": ts})
    return items


class News:
    def __init__(self, health, cache, feed):
        self.h, self.c, self.feed = health, cache, feed
        self.seen = set(x.get("id") for x in feed)
        self.akey, self.asec = os.environ.get("ALPACA_KEY"), os.environ.get("ALPACA_SECRET")
        self.avkey = os.environ.get("ALPHAVANTAGE_KEY")
        self.sec_ua = {"User-Agent": "PaperRace research " + os.environ.get("SEC_CONTACT_EMAIL", "paperrace@example.com")}

    def add(self, source, text, kind=None, symbols=(), url="", ts=None, sc=None):
        fid = uid(source, text[:80])
        if fid in self.seen:
            return
        self.seen.add(fid)
        if kind is None:
            kind, sc = score(text)
        self.feed.insert(0, {"id": fid, "ts": ts or now_ts(), "kind": kind, "source": source, "text": text[:220],
                             "symbols": list(symbols), "url": url, "score": sc})
        del self.feed[300:]

    def system(self, source, text):
        self.add(source, text, kind="system")

    def due(self, key, minutes):
        last = self.c.get("due", {}).get(key, 0)
        if now_ts() - last >= minutes * 60:
            self.c.setdefault("due", {})[key] = now_ts()
            return True
        return False

    # ---------- headline sources ----------
    def refresh(self, symbols, coins):
        if self.due("crypto_rss", 15):
            for name, url in [("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
                              ("Cointelegraph", "https://cointelegraph.com/rss")]:
                try:
                    for it in _rss(url)[:15]:
                        tags = [c for c in coins if re.search(rf"\b({c}|{ {'BTC':'bitcoin','ETH':'ether','SOL':'solana','XRP':'xrp','DOGE':'dogecoin','AVAX':'avalanche','LINK':'chainlink','ADA':'cardano'}[c]})\b", it["title"], re.I)]
                        if now_ts() - it["ts"] < 86400:
                            self.add(name, it["title"], symbols=tags, url=it["url"], ts=it["ts"])
                    self.h.ok("News feeds")
                except Exception as e:
                    self.h.fail("News feeds", f"{name}: {e}")
        if self.due("stock_rss", 15) and symbols:
            try:
                q = ",".join(symbols[:20])
                for it in _rss(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={q}&region=US&lang=en-US")[:30]:
                    tags = [s for s in symbols if re.search(rf"\b{s}\b", it["title"])]
                    if now_ts() - it["ts"] < 86400:
                        self.add("Yahoo Finance", it["title"], symbols=tags, url=it["url"], ts=it["ts"])
                self.h.ok("News feeds")
            except Exception as e:
                self.h.fail("News feeds", f"Yahoo RSS: {e}")
        if self.akey and self.due("benzinga", 15):
            try:
                r = get("https://data.alpaca.markets/v1beta1/news?limit=50&symbols=" + ",".join(symbols + [f"{c}USD" for c in coins]),
                        headers={"APCA-API-KEY-ID": self.akey, "APCA-API-SECRET-KEY": self.asec})
                for n in r.get("news", []):
                    ts = datetime.fromisoformat(n["created_at"].replace("Z", "+00:00")).timestamp()
                    syms = [s.replace("USD", "") if s.endswith("USD") and s[:-3] in coins else s for s in n.get("symbols", [])]
                    self.add("Benzinga", n["headline"], symbols=[s for s in syms if s in symbols or s in coins], url=n.get("url", ""), ts=ts)
                self.h.ok("Benzinga news")
            except Exception as e:
                self.h.fail("Benzinga news", e)

    def headlines_for(self, sym, hours=48):
        cut = now_ts() - hours * 3600
        return [f for f in self.feed if sym in f.get("symbols", []) and f["ts"] >= cut and f["kind"] != "system"]

    def google_news(self, sym):
        try:
            items = _rss(f"https://news.google.com/rss/search?q={urllib.parse.quote(sym + ' stock')}&hl=en-US&gl=US&ceid=US:en")[:12]
            for it in items:
                if now_ts() - it["ts"] < 2 * 86400:
                    self.add("Google News", it["title"], symbols=[sym], url=it["url"], ts=it["ts"])
            self.h.ok("News feeds")
        except Exception as e:
            self.h.fail("News feeds", f"Google News: {e}")

    def alpha_vantage(self, tickers):
        """Pre-scored sentiment. Free key allows 25 calls a day; we use at most 4."""
        day = et().strftime("%Y-%m-%d")
        av = self.c.setdefault("av", {"date": day, "calls": 0})
        if av["date"] != day:
            av.update(date=day, calls=0)
        if not self.avkey or av["calls"] >= 4 or not tickers:
            return
        try:
            r = get("https://www.alphavantage.co/query?function=NEWS_SENTIMENT&limit=50&tickers="
                    + ",".join(tickers) + f"&apikey={self.avkey}")
            av["calls"] += 1
            if "feed" not in r:
                raise RuntimeError(r.get("Information") or r.get("Note") or "no feed")
            for it in r["feed"][:30]:
                ts = datetime.strptime(it["time_published"], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).timestamp()
                for t in it.get("ticker_sentiment", []):
                    sym = t["ticker"].replace("CRYPTO:", "")
                    if sym in tickers or f"CRYPTO:{sym}" in tickers:
                        s = float(t["ticker_sentiment_score"])
                        kind = "good" if s >= 0.15 else "bad" if s <= -0.15 else "neutral"
                        self.add("Alpha Vantage", f"{it['title']} (mood {s:+.2f})", kind=kind, symbols=[sym], url=it.get("url", ""), ts=ts, sc=s)
            self.h.ok("Alpha Vantage", f"{25 - av['calls']} of 25 calls left today")
        except Exception as e:
            self.h.fail("Alpha Vantage", e)

    # ---------- events ----------
    def earnings_soon(self, sym, days=2):
        cache = self.c.setdefault("earnings", {})
        day = et().strftime("%Y-%m-%d")
        if cache.get(sym, {}).get("date") != day:
            try:
                cj = http.cookiejar.CookieJar()
                op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
                try:
                    op.open(urllib.request.Request("https://fc.yahoo.com", headers={"User-Agent": UA}), timeout=10)
                except Exception:
                    pass
                crumb = op.open(urllib.request.Request("https://query1.finance.yahoo.com/v1/test/getcrumb", headers={"User-Agent": UA}), timeout=10).read().decode()
                d = json.load(op.open(urllib.request.Request(
                    f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}?modules=calendarEvents&crumb={crumb}",
                    headers={"User-Agent": UA}), timeout=10))
                dates = d["quoteSummary"]["result"][0]["calendarEvents"]["earnings"].get("earningsDate", [])
                cache[sym] = {"date": day, "next": dates[0]["raw"] if dates else None}
                self.h.ok("Earnings dates")
            except Exception as e:
                self.h.fail("Earnings dates", e)
                cache[sym] = {"date": day, "next": None}
        nxt = cache[sym].get("next")
        return bool(nxt and 0 <= nxt - now_ts() <= days * 86400 + 86400)

    def sec_filings(self, syms):
        if not syms or not self.due("sec", 360):
            return
        try:
            tick = self.c.get("sec_map")
            if not tick:
                raw = get("https://www.sec.gov/files/company_tickers.json", headers=self.sec_ua)
                tick = {v["ticker"]: v["cik_str"] for v in raw.values()}
                self.c["sec_map"] = {k: tick[k] for k in tick if len(k) <= 5}
                tick = self.c["sec_map"]
            for s in syms:
                if s not in tick:
                    continue
                d = get(f"https://data.sec.gov/submissions/CIK{int(tick[s]):010d}.json", headers=self.sec_ua)
                rec = d["filings"]["recent"]
                for form, date, desc in zip(rec["form"][:10], rec["filingDate"][:10], rec.get("primaryDocDescription", [""] * 10)[:10]):
                    ts = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
                    if form in ("8-K", "8-K/A") and now_ts() - ts < 3 * 86400:
                        self.add("SEC filing", f"{s} filed a {form} on {date}{': ' + desc if desc else ''}", kind="neutral", symbols=[s], ts=ts)
                time.sleep(0.2)
            self.h.ok("SEC filings")
        except Exception as e:
            self.h.fail("SEC filings", e)

    def mood(self, mood):
        if self.due("fng", 60):
            try:
                v = get("https://api.alternative.me/fng/?limit=1")["data"][0]
                mood["fear_greed"] = {"value": int(v["value"]), "label": v["value_classification"]}
                self.h.ok("Mood gauges")
            except Exception as e:
                self.h.fail("Mood gauges", f"fear and greed: {e}")
        if self.due("vix", 15):
            try:
                r = get("https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=5d")["chart"]["result"][0]
                v = r["meta"]["regularMarketPrice"]
                mood["vix"] = {"value": round(v, 1), "label": "calm" if v < 20 else "nervous" if v < 30 else "fearful"}
                self.h.ok("Mood gauges")
            except Exception as e:
                self.h.fail("Mood gauges", f"VIX: {e}")
        if self.due("fred", 720):
            try:
                rows = get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10", raw=True).decode().strip().splitlines()
                last = next(r for r in reversed(rows) if not r.endswith(",") and not r.endswith("."))
                mood["ten_year"] = {"value": float(last.split(",")[1]), "date": last.split(",")[0]}
                self.h.ok("Macro data")
            except Exception as e:
                self.h.fail("Macro data", f"FRED: {e}")
        if self.due("calendar", 360):
            try:
                evs = get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
                keep = []
                for e in evs:
                    if e.get("country") == "USD" and e.get("impact") == "High":
                        ts = datetime.fromisoformat(e["date"]).timestamp()
                        keep.append({"title": e["title"], "ts": ts})
                self.c["calendar"] = keep
                self.h.ok("Event calendar")
            except Exception as e:
                self.h.fail("Event calendar", e)
        upcoming = sorted([e for e in self.c.get("calendar", []) if e["ts"] >= now_ts() - 3600], key=lambda e: e["ts"])
        mood["next_event"] = upcoming[0] if upcoming else None
        if self.due("reddit", 60):
            try:
                r = get("https://apewisdom.io/api/v1.0/filter/all-stocks/page/1")["results"][:5]
                mood["reddit"] = [{"ticker": x["ticker"], "mentions": x["mentions"]} for x in r]
                self.add("Reddit buzz (ApeWisdom)", "Most mentioned: " + ", ".join(f"{x['ticker']} ({x['mentions']})" for x in r),
                         kind="neutral", symbols=[x["ticker"] for x in r])
                self.h.ok("Reddit mentions")
            except Exception as e:
                self.h.fail("Reddit mentions", e)
        if self.due("polymarket", 360):
            try:
                ms = get("https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=200&order=volume24hr&ascending=false")
                fed = [m for m in ms if re.search(r"\bfed\b|interest rate|fomc", m.get("question", ""), re.I)]
                if fed:
                    m = fed[0]
                    outs = json.loads(m["outcomes"]) if isinstance(m["outcomes"], str) else m["outcomes"]
                    prices = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
                    mood["polymarket"] = {"question": m["question"], "odds": {o: round(float(p) * 100) for o, p in zip(outs, prices)}}
                self.h.ok("Prediction markets")
            except Exception as e:
                self.h.fail("Prediction markets", e)
        return mood

    def big_event_today(self):
        day = et().date()
        return [e for e in self.c.get("calendar", []) if et(e["ts"]).date() == day]
