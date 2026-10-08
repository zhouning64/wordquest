"""Settings loaded from the environment / .env (spec §5)."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cerebras_api_key: str = ""
    cerebras_model: str = "gpt-oss-120b"
    cerebras_base_url: str = "https://api.cerebras.ai/v1"
    llm_max_completion_tokens: int = 16000
    llm_timeout_s: float = 60.0
    image_provider: str = "none"  # "none" | "openai_compatible" (chosen provider: z.ai)
    image_api_key: str = ""
    image_model: str = ""  # z.ai: "glm-image" or "cogview-4-250304"
    image_base_url: str = ""  # required for openai_compatible (no default); z.ai: https://api.z.ai/api/paas/v4
    image_size: str = "1024x1024"  # sent as "size"; valid for both z.ai models
    image_quality: str = ""  # sent as "quality" only when non-empty ("standard" | "hd" on z.ai)
    site_access_code: str = ""
    parent_passcode: str = ""
    secret_key: str = ""
    data_dir: Path = Path("./data")
    storage: str = "local"
    gen_concurrency: int = 3
    ai_daily_call_limit: int = 2000

    @property
    def ai_enabled(self) -> bool:
        return bool(self.cerebras_api_key)

    def secret_values(self) -> list[str]:
        """Every configured secret (non-empty only), for redaction."""
        values = [
            self.cerebras_api_key,
            self.image_api_key,
            self.site_access_code,
            self.parent_passcode,
            self.secret_key,
        ]
        return [v for v in values if v]

    def resolved_secret_key(self) -> str:
        """SECRET_KEY if set; otherwise read (or create once) DATA_DIR/secret_key holding 32 random bytes as hex."""
        if self.secret_key:
            return self.secret_key
        path = self.data_dir / "secret_key"
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        self.data_dir.mkdir(parents=True, exist_ok=True)
        key = secrets.token_hex(32)
        path.write_text(key + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
        return key

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.data_dir / "images", self.data_dir / "logs"):
            d.mkdir(parents=True, exist_ok=True)
