import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.registry import get_adapter_for_profile
from app.core.errors import MODEL_CAPABILITY_MISMATCH, AppError
from app.db.models import AnalysisPlan, AnalysisTask, ModelProfile
from app.services.file_service import get_uploaded_image
from app.services.message_builder import MessageBuilder
from app.services.output_format import OUTPUT_FORMAT_SYSTEM_PROMPT
from app.services.prompt_service import PromptService
from app.skills.plan_skill.schemas import PlanSkillPromptBundle


PLAN_SKILL_NAME = "plan_skill"
PLAN_GENERATE_PROMPT_KEY = "plan/plan"
PLAN_REVISE_PROMPT_KEY = "plan/revise_plan"


class PlanSkillService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.prompt_service = PromptService(db)

    def load_generation_prompt_bundle(self) -> PlanSkillPromptBundle:
        return self._load_prompt_bundle(PLAN_GENERATE_PROMPT_KEY)

    def load_revision_prompt_bundle(self) -> PlanSkillPromptBundle:
        return self._load_prompt_bundle(PLAN_REVISE_PROMPT_KEY)

    async def stream_generate_plan(
        self,
        *,
        profile: ModelProfile,
        prompt_bundle: PlanSkillPromptBundle,
        task: AnalysisTask,
        user_message: str,
        file_summaries: list[str],
        image_infos: list[dict[str, Any]],
        image_file_ids: list[str],
        extra_params: dict[str, Any] | None,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        user_text = self.build_generation_context(
            user_message=user_message,
            file_summaries=file_summaries,
            image_infos=image_infos,
            profile=profile,
            task=task,
        )
        async for event in self._iter_model_events(
            profile=profile,
            prompt_bundle=prompt_bundle,
            user_text=user_text,
            image_file_ids=image_file_ids,
            extra_params=extra_params,
            delta_event="plan_delta",
            is_disconnected=is_disconnected,
        ):
            yield event

    async def stream_revise_plan(
        self,
        *,
        profile: ModelProfile,
        prompt_bundle: PlanSkillPromptBundle,
        task: AnalysisTask,
        previous_plan: AnalysisPlan,
        user_feedback: str,
        extra_params: dict[str, Any] | None,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        user_text = self.build_revision_context(
            task=task,
            previous_plan=previous_plan,
            user_feedback=user_feedback,
            profile=profile,
        )
        async for event in self._iter_model_events(
            profile=profile,
            prompt_bundle=prompt_bundle,
            user_text=user_text,
            image_file_ids=[],
            extra_params=extra_params,
            delta_event="plan_delta",
            is_disconnected=is_disconnected,
        ):
            yield event

    def load_image_infos(
        self,
        image_file_ids: list[str],
        profile: ModelProfile,
    ) -> list[dict[str, Any]]:
        if image_file_ids and not profile.supports_vision:
            raise AppError(
                MODEL_CAPABILITY_MISMATCH,
                "Plan Mode received image inputs, but the selected model profile does not support vision.",
                status_code=400,
            )
        image_infos: list[dict[str, Any]] = []
        for file_id in image_file_ids:
            uploaded = get_uploaded_image(self.db, file_id)
            image_infos.append(
                {
                    "file_id": uploaded.id,
                    "filename": uploaded.original_filename,
                    "mime_type": uploaded.mime_type,
                    "size": uploaded.size,
                }
            )
        return image_infos

    def _load_prompt_bundle(self, task_prompt_key: str) -> PlanSkillPromptBundle:
        prompts = self.prompt_service.load_many(
            [
                "shared/experiment_assistant",
                "shared/safety",
                "plan/system",
                task_prompt_key,
            ]
        )
        prompts.append(
            self.prompt_service.load_prompt_document(
                "plan_mode_prompts_txt",
                "plan_mode_prompts.txt",
            )
        )
        return PlanSkillPromptBundle(
            prompt_key=task_prompt_key,
            prompts=prompts,
            prompt_hashes={prompt.key: prompt.content_hash for prompt in prompts},
            prompt_version_hash=PromptService.composite_hash(prompts),
        )

    async def _iter_model_events(
        self,
        *,
        profile: ModelProfile,
        prompt_bundle: PlanSkillPromptBundle,
        user_text: str,
        image_file_ids: list[str],
        extra_params: dict[str, Any] | None,
        delta_event: str,
        is_disconnected: Callable[[], Awaitable[bool]] | None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        messages = self._build_provider_messages(
            prompt_bundle=prompt_bundle,
            user_text=user_text,
            image_file_ids=image_file_ids,
            supports_vision=profile.supports_vision,
        )
        adapter = get_adapter_for_profile(profile)

        async for provider_event in adapter.stream_chat(
            model=profile.model_name or "",
            messages=messages,
            extra_params=extra_params,
        ):
            if is_disconnected and await is_disconnected():
                break

            if provider_event.event == "delta":
                yield delta_event, {
                    "text": str(provider_event.data),
                    "prompt_key": prompt_bundle.prompt_key,
                    "skill": PLAN_SKILL_NAME,
                }
            elif provider_event.event == "usage":
                yield "usage", {"usage": provider_event.data, "skill": PLAN_SKILL_NAME}

    def _build_provider_messages(
        self,
        *,
        prompt_bundle: PlanSkillPromptBundle,
        user_text: str,
        image_file_ids: list[str],
        supports_vision: bool,
    ) -> list[dict[str, Any]]:
        system_content = "\n\n".join(
            prompt.content.strip()
            for prompt in prompt_bundle.prompts
            if prompt.key != prompt_bundle.prompt_key and prompt.content.strip()
        )
        task_prompt = "\n\n".join(
            prompt.content.strip()
            for prompt in prompt_bundle.prompts
            if prompt.key == prompt_bundle.prompt_key and prompt.content.strip()
        )
        history: list[dict[str, Any]] = []
        if system_content:
            history.append(
                {
                    "role": "system",
                    "content": "\n\n".join([system_content, OUTPUT_FORMAT_SYSTEM_PROMPT]),
                }
            )
        else:
            history.append({"role": "system", "content": OUTPUT_FORMAT_SYSTEM_PROMPT})
        full_user_text = "\n\n".join(part for part in [task_prompt, user_text] if part.strip())
        return MessageBuilder(self.db).build_messages(
            history=history,
            user_text=full_user_text,
            image_file_ids=image_file_ids,
            supports_vision=supports_vision,
        )

    @staticmethod
    def build_generation_context(
        *,
        user_message: str,
        file_summaries: list[str],
        image_infos: list[dict[str, Any]],
        profile: ModelProfile,
        task: AnalysisTask,
    ) -> str:
        payload = {
            "skill": PLAN_SKILL_NAME,
            "task_id": task.id,
            "user_message": user_message,
            "file_summaries": file_summaries,
            "image_inputs": image_infos,
            "model_capabilities": {
                "supports_stream": profile.supports_stream,
                "supports_vision": profile.supports_vision,
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def build_revision_context(
        *,
        task: AnalysisTask,
        previous_plan: AnalysisPlan,
        user_feedback: str,
        profile: ModelProfile,
    ) -> str:
        payload = {
            "skill": PLAN_SKILL_NAME,
            "task_id": task.id,
            "user_message": task.user_message,
            "previous_plan": {
                "id": previous_plan.id,
                "version": previous_plan.version,
                "plan_markdown": previous_plan.plan_markdown,
            },
            "user_feedback": user_feedback,
            "model_capabilities": {
                "supports_stream": profile.supports_stream,
                "supports_vision": profile.supports_vision,
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
