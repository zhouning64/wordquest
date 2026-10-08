from __future__ import annotations

from typing import Callable

from app.ai.images.base import ImageProvider
from app.ai.images.openai_compatible import OpenAICompatibleImageProvider
from app.config import Settings

# Required for openai_compatible, checked in this order. There is deliberately no default base URL: an implicit
# https://api.openai.com/v1 would send a z.ai key to OpenAI when IMAGE_BASE_URL is forgotten.
REQUIRED_SETTINGS: tuple[tuple[str, str], ...] = (
    ("image_base_url", "IMAGE_BASE_URL is required when IMAGE_PROVIDER=openai_compatible "
                       "(for z.ai use https://api.z.ai/api/paas/v4)"),
    ("image_api_key", "IMAGE_API_KEY is required when IMAGE_PROVIDER=openai_compatible "
                      "(create a z.ai key at https://z.ai/manage-apikey/apikey-list and put it in .env)"),
    ("image_model", "IMAGE_MODEL is required when IMAGE_PROVIDER=openai_compatible "
                    "(for z.ai use glm-image or cogview-4-250304)"),
)


def make_image_provider(
    settings: Settings, on_request: Callable[[], None] | None = None
) -> ImageProvider | None:
    """The configured provider, or None for pictures off. Bad settings raise ValueError with a message meant for
    the parent (create_app logs it, runs with pictures off and shows it in Parent → Status & backup)."""
    kind = (settings.image_provider or "").strip().lower() or "none"  # empty or whitespace-only = none
    if kind == "none":
        return None
    if kind == "openai_compatible":
        missing = [message for field, message in REQUIRED_SETTINGS if not getattr(settings, field).strip()]
        if missing:
            raise ValueError("; ".join(missing))
        return OpenAICompatibleImageProvider(
            api_key=settings.image_api_key,
            model=settings.image_model.strip(),
            base_url=settings.image_base_url.strip(),
            size=settings.image_size,
            quality=settings.image_quality,
            on_request=on_request,
        )
    raise ValueError(f"Unknown IMAGE_PROVIDER {settings.image_provider!r} (use 'none' or 'openai_compatible')")
