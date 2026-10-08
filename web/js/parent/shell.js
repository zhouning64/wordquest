// Shared Parent-area chrome: page frame with nav tabs, navigation helpers, auth-error routing, and the
// generation-queue banner. Screens get ctx from Task 20's router: {params, navigate(hash), alive(), ...}.
import { api } from "../api.js";
import { esc, toast } from "../ui.js";
import { POLL_MS, errText, queueInfo, shouldPoll } from "./helpers.js";

// False once the router has moved on to another route (async work started by this render must stop).
export function stillOn(ctx) {
  return !ctx || typeof ctx.alive !== "function" || ctx.alive();
}

export function go(ctx, hash) {
  if (ctx && typeof ctx.navigate === "function") ctx.navigate(hash);
  else location.hash = hash;
}

// Re-renders the current route (Task 20's navigate() re-runs the route when the hash is unchanged).
export function reload(ctx) {
  go(ctx, location.hash);
}

const TABS = [
  ["profiles", "👪 Profiles"],
  ["lists", "📝 Word lists"],
  ["stats", "📊 Progress"],
  ["backup", "💾 Status & backup"],
];

// Draws the parent page frame into root and returns the body element for the screen's content.
export function frame(root, ctx, active, title) {
  root.classList.add("wide");
  root.innerHTML = `
    <div class="pwrap">
      <div class="phead">
        <a class="plink" href="#/profiles">← Learners</a>
        <b>Parent area</b>
        <button type="button" class="plink" data-logout>Log out</button>
      </div>
      <nav class="ptabs" aria-label="Parent sections">
        ${TABS.map(([key, label]) => `<a class="ptab${key === active ? " active" : ""}" href="#/parent/${key}"${key === active ? ' aria-current="page"' : ""}>${esc(label)}</a>`).join("")}
      </nav>
      ${title ? `<h2 class="ptitle">${esc(title)}</h2>` : ""}
      <div class="pbody"></div>
    </div>`;
  root.querySelector("[data-logout]").addEventListener("click", () => logout(ctx));
  return root.querySelector(".pbody");
}

export async function logout(ctx) {
  try {
    await api.post("/api/auth/parent/logout", {});
  } catch (e) {
    // Best effort: the parent cookie also expires on its own after 12 hours.
  }
  toast("Logged out of the Parent area");
  go(ctx, "#/profiles");
}

// Sends the browser to the right screen for auth failures. Returns true when it redirected.
export function authRedirect(e) {
  const status = e && e.status;
  const detail = e && e.detail;
  if (status === 401 && detail === "access_code_required") {
    location.hash = "#/gate";
    return true;
  }
  if ((status === 401 && detail === "parent_login_required") || (status === 403 && detail === "parent_disabled")) {
    location.hash = "#/parent/login";
    return true;
  }
  return false;
}

export function showError(el, e) {
  if (!authRedirect(e)) el.textContent = errText(e);
}

export function toastError(e) {
  if (!authRedirect(e)) toast(errText(e));
}

export function errorBox(el, e) {
  if (!authRedirect(e)) el.innerHTML = `<p class="perr" role="alert">${esc(errText(e))}</p>`;
}

export function showAiOff(el) {
  el.hidden = false;
  el.classList.add("warn");
  el.textContent =
    "AI is not configured — new words stay “preparing” until CEREBRAS_API_KEY is added to .env and the server is restarted. Starter-set words still work.";
}

// Polls GET /api/parent/queue every POLL_MS while jobs are pending/running and shows the banner text.
// onTick(info) runs after every poll while jobs are active, and once more when the queue becomes idle.
// Returns {start, kick, stop}; kick() polls now (after the parent starts work), stop() ends polling.
export function queueBanner(el, ctx, onTick) {
  let timer = null;
  let busy = false;
  let again = false;
  let stopped = false;
  let wasActive = false;
  const live = () => !stopped && stillOn(ctx);

  function schedule(ms) {
    if (timer === null && live()) timer = setTimeout(tick, ms);
  }

  async function tick() {
    timer = null;
    if (!live()) return;
    if (busy) {
      again = true;
      return;
    }
    busy = true;
    try {
      let q;
      try {
        q = await api.get("/api/parent/queue");
      } catch (e) {
        if (!authRedirect(e)) schedule(POLL_MS);
        return;
      }
      if (!live()) return;
      const info = queueInfo(q);
      el.textContent = info.text;
      el.hidden = !info.text;
      el.classList.toggle("warn", info.paused);
      if (onTick && (info.active || wasActive)) await onTick(info);
      wasActive = info.active;
      if (shouldPoll(info)) schedule(POLL_MS);
    } finally {
      busy = false;
      if (again) {
        again = false;
        schedule(0);
      }
    }
  }

  return {
    start: () => tick(),
    kick: () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      wasActive = true;
      tick();
    },
    stop: () => {
      stopped = true;
      if (timer !== null) clearTimeout(timer);
      timer = null;
    },
  };
}
