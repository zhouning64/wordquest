import test from "node:test";
import assert from "node:assert/strict";
import {
  MAX_EVENT_MS, SessionEngine, gradeTyped, highlightParts, levenshtein, normalizeAnswer, shuffleChoices, speechText,
} from "../../web/js/engine.js";
import { seededRng } from "../../web/js/ui.js";

const TODAY = "2026-10-07";
const YESTERDAY = "2026-10-06";

const q = (id, word, type = "meaning") => ({
  id, type, tier: 1, prompt: `Question ${id} about ___`, choices: ["a", "b", "c", "d"],
  answer_index: 0, accepted_answers: [], explanation: `because ${id}`,
});

function wordData(word, { stage = 0, last = null, reasks = 2, checks = [3, 3] } = {}) {
  return {
    card: {
      pos: "adjective", forms: [], short_def: `${word} def`, kid_def: "", senses: [], examples: [],
      word_parts: "", memory_hook: "", synonyms: [], antonyms: [], right_use: { sentence: "" },
      wrong_use: { sentence: "", why: "" }, image_scene: "", emoji_scene: "📖✨",
    },
    image_url: null,
    source: "ai",
    stage,
    last_graded_on: last,
    reserves: {
      reasks: Array.from({ length: reasks }, (_, i) => q(`${word}-r${i + 1}`, word)),
      checks: checks.map((n, s) => Array.from({ length: n }, (_, i) => q(`${word}-c${s + 1}${i + 1}`, word))),
    },
  };
}

const qi = (word, id = `${word}-main`) => ({ kind: "question", word, question: q(id, word) });
const intro = (word) => ({ kind: "intro", word });

function payload({ mode = "normal", queue, words }) {
  return {
    session_id: "s1", mode, local_date: TODAY,
    settings: { session_minutes: 15, break_reminder: true, break_message: "Rest your eyes." },
    queue, words, empty_reason: null, preparing: { ready: 3, total: 3 },
  };
}

const filler = (n) => Array.from({ length: n }, (_, i) => qi(`w${i}`));
const fillerWords = (n) => Object.fromEntries(Array.from({ length: n }, (_, i) => [`w${i}`, wordData(`w${i}`)]));
const queueIds = (e) => e.queue.map((it) => it.question?.id ?? `intro:${it.word}`);

test("walks the queue in order: intro, then questions, then finished", () => {
  const e = new SessionEngine(payload({
    queue: [intro("a"), qi("b"), qi("a")],
    words: { a: wordData("a"), b: wordData("b", { stage: 2, last: YESTERDAY }) },
  }));
  assert.deepEqual(e.current(), { kind: "intro", word: "a" });
  assert.throws(() => e.answerQuestion(true), /not a question/);
  e.advance();
  assert.equal(e.current().word, "b");
  assert.equal(e.current().question.id, "b-main");
  e.answerQuestion(true);
  assert.equal(e.answerQuestion(true).duplicate, true);
  e.advance();
  assert.equal(e.current().question.id, "a-main");
  e.answerQuestion(true);
  e.advance();
  assert.equal(e.isFinished(), true);
  assert.equal(e.current(), null);
  const s = e.stats();
  assert.deepEqual(s.newWords, ["a"]);
  assert.equal(s.answered, 2);
  assert.equal(s.accuracy, 100);
});

test("the engine copies the queue and never mutates the payload", () => {
  const p = payload({ queue: [qi("a")], words: { a: wordData("a") } });
  const e = new SessionEngine(p);
  e.answerQuestion(false);
  assert.equal(p.queue.length, 1);
  assert.equal(p.queue[0].answered, undefined);
  assert.equal(e.queue.length, 2);
});

test("a second answer for the same item (double tap) is ignored", () => {
  const e = new SessionEngine(payload({
    queue: [qi("a"), ...filler(6)],
    words: { a: wordData("a", { stage: 1, last: YESTERDAY }), ...fillerWords(6) },
  }));
  const first = e.answerQuestion(false);
  assert.equal(first.reaskInserted, true);
  const length = e.queue.length;
  const again = e.answerQuestion(true);
  assert.equal(again.duplicate, true);
  assert.equal(again.stars, first.stars);
  assert.equal(again.stageUp, false);
  assert.equal(again.reaskInserted, false);
  assert.equal(e.queue.length, length, "no second re-ask");
  assert.equal(e.pos, 0, "the queue did not move");
  const s = e.stats();
  assert.equal(s.answered, 1);
  assert.equal(s.correct, 0);
  assert.deepEqual(s.missed, ["a"]);
  assert.deepEqual(s.starsUp, []);
});

test("skip() passes an item without grading and without counting an intro as met", () => {
  const e = new SessionEngine(payload({ queue: [intro("a"), qi("a")], words: { a: wordData("a") } }));
  e.skip();
  assert.equal(e.current().kind, "question");
  e.skip();
  const s = e.stats();
  assert.deepEqual(s.newWords, []);
  assert.equal(s.answered, 0);
  assert.equal(s.skipped, 2);
});

test("a miss inserts the first re-ask 5 positions later", () => {
  const e = new SessionEngine(payload({ queue: [qi("a"), ...filler(7)], words: { a: wordData("a"), ...fillerWords(7) } }));
  const r = e.answerQuestion(false);
  assert.equal(r.reaskInserted, true);
  assert.equal(e.queue.length, 9);
  assert.deepEqual(e.queue[5], { kind: "question", word: "a", question: q("a-r1", "a"), reask: true });
  assert.deepEqual(queueIds(e).slice(0, 6), ["a-main", "w0-main", "w1-main", "w2-main", "w3-main", "a-r1"]);
});

test("a re-ask goes to the end when fewer than 5 items remain", () => {
  const e = new SessionEngine(payload({ queue: [qi("a"), ...filler(2)], words: { a: wordData("a"), ...fillerWords(2) } }));
  e.answerQuestion(false);
  assert.deepEqual(queueIds(e), ["a-main", "w0-main", "w1-main", "a-r1"]);
});

test("at most 2 re-asks per word, used in reserve order", () => {
  const e = new SessionEngine(payload({ queue: [qi("a")], words: { a: wordData("a", { reasks: 2 }) } }));
  assert.equal(e.answerQuestion(false).reaskInserted, true);
  e.advance();
  assert.equal(e.current().question.id, "a-r1");
  assert.equal(e.current().reask, true);
  assert.equal(e.answerQuestion(false).reaskInserted, true);
  e.advance();
  assert.equal(e.current().question.id, "a-r2");
  assert.equal(e.answerQuestion(false).reaskInserted, false);
  e.advance();
  assert.equal(e.isFinished(), true);
});

test("no re-ask when the word has no re-ask or check reserves, and none after a correct answer", () => {
  const e = new SessionEngine(payload({ queue: [qi("a"), qi("b")], words: { a: wordData("a", { reasks: 0, checks: [0, 0] }), b: wordData("b") } }));
  assert.equal(e.answerQuestion(false).reaskInserted, false);
  e.advance();
  assert.equal(e.answerQuestion(true).reaskInserted, false);
  assert.equal(e.queue.length, 2);
});

test("unsure counts as a miss, inserts a re-ask, and is counted separately", () => {
  const e = new SessionEngine(payload({ queue: [qi("a")], words: { a: wordData("a", { stage: 3, last: YESTERDAY }) } }));
  const r = e.answerQuestion(true, true);
  assert.equal(r.stars, 1);
  assert.equal(r.reaskInserted, true);
  const s = e.stats();
  assert.equal(s.unsure, 1);
  assert.equal(s.correct, 0);
  assert.deepEqual(s.missed, ["a"]);
});

test("check attempts use set 1 then set 2; startCheck does not consume an attempt", () => {
  const e = new SessionEngine(payload({ queue: [qi("a")], words: { a: wordData("a") } }));
  e.answerQuestion(false);
  assert.equal(e.canCheck("a"), true);
  assert.deepEqual(e.startCheck("a").map((x) => x.id), ["a-c11", "a-c12", "a-c13"]);
  assert.deepEqual(e.startCheck("a").map((x) => x.id), ["a-c11", "a-c12", "a-c13"]);
  assert.equal(e.recordCheck("a", 1), "retry");
  assert.equal(e.checksUsed("a"), 1);
  assert.deepEqual(e.startCheck("a").map((x) => x.id), ["a-c21", "a-c22", "a-c23"]);
  assert.equal(e.recordCheck("a", 2), "pass");
  assert.equal(e.canCheck("a"), false);
  assert.equal(e.startCheck("a"), null);
  assert.equal(e.stats().checksPassed, 1);
  assert.equal(e.stats().checksFailed, 1);
});

test("a pass on attempt 1 still leaves exactly one more attempt; failing it is a give-up", () => {
  const e = new SessionEngine(payload({ queue: [qi("a")], words: { a: wordData("a") } }));
  assert.equal(e.recordCheck("a", 3), "pass");
  assert.equal(e.canCheck("a"), true);
  assert.equal(e.recordCheck("a", 1), "giveup");
  assert.equal(e.canCheck("a"), false);
});

test("canCheck is false when the reserve set has fewer than 2 questions", () => {
  const e = new SessionEngine(payload({ queue: [qi("a")], words: { a: wordData("a", { checks: [1, 3] }) } }));
  assert.equal(e.canCheck("a"), false);
  assert.equal(e.startCheck("a"), null);
});

test("second check fail cancels the word's pending re-asks and blocks new ones", () => {
  const e = new SessionEngine(payload({
    queue: [qi("a"), qi("w0"), qi("a", "a-later"), ...filler(5).slice(1)],
    words: { a: wordData("a"), ...fillerWords(5) },
  }));
  e.answerQuestion(false);
  assert.ok(queueIds(e).includes("a-r1"));
  assert.equal(e.recordCheck("a", 0), "retry");
  assert.equal(e.recordCheck("a", 1), "giveup");
  assert.ok(!queueIds(e).includes("a-r1"), "pending re-ask removed");
  assert.ok(queueIds(e).includes("a-later"), "regular questions for the word stay");
  assert.equal(e.current().question.id, "a-main", "position unchanged");
  e.advance();
  e.answerQuestion(true);
  e.advance();
  assert.equal(e.current().question.id, "a-later");
  assert.equal(e.answerQuestion(false).reaskInserted, false);
  assert.equal(e.canCheck("a"), false);
});

test("stars change only on the word's first graded answer today in a normal session", () => {
  const e = new SessionEngine(payload({
    queue: [qi("a"), qi("a", "a-2"), qi("b"), qi("c")],
    words: {
      a: wordData("a", { stage: 1, last: YESTERDAY }),
      b: wordData("b", { stage: 2, last: TODAY }),
      c: wordData("c", { stage: 4, last: YESTERDAY }),
    },
  }));
  assert.deepEqual(pickStars(e.answerQuestion(true)), { stageUp: true, stars: 2 });
  e.advance();
  assert.deepEqual(pickStars(e.answerQuestion(true)), { stageUp: false, stars: 2 });
  e.advance();
  assert.deepEqual(pickStars(e.answerQuestion(true)), { stageUp: false, stars: 2 }, "already graded today");
  e.advance();
  assert.deepEqual(pickStars(e.answerQuestion(false)), { stageUp: false, stars: 2 }, "miss drops 4 → 2");
  assert.deepEqual(e.stats().starsUp, ["a"]);
  assert.equal(e.starsFor("c"), 2);
});

// A new word whose first question would sit too close to its intro gets only the intro this session
// (app/learning/session.py), with empty reserves: it counts as met, and is never re-asked or checked.
test("an intro-only word (no question in the queue) is met, never re-asked or checked", () => {
  const e = new SessionEngine(payload({
    queue: [intro("n"), qi("a"), ...filler(5)],
    words: { n: wordData("n", { reasks: 0, checks: [0, 0] }), a: wordData("a"), ...fillerWords(5) },
  }));
  e.advance();                                  // past the intro
  assert.equal(e.answerQuestion(false).reaskInserted, true, "a's re-ask still works");
  assert.equal(e.canCheck("a"), true, "a's lock-in check still works");
  assert.equal(e.canCheck("n"), false);
  assert.equal(e.startCheck("n"), null);
  assert.equal(e.insertReask("n"), false, "no reserves: nothing to re-ask");
  while (!e.isFinished()) {
    if (e.current().kind === "question") e.answerQuestion(true);
    e.advance();
  }
  assert.ok(!e.queue.some((it) => it.kind === "question" && it.word === "n"));
  const s = e.stats();
  assert.deepEqual(s.newWords, ["n"]);
  assert.equal(s.answered, 7);                  // a, its re-ask, and the 5 fillers
  assert.deepEqual(s.missed, ["a"]);
  assert.equal(e.starsFor("n"), 0);
});

test("a new word (no progress) goes 0 → 1 star on its first correct answer", () => {
  const e = new SessionEngine(payload({ queue: [intro("n"), qi("n")], words: { n: wordData("n") } }));
  e.advance();
  assert.deepEqual(pickStars(e.answerQuestion(true)), { stageUp: true, stars: 1 });
});

test("practice sessions never change stars", () => {
  const e = new SessionEngine(payload({
    mode: "practice",
    queue: [qi("a"), qi("b")],
    words: { a: wordData("a", { stage: 2, last: YESTERDAY }), b: wordData("b", { stage: 3, last: YESTERDAY }) },
  }));
  assert.deepEqual(pickStars(e.answerQuestion(true)), { stageUp: false, stars: 2 });
  e.advance();
  assert.deepEqual(pickStars(e.answerQuestion(false)), { stageUp: false, stars: 3 });
  assert.deepEqual(e.stats().starsUp, []);
});

test("check results never change stars", () => {
  const e = new SessionEngine(payload({ queue: [qi("a")], words: { a: wordData("a", { stage: 2, last: YESTERDAY }) } }));
  e.recordCheck("a", 3);
  assert.equal(e.starsFor("a"), 2);
});

test("answerQuestion reports ms since the item was shown, using the injected clock", () => {
  let t = 1000;
  const e = new SessionEngine(payload({ queue: [qi("a"), qi("b")], words: { a: wordData("a"), b: wordData("b") } }), { now: () => t });
  t = 4500;
  assert.equal(e.answerQuestion(true).ms, 3500);
  t = 5000;
  e.advance();
  t = 5200;
  assert.equal(e.answerQuestion(true).ms, 200);
});

test("stats: accuracy and up to 5 keep-practicing words, lowest stars first", () => {
  const words = {};
  const queue = [];
  [3, 1, 4, 2, 5, 3].forEach((stage, i) => {
    words[`m${i}`] = wordData(`m${i}`, { stage, last: TODAY, reasks: 0, checks: [0, 0] });
    queue.push(qi(`m${i}`));
  });
  words.ok = wordData("ok", { reasks: 0, checks: [0, 0] });
  queue.push(qi("ok"));
  const e = new SessionEngine(payload({ queue, words }));
  for (let i = 0; i < 6; i++) { e.answerQuestion(false); e.advance(); }
  e.answerQuestion(true);
  const s = e.stats();
  assert.equal(s.accuracy, 14);
  assert.deepEqual(s.keepPracticing.map((k) => k.word), ["m1", "m3", "m0", "m5", "m2"]);
  assert.deepEqual(s.keepPracticing[0], { word: "m1", stage: 1 });
});

// ---- fix round 1: re-ask fallback (spec §8.1) and timing clamp ----
// A word whose pool has no spare questions has reasks: [] — a miss then reuses a lock-in question
// the learner has not seen yet; a starter-style word has the same 3 questions in both check sets.
const starterWord = (word) => {
  const set = () => ["x1", "x2", "x3"].map((n) => q(`${word}-${n}`, word));
  const d = wordData(word, { reasks: 0 });
  d.reserves.checks = [set(), set()];
  return d;
};

test("a word with no re-ask reserves gets an unseen lock-in question as its re-ask after a miss", () => {
  const e = new SessionEngine(payload({ queue: [qi("a"), ...filler(7)], words: { a: wordData("a", { reasks: 0 }), ...fillerWords(7) } }));
  const r = e.answerQuestion(false);
  assert.equal(r.reaskInserted, true);
  assert.equal(e.queue.length, 9);
  assert.deepEqual(e.queue[5], { kind: "question", word: "a", question: q("a-c11", "a"), reask: true, fallback: true });
  assert.deepEqual(queueIds(e).slice(0, 6), ["a-main", "w0-main", "w1-main", "w2-main", "w3-main", "a-c11"]);
});

test("a fallback re-ask goes to the end when fewer than 5 items remain", () => {
  const e = new SessionEngine(payload({ queue: [qi("a"), ...filler(2)], words: { a: wordData("a", { reasks: 0 }), ...fillerWords(2) } }));
  e.answerQuestion(false);
  assert.deepEqual(queueIds(e), ["a-main", "w0-main", "w1-main", "a-c11"]);
});

test("a fallback re-ask is skipped (not graded) once a lock-in check has shown the same question", () => {
  const e = new SessionEngine(payload({ queue: [qi("a")], words: { a: wordData("a", { reasks: 0 }) } }));
  e.answerQuestion(false);
  assert.deepEqual(queueIds(e), ["a-main", "a-c11"]);
  e.startCheck("a");
  assert.equal(e.recordCheck("a", 3), "pass");
  e.advance();
  assert.equal(e.isFinished(), true, "the shown question is not asked again");
  const s = e.stats();
  assert.equal(s.answered, 1);
  assert.equal(s.skipped, 0, "an engine-level skip is not a learner skip");
});

test("a fallback re-ask that has not been shown meanwhile is asked normally", () => {
  const e = new SessionEngine(payload({ queue: [qi("a")], words: { a: wordData("a", { reasks: 0 }) } }));
  e.answerQuestion(false);
  e.advance();
  assert.equal(e.current().question.id, "a-c11");
  assert.equal(e.current().reask, true);
  assert.equal(e.answerQuestion(true).duplicate, undefined);
  assert.equal(e.stats().answered, 2);
});

test("stale fallback re-asks are skipped in a row and the next real item is shown", () => {
  const e = new SessionEngine(payload({
    queue: [qi("a"), qi("w0")], words: { a: starterWord("a"), ...fillerWords(1) },
  }));
  e.answerQuestion(false);
  e.advance();
  e.answerQuestion(true);
  e.advance();                       // re-ask a-x1 is current now
  assert.equal(e.current().question.id, "a-x1");
  e.answerQuestion(false);           // missed again: second fallback goes to the end
  assert.deepEqual(queueIds(e), ["a-main", "w0-main", "a-x1", "a-x2"]);
  e.startCheck("a");                 // shows x1, x2, x3
  e.recordCheck("a", 3);
  e.advance();
  assert.equal(e.isFinished(), true);
});

test("fallback re-asks use distinct questions, never one that is queued or shown, at most 2 per word", () => {
  const e = new SessionEngine(payload({
    queue: [qi("a"), qi("a", "a-x1")],   // x1 is already queued as a regular question
    words: { a: starterWord("a") },
  }));
  e.answerQuestion(false);
  assert.deepEqual(queueIds(e), ["a-main", "a-x1", "a-x2"], "x1 is skipped as a candidate");
  e.advance();
  assert.equal(e.answerQuestion(false).reaskInserted, true);
  assert.deepEqual(queueIds(e), ["a-main", "a-x1", "a-x2", "a-x3"]);
  e.advance();
  assert.equal(e.answerQuestion(false).reaskInserted, false, "cap of 2 per word counts fallback re-asks");
  assert.deepEqual(queueIds(e), ["a-main", "a-x1", "a-x2", "a-x3"]);
});

test("regular re-ask reserves are used first, then the fallback fills the second slot", () => {
  const e = new SessionEngine(payload({ queue: [qi("a")], words: { a: wordData("a", { reasks: 1 }) } }));
  assert.equal(e.answerQuestion(false).reaskInserted, true);
  e.advance();
  assert.equal(e.current().question.id, "a-r1");
  assert.equal(e.current().fallback, undefined);
  assert.equal(e.answerQuestion(false).reaskInserted, true);
  e.advance();
  assert.equal(e.current().question.id, "a-c11");
  assert.equal(e.current().fallback, true);
  assert.equal(e.answerQuestion(false).reaskInserted, false);
});

test("no re-ask when every lock-in question has been shown, or the word has none", () => {
  const e = new SessionEngine(payload({
    queue: [qi("a"), qi("b")], words: { a: wordData("a", { reasks: 0 }), b: wordData("b", { reasks: 0, checks: [0, 0] }) },
  }));
  e.startCheck("a");
  e.recordCheck("a", 1);
  e.startCheck("a");                 // set 2 shown as well
  assert.equal(e.answerQuestion(false).reaskInserted, false);
  e.advance();
  assert.equal(e.answerQuestion(false).reaskInserted, false, "no check questions at all: no crash, no re-ask");
  assert.equal(e.queue.length, 2);
});

test("giving up cancels pending fallback re-asks and blocks new ones", () => {
  const e = new SessionEngine(payload({
    queue: [qi("a"), qi("w0"), qi("a", "a-later"), ...filler(5).slice(1)],
    words: { a: wordData("a", { reasks: 0 }), ...fillerWords(5) },
  }));
  e.answerQuestion(false);
  assert.ok(queueIds(e).includes("a-c11"));
  assert.equal(e.recordCheck("a", 0), "retry");
  assert.equal(e.recordCheck("a", 1), "giveup");
  assert.ok(!queueIds(e).includes("a-c11"), "pending fallback re-ask removed");
  assert.ok(queueIds(e).includes("a-later"), "regular questions for the word stay");
  e.advance();
  e.advance();
  assert.equal(e.current().question.id, "a-later");
  assert.equal(e.answerQuestion(false).reaskInserted, false);
});

test("a fallback re-ask never touches the payload and the payload questions stay unmodified", () => {
  const p = payload({ queue: [qi("a")], words: { a: wordData("a", { reasks: 0 }) } });
  const before = JSON.stringify(p);
  const e = new SessionEngine(p);
  e.answerQuestion(false);
  assert.equal(JSON.stringify(p), before);
});

test("ms is clamped to what the server accepts (an item left open overnight)", () => {
  let t = 0;
  const e = new SessionEngine(payload({ queue: [qi("a")], words: { a: wordData("a") } }), { now: () => t });
  t = 40 * 3600 * 1000;
  assert.equal(e.answerQuestion(true).ms, MAX_EVENT_MS);
  assert.equal(MAX_EVENT_MS, 86_400_000);
});

// ---- grading helper: mirror of grading.py ----
test("normalizeAnswer trims, lowercases, straightens quotes, collapses spaces", () => {
  assert.equal(normalizeAnswer("  Frugal  "), "frugal");
  assert.equal(normalizeAnswer("Don’t   STOP"), "don't stop");
  assert.equal(normalizeAnswer("“Hi”"), '"hi"');
  assert.equal(normalizeAnswer(null), "");
});

test("levenshtein distances", () => {
  assert.equal(levenshtein("kitten", "sitting"), 3);
  assert.equal(levenshtein("", "abc"), 3);
  assert.equal(levenshtein("frugal", "frugal"), 0);
  assert.equal(levenshtein("frugl", "frugal"), 1);
  assert.equal(levenshtein("frugla", "frugal"), 2);
});

test("gradeTyped: correct, near (distance 1, answer ≥ 5 letters), wrong", () => {
  assert.equal(gradeTyped("Frugal", ["frugal"]), "correct");
  assert.equal(gradeTyped("  frugal ", ["frugal"]), "correct");
  assert.equal(gradeTyped("depletes", ["deplete", "depletes"]), "correct");
  assert.equal(gradeTyped("frugl", ["frugal"]), "near");
  assert.equal(gradeTyped("quel", ["quell"]), "near");
  assert.equal(gradeTyped("frgl", ["frugal"]), "wrong");
  assert.equal(gradeTyped("cat", ["cast"]), "wrong", "answers shorter than 5 letters never get near");
  assert.equal(gradeTyped("", ["frugal"]), "wrong");
  assert.equal(gradeTyped("frugal", []), "wrong");
});

// ---- presentation helpers ----
test("shuffleChoices keeps the answer text under the remapped index and does not mutate", () => {
  const question = { ...q("x", "frugal"), choices: ["careful with money", "very loud", "a kind of fruit", "tired"], answer_index: 2 };
  const orders = new Set();
  for (let seed = 0; seed < 50; seed++) {
    const { choices, answerIndex } = shuffleChoices(question, seededRng(seed));
    assert.equal(choices[answerIndex], "a kind of fruit");
    assert.deepEqual([...choices].sort(), [...question.choices].sort());
    orders.add(choices.join("|"));
  }
  assert.ok(orders.size > 10, "order changes between renders");
  assert.deepEqual(question.choices, ["careful with money", "very loud", "a kind of fruit", "tired"]);
  assert.equal(question.answer_index, 2);
});

test("speechText reads blanks as 'blank' and never adds the answer", () => {
  assert.equal(speechText({ type: "spell_it", prompt: "She was ___ with her allowance. (means: careful with money)" }),
    "She was blank with her allowance. (means: careful with money)");
  assert.equal(speechText({ type: "fill_blank", prompt: "The ______ crowd cheered." }), "The blank crowd cheered.");
  assert.equal(speechText({ type: "pick_word", prompt: "Which word means “careful with money”?" }), "Which word means “careful with money”?");
});

test("highlightParts marks the word, its forms and simple inflections", () => {
  assert.deepEqual(highlightParts("She kept meticulous notes.", "meticulous"), [
    { text: "She kept ", hit: false }, { text: "meticulous", hit: true }, { text: " notes.", hit: false },
  ]);
  const hits = (s, w, f) => highlightParts(s, w, f).filter((p) => p.hit).map((p) => p.text);
  assert.deepEqual(hits("Resilient kids bounce back.", "resilient"), ["Resilient"]);
  assert.deepEqual(hits("Running depleted it; depleting more.", "deplete"), ["depleted", "depleting"]);
  assert.deepEqual(hits("He strove and strives.", "strive", ["strove", "strives"]), ["strove", "strives"]);
  assert.deepEqual(hits("She gave up too soon.", "give up", ["gave up"]), ["gave up"]);
  assert.deepEqual(hits("A frugally run house.", "frugal"), ["frugally"]);
  assert.deepEqual(hits("Nothing here.", "zenith"), []);
  assert.deepEqual(highlightParts("", "zenith"), [{ text: "", hit: false }]);
});

function pickStars(r) {
  return { stageUp: r.stageUp, stars: r.stars };
}
