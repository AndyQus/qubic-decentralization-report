"""Load the dashboard in headless chromium, capture console errors, screenshot."""
import sys, pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
url = (ROOT / "dashboard" / "index.html").as_uri()
errors = []

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width":1180,"height":1500})
    pg.on("console", lambda m: errors.append((m.type, m.text)) if m.type in ("error","warning") else None)
    pg.on("pageerror", lambda e: errors.append(("pageerror", str(e))))
    pg.goto(url); pg.wait_for_timeout(1600)
    pg.screenshot(path=str(ROOT/"scripts"/"shot_dark_en.png"), full_page=True)
    # switch to German
    pg.click("#lang-seg button[data-lang=de]"); pg.wait_for_timeout(500)
    pg.screenshot(path=str(ROOT/"scripts"/"shot_dark_de.png"), full_page=True)
    # light theme
    pg.click("#theme-btn"); pg.wait_for_timeout(800)
    pg.screenshot(path=str(ROOT/"scripts"/"shot_light_de.png"), full_page=True)
    # scrub to an earlier epoch (test animation state)
    pg.eval_on_selector("#epoch-range", "el=>{el.value='2'; el.dispatchEvent(new Event('input'))}")
    pg.wait_for_timeout(900)
    pg.screenshot(path=str(ROOT/"scripts"/"shot_light_de_ep.png"), full_page=True)
    b.close()

print("CONSOLE ISSUES:", len(errors))
for t,m in errors[:20]: print(" ", t, "-", m[:200])
print("screens written to scripts/shot_*.png")
