import test from "node:test";
import assert from "node:assert/strict";
import { api, ApiError, EventQueue, flushPendingQueues } from "../../web/js/api.js";

class MemStorage {
  constructor() { this.m = new Map(); }
  getItem(k) { return this.m.has(k) ? this.m.get(k) : null; }
  setItem(k, v) { this.m.set(k, String(v)); }
  removeItem(k) { this.m.delete(k); }
  key(i) { return [...this.m.keys()][i] ?? null; }
  get length() { return this.m.size; }
}

const jsonResponse = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  text: async () => (body === undefined ? "" : JSON.stringify(body)),
});

// Fresh stub fetch + localStorage for every test.
function stubs() {
  const storage = new MemStorage();
  const calls = [];
  let handler = async () => jsonResponse(200, { accepted: [] });
  Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true, writable: true });
  globalThis.fetch = async (url, opts) => {
    const body = opts && opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ url, method: opts.method, body, credentials: opts.credentials });
    return handler(url, opts, body);
  };
  return { storage, calls, setHandler: (fn) => { handler = fn; } };
}

const acceptAll = async (url, opts, body) => jsonResponse(200, { accepted: body.events.map((e) => e.client_event_id) });
const ev = (n) => ({
  client_event_id: `evt-000${n}`, kind: "answer", word: "frugal", correct: true,
  ms: 100 * n, local_date: "2026-10-07", at: `2026-10-07T10:00:0${n}.000Z`,
});
const ids = (events) => events.map((e) => e.client_event_id);
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };

test("api.get sends a same-origin GET and parses JSON", async () => {
  const s = stubs();
  s.setHandler(async () => jsonResponse(200, [{ id: "p1", name: "Maya", avatar: "🦊" }]));
  const data = await api.get("/api/profiles");
  assert.deepEqual(data, [{ id: "p1", name: "Maya", avatar: "🦊" }]);
  assert.equal(s.calls[0].method, "GET");
  assert.equal(s.calls[0].credentials, "same-origin");
  assert.equal(s.calls[0].body, undefined);
});

test("api.post sends a JSON body; errors become ApiError with status and detail", async () => {
  const s = stubs();
  s.setHandler(async () => jsonResponse(401, { detail: "access_code_required" }));
  await assert.rejects(api.post("/api/auth/site", { code: "nope" }), (e) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.status, 401);
    assert.equal(e.detail, "access_code_required");
    assert.equal(e.message, "access_code_required");
    return true;
  });
  assert.deepEqual(s.calls[0].body, { code: "nope" });
  assert.equal(s.calls[0].method, "POST");
});

test("put, patch and del use their HTTP methods; empty body parses as null", async () => {
  const s = stubs();
  s.setHandler(async () => jsonResponse(200));
  assert.equal(await api.put("/a", { x: 1 }), null);
  await api.patch("/b", { y: 2 });
  await api.del("/c");
  assert.deepEqual(s.calls.map((c) => c.method), ["PUT", "PATCH", "DELETE"]);
});

test("a network failure becomes ApiError status 0", async () => {
  stubs();
  globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(api.get("/api/profiles"), (e) => e instanceof ApiError && e.status === 0);
});

test("push keeps order, fills client_event_id, and mirrors to localStorage", () => {
  const s = stubs();
  const q = new EventQueue("s1");
  q.push(ev(1));
  const auto = q.push({ kind: "intro_seen", word: "lucid", local_date: "2026-10-07", at: "2026-10-07T10:00:05Z", ms: 0 });
  assert.match(auto.client_event_id, /^[0-9a-f-]{36}$/);
  const stored = JSON.parse(s.storage.getItem("wq-events-s1"));
  assert.deepEqual(ids(stored), ["evt-0001", auto.client_event_id]);
  assert.equal(q.pending, 2);
});

test("flush uploads oldest first to the session endpoint and clears accepted events", async () => {
  const s = stubs();
  s.setHandler(acceptAll);
  const q = new EventQueue("s1");
  [1, 2, 3].forEach((n) => q.push(ev(n)));
  assert.equal(await q.flush(), true);
  assert.equal(s.calls.length, 1);
  assert.equal(s.calls[0].url, "/api/sessions/s1/events");
  assert.deepEqual(ids(s.calls[0].body.events), ["evt-0001", "evt-0002", "evt-0003"]);
  assert.equal(q.pending, 0);
  assert.equal(s.storage.getItem("wq-events-s1"), null);
});

test("only one upload is in flight; events pushed meanwhile wait for the next flush", async () => {
  const s = stubs();
  const gate = deferred();
  s.setHandler(async (url, opts, body) => { await gate.promise; return acceptAll(url, opts, body); });
  const q = new EventQueue("s1");
  q.push(ev(1));
  q.push(ev(2));
  const p1 = q.flush();
  const p2 = q.flush();
  assert.equal(p1, p2);
  q.push(ev(3));
  assert.equal(s.calls.length, 1);
  gate.resolve();
  assert.equal(await p1, true);
  assert.deepEqual(ids(q.events), ["evt-0003"]);
  await q.flush();
  assert.equal(s.calls.length, 2);
  assert.deepEqual(ids(s.calls[1].body.events), ["evt-0003"]);
  assert.equal(q.pending, 0);
});

test("drops exactly the accepted ids and keeps the rest in order", async () => {
  const s = stubs();
  s.setHandler(async () => jsonResponse(200, { accepted: ["evt-0001", "evt-0003"] }));
  const q = new EventQueue("s1");
  [1, 2, 3, 4].forEach((n) => q.push(ev(n)));
  await q.flush();
  assert.deepEqual(ids(q.events), ["evt-0002", "evt-0004"]);
  assert.deepEqual(ids(JSON.parse(s.storage.getItem("wq-events-s1"))), ["evt-0002", "evt-0004"]);
});

test("a failed upload keeps every event and backs off", async () => {
  const s = stubs();
  s.setHandler(async () => jsonResponse(503, { detail: "busy" }));
  let t = 1000;
  const q = new EventQueue("s1", { now: () => t });
  q.push(ev(1));
  assert.equal(await q.flush(), false);
  assert.equal(q.pending, 1);
  assert.equal(q.retryAt, 6000);
  assert.equal(await q.flush(), false);
  assert.equal(q.retryAt, 11000);
  s.setHandler(acceptAll);
  t = 20000;
  assert.equal(await q.flush(), true);
  assert.equal(q.pending, 0);
  assert.equal(q.failures, 0);
});

test("events persist across instances (page reload) and upload after restore", async () => {
  const s = stubs();
  const first = new EventQueue("s9");
  first.push(ev(1));
  first.push(ev(2));
  const restored = new EventQueue("s9");
  assert.deepEqual(ids(restored.events), ["evt-0001", "evt-0002"]);
  s.setHandler(acceptAll);
  await restored.flush();
  assert.deepEqual(ids(s.calls[0].body.events), ["evt-0001", "evt-0002"]);
  assert.equal(s.storage.getItem("wq-events-s9"), null);
});

test("corrupt storage is ignored", () => {
  const s = stubs();
  s.storage.setItem("wq-events-s1", "{not json");
  assert.equal(new EventQueue("s1").pending, 0);
});

test("maxBatch limits a request; drain sends the rest in order", async () => {
  const s = stubs();
  s.setHandler(acceptAll);
  const q = new EventQueue("s1", { maxBatch: 2 });
  [1, 2, 3].forEach((n) => q.push(ev(n)));
  assert.equal(await q.drain(), true);
  assert.deepEqual(s.calls.map((c) => ids(c.body.events)), [["evt-0001", "evt-0002"], ["evt-0003"]]);
});

test("404 (session gone) drops the queue; 422 drops only the sent batch", async () => {
  const s = stubs();
  s.setHandler(async () => jsonResponse(404, { detail: "session_not_found" }));
  const gone = new EventQueue("old", { maxBatch: 1 });
  [1, 2].forEach((n) => gone.push(ev(n)));
  assert.equal(await gone.flush(), true);
  assert.equal(gone.pending, 0);

  s.setHandler(async () => jsonResponse(422, { detail: [{ msg: "bad" }] }));
  const bad = new EventQueue("s2", { maxBatch: 1 });
  [1, 2].forEach((n) => bad.push(ev(n)));
  assert.equal(await bad.flush(), false);
  assert.deepEqual(ids(bad.events), ["evt-0002"]);
});

test("start() flushes on an interval and stop() ends it", async () => {
  const s = stubs();
  s.setHandler(acceptAll);
  const q = new EventQueue("s1", { intervalMs: 10 });
  q.push(ev(1));
  q.start();
  await new Promise((r) => setTimeout(r, 60));
  q.stop();
  assert.equal(q.pending, 0);
  assert.equal(q.timer, null);
  const n = s.calls.length;
  q.push(ev(2));
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(s.calls.length, n);
});

test("flushPendingQueues uploads queues left by earlier sessions", async () => {
  const s = stubs();
  s.setHandler(acceptAll);
  s.storage.setItem("wq-events-a1", JSON.stringify([ev(1)]));
  s.storage.setItem("wq-events-b2", JSON.stringify([ev(2)]));
  s.storage.setItem("wq-last-profile", "p1");
  const drained = await flushPendingQueues();
  assert.deepEqual(drained.sort(), ["a1", "b2"]);
  assert.deepEqual(s.calls.map((c) => c.url).sort(), ["/api/sessions/a1/events", "/api/sessions/b2/events"]);
  assert.equal(s.storage.getItem("wq-events-a1"), null);
  assert.equal(s.storage.getItem("wq-last-profile"), "p1");
});
