#!/usr/bin/env python3
"""Legacy CSV BOM generator using the shared code beside this script."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))
from bom_conversions import (
    write_changes, write_suffix, convert_csv_to_xls, convert_cpl_to_xls,
    validate_cpl_input, load_cpl_adjustments, CPL_ADJUSTMENTS_PATH,
)

CPL_FILENAME = "Axon_PSUAC_R001_CPL.csv"


def write_cpl_xls(origin_dir: Path, order_dir: Path, bom_path: Path):
    source = origin_dir / CPL_FILENAME
    output_path = order_dir / f"{source.stem}.xls"
    result = convert_cpl_to_xls(source, bom_path, output_path)
    if result.adjusted:
        print("CPL footprint adjustments applied: " + ", ".join(result.adjusted))
    if result.removed:
        print("CPL excluded (not in output BOM): " + ", ".join(result.removed))
    if result.missing:
        print("WARNING: BOM designators missing from CPL: " + ", ".join(result.missing))
    else:
        print("CPL and BOM designator sets match.")
    return output_path, result.rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, help="Project folder; defaults to the current working directory")
    parser.add_argument("--original", type=Path)
    parser.add_argument("--current", type=Path)
    parser.add_argument("--changes", type=Path)
    parser.add_argument("--suffix", type=Path)
    parser.add_argument("--xls", type=Path)
    args = parser.parse_args()
    project = (args.project or Path.cwd()).resolve()
    order = project / "order"
    original = args.original or project / "Axon_PSUAC_R001_BOM_original.csv"
    current = args.current or project / "Axon_PSUAC_R001_BOM_modified.csv"
    changes = args.changes or project / "Axon_PSUAC_R001_BOM_changes.csv"
    suffix = args.suffix or project / "Axon_PSUAC_R001_BOM.csv"
    xls = args.xls or order / "Axon_PSUAC_R001_BOM.xls"
    validate_cpl_input(project / CPL_FILENAME)
    load_cpl_adjustments(CPL_ADJUSTMENTS_PATH)
    change_count = write_changes(original, current, changes)
    suffix_count = write_suffix(current, suffix)
    xls_count = convert_csv_to_xls(suffix, xls)
    cpl_path, cpl_count = write_cpl_xls(project, order, suffix)
    print(f"Wrote {change_count} changed designators to {changes}")
    print(f"Wrote {suffix_count} BOM rows to {suffix}")
    print(f"Wrote {xls_count} BOM rows to {xls}")
    print(f"Wrote {cpl_count} CPL rows to {cpl_path}")


if __name__ == "__main__":
    main()
