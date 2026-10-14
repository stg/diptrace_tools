from __future__ import annotations

# Intended use:
# Shared descriptor schema for Part show-tuple subrecords.


PART_SHOW_DESCRIPTOR: dict[str, object] = {
    "record_type": "PartShow",
    "descriptor": "PartShowTuple.v1",
    "decode": "layout",
    "header": {
        "checks": [
            {"offset": 0, "hex": "01 0f 42 41 0f 44 fc"},
            {"offset": 10, "hex": "00 00 00"},
        ],
        "payload_offset": 7,
    },
    "layout": [
        {"name": "FontToken", "format": "tok42"},
        {"name": "unknown1", "format": "bin[3]"},
        {"name": "TextToken", "format": "tok42"},
        {"name": "FontName", "format": "len16be_utf16be"},
        {"name": "Text", "format": "len16be_utf16be"},
        {"name": "Metric0", "format": "mbe24_bias1e4"},
        {"name": "TokenA", "format": "tok42"},
        {"name": "X", "format": "mbe24_bias1e4", "optional": True},
        {"name": "Y", "format": "mbe24_bias1e4", "optional": True},
        {"name": "Metric1", "format": "mbe24_bias1e4", "optional": True},
        {"name": "unknown2", "format": "bin[2]", "optional": True},
        {"name": "Metric2", "format": "mbe24_bias1e4", "optional": True},
        {"name": "Metric3", "format": "mbe24_bias1e4", "optional": True},
    ],
}
