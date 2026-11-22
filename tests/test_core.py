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
from app.recipients import normalize_phone, parse_file, parse_text  # noqa: E402
from app.renderer import Box, default_box, load_template, render_certificate, to_pdf_bytes  # noqa: E402
from app.whatsapp import WhatsAppClient, WhatsAppConfig, WhatsAppError  # noqa: E402
import make_samples  # noqa: E402


@pytest.fixture(scope="session")
def samples():
    return {name: fn() for name, fn in make_samples.SAMPLES.items()}


def test_default_box_is_centred(samples):
    b = default_box(2000, 1414)
    assert abs((b.x + b.w / 2) - 1000) <= 1 and 0.4 * 1414 < b.y < 0.5 * 1414


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
    t = upload(client, samples["line.png"])
    r = client.post("/api/recipients", data={"text": "عبدالله، 0551234567\nسارة، 05\nخالد، 0501112233"})
    people = r.json["recipients"]
    assert r.json["valid"] == 2
    common = {"session_id": t["session_id"], "box": t["box"], "font": "amiri-bold", "color": "#112233"}

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


def test_upload_is_light_and_returns_a_default_box(client, samples):
    t = upload(client, samples["line.png"])
    assert set(t) == {"session_id", "width", "height", "box"}
    img = client.get(f"/api/template/{t['session_id']}/image")
    assert img.mimetype == "image/jpeg" and len(img.data) < 400_000


def test_font_route(client):
    r = client.get("/fonts/amiri-bold.ttf")
    assert r.status_code == 200 and r.mimetype == "font/ttf"
    assert client.get("/fonts/../app.ttf").status_code == 404


def test_big_templates_are_capped(samples):
    buf = io.BytesIO(); samples["line.png"].resize((5000, 3535)).save(buf, "PNG")
    assert max(load_template(buf.getvalue(), "big.png").size) == 2480


def test_parallel_send_uses_all_workers(tmp_path):
    import threading
    from app.jobs import JobManager

    seen, lock = set(), threading.Lock()

    class SlowClient:
        def send_certificate(self, to, name, pdf, filename, caption=""):
            with lock:
                seen.add(threading.get_ident())
            time.sleep(0.2)
            return "ok"

    tpl = Image.new("RGB", (800, 560), "white")
    people = [{"name": f"n{i}", "phone": "966551234567"} for i in range(8)]
    settings = {"box": {"x": 100, "y": 200, "w": 600, "h": 80}, "font": "amiri", "color": "#000000"}
    t = time.time()
    job = JobManager().start(tpl, settings, people, SlowClient(), delay=0, workers=4)
    while job.state == "running":
        time.sleep(0.05)
    assert sum(i["status"] == "sent" for i in job.items) == 8
    assert len(seen) == 4 and time.time() - t < 1.2  # ~4x faster than one-by-one (1.6s)


# ------------------------------------------------------------------ send from my own number (links)
def wait_job(client, job_id):
    for _ in range(200):
        s = client.get(f"/api/jobs/{job_id}").json
        if s["state"] != "running":
            return s
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_links_mode_end_to_end(client, samples):
    t = upload(client, samples["line.png"])
    people = client.post("/api/recipients", data={"text": "عبدالله، 0551234567\nرقم غلط، 05"}).json["recipients"]
    job_id = client.post("/api/links", json={"session_id": t["session_id"], "box": t["box"],
                                             "recipients": people}).json["job_id"]
    s = wait_job(client, job_id)
    ok, bad = s["items"]
    assert ok["status"] == "sent" and ok["url"].startswith("http://localhost/c/")
    assert bad["status"] == "skipped" and "url" not in bad
    token_path = ok["url"].replace("http://localhost", "")
    page = client.get(token_path)
    assert page.status_code == 200 and "عبدالله" in page.get_data(as_text=True)
    assert client.get(token_path + "/preview.jpg").mimetype == "image/jpeg"
    pdf = client.get(token_path + "/file?dl=1")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF") and pdf.mimetype == "application/pdf"
    assert "attachment" in pdf.headers["Content-Disposition"]


def test_certificate_links_are_public_but_tokens_are_checked(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "APP_PASSWORD": "secret"})
    c = app.test_client()
    from app.storage import LocalStore
    url = LocalStore(tmp_path / "certs", "http://x").save(b"%PDF-1.4 test", "شهادة_سارة.pdf")
    r = c.get(url.replace("http://x", ""))  # no login needed for attendees
    assert r.status_code == 200 and "سارة" in r.get_data(as_text=True)
    f = c.get(url.replace("http://x", "") + "/file")
    assert f.status_code == 200 and "attachment" not in f.headers.get("Content-Disposition", "")
    assert c.get("/c/..%2F..%2Fetc%2Fpasswd").status_code != 200  # never reaches a file
    assert c.get("/c/AAAAAAAAAAAAAAAAAAAAAAAA").status_code == 404


def test_drive_store_posts_base64_and_returns_url():
    import base64, json as _json
    from app.storage import DriveStore, StorageError

    class S:
        def __init__(self, body): self.body, self.sent = body, None
        def post(self, url, data, timeout, headers):
            self.sent = _json.loads(data)
            return FakeResp(200, self.body)

    s = S({"url": "https://drive.google.com/file/d/abc/view"})
    assert DriveStore("https://script/exec", "pw", s).save(b"%PDF", "a.pdf").endswith("/view")
    assert s.sent["secret"] == "pw" and base64.b64decode(s.sent["data"]) == b"%PDF"
    with pytest.raises(StorageError, match="unauthorized"):
        DriveStore("https://script/exec", "bad", S({"error": "unauthorized"})).save(b"%PDF", "a.pdf")


def test_api_button_only_when_whatsapp_api_configured(tmp_path):
    plain = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "WHATSAPP_CONFIG": WhatsAppConfig()})
    html = plain.test_client().get("/").get_data(as_text=True)
    assert 'id="linkBtn"' in html and 'id="sendBtn"' not in html
    api = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "WHATSAPP_CONFIG": cfg()})
    assert 'id="sendBtn"' in api.test_client().get("/").get_data(as_text=True)


def test_drive_links_open_the_mirsal_page_and_fetch_from_drive(tmp_path, monkeypatch):
    import json as _json
    from app.storage import DriveFiles, DriveStore

    monkeypatch.setenv("DRIVE_SCRIPT_SECRET", "pw")
    pdf = to_pdf_bytes(Image.new("RGB", (600, 420), "white"), "سارة أحمد")

    class Upload:
        def post(self, url, data, timeout, headers):
            return FakeResp(200, {"url": "https://drive.google.com/file/d/FILEid_1234567890/view", "id": "FILEid_1234567890"})

    link = DriveStore("https://script/exec", "pw", Upload(), base_url="http://x").save(pdf, "a.pdf", name="سارة أحمد")
    assert link.startswith("http://x/d/FILEid_1234567890/")
    path = link.replace("http://x", "")

    class Drive:  # public Drive download endpoint
        calls = 0
        def get(self, url, timeout):
            Drive.calls += 1
            assert "FILEid_1234567890" in url
            r = FakeResp(200, None); r.content = pdf
            return r

    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "APP_PASSWORD": "secret",
                      "DRIVE_FILES": DriveFiles(tmp_path / "cache", Drive())})
    c = app.test_client()  # attendees never log in
    page = c.get(path)
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "سارة أحمد" in html and time.strftime("%Y-", time.gmtime()) in html
    assert c.get(path + "/preview.jpg").mimetype == "image/jpeg"
    f = c.get(path + "/file?dl=1")
    assert f.data.startswith(b"%PDF") and "attachment" in f.headers["Content-Disposition"]
    assert Drive.calls == 1  # served from cache after the first fetch
    assert c.get("/d/FILEid_1234567890/forged-signature").status_code == 404
    assert c.get("/d/OTHERfile_123456789/" + path.rsplit("/", 1)[1]).status_code == 404


def test_drive_links_go_to_static_page_when_configured():
    import json as _json
    from app.storage import DriveStore

    class S:
        sent = None
        def post(self, url, data, timeout, headers):
            S.sent = _json.loads(data)
            return FakeResp(200, {"url": "https://drive.google.com/file/d/FILEid_1234567890/view", "id": "FILEid_1234567890"})

    page = "https://engrevo.github.io/certificate-sender/c/"
    link = DriveStore("https://script/exec", "pw", S(), base_url="http://x", page_url=page).save(b"%PDF", "a.pdf", name="سارة")
    assert link == page + "?id=FILEid_1234567890"
    assert S.sent["name"] == "سارة"  # stored in Drive so the static page can show it
