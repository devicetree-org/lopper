"""Tests for Zephyr physical-memory policy resolution."""

# Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import pytest

from lopper.assists.yaml_to_dts_expansion import memory_expand
from lopper.assists.zephyr_memory import (
    LayoutError,
    _domain_memory_nodes,
    _normalized_memories,
    resolve_memory_node,
)
from lopper.tree import LopperNode, LopperTree


def test_yaml_sram_device_references_expand_to_phandles():
    """Explicit SRAM devices retain identity across YAML expansion."""
    tree = LopperTree()
    tree + LopperNode(-1, "/axi")
    tree + LopperNode(-1, "/domains")
    bank = LopperNode(-1, "/axi/psv_r5_0_atcm@0")
    bank.label = "psv_r5_0_atcm"
    bank["reg"] = [0, 0, 0, 0x10000]
    tree + bank
    domain = LopperNode(-1, "/domains/R5_0_ZEPHYR")
    domain["sram"] = [{
        "dev": "psv_r5_0_atcm@0",
        "start": 0,
        "size": 0x10000,
    }]
    tree + domain
    tree.sync()

    memory_expand(tree, domain, prop_name="sram")

    assert domain.propval("sram", list) == [bank.phandle]


def test_yaml_sram_device_requires_registers():
    """Synthetic SRAM nodes without registers are rejected immediately."""
    tree = LopperTree()
    tree + LopperNode(-1, "/axi")
    tree + LopperNode(-1, "/domains")
    bank = LopperNode(-1, "/axi/psv_r5_0_atcm")
    bank.label = "psv_r5_0_atcm"
    tree + bank
    domain = LopperNode(-1, "/domains/R5_0_ZEPHYR")
    domain["sram"] = [{
        "dev": "psv_r5_0_atcm",
        "start": 0,
        "size": 0x10000,
    }]
    tree + domain
    tree.sync()

    with pytest.raises(LayoutError, match="missing required 'reg'"):
        memory_expand(tree, domain, prop_name="sram")


def test_canonical_memory_name_resolves_existing_node():
    """A stable SDT name can alias an implementation-derived TCM node."""
    tree = LopperTree()
    tree + LopperNode(-1, "/axi")
    bank = LopperNode(
        -1, "/axi/ps_wizard_0_pmcps_0_psv_r5_0_atcm@0")
    bank["reg"] = [0, 0, 0, 0x10000]
    bank["xlnx,name"] = ["psv_r5_0_atcm"]
    tree + bank
    tree.sync()

    assert resolve_memory_node(tree, "psv_r5_0_atcm") is bank


def test_split_r5_local_tcm_range_uses_domain_core():
    """Identical split-core local ranges resolve through the domain CPU."""
    tree = LopperTree()
    root = tree["/"]
    root["#address-cells"] = [2]
    root["#size-cells"] = [2]
    axi = LopperNode(-1, "/axi")
    tree + axi
    axi["#address-cells"] = [2]
    axi["#size-cells"] = [2]
    tree + LopperNode(-1, "/domains")
    cluster = LopperNode(-1, "/cpus-r5@0")
    cluster.label = "cpus_r5_0"
    cluster["compatible"] = ["cpus,cluster", "cortex-r5"]
    tree + cluster
    cluster.phandle_or_create()
    banks = []
    for core in (0, 1):
        bank = LopperNode(-1, f"/axi/psu_r5_{core}_atcm@0")
        bank.label = f"psu_r5_{core}_atcm"
        bank["reg"] = [0, 0, 0, 0x10000]
        tree + bank
        banks.append(bank)
    domain = LopperNode(-1, "/domains/R5_0_ZEPHYR")
    tree + domain
    domain["cpus"] = [cluster.phandle, 0, 0]
    domain["sram"] = [0, 0, 0, 0x10000]
    tree.sync()

    assert _domain_memory_nodes(tree, domain) == (banks[0],)


def test_standalone_yaml_three_cell_sram_range():
    """Standalone YAML address-high/address-low/size tuples are accepted."""
    tree = LopperTree()
    root = tree["/"]
    root["#address-cells"] = [2]
    root["#size-cells"] = [1]
    axi = LopperNode(-1, "/axi")
    tree + axi
    axi["#address-cells"] = [2]
    axi["#size-cells"] = [1]
    tree + LopperNode(-1, "/domains")
    bank = LopperNode(-1, "/axi/psu_r5_0_atcm@0")
    bank["reg"] = [0, 0, 0x10000]
    tree + bank
    domain = LopperNode(-1, "/domains/R5_0_ZEPHYR")
    tree + domain
    domain["sram"] = [0, 0, 0x10000]
    tree.sync()

    assert _domain_memory_nodes(tree, domain) == (bank,)


def test_linker_name_retains_unit_address_without_label():
    """Anonymous memory nodes with a common base name remain distinct."""
    tree = LopperTree()
    axi = LopperNode(-1, "/axi")
    tree + axi
    axi["#address-cells"] = [1]
    axi["#size-cells"] = [1]
    first = LopperNode(-1, "/axi/sram@0")
    second = LopperNode(-1, "/axi/sram@20000")
    tree + first
    tree + second
    for node, origin in ((first, 0), (second, 0x20000)):
        node["reg"] = [origin, 0x10000]
        node["mpu-policy"] = ["readable", "writable", "cacheable"]

    memories = _normalized_memories((first, second), "cortexr5")

    assert tuple(memory.name for memory in memories) == ("SRAM_0", "SRAM_20000")
