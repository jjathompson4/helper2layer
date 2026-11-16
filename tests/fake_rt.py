"""A tiny in-memory stand-in for pymxs.runtime.

Only the surface helper2layer touches is emulated: node iteration, handles,
parent links that keep the world transform on unlink, layers with parents,
hold, delete, select. Transforms are real 4x3 matrices so the transform
check can be exercised. Unlinking re-decomposes the local matrix and drops
shear, which is the pessimistic assumption about what Max does when a
parent has non-uniform scale.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Point3:
    x: float
    y: float
    z: float

    def __iter__(self):
        yield self.x
        yield self.y
        yield self.z


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b, strict=True))


def _mul(a, s):
    return tuple(x * s for x in a)


def _norm(a):
    return math.sqrt(_dot(a, a))


class Matrix3:
    """Row-vector convention like Max: rows 1-3 are the axes, row 4 is translation."""

    def __init__(self, r1, r2, r3, r4):
        self.row1 = Point3(*r1)
        self.row2 = Point3(*r2)
        self.row3 = Point3(*r3)
        self.row4 = Point3(*r4)

    @classmethod
    def identity(cls):
        return cls((1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0))

    @classmethod
    def compose(cls, scale=(1, 1, 1), pos=(0, 0, 0), rot_z_deg=0.0):
        c, s = math.cos(math.radians(rot_z_deg)), math.sin(math.radians(rot_z_deg))
        r1 = (c * scale[0], s * scale[0], 0.0)
        r2 = (-s * scale[1], c * scale[1], 0.0)
        r3 = (0.0, 0.0, scale[2])
        return cls(r1, r2, r3, pos)

    def rows(self):
        return [tuple(self.row1), tuple(self.row2), tuple(self.row3), tuple(self.row4)]

    def __mul__(self, other):
        """self * other: apply self first, then other (child.local * parent.world)."""
        a = self.rows()
        b = other.rows()
        lin_b = b[:3]

        def xform_vec(v):
            return tuple(sum(v[i] * lin_b[i][j] for i in range(3)) for j in range(3))

        r1, r2, r3 = (xform_vec(a[0]), xform_vec(a[1]), xform_vec(a[2]))
        t = xform_vec(a[3])
        r4 = tuple(t[j] + b[3][j] for j in range(3))
        return Matrix3(r1, r2, r3, r4)

    def decomposed(self):
        """Gram-Schmidt: drop shear, return (Matrix3 without shear, scale)."""
        r1, r2, r3, r4 = self.rows()
        sx = _norm(r1)
        u1 = _mul(r1, 1 / sx)
        r2p = _sub(r2, _mul(u1, _dot(r2, u1)))
        sy = _norm(r2p)
        u2 = _mul(r2p, 1 / sy)
        r3p = _sub(_sub(r3, _mul(u1, _dot(r3, u1))), _mul(u2, _dot(r3, u2)))
        sz = _norm(r3p)
        u3 = _mul(r3p, 1 / sz)
        return Matrix3(_mul(u1, sx), _mul(u2, sy), _mul(u3, sz), r4), Point3(sx, sy, sz)


class _INode:
    def __init__(self, handle):
        self.handle = handle


class FakeNode:
    def __init__(self, rt, handle, name, cls, local=None, is_group_head=False):
        self._rt = rt
        self.inode = _INode(handle)
        self.name = name
        self.cls = cls
        self.local = local or Matrix3.identity()
        self.is_group_head = is_group_head
        self._parent = None
        self.children = []
        self.layer = None
        self.deleted = False

    @property
    def handle(self):
        return self.inode.handle

    # --- hierarchy -----------------------------------------------------
    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, value):
        """Max semantics: unlink keeps the world transform."""
        if value is None:
            if self._parent is not None:
                world = self.transform
                self._parent.children.remove(self)
                self._parent = None
                self.local, _ = world.decomposed()
                self._rt.log.append(("unlink", self.name))
            return
        raise NotImplementedError("fake only supports unlinking; build trees with link()")

    def link(self, parent):
        """Build-time link; keeps the local transform as given."""
        self._parent = parent
        parent.children.append(self)

    @property
    def transform(self):
        if self._parent is None:
            return self.local
        return self.local * self._parent.transform

    @property
    def scale(self):
        _, s = self.local.decomposed()
        return s

    def __repr__(self):
        return f"<FakeNode {self.name!r} #{self.handle}>"


class FakeLayer:
    def __init__(self, manager, name):
        self._manager = manager
        self.name = name
        self._parent = None
        self.nodes = []

    def setParent(self, layer):
        self._parent = layer
        self._manager.rt.log.append(("setParent", self.name, layer.name))
        return True

    def getParent(self):
        return self._parent

    def addNode(self, node):
        if node.layer is not None:
            node.layer.nodes.remove(node)
        node.layer = self
        self.nodes.append(node)
        self._manager.rt.log.append(("addNode", self.name, node.name))
        return True

    def __repr__(self):
        return f"<FakeLayer {self.name!r}>"


class FakeLayerManager:
    def __init__(self, rt):
        self.rt = rt
        self.layers = [FakeLayer(self, "0")]

    @property
    def count(self):
        return len(self.layers)

    def getLayer(self, i):
        return self.layers[i]

    def getLayerFromName(self, name):
        for layer in self.layers:
            if layer.name == name:
                return layer
        return None

    def newLayerFromName(self, name):
        if any(layer.name.lower() == name.lower() for layer in self.layers):
            return None
        layer = FakeLayer(self, name)
        self.layers.append(layer)
        self.rt.log.append(("newLayer", name))
        return layer

    def add_existing(self, name, parent_name=None):
        layer = FakeLayer(self, name)
        if parent_name is not None:
            layer._parent = self.getLayerFromName(parent_name)
        self.layers.append(layer)
        return layer


class _MaxOps:
    def __init__(self, rt):
        self.rt = rt

    def getNodeByHandle(self, handle):
        node = self.rt.nodes_by_handle.get(handle)
        return None if node is None or node.deleted else node


class _Selection(list):
    @property
    def count(self):
        return len(self)


class FakeRt:
    def __init__(self):
        self.nodes_by_handle = {}
        self.LayerManager = FakeLayerManager(self)
        self.maxOps = _MaxOps(self)
        self.selection = _Selection()
        self.log = []
        self.holds = 0
        self._next_handle = 1

    # --- scene building ------------------------------------------------
    def add_node(self, name, cls, parent=None, local=None, is_group_head=False):
        node = FakeNode(self, self._next_handle, name, cls, local, is_group_head)
        self._next_handle += 1
        self.nodes_by_handle[node.handle] = node
        if parent is not None:
            node.link(parent)
        return node

    @property
    def objects(self):
        return [n for n in self.nodes_by_handle.values() if not n.deleted]

    # --- runtime surface used by helper2layer -------------------------
    def classOf(self, node):
        return node.cls

    def isGroupHead(self, node):
        return node.is_group_head

    def holdMaxFile(self):
        self.holds += 1
        self.log.append(("hold",))

    def delete(self, node):
        if node.deleted:
            raise RuntimeError(f"{node!r} already deleted")
        for child in list(node.children):
            child.parent = None
        if node._parent is not None:
            node._parent.children.remove(node)
            node._parent = None
        if node.layer is not None:
            node.layer.nodes.remove(node)
        node.deleted = True
        self.log.append(("delete", node.name))

    def select(self, nodes):
        self.selection = _Selection(nodes)
        self.log.append(("select", tuple(n.name for n in nodes)))

    def clearSelection(self):
        self.selection = _Selection()
