"""Content-addressed local parse cache with bounded, killable worker processes."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from dataclasses import asdict, fields
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import SimpleNamespace

from app.core.config import BACKEND_DIR, get_settings
from app.parsers.local_parser import ParsedFileText
from app.parsers.types import ContentBlock


CACHE_SCHEMA = "structured-document-v1"
MAX_CACHE_BYTES = 64 * 1024 * 1024
FILE_FIELDS = (
    "id", "original_filename", "stored_filename", "mime_type", "extension",
    "size", "sha256", "local_path", "file_type",
)


def snapshot_uploaded_file(uploaded: object) -> SimpleNamespace:
    """Read ORM attributes before dispatching to a thread/child process."""
    return SimpleNamespace(**{name: getattr(uploaded, name, "") for name in FILE_FIELDS})


@lru_cache(maxsize=8)
def _limiter(concurrent: int) -> threading.BoundedSemaphore:
    return threading.BoundedSemaphore(concurrent)


@lru_cache(maxsize=256)
def _cache_lock(key: str) -> threading.Lock:
    return threading.Lock()


@lru_cache(maxsize=1)
def _parser_signature() -> str:
    digest = hashlib.sha256(CACHE_SCHEMA.encode())
    for source in sorted((BACKEND_DIR / "app" / "parsers").glob("*.py")):
        digest.update(source.name.encode())
        digest.update(source.read_bytes())
    for package in ("pypdf", "openpyxl", "python-docx", "python-pptx"):
        try:
            installed = version(package)
        except PackageNotFoundError:
            installed = "missing"
        digest.update(f"{package}:{installed}".encode())
    return digest.hexdigest()


def _failure(uploaded: object, message: str) -> ParsedFileText:
    return ParsedFileText(
        file_id=uploaded.id, filename=uploaded.original_filename,
        file_type=uploaded.file_type, mime_type=uploaded.mime_type,
        size=uploaded.size, error=message, parse_status="error",
    )


def _decode_result(payload: dict, uploaded: object) -> ParsedFileText:
    if not isinstance(payload, dict):
        raise ValueError("Invalid parse result")
    if payload.get("parse_status") not in {"success", "partial", "unsupported", "error"}:
        raise ValueError("Invalid parse status")
    if not isinstance(payload.get("text"), str) or not isinstance(payload.get("metadata"), dict):
        raise ValueError("Invalid parse content")
    if payload.get("error") is not None and not isinstance(payload["error"], str):
        raise ValueError("Invalid parse error")
    warnings = payload.get("warnings")
    blocks = payload.get("blocks")
    if not isinstance(warnings, list) or not all(isinstance(value, str) for value in warnings):
        raise ValueError("Invalid parse warnings")
    if not isinstance(blocks, list) or len(blocks) > 20000:
        raise ValueError("Invalid parse blocks")
    for block in blocks:
        if (not isinstance(block, dict)
                or not all(isinstance(block.get(key), str) for key in ("id", "kind", "text"))
                or not isinstance(block.get("location"), dict)
                or not isinstance(block.get("metadata"), dict)):
            raise ValueError("Invalid source block")
    allowed = {field.name for field in fields(ParsedFileText)}
    values = {key: value for key, value in payload.items() if key in allowed}
    values["blocks"] = [ContentBlock(**block) for block in payload.get("blocks", [])]
    values["text_chars"] = len(payload["text"])
    # Cache identity is content, but references always identify this upload.
    values.update(file_id=uploaded.id, filename=uploaded.original_filename,
                  file_type=uploaded.file_type, mime_type=uploaded.mime_type, size=uploaded.size)
    return ParsedFileText(**values)


def _read_cache(path: Path, uploaded: object) -> ParsedFileText | None:
    try:
        if path.stat().st_size > MAX_CACHE_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != CACHE_SCHEMA:
            return None
        return _decode_result(payload["result"], uploaded)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _run_worker(uploaded: object, output: Path, timeout: int) -> ParsedFileText:
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "LANG", "LC_ALL"}}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "app.parsers.worker"],
            input=json.dumps({"file": vars(snapshot_uploaded_file(uploaded)), "output": str(output)}),
            text=True, encoding="utf-8", cwd=str(BACKEND_DIR), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False, **options,
        )
        if completed.returncode != 0 or not output.is_file():
            return _failure(uploaded, "Document parser process failed; retry or inspect the source file.")
        if output.stat().st_size > MAX_CACHE_BYTES:
            return _failure(uploaded, "Structured extraction exceeded the local output size limit.")
        return _decode_result(json.loads(output.read_text(encoding="utf-8")), uploaded)
    except subprocess.TimeoutExpired:
        return _failure(uploaded, f"Document parsing timed out after {timeout} seconds; no complete extraction is available.")
    except (OSError, ValueError, TypeError, KeyError):
        return _failure(uploaded, "Document parser returned an invalid result.")


def get_parsed_file(uploaded: object) -> ParsedFileText:
    """Synchronous API: call from a worker thread in asynchronous HTTP handlers."""
    uploaded = snapshot_uploaded_file(uploaded)
    source = Path(uploaded.local_path)
    settings = get_settings()
    if not source.is_file():
        return _failure(uploaded, "File is missing on disk.")
    try:
        before = source.stat()
        if before.st_size > settings.max_file_upload_bytes:
            return _failure(uploaded, "File exceeds the document reader size limit.")
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        identity = f"{digest.hexdigest()}:{uploaded.extension}:{uploaded.file_type}:{_parser_signature()}"
        key = hashlib.sha256(identity.encode()).hexdigest()
        cache_dir = settings.storage_dir / "parsed"
        cache_dir.mkdir(parents=True, exist_ok=True)
        destination = cache_dir / f"{key}.json"
        with _cache_lock(str(destination)):
            cached = _read_cache(destination, uploaded)
            if cached is not None:
                return cached
            with _limiter(settings.file_parse_max_concurrent):
                with tempfile.TemporaryDirectory(prefix="parse-", dir=cache_dir) as scratch:
                    result = _run_worker(uploaded, Path(scratch) / "result.json", settings.file_parse_timeout_seconds)
                    after = source.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        return _failure(uploaded, "File changed during parsing; retry the operation.")
                    # Transient errors/timeouts must remain retryable.
                    if result.parse_status != "error":
                        staged = Path(scratch) / "cache.json"
                        staged.write_text(json.dumps({"schema": CACHE_SCHEMA, "result": asdict(result)},
                                                     ensure_ascii=False), encoding="utf-8")
                        os.replace(staged, destination)
                    return result
    except OSError:
        return _failure(uploaded, "Unable to read the file or write its local parse cache.")
