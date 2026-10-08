// Shared access-code gate (#/gate). Shown whenever an API call answers 401 access_code_required.
import { h, mount } from "../ui.js";

export function render(root, ctx) {
  const input = h("input", {
    id: "gate-code", type: "password", autocomplete: "off", autocapitalize: "off",
    spellcheck: "false", placeholder: "Access code", "aria-label": "Access code", required: true,
  });
  const msg = h("p", { class: "gate-msg", role: "alert", "aria-live": "polite" });
  const btn = h("button", { class: "btn btn-primary", type: "submit" }, "Enter →");

  async function submit(e) {
    e.preventDefault();
    const code = input.value.trim();
    if (!code) return;
    btn.disabled = true;
    msg.textContent = "";
    try {
      await ctx.api.post("/api/auth/site", { code });
      ctx.state.profiles = null;
      ctx.navigate("#/profiles");
    } catch (err) {
      if (err.status === 429) msg.textContent = "Too many tries. Please wait 15 minutes and try again.";
      else if (err.status === 0) msg.textContent = "Can't reach WordQuest. Check the connection.";
      else msg.textContent = "That code didn't work. Try again.";
      input.select();
    } finally {
      btn.disabled = false;
    }
  }

  mount(root,
    h("div", { class: "hero" }, h("h1", null, "WordQuest"), h("p", null, "Enter the family access code to start.")),
    h("form", { class: "card gate", onsubmit: submit },
      h("label", { for: "gate-code", class: "field-label" }, "Access code"),
      input, msg, btn),
  );
  input.focus();
}
