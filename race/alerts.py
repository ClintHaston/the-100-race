"""Phone alerts through ntfy, quiet hours, daily recap, and optional status sync from a private Google Sheet."""
import os, time, urllib.parse
from .util import get, now_ts, et, uid
from .market import is_coin


class Alerts:
    def __init__(self, cfg, health, log):
        self.cfg, self.h, self.log = cfg, health, log
        self.topic = os.environ.get("NTFY_TOPIC")
        self.dash = os.environ.get("DASHBOARD_URL", "")
        self.form = os.environ.get("FORM_URL") or cfg.get("form_url", "")
        self.status_url = os.environ.get("STATUS_URL")

    def quiet(self):
        start, end = self.cfg["quiet_hours_et"]
        h = et().hour
        return h >= start or h < end

    def wanted(self, lane, kind):
        """Only real-money buy and sell signals are alerts. Everything else goes to the news feed."""
        return kind in ("buy", "sell") and lane in self.cfg["alert_lanes"] and self.cfg["alert_mode"] == "signal"

    def keep(self, lane, kind):
        """Signals from the current or a past real-money lane stay in the log, so your holdings stay correct."""
        return kind in ("buy", "sell") and lane in self.cfg["alert_lanes"] + self.cfg.get("past_alert_lanes", [])

    def real_holdings(self):
        """What you actually own, from the buys and sells you marked done."""
        held = {}
        for a in sorted(self.log, key=lambda a: a["ts"]):
            if a.get("status") != "done" or not self.keep(a["lane"], a["kind"]):
                continue
            if a["kind"] == "buy":
                held[a["symbol"]] = held.get(a["symbol"], 0) + (a.get("dollars") or 0)
            elif a["kind"] == "sell":
                held.pop(a["symbol"], None)
        return held

    def add(self, lane, kind, sym, title, detail, dollars=None, stop=None, tp=None, push=True, urgent=False, price=None):
        a = {"id": uid(lane, kind, sym, now_ts()), "ts": now_ts(), "lane": lane, "kind": kind, "symbol": sym,
             "title": title, "detail": detail, "dollars": dollars, "stop": stop, "tp": tp, "price": price,
             "status": {"buy": "open", "sell": "open", "blocked": "blocked"}.get(kind, "info"),
             "mode": self.cfg["alert_mode"], "pushed": False}
        if not self.wanted(lane, kind):
            return a
        self.log.insert(0, a)
        del self.log[500:]
        if push and (kind == "sell" or not self.quiet()):
            self._push(a, kind == "sell")
        return a

    def flush_held(self):
        if self.quiet():
            return
        for a in reversed(self.log):
            if not a["pushed"] and a["kind"] == "buy" and a["status"] == "open" and now_ts() - a["ts"] < 12 * 3600:
                self._push(a, False)

    @staticmethod
    def kraken_link(sym):
        """Coins open Kraken Pro's trade screen; stocks open Kraken's stock page."""
        if not sym:
            return None
        return f"https://pro.kraken.com/app/trade/{sym.lower()}-usd" if is_coin(sym) else f"https://www.kraken.com/stocks/{sym.lower()}"

    def _form_link(self, a, choice):
        if not self.form:
            return None
        return self.form.replace("ALERT_ID", a["id"]).replace("CHOICE", urllib.parse.quote(choice))

    def one_tap_link(self, a, choice):
        """A link that logs the trade in the Sheet with a single tap, no form to fill in."""
        e = self.cfg.get("form_entries")
        if not self.form or not e or "/viewform" not in self.form:
            return None
        base = self.form.split("/viewform")[0] + "/formResponse"
        fields = {e["id"]: a["id"], e["choice"]: choice, e["platform"]: self.cfg.get("platform", ""),
                  e["asset"]: a.get("symbol") or "", e["dollars"]: "" if a.get("dollars") is None else f"{a['dollars']:.2f}",
                  e["price"]: "" if a.get("price") is None else f"{a['price']:.6g}",
                  e["notes"]: f"{a['kind'].title()} logged with one tap. Price is the signal price; edit it here if yours differed."}
        return base + "?" + urllib.parse.urlencode({f"entry.{k}": v for k, v in fields.items()})

    def _push(self, a, urgent):
        if not self.topic:
            return
        body = a["detail"]
        if a.get("stop") or a.get("tp"):
            body += f"\nStop-loss {a['stop'] or 'none'}, take-profit {a['tp'] or 'none'}"
        headers = {"Title": a["title"].encode("utf-8"), "Priority": "high" if urgent else "default",
                   "Tags": {"buy": "chart_with_upwards_trend", "sell": "chart_with_downwards_trend",
                            "blocked": "no_entry", "recap": "checkered_flag"}.get(a["kind"], "information_source")}
        actions = []
        # kraken.com/pay is claimed by the Kraken Android app, so this button opens the app itself.
        actions.append(f"view, Open Kraken, https://www.kraken.com/pay")
        if self.dash:
            headers["Click"] = f"{self.dash}#log-{a['id']}"
            actions.append(f"view, I did it, {self.dash}#log-{a['id']}")
        skip = self.one_tap_link(a, "Skipped")
        if skip:
            actions.append(f"http, Skip, {skip}, method=POST, clear=true")
        if actions:
            headers["Actions"] = "; ".join(actions)
        try:
            get(f"https://ntfy.sh/{self.topic}", data=body.encode("utf-8"), method="POST", raw=True,
                headers={k: (v if isinstance(v, str) else v.decode()) for k, v in headers.items()})
            a["pushed"] = True
            self.h.ok("Phone alerts")
        except Exception as e:
            self.h.fail("Phone alerts", e)

    def sync_status(self):
        """Reads only alert id and Done or Skipped from the private sheet's web app. No dollar amounts."""
        if not self.status_url:
            return
        try:
            for attempt in range(3):
                try:
                    rows = get(self.status_url)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(3 * (attempt + 1))
            m = {r["id"]: r["status"] for r in rows if "id" in r}
            for a in self.log:
                if a["id"] in m and a["status"] == "open":
                    a["status"] = "done" if m[a["id"]].lower().startswith("done") else "skipped"
            self.h.ok("My trades sync")
        except Exception as e:
            self.h.fail("My trades sync", e)
