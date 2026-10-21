"""PySide6 desktop browser for the standalone DipTrace part database."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QThread, QTimer, Qt, Signal, QItemSelectionModel
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QCheckBox, QTreeWidget, QTreeWidgetItem, QHeaderView,
    QFileDialog, QMessageBox, QPlainTextEdit, QProgressBar, QSplitter,
    QAbstractItemView, QMenu, QDialog, QDialogButtonBox, QGroupBox, QStyledItemDelegate,
)
from part_locations import LOCATIONS_PATH, load_locations, save_locations

from db_core import (
    APP_DIR, DATABASE_PATH, SETTINGS_PATH, RULES_PATH, SEARCH_FIELDS,
    Cancelled, natural_key, read_json, write_json, refresh_database,
    grouped_results, search_results,
)

from Shared.platform_helpers import open_file, configure_app_icon
from Shared.markdown_help import show_help
from Shared.scrolling import protect_values_from_wheel

DEFAULT_SETTINGS = {
    "schema_version": 1,
    "base_directory": str(APP_DIR.parent.parent),
    "search_fields": ["name", "value", "pattern"],
    "search_text": "",
    "expand_results_by_default": False,
}


class RefreshThread(QThread):
    progress = Signal(int, int, str)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, root, previous, force=False):
        super().__init__()
        self.root, self.previous, self.force = root, previous, force

    def run(self):
        try:
            database = refresh_database(
                self.root, self.previous, force=self.force,
                progress=self.progress.emit, cancelled=self.isInterruptionRequested,
            )
            if self.isInterruptionRequested():
                raise Cancelled()
            write_json(DATABASE_PATH, database)
            self.completed.emit(database)
        except Cancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error))


class ResultItem(QTreeWidgetItem):
    def __lt__(self, other):
        column = self.treeWidget().sortColumn()
        if self.parent() is None and column == 4:
            return int(self.data(4, Qt.UserRole) or 0) < int(other.data(4, Qt.UserRole) or 0)
        return natural_key(self.text(column)) < natural_key(other.text(column))


class LocationDelegate(QStyledItemDelegate):
    def __init__(self, browser):
        super().__init__(browser.tree)
        self.browser = browser

    def createEditor(self, parent, option, index):
        if index.column() != 3 or index.parent().isValid() or self.browser.locations_error:
            return None
        return super().createEditor(parent, option, index)


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Database settings")
        self.resize(620, 260)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Base directory:"))
        directory_row = QHBoxLayout()
        self.base = QLineEdit(settings["base_directory"])
        directory_row.addWidget(self.base, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.choose_base)
        directory_row.addWidget(browse)
        layout.addLayout(directory_row)
        hint = QLabel("A changed base directory takes effect on the next database refresh.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        group = QGroupBox("Search in")
        field_row = QHBoxLayout(group)
        self.fields = {}
        for field, title in zip(SEARCH_FIELDS, ("Name", "Value", "Pattern", "Filename")):
            checkbox = QCheckBox(title)
            checkbox.setChecked(field in settings["search_fields"])
            field_row.addWidget(checkbox)
            self.fields[field] = checkbox
        layout.addWidget(group)
        self.expand = QCheckBox("Expand results by default")
        self.expand.setChecked(bool(settings.get("expand_results_by_default", False)))
        layout.addWidget(self.expand)
        layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_base(self):
        directory = QFileDialog.getExistingDirectory(self, "Choose base directory", self.base.text())
        if directory:
            self.base.setText(directory)

    def save(self):
        if not self.base.text().strip() or not Path(self.base.text().strip()).is_dir():
            QMessageBox.warning(self, "Choose a base directory", "Select an existing directory.")
            return
        self.accept()

    def values(self):
        return {"base_directory": str(Path(self.base.text().strip()).resolve()),
                "search_fields": [field for field, checkbox in self.fields.items() if checkbox.isChecked()],
                "expand_results_by_default": self.expand.isChecked()}


class Browser(QMainWindow):
    def __init__(self):
        super().__init__()
        self.database, self.groups, self.worker = {}, [], None
        self.active_details = None
        self.locations, self.locations_error = {}, False
        self.close_when_finished = False
        self.settings = dict(DEFAULT_SETTINGS)
        self.startup_errors = []
        if SETTINGS_PATH.exists():
            try:
                saved = read_json(SETTINGS_PATH)
                if not isinstance(saved, dict) or saved.get("schema_version") != 1:
                    raise ValueError("Settings must be an object with schema_version 1.")
                for key in ("base_directory", "search_text"):
                    if key in saved and not isinstance(saved[key], str):
                        raise ValueError(f"Settings {key} must be a string.")
                if "search_fields" in saved and (not isinstance(saved["search_fields"], list)
                        or any(field not in SEARCH_FIELDS for field in saved["search_fields"])):
                    raise ValueError("Settings search_fields must be a list of name, value, pattern and/or filename.")
                self.settings.update(saved)
            except Exception as error:
                self.startup_errors.append(f"Could not load settings: {error}")
        self.setWindowTitle("DipTrace Part Database")
        self.resize(1350, 850)
        self.create_menus()
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        toolbar = QHBoxLayout()
        layout.addLayout(toolbar)
        self.refresh_button = QPushButton("Refresh database")
        self.refresh_button.clicked.connect(lambda: self.refresh())
        toolbar.addWidget(self.refresh_button)
        self.stop_button = QPushButton("Stop refresh")
        self.stop_button.clicked.connect(self.cancel_refresh)
        self.stop_button.setEnabled(False)
        toolbar.addWidget(self.stop_button)
        toolbar.addStretch()

        self.metadata = QLabel()
        self.metadata.setWordWrap(True)
        self.metadata.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.metadata)
        search_row = QHBoxLayout()
        layout.addLayout(search_row)
        self.search = QLineEdit(str(self.settings.get("search_text", "")))
        self.search.setClearButtonEnabled(True)
        self.update_filter_placeholder()
        self.search.textChanged.connect(self.schedule_filter)
        search_row.addWidget(self.search, 1)

        self.splitter = QSplitter(Qt.Vertical)
        layout.addWidget(self.splitter, 1)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Name", "Value", "Pattern", "Location", "Files"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setItemDelegate(LocationDelegate(self))
        self.tree.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.itemDoubleClicked.connect(self.result_double_clicked)
        self.tree.itemChanged.connect(self.location_changed)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        self.tree.header().setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((280, 250, 350, 300, 80)):
            self.tree.setColumnWidth(column, width)
        for column in range(5):
            self.tree.headerItem().setBackground(column, QColor("#e8eef8" if column < 3 else "#fff2cc" if column == 3 else "#e7f1e6"))
        self.tree.headerItem().setToolTip(3, "Editable storage location, e.g. Cabinet 2, drawer 7")
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        self.tree.itemExpanded.connect(self.show_details)
        self.tree.itemCollapsed.connect(lambda item: self.part_selected())
        self.tree.itemClicked.connect(lambda item, column: self.part_selected())
        self.tree.itemSelectionChanged.connect(self.part_selected)
        self.splitter.addWidget(self.tree)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Database refresh progress and errors appear here.")
        self.log.setMaximumBlockCount(2000)
        self.splitter.addWidget(self.log)
        self.splitter.setSizes([650, 120])
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status = QLabel("Ready")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(180)
        self.filter_timer.timeout.connect(self.apply_filter)
        self.settings_timer = QTimer(self)
        self.settings_timer.setSingleShot(True)
        self.settings_timer.setInterval(500)
        self.settings_timer.timeout.connect(self.save_settings)
        self.restore_layout()
        self.load_database()
        for error in self.startup_errors:
            self.log.appendPlainText(error)

    def create_menus(self):
        def action(menu, text, callback, shortcut=None):
            result = QAction(text, self)
            result.triggered.connect(callback)
            if shortcut:
                result.setShortcut(QKeySequence(shortcut))
            menu.addAction(result)
            return result
        file_menu = self.menuBar().addMenu("&File")
        self.open_action = action(file_menu, "Open selected schematic", self.open_selected, "Ctrl+O")
        self.open_action.setEnabled(False)
        action(file_menu, "Reload", self.load_database, "Ctrl+R")
        file_menu.addSeparator()
        action(file_menu, "Exit", self.close, "Alt+F4")
        edit_menu = self.menuBar().addMenu("&Edit")
        action(edit_menu, "Focus filter", self.focus_search, "Ctrl+F")
        self.copy_action = action(edit_menu, "Copy selected rows", self.copy_selected, "Ctrl+C")
        edit_menu.addSeparator()
        action(edit_menu, "Expand selected results", lambda: self.expand_selected_results(True))
        action(edit_menu, "Collapse selected results", lambda: self.expand_selected_results(False))
        action(edit_menu, "Expand all results", lambda: self.tree.expandAll())
        action(edit_menu, "Collapse all results", lambda: self.tree.collapseAll())
        tools_menu = self.menuBar().addMenu("&Tools")
        self.refresh_action = action(tools_menu, "Refresh database", lambda: self.refresh(), "F5")
        self.rebuild_action = action(tools_menu, "Rebuild database (read every file)", lambda: self.refresh(force=True))
        self.stop_action = action(tools_menu, "Stop refresh", self.cancel_refresh)
        self.stop_action.setEnabled(False)
        tools_menu.addSeparator()
        self.settings_action = action(tools_menu, "Settings…", self.show_settings)
        action(tools_menu, "Open program settings JSON", self.open_settings)
        action(tools_menu, "Open part locations", self.open_locations_file)
        action(tools_menu, "Open normalization rules", lambda: self.open_file(RULES_PATH))
        help_menu = self.menuBar().addMenu("&Help")
        action(help_menu, "How to use", lambda: show_help(self, APP_DIR / "README.md", "Part Database — How to use"))
        action(help_menu, "Normalization rule reference", lambda: self.open_file(APP_DIR.parent / "Shared" / "NORMALIZATION.txt"))

    def restore_layout(self):
        for key, restore in (("window_geometry", self.restoreGeometry),
                             ("part_column_state", self.tree.header().restoreState),
                             ("splitter_state", self.splitter.restoreState)):
            try:
                if self.settings.get(key):
                    restore(QByteArray.fromHex(self.settings[key].encode("ascii")))
            except (ValueError, TypeError, AttributeError):
                pass

    def save_settings(self):
        self.settings.update({
            "search_text": self.search.text(),
            "window_geometry": bytes(self.saveGeometry().toHex()).decode("ascii"),
            "part_column_state": bytes(self.tree.header().saveState().toHex()).decode("ascii"),
            "splitter_state": bytes(self.splitter.saveState().toHex()).decode("ascii"),
        })
        try:
            write_json(SETTINGS_PATH, self.settings)
        except OSError as error:
            self.log.appendPlainText(f"Could not save settings: {error}")

    def open_settings(self):
        self.save_settings()
        self.open_file(SETTINGS_PATH)

    def open_locations_file(self):
        if not LOCATIONS_PATH.exists():
            save_locations(self.locations, LOCATIONS_PATH)
        self.open_file(LOCATIONS_PATH)

    def open_file(self, path):
        try:
            open_file(str(path))
        except OSError as error:
            QMessageBox.warning(self, "Could not open file", str(error))

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def update_filter_placeholder(self):
        fields = self.settings["search_fields"]
        text = ", ".join(fields[:-1]) + ", or " + fields[-1] if len(fields) > 2 else " or ".join(fields)
        self.search.setPlaceholderText(f"Filter by {text}…" if fields else "Choose filter fields in Tools → Settings…")
        self.search.setToolTip("All words must match the configured fields. Wildcards: * = any text, # = digits, ? = one character.")

    def expand_selected_results(self, expanded):
        for item in self.tree.selectedItems():
            if item.parent() is None:
                item.setExpanded(expanded)

    def show_settings(self):
        if self.worker is not None:
            return
        dialog = SettingsDialog(self.settings, self)
        accepted = dialog.exec() == QDialog.Accepted
        values = dialog.values() if accepted else None
        dialog.deleteLater()
        if not accepted:
            return
        self.settings.update(values)
        self.update_filter_placeholder()
        self.filter_timer.stop()
        self.save_settings()
        self.update_metadata()
        self.apply_filter()

    def load_database(self):
        if self.worker is not None:
            return
        try:
            self.locations = load_locations(LOCATIONS_PATH)
            self.locations_error = False
        except (OSError, ValueError) as error:
            self.locations_error = True
            self.log.appendPlainText(f"Could not load locations; location editing disabled: {error}")
        if DATABASE_PATH.exists():
            try:
                database = read_json(DATABASE_PATH)
                if database.get("schema_version") != 2 or not isinstance(database.get("files"), dict):
                    raise ValueError("Unsupported database format.")
                groups = grouped_results(database)
                self.database, self.groups = database, groups
            except Exception as error:
                self.log.appendPlainText(f"Could not load database: {error}")
                self.status.setText("Database could not be loaded. Use Refresh database to build it.")
        self.update_metadata()
        self.apply_filter()
        if self.database.get("failures"):
            self.log.appendPlainText("Last refresh errors:")
            for failure in self.database["failures"]:
                self.log.appendPlainText(f"{failure['filename']}: {failure['error']}")

    def update_metadata(self):
        if not self.database:
            self.metadata.setText("No database yet. Choose a base directory and refresh to begin.")
            return
        timestamp = self.database.get("refreshed_at", "")
        try:
            timestamp = datetime.fromisoformat(timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
        message = (f"Last refreshed: {timestamp}   ·   {len(self.database['files']):,} schematics"
                   f"   ·   {len(self.groups):,} unique parts   ·   {len(self.database.get('failures', [])):,} errors")
        scanned_base = self.database.get("base_directory", "")
        if str(Path(self.settings["base_directory"]).resolve()) != scanned_base:
            message += f"\nDisplayed database uses {scanned_base}. Refresh to index the selected base directory."
        self.metadata.setText(message)

    def schedule_filter(self):
        self.filter_timer.start()
        self.settings_timer.start()

    def apply_filter(self):
        if not hasattr(self, "tree"):
            return
        fields = self.settings["search_fields"]
        self.tree.setUpdatesEnabled(False)
        self.tree.setSortingEnabled(False)
        self.tree.blockSignals(True)
        self.active_details = None
        self.tree.clear()
        matched, uses_count = 0, 0
        for group, uses in search_results(self.groups, self.search.text(), fields):
            matched += 1
            uses_count += len(uses)
            key = group["name"], group["value"], group["pattern"]
            location = self.locations.get(key, "")
            parent = ResultItem([*key, location, str(len(uses))])
            parent.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            parent.setData(4, Qt.UserRole, len(uses))
            parent.setData(0, Qt.UserRole, uses)
            parent.setData(0, Qt.UserRole + 1, key)
            parent.setData(0, Qt.UserRole + 2, location)
            parent.setToolTip(3, location or "Double-click to enter a drawer or storage location")
            parent.setToolTip(4, f"{len(uses)} matching files; expand for filenames and RefDes")
            self.tree.addTopLevelItem(parent)
            child = QTreeWidgetItem(parent)
            child.setFlags(Qt.ItemIsEnabled)
            child.setFirstColumnSpanned(True)
            parent.setExpanded(bool(self.settings.get("expand_results_by_default", False)))
            if parent.isExpanded():
                self.show_details(parent)
        self.tree.setSortingEnabled(True)
        self.tree.blockSignals(False)
        self.tree.setUpdatesEnabled(True)
        self.update_open_action()
        if self.worker is None:
            self.status.setText(f"{matched:,} parts · {uses_count:,} file matches" +
                                (" · Select at least one search field" if not fields and self.search.text().strip() else ""))

    def part_selected(self):
        self.active_details = None
        self.update_open_action()

    def result_double_clicked(self, item, column):
        if item.parent() is None and column != 3:
            item.setExpanded(not item.isExpanded())

    def location_changed(self, item, column):
        if column != 3 or item.parent() is not None or self.locations_error:
            return
        previous = item.data(0, Qt.UserRole + 2)
        location = item.text(3)
        if location == previous:
            return
        key = tuple(item.data(0, Qt.UserRole + 1))
        updated = dict(self.locations)
        if location:
            updated[key] = location
        else:
            updated.pop(key, None)
        try:
            save_locations(updated, LOCATIONS_PATH)
        except OSError as error:
            self.tree.blockSignals(True)
            item.setText(3, previous)
            self.tree.blockSignals(False)
            QMessageBox.warning(self, "Could not save location", str(error))
            return
        self.locations = updated
        item.setData(0, Qt.UserRole + 2, location)
        item.setToolTip(3, location or "Double-click to enter a drawer or storage location")

    def show_details(self, parent):
        """Create a separate inset table only when a part is expanded."""
        child = parent.child(0)
        if child is None or self.tree.itemWidget(child, 0) is not None:
            return
        uses = parent.data(0, Qt.UserRole)
        wrapper = QWidget()
        wrapper.setObjectName("occurrencePanel")
        wrapper.setStyleSheet("QWidget#occurrencePanel { background: #eef3f7; }")
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(24, 6, 12, 8)
        details = QTreeWidget()
        details.setColumnCount(2)
        details.setHeaderLabels(["Filename", "RefDes"])
        details.setRootIsDecorated(False)
        details.setUniformRowHeights(True)
        details.setAlternatingRowColors(True)
        details.setSelectionMode(QAbstractItemView.ExtendedSelection)
        details.setEditTriggers(QAbstractItemView.NoEditTriggers)
        details.setStyleSheet("QTreeWidget { background: #f7f9fc; alternate-background-color: #edf2f8; border: 1px solid #cbd5df; }")
        details.header().setSectionResizeMode(QHeaderView.Interactive)
        details.header().setStretchLastSection(True)
        details.setColumnWidth(0, max(300, int(self.tree.viewport().width() * 0.65)))
        state = self.settings.get("detail_column_state")
        if isinstance(state, str):
            details.header().restoreState(QByteArray.fromHex(state.encode("ascii")))
        for use in uses:
            row = ResultItem([use["filename"], ", ".join(use["refdes"])])
            row.setData(0, Qt.UserRole, use["filename"])
            row.setToolTip(0, row.text(0))
            row.setToolTip(1, row.text(1))
            details.addTopLevelItem(row)
        details.setSortingEnabled(True)
        details.sortByColumn(0, Qt.AscendingOrder)
        details.itemSelectionChanged.connect(lambda: self.details_selected(details))
        details.itemDoubleClicked.connect(lambda item, column: self.open_filename(item.text(0)))
        details.setContextMenuPolicy(Qt.CustomContextMenu)
        details.customContextMenuRequested.connect(lambda pos: self.details_context_menu(details, pos))
        details.header().sectionResized.connect(lambda: self.remember_detail_columns(details))
        layout.addWidget(details)
        row_height = details.sizeHintForRow(0)
        height = details.header().sizeHint().height() + min(len(uses), 8) * (row_height if row_height > 0 else 20) + 22
        details.setFixedHeight(height)
        wrapper.setFixedHeight(height + 14)
        self.tree.setItemWidget(child, 0, wrapper)

    def remember_detail_columns(self, details):
        self.settings["detail_column_state"] = bytes(details.header().saveState().toHex()).decode("ascii")
        self.settings_timer.start()

    def details_selected(self, details):
        self.active_details = details
        self.update_open_action()

    def update_open_action(self):
        self.open_action.setEnabled(self.active_details is not None and self.active_details.currentItem() is not None)
        table = self.active_details if self.active_details is not None else self.tree
        self.copy_action.setEnabled(bool(table.selectedItems()))

    def open_selected(self):
        if self.active_details is not None:
            item = self.active_details.currentItem()
            if item is not None:
                self.open_filename(item.text(0))

    def open_filename(self, filename):
        root = Path(self.database["base_directory"]).resolve()
        path = (root / filename).resolve()
        if not path.is_relative_to(root):
            QMessageBox.warning(self, "Invalid filename", "Filename is outside the database base directory.")
            return
        self.open_file(path)

    def copy_selected(self):
        table = self.active_details if self.active_details is not None else self.tree
        QApplication.clipboard().setText("\n".join("\t".join(item.text(c) for c in range(table.columnCount()))
                                                   for item in table.selectedItems()))

    def details_context_menu(self, details, position):
        item = details.itemAt(position)
        if item is None:
            return
        if item.isSelected():
            details.setCurrentItem(item, 0, QItemSelectionModel.NoUpdate)
        else:
            details.setCurrentItem(item)
        self.details_selected(details)
        menu = QMenu(self)
        menu.addAction("Open schematic", self.open_selected)
        menu.addSeparator()
        menu.addAction("Copy selected rows", self.copy_selected)
        menu.addAction("Copy filename", lambda: QApplication.clipboard().setText(item.text(0)))
        menu.addAction("Copy RefDes", lambda: QApplication.clipboard().setText(item.text(1)))
        menu.exec(details.viewport().mapToGlobal(position))

    def context_menu(self, position):
        item = self.tree.itemAt(position)
        if item is None or item.parent() is not None:
            return
        if item.isSelected():
            self.tree.setCurrentItem(item, 0, QItemSelectionModel.NoUpdate)
        else:
            self.tree.setCurrentItem(item)
        self.part_selected()
        menu = QMenu(self)
        menu.addAction("Collapse result" if item.isExpanded() else "Expand result",
                       lambda: item.setExpanded(not item.isExpanded()))
        menu.addSeparator()
        menu.addAction("Copy selected rows", self.copy_selected)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def refresh(self, force=False):
        if self.worker is not None:
            return
        root = Path(self.settings["base_directory"])
        if not self.settings["base_directory"].strip() or not root.is_dir():
            QMessageBox.warning(self, "Choose a base directory", "Select an existing directory in Tools → Settings before refreshing.")
            return
        self.settings["base_directory"] = str(root.resolve())
        self.save_settings()
        self.set_refreshing(True)
        self.refresh_success = False
        self.progress.setRange(0, 0)
        self.log.appendPlainText(f"{'Rebuilding' if force else 'Refreshing'}: {root}")
        self.worker = RefreshThread(root, self.database, force)
        self.worker.progress.connect(self.refresh_progress)
        self.worker.completed.connect(self.refresh_complete)
        self.worker.failed.connect(self.refresh_failed)
        self.worker.cancelled.connect(lambda: self.log.appendPlainText("Refresh cancelled; previous database retained."))
        self.worker.finished.connect(self.refresh_finished)
        self.worker.start()

    def set_refreshing(self, refreshing):
        for widget in (self.refresh_button,):
            widget.setEnabled(not refreshing)
        for action in (self.settings_action, self.refresh_action, self.rebuild_action):
            action.setEnabled(not refreshing)
        self.stop_button.setEnabled(refreshing)
        self.stop_action.setEnabled(refreshing)

    def refresh_progress(self, done, total, message):
        self.progress.setRange(0, total if total else 0)
        self.progress.setValue(done)
        self.status.setText(message)

    def refresh_complete(self, database):
        self.refresh_success = True
        self.database = database
        self.groups = grouped_results(database)
        self.update_metadata()
        self.apply_filter()
        scan = database["last_scan"]
        self.log.appendPlainText(f"Refresh complete: {scan['read']:,} read, {scan['reused']:,} unchanged, "
                                 f"{len(database['failures']):,} errors.")
        for failure in database["failures"]:
            self.log.appendPlainText(f"{failure['filename']}: {failure['error']}")

    def refresh_failed(self, error):
        self.log.appendPlainText(f"Refresh failed; previous database retained: {error}")
        QMessageBox.warning(self, "Refresh failed", error)

    def refresh_finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.set_refreshing(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if self.refresh_success else 0)
        self.apply_filter()
        if self.close_when_finished:
            self.close()

    def cancel_refresh(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.stop_button.setEnabled(False)
            self.stop_action.setEnabled(False)
            self.status.setText("Stopping after the current file…")

    def closeEvent(self, event):
        self.tree.setFocus()
        self.save_settings()
        if self.worker is not None:
            self.close_when_finished = True
            self.cancel_refresh()
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    protect_values_from_wheel(app)
    configure_app_icon(app, "part_db")
    app.setStyle("Fusion")
    if sys.platform == "win32":
        app.setFont(QFont("Segoe UI", 9))
    window = Browser()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
