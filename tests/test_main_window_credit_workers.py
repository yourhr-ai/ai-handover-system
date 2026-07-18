import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QTimer

import app.license_credits as license_credits
from app.license_credits import get_embedding_unit_cost

from app.ui.main_window import (
    CreditBalanceWorker,
    PackageOrderWorker,
    PackagePaymentPollWorker,
    ReportAiWorker,
    MainWindow,
)


def _run_responsiveness_check(worker, timeout_seconds: float = 2.0) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    ticks: list[bool] = []
    QTimer.singleShot(20, lambda: ticks.append(True))
    worker.start()
    deadline = time.perf_counter() + timeout_seconds
    while worker.isRunning() and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.005)
    worker.wait(100)
    app.processEvents()
    if worker.isRunning():
        raise AssertionError("worker did not finish")
    if not ticks:
        raise AssertionError("UI event loop stopped while credit network call waited")


class MainWindowCreditWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_embedding_unit_cost_is_read_fresh_from_server(self):
        with patch(
            "app.license_credits._request_json",
            side_effect=[
                {"embedding_krw_per_1k_tokens": 0.05},
                {"embedding_krw_per_1k_tokens": 0.08},
            ],
        ):
            self.assertEqual(get_embedding_unit_cost(), 0.05)
            self.assertEqual(get_embedding_unit_cost(), 0.08)

    def test_package_order_status_uses_server_query_endpoint(self):
        with patch("app.license_credits._request_json", return_value={"status": "pending"}) as request:
            result = license_credits.get_package_generation_order("order id")
        self.assertEqual(result, {"status": "pending"})
        request.assert_called_once_with(
            f"{license_credits._PACKAGE_ORDERS_URL}?id=order+id"
        )

    def test_package_order_preserves_byte_precision_at_pricing_boundary(self):
        just_above_5_4_gb = 5_400_000_001 / 1_000_000_000
        with patch("app.license_credits._request_json", return_value={}) as request:
            license_credits.create_package_generation_order(
                "license", just_above_5_4_gb
            )
        payload = request.call_args.kwargs["payload"]
        self.assertGreater(payload["requestedGb"], 5.4)

    def test_package_order_creation_does_not_block_ui_event_loop(self):
        calls = []
        with patch(
            "app.ui.main_window.create_package_generation_order",
            side_effect=lambda license_code, size_gb: (
                time.sleep(0.15),
                calls.append((license_code, size_gb)),
                {"allowed": True, "status": "completed"},
            )[2],
        ):
            _run_responsiveness_check(PackageOrderWorker("license", 6.25))
        self.assertEqual(calls, [("license", 6.25)])

    def test_free_package_order_starts_package_immediately(self):
        window = SimpleNamespace(
            _package_order_worker=object(),
            _rag_package_context={},
            _data_processing_payment_dialog=None,
            _close_data_processing_payment_dialog=MagicMock(),
            _start_rag_package_from_context=MagicMock(),
        )
        MainWindow._handle_package_order_completed(
            window, {"allowed": True, "status": "completed"}
        )
        window._start_rag_package_from_context.assert_called_once()
        window._close_data_processing_payment_dialog.assert_called_once()

    def test_payment_poll_detects_completed_without_blocking_ui(self):
        with patch(
            "app.ui.main_window.get_package_generation_order",
            side_effect=lambda *_: (time.sleep(0.15), {"status": "completed"})[1],
        ):
            _run_responsiveness_check(PackagePaymentPollWorker("order-1", timeout_seconds=1))

    def test_package_source_no_longer_calls_credit_reservation(self):
        from pathlib import Path
        source = (Path(__file__).parents[1] / "app/ui/main_window.py").read_text(encoding="utf-8")
        self.assertNotIn("reserve_credits(", source)
        self.assertNotIn("finalize_credit_reservation(", source)

    def test_report_flow_no_longer_precheck_or_consumes_locally(self):
        # /api/handover/ai/chat now reserves/finalizes credits server-side for
        # the "report" action, so the exe must not duplicate that with its own
        # precheck/consume calls.
        from pathlib import Path
        source = (Path(__file__).parents[1] / "app/ui/main_window.py").read_text(encoding="utf-8")
        self.assertNotIn('precheck_action(self.license_code, "report")', source)
        self.assertNotIn("consume_credits(", source.split("class ReportAiWorker", 1)[1].split("class AnalysisWorker", 1)[0])

    def test_startup_flush_and_balance_do_not_block_ui_event_loop(self):
        calls: list[str] = []
        with (
            patch(
                "app.ui.main_window.flush_pending_consumptions",
                side_effect=lambda: (calls.append("flush"), time.sleep(0.15))[0],
            ),
            patch("app.ui.main_window.check_balance", side_effect=lambda *_: calls.append("balance") or {}),
        ):
            _run_responsiveness_check(
                CreditBalanceWorker("license", flush_pending=True)
            )
        self.assertEqual(calls, ["flush", "balance"])

    def test_balance_worker_emits_the_queried_license_code(self):
        received = []
        worker = CreditBalanceWorker("license-new")
        worker.completed.connect(lambda license_code, balance: received.append((license_code, balance)))
        with patch(
            "app.ui.main_window.check_balance",
            side_effect=lambda *_: (time.sleep(0.05), {"low_balance": False})[1],
        ):
            _run_responsiveness_check(worker)
        self.assertEqual(received, [("license-new", {"low_balance": False})])

    def test_stale_license_balance_response_is_ignored(self):
        class FakeBanner:
            def __init__(self):
                self.hidden = False
                self.text = "old warning"

            def hide(self):
                self.hidden = True

            def show(self):
                self.hidden = False

            def setText(self, text):
                self.text = text

        window = SimpleNamespace(credit_balance_banner=FakeBanner())
        with patch("app.ui.main_window.load_saved_license_code", return_value="license-new"):
            MainWindow._apply_credit_balance(
                window, "license-old", {"low_balance": False}
            )
        self.assertFalse(window.credit_balance_banner.hidden)
        self.assertEqual(window.credit_balance_banner.text, "old warning")

    def test_current_license_sufficient_balance_hides_old_warning(self):
        class FakeBanner:
            hidden = False

            def hide(self):
                self.hidden = True

            def show(self):
                self.hidden = False

            def setText(self, _text):
                pass

        window = SimpleNamespace(credit_balance_banner=FakeBanner())
        with patch("app.ui.main_window.load_saved_license_code", return_value="license-new"):
            MainWindow._apply_credit_balance(
                window, "license-new", {"low_balance": False}
            )
        self.assertTrue(window.credit_balance_banner.hidden)

    def test_license_switch_focus_and_periodic_refresh_hooks_are_present(self):
        from pathlib import Path

        source = (Path(__file__).parents[1] / "app/ui/main_window.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("self.credit_balance_banner.hide()", source)
        self.assertIn("self._credit_refresh_timer.start(5 * 60 * 1000)", source)
        self.assertIn("QEvent.Type.WindowActivate", source)

    def test_report_ai_result_stays_off_ui_thread_and_refreshes_balance(self):
        memo = SimpleNamespace()
        calls: list[str] = []

        def slow_ai_result(*_args, **_kwargs):
            calls.append("ai_result")
            time.sleep(0.15)
            return {"_usage": {"prompt_tokens": 2}}

        with (
            patch("app.ui.main_window.get_or_refresh_ai_result", side_effect=slow_ai_result),
            patch("app.ui.main_window.flush_pending_consumptions", side_effect=lambda: calls.append("flush")),
            patch("app.ui.main_window.check_balance", return_value={}),
        ):
            _run_responsiveness_check(
                ReportAiWorker([memo], [], "", [], "license")
            )
        self.assertEqual(calls, ["ai_result", "flush"])

    def test_report_ai_insufficient_credits_emits_denied(self):
        from app.services.ai_proxy_client import InsufficientCreditsError

        memo = SimpleNamespace()
        denied_calls: list[bool] = []

        worker = ReportAiWorker([memo], [], "", [], "license")
        worker.denied.connect(lambda: denied_calls.append(True))
        app = QCoreApplication.instance() or QCoreApplication([])
        with patch(
            "app.ui.main_window.get_or_refresh_ai_result",
            side_effect=InsufficientCreditsError(),
        ):
            worker.start()
            deadline = time.perf_counter() + 2.0
            while worker.isRunning() and time.perf_counter() < deadline:
                app.processEvents()
                time.sleep(0.005)
            worker.wait(100)
            app.processEvents()
        self.assertEqual(denied_calls, [True])


if __name__ == "__main__":
    unittest.main()
