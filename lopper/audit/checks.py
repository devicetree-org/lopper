#/*
# * Copyright (c) 2026 AMD Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Lopper audit check handlers (data-driven DRCs).

A "check" is the executable predicate behind a Design Rule Check (DRC). Rules
(see ``assertions.py``) are *data* -- they name a check type and pass typed
parameters. Handlers are a small, fixed library of *reusable, parameterized*
predicates. A new DRC that fits an existing handler needs no Python; only a
genuinely new predicate kind warrants a new handler.

Two kinds of handler:

- Single-node  (``RELATIONAL = False``): receives the list of selected nodes
  and checks each one independently (presence, value, reference validity).
- Relational   (``RELATIONAL = True``): receives a *group map*
  ``{context_path: set(elements)}`` built from a rule's ``group-by`` selector
  and ``collect`` collector, and reasons over the set-of-sets (cross-domain
  exclusivity, consistent view, ...).

Collectors turn a node into a set of hashable elements (the things compared
across groups). Keeping ``collect`` a named, registered function is what lets a
single relational handler (``exclusive-across``) cover both "device in two
domains" (collect = a property's refs) and "CPU core in two domains" (collect =
decoded cpu triplet) with zero per-DRC code.
"""

import re
from typing import List, Dict, Set, Callable

import lopper.log

from .base import ValidationResult, ValidationPhase


# --------------------------------------------------------------------------
# Collectors: node -> set of hashable elements
# --------------------------------------------------------------------------

_COLLECTORS: Dict[str, Callable] = {}


def register_collector(name: str):
    """Decorator: register a collector under ``name`` (usable as ``collect:``)."""
    def _wrap(fn):
        _COLLECTORS[name] = fn
        return fn
    return _wrap


def get_collector(spec) -> Callable:
    """Resolve a ``collect:`` spec to a collector callable.

    Forms:
      - ``property:<name>``  -> the values of property <name>, as identities
      - ``<registered>``     -> a named collector (e.g. ``cpu-cores``)
      - ``{property: <name>, kind: range, ...}`` -> a *typed* collector

    The ``kind`` is what tells a relational handler how to compare elements.
    ``identity`` (the default) compares by equality, which is right for device
    phandles or ids; ``range`` yields ``(start, size)`` intervals, which must be
    compared by overlap or containment instead. Getting that wrong is the
    difference between "these two domains name the same region" and "these two
    regions intersect".

    Returns a callable ``fn(node) -> set | {element: flags}``.
    """
    if isinstance(spec, dict):
        kind = spec.get("kind", "identity")
        prop = spec.get("property")
        if kind == "range":
            ac = spec.get("address-cells")
            sc = spec.get("size-cells")
            return lambda node, tree=None: _collect_ranges(node, prop, tree, ac, sc)
        if kind == "identity":
            return lambda node, tree=None: _collect_property_values(node, prop)
        raise KeyError(f"unknown collector kind '{kind}'")

    if spec.startswith("property:"):
        prop = spec.split(":", 1)[1]
        return lambda node, tree=None: _collect_property_values(node, prop)
    return _COLLECTORS[spec]


def _collect_ranges(node, prop: str, tree=None,
                    address_cells=None, size_cells=None) -> Set:
    """Decode a ``<addr size addr size ...>`` property into (start, size) pairs.

    Cell widths and multi-cell assembly come from the shared memory helpers
    (`_get_cell_sizes`, `_cell_value_get`) rather than being decoded here, so
    range handling stays consistent with the rest of the audit code. Widths can
    still be pinned in the collector spec when a property does not follow its
    parent's cells.
    """
    from .memory import _get_cell_sizes, _cell_value_get

    val = node.propval(prop)
    if val == [""] or not isinstance(val, list):
        return set()

    ac, sc = _get_cell_sizes(tree, node) if tree is not None else (2, 2)
    if address_cells is not None:
        ac = int(address_cells)
    if size_cells is not None:
        sc = int(size_cells)

    stride = ac + sc
    if stride <= 0:
        return set()

    out = set()
    for i in range(0, len(val) - stride + 1, stride):
        try:
            start, _ = _cell_value_get(val, ac, i)
            size, _ = _cell_value_get(val, sc, i + ac)
        except (TypeError, ValueError, IndexError):
            continue
        out.add((int(start), int(size)))
    return out


def _collect_property_values(node, prop: str) -> Set:
    """Elements = the individual values of ``prop`` on the node ([] if absent)."""
    val = node.propval(prop)
    if val == [""]:
        return set()
    if not isinstance(val, list):
        val = [val]
    return {v for v in val if v != ""}


# Access flags-cell bit map, mirroring the set_bit() encoding in
# lopper/assists/yaml_to_dts_expansion.py. Bit 0 ("timeshare") is the
# time-shared / shared-access flag; the domains-spec `shared` flag is carried as
# this bit in the expanded `access` cell, so we surface it under both names.
# NOTE: the precise `shared` bit is pending confirmation in the language review
# (refinement R1). Keep this map as the single source of truth.
_ACCESS_FLAG_BITS = {
    0: "timeshare",
    2: "allow-secure",
    4: "read-only",
    6: "requested",
}


@register_collector("access")
def _collect_access(node, tree=None) -> Dict:
    """Decode a domain ``access`` property into ``{device: frozenset(flags)}``.

    The expanded form is ``access = <&dev flags>, ...`` -- (phandle, flags-cell)
    pairs, the same stride ``lopper_lib.node_accesses()`` relies on. The device
    is the element identity, so the same device claimed by two domains
    collides; the phandle is resolved through ``tree.pnode()`` so the identity
    is the node path, which is both stable and readable in a report, falling
    back to the raw phandle when it cannot be resolved. The flags cell is
    decoded to named flags so a relational handler can honor an exemption such
    as DOM-034's ``shared``.

    Only the low 32 bits of the flags cell are decoded; multi-cell (>32-bit)
    flag encodings are a documented follow-up.
    """
    val = node.propval("access")
    if val == [""] or not isinstance(val, list):
        return {}

    out: Dict[int, set] = {}
    for i in range(0, len(val) - 1, 2):
        try:
            dev = int(val[i])
            flags_cell = int(val[i + 1])
        except (TypeError, ValueError):
            continue
        names = {name for bit, name in _ACCESS_FLAG_BITS.items()
                 if flags_cell & (1 << bit)}
        if flags_cell & (1 << 0):   # timeshare == shared (see note above)
            names.add("shared")
        key = dev
        if tree is not None:
            target = tree.pnode(dev)
            if target is not None:
                key = target.abs_path
        out[key] = out.get(key, set()) | names
    return out


@register_collector("cpu-cores")
def _collect_cpu_cores(node, tree=None) -> Set:
    """Decode a domain ``cpus`` triplet into per-core identity elements.

    Per the SDT spec a ``cpus`` entry is (cluster-phandle, cpumask, exec-level).
    A core's identity is (cluster, bit-position) for each set bit in cpumask.
    Returns a set of ``(cluster, bit)`` tuples so two domains claiming the same
    physical core collide (cpu-core-exclusive).
    """
    val = node.propval("cpus")
    if val == [""] or not isinstance(val, list):
        return set()

    cores = set()
    # walk in triplets; tolerate trailing/short data without raising
    for i in range(0, len(val) - 2, 3):
        try:
            cluster = int(val[i])
            cpumask = int(val[i + 1])
        except (TypeError, ValueError):
            continue
        # resolve the cluster phandle through the core lookup so the identity
        # (and any report) names the cluster rather than a bare number
        if tree is not None:
            cnode = tree.pnode(cluster)
            if cnode is not None:
                cluster = cnode.abs_path
        bit = 0
        while cpumask:
            if cpumask & 1:
                cores.add((cluster, bit))
            cpumask >>= 1
            bit += 1
    return cores


# --------------------------------------------------------------------------
# Check handler base + registry
# --------------------------------------------------------------------------

class CheckHandler:
    """Base class for a DRC check handler.

    Subclasses set ``CHECK_TYPE`` (the string used in ``check:``) and
    ``RELATIONAL``, then implement ``execute``.
    """

    CHECK_TYPE: str = "base"
    RELATIONAL: bool = False

    def execute(self, tree, rule, selection, context=None) -> List[ValidationResult]:
        """Run the check.

        Args:
            tree: the LopperTree under audit.
            rule: the Rule being evaluated (id, params, message, ...).
            selection: for single-node handlers, the list of selected nodes;
                       for relational handlers, the group map
                       ``{context_path: {element: flags}}``.
            context: evaluation context. ``context["this"]`` is the node bound
                     by the enclosing context, if any (see assertions.py).

        Returns:
            A list of ValidationResult; failing results have ``passed=False``.
        """
        raise NotImplementedError

    # helper for subclasses -- builds a uniformly-tagged result
    def _fail(self, rule, message, source_path=None) -> ValidationResult:
        return ValidationResult(
            check_name=rule.id,
            phase=ValidationPhase[rule.phase_enum_name],
            passed=False,
            message=message or rule.message,
            source_path=source_path,
            details={"drc_id": rule.id, "severity": rule.severity},
        )


class CheckHandlerRegistry:
    """Maps a ``check:`` type string to a CheckHandler instance."""

    _handlers: Dict[str, CheckHandler] = {}

    @classmethod
    def register(cls, handler_class):
        inst = handler_class()
        cls._handlers[handler_class.CHECK_TYPE] = inst
        return handler_class

    @classmethod
    def get(cls, check_type: str) -> CheckHandler:
        return cls._handlers.get(check_type)

    @classmethod
    def known_types(cls) -> List[str]:
        return sorted(cls._handlers.keys())


# --------------------------------------------------------------------------
# Single-node handlers
# --------------------------------------------------------------------------

@CheckHandlerRegistry.register
class RequiredCheck(CheckHandler):
    """Every node in the selection must have all ``properties``."""
    CHECK_TYPE = "required"

    def execute(self, tree, rule, selection, context=None):
        results = []
        props = rule.params.get("properties", [])
        for node in selection:
            for p in props:
                if node.propval(p) == [""]:
                    results.append(self._fail(
                        rule,
                        f"{node.abs_path}: missing required property '{p}'",
                        node.abs_path))
        return results


@CheckHandlerRegistry.register
class EnumCheck(CheckHandler):
    """``property`` value must be one of ``values``."""
    CHECK_TYPE = "enum"

    def execute(self, tree, rule, selection, context=None):
        results = []
        prop = rule.params.get("property")
        allowed = rule.params.get("values", [])
        allowed_norm = {str(v) for v in allowed}
        for node in selection:
            val = node.propval(prop)
            if val == [""]:
                continue  # presence is the 'required' check's job
            actual = val[0] if isinstance(val, list) else val
            if str(actual) not in allowed_norm:
                results.append(self._fail(
                    rule,
                    f"{node.abs_path}: '{prop}' = {actual!r} not in {sorted(allowed_norm)}",
                    node.abs_path))
        return results


@CheckHandlerRegistry.register
class CompatibleContainsCheck(CheckHandler):
    """Node ``compatible`` must contain ``token``."""
    CHECK_TYPE = "compatible-contains"

    def execute(self, tree, rule, selection, context=None):
        results = []
        token = rule.params.get("token")
        for node in selection:
            compat = node.propval("compatible")
            compat = compat if isinstance(compat, list) else [compat]
            if token not in compat:
                results.append(self._fail(
                    rule,
                    f"{node.abs_path}: compatible {compat} must include '{token}'",
                    node.abs_path))
        return results


# Named fields of the `cpus` triplet: (cluster phandle, cpumask, exec level).
# Per the SDT spec the execution-level word carries bit 30 = lockstep and
# bit 31 = secure, with the low bits holding the exception level on A-profile.
_CPUS_FIELDS = {"cluster": 0, "cpumask": 1, "exec_level": 2}


@CheckHandlerRegistry.register
class BitmaskCheck(CheckHandler):
    """Numeric / bit-level constraints on a cell of a property.

    A string-matching selector cannot ask whether a bit is set, so this is
    both a check and -- used as a predicate selector term -- the way a rule
    conditions on something computed, such as a cluster being in lockstep.

    Parameters:
      ``property``  the property to read (default ``cpus``)
      ``field``     a named field of that property, or ``index`` for a raw
                    cell index. For ``cpus``: cluster, cpumask, exec_level.
      ``bit``/``set``   that bit must be set (or clear, with ``set: false``)
      ``mask``      value must have no bits outside this mask
      ``equals``    value must equal this exactly
      ``values``    value must be one of these
    """
    CHECK_TYPE = "bitmask"

    def _value(self, node, rule):
        prop = rule.params.get("property", "cpus")
        val = node.propval(prop)
        if val == [""] or not isinstance(val, list) or not val:
            return None
        field = rule.params.get("field")
        idx = rule.params.get("index")
        if idx is None:
            idx = _CPUS_FIELDS.get(field) if field else 0
        if idx is None or idx >= len(val):
            return None
        try:
            return int(val[idx])
        except (TypeError, ValueError):
            return None

    def execute(self, tree, rule, selection, context=None):
        p = rule.params
        results = []
        for node in selection:
            v = self._value(node, rule)
            if v is None:
                continue          # absence is 'required's concern
            what = p.get("field") or f"index {p.get('index', 0)}"
            bad = None

            if "bit" in p:
                want = p.get("set", True)
                if bool(v & (1 << int(p["bit"]))) != bool(want):
                    bad = (f"bit {p['bit']} of {what} is "
                           f"{'clear' if want else 'set'} ({hex(v)})")
            if bad is None and "mask" in p:
                extra = v & ~int(p["mask"])
                if extra:
                    bad = (f"{what} {hex(v)} sets bits outside "
                           f"{hex(int(p['mask']))} ({hex(extra)})")
            if bad is None and "equals" in p and v != int(p["equals"]):
                bad = f"{what} is {hex(v)}, expected {hex(int(p['equals']))}"
            if bad is None and "values" in p:
                allowed = [int(x) for x in p["values"]]
                if v not in allowed:
                    bad = (f"{what} is {hex(v)}, expected one of "
                           f"{[hex(x) for x in allowed]}")

            if bad:
                results.append(self._fail(
                    rule, f"{node.abs_path}: {bad}", node.abs_path))
        return results


@CheckHandlerRegistry.register
class CountCheck(CheckHandler):
    """Cardinality of the selection: ``min`` / ``max`` / ``exact``.

    A verdict about the selection as a whole rather than about any one node,
    so it reports once. Also the mechanism behind a context ``guard``, which is
    how a rule conditions on a *global* fact -- "...without a hypervisor
    domain" is `count: {max: 0}` over the hypervisor selection.
    """
    CHECK_TYPE = "count"

    def execute(self, tree, rule, selection, context=None):
        n = len(selection)
        lo = rule.params.get("min")
        hi = rule.params.get("max")
        exact = rule.params.get("exact")

        bad = []
        if exact is not None and n != int(exact):
            bad.append(f"expected exactly {exact}")
        if lo is not None and n < int(lo):
            bad.append(f"expected at least {lo}")
        if hi is not None and n > int(hi):
            bad.append(f"expected at most {hi}")
        if not bad:
            return []
        return [self._fail(rule, f"{'; '.join(bad)}, found {n}", None)]


@CheckHandlerRegistry.register
class IsNodeCheck(CheckHandler):
    """Is this node the one the enclosing context bound?

    Exists to be used as a *predicate* selector term, where negating it gives
    self-exclusion -- "every domain except this one" -- which is what turns a
    two-operand disjointness check into an ordinary two-group exclusivity
    check. ``params.equals: this`` compares against the bound node; a path
    string compares against that path.
    """
    CHECK_TYPE = "is-node"

    def execute(self, tree, rule, selection, context=None):
        target = rule.params.get("equals", "this")
        if target == "this":
            this = (context or {}).get("this")
            want = this.abs_path if this is not None else None
        else:
            want = str(target)
        results = []
        for node in selection:
            if want is None or node.abs_path != want:
                results.append(self._fail(
                    rule, f"{node.abs_path} is not {want!r}", node.abs_path))
        return results


@CheckHandlerRegistry.register
class SubsetOfCheck(CheckHandler):
    """Each selected node's collected elements must be a subset of another's.

    The other operand is reached with a relative selector, so the comparison
    is always "against *my* parent" rather than against some globally chosen
    node -- which is what makes it usable inside an iterating context.

    Parameters:
      ``of``        the property to compare (``access``, ``cpus``, ``memory``)
      ``kind``      how to collect it: ``identity`` (default), ``cpu-cores``
      ``relative``  how to reach the containing node (default ``parent-domain``)

    A node with no such relation is skipped: having no parent is not a subset
    violation.
    """
    CHECK_TYPE = "subset-of"

    def execute(self, tree, rule, selection, context=None):
        from .assertions import _eval_relative

        prop = rule.params.get("of", "access")
        kind = rule.params.get("kind", "identity")
        rel = rule.params.get("relative", "parent-domain")

        if kind == "cpu-cores":
            collect = lambda n: set(_collect_cpu_cores(n, tree))
        elif prop == "access":
            collect = lambda n: set(_collect_access(n, tree))
        else:
            collect = lambda n: set(_collect_property_values(n, prop))

        results = []
        for node in selection:
            targets = _eval_relative(tree, {"relative": rel}, node)
            if not targets:
                continue          # no containing node: nothing to be inside of
            mine = collect(node)
            if not mine:
                continue
            theirs = set()
            for t in targets:
                theirs |= collect(t)
            extra = mine - theirs
            if extra:
                where = ", ".join(sorted(t.abs_path for t in targets))
                results.append(self._fail(
                    rule,
                    f"{node.abs_path}: '{prop}' has "
                    f"{sorted(map(str, extra))} not present in {where}",
                    node.abs_path))
        return results


@CheckHandlerRegistry.register
class ConstCheck(CheckHandler):
    """``property`` must equal ``value`` exactly."""
    CHECK_TYPE = "const"

    def execute(self, tree, rule, selection, context=None):
        prop = rule.params.get("property")
        want = rule.params.get("value")
        results = []
        for node in selection:
            val = node.propval(prop)
            if val == [""]:
                continue          # absence is 'required's concern
            actual = val[0] if isinstance(val, list) and len(val) == 1 else val
            if str(actual) != str(want):
                results.append(self._fail(
                    rule,
                    f"{node.abs_path}: '{prop}' is {actual!r}, expected {want!r}",
                    node.abs_path))
        return results


@CheckHandlerRegistry.register
class PhandleTypeCheck(CheckHandler):
    """What a reference points *at* must be of the expected kind.

    ``ref-valid`` only asks whether a phandle resolves; this asks whether the
    node it resolves to is the right sort of thing -- that a domain's ``cpus``
    names an actual CPU cluster, say, rather than any node that happens to have
    a phandle.

    Parameters: ``property`` to follow, ``compatible`` the target must carry,
    and optionally ``index`` / ``stride`` for a property whose entries are
    tuples (``cpus`` is (cluster, cpumask, exec-level), so index 0, stride 3).
    """
    CHECK_TYPE = "phandle-type"

    def execute(self, tree, rule, selection, context=None):
        prop = rule.params.get("property")
        want = rule.params.get("compatible")
        idx = int(rule.params.get("index", 0))
        stride = int(rule.params.get("stride", 1)) or 1

        results = []
        for node in selection:
            val = node.propval(prop)
            if val == [""] or not isinstance(val, list):
                continue
            for i in range(idx, len(val), stride):
                try:
                    ph = int(val[i])
                except (TypeError, ValueError):
                    continue
                target = tree.pnode(ph)
                if target is None:
                    results.append(self._fail(
                        rule,
                        f"{node.abs_path}: '{prop}' references phandle "
                        f"{hex(ph)}, which resolves to no node",
                        node.abs_path))
                    continue
                compat = target.propval("compatible")
                compat = compat if isinstance(compat, list) else [compat]
                if want not in compat:
                    results.append(self._fail(
                        rule,
                        f"{node.abs_path}: '{prop}' references "
                        f"{target.abs_path}, which is not '{want}' "
                        f"(compatible {compat})",
                        node.abs_path))
        return results


@CheckHandlerRegistry.register
class AcyclicCheck(CheckHandler):
    """Following ``edge`` from each node must never return to it.

    ``edge`` names a property holding a path or label pointing at another node
    in the selection -- a domain's ``parent``, for instance, which must form a
    DAG rather than a loop.
    """
    CHECK_TYPE = "acyclic"

    def execute(self, tree, rule, selection, context=None):
        edge = rule.params.get("edge", "parent")

        def _target(node):
            val = node.propval(edge)
            if val == [""]:
                return None
            t = val[0] if isinstance(val, list) else val
            found = tree.deref(str(t))
            if isinstance(found, list):
                found = found[0] if found else None
            return found

        results = []
        reported = set()
        for node in selection:
            seen = [node.abs_path]
            cur = node
            while True:
                cur = _target(cur)
                if cur is None:
                    break
                if cur.abs_path in seen:
                    cycle = seen[seen.index(cur.abs_path):] + [cur.abs_path]
                    key = frozenset(cycle)
                    if key not in reported:
                        reported.add(key)
                        results.append(self._fail(
                            rule,
                            f"'{edge}' forms a cycle: {' -> '.join(cycle)}",
                            node.abs_path))
                    break
                seen.append(cur.abs_path)
        return results


@CheckHandlerRegistry.register
class RefExistsCheck(CheckHandler):
    """The path/label named by ``property`` must resolve to an existing node.

    Unlike ``ref-valid`` (which catches dangling *phandle* cells), this checks a
    string-valued reference -- e.g. a domain ``parent`` naming another domain's
    path (domain-parent-exists).
    """
    CHECK_TYPE = "ref-exists"

    def execute(self, tree, rule, selection, context=None):
        results = []
        prop = rule.params.get("property")
        for node in selection:
            val = node.propval(prop)
            if val == [""]:
                continue  # absence is 'required's concern
            target = val[0] if isinstance(val, list) else val
            target = str(target)
            # tree.deref() is the core resolver: it handles a phandle, a label
            # or an alias, so this rule does not have to know which it was given
            found = tree.deref(target)
            if found is None and target.startswith("/"):
                found = tree.nodes("^" + re.escape(target) + "$")
            if not found:
                results.append(self._fail(
                    rule,
                    f"{node.abs_path}: '{prop}' references '{target}' which does not exist",
                    node.abs_path))
        return results


@CheckHandlerRegistry.register
class RefValidCheck(CheckHandler):
    """Phandle/reference ``properties`` must resolve to a real node.

    Wraps the existing whole-tree ``check_invalid_phandles`` and filters its
    findings down to the selected nodes + named properties.
    """
    CHECK_TYPE = "ref-valid"

    def execute(self, tree, rule, selection, context=None):
        from .core import check_invalid_phandles
        props = set(rule.params.get("properties", []))
        sel_paths = {n.abs_path for n in selection}
        results = []
        for node_path, prop_name in check_invalid_phandles(tree, warn_only_modified=False):
            if node_path in sel_paths and (not props or prop_name in props):
                results.append(self._fail(
                    rule,
                    f"{node_path}: property '{prop_name}' has an unresolved reference",
                    node_path))
        return results


# --------------------------------------------------------------------------
# Relational handlers
# --------------------------------------------------------------------------

def _origin(context, group, element, fallback):
    """Which node contributed an element to a group (for diagnostics).

    With named groups the group key is a label rather than a node path, so
    without this a failure would say "claimed: [0x...]" and leave the reader to
    work out which domain is at fault.
    """
    o = (context or {}).get("origins") or {}
    paths = o.get((group, element))
    return ", ".join(sorted(paths)) if paths else fallback


def _nested(a: str, b: str) -> bool:
    """Is one node path an ancestor of the other (or the same node)?"""
    if not a or not b:
        return False
    for x, y in ((a, b), (b, a)):
        if x == y or y.startswith(x.rstrip("/") + "/"):
            return True
    return False


def _as_region(element, path):
    """A collected (start, size) element as a MemoryRegion, for interval math."""
    from .memory import MemoryRegion, MemoryRegionType
    start, size = element
    return MemoryRegion(start=start, size=size,
                        region_type=MemoryRegionType.DOMAIN_MEMORY,
                        source_path=path)


def _is_range(element) -> bool:
    return (isinstance(element, tuple) and len(element) == 2
            and all(isinstance(v, int) for v in element))


@CheckHandlerRegistry.register
class NoOverlapCheck(CheckHandler):
    """Ranges collected from different groups must not intersect.

    Like ``exclusive-across``, an element must not be claimed twice -- but
    elements are intervals here, so "claimed twice" means *overlap* rather than
    equality. Requires a ``range`` collector.

    ``params.unless-flag`` exempts an overlap when every group involved marks
    its range with that flag, which is how deliberately shared memory is
    allowed.

    ``params.ignore-nested`` (default true) skips pairs where one node is an
    ancestor of the other. Domains nest, and a child's memory is expected to
    lie inside its parent's -- that is a subset relation to be checked as such,
    not two independent claimants colliding. Without this, every hypervisor
    would be reported as overlapping each of its guests.
    """
    CHECK_TYPE = "no-overlap"
    RELATIONAL = True

    def execute(self, tree, rule, selection, context=None):
        unless = rule.params.get("unless-flag")
        ignore_nested = rule.params.get("ignore-nested", True)
        # flatten to (context, element, flags), skipping non-range elements
        items = []
        for ctx, elem_map in selection.items():
            for el, flags in elem_map.items():
                if _is_range(el):
                    items.append((ctx, el, flags))

        results = []
        seen = set()
        for i in range(len(items)):
            ctx_a, a, fa = items[i]
            for j in range(i + 1, len(items)):
                ctx_b, b, fb = items[j]
                if ctx_a == ctx_b:
                    continue          # within one group is a different rule
                if not _as_region(a, ctx_a).overlaps(_as_region(b, ctx_b)):
                    continue
                if unless and unless in fa and unless in fb:
                    continue
                oa = _origin(context, ctx_a, a, ctx_a)
                ob = _origin(context, ctx_b, b, ctx_b)
                if ignore_nested and _nested(oa, ob):
                    continue
                key = tuple(sorted([(ctx_a, a), (ctx_b, b)]))
                if key in seen:
                    continue
                seen.add(key)
                results.append(self._fail(
                    rule,
                    f"{oa} [{hex(a[0])}+{hex(a[1])}] overlaps "
                    f"{ob} [{hex(b[0])}+{hex(b[1])}]",
                    oa))
        return results


@CheckHandlerRegistry.register
class ContainedInCheck(CheckHandler):
    """Every range must fall inside one of the ranges of a container group.

    Two named groups: ``params.container`` names the one that bounds the
    others -- e.g. the physical memory described by the SDT, with the domains'
    claims checked against it.
    """
    CHECK_TYPE = "contained-in"
    RELATIONAL = True

    def execute(self, tree, rule, selection, context=None):
        cname = rule.params.get("container")
        if cname not in selection:
            lopper.log._warning(
                f"DRC {rule.id}: container group '{cname}' not in group-by")
            return []

        bounds = [_as_region(el, cname) for el in selection[cname]
                  if _is_range(el)]
        results = []
        for ctx, elem_map in selection.items():
            if ctx == cname:
                continue
            for el in elem_map:
                if not _is_range(el):
                    continue
                region = _as_region(el, ctx)
                if not any(b.contains(region) for b in bounds):
                    who = _origin(context, ctx, el, ctx)
                    results.append(self._fail(
                        rule,
                        f"{who}: [{hex(el[0])}+{hex(el[1])}] is not within "
                        f"any {cname} range",
                        who))
        return results


@CheckHandlerRegistry.register
class ExclusiveAcrossCheck(CheckHandler):
    """No collected element may appear in more than one group.

    Covers device-exclusive (device in >1 domain's access list) when
    ``collect: property:access``, and cpu-core-exclusive (CPU core in >1 domain) when
    ``collect: cpu-cores``. ``params.unless-flag`` (optional) names a per-group
    flag that, when present, exempts an element from the conflict -- the
    ``shared`` escape in DOM-034.
    """
    CHECK_TYPE = "exclusive-across"
    RELATIONAL = True

    def execute(self, tree, rule, selection, context=None):
        # selection: {group: {element: frozenset(flags)}}
        unless = rule.params.get("unless-flag")
        ignore_nested = rule.params.get("ignore-nested", True)

        # element -> {group: flags}   (note: `context` is the execution context
        # argument, so the loop variable must not shadow it)
        owners: Dict[object, Dict[str, frozenset]] = {}
        for group, elem_map in selection.items():
            for el, flags in elem_map.items():
                owners.setdefault(el, {})[group] = flags

        results = []
        for el, ctx_flags in owners.items():
            if len(ctx_flags) <= 1:
                continue
            # Exempt only when EVERY claiming context marks the element with the
            # exemption flag (e.g. a device shared by all its domains).
            if unless and all(unless in flags for flags in ctx_flags.values()):
                continue
            if ignore_nested:
                # A domain and one of its own descendants are not independent
                # claimants: a hypervisor covering its guests' CPUs is
                # containment, not a conflict. Report only when at least one
                # pair of claimants is genuinely unrelated.
                paths = [_origin(context, c, el, c) for c in ctx_flags]
                if all(_nested(paths[i], paths[j])
                       for i in range(len(paths))
                       for j in range(i + 1, len(paths))):
                    continue
            contexts = sorted(ctx_flags.keys())
            detail = ""
            if unless:
                missing = sorted(c for c, f in ctx_flags.items() if unless not in f)
                detail = f" (missing '{unless}' flag in: {missing})"
            results.append(self._fail(
                rule,
                f"{el!r} is claimed by multiple groups: {contexts}{detail}",
                None))
        return results
