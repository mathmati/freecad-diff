# SPDX-License-Identifier: MIT
"""serialize_document(doc) -> a canonical, versioned, tool-agnostic dict
capturing a FreeCAD document's SEMANTIC model: the feature tree, each
feature's parametric inputs (with expressions), the Sketcher geometry WITH
its constraint graph, attachments, and assigned materials -- the grounding
context an LLM/agent/MCP tool needs, as a documented schema (see SCHEMA.md)
rather than a screenshot or an ad-hoc per-tool dump.

Pure FreeCAD (no Gui), so it runs under plain freecadcmd and is unit-tested
headlessly. ``to_markdown()`` renders the same model as compact,
LLM-legible text.
"""
import FreeCAD as App

from . import constraints as C

SCHEMA_NAME = "freecad-model-context"
SCHEMA_VERSION = "1.0"

# Only these non-link property TYPES are serialized as params; anything else
# (PropertySheet, raw geometry lists, expression engines, ...) is skipped
# rather than dumped as an opaque repr -- keeps the model semantic, not a
# property dump.
_KNOWN_SCALAR_TYPES = {
    "App::PropertyLength", "App::PropertyDistance", "App::PropertyAngle",
    "App::PropertyArea", "App::PropertyVolume", "App::PropertySpeed",
    "App::PropertyFloat", "App::PropertyFloatConstraint", "App::PropertyQuantity",
    "App::PropertyInteger", "App::PropertyIntegerConstraint", "App::PropertyPercent",
    "App::PropertyBool", "App::PropertyString", "App::PropertyEnumeration",
    "App::PropertyVector", "App::PropertyPlacement",
}
# Property statuses that mark a property as computed/internal, not a
# user-meaningful semantic input.
_SKIP_STATUS = {"Hidden", "Transient", "Output", "Immutable"}
# Attachment/attacher internals that are noise for a semantic summary
# (AttachmentSupport + MapMode are kept -- handled explicitly for sketches).
_NOISE_PROPS = {
    "ArcFitTolerance", "AttacherEngine", "AttacherType", "MapPathParameter",
    "MapReversed", "MakeInternals", "Exports", "ExternalGeo", "ExternalTypes",
    "AttachmentOffset", "MapMode", "AttachmentSupport",
}
_DEFAULT_MATERIAL_NAMES = {"", "Default", "None"}

_defaults_cache = {}
_scratch_doc_name = "_mc_scratch_defaults"


def _default_obj(type_id):
    """A throwaway object of ``type_id`` (cached) whose property values are
    the type's defaults, so we can skip params left at their default. None if
    the type can't be instantiated standalone."""
    if type_id in _defaults_cache:
        return _defaults_cache[type_id]
    obj = None
    try:
        doc = App.getDocument(_scratch_doc_name)
    except Exception:
        try:
            doc = App.newDocument(_scratch_doc_name, hidden=True)
        except Exception:
            doc = None
    if doc is not None:
        try:
            obj = doc.addObject(type_id, "probe")
        except Exception:
            obj = None
    _defaults_cache[type_id] = obj
    return obj

# Property NAMES never worth serializing (computed shapes, internals).
_SKIP_PROPS = {
    "Shape", "AddSubShape", "PreviewShape", "SuppressedShape", "Label2",
    "ShapeMaterial", "ExpressionEngine", "Visibility", "Geometry",
    "Constraints", "FullyConstrained",
}
# Property TYPES never worth serializing (computed geometry blobs).
_SKIP_TYPES = {"Part::PropertyPartShape"}

# Quantity property types -> unit label.
_UNIT_BY_TYPE = {
    "App::PropertyLength": "mm", "App::PropertyDistance": "mm",
    "App::PropertyAngle": "deg", "App::PropertyArea": "mm^2",
    "App::PropertyVolume": "mm^3", "App::PropertySpeed": "mm/s",
}
_LINK_TYPES = {
    "App::PropertyLink", "App::PropertyLinkGlobal", "App::PropertyLinkHidden",
    "App::PropertyLinkSub", "App::PropertyLinkSubList", "App::PropertyLinkList",
}
_DATUM_TYPES = {
    "App::Origin", "App::Line", "App::Plane", "App::Point",
    "PartDesign::Plane", "PartDesign::Line", "PartDesign::Point",
    "PartDesign::CoordinateSystem",
}


def _role(obj):
    t = obj.TypeId
    if t == "PartDesign::Body":
        return "body"
    if t == "Sketcher::SketchObject":
        return "sketch"
    if t in _DATUM_TYPES:
        return "datum"
    if t.startswith("PartDesign::"):
        return "feature"
    if t.startswith("Part::"):
        return "solid"
    if t.startswith("Spreadsheet::"):
        return "spreadsheet"
    return "object"


def _expr_map(obj):
    try:
        return {path: expr for path, expr in (obj.ExpressionEngine or [])}
    except Exception:
        return {}


def _link_value(val):
    """Normalize any Link* property value to {object, [sub]} refs."""
    def one(target, subs=None):
        if target is None:
            return None
        ref = {"object": target.Name}
        subs = [s for s in (subs or []) if s]
        if subs:
            ref["sub"] = subs
        return ref

    if val is None:
        return None
    if isinstance(val, tuple) and len(val) == 2:      # LinkSub: (obj, [subs])
        return one(val[0], val[1])
    if isinstance(val, list):                          # LinkList / LinkSubList
        out = []
        for item in val:
            if isinstance(item, tuple) and len(item) == 2:
                r = one(item[0], item[1])
            else:
                r = one(item)
            if r:
                out.append(r)
        return out or None
    return one(val)                                    # plain Link


def _scalar_value(obj, name, type_id, expr_map):
    """Serialize a non-link property to {value, [unit], [expression]} or a
    bare JSON scalar. Returns (kind, payload) where kind is 'param'."""
    val = getattr(obj, name)
    entry = {}
    if type_id in _UNIT_BY_TYPE:
        try:
            entry = {"value": round(val.Value, 6), "unit": _UNIT_BY_TYPE[type_id]}
        except Exception:
            entry = {"value": _plain(val)}
    elif type_id == "App::PropertyPlacement":
        pl = _placement(val)
        if pl is None:                     # identity placement -> not semantic
            return None
        entry = {"value": pl}
    elif type_id == "App::PropertyVector":
        entry = {"value": [round(val.x, 6), round(val.y, 6), round(val.z, 6)]}
    else:
        entry = {"value": _plain(val)}
    if name in expr_map:
        entry["expression"] = expr_map[name]
    return entry


def _plain(val):
    if isinstance(val, (bool, int, float, str)) or val is None:
        return val
    try:
        return round(float(val), 6)
    except Exception:
        return str(val)


def _approx(a, b, tol=1e-9):
    """Approximate equality, recursive over lists: floats compared within
    ``tol`` so a solver artifact like 0.9999999999 counts as its default 1.0
    (exact compare made default-elision flap between saves, which then read
    as a phantom param change in the diff)."""
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_approx(x, y, tol) for x, y in zip(a, b))
    return a == b


def _is_default(name, type_id, default_obj, entry):
    """True if ``entry``'s value equals the property's default (so it can be
    omitted). Conservative: keeps the param on any uncertainty."""
    if default_obj is None:
        return False
    try:
        if name not in default_obj.PropertiesList:
            return False
        dval = _scalar_value(default_obj, name, type_id, {})
    except Exception:
        return False
    return dval is not None and _approx(dval.get("value"), entry.get("value"))


def _placement(p):
    try:
        pos = p.Base
        ax = p.Rotation.Axis
        if (abs(pos.x) < 1e-9 and abs(pos.y) < 1e-9 and abs(pos.z) < 1e-9
                and abs(p.Rotation.Angle) < 1e-12):
            return None                    # identity: nothing semantic to say
        return {
            "position": [round(pos.x, 6), round(pos.y, 6), round(pos.z, 6)],
            "axis": [round(ax.x, 6), round(ax.y, 6), round(ax.z, 6)],
            "angle_deg": round(p.Rotation.Angle * 57.29577951308232, 6),
        }
    except Exception:
        return None


def _material(obj):
    m = getattr(obj, "ShapeMaterial", None)
    if m is None:
        return None
    name = getattr(m, "Name", "") or ""
    if name in _DEFAULT_MATERIAL_NAMES:
        return None
    out = {"name": name}
    for attr, key in (("LibraryName", "library"), ("UUID", "uuid")):
        v = getattr(m, attr, None)
        if v:
            out[key] = v
    props = getattr(m, "PhysicalProperties", None)
    if isinstance(props, dict):
        for k in ("Density", "YoungsModulus", "PoissonRatio", "UltimateTensileStrength"):
            if k in props and props[k] not in (None, ""):
                out.setdefault("physical", {})[k] = str(props[k])
    return out


def _serialize_sketch(sk):
    geom = []
    try:
        for g in sk.Geometry:
            geom.append(C.serialize_geometry(g, getattr(g, "Construction", False)))
    except Exception:
        pass
    cons = []
    try:
        for c in sk.Constraints:
            cons.append(C.serialize_constraint(c))
    except Exception:
        pass
    out = {"geometry": geom, "constraints": cons}
    try:
        sup = _link_value(sk.AttachmentSupport)
        if sup:
            out["support"] = sup
        if getattr(sk, "MapMode", "") and sk.MapMode != "Deactivated":
            out["map_mode"] = sk.MapMode
    except Exception:
        pass
    return out


def _serialize_object(obj):
    role = _role(obj)
    node = {"id": obj.Name, "label": obj.Label, "type": obj.TypeId, "role": role}

    # Datum/origin scaffolding: keep it minimal (just identity) so it does
    # not drown the user-meaningful features.
    if role == "datum":
        return node

    expr_map = _expr_map(obj)
    default_obj = _default_obj(obj.TypeId)
    params, links = {}, {}
    for name in obj.PropertiesList:
        if (name in _SKIP_PROPS or name in _NOISE_PROPS
                or name == "Label" or name.startswith("_")):
            continue
        # PartDesign profile features ignore Direction unless UseCustomVector
        # is on, and FreeCAD stores either the computed normal or the
        # constructor default there depending on save state -- pure noise
        # unless the user actually enabled the custom vector.
        if name == "Direction" and getattr(obj, "UseCustomVector", None) is False:
            continue
        try:
            type_id = obj.getTypeIdOfProperty(name)
            status = set(obj.getPropertyStatus(name))
        except Exception:
            continue
        if type_id in _SKIP_TYPES or (status & _SKIP_STATUS):
            continue
        if type_id in _LINK_TYPES:
            lv = _link_value(getattr(obj, name))
            if lv:
                links[name] = lv
            continue
        if type_id not in _KNOWN_SCALAR_TYPES:
            continue
        try:
            entry = _scalar_value(obj, name, type_id, expr_map)
        except Exception:
            continue
        if entry is None:
            continue
        # Skip params left at the type's default (unless an expression drives
        # them) -- keeps the model to the values a user actually set.
        if "expression" not in entry and _is_default(name, type_id, default_obj, entry):
            continue
        params[name] = entry
    if params:
        node["params"] = params
    if links:
        node["links"] = links

    if role == "body":
        try:
            node["features"] = [f.Name for f in obj.Group]
            if obj.Tip is not None:
                node["tip"] = obj.Tip.Name
        except Exception:
            pass
    if role == "sketch":
        node["sketch"] = _serialize_sketch(obj)

    mat = _material(obj)
    if mat:
        node["material"] = mat
    return node


def _close_scratch():
    """Close the hidden defaults-probe document and drop its cached objects
    (they die with it) so serialization leaves no stray document behind."""
    global _defaults_cache
    _defaults_cache = {}
    try:
        App.closeDocument(_scratch_doc_name)
    except Exception:
        pass


#: document metadata a user edits on purpose (skip the save-noise fields
#: LastModifiedBy / *Date, which change on every save)
_DOC_META_FIELDS = ("Comment", "Company", "License", "LicenseURL", "CreatedBy")


def _document_meta(doc):
    """User-meaningful document metadata: the intentional fields plus any
    custom key/value pairs in doc.Meta. Empty values are omitted."""
    meta = {}
    for attr in _DOC_META_FIELDS:
        v = getattr(doc, attr, "") or ""
        if v:
            meta[attr] = v
    try:
        for k, v in dict(doc.Meta).items():
            if v not in (None, ""):
                meta["Meta." + k] = v
    except Exception:
        pass
    return meta


def serialize_document(doc):
    """Serialize a FreeCAD document to the canonical model-context dict."""
    try:
        document = {"name": doc.Name, "label": getattr(doc, "Label", doc.Name)}
        meta = _document_meta(doc)
        if meta:
            document["meta"] = meta
        return {
            "schema": SCHEMA_NAME,
            "schema_version": SCHEMA_VERSION,
            "document": document,
            "objects": [_serialize_object(o) for o in doc.Objects],
        }
    finally:
        _close_scratch()


# --------------------------------------------------------------------------
# LLM-legible markdown projection of the same model.
# --------------------------------------------------------------------------
def _fmt_ref(r):
    base = r.get("element") or ("geometry %d" % r["geometry"] if "geometry" in r else "?")
    return base + ("." + r["point"] if r.get("point") else "")


def _fmt_constraint(c):
    refs = " , ".join(_fmt_ref(r) for r in c.get("refs", []))
    val = ""
    if c.get("dimensional"):
        val = " = %g" % c["value"]
    name = ' "%s"' % c["name"] if c.get("name") else ""
    body = (": " + refs) if refs else ""
    return "%s%s%s%s" % (c["type"], name, body, val)


def to_markdown(model):
    """Render a serialized model dict as compact, LLM-legible text."""
    lines = []
    d = model["document"]
    lines.append("# Model: %s" % d.get("label", d["name"]))
    lines.append("schema %s v%s" % (model["schema"], model["schema_version"]))
    lines.append("")
    objs = {o["id"]: o for o in model["objects"]}
    n_datum = sum(1 for o in model["objects"] if o["role"] == "datum")

    def emit_params(o, indent="  "):
        for k, v in (o.get("params") or {}).items():
            unit = (" " + v["unit"]) if isinstance(v, dict) and "unit" in v else ""
            val = v["value"] if isinstance(v, dict) else v
            expr = (isinstance(v, dict) and v.get("expression"))
            ex = ("  (= %s)" % expr) if expr else ""
            lines.append("%s%s = %s%s%s" % (indent, k, val, unit, ex))
        for k, v in (o.get("links") or {}).items():
            lines.append("%s%s -> %s" % (indent, k, _links_str(v)))
        if o.get("material"):
            lines.append("%smaterial: %s" % (indent, o["material"]["name"]))

    def _links_str(v):
        items = v if isinstance(v, list) else [v]
        out = []
        for it in items:
            s = it["object"]
            if it.get("sub"):
                s += "[" + ",".join(it["sub"]) + "]"
            out.append(s)
        return ", ".join(out)

    for o in model["objects"]:
        if o["role"] == "body":
            tip = o.get("tip", "?")
            lines.append('## Body "%s" (tip: %s)' % (o["label"], tip))
            feats = o.get("features", [])
            if feats:
                lines.append("feature tree: " + " -> ".join(feats))
            lines.append("")
            for fid in feats:
                f = objs.get(fid)
                if not f:
                    continue
                if f["role"] == "sketch":
                    sk = f.get("sketch", {})
                    supp = ""
                    if sk.get("support"):
                        supp = "  (on %s)" % _links_str(sk["support"])
                    lines.append('### Sketch "%s"%s' % (f["label"], supp))
                    g = sk.get("geometry", [])
                    if g:
                        kinds = {}
                        for el in g:
                            kinds[el["type"]] = kinds.get(el["type"], 0) + 1
                        lines.append("geometry: " + ", ".join(
                            "%d %s" % (n, k) for k, n in kinds.items()))
                    cons = sk.get("constraints", [])
                    if cons:
                        lines.append("constraints:")
                        for c in cons:
                            lines.append("- " + _fmt_constraint(c))
                    emit_params(f)
                    lines.append("")
                else:
                    lines.append('### %s "%s"  [%s]' % (
                        f["role"].capitalize(), f["label"], f["type"]))
                    emit_params(f)
                    lines.append("")
    # non-Body top-level objects (loose solids, spreadsheets, ...)
    loose = [o for o in model["objects"]
             if o["role"] not in ("body", "datum")
             and not _in_a_body(o, model)]
    if loose:
        lines.append("## Other objects")
        for o in loose:
            lines.append('### %s "%s"  [%s]' % (o["role"].capitalize(), o["label"], o["type"]))
            emit_params(o)
            lines.append("")
    if n_datum:
        lines.append("_(+ %d datum/origin scaffolding objects omitted)_" % n_datum)
    return "\n".join(lines).rstrip() + "\n"


def _in_a_body(obj, model):
    for o in model["objects"]:
        if o["role"] == "body" and obj["id"] in o.get("features", []):
            return True
    return False
