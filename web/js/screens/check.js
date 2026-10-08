// Lock-in check (spec §8.4): the 3 questions of one reserve set, one at a time. Never changes stars.
import { h, mount } from "../ui.js";
import { mountQuestion, TYPE_LABEL } from "../question.js";

// ctx: {word, questions, checkSet (1|2), onAnswer(q, correct, ms), onDone(correctCount, missed)}
// missed: [{prompt, answer, explanation}] for the Learn page's "what you missed" banner.
export function render(root, ctx) {
  const qs = ctx.questions || [];
  let i = 0;
  let correctCount = 0;
  const missed = [];

  function show() {
    const q = qs[i];
    const slot = h("div");
    mount(root, h("div", { class: "check-view" },
      h("div", { class: "check-head" },
        h("b", null, `🔒 Lock it in: ${ctx.word}`),
        h("span", { class: "muted" }, ctx.checkSet === 2 ? "second try" : "")),
      slot));
    mountQuestion(slot, q, {
      label: TYPE_LABEL[q.type] || q.type,
      counter: `${i + 1} of ${qs.length}`,
      allowUnsure: false,
      onAnswered: (r, fb) => {
        if (r.correct) correctCount += 1;
        else missed.push({ prompt: q.prompt, answer: r.answerText, explanation: q.explanation });
        if (ctx.onAnswer) ctx.onAnswer(q, r.correct, r.ms);
        const last = i === qs.length - 1;
        const btn = h("button", {
          class: "btn btn-primary", type: "button", "data-enter": "",
          onclick: () => {
            if (last) ctx.onDone(correctCount, missed);
            else { i += 1; show(); }
          },
        }, last ? "See result" : "Next →");
        mount(fb,
          r.correct
            ? h("div", { class: "feedback good" }, "✓ Correct!")
            : h("div", { class: "feedback bad" }, "✗ The answer is ", h("b", null, r.answerText),
                q.explanation ? h("span", { class: "sub" }, q.explanation) : null),
          btn);
        btn.focus({ preventScroll: true });
      },
    });
  }
  show();
}
