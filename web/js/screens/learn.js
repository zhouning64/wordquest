// Learn page (spec §8.4). Sections in spec order; empty sections are hidden. Also exports the
// picture / word-header / highlighted-sentence pieces reused by the intro card.
import { h, mount, stars } from "../ui.js";
import { highlightParts } from "../engine.js";
import { speakButton } from "../speech.js";
import { promptNodes } from "../question.js";

const GRADS = [
  "linear-gradient(135deg,#6366f1,#a78bfa)", "linear-gradient(135deg,#0ea5e9,#22d3ee)",
  "linear-gradient(135deg,#f59e0b,#fbbf24)", "linear-gradient(135deg,#10b981,#34d399)",
  "linear-gradient(135deg,#ef4444,#f87171)", "linear-gradient(135deg,#8b5cf6,#d946ef)",
];

function gradientFor(word) {
  let n = 0;
  for (const ch of String(word)) n = (n * 31 + ch.codePointAt(0)) >>> 0;
  return GRADS[n % GRADS.length];
}

export function picture(word, data) {
  const card = (data && data.card) || {};
  const tile = () => h("div", {
    class: "tile-fallback emoji-tile", style: { background: gradientFor(word) },
    role: "img", "aria-label": `picture for ${word}`,
  }, card.emoji_scene || "📖✨");
  if (data && data.image_url) {
    const img = h("img", { class: "learn-img", src: data.image_url, alt: `illustration for ${word}` });
    img.addEventListener("error", () => img.replaceWith(tile()), { once: true });
    return img;
  }
  return tile();
}

export function highlighted(sentence, word, forms) {
  return highlightParts(sentence, word, forms).map((p) => (p.hit ? h("mark", { class: "hl" }, p.text) : p.text));
}

export function wordHeader(word, card, starCount = null) {
  return [
    h("div", { class: "learn-head" },
      h("h2", { class: "learn-word" }, word),
      speakButton(word, `Say ${word}`),
      starCount === null ? null : h("span", { class: "stars", "aria-label": `${starCount} of 5 stars` }, stars(starCount))),
    card.pos ? h("div", { class: "learn-pos" }, card.pos) : null,
  ];
}

function bannerEl(banner) {
  if (!banner) return null;
  if (banner.kind === "answer") {
    return h("div", { class: "banner banner-answer", role: "status" },
      "The answer is ", h("b", null, banner.answer || ""),
      banner.explanation ? h("span", { class: "sub" }, banner.explanation) : null);
  }
  const items = banner.items || [];
  return h("div", { class: "banner banner-missed", role: "status" },
    h("b", null, "Let's look at what you missed:"),
    items.map((m) => h("div", { class: "banner-item" },
      h("div", null, promptNodes(m.prompt)),
      h("span", { class: "sub" }, "Answer: ", h("b", null, m.answer || "")),
      m.explanation ? h("span", { class: "sub" }, m.explanation) : null)));
}

function section(title, ...children) {
  return h("section", { class: "learn-section" }, h("b", null, title), ...children);
}

// ctx: {word, data (payload.words[word]), banner, stars, canCheck, onCheck(), onBack(), readOnly}
// readOnly (Parent preview, Task 22): the page only — no check or back-to-session buttons.
export function render(root, ctx) {
  const { word, data } = ctx;
  const card = (data && data.card) || {};
  const forms = card.forms || [];
  const senses = card.senses || [];
  const examples = card.examples || [];
  const right = (card.right_use && card.right_use.sentence) || "";
  const wrong = card.wrong_use || {};
  const synonyms = card.synonyms || [];
  const antonyms = card.antonyms || [];

  const checkBtn = ctx.canCheck && !ctx.readOnly
    ? h("button", { class: "btn btn-primary", type: "button", "data-enter": "", onclick: () => ctx.onCheck() }, "I've got it — check me")
    : null;
  const backBtn = ctx.readOnly ? null : h("button", {
    class: checkBtn ? "btn btn-ghost" : "btn btn-secondary", type: "button",
    "data-enter": checkBtn ? null : "", onclick: () => ctx.onBack(),
  }, "← back to session");

  mount(root,
    bannerEl(ctx.banner),
    h("div", { class: "card learn-card" },
      picture(word, data),
      ...wordHeader(word, card, ctx.stars ?? null),
      card.short_def ? h("div", { class: "learn-def" }, card.short_def) : null,
      card.kid_def ? h("p", { class: "kid-def" }, card.kid_def) : null,
      senses.length > 1
        ? section("Meanings", senses.map((s, i) => h("div", { class: "sense" },
            h("div", { class: "learn-pos" }, `${i + 1}. ${s.pos}`),
            h("div", null, s.definition),
            s.example ? h("div", { class: "ex" }, h("span", { class: "ex-text" }, highlighted(s.example, word, forms))) : null)))
        : null,
      card.memory_hook ? h("div", { class: "hook" }, h("b", null, "🧠 Memory hook: "), card.memory_hook) : null,
      card.word_parts ? h("div", { class: "parts" }, h("b", null, "🔍 Word parts: "), card.word_parts) : null,
      examples.length
        ? section("Examples", examples.map((ex) => h("div", { class: "ex" },
            h("span", { class: "ex-text" }, highlighted(ex, word, forms)),
            speakButton(ex, "Read this sentence"))))
        : null,
      right || wrong.sentence
        ? section("Using it",
            right ? h("div", { class: "use-right" }, h("b", null, "✅ Right: "), highlighted(right, word, forms)) : null,
            wrong.sentence
              ? h("div", { class: "use-wrong" }, h("b", null, "❌ Wrong: "), highlighted(wrong.sentence, word, forms),
                  wrong.why ? h("span", { class: "sub" }, "Why: ", wrong.why) : null)
              : null)
        : null,
      synonyms.length ? section("Similar words", h("div", { class: "synchips" }, synonyms.map((s) => h("span", { class: "syn" }, s)))) : null,
      antonyms.length ? section("Opposites", h("div", { class: "synchips" }, antonyms.map((s) => h("span", { class: "syn ant" }, s)))) : null),
    checkBtn,
    backBtn,
  );
  const first = checkBtn || backBtn;
  if (first) first.focus({ preventScroll: true });
}
