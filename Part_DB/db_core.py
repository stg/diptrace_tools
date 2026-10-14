"""Compact part index; no dependency on the reverse-engineering project."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dch_parser.structural.decoder import decode_structural_components
from dch_parser.tree_query import TreeQuery
COMPANION_ROOT = Path(__file__).resolve().parent.parent
if str(COMPANION_ROOT) not in sys.path:
    sys.path.insert(0, str(COMPANION_ROOT))
from Shared.normalization import load_rules as load_rule_file, normalize_component, normalize_name
from Shared.value_normalization import normalize_value
from Shared.search import compile_query, matches_terms

APP_DIR = Path(__file__).resolve().parent
DATABASE_PATH = APP_DIR / "parts.json"
SETTINGS_PATH = APP_DIR / "settings.json"
RULES_PATH = APP_DIR / "normalization_rules.json"
SEARCH_FIELDS = ("name", "value", "pattern", "filename")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".vs", ".idea"}
# Change this when extraction semantics change, to invalidate old cached rows.
EXTRACTOR_VERSION = 3


class Cancelled(Exception):
    pass


def natural_key(text):
    return [(1, int(s)) if s.isdigit() else (0, s.casefold())
            for s in re.split(r"(\d+)", text) if s]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    """Replace atomically: an interrupted refresh cannot truncate the old DB."""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_rules(path=RULES_PATH):
    return load_rule_file(path)


def extract_parts(path, rules):
    tree = TreeQuery(decode_structural_components(path))
    branch = tree.find("Components")
    if branch is None:
        raise ValueError("Decoder did not find a Components branch.")
    grouped = {}
    for part in branch.find_all("Part", sort_by_offset=True):
        ref = part.text("RefDes").strip(" \t")
        if not ref or ref.startswith("NetPort"):
            continue
        name, value = part.text("Name"), part.text("Value")
        pattern_node = part.find("Pattern")
        pattern = pattern_node.text("Name") if pattern_node is not None else ""
        key = normalize_component(name, value, pattern, ref, rules)
        grouped.setdefault(key, set()).add(ref)
    return [{"name": n, "value": v, "pattern": p, "refdes": sorted(refs, key=natural_key)}
            for (n, v, p), refs in sorted(grouped.items())]


def refresh_database(root, previous=None, *, progress=lambda *args: None,
                     cancelled=lambda: False, force=False, rules_path=RULES_PATH):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"Base directory does not exist: {root}")
    rules, fingerprint = load_rules(rules_path)
    previous = previous or {}
    reusable = (not force and previous.get("schema_version") == 2
                and previous.get("base_directory") == str(root)
                and previous.get("rules_fingerprint") == fingerprint
                and previous.get("extractor_version") == EXTRACTOR_VERSION)
    cache = previous.get("files", {}) if reusable else {}
    files, failures, discovered = {}, [], []

    def check_cancel():
        if cancelled():
            raise Cancelled()

    def walk_error(error):
        failures.append({"filename": os.path.relpath(error.filename, root), "error": str(error)})

    progress(0, 0, "Discovering schematics…")
    for directory, subdirs, filenames in os.walk(root, onerror=walk_error, followlinks=False):
        check_cancel()
        subdirs[:] = [d for d in subdirs if d.lower() not in SKIP_DIRS and not Path(directory, d).is_symlink()]
        for filename in filenames:
            if filename.lower().endswith(".dch"):
                path = Path(directory, filename)
                if not path.is_symlink():
                    discovered.append(path)
        progress(0, 0, f"Discovering schematics… {len(discovered):,} found")
    discovered.sort(key=lambda p: natural_key(str(p)))
    read_count, reused_count = 0, 0
    for index, path in enumerate(discovered, 1):
        check_cancel()
        filename = path.relative_to(root).as_posix()
        try:
            before = path.stat()
            old = cache.get(filename)
            if (isinstance(old, dict) and old.get("mtime_ns") == before.st_mtime_ns
                    and old.get("size_bytes") == before.st_size and isinstance(old.get("parts"), list)):
                files[filename] = old
                reused_count += 1
            else:
                parts = extract_parts(path, rules)
                after = path.stat()
                if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                    raise ValueError("Schematic changed during refresh; refresh again.")
                files[filename] = {"mtime_ns": after.st_mtime_ns, "size_bytes": after.st_size,
                                   "parts": parts}
                read_count += 1
        except Exception as error:
            failures.append({"filename": filename, "error": str(error)})
        progress(index, len(discovered),
                 f"{index:,}/{len(discovered):,} files · {read_count:,} read · {reused_count:,} unchanged · {len(failures):,} errors — {filename}")
    check_cancel()
    return {"schema_version": 2, "extractor_version": EXTRACTOR_VERSION,
            "base_directory": str(root), "refreshed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "rules_fingerprint": fingerprint, "files": files, "failures": failures,
            "last_scan": {"read": read_count, "reused": reused_count, "found": len(discovered)}}


def grouped_results(database):
    """Derive the search view, rather than duplicating it in the saved JSON."""
    groups = {}
    for filename, file in database.get("files", {}).items():
        for part in file["parts"]:
            key = tuple(part[field] for field in ("name", "value", "pattern"))
            groups.setdefault(key, []).append({"filename": filename, "refdes": part["refdes"]})
    return [{"name": key[0], "value": key[1], "pattern": key[2],
             "uses": sorted(uses, key=lambda use: natural_key(use["filename"]))}
            for key, uses in sorted(groups.items())]


def search_results(groups, query, fields):
    """All whitespace-separated words must match checked fields; RefDes is never searched."""
    terms = compile_query(query)
    for group in groups:
        part_texts = [group[field] for field in fields if field != "filename"]
        if matches_terms(terms, part_texts):
            yield group, group["uses"]
        elif "filename" in fields:
            uses = [use for use in group["uses"]
                    if matches_terms(terms, [*part_texts, use["filename"]])]
            if uses:
                yield group, uses
