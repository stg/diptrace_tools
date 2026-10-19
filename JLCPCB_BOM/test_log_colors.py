"""Verify semantic log colors, including incomplete RoHS verification."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from bom_editor import Editor, LogView
from bom_conversions import MissingCPLFile


class LogColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def lines(self, log):
        result = []
        block = log.document().begin()
        while block.isValid():
            fragment = block.begin().fragment()
            result.append((block.text(), fragment.charFormat().foreground().color().name()))
            block = block.next()
        return result

    def test_rohs_green_requires_complete_positive_data_and_yellow_requires_unknown_parts(self):
        cases = [
            ({'compliant': ['R1'], 'non_compliant': [], 'unknown': []}, ['#236b39']),
            ({'compliant': ['R1'], 'non_compliant': [], 'unknown': ['R2']}, [None, '#805400']),
            ({'compliant': [], 'non_compliant': [], 'unknown': ['R2']}, ['#805400']),
            ({'compliant': [], 'non_compliant': ['R1'], 'unknown': []}, ['#9b2424']),
            ({'compliant': [], 'non_compliant': ['R1'], 'unknown': ['R2']}, ['#9b2424', '#805400']),
            ({'compliant': [], 'non_compliant': [], 'unknown': []}, [None]),
            (None, ['#9b2424']),
        ]
        with patch('bom_editor.QMessageBox.warning'):
            for result, colors in cases:
                with self.subTest(result=result):
                    log = LogView()
                    Editor.warn_non_rohs(SimpleNamespace(log=log, check_rohs_result=result))
                    lines = self.lines(log)
                    self.assertEqual(len(lines), len(colors))
                    for (_, actual), expected in zip(lines, colors):
                        if expected:
                            self.assertEqual(actual, expected)
                        else:
                            self.assertNotIn(actual, LogView.COLORS.values())

    def test_error_messages_and_missing_cpl_are_distinct_colors_and_plain_text_remains_copyable(self):
        log = LogView()
        log.appendPlainText('ERROR: Something failed')
        log.appendPlainText('C1: scrape failed: invalid response')
        log.appendPlainText('WARNING: BOM designators missing from CPL: R1')
        log.appendPlainText('Next stage')
        colors = [color for _, color in self.lines(log)]
        self.assertEqual(colors[:3], ['#9b2424', '#9b2424', '#805400'])
        self.assertNotIn(colors[3], LogView.COLORS.values())
        self.assertIn('Next stage', log.toPlainText())
        missing = SimpleNamespace(generate=lambda: (_ for _ in ()).throw(MissingCPLFile('Expected CPL is missing')))
        editor = SimpleNamespace(project=missing, log=LogView(), commit_editor=lambda: None)
        with patch('bom_editor.QMessageBox.warning'), patch.object(Editor, 'error') as error:
            Editor.generate(editor)
            error.assert_not_called()
        self.assertEqual(self.lines(editor.log)[0][1], '#805400')


if __name__ == '__main__':
    unittest.main()
