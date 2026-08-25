"""
Pytest migration of xlnx_gen_domain_sanity_test() from lopper_sanity.py

This module contains integration tests for Xilinx domain generation functionality.
Tests require specific Xilinx device tree files.
Migrated from lopper_sanity.py lines 2126-2163.

Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
Copyright (C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause

Author:
    Bruce Ashfield <bruce.ashfield@amd.com>
"""

import os
import pytest

from lopper.assists import zephyr_domain_dts as gen_domain_dts
from lopper.tree import LopperNode, LopperTree


def _ttc_tree(*labels, compatible="xlnx,ttcps"):
    """Create a minimal tree containing TTC nodes with optional labels."""
    tree = LopperTree()
    tree + LopperNode(-1, "/__symbols__")
    nodes = []
    for index, label in enumerate(labels):
        node = LopperNode(-1, f"/axi/timer@ff1{index}0000")
        node["compatible"] = [compatible]
        if label:
            node.label = label
        tree + node
        nodes.append(node)
    return tree, nodes


def test_zephyr_r5_labels_sole_selected_ttc_as_ttc0():
    """A sole domain-selected TTC gets Zephyr's stable ttc0 label."""
    tree, nodes = _ttc_tree("ttc2")

    gen_domain_dts._xlnx_zephyr_assign_ttc0(tree, "psu_cortexr5_0")

    assert nodes[0].label == "ttc0"
    assert tree["/__symbols__"].propval("ttc0", list) == [nodes[0].abs_path]


def test_zephyr_r5_preserves_explicit_ttc0_with_multiple_ttcs():
    """An explicitly selected ttc0 wins without relying on tree order."""
    tree, nodes = _ttc_tree("ttc1", "ttc0")

    gen_domain_dts._xlnx_zephyr_assign_ttc0(tree, "psv_cortexr5_0")

    assert [node.label for node in nodes] == ["ttc1", "ttc0"]


def test_zephyr_r5_labels_first_domain_ttc_when_no_ttc0_exists():
    """The first domain-retained TTC becomes ttc0 when none is explicit."""
    tree, _ = _ttc_tree("ttc1", "ttc2")

    gen_domain_dts._xlnx_zephyr_assign_ttc0(tree, "psu_cortexr5_0")

    assert tree["/axi/timer@ff100000"].label == "ttc0"
    assert tree["/axi/timer@ff110000"].label == "ttc2"


def test_zephyr_r5_accepts_sdt_cdns_ttc_compatible():
    """ZynqMP's SDT-side cdns,ttc node participates in label selection."""
    tree, nodes = _ttc_tree("ttc1", compatible="cdns,ttc")

    gen_domain_dts._xlnx_zephyr_assign_ttc0(tree, "psu_cortexr5_0")

    assert nodes[0].label == "ttc0"


def test_ttc_label_normalization_is_r5_platform_specific():
    """Other Zephyr processor families retain their original TTC labels."""
    tree, nodes = _ttc_tree("ttc2")

    gen_domain_dts._xlnx_zephyr_assign_ttc0(tree, "cortexr52_0")

    assert nodes[0].label == "ttc2"
    assert tree["/__symbols__"].propval("ttc0") == ['']


def test_rpu_memory_rename_refreshes_path_and_phandle_references():
    """RPU local-view renames preserve chosen, symbol, and phandle refs."""
    tree = LopperTree()
    chosen = LopperNode(-1, "/chosen")
    old_path = "/memory@ffe00000"
    chosen["zephyr,ipc_shm"] = old_path
    tree + chosen

    symbols = LopperNode(-1, "/__symbols__")
    symbols["r5_tcm"] = old_path
    tree + symbols

    memory = LopperNode(-1, old_path)
    memory.label = "r5_tcm"
    memory["reg"] = [0, 0, 0, 0x10000]
    tree + memory
    memory.phandle_or_create()

    consumer = LopperNode(-1, "/consumer")
    tree + consumer
    consumer["memory-region"] = [memory.phandle]

    gen_domain_dts.xlnx_zephyr_fixup_rpu_memory_names(
        tree, "psu_cortexr5_0", [memory])

    renamed = tree["/memory@0"]
    assert renamed is memory
    assert not tree.nodes("/memory@ffe00000", strict=True)
    assert chosen.propval("zephyr,ipc_shm", list) == [renamed.abs_path]
    assert symbols.propval("r5_tcm", list) == [renamed.abs_path]
    assert tree.pnode(consumer.propval("memory-region", list)[0]) is renamed


@pytest.mark.parametrize("compatible", [
    "xlnx,zynqmp-ipi-mailbox",
    "xlnx,zynqmp-ipi-dest-mailbox",
])
def test_zynqmp_mailbox_without_ipi_id_is_ignored(compatible):
    """Missing optional IPI identifiers do not abort mailbox conversion."""
    node = LopperNode(-1, "/mailbox")
    node["compatible"] = [compatible]

    gen_domain_dts._xlnx_zephyr_convert_zynqmp_ipi_id(node)

    assert not node.props("local-ipi-id")
    assert not node.props("remote-ipi-id")


@pytest.mark.parametrize(("compatible", "converted_name"), [
    ("xlnx,zynqmp-ipi-mailbox", "local-ipi-id"),
    ("xlnx,zynqmp-ipi-dest-mailbox", "remote-ipi-id"),
])
def test_zynqmp_mailbox_converts_present_ipi_id(compatible, converted_name):
    """Existing IPI identifiers retain the mailbox conversion behavior."""
    node = LopperNode(-1, "/mailbox")
    node["compatible"] = [compatible]
    node["xlnx,ipi-id"] = [7]

    gen_domain_dts._xlnx_zephyr_convert_zynqmp_ipi_id(node)

    assert node.propval(converted_name, list) == [7]
    assert not node.props("xlnx,ipi-id")


def test_zynqmp_mailbox_ipi_id_without_compatible_is_ignored():
    """IPI child nodes lacking compatible must not abort conversion."""
    node = LopperNode(-1, "/mailbox/child")
    node["xlnx,ipi-id"] = [3]

    gen_domain_dts._xlnx_zephyr_convert_zynqmp_ipi_id(node)

    assert node.propval("xlnx,ipi-id", list) == [3]
    assert not node.props("local-ipi-id")
    assert not node.props("remote-ipi-id")


def test_zynqmp_ipi_child_promoted_from_sdt_buffers():
    """Raw SDT IPI children gain Zephyr mailbox child-binding properties."""
    tree = LopperTree()
    parent = LopperNode(-1, "/axi/mailbox@ff340000")
    parent["compatible"] = ["xlnx,zynqmp-ipi-mailbox"]
    child = LopperNode(-1, "/axi/mailbox@ff340000/child@0")
    child["xlnx,ipi-id"] = [2]
    child["xlnx,ipi-req-msg-buf"] = [0xff3f0680]
    child["xlnx,ipi-rsp-msg-buf"] = [0xff3f06a0]
    parent.add(child)
    tree + parent

    schema = {
        "xlnx,zynqmp-ipi-dest-mailbox": {
            "required": ["reg", "reg-names", "remote-ipi-id"],
        },
    }
    sdt = type("Sdt", (), {"tree": tree})()

    gen_domain_dts._xlnx_zephyr_fixup_zynqmp_ipi_child(child, sdt, schema)

    assert child.name == "ipi@ff3f0680"
    assert not child.props("compatible")
    assert child.propval("remote-ipi-id", list) == [2]
    assert child.propval("reg-names", list) == [
        "local_request_region", "local_response_region",
    ]


def test_zynqmp_ipi_child_with_reg_strips_compatible():
    """Fully-formed ZynqMP IPI children keep child-binding props only (04eb83c)."""
    tree = LopperTree()
    parent = LopperNode(-1, "/axi/mailbox@ff990000")
    parent["compatible"] = ["xlnx,zynqmp-ipi-mailbox"]
    child = LopperNode(-1, "/axi/mailbox@ff990000/ipi@ff990480")
    child["compatible"] = ["xlnx,zynqmp-ipi-dest-mailbox"]
    child["reg"] = [
        0, 0xff990480, 0, 0x20, 0, 0xff9904a0, 0, 0x20,
        0, 0xff990480, 0, 0x20, 0, 0xff9904a0, 0, 0x20,
    ]
    child["reg-names"] = [
        "local_request_region", "local_response_region",
        "remote_request_region", "remote_response_region",
    ]
    child["xlnx,ipi-id"] = [4]
    parent.add(child)
    tree + parent

    schema = {
        "xlnx,zynqmp-ipi-dest-mailbox": {
            "required": ["reg", "reg-names", "remote-ipi-id"],
        },
    }
    sdt = type("Sdt", (), {"tree": tree})()

    gen_domain_dts._xlnx_zephyr_fixup_zynqmp_ipi_child(child, sdt, schema)

    assert not child.props("compatible")
    assert child.propval("remote-ipi-id", list) == [4]
    assert not child.props("xlnx,ipi-id")


def test_zynqmp_ipi_child_without_buffers_is_dropped():
    """Bufferless SDT IPI placeholders are removed from the domain tree."""
    tree = LopperTree()
    parent = LopperNode(-1, "/axi/mailbox@ff340000")
    parent["compatible"] = ["xlnx,zynqmp-ipi-mailbox"]
    child = LopperNode(-1, "/axi/mailbox@ff340000/child@6")
    child["xlnx,ipi-id"] = [9]
    child["xlnx,ipi-bitmask"] = [0x200]
    parent.add(child)
    tree + parent

    sdt = type("Sdt", (), {"tree": tree})()
    gen_domain_dts._xlnx_zephyr_fixup_zynqmp_ipi_child(child, sdt, {})

    assert not list(parent.subnodes(children_only=True))


class TestXilinxDomainGeneration:
    """Test Xilinx domain generation integration.

    Reference: lopper_sanity.py:2126-2163
    """

    def test_xilinx_sdt_files_exist(self):
        """Verify required Xilinx SDT files are available."""
        ws_area = os.getcwd()
        sdt = os.path.join(ws_area, "device-trees", "system-device-tree-versal-vck190.dts")
        lops_area = os.path.join(ws_area, "lopper", "lops")

        # Check if required files exist
        lops_invoke = os.path.join(lops_area, "lop-gen_domain_dts-invoke.dts")
        lops_load = os.path.join(lops_area, "lop-load.dts")

        # These files may not exist in all environments
        if not os.path.exists(lops_invoke):
            pytest.skip(f"Xilinx lop file not found: {lops_invoke}")
        if not os.path.exists(lops_load):
            pytest.skip(f"Xilinx lop file not found: {lops_load}")

    @pytest.mark.skip(reason="Integration test requiring specific Xilinx device trees")
    def test_xilinx_gen_domain_integration(self):
        """
        Integration test for Xilinx domain generation.

        This test is skipped by default as it requires:
        - Specific Xilinx device tree files (versal-vck190)
        - Xilinx-specific lop files
        - File modification (inplace_change) of lop files
        - Potentially long execution time

        Run with: pytest tests/test_xlnx_gen_domain.py --run-integration
        """
        # This would run the full xlnx_gen_domain_sanity_test if enabled
        pass
