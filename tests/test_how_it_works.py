"""The /how-it-works page is generated from the concept — keep it that way.

The page's whole value is that it cannot drift from docs/CONCEPT*.md. That
guarantee is only real if something fails when someone edits the concept and
forgets to regenerate, so that check lives here rather than in a comment.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_how_it_works import OUT, SOURCES, build, render  # noqa: E402


def test_page_is_in_sync_with_the_concept():
    """Fails when docs/CONCEPT*.md changed but the page was not regenerated."""
    assert OUT.exists(), "run: python scripts/build_how_it_works.py"
    assert OUT.read_text(encoding="utf-8") == build(), (
        "dashboard/how-it-works.html is stale.\n"
        "Run: python scripts/build_how_it_works.py"
    )


def test_check_flag_reports_sync():
    """The CI entry point (--check) agrees with the test above."""
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_how_it_works.py"), "--check"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert r.returncode == 0, r.stderr


def test_both_languages_are_rendered():
    html = OUT.read_text(encoding="utf-8")
    for lang in ("en", "de"):
        assert f'data-lang-block="{lang}"' in html


def test_concept_sections_reach_the_page():
    """Every numbered concept section shows up, so nothing is silently dropped."""
    for lang, src in SOURCES.items():
        md = src.read_text(encoding="utf-8")
        body, toc, _ = render(md, lang)
        # section 4.4 is the slot-transition logic; it must survive the render
        assert "4.4" in " ".join(t for _, t in toc) or "4.4" in body
        # internal bookkeeping sections stay off the public page
        assert not any(t.startswith(("8.", "9.")) for _, t in toc)


def test_no_unbalanced_markup():
    html = OUT.read_text(encoding="utf-8")
    for tag in ("div", "article", "nav", "table", "ul", "ol", "p"):
        opened = len(re.findall(rf"<{tag}[ >]", html))
        closed = len(re.findall(rf"</{tag}>", html))
        assert opened == closed, f"<{tag}> unbalanced: {opened} open / {closed} close"


def test_internal_links_resolve():
    """Table-of-contents and cross-reference links must point at real anchors."""
    html = OUT.read_text(encoding="utf-8")
    ids = set(re.findall(r'<h[234] id="([^"]+)"', html))
    for href in re.findall(r'href="#([^"]+)"', html):
        assert href in ids, f"dangling anchor #{href}"


def test_wide_tables_are_scroll_wrapped():
    """Tables must scroll in their own box so phones never scroll sideways."""
    html = OUT.read_text(encoding="utf-8")
    assert html.count("<table>") == html.count('<div class="tw">')


def test_page_is_mobile_ready():
    html = OUT.read_text(encoding="utf-8")
    assert 'name="viewport"' in html
    assert "@media (max-width:640px)" in html


def test_dashboard_links_to_the_page():
    index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert index.count("./how-it-works.html") >= 1
    # the link is translated like everything else in the header
    assert 'data-i18n="howItWorks"' in index


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
