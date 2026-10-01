from app.db.models import UploadedFile
from app.parsers.local_parser import ParsedFileText, parse_uploaded_file
from app.skills.files_skill.schemas import FilesSkillFileResult, FilesSkillInput, FilesSkillResult


def build_files_skill_result(skill_input: FilesSkillInput) -> FilesSkillResult:
    unique_files = _dedupe_files(skill_input.files)
    if not unique_files:
        return FilesSkillResult(status="skipped")

    file_results: list[FilesSkillFileResult] = []

    for uploaded in unique_files:
        if uploaded.file_type == "image":
            continue
        file_results.append(_to_file_result(parse_uploaded_file(uploaded)))

    if not file_results:
        return FilesSkillResult(status="skipped")

    context_parts = [
        "files_skill context",
        (
            "Use the extracted file content below as active context for this chat. "
            "Files are ordered by relevance; files uploaded in the current user turn appear first, "
            "and older chat files follow. "
            "When responding, first restate the document content that matters to the user's request, "
            "then answer the user's question. If the extracted content is incomplete, say so."
        ),
    ]
    base_context = "\n".join(context_parts)
    file_payloads = _build_file_payloads(file_results)
    text_budgets = _allocate_text_budgets(
        [len(payload["text"]) for payload in file_payloads],
        skill_input.max_context_chars - len(base_context) - sum(len(payload["header"]) for payload in file_payloads),
    )
    included_chars = 0
    truncated = False
    errors: list[str] = []

    for payload, text_budget in zip(file_payloads, text_budgets):
        result = payload["result"]
        if result.error:
            errors.append(f"{result.filename}: {result.error}")

        text = payload["text"]
        if text_budget <= 0:
            result.truncated = True
            truncated = True
            context_parts.append(payload["header"] + "[Omitted because files_skill context limit was reached.]")
            continue

        included = text[:text_budget]
        result.included_chars = len(included)
        if len(included) < len(text):
            result.truncated = True
            truncated = True
            included = included.rstrip() + "\n[File content truncated by files_skill context limit.]"
        included_chars += result.included_chars
        context_parts.append(payload["header"] + included)

    model_context = "\n".join(context_parts).strip()
    context_preview = model_context[: skill_input.preview_chars]
    if len(model_context) > skill_input.preview_chars:
        context_preview = context_preview.rstrip() + "\n[files_skill preview truncated.]"

    return FilesSkillResult(
        status="done",
        files=file_results,
        model_context=model_context,
        context_preview=context_preview,
        total_extracted_chars=sum(file.extracted_chars for file in file_results),
        included_chars=included_chars,
        truncated=truncated,
        errors=errors,
    )


def build_files_skill_result_from_outputs(
    *,
    file_results: list[FilesSkillFileResult],
    max_context_chars: int,
    preview_chars: int,
) -> FilesSkillResult:
    if not file_results:
        return FilesSkillResult(status="skipped")

    context_parts = [
        "files_skill per-file model context",
        (
            "Each file below was analyzed in an isolated files_skill model call with no chat "
            "history and no other file content. Use these per-file answers as the active "
            "document context for the main response. Prefer current-turn files first."
        ),
    ]
    base_context = "\n".join(context_parts)
    file_payloads = _build_output_payloads(file_results)
    text_budgets = _allocate_text_budgets(
        [len(payload["text"]) for payload in file_payloads],
        max_context_chars - len(base_context) - sum(len(payload["header"]) for payload in file_payloads),
    )
    included_chars = 0
    truncated = False
    errors: list[str] = []

    for payload, text_budget in zip(file_payloads, text_budgets):
        result = payload["result"]
        if result.error:
            errors.append(f"{result.filename}: {result.error}")
        if result.skill_error:
            errors.append(f"{result.filename}: {result.skill_error}")

        text = payload["text"]
        if text_budget <= 0:
            result.truncated = True
            truncated = True
            context_parts.append(payload["header"] + "[Omitted because files_skill context limit was reached.]")
            continue

        included = text[:text_budget]
        result.included_chars = len(included)
        if len(included) < len(text):
            result.truncated = True
            truncated = True
            included = included.rstrip() + "\n[files_skill output truncated by context limit.]"
        included_chars += result.included_chars
        context_parts.append(payload["header"] + included)

    model_context = "\n".join(context_parts).strip()
    context_preview = model_context[:preview_chars]
    if len(model_context) > preview_chars:
        context_preview = context_preview.rstrip() + "\n[files_skill preview truncated.]"

    return FilesSkillResult(
        status="done",
        files=file_results,
        model_context=model_context,
        context_preview=context_preview,
        total_extracted_chars=sum(file.extracted_chars for file in file_results),
        included_chars=included_chars,
        truncated=truncated,
        errors=errors,
    )


def _dedupe_files(files: list[UploadedFile]) -> list[UploadedFile]:
    seen: set[str] = set()
    unique: list[UploadedFile] = []
    for uploaded in files:
        file_id = getattr(uploaded, "id", "")
        if not file_id or file_id in seen:
            continue
        unique.append(uploaded)
        seen.add(file_id)
    return unique


def _to_file_result(parsed: ParsedFileText) -> FilesSkillFileResult:
    return FilesSkillFileResult(
        file_id=parsed.file_id,
        filename=parsed.filename,
        file_type=parsed.file_type,
        mime_type=parsed.mime_type,
        size=parsed.size,
        extracted_text=parsed.text,
        extracted_chars=parsed.text_chars,
        error=parsed.error,
    )


def _build_file_payloads(file_results: list[FilesSkillFileResult]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for index, result in enumerate(file_results, start=1):
        header = (
            f"\n\n[File {index}: {result.filename}]\n"
            f"file_id: {result.file_id}\n"
            f"type: {result.file_type}\n"
            f"mime: {result.mime_type}\n"
            f"size_bytes: {result.size}\n"
            "content:\n"
        )
        text = result.extracted_text or f"[Extraction unavailable: {result.error or 'empty content'}]"
        payloads.append({"result": result, "header": header, "text": text})
    return payloads


def _build_output_payloads(file_results: list[FilesSkillFileResult]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for index, result in enumerate(file_results, start=1):
        header = (
            f"\n\n[File {index}: {result.filename}]\n"
            f"file_id: {result.file_id}\n"
            f"type: {result.file_type}\n"
            f"mime: {result.mime_type}\n"
            f"size_bytes: {result.size}\n"
            "isolated_files_skill_answer:\n"
        )
        text = result.skill_output.strip()
        if not text:
            text = (
                "[files_skill model answer unavailable; extracted preview follows]\n"
                + (result.extracted_text or f"[Extraction unavailable: {result.error or 'empty content'}]")
            )
        payloads.append({"result": result, "header": header, "text": text})
    return payloads


def _allocate_text_budgets(text_lengths: list[int], total_budget: int) -> list[int]:
    if total_budget <= 0 or not text_lengths:
        return [0 for _ in text_lengths]

    budgets = [0 for _ in text_lengths]
    remaining = total_budget
    active = [index for index, length in enumerate(text_lengths) if length > 0]

    while remaining > 0 and active:
        share = max(1, remaining // len(active))
        next_active: list[int] = []
        progressed = False
        for index in active:
            need = text_lengths[index] - budgets[index]
            if need <= 0:
                continue
            addition = min(need, share, remaining)
            budgets[index] += addition
            remaining -= addition
            progressed = progressed or addition > 0
            if budgets[index] < text_lengths[index]:
                next_active.append(index)
            if remaining <= 0:
                break
        if not progressed:
            break
        active = next_active

    return budgets
