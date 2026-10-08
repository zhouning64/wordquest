// Shared UI helpers. Nothing here touches the DOM at import time, so Node tests can import it.

export function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function appendChildren(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false || c === true) continue;
    if (Array.isArray(c)) appendChildren(el, c);
    else if (typeof c === "object" && typeof c.nodeType === "number") el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
}

// h("button", {class: "btn", onclick: fn, disabled: true}, "text", childNode, [more]) → HTMLElement.
// Strings always become text nodes, so model output is never parsed as HTML.
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = String(v);
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === "value") el.value = String(v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, String(v));
    }
  }
  appendChildren(el, children);
  return el;
}

// Replaces el's children, skipping null/undefined/false (Element.replaceChildren would print "null").
export function mount(el, ...children) {
  const nodes = [];
  const walk = (list) => {
    for (const c of list) {
      if (c === null || c === undefined || c === false || c === true) continue;
      if (Array.isArray(c)) walk(c);
      else nodes.push(typeof c === "object" ? c : String(c));
    }
  };
  walk(children);
  el.replaceChildren(...nodes);
  return el;
}

let toastTimer = null;
export function toast(msg, ms = 2200) {
  let t = document.getElementById("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    t.className = "toast";
    t.setAttribute("role", "status");
    t.setAttribute("aria-live", "polite");
    document.body.appendChild(t);
  }
  t.textContent = String(msg);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), ms);
}

export function stars(n) {
  const k = Math.max(0, Math.min(5, Math.floor(Number(n) || 0)));
  return "★".repeat(k) + "☆".repeat(5 - k);
}

// Fisher–Yates on a copy. rng() must return a float in [0, 1).
export function shuffle(arr, rng = Math.random) {
  const a = Array.from(arr);
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// Deterministic rng (mulberry32) for tests and reproducible shuffles.
export function seededRng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// The learner's local calendar date as "YYYY-MM-DD".
export function localDate(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// RFC 4122 v4 id. crypto.randomUUID only exists in secure contexts (https/localhost), so fall back
// to getRandomValues for phones/iPads that reach the server over plain http on the home network.
export function uuid() {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") {
    try { return c.randomUUID(); } catch { /* insecure context: fall through */ }
  }
  const b = new Uint8Array(16);
  if (c && typeof c.getRandomValues === "function") c.getRandomValues(b);
  else for (let i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const x = Array.from(b, (v) => v.toString(16).padStart(2, "0")).join("");
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20)}`;
}
