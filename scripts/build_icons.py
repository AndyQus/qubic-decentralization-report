#!/usr/bin/env python3
"""Render the PNG icons and the share card from the project's logo geometry.

WHY THIS EXISTS AT ALL
----------------------
SVG is enough for a browser tab, and dashboard/assets/icon.svg is what modern
browsers use. It is not enough for the two places that matter most here:

  * X, Telegram, Discord and Slack do not render SVG in a link preview. The
    og:image has to be a PNG or the card shows no picture.
  * Android and iOS home-screen icons come from the PNGs in the webmanifest.

WHY IT RENDERS RATHER THAN CONVERTS
-----------------------------------
The obvious route is cairosvg. It is not installed, and more to the point it is
not in the runtime image (Dockerfile installs requirements.txt only, and that is
three packages). Adding a C-linked SVG renderer to a service whose whole job is
to serve numbers would be a heavy dependency for four static files.

So this script draws the same geometry with Pillow instead, which is already
available in the dev environment and is needed nowhere at runtime. The outputs
are committed, so the image ships them without ever running this script. That
also matters because this deployment can only be reached by pushing an image to
Docker Hub — anything that has to be generated must be generated before build.

The trade-off: the geometry lives twice, once in the SVGs and once here. They
are kept in step by the constants below, which carry the same numbers the SVGs
use, and by verify(), which fails if an output is missing or the wrong size.

Usage:  python scripts/build_icons.py [--verify]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - a dev-machine problem, not a runtime one
    sys.exit(
        "Pillow is required to rebuild the icons: pip install Pillow\n"
        "(Runtime does not need it — the PNGs are committed.)"
    )

ASSETS = Path(__file__).resolve().parent.parent / "dashboard" / "assets"

# ── The palette, identical to theme.css ──────────────────────────────────────
VIOLET = (126, 111, 255)  # #7e6fff — --gradient start
CYAN = (78, 224, 252)     # #4ee0fc — --gradient end
INK = (13, 17, 23)        # #0d1117 — the dark ground the mark is cut out of
TEXT = (242, 245, 250)    # #f2f5fa
MUTED = (138, 160, 171)   # #8aa0ab
SUBTLE = (201, 212, 224)  # #c9d4e0

# ── The Qubic logomark, in its own coordinates ───────────────────────────────
# Straight from Qubic's wordmark: two bars in a 14.0035 x 24 box, the left one
# 18.0012 tall. Because the shape is nothing but two rectangles, it needs no
# path parser — these four numbers ARE the path.
MARK_W, MARK_H = 14.0035, 24.0
BAR_L = (0.0, 0.0, 6.01795, 18.0012)      # x, y, w, h
BAR_R = (8.02734, 0.0, 14.0035 - 8.02734, 24.0)


def _gradient_square(size: int, radius_ratio: float = 114 / 512) -> Image.Image:
    """A rounded square filled with the 135° violet→cyan gradient.

    Rendered at 4x and downsampled, because Pillow has no anti-aliased shape
    drawing: at 1x the rounded corners come out visibly stepped at 32 px, which
    is exactly the size the favicon is seen at.
    """
    ss = 4
    n = size * ss
    grad = Image.new("RGB", (n, n))
    px = grad.load()
    # 135°: the gradient runs along (x + y), so the diagonal is the axis.
    for y in range(n):
        for x in range(n):
            t = (x + y) / (2 * (n - 1))
            px[x, y] = (
                round(VIOLET[0] + (CYAN[0] - VIOLET[0]) * t),
                round(VIOLET[1] + (CYAN[1] - VIOLET[1]) * t),
                round(VIOLET[2] + (CYAN[2] - VIOLET[2]) * t),
            )

    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, n - 1, n - 1), radius=round(n * radius_ratio), fill=255
    )

    out = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out.resize((size, size), Image.LANCZOS)


def _punch_mark(tile: Image.Image, mark_height_ratio: float = 248 / 512) -> None:
    """Cut the two bars out of the tile so the dark ground shows through.

    Cut out rather than painted on: painted white, the mark nearly vanishes
    against the light cyan end of the gradient. As a dark void it holds contrast
    against both ends. Same reasoning as the comment in icon.svg.
    """
    size = tile.width
    scale = (size * mark_height_ratio) / MARK_H
    mw, mh = MARK_W * scale, MARK_H * scale
    ox, oy = (size - mw) / 2, (size - mh) / 2

    draw = ImageDraw.Draw(tile)
    for bx, by, bw, bh in (BAR_L, BAR_R):
        draw.rectangle(
            (
                round(ox + bx * scale),
                round(oy + by * scale),
                round(ox + (bx + bw) * scale) - 1,
                round(oy + (by + bh) * scale) - 1,
            ),
            fill=INK + (255,),
        )


def icon(size: int) -> Image.Image:
    tile = _gradient_square(size)
    _punch_mark(tile)
    return tile


def _font(size: int, bold: bool = False):
    """A real font if the machine has one, Pillow's bitmap default otherwise.

    The default font does not scale, so a machine without any of these would
    produce an unreadable card. That is why verify() checks the card's size and
    why the rendered PNG is committed and reviewed by eye, not trusted blindly.
    """
    candidates = (
        ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "Helvetica-Bold.ttf"]
        if bold
        else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Helvetica.ttf"]
    )
    roots = [
        Path("C:/Windows/Fonts"),
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts/Supplemental"),
    ]
    for name in candidates:
        for root in roots:
            p = root / name
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), size)
                except OSError:
                    continue
    return ImageFont.load_default()


def og_image() -> Image.Image:
    """The 1200x630 share card — what X shows when the URL is posted."""
    W, H = 1200, 630
    card = Image.new("RGB", (W, H), INK)

    # The two washes behind the content. Drawn as a coarse radial falloff on a
    # small image and scaled up: the blur is the point, so resolution is not.
    for cx, cy, rx, ry, col, peak in (
        (980, 120, 520, 380, VIOLET, 0.30),
        (180, 600, 460, 340, CYAN, 0.22),
    ):
        small = Image.new("RGBA", (W // 8, H // 8), (0, 0, 0, 0))
        sp = small.load()
        for y in range(small.height):
            for x in range(small.width):
                dx = (x * 8 - cx) / rx
                dy = (y * 8 - cy) / ry
                d = (dx * dx + dy * dy) ** 0.5
                if d < 1.0:
                    sp[x, y] = col + (round(255 * peak * (1 - d)),)
        card.paste(
            small.resize((W, H), Image.BICUBIC),
            (0, 0),
            small.resize((W, H), Image.BICUBIC),
        )

    # Logo, 132 px at the same 22% corner radius as the app icon.
    card.paste(icon(132), (96, 150), icon(132))

    d = ImageDraw.Draw(card)
    d.text((262, 168), "Qubic Report", font=_font(64, bold=True), fill=TEXT)
    d.text((264, 240), "report.qubic.tools", font=_font(27), fill=MUTED)

    # What the report measures, in navigation order, each dot in its page's
    # accent colour.
    bullets = [
        (110, 386, CYAN, "Dezentralisierung"),
        (420, 386, (245, 158, 11), "Verbrannte Supply"),
        (784, 386, VIOLET, "Kurs"),
        (110, 452, (34, 197, 94), "Mining Live"),
    ]
    bf = _font(29, bold=True)
    for cx, cy, col, label in bullets:
        d.ellipse((cx - 7, cy - 7, cx + 7, cy + 7), fill=col)
        d.text((cx + 22, cy - 16), label, font=bf, fill=SUBTLE)

    d.text(
        (96, 530),
        "Gemessen aus der Kette. Offene API, versiegelte Epochen, nichts interpoliert.",
        font=_font(25),
        fill=MUTED,
    )

    # The gradient rule along the bottom edge, mirroring the SVG.
    for x in range(W):
        t = x / (W - 1)
        d.line(
            [(x, 618), (x, 630)],
            fill=tuple(round(VIOLET[i] + (CYAN[i] - VIOLET[i]) * t) for i in range(3)),
        )
    return card


# Each output, with the size verify() expects to find.
OUTPUTS = {
    "icon-192.png": 192,
    "icon-512.png": 512,
    "apple-touch-icon.png": 180,
    "favicon-32.png": 32,
}


def build() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for name, size in OUTPUTS.items():
        img = icon(size)
        img.save(ASSETS / name)
        print(f"  {name:24} {size}x{size}")

    og = og_image()
    og.save(ASSETS / "og-image.png", optimize=True)
    print(f"  {'og-image.png':24} {og.width}x{og.height}")

    # favicon.ico: still the only thing some feed readers and older crawlers
    # look for, and it costs one line.
    icon(64).save(
        ASSETS / "favicon.ico",
        sizes=[(16, 16), (32, 32), (48, 48)],
    )
    print(f"  {'favicon.ico':24} 16/32/48")


def verify() -> int:
    """Fail if an expected output is missing or the wrong size.

    Exists so CI (or a human before a Docker push) can tell a stale asset from
    a fresh one without opening each file.
    """
    bad = []
    for name, size in {**OUTPUTS, "og-image.png": None, "favicon.ico": None}.items():
        p = ASSETS / name
        if not p.exists():
            bad.append(f"missing: {name}")
            continue
        with Image.open(p) as im:
            if name == "og-image.png" and (im.width, im.height) != (1200, 630):
                bad.append(f"{name}: {im.width}x{im.height}, expected 1200x630")
            elif size and (im.width, im.height) != (size, size):
                bad.append(f"{name}: {im.width}x{im.height}, expected {size}x{size}")
    for line in bad:
        print(f"  FAIL {line}")
    if bad:
        return 1
    print(f"  all {len(OUTPUTS) + 2} assets present and correctly sized")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true", help="check outputs, build nothing")
    args = ap.parse_args()
    if args.verify:
        sys.exit(verify())
    print(f"Rendering icons into {ASSETS}")
    build()
    sys.exit(verify())
