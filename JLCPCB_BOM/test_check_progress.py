"""Check preparation must run outside the UI thread and report its stages."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from bom_editor import Editor
from bom_editor_core import expand_rows


class CheckProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(condition())

    def test_preparation_is_responsive_and_transitions_to_counted_progress(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            gate, ready = folder / 'continue', folder / 'ready'
            script = folder / 'checker.py'
            script.write_text(
                'from pathlib import Path\nimport time, sys, json\n'
                'def _read_excel_rows(*args):\n'
                '    raise AssertionError("Workbook must not be read in UI process")\n'
                'if __name__ == "__main__":\n'
                '    snapshot = json.loads(Path(sys.argv[sys.argv.index("--project-json") + 1]).read_text(encoding="utf-8"))\n'
                '    assert snapshot["rows"][0]["Notes"] == "Unsaved note"\n'
                '    assert snapshot["rows"][0]["Output selection"] == 1\n'
                '    print("CHECK_STAGE: Reading current project", flush=True)\n'
                f'    Path({str(ready)!r}).touch()\n'
                f'    while not Path({str(gate)!r}).exists(): time.sleep(0.01)\n'
                '    print("CHECK_TOTAL: 2", flush=True)\n'
                '    print("CHECK_STAGE: Preparing product cache", flush=True)\n'
                '    print("C1: already have HTML", flush=True)\n'
                '    print("C1: already have JSON", flush=True)\n', encoding='utf-8')
            settings = QSettings(str(folder / 'settings.ini'), QSettings.IniFormat)
            settings.setValue('column_layout_version', 1)
            with patch('bom_editor.QSettings', return_value=settings), patch.object(Editor, 'open_startup'), \
                    patch('bom_editor.CHECKER_PATH', script), \
                    patch.object(Editor, 'workbook_picker', side_effect=AssertionError('No workbook picker')), \
                    patch.object(Editor, 'warn_non_rohs'), patch.object(Editor, 'error') as error:
                editor = Editor()
                self.app.processEvents()
                records = expand_rows([{'RefDes': 'R1', 'Name': 'Primary', 'Notes': 'Unsaved note',
                                       'Output selection': 1, 'Alternates': [{'Name': 'Selected', 'LCSC Part #': 'C2'}]}])
                editor.project = SimpleNamespace(dirty=False, records=records, ai_check_path=folder / 'Board_AI_Check.txt',
                                                 path=folder / 'Board.dchxml')
                timer_fired = []
                QTimer.singleShot(0, lambda: timer_fired.append(True))
                try:
                    editor.check_with_ai()
                    self.assertIsNotNone(editor.process)
                    self.assertEqual(editor.progress.maximum(), 0)
                    self.wait_until(lambda: ready.exists() and 'Reading current project' in editor.log.toPlainText())
                    self.assertTrue(timer_fired)
                    self.assertEqual(editor.progress_label.text(), 'Reading current project')
                    self.assertEqual(editor.progress.maximum(), 0)
                    editor.handle_check_line('CHECK_TOTAL: 2')
                    editor.handle_check_line('C1: already have HTML')
                    self.assertEqual((editor.progress.maximum(), editor.progress.value()), (4, 1))
                    gate.touch()
                    self.wait_until(lambda: editor.process is None)
                    self.assertEqual(editor.progress_label.text(), 'Check completed')
                    self.assertEqual((editor.progress.maximum(), editor.progress.value()), (4, 4))
                    error.assert_not_called()
                finally:
                    if editor.process:
                        editor.process.kill()
                        self.wait_until(lambda: editor.process is None)
                    editor.close()


if __name__ == '__main__':
    unittest.main()
