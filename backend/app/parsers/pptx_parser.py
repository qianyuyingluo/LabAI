import re
from pathlib import Path
from zipfile import ZipFile

from app.parsers.docx_parser import read_xml_text
from app.parsers.text_parser import clean_text


def parse_pptx(path: Path) -> str:
    with ZipFile(path) as archive:
        slide_names = sorted(
            (
                name
                for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            ),
            key=_slide_sort_key,
        )
        lines = [f"Slides: {len(slide_names)}"]
        for index, name in enumerate(slide_names, start=1):
            text = clean_text(read_xml_text(archive, name))
            if text:
                lines.append(f"\nSlide {index}:\n{text}")
        return "\n".join(lines)


def _slide_sort_key(name: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", name)
    return int(match.group(1)) if match else 0
