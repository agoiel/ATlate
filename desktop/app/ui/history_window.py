"""Окно «История переводов» (режим разработчика): поиск, копирование, выгрузка в CSV, очистка."""

from __future__ import annotations

import html
import time

from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QHBoxLayout, QLineEdit, QMessageBox,
                               QTextBrowser, QVBoxLayout)

from .. import history
from ..i18n import tr
from . import theme
from .widgets import caption, label, pill

SHOW = 500              # сколько реплик показывать в окне (поиск идёт по всем)


class HistoryDialog(QDialog):
    def __init__(self, parent=None, path=None):
        super().__init__(parent)
        self.path = path or history.PATH
        self.setWindowTitle(tr("История переводов"))
        self.resize(660, 680)
        self.all = history.load(self.path)
        self.shown: list[history.Line] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label(tr("История переводов"), "cardTitle"))
        lay.addWidget(caption(tr("Пишется, пока включён режим разработчика. Хранятся последние несколько тысяч реплик.")))
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Поиск по оригиналу, переводу или тайтлу"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._fill())
        lay.addWidget(self.search)
        self.count = caption("")
        lay.addWidget(self.count)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.copy_btn = pill(tr("Копировать"), "tonal", "copy", tr("Найденные реплики: оригинал и перевод"))
        self.copy_btn.clicked.connect(self._copy)
        row.addWidget(self.copy_btn)
        self.csv_btn = pill(tr("Сохранить в CSV…"), "tonal", "download")
        self.csv_btn.clicked.connect(self._save_csv)
        row.addWidget(self.csv_btn)
        self.clear_btn = pill(tr("Очистить"), "outline", "trash")
        self.clear_btn.clicked.connect(self._clear)
        row.addWidget(self.clear_btn)
        row.addStretch(1)
        close = pill(tr("Готово"), "primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self._fill()

    def _fill(self) -> None:
        self.shown = history.search(self.all, self.search.text())
        n = len(self.shown)
        text = tr("Реплик: {0}", n)
        if n > SHOW:
            text += tr(" · показаны последние {0}", SHOW)
        self.count.setText(text)
        for b in (self.copy_btn, self.csv_btn):
            b.setEnabled(n > 0)
        self.clear_btn.setEnabled(bool(self.all))
        c = theme.palette()
        parts, head = [], None
        for x in self.shown[:SHOW]:
            h = (time.strftime("%d.%m %H:%M", time.localtime(x.time)), x.title, x.translator)
            if h != head:
                head = h
                meta = " · ".join(html.escape(p) for p in h if p)
                parts.append(f"<p style='margin:14px 0 2px 0; color:{c.text2}; font-size:9pt'>{meta}</p>")
            tag = f"{x.src.upper()} → {x.tgt.upper()} · " if x.src or x.tgt else ""
            parts.append(f"<p style='margin:4px 0'><span style='font-size:11pt'>{html.escape(x.translation)}</span><br>"
                         f"<span style='color:{c.text2}'>{html.escape(tag)}{html.escape(x.original)}</span></p>")
        if not parts:
            empty = tr("Ничего не найдено.") if self.all else tr("История пуста: включите режим разработчика и переведите что-нибудь.")
            parts.append(f"<p style='color:{c.text2}'><i>{html.escape(empty)}</i></p>")
        self.view.setHtml("".join(parts))

    def _copy(self) -> None:
        QApplication.clipboard().setText(history.to_text(self.shown))
        self.copy_btn.setText(tr("Скопировано"))

    def _save_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Сохранить историю"), "atlate_history.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            # utf-8 с меткой: Excel тогда правильно открывает кириллицу и корейский
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(history.to_csv(self.shown))
        except OSError as e:
            QMessageBox.warning(self, tr("Ошибка"), tr("Не удалось сохранить: {0}", e))

    def _clear(self) -> None:
        if QMessageBox.question(self, tr("Очистить историю?"), tr("Все сохранённые реплики будут удалены.")) \
                != QMessageBox.StandardButton.Yes:
            return
        history.clear(self.path)
        self.all = []
        self._fill()
