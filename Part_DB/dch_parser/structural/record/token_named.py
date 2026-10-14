from __future__ import annotations

# Intended use:
# Descriptor-only schema for TokenNamed records.

TOKEN_NAMED_DESCRIPTOR: dict[str, object] = {
    "record_type": "TokenNamed",
    "descriptor": "TokenNamed.v1",
    "decode": "layout",
    "header": {
        "checks": [
            {"offset": 1, "hex": "01 01 0f 42 41 0f 42 40 00 00 00"},
        ],
        "payload_offset": 12,
    },
    "layout": [
        {"name": "Name", "format": "len16le_utf16le"},
        {"name": "Meta", "format": "len16le_utf16le", "optional": True},
    ],
    "derived_fields": [
        {"name": "RawTypeByte", "op": "u8_at_record_offset", "offset": 0},
        {
            "name": "RoleCandidate",
            "op": "map_eq",
            "source": "RawTypeByte",
            "matches": [{"value": 64, "result": "EntryNameRecord"}],
            "default": "NameRecord",
        },
    ],
    "required_non_empty_fields": ["Name"],
}
