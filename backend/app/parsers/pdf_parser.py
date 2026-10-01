from pathlib import Path

from app.parsers.text_parser import clean_text


def parse_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "Install pypdf to extract PDF text content."

    reader = PdfReader(str(path))
    lines = [f"PDF pages: {len(reader.pages)}"]
    for page_number, page in enumerate(reader.pages, start=1):
        text = clean_text(page.extract_text() or "")
        if text:
            lines.append(f"\nPage {page_number}:\n{text}")
    return "\n".join(lines)
