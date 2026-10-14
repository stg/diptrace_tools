"""Ordered, conditional normalization rules shared by BOM and DB tools."""
import hashlib
import json
import re
from pathlib import Path
from .value_normalization import normalize_value, format_quantity

def load_rules(path):
    config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or config.get("schema_version") != 1 or not isinstance(config.get("rules"), list):
        raise ValueError("Normalization rules must have schema_version 1 and a rules list.")
    for rule in config["rules"]:
        if not isinstance(rule, dict) or rule.get("operation") not in {
                "regex_replace", "set", "format_quantity", "exclude", "clear_names",
                "normalize_discrete_values", "clear_name_when_pattern_starts_with_name"}:
            raise ValueError("Unknown normalization-rule operation.")
        for flag in ("enabled", "case_sensitive", "require_token_boundary", "explicit_resistance", "ignore_case", "recognized_only", "match_first_letter"):
            if flag in rule and type(rule[flag]) is not bool:
                raise ValueError(f"Rule {flag} must be true or false.")
        names = rule.get("names")
        if names is not None and (not isinstance(names, list) or any(not isinstance(n, str) for n in names)):
            raise ValueError("Rule names must be null or a list of strings.")
        if rule["operation"] == "clear_names" and names is None:
            raise ValueError("clear_names requires a names list.")
        if rule["operation"] == "normalize_discrete_values":
            units = rule.get("designator_units", {"R": "Ω", "C": "F"})
            if not isinstance(units, dict) or any(not re.fullmatch(r"[A-Z]+", prefix)
                    or unit not in {"Ω", "F", "H"} for prefix, unit in units.items()):
                raise ValueError("designator_units must map uppercase designator prefixes to Ω, F or H.")
        operation = rule["operation"]
        if operation in {"regex_replace", "set", "format_quantity"}:
            if rule.get("field") not in {"name", "value", "pattern"}:
                raise ValueError(f"{operation} field must be name, value or pattern.")
            if operation == "regex_replace":
                if not isinstance(rule.get("pattern"), str) or not isinstance(rule.get("replacement"), str):
                    raise ValueError("regex_replace requires pattern and replacement strings.")
                if type(rule.get("count", 0)) is not int or rule.get("count", 0) < 0:
                    raise ValueError("regex_replace count must be a nonnegative integer (0 = all).")
            if operation == "set" and not isinstance(rule.get("text"), str):
                raise ValueError("set requires a text string.")
            if operation == "format_quantity" and rule.get("unit") not in {"Ω", "F", "H"}:
                raise ValueError("format_quantity unit must be Ω, F or H.")
        conditions = rule.get("when", {})
        if not isinstance(conditions, dict) or any(field not in {"name", "value", "pattern", "refdes"}
                or not isinstance(pattern, str) for field, pattern in conditions.items()):
            raise ValueError("when must map name, value, pattern and/or refdes to regex strings.")
    # Human-readable descriptions do not affect extraction or invalidate the cache.
    signature = [{key: value for key, value in rule.items() if key != "description"}
                 for rule in config["rules"]]
    fingerprint = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    for index, rule in enumerate(config["rules"], 1):
        flags = re.IGNORECASE if rule.get("ignore_case", False) else 0
        try:
            rule["_when"] = [(field, re.compile(pattern, flags)) for field, pattern in rule.get("when", {}).items()]
            if rule["operation"] == "regex_replace":
                rule["_pattern"] = re.compile(rule["pattern"], flags)
                # Validate replacement escapes and capture references before scanning.
                rule["_pattern"].sub(rule["replacement"], "")
        except (re.error, IndexError) as error:
            raise ValueError(f"Invalid regex in normalization rule {index}: {error}") from error
    return config["rules"], fingerprint


def normalize_name(name, pattern, rules):
    for rule in rules:
        if not name or not rule.get("enabled", True):
            continue
        if rule["operation"] == "normalize_discrete_values":
            continue
        sensitive = rule.get("case_sensitive", True)
        compare = (lambda s: s) if sensitive else (lambda s: s.casefold())
        names = rule.get("names")
        if names is not None and compare(name) not in {compare(n) for n in names}:
            continue
        if rule["operation"] == "clear_names":
            name = ""
            continue
        # Slice the original pattern to keep Unicode case folding from shifting the boundary.
        if compare(pattern[:len(name)]) != compare(name):
            continue
        tail = pattern[len(name):]
        if not rule.get("require_token_boundary", True) or not tail or not tail[0].isalnum():
            name = ""
    return name


def normalize_record(name, value, pattern, refdes, rules):
    fields = {"name": name, "value": value, "pattern": pattern, "refdes": refdes, "excluded": False}
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        if not all(regex.search(fields[field]) for field, regex in rule.get("_when", [])):
            continue
        operation = rule["operation"]
        if operation == "exclude":
            fields["excluded"] = True
        elif operation == "regex_replace":
            field = rule["field"]
            fields[field] = rule["_pattern"].sub(rule["replacement"], fields[field], count=rule.get("count", 0))
        elif operation == "set":
            fields[rule["field"]] = rule["text"]
        elif operation == "format_quantity":
            field = rule["field"]
            fields[field] = format_quantity(fields[field], rule["unit"], recognized_only=rule.get("recognized_only", True))
        elif operation == "normalize_discrete_values":
            fields["value"] = normalize_value(fields["value"], refdes, rule)
        else:
            fields["name"] = normalize_name(fields["name"], fields["pattern"], [rule])
    return fields


def normalize_component(name, value, pattern, refdes, rules):
    fields = normalize_record(name, value, pattern, refdes, rules)
    return fields["name"], fields["value"], fields["pattern"]
