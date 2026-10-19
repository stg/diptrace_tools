import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QTableWidget, QStyleOptionViewItem, QStyle
from bom_editor import Editor, HEADERS, OUTPUT_COLUMN, STATUS_COLUMN
from bom_editor_core import expand_rows


class EditorNoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_note_is_editable_and_does_not_shift_exclusion_status_into_output(self):
        source = {'Name': '', 'Value': '', 'Pattern': 'RES_0402_N', 'LCSC Part #': ''}
        record = {**source, 'Value': 'DNP', 'Notes': 'Do not fit'}
        table = QTableWidget(1, len(HEADERS))
        editor = SimpleNamespace(table=table, view_rows=[['R1']], project=SimpleNamespace(
            source={'R1': source}, records={'R1': record}, outputs=[]))
        Editor.fill_row(editor, 0)
        self.assertEqual(table.item(0, 8).text(), 'Do not fit')
        self.assertTrue(table.item(0, 8).flags() & Qt.ItemIsEditable)
        self.assertEqual([table.item(0, c).text() for c in range(OUTPUT_COLUMN, STATUS_COLUMN)], ['', '', ''])
        self.assertEqual(table.item(0, STATUS_COLUMN).text(), 'Excluded, Value suffix differs')

    def test_saved_widths_and_sorts_migrate_around_inserted_note(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
            old_table = QTableWidget(0, 12)
            old_table.setColumnWidth(8, 333); old_table.setColumnWidth(11, 444)
            settings.setValue('column_state', old_table.horizontalHeader().saveState())
            settings.setValue('sort_columns', json.dumps([[11, False], [8, True]]))
            with patch('bom_editor.QSettings', return_value=settings), patch.object(Editor, 'open_startup'):
                editor = Editor()
                self.app.processEvents()
                self.assertEqual(editor.sort_columns, [(STATUS_COLUMN, False), (OUTPUT_COLUMN, True)])
                self.assertEqual(editor.table.columnWidth(OUTPUT_COLUMN), 333)
                self.assertEqual(editor.table.columnWidth(STATUS_COLUMN), 444)
                self.assertEqual(editor.table.horizontalHeaderItem(8).text(), 'Note')
                self.assertEqual(editor.table.horizontalHeaderItem(8).background().color().name(), '#fff2cc')
                editor.close()
                reopened = Editor()
                self.app.processEvents()
                self.assertEqual(reopened.sort_columns, [(STATUS_COLUMN, False), (OUTPUT_COLUMN, True)])
                reopened.close()

    def test_note_markers_color_note_and_status_without_hiding_primary_status(self):
        source = {'Name': '', 'Value': '', 'Pattern': 'RES_0402_N', 'LCSC Part #': ''}
        record = {**source, 'Notes': ''}
        table = QTableWidget(1, len(HEADERS))
        editor = SimpleNamespace(table=table, view_rows=[['R1']], project=SimpleNamespace(
            source={'R1': source}, records={'R1': record}, outputs=[]))
        for note, color, status in [('Check?', '#fff2cc', 'Note needs review'),
                                    ('Check?!', '#ffe0e0', 'Note requires attention'),
                                    ('Accepted', None, None)]:
            with self.subTest(note=note):
                record['Notes'] = note
                Editor.fill_row(editor, 0)
                self.assertEqual(table.item(0, STATUS_COLUMN).text(),
                                 'In sync' + (', ' + status if status else ''))
                for column in (8, STATUS_COLUMN):
                    if color:
                        self.assertEqual(table.item(0, column).background().color().name(), color)
                    else:
                        self.assertEqual(table.item(0, column).background().style(), Qt.NoBrush)

    def test_output_spans_follow_export_groups_sort_filter_and_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
            settings.setValue('column_layout_version', 1)
            records = expand_rows([
                dict(RefDes=ref, Name='', Value=value, Pattern='RES_0402_N', Notes=note,
                     **{'LCSC Part #': part})
                for ref, value, note, part in [('R1', '10k', 'A', 'C1'), ('R2', '10k', 'C', 'C1'),
                                               ('R3', '20k', 'B', 'C1'), ('R4', '10k', 'D', 'C2')]])
            with patch('bom_editor.QSettings', return_value=settings), patch.object(Editor, 'open_startup'):
                editor = Editor()
                self.app.processEvents()
                editor.project = SimpleNamespace(records=records, source=records, outputs=[],
                                                 dirty=False, path=Path('Board.dchxml'))
                editor.sort_columns = [(5, False), (7, False)]
                editor.rebuild()
                self.assertEqual(editor.view_rows[:2], [['R1'], ['R2']])
                for column in range(OUTPUT_COLUMN, STATUS_COLUMN):
                    self.assertEqual(editor.table.rowSpan(0, column), 2)
                    self.assertEqual(editor.table.columnSpan(0, column), 1)
                    self.assertEqual(editor.table.rowSpan(2, column), 1)  # Different supplier number
                self.assertEqual(editor.table.rowSpan(0, 8), 1)
                self.assertEqual(editor.table.rowSpan(0, STATUS_COLUMN), 1)
                self.assertIn('One generated BOM line: R1, R2', editor.table.item(0, OUTPUT_COLUMN).toolTip())
                editor.table.selectRow(1)
                self.assertTrue(editor.table.selectionModel().isSelected(editor.table.model().index(1, 0)))
                self.assertFalse(editor.table.selectionModel().isSelected(editor.table.model().index(0, 0)))
                for column in range(OUTPUT_COLUMN, STATUS_COLUMN):
                    option = QStyleOptionViewItem()
                    editor.table.itemDelegate().initStyleOption(option, editor.table.model().index(0, column))
                    self.assertTrue(option.state & QStyle.State_Selected)
                editor.table.clearSelection()
                option = QStyleOptionViewItem()
                editor.table.itemDelegate().initStyleOption(option, editor.table.model().index(0, OUTPUT_COLUMN))
                self.assertFalse(option.state & QStyle.State_Selected)
                editor.sort_columns = [(8, False)]
                editor.apply_sort()
                self.assertEqual(editor.view_rows, [['R1'], ['R3'], ['R2'], ['R4']])
                self.assertEqual(editor.table.rowSpan(0, OUTPUT_COLUMN), 1)
                editor.filter.setText('10k')  # R3 is hidden, making R1/R2 visually adjacent.
                self.assertTrue(editor.table.isRowHidden(1))
                self.assertEqual(editor.table.rowSpan(0, OUTPUT_COLUMN), 3)
                editor.filter.clear()
                self.assertEqual(editor.table.rowSpan(0, OUTPUT_COLUMN), 1)
                self.assertNotIn('One generated BOM line', editor.table.item(0, OUTPUT_COLUMN).toolTip())
                editor.sort_columns = [(5, False), (7, False)]
                editor.apply_sort()
                records['R2']['Value'] = '30k'
                editor.repaint_rows()
                self.assertEqual(editor.table.rowSpan(0, OUTPUT_COLUMN), 1)
                editor.close()

    def test_spans_preserve_stale_output_differences_and_restore_alternate_widgets(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
            settings.setValue('column_layout_version', 1)
            records = expand_rows([dict(RefDes='R1, R2', Name='', Value='10k', Pattern='RES_0402_N',
                                       **{'LCSC Part #': 'C1'})])
            records['R1']['Notes'] = 'A'
            records['R2']['Notes'] = 'B'
            records['R2']['Alternates'] = [dict(records['R2'])]
            records['R2']['Output selection'] = 1
            actual = {'R1': {'Comment': 'old value'}, 'R2': {'Comment': 'different old value'}}
            with patch('bom_editor.QSettings', return_value=settings), patch.object(Editor, 'open_startup'):
                editor = Editor()
                self.app.processEvents()
                editor.project = SimpleNamespace(records=records, source=records,
                                                 outputs=[(Path('old.xls'), actual)],
                                                 dirty=False, path=Path('Board.dchxml'))
                editor.rebuild()
                self.assertEqual(editor.table.rowSpan(0, OUTPUT_COLUMN), 2)
                cell = editor.table.item(0, OUTPUT_COLUMN)
                self.assertEqual(cell.text(), '(multiple output values)')
                self.assertIn('different old value', cell.toolTip())
                self.assertEqual(cell.background().color().name(), '#ffe0e0')
                self.assertTrue(editor.table.cellWidget(1, OUTPUT_COLUMN).isHidden())
                editor.filter.setText('B')
                self.assertTrue(editor.table.isRowHidden(0))
                self.assertEqual(editor.table.rowSpan(1, OUTPUT_COLUMN), 1)
                self.assertFalse(editor.table.cellWidget(1, OUTPUT_COLUMN).isHidden())
                self.assertEqual(editor.table.item(0, OUTPUT_COLUMN).text(), 'old value')
                editor.close()


if __name__ == '__main__':
    unittest.main()
