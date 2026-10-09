import test from "node:test";
import assert from "node:assert/strict";
import { PARENT_SCREENS, errorMessage, parseRoute } from "../../web/js/app.js";
import { emptyMessage, preparingMessage, summarizeHome } from "../../web/js/screens/home.js";

test("parseRoute maps learner hashes", () => {
  assert.deepEqual(parseRoute(""), { screen: "start", name: "start", params: [] });
  assert.deepEqual(parseRoute("#/"), { screen: "start", name: "start", params: [] });
  assert.deepEqual(parseRoute("#/gate"), { screen: "gate", name: "gate", params: [] });
  assert.deepEqual(parseRoute("#/profiles"), { screen: "profiles", name: "profiles", params: [] });
  assert.deepEqual(parseRoute("#/home/a1b2c3"), { screen: "home", name: "home", params: ["a1b2c3"] });
  assert.deepEqual(parseRoute("#/session/s%2F1"), { screen: "session", name: "session", params: ["s/1"] });
});

test("parseRoute: home/session without an id and unknown hashes are notfound", () => {
  assert.equal(parseRoute("#/home").screen, "notfound");
  assert.equal(parseRoute("#/session").screen, "notfound");
  assert.equal(parseRoute("#/nope").screen, "notfound");
  assert.equal(parseRoute("#/parent/hack").screen, "notfound");
});

test("parseRoute maps parent hashes to parent/<name>.js with the rest as params", () => {
  assert.deepEqual(PARENT_SCREENS, ["login", "profiles", "lists", "preview", "stats", "backup"]);
  assert.deepEqual(parseRoute("#/parent"), { screen: "parent", name: "login", params: [] });
  assert.deepEqual(parseRoute("#/parent/login"), { screen: "parent", name: "login", params: [] });
  assert.deepEqual(parseRoute("#/parent/lists/l1"), { screen: "parent", name: "lists", params: ["l1"] });
  assert.deepEqual(parseRoute("#/parent/preview/6-8/come%20across"), { screen: "parent", name: "preview", params: ["6-8", "come across"] });
});

test("errorMessage explains network and rate-limit errors", () => {
  assert.match(errorMessage({ status: 0, message: "network_error" }), /Can't reach WordQuest/);
  assert.match(errorMessage({ status: 429, message: "x" }), /Too many tries/);
  assert.equal(errorMessage({ status: 500, message: "boom" }), "boom");
});

test("summarizeHome reads the Task 17 home payload and plans 'N reviews + M new'", () => {
  const s = summarizeHome({
    profile: { id: "p1", name: "Maya", avatar: "🦊", band: "6-8", session_minutes: 15, new_words_per_session: 5 },
    mastered: 2, learning: 5, new: 10, due_today: 3, ready_new: 8,
    practice_eligible: 4,
    preparing: { ready: 18, total: 20 },
  });
  assert.equal(s.mastered, 2);
  assert.equal(s.learning, 5);
  assert.equal(s.fresh, 10);
  assert.equal(s.due, 3);
  assert.equal(s.plannedNew, 5);
  assert.equal(s.startLabel, "3 reviews + 5 new");
  assert.equal(s.practiceEligible, 4);
  assert.deepEqual(s.preparing, { ready: 18, total: 20 });
});

test("summarizeHome uses the profile's new-words setting, capped by ready words", () => {
  const s = summarizeHome({
    mastered: 0, learning: 1, new: 23, due_today: 1, ready_new: 2,
    practice_eligible: 0, preparing: { ready: 24, total: 24 },
    profile: { new_words_per_session: 7 },
  });
  assert.equal(s.startLabel, "1 review + 2 new");
  assert.equal(s.preparing, null, "nothing is preparing when ready == total");
  assert.equal(s.practiceEligible, 0);
  const none = summarizeHome({ due_today: 2, ready_new: 9, profile: { new_words_per_session: 0 } });
  assert.equal(none.plannedNew, 0, "a profile set to 0 new words per session plans none");
  assert.equal(none.startLabel, "2 reviews + 0 new");
});

test("summarizeHome plans only what the session can hold (capacity = max(10, 2 × minutes))", () => {
  // 15 min → 30 items: 5 reviews leave room for 12 new words (2 slots each), not the setting's 30.
  const s = summarizeHome({ due_today: 5, ready_new: 40, profile: { session_minutes: 15, new_words_per_session: 30 } });
  assert.equal(s.startLabel, "5 reviews + 12 new");
  // Reviews alone fill the session: they are cut to capacity and no new words are planned.
  const full = summarizeHome({ due_today: 45, ready_new: 9, profile: { session_minutes: 15, new_words_per_session: 5 } });
  assert.equal(full.startLabel, "30 reviews + 0 new");
  // 30 min → 60 items: 5 reviews + up to 27 new; a setting of 30 is capped at 27.
  const long = summarizeHome({ due_today: 5, ready_new: 40, profile: { session_minutes: 30, new_words_per_session: 30 } });
  assert.equal(long.startLabel, "5 reviews + 27 new");
});

test("summarizeHome defaults new-per-session to 5 and handles an empty home", () => {
  assert.equal(summarizeHome({ due_today: 0, ready_new: 24 }).startLabel, "0 reviews + 5 new");
  const empty = summarizeHome({});
  assert.equal(empty.startLabel, "Nothing due right now");
  assert.equal(empty.preparing, null);
  assert.equal(summarizeHome(null).mastered, 0);
});

test("empty-session and preparing messages match spec §8.1 wording", () => {
  assert.equal(preparingMessage({ ready: 12, total: 20 }), "Your words are still being prepared — 12 of 20 ready.");
  assert.equal(emptyMessage({ empty_reason: "preparing", preparing: { ready: 12, total: 20 } }),
    "Your words are still being prepared — 12 of 20 ready.");
  assert.equal(emptyMessage({ empty_reason: "nothing_due" }), "Nothing due today — practice shaky words?");
  assert.match(emptyMessage({ empty_reason: null, queue: [] }), /No words to practice yet/);
});
