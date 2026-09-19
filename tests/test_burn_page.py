"""The burn page's own rules, pinned so a later edit cannot quietly break them.

This page exists because the official counter publishes one cumulative number
per epoch and the ask was a per-day view. Everything finer than an epoch is
counted by this project, so the page carries an unusual obligation: it must say
which figures are measurements and never present an invented one. These tests
cover the parts of that obligation that live in the page and the API, not in the
browser:

  * both language dictionaries are complete — a missing key must never reach a
    reader as a raw key name;
  * the page never ships a hard-coded burn figure;
  * an empty store answers 503 rather than zeros, so the page can say "building"
    instead of claiming nothing burned;
  * coverage stays null when it cannot be computed, rather than reading 100%.

No test here touches the network.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdr.store import Store  # noqa: E402

PAGE = ROOT / "dashboard" / "burn.html"


def page_text() -> str:
    return PAGE.read_text(encoding="utf-8")


# -- the page ---------------------------------------------------------------

def test_page_exists_and_is_linked_from_the_dashboard():
    assert PAGE.exists()
    index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "burn.html" in index


def test_both_language_dictionaries_cover_the_same_keys():
    """A key present in one language and missing in the other surfaces to a
    reader as a raw identifier. Both dictionaries ship complete or neither does."""
    src = page_text()
    block = src[src.index("const I18N"):src.index("let lang =")]
    en = block[block.index("en: {"):block.index("de: {")]
    de = block[block.index("de: {"):]
    # Only match a key at a value boundary (line start or after a comma), so
    # prose inside a translated string ("on purpose:", "Panel:") is not read as
    # a key — that false positive is worse than no test, because it trains you
    # to ignore the failure.
    key_re = re.compile(r"(?:^\s*|,\s*)([A-Za-z][A-Za-z0-9_]*)\s*:\s*\"", re.M)
    en_keys = set(key_re.findall(en)) - {"en", "de"}
    de_keys = set(key_re.findall(de)) - {"en", "de"}
    assert en_keys, "no English keys parsed — the dictionary shape changed"
    assert en_keys == de_keys, (
        f"only in EN: {sorted(en_keys - de_keys)}; only in DE: {sorted(de_keys - en_keys)}")


def test_every_i18n_attribute_has_a_dictionary_entry():
    src = page_text()
    used = set(re.findall(r'data-i18n="([^"]+)"', src))
    block = src[src.index("const I18N"):src.index("let lang =")]
    defined = set(re.findall(r"(?:^\s*|,\s*)([A-Za-z][A-Za-z0-9_]*)\s*:\s*\"",
                             block, re.M))
    assert used <= defined, f"no dictionary entry for: {sorted(used - defined)}"


def test_the_page_carries_no_hard_coded_burn_figure():
    """The measured total belongs to the store. A number baked into the markup
    would keep rendering long after it stopped being true — and this page's whole
    claim is that its figures are measurements."""
    src = page_text()
    # the real total is ~5.4e13; any long digit run in the markup is suspect
    body = src[src.index("<body"):]
    markup = re.sub(r"<script.*?</script>", "", body, flags=re.S)
    assert not re.search(r"\d[\d.,]{9,}", markup), "a long numeric literal is in the markup"


def test_the_page_states_that_finer_than_epoch_figures_are_self_measured():
    """The honesty note is the reason this page is allowed to show a daily curve
    at all; losing it would make the page indistinguishable from one that
    interpolated the official counter."""
    src = page_text()
    for token in ("noteBody", "buildingBody", "tipMeasured"):
        assert token in src
    assert "burnedQus" in src   # the page names the official field it is not using


def test_no_external_resources():
    """Same rule as the other pages: no CDN, no external font, no library.

    Checks what the browser would actually FETCH — src/href/url()/import — rather
    than any occurrence of "http", since the page legitimately prints a localhost
    URL inside a help string telling the reader how to serve it.
    """
    src = page_text()
    fetched = re.findall(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", src)
    fetched += re.findall(r"""url\(\s*['"]?([^)'"]+)""", src)
    fetched += re.findall(r"""@import\s+["']([^"']+)["']""", src)
    external = [u for u in fetched if u.startswith(("http://", "https://", "//"))]
    assert not external, f"page fetches external resources: {external}"


def test_the_furnace_animation_is_driven_only_by_measured_events():
    """The hero animation's whole licence to exist is that every spark is a
    counted burn event. A spawn rate that did not come from the measurement —
    a constant, a random, a time-based fallback — would make it decoration
    pretending to be data, on a page whose entire argument is the opposite.
    """
    src = page_text()
    furnace = src[src.index("function updateFurnace"):src.index("/* Odometer")]
    # the rate is derived from the measured event count and nothing else
    assert "sparksPerSec = 0;" in furnace
    assert "events_per_day" in furnace or "eventsPerDay" in furnace
    assert "m.events" in furnace
    # and with no events it stays at zero, so nothing is drawn
    assert "if (!events)" in furnace
    assert "furnaceIdle" in furnace


def test_reduced_motion_keeps_the_figures_and_drops_the_animation():
    """The page must be fully readable without animation."""
    src = page_text()
    assert "prefers-reduced-motion" in src
    assert "reduceMotion" in src
    assert "furnaceReduced" in src


def test_the_odometer_lands_on_the_exact_measured_total():
    """An eased count-up is presentation; ending on a rounded value would make
    the headline figure subtly wrong."""
    src = page_text()
    start = src.index("function runOdometer")
    odo = src[start:start + 900]
    assert "fmtInt(target)" in odo   # the final assignment is the exact value
    assert "requestAnimationFrame" in odo


# -- the API contract the page depends on -----------------------------------

def _client(store_path: Path) -> TestClient:
    """A client bound to a specific store.

    QDR_DB is read when qdr.store is first imported, so setting it and reloading
    only api.server changes nothing — the module-level DEFAULT_DB is already
    computed. Pointing the server's cached Store at this path directly is what
    actually binds it, and it does not depend on import order.
    """
    import api.server as server
    server._store = Store(store_path)
    server._burn_cache.update(at=0.0, data=None)
    return TestClient(server.app)


def test_an_empty_store_answers_503_rather_than_zeros():
    """Zeros are indistinguishable from "nothing burned", which is never true.
    The page shows its building notice on a 503."""
    d = Path(tempfile.mkdtemp(prefix="qdr_burnpage_"))
    Store(d / "qdr.db").close()
    c = _client(d / "qdr.db")
    assert c.get("/v1/burn/latest").status_code == 503
    assert c.get("/v1/burn/series").status_code == 503
    # /contracts is deliberately NOT a 503: contract burns appear only in the
    # end-epoch logs, so holding none is a real answer rather than a store that
    # is not ready. It returns an empty list, and the page hides the panel.
    empty = c.get("/v1/burn/contracts")
    assert empty.status_code == 200
    assert empty.json()["by_contract"] == []


def test_coverage_is_null_until_two_epoch_boundaries_are_on_record():
    """One anchored epoch cannot yield a delta. Reporting 100% there would be a
    claim of completeness nobody measured."""
    d = Path(tempfile.mkdtemp(prefix="qdr_burnpage_"))
    s = Store(d / "qdr.db")
    s.put_burn_total(231, 53_662_829_138_067, circulating=177_337_170_861_933)
    s.put_burn_bucket(1000, 1499, 231, "2026-09-19", burned=4_950_000_000,
                      burn_events=4950)
    s.close()
    c = _client(d / "qdr.db")
    body = c.get("/v1/burn/latest").json()
    assert body["coverage"] is None
    assert body["burned_total"] == 53_662_829_138_067
    # the measured part is reported separately from the official total, always
    assert body["measured"]["burned"] == 4_950_000_000


def test_series_labels_every_point_as_measured():
    d = Path(tempfile.mkdtemp(prefix="qdr_burnpage_"))
    s = Store(d / "qdr.db")
    s.put_burn_total(231, 53_662_829_138_067)
    s.put_burn_bucket(1000, 1499, 231, "2026-09-19", burned=4_950_000_000,
                      burn_events=4950)
    s.set_burn_scan_state(last_tick=1499, first_tick=1000)
    s.close()
    c = _client(d / "qdr.db")
    body = c.get("/v1/burn/series?by=day").json()
    assert body["series"] and all(p["measured"] for p in body["series"])
    # where our own measurement starts has to be visible to the reader
    assert body["first_measured_day"] == "2026-09-19"
    assert body["first_measured_tick"] == 1000
