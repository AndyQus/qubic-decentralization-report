"""The price page's own rules, pinned so a later edit cannot quietly break them.

This page makes a claim no exchange chart makes — that every figure on it is a
reading this project took, with the time it was taken — and a second one that is
easy to lose in a refactor: the series is drawn as STEPS, because the price held
each value across its interval and a diagonal between two readings would assert
movement nobody measured.

Covered here:

  * both language dictionaries complete — a missing key must never surface to a
    reader as a raw identifier;
  * no hard-coded price, market cap or supply figure in the markup;
  * the page is reachable from the others, and links back to them;
  * the licensing boundary is stated on the page itself, not only in the code —
    this page exists in the shape it does because exchange data was refused;
  * the honesty vocabulary the chart depends on (steps, gaps, coverage) is
    actually present.

Rendering is verified separately by scripts/verify_price_page.py, which loads the
page in a browser; no test here touches the network or a browser.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DASH = ROOT / "dashboard"
PAGE = DASH / "price.html"


def page_text() -> str:
    return PAGE.read_text(encoding="utf-8")


# -- the page exists and is wired in ----------------------------------------

def test_page_exists_and_is_linked_from_every_sibling():
    """An unlinked page is one nobody finds. The three dashboard pages share a
    nav; all of them must carry this one."""
    assert PAGE.exists()
    for name in ("index.html", "burn.html", "mining.html"):
        src = (DASH / name).read_text(encoding="utf-8")
        assert "price.html" in src, f"{name} does not link the price page"


def test_page_links_back_to_the_others():
    src = page_text()
    for href in ("./", "./burn.html", "./mining.html", "./how-it-works.html"):
        assert f'href="{href}"' in src, f"nav is missing {href}"


def test_page_uses_the_shared_stylesheet():
    """Three pages, one design system. A page-local copy of the tokens would
    drift the moment theme.css changed."""
    assert re.search(r'<link rel="stylesheet" href="\./theme\.css\?v=[0-9a-f]+">',
                     page_text()), "theme.css is not linked with a cache-busting version"


# -- both languages complete -------------------------------------------------

def test_both_language_dictionaries_cover_the_same_keys():
    """A key present in one language and missing in the other surfaces to a
    reader as a raw identifier. Both dictionaries ship complete or neither does."""
    src = page_text()
    block = src[src.index("const I18N"):src.index("let lang =")]
    en = block[block.index("en: {"):block.index("de: {")]
    de = block[block.index("de: {"):]
    # Only match a key at a value boundary (line start or after a comma), so
    # prose inside a translated string is not read as a key.
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


def test_placeholders_match_between_languages():
    """A {span} that exists in English and not in German renders as a literal
    brace to a German reader."""
    src = page_text()
    block = src[src.index("const I18N"):src.index("let lang =")]
    en = block[block.index("en: {"):block.index("de: {")]
    de = block[block.index("de: {"):]
    pat = re.compile(r"(?:^\s*|,\s*)([A-Za-z][A-Za-z0-9_]*)\s*:\s*\"([^\"]*)\"", re.M)
    en_ph = {k: set(re.findall(r"\{(\w+)\}", v)) for k, v in pat.findall(en)}
    de_ph = {k: set(re.findall(r"\{(\w+)\}", v)) for k, v in pat.findall(de)}
    for k, ph in en_ph.items():
        if k in de_ph:
            assert ph == de_ph[k], f"{k}: EN has {sorted(ph)}, DE has {sorted(de_ph[k])}"


# -- nothing is baked in -----------------------------------------------------

def test_the_page_carries_no_hard_coded_market_figure():
    """A price or market cap baked into the markup would keep rendering long
    after it stopped being true — and this page's whole claim is that its
    figures are readings."""
    src = page_text()
    body = src[src.index("<body"):]
    markup = re.sub(r"<script.*?</script>", "", body, flags=re.S)
    markup = re.sub(r"<!--.*?-->", "", markup, flags=re.S)
    assert not re.search(r"\d[\d.,]{6,}", markup), \
        "a long numeric literal is in the markup"


def test_every_figure_placeholder_starts_empty():
    """Every slot the script fills renders as an em-dash until it has data, so a
    slow or failed load never shows a stale or invented number."""
    src = page_text()
    body = src[src.index("<body"):]
    for el_id in ("hero-price", "f-cap", "f-change", "f-supply", "f-epoch", "f-watch"):
        m = re.search(rf'id="{el_id}"[^>]*>([^<]*)<', body)
        assert m, f"#{el_id} not found in the markup"
        assert m.group(1).strip() in ("–", "-", ""), \
            f"#{el_id} ships with a value: {m.group(1)!r}"


# -- the honesty vocabulary --------------------------------------------------

def test_the_page_states_that_the_series_is_steps():
    """The step shape is the page's central claim about the data. Losing the
    sentence would leave a chart that looks like any other line chart."""
    src = page_text()
    block = src[src.index("const I18N"):src.index("let lang =")]
    assert "step" in block.lower(), "the EN copy no longer explains the steps"
    assert "stufen" in block.lower(), "the DE copy no longer explains the steps"


def test_the_chart_builder_emits_no_curve_commands():
    """A C/S/Q/T/A path command would smooth the series into a curve, asserting
    movement between two readings that nobody measured. The builder must only
    ever emit M, L and Z."""
    src = page_text()
    fn = src[src.index("function drawChart"):src.index("function showStepTip")]
    # Path data is assembled from string literals; look at those rather than at
    # prose, which legitimately contains those letters.
    for lit in re.findall(r'`([^`]*)`', fn):
        if "${" in lit and any(c in lit for c in "MLZ"):
            assert not re.search(r"\b[CSQTA]\s", lit), \
                f"path literal contains a curve command: {lit[:60]!r}"


def test_the_page_explains_gaps_and_coverage():
    """With only changes stored, a flat line means "steady" only if somebody was
    looking. The page has to carry that distinction in words, not just in a
    number."""
    src = page_text()
    block = src[src.index("const I18N"):src.index("let lang =")]
    assert "legGap" in block, "the gap legend entry is gone"
    assert "watchSub" in block, "the coverage sub-label is gone"
    assert "heldFor" in block, "the held-for line is gone"


def test_the_payout_study_disclaims_causation():
    """The study invites a causal reading by its very shape. The copy has to
    refuse that reading explicitly, in both languages."""
    src = page_text()
    block = src[src.index("const I18N"):src.index("let lang =")]
    en = block[block.index("en: {"):block.index("de: {")]
    de = block[block.index("de: {"):]
    assert "does not claim" in en.lower(), "EN study copy dropped the disclaimer"
    assert "behauptet nicht" in de.lower(), "DE study copy dropped the disclaimer"


# -- the licensing boundary --------------------------------------------------

def test_the_page_says_why_there_is_no_volume():
    """This page has no trading volume because using an exchange's data would
    breach their terms. That is a deliberate absence, and a reader comparing it
    against any market site deserves the reason on the page itself."""
    src = page_text()
    block = src[src.index("const I18N"):src.index("let lang =")]
    assert "aboutNoVolume" in block, "the no-volume explanation is gone"
    en = block[block.index("en: {"):block.index("de: {")]
    assert "mexc" in en.lower(), "the EN copy no longer names why volume is absent"


def test_the_page_names_its_source():
    """These figures get republished. Provenance travels with them."""
    src = page_text()
    block = src[src.index("const I18N"):src.index("let lang =")]
    assert "latest-stats" in block, "the source endpoint is no longer named"


def test_no_exchange_host_is_contacted_from_the_page():
    """The page must read only this project's own API. A URL pointing at an
    exchange would put the licensing problem straight into the reader's browser.

    Prose may name MEXC — explaining why volume is absent is the point — so this
    looks for hosts in URL position, not for the word.
    """
    src = page_text().lower()
    for host in ("mexc.com", "gateio.ws", "gate.io", "coingecko.com", "binance.com"):
        for prefix in ("//", "http://", "https://"):
            assert prefix + host not in src, f"the page contacts {host}"


def test_fetches_go_through_the_projects_own_api():
    src = page_text()
    urls = re.findall(r'getJSON\(\s*[`"\']([^`"\']+)', src)
    assert urls, "no API calls found — the page fetches nothing"
    for u in urls:
        assert u.startswith("/v1/"), f"unexpected fetch target: {u}"


def test_every_content_card_is_translucent_over_the_moving_background():
    """`.qs-card` alone sets only a background *image* — a gradient fading to
    transparent — and no background colour, so on a page running a canvas behind
    the content the animation shows straight through the card. Small type over
    moving particles needs the blur that `--translucent` brings.

    The hero is the documented exception: theme.css strips its border and tint
    on purpose (`.qs-card.hero`), and then cancels `--translucent` again for it,
    because a lighter panel there would restore exactly the box the hero is
    meant to shed. Its type is large enough to carry itself over the texture.
    """
    src = page_text()
    assert 'class="qs-canvas"' in src, (
        "precondition: this test is about pages with a background canvas")
    cards = re.findall(r'<section class="([^"]*qs-card[^"]*)"', src)
    assert cards, "no cards found — the markup shape changed"
    content = [c for c in cards if "hero" not in c]
    assert content, "no content cards found besides the hero"
    opaque = [c for c in content if "qs-card--translucent" not in c]
    assert not opaque, (
        f"content card(s) without --translucent over a moving canvas: {opaque}")
