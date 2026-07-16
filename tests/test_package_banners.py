import os
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from app.license_credits import fetch_package_banners
from app.ui.main_window import MainWindow, PackageBannerWorker, RagPackageProgressDialog


class PackageBannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QCoreApplication.instance()
        cls.ui_available = existing is None or isinstance(existing, QApplication)
        cls.app = existing or QApplication([])

    def test_fetch_package_banners_uses_public_endpoint(self):
        expected = {"left": {"text": "left", "linkUrl": "https://left", "active": True}}
        with patch("app.license_credits._request_json", return_value=expected) as request:
            self.assertEqual(fetch_package_banners(), expected)
        self.assertIn("/api/handover/package-banners", request.call_args.args[0])

    def test_fetch_failure_returns_none(self):
        with patch("app.license_credits._request_json", return_value=None):
            self.assertIsNone(fetch_package_banners())

    def test_worker_does_not_block_ui_event_loop(self):
        ticks = []
        worker = PackageBannerWorker()
        with patch(
            "app.ui.main_window.fetch_package_banners",
            side_effect=lambda: (time.sleep(0.15), None)[1],
        ):
            worker.start()
            QCoreApplication.instance().processEvents()
            deadline = time.perf_counter() + 2
            while worker.isRunning() and time.perf_counter() < deadline:
                ticks.append(True)
                QCoreApplication.instance().processEvents()
                time.sleep(0.005)
            worker.wait(100)
        self.assertTrue(ticks)

    def test_reused_progress_dialog_fetches_banners_only_once(self):
        dialog = SimpleNamespace(banner_fetch_started=False)
        workers = []

        class FakeWorker:
            def __init__(self, _parent):
                self.completed = SimpleNamespace(connect=lambda _slot: None)
                self.finished = SimpleNamespace(connect=lambda _slot: None)

            def deleteLater(self):
                pass

            def start(self):
                workers.append(self)

        window = SimpleNamespace(_package_banner_workers=set())
        with patch("app.ui.main_window.PackageBannerWorker", FakeWorker):
            MainWindow._start_package_banner_worker(window, dialog)
            MainWindow._start_package_banner_worker(window, dialog)
        self.assertTrue(dialog.banner_fetch_started)
        self.assertEqual(len(workers), 1)

    def _dialog(self):
        dialog = RagPackageProgressDialog(lambda: None)
        dialog.banner_container = QWidget(dialog)
        dialog.banner_labels = {
            "left": QLabel(dialog.banner_container),
            "right": QLabel(dialog.banner_container),
        }
        dialog.banner_container.hide()
        for label in dialog.banner_labels.values():
            label.hide()
        return dialog

    def test_both_one_and_no_banner_layout_states(self):
        if not self.ui_available:
            self.skipTest("A QCoreApplication was created earlier in the full test suite")
        dialog = self._dialog()
        window = SimpleNamespace(_rag_package_progress_box=dialog)
        both = {
            "left": {"text": "좌측", "linkUrl": "https://left.example", "active": True},
            "right": {"text": "우측", "linkUrl": "https://right.example", "active": True},
        }
        MainWindow._apply_package_banners(window, dialog, both)
        self.assertFalse(dialog.banner_container.isHidden())
        self.assertFalse(dialog.banner_labels["left"].isHidden())
        self.assertFalse(dialog.banner_labels["right"].isHidden())

        both["right"]["active"] = False
        MainWindow._apply_package_banners(window, dialog, both)
        self.assertFalse(dialog.banner_labels["left"].isHidden())
        self.assertTrue(dialog.banner_labels["right"].isHidden())

        MainWindow._apply_package_banners(window, dialog, None)
        self.assertTrue(dialog.banner_container.isHidden())

    def test_click_opens_only_http_urls(self):
        with patch("app.ui.main_window.QDesktopServices.openUrl") as open_url:
            MainWindow._open_package_banner_url(SimpleNamespace(), "https://left.example")
            MainWindow._open_package_banner_url(SimpleNamespace(), "javascript:alert(1)")
        open_url.assert_called_once()
        self.assertEqual(open_url.call_args.args[0], QUrl("https://left.example"))

    def test_banner_cards_match_admin_preview_style(self):
        source = Path(__file__).parents[1].joinpath("app/ui/main_window.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("background-color: rgba(245, 243, 255, 179)", source)
        self.assertIn("border: 1px dashed #DDD6FE", source)
        self.assertIn("border-radius: 16px", source)
        self.assertIn("padding: 16px 20px", source)
        self.assertIn("banner_label.setMinimumHeight(64)", source)


if __name__ == "__main__":
    unittest.main()
