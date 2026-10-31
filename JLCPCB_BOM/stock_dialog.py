"""Stock check dialog; successful values live only in the editor's memory."""
import json
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QTimer
from PySide6.QtGui import QColor, QIntValidator
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from bom_editor_core import output_record
from stock_check import FIELDS

WORKER = Path(__file__).with_name("stock_check.py")
PART_COLUMN = "JLCPCB Part #（optional）"


def stock_rows(records):
    """Use the same selected alternate, normalization and exclusions as BOM output."""
    grouped = Counter()
    for ref, record in records.items():
        output = output_record(ref, record)
        if output is not None:
            grouped[(output["Comment"], output["Footprint"],
                     output[PART_COLUMN].strip().upper())] += 1
    return [(*key, count) for key, count in
            sorted(grouped.items(), key=lambda entry: tuple(value.casefold() for value in entry[0]))]


def price_at_quantity(prices, supplier, required):
    """Return a USD unit price for this quantity, or None if no tier applies."""
    if not isinstance(prices, list) or not required:
        return None
    matches = []
    for tier in prices:
        if not isinstance(tier, dict):
            continue
        try:
            start = int(tier.get("startNumber") if supplier == "jlcpcb" else tier.get("ladder"))
            end = tier.get("endNumber") if supplier == "jlcpcb" else None
            end = int(end) if end is not None else None
            raw_price = tier.get("productPrice") if supplier == "jlcpcb" else tier.get("usdPrice")
            if supplier == "lcsc" and tier.get("isRealOff") and tier.get("discountPrice") is not None:
                try:
                    if (tier.get("currencySymbol") == "$" and
                            Decimal(str(tier.get("currencyPrice"))) == Decimal(str(raw_price))):
                        raw_price = tier["discountPrice"]
                except InvalidOperation:
                    pass
            if raw_price is None:
                continue
            price = Decimal(str(raw_price))
            if start > 0 and start <= required and (end is None or end < 0 or required <= end) and price.is_finite() and price >= 0:
                matches.append((start, price))
        except (ValueError, TypeError, InvalidOperation):
            continue
    return max(matches, key=lambda match: match[0])[1] if matches else None


def price_quote(prices, supplier, required, minimum=None):
    """Return unit price and quoted quantity, including a minimum purchase if needed."""
    if not required or not isinstance(prices, list):
        return None, None
    if type(minimum) is not int or minimum < 1 or supplier != "lcsc":
        minimum = 1
    quoted_quantity = max(required, minimum)
    unit = price_at_quantity(prices, supplier, quoted_quantity)
    if unit is None and supplier == "lcsc":
        starts = []
        for tier in prices:
            try:
                start = int(tier.get("ladder"))
                if start > quoted_quantity and price_at_quantity(prices, supplier, start) is not None:
                    starts.append(start)
            except (AttributeError, TypeError, ValueError):
                continue
        if starts:
            quoted_quantity = min(starts)
            unit = price_at_quantity(prices, supplier, quoted_quantity)
    return unit, quoted_quantity if unit is not None else None


def money(value):
    text = format(value, "f")
    whole, _, decimals = text.partition(".")
    return "$" + whole + "." + decimals.ljust(4, "0")


class StockDialog(QDialog):
    def __init__(self, records, cache, parent=None):
        super().__init__(parent)
        self.cache = cache
        self.rows = stock_rows(records)
        self.parts = list(dict.fromkeys(row[2] for row in self.rows if row[2]))
        self.closing = False
        self.process = None
        self.buffer = b""
        self.jobs = []
        self.completed = 0
        self.cancelled = False
        self.setWindowTitle("Stock check")
        self.resize(1550, 600)
        layout = QVBoxLayout(self)
        quantity_row = QHBoxLayout()
        quantity_row.addWidget(QLabel("Order quantity:"))
        self.order_quantity = QLineEdit("1")
        self.order_quantity.setValidator(QIntValidator(1, 2_147_483_647, self.order_quantity))
        self.order_quantity.setMaximumWidth(120)
        self.order_quantity.textChanged.connect(self.show_values)
        quantity_row.addWidget(self.order_quantity)
        quantity_row.addStretch()
        layout.addLayout(quantity_row)
        self.table = QTableWidget(len(self.rows), 12)
        self.table.setHorizontalHeaderLabels(["Component", "Footprint", "LCSC", "Qty / board", "Required",
                                              "JLCPCB stock", "JLCPCB idle parts", "LCSC stock",
                                              "JLCPCB component price", "JLCPCB sum price",
                                              "LCSC component price", "LCSC sum price"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((220, 125, 105, 95, 95, 120, 135, 115, 155, 145, 145, 130)):
            self.table.setColumnWidth(column, width)
        for index, row in enumerate(self.rows):
            for column, value in enumerate(row[:3]):
                self.table.setItem(index, column, QTableWidgetItem(value))
        layout.addWidget(self.table)
        progress_row = QHBoxLayout()
        self.status = QLabel("Ready")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        progress_row.addWidget(self.status)
        progress_row.addWidget(self.progress, 1)
        layout.addLayout(progress_row)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        self.abort_button = QPushButton("Abort")
        self.abort_button.setEnabled(False)
        self.abort_button.clicked.connect(self.abort)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.abort_button)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.show_values()
        QTimer.singleShot(0, self.check_unknown)

    def show_values(self):
        if not hasattr(self, "table"):
            return
        text = self.order_quantity.text()
        order_qty = int(text) if text.isdigit() and int(text) > 0 and self.order_quantity.hasAcceptableInput() else None
        demand = Counter()
        if order_qty:
            for _, _, part, count in self.rows:
                if part:
                    demand[part] += count * order_qty
        for row, (_, _, part, count) in enumerate(self.rows):
            required = count * order_qty if order_qty else None
            for column, value in ((3, count), (4, required)):
                item = QTableWidgetItem(f"{value:,}" if value is not None else "")
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, column, item)
            for column, field in enumerate(FIELDS, 5):
                value = self.cache.get(part, {}).get(field)
                state = value.get("status") if isinstance(value, dict) else None
                if not part:
                    state = "missing"
                text = (f"{value:,}" if type(value) is int else
                        "No data" if state == "missing" else "Fetch failed" if state == "error" else "")
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if state == "error":
                    item.setBackground(QColor("#ffe0e0"))
                    item.setForeground(QColor("#9b2424"))
                elif type(value) is not int:
                    item.setBackground(QColor("#fff2cc"))
                    item.setForeground(QColor("#805400"))
                elif field == "idle" and part and order_qty and value >= demand[part]:
                    item.setBackground(QColor("#e7f1e6"))
                    item.setForeground(QColor("#236b39"))
                elif field != "idle" and part and order_qty and value < demand[part] * 2:
                    red = value < demand[part]
                    item.setBackground(QColor("#ffe0e0" if red else "#fff2cc"))
                    item.setForeground(QColor("#9b2424" if red else "#805400"))
                item.setToolTip("No LCSC number" if not part else text if state else
                                "Not checked" if value is None else
                                f"Fallback stock; required: {demand[part]:,}" if field == "idle" and order_qty else
                                "Fallback stock" if field == "idle" else
                                f"Required for this part: {demand[part]:,}" if order_qty else "")
                self.table.setItem(row, column, item)
            for field, column in (("jlcpcb", 8), ("lcsc", 10)):
                prices = self.cache.get(part, {}).get(field + "_prices")
                state = prices.get("status") if isinstance(prices, dict) else None
                if not part:
                    state = "missing"
                minimum = self.cache.get(part, {}).get(field + "_minimum")
                unit, quoted = price_quote(prices, field, required, minimum)
                below_minimum = quoted is not None and quoted > required
                for offset, value in enumerate((unit, unit * quoted if unit is not None else None)):
                    label = (money(value) if value is not None else "No data" if state == "missing" or
                             (isinstance(prices, list) and required and unit is None) else
                             "Fetch failed" if state == "error" else "")
                    if offset == 1 and below_minimum:
                        label += f" (min {quoted:,})"
                    item = QTableWidgetItem(label)
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    if state == "error":
                        item.setBackground(QColor("#ffe0e0"))
                        item.setForeground(QColor("#9b2424"))
                    elif value is None or below_minimum:
                        item.setBackground(QColor("#fff2cc"))
                        item.setForeground(QColor("#805400"))
                    item.setToolTip(f"USD; minimum quoted quantity is {quoted:,}" if below_minimum else
                                    "USD, per component" if offset == 0 and value is not None else
                                    "USD, quantity for this line" if value is not None else label)
                    self.table.setItem(row, column + offset, item)

    def check_unknown(self):
        if self.closing:
            return
        self.start([(part, field) for part in self.parts for field in FIELDS
                    if field not in self.cache.get(part, {}) or
                    (isinstance(self.cache[part][field], dict) and
                     self.cache[part][field].get("status") == "error")])

    def refresh(self):
        if self.process:
            return
        self.start([(part, field) for part in self.parts for field in FIELDS])

    def start(self, jobs):
        if self.process:
            return
        self.jobs = jobs
        self.completed = 0
        self.cancelled = False
        self.buffer = b""
        self.progress.setRange(0, max(1, len(jobs)))
        self.progress.setValue(0 if jobs else 1)
        if not jobs:
            self.status.setText("No unknown stock values" if self.parts else "No LCSC numbers to check")
            return
        self.status.setText(f"Checking 0 of {len(jobs)} stock values")
        self.refresh_button.setEnabled(False)
        self.abort_button.setEnabled(True)
        process = QProcess(self)
        self.process = process
        process.readyReadStandardOutput.connect(self.read_output)
        process.finished.connect(self.worker_finished)
        process.errorOccurred.connect(self.process_error)
        process.started.connect(self.send_jobs)
        process.setWorkingDirectory(str(WORKER.parent))
        python = Path(sys.executable)
        if python.name.lower() == "pythonw.exe":
            python = python.with_name("python.exe")
        process.start(str(python), ["-X", "utf8", "-u", str(WORKER)])

    def send_jobs(self):
        if self.process:
            self.process.write(json.dumps(self.jobs).encode("utf-8"))
            self.process.closeWriteChannel()

    def read_output(self):
        if not self.process:
            return
        self.buffer += bytes(self.process.readAllStandardOutput())
        lines = self.buffer.split(b"\n")
        self.buffer = lines.pop()
        for line in lines:
            try:
                result = json.loads(line)
                part, field = result["part"], result["field"]
                if (part, field) not in self.jobs:
                    continue
                value = result.get("value")
                if type(value) is int and value >= 0:
                    self.cache.setdefault(part, {})[field] = value
                else:
                    missing = result.get("missing")
                    self.cache.setdefault(part, {})[field] = {
                        "status": "missing" if missing is not None else "error",
                        "message": str(missing if missing is not None else result.get("error", "Invalid response")),
                    }
                prices = result.get("prices")
                if isinstance(prices, list):
                    self.cache[part][field + "_prices"] = prices
                elif "error" in result:
                    self.cache[part][field + "_prices"] = {"status": "error"}
                else:
                    self.cache[part][field + "_prices"] = {"status": "missing"}
                self.cache[part].pop(field + "_price_data", None)
                if "price_data" in result:
                    self.cache[part][field + "_price_data"] = result["price_data"]
                self.cache[part].pop(field + "_minimum", None)
                if "minimum_quantity" in result:
                    self.cache[part][field + "_minimum"] = result["minimum_quantity"]
                self.completed += 1
                self.progress.setValue(self.completed)
                self.status.setText(f"Checking {self.completed} of {len(self.jobs)} stock values")
            except (ValueError, KeyError, TypeError):
                continue
        self.show_values()

    def process_error(self, error):
        if error == QProcess.FailedToStart:
            self.status.setText("Could not start stock check")
            self.finish_process()

    def worker_finished(self, code, status):
        self.read_output()
        if self.cancelled:
            message = "Aborted; completed results retained"
        elif code != 0 or self.completed != len(self.jobs):
            message = "Stock check failed; unknown values can be retried"
        elif any(isinstance(self.cache.get(part, {}).get(field), dict) and
                 self.cache[part][field].get("status") == "error" for part, field in self.jobs):
            message = "Finished with errors"
        else:
            message = "Stock check complete"
        self.status.setText(message)
        self.finish_process()

    def finish_process(self):
        if self.process:
            self.process.deleteLater()
            self.process = None
        self.refresh_button.setEnabled(True)
        self.abort_button.setEnabled(False)

    def abort(self):
        if self.process:
            self.cancelled = True
            self.status.setText("Aborting…")
            self.abort_button.setEnabled(False)
            self.process.kill()

    def stop_for_close(self):
        self.closing = True
        if self.process:
            self.cancelled = True
            self.process.kill()
            self.process.waitForFinished(2000)
            if self.process:
                self.read_output()
                self.finish_process()

    def done(self, result):
        self.stop_for_close()
        super().done(result)

    def closeEvent(self, event):
        self.stop_for_close()
        super().closeEvent(event)
