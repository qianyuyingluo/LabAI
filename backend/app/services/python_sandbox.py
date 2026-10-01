from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import UploadedFile
from app.services.file_service import GENERATED_EXTENSIONS, collect_chat_file_ids, get_uploaded_file


SANDBOX_NOT_READY = "SANDBOX_NOT_READY"
SANDBOX_POLICY_VIOLATION = "SANDBOX_POLICY_VIOLATION"
SANDBOX_OUTPUT_INVALID = "SANDBOX_OUTPUT_INVALID"

_SANDBOX_READY_IMPORTS = (
    "numpy",
    "pandas",
    "scipy",
    "matplotlib",
    "seaborn",
    "openpyxl",
    "xlsxwriter",
    "docx",
    "pptx",
    "PIL",
    "pypdf",
    "reportlab",
    "xlrd",
)
_SANDBOX_READINESS_TIMEOUT_SECONDS = 15
_SANDBOX_READINESS_FAILURE_TTL_SECONDS = 5
_SANDBOX_READINESS_CACHE_LOCK = threading.Lock()
_SANDBOX_READINESS_CACHE: tuple[tuple[tuple[str, int, int], ...], bool, float] | None = None

_DENIED_IMPORTS = {
    "ctypes",
    "ensurepip",
    "ftplib",
    "http",
    "httpx",
    "importlib",
    "multiprocessing",
    "pip",
    "requests",
    "smtplib",
    "socket",
    "subprocess",
    "telnetlib",
    "urllib",
    "venv",
    "webbrowser",
    "winreg",
}
_NETWORK_IMPORTS = {
    "ftplib",
    "http",
    "httpx",
    "requests",
    "smtplib",
    "socket",
    "telnetlib",
    "urllib",
}
_DENIED_CALLS = {"__import__", "compile", "eval", "exec"}
_DENIED_OS_CALLS = {
    "execl",
    "execle",
    "execlp",
    "execlpe",
    "execv",
    "execve",
    "execvp",
    "execvpe",
    "popen",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
    "startfile",
    "system",
}
_WINDOWS_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


@dataclass(slots=True)
class SandboxInput:
    file_id: str
    name: str
    path: str
    mime_type: str
    size: int


@dataclass(slots=True)
class SandboxOutput:
    name: str
    relative_path: str
    path: Path
    size: int


@dataclass(slots=True)
class SandboxExecutionResult:
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    memory_limited: bool = False
    output_limited: bool = False
    policy_error: str | None = None
    outputs: list[dict[str, object]] | None = None

    def to_tool_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["outputs"] = self.outputs or []
        return payload


class PythonSandboxWorkspace:
    def __init__(self, *, message_id: str, root: Path, inputs: list[SandboxInput]) -> None:
        self.message_id = message_id
        self.root = root
        self.inputs_dir = root / "inputs"
        self.outputs_dir = root / "outputs"
        self.temp_dir = root / "tmp"
        self.inputs = inputs
        self._execution_index = 0

    @property
    def prompt_context(self) -> str:
        manifest = [
            {
                "fileId": item.file_id,
                "name": item.name,
                "path": item.path,
                "mimeType": item.mime_type,
                "size": item.size,
            }
            for item in self.inputs
        ]
        return "Python sandbox workspace files:\n" + json.dumps(manifest, ensure_ascii=False, indent=2)

    async def execute(self, code: str) -> SandboxExecutionResult:
        settings = get_settings()
        if len(code.encode("utf-8")) > settings.sandbox_max_code_kb * 1024:
            return SandboxExecutionResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr="",
                policy_error=f"Python code exceeds {settings.sandbox_max_code_kb} KB.",
            )

        policy_error = _validate_code_policy(code, network_enabled=settings.sandbox_network_enabled)
        if policy_error:
            return SandboxExecutionResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr="",
                policy_error=policy_error,
            )

        python = sandbox_python_path()
        if python is None:
            raise AppError(
                SANDBOX_NOT_READY,
                "Python sandbox is not ready. Start LabAI with npm start.",
                status_code=503,
            )

        self._execution_index += 1
        source_path = self.root / f"user_code_{self._execution_index}.py"
        runner_path = self.root / "sandbox_runner.py"
        stdout_path = self.root / f"stdout_{self._execution_index}.log"
        stderr_path = self.root / f"stderr_{self._execution_index}.log"
        source_path.write_text(code, encoding="utf-8")
        if not runner_path.exists():
            runner_path.write_text(_runner_source(settings.sandbox_network_enabled), encoding="utf-8")

        env = _sandbox_environment(python, self.root, self.temp_dir)
        process_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_kwargs["start_new_session"] = True

        timed_out = False
        memory_limited = False
        output_limited = False
        async with _sandbox_semaphore():
            with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
                process = await asyncio.create_subprocess_exec(
                    str(python),
                    "-I",
                    "-B",
                    "-u",
                    str(runner_path),
                    str(source_path),
                    cwd=str(self.root),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    **process_kwargs,
                )
                monitor_state = {"memory_limited": False, "output_limited": False}
                monitor = asyncio.create_task(
                    _monitor_process(
                        process,
                        stdout_path=stdout_path,
                        stderr_path=stderr_path,
                        outputs_dir=self.outputs_dir,
                        state=monitor_state,
                    )
                )
                try:
                    await asyncio.wait_for(process.wait(), timeout=settings.sandbox_timeout_seconds)
                except TimeoutError:
                    timed_out = True
                    await _terminate_process_tree(process)
                finally:
                    monitor.cancel()
                    try:
                        await monitor
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass
                memory_limited = bool(monitor_state.get("memory_limited"))
                output_limited = bool(monitor_state.get("output_limited"))

        stdout, stdout_truncated = _read_limited(stdout_path, settings.sandbox_max_stdio_kb * 1024)
        stderr, stderr_truncated = _read_limited(stderr_path, settings.sandbox_max_stdio_kb * 1024)
        stdout = _sanitize_runtime_output(stdout, root=self.root, python=python)
        stderr = _sanitize_runtime_output(stderr, root=self.root, python=python)
        if stdout_truncated:
            stdout += "\n[stdout truncated]"
        if stderr_truncated:
            stderr += "\n[stderr truncated]"

        outputs: list[dict[str, object]] = []
        try:
            outputs = [
                {"name": output.name, "path": output.relative_path, "size": output.size}
                for output in self.collect_outputs()
            ]
        except AppError as exc:
            stderr = "\n".join(part for part in [stderr, exc.message] if part)
            output_limited = exc.code == "SANDBOX_OUTPUT_LIMIT"

        exit_code = process.returncode
        ok = (
            exit_code == 0
            and not timed_out
            and not memory_limited
            and not output_limited
        )
        return SandboxExecutionResult(
            ok=ok,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            memory_limited=memory_limited,
            output_limited=output_limited,
            outputs=outputs,
        )

    def collect_outputs(self) -> list[SandboxOutput]:
        settings = get_settings()
        root = self.outputs_dir.resolve()
        candidates = sorted(path for path in self.outputs_dir.rglob("*") if path.is_file())
        if len(candidates) > settings.sandbox_max_output_files:
            raise AppError(
                "SANDBOX_OUTPUT_LIMIT",
                f"Sandbox produced more than {settings.sandbox_max_output_files} files.",
                status_code=413,
            )

        outputs: list[SandboxOutput] = []
        total_size = 0
        for path in candidates:
            resolved = path.resolve()
            if not _is_relative_to(resolved, root) or _is_link_or_reparse(path):
                raise AppError(
                    SANDBOX_OUTPUT_INVALID,
                    "Sandbox output escaped the output directory.",
                    status_code=400,
                )
            extension = path.suffix.lower().lstrip(".")
            if extension not in GENERATED_EXTENSIONS:
                raise AppError(
                    SANDBOX_OUTPUT_INVALID,
                    f"Generated .{extension or '(no extension)'} files are not allowed.",
                    status_code=415,
                )
            size = path.stat().st_size
            if size <= 0:
                raise AppError(SANDBOX_OUTPUT_INVALID, f"{path.name} is empty.", status_code=415)
            if size > settings.sandbox_max_output_file_mb * 1024 * 1024:
                raise AppError("SANDBOX_OUTPUT_LIMIT", f"{path.name} is too large.", status_code=413)
            total_size += size
            if total_size > settings.sandbox_max_output_total_mb * 1024 * 1024:
                raise AppError("SANDBOX_OUTPUT_LIMIT", "Sandbox outputs are too large.", status_code=413)
            _validate_generated_file(path, extension)
            relative = path.relative_to(self.root).as_posix()
            outputs.append(SandboxOutput(path=path, relative_path=relative, name=path.name, size=size))
        return outputs

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class PythonSandboxService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    def create_workspace(
        self,
        *,
        message_id: str,
        chat_id: str,
        current_file_ids: list[str],
    ) -> PythonSandboxWorkspace:
        if sandbox_python_path() is None:
            raise AppError(
                SANDBOX_NOT_READY,
                "Python sandbox is not ready. Start LabAI with npm start.",
                status_code=503,
            )
        root = self.settings.sandbox_runs_dir / _safe_component(message_id)
        shutil.rmtree(root, ignore_errors=True)
        inputs_dir = root / "inputs"
        outputs_dir = root / "outputs"
        temp_dir = root / "tmp"
        for directory in (inputs_dir, outputs_dir, temp_dir):
            directory.mkdir(parents=True, exist_ok=True)

        file_ids = collect_chat_file_ids(
            self.db,
            chat_id,
            current_file_ids=current_file_ids,
            include_generated=True,
        )
        inputs: list[SandboxInput] = []
        total_size = 0
        used_names: set[str] = set()
        storage_root = self.settings.storage_dir.resolve()
        for file_id in file_ids:
            if len(inputs) >= self.settings.sandbox_max_input_files:
                break
            try:
                uploaded = get_uploaded_file(self.db, file_id)
            except AppError:
                continue
            source = Path(uploaded.local_path)
            try:
                resolved_source = source.resolve()
                actual_size = resolved_source.stat().st_size
            except OSError:
                continue
            try:
                invalid_source = (
                    not resolved_source.is_file()
                    or _is_link_or_reparse(source)
                    or not _is_relative_to(resolved_source, storage_root)
                )
            except OSError:
                continue
            if invalid_source:
                continue
            if total_size + actual_size > self.settings.sandbox_max_input_mb * 1024 * 1024:
                continue
            name = _unique_filename(uploaded, used_names)
            destination = inputs_dir / name
            shutil.copy2(resolved_source, destination)
            try:
                destination.chmod(stat.S_IREAD)
            except OSError:
                pass
            total_size += actual_size
            inputs.append(
                SandboxInput(
                    file_id=uploaded.id,
                    name=uploaded.original_filename,
                    path=f"inputs/{name}",
                    mime_type=uploaded.mime_type,
                    size=actual_size,
                )
            )

        manifest = [asdict(item) for item in inputs]
        (inputs_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return PythonSandboxWorkspace(message_id=message_id, root=root, inputs=inputs)


def sandbox_python_path() -> Path | None:
    configured = get_settings().labai_sandbox_python
    if not configured:
        return None
    path = Path(configured).expanduser().resolve()
    return path if path.is_file() else None


def _sandbox_environment_signature(python: Path) -> tuple[tuple[str, int, int], ...]:
    venv_root = python.parent.parent
    candidates = [python, venv_root / ".labai-environment.json", venv_root / "Lib" / "site-packages"]
    candidates.extend(sorted((venv_root / "lib").glob("python*/site-packages")))
    signature: list[tuple[str, int, int]] = []
    for candidate in candidates:
        try:
            stat_result = candidate.stat()
        except OSError:
            signature.append((str(candidate), -1, -1))
            continue
        signature.append((str(candidate), stat_result.st_mtime_ns, stat_result.st_size))
    return tuple(signature)


def _probe_sandbox_environment(python: Path) -> bool:
    probe = (
        "import importlib, sys\n"
        "if sys.version_info < (3, 11): raise SystemExit(2)\n"
        f"modules = {_SANDBOX_READY_IMPORTS!r}\n"
        "[importlib.import_module(name) for name in modules]\n"
    )
    env = os.environ.copy()
    env.update({"MPLBACKEND": "Agg", "PYTHONNOUSERSITE": "1"})
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [str(python), "-c", probe],
            check=False,
            cwd=str(python.parent.parent),
            env=env,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            timeout=_SANDBOX_READINESS_TIMEOUT_SECONDS,
            **kwargs,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _reset_sandbox_readiness_cache() -> None:
    global _SANDBOX_READINESS_CACHE
    with _SANDBOX_READINESS_CACHE_LOCK:
        _SANDBOX_READINESS_CACHE = None


def sandbox_ready() -> bool:
    global _SANDBOX_READINESS_CACHE
    python = sandbox_python_path()
    if python is None:
        _reset_sandbox_readiness_cache()
        return False
    try:
        signature = _sandbox_environment_signature(python)
    except OSError:
        return False

    now = time.monotonic()
    with _SANDBOX_READINESS_CACHE_LOCK:
        cached = _SANDBOX_READINESS_CACHE
        if cached is not None and cached[0] == signature:
            _, ready, checked_at = cached
            if ready or now - checked_at < _SANDBOX_READINESS_FAILURE_TTL_SECONDS:
                return ready
        ready = _probe_sandbox_environment(python)
        _SANDBOX_READINESS_CACHE = (signature, ready, time.monotonic())
        return ready


def cleanup_stale_workspaces() -> None:
    runs_dir = get_settings().sandbox_runs_dir
    if not runs_dir.is_dir():
        return
    for child in runs_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)


_semaphore: asyncio.Semaphore | None = None
_semaphore_size: int | None = None


def _sandbox_semaphore() -> asyncio.Semaphore:
    global _semaphore, _semaphore_size
    size = max(1, get_settings().sandbox_max_concurrent_runs)
    if _semaphore is None or _semaphore_size != size:
        _semaphore = asyncio.Semaphore(size)
        _semaphore_size = size
    return _semaphore


def _validate_code_policy(code: str, *, network_enabled: bool) -> str | None:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return f"Python syntax error at line {exc.lineno}: {exc.msg}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _DENIED_IMPORTS and not (network_enabled and root in _NETWORK_IMPORTS):
                    return f"Importing '{root}' is blocked by the sandbox policy."
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in _DENIED_IMPORTS and not (network_enabled and root in _NETWORK_IMPORTS):
                return f"Importing '{root}' is blocked by the sandbox policy."
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _DENIED_CALLS:
                return f"Calling '{node.func.id}' is blocked by the sandbox policy."
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in _DENIED_CALLS:
                    return f"Calling '{node.func.attr}' is blocked by the sandbox policy."
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    if node.func.attr in _DENIED_OS_CALLS:
                        return f"Calling 'os.{node.func.attr}' is blocked by the sandbox policy."
    return None


def _runner_source(network_enabled: bool) -> str:
    audit_guard = _AUDIT_GUARD.replace("__NETWORK_ENABLED__", repr(network_enabled))
    return f"""import os
import runpy
import sys
{audit_guard}

def _blocked_process(*args, **kwargs):
    raise PermissionError("Child processes and shell commands are disabled in the LabAI sandbox.")

import subprocess as _subprocess
for _name in ("Popen", "run", "call", "check_call", "check_output"):
    setattr(_subprocess, _name, _blocked_process)
for _name in {sorted(_DENIED_OS_CALLS)!r}:
    if hasattr(os, _name):
        setattr(os, _name, _blocked_process)

if len(sys.argv) != 2:
    raise SystemExit("sandbox runner expected one source path")
runpy.run_path(sys.argv[1], run_name="__main__")
"""


_AUDIT_GUARD = r"""
_LABAI_WORKSPACE = os.path.realpath(os.getcwd())
_LABAI_WRITE_ROOTS = tuple(
    os.path.realpath(os.path.join(_LABAI_WORKSPACE, name))
    for name in ("outputs", "tmp")
)
_LABAI_READ_ROOTS = [_LABAI_WORKSPACE]
_LABAI_READ_FILES = set()
for _entry in sys.path:
    if not _entry:
        continue
    _entry = os.path.realpath(_entry)
    if os.path.isdir(_entry):
        _LABAI_READ_ROOTS.append(_entry)
    elif os.path.isfile(_entry):
        _LABAI_READ_FILES.add(_entry)
_windows_dir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
if _windows_dir:
    _fonts_dir = os.path.realpath(os.path.join(_windows_dir, "Fonts"))
    if os.path.isdir(_fonts_dir):
        _LABAI_READ_ROOTS.append(_fonts_dir)
_LABAI_READ_ROOTS = tuple(dict.fromkeys(_LABAI_READ_ROOTS))
_LABAI_NETWORK_ENABLED = __NETWORK_ENABLED__


def _labai_resolve_path(value):
    if isinstance(value, int):
        return None
    try:
        return os.path.realpath(os.path.abspath(os.fsdecode(os.fspath(value))))
    except (TypeError, ValueError, OSError):
        raise PermissionError("The sandbox rejected an invalid filesystem path.")


def _labai_is_within(path, roots):
    if path is None:
        return True
    for root in roots:
        try:
            if os.path.commonpath((path, root)) == root:
                return True
        except (ValueError, OSError):
            continue
    return False


def _labai_require_read(path):
    resolved = _labai_resolve_path(path)
    if resolved in _LABAI_READ_FILES or _labai_is_within(resolved, _LABAI_READ_ROOTS):
        return
    raise PermissionError("Reading paths outside the sandbox workspace is blocked.")


def _labai_require_write(path):
    resolved = _labai_resolve_path(path)
    if _labai_is_within(resolved, _LABAI_WRITE_ROOTS):
        return
    raise PermissionError("Writing paths outside outputs/ and tmp/ is blocked.")


def _labai_open_is_write(mode, flags):
    if isinstance(mode, str) and any(marker in mode for marker in ("w", "a", "x", "+")):
        return True
    if isinstance(flags, int):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        return bool(flags & write_flags)
    return False


def _labai_audit(event, args):
    if not _LABAI_NETWORK_ENABLED and event.startswith("socket."):
        raise PermissionError("Network access is disabled in the LabAI sandbox.")
    if (
        event.startswith("subprocess.")
        or event in {"os.system", "os.posix_spawn", "os.startfile"}
        or event.startswith("os.exec")
        or event.startswith("os.spawn")
    ):
        raise PermissionError("Child processes and shell commands are disabled in the LabAI sandbox.")
    if event == "open" and args:
        path = args[0]
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else None
        if _labai_open_is_write(mode, flags):
            _labai_require_write(path)
        else:
            _labai_require_read(path)
        return
    if event in {"os.listdir", "os.scandir"} and args:
        _labai_require_read(args[0])
        return
    if event == "os.chdir" and args:
        resolved = _labai_resolve_path(args[0])
        if not _labai_is_within(resolved, (_LABAI_WORKSPACE,)):
            raise PermissionError("Changing directory outside the sandbox workspace is blocked.")
        return
    if event in {
        "os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown",
        "os.truncate", "os.utime",
    } and args:
        _labai_require_write(args[0])
        return
    if event in {"os.rename", "os.replace", "os.link", "os.symlink"} and len(args) >= 2:
        _labai_require_write(args[0])
        _labai_require_write(args[1])


sys.addaudithook(_labai_audit)
"""


def _sandbox_environment(python: Path, root: Path, temp_dir: Path) -> dict[str, str]:
    env = {
        "HOME": str(root),
        "USERPROFILE": str(root),
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(temp_dir / "matplotlib"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONNOUSERSITE": "1",
        "PATH": str(python.parent),
    }
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
        if system_root:
            env["SystemRoot"] = system_root
            env["WINDIR"] = system_root
            env["PATH"] = os.pathsep.join([str(python.parent), str(Path(system_root) / "System32")])
    return env


async def _monitor_process(
    process: asyncio.subprocess.Process,
    *,
    stdout_path: Path,
    stderr_path: Path,
    outputs_dir: Path,
    state: dict[str, bool],
) -> None:
    settings = get_settings()
    while process.returncode is None:
        output_bytes = _size_or_zero(stdout_path) + _size_or_zero(stderr_path)
        if output_bytes > settings.sandbox_max_stdio_kb * 1024 * 4:
            state["output_limited"] = True
            await _terminate_process_tree(process)
            return
        if _outputs_exceed_limits(outputs_dir):
            state["output_limited"] = True
            await _terminate_process_tree(process)
            return
        memory = _process_tree_memory(process.pid)
        if memory is not None and memory > settings.sandbox_max_memory_mb * 1024 * 1024:
            state["memory_limited"] = True
            await _terminate_process_tree(process)
            return
        await asyncio.sleep(0.2)


def _outputs_exceed_limits(outputs_dir: Path) -> bool:
    settings = get_settings()
    max_files = settings.sandbox_max_output_files
    max_file_bytes = settings.sandbox_max_output_file_mb * 1024 * 1024
    max_total_bytes = settings.sandbox_max_output_total_mb * 1024 * 1024
    count = 0
    total = 0
    try:
        for directory, child_dirs, filenames in os.walk(outputs_dir, followlinks=False):
            child_dirs[:] = [
                name for name in child_dirs if not (Path(directory) / name).is_symlink()
            ]
            for filename in filenames:
                count += 1
                if count > max_files:
                    return True
                try:
                    size = (Path(directory) / filename).stat().st_size
                except OSError:
                    continue
                if size > max_file_bytes:
                    return True
                total += size
                if total > max_total_bytes:
                    return True
    except OSError:
        return False
    return False


def _process_tree_memory(pid: int) -> int | None:
    try:
        import psutil

        process = psutil.Process(pid)
        children = process.children(recursive=True)
        return process.memory_info().rss + sum(child.memory_info().rss for child in children)
    except Exception:
        return None


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    try:
        import psutil

        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in children:
            child.kill()
        parent.kill()
    except Exception:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except Exception:
        pass


def _read_limited(path: Path, limit: int) -> tuple[str, bool]:
    if not path.exists():
        return "", False
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    truncated = len(data) > limit
    return data[:limit].decode("utf-8", errors="replace"), truncated


def _sanitize_runtime_output(value: str, *, root: Path, python: Path) -> str:
    if not value:
        return value
    sanitized = value
    replacements = (
        (str(root.resolve()), "<workspace>"),
        (str(python.parent.parent.resolve()), "<sandbox-env>"),
    )
    for original, replacement in replacements:
        sanitized = sanitized.replace(original, replacement)
        sanitized = sanitized.replace(original.replace("\\", "/"), replacement)

    def replace_traceback_path(match: re.Match[str]) -> str:
        raw_path = match.group(1)
        if raw_path.startswith(("<workspace>", "<sandbox-env>")):
            return match.group(0)
        name = Path(raw_path).name or "library"
        return f'File "<sandbox-library>/{name}"'

    return re.sub(r'File "((?:[A-Za-z]:[\\/]|/)[^"]+)"', replace_traceback_path, sanitized)


def _validate_generated_file(path: Path, extension: str) -> None:
    if extension in {"docx", "xlsx", "pptx"}:
        required = {
            "docx": "word/document.xml",
            "xlsx": "xl/workbook.xml",
            "pptx": "ppt/presentation.xml",
        }[extension]
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names or required not in names:
                    raise ValueError("required OOXML parts are missing")
        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            raise AppError(SANDBOX_OUTPUT_INVALID, f"{path.name} is not a valid {extension} file.") from exc
    elif extension == "json":
        try:
            json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AppError(SANDBOX_OUTPUT_INVALID, f"{path.name} is not valid JSON.") from exc
    elif extension in {"csv", "tsv", "txt", "md"}:
        try:
            path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise AppError(SANDBOX_OUTPUT_INVALID, f"{path.name} must be UTF-8 text.") from exc
    elif extension == "png":
        if not path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n":
            raise AppError(SANDBOX_OUTPUT_INVALID, f"{path.name} is not a valid PNG file.")
    elif extension in {"jpg", "jpeg"}:
        raw = path.read_bytes()
        if not (raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")):
            raise AppError(SANDBOX_OUTPUT_INVALID, f"{path.name} is not a valid JPEG file.")
    elif extension == "pdf":
        if not path.read_bytes()[:5] == b"%PDF-":
            raise AppError(SANDBOX_OUTPUT_INVALID, f"{path.name} is not a valid PDF file.")
        try:
            from pypdf import PdfReader

            PdfReader(str(path))
        except Exception as exc:
            raise AppError(SANDBOX_OUTPUT_INVALID, f"{path.name} is not a readable PDF file.") from exc
    elif extension == "svg":
        _sanitize_svg(path)


def _sanitize_svg(path: Path) -> None:
    import xml.etree.ElementTree as ET

    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError) as exc:
        raise AppError(SANDBOX_OUTPUT_INVALID, f"{path.name} is not valid SVG.") from exc
    root = tree.getroot()
    if not root.tag.lower().endswith("svg"):
        raise AppError(SANDBOX_OUTPUT_INVALID, f"{path.name} is not valid SVG.")
    for parent in list(root.iter()):
        for child in list(parent):
            tag = child.tag.lower() if isinstance(child.tag, str) else ""
            if tag.endswith(("script", "foreignobject", "style")):
                parent.remove(child)
        for key, value in list(parent.attrib.items()):
            normalized = key.lower().rsplit("}", 1)[-1]
            lowered = value.strip().lower()
            is_external_reference = normalized == "href" and not (
                not lowered or lowered.startswith("#")
            )
            is_unsafe_style = normalized == "style" and (
                "url(" in lowered or "@import" in lowered
            )
            if (
                normalized.startswith("on")
                or is_external_reference
                or is_unsafe_style
                or lowered.startswith(("javascript:", "http:", "https:", "//", "data:"))
            ):
                del parent.attrib[key]
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _unique_filename(uploaded: UploadedFile, used: set[str]) -> str:
    original = _safe_component(uploaded.original_filename, fallback=f"input-{uploaded.id}")
    stem = Path(original).stem or "input"
    suffix = Path(original).suffix
    candidate = original
    index = 2
    while candidate.lower() in used:
        candidate = f"{stem}-{index}{suffix}"
        index += 1
    used.add(candidate.lower())
    return candidate


def _safe_component(value: str, *, fallback: str = "sandbox-run") -> str:
    cleaned = _WINDOWS_INVALID_NAME.sub("_", value).strip(" .")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = fallback
    if Path(cleaned).stem.rstrip(" .").upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    if len(cleaned) > 180:
        suffix = Path(cleaned).suffix
        if len(suffix) > 20:
            suffix = ""
        cleaned = f"{Path(cleaned).stem[: 180 - len(suffix)]}{suffix}"
    return cleaned


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _size_or_zero(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0
