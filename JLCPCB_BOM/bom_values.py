"""Shared SI prefixes and value sorting for BOM tools."""
import re
from decimal import Decimal

# Official SI prefix symbols, plus interchangeable micro spellings.
# https://www.bipm.org/en/measurement-units/si-prefixes
SI_PREFIX_EXPONENTS = {
    "q": -30, "r": -27, "y": -24, "z": -21, "a": -18, "f": -15,
    "p": -12, "n": -9, "µ": -6, "μ": -6, "u": -6, "m": -3,
    "c": -2, "d": -1, "": 0, "da": 1, "h": 2, "k": 3,
    "M": 6, "G": 9, "T": 12, "P": 15, "E": 18, "Z": 21,
    "Y": 24, "R": 27, "Q": 30,
}
VALUE_UNITS = {"R", "Ω", "F", "H", "Hz", "V", "VDC", "VAC", "A", "W", "s", "m", "K", "%", "Ohm", "ohm", "Ohms", "ohms"}
NUMBER_TEXT = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
QUANTITY_RE = re.compile(rf"^({NUMBER_TEXT})([A-Za-zµμΩ%]*)(?=$|[\s-])")
SCIENTIFIC_QUANTITY_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)[eE][+-]?\d+[A-Za-zµμΩ%]*(?=$|[\s-])")
PREFIX_PATTERN = "|".join(re.escape(prefix) for prefix in sorted(SI_PREFIX_EXPONENTS, key=len, reverse=True) if prefix)
EMBEDDED_QUANTITY_RE = re.compile(rf"^([+-]?\d+)({PREFIX_PATTERN})(\d+)([A-Za-zΩ%]*)(?=$|[\s-])")


def engineering_magnitude(text):
    """Parse the leading BOM value, ignoring following voltage/tolerance text."""
    text = text.strip()
    embedded = None if SCIENTIFIC_QUANTITY_RE.match(text) else EMBEDDED_QUANTITY_RE.match(text)
    if embedded and (not embedded[4] or embedded[4] in VALUE_UNITS):
        prefix = embedded[2]
        # R between digits is the electronics decimal marker: 4R7 = 4.7Ω.
        exponent = 0 if prefix == "R" and embedded[4] in {"", "R", "Ω"} else SI_PREFIX_EXPONENTS[prefix]
        return Decimal(f"{embedded[1]}.{embedded[3]}").scaleb(exponent)
    match = QUANTITY_RE.match(text)
    if not match:
        return None
    number, suffix = match.groups()
    # A trailing R is the conventional resistor unit, not ronna.
    if suffix == "R":
        exponent = 0
    elif suffix in SI_PREFIX_EXPONENTS:
        exponent = SI_PREFIX_EXPONENTS[suffix]
    elif suffix in VALUE_UNITS:
        exponent = 0
    else:
        exponent = None
        for prefix in sorted(SI_PREFIX_EXPONENTS, key=len, reverse=True):
            if prefix and suffix.startswith(prefix) and suffix[len(prefix):] in VALUE_UNITS:
                exponent = SI_PREFIX_EXPONENTS[prefix]
                break
        if exponent is None:
            return None
    return Decimal(number).scaleb(exponent)


def natural_sort_key(text):
    return tuple((1, Decimal(part)) if re.fullmatch(r"\d+(?:\.\d+)?", part) else (0, part.casefold())
                 for part in re.split(r"(\d+(?:\.\d+)?)", text))


def value_sort_key(text):
    value = engineering_magnitude(text)
    # Equal magnitudes have exactly equal keys, preserving previous sorts even
    # for alternate spellings such as 100nF, 0.1uF, and 0.1µF.
    return (0, value) if value is not None else (1, natural_sort_key(text))
