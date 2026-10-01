from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.services.model_service import (
    create_model_profile,
    delete_model_profile,
    list_model_profiles,
    test_model_profile,
    update_model_profile,
)


router = APIRouter(prefix="/api/models", tags=["models"])


class ModelProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    provider: str
    base_url: str | None
    model_name: str | None
    api_key_env: str
    has_api_key: bool
    system_prompt: str | None
    supports_stream: bool
    supports_vision: bool
    supports_tools: bool

    @classmethod
    def from_profile(cls, profile: Any) -> "ModelProfileResponse":
        return cls(
            id=profile.id,
            name=profile.name,
            provider=profile.provider,
            base_url=profile.base_url,
            model_name=profile.model_name,
            api_key_env=profile.api_key_env,
            has_api_key=bool(profile.api_key_secret),
            system_prompt=profile.system_prompt,
            supports_stream=profile.supports_stream,
            supports_vision=profile.supports_vision,
            supports_tools=profile.supports_tools,
        )


class ModelProfileUpsertRequest(BaseModel):
    name: str
    base_url: str | None = None
    model_name: str | None = None
    api_key: str | None = None
    system_prompt: str | None = None
    supports_stream: bool = True
    supports_vision: bool = False
    supports_tools: bool = True


class ModelProfilePatchRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    api_key: str | None = None
    system_prompt: str | None = None
    supports_stream: bool | None = None
    supports_vision: bool | None = None
    supports_tools: bool | None = None


class ModelTestRequest(BaseModel):
    model_profile_id: str | None = None
    message: str = "Say OK in one short sentence."
    extra_params: dict[str, Any] | None = None


@router.get("", response_model=list[ModelProfileResponse])
async def get_models(db: Session = Depends(get_session)) -> list[ModelProfileResponse]:
    return [ModelProfileResponse.from_profile(profile) for profile in list_model_profiles(db)]


@router.post("", response_model=ModelProfileResponse)
async def create_model(
    request: ModelProfileUpsertRequest,
    db: Session = Depends(get_session),
) -> ModelProfileResponse:
    profile = create_model_profile(
        db,
        name=request.name,
        base_url=request.base_url,
        model_name=request.model_name,
        api_key=request.api_key,
        system_prompt=request.system_prompt,
        supports_stream=request.supports_stream,
        supports_vision=request.supports_vision,
        supports_tools=request.supports_tools,
    )
    return ModelProfileResponse.from_profile(profile)


@router.patch("/{model_profile_id}", response_model=ModelProfileResponse)
async def update_model(
    model_profile_id: str,
    request: ModelProfilePatchRequest,
    db: Session = Depends(get_session),
) -> ModelProfileResponse:
    profile = update_model_profile(
        db,
        model_profile_id,
        updates=request.model_dump(exclude_unset=True),
    )
    return ModelProfileResponse.from_profile(profile)


@router.delete("/{model_profile_id}")
async def delete_model(
    model_profile_id: str,
    db: Session = Depends(get_session),
) -> dict[str, bool]:
    delete_model_profile(db, model_profile_id)
    return {"ok": True}


@router.post("/test")
async def test_model(
    request: ModelTestRequest,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return await test_model_profile(
        db,
        model_profile_id=request.model_profile_id,
        message=request.message,
        extra_params=request.extra_params,
    )
