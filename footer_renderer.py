"""
footer_renderer.py
==================
Renders the footer zone of a BCT Tunisian cheque.

Footer is split into 3 sub-blocks driven by LayoutConfig ratios:

  LEFT   → Agency info (Payable à, Adresse, Tél)
  CENTER → Client info (RIB, Titulaire, Dates)
  RIGHT  → Signature zone

Below the 3 sub-blocks:
  • Expiration date (prominent)
  • Cheque reference line (MICR-style)
  • Security footer bar
"""

from __future__ import annotations

import random
import string
import math
import numpy as np
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from layout_engine import Align, VAlign, BoundingBox, LayoutConfig, LayoutEngine
from realism_effects import (
    jitter_text_color, blurred_text_layer,
    irregular_dot_fill, draw_irregular_line,
)


# ── Font loader ────────────────────────────────────────────────────────────

def _get_font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    if mono:
        for p in [
            "C:/Windows/Fonts/cour.ttf",
            "C:/Windows/Fonts/consola.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        ]:
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass

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


def _fmt_rib(rib: str) -> str:
    r = rib.replace(" ", "")
    if len(r) < 18:
        return r
    return f"{r[:2]} {r[2:5]} {r[5:18]} {r[18:]}"


# ── Signature drawing ─────────────────────────────────────────────────────

def _draw_signature(draw: ImageDraw.ImageDraw,
                    bank: dict,
                    x: int, y: int,
                    w: int, h: int) -> None:
    """
    Realistic cursive signature simulation.

    Improvements over the previous version:
    - Multiple overlapping strokes with independent path variation
    - Variable per-segment line thickness
    - Random discontinuities (pen lifts)
    - Slight angle tilt per stroke
    - Initial underdot / paraph marks (25 % chance)
    """
    if random.random() > 0.82:
        return

    color    = bank["color"]
    n_passes = random.randint(1, 3)   # up to 3 overlapping strokes

    for pass_idx in range(n_passes):
        # Each stroke starts at a slightly different horizontal offset
        sx   = x + random.randint(4, max(5, w // 5))
        base_y = y + random.randint(int(h * 0.25), int(h * 0.65))
        n_pts  = random.randint(12, 22)
        step_w = max(1, (w - (sx - x) - random.randint(4, 14)) // max(1, n_pts))

        # Build the stroke with random discontinuities
        segment: list[tuple[int, int]] = []
        freq  = random.uniform(0.30, 0.90)
        phase = random.uniform(0, math.pi * 2)
        amp   = random.randint(8, min(18, h // 2))

        for i in range(n_pts):
            px = sx + i * step_w
            py = base_y + int(amp * math.sin(i * freq + phase)
                              + random.randint(-4, 4))
            py = max(y + 2, min(y + h - 2, py))

            # Random pen-lift: end current segment and start a new one
            if i > 0 and random.random() < 0.12:
                if len(segment) >= 2:
                    draw.line(segment, fill=color,
                              width=random.randint(1, 3))
                segment = []

            segment.append((px, py))

        if len(segment) >= 2:
            draw.line(segment, fill=color,
                      width=random.randint(1, 3))

    # Paraph / underline flourish (50 % chance)
    if random.random() < 0.50:
        fx0 = x + random.randint(4, w // 4)
        fx1 = x + w - random.randint(4, w // 4)
        fy  = y + h - random.randint(6, 14)
        # Slight arc underline
        arc_pts = []
        for xi in range(fx0, fx1, 2):
            t   = (xi - fx0) / max(1, fx1 - fx0)
            arc_y = fy + int(4 * math.sin(math.pi * t))
            arc_pts.append((xi, arc_y))
        if len(arc_pts) >= 2:
            draw.line(arc_pts, fill=color, width=random.randint(1, 2))


# ── FooterRenderer ────────────────────────────────────────────────────────

class FooterRenderer:
    """
    Renders the footer zone of a BCT cheque.

    Parameters
    ----------
    img    : PIL Image
    draw   : ImageDraw
    bank   : bank config dict
    cfg    : LayoutConfig
    engine : LayoutEngine (for footer box computation)
    data   : cheque data dict
    show   : field visibility booleans
    """

    def __init__(self,
                 img:    Image.Image,
                 draw:   ImageDraw.ImageDraw,
                 bank:   dict,
                 cfg:    LayoutConfig,
                 engine: LayoutEngine,
                 data:   dict,
                 show:   dict | None = None) -> None:
        self.img    = img
        self.draw   = draw
        self.bank   = bank
        self.cfg    = cfg
        self.engine = engine
        self.data   = data
        self.show   = show or {k: True for k in
                               ["payable","rib","date","expiry","micr","signature"]}
        self.color  = bank["color"]

        sc = cfg.font_scale
        self.fn_lbl  = _get_font(max(8, int(13 * sc)))
        self.fn_val  = _get_font(max(8, int(14 * sc)))
        self.fn_sm   = _get_font(max(7, int(12 * sc)))
        self.fn_mic  = _get_font(max(6, int(9  * sc)))
        self.fn_num  = _get_font(max(8, int(16 * sc)), bold=True)
        self.fn_micr = _get_font(max(8, int(17 * sc)), mono=True)

    # ── Public entry point ────────────────────────────────────────────────

    def render(self) -> None:
        left_bb, center_bb, right_bb = self.engine.footer_boxes(self.cfg)
        fb = self.cfg.footer_bb

        # Top separator (already drawn by body, but reinforce)
        self.draw.line(
            [(fb.x0, fb.y0), (fb.x1, fb.y0)],
            fill=self.color, width=1,
        )

        # Vertical separators between columns
        self._vline(left_bb.x1, fb.y0, fb.y1 - 30)
        self._vline(center_bb.x1, fb.y0, fb.y1 - 30)

        # Render each sub-block
        self._render_left(left_bb)
        self._render_center(center_bb)
        self._render_right(right_bb)

        # Shared bottom row: expiry date + reference
        self._render_bottom_row(fb)

        # Security footer bar
        self._render_security_bar(fb)

    # ── Left: Agency info ─────────────────────────────────────────────────

    def _render_left(self, bb: BoundingBox) -> None:
        if not self.show.get("payable", True):
            return

        y   = bb.y0 + 8
        pad = bb.x0 + 8

        rows = [
            ("Payable à:",   "يدفع بـ:"),
            ("Adresse:",     "العنوان:"),
            ("Rue:",         "الشارع:"),
            ("",             ""),
            ("Tél:",         "الهاتف:"),
        ]
        line_h = _th(self.draw, "Payable à:", self.fn_lbl) + 4

        for fr, ar in rows:
            if y + line_h > bb.y1 - 10:
                break
            # Slight per-row baseline jitter
            y_row = y + random.randint(-1, 1)
            if fr:
                self.draw.text((pad, y_row), fr,
                               fill=jitter_text_color((40, 40, 40)), font=self.fn_lbl)
                aw = _tw(self.draw, ar, self.fn_mic)
                self.draw.text((bb.x1 - aw - 6, y_row), ar,
                               fill=jitter_text_color((80, 80, 80)), font=self.fn_mic)
                # Irregular fill dots
                lw      = _tw(self.draw, fr, self.fn_lbl)
                fill_x0 = pad + lw + 4
                fill_x1 = bb.x1 - aw - 10
                if fill_x1 > fill_x0 + 10:
                    dots = irregular_dot_fill(fill_x1 - fill_x0, ".", 7)
                    self.draw.text((fill_x0, y_row), dots,
                                   fill=jitter_text_color((180, 180, 180), spread=10),
                                   font=self.fn_sm)
            else:
                # Empty continuation line
                dots = irregular_dot_fill(bb.x1 - pad - 20, ".", 7)
                self.draw.text((pad, y_row), dots,
                               fill=jitter_text_color((190, 190, 190), spread=10),
                               font=self.fn_sm)
            y += line_h

    # ── Center: Client info (RIB, Titulaire, Dates) ───────────────────────

    def _render_center(self, bb: BoundingBox) -> None:
        y   = bb.y0 + 8
        pad = bb.x0 + 8

        # Titulaire header
        tit_lbl    = "Titulaire du compte"
        tit_ar_lbl = "صاحب الحساب"
        self.draw.text((pad, y), tit_lbl, fill=(40, 40, 40), font=self.fn_lbl)
        tw_ = _tw(self.draw, tit_lbl, self.fn_lbl)
        self.draw.text((pad + tw_ + 10, y), tit_ar_lbl,
                       fill=(50, 50, 50), font=self.fn_sm)
        y += _th(self.draw, tit_lbl, self.fn_lbl) + 6

        # RIB
        if self.show.get("rib", True):
            rib_str = _fmt_rib(self.data.get("rib", ""))
            self.draw.text((pad, y), rib_str, fill=(10, 10, 10), font=self.fn_micr)
            y += _th(self.draw, rib_str, self.fn_micr) + 4

            titulaire = self.data.get("titulaire", "M. " + "X" * 12)
            self.draw.text((pad, y), titulaire, fill=(10, 10, 10), font=self.fn_val)
            y += _th(self.draw, titulaire, self.fn_val) + 6
        else:
            self.draw.text((pad, y), "RIB: " + "." * 22,
                           fill=(160, 160, 160), font=self.fn_sm)
            y += 26

        # Date émission
        if self.show.get("date", True):
            date_lbl = "Date d'émission   |   تاريخ الإصدار"
            self.draw.text((pad, y), date_lbl, fill=(60, 60, 60), font=self.fn_mic)
            y += 14
            self.draw.text((pad, y), "....../....../......",
                           fill=(150, 150, 150), font=self.fn_sm)
            y += 18

        # Lieu d'émission
        lieu_lbl = "Lieu d'émission   |   مكان الإصدار"
        self.draw.text((pad, y), lieu_lbl, fill=(60, 60, 60), font=self.fn_mic)
        y += 14
        self.draw.text((pad, y), "." * 22, fill=(150, 150, 150), font=self.fn_sm)

    # ── Right: Signature zone ─────────────────────────────────────────────

    def _render_right(self, bb: BoundingBox) -> None:
        if not self.show.get("signature", True):
            return

        # Draw signature rectangle
        pad = 10
        sx0 = bb.x0 + pad
        sx1 = bb.x1 - pad

        valign = self.cfg.sig_valign
        if valign == VAlign.TOP:
            sy0 = bb.y0 + 8
            sy1 = sy0 + int(bb.h * 0.75)
        else:  # MIDDLE
            h = int(bb.h * 0.80)
            sy0 = bb.y0 + (bb.h - h) // 2
            sy1 = sy0 + h

        self.draw.rectangle([(sx0, sy0), (sx1, sy1)],
                            outline=self.color, width=2)

        # Label
        sig_lbl = "Signature: الإمضاء"
        lw = _tw(self.draw, sig_lbl, self.fn_sm)
        lx = sx0 + (sx1 - sx0 - lw) // 2
        self.draw.text((lx, sy0 + 6), sig_lbl, fill=self.color, font=self.fn_sm)

        # Separator inside box
        self.draw.line([(sx0 + 6, sy0 + 28), (sx1 - 6, sy0 + 28)],
                       fill=(200, 200, 200), width=1)

        # Signature strokes
        _draw_signature(self.draw, self.bank,
                        sx0 + 6, sy0 + 34,
                        sx1 - sx0 - 12, sy1 - sy0 - 40)

    # ── Shared bottom row ─────────────────────────────────────────────────

    def _render_bottom_row(self, fb: BoundingBox) -> None:
        """Expiration date (large) + MICR reference line."""
        # Reserve bottom 48px
        by = fb.y1 - 48
        draw_irregular_line(self.draw, fb.x0, fb.x1, by,
                            color=(200, 200, 200), amplitude=0.5, jitter_y=1)

        by += 4

        # Expiration date (prominent, colored)
        if self.show.get("expiry", True):
            exp = self.data.get("expiry_date")
            if isinstance(exp, datetime):
                exp_str = exp.strftime("%d / %m / %Y")
            else:
                exp_str = str(exp)

            exp_lbl = "Date d'expiration  |  تاريخ الانتهاء"
            # Slight jitter on label vs value y position
            y_lbl = by - 14 + random.randint(-1, 1)
            y_val = by + random.randint(-1, 1)
            self.draw.text((fb.x0 + 8, y_lbl), exp_lbl,
                           fill=jitter_text_color((80, 80, 80)), font=self.fn_mic)
            self.draw.text((fb.x0 + 8, y_val), exp_str,
                           fill=jitter_text_color(self.color, spread=8),
                           font=self.fn_num)

        # MICR reference line (right-aligned, with per-char horizontal jitter)
        if self.show.get("micr", True):
            ref  = self.data.get("cheque_reference", "")
            rib  = self.data.get("rib", "")[:18]
            micr = f'*"{rib}"  {ref}'
            mw   = _tw(self.draw, micr, self.fn_micr)
            # Slight horizontal jitter on the MICR block position
            mx = fb.x1 - mw - 10 + random.randint(-3, 3)
            my = by + random.randint(-1, 1)
            blurred_text_layer(
                self.img, micr, (mx, my),
                self.fn_micr,
                jitter_text_color((15, 15, 15)),
                blur_radius=random.uniform(0.4, 0.9),
            )

    # ── Security footer bar ───────────────────────────────────────────────

    def _render_security_bar(self, fb: BoundingBox) -> None:
        y_bar = fb.y1 - 22
        draw_irregular_line(self.draw, fb.x0, fb.x1, y_bar,
                            color=self.color, amplitude=0.4, jitter_y=1)

        legal = (f"CHÈQUE VALABLE 1099 JOURS • NON ENDOSSABLE • "
                 f"{self.bank['name']} – Tunis, Tunisie")
        self.draw.text((fb.x0 + 5 + random.randint(-1, 1), y_bar + 2), legal,
                       fill=jitter_text_color((130, 130, 130)), font=self.fn_mic)

        serial = "".join(random.choices(string.ascii_uppercase + string.digits, k=55))
        self.draw.text((fb.x0 + 5 + random.randint(-1, 1), y_bar + 13), serial,
                       fill=jitter_text_color((200, 200, 200), spread=8), font=self.fn_mic)

    # ── Helper ────────────────────────────────────────────────────────────

    def _vline(self, x: int, y0: int, y1: int) -> None:
        c = (
            self.color[0] // 4 + 180,
            self.color[1] // 4 + 180,
            self.color[2] // 4 + 180,
        )
        self.draw.line([(x, y0), (x, y1)], fill=c, width=1)