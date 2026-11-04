import json
import math
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog
from assembly_alternates import (choose_group, local_layout, apply_groups_to_cpl,
                                 validate_groups, active_group, reanchor_placement)
from bom_editor_core import (Project, expand_rows, assembly_records, read_generated,
                             workbook_grid, project_output_record, publish_files, atomic_write)
from bom_editor import Editor, OUTPUT_COLUMN
from bom_alternates_dialog import AlternatesDialog
from combined_alternate_dialog import CombinedAlternateDialog
from bom_conversions import convert_cpl_for_bom_rows, validate_cpl_input


def records():
    return expand_rows([{"RefDes": "J1, J2, J3", "Name": "Two pin", "Value": "",
        "Pattern": "2P", "LCSC Part #": "C1", "Alternates": [
            {"Name": "Other two pin", "Value": "", "Pattern": "2P", "LCSC Part #": "C2"}],
        "Output selection": 1}])


def grid(rotation=0, side="Top"):
    co,si = math.cos(math.radians(rotation)),math.sin(math.radians(rotation))
    return [["RefDes", "X (mm)", "Y (mm)", "Side", "Rotation"]] + [
        [f"J{i+1}", str(10+i*10.16*co), str(20+i*10.16*si),side,str(rotation)] for i in range(3)]


def group(gid="six", refs=None, cpl=None):
    refs = refs or ["J1", "J2", "J3"]
    return {"id":gid, "references":refs, "anchor":refs[0], "active": True,
            "component":{"Name":"Six pin", "Value":"", "Pattern":"6P", "LCSC Part #":"C3", "Notes":""},
            "placement":{"x_mm":10.16,"y_mm":0,"rotation_deg":0},
            "layout":local_layout(cpl or grid(),refs,refs[0])}


class AssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selected_group_overrides_once_and_restores_each_individual_choice(self):
        r = records();r["J2"]["Output selection"] = 0
        before = deepcopy(r)
        p = SimpleNamespace(records=r,assembly_groups=[group()])
        self.assertEqual(set(assembly_records(p)), {"J1"})
        self.assertEqual(assembly_records(p)["J1"]["LCSC Part #"],"C3")
        self.assertIsNone(project_output_record(p,"J2"))
        p.assembly_groups = choose_group(p.assembly_groups,["J2"])
        self.assertEqual([assembly_records(p)[ref]["LCSC Part #"] for ref in r],["C2","C1","C2"])
        self.assertEqual(r,before)

    def test_overlapping_groups_switch_atomically(self):
        a = group();b = group("four",["J2","J3"]);b["active"] = False
        groups = choose_group([a,b],["J2"],"four")
        validate_groups(groups,records())
        self.assertFalse(groups[0]["active"]);self.assertTrue(groups[1]["active"])
        self.assertTrue(a["active"]);self.assertFalse(b["active"])

    def test_invalid_groups_rejected(self):
        for mutate in [lambda g:g.update(references=["J1","J1"]),
                       lambda g:g.update(anchor="J9"),lambda g:g.update(layout={}),
                       lambda g:g["placement"].update(x_mm=float("nan")),
                       lambda g:g.update(placement=[] )]:
            g=group();mutate(g)
            with self.assertRaises(ValueError):validate_groups([g],records())
        with self.assertRaises(ValueError):validate_groups([group(),group("other")],records())

    def test_reanchoring_preserves_replacement_world_pose(self):
        for angle in (0,90,180,270,37):
            cpl=grid(angle);g=group(cpl=cpl)
            before=apply_groups_to_cpl(cpl,[g])[1]
            g["placement"]=reanchor_placement(cpl,"J1","J3",g["placement"])
            g["anchor"]="J3";g["layout"]=local_layout(cpl,g["references"],"J3")
            after=apply_groups_to_cpl(cpl,[g])[1]
            self.assertAlmostEqual(float(before[1]),float(after[1]),6)
            self.assertAlmostEqual(float(before[2]),float(after[2]),6)
            self.assertAlmostEqual(float(before[4]),float(after[4]),6)

    def test_publish_rolls_back_when_a_workbook_is_locked(self):
        with tempfile.TemporaryDirectory() as folder:
            paths=[Path(folder)/name for name in ("bom.xls","cpl.xls","project.json")]
            for p in paths:p.write_bytes(b"old")
            for locked in paths:
                def write(path,content):
                    if path==locked:raise PermissionError("File is open")
                    atomic_write(path,content)
                with patch("bom_editor_core.atomic_write",side_effect=write):
                    with self.assertRaises(PermissionError):publish_files({p:b"new" for p in paths})
                self.assertEqual([p.read_bytes() for p in paths],[b"old"]*3)

    def test_mil_source_combining_then_replacement_footprint_adjustment(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"cpl.csv"
            path.write_text("RefDes;X (mil);Y (mil);Side;Rotation\nJ1;100;200;Top;90\nJ2;100;600;Top;90\nJ3;100;1000;Top;90\n")
            g=group(cpl=validate_cpl_input(path))
            rules=Path(folder)/"rules.json"
            rules.write_text(json.dumps({"schema_version":1,"footprints":{"6P":{"x_mm":1,"y_mm":2,"rotation_deg":180}}}))
            output=Path(folder)/"out.xls"
            report=convert_cpl_for_bom_rows(path,[{"Designator":"J1","Footprint":"6P"}],output,
                                           adjustments_path=rules,assembly_groups=[g])
            row=workbook_grid(output)[1]
            self.assertAlmostEqual(float(row[1]),3.54)
            self.assertAlmostEqual(float(row[2]),17.24)
            self.assertEqual(float(row[4]),270)
            self.assertEqual(report.merged,["J1,J2,J3 => J1"])

    def test_non_finite_source_placement_rejected(self):
        for axis in (1,2,4):
            cpl=grid();cpl[2][axis]="nan"
            with self.assertRaises(ValueError):local_layout(cpl,["J1","J2","J3"],"J1")

    def test_local_offsets_follow_rotation_and_side(self):
        for side in ("Top","Bottom"):
            for angle in (0,90,180,270,37):
                with self.subTest(side=side,angle=angle):
                    cpl=grid(angle,side);g=group(cpl=cpl);before=deepcopy(cpl)
                    output=apply_groups_to_cpl(cpl,[g])
                    self.assertEqual(len(output),2)
                    self.assertAlmostEqual(float(output[1][1]),float(cpl[2][1]),6)
                    self.assertAlmostEqual(float(output[1][2]),float(cpl[2][2]),6)
                    self.assertEqual(output[1][3],side);self.assertEqual(cpl,before)

    def test_moved_member_missing_duplicate_and_wrong_side_rejected(self):
        g=group()
        changed=grid();changed[2][1]="20.3"
        with self.assertRaisesRegex(ValueError,"moved relative"):apply_groups_to_cpl(changed,[g])
        changed=grid();changed[2][3]="Bottom"
        with self.assertRaisesRegex(ValueError,"same PCB side"):apply_groups_to_cpl(changed,[g])
        with self.assertRaisesRegex(ValueError,"missing CPL"):apply_groups_to_cpl(grid()[:-1],[g])
        with self.assertRaisesRegex(ValueError,"Duplicate CPL"):apply_groups_to_cpl(grid()+[grid()[1]],[g])

    def test_project_save_reload_generate_and_failed_generation_preserves_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/"Board.dchxml";source.write_text("<Source/>")
            with patch("bom_editor_core.read_schematic",return_value={k:{f:v for f,v in r.items() if f in ("Name","Value","Pattern","LCSC Part #")} for k,r in records().items()}):
                p=Project(source);p.records=records();p.assembly_groups=[group()]
                p.cpl_path.write_text("\n".join(";".join(row) for row in grid()))
                original=p.cpl_path.read_bytes()
                self.assertTrue(p.dirty);self.assertEqual(p.generate(),(1,1))
                self.assertEqual(p.cpl_report.merged,["J1,J2,J3 => J1"])
                p=Project(p.json_path);self.assertFalse(p.dirty)
                self.assertEqual(p.document["schema_version"],2)
                self.assertEqual(set(read_generated(p.bom_xls)),{"J1"})
                cpl=workbook_grid(p.cpl_xls)
                self.assertEqual(cpl[1][0],"J1");self.assertAlmostEqual(float(cpl[1][1]),20.16)
                saved=(p.bom_xls.read_bytes(),p.cpl_xls.read_bytes(),p.json_path.read_bytes())
                with patch("bom_conversions.convert_cpl_for_bom_rows",side_effect=ValueError("Invalid rotation")):
                    with self.assertRaises(ValueError):p.generate()
                self.assertEqual(saved,(p.bom_xls.read_bytes(),p.cpl_xls.read_bytes(),p.json_path.read_bytes()))
                p.cpl_path.write_text(p.cpl_path.read_text().replace("20.16;20.0","21.16;20.0"))
                with self.assertRaises(ValueError):p.generate()
                self.assertEqual(saved,(p.bom_xls.read_bytes(),p.cpl_xls.read_bytes(),p.json_path.read_bytes()))
                p.cpl_path.write_bytes(original);p.assembly_groups=choose_group(p.assembly_groups,["J2"])
                self.assertEqual(p.generate(),(1,3))
                self.assertEqual(set(read_generated(p.bom_xls)),{"J1","J2","J3"})

    def make_editor(self,folder):
        settings=QSettings(str(Path(folder)/"settings.ini"),QSettings.IniFormat)
        with patch("bom_editor.QSettings",return_value=settings),patch.object(Editor,"open_startup"):
            e=Editor()
            self.app.processEvents()
        r=records();r["J2"]["Output selection"]=0
        p=SimpleNamespace(records=r,source=deepcopy(r),outputs=[],assembly_groups=[group()],dirty=False,path=Path("Board.dchxml"))
        e.project=p;e.rebuild()
        return e

    def test_ui_all_members_share_choice_cancel_and_undo_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            e=self.make_editor(folder)
            self.assertEqual(len(e.view_rows),3)
            for i in range(3):
                combo=e.table.cellWidget(i,OUTPUT_COLUMN)
                self.assertEqual(combo.currentData(),"six")
                self.assertIn("J1, J2, J3",combo.currentText())
            with patch.object(QMessageBox,"question",return_value=QMessageBox.No):
                e.select_output_component(("J2",),1)
            self.assertTrue(e.project.assembly_groups[0]["active"])
            with patch.object(QMessageBox,"question",return_value=QMessageBox.Yes):
                e.select_output_component(("J2",),1)
            self.assertFalse(e.project.assembly_groups[0]["active"])
            self.assertEqual([r["Output selection"] for r in e.project.records.values()],[1,1,1])
            e.undo_content()
            self.assertTrue(e.project.assembly_groups[0]["active"])
            self.assertEqual(e.project.records["J2"]["Output selection"],0)
            e.redo_content();self.assertFalse(e.project.assembly_groups[0]["active"])
            combo=e.table.cellWidget(1,OUTPUT_COLUMN)
            with patch.object(QMessageBox,"question",return_value=QMessageBox.Yes):
                combo.activated.emit(combo.findData("six"))
            self.assertTrue(e.project.assembly_groups[0]["active"])
            self.assertEqual([e.table.cellWidget(i,OUTPUT_COLUMN).currentData() for i in range(3)],["six"]*3)
            e.project=None;e.close()

    def test_group_edit_from_any_member_and_removal_is_shared_and_undoable(self):
        with tempfile.TemporaryDirectory() as folder:
            e=self.make_editor(folder)
            dialog=AlternatesDialog(["J2"],e.project.records["J2"],project=e.project)
            self.assertEqual(len(dialog.visible_groups),1)
            dialog.group_table.selectRow(0)
            with patch.object(QMessageBox,"question",return_value=QMessageBox.Yes):dialog.remove_group()
            self.assertEqual(dialog.groups,[])
            self.assertEqual(len(e.project.assembly_groups),1)
            e.change_content({(None,"assembly_groups"):dialog.groups},"Remove combined",regroup=True)
            self.assertEqual(set(assembly_records(e.project)),{"J1","J2","J3"})
            e.undo_content();self.assertEqual(set(assembly_records(e.project)),{"J1"})
            dialog.close();e.project=None;e.close()

    def test_removing_a_missing_member_removes_shared_choice_and_can_be_undone(self):
        with tempfile.TemporaryDirectory() as folder:
            e=self.make_editor(folder);e.project.source.pop("J2")
            with patch.object(QMessageBox,"question",return_value=QMessageBox.No):e.remove_missing_refs(["J2"])
            self.assertIn("J2",e.project.records)
            with patch.object(QMessageBox,"question",return_value=QMessageBox.Yes):e.remove_missing_refs(["J2"])
            self.assertNotIn("J2",e.project.records);self.assertEqual(e.project.assembly_groups,[])
            validate_groups(e.project.assembly_groups,e.project.records)
            e.undo_content();self.assertIn("J2",e.project.records)
            self.assertEqual(set(assembly_records(e.project)),{"J1"})
            e.project=None;e.close()

    def test_import_combined_part_edits_group_without_changing_individual_parts(self):
        for action in ("Change combined part number","Add combined alternative","Skip"):
            with self.subTest(action=action),tempfile.TemporaryDirectory() as folder:
                e=self.make_editor(folder);before=deepcopy(e.project.records)
                with patch.object(Editor,"workbook_picker",return_value=("selected.xls","")), \
                        patch("bom_editor.read_assignments",return_value={"J1":"C9"}), \
                        patch.object(QMessageBox,"exec",return_value=0), \
                        patch.object(QMessageBox,"clickedButton",lambda box:next(b for b in box.buttons() if b.text()==action)):
                    e.import_lcsc()
                self.assertEqual(e.project.records,before)
                groups=e.project.assembly_groups
                if action=="Change combined part number":self.assertEqual(groups[0]["component"]["LCSC Part #"],"C9")
                elif action=="Add combined alternative":
                    self.assertEqual(len(groups),2);self.assertFalse(groups[1]["active"])
                    self.assertEqual(groups[1]["component"]["LCSC Part #"],"C9")
                else:self.assertEqual(groups[0]["component"]["LCSC Part #"],"C3")
                validate_groups(groups,e.project.records)
                e.project=None;e.close()

    def test_dialog_reanchor_and_stale_preview_require_review(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"cpl.csv"
            path.write_text("\n".join(";".join(row) for row in grid()))
            p=SimpleNamespace(records=records(),cpl_path=path)
            d=CombinedAlternateDialog(p,["J1"],group())
            d.anchor.setText("J3");d.preview()
            self.assertAlmostEqual(d.offsets["x_mm"].value(),-10.16)
            moved=grid();moved[2][1]="21"
            path.write_text("\n".join(";".join(row) for row in moved))
            with patch.object(QMessageBox,"information") as info:d.accept()
            info.assert_called_once();self.assertNotEqual(d.result(),QDialog.Accepted)
            d.accept();self.assertEqual(d.result(),QDialog.Accepted)
            d.close()


if __name__ == "__main__":unittest.main()
