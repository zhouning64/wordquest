// "Who's learning?" picker (#/profiles). Its GET /api/profiles doubles as the access-gate check.
import { h, mount } from "../ui.js";

export const LAST_PROFILE_KEY = "wq-last-profile";

export function rememberProfile(id) {
  try { globalThis.localStorage?.setItem(LAST_PROFILE_KEY, id); } catch { /* storage blocked */ }
}

export async function render(root, ctx) {
  const profiles = await ctx.api.get("/api/profiles");
  if (!ctx.alive()) return;
  ctx.state.profiles = profiles;

  const pick = (p) => {
    rememberProfile(p.id);
    ctx.state.profileId = p.id;
    ctx.navigate(`#/home/${encodeURIComponent(p.id)}`);
  };

  const body = profiles.length
    ? h("div", { class: "profile-grid" },
        profiles.map((p) => h("button", {
          class: "profile-tile", type: "button", onclick: () => pick(p), "aria-label": `Learn as ${p.name}`,
        }, h("span", { class: "avatar", "aria-hidden": "true" }, p.avatar || "🙂"), h("span", { class: "pname" }, p.name))))
    : h("div", { class: "card center" },
        h("div", { class: "big-emoji", "aria-hidden": "true" }, "👋"),
        h("p", null, h("b", null, "No learners yet.")),
        h("p", { class: "muted" }, "A grown-up can add one in the Parent area."));

  mount(root,
    h("div", { class: "hero" }, h("h1", null, "WordQuest"), h("p", null, "Who's learning today?")),
    body,
    h("p", { class: "center" }, h("a", { class: "parent-link", href: "#/parent" }, "Parent")),
  );
}
