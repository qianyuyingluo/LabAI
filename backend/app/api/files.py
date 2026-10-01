from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.errors import FILE_NOT_FOUND, AppError
from app.services.file_service import get_uploaded_file, get_uploaded_image, save_uploaded_file, save_uploaded_image
from app.skills.files_skill import FilesSkillService


router = APIRouter(prefix="/api/files", tags=["files"])


class UploadImageResponse(BaseModel):
    file_id: str
    filename: str
    mime_type: str
    size: int


class UploadFileResponse(UploadImageResponse):
    file_type: str
    summary: str


@router.post("/upload-image", response_model=UploadImageResponse)
async def upload_image(
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
) -> UploadImageResponse:
    uploaded = await save_uploaded_image(db, file)
    return UploadImageResponse(
        file_id=uploaded.id,
        filename=uploaded.original_filename,
        mime_type=uploaded.mime_type,
        size=uploaded.size,
    )


@router.post("/upload", response_model=UploadFileResponse)
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
) -> UploadFileResponse:
    uploaded = await save_uploaded_file(db, file)
    return UploadFileResponse(
        file_id=uploaded.id,
        filename=uploaded.original_filename,
        mime_type=uploaded.mime_type,
        size=uploaded.size,
        file_type=uploaded.file_type,
        summary=FilesSkillService(db).build_upload_summary(uploaded),
    )


@router.get("/images/{file_id}")
async def read_uploaded_image(
    file_id: str,
    db: Session = Depends(get_session),
) -> FileResponse:
    uploaded = get_uploaded_image(db, file_id)
    image_path = Path(uploaded.local_path)
    if not image_path.is_file():
        raise AppError(FILE_NOT_FOUND, "Uploaded image file is missing on disk.", status_code=404)
    return FileResponse(
        image_path,
        filename=uploaded.original_filename,
        media_type=uploaded.mime_type,
    )


@router.get("/{file_id}")
async def read_uploaded_file(
    file_id: str,
    db: Session = Depends(get_session),
) -> FileResponse:
    uploaded = get_uploaded_file(db, file_id)
    file_path = Path(uploaded.local_path)
    if not file_path.is_file():
        raise AppError(FILE_NOT_FOUND, "Uploaded file is missing on disk.", status_code=404)
    return FileResponse(
        file_path,
        filename=uploaded.original_filename,
        media_type=uploaded.mime_type,
    )
