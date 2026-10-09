// Results (spec §8.6): accuracy, questions, new words met, words that gained stars,
// up to 5 keep-practicing words, and the break reminder card.
import { h, mount, stars } from "../ui.js";

const count = (x) => (Array.isArray(x) ? x.length : Number(x) || 0);

// server: POST /finish response or null (offline); local: engine.stats(); settings: payload.settings
export function summarizeResults(server, local, settings) {
  const st = settings || {};
  if (server) {
    return {
      accuracy: Math.round(Number(server.accuracy) || 0),
      answered: Number(server.answered) || 0,
      newWords: count(server.new_words),
      starsUp: Array.isArray(server.stars_up) ? server.stars_up : [],
      keep: (server.keep_practicing || []).slice(0, 5),
      breakReminder: Boolean(server.break_reminder),
      breakMessage: server.break_message || "",
      offline: false,
    };
  }
  const l = local || {};
  return {
    accuracy: l.accuracy || 0,
    answered: l.answered || 0,
    newWords: count(l.newWords),
    starsUp: l.starsUp || [],
    keep: (l.keepPracticing || []).slice(0, 5),
    breakReminder: Boolean(st.break_reminder),
    breakMessage: st.break_message || "",
    offline: true,
  };
}

// ctx: {results (server or null), local (engine.stats()), settings, onHome()}
export function render(root, ctx) {
  const s = summarizeResults(ctx.results, ctx.local, ctx.settings);
  const stat = (n, label) => h("div", { class: "stat" }, h("div", { class: "n" }, String(n)), h("div", { class: "l" }, label));
  const done = h("button", { class: "btn btn-primary", type: "button", "data-enter": "", onclick: () => ctx.onHome() }, "Done →");
  mount(root,
    h("div", { class: "card center results-card" },
      h("div", { class: "big-emoji", "aria-hidden": "true" }, s.accuracy >= 80 ? "🏆" : s.accuracy >= 60 ? "💪" : "📚"),
      h("h2", null, "Session complete!"),
      s.offline
        ? h("div", { class: "notice" }, "Couldn't reach the server — your answers are saved on this device and will upload next time.")
        : null,
      h("div", { class: "stat-grid" },
        stat(s.answered ? `${s.accuracy}%` : "—", "accuracy"),   // intro cards only: no questions, no 0%
        stat(s.answered, "questions"),
        stat(s.newWords, "new words met"),
        stat(s.starsUp.length, "words gained stars")),
      s.starsUp.length ? h("div", { class: "results-list" }, h("b", null, "⭐ Stars earned: "), s.starsUp.join(", ")) : null,
      s.keep.length
        ? h("div", { class: "results-list" }, h("b", null, "Keep practicing:"),
            s.keep.map((k) => h("div", { class: "weakword" }, h("b", null, k.word), h("span", { class: "stars" }, stars(k.stage)))))
        : null),
    s.breakReminder && s.breakMessage ? h("div", { class: "eyebreak", role: "note" }, "👀 ", s.breakMessage) : null,
    done,
  );
  done.focus({ preventScroll: true });
}
