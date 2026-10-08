from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest

from app.ai.content import INITIAL_MIX
from app.ai.llm import InvalidOutput
from app.models import LearnCard, Question, RightUse, Sense, WrongUse, new_id
from app.security import register_secrets
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


FAIL_MESSAGE = "learn card rejected: L2 fewer than 4 examples"


def first_two_questions() -> list[Question]:
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


def choice_question(qtype: str, tier: int, prompt: str, **fields) -> Question:
    return make_question(
        qtype,
        tier,
        prompt=prompt,
        choices=["tenacious", "very sleepy", "easily scared", "quite small"],
        answer_index=0,
        explanation="Tenacious means you keep going.",
        **fields,
    )


def full_pool() -> list[Question]:
    """The two questions above plus four more: 6 verified, 3 tier-1, 2 tier-2 (meets the worker's minimum)."""
    return first_two_questions() + [
        choice_question("pick_word", 1, 'Which word means "not giving up"?'),
        choice_question("fill_blank", 1, "The ___ goalie blocked every shot."),
        choice_question("synonym", 2, 'Which word is closest in meaning to "persistent"?'),
        choice_question("usage", 2, "Which sentence uses tenacious correctly?"),
    ]


class StubGenerator:
    """Stands in for ContentGenerator. `pools` maps a word to the questions make_questions returns for it;
    any other word gets the full pool."""

    def __init__(self, fail_words: set[str], pools: dict[str, list[Question]] | None = None, fail_message: str = FAIL_MESSAGE) -> None:
        self.fail_words = fail_words
        self.pools = pools or {}
        self.fail_message = fail_message
        self.question_calls: list[tuple] = []

    async def make_card(self, word: str, band: str) -> LearnCard:
        if word in self.fail_words:
            raise InvalidOutput(self.fail_message)
        return make_card()

    async def make_questions(self, word, band, card, mix, existing, version):
        self.question_calls.append((word, band, dict(mix), list(existing), version))
        return list(self.pools[word]) if word in self.pools else full_pool()


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


async def test_usage_recorder_forwards_reasoning_effort():
    smoke = load_smoke()
    inner = FakeLLM({"answer_check": [{"results": []}]})
    await smoke.UsageRecorder(inner).chat_json(name="answer_check", schema={}, system="s", user="u",
                                               reasoning_effort="high")
    assert inner.calls[0]["reasoning_effort"] == "high"


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
    assert "questions kept (verified): 6" in text
    assert "[meaning t1] What does tenacious mean?" in text
    assert "B) not giving up  ✓" in text
    assert "A) very sleepy\n" in text
    assert "accepted: tenacious" in text
    assert "=== in lieu of (6-8) FAILED: InvalidOutput: learn card rejected: L2 fewer than 4 examples" in text
    assert results == [
        {"word": "tenacious", "ok": True, "questions": 6, "error": ""},
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


NEED = "need 6 incl. 2 tier-1 and 1 tier-2"


async def test_run_words_fails_a_word_below_the_minimum_pool_like_the_worker_does():
    smoke = load_smoke()
    unchecked = [q.model_copy(update={"verified": False}) for q in full_pool()]
    only_tier1 = [choice_question("pick_word", 1, f"Which word is number {i}?") for i in range(6)]
    stub = StubGenerator(
        fail_words=set(),
        pools={"few": first_two_questions(), "empty": [], "unchecked": unchecked, "no tier 2": only_tier1},
    )
    out = io.StringIO()
    results = await smoke.run_words(stub, ["tenacious", "few", "empty", "unchecked", "no tier 2"], "6-8", out)
    text = out.getvalue()
    assert results == [
        {"word": "tenacious", "ok": True, "questions": 6, "error": ""},
        {"word": "few", "ok": False, "questions": 2, "error": f"only 2 verified questions ({NEED}); got 1 tier-1 and 0 tier-2"},
        {"word": "empty", "ok": False, "questions": 0, "error": f"only 0 verified questions ({NEED}); got 0 tier-1 and 0 tier-2"},
        {"word": "unchecked", "ok": False, "questions": 0, "error": f"only 0 verified questions ({NEED}); got 0 tier-1 and 0 tier-2"},
        {"word": "no tier 2", "ok": False, "questions": 6, "error": f"only 6 verified questions ({NEED}); got 6 tier-1 and 0 tier-2"},
    ]
    assert f"=== few (6-8) FAILED: PoolShortfall: only 2 verified questions ({NEED}); got 1 tier-1 and 0 tier-2" in text
    assert f"=== empty (6-8) FAILED: PoolShortfall: only 0 verified questions ({NEED})" in text
    assert "=== tenacious (6-8) FAILED" not in text
    # the card and the questions that were kept are still printed for a short word, to help diagnose it
    assert "=== few (6-8) — adjective ===" in text
    assert len(stub.question_calls) == 5  # a short word never stops the run


async def test_run_words_redacts_a_registered_secret_in_the_failed_line():
    smoke = load_smoke()
    register_secrets(["csk-sentinel-5f3a9c"])
    stub = StubGenerator(fail_words={"tenacious"}, fail_message="HTTP 401 for key csk-sentinel-5f3a9c")
    out = io.StringIO()
    results = await smoke.run_words(stub, ["tenacious"], "6-8", out)
    text = out.getvalue()
    assert "csk-sentinel-5f3a9c" not in text
    assert "=== tenacious (6-8) FAILED: InvalidOutput: HTTP 401 for key [REDACTED]" in text
    assert results == [{"word": "tenacious", "ok": False, "questions": 0, "error": "HTTP 401 for key [REDACTED]"}]


class FakeClient:
    """Replaces CerebrasClient in main() tests: never touches the network."""

    created: list[dict] = []
    model = "fake-model"

    def __init__(self, **kwargs) -> None:
        FakeClient.created.append(kwargs)

    async def aclose(self) -> None:
        pass


generator_kwargs: list[dict] = []  # the keyword arguments main() built the ContentGenerator with


def run_main(smoke, monkeypatch, tmp_path, stub, argv, key="csk-test-key-1234"):
    FakeClient.created = []
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("CEREBRAS_API_KEY", key)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "wq-store"))
    monkeypatch.setattr(smoke, "CerebrasClient", FakeClient)
    generator_kwargs.clear()
    monkeypatch.setattr(smoke, "ContentGenerator", lambda *args, **kwargs: generator_kwargs.append(kwargs) or stub)
    out = io.StringIO()
    return smoke.main(argv, out), out.getvalue()


def test_main_exits_0_when_every_word_has_a_full_pool(tmp_path, monkeypatch):
    smoke = load_smoke()
    code, text = run_main(smoke, monkeypatch, tmp_path, StubGenerator(fail_words=set()), ["tenacious"])
    assert code == 0
    assert "OK: every word produced a card and verified questions." in text


def test_main_exits_2_for_a_short_word_and_names_the_real_rejections_log(tmp_path, monkeypatch):
    smoke = load_smoke()
    stub = StubGenerator(fail_words=set(), pools={"tenacious": first_two_questions()})
    code, text = run_main(smoke, monkeypatch, tmp_path, stub, ["tenacious"])
    assert code == 2
    assert "OK: every word" not in text
    assert "FAILED: tenacious" in text
    assert str(tmp_path / "wq-store" / "logs" / "ai-rejections.jsonl") in text  # DATA_DIR is honoured, not a fixed "data/logs"


@pytest.mark.parametrize("blank", ["", "   ", "\t \n"])
def test_main_treats_a_blank_key_as_missing(tmp_path, monkeypatch, capsys, blank):
    smoke = load_smoke()
    code, text = run_main(smoke, monkeypatch, tmp_path, StubGenerator(fail_words=set()), ["tenacious"], key=blank)
    assert code == 1
    assert text == ""
    assert "CEREBRAS_API_KEY is not set" in capsys.readouterr().err
    assert FakeClient.created == []
    assert not (tmp_path / "wq-store").exists()


def test_main_strips_whitespace_around_the_key_before_using_it(tmp_path, monkeypatch):
    smoke = load_smoke()
    code, _ = run_main(smoke, monkeypatch, tmp_path, StubGenerator(fail_words=set()), ["tenacious"], key="  csk-test-key-1234 \n")
    assert code == 0
    assert [c["api_key"] for c in FakeClient.created] == ["csk-test-key-1234"]


def test_main_passes_reasoning_effort_to_the_generator_and_shows_it_in_the_header(tmp_path, monkeypatch):
    smoke = load_smoke()
    code, text = run_main(smoke, monkeypatch, tmp_path, StubGenerator(fail_words=set()),
                          ["--reasoning-effort", "medium", "tenacious"])
    assert code == 0
    assert generator_kwargs[0]["generation_reasoning_effort"] == "medium"
    assert text.splitlines()[0] == "Model fake-model · band 6-8 · reasoning medium · words: tenacious"


def test_main_without_the_flag_uses_the_configured_reasoning_effort(tmp_path, monkeypatch):
    smoke = load_smoke()
    code, text = run_main(smoke, monkeypatch, tmp_path, StubGenerator(fail_words=set()), ["tenacious"])
    assert code == 0
    assert generator_kwargs[0]["generation_reasoning_effort"] == "medium"  # the LLM_REASONING_EFFORT default
    assert text.splitlines()[0] == "Model fake-model · band 6-8 · reasoning medium · words: tenacious"


def test_main_with_an_empty_configured_effort_passes_none_and_says_model_default(tmp_path, monkeypatch):
    smoke = load_smoke()
    monkeypatch.setenv("LLM_REASONING_EFFORT", "")
    code, text = run_main(smoke, monkeypatch, tmp_path, StubGenerator(fail_words=set()), ["tenacious"])
    assert code == 0
    assert generator_kwargs[0]["generation_reasoning_effort"] is None
    assert text.splitlines()[0] == "Model fake-model · band 6-8 · reasoning model default · words: tenacious"


def test_the_flag_overrides_the_configured_reasoning_effort(tmp_path, monkeypatch):
    smoke = load_smoke()
    monkeypatch.setenv("LLM_REASONING_EFFORT", "low")
    code, text = run_main(smoke, monkeypatch, tmp_path, StubGenerator(fail_words=set()),
                          ["--reasoning-effort", "high", "tenacious"])
    assert code == 0
    assert generator_kwargs[0]["generation_reasoning_effort"] == "high"
    assert text.splitlines()[0] == "Model fake-model · band 6-8 · reasoning high · words: tenacious"


def test_main_rejects_an_unknown_reasoning_effort(tmp_path, monkeypatch):
    smoke = load_smoke()
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        smoke.main(["--reasoning-effort", "extreme"])
    assert exc.value.code == 2
