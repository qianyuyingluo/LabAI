"""Shared, JSON-serializable document extraction results and bounded output."""

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any
from zipfile import ZipFile


MAX_OUTPUT_CHARS = 500_000
MAX_BLOCKS = 20_000
MAX_PAGES = 500
MAX_CELLS = 100_000
MAX_ROWS = 10_000
MAX_COLUMNS = 256
MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 32 * 1024 * 1024
MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_BLOCK_METADATA_CHARS = 64_000
MAX_METADATA_CHARS = 1_000_000


@dataclass(slots=True)
class ContentBlock:
    id: str
    kind: str
    text: str
    location: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


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
    blocks: list[ContentBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    parse_status: str = "success"
    metadata: dict[str, Any] = field(default_factory=dict)


class ParseLimitError(ValueError):
    """An input was refused before allocating its expanded representation."""


def check_input_size(path: Path) -> None:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ParseLimitError(f"Input exceeds the {MAX_INPUT_BYTES}-byte parser limit.")


def check_zip_limits(path: Path) -> None:
    check_input_size(path)
    with ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_ENTRIES:
            raise ParseLimitError(f"Office archive exceeds {MAX_ZIP_ENTRIES} entries.")
        if sum(item.file_size for item in entries) > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise ParseLimitError("Office archive exceeds the 100 MiB expanded-size limit.")
        if any(item.file_size > MAX_ZIP_MEMBER_BYTES for item in entries):
            raise ParseLimitError("An Office archive member exceeds the 32 MiB expanded-size limit.")


def render_block(block: ContentBlock) -> str:
    source = ", ".join(f"{key}={value}" for key, value in block.location.items())
    return f"[{block.id} | {block.kind} | {source}]\n{block.text}"


@dataclass
class DocumentContent:
    blocks: list[ContentBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    parse_status: str = "success"
    error: str | None = None
    _chars: int = 0
    _metadata_chars: int = 0
    full: bool = False

    def warn(self, message: str, *, truncated: bool = False) -> None:
        if message not in self.warnings and len(self.warnings) < 100:
            self.warnings.append(message)
        if self.parse_status == "success":
            self.parse_status = "partial"
        if truncated:
            self.metadata["parse_truncated"] = True

    def add(self, kind: str, text: str, location: dict[str, Any],
            metadata: dict[str, Any] | None = None) -> bool:
        if self.full or not text.strip():
            return False
        if len(self.blocks) >= MAX_BLOCKS:
            self.warn(f"Extraction stopped at {MAX_BLOCKS} blocks.", truncated=True)
            self.full = True
            return False
        block_metadata = metadata or {}
        metadata_chars = len(json.dumps(block_metadata, ensure_ascii=False, separators=(",", ":")))
        if metadata_chars > min(MAX_BLOCK_METADATA_CHARS, MAX_METADATA_CHARS - self._metadata_chars):
            self.warn("Some block metadata was omitted at the metadata size limit; consult the original file for complete cell details.", truncated=True)
            block_metadata = {"metadata_truncated": True}
            metadata_chars = 32
        block = ContentBlock(f"b{len(self.blocks) + 1:06d}", kind, text, location, block_metadata)
        separator = 2 if self.blocks else 0
        remaining = MAX_OUTPUT_CHARS - self._chars - separator
        rendered = render_block(block)
        if len(rendered) > remaining:
            self.warn(f"Extraction stopped at {MAX_OUTPUT_CHARS} output characters.", truncated=True)
            self.full = True
            header_size = len(rendered) - len(block.text)
            if remaining <= header_size:
                return False
            block.text = block.text[:remaining - header_size]
            block.metadata["truncated"] = True
            rendered = render_block(block)
        self.blocks.append(block)
        self._chars += len(rendered) + separator
        self._metadata_chars += metadata_chars
        return True

    @property
    def text(self) -> str:
        return "\n\n".join(render_block(block) for block in self.blocks)
