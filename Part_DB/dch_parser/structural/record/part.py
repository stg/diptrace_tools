from __future__ import annotations

# Intended use:
# Descriptor-only schema for Part records.
# Decoding/encoding execution is handled by dch_parser.structural.codec.
from dch_parser.structural.record.pattern import PATTERN_DESCRIPTOR
from dch_parser.structural.record.part_show import PART_SHOW_DESCRIPTOR

PART_RECORD_DESCRIPTOR: dict[str, object] = {
    "record_type": "Part",
    "descriptor": "Part.v1",
    "decode": "layout",
    "subrecords": [
        {
            # Unified embedded subrecord extraction:
            # each declared record binds semantic child name + id + decoder.
            "op": "scan_subrecords",
            "records": [
                {
                    "name": "Pattern",
                    "id": "marker:0f424000|0f424100",
                    "descriptor": PATTERN_DESCRIPTOR,
                    "start_offset": -12,
                    "max_matches": 1,
                },
                {
                    "name": "ShowRefDes",
                    "id": "marker:01 0f 42 41 0f 44 fc",
                    "descriptor": PART_SHOW_DESCRIPTOR,
                    "match_field": "Text",
                    "match_parent_field": "RefDes",
                    "max_matches": 1,
                },
                {
                    "name": "ShowName",
                    "id": "marker:01 0f 42 41 0f 44 fc",
                    "descriptor": PART_SHOW_DESCRIPTOR,
                    "match_field": "Text",
                    "match_parent_field": "Name",
                    "max_matches": 1,
                },
                {
                    "name": "ShowValue",
                    "id": "marker:01 0f 42 41 0f 44 fc",
                    "descriptor": PART_SHOW_DESCRIPTOR,
                    "match_field": "Text",
                    "match_parent_field": "Value",
                    "max_matches": 1,
                },
            ],
        },
    ],
    # Machine-readable ordered layout consumed by codec.
    "layout": [
        {"name": "unknown1", "format": "bin[3]"},
        {"name": "Type", "format": "tok42"},
        {"name": "unknown2", "format": "tok42"},
        {"name": "unknown3", "format": "tok42"},
        {"name": "Page", "format": "tok42"},
        {"name": "Orientation", "format": "tok42"},
        {"name": "Group", "format": "tok42"},
        {"name": "Number", "format": "tok4x"},
        {"name": "unknown11", "format": "u8"},
        {"name": "X", "format": "be24_bias1e4"},
        {"name": "unknown12", "format": "u8"},
        {"name": "Y", "format": "be24_bias1e4"},
        {"name": "unknown13", "format": "u8"},
        {"name": "Width", "format": "be24_bias1e4"},
        {"name": "unknown14", "format": "u8"},
        {"name": "Height", "format": "be24_bias1e4"},
        {"name": "Name", "format": "len16be_utf16be"},
        {"name": "RefDes", "format": "len16be_utf16be"},
        {"name": "Value", "format": "len16be_utf16be"},
        {"name": "BaseRefDes", "format": "len16be_utf16be"},
        {"name": "BaseName", "format": "len16be_utf16be"},
        {"name": "unknown15", "format": "tok42", "optional": True},
        {"name": "unknown16", "format": "tok42", "optional": True},
        {"name": "unknown17", "format": "bin[1]", "optional": True},
        {"name": "unknown18", "format": "tok42", "optional": True},
        {"name": "PartType", "format": "tok42", "optional": True},
        {"name": "unknown20", "format": "bin[1]", "optional": True},
        {"name": "PartName", "format": "len16le_utf16le", "optional": True},
        {"name": "PartDescriptor", "format": "len16le_utf16le", "optional": True},
        {"name": "ShowRefDes", "format": "tok42", "optional": True},
        {"name": "ShowNameFlag", "format": "tok42", "optional": True},
        {"name": "ShowNumbers", "format": "tok42", "optional": True},
        {"name": "TextPosX", "format": "mbe24_bias1e4", "optional": True},
        {"name": "TextPosY", "format": "mbe24_bias1e4", "optional": True},
        {"name": "TextPosX2", "format": "mbe24_bias1e4", "optional": True},
        {"name": "TextPosY2", "format": "mbe24_bias1e4", "optional": True},
        {"name": "unknown21", "format": "bin[2]", "optional": True},
        {"name": "HiddenId", "format": "tok4x", "optional": True},
        {"name": "unknown23", "format": "tok42", "optional": True},
        {"name": "FieldIndex1", "format": "tok42", "optional": True},
        {"name": "FieldIndex2", "format": "tok42", "optional": True},
        {"name": "unknown22", "format": "bin[1]", "optional": True},
        {"name": "Angle", "format": "mbe24_bias1e4", "optional": True},
        {"name": "LibPath", "format": "len16be_utf16be", "optional": True},
        {"name": "LibPath_Variable", "format": "len16be_utf16be", "optional": True},
    ],
    "required_non_empty_fields": ["RefDes"],
    "derived_fields": [
        {"name": "SelectedByte", "op": "u8_from_bin_field", "source": "unknown1", "index": 2},
        {
            "name": "Selected",
            "op": "map_eq",
            "source": "SelectedByte",
            "default": "N",
            "matches": [{"value": 1, "result": "Y"}],
        },
        {"name": "LockedByte", "op": "u8_from_bin_field", "source": "unknown22", "index": 0},
        {
            "name": "Locked",
            "op": "map_eq",
            "source": "LockedByte",
            "default": "N",
            "matches": [{"value": 1, "result": "Y"}],
        },
    ],
}
