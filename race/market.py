"""Prices. Live mode reads Kraken, Coinbase, Alpaca (if keys) and Yahoo. Replay mode reads a saved bar file."""
import os, bisect
from datetime import datetime, timezone
from .util import get, now_ts, et, market_open

KRAKEN = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD", "XRP": "XRPUSD",
          "DOGE": "XDGUSD", "AVAX": "AVAXUSD", "LINK": "LINKUSD", "ADA": "ADAUSD"}
COINS = set(KRAKEN)


def ysym(s):
    return f"{s}-USD" if s in COINS else s


def is_coin(s):
    return s in COINS


class Live:
    def __init__(self, health, cache):
        self.h, self.c = health, cache
        self.akey, self.asec = os.environ.get("ALPACA_KEY"), os.environ.get("ALPACA_SECRET")

    def _yahoo(self, s, interval, rng):
        r = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym(s)}?interval={interval}&range={rng}")["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        return r, q

    def daily(self, syms):
        """Completed daily closes, cached once per day."""
        key = et().strftime("%Y-%m-%d")
        cache = self.c.setdefault("daily", {})
        if cache.get("date") != key or not all(s in cache.get("data", {}) for s in syms):
            data = dict(cache.get("data", {})) if cache.get("date") == key else {}
            for s in syms:
                if s in data:
                    continue
                try:
                    r, q = self._yahoo(s, "1d", "1y")
                    rows = [(datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), c)
                            for t, c in zip(r["timestamp"], q["close"]) if c is not None]
                    data[s] = rows
                    self.h.ok("Daily history")
                except Exception as e:
                    self.h.fail("Daily history", f"{s}: {e}")
            cache.update(date=key, data=data)
        out = {}
        today_utc = datetime.fromtimestamp(now_ts(), timezone.utc).strftime("%Y-%m-%d")
        today_et = et().strftime("%Y-%m-%d")
        for s in syms:
            rows = cache["data"].get(s, [])
            cut = today_utc if is_coin(s) else today_et
            out[s] = [r for r in rows if r[0] < cut]
        return out

    def hourly(self, syms):
        """Completed hourly bars for stocks: [ready_ts, o, h, l, c]. Refreshed once per hour."""
        hour = et().strftime("%Y-%m-%d %H")
        cache = self.c.setdefault("hourly", {})
        if cache.get("hour") != hour:
            data = {}
            for s in syms:
                try:
                    r, q = self._yahoo(s, "1h", "60d")
                    data[s] = [[t + 3600, o, hi, lo, c] for t, o, hi, lo, c in
                               zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"]) if c is not None]
                except Exception as e:
                    self.h.fail("Hourly history", f"{s}: {e}")
            if data:
                self.h.ok("Hourly history")
            cache.update(hour=hour, data=data)
        now = now_ts()
        return {s: [b for b in cache["data"].get(s, []) if b[0] <= now] for s in syms}

    def quotes(self, syms):
        out = {}
        coins = [s for s in syms if is_coin(s)]
        stocks = [s for s in syms if not is_coin(s)]
        if coins:
            try:
                r = get("https://api.kraken.com/0/public/Ticker?pair=" + ",".join(KRAKEN[s] for s in coins))
                if r["error"]:
                    raise RuntimeError(r["error"])
                res = r["result"]
                for s in coins:
                    key = next(k for k in res if k.replace("X", "").replace("Z", "").startswith(KRAKEN[s][:-3].replace("X", "")) or k == KRAKEN[s])
                    v = res[key]
                    out[s] = {"bid": float(v["b"][0]), "ask": float(v["a"][0]), "last": float(v["c"][0]), "src": "Kraken"}
                self.h.ok("Kraken prices")
            except Exception as e:
                self.h.fail("Kraken prices", e)
            try:
                gaps = []
                for s in coins[:3]:
                    c = get(f"https://api.exchange.coinbase.com/products/{s}-USD/ticker")
                    if s in out:
                        gaps.append(abs(float(c["price"]) / out[s]["last"] - 1) * 100)
                    else:
                        out[s] = {"bid": float(c["bid"]), "ask": float(c["ask"]), "last": float(c["price"]), "src": "Coinbase"}
                worst = max(gaps) if gaps else 0
                self.h.ok("Coinbase check", f"max gap {worst:.2f}%")
                if worst > 0.5:
                    self.h.fail("Coinbase check", f"Kraken and Coinbase differ by {worst:.2f}%")
            except Exception as e:
                self.h.fail("Coinbase check", e)
        if stocks and self.akey:
            try:
                r = get("https://data.alpaca.markets/v2/stocks/quotes/latest?feed=iex&symbols=" + ",".join(stocks),
                        headers={"APCA-API-KEY-ID": self.akey, "APCA-API-SECRET-KEY": self.asec})
                for s, q in r.get("quotes", {}).items():
                    if q.get("bp") and q.get("ap") and q["ap"] >= q["bp"] and (q["ap"] / q["bp"] - 1) < 0.01:
                        out[s] = {"bid": q["bp"], "ask": q["ap"], "last": (q["bp"] + q["ap"]) / 2, "src": "Alpaca"}
                self.h.ok("Stock quotes", "Alpaca")
            except Exception as e:
                self.h.fail("Stock quotes", e)
        for s in stocks:
            if s in out:
                continue
            try:
                r, _ = self._yahoo(s, "1d", "5d")
                p = r["meta"]["regularMarketPrice"]
                out[s] = {"bid": p, "ask": p, "last": p, "src": "Yahoo"}
                self.h.ok("Stock quotes", "Yahoo")
            except Exception as e:
                self.h.fail("Stock quotes", f"{s}: {e}")
        return out


class Replay:
    """Feeds saved bars as if they were live, one step at a time."""
    def __init__(self, path):
        import json
        raw = json.load(open(path))
        self.h_bars = {s: v["h"] for s, v in raw.items()}
        self.d_bars = {s: v["d"] for s, v in raw.items()}
        self.h_idx = {s: [b[0] for b in v] for s, v in self.h_bars.items()}

    def daily(self, syms):
        today_utc = datetime.fromtimestamp(now_ts(), timezone.utc).strftime("%Y-%m-%d")
        today_et = et().strftime("%Y-%m-%d")
        return {s: [r for r in self.d_bars.get(s, []) if r[0] < (today_utc if is_coin(s) else today_et)] for s in syms}

    def hourly(self, syms):
        now = now_ts()
        return {s: self.h_bars[s][:bisect.bisect_right(self.h_idx[s], now)] for s in syms}

    def quotes(self, syms):
        now, out = now_ts(), {}
        for s in syms:
            i = bisect.bisect_right(self.h_idx[s], now) - 1
            if i < 0:
                continue
            ready, o, h, l, c = self.h_bars[s][i]
            if not is_coin(s) and not market_open(now) and now - ready > 0:
                pass
            out[s] = {"bid": c, "ask": c, "last": c, "src": "Replay", "low": l, "high": h}
        return out
