"""Planner tests: pure Python, no Max."""
from conftest import DEFAULT_LAYERS, build_nodes, handle, handles_named, layer_names, layers_of

from helper2layer import ExistingLayer, NodeInfo, Options, build_plan, is_helper


def _by_name(plan):
    return {lp.name: lp for lp in plan.layers}


# --- nesting and order -------------------------------------------------------


def test_three_levels_parent_first(rhino_tree, opts_all):
    nodes = build_nodes(rhino_tree)
    plan = build_plan(nodes, DEFAULT_LAYERS, [], opts_all)
    names = layer_names(plan)
    by = _by_name(plan)
    assert by["Trim"].parent_name == "Walls::Interior"
    assert by["Walls::Interior"].parent_name == "Walls"
    assert by["Walls"].parent_name is None
    for lp in plan.layers:
        if lp.parent_name is not None:
            assert names.index(lp.parent_name) < names.index(lp.name)
    assert plan.top_level_names == ["Ceilings", "Furniture", "Walls"]
    assert plan.helper_count == 8  # Walls, Interior, Trim, Exterior, Ceilings, Interior, Empty, Furniture


def test_helper_count_matches_converted(rhino_tree, opts_all):
    nodes = build_nodes(rhino_tree)
    plan = build_plan(nodes, DEFAULT_LAYERS, [], opts_all)
    expected = {h for h, n in nodes.items() if is_helper(n)}
    assert set(plan.converted) == expected
    assert plan.helper_count == len(expected)


# --- naming ------------------------------------------------------------------


def test_repeated_names_under_different_parents_use_path_style(rhino_tree, opts_all):
    plan = build_plan(build_nodes(rhino_tree), DEFAULT_LAYERS, [], opts_all)
    names = layer_names(plan)
    assert "Walls::Interior" in names
    assert "Ceilings::Interior" in names
    assert "Interior" not in names
    assert "Exterior" in names  # unique names stay plain


def test_repeated_name_under_same_parent_gets_suffix(opts_all):
    tree = [("Furniture", "Dummy", [
        ("Chair", "Dummy", [("a", "Editable_Mesh", [])]),
        ("Chair", "Dummy", [("b", "Editable_Mesh", [])]),
    ])]
    plan = build_plan(build_nodes(tree), DEFAULT_LAYERS, [], opts_all)
    assert layer_names(plan) == ["Furniture", "Furniture::Chair", "Furniture::Chair (2)"]


def test_root_name_collides_with_default_layer_zero(opts_all):
    tree = [("0", "Dummy", [("m", "Editable_Mesh", [])])]
    plan = build_plan(build_nodes(tree), DEFAULT_LAYERS, [], opts_all)
    assert layers_of(plan) == [("0 (2)", None, False)]
    # Even if the reader somehow didn't report layer 0, never plan to create or reuse it.
    plan = build_plan(build_nodes(tree), [], [], opts_all)
    assert layers_of(plan) == [("0 (2)", None, False)]


def test_existing_layer_same_name_same_parent_is_reused(opts_all):
    tree = [("Walls", "Dummy", [("Interior", "Dummy", [("m", "Editable_Mesh", [])])])]
    existing = DEFAULT_LAYERS + [ExistingLayer("Walls"), ExistingLayer("Interior", "Walls")]
    plan = build_plan(build_nodes(tree), existing, [], opts_all)
    assert layers_of(plan) == [("Walls", None, True), ("Interior", "Walls", True)]
    assert plan.layers_to_create == 0
    assert plan.layers_to_reuse == 2


def test_existing_layer_same_name_different_parent_is_not_reused(opts_all):
    tree = [("Walls", "Dummy", [("Interior", "Dummy", [("m", "Editable_Mesh", [])])])]
    existing = DEFAULT_LAYERS + [ExistingLayer("Interior", None), ExistingLayer("Walls", None)]
    plan = build_plan(build_nodes(tree), existing, [], opts_all)
    assert layers_of(plan) == [("Walls", None, True), ("Interior (2)", "Walls", False)]


def test_collision_check_is_case_insensitive_reuse_is_exact(opts_all):
    tree = [("Walls", "Dummy", [("m", "Editable_Mesh", [])])]
    existing = DEFAULT_LAYERS + [ExistingLayer("walls", None)]
    plan = build_plan(build_nodes(tree), existing, [], opts_all)
    assert layers_of(plan) == [("Walls (2)", None, False)]


def test_suffix_skips_existing_names(opts_all):
    tree = [("Walls", "Dummy", [("m", "Editable_Mesh", [])])]
    existing = DEFAULT_LAYERS + [ExistingLayer("Walls", "Other"), ExistingLayer("Walls (2)", "Other"), ExistingLayer("Other")]
    plan = build_plan(build_nodes(tree), existing, [], opts_all)
    assert layers_of(plan) == [("Walls (3)", None, False)]


def test_blank_names_become_unnamed_helper(opts_all):
    tree = [
        ("", "Dummy", [("a", "Editable_Mesh", [])]),
        ("   ", "Point", [("b", "Editable_Mesh", [])]),
    ]
    plan = build_plan(build_nodes(tree), DEFAULT_LAYERS, [], opts_all)
    assert layer_names(plan) == ["Unnamed Helper", "Unnamed Helper (2)"]


def test_rerun_with_suffixed_siblings_reuses_everything(opts_mirror):
    tree = [("Furniture", "Dummy", [
        ("Chair", "Dummy", [("a", "Editable_Mesh", [])]),
        ("Chair", "Dummy", [("b", "Editable_Mesh", [])]),
    ])]
    nodes = build_nodes(tree)
    first = build_plan(nodes, DEFAULT_LAYERS, [], opts_mirror)
    produced = DEFAULT_LAYERS + [ExistingLayer(lp.name, lp.parent_name) for lp in first.layers]
    second = build_plan(nodes, produced, [], opts_mirror)
    assert layer_names(second) == layer_names(first)
    assert second.layers_to_create == 0
    assert second.layers_to_reuse == 3


# --- exclusions --------------------------------------------------------------


def test_group_heads_are_not_converted_but_travel_with_members(rhino_tree, opts_all):
    nodes = build_nodes(rhino_tree)
    plan = build_plan(nodes, DEFAULT_LAYERS, [], opts_all)
    chair = handle(nodes, "Chair")
    seat = handle(nodes, "chair_seat")
    assert chair not in plan.converted
    assert plan.assignments[chair] == "Furniture"
    assert plan.assignments[seat] == "Furniture"
    assert chair in plan.unlink  # direct child of a converted helper
    assert seat not in plan.unlink  # stays grouped under the head


def test_compass_and_daylight_untouched_at_root(opts_all):
    tree = [
        ("Compass001", "Compass", [("Daylight001", "Daylight", [])]),
        ("Site", "Dummy", [("ground", "Editable_Mesh", [])]),
    ]
    nodes = build_nodes(tree)
    plan = build_plan(nodes, DEFAULT_LAYERS, [], opts_all)
    compass = handle(nodes, "Compass001")
    sun = handle(nodes, "Daylight001")
    assert plan.converted == [handle(nodes, "Site")]
    assert compass not in plan.assignments and sun not in plan.assignments
    assert compass not in plan.unlink and compass not in plan.delete


def test_compass_under_helper_is_a_regular_child(opts_all):
    tree = [("Site", "Dummy", [
        ("Compass001", "Compass", [("Daylight001", "Daylight", [])]),
        ("ground", "Editable_Mesh", []),
    ])]
    nodes = build_nodes(tree)
    plan = build_plan(nodes, DEFAULT_LAYERS, [], opts_all)
    compass = handle(nodes, "Compass001")
    sun = handle(nodes, "Daylight001")
    assert plan.assignments[compass] == "Site"
    assert plan.assignments[sun] == "Site"
    assert compass in plan.unlink
    assert sun not in plan.unlink  # stays parented to the compass
    assert compass not in plan.delete


def test_container_and_other_helper_classes_ignored(opts_all):
    tree = [
        ("Container001", "Container", [("c_mesh", "Editable_Mesh", [])]),
        ("Bone001", "BoneGeometry", [("Bone002", "BoneGeometry", [])]),
        ("Layer", "Dummy", [("m", "Editable_Mesh", [])]),
    ]
    nodes = build_nodes(tree)
    plan = build_plan(nodes, DEFAULT_LAYERS, [], opts_all)
    assert plan.converted == [handle(nodes, "Layer")]
    assert set(plan.assignments) == {handle(nodes, "m")}


def test_geometry_with_children(rhino_tree, opts_all):
    nodes = build_nodes(rhino_tree)
    plan = build_plan(nodes, DEFAULT_LAYERS, [], opts_all)
    table = handle(nodes, "Table")
    top = handle(nodes, "table_top")
    assert plan.assignments[table] == "Furniture"
    assert plan.assignments[top] == "Furniture"
    assert table in plan.unlink
    assert top not in plan.unlink


# --- scope -------------------------------------------------------------------


def test_convert_selected_subtree_whose_parent_is_not_selected(rhino_tree, opts_selected):
    nodes = build_nodes(rhino_tree)
    walls = handle(nodes, "Walls")
    interior = next(h for h in handles_named(nodes, "Interior") if nodes[h].parent == walls)
    plan = build_plan(nodes, DEFAULT_LAYERS, [interior], opts_selected)
    assert plan.converted == [interior, handle(nodes, "Trim")]
    assert layers_of(plan) == [("Interior", None, False), ("Trim", "Interior", False)]
    assert plan.top_level_names == ["Interior"]
    assert plan.assignments == {
        handle(nodes, "wall_int_a"): "Interior",
        handle(nodes, "trim_a"): "Trim",
    }
    assert plan.unlink == [handle(nodes, "wall_int_a"), handle(nodes, "trim_a")]
    assert plan.delete == [handle(nodes, "Trim"), interior]
    assert walls not in plan.delete


def test_convert_selected_ignores_non_helpers_in_selection(rhino_tree, opts_selected):
    nodes = build_nodes(rhino_tree)
    plan = build_plan(nodes, DEFAULT_LAYERS, [handle(nodes, "Table"), handle(nodes, "Chair")], opts_selected)
    assert plan.helper_count == 0
    assert plan.layers == [] and plan.assignments == {}


def test_convert_selected_with_empty_selection_does_nothing(rhino_tree, opts_selected):
    plan = build_plan(build_nodes(rhino_tree), DEFAULT_LAYERS, [], opts_selected)
    assert plan.helper_count == 0


def test_convert_all_childless_rules(opts_all):
    tree = [
        ("Marker", "Point", []),  # top-level, childless: ignored
        ("Ceilings", "Dummy", [
            ("Empty", "Dummy", []),  # childless under a converted helper: becomes an empty layer
            ("c", "Editable_Mesh", []),
        ]),
    ]
    nodes = build_nodes(tree)
    plan = build_plan(nodes, DEFAULT_LAYERS, [], opts_all)
    assert handle(nodes, "Marker") not in plan.converted
    assert handle(nodes, "Marker") not in plan.assignments
    assert ("Empty", "Ceilings", False) in layers_of(plan)


def test_convert_all_rig_shows_in_preview(opts_all):
    tree = [
        ("Luminaire", "Dummy", [
            ("housing", "Editable_Mesh", []),
            ("Photometric_Light001", "Free_Light", []),
        ]),
        ("Walls", "Dummy", [("w", "Editable_Mesh", [])]),
    ]
    plan = build_plan(build_nodes(tree), DEFAULT_LAYERS, [], opts_all)
    assert plan.top_level_names == ["Luminaire", "Walls"]
    assert len(plan.warnings) == 1
    assert "Luminaire" in plan.warnings[0] and "Photometric_Light001" in plan.warnings[0]


def test_convert_selected_leaves_rig_alone(rhino_tree, opts_selected):
    tree = rhino_tree + [("Luminaire", "Dummy", [("lamp", "Free_Light", [])])]
    nodes = build_nodes(tree)
    plan = build_plan(nodes, DEFAULT_LAYERS, [handle(nodes, "Walls")], opts_selected)
    assert handle(nodes, "Luminaire") not in plan.converted
    assert handle(nodes, "lamp") not in plan.assignments
    assert plan.warnings == []


def test_unknown_scope_raises(rhino_tree):
    import pytest

    with pytest.raises(ValueError):
        build_plan(build_nodes(rhino_tree), DEFAULT_LAYERS, [], Options(scope="bogus"))


# --- modes -------------------------------------------------------------------


def test_flatten_unlinks_direct_children_and_deletes_deepest_first(rhino_tree, opts_all):
    nodes = build_nodes(rhino_tree)
    plan = build_plan(nodes, DEFAULT_LAYERS, [], opts_all)
    depth = {}
    for h in plan.delete:
        d, p = 0, nodes[h].parent
        while p is not None:
            d, p = d + 1, nodes[p].parent
        depth[h] = d
    depths = [depth[h] for h in plan.delete]
    assert depths == sorted(depths, reverse=True)
    for h in plan.unlink:
        assert h not in plan.converted
        assert nodes[h].parent in plan.converted
    assert not any(h in plan.assignments for h in plan.converted)


def test_mirror_mode_keeps_hierarchy_and_helpers(rhino_tree, opts_mirror):
    nodes = build_nodes(rhino_tree)
    plan = build_plan(nodes, DEFAULT_LAYERS, [], opts_mirror)
    assert plan.unlink == [] and plan.delete == []
    for lp in plan.layers:
        assert plan.assignments[lp.helper_handle] == lp.name
    assert plan.objects_to_move == len(nodes)  # every node has a converted ancestor or is one


def test_mirror_mode_rerun_creates_no_duplicates(rhino_tree, opts_mirror):
    nodes = build_nodes(rhino_tree)
    first = build_plan(nodes, DEFAULT_LAYERS, [], opts_mirror)
    produced = DEFAULT_LAYERS + [ExistingLayer(lp.name, lp.parent_name) for lp in first.layers]
    second = build_plan(nodes, produced, [], opts_mirror)
    assert layer_names(second) == layer_names(first)
    assert second.layers_to_create == 0
    assert second.layers_to_reuse == first.helper_count


def test_nodes_outside_any_helper_are_untouched(rhino_tree, opts_all):
    tree = rhino_tree + [("loose_mesh", "Editable_Mesh", [("loose_child", "Editable_Mesh", [])])]
    nodes = build_nodes(tree)
    plan = build_plan(nodes, DEFAULT_LAYERS, [], opts_all)
    for name in ("loose_mesh", "loose_child"):
        h = handle(nodes, name)
        assert h not in plan.assignments and h not in plan.unlink


def test_summary_lines_and_counts(rhino_tree, opts_all):
    plan = build_plan(build_nodes(rhino_tree), DEFAULT_LAYERS, [], opts_all)
    lines = plan.summary_lines()
    assert lines[0] == f"Helpers to convert: {plan.helper_count}"
    assert f"Layers to create: {plan.layers_to_create}" in lines
    assert any(line.startswith("Mode: Flatten") for line in lines)
    assert plan.layer_name_for_helper(plan.converted[0]) == plan.layers[0].name
    assert plan.layer_name_for_helper(-1) is None


def test_is_helper():
    assert is_helper(NodeInfo(1, "a", "Dummy"))
    assert is_helper(NodeInfo(1, "a", "Point"))
    assert not is_helper(NodeInfo(1, "a", "Dummy", is_group_head=True))
    assert not is_helper(NodeInfo(1, "a", "Compass"))
    assert not is_helper(NodeInfo(1, "a", "Editable_Mesh"))
