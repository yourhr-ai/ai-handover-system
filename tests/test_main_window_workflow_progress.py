import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from app.ui.main_window import WorkflowProgressBar


class WorkflowProgressBarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        existing_app = QCoreApplication.instance()
        if existing_app is not None and not isinstance(existing_app, QApplication):
            raise unittest.SkipTest(
                "A non-GUI QCoreApplication was created by an earlier test module"
            )
        cls.app = QApplication.instance() or QApplication([])

    def test_bar_is_a_static_guide_with_no_completion_state_api(self) -> None:
        # The bar must never reflect live completion state (see
        # MemoWorkflowProgressBar in memodialog.py for the same pattern), so
        # there is no per-step completion/current-step API left to call.
        progress = WorkflowProgressBar()
        self.assertEqual(
            progress.STEP_LABELS,
            ("메모작성", "패키지생성", "물어보기"),
        )
        self.assertFalse(hasattr(progress, "set_states"))
        self.assertFalse(hasattr(progress, "_completed"))
        self.assertFalse(hasattr(progress, "_current_index"))
        self.assertFalse(hasattr(progress, "_animation_timer"))

    def test_responsive_painting_at_compact_and_wide_sizes(self) -> None:
        progress = WorkflowProgressBar()
        for width in (520, 900):
            progress.resize(width, 62)
            image = progress.grab().toImage()
            self.assertEqual(image.width(), width)
            self.assertEqual(image.height(), 62)


if __name__ == "__main__":
    unittest.main()
