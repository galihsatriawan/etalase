"""Draw the demo photographs in samples/ (three flat-lay product shots and a thank-you card), deterministically.

They are synthetic stand-ins for a seller's real photos, so the demo store looks like a store without shipping anyone's
photographs in the repo: each product sits on a plain studio backdrop with a soft shadow, like the photos sellers take on
a table by a window. Run:  python scripts/make_sample_photos.py   (needs numpy and Pillow, both already installed for the tests).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parents[1] / "samples"
S = 720            # output size, square (a store card and a product page both show a square)
SS = 2             # supersampling for clean edges


def blur(a: np.ndarray, radius: float) -> np.ndarray:
    return np.asarray(Image.fromarray((np.clip(a, 0, 1) * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(radius)), dtype=float) / 255


def mask_of(draw_fn) -> np.ndarray:
    """Draw white shapes on black at 2x, then reduce, so the edge is anti-aliased."""
    img = Image.new("L", (S * SS, S * SS), 0)
    draw_fn(ImageDraw.Draw(img), SS)
    return np.asarray(img.resize((S, S), Image.LANCZOS), dtype=float) / 255


def backdrop(rng: np.random.Generator, tone: tuple[int, int, int]) -> np.ndarray:
    """A plain wall or table lit from the upper left, with a soft vignette."""
    y, x = np.mgrid[0:S, 0:S] / S
    light = 1.06 - 0.16 * np.hypot(x - 0.25, y - 0.15) - 0.07 * (x * 0.4 + y * 0.6)
    img = np.array(tone, dtype=float)[None, None, :] * light[..., None]
    return img + rng.normal(0, 1.1, (S, S, 1))


def folds(rng: np.random.Generator, count: int, amp: float, tilt: float, fine: bool = False) -> np.ndarray:
    y, x = np.mgrid[0:S, 0:S] / S
    height = np.zeros((S, S))
    for _ in range(count):
        angle = tilt + rng.normal(0, 0.3)
        freq = rng.uniform(5, 11) if fine else rng.uniform(1.6, 4.2)
        wobble = rng.uniform(0.5, 1.6) * np.sin(rng.uniform(1, 3) * y + rng.uniform(0, 6))
        height += rng.uniform(.4, 1) * amp * np.sin(freq * (x * np.cos(angle) + y * np.sin(angle)) * 2 * np.pi + wobble + rng.uniform(0, 6))
    return height


def cloth(rng, mask, color, *, count=6, amp=.8, tilt=.5, sheen=0., weave=0., crinkle=0., contrast=.26) -> np.ndarray:
    """Fabric texture for the pixels inside `mask`: folds lit from the upper left, plus an optional sheen, weave or crepe."""
    h = folds(rng, count, amp, tilt)
    gy, gx = np.gradient(h)
    slope = -(gx * -.7 + gy * -.6)
    shade = slope / (np.abs(slope).max() + 1e-9)
    img = np.array(color, dtype=float)[None, None, :] * (1 - contrast / 2 + contrast * shade)[..., None]
    if sheen:
        img += (np.clip(shade, 0, 1) ** 5)[..., None] * 255 * sheen
    if weave:
        rows = rng.normal(0, 1, (S, 1)); cols = rng.normal(0, 1, (1, S))
        img *= 1 + weave * (rows + cols + 0.7 * rng.normal(0, 1, (S, S)))[..., None]
    if crinkle:
        f = folds(rng, 26, .1, tilt + 1.1, fine=True)
        g2y, g2x = np.gradient(f)
        img *= (1 + crinkle * np.tanh(5 * (g2x - g2y)))[..., None]
    inner = blur(mask, 9)                                              # the rim of a cloth curls away from the light
    return img * (0.8 + 0.2 * inner)[..., None]


def composite(bg: np.ndarray, layers: list[tuple[np.ndarray, np.ndarray]], shadow: float = .3) -> Image.Image:
    """Lay each (mask, pixels) over the backdrop with a soft contact shadow underneath."""
    out = bg.copy()
    for mask, pix in layers:
        sh = blur(np.roll(np.roll(mask, 16, axis=0), 12, axis=1), 14) * shadow
        out *= (1 - sh)[..., None]
        tight = blur(np.roll(mask, 3, axis=0), 3) * shadow * .8
        out *= (1 - tight)[..., None]
        out = out * (1 - mask[..., None]) + pix * mask[..., None]
    out += np.random.default_rng(7).normal(0, 1.0, (S, S, 1))
    return Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGB")


def line_mask(points, width) -> np.ndarray:
    return mask_of(lambda d, k: d.line([(x * k, y * k) for x, y in points], fill=255, width=int(width * k), joint="curve"))


def poly(points, rounded: int = 0):
    def draw(d, k):
        d.polygon([(x * k, y * k) for x, y in points], fill=255)
    return mask_of(draw)


def rotate_pts(points, deg, cx=S / 2, cy=S / 2):
    t = np.radians(deg); c, s = np.cos(t), np.sin(t)
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in points]


def wavy(points, rng, amp=5.0, step=14):
    """Cloth never has a ruler-straight edge: cut each side into short pieces and nudge them along the side's normal."""
    out = []
    for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]):
        n = max(2, int(np.hypot(x1 - x0, y1 - y0) / step))
        nx, ny = (y1 - y0), -(x1 - x0); norm = np.hypot(nx, ny) or 1
        phase, freq = rng.uniform(0, 6), rng.uniform(.8, 2.2)
        for i in range(n):
            t = i / n
            off = amp * (np.sin(phase + freq * t * 6.28) + .5 * np.sin(2.7 * phase + 5.1 * t * 6.28))
            out.append((x0 + (x1 - x0) * t + nx / norm * off, y0 + (y1 - y0) * t + ny / norm * off))
    return out


def scarf(rng) -> Image.Image:
    """A silk pashmina folded in three and laid on a table, with the fringe showing at one end."""
    bg = backdrop(rng, (232, 234, 229))
    band = rotate_pts([(110, 250), (610, 250), (610, 470), (110, 470)], -14)
    top = rotate_pts([(150, 215), (575, 215), (575, 345), (150, 345)], -9)
    layers = []
    for pts, color, tilt in ((band, (112, 140, 108), .5), (top, (132, 160, 126), .8)):
        m = poly(wavy(pts, rng, 5.5))
        layers.append((m, cloth(rng, m, color, count=11, amp=1.6, tilt=tilt, sheen=.2, contrast=.48)))
    # the crease where the fold lies, and a short fringe at the open end of the lower layer
    crease = line_mask(rotate_pts([(150, 345), (575, 345)], -9), 3)
    layers[1] = (layers[1][0], layers[1][1] * (1 - 0.18 * blur(crease, 2))[..., None])
    fringe = mask_of(lambda d, k: [d.line([(x * k, y * k), ((x - 14 + i % 3 * 3) * k, (y + 26) * k)], fill=255, width=2 * k)
                                    for i, (x, y) in enumerate(rotate_pts([(608 - 0, 252 + j * 9) for j in range(24)], -14))])
    layers.insert(0, (fringe, np.ones((S, S, 3)) * np.array((104, 130, 100))))
    return composite(bg, layers)


def shirt(rng) -> Image.Image:
    """A linen shirt, flat, with a placket and buttons."""
    bg = backdrop(rng, (226, 220, 210))
    body = poly(wavy([(250, 175), (470, 175), (505, 610), (215, 610)], rng, 2.5, 18))
    sleeve_l = poly(wavy([(250, 175), (252, 262), (190, 410), (110, 372), (178, 232)], rng, 2.5, 18))
    sleeve_r = poly(wavy([(470, 175), (468, 262), (530, 410), (610, 372), (542, 232)], rng, 2.5, 18))
    collar = poly([(300, 150), (360, 215), (420, 150), (442, 176), (360, 262), (278, 176)])
    linen = (233, 226, 208)
    layers = []
    for m, tilt in ((sleeve_l, -.2), (sleeve_r, .4), (body, .1)):
        layers.append((m, cloth(rng, m, linen, count=6, amp=.6, tilt=tilt, weave=.02, contrast=.3)))
    c = layers[-1][1].copy()
    layers.append((collar, cloth(rng, collar, (240, 234, 218), count=3, amp=.4, tilt=.9, weave=.02)))
    # placket, hem stitching and buttons
    placket = line_mask([(360, 262), (360, 606)], 3)
    layers[2] = (layers[2][0], layers[2][1] * (1 - 0.12 * blur(placket, 1.6))[..., None])
    for y in range(300, 600, 60):
        b = mask_of(lambda d, k, y=y: d.ellipse(((352) * k, (y - 8) * k, 368 * k, (y + 8) * k), fill=255))
        layers.append((b, np.ones((S, S, 3)) * np.array((196, 186, 164)) * (0.9 + 0.1 * blur(b, 2))[..., None]))
    return composite(bg, layers, shadow=.26)


def hijab(rng) -> Image.Image:
    """A square voal scarf folded into a triangle, with the fold line showing."""
    bg = backdrop(rng, (236, 230, 224))
    tri = poly(wavy(rotate_pts([(90, 215), (630, 215), (360, 590)], -6), rng, 4.5))
    fold = poly(wavy(rotate_pts([(90, 215), (630, 215), (600, 268), (120, 268)], -6), rng, 3.5))
    taupe = (146, 118, 100)
    layers = [(tri, cloth(rng, tri, taupe, count=9, amp=1.4, tilt=.3, crinkle=.12, sheen=.05, contrast=.45)),
              (fold, cloth(rng, fold, (160, 130, 112), count=5, amp=1.0, tilt=.1, crinkle=.12, contrast=.45))]
    return composite(bg, layers, shadow=.3)


def card() -> Image.Image:
    img = Image.new("RGB", (S, S), (238, 232, 220))
    d = ImageDraw.Draw(img)
    d.rectangle((44, 44, S - 45, S - 45), outline=(150, 138, 116), width=2)
    try:
        big = ImageFont.truetype("DejaVuSans-Bold.ttf", 64); small = ImageFont.truetype("DejaVuSans.ttf", 24)
    except OSError:
        big = small = ImageFont.load_default()
    d.text((S / 2, S / 2 - 24), "Thank you", font=big, fill=(52, 48, 40), anchor="mm")
    d.text((S / 2, S / 2 + 48), "for a wonderful year", font=small, fill=(112, 102, 84), anchor="mm")
    return img


def main() -> None:
    OUT.mkdir(exist_ok=True)
    scarf(np.random.default_rng(11)).save(OUT / "pashmina-silk.png", optimize=True)
    shirt(np.random.default_rng(12)).save(OUT / "kemeja-linen.png", optimize=True)
    hijab(np.random.default_rng(13)).save(OUT / "voal-hijab.png", optimize=True)
    card().save(OUT / "thank-you.png", optimize=True)
    for f in sorted(OUT.glob("*.png")):
        print(f"{f.name}: {f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
