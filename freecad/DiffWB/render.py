# SPDX-License-Identifier: MIT
"""Presentation renderers for the semantic model diff.

Two audiences, one structured diff (see diff.py / SCHEMA.md appendix):

* ``diff_to_terminal(diff, color=...)`` -- terraform-plan-style terminal
  text: a counts summary line, changes grouped per object, aligned
  ``old -> new`` value lines, ANSI color with proper fallbacks
  (``color`` = "auto" respects NO_COLOR and only colors real TTYs).
* ``diff_to_html(...)`` lives in htmlreport.py (self-contained report that
  embeds the visual overlay from svgdiff.py).

Color is never the only channel: the +/~/- glyphs always remain, so the
plain-text rendering degrades losslessly.
"""
import os
import sys

# ANSI SGR codes (kept to the widely-safe 16-color set; bright variants
# render acceptably on both dark and light terminals)
_SGR = {
    "green": "32", "red": "31", "yellow": "33", "cyan": "36",
    "bold": "1", "dim": "2",
}


def _want_color(mode):
    if mode == "always":
        return True
    if mode == "never":
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


class _Paint(object):
    def __init__(self, enabled):
        self.enabled = enabled

    def __call__(self, text, *styles):
        if not self.enabled or not styles:
            return text
        codes = ";".join(_SGR[s] for s in styles)
        return "\x1b[%sm%s\x1b[0m" % (codes, text)


def _summary_counts(diff):
    n_add = len(diff.get("added", []))
    n_rem = len(diff.get("removed", []))
    n_chg = len(diff.get("changed", []))
    return n_add, n_chg, n_rem


def summary_line(diff):
    n_add, n_chg, n_rem = _summary_counts(diff)
    if not (n_add or n_chg or n_rem):
        return "No semantic changes."
    return "%d to add, %d to change, %d to remove." % (n_add, n_chg, n_rem)


def _object_head(o, glyph):
    label = o.get("label") or o["id"]
    t = o.get("type") or ""
    return "%s %s  [%s]" % (glyph, label, t) if t else "%s %s" % (glyph, label)


def diff_to_json(diff, indent=2):
    """Render the structured diff as pretty JSON (the diff dict is already
    the canonical machine format -- see SCHEMA.md). Ends in a newline."""
    import json
    return json.dumps(diff, indent=indent, sort_keys=False) + "\n"


def diff_to_terminal(diff, color="auto", level="normal"):
    """Render the structured diff as grouped, aligned, optionally colored
    terminal text. Returns a string ending in a newline.

    ``level``: "normal" shows every per-change line; "summary" shows only the
    counts line and one head per touched object (no detail rows)."""
    p = _Paint(_want_color(color))
    from . import diff as D
    summary = (level == "summary")

    old_l = diff.get("old", {}).get("label") or diff.get("old", {}).get("name", "old")
    new_l = diff.get("new", {}).get("label") or diff.get("new", {}).get("name", "new")
    lines = []
    lines.append(p("Model diff: %s -> %s" % (old_l, new_l), "bold"))

    n_add, n_chg, n_rem = _summary_counts(diff)
    if not (n_add or n_chg or n_rem):
        lines.append("No semantic changes.")
        return "\n".join(lines) + "\n"
    lines.append(p("%d to add" % n_add, "green") + ", "
                 + p("%d to change" % n_chg, "yellow") + ", "
                 + p("%d to remove" % n_rem, "red") + ".")
    lines.append("")

    for o in diff.get("added", []):
        lines.append(p(_object_head(o, "+"), "green", "bold"))
    for o in diff.get("removed", []):
        lines.append(p(_object_head(o, "-"), "red", "bold"))
    if diff.get("added") or diff.get("removed"):
        lines.append("")

    for o in diff.get("changed", []):
        lines.append(p(_object_head(o, "~"), "yellow", "bold"))
        if summary:
            continue
        # reuse diff.py's per-change wording, re-grouped under the object
        # head and colored by leading glyph
        for raw in D._change_lines(o):
            glyph = raw[:1]
            body = raw[2:]
            # strip the leading object name diff.py bakes in ("Name: ...")
            label = (o.get("label") or o["id"]) + ": "
            if body.startswith(label):
                body = body[len(label):]
            if glyph == "+":
                lines.append("    " + p("+ " + body, "green"))
            elif glyph == "-":
                lines.append("    " + p("- " + body, "red"))
            else:
                lines.append("    " + p("~ ", "yellow") + _color_arrow(body, p))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _color_arrow(body, p):
    """Color 'X -> Y' value transitions: the old side is dimmed, the new side
    stays at full intensity (terraform-plan convention). The change lines are
    free text, so we cannot reliably split a property-name prefix from the old
    value -- dimming the whole old side is correct and unambiguous."""
    if " -> " in body:
        head, _, rest = body.partition(" -> ")
        return "%s -> %s" % (p(head, "dim"), rest)
    return body
