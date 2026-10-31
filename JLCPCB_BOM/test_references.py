import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bom_editor_core import expand_rows, grouped_rows, read_assignments, read_generated, read_schematic
from bom_conversions import (assembly_bom_rows, convert_cpl_for_bom_rows,
                             explode_references, target_designators)


class ReferenceWorkflowTests(unittest.TestCase):
    def rows(self):
        return [dict(RefDes='J6', Name='Header', Value='', Pattern='Header', **{'LCSC Part #': 'C1'}),
                dict(RefDes='J6-1, J6-2, J6-10', Name='Plug', Value='', Pattern='Plug', **{'LCSC Part #': 'C2'})]

    def test_json_round_trip_keeps_parent_and_children_distinct(self):
        records = expand_rows(self.rows())
        self.assertEqual(set(records), {'J6', 'J6-1', 'J6-2', 'J6-10'})
        self.assertEqual(records['J6']['LCSC Part #'], 'C1')
        self.assertEqual(records['J6-1']['LCSC Part #'], 'C2')
        self.assertEqual(grouped_rows(records)[1]['RefDes'], 'J6-1, J6-2, J6-10')
        self.assertEqual(expand_rows(grouped_rows(records)), records)

    def test_named_pcb_jumper_json_round_trip_and_exclusion(self):
        rows = [dict(RefDes='BOOT', Name='', Value='DNM', Pattern='SJ-NO',
                     **{'LCSC Part #': '', 'Notes': 'PCB copper solder jumper'})]
        records = expand_rows(rows)
        self.assertEqual(set(records), {'BOOT'})
        self.assertEqual(expand_rows(grouped_rows(records)), records)
        self.assertEqual(assembly_bom_rows(grouped_rows(records)), [])

    def test_separate_style_multipart_export_uses_main_unit(self):
        text = '''<Source Type="DipTrace-Schematic"><Library><Library><Patterns>
        <Pattern PatternStyle="P"><Name>SOIC8</Name></Pattern>
        </Patterns></Library><Components>
        <Component ComponentStyle="A"><Part><Pattern Style="P"/></Part></Component>
        <Component ComponentStyle="B"><Part><Pattern Style="P"/></Part></Component>
        </Components></Library><Schematic><Components>
        <Part ComponentStyle="B" ComponentPart="0" PartNumber="1" AllowParts="Y" UpdateId="9">
        <RefDes>U1</RefDes><Name>Power</Name><Value>TL072</Value></Part>
        <Part ComponentStyle="A" ComponentPart="0" PartNumber="0" AllowParts="Y" UpdateId="9">
        <RefDes>U1</RefDes><Name>OPA2171</Name><Value/></Part>
        </Components></Schematic></Source>'''
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'board.dchxml'; path.write_text(text)
            records=read_schematic(path)
        self.assertEqual(set(records), {'U1'})
        self.assertEqual(records['U1']['Name'], 'OPA2171')
        self.assertEqual(records['U1']['Value'], '')
        self.assertEqual(records['U1']['Pattern'], 'SOIC8')

    def test_bom_conversion_keeps_full_designators(self):
        self.assertEqual(target_designators('j6. j6-1, J6-10'), 'J6, J6-1, J6-10')
        self.assertEqual(set(explode_references(self.rows(), Path('example.csv'))),
                         {'J6', 'J6-1', 'J6-2', 'J6-10'})
        rows = {r['Comment']: r for r in assembly_bom_rows(self.rows())}
        self.assertEqual(rows['Plug']['Designator'], 'J6-1, J6-2, J6-10')

    def test_supplier_assignment_and_generated_bom_import(self):
        grid = [['Designator', 'LCSC Part #'], ['J6', 'C1'], ['J6-1, J6-2', 'C2']]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bom.xls'; path.touch()
            with patch('bom_editor_core.workbook_grid', return_value=grid):
                self.assertEqual(read_assignments(path), {'J6': 'C1', 'J6-1': 'C2', 'J6-2': 'C2'})
                generated = read_generated(path)
        self.assertEqual(set(generated), {'J6', 'J6-1', 'J6-2'})
        self.assertEqual(generated['J6-1']['LCSC Part #'], 'C2')

    def test_cpl_selection_does_not_substitute_parent_for_child(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'board.csv'
            path.write_text('Designator;X (mm);Y (mm);Layer;Rotation\nJ6;1;2;Top;0\nJ6-1;3;4;Top;0\nJ6-2;5;6;Top;0\n')
            with patch('bom_conversions.write_xls_grid') as write:
                result = convert_cpl_for_bom_rows(path,
                    [{'Designator': 'J6-1', 'Footprint': 'Plug'}], Path(temp) / 'out.xls')
        self.assertEqual(result.removed, ['J6', 'J6-2'])
        self.assertEqual(result.missing, [])
        self.assertEqual(write.call_args.args[0][1][0], 'J6-1')


if __name__ == '__main__':
    unittest.main()
