import asyncio
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.parsers.types import ContentBlock, ParsedFileText
from app.services.document_tools import execute_document_tool
from app.skills.files_skill.context import build_files_skill_result
from app.skills.files_skill.schemas import FilesSkillInput
from app.skills.files_skill.service import FilesSkillService


def parsed_file(texts, *, file_type="text", status="success", warnings=None, parse_truncated=False):
    blocks = [ContentBlock(f"b{index + 1:06d}", "paragraph", text, {"page": index + 1}) for index, text in enumerate(texts)]
    text = "\n".join(texts)
    return ParsedFileText(
        file_id="file-1", filename="source.txt", file_type=file_type, mime_type="text/plain",
        size=len(text), text=text, text_chars=len(text), blocks=blocks, parse_status=status,
        warnings=warnings or [], metadata={"parse_truncated": parse_truncated},
    )


def uploaded_file(file_type="text"):
    return SimpleNamespace(
        id="file-1", original_filename="source.txt", stored_filename="stored.txt",
        mime_type="text/plain", extension="txt", size=1, sha256="hash",
        local_path="unused.txt", file_type=file_type,
    )


class DocumentContextTests(unittest.TestCase):
    def build(self, parsed, cap=60000, query=""):
        with patch("app.skills.files_skill.context.get_parsed_file", return_value=parsed):
            return build_files_skill_result(FilesSkillInput(
                files=[uploaded_file(parsed.file_type)], max_context_chars=cap, user_message=query,
            ))

    def test_40000_character_document_is_complete_without_model_call(self):
        parsed = parsed_file(["A" * 39990 + "TAIL_MARKER"])
        main_thread = threading.get_ident()
        parse_threads = []
        service = object.__new__(FilesSkillService)
        service.settings = SimpleNamespace(max_files_skill_context_chars=60000, files_skill_preview_chars=4000)

        def load(**kwargs):
            self.assertEqual(threading.get_ident(), main_thread)
            return [uploaded_file()]

        def parse(snapshot):
            parse_threads.append(threading.get_ident())
            self.assertIsInstance(snapshot, SimpleNamespace)
            return parsed

        service._load_files = load
        with patch("app.skills.files_skill.context.get_parsed_file", side_effect=parse), patch(
            "app.adapters.registry.get_adapter_for_profile", side_effect=AssertionError("No preliminary model call allowed")
        ) as adapter:
            result = asyncio.run(service.run_with_model(
                chat_id="chat", file_ids=["file-1"], profile=SimpleNamespace(), user_message="read it",
            ))
        adapter.assert_not_called()
        self.assertNotEqual(parse_threads[0], main_thread)
        self.assertIn("TAIL_MARKER", result.model_context)
        self.assertFalse(result.truncated)
        self.assertFalse(result.files[0].context_truncated)
        self.assertEqual(result.files[0].included_chars, len(parsed.blocks[0].text))

    def test_long_document_selects_relevant_tail_and_marks_excerpt(self):
        parsed = parsed_file(["irrelevant " * 600 for _ in range(20)] + ["终点温度为 372 K。TAIL_MARKER"])
        result = self.build(parsed, cap=5000, query="终点温度")
        self.assertIn("TAIL_MARKER", result.model_context)
        self.assertIn("b000021", result.model_context)
        self.assertTrue(result.truncated)
        self.assertFalse(result.files[0].parse_truncated)
        self.assertTrue(result.files[0].context_truncated)
        self.assertIn("b000021", result.files[0].included_block_ids)
        self.assertLessEqual(len(result.model_context), 5000)

    def test_total_budget_includes_headers_and_notices(self):
        parsed = parsed_file(["text " * 2000, "末尾数据"])
        for cap in (0, 1, 200, 400, 800, 1024, 2000, 10000):
            with self.subTest(cap=cap):
                result = self.build(parsed, cap=cap)
                self.assertLessEqual(len(result.model_context), cap)
                self.assertTrue(result.files[0].context_truncated)

    def test_partial_parsing_is_separate_from_context_truncation(self):
        result = self.build(parsed_file(["visible page"], status="partial", warnings=["Image-only page omitted."]))
        metadata = result.to_metadata()
        self.assertEqual(metadata["files"][0]["parseStatus"], "partial")
        self.assertEqual(metadata["files"][0]["parseWarnings"], ["Image-only page omitted."])
        self.assertFalse(metadata["contextTruncated"])
        self.assertFalse(metadata["parseTruncated"])
        self.assertFalse(metadata["truncated"])

    def test_parser_limit_propagates_even_when_all_extracted_blocks_fit(self):
        result = self.build(parsed_file(["extracted prefix"], status="partial", parse_truncated=True))
        self.assertTrue(result.truncated)
        self.assertTrue(result.files[0].parse_truncated)
        self.assertFalse(result.files[0].context_truncated)

    def test_spreadsheet_context_is_overview_not_full_data(self):
        parsed = parsed_file([f"A{row}=sample-{row}" for row in range(1, 101)], file_type="spreadsheet")
        for row, block in enumerate(parsed.blocks, 1):
            block.kind = "table_row"
            block.location = {"sheet": "Measurements", "row": row, "range": f"A{row}:D{row}"}
        result = self.build(parsed)
        self.assertIn("execute_python", result.model_context)
        self.assertIn("Measurements", result.model_context)
        self.assertIn("A1=sample-1", result.model_context)
        self.assertNotIn("A100=sample-100", result.model_context)
        self.assertEqual(len(result.files[0].included_block_ids), 4)
        self.assertTrue(result.files[0].context_truncated)


class DocumentToolTests(unittest.TestCase):
    def call(self, parsed, name="read_file", **arguments):
        arguments = {"file_id": "file-1", **arguments}
        with patch("app.services.document_tools.collect_chat_file_ids", return_value=["file-1"]), patch(
            "app.services.document_tools.get_uploaded_file", return_value=uploaded_file()
        ), patch("app.services.document_tools.get_parsed_file", return_value=parsed):
            return asyncio.run(execute_document_tool(
                None, chat_id="chat-1", current_file_ids=[], name=name, arguments=arguments,
            ))

    def test_other_chat_file_rejected_before_loading(self):
        with patch("app.services.document_tools.collect_chat_file_ids", return_value=["allowed"]), patch(
            "app.services.document_tools.get_uploaded_file"
        ) as get_file, patch("app.services.document_tools.get_parsed_file") as parse:
            result = asyncio.run(execute_document_tool(
                None, chat_id="chat", current_file_ids=[], name="read_file", arguments={"file_id": "not-allowed"},
            ))
        self.assertFalse(result["ok"])
        get_file.assert_not_called()
        parse.assert_not_called()

    def test_tool_arguments_are_strict_and_do_not_accept_paths(self):
        cases = [
            {"start_block": True}, {"start_block": 0}, {"start_offset": -1}, {"limit": "3"},
            {"max_chars": 10000000}, {"path": "C:/private.txt"}, {"limit": 0},
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.call(parsed_file(["text"]), **arguments)
                self.assertFalse(result["ok"])

    def test_search_finds_tail_and_source_neighbors(self):
        parsed = parsed_file(["noise " * 5000, "preceding context", "终点温度为 372 K。", "following context"])
        result = self.call(parsed, name="search_file", query="终点温度", neighbors=1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["matching_blocks"], 1)
        self.assertEqual([block["block_id"] for block in result["blocks"]], ["b000003", "b000002", "b000004"])
        self.assertIn("372", result["blocks"][0]["text"])
        self.assertIn("page=3", result["blocks"][0]["location"])
        self.assertTrue(result["contextTruncated"])

    def test_low_budget_search_prioritizes_match_over_neighbors(self):
        parsed = parsed_file(["preceding " * 1000, "UNIQUE_TARGET value=37", "following " * 1000])
        result = self.call(parsed, name="search_file", query="UNIQUE_TARGET", neighbors=1, max_chars=1024)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["blocks"][0]["block_id"], "b000002")
        self.assertIn("value=37", result["blocks"][0]["text"])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 1024)

    def test_low_budget_metadata_failure_does_not_succeed_with_stuck_cursor(self):
        parsed = parsed_file(["source text"], warnings=["W" * 120, "V" * 120])
        parsed.filename = "F" * 160
        parsed.blocks[0].location = {"section": "L" * 350}
        result = self.call(parsed, max_chars=1024)
        self.assertFalse(result["ok"], result)
        self.assertIn("larger max_chars", result["error"])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 1024)

    def test_long_block_can_be_read_completely_with_offsets_under_json_limit(self):
        source = ('中文 "quoted" \\ newline\n' * 200) + "TAIL"
        parsed = parsed_file([source])
        start, offset = 1, 0
        collected = []
        for _ in range(100):
            result = self.call(parsed, start_block=start, start_offset=offset, max_chars=2048)
            self.assertTrue(result["ok"], result)
            self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 2048)
            self.assertTrue(result["blocks"], result)
            collected.extend(block["text"] for block in result["blocks"])
            if result["next_block"] is None:
                break
            next_cursor = (result["next_block"], result["next_offset"])
            self.assertNotEqual(next_cursor, (start, offset))
            start, offset = next_cursor
        else:
            self.fail("Read cursor never reached the end")
        self.assertEqual("".join(collected), source)

    def test_unsupported_status_is_explicit(self):
        parsed = parsed_file([], status="unsupported", warnings=["Legacy format requires conversion."])
        result = self.call(parsed)
        self.assertFalse(result["ok"])
        self.assertEqual(result["parseStatus"], "unsupported")
        self.assertTrue(result["error"])

    def test_partial_without_source_blocks_reports_original_error(self):
        parsed = parsed_file([], status="partial", warnings=["Scanned pages require OCR."])
        parsed.error = "No text could be extracted from scanned PDF pages."
        for name, arguments in (("read_file", {}), ("search_file", {"query": "temperature"})):
            with self.subTest(name=name):
                result = self.call(parsed, name=name, **arguments)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"], parsed.error)
                self.assertEqual(result["parseStatus"], "partial")
                self.assertEqual(result["blocks"], [])
                self.assertFalse(result["parseTruncated"])

    def test_partial_with_source_blocks_remains_readable(self):
        parsed = parsed_file(["Extracted temperature is 372 K."], status="partial", warnings=["Scanned second page requires OCR."])
        result = self.call(parsed)
        self.assertTrue(result["ok"])
        self.assertEqual(result["parseStatus"], "partial")
        self.assertIn("372 K", result["blocks"][0]["text"])
        self.assertFalse(result["parseTruncated"])
        self.assertFalse(result["contextTruncated"])
        self.assertFalse(result["truncated"])


if __name__ == "__main__":
    unittest.main()
