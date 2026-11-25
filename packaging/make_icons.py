"""Draws Pathfinder's logo in every size the app, the installer and the docs use.

    python packaging/make_icons.py

The mark is drawn from Kanso (docs/design): warm paper (Washi canvas #FDFBF2) with its hairline
ring, two ink L-brackets (Kanso's mark for the thing you chose) and one jewel of Structural paint
(#1C4286) between them: the job you were looking for, found. Small sizes are snapped to whole
pixels so they stay crisp in the taskbar.

Writes jobwatcher/assets/icon.png (512), icon.ico (16 to 256; the small sizes as plain bitmaps,
which Windows' .NET Icon class needs) and logo.svg (for the window's favicon, the app and the README).
"""
from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "jobwatcher" / "assets"
PAPER, HAIR, INK, PAINT = "#FDFBF2", "#E1DFD7", "#111314", "#1C4286"
SUPER = 8  # drawn this many times larger, then averaged down: smooth curves, crisp straight edges

# size: (corner radius, inset, arm, stroke, dot radius), in pixels at that size
SNAPPED = {16: (3.5, 3, 5, 2, 2), 24: (5, 5, 7, 2, 2.5), 32: (7, 7, 9, 2, 3)}


def geometry(size: int):
    if size in SNAPPED:
        return SNAPPED[size]
    s = size / 256
    return (56 * s, round(56 * s), round(78 * s), max(2, round(18 * s)), 24 * s)


def draw(size: int) -> Image.Image:
    radius, inset, arm, stroke, dot = (v * SUPER for v in geometry(size))
    big = size * SUPER
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    ring = max(SUPER, round(big / 256))  # one hairline pixel at every size
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=radius, fill=HAIR)
    d.rounded_rectangle((ring, ring, big - 1 - ring, big - 1 - ring), radius=max(0, radius - ring), fill=PAPER)
    far = big - inset
    # top-left bracket, then bottom-right: two arms each, butt ends, mitred corner
    d.rectangle((inset, inset, inset + arm - 1, inset + stroke - 1), fill=INK)
    d.rectangle((inset, inset, inset + stroke - 1, inset + arm - 1), fill=INK)
    d.rectangle((far - arm, far - stroke, far - 1, far - 1), fill=INK)
    d.rectangle((far - stroke, far - arm, far - 1, far - 1), fill=INK)
    c = big / 2
    d.ellipse((c - dot, c - dot, c + dot, c + dot), fill=PAINT)
    return im.resize((size, size), Image.Resampling.BOX)


def svg() -> str:
    radius, inset, arm, stroke, dot = geometry(256)
    far, h = 256 - inset, stroke / 2  # strokes are centred on the path, so the path runs half a stroke in
    line = f'fill="none" stroke="{INK}" stroke-width="{stroke}" stroke-linecap="butt" stroke-linejoin="miter"'
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-label="Pathfinder">'
        f'<rect x="0.5" y="0.5" width="255" height="255" rx="{radius:g}" fill="{PAPER}" stroke="{HAIR}"/>'
        f'<path d="M{inset + h:g} {inset + arm}V{inset + h:g}H{inset + arm}" {line}/>'
        f'<path d="M{far - arm} {far - h:g}H{far - h:g}V{far - arm}" {line}/>'
        f'<circle cx="128" cy="128" r="{dot:g}" fill="{PAINT}"/></svg>\n')


def ico(frames: list[Image.Image]) -> bytes:
    """An .ico with each size drawn for that size: bitmaps up to 128, PNG at 256 (as Windows expects)."""
    blobs = []
    for im in frames:
        if im.width >= 256:
            out = io.BytesIO()
            im.save(out, "PNG")
            blobs.append(out.getvalue())
        else:
            out = io.BytesIO()
            im.save(out, "ICO", sizes=[im.size], bitmap_format="bmp")
            raw = out.getvalue()  # a one-frame .ico: its directory entry says where the bitmap is
            length, offset = struct.unpack("<II", raw[14:22])
            blobs.append(raw[offset:offset + length])
    head = struct.pack("<HHH", 0, 1, len(frames))
    entries, offset = b"", 6 + 16 * len(frames)
    for im, blob in zip(frames, blobs):
        w = im.width if im.width < 256 else 0
        entries += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    return head + entries + b"".join(blobs)


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    draw(512).save(ASSETS / "icon.png", optimize=True)
    (ASSETS / "icon.ico").write_bytes(ico([draw(s) for s in (16, 24, 32, 48, 64, 128, 256)]))
    (ASSETS / "logo.svg").write_text(svg(), encoding="utf-8")
    print("Wrote icon.png, icon.ico and logo.svg in", ASSETS)


if __name__ == "__main__":
    main()
