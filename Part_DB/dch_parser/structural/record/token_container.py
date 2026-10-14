from __future__ import annotations

# Intended use:
# Descriptor-only schema for TokenContainer records.

TOKEN_CONTAINER_DESCRIPTOR: dict[str, object] = {
    "record_type": "TokenContainer",
    "descriptor": "TokenContainer.v1",
    "decode": "layout",
    "header": {
        "checks": [
            {"offset": 1, "hex": "01 01 0f 42 41 0f 42 40 00 00 00 00 00 00 01 01 00 00"},
        ],
        "payload_offset": 19,
    },
    "layout": [],
    "derived_fields": [
        {"name": "RawTypeByte", "op": "u8_at_record_offset", "offset": 0},
        {"name": "InternalRecordStartCount", "op": "count_internal_record_starts"},
        {"name": "RoleCandidate", "op": "container_role_from_metrics"},
    ],
}
