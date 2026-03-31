"""
qr_generator.py
===============
Strict QR code generation for BCT Tunisian cheques.

URL format:
  https://pecc.tn/1/{cheque_number}/{composite_reference}/1/{client_name}/{plafond}/{expiry}/{rib}

Field rules:
  cheque_number        → exactly 7 digits
  composite_reference  → cheque_number + reference_suffix (min 18 random digits)
  client_name          → uppercase, no special chars except space
  plafond              → zero-padded to 15 digits
  expiry               → DDMMYYYY
  rib                  → exactly 20 digits
"""

import re
import random
import string
import qrcode
from PIL import Image
from datetime import datetime


# ── Validation helpers ─────────────────────────────────────────────────────

def _validate_cheque_number(cn: str) -> str:
    if not re.fullmatch(r"\d{7}", cn):
        raise ValueError(f"cheque_number must be exactly 7 digits, got: {cn!r}")
    return cn


def _validate_rib(rib: str) -> str:
    digits_only = re.sub(r"\s", "", rib)
    if not re.fullmatch(r"\d{20}", digits_only):
        raise ValueError(f"RIB must be exactly 20 digits (got {len(digits_only)}): {digits_only!r}")
    return digits_only


def _validate_expiry(expiry: datetime) -> str:
    return expiry.strftime("%d%m%Y")


def _pad_plafond(plafond: int) -> str:
    s = str(int(plafond))
    if len(s) > 15:
        raise ValueError(f"plafond too large for 15-digit field: {plafond}")
    return s.zfill(15)


def _sanitize_client_name(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9 ]", "", name).upper().strip()
    clean = re.sub(r"\s+", " ", clean)
    if not clean:
        clean = "CLIENT"
    return clean


def _build_composite_reference(cheque_number: str, suffix_digits: int = 19) -> str:
    suffix = "".join(random.choices(string.digits, k=suffix_digits))
    return cheque_number + suffix


# ── Public API ─────────────────────────────────────────────────────────────

def build_qr_payload(
    cheque_number: str,
    client_name: str,
    plafond: int,
    expiry: datetime,
    rib: str,
    *,
    corrupt: bool = False,
    corrupt_fields: list[str] | None = None,
) -> dict:
    """
    Build and validate the QR payload dict.

    Parameters
    ----------
    corrupt        : if True, intentionally corrupt fields listed in corrupt_fields
    corrupt_fields : list of field names to corrupt ('cheque_number', 'rib',
                     'plafond', 'reference')

    Returns
    -------
    dict with keys:
        url                  – full QR URL string
        cheque_number        – validated 7-digit string
        composite_reference  – full reference string
        client_name          – sanitized uppercase name
        plafond_str          – zero-padded 15-char plafond
        expiry_str           – DDMMYYYY string
        rib_clean            – clean 20-digit RIB
    """
    corrupt_fields = set(corrupt_fields or [])

    cn = _validate_cheque_number(cheque_number)
    rb = _validate_rib(rib)
    ex = _validate_expiry(expiry)
    pl = _pad_plafond(plafond)
    nm = _sanitize_client_name(client_name)
    cr = _build_composite_reference(cn)

    # ── Controlled corruption for invalid cheques ──────────────────────────
    if corrupt:
        if "cheque_number" in corrupt_fields:
            cn = "".join(random.choices(string.digits, k=7))
        if "rib" in corrupt_fields:
            rb = "".join(random.choices(string.digits, k=20))
        if "plafond" in corrupt_fields:
            wrong = int(pl) + random.randint(10000, 999999)
            pl = str(wrong).zfill(15)[:15]
        if "reference" in corrupt_fields:
            # Corrupt the suffix part of composite_reference
            cr = cn + "".join(random.choices(string.digits, k=19))
            # Flip a few digits
            lst = list(cr)
            for _ in range(random.randint(2, 5)):
                pos = random.randint(7, len(lst) - 1)
                lst[pos] = random.choice(string.digits)
            cr = "".join(lst)

    url = (
        f"https://pecc.tn/1"
        f"/{cn}"
        f"/{cr}"
        f"/1"
        f"/{nm}"
        f"/{pl}"
        f"/{ex}"
        f"/{rb}"
    )

    return {
        "url":                 url,
        "cheque_number":       cn,
        "composite_reference": cr,
        "client_name":         nm,
        "plafond_str":         pl,
        "expiry_str":          ex,
        "rib_clean":           rb,
    }


def make_qr_image(
    payload_dict: dict,
    color: tuple[int, int, int] = (0, 0, 0),
    size: int = 130,
    error_correction=None,
) -> Image.Image:
    """
    Render a QR code image from a payload dict produced by build_qr_payload().

    Parameters
    ----------
    payload_dict : output of build_qr_payload()
    color        : fill color (RGB)
    size         : output pixel size (square)
    error_correction : qrcode constant, defaults to ERROR_CORRECT_M
    """
    if error_correction is None:
        error_correction = qrcode.constants.ERROR_CORRECT_M

    qr = qrcode.QRCode(
        version=None,
        error_correction=error_correction,
        box_size=4,
        border=2,
    )
    qr.add_data(payload_dict["url"])
    qr.make(fit=True)
    q = qr.make_image(fill_color=color, back_color="white")
    return q.convert("RGB").resize((size, size), Image.NEAREST)


def make_corrupted_qr_image(
    color: tuple[int, int, int] = (100, 100, 100),
    size: int = 130,
) -> Image.Image:
    """Render a QR with invalid/garbage data (visual corruption only)."""
    garbage = "###INVALID_BCT_" + "".join(random.choices(string.ascii_uppercase, k=16))
    qr = qrcode.QRCode(version=2, box_size=3, border=2)
    qr.add_data(garbage)
    qr.make(fit=True)
    q = qr.make_image(fill_color=color, back_color="white")
    return q.convert("RGB").resize((size, size), Image.NEAREST)