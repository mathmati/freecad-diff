# SPDX-License-Identifier: MIT
"""Headless smoke test. Builds two versions of a small part, then checks the
diff and each output format. Run under FreeCAD's own interpreter:

    freecadcmd verify/smoke.py

Prints one PASS/FAIL line per check and exits non-zero if any fail.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "freecad"))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

from DiffWB import diff as D  # noqa: E402
from DiffWB import loaders  # noqa: E402
from DiffWB import render as R  # noqa: E402
from DiffWB import svgdiff as V  # noqa: E402
from DiffWB import htmlreport as H  # noqa: E402

fails = []


def check(name, ok):
    print(("PASS" if ok else "FAIL") + ": " + name)
    if not ok:
        fails.append(name)


def build(path, length, extra_box):
    doc = App.newDocument("v")
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "Sketch")
    sk.AttachmentSupport = [(doc.XY_Plane, "")]
    sk.MapMode = "FlatFace"
    for a, b in [((0, 0), (20, 0)), ((20, 0), (20, 10)),
                 ((20, 10), (0, 10)), ((0, 10), (0, 0))]:
        sk.addGeometry(Part.LineSegment(
            App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)), False)
    doc.recompute()
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = length
    if extra_box:
        bx = doc.addObject("Part::Box", "OldBox")
        bx.Placement.Base = App.Vector(30, 0, 0)
    doc.recompute()
    doc.saveAs(path)
    App.closeDocument(doc.Name)


tmp = tempfile.mkdtemp(prefix="fcdiff_smoke_")
v1 = os.path.join(tmp, "v1.FCStd")
v2 = os.path.join(tmp, "v2.FCStd")
build(v1, 15.0, True)
build(v2, 22.0, False)

old_model, old_shapes = loaders.model_and_shapes_from_file(v1)
new_model, new_shapes = loaders.model_and_shapes_from_file(v2)
d = D.diff_models(old_model, new_model)

check("length change detected",
      any(c.get("kind") == "param" for o in d["changed"] for c in o["changes"]))
pad_new = next(o for o in new_model["objects"] if o["id"] == "Pad")
check("unused Direction not serialized (save-state noise)",
      "Direction" not in (pad_new.get("params") or {}))
check("removed object detected", any(o["id"] == "OldBox" for o in d["removed"]))
check("text renders", "Length 15 mm -> 22 mm" in R.diff_to_terminal(d, color="never"))
check("json renders", '"schema"' in R.diff_to_json(d))
csv_out = R.diff_to_csv(d)
check("csv renders",
      csv_out.startswith("status,object,type,field,old,new")
      and "Length" in csv_out)
svg = V.build_overlay_svg(d, old_model, old_shapes, new_model, new_shapes)
check("svg overlay renders", "<svg" in svg and "changed (new)" in svg)
svg_co = V.build_overlay_svg(d, old_model, old_shapes, new_model, new_shapes,
                             callouts=True)
check("callouts are opt-in",
      'text-anchor="middle"' not in svg and 'text-anchor="middle"' in svg_co)
overlays = V.build_overlays(d, old_model, old_shapes, new_model, new_shapes)
html = H.diff_to_html(d, overlays=overlays)
check("html report self-contained",
      "<!DOCTYPE html>" in html and "<svg" in html
      and 'src="http' not in html and 'href="http' not in html)
from DiffWB import volumediff as VD  # noqa: E402
_st, old_c, new_c = V.object_statuses(d, old_model, new_model)
delta = VD.material_delta(old_shapes, new_shapes,
                          old_ids=set(old_c), new_ids=set(new_c))
check("volume delta computes",
      delta.get("ok") and (delta["added_volume"] > 0 or delta["removed_volume"] > 0))
mat = (delta.get("added_shape"), delta.get("removed_shape"))
svg_mat = V.build_overlay_svg(d, old_model, old_shapes, new_model, new_shapes,
                              material=mat)
check("material renders in overlay",
      "fcd-added_material" in svg_mat or "fcd-removed_material" in svg_mat)
check("empty diff is clean", D.is_empty(D.diff_models(old_model, old_model)))

print("\n" + ("ALL SMOKE CHECKS PASSED" if not fails else "FAILED: " + ", ".join(fails)))
sys.stdout.flush()
os._exit(1 if fails else 0)
