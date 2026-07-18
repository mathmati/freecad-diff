# SPDX-License-Identifier: MIT
"""Volumetric geometry diff: the actual material added and removed between two
versions, via boolean operations (requested in FreeCAD issue #12534, and done
by SolidWorks Compare Geometry, but by no FreeCAD tool).

We fuse every solid on each side into one shape, then:

    added   = new - old   (material present only in the new version)
    removed = old - new    (material present only in the old version)
    common  = old & new    (shared material)

and report the volumes. Booleans on real geometry can fail or be slow, so the
whole thing is defensive: a failure returns ``ok=False`` with a reason rather
than raising, and the caller simply omits the volume section.

Pure geometry, no diff/semantic coupling; the caller passes the ``{id: Shape}``
dicts it already loaded for the visual overlay.
"""


def _solids(shapes, ids=None):
    """All solid bodies across a ``{id: Part.Shape}`` dict (skip wires, faces,
    sketches and null/zero-volume shapes). If ``ids`` is given, only those
    object ids are used -- important so intermediate PartDesign features (a Pad
    whose result the Body already contains) are not double-counted, which would
    fuse a pocket back closed and hide the change."""
    out = []
    for oid, s in (shapes or {}).items():
        if ids is not None and oid not in ids:
            continue
        try:
            if s is None or s.isNull():
                continue
            if getattr(s, "Solids", None):
                out.extend(s.Solids)
            elif getattr(s, "Volume", 0.0) > 1e-9:
                out.append(s)
        except Exception:
            continue
    return out


def _fuse(solids):
    """Union a list of solids into one shape (None if the list is empty)."""
    if not solids:
        return None
    fused = solids[0]
    for s in solids[1:]:
        try:
            fused = fused.fuse(s)
        except Exception as exc:  # noqa: BLE001
            # skip a solid that refuses to fuse rather than aborting the
            # whole comparison, but say so: a silently dropped solid means
            # the reported volumes undercount
            import sys
            sys.stderr.write("volumediff: skipped a solid that failed to "
                             "fuse (%s); volumes may undercount\n" % exc)
            continue
    return fused


def material_delta(old_shapes, new_shapes, old_ids=None, new_ids=None):
    """Return a dict describing the material added/removed between the two
    shape sets. Keys: ok, old_volume, new_volume, added_volume,
    removed_volume, common_volume, net_volume, and (for rendering)
    added_shape / removed_shape (Part.Shape or None). On failure: ok=False
    plus ``error``.

    ``old_ids``/``new_ids``: restrict fusing to these object ids (the top-level
    shape carriers), so intermediate features are not double-counted."""
    old_f = _fuse(_solids(old_shapes, old_ids))
    new_f = _fuse(_solids(new_shapes, new_ids))
    res = {"ok": True, "added_shape": None, "removed_shape": None,
           "old_volume": 0.0, "new_volume": 0.0, "added_volume": 0.0,
           "removed_volume": 0.0, "common_volume": 0.0, "net_volume": 0.0}
    try:
        res["old_volume"] = float(old_f.Volume) if old_f else 0.0
        res["new_volume"] = float(new_f.Volume) if new_f else 0.0
        if old_f is None and new_f is None:
            return res
        if old_f is None:
            res["added_shape"] = new_f
            res["added_volume"] = res["new_volume"]
        elif new_f is None:
            res["removed_shape"] = old_f
            res["removed_volume"] = res["old_volume"]
        else:
            added = new_f.cut(old_f)
            removed = old_f.cut(new_f)
            common = old_f.common(new_f)
            res["added_shape"] = added if not added.isNull() else None
            res["removed_shape"] = removed if not removed.isNull() else None
            res["added_volume"] = float(added.Volume)
            res["removed_volume"] = float(removed.Volume)
            res["common_volume"] = float(common.Volume)
        res["net_volume"] = res["new_volume"] - res["old_volume"]
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return res


def _num(x):
    x = round(float(x), 3)
    if x == int(x):
        return "{:,}".format(int(x))
    return "{:,.3f}".format(x).rstrip("0").rstrip(".")


def _fmt_vol(mm3):
    """Human volume string: mm3, with a cm3 form for larger values."""
    if mm3 >= 1000.0:
        return "%s cm3 (%s mm3)" % (_num(mm3 / 1000.0), _num(mm3))
    return "%s mm3" % _num(mm3)


def volume_summary(delta):
    """Numbers-only dict for the diff output (no Shape objects, so it stays
    JSON-serializable)."""
    if not delta or not delta.get("ok"):
        return None
    return {
        "added_volume": delta["added_volume"],
        "removed_volume": delta["removed_volume"],
        "common_volume": delta["common_volume"],
        "old_volume": delta["old_volume"],
        "new_volume": delta["new_volume"],
        "net_volume": delta["net_volume"],
        "added_text": _fmt_vol(delta["added_volume"]),
        "removed_text": _fmt_vol(delta["removed_volume"]),
        "net_text": ("+" if delta["net_volume"] >= 0 else "-")
                    + _fmt_vol(abs(delta["net_volume"])),
    }
