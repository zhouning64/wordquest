# WordQuest AI — Design Spec (Phase 1: local)

- **Date:** 2026-10-07
- **Status:** Draft — awaiting review (revised after multi-lens review)
- **Scope of this spec:** Phase 1 (runs locally on a Mac). Phase 2 (Google Cloud Run) is outlined in §15 only so Phase 1 keeps the right seams.

---

## 1. Goal and success criteria

WordQuest is a Membean-style vocabulary trainer for any learner. A parent pastes a list of words; an AI (Cerebras) generates kid-appropriate learning content and quiz questions for each word, pitched to the learner's grade band. Learners answer varied questions; a wrong answer (or "I'm not sure") leads to a **Learn** page with a picture, meanings, memory hook, and example sentences, followed by a short **"lock it in"** check before returning to the session. Words come back on a spaced-repetition schedule across days.

Phase 1 is done when all of the following are true:

1. Runs locally with: `pip install -r requirements.txt`, copy `.env.example` → `.env`, `uvicorn app.main:app`. For phones/iPads on the home network: `uvicorn app.main:app --host 0.0.0.0` with `SITE_ACCESS_CODE` set (README documents both).
2. A parent can paste 20 words, assign the list to a profile, and see every word reach **ready** status without touching any file (given a valid Cerebras key).
3. A learner can complete a 15-minute session with no visible waiting on AI generation.
4. Wrong answer → Learn → lock-in check (2 of 3) → return to the same point in the session works end to end.
5. Spaced-repetition rules in §8.5 pass their unit tests.
6. No API key or passcode value ever appears in any HTTP response, stored error field, export, or log file (asserted by the test in §12).
7. All persistence goes through the storage interface in §6.3 (only `app/storage/` imports `sqlite3`; asserted by a test).

## 2. Decisions record

| Topic | Decision |
|---|---|
| Content generation | Live AI via Cerebras (`qwen-3.8-27b` at medium reasoning by default; configurable). Validated by code, answer-key-checked by a second blind AI pass, cached per word × grade band. |
| Question freshness | A cached **question pool** per word × band (~12 to start), topped up in the background as learners exhaust it. |
| Hosting | Phase 1 local (FastAPI + SQLite + local image folder). Phase 2 Google Cloud Run + Firestore + Cloud Storage via the same storage interface. |
| Images | AI illustrations through a pluggable `ImageProvider`; emoji-scene fallback. **Provider: z.ai** (decided 2026-10-07) via the OpenAI-style `images/generations` adapter; the model (`glm-image` vs `cogview-4-250304`) is chosen at an implementation checkpoint (§16). |
| Learners | Not tied to one child. "Who's learning?" profile picker, no passwords. Whole site behind one shared access code (when set). |
| Progress | Stored on the server per profile. |
| Word lists | Managed in a passcode-protected Parent area; content pre-generated in the background on save. |
| Difficulty | Per-profile grade band: `3-5`, `6-8`, `9-12`. Content is generated and cached per band. |
| Features | Spaced review across days; "I'm not sure" button; pronunciation audio (browser speech); per-profile session timer and break reminder. |
| Stack | Python 3.10+ / FastAPI / SQLite; plain HTML/CSS/ES-module JS frontend, no build step. Phase 1 runs as a single uvicorn process. |

## 3. Scope

**In Phase 1:** everything in §4–§14.

**Out of Phase 1:** Cloud Run deployment, Firestore/Cloud Storage backends, Dockerfile, migration CLI (all Phase 2); real user accounts; offline mode / service worker; leaderboards; non-English content; paid TTS audio.

## 4. Architecture

```
wordquest/
├─ app/
│  ├─ main.py               FastAPI app: lifespan (starts job worker), static mounts, routers, auth deps
│  ├─ config.py             Settings loaded from environment / .env (§5)
│  ├─ auth.py               access-code + parent cookies, attempt limiting
│  ├─ security.py           redact() for secrets in error text/logs; constant-time compare
│  ├─ api/
│  │  ├─ learner.py         profiles list, home, sessions, events, results
│  │  └─ parent.py          profiles/lists CRUD, content status/preview/regenerate, stats, export/import
│  ├─ ai/
│  │  ├─ llm.py             Cerebras client (httpx, OpenAI-compatible, strict json_schema), transport retries
│  │  ├─ schemas.py         JSON schemas sent to the model + pydantic models for parsed results
│  │  ├─ prompts.py         prompt templates: learn card, question batch, answer-key check
│  │  ├─ validate.py        code-level content checks (§7.5)
│  │  ├─ inflect.py         word-form matching (§7.7)
│  │  ├─ content.py         orchestrates generate → validate → check → store for a word × band
│  │  ├─ blocklist.txt      kid-safety word list
│  │  └─ images/
│  │     ├─ base.py         ImageProvider interface
│  │     ├─ none.py         no-op provider (emoji fallback only)
│  │     └─ <provider>.py   one real provider, chosen at checkpoint (§16)
│  ├─ learning/
│  │  ├─ srs.py             spaced-repetition rules (pure functions)
│  │  ├─ session.py         session builder + question selection
│  │  └─ grading.py         typed-answer normalization, near-miss detection
│  ├─ storage/
│  │  ├─ base.py            Repository + BlobStore interfaces (§6.3)
│  │  ├─ sqlite_repo.py     SQLite implementation
│  │  └─ local_blobs.py     files under DATA_DIR/images
│  ├─ jobs.py               background generation worker (asyncio)
│  └─ seed_legacy.py        imports the legacy 24-word starter set (§13)
├─ web/
│  ├─ index.html
│  ├─ manifest.webmanifest  + icons (Add to Home Screen)
│  ├─ css/app.css           visual style carried over from legacy index.html
│  └─ js/
│     ├─ api.js             fetch wrapper; ordered, buffered, retried event upload
│     ├─ app.js             hash router, screen switching
│     ├─ ui.js              esc(), toast(), stars(), shuffle(), shared rendering helpers
│     ├─ srs.js             display-only mirror of the §8.5 stage rule
│     ├─ speech.js          speechSynthesis wrapper
│     ├─ screens/           gate, profiles, home, session, intro, learn, check, results
│     └─ parent/            login, profiles, lists, word-preview, stats, backup
├─ legacy/index.html        the original static app (reference + seed source)
├─ scripts/smoke_ai.py      manual live test: generate 2 words end to end
├─ tests/
│  ├─ srs_vectors.json      shared test vectors for srs.py and web/js/srs.js
│  └─ js/srs.test.mjs       runs the vectors against srs.js (Node, if installed)
├─ data/                    (git-ignored) wordquest.db, images/, logs/
├─ .env.example
├─ requirements.txt
└─ README.md
```

**Data flow.** The browser talks only to our server. The server calls Cerebras and the image provider; every AI output passes validation (§7.5) and the answer-key check (§7.3) before it is stored or shown. Each question is delivered with its answer key so feedback is instant; the browser then posts answer events and the server updates the schedule. All persistence goes through the storage interface.

**Dependencies:** `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic`, `pydantic-settings`, `itsdangerous`, `pillow`, `python-multipart`; dev: `pytest`, `pytest-asyncio`.

## 5. Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `CEREBRAS_API_KEY` | *(empty)* | Empty → AI disabled; app runs with seeded/previously generated content; Parent area shows "AI not configured". |
| `CEREBRAS_MODEL` | `qwen-3.8-27b` | Any Cerebras model that supports strict `json_schema` output (`gpt-oss-120b` with `LLM_REASONING_EFFORT` empty also works). |
| `CEREBRAS_BASE_URL` | `https://api.cerebras.ai/v1` | |
| `LLM_MAX_COMPLETION_TOKENS` | `40000` | Sent as `max_completion_tokens` on every call (reasoning models spend tokens before output). |
| `LLM_TIMEOUT_S` | `180` | Seconds per AI request. |
| `LLM_REASONING_EFFORT` | `medium` | `low` / `medium` / `high`, sent as `reasoning_effort` on the Learn-card and question-batch calls; empty = not sent (the model's default). Anything else is rejected at startup. The answer check always sends `high` (§7.3). |
| `IMAGE_PROVIDER` | `none` | `none` or `openai_compatible` (any service with an OpenAI-style `POST {base}/images/generations`, including z.ai). |
| `IMAGE_BASE_URL` | *(empty)* | For z.ai: `https://api.z.ai/api/paas/v4`. |
| `IMAGE_API_KEY`, `IMAGE_MODEL` | *(empty)* | z.ai key; model `glm-image` or `cogview-4-250304`. |
| `IMAGE_SIZE` | `1024x1024` | Valid for both z.ai models (glm-image: 1024–2048 px, multiples of 32; cogview-4: 512–2048, multiples of 16). |
| `IMAGE_QUALITY` | *(empty)* | Empty = provider default; z.ai accepts `standard` (≈5–10 s) or `hd` (≈20 s). |
| `SITE_ACCESS_CODE` | *(empty)* | Empty → no access gate (fine on `127.0.0.1`; the server logs a warning once, on the first request from a non-loopback client, while no code is set). |
| `PARENT_PASSCODE` | *(empty)* | Empty → Parent area disabled with an explanatory message. |
| `SECRET_KEY` | *(auto)* | Cookie signing key. If unset in Phase 1, generated once and saved to `DATA_DIR/secret_key`. |
| `DATA_DIR` | `./data` | Database, images, logs. |
| `STORAGE` | `local` | Phase 2 adds `gcp`. |
| `GEN_CONCURRENCY` | `3` | Parallel generation jobs. |
| `AI_DAILY_CALL_LIMIT` | `2000` | Cap on outbound LLM + image HTTP requests per UTC day; jobs are deferred (not failed) when hit. |

`.env` is git-ignored; `.env.example` documents every variable with placeholder values. The five secret values (`CEREBRAS_API_KEY`, `IMAGE_API_KEY`, `SITE_ACCESS_CODE`, `PARENT_PASSCODE`, `SECRET_KEY`) are stripped of surrounding whitespace when loaded, so a whitespace-only value means "not configured".

## 6. Data model

### 6.1 Entities

IDs are short random strings (`uuid4().hex[:12]`). Composite keys are formatted strings so they map 1:1 to Firestore document IDs in Phase 2. Timestamps are UTC ISO-8601; learner-day logic uses the **learner's local date** (`YYYY-MM-DD`) sent by the browser.

**Profile** — key `id`
`name` (1–30 chars), `avatar` (one emoji), `band` (`3-5` | `6-8` | `9-12`), `list_ids` (ordered), `settings`: `session_minutes` (5–30, default 15), `new_words_per_session` (0–30, default 5), `break_reminder` (bool, default true), `break_message` (default "Take a 10-minute break — look at something far away."), `created_at`.

**WordList** — key `id`
`name` (1–60 chars), `words` (ordered, normalized per §6.2, max 600), `created_at`, `updated_at`.

**WordContent** (the Learn card) — key `"{band}:{word}"`
- `word`, `band`, `status`: `pending` | `ready` | `failed`, `error` (last failure text, redacted), `source`: `ai` | `legacy`
- `content_version` (int, starts at 1), `draft` (nullable: a complete card being built by Regenerate, with its own `content_version`)
- `pos` — part(s) of speech, e.g. `"adjective"`, `"adjective / verb"`
- `forms` — inflected and irregular forms of the word (e.g. `strive` → `strives, strove, striven, striving`)
- `short_def` — ≤ 90 chars, kid-friendly
- `kid_def` — ≤ 220 chars, fuller explanation
- `senses` — 1–3 of `{pos, def, example}` (multiple meanings shown separately)
- `examples` — 4–6 sentences in varied band-appropriate settings, each using the word or a form
- `word_parts` — string; empty when not meaningful
- `memory_hook` — string (may be empty for legacy content)
- `synonyms`, `antonyms` — 0–5 each
- `right_use` — `{sentence}`; `wrong_use` — `{sentence, why}` (both empty for legacy content; the Learn section is hidden when empty)
- `image_scene` — scene description for the illustrator; `emoji_scene` — a short emoji string
- `image_key` (nullable BlobStore key; URLs always come from `BlobStore.url_for()`), `image_status`: `none` | `pending` | `ready` | `failed`
- `model`, `generated_at`

`WordContent.status` becomes `ready` when the Learn card is valid **and** the question pool for the current `content_version` meets the minimum in §7.3. The image is optional for readiness (emoji fallback).

**Question** — key `id`; indexed by `"{band}:{word}"` and `content_version`
`word`, `band`, `content_version`, `type` (§8.2), `tier` (1–3, derived from type), `prompt` (may contain one `___`), `choices` (exactly 4 for choice types; `[]` for `spell_it`), `answer_index` (0–3 for choice types; `-1` for `spell_it`), `accepted_answers` (normalized forms for `spell_it`; `[]` otherwise), `explanation` (≤ 160 chars), `source` (`ai` | `legacy`), `verified` (bool), `created_at`. Pool cap: 40 questions per word × band × version. "Choice types" = every type except `spell_it`.

**WordProgress** — key `"{profile_id}:{word}"`
`stage` (0–5; displayed as stars), `due_date` (local date or null), `interval_days`, `introduced_on`, `last_graded_on` (local date of the last stage-changing answer), `seen` / `correct` / `wrong` / `unsure` counts, `seen_question_ids` (most recent 60), `updated_at`.

**Session** — key `id`
`profile_id`, `mode` (`normal` | `practice`), `local_date`, `started_at`, `finished_at`, `planned_minutes`, `active_minutes`, `answered`, `correct`, `unsure`, `new_words` (list), `stars_up` (list of words), `learn_opened` (count), `checks_passed` / `checks_failed`.

**AnswerEvent** — key `client_event_id` (generated in browser; makes uploads idempotent)
`session_id`, `profile_id`, `word`, `question_id` (nullable), `question_type`, `kind` (`answer` | `unsure` | `check_answer` | `check_result` | `intro_seen` | `learn_open`), `correct` (nullable bool), `check_set` (1 | 2; `check_result` only), `correct_count` (0–3; `check_result` only), `passed` (bool; `check_result` only), `ms` (time to answer), `local_date`, `at` (client timestamp).

**Job** — key `"{kind}:{band}:{word}"` (one record per kind × band × word; uniqueness comes from the key)
`kind` (`learn` | `questions` | `image` | `topup`), `band`, `word`, `target_version` (the `content_version` this job builds for), `chain` (bool: enqueue later stages on success), `status` (`pending` | `running` | `done` | `failed`), `attempts`, `last_error` (redacted), `not_before`, `lease_until`, `created_at`, `updated_at`.

**AuthFailure** — key `"{scope}:{client_ip}:{window_start}"` (`scope` = `site` | `parent`) — `count`. Lives in the Repository (not process memory) so it works across instances in Phase 2.

### 6.2 Word normalization

Applied when a list is saved: trim; lowercase; replace curly quotes; collapse internal whitespace; strip characters other than letters, spaces, hyphens, apostrophes; drop empties; dedupe preserving first occurrence. **Rejected and reported back to the parent** (never silently dropped): entries longer than 40 chars, more than 3 tokens, or matching the blocklist (§7.5). Max 600 words per list.

### 6.3 Storage interface (`app/storage/base.py`)

Callers outside `app/storage/` use only these methods — no SQL, no cross-entity joins — so Phase 2 can implement them on Firestore. The SQLite implementation uses WAL mode, one connection per thread, and `busy_timeout = 5000`.

```
Repository
  profiles:   list_profiles() · get_profile(id) · save_profile(p)
              · delete_profile(id)            cascades: progress, sessions, events of that profile
  lists:      list_lists() · get_list(id) · save_list(l)
              · delete_list(id)               also removes the id from every profile's list_ids
  content:    get_content(band, word) · get_contents(band, words) · save_content(c)
              · list_content_by_status(status) · list_contents()   all contents, by band then word
              · save_draft(band, word, card, version)
              · swap_draft(band, word, version)   atomic: card := draft, content_version := version,
                                                  draft := null, delete questions of older versions
              · set_image(band, word, key, status, version)   partial update; no-op if version is stale
  questions:  get_pool(band, word, version=None)    None → the active content_version
              · get_pools(band, words)        {word: active-version pool}
              · add_questions(band, word, version, qs)
              · delete_questions(band, word, version) -> count   exactly that content_version
              · max_question_version(band, word) -> int          0 when no questions are stored
  progress:   get_progress(profile_id, words) · list_progress(profile_id)
  sessions:   save_session(s) · get_session(id) · list_sessions(profile_id, limit)
  events:     apply_events(session_id, events, apply_fn) -> accepted_ids
                 One transaction (SQLite: BEGIN IMMEDIATE; Firestore: transaction). Inserts only
                 events whose client_event_id is new, calls apply_fn(session, progress_by_word,
                 new_events) to update WordProgress and Session counters, and commits all writes
                 together. Returns the ids now stored (new + already-present), so the client can
                 drop exactly those from its buffer.
              · list_events(profile_id, since_date)
  jobs:       enqueue_job(kind, band, word, target_version, chain)
                 no-op if the record is pending/running for the same or newer target_version;
                 otherwise (re)sets it to pending with attempts = 0
              · claim_next_job(now, lease_s)  atomic: picks a pending job (or a running one whose
                 lease_until < now) with not_before ≤ now, learn/questions/image before topup,
                 sets running + lease_until
              · get_job(key)
              · finish_job(key) · fail_job(key, error, retry_at | None)  (consumes an attempt)
              · defer_job(key, not_before)    (does NOT consume an attempt)
              · wake_jobs(kinds) -> count     pending jobs of these kinds get not_before := "" (claimable
                 now; attempts untouched; running/done/failed jobs unchanged); each job is updated on its own
              · job_counts()
  usage:      incr_ai_calls(utc_date) -> count · get_ai_calls(utc_date) -> count
  auth:       incr_auth_failure(scope, ip, window_start) -> count · clear_auth_failures(scope, ip)
  backup:     export_all() -> dict · import_all(dict)   (replace mode; see below)

BlobStore
  put(key, data: bytes, content_type) · url_for(key) -> str · exists(key) · delete(key)
```

Local `BlobStore` keys look like `images/{band}/{word}-v{version}.webp`, are stored under `DATA_DIR`, and are served at `/media/{key}`.

**Backup semantics.** `export_all()` contains all Repository records except jobs and auth failures. Images are not in the export (they stay in `DATA_DIR/images/`; copy the folder to move machines). `import_all()` replaces all data, sets `image_status = none` for any content whose blob is missing, and re-enqueues jobs for content that is `pending` or `failed`. The job worker is paused during import.

## 7. AI content pipeline

### 7.1 Triggers

- **List saved or edited / profile assigned a list / profile band changed:** for each word in the affected lists and each band used by profiles assigned to that list: if no `WordContent` exists, create it as `pending` (version 1) and enqueue `learn` with `chain = true`. If it exists and is `failed`, resume from the stage that failed — a valid Learn card and any verified questions are kept.
- **Parent "Regenerate"** builds the replacement **alongside** the current content; the current version stays `ready` and in use until the new one passes validation and the check, then `swap_draft` switches both card and pool in one write. If regeneration fails, the current version is untouched and the error is shown.
  - `all`: `learn` (draft, version v+1) → `questions` (for v+1) → swap → `image` (for v+1).
  - `learn`: `learn` → `questions` → swap; the existing image is kept.
  - `questions`: draft = copy of current card at v+1 → `questions` → swap.
  - `image`: `image` only, for the current version.
- **Pool top-up (learner-driven, bounded):** when a session is built and a profile has seen ≥ 75% of a word's pool, enqueue `topup` — unless one already ran for that word × band today (UTC), one is pending, or the pool is at its 40-question cap.
- **Startup:** parked jobs are woken (§7.2); jobs left `running` by a crash become claimable again when their lease expires.

Only the Parent area can cause new words to be generated. Learner actions can only cause top-ups, bounded per word per day, by the pool cap, and by `AI_DAILY_CALL_LIMIT`; `claim_next_job` always prefers parent-initiated work over top-ups.

### 7.2 Job worker (`app/jobs.py`)

An asyncio task started in the FastAPI lifespan runs `GEN_CONCURRENCY` workers (single process in Phase 1). Each loop claims a job with a 900 s (15-minute) lease — longer than a worst-case questions job — and runs it. At startup the lifespan calls `wake_jobs` for `learn`/`questions`/`topup` when a text generator is configured and for `image` when an image provider is configured, so adding a key or raising `AI_DAILY_CALL_LIMIT` and restarting resumes parked jobs at once (without a key, AI jobs are re-deferred 5 minutes at a time).

- **Version guard:** each job carries `target_version`; if the content's active version or draft version no longer matches when the job finishes, its result is discarded.
- **Attempts:** a job gets at most **3 attempts** — the first try, then retries after 5 s and 30 s — except a pool shortfall (§7.3), which keeps its verified questions between attempts and so gets up to **5 attempts** (retries after 5 s, 30 s, 60 s and 120 s). Invalid output (§7.5), `finish_reason = length`, or a pool shortfall each consume one attempt. The budget is judged by the failure that just happened: 2 shortfalls then invalid output is final at attempt 3, while 2 invalid outputs then a shortfall is retried after 60 s. Inside one attempt, `llm.py` retries only request errors (`httpx.RequestError`: transport errors, undecodable bodies, redirect loops) and HTTP 5xx, at most 2 times.
- **Deferral (no attempt consumed):** HTTP 429 → `defer_job` to `Retry-After` (or 60 s); a daily-quota 429 from Cerebras or reaching `AI_DAILY_CALL_LIMIT` → `defer_job` to the next UTC day.
- **Accounting:** every outbound HTTP request to an AI provider calls `incr_ai_calls` first; `usage` (prompt/completion tokens) from every response is logged to `DATA_DIR/logs/ai-usage.jsonl`.
- **Final failure, by kind:** `learn`/`questions` set `WordContent.status = failed` only for first-time generation (a regeneration failure leaves the current `ready` version alone and records the error); `image` sets only `image_status = failed` — unless the word already has a picture (a failed redraw), which then stays `ready` and in use while the failure is recorded on the job only; `topup` records `last_error` on the job and changes nothing else.
- **Chaining:** when `chain = true`, success enqueues the next stage (first-time: `learn` → `questions` → `image`). When `questions` succeeds with the minimum pool for a first-time word, `WordContent.status = ready`.

### 7.3 Model calls

**Model and reasoning.** The default model is `qwen-3.8-27b`. The Learn-card and question-batch calls send `reasoning_effort = LLM_REASONING_EFFORT` (default `medium`; empty → not sent, the model's default); the blind answer check always sends `high`. Chosen by a blind comparison on 17 fresh words: Qwen at medium had about half the serious-problem rate of `gpt-oss-120b` (6% vs 13% of shown questions) and kept 18% more questions, at ~4.7¢ vs ~1.2¢ per word × band. `gpt-oss-120b` remains available (`CEREBRAS_MODEL=gpt-oss-120b`, `LLM_REASONING_EFFORT=` empty).

All calls use `response_format: {type: "json_schema", json_schema: {name, strict: true, schema}}` with `max_completion_tokens = LLM_MAX_COMPLETION_TOKENS`. Cerebras strict mode does not support `oneOf`, `minItems`/`maxItems`, or `pattern`, so: the root is always an object; every object sets `additionalProperties: false`; **every field is required** (type-specific fields use sentinels, §6.1); counts and per-type shapes are enforced by §7.5.

1. **Learn card** — input: word, band style guide (§7.6). Output: all `WordContent` content fields including `forms`. Instruction: do not reuse the same sentence across fields.
2. **Question batch** — root `{questions: [Q]}`. Input: word, band style guide, the card's `senses`, `examples`, `forms`, `word_parts`, `synonyms`, `antonyms`, and the requested mix. Instructions: test only the given senses, but no wrong choice may be a correct use of any other sense; do not reuse any Learn-card sentence or situation, and vary scenes within a batch; wrong choices are near misses that tempt a student who half-knows the word, stated once as a recipe and referenced by every type: same part of speech, length and style, words the band knows, and the same family and tone as the key (for a feeling word, other feelings of the same kind; for an action word, other actions that could happen in the same place; for a describing word, other words that describe the same kind of thing), each ruled out by one specific detail in the question, never nonsense, joke or obviously unrelated choices, never simply the opposite of the key (only an `antonym` key is an opposite: for a word about great age, something old but not that old; for a bad mood, a different bad mood), and never a synonym or near-synonym; `synonym` wrong choices are other describing words for the same kind of thing that are not synonyms; `usage` wrong choices put the word where a look-alike or near-meaning word belongs, never attached to a thing it cannot describe at all; a `scenario` key avoids the definition's words and synonyms, and its wrong choices are people or situations in the same setting showing a related but different behaviour, never the opposite; `antonym` wrong choices never leave the key as the odd one out; `fill_blank` picks its wrong choices first, then writes a sentence whose situation makes only the word fit, with no meaning or definition in the sentence or in parentheses (only `spell_it` has a cue); exactly one defensible answer; explanations name or quote the answer and never refer to a choice letter or position; every `spell_it` prompt ends with a short definition cue in the model's own words (≤ 6 words, no synonym or family word of the answer, no word of 4+ letters starting with the answer's first letter), e.g. `(means: careful with money)` — code then appends the answer's first letter (and word count for a phrase), `(means: careful with money; starts with "f")`, before validation, so the blind check sees it (legacy questions are unchanged); `synonym`/`antonym` correct answers come from the card's lists; `word_parts` questions ask about a part named in the card's `word_parts`. The prompts state their rules abstractly. They contain only format placeholders (e.g. `(means: <short meaning>)`, `"<part> (<meaning>) + <part> (<meaning>) = <combined meaning>"`) and pure word-mechanics examples (inflections such as strive → strove and mouse → mice, stress and sound-alike notes for memory hooks, the band-level examples of easy vs hard synonym words); never sample sentences or content scenes the model could copy, because it copied them.
   - **Initial mix (12):** tier 1 — `meaning`, `pick_word`, `fill_blank` ×2; tier 2 — `usage`, `scenario` ×2, `synonym`, `antonym`; tier 3 — `spell_it` ×2, `word_parts`. Substitutions: no antonyms → extra `scenario`; no synonyms → extra `usage`; empty `word_parts` (including word parts emptied by L10) → extra `spell_it`.
   - **Top-up:** requests `min(6, 40 − pool size)` questions for the tier(s) whose verified count is lowest relative to the initial mix (computed by the worker; not per profile), and includes the pool's existing prompts with an instruction not to repeat them.
3. **Answer-key check (blind)** — root `{results: [{qid, passes, tempting, chosen_index, fill, alternatives, ambiguous, reason}]}`, sent with `reasoning_effort: "high"` (only this call; the other two use `LLM_REASONING_EFFORT`). Code assigns each candidate a `qid` before the call. The checker receives each question **exactly as the learner would see it** — prompt and choices only, never the answer key, the target word (unless it appears in the prompt), or the Learn card — plus one line naming the learners' grade band (`Learners are in grades 6-8.`), which reveals nothing about the answer. It tests each choice on its own: `passes` has one boolean per choice, in order (true if a careful teacher would mark that choice right, even when another is better; `[]` for `spell_it`), and `chosen_index` is its best choice (or `-1`). `tempting` rates every choice, in order, without knowing the key: an objective same-family judgement, true if the choice is the same kind of word or situation as the right answer (another feeling for a feeling word, another way of moving for a movement, another describing word for the same kind of thing, another person's behaviour or another situation in the same setting, a sentence using the word in a realistic but wrong way), false if it is a different kind of word or situation, the opposite of the right answer, a joke, or nonsense (`[]` for `spell_it`). It is not a guess at what a learner knows: a checker that knows the word well rated good near misses as not tempting. For `spell_it` it returns `fill` (else `""`) and `alternatives`: every other word or form that fits the blank and the hint as well (else `[]`). `ambiguous` = true if the question is confusing or nothing fits. A question is kept only if its `qid` appears exactly once, `ambiguous` is false, and — choice types — `passes` has one entry per choice, exactly one is true, and it and `chosen_index` are the `answer_index`, and `tempting` has one entry per choice with at least 2 of the 3 wrong choices true (so a quiz cannot be passed by elimination); `spell_it` — `fill` normalizes to an entry of `accepted_answers` and no alternative remains after removing those that normalize to an accepted answer. Missing, duplicate, or unknown `qid`s → unverified → dropped. Each drop is logged with its reason (`check: 2 choices pass`, `check: alternatives fit (giant)`, `check: too easy (1 of 3 wrong choices tempting)`, `check: 3 tempting for 4 choices`). Kept questions get `verified = true`.

**Minimum pool for readiness:** ≥ 6 verified questions including ≥ 2 tier-1 and ≥ 1 tier-2. If a batch falls short, the next attempt requests a replacement batch for the missing tiers (verified questions already obtained are kept).

### 7.4 Images

`ImageProvider.generate(prompt: str) -> bytes`. Prompt = fixed style preamble ("friendly flat cartoon illustration, consistent soft palette, simple background, no text or letters, kid-safe", plus one sentence asking that any children shown have varied appearances — skin tones, hair, girls and boys — never stereotyped) + `image_scene`. Providers may answer with base64 image data or with a temporary link (z.ai links expire after 30 days); a link is downloaded immediately, **without** the `Authorization` header (the link may point at another host), and downloads over 20 MB are rejected. The result is resized to max 768 px on the long edge, encoded as WebP, stored via `BlobStore.put`, and recorded with `set_image`. `IMAGE_PROVIDER=none` skips the job and leaves `image_status = none`. On failure the Learn page shows the `emoji_scene` card; the Parent area offers "Retry picture". A failed redraw of a word that already has a picture keeps that picture.

### 7.5 Code-level validation (`app/ai/validate.py`)

"Contains the word" always means the §7.7 matcher (word, rule-based inflections, or `forms`).

A **Learn card** is rejected (attempt consumed) if any of these fail:
- L1 `short_def` ≤ 90 chars, `kid_def` ≤ 220 chars, both non-empty.
- L2 1–3 `senses`, each `senses[i].example` contains the word. `examples`: entries that don't contain the word, or that repeat another card sentence (L9), are dropped; the card is rejected only if fewer than 4 remain (at most 6 kept).
- L3 `synonyms`/`antonyms` ≤ 5 each (extras truncated) and none matches the word.
- L4 `right_use.sentence` and `wrong_use.sentence` contain the word; `wrong_use.why` non-empty.
- L5 every sentence ≤ band limit + 5 words (§7.6).
- L6 no blocklisted term anywhere.
- L7 `emoji_scene` is non-empty, contains no letters or digits, and is ≤ 40 code points (the prompt asks for 3–6 emoji). If it fails, a default emoji scene is substituted — it never rejects the card.
- L8 (cleans, never rejects) for a one-word target, a several-word `forms` entry whose last word fails the first-3-letters rule (§7.7) is dropped (`most epoxy` for ephemeral; `more ephemeral` stays). Phrase targets are unchanged.
- L9 (cleans) an `examples` entry equal (normalized) to a sense example, `right_use` or `wrong_use` is dropped before L2 counts.
- L10 (cleans, never rejects) `word_parts` becomes `""` for a phrase target (several words), or when a part it names (the text before each top-level `(`, before the `=`) does not appear in the word's spelling, hyphens removed and case-insensitive (`frux (fruit) + -al` for frugal); a part written `a/b` needs one visible spelling. The question mix then swaps the `word_parts` slot for `spell_it` (§7.3) and Q4 drops any `word_parts` question.

Before the checks, AI card and question text has typographic hyphens (U+2010, U+2011) replaced by `-` and soft hyphens (U+00AD) removed. L8–L10 and this cleaning skip legacy content.

A **question** is dropped (others in the batch survive) if any of these fail:
- Q1 choice types: exactly 4 choices, distinct after case-folding and trimming, `answer_index` in 0–3, `accepted_answers == []`. `spell_it`: `choices == []`, `answer_index == -1`.
- Q2 `fill_blank` and `spell_it`: prompt contains exactly one `___` and does not otherwise contain the word (the code-added first-letter hint is ignored). `spell_it` prompt ends with a definition cue in parentheses (an AI prompt ending in a parenthetical without `means:` and without `___` gets the label added first). An AI `fill_blank` prompt must not end with a parenthetical (a definition or cue gives the answer away; the sentence's situation must make the word fit).
- Q3 `spell_it`: `accepted_answers` non-empty and every entry matches the word.
- Q4 type-specific keys: `pick_word` and `fill_blank` — the correct choice is the target word (or a form); `meaning` — the correct choice is not identical to any distractor; `usage` — every choice contains the word; `synonym`/`antonym` — the correct choice appears in the card's `synonyms`/`antonyms`.
- Q5 no reuse of Learn content: after case-folding, stripping punctuation, and filling `___` with the word and each form, neither the prompt nor any sentence-valued choice equals a Learn-card sentence (`examples`, `senses[].example`, `right_use`, `wrong_use`). Nor is it a near-copy of one of those or of `kid_def`: it shares a first name used in that sentence and ≥ 3 other content words, or (with ≥ 4 content words; Learn-card sentences only, not `kid_def`: the kid_def is itself a definition, so text that describes the meaning, such as a sentence whose situation shows it, a scenario or a `meaning` prompt, overlaps it by design; `meaning` choices and `pick_word` definitions are never compared) ≥ 60% of its content words. Content words are case-folded tokens of 3+ letters minus a small stopword list and the word's own tokens; the near-copy test covers the prompt (except a `pick_word` definition) and `usage`/`scenario` choices. Legacy exempt.
- Q6 sentence length ≤ band limit + 5 words; explanation ≤ 160 chars.
- Q7 no blocklisted term.
- Q8 (top-ups) the normalized prompt does not equal an existing pool prompt (the letter hint is ignored on both sides).
- Q9 the explanation does not refer to a choice by letter or position ("the first sentence", "the 2nd one", "the second definition", "the last one", "option B", "Sentence 1 uses…", "sentence 0", "Answer: B", "B is correct", "(A)"); choices are shuffled. Such an explanation is never rewritten: the question is dropped (no repair can tell a safe rewrite from one that turns "Sentence 2 uses frugal incorrectly." into a false statement, and live runs show these explanations are rare). Legacy exempt.
- Q10 the word is spelled correctly: no prompt or choice writes the word (or a form) with a hyphen added or dropped, a space dropped, or capitals inside it (`ephem-eral`, `ephemerAl`, `selfesteem`); a sentence-start capital or ALL CAPS is fine. Legacy exempt.
- Q11 `synonym`, `antonym`, `pick_word`: no wrong choice is the word or a form of it, or (case-folded, trimmed) one of the card's `synonyms` — a second right answer for `synonym`, a near-synonym for the others. Legacy exempt.
- Q12 `meaning`, `scenario`, `synonym`, `antonym`, `word_parts`: no choice contains the word (a giveaway). Legacy exempt.
- Q13 `spell_it`: the cue meaning (after `means:`, before the code-added hint) has no word of 4+ letters, other than a stopword, that starts with the first letter of `accepted_answers[0]` (`(means: great size)` for gigantic: "great" could be typed). Legacy exempt.

**Blocklist matching** is whole-token after case-folding, including the §7.7 inflections of each entry; a rejection's error text names the blocked term.

Every rejection is logged with the raw model output (redacted) to `DATA_DIR/logs/ai-rejections.jsonl`.

### 7.6 Band style guide (part of every prompt)

| Band | Max sentence words | Settings for examples | Definition style |
|---|---|---|---|
| `3-5` | 15 | school, pets, playground, family, cartoons | very plain words, no word harder than the target anywhere (definitions, synonyms, choices, explanations) |
| `6-8` | 20 | sports, video games (no battles, monsters or weapons), science class (safe lab habits only), friendships, chores | plain, may use one technical term if explained |
| `9-12` | 28 | history, literature, science, jobs, school debate club (school or science topics; no elections, voting, protests or political causes) | precise, SAT-style; "student"/"person", never "kid"; never open a sentence with a time or place phrase; every factual claim true; when mentioning a real event, leave out death tolls and disasters; invented people in historical settings |

### 7.7 Word-form matching (`app/ai/inflect.py`)

- **Tokenize:** case-fold; strip surrounding punctuation and a trailing possessive `'s`; keep internal hyphens and apostrophes.
- **Rule-based forms:** `+s`, `+es`, `+ed`, `+d`, `+ing`, `+er`, `+est`, `+ly`, `+r`, `+st`; `y→ied/ies/ier/iest/ily`; drop-`e` + `ing/ed/er/est`; doubled final consonant + `ed/ing/er/est`; `-le→-ly`; `-ic→-ically`; `c→ck` + `ed/ing`; `ie→ying`.
- **Model-supplied forms:** each entry of the card's `forms` is accepted if it shares the word's first 3 letters (guards against hallucinated "forms"); for a phrase, also if only its first token changes and keeps its first letter (`took for granted`, `gave up`).
- **Phrases:** all tokens must appear in order; the tokens after the first are contiguous, and in running text up to 4 other tokens may stand between the first token and the rest (`took her morning coffee for granted`, `gave it up`); the inflection may fall on any one token. Blocklist phrases (§7.5) stay fully contiguous, and a whole-candidate match (`accepted_answers`, choices) is never split.

This matcher is deliberately permissive and is used for content checks only. Grading of typed answers uses `accepted_answers`.

### 7.8 Cost guardrails

Measured with `qwen-3.8-27b` at medium reasoning: one pass (Learn card ~7k, question batch ~13k, check ~6.5k completion tokens plus ~6k prompt) costs about 4.7¢; with the distractor floor (§7.3, `MIN_TEMPTING_WRONG = 2`) most words need replacement batches, and the in-app checkpoint run (2026-10-08, 24 word × band items, retries included) measured ~6 calls, ~13k prompt + ~72k completion tokens and about **12¢ per word × band** (~$70–90 for a 600-word list in one band; at the default `AI_DAILY_CALL_LIMIT` of 2000 about 300 items per day). Only the Parent area initiates generation; top-ups are limited to one per word × band per day and by the 40-question cap; `AI_DAILY_CALL_LIMIT` caps everything. Images are generated once per word × band × version.

## 8. Learning engine

### 8.1 Session building (`app/learning/session.py`)

Inputs: profile, `mode`, learner `local_date`. Only words in the profile's **current lists** are eligible (progress for other words is kept, so stars return if a list is reassigned).

- **Capacity** `C = max(10, 2 × session_minutes)` items.
- **Reviews:** eligible words with progress where `due_date ≤ local_date` and content `ready`, sorted by `due_date` ascending, then `stage` ascending.
- **New words (normal mode only):** eligible words (list order, then word order) with no progress and content `ready`; take `new_words_per_session`, reduced to `0` if reviews alone ≥ `C`, else to `min(new_words_per_session, floor((C − reviews)/2))`.
- **Practice mode:** eligible words with stage 1–3, ordered by stage ascending then `wrong` descending; at most 12; no new words. Practice answers never change stars (§8.5).
- **Queue:** each review contributes one `question` item; each new word contributes an `intro` item, spread among the reviews (all intros first when there are none). New words' first `question` items form one block at the end of the queue, in intro order; a new word keeps its question only if **≥ 4 other items** sit between its intro and that question (`MIN_ITEMS_BETWEEN_INTRO_AND_QUESTION`), so it is not answered from short-term memory. A word that is too close gets its intro only (no question, empty reserves): `intro_seen` makes it stage 0, due today, so it is first quizzed in a later session. Word selection already fits `C`, so nothing is truncated.
- **Main question per item:** choose the tier from the word's stage (§8.2); prefer verified questions not in `seen_question_ids`; if none unseen in that tier, use the adjacent tier; if still none, the least-recently-seen question in any tier.
- **Reserves per session word** (so the browser never needs another round trip), drawn from **tiers 1–2**: 2 lock-in sets of 3 questions each (each set spanning ≥ 2 types) and up to 2 re-ask questions. Priority when the pool is too small for everything to be distinct: (1) no lock-in question repeats the main question; (2) the two lock-in sets are disjoint; (3) re-asks are distinct from all of the above — re-asks are dropped first (a dropped re-ask reuses a lock-in question the learner has not yet seen, or is skipped).
- **Payload:** the ordered queue; for each word its Learn card, image URL, current `stage`, `last_graded_on`, and reserves; profile settings (timer, break reminder).

If no reviews are due and no new words are ready, the response says why ("Your words are still being prepared — 12 of 20 ready" or "Nothing due today — practice shaky words?"). The practice button is shown only when practice has eligible words.

### 8.2 Question types and tiers

| Tier | Types | Used when the word's stage is |
|---|---|---|
| 1 — recognize | `meaning` (pick the definition), `pick_word` (definition → word), `fill_blank` (choose the word for the blank) | 0–1: 100% tier 1 |
| 2 — use | `usage` (which sentence uses it correctly), `scenario` (which situation fits), `synonym` (closest meaning), `antonym` (opposite) | 2–3: 30% tier 1, 70% tier 2 |
| 3 — recall | `spell_it` (type the word into a sentence; prompt ends with a definition cue and the answer's first letter), `word_parts` (4 choices: what a named part means, e.g. "In *benevolent*, *bene-* means…") | 4–5: 40% tier 2, 60% tier 3 |

All types except `spell_it` are 4-choice.

### 8.3 In-session flow

1. **Intro item (new word):** picture (or emoji card), word with 🔊, part of speech, `short_def`, one example. Button: "Got it →". Logs `intro_seen`.
2. **Question item:** type label, prompt, choices (**shuffled each time shown**, with `answer_index` remapped) or a text box, and **"I'm not sure"**. 🔊 reads: the definition for `pick_word`; the sentence with "blank" for `fill_blank`/`spell_it` (never the target word); the prompt for all other types. Keys 1–4 select a choice; Enter submits/advances.
3. **Correct:** green feedback and the explanation. Displayed stars change only if this is the word's first graded answer today in a normal session, computed in the browser by `web/js/srs.js` (a mirror of §8.5 checked against `tests/srs_vectors.json`); the server remains authoritative for stored progress and results. "Next →".
4. **Wrong:** red feedback, the correct answer, the explanation; buttons **📖 Learn this word** (primary) and "skip for now". A typed answer within edit distance 1 of an accepted answer (answer length ≥ 5) shows "So close — check the spelling" but is graded wrong.
5. **"I'm not sure":** graded as a miss (recorded as `unsure`), shows the answer, and opens Learn directly.
6. **Re-ask:** after a miss, a re-ask item for that word is inserted 5 positions later (or at the end if fewer remain), using the next unused re-ask reserve; at most 2 re-asks per word per session.

### 8.4 Learn page and lock-in check

**Learn page** sections in order: picture/emoji card; word + 🔊 + part of speech; `short_def` (prominent) and `kid_def`; senses (one card each, when > 1); memory hook (if non-empty); word parts (if non-empty); examples with the word highlighted (🔊 per sentence); ✅ right use / ❌ wrong use with "why" (if present); synonym and antonym chips. Footer button: **"I've got it — check me"** (hidden once the word has used both check attempts this session). "← back to session" is also available (no check; the re-ask still happens).

**Lock-in check:** at most **2 check attempts per word per session**, counted across all Learn visits; attempt 1 uses reserve set 1, attempt 2 uses set 2.
- **Pass = at least 2 of 3 correct** → toast "Locked in! 🔒" → return to the exact queue position.
- **First fail** → Learn page again with the missed question's explanation highlighted at the top → "check me" runs attempt 2.
- **Second fail** → "Let's come back to this one tomorrow" → return to the session; that word's pending re-asks are cancelled; `due_date` is set to tomorrow (§8.5). Any later miss on that word this session shows the answer and an optional Learn page with no check.
- Each check question logs a `check_answer` event (stats only). Each completed attempt logs one `check_result` event (`check_set`, `correct_count`, `passed`), from which the server updates session counters and applies the second-fail rule. Check answers **never change stars**.

### 8.5 Spaced-repetition rules (`app/learning/srs.py`)

Interval table (days) by the stage reached after a correct answer: 1 → 1, 2 → 3, 3 → 7, 4 → 14, 5 → 30.

- **Which answers change the stage:** only the first graded answer (`answer` or `unsure`; not `check_answer`) for a word on a given learner local date, **in a `normal` session**. Later answers that day, and all `practice` answers, update counts and `seen_question_ids` only.
- **Correct:** `stage = min(5, stage + 1)`. If the stage before this answer was < 5, `interval_days = table[new stage]` (so 4 → 5 gives 30). If it was already 5, `interval_days = min(60, max(30, interval_days × 2))`. `due_date = local_date + interval_days`. `last_graded_on = local_date`.
- **Miss (wrong or unsure):** if `stage == 0` it stays 0; otherwise `stage = max(1, stage − 2)`. `interval_days = 1`; `due_date = local_date + 1`; `last_graded_on = local_date`.
- **Practice miss:** stage unchanged; `due_date = min(due_date, local_date + 1)`.
- **New word introduced** (`intro_seen`): progress is created with `stage = 0`, `due_date = local_date`, `introduced_on = local_date`.
- **Second lock-in fail** (`check_result` with `check_set = 2`, `passed = false`): `due_date = local_date + 1`; stage unchanged.
- **Ordering:** the server applies each batch of events sorted by `at`. A graded event whose `local_date` is earlier than the word's `last_graded_on` updates counts only.
- **Derived status:** *new* = no progress; *learning* = stage 0–4; *mastered* = stage 5; *due today* = `due_date ≤ local_date`.

### 8.6 Session end

The session ends when the timer reaches 0 (the current item is finished first), the queue is exhausted, or the learner taps "✕ End" (if ≥ 1 answer, results are shown; otherwise return home). A session that runs out of items shows results if the learner answered a question or met a new word (accuracy shows "—" with no answers). The timer keeps running on the Learn and check screens. Results show: accuracy, questions answered, new words met, words that gained stars, up to 5 "keep practicing" words (lowest stage among words missed this session), and the break reminder card if enabled.

## 9. HTTP API

All JSON. When `SITE_ACCESS_CODE` is set, **every `/api/*` route except `POST /api/auth/site` requires the `wq_site` cookie** — including `/api/auth/parent` and `/api/parent/*`. Parent endpoints additionally require `wq_parent`.

**Auth**
- `POST /api/auth/site` `{code}` → sets `wq_site` (signed, HttpOnly, SameSite=Lax, 180 days).
- `POST /api/auth/parent` `{passcode}` → sets `wq_parent` (signed, HttpOnly, SameSite=Lax, 12 hours).
- `POST /api/auth/parent/logout`.
- Wrong codes: max 5 failures per scope per client IP per 15-minute window (counted via the Repository), then HTTP 429. Phase 1 uses the socket peer address as the client IP (Phase 2: §15).

**Learner**
- `GET /api/profiles` → `[{id, name, avatar}]`
- `GET /api/profiles/{id}/home?local_date=` → counts (mastered, learning, new, due today, ready new), practice-eligible count, preparing status.
- `POST /api/profiles/{id}/sessions` `{mode, local_date}` → session payload (§8.1).
- `POST /api/sessions/{sid}/events` `{events: [...]}` → `{accepted: [client_event_id, ...]}` via `apply_events` (§6.3).
- `POST /api/sessions/{sid}/finish` `{active_minutes}` → results summary (§8.6).

**Parent**
- `GET|POST /api/parent/profiles`, `PATCH|DELETE /api/parent/profiles/{id}` (DELETE cascades per §6.3).
- `GET|POST /api/parent/lists`, `PATCH|DELETE /api/parent/lists/{id}` (`POST`/`PATCH` accept `name`, words / `add_words` / `remove_words`, and `assign_profile_ids`; responses include rejected entries).
- `PUT /api/parent/profiles/{id}/lists` `{list_ids}`.
- `GET /api/parent/content?list_id=&band=` → per-word `status`, `image_status`, pool size, `error`, regeneration in progress.
- `GET /api/parent/content/{band}/{word}` → Learn card + full pool (with answers).
- `POST /api/parent/content/{band}/{word}/regenerate` `{part}`.
- `GET /api/parent/queue` → job counts by status; `get_ai_calls(today)` and the limit; whether jobs are deferred to tomorrow.
- `GET /api/parent/profiles/{id}/stats` → §11 dashboard data.
- `GET /api/parent/status` → AI configured (bool), model, image provider (never key values).
- `GET /api/parent/export` → JSON backup; `POST /api/parent/import` (multipart JSON, replace mode, `confirm=true` required).

**Static:** `/` → `web/index.html`; `/static/*` → `web/`; `/media/*` → `BlobStore`.

## 10. Learner UI

Phone- and iPad-first, works on desktop; style carried over from `legacy/index.html` (indigo/violet gradient hero, rounded white cards, large buttons, stars). Hash-routed screens: `#/gate`, `#/profiles`, `#/home`, `#/session`, and in-session overlays for intro, learn, check, results. `aria-live` on feedback regions; visible focus states; tap targets ≥ 44 px.

**Event buffering (`api.js`):** events are appended to an ordered queue mirrored to `localStorage`. At most one upload is in flight, and it always sends the oldest pending events first (every 5 s and on screen change, with backoff on failure). The client removes exactly the ids returned in `accepted`. Pending events are flushed before `finish`. Since the payload contains everything the session needs, a dropped connection does not interrupt the learner.

**Pronunciation (`speech.js`):** `speechSynthesis` with an `en-US` voice when available, rate 0.9. 🔊 buttons hide if the browser has no speech support.

**Add to Home Screen:** `manifest.webmanifest` with name, icons, `display: standalone`, theme color.

## 11. Parent area

Reached from a small "Parent" link on the profile picker; requires the parent passcode.

- **Profiles:** list; add/edit (name, avatar picker, band, session minutes, new words per session, break reminder + message); assign/reorder lists; delete with typed confirmation.
- **Word lists:** list with word counts and readiness ("18 / 20 ready"); create (name + textarea, one word per line or comma-separated, plus "assign to" profile checkboxes so generation starts on save — a list assigned to no profile has no band and generates nothing until assigned); edit (add/remove words); rejected entries shown with reasons; per-word row: status badge, pool size, **Preview** (renders the Learn page and the full question pool with answers), **Regenerate** (all / learn / questions / picture), **Retry** for failures. A queue banner shows progress while jobs run (and "paused until tomorrow" when the daily cap is hit).
- **Progress dashboard (per profile):** mastered / learning / new counts; overall accuracy and accuracy by question type; "not sure" rate; days practiced in the last 30 days; weakest 12 words (lowest stage, most misses); last 10 sessions (date, minutes, questions, accuracy).
- **Status & backup:** AI configured / model / image provider; today's AI calls vs. limit; Export and Import (replace, with confirmation).

## 12. Security and access control

- Keys and passcodes live only in `.env` / environment; never logged, never returned.
- **Redaction:** all stored and returned error text (`WordContent.error`, `Job.last_error`) and all log lines pass through `security.redact()`, which replaces every configured secret value.
- **Leak test:** configures random sentinel secrets, drives the real `llm.py` and image client through an `httpx.MockTransport` that fails with error messages echoing the key in both URL and body, then scans every endpoint response, stored `error`/`last_error` fields, the export output, and files under `DATA_DIR/logs/` for the sentinels.
- Cookies are signed with `SECRET_KEY` (`itsdangerous`), HttpOnly, SameSite=Lax; `Secure` when the request scheme is HTTPS.
- Passcode comparison uses `hmac.compare_digest`.
- Attempt limiting on both codes (§9).
- All AI text is HTML-escaped when rendered (`esc()` everywhere; no `innerHTML` with unescaped model output).
- Profile switching has no password by design (family/classroom use).

## 13. Legacy starter set

`python -m app.seed_legacy` (also run automatically on first start when the database is empty) parses `legacy/index.html` and creates a list "Starter set" with 24 words as `WordContent` for band **`6-8` only**, `source = legacy`, `status = ready`:
- Field mapping: `def → short_def`, `full → kid_def`, `ex → examples`, `parts → word_parts`, `syn → synonyms`, a single sense from `pos`/`def`/`ex[0]`; `forms` from the §7.7 rules; empty `memory_hook`, `right_use`, `wrong_use`; default `emoji_scene`.
- The 8 embedded images are decoded, converted to WebP, stored via `BlobStore`, and recorded with `set_image`.
- Questions are assembled in code, `verified = true`: `meaning`, `pick_word`, `fill_blank`, `spell_it` (with a definition cue), and one `synonym` question per word (correct choice from `syn`, distractors from other words' `syn`), with distractors drawn from other starter words.
- Legacy content is exempt from the §7.3 minimum pool, L2's 4-example minimum, L4, and Q5 (with only 2 examples, `fill_blank`/`spell_it` must reuse them).

When AI is configured, a parent can "Regenerate → all" any starter word to get full AI content; profiles in other bands get normal AI generation for the starter words when the list is assigned.

## 14. Error handling and testing

### 14.1 Failure behavior

| Failure | Behavior |
|---|---|
| Cerebras error / timeout (`LLM_TIMEOUT_S`, 180 s) | Retries per §7.2; then failure handling by job kind. Sessions are unaffected (ready content only). |
| Cerebras 429 / daily cap | Job deferred without consuming an attempt; Parent banner explains. |
| No Cerebras key | Starter set works for band `6-8` profiles; Parent area shows "AI not configured"; saving lists still works and words stay `pending` until a key is set. |
| Image failure | Emoji card; "Retry picture"; the word stays `ready`. A failed redraw keeps the earlier picture. |
| Regeneration failure | Current version keeps serving; error shown in Parent area. |
| Server restart mid-job | The job's lease expires and it is claimed again. |
| Lost connection mid-session | Session continues from the preloaded payload; events retried in order. |
| Invalid AI output | Item dropped or attempt consumed (§7.5); raw output logged (redacted). |

### 14.2 Tests

- **Unit (`pytest`):**
  - `srs.py`: every rule in §8.5, including the same-day rule, practice mode, 4 → 5 and mastered intervals, out-of-order events, second lock-in fail; the same cases are written to `tests/srs_vectors.json`.
  - `session.py`: capacity, current-lists filter, review ordering, new-word reduction, tier selection and fallback, reserve priority rules with small pools, practice mode.
  - `validate.py` and `inflect.py`: each L/Q rule with passing and failing fixtures, including irregular forms, phrases, and blocklist whole-token matching.
  - `grading.py`: normalization, near-miss. Word normalization and rejection reasons.
  - `sqlite_repo.py` against a temp database: `apply_events` idempotency and atomicity (simulated failure mid-apply leaves nothing applied), job leases and claim priority, `enqueue_job` semantics, `swap_draft`, `set_image` version guard, cascading deletes, export/import round trip.
- **AI layer:** `content.py` and `jobs.py` with a `FakeLLM` that returns canned JSON per schema name, including bad outputs (wrong counts, missing/duplicate `qid`, ambiguous check results, `finish_reason = length`, 429 with `Retry-After`) to exercise drops, attempts, deferrals, failure-by-kind, and version-guard discards. No network in the test suite.
- **API (`TestClient`):** site gate on/off (incl. parent routes behind the gate), parent auth and attempt limiting, profile/list CRUD and rejected entries, generation triggers enqueue the right jobs, regenerate keeps the old version serving until swap, session start → events → finish, the leak test (§12).
- **JS:** `node tests/js/srs.test.mjs` runs `srs_vectors.json` against `web/js/srs.js` (skipped if Node is not installed).
- **Structure:** a test asserting only `app/storage/` imports `sqlite3`.
- **Manual live check:** `scripts/smoke_ai.py` generates 2 words for one band against real Cerebras and prints the result and token usage.
- **End to end (manual, in-browser):** full session including wrong → Learn → check pass, check fail twice, "I'm not sure", timer end; Parent list creation through ready status; regenerate.
- **Content-quality checkpoint:** before bulk generation, generate ~10 words across all 3 bands and review together; adjust prompts; revise the §7.8 estimate.

## 15. Phase 2 outline (not built in Phase 1)

- `STORAGE=gcp` selects a `FirestoreRepository` and a `GcsBlobStore` implementing §6.3; Dockerfile; deploy to Cloud Run in a free-tier region (`us-central1`, `us-east1`, or `us-west1`); images served from Cloud Storage.
- Secrets (`CEREBRAS_API_KEY`, image key, codes, `SECRET_KEY`) via Secret Manager. With `STORAGE=gcp` the app refuses to start if `SECRET_KEY` or `SITE_ACCESS_CODE` is empty.
- Logs (AI usage, rejections) go to stdout → Cloud Logging instead of `DATA_DIR/logs/`.
- Client IP for attempt limiting comes from the `X-Forwarded-For` entry appended by Google's front end; cookies are always `Secure`.
- Cloud Run instances scale to zero, so the in-process worker moves to Cloud Tasks or a Cloud Run job; leases and key-based job uniqueness already fit Firestore.
- Migration uses a CLI (not the HTTP import): copy `DATA_DIR/images/` to the bucket under the same keys, then write the export to Firestore in batches; jobs are re-enqueued for `pending`/`failed` content.
- A $1 budget alert on the billing account.

## 16. Open items (resolved during implementation)

1. **Image model** — resolved 2026-10-08 at checkpoint B: z.ai `IMAGE_MODEL=cogview-4-250304` with `IMAGE_QUALITY=standard` (`IMAGE_PROVIDER=openai_compatible`, `IMAGE_BASE_URL=https://api.z.ai/api/paas/v4`), picked by the parent after comparing `glm-image` and `cogview-4-250304` on the same 5 words (measured ≈69 s vs ≈8.5 s per picture; glm-image looked more polished, cogview-4 was judged good enough at a third of the time and two thirds of the price).
2. **Cerebras rate limits** for the account tier — tune `GEN_CONCURRENCY` from observed 429s.
