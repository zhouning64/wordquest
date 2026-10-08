import test from "node:test";
import assert from "node:assert/strict";
import { summarizeResults } from "../../web/js/screens/results.js";
import { activeMinutes, flushThenFinish, MAX_ACTIVE_MINUTES } from "../../web/js/screens/session.js";
import { api, EventQueue } from "../../web/js/api.js";

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

// ---- fix round 1: /finish is posted only after every pending event was uploaded ----
class MemStorage {
  constructor() { this.m = new Map(); }
  getItem(k) { return this.m.has(k) ? this.m.get(k) : null; }
  setItem(k, v) { this.m.set(k, String(v)); }
  removeItem(k) { this.m.delete(k); }
  key(i) { return [...this.m.keys()][i] ?? null; }
  get length() { return this.m.size; }
}

const reply = (status, body) => ({ ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body) });
const evt = (n) => ({
  client_event_id: `evt-${n}`, kind: "answer", word: "frugal", correct: true,
  ms: 10, local_date: "2026-10-07", at: "2026-10-07T10:00:00.000Z",
});

// A stub fetch: `eventsStatus` decides what POST .../events answers; /finish always succeeds.
function server(eventsStatus) {
  const calls = [];
  globalThis.fetch = async (url, opts) => {
    calls.push(url);
    if (url.endsWith("/events")) {
      if (eventsStatus === "offline") throw new TypeError("Failed to fetch");
      const body = JSON.parse(opts.body);
      return eventsStatus === 200
        ? reply(200, { accepted: body.events.map((e) => e.client_event_id) })
        : reply(eventsStatus, { detail: "boom" });
    }
    return reply(200, { accuracy: 100, answered: 2 });
  };
  return calls;
}

function queueWith(n) {
  const q = new EventQueue("s1", { storage: new MemStorage() });
  for (let i = 1; i <= n; i++) q.push(evt(i));
  return q;
}

const postFinish = () => api.post("/api/sessions/s1/finish", { active_minutes: 3 });

test("flushThenFinish uploads pending events, then posts /finish", async () => {
  const calls = server(200);
  const q = queueWith(2);
  const res = await flushThenFinish(q, postFinish);
  assert.deepEqual(res, { accuracy: 100, answered: 2 });
  assert.equal(q.pending, 0);
  assert.deepEqual(calls, ["/api/sessions/s1/events", "/api/sessions/s1/finish"]);
});

test("flushThenFinish posts /finish straight away when nothing is pending", async () => {
  const calls = server(200);
  const res = await flushThenFinish(queueWith(0), postFinish);
  assert.equal(res.answered, 2);
  assert.deepEqual(calls, ["/api/sessions/s1/finish"]);
});

for (const [name, status] of [["offline", "offline"], ["server error", 500], ["rate limited", 429]]) {
  test(`flushThenFinish does not post /finish while events are still pending (${name})`, async () => {
    const calls = server(status);
    const storage = new MemStorage();
    const q = new EventQueue("s1", { storage });
    q.push(evt(1));
    q.push(evt(2));
    const res = await flushThenFinish(q, postFinish);
    assert.equal(res, null, "null = show the offline results");
    assert.ok(!calls.some((u) => u.endsWith("/finish")), "the session is not finished on the server");
    assert.equal(q.pending, 2);
    assert.ok(storage.getItem("wq-events-s1"), "the events stay in localStorage for the next visit");
  });
}

test("a rejected (422) batch is dropped by the queue, so nothing is pending and /finish still goes out", async () => {
  const calls = server(422);
  const q = queueWith(2);
  const res = await flushThenFinish(q, postFinish);
  assert.equal(q.pending, 0);
  assert.equal(res.answered, 2);
  assert.ok(calls.some((u) => u.endsWith("/finish")));
});

test("flushThenFinish lets a /finish failure through so the caller can handle 401", async () => {
  server(200);
  const err = Object.assign(new Error("nope"), { status: 401 });
  await assert.rejects(flushThenFinish(queueWith(1), async () => { throw err; }), (e) => e === err);
});

test("active_minutes is rounded and clamped to the 600 the server accepts", () => {
  assert.equal(MAX_ACTIVE_MINUTES, 600);
  assert.equal(activeMinutes(0, 4 * 60000 + 20000), 4);
  assert.equal(activeMinutes(1000, 500), 0, "a clock that went backwards never gives a negative");
  assert.equal(activeMinutes(0, 20 * 3600 * 1000), 600);
});
