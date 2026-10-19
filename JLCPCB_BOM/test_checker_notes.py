import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jlcpcb_bom_checker as checker
from bom_editor_core import expand_rows


class CheckNoteTests(unittest.TestCase):
    def records(self):
        return expand_rows([
            {'RefDes': 'J3', 'Name': 'USB4105-GF-A', 'LCSC Part #': 'C1',
             'Notes': 'Stake length noncritical'},
            {'RefDes': 'J4', 'Name': 'Other connector', 'LCSC Part #': 'C1'},
        ])

    def test_notes_belong_to_designators_not_just_shared_supplier_number(self):
        records = self.records()
        self.assertEqual(checker.notes_for_row(records, 'J3', 'C1'), 'Stake length noncritical')
        self.assertEqual(checker.notes_for_row(records, 'J4', 'C1'), '')
        self.assertEqual(checker.notes_for_row(records, 'J3, J4', 'C1'), 'J3: Stake length noncritical')
        self.assertEqual(checker.notes_for_row(records, '', 'C1'), 'J3: Stake length noncritical')

    def test_comment_separator_is_optional(self):
        self.assertEqual(checker.verification_line('original', 'model', 'name'),
                         'original\tmodel: name\n')
        self.assertEqual(checker.verification_line('original', 'model', 'name', 'accepted'),
                         'original\tmodel: name // accepted\n')

    def test_selected_alternate_notes_are_included_without_duplication(self):
        records = self.records()
        records['J3']['Alternates'] = [{'LCSC Part #': 'C2', 'Notes': 'Different finish acceptable'}]
        records['J3']['Output selection'] = 1
        self.assertEqual(checker.notes_for_row(records, 'J3', 'C2'),
                         'Stake length noncritical; Different finish acceptable')

    def test_both_workbook_formats_retain_designators_for_note_matching(self):
        from openpyxl import Workbook
        with tempfile.TemporaryDirectory() as temp:
            for matching in (False, True):
                with self.subTest(matching=matching):
                    workbook = Workbook(); sheet = workbook.active
                    if matching:
                        sheet.append(['BOM matching results'])
                        sheet.append(['Comment', 'Designator', 'Matches', 'Product Link',
                                      'JLCPCB Part # (optional)', 'JLCPCB Part #'])
                        sheet.append(['Plug', 'J6-1', '', '', 'C2', 'C1'])
                    else:
                        sheet.append(['Comment', 'Designator', 'Footprint', 'JLCPCB Part #'])
                        sheet.append(['Plug', 'J6-1', 'Plug', 'C1'])
                    path = Path(temp) / f'{matching}.xls'
                    workbook.save(path); workbook.close()
                    self.assertEqual(checker._read_excel_rows(path), [('Plug', 'C1')])
                    self.assertEqual(checker._read_excel_rows(path, with_designators=True),
                                     [('Plug', 'C1', 'J6-1')])

    def test_checker_report_includes_notes_without_changing_the_workbook(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            bom = folder / 'Board.csv'
            csv = 'Comment,Designator,LCSC Part #\nUSB4105-GF-A,J3,C1\nOther connector,J4,C1\n'
            bom.write_text(csv)
            project = folder / 'Board.json'
            project.write_text(json.dumps({'rows': [
                {'RefDes': ref, **record} for ref, record in self.records().items()]}))
            cache = folder / 'cache'; cache.mkdir()
            (cache / 'C1.json').write_text(json.dumps({'detail': {
                'productModel': 'USB4105-GF-A-060', 'productNameEn': 'USB connector'}}))
            with patch.object(checker, 'cache_directory', return_value=cache), \
                 patch.object(checker, 'import_local_cache', return_value=(0, 0)), \
                 patch('sys.argv', ['checker', str(bom), '--scrape-only', '--project-json', str(project)]), \
                 patch('builtins.print'):
                self.assertEqual(checker.main(), 0)
            report = bom.with_suffix('.txt').read_text()
            self.assertIn(' // optional notes', report)
            self.assertIn('USB4105-GF-A\tUSB4105-GF-A-060: USB connector // Stake length noncritical\n', report)
            self.assertIn('Other connector\tUSB4105-GF-A-060: USB connector\n', report)
            self.assertEqual(bom.read_text(), csv)

    def test_internal_check_uses_selected_parts_grouping_rules_notes_and_full_rohs_refs(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            project = folder / 'Board.json'
            rows = [
                {'RefDes': 'R1', 'Value': '10k', 'Pattern': 'RES_0402_N',
                 'LCSC Part #': 'C1', 'Notes': '1% accepted'},
                {'RefDes': 'R2', 'Value': '10k', 'Pattern': 'RES_0402_N', 'LCSC Part #': 'C1'},
                {'RefDes': 'R3', 'Value': 'DNP', 'LCSC Part #': 'C3'},
                {'RefDes': 'U1', 'Name': 'Unassigned device', 'Notes': '? Assign part'},
                {'RefDes': 'J6-1, J6-2', 'Name': 'Primary connector', 'LCSC Part #': 'C3',
                 'Notes': 'Finish accepted', 'Output selection': 1, 'Alternates': [
                     {'Name': 'Selected connector', 'Pattern': 'SOT23', 'LCSC Part #': 'C2',
                      'Notes': 'Alternate accepted'}]},
            ]
            before = json.dumps({'schema_version': 1, 'rows': rows}).encode()
            project.write_bytes(before)
            cache = folder / 'cache'; cache.mkdir()
            for part, model, environment in [('C1', 'Resistor', True), ('C2', 'Selected connector', False)]:
                (cache / f'{part}.json').write_text(json.dumps({'detail': {
                    'productModel': model, 'productNameEn': 'Supplier description', 'isEnvironment': environment}}))
            report = folder / 'order' / 'Board_AI_Check.txt'
            output = io.StringIO()
            with patch.object(checker, 'cache_directory', return_value=cache), \
                    patch.object(checker, 'import_local_cache', return_value=(0, 0)), \
                    patch.object(checker, '_read_excel_rows', side_effect=AssertionError('No workbook')), \
                    patch.object(checker, '_download_html', side_effect=AssertionError('No network')), \
                    patch('sys.argv', ['checker', '--project-json', str(project), '--output', str(report), '--scrape-only']), \
                    patch('sys.stdout', output):
                self.assertEqual(checker.main(), 0)
            text = report.read_text(encoding='utf-8')
            self.assertIn('R1, R2:', text)
            self.assertIn('[footprint: 0402]', text)
            self.assertIn('R1: 1% accepted', text)
            self.assertIn('Selected connector', text)
            self.assertNotIn('Primary connector', text)
            self.assertIn('Finish accepted; Alternate accepted', text)
            self.assertNotIn('R3:', text)
            self.assertIn('Unassigned-device', text)
            self.assertIn('(no LCSC part number assigned) // ? Assign part', text)
            self.assertIn('CHECK_TOTAL: 2', output.getvalue())
            rohs = json.loads(next(line.removeprefix('CHECK_ROHS: ') for line in output.getvalue().splitlines()
                                   if line.startswith('CHECK_ROHS: ')))
            self.assertEqual(rohs, {'compliant': ['R1', 'R2'], 'non_compliant': ['J6-1', 'J6-2'], 'unknown': ['U1']})
            self.assertIn('Non-RoHS', report.with_suffix('.rohs.txt').read_text())
            self.assertEqual(project.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
