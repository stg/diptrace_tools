import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from bom_editor import Editor
from bom_editor_core import Project, MissingSchematic, expand_rows, grouped_rows


def schematic(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('''<Source Type="DipTrace-Schematic"><Library>
      <Library><Patterns><Pattern PatternStyle="P"><Name>RES_0402_N</Name></Pattern></Patterns></Library>
      <Components><Component ComponentStyle="C"><Part PartType="Normal"><Pattern Style="P"/></Part></Component></Components>
      </Library><Schematic><Components>
      <Part ComponentStyle="C"><RefDes>R1</RefDes><Name>RES</Name><Value>10k</Value></Part>
      <Part ComponentStyle="C"><RefDes>BOOT</RefDes><Name/><Value>DNM</Value></Part>
      </Components></Schematic></Source>''')
    return path


def project_json(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {'schema_version': 1, 'source': str(source), 'output_folder': 'order', 'rows': [
        {'RefDes': 'BOOT', 'Name': '', 'Value': 'DNM', 'Pattern': 'SJ-NO', 'LCSC Part #': '', 'Notes': 'Copper jumper'},
        {'RefDes': 'R1', 'Name': '', 'Value': '22k', 'Pattern': '0402', 'LCSC Part #': 'C1', 'Notes': 'Precision',
         'Alternates': [{'Name': '', 'Value': '22k', 'Pattern': '0402', 'LCSC Part #': 'C2', 'Notes': 'Alternative'}],
         'Output selection': 1}]}
    path.write_text(json.dumps(document))
    return path


class ProjectOpenTests(unittest.TestCase):
    def test_json_relative_source_preserves_chosen_file_and_options(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); source = schematic(folder / 'schematics' / 'Board.dchxml')
            path = project_json(folder / 'choices.json', 'schematics/Board.dchxml')
            project = Project(path)
            self.assertEqual(project.path, source.resolve())
            self.assertEqual(project.json_path, path.resolve())
            self.assertFalse(project.dirty)
            self.assertEqual(project.records['R1']['Output selection'], 1)
            self.assertEqual(project.records['R1']['Alternates'][0]['LCSC Part #'], 'C2')
            self.assertEqual(project.output_dir, source.parent / 'order')
            self.assertEqual(project.cpl_path.parent, source.parent)
            project.records['R1']['Notes'] = 'Changed'
            project.save()
            self.assertEqual(Project(path).records['R1']['Notes'], 'Changed')
            self.assertFalse(source.with_suffix('.json').exists())

    def test_absolute_source_and_missing_source_relocation(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); source = schematic(folder / 'new' / 'Board.dchxml')
            path = project_json(folder / 'choices.json', source.resolve())
            self.assertEqual(Project(path).path, source.resolve())
            path = project_json(path, 'old/Board.dchxml'); before = path.read_bytes()
            with self.assertRaises(MissingSchematic) as raised:
                Project(path)
            self.assertEqual(raised.exception.json_path, path.resolve())
            self.assertEqual(path.read_bytes(), before)
            recovered = Project(path, schematic_path=source)
            self.assertTrue(recovered.dirty)
            self.assertEqual(recovered.records['R1']['Value'], '22k')
            recovered.save()
            self.assertFalse(recovered.dirty)
            self.assertEqual(Project(path).path, source.resolve())
            self.assertFalse(source.with_suffix('.json').exists())

    def test_invalid_json_source_is_not_guessed_or_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'choices.json'
            for source in (None, '', [], 42):
                project_json(path, 'Board.dchxml')
                data = json.loads(path.read_text()); data['source'] = source
                path.write_text(json.dumps(data)); before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, 'source path'):
                    Project(path)
                self.assertEqual(path.read_bytes(), before)

    def test_named_and_numbered_designators_can_share_a_group(self):
        rows = [{'RefDes': 'BOOT, TEST, R1, J6-2', 'Name': '', 'Value': 'DNM', 'Pattern': '', 'LCSC Part #': ''}]
        records = expand_rows(rows)
        self.assertEqual(set(records), {'BOOT', 'TEST', 'R1', 'J6-2'})
        self.assertEqual(expand_rows(grouped_rows(records)), records)


class EditorProjectOpenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_schematic_and_named_json_open_reload_recent_and_boot_row(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); source = schematic(folder / 'Board.dchxml')
            companion = project_json(source.with_suffix('.json'), source.name)
            custom = project_json(folder / 'other-choice.json', source.name)
            settings = QSettings(str(folder / 'settings.ini'), QSettings.IniFormat)
            with patch('bom_editor.QSettings', return_value=settings), patch.object(Editor, 'open_startup'), \
                    patch.object(Editor, 'error', side_effect=lambda error: self.fail(str(error))):
                editor = Editor(); self.app.processEvents()
                self.assertTrue(editor.open_path(source))
                boot_index = next(i for i,g in enumerate(editor.view_rows) if g == ['BOOT'])
                self.assertIn('Excluded', editor.table.item(boot_index, 12).text())
                original = companion.read_bytes()
                self.assertTrue(editor.open_path(custom))
                self.assertEqual(settings.value('last_file'), str(custom.resolve()))
                editor.project.records['R1']['Notes'] = 'Named project edit'
                editor.project.save(); editor.reload()
                self.assertEqual(editor.project.json_path, custom.resolve())
                self.assertEqual(editor.project.records['R1']['Notes'], 'Named project edit')
                self.assertEqual(editor.project.records['R1']['Output selection'], 1)
                self.assertEqual(companion.read_bytes(), original)
                with patch('bom_editor.QFileDialog.getOpenFileName', return_value=('', '')) as picker:
                    editor.open_picker()
                    self.assertIn('*.json', picker.call_args.args[3])
                editor.close()

    def test_missing_source_picker_relinks_and_cancel_keeps_current_project(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); source = schematic(folder / 'moved' / 'Board.dchxml')
            path = project_json(folder / 'choices.json', 'old/Board.dchxml')
            settings = QSettings(str(folder / 'settings.ini'), QSettings.IniFormat)
            with patch('bom_editor.QSettings', return_value=settings), patch.object(Editor, 'open_startup'), \
                    patch.object(Editor, 'error', side_effect=lambda error: self.fail(str(error))):
                editor = Editor(); self.app.processEvents()
                with patch('bom_editor.QFileDialog.getOpenFileName', return_value=(str(source), '')):
                    self.assertTrue(editor.open_path(path))
                self.assertTrue(editor.project.dirty)
                editor.project.save(); editor.reload()
                current = editor.project
                other = project_json(folder / 'missing.json', 'missing/Board.dchxml'); before = other.read_bytes()
                with patch('bom_editor.QFileDialog.getOpenFileName', return_value=('', '')):
                    self.assertFalse(editor.open_path(other))
                self.assertIs(editor.project, current)
                self.assertEqual(other.read_bytes(), before)
                editor.close()


if __name__ == '__main__':
    unittest.main()
