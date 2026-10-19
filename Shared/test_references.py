import unittest

from Shared.references import REFERENCE_RE, reference_sort_key, split_reference


class ReferenceTests(unittest.TestCase):
    def test_counter_is_only_the_final_numeric_run(self):
        for ref, expected in [('J6', ('J', 6)), ('J6-1', ('J6-', 1)),
                              ('J6-123', ('J6-', 123)), ('BAT22', ('BAT', 22)),
                              ('J6-1-002', ('J6-1-', 2)), ('A1B02', ('A1B', 2))]:
            with self.subTest(ref=ref):
                self.assertEqual(split_reference(ref), expected)
        with self.assertRaises(ValueError):
            split_reference('J6-')

    def test_grouped_references_preserve_prefixes_and_separators(self):
        self.assertEqual(REFERENCE_RE.findall('J6, J6-1; J6-12\nA1B02. R208. R8'),
                         ['J6', 'J6-1', 'J6-12', 'A1B02', 'R208', 'R8'])
        self.assertEqual(REFERENCE_RE.findall('J6- J6-1X'), [])

    def test_counters_sort_numerically(self):
        self.assertEqual(sorted(['J6-10', 'J6-2', 'J6-1'], key=reference_sort_key),
                         ['J6-1', 'J6-2', 'J6-10'])


if __name__ == '__main__':
    unittest.main()
