#/*
# * Copyright (C) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""Structural comparison of two device trees.

Produces a :class:`Delta` describing how a *target* tree differs from a
*source* tree, at node and property granularity. The delta is
format-agnostic; renderers (overlay / unified / equivalence) consume it.

Phase 1 (this module's initial form): path-keyed matching with literal
property-value comparison. Phandle-value normalization (compare by
resolved target, not raw number), alternate keys, and the output
renderers land in later phases. See
``agent-files/tree-compare-design.md``.
"""

import lopper.log

lopper.log._init(__name__)


# Derived / bookkeeping nodes are never authored by the user and churn
# between compiles, so they are excluded from a user-facing diff.
_COMPARE_EXCLUDE_NODES = frozenset({
    "/__symbols__",
    "/__fixups__",
    "/__local_fixups__",
    "/aliases",
    "/__lopper-overlays__",
    "/__lopper-phandles__",
})


class NodeDelta:
    """Property-level differences for a node present in both trees.

    Attributes:
        path (str): the node's absolute path (the match key in Phase 1).
        node_a (LopperNode): the node in the source tree.
        node_b (LopperNode): the node in the target tree.
        added_props (list): LopperProp objects present in B, absent in A.
        removed_props (list): LopperProp objects present in A, absent in B.
        changed_props (list): (name, value_a, value_b) tuples for
            properties present in both whose values differ.
    """

    def __init__(self, path, node_a, node_b):
        self.path = path
        self.node_a = node_a
        self.node_b = node_b
        self.added_props = []
        self.removed_props = []
        self.changed_props = []

    def __bool__(self):
        return bool(self.added_props or self.removed_props or self.changed_props)

    def __repr__(self):
        return (f"NodeDelta({self.path!r}: +{len(self.added_props)} "
                f"-{len(self.removed_props)} ~{len(self.changed_props)})")


class Delta:
    """The structural difference between a source tree (A) and target (B).

    Attributes:
        key (str): the node-matching key used to produce this delta.
        added_nodes (list): LopperNode objects only in the target (B).
        removed_nodes (list): LopperNode objects only in the source (A).
        changed_nodes (list): NodeDelta objects for nodes in both trees
            whose properties differ.
    """

    def __init__(self, key="path"):
        self.key = key
        self.added_nodes = []
        self.removed_nodes = []
        self.changed_nodes = []

    def __bool__(self):
        return bool(self.added_nodes or self.removed_nodes or self.changed_nodes)

    def equivalent(self):
        """Return True if the two trees are structurally equivalent."""
        return not bool(self)

    def __repr__(self):
        return (f"Delta(key={self.key!r}: added={len(self.added_nodes)} "
                f"removed={len(self.removed_nodes)} "
                f"changed={len(self.changed_nodes)})")


def _node_map(tree):
    """Map abs_path -> LopperNode for user-facing nodes of a tree.

    Excludes derived/bookkeeping nodes (and their descendants) that would
    otherwise add noise to the diff.
    """
    node_map = {}
    for node in tree:
        path = node.abs_path
        if path in _COMPARE_EXCLUDE_NODES:
            continue
        # skip descendants of excluded nodes too
        if any(path.startswith(ex + "/") for ex in _COMPARE_EXCLUDE_NODES):
            continue
        node_map[path] = node
    return node_map


def _values_equal(prop_a, prop_b):
    """Phase 1: literal comparison of property values.

    Phandle-aware comparison (by resolved target) is added in Phase 2.
    """
    return prop_a.value == prop_b.value


def _compare_node(node_a, node_b, path):
    """Diff the properties of two nodes at the same key. Returns a
    NodeDelta, or None if the nodes' properties are identical."""
    delta = NodeDelta(path, node_a, node_b)

    props_a = node_a.__props__
    props_b = node_b.__props__

    for name, prop in props_b.items():
        if name not in props_a:
            delta.added_props.append(prop)

    for name, prop in props_a.items():
        if name not in props_b:
            delta.removed_props.append(prop)
        else:
            if not _values_equal(prop, props_b[name]):
                delta.changed_props.append((name, prop.value, props_b[name].value))

    return delta if delta else None


def compare(tree_a, tree_b, key="path"):
    """Compare two LopperTrees and return a :class:`Delta`.

    Args:
        tree_a (LopperTree): the source / base tree.
        tree_b (LopperTree): the target tree.
        key (str): node-matching key. Phase 1 supports only "path".

    Returns:
        Delta: the structural difference (B relative to A).
    """
    if key != "path":
        raise NotImplementedError(
            f"compare key {key!r} not supported yet (Phase 3); use 'path'")

    delta = Delta(key=key)

    map_a = _node_map(tree_a)
    map_b = _node_map(tree_b)
    paths_a = set(map_a)
    paths_b = set(map_b)

    for path in sorted(paths_b - paths_a):
        delta.added_nodes.append(map_b[path])

    for path in sorted(paths_a - paths_b):
        delta.removed_nodes.append(map_a[path])

    for path in sorted(paths_a & paths_b):
        node_delta = _compare_node(map_a[path], map_b[path], path)
        if node_delta:
            delta.changed_nodes.append(node_delta)

    return delta
