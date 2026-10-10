// Display-only mirror of app/learning/srs.py (spec §8.5). The server stays authoritative;
// tests/js/srs.test.mjs checks this file against tests/srs_vectors.json.

export const INTERVALS = { 1: 1, 2: 3, 3: 7, 4: 14, 5: 30 };
export const MAX_COUNTED_PER_DAY = 3;
export const MIN_HOURS_BETWEEN_COUNTED = 2;

// "YYYY-MM-DD" + n days, computed in UTC so DST never shifts the date.
export function addDays(localDate, n) {
  const [y, m, d] = String(localDate).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

const MIN_GAP_MS = MIN_HOURS_BETWEEN_COUNTED * 3_600_000;
const COUNTING_MODES = ["normal", "practice"];

// Milliseconds since the epoch, or null when missing or unparseable (mirrors _epoch_ms in srs.py).
function epochMs(at) {
  if (!at) return null;
  const ms = Date.parse(at);
  return Number.isNaN(ms) ? null : ms;
}

// The word's counted answers on localDate. A row graded that day before last_graded_at existed (pre-upgrade)
// counts as one answer, with the 2-hour gap already satisfied.
export function countedOn(state, localDate) {
  if ((state.last_graded_on ?? null) !== localDate) return 0;
  if ((state.last_graded_at ?? null) === null) return Math.max(1, state.graded_today ?? 0);
  return state.graded_today ?? 0;
}

// Dated before the word's last counted answer, by learner date or by `at`: such an answer updates counts only.
export function isOutOfOrder(state, localDate, at) {
  const lastOn = state.last_graded_on ?? null;
  if (lastOn !== null && localDate < lastOn) return true;
  const last = epochMs(state.last_graded_at);
  const now = epochMs(at);
  return last !== null && now !== null && now < last;
}

// Counted ("stage-changing"): normal or practice session, not out of order, fewer than MAX_COUNTED_PER_DAY counted
// answers on localDate, and at least MIN_HOURS_BETWEEN_COUNTED since the last counted one (even across midnight).
export function isStageChanging(state, localDate, mode, at) {
  if (!COUNTING_MODES.includes(mode) || isOutOfOrder(state, localDate, at)) return false;
  if (countedOn(state, localDate) >= MAX_COUNTED_PER_DAY) return false;
  const now = epochMs(at);
  if (now === null) return false;
  const last = epochMs(state.last_graded_at);
  return last === null || now - last >= MIN_GAP_MS;
}

// state = {stage, due_date, interval_days, last_graded_on, graded_today, last_graded_at}; `at` is the answer's UTC
// ISO time (the same value the event carries). Returns a NEW state (input untouched).
export function applyGraded(state, { correct, unsure = false, localDate, at, mode }) {
  const s = {
    stage: state.stage ?? 0,
    due_date: state.due_date ?? null,
    interval_days: state.interval_days ?? 0,
    last_graded_on: state.last_graded_on ?? null,
    graded_today: state.graded_today ?? 0,
    last_graded_at: state.last_graded_at ?? null,
  };
  const ok = Boolean(correct) && !unsure;

  // An answer dated before the last counted answer only updates counts (server side).
  if (isOutOfOrder(s, localDate, at)) {
    return { state: s, stageUp: false };
  }

  if (isStageChanging(s, localDate, mode, at)) {
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
    s.graded_today = countedOn(s, localDate) + 1;   // before last_graded_on moves: a new date starts at 1
    s.last_graded_on = localDate;
    s.last_graded_at = at;
    return { state: s, stageUp: s.stage > before };
  }

  if (mode === "practice" && !ok) {
    const tomorrow = addDays(localDate, 1);
    const current = s.due_date ?? tomorrow;
    s.due_date = current < tomorrow ? current : tomorrow;
  }
  return { state: s, stageUp: false };
}
