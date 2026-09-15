"""Claude Haiku, used for two small jobs only: a buy veto and a morning brief. Hard monthly cap."""
import os, json, re
from .util import get, et

IN_RATE, OUT_RATE = 1.0 / 1e6, 5.0 / 1e6   # Claude Haiku 4.5 list price per token


class AI:
    def __init__(self, cfg, health, cache):
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        self.model, self.cap = cfg["ai_model"], cfg["ai_monthly_cap_usd"]
        self.h = health
        month = et().strftime("%Y-%m")
        self.b = cache.setdefault("ai", {"month": month, "cost": 0.0, "calls": 0})
        if self.b["month"] != month:
            self.b.update(month=month, cost=0.0, calls=0)

    def available(self):
        return bool(self.key) and self.b["cost"] < self.cap

    def _ask(self, system, prompt, max_tokens=300):
        body = json.dumps({"model": self.model, "max_tokens": max_tokens, "system": system,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        r = get("https://api.anthropic.com/v1/messages", data=body, method="POST", timeout=40,
                headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        u = r.get("usage", {})
        self.b["cost"] = round(self.b["cost"] + u.get("input_tokens", 0) * IN_RATE + u.get("output_tokens", 0) * OUT_RATE, 5)
        self.b["calls"] += 1
        text = "".join(c.get("text", "") for c in r.get("content", []))
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}

    def veto(self, sym, headlines, events):
        """Returns (veto: bool, reason: str). Falls back to None when AI is unavailable."""
        if not self.available():
            return None
        lines = "\n".join(f"- [{h['source']}] {h['text']}" for h in headlines[:25]) or "- (no headlines in the last 48 hours)"
        ev = ", ".join(e["title"] for e in events) or "none"
        system = ("You screen a rules-based paper trading system. You do not pick trades. Your only job is to block a buy "
                  "when recent news shows a specific, material problem for this asset: fraud or accounting issues, regulatory "
                  "action, a hack or exploit, bankruptcy or default risk, delisting or trading halt, a guidance cut, or a "
                  "product recall. Ordinary price moves, opinions, and analyst chatter are not reasons to block. "
                  'Reply with JSON only: {"veto": true or false, "reason": "under 15 words"}')
        try:
            out = self._ask(system, f"Asset: {sym}\nHigh-impact US events today: {ev}\nHeadlines from the last 48 hours:\n{lines}", 120)
            self.h.ok("AI check", f"${self.b['cost']:.2f} spent this month")
            return bool(out.get("veto")), str(out.get("reason", ""))[:120]
        except Exception as e:
            self.h.fail("AI check", e)
            return None

    def brief(self, mood, events, headlines):
        if not self.available():
            return None
        system = ("You write a short morning brief for a paper trading dashboard and choose a risk mode. "
                  "Choose 'careful' only when a scheduled high-impact US event today (such as CPI, jobs report, or a Fed "
                  "decision) or clearly stressed markets make new buys unwise today. Otherwise choose 'normal'. "
                  'Reply with JSON only: {"mode": "normal" or "careful", "brief": "two plain sentences, no jargon"}')
        prompt = (f"Mood gauges: {json.dumps({k: v for k, v in mood.items() if k != 'reddit'}, default=str)}\n"
                  f"High-impact US events today: {', '.join(e['title'] for e in events) or 'none'}\n"
                  "Top headlines:\n" + "\n".join(f"- {h['text']}" for h in headlines[:20]))
        try:
            out = self._ask(system, prompt, 200)
            self.h.ok("AI check", f"${self.b['cost']:.2f} spent this month")
            return out
        except Exception as e:
            self.h.fail("AI check", e)
            return None
