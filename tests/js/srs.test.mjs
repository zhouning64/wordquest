import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { INTERVALS, addDays, applyGraded, isStageChanging } from "../../web/js/srs.js";

const vectors = JSON.parse(readFileSync(new URL("../srs_vectors.json", import.meta.url), "utf8"));
const FIELDS = ["stage", "due_date", "interval_days", "last_graded_on"];
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
      mode: v.event.mode,
    });
    assert.deepEqual(pick(state), pick(v.after));
    assert.equal(stageUp, v.stage_up);
    assert.deepEqual(v.before, before, "applyGraded must not mutate its input");
  });
}

const D = "2026-10-07";
const st = (stage, last = null, interval_days = 0, due_date = null) => ({ stage, due_date, interval_days, last_graded_on: last });

test("interval table matches spec §8.5", () => {
  assert.deepEqual(INTERVALS, { 1: 1, 2: 3, 3: 7, 4: 14, 5: 30 });
});

test("addDays crosses month, year and leap day", () => {
  assert.equal(addDays("2026-10-07", 1), "2026-10-08");
  assert.equal(addDays("2026-10-31", 1), "2026-11-01");
  assert.equal(addDays("2026-12-31", 1), "2027-01-01");
  assert.equal(addDays("2028-02-28", 1), "2028-02-29");
  assert.equal(addDays("2026-03-08", 30), "2026-04-07");
});

test("isStageChanging: only the first graded answer of a day in normal mode", () => {
  assert.equal(isStageChanging(st(0), D, "normal"), true);
  assert.equal(isStageChanging(st(2, "2026-10-06"), D, "normal"), true);
  assert.equal(isStageChanging(st(2, D), D, "normal"), false);
  assert.equal(isStageChanging(st(2, "2026-10-06"), D, "practice"), false);
});

test("correct from stage 0 → stage 1, due tomorrow, stageUp", () => {
  const { state, stageUp } = applyGraded(st(0), { correct: true, unsure: false, localDate: D, mode: "normal" });
  assert.deepEqual(state, { stage: 1, due_date: "2026-10-08", interval_days: 1, last_graded_on: D });
  assert.equal(stageUp, true);
});

test("correct 4 → 5 uses the table (30 days)", () => {
  const { state } = applyGraded(st(4, "2026-09-20", 14), { correct: true, unsure: false, localDate: D, mode: "normal" });
  assert.deepEqual(state, { stage: 5, due_date: "2026-11-06", interval_days: 30, last_graded_on: D });
});

test("mastered word doubles its interval, clamped to 30..60", () => {
  let r = applyGraded(st(5, "2026-09-01", 30), { correct: true, unsure: false, localDate: D, mode: "normal" });
  assert.equal(r.state.stage, 5);
  assert.equal(r.state.interval_days, 60);
  assert.equal(r.stageUp, false);
  r = applyGraded(st(5, "2026-09-01", 45), { correct: true, unsure: false, localDate: D, mode: "normal" });
  assert.equal(r.state.interval_days, 60);
  r = applyGraded(st(5, "2026-09-01", 0), { correct: true, unsure: false, localDate: D, mode: "normal" });
  assert.equal(r.state.interval_days, 30);
});

test("miss: stage 0 stays 0, stage 1 stays 1, stage 4 drops to 2; due tomorrow", () => {
  for (const [from, to] of [[0, 0], [1, 1], [2, 1], [4, 2], [5, 3]]) {
    const { state, stageUp } = applyGraded(st(from, "2026-10-01", 7), { correct: false, unsure: false, localDate: D, mode: "normal" });
    assert.equal(state.stage, to, `from ${from}`);
    assert.equal(state.interval_days, 1);
    assert.equal(state.due_date, "2026-10-08");
    assert.equal(state.last_graded_on, D);
    assert.equal(stageUp, false);
  }
});

test("unsure is a miss even if correct is passed as true", () => {
  const { state } = applyGraded(st(3, "2026-10-01", 7), { correct: true, unsure: true, localDate: D, mode: "normal" });
  assert.equal(state.stage, 1);
});

test("second answer the same day changes nothing", () => {
  const first = applyGraded(st(2, "2026-10-01", 3), { correct: true, unsure: false, localDate: D, mode: "normal" });
  const second = applyGraded(first.state, { correct: false, unsure: false, localDate: D, mode: "normal" });
  assert.deepEqual(second.state, first.state);
  assert.equal(second.stageUp, false);
});

test("practice: correct changes nothing; miss pulls due_date to tomorrow at the latest", () => {
  const base = st(2, "2026-10-01", 3, "2026-10-12");
  assert.deepEqual(applyGraded(base, { correct: true, unsure: false, localDate: D, mode: "practice" }).state, base);
  const miss = applyGraded(base, { correct: false, unsure: false, localDate: D, mode: "practice" });
  assert.deepEqual(miss.state, { ...base, due_date: "2026-10-08" });
  const earlier = st(2, "2026-10-01", 3, "2026-10-05");
  assert.equal(applyGraded(earlier, { correct: false, unsure: false, localDate: D, mode: "practice" }).state.due_date, "2026-10-05");
  const noDue = st(1, null, 0, null);
  assert.equal(applyGraded(noDue, { correct: false, unsure: true, localDate: D, mode: "practice" }).state.due_date, "2026-10-08");
});

test("an answer dated before last_graded_on changes nothing", () => {
  const base = st(3, "2026-10-08", 7, "2026-10-15");
  for (const mode of ["normal", "practice"]) {
    const r = applyGraded(base, { correct: false, unsure: false, localDate: D, mode });
    assert.deepEqual(r.state, base);
    assert.equal(r.stageUp, false);
  }
});
