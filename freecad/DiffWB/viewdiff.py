# SPDX-License-Identifier: MIT
"""In-viewport 3D comparison: build a temporary document that shows the OLD
and NEW versions of a model together in the 3D view, colored by status --
added (bluish green, opaque), removed (vermillion, semi-transparent),
changed (new shape in blue over a grey ghost of the old), unchanged (quiet
transparent grey context).

The input documents are never touched: every displayed object is a static
``Part::Feature`` carrying a detached copy of a shape (``Part.Shape.copy()``
survives closing its source document, see loaders/svgdiff). Objects are
grouped under one ``App::DocumentObjectGroup`` per status, so the model
tree doubles as a legend and FreeCAD's native group visibility toggles work
as per-category show/hide.

Split like the rest of the addon: this module is the GUI-free core.
``build_comparison_document`` assembles the document (works headless under
freecadcmd, which is how the harness exercises it); colors and transparency
are ViewObject properties that only exist under the GUI, so
``apply_view_styles`` sets them when present and is a no-op headless. The
"Compare in 3D" command in commands.py is the thin GUI layer on top.

Which objects are drawn follows svgdiff.object_statuses -- one carrier per
top-level shape (a PartDesign Body once, as its tip solid; datum/sketch
scaffolding and features inside a Body skipped), so the view is not
polluted by intermediate features.
"""
import sys

import FreeCAD as App

from . import svgdiff as V

#: base name of the temporary comparison document (FreeCAD uniquifies it if
#: a user document already owns the name)
DIFFVIEW_DOC_NAME = "DiffView"

#: marks documents created by this module, so cleanup only ever closes our
#: own temporary documents and never a user document that happens to share
#: the DiffView name
MARKER = "freecad-diff temporary 3D comparison (never saved by the tool)"

#: one group per status: (status key, group Name, group Label). Order is
#: the tree order, most attention-worthy first.
GROUPS = (
    ("added", "Added", "Added"),
    ("removed", "Removed", "Removed"),
    ("changed_new", "ChangedNew", "Changed (new)"),
    ("changed_old", "ChangedOld", "Changed (old)"),
    ("unchanged", "Unchanged", "Unchanged"),
)

#: per-status view styling. Hues are the addon's Okabe-Ito palette
#: (svgdiff.PALETTE_OKABE_ITO), reused verbatim so the 3D view, the SVG
#: overlay and the report all speak the same color language. Transparency
#: is the second channel: the current model (added / changed-new) is
#: opaque, what is gone (removed) is half see-through, ghosts and unchanged
#: context recede almost entirely.
VIEW_STYLE = {
    "added":       {"color": V.PALETTE_OKABE_ITO["added"]["stroke"],       "transparency": 0},
    "removed":     {"color": V.PALETTE_OKABE_ITO["removed"]["stroke"],     "transparency": 60},
    "changed_new": {"color": V.PALETTE_OKABE_ITO["changed_new"]["stroke"], "transparency": 0},
    "changed_old": {"color": V.PALETTE_OKABE_ITO["changed_old"]["stroke"], "transparency": 80},
    "unchanged":   {"color": V.PALETTE_OKABE_ITO["unchanged"]["stroke"],   "transparency": 85},
}


def _rgb(hex_color):
    """'#rrggbb' -> (r, g, b) floats in 0..1 (FreeCAD color property form)."""
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def build_comparison_document(diff, old_model, old_shapes, new_model, new_shapes):
    """Assemble the temporary comparison document from a diff and the two
    sides' detached shape dicts (``{object_id: Part.Shape}``, see
    ``loaders.model_and_shapes_from_file``). Any previous comparison
    document is closed first, so at most one exists at a time.

    Returns ``(doc, counts)`` where ``counts`` maps each GROUPS status key
    to the number of shapes placed in that group. Objects without a shape
    (groups, spreadsheets already filtered upstream) are skipped silently;
    a shape that fails to copy is skipped with a stderr note and never
    aborts the build."""
    close_comparison_documents()
    statuses, old_c, new_c = V.object_statuses(diff, old_model, new_model)

    try:
        doc = App.newDocument(DIFFVIEW_DOC_NAME, temp=True)
    except TypeError:  # older signature without temp
        doc = App.newDocument(DIFFVIEW_DOC_NAME)
    doc.Comment = MARKER

    groups = {}
    counts = {}
    for key, name, label in GROUPS:
        g = doc.addObject("App::DocumentObjectGroup", name)
        g.Label = label
        groups[key] = g
        counts[key] = 0

    def add(key, oid, shapes, carriers):
        shp = shapes.get(oid)
        if shp is None:
            return  # no shape (e.g. a plain group): nothing to show, skip
        feat = None
        try:
            feat = doc.addObject("Part::Feature", oid)
            feat.Shape = shp.copy()
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write("viewdiff: skipping %r (%s): shape copy failed: %s\n"
                             % (oid, key, exc))
            if feat is not None:
                try:
                    doc.removeObject(feat.Name)
                except Exception:
                    pass
            return
        o = carriers.get(oid) or {}
        feat.Label = o.get("label") or oid
        groups[key].addObject(feat)
        counts[key] += 1

    for oid, st in sorted(statuses.items()):
        if st == "added":
            add("added", oid, new_shapes, new_c)
        elif st == "removed":
            add("removed", oid, old_shapes, old_c)
        elif st == "changed":
            add("changed_new", oid, new_shapes, new_c)
            add("changed_old", oid, old_shapes, old_c)
        else:
            add("unchanged", oid, new_shapes, new_c)

    doc.recompute()
    return doc, counts


def apply_view_styles(doc):
    """Color each group's members per VIEW_STYLE. ViewObjects only exist
    under the GUI, so under freecadcmd this is a harmless no-op."""
    for key, name, _label in GROUPS:
        grp = doc.getObject(name)
        if grp is None:
            continue
        rgb = _rgb(VIEW_STYLE[key]["color"])
        transparency = VIEW_STYLE[key]["transparency"]
        for feat in grp.Group:
            vo = getattr(feat, "ViewObject", None)
            if vo is None:
                continue
            try:
                vo.ShapeColor = rgb
                vo.LineColor = rgb
                vo.Transparency = transparency
            except Exception:
                continue


def close_comparison_documents():
    """Close every comparison document this module created, identified by
    the MARKER comment (a user document merely named DiffView is left
    alone). Safe to call when none exist -- e.g. after the user already
    closed it by hand. Returns the number of documents closed."""
    closed = 0
    for name, doc in list(App.listDocuments().items()):
        if getattr(doc, "Comment", None) != MARKER:
            continue
        try:
            App.closeDocument(name)
            closed += 1
        except Exception:
            pass
    return closed
