# SPDX-License-Identifier: MIT
"""Load a saved FreeCAD document into a model-context dict without touching
any document the user has open.

The trick: FreeCAD keys open documents by file path, so opening a file the
user is already editing (or opening the same path twice, as a two-version
diff of one file needs) would collide. We therefore copy the file to a
temporary path first, open THAT, serialize, and close it again. Works under
both the GUI and plain freecadcmd.
"""
import os
import shutil
import tempfile

import FreeCAD as App

from . import serialize as S


def model_from_file(path):
    """Open a saved .FCStd non-invasively and return its model-context dict
    (the document label is set to the original file's basename so diffs read
    naturally). Raises IOError/OSError on unreadable paths."""
    return model_and_shapes_from_file(path, want_shapes=False)[0]


def model_and_shapes_from_file(path, want_shapes=True):
    """Like :func:`model_from_file`, but also returns ``{object_id:
    Part.Shape}`` for every shape-bearing object -- detached copies that
    survive the document being closed, for the visual diff overlay."""
    if not os.path.isfile(path):
        raise IOError("No such file: %s" % path)
    tmpdir = tempfile.mkdtemp(prefix="modelcontext_")
    tmp = os.path.join(tmpdir, os.path.basename(path))
    doc = None
    try:
        shutil.copy2(path, tmp)
        try:
            doc = App.openDocument(tmp, hidden=True)
        except TypeError:  # FreeCAD < 0.19: no hidden kwarg
            doc = App.openDocument(tmp)
        model = S.serialize_document(doc)
        model["document"]["label"] = os.path.basename(path)
        shapes = {}
        if want_shapes:
            shapes = shapes_from_document(doc)
        return model, shapes
    finally:
        if doc is not None:
            try:
                App.closeDocument(doc.Name)
            except Exception:
                pass
        shutil.rmtree(tmpdir, ignore_errors=True)


def shapes_from_document(doc):
    """Detached ``{object_id: Part.Shape}`` copies for every object in
    ``doc`` carrying a non-empty shape (copies survive document close)."""
    shapes = {}
    for obj in doc.Objects:
        sh = getattr(obj, "Shape", None)
        if sh is None:
            continue
        try:
            if sh.isNull():
                continue
            shapes[obj.Name] = sh.copy()
        except Exception:
            continue
    return shapes
