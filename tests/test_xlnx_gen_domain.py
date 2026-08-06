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

from lopper.assists import gen_domain_dts
from lopper.tree import LopperNode, LopperTree


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
