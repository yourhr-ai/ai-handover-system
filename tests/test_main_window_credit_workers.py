import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QTimer

from app.license_credits import get_embedding_unit_cost

from app.ui.main_window import (
    CreditBalanceWorker,
    CreditFinalizeWorker,
    CreditPrecheckWorker,
    PackageCreditFinalizeWorker,
    PackageCreditReserveWorker,
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

    def test_package_precheck_does_not_block_ui_event_loop(self):
        with patch(
            "app.ui.main_window.precheck_action",
            side_effect=lambda *_args: (time.sleep(0.15), {"allowed": True})[1],
        ):
            _run_responsiveness_check(CreditPrecheckWorker("license", "package"))

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

    def test_package_finalize_preserves_consume_flush_balance_order(self):
        calls: list[str] = []

        def slow_consume(*_args, **_kwargs):
            calls.append("consume")
            time.sleep(0.15)

        with (
            patch("app.ui.main_window.consume_credits", side_effect=slow_consume),
            patch("app.ui.main_window.flush_pending_consumptions", side_effect=lambda: calls.append("flush")),
            patch("app.ui.main_window.check_balance", side_effect=lambda *_: calls.append("balance") or {}),
        ):
            _run_responsiveness_check(
                CreditFinalizeWorker("license", "package", {"embedding_tokens": 12})
            )
        self.assertEqual(calls, ["consume", "flush", "balance"])

    def test_package_reserves_full_estimated_cost_without_blocking_ui(self):
        calls = []
        with patch(
            "app.ui.main_window.reserve_credits",
            side_effect=lambda license_code, action, cost: (
                time.sleep(0.15),
                calls.append((license_code, action, cost)),
                {"allowed": True, "reservation_id": "reservation-1"},
            )[2],
        ):
            _run_responsiveness_check(PackageCreditReserveWorker("license", 1166))
        self.assertEqual(calls, [("license", "package", 1166)])

    def test_package_finalize_uses_actual_tokens_and_cancel_releases_reservation(self):
        calls = []

        def finalize(*args, **kwargs):
            calls.append((args, kwargs))
            time.sleep(0.05)
            return {"balance_after": 10}

        with (
            patch("app.ui.main_window.finalize_credit_reservation", side_effect=finalize),
            patch("app.ui.main_window.check_balance", return_value={}),
        ):
            _run_responsiveness_check(
                PackageCreditFinalizeWorker(
                    "license", "reservation-1", embedding_tokens=12345
                )
            )
            _run_responsiveness_check(
                PackageCreditFinalizeWorker(
                    "license", "reservation-2", cancel=True
                )
            )
        self.assertEqual(calls[0][0], ("license", "reservation-1", "package"))
        self.assertEqual(calls[0][1]["embedding_tokens"], 12345)
        self.assertFalse(calls[0][1]["cancel"])
        self.assertEqual(calls[1][0], ("license", "reservation-2", "package"))
        self.assertTrue(calls[1][1]["cancel"])

    def test_insufficient_full_estimate_does_not_start_package(self):
        window = SimpleNamespace(
            _package_reserve_worker=object(),
            _package_reserve_canceled=False,
            _rag_package_context={"estimated_cost": 1166},
            _finish_cost_estimation=MagicMock(),
        )
        with patch("app.ui.main_window.QMessageBox.warning") as warning:
            MainWindow._handle_package_reserve_completed(
                window,
                "license",
                {
                    "allowed": False,
                    "required_credits": 1166,
                    "balance": 40,
                },
            )
        window._finish_cost_estimation.assert_called_once()
        self.assertIn("1,166크레딧", warning.call_args.args[2])
        self.assertIn("40크레딧", warning.call_args.args[2])

    def test_successful_reservation_is_kept_until_package_finishes(self):
        context = {
            "estimated_cost": 1166,
            "result": object(),
            "folder_paths": ["folder"],
            "api_key": "key",
            "output_path": "package",
            "parsed_emails": [],
            "kakao_file_paths": [],
            "extension_size_limits": {},
        }
        window = SimpleNamespace(
            _package_reserve_worker=object(),
            _package_reserve_canceled=False,
            _rag_package_context=context,
            _start_rag_package_worker=MagicMock(),
        )
        MainWindow._handle_package_reserve_completed(
            window,
            "license",
            {"allowed": True, "reservation_id": "reservation-1"},
        )
        self.assertEqual(window._package_reservation_id, "reservation-1")
        self.assertEqual(window._package_reservation_license_code, "license")
        window._start_rag_package_worker.assert_called_once()

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

    def test_report_precheck_ai_consume_stays_off_ui_thread(self):
        memo = SimpleNamespace()
        calls: list[str] = []

        def slow_precheck(*_args):
            calls.append("precheck")
            time.sleep(0.15)
            return {"allowed": True}

        with (
            patch("app.ui.main_window.precheck_action", side_effect=slow_precheck),
            patch("app.ui.main_window.get_or_refresh_ai_result", return_value={"_usage": {"prompt_tokens": 2}}),
            patch("app.ui.main_window.consume_credits", side_effect=lambda *_args, **_kwargs: calls.append("consume")),
            patch("app.ui.main_window.flush_pending_consumptions", side_effect=lambda: calls.append("flush")),
            patch("app.ui.main_window.check_balance", return_value={}),
        ):
            _run_responsiveness_check(
                ReportAiWorker([memo], [], "", [], "license")
            )
        self.assertEqual(calls, ["precheck", "consume", "flush"])


if __name__ == "__main__":
    unittest.main()
