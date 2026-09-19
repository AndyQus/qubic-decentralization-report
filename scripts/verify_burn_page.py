"""Load dashboard/burn.html against a running API, screenshot every state.

The chart is hand-rolled SVG, so the validator that checks colour says nothing
about geometry: label collisions, bars degenerating to hairlines, a mobile body
that scrolls sideways. Those are only visible by rendering it and looking.

Usage:
    python -m uvicorn api.server:app --port 8079        # with a filled store
    python scripts/verify_burn_page.py http://127.0.0.1:8079

Writes scripts/burn_*.png and reports console errors and any horizontal overflow.
"""
import sys
import pathlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8079").rstrip("/")
URL = f"{BASE}/dashboard/burn.html"

issues: list[tuple[str, str]] = []


def body_overflows(page) -> bool:
    """The page body must never scroll horizontally — wide content scrolls in
    its own container instead."""
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


def shot(page, name):
    page.screenshot(path=str(ROOT / "scripts" / f"burn_{name}.png"), full_page=True)
    if body_overflows(page):
        issues.append(("overflow", f"{name}: body scrolls horizontally"))


with sync_playwright() as p:
    b = p.chromium.launch()

    # --- desktop ---------------------------------------------------------
    pg = b.new_page(viewport={"width": 1180, "height": 1500})
    pg.on("console", lambda m: issues.append((m.type, m.text))
          if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: issues.append(("pageerror", str(e))))
    pg.goto(URL)
    pg.wait_for_timeout(1800)
    shot(pg, "dark_en_day")

    pg.click("#lang-seg button[data-lang=de]")
    pg.wait_for_timeout(500)
    shot(pg, "dark_de_day")

    pg.click("#mode-seg button[data-mode=cumulative]")
    pg.wait_for_timeout(700)
    shot(pg, "dark_de_cumulative")

    pg.click("#mode-seg button[data-mode=delta]")
    pg.click("#by-seg button[data-by=epoch]")
    pg.wait_for_timeout(900)
    shot(pg, "dark_de_epoch")

    pg.click("#by-seg button[data-by=year]")
    pg.wait_for_timeout(900)
    shot(pg, "dark_de_year")

    pg.click("#by-seg button[data-by=day]")
    pg.click("#theme-btn")
    pg.wait_for_timeout(800)
    shot(pg, "light_de_day")

    # The tooltip is the only place the exact figure appears on the chart, so it
    # gets its own shot. nth-of-type would index among ALL sibling SVG elements
    # (bars, grid lines, labels), not among the hit rects — .nth() is what picks
    # the Nth match of the selector itself.
    hits = pg.locator(".bar-hit")
    if hits.count():
        hits.nth(max(0, hits.count() // 2)).hover()
        pg.wait_for_timeout(400)
        if not pg.locator("#tip.show").count():
            issues.append(("tooltip", "hovering a bar did not show the tooltip"))
    else:
        issues.append(("chart", "no hit targets rendered — chart is empty"))
    shot(pg, "light_de_tooltip")

    # --- mobile ----------------------------------------------------------
    mob = b.new_page(viewport={"width": 390, "height": 1400},
                     device_scale_factor=2, is_mobile=True, has_touch=True)
    mob.on("pageerror", lambda e: issues.append(("pageerror(mobile)", str(e))))
    mob.goto(URL)
    mob.wait_for_timeout(1800)
    shot(mob, "mobile_dark_en")

    mob.click("#lang-seg button[data-lang=de]")
    mob.wait_for_timeout(600)
    shot(mob, "mobile_dark_de")

    b.close()

print("ISSUES:", len(issues))
for kind, msg in issues[:25]:
    print(" ", kind, "-", msg[:220])
print("screens written to scripts/burn_*.png")
