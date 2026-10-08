from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET_FIELDS = {"cerebras_api_key", "image_api_key", "site_access_code", "parent_passcode", "secret_key"}


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.cerebras_api_key == ""
    assert s.cerebras_model == "gpt-oss-120b"
    assert s.cerebras_base_url == "https://api.cerebras.ai/v1"
    assert s.llm_max_completion_tokens == 16000
    assert s.llm_timeout_s == 60.0
    assert s.image_provider == "none"
    assert s.image_api_key == "" and s.image_model == "" and s.image_base_url == ""
    assert s.image_size == "1024x1024" and s.image_quality == ""
    assert s.site_access_code == "" and s.parent_passcode == "" and s.secret_key == ""
    assert s.data_dir == Path("data")
    assert s.storage == "local"
    assert s.gen_concurrency == 3
    assert s.ai_daily_call_limit == 2000
    assert s.ai_enabled is False


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-test-123456")
    monkeypatch.setenv("CEREBRAS_MODEL", "llama-test")
    monkeypatch.setenv("GEN_CONCURRENCY", "5")
    monkeypatch.setenv("LLM_TIMEOUT_S", "12.5")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("IMAGE_PROVIDER", "openai_compatible")
    monkeypatch.setenv("IMAGE_SIZE", "1280x1280")
    monkeypatch.setenv("IMAGE_QUALITY", "hd")
    s = Settings(_env_file=None)
    assert s.cerebras_api_key == "csk-test-123456"
    assert s.cerebras_model == "llama-test"
    assert s.gen_concurrency == 5
    assert s.llm_timeout_s == 12.5
    assert s.data_dir == tmp_path / "elsewhere"
    assert s.image_provider == "openai_compatible"
    assert s.image_size == "1280x1280" and s.image_quality == "hd"
    assert s.ai_enabled is True


def test_env_file_is_read_and_unknown_keys_are_ignored(tmp_path: Path) -> None:
    env_file = tmp_path / "test.env"
    env_file.write_text("PARENT_PASSCODE=pp-from-file\nAI_DAILY_CALL_LIMIT=7\nSOMETHING_UNRELATED=1\n", encoding="utf-8")
    s = Settings(_env_file=env_file)
    assert s.parent_passcode == "pp-from-file"
    assert s.ai_daily_call_limit == 7


def test_settings_fixture_points_at_tmp_dir(settings: Settings, tmp_path: Path) -> None:
    assert settings.data_dir == tmp_path / "data"
    assert not settings.data_dir.exists()  # no side effects until ensure_dirs()/resolved_secret_key()


def test_secret_values_excludes_empty_and_non_secret_fields() -> None:
    s = Settings(_env_file=None, cerebras_api_key="csk-aaaa1111", parent_passcode="pp-2222", cerebras_model="m-3333")
    assert s.secret_values() == ["csk-aaaa1111", "pp-2222"]
    assert Settings(_env_file=None).secret_values() == []


def test_secret_values_lists_all_five_in_order() -> None:
    s = Settings(
        _env_file=None,
        cerebras_api_key="k1-xxxx",
        image_api_key="k2-xxxx",
        site_access_code="k3-xxxx",
        parent_passcode="k4-xxxx",
        secret_key="k5-xxxx",
    )
    assert s.secret_values() == ["k1-xxxx", "k2-xxxx", "k3-xxxx", "k4-xxxx", "k5-xxxx"]


def test_resolved_secret_key_uses_explicit_value_without_touching_disk(settings: Settings) -> None:
    s = settings.model_copy(update={"secret_key": "explicit-secret-key"})
    assert s.resolved_secret_key() == "explicit-secret-key"
    assert not (settings.data_dir / "secret_key").exists()


def test_resolved_secret_key_is_created_once_and_stable(settings: Settings) -> None:
    first = settings.resolved_secret_key()
    path = settings.data_dir / "secret_key"
    assert path.is_file()
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert settings.resolved_secret_key() == first
    again = Settings(_env_file=None, data_dir=settings.data_dir)
    assert again.resolved_secret_key() == first
    assert path.read_text(encoding="utf-8").strip() == first


def test_ensure_dirs_creates_data_images_and_logs(settings: Settings) -> None:
    settings.ensure_dirs()
    assert (settings.data_dir).is_dir()
    assert (settings.data_dir / "images").is_dir()
    assert (settings.data_dir / "logs").is_dir()
    settings.ensure_dirs()  # idempotent


def test_env_example_documents_every_setting_without_real_secrets() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    entries = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            entries[key.strip()] = value.strip()
    assert set(entries) == {name.upper() for name in Settings.model_fields}
    for field in SECRET_FIELDS:
        assert entries[field.upper()] == "", f"{field.upper()} must be empty in .env.example"


def test_env_example_loads_as_defaults() -> None:
    s = Settings(_env_file=REPO_ROOT / ".env.example")
    assert s.model_dump() == Settings(_env_file=None).model_dump()
