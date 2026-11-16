# Progress

## Status (2026-09-23)

- [x] Plan approved (see `~/.claude/plans/pasted-content-id-6a74-helpers-to-agile-orbit.md`).
- [x] `helper2layer.py` written: model, planner, reader, applier, UI, main.
- [x] Planner unit tests (pure Python) and applier tests against a fake runtime.
- [x] ruff clean, imports outside Max, parses under Python 3.10 grammar.
- [x] pymxs.undo label form, layer name case-insensitivity and undo-block exception swallowing checked against Autodesk docs.
- [ ] First run in Max 2024 (Jeff). Expect API surprises in READER/APPLIER/UI; the
      planner should not need changes.
- [ ] Run in Max 2025/2026 to confirm the PySide6 path.

## Things to verify in Max first

These are the API calls the agent could not run. Each one is used in exactly one place.

| Call | Where | What could go wrong |
| --- | --- | --- |
| `node.inode.handle` | `_node_handle` | If unavailable, use `rt.getHandleByAnim`? No: that is a different handle space. Fall back to `node.handle`. |
| `str(rt.classOf(node))` | `snapshot_scene` | Confirm it yields `Dummy` / `Point` for FBX nulls. |
| `layer.getParent()` returning `None` at top level | `snapshot_scene` | Might return `undefined` as a non-None object. |
| `rt.LayerManager.newLayerFromName` returning `None` on a name clash | `_apply` | Confirm it does not raise instead. |
| `node.parent = None` | `_apply` | Confirm it unlinks and keeps world position. |
| `node.transform.row1..row4`, `node.scale` | `_rows`, `_vec` | Attribute names on pymxs Matrix3/Point3. |
| `pymxs.undo(True, label)`, `pymxs.redraw(False)` | `apply_plan` | Verified in the 2022 pymxs docs; exceptions inside the block are swallowed, handled in `apply_plan`. |
| `rt.select(list_of_nodes)` | `select_handles` | May need `rt.Array(*nodes)`. |
| `qtmax.GetQMaxMainWindow()` | `_load_qt` | Present in 2024 and 2025+. |
| `dialog.exec` vs `exec_` | `_exec_dialog` | Handled by lookup; confirm no deprecation dialog. |

## Acceptance tests (Jeff, in Max)

- [ ] Rhino file with 3+ nesting levels, repeated sublayer names, an empty layer, and a
      block instance imports; Convert All preview counts look right.
- [ ] Max layer tree matches Rhino's.
- [ ] Iray+ render identical before and after.
- [ ] Daylight system untouched.
- [ ] Dummy-parented luminaire assembly shows in the Convert All preview (top-level list
      and the rig warning); Convert Selected leaves it alone.
- [ ] Mirror mode run twice: no duplicate layers.
- [ ] Import with unit conversion on: scale flags behave (objects listed under
      "Own scale changed", none under "Moved").
- [ ] Edit > Fetch restores the pre-conversion scene.

## Open questions

- Do Rhino block instances arrive as helpers? If a block instance is a Dummy with mesh
  children, Convert All makes it a layer (`Chair`, `Furniture::Chair (2)`, ...). If it
  arrives as a Max group, the group head is skipped and the block moves as a unit, which
  is probably what the team wants. Decide after the sample file.
- Do FBX nulls land as Dummy or Point in Max 2024? Both are handled; note which for the
  record.
- Toolbar button, macro, or menu entry? README shows the MacroScript one-liner.
