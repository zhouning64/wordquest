import { test } from "node:test";
import assert from "node:assert/strict";
import { barChart, dayLabel, numbersTable, trendViews } from "../../web/js/parent/charts.js";

// Every <rect class="NAME" ...> in the SVG, with its numeric attributes.
function rects(svg, cls) {
  return [...svg.matchAll(new RegExp(`<rect class="${cls}"([^>]*)>`, "g"))].map((m) => {
    const attr = (name) => {
      const a = m[1].match(new RegExp(`\\s${name}="([^"]*)"`));
      return a ? a[1] : undefined;
    };
    return { i: Number(attr("data-i")), s: Number(attr("data-s")), x: Number(attr("x")), y: Number(attr("y")),
      w: Number(attr("width")), h: Number(attr("height")), fill: attr("fill") };
  });
}
const close = (a, b) => Math.abs(a - b) < 0.05;
const series = (...values) => values.map((v, k) => ({ name: `s${k}`, color: `#00000${k}`, values: v }));

test("dayLabel turns an ISO date into a short month and day", () => {
  assert.equal(dayLabel("2026-10-08"), "Oct 8");
  assert.equal(dayLabel("2026-01-31"), "Jan 31");
});

test("stacked segments are proportional and sit on top of each other, first series at the bottom", () => {
  const svg = barChart({ labels: ["Oct 8"], series: series([1], [3]), stacked: true, ariaLabel: "x" });
  const [bottom, top] = rects(svg, "bar");
  assert.equal(bottom.fill, "#000000");
  assert.ok(close(top.h, 3 * bottom.h), `heights ${bottom.h} and ${top.h}`);
  assert.ok(close(top.y + top.h, bottom.y), "the second segment starts where the first one ends");
});

test("zero values draw no bar", () => {
  const svg = barChart({ labels: ["a", "b"], series: series([0, 2], [4, 0]), stacked: true, ariaLabel: "x" });
  assert.deepEqual(rects(svg, "bar").map((r) => `${r.i}:${r.s}`).sort(), ["0:1", "1:0"]);
});

test("negative values hang below the baseline, to scale", () => {
  const svg = barChart({ labels: ["a"], series: series([5], [-2]), ariaLabel: "x" });
  const [up, down] = rects(svg, "bar");
  assert.ok(close(up.y + up.h, down.y), "both bars meet at the baseline");
  assert.ok(close(down.h, (2 / 5) * up.h), `heights ${up.h} and ${down.h}`);
});

test("grouped bars sit side by side inside their column", () => {
  const svg = barChart({ labels: ["a"], series: series([1], [2], [3]), ariaLabel: "x" });
  const bars = rects(svg, "bar").sort((p, q) => p.x - q.x);
  assert.equal(bars.length, 3);
  for (let k = 1; k < bars.length; k++) assert.ok(bars[k].x >= bars[k - 1].x + bars[k - 1].w, "no overlap");
  const [hit] = rects(svg, "hit");
  assert.ok(bars[0].x >= hit.x && bars[2].x + bars[2].w <= hit.x + hit.w, "inside the column");
});

test("each column has one hover target, and the targets tile the plot without gaps", () => {
  const svg = barChart({ labels: ["a", "b", "c"], series: series([1, 0, 2]), ariaLabel: "x" });
  const hits = rects(svg, "hit");
  assert.deepEqual(hits.map((r) => r.i), [0, 1, 2]);
  assert.ok(close(hits[0].x + hits[0].w, hits[1].x) && close(hits[1].x + hits[1].w, hits[2].x));
});

test("a long run of days thins the axis labels but always labels the last day", () => {
  const labels = Array.from({ length: 30 }, (_, k) => `d${k}`);
  const svg = barChart({ labels, series: series(labels.map(() => 1)), ariaLabel: "x" });
  const shown = [...svg.matchAll(/<text class="xl"[^>]*>([^<]*)<\/text>/g)].map((m) => m[1]);
  assert.ok(shown.length >= 4 && shown.length <= 8, `${shown.length} labels`);
  assert.equal(shown.at(-1), "d29");
});

test("all-zero data still draws a clean chart", () => {
  const svg = barChart({ labels: ["a", "b"], series: series([0, 0]), ariaLabel: "x" });
  assert.ok(!svg.includes("NaN") && !svg.includes("Infinity"));
  assert.equal(rects(svg, "bar").length, 0);
});

test("the chart is labelled for screen readers, safely escaped", () => {
  const svg = barChart({ labels: ["a"], series: series([1]), ariaLabel: 'Stars "today" <b>' });
  assert.match(svg, /role="img"/);
  assert.match(svg, /aria-label="Stars &quot;today&quot; &lt;b&gt;"/);
});

// ---------- trendViews: the stats payload shaped for the three charts ----------

const TRENDS = {
  days: [
    { date: "2026-10-10", stars: [0, 5, 22, 45, 0, 0], new: 1, gained: 1, lost: 1 },
    { date: "2026-10-11", stars: [2, 7, 24, 58, 8, 0], new: 27, gained: 82, lost: 5 },
  ],
  upcoming: [{ date: "2026-10-11", due: 3 }, { date: "2026-10-12", due: 1 }, { date: "2026-10-13", due: 0 }],
};

test("star view has one series per star level, 0 stars first", () => {
  const v = trendViews(TRENDS).stars;
  assert.deepEqual(v.labels, ["Oct 10", "Oct 11"]);
  assert.deepEqual(v.series.map((s) => s.values), [[0, 2], [5, 7], [22, 24], [45, 58], [0, 8], [0, 0]]);
  assert.equal(v.details[1], "Oct 11 — 99 words: 0★ 2, 1★ 7, 2★ 24, 3★ 58, 4★ 8, 5★ 0");
});

test("activity view draws stars lost below the line and words the totals", () => {
  const v = trendViews(TRENDS).activity;
  assert.deepEqual(v.series.map((s) => s.values), [[1, 27], [1, 82], [-1, -5]]);
  assert.equal(v.details[0], "Oct 10 — 1 new word, 1 star gained, 1 star lost");
  assert.equal(v.details[1], "Oct 11 — 27 new words, 82 stars gained, 5 stars lost");
});

test("upcoming view calls the first day Today and says it includes overdue words", () => {
  const v = trendViews(TRENDS).upcoming;
  assert.deepEqual(v.labels, ["Today", "Oct 12", "Oct 13"]);
  assert.deepEqual(v.series[0].values, [3, 1, 0]);
  assert.equal(v.details[0], "Today — 3 reviews due (including overdue)");
  assert.equal(v.details[1], "Oct 12 — 1 review due");
});

test("no history means no trend views", () => {
  assert.equal(trendViews({ days: [], upcoming: TRENDS.upcoming }), null);
  assert.equal(trendViews(undefined), null);
});

test("the numbers table has a row per day and shows stars lost as a plain count", () => {
  const html = numbersTable(trendViews(TRENDS).activity);
  const rows = [...html.matchAll(/<tr>(.*?)<\/tr>/g)].map((m) => [...m[1].matchAll(/<t[hd][^>]*>([^<]*)<\/t[hd]>/g)].map((c) => c[1]));
  assert.deepEqual(rows, [
    ["day", "New words", "Stars gained", "Stars lost"],
    ["Oct 10", "1", "1", "1"],
    ["Oct 11", "27", "82", "5"],
  ]);
});

test("a small negative value gets a small space below the baseline, not a whole gridline step", () => {
  const svg = barChart({ labels: ["a"], series: series([100], [-5]), ariaLabel: "x" });
  const [up] = rects(svg, "bar");
  const [hit] = rects(svg, "hit");
  const baseline = up.y + up.h;
  assert.ok((hit.y + hit.h - baseline) / hit.h < 0.1, `below the baseline: ${hit.y + hit.h - baseline} of ${hit.h}`);
});
