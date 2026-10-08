from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
PARENT_DIR = ROOT / "web" / "js" / "parent"
SCREENS = ["login", "profiles", "lists", "preview", "stats", "backup"]
SUPPORT = ["helpers", "shell"]


@pytest.fixture()
def client(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    app = create_app(settings, start_worker=False, seed=False)
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("name", SCREENS + SUPPORT)
def test_parent_module_is_served_as_javascript(client, name):
    r = client.get(f"/static/js/parent/{name}.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]


@pytest.mark.parametrize("name", SCREENS)
def test_each_parent_screen_exports_render(name):
    src = (PARENT_DIR / f"{name}.js").read_text(encoding="utf-8")
    assert re.search(r"^export async function render\(root, ctx\)", src, re.MULTILINE)


def test_app_router_loads_every_parent_screen():
    src = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"PARENT_SCREENS\s*=\s*\[([^\]]*)\]", src)
    assert m, "web/js/app.js must define PARENT_SCREENS"
    assert re.findall(r'"([a-z]+)"', m.group(1)) == SCREENS
    assert re.search(r"import\(`\./parent/\$\{\w+\}\.js`\)", src)


def test_preview_reuses_the_learner_learn_renderer_read_only():
    src = (PARENT_DIR / "preview.js").read_text(encoding="utf-8")
    assert 'import { render as renderLearn } from "../screens/learn.js";' in src
    assert "readOnly: true" in src
    assert "canCheck: false" in src


def test_parent_styles_are_appended_to_app_css():
    css = (ROOT / "web" / "css" / "app.css").read_text(encoding="utf-8")
    for selector in (".pwrap", ".ptabs", ".pbadge.is-ready", ".pbanner.warn", ".pq li.correct", ".pbtn-danger",
                     ".preview-learn > button", ".pimgerr"):
        assert selector in css


# ======================================================================================================================
# Fail-closed escaping guard for the Parent area (web/js/parent/*.js)
#
# The Parent area renders through innerHTML with template literals, and most values come from the server (profile and
# list names, AI-written words/cards/questions, error texts). Every `${...}` inside a template literal must therefore
# be one of:
#   1. provably safe by syntax (`is_safe_output` below): a single `esc(...)` call, a single `encodeURIComponent(...)`
#      call, a string/template literal, `blankHtml(esc(...))`, a call of one of the HTML_HELPERS (whose own
#      interpolations are covered by this very test), or a `?:` / `||` / `+` / `.map(x => <safe>).join("<literal>")`
#      combination of those; or
#   2. listed in ALLOWED_INTERPOLATIONS with the number of times it occurs in that file and the reason it is safe.
# Anything else fails the test, so a new unescaped interpolation (even a copy of an allowlisted expression, because the
# counts must match exactly) cannot slip in without a reviewed allowlist change. Scanning problems (unterminated
# strings or templates) also fail the test instead of being guessed around.
# ======================================================================================================================


class ScanError(ValueError):
    """The JavaScript could not be scanned reliably; the guard fails rather than guessing."""


def _line_of(src, i):
    return src.count("\n", 0, i) + 1


def _skip_string(src, i):
    quote = src[i]
    j = i + 1
    while j < len(src) and src[j] != "\n":
        if src[j] == "\\":
            j += 2
        elif src[j] == quote:
            return j + 1
        else:
            j += 1
    raise ScanError(f"unterminated string literal at line {_line_of(src, i)}")


def _skip_comment(src, i):
    if src.startswith("//", i):
        j = src.find("\n", i)
        return len(src) if j < 0 else j
    j = src.find("*/", i + 2)
    if j < 0:
        raise ScanError(f"unterminated block comment at line {_line_of(src, i)}")
    return j + 2


def _skip_template(src, i, found=None, depth=0):
    """src[i] is a backtick. Returns the index after the closing backtick; every `${expr}` met on the way (nested
    templates included) is appended to `found` as (line, expr, depth, text_before)."""
    j = i + 1
    while j < len(src):
        c = src[j]
        if c == "\\":
            j += 2
        elif c == "`":
            return j + 1
        elif c == "$" and src.startswith("${", j):
            end = _skip_expression(src, j + 2, found, depth + 1)
            if found is not None:
                found.append((_line_of(src, j), src[j + 2:end], depth, src[max(0, j - 300):j]))
            j = end + 1
        else:
            j += 1
    raise ScanError(f"unterminated template literal at line {_line_of(src, i)}")


def _skip_expression(src, i, found, depth):
    """Scans JS code up to the `}` that closes a `${`; returns the index of that brace."""
    level = 0
    while i < len(src):
        c = src[i]
        if c in "'\"":
            i = _skip_string(src, i)
        elif c == "`":
            i = _skip_template(src, i, found, depth)
        elif c == "/" and src[i + 1:i + 2] in ("/", "*"):
            i = _skip_comment(src, i)
        else:
            if c == "{":
                level += 1
            elif c == "}":
                if level == 0:
                    return i
                level -= 1
            i += 1
    raise ScanError("unterminated ${ expression")


def interpolations(src):
    """Every `${...}` inside any template literal of `src`: sorted (line, expr, depth, text_before) tuples."""
    found = []
    i = 0
    while i < len(src):
        c = src[i]
        if c in "'\"":
            i = _skip_string(src, i)
        elif c == "`":
            i = _skip_template(src, i, found)
        elif c == "/" and src[i + 1:i + 2] in ("/", "*"):
            i = _skip_comment(src, i)
        else:
            i += 1
    return sorted(found)


def _top_level(expr):
    """(index, char) of every character of `expr` outside strings/templates and at bracket depth 0."""
    i = 0
    depth = 0
    while i < len(expr):
        c = expr[i]
        if c in "'\"":
            i = _skip_string(expr, i)
            continue
        if c == "`":
            i = _skip_template(expr, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0:
            yield i, c
        i += 1


def _matching(expr, i):
    """Index of the bracket closing the one at expr[i] (strings/templates skipped), or -1."""
    depth = 0
    while i < len(expr):
        c = expr[i]
        if c in "'\"":
            i = _skip_string(expr, i)
            continue
        if c == "`":
            i = _skip_template(expr, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _single_literal(e):
    if not e or e[0] not in "'\"`":
        return False
    return (_skip_template(e, 0) if e[0] == "`" else _skip_string(e, 0)) == len(e)


def _single_call(e, name):
    return e.startswith(name + "(") and _matching(e, len(name)) == len(e) - 1


def _split_ternary(e):
    q = None
    nested = 0
    for i, c in _top_level(e):
        if c == "?":
            if e[i + 1:i + 2] == "?" or e[i - 1:i] == "?":
                continue  # `??`
            if e[i + 1:i + 2] == "." and not e[i + 2:i + 3].isdigit():
                continue  # `?.`
            if q is None:
                q = i
            else:
                nested += 1
        elif c == ":" and q is not None:
            if nested == 0:
                return e[q + 1:i].strip(), e[i + 1:].strip()
            nested -= 1
    return None


def _split_binary(e, op):
    """Splits at top-level `||` or `+` (not `++`, `+=`); None when `op` does not occur."""
    pos = [i for i, c in _top_level(e) if c == op[0]]
    if op == "||":
        cuts = [a for a, b in zip(pos, pos[1:]) if b == a + 1]
        width = 2
    else:
        cuts = [i for i in pos if e[i - 1:i] not in ("+",) and e[i + 1:i + 2] not in ("+", "=")]
        width = 1
    if not cuts:
        return None
    parts = []
    last = 0
    for c in cuts:
        parts.append(e[last:c].strip())
        last = c + width
    parts.append(e[last:].strip())
    return parts


def _map_join_body(e):
    """For `<receiver>.map(<arrow>).join(<string literal>)` returns the arrow's expression body, else None."""
    head = None
    for i, _ in _top_level(e):
        if e.startswith(".join(", i) and _matching(e, i + 5) == len(e) - 1 and _single_literal(e[i + 6:-1].strip()):
            head = e[:i].rstrip()
            break
    if head is None:
        return None
    for i, _ in reversed(list(_top_level(head))):
        if head.startswith(".map(", i) and _matching(head, i + 4) == len(head) - 1:
            args = head[i + 5:-1]
            for k, _ in _top_level(args):
                if args.startswith("=>", k):
                    body = args[k + 2:].strip()
                    return None if body.startswith("{") else body
            return None
    return None


# Functions that return HTML built only from templates in web/js/parent (every one of their own interpolations is
# checked by this test). Reviewed: rejectedHtml, regenSelectHtml, wordRowHtml (lists.js), questionHtml and
# fallbackLearnHtml (preview.js), dangerHtml (profiles.js), stat (stats.js).
HTML_HELPERS = ("rejectedHtml", "regenSelectHtml", "wordRowHtml", "questionHtml", "fallbackLearnHtml", "dangerHtml", "stat")


def is_safe_output(expr):
    """True when the value of `expr` can only be an escaped string, an encodeURIComponent() result, a literal (a
    template's own interpolations are checked on their own) or the HTML of a reviewed helper."""
    e = expr.strip()
    if not e:
        return False
    if _single_literal(e) or _single_call(e, "esc") or _single_call(e, "encodeURIComponent"):
        return True
    if any(_single_call(e, h) for h in HTML_HELPERS):
        return True
    if _single_call(e, "blankHtml"):  # blankHtml(escaped) only wraps the blank marker; its argument must be escaped
        return _single_call(e[len("blankHtml("):-1].strip(), "esc")
    if e[0] == "(" and _matching(e, 0) == len(e) - 1:
        return is_safe_output(e[1:-1])
    for op in ("||", "+"):
        parts = _split_binary(e, op)
        if parts:
            return all(is_safe_output(p) for p in parts)
    tern = _split_ternary(e)
    if tern:  # the condition is never output, only the two branches are
        return is_safe_output(tern[0]) and is_safe_output(tern[1])
    body = _map_join_body(e)
    return body is not None and is_safe_output(body)


def skeleton(expr):
    """Whitespace-collapsed expression with string literals shown as "" and template literals as `` (their own
    interpolations are checked separately); this is the key used by ALLOWED_INTERPOLATIONS."""
    out = []
    i = 0
    while i < len(expr):
        c = expr[i]
        if c in "'\"":
            i = _skip_string(expr, i)
            out.append('""')
        elif c == "`":
            i = _skip_template(expr, i)
            out.append("``")
        else:
            out.append(c)
            i += 1
    return " ".join("".join(out).split())


# Places where esc() alone is not enough, so even an escaped interpolation is rejected there.
UNSAFE_CONTEXTS = (
    (re.compile(r"='$"), "single-quoted attribute value (only double-quoted attributes are reviewed; encodeURIComponent() keeps `'`)"),
    (re.compile(r"\bstyle\s*=\s*[\"'][^\"']*$"), "inside a style attribute"),
    (re.compile(r"\bon[a-z]+\s*=\s*[\"']?[^\"'>]*$"), "inside an event-handler attribute"),
    (re.compile(r"<(?:script|style)\b[^>]*>[^<]*$", re.I), "inside a <script> or <style> element"),
)
UNQUOTED_ATTRIBUTE = re.compile(r"=\s*$")  # <a href=${esc(x)}: esc() does not escape spaces, so this is injectable
URL_ATTRIBUTE = re.compile(r"\b(?:href|src|action|formaction|xlink:href)\s*=\s*[\"']$", re.I)
URL_ATTRIBUTE_VALUES = ("esc(listHref(", "esc(previewHref(")  # fixed "#/parent/..." prefix, so no `javascript:` URLs


def context_problems(expr, before):
    e = expr.strip()
    problems = []
    for pattern, what in UNSAFE_CONTEXTS:
        if pattern.search(before):
            problems.append(what)
    if UNQUOTED_ATTRIBUTE.search(before) and not _single_call(e, "encodeURIComponent"):
        problems.append("unquoted attribute value (or text right after `=`)")
    if URL_ATTRIBUTE.search(before) and not e.startswith(URL_ATTRIBUTE_VALUES):
        problems.append("URL attribute value that is not listHref()/previewHref() (esc() does not stop `javascript:`)")
    return problems


def guard_problems(src, allowed):
    """Problems of one JS source: unreviewed interpolations, unsafe contexts and stale allowlist entries.
    `allowed` maps skeleton -> (expected_count, reason)."""
    problems = []
    unreviewed = {}
    for line, expr, _depth, before in interpolations(src):
        for what in context_problems(expr, before):
            problems.append(f"line {line}: ${{{skeleton(expr)}}} is {what}")
        if not is_safe_output(expr):
            unreviewed.setdefault(skeleton(expr), []).append(line)
    for sk, lines in unreviewed.items():
        want = allowed.get(sk, (0, ""))[0]
        if len(lines) != want:
            problems.append(
                f"line(s) {lines}: ${{{sk}}} is not escaped with esc(...) and is allowlisted {want}x, found {len(lines)}x "
                "(escape it, or review it and update ALLOWED_INTERPOLATIONS)"
            )
    for sk, (want, _reason) in allowed.items():
        if sk not in unreviewed:
            problems.append(f"allowlist entry ${{{sk}}} (x{want}) no longer occurs: remove it from ALLOWED_INTERPOLATIONS")
    return problems


def sink_problems(src, allowed):
    """Every `.innerHTML = <rhs>;` must assign a safe_output expression or an allowlisted constant; other HTML sinks
    (insertAdjacentHTML, outerHTML, document.write, ...) are not used at all."""
    problems = []
    for m in re.finditer(r"insertAdjacentHTML|outerHTML|document\.write|createContextualFragment|srcdoc|\beval\(|new Function", src):
        problems.append(f"line {_line_of(src, m.start())}: {m.group(0)} is not allowed in the Parent area")
    assignments = list(re.finditer(r"\.innerHTML\s*=(?!=)", src))
    if len(assignments) != len(re.findall(r"innerHTML", src)):
        problems.append("innerHTML is used other than as a plain `x.innerHTML = <expr>;` assignment (+=, [\"innerHTML\"], reads...)")
    seen = {}
    for m in assignments:
        i = m.end()
        depth = 0
        while i < len(src):
            c = src[i]
            if c in "'\"":
                i = _skip_string(src, i)
                continue
            if c == "`":
                i = _skip_template(src, i)
                continue
            if c == "/" and src[i + 1:i + 2] in ("/", "*"):
                i = _skip_comment(src, i)
                continue
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c == ";" and depth == 0:
                break
            i += 1
        else:
            raise ScanError(f"innerHTML assignment at line {_line_of(src, m.start())} has no terminating ';'")
        rhs = src[m.end():i].strip()
        if not is_safe_output(rhs):
            seen.setdefault(skeleton(rhs), []).append(_line_of(src, m.start()))
    for sk, lines in seen.items():
        want = allowed.get(sk, (0, ""))[0]
        if len(lines) != want:
            problems.append(f"line(s) {lines}: innerHTML = {sk} is not a reviewed HTML value (allowlisted {want}x, found {len(lines)}x)")
    for sk in allowed:
        if sk not in seen:
            problems.append(f"sink allowlist entry {sk} no longer occurs: remove it from ALLOWED_SINKS")
    return problems


# ---- reviewed allowlist ---------------------------------------------------------------------------------------------
# Each entry: skeleton of the interpolation -> (times it occurs in that file, why it is safe). Reviewed expression by
# expression against the code; the reasons below are categories, the comment above each group names the specifics.
NUMBER = "a number (array length, index, or Number()/Math result), so it can only be digits"
CLASS_NAME = "CSS class from badge()/imageBadge() in helpers.js, which only return the literals is-ready/is-busy/is-failed/is-none"
CONSTANT = "a fixed constant defined in the Parent area (not server data)"
TEXT_SINK = "only reaches a plain-text sink (toast() sets textContent; window.confirm() dialog; textContent assignment), never innerHTML"
ESCAPED_LATER = "plain text returned by a helper whose only HTML use is wrapped in esc() (see the comment on the entry)"
LOCAL_HTML = "a local value built in the same function from templates whose interpolations are checked by this test"

ALLOWED_INTERPOLATIONS = {
    "backup.js": {
        # Error message of a failed upload: the HTTP status number. errText() shows it via textContent.
        "res.status": (1, NUMBER),
    },
    "helpers.js": {
        # listHref(): `base` is "#/parent/lists/" + encodeURIComponent(listId); callers put it in href through esc().
        "base": (1, "fixed '#/parent/lists/' prefix plus an encodeURIComponent() result"),
        # readinessText(): `${ready} / ${total} ready` from readiness(), both integer counts.
        "ready": (1, NUMBER),
        "total": (1, NUMBER),
        # pct(): Math.round() of a number.
        "Math.round(n <= 1 ? n * 100 : n)": (1, NUMBER),
        # queueInfo(): num() = Number(v || 0) for all of calls, limit, pending, running; text is set via textContent.
        "calls": (2, NUMBER),
        "limit": (2, NUMBER),
        "pending + running": (1, NUMBER),
        "pending": (1, NUMBER),
        "running": (1, NUMBER),
        # imageProviderText(): plain text from /api/parent/status; its only HTML use is esc(imageProviderText(st)).
        "p": (1, ESCAPED_LATER),
        "status.image_model": (1, ESCAPED_LATER),
        # checkBackupFile(): shown through errEl.textContent in backup.js only (the version comes from an uploaded file).
        "String(data.version)": (1, TEXT_SINK),
        # countsText(): `${v} ${k...}` with v filtered by typeof number; k is a server key. backup.js only uses it as
        # esc(summary).
        "v": (1, ESCAPED_LATER),
        'k.replace(/_/g, "")': (1, ESCAPED_LATER),
    },
    "lists.js": {
        # n = array length (wordCount, rejectedHtml, the live entry counter).
        "n": (3, NUMBER),
        # wordCount() returns "<n> word(s)" built from a length and two fixed words.
        "wordCount(list.words)": (1, NUMBER),
        "MAX_LIST_WORDS": (2, CONSTANT),
        # wordRowHtml(): classes come from badge()/imageBadge(); poolSize() is Number(...).
        "b.cls": (1, CLASS_NAME),
        "ib.cls": (1, CLASS_NAME),
        "poolSize(row)": (1, NUMBER),
        # drawRows(): both go to textContent.
        "list.words.length": (1, NUMBER),
        "BAND_LABEL[band]": (1, CONSTANT),
        "readinessText(list.words, rows)": (1, NUMBER),
        # confirm() dialogs and toast() messages: word and list.name are text, label comes from REGEN_PARTS.
        "list.name": (1, TEXT_SINK),
        "word": (5, TEXT_SINK),
        "label": (1, CONSTANT),
    },
    "preview.js": {
        # title passed to frame(), which renders it as esc(title); confirm() dialog and toast() text.
        "word": (3, TEXT_SINK + "; the title goes through frame(), which renders esc(title)"),
        "b.cls": (1, CLASS_NAME),
        "ib.cls": (1, CLASS_NAME),
        "pool.length": (1, NUMBER),
        "label": (1, CONSTANT),
        # questionHtml(): question number, and `answers` = the spell_it/choices templates defined just above it, whose
        # interpolations are all esc(...).
        "i + 1": (1, NUMBER),
        "answers": (1, LOCAL_HTML),
    },
    "profiles.js": {
        # `cards` = data.profiles.map(... template ...).join("") built just above; all its interpolations use esc().
        "cards || ``": (1, LOCAL_HTML),
        # field(n): n is always a literal field name and only builds a CSS selector, not HTML.
        "n": (1, "a literal field name used in a CSS selector string, not in HTML"),
        # renderListPicker(): counts and indexes, and the local count() helper that returns a <span> holding a number.
        "(l.words || []).length": (1, NUMBER),
        "count(l)": (2, "local helper returning '<span class=\"muted\">(N word(s))</span>' with N a length"),
        "i + 1": (1, NUMBER),
        "i": (2, NUMBER),
        # wireDelete(): toast text.
        "p.name": (1, TEXT_SINK),
    },
    "shell.js": {
        # frame(): key comes from the constant TABS table (profiles/lists/stats/backup).
        "key": (1, CONSTANT),
    },
}

# innerHTML assignments that are not a safe_output expression.
ALLOWED_SINKS = {
    # login.js: DISABLED_HTML is a constant template literal without any interpolation.
    "login.js": {"DISABLED_HTML": (2, "constant template literal without interpolations")},
    # profiles.js renderListPicker(): `assigned.map((lid, i) => { const l = ...; return <template>; }).join("")` plus
    # `others.map((l) => <template>).join("")`. The first arrow has a block body (so the generic .map rule does not
    # apply), but its only statement that produces output is `return <template>`; the interpolations of both
    # templates are esc(...) or reviewed above. Any change to this expression changes the skeleton and fails the test.
    "profiles.js": {
        'assigned .map((lid, i) => { const l = byId.get(lid); return ``; }) .join("") + others .map((l) => ``) .join("")': (
            1,
            "map-join of two templates (one with a block-bodied arrow that returns a template)",
        ),
    },
}


def _parent_js_files():
    return sorted(PARENT_DIR.glob("*.js"))


def test_allowlists_only_name_existing_parent_files():
    names = {p.name for p in _parent_js_files()}
    assert set(ALLOWED_INTERPOLATIONS) <= names
    assert set(ALLOWED_SINKS) <= names
    assert set(SCREENS + SUPPORT) <= {n[:-3] for n in names}


@pytest.mark.parametrize("path", _parent_js_files(), ids=lambda p: p.name)
def test_every_parent_interpolation_is_escaped_or_reviewed(path):
    src = path.read_text(encoding="utf-8")
    allowed = ALLOWED_INTERPOLATIONS.get(path.name, {})
    assert guard_problems(src, allowed) == []
    assert len(interpolations(src)) > 0  # the scanner must actually have seen template literals in every file


@pytest.mark.parametrize("path", _parent_js_files(), ids=lambda p: p.name)
def test_parent_html_sinks_are_reviewed(path):
    src = path.read_text(encoding="utf-8")
    assert sink_problems(src, ALLOWED_SINKS.get(path.name, {})) == []


# ---- the guard itself must fail closed (self-tests on small JS snippets) ---------------------------------------------

def _problems(js, allowed=None):
    return guard_problems(js, allowed or {})


def test_guard_rejects_unescaped_interpolations():
    js = r"""const h = `<b>${p.name}</b> ${q.card.short_def} ${esc(x)}`;"""
    problems = _problems(js)
    assert len(problems) == 2
    assert "p.name" in problems[0] and "q.card.short_def" in problems[1]


@pytest.mark.parametrize("expr", ["esc(a) + b", "esc(a).x", "esc(a), b", "esc (a)", "xesc(a)", "esc(a) || b", "esc(a) ? b : c"])
def test_guard_does_not_trust_a_partial_esc_call(expr):
    assert len(_problems("h = `${" + expr + "}`;")) == 1


@pytest.mark.parametrize(
    "expr",
    [
        "esc(a)",
        "esc(a.b || '')",
        "encodeURIComponent(x)",
        "cond ? `<b>${esc(x)}</b>` : ''",
        "a ? esc(b) : esc(c)",
        "esc(a) + esc(b)",
        "esc(a) || `<i>none</i>`",
        "blankHtml(esc(q.prompt))",
        "stat(1, 'x')",
        "rows.map((r) => `<li>${esc(r)}</li>`).join('')",
        "(q.choices || [])\n .map((c, i) => (i ? `<li>${esc(c)}</li>` : `<li class='x'>${esc(c)}</li>`))\n .join('')",
        "rows.length ? rows.map((r) => esc(r)).join(', ') : 'none'",
        "a?.b ? `<p>${esc(c)}</p>` : ''",
    ],
)
def test_guard_accepts_the_structurally_safe_forms(expr):
    assert _problems("h = `${" + expr + "}`;") == []


@pytest.mark.parametrize(
    "expr",
    [
        "x ? esc(a) : b",
        "x ? `<i>` : b.name",
        "rows.map((r) => r.name).join('')",
        "rows.map((r) => `<li>${esc(r)}</li>`).join(sep)",
        "rows.map((r) => { return r; }).join('')",
        "esc(a) + b.name",
        "blankHtml(q.prompt)",
        "blankHtml(esc(q.prompt) + extra)",
        "unknownHelper(x)",
        "a ?? b",
        "a.b",
        "`<i>${esc(a)}</i>` + raw",
    ],
)
def test_guard_flags_raw_branches_and_unknown_helpers(expr):
    assert _problems("h = `${" + expr + "}`;") != []


def test_guard_sees_unescaped_interpolations_nested_inside_safe_looking_wrappers():
    js = "h = `${rows.map((r) => `<li>${r.name}</li>`).join('')} ${a ? `<b>${raw}</b>` : ''}`;"
    problems = _problems(js)
    assert any("r.name" in p for p in problems)
    assert any("${raw}" in p for p in problems)


def test_guard_allowlist_counts_must_match_exactly():
    js = "a = `${n} ${n}`;"
    assert _problems(js, {"n": (2, "x")}) == []
    assert len(_problems(js, {"n": (1, "x")})) == 1  # a copy of a reviewed expression needs a new review
    assert len(_problems(js, {})) == 1
    assert len(_problems("a = `${esc(n)}`;", {"n": (1, "x")})) == 1  # stale entry


@pytest.mark.parametrize(
    "js",
    [
        "h = `<a href=${esc(x)}>`;",
        "h = `<a href=\"${esc(p.url)}\">`;",
        "h = `<a href=\"${esc(label)}\">`;",
        "h = `<b onclick=\"${esc(x)}\">`;",
        "h = `<i style=\"width:${esc(w)}px\">`;",
        "h = `<script>${esc(x)}</script>`;",
        "h = `<b title='${esc(x)}'>`;",
    ],
)
def test_guard_rejects_escaped_values_in_contexts_where_esc_is_not_enough(js):
    assert any("is " in p for p in _problems(js))


def test_guard_accepts_the_known_safe_attribute_forms():
    js = (
        "h = `<a href=\"${esc(listHref(id))}\">${esc(a)}</a> <a href=\"${esc(previewHref(b, w))}\">p</a> "
        "<input value=\"${esc(v)}\" class=\"c${x ? ' on' : ''}\"> <a href=\"#/x/${esc(encodeURIComponent(i))}\">`;\n"
        "u = `/api/x?list_id=${encodeURIComponent(a)}&band=${encodeURIComponent(b)}`;"
    )
    assert _problems(js) == []


def test_scanner_skips_strings_and_comments_and_follows_nested_templates():
    js = (
        "// a comment with a backtick ` and an apostrophe isn't a problem\n"
        "/* ${notReal} */ const s = \"a ` quote\" + 'it`s';\n"
        "const t = `x ${a ? `y ${b} ${'}'}` : `${c}`} z`;\n"
    )
    found = [expr for _line, expr, _depth, _before in interpolations(js)]
    assert sorted(found) == sorted(["b", "'}'", "c", "a ? `y ${b} ${'}'}` : `${c}`"])


@pytest.mark.parametrize("js", ["x = `abc ${a", "x = `abc", "x = 'abc\nrest'", "/* open"])
def test_scanner_errors_instead_of_guessing(js):
    with pytest.raises(ScanError):
        interpolations(js)


def test_sink_guard_checks_every_innerhtml_assignment():
    ok = "a.innerHTML = '';\nb.innerHTML = `<p>${esc(x)}</p>`;\nc.innerHTML = rows.map((r) => `<i>${esc(r)}</i>`).join('') || `<p>none</p>`;"
    assert sink_problems(ok, {}) == []
    assert len(sink_problems("a.innerHTML = value;", {})) == 1
    assert len(sink_problems("a.innerHTML = '<b>' + name + '</b>';", {})) == 1
    assert len(sink_problems("a.innerHTML = value;", {"value": (1, "x")})) == 0
    assert len(sink_problems("a.innerHTML += `<p>${esc(x)}</p>`;", {})) == 1  # compound assignment is not recognised
    assert len(sink_problems("a['innerHTML'] = x;", {})) == 1
    assert sink_problems("a.insertAdjacentHTML('beforeend', x);", {}) != []
    assert sink_problems("a.outerHTML = `<p></p>`;", {}) != []
    with pytest.raises(ScanError):
        sink_problems("a.innerHTML = value", {})


def test_list_page_poll_keeps_the_word_rows_and_their_focus():
    # The queue poll must not rebuild the rows (open "Regenerate…" menus, focused Retry/remove/Preview controls) when
    # nothing changed or while the parent is using one of them (decision logic: redrawAction in helpers.js).
    src = (PARENT_DIR / "lists.js").read_text(encoding="utf-8")
    assert "redrawAction(drawn, next, { force, busy })" in src
    assert "wordsEl.contains(document.activeElement)" in src
    assert src.count("wordsEl.innerHTML") == 1  # the rows are rebuilt in exactly one place: drawRows
