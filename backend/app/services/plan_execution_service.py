import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AnalysisPlan, AnalysisTask, ModelProfile
from app.services.message_builder import MessageBuilder
from app.services.output_format import OUTPUT_FORMAT_SYSTEM_PROMPT
from app.services.prompt_service import LoadedPrompt, PromptService


EXECUTION_PROMPT_KEY = "plan/execute_approved_plan"
PLAN_APPROVAL_USER_MESSAGE = "执行此计划"


class PlanExecutionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.prompt_service = PromptService(db)

    def build_provider_messages(
        self,
        *,
        profile: ModelProfile,
        task: AnalysisTask,
        plan: AnalysisPlan,
    ) -> list[dict[str, Any]]:
        prompts = self.prompt_service.load_many(
            [
                "shared/experiment_assistant",
                "shared/safety",
                EXECUTION_PROMPT_KEY,
            ]
        )
        return self._build_provider_messages(
            prompts=prompts,
            user_text=self.build_execution_context(task=task, plan=plan, profile=profile),
            supports_vision=profile.supports_vision,
        )

    def _build_provider_messages(
        self,
        *,
        prompts: list[LoadedPrompt],
        user_text: str,
        supports_vision: bool,
    ) -> list[dict[str, Any]]:
        system_content = "\n\n".join(
            prompt.content.strip()
            for prompt in prompts
            if prompt.key != EXECUTION_PROMPT_KEY and prompt.content.strip()
        )
        task_prompt = "\n\n".join(
            prompt.content.strip()
            for prompt in prompts
            if prompt.key == EXECUTION_PROMPT_KEY and prompt.content.strip()
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
            image_file_ids=[],
            supports_vision=supports_vision,
        )

    @staticmethod
    def build_execution_context(
        *,
        task: AnalysisTask,
        plan: AnalysisPlan,
        profile: ModelProfile,
    ) -> str:
        payload = {
            "approval_message": PLAN_APPROVAL_USER_MESSAGE,
            "execution_request": {
                "phase": "execute_approved_plan",
                "user_intent": "The user has approved the plan and is asking the assistant to execute it now.",
                "required_behavior": [
                    "Do not generate another plan.",
                    "Do not ask for plan approval again.",
                    "Execute the approved work rather than merely describing how to do it.",
                    "Use the Python sandbox for real calculations, plots, and downloadable files when needed.",
                    "Verify tool results and never claim that code ran or a file exists unless the tool confirms it.",
                ],
            },
            "task_id": task.id,
            "user_message": task.user_message,
            "approved_plan": {
                "id": plan.id,
                "version": plan.version,
                "plan_markdown": plan.plan_markdown,
            },
            "model_capabilities": {
                "supports_stream": profile.supports_stream,
                "supports_vision": profile.supports_vision,
                "supports_tools": profile.supports_tools,
            },
            "execution_limits": {
                "python_sandbox": True,
                "real_data_calculation": True,
                "tool_calls": True,
            },
        }
        return "\n\n".join(
            [
                PLAN_APPROVAL_USER_MESSAGE,
                json.dumps(payload, ensure_ascii=False, indent=2),
            ]
        )
