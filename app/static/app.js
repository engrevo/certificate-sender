(() => {
  const $ = (id) => document.getElementById(id);
  const state = { sid: null, W: 0, H: 0, box: null, detected: null, hasErase: false, recipients: [], jobId: null };

  const METHOD_MSG = {
    placeholder: "✓ لقينا كلمة الاسم داخل الشهادة، وراح نمسحها ونكتب مكانها اسم كل شخص.",
    line: "✓ لقينا الخط المخصص للاسم، والاسم بينكتب فوقه.",
    whitespace: "حددنا أوسع مساحة فاضية في وسط الشهادة. تأكد من المكان وعدّله لو احتاج.",
    center: "ما قدرنا نحدد المكان بثقة، فحطينا المربع في الوسط. اسحبه للمكان الصحيح.",
  };

  // ------------------------------------------------------------ utils
  let toastTimer;
  function toast(msg, isErr = false) {
    const t = $("toast");
    t.textContent = msg; t.className = "toast" + (isErr ? " err" : ""); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 4500);
  }
  async function api(url, opts = {}) {
    const res = await fetch(url, opts);
    if (!res.ok) {
      let msg = "صار خطأ، حاول مرة ثانية";
      try { msg = (await res.json()).error || msg; } catch (_) {}
      throw new Error(msg);
    }
    return res;
  }
  const settings = () => ({
    session_id: state.sid, box: state.box, font: $("font").value, color: $("color").value,
    erase_placeholder: $("erase").checked,
  });
  function refreshButtons() {
    const ready = state.sid && state.recipients.some((r) => r.name);
    $("downloadZip").disabled = !ready;
    $("sendBtn").disabled = !(state.sid && state.recipients.some((r) => !r.error));
  }

  // ------------------------------------------------------------ step 1: template
  const drop = $("drop");
  ["dragover", "dragenter"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (ev) => { ev.preventDefault(); if (ev.dataTransfer.files[0]) uploadTemplate(ev.dataTransfer.files[0]); });
  $("templateFile").addEventListener("change", (e) => e.target.files[0] && uploadTemplate(e.target.files[0]));

  async function uploadTemplate(file) {
    const fd = new FormData(); fd.append("file", file);
    drop.querySelector("strong").textContent = "جاري تحليل الشهادة…";
    try {
      const data = await (await api("/api/template", { method: "POST", body: fd })).json();
      state.sid = data.session_id; state.W = data.width; state.H = data.height;
      state.detected = data.detection; state.box = { ...data.detection.box };
      state.hasErase = (data.detection.erase || []).length > 0;
      $("color").value = data.detection.color || "#1a1a1a";
      $("eraseWrap").hidden = !state.hasErase;
      const msg = $("detectMsg");
      msg.textContent = METHOD_MSG[data.detection.method] || "";
      msg.className = "detect" + (data.detection.confidence < 0.6 ? " low" : "");
      $("tplImg").src = `/api/template/${state.sid}/image`;
      $("editor").hidden = false;
      drop.querySelector("strong").textContent = "تغيير الشهادة — اسحب ملف جديد أو اضغط";
      placeBox(); schedulePreview(0); refreshButtons();
    } catch (err) {
      drop.querySelector("strong").textContent = "اسحب الشهادة هنا أو اضغط للاختيار";
      toast(err.message, true);
    }
  }

  // ------------------------------------------------------------ drag / resize box
  const boxEl = $("box"), stage = $("stage");
  function placeBox() {
    const b = state.box;
    Object.assign(boxEl.style, {
      left: (b.x / state.W) * 100 + "%", top: (b.y / state.H) * 100 + "%",
      width: (b.w / state.W) * 100 + "%", height: (b.h / state.H) * 100 + "%",
    });
  }
  let drag = null;
  boxEl.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    const r = stage.getBoundingClientRect();
    drag = { mode: e.target.dataset.h || "move", sx: e.clientX, sy: e.clientY, k: state.W / r.width, start: { ...state.box } };
    boxEl.setPointerCapture(e.pointerId);
  });
  boxEl.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = (e.clientX - drag.sx) * drag.k, dy = (e.clientY - drag.sy) * drag.k, s = drag.start;
    let { x, y, w, h } = s; const min = 20;
    if (drag.mode === "move") { x = s.x + dx; y = s.y + dy; }
    if (drag.mode.includes("e")) w = Math.max(min, s.w + dx);
    if (drag.mode.includes("w")) { w = Math.max(min, s.w - dx); x = s.x + s.w - w; }
    if (drag.mode.includes("s")) h = Math.max(min, s.h + dy);
    if (drag.mode.includes("n")) { h = Math.max(min, s.h - dy); y = s.y + s.h - h; }
    x = Math.min(Math.max(0, x), state.W - w); y = Math.min(Math.max(0, y), state.H - h);
    state.box = { x: Math.round(x), y: Math.round(y), w: Math.round(Math.min(w, state.W)), h: Math.round(Math.min(h, state.H)) };
    placeBox();
  });
  const endDrag = () => { if (drag) { drag = null; schedulePreview(); } };
  boxEl.addEventListener("pointerup", endDrag);
  boxEl.addEventListener("pointercancel", endDrag);
  $("resetBox").addEventListener("click", () => { state.box = { ...state.detected.box }; placeBox(); schedulePreview(0); });

  // ------------------------------------------------------------ preview
  let pvTimer, pvSeq = 0, pvUrl;
  function schedulePreview(delay = 350) { clearTimeout(pvTimer); pvTimer = setTimeout(renderPreview, delay); }
  async function renderPreview() {
    if (!state.sid) return;
    const seq = ++pvSeq;
    try {
      const res = await api("/api/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...settings(), name: $("sampleName").value }),
      });
      const blob = await res.blob();
      if (seq !== pvSeq) return;
      if (pvUrl) URL.revokeObjectURL(pvUrl);
      pvUrl = URL.createObjectURL(blob); $("previewImg").src = pvUrl;
    } catch (err) { toast(err.message, true); }
  }
  ["sampleName", "font", "color", "erase"].forEach((id) => $(id).addEventListener("input", () => schedulePreview()));

  // ------------------------------------------------------------ step 2: list
  $("listFile").addEventListener("change", (e) => e.target.files[0] && loadList(e.target.files[0]));
  $("loadList").addEventListener("click", () => loadList(null));
  async function loadList(file) {
    const fd = new FormData();
    if (file) fd.append("file", file); else fd.append("text", $("listText").value);
    try {
      const data = await (await api("/api/recipients", { method: "POST", body: fd })).json();
      state.recipients = data.recipients; renderList();
    } catch (err) { toast(err.message, true); }
    $("listFile").value = "";
  }
  function statusCell(r) {
    if (r.sendStatus) {
      const map = { pending: ["بالانتظار", "st-wait"], sending: ["جاري الإرسال…", "st-wait"], sent: ["✓ انرسلت", "st-ok"],
                    failed: ["✗ " + (r.sendError || "فشل"), "st-bad"], skipped: ["تم تخطيه: " + (r.sendError || ""), "st-bad"] };
      const [t, c] = map[r.sendStatus] || [r.sendStatus, ""];
      return `<span class="${c}">${esc(t)}</span>`;
    }
    return r.error ? `<span class="st-bad">${esc(r.error)}</span>` : `<span class="st-ok">جاهز</span>`;
  }
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function renderList() {
    const tb = $("listTable").querySelector("tbody");
    tb.innerHTML = state.recipients.map((r, i) => `<tr>
      <td>${i + 1}</td><td>${esc(r.name)}</td><td class="phone">${esc(r.phone ? "+" + r.phone : r.phone_raw)}</td>
      <td>${statusCell(r)}</td><td>${state.jobId ? "" : `<button class="rm" data-i="${i}" title="حذف">✕</button>`}</td></tr>`).join("");
    $("listTable").hidden = !state.recipients.length;
    const ok = state.recipients.filter((r) => !r.error).length, bad = state.recipients.length - ok;
    $("listSummary").textContent = state.recipients.length
      ? `${state.recipients.length} اسم — ${ok} جاهز للإرسال${bad ? ` — ${bad} فيه مشكلة (يتولّد له شهادة بس ما ينرسل)` : ""}` : "";
    refreshButtons();
  }
  $("listTable").addEventListener("click", (e) => {
    const b = e.target.closest(".rm"); if (!b) return;
    state.recipients.splice(+b.dataset.i, 1); renderList();
  });

  // ------------------------------------------------------------ step 3: generate / send
  $("downloadZip").addEventListener("click", async () => {
    const btn = $("downloadZip"); btn.disabled = true; const label = btn.textContent; btn.textContent = "جاري التوليد…";
    try {
      const res = await api("/api/generate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...settings(), recipients: state.recipients }),
      });
      const a = document.createElement("a"); a.href = URL.createObjectURL(await res.blob());
      a.download = "certificates.zip"; a.click();
    } catch (err) { toast(err.message, true); }
    btn.textContent = label; refreshButtons();
  });

  $("sendBtn").addEventListener("click", async () => {
    const n = state.recipients.filter((r) => !r.error).length;
    if (!confirm(`راح تنرسل ${n} شهادة على واتساب. متأكد؟`)) return;
    try {
      const cap = $("caption");
      const data = await (await api("/api/send", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...settings(), recipients: state.recipients, caption: cap ? cap.value : "" }),
      })).json();
      state.jobId = data.job_id;
      $("sendBtn").disabled = true; $("cancelBtn").hidden = false; $("progress").hidden = false; $("reportLink").hidden = true;
      poll();
    } catch (err) { toast(err.message, true); }
  });
  $("cancelBtn").addEventListener("click", () => state.jobId && api(`/api/jobs/${state.jobId}/cancel`, { method: "POST" }));

  async function poll() {
    try {
      const job = await (await api(`/api/jobs/${state.jobId}`)).json();
      job.items.forEach((it, i) => { if (state.recipients[i]) { state.recipients[i].sendStatus = it.status; state.recipients[i].sendError = it.error; } });
      renderList();
      const c = job.counts, done = c.sent + c.failed + c.skipped;
      $("barFill").style.width = (job.total ? (done / job.total) * 100 : 100) + "%";
      $("progressText").textContent = `انرسلت ${c.sent} من ${job.total}` + (c.failed ? ` — فشل ${c.failed}` : "") + (c.skipped ? ` — تخطي ${c.skipped}` : "");
      if (job.state === "running") return setTimeout(poll, 1500);
      $("cancelBtn").hidden = true;
      const link = $("reportLink"); link.href = `/api/jobs/${state.jobId}/report.csv`; link.hidden = false;
      toast(job.state === "cancelled" ? "تم إيقاف الإرسال" : "انتهى الإرسال ✓");
      state.jobId = null; refreshButtons();
    } catch (err) { toast(err.message, true); setTimeout(poll, 3000); }
  }
})();
