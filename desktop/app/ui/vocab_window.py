"""Окно «Словарик»: слова, отмеченные на экране, — таблица «слово — перевод — заметка».

Слово, перевод и заметку можно править прямо в таблице (сохраняется сразу), есть поиск,
удаление, копирование и выгрузка в CSV — первые два столбца подходят для карточек Anki.
Под таблицей — где слово встретилось: фраза оригинала и её перевод.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
                               QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import i18n, languages, vocab
from ..i18n import tr
from . import theme
from .widgets import caption, label, pill

COLS = ("word", "translation", "note")      # редактируемые столбцы


class VocabDialog(QDialog):
    def __init__(self, parent=None, path=None, target_lang: str = "ru"):
        super().__init__(parent)
        self.path = path      # None — файл словарика в папке данных (vocab.PATH)
        self.target_lang = target_lang
        self.setWindowTitle(tr("Словарик"))
        self.resize(780, 620)
        self.entries = vocab.load(self.path)
        self.shown: list[vocab.Entry] = []
        self._filling = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label(tr("Словарик"), "cardTitle"))
        lay.addWidget(caption(tr("Наведите мышь на слово в переводе и нажмите «В словарик» — слово оригинала "
                                 "попадёт сюда с переводом. Заметку (транскрипцию, род, что угодно) "
                                 "впишите сами — двойной щелчок по ячейке.")))
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Поиск по слову, переводу, заметке или тайтлу"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._fill())
        lay.addWidget(self.search)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Слово"), tr("Перевод"), tr("Заметка"), tr("Язык")])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed)
        pal = theme.palette()
        self.table.setStyleSheet(f"QTableWidget {{ selection-background-color: {pal.selected}; "
                                 f"selection-color: {pal.text}; }}")
        self.table.itemChanged.connect(self._on_edit)
        self.table.currentCellChanged.connect(lambda r, *_: self._show_example(r))
        lay.addWidget(self.table, 1)
        self.example = caption("")
        self.example.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.example)
        self.count = caption("")
        lay.addWidget(self.count)

        row = QHBoxLayout()
        row.setSpacing(8)
        add_btn = pill(tr("Добавить"), "tonal", "plus", tr("Добавить слово вручную"))
        add_btn.clicked.connect(self._add_row)
        row.addWidget(add_btn)
        self.copy_btn = pill(tr("Копировать"), "tonal", "copy", tr("Выбранные слова (или все найденные): слово — перевод"))
        self.copy_btn.clicked.connect(self._copy)
        row.addWidget(self.copy_btn)
        self.csv_btn = pill(tr("Сохранить в CSV…"), "tonal", "download", tr("Таблица для Excel и карточек Anki"))
        self.csv_btn.clicked.connect(self._save_csv)
        row.addWidget(self.csv_btn)
        self.del_btn = pill(tr("Удалить"), "outline", "trash", tr("Удалить выбранные слова"))
        self.del_btn.clicked.connect(self._delete)
        row.addWidget(self.del_btn)
        row.addStretch(1)
        done = pill(tr("Готово"), "primary")
        done.clicked.connect(self.accept)
        row.addWidget(done)
        lay.addLayout(row)
        self._fill()

    # ------------------------------------------------------------ таблица
    def _fill(self) -> None:
        self._filling = True
        self.shown = vocab.search(self.entries, self.search.text())
        self.table.setRowCount(len(self.shown))
        for r, e in enumerate(self.shown):
            for c, field in enumerate(COLS):
                self.table.setItem(r, c, QTableWidgetItem(getattr(e, field)))
            lang = QTableWidgetItem(i18n.lower(languages.name_of(e.lang)) if e.lang else "")
            lang.setFlags(lang.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(r, 3, lang)
        self._filling = False
        n = len(self.shown)
        self.count.setText(tr("Слов: {0}", n) if n == len(self.entries) else tr("Найдено: {0} из {1}", n, len(self.entries)))
        for b in (self.copy_btn, self.csv_btn, self.del_btn):
            b.setEnabled(n > 0)
        self._show_example(self.table.currentRow())
        if not self.entries:
            self.example.setText(tr("Пока пусто: наведите мышь на слово в переводе и нажмите «В словарик»."))

    def _show_example(self, row: int) -> None:
        if not (0 <= row < len(self.shown)):
            self.example.setText("")
            return
        e = self.shown[row]
        parts = []
        if e.context:
            parts.append(tr("Пример: {0}", e.context) + (f" — {e.context_tr}" if e.context_tr else ""))
        meta = [x for x in (e.title, time.strftime("%d.%m.%Y", time.localtime(e.added)) if e.added else "") if x]
        if meta:
            parts.append(" · ".join(meta))
        self.example.setText("\n".join(parts))

    def _on_edit(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() >= len(COLS) or not (0 <= item.row() < len(self.shown)):
            return
        e = self.shown[item.row()]
        setattr(e, COLS[item.column()], item.text().strip())
        self._save()

    def _save(self) -> None:
        # пустые строки (добавили и не заполнили) не сохраняем
        try:
            vocab.save([e for e in self.entries if e.word.strip()], self.path)
        except OSError as err:
            QMessageBox.warning(self, tr("Ошибка"), tr("Не удалось сохранить: {0}", err))

    def _add_row(self) -> None:
        e = vocab.Entry("", lang="", tgt=self.target_lang)
        self.entries.insert(0, e)
        self.search.clear()
        self._fill()
        self.table.setCurrentCell(0, 0)
        self.table.editItem(self.table.item(0, 0))

    def _selected(self) -> list[vocab.Entry]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.shown[r] for r in rows if r < len(self.shown)] or list(self.shown)

    def _copy(self) -> None:
        QApplication.clipboard().setText(vocab.to_text([e for e in self._selected() if e.word]))
        self.copy_btn.setText(tr("Скопировано"))

    def _save_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Сохранить словарик"), "atlate_vocabulary.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            # utf-8 с меткой: Excel тогда правильно открывает кириллицу и корейский
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(vocab.to_csv([e for e in self.shown if e.word]))
        except OSError as e:
            QMessageBox.warning(self, tr("Ошибка"), tr("Не удалось сохранить: {0}", e))

    def _delete(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        chosen = [self.shown[r] for r in rows if r < len(self.shown)]
        if not chosen:
            return
        if QMessageBox.question(self, tr("Удалить слова?"), tr("Удалить из словарика: {0}?", len(chosen))) \
                != QMessageBox.StandardButton.Yes:
            return
        ids = {id(e) for e in chosen}
        self.entries = [e for e in self.entries if id(e) not in ids]
        self._save()
        self._fill()
