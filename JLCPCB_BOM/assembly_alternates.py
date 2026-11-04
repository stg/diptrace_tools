"""Shared assembly alternatives: reversible BOM overrides and local CPL transforms."""
from copy import deepcopy
import math
import re

COMPONENT_FIELDS = ("Name", "Value", "Pattern", "LCSC Part #", "Notes")


def groups_for(project, ref):
    return [g for g in getattr(project, "assembly_groups", []) if ref in g["references"]]


def active_group(project, ref):
    return next((g for g in groups_for(project, ref) if g.get("active", False)), None)


def import_target(project, ref, present, code):
    """Infer an import's target from reference coverage, never from active state alone."""
    groups = groups_for(project, ref)
    if any(set(g["references"]) <= present for g in groups):
        return None, False
    candidates = [g for g in groups if g["anchor"] == ref and
                  set(g["references"]).intersection(present) == {ref}]
    matched = [g for g in candidates if g["component"].get("LCSC Part #", "").upper() == code]
    if len(matched) == 1:
        return matched[0], False
    if len(candidates) == 1:
        return candidates[0], False
    if candidates:
        return None, True
    # Partial coverage containing an anchor could be an incomplete combined export.
    partial = any(g["anchor"] in present and 1 < len(set(g["references"]) & present) < len(g["references"])
                  for g in groups)
    return None, partial


def validate_groups(groups, records):
    if not isinstance(groups, list):
        raise ValueError("assembly_alternatives must be a list.")
    ids, occupied = set(), set()
    for g in groups:
        if not isinstance(g, dict) or not isinstance(g.get("id"), str) or not g["id"]:
            raise ValueError("Each combined alternative needs an id.")
        if g["id"] in ids:
            raise ValueError("Duplicate combined alternative id: " + g["id"])
        ids.add(g["id"])
        refs = g.get("references")
        if (not isinstance(refs, list) or len(refs) < 2 or
                any(not isinstance(r, str) or r not in records for r in refs) or len(set(refs)) != len(refs)):
            raise ValueError(f"{g['id']}: include at least two distinct, existing references.")
        if g.get("anchor") not in refs:
            raise ValueError(f"{g['id']}: output/anchor reference must belong to the group.")
        if type(g.get("active", False)) is not bool:
            raise ValueError(f"{g['id']}: active must be true or false.")
        component = g.get("component")
        if not isinstance(component, dict) or any(not isinstance(component.get(f, ""), str) for f in COMPONENT_FIELDS):
            raise ValueError(f"{g['id']}: invalid replacement component.")
        if not component.get("Name") or not component.get("Pattern"):
            raise ValueError(f"{g['id']}: replacement needs a name and footprint.")
        code = component.get("LCSC Part #", "")
        if code and not re.fullmatch(r"C\d+", code):
            raise ValueError(f"{g['id']}: invalid LCSC number.")
        if not isinstance(g.get("placement", {}), dict):
            raise ValueError(f"{g['id']}: placement must be an object.")
        for field in ("x_mm", "y_mm", "rotation_deg"):
            value = g.get("placement", {}).get(field, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{g['id']}: {field} must be finite and numeric.")
        layout = g.get("layout")
        if not isinstance(layout, dict) or set(layout) != set(refs):
            raise ValueError(f"{g['id']}: placement verification is missing; edit and verify the group.")
        for ref, pose in layout.items():
            if (not isinstance(pose, list) or len(pose) != 3 or
                    any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in pose)):
                raise ValueError(f"{g['id']}: invalid verified position for {ref}.")
        if g.get("active", False):
            overlap = occupied.intersection(refs)
            if overlap:
                raise ValueError("Overlapping active combined alternatives: " + ", ".join(sorted(overlap)))
            occupied.update(refs)


def choose_group(groups, refs, group_id=None):
    """Return one atomic state change; ordinary component selections stay untouched."""
    result = deepcopy(groups)
    target = next((g for g in result if g["id"] == group_id), None) if group_id else None
    if group_id and target is None:
        raise ValueError("Unknown combined alternative: " + group_id)
    affected = set(target["references"] if target else refs)
    for g in result:
        if affected.intersection(g["references"]):
            g["active"] = bool(target and g["id"] == target["id"])
    return result


def placement_index(cpl):
    headers = [h.strip().casefold() for h in cpl[0]]
    def col(names):
        return next(i for i, h in enumerate(headers) if h in names)
    from bom_conversions import cpl_coordinate_column
    columns = (cpl_coordinate_column(cpl[0], "x"), cpl_coordinate_column(cpl[0], "y"),
               col({"rotation"}), col({"side", "layer"}))
    refcol = col({"refdes", "designator"})
    index = {}
    for row in cpl[1:]:
        if not any(str(v).strip() for v in row):
            continue
        ref = row[refcol].strip().upper()
        if ref in index:
            raise ValueError("Duplicate CPL placement: " + ref)
        index[ref] = row
    return index, columns


def local_layout(cpl, refs, anchor):
    index, (xc, yc, rc, sc) = placement_index(cpl)
    missing = set(refs) - set(index)
    if missing:
        raise ValueError("Combined alternative is missing CPL placements: " + ", ".join(sorted(missing)))
    for ref in refs:
        pose = index[ref]
        if len(pose) <= max(xc, yc, rc, sc):
            raise ValueError("Incomplete CPL placement: " + ref)
        if not all(math.isfinite(float(pose[i])) for i in (xc, yc, rc)):
            raise ValueError("Non-finite CPL placement: " + ref)
    a = index[anchor]
    angle = math.radians(float(a[rc])); co, si = math.cos(angle), math.sin(angle)
    result = {}
    for ref in refs:
        row = index[ref]
        if row[sc].strip().casefold() != a[sc].strip().casefold():
            raise ValueError("Combined components must be on the same PCB side: " + ref)
        dx, dy = float(row[xc]) - float(a[xc]), float(row[yc]) - float(a[yc])
        result[ref] = [round(dx*co + dy*si, 6), round(-dx*si + dy*co, 6),
                       round((float(row[rc]) - float(a[rc])) % 360, 6)]
    return result


def reanchor_placement(cpl, old_anchor, new_anchor, offsets):
    """Keep the replacement at its physical pose when its anchor changes."""
    relative = local_layout(cpl, [old_anchor, new_anchor], old_anchor)[new_anchor]
    dx = offsets.get("x_mm", 0) - relative[0]
    dy = offsets.get("y_mm", 0) - relative[1]
    angle = math.radians(relative[2]); co, si = math.cos(angle), math.sin(angle)
    return {"x_mm": dx*co + dy*si, "y_mm": -dx*si + dy*co,
            "rotation_deg": (offsets.get("rotation_deg", 0)-relative[2]+180) % 360-180}


def apply_groups_to_cpl(cpl, groups):
    """Transform normalized-mm source placements before footprint-wide corrections."""
    index, (xc, yc, rc, sc) = placement_index(cpl)
    removed, replacements = set(), {}
    for g in groups:
        if not g.get("active", False):
            continue
        layout = local_layout(cpl, g["references"], g["anchor"])
        for ref, actual in layout.items():
            expected = g["layout"][ref]
            rotation_error = abs((actual[2] - expected[2] + 180) % 360 - 180)
            if math.hypot(actual[0]-expected[0], actual[1]-expected[1]) > 0.05 or rotation_error > 0.01:
                raise ValueError(f"{g['id']}: {ref} moved relative to its group; edit and verify placement before generating.")
        row = index[g["anchor"]].copy()
        angle = math.radians(float(row[rc])); co, si = math.cos(angle), math.sin(angle)
        offsets = g.get("placement", {}); x, y = offsets.get("x_mm", 0), offsets.get("y_mm", 0)
        row[xc] = str(float(row[xc]) + x*co - y*si)
        row[yc] = str(float(row[yc]) + x*si + y*co)
        row[rc] = str((float(row[rc]) + offsets.get("rotation_deg", 0)) % 360)
        replacements[g["anchor"]] = row
        removed.update(set(g["references"]) - {g["anchor"]})
    refcol = next(i for i,h in enumerate(cpl[0]) if h.strip().casefold() in {"refdes", "designator"})
    result = [cpl[0]]
    for row in cpl[1:]:
        ref = row[refcol].strip().upper() if len(row) > refcol else ""
        if ref not in removed:
            result.append(replacements.get(ref, row))
    return result
