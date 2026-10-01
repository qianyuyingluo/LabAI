from typing import Any

from sqlalchemy.orm import Session

from app.adapters.registry import get_adapter_for_profile
from app.core.config import get_settings
from app.db.models import ModelProfile, UploadedFile
from app.parsers.local_parser import parse_uploaded_file
from app.services.file_service import collect_chat_file_ids, get_uploaded_file
from app.skills.files_skill.context import build_files_skill_result, build_files_skill_result_from_outputs
from app.skills.files_skill.schemas import FilesSkillInput, FilesSkillResult

FILES_SKILL_SYSTEM_PROMPT = (
    "You are LabAI files_skill. Analyze exactly one uploaded file per call. "
    "Do not use chat history, previous files, or any other document. "
    "Answer in Chinese. If the extracted content is incomplete, say so. "
    "Focus on what this single file is and what it says about the user's current request."
)


class FilesSkillService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    def run(
        self,
        *,
        chat_id: str | None,
        file_ids: list[str],
        include_chat_files: bool = True,
    ) -> FilesSkillResult:
        uploaded_files = self._load_files(
            chat_id=chat_id,
            file_ids=file_ids,
            include_chat_files=include_chat_files,
        )
        return build_files_skill_result(
            FilesSkillInput(
                files=uploaded_files,
                max_context_chars=self.settings.max_files_skill_context_chars,
                preview_chars=self.settings.files_skill_preview_chars,
            )
        )

    async def run_with_model(
        self,
        *,
        chat_id: str | None,
        file_ids: list[str],
        profile: ModelProfile,
        user_message: str,
        include_chat_files: bool = True,
        extra_params: dict[str, Any] | None = None,
    ) -> FilesSkillResult:
        extracted = self.run(
            chat_id=chat_id,
            file_ids=file_ids,
            include_chat_files=include_chat_files,
        )
        if extracted.status == "skipped":
            return extracted

        adapter = get_adapter_for_profile(profile)
        for file_result in extracted.files:
            try:
                response = await adapter.chat(
                    model=profile.model_name or "",
                    messages=_build_single_file_messages(
                        file_result=file_result,
                        user_message=user_message,
                        char_limit=self.settings.files_skill_per_file_model_chars,
                    ),
                    extra_params=_non_stream_extra_params(extra_params),
                )
                file_result.skill_output = response.content.strip()
                file_result.skill_output_chars = len(file_result.skill_output)
            except Exception as exc:
                file_result.skill_error = f"files_skill model call failed: {exc.__class__.__name__}"

        return build_files_skill_result_from_outputs(
            file_results=extracted.files,
            max_context_chars=self.settings.max_files_skill_context_chars,
            preview_chars=self.settings.files_skill_preview_chars,
        )

    def has_context_files(
        self,
        *,
        chat_id: str | None,
        file_ids: list[str],
        include_chat_files: bool = True,
    ) -> bool:
        return bool(
            self._load_files(
                chat_id=chat_id,
                file_ids=file_ids,
                include_chat_files=include_chat_files,
            )
        )

    def build_upload_summary(self, uploaded: UploadedFile) -> str:
        result = parse_uploaded_file(uploaded)
        header = [
            f"File: {result.filename}",
            f"File ID: {result.file_id}",
            f"Kind: {result.file_type}",
            f"MIME: {result.mime_type}",
            f"Size: {result.size} bytes",
            "",
            "Extracted preview:",
        ]
        body = result.text or f"Content extraction failed: {result.error or 'empty content'}"
        summary = "\n".join([*header, body])
        limit = self.settings.files_skill_upload_summary_chars
        if len(summary) <= limit:
            return summary
        return summary[:limit].rstrip() + "\n\n[Preview truncated.]"

    def _load_files(
        self,
        *,
        chat_id: str | None,
        file_ids: list[str],
        include_chat_files: bool,
    ) -> list[UploadedFile]:
        ordered_ids: list[str] = []
        seen: set[str] = set()

        for file_id in file_ids:
            if file_id not in seen:
                ordered_ids.append(file_id)
                seen.add(file_id)

        if include_chat_files and chat_id:
            for file_id in self._chat_attachment_file_ids(chat_id):
                if file_id not in seen:
                    ordered_ids.append(file_id)
                    seen.add(file_id)

        files: list[UploadedFile] = []
        for file_id in ordered_ids:
            uploaded = get_uploaded_file(self.db, file_id)
            if uploaded.file_type != "image":
                files.append(uploaded)
        return files

    def _chat_attachment_file_ids(self, chat_id: str) -> list[str]:
        return collect_chat_file_ids(self.db, chat_id, include_generated=True)


def _build_single_file_messages(
    *,
    file_result: Any,
    user_message: str,
    char_limit: int,
) -> list[dict[str, str]]:
    extracted = file_result.extracted_text or f"[Extraction unavailable: {file_result.error or 'empty content'}]"
    truncated = len(extracted) > char_limit
    content = extracted[:char_limit]
    if truncated:
        content = content.rstrip() + "\n[Extracted content truncated for this isolated files_skill call.]"
    question = user_message.strip() or "Summarize this uploaded file."
    user_payload = (
        "Current user request:\n"
        f"{question}\n\n"
        "Single file metadata:\n"
        f"- filename: {file_result.filename}\n"
        f"- file_id: {file_result.file_id}\n"
        f"- type: {file_result.file_type}\n"
        f"- mime: {file_result.mime_type}\n"
        f"- size_bytes: {file_result.size}\n"
        f"- extracted_chars: {file_result.extracted_chars}\n"
        f"- extraction_error: {file_result.error or 'none'}\n\n"
        "Single file extracted content:\n"
        f"{content}\n\n"
        "Respond in Chinese with a concise but useful answer for this file only. "
        "Start by identifying what this file is, then answer the current user request using only this file."
    )
    return [
        {"role": "system", "content": FILES_SKILL_SYSTEM_PROMPT},
        {"role": "user", "content": user_payload},
    ]


def _non_stream_extra_params(extra_params: dict[str, Any] | None) -> dict[str, Any] | None:
    if not extra_params:
        return None
    return {
        key: value
        for key, value in extra_params.items()
        if key not in {"stream", "stream_options"}
    }
