// Learner home (#/home/<profileId>): progress chips, Start session, Practice, preparing notice.
import { h, localDate, mount, toast } from "../ui.js";
import { rememberProfile } from "./profiles.js";

const DEFAULT_NEW_PER_SESSION = 5;   // ProfileSettings.new_words_per_session default
const DEFAULT_SESSION_MINUTES = 15;  // ProfileSettings.session_minutes default
const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// Normalizes GET /api/profiles/{id}/home (Task 17): {profile: {..., new_words_per_session}, mastered, learning,
// new, due_today, ready_new, practice_eligible, preparing: {ready, total}} — counts flat at the top level.
export function summarizeHome(home) {
  const hm = home || {};
  const due = num(hm.due_today);
  const readyNew = num(hm.ready_new);
  const perRaw = hm.profile?.new_words_per_session;
  const perSession = perRaw === undefined || perRaw === null ? DEFAULT_NEW_PER_SESSION : num(perRaw);
  const minRaw = hm.profile?.session_minutes;
  const minutes = minRaw === undefined || minRaw === null ? DEFAULT_SESSION_MINUTES : num(minRaw);
  // Same sizing as the server (app/learning/session.py capacity and _select_words): reviews fill the session first,
  // and each new word needs two slots (its intro and its question).
  const cap = Math.max(10, 2 * minutes);
  const reviews = Math.min(due, cap);
  const room = due >= cap ? 0 : Math.floor((cap - due) / 2);
  const plannedNew = Math.min(readyNew, perSession, room);
  const prep = hm.preparing;
  const preparing = prep && num(prep.total) > num(prep.ready) ? { ready: num(prep.ready), total: num(prep.total) } : null;
  const startLabel = reviews === 0 && plannedNew === 0
    ? "Nothing due right now"
    : `${plural(reviews, "review", "reviews")} + ${plannedNew} new`;
  return {
    mastered: num(hm.mastered),
    learning: num(hm.learning),
    fresh: num(hm.new),
    due,
    readyNew,
    plannedNew,
    practiceEligible: num(hm.practice_eligible),
    preparing,
    startLabel,
  };
}

export function preparingMessage(p) {
  return `Your words are still being prepared — ${num(p && p.ready)} of ${num(p && p.total)} ready.`;
}

// Text for a session payload whose queue is empty (spec §8.1).
export function emptyMessage(payload) {
  if (payload && payload.empty_reason === "preparing") return preparingMessage(payload.preparing);
  if (payload && payload.empty_reason === "nothing_due") return "Nothing due today — practice shaky words?";
  return "No words to practice yet. Ask a grown-up to add a word list.";
}

export async function render(root, ctx) {
  const id = ctx.params[0];
  const today = localDate();
  let profiles;
  let home;
  try {
    [profiles, home] = await Promise.all([
      ctx.state.profiles ? Promise.resolve(ctx.state.profiles) : ctx.api.get("/api/profiles"),
      ctx.api.get(`/api/profiles/${encodeURIComponent(id)}/home?local_date=${today}`),
    ]);
  } catch (err) {
    if (err.status === 404) {   // profile was deleted: back to the picker
      if (ctx.alive()) ctx.navigate("#/profiles", { replace: true });
      return;
    }
    throw err;
  }
  if (!ctx.alive()) return;
  ctx.state.profiles = profiles;
  const profile = profiles.find((p) => p.id === id) || { id, name: "Learner", avatar: "🙂" };
  ctx.state.profileId = id;
  rememberProfile(id);
  const s = summarizeHome(home);

  const notice = h("div", { class: "notice", role: "status", "aria-live": "polite", hidden: true });
  const showNotice = (text) => {
    notice.textContent = text;
    notice.hidden = false;
  };

  const chip = (icon, n, label) => h("div", { class: "chip" }, `${icon} ${n}`, h("small", null, label));

  const startBtn = h("button", { class: "btn btn-primary", type: "button", onclick: () => start("normal") },
    "▶ Start session", h("span", { class: "btn-sub" }, s.startLabel));
  const practiceBtn = s.practiceEligible > 0
    ? h("button", { class: "btn btn-secondary", type: "button", onclick: () => start("practice") },
        "🔁 Practice shaky words", h("span", { class: "btn-sub" }, plural(s.practiceEligible, "word", "words")))
    : null;

  async function start(mode) {
    startBtn.disabled = true;
    if (practiceBtn) practiceBtn.disabled = true;
    try {
      const payload = await ctx.api.post(`/api/profiles/${encodeURIComponent(id)}/sessions`, { mode, local_date: localDate() });
      if (!ctx.alive()) return;
      if (payload.empty_reason || !(payload.queue && payload.queue.length)) {
        showNotice(emptyMessage(payload));
        return;
      }
      ctx.state.session = payload;
      ctx.navigate(`#/session/${encodeURIComponent(payload.session_id)}`);
    } catch (err) {
      if (err.status === 401) ctx.fail(err);
      else toast("Couldn't start a session — please try again.");
    } finally {
      startBtn.disabled = false;
      if (practiceBtn) practiceBtn.disabled = false;
    }
  }

  mount(root,
    h("div", { class: "hero home-hero" },
      h("span", { class: "avatar avatar-lg", "aria-hidden": "true" }, profile.avatar || "🙂"),
      h("div", null, h("h1", null, `Hi, ${profile.name}!`), h("p", null, "Ready for today's words?"))),
    h("div", { class: "chips" },
      chip("🌟", s.mastered, "mastered"),
      chip("📖", s.learning, "learning"),
      chip("🆕", s.fresh, "new"),
      chip("📅", s.due, "due today")),
    s.preparing ? h("div", { class: "notice", role: "status" }, "⏳ ", preparingMessage(s.preparing)) : null,
    h("div", { class: "card" }, startBtn, practiceBtn, notice),
    h("button", { class: "btn btn-ghost", type: "button", onclick: () => ctx.navigate("#/profiles") }, "↩ Switch learner"),
    h("p", { class: "center" }, h("a", { class: "parent-link", href: "#/parent" }, "Parent")),
  );
}
