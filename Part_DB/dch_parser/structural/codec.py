from __future__ import annotations

# Intended use:
# Shared descriptor codec for record decoders/encoders.
# Known fields are encoded from typed values; unknown fields retain raw binary blobs.

from typing import Any


def decode_len16be_utf16be(data: bytes, pos: int, max_chars: int = 256) -> tuple[str, int, int] | None:
    if pos + 2 > len(data):
        return None
    n = (data[pos] << 8) | data[pos + 1]
    if n < 0 or n > max_chars:
        return None
    end = pos + 2 + 2 * n
    if end > len(data):
        return None
    try:
        text = data[pos + 2 : end].decode("utf-16-be")
    except UnicodeDecodeError:
        return None
    for ch in text:
        o = ord(ch)
        if o == 0:
            return None
        if o < 32 and ch not in ("\t", "\n", "\r"):
            return None
    return text, pos, end


def encode_len16be_utf16be(text: str) -> bytes:
    chars = str(text)
    n = len(chars)
    if n > 0xFFFF:
        raise ValueError("string too long for len16")
    raw = chars.encode("utf-16-be")
    return bytes([(n >> 8) & 0xFF, n & 0xFF]) + raw


def decode_len16le_utf16le(data: bytes, pos: int, max_chars: int = 256) -> tuple[str, int, int] | None:
    if pos + 2 > len(data):
        return None
    n = data[pos] | (data[pos + 1] << 8)
    if n < 0 or n > max_chars:
        return None
    if n == 0:
        return "", pos, pos + 2
    end = pos + 2 + 2 * n
    if end > len(data):
        return None
    try:
        text = data[pos + 2 : end].decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    for ch in text:
        o = ord(ch)
        if o == 0:
            return None
        if o < 32 and ch not in ("\t", "\n", "\r"):
            return None
    return text, pos, end


def decode_len16le_utf16le_noterm(data: bytes, pos: int, max_chars: int = 256) -> tuple[str, int, int] | None:
    if pos + 2 > len(data):
        return None
    n = data[pos] | (data[pos + 1] << 8)
    if n < 0 or n > max_chars:
        return None
    if n == 0:
        return "", pos, pos + 2
    end = pos + 2 + (2 * n - 1)
    if end > len(data):
        return None
    raw = data[pos + 2 : end]
    body = raw[:-1]
    tail = raw[-1]
    if len(body) % 2 != 0:
        return None
    try:
        text = body.decode("utf-16-le") + chr(tail)
    except UnicodeDecodeError:
        return None
    for ch in text:
        o = ord(ch)
        if o == 0:
            return None
        if o < 32 and ch not in ("\t", "\n", "\r"):
            return None
    return text, pos, end


def encode_len16le_utf16le_noterm(text: str) -> bytes:
    chars = str(text)
    n = len(chars)
    if n <= 0:
        raise ValueError("len16le_noterm does not support empty string")
    if n > 0xFFFF:
        raise ValueError("string too long for len16")
    body = chars[:-1].encode("utf-16-le") if n > 1 else b""
    tail = bytes([ord(chars[-1]) & 0xFF])
    return bytes([n & 0xFF, (n >> 8) & 0xFF]) + body + tail


# Primitive raw decoders aligned with legacy heuristic decode vocabulary.
def _primitive_size(fmt: str) -> int | None:
    if fmt in ("u8", "s8"):
        return 1
    if fmt in ("u16le", "u16be"):
        return 2
    if fmt in ("u24be", "be24_bias1e4", "tok42", "tok4x"):
        return 3
    if fmt in ("mbe24_bias1e4",):
        return 4
    return None


def _decode_tok42_value_at(data: bytes, pos: int) -> int | None:
    if pos < 0 or pos + 3 > len(data):
        return None
    if data[pos] != 0x0F or data[pos + 1] != 0x42:
        return None
    return int(data[pos + 2]) - 0x40


def _decode_tok4x_value_at(data: bytes, pos: int) -> int | None:
    if pos < 0 or pos + 3 > len(data):
        return None
    if data[pos] != 0x0F:
        return None
    tag = int(data[pos + 1])
    v = int(data[pos + 2])
    if tag == 0x42:
        return v - 0x40
    if 0x43 <= tag <= 0x4F:
        return ((tag - 0x43) * 256) + 192 + v
    return None


def _decode_be24_with_bias1e4_at(data: bytes, pos: int) -> float | None:
    if pos < 0 or pos + 3 > len(data):
        return None
    raw = (data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2]
    return (float(raw) - float(0x9ACA00)) / 10_000.0


def decode_raw_primitive(data: bytes, pos: int, fmt: str) -> dict[str, Any] | None:
    n = _primitive_size(fmt)
    if n is None or pos < 0 or pos + n > len(data):
        return None
    raw = bytearray(data[pos : pos + n])
    value: Any = None
    if fmt == "u8":
        value = int(data[pos])
    elif fmt == "s8":
        u = int(data[pos])
        value = u - 256 if u >= 128 else u
    elif fmt == "u16le":
        value = int(data[pos] | (data[pos + 1] << 8))
    elif fmt == "u16be":
        value = int((data[pos] << 8) | data[pos + 1])
    elif fmt == "u24be":
        value = int((data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2])
    elif fmt == "tok42":
        v = _decode_tok42_value_at(data, pos)
        value = int(v) if isinstance(v, int) else None
    elif fmt == "tok4x":
        v = _decode_tok4x_value_at(data, pos)
        value = int(v) if isinstance(v, int) else None
    elif fmt == "be24_bias1e4":
        v2 = _decode_be24_with_bias1e4_at(data, pos)
        value = float(v2) if isinstance(v2, float) else None
    elif fmt == "mbe24_bias1e4":
        if data[pos] != 0x3B:
            value = None
        else:
            v3 = _decode_be24_with_bias1e4_at(data, pos + 1)
            value = float(v3) if isinstance(v3, float) else None
    return {
        "format": fmt,
        "offset": int(pos),
        "size": int(n),
        "raw": raw,
        "value": value,
    }


def encode_record_node_bytes(node: dict[str, Any]) -> bytes:
    ni = node.get("_internal", {})
    if not isinstance(ni, dict):
        raise ValueError("record node missing _internal")
    raw = ni.get("raw")
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("record node missing _internal.raw")
    buf = bytearray(raw)
    codec = str(ni.get("codec", ""))
    fields = node.get("props", {}) if isinstance(node.get("props"), dict) else {}
    original = ni.get("original_props", {}) if isinstance(ni.get("original_props"), dict) else {}
    field_offsets = ni.get("field_offsets", {}) if isinstance(ni.get("field_offsets"), dict) else {}

    def _patch(field: str, enc: str) -> None:
        if field not in fields or field not in original:
            return
        if str(fields.get(field, "")) == str(original.get(field, "")):
            return
        fo = field_offsets.get(field)
        if not isinstance(fo, dict):
            raise ValueError(f"cannot encode edited field {field}: missing offsets")
        s = fo.get("start")
        e = fo.get("end")
        if not (isinstance(s, int) and isinstance(e, int) and 0 <= s <= e):
            raise ValueError(f"cannot encode edited field {field}: invalid offsets")
        rec_off = ni.get("offset")
        if not isinstance(rec_off, int):
            raise ValueError("record missing offset")
        ls = s - rec_off
        le = e - rec_off
        if ls < 0 or le < ls or le > len(buf):
            raise ValueError(f"cannot encode edited field {field}: offset out of record")
        if enc == "len16be+utf16be":
            nb = encode_len16be_utf16be(str(fields.get(field, "")))
        elif enc == "len16le+utf16le_noterm":
            nb = encode_len16le_utf16le_noterm(str(fields.get(field, "")))
        else:
            raise ValueError(f"unsupported field encoding {enc}")
        if len(nb) != (le - ls):
            raise ValueError(
                f"edited field {field} changes record length ({len(nb)} != {le - ls}); "
                "variable-length record rewrite not implemented yet"
            )
        buf[ls:le] = nb

    def _patch_unknown_bin(field: str) -> None:
        if field not in fields or field not in original:
            return
        old_v = original.get(field)
        new_v = fields.get(field)
        if not isinstance(old_v, (bytes, bytearray)) or not isinstance(new_v, (bytes, bytearray)):
            return
        if bytes(old_v) == bytes(new_v):
            return
        fo = field_offsets.get(field)
        if not isinstance(fo, dict):
            raise ValueError(f"cannot encode edited field {field}: missing offsets")
        s = fo.get("start")
        e = fo.get("end")
        if not (isinstance(s, int) and isinstance(e, int) and 0 <= s <= e):
            raise ValueError(f"cannot encode edited field {field}: invalid offsets")
        rec_off = ni.get("offset")
        if not isinstance(rec_off, int):
            raise ValueError("record missing offset")
        ls = s - rec_off
        le = e - rec_off
        if ls < 0 or le < ls or le > len(buf):
            raise ValueError(f"cannot encode edited field {field}: offset out of record")
        nb = bytes(new_v)
        if len(nb) != (le - ls):
            raise ValueError(
                f"edited field {field} changes record length ({len(nb)} != {le - ls}); "
                "variable-length record rewrite not implemented yet"
            )
        buf[ls:le] = nb

    if codec == "Part.v1":
        _patch("Name", "len16be+utf16be")
        _patch("RefDes", "len16be+utf16be")
        _patch("Value", "len16be+utf16be")
        _patch("BaseRefDes", "len16be+utf16be")
        _patch("BaseName", "len16be+utf16be")
    elif codec == "Pattern.v1":
        _patch("Name", "len16le+utf16le_noterm")
    else:
        for k in list(fields.keys()):
            _patch_unknown_bin(str(k))

    return bytes(buf)


def make_bin_segment(
    *,
    name: str,
    role: str,
    start: int,
    end: int,
    data: bytes,
) -> dict[str, Any]:
    seg = data[start:end] if 0 <= start <= end <= len(data) else b""
    return {
        "Name": name,
        "Role": role,
        "Kind": "bin",
        "Start": int(start),
        "End": int(end),
        "Length": int(max(0, end - start)),
        "Value": bytearray(seg),
    }


def make_text_segment(
    *,
    name: str,
    role: str,
    start: int,
    end: int,
    encoding: str,
    value: str,
) -> dict[str, Any]:
    return {
        "Name": name,
        "Role": role,
        "Kind": "text",
        "Start": int(start),
        "End": int(end),
        "Length": int(max(0, end - start)),
        "Encoding": encoding,
        "Value": str(value),
    }


def encode_segments(segments: list[dict[str, Any]]) -> bytes:
    out = bytearray()
    for s in segments:
        kind = str(s.get("Kind", ""))
        if kind == "bin":
            v = s.get("Value")
            if isinstance(v, (bytes, bytearray)):
                out.extend(v)
                continue
            if isinstance(v, list):
                out.extend(bytearray(int(x) & 0xFF for x in v))
                continue
            raise ValueError("bin segment missing bytes-like Value")
            continue
        if kind == "text":
            enc = str(s.get("Encoding", ""))
            val = str(s.get("Value", ""))
            if enc == "len16be+utf16be":
                out.extend(encode_len16be_utf16be(val))
                continue
            raise ValueError(f"unsupported text encoding: {enc}")
        raise ValueError(f"unsupported segment kind: {kind}")
    return bytes(out)


def _decode_layout_segments(
    data: bytes,
    *,
    cursor: int,
    record_end: int,
    layout: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, dict[str, int]], int] | None:
    segments: list[dict[str, Any]] = []
    text_segments: list[dict[str, Any]] = []
    bin_segments: list[dict[str, Any]] = []
    fields_out: dict[str, Any] = {}
    field_offsets: dict[str, dict[str, int]] = {}

    for item in layout:
        if not isinstance(item, dict):
            return None
        name = str(item.get("name", ""))
        fmt = str(item.get("format", "")).strip()
        optional = bool(item.get("optional", False))
        if fmt == "" or name == "":
            return None
        role = ""

        if (fmt.startswith("bin[") and fmt.endswith("]")) or (fmt.startswith("header[") and fmt.endswith("]")):
            is_header = fmt.startswith("header[")
            ln_token = fmt[7:-1].strip() if is_header else fmt[4:-1].strip()
            seg_role = "header" if is_header else (role or "unknown_blob")
            if ln_token == "*":
                seg = make_bin_segment(name=name, role=seg_role, start=cursor, end=record_end, data=data)
                cursor = int(record_end)
            else:
                try:
                    n = int(ln_token)
                except (TypeError, ValueError):
                    return None
                if n < 0 or cursor + n > record_end:
                    return None
                seg = make_bin_segment(name=name, role=seg_role, start=cursor, end=cursor + n, data=data)
                cursor += n
            segments.append(seg)
            bin_segments.append(seg)
            field_offsets[name] = {"start": int(seg.get("Start", 0)), "end": int(seg.get("End", 0))}
            v = seg.get("Value")
            if isinstance(v, (bytes, bytearray)):
                fields_out[name] = bytearray(v)
            continue

        if fmt in ("len16be_utf16be", "len16le_utf16le", "len16le_utf16le_noterm"):
            if fmt == "len16be_utf16be":
                d = decode_len16be_utf16be(data, cursor, max_chars=256)
                enc = "len16be+utf16be"
            elif fmt == "len16le_utf16le":
                d = decode_len16le_utf16le(data, cursor, max_chars=256)
                enc = "len16le+utf16le"
            else:
                d = decode_len16le_utf16le_noterm(data, cursor, max_chars=256)
                enc = "len16le+utf16le_noterm"
            if d is None:
                if optional:
                    continue
                return None
            t, s0, s1 = d
            if s0 != cursor:
                if optional:
                    continue
                return None
            seg = make_text_segment(name=name, role=role or "text", start=s0, end=s1, encoding=enc, value=t)
            cursor = s1
            segments.append(seg)
            text_segments.append(seg)
            fields_out[name] = t
            field_offsets[name] = {"start": int(s0), "end": int(s1)}
            continue

        if _primitive_size(fmt) is not None:
            sz = _primitive_size(fmt)
            if sz is None or cursor + sz > record_end:
                if optional:
                    continue
                return None
            rv = decode_raw_primitive(data, cursor, fmt)
            if rv is None:
                if optional:
                    continue
                return None
            fields_out[name] = rv
            field_offsets[name] = {"start": int(cursor), "end": int(cursor + int(rv["size"]))}
            cursor += int(rv["size"])
            continue

        if optional:
            continue
        return None

    return segments, text_segments, bin_segments, fields_out, field_offsets, cursor


def build_segment_plan(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for s in segments:
        plan.append(
            {
                "Name": s.get("Name", ""),
                "Role": s.get("Role", ""),
                "Kind": s.get("Kind", ""),
                "Start": int(s.get("Start", 0)),
                "End": int(s.get("End", 0)),
                "Length": int(s.get("Length", 0)),
                "Encoding": str(s.get("Encoding", "")),
            }
        )
    plan.sort(key=lambda x: int(x["Start"]))
    return plan


def _apply_header_checks(
    data: bytes,
    *,
    record_start: int,
    record_end: int,
    header: dict[str, Any] | None,
) -> int | None:
    if not isinstance(header, dict):
        return int(record_start)
    checks = header.get("checks", [])
    if isinstance(checks, list):
        for chk in checks:
            if not isinstance(chk, dict):
                return None
            try:
                off = int(chk.get("offset", 0))
            except (TypeError, ValueError):
                return None
            hx = str(chk.get("hex", "")).strip()
            if hx == "":
                return None
            b = bytes.fromhex(hx)
            s = record_start + off
            e = s + len(b)
            if s < record_start or e > record_end or e > len(data):
                return None
            if data[s:e] != b:
                return None
    try:
        payload_offset = int(header.get("payload_offset", 0))
    except (TypeError, ValueError):
        return None
    cursor = record_start + payload_offset
    if cursor < record_start or cursor > record_end:
        return None
    return int(cursor)


def _apply_derived_fields(
    data: bytes,
    *,
    record_start: int,
    record_end: int,
    derived_fields: list[dict[str, Any]] | Any,
    fields_out: dict[str, Any],
) -> bool:
    if not isinstance(derived_fields, list):
        return True
    for rule in derived_fields:
        if not isinstance(rule, dict):
            return False
        name = str(rule.get("name", "")).strip()
        op = str(rule.get("op", "")).strip()
        if name == "" or op == "":
            return False

        if op == "u8_at_record_offset":
            try:
                off = int(rule.get("offset", 0))
            except (TypeError, ValueError):
                return False
            p = record_start + off
            if p < record_start or p >= record_end or p >= len(data):
                return False
            fields_out[name] = int(data[p])
            continue

        if op == "u8_from_bin_field":
            src = str(rule.get("source", "")).strip()
            if src == "":
                return False
            try:
                idx = int(rule.get("index", 0))
            except (TypeError, ValueError):
                return False
            bv = fields_out.get(src)
            if not isinstance(bv, (bytes, bytearray)):
                return False
            if idx < 0 or idx >= len(bv):
                return False
            fields_out[name] = int(bv[idx])
            continue

        if op == "count_internal_record_starts":
            fields_out[name] = int(_count_internal_record_starts(data, record_start, record_end))
            continue

        if op == "map_eq":
            src = str(rule.get("source", ""))
            sv = fields_out.get(src)
            out = rule.get("default")
            matches = rule.get("matches", [])
            if isinstance(matches, list):
                for m in matches:
                    if not isinstance(m, dict):
                        continue
                    if sv == m.get("value"):
                        out = m.get("result")
                        break
            fields_out[name] = out
            continue

        if op == "container_role_from_metrics":
            raw_type = fields_out.get("RawTypeByte")
            internal_count = fields_out.get("InternalRecordStartCount")
            if not isinstance(raw_type, int) or not isinstance(internal_count, int):
                return False
            if internal_count > 0:
                role = "RecordListContainer"
            elif raw_type == 0x40:
                role = "EntryLeafContainer"
            elif raw_type not in (0x00, 0x0C, 0x01):
                role = "ControlContainer"
            else:
                role = "LeafContainer"
            fields_out[name] = role
            continue

        return False
    return True


def _decode_layout_record_by_descriptor(
    data: bytes,
    *,
    record_index: int,
    record_start: int,
    record_end: int,
    descriptor: dict[str, Any],
) -> dict[str, Any] | None:
    layout = descriptor.get("layout")
    if not isinstance(layout, list):
        return None

    cursor0 = _apply_header_checks(
        data,
        record_start=int(record_start),
        record_end=int(record_end),
        header=descriptor.get("header") if isinstance(descriptor.get("header"), dict) else None,
    )
    if cursor0 is None:
        return None
    dec = _decode_layout_segments(data, cursor=int(cursor0), record_end=int(record_end), layout=layout)
    if dec is None:
        return None
    segments, text_segments, bin_segments, fields_out, field_offsets, cursor = dec

    if not _apply_derived_fields(
        data,
        record_start=int(record_start),
        record_end=int(record_end),
        derived_fields=descriptor.get("derived_fields"),
        fields_out=fields_out,
    ):
        return None

    consumed_end = cursor
    required_non_empty = descriptor.get("required_non_empty_fields", [])
    if isinstance(required_non_empty, list):
        for fn in required_non_empty:
            key = str(fn)
            v = fields_out.get(key, "")
            if not isinstance(v, str) or v == "":
                return None

    required_eq = descriptor.get("required_field_equals", {})
    if isinstance(required_eq, dict):
        for k, expected in required_eq.items():
            if fields_out.get(str(k)) != expected:
                return None

    segment_plan = build_segment_plan(segments)

    out = {
        "RecordType": str(descriptor.get("record_type", "Part")),
        "Descriptor": str(descriptor.get("descriptor", "Part.v1")),
        "RecordIndex": int(record_index),
        "Offset": int(record_start),
        "EndOffset": int(record_end),
        "Length": int(record_end - record_start),
        "ConsumedEndOffset": int(consumed_end),
        "ConsumedLength": int(consumed_end - record_start),
        "FieldOffsets": field_offsets,
        "Fields": fields_out,
        "FieldDescriptors": text_segments,
        "BinaryDescriptors": bin_segments,
        "Segments": segments,
        "SegmentPlan": segment_plan,
    }
    _decode_embedded_subrecords(
        data,
        descriptor=descriptor,
        record=out,
    )
    _apply_descriptor_semantics(out, descriptor)
    return out


def _decode_embedded_subrecords(
    data: bytes,
    *,
    descriptor: dict[str, Any],
    record: dict[str, Any],
) -> None:
    subs = descriptor.get("subrecords")
    if not isinstance(subs, list) or not subs:
        return
    rec_idx = int(record.get("RecordIndex", 0))
    rec_start = int(record.get("Offset", 0))
    rec_end = int(record.get("EndOffset", rec_start))
    parent_fields = record.get("Fields", {}) if isinstance(record.get("Fields"), dict) else {}
    out_children: list[dict[str, Any]] = []

    def _scan_stream(records_cfg: list[dict[str, Any]]) -> None:
        found: list[dict[str, Any]] = []
        marker_pos_map: dict[bytes, list[int]] = {}

        def _marker_positions(mk: bytes) -> list[int]:
            cached = marker_pos_map.get(mk)
            if cached is not None:
                return cached
            pos: list[int] = []
            p = rec_start + 12
            while p + len(mk) <= rec_end:
                i = data.find(mk, p, rec_end)
                if i < 0:
                    break
                pos.append(int(i))
                p = i + 1
            marker_pos_map[mk] = pos
            return pos

        for cfg in records_cfg:
            if not isinstance(cfg, dict):
                continue
            dsc = cfg.get("descriptor")
            if not isinstance(dsc, dict):
                continue
            cfg_name = str(cfg.get("name", "")).strip()
            cfg_id = str(cfg.get("id", "")).strip().lower()
            marker_choices: list[bytes] = []
            if cfg_id.startswith("marker:"):
                for tok in cfg_id.split(":", 1)[1].split("|"):
                    h = tok.strip()
                    if h == "":
                        continue
                    try:
                        marker_choices.append(bytes.fromhex(h))
                    except ValueError:
                        continue
            if not marker_choices:
                continue
            try:
                start_offset = int(cfg.get("start_offset", 0))
            except (TypeError, ValueError):
                start_offset = 0
            match_field = str(cfg.get("match_field", "Text")).strip()
            match_parent_field = str(cfg.get("match_parent_field", "")).strip()
            parent_match_val = ""
            require_parent_match = False
            if match_parent_field != "":
                require_parent_match = True
                pv = parent_fields.get(match_parent_field, "")
                parent_match_val = str(pv).strip() if isinstance(pv, str) else str(pv)
                if parent_match_val == "":
                    # Deterministic rule: configured parent match with empty parent value means no match.
                    continue
            try:
                max_matches = int(cfg.get("max_matches", 0))
            except (TypeError, ValueError):
                max_matches = 0
            cfg_hits = 0
            scan_end = rec_end
            scan_end_marker = str(cfg.get("scan_end_marker", "")).strip().lower()
            if scan_end_marker:
                for tok in scan_end_marker.split("|"):
                    h = tok.strip()
                    if h == "":
                        continue
                    try:
                        mk2 = bytes.fromhex(h)
                    except ValueError:
                        continue
                    pos_list = _marker_positions(mk2)
                    if pos_list:
                        first_pos = int(pos_list[0])
                        if first_pos < scan_end:
                            scan_end = first_pos
            if scan_end <= rec_start + 12:
                continue

            for mk in marker_choices:
                for i in _marker_positions(mk):
                    if i >= scan_end:
                        break
                    rec_start_i = i + start_offset
                    if rec_start_i < rec_start or rec_start_i >= rec_end:
                        continue
                    decode_end = int(scan_end) if scan_end > rec_start else rec_end
                    got = decode_record_by_descriptor(
                        data,
                        record_index=0,
                        record_start=rec_start_i,
                        record_end=decode_end,
                        descriptor=dsc,
                    )
                    if got is None:
                        continue
                    consumed_end = got.get("ConsumedEndOffset")
                    if not isinstance(consumed_end, int):
                        continue
                    if consumed_end <= rec_start_i or consumed_end > rec_end:
                        continue
                    child_start = int(got.get("Offset", rec_start_i))
                    if child_start < rec_start_i:
                        child_start = rec_start_i
                    if consumed_end <= child_start:
                        continue
                    if require_parent_match:
                        fields = got.get("Fields", {})
                        if not isinstance(fields, dict):
                            continue
                        v = str(fields.get(match_field, "")).strip()
                        if v != parent_match_val:
                            continue
                    rec_out = dict(got)
                    if cfg_name != "":
                        rec_out["RecordType"] = cfg_name
                    rec_out["ParentRecordIndex"] = int(rec_idx)
                    rec_out["Offset"] = int(child_start)
                    rec_out["EndOffset"] = int(consumed_end)
                    rec_out["Length"] = int(consumed_end - child_start)
                    rec_out["ConsumedLength"] = int(consumed_end - child_start)
                    found.append(rec_out)
                    cfg_hits += 1
                    if max_matches > 0 and cfg_hits >= max_matches:
                        break
                if max_matches > 0 and cfg_hits >= max_matches:
                    break

        if not found:
            return
        found.sort(key=lambda r: (int(r.get("Offset", 0)), int(r.get("EndOffset", 0))))
        dedup: list[dict[str, Any]] = []
        seen: set[tuple[int, int, str]] = set()
        for r in found:
            k = (int(r.get("Offset", 0)), int(r.get("EndOffset", 0)), str(r.get("RecordType", "")))
            if k in seen:
                continue
            seen.add(k)
            dedup.append(r)
        for idx, r in enumerate(dedup):
            r["RecordIndex"] = int(idx)
            out_children.append(r)

    for rule in subs:
        if not isinstance(rule, dict):
            continue
        op = str(rule.get("op", "")).strip()
        if op == "scan_subrecords":
            records = rule.get("records")
            if isinstance(records, list):
                _scan_stream([r for r in records if isinstance(r, dict)])
            continue
    if out_children:
        record["Children"] = out_children


def _count_internal_record_starts(data: bytes, start: int, end: int) -> int:
    c = 0
    p = start + 1
    lim = min(end, len(data))
    while p + 6 < lim:
        x = data[p]
        if (
            x not in (0x00, 0x0C)
            and data[p + 1] == 0x01
            and data[p + 2] in (0x00, 0x01)
            and data[p + 3] == 0x0F
            and data[p + 4] == 0x42
        ):
            c += 1
        p += 1
    return c


def _pattern_prelude_matches(pre: bytes, rule: str) -> bool:
    if len(pre) != 12:
        return False
    if rule.startswith("0f 42 [40|41|42]"):
        return (
            pre[0] == 0x0F
            and pre[1] == 0x42
            and pre[2] in (0x40, 0x41, 0x42)
            and pre[3] in (0x00, 0x01)
            and pre[4] == 0x3B
            and pre[8] == 0x3B
        )
    if rule.startswith("0f 42 [42|44]"):
        return (
            pre[0] == 0x0F
            and pre[1] == 0x42
            and pre[2] in (0x42, 0x44)
            and pre[3] == 0x01
            and pre[4] == 0x3B
            and pre[8] == 0x3B
        )
    return False


def decode_pattern_extension_by_descriptor(
    data: bytes,
    *,
    record_index: int,
    record_start: int,
    record_end: int,
    descriptor: dict[str, Any],
) -> dict[str, Any] | None:
    scan = descriptor.get("scan", {})
    if not isinstance(scan, dict):
        return None
    anchored = bool(scan.get("anchored", False))
    signatures = scan.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        signatures = [scan]

    def _try_at_pos(p: int) -> dict[str, Any] | None:
        if p < record_start + 12 or p >= record_end:
            return None
        pre = data[p - 12 : p]
        for sig in signatures:
            if not isinstance(sig, dict):
                continue
            rule = str(sig.get("prelude_rule", ""))
            marker_hex = str(sig.get("marker_hex", ""))
            m = bytes.fromhex(marker_hex) if marker_hex else b""
            if not m:
                continue
            if p + len(m) > record_end:
                continue
            if not _pattern_prelude_matches(pre, rule):
                continue
            if data[p : p + len(m)] != m:
                continue
            d = decode_len16le_utf16le_noterm(data, p + len(m), max_chars=256)
            if d is None:
                d = decode_len16le_utf16le(data, p + len(m), max_chars=256)
            if d is None:
                continue
            name, s0, s1 = d
            out = {
                "RecordType": str(descriptor.get("record_type", "PatternExtension")),
                "Descriptor": str(descriptor.get("descriptor", "PatternExtension.v1")),
                "RecordIndex": int(record_index),
                "Offset": int(record_start),
                "EndOffset": int(record_end),
                "Length": int(record_end - record_start),
                "ConsumedEndOffset": int(s1),
                "ConsumedLength": int(s1 - record_start),
                "FieldOffsets": {"Name": {"start": int(s0), "end": int(s1)}},
                "Fields": {"Name": name},
            }
            _apply_descriptor_semantics(out, descriptor)
            return out
        return None

    if anchored:
        got = _try_at_pos(record_start + 12)
        if got is not None:
            return got
        return None

    p = record_start + 12
    while p + 6 < record_end:
        got = _try_at_pos(p)
        if got is not None:
            return got
        p += 1
    return None


def decode_cache_entry_by_descriptor(
    data: bytes,
    *,
    record_index: int,
    record_start: int,
    record_end: int,
    descriptor: dict[str, Any],
) -> dict[str, Any] | None:
    s = int(record_start)
    e = int(record_end)
    if s < 0 or e <= s or e > len(data):
        return None
    m40: list[str] = []
    m41: list[str] = []
    marker_fields: list[dict[str, Any]] = []
    i = s
    while i + 6 <= e:
        if data[i] == 0x0F and data[i + 1] == 0x42 and data[i + 2] in (0x40, 0x41):
            mk = int(data[i + 2])
            d = decode_len16be_utf16be(data, i + 3, max_chars=256)
            if d is not None and d[1] == i + 3 and d[2] <= e:
                txt = str(d[0]).strip()
                if txt != "":
                    if mk == 0x40:
                        m40.append(txt)
                    else:
                        m41.append(txt)
                    marker_fields.append(
                        {
                            "marker": mk,
                            "text": txt,
                            "start": int(i),
                            "end": int(d[2]),
                        }
                    )
                i = int(d[2])
                continue
        i += 1

    if not m40 and not m41:
        return None

    out = {
        "RecordType": str(descriptor.get("record_type", "CacheEntry")),
        "Descriptor": str(descriptor.get("descriptor", "CacheEntry.v1")),
        "RecordIndex": int(record_index),
        "Offset": int(record_start),
        "EndOffset": int(record_end),
        "Length": int(record_end - record_start),
        "ConsumedEndOffset": int(record_end),
        "ConsumedLength": int(record_end - record_start),
        "Fields": {
            "Marker40Strings": m40,
            "Marker41Strings": m41,
            "MarkerFields": marker_fields,
            # Canonical semantic aliases.
            "PatternCandidates": list(m40),
            "LinkTokens": list(m41),
        },
    }
    _apply_descriptor_semantics(out, descriptor)
    return out


def decode_record_by_descriptor(
    data: bytes,
    *,
    record_index: int,
    record_start: int,
    record_end: int,
    descriptor: dict[str, Any],
) -> dict[str, Any] | None:
    mode = str(descriptor.get("decode", "layout"))
    if mode == "layout":
        return _decode_layout_record_by_descriptor(
            data,
            record_index=record_index,
            record_start=record_start,
            record_end=record_end,
            descriptor=descriptor,
        )
    if mode == "scan_marker_strings":
        return decode_pattern_extension_by_descriptor(
            data,
            record_index=record_index,
            record_start=record_start,
            record_end=record_end,
            descriptor=descriptor,
        )
    if mode == "scan_cache_markers":
        return decode_cache_entry_by_descriptor(
            data,
            record_index=record_index,
            record_start=record_start,
            record_end=record_end,
            descriptor=descriptor,
        )
    return None


def _apply_descriptor_semantics(decoded: dict[str, Any], descriptor: dict[str, Any]) -> None:
    sem_rules = descriptor.get("semantics")
    fields = decoded.get("Fields")
    if not isinstance(sem_rules, list) or not isinstance(fields, dict):
        return
    semantics: list[dict[str, Any]] = []
    for r in sem_rules:
        if not isinstance(r, dict):
            continue
        key = str(r.get("key", "")).strip()
        field = str(r.get("field", "")).strip()
        if key == "" or field == "":
            continue
        try:
            prio = int(r.get("priority", 0))
        except (TypeError, ValueError):
            prio = 0
        raw = fields.get(field)
        if isinstance(raw, str):
            val = raw.strip()
            if val != "":
                semantics.append({"key": key, "value": val, "priority": prio})
        elif isinstance(raw, list):
            for it in raw:
                if not isinstance(it, str):
                    continue
                val = it.strip()
                if val != "":
                    semantics.append({"key": key, "value": val, "priority": prio})
    if semantics:
        decoded["Semantics"] = semantics
