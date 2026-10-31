import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from bom_editor_core import expand_rows
from stock_check import MissingData, get_idle, get_jlcpcb, get_lcsc
from stock_dialog import StockDialog, stock_rows, price_at_quantity, price_quote


def response(data):
    result = Mock()
    result.json.return_value = {"code": 200, "data": data}
    return result


class LookupTests(unittest.TestCase):
    def test_idle_sums_all_pages_and_validates_parts(self):
        session = Mock()
        session.post.side_effect = [
            response({"total": 2, "list": [{"componentCode": "C1", "availableStockNum": 4}],
                      "hasNextPage": True}),
            response({"total": 2, "list": [{"componentCode": "C1", "availableStockNum": 9}],
                      "hasNextPage": False}),
        ]
        self.assertEqual(get_idle(session, "C1")["value"], 13)
        self.assertEqual(session.post.call_args_list[1].kwargs["json"]["pageNum"], 2)
        session.post.side_effect = None
        session.post.return_value = response({"total": 0, "list": None, "hasNextPage": False})
        self.assertEqual(get_idle(session, "C1")["value"], 0)
        session.post.return_value = response({"total": 1, "list": [{"componentCode": "C2", "availableStockNum": 9}]})
        with self.assertRaises(ValueError):
            get_idle(session, "C1")

    def test_supplier_results_require_matching_part_and_valid_quantity(self):
        session = Mock()
        session.get.return_value = response({"componentCode": "C1", "stockCount": 0})
        self.assertEqual(get_jlcpcb(session, "C1")["value"], 0)
        with self.assertRaises(ValueError):
            get_jlcpcb(session, "C2")
        data = {"props": {"pageProps": {"webData": {"productCode": "C1", "stockNumber": 42}}}}
        session.get.return_value.text = '<script id="__NEXT_DATA__">' + json.dumps(data) + '</script>'
        self.assertEqual(get_lcsc(session, "C1")["value"], 42)
        with self.assertRaises(ValueError):
            get_lcsc(session, "C2")
        data["props"]["pageProps"]["webData"]["stockNumber"] = None
        session.get.return_value.text = '<script id="__NEXT_DATA__">' + json.dumps(data) + '</script>'
        self.assertIn("missing", get_lcsc(session, "C1"))

    def test_quantity_breaks_and_missing_prices(self):
        jlc = [{"startNumber": 1, "endNumber": 9, "productPrice": "0.1"},
               {"startNumber": 10, "endNumber": -1, "productPrice": "0.08"}]
        lcsc = [{"ladder": 5, "usdPrice": "0.15"}, {"ladder": 20, "usdPrice": "0.12"}]
        self.assertEqual(str(price_at_quantity(jlc, "jlcpcb", 9)), "0.1")
        self.assertEqual(str(price_at_quantity(jlc, "jlcpcb", 10)), "0.08")
        self.assertIsNone(price_at_quantity(lcsc, "lcsc", 4))
        self.assertEqual(str(price_at_quantity(lcsc, "lcsc", 20)), "0.12")
        self.assertEqual(tuple(map(str, price_quote(lcsc, "lcsc", 4, 5))), ("0.15", "5"))
        discount = [{"ladder": 5, "usdPrice": "0.3046", "currencyPrice": "0.3046",
                     "currencySymbol": "$", "isRealOff": True, "discountPrice": "0.2742"}]
        self.assertEqual(str(price_at_quantity(discount, "lcsc", 5)), "0.2742")
        discount[0]["currencyPrice"] = "2.00"
        self.assertEqual(str(price_at_quantity(discount, "lcsc", 5)), "0.3046")


class StockDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def records(self):
        return expand_rows([
            {"RefDes": "R1, R2", "Name": "", "Value": "10k", "Pattern": "RES_0402_N", "LCSC Part #": "C1"},
            {"RefDes": "R3", "Name": "", "Value": "DNP", "Pattern": "RES_0402_N", "LCSC Part #": "C2"},
            {"RefDes": "R4", "Name": "primary", "Value": "", "Pattern": "A", "LCSC Part #": "C2",
             "Output selection": 1, "Alternates": [{"Name": "alternate", "Value": "", "Pattern": "B", "LCSC Part #": "C3"}]},
            {"RefDes": "R5", "Name": "unassigned", "Value": "", "Pattern": "B", "LCSC Part #": ""},
        ])

    def wait_until(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(predicate(), "Timed out waiting for stock dialog")

    def test_output_rows_follow_selected_alternates_and_exclusions(self):
        rows = stock_rows(self.records())
        self.assertEqual({row[2] for row in rows}, {"C1", "C3", ""})
        self.assertEqual(sum(row[2] == "C1" for row in rows), 1)

    def test_cache_abort_retry_refresh_and_partial_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = Path(temp) / "worker.py"
            worker.write_text('''import json, sys, time\njobs=json.load(sys.stdin)\nfor part,field in jobs:\n print(json.dumps({"part":part,"field":field,**({"error":"offline"} if part=="C3" and field=="idle" else {"value":123})}), flush=True)\n time.sleep(0.12)\n''', encoding="utf-8")
            cache = {"C1": {"jlcpcb": 10, "idle": 20, "lcsc": 30}}
            with patch("stock_dialog.WORKER", worker):
                dialog = StockDialog(self.records(), cache)
                dialog.show()
                self.wait_until(lambda: dialog.completed >= 1)
                self.assertEqual(len(dialog.jobs), 3)
                dialog.abort()
                self.wait_until(lambda: dialog.process is None)
                self.assertEqual(cache["C1"]["jlcpcb"], 10)
                self.assertEqual(cache["C3"]["jlcpcb"], 123)
                dialog.close()
                dialog = StockDialog(self.records(), cache)
                dialog.show()
                self.wait_until(lambda: dialog.process is not None)
                self.assertEqual(dialog.jobs, [("C3", "idle"), ("C3", "lcsc")])
                self.wait_until(lambda: dialog.process is None)
                self.assertEqual(cache["C3"]["idle"]["status"], "error")
                row = next(i for i, data in enumerate(dialog.rows) if data[2] == "C3")
                self.assertEqual(dialog.table.item(row, 6).text(), "Fetch failed")
                self.assertEqual(dialog.table.item(row, 6).background().color().name(), "#ffe0e0")
                dialog.refresh()
                self.assertEqual(len(dialog.jobs), 6)
                self.assertEqual(cache["C1"]["jlcpcb"], 10)
                self.assertEqual(cache["C3"]["lcsc"], 123)
                self.wait_until(lambda: dialog.process is None)
                self.assertEqual(cache["C1"]["jlcpcb"], 123)
                dialog.close()

    def test_refresh_abort_and_close_keep_unfinished_previous_values(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = Path(temp) / "worker.py"
            worker.write_text('''import json,sys,time\njobs=json.load(sys.stdin)\nfor part,field in jobs:\n time.sleep(0.3)\n print(json.dumps({"part":part,"field":field,"value":777}),flush=True)\n''', encoding="utf-8")
            cache = {part: {field: 12 for field in ("jlcpcb", "idle", "lcsc")} for part in ("C1", "C3")}
            with patch("stock_dialog.WORKER", worker):
                dialog = StockDialog(self.records(), cache)
                dialog.show()
                self.app.processEvents()
                dialog.refresh()
                self.wait_until(lambda: dialog.completed == 1)
                dialog.abort()
                self.wait_until(lambda: dialog.process is None)
                self.assertEqual(sum(value == 777 for values in cache.values() for value in values.values()), 1)
                dialog.refresh()
                dialog.reject()
                self.assertIsNone(dialog.process)
                self.assertEqual(sum(value == 777 for values in cache.values() for value in values.values()), 1)

    def test_unknown_is_blank_and_missing_is_yellow(self):
        cache = {"C1": {"idle": {"status": "missing", "message": "No stock data"}}}
        dialog = StockDialog(self.records(), cache)
        row = next(i for i, data in enumerate(dialog.rows) if data[2] == "C1")
        self.assertEqual(dialog.table.item(row, 5).text(), "")
        self.assertEqual(dialog.table.item(row, 5).background().color().name(), "#fff2cc")
        self.assertEqual(dialog.table.item(row, 6).text(), "No data")
        self.assertEqual(dialog.table.item(row, 6).background().color().name(), "#fff2cc")
        dialog.close()

    def test_prices_quantities_and_shared_part_stock_thresholds_update(self):
        records = self.records()
        records["R6"] = {**records["R1"], "Value": "20k"}
        cache = {"C1": {"jlcpcb": 7, "idle": 6, "lcsc": 12,
                        "jlcpcb_prices": [{"startNumber": 1, "endNumber": 3, "productPrice": "0.10"},
                                          {"startNumber": 4, "endNumber": -1, "productPrice": "0.075"}],
                        "lcsc_prices": [{"ladder": 5, "usdPrice": "0.05"}]}}
        dialog = StockDialog(records, cache)
        dialog.order_quantity.setText("2")
        row = next(i for i, data in enumerate(dialog.rows) if data[2] == "C1" and data[3] == 2)
        self.assertEqual(dialog.table.item(row, 3).text(), "2")
        self.assertEqual(dialog.table.item(row, 4).text(), "4")
        self.assertEqual(dialog.table.item(row, 8).text(), "$0.0750")
        self.assertEqual(dialog.table.item(row, 9).text(), "$0.3000")
        self.assertEqual(dialog.table.item(row, 10).text(), "$0.0500")
        self.assertEqual(dialog.table.item(row, 11).text(), "$0.2500 (min 5)")
        self.assertEqual(dialog.table.item(row, 11).background().color().name(), "#fff2cc")
        self.assertEqual(dialog.table.item(row, 5).background().color().name(), "#fff2cc")
        self.assertEqual(dialog.table.item(row, 6).background().color().name(), "#e7f1e6")
        self.assertEqual(dialog.table.item(row, 7).background().style(), Qt.NoBrush)
        dialog.order_quantity.setText("3")
        self.assertEqual(dialog.table.item(row, 5).background().color().name(), "#ffe0e0")
        self.assertEqual(dialog.table.item(row, 6).background().style(), Qt.NoBrush)
        self.assertEqual(dialog.table.item(row, 10).text(), "$0.0500")
        self.assertEqual(dialog.table.item(row, 11).text(), "$0.3000")
        dialog.order_quantity.clear()
        self.assertEqual(dialog.table.item(row, 4).text(), "")
        self.assertEqual(dialog.table.item(row, 8).text(), "")
        dialog.close()


if __name__ == "__main__":
    unittest.main()
