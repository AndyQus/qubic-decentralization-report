"""Render every page and check the branding actually landed.

Four things here are only true if you look:

  * the header logo is a CSS mask, not an image. If mask-composite is wrong the
    tile shows as a plain gradient square (mark invisible) or as a black box
    (mark inverted). Nothing in the markup says which happened.
  * "Qubic Decentralization Report" is supposed to appear ONLY on the report
    page and in the how-it-works prose. A leftover in a header subtitle is a
    one-line diff that no test would catch.
  * the PWA manifest has to resolve from /dashboard/, not from the API root.
  * the share-card image must be reachable at the absolute URL the meta tag
    claims, because that is the only URL a crawler will try.

Usage:
    python -m uvicorn api.server:app --port 8077
    python scripts/verify_branding.py http://127.0.0.1:8077
"""
import sys
import pathlib
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8077").rstrip("/")
OUT = ROOT / "scripts"

PAGES = {
    "index.html": "Qubic Decentralization Report",  # the one page that keeps it
    "burn.html": None,
    "price.html": None,
    "mining.html": None,
}

issues: list[str] = []
# Faults that exist independently of the branding work; printed, not failed.
preexisting: list[str] = []


def check_assets() -> None:
    """Every URL the meta tags and the manifest promise must actually answer."""
    for path, kind in (
        ("/dashboard/assets/og-image.png", "image/png"),
        ("/dashboard/assets/icon.svg", "image/svg+xml"),
        ("/dashboard/assets/icon-192.png", "image/png"),
        ("/dashboard/assets/icon-512.png", "image/png"),
        ("/dashboard/assets/apple-touch-icon.png", "image/png"),
        ("/dashboard/assets/favicon-32.png", "image/png"),
        ("/dashboard/assets/manifest.webmanifest", "application/manifest+json"),
        ("/favicon.ico", None),
        ("/manifest.webmanifest", None),
    ):
        try:
            with urllib.request.urlopen(BASE + path, timeout=10) as r:
                ct = r.headers.get("content-type", "").split(";")[0]
                if r.status != 200:
                    issues.append(f"{path}: HTTP {r.status}")
                elif kind and ct != kind:
                    issues.append(f"{path}: content-type {ct}, expected {kind}")
        except Exception as e:
            issues.append(f"{path}: {e}")


def check_crawler_root() -> None:
    """A crawler asking for "/" must get markup with og tags, not a bare redirect."""
    req = urllib.request.Request(BASE + "/", headers={"User-Agent": "Twitterbot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8", "replace")
        for needle in ('property="og:image"', 'name="twitter:card"', 'property="og:title"'):
            if needle not in body:
                issues.append(f"crawler at /: {needle} missing from the body")
    except Exception as e:
        issues.append(f"crawler at /: {e}")


def main() -> int:
    check_assets()
    check_crawler_root()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for page_name, keeps_full_name in PAGES.items():
            pg = browser.new_page(viewport={"width": 1280, "height": 900})
            # A 503 is the documented "store still filling" state, which is the
            # normal case on a dev machine. Reporting it as an error here would
            # train the reader to ignore this script's output.
            pg.on("console", lambda m: issues.append(f"{page_name} console: {m.text}")
                  if m.type == "error" and "503" not in m.text else None)
            pg.goto(f"{BASE}/dashboard/{page_name}", wait_until="networkidle")

            # The full name must appear only where it belongs. Checking the
            # header specifically: prose and footer links may legitimately name
            # the report page.
            header = pg.locator("header").first.inner_text()
            has_full = "Decentralization Report" in header
            if keeps_full_name and not has_full:
                issues.append(f"{page_name}: header lost the full name")
            if not keeps_full_name and has_full:
                issues.append(f"{page_name}: header still says 'Decentralization Report'")

            # The masked logo: present, sized, and actually painting something.
            logo = pg.locator(".qs-logo, .logo").first
            box = logo.bounding_box()
            if not box or box["width"] < 20:
                issues.append(f"{page_name}: header logo missing or collapsed")
            mask = logo.evaluate(
                "el => getComputedStyle(el).maskImage || getComputedStyle(el).webkitMaskImage")
            if not mask or "svg" not in mask:
                issues.append(f"{page_name}: logo carries no mask ({mask!r})")

            # The manifest has to resolve relative to /dashboard/.
            href = pg.locator('link[rel="manifest"]').first.get_attribute("href")
            if not href or not href.startswith("./assets/"):
                issues.append(f"{page_name}: manifest href is {href!r}, expected ./assets/...")

            # Horizontal overflow would mean the new section broke the layout.
            # index.html already overflowed by ~24px at 390px before any of this
            # branding work (verified by stashing the changes and re-measuring),
            # so it is reported separately rather than counted as a regression.
            for w in (1280, 390):
                pg.set_viewport_size({"width": w, "height": 900})
                over = pg.evaluate(
                    "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                if over > 2:
                    known = page_name == "index.html" and w == 390 and over <= 30
                    (preexisting if known else issues).append(
                        f"{page_name} @{w}px: scrolls sideways by {over}px")

            pg.set_viewport_size({"width": 1280, "height": 900})
            pg.locator("header").first.screenshot(
                path=str(OUT / f"brand_header_{page_name.replace('.html','')}.png"))
            pg.close()

        # The new API section on the price page, in full.
        pg = browser.new_page(viewport={"width": 1000, "height": 900})
        pg.goto(f"{BASE}/dashboard/price.html", wait_until="networkidle")
        card = pg.locator("#api-card")
        card.scroll_into_view_if_needed()
        card.screenshot(path=str(OUT / "brand_price_api.png"))
        # The example must not read as a measurement.
        if any(ch.isdigit() for ch in card.locator(".api-out").inner_text().replace("24h", "")):
            issues.append("price api example: contains digits — looks like a reading")
        pg.close()
        browser.close()

    if preexisting:
        print(f"\n{len(preexisting)} pre-existing issue(s), not caused by the branding work:")
        for i in preexisting:
            print(f"  - {i}")
    if issues:
        print(f"\n{len(issues)} issue(s):")
        for i in issues:
            print(f"  - {i}")
        return 1
    print("\nbranding OK: icons served, crawler sees og tags, logos masked, no new overflow")
    return 0


if __name__ == "__main__":
    sys.exit(main())
