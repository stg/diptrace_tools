"""Discrete-value formatting adapted from JLCPCB_BOM's BOM conversions.

Only recognized numeric quantities are changed. Display text and trailing
voltage/tolerance/assembly notes are preserved; this is not an assembly export.
"""
import re

# Extracted from JLCPCB_BOM/bom_values.py; SI symbols remain case-sensitive.
SI_PREFIXES = ("q", "r", "y", "z", "a", "f", "p", "n", "µ", "m", "c", "d",
               "", "da", "h", "k", "M", "G", "T", "P", "E", "Z", "Y", "R", "Q")
PREFIX_PATTERN = "|".join(re.escape(s) for s in sorted(SI_PREFIXES, key=len, reverse=True) if s)
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
QUANTITY = re.compile(rf"^({NUMBER})({PREFIX_PATTERN}|)$")
EMBEDDED = re.compile(rf"^([+-]?\d+)({PREFIX_PATTERN})(\d+)$")
LEGACY_PREFIX_PATTERN = "|".join(re.escape(s) for s in sorted((*SI_PREFIXES, "u", "μ"), key=len, reverse=True) if s)
LEGACY_EMBEDDED = re.compile(rf"^(\d+)({LEGACY_PREFIX_PATTERN})(\d+)(?i:[RFΩ])?$")
EXPLICIT_RESISTANCE = re.compile(rf"^(?:\d+(?:\.\d+)?(?:{LEGACY_PREFIX_PATTERN})?|\d+(?:{LEGACY_PREFIX_PATTERN})\d+)[RΩ]$")
SCIENTIFIC = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)[eE][+-]?\d+[A-Za-zµμΩ%]*(?=$|[\s-])")


def normalize_quantity(token, unit):
    token = token.replace("u", "µ").replace("U", "µ").replace("μ", "µ").replace("Ω", "Ω")
    suffixes = {"Ω": ("Ohms", "ohms", "Ohm", "ohm", "Ω", "R"), "F": ("F",), "H": ("H",)}[unit]
    for suffix in suffixes:
        if token.endswith(suffix):
            token = token[:-len(suffix)]
            break
    # Test scientific notation before embedded forms (1E3 is not 1.3 exa).
    match = QUANTITY.fullmatch(token)
    if match:
        number, scale = match.groups()
        if unit == "Ω" and scale == "R":
            scale = ""
        return number + scale + unit
    embedded = EMBEDDED.fullmatch(token)
    if embedded:
        before, scale, after = embedded.groups()
        if scale == "R":
            if unit != "Ω":
                return None
            scale = ""
        return f"{before}.{after}{scale}{unit}"
    return None


def normalize_value(value, refdes, rule):
    if not value:
        return value
    leading = re.match(r"[A-Za-z]+", refdes)
    prefix = leading.group().upper() if leading else ""
    if rule.get("match_first_letter", False):
        prefix = prefix[:1]
    unit = rule.get("designator_units", {"R": "Ω", "C": "F"}).get(prefix)
    first, *trailing = value.split(maxsplit=1)
    # As in JLCPCB_BOM, explicit resistance also works for non-R/C parts (e.g. L5 = 0R).
    if unit is None and rule.get("explicit_resistance", True):
        embedded = LEGACY_EMBEDDED.fullmatch(first)
        if not rule.get("recognized_only", True):
            explicit = EXPLICIT_RESISTANCE.fullmatch(first) or (embedded and embedded.group(2) == "R")
        else:
            explicit = re.search(r"(?:R|Ω|Ω|[Oo]hms?)$", first) or re.fullmatch(r"[+-]?\d+R\d+", first)
        if explicit:
            unit = "Ω"
    if unit is None:
        return value
    return format_quantity(value, unit, recognized_only=rule.get("recognized_only", True))


def format_quantity(value, unit, *, recognized_only=True):
    if not value:
        return value
    first, *trailing = value.split(maxsplit=1)
    if recognized_only:
        normalized = normalize_quantity(first, unit)
    else:
        # Compatibility with the established JLCPCB_BOM assembly formatter.
        first = first.replace("u", "µ").replace("U", "µ").replace("μ", "µ")
        embedded = None if SCIENTIFIC.fullmatch(first) else LEGACY_EMBEDDED.fullmatch(first)
        if embedded:
            scale = embedded.group(2)
            if unit == "Ω" and scale == "R":
                scale = ""
            first = f"{embedded.group(1)}.{embedded.group(3)}{scale}"
        else:
            first = re.sub(r"[RFΩ]$", "", first)
        normalized = first + unit
    if normalized is None:
        return value
    return normalized + (" " + trailing[0] if trailing else "")
