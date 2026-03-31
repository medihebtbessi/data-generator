"""
==============================================================================
BCT CHEQUE GENERATOR – REFACTORED PRODUCTION GRADE
Dynamic Layout | Flexible Header | Strict QR URLs | Modular Architecture

Modules:
  qr_generator.py     – QR URL builder & validator
  layout_engine.py    – Dynamic bounding-box layout
  header_renderer.py  – Flexible header with 8 variants
  body_renderer.py    – Variable body alignment & line styles
  footer_renderer.py  – 3-column footer with signature zone

QR URL format:
  https://pecc.tn/1/{cheque_number}/{composite_reference}/1/{client_name}/{plafond}/{expiry}/{rib}
==============================================================================
"""

import os
import io
import csv
import random
import string
import numpy as np
import cv2
from faker import Faker
from tqdm import tqdm
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timedelta

# ── Local modules ──────────────────────────────────────────────────────────
from qr_generator    import build_qr_payload, make_qr_image, make_corrupted_qr_image
from layout_engine   import LayoutEngine, LayoutConfig, W, H, M
from header_renderer import HeaderRenderer
from body_renderer   import BodyRenderer
from footer_renderer import FooterRenderer
from realism_effects import apply_realism_effects

fake   = Faker("fr_FR")
engine = LayoutEngine()

random.seed(None)

# ========================== CONFIG ========================================
BASE_DIR      = "bct_dataset_v2"
SPLITS        = {"train": 0.70, "val": 0.15, "test": 0.15}

VALID_COUNT   = 1500
INVALID_COUNT = 900
NON_COUNT     = 900

META_ROWS = []

# ========================== BANKS =========================================
BANKS = [
    {
        "name":      "BANQUE NATIONALE AGRICOLE",
        "short":     "BNA",
        "logo":      "assets/bna.png",
        "color":     (0, 102, 51),
        "bg_tint":   (242, 252, 246),
        "header_ar": "البنك الوطني الفلاحي",
    },
    {
        "name":      "BANQUE INTERNATIONALE ARABE DE TUNISIE",
        "short":     "BIAT",
        "logo":      "assets/biat.png",
        "color":     (0, 51, 153),
        "bg_tint":   (245, 248, 255),
        "header_ar": "البنك الدولي العربي لتونس",
    },
    {
        "name":      "BANQUE ZITOUNA",
        "short":     "ZITOUNA",
        "logo":      "assets/zitouna.png",
        "color":     (0, 128, 0),
        "bg_tint":   (248, 255, 248),
        "header_ar": "بنك الزيتونة",
    },
    {
        "name":      "ARAB TUNISIAN BANK",
        "short":     "ATB",
        "logo":      "assets/atb.png",
        "color":     (180, 0, 0),
        "bg_tint":   (255, 245, 245),
        "header_ar": "البنك العربي لتونس",
    },
    {
        "name":      "SOCIETE TUNISIENNE DE BANQUE",
        "short":     "STB",
        "logo":      "assets/stb.png",
        "color":     (153, 0, 0),
        "bg_tint":   (255, 248, 248),
        "header_ar": "الشركة التونسية للبنك",
    },
    {
        "name":      "UNION INTERNATIONALE DE BANQUES",
        "short":     "UIB",
        "logo":      "assets/uib.png",
        "color":     (0, 51, 102),
        "bg_tint":   (240, 248, 255),
        "header_ar": "الاتحاد الدولي للبنوك",
    },
    {
        "name":      "BANQUE DE L'HABITAT",
        "short":     "BH",
        "logo":      "assets/bh.png",
        "color":     (0, 102, 204),
        "bg_tint":   (240, 250, 255),
        "header_ar": "بنك الإسكان",
    },
    {
        "name":      "BANQUE TUNISO-KOWEITIENNE",
        "short":     "BTK",
        "logo":      "assets/btk.png",
        "color":     (0, 120, 90),
        "bg_tint":   (240, 255, 250),
        "header_ar": "البنك التونسي الكويتي",
    },
]

INVALID_DEFECTS = [
    "expired", "amount_mismatch", "missing_signature", "missing_qr",
    "missing_logo", "missing_rib", "missing_date", "missing_beneficiaire",
    "missing_amount", "invalid_rib", "qr_corrupted", "stamp_annule",
    "overwritten", "fake_color", "wrong_bank_code", "partial_missing",
    "ink_bleed", "torn_border",
    # QR-specific corruptions
    "qr_wrong_cheque_num", "qr_wrong_rib", "qr_wrong_plafond", "qr_wrong_reference",
]

NON_TYPES = [
    "facture_eau", "facture_electricite", "facture_telecom",
    "recu_bancaire", "releve_compte", "ordre_virement",
    "attestation_bancaire", "bon_commande",
]


# ========================== UTILITIES =====================================

def _get_font(size: int, bold: bool = False, mono: bool = False):
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


def split_assign() -> str:
    r, c = random.random(), 0.0
    for sp, ratio in SPLITS.items():
        c += ratio
        if r <= c:
            return sp
    return "train"


def gen_rib(corrupt: bool = False, wrong_code: bool = False) -> str:
    bc  = "99" if wrong_code else random.choice(
        ["03","08","10","11","12","14","16","17"])
    ag  = "".join(random.choices(string.digits, k=3))
    cpt = "".join(random.choices(string.digits, k=13))
    cle = "".join(random.choices(string.digits, k=2))
    rib = bc + ag + cpt + cle   # 20 digits
    if corrupt:
        rib = rib[:random.randint(9, 16)]   # truncated → fails validation
    return rib


def gen_cheque_num() -> str:
    return "".join(random.choices(string.digits, k=7))


def gen_cheque_reference(cheque_num: str) -> str:
    """reference = cheque_number + "1" + random_20_digits"""
    suffix = "1" + "".join(random.choices(string.digits, k=20))
    return cheque_num + suffix


def gen_amount() -> int:
    tier = random.choices([0, 1, 2, 3], weights=[0.28, 0.38, 0.24, 0.10])[0]
    if tier == 0: return random.randint(50,    499)
    if tier == 1: return random.randint(500,   9999)
    if tier == 2: return random.randint(10000, 49999)
    return           random.randint(50000, 200000)


_U = ["","un","deux","trois","quatre","cinq","six","sept","huit","neuf"]
_T = ["dix","onze","douze","treize","quatorze","quinze","seize",
      "dix-sept","dix-huit","dix-neuf"]
_D = ["","","vingt","trente","quarante","cinquante","soixante",
      "soixante-dix","quatre-vingt","quatre-vingt-dix"]

def _n2fr(n):
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

def amount_to_words(a: int) -> str:
    d = int(a)
    return _n2fr(d) + " dinar" + ("s" if d > 1 else "")


# ── Background ─────────────────────────────────────────────────────────────

def create_bg(bank: dict) -> Image.Image:
    tint = np.array(bank["bg_tint"], dtype=np.int16)
    tint = np.clip(tint + np.random.randint(-6, 7, 3), 200, 255).astype(np.uint8)
    base = np.full((H, W, 3), tint.tolist(), dtype=np.uint8)

    noise = np.random.normal(0, 1.2, (H, W, 3))
    base  = np.clip(base.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    grad  = np.linspace(0, 8, H).reshape(H, 1, 1)
    base  = np.clip(base.astype(np.float32) + grad, 0, 255).astype(np.uint8)

    for i in range(0, H, random.randint(18, 30)):
        if random.random() < 0.18:
            base[i, :] = np.clip(base[i, :].astype(np.int16) - 3, 0, 255)

    # Guilloché diagonal
    arr   = base.astype(np.float32)
    color = bank["color"]
    gc    = (
        float(color[0]) * 0.08 + 230.0,
        float(color[1]) * 0.08 + 230.0,
        float(color[2]) * 0.08 + 230.0,
    )
    for xi in range(0, W, 60):
        for yi in range(0, H, 60):
            if random.random() < 0.12:
                x2 = min(W - 1, xi + 40)
                y2 = min(H - 1, yi + 20)
                cv2.line(arr, (xi, yi), (x2, y2), gc, 1)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


# ── Scan effects (unchanged from original) ─────────────────────────────────

def apply_scan_effects(pil_img: Image.Image, intensity: str = "medium") -> Image.Image:
    arr = np.array(pil_img).astype(np.float32)
    h, w = arr.shape[:2]
    ranges = {"light": 0.6, "medium": 1.4, "heavy": 3.2}
    rng = ranges[intensity]

    angle = random.uniform(-rng, rng)
    M_rot = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    arr = cv2.warpAffine(arr, M_rot, (w, h), borderValue=(255, 255, 255))

    if random.random() < 0.28:
        s = {"light": 4, "medium": 9, "heavy": 20}[intensity]
        pts1 = np.float32([[0, 0], [w, 0], [0, h], [w, h]])
        pts2 = np.float32([
            [random.randint(0, s), random.randint(0, s)],
            [w - random.randint(0, s), random.randint(0, s)],
            [random.randint(0, s), h - random.randint(0, s)],
            [w - random.randint(0, s), h - random.randint(0, s)],
        ])
        Mp  = cv2.getPerspectiveTransform(pts1, pts2)
        arr = cv2.warpPerspective(arr, Mp, (w, h), borderValue=(255, 255, 255))

    alpha = random.uniform(0.90, 1.12)
    beta  = random.uniform(-10, 10)
    arr   = np.clip(arr * alpha + beta, 0, 255)

    blur_p = {"light": 0.05, "medium": 0.18, "heavy": 0.40}[intensity]
    if random.random() < blur_p:
        k = random.choice([3, 3, 5])
        arr = cv2.GaussianBlur(arr, (k, k), 0)

    sigma = {"light": 0.4, "medium": 1.0, "heavy": 2.2}[intensity]
    arr  += np.random.normal(0, sigma, arr.shape)

    if random.random() < 0.32:
        from io import BytesIO
        tmp = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        buf = BytesIO()
        tmp.save(buf, format="JPEG", quality=random.randint(60, 88))
        buf.seek(0)
        arr = np.array(Image.open(buf).copy()).astype(np.float32)

    if random.random() < 0.15:
        arr[:, :, 0] = np.clip(arr[:, :, 0] + random.uniform(0, 8), 0, 255)
        arr[:, :, 2] = np.clip(arr[:, :, 2] - random.uniform(0, 6), 0, 255)

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def save_img(img: Image.Image, label: str, index: int,
             bank_short: str = "", defect: str = "") -> None:
    sp     = split_assign()
    folder = os.path.join(BASE_DIR, sp, label)
    os.makedirs(folder, exist_ok=True)
    fname  = f"{label}_{index:05d}.jpg"
    fpath  = os.path.join(folder, fname)
    img.save(fpath, format="JPEG", quality=94, subsampling=0)
    META_ROWS.append({
        "split":  sp,
        "label":  label,
        "path":   os.path.join(sp, label, fname),
        "bank":   bank_short,
        "defect": defect,
    })


# ========================== CHEQUE BUILDER ================================

def _build_cheque_base(bank: dict, cfg: LayoutConfig) -> tuple:
    """Common setup: background, border, watermark. Returns (img, draw)."""
    color = bank["color"]
    img   = create_bg(bank)
    draw  = ImageDraw.Draw(img)

    # Double border
    draw.rectangle([(M, M), (W - M, H - M)], outline=color, width=3)
    draw.rectangle([(M + 4, M + 4), (W - M - 4, H - M - 4)], outline=color, width=1)

    # Pale watermark
    fn_wm = _get_font(80, bold=True)
    wm    = bank["short"]
    wb    = draw.textbbox((0, 0), wm, font=fn_wm)
    ww, wh = wb[2] - wb[0], wb[3] - wb[1]
    draw.text((W // 2 - ww // 2, H // 2 - wh // 2), wm,
              fill=(238, 238, 238), font=fn_wm)

    return img, draw


# ========================== VALID CHEQUE ==================================

def create_valid_cheque(index: int) -> None:
    bank   = random.choice(BANKS)
    cfg    = engine.generate()

    img, draw = _build_cheque_base(bank, cfg)

    # Cheque data
    amount      = gen_amount()
    rib         = gen_rib()
    cheque_num  = gen_cheque_num()
    expiry_date = datetime.now() + timedelta(days=random.randint(90, 1099))
    plafond     = random.randint(amount, amount + random.randint(5000, 50000))
    titulaire   = fake.name().upper()
    reference   = gen_cheque_reference(cheque_num)

    # ── QR payload (strict, validated) ────────────────────────────────────
    try:
        qr_payload = build_qr_payload(
            cheque_number=cheque_num,
            client_name=titulaire,
            plafond=plafond,
            expiry=expiry_date,
            rib=rib,
        )
    except ValueError as e:
        # Fallback: regenerate RIB to 20 digits
        rib = gen_rib()
        qr_payload = build_qr_payload(
            cheque_number=cheque_num,
            client_name=titulaire,
            plafond=plafond,
            expiry=expiry_date,
            rib=rib,
        )

    qr_img = make_qr_image(qr_payload, color=bank["color"], size=130)

    data = {
        "amount":            amount,
        "amount_words":      amount_to_words(amount),
        "rib":               rib,
        "cheque_num":        cheque_num,
        "cheque_reference":  reference,
        "expiry_date":       expiry_date,
        "plafond":           plafond,
        "beneficiaire":      "",   # handwritten on real cheque
        "titulaire":         "M. " + titulaire,
    }

    # ── Render zones ──────────────────────────────────────────────────────
    HeaderRenderer(img, draw, bank, cfg, engine).render(
        cheque_num=cheque_num,
        plafond=plafond,
        qr_img=qr_img,
    )

    BodyRenderer(draw, bank, cfg, engine, data).render()

    FooterRenderer(img, draw, bank, cfg, engine, data).render()

    img = apply_realism_effects(img)
    img = apply_scan_effects(img,
          intensity=random.choices(["light","medium","heavy"], weights=[0.30,0.55,0.15])[0])
    save_img(img, "valid", index, bank_short=bank["short"])


# ========================== INVALID CHEQUE ================================

def create_invalid_cheque(index: int) -> None:
    bank   = random.choice(BANKS)
    cfg    = engine.generate()
    defect = random.choice(INVALID_DEFECTS)

    img, draw = _build_cheque_base(bank, cfg)

    # ── Visibility flags ──────────────────────────────────────────────────
    if defect == "partial_missing":
        missing = set(random.sample(
            ["qr","logo","signature","rib","date","beneficiaire","micr"],
            k=random.randint(2, 4),
        ))
    else:
        missing = set()

    show_logo = "logo"      not in missing and defect != "missing_logo"
    show_qr   = "qr"        not in missing and defect != "missing_qr"
    show_sig  = "signature" not in missing and defect != "missing_signature"
    show_rib  = "rib"       not in missing and defect != "missing_rib"
    show_date = "date"      not in missing and defect != "missing_date"
    show_micr = "micr"      not in missing

    # ── Cheque data ────────────────────────────────────────────────────────
    if defect == "expired":
        expiry_date = datetime.now() - timedelta(days=random.randint(100, 1000))
    else:
        expiry_date = datetime.now() + timedelta(days=random.randint(30, 1099))

    amount      = gen_amount()
    wrong_amount= amount + random.randint(200, 8000)
    rib         = gen_rib(corrupt=(defect == "invalid_rib"),
                          wrong_code=(defect == "wrong_bank_code"))
    cheque_num  = gen_cheque_num()
    plafond     = random.randint(max(100, amount - 5000), amount + 20000)
    titulaire   = fake.name().upper()
    reference   = gen_cheque_reference(cheque_num)

    # ── QR: consistent or intentionally corrupted ─────────────────────────
    qr_corrupt_fields: list[str] = []
    is_qr_fraud = defect in (
        "qr_wrong_cheque_num", "qr_wrong_rib",
        "qr_wrong_plafond",    "qr_wrong_reference",
    )
    if is_qr_fraud:
        qr_corrupt_fields = [defect.replace("qr_wrong_", "")]

    rib_for_qr = rib if len(rib) == 20 else gen_rib()   # ensure 20 digits

    try:
        qr_payload = build_qr_payload(
            cheque_number=cheque_num,
            client_name=titulaire,
            plafond=plafond,
            expiry=expiry_date,
            rib=rib_for_qr,
            corrupt=is_qr_fraud,
            corrupt_fields=qr_corrupt_fields,
        )
        if show_qr and defect == "qr_corrupted":
            qr_img = make_corrupted_qr_image(size=130)
        elif show_qr:
            qr_img = make_qr_image(qr_payload, color=bank["color"], size=130)
        else:
            qr_img = Image.new("RGB", (130, 130), (255, 255, 255))
    except ValueError:
        qr_img = Image.new("RGB", (130, 130), (255, 255, 255))

    data = {
        "amount":           wrong_amount if defect == "amount_mismatch" else amount,
        "amount_words":     amount_to_words(wrong_amount if defect == "amount_mismatch"
                                            else amount),
        "rib":              rib,
        "cheque_num":       cheque_num,
        "cheque_reference": reference,
        "expiry_date":      expiry_date,
        "plafond":          plafond,
        "beneficiaire":     "",
        "titulaire":        "M. " + titulaire,
    }

    show_body = {
        "montant_line":    defect != "missing_amount",
        "montant_lettres": defect != "missing_amount",
        "ordre":           defect != "missing_beneficiaire",
    }
    show_footer = {
        "payable":   True,
        "signature": show_sig,
        "rib":       show_rib,
        "date":      show_date,
        "expiry":    show_date,
        "micr":      show_micr,
    }

    # ── Torn border override ───────────────────────────────────────────────
    if defect == "torn_border":
        # Already drew full border in base; erase some sides
        bg_color = bank["bg_tint"]
        sides = random.sample(["top","bottom","left","right"], k=random.randint(1, 2))
        thick = 5
        for side in sides:
            if side == "top":
                draw.rectangle([(M, M), (W - M, M + thick)], fill=bg_color)
            elif side == "bottom":
                draw.rectangle([(M, H - M - thick), (W - M, H - M)], fill=bg_color)
            elif side == "left":
                draw.rectangle([(M, M), (M + thick, H - M)], fill=bg_color)
            elif side == "right":
                draw.rectangle([(W - M - thick, M), (W - M, H - M)], fill=bg_color)

    # ── Render zones ──────────────────────────────────────────────────────
    HeaderRenderer(img, draw, bank, cfg, engine).render(
        cheque_num=cheque_num,
        plafond=plafond,
        qr_img=qr_img,
        show_logo=show_logo,
        show_qr=show_qr,
    )

    BodyRenderer(draw, bank, cfg, engine, data, show=show_body).render()

    FooterRenderer(img, draw, bank, cfg, engine, data, show=show_footer).render()

    # ── Special overlays ──────────────────────────────────────────────────
    if defect == "stamp_annule":
        fn_big = _get_font(90, bold=True)
        draw.text((W // 2, H // 2), "ANNULÉ", fill=(200, 0, 0),
                  font=fn_big, anchor="mm")
        draw.ellipse([(W // 2 - 200, H // 2 - 60), (W // 2 + 200, H // 2 + 60)],
                     outline=(200, 0, 0), width=5)

    elif defect == "overwritten":
        for _ in range(random.randint(4, 9)):
            x1 = random.randint(80, W - 150)
            y1 = random.randint(cfg.header_bb.y1 + 10, H - 50)
            draw.line([(x1, y1), (x1 + random.randint(60, 200),
                                   y1 + random.randint(-18, 18))],
                      fill=(15, 15, 15), width=random.randint(2, 4))

    elif defect == "ink_bleed":
        arr = np.array(img).astype(np.float32)
        for _ in range(random.randint(2, 5)):
            cx = random.randint(60, W - 60)
            cy = random.randint(cfg.header_bb.y1, H - 40)
            r  = random.randint(6, 22)
            cv2.circle(arr, (cx, cy), r, (15, 15, 80), -1)
            x1b, y1b = max(0, cx - r * 3), max(0, cy - r * 3)
            x2b, y2b = min(W, cx + r * 3), min(H, cy + r * 3)
            roi = arr[y1b:y2b, x1b:x2b]
            if roi.size:
                arr[y1b:y2b, x1b:x2b] = cv2.GaussianBlur(roi, (7, 7), 3)
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

    elif defect == "fake_color":
        col = tuple(random.randint(215, 255) for _ in range(3))
        ov  = Image.new("RGB", (W, H), col)
        img = Image.blend(img, ov, 0.20)

    img = apply_realism_effects(img)
    img = apply_scan_effects(img, intensity="heavy")
    save_img(img, "invalid", index, bank_short=bank["short"], defect=defect)


# ========================== NON-CHEQUES ===================================
# Non-cheque generation is kept from original (no layout engine used)
# Import selectively to avoid duplication

def _get_font_nc(size, bold=False):
    return _get_font(size, bold=bold)

def _tw(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]

def _th(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]

def _make_nc_qr(data, size=130, color=(0, 0, 0)):
    import qrcode as _qr
    try:
        qrc = _qr.QRCode(version=2, box_size=4, border=2)
        qrc.add_data(data)
        qrc.make(fit=True)
        q = qrc.make_image(fill_color=color, back_color="white")
        return q.convert("RGB").resize((size, size), Image.NEAREST)
    except Exception:
        return Image.new("RGB", (size, size), (240, 240, 240))

def _fmt_rib(rib: str) -> str:
    r = rib.replace(" ", "")
    if len(r) < 18:
        return r
    return f"{r[:2]} {r[2:5]} {r[5:18]} {r[18:]}"

NC_HDR_H = 190   # matches layout constant for header zone height in non-cheques


def create_non_cheque(index: int) -> None:
    doc_type = random.choice(NON_TYPES)
    use_bank = random.random() < 0.45
    bank     = random.choice(BANKS) if use_bank else None

    if bank:
        img       = create_bg(bank)
        doc_color = bank["color"]
    else:
        base = np.full((H, W, 3), 255, dtype=np.uint8)
        base = np.clip(base + np.random.normal(0, 0.8, base.shape).astype(np.int16),
                       0, 255).astype(np.uint8)
        img       = Image.fromarray(base)
        doc_color = tuple(random.randint(0, 80) for _ in range(3))

    draw = ImageDraw.Draw(img)

    fn_lg  = _get_font_nc(20, bold=True)
    fn_md  = _get_font_nc(14)
    fn_sm  = _get_font_nc(12)
    fn_mic = _get_font_nc(9)

    # Optional bank logo
    if bank and random.random() < 0.55:
        path = bank.get("logo", "")
        if os.path.exists(path):
            logo = Image.open(path).convert("RGBA")
            nw, nh = int(logo.width * 0.5), int(logo.height * 0.5)
            logo = logo.resize((nw, nh), Image.LANCZOS)
            img.paste(logo, (M + 8, 20), logo)

    if random.random() < 0.35:
        draw.rectangle([(M, M), (W - M, H - M)], outline=doc_color, width=2)

    y = NC_HDR_H + 10

    if doc_type == "facture_eau":
        ref_s  = random.randint(100000, 999999)
        abonne = fake.name()
        conso  = random.randint(5, 80)
        mt     = conso * random.uniform(0.5, 1.2)
        ttc    = mt * 1.07
        has_qr = random.random() < 0.60

        draw.text((W // 2, y), "SONEDE – SOCIÉTÉ NATIONALE DES EAUX",
                  fill=(0, 60, 120), font=fn_lg, anchor="mt"); y += 26
        draw.text((W // 2, y), "FACTURE D'EAU",
                  fill=(0, 60, 120), font=fn_sm, anchor="mt"); y += 24
        draw.line([(M, y), (W - M, y)], fill=(0, 60, 120), width=2); y += 10

        draw.text((M + 5, y), f"Abonné       : {abonne}",          fill=(20, 20, 20), font=fn_sm); y += 20
        draw.text((M + 5, y), f"Réf          : {ref_s}",            fill=(20, 20, 20), font=fn_sm); y += 20
        draw.text((M + 5, y), f"Consommation : {conso} m³",         fill=(20, 20, 20), font=fn_sm); y += 20
        draw.text((M + 5, y), f"Montant HT   : {mt:.3f} TND",       fill=(20, 20, 20), font=fn_sm); y += 20
        draw.text((M + 5, y), f"TVA 7%       : {mt * 0.07:.3f} TND",fill=(20, 20, 20), font=fn_sm); y += 20
        draw.text((M + 5, y), f"TOTAL TTC    : {ttc:.3f} TND",      fill=(0, 60, 120), font=fn_md); y += 28
        if has_qr:
            qr_s = _make_nc_qr(f"SONEDE|{ref_s}|{ttc:.3f}", size=140, color=(0, 60, 120))
            img.paste(qr_s, (W - 165, NC_HDR_H + 18))
            draw.text((W - 165, NC_HDR_H + 18 + 143), "Scan pour payer",
                      fill=(80, 80, 80), font=fn_mic)
        for bi in range(0, min(400, W - M * 2 - 20), 3):
            bw = random.choice([1, 2, 2, 3])
            draw.rectangle([(M + 5 + bi, y), (M + 5 + bi + bw, y + 28)], fill=(0, 0, 0))

    elif doc_type == "facture_electricite":
        ref_steg = random.randint(1000000, 9999999)
        kwh      = random.randint(80, 800)
        mt       = kwh * random.uniform(0.12, 0.25)
        ttc      = mt * 1.19
        has_qr   = random.random() < 0.55

        draw.text((W // 2, y), "STEG – SOCIÉTÉ TUNISIENNE DE L'ÉLECTRICITÉ ET DU GAZ",
                  fill=(0, 60, 120), font=fn_sm, anchor="mt"); y += 22
        draw.text((W // 2, y), "AVIS D'ÉCHÉANCE",
                  fill=(0, 60, 120), font=fn_lg, anchor="mt"); y += 28
        draw.line([(M, y), (W - M, y)], fill=(0, 60, 120), width=2); y += 10

        draw.text((M + 5, y), f"Client       : {fake.name()}",     fill=(20, 20, 20), font=fn_sm); y += 20
        draw.text((M + 5, y), f"Réf          : {ref_steg}",        fill=(20, 20, 20), font=fn_sm); y += 20
        draw.text((M + 5, y), f"Consommation : {kwh} kWh",         fill=(20, 20, 20), font=fn_sm); y += 20
        draw.text((M + 5, y), f"TOTAL TTC    : {ttc:.3f} TND",     fill=(0, 60, 120), font=fn_md)
        if has_qr:
            qr_s = _make_nc_qr(f"STEG|{ref_steg}|{kwh}kWh|{ttc:.3f}", size=140, color=(0, 60, 120))
            img.paste(qr_s, (W - 165, NC_HDR_H + 18))

    elif doc_type == "facture_telecom":
        op      = random.choice(["OOREDOO TUNISIE","ORANGE TUNISIE","TELECOM TUNISIE"])
        opc     = random.choice([(220, 50, 50), (255, 100, 0), (0, 70, 150)])
        ref_tel = random.randint(100000, 999999)
        mt      = random.uniform(20, 150)
        has_qr  = random.random() < 0.65

        draw.text((W // 2, y), op, fill=opc, font=fn_lg, anchor="mt"); y += 28
        draw.text((W // 2, y), "FACTURE MENSUELLE", fill=(60, 60, 60), font=fn_sm, anchor="mt"); y += 26
        draw.line([(M, y), (W - M, y)], fill=opc, width=2); y += 10
        draw.text((M + 5, y), f"TOTAL TTC : {mt:.3f} TND", fill=opc, font=fn_md)
        if has_qr:
            qr_s = _make_nc_qr(f"{op}|{ref_tel}|{mt:.3f}", size=140, color=opc)
            img.paste(qr_s, (W - 165, NC_HDR_H + 18))

    elif doc_type == "recu_bancaire":
        bname  = bank["name"] if bank else "Banque"
        ref_rc = random.randint(10000, 99999)
        mt     = gen_amount()

        draw.text((W // 2, y), "REÇU BANCAIRE", fill=doc_color, font=fn_lg, anchor="mt"); y += 28
        draw.text((W // 2, y), bname, fill=doc_color, font=fn_sm, anchor="mt"); y += 24
        draw.line([(M, y), (W - M, y)], fill=doc_color, width=2); y += 10
        draw.text((M + 5, y), f"Réf     : {ref_rc}",           fill=(20, 20, 20), font=fn_sm); y += 20
        draw.text((M + 5, y), f"Montant : {mt:,.0f} TND".replace(",", " "),
                  fill=doc_color, font=fn_md)

    elif doc_type == "releve_compte":
        rib_rel  = gen_rib()
        balance  = random.randint(500, 50000)

        draw.text((W // 2, y), "RELEVÉ DE COMPTE", fill=doc_color, font=fn_lg, anchor="mt"); y += 28
        draw.line([(M, y), (W - M, y)], fill=doc_color, width=2); y += 8
        draw.text((M + 5, y), f"Titulaire : {fake.name()}", fill=(20, 20, 20), font=fn_sm); y += 20
        draw.text((M + 5, y), f"RIB       : {_fmt_rib(rib_rel)}", fill=(20, 20, 20), font=fn_sm); y += 20
        draw.text((M + 5, y), f"Solde     : {balance:,} TND".replace(",", " "),
                  fill=doc_color, font=fn_md)
        qr_s = _make_nc_qr(f"RELEVE|{rib_rel}|{balance}", size=120, color=(40, 40, 120))
        img.paste(qr_s, (W - 150, NC_HDR_H + 14))

    elif doc_type == "ordre_virement":
        rib_don = gen_rib()
        rib_ben = gen_rib()
        mt      = random.randint(200, 30000)
        ref_ov  = random.randint(10000, 99999)

        draw.text((W // 2, y), "ORDRE DE VIREMENT BANCAIRE",
                  fill=doc_color, font=fn_lg, anchor="mt"); y += 28
        draw.line([(M, y), (W - M, y)], fill=doc_color, width=2); y += 10
        draw.text((M + 5, y), f"Réf. opération   : {ref_ov}",    fill=(20, 20, 20), font=fn_sm); y += 22
        draw.text((M + 5, y), f"Montant          : {mt:,.0f} TND".replace(",", " "),
                  fill=doc_color, font=fn_md)

        qr_payload = f"VIREMENT|REF:{ref_ov}|DON:{rib_don}|BEN:{rib_ben}|MT:{mt}|TND"
        qr_s = _make_nc_qr(qr_payload, size=165, color=(30, 30, 100))
        img.paste(qr_s, (W - M - 175, NC_HDR_H + 14))

    elif doc_type == "attestation_bancaire":
        draw.text((W // 2, y), "ATTESTATION BANCAIRE", fill=doc_color, font=fn_lg, anchor="mt"); y += 30
        draw.line([(M, y), (W - M, y)], fill=doc_color, width=1); y += 8
        who = bank["name"] if bank else "la Banque"
        draw.text((M + 5, y), f"Nous soussignés, {who}, attestons que:", fill=(30, 30, 30), font=fn_sm); y += 20
        for _ in range(random.randint(3, 6)):
            draw.text((M + 5, y), fake.sentence()[:75], fill=(40, 40, 40), font=fn_mic); y += 14

    else:  # bon_commande
        draw.text((W // 2, y), "BON DE COMMANDE", fill=doc_color, font=fn_lg, anchor="mt"); y += 30
        draw.line([(M, y), (W - M, y)], fill=doc_color, width=1); y += 8
        draw.text((M + 5, y), fake.company(), fill=(20, 20, 20), font=fn_md); y += 26
        total = 0
        for _ in range(random.randint(4, 9)):
            p = random.randint(30, 2000)
            total += p
            draw.text((M + 5, y), fake.catch_phrase()[:50], fill=(40, 40, 40), font=fn_mic)
            ps = f"{p} TND"
            pw = _tw(draw, ps, fn_mic)
            draw.text((W - M - pw - 5, y), ps, fill=(40, 40, 40), font=fn_mic); y += 16
        ts  = f"TOTAL: {total:,.0f} TND".replace(",", " ")
        tsw = _tw(draw, ts, fn_md)
        draw.text((W - M - tsw - 5, y), ts, fill=doc_color, font=fn_md)

    # Bottom microtexte
    draw.text((M + 5, H - M - 14),
              "".join(random.choices(string.ascii_uppercase + string.digits, k=60)),
              fill=(200, 200, 200), font=fn_mic)

    img = apply_realism_effects(img)
    img = apply_scan_effects(img, intensity=random.choice(["light","medium"]))
    save_img(img, "non_cheque", index,
             bank_short=bank["short"] if bank else "")


# ========================== METADATA =====================================

def save_metadata() -> None:
    os.makedirs(BASE_DIR, exist_ok=True)
    fpath = os.path.join(BASE_DIR, "metadata.csv")
    with open(fpath, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["split","label","path","bank","defect"])
        w.writeheader()
        w.writerows(META_ROWS)
    print(f"\n  📋 Metadata: {fpath}  ({len(META_ROWS)} entrées)")


# ========================== MAIN =========================================

def generate() -> None:
    print("=" * 70)
    print("  BCT CHEQUE DATASET – REFACTORED V2")
    print(f"     Dynamic Layout  |  {W}×{H} px  |  3 classes")
    print(f"     QR: https://pecc.tn/1/... (validated)")
    print("=" * 70)
    print(f"   Valid       : {VALID_COUNT}")
    print(f"   Invalid     : {INVALID_COUNT}  ({len(INVALID_DEFECTS)} défauts)")
    print(f"   Non-chèque  : {NON_COUNT}  ({len(NON_TYPES)} types)")
    print(f"   Banques     : {len(BANKS)}")
    print(f"   Layout variants : 8 header compositions")
    print("=" * 70)

    for sp in SPLITS:
        for lbl in ["valid","invalid","non_cheque"]:
            os.makedirs(os.path.join(BASE_DIR, sp, lbl), exist_ok=True)

    print("\n  Chèques VALIDES ...")
    for i in tqdm(range(VALID_COUNT), desc="  Valid", ncols=65):
        create_valid_cheque(i)

    print("\n  Chèques INVALIDES ...")
    for i in tqdm(range(INVALID_COUNT), desc="  Invalid", ncols=65):
        create_invalid_cheque(i)

    print("\n  NON-CHÈQUES ...")
    for i in tqdm(range(NON_COUNT), desc="  Non-chèque", ncols=65):
        create_non_cheque(i)

    save_metadata()

    print("\n" + "=" * 70)
    for sp, ratio in SPLITS.items():
        vc = int(VALID_COUNT * ratio)
        ic = int(INVALID_COUNT * ratio)
        nc = int(NON_COUNT * ratio)
        print(f"   {sp.upper():5s}: {vc:4d} valid / {ic:3d} invalid / {nc:3d} non-chèque")
    total = VALID_COUNT + INVALID_COUNT + NON_COUNT
    print(f"\n   Total : {total} images → {BASE_DIR}/")
    print("\n   EfficientNet-B0 :")
    print("     resize(224,224) | mean=[.485,.456,.406] | std=[.229,.224,.225]")
    print("     batch=32 | lr=1e-4 | ReduceLROnPlateau | warmup 3 epochs")
    print("=" * 70)


if __name__ == "__main__":
    generate()