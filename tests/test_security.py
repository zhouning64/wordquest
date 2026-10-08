from __future__ import annotations

import json
from pathlib import Path

from app.logs import JsonlLog
from app.security import REDACTED, redact, register_secrets, safe_equal


def test_redact_is_noop_when_nothing_registered() -> None:
    assert redact("Authorization: Bearer csk-live-123456") == "Authorization: Bearer csk-live-123456"
    assert redact("") == ""


def test_redact_replaces_every_registered_secret_everywhere() -> None:
    register_secrets(["csk-sentinel-9f8e7d", "parent-pass-4321"])
    text = "key=csk-sentinel-9f8e7d url=/v1?k=csk-sentinel-9f8e7d code parent-pass-4321"
    assert redact(text) == f"key={REDACTED} url=/v1?k={REDACTED} code {REDACTED}"


def test_register_secrets_accumulates_across_calls() -> None:
    register_secrets(["first-secret"])
    register_secrets(["second-secret"])
    assert redact("first-secret / second-secret") == f"{REDACTED} / {REDACTED}"


def test_short_and_empty_values_are_ignored() -> None:
    register_secrets(["", "abc", "xy"])
    assert redact("abc xy abcdef") == "abc xy abcdef"


def test_longer_secret_wins_over_contained_shorter_one() -> None:
    register_secrets(["abcd", "abcd1234"])
    assert redact("token abcd1234 and abcd") == f"token {REDACTED} and {REDACTED}"


def test_url_encoded_form_of_a_secret_is_redacted() -> None:
    register_secrets(["k3y+with/slash="])
    assert redact("GET /v1?key=k3y%2Bwith%2Fslash%3D") == f"GET /v1?key={REDACTED}"


def test_safe_equal() -> None:
    assert safe_equal("open-sesame", "open-sesame") is True
    assert safe_equal("open-sesame", "open-sesamE") is False
    assert safe_equal("short", "much-longer-value") is False
    assert safe_equal("", "") is True
    assert safe_equal("café-🔑", "café-🔑") is True


def test_jsonl_log_writes_one_redacted_json_line_per_record(tmp_path: Path) -> None:
    register_secrets(["csk-sentinel-9f8e7d"])
    log = JsonlLog(tmp_path / "logs" / "nested" / "ai-usage.jsonl")
    log.write({"event": "call", "error": "401 for key csk-sentinel-9f8e7d", "tokens": 12})
    log.write({"event": "call", "word": "café", "emoji": "📖✨"})
    raw = (tmp_path / "logs" / "nested" / "ai-usage.jsonl").read_text(encoding="utf-8")
    lines = raw.splitlines()
    assert len(lines) == 2
    assert raw.endswith("\n")
    assert "csk-sentinel-9f8e7d" not in raw
    assert json.loads(lines[0]) == {"event": "call", "error": f"401 for key {REDACTED}", "tokens": 12}
    assert json.loads(lines[1]) == {"event": "call", "word": "café", "emoji": "📖✨"}
    assert "café" in lines[1]  # ensure_ascii=False keeps text readable


def test_jsonl_log_appends_to_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    JsonlLog(path).write({"n": 1})
    JsonlLog(path).write({"n": 2})
    assert [json.loads(line)["n"] for line in path.read_text(encoding="utf-8").splitlines()] == [1, 2]
