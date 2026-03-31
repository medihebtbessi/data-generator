"""
realism_effects.py
==================
Post-processing realism layer for BCT cheque generation.

Adds document-level visual imperfections to make generated cheques look
more like real scanned bank documents.

Main entry point
----------------
apply_realism_effects(img)   – call this BEFORE apply_scan_effects()

Also exports
------------
jitter_text_color()          – slight ink-color variation for text drawing
blurred_text_layer()         – draw text onto a sub-image then blur & composite
irregular_dot_fill()         – build a dot-fill string with uneven spacing
draw_irregular_line()        – draw a slightly wavy horizontal line
"""

from __future__ import annotations

import random
import math

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageDraw, ImageFont


# ── Document-level realism ────────────────────────────────────────────────

def apply_realism_effects(img: Image.Image) -> Image.Image:
    """
    Apply a stack of document-level realism effects to a cheque image.

    Effects applied (in order):
      1. Paper texture  – subtle grain + horizontal fiber streaks
      2. Ink stains     – faint brownish blotches (45 % chance)
      3. Lighting       – uneven illumination gradient
      4. Global warp    – small rotation (-0.8° to +0.8°), scale (0.985–1.015),
                          and translation jitter (±5 px)

    The function is deliberately conservative: each effect is weak enough that
    the cheque remains OCR-readable while looking like a scanned document.
    """
    arr = np.array(img).astype(np.float32)

    arr = _add_paper_texture(arr)
    arr = _add_stains(arr)
    arr = _add_lighting_gradient(arr)
    arr = _apply_global_distortions(arr)

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


# ── Per-element realism helpers (importable by renderers) ─────────────────

def jitter_text_color(base_color: tuple[int, int, int],
                      spread: int = 18) -> tuple[int, int, int]:
    """
    Return a slightly perturbed version of *base_color* to simulate
    real ink variation.  Each channel is nudged symmetrically by ±spread//2,
    allowing both slightly lighter and darker ink variations.
    """
    half = max(1, spread // 2)
    return tuple(
        max(0, min(255, c + random.randint(-half, half)))
        for c in base_color
    )


def blurred_text_layer(
    img: Image.Image,
    text: str,
    xy: tuple[int, int],
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    *,
    blur_radius: float | None = None,
) -> None:
    """
    Draw *text* onto *img* at *xy* with a slight Gaussian blur to simulate
    real printed/scanned text.

    Parameters
    ----------
    img         : destination PIL Image (modified in place)
    text        : string to draw
    xy          : (x, y) top-left position
    font        : PIL font
    fill        : RGB colour tuple
    blur_radius : Gaussian sigma (default: uniform in [0.3, 0.7])
    """
    if blur_radius is None:
        blur_radius = random.uniform(0.3, 0.7)

    # Bounding box of the text
    tmp_draw = ImageDraw.Draw(img)
    bb = tmp_draw.textbbox(xy, text, font=font)
    pad = max(4, int(blur_radius * 4))
    rx0 = max(0, bb[0] - pad)
    ry0 = max(0, bb[1] - pad)
    rx1 = min(img.width,  bb[2] + pad)
    ry1 = min(img.height, bb[3] + pad)

    # Render text on a transparent overlay
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    ov_draw.text(xy, text, fill=fill + (255,), font=font)

    # Crop, blur, composite
    region = overlay.crop((rx0, ry0, rx1, ry1))
    region = region.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    base_crop = img.crop((rx0, ry0, rx1, ry1)).convert("RGBA")
    merged = Image.alpha_composite(base_crop, region)
    img.paste(merged.convert(img.mode), (rx0, ry0))


def irregular_dot_fill(width_px: int, char: str = ".", base_spacing: int = 7) -> str:
    """
    Build a fill string of *char* with slightly uneven inter-character spacing
    (simulated via extra space characters) so the resulting text looks hand-typed
    rather than algorithmically generated.
    """
    result: list[str] = []
    x = 0
    while x < width_px:
        result.append(char)
        step = base_spacing + random.randint(-2, 3)
        x += max(3, step)
    return "".join(result)


def draw_irregular_line(
    draw: ImageDraw.ImageDraw,
    x0: int, x1: int, y: int,
    color: tuple[int, int, int] = (160, 160, 160),
    *,
    amplitude: float = 1.2,
    frequency: float | None = None,
    jitter_y: int = 1,
) -> None:
    """
    Draw a horizontal line from (x0, y) to (x1, y) with a subtle sine-wave
    undulation and per-pixel vertical noise to simulate imperfect printing.

    Parameters
    ----------
    amplitude  : peak sine displacement in pixels
    frequency  : sine cycles across the full line (default: random 2–5)
    jitter_y   : max random per-pixel Y noise in addition to the sine wave
    """
    if frequency is None:
        frequency = random.uniform(2.0, 5.0)

    length = x1 - x0
    if length <= 0:
        return

    pts: list[tuple[int, int]] = []
    for i, x in enumerate(range(x0, x1)):
        sine_y = amplitude * math.sin(2 * math.pi * frequency * i / length)
        noise_y = random.randint(-jitter_y, jitter_y)
        pts.append((x, int(y + sine_y + noise_y)))

    draw.line(pts, fill=color, width=1)


# ── Internal helpers ──────────────────────────────────────────────────────

def _add_paper_texture(arr: np.ndarray) -> np.ndarray:
    """Subtle grain + very faint horizontal fiber streaks."""
    h, w = arr.shape[:2]

    sigma = random.uniform(0.6, 2.0)
    grain = np.random.normal(0, sigma, (h, w, 3))
    arr = arr + grain

    # Horizontal fiber-like streaks (very sparse, very low opacity)
    if random.random() < 0.45:
        n_streaks = random.randint(2, 7)
        for _ in range(n_streaks):
            y_pos     = random.randint(0, h - 1)
            thickness = random.randint(1, 2)
            intensity = random.uniform(1.5, 4.0)
            # Slight darkening streak
            y1 = min(h, y_pos + thickness)
            arr[y_pos:y1, :, :] = np.clip(
                arr[y_pos:y1, :, :] - intensity, 0, 255
            )

    return arr


def _add_stains(arr: np.ndarray) -> np.ndarray:
    """Faint ink blotches / coffee-ring artefacts."""
    if random.random() > 0.45:
        return arr

    h, w = arr.shape[:2]
    n_stains = random.randint(1, 3)

    for _ in range(n_stains):
        cx = random.randint(w // 6, 5 * w // 6)
        cy = random.randint(h // 6, 5 * h // 6)
        r  = random.randint(10, 40)

        Y, X = np.ogrid[:h, :w]
        dist = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2).astype(np.float32)

        # Ring-concentrated mask (coffee-stain look)
        sigma_ring = max(r * 0.25, 4.0)
        mask = np.exp(-((dist - r) / sigma_ring) ** 2)
        mask *= random.uniform(0.04, 0.12)

        # Yellowish-brownish tint
        tint = np.array([
            random.uniform(-4, 2),
            random.uniform(-6, -1),
            random.uniform(-10, -4),
        ], dtype=np.float32)

        arr += mask[:, :, np.newaxis] * tint

    return arr


def _add_lighting_gradient(arr: np.ndarray) -> np.ndarray:
    """Simulate uneven scanner / lamp illumination."""
    h, w = arr.shape[:2]

    direction = random.choice(["vertical", "horizontal", "diagonal", "radial"])
    intensity = random.uniform(4.0, 14.0)

    if direction == "vertical":
        grad = np.linspace(0, intensity, h).reshape(h, 1, 1)
        if random.random() < 0.5:
            grad = grad[::-1]
        arr = arr + grad

    elif direction == "horizontal":
        grad = np.linspace(0, intensity, w).reshape(1, w, 1)
        if random.random() < 0.5:
            grad = grad[:, ::-1, :]
        arr = arr + grad

    elif direction == "diagonal":
        grad_h = np.linspace(0, intensity * 0.5, h).reshape(h, 1, 1)
        grad_w = np.linspace(0, intensity * 0.5, w).reshape(1, w, 1)
        arr = arr + grad_h + grad_w

    else:  # radial vignette
        Y, X = np.mgrid[:h, :w]
        cx_, cy_ = w // 2, h // 2
        dist = np.sqrt(
            ((X - cx_) / (w * 0.6)) ** 2 + ((Y - cy_) / (h * 0.6)) ** 2
        )
        vignette = np.clip(dist * intensity * 1.2, 0, intensity * 2)
        arr = arr - vignette[:, :, np.newaxis]

    return arr


def _apply_global_distortions(arr: np.ndarray) -> np.ndarray:
    """Small rotation, scale, and translation to simulate document placement."""
    h, w = arr.shape[:2]

    angle = random.uniform(-0.8, 0.8)
    scale = random.uniform(0.985, 1.015)
    tx    = random.randint(-5, 5)
    ty    = random.randint(-5, 5)

    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, scale)
    M[0, 2] += tx
    M[1, 2] += ty

    arr = cv2.warpAffine(
        arr, M, (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )

    return arr
