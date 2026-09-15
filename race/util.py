import json, os, time, urllib.request, urllib.parse, hashlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UA = "Mozilla/5.0 (paper-race research bot; contact via GitHub repo)"
HERE = os.path.dirname(os.path.abspath(__file__))


def now_ts():
    return float(os.environ.get("RACE_NOW", time.time()))


def et(ts=None):
    return datetime.fromtimestamp(now_ts() if ts is None else ts, ET)


def get(url, headers=None, timeout=20, raw=False, data=None, method=None):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return body if raw else json.loads(body)


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"), default=float)
    os.replace(tmp, path)


def uid(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def market_open(ts=None):
    d = et(ts)
    if d.weekday() >= 5:
        return False
    m = d.hour * 60 + d.minute
    return 9 * 60 + 30 <= m < 16 * 60


class Health:
    def __init__(self, prev):
        self.s = prev or {}

    def ok(self, name, note=""):
        self.s[name] = {"ok": True, "at": now_ts(), "note": note}

    def fail(self, name, err):
        prev = self.s.get(name, {})
        self.s[name] = {"ok": False, "at": prev.get("at"), "failed_at": now_ts(), "note": str(err)[:120]}
