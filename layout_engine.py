"""
layout_engine.py  (v2 – constraint-safe)
========================================
Dynamic layout system for BCT Tunisian cheque generation.

Architecture
------------
BoundingBox           – named-tuple rectangle with placement helpers
HeaderVariant         – 3-slot element assignment + width ratios
LayoutConfig          – per-cheque frozen snapshot + derived BoundingBoxes
LayoutEngine          – random config generator + zone-box helpers

Constraint system
-----------------
validate_header_layout()   – detects and repairs slot violations at render-time
compute_split_ratios()     – minimum-height-aware vertical split ratios
_find_best_destination()   – safe slot selection for relocated elements

Guaranteed invariants after validate_header_layout()
-----------------------------------------------------
  P1  qr and logo NEVER share a slot
  P2  Each slot contains at most 1 LARGE element  (qr | logo)
  P3  QR receives >= QR_MIN_RENDERABLE px of height in its slot
  P4  QR slot width >= qr_size + QR_PAD
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple


# ── Canvas constants ───────────────────────────────────────────────────────

W = 1800
H = 750
M = 14       # outer margin px


# ── QR size constants (re-exported for header_renderer) ───────────────────

QR_SIZE           = 130   # default QR render size (square px)
QR_MIN_RENDERABLE =  80   # below this px the QR modules are unreadable
QR_PAD            =   8   # clearance added around the QR image


# ── Element taxonomy ───────────────────────────────────────────────────────

LARGE_ELEMENTS = frozenset({"qr", "logo"})
SMALL_ELEMENTS = frozenset({"bank_name", "cheque_info", "plafond"})
ALL_ELEMENTS   = LARGE_ELEMENTS | SMALL_ELEMENTS

# Pairs that must never occupy the same slot
INCOMPATIBLE_PAIRS: frozenset[frozenset] = frozenset({
    frozenset({"qr", "logo"}),
})


# ── Element size budgets ───────────────────────────────────────────────────
#
# ELEM_MIN_H[e]      – minimum pixel height for e to be readable
# ELEM_IDEAL_WEIGHT[e] – relative weight for distributing surplus height
# ELEM_MIN_W[e]      – minimum pixel width for e to be readable

ELEM_MIN_H: dict[str, int] = {
    "qr":          QR_SIZE + QR_PAD,    # 138 px
    "logo":        40,
    "bank_name":   44,                   # 2 text lines + gap
    "cheque_info": 58,                   # 3 text lines + spacing
    "plafond":     52,                   # 4 text lines + spacing
}

ELEM_IDEAL_WEIGHT: dict[str, float] = {
    "qr":          4.0,    # dominant — highest priority
    "logo":        3.5,
    "bank_name":   2.0,
    "cheque_info": 1.8,
    "plafond":     1.4,
}

ELEM_MIN_W: dict[str, int] = {
    "qr":          QR_SIZE + QR_PAD,    # 138 px
    "logo":        60,
    "bank_name":   120,
    "cheque_info": 150,
    "plafond":     120,
}

# Relocation priority: lower number = move first (least important)
RELOC_PRIORITY: dict[str, int] = {
    "plafond":     0,
    "bank_name":   1,
    "cheque_info": 2,
    "logo":        3,
    "qr":          99,   # never relocate QR out of P3 pass
}


# ── Alignment enums ────────────────────────────────────────────────────────

class Align(Enum):
    LEFT   = "left"
    CENTER = "center"
    RIGHT  = "right"


class VAlign(Enum):
    TOP    = "top"
    MIDDLE = "middle"
    BOTTOM = "bottom"


# ── BoundingBox ────────────────────────────────────────────────────────────

class BoundingBox(NamedTuple):
    x0: int
    y0: int
    x1: int
    y1: int

    # ── Dimensions ────────────────────────────────────────────────────────
    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    @property
    def cx(self) -> int:
        return (self.x0 + self.x1) // 2

    @property
    def cy(self) -> int:
        return (self.y0 + self.y1) // 2

    # ── Placement helpers ─────────────────────────────────────────────────
    def x_for(self, elem_w: int, align: Align, pad: int = 6) -> int:
        if align == Align.LEFT:
            return self.x0 + pad
        if align == Align.RIGHT:
            return self.x1 - elem_w - pad
        return self.cx - elem_w // 2   # CENTER

    def y_for(self, elem_h: int, valign: VAlign, pad: int = 4) -> int:
        if valign == VAlign.TOP:
            return self.y0 + pad
        if valign == VAlign.BOTTOM:
            return self.y1 - elem_h - pad
        return self.cy - elem_h // 2   # MIDDLE

    def clamp_x(self, x: int, elem_w: int, pad: int = 4) -> int:
        return max(self.x0 + pad, min(x, self.x1 - elem_w - pad))

    def clamp_y(self, y: int, elem_h: int, pad: int = 4) -> int:
        return max(self.y0 + pad, min(y, self.y1 - elem_h - pad))

    # ── Structural helpers ────────────────────────────────────────────────
    def inset(self, dx: int = 8, dy: int = 4) -> "BoundingBox":
        return BoundingBox(self.x0 + dx, self.y0 + dy,
                           self.x1 - dx, self.y1 - dy)

    def split_h(self, ratio: float) -> tuple["BoundingBox", "BoundingBox"]:
        """Split horizontally (left | right) at ratio (0–1)."""
        mid = self.x0 + int(self.w * ratio)
        return (
            BoundingBox(self.x0, self.y0, mid,     self.y1),
            BoundingBox(mid,     self.y0, self.x1, self.y1),
        )

    def split_v(self, ratio: float) -> tuple["BoundingBox", "BoundingBox"]:
        """Split vertically (top | bottom) at ratio (0–1)."""
        mid = self.y0 + int(self.h * ratio)
        return (
            BoundingBox(self.x0, self.y0, self.x1, mid),
            BoundingBox(self.x0, mid,     self.x1, self.y1),
        )

    def random_jitter(self, jx: int = 6, jy: int = 4) -> "BoundingBox":
        dx = random.randint(-jx, jx)
        dy = random.randint(-jy, jy)
        return BoundingBox(self.x0 + dx, self.y0 + dy,
                           self.x1 + dx, self.y1 + dy)

    def can_fit(self, elem: str) -> bool:
        """True when this box satisfies the element's minimum size budget."""
        return (self.w >= ELEM_MIN_W.get(elem, 40) and
                self.h >= ELEM_MIN_H.get(elem, 20))


# ── Split-ratio engine ─────────────────────────────────────────────────────

def compute_split_ratios(elements: list[str], available_h: int) -> list[float]:
    """
    Compute vertical height ratios for co-located elements in a single slot.

    Algorithm
    ---------
    1. Allocate each element its ELEM_MIN_H first  (guaranteed floor).
    2. Distribute remaining pixels by ELEM_IDEAL_WEIGHT (proportional surplus).
    3. Degenerate case: if sum(minimums) > available_h, use pure min-ratios
       (everyone gets squeezed equally below their declared minimum).

    This ensures QR always receives as close to QR_SIZE px as available_h allows
    before any co-tenant takes space.

    Returns
    -------
    list[float] that sums to 1.0, length == len(elements)
    """
    n = len(elements)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    min_heights = [ELEM_MIN_H.get(e, 30) for e in elements]
    total_min   = sum(min_heights)

    if total_min >= available_h:
        # Not enough room even for minimums → proportional min-ratio
        return [mh / total_min for mh in min_heights]

    remaining = available_h - total_min
    weights   = [ELEM_IDEAL_WEIGHT.get(e, 1.5) for e in elements]
    w_total   = sum(weights)

    heights = [mh + int(remaining * w / w_total)
               for mh, w in zip(min_heights, weights)]

    total = sum(heights)
    return [h / total for h in heights]


def qr_height_in_slot(elements: list[str], available_h: int) -> int:
    """
    Return the pixel height QR would receive via compute_split_ratios.
    Returns 0 if 'qr' is not in elements.
    """
    if "qr" not in elements:
        return 0
    if len(elements) == 1:
        return available_h
    ratios = compute_split_ratios(elements, available_h)
    idx    = elements.index("qr")
    return int(available_h * ratios[idx])


# ── Safe slot destination finder ──────────────────────────────────────────

def _find_best_destination(
    slots:   dict[str, list[str]],
    element: str,
    exclude: list[str],
) -> str:
    """
    Find the best slot to receive ``element`` without creating new violations.

    Selection criteria (tried in order):
      1. No incompatible pair would be created
      2. No second LARGE element would be placed in the slot
      3. Fewest current elements (lowest load wins)

    Falls back progressively to weaker constraints if no ideal slot exists.
    """
    is_large = element in LARGE_ELEMENTS

    # ── Ideal candidates: full constraint satisfaction ─────────────────────
    ideal: list[tuple[int, str]] = []
    for sname, elems in slots.items():
        if sname in exclude:
            continue
        # Check no incompatible pair
        for pair in INCOMPATIBLE_PAIRS:
            if element in pair and any(e in pair and e != element for e in elems):
                break
        else:
            # Check LARGE capacity
            if is_large and any(e in LARGE_ELEMENTS for e in elems):
                continue
            ideal.append((len(elems), sname))

    if ideal:
        ideal.sort()
        return ideal[0][1]

    # ── Relaxed fallback 1: skip LARGE capacity check ─────────────────────
    relaxed: list[tuple[int, str]] = []
    for sname, elems in slots.items():
        if sname in exclude:
            continue
        conflict = False
        for pair in INCOMPATIBLE_PAIRS:
            if element in pair and any(e in pair and e != element for e in elems):
                conflict = True
                break
        if not conflict:
            relaxed.append((len(elems), sname))

    if relaxed:
        relaxed.sort()
        return relaxed[0][1]

    # ── Ultimate fallback: first non-excluded slot ─────────────────────────
    for sname in slots:
        if sname not in exclude:
            return sname

    return list(slots.keys())[0]   # should never be reached with 3 slots


# ── Header layout validator ────────────────────────────────────────────────

def validate_header_layout(
    slots:       dict[str, list[str]],
    slot_widths: dict[str, int],
    slot_height: int,
    *,
    qr_size: int = QR_SIZE,
) -> tuple[dict[str, list[str]], list[str]]:
    """
    Validate and repair header slot assignments before rendering.

    The original ``slots`` dict is NEVER mutated; a repaired deep copy is returned.

    Parameters
    ----------
    slots        : {'left': [...], 'center': [...], 'right': [...]}
    slot_widths  : pixel width of each named slot
    slot_height  : pixel height shared by all slots
    qr_size      : QR image size in pixels

    Returns
    -------
    (fixed_slots, warnings)
        fixed_slots – repaired slot dict ready for rendering
        warnings    – list of human-readable fix messages (empty if no issues)

    Passes
    ------
    P1 – Incompatible pairs  (qr + logo in same slot  → HARD violation)
    P2 – LARGE element cap   (max 1 LARGE per slot)
    P3 – QR minimum height   (QR must receive >= QR_MIN_RENDERABLE px)
    P4 – QR minimum width    (slot width must be >= qr_size + QR_PAD)
    """
    result:   dict[str, list[str]] = {k: list(v) for k, v in slots.items()}
    warnings: list[str]            = []
    qr_min_w = qr_size + QR_PAD

    slot_names = list(result.keys())

    # ── P1: Incompatible pair detection ───────────────────────────────────
    for sname in slot_names:
        elems = result[sname]
        for pair in INCOMPATIBLE_PAIRS:
            in_slot = [e for e in elems if e in pair]
            if len(in_slot) < 2:
                continue   # no conflict here

            # Keep highest-priority element (qr > logo), relocate rest
            prio_order  = ["qr", "logo"]
            keep        = next((e for e in prio_order if e in in_slot), in_slot[0])
            to_relocate = [e for e in in_slot if e != keep]

            for elem in to_relocate:
                result[sname].remove(elem)
                dest = _find_best_destination(result, elem, exclude=[sname])
                result[dest].append(elem)
                warnings.append(
                    f"P1: incompatible pair {set(pair)} in '{sname}' "
                    f"→ relocated '{elem}' to '{dest}'"
                )

    # ── P2: Max 1 LARGE per slot ──────────────────────────────────────────
    for sname in slot_names:
        large_here = [e for e in result[sname] if e in LARGE_ELEMENTS]
        while len(large_here) > 1:
            # Move lowest-priority LARGE (logo < qr)
            to_move = "logo" if "logo" in large_here else large_here[-1]
            result[sname].remove(to_move)
            large_here.remove(to_move)
            dest = _find_best_destination(result, to_move, exclude=[sname])
            result[dest].append(to_move)
            warnings.append(
                f"P2: 2 LARGE elements in '{sname}' "
                f"→ relocated '{to_move}' to '{dest}'"
            )

    # ── P3: QR minimum height guarantee ───────────────────────────────────
    # After compute_split_ratios, QR must get >= QR_MIN_RENDERABLE px.
    # If not, relocate the lowest-priority co-tenant and recheck.
    for sname in slot_names:
        elems = result[sname]
        if "qr" not in elems or len(elems) == 1:
            continue

        for _ in range(len(elems) - 1):   # at most remove all co-tenants
            qr_h = qr_height_in_slot(result[sname], slot_height)
            if qr_h >= QR_MIN_RENDERABLE:
                break

            others  = [e for e in result[sname] if e != "qr"]
            to_move = min(others, key=lambda e: RELOC_PRIORITY.get(e, 5))
            dest    = _find_best_destination(result, to_move, exclude=[sname])
            result[sname].remove(to_move)
            result[dest].append(to_move)
            warnings.append(
                f"P3: QR height={qr_h}px < {QR_MIN_RENDERABLE}px in '{sname}' "
                f"→ relocated '{to_move}' to '{dest}'"
            )

    # ── P4: QR minimum slot width ─────────────────────────────────────────
    for sname in slot_names:
        if "qr" not in result[sname]:
            continue
        slot_w = slot_widths.get(sname, 0)
        if slot_w >= qr_min_w:
            continue

        # Swap QR to the widest available slot
        widest = max(
            (s for s in slot_names if s != sname),
            key=lambda s: slot_widths.get(s, 0),
        )
        result[sname].remove("qr")
        result[widest].append("qr")
        warnings.append(
            f"P4: QR slot_w={slot_w}px < {qr_min_w}px in '{sname}' "
            f"→ moved QR to '{widest}' (w={slot_widths.get(widest, 0)}px)"
        )

    return result, warnings


# ── HeaderVariant ──────────────────────────────────────────────────────────

@dataclass
class HeaderVariant:
    """
    Defines element distribution across 3 horizontal header slots.

    All 8 built-in variants satisfy P1 and P2 statically.
    P3 and P4 are handled at render-time by validate_header_layout()
    because they depend on the actual pixel dimensions of each cheque.
    """
    left_slot:   list[str]
    center_slot: list[str]
    right_slot:  list[str]

    left_ratio:   float  = 0.28
    center_ratio: float  = 0.38
    right_ratio:  float  = 0.34

    left_align:   Align  = Align.LEFT
    center_align: Align  = Align.CENTER
    right_align:  Align  = Align.RIGHT
    valign:       VAlign = VAlign.MIDDLE

    def as_slot_dict(self) -> dict[str, list[str]]:
        """Return a mutable deep-copy of the slot assignments."""
        return {
            "left":   list(self.left_slot),
            "center": list(self.center_slot),
            "right":  list(self.right_slot),
        }

    def align_for(self, slot_name: str) -> Align:
        return {
            "left":   self.left_align,
            "center": self.center_align,
            "right":  self.right_align,
        }[slot_name]


# ── Pre-built header variants ──────────────────────────────────────────────
#
# All 8 variants satisfy P1 and P2.
# V6 has been corrected: the original right_slot=["logo","qr"] (P1 violation)
# is now split → QR moved to left_slot.

HEADER_VARIANTS: list[HeaderVariant] = [

    # V0 · Classic  logo | name | qr + info
    HeaderVariant(
        left_slot   = ["logo"],
        center_slot = ["bank_name"],
        right_slot  = ["qr", "cheque_info"],
        left_ratio=0.25, center_ratio=0.38, right_ratio=0.37,
    ),

    # V1 · QR-left heavy  qr + plafond | name + info | logo
    HeaderVariant(
        left_slot   = ["qr", "plafond"],
        center_slot = ["bank_name", "cheque_info"],
        right_slot  = ["logo"],
        left_ratio=0.24, center_ratio=0.44, right_ratio=0.32,
        center_align=Align.LEFT,
    ),

    # V2 · Centre-stacked logo  qr | logo + name | info + plafond
    HeaderVariant(
        left_slot   = ["qr"],
        center_slot = ["logo", "bank_name"],
        right_slot  = ["cheque_info", "plafond"],
        left_ratio=0.20, center_ratio=0.44, right_ratio=0.36,
        center_align=Align.CENTER,
    ),

    # V3 · QR centre island  name + logo | qr | info + plafond
    HeaderVariant(
        left_slot   = ["bank_name", "logo"],
        center_slot = ["qr"],
        right_slot  = ["cheque_info", "plafond"],
        left_ratio=0.32, center_ratio=0.22, right_ratio=0.46,
        left_align=Align.LEFT, right_align=Align.LEFT,
    ),

    # V4 · QR-right heavy  logo + name | info | qr + plafond
    HeaderVariant(
        left_slot   = ["logo", "bank_name"],
        center_slot = ["cheque_info"],
        right_slot  = ["qr", "plafond"],
        left_ratio=0.30, center_ratio=0.30, right_ratio=0.40,
        left_align=Align.LEFT, center_align=Align.LEFT,
    ),

    # V5 · QR-left + centre-logo  qr + plafond | logo + name | info
    HeaderVariant(
        left_slot   = ["qr", "plafond"],
        center_slot = ["logo", "bank_name"],
        right_slot  = ["cheque_info"],
        left_ratio=0.24, center_ratio=0.42, right_ratio=0.34,
        center_align=Align.CENTER,
    ),

    # V6 (FIXED) · Separated  qr + info | name + plafond | logo
    #   Previously right_slot=["logo","qr"]  →  P1 violation, now corrected.
    HeaderVariant(
        left_slot   = ["qr", "cheque_info"],
        center_slot = ["bank_name", "plafond"],
        right_slot  = ["logo"],
        left_ratio=0.28, center_ratio=0.40, right_ratio=0.32,
        left_align=Align.LEFT,
    ),

    # V7 · Wide-centre logo  qr | logo + name + info | plafond
    HeaderVariant(
        left_slot   = ["qr"],
        center_slot = ["logo", "bank_name", "cheque_info"],
        right_slot  = ["plafond"],
        left_ratio=0.18, center_ratio=0.58, right_ratio=0.24,
        center_align=Align.CENTER,
    ),
]


# ── LayoutConfig ──────────────────────────────────────────────────────────

@dataclass
class LayoutConfig:
    """
    Immutable snapshot of all layout decisions for one cheque.
    Derived BoundingBoxes are computed automatically via __post_init__.
    """

    header_h: int
    body_h:   int
    footer_h: int

    header_variant_idx: int

    body_align:      Align
    body_line_style: str    # "dots" | "line" | "mixed"
    body_spacing:    int

    footer_left_ratio:   float
    footer_center_ratio: float
    footer_right_ratio:  float

    sig_valign: VAlign

    jitter_x:   int
    jitter_y:   int
    font_scale: float

    # Auto-computed
    header_bb: BoundingBox = field(init=False)
    body_bb:   BoundingBox = field(init=False)
    footer_bb: BoundingBox = field(init=False)

    def __post_init__(self) -> None:
        self.header_bb = BoundingBox(M, M,
                                     W - M, M + self.header_h)
        self.body_bb   = BoundingBox(M, M + self.header_h,
                                     W - M, M + self.header_h + self.body_h)
        self.footer_bb = BoundingBox(M, M + self.header_h + self.body_h,
                                     W - M, H - M)

    @property
    def header_variant(self) -> HeaderVariant:
        return HEADER_VARIANTS[self.header_variant_idx]


# ── LayoutEngine ──────────────────────────────────────────────────────────

class LayoutEngine:
    """
    Produces LayoutConfig instances with controlled randomness.

    Usage
    -----
    engine = LayoutEngine()
    cfg    = engine.generate()          # random valid layout
    cfg    = engine.generate(seed=42)   # reproducible
    """

    # Header height floor raised to 165 px to give QR+co-tenant more vertical room
    HEADER_H_RANGE = (165, 215)
    BODY_H_RANGE   = (220, 290)

    def generate(self, seed: int | None = None) -> LayoutConfig:
        if seed is not None:
            random.seed(seed)

        header_h = random.randint(*self.HEADER_H_RANGE)
        body_h   = random.randint(*self.BODY_H_RANGE)

        footer_h = H - 2 * M - header_h - body_h
        footer_h = max(footer_h, 180)
        body_h   = H - 2 * M - header_h - footer_h   # absorb any correction

        fl = random.uniform(0.22, 0.32)
        fc = random.uniform(0.34, 0.44)
        fr = 1.0 - fl - fc

        return LayoutConfig(
            header_h = header_h,
            body_h   = body_h,
            footer_h = footer_h,

            header_variant_idx = random.randrange(len(HEADER_VARIANTS)),

            body_align      = random.choice(list(Align)),
            body_line_style = random.choice(["dots", "line", "mixed"]),
            body_spacing    = random.randint(2, 10),

            footer_left_ratio   = fl,
            footer_center_ratio = fc,
            footer_right_ratio  = fr,

            sig_valign = random.choice([VAlign.TOP, VAlign.MIDDLE]),

            jitter_x   = random.randint(0, 5),
            jitter_y   = random.randint(0, 3),
            font_scale = random.uniform(0.90, 1.10),
        )

    # ── Zone geometry ─────────────────────────────────────────────────────

    def header_slot_boxes(
        self, cfg: LayoutConfig
    ) -> tuple[BoundingBox, BoundingBox, BoundingBox]:
        """
        Return (left_bb, center_bb, right_bb) for the header.
        Top indent accounts for the decorative stripe; bottom adds clearance.
        """
        hb = cfg.header_bb
        v  = cfg.header_variant

        y0, y1  = hb.y0 + 10, hb.y1 - 4
        x0, x1  = hb.x0 + 6,  hb.x1 - 6
        inner_w = x1 - x0

        x1l = x0  + int(inner_w * v.left_ratio)
        x1c = x1l + int(inner_w * v.center_ratio)

        return (
            BoundingBox(x0,  y0, x1l, y1),
            BoundingBox(x1l, y0, x1c, y1),
            BoundingBox(x1c, y0, x1,  y1),
        )

    def header_slot_widths(self, cfg: LayoutConfig) -> dict[str, int]:
        """Pixel width of each header slot keyed by name."""
        l, c, r = self.header_slot_boxes(cfg)
        return {"left": l.w, "center": c.w, "right": r.w}

    def header_slot_height(self, cfg: LayoutConfig) -> int:
        """Pixel height shared by all header slots."""
        l, _, _ = self.header_slot_boxes(cfg)
        return l.h

    def footer_boxes(
        self, cfg: LayoutConfig
    ) -> tuple[BoundingBox, BoundingBox, BoundingBox]:
        """Return (left_bb, center_bb, right_bb) for the footer."""
        fb      = cfg.footer_bb
        inner_w = fb.w

        x1l = fb.x0 + int(inner_w * cfg.footer_left_ratio)
        x1c = x1l   + int(inner_w * cfg.footer_center_ratio)

        return (
            BoundingBox(fb.x0, fb.y0, x1l,  fb.y1),
            BoundingBox(x1l,  fb.y0, x1c,  fb.y1),
            BoundingBox(x1c,  fb.y0, fb.x1, fb.y1),
        )

    def jitter_pos(self, cfg: LayoutConfig, x: int, y: int) -> tuple[int, int]:
        return (
            x + random.randint(-cfg.jitter_x, cfg.jitter_x),
            y + random.randint(-cfg.jitter_y, cfg.jitter_y),
        )