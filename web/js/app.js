// Hash router. Screens are loaded with dynamic import() so a screen that fails to load (or does not
// exist yet) never breaks the others. Every screen module exports render(root, ctx); render may be
// async and may return a cleanup function that runs when the route changes.
import { api, ApiError, flushPendingQueues } from "./api.js";
import { h, mount } from "./ui.js";

export const PARENT_SCREENS = ["login", "profiles", "lists", "preview", "stats", "backup"];

const LOADERS = {
  gate: () => import("./screens/gate.js"),
  profiles: () => import("./screens/profiles.js"),
  home: () => import("./screens/home.js"),
  session: () => import("./screens/session.js"),
  parent: (name) => import(`./parent/${name}.js`),
};

// "#/home/abc" → {screen: "home", name: "home", params: ["abc"]}
// "#/parent" → {screen: "parent", name: "login", params: []}; "#/parent/lists/x" → name "lists", params ["x"]
export function parseRoute(hash) {
  const raw = String(hash || "").replace(/^#\/?/, "");
  const parts = raw.split("/").filter(Boolean).map((p) => {
    try { return decodeURIComponent(p); } catch { return p; }
  });
  if (!parts.length) return { screen: "start", name: "start", params: [] };
  const [head, ...rest] = parts;
  if (head === "parent") {
    const name = rest[0] || "login";
    if (!PARENT_SCREENS.includes(name)) return { screen: "notfound", name: "notfound", params: [] };
    return { screen: "parent", name, params: rest.slice(1) };
  }
  if (head === "gate" || head === "profiles") return { screen: head, name: head, params: [] };
  if ((head === "home" || head === "session") && rest[0]) return { screen: head, name: head, params: [rest[0]] };
  return { screen: "notfound", name: "notfound", params: [] };
}

export function errorMessage(err) {
  if (err && err.status === 0) return "Can't reach WordQuest. Check the connection and try again.";
  if (err && err.status === 429) return "Too many tries. Please wait a few minutes and try again.";
  return (err && err.message) || "Something went wrong.";
}

const state = { profiles: null, profileId: null, session: null };
let root = null;
let cleanup = null;
let seq = 0;

export function navigate(hash, { replace = false } = {}) {
  if (replace) {
    history.replaceState(null, "", hash);
    route();
  } else if (location.hash === hash) {
    route();
  } else {
    location.hash = hash;   // the hashchange listener calls route()
  }
}

function showError(err, retry) {
  mount(root,
    h("div", { class: "card error-card", role: "alert" },
      h("h2", null, "Oops"),
      h("p", null, errorMessage(err)),
      h("button", { class: "btn btn-primary", type: "button", onclick: retry }, "Try again"),
      h("button", { class: "btn btn-ghost", type: "button", onclick: () => navigate("#/profiles") }, "← Home")),
  );
}

function handleError(err) {
  if (err instanceof ApiError && err.status === 401) {
    if (err.detail === "access_code_required") return navigate("#/gate", { replace: true });
    if (err.detail === "parent_login_required") return navigate("#/parent/login", { replace: true });
  }
  showError(err, () => route());
}

async function route() {
  const my = ++seq;
  if (cleanup) {
    try { cleanup(); } catch { /* a broken cleanup must not block navigation */ }
    cleanup = null;
  }
  const r = parseRoute(location.hash);
  root.className = "wrap";
  window.scrollTo(0, 0);
  if (r.screen === "start" || r.screen === "notfound") {
    navigate("#/profiles", { replace: true });
    return;
  }
  let mod;
  try {
    mod = r.screen === "parent" ? await LOADERS.parent(r.name) : await LOADERS[r.screen]();
  } catch (err) {
    if (my === seq) showError(new Error("This screen isn't available yet."), () => route());
    return;
  }
  if (my !== seq) return;
  const ctx = {
    api,
    state,
    navigate,
    params: r.params,
    alive: () => my === seq,
    fail: (err) => { if (my === seq) handleError(err); },
  };
  try {
    const result = await mod.render(root, ctx);
    if (typeof result === "function") {
      if (my === seq) cleanup = result;
      else result();
    }
  } catch (err) {
    if (my === seq) handleError(err);
  }
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  root = document.getElementById("app");
  window.addEventListener("hashchange", () => route());
  flushPendingQueues().catch(() => { /* retried on the next visit */ });
  route();
}
