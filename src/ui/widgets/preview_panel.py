from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QGroupBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class PreviewPanel(QWidget):
    """마크다운 분석 결과를 HTML로 렌더링하는 패널."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("분석 결과")
        group_layout = QVBoxLayout(group)

        self.browser = QTextBrowser()
        self.browser.setMinimumHeight(300)
        self.browser.setOpenExternalLinks(False)
        self.browser.setPlaceholderText("분석 결과가 여기에 표시됩니다.")
        group_layout.addWidget(self.browser)

        layout.addWidget(group)

    def set_markdown(self, text: str) -> None:
        """Convert *text* (markdown) to HTML and display it."""
        html = self._markdown_to_html(text)
        self.browser.setHtml(html)

    def clear(self) -> None:
        self.browser.clear()

    # ------------------------------------------------------------------
    @staticmethod
    def _markdown_to_html(md: str) -> str:
        """Lightweight markdown → HTML conversion for preview purposes."""
        lines = md.splitlines()
        html_lines: list[str] = [
            "<html><body style='font-family: 맑은 고딕, sans-serif; "
            "font-size: 13px; line-height: 1.7; padding: 8px;'>"
        ]
        in_list = False

        for line in lines:
            # Headings
            if line.startswith("### "):
                if in_list:
                    html_lines.append("</ul>"); in_list = False
                html_lines.append(f"<h3 style='color:#1F497D'>{line[4:]}</h3>")
            elif line.startswith("## "):
                if in_list:
                    html_lines.append("</ul>"); in_list = False
                html_lines.append(f"<h2 style='color:#1F497D; border-bottom:1px solid #ccc'>{line[3:]}</h2>")
            elif line.startswith("# "):
                if in_list:
                    html_lines.append("</ul>"); in_list = False
                html_lines.append(f"<h1 style='color:#1F497D'>{line[2:]}</h1>")
            # Horizontal rule
            elif re.match(r"^---+$", line):
                if in_list:
                    html_lines.append("</ul>"); in_list = False
                html_lines.append("<hr/>")
            # Bullet list
            elif re.match(r"^[-*] ", line):
                if not in_list:
                    html_lines.append("<ul>"); in_list = True
                inner = _apply_inline(line[2:])
                html_lines.append(f"<li>{inner}</li>")
            # Numbered list
            elif re.match(r"^\d+\. ", line):
                if not in_list:
                    html_lines.append("<ul>"); in_list = True
                inner = _apply_inline(re.sub(r"^\d+\. ", "", line))
                html_lines.append(f"<li>{inner}</li>")
            elif line.strip() == "":
                if in_list:
                    html_lines.append("</ul>"); in_list = False
                html_lines.append("<br/>")
            else:
                if in_list:
                    html_lines.append("</ul>"); in_list = False
                html_lines.append(f"<p>{_apply_inline(line)}</p>")

        if in_list:
            html_lines.append("</ul>")
        html_lines.append("</body></html>")
        return "\n".join(html_lines)


def _apply_inline(text: str) -> str:
    """Apply bold/italic inline markdown within a single line."""
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text
