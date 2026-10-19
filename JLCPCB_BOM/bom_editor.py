"""Desktop editor for DipTrace schematic BOM sidecar JSON files."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QTimer, QSettings, QItemSelectionModel, QEvent
from PySide6.QtGui import QColor, QBrush, QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox, QPlainTextEdit, QProgressBar, QSplitter, QAbstractItemView, QMenu, QComboBox, QDialog,
)

from bom_editor_core import Project, FIELDS, grouped_rows, record_group_key, REFS, output_record, read_assignments, validate_lcsc, ref_key, names_match, name_has_added_suffix, value_has_added_suffix, pattern_difference_is_warning, natural_sort_key, value_sort_key
from bom_alternates_dialog import AlternatesDialog
from bom_conversions import CPL_ADJUSTMENTS_PATH
from Shared.search import compile_query, matches_terms
from Shared.platform_helpers import open_file

RED = QBrush(QColor("#ffe0e0"))
INK = QBrush(QColor("#9b2424"))
YELLOW = QBrush(QColor("#fff2cc"))
WARNING_INK = QBrush(QColor("#805400"))
HEADERS = ["Designator(s)", "Input · Name", "Input · Value", "Input · Pattern",
           "JSON · Name", "JSON · Value", "JSON · Pattern", "JSON · JLCPCB",
           "Output · Component", "Output · Footprint", "Output · JLCPCB", "Status"]
EDITABLE = {4: "Name", 5: "Value", 6: "Pattern", 7: "LCSC Part #"}


class Editor(QMainWindow):
    def __init__(self, initial=None):
        super().__init__()
        self.project = None
        self.view_rows = []
        self.expanded = set()
        self.process = None
        self.undo_history = []
        self.redo_history = []
        self.buffer = ""
        self.check_cancelled = False
        self.settings = QSettings("DevSound", "JLCPCBBOMEditor")
        if not self.settings.allKeys():
            legacy_settings = QSettings("DevSound", "DipTraceBOMEditor")
            for key in legacy_settings.allKeys():
                self.settings.setValue(key, legacy_settings.value(key))
            self.settings.setValue("legacy_settings_imported", True)
            self.settings.sync()
        try:
            saved_sort = json.loads(self.settings.value("sort_columns", "[]", type=str))
            self.sort_columns = [(column, descending) for column, descending in saved_sort
                                 if type(column) is int and 0 <= column < len(HEADERS) and type(descending) is bool]
        except (TypeError, ValueError):
            self.sort_columns = []
        self.resize(1600, 900)
        self.setWindowTitle("JLCPCB BOM Editor")
        self.create_menus()
        root = QWidget()
        layout = QVBoxLayout(root)
        self.setCentralWidget(root)
        toolbar = QHBoxLayout()
        layout.addLayout(toolbar)
        self.buttons = {}
        for name, handler in [
            ("Expand selected group", self.expand_selected),
            ("Compact identical", self.compact_identical),
            ("Generate outputs", self.generate),
        ]:
            button = QPushButton(name)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
            self.buttons[name] = button
        toolbar.addStretch()
        self.paths = QLabel("Open a DipTrace .dchxml schematic to begin.")
        self.paths.setWordWrap(True)
        self.paths.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.paths)
        row = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setClearButtonEnabled(True)
        self.filter.installEventFilter(self)
        self.filter.setPlaceholderText("Filter by designator, name, value, footprint, or JLCPCB part number…")
        self.filter.setToolTip("All words must match. Wildcards: * = any text, # = digits, ? = one character.")
        self.filter.textChanged.connect(self.apply_filter)
        row.addWidget(self.filter)
        layout.addLayout(row)
        splitter = QSplitter(Qt.Vertical)
        self.splitter = splitter
        layout.addWidget(splitter, 1)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        for column in range(len(HEADERS) - 1):
            color = "#e8eef8" if column < 4 else "#fff2cc" if column < 8 else "#e7f1e6"
            self.table.horizontalHeaderItem(column).setBackground(QColor(color))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.itemSelectionChanged.connect(self.update_copy_action)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setMinimumSectionSize(18)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().sectionClicked.connect(self.sort_column)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self.sort_header_context_menu)
        self.table.itemChanged.connect(self.edited)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.row_context_menu)
        splitter.addWidget(self.table)
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        progress_row = QHBoxLayout()
        self.progress_label = QLabel("Ready")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.cancel_button = QPushButton("Stop check")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_check)
        progress_row.addWidget(self.progress_label)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.cancel_button)
        log_layout.addLayout(progress_row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.setPlaceholderText("Output generation and live LCSC check progress appear here.")
        log_layout.addWidget(self.log)
        splitter.addWidget(log_panel)
        splitter.setSizes([650, 180])
        for column, width in enumerate([175, 170, 155, 145, 170, 155, 145, 115, 250, 135, 115, 240]):
            self.table.setColumnWidth(column, width)
        geometry = self.settings.value("window_geometry")
        if geometry:
            self.restoreGeometry(geometry)
        header = self.settings.value("column_state")
        if header:
            self.table.horizontalHeader().restoreState(header)
        splitter_state = self.settings.value("splitter_state")
        if splitter_state:
            self.splitter.restoreState(splitter_state)
        self.update_sort_headers()
        self.update_controls()
        QTimer.singleShot(0, lambda: self.open_startup(initial))

    def create_menus(self):
        self.actions = {}

        def action(menu, name, handler, shortcut=None):
            item = QAction(name, self)
            item.triggered.connect(handler)
            if shortcut:
                item.setShortcut(shortcut)
            menu.addAction(item)
            self.actions[name] = item
            return item

        file_menu = self.menuBar().addMenu("&File")
        action(file_menu, "Open schematic…", self.open_picker, QKeySequence.Open)
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self.refresh_recent_menu()
        action(file_menu, "Reload", self.reload, QKeySequence("Ctrl+R"))
        file_menu.addSeparator()
        action(file_menu, "Save JSON", self.save, QKeySequence.Save)
        file_menu.addSeparator()
        action(file_menu, "Exit", self.close, QKeySequence("Alt+F4"))

        edit_menu = self.menuBar().addMenu("&Edit")
        action(edit_menu, "Undo", self.undo_content, QKeySequence("Ctrl+Z"))
        action(edit_menu, "Redo", self.redo_content)
        self.actions["Redo"].setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        edit_menu.addSeparator()
        action(edit_menu, "Focus filter", self.focus_filter, QKeySequence.Find)
        action(edit_menu, "Copy selected rows", self.copy_selected, QKeySequence.Copy)
        edit_menu.addSeparator()
        action(edit_menu, "Expand selected group", self.expand_selected)
        action(edit_menu, "Compact identical", self.compact_identical)
        action(edit_menu, "Use input → JSON", self.use_input)

        tools_menu = self.menuBar().addMenu("&Tools")
        self.back_annotate_menu = tools_menu.addMenu("Back-annotate to schematic")
        action(self.back_annotate_menu, "All", lambda: self.sync(errors_only=False))
        action(self.back_annotate_menu, "Errors only", lambda: self.sync(errors_only=True))
        action(tools_menu, "Generate outputs", self.generate)
        tools_menu.addSeparator()
        action(tools_menu, "JLCPCB part numbers from XLS(X)…", self.import_lcsc)
        action(tools_menu, "Check JLCPCB BOM from XLS(X)…", self.check_lcsc)
        tools_menu.addSeparator()
        action(tools_menu, "Open project settings JSON", self.open_project_settings)
        action(tools_menu, "Open new-project defaults", self.open_project_defaults)
        action(tools_menu, "Open place correction file", self.open_place_corrections)
        action(tools_menu, "Open normalization rules", self.open_normalization_rules)
        action(tools_menu, "Open AI check settings", self.open_ai_check_settings)

        help_menu = self.menuBar().addMenu("&Help")
        action(help_menu, "How to use", self.open_help)
        action(help_menu, "Normalization rule reference", self.open_rule_reference)

    def open_normalization_rules(self):
        from bom_conversions import NORMALIZATION_RULES_PATH
        try:
            open_file(str(NORMALIZATION_RULES_PATH))
        except Exception as exc:
            self.error(exc)

    def open_project_defaults(self):
        from project_config import PROJECT_DEFAULTS_PATH
        try:
            open_file(str(PROJECT_DEFAULTS_PATH))
        except Exception as exc:
            self.error(exc)

    def open_ai_check_settings(self):
        from jlcpcb_bom_checker import AI_CHECK_SETTINGS_PATH
        try:
            open_file(str(AI_CHECK_SETTINGS_PATH))
        except Exception as exc:
            self.error(exc)

    def open_rule_reference(self):
        try:
            open_file(str(Path(__file__).resolve().parent.parent / "Shared" / "NORMALIZATION.txt"))
        except Exception as exc:
            self.error(exc)

    def open_help(self):
        try:
            open_file(str(Path(__file__).with_name("HELP.txt")))
        except Exception as exc:
            self.error(exc)

    def open_project_settings(self):
        self.commit_editor()
        if self.project.dirty:
            answer = QMessageBox.question(
                self, "Save JSON before opening settings?",
                "Save current grid edits before opening the project JSON for editing?",
                QMessageBox.Save | QMessageBox.Cancel, QMessageBox.Save)
            if answer != QMessageBox.Save:
                return
            self.save()
            if self.project.dirty:
                return
        try:
            open_file(str(self.project.json_path))
            self.log.appendPlainText("Opened project settings. Reload after editing to apply changes.")
        except Exception as exc:
            self.error(exc)

    def open_place_corrections(self):
        try:
            open_file(str(CPL_ADJUSTMENTS_PATH))
            self.log.appendPlainText("Opened place correction file. Changes apply on the next output generation.")
        except Exception as exc:
            self.error(exc)

    def recent_files(self):
        files = self.settings.value("recent_files", [])
        return [files] if isinstance(files, str) else list(files or [])

    def refresh_recent_menu(self):
        self.recent_menu.clear()
        files = self.recent_files()
        for index, filename in enumerate(files[:10], 1):
            path = Path(filename)
            label = f"{index}. {path.name} — {path.parent}".replace("&", "&&")
            item = self.recent_menu.addAction(label)
            item.setToolTip(str(path))
            item.setData(str(path))
            item.triggered.connect(lambda checked=False, selected=path: self.open_recent(selected))
        if not files:
            item = self.recent_menu.addAction("No recent schematics")
            item.setEnabled(False)

    def remember_file(self, path):
        path = Path(path).resolve()
        files = [str(path)] + [old for old in self.recent_files()
                              if os.path.normcase(str(Path(old))) != os.path.normcase(str(path))]
        self.settings.setValue("recent_files", files[:10])
        self.settings.setValue("last_file", str(path))
        self.settings.setValue("last_directory", str(path.parent))
        self.settings.sync()
        self.refresh_recent_menu()

    def open_startup(self, initial):
        path = initial or self.settings.value("last_file", "", type=str)
        if path and Path(path).is_file():
            if self.open_path(path):
                return
        elif path:
            self.log.appendPlainText(f"Previous schematic is unavailable: {path}")
        self.open_picker()

    def open_recent(self, path):
        if not self.discard_ok():
            return
        if not Path(path).is_file():
            QMessageBox.information(self, "Schematic unavailable", f"This recent file is unavailable:\n{path}")
            return
        self.open_path(path)

    def focus_filter(self):
        self.filter.setFocus()
        self.filter.selectAll()

    def copy_selected(self):
        self.commit_editor()
        lines = []
        for row in self.selected_rows():
            cells = []
            for column in range(self.table.columnCount()):
                widget = self.table.cellWidget(row, column)
                item = self.table.item(row, column)
                cells.append(widget.currentText() if isinstance(widget, QComboBox) else item.text() if item else "")
            lines.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(lines))

    def update_copy_action(self):
        self.actions["Copy selected rows"].setEnabled(self.process is None and bool(self.selected_rows()))

    def eventFilter(self, watched, event):
        # The filter is navigation, so its native text undo must not swallow
        # the BOM content shortcuts after commands move focus here.
        if watched is getattr(self, "filter", None) and event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress):
            modifiers = event.modifiers()
            undo = event.key() == Qt.Key_Z and modifiers == Qt.ControlModifier
            redo = ((event.key() == Qt.Key_Y and modifiers == Qt.ControlModifier)
                    or (event.key() == Qt.Key_Z and modifiers == (Qt.ControlModifier | Qt.ShiftModifier)))
            if undo or redo:
                event.accept()
                if event.type() == QEvent.KeyPress:
                    (self.undo_content if undo else self.redo_content)()
                return True
        return super().eventFilter(watched, event)

    def change_content(self, updates, description, regroup=False):
        """Record one content operation; saving or generating files never enters history."""
        changes = []
        for (ref, field), value in updates.items():
            previous = self.project.records.get(ref) if field is None else self.project.records[ref][field]
            if previous != value:
                changes.append((ref, field, deepcopy(previous), deepcopy(value)))
        if not changes:
            self.repaint_rows()
            return
        self.undo_history.append((description, changes, regroup))
        self.redo_history.clear()
        self.restore_content(changes, True, regroup)

    def restore_content(self, changes, forward, regroup):
        for ref, field, before, after in changes:
            value = deepcopy(after if forward else before)
            if field is None:
                if value is None:
                    self.project.records.pop(ref, None)
                else:
                    self.project.records[ref] = value
            else:
                self.project.records[ref][field] = value
        if regroup:
            self.rebuild()
        else:
            # Undo after compaction can make a grouped row no longer identical.
            # Split it in place so a later edit cannot overwrite hidden differences.
            rows = []
            for refs in self.view_rows:
                groups = {}
                for ref in refs:
                    key = record_group_key(self.project.records[ref])
                    groups.setdefault(key, []).append(ref)
                rows.extend(groups.values())
            self.view_rows = rows
            self.table.blockSignals(True)
            self.table.setRowCount(len(rows))
            self.table.blockSignals(False)
            self.repaint_rows()
        self.update_controls()
        self.update_status()

    def undo_content(self):
        if not self.project or self.process:
            return
        self.commit_editor()
        if self.undo_history:
            command = self.undo_history.pop()
            self.redo_history.append(command)
            self.restore_content(command[1], False, command[2])
        self.table.setFocus()

    def redo_content(self):
        if not self.project or self.process:
            return
        self.commit_editor()
        if self.redo_history:
            command = self.redo_history.pop()
            self.undo_history.append(command)
            self.restore_content(command[1], True, command[2])
        self.table.setFocus()

    def sort_column(self, column):
        if not self.project or self.process:
            return
        self.commit_editor()
        previous = dict(self.sort_columns)
        descending = previous.get(column, False)
        if self.sort_columns and self.sort_columns[0][0] == column:
            descending = not descending
        self.sort_columns = [(column, descending)] + [entry for entry in self.sort_columns if entry[0] != column]
        self.settings.setValue("sort_columns", json.dumps(self.sort_columns))
        self.settings.sync()
        self.update_sort_headers()
        self.apply_sort()

    def update_sort_headers(self):
        priorities = {column: (index + 1, descending) for index, (column, descending) in enumerate(self.sort_columns)}
        for column, label in enumerate(HEADERS):
            cell = self.table.horizontalHeaderItem(column)
            if column in priorities:
                priority, descending = priorities[column]
                direction = "descending" if descending else "ascending"
                cell.setText(f"{label} [{priority}]")
                cell.setToolTip(f"Sort priority {priority}, {direction}. Click to make primary; click primary again to reverse. Right-click to remove sorting.")
            else:
                cell.setText(label)
                cell.setToolTip("Click to sort. Previous sorts remain secondary.")
        header = self.table.horizontalHeader()
        header.setSortIndicatorShown(bool(self.sort_columns))
        if self.sort_columns:
            column, descending = self.sort_columns[0]
            header.setSortIndicator(column, Qt.DescendingOrder if descending else Qt.AscendingOrder)

    def sort_header_context_menu(self, position):
        header = self.table.horizontalHeader()
        column = header.logicalIndexAt(position)
        if column not in dict(self.sort_columns):
            return
        menu = QMenu(header)
        remove = menu.addAction("Remove sorting")
        remove.setEnabled(self.process is None)
        if menu.exec(header.mapToGlobal(position)) == remove:
            self.remove_sort_column(column)

    def remove_sort_column(self, column):
        if self.process is not None or column not in dict(self.sort_columns):
            return
        self.commit_editor()
        self.sort_columns = [entry for entry in self.sort_columns if entry[0] != column]
        self.settings.setValue("sort_columns", json.dumps(self.sort_columns))
        self.settings.sync()
        self.update_sort_headers()
        self.apply_sort()

    def apply_sort(self):
        if not self.sort_columns or not self.view_rows:
            return
        values = {tuple(refs): [self.table.item(row, column).text() for column in range(len(HEADERS))]
                  for row, refs in enumerate(self.view_rows)}
        selected = {ref for row in self.selected_rows() for ref in self.view_rows[row]}
        current_row, current_column = self.table.currentRow(), self.table.currentColumn()
        current_ref = self.view_rows[current_row][0] if 0 <= current_row < len(self.view_rows) else None

        # Python sorts are stable. Apply the least significant column first,
        # keeping previous sorts within ties of each newer primary column.
        for column, descending in reversed(self.sort_columns):
            key = value_sort_key if column in {2, 5, 8} else natural_sort_key
            self.view_rows.sort(key=lambda refs: key(values[tuple(refs)][column]), reverse=descending)
        self.table.blockSignals(True)
        for row in range(len(self.view_rows)):
            self.fill_row(row)
        self.table.clearSelection()
        for row, refs in enumerate(self.view_rows):
            if selected.intersection(refs):
                self.table.selectionModel().select(self.table.model().index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
            if current_ref in refs:
                self.table.setCurrentCell(row, max(0, current_column), QItemSelectionModel.NoUpdate)
        self.table.blockSignals(False)
        self.apply_filter()

    def error(self, exc):
        self.log.appendPlainText(f"ERROR: {exc}")
        QMessageBox.critical(self, "BOM editor", str(exc))

    def update_controls(self):
        running = self.process is not None
        for name, button in self.buttons.items():
            button.setEnabled(not running and (self.project is not None or name == "Open schematic…"))
        for name, action in self.actions.items():
            action.setEnabled(name in {"Exit", "How to use", "Normalization rule reference", "Focus filter"}
                              or (not running and (self.project is not None or name in {
                                  "Open schematic…", "Open place correction file", "Open normalization rules", "Open AI check settings", "Open new-project defaults"})))
        self.update_copy_action()
        for name, history in (("Undo", self.undo_history), ("Redo", self.redo_history)):
            self.actions[name].setEnabled(not running and self.project is not None and bool(history))
            self.actions[name].setText(f"{name} {history[-1][0]}" if history else name)
        self.recent_menu.setEnabled(not running and bool(self.recent_files()))
        self.back_annotate_menu.setEnabled(not running and self.project is not None)
        self.table.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        suffix = " *" if self.project and self.project.dirty else ""
        title = f"{self.project.path.name} — JLCPCB BOM Editor{suffix}" if self.project else "JLCPCB BOM Editor"
        self.setWindowTitle(title)

    def commit_editor(self):
        # Move focus out of an active cell before saving/generating.
        self.filter.setFocus()
        QApplication.processEvents()

    def discard_ok(self):
        self.commit_editor()
        if self.project and self.project.dirty:
            return QMessageBox.question(self, "Discard changes?", "Discard unsaved JSON edits and reload files?",
                                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
        return True

    def open_picker(self):
        if not self.discard_ok():
            return
        directory = str(self.project.path.parent) if self.project else self.settings.value("last_directory", "", type=str)
        if directory and not Path(directory).is_dir():
            directory = ""
        path, _ = QFileDialog.getOpenFileName(self, "Open DipTrace schematic", directory,
                                              "DipTrace schematic XML (*.dchxml)")
        if path:
            self.settings.setValue("last_directory", str(Path(path).resolve().parent))
            self.settings.sync()
            self.open_path(path)

    def open_path(self, path):
        try:
            project = Project(path)
            self.project = project
            self.undo_history.clear()
            self.redo_history.clear()
            self.expanded.clear()
            self.rebuild()
            self.paths.setText(f"Schematic: {project.path}\nJSON: {project.json_path}   •   XLS folder: {project.output_dir}")
            self.remember_file(project.path)
            self.log.appendPlainText(f"Loaded {len(project.records)} components from {project.path.name}; {len(self.view_rows)} groups.")
            if project.output_errors:
                self.log.appendPlainText("\n".join(project.output_errors))
            self.update_controls()
            return True
        except Exception as exc:
            self.error(exc)
            return False

    def reload(self):
        if self.project and self.discard_ok():
            self.open_path(self.project.path)

    def rebuild(self):
        self.view_rows = []
        for row in grouped_rows(self.project.records):
            refs = REFS.findall(row["RefDes"])
            if self.expanded.intersection(refs):
                self.view_rows.extend([[ref] for ref in refs])
            else:
                self.view_rows.append(refs)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.view_rows))
        for i in range(len(self.view_rows)):
            self.fill_row(i)
        self.table.blockSignals(False)
        self.apply_sort()
        self.apply_filter()
        self.update_status()

    def fill_row(self, index):
        self.table.removeCellWidget(index, 8)
        refs = self.view_rows[index]
        project = self.project
        status = []
        source_mismatch = False
        source_critical_mismatch = False
        source_warnings = []
        output_mismatch = False
        using_alternate = any(project.records[ref].get("Output selection", 0) for ref in refs)

        def item(column, text, mismatch=False, tooltip="", warning=False):
            cell = QTableWidgetItem(text)
            cell.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable |
                          (Qt.ItemIsEditable if column in EDITABLE else Qt.NoItemFlags))
            if mismatch:
                cell.setBackground(YELLOW if warning else RED)
                cell.setForeground(WARNING_INK if warning else INK)
            cell.setToolTip(tooltip or text)
            self.table.setItem(index, column, cell)

        def display(values):
            unique = list(dict.fromkeys(values))
            return unique[0] if len(unique) == 1 else "(mixed)"

        item(0, ", ".join(refs))
        for column, field in [(1, "Name"), (2, "Value"), (3, "Pattern")]:
            values = [project.source.get(ref, {}).get(field, "(missing)") for ref in refs]
            mismatch = any(
                ref not in project.source or not (
                    (names_match(project.source[ref][field], project.records[ref][field])
                     and not name_has_added_suffix(project.source[ref][field], project.records[ref][field]))
                    if field == "Name" else project.source[ref][field].casefold() == project.records[ref][field].casefold()
                ) for ref in refs
            )
            source_mismatch |= mismatch
            warning = field == "Pattern" and all(ref in project.source and (
                project.source[ref][field].casefold() == project.records[ref][field].casefold()
                or pattern_difference_is_warning(project.source[ref][field], project.records[ref][field])
            ) for ref in refs)
            if field == "Name":
                warning = all(ref in project.source and
                              names_match(project.source[ref][field], project.records[ref][field])
                              for ref in refs)
            if field == "Value":
                warning = all(ref in project.source and (
                    project.source[ref][field].casefold() == project.records[ref][field].casefold()
                    or value_has_added_suffix(project.source[ref][field], project.records[ref][field])
                ) for ref in refs)
            if warning and mismatch:
                source_warnings.append(f"{field} suffix" if field in {"Name", "Value"} else field)
            source_critical_mismatch |= mismatch and not warning
            item(column, display(values), mismatch, "\n".join(f"{ref}: {value}" for ref, value in zip(refs, values)), warning=warning)
        for column, field in [(4, "Name"), (5, "Value"), (6, "Pattern"), (7, "LCSC Part #")]:
            values = [project.records[ref][field] for ref in refs]
            item(column, display(values), False, "\n".join(f"{ref}: {value}" for ref, value in zip(refs, values)))
        expected = {ref: output_record(ref, project.records[ref]) for ref in refs}
        for column, field in [(8, "Comment"), (9, "Footprint"), (10, "JLCPCB Part #（optional）")]:
            values = [expected[ref][field] if expected[ref] is not None else "(excluded by rules)" for ref in refs]
            shown_values = []
            mismatch = False
            tips = []
            for ref, preview in zip(refs, values):
                value = expected[ref]
                # Prefer the actual XLS before falling back to JSON's converted
                # preview. A blank value in an output is still actual data.
                existing_output = next(((path, actual[ref]) for path, actual in reversed(project.outputs)
                                        if ref in actual), None)
                if existing_output is not None:
                    path, current_row = existing_output
                    shown_values.append(current_row.get(field, ""))
                    tips.append(f"{ref} · displayed from {path.name}")
                else:
                    shown_values.append(preview)
                    tips.append(f"{ref} · displayed preview (no output row)")
                tips.append(f"{ref} · expected: {preview}")
                for path, actual in project.outputs:
                    existing = actual.get(ref)
                    if value is None:
                        differs = existing is not None
                        current = existing.get(field, "") if existing else "omitted"
                    else:
                        differs = existing is None or existing.get(field, "") != value[field]
                        current = existing.get(field, "") if existing else "(missing)"
                    mismatch |= differs
                    tips.append(f"{ref} · {path.name}: {current}")
            output_mismatch |= mismatch
            tooltip = "Expected: " + display(values) + "\n" + "\n".join(tips)
            item(column, display(shown_values), mismatch or using_alternate, tooltip, warning=using_alternate)
            if column == 8 and project.records[refs[0]].get("Alternates"):
                record = project.records[refs[0]]
                combo = QComboBox(self.table)
                for selection in range(len(record["Alternates"]) + 1):
                    candidate = {**record, "Output selection": selection}
                    converted = output_record(refs[0], candidate)
                    component = record if selection == 0 else record["Alternates"][selection - 1]
                    label = component["Name"] or (converted["Comment"] if converted else "(excluded by rules)") or "(unnamed)"
                    combo.addItem(("Alt: " if selection else "") + label)
                    combo.setItemData(selection, " · ".join(component.get(key, "") for key in (*FIELDS, "Notes") if component.get(key)), Qt.ToolTipRole)
                combo.setCurrentIndex(record.get("Output selection", 0))
                combo.setToolTip("Selected for next generation: " + combo.currentText() + "\n" + tooltip)
                if using_alternate:
                    background, foreground = "#fff2cc", "#805400"
                else:
                    background, foreground = ("#ffe0e0", "#9b2424") if mismatch else ("#e7f1e6", "#202020")
                combo.setStyleSheet(f"QComboBox {{ background-color: {background}; color: {foreground}; border: 0; padding: 0 3px; }}")
                combo.activated.connect(lambda selection, selected_refs=tuple(refs): self.select_output_component(selected_refs, selection))
                combo.setContextMenuPolicy(Qt.CustomContextMenu)
                combo.customContextMenuRequested.connect(
                    lambda position, widget=combo: self.row_context_menu(widget.mapTo(self.table.viewport(), position)))
                self.table.setCellWidget(index, column, combo)
        if any(ref not in project.source for ref in refs):
            status.append("Missing from schematic")
        if all(value is None for value in expected.values()):
            status.append("Excluded")
        if output_mismatch:
            status.append("Generate needed")
        if source_critical_mismatch:
            status.append("Input differs")
        if not status:
            status.append("In sync")
        if source_mismatch and not source_critical_mismatch:
            status.extend(f"{field} differs" for field in source_warnings)
        if using_alternate:
            status.append("Alternate selected")
        item(11, ", ".join(status), source_mismatch or output_mismatch or using_alternate,
             warning=not source_critical_mismatch and (not output_mismatch or using_alternate))

    def edited(self, cell):
        if cell.column() not in EDITABLE or not self.project:
            return
        row = cell.row()
        field = EDITABLE[cell.column()]
        value = cell.text().strip()
        try:
            if field == "LCSC Part #":
                value = value.upper()
                validate_lcsc(value)
            updates = {(ref, field): value for ref in self.view_rows[row]}
            self.change_content(updates, f"Edit {field}")
        except Exception as exc:
            self.error(exc)
            self.table.blockSignals(True)
            self.fill_row(row)
            self.table.blockSignals(False)

    def selected_rows(self):
        return sorted({index.row() for index in self.table.selectionModel().selectedRows()})

    def expand_selected(self):
        self.commit_editor()
        self.expand_rows_at(self.selected_rows())

    def row_context_menu(self, position):
        if not self.project or (self.process and self.process.state() != QProcess.NotRunning):
            return
        self.commit_editor()
        row = self.table.rowAt(position.y())
        if row < 0:
            return
        refs = tuple(self.view_rows[row])
        if row not in self.selected_rows():
            self.table.selectRow(row)
        menu = QMenu(self.table)
        expand = menu.addAction("Expand group")
        expand.setEnabled(len(refs) > 1)
        menu.addSeparator()
        copy_rows = menu.addAction("Copy selected rows")
        menu.addSeparator()
        load_schematic = menu.addAction("Use input → JSON")
        load_schematic.setEnabled(any(ref in self.project.source for ref in refs))
        edit_alternates = menu.addAction("Edit alternates…")
        menu.addSeparator()
        annotate_all = menu.addAction("Back-annotate all")
        annotate_errors = menu.addAction("Back-annotate errors")
        menu.addSeparator()
        remove_missing = menu.addAction("Remove missing from JSON")
        remove_missing.setEnabled(any(ref not in self.project.source for ref in refs))
        chosen = menu.exec(self.table.viewport().mapToGlobal(position))
        if chosen == expand:
            self.expand_rows_at([row])
        elif chosen == copy_rows:
            self.copy_selected()
        elif chosen == annotate_all:
            self.sync(errors_only=False, refs=refs)
        elif chosen == annotate_errors:
            self.sync(errors_only=True, refs=refs)
        elif chosen == load_schematic:
            self.load_schematic_refs(refs)
        elif chosen == edit_alternates:
            self.edit_alternates(refs)
        elif chosen == remove_missing:
            self.remove_missing_refs(refs)

    def remove_missing_refs(self, refs):
        updates = {(ref, None): None for ref in refs if ref not in self.project.source}
        self.change_content(updates, "Remove missing components", regroup=True)

    def edit_alternates(self, refs):
        dialog = AlternatesDialog(refs, self.project.records[refs[0]], self)
        if dialog.exec() != QDialog.Accepted:
            return
        updates = {}
        for ref in refs:
            updates[(ref, "Alternates")] = dialog.alternates
            updates[(ref, "Output selection")] = dialog.selection
        self.change_content(updates, "Edit alternates")

    def select_output_component(self, refs, selection):
        self.change_content({(ref, "Output selection"): selection for ref in refs}, "Select output component")

    def expand_rows_at(self, selected):
        # Split only selected groups at their existing positions. Do not regroup
        # other rows until Reload.
        for index in reversed(selected):
            refs = self.view_rows[index]
            self.expanded.update(refs)
            self.view_rows[index:index + 1] = [[ref] for ref in refs]
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.view_rows))
        for i in range(len(self.view_rows)):
            self.fill_row(i)
        self.table.blockSignals(False)
        self.apply_filter()
        self.update_status()

    def compact_identical(self):
        self.commit_editor()
        self.expanded.clear()
        self.rebuild()

    def use_input(self):
        self.commit_editor()
        rows = self.selected_rows()
        if not rows:
            QMessageBox.information(self, "Use schematic values", "Select the rows to copy into JSON.")
            return
        self.load_schematic_refs([ref for index in rows for ref in self.view_rows[index]])

    def load_schematic_refs(self, refs):
        updates = {}
        for ref in refs:
            if ref in self.project.source:
                for field in ("Name", "Value", "Pattern"):
                    updates[(ref, field)] = self.project.source[ref][field]
        self.change_content(updates, "Load from schematic")

    def repaint_rows(self):
        self.table.blockSignals(True)
        for i in range(len(self.view_rows)):
            self.fill_row(i)
        self.table.blockSignals(False)
        self.apply_filter()
        self.update_controls()
        self.update_status()

    def apply_filter(self):
        terms = compile_query(self.filter.text())
        for i in range(self.table.rowCount()):
            values = [self.table.item(i, c).text() for c in range(self.table.columnCount()) if self.table.item(i, c)]
            alternates = self.project.records[self.view_rows[i][0]].get("Alternates", [])
            values.extend(value for alternate in alternates for value in alternate.values() if isinstance(value, str))
            self.table.setRowHidden(i, not matches_terms(terms, values))
        self.update_status()

    def update_status(self):
        if self.project:
            visible = sum(not self.table.isRowHidden(i) for i in range(self.table.rowCount()))
            self.statusBar().showMessage(f"{len(self.project.records)} components · {len(self.view_rows)} displayed rows · {visible} visible · " +
                                        ("Unsaved edits" if self.project.dirty else "JSON saved"))

    def save(self):
        if not self.project:
            return
        self.commit_editor()
        try:
            self.project.save()
            self.log.appendPlainText(f"Saved {self.project.json_path}")
            self.update_controls()
            self.update_status()
        except Exception as exc:
            self.error(exc)

    def sync(self, errors_only=False, refs=None):
        self.commit_editor()
        try:
            changes, backup = self.project.sync_schematic(errors_only=errors_only, refs=refs)
            mode = "errors only" if errors_only else "all"
            if refs is not None:
                mode += "; " + ", ".join(refs)
            self.log.appendPlainText(f"Back-annotated {changes} Name/Value fields ({mode}) to {self.project.path.name}.")
            if backup:
                self.log.appendPlainText(f"Backup: {backup}")
            self.repaint_rows()
        except Exception as exc:
            self.error(exc)

    def generate(self):
        self.commit_editor()
        try:
            bom, cpl = self.project.generate()
            self.log.appendPlainText(f"Generated {bom} BOM rows and {cpl} CPL rows. XLS: {self.project.output_dir}")
            report = self.project.cpl_report
            if report:
                if report.adjusted:
                    self.log.appendPlainText("CPL footprint adjustments applied: " + ", ".join(report.adjusted))
                if report.removed:
                    self.log.appendPlainText("CPL excluded (not in output BOM): " + ", ".join(report.removed))
                if report.missing:
                    self.log.appendPlainText("WARNING: BOM designators missing from CPL: " + ", ".join(report.missing))
                else:
                    self.log.appendPlainText("CPL and BOM designator sets match.")
            self.repaint_rows()
        except Exception as exc:
            self.error(exc)

    def workbook_picker(self, title):
        return QFileDialog.getOpenFileName(self, title, str(self.project.output_dir),
                                           "LCSC / JLCPCB workbooks (*.xls *.xlsx)")[0]

    def import_lcsc(self):
        self.commit_editor()
        path = self.workbook_picker("Import JLCPCB numbers from workbook")
        if not path:
            return
        try:
            assignments = read_assignments(path)
            filled = replaced = added = declined = 0
            unknown = []
            updates = {}
            import_groups = {}
            for ref, part in assignments.items():
                if ref not in self.project.records:
                    unknown.append(ref)
                    continue
                key = (record_group_key(self.project.records[ref]), part)
                import_groups.setdefault(key, []).append(ref)
            for (_, part), refs in import_groups.items():
                refs.sort(key=ref_key)
                ref = refs[0]
                old = self.project.records[ref]["LCSC Part #"]
                record = self.project.records[ref]
                known_parts = {old.upper(), *(alternate["LCSC Part #"].upper() for alternate in record["Alternates"])}
                if part in known_parts:
                    continue
                if any(known_parts):
                    prompt = QMessageBox(self)
                    prompt.setIcon(QMessageBox.Question)
                    prompt.setWindowTitle("JLCPCB part assignment differs")
                    prompt.setText(f"{', '.join(refs)}: imported {part} is not in the current component list.\nPrimary: {old or '(blank)'}")
                    prompt.setInformativeText("Adding an alternate keeps the primary and output selection. Name, Value, and Pattern are copied from the primary for you to review in Edit alternates.")
                    replace_button = prompt.addButton("Replace primary", QMessageBox.AcceptRole)
                    alternate_button = prompt.addButton("Add as alternate", QMessageBox.ActionRole)
                    skip_button = prompt.addButton("Skip", QMessageBox.RejectRole)
                    prompt.setDefaultButton(skip_button)
                    prompt.setEscapeButton(skip_button)
                    prompt.exec()
                    if prompt.clickedButton() == alternate_button:
                        alternate = {field: record[field] for field in FIELDS}
                        alternate["LCSC Part #"] = part
                        alternate["Notes"] = "Imported LCSC number; component fields copied from primary."
                        for ref in refs:
                            updates[(ref, "Alternates")] = deepcopy(record["Alternates"]) + [deepcopy(alternate)]
                        added += len(refs)
                        continue
                    if prompt.clickedButton() != replace_button:
                        declined += len(refs)
                        continue
                    replaced += len(refs)
                else:
                    filled += len(refs)
                for ref in refs:
                    updates[(ref, "LCSC Part #")] = part
            self.change_content(updates, "Import JLCPCB numbers", regroup=True)
            self.update_controls()
            self.log.appendPlainText(f"LCSC import: filled {filled}, overwritten {replaced}, added alternates {added}, declined {declined}. Save JSON to retain edits.")
            if unknown:
                self.log.appendPlainText("Unknown designators skipped: " + ", ".join(unknown))
        except Exception as exc:
            self.error(exc)

    def check_lcsc(self):
        self.commit_editor()
        path = self.workbook_picker("Select workbook to check LCSC parts")
        if not path:
            return
        try:
            script = self.project.checker_path()
            self.check_path = Path(path)
            self.check_cancelled = False
            self.buffer = ""
            self.download_seen = set()
            self.scrape_seen = set()
            self.total_parts = 0
            self.check_error_count = 0
            # Read using the checker's existing parsing to measure its actual
            # workload, including its original workbook format.
            import importlib.util
            spec = importlib.util.spec_from_file_location("bom_checker", script)
            checker = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(checker)
            self.checker_module = checker
            self.total_parts = len(checker._normalize_parts([row[1] for row in checker._read_excel_rows(path)]))
            if not self.total_parts:
                raise ValueError("No LCSC part numbers found in the selected workbook.")
            self.progress.setRange(0, self.total_parts * 2)
            self.progress.setValue(0)
            self.progress_label.setText(f"Checking {self.total_parts} parts…")
            self.log.appendPlainText(f"Checking {path}\nRunning existing checker: {script}")
            process = QProcess(self)
            self.process = process
            process.setProcessChannelMode(QProcess.MergedChannels)
            process.readyReadStandardOutput.connect(self.check_output)
            process.finished.connect(self.check_finished)
            process.errorOccurred.connect(self.check_process_error)
            process.setWorkingDirectory(str(script.parent))
            python = Path(sys.executable)
            if python.name.lower() == "pythonw.exe":
                python = python.with_name("python.exe")
            process.start(str(python), ["-u", str(script), str(path)])
            self.update_controls()
        except Exception as exc:
            self.error(exc)

    def check_output(self):
        self.buffer += bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        lines = self.buffer.split("\n")
        self.buffer = lines.pop()
        for line in lines:
            self.handle_check_line(line.rstrip("\r"))

    def handle_check_line(self, line):
        self.log.appendPlainText(line)
        match = re.match(r"(C\d+): (.*)", line)
        if match:
            part, message = match.groups()
            if "already have HTML" in message or "downloading" in message:
                self.download_seen.add(part)
            if "already have JSON" in message or "scraping HTML" in message or "missing HTML" in message:
                self.scrape_seen.add(part)
            self.progress.setValue(len(self.download_seen) + len(self.scrape_seen))
            self.progress_label.setText(f"{part}: {message}")
        if "Failed " in line or "scrape failed" in line or "missing HTML" in line:
            self.check_error_count += 1

    def check_process_error(self, error):
        if error == QProcess.FailedToStart:
            self.log.appendPlainText("ERROR: Could not start the LCSC checker.")
            self.process = None
            self.update_controls()

    def check_finished(self, code, status):
        self.check_output()
        if self.buffer:
            self.handle_check_line(self.buffer)
            self.buffer = ""
        self.process.deleteLater()
        self.process = None
        self.update_controls()
        if self.check_cancelled:
            self.progress_label.setText("Check stopped")
            return
        if code != 0:
            self.progress_label.setText("Check failed — see log")
            self.error(f"LCSC checker exited with code {code}. See the progress log.")
            return
        self.progress.setValue(self.progress.maximum())
        self.progress_label.setText("Check completed" if not self.check_error_count else "Check completed with download/scrape errors — see log")
        self.warn_non_rohs()
        report = self.check_path.with_suffix(".txt")
        if report.exists():
            self.log.appendPlainText(f"Opening {report}")
            try:
                open_file(str(report))
            except Exception as exc:
                self.error(exc)

    def warn_non_rohs(self):
        try:
            assignments = read_assignments(self.check_path)
            non_rohs = []
            for ref, part in assignments.items():
                cache = self.checker_module.cache_directory() / f"{part}.json"
                if cache.exists() and self.checker_module._extract_rohs_from_json(cache) == "Non-RoHS":
                    non_rohs.append(ref)
            if non_rohs:
                message = "List contains non-RoHS: " + ", ".join(sorted(non_rohs, key=ref_key))
                self.log.appendPlainText(message)
                QMessageBox.warning(self, "Non-RoHS components", message)
        except Exception as exc:
            # Legacy checker workbooks may not have a Designator column.
            # Still show a warning when its report identifies non-RoHS parts.
            self.log.appendPlainText(f"Could not map RoHS results to designators: {exc}")
            if "Warning: Non-RoHS components:" in self.log.toPlainText().split(f"Checking {self.check_path}")[-1]:
                QMessageBox.warning(self, "Non-RoHS components",
                                    "List contains non-RoHS: designators unavailable in this workbook. See the RoHS report.")

    def cancel_check(self):
        if self.process:
            self.check_cancelled = True
            self.process.kill()

    def closeEvent(self, event):
        if self.process:
            answer = QMessageBox.question(self, "Stop check?", "Stop the active check and close the editor?",
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.cancel_check()
            self.process.waitForFinished(3000)
        self.commit_editor()
        if self.project and self.project.dirty:
            answer = QMessageBox.warning(
                self, "Unsaved JSON changes",
                f"{self.project.json_path.name} has unsaved changes.\n\n"
                "Save them before closing? Choosing Discard will lose these changes.",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save,
            )
            if answer not in (QMessageBox.Save, QMessageBox.Discard):
                event.ignore()
                return
            if answer == QMessageBox.Save:
                try:
                    self.project.save()
                except Exception as exc:
                    self.error(exc)
                    event.ignore()
                    return
        self.settings.setValue("window_geometry", self.saveGeometry())
        self.settings.setValue("column_state", self.table.horizontalHeader().saveState())
        self.settings.setValue("splitter_state", self.splitter.saveState())
        self.settings.sync()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schematic", nargs="?", type=Path, help="Optional .dchxml to open")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    window = Editor(args.schematic)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
