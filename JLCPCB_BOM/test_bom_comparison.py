import unittest

from bom_editor_core import value_has_added_suffix


class ValueComparisonTests(unittest.TestCase):
    def test_adding_a_value_to_blank_is_a_warning(self):
        for original in ('', ' \t '):
            for edited in ('DNP', 'DNM', '100nF', 'Custom assembly note'):
                with self.subTest(original=original, edited=edited):
                    self.assertTrue(value_has_added_suffix(original, edited))

    def test_retained_value_with_added_words_is_a_warning(self):
        self.assertTrue(value_has_added_suffix('100n', '100n 50V X7R'))
        self.assertTrue(value_has_added_suffix('10u 10V', '10U 10v X7R'))

    def test_replacement_and_removal_remain_errors(self):
        for original, edited in [('100n', 'DNP'), ('100n', ''), ('100n', '100nF'),
                                 ('10u 10V', '10u 50V X7R')]:
            with self.subTest(original=original, edited=edited):
                self.assertFalse(value_has_added_suffix(original, edited))
        self.assertFalse(value_has_added_suffix('', ''))


if __name__ == '__main__':
    unittest.main()
