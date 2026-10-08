// #/parent/backup — AI/image status, today's AI calls, export download and import (replace) with confirmation.
import { api } from "../api.js";
import { esc, toast } from "../ui.js";
import {
  aiConfigured,
  checkBackupFile,
  countsText,
  errText,
  imageConfigError,
  imageProviderText,
  queueInfo,
} from "./helpers.js";
import { authRedirect, errorBox, frame, stillOn } from "./shell.js";

export async function render(root, ctx) {
  const body = frame(root, ctx, "backup", "Status & backup");
  body.innerHTML = `
    <div class="card" id="bstatus"><p class="muted" style="margin:0">Loading…</p></div>
    <div class="card">
      <h3 style="margin:0 0 6px">Export</h3>
      <p class="muted" style="margin:0 0 8px">Downloads every profile, word list, Learn card, question and all progress as one .json file.
        Pictures are not inside the file — they stay in the <code>data/images</code> folder (copy that folder too when moving to another computer).</p>
      <a class="btn btn-secondary" href="/api/parent/export" download>⬇ Download backup</a>
    </div>
    <div class="card danger">
      <h3 style="margin:0 0 6px">Import</h3>
      <p class="muted" style="margin:0 0 8px">Restores a backup file. This <b>replaces everything</b> on this server — all profiles, lists, words and progress.
        Download a backup first if you might want today's data back.</p>
      <label class="pfield"><span>Backup file (.json)</span><input type="file" id="bfile" accept="application/json,.json"></label>
      <label class="pcheck"><input type="checkbox" id="bconfirm"> I understand this replaces all current data</label>
      <button type="button" class="btn pbtn-danger" id="bimport" disabled>⬆ Import backup</button>
      <p class="perr" id="berr" role="alert" aria-live="polite"></p>
      <div id="bresult" aria-live="polite"></div>
    </div>`;

  const statusEl = body.querySelector("#bstatus");
  const fileEl = body.querySelector("#bfile");
  const confirmEl = body.querySelector("#bconfirm");
  const btn = body.querySelector("#bimport");
  const errEl = body.querySelector("#berr");
  const resultEl = body.querySelector("#bresult");
  const sync = () => {
    btn.disabled = !(fileEl.files && fileEl.files.length && confirmEl.checked);
  };
  fileEl.addEventListener("change", sync);
  confirmEl.addEventListener("change", sync);
  btn.addEventListener("click", async () => {
    errEl.textContent = "";
    resultEl.innerHTML = "";
    const file = fileEl.files && fileEl.files[0];
    if (!file || !confirmEl.checked) return;
    const problem = checkBackupFile(await file.text());
    if (problem) {
      errEl.textContent = problem;
      return;
    }
    btn.disabled = true;
    btn.textContent = "Importing…";
    try {
      const res = await uploadBackup(file);
      const summary = countsText(res.counts);
      resultEl.innerHTML = `<div class="pbanner">✅ Backup restored.${summary ? ` Imported ${esc(summary)}.` : ""}</div>`;
      toast("Backup restored ✓");
      confirmEl.checked = false;
      fileEl.value = "";
      await loadStatus(statusEl, ctx);
    } catch (e) {
      if (!authRedirect(e)) errEl.textContent = errText(e);
    } finally {
      btn.textContent = "⬆ Import backup";
      sync();
    }
  });

  await loadStatus(statusEl, ctx);
}

async function loadStatus(el, ctx) {
  let st;
  let q;
  try {
    [st, q] = await Promise.all([api.get("/api/parent/status"), api.get("/api/parent/queue")]);
  } catch (e) {
    errorBox(el, e);
    return;
  }
  if (!stillOn(ctx)) return;
  const info = queueInfo(q);
  const imageProblem = imageConfigError(st);
  el.innerHTML = `
    <h3 style="margin:0 0 8px">Status</h3>
    <table class="ptable"><tbody>
      <tr><th scope="row">AI</th><td>${
        aiConfigured(st) ? `✅ configured — model <b>${esc(st.model || "")}</b>` : "⚠️ not configured — add CEREBRAS_API_KEY to .env and restart the server"
      }</td></tr>
      <tr><th scope="row">Pictures</th><td>${esc(imageProviderText(st))}${
        imageProblem ? `<p class="perr">⚠️ ${esc(imageProblem)} — fix .env and restart the server.</p>` : ""
      }</td></tr>
      <tr><th scope="row">AI calls today</th><td>${esc(info.calls)} / ${esc(info.limit)}${info.paused ? " — paused until tomorrow" : ""}</td></tr>
      <tr><th scope="row">Jobs</th><td>${esc(info.running)} working · ${esc(info.pending)} waiting · ${esc(info.failed)} failed</td></tr>
    </tbody></table>`;
}

// Multipart upload (api.js only sends JSON): form fields `file` and `confirm=true` (Task 18 POST /api/parent/import).
async function uploadBackup(file) {
  const fd = new FormData();
  fd.append("file", file, file.name || "wordquest-backup.json");
  fd.append("confirm", "true");
  const res = await fetch("/api/parent/import", { method: "POST", body: fd, credentials: "same-origin" });
  let data = {};
  try {
    data = await res.json();
  } catch (e) {
    data = {};
  }
  if (!res.ok) {
    const err = new Error(typeof data.detail === "string" ? data.detail : `Import failed (HTTP ${res.status})`);
    err.status = res.status;
    err.detail = data.detail;
    throw err;
  }
  return data;
}
