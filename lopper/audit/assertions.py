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

Rule shape (YAML)::

    drc:
      - id: DRC-DOM-003           # stable catalog id
        severity: error           # error | warning | info | block
        phase: post-yaml          # early | post-yaml | post-processing
        select: ["/domains/.*"]   # node selectors (path regex [:prop[:val]])
        check: compatible-contains
        params: { token: "openamp,domain-v1" }
        message: "domain compatible must include openamp,domain-v1"

      # relational form: group-by buckets nodes per context, collect turns each
      # bucket node into a set of comparable elements
      - id: DRC-DOM-034
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
    """A single Design Rule Check, expressed as data."""
    id: str
    check: str
    phase: str = "post-processing"
    severity: str = "error"
    select: List[str] = field(default_factory=list)
    group_by: Optional[str] = None
    collect: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    message: str = ""
    enabled: bool = True
    platform: Optional[str] = None

    @property
    def phase_enum_name(self) -> str:
        return _PHASE_MAP.get(self.phase, "POST_PROCESSING")

    @property
    def phase_enum(self) -> ValidationPhase:
        return ValidationPhase[self.phase_enum_name]

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Rule":
        sev = str(d.get("severity", "error")).lower()
        if sev not in _VALID_SEVERITIES:
            raise ValueError(f"DRC {d.get('id')}: invalid severity {sev!r}")
        if "check" not in d or "id" not in d:
            raise ValueError(f"DRC rule missing required 'id'/'check': {d!r}")
        sel = d.get("select", [])
        if isinstance(sel, str):
            sel = [sel]
        return cls(
            id=str(d["id"]),
            check=str(d["check"]),
            phase=str(d.get("phase", "post-processing")),
            severity=sev,
            select=list(sel),
            group_by=d.get("group-by", d.get("group_by")),
            collect=d.get("collect"),
            params=dict(d.get("params", {})),
            message=str(d.get("message", "")),
            enabled=bool(d.get("enabled", True)),
            platform=d.get("platform"),
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
        # support a single check_0 subnode form
        params = {}
        for child in node.children():
            if child.name.startswith("check"):
                check = check or _val_node(child, "check")
                params = _params_from_node(child)
        sel = _val("select")
        if isinstance(sel, str):
            sel = [sel]
        return {
            "id": rid,
            "check": check,
            "phase": _val("phase", "post-processing"),
            "severity": _val("severity", "error"),
            "select": sel or [],
            "group-by": _val("group-by"),
            "collect": _val("collect"),
            "params": params,
            "message": _val("message", ""),
        }

    # -- querying ----------------------------------------------------------
    def rules_for_phase(self, phase: ValidationPhase) -> List[Rule]:
        return [r for r in self._rules
                if r.enabled and r.phase_enum == phase]

    def all_rules(self) -> List[Rule]:
        return list(self._rules)

    # -- selection ---------------------------------------------------------
    def select_nodes(self, tree, rule: Rule) -> List:
        """Resolve a rule's ``select`` list to a node list (union of selectors).

        Each selector is ``path_regex[:prop[:val]]`` (the lop,select shape).
        """
        seen = {}
        for sel in rule.select:
            for node in _eval_selector(tree, sel):
                seen[node.abs_path] = node
        return list(seen.values())

    def build_groups(self, tree, rule: Rule) -> Dict[str, Dict[object, frozenset]]:
        """Build the relational ``{context_path: {element: frozenset(flags)}}`` map.

        ``group-by`` selects one node per context; ``collect`` turns each into a
        set of comparable elements. Collectors may return either a bare set of
        elements (no flags) or a ``{element: flags}`` mapping (flag-aware, e.g.
        the ``access`` collector); both are normalized to the flagged form so
        relational handlers can consult per-element flags uniformly.
        """
        collector = get_collector(rule.collect)
        groups = {}
        for node in _eval_selector(tree, rule.group_by):
            raw = collector(node)
            if isinstance(raw, dict):
                elem_map = {el: frozenset(flags) for el, flags in raw.items()}
            else:
                elem_map = {el: frozenset() for el in raw}
            groups[node.abs_path] = elem_map
        return groups


def _eval_selector(tree, selector: str) -> List:
    """Evaluate one ``path_regex[:prop[:val]]`` selector against the tree."""
    if not selector:
        return []
    parts = selector.split(":")
    node_regex = parts[0]
    prop = parts[1] if len(parts) > 1 else ""
    prop_val = parts[2] if len(parts) > 2 else ""

    nodes = tree.nodes(node_regex) if node_regex.startswith("/") else tree.lnodes(node_regex)

    if not prop:
        return nodes
    filtered = []
    for n in nodes:
        val = n.propval(prop)
        if val == [""]:
            continue
        if not prop_val:
            filtered.append(n)
            continue
        hay = " ".join(str(v) for v in (val if isinstance(val, list) else [val]))
        if re.search(prop_val, hay):
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

    def _rule_active(self, rule: Rule) -> bool:
        if self._id_filter:
            return rule.id in self._id_filter
        return True

    def run_phase(self, phase, tree, **kwargs):
        registry = get_drc_registry()
        phase_results = []
        for rule in registry.rules_for_phase(phase):
            if not self._rule_active(rule):
                continue
            handler = CheckHandlerRegistry.get(rule.check)
            if handler is None:
                lopper.log._warning(
                    f"DRC {rule.id}: unknown check type '{rule.check}', skipping")
                continue
            try:
                if handler.RELATIONAL:
                    selection = registry.build_groups(tree, rule)
                else:
                    selection = registry.select_nodes(tree, rule)
                phase_results.extend(handler.execute(tree, rule, selection))
            except Exception as e:
                lopper.log._warning(f"DRC {rule.id}: error during evaluation: {e}")

        self.results.extend(phase_results)
        return phase_results

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
