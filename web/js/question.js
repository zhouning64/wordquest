// Renders one question (choices or a text box) and reports the learner's answer.
// Shared by the session screen and the lock-in check. Feedback is drawn by the caller.
import { h, mount } from "./ui.js";
import { gradeTyped, shuffleChoices, speechText } from "./engine.js";
import { speakButton } from "./speech.js";

export const TYPE_LABEL = {
  meaning: "pick the meaning",
  pick_word: "pick the word",
  fill_blank: "fill in the blank",
  usage: "use it right",
  scenario: "which fits?",
  synonym: "closest meaning",
  antonym: "opposite",
  spell_it: "type the word",
  word_parts: "word parts",
};

// Prompt text with each "___" drawn as a blank. Text nodes only — never innerHTML.
export function promptNodes(prompt) {
  const out = [];
  String(prompt || "").split(/_{2,}/).forEach((part, i) => {
    if (i > 0) out.push(h("span", { class: "blank", "aria-label": "blank" }, "______"));
    if (part) out.push(part);
  });
  return out;
}

// opts: {label, counter, allowUnsure = true, onAnswered(result, feedbackEl)}
// result: {correct, unsure, near, given, answerText, ms}
export function mountQuestion(container, q, { label, counter = "", allowUnsure = true, onAnswered } = {}) {
  const startedAt = Date.now();
  let done = false;
  let unsureBtn = null;
  let disableAll = () => {};
  const feedback = h("div", { class: "fb-region", "aria-live": "polite" });

  const finish = (result) => {
    if (done) return;
    done = true;
    disableAll();
    if (unsureBtn) unsureBtn.disabled = true;
    result.ms = Math.max(0, Date.now() - startedAt);
    onAnswered(result, feedback);
  };

  const card = h("div", { class: "card qcard" },
    h("div", { class: "qhead" },
      h("span", { class: "qtype" }, label || TYPE_LABEL[q.type] || q.type),
      counter ? h("span", { class: "muted" }, counter) : null,
      speakButton(speechText(q), "Read the question")),
    h("p", { class: "qprompt" }, promptNodes(q.prompt)));

  let unsure = () => {};
  if (q.type === "spell_it") {
    const answerText = (q.accepted_answers && q.accepted_answers[0]) || "";
    const input = h("input", {
      type: "text", autocomplete: "off", autocapitalize: "off", autocorrect: "off", spellcheck: "false",
      placeholder: "type the word…", "aria-label": "Your answer",
    });
    const go = h("button", { type: "submit", "aria-label": "Check my answer" }, "✓");
    disableAll = () => { input.disabled = true; go.disabled = true; };
    card.append(h("form", {
      class: "typebox",
      onsubmit: (e) => {
        e.preventDefault();
        if (done || !input.value.trim()) return;
        const grade = gradeTyped(input.value, q.accepted_answers);
        finish({ correct: grade === "correct", unsure: false, near: grade === "near", given: input.value, answerText });
      },
    }, input, go));
    unsure = () => finish({ correct: false, unsure: true, near: false, given: null, answerText });
    setTimeout(() => input.focus(), 0);
  } else {
    const { choices, answerIndex } = shuffleChoices(q);
    const buttons = choices.map((text, i) => h("button", {
      class: "btn btn-opt", type: "button", "data-key": String(i + 1), onclick: () => choose(i),
    }, h("span", { class: "optkey", "aria-hidden": "true" }, String(i + 1)), text));
    const mark = (picked) => buttons.forEach((b, j) => {
      if (j === answerIndex) b.classList.add("correct");
      else if (j === picked) b.classList.add("wrong");
    });
    disableAll = () => buttons.forEach((b) => { b.disabled = true; });
    const choose = (i) => {
      if (done) return;
      mark(i);
      finish({ correct: i === answerIndex, unsure: false, near: false, given: i, answerText: choices[answerIndex] });
    };
    unsure = () => {
      if (done) return;
      mark(-1);
      finish({ correct: false, unsure: true, near: false, given: null, answerText: choices[answerIndex] });
    };
    card.append(...buttons);
  }

  if (allowUnsure) {
    unsureBtn = h("button", { class: "btn btn-ghost", type: "button", onclick: () => unsure() }, "🤔 I'm not sure");
    card.append(unsureBtn);
  }
  mount(container, card, feedback);
  return { card, feedback };
}
