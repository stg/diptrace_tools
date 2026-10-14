"""Behavior tests: python -m unittest discover -s Part_DB -p test_*.py"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db_core as core


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rules = self.root / "rules.json"
        self.rules.write_text(json.dumps({"schema_version": 1, "rules": []}))
        self.board = self.root / "project" / "Board.dch"
        self.board.parent.mkdir()
        self.board.write_bytes(b"test")
        (self.root / "ignored.dchxml").write_text("not binary")
        self.rows = [{"name": "IC", "value": "123", "pattern": "SOIC", "refdes": ["U1", "U2"]}]

    def refresh(self, previous=None, **kwargs):
        return core.refresh_database(self.root, previous, rules_path=self.rules, **kwargs)

    def test_incremental_changed_deleted_and_rules(self):
        with patch.object(core, "extract_parts", return_value=self.rows) as extract:
            first = self.refresh()
            self.assertEqual(extract.call_count, 1)
            self.assertEqual(list(first["files"]), ["project/Board.dch"])
            second = self.refresh(first)
            self.assertEqual(extract.call_count, 1)
            self.assertEqual(second["last_scan"]["reused"], 1)
            self.board.write_bytes(b"changed-size")
            third = self.refresh(second)
            self.assertEqual(extract.call_count, 2)
            self.rules.write_text(json.dumps({"schema_version": 1, "rules": [
                {"operation": "clear_name_when_pattern_starts_with_name", "enabled": False}]}))
            fourth = self.refresh(third)
            self.assertEqual(extract.call_count, 3)
            self.refresh(fourth, force=True)
            self.assertEqual(extract.call_count, 4)
            self.board.unlink()
            self.assertEqual(self.refresh(fourth)["files"], {})

    def test_failure_retried_and_cancellation(self):
        with patch.object(core, "extract_parts", side_effect=ValueError("bad schematic")) as extract:
            first = self.refresh()
            self.assertEqual(first["files"], {})
            self.assertEqual(first["failures"][0]["filename"], "project/Board.dch")
            self.refresh(first)
            self.assertEqual(extract.call_count, 2)
        with self.assertRaises(core.Cancelled):
            self.refresh(cancelled=lambda: True)

    def test_name_rule_semantics(self):
        rules = [{"operation": "clear_name_when_pattern_starts_with_name"}]
        self.assertEqual(core.normalize_name("RES", "RES_0603", rules), "")
        self.assertEqual(core.normalize_name("RES", "RES", rules), "")
        self.assertEqual(core.normalize_name("RES", "RES0603", rules), "RES")
        self.assertEqual(core.normalize_name("RES", "res_0603", rules), "RES")
        specific = [{**rules[0], "names": ["CAP"]}]
        self.assertEqual(core.normalize_name("RES", "RES_0603", specific), "RES")
        insensitive = [{**rules[0], "case_sensitive": False, "require_token_boundary": False}]
        self.assertEqual(core.normalize_name("RES", "res0603", insensitive), "")

    def test_generic_names_do_not_depend_on_footprint(self):
        rules, _ = core.load_rules()
        for name in ("RES", "CAP", "IND", "res", "Cap", "Ind"):
            self.assertEqual(core.normalize_component(name, "", "UnrelatedFootprint", "R1", rules)[0], "")
        for name in ("RESISTOR_ARRAY", "TPS5430", "INDUCTOR_ARRAY"):
            self.assertEqual(core.normalize_component(name, "", name + "_FOOTPRINT", "U1", rules)[0], name)

    def test_discrete_value_normalization_preserves_notes_and_unknown_text(self):
        rules, _ = core.load_rules()
        cases = [("4k7", "R1", "4.7kΩ"), ("4R7", "R2", "4.7Ω"),
                 ("10k 1% DNP", "R3", "10kΩ 1% DNP"),
                 ("2u2 16V X5R", "C1", "2.2µF 16V X5R"),
                 ("100nF", "C2", "100nF"), ("1μF", "C3", "1µF"),
                 ("1m", "R4", "1mΩ"), ("1M", "R5", "1MΩ"),
                 ("1e3", "R6", "1e3Ω"), ("1f", "C4", "1fF"),
                 ("0R", "L5", "0Ω"), ("1k5R", "X1", "1.5kΩ"),
                 ("2u2 1A", "L1", "2.2µH 1A"), ("10uH", "L2", "10µH"),
                 ("4R7", "L3", "4.7Ω"), ("Ferrite", "L4", "Ferrite"),
                 ("DNP", "R7", "DNP"), ("Ferrite 100Ω", "R8", "Ferrite 100Ω"),
                 ("10V", "C5", "10V"), ("2u2", "U1", "2u2"),
                 ("10kΩ 1%", "R9", "10kΩ 1%")]
        for value, ref, expected in cases:
            with self.subTest(value=value, ref=ref):
                self.assertEqual(core.normalize_component("", value, "", ref, rules)[1], expected)
        self.assertEqual(core.normalize_value("2u2", "L1", {"designator_units": {"L": "H"}}), "2.2µH")

    def test_normalization_groups_equivalent_parts_without_losing_refdes(self):
        def part(ref, name, value):
            return {"type": "Part", "props": {"RefDes": ref, "Name": name, "Value": value},
                    "children": [{"type": "Pattern", "props": {"Name": "0603"}}]}
        tree = {"type": "Root", "children": [{"type": "Components", "children": [
            part("R1", "RES", "4k7"), part("R2", "", "4.7kΩ")]}]}
        rules, _ = core.load_rules()
        with patch.object(core, "decode_structural_components", return_value=tree):
            rows = core.extract_parts(self.board, rules)
        self.assertEqual(rows, [{"name": "", "value": "4.7kΩ", "pattern": "0603", "refdes": ["R1", "R2"]}])

    def test_search_never_uses_refdes_and_restricts_filenames(self):
        group = {"name": "IC", "value": "123", "pattern": "SOIC", "uses": [
            {"filename": "project/Board.dch", "refdes": ["U1"]},
            {"filename": "other/Else.dch", "refdes": ["U2"]}]}
        find = lambda query, fields: list(core.search_results([group], query, fields))
        self.assertEqual(find("U1", core.SEARCH_FIELDS), [])
        self.assertEqual(find("Board", ["name", "value", "pattern"]), [])
        match = find("ic BOARD", ["name", "filename"])
        self.assertEqual([use["filename"] for use in match[0][1]], ["project/Board.dch"])
        self.assertEqual(find("Board Else", ["filename"]), [])
        self.assertEqual(find("ic", []), [])
        self.assertEqual(len(find("", [])), 1)
        self.assertEqual(len(find("pro*/Bo?rd.dch", ["filename"])), 1)
        self.assertEqual(len(find("IC # SO?C", ["name", "value", "pattern"])), 1)


if __name__ == "__main__":
    unittest.main()
