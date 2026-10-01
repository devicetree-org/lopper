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
from pathlib import Path

import pytest
import yaml

from lopper.assists import (
    lopper_lib,
    openamp_xlnx,
    openamp_xlnx_common,
    xlnx_rpu_tcm,
    yaml_to_dts_expansion,
)
from lopper.tree import LopperNode, LopperTree


REPO_ROOT = Path(__file__).resolve().parent.parent
ZYNQMP_OPENAMP_YAML = (
    REPO_ROOT / "demos" / "openamp" / "inputs" /
    "openamp-overlay-zynqmp.yaml"
)


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
    def __init__(self, domains, phandles, symbols=None):
        self._domains = domains
        self._phandles = phandles
        self._symbols = symbols

    def __getitem__(self, path):
        if path == "/domains":
            return self._domains
        if path == "/__symbols__" and self._symbols is not None:
            return self._symbols
        raise KeyError(path)

    def pnode(self, phandle):
        return self._phandles.get(phandle)


def _remoteproc_v2_fixture(
        pd_id=0x44, legacy_pd=None,
        node_name="r52_0a_atcm_global@eba00000", reg_size=0x10000,
        with_reg=True):
    """Build the minimum channel data needed for R52 TCM construction."""
    tree = LopperTree()
    axi = LopperNode(-1, "/axi")
    axi["#address-cells"] = [2]
    axi["#size-cells"] = [2]
    tree + axi
    tcm = LopperNode(-1, f"/axi/{node_name}")
    tcm["xlnx,ip-name"] = ["r52_0a_atcm_global"]
    if with_reg:
        base = int(node_name.split("@")[1], 16)
        tcm["reg"] = (lopper_lib.int_to_cells(base, 2) +
                      lopper_lib.int_to_cells(reg_size, 2))
    if pd_id is not None:
        tcm["power-domains"] = [0xA5, pd_id]
    if legacy_pd is not None:
        tcm["xlnx,power-domain"] = [legacy_pd]
    tree + tcm

    pd_property = type("PowerDomainProperty", (), {"value": [0xA5, 0]})()
    channel_info = {
        "cpu_config": openamp_xlnx.CPU_CONFIG.RPU_SPLIT,
        "remote_node": object(),
        "rpu_core_pd_prop": pd_property,
    }
    return channel_info, tcm


def _cpu_selection_fixture(mask, cpu_count=1, first_reg=0):
    cpus = [
        _FakeNode(
            f"cpu@{first_reg + index:x}",
            {"reg": [first_reg + index]},
            label=f"cpu_{first_reg + index}",
        )
        for index in range(cpu_count)
    ]
    cluster = _FakeNode("cpus-r5@0", label="cpus_r5", children=cpus)
    for cpu in cpus:
        cpu.parent = cluster
    domain = _FakeNode("RPU", {"cpus": [1, mask, 0]})
    tree = _FakeTree(_FakeNode("domains"), {1: cluster})
    return tree, domain, cpus


@pytest.mark.parametrize(
    "mask, expected",
    [(0x1, [0]), (0x2, [1]), (0x3, [0, 1])],
)
def test_domain_cpu_resolver_uses_cluster_relative_masks(mask, expected):
    tree, domain, cpus = _cpu_selection_fixture(mask, cpu_count=2)

    selection = lopper_lib.resolve_domain_cpus(tree, domain)

    assert selection.source == lopper_lib.CpuSelectionSource.CLUSTER_RELATIVE
    assert selection.cpus == [cpus[index] for index in expected]


def test_domain_cpu_resolver_accepts_legacy_label_for_split_cluster():
    tree, domain, cpus = _cpu_selection_fixture(0x2, first_reg=1)

    selection = lopper_lib.resolve_domain_cpus(tree, domain, "cpu_1")

    assert selection.source == lopper_lib.CpuSelectionSource.LEGACY_CLUSTER_CPU
    assert selection.cpus == cpus
    assert "use 0x1" not in selection.diagnostic


def test_domain_cpu_resolver_accepts_unambiguous_legacy_core_mask():
    tree, domain, cpus = _cpu_selection_fixture(0x2, first_reg=1)

    selection = lopper_lib.resolve_domain_cpus(tree, domain)

    assert selection.source == lopper_lib.CpuSelectionSource.LEGACY_CORE_INDEX
    assert selection.cpus == cpus
    assert "use 0x1" in selection.diagnostic


@pytest.mark.parametrize("mask", [0, 0x4])
def test_domain_cpu_resolver_rejects_ambiguous_masks(mask):
    tree, domain, _ = _cpu_selection_fixture(mask, first_reg=1)

    selection = lopper_lib.resolve_domain_cpus(tree, domain)

    assert selection.source == lopper_lib.CpuSelectionSource.UNRESOLVED
    assert selection.cpus == []


def test_domain_cpu_resolver_uses_legacy_label_for_zero_mask():
    tree, domain, cpus = _cpu_selection_fixture(0, first_reg=1)

    selection = lopper_lib.resolve_domain_cpus(tree, domain, "cpu_1")

    assert selection.source == lopper_lib.CpuSelectionSource.LEGACY_CLUSTER_CPU
    assert selection.cpus == cpus


def test_domain_cpu_resolver_rejects_missing_legacy_label_sentinel():
    tree, domain, cpus = _cpu_selection_fixture(0, first_reg=1)
    cpus[0].label = ""

    selection = lopper_lib.resolve_domain_cpus(tree, domain, [""])

    assert selection.source == lopper_lib.CpuSelectionSource.UNRESOLVED
    assert selection.cpus == []


def test_domain_cpu_resolver_resolves_legacy_symbol_for_zero_mask():
    tree, domain, cpus = _cpu_selection_fixture(0, first_reg=1)
    cpus[0].label = None
    cpus[0].abs_path = "/cpus-r5@0/cpu@1"
    tree._symbols = _FakeNode(
        "__symbols__", {"psu_cortexr5_1": [cpus[0].abs_path]})

    selection = lopper_lib.resolve_domain_cpus(
        tree, domain, "psu_cortexr5_1")

    assert selection.source == lopper_lib.CpuSelectionSource.LEGACY_CLUSTER_CPU
    assert selection.cpus == cpus


def test_openamp_legacy_processor_falls_back_to_referenced_cluster():
    tree, domain, cpus = _cpu_selection_fixture(0, first_reg=1)
    cpus[0].label = "psx_cortexr52_1"
    dtd = _FakeNode(
        "domain-to-domain", {"cluster_cpu": ["cortexr52_1"]})
    domain._children = [dtd]

    assert openamp_xlnx_common._openamp_domain_selects_cpu(
        tree, domain, cpus[0])

    other_cpu = _FakeNode("cpu@1", parent=_FakeNode("cpus-r52@0"))
    assert not openamp_xlnx_common._openamp_domain_selects_cpu(
        tree, domain, other_cpu)


def test_openamp_legacy_processor_does_not_widen_within_cluster():
    tree, domain, cpus = _cpu_selection_fixture(0x4, cpu_count=2)
    cpus[0].label = "psx_cortexr52_0"
    cpus[1].label = "psx_cortexr52_1"
    dtd = _FakeNode(
        "domain-to-domain", {"cluster_cpu": ["cortexr52_1"]})
    domain._children = [dtd]

    assert not openamp_xlnx_common._openamp_domain_selects_cpu(
        tree, domain, cpus[0])
    assert openamp_xlnx_common._openamp_domain_selects_cpu(
        tree, domain, cpus[1])


def test_domain_cpu_resolver_prefers_valid_mask_over_legacy_label():
    tree, domain, cpus = _cpu_selection_fixture(0x2, cpu_count=2)

    selection = lopper_lib.resolve_domain_cpus(tree, domain, "cpu_0")

    assert selection.source == lopper_lib.CpuSelectionSource.CLUSTER_RELATIVE
    assert selection.cpus == [cpus[1]]
    assert "disagrees" in selection.diagnostic


def test_domain_cpu_resolver_rejects_unknown_cluster():
    domain = _FakeNode("RPU", {"cpus": [99, 0x1, 0]})
    tree = _FakeTree(_FakeNode("domains"), {})

    selection = lopper_lib.resolve_domain_cpus(tree, domain)

    assert selection.source == lopper_lib.CpuSelectionSource.UNRESOLVED
    assert selection.cpus == []
    assert "unknown cluster" in selection.diagnostic


def test_domain_cpu_resolver_rejects_cluster_without_cpus():
    cluster = _FakeNode("cpus-r5@1", label="cpus_r5_1")
    domain = _FakeNode("RPU", {"cpus": [1, 0x1, 0]})
    tree = _FakeTree(_FakeNode("domains"), {1: cluster})

    selection = lopper_lib.resolve_domain_cpus(tree, domain)

    assert selection.source == lopper_lib.CpuSelectionSource.UNRESOLVED
    assert selection.cpus == []
    assert "selects no CPU" in selection.diagnostic


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


@pytest.mark.parametrize("target_os", ["baremetal_dt", "freertos",
                                       "zephyr_dt", None])
def test_openamp_restores_selected_nonlinux_timer_binding(monkeypatch,
                                                           target_os):
    """Non-Linux output restores only its relation-selected UIO timer."""
    tree = LopperTree()
    tree + LopperNode(-1, "/axi")
    tree + LopperNode(-1, "/domains")
    cluster = LopperNode(-1, "/cpus-r5@0")
    tree + cluster
    cluster.phandle_or_create()
    cpu = LopperNode(-1, "/cpus-r5@0/cpu@0")
    tree + cpu

    ttc0 = LopperNode(-1, "/axi/timer@ff110000")
    ttc0["compatible"] = "cdns,ttc"
    ttc0["status"] = "okay"
    tree + ttc0
    ttc0.phandle_or_create()

    ttc2 = LopperNode(-1, "/axi/timer@ff130000")
    ttc2["compatible"] = "uio"
    ttc2["status"] = "disabled"
    tree + ttc2
    ttc2.phandle_or_create()

    fan = LopperNode(-1, "/pwm-fan")
    fan["compatible"] = "pwm-fan"
    fan["pwms"] = [ttc0.phandle, 2, 40000, 1]
    tree + fan

    domain = LopperNode(-1, "/domains/APU_Linux")
    tree + domain
    domain["os,type"] = ["linux"]
    domain["cpus"] = [cluster.phandle, 0x1, 0]
    d2d = LopperNode(-1, "/domains/APU_Linux/domain-to-domain")
    tree + d2d
    relation = LopperNode(
        -1,
        "/domains/APU_Linux/domain-to-domain/libmetal-relation",
    )
    tree + relation
    channel = LopperNode(
        -1,
        "/domains/APU_Linux/domain-to-domain/libmetal-relation/relation0",
    )
    tree + channel
    channel["timer"] = [ttc2.phandle]
    tree.sync()

    monkeypatch.setattr(openamp_xlnx, "get_platform",
                        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.ZYNQMP)
    monkeypatch.setattr(openamp_xlnx, "get_cpu_node",
                        lambda sdt, options: cpu)

    sdt = type("FakeSdt", (), {"tree": tree})()
    assert openamp_xlnx.xlnx_openamp_update_relation_timers(
        sdt, target_os, "psu_cortexa53_0")

    assert tree[ttc0.abs_path] is ttc0
    assert fan.propval("pwms", list) == [ttc0.phandle, 2, 40000, 1]
    assert ttc2.propval("compatible", list) == ["cdns,ttc"]
    assert ttc2.propval("status", list) == ["disabled"]


def test_openamp_enables_only_selected_linux_uio_timer(monkeypatch):
    """Linux enables its relation timer without deleting other TTC users."""
    tree = LopperTree()
    tree + LopperNode(-1, "/axi")
    tree + LopperNode(-1, "/domains")
    cluster = LopperNode(-1, "/cpus-a53@0")
    tree + cluster
    cluster.phandle_or_create()
    cpu = LopperNode(-1, "/cpus-a53@0/cpu@0")
    tree + cpu

    selected = LopperNode(-1, "/axi/timer@ff130000")
    selected["compatible"] = "uio"
    selected["status"] = "disabled"
    tree + selected
    selected.phandle_or_create()
    unrelated = LopperNode(-1, "/axi/timer@ff110000")
    unrelated["compatible"] = "cdns,ttc"
    unrelated["status"] = "okay"
    tree + unrelated
    unrelated.phandle_or_create()
    second_selected = LopperNode(-1, "/axi/timer@ff150000")
    second_selected["compatible"] = ["vendor,timer", "uio"]
    second_selected["status"] = "disabled"
    tree + second_selected
    second_selected.phandle_or_create()

    domain = LopperNode(-1, "/domains/APU_Linux")
    tree + domain
    domain["cpus"] = [cluster.phandle, 0x1, 0]
    tree + LopperNode(-1, "/domains/APU_Linux/domain-to-domain")
    tree + LopperNode(
        -1, "/domains/APU_Linux/domain-to-domain/libmetal-relation")
    channel = LopperNode(
        -1,
        "/domains/APU_Linux/domain-to-domain/libmetal-relation/relation0",
    )
    tree + channel
    channel["timer"] = [selected.phandle]
    tree + LopperNode(
        -1, "/domains/APU_Linux/domain-to-domain/openamp-relation")
    second_channel = LopperNode(
        -1,
        "/domains/APU_Linux/domain-to-domain/openamp-relation/relation0",
    )
    tree + second_channel
    second_channel["timer"] = [second_selected.phandle]
    tree.sync()

    monkeypatch.setattr(openamp_xlnx, "get_platform",
                        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.ZYNQMP)
    monkeypatch.setattr(openamp_xlnx, "get_cpu_node",
                        lambda sdt, options: cpu)

    sdt = type("FakeSdt", (), {"tree": tree})()
    assert openamp_xlnx.xlnx_openamp_update_relation_timers(
        sdt, "linux_dt", "psu_cortexa53_0")
    assert selected.propval("compatible", list) == ["uio"]
    assert selected.propval("status", list) == ["okay"]
    assert second_selected.propval("compatible", list) == [
        "vendor,timer", "uio"]
    assert second_selected.propval("status", list) == ["okay"]
    assert tree[unrelated.abs_path] is unrelated
    assert unrelated.propval("compatible", list) == ["cdns,ttc"]


def _construct_remoteproc_v2(monkeypatch, platform, channel_info, tcm,
                             rpu_core=0):
    """Run remoteproc cluster construction and capture its outputs."""
    captured = {}

    monkeypatch.setattr(
        openamp_xlnx, "determinte_rpu_core",
        lambda tree, cpu_config, remote_node: openamp_xlnx.RPU_CORE(rpu_core))
    monkeypatch.setattr(
        openamp_xlnx, "get_platform",
        lambda tree, verbose=0: platform)

    def capture_cluster(tree, platform, cpu_config, ranges, path):
        captured["ranges"] = ranges
        return True

    def capture_core(tree, info, power_domains, reg, reg_names, path,
                     platform):
        captured["power_domains"] = power_domains
        captured["reg"] = reg
        captured["reg_names"] = reg_names
        return "core"

    monkeypatch.setattr(
        openamp_xlnx, "xlnx_remoteproc_v2_add_cluster", capture_cluster)
    monkeypatch.setattr(
        openamp_xlnx, "xlnx_remoteproc_v2_add_core", capture_core)

    result = openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
        tcm.tree, channel_info, [tcm])
    return result, captured


@pytest.mark.parametrize(
    "platform, core, pd_id, legacy_pd, node_name, size, bank, offset, "
    "reg_name",
    [
        # ZynqMP: power-domains holds the firmware ID directly.
        (openamp_xlnx.SOC_TYPE.ZYNQMP, 0, 15, None,
         "psu_r5_0_atcm_global@ffe00000", 0x10000, 0, 0x0, "atcm0"),
        (openamp_xlnx.SOC_TYPE.ZYNQMP, 1, 18, None,
         "psu_r5_1_btcm_global@ffeb0000", 0x10000, 1, 0x20000, "btcm1"),
        # Versal: firmware IDs, including R5 core 1.
        (openamp_xlnx.SOC_TYPE.VERSAL, 0, 0x1831800B, None,
         "psv_r5_0_atcm_global@ffe00000", 0x10000, 0, 0x0, "atcm0"),
        (openamp_xlnx.SOC_TYPE.VERSAL, 1, 0x1831800E, None,
         "psv_r5_1_btcm_global@ffeb0000", 0x10000, 1, 0x20000, "btcm1"),
        # Versal NET: TCM_A_1A is the core 1 ATCM at 0xeba40000.
        (openamp_xlnx.SOC_TYPE.VERSAL_NET, 1, 0x183180CE, None,
         "psx_r52_1a_atcm_global@eba40000", 0x10000, 1, 0x0, "atcm0"),
        # Versal NET: transitional SDTs may still use xlnx,power-domain.
        (openamp_xlnx.SOC_TYPE.VERSAL_NET, 0, 0xDEADBEEF, 0x183180CB,
         "psx_r52_0a_atcm_global@eba00000", 0x10000, 0, 0x0, "atcm0"),
        # Versal NET cluster B: TCM_B_0A at 0xeba80000, TCM_B_1B at
        # 0xebad0000. The SDT's xlnx,power-domain for r52_0b holds TCM_A_1A
        # (0x183180ce); power-domains is used first.
        (openamp_xlnx.SOC_TYPE.VERSAL_NET, 2, 0x183180D1, 0x183180CE,
         "psx_r52_0b_atcm_global@eba80000", 0x10000, 0, 0x0, "atcm0"),
        (openamp_xlnx.SOC_TYPE.VERSAL_NET, 3, 0x183180D5, None,
         "psx_r52_1b_btcm_global@ebad0000", 0x8000, 1, 0x10000, "btcm0"),
        # Versal2: SCMI IDs for cluster A core 0 and core 1 (r52_1a).
        (openamp_xlnx.SOC_TYPE.VERSAL2, 0, 0x44, None,
         "r52_0a_atcm_global@eba00000", 0x10000, 0, 0x0, "atcm0"),
        (openamp_xlnx.SOC_TYPE.VERSAL2, 1, 0x47, None,
         "r52_1a_atcm_global@eba40000", 0x10000, 1, 0x0, "atcm0"),
        (openamp_xlnx.SOC_TYPE.VERSAL2, 1, 0x49, None,
         "r52_1a_ctcm_global@eba60000", 0x8000, 1, 0x18000, "ctcm0"),
        # Versal2: the address comes from the SDT, not the address table.
        (openamp_xlnx.SOC_TYPE.VERSAL2, 6, 0x56, None,
         "r52_0d_atcm_global@ebb80000", 0x10000, 0, 0x0, "atcm0"),
    ],
)
def test_remoteproc_v2_tcm_ranges_use_sdt_reg(
        monkeypatch, platform, core, pd_id, legacy_pd, node_name, size, bank,
        offset, reg_name):
    """Remoteproc ranges use the SDT TCM address with the table bank view."""
    channel_info, tcm = _remoteproc_v2_fixture(
        pd_id, legacy_pd, node_name, reg_size=size)
    base = int(node_name.split("@")[1], 16)

    result, captured = _construct_remoteproc_v2(
        monkeypatch, platform, channel_info, tcm, rpu_core=core)

    assert result == "core"
    assert captured["power_domains"] == [0xA5, 0, 0xA5, pd_id]
    assert captured["reg"] == [bank, offset, 0x0, size]
    assert captured["ranges"] == [bank, offset, 0x0, base, 0x0, size]
    assert captured["reg_names"] == [reg_name]


# Local offsets of the A, B, and C TCM banks on an R52 core.
_R52_BANK_OFFSETS = (0x0, 0x10000, 0x18000)


def _r52_legacy_core_and_bank(legacy_id):
    """Core and bank encoded in a Versal NET/Versal2 TCM firmware ID.

    TCM_A_0A..TCM_B_1C are 0x183180cb-0x183180d6 and TCM_C_0A..TCM_E_1C
    are 0x18318100-0x18318111: three banks per core, in RPU core order
    (A_0, A_1, B_0, B_1, C_0, ...).
    """
    if 0x183180CB <= legacy_id <= 0x183180D6:
        index = legacy_id - 0x183180CB
        return index // 3, index % 3
    if 0x18318100 <= legacy_id <= 0x18318111:
        index = legacy_id - 0x18318100
        return 4 + index // 3, index % 3
    return None


def test_r52_tcm_table_bank_view_matches_firmware_id():
    """Each R52 table entry maps its TCM to the right core and bank."""
    checked = 0
    for legacy_id, mapping in openamp_xlnx.legacy_memory_nodes.items():
        decoded = _r52_legacy_core_and_bank(legacy_id)
        if decoded is None:
            continue
        core, bank = decoded
        expected = [core % 2, _R52_BANK_OFFSETS[bank]]
        assert mapping["rpu_view"][:2] == expected, hex(legacy_id)
        assert mapping["system_view"][:2] == expected, hex(legacy_id)
        checked += 1
    assert checked


def test_r5_tcm_table_bank_view_matches_firmware_id():
    """Each R5 table entry maps its TCM to the right core and bank."""
    expected = {
        # ZynqMP psu_r5_{0,1}_{a,b}tcm_global
        15: [0, 0x0], 16: [0, 0x20000], 17: [1, 0x0], 18: [1, 0x20000],
        # Versal psv_r5_{0,1}_{a,b}tcm_global
        0x1831800B: [0, 0x0], 0x1831800C: [0, 0x20000],
        0x1831800D: [1, 0x0], 0x1831800E: [1, 0x20000],
    }
    for legacy_id, view in expected.items():
        mapping = openamp_xlnx.legacy_memory_nodes[legacy_id]
        assert mapping["rpu_view"][:2] == view, hex(legacy_id)
        assert mapping["system_view"][:2] == view, hex(legacy_id)


def test_versal2_scmi_tcm_ids_translate_to_same_core_and_bank():
    """Each Versal2 SCMI TCM ID maps to the same core and bank.

    SCMI TCM IDs start at SCMI_PD_VERSAL2_DEV_TCM_A_0A (0x44) and follow
    the same core and bank order as the firmware IDs.
    """
    table = openamp_xlnx.versal2_scmi_to_legacy_pd
    for scmi_id, legacy_id in table.items():
        index = scmi_id - 0x44
        assert (index // 3, index % 3) == \
            _r52_legacy_core_and_bank(legacy_id), hex(scmi_id)
        assert legacy_id in openamp_xlnx.legacy_memory_nodes, hex(scmi_id)


def test_versal2_scmi_tcm_ids_cover_every_bank():
    """Every Versal2 SCMI TCM ID, TCM_A_0A (0x44) to TCM_E_1C (0x61), maps."""
    assert sorted(openamp_xlnx.versal2_scmi_to_legacy_pd) == \
        list(range(0x44, 0x62))


def test_remoteproc_v2_versal2_ignores_xlnx_power_domain(monkeypatch):
    """Versal2 maps power-domains, not a conflicting xlnx,power-domain."""
    # SCMI 0x4a is TCM_B_0A. The SDT's xlnx,power-domain for the same node
    # has been seen to carry TCM_A_1A (0x183180ce).
    channel_info, tcm = _remoteproc_v2_fixture(
        0x4A, 0x183180CE, "r52_0b_atcm_global@eba80000")

    result, captured = _construct_remoteproc_v2(
        monkeypatch, openamp_xlnx.SOC_TYPE.VERSAL2, channel_info, tcm,
        rpu_core=2)

    assert result == "core"
    assert captured["ranges"] == [0, 0x0, 0x0, 0xEBA80000, 0x0, 0x10000]


@pytest.mark.parametrize(
    "platform, core, pd_id, node_name, owner",
    [
        # RPU1 given RPU0's ATCM.
        (openamp_xlnx.SOC_TYPE.ZYNQMP, 1, 15,
         "psu_r5_0_atcm_global@ffe00000", 0),
        (openamp_xlnx.SOC_TYPE.VERSAL, 0, 0x1831800E,
         "psv_r5_1_btcm_global@ffeb0000", 1),
        # RPU_A_1 given r52_0b (TCM_B_0A), the bank the Versal NET and
        # Versal2 SDTs swap with r52_1a in xlnx,power-domain.
        (openamp_xlnx.SOC_TYPE.VERSAL_NET, 1, 0x183180D1,
         "psx_r52_0b_atcm_global@eba80000", 2),
        (openamp_xlnx.SOC_TYPE.VERSAL2, 1, 0x4A,
         "r52_0b_atcm_global@eba80000", 2),
    ],
)
def test_remoteproc_v2_rejects_tcm_of_another_core(
        monkeypatch, capsys, platform, core, pd_id, node_name, owner):
    """A split core may not load another core's TCM bank."""
    channel_info, tcm = _remoteproc_v2_fixture(pd_id, None, node_name)

    result, _ = _construct_remoteproc_v2(
        monkeypatch, platform, channel_info, tcm, rpu_core=core)

    assert result is False
    assert (f"TCM node {tcm.abs_path} belongs to RPU core {owner}, but the "
            f"remote runs on RPU core {core}") in capsys.readouterr().out


@pytest.mark.parametrize(
    "platform, core, pd_id, node_name, allowed",
    [
        # R5 lockstep core 0 uses core 1's banks in its own cluster ...
        (openamp_xlnx.SOC_TYPE.ZYNQMP, 0, 17,
         "psu_r5_1_atcm_global@ffe90000", True),
        # ... R52 cores do not combine TCM in lockstep ...
        (openamp_xlnx.SOC_TYPE.VERSAL2, 2, 0x4D,
         "r52_1b_atcm_global@ebac0000", False),
        # ... and no core uses the banks of another cluster.
        (openamp_xlnx.SOC_TYPE.VERSAL2, 0, 0x4A,
         "r52_0b_atcm_global@eba80000", False),
    ],
)
def test_remoteproc_v2_lockstep_uses_its_cluster_tcm(
        monkeypatch, platform, core, pd_id, node_name, allowed):
    """In R5 lockstep, core 0 may load the TCM banks of its whole cluster."""
    channel_info, tcm = _remoteproc_v2_fixture(pd_id, None, node_name)
    channel_info["cpu_config"] = openamp_xlnx.CPU_CONFIG.RPU_LOCKSTEP

    result, _ = _construct_remoteproc_v2(
        monkeypatch, platform, channel_info, tcm, rpu_core=core)

    assert (result == "core") is allowed


def test_remoteproc_v2_requires_tcm_reg(monkeypatch, capsys):
    """A TCM node without reg is rejected instead of using the table."""
    channel_info, tcm = _remoteproc_v2_fixture(with_reg=False)

    result, _ = _construct_remoteproc_v2(
        monkeypatch, openamp_xlnx.SOC_TYPE.VERSAL2, channel_info, tcm)

    assert result is False
    assert "is missing a valid reg property" in capsys.readouterr().out


@pytest.mark.parametrize(
    "pd_id, legacy_pd, expected_error",
    [
        (None, None, "missing a valid power-domains property"),
        (0xDEADBEEF, None,
         "no address mapping for power-domains ID 0xdeadbeef"),
        (0xDEADBEEF, 0xFEEDFACE,
         "power-domains ID 0xdeadbeef, legacy ID 0xfeedface"),
    ],
)
def test_remoteproc_v2_reports_invalid_tcm_mapping(
        monkeypatch, capsys, pd_id, legacy_pd, expected_error):
    """Missing and unknown TCM power-domain IDs have useful diagnostics."""
    channel_info, tcm = _remoteproc_v2_fixture(pd_id, legacy_pd)

    monkeypatch.setattr(
        openamp_xlnx, "determinte_rpu_core",
        lambda tree, cpu_config, remote_node: openamp_xlnx.RPU_CORE.RPU_0)
    monkeypatch.setattr(
        openamp_xlnx, "get_platform",
        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.VERSAL2)

    result = openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
        object(), channel_info, [tcm])

    assert result is False
    diagnostic = capsys.readouterr().out
    assert tcm.abs_path in diagnostic
    assert expected_error in diagnostic


def _rpu_remote(core, config="split", core_pd=0xC0):
    """Remote domain as YAML expansion leaves it for RPU core ``core``."""
    remote = LopperNode(-1, "/domains/RPU")
    remote["cpu_config_str"] = [config]
    remote["core_num"] = [0]
    remote["rpu_core_num"] = [core]
    remote["rpu_pd_val"] = [0xA5, core_pd]
    return remote


@pytest.mark.parametrize(
    "rpu_core_num, core_num, rpu_core",
    [
        # SDTs give cpus_r5_1 and every R52 cluster a single cpu@0, so
        # core_num (the CPU reg) is 0; YAML expansion stores the core's
        # number from the cluster's unit address.
        (1, 0, 1),
        (7, 0, 7),
        # A remote expanded without rpu_core_num keeps core_num.
        (None, 1, 1),
    ],
)
def test_rpu_core_comes_from_yaml_expansion(rpu_core_num, core_num, rpu_core):
    """The RPU core number comes from rpu_core_num, not the CPU reg."""
    remote = LopperNode(-1, "/domains/RPU")
    remote["core_num"] = [core_num]
    if rpu_core_num is not None:
        remote["rpu_core_num"] = [rpu_core_num]
    split = openamp_xlnx.CPU_CONFIG.RPU_SPLIT

    assert openamp_xlnx.determinte_rpu_core(None, split, remote) == \
        openamp_xlnx.RPU_CORE(rpu_core)


@pytest.mark.parametrize(
    "platform, clusters",
    [
        (openamp_xlnx.SOC_TYPE.ZYNQMP, ["ffe00000"] * 2),
        (openamp_xlnx.SOC_TYPE.VERSAL, ["ffe00000"] * 2),
        (openamp_xlnx.SOC_TYPE.VERSAL_NET,
         ["eba00000", "eba00000", "eba80000", "eba80000"]),
        (openamp_xlnx.SOC_TYPE.VERSAL2,
         ["eba00000", "eba00000", "eba80000", "eba80000", "ebb00000",
          "ebb00000", "ebb80000", "ebb80000", "ebc00000", "ebc00000"]),
    ],
)
def test_cluster_address_is_core_0_atcm(platform, clusters):
    """Each cluster is named after its core 0 ATCM address in the SDT."""
    assert [openamp_xlnx.xlnx_remoteproc_v2_cluster_base_str(
        platform, openamp_xlnx.RPU_CORE(core))
        for core in range(len(clusters))] == clusters


@pytest.mark.parametrize(
    "cpu_type, banks, base",
    [
        # ZynqMP and Versal R5: both cores' ATCM and BTCM.
        ("cortexr5", [0xFFE00000, 0xFFE20000, 0xFFE90000, 0xFFEB0000],
         0xFFE00000),
        # R52 clusters A to E: the A, B and C banks of both cores.
        ("cortexr52", [0xEBA00000, 0xEBA20000, 0xEBA40000, 0xEBA60000],
         0xEBA00000),
        ("cortexr52", [0xEBA80000, 0xEBAA0000, 0xEBAC0000, 0xEBAE0000],
         0xEBA80000),
        ("cortexr52", [0xEBB00000, 0xEBB20000, 0xEBB40000, 0xEBB60000],
         0xEBB00000),
        ("cortexr52", [0xEBB80000, 0xEBBA0000, 0xEBBC0000, 0xEBBE0000],
         0xEBB80000),
        ("cortexr52", [0xEBC00000, 0xEBC20000, 0xEBC40000, 0xEBC60000],
         0xEBC00000),
    ],
)
def test_cluster_tcm_base_is_core_0_atcm(cpu_type, banks, base):
    """Every TCM bank of an RPU cluster gives the cluster's core 0 ATCM."""
    assert {xlnx_rpu_tcm.rpu_cluster_tcm_base(bank, cpu_type)
            for bank in banks} == {base}


def _tcm_node(tree, name, size, pd_id):
    """Add an SDT TCM node /axi/<name> with a reg and power-domains."""
    node = LopperNode(-1, f"/axi/{name}")
    node["xlnx,ip-name"] = ["tcm_global"]
    node["reg"] = (
        lopper_lib.int_to_cells(int(name.split("@")[1], 16), 2) +
        lopper_lib.int_to_cells(size, 2))
    node["power-domains"] = [0xA5, pd_id]
    tree + node
    return node


@pytest.mark.parametrize(
    "platform, remotes, expected",
    [
        # ZynqMP (Kria and ZCU102): both R5 cores in split mode.
        (openamp_xlnx.SOC_TYPE.ZYNQMP,
         [(0, 0x7, [("psu_r5_0_atcm_global@ffe00000", 0x10000, 15)]),
          (1, 0x8, [("psu_r5_1_atcm_global@ffe90000", 0x10000, 17)])],
         {"/remoteproc@ffe00000/r5f@0": 0x7,
          "/remoteproc@ffe00000/r5f@1": 0x8}),
        # Versal NET: RPU_A_1 in cluster A and RPU_B_0 in cluster B.
        (openamp_xlnx.SOC_TYPE.VERSAL_NET,
         [(1, 0x181100C0, [("psx_r52_1a_atcm_global@eba40000", 0x10000,
                            0x183180CE)]),
          (2, 0x181100C1, [("psx_r52_0b_atcm_global@eba80000", 0x10000,
                            0x183180D1)])],
         {"/remoteproc@eba00000/r52f@1": 0x181100C0,
          "/remoteproc@eba80000/r52f@0": 0x181100C1}),
        # Versal2 SCMI IDs: RPU_A_0 and RPU_A_1, and RPU_D_1.
        (openamp_xlnx.SOC_TYPE.VERSAL2,
         [(0, 0, [("r52_0a_atcm_global@eba00000", 0x10000, 0x44)]),
          (1, 1, [("r52_1a_atcm_global@eba40000", 0x10000, 0x47)]),
          (7, 7, [("r52_1d_atcm_global@ebbc0000", 0x10000, 0x59)])],
         {"/remoteproc@eba00000/r52f@0": 0,
          "/remoteproc@eba00000/r52f@1": 1,
          "/remoteproc@ebb80000/r52f@1": 7}),
        # Versal2 SCMI IDs in clusters B and C: RPU_B_0 and RPU_C_1.
        (openamp_xlnx.SOC_TYPE.VERSAL2,
         [(2, 2, [("r52_0b_atcm_global@eba80000", 0x10000, 0x4A)]),
          (5, 5, [("r52_1c_atcm_global@ebb40000", 0x10000, 0x53)])],
         {"/remoteproc@eba80000/r52f@0": 2,
          "/remoteproc@ebb00000/r52f@1": 5}),
    ],
)
def test_remoteproc_v2_places_each_rpu_core(
        monkeypatch, platform, remotes, expected):
    """Remotes on any RPU core get their own cluster and core node."""
    tree = LopperTree()
    axi = LopperNode(-1, "/axi")
    axi["#address-cells"] = [2]
    axi["#size-cells"] = [2]
    tree + axi
    monkeypatch.setattr(
        openamp_xlnx, "get_platform", lambda tree, verbose=0: platform)

    for core, core_pd, tcms in remotes:
        info = {"remote_node": _rpu_remote(core, core_pd=core_pd)}
        assert openamp_xlnx.xlnx_remoteproc_rpu_parse(tree, None, info, [])
        tcm_nodes = [_tcm_node(tree, *tcm) for tcm in tcms]
        assert openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
            tree, info, tcm_nodes)

    cores = [n for n in tree["/"].subnodes()
             if n.name.startswith(("r5f@", "r52f@"))]
    assert {n.abs_path: n.propval("power-domains", list)[1]
            for n in cores} == expected

    # Each cluster maps the ATCM of each of its cores, tagged with the
    # core's bank.
    for cluster in {n.parent for n in cores}:
        ranges = cluster.propval("ranges", list)
        banks = sorted(int(n.name.split("@")[1])
                       for n in cluster.subnodes(children_only=True))
        assert sorted(ranges[0::6]) == banks


def test_remoteproc_v2_rejects_second_relation_for_core(monkeypatch, capsys):
    """Two remoteproc relations for one RPU core fail on the second one."""
    tree = LopperTree()
    axi = LopperNode(-1, "/axi")
    axi["#address-cells"] = [2]
    axi["#size-cells"] = [2]
    tree + axi
    monkeypatch.setattr(
        openamp_xlnx, "get_platform",
        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.ZYNQMP)
    atcm = _tcm_node(tree, "psu_r5_1_atcm_global@ffe90000", 0x10000, 17)
    btcm = _tcm_node(tree, "psu_r5_1_btcm_global@ffeb0000", 0x10000, 18)

    # Two domains on RPU1, such as a baremetal and a Zephyr domain.
    for index, tcm in enumerate((atcm, btcm)):
        info = {"remote_node": _rpu_remote(1)}
        assert openamp_xlnx.xlnx_remoteproc_rpu_parse(tree, None, info, [])
        result = openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
            tree, info, [tcm])
        if index == 0:
            assert result
            ranges = tree["/remoteproc@ffe00000"].propval("ranges", list)

    assert result is False
    assert tree["/remoteproc@ffe00000"].propval("ranges", list) == ranges
    assert ("/remoteproc@ffe00000/r5f@1 already exists; each RPU core can "
            "have only one remoteproc relation") in capsys.readouterr().out


@pytest.mark.parametrize(
    "relations, expected_error",
    [
        # A cluster in lockstep runs on its core 0.
        ([(1, "lockstep", "psu_r5_1_atcm_global@ffe90000", 17)],
         "RPU core 1 is core 1 of its cluster; a cluster in lockstep runs "
         "on its core 0"),
        # Core 1 is not free while the cluster runs in lockstep ...
        ([(0, "lockstep", "psu_r5_0_atcm_global@ffe00000", 15),
          (1, "split", "psu_r5_1_atcm_global@ffe90000", 17)],
         "/remoteproc@ffe00000/r5f@0 already uses /remoteproc@ffe00000; a "
         "cluster in lockstep can have only one remoteproc relation"),
        # ... and a cluster with a split core cannot switch to lockstep.
        ([(1, "split", "psu_r5_1_atcm_global@ffe90000", 17),
          (0, "lockstep", "psu_r5_0_atcm_global@ffe00000", 15)],
         "/remoteproc@ffe00000/r5f@1 already uses /remoteproc@ffe00000; a "
         "cluster in lockstep can have only one remoteproc relation"),
    ],
)
def test_remoteproc_v2_lockstep_cluster_has_one_relation(
        monkeypatch, capsys, relations, expected_error):
    """A cluster in lockstep has one remoteproc relation, on its core 0."""
    tree = LopperTree()
    axi = LopperNode(-1, "/axi")
    axi["#address-cells"] = [2]
    axi["#size-cells"] = [2]
    tree + axi
    monkeypatch.setattr(
        openamp_xlnx, "get_platform",
        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.ZYNQMP)

    results = []
    for core, config, tcm_name, tcm_pd in relations:
        info = {"remote_node": _rpu_remote(core, config=config)}
        assert openamp_xlnx.xlnx_remoteproc_rpu_parse(tree, None, info, [])
        tcm = _tcm_node(tree, tcm_name, 0x10000, tcm_pd)
        results.append(openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
            tree, info, [tcm]))

    assert all(results[:-1]) and results[-1] is False
    assert expected_error in capsys.readouterr().out


@pytest.mark.parametrize(
    "platform, core_pd, tcms, tcm_pds",
    [
        (openamp_xlnx.SOC_TYPE.ZYNQMP, 0x7,
         ["psu-r5-0-atcm-global@ffe00000", "psu-r5-0-btcm-global@ffe20000",
          "psu-r5-1-atcm-global@ffe90000", "psu-r5-1-btcm-global@ffeb0000"],
         [0xF, 0x10, 0x11, 0x12]),
        (openamp_xlnx.SOC_TYPE.VERSAL, 0x18110005,
         ["psv_r5_0_atcm_global@ffe00000", "psv_r5_0_btcm_global@ffe20000",
          "psv_r5_1_atcm_global@ffe90000", "psv_r5_1_btcm_global@ffeb0000"],
         [0x1831800B, 0x1831800C, 0x1831800D, 0x1831800E]),
    ],
)
def test_remoteproc_v2_r5_lockstep_matches_binding(
        monkeypatch, platform, core_pd, tcms, tcm_pds):
    """R5 lockstep maps core 1's banks after core 0's, as in the binding.

    The xlnx,zynqmp-r5fss lockstep example: core 0 has ATCM and BTCM at
    local 0x0 and 0x20000 (global 0xffe00000, 0xffe20000) and core 1's at
    local 0x10000 and 0x30000 (global 0xffe10000, 0xffe30000), all in bank 0.
    """
    tree = LopperTree()
    axi = LopperNode(-1, "/axi")
    axi["#address-cells"] = [2]
    axi["#size-cells"] = [2]
    tree + axi
    monkeypatch.setattr(
        openamp_xlnx, "get_platform", lambda tree, verbose=0: platform)
    info = {"remote_node": _rpu_remote(0, config="lockstep",
                                       core_pd=core_pd)}
    assert openamp_xlnx.xlnx_remoteproc_rpu_parse(tree, None, info, [])
    nodes = [_tcm_node(tree, name, 0x10000, pd_id)
             for name, pd_id in zip(tcms, tcm_pds)]

    core = openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
        tree, info, nodes)

    cluster = tree["/remoteproc@ffe00000"]
    assert cluster.propval("xlnx,cluster-mode", list) == [1]
    assert cluster.propval("xlnx,tcm-mode", list) == [1]
    assert cluster.propval("ranges", list) == [
        0, 0x0, 0, 0xFFE00000, 0, 0x10000,
        0, 0x20000, 0, 0xFFE20000, 0, 0x10000,
        0, 0x10000, 0, 0xFFE10000, 0, 0x10000,
        0, 0x30000, 0, 0xFFE30000, 0, 0x10000]
    assert core.abs_path == "/remoteproc@ffe00000/r5f@0"
    assert core.propval("reg", list) == [
        0, 0x0, 0, 0x10000, 0, 0x20000, 0, 0x10000,
        0, 0x10000, 0, 0x10000, 0, 0x30000, 0, 0x10000]
    assert core.propval("reg-names", list) == [
        "atcm0", "btcm0", "atcm1", "btcm1"]
    assert core.propval("power-domains", list)[1::2] == [core_pd] + tcm_pds


def test_remoteproc_v2_rejects_tcm_bank_listed_twice(monkeypatch, capsys):
    """A TCM bank listed twice, such as by an SDT lockstep node, fails."""
    tree = LopperTree()
    axi = LopperNode(-1, "/axi")
    axi["#address-cells"] = [2]
    axi["#size-cells"] = [2]
    tree + axi
    monkeypatch.setattr(
        openamp_xlnx, "get_platform",
        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.ZYNQMP)
    info = {"remote_node": _rpu_remote(0, config="lockstep")}
    assert openamp_xlnx.xlnx_remoteproc_rpu_parse(tree, None, info, [])
    # The SDT's psu_r5_0_atcm_lockstep node has core 0's ATCM power domain.
    nodes = [_tcm_node(tree, "psu-r5-0-atcm-global@ffe00000", 0x10000, 15),
             _tcm_node(tree, "psu-r5-0-atcm-lockstep@ffe10000", 0x10000, 15)]

    assert openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
        tree, info, nodes) is False
    assert ("TCM node /axi/psu-r5-0-atcm-lockstep@ffe10000 is a TCM bank "
            "that another elfload node of the remote already lists") in \
        capsys.readouterr().out


def _sdt_tree_with_axi():
    tree = LopperTree()
    axi = LopperNode(-1, "/axi")
    axi["#address-cells"] = [2]
    axi["#size-cells"] = [2]
    tree + axi
    return tree


def _sdt_node(tree, path, address, size, pd_id=None):
    node = LopperNode(-1, path)
    node["reg"] = (lopper_lib.int_to_cells(address, 2) +
                   lopper_lib.int_to_cells(size, 2))
    if pd_id is not None:
        node["power-domains"] = [0xA5, pd_id]
    tree + node
    node.phandle_or_create()
    return node


def _sdt_cluster(tree, path, cpu_regs):
    cluster = LopperNode(-1, path)
    cluster["compatible"] = ["cpus,cluster"]
    tree + cluster
    cpus = []
    for reg in cpu_regs:
        cpu = LopperNode(-1, f"{path}/cpu@{reg:x}")
        cpu["compatible"] = ["arm,cortex-r52"]
        cpu["reg"] = [reg]
        tree + cpu
        cpus.append(cpu)
    return cluster, cpus


@pytest.mark.parametrize(
    "cluster_path, cpu_regs, selected, core",
    [
        # Current SDTs: one cluster per RPU core, holding cpu@0.
        ("/cpus-r52@3", [0], 0, 3),
        ("/cpus-r5@1", [0], 0, 1),
        # Older SDTs: both R5 cores in one cluster.
        ("/cpus-r5@0", [0, 1], 1, 1),
    ],
)
def test_yaml_rpu_core_number_comes_from_sdt_cluster(
        cluster_path, cpu_regs, selected, core):
    """YAML expansion numbers an RPU core by its SDT cluster."""
    tree = LopperTree()
    cluster, cpus = _sdt_cluster(tree, cluster_path, cpu_regs)

    assert yaml_to_dts_expansion._rpu_core_number(
        cluster, cpus[selected]) == core


def test_yaml_rpu_tcm_view_comes_from_cluster_address_map():
    """YAML expansion keeps the TCM banks of the cluster's address-map.

    ZynqMP SDTs map each R5 core's banks at their core-local addresses.
    Entries for other devices and for TCM nodes without a power domain are
    skipped.
    """
    tree = _sdt_tree_with_axi()
    atcm = _sdt_node(tree, "/axi/psu_r5_0_atcm@0", 0x0, 0x10000, 15)
    btcm = _sdt_node(tree, "/axi/psu-r5-0-btcm@20000", 0x20000, 0x10000, 16)
    tcm_ram = _sdt_node(tree, "/axi/psu_r5_tcm_ram_0@0", 0x0, 0x40000)
    serial = _sdt_node(tree, "/axi/serial@ff000000", 0xFF000000, 0x1000,
                       0x21)
    cluster, _ = _sdt_cluster(tree, "/cpus-r5@0", [0])
    cluster["#ranges-address-cells"] = [1]
    cluster["#ranges-size-cells"] = [1]
    cluster["address-map"] = [
        0xFF000000, serial.phandle, 0xFF000000, 0x1000,
        0x0, atcm.phandle, 0x0, 0x10000,
        0x20000, btcm.phandle, 0x20000, 0x10000,
        0x0, tcm_ram.phandle, 0x0, 0x40000,
    ]

    assert yaml_to_dts_expansion._rpu_tcm_view(tree, cluster) == [
        15, 0x0, 0x10000, 16, 0x20000, 0x10000]


@pytest.mark.parametrize(
    "core, base",
    [(0, 0xEBA00000), (1, 0xEBA00000), (3, 0xEBA80000), (4, 0xEBB00000),
     (7, 0xEBB80000), (9, 0xEBC00000), (10, None)],
)
def test_yaml_rpu_cluster_base_comes_from_sdt_tcm(core, base):
    """YAML expansion finds a core's RPU cluster from the SDT's TCM banks."""
    tree = _sdt_tree_with_axi()
    for name, address in (
            ("r52_1e_ctcm_global", 0xEBC60000),
            ("r52_0a_atcm_global", 0xEBA00000),
            ("r52_1a_btcm_global", 0xEBA50000),
            ("r52_0b_atcm_global", 0xEBA80000),
            ("r52_1c_atcm_global", 0xEBB40000),
            ("r52_0d_btcm_global", 0xEBB90000)):
        _sdt_node(tree, f"/axi/{name}@{address:x}", address, 0x8000)
    # Not a global TCM bank.
    _sdt_node(tree, "/axi/r52_tcm_alias@0", 0x0, 0x100000)

    assert yaml_to_dts_expansion._rpu_cluster_base(
        tree, "cortexr52", core) == base


def test_rpmsg_allows_one_relation_per_remote_core():
    """One host may have RPMsg relations to several remote cores."""
    tree = LopperTree()
    tree + LopperNode(-1, "/reserved-memory")
    tree + LopperNode(-1, "/remoteproc@ffe00000")
    for core_index in (0, 1):
        carveouts = []
        for name in ("vdev0vring0", "vdev0vring1", "vdev0buffer"):
            node = LopperNode(
                -1, f"/reserved-memory/rpu{core_index}{name}@"
                    f"{0x9880000 + 0x100000 * core_index:x}")
            tree + node
            node.phandle_or_create()
            carveouts.append(node)
        core = LopperNode(-1, f"/remoteproc@ffe00000/r5f@{core_index}")
        tree + core
        core["memory-region"] = [carveouts[0].phandle]
        ipi = LopperNode(-1, f"/ipi@ff34{core_index}000")
        tree + ipi
        ipi.phandle_or_create()
        relation = LopperNode(
            -1, f"/domains/APU/rpmsg-relation/relation{core_index}")
        tree.sync()
        assert openamp_xlnx.xlnx_rpmsg_update_tree_linux(
            tree, relation, ipi, core, carveouts)
        assert core.propval("mboxes", list) == [ipi.phandle, 0,
                                                 ipi.phandle, 1]


def test_rpmsg_rejects_second_relation_for_core(capsys):
    """A second RPMsg relation to one remoteproc core fails."""
    tree = LopperTree()
    tree + LopperNode(-1, "/reserved-memory")
    carveouts = []
    for name in ("vdev0vring0@9880000", "vdev0vring1@9884000",
                 "vdev0buffer@9888000"):
        node = LopperNode(-1, f"/reserved-memory/{name}")
        tree + node
        node.phandle_or_create()
        carveouts.append(node)
    core = LopperNode(-1, "/remoteproc@ffe00000/r5f@0")
    tree + LopperNode(-1, "/remoteproc@ffe00000")
    tree + core
    core["memory-region"] = [carveouts[0].phandle]
    ipi = LopperNode(-1, "/ipi@ff340000")
    tree + ipi
    ipi.phandle_or_create()
    relation = LopperNode(-1, "/domains/APU/rpmsg-relation/relation0")
    tree.sync()

    assert openamp_xlnx.xlnx_rpmsg_update_tree_linux(
        tree, relation, ipi, core, list(carveouts))
    assert not openamp_xlnx.xlnx_rpmsg_update_tree_linux(
        tree, relation, ipi, core, list(carveouts))
    assert ("/remoteproc@ffe00000/r5f@0 already has an RPMsg relation" in
            capsys.readouterr().out)


@pytest.mark.parametrize(
    "libmetal_ipi, allowed",
    [("/axi/ipi@ff340000/child@ff320000", False),
     ("/axi/ipi@ff350000/child@ff320000", True)],
)
def test_libmetal_rejects_ipi_used_by_rpmsg(
        monkeypatch, libmetal_ipi, allowed):
    """libmetal cannot use an IPI agent that RPMsg uses as a mailbox."""
    tree = LopperTree()
    for path in ("/axi", "/axi/ipi@ff340000", "/axi/ipi@ff340000/child@ff310000",
                 "/axi/ipi@ff340000/child@ff320000", "/axi/ipi@ff350000",
                 "/axi/ipi@ff350000/child@ff320000"):
        tree + LopperNode(-1, path)
    for path in ("/axi/ipi@ff340000/child@ff310000", libmetal_ipi):
        tree[path].phandle_or_create()
        tree[path]["xlnx,ipi-bitmask"] = [0x100]
    # RPMsg on RPU0 through IPI agent ff340000.
    core = LopperNode(-1, "/remoteproc@ffe00000/r5f@0")
    tree + LopperNode(-1, "/remoteproc@ffe00000")
    tree + core
    rpmsg_mbox = tree["/axi/ipi@ff340000/child@ff310000"].phandle
    core["mboxes"] = [rpmsg_mbox, 0, rpmsg_mbox, 1]
    # libmetal to RPU1.
    relation = LopperNode(-1, "/domains/APU/libmetal-relation")
    tree + LopperNode(-1, "/domains")
    tree + LopperNode(-1, "/domains/APU")
    tree + relation
    channel = LopperNode(-1, "/domains/APU/libmetal-relation/relation0")
    tree + channel
    channel["mbox"] = [tree[libmetal_ipi].phandle]
    tree.sync()
    monkeypatch.setattr(
        openamp_xlnx, "get_platform",
        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.ZYNQMP)

    assert openamp_xlnx.xlnx_libmetal_linux_setup_ipi(
        tree, relation, "psu_cortexa53_0") is allowed


@pytest.mark.parametrize(
    "address_cells,size_cells", [(1, 1), (2, 1), (2, 2)])
def test_zephyr_ipc_shm_replaces_domain_carveout_references(
        address_cells, size_cells):
    """Consolidated IPC memory replaces deleted domain phandles."""
    tree = LopperTree()
    tree + LopperNode(-1, "/chosen")
    reserved_memory = LopperNode(-1, "/reserved-memory")
    reserved_memory["#address-cells"] = [address_cells]
    reserved_memory["#size-cells"] = [size_cells]
    tree + reserved_memory
    tree + LopperNode(-1, "/domains")
    carveouts = []
    for name, address, size in (
            ("vring0", 0x9880000, 0x4000),
            ("vring1", 0x9884000, 0x4000),
            ("buffer", 0x9888000, 0x78000)):
        node = LopperNode(-1, f"/reserved-memory/{name}@{address:x}")
        node["reg"] = (
            lopper_lib.int_to_cells(address, address_cells) +
            lopper_lib.int_to_cells(size, size_cells)
        )
        tree + node
        node.phandle_or_create()
        carveouts.append(node)
    firmware = LopperNode(-1, "/reserved-memory/rproc@9800000")
    firmware["reg"] = (
        lopper_lib.int_to_cells(0x9800000, address_cells) +
        lopper_lib.int_to_cells(0x60000, size_cells)
    )
    tree + firmware
    firmware.phandle_or_create()
    domain = LopperNode(-1, "/domains/R5_0_ZEPHYR")
    domain["reserved-memory"] = [
        carveouts[0].phandle, carveouts[1].phandle,
        carveouts[2].phandle, firmware.phandle,
    ]
    tree + domain
    tree.sync()

    ipc = openamp_xlnx.xlnx_openamp_configure_zephyr_ipc_shm(
        tree, carveouts)

    assert domain.propval("reserved-memory", list) == [
        ipc.phandle, firmware.phandle]
    assert tree["/chosen"].propval("zephyr,ipc_shm", list) == [
        ipc.abs_path]
    assert ipc.propval("reg", list) == (
        lopper_lib.int_to_cells(0x9880000, address_cells) +
        lopper_lib.int_to_cells(0x80000, size_cells)
    )


def test_openamp_header_uses_vdev0buffer_reg_size(tmp_path):
    """SHARED_MEM_SIZE is the vdev0buffer size, not its base address."""
    policy = yaml.safe_load(ZYNQMP_OPENAMP_YAML.read_text())
    definitions = policy["definitions"]["OpenAMP"]
    region_names = (
        "rpu0vdev0vring0",
        "rpu0vdev0vring1",
        "rpu0vdev0buffer",
    )
    regions = {
        name: definitions[name][0]
        for name in region_names
    }

    tree = LopperTree()
    reserved_memory = LopperNode(-1, "/reserved-memory")
    reserved_memory["#address-cells"] = [1]
    reserved_memory["#size-cells"] = [1]
    tree + reserved_memory

    carveouts = []
    for name in region_names:
        address = regions[name]["start"]
        size = regions[name]["size"]
        node = LopperNode(-1, f"/reserved-memory/{name}@{address:x}")
        node["reg"] = [address, size]
        tree + node
        carveouts.append(node)

    axi = LopperNode(-1, "/axi")
    axi["#address-cells"] = [1]
    axi["#size-cells"] = [1]
    tree + axi
    remote_ipi = LopperNode(-1, "/axi/mailbox@ff340000")
    remote_ipi["reg"] = [0xff340000, 0x10000]
    remote_ipi["xlnx,int-id"] = [65]
    tree + remote_ipi
    host_ipi = LopperNode(
        -1, "/axi/mailbox@ff340000/child@ff350000")
    host_ipi["xlnx,ipi-bitmask"] = [0x200]
    tree + host_ipi
    tree.sync()

    output = tmp_path / "platform_info.h"
    assert openamp_xlnx.xlnx_openamp_gen_outputs_only(
        tree, "cortexr5_0", output, carveouts, host_ipi)

    generated = output.read_text()
    vring0 = regions["rpu0vdev0vring0"]
    buffer = regions["rpu0vdev0buffer"]
    assert (f"#define SHARED_MEM_PA           "
            f"{vring0['start']:#x}") in generated
    assert (f"#define SHARED_MEM_SIZE         "
            f"{buffer['size']:#x}") in generated
    assert (f"#define SHARED_BUF_OFFSET       "
            f"{buffer['start'] - vring0['start']:#x}") in generated


@pytest.mark.parametrize(
    "address_cells,size_cells", [(1, 1), (2, 1), (2, 2)])
def test_carveout_overlap_detected_for_either_node_order(
        address_cells, size_cells):
    """An overlap is found when the selected carveout is the second node."""
    tree = LopperTree()
    reserved_memory = LopperNode(-1, "/reserved-memory")
    reserved_memory["#address-cells"] = [address_cells]
    reserved_memory["#size-cells"] = [size_cells]
    tree + reserved_memory

    unrelated = LopperNode(-1, "/reserved-memory/unrelated@1000")
    unrelated["reg"] = (
        lopper_lib.int_to_cells(0x1000, address_cells) +
        lopper_lib.int_to_cells(0x1000, size_cells)
    )
    tree + unrelated
    carveout = LopperNode(-1, "/reserved-memory/carveout@1800")
    carveout["reg"] = (
        lopper_lib.int_to_cells(0x1800, address_cells) +
        lopper_lib.int_to_cells(0x1000, size_cells)
    )
    tree + carveout
    tree.sync()

    assert not openamp_xlnx.xlnx_validate_carveouts(tree, [carveout])


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

    with pytest.raises(SystemExit) as error:
        openamp_xlnx.openamp_nontree_outputs_handler(
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

    assert error.value.code == 1
    assert "cannot generate 'unused.cmake'" in caplog.text
    assert "no libmetal,ipc-v1 relation found" in caplog.text
    assert "processor 'psu_cortexr5_0'" in caplog.text
    assert "APU_Linux (os=linux, processor=cpus_a53)" in caplog.text
    assert "R5_1_BAREMETAL (os=baremetal, processor=psu_cortexr5_1)" in caplog.text


def test_openamp_relation_failure_exits_nonzero(monkeypatch, caplog):
    """A requested tree transformation must not report successful output."""
    config = {
        "machine": "cortexa78_0",
        "dt_type": "linux_dt",
        "openamp_output_filename": None,
        "report_valid_ipis": False,
    }
    sdt = type("FakeSdt", (), {"tree": object()})()

    monkeypatch.setattr(
        openamp_xlnx, "parse_openamp_args", lambda args: config)
    monkeypatch.setattr(
        openamp_xlnx, "xlnx_openamp_find_compat_domains",
        lambda tree: True)
    monkeypatch.setattr(
        openamp_xlnx, "xlnx_openamp_update_relation_timers",
        lambda sdt, dt_type, machine: True)
    monkeypatch.setattr(
        openamp_xlnx, "xlnx_handle_relations",
        lambda sdt, machine, find_only, os: False)

    with pytest.raises(SystemExit) as error:
        openamp_xlnx.xlnx_openamp_parse(
            sdt, {"args": ["cortexa78_0", "linux_dt"]})

    assert error.value.code == 1
    assert "failed to process OpenAMP relations" in caplog.text
    assert "processor 'cortexa78_0' and OS 'linux_dt'" in caplog.text
