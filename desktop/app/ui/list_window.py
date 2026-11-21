"""Окно со списком: оригинал и перевод каждого облака."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QTextBrowser, QVBoxLayout, QWidget

from ..i18n import tr
from ..models import PageResult


class ListWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle(tr("Перевод — список"))
        self.resize(460, 640)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.show_original = QCheckBox(tr("Показывать оригинал"))
        self.show_original.setChecked(True)
        self.on_top = QCheckBox(tr("Поверх всех окон"))
        self.on_top.setChecked(True)
        self.on_top.toggled.connect(self._set_on_top)
        self.show_original.toggled.connect(lambda _: self.set_result(self._last))
        lay = QVBoxLayout(self)
        lay.addWidget(self.view)
        lay.addWidget(self.show_original)
        lay.addWidget(self.on_top)
        self._last: PageResult | None = None
        self._set_on_top(True)

    def _set_on_top(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if self.isVisible():
            self.show()

    def set_result(self, result: PageResult | None) -> None:
        self._last = result
        if result is None:
            self.view.setHtml("")
            return
        parts = []
        for i, b in enumerate(result.bubbles, 1):
            orig = html.escape(b.text)
            if b.translation:
                shown = html.escape(b.translation)
            else:
                shown = f"<i style='color:#888'>— {html.escape(tr(b.skip_reason or 'нет перевода'))} —</i>"
            block = f"<p style='margin:6px 0'><b style='color:#4a90d9'>{i}.</b> <span style='font-size:15px'>{shown}</span>"
            if self.show_original.isChecked():
                tag = f"{b.lang.upper()} · " if b.lang else ""
                block += f"<br><span style='color:#999;font-size:12px'>{tag}{orig}</span>"
            parts.append(block + "</p>")
        if result.error:
            parts.insert(0, f"<p style='color:#d9534f'>{html.escape(result.error)}</p>")
        self.view.setHtml("".join(parts) or tr("<i>Текст не найден</i>"))
