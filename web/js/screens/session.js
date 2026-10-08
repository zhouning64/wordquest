// Session screen (#/session/<sessionId>): timer, progress, and the item loop. Intro, Learn, lock-in
// check and results render inside this screen, so the timer keeps running on Learn and check.
import { h, mount, stars, toast, uuid } from "../ui.js";
import { EventQueue } from "../api.js";
import { SessionEngine } from "../engine.js";
import { mountQuestion, TYPE_LABEL } from "../question.js";
import { stopSpeaking } from "../speech.js";
import * as intro from "./intro.js";
import * as learn from "./learn.js";
import * as check from "./check.js";
import * as results from "./results.js";

export const DEBUG_SECONDS_KEY = "wq-debug-seconds";   // devtools-only override of the session length

export const MAX_ACTIVE_MINUTES = 600;   // /finish rejects a larger active_minutes

// Whole minutes since startedAt, never negative and never above what the server accepts.
export function activeMinutes(startedAt, now = Date.now()) {
  return Math.min(MAX_ACTIVE_MINUTES, Math.max(0, Math.round((now - startedAt) / 60000)));
}

// Spec §10: pending events are flushed before finish. drain() resolves false instead of throwing when an
// upload fails, so check what is left: with events still pending the session is NOT finished on the
// server (it would score without them) and null is returned — the caller shows the offline results and
// the events stay in localStorage for the next visit. postFinish() errors propagate (401 handling).
export async function flushThenFinish(queue, postFinish) {
  await queue.drain();
  if (queue.pending > 0) return null;
  return postFinish();
}

export function formatClock(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function debugSeconds() {
  try {
    const v = parseInt(globalThis.localStorage?.getItem(DEBUG_SECONDS_KEY) ?? "", 10);
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch {
    return null;
  }
}

export function render(root, ctx) {
  const sid = ctx.params[0];
  const payload = ctx.state.session;
  const homeHash = ctx.state.profileId ? `#/home/${encodeURIComponent(ctx.state.profileId)}` : "#/profiles";
  if (!payload || payload.session_id !== sid) {   // reload or deep link: the payload lives only in memory
    ctx.navigate(homeHash, { replace: true });
    return undefined;
  }

  const engine = new SessionEngine(payload);
  const queue = new EventQueue(sid);
  queue.start();
  const settings = payload.settings || {};
  const totalSec = debugSeconds() ?? Math.max(1, Number(settings.session_minutes) || 15) * 60;
  const startedAt = Date.now();
  const endsAt = startedAt + totalSec * 1000;
  let timeUp = false;
  let finished = false;
  let tornDown = false;

  const timerEl = h("div", { class: "timer", role: "timer", "aria-label": "time left" }, formatClock(totalSec));
  const fill = h("div", { style: "width:0%" });
  const body = h("div", { class: "session-body" });
  const endBtn = h("button", { class: "btn btn-ghost btn-inline", type: "button", onclick: () => endEarly() }, "✕ End");
  mount(root, h("div", { class: "topbar" }, endBtn, timerEl), h("div", { class: "pbar", "aria-hidden": "true" }, fill), body);

  function tick() {
    const left = Math.max(0, Math.ceil((endsAt - Date.now()) / 1000));
    timerEl.textContent = formatClock(left);
    timerEl.classList.toggle("low", left <= 120);
    if (left <= 0 && !timeUp) {
      timeUp = true;   // finish after the current item (checked in showCurrent)
      toast("⏰ Time's up — finish this one!");
    }
  }
  const timerId = setInterval(tick, 1000);

  function logEvent(kind, word, extra = {}) {
    queue.push({
      client_event_id: uuid(), kind, word,
      question_id: null, question_type: null, correct: null,
      check_set: null, correct_count: null, passed: null,
      ms: 0, local_date: payload.local_date, at: new Date().toISOString(),
      ...extra,
    });
  }

  function screenChanged() {
    stopSpeaking();
    queue.flush();
    window.scrollTo(0, 0);
  }

  function updateProgress() {
    const n = engine.queue.length;
    fill.style.width = n ? `${Math.round((100 * Math.min(engine.pos, n)) / n)}%` : "0%";
  }

  function next() {
    engine.advance();
    showCurrent();
  }

  function showCurrent() {
    if (finished) return;
    updateProgress();
    if (timeUp || engine.isFinished()) {
      finish();
      return;
    }
    const item = engine.current();
    const data = payload.words && payload.words[item.word];
    if (!data || (item.kind === "question" && !item.question)) {
      engine.skip();
      showCurrent();
      return;
    }
    screenChanged();
    if (item.kind === "intro") {
      intro.render(body, { ...ctx, word: item.word, data, onDone: () => { logEvent("intro_seen", item.word); next(); } });
      return;
    }
    showQuestion(item);
  }

  function showQuestion(item) {
    const q = item.question;
    mountQuestion(body, q, {
      label: item.reask ? `${TYPE_LABEL[q.type] || q.type} · again` : undefined,
      onAnswered: (r, fb) => {
        const res = engine.answerQuestion(r.correct, r.unsure);
        logEvent(r.unsure ? "unsure" : "answer", item.word, {
          question_id: q.id, question_type: q.type, correct: r.unsure ? false : r.correct, ms: res.ms,
        });
        if (r.unsure) {
          openLearn(item.word, { kind: "answer", answer: r.answerText, explanation: q.explanation });
        } else if (r.correct) {
          renderCorrect(fb, q, res);
        } else {
          renderWrong(fb, q, r, item.word);
        }
      },
    });
  }

  function renderCorrect(fb, q, res) {
    const nextBtn = h("button", { class: "btn btn-primary", type: "button", "data-enter": "", onclick: next }, "Next →");
    mount(fb,
      h("div", { class: "feedback good" },
        "✓ Correct! ",
        h("span", { class: "stars", "aria-label": `${res.stars} of 5 stars` }, stars(res.stars)),
        res.stageUp ? h("span", { class: "sub" }, "⭐ You earned a star!") : null,
        q.explanation ? h("span", { class: "sub" }, q.explanation) : null),
      nextBtn);
    nextBtn.focus({ preventScroll: true });
  }

  function renderWrong(fb, q, r, word) {
    const learnBtn = h("button", { class: "btn btn-learn", type: "button", "data-enter": "", onclick: () => openLearn(word, null) }, "📖 Learn this word");
    mount(fb,
      h("div", { class: "feedback bad" },
        "✗ Not quite — the answer is ", h("b", null, r.answerText),
        r.near ? h("span", { class: "sub" }, "So close — check the spelling!") : null,
        q.explanation ? h("span", { class: "sub" }, q.explanation) : null),
      learnBtn,
      h("button", { class: "btn btn-ghost", type: "button", onclick: next }, "skip for now →"));
    learnBtn.focus({ preventScroll: true });
  }

  function openLearn(word, banner) {
    logEvent("learn_open", word);
    screenChanged();
    learn.render(body, {
      ...ctx, word, data: payload.words[word], banner,
      stars: engine.starsFor(word),
      canCheck: engine.canCheck(word),
      onCheck: () => runCheck(word),
      onBack: next,   // back to the session: continue after the missed item (its re-ask is already queued)
    });
  }

  function runCheck(word) {
    const questions = engine.startCheck(word);
    if (!questions) {
      next();
      return;
    }
    const checkSet = engine.checksUsed(word) + 1;
    screenChanged();
    check.render(body, {
      ...ctx, word, questions, checkSet,
      onAnswer: (q, correct, ms) => logEvent("check_answer", word, { question_id: q.id, question_type: q.type, correct, ms }),
      onDone: (correctCount, missed) => {
        const outcome = engine.recordCheck(word, correctCount);
        logEvent("check_result", word, { check_set: checkSet, correct_count: correctCount, passed: outcome === "pass" });
        if (outcome === "pass") {
          toast("Locked in! 🔒");
          next();
        } else if (outcome === "retry") {
          openLearn(word, { kind: "missed", items: missed });
        } else {
          showGiveUp(word);
        }
      },
    });
  }

  function showGiveUp(word) {
    screenChanged();
    const btn = h("button", { class: "btn btn-primary", type: "button", "data-enter": "", onclick: next }, "Back to the session →");
    mount(body,
      h("div", { class: "card center giveup" },
        h("div", { class: "big-emoji", "aria-hidden": "true" }, "🌙"),
        h("h2", null, "Let's come back to this one tomorrow"),
        h("p", { class: "muted" }, `“${word}” will show up again tomorrow. Nice effort!`)),
      btn);
    btn.focus({ preventScroll: true });
  }

  function endEarly() {
    if (finished) return;
    if (engine.stats().answered >= 1) {
      finish();
    } else {
      finished = true;
      teardown();
      ctx.navigate(homeHash);
    }
  }

  async function finish() {
    if (finished) return;
    finished = true;
    clearInterval(timerId);
    endBtn.disabled = true;
    const local = engine.stats();
    if (local.answered === 0) {
      teardown();
      ctx.navigate(homeHash);
      return;
    }
    screenChanged();
    mount(body, h("div", { class: "card center" }, h("p", null, "Saving your results…")));
    const minutes = activeMinutes(startedAt);
    let server = null;   // stays null when /finish was not reached (offline or events still pending): local results
    try {
      server = await flushThenFinish(queue, () => ctx.api.post(`/api/sessions/${encodeURIComponent(sid)}/finish`, { active_minutes: minutes }));
    } catch (err) {
      if (err && err.status === 401) {
        teardown();
        ctx.fail(err);
        return;
      }
      server = null;   // offline: show local results; events stay in localStorage for the next visit
    }
    queue.stop();
    if (!ctx.alive()) return;
    ctx.state.session = null;
    results.render(root, { ...ctx, results: server, local, settings, onHome: () => ctx.navigate(homeHash) });
  }

  // Keys 1–4 pick a choice; Enter presses the screen's primary button (data-enter).
  function onKey(e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = ((e.target && e.target.tagName) || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (/^[1-4]$/.test(e.key)) {
      const btn = root.querySelector(`.btn-opt[data-key="${e.key}"]:not(:disabled)`);
      if (btn) {
        e.preventDefault();
        btn.click();
      }
    } else if (e.key === "Enter" && tag !== "button" && tag !== "a") {
      const btn = root.querySelector("[data-enter]:not(:disabled)");
      if (btn) {
        e.preventDefault();
        btn.click();
      }
    }
  }
  document.addEventListener("keydown", onKey);

  function teardown() {
    if (tornDown) return;
    tornDown = true;
    // Back → Forward must not re-render this payload (a second run would re-post its events under new ids).
    if (ctx.state.session === payload) ctx.state.session = null;
    clearInterval(timerId);
    document.removeEventListener("keydown", onKey);
    stopSpeaking();
    queue.flush();
    queue.stop();
  }

  showCurrent();
  return teardown;
}
