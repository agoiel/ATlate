"""Окно со списком: оригинал и перевод каждого облака.

Щелчок по переводу копирует его; «Копировать всё» — перевод всей страницы.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
                               QVBoxLayout, QWidget)

from ..i18n import tr
from ..models import PageResult


class ListWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle(tr("Перевод — список"))
        self.resize(460, 640)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._on_link)
        self.show_original = QCheckBox(tr("Показывать оригинал"))
        self.show_original.setChecked(True)
        self.on_top = QCheckBox(tr("Поверх всех окон"))
        self.on_top.setChecked(True)
        self.on_top.toggled.connect(self._set_on_top)
        self.show_original.toggled.connect(lambda _: self.set_result(self._last))
        self.hint = QLabel(tr("Щелчок по переводу — скопировать его"))
        self.hint.setStyleSheet("color: #888;")
        self.copy_all = QPushButton(tr("Копировать всё"))
        self.copy_all.clicked.connect(self._copy_all)
        self._hint_back = QTimer(self)
        self._hint_back.setSingleShot(True)
        self._hint_back.timeout.connect(lambda: self.hint.setText(tr("Щелчок по переводу — скопировать его")))
        lay = QVBoxLayout(self)
        lay.addWidget(self.view)
        row = QHBoxLayout()
        row.addWidget(self.hint, 1)
        row.addWidget(self.copy_all)
        lay.addLayout(row)
        lay.addWidget(self.show_original)
        lay.addWidget(self.on_top)
        self._last: PageResult | None = None
        self._set_on_top(True)
        self.set_result(None)

    def _set_on_top(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if self.isVisible():
            self.show()

    # ------------------------------------------------------------ копирование
    def _copied(self, text: str, what: str) -> None:
        QApplication.clipboard().setText(text)
        self.hint.setText(what)
        self._hint_back.start(2500)

    def _on_link(self, url: QUrl) -> None:
        if url.scheme() != "copy" or self._last is None:
            return
        try:
            b = self._last.bubbles[int(url.path() or url.host())]
        except (ValueError, IndexError):
            return
        if b.translation.strip():
            self._copied(b.translation.strip(), tr("Скопировано"))

    def _copy_all(self) -> None:
        text = self._last.copy_text() if self._last is not None else ""
        if text:
            self._copied(text, tr("Скопировано облаков: {0}", len(self._last.translations())))

    # ------------------------------------------------------------ показ
    def set_result(self, result: PageResult | None) -> None:
        self._last = result
        self.copy_all.setEnabled(bool(result is not None and result.translations()))
        if result is None:
            self.view.setHtml("")
            return
        parts = []
        for i, b in enumerate(result.bubbles, 1):
            orig = html.escape(b.text)
            if b.translation:
                # ссылка «copy:N» — щелчок копирует перевод этого облака
                shown = (f"<a href='copy:{i - 1}' style='color:inherit; text-decoration:none'>"
                         f"{html.escape(b.translation)}</a>")
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
