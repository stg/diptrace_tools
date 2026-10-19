"""DipTrace references: an arbitrary prefix followed by a numeric counter."""
import re


# Lists may use commas, semicolons, whitespace, or the legacy "R208. R8" typo.
# Match whole tokens so an embedded counter never truncates e.g. J6-1 to J6.
REFERENCE_RE = re.compile(r"(?<![^\s,;.])[^\s,;.]*[0-9]+(?=$|[\s,;.])")


def split_reference(reference):
    """Return the prefix and the consecutive digits at the end as an integer."""
    match = re.fullmatch(r"(.*?)([0-9]+)", reference)
    if match is None:
        raise ValueError(f"Reference has no trailing numeric counter: {reference!r}")
    return match[1], int(match[2])


def reference_sort_key(reference):
    """Sort every numeric run naturally, including counters after rich prefixes."""
    return tuple((1, int(part)) if index % 2 else (0, part.upper())
                 for index, part in enumerate(re.split(r"([0-9]+)", reference)))
