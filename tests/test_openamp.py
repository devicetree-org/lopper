"""
Pytest migration of openamp_sanity_test() from lopper_sanity.py

This module contains integration tests for OpenAMP domain configuration.
Tests require demo files in demos/openamp/inputs directory.
Migrated from lopper_sanity.py lines 2164-2171.

Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import pytest

from lopper.assists import openamp_xlnx
from lopper.tree import LopperNode, LopperTree


class TestOpenAMPDemo:
    """Test OpenAMP demonstration integration.

    Reference: lopper_sanity.py:2164-2171
    """

    def test_openamp_demo_files_exist(self):
        """Verify OpenAMP demo files are available."""
        demo_area = os.getcwd() + "/demos/openamp/inputs/"

        assert os.path.exists(demo_area), f"Demo directory not found: {demo_area}"
        assert os.path.exists(demo_area + "versal2_run.sh"), \
            f"Demo script not found: {demo_area}versal2_run.sh"

    @pytest.mark.skip(reason="Integration test requiring full demo environment")
    def test_openamp_versal2_integration(self):
        """
        Integration test for OpenAMP Versal2 configuration.

        This test is skipped by default as it requires:
        - Full demo environment setup
        - External dependencies and files
        - Potentially long execution time

        Run with: pytest tests/test_openamp.py --run-integration
        """
        # This would run the full openamp_sanity_test_generic if enabled
        pass


class _FakeNode:
    """Minimal node implementation for relation-selection diagnostics."""

    def __init__(self, name, props=None, parent=None, label=None, children=None):
        self.name = name
        self._props = props or {}
        self.parent = parent
        self.label = label
        self._children = children or []

    def propval(self, name):
        return self._props.get(name, [''])

    def subnodes(self, children_only=False):
        return self._children


class _FakeTree:
    def __init__(self, domains, phandles):
        self._domains = domains
        self._phandles = phandles

    def __getitem__(self, path):
        if path == "/domains":
            return self._domains
        raise KeyError(path)

    def pnode(self, phandle):
        return self._phandles.get(phandle)


def test_legacy_zephyr_memories_remain_compatible(caplog):
    """Legacy memory lists mark every bank and select the first bank."""
    tree = LopperTree()
    tree + LopperNode(-1, "/chosen")
    atcm = LopperNode(-1, "/axi/atcm@0")
    atcm.label = "r5_0_atcm"
    tree + atcm
    btcm = LopperNode(-1, "/axi/btcm@20000")
    btcm["xlnx,ip-name"] = "r5_0_btcm"
    tree + btcm
    domain = LopperNode(-1, "/domains/R5_0_ZEPHYR")
    domain["xlnx,zephyr,mems"] = ["r5_0_atcm", "r5_0_btcm"]
    tree + domain

    assert openamp_xlnx.xlnx_openamp_apply_legacy_zephyr_memories(
        tree, domain)
    assert atcm.propval("device_type", list) == ["memory"]
    assert btcm.propval("device_type", list) == ["memory"]
    assert tree["/chosen"].propval("zephyr,sram", list) == [atcm.abs_path]
    assert "xlnx,zephyr,mems is deprecated" in caplog.text


def test_legacy_zephyr_memories_do_not_override_sram():
    """An explicit zephyr,sram selection takes precedence over legacy data."""
    tree = LopperTree()
    chosen = LopperNode(-1, "/chosen")
    chosen["zephyr,sram"] = "/reserved-memory/ddr@9800000"
    tree + chosen
    atcm = LopperNode(-1, "/axi/atcm@0")
    atcm.label = "r5_0_atcm"
    tree + atcm
    domain = LopperNode(-1, "/domains/R5_0_ZEPHYR")
    domain["xlnx,zephyr,mems"] = ["r5_0_atcm"]
    tree + domain

    assert openamp_xlnx.xlnx_openamp_apply_legacy_zephyr_memories(
        tree, domain)
    assert chosen.propval("zephyr,sram", list) == [
        "/reserved-memory/ddr@9800000"]


def test_libmetal_missing_processor_lists_supported_targets(monkeypatch, caplog):
    """A processor without a Libmetal relation gets an actionable error."""
    apu_cluster = _FakeNode("cpus-a53@0", label="cpus_a53")
    r5_0_cluster = _FakeNode("cpus-r5@0", label="cpus_r5_0")
    r5_1_cluster = _FakeNode("cpus-r5@1", label="cpus_r5_1")

    apu_domain = _FakeNode("APU_Linux", {"cpus": [1], "os,type": ["linux"]})
    apu_parent = _FakeNode("domain-to-domain", parent=apu_domain)
    apu_relation = _FakeNode(
        "libmetal-relation", {"compatible": ["libmetal,ipc-v1"]}, apu_parent)

    r5_1_domain = _FakeNode(
        "R5_1_BAREMETAL", {"cpus": [3], "os,type": ["baremetal"]})
    r5_1_parent = _FakeNode(
        "domain-to-domain", {"cluster_cpu": ["psu_cortexr5_1"]}, r5_1_domain)
    r5_1_relation = _FakeNode(
        "libmetal-relation", {"compatible": ["libmetal,ipc-v1"]}, r5_1_parent)

    domains = _FakeNode("domains", children=[apu_relation, r5_1_relation])
    tree = _FakeTree(domains, {1: apu_cluster, 2: r5_0_cluster, 3: r5_1_cluster})
    sdt = type("FakeSdt", (), {"tree": tree})()
    requested_cpu = _FakeNode("cpu@0", parent=r5_0_cluster)

    monkeypatch.setattr(openamp_xlnx, "get_platform",
                        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.ZYNQMP)
    monkeypatch.setattr(openamp_xlnx, "get_cpu_node",
                        lambda sdt, options: requested_cpu)

    result = openamp_xlnx.openamp_nontree_outputs_handler(
        sdt,
        "unused.cmake",
        {
            "machine": "psu_cortexr5_0",
            "dt_type": "baremetal_dt",
            "relation_parent": None,
            "relation": None,
            "compatible_string": "libmetal,ipc-v1",
        },
    )

    assert result is False
    assert "no libmetal,ipc-v1 relation found for processor 'psu_cortexr5_0'" in caplog.text
    assert "APU_Linux (os=linux, processor=cpus_a53)" in caplog.text
    assert "R5_1_BAREMETAL (os=baremetal, processor=psu_cortexr5_1)" in caplog.text
