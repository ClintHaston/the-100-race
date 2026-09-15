"""Phone alerts through ntfy, quiet hours, daily recap, and optional status sync from a private Google Sheet."""
import os, urllib.parse
from .util import get, now_ts, et, uid


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

    def add(self, lane, kind, sym, title, detail, dollars=None, stop=None, tp=None, push=True, urgent=False):
        a = {"id": uid(lane, kind, sym, now_ts()), "ts": now_ts(), "lane": lane, "kind": kind, "symbol": sym,
             "title": title, "detail": detail, "dollars": dollars, "stop": stop, "tp": tp,
             "status": {"buy": "open", "sell": "open", "blocked": "blocked"}.get(kind, "info"),
             "mode": self.cfg["alert_mode"], "pushed": False}
        if kind in ("buy", "sell") and (self.cfg["alert_mode"] == "report" or lane not in self.cfg["alert_lanes"]):
            a["status"] = "info"
            a["mode"] = "report"
        self.log.insert(0, a)
        del self.log[500:]
        wanted = lane in self.cfg["alert_lanes"] or lane == "ALL"
        if push and wanted and (urgent or kind == "sell" or not self.quiet()):
            self._push(a, urgent or kind == "sell")
        return a

    def flush_held(self):
        if self.quiet():
            return
        for a in reversed(self.log):
            if not a["pushed"] and a["kind"] in ("buy", "blocked") and now_ts() - a["ts"] < 12 * 3600 \
                    and (a["lane"] in self.cfg["alert_lanes"]):
                self._push(a, False)

    def _form_link(self, a, choice):
        if not self.form:
            return None
        return self.form.replace("ALERT_ID", a["id"]).replace("CHOICE", urllib.parse.quote(choice))

    def _push(self, a, urgent):
        if not self.topic:
            return
        body = a["detail"]
        if a.get("stop") or a.get("tp"):
            body += f"\nStop-loss {a['stop'] or 'none'}, take-profit {a['tp'] or 'none'}"
        headers = {"Title": a["title"].encode("utf-8"), "Priority": "high" if urgent else "default",
                   "Tags": {"buy": "chart_with_upwards_trend", "sell": "chart_with_downwards_trend",
                            "blocked": "no_entry", "recap": "checkered_flag"}.get(a["kind"], "information_source")}
        if self.dash:
            headers["Click"] = f"{self.dash}#alerts"
        actions = []
        if a["kind"] in ("buy", "sell") and a["mode"] == "signal":
            for label, choice in (("I did it", "Done"), ("Skip", "Skipped")):
                link = self._form_link(a, choice)
                if link:
                    actions.append(f"view, {label}, {link}")
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
            rows = get(self.status_url)
            m = {r["id"]: r["status"] for r in rows if "id" in r}
            for a in self.log:
                if a["id"] in m and a["status"] == "open":
                    a["status"] = "done" if m[a["id"]].lower().startswith("done") else "skipped"
            self.h.ok("My trades sync")
        except Exception as e:
            self.h.fail("My trades sync", e)
