"""Generate demo certificate templates used by the tests and the README.

    python samples/make_samples.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
FONTS = ROOT.parent / "app" / "fonts"
W, H = 2000, 1414  # A4 landscape ratio


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size, layout_engine=ImageFont.Layout.RAQM)


def frame(bg="#fbf8f1", accent="#1f4e79"):
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, W - 40, H - 40], outline=accent, width=14)
    d.rectangle([70, 70, W - 70, H - 70], outline="#c9a227", width=4)
    return img, d


def centered(d, y, text, f, fill):
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    d.text(((W - (r - l)) / 2 - l, y), text, font=f, fill=fill)


def header(d, accent):
    centered(d, 170, "شهادة حضور", font("Amiri-Bold.ttf", 120), accent)
    centered(d, 380, "يشهد نادي الذكاء الاصطناعي بأن", font("Amiri-Regular.ttf", 56), "#333333")


def footer(d):
    centered(d, 860, "قد حضر ورشة «برمجة الدرونز والتحكم بها»", font("Amiri-Regular.ttf", 56), "#333333")
    centered(d, 950, "المقامة بتاريخ ٢٠ نوفمبر ٢٠٢٦م", font("Amiri-Regular.ttf", 48), "#555555")
    d.line([250, 1200, 650, 1200], fill="#333333", width=3)
    d.line([W - 650, 1200, W - 250, 1200], fill="#333333", width=3)
    f = font("Tajawal-Medium.ttf", 36)
    d.text((350, 1215), "رئيس النادي", font=f, fill="#333333")
    d.text((W - 560, 1215), "منسق الفعالية", font=f, fill="#333333")


def placeholder():
    img, d = frame()
    header(d, "#1f4e79")
    centered(d, 590, "{الاسم}", font("Tajawal-Bold.ttf", 90), "#7a1f1f")
    footer(d)
    return img


def placeholder_en():
    img, d = frame("#ffffff", "#0b6e4f")
    centered(d, 170, "CERTIFICATE OF ATTENDANCE", font("Tajawal-Bold.ttf", 96), "#0b6e4f")
    centered(d, 380, "This is to certify that", font("Amiri-Regular.ttf", 56), "#333333")
    centered(d, 590, "{{name}}", font("Tajawal-Bold.ttf", 90), "#0b6e4f")
    centered(d, 860, "attended the Drone Programming Workshop", font("Amiri-Regular.ttf", 56), "#333333")
    return img


def line():
    img, d = frame()
    header(d, "#1f4e79")
    d.line([500, 760, W - 500, 760], fill="#555555", width=3)
    footer(d)
    return img


def dotted():
    img, d = frame("#f4f7fb", "#5b2a86")
    header(d, "#5b2a86")
    for x in range(520, W - 520, 18):
        d.ellipse([x, 754, x + 6, 760], fill="#666666")
    footer(d)
    return img


def blank():
    img, d = frame()
    header(d, "#1f4e79")
    footer(d)
    return img


SAMPLES = {
    "placeholder_ar.png": placeholder,
    "placeholder_en.png": placeholder_en,
    "line.png": line,
    "dotted.png": dotted,
    "blank.png": blank,
}

if __name__ == "__main__":
    for name, fn in SAMPLES.items():
        fn().save(ROOT / name)
        print("wrote", name)
