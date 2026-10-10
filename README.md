# WordQuest

WordQuest is a vocabulary trainer for kids (in the style of Membean) that runs on your own computer.

A parent pastes a list of words. An AI (Cerebras) writes a kid-friendly **Learn page** and a pool of quiz
questions for every word, pitched to the learner's grade band (grades 3–5, 6–8 or 9–12). Every AI answer key is
checked by code and by a second, blind AI pass before a learner sees it. Learners answer varied questions; a wrong
answer (or "I'm not sure") opens the Learn page — picture, meanings, memory hook, example sentences — followed by a
quick 3-question "lock it in" check. Words come back on a spaced-repetition schedule across days, and stars show
how well each word is known. Extra practice counts too: a word's stars can change up to 3 times a day, at least 2 hours
apart, in regular or practice sessions.

- Runs locally: one Python process (FastAPI + SQLite + a local pictures folder). No cloud account is needed
  except a Cerebras API key for generating new words.
- Works on iPads and phones on your home Wi-Fi, and can be added to the Home Screen like an app.
- No accounts: a "Who's learning?" picker, plus an optional shared access code for the whole site and a passcode
  for the Parent area.
- A 24-word starter set (grades 6–8) works even without an API key.
- The original single-file version of the app is kept at `legacy/index.html`.

## Requirements

- macOS (Linux works the same way) with **Python 3.10 or newer**. Check with `python3 --version`. If it prints 3.9
  or older, install a newer Python (python.org installer, or `brew install python@3.12`) and use that interpreter
  (for example `python3.12`) in the commands below.
- **Node.js 21 or newer** (the tests were run on Node 24) — only needed to run the JavaScript tests. The test command
  below passes a quoted `"tests/js/*.test.mjs"` pattern that Node itself expands, which older versions do not do.
- A **Cerebras API key** for generating new words (see [Adding the Cerebras key](#adding-the-cerebras-key)).
- Optional: a **z.ai API key** for pictures (see [Pictures (z.ai)](#pictures-zai)); without one, Learn pages show
  emoji scenes.

## Setup

```bash
git clone https://github.com/zhouning64/wordquest.git
cd wordquest
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt     # requirements.txt is enough if you will not run the tests
cp .env.example .env
```

`.env` holds your settings and secrets. It is git-ignored — never commit it. The copied file works as-is (AI off,
no passcodes); the sections below say which lines to fill in.

## Run on this computer

```bash
.venv/bin/uvicorn app.main:app
```

Open <http://127.0.0.1:8000>. On the first start the app creates the `data/` folder and the "Starter set" word list.
Stop the server with `Ctrl+C`.

After changing `.env`, restart with `scripts/restart.sh` (add `--lan` for an iPad or phone on your Wi-Fi). It stops
the WordQuest server already running on the port, even one started in another terminal, and starts it again in this
terminal; it refuses to touch a port that another program is using.

To use the app you need at least one learner profile, created in the Parent area (next sections): set a parent
passcode, add a profile, and assign it a word list.

## Run for an iPad or phone on your home Wi-Fi

1. In `.env`, set a shared access code that everyone in the family will type once per device (it is remembered
   for 180 days):

   ```
   SITE_ACCESS_CODE=pick-a-family-code
   ```

   Without an access code anyone on your Wi-Fi could open the app. The server does not warn at start-up; it logs a
   warning in its terminal once, the first time another device reaches it while no access code is set.

2. Start the server so other devices can reach it:

   ```bash
   .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

   If macOS asks whether Python may accept incoming network connections, click **Allow**.

3. Find the Mac's address on your Wi-Fi (its LAN IP):

   ```bash
   ipconfig getifaddr en0
   ```

   This prints something like `192.168.1.23`. If it prints nothing, try `ipconfig getifaddr en1`, or open
   **System Settings → Wi-Fi → Details…** next to your network and read **IP address**.

4. On the iPad or phone (connected to the same Wi-Fi), open Safari at `http://192.168.1.23:8000` — your IP from
   step 3 — and enter the access code.

5. Optional: in Safari tap **Share → Add to Home Screen** to get a WordQuest icon that opens full-screen.

Keep the Mac awake while someone is practicing. If the iPad cannot connect: check both devices are on the same
network, that you started the server with `--host 0.0.0.0`, and that the IP has not changed (routers sometimes hand
out a new one after a restart — run step 3 again).

## Parent passcode and the Parent area

The Parent area is where you add learner profiles, paste word lists, preview and regenerate words, see progress,
and make backups. It is turned off until you set a passcode in `.env`:

```
PARENT_PASSCODE=pick-a-parent-passcode
```

Restart the server, then on the "Who's learning?" screen tap **Parent** and enter the passcode (you stay logged in
for 12 hours on that device). After 5 wrong tries the Parent login is locked for 15 minutes. If you forget the
passcode, change it in `.env` and restart.

Changing a code (`PARENT_PASSCODE` or `SITE_ACCESS_CODE`) stops new logins but does not log out devices that already
entered it. To log every device out, stop the server, delete `data/secret_key`, and start it again — everyone then
re-enters the codes. (If you set `SECRET_KEY` in `.env`, change that value instead.)

In the Parent area:

- **Profiles** — name, avatar, grade band, session length (5–30 minutes), new words per session (0–30), the break
  reminder, and which word lists the learner studies (in order).
- **Word lists** — paste words one per line or separated by commas (up to 600 per list) and tick the profiles to
  assign. Words start being prepared in the background right away; the page shows "18 / 20 ready" per grade band
  and a progress banner while the AI works. Entries that are too long, longer than 3 words, or not kid-appropriate
  are listed with the reason instead of being added. Each word has **Preview** (the Learn page plus every question
  with its answer), **Regenerate** (everything, Learn card + questions, questions only, or picture only — learners
  keep the old version until the new one is ready) and **Retry** when something failed.
- **Progress** — mastered / learning / new counts, accuracy by question type, the "not sure" rate, days practiced,
  the weakest words, and the last 10 sessions.
- **Status & backup** — whether AI is configured, the model, the picture provider and its model, today's AI
  calls, and backup export/import.

## Adding the Cerebras key

1. Create an API key in the Cerebras Cloud console: <https://cloud.cerebras.ai> → **API Keys**.
2. Put it in `.env`:

   ```
   CEREBRAS_API_KEY=csk-your-key-here
   ```

   The default model is `qwen-3.8-27b` at medium reasoning (`LLM_REASONING_EFFORT=medium`, used for the Learn card
   and the questions; the blind answer check always uses high). In a blind comparison on 17 fresh words it had about
   half the serious-problem rate of `gpt-oss-120b` (6% vs 13% of shown questions) and kept 18% more questions.
   `gpt-oss-120b` remains available: set `CEREBRAS_MODEL=gpt-oss-120b` and leave `LLM_REASONING_EFFORT=` empty (the
   model's own default). Any other Cerebras model that supports strict `json_schema` output also works.

3. Check the key with the smoke script (it generates two words and prints the Learn cards, the questions that passed
   the checks, and the token usage; nothing is saved to the database):

   ```bash
   .venv/bin/python scripts/smoke_ai.py
   .venv/bin/python scripts/smoke_ai.py --band 3-5 brave curious     # other words / another band
   .venv/bin/python scripts/smoke_ai.py --reasoning-effort high brave  # override LLM_REASONING_EFFORT
   ```

   Exit code 1 with "CEREBRAS_API_KEY is not set" means `.env` has no key (run the script from the repo root).

4. Restart the server. **Parent → Status & backup** now shows "AI: configured".

Cost and rate controls (all in `.env`):

- Cost with the default model: about 12¢ per word per grade band, measured in the app at the content checkpoint
  (2026-10-08, 24 words, retries included), so a 600-word list costs roughly $70–90 for one band (pictures are
  extra, see [Pictures](#pictures-zai)). The strict checks drop many questions (wrong choices that are too easy,
  more than one defensible answer), so a word takes about 6 Cerebras calls on average: a Learn card, then question
  batches and answer checks until the pool is big enough (up to 5 attempts).
- At about 6 calls per word, the default `AI_DAILY_CALL_LIMIT` of 2000 covers roughly 300 word × band items a day;
  raise it before adding a very large list if you want it ready the same day.
- `AI_DAILY_CALL_LIMIT` (default 2000) caps outbound AI requests (text and pictures) per UTC day. When it is
  reached, waiting words pause until tomorrow and the Parent area says "Paused until tomorrow". To resume sooner,
  raise the limit and restart the server.
- `GEN_CONCURRENCY` (default 3) is how many words are prepared in parallel. Lower it if the Cerebras free tier
  answers with many "429 Too Many Requests" errors.
- Only the Parent area starts generation for new words. Learners can only trigger small question top-ups (at most
  one per word per day).

Without a key the app still works with the starter set and with any content generated earlier; newly added words
stay "preparing" until a key is configured (add it and restart the server; they resume right away).

## Pictures (z.ai)

By default (`IMAGE_PROVIDER=none`) every Learn page shows an emoji scene instead of a picture. WordQuest draws its
pictures with **z.ai**, through z.ai's OpenAI-style images API (`POST <base url>/images/generations`):

1. Create an API key in the z.ai console: <https://z.ai/manage-apikey/apikey-list>.
2. Put these lines in `.env`. The key belongs only in `.env` (git ignores it) — never paste it into a chat, an
   issue, a screenshot or a commit:

   ```
   IMAGE_PROVIDER=openai_compatible
   IMAGE_BASE_URL=https://api.z.ai/api/paas/v4
   IMAGE_MODEL=cogview-4-250304
   IMAGE_QUALITY=standard
   IMAGE_API_KEY=<your z.ai API key>
   ```

3. Restart the server. New words get a picture automatically; for existing words use **Parent → Word lists → word
   → ↻ Regenerate… → Picture only**.

All three of `IMAGE_BASE_URL`, `IMAGE_API_KEY` and `IMAGE_MODEL` are required — there is no default base URL, so a
z.ai key is never sent anywhere else. If one is missing (or `IMAGE_PROVIDER` is misspelled), WordQuest still starts,
with pictures off, logs why, and **Parent → Status & backup** shows the reason under "Pictures".

Choosing the model (`IMAGE_MODEL`). The prices are the ones z.ai published when this was written — check the current
prices on z.ai before making many pictures:

| Model | Price per picture | Default quality and typical time | Allowed `IMAGE_SIZE` |
|---|---|---|---|
| `glm-image` | $0.015 | `hd`, about 20 s | 1024–2048 px per side, multiples of 32 (z.ai default 1280x1280) |
| `cogview-4-250304` | $0.01 | `standard`, about 5–10 s | 512–2048 px per side, multiples of 16 (z.ai default 1024x1024) |

- Picked at the image checkpoint (2026-10-08) after comparing both models on the same 5 words: `IMAGE_MODEL=cogview-4-250304` with `IMAGE_QUALITY=standard`. Measured there: about 8.5 s per picture for `cogview-4-250304` and about 69 s for `glm-image` (the table's times are z.ai's). To compare yourself, make pictures for the same few words with each model (**↻ Regenerate… → Picture only**).
- `IMAGE_SIZE` (default `1024x1024`, valid for both models) is sent as `size`. WordQuest shrinks every picture to at
  most 768 px before storing it, so larger sizes rarely show.
- `IMAGE_QUALITY` (default empty = the model's own default) is sent as `quality` only when set: `standard` (faster)
  or `hd` (more detail).

z.ai answers with a temporary link that expires after 30 days, so WordQuest downloads every picture right away
(without sending the API key to the download host; anything over 20 MB is refused; if the link is not ready yet —
z.ai sometimes answers "file not exist" for a few seconds — it waits and fetches the same link again for up to about
30 seconds instead of paying for a new picture) and stores it as a WebP file in
`data/images/` — nothing depends on the link afterwards. Pictures are made once per word, grade band and content
version, and every picture request counts toward `AI_DAILY_CALL_LIMIT`. If a picture fails, learners see the emoji
scene; the word's row in the Parent area shows "🖼 failed" with the reason underneath (Preview shows it too, for
example "image provider HTTP 401" for a wrong key) and a **Retry picture** button. If redrawing a word that already
has a picture fails, the earlier picture stays in use.

Other services with the same OpenAI-style images API work too: set their `IMAGE_BASE_URL` and `IMAGE_MODEL`.
WordQuest sends `model`, `prompt`, `size` and (when set) `quality` — no `n` and no `response_format` — and accepts
either a base64 `b64_json` answer or an image `url`.

## Running the tests

```bash
.venv/bin/python -m pytest              # Python: unit, storage, AI-layer (faked), API and security tests
node --test "tests/js/*.test.mjs"       # JavaScript: spaced-repetition mirror, session engine, Parent helpers
```

The JavaScript command needs Node 21 or newer (see Requirements). The tests never call the network; the AI and the
image provider are replaced by fakes.

## Your data and backups

Everything lives in the `data/` folder (git-ignored; change the location with `DATA_DIR` in `.env`):

| Path | What it is |
|---|---|
| `data/wordquest.db` (+ `-wal`, `-shm`) | SQLite database: profiles, lists, Learn cards, questions, progress, sessions |
| `data/images/` | Generated pictures (WebP) |
| `data/logs/ai-usage.jsonl` | Token usage of every AI call |
| `data/logs/ai-rejections.jsonl` | AI output that failed the checks (keys and passcodes are redacted) |
| `data/secret_key` | Cookie-signing key, created automatically when `SECRET_KEY` is empty |

**Backup:** Parent → Status & backup → **Download backup** saves one `.json` file with all profiles, lists, Learn
cards, questions and progress. Pictures are not inside that file — also copy the `data/images/` folder.

**Restore or move to another computer:** set up WordQuest there, copy your `data/images/` folder into its `data/`
folder, then Parent → Status & backup → choose the backup file → tick "I understand this replaces all current data"
→ **Import backup**. Import replaces everything on that server. Words whose picture file is missing fall back to the
emoji scene (make a new one with **↻ Regenerate… → Picture only**). Alternatively, stop the server and copy the
whole `data/` folder.

## Configuration reference

| Variable | Default | Meaning |
|---|---|---|
| `CEREBRAS_API_KEY` | *(empty)* | Empty = AI disabled |
| `CEREBRAS_MODEL` | `qwen-3.8-27b` | Cerebras model with strict `json_schema` support (`gpt-oss-120b` also works) |
| `CEREBRAS_BASE_URL` | `https://api.cerebras.ai/v1` | |
| `LLM_MAX_COMPLETION_TOKENS` | `40000` | Sent as `max_completion_tokens` |
| `LLM_TIMEOUT_S` | `180` | Seconds per AI request |
| `LLM_REASONING_EFFORT` | `medium` | `low`, `medium` or `high` for the Learn-card and question calls; empty = the model's default (use empty for `gpt-oss-120b`). The answer check always uses `high` |
| `IMAGE_PROVIDER` | `none` | `none` or `openai_compatible` (z.ai: see [Pictures](#pictures-zai)) |
| `IMAGE_BASE_URL` | *(empty)* | Required for `openai_compatible` (no default) — z.ai: `https://api.z.ai/api/paas/v4` |
| `IMAGE_MODEL` | *(empty)* | Required for `openai_compatible` — z.ai: `glm-image` or `cogview-4-250304` |
| `IMAGE_API_KEY` | *(empty)* | Required for `openai_compatible` — your z.ai key, only in `.env` |
| `IMAGE_SIZE` | `1024x1024` | Sent as `size`; valid for both z.ai models |
| `IMAGE_QUALITY` | *(empty)* | `standard` or `hd`; empty = the model's default (not sent) |
| `SITE_ACCESS_CODE` | *(empty)* | Shared code for the whole site; empty = no gate |
| `PARENT_PASSCODE` | *(empty)* | Empty = Parent area turned off |
| `SECRET_KEY` | *(auto)* | Cookie signing key; generated into `data/secret_key` when empty |
| `DATA_DIR` | `./data` | Database, pictures, logs |
| `STORAGE` | `local` | Phase 2 adds `gcp` |
| `GEN_CONCURRENCY` | `3` | Words prepared in parallel |
| `AI_DAILY_CALL_LIMIT` | `2000` | AI requests per UTC day before generation pauses until tomorrow |

## Phase 2 (not built yet)

Phase 1 runs on one computer. Phase 2 will run the same app on Google Cloud Run with Firestore and Cloud Storage
behind the same storage interface (`STORAGE=gcp`), with secrets in Secret Manager. See §15 of
`docs/superpowers/specs/2026-10-07-wordquest-ai-design.md`.

## Project layout

```
app/                 FastAPI app: config, auth, API routes, AI pipeline, learning rules, storage, job worker
web/                 Browser app (plain ES modules, no build step): learner screens and the Parent area
scripts/smoke_ai.py  Live check against the real Cerebras API
tests/               pytest suite; tests/js/ holds the Node tests
legacy/index.html    The original single-file app (also the source of the starter set)
docs/                Design spec and implementation plan
```
