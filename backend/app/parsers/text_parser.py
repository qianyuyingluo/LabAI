import csv
import io
import re
from pathlib import Path


TEXT_BYTE_LIMIT = 5 * 1024 * 1024


def parse_text(path: Path) -> str:
    with path.open("rb") as handle:
        data = handle.read(TEXT_BYTE_LIMIT + 1)
    text = decode_text(data[:TEXT_BYTE_LIMIT])
    if len(data) > TEXT_BYTE_LIMIT:
        text += "\n\n[Text extraction truncated at local reader byte limit.]"
    return text


def parse_delimited(path: Path, delimiter: str) -> str:
    text = parse_text(path)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = ["Delimited file content:"]
    for row in reader:
        rows.append(" | ".join(format_cell(value) for value in row))
    return "\n".join(rows)


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def format_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ").replace("\r", " ").strip()


def clean_text(text: str) -> str:
    normalized = text.replace("\x00", "")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()
