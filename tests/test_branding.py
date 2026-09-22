"""The branding assets and where the project's name is allowed to appear.

Two classes of mistake are easy to make here and invisible until someone shares
a link or installs the app:

  * an icon referenced by a page or the manifest that is not in the repo. The
    runtime image cannot generate these (no Pillow, no SVG renderer), so a
    missing file is a 404 in production, not a build error.
  * "Qubic Decentralization Report" creeping back into a page that is not the
    report. The project is an application called "Qubic Report" with several
    pages; only the report page and the concept page carry the long name.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DASH = ROOT / "dashboard"
ASSETS = DASH / "assets"

# Pages that legitimately carry the full name, and why.
KEEPS_LONG_NAME = {
    "index.html",       # it IS the Decentralization Report
    "how-it-works.html",  # explains that concept by name, in prose
}
# Pages that must not carry it in their <title> or header subtitle.
RENAMED = ["burn.html", "price.html", "mining.html", "log.html"]


def test_every_referenced_icon_exists():
    """A <link rel=icon|manifest|apple-touch-icon> must point at a real file."""
    missing = []
    for page in sorted(DASH.glob("*.html")):
        src = page.read_text(encoding="utf-8")
        for href in re.findall(r'<link[^>]+href="(\./assets/[^"]+)"', src):
            target = DASH / href[2:]
            if not target.exists():
                missing.append(f"{page.name} -> {href}")
    assert not missing, "referenced but absent: " + ", ".join(missing)


def test_manifest_icons_exist_and_name_the_app():
    m = json.loads((ASSETS / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert m["name"] == "Qubic Report"
    # Relative, because the dashboard is served under /dashboard/ — an absolute
    # "/" would scope the PWA to the API root, where the pages do not live.
    assert not m["start_url"].startswith("/"), "start_url must be relative"
    assert not m["scope"].startswith("/"), "scope must be relative"
    for icon in m["icons"]:
        assert (ASSETS / icon["src"]).exists(), f"manifest names a missing icon: {icon['src']}"


def test_the_share_card_is_a_png_of_the_right_size():
    """X does not render SVG, and it reads the declared dimensions."""
    from PIL import Image  # dev-only dependency; the runtime never needs it

    with Image.open(ASSETS / "og-image.png") as im:
        assert (im.width, im.height) == (1200, 630)

    for page in sorted(DASH.glob("*.html")):
        src = page.read_text(encoding="utf-8")
        for url in re.findall(r'<meta property="og:image" content="([^"]+)"', src):
            # Absolute, or a crawler cannot resolve it at all.
            assert url.startswith("https://"), f"{page.name}: og:image is not absolute"
            assert url.endswith(".png"), f"{page.name}: og:image must be a PNG"


def test_only_the_report_pages_carry_the_long_name():
    for name in RENAMED:
        page = DASH / name
        if not page.exists():
            continue
        src = page.read_text(encoding="utf-8")
        title = re.search(r"<title>(.*?)</title>", src, re.S)
        assert title, f"{name} has no <title>"
        assert "Decentralization Report" not in title.group(1), (
            f"{name}: the page title still says 'Decentralization Report'. "
            "That name belongs to the report page; other pages use 'Qubic Report'."
        )


def test_the_report_page_keeps_its_name():
    """The rename must not erase the one place the full name belongs."""
    for name in KEEPS_LONG_NAME:
        page = DASH / name
        if page.exists():
            assert "Qubic Decentralization Report" in page.read_text(encoding="utf-8"), (
                f"{name} lost the project's full name"
            )


def test_every_page_declares_a_share_card_and_an_icon():
    for page in sorted(DASH.glob("*.html")):
        if page.name == "log.html":
            continue  # operator page, deliberately not shareable
        src = page.read_text(encoding="utf-8")
        for needle in ('property="og:image"', 'name="twitter:card"', 'rel="manifest"'):
            assert needle in src, f"{page.name} is missing {needle}"
