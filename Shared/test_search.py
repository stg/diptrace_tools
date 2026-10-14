import unittest
from Shared.search import compile_query, matches_terms


class WildcardTests(unittest.TestCase):
    def matches(self, query, *values):
        return matches_terms(compile_query(query), values)

    def test_wildcards_and_literal_punctuation(self):
        self.assertTrue(self.matches("Board_R#.dch", "old/Board_R123.dch"))
        self.assertFalse(self.matches("Board_R#.dch", "Board_RA.dch"))
        self.assertTrue(self.matches("Board_R?.dch", "Board_R2.dch"))
        self.assertFalse(self.matches("Board_R?.dch", "Board_R12.dch"))
        self.assertTrue(self.matches("RES*0603", "res_border_0603"))
        self.assertFalse(self.matches("Board.dch", "BoardXdch"))

    def test_escapes_and_all_words_across_fields(self):
        self.assertTrue(self.matches(r"\* \# \?", "*#?"))
        self.assertFalse(self.matches(r"\?", "X"))
        self.assertTrue(self.matches("abc SO?C", "ABC123", "SOIC"))
        self.assertFalse(self.matches("abc SO?C", "ABC123", "QFN"))
        self.assertTrue(self.matches(""))
        self.assertFalse(self.matches("*"))
