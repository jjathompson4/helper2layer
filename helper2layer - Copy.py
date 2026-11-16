"""
helper2layer.py - Helpers to Layers for 3ds Max.

Converts the Dummy/Point helper hierarchy that a Rhino > FBX > 3ds Max import
creates into a matching, nested Max layer structure (one layer per helper),
moves every object onto the layer of its nearest helper, then (by default)
unlinks the objects and deletes the helpers.

Run in Max: drag this file into a viewport, or from a MacroScript:
    python.ExecuteFile @"C:\\path\\to\\helper2layer.py"
See README.md for install and rollback (Edit > Fetch).

Supports 3ds Max 2024 (Python 3.10, PySide2) and 2025+ (Python 3.11, PySide6).

Layout of this file
-------------------
  MODEL    plain dataclasses shared by every section
  PLANNER  pure Python: decides layers, names, assignments, unlinks, deletes
  READER   pymxs: snapshots the scene into MODEL objects
  APPLIER  pymxs: executes a Plan, checks transforms, builds a Report
  UI       Qt dialogs (options, preview, report)
  MAIN     show() entry point

MODEL and PLANNER import nothing from Max, so they are unit-tested outside
Max (see tests/). Every pymxs and Qt import is deferred into the functions
that need it, so `import helper2layer` works on any machine.
"""
from __future__ import annotations

import contextlib
import traceback
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

__version__ = "0.1.0"
TOOL_NAME = "Helpers to Layers"
HELPER_CLASSES = ("Dummy", "Point")
UNNAMED_HELPER = "Unnamed Helper"
DEFAULT_LAYER_NAME = "0"
PATH_SEPARATOR = "::"
UNDO_LABEL = TOOL_NAME
TRANSFORM_TOLERANCE = 1e-4
SCOPE_SELECTED = "selected"
SCOPE_ALL = "all"
# Class-name fragments that suggest a helper is a rig rather than a Rhino layer.
RIG_CLASS_HINTS = ("light", "sun", "camera", "sky")


# =============================================================================
# MODEL
# =============================================================================


@dataclass
class NodeInfo:
    """One scene node, as plain data. `cls` is the MAXScript class name."""

    handle: int
    name: str
    cls: str
    parent: int | None = None
    children: list[int] = field(default_factory=list)
    is_group_head: bool = False


@dataclass(frozen=True)
class ExistingLayer:
    """A layer already in the scene. `parent_name` is None at the top level."""

    name: str
    parent_name: str | None = None


@dataclass
class Options:
    scope: str = SCOPE_SELECTED  # SCOPE_SELECTED or SCOPE_ALL
    flatten: bool = True  # False = mirror mode: keep helpers and hierarchy
    hold: bool = True  # run Edit > Hold before applying


@dataclass
class LayerPlan:
    name: str
    parent_name: str | None
    helper_handle: int
    reuse: bool  # True = an existing layer with this name and parent is reused


@dataclass
class Plan:
    layers: list[LayerPlan] = field(default_factory=list)  # parents first
    assignments: dict[int, str] = field(default_factory=dict)  # handle -> layer
    unlink: list[int] = field(default_factory=list)
    delete: list[int] = field(default_factory=list)  # deepest first
    converted: list[int] = field(default_factory=list)  # depth order
    top_level_names: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    flatten: bool = True

    @property
    def helper_count(self) -> int:
        return len(self.converted)

    @property
    def layers_to_create(self) -> int:
        return sum(1 for lp in self.layers if not lp.reuse)

    @property
    def layers_to_reuse(self) -> int:
        return sum(1 for lp in self.layers if lp.reuse)

    @property
    def objects_to_move(self) -> int:
        return len(self.assignments)

    def layer_name_for_helper(self, handle: int) -> str | None:
        for lp in self.layers:
            if lp.helper_handle == handle:
                return lp.name
        return None

    def summary_lines(self) -> list[str]:
        mode = (
            "Flatten: objects unlinked, helpers deleted"
            if self.flatten
            else "Mirror: hierarchy and helpers kept"
        )
        return [
            f"Helpers to convert: {self.helper_count}",
            f"Layers to create: {self.layers_to_create}",
            f"Layers to reuse: {self.layers_to_reuse}",
            f"Objects to move: {self.objects_to_move}",
            f"Objects to unlink: {len(self.unlink)}",
            f"Helpers to delete: {len(self.delete)}",
            f"Mode: {mode}",
        ]


@dataclass
class Flagged:
    handle: int
    name: str
    reason: str


@dataclass
class Report:
    layers_created: int = 0
    layers_reused: int = 0
    objects_moved: int = 0
    objects_unlinked: int = 0
    helpers_deleted: int = 0
    moved: list[Flagged] = field(default_factory=list)  # world position changed
    skewed: list[Flagged] = field(default_factory=list)  # rotation/shear changed
    scale_changed: list[Flagged] = field(default_factory=list)  # own scale changed
    missing: list[int] = field(default_factory=list)  # handles not found in Max

    @property
    def flagged(self) -> list[Flagged]:
        return self.moved + self.skewed + self.scale_changed

    def flagged_handles(self) -> list[int]:
        seen: dict[int, None] = {}
        for f in self.flagged:
            seen.setdefault(f.handle, None)
        return list(seen)

    def summary_lines(self) -> list[str]:
        lines = [
            f"Layers created: {self.layers_created}",
            f"Layers reused: {self.layers_reused}",
            f"Objects moved to layers: {self.objects_moved}",
            f"Objects unlinked: {self.objects_unlinked}",
            f"Helpers deleted: {self.helpers_deleted}",
        ]
        if self.missing:
            lines.append(f"Nodes not found in scene (skipped): {len(self.missing)}")
        for title, items in (
            ("Moved (world position changed)", self.moved),
            ("Skewed (rotation/shear changed, helper had non-uniform scale?)", self.skewed),
            ("Own scale changed (root helper carried unit-conversion scale?)", self.scale_changed),
        ):
            if items:
                lines.append("")
                lines.append(f"{title}: {len(items)}")
                lines.extend(f"  {f.name}" for f in items)
        if not self.flagged:
            lines.append("")
            lines.append("Transform check: all unlinked objects kept their world transform.")
        return lines


# =============================================================================
# PLANNER (pure Python)
# =============================================================================


def is_helper(node: NodeInfo) -> bool:
    """Dummy or Point, and not the hidden dummy that holds a Max group together."""
    return node.cls in HELPER_CLASSES and not node.is_group_head


def _helper_descendants(nodes: dict[int, NodeInfo], root: int) -> list[int]:
    """Helper descendants of `root`, walking only through helpers."""
    out: list[int] = []
    stack = list(nodes[root].children)
    while stack:
        h = stack.pop()
        n = nodes.get(h)
        if n is not None and is_helper(n):
            out.append(h)
            stack.extend(n.children)
    return out


def find_converted(nodes: dict[int, NodeInfo], selection: Iterable[int], scope: str) -> set[int]:
    """Handles of every helper this run converts."""
    if scope == SCOPE_SELECTED:
        roots = [h for h in selection if h in nodes and is_helper(nodes[h])]
    elif scope == SCOPE_ALL:
        roots = [h for h, n in nodes.items() if is_helper(n) and n.children]
    else:
        raise ValueError(f"unknown scope {scope!r}")
    converted: set[int] = set()
    for r in roots:
        converted.add(r)
        converted.update(_helper_descendants(nodes, r))
    return converted


def _depth(nodes: dict[int, NodeInfo], handle: int) -> int:
    d = 0
    p = nodes[handle].parent
    while p is not None and p in nodes:
        d += 1
        p = nodes[p].parent
    return d


class _AncestorFinder:
    """Memoised 'nearest converted ancestor' lookup (excluding the node itself)."""

    def __init__(self, nodes: dict[int, NodeInfo], converted: set[int]):
        self.nodes = nodes
        self.converted = converted
        self.cache: dict[int, int | None] = {}

    def __call__(self, handle: int) -> int | None:
        walk: list[int] = []
        h = self.nodes[handle].parent
        result: int | None = None
        while h is not None and h in self.nodes:
            if h in self.converted:
                result = h
                break
            if h in self.cache:
                result = self.cache[h]
                break
            walk.append(h)
            h = self.nodes[h].parent
        for w in walk:
            self.cache[w] = result
        self.cache[handle] = result
        return result


def _base_name(node: NodeInfo) -> str:
    return node.name.strip() or UNNAMED_HELPER


def _plan_layer_names(
    nodes: dict[int, NodeInfo],
    ordered: list[int],
    ancestor_of: Callable[[int], int | None],
    existing: Iterable[ExistingLayer],
) -> list[LayerPlan]:
    """Assign a unique layer name to each converted helper (parents first).

    Rules, in order:
      * blank name -> UNNAMED_HELPER
      * a name repeated among this run's helpers -> Rhino path style
        (converted ancestors joined with '::'), e.g. 'Walls::Interior'
      * candidate, candidate (2), candidate (3) ... : the first that is
          - not already planned this run, and
          - either an existing layer with the same parent (reuse), or
          - not an existing layer at all (create).
        The default layer '0' is never reused. Collision checks ignore case;
        reuse needs an exact match.
    """
    counts = Counter(_base_name(nodes[h]) for h in ordered)
    existing_by_key = {(lay.name, lay.parent_name): lay for lay in existing}
    existing_lower = {lay.name.lower() for lay in existing}
    existing_lower.add(DEFAULT_LAYER_NAME.lower())
    planned_lower: set[str] = set()
    layer_name: dict[int, str] = {}
    path_parts: dict[int, list[str]] = {}
    layers: list[LayerPlan] = []

    for h in ordered:
        base = _base_name(nodes[h])
        parent = ancestor_of(h)
        parent_name = layer_name[parent] if parent is not None else None
        parts = (path_parts[parent] if parent is not None else []) + [base]
        path_parts[h] = parts
        candidate = PATH_SEPARATOR.join(parts) if counts[base] > 1 else base

        i = 1
        while True:
            name = candidate if i == 1 else f"{candidate} ({i})"
            i += 1
            low = name.lower()
            if low in planned_lower:
                continue
            if name != DEFAULT_LAYER_NAME and (name, parent_name) in existing_by_key:
                reuse = True
                break
            if low in existing_lower:
                continue
            reuse = False
            break

        planned_lower.add(name.lower())
        layer_name[h] = name
        layers.append(LayerPlan(name=name, parent_name=parent_name, helper_handle=h, reuse=reuse))
    return layers


def _rig_warnings(nodes: dict[int, NodeInfo], converted: set[int], layer_name: dict[int, str]) -> list[str]:
    """Names of converted helpers whose subtree holds lights/cameras: likely rigs."""
    out: list[str] = []
    for h in sorted(converted, key=lambda x: layer_name[x].lower()):
        stack = list(nodes[h].children)
        hit: str | None = None
        while stack and hit is None:
            c = nodes.get(stack.pop())
            if c is None:
                continue
            if any(k in c.cls.lower() for k in RIG_CLASS_HINTS):
                hit = c.name
            elif c.handle not in converted:
                stack.extend(c.children)
        if hit is not None:
            out.append(f"'{layer_name[h]}' contains {hit!r}; is this a rig rather than a Rhino layer?")
    return out


def build_plan(
    nodes: dict[int, NodeInfo],
    existing: Iterable[ExistingLayer],
    selection: Iterable[int],
    options: Options,
) -> Plan:
    """Decide everything; touch nothing. See module docstring for the rules."""
    existing = list(existing)
    converted = find_converted(nodes, selection, options.scope)
    if not converted:
        return Plan(flatten=options.flatten)

    ordered = sorted(
        converted,
        key=lambda h: (_depth(nodes, h), _base_name(nodes[h]).lower(), h),
    )
    ancestor_of = _AncestorFinder(nodes, converted)
    layers = _plan_layer_names(nodes, ordered, ancestor_of, existing)
    layer_name = {lp.helper_handle: lp.name for lp in layers}

    assignments: dict[int, str] = {}
    for h in nodes:
        if h in converted:
            if not options.flatten:
                assignments[h] = layer_name[h]
            continue
        a = ancestor_of(h)
        if a is not None:
            assignments[h] = layer_name[a]

    unlink: list[int] = []
    delete: list[int] = []
    if options.flatten:
        unlink = sorted(
            h for h, n in nodes.items() if h not in converted and n.parent in converted
        )
        delete = list(reversed(ordered))

    top_level = sorted(
        (layer_name[h] for h in ordered if ancestor_of(h) is None), key=str.lower
    )
    warnings = _rig_warnings(nodes, converted, layer_name)

    return Plan(
        layers=layers,
        assignments=assignments,
        unlink=unlink,
        delete=delete,
        converted=ordered,
        top_level_names=top_level,
        warnings=warnings,
        flatten=options.flatten,
    )


# =============================================================================
# READER (pymxs)
# =============================================================================


def _node_handle(node: Any) -> int:
    return int(node.inode.handle)


def snapshot_scene(rt: Any = None) -> tuple[dict[int, NodeInfo], list[ExistingLayer], list[int]]:
    """Read the scene into plain data. `rt` defaults to pymxs.runtime."""
    if rt is None:
        from pymxs import runtime as rt  # type: ignore[no-redef]

    nodes: dict[int, NodeInfo] = {}
    for node in rt.objects:
        h = _node_handle(node)
        parent = node.parent
        nodes[h] = NodeInfo(
            handle=h,
            name=str(node.name),
            cls=str(rt.classOf(node)),
            parent=_node_handle(parent) if parent is not None else None,
            is_group_head=bool(rt.isGroupHead(node)),
        )
    # Build child lists from the parent map; cheaper than node.children per node.
    for h, n in nodes.items():
        if n.parent is not None and n.parent in nodes:
            nodes[n.parent].children.append(h)

    existing: list[ExistingLayer] = []
    for i in range(int(rt.LayerManager.count)):
        layer = rt.LayerManager.getLayer(i)
        parent_layer = layer.getParent()
        existing.append(
            ExistingLayer(
                name=str(layer.name),
                parent_name=str(parent_layer.name) if parent_layer is not None else None,
            )
        )

    selection = [_node_handle(n) for n in rt.selection]
    return nodes, existing, selection


# =============================================================================
# APPLIER (pymxs)
# =============================================================================


def _null_ctx(*_args: Any, **_kwargs: Any) -> contextlib.AbstractContextManager:
    return contextlib.nullcontext()


def _rows(matrix: Any) -> list[tuple[float, float, float]]:
    return [
        (float(r.x), float(r.y), float(r.z))
        for r in (matrix.row1, matrix.row2, matrix.row3, matrix.row4)
    ]


def _vec(p: Any) -> tuple[float, float, float]:
    return (float(p.x), float(p.y), float(p.z))


def _close(a: float, b: float, tol: float = TRANSFORM_TOLERANCE) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def _rows_close(a: Iterable[tuple[float, ...]], b: Iterable[tuple[float, ...]]) -> bool:
    return all(_close(x, y) for ra, rb in zip(a, b, strict=True) for x, y in zip(ra, rb, strict=True))


def apply_plan(
    plan: Plan,
    rt: Any = None,
    undo_ctx: Callable[..., Any] | None = None,
    redraw_ctx: Callable[..., Any] | None = None,
    hold: bool = True,
) -> Report:
    """Execute `plan` in Max. Raises on failure; the caller tells the user to Fetch."""
    if rt is None:
        import pymxs

        rt = pymxs.runtime
        undo_ctx = undo_ctx or pymxs.undo
        redraw_ctx = redraw_ctx or pymxs.redraw
    undo_ctx = undo_ctx or _null_ctx
    redraw_ctx = redraw_ctx or _null_ctx

    if hold:
        rt.holdMaxFile()

    # pymxs.undo swallows exceptions raised inside its block (it rolls back and
    # treats itself as the handler), so a failure would otherwise look like
    # success. Capture the error, re-raise inside so the rollback still runs,
    # then re-raise outside if the context manager ate it.
    report: Report | None = None
    error: BaseException | None = None
    with undo_ctx(True, UNDO_LABEL), redraw_ctx(False):
        try:
            report = _apply(plan, rt)
        except BaseException as err:
            error = err
            raise
    if error is not None:
        raise error
    if report is None:
        raise RuntimeError("apply produced no report; scene may have been rolled back")
    return report


def _apply(plan: Plan, rt: Any) -> Report:
    report = Report()
    node_cache: dict[int, Any] = {}

    def node_of(handle: int) -> Any:
        if handle not in node_cache:
            node_cache[handle] = rt.maxOps.getNodeByHandle(handle)
        return node_cache[handle]

    # 1. Layers, parents first.
    layers: dict[str, Any] = {}
    for lp in plan.layers:
        layer = rt.LayerManager.getLayerFromName(lp.name) if lp.reuse else None
        if layer is None:
            layer = rt.LayerManager.newLayerFromName(lp.name)
            if layer is None:
                raise RuntimeError(f"Max refused to create layer {lp.name!r}; does it already exist?")
            report.layers_created += 1
        else:
            report.layers_reused += 1
        if lp.parent_name is not None:
            layer.setParent(layers[lp.parent_name])
        layers[lp.name] = layer

    # 2. Assignments.
    for handle, layer_name in plan.assignments.items():
        node = node_of(handle)
        if node is None:
            report.missing.append(handle)
            continue
        layers[layer_name].addNode(node)
        report.objects_moved += 1

    # 3. Record transforms, then unlink (Max keeps world position by default).
    before: dict[int, tuple[list[tuple[float, float, float]], tuple[float, float, float]]] = {}
    for handle in plan.unlink:
        node = node_of(handle)
        if node is None:
            if handle not in report.missing:
                report.missing.append(handle)
            continue
        before[handle] = (_rows(node.transform), _vec(node.scale))
    for handle in before:
        node_of(handle).parent = None
        report.objects_unlinked += 1

    # 4. Delete helpers, deepest first.
    for handle in plan.delete:
        node = node_of(handle)
        if node is None:
            if handle not in report.missing:
                report.missing.append(handle)
            continue
        rt.delete(node)
        report.helpers_deleted += 1

    # 5. Transform check.
    for handle, (rows_before, scale_before) in before.items():
        node = node_of(handle)
        name = str(node.name)
        rows_after = _rows(node.transform)
        if not _rows_close(rows_before[3:], rows_after[3:]):
            report.moved.append(Flagged(handle, name, "moved"))
        if not _rows_close(rows_before[:3], rows_after[:3]):
            report.skewed.append(Flagged(handle, name, "skewed"))
        if not _rows_close([scale_before], [_vec(node.scale)]):
            report.scale_changed.append(Flagged(handle, name, "scale"))
    return report


def select_handles(rt: Any, handles: Iterable[int]) -> int:
    """Select the given nodes in Max. Returns how many were found."""
    found = [n for n in (rt.maxOps.getNodeByHandle(h) for h in handles) if n is not None]
    if found:
        rt.select(found)
    else:
        rt.clearSelection()
    return len(found)


# =============================================================================
# UI (Qt; PySide2 on Max 2024, PySide6 on Max 2025+)
# =============================================================================


def _load_qt() -> tuple[Any, Any, Any]:
    """Return (QtWidgets, QtCore, max_main_window). Raises ImportError outside Max."""
    try:
        from PySide6 import QtCore, QtWidgets  # type: ignore[import-not-found]
    except ImportError:
        from PySide2 import QtCore, QtWidgets  # type: ignore[import-not-found,no-redef]
    try:
        from qtmax import GetQMaxMainWindow  # type: ignore[import-not-found]

        main_window = GetQMaxMainWindow()
    except ImportError:
        main_window = None
    return QtWidgets, QtCore, main_window


def _exec_dialog(dialog: Any) -> int:
    fn = getattr(dialog, "exec", None) or dialog.exec_
    return int(fn())


def _print_report(report: Report) -> None:
    """Echo the report to the MAXScript Listener (print goes there inside Max)."""
    print(f"--- {TOOL_NAME} ---")
    for line in report.summary_lines():
        print(line)
    print("-" * (len(TOOL_NAME) + 8))


def _build_ui(QtWidgets: Any, QtCore: Any) -> type:
    """Define the dialog classes once Qt is known to be importable."""

    class PreviewDialog(QtWidgets.QDialog):
        def __init__(self, plan: Plan, parent: Any = None):
            super().__init__(parent)
            self.setWindowTitle(f"{TOOL_NAME}: preview")
            self.setMinimumWidth(420)
            layout = QtWidgets.QVBoxLayout(self)
            layout.addWidget(QtWidgets.QLabel("\n".join(plan.summary_lines())))
            layout.addWidget(QtWidgets.QLabel(f"Top-level helpers ({len(plan.top_level_names)}):"))
            lst = QtWidgets.QListWidget()
            lst.addItems(plan.top_level_names)
            lst.setMaximumHeight(160)
            layout.addWidget(lst)
            if plan.warnings:
                warn = QtWidgets.QLabel("Check these before applying:\n" + "\n".join(plan.warnings))
                warn.setWordWrap(True)
                layout.addWidget(warn)
            buttons = QtWidgets.QHBoxLayout()
            buttons.addStretch(1)
            apply_btn = QtWidgets.QPushButton("Apply")
            cancel_btn = QtWidgets.QPushButton("Cancel")
            apply_btn.setDefault(True)
            apply_btn.clicked.connect(self.accept)
            cancel_btn.clicked.connect(self.reject)
            buttons.addWidget(apply_btn)
            buttons.addWidget(cancel_btn)
            layout.addLayout(buttons)

    class ReportDialog(QtWidgets.QDialog):
        def __init__(self, report: Report, rt: Any, parent: Any = None):
            super().__init__(parent)
            self.report = report
            self.rt = rt
            self.setWindowTitle(f"{TOOL_NAME}: done")
            self.setMinimumSize(460, 320)
            layout = QtWidgets.QVBoxLayout(self)
            text = QtWidgets.QTextEdit()
            text.setReadOnly(True)
            text.setPlainText("\n".join(report.summary_lines()))
            layout.addWidget(text)
            layout.addWidget(QtWidgets.QLabel("Not what you expected? Edit > Fetch restores the held scene."))
            buttons = QtWidgets.QHBoxLayout()
            select_btn = QtWidgets.QPushButton("Select flagged objects")
            select_btn.setEnabled(bool(report.flagged))
            select_btn.clicked.connect(self.select_flagged)
            close_btn = QtWidgets.QPushButton("Close")
            close_btn.setDefault(True)
            close_btn.clicked.connect(self.accept)
            buttons.addWidget(select_btn)
            buttons.addStretch(1)
            buttons.addWidget(close_btn)
            layout.addLayout(buttons)

        def select_flagged(self) -> None:
            select_handles(self.rt, self.report.flagged_handles())

    class MainDialog(QtWidgets.QDialog):
        def __init__(self, parent: Any = None):
            super().__init__(parent)
            self.setWindowTitle(f"{TOOL_NAME} v{__version__}")
            self.setMinimumWidth(380)
            layout = QtWidgets.QVBoxLayout(self)

            scope_box = QtWidgets.QGroupBox("Scope")
            scope_layout = QtWidgets.QVBoxLayout(scope_box)
            self.rb_selected = QtWidgets.QRadioButton("Convert Selected helpers (plus their sub-helpers)")
            self.rb_all = QtWidgets.QRadioButton("Convert All helpers that have children")
            scope_layout.addWidget(self.rb_selected)
            scope_layout.addWidget(self.rb_all)
            layout.addWidget(scope_box)

            self.cb_flatten = QtWidgets.QCheckBox("Flatten: unlink objects and delete the helpers")
            self.cb_flatten.setChecked(True)
            self.cb_flatten.setToolTip("Off = mirror mode: build and fill layers, keep hierarchy and helpers.")
            self.cb_hold = QtWidgets.QCheckBox("Hold scene first (Edit > Fetch restores)")
            self.cb_hold.setChecked(True)
            self.cb_hold.setToolTip("Overwrites any existing Hold.")
            layout.addWidget(self.cb_flatten)
            layout.addWidget(self.cb_hold)

            self.status = QtWidgets.QLabel("")
            layout.addWidget(self.status)

            buttons = QtWidgets.QHBoxLayout()
            buttons.addStretch(1)
            preview_btn = QtWidgets.QPushButton("Preview...")
            preview_btn.setDefault(True)
            preview_btn.clicked.connect(self.on_preview)
            close_btn = QtWidgets.QPushButton("Close")
            close_btn.clicked.connect(self.close)
            buttons.addWidget(preview_btn)
            buttons.addWidget(close_btn)
            layout.addLayout(buttons)

            self.refresh_scope_default()

        def refresh_scope_default(self) -> None:
            try:
                from pymxs import runtime as rt

                has_selection = int(rt.selection.count) > 0
            except Exception:  # noqa: BLE001 - outside Max, or selection unreadable
                has_selection = False
            (self.rb_selected if has_selection else self.rb_all).setChecked(True)

        def options(self) -> Options:
            return Options(
                scope=SCOPE_SELECTED if self.rb_selected.isChecked() else SCOPE_ALL,
                flatten=self.cb_flatten.isChecked(),
                hold=self.cb_hold.isChecked(),
            )

        def on_preview(self) -> None:
            import pymxs

            rt = pymxs.runtime
            options = self.options()
            try:
                nodes, existing, selection = snapshot_scene(rt)
                plan = build_plan(nodes, existing, selection, options)
            except Exception as err:  # noqa: BLE001 - report anything to the user
                traceback.print_exc()
                QtWidgets.QMessageBox.critical(self, TOOL_NAME, f"Could not read the scene:\n{err}")
                return

            if plan.helper_count == 0:
                what = "in the selection" if options.scope == SCOPE_SELECTED else "with children in the scene"
                QtWidgets.QMessageBox.information(
                    self, TOOL_NAME, f"No Dummy or Point helpers found {what}. Nothing to do."
                )
                return

            if not _exec_dialog(PreviewDialog(plan, self)):
                self.status.setText("Cancelled; no changes made.")
                return

            try:
                report = apply_plan(plan, rt, pymxs.undo, pymxs.redraw, hold=options.hold)
            except Exception as err:  # noqa: BLE001 - partial apply: tell the user how to roll back
                traceback.print_exc()
                rollback = (
                    "Use Edit > Fetch to restore the scene held before this run."
                    if options.hold
                    else "Hold was off. Try Undo (Ctrl+Z), then check the scene carefully."
                )
                QtWidgets.QMessageBox.critical(
                    self, TOOL_NAME, f"Apply failed partway:\n{err}\n\n{rollback}"
                )
                self.status.setText("Apply failed. See the Listener for details.")
                return

            _print_report(report)
            if report.flagged:
                select_handles(rt, report.flagged_handles())
            _exec_dialog(ReportDialog(report, rt, self))
            self.status.setText(
                f"Done: {report.layers_created} layers created, {report.layers_reused} reused, "
                f"{report.objects_moved} objects moved."
            )
            self.refresh_scope_default()

    return MainDialog


# =============================================================================
# MAIN
# =============================================================================

_dialog: Any = None  # keep a reference so Max's garbage collector doesn't close it


def show() -> Any:
    """Open the tool dialog inside 3ds Max."""
    global _dialog
    QtWidgets, QtCore, main_window = _load_qt()
    MainDialog = _build_ui(QtWidgets, QtCore)
    if _dialog is not None:
        with contextlib.suppress(Exception):
            _dialog.close()
    _dialog = MainDialog(main_window)
    _dialog.show()
    return _dialog


if __name__ == "__main__":
    show()
