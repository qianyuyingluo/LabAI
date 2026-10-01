import os

from app.adapters.base import ModelAdapter
from app.adapters.openai_compat import OpenAICompatAdapter
from app.core.config import get_settings
from app.core.errors import MODEL_NOT_CONFIGURED, AppError
from app.db.models import ModelProfile


def get_adapter_for_profile(profile: ModelProfile) -> ModelAdapter:
    if profile.provider != "openai_compat":
        raise AppError(
            MODEL_NOT_CONFIGURED,
            f"Provider '{profile.provider}' is not supported by this backend version.",
            status_code=400,
        )

    settings = get_settings()
    api_key = profile.api_key_secret or os.getenv(profile.api_key_env)
    if not api_key and profile.api_key_env == "OPENAI_COMPAT_API_KEY":
        api_key = settings.openai_compat_api_key

    return OpenAICompatAdapter(
        api_key=api_key,
        base_url=profile.base_url,
        supports_tools=getattr(profile, "supports_tools", True) is not False,
    )
