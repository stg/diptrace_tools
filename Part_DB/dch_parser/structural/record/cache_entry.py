from __future__ import annotations

# Intended use:
# Minimal typed decoder for cache-link records carrying 0f 42 [40|41] marker strings.

CACHE_ENTRY_DESCRIPTOR: dict[str, object] = {
    "record_type": "CacheEntry",
    "descriptor": "CacheEntry.v1",
    "decode": "scan_cache_markers",
    "semantics": [
        {"key": "cache_pattern_name", "field": "Marker40Strings", "priority": 100},
        {"key": "cache_name_token", "field": "Marker41Strings", "priority": 100},
    ],
}
