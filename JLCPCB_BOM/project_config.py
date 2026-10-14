"""Creation-time project defaults; later edits belong to the project JSON."""
import json
from copy import deepcopy
from pathlib import Path

PROJECT_DEFAULTS_PATH = Path(__file__).with_name("project_defaults.json")


def project_defaults(stem, path=None):
    config = json.loads(Path(path or PROJECT_DEFAULTS_PATH).read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or config.get("schema_version") != 1 or not isinstance(config.get("project"), dict):
        raise ValueError("Project defaults must have schema_version 1 and a project object.")
    values = deepcopy(config["project"])
    if {"rows", "source", "schema_version"}.intersection(values):
        raise ValueError("Project defaults cannot override rows, source or schema_version.")
    for field in ("output_folder", "cpl_file", "bom_file", "cpl_output_file"):
        if not isinstance(values.get(field), str) or not values[field].strip():
            raise ValueError(f"Project defaults {field} must be a nonempty string.")
        try:
            values[field] = values[field].format(stem=stem)
        except (KeyError, ValueError, IndexError) as error:
            raise ValueError(f"Invalid {field} template; use {{stem}} for the schematic name.") from error
        if field != "output_folder":
            validate_filename(values[field], field, xls=field != "cpl_file")
    if values["bom_file"].casefold() == values["cpl_output_file"].casefold():
        raise ValueError("BOM and CPL output filenames must differ.")
    if type(values.get("cpl_decimals", 3)) is not int or not 0 <= values.get("cpl_decimals", 3) <= 9:
        raise ValueError("cpl_decimals must be an integer from 0 to 9.")
    return values


def validate_filename(filename, field, *, xls=False):
    if not isinstance(filename, str) or not filename.strip() or Path(filename).name != filename or filename in {".", ".."}:
        raise ValueError(f"{field} must be a filename without a folder path.")
    if xls and Path(filename).suffix.lower() != ".xls":
        raise ValueError(f"{field} must end in .xls; output is a legacy Excel workbook.")
    return filename
