import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QTimer

from app.services.rag_search import build_chunk_search_index
from app.ui.chatbot_dialog import ChatAnswerWorker, CreditUsageWorker


def _wait_for_thread(worker, timeout_seconds: float = 2.0) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    deadline = time.perf_counter() + timeout_seconds
    while worker.isRunning() and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.005)
    worker.wait(100)
    app.processEvents()


class ChatbotBackgroundWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_embedding_wait_does_not_block_ui_event_loop(self):
        ticks: list[float] = []
        QTimer.singleShot(20, lambda: ticks.append(time.perf_counter()))
        index = build_chunk_search_index([])
        worker = ChatAnswerWorker("질문", index, "license")

        def slow_embed(*_args):
            time.sleep(0.15)
            return [0.0]

        with patch("app.ui.chatbot_dialog.embed_query", side_effect=slow_embed):
            worker.start()
            _wait_for_thread(worker)

        self.assertTrue(ticks, "UI event loop should keep processing while the embedding call waits")

    def test_credit_balance_refresh_wait_does_not_block_ui_event_loop(self):
        ticks: list[float] = []
        QTimer.singleShot(20, lambda: ticks.append(time.perf_counter()))
        worker = CreditUsageWorker("license")

        def slow_balance(*_args, **_kwargs):
            time.sleep(0.15)
            return {"low_balance": False}

        with (
            patch("app.ui.chatbot_dialog.flush_pending_consumptions", return_value=0),
            patch("app.ui.chatbot_dialog.check_balance", side_effect=slow_balance),
        ):
            worker.start()
            _wait_for_thread(worker)

        self.assertTrue(ticks, "UI event loop should keep processing while credit update waits")


if __name__ == "__main__":
    unittest.main()
