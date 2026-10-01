from dataclasses import dataclass, field

from app.db.models import UploadedFile


@dataclass(slots=True)
class FilesSkillInput:
    files: list[UploadedFile]
    max_context_chars: int
    preview_chars: int = 4000


@dataclass(slots=True)
class FilesSkillFileResult:
    file_id: str
    filename: str
    file_type: str
    mime_type: str
    size: int
    extracted_text: str = ""
    extracted_chars: int = 0
    included_chars: int = 0
    truncated: bool = False
    error: str | None = None
    skill_output: str = ""
    skill_output_chars: int = 0
    skill_error: str | None = None

    def to_metadata(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "fileId": self.file_id,
            "filename": self.filename,
            "fileType": self.file_type,
            "mimeType": self.mime_type,
            "size": self.size,
            "extractedChars": self.extracted_chars,
            "includedChars": self.included_chars,
            "truncated": self.truncated,
            "skillOutputChars": self.skill_output_chars,
        }
        if self.error:
            payload["error"] = self.error
        if self.skill_output:
            payload["skillOutput"] = self.skill_output
        if self.skill_error:
            payload["skillError"] = self.skill_error
        return payload


@dataclass(slots=True)
class FilesSkillResult:
    status: str
    files: list[FilesSkillFileResult] = field(default_factory=list)
    model_context: str = ""
    context_preview: str = ""
    total_extracted_chars: int = 0
    included_chars: int = 0
    truncated: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def has_context(self) -> bool:
        return bool(self.model_context.strip())

    def to_metadata(self) -> dict[str, object]:
        return {
            "status": self.status,
            "files": [file.to_metadata() for file in self.files],
            "contextPreview": self.context_preview,
            "totalExtractedChars": self.total_extracted_chars,
            "includedChars": self.included_chars,
            "truncated": self.truncated,
            "errors": self.errors,
        }
