#!/usr/bin/env python3
"""Shared BOM output conversions used by the CLI and desktop editor."""

from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
COMPANION_ROOT = Path(__file__).resolve().parent.parent
if str(COMPANION_ROOT) not in sys.path:
    sys.path.insert(0, str(COMPANION_ROOT))
from Shared.normalization import load_rules, normalize_record
from Shared.value_normalization import format_quantity
from Shared.references import REFERENCE_RE, reference_sort_key



TARGET_COLUMNS = (
    "Comment",
    "Designator",
    "Footprint",
    "JLCPCB Part #（optional）",
)
CPL_ADJUSTMENTS_PATH = Path(__file__).with_name("cpl_adjustments.json")

NORMALIZATION_RULES_PATH = Path(__file__).with_name("normalization_rules.json")
_rule_cache = None


def load_normalization_rules(force=False):
    global _rule_cache
    stat = NORMALIZATION_RULES_PATH.stat()
    signature = (str(NORMALIZATION_RULES_PATH), stat.st_mtime_ns, stat.st_size)
    if force or _rule_cache is None or _rule_cache[0] != signature:
        rules, _fingerprint = load_rules(NORMALIZATION_RULES_PATH)
        _rule_cache = signature, rules
    return _rule_cache[1]


def normalized_row(row, rules=None):
    references = REFERENCE_RE.findall(row.get("RefDes", ""))
    ref = references[0] if references else ""
    normalized = normalize_record(row.get("Name", ""), row.get("Value", ""),
                                              row.get("Pattern", ""), ref,
                                              rules if rules is not None else load_normalization_rules())
    return {**row, "Name": normalized["name"], "Value": normalized["value"],
            "Pattern": normalized["pattern"], "_excluded": normalized["excluded"]}


def read_bom(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=";")
        expected = {"RefDes", "Name", "Value", "Pattern"}
        if not expected.issubset(reader.fieldnames or []):
            raise ValueError(
                f"{path} must have semicolon-delimited columns: "
                "RefDes;Name;Value;Pattern"
            )
        columns = ["RefDes", "Name", "Value", "Pattern"]
        if "LCSC Part #" in (reader.fieldnames or []):
            columns.append("LCSC Part #")
        return [
            {key: (row.get(key) or "").strip() for key in columns}
            for row in reader
        ]


def explode_references(rows: list[dict[str, str]], path: Path) -> dict[str, dict[str, str]]:
    by_reference: dict[str, dict[str, str]] = {}
    for row in rows:
        references = REFERENCE_RE.findall(row["RefDes"])
        if not references:
            raise ValueError(f"No designators found in {path}: {row['RefDes']!r}")
        for reference in references:
            reference = reference.upper()
            if reference in by_reference:
                raise ValueError(f"Duplicate designator {reference} in {path}")
            by_reference[reference] = row
    return by_reference


def natural_reference_key(reference: str):
    return reference_sort_key(reference)


def write_changes(original_path: Path, current_path: Path, output_path: Path) -> int:
    original = explode_references(read_bom(original_path), original_path)
    current = explode_references(read_bom(current_path), current_path)
    refs = sorted(original.keys() | current.keys(), key=natural_reference_key)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("RefDes", "Name_was", "Name_new", "Value_was", "Value_new"))
        count = 0
        for reference in refs:
            old = original.get(reference, {})
            new = current.get(reference, {})
            old_name, new_name = old.get("Name", ""), new.get("Name", "")
            old_value, new_value = old.get("Value", ""), new.get("Value", "")
            if (old_name, old_value) != (new_name, new_value):
                writer.writerow((reference, old_name, new_name, old_value, new_value))
                count += 1
    return count


def target_footprint(pattern: str) -> str:
    return normalized_row({"Name": "", "Value": "", "Pattern": pattern})["Pattern"]


def pattern_has_normalization_rule(pattern: str) -> bool:
    return target_footprint(pattern) != pattern


def _comment(row):
    comment = " ".join(value for value in (row["Name"], row["Value"]) if value)
    return re.sub(r"\s+", "-", comment.strip())


def target_comment(row: dict[str, str]) -> str:
    return _comment(normalized_row(row))


def output_fields(row):
    normalized = normalized_row(row)
    return {"Comment": _comment(normalized), "Footprint": normalized["Pattern"]}


def normalize_discrete_value(value: str, prefix: str) -> str:
    """Compatibility API for the established assembly value formatter."""
    if prefix not in {"R", "C"} or not value:
        return value
    return format_quantity(value, "?" if prefix == "R" else "F", recognized_only=False)


def target_designators(raw: str) -> str:
    """Normalize grouped references, including period-separated source typos."""
    return ", ".join(reference.upper() for reference in REFERENCE_RE.findall(raw))


def assembly_bom_rows(current_rows):
    """Apply configured normalization and preserve the established export order."""
    rules = load_normalization_rules(force=True)
    prepared = [(row, normalized_row(row, rules)) for row in current_rows]
    prepared = [(original, normalized) for original, normalized in prepared
                if not normalized["_excluded"]]
    prepared.sort(key=lambda pair: (_comment(pair[1]).casefold(),
                                   pair[0]["Pattern"].casefold(), pair[0]["RefDes"].casefold()))
    return [{"Comment": _comment(normalized),
             "Designator": target_designators(original["RefDes"]),
             "Footprint": normalized["Pattern"],
             TARGET_COLUMNS[3]: original.get("LCSC Part #", "")}
            for original, normalized in prepared]


def write_suffix(current_path: Path, output_path: Path) -> int:
    """Legacy CLI CSV export, using the same conversion as direct XLS output."""
    rows = assembly_bom_rows(read_bom(current_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=TARGET_COLUMNS, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def read_csv_grid(csv_path: Path) -> list[list[str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        sample = stream.read(8192)
        stream.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return list(csv.reader(stream, dialect))


def write_xls_grid(rows, output_path: Path, header_rename=None):
    try:
        import xlwt
    except ImportError as error:
        raise RuntimeError(
            "Writing the companion .xls requires xlwt (install with: python -m pip install xlwt)"
        ) from error

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = xlwt.Workbook(encoding="utf-8")
    sheet = workbook.add_sheet("BOM")
    for row_index, row in enumerate(rows):
        if row_index == 0 and header_rename:
            row = [header_rename.get(value, value) for value in row]
        for column_index, value in enumerate(row):
            sheet.write(row_index, column_index, value)
    workbook.save(str(output_path))


def convert_csv_to_xls(
    csv_path: Path,
    output_path: Path,
    *,
    has_header: bool = True,
    header_rename: dict[str, str] | None = None,
) -> int:
    """Convert a CSV to a real legacy Excel BIFF .xls workbook."""
    rows = read_csv_grid(csv_path)
    write_xls_grid(rows, output_path, header_rename)
    return max(0, len(rows) - int(has_header))


def write_bom_xls(bom_rows, output_path: Path) -> int:
    """Write the assembly BOM directly as a real BIFF XLS workbook."""
    grid = [list(TARGET_COLUMNS)] + [[row.get(field, "") for field in TARGET_COLUMNS] for row in bom_rows]
    write_xls_grid(grid, output_path)
    return len(bom_rows)


@dataclass
class CPLResult:
    rows: int
    removed: list[str]
    missing: list[str]
    adjusted: list[str]


def cpl_number(value, description):
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{description} must be numeric: {value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"{description} must be finite: {value!r}")
    return number


def load_cpl_adjustments(path: Path):
    document = json.loads(path.read_text(encoding="utf-8-sig"), parse_float=Decimal)
    if (not isinstance(document, dict) or document.get("schema_version") != 1
            or not isinstance(document.get("footprints"), dict)):
        raise ValueError(f"Invalid CPL adjustment database: {path}")
    rules = {}
    fields = {"rotation_deg", "x_mm", "y_mm"}
    for footprint, offsets in document["footprints"].items():
        if not isinstance(offsets, dict) or set(offsets) - fields:
            raise ValueError(f"Invalid CPL rule for {footprint!r}; use rotation_deg, x_mm, y_mm.")
        key = footprint.strip().casefold()
        if not key or key in rules:
            raise ValueError(f"Empty or duplicate CPL footprint rule: {footprint!r}")
        rules[key] = {field: cpl_number(offsets.get(field, 0), f"{footprint} {field}")
                      for field in fields}
    return rules


CPL_UNIT_FACTORS = {"mm": Decimal(1), "millimeter": Decimal(1), "millimeters": Decimal(1),
                    "millimetre": Decimal(1), "millimetres": Decimal(1),
                    "in": Decimal("25.4"), "inch": Decimal("25.4"), "inches": Decimal("25.4"),
                    "mil": Decimal("0.0254"), "mils": Decimal("0.0254")}


def cpl_coordinate_spec(headers, axis):
    matches = []
    for index, header in enumerate(headers):
        label = header.strip().casefold()
        match = re.fullmatch(r"(?:origin\s+|mid\s+)?([xy])\s*\(\s*([a-z]+)\s*\)", label)
        if match and match[1] == axis and match[2] in CPL_UNIT_FACTORS:
            matches.append((index, match[2], CPL_UNIT_FACTORS[match[2]]))
    if len(matches) != 1:
        raise ValueError(f"CPL needs one {axis.upper()} coordinate column with units mm, in/inch/inches or mil/mils, such as Origin {axis.upper()} (mm).")
    return matches[0]


def cpl_coordinate_column(headers, axis):
    return cpl_coordinate_spec(headers, axis)[0]


class MissingCPLFile(ValueError):
    """The expected placement export has not been supplied yet."""


def validate_cpl_input(csv_path: Path):
    if not csv_path.is_file():
        raise MissingCPLFile(f"Expected CPL input file is missing: {csv_path}.\nSet cpl_file in the project JSON to override the filename.")
    rows = read_csv_grid(csv_path)
    if not rows:
        raise ValueError(f"Expected CPL input file is empty: {csv_path}")
    try:
        specs = {axis: cpl_coordinate_spec(rows[0], axis) for axis in ("x", "y")}
    except ValueError as exc:
        raise ValueError(f"Invalid units in expected CPL input file: {csv_path}\n{exc}") from exc
    labels = {header.strip().casefold() for header in rows[0]}
    for alternatives, description in (({"refdes", "designator"}, "RefDes or Designator"),
                                       ({"side", "layer"}, "Side or Layer"), ({"rotation"}, "Rotation")):
        if not labels.intersection(alternatives):
            raise ValueError(f"CPL input needs a {description} column: {csv_path}")
    # Convert in memory. Never rewrite the source export; offsets are in mm.
    for axis, (column, unit, factor) in specs.items():
        original_header = rows[0][column]
        if factor != 1:
            rows[0][column] = re.sub(r"\([^)]*\)", "(mm)", original_header)
        for row_number, row in enumerate(rows[1:], 2):
            if not any(value.strip() for value in row):
                continue
            if len(row) <= column:
                raise ValueError(f"CPL {axis.upper()} coordinate missing on row {row_number}")
            coordinate = cpl_number(row[column], f"CPL row {row_number} {axis.upper()}")
            if factor != 1:
                row[column] = format(coordinate * factor, "f")
    return rows


def apply_cpl_adjustment(row, headers, offsets, ref, decimal_places=3):
    row = row.copy()
    rotation_column = next((index for index, header in enumerate(headers)
                            if header.strip().casefold() == "rotation"), None)
    if rotation_column is None or len(row) <= rotation_column:
        raise ValueError(f"CPL Rotation is missing for {ref}")
    rotation = cpl_number(row[rotation_column], f"{ref} rotation") + offsets["rotation_deg"]
    row[rotation_column] = format((rotation % 360 + 360) % 360, "f")
    for axis in ("x", "y"):
        column = cpl_coordinate_column(headers, axis)
        if len(row) <= column:
            raise ValueError(f"CPL {axis.upper()} is missing for {ref}")
        coordinate = cpl_number(row[column], f"{ref} {axis.upper()}") + offsets[f"{axis}_mm"]
        rounded = coordinate.quantize(Decimal(1).scaleb(-decimal_places), rounding=ROUND_HALF_UP)
        row[column] = format(abs(rounded) if rounded == 0 else rounded, "f")
    return row


def convert_cpl_to_xls(csv_path: Path, bom_path: Path, output_path: Path,
                       *, adjustments_path: Path | None = None) -> CPLResult:
    """Legacy CSV entry point for the shared assembly placement conversion."""
    bom = read_csv_grid(bom_path)
    if not bom or "Designator" not in bom[0]:
        raise ValueError(f"No Designator column in {bom_path}")
    if "Footprint" not in bom[0]:
        raise ValueError(f"No Footprint column in {bom_path}")
    bom_rows = [dict(zip(bom[0], row)) for row in bom[1:]]
    return convert_cpl_for_bom_rows(csv_path, bom_rows, output_path, adjustments_path=adjustments_path)


def convert_cpl_for_bom_rows(csv_path: Path, bom_rows, output_path: Path,
                            *, adjustments_path: Path | None = None, decimal_places=3) -> CPLResult:
    """Filter and correct placements using the final in-memory assembly BOM."""
    if type(decimal_places) is not int or not 0 <= decimal_places <= 9:
        raise ValueError("CPL decimal places must be an integer from 0 to 9.")
    footprints = {}
    for row in bom_rows:
        for ref in REFERENCE_RE.findall(row["Designator"]):
            footprints[ref.upper()] = row["Footprint"]
    allowed = set(footprints)
    adjustments = load_cpl_adjustments(adjustments_path or CPL_ADJUSTMENTS_PATH)
    cpl = validate_cpl_input(csv_path)
    ref_column = next((index for index, header in enumerate(cpl[0])
                       if header.strip().casefold() in {"refdes", "designator"}), None)
    if ref_column is None:
        raise ValueError(f"No RefDes or Designator column in {csv_path}")
    rows = [cpl[0]]
    present, removed, adjusted = set(), set(), set()
    for row in cpl[1:]:
        if not any(value.strip() for value in row):
            continue
        ref = row[ref_column].strip().upper() if len(row) > ref_column else ""
        if ref not in allowed:
            if ref:
                removed.add(ref)
            continue
        if ref in present:
            raise ValueError(f"Duplicate CPL placement for {ref} in {csv_path}")
        present.add(ref)
        offsets = adjustments.get(footprints[ref].strip().casefold(),
                                  {"rotation_deg": Decimal(0), "x_mm": Decimal(0), "y_mm": Decimal(0)})
        row = apply_cpl_adjustment(row, cpl[0], offsets, ref, decimal_places)
        if any(offsets.values()):
            adjusted.add(ref)
        rows.append(row)
    renamed = {header: "Designator" for header in cpl[0]
               if header.strip().casefold() in {"refdes", "designator"}}
    renamed[cpl[0][cpl_coordinate_column(cpl[0], "x")]] = "Mid X"
    renamed[cpl[0][cpl_coordinate_column(cpl[0], "y")]] = "Mid Y"
    renamed.update({header: "Layer" for header in cpl[0] if header.strip().casefold() in {"side", "layer"}})
    write_xls_grid(rows, output_path, renamed)
    return CPLResult(len(rows) - 1, sorted(removed),
                     sorted(allowed - present, key=natural_reference_key),
                     sorted(adjusted, key=natural_reference_key))
