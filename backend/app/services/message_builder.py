import base64
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import FILE_NOT_FOUND, FILE_TOO_LARGE, FILE_TYPE_NOT_SUPPORTED, MODEL_CAPABILITY_MISMATCH, AppError
from app.db.models import UploadedFile
from app.services.file_service import ALLOWED_IMAGE_MIME_BY_EXTENSION, get_uploaded_image


class MessageBuilder:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    def build_messages(
        self,
        *,
        history: list[dict[str, Any]],
        user_text: str,
        image_file_ids: list[str],
        file_summaries: list[str] | None = None,
        supports_vision: bool,
    ) -> list[dict[str, Any]]:
        messages = list(history)
        files = [get_uploaded_image(self.db, file_id) for file_id in image_file_ids]
        messages.append(
            self.build_user_message(
                self._with_file_summaries(user_text, file_summaries or []),
                files,
                supports_vision,
            )
        )
        return messages

    def build_user_message(
        self,
        user_text: str,
        files: list[UploadedFile],
        supports_vision: bool,
    ) -> dict[str, Any]:
        if not files:
            return {"role": "user", "content": user_text}

        if not supports_vision:
            raise AppError(
                MODEL_CAPABILITY_MISMATCH,
                "This model profile does not support vision. Enable supports_vision or choose a vision model.",
                status_code=400,
            )

        content: list[dict[str, Any]] = []
        if user_text.strip():
            content.append({"type": "text", "text": user_text})
        for uploaded in files:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._to_data_url(uploaded)},
                }
            )
        return {"role": "user", "content": content}

    def _to_data_url(self, uploaded: UploadedFile) -> str:
        allowed_mimes = ALLOWED_IMAGE_MIME_BY_EXTENSION.get(uploaded.extension)
        if uploaded.file_type != "image" or not allowed_mimes or uploaded.mime_type not in allowed_mimes:
            raise AppError(
                FILE_TYPE_NOT_SUPPORTED,
                "Uploaded file is not a supported image.",
                status_code=415,
            )

        if uploaded.size > self.settings.max_image_upload_bytes:
            raise AppError(
                FILE_TOO_LARGE,
                f"Image is larger than {self.settings.max_image_upload_mb} MB.",
                status_code=413,
            )

        path = Path(uploaded.local_path)
        if not path.is_file():
            raise AppError(FILE_NOT_FOUND, "Uploaded image file is missing on disk.", status_code=404)

        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{uploaded.mime_type};base64,{encoded}"

    @staticmethod
    def _with_file_summaries(user_text: str, file_summaries: list[str]) -> str:
        summaries = [summary.strip() for summary in file_summaries if summary.strip()]
        if not summaries:
            return user_text

        attachment_context = "\n\n".join(
            f"Attachment {index}:\n{summary}" for index, summary in enumerate(summaries, start=1)
        )
        parts = [user_text.strip(), "Attached file context:", attachment_context]
        return "\n\n".join(part for part in parts if part)
