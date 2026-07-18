# SPDX-License-Identifier: MIT
"""Visual model diff: overlay the OLD and NEW versions of a document as
styled 2D line-art in one SVG, so a reader sees at a glance what was added
(green, solid), removed (red, dashed), changed (old ghost dashed grey
underneath, new solid blue on top) and untouched (thin light grey context).

Rendering is fully headless: each drawn object is projected with
``TechDraw.projectToSVG`` (no GL context needed), and separate projections
share the same 2D frame for a given direction, so per-object fragments
overlay exactly. Styling is applied by rewriting each fragment's single
wrapping ``<g>`` (TechDraw puts fill/stroke attributes there, verified
against FreeCAD 1.1). The composite viewBox comes from Qt's own rendered
bounds of a wrapper group (``QSvgRenderer.boundsOnElement`` -- parent
transforms are skipped by Qt, but a wrapper's bounds DO include its
descendants' transforms).

Draw policy: one silhouette per top-level shape carrier -- a PartDesign
Body is drawn once (its tip solid), features inside a Body are not drawn
separately (the Body silhouette carries their effect), datum/origin
scaffolding and sketches are skipped. A Body counts as changed when it or
anything in its feature tree changed.
"""
import os
import re
import tempfile

import FreeCAD as App

# Default palette. Chosen to survive both white report backgrounds and
# common color-vision deficiencies: green/red are the industry convention
# for added/removed (GitHub 3D diff, SolidWorks Compare), disambiguated
# here by line STYLE as well (added solid, removed dashed) so color is
# never the only channel. Changed pairs old ghost grey with new blue.
PALETTE = {
    "added":       {"stroke": "#1a7f37", "px": 2.2, "dash": None,       "opacity": 1.0},
    "removed":     {"stroke": "#cf222e", "px": 1.8, "dash": (6.0, 4.0), "opacity": 0.9},
    "changed_old": {"stroke": "#8c959f", "px": 1.6, "dash": (5.0, 4.0), "opacity": 0.95},
    "changed_new": {"stroke": "#0969da", "px": 2.2, "dash": None,       "opacity": 1.0},
    # unchanged is the "what was already here" context: a solid, clearly
    # defined mid-grey so the baseline reads at a glance under the colored
    # changes (added/removed/changed are distinguished by hue on top of it).
    "unchanged":   {"stroke": "#57606a", "px": 1.7, "dash": None,       "opacity": 1.0},
    # the actual boolean material added/removed between versions (volume diff);
    # drawn bold on top so the isolated chunk of material stands out.
    "added_material":   {"stroke": "#1a7f37", "px": 3.0, "dash": None,       "opacity": 1.0},
    "removed_material": {"stroke": "#cf222e", "px": 2.6, "dash": (6.0, 4.0), "opacity": 1.0},
}
#: Okabe-Ito colorblind-safe alternative. Same line-style channel as the
#: default (added solid, removed dashed, changed-old ghosted) so it reads
#: the same for viewers with any color-vision deficiency; hues are drawn
#: from the Okabe-Ito qualitative set (bluish-green / vermillion / blue /
#: grey) which stay distinguishable under deuteranopia and protanopia.
PALETTE_OKABE_ITO = {
    "added":       {"stroke": "#009e73", "px": 2.2, "dash": None,       "opacity": 1.0},
    "removed":     {"stroke": "#d55e00", "px": 1.8, "dash": (6.0, 4.0), "opacity": 0.95},
    "changed_old": {"stroke": "#999999", "px": 1.4, "dash": (4.0, 4.0), "opacity": 0.85},
    "changed_new": {"stroke": "#0072b2", "px": 2.2, "dash": None,       "opacity": 1.0},
    "unchanged":   {"stroke": "#666666", "px": 1.7, "dash": None,       "opacity": 1.0},
    "added_material":   {"stroke": "#009e73", "px": 3.0, "dash": None,       "opacity": 1.0},
    "removed_material": {"stroke": "#d55e00", "px": 2.6, "dash": (6.0, 4.0), "opacity": 1.0},
}

#: named palettes selectable by string (build_overlay_svg ``palette=``)
PALETTES = {"default": PALETTE, "okabe-ito": PALETTE_OKABE_ITO}

#: bottom-to-top draw order so the informative strokes win overlaps; the
#: boolean material chunks (when computed) sit on top of everything
DRAW_ORDER = ("unchanged", "changed_old", "changed_new", "removed", "added",
              "removed_material", "added_material")

LEGEND = (
    ("added", "added"),
    ("removed", "removed"),
    ("changed_new", "changed (new)"),
    ("changed_old", "changed (old)"),
    ("unchanged", "unchanged"),
)

#: named view directions (world -> screen projection directions)
VIEWS = {
    "iso":   (-1.0, -1.0, -1.0),
    "front": (0.0, -1.0, 0.0),
    "top":   (0.0, 0.0, -1.0),
    "right": (-1.0, 0.0, 0.0),
}

_G_OPEN_RE = re.compile(r"<g\b[^>]*>")


def object_statuses(diff, old_model, new_model):
    """Map drawable top-level object ids to a status: added / removed /
    changed / unchanged, applying the Body aggregation rule. Returns
    (statuses, old_ids_to_draw, new_ids_to_draw)."""
    added = {o["id"] for o in diff.get("added", [])}
    removed = {o["id"] for o in diff.get("removed", [])}
    changed = {o["id"] for o in diff.get("changed", [])}

    def carriers(model):
        objs = {o["id"]: o for o in model.get("objects", [])}
        in_body = set()
        for o in objs.values():
            if o.get("role") == "body":
                in_body.update(o.get("features") or [])
        out = {}
        for oid, o in objs.items():
            if o.get("role") in ("datum", "sketch", "spreadsheet"):
                continue
            if oid in in_body:
                continue
            out[oid] = o
        return out

    old_c, new_c = carriers(old_model), carriers(new_model)

    def body_touched(o):
        feats = set(o.get("features") or [])
        return bool(feats & (added | removed | changed))

    statuses = {}
    for oid, o in new_c.items():
        if oid not in old_c:
            statuses[oid] = "added"
        elif oid in changed or (o.get("role") == "body" and body_touched(o)):
            statuses[oid] = "changed"
        else:
            old_o = old_c.get(oid, {})
            if old_o.get("role") == "body" and body_touched(old_o):
                statuses[oid] = "changed"
            else:
                statuses[oid] = "unchanged"
    for oid in old_c:
        if oid not in new_c:
            statuses[oid] = "removed"
    return statuses, old_c, new_c


def _project(shape, direction):
    import TechDraw
    return TechDraw.projectToSVG(shape, App.Vector(*direction))


def _restyle(fragment, cls):
    """Rewrite the fragment's wrapping <g> so the whole fragment renders in
    the class's color; width/dash placeholders are resolved after the
    global scale is known (tokens replaced in _finalize)."""
    def repl(m):
        g = m.group(0)
        g = re.sub(r'stroke="[^"]*"', 'stroke="__STROKE_%s__"' % cls, g)
        g = re.sub(r'stroke-width="[^"]*"', 'stroke-width="__WIDTH_%s__"' % cls, g)
        g = g[:-1] + ' stroke-dasharray="__DASH_%s__" stroke-opacity="__OP_%s__">' % (cls, cls)
        return g
    return _G_OPEN_RE.sub(repl, fragment)


def _bbox_of(svg_body):
    """Painted bounds of ``svg_body`` via a QSvgRenderer wrapper group."""
    from PySide import QtSvg

    probe = ('<svg xmlns="http://www.w3.org/2000/svg">\n<g id="mcroot">\n'
             + svg_body + "\n</g>\n</svg>\n")
    fd, path = tempfile.mkstemp(suffix=".svg")
    try:
        with os.fdopen(fd, "w") as f:
            # tokens are not valid attribute values yet; neutralize for probing
            f.write(re.sub(r"__[A-Z]+_[a-z_]+__", "1", probe))
        r = QtSvg.QSvgRenderer(path)
        if not r.isValid():
            return None
        rect = r.boundsOnElement("mcroot")
        if rect.isNull() or rect.isEmpty():
            return None
        return (rect.x(), rect.y(), rect.width(), rect.height())
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _revision_cloud(x, y, w, h, color, scallop=7.0):
    """SVG path of a revision cloud (scalloped rectangle) around the given
    screen-space box, stroked in ``color``. Each side is a run of outward
    semicircular arcs -- the drafting convention for circling a revision."""
    import math
    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    d = ["M %.2f %.2f" % corners[0]]
    for i in range(4):
        x0, y0 = corners[i]
        x1, y1 = corners[(i + 1) % 4]
        seg = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(round(seg / (2.0 * scallop))))
        r = seg / (2.0 * n)  # exact semicircle radius for this side
        for k in range(1, n + 1):
            px = x0 + (x1 - x0) * k / n
            py = y0 + (y1 - y0) * k / n
            # sweep-flag 1 bulges outward for a clockwise perimeter (y-down)
            d.append("A %.2f %.2f 0 0 1 %.2f %.2f" % (r, r, px, py))
    d.append("Z")
    return ('<path d="%s" fill="none" stroke="%s" stroke-width="1.4" '
            'stroke-opacity="0.9"/>' % (" ".join(d), color))


def _number_badge(x, y, n, color):
    """A small filled circle badge carrying the revision number, placed at a
    box corner (white digit on the status color for contrast)."""
    r = 10
    return ('<g><circle cx="%.1f" cy="%.1f" r="%d" fill="%s" '
            'stroke="white" stroke-width="1.5"/>'
            '<text x="%.1f" y="%.1f" font-family="sans-serif" font-size="12" '
            'font-weight="bold" fill="white" text-anchor="middle">%d</text></g>'
            % (x, y, r, color, x, y + 4, n))


def build_overlay_svg(diff, old_model, old_shapes, new_model, new_shapes,
                      direction="iso", width=760, height=560,
                      palette=None, title=None, legend=True, callouts=False,
                      material=None):
    """Compose the styled overlay SVG (returned as a string).

    ``old_shapes``/``new_shapes``: {object_id: Part.Shape} (see
    ``loaders.model_and_shapes_from_file`` / ``shapes_from_document``).
    ``direction``: a VIEWS key or an (x, y, z) tuple.
    ``callouts``: when True, draw a numbered revision cloud around every
    added/removed/changed object (the drafting convention for marking a
    revised region). Off by default -- the color+line-style channel already
    distinguishes states, and clouds add ink; opt in when a reviewer wants
    each change explicitly circled and numbered.
    """
    if isinstance(palette, str):
        palette = PALETTES.get(palette, PALETTE)
    pal = dict(PALETTE)
    if palette:
        pal.update(palette)
    dir_vec = VIEWS.get(direction, direction if isinstance(direction, tuple) else VIEWS["iso"])

    statuses, old_c, new_c = object_statuses(diff, old_model, new_model)

    layers = {k: [] for k in DRAW_ORDER}
    # per-object fragments, so a callout can be anchored to each change's bbox
    marks = []  # list of (status, oid, [fragment, ...]) for added/removed/changed
    for oid, st in statuses.items():
        if st == "added" and oid in new_shapes:
            frag = _restyle(_project(new_shapes[oid], dir_vec), "added")
            layers["added"].append(frag)
            marks.append(("added", oid, [frag]))
        elif st == "removed" and oid in old_shapes:
            frag = _restyle(_project(old_shapes[oid], dir_vec), "removed")
            layers["removed"].append(frag)
            marks.append(("removed", oid, [frag]))
        elif st == "changed":
            frags = []
            if oid in old_shapes:
                f = _restyle(_project(old_shapes[oid], dir_vec), "changed_old")
                layers["changed_old"].append(f)
                frags.append(f)
            if oid in new_shapes:
                f = _restyle(_project(new_shapes[oid], dir_vec), "changed_new")
                layers["changed_new"].append(f)
                frags.append(f)
            if frags:
                marks.append(("changed", oid, frags))
        elif st == "unchanged" and oid in new_shapes:
            layers["unchanged"].append(_restyle(_project(new_shapes[oid], dir_vec), "unchanged"))

    # boolean material chunks (from volumediff), drawn bold on top
    if material:
        added_shape, removed_shape = material
        for shp, cls in ((removed_shape, "removed_material"),
                         (added_shape, "added_material")):
            if shp is None:
                continue
            try:
                layers[cls].append(_restyle(_project(shp, dir_vec), cls))
            except Exception:
                continue

    # each layer wrapped in a class-tagged group, so an HTML host can fade the
    # old side (changed_old, removed) against the new side (changed_new, added)
    # with a slider -- see htmlreport's old/new wipe
    body = "\n".join('<g class="fcd-%s">\n%s\n</g>' % (k, "\n".join(layers[k]))
                     for k in DRAW_ORDER if layers[k])
    if not body.strip():
        body = ""
    bbox = _bbox_of(body) if body else None
    if bbox is None:
        bbox = (-10.0, -10.0, 20.0, 20.0)
    x, y, w, h = bbox
    margin = 0.06 * max(w, h) or 1.0
    x, y, w, h = x - margin, y - margin, w + 2 * margin, h + 2 * margin

    # uniform scale: pad the shorter axis to the target aspect
    target = float(width) / float(height)
    aspect = (w / h) if h else target
    if aspect > target:
        nh = w / target
        y -= (nh - h) / 2.0
        h = nh
    elif aspect < target:
        nw = h * target
        x -= (nw - w) / 2.0
        w = nw

    px_per_unit = float(width) / w if w else 1.0

    def units(px):
        return max(px / px_per_unit, 1e-6)

    # resolve style tokens now that the scale is known
    for cls, spec in pal.items():
        body = body.replace("__STROKE_%s__" % cls, spec["stroke"])
        body = body.replace("__WIDTH_%s__" % cls, "%.6g" % units(spec["px"]))
        dash = spec.get("dash")
        dash_val = ("%.6g %.6g" % (units(dash[0]), units(dash[1]))) if dash else "none"
        body = body.replace("__DASH_%s__" % cls, dash_val)
        body = body.replace("__OP_%s__" % cls, "%.3g" % spec.get("opacity", 1.0))

    # legend + title in SCREEN space (outer svg), model in inner viewBox'd svg
    has_material = bool(layers["added_material"] or layers["removed_material"])
    legend_h = (34 + (18 if has_material else 0)) if legend else 0
    title_h = 26 if title else 0
    total_h = height + legend_h + title_h
    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
               'viewBox="0 0 %d %d">' % (width, total_h, width, total_h))
    out.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (width, total_h))
    yoff = 0
    if title:
        out.append('<text x="12" y="18" font-family="sans-serif" font-size="14" '
                   'fill="#24292f">%s</text>' % _esc(title))
        yoff += title_h
    # map model coords into screen space with a transform group (Qt's SVG
    # Tiny 1.2 renderer rejects nested <svg> elements)
    scale = float(width) / w if w else 1.0
    tx = -x * scale
    ty = yoff - y * scale
    out.append('<g transform="translate(%.6g %.6g) scale(%.6g)">' % (tx, ty, scale))
    out.append(body)
    out.append('</g>')

    # numbered revision clouds (opt-in). Anchor each to its object's painted
    # bbox, mapped from model space into screen space with the same transform.
    if callouts and marks:
        anchors = []
        for st, oid, frags in marks:
            mb = _bbox_of("\n".join(frags))
            if mb is None:
                continue
            mx, my, mw, mh = mb
            sx, sy = tx + mx * scale, ty + my * scale
            sw, sh = mw * scale, mh * scale
            anchors.append((st, sx, sy, sw, sh))
        # number top-to-bottom, left-to-right so the sequence reads naturally
        anchors.sort(key=lambda a: (round(a[2], 1), round(a[1], 1)))
        for i, (st, sx, sy, sw, sh) in enumerate(anchors, start=1):
            color = pal[st if st != "changed" else "changed_new"]["stroke"]
            pad = 7.0
            out.append(_revision_cloud(sx - pad, sy - pad, sw + 2 * pad,
                                       sh + 2 * pad, color))
            out.append(_number_badge(sx - pad, sy - pad, i, color))
    if legend:
        base_y = yoff + height + 22
        material_items = []
        if layers["added_material"]:
            material_items.append(("added_material", "material added"))
        if layers["removed_material"]:
            material_items.append(("removed_material", "material removed"))

        def _draw_legend(items, ly):
            lx = 12
            for cls, label in items:
                spec = pal[cls]
                dash = spec.get("dash")
                dash_attr = (' stroke-dasharray="%g %g"' % dash) if dash else ""
                out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" '
                           'stroke-width="%g"%s stroke-opacity="%g"/>'
                           % (lx, ly - 4, lx + 26, ly - 4, spec["stroke"],
                              max(spec["px"], 1.6), dash_attr,
                              spec.get("opacity", 1.0)))
                out.append('<text x="%d" y="%d" font-family="sans-serif" '
                           'font-size="12" fill="#24292f">%s</text>'
                           % (lx + 32, ly, _esc(label)))
                lx += 32 + 9 * len(label) + 34

        _draw_legend(list(LEGEND), base_y)
        if material_items:
            _draw_legend(material_items, base_y + 18)
    out.append('</svg>')
    return "\n".join(out)


def build_overlays(diff, old_model, old_shapes, new_model, new_shapes,
                   views=("iso", "front", "top"), palette=None,
                   width=760, height=560, legend=True, callouts=False,
                   material=None):
    """Build one overlay SVG per named view. Returns an ordered
    ``{view_name: svg_string}`` dict (skipping any view that fails to
    project). Convenience wrapper over :func:`build_overlay_svg` for the
    HTML report's view tabs."""
    out = {}
    for v in views:
        try:
            out[v] = build_overlay_svg(
                diff, old_model, old_shapes, new_model, new_shapes,
                direction=v, width=width, height=height, palette=palette,
                title=None, legend=legend, callouts=callouts, material=material)
        except Exception as exc:  # noqa: BLE001
            # a failed projection drops just that view; make the omission
            # diagnosable rather than silently shipping a viz-less report
            import sys
            sys.stderr.write("svgdiff: view %r failed to render: %s\n" % (v, exc))
            continue
    return out


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def rasterize(svg_text, out_path, width, height):
    """Render an SVG string to PNG via FreeCAD's bundled QtSvg (headless-
    safe). Used by verification; the GUI/report embed the SVG directly."""
    import sys

    from PySide import QtSvg, QtGui, QtCore

    # painting text needs a QGuiApplication; freecadcmd has none, and a
    # headless environment has no display, so use Qt's offscreen platform
    if QtGui.QGuiApplication.instance() is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QtGui.QGuiApplication(sys.argv[:1])

    fd, tmp = tempfile.mkstemp(suffix=".svg")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(svg_text)
        renderer = QtSvg.QSvgRenderer(tmp)
        img = QtGui.QImage(int(width), int(height), QtGui.QImage.Format_ARGB32)
        img.fill(QtGui.QColor("white"))
        painter = QtGui.QPainter(img)
        renderer.render(painter, QtCore.QRectF(0, 0, float(width), float(height)))
        painter.end()
        img.save(out_path)
        return out_path
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
