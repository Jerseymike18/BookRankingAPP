"""Generate the PWA icon set from the existing Fable design tokens.

No new visual language: sage ground (--color-sage #4A7C59) with three parchment
"spines" (--color-ground #F8F5F0) — the shelf mark the tier spines already imply.
Drawn at 4x and downsampled for antialiasing (Pillow has no AA for rectangles).
"""

from PIL import Image, ImageDraw

SAGE = (0x4A, 0x7C, 0x59, 255)
GROUND = (0xF8, 0xF5, 0xF0, 255)
SS = 4  # supersample factor

OUT = "frontend/public/icons"


def draw_mark(size: int, *, maskable: bool) -> Image.Image:
    """One icon at `size` px.

    maskable=False → rounded-square badge with a small transparent margin, the
    shape a browser/OS shows as-is.
    maskable=True  → full-bleed sage so any OS mask (circle, squircle, rounded
    square) crops safely; the glyph stays inside the 80% safe zone.
    """
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if maskable:
        d.rectangle([0, 0, s, s], fill=SAGE)
        # Safe zone is the centred 80% circle; keep the glyph inside ~56% of the
        # canvas so it survives an aggressive circular crop.
        glyph_w, glyph_h = s * 0.44, s * 0.44
    else:
        pad = s * 0.045
        d.rounded_rectangle(
            [pad, pad, s - pad, s - pad], radius=s * 0.22, fill=SAGE
        )
        glyph_w, glyph_h = s * 0.52, s * 0.54

    # Spines on a shelf: uneven widths AND heights (a bar chart has uniform
    # widths and a common baseline — the varied widths plus the shelf rule under
    # them are what make this read as books).
    spines = [  # (relative width, relative height)
        (0.30, 0.86),
        (0.19, 1.00),
        (0.26, 0.72),
        (0.16, 0.92),
    ]
    gap = glyph_w * 0.055
    total_w = sum(w for w, _ in spines)
    usable = glyph_w - gap * (len(spines) - 1)

    shelf_h = glyph_h * 0.085
    x0 = (s - glyph_w) / 2
    shelf_bottom = (s + glyph_h) / 2
    baseline = shelf_bottom - shelf_h * 1.9  # spines sit on top of the shelf

    x = x0
    for w, h in spines:
        bar_w = usable * (w / total_w)
        top = baseline - (glyph_h - shelf_h * 1.9) * h
        d.rounded_rectangle(
            [x, top, x + bar_w, baseline],
            radius=bar_w * 0.22,
            fill=GROUND,
        )
        x += bar_w + gap

    d.rounded_rectangle(
        [x0, shelf_bottom - shelf_h, x0 + glyph_w, shelf_bottom],
        radius=shelf_h * 0.5,
        fill=GROUND,
    )

    return img.resize((size, size), Image.LANCZOS)


for size in (180, 192, 512):
    draw_mark(size, maskable=False).save(f"{OUT}/icon-{size}.png")
draw_mark(512, maskable=True).save(f"{OUT}/icon-512-maskable.png")
print("wrote icons")
