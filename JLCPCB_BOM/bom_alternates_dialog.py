"""Modal editor for the alternate components of a BOM row."""
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from bom_editor_core import ALTERNATE_FIELDS, validate_lcsc


class AlternatesDialog(QDialog):
    def __init__(self, refs, record, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit alternates — " + ", ".join(refs))
        self.resize(950, 380)
        self.primary = record
        self.selection = record.get("Output selection", 0)
        self.alternates = deepcopy(record.get("Alternates", []))
        layout = QVBoxLayout(self)
        primary = QLabel("Primary: " + " · ".join(record[field] for field in ALTERNATE_FIELDS[:-1] if record[field]))
        primary.setTextFormat(Qt.PlainText)
        primary.setWordWrap(True)
        layout.addWidget(primary)
        layout.addWidget(QLabel("Choose the component for output in the main grid. Back-annotation uses the primary."))
        self.table = QTableWidget(0, len(ALTERNATE_FIELDS))
        self.table.setHorizontalHeaderLabels(["Name", "Value", "Pattern", "LCSC number", "Notes"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.verticalHeader().setMinimumSectionSize(18)
        for column, width in enumerate((195, 170, 175, 110, 245)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table)
        for alternate in self.alternates:
            self.add_row(alternate)
        controls = QHBoxLayout()
        add = QPushButton("Add alternate")
        add.clicked.connect(self.add_alternate)
        controls.addWidget(add)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self.remove_selected)
        controls.addWidget(remove)
        controls.addStretch()
        layout.addLayout(controls)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText("Apply")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.original = self.content_state()

    def add_row(self, alternate):
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, field in enumerate(ALTERNATE_FIELDS):
            self.table.setItem(row, column, QTableWidgetItem(alternate.get(field, "")))

    def add_alternate(self):
        self.add_row({"Value": self.primary["Value"], "Pattern": self.primary["Pattern"]})
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)
        self.table.editItem(self.table.currentItem())

    def remove_selected(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()}, reverse=True)
        for row in rows:
            if self.selection == row + 1:
                self.selection = 0
            elif self.selection > row + 1:
                self.selection -= 1
            self.table.removeRow(row)

    def content_state(self):
        return (self.selection, [
            [self.table.item(row, column).text() for column in range(len(ALTERNATE_FIELDS))]
            for row in range(self.table.rowCount())
        ])

    def commit_editor(self):
        self.buttons.setFocus()
        QApplication.processEvents()

    def accept(self):
        self.commit_editor()
        alternates = []
        for row in range(self.table.rowCount()):
            alternate = {field: self.table.item(row, column).text().strip()
                         for column, field in enumerate(ALTERNATE_FIELDS)}
            alternate["LCSC Part #"] = alternate["LCSC Part #"].upper()
            try:
                validate_lcsc(alternate["LCSC Part #"])
                if not any(alternate[field] for field in ALTERNATE_FIELDS[:-1]):
                    raise ValueError("Enter a component or remove the empty alternate.")
            except ValueError as exc:
                QMessageBox.warning(self, "Alternate " + str(row + 1), str(exc))
                return
            alternates.append(alternate)
        self.alternates = alternates
        super().accept()

    def reject(self):
        self.commit_editor()
        if self.content_state() != self.original:
            if QMessageBox.question(self, "Discard alternate edits?", "Discard changes to these alternates?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        super().reject()
