import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from helper2layer import ExistingLayer, NodeInfo, Options  # noqa: E402

# A tree spec is a list of (name, cls, children) or (name, cls, children, extras)
# where extras is a dict with optional keys: group_head (bool).


def build_nodes(tree, start_handle=1):
    """Turn a nested tuple spec into {handle: NodeInfo} with sequential handles."""
    nodes = {}
    counter = [start_handle]

    def add(spec, parent):
        name, cls, children = spec[0], spec[1], spec[2]
        extras = spec[3] if len(spec) > 3 else {}
        h = counter[0]
        counter[0] += 1
        nodes[h] = NodeInfo(
            handle=h,
            name=name,
            cls=cls,
            parent=parent,
            is_group_head=extras.get("group_head", False),
        )
        if parent is not None:
            nodes[parent].children.append(h)
        for child in children:
            add(child, h)

    for spec in tree:
        add(spec, None)
    return nodes


def handles_named(nodes, name):
    return [h for h, n in nodes.items() if n.name == name]


def handle(nodes, name):
    hs = handles_named(nodes, name)
    assert len(hs) == 1, f"{name!r} matches {len(hs)} nodes"
    return hs[0]


def layers_of(plan):
    """[(name, parent_name, reuse), ...] in plan order."""
    return [(lp.name, lp.parent_name, lp.reuse) for lp in plan.layers]


def layer_names(plan):
    return [lp.name for lp in plan.layers]


DEFAULT_LAYERS = [ExistingLayer("0", None)]


@pytest.fixture
def opts_all():
    return Options(scope="all", flatten=True, hold=True)


@pytest.fixture
def opts_selected():
    return Options(scope="selected", flatten=True, hold=True)


@pytest.fixture
def opts_mirror():
    return Options(scope="all", flatten=False, hold=True)


@pytest.fixture
def rhino_tree():
    """Three nesting levels, repeated 'Interior' under two parents, an empty layer,
    a group (block instance), geometry with a child."""
    return [
        ("Walls", "Dummy", [
            ("Interior", "Dummy", [
                ("wall_int_a", "Editable_Mesh", []),
                ("Trim", "Dummy", [("trim_a", "Editable_Mesh", [])]),
            ]),
            ("Exterior", "Dummy", [("wall_ext_a", "Editable_Mesh", [])]),
        ]),
        ("Ceilings", "Dummy", [
            ("Interior", "Dummy", [("ceiling_a", "Editable_Mesh", [])]),
            ("Empty", "Dummy", []),
        ]),
        ("Furniture", "Dummy", [
            ("Chair", "Dummy", [
                ("chair_seat", "Editable_Mesh", []),
                ("chair_leg", "Editable_Mesh", []),
            ], {"group_head": True}),
            ("Table", "Editable_Mesh", [("table_top", "Editable_Mesh", [])]),
        ]),
    ]
