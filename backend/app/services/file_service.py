import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

import aiofiles
from fastapi import UploadFile
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import (
    DATABASE_ERROR,
    FILE_NOT_FOUND,
    FILE_TOO_LARGE,
    FILE_TYPE_NOT_SUPPORTED,
    AppError,
)
from app.db.models import Message, UploadedFile


ALLOWED_IMAGE_MIME_BY_EXTENSION = {
    "png": {"image/png"},
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "webp": {"image/webp"},
}

ALLOWED_FILE_MIME_BY_EXTENSION = {
    **ALLOWED_IMAGE_MIME_BY_EXTENSION,
    "csv": {"text/csv", "application/csv", "application/vnd.ms-excel"},
    "tsv": {"text/tab-separated-values", "text/plain"},
    "txt": {"text/plain"},
    "md": {"text/markdown", "text/plain"},
    "json": {"application/json", "text/json", "text/plain"},
    "svg": {"image/svg+xml", "text/xml", "application/xml"},
    "pdf": {"application/pdf"},
    "doc": {"application/msword", "application/octet-stream"},
    "docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    },
    "xls": {"application/vnd.ms-excel", "application/octet-stream"},
    "xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/octet-stream",
    },
    "ppt": {"application/vnd.ms-powerpoint", "application/octet-stream"},
    "pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
        "application/octet-stream",
    },
}

FILE_TYPE_BY_EXTENSION = {
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "webp": "image",
    "csv": "spreadsheet",
    "tsv": "spreadsheet",
    "xls": "spreadsheet",
    "xlsx": "spreadsheet",
    "txt": "text",
    "md": "text",
    "json": "data",
    "svg": "image",
    "pdf": "document",
    "doc": "document",
    "docx": "document",
    "ppt": "presentation",
    "pptx": "presentation",
}

DEFAULT_MIME_BY_EXTENSION = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "txt": "text/plain",
    "md": "text/markdown",
    "json": "application/json",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

PERMISSIVE_UPLOAD_MIMES = {"", "application/octet-stream"}


def get_uploaded_file(db: Session, file_id: str) -> UploadedFile:
    uploaded = db.get(UploadedFile, file_id)
    if uploaded is None:
        raise AppError(FILE_NOT_FOUND, "Uploaded file was not found.", status_code=404)
    return uploaded


GENERATED_EXTENSIONS = {
    "docx",
    "xlsx",
    "pptx",
    "csv",
    "tsv",
    "json",
    "png",
    "jpg",
    "jpeg",
    "svg",
    "pdf",
    "txt",
    "md",
}


def register_generated_file(db: Session, source: Path, *, original_filename: str) -> UploadedFile:
    """Copy a validated sandbox artifact into managed storage and register it for download."""
    settings = get_settings()
    safe_name = Path(original_filename.replace("\\", "/")).name or "generated-file"
    extension = Path(safe_name).suffix.lower().lstrip(".")
    if extension not in GENERATED_EXTENSIONS:
        raise AppError(
            FILE_TYPE_NOT_SUPPORTED,
            "The generated file type is not allowed.",
            status_code=415,
            details={"extension": extension},
        )
    if not source.is_file() or source.is_symlink():
        raise AppError(FILE_NOT_FOUND, "Generated file is missing.", status_code=404)

    size = source.stat().st_size
    if size <= 0:
        raise AppError(FILE_TYPE_NOT_SUPPORTED, "Generated file is empty.", status_code=415)
    if size > settings.sandbox_max_output_file_mb * 1024 * 1024:
        raise AppError(FILE_TOO_LARGE, "Generated file is too large.", status_code=413)

    destination_dir = settings.generated_file_dir / (FILE_TYPE_BY_EXTENSION.get(extension) or "data")
    destination_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{uuid4().hex}.{extension}"
    destination = destination_dir / stored_filename
    shutil.copy2(source, destination)
    digest = _sha256_file(destination)
    uploaded = UploadedFile(
        original_filename=safe_name,
        stored_filename=stored_filename,
        mime_type=DEFAULT_MIME_BY_EXTENSION.get(extension, "application/octet-stream"),
        extension=extension,
        size=size,
        sha256=digest,
        local_path=str(destination),
        file_type=FILE_TYPE_BY_EXTENSION.get(extension) or "data",
    )
    try:
        db.add(uploaded)
        db.flush()
    except SQLAlchemyError as exc:
        destination.unlink(missing_ok=True)
        raise AppError(DATABASE_ERROR, "Failed to register generated file.", status_code=500) from exc
    return uploaded


def generated_file_payload(uploaded: UploadedFile, *, status: str = "ready") -> dict[str, object]:
    return {
        "fileId": uploaded.id,
        "name": uploaded.original_filename,
        "mimeType": uploaded.mime_type,
        "fileType": uploaded.file_type,
        "size": uploaded.size,
        "sha256": uploaded.sha256,
        "status": status,
    }


def collect_chat_file_ids(
    db: Session,
    chat_id: str,
    *,
    current_file_ids: list[str] | None = None,
    include_generated: bool = True,
) -> list[str]:
    """Return current files first, then newest files already referenced by the chat."""
    ordered: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            ordered.append(value)

    for file_id in current_file_ids or []:
        add(file_id)

    messages = list(
        db.query(Message)
        .filter(Message.chat_id == chat_id, Message.status.in_(["done", "streaming", "error"]))
        .order_by(Message.created_at.desc())
        .all()
    )
    for message in messages:
        metadata = _metadata_dict(message.metadata_json)
        for attachment in metadata.get("attachments", []):
            if isinstance(attachment, dict):
                add(attachment.get("fileId"))
        if include_generated:
            for generated in metadata.get("generatedFiles", []):
                if isinstance(generated, dict) and generated.get("status", "ready") == "ready":
                    add(generated.get("fileId"))
    return ordered


def delete_managed_file(db: Session, uploaded: UploadedFile) -> Path | None:
    """Stage a generated-file row for deletion and return its safe cleanup path."""
    cleanup_path = _generated_cleanup_path(uploaded.local_path)
    db.delete(uploaded)
    return cleanup_path


def unlink_managed_files(paths: list[Path]) -> None:
    """Best-effort filesystem cleanup after the owning database transaction commits."""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # The database is already authoritative at this point. A stale file
            # can be removed by a later storage cleanup without failing the API.
            continue


def _generated_cleanup_path(local_path: str) -> Path | None:
    try:
        root = get_settings().generated_file_dir.resolve()
        candidate = Path(local_path).resolve()
    except (OSError, RuntimeError):
        return None
    if candidate == root:
        return None
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _metadata_dict(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_uploaded_image(db: Session, file_id: str) -> UploadedFile:
    uploaded = get_uploaded_file(db, file_id)
    if uploaded.file_type != "image":
        raise AppError(FILE_NOT_FOUND, "Uploaded image was not found.", status_code=404)
    return uploaded


async def save_uploaded_image(db: Session, upload: UploadFile) -> UploadedFile:
    return await save_uploaded_file(db, upload, require_image=True)


async def save_uploaded_file(
    db: Session,
    upload: UploadFile,
    *,
    require_image: bool = False,
) -> UploadedFile:
    settings = get_settings()
    original_name = Path((upload.filename or "upload").replace("\\", "/")).name
    extension = Path(original_name).suffix.lower().lstrip(".")
    mime_type = (upload.content_type or "").lower()

    allowed_mimes = ALLOWED_FILE_MIME_BY_EXTENSION.get(extension)
    file_type = FILE_TYPE_BY_EXTENSION.get(extension)
    if require_image and file_type != "image":
        raise AppError(
            FILE_TYPE_NOT_SUPPORTED,
            "Only png, jpg, jpeg, and webp images are supported.",
            status_code=415,
            details={"mime_type": mime_type, "extension": extension},
        )

    if not allowed_mimes or file_type is None:
        raise AppError(
            FILE_TYPE_NOT_SUPPORTED,
            "Only images, text, csv, Excel, PDF, Word, and PowerPoint files are supported.",
            status_code=415,
            details={"mime_type": mime_type, "extension": extension},
        )

    if mime_type not in allowed_mimes and mime_type not in PERMISSIVE_UPLOAD_MIMES:
        raise AppError(
            FILE_TYPE_NOT_SUPPORTED,
            "The uploaded file type does not match its extension.",
            status_code=415,
            details={"mime_type": mime_type, "extension": extension},
        )
    stored_mime_type = mime_type
    if stored_mime_type in PERMISSIVE_UPLOAD_MIMES:
        stored_mime_type = DEFAULT_MIME_BY_EXTENSION.get(extension, "application/octet-stream")

    max_bytes = (
        settings.max_image_upload_bytes if file_type == "image" else settings.max_file_upload_bytes
    )
    max_mb = settings.max_image_upload_mb if file_type == "image" else settings.max_file_upload_mb
    upload_dir = (
        settings.image_upload_dir if file_type == "image" else settings.file_upload_dir / file_type
    )
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{uuid4().hex}.{extension}"
    destination = upload_dir / stored_filename
    digest = hashlib.sha256()
    size = 0

    try:
        async with aiofiles.open(destination, "wb") as out_file:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    await out_file.close()
                    destination.unlink(missing_ok=True)
                    raise AppError(
                        FILE_TOO_LARGE,
                        f"File is larger than {max_mb} MB.",
                        status_code=413,
                    )
                digest.update(chunk)
                await out_file.write(chunk)
    finally:
        await upload.close()

    if size == 0:
        destination.unlink(missing_ok=True)
        raise AppError(
            FILE_TYPE_NOT_SUPPORTED,
            "Uploaded file is empty.",
            status_code=415,
        )

    uploaded = UploadedFile(
        original_filename=original_name,
        stored_filename=stored_filename,
        mime_type=stored_mime_type,
        extension=extension,
        size=size,
        sha256=digest.hexdigest(),
        local_path=str(destination),
        file_type=file_type,
    )

    try:
        db.add(uploaded)
        db.commit()
        db.refresh(uploaded)
    except SQLAlchemyError as exc:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise AppError(
            DATABASE_ERROR,
            "Failed to save uploaded file metadata.",
            status_code=500,
        ) from exc

    return uploaded
