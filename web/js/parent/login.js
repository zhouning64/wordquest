// #/parent and #/parent/login — parent passcode screen.
import { api } from "../api.js";
import { esc } from "../ui.js";
import { errText } from "./helpers.js";
import { go, stillOn } from "./shell.js";

const DISABLED_HTML = `
  <h3 style="margin:0 0 6px">The Parent area is turned off</h3>
  <p style="margin:0 0 8px">No parent passcode is set on this server.</p>
  <p class="muted" style="margin:0">On the computer running WordQuest, add a line like
  <code>PARENT_PASSCODE=pick-a-passcode</code> to the <code>.env</code> file and restart the server.</p>`;

export async function render(root, ctx) {
  root.innerHTML = `
    <div class="pwrap narrow">
      <div class="hero"><h1>Parent area</h1><p>Profiles, word lists, progress and backups</p></div>
      <div class="card" id="plogin"><p class="muted" style="margin:0">Checking…</p></div>
      <a class="plink" href="#/profiles">← back to learners</a>
    </div>`;
  const box = root.querySelector("#plogin");
  try {
    await api.get("/api/parent/status");
    if (stillOn(ctx)) go(ctx, "#/parent/profiles");
    return;
  } catch (e) {
    if (!stillOn(ctx)) return;
    if (e.status === 401 && e.detail === "access_code_required") {
      go(ctx, "#/gate");
      return;
    }
    if (e.status === 403 && e.detail === "parent_disabled") {
      box.innerHTML = DISABLED_HTML;
      return;
    }
    showForm(box, ctx, e.status === 401 ? "" : errText(e));
  }
}

function showForm(box, ctx, message) {
  box.innerHTML = `
    <form id="plogin-form" novalidate>
      <label class="pfield"><span>Parent passcode</span>
        <input type="password" name="passcode" autocomplete="current-password" autocapitalize="off" required></label>
      <button class="btn btn-primary" type="submit">Enter</button>
      <p class="perr" role="alert" aria-live="polite">${esc(message)}</p>
    </form>`;
  const form = box.querySelector("form");
  const input = form.querySelector('[name="passcode"]');
  const err = form.querySelector(".perr");
  const btn = form.querySelector('button[type="submit"]');
  input.focus();
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const passcode = input.value;
    if (!passcode) {
      err.textContent = "Type the parent passcode.";
      return;
    }
    btn.disabled = true;
    err.textContent = "";
    try {
      await api.post("/api/auth/parent", { passcode });
      go(ctx, "#/parent/profiles");
    } catch (e) {
      if (e.status === 401 && e.detail === "access_code_required") {
        go(ctx, "#/gate");
        return;
      }
      if (e.status === 403 && e.detail === "parent_disabled") {
        box.innerHTML = DISABLED_HTML;
        return;
      }
      if (e.status === 429) err.textContent = "Too many wrong tries. Wait 15 minutes and try again.";
      else if (e.status === 401) err.textContent = "That passcode isn't right.";
      else err.textContent = errText(e);
      input.value = "";
      input.focus();
    } finally {
      btn.disabled = false;
    }
  });
}
