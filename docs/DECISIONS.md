# Decisions

Every decision that changes behavior relative to the original build plan, dated.

## 2026-09-23

- **Environment.** Target Max 2024 (Python 3.10, PySide2) now and Max 2025+ (Python 3.11,
  PySide6) later. Code keeps a Python 3.10 syntax floor and loads PySide6 first, PySide2
  as fallback.
- **Packaging.** One drag-and-drop `helper2layer.py` (Jeff's choice). Testability is kept
  by deferring every pymxs and Qt import into the functions that need them, so the file
  imports on any machine and the planner is tested directly.
- **Default layer `0` is never reused.** A top-level helper named `0` would otherwise map
  onto Max's default layer, which cannot take a parent. It becomes `0 (2)`. The planner
  treats `0` as taken even if the scene reader did not report it.
- **Collision check is case-insensitive; reuse needs an exact match.** Max layer name
  uniqueness may or may not ignore case depending on version, so the safe assumption
  for creating is "taken", and the safe assumption for reusing is "only if identical".
  Jeff: verify in Max whether `walls` and `Walls` can coexist; if they can, this only
  costs an occasional ` (2)`.
- **Suffixed layers are reusable on rerun.** Candidates are tried in order (`Chair`,
  `Chair (2)`, ...) and the first one that is either free or an existing layer with the
  same parent is taken. Without this, a mirror-mode rerun over two sibling `Chair`
  helpers would create `Chair (3)`.
- **Path-style names use converted ancestors only.** `Walls::Interior` is built from the
  helpers converted in this run, so Convert Selected on a subtree yields `Interior`, not
  `Walls::Interior`, when `Walls` is not being converted.
- **Layer parent = nearest converted ancestor**, walking up through any node type. A
  converted helper whose parent is geometry or an unconverted helper is parented to the
  next converted helper above, or becomes top-level.
- **Childless helpers under Convert All.** A childless helper is converted only when it
  sits under a converted helper (so an empty Rhino layer still becomes a layer). A
  childless top-level helper is ignored; it is more likely a marker than a layer.
- **Rig warning.** The preview lists a converted helper whose subtree holds a node whose
  class name contains `light`, `sun`, `camera` or `sky`. This is the only heuristic; it
  exists so a dummy-parented luminaire is visible before Apply.
- **Transform check split into three flags.** `moved` = translation row changed,
  `skewed` = any of the three axis rows changed, `scale_changed` = the node's own
  `.scale` changed. A uniform unit-conversion scale on a root helper trips only the
  third; a non-uniform helper scale with a rotated child trips the second.
- **Missing nodes are skipped, not fatal.** If a handle from the plan no longer resolves
  during apply (should not happen; scene changed between preview and apply), it is
  recorded in `Report.missing` and the run continues.
- **Failed layer creation is fatal.** `newLayerFromName` returning nothing means the name
  exists in a way the planner did not predict; the run stops and the user is told to Fetch.
- **Flagged objects are selected automatically** after apply, in addition to the report
  button, and their names go to the Listener.
- **Errors inside the undo block are re-raised.** Autodesk's pymxs docs state that an
  exception raised inside `with pymxs.undo(True, label):` is undone and *not* propagated.
  `apply_plan` records the exception, lets the block roll back, then raises it again so
  the UI shows the error and the Fetch instruction instead of a success report.
- **Layer name lookup is case-insensitive in Max** (documented for `getLayerObject`), which
  confirms the case-insensitive collision rule above.
