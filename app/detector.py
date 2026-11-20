"""Automatic detection of where the attendee name belongs on a certificate.

Strategies, in priority order:
1. placeholder  - an explicit placeholder written by the organizer, e.g. {name},
                  <<name>>, [الاسم], NAME, or a stand-alone "الاسم" on its own line.
                  The placeholder is erased before the real name is drawn.
2. line         - a horizontal line (solid, dashed or dotted) near the middle of the
                  page that the name is written above.
3. whitespace   - the largest empty horizontal band in the central part of the page.
4. center       - last resort: the middle of the page.

Every result is a suggestion: the web UI lets the organizer drag/resize the box.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

import cv2
import numpy as np
from PIL import Image

try:
    import pytesseract
except ImportError:  # OCR is optional; the other strategies still work.
    pytesseract = None


@dataclass
class Box:
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


@dataclass
class Detection:
    box: Box
    method: str
    confidence: float
    color: str = "#1a1a1a"
    erase: list[Box] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


OCR_MAX_SIDE = 1500
OCR_TIMEOUT = 30  # seconds

# --------------------------------------------------------------------------- helpers

_ARABIC_DIACRITICS = re.compile(r"[ً-ْـ]")  # harakat + tatweel
# Tokens that are unambiguously placeholders when wrapped in brackets/braces.
_PLACEHOLDER_WORDS = {
    "name", "fullname", "full_name", "participant", "participant_name", "attendee",
    "الاسم", "اسم", "اسم_المشارك", "المشارك", "اسمالمشارك", "اسم_الحضور",
}
_BRACKETED = re.compile(r"^[\[\{<«(]{1,2}\s*([^\]\}>»)]+?)\s*[\]\}>»)]{1,2}$")


def _normalize_token(text: str) -> str:
    t = _ARABIC_DIACRITICS.sub("", text).strip().lower()
    return t


def is_placeholder_text(text: str, alone_on_line: bool) -> bool:
    """Decide whether an OCR'd token (or joined line) is a name placeholder."""
    t = _normalize_token(text)
    if not t:
        return False
    m = _BRACKETED.match(t)
    if m:
        inner = re.sub(r"[\s\-]+", "_", m.group(1).strip())
        return inner in _PLACEHOLDER_WORDS or inner.replace("_", "") in _PLACEHOLDER_WORDS
    bare = t.strip(" :.-_")
    if bare in {"name", "full name", "participant name"} and text.strip().isupper():
        return True  # "NAME" written in capitals as a placeholder
    if alone_on_line and bare in {"الاسم", "اسم المشارك", "اسم المشاركة", "[الاسم]"}:
        return True
    return False


def dominant_ink_color(rgb: np.ndarray) -> str:
    """Median color of the darkest pixels in a region, as #rrggbb."""
    if rgb.size == 0:
        return "#1a1a1a"
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if gray.max() - gray.min() < 40:
        return "#1a1a1a"
    ink = _ink_mask(gray) > 0
    if ink.sum() < 20:
        return "#1a1a1a"
    # Among ink pixels, keep the darker half (anti-aliased edges are lighter).
    mask = ink & (gray <= np.median(gray[ink]))
    r, g, b = (int(np.median(rgb[..., c][mask])) for c in range(3))
    return f"#{r:02x}{g:02x}{b:02x}"


def _ink_mask(gray: np.ndarray) -> np.ndarray:
    """Binary mask (255 = ink) robust to coloured / textured backgrounds."""
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    adaptive = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15
    )
    return cv2.bitwise_and(otsu, adaptive)


def _default_box_height(H: int) -> int:
    return int(H * 0.085)


# --------------------------------------------------------------------------- strategy 1

def detect_placeholder(img: Image.Image) -> Detection | None:
    if pytesseract is None:
        return None
    rgb = np.array(img.convert("RGB"))
    H, W = rgb.shape[:2]
    # OCR only the body of the page (skip outer frame/header/footer margins) on a
    # small grayscale copy: this is by far the slowest step, so keep it light.
    y0c, y1c = int(H * 0.12), int(H * 0.92)
    x0c, x1c = int(W * 0.04), int(W * 0.96)
    crop = cv2.cvtColor(rgb[y0c:y1c, x0c:x1c], cv2.COLOR_RGB2GRAY)
    scale = min(1.0, OCR_MAX_SIDE / max(crop.shape))
    small = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else crop
    data = None
    for lang in ("ara+eng", None):
        try:
            kw = {"lang": lang} if lang else {}
            data = pytesseract.image_to_data(small, config="--psm 11", output_type=pytesseract.Output.DICT,
                                             timeout=OCR_TIMEOUT, **kw)
            break
        except Exception:
            continue
    if data is None:
        return None

    words = []
    for i, text in enumerate(data["text"]):
        if not text or not text.strip():
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        words.append({
            "text": text.strip(), "conf": conf,
            "x": data["left"][i] / scale + x0c, "y": data["top"][i] / scale + y0c,
            "w": data["width"][i] / scale, "h": data["height"][i] / scale,
        })

    # Group words that sit on the same visual row.
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda d: d["y"] + d["h"] / 2):
        cy = w["y"] + w["h"] / 2
        for row in rows:
            ref = row[0]
            if abs((ref["y"] + ref["h"] / 2) - cy) < max(ref["h"], w["h"]) * 0.6:
                row.append(w)
                break
        else:
            rows.append([w])

    candidates = []
    for row in rows:
        row.sort(key=lambda d: d["x"])
        joined = " ".join(w["text"] for w in row)
        compact = "".join(w["text"] for w in row)
        alone = len(row) <= 3
        hits = [w for w in row if is_placeholder_text(w["text"], alone_on_line=len(row) == 1)]
        if not hits and (is_placeholder_text(joined, alone) or is_placeholder_text(compact, alone)):
            hits = row
        if hits:
            x0 = min(w["x"] for w in hits); y0 = min(w["y"] for w in hits)
            x1 = max(w["x"] + w["w"] for w in hits); y1 = max(w["y"] + w["h"] for w in hits)
            candidates.append((x0, y0, x1, y1))

    if not candidates:
        return None
    # Prefer the candidate closest to the page centre.
    x0, y0, x1, y1 = min(candidates, key=lambda c: abs((c[1] + c[3]) / 2 - H / 2))
    ph = y1 - y0
    pad = max(4, int(ph * 0.25))
    erase = Box(int(x0 - pad), int(y0 - pad), int(x1 - x0 + 2 * pad), int(ph + 2 * pad)).clip(W, H)

    cx = (x0 + x1) / 2
    box_h = max(int(ph * 1.9), int(H * 0.06))
    box_w = max(int((x1 - x0) * 3), int(W * 0.55))
    box_w = int(min(box_w, 2 * min(cx, W - cx)))  # keep it centred on the placeholder
    box = Box(int(cx - box_w / 2), int((y0 + y1) / 2 - box_h / 2), box_w, box_h).clip(W, H)

    color = dominant_ink_color(rgb[int(y0):int(y1), int(x0):int(x1)])
    return Detection(box=box, method="placeholder", confidence=0.95, color=color, erase=[erase])


# --------------------------------------------------------------------------- strategy 2

def find_horizontal_lines(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Return (x, y, w, h) of thin horizontal lines, including dashed/dotted ones."""
    H, W = gray.shape
    ink = _ink_mask(gray)
    # Join dots/dashes horizontally, then keep only long horizontal runs.
    join = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, W // 90), 1)))
    long_k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, W // 9), 1))
    lines = cv2.morphologyEx(join, cv2.MORPH_OPEN, long_k)
    n, _, stats, _ = cv2.connectedComponentsWithStats(lines, connectivity=8)
    out = []
    max_thick = max(8, int(H * 0.012))
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if h > max_thick or w < W * 0.15:
            continue
        # A real line is "thin everywhere": check that the ink above and below
        # the line is mostly absent (otherwise it's the base of a text row).
        out.append((int(x), int(y), int(w), int(h)))
    return out


def detect_line(img: Image.Image) -> Detection | None:
    rgb = np.array(img.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    H, W = gray.shape
    ink = _ink_mask(gray)
    best, best_score = None, -1e9
    for x, y, w, h in find_horizontal_lines(gray):
        cx, cy = x + w / 2, y + h / 2
        # Ignore frame/border lines and full-width decorative rules.
        # Signature lines sit to the sides; the name line is horizontally centred.
        if abs(cx - W / 2) > W * 0.12:
            continue
        if w > W * 0.88 or cy < H * 0.12 or cy > H * 0.9 or x < W * 0.03 or x + w > W * 0.97:
            continue
        box_h = _default_box_height(H)
        above = ink[max(0, y - box_h):max(0, y - 3), x:x + w]
        emptiness = 1.0 - (above.mean() / 255 if above.size else 1.0)
        if emptiness < 0.9:
            continue  # text already sits on this line -> it's an underline, not a blank
        centered = 1 - abs(cx - W / 2) / (W / 2)
        vertical = 1 - abs(cy - H * 0.52) / (H / 2)
        score = 2.0 * centered + 1.5 * vertical + 1.0 * (w / W) + 2.0 * emptiness
        if score > best_score:
            best, best_score = (x, y, w, h), score
    if best is None:
        return None
    x, y, w, h = best
    box_h = _default_box_height(H)
    # Shrink the box if other content sits close above the line.
    col = ink[:max(0, y - 3), x:x + w].any(axis=1)
    rows_with_ink = np.where(col)[0]
    top_limit = rows_with_ink[rows_with_ink < y - 3].max() + 1 if rows_with_ink.size else 0
    top = max(top_limit + int(H * 0.008), y - box_h)
    box = Box(x, int(top), w, int(y - top - max(2, H * 0.004))).clip(W, H)
    if box.h < H * 0.025:
        return None
    color = dominant_ink_color(rgb[int(H * 0.15):int(H * 0.85), int(W * 0.15):int(W * 0.85)])
    return Detection(box=box, method="line", confidence=0.8, color=color)


# --------------------------------------------------------------------------- strategy 3

def detect_whitespace(img: Image.Image) -> Detection | None:
    rgb = np.array(img.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    H, W = gray.shape
    ink = _ink_mask(gray)
    band = ink[:, int(W * 0.15):int(W * 0.85)]
    has_ink = (band > 0).mean(axis=1) > 0.002
    y_lo, y_hi = int(H * 0.22), int(H * 0.78)
    best = (0, 0)
    start = None
    for yy in range(y_lo, y_hi + 1):
        empty = yy < y_hi and not has_ink[yy]
        if empty and start is None:
            start = yy
        elif not empty and start is not None:
            if yy - start > best[1] - best[0]:
                best = (start, yy)
            start = None
    gap = best[1] - best[0]
    if gap < H * 0.05:
        return None
    box_h = int(min(gap * 0.7, H * 0.1))
    cy = (best[0] + best[1]) // 2
    box = Box(int(W * 0.2), cy - box_h // 2, int(W * 0.6), box_h).clip(W, H)
    color = dominant_ink_color(rgb[int(H * 0.15):int(H * 0.85), int(W * 0.15):int(W * 0.85)])
    return Detection(box=box, method="whitespace", confidence=0.5, color=color)


# --------------------------------------------------------------------------- public

def detect_name_box(img: Image.Image) -> Detection:
    for strategy in (detect_placeholder, detect_line, detect_whitespace):
        try:
            result = strategy(img)
        except Exception:
            result = None
        if result is not None:
            return result
    W, H = img.size
    return Detection(
        box=Box(int(W * 0.2), int(H * 0.45), int(W * 0.6), _default_box_height(H)),
        method="center", confidence=0.2,
    )
