"""Простой редактор глоссария: таблица «оригинал — перевод — заметка»."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from ..config import GLOSSARY_DIR
from ..glossary import Glossary, GlossaryEntry


def new_glossary_file(parent, source_lang: str, default: str = "", auto: bool = False) -> Path | None:
    """Спрашивает название тайтла и создаёт пустой глоссарий (auto — авто-глоссарий, пополняется сам)."""
    title = "Новый авто-глоссарий" if auto else "Новый глоссарий"
    name, ok = QInputDialog.getText(parent, title, "Название тайтла:", text=default)
    if not ok or not name.strip():
        return None
    slug = re.sub(r"[^\w\-]+", "_", name.strip(), flags=re.UNICODE).strip("_") or "glossary"
    GLOSSARY_DIR.mkdir(parents=True, exist_ok=True)
    path = GLOSSARY_DIR / f"{slug}.json"
    i = 2
    while path.exists():
        path = GLOSSARY_DIR / f"{slug}_{i}.json"
        i += 1
    Glossary(name=name.strip(), source_lang=source_lang, auto=auto).save(path)
    return path


AUTO_MARK = "авто"      # так в редакторе помечены записи, добавленные автоматически


class GlossaryEditor(QDialog):
    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.glossary = Glossary.load(self.path)
        self.setWindowTitle(f"Глоссарий — {self.glossary.name}")
        self.resize(720, 520)

        self.name_edit = QLineEdit(self.glossary.name)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Оригинал", "Перевод", "Заметка (пол, кто это, как обращаться)", ""])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        for e in self.glossary.entries:
            self._add_row(e.source, e.target, e.note, auto=e.auto)
        # исправленная или подтверждённая запись перестаёт быть «авто»
        self.table.itemChanged.connect(self._on_item_changed)

        add_btn = QPushButton("+ Добавить")
        del_btn = QPushButton("− Удалить выбранное")
        add_btn.clicked.connect(lambda: self._add_row("", "", "", focus=True))
        del_btn.clicked.connect(self._delete_rows)
        self.auto_btn = QPushButton("Удалить все «авто»")
        self.auto_btn.setToolTip("Удалить записи, которые добавил ИИ-переводчик и которые вы ещё не правили")
        self.auto_btn.clicked.connect(self._delete_auto)

        hint = QLabel("Имена, названия, звания, обращения. Заметки помогают ИИ-переводчикам "
                      "(например: «главный герой, мужчина»). Обычные переводчики используют "
                      "только колонку «Перевод». Пометка «авто» — запись добавил ИИ-переводчик: "
                      "проверьте её; после правки пометка исчезнет.")
        hint.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(QLabel("Название:"))
        top.addWidget(self.name_edit)
        row = QHBoxLayout()
        row.addWidget(add_btn)
        row.addWidget(del_btn)
        row.addWidget(self.auto_btn)
        row.addStretch()
        self.auto_btn.setVisible(any(e.auto for e in self.glossary.entries))

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(hint)
        lay.addWidget(self.table)
        lay.addLayout(row)
        lay.addWidget(buttons)

    def _add_row(self, src: str, tgt: str, note: str, focus: bool = False, auto: bool = False) -> None:
        r = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(r)
        for c, v in enumerate((src, tgt, note)):
            self.table.setItem(r, c, QTableWidgetItem(v))
        mark = QTableWidgetItem(AUTO_MARK if auto else "")
        mark.setFlags(mark.flags() & ~Qt.ItemFlag.ItemIsEditable)
        mark.setToolTip("Добавлено ИИ-переводчиком автоматически" if auto else "")
        mark.setForeground(QColor("#AC8653"))
        self.table.setItem(r, 3, mark)
        self.table.blockSignals(False)
        if focus:
            self.table.setCurrentCell(r, 0)
            self.table.editItem(self.table.item(r, 0))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() < 3:
            mark = self.table.item(item.row(), 3)
            if mark is not None and mark.text():
                self.table.blockSignals(True)
                mark.setText("")
                mark.setToolTip("")
                self.table.blockSignals(False)

    def _delete_auto(self) -> None:
        rows = [r for r in range(self.table.rowCount())
                if self.table.item(r, 3) is not None and self.table.item(r, 3).text()]
        if not rows:
            return
        if QMessageBox.question(self, "Удалить «авто»?", f"Удалить записей: {len(rows)}?") \
                != QMessageBox.StandardButton.Yes:
            return
        for r in reversed(rows):
            self.table.removeRow(r)
        self.auto_btn.setVisible(False)

    def _delete_rows(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def _save(self) -> None:
        entries = []
        for r in range(self.table.rowCount()):
            vals = [(self.table.item(r, c).text().strip() if self.table.item(r, c) else "")
                    for c in range(3)]
            mark = self.table.item(r, 3)
            if vals[0] and vals[1]:
                entries.append(GlossaryEntry(*vals, auto=bool(mark and mark.text())))
        self.glossary.name = self.name_edit.text().strip() or self.glossary.name
        self.glossary.entries = entries
        try:
            self.glossary.save(self.path)
        except OSError as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить: {e}")
            return
        self.accept()
