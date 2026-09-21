"""The ?v= on theme.css must match the file it points at.

The pages link the shared stylesheet with a content hash, because the API sends
`no-cache` (store it, but revalidate) and a proxy that ignores the header would
otherwise keep serving an old stylesheet against markup that already expects the
new one — which looks like a broken page, not merely a stale one.

Nothing generated that hash: it was written by hand, so it silently went stale
the first time the stylesheet changed without someone remembering to update it.
This test is that someone.
"""
import hashlib
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSS = ROOT / "dashboard" / "theme.css"
LINK = re.compile(r"theme\.css\?v=([0-9a-f]+)")


def expected_hash() -> str:
    return hashlib.sha256(CSS.read_bytes()).hexdigest()[:8]


def test_every_page_links_the_current_stylesheet_hash():
    want = expected_hash()
    checked = 0
    for page in sorted((ROOT / "dashboard").glob("*.html")):
        found = LINK.findall(page.read_text(encoding="utf-8"))
        for got in found:
            checked += 1
            assert got == want, (
                f"{page.name} links theme.css?v={got}, but the file hashes to "
                f"{want}. Update the ?v= in every dashboard/*.html that links it."
            )
    assert checked, "no page links theme.css — has the link been renamed?"


def test_the_pages_agree_with_each_other():
    """One hash, or a reader gets different stylesheets on different pages."""
    seen = {}
    for page in sorted((ROOT / "dashboard").glob("*.html")):
        for got in LINK.findall(page.read_text(encoding="utf-8")):
            seen.setdefault(got, []).append(page.name)
    assert len(seen) <= 1, f"pages disagree on the stylesheet hash: {seen}"
