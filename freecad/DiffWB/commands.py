# SPDX-License-Identifier: MIT
"""GUI commands: diff the active document against its saved version or against
another file, and export/copy the underlying semantic model. Thin wrappers
over the headless ``diff`` / ``serialize`` cores -- all the real work is
testable without a GUI.
"""
import json
import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui, QtWidgets

from . import diff as D
from . import loaders
from . import serialize as S

_ICON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "Resources", "Icons")


def _icon(name="freecaddiff"):
    return os.path.join(_ICON_DIR, name + ".svg")


def _status(msg):
    try:
        Gui.getMainWindow().statusBar().showMessage(msg, 6000)
    except Exception:
        pass


def _output_base(doc):
    """Where to write the export: next to the saved .FCStd, else the user's
    home dir (surfaced in the status message, never silent)."""
    if doc.FileName:
        return os.path.splitext(doc.FileName)[0]
    return os.path.join(os.path.expanduser("~"), doc.Name)


class _ExportCommand(object):
    def GetResources(self):
        return {"MenuText": "Export Model (JSON + Markdown)",
                "ToolTip": ("Write the active document's semantic model "
                            "(feature tree + sketch constraints + parameters + "
                            "materials) to JSON + Markdown next to the document."),
                "Pixmap": _icon("export")}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        doc = App.ActiveDocument
        if doc is None:
            return
        model = S.serialize_document(doc)
        base = _output_base(doc)
        try:
            with open(base + ".modelcontext.json", "w", encoding="utf-8") as f:
                json.dump(model, f, indent=2, ensure_ascii=False)
            with open(base + ".modelcontext.md", "w", encoding="utf-8") as f:
                f.write(S.to_markdown(model))
        except Exception as exc:  # noqa: BLE001
            _status("FreeCAD Diff: export failed (%s)" % exc)
            return
        _status("Model written to %s.modelcontext.json / .md" % base)


class _CopyMarkdownCommand(object):
    def GetResources(self):
        return {"MenuText": "Copy Model as Markdown",
                "ToolTip": ("Copy the active document's semantic model as "
                            "Markdown to the clipboard, ready to paste into an "
                            "AI chat as grounding context."),
                "Pixmap": _icon("copy")}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        doc = App.ActiveDocument
        if doc is None:
            return
        md = S.to_markdown(S.serialize_document(doc))
        try:
            QtWidgets.QApplication.clipboard().setText(md)
            _status("Model copied to clipboard as Markdown.")
        except Exception as exc:  # noqa: BLE001
            _status("FreeCAD Diff: copy failed (%s)" % exc)


def _diff_to_qt_html(diff):
    """A small rich-text fragment for Qt's rich-text engine (a subset of
    HTML/CSS -- no flexbox/sticky, so this is deliberately simple: colored
    +/~/- rows). The full self-contained report is what Export HTML writes."""
    import html as _html

    def e(s):
        return _html.escape(str(s))

    old_l = diff.get("old", {}).get("label") or "old"
    new_l = diff.get("new", {}).get("label") or "new"
    parts = ['<div style="font-family:sans-serif">']
    parts.append('<p><b>Model diff:</b> %s &rarr; %s</p>' % (e(old_l), e(new_l)))
    if D.is_empty(diff):
        parts.append('<p style="color:#57606a">No semantic changes.</p></div>')
        return "".join(parts)
    n_add = len(diff.get("added", []))
    n_chg = len(diff.get("changed", []))
    n_rem = len(diff.get("removed", []))
    parts.append('<p><span style="color:#1a7f37">+%d added</span> &nbsp; '
                 '<span style="color:#9a6700">~%d changed</span> &nbsp; '
                 '<span style="color:#cf222e">&minus;%d removed</span></p>'
                 % (n_add, n_chg, n_rem))
    parts.append('<pre style="font-family:monospace">')
    for c in diff.get("document_changes", []):
        parts.append('<span style="color:#9a6700">~ document: %s %s -&gt; %s</span>\n'
                     % (e(c["name"]), e(c.get("old") or "(unset)"),
                        e(c.get("new") or "(unset)")))
    for o in diff.get("added", []):
        parts.append('<span style="color:#1a7f37">+ %s (%s)</span>\n'
                     % (e(o.get("label") or o["id"]), e(o.get("type"))))
    for o in diff.get("removed", []):
        parts.append('<span style="color:#cf222e">- %s (%s)</span>\n'
                     % (e(o.get("label") or o["id"]), e(o.get("type"))))
    for o in diff.get("changed", []):
        parts.append('<span style="color:#9a6700">~ %s</span>\n'
                     % e(o.get("label") or o["id"]))
        for raw in D._change_lines(o):
            g = raw[:1]
            col = {"+": "#1a7f37", "-": "#cf222e"}.get(g, "#57606a")
            parts.append('    <span style="color:%s">%s</span>\n' % (col, e(raw)))
    parts.append('</pre></div>')
    return "".join(parts)


def _show_diff_dialog(title, diff, on_export_html=None):
    dlg = QtWidgets.QDialog(Gui.getMainWindow())
    dlg.setWindowTitle(title)
    dlg.resize(680, 520)
    lay = QtWidgets.QVBoxLayout(dlg)
    view = QtWidgets.QTextBrowser(dlg)
    view.setOpenExternalLinks(True)
    view.setHtml(_diff_to_qt_html(diff))
    lay.addWidget(view)
    row = QtWidgets.QHBoxLayout()
    callout_cb = None
    material_cb = None
    if on_export_html is not None:
        callout_cb = QtWidgets.QCheckBox("Number changes (revision clouds)", dlg)
        callout_cb.setToolTip("Circle and number each added/removed/changed "
                              "object on the visual overlay in the HTML report.")
        row.addWidget(callout_cb)
        material_cb = QtWidgets.QCheckBox("Show added/removed material", dlg)
        material_cb.setToolTip("Compute the actual material added and removed "
                               "(boolean volume) and draw it. Slower.")
        row.addWidget(material_cb)
    row.addStretch(1)
    if on_export_html is not None:
        html_btn = QtWidgets.QPushButton("Export HTML Report...", dlg)
        html_btn.clicked.connect(
            lambda: on_export_html(callout_cb.isChecked(),
                                   material_cb.isChecked()))
        row.addWidget(html_btn)
    copy_btn = QtWidgets.QPushButton("Copy Text", dlg)
    close_btn = QtWidgets.QPushButton("Close", dlg)
    row.addWidget(copy_btn)
    row.addWidget(close_btn)
    lay.addLayout(row)
    copy_btn.clicked.connect(
        lambda: QtWidgets.QApplication.clipboard().setText(D.diff_to_text(diff)))
    close_btn.clicked.connect(dlg.accept)
    dlg.exec_()


def _export_html_report(diff, old_model, old_shapes, new_model, new_shapes,
                        default_name, callouts=False, volume=False):
    """Save-dialog -> write the self-contained HTML report (with visual
    overlay) -> open it in the user's browser."""
    from . import htmlreport as H
    from . import svgdiff as V

    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        Gui.getMainWindow(), "Export HTML diff report",
        default_name, "HTML report (*.html)")
    if not path:
        return
    if not path.lower().endswith(".html"):
        path += ".html"
    try:
        material = None
        # the dialog reuses one diff dict across exports, so clear any volume
        # section from a previous export before deciding whether to recompute
        diff.pop("geometry", None)
        if volume and old_shapes is not None and new_shapes is not None:
            from . import volumediff as VD
            _st, old_c, new_c = V.object_statuses(diff, old_model, new_model)
            delta = VD.material_delta(old_shapes, new_shapes,
                                      old_ids=set(old_c), new_ids=set(new_c))
            summary = VD.volume_summary(delta)
            if summary:
                diff["geometry"] = summary
                material = (delta.get("added_shape"), delta.get("removed_shape"))
        overlays = {}
        if old_shapes is not None and new_shapes is not None:
            overlays = V.build_overlays(diff, old_model, old_shapes,
                                        new_model, new_shapes,
                                        callouts=callouts, material=material)
        html = H.diff_to_html(diff, overlays=overlays)
        # the report contains non-ASCII glyphs (arrows, minus sign); write
        # UTF-8 explicitly so it does not crash on a non-UTF-8 locale
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(html)
    except Exception as exc:  # noqa: BLE001
        _status("FreeCAD Diff: HTML export failed (%s)" % exc)
        return
    _status("Report written to %s" % path)
    try:
        from PySide import QtCore
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
    except Exception:
        pass


class _DiffSavedCommand(object):
    def GetResources(self):
        return {"MenuText": "Diff Against Saved",
                "ToolTip": ("Show what changed in the active document since "
                            "it was last saved: features added or removed, "
                            "parameters changed, constraints edited."),
                "Pixmap": _icon("diff_saved")}

    def IsActive(self):
        doc = App.ActiveDocument
        return doc is not None and bool(doc.FileName)

    def Activated(self):
        doc = App.ActiveDocument
        if doc is None or not doc.FileName:
            return
        try:
            old_model, old_shapes = loaders.model_and_shapes_from_file(
                doc.FileName, want_shapes=True)
        except Exception as exc:  # noqa: BLE001
            _status("FreeCAD Diff: could not read saved file (%s)" % exc)
            return
        new_model = S.serialize_document(doc)
        new_model["document"]["label"] = "current (unsaved)"
        new_shapes = loaders.shapes_from_document(doc)
        d = D.diff_models(old_model, new_model)
        base = os.path.splitext(doc.FileName)[0] + ".diff.html"
        _show_diff_dialog(
            "Model diff: saved vs current", d,
            on_export_html=lambda co, vol: _export_html_report(
                d, old_model, old_shapes, new_model, new_shapes, base,
                callouts=co, volume=vol))


class _DiffFilesCommand(object):
    def GetResources(self):
        return {"MenuText": "Diff Two Files...",
                "ToolTip": ("Pick two saved FreeCAD documents and show the "
                            "semantic differences between them."),
                "Pixmap": _icon("diff_files")}

    def IsActive(self):
        return True

    def Activated(self):
        filt = "FreeCAD documents (*.FCStd)"
        a, _ = QtWidgets.QFileDialog.getOpenFileName(
            Gui.getMainWindow(), "Older version", "", filt)
        if not a:
            return
        b, _ = QtWidgets.QFileDialog.getOpenFileName(
            Gui.getMainWindow(), "Newer version", os.path.dirname(a), filt)
        if not b:
            return
        try:
            old_model, old_shapes = loaders.model_and_shapes_from_file(a, want_shapes=True)
            new_model, new_shapes = loaders.model_and_shapes_from_file(b, want_shapes=True)
        except Exception as exc:  # noqa: BLE001
            _status("FreeCAD Diff: diff failed (%s)" % exc)
            return
        d = D.diff_models(old_model, new_model)
        base = os.path.splitext(b)[0] + ".diff.html"
        _show_diff_dialog(
            "Model diff: %s -> %s" % (os.path.basename(a), os.path.basename(b)),
            d, on_export_html=lambda co, vol: _export_html_report(
                d, old_model, old_shapes, new_model, new_shapes, base,
                callouts=co, volume=vol))


def register():
    Gui.addCommand("Diff_Export", _ExportCommand())
    Gui.addCommand("Diff_CopyMarkdown", _CopyMarkdownCommand())
    Gui.addCommand("Diff_DiffSaved", _DiffSavedCommand())
    Gui.addCommand("Diff_DiffFiles", _DiffFilesCommand())
