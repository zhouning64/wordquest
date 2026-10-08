// #/parent/stats[/<profileId>] — progress dashboard for one learner (spec §11).
import { api } from "../api.js";
import { esc, localDate } from "../ui.js";
import { QTYPE_LABEL, asRows, pct, sessionRow, starText, statsCounts, typeRows, weakRow } from "./helpers.js";
import { errorBox, frame, stillOn } from "./shell.js";

function stat(value, label) {
  return `<div class="stat"><div class="n">${esc(value)}</div><div class="l">${esc(label)}</div></div>`;
}

export async function render(root, ctx) {
  const body = frame(root, ctx, "stats", "Progress");
  body.innerHTML = `<p class="muted">Loading…</p>`;
  let profiles;
  try {
    profiles = asRows(await api.get("/api/parent/profiles"), "profiles");
  } catch (e) {
    errorBox(body, e);
    return;
  }
  if (!stillOn(ctx)) return;
  if (!profiles.length) {
    body.innerHTML = `<div class="card"><p class="muted" style="margin:0">No profiles yet. Add one under “Profiles”.</p></div>`;
    return;
  }
  const pid = profiles.some((p) => p.id === ctx.params[0]) ? ctx.params[0] : profiles[0].id;
  body.innerHTML = `
    <nav class="ptabs" aria-label="Choose a learner">
      ${profiles.map((p) => `<a class="ptab${p.id === pid ? " active" : ""}" href="#/parent/stats/${esc(encodeURIComponent(p.id))}">${esc(p.avatar)} ${esc(p.name)}</a>`).join("")}
    </nav>
    <div id="sbody"><p class="muted">Loading…</p></div>`;
  const out = body.querySelector("#sbody");
  let s;
  try {
    s = await api.get(`/api/parent/profiles/${encodeURIComponent(pid)}/stats?local_date=${encodeURIComponent(localDate())}`);
  } catch (e) {
    errorBox(out, e);
    return;
  }
  if (!stillOn(ctx)) return;

  const c = statsCounts(s);
  const types = typeRows(s.accuracy && s.accuracy.by_type);
  const weak = asRows(s.weakest).map(weakRow).slice(0, 12);
  const sessions = asRows(s.recent_sessions).map(sessionRow).slice(0, 10);

  out.innerHTML = `
    <div class="stat-grid">
      ${stat(c.mastered, "mastered (5 stars)")}
      ${stat(c.learning, "learning")}
      ${stat(c.new, "new (not started)")}
      ${stat(c.due_today, "due today")}
      ${stat(pct(c.accuracy), "accuracy (last 30 days)")}
      ${stat(c.answered, "answers (last 30 days)")}
      ${stat(pct(c.unsure_rate), "“not sure” rate (last 30 days)")}
      ${stat(c.days_practiced_30, "days practiced (last 30)")}
    </div>
    <div class="card">
      <h3 style="margin:0 0 8px">Accuracy by question type</h3>
      ${
        types.length
          ? `<div class="ptable-wrap"><table class="ptable">
              <thead><tr><th scope="col">type</th><th scope="col">answered</th><th scope="col">correct</th><th scope="col">accuracy</th></tr></thead>
              <tbody>${types.map((r) => `<tr><td>${esc(QTYPE_LABEL[r.type] || r.type)}</td><td>${esc(r.answered)}</td><td>${esc(r.correct)}</td><td>${esc(pct(r.accuracy))}</td></tr>`).join("")}</tbody>
            </table></div>`
          : `<p class="muted" style="margin:0">No answers yet.</p>`
      }
    </div>
    <div class="card">
      <h3 style="margin:0 0 8px">Weakest words</h3>
      ${
        weak.length
          ? weak.map((w) => `<div class="pweak"><b>${esc(w.word)}</b><span><span class="stars" aria-label="${esc(w.stage)} of 5 stars">${esc(starText(w.stage))}</span> <span class="muted">${esc(w.misses)} missed</span></span></div>`).join("")
          : `<p class="muted" style="margin:0">No shaky words yet — nice work!</p>`
      }
    </div>
    <div class="card">
      <h3 style="margin:0 0 8px">Last 10 sessions</h3>
      ${
        sessions.length
          ? `<div class="ptable-wrap"><table class="ptable">
              <thead><tr><th scope="col">date</th><th scope="col">mode</th><th scope="col">minutes</th><th scope="col">questions</th><th scope="col">accuracy</th></tr></thead>
              <tbody>${sessions.map((r) => `<tr><td>${esc(r.date)}</td><td>${esc(r.mode)}</td><td>${esc(r.minutes)}</td><td>${esc(r.answered)}</td><td>${esc(pct(r.accuracy))}</td></tr>`).join("")}</tbody>
            </table></div>`
          : `<p class="muted" style="margin:0">No sessions yet.</p>`
      }
    </div>`;
}
