from __future__ import annotations

# Intended use:
# Descriptor-only schema for CacheLib component entry records.

CL_COMPONENT_DESCRIPTOR: dict[str, object] = {
    "record_type": "cl_Component",
    "descriptor": "cl_Component.v1",
    "decode": "scan_cache_markers",
    "semantics": [
        {"key": "cache_pattern_candidate", "field": "PatternCandidates", "priority": 100},
        {"key": "cache_link_token", "field": "LinkTokens", "priority": 100},
    ],
}
