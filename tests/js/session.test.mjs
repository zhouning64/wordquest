import test from "node:test";
import assert from "node:assert/strict";
import { render } from "../../web/js/screens/session.js";

// Just enough DOM for the session screen and its intro card (Node has none).
class FakeNode {
  constructor(tag) {
    this.nodeType = tag === "#text" ? 3 : 1;
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.attrs = {};
    this.style = {};
    this.className = "";
    this.textContent = "";
    this.classList = { toggle() {}, add() {}, remove() {}, contains: () => false };
    this.listeners = {};
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener() {}
  click() { for (const fn of this.listeners.click || []) fn({ stopPropagation() {} }); }
  focus() {}
  querySelector() { return null; }
}

function installDom() {
  const listeners = new Set();
  globalThis.document = {
    createElement: (tag) => new FakeNode(tag),
    createTextNode: (text) => Object.assign(new FakeNode("#text"), { textContent: String(text) }),
    getElementById: () => null,
    addEventListener: (type, fn) => listeners.add(fn),
    removeEventListener: (type, fn) => listeners.delete(fn),
  };
  globalThis.window = { scrollTo() {} };
  return listeners;
}

function payload() {
  return {
    session_id: "s1", mode: "normal", local_date: "2026-10-07", settings: { session_minutes: 15 },
    queue: [{ kind: "intro", word: "frugal" }],
    words: { frugal: { stage: 0, card: { short_def: "careful with money" }, image_url: null } },
  };
}

function makeCtx(state) {
  const navigations = [];
  return {
    navigations,
    ctx: {
      api: {}, state, params: ["s1"], alive: () => true, fail: () => {},
      navigate: (hash, opts) => navigations.push([hash, opts]),
    },
  };
}

test("leaving a session (Back) drops its payload, so Forward cannot replay it under new event ids", () => {
  const listeners = installDom();
  const state = { profileId: "p1", session: payload() };
  const { ctx, navigations } = makeCtx(state);

  const teardown = render(new FakeNode("main"), ctx);
  assert.equal(typeof teardown, "function", "the session screen started");
  assert.equal(listeners.size, 1);
  teardown();   // the router runs the cleanup when the hash changes (Back)
  assert.equal(state.session, null);
  assert.equal(listeners.size, 0);

  const again = render(new FakeNode("main"), ctx);   // Forward to #/session/s1
  assert.equal(again, undefined, "no second session is started");
  assert.deepEqual(navigations, [["#/home/p1", { replace: true }]]);
});

test("teardown leaves a newer session payload alone", () => {
  installDom();
  const state = { profileId: "p1", session: payload() };
  const { ctx } = makeCtx(state);
  const teardown = render(new FakeNode("main"), ctx);
  const newer = { ...payload(), session_id: "s2" };
  state.session = newer;
  teardown();
  assert.equal(state.session, newer);
});

// ---- a session made only of intro cards (each first question was too close to its intro) ----
const nodes = (n) => [n, ...n.children.flatMap((c) => (typeof c === "object" ? nodes(c) : []))];
const text = (n) => nodes(n).filter((x) => x.nodeType === 3).map((x) => x.textContent).join("");
const statValue = (root, label) => {
  const stat = nodes(root).find((x) => x.className === "stat" && text(x).endsWith(label));
  return stat && text(stat.children[0]);
};
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); };

test("a session of intros only ends on the results screen with the new words met, not silently at home", async () => {
  installDom();
  const uploads = [];
  globalThis.fetch = async (url, opts) => {
    uploads.push(url);
    const accepted = JSON.parse(opts.body).events.map((e) => e.client_event_id);
    return { ok: true, status: 200, text: async () => JSON.stringify({ accepted }) };
  };
  try {
    const state = { profileId: "p1", session: payload() };
    const { ctx, navigations } = makeCtx(state);
    const posted = [];
    ctx.api = {
      post: async (path) => {
        posted.push(path);
        return { accuracy: 0, answered: 0, new_words: ["frugal"], stars_up: [], keep_practicing: [],
          break_reminder: false, break_message: "" };
      },
    };
    const root = new FakeNode("main");
    render(root, ctx);
    const gotIt = nodes(root).find((x) => x.tagName === "BUTTON" && text(x).startsWith("Got it"));
    gotIt.click();   // the last (only) item: the queue is exhausted
    await settle();

    assert.deepEqual(navigations, [], "not sent home");
    assert.ok(uploads.some((u) => u.endsWith("/events")), "intro_seen is uploaded before /finish");
    assert.deepEqual(posted, ["/api/sessions/s1/finish"]);
    assert.ok(text(root).includes("Session complete!"));
    assert.equal(statValue(root, "new words met"), "1");
    assert.equal(statValue(root, "questions"), "0");
    assert.equal(statValue(root, "accuracy"), "—", "no questions: no 0% accuracy");
  } finally {
    delete globalThis.fetch;
  }
});
