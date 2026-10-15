import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QImage, QPainter, QPolygonF, QTransform, QColor, QPen, QBrush
from PySide6.QtTest import QTest
from lxml import etree as ET
import layout_viewer as ui
from layout_core import Board, default_recipe, write_json, Routing, rotate
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

    def test_layer_keys_are_render_only_and_leave_text_entry_alone(self):
        recipe = default_recipe(Board(self.source)); path = self.root/'study.json'; write_json(path,recipe)
        self.window.open_path(path,ask=False); self.window.show(); self.window.activateWindow(); self.app.processEvents()
        self.window.canvas.setFocus(); self.app.processEvents(); key = self.window.key()
        QTest.keyClick(self.window.canvas,Qt.Key_2); self.assertEqual(self.window.layer_combo.currentIndex(),1)
        QTest.keyClick(self.window.canvas,Qt.Key_PageDown); self.assertEqual(self.window.layer_combo.currentIndex(),1)
        QTest.keyClick(self.window.canvas,Qt.Key_PageUp); self.assertEqual(self.window.layer_combo.currentIndex(),0)
        QTest.keyClick(self.window.canvas,Qt.Key_PageUp); self.assertEqual(self.window.layer_combo.currentIndex(),0)
        self.assertEqual(self.window.key(),key)
        for name in ('tracks','vias','pads','labels'):
            self.window.checks[name].toggle()
        self.assertEqual(self.window.key(),key)
        self.window.copy_checks['tracks'].toggle(); self.assertNotEqual(self.window.key(),key)
        self.window.gap.setFocus(); self.app.processEvents()
        QTest.keyClick(self.window.gap,Qt.Key_2); self.assertEqual(self.window.layer_combo.currentIndex(),0)
        self.window.timer.stop()

    def test_preview_requires_expanded_replication_panel(self):
        recipe = default_recipe(Board(self.source)); path = self.root/'study.json'; write_json(path,recipe)
        self.window.open_path(path,ask=False); self.window.show(); self.app.processEvents()
        self.assertFalse(self.window.preview.isEnabled())
        self.window.rep_toggle.setChecked(True); self.assertTrue(self.window.preview.isEnabled())
        self.window.preview.setChecked(True)
        self.window.rep_toggle.setChecked(False)
        self.assertFalse(self.window.preview.isEnabled()); self.assertFalse(self.window.preview.isChecked())
        self.assertTrue(self.window.scope_buttons['All'].isChecked())
        self.assertFalse(self.window.layers_panel.isVisible()); self.window.timer.stop()

    def test_hiding_vias_keeps_current_tracks_visible(self):
        recipe = default_recipe(Board(self.source)); recipe['groups'] = [{'name':'Source','refs':['R1','R2']}]
        recipe['view'].update(tracks=True,vias=False)
        recipe['replication'].update(tracks=False,vias=False,layers=[])
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        self.assertEqual(sum(i.data(1) == 'track' for i in self.window.canvas.scene().items()),2)
        self.assertFalse(any(i.data(1) == 'via' for i in self.window.canvas.scene().items()))

    def test_unfolding_replication_keeps_current_groups_visible(self):
        recipe = default_recipe(Board(self.source))
        recipe['groups'] = [{'name':'A','refs':['R1','R2']},{'name':'B','refs':['R3','R4']}]
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        self.window.rep_toggle.setChecked(True); self.window.timer.stop(); self.window.redraw()
        self.assertEqual(set(self.window.body_items),{'R1','R2','R3','R4'})

    def test_rotated_body_hit_testing_in_mirrored_views(self):
        canvas = self.window.canvas; canvas.show()
        for angle in [0,.4,1.2,2.8]:
            canvas.scene().clear()
            points = [QPointF(*rotate(x,y,angle)) for x,y in [(-2,-.5),(2,-.5),(2,.5),(-2,.5)]]
            item = canvas.scene().addPolygon(QPolygonF(points),QPen(Qt.black),QBrush(Qt.red))
            item.setData(0,'R1'); item.setData(1,'body'); canvas.body_items = {'R1':item}
            for mirror in [False,True]:
                canvas.setTransform(QTransform().scale(-40 if mirror else 40,-40))
                canvas.centerOn(0,0)
                for x,y in [(0,0),(1,.2),(-1,-.2)]:
                    pos = canvas.mapFromScene(QPointF(*rotate(x,y,angle)))
                    self.assertEqual(canvas.ref_at(pos),'R1')
                outside = canvas.mapFromScene(QPointF(*rotate(0,1.2,angle)))
                self.assertEqual(canvas.ref_at(outside),'')
        canvas.body_items = {}

    def test_layer_opacity_is_applied_after_overlapping_tracks_compose(self):
        b = fixture()
        for ref,pos in [('R1',(-10,-10)),('R2',(10,10))]:
            b.parts[ref].element.set('X',str(pos[0])); b.parts[ref].element.set('Y',str(pos[1]))
        for key,coords in [(('0','0'),[(-2,0),(2,0)]),(('0','1'),[(0,-2),(0,2)])]:
            for p,(x,y) in zip(b.traces[key].findall('./Points/Point'),coords):
                p.set('X',str(x)); p.set('Y',str(y)); p.set('Lay','0'); p.set('Width','.4')
        self.source.write_bytes(ET.tostring(b.root)); b = Board(self.source)
        recipe = default_recipe(b); recipe['groups'] = [{'name':'Source','refs':['R1','R2']}]
        recipe['view'].update(tracks=True,labels=False,active_layer='1')
        path = self.root/'study.json'; write_json(path,recipe)
        with patch.object(Board,'routing',return_value=Routing({('0','0'):{0},('0','1'):{0}},set())):
            self.window.open_path(path,ask=False)
        image = QImage(600,600,QImage.Format_ARGB32_Premultiplied); image.fill(QColor('#fafafa'))
        painter = QPainter(image); self.window.canvas.scene().render(painter,QRectF(0,0,600,600),QRectF(-3,-3,6,6)); painter.end()
        overlap = image.pixelColor(300,300); single = image.pixelColor(450,300)
        self.assertEqual(overlap,single)
        copper = QColor(ui.COPPER_COLOR)
        for actual,foreground in zip(overlap.getRgb()[:3],copper.getRgb()[:3]):
            self.assertAlmostEqual(actual,250*2/3+foreground/3,delta=2)


if __name__ == '__main__':
    unittest.main()
