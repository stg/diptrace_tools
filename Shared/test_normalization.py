"""Rule-language behavior tests; run unittest discover -s Shared."""
import json
import tempfile
import unittest
from pathlib import Path
from Shared.normalization import load_rules, normalize_component, normalize_record, note_highlight


class RuleLanguageTests(unittest.TestCase):
    def load(self, rules):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_text(json.dumps({"schema_version": 1, "rules": rules}), encoding="utf-8")
            return load_rules(path)

    def test_captures_conditions_and_order(self):
        rules, _ = self.load([
            {"operation": "regex_replace", "field": "name", "pattern": "^vendor:(.*)$", "replacement": "\\g<1>", "ignore_case": True},
            {"operation": "regex_replace", "field": "pattern", "pattern": "^PKG_(\\d{4})$", "replacement": "\\g<1>"},
            {"operation": "set", "field": "value", "text": "4k7", "when": {"name": "^RES$", "refdes": "^R"}},
            {"operation": "format_quantity", "field": "value", "unit": "Ω", "when": {"refdes": "^R"}},
            {"operation": "set", "field": "name", "text": "wrong", "enabled": False},
        ])
        self.assertEqual(normalize_component("Vendor:RES", "anything", "PKG_0603", "R1", rules), ("RES", "4.7kΩ", "0603"))
        self.assertEqual(normalize_component("Vendor:RES", "anything", "OTHER", "C1", rules), ("RES", "anything", "OTHER"))

    def test_invalid_patterns_and_captures_fail_before_processing(self):
        for pattern, replacement in [("[", ""), ("(x)", "\\g<missing>")]:
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                self.load([{"operation": "regex_replace", "field": "value", "pattern": pattern, "replacement": replacement}])
        with self.assertRaises(ValueError):
            self.load([{"operation": "set", "field": "refdes", "text": "R2"}])

    def test_description_edits_do_not_invalidate_cache(self):
        rule = {"operation": "set", "field": "name", "text": "", "description": "first"}
        _, first = self.load([rule])
        _, second = self.load([{**rule, "description": "new wording"}])
        self.assertEqual(first, second)

    def test_note_highlights_are_ordered_conditional_and_display_only(self):
        plain, fingerprint = self.load([])
        rules, styled_fingerprint = self.load([
            {"operation": "highlight", "field": "notes", "when": {"notes": "\\?"},
             "color": "yellow", "status": "Review"},
            {"operation": "highlight", "field": "notes", "when": {"notes": "!"},
             "color": "red", "status": "Action"},
            {"operation": "highlight", "field": "notes", "when": {"notes": "!"},
             "color": "yellow", "enabled": False},
        ])
        self.assertEqual(note_highlight('Check?', rules), {'color': 'yellow', 'status': 'Review'})
        self.assertEqual(note_highlight('Check?!', rules), {'color': 'red', 'status': 'Action'})
        self.assertIsNone(note_highlight('Accepted', rules))
        self.assertEqual(normalize_record('RES', '10k', '0603', 'R1', rules),
                         normalize_record('RES', '10k', '0603', 'R1', plain))
        self.assertEqual(fingerprint, styled_fingerprint)
        for change in ({'field': 'value'}, {'color': 'blue'}, {'status': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load([{'operation': 'highlight', 'field': 'notes', 'color': 'red', **change}])

    def test_exclusion_remains_set_after_later_rewrites(self):
        rules, _ = self.load([
            {"operation": "exclude", "when": {"value": "^DNP$"}},
            {"operation": "set", "field": "value", "text": "10k"},
        ])
        excluded = normalize_record("RES", "DNP", "0603", "R1", rules)
        included = normalize_record("RES", "4k7", "0603", "R2", rules)
        self.assertTrue(excluded["excluded"])
        self.assertEqual(excluded["value"], "10k")
        self.assertFalse(included["excluded"])
