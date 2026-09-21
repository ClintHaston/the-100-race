"""Headless check of every tab and control. Prints pass and fail counts."""
import sys, json
from playwright.sync_api import sync_playwright
URL = sys.argv[1]
results = []
def check(name, ok, info=""):
    results.append((name, bool(ok), info)); print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""))

with sync_playwright() as p:
    b = p.chromium.launch()
    for label, vw in (("desktop", 1440), ("phone", 390)):
        pg = b.new_page(viewport={"width": vw, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "fonts" not in m.text else None)
        pg.goto(URL); pg.wait_for_selector("#track svg", timeout=15000)
        check(f"{label}: page loads with standings", pg.locator("#track svg").count() == 1)
        expected = pg.evaluate("D.race.lanes.filter(l=>!l.shadow).length + D.race.bench.length")
        check(f"{label}: standings shows every lane and benchmark", pg.locator("#track .bval").count() == expected, f"{pg.locator('#track .bval').count()} of {expected}")
        check(f"{label}: no sideways scroll", pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"),
              pg.evaluate("document.documentElement.scrollWidth"))
        for tab, panel, sel in [("race", "p-race", "#chart svg"), ("positions", "p-positions", "#closed table, #closed .empty"),
                                ("deck", "p-deck", "#deck-full .w8, #deck-full .empty"), ("alerts", "p-alerts", "#al-full .al"),
                                ("markets", "p-markets", "#mk-full table"), ("feed", "p-feed", "#feed-full .fi"),
                                ("mine", "p-mine", "#sheet"), ("settings", "p-settings", "#settings dt")]:
            pg.click(f"#t-{tab}")
            vis = pg.locator(f"#{panel}").is_visible()
            others = pg.locator(".panel.on").count()
            check(f"{label}: tab '{tab}' shows its panel only", vis and others == 1)
            check(f"{label}: tab '{tab}' has content", pg.locator(sel).count() > 0, pg.locator(sel).count())
            check(f"{label}: tab '{tab}' updates the link", pg.evaluate("location.hash") == "#" + tab)
        pg.click("#t-race")
        n0 = pg.locator("#chart polyline").count()
        pg.locator("#legend .lg").first.click()
        n1 = pg.locator("#chart polyline").count()
        check(f"{label}: chart legend hides a line", n1 == n0 - 1, f"{n0} to {n1}")
        pg.locator("#legend .lg").first.click()
        check(f"{label}: chart legend shows it again", pg.locator("#chart polyline").count() == n0)
        pg.click("#t-alerts")
        total = pg.locator("#al-full .al").count()
        pg.click("#al-filter [data-k=blocked]")
        blocked = pg.locator("#al-full .al").count()
        check(f"{label}: alert filter narrows the list", blocked <= total and pg.locator("#al-full .al:not(.s-blocked)").count() == 0, f"{total} to {blocked}")
        pg.click("#al-filter [data-k=all]")
        pg.click("#t-feed"); pg.click("#fd-filter [data-k=system]")
        check(f"{label}: feed filter shows only system items", pg.locator("#feed-full .fk:not(.k-system)").count() == 0)
        pg.click("#t-mine")
        pg.fill("#sheet", "https://docs.google.com/spreadsheets/d/example")
        pg.fill("#form", "https://docs.google.com/forms/d/e/x/viewform?entry.1=ALERT_ID&entry.2=CHOICE")
        pg.click("#savelinks")
        check(f"{label}: private links save on this device", "Open my private trade sheet" in pg.inner_text("#mine-link"))
        pg.click("#t-alerts")
        opens = pg.locator("#al-full .s-open").count()
        btns = pg.locator("#al-full .s-open .btns button").count()
        check(f"{label}: open alerts get I did it and Skip buttons", opens == 0 or btns == opens * 2, f"{opens} open, {btns} buttons")
        if btns:
            sent = []
            pg.route("**/formResponse*", lambda route: (sent.append(route.request.url), route.fulfill(status=200, body="ok")))
            first = pg.locator("#al-full .s-open").first
            aid = first.get_attribute("id")[2:]
            first.locator("button[data-choice=Done]").click()
            pg.wait_for_timeout(800)
            check(f"{label}: one tap sends the trade to the form", len(sent) == 1 and aid in sent[0] and "Done" in sent[0], sent[0][-60:] if sent else "nothing sent")
            check(f"{label}: tapped alert shows as Done right away", pg.locator(f"#a-{aid}.s-done").count() >= 1)
            check(f"{label}: amount, asset and platform are filled in", sent and "Kraken" in sent[0] and "entry.1234101252=" in sent[0])
        pg.reload(); pg.wait_for_selector("#track svg", state="attached")
        check(f"{label}: reload reopens the same tab", pg.locator("#p-alerts").is_visible())
        pg.click("#t-race")
        check(f"{label}: reload keeps working", pg.locator("#track .bval").count() == expected)
        check(f"{label}: no script errors", not errs, "; ".join(errs)[:200])
        pg.screenshot(path=f"/tmp/dash_{label}.png", full_page=True)
        pg.close()
    pg = b.new_page()
    pg.goto(URL + "?data=/missing/"); pg.wait_for_timeout(2500)
    check("missing data shows a clear message", "could not be loaded" in pg.inner_text("#error"))
    b.close()
passed = sum(r[1] for r in results)
print(f"\n{passed} passed, {len(results) - passed} failed, {len(results)} checks")
sys.exit(0 if passed == len(results) else 1)
