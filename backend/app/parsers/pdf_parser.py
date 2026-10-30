from pathlib import Path

from app.parsers.text_parser import clean_text
from app.parsers.types import DocumentContent, MAX_PAGES, check_input_size


def extract_pdf(path: Path) -> DocumentContent:
    from pypdf import PdfReader

    check_input_size(path)
    reader = PdfReader(str(path))
    result = DocumentContent(metadata={"page_count": len(reader.pages), "ocr_performed": False})
    if len(reader.pages) > MAX_PAGES:
        result.warn(f"Only the first {MAX_PAGES} PDF pages were extracted.", truncated=True)
    empty_pages = []
    processed = 0
    for page_number, page in enumerate(reader.pages, start=1):
        if page_number > MAX_PAGES or result.full:
            break
        processed += 1
        try:
            text = clean_text(page.extract_text() or "")
            if text:
                result.add("page", text, {"page": page_number})
            else:
                empty_pages.append(page_number)
            resources = page.get("/Resources", {})
            resources = resources.get_object() if hasattr(resources, "get_object") else resources
            xobjects = resources.get("/XObject", {})
            xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
            if any(obj.get_object().get("/Subtype") == "/Image" for obj in xobjects.values()):
                result.warn("PDF contains raster images; their content was not extracted and OCR was not performed.")
        except Exception as exc:
            result.warn(f"PDF page {page_number} could not be fully extracted ({exc.__class__.__name__}).")
    if empty_pages:
        result.metadata["empty_pages"] = empty_pages
        result.warn(f"PDF pages without extractable text: {', '.join(map(str, empty_pages))}. They may be blank or scanned; OCR was not performed.")
    result.metadata["processed_pages"] = processed
    return result


def parse_pdf(path: Path) -> str:
    return extract_pdf(path).text
