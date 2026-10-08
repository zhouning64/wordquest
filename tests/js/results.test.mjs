import test from "node:test";
import assert from "node:assert/strict";
import { summarizeResults } from "../../web/js/screens/results.js";

test("summarizeResults prefers the server result and falls back to local stats offline", () => {
  const local = { answered: 4, correct: 3, accuracy: 75, newWords: ["a"], starsUp: ["b"], keepPracticing: [{ word: "c", stage: 1 }] };
  const settings = { break_reminder: true, break_message: "Rest your eyes." };
  const server = {
    accuracy: 80, answered: 5, new_words: ["a", "d"], stars_up: ["b", "e"],
    keep_practicing: [{ word: "c", stage: 0 }], break_reminder: false, break_message: "x",
  };
  assert.deepEqual(summarizeResults(server, local, settings), {
    accuracy: 80, answered: 5, newWords: 2, starsUp: ["b", "e"], keep: [{ word: "c", stage: 0 }],
    breakReminder: false, breakMessage: "x", offline: false,
  });
  assert.deepEqual(summarizeResults(null, local, settings), {
    accuracy: 75, answered: 4, newWords: 1, starsUp: ["b"], keep: [{ word: "c", stage: 1 }],
    breakReminder: true, breakMessage: "Rest your eyes.", offline: true,
  });
  assert.equal(summarizeResults({ ...server, new_words: 3 }, local, settings).newWords, 3);
});

test("summarizeResults rounds accuracy, caps keep-practicing at 5, and tolerates missing fields", () => {
  const keep = ["a", "b", "c", "d", "e", "f"].map((word, stage) => ({ word, stage }));
  const s = summarizeResults({ accuracy: 66.7, answered: 3, keep_practicing: keep }, null, null);
  assert.equal(s.accuracy, 67);
  assert.equal(s.keep.length, 5);
  assert.equal(s.newWords, 0);
  assert.deepEqual(s.starsUp, []);
  assert.equal(s.breakReminder, false);
  const offline = summarizeResults(null, null, { break_reminder: false, break_message: "x" });
  assert.equal(offline.answered, 0);
  assert.equal(offline.breakReminder, false);
});
