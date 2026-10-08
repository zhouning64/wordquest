// #/parent/profiles (list), #/parent/profiles/new (create), #/parent/profiles/<id> (edit + delete).
import { api } from "../api.js";
import { esc, toast } from "../ui.js";
import {
  AVATARS,
  BANDS,
  BAND_LABEL,
  DEFAULT_BREAK_MESSAGE,
  asRows,
  confirmMatches,
  moveItem,
  profilePayload,
  toggleId,
  validateProfile,
} from "./helpers.js";
import { errorBox, frame, go, showError, stillOn } from "./shell.js";

export async function render(root, ctx) {
  const id = ctx.params[0];
  if (!id) return renderIndex(root, ctx);
  return renderForm(root, ctx, id === "new" ? null : id);
}

async function loadAll() {
  const [profiles, lists] = await Promise.all([api.get("/api/parent/profiles"), api.get("/api/parent/lists")]);
  return { profiles: asRows(profiles, "profiles"), lists: asRows(lists, "lists") };
}

async function renderIndex(root, ctx) {
  const body = frame(root, ctx, "profiles", "Profiles");
  body.innerHTML = `<p class="muted">Loading…</p>`;
  let data;
  try {
    data = await loadAll();
  } catch (e) {
    errorBox(body, e);
    return;
  }
  if (!stillOn(ctx)) return;
  const listName = new Map(data.lists.map((l) => [l.id, l.name]));
  const cards = data.profiles
    .map((p) => {
      const s = p.settings || {};
      const names = (p.list_ids || []).map((lid) => listName.get(lid)).filter(Boolean);
      return `
        <div class="card prow">
          <span class="pavatar" aria-hidden="true">${esc(p.avatar || "🙂")}</span>
          <div class="grow">
            <b>${esc(p.name)}</b> <span class="pbadge is-none">${esc(BAND_LABEL[p.band] || p.band)}</span>
            <div class="muted">${esc(s.session_minutes)} min · ${esc(s.new_words_per_session)} new words per session · break reminder ${s.break_reminder ? "on" : "off"}</div>
            <div class="muted">Lists: ${names.length ? names.map((n) => esc(n)).join(", ") : "none yet"}</div>
          </div>
          <a class="btn btn-secondary pbtn" href="#/parent/profiles/${esc(encodeURIComponent(p.id))}">Edit</a>
          <a class="btn btn-ghost pbtn" href="#/parent/stats/${esc(encodeURIComponent(p.id))}">Progress</a>
        </div>`;
    })
    .join("");
  body.innerHTML = `
    ${cards || `<div class="card"><p class="muted" style="margin:0">No learner profiles yet. Add one to get started.</p></div>`}
    <a class="btn btn-primary" href="#/parent/profiles/new">+ Add profile</a>`;
}

async function renderForm(root, ctx, id) {
  const body = frame(root, ctx, "profiles", id ? "Edit profile" : "Add profile");
  body.innerHTML = `<p class="muted">Loading…</p>`;
  let data;
  try {
    data = await loadAll();
  } catch (e) {
    errorBox(body, e);
    return;
  }
  if (!stillOn(ctx)) return;
  const existing = id ? data.profiles.find((p) => p.id === id) : null;
  if (id && !existing) {
    body.innerHTML = `<div class="card"><p>That profile no longer exists.</p><a class="btn btn-secondary" href="#/parent/profiles">← Profiles</a></div>`;
    return;
  }
  const s = (existing && existing.settings) || {};
  const listIds = new Set(data.lists.map((l) => l.id));
  const state = {
    name: existing ? existing.name : "",
    avatar: existing ? existing.avatar : AVATARS[0],
    band: existing ? existing.band : "6-8",
    session_minutes: s.session_minutes ?? 15,
    new_words_per_session: s.new_words_per_session ?? 5,
    break_reminder: s.break_reminder ?? true,
    break_message: s.break_message ?? DEFAULT_BREAK_MESSAGE,
    order: existing ? (existing.list_ids || []).filter((lid) => listIds.has(lid)) : [],
  };
  const avatars = AVATARS.includes(state.avatar) ? AVATARS : [state.avatar, ...AVATARS];
  body.innerHTML = `
    <form class="card" id="pform" novalidate>
      <label class="pfield"><span>Name</span><input type="text" name="name" maxlength="30" required value="${esc(state.name)}"></label>
      <div class="pfield"><span id="avatar-label">Avatar</span>
        <div class="avatars" role="group" aria-labelledby="avatar-label">
          ${avatars.map((a) => `<button type="button" class="avatar-opt" data-avatar="${esc(a)}" aria-pressed="${a === state.avatar ? "true" : "false"}">${esc(a)}</button>`).join("")}
        </div>
      </div>
      <label class="pfield"><span>Grade band</span>
        <select name="band">${BANDS.map((b) => `<option value="${esc(b)}"${b === state.band ? " selected" : ""}>${esc(BAND_LABEL[b])}</option>`).join("")}</select></label>
      <p class="muted" id="band-note" hidden>Changing the band starts preparing this profile's words for the new band. Stars are kept.</p>
      <label class="pfield"><span>Session length in minutes (5–30)</span>
        <input type="number" name="session_minutes" min="5" max="30" step="1" inputmode="numeric" value="${esc(state.session_minutes)}"></label>
      <label class="pfield"><span>New words per session (0–10)</span>
        <input type="number" name="new_words_per_session" min="0" max="10" step="1" inputmode="numeric" value="${esc(state.new_words_per_session)}"></label>
      <label class="pcheck"><input type="checkbox" name="break_reminder"${state.break_reminder ? " checked" : ""}> Show a break reminder after each session</label>
      <label class="pfield"><span>Break message</span>
        <input type="text" name="break_message" maxlength="200" value="${esc(state.break_message)}"${state.break_reminder ? "" : " disabled"}></label>
      <div class="pfield"><span>Word lists (studied in this order)</span><div id="plists"></div></div>
      <p class="perr" role="alert" aria-live="polite"></p>
      <button class="btn btn-primary" type="submit">${id ? "Save changes" : "Create profile"}</button>
      <a class="btn btn-ghost" href="#/parent/profiles">Cancel</a>
    </form>
    ${existing ? dangerHtml(existing) : ""}`;

  const form = body.querySelector("#pform");
  const field = (n) => form.querySelector(`[name="${n}"]`);
  const errEl = form.querySelector(".perr");
  const submitBtn = form.querySelector('button[type="submit"]');

  form.querySelectorAll(".avatar-opt").forEach((b) =>
    b.addEventListener("click", () => {
      state.avatar = b.dataset.avatar;
      form.querySelectorAll(".avatar-opt").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
    }),
  );
  field("band").addEventListener("change", () => {
    body.querySelector("#band-note").hidden = !existing || field("band").value === existing.band;
  });
  field("break_reminder").addEventListener("change", () => {
    field("break_message").disabled = !field("break_reminder").checked;
  });
  renderListPicker(body.querySelector("#plists"), state, data.lists);

  let savedId = id;
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    state.name = field("name").value;
    state.band = field("band").value;
    state.session_minutes = field("session_minutes").value;
    state.new_words_per_session = field("new_words_per_session").value;
    state.break_reminder = field("break_reminder").checked;
    state.break_message = field("break_message").value;
    const errs = validateProfile(state);
    if (errs.length) {
      errEl.textContent = errs.join(" ");
      return;
    }
    errEl.textContent = "";
    submitBtn.disabled = true;
    try {
      const payload = profilePayload(state);
      if (savedId) {
        await api.patch(`/api/parent/profiles/${encodeURIComponent(savedId)}`, payload);
      } else {
        const created = await api.post("/api/parent/profiles", payload);
        savedId = (created && created.id) || (created && created.profile && created.profile.id);
      }
      await api.put(`/api/parent/profiles/${encodeURIComponent(savedId)}/lists`, { list_ids: state.order });
      toast(id ? "Profile saved ✓" : "Profile created ✓");
      go(ctx, "#/parent/profiles");
    } catch (e) {
      showError(errEl, e);
    } finally {
      submitBtn.disabled = false;
    }
  });

  if (existing) wireDelete(body, ctx, existing);
}

function renderListPicker(el, state, lists) {
  if (!lists.length) {
    el.innerHTML = `<p class="muted" style="margin:0">No word lists yet — create one under “Word lists”, then come back to assign it.</p>`;
    return;
  }
  const byId = new Map(lists.map((l) => [l.id, l]));
  const assigned = state.order.filter((lid) => byId.has(lid));
  const others = lists.filter((l) => !assigned.includes(l.id));
  const count = (l) => `<span class="muted">(${(l.words || []).length} ${(l.words || []).length === 1 ? "word" : "words"})</span>`;
  el.innerHTML =
    assigned
      .map((lid, i) => {
        const l = byId.get(lid);
        return `
          <div class="prow">
            <label class="pcheck grow"><input type="checkbox" data-list="${esc(lid)}" checked> ${i + 1}. ${esc(l.name)} ${count(l)}</label>
            <span class="porder">
              <button type="button" data-move="${i}" data-delta="-1" aria-label="Move ${esc(l.name)} up"${i === 0 ? " disabled" : ""}>↑</button>
              <button type="button" data-move="${i}" data-delta="1" aria-label="Move ${esc(l.name)} down"${i === assigned.length - 1 ? " disabled" : ""}>↓</button>
            </span>
          </div>`;
      })
      .join("") +
    others
      .map((l) => `<div class="prow"><label class="pcheck grow"><input type="checkbox" data-list="${esc(l.id)}"> ${esc(l.name)} ${count(l)}</label></div>`)
      .join("");
  el.querySelectorAll("input[data-list]").forEach((cb) =>
    cb.addEventListener("change", () => {
      state.order = toggleId(state.order, cb.dataset.list, cb.checked);
      renderListPicker(el, state, lists);
    }),
  );
  el.querySelectorAll("button[data-move]").forEach((b) =>
    b.addEventListener("click", () => {
      state.order = moveItem(state.order, Number(b.dataset.move), Number(b.dataset.delta));
      renderListPicker(el, state, lists);
    }),
  );
}

function dangerHtml(p) {
  return `
    <div class="card danger">
      <h3 style="margin:0 0 6px">Delete this profile</h3>
      <p class="muted" style="margin:0 0 8px">This deletes <b>${esc(p.name)}</b> with all of their stars, progress and session history.
        Word lists are kept. This cannot be undone.</p>
      <label class="pfield"><span>Type the name “${esc(p.name)}” to confirm</span>
        <input type="text" id="pdel-name" autocomplete="off" autocapitalize="off"></label>
      <button type="button" class="btn pbtn-danger" id="pdel" disabled>Delete ${esc(p.name)}</button>
      <p class="perr" id="pdel-err" role="alert" aria-live="polite"></p>
    </div>`;
}

function wireDelete(body, ctx, p) {
  const input = body.querySelector("#pdel-name");
  const btn = body.querySelector("#pdel");
  const err = body.querySelector("#pdel-err");
  input.addEventListener("input", () => {
    btn.disabled = !confirmMatches(input.value, p.name);
  });
  btn.addEventListener("click", async () => {
    if (!confirmMatches(input.value, p.name)) return;
    btn.disabled = true;
    try {
      await api.del(`/api/parent/profiles/${encodeURIComponent(p.id)}`);
      toast(`Deleted ${p.name}`);
      go(ctx, "#/parent/profiles");
    } catch (e) {
      showError(err, e);
      btn.disabled = false;
    }
  });
}
