import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[2]
TESTFILES_DIR = Path(os.environ.get("LABAI_TESTFILES_DIR", ROOT_DIR / "testfiles"))
TEMP_DIR = Path(tempfile.mkdtemp(prefix="labai-file-converters-"))

os.environ["BACKEND_STORAGE_DIR"] = str(TEMP_DIR / "storage")
os.environ["DATABASE_URL"] = "sqlite:///" + (TEMP_DIR / "labai.sqlite3").as_posix()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.adapters.base import AdapterResponse  # noqa: E402
from app.db.models import Chat, Message, ModelProfile  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.skills.files_skill import FilesSkillService  # noqa: E402


class FileConverterUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        shutil.rmtree(TEMP_DIR, ignore_errors=True)

    def test_testfiles_upload_and_parse(self) -> None:
        cases = [
            ("*.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "spreadsheet"),
            ("*.pdf", "application/pdf", "document"),
            ("*.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "document"),
            ("*.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "presentation"),
        ]

        for pattern, mime_type, expected_type in cases:
            path = self._first_testfile(pattern)
            with self.subTest(path=path.name):
                payload = self._upload(path, mime_type)
                self.assertEqual(payload["file_type"], expected_type)
                self.assertGreater(payload["size"], 0)
                self.assertIn("Extracted preview:", payload["summary"])
                self.assertNotIn("Content extraction failed", payload["summary"])
                self._assert_download(payload["file_id"], payload["size"])

    def test_image_upload_remains_compatible(self) -> None:
        image_path = ROOT_DIR / "backend" / "examples" / "deepseek" / "wrong.png"
        payload = self._upload(image_path, "image/png")

        self.assertEqual(payload["file_type"], "image")
        self.assertIn("Image file.", payload["summary"])
        self._assert_download(payload["file_id"], payload["size"])

        image_response = self.client.get(f"/api/files/images/{payload['file_id']}")
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(len(image_response.content), payload["size"])

    def test_files_skill_builds_context_from_testfiles(self) -> None:
        uploaded_ids = []
        for pattern, mime_type in [
            ("*.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("*.pdf", "application/pdf"),
            ("*.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("*.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ]:
            path = self._first_testfile(pattern)
            uploaded_ids.append(str(self._upload(path, mime_type)["file_id"]))

        with SessionLocal() as db:
            result = FilesSkillService(db).run(chat_id=None, file_ids=uploaded_ids)

        self.assertEqual(result.status, "done")
        self.assertTrue(result.has_context)
        self.assertGreater(result.total_extracted_chars, 0)
        self.assertIn("files_skill context", result.model_context)
        result_ids = {file.file_id for file in result.files}
        for file_id in uploaded_ids:
            self.assertIn(file_id, result_ids)

    def test_files_skill_reuses_current_chat_files(self) -> None:
        path = self._first_testfile("*.xlsx")
        payload = self._upload(
            path,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        with SessionLocal() as db:
            chat = Chat(title="files skill context test")
            db.add(chat)
            db.flush()
            db.add(
                Message(
                    chat_id=chat.id,
                    role="user",
                    content="uploaded a file",
                    status="done",
                    metadata_json=(
                        '{"attachments":[{"fileId":"'
                        + str(payload["file_id"])
                        + '","name":"weight.xlsx","size":1,"type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}]}'
                    ),
                )
            )
            db.commit()
            chat_id = chat.id

        with SessionLocal() as db:
            result = FilesSkillService(db).run(chat_id=chat_id, file_ids=[])
            empty_chat = Chat(title="empty files skill context test")
            db.add(empty_chat)
            db.commit()
            empty_result = FilesSkillService(db).run(chat_id=empty_chat.id, file_ids=[])

        self.assertEqual(result.status, "done")
        self.assertIn("weight.xlsx", result.model_context)
        self.assertEqual(empty_result.status, "skipped")

    def test_files_skill_current_turn_files_are_first(self) -> None:
        old_payload = self._upload(
            self._first_testfile("*.docx"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        current_payload = self._upload(
            self._first_testfile("*.xlsx"),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        with SessionLocal() as db:
            chat = Chat(title="files skill priority test")
            db.add(chat)
            db.flush()
            db.add(
                Message(
                    chat_id=chat.id,
                    role="user",
                    content="old upload",
                    status="done",
                    metadata_json=(
                        '{"attachments":[{"fileId":"'
                        + str(old_payload["file_id"])
                        + '","name":"old.docx","size":1,"type":"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}]}'
                    ),
                )
            )
            db.commit()
            chat_id = chat.id

        with SessionLocal() as db:
            result = FilesSkillService(db).run(
                chat_id=chat_id,
                file_ids=[str(current_payload["file_id"])],
            )

        self.assertEqual(result.status, "done")
        self.assertGreaterEqual(len(result.files), 2)
        self.assertEqual(result.files[0].file_id, str(current_payload["file_id"]))
        self.assertIn("weight.xlsx", result.model_context)

    def test_files_skill_model_call_isolated_per_file(self) -> None:
        uploaded_ids = []
        for pattern, mime_type in [
            ("*.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("*.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ]:
            uploaded_ids.append(str(self._upload(self._first_testfile(pattern), mime_type)["file_id"]))

        class FakeAdapter:
            def __init__(self) -> None:
                self.calls: list[list[dict[str, str]]] = []

            async def chat(self, **kwargs: object) -> AdapterResponse:
                messages = kwargs["messages"]
                assert isinstance(messages, list)
                self.calls.append(messages)
                user_content = str(messages[-1]["content"])
                if "weight.xlsx" in user_content:
                    return AdapterResponse(content="独立回答: weight.xlsx 是一个表格文件。")
                return AdapterResponse(content="独立回答: docx 是一个文档文件。")

        fake = FakeAdapter()
        profile = ModelProfile(
            name="fake",
            provider="openai_compat",
            model_name="fake-model",
            supports_stream=True,
            supports_vision=False,
        )

        async def run_case() -> None:
            with SessionLocal() as db:
                service = FilesSkillService(db)
                with patch("app.skills.files_skill.service.get_adapter_for_profile", return_value=fake):
                    result = await service.run_with_model(
                        chat_id=None,
                        file_ids=uploaded_ids,
                        profile=profile,
                        user_message="这是什么",
                    )
            self.assertEqual(result.status, "done")
            self.assertEqual(len(fake.calls), 2)
            self.assertIn("files_skill per-file model context", result.model_context)
            self.assertIn("独立回答: docx 是一个文档文件。", result.model_context)
            self.assertIn("独立回答: weight.xlsx 是一个表格文件。", result.model_context)
            for call in fake.calls:
                self.assertEqual([message["role"] for message in call], ["system", "user"])
                self.assertNotIn("files_skill per-file model context", str(call))

        asyncio.run(run_case())

    def _upload(self, path: Path, mime_type: str) -> dict[str, object]:
        with path.open("rb") as handle:
            response = self.client.post(
                "/api/files/upload",
                files={"file": (path.name, handle, mime_type)},
            )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _assert_download(self, file_id: object, expected_size: object) -> None:
        response = self.client.get(f"/api/files/{file_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.content), expected_size)

    @staticmethod
    def _first_testfile(pattern: str) -> Path:
        try:
            return next(TESTFILES_DIR.glob(pattern))
        except StopIteration as exc:
            raise AssertionError(f"No test file matching {pattern} in {TESTFILES_DIR}") from exc


if __name__ == "__main__":
    unittest.main()
