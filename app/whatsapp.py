"""WhatsApp Cloud API (Meta) client for sending certificate PDFs.

Business-initiated messages (the attendee hasn't messaged you in the last 24h)
MUST use an approved message template. Create one in WhatsApp Manager with:
  * Header: Document
  * Body:   e.g. "مرحبًا {{1}}، شكرًا لحضورك. مرفق شهادة الحضور."
and set WHATSAPP_TEMPLATE_NAME / WHATSAPP_TEMPLATE_LANG accordingly.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

GRAPH = "https://graph.facebook.com"


class WhatsAppError(Exception):
    pass


@dataclass
class WhatsAppConfig:
    token: str = ""
    phone_number_id: str = ""
    api_version: str = "v21.0"
    template_name: str = ""
    template_lang: str = "ar"
    template_body_name_param: bool = True
    mode: str = "template"  # "template" or "document" (only inside the 24h window)
    dry_run: bool = False
    outbox_dir: str = "data/outbox"

    @classmethod
    def from_env(cls) -> "WhatsAppConfig":
        e = os.environ.get
        return cls(
            token=e("WHATSAPP_TOKEN", ""),
            phone_number_id=e("WHATSAPP_PHONE_NUMBER_ID", ""),
            api_version=e("WHATSAPP_API_VERSION", "v21.0"),
            template_name=e("WHATSAPP_TEMPLATE_NAME", ""),
            template_lang=e("WHATSAPP_TEMPLATE_LANG", "ar"),
            template_body_name_param=e("WHATSAPP_TEMPLATE_NAME_PARAM", "1") not in ("0", "false", "no"),
            mode=e("WHATSAPP_SEND_MODE", "template"),
            dry_run=e("DRY_RUN", "0").lower() in ("1", "true", "yes"),
            outbox_dir=e("OUTBOX_DIR", "data/outbox"),
        )

    def problems(self) -> list[str]:
        if self.dry_run:
            return []
        p = []
        if not self.token:
            p.append("WHATSAPP_TOKEN غير مضبوط")
        if not self.phone_number_id:
            p.append("WHATSAPP_PHONE_NUMBER_ID غير مضبوط")
        if self.mode == "template" and not self.template_name:
            p.append("WHATSAPP_TEMPLATE_NAME غير مضبوط")
        return p


class WhatsAppClient:
    def __init__(self, cfg: WhatsAppConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.http = session or requests.Session()

    # ------------------------------------------------------------------ low level
    def _url(self, path: str) -> str:
        return f"{GRAPH}/{self.cfg.api_version}/{self.cfg.phone_number_id}/{path}"

    def _request(self, method: str, path: str, retries: int = 3, **kw) -> dict:
        headers = {"Authorization": f"Bearer {self.cfg.token}"}
        for attempt in range(retries + 1):
            try:
                r = self.http.request(method, self._url(path), headers=headers, timeout=60, **kw)
            except requests.RequestException as exc:
                if attempt == retries:
                    raise WhatsAppError(f"تعذّر الاتصال بخوادم واتساب: {exc}") from exc
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
            try:
                body = r.json()
            except ValueError:
                body = {}
            if not r.ok or "error" in body:
                err = body.get("error", {})
                msg = err.get("error_user_msg") or err.get("message") or r.text[:300]
                code = err.get("code")
                raise WhatsAppError(f"[{code}] {msg}" if code else msg)
            return body
        raise WhatsAppError("فشل الطلب بعد عدة محاولات")

    # ------------------------------------------------------------------ API
    def upload_pdf(self, pdf: bytes, filename: str) -> str:
        body = self._request(
            "POST", "media",
            files={"file": (filename, pdf, "application/pdf")},
            data={"messaging_product": "whatsapp", "type": "application/pdf"},
        )
        return body["id"]

    def build_payload(self, to: str, media_id: str, filename: str, name: str, caption: str = "") -> dict:
        if self.cfg.mode == "document":
            return {
                "messaging_product": "whatsapp", "to": to, "type": "document",
                "document": {"id": media_id, "filename": filename, "caption": caption},
            }
        components = [{
            "type": "header",
            "parameters": [{"type": "document", "document": {"id": media_id, "filename": filename}}],
        }]
        if self.cfg.template_body_name_param:
            components.append({"type": "body", "parameters": [{"type": "text", "text": name}]})
        return {
            "messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {
                "name": self.cfg.template_name,
                "language": {"code": self.cfg.template_lang},
                "components": components,
            },
        }

    def send_certificate(self, to: str, name: str, pdf: bytes, filename: str, caption: str = "") -> str:
        """Upload the PDF and send it. Returns the WhatsApp message id."""
        if self.cfg.dry_run:
            out = Path(self.cfg.outbox_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{to}_{filename}").write_bytes(pdf)
            return f"dry-run-{to}"
        media_id = self.upload_pdf(pdf, filename)
        body = self._request("POST", "messages", json=self.build_payload(to, media_id, filename, name, caption))
        return body.get("messages", [{}])[0].get("id", "")
