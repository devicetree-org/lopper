#/*
# * Copyright (c) 2026 AMD Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Data-driven Design Rule Checks (DRCs) for the lopper audit framework.

A DRC is expressed as *data* -- a Rule -- not Python. A Rule names a check type
(see ``checks.py``), a phase, a node selection, and typed parameters. The audit
framework loads rules from:

  - shipped catalog YAML under ``lopper/audit/drc/*.yaml`` (the open DRC set)
  - user-authored YAML (``drc:`` list), and
  - in-tree ``/__assertions__`` nodes (the YAML->DTS expanded form)

and runs the ones whose phase matches, through ``DRCValidator`` -- a normal
``BaseValidator`` so it plugs into the existing ``run_audit_phase`` plumbing and
``-W`` flag model.

A rule, in YAML::

    drc:
      - id: domain-compatible           # stable catalog id
        severity: error           # error | warning | info | block
        phase: post-yaml          # early | post-yaml | post-processing
        select: ["/domains/.*"]   # node selectors (path regex [:prop[:val]])
        check: compatible-contains
        params: { token: "openamp,domain-v1" }
        message: "domain compatible must include openamp,domain-v1"

      # relational form: group-by buckets nodes per context, collect turns each
      # bucket node into a set of comparable elements
      - id: device-exclusive
        severity: error
        phase: post-processing
        group-by: "/domains/.*"
        collect: "property:access"
        check: exclusive-across
        params: { unless-flag: shared }
        message: "device in >1 domain requires the shared flag"
"""

import os
import glob
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

import lopper.log
from .base import (
    ValidationPhase,
    ValidationResult,
    BaseValidator,
    ValidatorRegistry,
)
from .checks import CheckHandlerRegistry, get_collector

try:
    from ruamel.yaml import YAML
    _yaml_loader = YAML(typ="safe")
except ImportError:
    import yaml as _pyyaml
    _yaml_loader = None


# Map the language's phase strings to the framework's ValidationPhase enum.
# pre-assist / post-assist are reserved for the assist-hook work and fold onto
# POST_PROCESSING until those hooks land (see findings doc, out-of-scope).
_PHASE_MAP = {
    "early": "EARLY",
    "post-yaml": "POST_YAML",
    "post-processing": "POST_PROCESSING",
    "pre-assist": "POST_PROCESSING",
    "post-assist": "POST_PROCESSING",
}

_VALID_SEVERITIES = {"error", "warning", "info", "block"}


@dataclass
class Rule:
    """A single Design Rule Check, expressed as data.

    A rule is either a **context** or a **check**, never both:

    - a rule carrying ``rules`` is a context. Its selection is evaluated and
      *iterated*: for each matched node the children run with that node bound
      as ``this``. A context is a selection, not an assertion -- it has no truth
      value and is never reported. Nesting therefore expresses implication
      ("if these nodes exist, check the following about each") without an
      ``if``/``when`` keyword, and sibling children are a conjunction.
    - a rule carrying ``check`` is a check. It runs its handler and its
      failures are what get reported.

    ``severity`` on a context is an inherited default for its subtree, not a
    verdict level; any descendant may override it.
    """
    id: str
    check: Optional[str] = None
    phase: str = "post-processing"
    severity: str = "error"
    select: List[str] = field(default_factory=list)
    group_by: Optional[str] = None
    collect: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    message: str = ""
    enabled: bool = True
    platform: Optional[str] = None
    rules: List["Rule"] = field(default_factory=list)
    guard: Optional[Dict[str, Any]] = None
    tree: Optional[str] = None

    @property
    def is_context(self) -> bool:
        return bool(self.rules)

    @property
    def phase_enum_name(self) -> str:
        return _PHASE_MAP.get(self.phase, "POST_PROCESSING")

    @property
    def phase_enum(self) -> ValidationPhase:
        return ValidationPhase[self.phase_enum_name]

    @classmethod
    def from_dict(cls, d: Dict[str, Any], inherited: Optional[Dict] = None) -> "Rule":
        """Build a Rule (and, recursively, its children).

        ``inherited`` carries defaults handed down from an enclosing context
        (currently ``severity`` and ``phase``).
        """
        inherited = inherited or {}
        if "id" not in d:
            raise ValueError(f"DRC rule missing required 'id': {d!r}")

        children_raw = d.get("rules") or []
        has_check = "check" in d
        if children_raw and has_check:
            raise ValueError(
                f"DRC {d['id']}: a rule is either a context ('rules') or a check "
                f"('check'), not both")
        if not children_raw and not has_check:
            raise ValueError(
                f"DRC {d['id']}: a rule needs either 'check' or 'rules'")

        sev = str(d.get("severity", inherited.get("severity", "error"))).lower()
        if sev not in _VALID_SEVERITIES:
            raise ValueError(f"DRC {d['id']}: invalid severity {sev!r}")
        phase = str(d.get("phase", inherited.get("phase", "post-processing")))
        tree_name = d.get("tree", inherited.get("tree"))

        sel = d.get("select", [])
        if isinstance(sel, str):
            sel = [sel]

        kids = [cls.from_dict(c, {"severity": sev, "phase": phase,
                                  "tree": tree_name})
                for c in children_raw]

        return cls(
            id=str(d["id"]),
            check=str(d["check"]) if has_check else None,
            phase=phase,
            severity=sev,
            select=list(sel),
            group_by=d.get("group-by", d.get("group_by")),
            collect=d.get("collect"),
            params=dict(d.get("params", {})),
            message=str(d.get("message", "")),
            enabled=bool(d.get("enabled", True)),
            platform=d.get("platform"),
            rules=kids,
            guard=d.get("guard"),
            tree=tree_name,
        )


def _load_yaml(path: str):
    with open(path, "r") as f:
        if _yaml_loader:
            return _yaml_loader.load(f)
        return _pyyaml.safe_load(f)


class AssertionRegistry:
    """Holds the loaded DRC rules and resolves a selection for each."""

    def __init__(self):
        self._rules: List[Rule] = []
        self._shipped_loaded = False

    # -- loading -----------------------------------------------------------
    def clear(self):
        self._rules = []
        self._shipped_loaded = False

    def add_rule(self, rule: Rule):
        self._rules.append(rule)

    def load_dict(self, data: Dict[str, Any]) -> int:
        """Load rules from a parsed mapping containing a ``drc:`` list."""
        count = 0
        for entry in (data or {}).get("drc", []) or []:
            self._rules.append(Rule.from_dict(entry))
            count += 1
        return count

    def load_yaml_file(self, path: str) -> int:
        return self.load_dict(_load_yaml(path))

    def load_dir(self, dirpath: str) -> int:
        count = 0
        for path in sorted(glob.glob(os.path.join(dirpath, "*.yaml"))):
            count += self.load_yaml_file(path)
        return count

    def load_shipped(self) -> int:
        """Load the catalog shipped under ``lopper/audit/drc/``."""
        if self._shipped_loaded:
            return 0
        drc_dir = os.path.join(os.path.dirname(__file__), "drc")
        n = self.load_dir(drc_dir) if os.path.isdir(drc_dir) else 0
        self._shipped_loaded = True
        return n

    def collect_from_tree(self, tree) -> int:
        """Load rules expressed as ``/__assertions__/rule_N`` DTS nodes.

        This is the in-tree (YAML->DTS expanded) form. Each ``rule_N`` subnode
        carries the same fields as the YAML rule; a ``check_0`` subnode (or
        inline ``check``) names the check type.
        """
        count = 0
        anchors = tree.nodes("/__assertions__$") if tree else []
        for anchor in anchors:
            for child in anchor.children():
                d = self._rule_node_to_dict(child)
                if d:
                    self._rules.append(Rule.from_dict(d))
                    count += 1
        return count

    @staticmethod
    def _rule_node_to_dict(node) -> Optional[Dict[str, Any]]:
        def _val(name, default=None):
            v = node.propval(name)
            if v == [""]:
                return default
            return v[0] if isinstance(v, list) and len(v) == 1 else v

        rid = _val("id")
        if not rid:
            return None
        check = _val("check")
        # support a single check_0 subnode form, and nested rule_N subnodes
        params = {}
        kids = []
        for child in node.children():
            if child.name.startswith("check"):
                check = check or _val_node(child, "check")
                params = _params_from_node(child)
            elif child.name.startswith("rule"):
                kid = AssertionRegistry._rule_node_to_dict(child)
                if kid:
                    kids.append(kid)
        sel = _val("select")
        if isinstance(sel, str):
            sel = [sel]
        d = {
            "id": rid,
            "phase": _val("phase", "post-processing"),
            "severity": _val("severity", "error"),
            "select": sel or [],
            "group-by": _val("group-by"),
            "collect": _val("collect"),
            "message": _val("message", ""),
        }
        # context xor check -- mirror the YAML form
        if kids:
            d["rules"] = kids
        else:
            d["check"] = check
            d["params"] = params
        return d

    # -- querying ----------------------------------------------------------
    def rules_for_phase(self, phase: ValidationPhase) -> List[Rule]:
        """Top-level rules with anything to do in this phase.

        A context is included when any rule in its subtree matches, since a
        descendant may override the inherited phase.
        """
        def _matches(r: Rule) -> bool:
            if not r.enabled:
                return False
            if r.is_context:
                return any(_matches(c) for c in r.rules)
            return r.phase_enum == phase

        return [r for r in self._rules if _matches(r)]

    def all_rules(self) -> List[Rule]:
        return list(self._rules)

    # -- selection ---------------------------------------------------------
    def select_nodes(self, tree, rule: Rule, this=None) -> List:
        """Resolve a rule's ``select`` terms to a node list.

        A term is either

        - a **string** ``path_regex[:prop[:val]]`` (as in lop,select), or
        - a **predicate** mapping ``{check, params, negate}``, which is
          evaluated per candidate node by the named check handler: the
          predicate holds when the handler reports no failure for that node.
          This is how a computed condition -- a bit being set, a value being in
          range -- is expressed, since a string term can only match text.

        Composition follows lop,select: a term that carries a node regex
        **accumulates** (OR), a term that does not **refines** what has been
        selected so far (AND). Predicate terms never carry a node regex, so
        they always refine. ``negate: true`` inverts a predicate.
        """
        return self._eval_terms(tree, rule.select, rule, this)

    def _eval_terms(self, tree, terms, rule: Rule, this=None) -> List:
        """Evaluate a list of selector terms (see select_nodes for semantics)."""
        if isinstance(terms, (str, dict)):
            terms = [terms]
        selected: Dict[str, Any] = {}
        for term in terms or []:
            if isinstance(term, dict) and "relative" in term:
                # relative term -> accumulate, resolved from the bound node
                for node in _eval_relative(tree, term, this):
                    selected[node.abs_path] = node
                continue
            if isinstance(term, dict):
                # predicate term -> refine
                selected = {p: n for p, n in selected.items()
                            if _eval_predicate(tree, term, n, rule, this)}
                continue

            node_regex = str(term).split(":")[0]
            if node_regex:
                for node in _eval_selector(tree, term):
                    selected[node.abs_path] = node
            else:
                # ":prop[:val]" -> refine the current selection
                refined = _eval_selector(tree, term, nodes=list(selected.values()))
                selected = {n.abs_path: n for n in refined}
        return list(selected.values())

    def build_groups(self, tree, rule: Rule, this=None, origins=None) -> Dict[str, Dict[object, frozenset]]:
        """Build the relational ``{context_path: {element: frozenset(flags)}}`` map.

        ``group-by`` is either

        - a **selector** -- one group per matched node, keyed by its path; or
        - a **mapping of name -> selector** -- explicit named groups, each the
          union of everything its selector matches.

        Named groups are what let a two-operand question be asked with the
        one relational handler: disjointness of A and B is exactly exclusivity
        over the two-group map {A, B}, so no separate `disjoint-from` handler
        is needed. Combined with a negated ``is-node`` predicate term, that
        also expresses "every domain except this one".

        ``collect`` turns each node into a set of comparable elements.
        Collectors may return a bare set (no flags) or a ``{element: flags}``
        mapping; both are normalized to the flagged form so relational handlers
        can consult per-element flags uniformly.
        """
        def _elems(nodes, collector, gname=None):
            merged: Dict[object, frozenset] = {}
            for node in nodes:
                raw = collector(node, tree)
                if origins is not None:
                    for el in (raw or {}):
                        origins.setdefault((gname, el), set()).add(node.abs_path)
                items = (raw.items() if isinstance(raw, dict)
                         else ((el, ()) for el in raw))
                for el, flags in items:
                    merged[el] = merged.get(el, frozenset()) | frozenset(flags)
            return merged

        # `collect` may be a single spec, or -- parallel to named groups -- a
        # mapping of group name to spec, since the groups being compared do not
        # have to carry the same property (a domain's claimed `memory` versus
        # the physical `reg` it must fall inside, say).
        spec = rule.collect
        # a dict is a typed collector spec if it names one, otherwise it is a
        # per-group mapping
        per_group = (isinstance(spec, dict)
                     and not ({"property", "kind"} & set(spec)))

        def _collector_for(name):
            if not per_group:
                return get_collector(spec)
            if name in spec:
                return get_collector(spec[name])
            raise KeyError(
                f"DRC {rule.id}: no collector given for group '{name}'")

        if isinstance(rule.group_by, dict):
            return {name: _elems(self._eval_terms(tree, terms, rule, this),
                                 _collector_for(name), name)
                    for name, terms in rule.group_by.items()}

        collector = _collector_for(None)
        return {node.abs_path: _elems([node], collector, node.abs_path)
                for node in self._eval_terms(tree, rule.group_by, rule, this)}


# Relative selectors: named ways to reach a node *from* the one a context has
# bound. Deliberately a small, fixed vocabulary rather than a path language --
# each entry is a relationship the domain model actually has.
def _rel_parent_domain(tree, node):
    """The domain named by this node's `parent` property."""
    val = node.propval("parent")
    if val == [""]:
        return []
    found = tree.deref(str(val[0] if isinstance(val, list) else val))
    if found is None:
        return []
    return found if isinstance(found, list) else [found]


def _rel_ancestor_domain(tree, node):
    """The nearest enclosing domain in the tree, if any."""
    probe = getattr(node, "parent", None)
    while probe is not None:
        compat = probe.propval("compatible")
        compat = compat if isinstance(compat, list) else [compat]
        if "openamp,domain-v1" in compat:
            return [probe]
        probe = getattr(probe, "parent", None)
    return []


def _rel_children_domains(tree, node):
    """Domains nested directly under this one (a hypervisor's guests)."""
    out = []
    for child in node.children():
        compat = child.propval("compatible")
        compat = compat if isinstance(compat, list) else [compat]
        if "openamp,domain-v1" in compat:
            out.append(child)
    return out


def _rel_cluster(tree, node):
    """The CPU cluster(s) this domain's `cpus` triplets reference."""
    val = node.propval("cpus")
    if val == [""] or not isinstance(val, list):
        return []
    out = []
    for i in range(0, len(val), 3):
        try:
            target = tree.pnode(int(val[i]))
        except (TypeError, ValueError):
            continue
        if target is not None:
            out.append(target)
    return out


def _rel_named(tree, node, prop):
    """The node named by an arbitrary reference property (host, remote, ...)."""
    val = node.propval(prop)
    if val == [""]:
        return []
    out = []
    for v in (val if isinstance(val, list) else [val]):
        found = tree.deref(str(v))
        if found is None:
            continue
        out.extend(found if isinstance(found, list) else [found])
    return out


_RELATIONS = {
    "parent-domain": _rel_parent_domain,
    "ancestor-domain": _rel_ancestor_domain,
    "guests": _rel_children_domains,
    "children-domains": _rel_children_domains,
    "cluster": _rel_cluster,
}


def _eval_relative(tree, term: Dict[str, Any], this) -> List:
    """Resolve a ``{relative: <name>}`` selector term against the bound node."""
    if this is None:
        return []
    name = term.get("relative")
    fn = _RELATIONS.get(name)
    if fn is not None:
        return fn(tree, this)
    # not a fixed relation: treat it as a reference-valued property name,
    # which covers host/remote and anything else that names another node
    return _rel_named(tree, this, str(name))


def _guard_holds(tree, rule: Rule, registry, this=None) -> bool:
    """Evaluate a context's ``guard``: a selection plus a cardinality test.

    ``guard: {select: [...], count: {max: 0}}`` -- children run only when the
    count constraint is satisfied. Reuses the ``count`` handler rather than
    introducing an existence operator.
    """
    g = rule.guard or {}
    probe = Rule(id=f"{rule.id}[guard]", check="count",
                 phase=rule.phase, severity=rule.severity,
                 select=list(g.get("select", []) or []),
                 params=dict(g.get("count", {}) or {}), message="")
    handler = CheckHandlerRegistry.get("count")
    try:
        nodes = registry.select_nodes(tree, probe, this=this)
        return not handler.execute(tree, probe, nodes, context={"this": this})
    except Exception as e:
        lopper.log._warning(f"DRC {rule.id}: guard raised: {e}")
        return False


def _eval_predicate(tree, term: Dict[str, Any], node, rule: Rule, this=None) -> bool:
    """Is a predicate selector term satisfied for ``node``?

    The term names an ordinary check handler; the predicate holds when that
    handler reports no failure for the node. Reusing the handlers means there
    is no second, parallel library of predicates to maintain.
    """
    handler = CheckHandlerRegistry.get(term.get("check"))
    if handler is None:
        lopper.log._warning(
            f"DRC {rule.id}: unknown predicate check "
            f"'{term.get('check')}', term ignored")
        return True
    probe = Rule(id=f"{rule.id}[predicate]", check=term.get("check"),
                 phase=rule.phase, severity=rule.severity,
                 params=dict(term.get("params", {})), message="")
    try:
        holds = not handler.execute(tree, probe, [node], context={"this": this})
    except Exception as e:
        lopper.log._warning(f"DRC {rule.id}: predicate raised: {e}")
        return True
    return (not holds) if term.get("negate") else holds


def _eval_selector(tree, selector: str, nodes=None) -> List:
    """Evaluate one ``path_regex[:prop[:val]]`` selector.

    ``nodes`` restricts evaluation to an existing selection, which is how a
    term with an empty node regex refines rather than accumulates.
    """
    if not selector:
        return []
    parts = selector.split(":")
    node_regex = parts[0]
    prop = parts[1] if len(parts) > 1 else ""
    prop_val = parts[2] if len(parts) > 2 else ""

    if not node_regex:
        nodes = list(nodes or [])
    else:
        nodes = tree.nodes(node_regex) if node_regex.startswith("/") else tree.lnodes(node_regex)

    if not prop:
        return nodes
    filtered = []
    for n in nodes:
        val = n.propval(prop)
        if val == [""]:
            continue
        if not prop_val:
            # ":prop" with no value = "node has this property"
            filtered.append(n)
            continue
        # Match per list ELEMENT, and require the whole element to match.
        #
        # A device tree list property is a list of distinct strings, so joining
        # them and substring-searching conflates values that merely share a
        # prefix. `compatible = "openamp,domain-v1", "xen,domain-v2"` (two
        # values, both domains) must match "openamp,domain-v1", while
        # `compatible = "openamp,domain-v1,devices"` (one value: a device
        # inventory, not a domain) must not. fullmatch on each element draws
        # that line, and still allows regexes such as "serial.*".
        values = val if isinstance(val, list) else [val]
        if any(re.fullmatch(prop_val, str(v)) for v in values):
            filtered.append(n)
    return filtered


# Module-global registry: shipped catalog + anything callers add.
_drc_registry: Optional[AssertionRegistry] = None


def get_drc_registry() -> AssertionRegistry:
    global _drc_registry
    if _drc_registry is None:
        _drc_registry = AssertionRegistry()
        _drc_registry.load_shipped()
    return _drc_registry


def reset_drc_registry():
    """Reset the global DRC registry (primarily for tests)."""
    global _drc_registry
    _drc_registry = None


@ValidatorRegistry.register
class DRCValidator(BaseValidator):
    """Runs data-driven DRC rules at each phase.

    Enabled by ``-W drc`` / ``-W drc_all`` / ``-W all`` (run all enabled rules),
    or ``-W drc:<ID>`` (run only the named rules). Rule severity drives
    reporting: ``block`` is a hard stop regardless of ``--werror``.
    """

    CATEGORY = "drc"
    WARNING_FLAGS = ["drc"]
    META_FLAGS = {"drc_all": ["drc"]}

    def __init__(self, warnings=None, werror=False):
        super().__init__(warnings=warnings, werror=werror)
        # keep raw flags so 'drc:<id>' tokens survive BaseValidator filtering
        self._raw_warnings = list(warnings or [])
        self._id_filter = {w.split(":", 1)[1] for w in self._raw_warnings
                           if w.startswith("drc:")}

    def is_enabled(self) -> bool:
        # DRCs are intentionally NOT part of the '-W all' sweep while the rule
        # set is incomplete: a partial catalog firing on real SDTs would emit
        # spurious results and erode trust in '-W all'. They run only via the
        # explicit drc / drc_all / drc:<id> flags. Re-add 'all' here once the
        # catalog is complete and validated.
        if {"drc", "drc_all"}.intersection(self._raw_warnings):
            return True
        return bool(self._id_filter)

    def _rule_active(self, rule: Rule, chain=()) -> bool:
        """A leaf runs if it, or any context enclosing it, was named in -W."""
        if not self._id_filter:
            return True
        if rule.id in self._id_filter:
            return True
        return any(context_id in self._id_filter for context_id in chain)

    def run_phase(self, phase, tree, **kwargs):
        registry = get_drc_registry()
        # Named trees a rule may target with `tree:`. The caller supplies them
        # because only it knows what has been loaded; absent that, only the
        # main assembled tree is reachable.
        self._subtrees = kwargs.get("subtrees") or {}
        phase_results = []
        for rule in registry.rules_for_phase(phase):
            phase_results.extend(
                self._run_rule(tree, rule, phase, registry, this=None, chain=()))
        self.results.extend(phase_results)
        return phase_results

    def _run_rule(self, tree, rule, phase, registry, this=None, chain=()):
        """Evaluate one rule, recursing into contexts.

        A context iterates: for each node its selection matches, the children run
        with that node bound as ``this``. A context with no selection of its own
        is a pure grouping node and passes the current binding through. At the
        top level ``this`` is None, i.e. a single implicit iteration, which is
        exactly the pre-nesting behaviour.
        """
        if not rule.enabled:
            return []

        # `tree:` retargets a rule at a named tree; absent, it runs against the
        # main assembled tree, which is where all the inputs have converged.
        if rule.tree:
            named = getattr(self, "_subtrees", {}).get(rule.tree)
            if named is None:
                lopper.log._warning(
                    f"DRC {rule.id}: tree '{rule.tree}' is not loaded; "
                    f"rule skipped (it is not passing, it did not run)")
                return []
            tree = named

        if rule.is_context:
            # A guard conditions the context on a GLOBAL fact rather than on the
            # node under test -- the "...without a hypervisor domain" rules.
            # Ordinary context semantics are backwards for that: a context that
            # selects nothing runs nothing, whereas here the children should
            # run precisely because nothing was selected.
            if rule.guard and not _guard_holds(tree, rule, registry, this):
                return []
            if rule.select:
                bindings = registry.select_nodes(tree, rule, this=this)
            else:
                bindings = [this]
            results = []
            for node in bindings:
                for child in rule.rules:
                    results.extend(
                        self._run_rule(tree, child, phase, registry,
                                       this=node, chain=chain + (rule.id,)))
            return results

        # leaf check
        if rule.phase_enum != phase:
            return []
        if not self._rule_active(rule, chain):
            return []
        if rule.guard and not _guard_holds(tree, rule, registry, this):
            return []

        handler = CheckHandlerRegistry.get(rule.check)
        if handler is None:
            lopper.log._warning(
                f"DRC {rule.id}: unknown check type '{rule.check}', skipping")
            return []

        try:
            origins: Dict[Any, Any] = {}
            if handler.RELATIONAL:
                selection = registry.build_groups(tree, rule, this=this,
                                                  origins=origins)
            else:
                if rule.select:
                    selection = registry.select_nodes(tree, rule, this=this)
                elif this is not None:
                    # no selection of its own: the check applies to the node
                    # the enclosing context bound
                    selection = [this]
                else:
                    selection = []
            results = handler.execute(tree, rule, selection,
                                      context={"this": this,
                                               "origins": origins})
        except Exception as e:
            lopper.log._warning(f"DRC {rule.id}: error during evaluation: {e}")
            return []

        if chain:
            for r in results:
                if r.details is not None:
                    r.details["context"] = list(chain)
        return results

    def report(self) -> int:
        """Report DRC results. ``block`` severity exits immediately."""
        import sys
        error_count = 0
        for result in self.results:
            if result.passed:
                continue
            sev = (result.details or {}).get("severity", "error")
            drc_id = (result.details or {}).get("drc_id", result.check_name)
            msg = f"[{drc_id}] {result.message}"
            if sev == "info":
                lopper.log._info(msg)
            elif sev == "warning":
                lopper.log._warning(msg)
                error_count += 0  # not an error unless escalated by werror
            elif sev == "block":
                lopper.log._error(msg)
                lopper.log._error(f"[{drc_id}] is a blocking DRC; aborting")
                sys.exit(1)
            else:  # error
                if self.werror:
                    lopper.log._error(msg)
                else:
                    lopper.log._warning(msg)
                error_count += 1
        return error_count


def _val_node(node, name, default=None):
    v = node.propval(name)
    if v == [""]:
        return default
    return v[0] if isinstance(v, list) and len(v) == 1 else v


def _params_from_node(node) -> Dict[str, Any]:
    """Extract check params from a check_N node (best-effort, reference impl)."""
    params = {}
    for prop in node.__props__.values():
        if prop.name in ("check",):
            continue
        val = prop.value
        params[prop.name] = val[0] if isinstance(val, list) and len(val) == 1 else val
    return params
