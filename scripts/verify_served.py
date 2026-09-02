"""Load the API-served dashboard and the embed example over http; check + screenshot."""
import sys, pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8000"
errors = []

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width":1180,"height":1400})
    pg.on("console", lambda m: errors.append((m.type, m.text)) if m.type=="error" else None)
    pg.on("pageerror", lambda e: errors.append(("pageerror", str(e))))

    # 1) dashboard served by the API (same-origin auto-fetch of /v1/dashboard-data)
    pg.goto(BASE + "/dashboard/", wait_until="networkidle"); pg.wait_for_timeout(1400)
    ok_epoch = pg.text_content("#hdr-epoch")
    ok_ops = pg.text_content(".kpi .k-val")
    pg.screenshot(path=str(ROOT/"scripts"/"shot_served_dashboard.png"), full_page=True)
    print("dashboard served: epoch=", ok_epoch, "first KPI=", ok_ops)

    # 2) embed widget example
    pg.goto(BASE + "/dashboard/../examples/embed-example.html", wait_until="networkidle")
    # data-api="" -> same origin; give it a moment to fetch
    pg.wait_for_timeout(1200)
    txt = pg.text_content("#qdr-dark") or ""
    print("embed rendered contains 'Nakamoto' or 'operators':", ("Nakamoto" in txt or "operators" in txt or "Betreiber" in txt))
    pg.screenshot(path=str(ROOT/"scripts"/"shot_served_embed.png"), full_page=True)

    b.close()

print("CONSOLE ERRORS:", len(errors))
for t,m in errors[:15]: print(" ", t, "-", m[:200])
