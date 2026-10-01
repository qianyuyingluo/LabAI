from dataclasses import dataclass
from pathlib import Path

from app.db.models import UploadedFile
from app.parsers.docx_parser import parse_docx
from app.parsers.excel_parser import parse_xlsx
from app.parsers.pdf_parser import parse_pdf
from app.parsers.pptx_parser import parse_pptx
from app.parsers.text_parser import clean_text, parse_delimited, parse_text


@dataclass(slots=True)
class ParsedFileText:
    file_id: str
    filename: str
    file_type: str
    mime_type: str
    size: int
    text: str = ""
    text_chars: int = 0
    error: str | None = None


def parse_uploaded_file(uploaded: UploadedFile) -> ParsedFileText:
    path = Path(uploaded.local_path)
    result = ParsedFileText(
        file_id=uploaded.id,
        filename=uploaded.original_filename,
        file_type=uploaded.file_type,
        mime_type=uploaded.mime_type,
        size=uploaded.size,
    )

    if uploaded.file_type == "image":
        result.text = "Image file. Use the uploaded image ID as a vision input."
        result.text_chars = len(result.text)
        return result

    if not path.is_file():
        result.error = "File is missing on disk."
        return result

    try:
        result.text = clean_text(_parse_content(path, uploaded.extension.lower(), uploaded.file_type))
    except Exception as exc:
        result.error = f"{exc.__class__.__name__}: {exc}"
        result.text = ""

    if not result.text and not result.error:
        result.error = "No text content could be extracted."

    result.text_chars = len(result.text)
    return result


def _parse_content(path: Path, extension: str, file_type: str) -> str:
    if extension in {"txt", "md"}:
        return parse_text(path)
    if extension in {"csv", "tsv"}:
        return parse_delimited(path, "\t" if extension == "tsv" else ",")
    if extension == "xlsx":
        return parse_xlsx(path)
    if extension == "pdf":
        return parse_pdf(path)
    if extension == "docx":
        return parse_docx(path)
    if extension == "pptx":
        return parse_pptx(path)
    if extension in {"doc", "xls", "ppt"}:
        return (
            "This legacy binary Office format was saved, but local text extraction is not "
            "available. Convert it to docx, xlsx, or pptx for richer context."
        )
    return f"No parser is available for file type '{file_type}' with extension '{extension}'."
