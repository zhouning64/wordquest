// Small SVG bar charts for the parent Progress page (spec §11). No chart library: the app also runs offline on the
// home network. Pure functions that return markup, so Node tests can check the geometry.
import { esc } from "../ui.js";

const PAD = { top: 10, right: 6, bottom: 24, left: 34 };
const MIN_LABEL_GAP = 60;  // px between x-axis labels
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// One hue, darker as stars go up; 0 stars is grey (seen, not learned yet).
export const STAR_COLORS = ["#cbd5e1", "#a5b4fc", "#818cf8", "#6366f1", "#4338ca", "#312e81"];
const NEW_COLOR = "#94a3b8";
const GAINED_COLOR = "#4f46e5";
const LOST_COLOR = "#ef4444";
const DUE_COLOR = "#6366f1";

const round = (n) => Math.round(n * 100) / 100;
const num = (v) => Number(v || 0);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

export function dayLabel(date) {
  const [, m, d] = String(date).split("-").map(Number);
  return `${MONTHS[m - 1]} ${d}`;
}

// Whole-number gridline step (1, 2, 5 × 10^k) giving about 4 gridlines.
function gridStep(span) {
  const raw = span / 4;
  if (raw <= 1) return 1;
  const pow = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw);
}

function valueRange(series, n, stacked) {
  let hi = 0;
  let lo = 0;
  for (let i = 0; i < n; i++) {
    if (stacked) {
      let up = 0;
      let down = 0;
      for (const s of series) {
        const v = num(s.values[i]);
        if (v > 0) up += v;
        else down += v;
      }
      hi = Math.max(hi, up);
      lo = Math.min(lo, down);
    } else {
      for (const s of series) {
        hi = Math.max(hi, num(s.values[i]));
        lo = Math.min(lo, num(s.values[i]));
      }
    }
  }
  const step = gridStep(hi);
  // The side below zero (stars lost) is usually small: round it on its own scale, not to a whole gridline step.
  const below = lo < 0 ? gridStep(-lo) : 1;
  return { top: Math.ceil(hi / step) * step || step, bottom: -Math.ceil(-lo / below) * below, step };
}

// labels: one per column; series: [{name, color, values}] (negative values hang below the baseline).
// Stacked charts pile the series in each column, first series at the bottom; otherwise they stand side by side.
export function barChart({ labels, series, stacked = false, ariaLabel, width = 600, height = 180 }) {
  const n = labels.length;
  const plotW = width - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom;
  const { top, bottom, step } = valueRange(series, n, stacked);
  const y = (v) => PAD.top + ((top - v) / (top - bottom)) * plotH;
  const colW = plotW / n;
  const out = [];

  for (let v = Math.ceil(bottom / step) * step; v <= top; v += step) {
    out.push(`<line class="${v === 0 ? "base" : "grid"}" x1="${esc(PAD.left)}" x2="${esc(width - PAD.right)}" y1="${esc(round(y(v)))}" y2="${esc(round(y(v)))}"/>`);
    out.push(`<text class="yl" x="${esc(PAD.left - 6)}" y="${esc(round(y(v) + 4))}" text-anchor="end">${esc(v)}</text>`);
  }

  const bar = (i, s, x, w, from, to) => {
    const [y1, y2] = [y(Math.max(from, to)), y(Math.min(from, to))];
    out.push(`<rect class="bar" data-i="${esc(i)}" data-s="${esc(s)}" x="${esc(round(x))}" y="${esc(round(y1))}" width="${esc(round(w))}" height="${esc(round(y2 - y1))}" fill="${esc(series[s].color)}"/>`);
  };
  for (let i = 0; i < n; i++) {
    const left = PAD.left + i * colW;
    if (stacked) {
      const w = Math.min(colW * 0.7, 28);
      let up = 0;
      let down = 0;
      series.forEach((ser, s) => {
        const v = num(ser.values[i]);
        if (v > 0) bar(i, s, left + (colW - w) / 2, w, up, (up += v));
        else if (v < 0) bar(i, s, left + (colW - w) / 2, w, down, (down += v));
      });
    } else {
      const group = Math.min(colW * 0.8, series.length * 24);
      const slot = group / series.length;
      series.forEach((ser, s) => {
        const v = num(ser.values[i]);
        if (v !== 0) bar(i, s, left + (colW - group) / 2 + s * slot + 1, Math.max(1, slot - 2), 0, v);
      });
    }
  }

  const every = Math.ceil(n / Math.max(1, Math.floor(plotW / MIN_LABEL_GAP)));
  for (let i = (n - 1) % every; i < n; i += every) {  // counted back from the last day, so today is always labelled
    out.push(`<text class="xl" x="${esc(round(PAD.left + (i + 0.5) * colW))}" y="${esc(height - 6)}" text-anchor="middle">${esc(labels[i])}</text>`);
  }
  for (let i = 0; i < n; i++) {
    out.push(`<rect class="hit" data-i="${esc(i)}" x="${esc(round(PAD.left + i * colW))}" y="${esc(PAD.top)}" width="${esc(round(colW))}" height="${esc(round(plotH))}" fill="transparent"/>`);
  }
  return `<svg class="pchart" viewBox="0 0 ${esc(width)} ${esc(height)}" width="${esc(width)}" height="${esc(height)}" role="img" aria-label="${esc(ariaLabel)}">${out.join("")}</svg>`;
}

// GET /api/parent/profiles/{id}/stats `trends` (app/learning/trends.py): {days: [{date, stars: [6 counts], new,
// gained, lost}], upcoming: [{date, due}]} → the three charts' labels, series and per-column detail lines.
export function trendViews(trends) {
  const days = trends && Array.isArray(trends.days) ? trends.days : [];
  if (!days.length) return null;
  const upcoming = Array.isArray(trends.upcoming) ? trends.upcoming : [];
  const labels = days.map((d) => dayLabel(d.date));
  const starRows = days.map((d) => STAR_COLORS.map((_, k) => num(d.stars && d.stars[k])));
  const dueLabels = upcoming.map((u, i) => (i === 0 ? "Today" : dayLabel(u.date)));
  return {
    stars: {
      labels,
      series: STAR_COLORS.map((color, k) => ({ name: `${k}★`, color, values: starRows.map((r) => r[k]) })),
      details: starRows.map((r, i) => {
        const total = r.reduce((a, b) => a + b, 0);
        return `${labels[i]} — ${plural(total, "word", "words")}: ${r.map((c, k) => `${k}★ ${c}`).join(", ")}`;
      }),
    },
    activity: {
      labels,
      series: [
        { name: "New words", color: NEW_COLOR, values: days.map((d) => num(d.new)) },
        { name: "Stars gained", color: GAINED_COLOR, values: days.map((d) => num(d.gained)) },
        { name: "Stars lost", color: LOST_COLOR, values: days.map((d) => (num(d.lost) ? -num(d.lost) : 0)) },
      ],
      details: days.map((d, i) => `${labels[i]} — ${plural(num(d.new), "new word", "new words")}, `
        + `${plural(num(d.gained), "star gained", "stars gained")}, ${plural(num(d.lost), "star lost", "stars lost")}`),
    },
    upcoming: {
      labels: dueLabels,
      series: [{ name: "Reviews due", color: DUE_COLOR, values: upcoming.map((u) => num(u.due)) }],
      details: upcoming.map((u, i) => `${dueLabels[i]} — ${plural(num(u.due), "review", "reviews")} due`
        + (i === 0 ? " (including overdue)" : "")),
    },
  };
}

// The chart's numbers as a table (for screen readers and exact values); negative bars are shown as counts.
export function numbersTable(view) {
  const head = `<tr><th scope="col">day</th>${view.series.map((s) => `<th scope="col">${esc(s.name)}</th>`).join("")}</tr>`;
  const rows = view.labels.map((label, i) =>
    `<tr><td>${esc(label)}</td>${view.series.map((s) => `<td>${esc(Math.abs(num(s.values[i])))}</td>`).join("")}</tr>`);
  return `<div class="ptable-wrap"><table class="ptable"><thead>${head}</thead><tbody>${rows.join("")}</tbody></table></div>`;
}
