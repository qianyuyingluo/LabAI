import csv
import codecs
import io
import json
import re
from pathlib import Path

from app.parsers.types import DocumentContent, MAX_CELLS, MAX_COLUMNS, MAX_ROWS, check_input_size


TEXT_BYTE_LIMIT = 5 * 1024 * 1024


def parse_text(path: Path) -> str:
    return extract_text(path).text


def _read_text(path: Path, result: DocumentContent) -> str:
    check_input_size(path)
    with path.open("rb") as handle:
        data = handle.read(TEXT_BYTE_LIMIT + 1)
    if len(data) > TEXT_BYTE_LIMIT:
        result.warn("Text input was truncated at the 5 MiB reader limit.", truncated=True)
    text, encoding = _decode_text(data[:TEXT_BYTE_LIMIT], incomplete_tail=len(data) > TEXT_BYTE_LIMIT)
    result.metadata["encoding"] = encoding
    if encoding == "latin-1":
        result.warn("Text encoding could not be identified; latin-1 fallback may contain incorrectly decoded characters.")
    return text


def parse_delimited(path: Path, delimiter: str) -> str:
    return extract_delimited(path, delimiter).text


def extract_text(path: Path) -> DocumentContent:
    result = DocumentContent()
    text = _read_text(path, result)
    pending: list[str] = []
    first_line = 1
    last_line = 1
    size = 0

    def flush() -> None:
        if pending:
            value = "\n".join(pending)
            heading = re.match(r"^(#{1,6})\s+", value) if path.suffix.lower() == ".md" else None
            result.add("heading" if heading else "text", value,
                       {"line_start": first_line, "line_end": last_line},
                       {"heading_level": len(heading[1])} if heading else {})
            pending.clear()

    for number, line in enumerate(text.splitlines(), start=1):
        if result.full:
            break
        if not line.strip() or size + len(line) > 4000:
            flush()
            size = 0
        if line.strip():
            if not pending:
                first_line = number
            last_line = number
            pending.append(line)
            size += len(line) + 1
    flush()
    return result


def extract_delimited(path: Path, delimiter: str) -> DocumentContent:
    result = DocumentContent(metadata={"delimiter": delimiter})
    text = _read_text(path, result)
    # A byte-limited prefix must not turn a half-record into a purported complete row.
    if result.metadata.get("parse_truncated"):
        text = text.rsplit("\n", 1)[0] if "\n" in text else ""
    reader = csv.reader(io.StringIO(text), delimiter=delimiter, strict=True)
    old_limit = csv.field_size_limit()
    csv.field_size_limit(TEXT_BYTE_LIMIT)
    total_cells = 0
    previous_line = 0
    try:
        for number, row in enumerate(reader, start=1):
            if number > MAX_ROWS or total_cells + len(row) > MAX_CELLS:
                result.warn("Delimited-file row/cell extraction limit reached.", truncated=True)
                break
            total_cells += len(row)
            if len(row) > MAX_COLUMNS:
                result.warn(f"Delimited rows were limited to {MAX_COLUMNS} columns.", truncated=True)
                row = row[:MAX_COLUMNS]
            if number == 1:
                result.metadata["first_row"] = row
            start_line = previous_line + 1
            previous_line = reader.line_num
            if any(value for value in row):
                cells = [{"column": index, "value": value} for index, value in enumerate(row, 1)]
                labels = [f"C{index}={json.dumps(value, ensure_ascii=False)}" for index, value in enumerate(row, 1)]
                result.add("table_row", " | ".join(labels),
                           {"row": number, "line_start": start_line, "line_end": reader.line_num},
                           {"cells": cells})
            if result.full:
                break
    except csv.Error as exc:
        if not result.blocks:
            raise
        result.warn(f"Delimited parsing stopped near line {reader.line_num}: {exc}", truncated=True)
    finally:
        csv.field_size_limit(old_limit)
    result.metadata["cells_processed"] = total_cells
    return result


def extract_json(path: Path) -> DocumentContent:
    result = DocumentContent()
    text = _read_text(path, result)
    if result.metadata.get("parse_truncated"):
        result.error = "JSON exceeds the reader limit; a truncated JSON value cannot be parsed reliably."
        return result
    duplicates: list[str] = []

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                duplicates.append(key)
            value[key] = item
        return value

    def invalid_constant(value: str) -> None:
        raise ValueError(f"Non-standard JSON constant: {value}")

    value = json.loads(text, object_pairs_hook=object_pairs, parse_constant=invalid_constant)
    if duplicates:
        result.warn("JSON contains duplicate object keys; only their final values are retained.")
    result.metadata["root_type"] = type(value).__name__
    stack = [("", value, 0)]
    while stack and not result.full:
        pointer, item, depth = stack.pop()
        if depth > 100:
            result.warn("JSON nesting beyond 100 levels was omitted.", truncated=True)
            continue
        if isinstance(item, dict) and item:
            for key, child in reversed(list(item.items())):
                escaped = key.replace("~", "~0").replace("/", "~1")
                stack.append((pointer + "/" + escaped, child, depth + 1))
        elif isinstance(item, list) and item:
            for index in range(len(item) - 1, -1, -1):
                stack.append((pointer + "/" + str(index), item[index], depth + 1))
        else:
            result.add("json_value", json.dumps(item, ensure_ascii=False),
                       {"json_pointer": pointer}, {"value_type": type(item).__name__})
    return result


def decode_text(data: bytes) -> str:
    return _decode_text(data)[0]


def _decode_text(data: bytes, *, incomplete_tail: bool = False) -> tuple[str, str]:
    # UTF-16 without a BOM can accept arbitrary even-length GBK/Latin bytes.
    # Only select it using a BOM or a convincing interleaved-NUL pattern.
    def decode(encoding: str) -> tuple[str, str]:
        decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
        return decoder.decode(data, final=not incomplete_tail), encoding

    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return decode("utf-32")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return decode("utf-16")
    sample = data[:4096]
    if len(sample) >= 4 and len(data) % 2 == 0:
        if sample[1::2].count(0) > len(sample) // 4:
            return decode("utf-16-le")
        if sample[::2].count(0) > len(sample) // 4:
            return decode("utf-16-be")
    for encoding in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            return decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeError("Unable to decode document.")


def format_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ").replace("\r", " ").strip()


def clean_text(text: str) -> str:
    normalized = text.replace("\x00", "")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()
