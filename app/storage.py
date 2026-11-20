"""Where generated certificates live so attendees can open them from a WhatsApp link.

* LocalStore  - files on this server, served at /c/<token>. Simple, but on hosts
                with an ephemeral disk (e.g. Render free) the files disappear when
                the server sleeps or redeploys.
* DriveStore  - uploads each PDF to the organizer's Google Drive through a tiny
                Apps Script web app (see apps_script/Code.gs). Links are permanent.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
from pathlib import Path

import requests

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class StorageError(Exception):
    pass


class LocalStore:
    kind = "local"
    durable = False

    def __init__(self, root: str | Path, base_url: str):
        self.root = Path(root)
        self.base_url = base_url.rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, pdf: bytes, filename: str) -> str:
        token = secrets.token_urlsafe(18)
        (self.root / f"{token}.pdf").write_bytes(pdf)
        (self.root / f"{token}.json").write_text(json.dumps({"filename": filename}, ensure_ascii=False))
        return f"{self.base_url}/c/{token}"

    def lookup(self, token: str) -> tuple[Path, str] | None:
        if not _TOKEN.match(token or ""):
            return None
        pdf = self.root / f"{token}.pdf"
        if not pdf.exists():
            return None
        try:
            filename = json.loads((self.root / f"{token}.json").read_text())["filename"]
        except (OSError, ValueError, KeyError):
            filename = "certificate.pdf"
        return pdf, filename


class DriveStore:
    kind = "drive"
    durable = True

    def __init__(self, url: str, secret: str, session: requests.Session | None = None):
        self.url = url
        self.secret = secret
        self.http = session or requests.Session()

    def save(self, pdf: bytes, filename: str) -> str:
        payload = {"secret": self.secret, "filename": filename, "data": base64.b64encode(pdf).decode()}
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
                return body["url"]
            raise StorageError(f"Google Drive رفض الملف: {body.get('error', 'رد غير متوقع')}")
        raise last or StorageError("تعذّر الحفظ في Google Drive")


def make_store(data_dir: str, base_url: str):
    url = os.environ.get("DRIVE_SCRIPT_URL", "").strip()
    if url:
        return DriveStore(url, os.environ.get("DRIVE_SCRIPT_SECRET", ""))
    return LocalStore(Path(data_dir) / "certs", base_url)


class LinkClient:
    """Plays the role of the WhatsApp client in a job, but returns a link instead."""

    returns_url = True

    def __init__(self, store):
        self.store = store

    def send_certificate(self, to, name, pdf, filename, caption=""):
        try:
            return self.store.save(pdf, filename)
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"تعذّر حفظ الشهادة: {exc}") from exc
