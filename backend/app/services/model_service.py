from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import ToolCall
from app.adapters.registry import get_adapter_for_profile
from app.core.errors import MODEL_NOT_CONFIGURED, MODEL_TOOL_CALLING_UNSUPPORTED, AppError
from app.db.models import ModelProfile


def list_model_profiles(db: Session) -> list[ModelProfile]:
    return list(db.scalars(select(ModelProfile).order_by(ModelProfile.created_at)).all())


def create_model_profile(
    db: Session,
    *,
    name: str,
    base_url: str | None,
    model_name: str | None,
    api_key: str | None,
    system_prompt: str | None,
    supports_stream: bool,
    supports_vision: bool,
    supports_tools: bool = True,
) -> ModelProfile:
    profile = ModelProfile(
        name=name.strip() or "OpenAI Compatible Model",
        provider="openai_compat",
        base_url=_clean_optional(base_url),
        model_name=_clean_optional(model_name),
        api_key_env="OPENAI_COMPAT_API_KEY",
        api_key_secret=_clean_optional(api_key),
        system_prompt=_clean_optional(system_prompt),
        supports_stream=supports_stream,
        supports_vision=supports_vision,
        supports_tools=supports_tools,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def update_model_profile(
    db: Session,
    profile_id: str,
    updates: dict[str, Any],
) -> ModelProfile:
    profile = get_model_profile(db, profile_id)
    if "name" in updates:
        profile.name = (updates["name"] or "").strip() or profile.name
    if "base_url" in updates:
        profile.base_url = _clean_optional(updates["base_url"])
    if "model_name" in updates:
        profile.model_name = _clean_optional(updates["model_name"])
    if "api_key" in updates and updates["api_key"] and updates["api_key"].strip():
        profile.api_key_secret = updates["api_key"].strip()
    if "system_prompt" in updates:
        profile.system_prompt = _clean_optional(updates["system_prompt"])
    if "supports_stream" in updates:
        profile.supports_stream = bool(updates["supports_stream"])
    if "supports_vision" in updates:
        profile.supports_vision = bool(updates["supports_vision"])
    if "supports_tools" in updates:
        profile.supports_tools = bool(updates["supports_tools"])

    db.commit()
    db.refresh(profile)
    return profile


def delete_model_profile(db: Session, profile_id: str) -> None:
    profile = get_model_profile(db, profile_id)
    count = len(list_model_profiles(db))
    if count <= 1:
        raise AppError(
            MODEL_NOT_CONFIGURED,
            "At least one model profile must remain.",
            status_code=400,
        )
    db.delete(profile)
    db.commit()


def get_model_profile(db: Session, model_profile_id: str | None = None) -> ModelProfile:
    if model_profile_id:
        profile = db.get(ModelProfile, model_profile_id)
    else:
        profile = db.get(ModelProfile, "default-openai-compatible")
        if profile is None:
            profile = db.scalar(select(ModelProfile).order_by(ModelProfile.created_at))

    if profile is None:
        raise AppError(
            MODEL_NOT_CONFIGURED,
            "No model profile is configured.",
            status_code=400,
        )
    return profile


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def system_prompt_messages(profile: ModelProfile) -> list[dict[str, str]]:
    system_prompt = (profile.system_prompt or "").strip()
    if not system_prompt:
        return []
    return [{"role": "system", "content": system_prompt}]


async def test_model_profile(
    db: Session,
    *,
    model_profile_id: str | None = None,
    message: str = "Say OK in one short sentence.",
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = get_model_profile(db, model_profile_id)
    if not profile.model_name:
        raise AppError(
            MODEL_NOT_CONFIGURED,
            "Model name is not configured. Set OPENAI_COMPAT_DEFAULT_MODEL.",
            status_code=400,
        )

    adapter = get_adapter_for_profile(profile)
    if profile.supports_tools:
        response = await adapter.chat(
            model=profile.model_name,
            messages=[
                *system_prompt_messages(profile),
                {
                    "role": "user",
                    "content": (
                        "Call the labai_capability_probe tool exactly once. "
                        "Do not answer with ordinary text."
                    ),
                },
            ],
            extra_params=extra_params,
            tools=[_tool_calling_probe_definition()],
            tool_choice={
                "type": "function",
                "function": {"name": "labai_capability_probe"},
            },
        )
        probe_call = next(
            (call for call in response.tool_calls if call.name == "labai_capability_probe"),
            None,
        )
        if probe_call is None:
            raise AppError(
                MODEL_TOOL_CALLING_UNSUPPORTED,
                "Model did not return the required tool call during capability testing.",
                status_code=400,
                details={
                    "model_profile_id": profile.id,
                    "finish_reason": response.finish_reason,
                },
            )
        tool_calling_supported = True
    else:
        response = await adapter.chat(
            model=profile.model_name,
            messages=[
                *system_prompt_messages(profile),
                {"role": "user", "content": message},
            ],
            extra_params=extra_params,
        )
        tool_calling_supported = False

    return {
        "ok": True,
        "model_profile_id": profile.id,
        "model_name": profile.model_name,
        "response": response.content or (
            "Tool calling probe succeeded." if tool_calling_supported else ""
        ),
        "usage": response.usage,
        "finish_reason": response.finish_reason,
        "tool_calling_supported": tool_calling_supported,
        "tool_calls": [_serialize_tool_call(call) for call in response.tool_calls],
    }


def _tool_calling_probe_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "labai_capability_probe",
            "description": "Confirm that this model can emit OpenAI-compatible tool calls.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }


def _serialize_tool_call(call: ToolCall) -> dict[str, Any]:
    return call.to_dict()
