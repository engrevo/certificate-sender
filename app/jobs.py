"""Background sending jobs with per-recipient status."""
from __future__ import annotations

import csv
import io
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

from PIL import Image

from .detector import Box
from .renderer import render_certificate, to_pdf_bytes
from .whatsapp import WhatsAppClient, WhatsAppError


def safe_filename(name: str, prefix: str = "شهادة") -> str:
    clean = re.sub(r'[\\/:*?"<>|\n\r\t]+', "", name).strip().replace(" ", "_")[:80]
    return f"{prefix}_{clean or 'attendee'}.pdf"


@dataclass
class Job:
    id: str
    total: int
    items: list[dict] = field(default_factory=list)
    state: str = "running"  # running | done | cancelled
    started: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event)

    def summary(self) -> dict:
        counts = {s: sum(1 for i in self.items if i["status"] == s) for s in ("pending", "sending", "sent", "failed", "skipped")}
        return {"id": self.id, "state": self.state, "total": self.total, "counts": counts, "items": self.items}

    def report_csv(self) -> bytes:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["name", "phone", "status", "message_id", "error"])
        for i in self.items:
            w.writerow([i["name"], i.get("phone") or i.get("phone_raw", ""), i["status"], i.get("message_id", ""), i.get("error") or ""])
        return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel shows Arabic


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def start(self, template: Image.Image, settings: dict, recipients: list[dict],
              client: WhatsAppClient, delay: float = 1.0, caption: str = "") -> Job:
        job = Job(id=uuid.uuid4().hex, total=len(recipients))
        for r in recipients:
            job.items.append({
                "name": r["name"], "phone": r.get("phone"), "phone_raw": r.get("phone_raw", ""),
                "status": "skipped" if r.get("error") or not r.get("phone") else "pending",
                "error": r.get("error"),
            })
        with self.lock:
            self.jobs[job.id] = job
        t = threading.Thread(target=self._run, args=(job, template, settings, client, delay, caption), daemon=True)
        t.start()
        return job

    def _run(self, job: Job, template, settings, client, delay, caption):
        box = Box(**settings["box"])
        erase = [Box(**b) for b in settings.get("erase", [])]
        for item in job.items:
            if job.cancel.is_set():
                break
            if item["status"] != "pending":
                continue
            item["status"] = "sending"
            try:
                img = render_certificate(template, item["name"], box, settings.get("font"), settings.get("color", "#1a1a1a"), erase)
                pdf = to_pdf_bytes(img)
                msg_caption = caption.replace("{name}", item["name"]) if caption else ""
                item["message_id"] = client.send_certificate(item["phone"], item["name"], pdf, safe_filename(item["name"]), msg_caption)
                item["status"] = "sent"
            except WhatsAppError as exc:
                item["status"], item["error"] = "failed", str(exc)
            except Exception as exc:  # keep going with the rest of the list
                item["status"], item["error"] = "failed", f"خطأ غير متوقع: {exc}"
            time.sleep(delay)
        job.state = "cancelled" if job.cancel.is_set() else "done"
