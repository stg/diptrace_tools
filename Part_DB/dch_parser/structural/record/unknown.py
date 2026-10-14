from __future__ import annotations

# Intended use:
# Fallback descriptor for records with no specific decoder.

UNKNOWN_RECORD_DESCRIPTOR: dict[str, object] = {
    "record_type": "Unknown",
    "descriptor": "Unknown.v1",
    "decode": "layout",
    "layout": [
        {"name": "unknown1", "format": "bin[*]"},
    ],
}
