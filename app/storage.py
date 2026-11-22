"""Where generated certificates live so attendees can open them from a WhatsApp link.

* LocalStore  - files on this server, served at /c/<token>. Simple, but on hosts
                with an ephemeral disk (e.g. Render free) the files disappear when
                the server sleeps or redeploys.
* DriveStore  - uploads each PDF to the organizer's Google Drive through a tiny
                Apps Script web app (see apps_script/Code.gs). Files are permanent,
                and the link still opens Mirsal's own page at /d/<file id>/<signature>:
                DriveFiles fetches the PDF back from Drive when someone opens it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

import requests

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
_DRIVE_URL_ID = re.compile(r"/d/([A-Za-z0-9_-]{10,128})|[?&]id=([A-Za-z0-9_-]{10,128})")
MAX_DRIVE_PDF = 20 * 1024 * 1024


class StorageError(Exception):
    pass


class LocalStore:
    kind = "local"
    durable = False

    def __init__(self, root: str | Path, base_url: str):
        self.root = Path(root)
        self.base_url = base_url.rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, pdf: bytes, filename: str, name: str = "", preview: bytes | None = None) -> str:
        token = secrets.token_urlsafe(18)
        (self.root / f"{token}.pdf").write_bytes(pdf)
        if preview:
            (self.root / f"{token}.jpg").write_bytes(preview)
        meta = {"filename": filename, "name": name, "issued": datetime.now(timezone.utc).date().isoformat()}
        (self.root / f"{token}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return f"{self.base_url}/c/{token}"

    def lookup(self, token: str) -> tuple[Path, str] | None:
        info = self.info(token)
        return (info["pdf"], info["filename"]) if info else None

    def info(self, token: str) -> dict | None:
        """Everything the attendee's download page needs, or None for an unknown token."""
        if not _TOKEN.match(token or ""):
            return None
        pdf = self.root / f"{token}.pdf"
        if not pdf.exists():
            return None
        try:
            meta = json.loads((self.root / f"{token}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        preview = self.root / f"{token}.jpg"
        return {"pdf": pdf, "filename": meta.get("filename") or "certificate.pdf",
                "name": meta.get("name", ""), "issued": meta.get("issued", ""),
                "preview": preview if preview.exists() else None}


def sign_drive_id(file_id: str, key: str) -> str:
    """Short signature so /d/ links only work for files this site uploaded."""
    mac = hmac.new(key.encode(), file_id.encode(), hashlib.sha256).digest()[:12]
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def check_drive_link(file_id: str, sig: str, key: str) -> bool:
    return bool(key and DRIVE_ID.match(file_id or "") and
                hmac.compare_digest(sign_drive_id(file_id, key), sig or ""))


class DriveStore:
    kind = "drive"
    durable = True

    def __init__(self, url: str, secret: str, session: requests.Session | None = None, base_url: str = "",
                 page_url: str = ""):
        self.url = url
        self.secret = secret
        self.base_url = base_url.rstrip("/")
        self.page_url = page_url.strip()
        self.http = session or requests.Session()

    def page_link(self, body: dict) -> str:
        """Where the attendee lands, best first:
        1. the static Mirsal page (CERT_PAGE_URL, e.g. GitHub Pages): never sleeps, opens instantly;
        2. this server's /d/ page;
        3. the plain Drive link."""
        file_id = body.get("id")
        if not file_id:
            m = _DRIVE_URL_ID.search(body.get("url", ""))
            file_id = m and (m.group(1) or m.group(2))
        if not (file_id and DRIVE_ID.match(file_id)):
            return body["url"]
        if self.page_url:
            return f"{self.page_url}{'&' if '?' in self.page_url else '?'}id={file_id}"
        if self.base_url and self.secret:
            return f"{self.base_url}/d/{file_id}/{sign_drive_id(file_id, self.secret)}"
        return body["url"]

    def save(self, pdf: bytes, filename: str, name: str = "", preview: bytes | None = None) -> str:
        payload = {"secret": self.secret, "filename": filename, "name": name,
                   "data": base64.b64encode(pdf).decode()}
        last = None
        for _ in range(3):
            try:
                r = self.http.post(self.url, data=json.dumps(payload), timeout=90,
                                   headers={"Content-Type": "text/plain;charset=utf-8"})
                body = r.json()
            except (requests.RequestException, ValueError) as exc:
                last = StorageError(f"تعذّر الحفظ في Google Drive: {exc}")
                continue
            if body.get("url"):
                return self.page_link(body)
            raise StorageError(f"Google Drive رفض الملف: {body.get('error', 'رد غير متوقع')}")
        raise last or StorageError("تعذّر الحفظ في Google Drive")


def make_store(data_dir: str, base_url: str):
    url = os.environ.get("DRIVE_SCRIPT_URL", "").strip()
    if url:
        return DriveStore(url, os.environ.get("DRIVE_SCRIPT_SECRET", ""), base_url=base_url,
                          page_url=os.environ.get("CERT_PAGE_URL", ""))
    return LocalStore(Path(data_dir) / "certs", base_url)


class DriveFiles:
    """Reads certificates back from Google Drive for the attendee page, with a disk cache.

    The cache may vanish (ephemeral disk); Drive stays the source of truth.
    """

    DOWNLOAD_URLS = ("https://drive.usercontent.google.com/download?id={id}&export=download&confirm=t",
                     "https://drive.google.com/uc?export=download&id={id}")

    def __init__(self, cache_dir: str | Path, session: requests.Session | None = None):
        self.cache = Path(cache_dir)
        self.http = session or requests.Session()

    def _download(self, file_id: str) -> bytes | None:
        for tpl in self.DOWNLOAD_URLS:
            try:
                r = self.http.get(tpl.format(id=file_id), timeout=30)
            except requests.RequestException:
                continue
            data = r.content
            if r.status_code == 200 and data.startswith(b"%PDF") and len(data) <= MAX_DRIVE_PDF:
                return data
        return None

    def info(self, file_id: str) -> dict | None:
        if not DRIVE_ID.match(file_id or ""):
            return None
        pdf = self.cache / f"{file_id}.pdf"
        meta_path = self.cache / f"{file_id}.json"
        preview = self.cache / f"{file_id}.jpg"
        if not (pdf.exists() and meta_path.exists()):
            data = self._download(file_id)
            if data is None:
                return None
            self.cache.mkdir(parents=True, exist_ok=True)
            meta, jpg = describe_pdf(data)
            _atomic_write(pdf, data)
            if jpg:
                _atomic_write(preview, jpg)
            _atomic_write(meta_path, json.dumps(meta, ensure_ascii=False).encode("utf-8"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        name = meta.get("name", "")
        filename = "شهادة_" + name.replace(" ", "_") + ".pdf" if name else "certificate.pdf"
        return {"pdf": pdf, "filename": filename, "name": name, "issued": meta.get("issued", ""),
                "preview": preview if preview.exists() else None}


def describe_pdf(data: bytes) -> tuple[dict, bytes | None]:
    """Name + issue date from the PDF metadata, and a JPEG of the first page."""
    import fitz  # PyMuPDF

    with fitz.open(stream=data, filetype="pdf") as doc:
        md = doc.metadata or {}
        created = (md.get("creationDate") or "").removeprefix("D:")
        issued = ""
        if len(created) >= 8 and created[:8].isdigit():
            issued = f"{created[:4]}-{created[4:6]}-{created[6:8]}"
        jpg = None
        if doc.page_count:
            page = doc[0]
            zoom = min(2.0, 1600 / max(page.rect.width, page.rect.height, 1))
            jpg = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("jpeg", jpg_quality=85)
    return {"name": (md.get("title") or "").strip()[:120], "issued": issued}, jpg


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + "." + secrets.token_hex(4) + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class LinkClient:
    """Plays the role of the WhatsApp client in a job, but returns a link instead."""

    returns_url = True

    def __init__(self, store):
        self.store = store

    def send_certificate(self, to, name, pdf, filename, caption="", preview=None):
        try:
            return self.store.save(pdf, filename, name=name, preview=preview)
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"تعذّر حفظ الشهادة: {exc}") from exc
