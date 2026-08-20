#/*
# * Copyright (c) 2026 AMD Inc. All rights reserved.
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

"""
Tests for the data-driven DRC assertion language (lopper/audit/assertions.py,
lopper/audit/checks.py).

Covers:
  - Rule parsing + validation
  - shipped catalog load
  - single-node handlers: required, enum, compatible-contains, ref-exists
  - relational handlers: exclusive-across via property:* and cpu-cores collectors
    (domain-id-unique unique id, device-exclusive device exclusivity, cpu-core-exclusive CPU core)
  - severity handling (block -> hard stop; error count)
  - per-id flag filtering
  - end-to-end via ValidatorRegistry.run_phase
"""

import glob
import os

import pytest

from lopper.tree import LopperTree, LopperNode, LopperProp
import lopper.audit as A
from lopper.audit import (
    Rule, AssertionRegistry, DRCValidator,
    ValidationPhase, get_drc_registry, reset_drc_registry,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _domain(tree, path, *, compat="openamp,domain-v1", id_=None, os_type=None,
            parent=None, cpus=None, access=None):
    n = LopperNode(-1, path)
    if compat is not None:
        n + LopperProp(name="compatible", value=[compat])
    if id_ is not None:
        n + LopperProp(name="id", value=[id_])
    if os_type is not None:
        n + LopperProp(name="os,type", value=[os_type])
    if parent is not None:
        n + LopperProp(name="parent", value=[parent])
    if cpus is not None:
        n + LopperProp(name="cpus", value=list(cpus))
    if access is not None:
        n + LopperProp(name="access", value=list(access))
    tree.add(n)
    return n


def _tree_with_domains():
    tree = LopperTree()
    return tree


def _plain_node(tree, path, **props):
    """A node with arbitrary properties and no domain compatible."""
    n = LopperNode(-1, path)
    for k, v in props.items():
        n + LopperProp(name=k.replace("__", ","), value=[v])
    tree.add(n)
    return n


def _run(rule_ids, tree, phase, werror=False):
    """Run a fresh DRCValidator for given phase, return (results, error_count)."""
    warnings = ["drc_all"] if rule_ids is None else [f"drc:{i}" for i in rule_ids]
    v = DRCValidator(warnings=warnings, werror=werror)
    v.run_phase(phase, tree)
    return v


# --------------------------------------------------------------------------
# Rule parsing
# --------------------------------------------------------------------------

class TestRuleParsing:
    def test_from_dict_minimal(self):
        r = Rule.from_dict({"id": "X-1", "check": "required",
                            "params": {"properties": ["id"]}})
        assert r.id == "X-1"
        assert r.check == "required"
        assert r.phase == "post-processing"
        assert r.severity == "error"

    def test_select_string_coerced_to_list(self):
        r = Rule.from_dict({"id": "X", "check": "required", "select": "/domains/.*"})
        assert r.select == ["/domains/.*"]

    def test_phase_enum_mapping(self):
        r = Rule.from_dict({"id": "X", "check": "required", "phase": "post-yaml"})
        assert r.phase_enum == ValidationPhase.POST_YAML

    def test_invalid_severity_raises(self):
        with pytest.raises(ValueError):
            Rule.from_dict({"id": "X", "check": "required", "severity": "fatal"})

    def test_missing_check_raises(self):
        with pytest.raises(ValueError):
            Rule.from_dict({"id": "X"})


# --------------------------------------------------------------------------
# Shipped catalog
# --------------------------------------------------------------------------

class TestShippedCatalog:
    def test_catalog_loads_expected_ids(self):
        reset_drc_registry()
        reg = get_drc_registry()
        ids = {r.id for r in reg.all_rules()}
        for expect in ("domain-compatible", "domain-id-present", "domain-os-type",
                       "domain-id-unique", "domain-parent-exists", "cpu-core-exclusive", "device-exclusive"):
            assert expect in ids

    def test_handlers_registered(self):
        known = A.CheckHandlerRegistry.known_types()
        for h in ("required", "enum", "compatible-contains", "ref-exists",
                  "ref-valid", "exclusive-across"):
            assert h in known


# --------------------------------------------------------------------------
# Single-node handlers
# --------------------------------------------------------------------------

class TestSingleNodeChecks:
    def test_required_missing_id_fails(self):
        # cpus/memory give the nodes the structural evidence that candidate
        # domain selection looks for (see domains-baseline.yaml).
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", id_=1, cpus=[1, 1, 0])   # ok
        _domain(tree, "/domains/d2", cpus=[1, 2, 0])          # missing id
        tree.sync()
        v = _run(["domain-id-present"], tree, ValidationPhase.POST_YAML)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert fails[0].source_path == "/domains/d2"

    def test_enum_bad_os_type_fails(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", os_type="linux")     # ok
        _domain(tree, "/domains/d2", os_type="plan9")     # bad
        tree.sync()
        v = _run(["domain-os-type"], tree, ValidationPhase.POST_YAML)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert fails[0].source_path == "/domains/d2"

    def test_compatible_contains_fails(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", cpus=[1, 1, 0])                      # has token
        _domain(tree, "/domains/d2", compat="acme,thing", cpus=[1, 2, 0]) # missing token
        tree.sync()
        v = _run(["domain-compatible"], tree, ValidationPhase.POST_YAML)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert fails[0].source_path == "/domains/d2"

    def test_ref_exists_bad_parent_fails(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/parent_dom", id_=1)
        _domain(tree, "/domains/d2", id_=2, parent="/domains/parent_dom")  # ok
        _domain(tree, "/domains/d3", id_=3, parent="/domains/ghost")       # dangling
        tree.sync()
        v = _run(["domain-parent-exists"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert fails[0].source_path == "/domains/d3"

    def test_clean_tree_passes(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", id_=1, os_type="linux")
        tree.sync()
        v = _run(["domain-compatible", "domain-id-present", "domain-os-type"],
                 tree, ValidationPhase.POST_YAML)
        assert [r for r in v.results if not r.passed] == []


# --------------------------------------------------------------------------
# Relational handlers
# --------------------------------------------------------------------------

class TestDomainSelection:
    """R10: /domains/.* matched every descendant, not just domains."""

    def test_chosen_child_is_not_a_domain(self):
        # The reported false positive: a `chosen` child under a domain was
        # selected as a domain and reported for DOM-003/DOM-004.
        tree = _tree_with_domains()
        _domain(tree, "/domains/openamp_r5", id_=1, cpus=[1, 0x2, 0x80000000])
        _plain_node(tree, "/domains/openamp_r5/chosen", bootargs="console=ttyAMA0")
        tree.sync()
        v = _run(["domain-compatible", "domain-id-present"], tree, ValidationPhase.POST_YAML)
        paths = [r.source_path for r in v.results if not r.passed]
        assert not any("chosen" in (p or "") for p in paths), paths

    def test_group_node_is_not_a_domain(self):
        # openamp,group-v1 nodes live alongside domains and carry `access`,
        # so `access` must not be part of the candidate criteria.
        tree = _tree_with_domains()
        g = LopperNode(-1, "/domains/resource_group_1")
        g + LopperProp(name="compatible", value=["openamp,group-v1"])
        g + LopperProp(name="access", value=[100, 0])
        tree.add(g)
        tree.sync()
        v = _run(["domain-compatible", "domain-id-present"], tree, ValidationPhase.POST_YAML)
        paths = [r.source_path for r in v.results if not r.passed]
        assert not any("resource_group" in (p or "") for p in paths), paths

    def test_group_node_with_memory_is_not_a_domain(self):
        # resource_group_2 in the xen SDT is `compatible = openamp,group-v1`
        # with only a `memory` property -- so `memory` cannot be part of the
        # candidate criteria either.
        tree = _tree_with_domains()
        g = LopperNode(-1, "/domains/resource_group_2")
        g + LopperProp(name="compatible", value=["openamp,group-v1"])
        g + LopperProp(name="memory", value=[0x0, 0x500000, 0x0, 0x1000])
        tree.add(g)
        tree.sync()
        v = _run(["domain-compatible", "domain-id-present"], tree, ValidationPhase.POST_YAML)
        paths = [r.source_path for r in v.results if not r.passed]
        assert not any("resource_group" in (p or "") for p in paths), paths

    def test_devices_inventory_not_selected_strictly(self):
        # "openamp,domain-v1,devices" is ONE string (a device inventory), not
        # the domain compatible. Substring matching used to conflate them.
        tree = _tree_with_domains()
        _domain(tree, "/domains/inventory", compat="openamp,domain-v1,devices",
                os_type="bogus")
        tree.sync()
        v = _run(["domain-os-type"], tree, ValidationPhase.POST_YAML)
        assert [r for r in v.results if not r.passed] == []

    def test_multi_value_compatible_is_selected(self):
        # `compatible = "openamp,domain-v1", "xen,domain-v2"` IS a domain.
        tree = _tree_with_domains()
        n = LopperNode(-1, "/domains/guest")
        n + LopperProp(name="compatible",
                       value=["openamp,domain-v1", "xen,domain-v2"])
        n + LopperProp(name="os,type", value=["bogus"])
        tree.add(n)
        tree.sync()
        v = _run(["domain-os-type"], tree, ValidationPhase.POST_YAML)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert fails[0].source_path == "/domains/guest"

    def test_nested_domain_is_selected(self):
        # Domains nest (hypervisor -> guests); depth must not disqualify them.
        tree = _tree_with_domains()
        _domain(tree, "/domains/subsystem1", id_=1)
        _domain(tree, "/domains/subsystem1/xen", id_=2)
        _domain(tree, "/domains/subsystem1/xen/dom0", os_type="bogus")
        tree.sync()
        v = _run(["domain-os-type"], tree, ValidationPhase.POST_YAML)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert fails[0].source_path == "/domains/subsystem1/xen/dom0"

    def test_domain_missing_compatible_still_caught(self):
        # The reason DOM-003 uses candidate selection: a domain authored
        # outside the YAML path can genuinely lack the compatible.
        tree = _tree_with_domains()
        _domain(tree, "/domains/handwritten", compat=None, id_=1,
                cpus=[1, 0x1, 0])
        tree.sync()
        v = _run(["domain-compatible"], tree, ValidationPhase.POST_YAML)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert fails[0].source_path == "/domains/handwritten"


class TestSelectorMatching:
    """Selector property values match a whole list element."""

    def test_prop_only_means_has_property(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/a", cpus=[1, 1, 0])
        _domain(tree, "/domains/b")
        tree.sync()
        from lopper.audit.assertions import _eval_selector
        got = {n.abs_path for n in _eval_selector(tree, "/domains/.*:cpus")}
        assert got == {"/domains/a"}

    def test_value_match_is_whole_element(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/real", compat="openamp,domain-v1")
        _domain(tree, "/domains/inv", compat="openamp,domain-v1,devices")
        tree.sync()
        from lopper.audit.assertions import _eval_selector
        got = {n.abs_path for n in
               _eval_selector(tree, "/domains/.*:compatible:openamp,domain-v1")}
        assert got == {"/domains/real"}

    def test_regex_values_still_work(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/a", os_type="linux")
        _domain(tree, "/domains/b", os_type="zephyr")
        tree.sync()
        from lopper.audit.assertions import _eval_selector
        got = {n.abs_path for n in
               _eval_selector(tree, "/domains/.*:os,type:li.*")}
        assert got == {"/domains/a"}


class TestRelationalChecks:
    def test_unique_id_conflict(self):
        # domain-id-unique: two domains share id 5
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", id_=5)
        _domain(tree, "/domains/d2", id_=5)
        _domain(tree, "/domains/d3", id_=6)
        tree.sync()
        v = _run(["domain-id-unique"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1

    def test_device_exclusivity_conflict(self):
        # device-exclusive: device 100 in two domains' access lists, neither shared.
        # access is (phandle, flags-cell) pairs; flags 0 = not shared.
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", access=[100, 0, 101, 0])
        _domain(tree, "/domains/d2", access=[100, 0, 102, 0])
        tree.sync()
        v = _run(["device-exclusive"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert "100" in fails[0].message

    def test_device_exclusivity_clean(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", access=[100, 0, 101, 0])
        _domain(tree, "/domains/d2", access=[102, 0, 103, 0])
        tree.sync()
        v = _run(["device-exclusive"], tree, ValidationPhase.POST_PROCESSING)
        assert [r for r in v.results if not r.passed] == []

    def test_device_exclusivity_shared_exempt(self):
        # Both domains mark device 100 shared (flags bit 0) -> exempt, no fail.
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", access=[100, 0x1, 101, 0])
        _domain(tree, "/domains/d2", access=[100, 0x1, 102, 0])
        tree.sync()
        v = _run(["device-exclusive"], tree, ValidationPhase.POST_PROCESSING)
        assert [r for r in v.results if not r.passed] == []

    def test_device_exclusivity_partial_shared_fails(self):
        # One domain marks shared, the other does not -> still a conflict.
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", access=[100, 0x1, 101, 0])
        _domain(tree, "/domains/d2", access=[100, 0x0, 102, 0])
        tree.sync()
        v = _run(["device-exclusive"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert "100" in fails[0].message

    def test_cpu_core_conflict(self):
        # cpu-core-exclusive: cluster 1 core 0 claimed by both domains
        # cpus triplet = (cluster, cpumask, el)
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", cpus=[1, 0x3, 1])  # cores 0,1
        _domain(tree, "/domains/d2", cpus=[1, 0x1, 1])  # core 0  -> conflict
        tree.sync()
        v = _run(["cpu-core-exclusive"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1

    def test_cpu_core_clean_different_clusters(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", cpus=[1, 0x3, 1])
        _domain(tree, "/domains/d2", cpus=[2, 0x3, 1])  # different cluster
        tree.sync()
        v = _run(["cpu-core-exclusive"], tree, ValidationPhase.POST_PROCESSING)
        assert [r for r in v.results if not r.passed] == []


# --------------------------------------------------------------------------
# Severity + flag filtering
# --------------------------------------------------------------------------

class TestSeverityAndFlags:
    def test_block_severity_exits(self):
        reset_drc_registry()
        reg = get_drc_registry()
        reg.add_rule(Rule.from_dict({
            "id": "test-block-severity", "check": "required", "phase": "post-yaml",
            "severity": "block", "select": ["/domains/.*"],
            "params": {"properties": ["id"]},
            "message": "blocking: id required",
        }))
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1")  # missing id -> blocking fail
        tree.sync()
        v = DRCValidator(warnings=["drc:test-block-severity"])
        v.run_phase(ValidationPhase.POST_YAML, tree)
        with pytest.raises(SystemExit):
            v.report()
        reset_drc_registry()

    def test_error_severity_counts(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", cpus=[1, 1, 0])  # missing id
        tree.sync()
        v = _run(["domain-id-present"], tree, ValidationPhase.POST_YAML, werror=True)
        assert v.report() == 1

    def test_id_filter_runs_only_named_rule(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", compat="acme,x")  # violates DOM-003 only
        tree.sync()
        # enable only DOM-004 (required id) -> DOM-003 (compat) must NOT run
        v = _run(["domain-id-present"], tree, ValidationPhase.POST_YAML)
        ids = {(r.details or {}).get("drc_id") for r in v.results if not r.passed}
        assert "domain-compatible" not in ids

    def test_disabled_when_no_drc_flag(self):
        v = DRCValidator(warnings=["memory_all"])
        assert v.is_enabled() is False

    def test_enabled_with_drc_all(self):
        v = DRCValidator(warnings=["drc_all"])
        assert v.is_enabled() is True

    def test_w_all_does_not_enable_drc(self):
        # R0: DRCs are opt-in only; '-W all' must NOT sweep them in while the
        # catalog is incomplete.
        v = DRCValidator(warnings=["all"])
        assert v.is_enabled() is False


# --------------------------------------------------------------------------
# End-to-end through the registry
# --------------------------------------------------------------------------

class TestNesting:
    """Contexts iterate and bind `this`; only leaves report."""

    def _reg(self, *rule_dicts):
        reset_drc_registry()
        reg = get_drc_registry()
        for d in rule_dicts:
            reg.add_rule(Rule.from_dict(d))
        return reg

    def test_context_gates_children(self):
        # context selects only linux domains -> the child never sees the zephyr one
        self._reg({
            "id": "LNX-CONTEXT", "phase": "post-yaml",
            "select": ["/domains/.*:os,type:linux"],
            "rules": [{"id": "T-NEEDS-ID", "check": "required",
                       "params": {"properties": ["id"]},
                       "message": "needs id"}],
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/lin", os_type="linux")      # missing id -> fails
        _domain(tree, "/domains/zep", os_type="zephyr")     # missing id -> not selected
        tree.sync()
        v = _run(["LNX-CONTEXT"], tree, ValidationPhase.POST_YAML)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert fails[0].source_path == "/domains/lin"
        reset_drc_registry()

    def test_context_selecting_nothing_runs_nothing(self):
        self._reg({
            "id": "EMPTY-CONTEXT", "phase": "post-yaml",
            "select": ["/domains/.*:os,type:plan9"],
            "rules": [{"id": "T-NEVER", "check": "required",
                       "params": {"properties": ["nope"]}, "message": "x"}],
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/lin", os_type="linux")
        tree.sync()
        v = _run(["EMPTY-CONTEXT"], tree, ValidationPhase.POST_YAML)
        assert [r for r in v.results if not r.passed] == []
        reset_drc_registry()

    def test_context_itself_never_reported(self):
        self._reg({
            "id": "CONTEXT-X", "phase": "post-yaml",
            "select": ["/domains/.*:os,type:linux"],
            "rules": [{"id": "LEAF-Y", "check": "required",
                       "params": {"properties": ["id"]}, "message": "y"}],
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/lin", os_type="linux")
        tree.sync()
        v = _run(["CONTEXT-X"], tree, ValidationPhase.POST_YAML)
        ids = {(r.details or {}).get("drc_id") for r in v.results if not r.passed}
        assert ids == {"LEAF-Y"}          # the context id never appears as a verdict
        reset_drc_registry()

    def test_severity_inherited_and_overridable(self):
        self._reg({
            "id": "SEV-CONTEXT", "phase": "post-yaml", "severity": "warning",
            "select": ["/domains/.*:os,type:linux"],
            "rules": [
                {"id": "INHERITS", "check": "required",
                 "params": {"properties": ["aaa"]}, "message": "a"},
                {"id": "OVERRIDES", "check": "required", "severity": "error",
                 "params": {"properties": ["bbb"]}, "message": "b"},
            ],
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/lin", os_type="linux")
        tree.sync()
        v = _run(["SEV-CONTEXT"], tree, ValidationPhase.POST_YAML)
        sev = {(r.details or {}).get("drc_id"): (r.details or {}).get("severity")
               for r in v.results if not r.passed}
        assert sev == {"INHERITS": "warning", "OVERRIDES": "error"}
        reset_drc_registry()

    def test_context_id_is_a_group_handle(self):
        # -W drc:<context id> enables everything beneath it
        self._reg({
            "id": "GRP", "phase": "post-yaml",
            "select": ["/domains/.*:os,type:linux"],
            "rules": [{"id": "UNDER-GRP", "check": "required",
                       "params": {"properties": ["id"]}, "message": "z"}],
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/lin", os_type="linux")
        tree.sync()
        v = _run(["GRP"], tree, ValidationPhase.POST_YAML)
        assert len([r for r in v.results if not r.passed]) == 1
        reset_drc_registry()

    def test_context_chain_recorded_in_details(self):
        self._reg({
            "id": "OUTER", "phase": "post-yaml",
            "select": ["/domains/.*:os,type:linux"],
            "rules": [{"id": "INNER-LEAF", "check": "required",
                       "params": {"properties": ["id"]}, "message": "q"}],
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/lin", os_type="linux")
        tree.sync()
        v = _run(["OUTER"], tree, ValidationPhase.POST_YAML)
        fails = [r for r in v.results if not r.passed]
        assert fails[0].details.get("context") == ["OUTER"]
        reset_drc_registry()

    def test_leaf_without_select_checks_bound_node(self):
        # the context binds `this`; a leaf with no select of its own uses it
        self._reg({
            "id": "BIND", "phase": "post-yaml",
            "select": ["/domains/.*:os,type:linux"],
            "rules": [{"id": "ON-THIS", "check": "compatible-contains",
                       "params": {"token": "openamp,domain-v1"},
                       "message": "compat"}],
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/good", os_type="linux")
        _domain(tree, "/domains/bad", os_type="linux", compat="acme,thing")
        tree.sync()
        v = _run(["BIND"], tree, ValidationPhase.POST_YAML)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert fails[0].source_path == "/domains/bad"
        reset_drc_registry()


class TestContextXorCheck:
    def test_both_rules_and_check_rejected(self):
        with pytest.raises(ValueError, match="context"):
            Rule.from_dict({"id": "BAD", "check": "required",
                            "rules": [{"id": "K", "check": "required"}]})

    def test_neither_rejected(self):
        with pytest.raises(ValueError, match="check.*rules|rules"):
            Rule.from_dict({"id": "BAD2", "select": ["/x"]})

    def test_children_parsed_recursively(self):
        r = Rule.from_dict({
            "id": "A", "select": ["/x"],
            "rules": [{"id": "B", "select": ["/y"],
                       "rules": [{"id": "C", "check": "required"}]}],
        })
        assert r.is_context and r.rules[0].is_context
        assert r.rules[0].rules[0].id == "C"
        assert not r.rules[0].rules[0].is_context


class TestPredicateSelectorTerms:
    """A select term may be a predicate, evaluated by an ordinary handler."""

    def _sel(self, tree, terms, this=None):
        reg = get_drc_registry()
        r = Rule.from_dict({"id": "SEL-PROBE", "check": "required",
                            "params": {"properties": ["nope"]},
                            "select": terms, "message": "x"})
        return {n.abs_path for n in reg.select_nodes(tree, r, this=this)}

    def test_predicate_refines_selection(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/good", cpus=[1, 1, 0])
        _domain(tree, "/domains/bad", cpus=[1, 2, 0], compat="acme,thing")
        tree.sync()
        got = self._sel(tree, [
            "/domains/.*",
            {"check": "compatible-contains",
             "params": {"token": "openamp,domain-v1"}},
        ])
        assert got == {"/domains/good"}

    def test_predicate_negation(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/good", cpus=[1, 1, 0])
        _domain(tree, "/domains/bad", cpus=[1, 2, 0], compat="acme,thing")
        tree.sync()
        got = self._sel(tree, [
            "/domains/.*",
            {"check": "compatible-contains",
             "params": {"token": "openamp,domain-v1"}, "negate": True},
        ])
        assert got == {"/domains/bad"}

    def test_multiple_predicates_and_together(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/a", id_=1, os_type="linux")
        _domain(tree, "/domains/b", os_type="linux")            # no id
        tree.sync()
        got = self._sel(tree, [
            "/domains/.*",
            {"check": "required", "params": {"properties": ["id"]}},
            {"check": "enum",
             "params": {"property": "os,type", "values": ["linux"]}},
        ])
        assert got == {"/domains/a"}

    def test_unknown_predicate_is_ignored_not_fatal(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/a", cpus=[1, 1, 0])
        tree.sync()
        got = self._sel(tree, ["/domains/.*", {"check": "no-such-check"}])
        assert got == {"/domains/a"}      # term ignored, selection preserved


class TestSelectorRefineForm:
    """A string term with an empty node regex refines (AND), per lop,select."""

    def test_empty_node_regex_refines(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/a", cpus=[1, 1, 0], os_type="linux")
        _domain(tree, "/domains/b", cpus=[1, 2, 0])
        tree.sync()
        reg = get_drc_registry()
        r = Rule.from_dict({"id": "P", "check": "required",
                            "params": {"properties": ["nope"]},
                            "select": ["/domains/.*", ":os,type"],
                            "message": "x"})
        got = {n.abs_path for n in reg.select_nodes(tree, r)}
        assert got == {"/domains/a"}

    def test_terms_with_node_regex_still_union(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/a", cpus=[1, 1, 0])
        _domain(tree, "/domains/b", os_type="linux")
        tree.sync()
        reg = get_drc_registry()
        r = Rule.from_dict({"id": "P", "check": "required",
                            "params": {"properties": ["nope"]},
                            "select": ["/domains/.*:cpus", "/domains/.*:os,type"],
                            "message": "x"})
        got = {n.abs_path for n in reg.select_nodes(tree, r)}
        assert got == {"/domains/a", "/domains/b"}


class TestNamedGroups:
    """group-by may be named groups; two groups == disjointness."""

    def _reg(self, *rule_dicts):
        reset_drc_registry()
        reg = get_drc_registry()
        for d in rule_dicts:
            reg.add_rule(Rule.from_dict(d))
        return reg

    def test_two_named_groups_detect_overlap(self):
        self._reg({
            "id": "TWO-GRP", "phase": "post-processing",
            "group-by": {"left": ["/domains/l.*"], "right": ["/domains/r.*"]},
            "collect": "access",
            "check": "exclusive-across",
            "message": "claimed by both sides",
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/lone", access=[100, 0])
        _domain(tree, "/domains/rone", access=[100, 0])   # same device
        tree.sync()
        v = _run(["TWO-GRP"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert "left" in fails[0].message and "right" in fails[0].message
        reset_drc_registry()

    def test_members_of_the_same_group_do_not_fire(self):
        # two nodes in one named group collapse -> not a conflict for this rule
        self._reg({
            "id": "ONE-GRP", "phase": "post-processing",
            "group-by": {"left": ["/domains/l.*"], "right": ["/domains/r.*"]},
            "collect": "access",
            "check": "exclusive-across",
            "message": "x",
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/lone", access=[100, 0])
        _domain(tree, "/domains/ltwo", access=[100, 0])   # same group
        tree.sync()
        v = _run(["ONE-GRP"], tree, ValidationPhase.POST_PROCESSING)
        assert [r for r in v.results if not r.passed] == []
        reset_drc_registry()

    def test_self_exclusion_via_negated_is_node(self):
        # "this domain's devices vs every OTHER domain's devices"
        self._reg({
            "id": "SELF-CONTEXT", "phase": "post-processing",
            "select": ["/domains/host"],
            "rules": [{
                "id": "SELF-EXCL",
                "group-by": {
                    "mine": ["/domains/host"],
                    "others": ["/domains/.*",
                               {"check": "is-node",
                                "params": {"equals": "this"},
                                "negate": True}],
                },
                "collect": "access",
                "check": "exclusive-across",
                "message": "device claimed elsewhere",
            }],
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/host", access=[100, 0])
        _domain(tree, "/domains/other", access=[100, 0])
        tree.sync()
        v = _run(["SELF-CONTEXT"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1        # host vs other, host not compared to itself
        reset_drc_registry()

    def test_is_node_predicate_selects_bound_node(self):
        tree = _tree_with_domains()
        a = _domain(tree, "/domains/a", cpus=[1, 1, 0])
        _domain(tree, "/domains/b", cpus=[1, 2, 0])
        tree.sync()
        reg = get_drc_registry()
        r = Rule.from_dict({"id": "P", "check": "required",
                            "params": {"properties": ["nope"]},
                            "select": ["/domains/.*",
                                       {"check": "is-node",
                                        "params": {"equals": "this"}}],
                            "message": "x"})
        got = {n.abs_path for n in reg.select_nodes(tree, r, this=a)}
        assert got == {"/domains/a"}


class TestCountAndGuards:
    """count is a verdict about the selection; guards condition on globals."""

    def _reg(self, *rule_dicts):
        reset_drc_registry()
        reg = get_drc_registry()
        for d in rule_dicts:
            reg.add_rule(Rule.from_dict(d))
        return reg

    def test_count_max_exceeded(self):
        # the wildcard-in-more-than-one-domain case
        self._reg({
            "id": "CNT-MAX", "phase": "post-processing",
            "select": ["/domains/.*:glob"],
            "check": "count", "params": {"max": 1},
            "message": "glob used in more than one domain",
        })
        tree = _tree_with_domains()
        for p in ("/domains/a", "/domains/b"):
            n = LopperNode(-1, p)
            n + LopperProp(name="glob", value=["*"])
            tree.add(n)
        tree.sync()
        v = _run(["CNT-MAX"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1                  # one verdict about the set
        assert "found 2" in fails[0].message
        reset_drc_registry()

    def test_count_min_and_exact(self):
        self._reg(
            {"id": "CNT-MIN", "phase": "post-processing",
             "select": ["/domains/.*:cpus"], "check": "count",
             "params": {"min": 2}, "message": "need 2"},
            {"id": "CNT-EXACT", "phase": "post-processing",
             "select": ["/domains/.*:cpus"], "check": "count",
             "params": {"exact": 1}, "message": "need exactly 1"},
        )
        tree = _tree_with_domains()
        _domain(tree, "/domains/only", cpus=[1, 1, 0])
        tree.sync()
        v = _run(["CNT-MIN", "CNT-EXACT"], tree, ValidationPhase.POST_PROCESSING)
        ids = {(r.details or {}).get("drc_id")
               for r in v.results if not r.passed}
        assert ids == {"CNT-MIN"}               # exact:1 satisfied, min:2 not
        reset_drc_registry()

    def test_guard_blocks_children_when_not_satisfied(self):
        # "no hypervisor domain exists" -> guard false when one DOES exist
        self._reg({
            "id": "NO-HYP", "phase": "post-processing",
            "guard": {"select": ["/domains/.*:os,type:xen"],
                      "count": {"max": 0}},
            "rules": [{"id": "HYP-CHILD", "check": "required",
                       "params": {"properties": ["nope"]},
                       "select": ["/domains/.*:cpus"],
                       "message": "child ran"}],
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/hyp", os_type="xen", cpus=[1, 1, 0])
        tree.sync()
        v = _run(["NO-HYP"], tree, ValidationPhase.POST_PROCESSING)
        assert [r for r in v.results if not r.passed] == []
        reset_drc_registry()

    def test_guard_allows_children_when_satisfied(self):
        self._reg({
            "id": "NO-HYP2", "phase": "post-processing",
            "guard": {"select": ["/domains/.*:os,type:xen"],
                      "count": {"max": 0}},
            "rules": [{"id": "HYP-CHILD2", "check": "required",
                       "params": {"properties": ["nope"]},
                       "select": ["/domains/.*:cpus"],
                       "message": "child ran"}],
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/lin", os_type="linux", cpus=[1, 1, 0])
        tree.sync()
        v = _run(["NO-HYP2"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1 and fails[0].details["drc_id"] == "HYP-CHILD2"
        reset_drc_registry()


class TestRangeCollectorAndIntervals:
    """Ranges are compared as intervals, not by equality."""

    def _reg(self, *rule_dicts):
        reset_drc_registry()
        reg = get_drc_registry()
        for d in rule_dicts:
            reg.add_rule(Rule.from_dict(d))
        return reg

    def _mem_domain(self, tree, path, cells):
        n = LopperNode(-1, path)
        n + LopperProp(name="compatible", value=["openamp,domain-v1"])
        n + LopperProp(name="#address-cells", value=[2])
        n + LopperProp(name="#size-cells", value=[2])
        n + LopperProp(name="memory", value=list(cells))
        tree.add(n)
        return n

    def test_range_collector_decodes_pairs(self):
        from lopper.audit.checks import get_collector
        tree = _tree_with_domains()
        n = self._mem_domain(tree, "/domains/d", [0x0, 0x0, 0x0, 0x8000000])
        tree.sync()
        got = get_collector({"property": "memory", "kind": "range"})(n, tree)
        assert got == {(0x0, 0x8000000)}

    def test_range_collector_honours_cell_widths(self):
        # cell widths come from the parent, per the usual device tree rule,
        # via the shared _get_cell_sizes helper
        from lopper.audit.checks import get_collector
        tree = _tree_with_domains()
        parent = LopperNode(-1, "/one_cell")
        parent + LopperProp(name="#address-cells", value=[1])
        parent + LopperProp(name="#size-cells", value=[1])
        tree.add(parent)
        n = LopperNode(-1, "/one_cell/d")
        n + LopperProp(name="memory", value=[0x1000, 0x100, 0x8000, 0x200])
        tree.add(n)
        tree.sync()
        got = get_collector({"property": "memory", "kind": "range"})(n, tree)
        assert got == {(0x1000, 0x100), (0x8000, 0x200)}

    def test_cell_widths_can_be_pinned_in_the_spec(self):
        from lopper.audit.checks import get_collector
        tree = _tree_with_domains()
        n = LopperNode(-1, "/domains/pinned")
        n + LopperProp(name="memory", value=[0x1000, 0x100])
        tree.add(n)
        tree.sync()
        got = get_collector({"property": "memory", "kind": "range",
                             "address-cells": 1, "size-cells": 1})(n, tree)
        assert got == {(0x1000, 0x100)}

    def test_overlapping_domains_flagged(self):
        self._reg({
            "id": "OVL", "phase": "post-processing",
            "group-by": "/domains/.*:compatible:openamp,domain-v1",
            "collect": {"property": "memory", "kind": "range"},
            "check": "no-overlap", "message": "overlap",
        })
        tree = _tree_with_domains()
        self._mem_domain(tree, "/domains/a", [0x0, 0x0, 0x0, 0x2000])
        self._mem_domain(tree, "/domains/b", [0x0, 0x1000, 0x0, 0x2000])
        tree.sync()
        v = _run(["OVL"], tree, ValidationPhase.POST_PROCESSING)
        assert len([r for r in v.results if not r.passed]) == 1
        reset_drc_registry()

    def test_adjacent_ranges_do_not_overlap(self):
        self._reg({
            "id": "ADJ", "phase": "post-processing",
            "group-by": "/domains/.*:compatible:openamp,domain-v1",
            "collect": {"property": "memory", "kind": "range"},
            "check": "no-overlap", "message": "overlap",
        })
        tree = _tree_with_domains()
        self._mem_domain(tree, "/domains/a", [0x0, 0x0, 0x0, 0x1000])
        self._mem_domain(tree, "/domains/b", [0x0, 0x1000, 0x0, 0x1000])
        tree.sync()
        v = _run(["ADJ"], tree, ValidationPhase.POST_PROCESSING)
        assert [r for r in v.results if not r.passed] == []
        reset_drc_registry()

    def test_identical_ranges_would_pass_equality_but_overlap(self):
        # the point of a range kind: equality-based exclusivity would also
        # catch this, but overlap catches the partial case above too
        self._reg({
            "id": "SAME", "phase": "post-processing",
            "group-by": "/domains/.*:compatible:openamp,domain-v1",
            "collect": {"property": "memory", "kind": "range"},
            "check": "no-overlap", "message": "overlap",
        })
        tree = _tree_with_domains()
        self._mem_domain(tree, "/domains/a", [0x0, 0x0, 0x0, 0x1000])
        self._mem_domain(tree, "/domains/b", [0x0, 0x0, 0x0, 0x1000])
        tree.sync()
        v = _run(["SAME"], tree, ValidationPhase.POST_PROCESSING)
        assert len([r for r in v.results if not r.passed]) == 1
        reset_drc_registry()

    def test_parent_child_overlap_is_not_a_conflict(self):
        # domains nest; a child's memory lying inside its parent's is the
        # subset relation, not two independent claimants colliding
        self._reg({
            "id": "NEST", "phase": "post-processing",
            "group-by": "/domains/.*:compatible:openamp,domain-v1",
            "collect": {"property": "memory", "kind": "range"},
            "check": "no-overlap", "message": "overlap",
        })
        tree = _tree_with_domains()
        self._mem_domain(tree, "/domains/parent", [0x0, 0x0, 0x0, 0x8000])
        self._mem_domain(tree, "/domains/parent/child", [0x0, 0x1000, 0x0, 0x1000])
        tree.sync()
        v = _run(["NEST"], tree, ValidationPhase.POST_PROCESSING)
        assert [r for r in v.results if not r.passed] == []
        reset_drc_registry()

    def test_sibling_overlap_still_flagged_under_a_parent(self):
        self._reg({
            "id": "SIB", "phase": "post-processing",
            "group-by": "/domains/.*:compatible:openamp,domain-v1",
            "collect": {"property": "memory", "kind": "range"},
            "check": "no-overlap", "message": "overlap",
        })
        tree = _tree_with_domains()
        self._mem_domain(tree, "/domains/p", [0x0, 0x0, 0x0, 0x8000])
        self._mem_domain(tree, "/domains/p/a", [0x0, 0x1000, 0x0, 0x2000])
        self._mem_domain(tree, "/domains/p/b", [0x0, 0x2000, 0x0, 0x2000])
        tree.sync()
        v = _run(["SIB"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1          # a vs b only; neither vs their parent
        assert "/domains/p/a" in fails[0].message
        assert "/domains/p/b" in fails[0].message
        reset_drc_registry()

    def test_contained_in_with_per_group_collectors(self):
        self._reg({
            "id": "INMEM", "phase": "post-processing",
            "group-by": {"claimed": ["/domains/.*:compatible:openamp,domain-v1"],
                         "physical": ["/memory@.*"]},
            "collect": {"claimed": {"property": "memory", "kind": "range"},
                        "physical": {"property": "reg", "kind": "range"}},
            "check": "contained-in", "params": {"container": "physical"},
            "message": "outside physical memory",
        })
        tree = _tree_with_domains()
        phys = LopperNode(-1, "/memory@0")
        phys + LopperProp(name="#address-cells", value=[2])
        phys + LopperProp(name="#size-cells", value=[2])
        phys + LopperProp(name="reg", value=[0x0, 0x0, 0x0, 0x8000000])
        tree.add(phys)
        self._mem_domain(tree, "/domains/ok", [0x0, 0x1000, 0x0, 0x1000])
        self._mem_domain(tree, "/domains/bad", [0x9, 0x0, 0x0, 0x1000])
        tree.sync()
        v = _run(["INMEM"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert "/domains/bad" in fails[0].message
        reset_drc_registry()


class TestTreeTargeting:
    """`tree:` retargets a rule at a named tree instead of the main one."""

    def _reg(self, *rule_dicts):
        reset_drc_registry()
        reg = get_drc_registry()
        for d in rule_dicts:
            reg.add_rule(Rule.from_dict(d))
        return reg

    def _run_with(self, rule_ids, tree, phase, subtrees=None):
        v = DRCValidator(warnings=[f"drc:{i}" for i in rule_ids])
        v.run_phase(phase, tree, subtrees=subtrees or {})
        return v

    def test_rule_runs_against_the_named_tree(self):
        self._reg({"id": "OTHER-TREE", "phase": "post-yaml",
                   "tree": "aux",
                   "select": ["/domains/.*:compatible:openamp,domain-v1"],
                   "check": "required",
                   "params": {"properties": ["id"]},
                   "message": "needs id"})
        main = _tree_with_domains()
        _domain(main, "/domains/in_main", id_=1)          # fine
        main.sync()
        aux = _tree_with_domains()
        _domain(aux, "/domains/in_aux")                   # missing id
        aux.sync()

        v = self._run_with(["OTHER-TREE"], main, ValidationPhase.POST_YAML,
                           subtrees={"aux": aux})
        fails = [r.source_path for r in v.results if not r.passed]
        assert fails == ["/domains/in_aux"]               # the aux tree, not main
        reset_drc_registry()

    def test_absent_tree_skips_loudly_rather_than_passing(self):
        self._reg({"id": "NO-TREE", "phase": "post-yaml",
                   "tree": "not-loaded",
                   "select": ["/domains/.*"],
                   "check": "required",
                   "params": {"properties": ["id"]},
                   "message": "needs id"})
        main = _tree_with_domains()
        _domain(main, "/domains/d")
        main.sync()
        v = self._run_with(["NO-TREE"], main, ValidationPhase.POST_YAML)
        # nothing reported, because the rule did not run -- not because it passed
        assert [r for r in v.results if not r.passed] == []
        reset_drc_registry()

    def test_children_inherit_the_tree(self):
        self._reg({
            "id": "T-CONTEXT", "phase": "post-yaml", "tree": "aux",
            "select": ["/domains/.*:compatible:openamp,domain-v1"],
            "rules": [{"id": "T-LEAF", "check": "required",
                       "params": {"properties": ["id"]}, "message": "id"}],
        })
        main = _tree_with_domains()
        _domain(main, "/domains/main_only")
        main.sync()
        aux = _tree_with_domains()
        _domain(aux, "/domains/aux_only")
        aux.sync()
        v = self._run_with(["T-CONTEXT"], main, ValidationPhase.POST_YAML,
                           subtrees={"aux": aux})
        fails = [r.source_path for r in v.results if not r.passed]
        assert fails == ["/domains/aux_only"]
        reset_drc_registry()

    def test_default_is_the_main_tree(self):
        self._reg({"id": "MAIN", "phase": "post-yaml",
                   "select": ["/domains/.*:compatible:openamp,domain-v1"],
                   "check": "required",
                   "params": {"properties": ["id"]}, "message": "id"})
        main = _tree_with_domains()
        _domain(main, "/domains/d")
        main.sync()
        aux = _tree_with_domains()
        _domain(aux, "/domains/other")
        aux.sync()
        v = self._run_with(["MAIN"], main, ValidationPhase.POST_YAML,
                           subtrees={"aux": aux})
        fails = [r.source_path for r in v.results if not r.passed]
        assert fails == ["/domains/d"]
        reset_drc_registry()


class TestRelativeSelectors:
    """Reaching a related node from the one a context has bound."""

    def _reg(self, *rule_dicts):
        reset_drc_registry()
        reg = get_drc_registry()
        for d in rule_dicts:
            reg.add_rule(Rule.from_dict(d))
        return reg

    def test_relative_parent_domain(self):
        from lopper.audit.assertions import _eval_relative
        tree = _tree_with_domains()
        p = _domain(tree, "/domains/mum")
        k = _domain(tree, "/domains/kid", parent="/domains/mum")
        tree.sync()
        got = _eval_relative(tree, {"relative": "parent-domain"}, k)
        assert [n.abs_path for n in got] == ["/domains/mum"]

    def test_relative_ancestor_and_guests(self):
        from lopper.audit.assertions import _eval_relative
        tree = _tree_with_domains()
        h = _domain(tree, "/domains/hyp")
        g1 = _domain(tree, "/domains/hyp/g1")
        _domain(tree, "/domains/hyp/g2")
        tree.sync()
        up = _eval_relative(tree, {"relative": "ancestor-domain"}, g1)
        assert [n.abs_path for n in up] == ["/domains/hyp"]
        down = _eval_relative(tree, {"relative": "guests"}, h)
        assert sorted(n.abs_path for n in down) == \
            ["/domains/hyp/g1", "/domains/hyp/g2"]

    def test_relative_cluster(self):
        from lopper.audit.assertions import _eval_relative
        tree = _tree_with_domains()
        c = LopperNode(-1, "/cpus-cluster@0")
        c + LopperProp(name="compatible", value=["cpus,cluster"])
        tree.add(c)
        tree.sync()
        ph = c.phandle_or_create()
        d = _domain(tree, "/domains/d", cpus=[ph, 0x1, 0])
        tree.sync()
        got = _eval_relative(tree, {"relative": "cluster"}, d)
        assert [n.abs_path for n in got] == ["/cpus-cluster@0"]

    def test_relative_term_in_a_context(self):
        # a context binds each domain; the child selects that domain's guests
        self._reg({
            "id": "REL-CONTEXT", "phase": "post-processing",
            "select": ["/domains/hyp"],
            "rules": [{"id": "REL-LEAF", "check": "required",
                       "select": [{"relative": "guests"}],
                       "params": {"properties": ["id"]},
                       "message": "guest needs id"}],
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/hyp", id_=1)
        _domain(tree, "/domains/hyp/g1", id_=2)
        _domain(tree, "/domains/hyp/g2")            # missing id
        tree.sync()
        v = _run(["REL-CONTEXT"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r.source_path for r in v.results if not r.passed]
        assert fails == ["/domains/hyp/g2"]
        reset_drc_registry()


class TestSubsetOf:
    """A node's resources must be within its containing node's."""

    def test_access_subset_of_parent(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/mum", access=[100, 0, 101, 0])
        _domain(tree, "/domains/ok", parent="/domains/mum", access=[100, 0])
        _domain(tree, "/domains/no", parent="/domains/mum", access=[999, 0])
        tree.sync()
        v = _run(["domain-access-within-parent"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r.source_path for r in v.results if not r.passed]
        assert fails == ["/domains/no"]

    def test_no_parent_is_not_a_violation(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/solo", access=[100, 0])
        tree.sync()
        v = _run(["domain-access-within-parent"], tree, ValidationPhase.POST_PROCESSING)
        assert [r for r in v.results if not r.passed] == []

    def test_nested_cpus_subset_of_ancestor(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/hyp", cpus=[1, 0x3, 0])       # cores 0,1
        _domain(tree, "/domains/hyp/ok", cpus=[1, 0x1, 0])    # core 0
        _domain(tree, "/domains/hyp/no", cpus=[1, 0x4, 0])    # core 2: not held
        tree.sync()
        v = _run(["nested-domain-cpus-within-parent"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r.source_path for r in v.results if not r.passed]
        assert fails == ["/domains/hyp/no"]


class TestSmallHandlers:
    """const, phandle-type and acyclic."""

    def _reg(self, *rule_dicts):
        reset_drc_registry()
        reg = get_drc_registry()
        for d in rule_dicts:
            reg.add_rule(Rule.from_dict(d))
        return reg

    def test_const(self):
        self._reg({"id": "C1", "phase": "post-yaml",
                   "select": ["/domains/.*:compatible:openamp,domain-v1"],
                   "check": "const",
                   "params": {"property": "os,type", "value": "linux"},
                   "message": "must be linux"})
        tree = _tree_with_domains()
        _domain(tree, "/domains/ok", os_type="linux")
        _domain(tree, "/domains/no", os_type="zephyr")
        tree.sync()
        v = _run(["C1"], tree, ValidationPhase.POST_YAML)
        assert [r.source_path for r in v.results if not r.passed] == ["/domains/no"]
        reset_drc_registry()

    def _cluster(self, tree, path, compat="cpus,cluster"):
        c = LopperNode(-1, path)
        c + LopperProp(name="compatible", value=[compat])
        tree.add(c)
        tree.sync()
        return c.phandle_or_create()

    def test_phandle_type_target_wrong_kind(self):
        tree = _tree_with_domains()
        good = self._cluster(tree, "/cpus-cluster@0")
        bad = self._cluster(tree, "/not-a-cluster", compat="acme,thing")
        _domain(tree, "/domains/ok", cpus=[good, 0x1, 0])
        _domain(tree, "/domains/no", cpus=[bad, 0x1, 0])
        tree.sync()
        v = _run(["cpus-reference-cluster"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r.source_path for r in v.results if not r.passed]
        assert fails == ["/domains/no"]

    def test_phandle_type_unresolvable(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/dangling", cpus=[0xdead, 0x1, 0])
        tree.sync()
        v = _run(["cpus-reference-cluster"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1 and "resolves to no node" in fails[0].message

    def test_acyclic_detects_a_loop(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/a", parent="/domains/b")
        _domain(tree, "/domains/b", parent="/domains/a")
        tree.sync()
        v = _run(["domain-parent-acyclic"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1            # reported once, not once per member
        assert "cycle" in fails[0].message

    def test_acyclic_accepts_a_chain(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/root")
        _domain(tree, "/domains/mid", parent="/domains/root")
        _domain(tree, "/domains/leaf", parent="/domains/mid")
        tree.sync()
        v = _run(["domain-parent-acyclic"], tree, ValidationPhase.POST_PROCESSING)
        assert [r for r in v.results if not r.passed] == []

    def test_acyclic_self_reference(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/loop", parent="/domains/loop")
        tree.sync()
        v = _run(["domain-parent-acyclic"], tree, ValidationPhase.POST_PROCESSING)
        assert len([r for r in v.results if not r.passed]) == 1


class TestNestedClaimants:
    """A domain and its own descendant are not independent claimants."""

    def _reg(self, *rule_dicts):
        reset_drc_registry()
        reg = get_drc_registry()
        for d in rule_dicts:
            reg.add_rule(Rule.from_dict(d))
        return reg

    def test_parent_and_child_sharing_a_device_is_not_a_conflict(self):
        self._reg({
            "id": "NEST-DEV", "phase": "post-processing",
            "group-by": "/domains/.*:compatible:openamp,domain-v1",
            "collect": "access", "check": "exclusive-across",
            "message": "device claimed twice",
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/host", access=[100, 0])
        _domain(tree, "/domains/host/guest", access=[100, 0])
        tree.sync()
        v = _run(["NEST-DEV"], tree, ValidationPhase.POST_PROCESSING)
        assert [r for r in v.results if not r.passed] == []
        reset_drc_registry()

    def test_unrelated_domains_sharing_a_device_still_conflict(self):
        self._reg({
            "id": "SIB-DEV", "phase": "post-processing",
            "group-by": "/domains/.*:compatible:openamp,domain-v1",
            "collect": "access", "check": "exclusive-across",
            "message": "device claimed twice",
        })
        tree = _tree_with_domains()
        _domain(tree, "/domains/a", access=[100, 0])
        _domain(tree, "/domains/b", access=[100, 0])
        tree.sync()
        v = _run(["SIB-DEV"], tree, ValidationPhase.POST_PROCESSING)
        assert len([r for r in v.results if not r.passed]) == 1
        reset_drc_registry()

    def test_cpu_conflict_guarded_off_when_a_hypervisor_exists(self):
        # cpu-core-exclusive as shipped: the catalog says the conflict is one hardware
        # cannot resolve *without a hypervisor*, so a hypervisor disarms it
        tree = _tree_with_domains()
        _domain(tree, "/domains/a", cpus=[1, 0x1, 0])
        _domain(tree, "/domains/b", cpus=[1, 0x1, 0])       # same core
        hyp = LopperNode(-1, "/domains/hyp")
        hyp + LopperProp(name="compatible",
                         value=["openamp,domain-v1", "openamp,hypervisor-v1"])
        tree.add(hyp)
        tree.sync()
        v = _run(["cpu-core-exclusive"], tree, ValidationPhase.POST_PROCESSING)
        assert [r for r in v.results if not r.passed] == []

    def test_cpu_conflict_reported_without_a_hypervisor(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/a", cpus=[1, 0x1, 0])
        _domain(tree, "/domains/b", cpus=[1, 0x1, 0])
        tree.sync()
        v = _run(["cpu-core-exclusive"], tree, ValidationPhase.POST_PROCESSING)
        assert len([r for r in v.results if not r.passed]) == 1


class TestBitmask:
    """Bit-level checks, and the same handler used as a computed condition."""

    def _reg(self, *rule_dicts):
        reset_drc_registry()
        reg = get_drc_registry()
        for d in rule_dicts:
            reg.add_rule(Rule.from_dict(d))
        return reg

    LOCKSTEP = 1 << 30

    def test_bit_set_and_clear(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/ls", cpus=[1, 0x1, self.LOCKSTEP])
        _domain(tree, "/domains/split", cpus=[1, 0x3, 0])
        tree.sync()
        self._reg({"id": "BIT", "phase": "post-processing",
                   "select": ["/domains/.*:compatible:openamp,domain-v1"],
                   "check": "bitmask",
                   "params": {"property": "cpus", "field": "exec_level",
                              "bit": 30, "set": True},
                   "message": "not lockstep"})
        v = _run(["BIT"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r.source_path for r in v.results if not r.passed]
        assert fails == ["/domains/split"]
        reset_drc_registry()

    def test_equals_and_mask(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/a", cpus=[1, 0x1, 0])
        _domain(tree, "/domains/b", cpus=[1, 0x3, 0x1234])
        tree.sync()
        self._reg(
            {"id": "EQ", "phase": "post-processing",
             "select": ["/domains/.*:compatible:openamp,domain-v1"],
             "check": "bitmask",
             "params": {"property": "cpus", "field": "cpumask", "equals": 0x1},
             "message": "cpumask"},
            {"id": "MSK", "phase": "post-processing",
             "select": ["/domains/.*:compatible:openamp,domain-v1"],
             "check": "bitmask",
             "params": {"property": "cpus", "field": "exec_level",
                        "mask": 0xc0000000},
             "message": "stray bits"},
        )
        v = _run(["EQ", "MSK"], tree, ValidationPhase.POST_PROCESSING)
        got = {((r.details or {}).get("drc_id"), r.source_path)
               for r in v.results if not r.passed}
        assert got == {("EQ", "/domains/b"), ("MSK", "/domains/b")}
        reset_drc_registry()

    def test_lockstep_rule_end_to_end(self):
        # lockstep-single-core as shipped: a computed condition (predicate term)
        # gating a computed check
        tree = _tree_with_domains()
        _domain(tree, "/domains/ok", cpus=[1, 0x1, self.LOCKSTEP])
        _domain(tree, "/domains/bad", cpus=[1, 0x3, self.LOCKSTEP])
        _domain(tree, "/domains/notls", cpus=[1, 0x3, 0])   # not lockstep: N/A
        tree.sync()
        v = _run(["lockstep-single-core"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r.source_path for r in v.results if not r.passed]
        assert fails == ["/domains/bad"]


class TestRuleSources:
    """Rules come from data, not code: shipped catalog, user files, the tree."""

    def test_shipped_catalog_ships_as_package_data(self):
        # The catalog is data next to the module, so it must be found relative
        # to the package (and be listed in MANIFEST.in, or an installed lopper
        # silently loads zero rules).
        import lopper.audit.assertions as A_
        drc_dir = os.path.join(os.path.dirname(A_.__file__), "drc")
        assert os.path.isdir(drc_dir)
        assert glob.glob(os.path.join(drc_dir, "*.yaml"))

        manifest = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(A_.__file__))),
            "..", "MANIFEST.in")
        if os.path.exists(manifest):
            with open(manifest) as f:
                assert "lopper/audit/drc" in f.read(), \
                    "drc/*.yaml must be in MANIFEST.in or it will not ship"

    def test_load_yaml_file_adds_rules(self, tmp_path):
        reset_drc_registry()
        reg = get_drc_registry()
        before = len(reg.all_rules())
        p = tmp_path / "extra.yaml"
        p.write_text(
            "drc:\n"
            "  - id: EXTRA-1\n"
            "    check: required\n"
            "    select: ['/domains/.*']\n"
            "    params: {properties: ['zzz']}\n"
            "    message: extra\n")
        n = reg.load_yaml_file(str(p))
        assert n == 1
        assert len(reg.all_rules()) == before + 1
        assert any(r.id == "EXTRA-1" for r in reg.all_rules())
        reset_drc_registry()

    def test_load_dir_adds_rules(self, tmp_path):
        reset_drc_registry()
        reg = get_drc_registry()
        (tmp_path / "a.yaml").write_text(
            "drc:\n  - id: DIR-A\n    check: required\n"
            "    params: {properties: ['q']}\n    message: a\n")
        (tmp_path / "b.yaml").write_text(
            "drc:\n  - id: DIR-B\n    check: required\n"
            "    params: {properties: ['q']}\n    message: b\n")
        assert reg.load_dir(str(tmp_path)) == 2
        ids = {r.id for r in reg.all_rules()}
        assert {"DIR-A", "DIR-B"} <= ids
        reset_drc_registry()

    def test_collect_from_tree_reads_in_tree_assertions(self):
        # rules carried in the SDT itself, under /__assertions__
        reset_drc_registry()
        reg = get_drc_registry()
        tree = _tree_with_domains()
        anchor = LopperNode(-1, "/__assertions__")
        tree.add(anchor)
        r = LopperNode(-1, "/__assertions__/rule_0")
        r + LopperProp(name="id", value=["IN-TREE-1"])
        r + LopperProp(name="check", value=["required"])
        r + LopperProp(name="select", value=["/domains/.*"])
        r + LopperProp(name="message", value=["in tree"])
        tree.add(r)
        tree.sync()

        n = reg.collect_from_tree(tree)
        assert n == 1
        assert any(x.id == "IN-TREE-1" for x in reg.all_rules())
        reset_drc_registry()


class TestEndToEnd:
    def test_run_phase_via_registry(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", id_=1, os_type="linux", access=[100, 0])
        _domain(tree, "/domains/d2", id_=1, os_type="bad", access=[100, 0])  # dup id, dev 100 in both (not shared)
        tree.sync()
        errs = A.run_audit_phase(ValidationPhase.POST_PROCESSING, tree,
                                 warnings=["drc_all"], werror=True)
        # DOM-006 (dup id) + DOM-034 (unshared device in two domains) both fire
        assert errs >= 2
