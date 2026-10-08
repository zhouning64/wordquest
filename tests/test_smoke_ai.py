from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest

from app.ai.content import INITIAL_MIX
from app.ai.llm import InvalidOutput
from app.models import LearnCard, Question, RightUse, Sense, WrongUse, new_id
from tests.fakes import FakeLLM

ROOT = Path(__file__).resolve().parents[1]


def load_smoke():
    spec = importlib.util.spec_from_file_location("smoke_ai", ROOT / "scripts" / "smoke_ai.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_card() -> LearnCard:
    return LearnCard(
        pos="adjective",
        forms=["tenaciously", "tenacity"],
        short_def="holding on firmly; not giving up",
        kid_def="A tenacious person keeps trying and does not let go, even when something is hard.",
        senses=[Sense(pos="adjective", definition="not giving up", example="The tenacious goalie blocked every shot.")],
        examples=[
            "Our tenacious team practiced every single day.",
            "The tenacious puppy held on to the rope.",
            "She was tenacious about finishing the puzzle.",
            "A tenacious climber reached the top at last.",
        ],
        word_parts="ten- (to hold)",
        memory_hook="TEN fingers holding on tight",
        synonyms=["persistent", "determined"],
        antonyms=["quitting"],
        right_use=RightUse(sentence="The tenacious student kept asking until she understood."),
        wrong_use=WrongUse(sentence="The tenacious ice cream melted.", why="Ice cream cannot keep trying."),
        image_scene="a puppy tugging a rope with all its strength",
        emoji_scene="🐶🪢💪",
    )


def make_question(qtype: str, tier: int, **fields) -> Question:
    data = {"id": new_id(), "word": "tenacious", "band": "6-8", "content_version": 1, "type": qtype, "tier": tier, "verified": True}
    data.update(fields)
    return Question(**data)


class StubGenerator:
    def __init__(self, fail_words: set[str]) -> None:
        self.fail_words = fail_words
        self.question_calls: list[tuple] = []

    async def make_card(self, word: str, band: str) -> LearnCard:
        if word in self.fail_words:
            raise InvalidOutput("learn card rejected: L2 fewer than 4 examples")
        return make_card()

    async def make_questions(self, word, band, card, mix, existing, version):
        self.question_calls.append((word, band, dict(mix), list(existing), version))
        return [
            make_question(
                "meaning",
                1,
                prompt="What does tenacious mean?",
                choices=["very sleepy", "not giving up", "easily scared", "quite small"],
                answer_index=1,
                explanation="Tenacious means you keep going.",
            ),
            make_question(
                "spell_it",
                3,
                prompt="The ___ goalie never quit. (means: not giving up)",
                answer_index=-1,
                accepted_answers=["tenacious"],
                explanation="Spelled t-e-n-a-c-i-o-u-s.",
            ),
        ]


def test_main_without_key_exits_1_with_a_clear_message(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    smoke = load_smoke()
    assert smoke.main([]) == 1
    err = capsys.readouterr().err
    assert "CEREBRAS_API_KEY is not set" in err
    assert ".env" in err
    assert not (tmp_path / "data").exists()  # nothing created before the key check


def test_main_rejects_an_unknown_band(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    smoke = load_smoke()
    with pytest.raises(SystemExit) as exc:
        smoke.main(["--band", "1-2"])
    assert exc.value.code == 2


async def test_usage_recorder_passes_results_through_and_records_usage():
    smoke = load_smoke()
    recorder = smoke.UsageRecorder(FakeLLM({"learn_card": [{"pos": "adjective"}]}))
    result = await recorder.chat_json(name="learn_card", schema={}, system="sys", user="usr")
    assert result.data == {"pos": "adjective"}
    assert recorder.calls == [{"name": "learn_card", "usage": {"completion_tokens": 10}, "finish_reason": "stop"}]


def test_summarize_usage_totals_calls_and_fills_missing_totals():
    smoke = load_smoke()
    calls = [
        {"name": "learn_card", "usage": {"prompt_tokens": 100, "completion_tokens": 900, "total_tokens": 1000}, "finish_reason": "stop"},
        {"name": "question_batch", "usage": {"prompt_tokens": 50, "completion_tokens": 2000}, "finish_reason": "stop"},
        {"name": "answer_check", "usage": {}, "finish_reason": "stop"},
    ]
    assert smoke.summarize_usage(calls) == {"calls": 3, "prompt_tokens": 150, "completion_tokens": 2900, "total_tokens": 3050}
    report = smoke.format_usage(calls, 2)
    assert "total: 3 calls, 150 prompt + 2900 completion = 3050 tokens" in report
    assert "average completion tokens per word: 1450" in report


async def test_run_words_prints_cards_and_questions_and_continues_after_a_failure():
    smoke = load_smoke()
    stub = StubGenerator(fail_words={"in lieu of"})
    out = io.StringIO()
    results = await smoke.run_words(stub, ["tenacious", "in lieu of"], "6-8", out)
    text = out.getvalue()
    assert "=== tenacious (6-8) — adjective ===" in text
    assert "short_def:   holding on firmly; not giving up" in text
    assert "questions kept (verified): 2" in text
    assert "[meaning t1] What does tenacious mean?" in text
    assert "B) not giving up  ✓" in text
    assert "A) very sleepy\n" in text
    assert "accepted: tenacious" in text
    assert "=== in lieu of (6-8) FAILED: InvalidOutput: learn card rejected: L2 fewer than 4 examples" in text
    assert results == [
        {"word": "tenacious", "ok": True, "questions": 2, "error": ""},
        {"word": "in lieu of", "ok": False, "questions": 0, "error": "learn card rejected: L2 fewer than 4 examples"},
    ]
    # the initial batch is requested with the spec §7.3 mix, no existing pool, version 1
    assert stub.question_calls == [("tenacious", "6-8", dict(INITIAL_MIX), [], 1)]


def test_format_card_marks_empty_optional_fields():
    smoke = load_smoke()
    card = make_card().model_copy(
        update={"word_parts": "", "memory_hook": "", "antonyms": [], "right_use": RightUse(), "wrong_use": WrongUse()}
    )
    text = smoke.format_card("tenacious", "6-8", card)
    assert "word_parts:  (none)" in text
    assert "memory_hook: (none)" in text
    assert "antonyms:    (none)" in text
    assert "right_use:   (none)" in text
    assert "wrong_use:   (none)" in text
    assert "(why:" not in text
