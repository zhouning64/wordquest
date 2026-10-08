"""Manual live check: generate WordQuest content for a few words against the real Cerebras API.

Run from the repo root (reads CEREBRAS_* settings from .env):

    .venv/bin/python scripts/smoke_ai.py                      # "tenacious" and "in lieu of", band 6-8
    .venv/bin/python scripts/smoke_ai.py --band 3-5 brave curious

For each word it makes one Learn card and one initial question batch (with the blind answer-key check),
then prints the card, the questions that survived validation and checking, and the token usage.
Nothing is written to the database. Usage and rejection logs go to DATA_DIR/logs/ like the real worker.
Exit codes: 0 = every word succeeded, 1 = no CEREBRAS_API_KEY, 2 = at least one word failed
(including a word whose verified questions fall below the minimum pool the real worker needs).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, TextIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.content import MIN_POOL, MIN_TIER1, MIN_TIER2, ContentGenerator, initial_mix, pool_meets_minimum  # noqa: E402
from app.ai.llm import CerebrasClient, LLMError, LLMResult  # noqa: E402
from app.config import Settings  # noqa: E402
from app.logs import JsonlLog  # noqa: E402
from app.models import BANDS, LearnCard, Question  # noqa: E402
from app.security import redact, register_secrets  # noqa: E402

DEFAULT_WORDS = ["tenacious", "in lieu of"]
DEFAULT_BAND = "6-8"
NO_KEY_MESSAGE = (
    "ERROR: CEREBRAS_API_KEY is not set.\n"
    "Add CEREBRAS_API_KEY=<your key> to the .env file in the repo root (see README, 'Adding the Cerebras key'),\n"
    "then run this script again from the repo root."
)
LETTERS = "ABCD"


class UsageRecorder:
    """Wraps an LLM client and remembers the token usage of every call it makes."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[dict] = []

    async def chat_json(
        self, *, name: str, schema: dict, system: str, user: str, reasoning_effort: str | None = None
    ) -> LLMResult:
        result = await self.inner.chat_json(
            name=name, schema=schema, system=system, user=user, reasoning_effort=reasoning_effort
        )
        self.calls.append({"name": name, "usage": dict(result.usage or {}), "finish_reason": result.finish_reason})
        return result


def _num(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def summarize_usage(calls: list[dict]) -> dict:
    total = {"calls": len(calls), "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for call in calls:
        usage = call.get("usage") or {}
        prompt = _num(usage.get("prompt_tokens"))
        completion = _num(usage.get("completion_tokens"))
        total["prompt_tokens"] += prompt
        total["completion_tokens"] += completion
        total["total_tokens"] += _num(usage.get("total_tokens")) or prompt + completion
    return total


def format_card(word: str, band: str, card: LearnCard) -> str:
    lines = [
        "",
        f"=== {word} ({band}) — {card.pos} ===",
        f"short_def:   {card.short_def}",
        f"kid_def:     {card.kid_def}",
        f"forms:       {', '.join(card.forms) or '(none)'}",
        "senses:",
    ]
    for i, sense in enumerate(card.senses, 1):
        lines.append(f"  {i}. ({sense.pos}) {sense.definition} — {sense.example}")
    lines.append("examples:")
    lines.extend(f"  - {ex}" for ex in card.examples)
    lines += [
        f"word_parts:  {card.word_parts or '(none)'}",
        f"memory_hook: {card.memory_hook or '(none)'}",
        f"synonyms:    {', '.join(card.synonyms) or '(none)'}",
        f"antonyms:    {', '.join(card.antonyms) or '(none)'}",
        f"right_use:   {card.right_use.sentence or '(none)'}",
        f"wrong_use:   {card.wrong_use.sentence or '(none)'}" + (f"  (why: {card.wrong_use.why})" if card.wrong_use.why else ""),
        f"image_scene: {card.image_scene or '(none)'}",
        f"emoji_scene: {card.emoji_scene}",
    ]
    return "\n".join(lines)


def format_questions(questions: list[Question]) -> str:
    lines = [f"questions kept (verified): {len(questions)}"]
    for q in questions:
        lines.append(f"  [{q.type} t{q.tier}] {q.prompt}")
        if q.type == "spell_it":
            lines.append(f"      accepted: {', '.join(q.accepted_answers)}")
        else:
            for i, choice in enumerate(q.choices):
                mark = "  ✓" if i == q.answer_index else ""
                lines.append(f"      {LETTERS[i] if i < len(LETTERS) else i}) {choice}{mark}")
        if q.explanation:
            lines.append(f"      why: {q.explanation}")
    return "\n".join(lines)


def format_usage(calls: list[dict], words: int) -> str:
    lines = ["", "=== token usage ==="]
    for call in calls:
        usage = call["usage"]
        lines.append(
            f"  {call['name']:<15} prompt {_num(usage.get('prompt_tokens')):>6}  "
            f"completion {_num(usage.get('completion_tokens')):>6}  finish {call['finish_reason']}"
        )
    total = summarize_usage(calls)
    lines.append(
        f"  total: {total['calls']} calls, {total['prompt_tokens']} prompt + {total['completion_tokens']} completion "
        f"= {total['total_tokens']} tokens"
    )
    if words:
        lines.append(f"  average completion tokens per word: {total['completion_tokens'] // words}")
    return "\n".join(lines)


def pool_shortfall(questions: list[Question]) -> str:
    """Empty string when the verified questions meet the minimum pool the real worker requires, else the reason."""
    if pool_meets_minimum(questions):
        return ""
    verified = [q for q in questions if q.verified]
    tier1 = sum(1 for q in verified if q.tier == 1)
    tier2 = sum(1 for q in verified if q.tier == 2)
    return (
        f"only {len(verified)} verified questions (need {MIN_POOL} incl. {MIN_TIER1} tier-1 and {MIN_TIER2} tier-2); "
        f"got {tier1} tier-1 and {tier2} tier-2"
    )


async def run_words(generator: Any, words: list[str], band: str, out: TextIO | None = None) -> list[dict]:
    """Generate a card and an initial question batch per word; print them; never stop at a failed word.

    A word whose verified questions fall below the worker's minimum pool counts as failed (the real worker
    would fail it with PoolShortfall), after its card and the questions that were kept are printed."""
    out = out or sys.stdout
    results: list[dict] = []
    for word in words:
        try:
            card = await generator.make_card(word, band)
            mix = initial_mix(card)
            questions = await generator.make_questions(word, band, card, mix, [], 1)
        except LLMError as exc:
            error = redact(str(exc))
            print(f"\n=== {word} ({band}) FAILED: {type(exc).__name__}: {error}", file=out)
            results.append({"word": word, "ok": False, "questions": 0, "error": error})
            continue
        verified = [q for q in questions if q.verified]
        print(format_card(word, band, card), file=out)
        print(f"requested mix: {json.dumps(mix)}", file=out)
        print(format_questions(verified), file=out)
        shortfall = pool_shortfall(verified)
        if shortfall:
            print(f"\n=== {word} ({band}) FAILED: PoolShortfall: {shortfall}", file=out)
            results.append({"word": word, "ok": False, "questions": len(verified), "error": shortfall})
            continue
        results.append({"word": word, "ok": True, "questions": len(verified), "error": ""})
    return results


async def _amain(settings: Settings, words: list[str], band: str, out: TextIO) -> int:
    logs = settings.data_dir / "logs"
    rejections = logs / "ai-rejections.jsonl"
    client = CerebrasClient(
        api_key=settings.cerebras_api_key,
        model=settings.cerebras_model,
        base_url=settings.cerebras_base_url,
        max_completion_tokens=settings.llm_max_completion_tokens,
        timeout_s=settings.llm_timeout_s,
    )
    recorder = UsageRecorder(client)
    generator = ContentGenerator(
        recorder,
        model_name=client.model,
        rejection_log=JsonlLog(rejections),
        usage_log=JsonlLog(logs / "ai-usage.jsonl"),
    )
    print(f"Model {client.model} · band {band} · words: {', '.join(words)}", file=out)
    try:
        results = await run_words(generator, words, band, out)
    finally:
        await client.aclose()
    print(format_usage(recorder.calls, len(words)), file=out)
    failed = [r["word"] for r in results if not r["ok"]]
    if failed:
        print(f"\nFAILED: {', '.join(failed)} — see {rejections} for the raw model output.", file=out)
        return 2
    print("\nOK: every word produced a card and verified questions.", file=out)
    return 0


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate WordQuest content for a few words against the real Cerebras API.")
    parser.add_argument("words", nargs="*", help=f"words or phrases to generate (default: {', '.join(DEFAULT_WORDS)})")
    parser.add_argument("--band", choices=list(BANDS), default=DEFAULT_BAND, help="grade band (default: 6-8)")
    args = parser.parse_args(argv)
    out = out or sys.stdout
    settings = Settings()
    settings.cerebras_api_key = settings.cerebras_api_key.strip()  # a blank or padded key must not reach the API as a 401
    if not settings.cerebras_api_key:
        print(NO_KEY_MESSAGE, file=sys.stderr)
        return 1
    register_secrets(settings.secret_values())
    settings.ensure_dirs()
    words = [w.strip().lower() for w in args.words if w.strip()] or list(DEFAULT_WORDS)
    return asyncio.run(_amain(settings, words, args.band, out))


if __name__ == "__main__":
    sys.exit(main())
