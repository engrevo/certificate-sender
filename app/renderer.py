"""Load certificate templates and draw names on them (Arabic + Latin)."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, features

from .detector import Box

FONTS_DIR = Path(__file__).parent / "fonts"
FONTS = {
    "amiri-bold": ("Amiri — نسخ عريض", "Amiri-Bold.ttf"),
    "amiri": ("Amiri — نسخ", "Amiri-Regular.ttf"),
    "tajawal-bold": ("Tajawal — حديث عريض", "Tajawal-Bold.ttf"),
    "tajawal": ("Tajawal — حديث", "Tajawal-Medium.ttf"),
}
DEFAULT_FONT = "amiri-bold"
PDF_DPI = 200
MAX_TEMPLATE_SIDE = 3508  # A4 at 300 dpi

_HAS_RAQM = features.check("raqm")


def load_template(data: bytes, filename: str) -> Image.Image:
    """Open a PNG/JPG/WEBP or the first page of a PDF as an RGB image."""
    if filename.lower().endswith(".pdf") or data[:4] == b"%PDF":
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as doc:
            page = doc[0]
            pix = page.get_pixmap(dpi=PDF_DPI, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    else:
        img = Image.open(io.BytesIO(data))
        img.load()
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img, mask=img.split()[-1])
            img = bg
        img = img.convert("RGB")
    if max(img.size) > MAX_TEMPLATE_SIDE:
        img.thumbnail((MAX_TEMPLATE_SIDE, MAX_TEMPLATE_SIDE), Image.LANCZOS)
    return img


def _font(key: str, size: int) -> ImageFont.FreeTypeFont:
    _, file = FONTS.get(key, FONTS[DEFAULT_FONT])
    engine = ImageFont.Layout.RAQM if _HAS_RAQM else ImageFont.Layout.BASIC
    return ImageFont.truetype(str(FONTS_DIR / file), size, layout_engine=engine)


def _shape(text: str) -> str:
    """Without libraqm, reshape Arabic letters and reorder for display."""
    if _HAS_RAQM:
        return text
    import arabic_reshaper
    from bidi.algorithm import get_display

    return get_display(arabic_reshaper.reshape(text))


def _background_color(arr: np.ndarray, box: Box) -> tuple[int, int, int]:
    """Median colour of a thin ring just outside the box."""
    H, W = arr.shape[:2]
    m = 4
    x0, y0 = max(0, box.x - m), max(0, box.y - m)
    x1, y1 = min(W, box.x + box.w + m), min(H, box.y + box.h + m)
    ring = np.concatenate([
        arr[y0:box.y, x0:x1].reshape(-1, 3),
        arr[box.y + box.h:y1, x0:x1].reshape(-1, 3),
        arr[y0:y1, x0:box.x].reshape(-1, 3),
        arr[y0:y1, box.x + box.w:x1].reshape(-1, 3),
    ])
    if ring.size == 0:
        return (255, 255, 255)
    return tuple(int(v) for v in np.median(ring, axis=0))


def erase_regions(img: Image.Image, boxes: list[Box]) -> Image.Image:
    out = img.copy()  # never draw on the shared template
    if not boxes:
        return out
    arr = np.array(out)
    draw = ImageDraw.Draw(out)
    for b in boxes:
        draw.rectangle([b.x, b.y, b.x + b.w, b.y + b.h], fill=_background_color(arr, b))
    return out


def fit_font(text: str, box: Box, font_key: str, max_size: int | None = None) -> ImageFont.FreeTypeFont:
    shaped = _shape(text)
    size = int(max_size or box.h * 0.9)
    size = max(size, 8)
    while size > 8:
        font = _font(font_key, size)
        l, t, r, b = font.getbbox(shaped)
        if (r - l) <= box.w * 0.96 and (b - t) <= box.h * 0.98:
            return font
        size = int(size * 0.94)
    return _font(font_key, 8)


def render_certificate(
    template: Image.Image,
    name: str,
    box: Box,
    font_key: str = DEFAULT_FONT,
    color: str = "#1a1a1a",
    erase: list[Box] | None = None,
    max_size: int | None = None,
    align: str = "center",
) -> Image.Image:
    img = erase_regions(template, erase or [])
    name = " ".join(name.split())
    if not name:
        return img
    font = fit_font(name, box, font_key, max_size)
    shaped = _shape(name)
    draw = ImageDraw.Draw(img)
    l, t, r, b = font.getbbox(shaped)
    tw, th = r - l, b - t
    if align == "right":
        x = box.x + box.w - tw - l
    elif align == "left":
        x = box.x - l
    else:
        x = box.x + (box.w - tw) / 2 - l
    y = box.y + (box.h - th) / 2 - t
    draw.text((x, y), shaped, font=font, fill=color)
    return img


def to_pdf_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=PDF_DPI, quality=92)
    return buf.getvalue()


def to_png_bytes(img: Image.Image, max_side: int | None = None) -> bytes:
    if max_side and max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
