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

Two handler shapes:

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


def get_collector(spec: str) -> Callable:
    """Resolve a ``collect:`` spec to a collector callable.

    Two forms:
      - ``property:<name>``  -> collect the (string) values of property <name>
      - ``<registered>``     -> a named collector (e.g. ``cpu-cores``)

    Returns a callable ``fn(node) -> set`` or raises KeyError.
    """
    if spec.startswith("property:"):
        prop = spec.split(":", 1)[1]
        return lambda node: _collect_property_values(node, prop)
    return _COLLECTORS[spec]


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
def _collect_access(node) -> Dict:
    """Decode a domain ``access`` property into ``{device: frozenset(flags)}``.

    The expanded form is ``access = <&dev flags>, ...`` -- (phandle, flags-cell)
    pairs. The device phandle is the element identity (so the same device across
    two domains collides); the flags cell is decoded to named flags so a
    relational handler can honor an exemption such as DOM-034's ``shared``.

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
        out[dev] = out.get(dev, set()) | names
    return out


@register_collector("cpu-cores")
def _collect_cpu_cores(node) -> Set:
    """Decode a domain ``cpus`` triplet into per-core identity elements.

    Per the SDT spec a ``cpus`` entry is (cluster-phandle, cpumask, exec-level).
    A core's identity is (cluster, bit-position) for each set bit in cpumask.
    Returns a set of ``(cluster, bit)`` tuples so two domains claiming the same
    physical core collide (DRC-DOM-012).
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

    def execute(self, tree, rule, selection) -> List[ValidationResult]:
        """Run the check.

        Args:
            tree: the LopperTree under audit.
            rule: the Rule being evaluated (id, params, message, ...).
            selection: for single-node handlers, the list of selected nodes;
                       for relational handlers, the group map
                       ``{context_path: set(elements)}``.

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

    def execute(self, tree, rule, selection):
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

    def execute(self, tree, rule, selection):
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

    def execute(self, tree, rule, selection):
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


@CheckHandlerRegistry.register
class RefExistsCheck(CheckHandler):
    """The path/label named by ``property`` must resolve to an existing node.

    Unlike ``ref-valid`` (which catches dangling *phandle* cells), this checks a
    string-valued reference -- e.g. a domain ``parent`` naming another domain's
    path (DRC-DOM-007).
    """
    CHECK_TYPE = "ref-exists"

    def execute(self, tree, rule, selection):
        results = []
        prop = rule.params.get("property")
        for node in selection:
            val = node.propval(prop)
            if val == [""]:
                continue  # absence is 'required's concern
            target = val[0] if isinstance(val, list) else val
            target = str(target)
            found = (tree.nodes("^" + re.escape(target) + "$")
                     if target.startswith("/") else tree.lnodes(target))
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

    def execute(self, tree, rule, selection):
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

@CheckHandlerRegistry.register
class ExclusiveAcrossCheck(CheckHandler):
    """No collected element may appear in more than one group.

    Covers DRC-DOM-034 (device in >1 domain's access list) when
    ``collect: property:access``, and DRC-DOM-012 (CPU core in >1 domain) when
    ``collect: cpu-cores``. ``params.unless-flag`` (optional) names a per-group
    flag that, when present, exempts an element from the conflict -- the
    ``shared`` escape in DOM-034.
    """
    CHECK_TYPE = "exclusive-across"
    RELATIONAL = True

    def execute(self, tree, rule, selection):
        # selection: {context_path: {element: frozenset(flags)}}
        unless = rule.params.get("unless-flag")

        # element -> {context: flags}
        owners: Dict[object, Dict[str, frozenset]] = {}
        for context, elem_map in selection.items():
            for el, flags in elem_map.items():
                owners.setdefault(el, {})[context] = flags

        results = []
        for el, ctx_flags in owners.items():
            if len(ctx_flags) <= 1:
                continue
            # Exempt only when EVERY claiming context marks the element with the
            # exemption flag (e.g. a device shared by all its domains).
            if unless and all(unless in flags for flags in ctx_flags.values()):
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
