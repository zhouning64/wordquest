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
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener() {}
  removeEventListener() {}
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
