"""
body_renderer.py
================
Renders the body zone of a BCT Tunisian cheque.

Body contains:
  1. Legal instruction line (Payer contre ce chèque…)
  2. Amount in figures row  (dots/line + "ار")
  3. Amount in letters row  (dots/line + "ار")
  4. Separator
  5. "A l'ordre de" row with beneficiary name
  6. Amount handwritten box (optional overlay)

All positions, alignment and line styles are driven by LayoutConfig.
"""

from __future__ import annotations

import random

from PIL import ImageDraw, ImageFont

from layout_engine import Align, BoundingBox, LayoutConfig, LayoutEngine


# ── Font loader ────────────────────────────────────────────────────────────

def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        ("C:/Windows/Fonts/arial.ttf",   "C:/Windows/Fonts/arialbd.ttf"),
        ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ]
    for reg, bld in candidates:
        try:
            return ImageFont.truetype(bld if bold else reg, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _tw(draw, text, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def _th(draw, text, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]


# ── Line style helpers ─────────────────────────────────────────────────────

def _make_fill_line(style: str, width_px: int) -> str:
    """Return a fill character string of approximate width_px for given style."""
    if style == "dots":
        return "." * (width_px // 7)
    elif style == "line":
        return "_" * (width_px // 6)
    else:  # mixed: dots then underscores
        half = width_px // 2
        return "." * (half // 7) + "_" * (half // 6)


def _draw_actual_line(draw: ImageDraw.ImageDraw,
                      x0: int, x1: int, y: int,
                      color=(160, 160, 160), style="dots") -> None:
    """Draw an actual separator line (used for 'line' style)."""
    if style == "line":
        draw.line([(x0, y), (x1, y)], fill=color, width=1)


# ── BodyRenderer ──────────────────────────────────────────────────────────

class BodyRenderer:
    """
    Renders the body zone of a BCT cheque.

    Parameters
    ----------
    draw        : ImageDraw on the cheque Image
    bank        : bank config dict
    cfg         : LayoutConfig from LayoutEngine
    data        : cheque data dict (amount, amount_words, beneficiaire, …)
    show        : dict of booleans controlling which fields appear
    """

    def __init__(self,
                 draw: ImageDraw.ImageDraw,
                 bank: dict,
                 cfg: LayoutConfig,
                 engine: LayoutEngine,
                 data: dict,
                 show: dict | None = None) -> None:
        self.draw   = draw
        self.bank   = bank
        self.cfg    = cfg
        self.engine = engine
        self.data   = data
        self.show   = show or {k: True for k in
                               ["montant_line","montant_lettres","ordre"]}
        self.color  = bank["color"]

        sc = cfg.font_scale
        self.fn_lbl  = _get_font(max(8, int(13 * sc)))
        self.fn_val  = _get_font(max(8, int(14 * sc)))
        self.fn_sm   = _get_font(max(7, int(12 * sc)))
        self.fn_mic  = _get_font(max(6, int(9  * sc)))

    # ── Public entry point ────────────────────────────────────────────────

    def render(self) -> None:
        bb     = self.cfg.body_bb
        align  = self.cfg.body_align
        style  = self.cfg.body_line_style
        sp     = self.cfg.body_spacing      # extra vertical spacing

        y = bb.y0 + 8

        # ── Row 0: Legal text ──────────────────────────────────────────────
        y = self._row_legal(bb, y, align)
        y += 4 + sp

        # ── Row 1: Amount fill line ────────────────────────────────────────
        if self.show.get("montant_line", True):
            y = self._row_amount_fill(bb, y, align, style)
            y += 2 + sp

        # ── Row 2: Amount in letters ───────────────────────────────────────
        if self.show.get("montant_lettres", True):
            y = self._row_amount_letters(bb, y, align, style)
            y += 4 + sp

        # ── Separator ─────────────────────────────────────────────────────
        sep_y = min(y, bb.y1 - 60)
        self.draw.line([(bb.x0, sep_y), (bb.x1, sep_y)], fill=self.color, width=1)
        y = sep_y + 6

        # ── Row 3: A l'ordre de ───────────────────────────────────────────
        if self.show.get("ordre", True):
            self._row_ordre(bb, y, align, style)

    # ── Private row renderers ─────────────────────────────────────────────

    def _x_for(self, bb: BoundingBox, text_w: int, align: Align) -> int:
        jx = self.cfg.jitter_x
        x  = bb.x_for(text_w, align, pad=8)
        x += random.randint(-jx, jx)
        return bb.clamp_x(x, text_w)

    def _row_legal(self, bb: BoundingBox, y: int, align: Align) -> int:
        legal_fr = "Payer contre ce chèque non endossable le montant suivant"
        legal_ar = "يدفع مقابل هذا الشيك غير القابل للتظهير، المبلغ التالي:"

        # French always left-anchored for readability
        self.draw.text((bb.x0 + 8, y), legal_fr, fill=(30, 30, 30), font=self.fn_lbl)

        # Arabic always right-anchored
        aw = _tw(self.draw, legal_ar, self.fn_sm)
        self.draw.text((bb.x1 - aw - 8, y), legal_ar, fill=(30, 30, 30), font=self.fn_sm)

        return y + _th(self.draw, legal_fr, self.fn_lbl) + 2

    def _row_amount_fill(self, bb: BoundingBox, y: int,
                         align: Align, style: str) -> int:
        """Row of fill characters for the handwritten amount in figures."""
        right_label = "ار"
        rl_w = _tw(self.draw, right_label, self.fn_lbl)

        fill_w = bb.w - rl_w - 20
        fill   = _make_fill_line(style, fill_w)
        fill_w_actual = _tw(self.draw, fill, self.fn_sm)

        x_fill = self._x_for(BoundingBox(bb.x0, y, bb.x1 - rl_w - 12, y + 20),
                              fill_w_actual, align)
        self.draw.text((x_fill, y), fill, fill=(160, 160, 160), font=self.fn_sm)
        self.draw.text((bb.x1 - rl_w - 6, y), right_label,
                       fill=(60, 60, 60), font=self.fn_lbl)

        if style in ("line", "mixed"):
            _draw_actual_line(self.draw, bb.x0 + 8, bb.x1 - rl_w - 14,
                              y + _th(self.draw, fill, self.fn_sm) + 2,
                              style="line")

        return y + _th(self.draw, fill, self.fn_sm) + 2

    def _row_amount_letters(self, bb: BoundingBox, y: int,
                            align: Align, style: str) -> int:
        """Row showing amount in words (ALWAYS blank writable line)."""

        right_label = "ار"
        rl_w = _tw(self.draw, right_label, self.fn_lbl)

        # Always draw blank fill line (no words)
        fill   = _make_fill_line(style, bb.w - rl_w - 20)
        fill_w = _tw(self.draw, fill, self.fn_sm)

        x = self._x_for(
            BoundingBox(bb.x0, y, bb.x1 - rl_w - 12, y + 20),
            fill_w,
            align
        )

        self.draw.text(
            (x, y),
            fill,
            fill=(160, 160, 160),
            font=self.fn_sm
        )

        # Right-side Arabic label
        self.draw.text(
            (bb.x1 - rl_w - 6, y),
            right_label,
            fill=(60, 60, 60),
            font=self.fn_lbl
        )

        return y + _th(self.draw, right_label, self.fn_lbl) + 2

        

    def _row_ordre(self, bb: BoundingBox, y: int,
                   align: Align, style: str) -> None:
        """Render the "A l'ordre de" row."""
        label    = "A l'ordre de:"
        label_ar = "لأمر السيد/ة:"
        lw       = _tw(self.draw, label, self.fn_lbl)

        self.draw.text((bb.x0 + 8, y), label, fill=(40, 40, 40), font=self.fn_lbl)

        # Arabic label right-aligned
        aw = _tw(self.draw, label_ar, self.fn_sm)
        self.draw.text((bb.x1 - aw - 8, y), label_ar,
                       fill=(60, 60, 60), font=self.fn_sm)

        # Beneficiary name or fill
        benef = self.data.get("beneficiaire", "")
        bx    = bb.x0 + lw + 14
        bx   += random.randint(-self.cfg.jitter_x, self.cfg.jitter_x)

        if benef:
            self.draw.text((bx, y), benef[:40], fill=(15, 15, 15), font=self.fn_val)
        else:
            fill_w = bb.x1 - aw - bx - 20
            fill   = _make_fill_line(style, max(fill_w, 40))
            self.draw.text((bx, y), fill, fill=(180, 180, 180), font=self.fn_sm)