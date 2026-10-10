import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  INTERVALS, MAX_COUNTED_PER_DAY, MIN_HOURS_BETWEEN_COUNTED, addDays, applyGraded, isStageChanging,
} from "../../web/js/srs.js";

const vectors = JSON.parse(readFileSync(new URL("../srs_vectors.json", import.meta.url), "utf8"));
const FIELDS = ["stage", "due_date", "interval_days", "last_graded_on", "graded_today", "last_graded_at"];
const pick = (s) => Object.fromEntries(FIELDS.map((k) => [k, s[k] ?? null]));

test("srs_vectors.json is a non-empty list", () => {
  assert.ok(Array.isArray(vectors));
  assert.ok(vectors.length > 0);
});

for (const v of vectors) {
  test(`vector: ${v.name}`, () => {
    const before = structuredClone(v.before);
    const { state, stageUp } = applyGraded(v.before, {
      correct: v.event.correct,
      unsure: v.event.unsure,
      localDate: v.event.local_date,
      at: v.event.at,
      mode: v.event.mode,
    });
    assert.deepEqual(pick(state), pick(v.after));
    assert.equal(stageUp, v.stage_up);
    assert.deepEqual(v.before, before, "applyGraded must not mutate its input");
  });
}

const D = "2026-10-07";
const AT = "2026-10-07T15:00:00.000Z";
const st = (stage, last = null, interval_days = 0, due_date = null, graded_today = last ? 1 : 0, last_graded_at = last ? `${last}T09:00:00.000Z` : null) => (
  { stage, due_date, interval_days, last_graded_on: last, graded_today, last_graded_at });

test("interval table and daily limits match spec §8.5", () => {
  assert.deepEqual(INTERVALS, { 1: 1, 2: 3, 3: 7, 4: 14, 5: 30 });
  assert.equal(MAX_COUNTED_PER_DAY, 3);
  assert.equal(MIN_HOURS_BETWEEN_COUNTED, 2);
});

test("addDays crosses month, year and leap day", () => {
  assert.equal(addDays("2026-10-07", 1), "2026-10-08");
  assert.equal(addDays("2026-10-31", 1), "2026-11-01");
  assert.equal(addDays("2026-12-31", 1), "2027-01-01");
  assert.equal(addDays("2028-02-28", 1), "2028-02-29");
  assert.equal(addDays("2026-03-08", 30), "2026-04-07");
});

test("isStageChanging: normal or practice, at most 3 a day, at least 2 hours apart, in order", () => {
  assert.equal(isStageChanging(st(0), D, "normal", AT), true);
  assert.equal(isStageChanging(st(0), D, "practice", AT), true);
  assert.equal(isStageChanging(st(2, "2026-10-06"), D, "normal", AT), true);
  assert.equal(isStageChanging(st(2, "2026-10-06"), D, "practice", AT), true);
  assert.equal(isStageChanging(st(2, D, 3, null, 1, "2026-10-07T13:00:00.000Z"), D, "normal", AT), true, "exactly 2 h");
  assert.equal(isStageChanging(st(2, D, 3, null, 1, "2026-10-07T13:00:00.001Z"), D, "normal", AT), false, "1 ms short");
  assert.equal(isStageChanging(st(2, D, 3, null, 2, "2026-10-07T09:00:00.000Z"), D, "practice", AT), true, "third");
  assert.equal(isStageChanging(st(2, D, 3, null, 3, "2026-10-07T09:00:00.000Z"), D, "practice", AT), false, "fourth");
  assert.equal(isStageChanging(st(2, "2026-10-06", 3, null, 3, "2026-10-07T14:00:00.000Z"), D, "normal", AT), false,
    "new day, but 1 h after the last counted answer");
  assert.equal(isStageChanging(st(2, "2026-10-08"), D, "normal", AT), false, "local date out of order");
  assert.equal(isStageChanging(st(2, D, 3, null, 1, "2026-10-07T18:00:00.000Z"), D, "practice", AT), false, "at out of order");
  assert.equal(isStageChanging(st(2, D, 3, null, 0, null), D, "normal", AT), true, "pre-upgrade row: 1 so far, gap ok");
  assert.equal(isStageChanging(st(0), D, "cram", AT), false);
});

test("correct from stage 0 → stage 1, due tomorrow, stageUp", () => {
  const { state, stageUp } = applyGraded(st(0), { correct: true, unsure: false, localDate: D, at: AT, mode: "normal" });
  assert.deepEqual(state, { stage: 1, due_date: "2026-10-08", interval_days: 1, last_graded_on: D, graded_today: 1, last_graded_at: AT });
  assert.equal(stageUp, true);
});

test("correct 4 → 5 uses the table (30 days)", () => {
  const { state } = applyGraded(st(4, "2026-09-20", 14), { correct: true, unsure: false, localDate: D, at: AT, mode: "normal" });
  assert.deepEqual(state, { stage: 5, due_date: "2026-11-06", interval_days: 30, last_graded_on: D, graded_today: 1, last_graded_at: AT });
});

test("mastered word doubles its interval, clamped to 30..60", () => {
  let r = applyGraded(st(5, "2026-09-01", 30), { correct: true, unsure: false, localDate: D, at: AT, mode: "normal" });
  assert.equal(r.state.stage, 5);
  assert.equal(r.state.interval_days, 60);
  assert.equal(r.stageUp, false);
  r = applyGraded(st(5, "2026-09-01", 45), { correct: true, unsure: false, localDate: D, at: AT, mode: "normal" });
  assert.equal(r.state.interval_days, 60);
  r = applyGraded(st(5, "2026-09-01", 0), { correct: true, unsure: false, localDate: D, at: AT, mode: "normal" });
  assert.equal(r.state.interval_days, 30);
});

test("miss: stage 0 stays 0, stage 1 stays 1, stage 4 drops to 2; due tomorrow (normal and practice)", () => {
  for (const mode of ["normal", "practice"]) {
    for (const [from, to] of [[0, 0], [1, 1], [2, 1], [4, 2], [5, 3]]) {
      const { state, stageUp } = applyGraded(st(from, "2026-10-01", 7), { correct: false, unsure: false, localDate: D, at: AT, mode });
      assert.equal(state.stage, to, `${mode} from ${from}`);
      assert.equal(state.interval_days, 1);
      assert.equal(state.due_date, "2026-10-08");
      assert.equal(state.last_graded_on, D);
      assert.equal(stageUp, false);
    }
  }
});

test("unsure is a miss even if correct is passed as true", () => {
  const { state } = applyGraded(st(3, "2026-10-01", 7), { correct: true, unsure: true, localDate: D, at: AT, mode: "normal" });
  assert.equal(state.stage, 1);
});

test("a second answer minutes later changes nothing; 2 hours later it counts; a fourth that day never does", () => {
  const first = applyGraded(st(1, "2026-10-01", 1), { correct: true, unsure: false, localDate: D, at: "2026-10-07T08:00:00.000Z", mode: "normal" });
  const soon = applyGraded(first.state, { correct: false, unsure: false, localDate: D, at: "2026-10-07T08:05:00.000Z", mode: "normal" });
  assert.deepEqual(soon.state, first.state);
  assert.equal(soon.stageUp, false);
  const second = applyGraded(soon.state, { correct: true, unsure: false, localDate: D, at: "2026-10-07T10:00:00.000Z", mode: "practice" });
  assert.deepEqual([second.stageUp, second.state.stage, second.state.graded_today], [true, 3, 2]);
  const third = applyGraded(second.state, { correct: true, unsure: false, localDate: D, at: "2026-10-07T12:00:00.000Z", mode: "practice" });
  assert.deepEqual([third.stageUp, third.state.stage, third.state.graded_today], [true, 4, 3]);
  const fourth = applyGraded(third.state, { correct: true, unsure: false, localDate: D, at: "2026-10-07T20:00:00.000Z", mode: "practice" });
  assert.deepEqual(fourth.state, third.state);
  assert.equal(fourth.stageUp, false);
});

test("practice: a counted correct earns a star; an uncounted miss pulls due_date to tomorrow at the latest", () => {
  const base = st(2, "2026-10-01", 3, "2026-10-12");
  const up = applyGraded(base, { correct: true, unsure: false, localDate: D, at: AT, mode: "practice" });
  assert.deepEqual(up.state, { stage: 3, due_date: "2026-10-14", interval_days: 7, last_graded_on: D, graded_today: 1, last_graded_at: AT });
  assert.equal(up.stageUp, true);
  const recent = st(2, D, 3, "2026-10-12", 1, "2026-10-07T14:00:00.000Z");      // counted 1 h ago
  assert.deepEqual(applyGraded(recent, { correct: true, unsure: false, localDate: D, at: AT, mode: "practice" }).state, recent);
  const miss = applyGraded(recent, { correct: false, unsure: false, localDate: D, at: AT, mode: "practice" });
  assert.deepEqual(miss.state, { ...recent, due_date: "2026-10-08" });
  const earlier = st(2, D, 3, "2026-10-05", 1, "2026-10-07T14:00:00.000Z");
  assert.equal(applyGraded(earlier, { correct: false, unsure: false, localDate: D, at: AT, mode: "practice" }).state.due_date, "2026-10-05");
  const noDue = st(1, D, 0, null, 3, "2026-10-07T09:00:00.000Z");                // already 3 counted today
  assert.equal(applyGraded(noDue, { correct: false, unsure: true, localDate: D, at: AT, mode: "practice" }).state.due_date, "2026-10-08");
});

test("an out-of-order answer (earlier local date or earlier at) changes nothing", () => {
  const base = st(3, "2026-10-08", 7, "2026-10-15");
  for (const mode of ["normal", "practice"]) {
    const r = applyGraded(base, { correct: false, unsure: false, localDate: D, at: AT, mode });
    assert.deepEqual(r.state, base);
    assert.equal(r.stageUp, false);
  }
  const later = st(3, D, 7, "2026-10-14", 1, "2026-10-07T18:00:00.000Z");
  const r = applyGraded(later, { correct: false, unsure: false, localDate: D, at: AT, mode: "practice" });
  assert.deepEqual(r.state, later, "a practice miss with an earlier at does not pull the due date");
});

test("applyGraded accepts state without the new fields (older payloads)", () => {
  const old = { stage: 2, due_date: "2026-10-10", interval_days: 3, last_graded_on: D };   // graded today, pre-upgrade
  const r = applyGraded(old, { correct: true, unsure: false, localDate: D, at: AT, mode: "practice" });
  assert.deepEqual(r.state, { stage: 3, due_date: "2026-10-14", interval_days: 7, last_graded_on: D, graded_today: 2, last_graded_at: AT });
});
