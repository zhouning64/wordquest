// Display-only mirror of app/learning/srs.py (spec §8.5). The server stays authoritative;
// tests/js/srs.test.mjs checks this file against tests/srs_vectors.json.

export const INTERVALS = { 1: 1, 2: 3, 3: 7, 4: 14, 5: 30 };

// "YYYY-MM-DD" + n days, computed in UTC so DST never shifts the date.
export function addDays(localDate, n) {
  const [y, m, d] = String(localDate).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

export function isStageChanging(state, localDate, mode) {
  const last = state.last_graded_on ?? null;
  return mode === "normal" && (last === null || localDate > last);
}

// state = {stage, due_date, interval_days, last_graded_on}; returns a NEW state (input untouched).
export function applyGraded(state, { correct, unsure = false, localDate, mode }) {
  const s = {
    stage: state.stage ?? 0,
    due_date: state.due_date ?? null,
    interval_days: state.interval_days ?? 0,
    last_graded_on: state.last_graded_on ?? null,
  };
  const ok = Boolean(correct) && !unsure;

  // An answer dated before the last stage-changing answer only updates counts (server side).
  if (s.last_graded_on !== null && localDate < s.last_graded_on) {
    return { state: s, stageUp: false };
  }

  if (isStageChanging(s, localDate, mode)) {
    const before = s.stage;
    if (ok) {
      s.stage = Math.min(5, before + 1);
      s.interval_days = before < 5
        ? INTERVALS[s.stage]
        : Math.min(60, Math.max(30, s.interval_days * 2));
    } else {
      s.stage = before === 0 ? 0 : Math.max(1, before - 2);
      s.interval_days = 1;
    }
    s.due_date = addDays(localDate, s.interval_days);
    s.last_graded_on = localDate;
    return { state: s, stageUp: s.stage > before };
  }

  if (mode === "practice" && !ok) {
    const tomorrow = addDays(localDate, 1);
    const current = s.due_date ?? tomorrow;
    s.due_date = current < tomorrow ? current : tomorrow;
  }
  return { state: s, stageUp: false };
}
