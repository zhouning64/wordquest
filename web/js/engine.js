// Session engine: a pure state machine over the session payload (no DOM), plus pure helpers
// for grading typed answers, shuffling choices, 🔊 text, and highlighting the word in sentences.
import { applyGraded } from "./srs.js";
import { shuffle } from "./ui.js";

export const REASK_GAP = 5;            // a re-ask goes 5 positions after the missed item (or at the end)
export const MAX_REASKS_PER_WORD = 2;
export const MAX_CHECK_ATTEMPTS = 2;
export const CHECK_PASS = 2;           // lock-in check passes with at least 2 correct
export const MAX_EVENT_MS = 86_400_000; // the server rejects an event whose ms is larger (an item left open overnight)

// ---- typed-answer grading: mirror of app/learning/grading.py ----
export function normalizeAnswer(s) {
  return String(s ?? "")
    .replace(/[‘’‛′]/g, "'")
    .replace(/[“”‟″]/g, '"')
    .toLowerCase()
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
}

export function levenshtein(a, b) {
  const s = Array.from(String(a));
  const t = Array.from(String(b));
  let prev = Array.from({ length: t.length + 1 }, (_, j) => j);
  for (let i = 1; i <= s.length; i++) {
    const cur = [i];
    for (let j = 1; j <= t.length; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (s[i - 1] === t[j - 1] ? 0 : 1));
    }
    prev = cur;
  }
  return prev[t.length];
}

// "correct" | "near" | "wrong"; near = distance 1 to some accepted answer of length ≥ 5 (still graded wrong).
export function gradeTyped(given, accepted) {
  const g = normalizeAnswer(given);
  const answers = (accepted || []).map(normalizeAnswer).filter(Boolean);
  if (!g) return "wrong";
  if (answers.includes(g)) return "correct";
  if (answers.some((a) => Array.from(a).length >= 5 && levenshtein(g, a) === 1)) return "near";
  return "wrong";
}

// ---- question presentation helpers ----
// Shuffles a choice question's choices (fresh order every call) and remaps answer_index.
export function shuffleChoices(q, rng = Math.random) {
  const order = shuffle(q.choices.map((_, i) => i), rng);
  return { choices: order.map((i) => q.choices[i]), answerIndex: order.indexOf(q.answer_index), order };
}

// Text for the question's 🔊 button: the prompt with every "___" read as "blank" — so fill_blank and
// spell_it never speak the target word, and pick_word reads its definition prompt.
export function speechText(q) {
  return String((q && q.prompt) || "").replace(/_{2,}/g, " blank ").replace(/\s+/g, " ").trim();
}

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Splits a sentence into [{text, hit}] where hit marks the word, a card form, or a simple inflection.
export function highlightParts(sentence, word, forms = []) {
  const s = String(sentence ?? "");
  const cands = new Set();
  const add = (x) => {
    const v = String(x || "").trim().toLowerCase();
    if (v.length >= 3) cands.add(v);
  };
  add(word);
  for (const f of forms || []) add(f);
  const w = String(word || "").trim().toLowerCase();
  if (w.length >= 5 && /[ey]$/.test(w)) add(w.slice(0, -1));   // deplete → deplet(ing), happy → happ(ily)
  if (!s || !cands.size) return [{ text: s, hit: false }];
  const alts = [...cands].sort((a, b) => b.length - a.length).map((a) => escapeRe(a).replace(/ +/g, "\\s+"));
  const re = new RegExp(`(^|[^\\p{L}])((?:${alts.join("|")})[\\p{L}'’-]*)`, "giu");
  const out = [];
  let last = 0;
  let m;
  while ((m = re.exec(s)) !== null) {
    const start = m.index + m[1].length;
    if (start > last) out.push({ text: s.slice(last, start), hit: false });
    out.push({ text: m[2], hit: true });
    last = start + m[2].length;
    re.lastIndex = last;
  }
  if (last < s.length) out.push({ text: s.slice(last), hit: false });
  return out;
}

// ---- the engine ----
export class SessionEngine {
  constructor(payload, { now = () => Date.now() } = {}) {
    this.payload = payload;
    this.mode = payload.mode;
    this.localDate = payload.local_date;
    this.now = now;
    this.words = payload.words || {};
    this.queue = (payload.queue || []).map((item) => ({ ...item }));
    this.pos = 0;
    this.shown = new Set();   // ids of questions already put in front of the learner (main, re-ask, check)
    this.shownAt = now();
    this.wordState = {};
    for (const [w, d] of Object.entries(this.words)) {
      this.wordState[w] = { stage: d.stage ?? 0, due_date: null, interval_days: 0, last_graded_on: d.last_graded_on ?? null };
    }
    this.reasksUsed = {};
    this.checkAttempts = {};
    this.gaveUp = new Set();
    this.counts = { answered: 0, correct: 0, unsure: 0, skipped: 0, checksPassed: 0, checksFailed: 0 };
    this.newWords = [];
    this.starsUp = [];
    this.missed = [];
    this._enter();
  }

  // Called whenever pos changes: drops fallback re-asks whose question was shown meanwhile (e.g. by a
  // lock-in check) without grading them, then records the new current question as shown.
  _enter() {
    for (let it = this.current(); it && it.fallback && this.shown.has(it.question.id); it = this.current()) this.pos += 1;
    const item = this.current();
    if (item && item.question) this.shown.add(item.question.id);
    this.shownAt = this.now();
  }

  current() {
    return this.pos < this.queue.length ? this.queue[this.pos] : null;
  }

  isFinished() {
    return this.pos >= this.queue.length;
  }

  starsFor(word) {
    return (this.wordState[word] && this.wordState[word].stage) || 0;
  }

  advance() {
    const item = this.current();
    if (!item) return;
    if (item.kind === "intro" && !this.newWords.includes(item.word)) this.newWords.push(item.word);
    this.pos += 1;
    this._enter();
  }

  // Moves past the current item without grading it (e.g. an item whose word data is missing).
  skip() {
    if (!this.current()) return;
    this.counts.skipped += 1;
    this.pos += 1;
    this._enter();
  }

  answerQuestion(correct, unsure = false) {
    const item = this.current();
    if (!item || item.kind !== "question") throw new Error("current item is not a question");
    // A double tap or a repeated Enter on the same item is ignored: nothing is recorded twice.
    if (item.result) return { ...item.result, reaskInserted: false, duplicate: true };
    item.answered = true;
    const ok = Boolean(correct) && !unsure;
    const word = item.word;
    const ms = Math.min(MAX_EVENT_MS, Math.max(0, Math.round(this.now() - this.shownAt)));
    this.counts.answered += 1;
    if (ok) this.counts.correct += 1;
    if (unsure) this.counts.unsure += 1;
    const before = this.wordState[word] || { stage: 0, due_date: null, interval_days: 0, last_graded_on: null };
    const { state, stageUp } = applyGraded(before, { correct: ok, unsure: Boolean(unsure), localDate: this.localDate, mode: this.mode });
    this.wordState[word] = state;
    if (stageUp && !this.starsUp.includes(word)) this.starsUp.push(word);
    let reaskInserted = false;
    if (!ok) {
      if (!this.missed.includes(word)) this.missed.push(word);
      reaskInserted = this.insertReask(word);
    }
    item.result = { stageUp, stars: state.stage, ms, reaskInserted };
    return item.result;
  }

  // Inserts the next unused re-ask reserve REASK_GAP positions after the current item (or at the end).
  // Spec §8.1: when the word has no (more) re-ask reserves, a lock-in question the learner has not seen
  // yet stands in for it (a "fallback" re-ask, skipped later if a check shows it first); none left = none.
  insertReask(word) {
    if (this.gaveUp.has(word)) return false;
    const used = this.reasksUsed[word] || 0;
    if (used >= MAX_REASKS_PER_WORD) return false;
    const reserves = (this.words[word] && this.words[word].reserves) || {};
    let item = null;
    const q = (reserves.reasks || [])[used];
    if (q) {
      item = { kind: "question", word, question: q, reask: true };
    } else {
      const queued = new Set(this.queue.map((it) => it.question && it.question.id));
      const spare = [].concat(...(reserves.checks || [])).find((c) => c && !this.shown.has(c.id) && !queued.has(c.id));
      if (spare) item = { kind: "question", word, question: spare, reask: true, fallback: true };
    }
    if (!item) return false;
    this.reasksUsed[word] = used + 1;
    const at = Math.min(this.pos + REASK_GAP, this.queue.length);
    this.queue.splice(at, 0, item);
    return true;
  }

  checksUsed(word) {
    return this.checkAttempts[word] || 0;
  }

  canCheck(word) {
    if (this.gaveUp.has(word)) return false;
    const n = this.checksUsed(word);
    if (n >= MAX_CHECK_ATTEMPTS) return false;
    const checks = (this.words[word] && this.words[word].reserves && this.words[word].reserves.checks) || [];
    return Array.isArray(checks[n]) && checks[n].length >= CHECK_PASS;
  }

  // Attempt 1 uses reserve set 1, attempt 2 uses set 2. Does not consume the attempt (recordCheck does).
  startCheck(word) {
    if (!this.canCheck(word)) return null;
    const set = this.words[word].reserves.checks[this.checksUsed(word)].slice();
    for (const c of set) this.shown.add(c.id);
    return set;
  }

  recordCheck(word, correctCount) {
    const attempt = this.checksUsed(word) + 1;
    this.checkAttempts[word] = attempt;
    if (correctCount >= CHECK_PASS) {
      this.counts.checksPassed += 1;
      return "pass";
    }
    this.counts.checksFailed += 1;
    if (attempt < MAX_CHECK_ATTEMPTS) return "retry";
    this.gaveUp.add(word);
    // cancel this word's re-asks that have not been shown yet
    this.queue = this.queue.filter((item, i) => i <= this.pos || !(item.reask && item.word === word));
    return "giveup";
  }

  stats() {
    const { answered, correct, unsure, skipped, checksPassed, checksFailed } = this.counts;
    const keepPracticing = this.missed
      .map((word) => ({ word, stage: this.starsFor(word) }))
      .sort((a, b) => a.stage - b.stage)
      .slice(0, 5);
    return {
      answered,
      correct,
      unsure,
      skipped,
      accuracy: answered ? Math.round((100 * correct) / answered) : 0,
      newWords: [...this.newWords],
      starsUp: [...this.starsUp],
      missed: [...this.missed],
      keepPracticing,
      checksPassed,
      checksFailed,
      position: this.pos,
      total: this.queue.length,
    };
  }
}
