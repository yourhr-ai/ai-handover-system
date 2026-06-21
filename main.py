"""
AI 인수인계 자동화 시스템 — 진입점
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from src.config.settings import Settings
from src.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("AI 인수인계 자동화 시스템")
    app.setOrganizationName("handover")

    settings = Settings.load()

    window = MainWindow(settings)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
