import io
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "samples"))

from app import create_app  # noqa: E402
from app.detector import Box, detect_name_box, is_placeholder_text  # noqa: E402
from app.recipients import normalize_phone, parse_file, parse_text  # noqa: E402
from app.renderer import load_template, render_certificate, to_pdf_bytes  # noqa: E402
from app.whatsapp import WhatsAppClient, WhatsAppConfig, WhatsAppError  # noqa: E402
import make_samples  # noqa: E402


@pytest.fixture(scope="session")
def samples():
    return {name: fn() for name, fn in make_samples.SAMPLES.items()}


def center(b):
    return b.x + b.w / 2, b.y + b.h / 2


# ------------------------------------------------------------------ detection
def test_arabic_placeholder_detected_and_erased(samples):
    d = detect_name_box(samples["placeholder_ar.png"])
    assert d.method == "placeholder"
    cx, cy = center(d.box)
    assert abs(cx - 1000) < 60 and 560 < cy < 720
    assert d.erase, "placeholder must be erased"
    assert d.color.lower() == "#7a1f1f"


def test_english_placeholder_detected(samples):
    d = detect_name_box(samples["placeholder_en.png"])
    assert d.method == "placeholder"


@pytest.mark.parametrize("name", ["line.png", "dotted.png"])
def test_line_detected(samples, name):
    d = detect_name_box(samples[name])
    assert d.method == "line"
    assert d.box.y + d.box.h <= 760 and d.box.y + d.box.h > 700  # just above the line
    assert d.box.w > 900


def test_blank_uses_whitespace_not_signature_line(samples):
    d = detect_name_box(samples["blank.png"])
    assert d.method == "whitespace"
    cx, cy = center(d.box)
    assert 500 < cy < 850 and abs(cx - 1000) < 50


@pytest.mark.parametrize("text,alone,expected", [
    ("{الاسم}", False, True), ("{{name}}", False, True), ("<<Name>>", False, True),
    ("[اسم المشارك]", False, True), ("NAME", False, True), ("الاسم", True, True),
    ("الاسم", False, False), ("name", False, False), ("نادي", True, False),
])
def test_placeholder_rules(text, alone, expected):
    assert is_placeholder_text(text, alone) is expected


# ------------------------------------------------------------------ rendering
def test_render_changes_box_and_long_names_fit(samples):
    tpl = samples["line.png"]
    box = Box(500, 640, 1000, 110)
    out = render_certificate(tpl, "عبدالرحمن بن عبدالعزيز بن عبدالله آل سعود القحطاني الشهري", box)
    diff = Image.eval(Image.frombytes("L", tpl.size, bytes(
        abs(a - b) for a, b in zip(tpl.convert("L").tobytes(), out.convert("L").tobytes()))), lambda v: 255 if v > 30 else 0)
    bbox = diff.getbbox()
    assert bbox is not None
    assert bbox[0] >= box.x and bbox[2] <= box.x + box.w  # stays inside the box


def test_pdf_roundtrip(samples):
    buf = io.BytesIO(); samples["line.png"].save(buf, "PNG")
    pdf = to_pdf_bytes(load_template(buf.getvalue(), "t.png"))
    assert pdf.startswith(b"%PDF")
    img = load_template(pdf, "t.pdf")
    assert img.width > 1000


# ------------------------------------------------------------------ recipients
@pytest.mark.parametrize("raw,expected", [
    ("0551234567", "966551234567"), ("551234567", "966551234567"), ("+966 55 123 4567", "966551234567"),
    ("00966551234567", "966551234567"), ("٠٥٥١٢٣٤٥٦٧", "966551234567"), ("966551234567", "966551234567"),
    ("551234567.0", "966551234567"), ("+201001234567", "201001234567"),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw, "966") == (expected, None)


@pytest.mark.parametrize("raw", ["", "0123", "0412345678", "abc"])
def test_bad_phones(raw):
    phone, err = normalize_phone(raw, "966")
    assert phone is None and err


def test_parse_text_formats():
    items = parse_text("عبدالله محمد، 0551234567\nسارة\t0569876543\nJohn Smith 0501112233\n\nبدون رقم")
    assert [i["name"] for i in items] == ["عبدالله محمد", "سارة", "John Smith", "بدون رقم"]
    assert items[0]["phone"] == "966551234567" and items[2]["phone"] == "966501112233"
    assert items[3]["error"]


def test_parse_csv_with_arabic_headers_and_reversed_columns():
    data = "الجوال,الاسم\n0551234567,عبدالله\n".encode("utf-8-sig")
    items = parse_file(data, "list.csv")
    assert items == [{"name": "عبدالله", "phone_raw": "0551234567", "phone": "966551234567", "error": None}]


def test_parse_xlsx_without_header(tmp_path):
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    ws.append(["سارة", 569876543]); ws.append(["خالد", "0501112233"])
    p = tmp_path / "l.xlsx"; wb.save(p)
    items = parse_file(p.read_bytes(), "l.xlsx")
    assert [i["phone"] for i in items] == ["966569876543", "966501112233"]


# ------------------------------------------------------------------ whatsapp
class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)
        self.ok = status < 400

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)


def cfg(**kw):
    base = dict(token="T", phone_number_id="123", template_name="cert", template_lang="ar")
    base.update(kw)
    return WhatsAppConfig(**base)


def test_whatsapp_template_flow():
    s = FakeSession([FakeResp(200, {"id": "MEDIA1"}), FakeResp(200, {"messages": [{"id": "wamid.X"}]})])
    mid = WhatsAppClient(cfg(), s).send_certificate("966551234567", "سارة", b"%PDF", "c.pdf")
    assert mid == "wamid.X"
    assert s.calls[0][1].endswith("/123/media")
    payload = s.calls[1][2]["json"]
    assert payload["type"] == "template" and payload["template"]["name"] == "cert"
    header, body = payload["template"]["components"]
    assert header["parameters"][0]["document"]["id"] == "MEDIA1"
    assert body["parameters"][0]["text"] == "سارة"
    assert s.calls[1][2]["headers"]["Authorization"] == "Bearer T"


def test_whatsapp_error_message_surfaces():
    s = FakeSession([FakeResp(400, {"error": {"code": 131030, "message": "Recipient not in allowed list"}})])
    with pytest.raises(WhatsAppError, match="131030"):
        WhatsAppClient(cfg(), s).send_certificate("966551234567", "x", b"%PDF", "c.pdf")


def test_config_problems():
    assert cfg(dry_run=True, token="").problems() == []
    assert len(WhatsAppConfig().problems()) == 3


# ------------------------------------------------------------------ flask end-to-end
@pytest.fixture
def client(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SEND_DELAY": 0, "APP_PASSWORD": "",
                      "WHATSAPP_CONFIG": WhatsAppConfig(dry_run=True, outbox_dir=str(tmp_path / "outbox"))})
    return app.test_client()


def upload(client, img):
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0)
    r = client.post("/api/template", data={"file": (buf, "cert.png")}, content_type="multipart/form-data")
    assert r.status_code == 200, r.json
    return r.json


def test_end_to_end_dry_run(client, samples, tmp_path):
    t = upload(client, samples["placeholder_ar.png"])
    assert t["detection"]["method"] == "placeholder"
    r = client.post("/api/recipients", data={"text": "عبدالله، 0551234567\nسارة، 05\nخالد، 0501112233"})
    people = r.json["recipients"]
    assert r.json["valid"] == 2
    common = {"session_id": t["session_id"], "box": t["detection"]["box"], "font": "amiri-bold", "color": "#112233"}

    assert client.post("/api/preview", json={**common, "name": "تجربة"}).mimetype == "image/png"

    z = client.post("/api/generate", json={**common, "recipients": people})
    import zipfile
    assert len(zipfile.ZipFile(io.BytesIO(z.data)).namelist()) == 3

    job_id = client.post("/api/send", json={**common, "recipients": people}).json["job_id"]
    for _ in range(100):
        s = client.get(f"/api/jobs/{job_id}").json
        if s["state"] != "running":
            break
        time.sleep(0.1)
    assert s["counts"]["sent"] == 2 and s["counts"]["skipped"] == 1
    assert len(list((tmp_path / "outbox").glob("*.pdf"))) == 2
    report = client.get(f"/api/jobs/{job_id}/report.csv").data.decode("utf-8-sig")
    assert "sent" in report and "skipped" in report


def test_rejects_bad_input(client):
    assert client.post("/api/preview", json={"session_id": "../../etc"}).status_code == 400
    bad = client.post("/api/template", data={"file": (io.BytesIO(b"x"), "a.exe")}, content_type="multipart/form-data")
    assert bad.status_code == 400 and "الصيغ" in bad.json["error"]


def test_login_required(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "APP_PASSWORD": "secret"})
    c = app.test_client()
    assert c.get("/").status_code == 302
    assert c.post("/api/recipients", data={"text": "a, 0551234567"}).status_code == 401
    c.post("/login", data={"password": "secret"})
    assert c.get("/").status_code == 200
