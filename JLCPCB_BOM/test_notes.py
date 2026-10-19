import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bom_editor_core import Project, expand_rows, grouped_rows, read_generated


class NoteTests(unittest.TestCase):
    def test_notes_survive_grouping_without_merging_different_notes(self):
        rows = [dict(RefDes='J6-1, J6-2', Name='Plug', Value='', Pattern='Plug',
                     Notes='Stake length noncritical', **{'LCSC Part #': 'C1'})]
        records = expand_rows(rows)
        self.assertEqual(expand_rows(grouped_rows(records)), records)
        records['J6-2']['Notes'] = 'Another note'
        self.assertEqual(len(grouped_rows(records)), 2)

    def test_save_reload_and_generation_preserve_notes_only_in_json(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'Board.dchxml'
            source.write_text('<Source/>')
            schematic = {ref: {'Name': '', 'Value': '10k', 'Pattern': 'RES_0402_N',
                                'LCSC Part #': ''} for ref in ('R1', 'R2')}
            with patch('bom_editor_core.read_schematic', return_value=schematic):
                project = Project(source)
                self.assertEqual(project.records['R1']['Notes'], '')
                project.records['R1']['Notes'] = 'Use the precision variant'
                project.records['R2']['Notes'] = 'Another note'
                project.document['output_folder'] = 'order'
                self.assertEqual(project.ai_check_path, Path(temp) / 'order' / 'Board_AI_Check.txt')
                project.document['ai_check_file'] = 'Review.txt'
                self.assertEqual(project.ai_check_path, Path(temp) / 'order' / 'Review.txt')
                self.assertTrue(project.dirty)
                project.save()
                reloaded = Project(source)
                self.assertEqual(reloaded.records['R1']['Notes'], 'Use the precision variant')
                self.assertEqual(reloaded.records['R2']['Notes'], 'Another note')
                reloaded.cpl_path.write_text('RefDes;X (mm);Y (mm);Side;Rotation\nR1;1;2;Top;0\nR2;3;4;Top;0\n')
                bom_count, cpl_count = reloaded.generate()
            self.assertEqual((bom_count, cpl_count), (1, 2))
            output = read_generated(reloaded.bom_xls)
            self.assertEqual(set(output), {'R1', 'R2'})
            self.assertFalse(any('note' in key.casefold() for row in output.values() for key in row))
            saved = json.loads(reloaded.json_path.read_bytes())
            self.assertEqual({row['Notes'] for row in saved['rows']},
                             {'Use the precision variant', 'Another note'})
            self.assertEqual(source.read_text(), '<Source/>')


if __name__ == '__main__':
    unittest.main()
