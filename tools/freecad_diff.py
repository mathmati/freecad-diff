# SPDX-License-Identifier: MIT
"""Command-line FreeCAD model diff: what changed between two .FCStd files,
semantically (features added/removed, parameters changed, constraints
edited). Renders as terminal text, JSON, a self-contained HTML report, or a
visual overlay SVG.

Run under FreeCAD's own interpreter:

    FCDIFF_OLD=old.FCStd FCDIFF_NEW=new.FCStd freecadcmd freecad_diff.py

or with two positional .FCStd paths. Because freecadcmd owns the real
command line and its own parser rejects some option names even behind the
``--pass`` sentinel, OPTIONS ARE SET VIA ENVIRONMENT VARIABLES -- the one
interface that is reliable under freecadcmd:

    FCDIFF_FORMAT=html FCDIFF_OUTPUT=diff.html \\
        freecadcmd freecad_diff.py old.FCStd new.FCStd
    FCDIFF_FORMAT=svg  FCDIFF_OUTPUT=diff.svg \\
        freecadcmd freecad_diff.py old.FCStd new.FCStd
    FCDIFF_SUMMARY=1   FCDIFF_COLOR=always \\
        freecadcmd freecad_diff.py old.FCStd new.FCStd

Environment options (all optional):
    FCDIFF_FORMAT   text | json | csv | html | svg   (default: text)
    FCDIFF_COLOR    auto | always | never      (text ANSI; default: auto)
    FCDIFF_SUMMARY  set (non-empty) -> object heads only, no detail rows
    FCDIFF_PALETTE  default | okabe-ito        (svg/html visuals)
    FCDIFF_VIEWS    comma list of iso,front,top,right   (svg/html)
    FCDIFF_CALLOUTS set (non-empty) -> number each change with a revision
                     cloud on the overlay (svg/html; off by default)
    FCDIFF_TOLERANCE numeric; ignore dimension/constraint value changes
                     smaller than this (e.g. 0.01), to filter noise
    FCDIFF_VOLUME   set (non-empty) -> also compute added/removed material
                     volume via boolean ops (slower; needs the shapes)
    FCDIFF_OUTPUT   path to write to instead of stdout

The same options are also accepted as ``--pass``-forwarded flags
(--format/--color/--summary/--palette/--view/-o) for callers that can use
them, but the env vars above are the supported interface under freecadcmd.

Git integration, so `git diff` shows semantic changes for model files:

    git config diff.fcstd.command "freecadcmd /path/to/freecad_diff.py"
    echo "*.FCStd diff=fcstd" >> .gitattributes

Git invokes the driver as: <cmd> <path> <old-file> <old-hex> <old-mode>
<new-file> <new-hex> <new-mode>. The script detects that shape (last 7
arguments), handles /dev/null for added/deleted files, and ALWAYS exits 0
in git-driver mode (git treats a nonzero driver exit as fatal). Outside
git-driver mode the exit code is a contract for scripts: 0 = no semantic
changes, 1 = differences found, 2 = error.
"""
import functools
import os
import sys

print = functools.partial(print, flush=True)

_NULL_PATHS = ("/dev/null", "nul", "NUL")
_FORMATS = ("text", "json", "csv", "html", "svg")

#: set once main() knows whether it was invoked as git's external-diff
#: driver, so the top-level crash handler can still exit 0 in that mode
#: (git aborts the whole command on a nonzero driver exit).
_GIT_MODE = False


def _find_addon_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)  # tools/ -> addon root


def _parse_opts(argv):
    """Pull recognized --flags out of argv, returning (opts, positionals).
    Unknown args are left as positionals so git-driver detection still sees
    its seven slots (git never passes our flags).

    freecadcmd owns the real command line, so interactive users forward
    flags to the script with FreeCAD's ``--pass`` sentinel:

        freecadcmd freecad_diff.py old.FCStd new.FCStd --pass --format html

    Defaults are seeded from FCDIFF_* env vars for scripting without flags."""
    env = os.environ.get

    def _flag(name):
        v = (env(name) or "").strip().lower()
        return v not in ("", "0", "false", "no", "off")

    opts = {"format": env("FCDIFF_FORMAT", "text"),
            "color": env("FCDIFF_COLOR", "auto"),
            "summary": _flag("FCDIFF_SUMMARY"),
            "palette": env("FCDIFF_PALETTE", "default"),
            "views": tuple((env("FCDIFF_VIEWS") or "iso,front,top").split(",")),
            "callouts": _flag("FCDIFF_CALLOUTS"),
            "tolerance": env("FCDIFF_TOLERANCE"),
            "volume": _flag("FCDIFF_VOLUME"),
            "output": env("FCDIFF_OUTPUT")}
    pos = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--pass":  # freecadcmd's forward-to-script sentinel, not ours
            i += 1; continue
        if a in ("--format", "-f") and i + 1 < len(argv):
            opts["format"] = argv[i + 1]; i += 2; continue
        if a.startswith("--format="):
            opts["format"] = a.split("=", 1)[1]; i += 1; continue
        if a == "--color" and i + 1 < len(argv):
            opts["color"] = argv[i + 1]; i += 2; continue
        if a.startswith("--color="):
            opts["color"] = a.split("=", 1)[1]; i += 1; continue
        if a in ("--summary", "--brief"):
            opts["summary"] = True; i += 1; continue
        if a in ("--all", "--full"):
            opts["summary"] = False; i += 1; continue
        if a == "--callouts":
            opts["callouts"] = True; i += 1; continue
        if a == "--palette" and i + 1 < len(argv):
            opts["palette"] = argv[i + 1]; i += 2; continue
        if a.startswith("--palette="):
            opts["palette"] = a.split("=", 1)[1]; i += 1; continue
        if a == "--view" and i + 1 < len(argv):
            opts["views"] = tuple(argv[i + 1].split(",")); i += 2; continue
        if a.startswith("--view="):
            opts["views"] = tuple(a.split("=", 1)[1].split(",")); i += 1; continue
        if a in ("-o", "--output") and i + 1 < len(argv):
            opts["output"] = argv[i + 1]; i += 2; continue
        if a.startswith("--output="):
            opts["output"] = a.split("=", 1)[1]; i += 1; continue
        pos.append(a)
        i += 1
    return opts, pos


def _pick_paths(pos):
    """Returns (old_path, new_path, git_mode) from env or positionals."""
    old = os.environ.get("FCDIFF_OLD")
    new = os.environ.get("FCDIFF_NEW")
    if old and new:
        return old, new, False
    # git external-diff shape: ... path old-file old-hex old-mode new-file
    # new-hex new-mode  (freecadcmd keeps the script path in argv, so take
    # the LAST seven arguments regardless of what precedes them)
    if len(pos) >= 7:
        t = pos[-7:]
        return t[1], t[4], True
    cand = [a for a in pos if a.lower().endswith((".fcstd", ".fcstd1"))]
    if len(cand) >= 2:
        return cand[0], cand[1], False
    return None, None, False


def _empty_model(label):
    return {"schema": "freecad-model-context", "schema_version": "1.0",
            "document": {"name": label, "label": label}, "objects": []}


def _load(loaders, path, side_label, want_shapes):
    """Load a model (and optionally shapes), treating git's /dev/null
    (added/deleted file) as an empty document."""
    if path in _NULL_PATHS or os.path.basename(path or "") in _NULL_PATHS:
        m = _empty_model(side_label)
        return (m, {}) if want_shapes else (m, None)
    if want_shapes:
        return loaders.model_and_shapes_from_file(path, want_shapes=True)
    return loaders.model_from_file(path), None


def main():
    global _GIT_MODE
    opts, pos = _parse_opts(sys.argv[1:])
    old_path, new_path, git_mode = _pick_paths(pos)
    _GIT_MODE = git_mode  # so the top-level crash handler honors the contract
    if not old_path or not new_path:
        print("usage: FCDIFF_OLD=a.FCStd FCDIFF_NEW=b.FCStd freecadcmd "
              "freecad_diff.py   (or pass two .FCStd paths as arguments)")
        return 0 if git_mode else 2
    fmt = opts["format"]
    if fmt not in _FORMATS:
        print("freecad_diff: unknown --format %r (use %s)"
              % (fmt, "|".join(_FORMATS)))
        return 0 if git_mode else 2

    sys.path.insert(0, os.path.join(_find_addon_root(), "freecad"))
    try:
        from DiffWB import diff as D
        from DiffWB import loaders
    except ImportError:
        # installed as an addon: the normal namespaced import works instead
        from freecad.DiffWB import diff as D
        from freecad.DiffWB import loaders

    # HTML and SVG need the geometry; text/json do not -- unless a volume
    # diff was asked for, which needs the shapes regardless of format.
    want_shapes = fmt in ("html", "svg") or opts["volume"]
    old_model, old_shapes = _load(loaders, old_path, "(absent)", want_shapes)
    new_model, new_shapes = _load(loaders, new_path, "(absent)", want_shapes)

    tol = None
    if opts.get("tolerance"):
        try:
            tol = float(opts["tolerance"])
        except ValueError:
            sys.stderr.write("freecad_diff: ignoring bad FCDIFF_TOLERANCE %r\n"
                             % opts["tolerance"])
    d = D.diff_models(old_model, new_model, tolerance=tol)
    if opts["volume"] and old_shapes is not None and new_shapes is not None:
        try:
            from DiffWB import volumediff as VD
            from DiffWB import svgdiff as VSV
        except ImportError:
            from freecad.DiffWB import volumediff as VD
            from freecad.DiffWB import svgdiff as VSV
        # only fuse top-level shape carriers, not intermediate features
        _st, old_c, new_c = VSV.object_statuses(d, old_model, new_model)
        delta = VD.material_delta(old_shapes, new_shapes,
                                  old_ids=set(old_c), new_ids=set(new_c))
        summary = VD.volume_summary(delta)
        if summary:
            d["geometry"] = summary
            # keep the boolean shapes so the visual overlay can outline the
            # actual added/removed material chunk
            opts["_material"] = (delta.get("added_shape"),
                                 delta.get("removed_shape"))
    text = _render(D, d, opts, old_model, old_shapes, new_model, new_shapes)

    # In git-driver mode git reads the diff from stdout; honoring an ambient
    # FCDIFF_OUTPUT there would silently write to a file and hand git an
    # empty diff, so git mode always writes to stdout.
    if opts["output"] and not git_mode:
        with open(opts["output"], "w", encoding="utf-8", newline="") as f:
            f.write(text)
        print("wrote %s (%s)" % (opts["output"], fmt))
    else:
        sys.stdout.write(text)

    if git_mode:
        return 0  # git treats nonzero driver exit as fatal
    return 0 if D.is_empty(d) else 1


def _render(D, d, opts, old_model, old_shapes, new_model, new_shapes):
    fmt = opts["format"]
    try:
        from DiffWB import render as R
    except ImportError:
        from freecad.DiffWB import render as R
    if fmt == "json":
        return R.diff_to_json(d)
    if fmt == "csv":
        return R.diff_to_csv(d)
    if fmt == "text":
        level = "summary" if opts["summary"] else "normal"
        return R.diff_to_terminal(d, color=opts["color"], level=level)
    # visual formats
    try:
        from DiffWB import svgdiff as V
    except ImportError:
        from freecad.DiffWB import svgdiff as V
    material = opts.get("_material")
    if fmt == "svg":
        view = opts["views"][0] if opts["views"] else "iso"
        return V.build_overlay_svg(d, old_model, old_shapes or {},
                                   new_model, new_shapes or {},
                                   direction=view, palette=opts["palette"],
                                   callouts=opts.get("callouts", False),
                                   material=material,
                                   title="%s -> %s" % (
                                       old_model["document"]["label"],
                                       new_model["document"]["label"]))
    # html
    try:
        from DiffWB import htmlreport as H
    except ImportError:
        from freecad.DiffWB import htmlreport as H
    overlays = V.build_overlays(d, old_model, old_shapes or {},
                                new_model, new_shapes or {},
                                views=opts["views"], palette=opts["palette"],
                                callouts=opts.get("callouts", False),
                                material=material)
    return H.diff_to_html(d, overlays=overlays)


# NB: freecadcmd does not execute scripts with __name__ == "__main__", so
# run unconditionally. os._exit propagates the exit code without tripping
# over freecadcmd's SystemExit handling; the tool is read-only so skipping
# FreeCAD's teardown is safe. ANY uncaught error must exit 2, never 0 --
# a crash that reads as "no changes" is the worst failure mode a diff tool
# can have.
try:
    rc = main()
except Exception as exc:  # noqa: BLE001
    import traceback
    sys.stderr.write(traceback.format_exc())
    sys.stderr.write("freecad_diff: error: %s\n" % exc)
    # In git-driver mode a nonzero exit aborts the entire git command, so we
    # keep the "always exit 0 in git mode" contract even on a crash -- the
    # diagnostic goes to stderr, git just sees an empty diff for that file.
    rc = 0 if _GIT_MODE else 2
sys.stdout.flush()
sys.stderr.flush()
os._exit(rc)
