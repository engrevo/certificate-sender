"""Flask app: upload a certificate, auto-detect the name spot, send via WhatsApp."""
from __future__ import annotations

import hmac
import io
import json
import os
import re
import uuid
import zipfile
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template, request, send_file,
                   session, url_for)
from PIL import Image

from .detector import Box, detect_name_box
from .jobs import JobManager, safe_filename
from .recipients import parse_file, parse_text
from .renderer import (DEFAULT_FONT, FONTS, FONTS_DIR, erase_fill_colors, erase_regions, load_template,
                       render_certificate, to_pdf_bytes, to_png_bytes, view_jpeg)
from .storage import LinkClient, LocalStore, make_store
from .whatsapp import WhatsAppClient, WhatsAppConfig

TEMPLATE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
LIST_EXT = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm"}
_SID = re.compile(r"^[0-9a-f]{32}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def create_app(config: dict | None = None) -> Flask:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or os.urandom(32),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        DATA_DIR=os.environ.get("DATA_DIR", "data"),
        APP_PASSWORD=os.environ.get("APP_PASSWORD", ""),
        SEND_DELAY=float(os.environ.get("SEND_DELAY_SECONDS", "0.2")),
        SEND_WORKERS=int(os.environ.get("SEND_CONCURRENCY", "4")),
        SEND_FILE_MAX_AGE_DEFAULT=86400,
        MAX_RECIPIENTS=int(os.environ.get("MAX_RECIPIENTS", "1000")),
    )
    if config:
        app.config.update(config)
    jobs = JobManager()
    app.extensions["jobs"] = jobs

    def wa_config() -> WhatsAppConfig:
        return app.config.get("WHATSAPP_CONFIG") or WhatsAppConfig.from_env()

    def public_base_url() -> str:
        return (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or request.url_root).rstrip("/")

    def store():
        return app.config.get("CERT_STORE") or make_store(app.config["DATA_DIR"], public_base_url())

    def api_ready(cfg: WhatsAppConfig) -> bool:
        return bool(cfg.token and cfg.phone_number_id)

    # ------------------------------------------------------------- helpers
    def session_dir(sid: str) -> Path:
        if not _SID.match(sid or ""):
            abort(400, "معرّف الجلسة غير صالح")
        d = Path(app.config["DATA_DIR"]) / "sessions" / sid
        if not d.exists():
            abort(404, "انتهت الجلسة، ارفع الشهادة من جديد")
        return d

    def load_session(sid: str) -> tuple[Image.Image, dict]:
        d = session_dir(sid)
        return Image.open(d / "template.png").convert("RGB"), json.loads((d / "meta.json").read_text())

    def parse_settings(payload: dict, meta: dict) -> dict:
        W, H = meta["width"], meta["height"]
        try:
            b = payload["box"]
            box = Box(int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"])).clip(W, H)
        except (KeyError, TypeError, ValueError):
            abort(400, "إحداثيات مكان الاسم غير صالحة")
        erase = []
        if payload.get("erase_placeholder", True):
            erase = [Box(**e).clip(W, H) for e in meta["detection"].get("erase", [])]
        font = payload.get("font") if payload.get("font") in FONTS else DEFAULT_FONT
        color = payload.get("color") if _COLOR.match(str(payload.get("color", ""))) else "#1a1a1a"
        return {"box": box.__dict__, "erase": [e.__dict__ for e in erase], "font": font, "color": color}

    def render(template, settings, name, erased=False):
        erase = [] if erased else [Box(**e) for e in settings["erase"]]
        return render_certificate(template, name, Box(**settings["box"]), settings["font"], settings["color"], erase)

    def clean_recipients(raw) -> list[dict]:
        from .recipients import make_recipient
        if not isinstance(raw, list) or not raw:
            abort(400, "قائمة الأسماء فارغة")
        if len(raw) > app.config["MAX_RECIPIENTS"]:
            abort(400, f"الحد الأقصى {app.config['MAX_RECIPIENTS']} اسم في الدفعة")
        return [make_recipient(r.get("name", ""), r.get("phone_raw") or r.get("phone") or "") for r in raw]

    @app.context_processor
    def asset_helper():
        def asset(filename):
            """Static URL with a version tag, so browsers fetch new files after each deploy."""
            try:
                v = int((Path(app.static_folder) / filename).stat().st_mtime)
            except OSError:
                v = 0
            return url_for("static", filename=filename, v=v)
        return {"asset": asset}

    # ------------------------------------------------------------- auth
    @app.before_request
    def require_login():
        if not app.config["APP_PASSWORD"] or request.endpoint in ("login", "static", "certificate_link"):
            return None
        if session.get("ok"):
            return None
        if request.path.startswith("/api/"):
            return jsonify(error="سجّل الدخول أولًا"), 401
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            if hmac.compare_digest(request.form.get("password", ""), app.config["APP_PASSWORD"]):
                session["ok"] = True
                return redirect(url_for("index"))
            error = "كلمة المرور غير صحيحة"
        return render_template("login.html", error=error)

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def json_error(e):
        msg = getattr(e, "description", str(e))
        if getattr(e, "code", 0) == 413:
            msg = "حجم الملف أكبر من المسموح (25 ميجابايت)"
        return jsonify(error=msg), getattr(e, "code", 400)

    # ------------------------------------------------------------- pages
    @app.get("/")
    def index():
        cfg = wa_config()
        font_urls = {k: url_for("font_file", key=k) for k in FONTS}
        st = store()
        return render_template("index.html", fonts=FONTS, default_font=DEFAULT_FONT, font_urls=font_urls,
                               wa_problems=cfg.problems(), dry_run=cfg.dry_run, wa_mode=cfg.mode,
                               api_ready=api_ready(cfg), storage_durable=st.durable,
                               ephemeral_host=bool(os.environ.get("RENDER")))

    # ------------------------------------------------------------- API
    @app.post("/api/template")
    def upload_template():
        f = request.files.get("file")
        if not f or not f.filename:
            abort(400, "اختر ملف الشهادة")
        if Path(f.filename).suffix.lower() not in TEMPLATE_EXT:
            abort(400, "الصيغ المدعومة: PNG, JPG, WEBP, PDF")
        try:
            img = load_template(f.read(), f.filename)
        except Exception:
            abort(400, "تعذّر قراءة الملف، تأكد أنه صورة أو PDF سليم")
        det = detect_name_box(img)
        sid = uuid.uuid4().hex
        d = Path(app.config["DATA_DIR"]) / "sessions" / sid
        d.mkdir(parents=True)
        img.save(d / "template.png", compress_level=1)
        (d / "view.jpg").write_bytes(view_jpeg(img))
        detection = det.to_dict()
        detection["erase_fill"] = erase_fill_colors(img, det.erase)
        meta = {"width": img.width, "height": img.height, "detection": detection}
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
        return jsonify(session_id=sid, **meta)

    @app.get("/api/template/<sid>/image")
    def template_image(sid):
        d = session_dir(sid)
        if (d / "view.jpg").exists():
            return send_file(d / "view.jpg", mimetype="image/jpeg", max_age=3600)
        return send_file(d / "template.png", mimetype="image/png")

    @app.get("/fonts/<key>.ttf")
    def font_file(key):
        if key not in FONTS:
            abort(404, "الخط غير موجود")
        return send_file(FONTS_DIR / FONTS[key][1], mimetype="font/ttf", max_age=30 * 86400)

    @app.post("/api/preview")
    def preview():
        p = request.get_json(force=True, silent=True) or {}
        template, meta = load_session(p.get("session_id", ""))
        settings = parse_settings(p, meta)
        name = (p.get("name") or "").strip() or "عبدالله محمد العتيبي"
        png = to_png_bytes(render(template, settings, name[:120]), max_side=1600)
        return send_file(io.BytesIO(png), mimetype="image/png")

    @app.post("/api/recipients")
    def recipients():
        f = request.files.get("file")
        if f and f.filename:
            if Path(f.filename).suffix.lower() not in LIST_EXT:
                abort(400, "الصيغ المدعومة للقائمة: Excel أو CSV أو TXT")
            try:
                items = parse_file(f.read(), f.filename)
            except Exception:
                abort(400, "تعذّر قراءة ملف القائمة")
        else:
            items = parse_text(request.form.get("text", ""))
        if not items:
            abort(400, "ما لقيت أسماء في القائمة")
        return jsonify(recipients=items, valid=sum(1 for i in items if not i["error"]))

    @app.post("/api/generate")
    def generate_zip():
        p = request.get_json(force=True, silent=True) or {}
        template, meta = load_session(p.get("session_id", ""))
        settings = parse_settings(p, meta)
        people = [r for r in clean_recipients(p.get("recipients")) if r["name"]]
        base = erase_regions(template, [Box(**e) for e in settings["erase"]])
        buf = io.BytesIO()
        used = set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for r in people:
                fn = safe_filename(r["name"])
                n = 2
                while fn in used:
                    fn = safe_filename(f"{r['name']}_{n}"); n += 1
                used.add(fn)
                z.writestr(fn, to_pdf_bytes(render(base, settings, r["name"], erased=True)))
        buf.seek(0)
        return send_file(buf, mimetype="application/zip", as_attachment=True, download_name="certificates.zip")

    @app.post("/api/send")
    def send():
        p = request.get_json(force=True, silent=True) or {}
        cfg = wa_config()
        if cfg.problems():
            abort(400, "إعدادات واتساب ناقصة: " + "، ".join(cfg.problems()))
        template, meta = load_session(p.get("session_id", ""))
        settings = parse_settings(p, meta)
        people = clean_recipients(p.get("recipients"))
        client = app.config.get("WHATSAPP_CLIENT") or WhatsAppClient(cfg)
        caption = str(p.get("caption", ""))[:900]
        job = jobs.start(template, settings, people, client, app.config["SEND_DELAY"], caption,
                         app.config["SEND_WORKERS"])
        return jsonify(job_id=job.id)

    @app.post("/api/links")
    def make_links():
        """Generate every certificate, store it, and return a shareable link per person."""
        p = request.get_json(force=True, silent=True) or {}
        template, meta = load_session(p.get("session_id", ""))
        settings = parse_settings(p, meta)
        people = clean_recipients(p.get("recipients"))
        job = jobs.start(template, settings, people, LinkClient(store()), 0, "", app.config["SEND_WORKERS"])
        return jsonify(job_id=job.id)

    @app.get("/c/<token>")
    def certificate_link(token):
        st = store()
        found = st.lookup(token) if isinstance(st, LocalStore) else None
        if not found:
            return render_template("expired.html"), 404
        path, filename = found
        return send_file(path, mimetype="application/pdf", download_name=filename, max_age=86400)

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id):
        job = jobs.get(job_id) or abort(404, "العملية غير موجودة")
        return jsonify(job.summary())

    @app.post("/api/jobs/<job_id>/cancel")
    def job_cancel(job_id):
        job = jobs.get(job_id) or abort(404, "العملية غير موجودة")
        job.cancel.set()
        return jsonify(ok=True)

    @app.get("/api/jobs/<job_id>/report.csv")
    def job_report(job_id):
        job = jobs.get(job_id) or abort(404, "العملية غير موجودة")
        return send_file(io.BytesIO(job.report_csv()), mimetype="text/csv", as_attachment=True,
                         download_name="send-report.csv")

    return app
