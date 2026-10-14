import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from lxml import etree as ET
import layout_viewer as ui
from layout_core import Board, default_recipe, write_json
from test_layout_core import fixture


class ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.settings = patch.object(ui,'SETTINGS_PATH',self.root/'settings.json'); self.settings.start()
        self.window = ui.Viewer()
        self.errors = []; self.window.error = lambda error:self.errors.append(str(error))
        self.source = self.root/'board.dipxml'; self.source.write_bytes(ET.tostring(fixture().root))

    def tearDown(self):
        if self.window.worker:
            self.window.worker.requestInterruption(); self.window.worker.wait(5000)
            self.app.processEvents()
        self.window.timer.stop(); self.window.saved = json.dumps(self.window.recipe,sort_keys=True) if self.window.recipe else ''
        self.window.close(); self.settings.stop(); self.temp.cleanup()

    def test_xml_save_as_and_recipe_round_trip(self):
        recipe = self.root/'study.json'
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(recipe),'')):
            self.window.open_path(self.source,ask=False)
        self.assertTrue(recipe.is_file()); self.assertEqual(json.loads(recipe.read_text())['pcb'],'board.dipxml')
        self.window.show(); self.app.processEvents(); self.assertFalse(self.window.rep_panel.isVisible())
        self.window.select('R1'); self.assertIn('R1',self.window.inspector.text())
        self.window.recipe['clearance_mm'] = .35
        other = self.root/'other-study.json'
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(other),'')):
            self.assertTrue(self.window.save_as())
        self.window.open_path(recipe,ask=False); self.assertEqual(self.window.recipe['clearance_mm'],.2)
        self.window.open_path(other,ask=False); self.assertEqual(self.window.recipe['clearance_mm'],.35)
        self.assertFalse(self.errors,self.errors)

    def test_preview_export_and_filled_groups(self):
        recipe = default_recipe(Board(self.source)); recipe['groups'] = [{'name':'Source','refs':['R1','R2']}]
        recipe['view'].update(tracks=True,vias=True); recipe_path = self.root/'study.json'; write_json(recipe_path,recipe)
        self.window.open_path(recipe_path,ask=False); self.window.rep_toggle.setChecked(True); self.window.preview.setChecked(True)
        self.window.refresh_display(); deadline = time.monotonic()+8
        while self.window.worker and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(.005)
        self.app.processEvents(); self.window.timer.stop()
        self.assertIsNone(self.window.worker); self.assertIsNotNone(self.window.preview_board,self.window.preview_error)
        expected = len(self.window.preview_board.parts); original = self.source.read_bytes(); output = self.root/'replicated.dipxml'
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(output),'')):
            self.window.export()
        self.assertFalse(self.errors,self.errors); self.assertEqual(self.source.read_bytes(),original)
        self.assertEqual(len(Board(output).parts),expected)
        saved = json.loads(recipe_path.read_text()); self.assertEqual(len(saved['groups']),2)
        self.assertEqual(saved['replication']['mode'],'Arrange existing groups'); self.assertFalse(saved['view']['preview'])
        self.window.open_path(recipe_path,ask=False); self.assertEqual(self.window.board.path,output)

    def test_existing_recipe_ignores_new_defaults(self):
        recipe = default_recipe(Board(self.source)); path = self.root/'study.json'; write_json(path,recipe)
        import layout_core
        broken = self.root/'bad-defaults.json'; broken.write_text('invalid JSON')
        with patch.object(layout_core,'DEFAULTS_PATH',broken):
            self.window.open_path(path,ask=False)
        self.assertFalse(self.errors,self.errors); self.assertEqual(self.window.recipe['clearance_mm'],.2)


if __name__ == '__main__':
    unittest.main()
