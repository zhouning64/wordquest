import { mock, test } from "node:test";
import assert from "node:assert/strict";
import {
  AVATARS,
  BANDS,
  aiConfigured,
  asRows,
  badge,
  bandsForList,
  blankHtml,
  checkBackupFile,
  confirmMatches,
  countsText,
  createRowRedrawer,
  errText,
  imageBadge,
  imageConfigError,
  imageErrorText,
  imageProviderText,
  listHref,
  moveItem,
  pct,
  previewHref,
  profilePayload,
  profilesForList,
  queueInfo,
  readiness,
  readinessText,
  redrawAction,
  retryAction,
  rowSignature,
  rowSignatures,
  sessionRow,
  shouldPoll,
  splitEntries,
  starText,
  statsCounts,
  toggleId,
  typeRows,
  validateProfile,
  weakRow,
} from "../../web/js/parent/helpers.js";
import { queueBanner } from "../../web/js/parent/shell.js";

test("previewHref and listHref build router hashes with each part URI-encoded", () => {
  assert.equal(previewHref("6-8", "in lieu of"), "#/parent/preview/6-8/in%20lieu%20of");
  assert.equal(previewHref("9-12", "self-esteem"), "#/parent/preview/9-12/self-esteem");
  assert.equal(listHref("L1"), "#/parent/lists/L1");
  assert.equal(listHref("L1", "9-12"), "#/parent/lists/L1/9-12");
  const parts = previewHref("3-5", "o'clock").replace(/^#\//, "").split("/").map(decodeURIComponent);
  assert.deepEqual(parts, ["parent", "preview", "3-5", "o'clock"]);
});

test("bandsForList returns the bands of assigned profiles in band order, without duplicates", () => {
  const profiles = [
    { id: "a", band: "9-12", list_ids: ["L1"] },
    { id: "b", band: "3-5", list_ids: ["L1", "L2"] },
    { id: "c", band: "9-12", list_ids: ["L1"] },
    { id: "d", band: "6-8", list_ids: ["L2"] },
  ];
  assert.deepEqual(bandsForList(profiles, "L1"), ["3-5", "9-12"]);
  assert.deepEqual(bandsForList(profiles, "L2"), ["3-5", "6-8"]);
  assert.deepEqual(bandsForList(profiles, "L3"), []);
  assert.deepEqual(profilesForList(profiles, "L2").map((p) => p.id), ["b", "d"]);
  assert.deepEqual(BANDS, ["3-5", "6-8", "9-12"]);
});

test("readiness counts only list words whose content is ready", () => {
  const rows = [
    { word: "a", status: "ready" },
    { word: "b", status: "pending" },
    { word: "zz", status: "ready" },
  ];
  assert.deepEqual(readiness(["a", "b", "c"], rows), { ready: 1, total: 3 });
  assert.equal(readinessText(["a", "b", "c"], rows), "1 / 3 ready");
  assert.equal(readinessText([], []), "0 / 0 ready");
});

test("asRows accepts a bare array or a wrapped one", () => {
  assert.deepEqual(asRows([1, 2]), [1, 2]);
  assert.deepEqual(asRows({ words: [3] }, "words", "items"), [3]);
  assert.deepEqual(asRows({ items: [4] }, "words", "items"), [4]);
  assert.deepEqual(asRows(null, "words"), []);
  assert.deepEqual(asRows({ other: 1 }, "words"), []);
});

test("badge describes content status, including regeneration in progress", () => {
  assert.deepEqual(badge(undefined), { label: "not started", cls: "is-none" });
  assert.deepEqual(badge({ word: "a", status: "missing" }), { label: "not started", cls: "is-none" });
  assert.deepEqual(badge({ status: "ready" }), { label: "ready", cls: "is-ready" });
  assert.deepEqual(badge({ status: "ready", regenerating: true }), { label: "ready · updating", cls: "is-busy" });
  assert.deepEqual(badge({ status: "pending" }), { label: "preparing", cls: "is-busy" });
  assert.deepEqual(badge({ status: "failed" }), { label: "failed", cls: "is-failed" });
  assert.deepEqual(imageBadge({ image_status: "ready" }), { label: "🖼 picture", cls: "is-ready" });
  assert.deepEqual(imageBadge({ image_status: "pending" }), { label: "🖼 drawing…", cls: "is-busy" });
  assert.deepEqual(imageBadge({ image_status: "failed" }), { label: "🖼 failed", cls: "is-failed" });
  assert.deepEqual(imageBadge({ image_status: "none" }), { label: "🖼 emoji", cls: "is-none" });
});

test("retryAction resumes failed words via the list and retries failed pictures via regenerate", () => {
  assert.equal(retryAction({ status: "failed", image_status: "none" }), "resume");
  assert.equal(retryAction({ status: "ready", image_status: "failed" }), "image");
  assert.equal(retryAction({ status: "ready", image_status: "ready" }), null);
  assert.equal(retryAction({ status: "pending", image_status: "none" }), null);
  assert.equal(retryAction(undefined), null);
});

test("imageErrorText shows why a picture failed, and nothing for other picture states", () => {
  const failed = { image_status: "failed", image_error: " LLMError: image provider HTTP 401: invalid key " };
  assert.equal(imageErrorText(failed), "LLMError: image provider HTTP 401: invalid key");
  assert.equal(imageErrorText({ image_status: "failed" }), "");
  assert.equal(imageErrorText({ image_status: "ready", image_error: "an old reason" }), "");
  assert.equal(imageErrorText(null), "");
});

test("imageConfigError and imageProviderText report picture settings that need fixing", () => {
  const reason = "IMAGE_BASE_URL is required when IMAGE_PROVIDER=openai_compatible (for z.ai use https://api.z.ai/api/paas/v4)";
  const broken = { ai_enabled: true, model: "gpt-oss-120b", image_provider: "none", image_model: "",
    image_config_error: reason, parent_enabled: true };
  assert.equal(imageConfigError(broken), reason);
  assert.equal(imageProviderText(broken), "off — the picture settings in .env need fixing (learners see emoji pictures)");
  assert.equal(imageConfigError({ ...broken, image_config_error: "" }), "");
  assert.equal(imageProviderText({ ...broken, image_config_error: "" }), "none — learners see emoji pictures");
  assert.equal(imageConfigError(null), "");
});

test("moveItem swaps neighbours and ignores moves past either end", () => {
  const src = ["a", "b", "c"];
  assert.deepEqual(moveItem(src, 0, 1), ["b", "a", "c"]);
  assert.deepEqual(moveItem(src, 2, -1), ["a", "c", "b"]);
  const same = moveItem(src, 0, -1);
  assert.deepEqual(same, ["a", "b", "c"]);
  assert.notEqual(same, src);
  assert.deepEqual(moveItem(src, 2, 1), ["a", "b", "c"]);
  assert.deepEqual(src, ["a", "b", "c"]);
});

test("toggleId appends when checked and removes when unchecked", () => {
  assert.deepEqual(toggleId(["a"], "b", true), ["a", "b"]);
  assert.deepEqual(toggleId(["a", "b"], "b", true), ["a", "b"]);
  assert.deepEqual(toggleId(["a", "b"], "a", false), ["b"]);
});

test("pct formats fractions and percents and shows a dash for missing values", () => {
  assert.equal(pct(null), "—");
  assert.equal(pct(undefined), "—");
  assert.equal(pct(0), "0%");
  assert.equal(pct(0.857), "86%");
  assert.equal(pct(1), "100%");
  assert.equal(pct(86), "86%");
});

test("errText turns ApiError details into readable text", () => {
  assert.match(errText({ status: 403, detail: "parent_disabled" }), /PARENT_PASSCODE/);
  assert.equal(errText({ status: 422, detail: [{ msg: "too short" }, { msg: "bad band" }] }), "too short; bad band");
  assert.equal(errText({ status: 400, detail: "unknown part" }), "unknown part");
  assert.equal(errText({ status: 400, detail: "confirm_required" }), "Tick the confirmation box before importing.");
  assert.match(errText({ status: 503, detail: "busy" }), /import again/);
  assert.equal(errText({ message: "boom" }), "boom");
  assert.equal(errText(null), "Something went wrong.");
});

test("queueInfo summarises job counts, AI calls and the daily pause", () => {
  const busy = queueInfo({ counts: { pending: 3, running: 2, done: 5, failed: 1 }, ai_calls_today: 40, ai_daily_limit: 2000, deferred_to_tomorrow: false });
  assert.equal(busy.active, true);
  assert.equal(busy.paused, false);
  assert.match(busy.text, /2 working, 3 waiting/);
  assert.match(busy.text, /40 \/ 2000/);
  assert.equal(shouldPoll(busy), true);

  const paused = queueInfo({ counts: { pending: 4, running: 0, done: 0, failed: 0 }, ai_calls_today: 2000, ai_daily_limit: 2000, deferred_to_tomorrow: true });
  assert.match(paused.text, /Paused until tomorrow/);
  assert.match(paused.text, /2000 \/ 2000/);
  assert.equal(shouldPoll(paused), false);

  const idle = queueInfo({ counts: { pending: 0, running: 0, done: 9, failed: 0 }, ai_calls_today: 12, ai_daily_limit: 2000 });
  assert.equal(idle.active, false);
  assert.equal(idle.text, "");
  assert.equal(shouldPoll(idle), false);
});

test("validateProfile enforces the spec limits", () => {
  const ok = { name: "Maya", band: "6-8", session_minutes: 15, new_words_per_session: 5, break_reminder: true, break_message: "Rest your eyes." };
  assert.deepEqual(validateProfile(ok), []);
  assert.deepEqual(validateProfile({ ...ok, name: "🦊".repeat(30) }), []);
  assert.ok(validateProfile({ ...ok, name: "  " }).includes("Name is required."));
  assert.equal(validateProfile({ ...ok, name: "x".repeat(31) }).length, 1);
  assert.equal(validateProfile({ ...ok, band: "1-2" }).length, 1);
  assert.equal(validateProfile({ ...ok, session_minutes: 4 }).length, 1);
  assert.equal(validateProfile({ ...ok, session_minutes: 31 }).length, 1);
  assert.equal(validateProfile({ ...ok, session_minutes: 7.5 }).length, 1);
  assert.deepEqual(validateProfile({ ...ok, session_minutes: "30", new_words_per_session: "0" }), []);
  assert.equal(validateProfile({ ...ok, new_words_per_session: 11 }).length, 1);
  assert.equal(validateProfile({ ...ok, new_words_per_session: -1 }).length, 1);
  assert.equal(validateProfile({ ...ok, break_message: "  " }).length, 1);
  assert.deepEqual(validateProfile({ ...ok, break_reminder: false, break_message: "" }), []);
});

test("profilePayload trims and converts form values", () => {
  const p = profilePayload({ name: "  Maya ", avatar: "🦊", band: "9-12", session_minutes: "20", new_words_per_session: "3", break_reminder: false, break_message: " " });
  assert.deepEqual(p, {
    name: "Maya",
    avatar: "🦊",
    band: "9-12",
    settings: { session_minutes: 20, new_words_per_session: 3, break_reminder: false, break_message: "Take a 10-minute break — look at something far away." },
  });
});

test("confirmMatches requires the exact profile name (surrounding spaces ignored)", () => {
  assert.equal(confirmMatches("  Maya ", "Maya"), true);
  assert.equal(confirmMatches("maya", "Maya"), false);
  assert.equal(confirmMatches("", "Maya"), false);
});

test("splitEntries splits on newlines and commas and drops blanks", () => {
  assert.deepEqual(splitEntries("tenacious\n in lieu of ,, frugal\n\n"), ["tenacious", "in lieu of", "frugal"]);
  assert.deepEqual(splitEntries("   "), []);
  assert.deepEqual(splitEntries("a\r\nb"), ["a", "b"]);
});

test("typeRows accepts a map or a list and orders rows by question type", () => {
  const fromMap = typeRows({ spell_it: { answered: 4, correct: 1 }, meaning: { answered: 10, correct: 9 }, usage: { answered: 0, correct: 0 } });
  assert.deepEqual(fromMap.map((r) => r.type), ["meaning", "usage", "spell_it"]);
  assert.equal(fromMap[0].accuracy, 0.9);
  assert.equal(fromMap[1].accuracy, null);
  assert.equal(fromMap[2].accuracy, 0.25);
  const fromList = typeRows([{ type: "antonym", answered: 2, correct: 2, accuracy: 1 }, { type: "pick_word", answered: 5, correct: 4 }]);
  assert.deepEqual(fromList.map((r) => [r.type, r.accuracy]), [["pick_word", 0.8], ["antonym", 1]]);
  assert.deepEqual(typeRows(undefined), []);
});

test("sessionRow, weakRow and statsCounts read the profile_stats payload", () => {
  assert.deepEqual(sessionRow({ id: "s1", local_date: "2026-10-07", mode: "normal", minutes: 12, answered: 20, correct: 15, unsure: 1, accuracy: 0.75, finished: true }),
    { date: "2026-10-07", minutes: 12, answered: 20, accuracy: 0.75, mode: "normal" });
  assert.deepEqual(sessionRow({ local_date: "2026-10-06", mode: "practice", minutes: 3, answered: 0, correct: 0, accuracy: null }),
    { date: "2026-10-06", minutes: 3, answered: 0, accuracy: null, mode: "practice" });
  assert.deepEqual(sessionRow({ local_date: "2026-10-05", active_minutes: 9, answered: 4, correct: 3 }),
    { date: "2026-10-05", minutes: 9, answered: 4, accuracy: 0.75, mode: "normal" });
  assert.deepEqual(weakRow({ word: "quell", stage: 1, wrong: 3, unsure: 1, misses: 4, seen: 6, due_date: "2026-10-08" }), { word: "quell", stage: 1, misses: 4 });
  assert.deepEqual(weakRow({ word: "lucid", stage: 0, wrong: 2, unsure: 1 }), { word: "lucid", stage: 0, misses: 3 });
  const stats = {
    counts: { mastered: 2, learning: 5, new: 13, due_today: 4, total: 20 },
    accuracy: { overall: 0.8, answered: 50, correct: 40, by_type: { meaning: { answered: 10, correct: 9, accuracy: 0.9 } } },
    unsure_rate: 0.1,
    days_practiced_30: 4,
  };
  assert.deepEqual(statsCounts(stats), { mastered: 2, learning: 5, new: 13, due_today: 4, accuracy: 0.8, answered: 50, unsure_rate: 0.1, days_practiced_30: 4 });
  const empty = statsCounts({ counts: { mastered: 0, learning: 0, new: 3, due_today: 0, total: 3 }, accuracy: { overall: null, answered: 0, correct: 0, by_type: {} }, unsure_rate: null, days_practiced_30: 0 });
  assert.equal(empty.accuracy, null);
  assert.equal(empty.unsure_rate, null);
  assert.equal(empty.new, 3);
});

test("starText draws five stars", () => {
  assert.equal(starText(0), "☆☆☆☆☆");
  assert.equal(starText(3), "★★★☆☆");
  assert.equal(starText(9), "★★★★★");
});

test("checkBackupFile accepts only WordQuest backup JSON", () => {
  assert.equal(checkBackupFile('{"format":"wordquest-backup","version":1,"profiles":[]}'), null);
  assert.match(checkBackupFile('{"format":"something-else"}'), /isn't a WordQuest backup/);
  assert.match(checkBackupFile("not json"), /isn't valid JSON/);
  assert.match(checkBackupFile('{"format":"wordquest-backup","version":2}'), /version/);
});

test("countsText lists numeric import counts", () => {
  assert.equal(countsText({ profiles: 2, lists: 1, contents: 24, images_missing: 0 }), "2 profiles, 1 lists, 24 contents, 0 images missing");
  assert.equal(countsText({ ok: true, note: "x" }), "");
  assert.equal(countsText({}), "");
});

test("blankHtml highlights the blank in an already-escaped prompt", () => {
  assert.equal(blankHtml("The ___ dog &amp; cat"), 'The <span class="blank">___</span> dog &amp; cat');
  assert.equal(blankHtml("no blank"), "no blank");
});

test("status helpers read the parent status payload", () => {
  const off = { ai_enabled: true, model: "gpt-oss-120b", image_provider: "none", image_model: "", image_config_error: "",
    parent_enabled: true };
  assert.equal(aiConfigured(off), true);
  assert.equal(aiConfigured({ ai_enabled: false }), false);
  assert.equal(aiConfigured(null), false);
  assert.equal(imageProviderText(off), "none — learners see emoji pictures");
  assert.equal(imageProviderText({ ...off, image_provider: "openai_compatible", image_model: "glm-image" }),
    "openai_compatible (glm-image)");
  assert.equal(imageProviderText({ ...off, image_provider: "openai_compatible" }), "openai_compatible");
  assert.equal(imageProviderText(null), "none — learners see emoji pictures");
});

test("AVATARS offers 16 distinct emoji", () => {
  assert.equal(AVATARS.length, 16);
  assert.equal(new Set(AVATARS).size, 16);
});

test("rowSignature changes with every field a word row is drawn from, and with nothing else", () => {
  const row = { word: "tenacious", status: "ready", image_status: "ready", pool_size: 5, regenerating: false, error: "", image_error: "" };
  const base = rowSignature("tenacious", row);
  assert.equal(rowSignature("tenacious", { ...row }), base);
  assert.equal(rowSignature("tenacious", { ...row, band: "6-8", content_version: 2, source: "legacy" }), base);
  assert.equal(rowSignature("tenacious", { ...row, regenerating: undefined }), base);
  assert.equal(rowSignature("tenacious", { ...row, pool: undefined, error: undefined, image_error: undefined }), base);
  const variants = [
    { status: "failed" },
    { image_status: "failed" },
    { pool_size: 6 },
    { regenerating: true },
    { error: "boom" },
    { image_error: "HTTP 401" },
  ];
  const sigs = new Set([base]);
  for (const v of variants) sigs.add(rowSignature("tenacious", { ...row, ...v }));
  assert.equal(sigs.size, variants.length + 1);
  assert.notEqual(rowSignature("frugal", row), base);
  assert.notEqual(rowSignature("tenacious", undefined), base);
  assert.equal(rowSignature("tenacious", undefined), rowSignature("tenacious", null));
  assert.equal(rowSignature("tenacious", { pool: 4 }), rowSignature("tenacious", { pool_size: 4 }));
});

test("rowSignatures follows the list's words, including words that have no content row yet", () => {
  const rows = [{ word: "a", status: "ready", pool_size: 5 }, { word: "zz", status: "ready" }];
  const sigs = rowSignatures(["a", "b"], rows);
  assert.equal(sigs.length, 2);
  assert.equal(sigs[0], rowSignature("a", rows[0]));
  assert.equal(sigs[1], rowSignature("b", undefined));
  assert.deepEqual(rowSignatures([], rows), []);
  assert.deepEqual(rowSignatures(undefined, undefined), []);
  assert.notDeepEqual(rowSignatures(["a", "b"], rows), rowSignatures(["b", "a"], rows));
});

test("redrawAction redraws on changes, skips identical polls, and waits while focus is inside the rows", () => {
  const a = rowSignatures(["x", "y"], [{ word: "x", status: "pending" }, { word: "y", status: "ready" }]);
  const same = rowSignatures(["x", "y"], [{ word: "x", status: "pending" }, { word: "y", status: "ready" }]);
  const changed = rowSignatures(["x", "y"], [{ word: "x", status: "ready" }, { word: "y", status: "ready" }]);
  const shorter = rowSignatures(["x"], [{ word: "x", status: "pending" }]);
  // first draw: nothing on screen yet
  assert.equal(redrawAction(null, a), "redraw");
  assert.equal(redrawAction(null, a, { busy: true }), "redraw");
  // identical data: never rebuild, whatever the focus
  assert.equal(redrawAction(a, same), "unchanged");
  assert.equal(redrawAction(a, same, { busy: true }), "unchanged");
  // changed data: redraw, unless the parent is using a control in the rows (retried on the next poll)
  assert.equal(redrawAction(a, changed), "redraw");
  assert.equal(redrawAction(a, changed, { busy: false }), "redraw");
  assert.equal(redrawAction(a, changed, { busy: true }), "defer");
  assert.equal(redrawAction(a, shorter), "redraw");
  assert.equal(redrawAction(a, shorter, { busy: true }), "defer");
  // the parent's own action (add/remove/retry/regenerate) always redraws
  assert.equal(redrawAction(a, same, { force: true }), "redraw");
  assert.equal(redrawAction(a, changed, { force: true, busy: true }), "redraw");
  // redrawAction itself is stateless: the same fresh data redraws as soon as focus is gone (createRowRedrawer below
  // remembers the deferral, because the queue poll may not tick again)
  assert.equal(redrawAction(a, changed, { busy: true }), "defer");
  assert.equal(redrawAction(a, changed, { busy: false }), "redraw");
});

const sigs = (...statuses) => statuses.map((st, i) => rowSignature(`w${i}`, { status: st }));

test("createRowRedrawer: a redraw deferred on the queue's last tick runs when focus leaves the rows", () => {
  // The reviewer's reproduction: tick 1 (jobs running) draws; tick 2 is the last one (queue idle, the poll stops) and
  // finds focus inside the rows, so it defers; nothing ticks again. Focus then leaves the rows.
  const pending = sigs("pending", "pending");
  const settled = sigs("pending", "failed");
  let onScreen = null;
  const redrawer = createRowRedrawer((s) => {
    onScreen = s;
  });
  assert.equal(redrawer.update(pending, { busy: false }), "redraw");
  assert.equal(redrawer.update(settled, { busy: true }), "defer");
  assert.equal(redrawer.pending, true);
  assert.deepEqual(onScreen, pending, "still the old rows while the parent is using one of them");
  // focus moves to another control of the rows: still in use
  assert.equal(redrawer.focusOut({ toInside: true }), "idle");
  assert.deepEqual(onScreen, pending);
  // focus leaves the rows (to another element, or to nothing): the deferred redraw runs now, without any new tick
  assert.equal(redrawer.focusOut({ toInside: false }), "redraw");
  assert.deepEqual(onScreen, settled);
  assert.equal(redrawer.pending, false);
  assert.equal(redrawer.focusOut({ toInside: false }), "idle", "nothing pending any more");
});

test("createRowRedrawer: pending state follows the data and the parent's own actions", () => {
  let draws = 0;
  const redrawer = createRowRedrawer(() => {
    draws += 1;
  });
  const a = sigs("pending");
  const b = sigs("ready");
  const c = sigs("failed");
  assert.equal(redrawer.focusOut({ toInside: false }), "idle", "nothing was drawn or deferred yet");
  assert.equal(redrawer.update(a), "redraw");
  assert.equal(draws, 1);
  // identical data never redraws and never leaves anything pending
  assert.equal(redrawer.update(sigs("pending"), { busy: true }), "unchanged");
  assert.equal(redrawer.pending, false);
  assert.equal(redrawer.focusOut({ toInside: false }), "idle");
  assert.equal(draws, 1);
  // deferred, then the data moves on again while focus is still inside: the newest data is drawn on focusOut
  let last = null;
  const r2 = createRowRedrawer((s) => {
    last = s;
  });
  r2.update(a);
  r2.update(b, { busy: true });
  r2.update(c, { busy: true });
  assert.equal(r2.focusOut({ toInside: false }), "redraw");
  assert.deepEqual(last, c);
  // deferred, then the data returns to what is on screen: nothing to do any more
  const r3 = createRowRedrawer(() => {});
  r3.update(a);
  assert.equal(r3.update(b, { busy: true }), "defer");
  assert.equal(r3.update(a, { busy: true }), "unchanged");
  assert.equal(r3.pending, false);
  assert.equal(r3.focusOut({ toInside: false }), "idle");
  // a redraw caused by the parent's own action clears a pending deferral
  const r4 = createRowRedrawer(() => {});
  r4.update(a);
  r4.update(b, { busy: true });
  assert.equal(r4.update(b, { force: true, busy: true }), "redraw");
  assert.equal(r4.pending, false);
  assert.equal(r4.focusOut({ toInside: false }), "idle");
});

test("the real queueBanner stops after the idle tick, and the deferred redraw still runs when focus leaves the rows", async () => {
  const realFetch = globalThis.fetch;
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const queues = [
      { counts: { pending: 1, running: 1, done: 0, failed: 0 }, ai_calls_today: 3, ai_daily_limit: 2000, deferred_to_tomorrow: false },
      { counts: { pending: 0, running: 0, done: 1, failed: 1 }, ai_calls_today: 6, ai_daily_limit: 2000, deferred_to_tomorrow: false },
    ];
    let polls = 0;
    globalThis.fetch = async () => ({ ok: true, status: 200, text: async () => JSON.stringify(queues[Math.min(polls++, queues.length - 1)]) });
    const flush = () => new Promise((resolve) => setImmediate(resolve));
    const el = { textContent: "", hidden: true, classList: { toggle() {} } };
    let serverStatus = "pending"; // what GET /content would say for the one word
    let focusInRows = false; // is the parent using a control in the rows?
    let onScreen = null;
    const redrawer = createRowRedrawer((s) => {
      onScreen = s;
    });
    // same shape as lists.js: loadRows() then drawRows() on every tick, including the last one
    const banner = queueBanner(el, { alive: () => true }, async () => {
      redrawer.update(rowSignatures(["w"], [{ word: "w", status: serverStatus }]), { busy: focusInRows });
    });
    await banner.start(); // tick 1: jobs running, rows drawn
    assert.deepEqual(onScreen, rowSignatures(["w"], [{ word: "w", status: "pending" }]));
    assert.match(el.textContent, /Preparing words/);

    serverStatus = "failed";
    focusInRows = true; // e.g. the parent cancelled the Regenerate/remove confirm: focus is still on that control
    mock.timers.tick(3000);
    await flush(); // tick 2: the queue is idle, so this is the last tick
    assert.equal(polls, 2);
    assert.equal(el.textContent, "");
    assert.equal(redrawer.pending, true);
    assert.deepEqual(onScreen, rowSignatures(["w"], [{ word: "w", status: "pending" }]), "deferred: rows still show the old state");

    mock.timers.tick(60000);
    await flush();
    assert.equal(polls, 2, "the poll has stopped: no tick will retry the redraw");

    focusInRows = false; // focus leaves the rows (tab out / click elsewhere)
    assert.equal(redrawer.focusOut({ toInside: false }), "redraw");
    assert.deepEqual(onScreen, rowSignatures(["w"], [{ word: "w", status: "failed" }]));
  } finally {
    mock.timers.reset();
    globalThis.fetch = realFetch;
  }
});
