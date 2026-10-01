import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import BACKEND_DIR
from app.core.errors import AppError
from app.db.models import PromptVersion, now_utc


PROMPT_NOT_FOUND = "PROMPT_NOT_FOUND"


@dataclass(frozen=True, slots=True)
class LoadedPrompt:
    key: str
    content: str
    content_hash: str
    relative_path: str


class PromptService:
    def __init__(self, db: Session, prompts_dir: Path | None = None) -> None:
        self.db = db
        self.prompts_dir = prompts_dir or (BACKEND_DIR / "app" / "prompts")

    def load_prompt(self, key: str) -> LoadedPrompt:
        normalized_key = self._normalize_key(key)
        relative_path = f"{normalized_key}.md"
        return self._load_file(normalized_key, relative_path)

    def load_prompt_document(self, key: str, relative_path: str) -> LoadedPrompt:
        normalized_key = self._normalize_key(key)
        return self._load_file(normalized_key, relative_path)

    def _load_file(self, key: str, relative_path: str) -> LoadedPrompt:
        path = self.prompts_dir / relative_path

        if not path.is_file():
            raise AppError(
                PROMPT_NOT_FOUND,
                f"Prompt '{key}' was not found.",
                status_code=404,
                details={"path": relative_path},
            )

        raw = path.read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()
        prompt = LoadedPrompt(
            key=key,
            content=raw.decode("utf-8"),
            content_hash=content_hash,
            relative_path=relative_path.replace("\\", "/"),
        )
        self._record_prompt_version(prompt)
        return prompt

    def load_many(self, keys: list[str]) -> list[LoadedPrompt]:
        return [self.load_prompt(key) for key in keys]

    def get_plan_mode_prompts(self) -> list[LoadedPrompt]:
        prompts = self.load_many(
            [
                "shared/experiment_assistant",
                "shared/safety",
                "plan/system",
                "plan/plan",
                "plan/revise_plan",
                "plan/execute_approved_plan",
            ]
        )
        prompts.append(self.load_prompt_document("plan_mode_prompts_txt", "plan_mode_prompts.txt"))
        return prompts

    def get_plain_mode_prompts(self) -> list[LoadedPrompt]:
        return self.get_plan_mode_prompts()

    @staticmethod
    def composite_hash(prompts: list[LoadedPrompt]) -> str:
        digest = hashlib.sha256()
        for prompt in prompts:
            digest.update(prompt.key.encode("utf-8"))
            digest.update(b"\0")
            digest.update(prompt.content_hash.encode("ascii"))
            digest.update(b"\0")
        return digest.hexdigest()

    def _record_prompt_version(self, prompt: LoadedPrompt) -> None:
        existing = self.db.scalar(
            select(PromptVersion).where(
                PromptVersion.prompt_key == prompt.key,
                PromptVersion.content_hash == prompt.content_hash,
            )
        )
        if existing is None:
            self.db.add(
                PromptVersion(
                    prompt_key=prompt.key,
                    relative_path=prompt.relative_path,
                    content_hash=prompt.content_hash,
                )
            )
        else:
            existing.relative_path = prompt.relative_path
            existing.last_seen_at = now_utc()
        self.db.commit()

    @staticmethod
    def _normalize_key(key: str) -> str:
        normalized = key.strip().replace("\\", "/").removesuffix(".md")
        parts = [part for part in normalized.split("/") if part]
        if not parts or any(part in {".", ".."} for part in parts):
            raise AppError(PROMPT_NOT_FOUND, "Prompt key is invalid.", status_code=400)
        return "/".join(parts)
