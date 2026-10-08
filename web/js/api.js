// fetch wrapper + ordered, buffered, retried event upload (spec §10 "Event buffering").
import { uuid } from "./ui.js";

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" && detail ? detail : `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;   // 0 = network failure
    this.detail = detail;   // FastAPI "detail" (string, list, or null)
  }
}

async function request(method, path, body) {
  const opts = { method, credentials: "same-origin", headers: { Accept: "application/json" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await globalThis.fetch(path, opts);
  } catch {
    throw new ApiError(0, "network_error");
  }
  let data = null;
  try {
    const text = await res.text();
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) throw new ApiError(res.status, data && data.detail !== undefined ? data.detail : null);
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body = {}) => request("POST", path, body),
  put: (path, body = {}) => request("PUT", path, body),
  patch: (path, body = {}) => request("PATCH", path, body),
  del: (path) => request("DELETE", path),
};

const PREFIX = "wq-events-";

function defaultStorage() {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;   // storage blocked (private mode / disabled cookies)
  }
}

export class EventQueue {
  // opts (all optional, for tests): storage, intervalMs (5000), maxBatch (50), now (() => ms)
  constructor(sessionId, opts = {}) {
    this.sessionId = sessionId;
    this.key = PREFIX + sessionId;
    this.storage = opts.storage !== undefined ? opts.storage : defaultStorage();
    this.intervalMs = opts.intervalMs ?? 5000;
    this.maxBatch = opts.maxBatch ?? 50;
    this.now = opts.now ?? (() => Date.now());
    this.events = this._load();
    this.inFlight = null;
    this.timer = null;
    this.failures = 0;
    this.retryAt = 0;
  }

  _load() {
    if (!this.storage) return [];
    try {
      const raw = this.storage.getItem(this.key);
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch {
      return [];
    }
  }

  _save() {
    if (!this.storage) return;
    try {
      if (this.events.length) this.storage.setItem(this.key, JSON.stringify(this.events));
      else this.storage.removeItem(this.key);
    } catch {
      /* quota exceeded or storage blocked: the in-memory queue still works */
    }
  }

  get pending() {
    return this.events.length;
  }

  push(fields) {
    const ev = { ...fields };
    if (!ev.client_event_id) ev.client_event_id = uuid();
    this.events.push(ev);
    this._save();
    return ev;
  }

  // Uploads the oldest pending events. At most one request is in flight; a second call while one
  // is running returns the same promise. Resolves true on success (or nothing to send), false on failure.
  flush() {
    if (this.inFlight) return this.inFlight;
    if (!this.events.length) return Promise.resolve(true);
    const batch = this.events.slice(0, this.maxBatch);
    const path = `/api/sessions/${encodeURIComponent(this.sessionId)}/events`;
    this.inFlight = api.post(path, { events: batch })
      .then((res) => {
        const accepted = new Set((res && res.accepted) || []);
        this.events = this.events.filter((e) => !accepted.has(e.client_event_id));
        this._save();
        this.failures = 0;
        this.retryAt = 0;
        return true;
      })
      .catch((err) => {
        if (err && err.status === 404) {          // session no longer exists: nothing can ever accept these
          this.events = [];
          this._save();
          return true;
        }
        if (err && (err.status === 400 || err.status === 422)) {   // malformed batch would block the queue forever
          const sent = new Set(batch.map((e) => e.client_event_id));
          this.events = this.events.filter((e) => !sent.has(e.client_event_id));
          this._save();
          return false;
        }
        this.failures += 1;
        this.retryAt = this.now() + Math.min(60000, 5000 * 2 ** (this.failures - 1));
        return false;
      })
      .finally(() => {
        this.inFlight = null;
      });
    return this.inFlight;
  }

  // Flush repeatedly until empty; stops at the first failure. Resolves true when nothing is left.
  async drain(maxRounds = 20) {
    for (let i = 0; i < maxRounds && this.events.length; i++) {
      const ok = await this.flush();
      if (!ok) return false;
    }
    return this.events.length === 0;
  }

  start() {
    if (this.timer) return;
    this.timer = setInterval(() => {
      if (this.now() >= this.retryAt) this.flush();
    }, this.intervalMs);
  }

  stop() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }
}

// Uploads queues left behind by earlier visits (closed tab, lost connection). Returns drained session ids.
export async function flushPendingQueues(storage = defaultStorage()) {
  if (!storage) return [];
  const ids = [];
  for (let i = 0; i < storage.length; i++) {
    const k = storage.key(i);
    if (k && k.startsWith(PREFIX)) ids.push(k.slice(PREFIX.length));
  }
  const drained = [];
  for (const sid of ids) {
    if (await new EventQueue(sid, { storage }).drain(5)) drained.push(sid);
  }
  return drained;
}
