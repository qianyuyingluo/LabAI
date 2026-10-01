import unittest

from app.db.models import ModelProfile
from app.services.model_service import system_prompt_messages
from app.services.output_format import OUTPUT_FORMAT_SYSTEM_PROMPT
from app.services.plan_execution_service import PlanExecutionService
from app.services.prompt_service import LoadedPrompt
from app.skills.files_skill.service import FILES_SKILL_SYSTEM_PROMPT
from app.skills.plan_skill.schemas import PlanSkillPromptBundle
from app.skills.plan_skill.service import PlanSkillService


class OutputFormatPromptTests(unittest.TestCase):
    def test_regular_chat_does_not_inject_format_prompt_by_default(self) -> None:
        profile = ModelProfile(name="test", provider="openai_compat")

        self.assertEqual(system_prompt_messages(profile), [])

    def test_regular_chat_preserves_only_custom_system_prompt(self) -> None:
        profile = ModelProfile(
            name="test",
            provider="openai_compat",
            system_prompt="Custom lab behavior.",
        )
        messages = system_prompt_messages(profile)

        self.assertEqual(messages, [{"role": "system", "content": "Custom lab behavior."}])
        self.assertNotIn(OUTPUT_FORMAT_SYSTEM_PROMPT, messages[0]["content"])

    def test_files_skill_does_not_inject_plan_format_prompt(self) -> None:
        self.assertNotIn(OUTPUT_FORMAT_SYSTEM_PROMPT, FILES_SKILL_SYSTEM_PROMPT)

    def test_plan_skill_uses_shared_output_format(self) -> None:
        service = PlanSkillService(db=None)  # type: ignore[arg-type]
        prompt_bundle = PlanSkillPromptBundle(
            prompt_key="plan/plan",
            prompts=[
                LoadedPrompt(
                    key="shared/experiment_assistant",
                    content="Plan mode system.",
                    content_hash="hash-1",
                    relative_path="shared/experiment_assistant.md",
                ),
                LoadedPrompt(
                    key="plan/plan",
                    content="Plan task prompt.",
                    content_hash="hash-2",
                    relative_path="plan/plan.md",
                ),
            ],
            prompt_hashes={},
            prompt_version_hash="bundle-hash",
        )

        messages = service._build_provider_messages(
            prompt_bundle=prompt_bundle,
            user_text="User request.",
            image_file_ids=[],
            supports_vision=False,
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Plan mode system.", messages[0]["content"])
        self.assertIn(OUTPUT_FORMAT_SYSTEM_PROMPT, messages[0]["content"])

    def test_plan_execution_uses_shared_output_format(self) -> None:
        service = PlanExecutionService(db=None)  # type: ignore[arg-type]
        messages = service._build_provider_messages(
            prompts=[
                LoadedPrompt(
                    key="shared/experiment_assistant",
                    content="Execution system.",
                    content_hash="hash-1",
                    relative_path="shared/experiment_assistant.md",
                ),
                LoadedPrompt(
                    key="plan/execute_approved_plan",
                    content="Execution task.",
                    content_hash="hash-2",
                    relative_path="plan/execute_approved_plan.md",
                ),
            ],
            user_text="Execute.",
            supports_vision=False,
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Execution system.", messages[0]["content"])
        self.assertIn(OUTPUT_FORMAT_SYSTEM_PROMPT, messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
