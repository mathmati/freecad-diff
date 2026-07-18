# FreeCAD Model Context - schema `freecad-model-context` v1.0

A canonical, versioned, **tool-agnostic** JSON serialization of a FreeCAD
document's *semantic* model: the feature tree, each feature's parametric
inputs (with expressions), the Sketcher geometry **with its constraint
graph**, attachments, and assigned materials. It is grounding context for an
LLM / agent / MCP tool - a published schema anyone can adopt, rather than the
ad-hoc per-tool dumps each FreeCAD AI/MCP server reinvents today.

**Prior art / lineage.** The point-based geometry + constraint-as-edge model
here follows the serialization pattern published by *CAD-Assistant* (Mallis et
al., ICCV 2025, arXiv:2412.13810), which serializes FreeCAD sketches to JSON
with point-parameterized primitives and constraints as references between
them. This schema's contribution is not that pattern but its packaging as a
**documented, versioned, round-trippable, standalone** artifact with a
reference serializer, designed for cross-tool adoption.

## Top level

```json
{
  "schema": "freecad-model-context",
  "schema_version": "1.0",
  "document": { "name": "<internal name>", "label": "<user label>" },
  "objects": [ <object>, ... ]
}
```

Consumers MUST check `schema` and `schema_version`. Minor versions are
additive (new optional keys); a major bump may change existing keys.

## Object

```json
{
  "id":    "Pad",                 // App DocumentObject.Name (stable id)
  "label": "Pad",                 // user-facing Label
  "type":  "PartDesign::Pad",     // FreeCAD TypeId
  "role":  "feature",             // body | sketch | feature | solid | datum | spreadsheet | object
  "params": { <name>: <value-entry>, ... },   // meaningful, non-default inputs only
  "links":  { <name>: <ref> | [<ref>...], ... },
  "features": ["Sketch", "Pad"],  // body only: ordered children
  "tip":      "Pad",              // body only: current tip feature id
  "sketch":   { ... },            // sketch only (see below)
  "material": { ... }             // present only if a non-default material is assigned
}
```

- **`role`** classifies the object so a consumer can filter (e.g. ignore
  `datum` scaffolding - origin planes/axes are emitted as identity-only nodes).
- **`params`** contains only properties a user meaningfully set: computed
  shapes, hidden/transient/output properties, attacher internals, and values
  left at their type default are omitted by design.

### value-entry

```json
{ "value": 15.0, "unit": "mm", "expression": "Spreadsheet.height" }
```

- `value`: JSON scalar, or `[x,y,z]` for a vector, or a placement object
  `{ "position":[x,y,z], "axis":[x,y,z], "angle_deg": <deg> }`.
- `unit`: present for quantity properties (`mm`, `deg`, `mm^2`, ...).
- `expression`: present iff the property is expression-bound; the string is
  the FreeCAD expression (references other objects/spreadsheet aliases).

### ref (link targets)

```json
{ "object": "Sketch", "sub": ["Face6"] }   // "sub" optional (sub-elements)
```

A link property's value is one `ref`, or a list of `ref`s for list links.

## `sketch`

```json
{
  "geometry":   [ <geometry>, ... ],
  "constraints":[ <constraint>, ... ],
  "support":    [ { "object": "XY_Plane" } ],   // attachment target(s)
  "map_mode":   "FlatFace"                        // attachment mode
}
```

### geometry (indexed by position in the array = its Sketcher GeoId)

```json
{ "type": "line",  "start": [x,y], "end": [x,y] }
{ "type": "circle","center":[x,y], "radius": r }
{ "type": "arc",   "center":[x,y], "radius": r, "start":[x,y], "end":[x,y] }
{ "type": "point", "at": [x,y] }
```

Optional `"construction": true` for construction geometry. Coordinates are in
the sketch's local 2D frame.

### constraint

```json
{
  "type": "Coincident",                 // FreeCAD constraint type
  "refs": [ <geo-ref>, <geo-ref> ],     // the elements it relates (used slots only)
  "value": 20.0,                        // dimensional constraints only (degrees for Angle)
  "dimensional": true,                  // present+true for dimensional constraints
  "driving": false,                     // present iff a reference (non-driving) dimension
  "name": "width"                       // present iff the constraint is named
}
```

#### geo-ref

```json
{ "geometry": 0, "point": "end" }       // point on a sketch geometry element
{ "element": "x_axis" }                 // one of the sketch axes/origin
```

- `geometry`: index into the sketch's `geometry` array (its GeoId).
- `element`: for the sketch's own axes/origin (`x_axis`, `y_axis`, `origin`).
- `point`: `start` | `end` | `center` - omitted when the constraint applies to
  the whole element (e.g. `Horizontal` on a line).

FreeCAD's internal `PointPos` integers and the `-2000` "unused" sentinel are
NOT exposed - they are resolved to the named roles above so the graph is
self-describing.

## `material`

```json
{ "name": "Steel-Generic", "library": "Standard", "uuid": "...",
  "physical": { "Density": "7850 kg/m^3", "YoungsModulus": "210 GPa" } }
```

Present only when a non-default material is assigned (`Default`/`None`/empty
are omitted). `physical` carries a subset of the material's physical
properties when available.

## Notes for consumers

- `id` is stable within a document and is what `links`/`refs`/`profile`
  reference; `label` is for humans.
- The serialization is **read-only context**: it captures enough to *describe*
  and *reason about* the model (the design intent in the constraint graph +
  parametric relationships), and is intended to round-trip through JSON
  losslessly. Full geometric *reconstruction* from the schema (rebuilding a
  live document) is a deliberate non-goal of v1.

## Appendix: diff output (`freecad-model-context-diff` v1.0)

`diff.diff_models(old, new)` compares two model-context dicts and returns:

```json
{
  "schema": "freecad-model-context-diff",
  "schema_version": "1.0",
  "old": { "name": "...", "label": "..." },
  "new": { "name": "...", "label": "..." },
  "added":   [ { "id", "label", "type", "role" } ],
  "removed": [ { "id", "label", "type", "role" } ],
  "changed": [ { "id", "label", "type", "role", "changes": [ <change> ] } ]
}
```

Objects are matched by `id`; datum scaffolding is ignored. Each `<change>`
has a `kind` and kind-specific fields:

- `param` (`name`, `old`, `new`, optional `old_expression`/`new_expression`),
  `param_added`, `param_removed`
- `label`, `type`, `link`, `material` (`old`, `new`)
- `tree` (body feature order, `old`/`new` lists), `tip`
- `constraint_added` / `constraint_removed` (`constraint`: rendered text),
  `constraint_value` (`constraint`, `old`, `new`),
  `constraint_edited`
- `geometry_count` (`old`, `new`), `geometry_edited` (`count`)

Constraints are matched by (type, geometry references, user name), so a
dimensional value edit is a `constraint_value` change rather than a
remove-plus-add. Values compare with a small numeric tolerance.
