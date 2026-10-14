import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bom_conversions import validate_cpl_input, apply_cpl_adjustment, convert_cpl_for_bom_rows


class PlacementTests(unittest.TestCase):
    def test_units_offsets_and_source_preservation(self):
        for unit, factor in [('mm', '1'), ('in', '25.4'), ('inch', '25.4'), ('inches', '25.4'), ('mil', '.0254'), ('mils', '.0254')]:
            with self.subTest(unit=unit), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'board.csv'
                source = f'RefDes;Origin X ({unit});Origin Y ({unit});Side;Rotation\nR1;1.23456;-0.8;Top;90\n'
                path.write_text(source, encoding='utf-8')
                rows = validate_cpl_input(path)
                offsets = {'x_mm': Decimal('.1'), 'y_mm': Decimal('-.2'), 'rotation_deg': Decimal('270')}
                output = apply_cpl_adjustment(rows[1], rows[0], offsets, 'R1')
                self.assertEqual(Decimal(output[1]), (Decimal('1.23456') * Decimal(factor) + Decimal('.1')).quantize(Decimal('.001')))
                self.assertEqual(Decimal(output[2]), (Decimal('-.8') * Decimal(factor) - Decimal('.2')).quantize(Decimal('.001')))
                self.assertEqual(output[4], '0')
                self.assertEqual(path.read_text(), source)

    def test_excluded_parts_removed_from_placements(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'board.csv'
            path.write_text('Designator;X (mm);Y (mm);Layer;Rotation\nR1;1;2;Top;0\nR2;3;4;Top;0\n')
            with patch('bom_conversions.write_xls_grid') as write:
                result = convert_cpl_for_bom_rows(path, [{'Designator': 'R1', 'Footprint': '0402'}], Path(temp) / 'output.xls')
            self.assertEqual(result.removed, ['R2'])
            self.assertEqual(write.call_args.args[0][1][1:3], ['1.000', '2.000'])

    def test_missing_units_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'board.csv'
            path.write_text('RefDes;Origin X;Origin Y;Side;Rotation\nR1;1;2;Top;0\n')
            with self.assertRaises(ValueError):
                validate_cpl_input(path)


if __name__ == '__main__':
    unittest.main()
