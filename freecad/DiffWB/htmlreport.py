# SPDX-License-Identifier: MIT
"""Self-contained HTML report for the semantic model diff.

One file, no external assets, no JavaScript required to read it: a sticky
header with add/change/remove count chips, an optional embedded visual
overlay (the SVG from :mod:`svgdiff`, inlined), then one ``<details>`` block
per changed/added/removed object with aligned ``old -> new`` rows. Unchanged
context is not listed (the diff already excludes it). Dark and light are both
honored via ``prefers-color-scheme``, with a small no-framework toggle that
degrades to the OS preference when scripting is off.

The report is built purely from the structured diff (see diff.py) plus, if
provided, one or more overlay SVG strings. It never imports FreeCAD, so it
runs anywhere the diff does; callers that want the visual pass rendered SVGs
in via ``overlays=``.
"""

from . import diff as D

_KIND_GLYPH = {"+": "add", "-": "rem", "~": "chg"}


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _anchor(oid):
    # stable, id-safe anchor for deep links
    return "obj-" + "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(oid))


def _split_arrow(body):
    """('Length 15 mm', '20 mm') from 'Length 15 mm -> 20 mm', else (body, None)."""
    if " -> " in body:
        head, _, new = body.partition(" -> ")
        return head, new
    return body, None


def _row(glyph, body):
    kind = _KIND_GLYPH.get(glyph, "chg")
    old, new = _split_arrow(body)
    if new is not None:
        return ('<tr class="%s"><td class="g">%s</td>'
                '<td class="old">%s</td><td class="arrow">&rarr;</td>'
                '<td class="new">%s</td></tr>'
                % (kind, glyph, _esc(old), _esc(new)))
    return ('<tr class="%s"><td class="g">%s</td>'
            '<td class="single" colspan="3">%s</td></tr>'
            % (kind, glyph, _esc(body)))


def _object_block(o, glyph, kind, open_):
    label = o.get("label") or o["id"]
    typ = o.get("type") or ""
    anchor = _anchor(o["id"])
    head = ('<summary><span class="glyph %s">%s</span> '
            '<span class="label">%s</span>'
            % (kind, glyph, _esc(label)))
    if typ:
        head += ' <span class="type">%s</span>' % _esc(typ)
    head += '</summary>'

    rows = []
    if glyph == "+":
        rows.append(_row("+", "added (%s)" % (typ or "object")))
    elif glyph == "-":
        rows.append(_row("-", "removed (%s)" % (typ or "object")))
    else:
        for raw in D._change_lines(o):
            g = raw[:1]
            b = raw[2:]
            pre = (o.get("label") or o["id"]) + ": "
            if b.startswith(pre):
                b = b[len(pre):]
            rows.append(_row(g, b))

    return ('<details id="%s" class="obj %s"%s>%s'
            '<table class="changes">%s</table></details>'
            % (anchor, kind, " open" if open_ else "", head, "".join(rows)))


_CSS = """
:root{--bg:#ffffff;--fg:#1f2328;--muted:#656d76;--line:#d0d7de;
--card:#f6f8fa;--add:#1a7f37;--rem:#cf222e;--chg:#9a6700;--new:#0969da;
--addbg:#dafbe1;--rembg:#ffebe9;--chgbg:#fff8c5;--headbg:#ffffffee}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;--line:#30363d;--card:#161b22;
--add:#3fb950;--rem:#f85149;--chg:#d29922;--new:#58a6ff;
--addbg:#12261e;--rembg:#25171c;--chgbg:#272115;--headbg:#0d1117ee}}
:root[data-theme=dark]{--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;
--line:#30363d;--card:#161b22;--add:#3fb950;--rem:#f85149;--chg:#d29922;
--new:#58a6ff;--addbg:#12261e;--rembg:#25171c;--chgbg:#272115;--headbg:#0d1117ee}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--headbg);
backdrop-filter:blur(6px);border-bottom:1px solid var(--line);
padding:14px 20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
header h1{font-size:16px;margin:0;font-weight:600}
header .sub{color:var(--muted);font-size:13px}
.chips{display:flex;gap:8px;margin-left:auto}
.chip{font-size:12px;font-weight:600;padding:2px 10px;border-radius:999px;
border:1px solid transparent}
.chip.add{color:var(--add);background:var(--addbg);border-color:var(--add)}
.chip.rem{color:var(--rem);background:var(--rembg);border-color:var(--rem)}
.chip.chg{color:var(--chg);background:var(--chgbg);border-color:var(--chg)}
.chip.zero{opacity:.4}
main{max-width:1000px;margin:0 auto;padding:20px}
.viz{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:10px;margin-bottom:20px;text-align:center;overflow-x:auto}
.viz svg{max-width:100%;height:auto}
.viz .tabs{display:flex;gap:6px;justify-content:center;margin-bottom:8px;
flex-wrap:wrap}
.viz .tabs button{font:inherit;font-size:12px;padding:3px 12px;cursor:pointer;
border:1px solid var(--line);background:var(--bg);color:var(--fg);
border-radius:6px}
.viz .tabs button[aria-selected=true]{background:var(--new);color:#fff;
border-color:var(--new)}
.viz .frame{display:none}.viz .frame.on{display:block}
.wipe{display:flex;align-items:center;justify-content:center;gap:10px;
margin-bottom:8px;font-size:12px;color:var(--muted)}
.wipe input{flex:0 1 260px}
.fcd-changed_old,.fcd-removed{opacity:var(--fcd-old,1)}
.fcd-changed_new,.fcd-added{opacity:var(--fcd-new,1)}
.material{display:flex;gap:14px;align-items:center;flex-wrap:wrap;
background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:10px 14px;margin-bottom:16px;font-size:13px}
.material .mlabel{font-weight:600}
.material .madd{color:var(--add)}.material .mrem{color:var(--rem)}
.material .mnet{color:var(--muted);margin-left:auto;
font-family:ui-monospace,Menlo,Consolas,monospace}
details.obj{border:1px solid var(--line);border-radius:8px;margin-bottom:10px;
background:var(--card);overflow:hidden}
details.obj>summary{cursor:pointer;padding:10px 14px;font-weight:600;
list-style:none;display:flex;align-items:center;gap:8px}
details.obj>summary::-webkit-details-marker{display:none}
details.obj>summary::before{content:"\\25B8";color:var(--muted);
font-size:11px;transition:transform .12s}
details.obj[open]>summary::before{transform:rotate(90deg)}
.glyph{display:inline-block;width:20px;height:20px;line-height:20px;
text-align:center;border-radius:5px;font-weight:700;font-size:13px}
.glyph.add{color:var(--add);background:var(--addbg)}
.glyph.rem{color:var(--rem);background:var(--rembg)}
.glyph.chg{color:var(--chg);background:var(--chgbg)}
.label{font-weight:600}.type{color:var(--muted);font-weight:400;font-size:12px}
table.changes{width:100%;border-collapse:collapse;border-top:1px solid var(--line)}
table.changes td{padding:5px 10px;vertical-align:top;
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}
table.changes td.g{width:20px;text-align:center;font-weight:700;color:var(--muted)}
tr.add td.g{color:var(--add)}tr.rem td.g{color:var(--rem)}tr.chg td.g{color:var(--chg)}
td.old{color:var(--muted);text-decoration:line-through;
text-decoration-color:var(--rem);width:42%}
td.arrow{color:var(--muted);width:20px;text-align:center}
td.new{color:var(--new);width:42%}
td.single{color:var(--fg)}
tr.add td.single{color:var(--add)}tr.rem td.single{color:var(--rem)}
.empty{text-align:center;color:var(--muted);padding:60px 20px;font-size:15px}
footer{max-width:1000px;margin:0 auto;padding:0 20px 40px;color:var(--muted);
font-size:12px}
.tbtn{font:inherit;font-size:12px;padding:3px 10px;cursor:pointer;
border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:6px}
"""

_JS = """
(function(){
var root=document.documentElement,KEY="mcdiff-theme";
try{var s=localStorage.getItem(KEY);if(s)root.setAttribute("data-theme",s);}catch(e){}
var b=document.getElementById("themebtn");
if(b)b.onclick=function(){
 var cur=root.getAttribute("data-theme");
 var mql=window.matchMedia&&window.matchMedia("(prefers-color-scheme:dark)").matches;
 var next=(cur?cur==="dark":mql)?"light":"dark";
 root.setAttribute("data-theme",next);
 try{localStorage.setItem(KEY,next);}catch(e){}
};
var sl=document.querySelector(".fcd-slider");
if(sl){var apply=function(){
 var t=+sl.value;
 root.style.setProperty("--fcd-old", Math.min(1,(100-t)/50).toFixed(3));
 root.style.setProperty("--fcd-new", Math.min(1,t/50).toFixed(3));
};sl.addEventListener("input",apply);apply();}
var tabs=document.querySelectorAll(".viz .tabs button");
for(var i=0;i<tabs.length;i++){tabs[i].onclick=function(){
 var v=this.getAttribute("data-view"),wrap=this.closest(".viz");
 var bs=wrap.querySelectorAll(".tabs button");
 for(var j=0;j<bs.length;j++)bs[j].setAttribute("aria-selected",bs[j]===this);
 var fs=wrap.querySelectorAll(".frame");
 for(var k=0;k<fs.length;k++)fs[k].className="frame"+(fs[k].getAttribute("data-view")===v?" on":"");
};}
})();
"""


def diff_to_html(diff, overlays=None, title=None):
    """Render the structured ``diff`` as a single self-contained HTML string.

    ``overlays``: optional ``{view_name: svg_string}`` (or a single SVG
    string) from :func:`svgdiff.build_overlay_svg`. When more than one is
    given the report shows view tabs; with scripting off the first is shown.
    """
    old_l = diff.get("old", {}).get("label") or diff.get("old", {}).get("name", "old")
    new_l = diff.get("new", {}).get("label") or diff.get("new", {}).get("name", "new")
    n_add = len(diff.get("added", []))
    n_rem = len(diff.get("removed", []))
    n_chg = len(diff.get("changed", []))
    doc_title = title or ("Model diff: %s → %s" % (old_l, new_l))

    if isinstance(overlays, str):
        overlays = {"view": overlays}

    out = []
    out.append("<!DOCTYPE html><html lang=\"en\"><head>")
    out.append('<meta charset="utf-8">')
    out.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    out.append("<title>%s</title>" % _esc(doc_title))
    out.append("<style>%s</style>" % _CSS)
    out.append("</head><body>")

    # sticky header + count chips
    out.append('<header><div><h1>%s</h1>'
               '<div class="sub">%s &rarr; %s</div></div>'
               % (_esc("Model diff"), _esc(old_l), _esc(new_l)))
    out.append('<div class="chips">')
    out.append('<span class="chip add%s">+%d added</span>'
               % ("" if n_add else " zero", n_add))
    out.append('<span class="chip chg%s">~%d changed</span>'
               % ("" if n_chg else " zero", n_chg))
    out.append('<span class="chip rem%s">−%d removed</span>'
               % ("" if n_rem else " zero", n_rem))
    out.append('<button id="themebtn" class="tbtn" type="button" '
               'title="Toggle theme">◑ theme</button>')
    out.append('</div></header>')

    out.append('<main>')

    if D.is_empty(diff):
        out.append('<div class="empty">No semantic changes between these two versions.</div>')
        out.append('</main><footer>Generated by freecad-diff'
                   ' · <code>%s</code></footer>' % D.DIFF_SCHEMA_NAME)
        out.append("<script>%s</script></body></html>" % _JS)
        return "\n".join(out)

    # visual overlay(s)
    if overlays:
        out.append('<div class="viz">')
        names = list(overlays.keys())
        if len(names) > 1:
            out.append('<div class="tabs">')
            for i, name in enumerate(names):
                out.append('<button type="button" data-view="%s" '
                           'aria-selected="%s">%s</button>'
                           % (_esc(name), "true" if i == 0 else "false",
                              _esc(name)))
            out.append('</div>')
        # old/new wipe: only when there is an old side to fade against
        if diff.get("changed") or diff.get("removed"):
            out.append('<div class="wipe"><span>old</span>'
                       '<input type="range" min="0" max="100" value="50" '
                       'class="fcd-slider" aria-label="blend old and new" '
                       'title="Slide to fade between the old and new version">'
                       '<span>new</span></div>')
        for i, name in enumerate(names):
            svg = _strip_svg_prolog(overlays[name])
            out.append('<div class="frame%s" data-view="%s">%s</div>'
                       % (" on" if i == 0 else "", _esc(name), svg))
        out.append('</div>')

    # volumetric material summary (opt-in; present only when computed)
    g = diff.get("geometry")
    if g:
        out.append('<div class="material">'
                   '<span class="mlabel">Material</span>'
                   '<span class="madd">+%s added</span>'
                   '<span class="mrem">&minus;%s removed</span>'
                   '<span class="mnet">net %s</span></div>'
                   % (_esc(g["added_text"]), _esc(g["removed_text"]),
                      _esc(g["net_text"])))

    # document metadata changes (Comment, Company, License, custom Meta)
    doc_changes = diff.get("document_changes", [])
    if doc_changes:
        rows = []
        for c in doc_changes:
            rows.append(_row("~", "%s %s -> %s" % (
                c["name"], c.get("old") or "(unset)", c.get("new") or "(unset)")))
        out.append('<details class="obj chg" open>'
                   '<summary><span class="glyph chg">~</span> '
                   '<span class="label">Document</span></summary>'
                   '<table class="changes">%s</table></details>' % "".join(rows))

    # per-object blocks: changed (open) first, then added, then removed
    for o in diff.get("changed", []):
        out.append(_object_block(o, "~", "chg", open_=True))
    for o in diff.get("added", []):
        out.append(_object_block(o, "+", "add", open_=True))
    for o in diff.get("removed", []):
        out.append(_object_block(o, "-", "rem", open_=True))

    out.append('</main>')
    out.append('<footer>Generated by freecad-diff · <code>%s</code> '
               'v%s</footer>' % (D.DIFF_SCHEMA_NAME, D.DIFF_SCHEMA_VERSION))
    out.append("<script>%s</script></body></html>" % _JS)
    return "\n".join(out)


def _strip_svg_prolog(svg):
    """Drop an XML declaration so the SVG inlines cleanly inside HTML."""
    s = svg.lstrip()
    if s.startswith("<?xml"):
        end = s.find("?>")
        if end != -1:
            s = s[end + 2:].lstrip()
    return s
