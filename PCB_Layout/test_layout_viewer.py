import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QImage, QPainter, QPolygonF, QTransform, QColor, QPen, QBrush
from PySide6.QtTest import QTest
from lxml import etree as ET
import layout_viewer as ui
from layout_core import Board, default_recipe, write_json, Routing, rotate
from test_layout_core import fixture, linked_grid


class ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.settings = patch.object(ui,'SETTINGS_PATH',self.root/'settings.json'); self.settings.start()
        self.arrange_question = patch.object(ui.QMessageBox,'question',return_value=QMessageBox.No)
        self.question = self.arrange_question.start()
        self.window = ui.Viewer()
        self.errors = []; self.window.error = lambda error:self.errors.append(str(error))
        self.source = self.root/'board.dipxml'; self.source.write_bytes(ET.tostring(fixture().root))

    def tearDown(self):
        if self.window.worker:
            self.window.worker.requestInterruption(); self.window.worker.wait(5000)
            self.app.processEvents()
        self.window.timer.stop(); self.window.saved = json.dumps(self.window.recipe,sort_keys=True) if self.window.recipe else ''
        self.window.close(); self.arrange_question.stop(); self.settings.stop(); self.temp.cleanup()

    def wait_for_replication(self):
        deadline = time.monotonic()+8
        while self.window.worker and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(.005)
        self.app.processEvents(); self.window.timer.stop()
        self.assertIsNone(self.window.worker)
        self.assertFalse(self.window.preview_error,self.window.preview_error)

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
        self.window.refresh_display(); self.wait_for_replication()
        self.assertIsNone(self.window.worker); self.assertIsNotNone(self.window.preview_board,self.window.preview_error)
        expected = len(self.window.preview_board.parts); original = self.source.read_bytes(); output = self.root/'replicated.dipxml'
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(output),'')):
            self.window.export()
        self.assertFalse(self.errors,self.errors); self.assertEqual(self.source.read_bytes(),original)
        self.assertEqual(len(Board(output).parts),expected)
        saved = json.loads(recipe_path.read_text()); self.assertEqual(len(saved['groups']),1)
        self.assertEqual(saved['replication']['mode'],'Create copies')
        self.assertNotIn('replicated_pcb',saved)
        self.assertEqual(self.window.recipe['replicated_pcb'],str(output))
        self.assertEqual(json.loads((self.root/'session.json').read_text())['recipe']['replicated_pcb'],str(output))
        self.window.open_path(recipe_path,ask=False); self.assertEqual(self.window.board.path,self.source)
        self.assertNotIn('replicated_pcb',self.window.recipe)

    def test_export_builds_latest_and_remembers_filename(self):
        recipe = default_recipe(Board(self.source)); recipe['groups'] = [{'name':'Source','refs':['R1','R2']}]
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        self.window.show(); self.window.activateWindow(); self.window.canvas.setFocus(); self.app.processEvents()
        output = self.root/'replicated.dipxml'
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(output),'')) as choose:
            QTest.keyClick(self.window.canvas,Qt.Key_E,Qt.ControlModifier)
            self.wait_for_replication(); choose.assert_called_once()
        self.assertTrue(output.exists()); self.assertEqual(self.window.board.path,self.source)
        self.assertEqual(self.window.recipe['replicated_pcb'],str(output))
        self.assertNotIn('replicated_pcb',json.loads(path.read_text()))
        self.window.inputs['dx'].setValue(9)
        with patch.object(ui.QFileDialog,'getSaveFileName',side_effect=AssertionError('Save asked again')):
            QTest.keyClick(self.window.canvas,Qt.Key_E,Qt.ControlModifier)
            self.wait_for_replication()
        saved_board = Board(output)
        copied = self.window.preview_groups[1]['refs']
        self.assertEqual([saved_board.parts[r].x for r in copied],[9,11])
        other = self.root/'other.dipxml'
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(other),'')) as choose:
            QTest.keyClick(self.window.canvas,Qt.Key_E,Qt.ControlModifier|Qt.ShiftModifier)
            self.wait_for_replication(); choose.assert_called_once()
        self.assertTrue(other.exists()); self.assertEqual(self.window.recipe['replicated_pcb'],str(other))
        self.assertNotIn('replicated_pcb',json.loads(path.read_text()))
        self.assertFalse(self.errors,self.errors)

    def test_in_place_save_refreshes_and_repeated_save_does_not_add_parts(self):
        self.question.return_value = QMessageBox.Yes
        recipe = default_recipe(Board(self.source)); recipe['groups'] = [{'name':'Source','refs':['R1','R2']}]
        recipe['replicated_pcb'] = str(self.source)
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        original = self.source.read_bytes()
        with patch.object(ui.QFileDialog,'getSaveFileName',side_effect=AssertionError('Remembered filename prompted')):
            self.window.export(); self.wait_for_replication()
        saved_board = Board(self.source); count = len(saved_board.parts)
        self.assertEqual(self.window.board.data,self.source.read_bytes()); self.assertNotEqual(self.window.board.data,original)
        self.assertEqual(len(self.window.recipe['groups']),2)
        self.assertEqual(self.window.recipe['replication']['mode'],'Arrange existing groups')
        self.assertFalse(self.window.preview.isChecked()); self.assertEqual(len(self.window.body_items),4)
        backups = list(self.root.glob('board.dipxml.*.bak'))
        self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_bytes(),original)
        with patch.object(ui.QFileDialog,'getSaveFileName',side_effect=AssertionError('Remembered filename prompted')):
            self.window.export(); self.wait_for_replication()
        self.assertEqual(len(Board(self.source).parts),count)
        self.assertFalse(self.errors,self.errors)

    def test_grid_create_overwrite_can_open_generated_groups_in_arrange_mode(self):
        self.question.return_value = QMessageBox.Yes
        recipe = default_recipe(Board(self.source))
        recipe['groups'] = [{'name':'Source','refs':['R1','R2']}]
        recipe['replication'].update(layout='Grid',columns=2,rows=2,column_dx=3,row_dy=4)
        recipe['rotation_offsets'] = {'R1':15}
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        original = self.source.read_bytes(); output = self.source
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(output),'')):
            self.window.export(); self.wait_for_replication()
        self.assertTrue(output.exists()); self.assertFalse(self.errors,self.errors)
        self.question.assert_called_once()
        self.assertTrue(output.exists())  # Question is only reached after the file is written.
        self.assertEqual(self.window.board.path,output)
        self.assertNotEqual(self.source.read_bytes(),original)
        self.assertEqual(next(self.root.glob('board.dipxml.*.bak')).read_bytes(),original)
        saved = json.loads(path.read_text())
        self.assertEqual(len(saved['groups']),1)
        self.assertEqual(saved['replication']['mode'],'Create copies')
        self.assertEqual(len(self.window.recipe['groups']),4)
        self.assertEqual(self.window.recipe['replication']['mode'],'Arrange existing groups')
        self.assertEqual(self.window.recipe['replication']['source'],0)
        for group in self.window.recipe['groups']:
            self.assertEqual(self.window.recipe['rotation_offsets'][group['refs'][0]],15)
        count = len(Board(output).parts)
        self.window.export(); self.wait_for_replication()
        self.assertEqual(len(Board(output).parts),count)
        self.question.assert_called_once()
        self.assertFalse(self.errors,self.errors)

    def test_create_export_as_is_output_only_even_if_switch_question_would_be_yes(self):
        self.question.return_value = QMessageBox.Yes
        recipe = default_recipe(Board(self.source)); recipe['groups'] = [{'name':'Source','refs':['R1','R2']}]
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        self.window.rep_toggle.setChecked(True); self.window.preview.setChecked(True)
        self.window.refresh_display(); self.wait_for_replication()
        original_board = self.window.board; original_preview = self.window.preview_board
        groups = json.dumps(self.window.recipe['groups']); output = self.root/'result.dipxml'
        original_recipe = path.read_bytes()
        with patch.object(self.window,'populate',side_effect=AssertionError('Save as repopulated')), patch.object(self.window,'refresh_display',side_effect=AssertionError('Save as recalculated')):
            self.window.save_replicated_to(output)
        self.question.assert_not_called(); self.assertIs(self.window.board,original_board)
        self.assertIs(self.window.preview_board,original_preview); self.assertTrue(output.exists())
        self.assertEqual(json.dumps(self.window.recipe['groups']),groups)
        self.assertEqual(self.window.recipe['replication']['mode'],'Create copies')
        self.assertEqual(path.read_bytes(),original_recipe)
        self.assertFalse(self.errors,self.errors)

    def test_recipe_shortcuts_only_write_json(self):
        recipe = default_recipe(Board(self.source)); path = self.root/'study.json'
        output = self.root/'result.dipxml'; output.write_bytes(b'Unchanged output')
        recipe['replicated_pcb'] = str(output); write_json(path,recipe)
        self.window.open_path(path,ask=False)
        self.window.show(); self.window.activateWindow(); self.window.canvas.setFocus(); self.app.processEvents()
        original = self.source.read_bytes(); self.window.inputs['dx'].setValue(9)
        with patch.object(self.window,'export',side_effect=AssertionError('Save exported PCB')):
            QTest.keyClick(self.window.canvas,Qt.Key_S,Qt.ControlModifier)
            self.assertEqual(json.loads(path.read_text())['replication']['dx'],9)
            other = self.root/'other.json'
            with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(other),'')):
                QTest.keyClick(self.window.canvas,Qt.Key_S,Qt.ControlModifier|Qt.ShiftModifier)
            self.assertTrue(other.exists()); self.assertEqual(self.window.recipe_path,other)
        self.assertEqual(self.source.read_bytes(),original)
        self.assertEqual(output.read_bytes(),b'Unchanged output')
        self.assertFalse(self.errors,self.errors)

    def test_unnamed_export_does_not_ask_to_save_recipe(self):
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=('','')):
            self.window.open_path(self.source,ask=False)
        output = self.root/'result.dipxml'; original = self.source.read_bytes()
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(output),'')) as choose, \
                patch.object(self.window,'save',side_effect=AssertionError('Export saved recipe')):
            self.window.export(); self.wait_for_replication()
            choose.assert_called_once()
        self.assertTrue(output.exists()); self.assertIsNone(self.window.recipe_path)
        self.assertEqual(self.source.read_bytes(),original)
        self.assertFalse(self.errors,self.errors)

    def test_reload_pcb_preserves_unsaved_recipe_and_updates_preview(self):
        recipe = default_recipe(Board(self.source)); recipe['groups'] = [{'name':'Source','refs':['R1','R2']}]
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        self.window.rep_toggle.setChecked(True); self.window.preview.setChecked(True)
        self.window.inputs['dx'].setValue(9); self.window.mirror.setChecked(True)
        self.window.refresh_display(); self.wait_for_replication()
        self.window.capture_project_view(); view = json.loads(json.dumps(self.window.recipe['view']))
        original_recipe = path.read_bytes()
        changed = Board(self.source); changed.parts['R1'].element.set('X','42')
        self.source.write_bytes(ET.tostring(changed.root))
        # An external recipe edit must not replace the working settings on PCB reload.
        external = json.loads(path.read_text()); external['replication']['dx'] = 77; write_json(path,external)
        self.window.reload(); self.wait_for_replication()
        self.question.assert_not_called()
        self.assertEqual(self.window.board.parts['R1'].x,42)
        self.assertEqual(self.window.preview_board.parts['R1'].x,42)
        self.assertEqual(self.window.inputs['dx'].value(),9)
        self.assertEqual(self.window.recipe['groups'],recipe['groups'])
        self.assertTrue(self.window.preview.isChecked()); self.assertTrue(self.window.mirror.isChecked())
        self.assertAlmostEqual(abs(self.window.canvas.transform().m11()),view['camera']['scale'])
        self.assertEqual(json.loads(path.read_text())['replication']['dx'],77)
        self.assertTrue(self.window.dirty()); self.assertFalse(self.errors,self.errors)

    def test_revert_recipe_cancel_then_discard_reads_saved_json(self):
        recipe = default_recipe(Board(self.source)); path = self.root/'study.json'; write_json(path,recipe)
        self.window.open_path(path,ask=False); self.window.inputs['dx'].setValue(9)
        self.window.mirror.setChecked(True)
        external = json.loads(path.read_text()); external['replication']['dx'] = 7; write_json(path,external)
        saved_bytes = path.read_bytes()
        self.question.return_value = QMessageBox.Cancel; self.window.revert_button.click()
        self.assertEqual(self.window.inputs['dx'].value(),9); self.assertTrue(self.window.mirror.isChecked())
        self.assertEqual(path.read_bytes(),saved_bytes)
        self.question.return_value = QMessageBox.Discard; self.window.revert_button.click()
        self.assertEqual(self.window.inputs['dx'].value(),7); self.assertFalse(self.window.mirror.isChecked())
        self.assertEqual(path.read_bytes(),saved_bytes)
        self.assertFalse(self.window.dirty()); self.assertFalse(self.errors,self.errors)

    def test_relink_keeps_recipe_and_settings_and_rejects_missing_parts(self):
        board,recipe = linked_grid(); self.source.write_bytes(ET.tostring(board.root)); recipe['pcb'] = str(self.source)
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        settings = json.loads(json.dumps(self.window.recipe['replication'])); groups = json.dumps(self.window.recipe['groups'])
        new_source = self.root/'revised.dipxml'; new_source.write_bytes(self.source.read_bytes())
        self.window.rep_toggle.setChecked(True); self.window.preview.setChecked(True)
        self.window.refresh_display(); self.wait_for_replication()
        with patch.object(ui.QFileDialog,'getOpenFileName',return_value=(str(new_source),'')):
            self.window.relink_button.click()
        self.assertEqual(self.window.board.path,new_source); self.assertEqual(self.window.recipe_path,path)
        self.assertEqual(self.window.recipe['replication'],settings)
        self.assertEqual(json.dumps(self.window.recipe['groups']),groups)
        self.assertFalse(self.window.preview.isChecked()); self.assertIsNone(self.window.preview_board)
        self.assertEqual(self.window.input_label.text(),'revised.dipxml'); self.assertTrue(self.window.dirty())
        self.window.save(); self.assertEqual(json.loads(path.read_text())['pcb'],'revised.dipxml')
        self.assertFalse(self.errors,self.errors)
        missing = Board(new_source); missing.parts['U1'].element.find('RefDes').text = 'U99'
        wrong = self.root/'wrong.dipxml'; wrong.write_bytes(ET.tostring(missing.root))
        self.window.relink_to(wrong)
        self.assertEqual(self.window.board.path,new_source); self.assertIn('Unknown part RefDes',self.errors[-1])

    def test_save_as_exports_loaded_preview_when_input_changed_but_will_not_overwrite_input(self):
        recipe = default_recipe(Board(self.source))
        recipe['groups'] = [{'name':'Source','refs':['R1','R2']}]
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        changed = Board(self.source); changed.parts['R1'].element.set('X','42')
        changed_bytes = ET.tostring(changed.root); self.source.write_bytes(changed_bytes)
        output = self.root/'new.dipxml'
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(output),'')):
            self.window.export(); self.wait_for_replication()
        self.assertTrue(output.exists()); self.assertFalse(self.errors,self.errors)
        self.assertEqual(Board(output).parts['R1'].x,0)
        self.assertEqual(self.source.read_bytes(),changed_bytes)
        self.assertEqual(self.window.board.path,self.source)
        self.assertEqual(self.window.recipe['replication']['mode'],'Create copies')
        self.assertEqual(len(self.window.recipe['groups']),1)
        self.window.save_replicated_to(self.source)
        self.assertEqual(self.source.read_bytes(),changed_bytes)
        self.assertIn('input PCB changed on disk',self.errors[-1])
        self.assertEqual(len(list(self.root.glob('board.dipxml.*.bak'))),0)

    def test_replicated_output_path_survives_recipe_save_as(self):
        recipe = default_recipe(Board(self.source)); recipe['replicated_pcb'] = 'replicated.dipxml'
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        folder = self.root/'nested'; folder.mkdir(); other = folder/'study.json'
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(other),'')):
            self.assertTrue(self.window.save_as())
        self.window.open_path(other,ask=False)
        self.assertEqual(self.window.recipe['replicated_pcb'],str(self.root/'replicated.dipxml'))

    def test_failed_replication_does_not_write_or_remember_output(self):
        recipe = default_recipe(Board(self.source))
        recipe['groups'] = [{'name':'Source','refs':['R1','R2']},{'name':'Target','refs':['R3']}]
        recipe['replication']['mode'] = 'Arrange existing groups'
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        output = self.root/'invalid.dipxml'
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(output),'')):
            self.window.export()
        deadline = time.monotonic()+8
        while self.window.worker and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(.005)
        self.window.timer.stop(); self.assertIsNone(self.window.worker)
        self.assertTrue(self.window.preview_error); self.assertIsNone(self.window.pending_output)
        self.assertFalse(output.exists()); self.assertNotIn('replicated_pcb',json.loads(path.read_text()))

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

    def test_grid_rotation_and_preservation_controls_persist(self):
        board,recipe = linked_grid(); self.source.write_bytes(ET.tostring(board.root)); recipe['pcb'] = str(self.source)
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        self.window.rep_toggle.setChecked(True)
        self.assertTrue(self.window.rotate_odd.isChecked()); self.assertTrue(self.window.rotate_axes['Rows'].isChecked())
        self.assertTrue(self.window.preserve_skipped.isEnabled()); self.assertTrue(self.window.preserve_skipped.isChecked())
        self.window.rotate_axes['Columns'].setChecked(True); self.window.preserve_skipped.setChecked(False)
        self.window.grid_order.setCurrentText('Column first')
        self.window.save(); self.window.open_path(path,ask=False)
        self.assertEqual(self.window.recipe['replication']['rotate_odd_axis'],'Columns')
        self.assertEqual(self.window.grid_order.currentText(),'Column first')
        self.assertFalse(self.window.preserve_skipped.isChecked())
        self.window.rep_fields['mode'].setCurrentText('Create copies')
        self.assertFalse(self.window.preserve_skipped.isEnabled())
        self.window.rotate_odd.setChecked(False)
        self.assertTrue(all(not button.isEnabled() for button in self.window.rotate_axes.values()))
        self.assertFalse(self.errors,self.errors)

    def test_close_restores_unsaved_named_project_without_saving_replication_edits(self):
        recipe = default_recipe(Board(self.source)); recipe['groups'] = [{'name':'Source','refs':['R1','R2']}]
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        original_step = recipe['replication']['dx']
        self.window.rep_toggle.setChecked(True); self.window.inputs['dx'].setValue(9)
        self.window.mirror.setChecked(True); self.window.canvas.scale(1.5,1.5)
        self.window.close(); self.question.assert_not_called()
        saved = json.loads(path.read_text())
        self.assertEqual(saved['replication']['dx'],original_step)
        self.assertTrue(saved['view']['mirror']); self.assertTrue(saved['view']['replication_open'])
        scale = saved['view']['camera']['scale']
        self.window = ui.Viewer(); self.window.error = lambda e:self.errors.append(str(e))
        self.app.processEvents()
        self.assertEqual(self.window.recipe_path,path)
        self.assertEqual(self.window.inputs['dx'].value(),9)
        self.assertTrue(self.window.mirror.isChecked()); self.assertTrue(self.window.rep_toggle.isChecked())
        self.assertAlmostEqual(abs(self.window.canvas.transform().m11()),scale)
        self.assertTrue(self.window.dirty()); self.assertFalse(self.errors,self.errors)

    def test_unnamed_session_restores_but_switching_projects_still_asks(self):
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=('','')):
            self.window.open_path(self.source,ask=False)
        self.assertIsNone(self.window.recipe_path)
        self.window.inputs['dx'].setValue(8)
        self.window.close(); self.question.assert_not_called()
        self.window = ui.Viewer(); self.window.error = lambda e:self.errors.append(str(e))
        self.app.processEvents()
        self.assertIsNone(self.window.recipe_path); self.assertEqual(self.window.inputs['dx'].value(),8)
        other = self.root/'another.json'; write_json(other,default_recipe(Board(self.source)))
        self.question.return_value = QMessageBox.Cancel
        self.window.open_path(other)
        self.question.assert_called_once(); self.assertIsNone(self.window.recipe_path)
        self.assertEqual(self.window.inputs['dx'].value(),8)
        self.assertFalse(self.errors,self.errors)

    def test_startup_file_does_not_discard_a_previous_unsaved_session(self):
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=('','')):
            self.window.open_path(self.source,ask=False)
        self.window.inputs['dx'].setValue(11); self.window.close()
        other = self.root/'another.json'; write_json(other,default_recipe(Board(self.source)))
        self.question.return_value = QMessageBox.Cancel
        self.window = ui.Viewer(other); self.window.error = lambda e:self.errors.append(str(e))
        self.app.processEvents()
        self.question.assert_called_once()
        self.assertIsNone(self.window.recipe_path); self.assertEqual(self.window.inputs['dx'].value(),11)
        self.assertFalse(self.errors,self.errors)

    def test_all_displays_intergroup_tracks_once_and_source_stays_local(self):
        board,recipe = linked_grid(); self.source.write_bytes(ET.tostring(board.root)); recipe['pcb'] = str(self.source)
        recipe['view'].update(tracks=True,vias=True,routing_scope='All')
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        self.assertEqual(sum(item.data(1) == 'track' for item in self.window.canvas.scene().items()),7)
        self.window.scope_buttons['Source'].setChecked(True); self.window.redraw()
        self.assertFalse(any(item.data(1) == 'track' for item in self.window.canvas.scene().items()))
        self.window.scope_buttons['All'].setChecked(True); self.window.show_group.setCurrentIndex(1); self.window.redraw()
        self.assertFalse(any(item.data(1) == 'track' for item in self.window.canvas.scene().items()))
        self.assertFalse(self.errors,self.errors)

    def test_preview_shows_preserved_edge_and_hides_it_when_disabled(self):
        board,recipe = linked_grid(); self.source.write_bytes(ET.tostring(board.root)); recipe['pcb'] = str(self.source)
        recipe['view'].update(tracks=True,vias=True)
        path = self.root/'study.json'; write_json(path,recipe); self.window.open_path(path,ask=False)
        self.window.rep_toggle.setChecked(True); self.window.preview.setChecked(True)
        self.window.refresh_display(); self.wait_for_replication()
        self.assertEqual(sum(item.data(1) == 'track' for item in self.window.canvas.scene().items()),13)
        self.assertTrue(self.window.preview_board.nets['2'].findall('./Traces/Trace'))
        self.window.preserve_skipped.setChecked(False)
        self.window.refresh_display(); self.wait_for_replication()
        self.assertEqual(sum(item.data(1) == 'track' for item in self.window.canvas.scene().items()),12)
        self.assertFalse(self.window.preview_board.nets['2'].findall('./Traces/Trace'))
        self.assertFalse(self.errors,self.errors)

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
