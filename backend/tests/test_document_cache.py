import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.core.config import Settings
from app.services.document_service import get_parsed_file


class DocumentCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="labai-cache-test-")
        self.root = Path(self.temp.name)
        self.settings = Settings(_env_file=None, backend_storage_dir=self.root / "storage")
        self.settings_patch = patch("app.services.document_service.get_settings", return_value=self.settings)
        self.settings_patch.start()

    def tearDown(self):
        self.settings_patch.stop()
        self.temp.cleanup()

    def uploaded(self, text="source evidence", name="source.txt", file_id="first"):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return SimpleNamespace(id=file_id, original_filename=name, stored_filename=name,
                               local_path=str(path), extension="txt", file_type="text",
                               mime_type="text/plain", size=path.stat().st_size, sha256="")

    def test_worker_cache_reuses_content_and_rebinds_upload_identity(self):
        source = self.uploaded()
        first = get_parsed_file(source)
        self.assertEqual(first.parse_status, "success", first.error)
        self.assertIn("source evidence", first.text)
        duplicate = self.uploaded(name="duplicate.txt", file_id="second")
        with patch("app.services.document_service._run_worker", side_effect=AssertionError("cache miss")):
            cached = get_parsed_file(duplicate)
        self.assertEqual(cached.file_id, "second")
        self.assertEqual(cached.filename, "duplicate.txt")
        self.assertEqual(cached.blocks, first.blocks)

    def test_changed_content_and_corrupt_cache_are_reparsed(self):
        source = self.uploaded()
        get_parsed_file(source)
        for entry in (self.settings.storage_dir / "parsed").glob("*.json"):
            entry.write_text("invalid json", encoding="utf-8")
        self.assertIn("source evidence", get_parsed_file(source).text)
        entry = next((self.settings.storage_dir / "parsed").glob("*.json"))
        malformed = json.loads(entry.read_text(encoding="utf-8"))
        malformed["result"]["blocks"][0]["location"] = []
        entry.write_text(json.dumps(malformed), encoding="utf-8")
        self.assertIn("source evidence", get_parsed_file(source).text)
        Path(source.local_path).write_text("new measurements", encoding="utf-8")
        self.assertIn("new measurements", get_parsed_file(source).text)

    def test_timeout_reports_error_and_is_not_cached(self):
        source = self.uploaded()
        with patch("app.services.document_service.subprocess.run", side_effect=subprocess.TimeoutExpired("parser", 45)):
            failed = get_parsed_file(source)
        self.assertEqual(failed.parse_status, "error")
        self.assertIn("timed out", failed.error)
        self.assertEqual(list((self.settings.storage_dir / "parsed").glob("*.json")), [])
        self.assertEqual(get_parsed_file(source).parse_status, "success")

    def test_worker_environment_does_not_inherit_model_secrets(self):
        source = self.uploaded()
        with patch.dict("os.environ", {"OPENAI_COMPAT_API_KEY": "must-not-inherit"}):
            with patch("app.services.document_service.subprocess.run", return_value=SimpleNamespace(returncode=1)) as run:
                get_parsed_file(source)
        self.assertNotIn("OPENAI_COMPAT_API_KEY", run.call_args.kwargs["env"])


if __name__ == "__main__":
    unittest.main()
