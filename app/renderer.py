"""Load certificate templates and draw names on them (Arabic + Latin)."""
from __future__ import annotations

import io
import time
from functools import lru_cache
from pathlib import Path

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont, features

FONTS_DIR = Path(__file__).parent / "fonts"
FONTS = {
    "amiri-bold": ("Amiri — نسخ عريض", "Amiri-Bold.ttf"),
    "amiri": ("Amiri — نسخ", "Amiri-Regular.ttf"),
    "tajawal-bold": ("Tajawal — حديث عريض", "Tajawal-Bold.ttf"),
    "tajawal": ("Tajawal — حديث", "Tajawal-Medium.ttf"),
}
DEFAULT_FONT = "amiri-bold"
PDF_DPI = 200
MAX_TEMPLATE_SIDE = 2480  # A4 long side at ~210 dpi: sharp in print, much faster
VIEW_MAX_SIDE = 1600     # lightweight copy shown in the browser

_HAS_RAQM = features.check("raqm")


@dataclass
class Box:
    """Where the name goes, in template pixels."""
    x: int
    y: int
    w: int
    h: int

    def clip(self, width: int, height: int) -> "Box":
        x = max(0, min(self.x, width - 1))
        y = max(0, min(self.y, height - 1))
        w = max(1, min(self.w, width - x))
        h = max(1, min(self.h, height - y))
        return Box(x, y, w, h)


def default_box(width: int, height: int) -> Box:
    """Starting position the organizer then drags into place: centred, a bit above the middle."""
    w, h = int(width * 0.6), int(height * 0.085)
    return Box((width - w) // 2, int(height * 0.45), w, h)


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


@lru_cache(maxsize=256)
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


def view_jpeg(img: Image.Image) -> bytes:
    small = img.copy()
    small.thumbnail((VIEW_MAX_SIDE, VIEW_MAX_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


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
    max_size: int | None = None,
    align: str = "center",
) -> Image.Image:
    img = template.copy()  # never draw on the shared template
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


def to_pdf_bytes(img: Image.Image, name: str = "") -> bytes:
    """PDF of the certificate. The attendee's name and issue date go in the PDF metadata,
    so a stored file describes itself (the download page reads them back)."""
    buf = io.BytesIO()
    meta = {"creator": "مِرسال", "subject": "شهادة حضور", "creationDate": time.gmtime()}
    if name:
        meta["title"] = name
    img.save(buf, format="PDF", resolution=PDF_DPI, quality=92, **meta)
    return buf.getvalue()


def to_png_bytes(img: Image.Image, max_side: int | None = None) -> bytes:
    if max_side and max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)
    return buf.getvalue()
