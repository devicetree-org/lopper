#/*
# * Copyright (C) 2026 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""Structural comparison of two device trees.

Produces a :class:`Delta` describing how a *target* tree differs from a
*source* tree, at node and property granularity. The delta is
format-agnostic; renderers (fragment / unified / equivalence) consume it.

Implemented: node matching by path/label/address/name (non-path keys
fall back to path for nodes lacking or sharing that key, and record a
"moved" node when a matched pair's paths differ); property
add/remove/change with phandle-value normalization (phandle references
compare by resolved target, not raw number). Not yet: the output
renderers (fragment / unified / equivalence file output). See
``agent-files/tree-compare-design.md``.

Relationship to existing core comparison routines:

* ``LopperNode.__eq__`` / ``__hash__`` define node identity by
  ``abs_path``. This module's path keying is consistent with that.
* ``LopperProp.compare()`` is a *fuzzy, asymmetric matcher* used by the
  lop conditional-selection engine (single-in-list, string-as-regex).
  It answers "does this property satisfy this condition?", NOT "are
  these two properties equal?", so it is deliberately NOT used here --
  a structural diff needs exact/semantic equality (see _values_equal).
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

# Auto-generated bookkeeping properties. Phandle *numbers* are assigned
# per compile and differ between trees; the `phandle` property is the raw
# number itself, so it churns and is excluded (phandle *references* are
# instead normalized by resolved target, see _normalized_value).
_COMPARE_EXCLUDE_PROPS = frozenset({
    "phandle",
    "linux,phandle",
})


class NodeDelta:
    """Property-level differences for a node present in both trees.

    Attributes:
        path (str): the target node's absolute path (source path if the
            node was removed). With a non-path match key this is the
            target's location even if it moved.
        node_a (LopperNode): the node in the source tree.
        node_b (LopperNode): the node in the target tree.
        moved (tuple or None): (source_path, target_path) when the two
            nodes were matched by a non-path key and their paths differ;
            None otherwise.
        added_props (list): LopperProp objects present in B, absent in A.
        removed_props (list): LopperProp objects present in A, absent in B.
        changed_props (list): (name, value_a, value_b) tuples for
            properties present in both whose values differ.
    """

    def __init__(self, path, node_a, node_b):
        self.path = path
        self.node_a = node_a
        self.node_b = node_b
        self.moved = None
        self.added_props = []
        self.removed_props = []
        self.changed_props = []

    def __bool__(self):
        return bool(self.moved or self.added_props
                    or self.removed_props or self.changed_props)

    def __repr__(self):
        moved = " moved" if self.moved else ""
        return (f"NodeDelta({self.path!r}:{moved} +{len(self.added_props)} "
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
        self.tree_a = None   # source tree (the fragment applies to this)
        self.tree_b = None   # target tree
        self.added_nodes = []
        self.removed_nodes = []
        self.changed_nodes = []

    def __bool__(self):
        return bool(self.added_nodes or self.removed_nodes or self.changed_nodes)

    def equivalent(self):
        """Return True if the two trees are structurally equivalent."""
        return not bool(self)

    def emit(self, fmt="unified", output=None, as_string=False):
        """Render this delta.

        Args:
            fmt (str): "equivalence" (one-line equivalent/differ),
                "unified" (human/traceability +/- text), or "fragment"
                (a concatenated device-tree fragment of the delta).
            output (str, optional): path to write the rendered text to.
                When given, the file is written and its path returned.
            as_string (bool): ignored when ``output`` is set; otherwise
                the rendered text is returned (this is also the default).

        Returns:
            str: the rendered text, or the output path when ``output`` set.
        """
        if fmt == "equivalence":
            text = "equivalent" if self.equivalent() else "differ"
        elif fmt == "unified":
            text = _render_unified(self)
        elif fmt == "fragment":
            text = _render_fragment(self)
        else:
            raise NotImplementedError(f"unknown compare output format {fmt!r}")
        return _deliver(text, output, as_string)

    def __repr__(self):
        return (f"Delta(key={self.key!r}: added={len(self.added_nodes)} "
                f"removed={len(self.removed_nodes)} "
                f"changed={len(self.changed_nodes)})")


SUPPORTED_KEYS = frozenset({"path", "label", "address", "name"})


def _user_nodes(tree):
    """List of user-facing nodes of a tree.

    Excludes derived/bookkeeping nodes (and their descendants) that would
    otherwise add noise to the diff.
    """
    nodes = []
    for node in tree:
        path = node.abs_path
        if path in _COMPARE_EXCLUDE_NODES:
            continue
        if any(path.startswith(ex + "/") for ex in _COMPARE_EXCLUDE_NODES):
            continue
        nodes.append(node)
    return nodes


def _node_key(node, key):
    """The value used to match a node under the given key, or None if the
    node has no value for that key (e.g. no label)."""
    if key == "path":
        return node.abs_path
    if key == "label":
        return node.label or None
    name = node.name
    if key == "address":
        return name.split("@", 1)[1] if "@" in name else None
    if key == "name":
        return name.split("@", 1)[0] if "@" in name else name
    raise NotImplementedError(f"compare key {key!r} is not supported")


def _unique_key_index(nodes, key):
    """Map key_value -> node for values that are non-None and *unique*
    within ``nodes``. Ambiguous (duplicate) or absent key values are
    omitted, so those nodes fall through to path matching."""
    counts = {}
    first = {}
    for node in nodes:
        value = _node_key(node, key)
        if value is None:
            continue
        counts[value] = counts.get(value, 0) + 1
        first.setdefault(value, node)
    return {v: first[v] for v, c in counts.items() if c == 1}


def _match(nodes_a, nodes_b, key):
    """Correlate nodes of two trees under a match key.

    Returns (pairs, only_a, only_b) where pairs is a list of
    (node_a, node_b). Matching is two-pass: first by the requested key
    (unique values present in both trees), then the remainder by abs_path.
    For key="path" only the path pass runs. Nodes matched by a non-path
    key whose paths differ are "moved" (recorded on the NodeDelta).
    """
    pairs = []
    consumed_a = set()
    consumed_b = set()

    if key != "path":
        index_a = _unique_key_index(nodes_a, key)
        index_b = _unique_key_index(nodes_b, key)
        for value, node_a in index_a.items():
            node_b = index_b.get(value)
            if node_b is not None:
                pairs.append((node_a, node_b))
                consumed_a.add(id(node_a))
                consumed_b.add(id(node_b))

    remaining_b_by_path = {n.abs_path: n for n in nodes_b
                           if id(n) not in consumed_b}
    for node_a in nodes_a:
        if id(node_a) in consumed_a:
            continue
        node_b = remaining_b_by_path.get(node_a.abs_path)
        if node_b is not None and id(node_b) not in consumed_b:
            pairs.append((node_a, node_b))
            consumed_a.add(id(node_a))
            consumed_b.add(id(node_b))

    only_a = [n for n in nodes_a if id(n) not in consumed_a]
    only_b = [n for n in nodes_b if id(n) not in consumed_b]
    return pairs, only_a, only_b


def _normalized_value(prop):
    """Return a comparison-normalized value for a property.

    For phandle-bearing properties, each phandle cell is replaced by a
    stable token derived from its *resolved target* (the target node's
    abs_path), so that a difference in phandle *numbers* between two
    independently-compiled trees does not register as a change. Non-phandle
    cells and non-integer values are returned as-is.

    Mirrors the phandle_map() consumption pattern used elsewhere in lopper
    (see assists/compose_non_linux.py): flatten the record map, align it
    1:1 with the value cells, and swap phandle slots for their target.
    """
    raw = prop.value
    if not isinstance(raw, list) or not all(isinstance(v, int) for v in raw):
        return raw

    try:
        pmap = prop.phandle_map()
    except Exception:
        return raw
    if not pmap:
        return raw

    flat = [slot for record in pmap for slot in record]
    if len(flat) != len(raw):
        return raw

    normalized = []
    for slot, val in zip(flat, raw):
        target_path = getattr(slot, "abs_path", None)
        if target_path is not None:
            # phandle slot -> canonical, compile-stable target token
            # (string can't collide with the integer cells it replaces)
            normalized.append("@" + target_path)
        else:
            normalized.append(val)
    return normalized


def _values_equal(prop_a, prop_b):
    """Compare two properties, normalizing phandle references by target."""
    return _normalized_value(prop_a) == _normalized_value(prop_b)


def _compare_node(node_a, node_b):
    """Diff a matched pair of nodes. Returns a NodeDelta, or None if the
    nodes are identical (same path and properties)."""
    delta = NodeDelta(node_b.abs_path, node_a, node_b)

    if node_a.abs_path != node_b.abs_path:
        delta.moved = (node_a.abs_path, node_b.abs_path)

    props_a = {n: p for n, p in node_a.__props__.items()
               if n not in _COMPARE_EXCLUDE_PROPS}
    props_b = {n: p for n, p in node_b.__props__.items()
               if n not in _COMPARE_EXCLUDE_PROPS}

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
        key (str): node-matching key, one of SUPPORTED_KEYS ("path",
            "label", "address", "name"). Non-path keys fall back to path
            matching for nodes that lack (or share) that key value.

    Returns:
        Delta: the structural difference (B relative to A).
    """
    if key not in SUPPORTED_KEYS:
        raise NotImplementedError(
            f"compare key {key!r} not supported; use one of "
            f"{sorted(SUPPORTED_KEYS)}")

    delta = Delta(key=key)
    delta.tree_a = tree_a
    delta.tree_b = tree_b

    nodes_a = _user_nodes(tree_a)
    nodes_b = _user_nodes(tree_b)
    pairs, only_a, only_b = _match(nodes_a, nodes_b, key)

    delta.added_nodes = sorted(only_b, key=lambda n: n.abs_path)
    delta.removed_nodes = sorted(only_a, key=lambda n: n.abs_path)

    for node_a, node_b in sorted(pairs, key=lambda pr: pr[1].abs_path):
        node_delta = _compare_node(node_a, node_b)
        if node_delta:
            delta.changed_nodes.append(node_delta)

    return delta


# --------------------------------------------------------------------------
# Renderers
# --------------------------------------------------------------------------

def _deliver(text, output, as_string):
    """Return the rendered text, or write it to ``output`` and return the
    path. ``as_string`` is retained for API symmetry; returning the text
    is the default when no output file is requested."""
    if not text.endswith("\n"):
        text += "\n"
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
        return output
    return text


def _fmt_value(value):
    """Render a property value compactly for the unified format."""
    if value in ([], [""], "", None):
        return ""  # boolean / present-empty property
    if isinstance(value, list) and value and all(isinstance(v, int) for v in value):
        return "<" + " ".join(hex(v) for v in value) + ">"
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return ", ".join('"%s"' % v for v in value)
    return repr(value)


def _fmt_prop(name, value):
    rendered = _fmt_value(value)
    return name if rendered == "" else f"{name} = {rendered}"


def _render_unified(delta):
    """Deterministic, path-sorted +/- rendering of a Delta.

    Removed nodes are prefixed '-', added '+', changed nodes are listed
    with their property changes indented ('+ ' added, '- ' removed,
    '~ ' changed). Output ordering is fully sorted so re-runs on the same
    inputs are byte-identical.
    """
    lines = ["--- a", "+++ b"]

    for node in delta.removed_nodes:
        lines.append(f"- {node.abs_path}")

    for node in delta.added_nodes:
        lines.append(f"+ {node.abs_path}")

    for nd in delta.changed_nodes:
        if nd.moved:
            lines.append(f"  {nd.path} (moved from {nd.moved[0]})")
        else:
            lines.append(f"  {nd.path}")
        for prop in sorted(nd.added_props, key=lambda p: p.name):
            lines.append(f"    + {_fmt_prop(prop.name, prop.value)}")
        for prop in sorted(nd.removed_props, key=lambda p: p.name):
            lines.append(f"    - {_fmt_prop(prop.name, prop.value)}")
        for name, val_a, val_b in sorted(nd.changed_props, key=lambda c: c[0]):
            lines.append(f"    ~ {name}: {_fmt_value(val_a)} -> {_fmt_value(val_b)}")

    return "\n".join(lines) + "\n"


# --- fragment renderer -----------------------------------------------------
#
# The fragment is an *include-fragment* (no /dts-v1/, no /plugin/): it is
# meant to be concatenated with the source tree and recompiled, which is
# how dtc/Zephyr resolve `&label` and apply `/delete-*/`. Applying the
# fragment to the source reproduces the target:  source + fragment == target.
#
# Deletions have no in-memory tree representation in lopper, and
# /delete-property/ must appear inside the target node's block, so this
# renderer serializes directly from the Delta rather than via the tree
# writer -- reusing phandle_map() so phandle cells render as `&label`.

def _node_ref(node):
    """DTS reference to an existing node: `&label` if it has one, else the
    path-reference form `&{/abs/path}` (both are valid dtc node references)."""
    return ("&" + node.label) if node.label else ("&{" + node.abs_path + "}")


def _phandle_cells(prop, raw):
    """Render an integer cell list, emitting phandle slots as `&label`."""
    try:
        pmap = prop.phandle_map()
    except Exception:
        pmap = []
    flat = [slot for record in pmap for slot in record] if pmap else []
    if len(flat) != len(raw):
        return [hex(v) for v in raw]
    cells = []
    for slot, val in zip(flat, raw):
        label = getattr(slot, "label", None)
        cells.append("&" + label if label else hex(val))
    return cells


def _fragment_value(prop):
    """Render a property value as fragment DTS, or None if unrenderable."""
    raw = prop.value
    if raw in ([], [""], "", None):
        return None  # boolean / present-empty -> caller emits `name;`
    if isinstance(raw, str):
        return '"%s"' % raw
    if isinstance(raw, list) and raw and all(isinstance(v, str) for v in raw):
        return ", ".join('"%s"' % v for v in raw)
    if isinstance(raw, list) and all(isinstance(v, int) for v in raw):
        return "<" + " ".join(_phandle_cells(prop, raw)) + ">"
    return None


def _prop_lines(props, indent):
    lines = []
    for prop in props:
        if prop.name in _COMPARE_EXCLUDE_PROPS:
            continue
        value = _fragment_value(prop)
        if value is None and prop.value in ([], [""], "", None):
            lines.append(f"{indent}{prop.name};")
        elif value is not None:
            lines.append(f"{indent}{prop.name} = {value};")
        else:
            lopper.log._warning(
                f"compare fragment: cannot render property {prop.name!r} "
                f"on {prop.node.abs_path if prop.node else '?'}; skipped")
    return lines


def _serialize_subtree(node, indent):
    """Emit a node's body: its properties then its child node blocks."""
    lines = _prop_lines(node.__props__.values(), indent)
    for child in node.child_nodes.values():
        if child.abs_path in _COMPARE_EXCLUDE_NODES:
            continue
        label = (child.label + ": ") if child.label else ""
        lines.append(f"{indent}{label}{child.name} {{")
        lines.extend(_serialize_subtree(child, indent + "\t"))
        lines.append(f"{indent}}};")
    return lines


def _render_fragment(delta):
    """Render the delta as a concatenated device-tree fragment (source -> target)."""
    blocks = []

    # changed nodes: override added/changed props, delete removed props
    for nd in delta.changed_nodes:
        body = []
        wanted = ([p.name for p in nd.added_props]
                  + [name for (name, _a, _b) in nd.changed_props])
        props = [nd.node_b.__props__[n] for n in wanted
                 if n in nd.node_b.__props__]
        body.extend(_prop_lines(props, "\t"))
        for prop in nd.removed_props:
            body.append(f"\t/delete-property/ {prop.name};")
        if body:
            blocks.append(_node_ref(nd.node_b) + " {\n"
                          + "\n".join(body) + "\n};")

    # added nodes: emit the top of each added subtree, targeting its parent.
    # descendants that are also "added" come via the subtree recursion, so
    # skip any added node whose parent is itself added.
    added_paths = {n.abs_path for n in delta.added_nodes}
    for node in delta.added_nodes:
        parent = node.parent
        if parent is not None and parent.abs_path in added_paths:
            continue
        label = (node.label + ": ") if node.label else ""
        inner = _serialize_subtree(node, "\t\t")
        child_block = (f"\t{label}{node.name} {{\n"
                       + "\n".join(inner) + "\n\t};")
        if parent is None or parent.abs_path == "/":
            blocks.append("/ {\n" + child_block + "\n};")
        else:
            blocks.append(_node_ref(parent) + " {\n" + child_block + "\n};")

    # removed nodes: delete by reference (top of each removed subtree only)
    removed_paths = {n.abs_path for n in delta.removed_nodes}
    for node in delta.removed_nodes:
        parent = node.parent
        if parent is not None and parent.abs_path in removed_paths:
            continue
        blocks.append(f"/delete-node/ {_node_ref(node)};")

    return "\n\n".join(blocks) + "\n"
