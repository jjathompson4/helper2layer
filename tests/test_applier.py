"""Applier and reader tests against the in-memory FakeRt."""
import pytest
from fake_rt import FakeRt, Matrix3

from helper2layer import (
    ExistingLayer,
    LayerPlan,
    Options,
    Plan,
    apply_plan,
    build_plan,
    select_handles,
    snapshot_scene,
)


def _log_kinds(rt):
    return [entry[0] for entry in rt.log]


@pytest.fixture
def scene():
    """Walls > Interior > wall_a ; Walls > wall_b ; a group under Furniture ; Compass at root."""
    rt = FakeRt()
    walls = rt.add_node("Walls", "Dummy")
    interior = rt.add_node("Interior", "Dummy", walls, Matrix3.compose(pos=(0, 0, 100)))
    rt.add_node("wall_a", "Editable_Mesh", interior, Matrix3.compose(pos=(10, 0, 0)))
    rt.add_node("wall_b", "Editable_Mesh", walls, Matrix3.compose(pos=(0, 5, 0)))
    furniture = rt.add_node("Furniture", "Dummy")
    chair = rt.add_node("Chair", "Dummy", furniture, is_group_head=True)
    rt.add_node("seat", "Editable_Mesh", chair)
    compass = rt.add_node("Compass001", "Compass")
    rt.add_node("Daylight001", "Daylight", compass)
    return rt


def _run(rt, options, hold=True):
    nodes, existing, selection = snapshot_scene(rt)
    plan = build_plan(nodes, existing, selection, options)
    report = apply_plan(plan, rt, hold=hold)
    return plan, report


def test_snapshot_reads_hierarchy_layers_and_selection(scene):
    scene.LayerManager.add_existing("Old")
    scene.LayerManager.add_existing("OldChild", "Old")
    scene.selection.append(scene.nodes_by_handle[1])
    nodes, existing, selection = snapshot_scene(scene)
    assert nodes[1].name == "Walls" and nodes[1].children == [2, 4]
    assert nodes[2].parent == 1 and nodes[3].parent == 2
    assert nodes[6].is_group_head is True
    assert nodes[8].cls == "Compass"
    assert existing == [ExistingLayer("0"), ExistingLayer("Old"), ExistingLayer("OldChild", "Old")]
    assert selection == [1]


def test_flatten_end_to_end(scene):
    plan, report = _run(scene, Options(scope="all", flatten=True))
    assert scene.holds == 1
    assert report.layers_created == 3 and report.layers_reused == 0
    names = {layer.name: layer for layer in scene.LayerManager.layers}
    assert set(names) == {"0", "Walls", "Interior", "Furniture"}
    assert names["Interior"].getParent() is names["Walls"]
    assert names["Walls"].getParent() is None
    by_name = {n.name: n for n in scene.objects}
    assert by_name["wall_a"].layer is names["Interior"]
    assert by_name["wall_b"].layer is names["Walls"]
    assert by_name["Chair"].layer is names["Furniture"]
    assert by_name["seat"].layer is names["Furniture"]
    assert "Walls" not in by_name and "Interior" not in by_name
    assert by_name["wall_a"].parent is None
    assert by_name["seat"].parent is by_name["Chair"]  # group intact
    assert by_name["Daylight001"].parent is by_name["Compass001"]  # daylight untouched
    assert report.helpers_deleted == 3 and report.objects_unlinked == 3  # Walls, Interior, Furniture
    assert report.flagged == []
    assert report.missing == []


def test_call_order(scene):
    _run(scene, Options(scope="all", flatten=True))
    kinds = _log_kinds(scene)
    assert kinds[0] == "hold"
    new_layers = [e[1] for e in scene.log if e[0] == "newLayer"]
    assert new_layers.index("Walls") < new_layers.index("Interior")
    last_add = max(i for i, k in enumerate(kinds) if k == "addNode")
    first_unlink = min(i for i, k in enumerate(kinds) if k == "unlink")
    last_unlink = max(i for i, k in enumerate(kinds) if k == "unlink")
    first_delete = min(i for i, k in enumerate(kinds) if k == "delete")
    assert last_add < first_unlink < last_unlink < first_delete
    deletes = [e[1] for e in scene.log if e[0] == "delete"]
    assert deletes.index("Interior") < deletes.index("Walls")


def test_world_transform_preserved_on_unlink(scene):
    by_name = {n.name: n for n in scene.objects}
    before = by_name["wall_a"].transform.rows()
    _run(scene, Options(scope="all", flatten=True))
    assert by_name["wall_a"].transform.rows() == pytest.approx(before)
    assert tuple(by_name["wall_a"].transform.row4) == (10, 0, 100)


def test_hold_can_be_skipped(scene):
    _run(scene, Options(scope="all", flatten=True), hold=False)
    assert scene.holds == 0


def test_mirror_mode_twice_reuses_layers(scene):
    plan1, report1 = _run(scene, Options(scope="all", flatten=False))
    assert report1.layers_created == 3 and report1.helpers_deleted == 0 and report1.objects_unlinked == 0
    by_name = {n.name: n for n in scene.objects}
    assert by_name["Interior"].parent is by_name["Walls"]
    assert by_name["Walls"].layer.name == "Walls"
    scene.log.clear()
    plan2, report2 = _run(scene, Options(scope="all", flatten=False))
    assert plan2.layers_to_create == 0
    assert report2.layers_created == 0 and report2.layers_reused == 3
    assert "newLayer" not in _log_kinds(scene)
    assert len(scene.LayerManager.layers) == 4


def test_unit_conversion_scale_is_flagged_as_scale_change_only():
    rt = FakeRt()
    root = rt.add_node("RhinoRoot", "Dummy", local=Matrix3.compose(scale=(0.0254,) * 3))
    rt.add_node("part", "Editable_Mesh", root, Matrix3.compose(pos=(100, 0, 0)))
    plan, report = _run(rt, Options(scope="all", flatten=True))
    assert [f.name for f in report.scale_changed] == ["part"]
    assert report.moved == [] and report.skewed == []
    part = next(n for n in rt.objects if n.name == "part")
    assert tuple(part.transform.row4) == pytest.approx((2.54, 0, 0))
    assert tuple(part.scale) == pytest.approx((0.0254,) * 3)


def test_non_uniform_parent_scale_flags_skew():
    rt = FakeRt()
    root = rt.add_node("Stretched", "Dummy", local=Matrix3.compose(scale=(2, 1, 1)))
    rt.add_node("rotated", "Editable_Mesh", root, Matrix3.compose(rot_z_deg=45))
    rt.add_node("axis_aligned", "Editable_Mesh", root, Matrix3.compose(pos=(1, 2, 3)))
    plan, report = _run(rt, Options(scope="all", flatten=True))
    assert [f.name for f in report.skewed] == ["rotated"]
    assert report.moved == []
    assert {f.name for f in report.scale_changed} == {"rotated", "axis_aligned"}
    assert report.flagged_handles() == sorted(report.flagged_handles(), key=report.flagged_handles().index)
    assert len(report.flagged_handles()) == 2


def test_report_summary_lists_flagged_names():
    rt = FakeRt()
    root = rt.add_node("Stretched", "Dummy", local=Matrix3.compose(scale=(2, 1, 1)))
    rt.add_node("rotated", "Editable_Mesh", root, Matrix3.compose(rot_z_deg=45))
    _, report = _run(rt, Options(scope="all", flatten=True))
    text = "\n".join(report.summary_lines())
    assert "Skewed" in text and "  rotated" in text
    assert "all unlinked objects kept" not in text


def test_clean_report_says_so(scene):
    _, report = _run(scene, Options(scope="all", flatten=True))
    assert "kept their world transform" in "\n".join(report.summary_lines())


def test_select_handles(scene):
    n = select_handles(scene, [3, 4, 999])
    assert n == 2
    assert [node.name for node in scene.selection] == ["wall_a", "wall_b"]
    assert select_handles(scene, []) == 0
    assert list(scene.selection) == []


def test_missing_nodes_are_skipped_not_fatal(scene):
    plan = Plan(
        layers=[LayerPlan("Solo", None, 999, False)],
        assignments={999: "Solo", 3: "Solo"},
        unlink=[999],
        delete=[999],
    )
    report = apply_plan(plan, scene, hold=False)
    assert report.missing == [999]
    assert report.objects_moved == 1


def test_layer_creation_failure_raises(scene):
    scene.LayerManager.add_existing("Taken")
    plan = Plan(layers=[LayerPlan("Taken", None, 1, False)])
    with pytest.raises(RuntimeError, match="Taken"):
        apply_plan(plan, scene, hold=False)


def test_reuse_falls_back_to_create_if_layer_vanished(scene):
    plan = Plan(layers=[LayerPlan("Ghost", None, 1, True)])
    report = apply_plan(plan, scene, hold=False)
    assert report.layers_created == 1 and report.layers_reused == 0


def test_context_managers_are_entered():
    entered = []

    class Ctx:
        def __init__(self, *args):
            self.args = args

        def __enter__(self):
            entered.append(self.args)

        def __exit__(self, *exc):
            entered.append(("exit", self.args[0]))

    rt = FakeRt()
    apply_plan(Plan(), rt, undo_ctx=Ctx, redraw_ctx=Ctx, hold=False)
    assert entered[0] == (True, "Helpers to Layers")
    assert entered[1] == (False,)
    assert entered[-1] == ("exit", True)


def test_exception_swallowed_by_undo_context_is_still_raised(scene):
    """pymxs.undo eats exceptions raised inside its block; the applier must not report success."""

    class Swallow:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return True  # swallow, like pymxs.undo

    scene.LayerManager.add_existing("Taken")
    plan = Plan(layers=[LayerPlan("Taken", None, 1, False)])
    with pytest.raises(RuntimeError, match="Taken"):
        apply_plan(plan, scene, undo_ctx=Swallow, redraw_ctx=Swallow, hold=False)
