from __future__ import annotations

# Intended use:
# Structural-first DCH decoder orchestrator.
# It frames records from binary headers and dispatches to record-type decoders.
# No fallback scanning and no heuristic recovery.

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dch_parser.structural.codec import decode_record_by_descriptor
from dch_parser.structural.codec import encode_record_node_bytes
from dch_parser.structural.record.part import PART_RECORD_DESCRIPTOR
from dch_parser.structural.record.pattern import PATTERN_DESCRIPTOR
from dch_parser.structural.record.cache_entry import CACHE_ENTRY_DESCRIPTOR
from dch_parser.structural.record.cachelib import CACHELIB_DESCRIPTOR
from dch_parser.structural.record.cl_component import CL_COMPONENT_DESCRIPTOR
from dch_parser.structural.record.token_container import TOKEN_CONTAINER_DESCRIPTOR
from dch_parser.structural.record.token_named import TOKEN_NAMED_DESCRIPTOR
from dch_parser.structural.record.unknown import UNKNOWN_RECORD_DESCRIPTOR

START_MARKER = b"\x00\x01\x00\x0F\x42"
ENABLE_HEADER_PAIR_COLLAPSE = True
PART_START_VARIANTS = (
    b"\x00\x01\x00\x0F\x42",  # dominant stream
    b"\x0C\x01\x00\x0F\x42",  # first-record variant observed in Laserdome
    b"\x00\x01\x01\x0F\x42",  # variant observed for top-level Part records
    b"\x0C\x01\x01\x0F\x42",  # same variant with 0x0C lead
)


@dataclass(frozen=True)
class StructuralRecord:
    index: int
    start: int
    end: int
    length: int
    lead: int
    mode: int
    family: str
    group: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "Index": self.index,
            "Offset": self.start,
            "EndOffset": self.end,
            "Length": self.length,
            "Header": {
                "Lead": self.lead,
                "Mode": self.mode,
                "MarkerHex": START_MARKER.hex(" "),
            },
            "Family": self.family,
        }


def read_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def encode_structural_tree(tree: dict[str, Any]) -> bytes:
    if not isinstance(tree, dict):
        raise ValueError("tree must be a dict")
    internal = tree.get("_internal")
    if not isinstance(internal, dict):
        raise ValueError("tree missing _internal")
    prefix = internal.get("prefix_bytes")
    if not isinstance(prefix, (bytes, bytearray)):
        raise ValueError("tree missing _internal.prefix_bytes")

    top_records_by_index: dict[int, tuple[int, dict[str, Any]]] = {}

    def _walk(n: dict[str, Any]) -> None:
        ni = n.get("_internal", {})
        if isinstance(ni, dict):
            if bool(ni.get("top_level", False)):
                ti = ni.get("top_index")
                off = ni.get("offset")
                if isinstance(ti, int) and isinstance(off, int):
                    prev = top_records_by_index.get(ti)
                    if prev is None or off < int(prev[0]):
                        top_records_by_index[ti] = (off, n)
        for ch in n.get("children", []) if isinstance(n.get("children"), list) else []:
            if isinstance(ch, dict):
                _walk(ch)

    for b in tree.get("children", []) if isinstance(tree.get("children"), list) else []:
        if isinstance(b, dict):
            _walk(b)

    top_records = sorted(((ti, off, n) for ti, (off, n) in top_records_by_index.items()), key=lambda x: x[0])
    out = bytearray(prefix)
    for _ti, _off, n in top_records:
        out.extend(encode_record_node_bytes(n))
    return bytes(out)


def _is_strict_top_level_start(data: bytes, i: int) -> bool:
    if not (
        i + 7 < len(data)
        and data[i] in (0x00, 0x0C, 0x01, 0x10, 0x42)
        and data[i + 1] == 0x01
        and data[i + 2] in (0x00, 0x01)
        and data[i + 3] == 0x0F
        and data[i + 4] == 0x42
    ):
        return False
    mode = data[i + 6]
    next_b = data[i + 7]
    # Observed invariant across corpus:
    #  - mode 0x0F records continue with 0x42
    #  - mode 0x00/0x01 records continue with token lead bytes
    if mode == 0x0F:
        return next_b == 0x42
    if mode == 0x01:
        return next_b == 0x0F
    if mode == 0x00:
        return next_b in (0x0F, 0x01)
    return False


def _is_generic_record_start(data: bytes, i: int, *, allow_top_lead: bool) -> bool:
    if not (
        i + 7 < len(data)
        and data[i + 1] == 0x01
        and data[i + 2] in (0x00, 0x01)
        and data[i + 3] == 0x0F
        and data[i + 4] == 0x42
    ):
        return False
    x = data[i]
    if allow_top_lead:
        if x not in (0x00, 0x0C, 0x01):
            return False
    else:
        if x in (0x00, 0x0C):
            return False
    mode = data[i + 6]
    next_b = data[i + 7]
    if mode == 0x0F:
        return next_b == 0x42
    if mode == 0x01:
        return next_b == 0x0F
    if mode == 0x00:
        return next_b in (0x0F, 0x01)
    return False


def _find_next_strict_top_level_start(data: bytes, start: int) -> int:
    i = max(0, start)
    limit = len(data) - 6
    while i < limit:
        if _is_strict_top_level_start(data, i):
            return i
        i += 1
    return -1


def _find_first_top_level_start(data: bytes) -> int:
    first_strict = _find_next_strict_top_level_start(data, 0)
    if first_strict < 0:
        return -1

    # Leading prelude record: some files begin with one top-level token record
    # encoded as x 01 yy 0F 42 (x != 00/0C) before the strict stream.
    i = 0
    while i + 6 < first_strict:
        if (
            data[i] not in (0x00, 0x0C)
            and data[i + 1] == 0x01
            and data[i + 2] in (0x00, 0x01)
            and data[i + 3] == 0x0F
            and data[i + 4] == 0x42
            and data[i + 6] == 0x0F
        ):
            return i
        i += 1
    return first_strict


def _find_internal_record_starts(data: bytes, start: int, end: int) -> list[tuple[int, int]]:
    # General nested candidate:
    #   x 01 yy 0F 42, where x != {00,0C}
    out: list[tuple[int, int]] = []
    p = max(start + 1, 0)
    lim = min(end, len(data))
    while p + 6 < lim:
        if _is_generic_record_start(data, p, allow_top_lead=False):
            out.append((p, data[p]))
        p += 1
    return out


def classify_family(mode: int) -> str:
    if mode == 0x0F:
        return "token"
    if mode in (0x00, 0x01):
        return "typed"
    return "unknown"


def decode_structural_records(data: bytes) -> list[StructuralRecord]:
    out: list[StructuralRecord] = []
    s = _find_first_top_level_start(data)
    i = 0
    while s >= 0:
        n = _find_next_strict_top_level_start(data, s + 1)
        e = n if n >= 0 else len(data)
        lead = data[s + 5] if s + 5 < len(data) else -1
        mode = data[s + 6] if s + 6 < len(data) else -1
        out.append(
            StructuralRecord(
                index=i,
                start=s,
                end=e,
                length=e - s,
                lead=lead,
                mode=mode,
                family=classify_family(mode),
                group=0,
            )
        )
        if n < 0:
            break
        s = n
        i += 1
    return out


def _decode_record_payloads(data: bytes, records: list[StructuralRecord]) -> list[dict[str, Any]]:
    decoded: list[dict[str, Any]] = []
    for r in records:
        dec = _decode_one_record(data, r)
        if dec is not None:
            decoded.append(dec)
    return decoded


def _decode_one_record(data: bytes, r: StructuralRecord) -> dict[str, Any] | None:
    # Descriptor-driven decoder selection.
    def _try_descriptors(descs: list[dict[str, Any]]) -> dict[str, Any] | None:
        for d in descs:
            dec = decode_record_by_descriptor(
                data,
                record_index=r.index,
                record_start=r.start,
                record_end=r.end,
                descriptor=d,
            )
            if dec is not None:
                return dec
        return None

    if r.family in ("token", "nested"):
        dec = _try_descriptors(
            [
                CACHELIB_DESCRIPTOR,
                TOKEN_NAMED_DESCRIPTOR,
                TOKEN_CONTAINER_DESCRIPTOR,
                PART_RECORD_DESCRIPTOR,
                CACHE_ENTRY_DESCRIPTOR,
            ]
        )
        if dec is not None:
            return dec
        return decode_record_by_descriptor(
            data,
            record_index=r.index,
            record_start=r.start,
            record_end=r.end,
            descriptor=UNKNOWN_RECORD_DESCRIPTOR,
        )
    if r.family in ("typed", "nested") and r.mode == 1:
        dec = _try_descriptors([PATTERN_DESCRIPTOR, CACHE_ENTRY_DESCRIPTOR])
        if dec is not None:
            return dec
        return decode_record_by_descriptor(
            data,
            record_index=r.index,
            record_start=r.start,
            record_end=r.end,
            descriptor=UNKNOWN_RECORD_DESCRIPTOR,
        )
    return decode_record_by_descriptor(
        data,
        record_index=r.index,
        record_start=r.start,
        record_end=r.end,
        descriptor=UNKNOWN_RECORD_DESCRIPTOR,
    )


def decode_structural_components(path: str | Path) -> dict[str, Any]:
    data = read_bytes(path)
    records = decode_structural_records(data)
    decoded_records = _decode_record_payloads(data, records)
    decoded_by_index: dict[int, dict[str, Any]] = {}
    for d in decoded_records:
        try:
            decoded_by_index[int(d.get("RecordIndex", -1))] = d
        except (TypeError, ValueError):
            continue
    decoded_any_cache: dict[tuple[int, int, int, int, str], dict[str, Any] | None] = {}

    def _decode_cached(r: StructuralRecord) -> dict[str, Any] | None:
        k = (int(r.start), int(r.end), int(r.mode), int(r.lead), str(r.family))
        if k in decoded_any_cache:
            return decoded_any_cache[k]
        dec = _decode_one_record(data, r)
        decoded_any_cache[k] = dec
        return dec

    # Structural depth rules from header transitions only.
    # Rule A: token(b2=0) -> typed(mode=1) immediate follower is a child.
    # Rule B: token(b2=1) -> typed(mode=0) immediate follower is a child.
    # These are header-class transitions, not record-type checks.
    collapsed_children_by_parent_index: dict[int, list[StructuralRecord]] = {}
    collapsed_child_indices: set[int] = set()
    header_pair_relation_by_child_index: dict[int, dict[str, int]] = {}
    if ENABLE_HEADER_PAIR_COLLAPSE:
        for i in range(0, len(records) - 1):
            r = records[i]
            n = records[i + 1]
            if n.index in collapsed_child_indices:
                continue
            rb2 = data[r.start + 2] if r.start + 2 < len(data) else -1
            attach = r.mode == 0x0F and (
                (rb2 == 0x00 and n.mode == 0x01) or (rb2 == 0x01 and n.mode == 0x00)
            )
            if not attach:
                continue
            collapsed_children_by_parent_index.setdefault(r.index, []).append(n)
            collapsed_child_indices.add(n.index)
            rel: dict[str, int] = {
                "parent_index": int(r.index),
                "parent_start": int(r.start),
                "parent_end": int(r.end),
                "child_start": int(n.start),
                "child_end": int(n.end),
            }
            pdec2 = decoded_by_index.get(r.index)
            if isinstance(pdec2, dict):
                ce2 = pdec2.get("ConsumedEndOffset")
                if isinstance(ce2, int):
                    rel["parent_consumed_end"] = int(ce2)
                    rel["gap_from_parent_consumed"] = int(n.start - ce2)
            header_pair_relation_by_child_index[n.index] = rel

    # Structural tree: root -> contiguous mode-runs -> record nodes.
    # Nested records are parsed recursively by the same structural header rule.
    def _should_attach_child_by_header(parent: dict[str, Any], child: dict[str, Any]) -> bool:
        try:
            po = int(parent.get("_internal", {}).get("offset", -1))
            pmode = int(parent.get("_internal", {}).get("header", {}).get("Mode", -1))
            cmode = int(child.get("_internal", {}).get("header", {}).get("Mode", -1))
        except (TypeError, ValueError):
            return False
        if po < 0 or po + 2 >= len(data):
            return False
        pb2 = data[po + 2]
        return pmode == 0x0F and ((pb2 == 0x00 and cmode == 0x01) or (pb2 == 0x01 and cmode == 0x00))

    def _collapse_children_by_header(children: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not children:
            return children
        out: list[dict[str, Any]] = []
        i = 0
        while i < len(children):
            p = children[i]
            if i + 1 < len(children):
                c = children[i + 1]
                pf = str(p.get("_internal", {}).get("family", ""))
                cf = str(c.get("_internal", {}).get("family", ""))
                structural_ok = pf not in ("subrecord", "extension") and cf not in ("subrecord", "extension")
                if structural_ok and _should_attach_child_by_header(p, c):
                    p_children = p.get("children")
                    if not isinstance(p_children, list):
                        p["children"] = [c]
                    else:
                        p_children.append(c)
                    out.append(p)
                    i += 2
                    continue
            out.append(p)
            i += 1
        return out

    def _build_record_node(r: StructuralRecord, depth: int = 0, max_depth: int = 6) -> dict[str, Any]:
        dec = decoded_by_index.get(r.index) if r.family != "nested" else _decode_cached(r)
        child_nodes: list[dict[str, Any]] = []

        def _decoded_child_to_node(c: dict[str, Any], seq_index: int) -> dict[str, Any]:
            return {
                "type": str(c.get("RecordType", "Record")),
                "props": c.get("Fields", {}) if isinstance(c.get("Fields"), dict) else {},
                "children": [],
                "_internal": {
                    "node_type": "Record",
                    "index": int(seq_index),
                    "offset": int(c.get("Offset", -1)),
                    "end_offset": int(c.get("EndOffset", -1)),
                    "consumed_end_offset": int(c.get("ConsumedEndOffset", c.get("EndOffset", -1))),
                    "length": int(c.get("Length", -1)),
                    "header": {"Lead": -1, "Mode": -1},
                    "family": "subrecord",
                    "decoded_record_type": str(c.get("RecordType", "")),
                    "codec": str(c.get("Descriptor", "")),
                    "field_offsets": c.get("FieldOffsets", {}) if isinstance(c.get("FieldOffsets"), dict) else {},
                    "original_props": dict(c.get("Fields", {})) if isinstance(c.get("Fields"), dict) else {},
                    "raw": bytearray(),
                },
            }
        parse_generic_nested = True
        if depth < max_depth and parse_generic_nested:
            nested_scan_start = r.start
            if isinstance(dec, dict):
                ce = dec.get("ConsumedEndOffset")
                if isinstance(ce, int) and r.start <= ce <= r.end:
                    if ce > nested_scan_start:
                        nested_scan_start = ce
            nested = _find_internal_record_starts(data, nested_scan_start, r.end)
            for i, (cs, x) in enumerate(nested):
                ce = nested[i + 1][0] if i + 1 < len(nested) else r.end
                clead = data[cs + 5] if cs + 5 < len(data) else -1
                cmode = data[cs + 6] if cs + 6 < len(data) else -1
                nr = StructuralRecord(
                    index=i,
                    start=cs,
                    end=ce,
                    length=ce - cs,
                    lead=clead,
                    mode=cmode,
                    family="nested",
                )
                nnode = _build_record_node(nr, depth=depth + 1, max_depth=max_depth)
                # Preserve nested-local header type byte.
                if isinstance(nnode.get("_internal"), dict):
                    nnode["_internal"]["header"] = {
                        "TypeByte": x,
                        "Lead": clead,
                        "Mode": cmode,
                    }
                child_nodes.append(nnode)

        if isinstance(dec, dict):
            dec_children = dec.get("Children")
            if isinstance(dec_children, list):
                for dc in dec_children:
                    if isinstance(dc, dict):
                        child_nodes.append(_decoded_child_to_node(dc, len(child_nodes)))

        for cr in collapsed_children_by_parent_index.get(r.index, []):
            ch_node = _build_record_node(cr, depth=depth + 1, max_depth=max_depth)
            rel = header_pair_relation_by_child_index.get(cr.index)
            if isinstance(rel, dict):
                ch_node.setdefault("_internal", {})
                ch_node["_internal"]["attached_by_header_pair"] = rel
            child_nodes.append(ch_node)

        child_nodes.sort(key=lambda n: int(n.get("_internal", {}).get("offset", 0)))
        child_nodes = _collapse_children_by_header(child_nodes)

        props: dict[str, Any] = {}
        rec_type = "Record"
        codec_desc = ""
        decoded_record_type = ""
        if isinstance(dec, dict):
            rec_type = str(dec.get("RecordType", "Record")) or "Record"
            decoded_record_type = str(dec.get("RecordType", ""))
            codec_desc = str(dec.get("Descriptor", ""))
            f = dec.get("Fields")
            if isinstance(f, dict):
                props.update(f)

        return {
            "type": rec_type,
            "props": props,
            "children": child_nodes,
            "_internal": {
                "node_type": "Record",
                "index": r.index,
                "top_level": r.family != "nested",
                "top_index": r.index if r.family != "nested" else -1,
                "offset": r.start,
                "end_offset": r.end,
                "consumed_end_offset": int(dec.get("ConsumedEndOffset", r.end)) if isinstance(dec, dict) else r.end,
                "length": r.length,
                "header": {"Lead": r.lead, "Mode": r.mode},
                "family": r.family,
                "decoded_record_type": decoded_record_type,
                "codec": codec_desc,
                "field_offsets": dec.get("FieldOffsets", {}) if isinstance(dec.get("FieldOffsets"), dict) else {},
                "original_props": dict(dec.get("Fields", {})) if isinstance(dec.get("Fields"), dict) else {},
                "raw": bytearray(data[r.start:r.end]) if 0 <= r.start < r.end <= len(data) else bytearray(),
            },
        }

    collapsed_indices = collapsed_child_indices
    visible_records = [r for r in records if r.index not in collapsed_indices]
    run_nodes: list[dict[str, Any]] = []

    def _append_run_segment(seg: list[StructuralRecord], mode: int) -> None:
        if not seg:
            return
        seg_children = [_build_record_node(r, depth=0) for r in seg]
        type_counts: dict[str, int] = {}
        part_positions: list[int] = []
        nonpart_positions: list[int] = []
        for j, r in enumerate(seg):
            decj = decoded_by_index.get(r.index)
            rtj = str(decj.get("RecordType", "None")) if isinstance(decj, dict) else "None"
            type_counts[rtj] = type_counts.get(rtj, 0) + 1
            if rtj == "Part":
                part_positions.append(j)
            else:
                nonpart_positions.append(j)

        branch_hint = "non_token_run"
        cache_tail_start_offset: int | None = None
        if int(mode) == 0x0F:
            part_count = int(type_counts.get("Part", 0))
            nonpart_count = int(len(seg) - part_count)
            if part_count == 0 and nonpart_count > 0:
                branch_hint = "cache_run"
            elif part_count > 0 and nonpart_count == 0:
                branch_hint = "components_only"
            elif part_count > 0 and nonpart_count > 0:
                first_nonpart = min(nonpart_positions) if nonpart_positions else -1
                part_after_nonpart = sum(1 for p in part_positions if p > first_nonpart) if first_nonpart >= 0 else 0
                if part_after_nonpart == 0 and first_nonpart >= 0:
                    branch_hint = "components_then_cache_tail"
                    if 0 <= first_nonpart < len(seg):
                        cache_tail_start_offset = int(seg[first_nonpart].start)
                else:
                    branch_hint = "components_mixed"
                    if part_positions:
                        last_part = max(part_positions)
                        if 0 <= last_part + 1 < len(seg):
                            cache_tail_start_offset = int(seg[last_part + 1].start)

        run_nodes.append(
            {
                "NodeType": "ModeRun",
                "Mode": int(mode),
                "Family": seg[0].family,
                "StartIndex": seg[0].index,
                "EndIndex": seg[-1].index,
                "Offset": seg[0].start,
                "EndOffset": seg[-1].end,
                "Count": len(seg),
                "Children": seg_children,
                "RunRecordTypeCounts": dict(sorted(type_counts.items())),
                "BranchHint": branch_hint,
                "CacheTailStartOffset": cache_tail_start_offset,
            }
        )

    if visible_records:
        run_start = 0
        cur_mode = visible_records[0].mode
        for i in range(1, len(visible_records)):
            if visible_records[i].mode != cur_mode:
                _append_run_segment(visible_records[run_start:i], cur_mode)
                run_start = i
                cur_mode = visible_records[i].mode
        _append_run_segment(visible_records[run_start:], cur_mode)

    # Normalize visible mode-runs into semantic branches.
    component_branch_records: list[dict[str, Any]] = []
    cache_branch_records: list[dict[str, Any]] = []
    for run in run_nodes:
        if not isinstance(run, dict):
            continue
        mode = int(run.get("Mode", -1)) if isinstance(run.get("Mode"), int) else -1
        if mode != 0x0F:
            continue
        hint = str(run.get("BranchHint", ""))
        cache_tail_start = run.get("CacheTailStartOffset")
        cache_tail_start_i = int(cache_tail_start) if isinstance(cache_tail_start, int) else None
        children = run.get("Children", []) if isinstance(run.get("Children"), list) else []
        if hint == "cache_run":
            for rec in children:
                if not isinstance(rec, dict):
                    continue
                cache_branch_records.append(rec)
            continue

        for rec in children:
            if not isinstance(rec, dict):
                continue
            rec_off = int(rec.get("_internal", {}).get("offset", -1))
            in_cache_tail = cache_tail_start_i is not None and rec_off >= cache_tail_start_i
            if in_cache_tail:
                cache_branch_records.append(rec)
            else:
                component_branch_records.append(rec)

    def _walk_records(node: dict[str, Any]) -> list[dict[str, Any]]:
        out_nodes: list[dict[str, Any]] = []
        if not isinstance(node, dict):
            return out_nodes
        out_nodes.append(node)
        for ch in node.get("children", []) if isinstance(node.get("children"), list) else []:
            if isinstance(ch, dict):
                out_nodes.extend(_walk_records(ch))
        return out_nodes

    # Global structural promotion rule:
    # If a nested TokenContainer is a record-list container, its direct child records
    # represent cache-list items and are promoted into CacheLib branch.
    existing_cache_offsets: set[int] = set()
    for rec in cache_branch_records:
        if isinstance(rec, dict):
            off = rec.get("_internal", {}).get("offset")
            if isinstance(off, int):
                existing_cache_offsets.add(int(off))
    for top in component_branch_records:
        if not isinstance(top, dict):
            continue
        for node in _walk_records(top):
            if str(node.get("type", "")) not in ("TokenContainer", "CacheLib"):
                continue
            f = node.get("props")
            if not isinstance(f, dict):
                continue
            if str(f.get("RoleCandidate", "")) != "RecordListContainer":
                continue
            for child in node.get("children", []) if isinstance(node.get("children"), list) else []:
                if not isinstance(child, dict):
                    continue
                off = int(child.get("_internal", {}).get("offset", -1))
                if off < 0 or off in existing_cache_offsets:
                    continue
                cache_branch_records.append(child)
                existing_cache_offsets.add(off)

    # Deterministic cache-component decoding for cache branch nodes.
    for rec in cache_branch_records:
        if not isinstance(rec, dict):
            continue
        s = rec.get("_internal", {}).get("offset")
        e = rec.get("_internal", {}).get("end_offset")
        if not (isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(data)):
            continue
        cc = decode_record_by_descriptor(
            data,
            record_index=int(rec.get("_internal", {}).get("index", 0)),
            record_start=int(s),
            record_end=int(e),
            descriptor=CL_COMPONENT_DESCRIPTOR,
        )
        if not isinstance(cc, dict):
            continue
        f = cc.get("Fields")
        if not isinstance(f, dict):
            continue
        pattern_candidates = f.get("PatternCandidates")
        link_tokens = f.get("LinkTokens")
        if not (
            (isinstance(pattern_candidates, list) and len(pattern_candidates) > 0)
            or (isinstance(link_tokens, list) and len(link_tokens) > 0)
        ):
            continue
        rec["_cache_component"] = cc

    aux_typed_records: list[dict[str, Any]] = []
    for run in run_nodes:
        if not isinstance(run, dict):
            continue
        mode = int(run.get("Mode", -1)) if isinstance(run.get("Mode"), int) else -1
        if mode in (0x0F,):
            continue
        for rec in run.get("Children", []) if isinstance(run.get("Children"), list) else []:
            if isinstance(rec, dict):
                aux_typed_records.append(rec)

    # Finalize cache nodes in-place: canonical record type and merged cache decode fields.
    for rec in cache_branch_records:
        if not isinstance(rec, dict):
            continue
        rec["type"] = "cl_Component"
        ccd = rec.get("_cache_component")
        if isinstance(ccd, dict):
            cf = ccd.get("Fields")
            if isinstance(cf, dict):
                props = rec.get("props")
                if not isinstance(props, dict):
                    props = {}
                    rec["props"] = props
                props.update(cf)
            rec.setdefault("_internal", {})
            rec["_internal"]["codec"] = str(ccd.get("Descriptor", rec.get("_internal", {}).get("codec", "")))

    normalized_branches: list[dict[str, Any]] = []
    if component_branch_records:
        normalized_branches.append(
            {
                "type": "Components",
                "props": {},
                "children": [c for c in component_branch_records if isinstance(c, dict)],
                "_internal": {"node_type": "Branch", "codec": "Components.v1"},
            }
        )
    if cache_branch_records:
        normalized_branches.append(
            {
                "type": "CacheLib",
                "props": {},
                "children": [c for c in cache_branch_records if isinstance(c, dict)],
                "_internal": {"node_type": "Branch", "codec": "CacheLib.v1"},
            }
        )
    if aux_typed_records:
        normalized_branches.append(
            {
                "type": "AuxTyped",
                "props": {},
                "children": [c for c in aux_typed_records if isinstance(c, dict)],
                "_internal": {"node_type": "Branch", "codec": "AuxTyped.v1"},
            }
        )

    prefix_end = records[0].start if records else 0

    out_tree = {
        "type": "Schematic",
        "props": {
            "Source": str(path),
        },
        "children": normalized_branches,
        "_internal": {
            "codec": "Schematic.v1",
            "offset": 0,
            "end_offset": len(data),
            "prefix_bytes": bytearray(data[:prefix_end]),
        },
    }
    out_tree["clean_eof"] = True
    return out_tree
