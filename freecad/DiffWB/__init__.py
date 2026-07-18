# SPDX-License-Identifier: MIT
"""freecad-diff -- show what changed between two versions of a FreeCAD
document (features, parameters, sketch constraints, materials) as text, JSON,
an SVG overlay, or a self-contained HTML report.

Both versions are read into the same canonical JSON model of the document
(feature tree + sketch geometry with constraints + parameters + expressions +
materials), and the diff is a comparison of that model. The importable cores
are pure and headless:

    from freecad.DiffWB import serialize, diff
    old = serialize.serialize_document(doc_a)
    new = serialize.serialize_document(doc_b)
    d = diff.diff_models(old, new)             # -> structured diff dict

See SCHEMA.md for the JSON model. The renderers live in ``render`` (text +
JSON), ``svgdiff`` (visual overlay) and ``htmlreport`` (HTML). The GUI
commands live in ``commands``.
"""
