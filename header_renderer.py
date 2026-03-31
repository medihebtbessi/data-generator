"""
header_renderer.py  (v3 – constraint-safe)
==========================================
Renders the BCT cheque header zone using a validated dynamic layout.

Element renderers
-----------------
_render_logo()         – image logo with text fallback
_render_bank_name()    – French + Arabic bank name block
_render_qr_adaptive()  – QR with auto-scale when bounding box < QR_SIZE
_render_plafond()      – ceiling amount block with size-adaptive fallback
_render_cheque_info()  – title / BPD / number block

Safety layer (new in v2, extended in v3)
-----------------------------------------
validate_header_layout() is called in HeaderRenderer.render() BEFORE any
drawing.  The validated/repaired slot dict is used for all subsequent
rendering, so invariants P1–P5 are always satisfied at render time.

_check_element_fits()  – pre-render minimum-size guard; when it returns False
                         the element is SKIPPED (not silently rendered into a
                         too-small box) so no content overlap occurs.
_render_qr_adaptive()  – scales the QR image down to fit if bb.h < QR_SIZE,
                         respects QR_MIN_RENDERABLE as absolute floor

Slot splitting
--------------
_split_ratios_for_slot() wraps compute_split_ratios() from layout_engine,
passing the actual available_h of the bounding box so minimums are exact.
"""

from __future__ import annotations

import os
import random
import warnings as _warnings
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from layout_engine import (
    Align, VAlign, BoundingBox, LayoutConfig, LayoutEngine,
    QR_SIZE, QR_MIN_RENDERABLE, QR_PAD,
    ELEM_MIN_H, ELEM_MIN_W,
    compute_split_ratios,
    validate_header_layout,
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
        ("C:/Windows/Fonts/arial.ttf",    "C:/Windows/Fonts/arialbd.ttf"),
        ("C:/Windows/Fonts/calibri.ttf",  "C:/Windows/Fonts/calibrib.ttf"),
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


def _tw(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def _th(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]


# ── Font set ───────────────────────────────────────────────────────────────

def _font_set(scale: float = 1.0) -> dict[str, ImageFont.FreeTypeFont]:
    def s(base: int) -> int:
        return max(8, int(base * scale))
    return {
        "bank":    _get_font(s(20), bold=True),
        "bank_ar": _get_font(s(17)),
        "title":   _get_font(s(22), bold=True),
        "bpd":     _get_font(s(15)),
        "num":     _get_font(s(16), bold=True),
        "plafond": _get_font(s(13)),
        "micro":   _get_font(s(9)),
    }


# ── Amount-to-words (inline copy to avoid circular imports) ───────────────

_U = ["","un","deux","trois","quatre","cinq","six","sept","huit","neuf"]
_T = ["dix","onze","douze","treize","quatorze","quinze","seize",
      "dix-sept","dix-huit","dix-neuf"]
_D = ["","","vingt","trente","quarante","cinquante","soixante",
      "soixante-dix","quatre-vingt","quatre-vingt-dix"]

def _n2fr(n: int) -> str:
    if n == 0:  return "zéro"
    if n < 10:  return _U[n]
    if n < 20:  return _T[n - 10]
    if n < 100:
        d, u = divmod(n, 10)
        return _D[d] + ("-" + _U[u] if u else "")
    if n < 1000:
        h, r = divmod(n, 100)
        base = (_U[h] + " cent") if h > 1 else "cent"
        return base + (" " + _n2fr(r) if r else "")
    if n < 1_000_000:
        t, r = divmod(n, 1000)
        return (_n2fr(t) + " mille") + (" " + _n2fr(r) if r else "")
    return str(n)




# ── Pre-render size guard ──────────────────────────────────────────────────

def _check_element_fits(element: str, bb: BoundingBox) -> bool:
    """
    Return True when bb satisfies the element's minimum size budget.
    Emits a Python warning (not an exception) when the check fails so
    callers can decide whether to skip, adapt, or proceed.
    """
    min_w = ELEM_MIN_W.get(element, 40)
    min_h = ELEM_MIN_H.get(element, 20)

    if bb.w < min_w or bb.h < min_h:
        _warnings.warn(
            f"[HeaderRenderer] '{element}' bb=({bb.w}×{bb.h}) "
            f"< required ({min_w}×{min_h}); element will be adapted.",
            stacklevel=3,
        )
        return False
    return True


# ── Split-ratio helper (wraps layout_engine) ──────────────────────────────

def _split_ratios_for_slot(elements: list[str], slot_bb: BoundingBox) -> list[float]:
    """
    Delegate to compute_split_ratios() with the actual pixel height of the
    slot bounding box so minimum allocations are exact, not estimated.
    """
    return compute_split_ratios(elements, slot_bb.h)


# ── Individual element renderers ──────────────────────────────────────────

def _render_logo(img:   Image.Image,
                 draw:  ImageDraw.ImageDraw,
                 bank:  dict,
                 bb:    BoundingBox,
                 align: Align,
                 fonts: dict) -> None:
    """
    Render the bank logo image (or a styled text fallback) inside bb.
    The image is scaled to fit the bounding box while preserving aspect ratio.
    Minimum size: 60 × 30 px (ELEM_MIN_W/H for 'logo').
    """
    color = bank["color"]
    path  = bank.get("logo", "")

    if os.path.exists(path):
        try:
            logo  = Image.open(path).convert("RGBA")
            avail_w = max(bb.w - 10, 10)
            avail_h = max(bb.h - 10, 10)
            ratio   = min(avail_h / logo.height, avail_w / logo.width, 1.0)
            nw      = max(1, int(logo.width  * ratio))
            nh      = max(1, int(logo.height * ratio))
            logo    = logo.resize((nw, nh), Image.LANCZOS)
            x       = bb.clamp_x(bb.x_for(nw, align), nw)
            y       = bb.clamp_y(bb.y_for(nh, VAlign.MIDDLE), nh)
            img.paste(logo, (x, y), logo)
            return
        except Exception:
            pass   # fall through to text fallback

    # Text fallback
    fn  = fonts["bank"]
    lbl = f"[{bank['short']}]"
    lw  = _tw(draw, lbl, fn)
    lh  = _th(draw, lbl, fn)
    x   = bb.clamp_x(bb.x_for(lw, align), lw)
    y   = bb.clamp_y(bb.y_for(lh, VAlign.MIDDLE), lh)
    draw.text((x, y), lbl, fill=color, font=fn)


def _render_bank_name(draw:  ImageDraw.ImageDraw,
                      bank:  dict,
                      bb:    BoundingBox,
                      align: Align,
                      fonts: dict) -> None:
    """
    Render the French bank name + Arabic subtitle, vertically centred in bb.
    The French name is truncated word-by-word if it would overflow the width.
    """
    color   = bank["color"]
    fn_fr   = fonts["bank"]
    fn_ar   = fonts["bank_ar"]
    name    = bank["name"]
    name_ar = bank["header_ar"]

    # Truncate French name to fit width  (word-aware, then char-aware fallback)
    avail_w = bb.w - 12
    if _tw(draw, name, fn_fr) > avail_w:
        words = name.split()
        for cut in range(len(words) - 1, 0, -1):
            candidate = " ".join(words[:cut])
            if _tw(draw, candidate, fn_fr) <= avail_w:
                name = candidate
                break
        else:
            while _tw(draw, name, fn_fr) > avail_w and len(name) > 4:
                name = name[:-1]

    fr_h    = _th(draw, name,    fn_fr)
    ar_h    = _th(draw, name_ar, fn_ar)
    total_h = fr_h + 6 + ar_h
    y0      = bb.clamp_y(bb.y_for(total_h, VAlign.MIDDLE), total_h)

    fr_w = _tw(draw, name,    fn_fr)
    ar_w = _tw(draw, name_ar, fn_ar)

    draw.text((bb.clamp_x(bb.x_for(fr_w, align), fr_w), y0),
              name,    fill=color, font=fn_fr)
    draw.text((bb.clamp_x(bb.x_for(ar_w, align), ar_w), y0 + fr_h + 6),
              name_ar, fill=color, font=fn_ar)


def _render_qr_adaptive(img:    Image.Image,
                        qr_img: Image.Image,
                        bb:     BoundingBox,
                        align:  Align) -> bool:
    """
    Render the QR code inside bb with automatic downscaling.

    Logic
    -----
    1. Compute available space: bb dimensions minus QR_PAD on each side.
    2. If available < QR_MIN_RENDERABLE  → skip (too small to scan; log warning).
    3. If available < QR_SIZE            → scale the QR image down to fit.
    4. Place scaled/original QR at the correct alignment inside bb.

    Returns True if the QR was rendered, False if skipped.
    """
    avail_w = bb.w - QR_PAD * 2
    avail_h = bb.h - QR_PAD * 2

    if avail_w < QR_MIN_RENDERABLE or avail_h < QR_MIN_RENDERABLE:
        _warnings.warn(
            f"[HeaderRenderer] QR skipped: bb=({bb.w}×{bb.h}) "
            f"< minimum ({QR_MIN_RENDERABLE}×{QR_MIN_RENDERABLE})",
            stacklevel=2,
        )
        return False

    qw, qh = qr_img.size

    # Scale down only if necessary (never upscale)
    if avail_w < qw or avail_h < qh:
        scale    = min(avail_w / qw, avail_h / qh)
        new_size = max(QR_MIN_RENDERABLE, int(qw * scale))
        qr_img   = qr_img.resize((new_size, new_size), Image.NEAREST)
        qw = qh  = new_size

    x = bb.clamp_x(bb.x_for(qw, align), qw)
    y = bb.clamp_y(bb.y_for(qh, VAlign.MIDDLE), qh)
    img.paste(qr_img, (x, y))
    return True


def _render_plafond(draw: ImageDraw.ImageDraw,
                    bank: dict, bb: BoundingBox, align: Align,
                    plafond: int, fonts: dict,
                    qr_occupies_left: bool = False) -> None:
    """
    Render plafond text block inside bb.

    Size-adaptive behaviour
    -----------------------
    Full layout  (bb.h >= ELEM_MIN_H['plafond']):  label1 + label2 + value
    Compact layout (bb.h < ELEM_MIN_H['plafond']): value line only, centred
    """

    color  = bank["color"]
    fn_pl  = fonts["plafond"]
    fn_mic = fonts["micro"]

    val = f"{plafond:,.0f} TND".replace(",", " ")

    min_h = ELEM_MIN_H.get("plafond", 52)

    if bb.h < min_h:
        # Compact fallback: just the numeric value, vertically centred
        val_h = _th(draw, val, fn_pl)
        val_w = _tw(draw, val, fn_pl)
        y = bb.clamp_y(bb.y_for(val_h, VAlign.MIDDLE), val_h)
        x = bb.clamp_x(bb.x_for(val_w, align), val_w)
        draw.text((x, y), val, fill=color, font=fn_pl)
        return

    label1 = "Plafond du chèque"
    label2 = "القيمة القصوى للشيك"

    lines      = [label1, label2, val]
    fonts_list = [fn_mic, fn_mic, fn_pl]

    line_h  = _th(draw, label1, fn_mic) + 2
    total_h = line_h * len(lines)

    y0 = bb.y_for(total_h, VAlign.MIDDLE)

    for i, (txt, fn) in enumerate(zip(lines, fonts_list)):
        tw_ = _tw(draw, txt, fn)
        x   = bb.x_for(tw_, align)
        draw.text(
            (x, y0 + i * line_h),
            txt,
            fill=(60, 60, 60) if i < 2 else color,
            font=fn,
        )

def _render_cheque_info(draw:       ImageDraw.ImageDraw,
                        bank:       dict,
                        bb:         BoundingBox,
                        align:      Align,
                        cheque_num: str,
                        fonts:      dict) -> None:
    """
    Render the cheque identification block:
      Line 1: "شيك  Chèque  BANQUE"
      Line 2: "B.P.D: ─────────────"
      Line 3: "N°: XXXXXXX  عدد"

    If bb is too narrow for line 1, "BANQUE" is dropped.
    """
    color    = bank["color"]
    fn_title = fonts["title"]
    fn_bpd   = fonts["bpd"]
    fn_num   = fonts["num"]

    cheque_lbl = "شيك  Chèque"
    banque_lbl = " BANQUE"
    bpd_lbl    = "B.P.D: " + "─" * 10
    num_lbl    = f"N°: {cheque_num}  عدد"

    lh0     = _th(draw, cheque_lbl, fn_title) + 4
    total_h = lh0 + 20 + 18
    y0      = bb.clamp_y(bb.y_for(total_h, VAlign.MIDDLE), total_h)

    # ── Line 1: title (+ BANQUE if it fits) ───────────────────────────────
    title_w  = _tw(draw, cheque_lbl, fn_title)
    banque_w = _tw(draw, banque_lbl, fn_bpd)
    combined = title_w + banque_w

    if combined <= bb.w - 12:
        tx = bb.clamp_x(bb.x_for(combined, align), combined)
        draw.text((tx, y0), cheque_lbl, fill=color, font=fn_title)
        draw.text((tx + title_w, y0 + 4), banque_lbl,
                  fill=(50, 50, 50), font=fn_bpd)
    else:
        tx = bb.clamp_x(bb.x_for(title_w, align), title_w)
        draw.text((tx, y0), cheque_lbl, fill=color, font=fn_title)

    # ── Line 2: BPD ───────────────────────────────────────────────────────
    bw = _tw(draw, bpd_lbl, fn_bpd)
    bx = bb.clamp_x(bb.x_for(bw, align), bw)
    draw.text((bx, y0 + lh0), bpd_lbl, fill=(50, 50, 50), font=fn_bpd)

    # ── Line 3: Number ────────────────────────────────────────────────────
    nw = _tw(draw, num_lbl, fn_num)
    nx = bb.clamp_x(bb.x_for(nw, align), nw)
    draw.text((nx, y0 + lh0 + 20), num_lbl, fill=(20, 20, 20), font=fn_num)


# ── Separator helpers ──────────────────────────────────────────────────────

def _draw_hline(draw: ImageDraw.ImageDraw,
                x0: int, x1: int, y: int,
                color: tuple, width: int = 1) -> None:
    draw.line([(x0, y), (x1, y)], fill=color, width=width)


def _draw_vline(draw: ImageDraw.ImageDraw,
                x: int, y0: int, y1: int,
                color: tuple) -> None:
    ghost = (
        color[0] // 4 + 180,
        color[1] // 4 + 180,
        color[2] // 4 + 180,
    )
    draw.line([(x, y0), (x, y1)], fill=ghost, width=1)


# ══════════════════════════════════════════════════════════════════════════════
# HeaderRenderer
# ══════════════════════════════════════════════════════════════════════════════

class HeaderRenderer:
    """
    Renders the header zone of a BCT cheque.

    render() workflow
    -----------------
    1. Compute slot BoundingBoxes from LayoutEngine.
    2. Call validate_header_layout() → obtain a constraint-safe slot dict.
    3. Log any fixes that were applied.
    4. Render decorative chrome (stripe, bottom separator, slot dividers).
    5. Dispatch each slot to _render_slot().

    _render_slot() workflow
    -----------------------
    1. If slot has 1 element → render directly into full slot bb.
    2. If slot has N > 1 elements:
         a. Call _split_ratios_for_slot() (minimum-height aware).
         b. Build a sub-BoundingBox for each element.
         c. For each sub-bb: call _check_element_fits(); render or adapt.

    QR adaptive rendering
    ---------------------
    _render_qr_adaptive() handles QR scaling:
      - bb too small (< QR_MIN_RENDERABLE): QR skipped with warning
      - bb between QR_MIN_RENDERABLE and QR_SIZE: QR scaled down to fit
      - bb >= QR_SIZE: QR rendered at full size
    """

    def __init__(self,
                 img:    Image.Image,
                 draw:   ImageDraw.ImageDraw,
                 bank:   dict,
                 cfg:    LayoutConfig,
                 engine: LayoutEngine) -> None:
        self.img    = img
        self.draw   = draw
        self.bank   = bank
        self.cfg    = cfg
        self.engine = engine
        self.fonts  = _font_set(cfg.font_scale)
        self.color  = bank["color"]

    # ── Public entry point ────────────────────────────────────────────────

    def render(self,
               cheque_num: str,
               plafond:    int,
               qr_img:     Image.Image,
               *,
               show_logo: bool = True,
               show_qr:   bool = True) -> None:
        """
        Render all header elements.

        Calls validate_header_layout() before drawing anything so that
        invariants P1–P4 are always satisfied regardless of which
        HeaderVariant was chosen.

        Parameters
        ----------
        cheque_num : 7-digit string
        plafond    : ceiling amount integer
        qr_img     : QR PIL Image (will be auto-scaled if needed)
        show_logo  : False → logo zone left blank (invalid cheque use)
        show_qr    : False → QR zone left blank (invalid cheque use)
        """
        v  = self.cfg.header_variant
        hb = self.cfg.header_bb

        left_bb, center_bb, right_bb = self.engine.header_slot_boxes(self.cfg)
        slot_boxes  = {"left": left_bb, "center": center_bb, "right": right_bb}
        slot_widths = self.engine.header_slot_widths(self.cfg)
        slot_height = self.engine.header_slot_height(self.cfg)

        # ── Step 1: Validate and repair slot assignments ───────────────────
        raw_slots   = v.as_slot_dict()
        fixed_slots, fix_log = validate_header_layout(
            raw_slots,
            slot_widths=slot_widths,
            slot_height=slot_height,
        )

        if fix_log:
            for msg in fix_log:
                _warnings.warn(f"[HeaderRenderer.validate] {msg}", stacklevel=2)

        # ── Step 2: QR placeholder when not shown ─────────────────────────
        if not show_qr:
            qr_img = Image.new("RGB", (QR_SIZE, QR_SIZE), (255, 255, 255))

        # ── Step 3: Decorative chrome ──────────────────────────────────────
        stripe_h = random.randint(6, 10)
        self.draw.rectangle(
            [(hb.x0, hb.y0), (hb.x1, hb.y0 + stripe_h)],
            fill=self.color,
        )
        _draw_hline(self.draw, hb.x0, hb.x1, hb.y1, self.color, width=2)

        # Subtle vertical dividers between slots
        _draw_vline(self.draw, left_bb.x1,
                    hb.y0 + stripe_h + 4, hb.y1 - 4, self.color)
        _draw_vline(self.draw, center_bb.x1,
                    hb.y0 + stripe_h + 4, hb.y1 - 4, self.color)

        # ── Step 4: Render each slot ──────────────────────────────────────
        for slot_name, elements in fixed_slots.items():
            bb    = slot_boxes[slot_name]
            align = v.align_for(slot_name)
            self._render_slot(
                bb, align, elements,
                cheque_num, plafond, qr_img, show_logo,
            )

    # ── Slot dispatcher ───────────────────────────────────────────────────

    def _render_slot(self,
                     bb:         BoundingBox,
                     align:      Align,
                     elements:   list[str],
                     cheque_num: str,
                     plafond:    int,
                     qr_img:     Image.Image,
                     show_logo:  bool) -> None:
        """
        Render all elements in one slot.

        Single element → full slot bb.
        Multiple elements → vertically partitioned sub-bbs via
        _split_ratios_for_slot() (minimum-height aware).
        """
        if not elements:
            return

        if len(elements) == 1:
            sub_bb = self._jitter_bb(bb)
            self._render_element(elements[0], sub_bb, align,
                                  cheque_num, plafond, qr_img, show_logo)
            return

        # Multiple elements: compute height ratios with exact slot height
        ratios = _split_ratios_for_slot(elements, bb)
        y_cur  = bb.y0

        for elem, ratio in zip(elements, ratios):
            seg_h  = max(1, int(bb.h * ratio))
            seg_bb = BoundingBox(bb.x0, y_cur, bb.x1, y_cur + seg_h)
            jbb    = self._jitter_bb(seg_bb)

            # Pre-render size check: skip the element when the bounding box
            # is genuinely too small to render it readably.  This prevents
            # content from being drawn into a cramped area and overlapping
            # with neighbouring elements.
            if not _check_element_fits(elem, jbb):
                y_cur += seg_h
                continue

            self._render_element(elem, jbb, align,
                                  cheque_num, plafond, qr_img, show_logo)
            y_cur += seg_h

    # ── Element dispatcher ────────────────────────────────────────────────

    def _render_element(self,
                        name:       str,
                        bb:         BoundingBox,
                        align:      Align,
                        cheque_num: str,
                        plafond:    int,
                        qr_img:     Image.Image,
                        show_logo:  bool) -> None:
        """
        Dispatch to the appropriate renderer.

        Size-adaptive behaviour per element:
          qr          → _render_qr_adaptive (scales down, respects floor)
          logo        → _render_logo        (scales to fit, text fallback)
          bank_name   → _render_bank_name   (truncates to width)
          cheque_info → _render_cheque_info (drops "BANQUE" if too narrow)
          plafond     → _render_plafond     (drops label lines if too short)
        """
        if name == "logo":
            if show_logo:
                _render_logo(self.img, self.draw, self.bank, bb, align, self.fonts)

        elif name == "qr":
            _render_qr_adaptive(self.img, qr_img, bb, align)

        elif name == "bank_name":
            if bb.can_fit("bank_name"):
                _render_bank_name(self.draw, self.bank, bb, align, self.fonts)
            else:
                # Minimal fallback: just the short bank name on one line
                fn  = self.fonts["bank"]
                lbl = self.bank["short"]
                lw  = _tw(self.draw, lbl, fn)
                x   = bb.clamp_x(bb.x_for(lw, align), lw)
                y   = bb.clamp_y(bb.y_for(16, VAlign.MIDDLE), 16)
                self.draw.text((x, y), lbl, fill=self.color, font=fn)

        elif name == "cheque_info":
            _render_cheque_info(
                self.draw, self.bank, bb, align, cheque_num, self.fonts
            )

        elif name == "plafond":
            _render_plafond(
                self.draw, self.bank, bb, align, plafond, self.fonts
            )

    # ── Jitter helper ─────────────────────────────────────────────────────

    def _jitter_bb(self, bb: BoundingBox) -> BoundingBox:
        """
        Apply bounded position jitter for realism.
        Jitter is asymmetric so the bb still contains the original area
        plus or minus a small pixel offset.
        """
        jx = self.cfg.jitter_x
        jy = self.cfg.jitter_y
        return BoundingBox(
            bb.x0 + random.randint(-jx, jx),
            bb.y0 + random.randint(-jy, jy),
            bb.x1 + random.randint(-jx, jx),
            bb.y1 + random.randint(-jy, jy),
        )