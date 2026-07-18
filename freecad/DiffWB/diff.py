# SPDX-License-Identifier: MIT
"""Semantic diff of two model-context serializations (see SCHEMA.md).

Answers "what changed between two versions of a FreeCAD document" at the
level a human cares about: features added or removed, a Length going from
15 mm to 20 mm, a constraint removed, a label renamed, the feature-tree
order changing. This is the payoff of the canonical schema: because both
sides are the same versioned JSON, the diff is plain data comparison, no
geometry kernel needed.

Pure Python (no FreeCAD imports), so it runs anywhere: inside FreeCAD,
under freecadcmd, or in a plain interpreter fed two saved JSON exports.
Objects are matched by ``id`` (FreeCAD's internal object Name, stable
across saves); constraints are matched by a signature of (type, refs,
user name) so a dimensional constraint whose value changed is reported as
a value change rather than a remove-plus-add.

Known v1 limits, stated rather than hidden: sketch geometry is compared
positionally (by GeoId index), so inserting a geometry element mid-list
reads as several edited elements. A dimensional value difference is only
reported as a value CHANGE when the geometry it references is itself
unchanged; when indices have shifted, the pair is reported as removed
plus added instead of misattributing a value change to the wrong element.
"""

DIFF_SCHEMA_NAME = "freecad-model-context-diff"
DIFF_SCHEMA_VERSION = "1.0"

_NUM_TOL = 1e-9


def _entry_value(entry):
    if isinstance(entry, dict):
        return entry.get("value"), entry.get("unit"), entry.get("expression")
    return entry, None, None


def _values_equal(a, b):
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, float) and isinstance(b, (int, float)):
        return abs(a - float(b)) <= _NUM_TOL
    if isinstance(b, float) and isinstance(a, (int, float)):
        return abs(float(a) - b) <= _NUM_TOL
    return a == b


def _fmt(value, unit=None):
    import math
    if isinstance(value, float) and math.isfinite(value) and value == int(value):
        value = int(value)
    s = str(value)
    return "%s %s" % (s, unit) if unit else s


def _refs_key(refs):
    out = []
    for r in refs or []:
        if "geometry" in r:
            base = "g%d" % r["geometry"]
        else:
            base = r.get("element", "?")
        if r.get("point"):
            base += "." + r["point"]
        out.append(base)
    return tuple(out)


def _constraint_sig(c):
    """Identity of a constraint, excluding its dimensional value so value
    edits match up as changes."""
    return (c.get("type"), _refs_key(c.get("refs")), c.get("name", ""))


def _fmt_constraint(c):
    refs = ", ".join(_refs_key(c.get("refs")))
    s = c.get("type", "?")
    if c.get("name"):
        s += ' "%s"' % c["name"]
    if refs:
        s += ": " + refs
    if c.get("dimensional"):
        s += " = %s" % _fmt(c.get("value"))
    return s


def _diff_params(old, new, changes):
    op, np = old.get("params") or {}, new.get("params") or {}
    for name in sorted(set(op) | set(np)):
        if name not in np:
            v, u, e = _entry_value(op[name])
            changes.append({"kind": "param_removed", "name": name,
                            "old": _fmt(v, u), "old_expression": e})
        elif name not in op:
            v, u, e = _entry_value(np[name])
            changes.append({"kind": "param_added", "name": name,
                            "new": _fmt(v, u), "new_expression": e})
        else:
            ov, ou, oe = _entry_value(op[name])
            nv, nu, ne = _entry_value(np[name])
            if not _values_equal(ov, nv) or ou != nu or oe != ne:
                ch = {"kind": "param", "name": name,
                      "old": _fmt(ov, ou), "new": _fmt(nv, nu)}
                if ch["old"] == ch["new"]:
                    ch["old"], ch["new"] = repr(ov), repr(nv)
                if oe != ne:
                    ch["old_expression"], ch["new_expression"] = oe, ne
                changes.append(ch)


def _links_str(v):
    items = v if isinstance(v, list) else [v]
    out = []
    for it in items or []:
        s = it.get("object", "?")
        if it.get("sub"):
            s += "[" + ",".join(it["sub"]) + "]"
        out.append(s)
    return ", ".join(out)


def _diff_links(old, new, changes):
    ol, nl = old.get("links") or {}, new.get("links") or {}
    for name in sorted(set(ol) | set(nl)):
        a = _links_str(ol.get(name)) if name in ol else None
        b = _links_str(nl.get(name)) if name in nl else None
        if a != b:
            changes.append({"kind": "link", "name": name, "old": a, "new": b})


def _diff_sketch(old, new, changes):
    os_, ns = old.get("sketch") or {}, new.get("sketch") or {}

    # attachment: a reattached sketch is a major edit
    o_sup, n_sup = _links_str(os_.get("support")), _links_str(ns.get("support"))
    o_map, n_map = os_.get("map_mode"), ns.get("map_mode")
    if o_sup != n_sup or o_map != n_map:
        changes.append({"kind": "sketch_attach",
                        "old": "%s (%s)" % (o_sup or "none", o_map or "none"),
                        "new": "%s (%s)" % (n_sup or "none", n_map or "none")})

    og, ng = os_.get("geometry") or [], ns.get("geometry") or []
    if len(og) != len(ng):
        changes.append({"kind": "geometry_count",
                        "old": len(og), "new": len(ng)})
    else:
        n_changed = sum(1 for a, b in zip(og, ng) if a != b)
        if n_changed:
            changes.append({"kind": "geometry_edited", "count": n_changed})

    def _geometry_stable(c):
        """True if the geometry a constraint references is STRUCTURALLY the
        same element in both versions (same list length, same type at the
        referenced indices), so a value difference really belongs to THIS
        constraint rather than to an index shift. Coordinates are ignored on
        purpose: changing a driving dimension legitimately moves geometry."""
        if len(og) != len(ng):
            return False
        for r in c.get("refs") or []:
            i = r.get("geometry")
            if i is None:
                continue
            if i >= len(og):
                return False
            if (og[i].get("type") != ng[i].get("type")
                    or og[i].get("construction") != ng[i].get("construction")):
                return False
        return True

    oc = {}
    for c in os_.get("constraints") or []:
        oc.setdefault(_constraint_sig(c), []).append(c)
    nc = {}
    for c in ns.get("constraints") or []:
        nc.setdefault(_constraint_sig(c), []).append(c)
    for sig in sorted(set(oc) | set(nc), key=repr):
        a, b = list(oc.get(sig, [])), list(nc.get(sig, []))
        # pair off exactly-equal constraints first (reorders are not changes)
        for ca in a[:]:
            for cb in b:
                if ca == cb:
                    a.remove(ca)
                    b.remove(cb)
                    break
        # pair remainders positionally; only claim a value change when the
        # referenced geometry itself is unchanged, else it is an index shift
        for ca, cb in zip(a, b):
            if (ca.get("dimensional")
                    and not _values_equal(ca.get("value"), cb.get("value"))
                    and _geometry_stable(cb)):
                changes.append({"kind": "constraint_value",
                                "constraint": _fmt_constraint(cb),
                                "old": _fmt(ca.get("value")),
                                "new": _fmt(cb.get("value"))})
            else:
                changes.append({"kind": "constraint_removed",
                                "constraint": _fmt_constraint(ca)})
                changes.append({"kind": "constraint_added",
                                "constraint": _fmt_constraint(cb)})
        for c in a[len(b):]:
            changes.append({"kind": "constraint_removed",
                            "constraint": _fmt_constraint(c)})
        for c in b[len(a):]:
            changes.append({"kind": "constraint_added",
                            "constraint": _fmt_constraint(c)})


def _diff_object(old, new):
    changes = []
    if old.get("label") != new.get("label"):
        changes.append({"kind": "label", "old": old.get("label"), "new": new.get("label")})
    if old.get("type") != new.get("type"):
        changes.append({"kind": "type", "old": old.get("type"), "new": new.get("type")})
    _diff_params(old, new, changes)
    _diff_links(old, new, changes)
    if old.get("role") == "body" or new.get("role") == "body":
        if (old.get("features") or []) != (new.get("features") or []):
            changes.append({"kind": "tree", "old": old.get("features") or [],
                            "new": new.get("features") or []})
        if old.get("tip") != new.get("tip"):
            changes.append({"kind": "tip", "old": old.get("tip"), "new": new.get("tip")})
    if old.get("sketch") is not None or new.get("sketch") is not None:
        _diff_sketch(old, new, changes)
    omat, nmat = old.get("material") or {}, new.get("material") or {}
    if omat.get("name") != nmat.get("name"):
        changes.append({"kind": "material", "old": omat.get("name"),
                        "new": nmat.get("name")})
    elif omat != nmat:
        changes.append({"kind": "material_edited",
                        "name": nmat.get("name") or omat.get("name")})
    return changes


def diff_models(old_model, new_model):
    """Structured diff of two model-context dicts. Returns a dict with
    ``added``, ``removed`` and ``changed`` object lists (see module doc)."""
    old_objs = {o["id"]: o for o in old_model.get("objects", [])}
    new_objs = {o["id"]: o for o in new_model.get("objects", [])}

    def _brief(o):
        return {"id": o["id"], "label": o.get("label"), "type": o.get("type"),
                "role": o.get("role")}

    added = [_brief(new_objs[i]) for i in new_objs if i not in old_objs
             and new_objs[i].get("role") != "datum"]
    removed = [_brief(old_objs[i]) for i in old_objs if i not in new_objs
               and old_objs[i].get("role") != "datum"]
    changed = []
    for oid in old_objs:
        if oid not in new_objs:
            continue
        ch = _diff_object(old_objs[oid], new_objs[oid])
        if ch:
            entry = _brief(new_objs[oid])
            entry["changes"] = ch
            changed.append(entry)

    return {
        "schema": DIFF_SCHEMA_NAME,
        "schema_version": DIFF_SCHEMA_VERSION,
        "old": old_model.get("document", {}),
        "new": new_model.get("document", {}),
        "added": sorted(added, key=lambda o: o["id"]),
        "removed": sorted(removed, key=lambda o: o["id"]),
        "changed": sorted(changed, key=lambda o: o["id"]),
    }


def is_empty(diff):
    return not (diff["added"] or diff["removed"] or diff["changed"])


def _change_lines(o):
    lines = []
    name = o.get("label") or o["id"]
    for c in o.get("changes", []):
        k = c["kind"]
        if k == "sketch_attach":
            lines.append("~ %s: attachment %s -> %s" % (name, c["old"], c["new"]))
        elif k == "material_edited":
            lines.append("~ %s: material \"%s\" properties edited" % (name, c["name"]))
        elif k == "param":
            line = "~ %s: %s %s -> %s" % (name, c["name"], c["old"], c["new"])
            if "new_expression" in c and (c.get("old_expression") or c.get("new_expression")):
                line += "  (expression: %s -> %s)" % (
                    c.get("old_expression") or "none", c.get("new_expression") or "none")
            lines.append(line)
        elif k == "param_added":
            line = "+ %s: %s = %s" % (name, c["name"], c["new"])
            if c.get("new_expression"):
                line += "  (= %s)" % c["new_expression"]
            lines.append(line)
        elif k == "param_removed":
            # a param disappearing from the serialization usually means it
            # was set back to its type default (defaults are elided)
            line = "~ %s: %s %s -> (default/unset)" % (name, c["name"], c["old"])
            if c.get("old_expression"):
                line += "  (expression %s removed)" % c["old_expression"]
            lines.append(line)
        elif k == "label":
            lines.append("~ %s: renamed \"%s\" -> \"%s\"" % (o["id"], c["old"], c["new"]))
        elif k == "link":
            lines.append("~ %s: %s -> %s (was %s)" % (name, c["name"],
                         c.get("new") or "none", c.get("old") or "none"))
        elif k == "tree":
            lines.append("~ %s: feature order %s -> %s" % (
                name, " > ".join(c["old"]) or "(empty)", " > ".join(c["new"]) or "(empty)"))
        elif k == "tip":
            lines.append("~ %s: tip %s -> %s" % (name, c["old"] or "(none)",
                                                 c["new"] or "(none)"))
        elif k == "constraint_added":
            lines.append("+ %s: constraint %s" % (name, c["constraint"]))
        elif k == "constraint_removed":
            lines.append("- %s: constraint %s" % (name, c["constraint"]))
        elif k == "constraint_value":
            lines.append("~ %s: constraint %s (was %s)" % (name, c["constraint"], c["old"]))
        elif k == "constraint_edited":
            lines.append("~ %s: constraint edited: %s" % (name, c["constraint"]))
        elif k == "geometry_count":
            lines.append("~ %s: sketch geometry count %d -> %d" % (name, c["old"], c["new"]))
        elif k == "geometry_edited":
            lines.append("~ %s: %d sketch geometry element(s) moved/edited" % (name, c["count"]))
        elif k == "material":
            lines.append("~ %s: material %s -> %s" % (name, c.get("old") or "none",
                                                      c.get("new") or "none"))
        elif k == "type":
            lines.append("~ %s: type %s -> %s" % (name, c["old"], c["new"]))
    return lines


def diff_to_text(diff):
    """Render a structured diff as compact, git-style +/~/- text."""
    if is_empty(diff):
        return "No semantic changes.\n"
    lines = []
    for o in diff["added"]:
        lines.append("+ %s (%s) added" % (o.get("label") or o["id"], o.get("type")))
    for o in diff["removed"]:
        lines.append("- %s (%s) removed" % (o.get("label") or o["id"], o.get("type")))
    for o in diff["changed"]:
        lines.extend(_change_lines(o))
    return "\n".join(lines) + "\n"


def diff_to_markdown(diff):
    """Render a structured diff as Markdown."""
    old_l = diff.get("old", {}).get("label") or diff.get("old", {}).get("name", "old")
    new_l = diff.get("new", {}).get("label") or diff.get("new", {}).get("name", "new")
    head = "# Model diff: %s -> %s\n\n" % (old_l, new_l)
    if is_empty(diff):
        return head + "No semantic changes.\n"
    body = "```\n" + diff_to_text(diff) + "```\n"
    return head + body
