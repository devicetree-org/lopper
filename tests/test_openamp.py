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
        "remote_node": LopperNode(-1, "/domains/RPU"),
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
        # Versal NET cluster B: TCM_B_0A at 0xeba80000, TCM_B_1B at
        # 0xebad0000. The SDT's xlnx,power-domain for r52_0b holds TCM_A_1A
        # (0x183180ce); only power-domains is used.
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
        # Versal2 cluster D.
        (openamp_xlnx.SOC_TYPE.VERSAL2, 6, 0x56, None,
         "r52_0d_atcm_global@ebb80000", 0x10000, 0, 0x0, "atcm0"),
    ],
)
def test_remoteproc_v2_tcm_ranges_use_sdt_reg(
        monkeypatch, platform, core, pd_id, legacy_pd, node_name, size, bank,
        offset, reg_name):
    """Remoteproc ranges use the SDT TCM address and the family TCM layout.

    The bank index is the core's position in its cluster and the local
    offset is the bank type's core-local address.
    """
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


def test_remoteproc_v2_requires_tcm_reg(monkeypatch, caplog):
    """A TCM node without reg is rejected."""
    channel_info, tcm = _remoteproc_v2_fixture(with_reg=False)

    result, _ = _construct_remoteproc_v2(
        monkeypatch, openamp_xlnx.SOC_TYPE.VERSAL2, channel_info, tcm)

    assert result is False
    assert "is missing a valid reg property" in caplog.text


def test_remoteproc_v2_errors_go_through_lopper_logging(
        monkeypatch, capsys, caplog):
    """Remoteproc errors and trace go through Lopper's logger, not stdout."""
    tree = LopperTree()
    monkeypatch.setattr(
        openamp_xlnx, "get_platform",
        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.VERSAL2)
    info = {"remote_node": _rpu_remote(1, config="lockstep")}
    assert openamp_xlnx.xlnx_remoteproc_rpu_parse(tree, None, info, [])

    assert openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
        tree, info, []) is False

    assert capsys.readouterr().out == ""
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert [r.getMessage() for r in errors] == [
        "openamp_xlnx: RPU core 1 is core 1 of its cluster; a cluster in "
        "lockstep runs on its core 0"]


@pytest.mark.parametrize(
    "pd_id, node_name, expected_error",
    [
        (None, "r52_0a_atcm_global@eba00000",
         "missing a valid power-domains property"),
        # Not an ATCM, BTCM or CTCM bank.
        (0x44, "r52_tcm_alias@eba00000",
         "TCM node /axi/r52_tcm_alias@eba00000 is not an ATCM, BTCM or CTCM "
         "bank of a cortexr52 core"),
        # R52 cores have no DTCM.
        (0x44, "r52_0a_dtcm_global@eba00000",
         "TCM node /axi/r52_0a_dtcm_global@eba00000 is not an ATCM, BTCM or "
         "CTCM bank of a cortexr52 core"),
    ],
)
def test_remoteproc_v2_reports_invalid_tcm_mapping(
        monkeypatch, caplog, pd_id, node_name, expected_error):
    """A TCM node without a power domain or bank type is reported."""
    channel_info, tcm = _remoteproc_v2_fixture(pd_id, None, node_name)

    monkeypatch.setattr(
        openamp_xlnx, "determinte_rpu_core",
        lambda tree, cpu_config, remote_node: openamp_xlnx.RPU_CORE.RPU_0)
    monkeypatch.setattr(
        openamp_xlnx, "get_platform",
        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.VERSAL2)

    result = openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
        object(), channel_info, [tcm])

    assert result is False
    assert expected_error in caplog.text


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


def _r52_sdt_cores(prefix, cluster_bases, first_tcm_pd, core_pd):
    """SDT TCM banks of each R52 core, in RPU core order.

    Each R52 cluster's core 0 banks are at its base, +0x10000 and +0x20000,
    and core 1's 0x40000 above them; ATCM is 64 KB, BTCM and CTCM 32 KB.
    Each core's three banks have consecutive power-domain IDs.
    """
    cores = []
    for cluster, base in enumerate(cluster_bases):
        letter = "abcde"[cluster]
        for position in range(2):
            core = 2 * cluster + position
            banks = []
            for index, bank in enumerate(("atcm", "btcm", "ctcm")):
                address = base + position * 0x40000 + index * 0x10000
                banks.append((
                    f"{prefix}{position}{letter}_{bank}_global@{address:x}",
                    address, 0x10000 if bank == "atcm" else 0x8000,
                    first_tcm_pd + 3 * core + index, bank))
            cores.append((core_pd(core), banks))
    return cores


# The SDT TCM banks of each RPU core, in RPU core order, with the core's
# power domain: (core power domain, [(node name, global address, size,
# power-domains ID, bank type), ...]).
_SDT_RPU_CORES = {
    openamp_xlnx.SOC_TYPE.ZYNQMP: [
        (0x7, [("psu_r5_0_atcm_global@ffe00000", 0xFFE00000, 0x10000, 15,
                "atcm"),
               ("psu_r5_0_btcm_global@ffe20000", 0xFFE20000, 0x10000, 16,
                "btcm")]),
        (0x8, [("psu_r5_1_atcm_global@ffe90000", 0xFFE90000, 0x10000, 17,
                "atcm"),
               ("psu_r5_1_btcm_global@ffeb0000", 0xFFEB0000, 0x10000, 18,
                "btcm")]),
    ],
    openamp_xlnx.SOC_TYPE.VERSAL: [
        (0x18110005,
         [("psv_r5_0_atcm_global@ffe00000", 0xFFE00000, 0x10000,
           0x1831800B, "atcm"),
          ("psv_r5_0_btcm_global@ffe20000", 0xFFE20000, 0x10000,
           0x1831800C, "btcm")]),
        (0x18110006,
         [("psv_r5_1_atcm_global@ffe90000", 0xFFE90000, 0x10000,
           0x1831800D, "atcm"),
          ("psv_r5_1_btcm_global@ffeb0000", 0xFFEB0000, 0x10000,
           0x1831800E, "btcm")]),
    ],
    openamp_xlnx.SOC_TYPE.VERSAL_NET: _r52_sdt_cores(
        "psx_r52_", [0xEBA00000, 0xEBA80000], 0x183180CB,
        lambda core: 0x181100BF + core),
    openamp_xlnx.SOC_TYPE.VERSAL2: _r52_sdt_cores(
        "r52_", [0xEBA00000, 0xEBA80000, 0xEBB00000, 0xEBB80000,
                 0xEBC00000], 0x44, lambda core: core),
}

# Linux remoteproc binding values and core-local TCM addresses per family.
_R52_LOCAL = {"atcm": 0x0, "btcm": 0x10000, "ctcm": 0x18000}
_REMOTEPROC_BINDING = {
    openamp_xlnx.SOC_TYPE.ZYNQMP: ("xlnx,zynqmp-r5fss", "xlnx,zynqmp-r5f",
                                   "r5f", {"atcm": 0x0, "btcm": 0x20000},
                                   [0xFFE00000]),
    openamp_xlnx.SOC_TYPE.VERSAL: ("xlnx,versal-r5fss", "xlnx,versal-r5f",
                                   "r5f", {"atcm": 0x0, "btcm": 0x20000},
                                   [0xFFE00000]),
    openamp_xlnx.SOC_TYPE.VERSAL_NET: ("xlnx,versal-net-r52fss",
                                       "xlnx,versal-net-r52f", "r52f",
                                       _R52_LOCAL,
                                       [0xEBA00000, 0xEBA80000]),
    openamp_xlnx.SOC_TYPE.VERSAL2: ("xlnx,versal-net-r52fss",
                                    "xlnx,versal2-r52f", "r52f", _R52_LOCAL,
                                    [0xEBA00000, 0xEBA80000, 0xEBB00000,
                                     0xEBB80000, 0xEBC00000]),
}


def _expected_reg_name(platform, core, bank):
    """reg-names Lopper gives a bank.

    R5 core 1's banks are named atcm1 and btcm1 in split mode too; the
    binding wants atcm0 and btcm0 (CR filed separately).
    """
    if platform in (openamp_xlnx.SOC_TYPE.ZYNQMP,
                    openamp_xlnx.SOC_TYPE.VERSAL):
        return f"{bank}{core % 2}"
    return f"{bank}0"


@pytest.mark.parametrize(
    "platform, cores",
    [
        # ZynqMP and Versal R5 in split mode: both cores, core 0 only, and
        # core 1 only.
        (openamp_xlnx.SOC_TYPE.ZYNQMP, [0, 1]),
        (openamp_xlnx.SOC_TYPE.ZYNQMP, [0]),
        (openamp_xlnx.SOC_TYPE.ZYNQMP, [1]),
        (openamp_xlnx.SOC_TYPE.VERSAL, [0, 1]),
        (openamp_xlnx.SOC_TYPE.VERSAL, [1]),
        # Versal NET: both clusters fully used, each core alone, and core 1
        # added before core 0.
        (openamp_xlnx.SOC_TYPE.VERSAL_NET, [0, 1, 2, 3]),
        (openamp_xlnx.SOC_TYPE.VERSAL_NET, [1]),
        (openamp_xlnx.SOC_TYPE.VERSAL_NET, [2]),
        (openamp_xlnx.SOC_TYPE.VERSAL_NET, [3]),
        (openamp_xlnx.SOC_TYPE.VERSAL_NET, [3, 2]),
        # Versal2: all five clusters fully used, and single cores.
        (openamp_xlnx.SOC_TYPE.VERSAL2, list(range(10))),
        (openamp_xlnx.SOC_TYPE.VERSAL2, [1]),
        (openamp_xlnx.SOC_TYPE.VERSAL2, [4]),
        (openamp_xlnx.SOC_TYPE.VERSAL2, [9]),
    ],
)
def test_remoteproc_v2_split_clusters(monkeypatch, platform, cores):
    """Split RPU cores load all their TCM banks, in their own cluster.

    Each core's ranges, reg, reg-names and power-domains are checked against
    the SDT bank addresses and the Linux binding's core-local layout.
    """
    tree = LopperTree()
    axi = LopperNode(-1, "/axi")
    axi["#address-cells"] = [2]
    axi["#size-cells"] = [2]
    tree + axi
    monkeypatch.setattr(
        openamp_xlnx, "get_platform", lambda tree, verbose=0: platform)
    (cluster_compatible, core_compatible, core_name, local,
     cluster_bases) = _REMOTEPROC_BINDING[platform]

    expected_ranges = {}
    for core in cores:
        core_pd, banks = _SDT_RPU_CORES[platform][core]
        info = {"remote_node": _rpu_remote(core, core_pd=core_pd)}
        assert openamp_xlnx.xlnx_remoteproc_rpu_parse(tree, None, info, [])
        nodes = [_tcm_node(tree, name, size, pd_id)
                 for name, _, size, pd_id, _ in banks]

        node = openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
            tree, info, nodes)

        cluster_path = f"/remoteproc@{cluster_bases[core // 2]:x}"
        position = core % 2
        assert node.abs_path == f"{cluster_path}/{core_name}@{position}"
        assert node.propval("compatible", list) == [core_compatible]
        assert node.propval("power-domains", list) == [0xA5, core_pd] + [
            cell for _, _, _, pd_id, _ in banks for cell in (0xA5, pd_id)]
        assert node.propval("reg", list) == [
            cell for _, _, size, _, bank in banks
            for cell in (position, local[bank], 0, size)]
        assert node.propval("reg-names", list) == [
            _expected_reg_name(platform, core, bank)
            for _, _, _, _, bank in banks]
        expected_ranges.setdefault(cluster_path, []).extend(
            cell for _, address, size, _, bank in banks
            for cell in (position, local[bank], 0, address, 0, size))

    clusters = [n for n in tree["/"].subnodes(children_only=True)
                if n.name.startswith("remoteproc@")]
    assert sorted(n.abs_path for n in clusters) == sorted(expected_ranges)
    for cluster in clusters:
        assert cluster.propval("compatible", list) == [cluster_compatible]
        assert cluster.propval("xlnx,cluster-mode", list) == [0]
        assert cluster.propval("ranges", list) == \
            expected_ranges[cluster.abs_path]
        if core_name == "r5f":
            assert cluster.propval("xlnx,tcm-mode", list) == [0]
        else:
            assert cluster.propval("xlnx,tcm-mode", list) == [""]


def test_remoteproc_v2_uses_sdt_core_local_tcm_address(monkeypatch):
    """A core-local TCM address from the SDT address-map is used.

    Only entries that map a bank somewhere other than its global address
    are the core's view; a cluster that maps the bank at its global address
    falls back to the family layout.
    """
    tree = LopperTree()
    axi = LopperNode(-1, "/axi")
    axi["#address-cells"] = [2]
    axi["#size-cells"] = [2]
    tree + axi
    monkeypatch.setattr(
        openamp_xlnx, "get_platform",
        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.ZYNQMP)
    remote = _rpu_remote(1, core_pd=0x8)
    # ATCM mapped at a made-up core address 0x8000; BTCM mapped at its
    # global address, as Versal SDTs do.
    remote["rpu_tcm_view"] = [17, 0x8000, 0x10000, 18, 0xFFEB0000, 0x10000]
    info = {"remote_node": remote}
    assert openamp_xlnx.xlnx_remoteproc_rpu_parse(tree, None, info, [])
    nodes = [_tcm_node(tree, "psu_r5_1_atcm_global@ffe90000", 0x10000, 17),
             _tcm_node(tree, "psu_r5_1_btcm_global@ffeb0000", 0x10000, 18)]

    node = openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
        tree, info, nodes)

    assert node.propval("reg", list) == [
        1, 0x8000, 0, 0x10000, 1, 0x20000, 0, 0x10000]


def test_remoteproc_v2_remote_without_tcm_uses_sdt_cluster_base(monkeypatch):
    """A remote that loads no TCM still joins its RPU cluster's node."""
    tree = LopperTree()
    monkeypatch.setattr(
        openamp_xlnx, "get_platform",
        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.VERSAL2)
    remote = _rpu_remote(7)
    remote["rpu_cluster_base"] = [0xEBB80000]
    info = {"remote_node": remote}
    assert openamp_xlnx.xlnx_remoteproc_rpu_parse(tree, None, info, [])

    node = openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(tree, info, [])

    assert node.abs_path == "/remoteproc@ebb80000/r52f@1"
    assert tree["/remoteproc@ebb80000"].propval("ranges", list) == []


def test_remoteproc_v2_remote_without_tcm_needs_cluster_base(
        monkeypatch, caplog):
    """Without TCM or an SDT cluster base, the cluster is unknown."""
    tree = LopperTree()
    monkeypatch.setattr(
        openamp_xlnx, "get_platform",
        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.VERSAL2)
    info = {"remote_node": _rpu_remote(7)}
    assert openamp_xlnx.xlnx_remoteproc_rpu_parse(tree, None, info, [])

    assert openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
        tree, info, []) is False
    assert ("no TCM bank or RPU cluster base found for remote /domains/RPU"
            in caplog.text)


def test_remoteproc_v2_rejects_tcm_of_two_clusters(monkeypatch, caplog):
    """A remote whose TCM banks are in two RPU clusters fails."""
    tree = LopperTree()
    axi = LopperNode(-1, "/axi")
    axi["#address-cells"] = [2]
    axi["#size-cells"] = [2]
    tree + axi
    monkeypatch.setattr(
        openamp_xlnx, "get_platform",
        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.VERSAL_NET)
    info = {"remote_node": _rpu_remote(1)}
    assert openamp_xlnx.xlnx_remoteproc_rpu_parse(tree, None, info, [])
    nodes = [_tcm_node(tree, "psx_r52_1a_atcm_global@eba40000", 0x10000,
                       0x183180CE),
             _tcm_node(tree, "psx_r52_0b_atcm_global@eba80000", 0x10000,
                       0x183180D1)]

    assert openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
        tree, info, nodes) is False
    assert ("the TCM banks of remote /domains/RPU are in more than one RPU "
            "cluster: 0xeba00000, 0xeba80000") in caplog.text


def test_remoteproc_v2_rejects_second_relation_for_core(monkeypatch, caplog):
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
            "have only one remoteproc relation") in caplog.text


_MALFORMED_TCM = "is malformed. Fix the power-domains of these nodes"


@pytest.mark.parametrize(
    "platform, core_pd, banks, malformed",
    [
        # The 2026.2 ZCU102 and K24c SDTs give the lockstep banks core 0's
        # power domains (15, 16) where Linux expects core 1's (17, 18).
        (openamp_xlnx.SOC_TYPE.ZYNQMP, 0x7,
         [("psu-r5-0-atcm-global@ffe00000", 15),
          ("psu-r5-0-btcm-global@ffe20000", 16),
          ("psu-r5-0-atcm-lockstep@ffe10000", 15),
          ("psu-r5-0-btcm-lockstep@ffe30000", 16)], True),
        # The same SDT once fixed.
        (openamp_xlnx.SOC_TYPE.ZYNQMP, 0x7,
         [("psu-r5-0-atcm-global@ffe00000", 15),
          ("psu-r5-0-btcm-global@ffe20000", 16),
          ("psu-r5-0-atcm-lockstep@ffe10000", 17),
          ("psu-r5-0-btcm-lockstep@ffe30000", 18)], False),
        # The 2026.2 VRK165 and VPK360 SDTs, and once fixed.
        (openamp_xlnx.SOC_TYPE.VERSAL, 0x18110005,
         [("psv_r5_0_atcm_global@ffe00000", 0x1831800B),
          ("psv_r5_0_btcm_global@ffe20000", 0x1831800C),
          ("psv_r5_0_atcm_lockstep@ffe10000", 0x1831800B),
          ("psv_r5_0_btcm_lockstep@ffe30000", 0x1831800C)], True),
        (openamp_xlnx.SOC_TYPE.VERSAL, 0x18110005,
         [("psv_r5_0_atcm_global@ffe00000", 0x1831800B),
          ("psv_r5_0_btcm_global@ffe20000", 0x1831800C),
          ("psv_r5_0_atcm_lockstep@ffe10000", 0x1831800D),
          ("psv_r5_0_btcm_lockstep@ffe30000", 0x1831800E)], False),
    ],
)
def test_remoteproc_v2_r5_lockstep_copies_sdt_banks(
        monkeypatch, caplog, platform, core_pd, banks, malformed):
    """R5 lockstep maps the SDT's banks as the binding's lockstep example.

    The xlnx,zynqmp-r5fss lockstep example: ATCM and BTCM at local 0x0 and
    0x20000 (global 0xffe00000, 0xffe20000), and the second ATCM and BTCM
    at local 0x10000 and 0x30000 (global 0xffe10000, 0xffe30000), all in
    bank 0. Power domains are copied from the SDT; when the SDT repeats
    one, the output is malformed and Lopper says so.
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
    nodes = [_tcm_node(tree, name, 0x10000, pd_id) for name, pd_id in banks]

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
    assert core.propval("power-domains", list) == [0xA5, core_pd] + [
        cell for _, pd_id in banks for cell in (0xA5, pd_id)]
    warnings = [r.getMessage() for r in caplog.records
                if _MALFORMED_TCM in r.getMessage()]
    if malformed:
        assert len(warnings) == 2
        assert (f"TCM node /axi/{banks[2][0]} has the same power domain "
                f"({hex(banks[2][1])}) as TCM node /axi/{banks[0][0]}; the "
                "remoteproc node for remote /domains/RPU lists it twice and "
                "is malformed") in warnings[0]
    else:
        assert warnings == []


@pytest.mark.parametrize(
    "platform, core",
    [
        (openamp_xlnx.SOC_TYPE.VERSAL_NET, 0),
        (openamp_xlnx.SOC_TYPE.VERSAL_NET, 2),
        (openamp_xlnx.SOC_TYPE.VERSAL2, 4),
        (openamp_xlnx.SOC_TYPE.VERSAL2, 8),
    ],
)
def test_remoteproc_v2_r52_lockstep_uses_core_0_banks(
        monkeypatch, caplog, platform, core):
    """R52 cores do not combine TCM: lockstep core 0 loads its own banks.

    The banks and their addresses are those of split mode; only the
    cluster mode changes.
    """
    tree = LopperTree()
    axi = LopperNode(-1, "/axi")
    axi["#address-cells"] = [2]
    axi["#size-cells"] = [2]
    tree + axi
    monkeypatch.setattr(
        openamp_xlnx, "get_platform", lambda tree, verbose=0: platform)
    (cluster_compatible, core_compatible, _, local,
     cluster_bases) = _REMOTEPROC_BINDING[platform]
    core_pd, banks = _SDT_RPU_CORES[platform][core]
    info = {"remote_node": _rpu_remote(core, config="lockstep",
                                       core_pd=core_pd)}
    assert openamp_xlnx.xlnx_remoteproc_rpu_parse(tree, None, info, [])
    nodes = [_tcm_node(tree, name, size, pd_id)
             for name, _, size, pd_id, _ in banks]

    node = openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
        tree, info, nodes)

    cluster_path = f"/remoteproc@{cluster_bases[core // 2]:x}"
    cluster = tree[cluster_path]
    assert cluster.propval("compatible", list) == [cluster_compatible]
    assert cluster.propval("xlnx,cluster-mode", list) == [1]
    assert cluster.propval("xlnx,tcm-mode", list) == [""]
    assert cluster.propval("ranges", list) == [
        cell for _, address, size, _, bank in banks
        for cell in (0, local[bank], 0, address, 0, size)]
    assert node.abs_path == f"{cluster_path}/r52f@0"
    assert node.propval("compatible", list) == [core_compatible]
    assert node.propval("reg", list) == [
        cell for _, _, size, _, bank in banks
        for cell in (0, local[bank], 0, size)]
    assert node.propval("reg-names", list) == ["atcm0", "btcm0", "ctcm0"]
    assert _MALFORMED_TCM not in caplog.text


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
        monkeypatch, caplog, relations, expected_error):
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
    assert expected_error in caplog.text


def test_remoteproc_v2_warns_on_power_domain_listed_twice(
        monkeypatch, caplog):
    """Two TCM nodes with one power domain still give output, with a
    warning that it is malformed because of the SDT."""
    tree = LopperTree()
    axi = LopperNode(-1, "/axi")
    axi["#address-cells"] = [2]
    axi["#size-cells"] = [2]
    tree + axi
    monkeypatch.setattr(
        openamp_xlnx, "get_platform",
        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.VERSAL2)
    info = {"remote_node": _rpu_remote(0)}
    assert openamp_xlnx.xlnx_remoteproc_rpu_parse(tree, None, info, [])
    nodes = [_tcm_node(tree, "r52_0a_atcm_global@eba00000", 0x10000, 0x44),
             _tcm_node(tree, "r52_0a_btcm_global@eba10000", 0x8000, 0x44)]

    core = openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
        tree, info, nodes)

    assert core.propval("power-domains", list) == [
        0xA5, 0xC0, 0xA5, 0x44, 0xA5, 0x44]
    assert ("TCM node /axi/r52_0a_btcm_global@eba10000 has the same power "
            "domain (0x44) as TCM node /axi/r52_0a_atcm_global@eba00000; "
            "the remoteproc node for remote /domains/RPU lists it twice and "
            "is malformed. Fix the power-domains of these nodes in the "
            "system device tree.") in caplog.text


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


def test_rpmsg_rejects_second_relation_for_core(caplog):
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
            caplog.text)


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
