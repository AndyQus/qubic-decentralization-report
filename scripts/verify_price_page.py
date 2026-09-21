"""Load dashboard/price.html against a running API, screenshot every state.

The chart is hand-rolled SVG, so nothing short of rendering it says whether the
geometry holds: label collisions, a step path degenerating to a flat line, a
mobile body that scrolls sideways. Those are only visible by looking.

Two things here are specific to this page and worth watching in the shots:

  * the price is ~4e-7 USD, so the axis labels carry many decimals — they are
    the most likely thing to collide or to be clipped by the left padding;
  * the series is STEPS. A rendered curve would mean the path builder emitted
    diagonals, which would be the page asserting movement it never measured.

Usage:
    python -m uvicorn api.server:app --port 8077        # with a filled store
    python scripts/verify_price_page.py http://127.0.0.1:8077

Writes scripts/price_*.png and reports console errors and horizontal overflow.
"""
import sys
import pathlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8077").rstrip("/")
URL = f"{BASE}/dashboard/price.html"

issues: list[tuple[str, str]] = []


def body_overflows(page) -> bool:
    """The page body must never scroll horizontally — wide content scrolls in
    its own container instead."""
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


def shot(page, name):
    page.screenshot(path=str(ROOT / "scripts" / f"price_{name}.png"), full_page=True)
    if body_overflows(page):
        issues.append(("overflow", f"{name}: body scrolls horizontally"))


with sync_playwright() as p:
    b = p.chromium.launch()

    # --- desktop ---------------------------------------------------------
    pg = b.new_page(viewport={"width": 1180, "height": 1600})

    def console(m):
        """A 503 is not a defect here.

        An unfilled store — no reading yet, or no epoch boundary watched yet —
        answers 503 by design, and the page turns that into a sentence. Counting
        it as an issue would train whoever runs this to ignore the output, which
        is worse than not checking at all. Everything else still counts.
        """
        if m.type not in ("error", "warning"):
            return
        if "503" in m.text or "Service Unavailable" in m.text:
            return
        issues.append((m.type, m.text))

    pg.on("console", console)
    pg.on("pageerror", lambda e: issues.append(("pageerror", str(e))))
    pg.goto(URL)
    pg.wait_for_timeout(2000)
    shot(pg, "dark_en_24h")

    # The step path is the page's central honesty claim: horizontal runs joined
    # by vertical jumps, never a diagonal. A path command list containing an
    # unexpected curve operator would mean the builder smoothed the data.
    d = pg.evaluate(
        "() => { const ps = document.querySelectorAll('#chart path');"
        " return ps.length ? ps[ps.length-1].getAttribute('d') : ''; }"
    )
    if not d:
        issues.append(("chart", "no path rendered — chart is empty"))
    elif any(c in d for c in ("C", "S", "Q", "T", "A")):
        issues.append(("honesty", "price path contains a curve command — "
                                  "the series must be drawn as steps"))

    pg.click("#lang-seg button[data-lang=de]")
    pg.wait_for_timeout(600)
    shot(pg, "dark_de_24h")

    for win, label in (("3600", "1h"), ("604800", "7d"), ("0", "all")):
        pg.click(f"#window-seg button[data-window='{win}']")
        pg.wait_for_timeout(900)
        shot(pg, f"dark_de_{label}")

    pg.click("#window-seg button[data-window='86400']")
    pg.wait_for_timeout(900)
    pg.click("#theme-btn")
    pg.wait_for_timeout(800)
    shot(pg, "light_de_24h")

    # The tooltip carries the figures that do not fit on the chart (held-for,
    # poll count), so it gets its own shot.
    # Moved to a point rather than hovering an element: the hit rects are one
    # per step and sit side by side, so Playwright's own actionability check
    # sees a neighbour "intercepting" the click target and retries forever.
    # The page listens on mousemove, so a plain pointer move is also the truer
    # test of what a reader does.
    box = pg.locator("#chart").bounding_box()
    if box:
        pg.mouse.move(box["x"] + box["width"] * 0.55,
                      box["y"] + box["height"] * 0.45)
        pg.wait_for_timeout(500)
        if not pg.locator("#tip.show").count():
            issues.append(("tooltip", "moving over the chart did not show the tooltip"))
    else:
        issues.append(("chart", "chart has no box — nothing rendered"))
    shot(pg, "light_de_tooltip")

    # The study is the reason this page exists, so its section must always say
    # something: the verdict once a boundary has been watched, otherwise the
    # sentence explaining that none has. A section that is simply blank is the
    # failure — a reader would scroll past an empty box and learn nothing.
    if not (pg.locator("#study-verdict").is_visible()
            or pg.locator("#study-empty").is_visible()):
        issues.append(("study", "the payout study section renders nothing at all"))

    # --- mobile ----------------------------------------------------------
    mob = b.new_page(viewport={"width": 390, "height": 1500},
                     device_scale_factor=2, is_mobile=True, has_touch=True)
    mob.on("pageerror", lambda e: issues.append(("pageerror(mobile)", str(e))))
    mob.goto(URL)
    mob.wait_for_timeout(2000)
    shot(mob, "mobile_dark_en")

    mob.click("#lang-seg button[data-lang=de]")
    mob.wait_for_timeout(700)
    shot(mob, "mobile_dark_de")

    b.close()

print("ISSUES:", len(issues))
for kind, msg in issues[:25]:
    print(" ", kind, "-", msg[:220])
print("screens written to scripts/price_*.png")
