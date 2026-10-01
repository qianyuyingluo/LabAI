from dataclasses import dataclass

from app.services.prompt_service import LoadedPrompt


@dataclass(frozen=True, slots=True)
class PlanSkillPromptBundle:
    prompt_key: str
    prompts: list[LoadedPrompt]
    prompt_hashes: dict[str, str]
    prompt_version_hash: str
