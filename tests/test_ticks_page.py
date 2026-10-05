"""The ticks page's own rules, pinned so a later edit cannot quietly break them.

  * it is reachable from every other page, and links back to them;
  * both language dictionaries are complete, and every data-i18n key in the
    markup has a translation — a missing key surfaces to a reader as a raw
    identifier;
  * it loads no third-party code (the original Star Rain pulled three font
    families and an icon set from Google on every visit);
  * every API path it calls exists on the server.

Rendering and frame rate are checked in a browser, not here; no test here
touches the network.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DASH = ROOT / "dashboard"
PAGE = DASH / "ticks.html"
SCRIPT = DASH / "ticks.js"


def html() -> str:
    return PAGE.read_text(encoding="utf-8")


def js() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_page_is_linked_from_every_sibling():
    for name in ("index.html", "burn.html", "price.html", "mining.html"):
        src = (DASH / name).read_text(encoding="utf-8")
        assert 'href="./ticks.html"' in src, f"{name} does not link the ticks page"
        assert "ticksLive" in src, f"{name} links the page but has no label for it"


def test_page_links_back_to_the_others():
    src = html()
    for href in ("./", "./burn.html", "./price.html", "./mining.html", "./how-it-works.html"):
        assert f'href="{href}"' in src, f"nav is missing {href}"
    assert 'href="./ticks.html" aria-current="page"' in src


def _dict_keys(block: str) -> set[str]:
    return set(re.findall(r"^\s*(\w+):", block, re.M)) | set(re.findall(r",\s*(\w+):", block))


def test_both_language_dictionaries_cover_the_same_keys():
    src = js()
    en = src[src.index("en: {") + 5:src.index("de: {")]
    de = src[src.index("de: {") + 5:src.index("var lang =")]
    ken, kde = _dict_keys(en), _dict_keys(de)
    assert ken - kde == set(), f"missing in DE: {sorted(ken - kde)}"
    assert kde - ken == set(), f"missing in EN: {sorted(kde - ken)}"


def test_every_markup_key_is_translated():
    src = js()
    en = _dict_keys(src[src.index("en: {") + 5:src.index("de: {")])
    used = set(re.findall(r'data-i18n="(\w+)"', html()))
    assert used - en == set(), f"untranslated: {sorted(used - en)}"


def test_no_third_party_code_or_fonts():
    src = html()
    for tag in re.findall(r"<(?:script|link)[^>]+(?:src|href)=\"([^\"]+)\"", src):
        assert not tag.startswith(("http://", "https://", "//")), f"external resource: {tag}"


def test_every_api_path_the_page_calls_exists():
    import api.server as server

    routes = {getattr(r, "path", "") for r in server.app.routes}
    called = set(re.findall(r'apiUrl\("(/[^"?]+)', js()))
    called = {"/v1/ticks/{tick}" if p == "/v1/ticks/" else p for p in called}
    assert called, "the page calls no API at all?"
    for path in called:
        assert path in routes, f"ticks.js calls {path}, which the server does not serve"


def test_the_stream_has_a_polling_fallback():
    """A browser without EventSource, or a proxy that buffers the stream, must
    still get ticks: the page then polls /v1/ticks/recent."""
    src = js()
    assert "window.EventSource" in src
    assert "/v1/ticks/recent" in src
    assert "forcePoll" in src           # the watchdog's switch after 12 s of silence


def test_the_page_links_the_current_script_hash():
    """Same rule as theme.css: the ?v= changes with the file, so no browser or
    proxy can keep running an old ticks.js against new markup — which is how a
    removed effect showed up again in a reader's browser on 2026-10-05."""
    import hashlib
    want = hashlib.sha256(SCRIPT.read_bytes()).hexdigest()[:8]
    got = re.findall(r'src="\./ticks\.js\?v=([0-9a-f]+)"', html())
    assert got == [want], f"ticks.html links ticks.js?v={got}, the file hashes to {want}"
