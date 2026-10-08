// #/parent/preview/<band>/<word> — the learner's Learn page (rendered by screens/learn.js in read-only mode)
// plus the full question pool with the answers highlighted.
import { api } from "../api.js";
import { esc, toast } from "../ui.js";
import { render as renderLearn } from "../screens/learn.js";
import {
  BAND_LABEL,
  QTYPES,
  QTYPE_LABEL,
  REGEN_PARTS,
  TIER,
  asRows,
  badge,
  blankHtml,
  imageBadge,
  imageErrorText,
} from "./helpers.js";
import { errorBox, frame, go, reload, stillOn, toastError } from "./shell.js";

export async function render(root, ctx) {
  const [band, word] = ctx.params;
  const body = frame(root, ctx, "lists", word ? `Preview: ${word}` : "Preview");
  if (!band || !word) {
    body.innerHTML = `<p class="perr">No word chosen. Open a word list and press Preview on a word.</p>`;
    return;
  }
  body.innerHTML = `<p class="muted">Loading…</p>`;
  let res;
  try {
    res = await api.get(`/api/parent/content/${encodeURIComponent(band)}/${encodeURIComponent(word)}`);
  } catch (e) {
    errorBox(body, e);
    return;
  }
  if (!stillOn(ctx)) return;
  const content = res.content || res;
  const pool = sortPool(asRows(res.pool));
  const b = badge({ ...content, regenerating: content.draft_version != null });
  const ib = imageBadge(content);
  const imageError = imageErrorText({ image_status: content.image_status, image_error: res.image_error });
  const version = content.content_version || 1;

  body.innerHTML = `
    <button type="button" class="plink" id="pv-back">← Back</button>
    <div class="card">
      <div class="prow" style="border:0;padding:0">
        <div class="grow">
          <b style="font-size:22px">${esc(content.word || word)}</b>
          <span class="pbadge is-none">${esc(BAND_LABEL[band] || band)}</span>
          <span class="pbadge ${b.cls}">${esc(b.label)}</span><span class="pbadge ${ib.cls}">${esc(ib.label)}</span>
          <div class="muted">Version ${esc(version)} · ${content.source === "legacy" ? "starter set" : "AI"}${content.model ? ` · ${esc(content.model)}` : ""}${content.generated_at ? ` · ${esc(content.generated_at)}` : ""}</div>
          ${imageError ? `<span class="pimgerr">Picture failed: ${esc(imageError)}</span>` : ""}
        </div>
        <select class="pregen" id="pv-regen" aria-label="Regenerate this word">
          <option value="">↻ Regenerate…</option>
          ${REGEN_PARTS.map((p) => `<option value="${esc(p.part)}">${esc(p.label)}</option>`).join("")}
        </select>
      </div>
      ${content.draft_version ? `<div class="pbanner" style="margin:10px 0 0">A new version (v${esc(content.draft_version)}) is being prepared. Learners keep using v${esc(version)} until it is ready.</div>` : ""}
      ${content.error ? `<p class="perr">Last error: ${esc(content.error)}</p>` : ""}
    </div>
    <h3>Learn page — what the learner sees</h3>
    <div class="preview-learn"></div>
    <h3>Question pool — ${pool.length} questions (version ${esc(version)})</h3>
    <div class="card">${pool.length ? pool.map((q, i) => questionHtml(q, i)).join("") : `<p class="muted" style="margin:0">No questions yet.</p>`}</div>`;

  body.querySelector("#pv-back").addEventListener("click", () => {
    if (history.length > 1) history.back();
    else go(ctx, "#/parent/lists");
  });
  body.querySelector("#pv-regen").addEventListener("change", async (ev) => {
    const part = ev.target.value;
    ev.target.value = "";
    if (!part) return;
    const label = (REGEN_PARTS.find((p) => p.part === part) || { label: part }).label;
    if (!window.confirm(`Regenerate “${word}” — ${label}? Learners keep the current version until the new one is ready.`)) return;
    try {
      await api.post(`/api/parent/content/${encodeURIComponent(band)}/${encodeURIComponent(word)}/regenerate`, { part });
      toast(`Regenerating ${word}… watch progress on the list page.`);
      reload(ctx);
    } catch (e) {
      toastError(e);
    }
  });

  const learnEl = body.querySelector(".preview-learn");
  if (!content.card) {
    learnEl.innerHTML = `<div class="card"><p class="muted" style="margin:0">${
      content.status === "failed"
        ? "No Learn card — generation failed. See the error above and use Retry on the list page."
        : "No Learn card yet — it is still being prepared."
    }</p></div>`;
    return;
  }
  const entry = {
    card: content.card,
    image_url: res.image_url || null,
    source: content.source || "ai",
    stage: 0,
    last_graded_on: null,
    reserves: { reasks: [], checks: [] },
  };
  try {
    // Task 21's Learn renderer: ctx {word, data, banner, stars, canCheck, onCheck, onBack, readOnly}. readOnly: true
    // draws no session buttons; the `.preview-learn > button` CSS rule would hide any that remained.
    await renderLearn(learnEl, {
      readOnly: true,
      word: content.word || word,
      data: entry,
      banner: null,
      stars: null,
      canCheck: false,
      onCheck: () => {},
      onBack: () => {},
    });
  } catch (e) {
    learnEl.innerHTML = fallbackLearnHtml(content.word || word, content.card);
  }
}

function sortPool(pool) {
  const order = (t) => {
    const i = QTYPES.indexOf(t);
    return i < 0 ? 99 : i;
  };
  return pool.slice().sort((a, b) => order(a.type) - order(b.type) || String(a.created_at || "").localeCompare(String(b.created_at || "")));
}

function questionHtml(q, i) {
  const tier = q.tier ?? TIER[q.type] ?? "?";
  const answers =
    q.type === "spell_it"
      ? `<p style="margin:6px 0 0"><b>Accepted answers:</b> ${(q.accepted_answers || []).map((a) => esc(a)).join(", ")}</p>`
      : `<ol type="A">${(q.choices || [])
          .map((c, ci) => (ci === q.answer_index ? `<li class="correct">${esc(c)} ✓</li>` : `<li>${esc(c)}</li>`))
          .join("")}</ol>`;
  return `
    <div class="pq">
      <span class="qtype">${esc(QTYPE_LABEL[q.type] || q.type)}</span>
      <span class="meta">tier ${esc(tier)} · ${q.verified ? "✓ verified" : "not verified"} · ${q.source === "legacy" ? "starter set" : "AI"}</span>
      <p style="margin:8px 0 0;font-weight:600">${i + 1}. ${blankHtml(esc(q.prompt))}</p>
      ${answers}
      ${q.explanation ? `<p class="muted" style="margin:6px 0 0"><b>Why:</b> ${esc(q.explanation)}</p>` : ""}
    </div>`;
}

// Used only if screens/learn.js throws while rendering, so the parent still sees the card text.
function fallbackLearnHtml(word, card) {
  return `
    <div class="card">
      <p class="perr">The Learn page renderer failed; showing the card text instead.</p>
      <h2 class="learn-word">${esc(word)}</h2>
      <div class="learn-pos">${esc(card.pos)}</div>
      <div class="learn-def">${esc(card.short_def)}</div>
      <p>${esc(card.kid_def)}</p>
      ${(card.examples || []).map((s) => `<div class="ex">${esc(s)}</div>`).join("")}
    </div>`;
}
