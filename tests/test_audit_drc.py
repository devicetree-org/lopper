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
    (DRC-DOM-006 unique id, DRC-DOM-034 device exclusivity, DRC-DOM-012 CPU core)
  - severity handling (block -> hard stop; error count)
  - per-id flag filtering
  - end-to-end via ValidatorRegistry.run_phase
"""

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
        for expect in ("DRC-DOM-003", "DRC-DOM-004", "DRC-DOM-005",
                       "DRC-DOM-006", "DRC-DOM-007", "DRC-DOM-012", "DRC-DOM-034"):
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
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", id_=1)          # ok
        _domain(tree, "/domains/d2")                 # missing id
        tree.sync()
        v = _run(["DRC-DOM-004"], tree, ValidationPhase.POST_YAML)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert fails[0].source_path == "/domains/d2"

    def test_enum_bad_os_type_fails(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", os_type="linux")     # ok
        _domain(tree, "/domains/d2", os_type="plan9")     # bad
        tree.sync()
        v = _run(["DRC-DOM-005"], tree, ValidationPhase.POST_YAML)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert fails[0].source_path == "/domains/d2"

    def test_compatible_contains_fails(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1")                       # has token
        _domain(tree, "/domains/d2", compat="acme,thing")  # missing token
        tree.sync()
        v = _run(["DRC-DOM-003"], tree, ValidationPhase.POST_YAML)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert fails[0].source_path == "/domains/d2"

    def test_ref_exists_bad_parent_fails(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/parent_dom", id_=1)
        _domain(tree, "/domains/d2", id_=2, parent="/domains/parent_dom")  # ok
        _domain(tree, "/domains/d3", id_=3, parent="/domains/ghost")       # dangling
        tree.sync()
        v = _run(["DRC-DOM-007"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert fails[0].source_path == "/domains/d3"

    def test_clean_tree_passes(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", id_=1, os_type="linux")
        tree.sync()
        v = _run(["DRC-DOM-003", "DRC-DOM-004", "DRC-DOM-005"],
                 tree, ValidationPhase.POST_YAML)
        assert [r for r in v.results if not r.passed] == []


# --------------------------------------------------------------------------
# Relational handlers
# --------------------------------------------------------------------------

class TestRelationalChecks:
    def test_unique_id_conflict(self):
        # DRC-DOM-006: two domains share id 5
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", id_=5)
        _domain(tree, "/domains/d2", id_=5)
        _domain(tree, "/domains/d3", id_=6)
        tree.sync()
        v = _run(["DRC-DOM-006"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1

    def test_device_exclusivity_conflict(self):
        # DRC-DOM-034: device 100 in two domains' access lists
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", access=[100, 101])
        _domain(tree, "/domains/d2", access=[100, 102])
        tree.sync()
        v = _run(["DRC-DOM-034"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1
        assert "100" in fails[0].message

    def test_device_exclusivity_clean(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", access=[100, 101])
        _domain(tree, "/domains/d2", access=[102, 103])
        tree.sync()
        v = _run(["DRC-DOM-034"], tree, ValidationPhase.POST_PROCESSING)
        assert [r for r in v.results if not r.passed] == []

    def test_cpu_core_conflict(self):
        # DRC-DOM-012: cluster 1 core 0 claimed by both domains
        # cpus triplet = (cluster, cpumask, el)
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", cpus=[1, 0x3, 1])  # cores 0,1
        _domain(tree, "/domains/d2", cpus=[1, 0x1, 1])  # core 0  -> conflict
        tree.sync()
        v = _run(["DRC-DOM-012"], tree, ValidationPhase.POST_PROCESSING)
        fails = [r for r in v.results if not r.passed]
        assert len(fails) == 1

    def test_cpu_core_clean_different_clusters(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", cpus=[1, 0x3, 1])
        _domain(tree, "/domains/d2", cpus=[2, 0x3, 1])  # different cluster
        tree.sync()
        v = _run(["DRC-DOM-012"], tree, ValidationPhase.POST_PROCESSING)
        assert [r for r in v.results if not r.passed] == []


# --------------------------------------------------------------------------
# Severity + flag filtering
# --------------------------------------------------------------------------

class TestSeverityAndFlags:
    def test_block_severity_exits(self):
        reset_drc_registry()
        reg = get_drc_registry()
        reg.add_rule(Rule.from_dict({
            "id": "DRC-TEST-BLOCK", "check": "required", "phase": "post-yaml",
            "severity": "block", "select": ["/domains/.*"],
            "params": {"properties": ["id"]},
            "message": "blocking: id required",
        }))
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1")  # missing id -> blocking fail
        tree.sync()
        v = DRCValidator(warnings=["drc:DRC-TEST-BLOCK"])
        v.run_phase(ValidationPhase.POST_YAML, tree)
        with pytest.raises(SystemExit):
            v.report()
        reset_drc_registry()

    def test_error_severity_counts(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1")  # missing id
        tree.sync()
        v = _run(["DRC-DOM-004"], tree, ValidationPhase.POST_YAML, werror=True)
        assert v.report() == 1

    def test_id_filter_runs_only_named_rule(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", compat="acme,x")  # violates DOM-003 only
        tree.sync()
        # enable only DOM-004 (required id) -> DOM-003 (compat) must NOT run
        v = _run(["DRC-DOM-004"], tree, ValidationPhase.POST_YAML)
        ids = {(r.details or {}).get("drc_id") for r in v.results if not r.passed}
        assert "DRC-DOM-003" not in ids

    def test_disabled_when_no_drc_flag(self):
        v = DRCValidator(warnings=["memory_all"])
        assert v.is_enabled() is False

    def test_enabled_with_drc_all(self):
        v = DRCValidator(warnings=["drc_all"])
        assert v.is_enabled() is True


# --------------------------------------------------------------------------
# End-to-end through the registry
# --------------------------------------------------------------------------

class TestEndToEnd:
    def test_run_phase_via_registry(self):
        tree = _tree_with_domains()
        _domain(tree, "/domains/d1", id_=1, os_type="linux", access=[100])
        _domain(tree, "/domains/d2", id_=1, os_type="bad", access=[100])  # dup id, bad os, shared dev
        tree.sync()
        errs = A.run_audit_phase(ValidationPhase.POST_PROCESSING, tree,
                                 warnings=["drc_all"], werror=True)
        # DOM-006 (dup id) + DOM-034 (shared device) both fire here
        assert errs >= 2
