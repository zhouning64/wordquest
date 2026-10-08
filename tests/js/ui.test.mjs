import test from "node:test";
import assert from "node:assert/strict";
import { esc, localDate, mount, seededRng, shuffle, stars, uuid } from "../../web/js/ui.js";

test("esc escapes HTML-significant characters", () => {
  assert.equal(esc(`<b a="1">Tom's & Jerry</b>`), "&lt;b a=&quot;1&quot;&gt;Tom&#39;s &amp; Jerry&lt;/b&gt;");
  assert.equal(esc(null), "");
  assert.equal(esc(3), "3");
});

test("stars renders 0..5 and clamps", () => {
  assert.equal(stars(0), "☆☆☆☆☆");
  assert.equal(stars(3), "★★★☆☆");
  assert.equal(stars(5), "★★★★★");
  assert.equal(stars(9), "★★★★★");
  assert.equal(stars(-2), "☆☆☆☆☆");
  assert.equal(stars(undefined), "☆☆☆☆☆");
});

test("shuffle returns a permutation, leaves the input alone, and is deterministic with a seeded rng", () => {
  const input = [1, 2, 3, 4, 5, 6];
  const out = shuffle(input, seededRng(7));
  assert.deepEqual(input, [1, 2, 3, 4, 5, 6]);
  assert.deepEqual([...out].sort((a, b) => a - b), input);
  assert.deepEqual(shuffle(input, seededRng(7)), out);
  const seen = new Set();
  for (let s = 0; s < 40; s++) seen.add(shuffle(input, seededRng(s)).join(","));
  assert.ok(seen.size > 10, "different seeds give different orders");
});

test("seededRng yields floats in [0, 1)", () => {
  const r = seededRng(42);
  for (let i = 0; i < 1000; i++) {
    const x = r();
    assert.ok(x >= 0 && x < 1);
  }
});

test("localDate formats the local calendar date", () => {
  assert.equal(localDate(new Date(2026, 0, 5, 23, 59)), "2026-01-05");
  assert.equal(localDate(new Date(2026, 9, 7, 0, 1)), "2026-10-07");
  assert.match(localDate(), /^\d{4}-\d{2}-\d{2}$/);
});

test("uuid is a v4 UUID, unique, with a getRandomValues fallback", () => {
  const a = uuid();
  assert.match(a, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.notEqual(uuid(), a);
  const real = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: { getRandomValues: (b) => { for (let i = 0; i < b.length; i++) b[i] = (i * 37) & 255; return b; } },
  });
  try {
    assert.match(uuid(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  } finally {
    Object.defineProperty(globalThis, "crypto", real);
  }
});

test("mount skips null/false/undefined, flattens arrays, and stringifies numbers", () => {
  const node = { nodeType: 1, id: "n" };
  const el = { replaceChildren(...kids) { this.kids = kids; } };
  assert.equal(mount(el, null, "a", [node, false, [3]], undefined, true), el);
  assert.deepEqual(el.kids, ["a", node, "3"]);
});
