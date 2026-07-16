import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from app.services.package_loader import (
    PackageLoadCancelled,
    load_packages_from_folder,
)
from app.services.rag_search import build_chunk_search_index
from app.ui.chatbot_dialog import ChatbotDialog


class PackageLoadResponsivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QCoreApplication.instance()
        cls.ui_available = existing is None or isinstance(existing, QApplication)
        cls.app = existing or QApplication([])

    def _write_package(self, folder: Path, count: int = 600) -> Path:
        root = folder / "package"
        root.mkdir()
        (root / "manifest.json").write_text(
            json.dumps({"package_name": "test"}), encoding="utf-8"
        )
        (root / "source_map.json").write_text("{}", encoding="utf-8")
        with (root / "chunks.jsonl").open("w", encoding="utf-8") as stream:
            for index in range(count):
                stream.write(
                    json.dumps(
                        {
                            "chunk_id": str(index),
                            "chunk_text": f"내용 {index}",
                            "embedding": [float(index % 3), 1.0, 0.5],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        archive = folder / "package.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            for path in root.iterdir():
                output.write(path, arcname=f"package/{path.name}")
        for path in root.iterdir():
            path.unlink()
        root.rmdir()
        return archive

    def test_loader_reports_stages_and_builds_equivalent_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            self._write_package(folder)
            progress = []
            packages = load_packages_from_folder(
                str(folder), progress_callback=lambda stage, value: progress.append((stage, value))
            )
        self.assertEqual(len(packages), 1)
        self.assertTrue(any(stage == "extract" for stage, _ in progress))
        self.assertTrue(any(stage == "parse" for stage, _ in progress))
        index_progress = []
        index = build_chunk_search_index(
            packages[0]["chunks"],
            progress_callback=lambda stage, value: index_progress.append((stage, value)),
        )
        self.assertEqual(index.matrix.shape, (600, 3))
        self.assertTrue(any(stage == "matrix" for stage, _ in index_progress))
        self.assertTrue(any(stage == "metadata" for stage, _ in index_progress))

    def test_loader_cancellation_is_checked_during_json_parse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            self._write_package(folder, count=1000)
            parsed = 0

            def progress(stage: str, value: int) -> None:
                nonlocal parsed
                if stage == "parse":
                    parsed = value

            with self.assertRaises(PackageLoadCancelled):
                load_packages_from_folder(
                    str(folder),
                    progress_callback=progress,
                    cancel_check=lambda: parsed >= 250,
                )
        self.assertGreaterEqual(parsed, 250)

    def test_dialog_cancel_requests_interruption_and_close_is_nonblocking(self):
        if not self.ui_available:
            self.skipTest("A QCoreApplication was created earlier in the full test suite")
        dialog = ChatbotDialog()
        worker = MagicMock()
        worker.isRunning.return_value = True
        dialog.package_load_worker = worker
        dialog.cancel_load_button.show()
        dialog._cancel_package_load()
        worker.requestInterruption.assert_called_once()
        self.assertFalse(dialog.cancel_load_button.isEnabled())
        self.assertIn("취소", dialog.status_label.text())
        dialog.close()
        self.app.processEvents()
        self.assertGreaterEqual(worker.requestInterruption.call_count, 2)


if __name__ == "__main__":
    unittest.main()
