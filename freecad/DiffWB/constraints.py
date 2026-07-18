# SPDX-License-Identifier: MIT
"""Legible serialization of Sketcher geometry + constraints -- the part no
existing FreeCAD AI/MCP tool publishes as a documented format.

Pure (no Gui), so it is exercised headlessly. Everything here turns
FreeCAD's index-based constraint graph (GeoId + PointPos ints) into a
self-describing JSON form: geometry referenced by index + a named role
("start"/"end"/"center"/...), so an LLM/agent can read the constraint
network without knowing FreeCAD's internal enum values.
"""

# Sketcher PointPos enum -> readable role.
_POINT_POS = {0: None, 1: "start", 2: "end", 3: "center"}

# Special negative GeoIds used by Sketcher for the sketch's own axes/origin.
_SPECIAL_GEO = {-1: "x_axis", -2: "y_axis", -3: "origin", -4: "b_spline_pole"}

# GeoId sentinel meaning "this slot is unused".
_GEO_UNDEF = -2000


def point_pos_name(pos):
    return _POINT_POS.get(pos, None)


def geo_ref(geo_id, pos):
    """Return a JSON-legible reference to a geometry element (or one of its
    points), or None if the slot is unused. ``geo_id`` is a Sketcher GeoId;
    ``pos`` a PointPos int."""
    if geo_id is None or geo_id <= _GEO_UNDEF:
        return None
    ref = {}
    if geo_id < 0:
        ref["element"] = _SPECIAL_GEO.get(geo_id, "special%d" % geo_id)
    else:
        ref["geometry"] = geo_id
    role = point_pos_name(pos)
    if role:
        ref["point"] = role
    return ref


def serialize_geometry(geo, construction=False):
    """Serialize one Part geometry element from a sketch to a compact dict.
    Unknown geometry types fall back to their class name + no coordinates."""
    kind = geo.__class__.__name__
    out = {"type": _GEOM_NAMES.get(kind, kind)}
    if construction:
        out["construction"] = True
    try:
        if kind == "LineSegment":
            out["start"] = _xy(geo.StartPoint)
            out["end"] = _xy(geo.EndPoint)
        elif kind == "Circle":
            out["center"] = _xy(geo.Center)
            out["radius"] = round(geo.Radius, 6)
        elif kind == "ArcOfCircle":
            out["center"] = _xy(geo.Center)
            out["radius"] = round(geo.Radius, 6)
            out["start"] = _xy(geo.StartPoint)
            out["end"] = _xy(geo.EndPoint)
        elif kind == "Point":
            out["at"] = _xy(geo.X if hasattr(geo, "X") else geo)
        elif kind in ("Ellipse", "ArcOfEllipse"):
            out["center"] = _xy(geo.Center)
    except Exception:
        pass
    return out


_GEOM_NAMES = {
    "LineSegment": "line",
    "Circle": "circle",
    "ArcOfCircle": "arc",
    "Point": "point",
    "Ellipse": "ellipse",
    "ArcOfEllipse": "arc_ellipse",
    "BSplineCurve": "bspline",
}


def _xy(v):
    try:
        return [round(v.x, 6), round(v.y, 6)]
    except Exception:
        return None


def serialize_constraint(c):
    """Serialize one Sketcher.Constraint into a self-describing dict:
    ``type`` (Coincident/Horizontal/Distance/...), the geometry references
    it relates (only the used slots), an optional dimensional ``value``
    (degrees for Angle constraints; FreeCAD stores radians internally), the
    user ``name`` if the constraint was named, and ``driving: false`` for
    reference (non-driving) dimensions."""
    import math
    out = {"type": c.Type}
    refs = []
    for gid, pos in ((c.First, c.FirstPos), (c.Second, c.SecondPos), (c.Third, c.ThirdPos)):
        r = geo_ref(gid, pos)
        if r is not None:
            refs.append(r)
    if refs:
        out["refs"] = refs
    # Dimensional constraints carry a value; flag which are driving vs. the
    # geometric ones (value stays 0 and is meaningless for those).
    if c.Type in _DIMENSIONAL:
        value = c.Value
        if c.Type == "Angle":
            value = math.degrees(value)
        out["value"] = round(value, 6)
        out["dimensional"] = True
    if getattr(c, "Driving", True) is False:
        out["driving"] = False
    if getattr(c, "Name", ""):
        out["name"] = c.Name
    return out


_DIMENSIONAL = {
    "Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle",
    "SnellsLaw", "Weight",
}
