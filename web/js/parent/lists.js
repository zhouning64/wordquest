// #/parent/lists (all lists + readiness), #/parent/lists/new (create), #/parent/lists/<id>[/<band>] (edit + word rows).
import { api } from "../api.js";
import { esc, toast } from "../ui.js";
import {
  BAND_LABEL,
  MAX_LIST_WORDS,
  REGEN_PARTS,
  aiConfigured,
  asRows,
  badge,
  bandsForList,
  hasContent,
  imageBadge,
  imageErrorText,
  listHref,
  poolSize,
  previewHref,
  profilesForList,
  readinessText,
  redrawAction,
  retryAction,
  rowsByWord,
  rowSignatures,
  splitEntries,
} from "./helpers.js";
import { errorBox, frame, go, queueBanner, reload, showAiOff, showError, stillOn, toastError } from "./shell.js";

// Rejected entries from the most recent create, shown once on the list page that follows.
const lastRejected = new Map();

export async function render(root, ctx) {
  const [id, band] = ctx.params;
  if (!id) return renderIndex(root, ctx);
  if (id === "new") return renderCreate(root, ctx);
  return renderDetail(root, ctx, id, band);
}

async function fetchContent(listId, band) {
  const res = await api.get(`/api/parent/content?list_id=${encodeURIComponent(listId)}&band=${encodeURIComponent(band)}`);
  return asRows(res, "words", "items");
}

async function fetchStatus() {
  try {
    return await api.get("/api/parent/status");
  } catch (e) {
    return null;
  }
}

function wordCount(words) {
  const n = (words || []).length;
  return `${n} ${n === 1 ? "word" : "words"}`;
}

function listUrl(id) {
  return `/api/parent/lists/${encodeURIComponent(id)}`;
}

function regenUrl(band, word) {
  return `/api/parent/content/${encodeURIComponent(band)}/${encodeURIComponent(word)}/regenerate`;
}

function rejectedHtml(rejected) {
  if (!rejected || !rejected.length) return "";
  const n = rejected.length;
  return `
    <div class="rejected" role="status">
      <b>${n} ${n === 1 ? "entry was" : "entries were"} not added:</b>
      <ul>${rejected.map((r) => `<li><b>${esc(r.entry)}</b> — ${esc(r.reason)}</li>`).join("")}</ul>
    </div>`;
}

// ---------- all lists ----------

async function renderIndex(root, ctx) {
  const body = frame(root, ctx, "lists", "Word lists");
  body.innerHTML = `
    <div class="pbanner" id="qbanner" hidden aria-live="polite"></div>
    <div id="lists-body"><p class="muted">Loading…</p></div>
    <a class="btn btn-primary" href="#/parent/lists/new">+ New list</a>`;
  const out = body.querySelector("#lists-body");

  const draw = async () => {
    const [listsRes, profRes] = await Promise.all([api.get("/api/parent/lists"), api.get("/api/parent/profiles")]);
    const lists = asRows(listsRes, "lists");
    const profiles = asRows(profRes, "profiles");
    const summaries = await Promise.all(
      lists.map(async (l) => {
        const bands = bandsForList(profiles, l.id);
        const per = await Promise.all(bands.map(async (b) => ({ band: b, text: readinessText(l.words || [], await fetchContent(l.id, b)) })));
        return { list: l, per, who: profilesForList(profiles, l.id) };
      }),
    );
    if (!stillOn(ctx)) return;
    out.innerHTML =
      summaries
        .map(({ list, per, who }) => `
          <div class="card prow">
            <div class="grow">
              <b style="font-size:17px">${esc(list.name)}</b>
              <div class="muted">${wordCount(list.words)} · ${who.length ? who.map((p) => `${esc(p.avatar)} ${esc(p.name)}`).join(", ") : "not assigned"}</div>
              <div>${
                per.length
                  ? per.map((x) => `<span class="pbadge is-none">${esc(BAND_LABEL[x.band])}: ${esc(x.text)}</span>`).join(" ")
                  : `<span class="muted">Not assigned to anyone yet — nothing is prepared until you assign it to a profile.</span>`
              }</div>
            </div>
            <a class="btn btn-secondary pbtn" href="${esc(listHref(list.id))}">Open</a>
          </div>`)
        .join("") || `<div class="card"><p class="muted" style="margin:0">No word lists yet.</p></div>`;
  };

  try {
    await draw();
  } catch (e) {
    errorBox(out, e);
    return;
  }
  const bannerEl = body.querySelector("#qbanner");
  const status = await fetchStatus();
  if (!stillOn(ctx)) return undefined;
  if (!aiConfigured(status)) {
    showAiOff(bannerEl);
    return undefined;
  }
  const banner = queueBanner(bannerEl, ctx, () => draw().catch(() => {}));
  banner.start();
  return () => banner.stop();
}

// ---------- create ----------

async function renderCreate(root, ctx) {
  const body = frame(root, ctx, "lists", "New word list");
  body.innerHTML = `<p class="muted">Loading…</p>`;
  let profiles;
  try {
    profiles = asRows(await api.get("/api/parent/profiles"), "profiles");
  } catch (e) {
    errorBox(body, e);
    return;
  }
  if (!stillOn(ctx)) return;
  body.innerHTML = `
    <form class="card" id="lform" novalidate>
      <label class="pfield"><span>List name</span>
        <input type="text" name="name" maxlength="60" required placeholder="e.g. Week 3 words"></label>
      <label class="pfield"><span>Words — one per line, or separated by commas (up to ${MAX_LIST_WORDS})</span>
        <textarea name="words" spellcheck="false" autocapitalize="off" placeholder="tenacious&#10;in lieu of&#10;frugal"></textarea></label>
      <p class="muted" id="lcount">0 entries</p>
      <div class="pfield"><span>Assign to (words start being prepared as soon as you save)</span>
        ${
          profiles.length
            ? profiles.map((p) => `<label class="pcheck"><input type="checkbox" name="assign" value="${esc(p.id)}"> ${esc(p.avatar)} ${esc(p.name)} <span class="muted">(${esc(BAND_LABEL[p.band] || p.band)})</span></label>`).join("")
            : `<p class="muted">No profiles yet — you can assign this list later.</p>`
        }
        <p class="muted">A list assigned to no profile has no grade band, so nothing is prepared until you assign it.</p>
      </div>
      <p class="perr" role="alert" aria-live="polite"></p>
      <button class="btn btn-primary" type="submit">Save list</button>
      <a class="btn btn-ghost" href="#/parent/lists">Cancel</a>
    </form>`;
  const form = body.querySelector("#lform");
  const ta = form.querySelector("textarea");
  const countEl = body.querySelector("#lcount");
  const errEl = form.querySelector(".perr");
  const btn = form.querySelector('button[type="submit"]');
  ta.addEventListener("input", () => {
    const n = splitEntries(ta.value).length;
    countEl.textContent = `${n} ${n === 1 ? "entry" : "entries"}` + (n > MAX_LIST_WORDS ? ` — only the first ${MAX_LIST_WORDS} words are kept` : "");
  });
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = form.querySelector('[name="name"]').value.trim();
    const words = splitEntries(ta.value);
    if (!name) {
      errEl.textContent = "Give the list a name.";
      return;
    }
    if (!words.length) {
      errEl.textContent = "Add at least one word.";
      return;
    }
    const assign = [...form.querySelectorAll('input[name="assign"]:checked')].map((c) => c.value);
    errEl.textContent = "";
    btn.disabled = true;
    try {
      const res = await api.post("/api/parent/lists", { name, words, assign_profile_ids: assign });
      const list = res.list || res;
      lastRejected.set(list.id, res.rejected || []);
      toast("List saved ✓");
      go(ctx, listHref(list.id));
    } catch (e) {
      showError(errEl, e);
    } finally {
      btn.disabled = false;
    }
  });
}

// ---------- one list ----------

function regenSelectHtml(word) {
  return `
    <select class="pregen" data-regen="${esc(word)}" aria-label="Regenerate ${esc(word)}">
      <option value="">↻ Regenerate…</option>
      ${REGEN_PARTS.map((p) => `<option value="${esc(p.part)}">${esc(p.label)}</option>`).join("")}
    </select>`;
}

function wordRowHtml(word, row, band) {
  const b = badge(row);
  const ib = imageBadge(row);
  const imageError = imageErrorText(row);
  const retry = band ? retryAction(row) : null;
  const exists = Boolean(band) && hasContent(row);
  const canRegen = exists && (row.status === "ready" || row.status === "failed");
  return `
    <div class="prow wordrow">
      <div class="grow">
        <b>${esc(word)}</b>
        ${band ? `<div><span class="pbadge ${b.cls}">${esc(b.label)}</span><span class="pbadge ${ib.cls}">${esc(ib.label)}</span> <span class="muted">${poolSize(row)} questions</span></div>` : ""}
        ${band && imageError ? `<span class="pimgerr">Picture: ${esc(imageError)}</span>` : ""}
      </div>
      ${exists ? `<a class="btn btn-ghost pbtn" href="${esc(previewHref(band, word))}">Preview</a>` : ""}
      ${canRegen ? regenSelectHtml(word) : ""}
      ${retry ? `<button type="button" class="btn btn-secondary pbtn" data-act="retry" data-retry="${esc(retry)}" data-word="${esc(word)}">${retry === "image" ? "Retry picture" : "Retry"}</button>` : ""}
      <button type="button" class="btn btn-ghost pbtn" data-act="remove" data-word="${esc(word)}" aria-label="Remove ${esc(word)} from this list">✕</button>
      ${row && row.error ? `<div class="werr">${esc(row.error)}</div>` : ""}
    </div>`;
}

async function renderDetail(root, ctx, id, bandParam) {
  const body = frame(root, ctx, "lists", "Word list");
  body.innerHTML = `<p class="muted">Loading…</p>`;
  let lists;
  let profiles;
  let status;
  try {
    const [lr, pr, st] = await Promise.all([api.get("/api/parent/lists"), api.get("/api/parent/profiles"), api.get("/api/parent/status")]);
    lists = asRows(lr, "lists");
    profiles = asRows(pr, "profiles");
    status = st;
  } catch (e) {
    errorBox(body, e);
    return;
  }
  if (!stillOn(ctx)) return;
  const list = lists.find((l) => l.id === id);
  if (!list) {
    body.innerHTML = `<div class="card"><p>That list no longer exists.</p><a class="btn btn-secondary" href="#/parent/lists">← Word lists</a></div>`;
    return;
  }
  list.words = list.words || [];
  const bands = bandsForList(profiles, id);
  const band = bands.includes(bandParam) ? bandParam : bands[0] || null;
  const assigned = new Set(profilesForList(profiles, id).map((p) => p.id));
  const rejected = lastRejected.get(id) || [];
  lastRejected.delete(id);

  body.innerHTML = `
    <div class="pbanner" id="qbanner" hidden aria-live="polite"></div>
    <div id="rejected">${rejectedHtml(rejected)}</div>
    <div class="card">
      <form id="lname" class="prow" style="border:0;padding:0" novalidate>
        <label class="pfield grow" style="margin:0"><span>List name</span>
          <input type="text" name="name" maxlength="60" value="${esc(list.name)}"></label>
        <button class="btn btn-secondary pbtn" type="submit">Rename</button>
      </form>
      <div class="pfield"><span>Assigned to</span>
        ${
          profiles.length
            ? profiles.map((p) => `<label class="pcheck"><input type="checkbox" name="assign" value="${esc(p.id)}"${assigned.has(p.id) ? " checked" : ""}> ${esc(p.avatar)} ${esc(p.name)} <span class="muted">(${esc(BAND_LABEL[p.band] || p.band)})</span></label>`).join("")
            : `<p class="muted">No profiles yet.</p>`
        }
        ${profiles.length ? `<button type="button" class="btn btn-secondary pbtn" id="lassign">Save assignment</button>` : ""}
      </div>
      <p class="perr" id="lhead-err" role="alert" aria-live="polite"></p>
    </div>
    <div class="card">
      <h3 style="margin:0 0 6px">Add words</h3>
      <form id="ladd" novalidate>
        <label class="pfield"><span>One per line, or separated by commas</span>
          <textarea name="add" spellcheck="false" autocapitalize="off" style="min-height:90px"></textarea></label>
        <button class="btn btn-secondary pbtn" type="submit">Add words</button>
        <p class="perr" role="alert" aria-live="polite"></p>
      </form>
    </div>
    <div class="card">
      <div class="prow" style="border:0;padding:0 0 6px">
        <h3 class="grow" style="margin:0" id="lheading"></h3>
        <span id="lready" class="pbadge is-none"></span>
      </div>
      ${bands.length > 1 ? `<nav class="ptabs" aria-label="Grade band">${bands.map((b) => `<a class="ptab${b === band ? " active" : ""}" href="${esc(listHref(id, b))}">${esc(BAND_LABEL[b])}</a>`).join("")}</nav>` : ""}
      ${band ? "" : `<p class="muted">Not assigned to anyone yet — tick a profile above and save, so the words start being prepared.</p>`}
      <div id="lwords"><p class="muted">Loading…</p></div>
    </div>`;

  const headErr = body.querySelector("#lhead-err");
  const wordsEl = body.querySelector("#lwords");
  const readyEl = body.querySelector("#lready");
  const headingEl = body.querySelector("#lheading");
  const bannerEl = body.querySelector("#qbanner");
  let rows = [];
  let banner = null;
  let drawn = null; // signatures of the word rows currently on screen (null until the first draw)

  // The queue poll calls this every few seconds while words are being prepared. The heading and the "x / y ready" chip
  // are plain text and always refreshed; the word rows (selects, buttons, links) are only rebuilt when their data
  // changed, and not while the parent is using one of them. `force` is for redraws caused by the parent's own action.
  const drawRows = ({ force = false } = {}) => {
    const by = rowsByWord(rows);
    headingEl.textContent = `Words (${list.words.length})`;
    readyEl.hidden = !band;
    readyEl.textContent = band ? `${BAND_LABEL[band]}: ${readinessText(list.words, rows)}` : "";
    const next = rowSignatures(list.words, band ? rows : []);
    const busy = wordsEl.contains(document.activeElement);
    if (redrawAction(drawn, next, { force, busy }) !== "redraw") return;
    wordsEl.innerHTML = list.words.map((w) => wordRowHtml(w, band ? by.get(w) : null, band)).join("") || `<p class="muted">This list is empty.</p>`;
    drawn = next;
  };
  const loadRows = async ({ force = false } = {}) => {
    if (band) rows = await fetchContent(id, band);
    if (stillOn(ctx)) drawRows({ force });
  };
  const afterChange = async () => {
    await loadRows({ force: true });
    if (banner) banner.kick();
  };

  body.querySelector("#lname").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = ev.target.querySelector('[name="name"]').value.trim();
    if (!name) {
      headErr.textContent = "The list needs a name.";
      return;
    }
    try {
      const res = await api.patch(listUrl(id), { name });
      list.name = (res.list || res).name || name;
      headErr.textContent = "";
      toast("Renamed ✓");
    } catch (e) {
      showError(headErr, e);
    }
  });

  const assignBtn = body.querySelector("#lassign");
  if (assignBtn) {
    assignBtn.addEventListener("click", async () => {
      const checked = [...body.querySelectorAll('input[name="assign"]:checked')].map((c) => c.value);
      assignBtn.disabled = true;
      try {
        // assign_profile_ids is the complete set of profiles that should study this list (Task 18).
        await api.patch(listUrl(id), { assign_profile_ids: checked });
        toast("Assignment saved ✓");
        reload(ctx);
      } catch (e) {
        showError(headErr, e);
        assignBtn.disabled = false;
      }
    });
  }

  body.querySelector("#ladd").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const form = ev.target;
    const ta = form.querySelector("textarea");
    const errEl = form.querySelector(".perr");
    const words = splitEntries(ta.value);
    if (!words.length) {
      errEl.textContent = "Type at least one word.";
      return;
    }
    try {
      const res = await api.patch(listUrl(id), { add_words: words });
      const updated = res.list || res;
      if (Array.isArray(updated.words)) list.words = updated.words;
      body.querySelector("#rejected").innerHTML = rejectedHtml(res.rejected || []);
      ta.value = "";
      errEl.textContent = "";
      toast("Words added ✓");
      await afterChange();
    } catch (e) {
      showError(errEl, e);
    }
  });

  wordsEl.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const word = btn.dataset.word;
    if (btn.dataset.act === "remove") {
      if (!window.confirm(`Remove “${word}” from ${list.name}? Learners keep their stars for it.`)) return;
      try {
        const res = await api.patch(listUrl(id), { remove_words: [word] });
        const updated = res.list || res;
        list.words = Array.isArray(updated.words) ? updated.words : list.words.filter((w) => w !== word);
        toast(`Removed ${word}`);
        drawRows({ force: true });
      } catch (e) {
        toastError(e);
      }
      return;
    }
    if (btn.dataset.act === "retry") {
      btn.disabled = true;
      try {
        if (btn.dataset.retry === "image") await api.post(regenUrl(band, word), { part: "image" });
        // Re-sending the unchanged assignment runs on_lists_changed → ensure_generation, which resumes failed words.
        else await api.patch(listUrl(id), { assign_profile_ids: [...assigned] });
        toast(`Trying ${word} again…`);
        await afterChange();
      } catch (e) {
        toastError(e);
        btn.disabled = false;
      }
    }
  });

  wordsEl.addEventListener("change", async (ev) => {
    const sel = ev.target.closest("select[data-regen]");
    if (!sel || !sel.value) return;
    const part = sel.value;
    const word = sel.dataset.regen;
    sel.value = "";
    const label = (REGEN_PARTS.find((p) => p.part === part) || { label: part }).label;
    if (!window.confirm(`Regenerate “${word}” — ${label}? Learners keep the current version until the new one is ready.`)) return;
    try {
      await api.post(regenUrl(band, word), { part });
      toast(`Regenerating ${word}…`);
      await afterChange();
    } catch (e) {
      toastError(e);
    }
  });

  try {
    await loadRows();
  } catch (e) {
    errorBox(wordsEl, e);
  }
  if (!stillOn(ctx)) return undefined;
  if (!aiConfigured(status)) {
    showAiOff(bannerEl);
    return undefined;
  }
  banner = queueBanner(bannerEl, ctx, () => loadRows().catch(() => {}));
  banner.start();
  return () => banner.stop();
}
