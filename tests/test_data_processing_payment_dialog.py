import unittest
from unittest.mock import patch

from PySide6.QtCore import QUrl
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from app.ui.main_window import DataProcessingPaymentDialog


class DataProcessingPaymentDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QCoreApplication.instance()
        if existing is not None and not isinstance(existing, QApplication):
            raise unittest.SkipTest("A QCoreApplication was created earlier in the full test suite")
        cls.app = QApplication.instance() or QApplication([])

    def test_exact_message_has_no_amount_and_buttons_exist(self):
        dialog = DataProcessingPaymentDialog(0.35)
        self.assertEqual(
            dialog.message_label.text(),
            "자료 처리에 결제가 필요합니다. 필요 용량: 0.35GB",
        )
        self.assertNotIn("원", dialog.message_label.text())
        self.assertEqual(dialog.retry_button.text(), "이어서 진행")
        self.assertEqual(dialog.cancel_button.text(), "취소")

    def test_portal_button_opens_documentation_portal(self):
        dialog = DataProcessingPaymentDialog(1.2)
        with patch("app.ui.main_window.QDesktopServices.openUrl") as open_url:
            dialog.portal_button.click()
        open_url.assert_called_once_with(QUrl(DataProcessingPaymentDialog.PORTAL_URL))

    def test_retry_disables_and_reenables_after_unconfirmed(self):
        dialog = DataProcessingPaymentDialog(1.2)
        dialog.set_checking(True)
        self.assertFalse(dialog.retry_button.isEnabled())
        self.assertEqual(dialog.retry_button.text(), "확인 중...")
        dialog.show_not_confirmed()
        self.assertTrue(dialog.retry_button.isEnabled())
        self.assertIn("아직 결제가 확인되지 않았습니다", dialog.status_label.text())


if __name__ == "__main__":
    unittest.main()
