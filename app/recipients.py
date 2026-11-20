"""Parse attendee lists (CSV / Excel / pasted text) and normalise phone numbers."""
from __future__ import annotations

import csv
import io
import os
import re

_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
NAME_HEADERS = {"name", "full name", "fullname", "الاسم", "اسم", "الاسم الكامل", "اسم المشارك", "الاسم الثلاثي"}
PHONE_HEADERS = {"phone", "mobile", "whatsapp", "number", "phone number", "رقم", "الرقم", "الجوال",
                 "جوال", "رقم الجوال", "رقم الواتساب", "واتساب", "الهاتف", "رقم الهاتف"}


def default_country_code() -> str:
    return os.environ.get("DEFAULT_COUNTRY_CODE", "966").lstrip("+")


def normalize_phone(raw: str, country_code: str | None = None) -> tuple[str | None, str | None]:
    """Return (E.164 digits without '+', error). Saudi local formats are handled by default."""
    cc = (country_code or default_country_code()).lstrip("+")
    s = str(raw or "").translate(_DIGIT_MAP).strip()
    if s.endswith(".0"):  # numbers read from Excel as floats
        s = s[:-2]
    has_plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None, "رقم الجوال فارغ"
    if has_plus:
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith(cc):
        pass
    elif digits.startswith("0"):
        digits = cc + digits[1:]
    elif cc == "966" and len(digits) == 9 and digits.startswith("5"):
        digits = cc + digits
    elif len(digits) <= 10:
        digits = cc + digits
    if cc == "966" and digits.startswith("966") and not re.fullmatch(r"9665\d{8}", digits):
        return None, "رقم سعودي غير صحيح (المتوقع 05xxxxxxxx)"
    if not 8 <= len(digits) <= 15:
        return None, "طول الرقم غير صحيح"
    return digits, None


def _looks_like_phone(value: str) -> bool:
    d = re.sub(r"\D", "", str(value).translate(_DIGIT_MAP))
    return 7 <= len(d) <= 16 and len(d) >= len(re.sub(r"\s", "", str(value))) * 0.6


def _rows_to_recipients(rows: list[list[str]]) -> list[dict]:
    rows = [[str(c).strip() if c is not None else "" for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    header = [c.strip().lower() for c in rows[0]]
    name_col = next((i for i, h in enumerate(header) if h in NAME_HEADERS), None)
    phone_col = next((i for i, h in enumerate(header) if h in PHONE_HEADERS), None)
    body = rows[1:] if (name_col is not None or phone_col is not None) else rows
    if phone_col is None:
        width = max(len(r) for r in body)
        scores = [sum(_looks_like_phone(r[i]) for r in body if i < len(r)) for i in range(width)]
        phone_col = max(range(width), key=lambda i: scores[i]) if any(scores) else None
    if name_col is None:
        width = max(len(r) for r in body)
        name_col = next((i for i in range(width) if i != phone_col), 0)
    out = []
    for r in body:
        name = r[name_col] if name_col < len(r) else ""
        phone_raw = r[phone_col] if phone_col is not None and phone_col < len(r) else ""
        out.append(make_recipient(name, phone_raw))
    return out


def make_recipient(name: str, phone_raw: str) -> dict:
    name = " ".join(str(name).split())
    phone, err = normalize_phone(phone_raw)
    if not name:
        err = "الاسم فارغ"
    return {"name": name, "phone_raw": str(phone_raw), "phone": phone, "error": err}


def parse_text(text: str) -> list[dict]:
    """Each line: name and phone separated by comma, tab, semicolon or just a space."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in re.split(r"[,\t;،|]", line) if p.strip()]
        if len(parts) < 2:
            m = re.match(r"^(.*?)[\s:]+([+\d٠-٩۰-۹][\d\s\-٠-٩۰-۹]{6,})$", line)
            parts = [m.group(1), m.group(2)] if m else [line, ""]
        rows.append(parts)
    return _rows_to_recipients(rows)


def parse_file(data: bytes, filename: str) -> list[dict]:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = [[("" if c is None else c) for c in row] for row in ws.iter_rows(values_only=True)]
        return _rows_to_recipients(rows)
    text = data.decode("utf-8-sig", errors="replace")
    if name.endswith((".csv", ".tsv")):
        dialect = csv.excel_tab if name.endswith(".tsv") or "\t" in text.splitlines()[0] else csv.excel
        return _rows_to_recipients(list(csv.reader(io.StringIO(text), dialect)))
    return parse_text(text)
