from pathlib import Path
from typing import TYPE_CHECKING

from app.parsers.docx_parser import extract_docx
from app.parsers.excel_parser import extract_xlsx
from app.parsers.pdf_parser import extract_pdf
from app.parsers.pptx_parser import extract_pptx
from app.parsers.text_parser import extract_delimited, extract_json, extract_text
from app.parsers.types import DocumentContent, ParseLimitError, ParsedFileText, check_input_size

if TYPE_CHECKING:
    from app.db.models import UploadedFile


def parse_uploaded_file(uploaded: "UploadedFile") -> ParsedFileText:
    """Extract source blocks without invoking a model or changing the original file."""
    path = Path(uploaded.local_path)
    result = ParsedFileText(
        file_id=uploaded.id,
        filename=uploaded.original_filename,
        file_type=uploaded.file_type,
        mime_type=uploaded.mime_type,
        size=uploaded.size,
    )

    if not path.is_file():
        result.error = "File is missing on disk."
        result.parse_status = "error"
        return result
    if uploaded.file_type == "image":
        result.text = "Image file. Use the uploaded image ID as a vision input."
        result.text_chars = len(result.text)
        result.parse_status = "unsupported"
        result.warnings = ["Local text extraction does not perform OCR on images."]
        result.metadata = {"requires_vision": True}
        return result

    try:
        check_input_size(path)
        content = _extract_content(path, uploaded.extension.lower().lstrip("."), uploaded.file_type)
        result.blocks = content.blocks
        result.text = content.text
        result.warnings = content.warnings
        result.metadata = content.metadata
        result.parse_status = content.parse_status
        result.error = content.error
    except ParseLimitError as exc:
        result.parse_status = "partial"
        result.error = str(exc)
        result.warnings = [str(exc)]
        result.metadata = {"parse_truncated": True}
    except Exception as exc:
        result.parse_status = "error"
        result.error = f"{exc.__class__.__name__}: {exc}"

    if not result.blocks and not result.error:
        result.error = "No text content could be extracted."
        if result.parse_status == "success":
            result.parse_status = "error"

    result.text_chars = len(result.text)
    return result


def _extract_content(path: Path, extension: str, file_type: str) -> DocumentContent:
    if extension in {"txt", "md"}:
        return extract_text(path)
    if extension in {"csv", "tsv"}:
        return extract_delimited(path, "\t" if extension == "tsv" else ",")
    if extension == "json":
        return extract_json(path)
    parsers = {"xlsx": extract_xlsx, "pdf": extract_pdf, "docx": extract_docx, "pptx": extract_pptx}
    if extension in parsers:
        return parsers[extension](path)
    if extension in {"doc", "xls", "ppt"}:
        message = "Legacy binary Office extraction is unsupported. Convert the file to docx, xlsx, or pptx."
    else:
        message = f"No parser is available for file type '{file_type}' with extension '{extension}'."
    return DocumentContent(parse_status="unsupported", error=message, warnings=[message])


def _parse_content(path: Path, extension: str, file_type: str) -> str:
    return _extract_content(path, extension, file_type).text
