# Helpers to Layers (helper2layer)

A 3ds Max tool that converts the helper hierarchy created by a Rhino > FBX > 3ds Max
import into a matching nested layer structure, 1:1, then removes the helpers.

- One Max layer per Dummy/Point helper, nested to match the helper tree.
- Every object goes on the layer of its nearest helper.
- Flatten (default): objects are unlinked (world position kept) and the helpers deleted.
- Mirror mode: layers are built and filled; hierarchy and helpers stay.
- Preview before apply, Hold before apply, transform check and report after.

Supports 3ds Max 2024 (Python 3.10, PySide2) and 2025+ (Python 3.11, PySide6).

## Install

`helper2layer.py` is the whole tool. Put it anywhere, e.g. your scripts folder.

Run it one of these ways:

1. Drag `helper2layer.py` from Explorer into a Max viewport.
2. Scripting > Run Script, pick the file.
3. Toolbar button: create a MacroScript with this body, then drag it to a toolbar
   from Customize > Customize User Interface (category `Team Tools`):

   ```maxscript
   macroScript HelpersToLayers category:"Team Tools" tooltip:"Helpers to Layers"
   (
       python.ExecuteFile @"C:\path\to\helper2layer.py"
   )
   ```

## Use

1. Optionally select the helpers you want converted.
2. Run the tool. Pick a scope:
   - **Convert Selected**: the selected Dummy/Point helpers plus their sub-helpers.
   - **Convert All**: every Dummy/Point helper with at least one child, plus sub-helpers.
     This also catches intentional rigs (a luminaire parented to a dummy). The preview
     lists top-level helper names and warns about helpers that contain lights or cameras.
3. Leave **Flatten** on to unlink objects and delete helpers. Turn it off for mirror mode.
4. Leave **Hold** on. It runs Edit > Hold before applying, which overwrites any existing hold.
5. Click **Preview...**, read the counts and the top-level names, then **Apply**.
6. Read the report. Flagged objects are selected for you and their names are printed to
   the MAXScript Listener.

## Rollback

Edit > Fetch restores the scene held before the run. Undo is wrapped in one block too,
but layer creation does not always undo cleanly, so Fetch is the real rollback.

If Apply fails partway, the tool says so and tells you to Fetch.

## Naming rules

- Layer name = helper name. Blank names become `Unnamed Helper`.
- A name that repeats among the helpers in one run uses Rhino's path style:
  `Walls::Interior`, `Ceilings::Interior`.
- Anything still colliding gets ` (2)`, ` (3)`.
- An existing layer with the same name and the same parent layer is reused, so rerunning
  does not create duplicates. The default layer `0` is never reused.

## What is excluded

- Group heads (the hidden dummy that holds a Max group together). Groups move to a layer
  as a unit.
- Anything that is not a Dummy or Point: Containers, the Daylight system's Compass, bones.
- Under Convert All, a childless top-level helper (a lone Point marker) is left alone.

## Transform check

Each unlinked object's world transform is recorded before unlinking and compared after.

- **Moved**: world position changed.
- **Skewed**: rotation or shear changed. Happens when a helper has non-uniform scale.
- **Own scale changed**: the object's local scale changed. Happens when a root helper
  carries unit-conversion scale (FBX import with unit conversion on). The object still
  sits in the same place; only its scale values differ.

## Development

Logic is split inside the one file: MODEL, PLANNER (pure Python), READER and APPLIER
(pymxs), UI (Qt), MAIN. The planner is unit-tested outside Max against fake hierarchies,
and the applier is tested against an in-memory fake of `pymxs.runtime`.

```bash
python3 -m venv .venv && .venv/bin/pip install pytest ruff
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

See `docs/DECISIONS.md` for behavior decisions and `docs/PROGRESS.md` for status and
the in-Max acceptance checklist.
