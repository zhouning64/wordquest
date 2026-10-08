// Pure helpers for the Parent area. No DOM access and no imports, so Node's test runner can load this file
// (tests/js/parent_helpers.test.mjs). Rendering code lives in the screen modules and always escapes with esc().

export const BANDS = ["3-5", "6-8", "9-12"];
export const BAND_LABEL = { "3-5": "Grades 3–5", "6-8": "Grades 6–8", "9-12": "Grades 9–12" };
export const QTYPES = ["meaning", "pick_word", "fill_blank", "usage", "scenario", "synonym", "antonym", "spell_it", "word_parts"];
export const QTYPE_LABEL = {
  meaning: "Meaning",
  pick_word: "Pick the word",
  fill_blank: "Fill the blank",
  usage: "Usage",
  scenario: "Scenario",
  synonym: "Synonym",
  antonym: "Antonym",
  spell_it: "Spell it",
  word_parts: "Word parts",
};
export const TIER = { meaning: 1, pick_word: 1, fill_blank: 1, usage: 2, scenario: 2, synonym: 2, antonym: 2, spell_it: 3, word_parts: 3 };
export const AVATARS = ["🙂", "😎", "🦊", "🐼", "🐯", "🦁", "🐸", "🐵", "🦄", "🐙", "🐢", "🦖", "🚀", "⚽", "🎨", "🌟"];
export const REGEN_PARTS = [
  { part: "all", label: "Everything (card, questions, picture)" },
  { part: "learn", label: "Learn card + questions" },
  { part: "questions", label: "Questions only" },
  { part: "image", label: "Picture only" },
];
export const POLL_MS = 3000;
export const MAX_LIST_WORDS = 600;
export const DEFAULT_BREAK_MESSAGE = "Take a 10-minute break — look at something far away.";

const FRIENDLY = {
  parent_disabled: "The Parent area is turned off. Set PARENT_PASSCODE in the .env file and restart the server.",
  parent_login_required: "Please log in to the Parent area again.",
  access_code_required: "Please enter the site access code first.",
  network_error: "Can't reach WordQuest. Check the connection and try again.",
  too_many_attempts: "Too many wrong tries. Wait 15 minutes and try again.",
  confirm_required: "Tick the confirmation box before importing.",
  invalid_json: "That file isn't valid JSON.",
  busy: "WordQuest is still finishing a word in the background. Wait a minute and import again.",
  profile_not_found: "That profile no longer exists.",
  list_not_found: "That word list no longer exists.",
  content_not_found: "That word has no content for this grade band yet.",
  unknown_band: "Unknown grade band.",
};

// Hrefs for Task 20's router: "#/parent/<screen>/<params…>", each param URI-encoded (the router decodes them).
export function previewHref(band, word) {
  return `#/parent/preview/${encodeURIComponent(band)}/${encodeURIComponent(word)}`;
}

export function listHref(listId, band) {
  const base = `#/parent/lists/${encodeURIComponent(listId)}`;
  return band ? `${base}/${encodeURIComponent(band)}` : base;
}

// Accepts a bare JSON array or an object wrapping the array under one of `keys`.
export function asRows(res, ...keys) {
  if (Array.isArray(res)) return res;
  if (res && typeof res === "object") {
    for (const k of keys) if (Array.isArray(res[k])) return res[k];
  }
  return [];
}

export function profilesForList(profiles, listId) {
  return (profiles || []).filter((p) => (p.list_ids || []).includes(listId));
}

export function bandsForList(profiles, listId) {
  const used = new Set(profilesForList(profiles, listId).map((p) => p.band));
  return BANDS.filter((b) => used.has(b));
}

export function rowsByWord(rows) {
  const m = new Map();
  for (const r of rows || []) m.set(r.word, r);
  return m;
}

export function readiness(words, rows) {
  const by = rowsByWord(rows);
  let ready = 0;
  for (const w of words || []) {
    const r = by.get(w);
    if (r && r.status === "ready") ready += 1;
  }
  return { ready, total: (words || []).length };
}

export function readinessText(words, rows) {
  const { ready, total } = readiness(words, rows);
  return `${ready} / ${total} ready`;
}

export function hasContent(row) {
  return Boolean(row && row.status && row.status !== "none" && row.status !== "missing");
}

// Status chips are rendered as <span class="pbadge {cls}">.
export function badge(row) {
  if (!hasContent(row)) return { label: "not started", cls: "is-none" };
  if (row.status === "ready") {
    return row.regenerating ? { label: "ready · updating", cls: "is-busy" } : { label: "ready", cls: "is-ready" };
  }
  if (row.status === "pending") return { label: "preparing", cls: "is-busy" };
  if (row.status === "failed") return { label: "failed", cls: "is-failed" };
  return { label: String(row.status), cls: "is-none" };
}

export function imageBadge(row) {
  const s = row && row.image_status;
  if (s === "ready") return { label: "🖼 picture", cls: "is-ready" };
  if (s === "pending") return { label: "🖼 drawing…", cls: "is-busy" };
  if (s === "failed") return { label: "🖼 failed", cls: "is-failed" };
  return { label: "🖼 emoji", cls: "is-none" };
}

// Why a picture failed (Task 18 `image_error` on content rows and on the Preview payload); "" unless it failed.
export function imageErrorText(row) {
  if (!row || row.image_status !== "failed") return "";
  return String(row.image_error || "").trim();
}

export function poolSize(row) {
  return Number((row && (row.pool_size ?? row.pool)) || 0);
}

// Everything a word row on the list page is drawn from, as one string. The queue poll redraws the rows only when
// the signatures of the words on screen changed (see redrawAction), so unrelated polls never rebuild the controls.
export function rowSignature(word, row) {
  if (!row) return JSON.stringify([word, null]);
  return JSON.stringify([
    word,
    row.status ?? null,
    row.image_status ?? null,
    poolSize(row),
    Boolean(row.regenerating),
    row.error ?? "",
    row.image_error ?? "",
  ]);
}

export function rowSignatures(words, rows) {
  const by = rowsByWord(rows);
  return (words || []).map((w) => rowSignature(w, by.get(w)));
}

// What to do with the word rows after content was fetched. `drawn` = signatures of the rows on screen (null before
// the first draw), `next` = signatures of the fresh data. "force" is for redraws the parent just caused themselves
// (add/remove a word, retry, regenerate): they always redraw. Polls redraw only when something changed ("unchanged"
// otherwise) and wait ("defer", retried on the next tick) while focus is inside the rows (`busy`), so an open
// "Regenerate…" menu or a focused Retry/✕/Preview control is never destroyed under the parent's hands.
export function redrawAction(drawn, next, { force = false, busy = false } = {}) {
  if (force || !drawn) return "redraw";
  const same = drawn.length === next.length && drawn.every((sig, i) => sig === next[i]);
  if (same) return "unchanged";
  return busy ? "defer" : "redraw";
}

// "resume": generation failed → PATCH the list's assignment again, so on_lists_changed → ensure_generation
// resumes from the failed stage. "image": only the picture failed → regenerate {part: "image"}.
export function retryAction(row) {
  if (!row) return null;
  if (row.status === "failed") return "resume";
  if (row.image_status === "failed") return "image";
  return null;
}

export function moveItem(arr, index, delta) {
  const out = arr.slice();
  const j = index + delta;
  if (index < 0 || index >= out.length || j < 0 || j >= out.length) return out;
  [out[index], out[j]] = [out[j], out[index]];
  return out;
}

export function toggleId(order, id, on) {
  if (on) return order.includes(id) ? order.slice() : [...order, id];
  return order.filter((x) => x !== id);
}

export function pct(x) {
  if (x === null || x === undefined || x === "" || Number.isNaN(Number(x))) return "—";
  const n = Number(x);
  return `${Math.round(n <= 1 ? n * 100 : n)}%`;
}

export function errText(e) {
  if (!e) return "Something went wrong.";
  const d = e.detail;
  if (typeof d === "string" && d) return FRIENDLY[d] || d;
  if (Array.isArray(d)) return d.map((x) => (x && x.msg ? x.msg : String(x))).join("; ");
  if (d && typeof d === "object") return d.message || JSON.stringify(d);
  return e.message || "Something went wrong.";
}

// GET /api/parent/queue → banner state. Shape (Task 18): {counts: {pending, running, done, failed}, ai_calls_today,
// ai_daily_limit, deferred_to_tomorrow}.
export function queueInfo(q) {
  const src = (q && q.counts) || q || {};
  const num = (v) => Number(v || 0);
  const pending = num(src.pending);
  const running = num(src.running);
  const failed = num(src.failed);
  const done = num(src.done);
  const calls = num(q && q.ai_calls_today);
  const limit = num(q && q.ai_daily_limit);
  const paused = Boolean(q && q.deferred_to_tomorrow);
  const active = pending + running > 0;
  let text = "";
  if (paused) {
    text = `Paused until tomorrow — today's AI limit is used up (${calls} / ${limit} calls). ${pending + running} job(s) will continue tomorrow.`;
  } else if (active) {
    text = `Preparing words… ${running} working, ${pending} waiting.` + (limit ? ` AI calls today: ${calls} / ${limit}.` : "");
  }
  return { pending, running, failed, done, calls, limit, paused, active, text };
}

export function shouldPoll(info) {
  return Boolean(info && info.active && !info.paused);
}

// GET /api/parent/status (Task 18): {ai_enabled, model, image_provider, image_model, image_config_error, parent_enabled}.
export function aiConfigured(status) {
  return Boolean(status && status.ai_enabled);
}

// Why pictures are off although the .env asks for them (bad IMAGE_* settings, Task 16); "" when the settings are fine.
export function imageConfigError(status) {
  return String((status && status.image_config_error) || "").trim();
}

export function imageProviderText(status) {
  if (imageConfigError(status)) return "off — the picture settings in .env need fixing (learners see emoji pictures)";
  const p = (status && status.image_provider) || "none";
  if (p === "none") return "none — learners see emoji pictures";
  return status.image_model ? `${p} (${status.image_model})` : p;
}

function codePoints(s) {
  return [...String(s)].length;
}

export function validateProfile(f) {
  const errs = [];
  const name = String(f.name || "").trim();
  if (!name) errs.push("Name is required.");
  else if (codePoints(name) > 30) errs.push("Name must be 30 characters or fewer.");
  if (!BANDS.includes(f.band)) errs.push("Pick a grade band.");
  const m = Number(f.session_minutes);
  if (String(f.session_minutes).trim() === "" || !Number.isInteger(m) || m < 5 || m > 30) {
    errs.push("Session length must be a whole number of minutes from 5 to 30.");
  }
  const nw = Number(f.new_words_per_session);
  if (String(f.new_words_per_session).trim() === "" || !Number.isInteger(nw) || nw < 0 || nw > 10) {
    errs.push("New words per session must be a whole number from 0 to 10.");
  }
  if (f.break_reminder && !String(f.break_message || "").trim()) {
    errs.push("Write a break message or turn the break reminder off.");
  }
  return errs;
}

export function profilePayload(f) {
  return {
    name: String(f.name || "").trim(),
    avatar: f.avatar || "🙂",
    band: f.band,
    settings: {
      session_minutes: Number(f.session_minutes),
      new_words_per_session: Number(f.new_words_per_session),
      break_reminder: Boolean(f.break_reminder),
      break_message: String(f.break_message || "").trim() || DEFAULT_BREAK_MESSAGE,
    },
  };
}

export function confirmMatches(typed, name) {
  return String(typed || "").trim() === String(name || "") && String(name || "") !== "";
}

export function splitEntries(text) {
  return String(text || "")
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function typeRows(byType) {
  let rows = [];
  if (Array.isArray(byType)) {
    rows = byType.map((r) => ({ type: r.type, answered: Number(r.answered || 0), correct: Number(r.correct || 0), accuracy: r.accuracy }));
  } else if (byType && typeof byType === "object") {
    rows = Object.entries(byType).map(([type, r]) => ({ type, answered: Number(r.answered || 0), correct: Number(r.correct || 0), accuracy: r.accuracy }));
  }
  rows = rows.map((r) => ({ ...r, accuracy: r.accuracy ?? (r.answered ? r.correct / r.answered : null) }));
  const order = (t) => {
    const i = QTYPES.indexOf(t);
    return i < 0 ? 99 : i;
  };
  return rows.sort((a, b) => order(a.type) - order(b.type));
}

export function sessionRow(s) {
  const answered = Number(s.answered || 0);
  const correct = Number(s.correct || 0);
  return {
    date: s.local_date || "",
    minutes: Number(s.minutes ?? s.active_minutes ?? 0),
    answered,
    accuracy: s.accuracy ?? (answered ? correct / answered : null),
    mode: s.mode || "normal",
  };
}

export function weakRow(w) {
  return { word: w.word, stage: Number(w.stage || 0), misses: Number(w.misses ?? (Number(w.wrong || 0) + Number(w.unsure || 0))) };
}

// GET /api/parent/profiles/{id}/stats (Task 18 profile_stats): {counts: {mastered, learning, new, due_today, total},
// accuracy: {overall, answered, correct, by_type}, unsure_rate, days_practiced_30, weakest, recent_sessions}.
export function statsCounts(s) {
  const counts = (s && s.counts) || {};
  const acc = (s && s.accuracy) || {};
  const num = (v) => Number(v || 0);
  return {
    mastered: num(counts.mastered),
    learning: num(counts.learning),
    new: num(counts.new),
    due_today: num(counts.due_today),
    accuracy: acc.overall ?? null,
    answered: num(acc.answered),
    unsure_rate: (s && s.unsure_rate) ?? null,
    days_practiced_30: num(s && s.days_practiced_30),
  };
}

export function starText(n) {
  const k = Math.max(0, Math.min(5, Number(n || 0)));
  return "★".repeat(k) + "☆".repeat(5 - k);
}

// Returns null when `text` looks like a WordQuest export, otherwise a message for the parent.
export function checkBackupFile(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch (e) {
    return "That file isn't valid JSON. Choose the .json file made by “Download backup”.";
  }
  if (!data || data.format !== "wordquest-backup") return "That file isn't a WordQuest backup.";
  if (data.version !== 1) return `This backup is version ${String(data.version)}; this app can only import version 1.`;
  return null;
}

// Import response (Task 18): {ok: true, counts: {profiles, lists, contents, ...}} → "2 profiles, 1 lists, …".
export function countsText(counts) {
  return Object.entries(counts || {})
    .filter(([, v]) => typeof v === "number")
    .map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`)
    .join(", ");
}

// `escaped` must already be HTML-escaped; only the blank marker is turned into markup.
export function blankHtml(escaped) {
  return String(escaped).replace(/_{3,}/g, '<span class="blank">___</span>');
}
