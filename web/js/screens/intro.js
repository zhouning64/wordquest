// Intro card for a new word (spec §8.3 step 1): picture, word + 🔊, part of speech, short_def, one example.
import { h, mount } from "../ui.js";
import { highlighted, picture, wordHeader } from "./learn.js";

// ctx: {word, data (payload.words[word]), onDone()}
export function render(root, ctx) {
  const { word, data } = ctx;
  const card = (data && data.card) || {};
  const example = (card.examples && card.examples[0]) || (card.senses && card.senses[0] && card.senses[0].example) || "";
  const btn = h("button", { class: "btn btn-primary", type: "button", "data-enter": "", onclick: () => ctx.onDone() }, "Got it →");
  mount(root,
    h("div", { class: "card intro-card" },
      h("span", { class: "qtype" }, "✨ new word"),
      picture(word, data),
      ...wordHeader(word, card),
      card.short_def ? h("div", { class: "learn-def" }, card.short_def) : null,
      example ? h("div", { class: "ex" }, h("span", { class: "ex-text" }, highlighted(example, word, card.forms || []))) : null),
    btn,
  );
  btn.focus({ preventScroll: true });
}
