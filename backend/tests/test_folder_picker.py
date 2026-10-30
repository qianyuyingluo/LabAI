import asyncio
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.workspaces import PickFolderRequest, pick_workspace_folder
from app.core.errors import AppError
from app.main import app
from app.services.folder_picker import _initial_directory, _picker_command, pick_folder


class FolderPickerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="labai-picker-")
        self.root = Path(self.temp.name)
        self.project = self.root / "中文项目 with space 'quote' $value"
        self.project.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def helper_command(initial):
        return [sys.executable, "-I", "-c",
                "import json,sys; sys.stdout.reconfigure(encoding='utf-8'); print(json.dumps({'path':sys.argv[1]},ensure_ascii=False))",
                str(initial)]

    def test_helper_uses_separate_arguments_and_unicode_result_round_trips(self):
        command = _picker_command(self.project)
        self.assertEqual(command[-1], str(self.project))
        self.assertIn("folder_picker_helper.py", command[-2])
        with patch("app.services.folder_picker._picker_command", side_effect=self.helper_command):
            selected = asyncio.run(pick_folder(str(self.project)))
        self.assertEqual(selected, str(self.project.resolve()))

    def test_invalid_initial_directory_falls_back_to_home(self):
        for initial in (None, "", "relative", str(self.root / "missing")):
            self.assertEqual(_initial_directory(initial), Path.home())
        file = self.root / "file.txt"
        file.write_text("test")
        self.assertEqual(_initial_directory(str(file)), Path.home())

    def test_cancel_result_is_normal_and_bad_helper_result_is_error(self):
        cases = [("print('{\"path\":null}')", None), ("print('bad json')", "FOLDER_PICKER_UNAVAILABLE"),
                 ("print('{\"path\":123}')", "FOLDER_PICKER_UNAVAILABLE")]
        for script, expected in cases:
            with self.subTest(script=script), patch("app.services.folder_picker._picker_command", return_value=[sys.executable, "-I", "-c", script]):
                if expected is None:
                    self.assertIsNone(asyncio.run(pick_folder()))
                else:
                    with self.assertRaises(AppError) as caught:
                        asyncio.run(pick_folder())
                    self.assertEqual(caught.exception.code, expected)

    def test_concurrent_dialog_is_rejected_and_cancel_reaps_helper(self):
        async def scenario():
            processes = []
            original = subprocess.Popen

            def launch(*args, **kwargs):
                process = original(*args, **kwargs)
                processes.append(process)
                return process

            with patch("app.services.folder_picker._picker_command", return_value=[sys.executable, "-I", "-c", "import time; time.sleep(30)"]), patch("app.services.folder_picker.subprocess.Popen", side_effect=launch):
                task = asyncio.create_task(pick_folder())
                await asyncio.sleep(0.03)
                self.assertEqual(len(processes), 1)
                with self.assertRaises(AppError) as caught:
                    await pick_folder()
                self.assertEqual(caught.exception.code, "FOLDER_PICKER_BUSY")
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertIsNotNone(processes[0].poll())
            # The lock must be available again after cancellation.
            with patch("app.services.folder_picker._picker_command", side_effect=self.helper_command):
                self.assertEqual(await pick_folder(str(self.project)), str(self.project.resolve()))
        asyncio.run(scenario())

    def test_timeout_reaps_helper_and_releases_dialog_lock(self):
        processes = []
        original = subprocess.Popen

        def launch(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            return process

        with patch("app.services.folder_picker.PICKER_TIMEOUT_SECONDS", 0.03), patch("app.services.folder_picker._picker_command", return_value=[sys.executable, "-I", "-c", "import time; time.sleep(30)"]), patch("app.services.folder_picker.subprocess.Popen", side_effect=launch):
            with self.assertRaises(AppError) as caught:
                asyncio.run(pick_folder())
        self.assertEqual(caught.exception.code, "FOLDER_PICKER_TIMEOUT")
        self.assertIsNotNone(processes[0].poll())
        with patch("app.services.folder_picker._picker_command", side_effect=self.helper_command):
            self.assertEqual(asyncio.run(pick_folder(str(self.project))), str(self.project.resolve()))


class FolderPickerApiTests(unittest.TestCase):
    def test_api_selected_and_cancelled_response_and_cross_origin_guard(self):
        with patch("app.main.init_db"), patch("app.main.cleanup_stale_workspaces"), TestClient(app) as client:
            for selected in (None, "C:\\Projects\\demo"):
                with patch("app.api.workspaces.pick_folder", new=AsyncMock(return_value=selected)) as picker:
                    response = client.post("/api/workspaces/pick-folder", json={"initial_path": None})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json(), {"path": selected, "cancelled": selected is None})
                    picker.assert_awaited_once_with(None)
            with patch("app.api.workspaces.pick_folder", new=AsyncMock()) as picker:
                response = client.post("/api/workspaces/pick-folder", json={}, headers={"Origin": "https://untrusted.example"})
                self.assertEqual(response.status_code, 403)
                picker.assert_not_called()

    def test_remote_client_cannot_open_local_dialog(self):
        request = SimpleNamespace(client=SimpleNamespace(host="192.0.2.20"))
        with patch("app.api.workspaces.pick_folder", new=AsyncMock()) as picker:
            with self.assertRaises(AppError) as caught:
                asyncio.run(pick_workspace_folder(PickFolderRequest(), request))
            self.assertEqual(caught.exception.code, "FOLDER_PICKER_LOCAL_ONLY")
            picker.assert_not_called()

    def test_disconnected_client_cancels_picker(self):
        cleaned_up = []

        async def wait_for_selection(_):
            try:
                await asyncio.sleep(30)
            finally:
                cleaned_up.append(True)

        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), is_disconnected=AsyncMock(return_value=True))
        with patch("app.api.workspaces.pick_folder", side_effect=wait_for_selection):
            with self.assertRaises(AppError) as caught:
                asyncio.run(pick_workspace_folder(PickFolderRequest(), request))
        self.assertEqual(caught.exception.code, "FOLDER_PICKER_DISCONNECTED")
        self.assertEqual(cleaned_up, [True])


if __name__ == "__main__":
    unittest.main()
