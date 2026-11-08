"""
Tests for RPU sleep and tick timers selected by domain properties.

Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: BSD-3-Clause
"""

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from lopper.assists import (
    openamp_xlnx,
    openamp_xlnx_timers as timers,
    yaml_to_dts_expansion,
)
from lopper.tree import LopperNode, LopperTree

REPO_ROOT = Path(__file__).resolve().parent.parent

TTC_BASES = [0xff0e0000, 0xff0f0000, 0xff100000, 0xff110000]


def _node(tree, path, **props):
    node = LopperNode(-1, path)
    tree + node
    for name, value in props.items():
        node[name.replace("_", "-")] = value
    return node


class System:
    """A small Versal-like tree: four TTCs, an APU and two RPU domains."""

    def __init__(self):
        tree = LopperTree()
        self.tree = tree
        tree + LopperNode(-1, "/axi")
        tree["/axi"]["#address-cells"] = [2]
        tree["/axi"]["#size-cells"] = [2]
        self.ttc = []
        for index, base in enumerate(TTC_BASES):
            node = _node(tree, f"/axi/timer@{base:x}",
                         compatible="cdns,ttc", status="okay",
                         reg=[0, base, 0, 0x1000])
            node["xlnx,name"] = f"CIPS_0_pspmc_0_psv_ttc_{index}"
            node.label = f"ttc{index}"
            node.phandle_or_create()
            self.ttc.append(node)

        tree + LopperNode(-1, "/domains")
        self.apu_cluster = _node(tree, "/cpus-a72@0")
        self.apu_cluster.phandle_or_create()
        self.rpu_cluster = _node(tree, "/cpus-r5@0")
        self.rpu_cluster.phandle_or_create()

        self.apu = self.domain("APU_Linux", "linux", self.apu_cluster)
        self.r5_0 = self.domain("R5_0_FREERTOS", "freertos", self.rpu_cluster)
        self.r5_1 = self.domain("R5_1_BAREMETAL", "baremetal",
                                self.rpu_cluster, mask=0x2)
        tree.sync()

    def domain(self, name, os_type, cluster, mask=0x1):
        node = _node(self.tree, f"/domains/{name}")
        node["os,type"] = os_type
        node["cpus"] = [cluster.phandle, mask, 0]
        node.phandle_or_create()
        return node

    def relation(self, domain, compatible, timers, side="host", peer=None):
        """Add a relation child naming ``timers`` to ``domain``."""
        group_name = {"openamp,rpmsg-v1": "rpmsg-relation",
                      "libmetal,ipc-v1": "libmetal-relation"}[compatible]
        d2d = f"{domain.abs_path}/domain-to-domain"
        if d2d not in self.tree:
            _node(self.tree, d2d,
                  compatible="openamp,domain-to-domain-v1")
        group = f"{d2d}/{group_name}"
        if group not in self.tree:
            _node(self.tree, group, compatible=compatible)
        index = len(self.tree[group].subnodes(children_only=True)) - 1
        relation = _node(self.tree, f"{group}/relation{max(index, 0)}")
        relation[side] = [(peer or self.apu).phandle]
        relation["timer"] = [t.phandle for t in timers]
        self.tree.sync()
        return relation

    def set_timer(self, domain, role, ttc, counter):
        domain[timers.ROLE_PROPERTIES[role]] = [ttc.phandle, counter]
        self.tree.sync()

    def resolve(self, domain):
        return timers.resolve_domain_timers(self.tree, domain)


@pytest.fixture
def system():
    return System()


def _addresses(result):
    return {role: choice.address for role, choice in result.choices.items()}


def test_explicit_properties_select_counters(system):
    system.set_timer(system.r5_0, timers.SLEEP, system.ttc[3], 0)
    system.set_timer(system.r5_0, timers.TICK, system.ttc[3], 1)

    result = system.resolve(system.r5_0)

    assert not result.errors and not result.warnings
    assert _addresses(result) == {"sleep": 0xff110000, "tick": 0xff110004}
    assert result.choices["tick"].instance == "CIPS_0_pspmc_0_psv_ttc_10"
    source = timers.render_timer_config(result)
    assert "UINTPTR XSleepTimer_Instance = 0xff110000U;" in source
    assert "UINTPTR XTickTimer_Instance = 0xff110004U;" in source
    assert "weak" not in source.split("*/")[-1]


def test_rpmsg_relation_timer_is_the_fallback(system):
    system.relation(system.r5_0, "openamp,rpmsg-v1", [system.ttc[3]])

    result = system.resolve(system.r5_0)

    assert result.fallback and not result.errors
    assert _addresses(result) == {"sleep": 0xff110000, "tick": 0xff110004}
    assert "RPMsg relation timer" in result.choices["sleep"].source


def test_baremetal_fallback_selects_only_sleep(system):
    system.relation(system.r5_1, "openamp,rpmsg-v1", [system.ttc[3]])

    result = system.resolve(system.r5_1)

    assert _addresses(result) == {"sleep": 0xff110000}
    assert "XTickTimer_Instance" not in timers.render_timer_config(result)


def test_explicit_properties_win_over_relation(system):
    system.relation(system.r5_0, "openamp,rpmsg-v1", [system.ttc[3]])
    system.set_timer(system.r5_0, timers.SLEEP, system.ttc[1], 2)

    result = system.resolve(system.r5_0)

    assert not result.fallback
    assert _addresses(result) == {"sleep": 0xff0f0008}


def test_no_timer_named_selects_nothing(system):
    result = system.resolve(system.r5_0)
    assert not result.choices and not result.errors and not result.warnings


def test_libmetal_relation_timer_is_never_a_fallback(system):
    system.relation(system.r5_1, "libmetal,ipc-v1", [system.ttc[2]])
    assert not system.resolve(system.r5_1).choices


@pytest.mark.parametrize("domain, role", [
    ("r5_1", timers.TICK),
    ("apu", timers.SLEEP),
])
def test_unsupported_os_type_is_an_error(system, domain, role):
    node = getattr(system, domain)
    system.set_timer(node, role, system.ttc[3], 1)

    result = system.resolve(node)

    assert not result.choices and not result.warnings
    assert "is not supported for os,type" in result.errors[0]


def test_unsupported_role_prevents_partial_output(system):
    system.set_timer(system.r5_1, timers.SLEEP, system.ttc[3], 0)
    system.set_timer(system.r5_1, timers.TICK, system.ttc[3], 1)
    result = system.resolve(system.r5_1)
    assert result.errors and not result.choices


def test_unresolved_label_is_an_error(system):
    system.r5_0["xlnx,sleep-timer"] = ["ttc9", 0]
    result = system.resolve(system.r5_0)
    assert "names unknown timer 'ttc9'" in result.errors[0]
    assert not result.choices


def test_unresolved_phandle_is_an_error(system):
    system.r5_0["xlnx,sleep-timer"] = [0x7777, 0]
    assert "does not resolve" in system.resolve(system.r5_0).errors[0]


def test_bad_counter_is_an_error(system):
    system.set_timer(system.r5_0, timers.SLEEP, system.ttc[3], 3)
    assert "counter 3 is not valid" in system.resolve(system.r5_0).errors[0]


def test_unknown_compatible_is_an_error(system):
    system.ttc[3]["compatible"] = "xlnx,xps-timer-1.00.a"
    system.set_timer(system.r5_0, timers.SLEEP, system.ttc[3], 0)
    assert "no timer handler for compatible xlnx,xps-timer-1.00.a" in \
        system.resolve(system.r5_0).errors[0]


def test_uio_timer_is_an_error(system):
    system.ttc[2]["compatible"] = "uio"
    system.set_timer(system.r5_0, timers.SLEEP, system.ttc[2], 0)
    assert "Linux UIO device" in system.resolve(system.r5_0).errors[0]


def test_timer_outside_access_list_is_an_error(system):
    system.r5_0["access"] = [system.ttc[0].phandle, 0]
    system.set_timer(system.r5_0, timers.SLEEP, system.ttc[3], 0)
    assert "access list that does not include" in \
        system.resolve(system.r5_0).errors[0]


def test_timer_inside_access_list_is_accepted(system):
    system.r5_0["access"] = [system.ttc[3].phandle, 0]
    system.set_timer(system.r5_0, timers.SLEEP, system.ttc[3], 0)
    assert not system.resolve(system.r5_0).errors


def test_timer_in_other_domain_access_is_an_error(system):
    system.apu["access"] = [system.ttc[3].phandle, 0]
    system.set_timer(system.r5_0, timers.SLEEP, system.ttc[3], 0)
    assert "APU_Linux access" in system.resolve(system.r5_0).errors[0]


def test_timer_of_another_domain_property_is_an_error(system):
    system.set_timer(system.r5_1, timers.SLEEP, system.ttc[3], 2)
    system.set_timer(system.r5_0, timers.SLEEP, system.ttc[3], 0)
    assert "R5_1_BAREMETAL xlnx,sleep-timer" in \
        system.resolve(system.r5_0).errors[0]


def test_timer_of_a_libmetal_relation_is_an_error(system):
    # The domain's own Libmetal timer is shared with Linux.
    system.relation(system.r5_0, "libmetal,ipc-v1", [system.ttc[2]])
    system.set_timer(system.r5_0, timers.SLEEP, system.ttc[2], 0)
    assert "libmetal-relation/relation0 timer" in \
        system.resolve(system.r5_0).errors[0]


def test_timer_of_host_rpmsg_relation_is_an_error(system):
    system.relation(system.apu, "openamp,rpmsg-v1", [system.ttc[0]],
                    side="remote", peer=system.r5_0)
    system.set_timer(system.r5_0, timers.SLEEP, system.ttc[0], 0)
    assert "APU_Linux/domain-to-domain" in \
        system.resolve(system.r5_0).errors[0]


def test_same_counter_for_sleep_and_tick_is_an_error(system):
    system.set_timer(system.r5_0, timers.SLEEP, system.ttc[3], 1)
    system.set_timer(system.r5_0, timers.TICK, system.ttc[3], 1)
    result = system.resolve(system.r5_0)
    assert "both use ttc3 counter 1" in result.errors[0]
    assert not result.choices


def test_fallback_problem_is_a_warning(system):
    system.relation(system.r5_0, "openamp,rpmsg-v1", [system.ttc[2]])
    system.relation(system.r5_1, "libmetal,ipc-v1", [system.ttc[2]])

    result = system.resolve(system.r5_0)

    assert not result.errors and not result.choices
    assert "the BSP timers apply" in result.warnings[0]


def test_bsp_default_used_elsewhere_warns(system):
    system.relation(system.r5_0, "openamp,rpmsg-v1", [system.ttc[3]])
    system.ttc[2]["compatible"] = "uio"

    result = system.resolve(system.r5_1)
    warnings = timers.bsp_timer_warnings(
        system.tree, result, {"sleep": 0xff110000, "tick": 0xff100000})

    assert len(warnings) == 2
    assert "sleep timer 0xff110000 (ttc3)" in warnings[0]
    assert "R5_0_FREERTOS/domain-to-domain/rpmsg-relation" in warnings[0]
    assert "Linux UIO" in warnings[1]


def test_bsp_default_not_checked_for_selected_role(system):
    system.relation(system.r5_0, "openamp,rpmsg-v1", [system.ttc[3]])
    result = system.resolve(system.r5_0)
    assert not timers.bsp_timer_warnings(
        system.tree, result, {"sleep": 0xff110000})


def test_linux_disables_only_property_timers(system):
    system.relation(system.r5_1, "openamp,rpmsg-v1", [system.ttc[1]])
    system.set_timer(system.r5_0, timers.SLEEP, system.ttc[3], 0)
    sdt = type("FakeSdt", (), {"tree": system.tree})()

    assert openamp_xlnx.xlnx_openamp_disable_domain_timers(sdt)

    assert system.ttc[3].propval("status", list) == ["disabled"]
    assert system.ttc[1].propval("status", list) == ["okay"]


def test_linux_fails_on_invalid_property(system):
    system.set_timer(system.r5_0, timers.SLEEP, system.ttc[3], 4)
    sdt = type("FakeSdt", (), {"tree": system.tree})()
    assert not openamp_xlnx.xlnx_openamp_disable_domain_timers(sdt)
    assert system.ttc[3].propval("status", list) == ["okay"]


def test_yaml_labels_expand_to_phandles(system):
    system.r5_0["xlnx,sleep-timer"] = ["ttc3", "0"]
    system.r5_0["xlnx,tick-timer"] = ["&ttc3", "1"]

    assert yaml_to_dts_expansion.domain_timers_expand(
        system.tree, system.r5_0)

    assert system.r5_0.propval("xlnx,sleep-timer", list) == [
        system.ttc[3].phandle, 0]
    assert system.r5_0.propval("xlnx,tick-timer", list) == [
        system.ttc[3].phandle, 1]


def test_yaml_unknown_label_is_left_for_the_checks(system):
    system.r5_0["xlnx,sleep-timer"] = ["ttc9", "0"]

    assert not yaml_to_dts_expansion.domain_timers_expand(
        system.tree, system.r5_0)

    assert "names unknown timer 'ttc9'" in \
        system.resolve(system.r5_0).errors[0]


TTC_SPLIT_DTS = textwrap.dedent("""\
    /dts-v1/;
    / {
        #address-cells = <2>;
        #size-cells = <2>;
        model = "ttc split test";
        axi {
            compatible = "simple-bus";
            #address-cells = <2>;
            #size-cells = <2>;
            ranges;
            gic: interrupt-controller@f9000000 {
                compatible = "arm,gic-v3";
                #interrupt-cells = <3>;
                interrupt-controller;
                reg = <0x0 0xf9000000 0x0 0x80000>;
            };
    %s
        };
    };
    """)

TTC_NODE = """\
            ttc%(n)d: timer@%(base)x {
                compatible = "cdns,ttc";
                status = "okay";
                interrupt-parent = <&gic>;
                interrupts = <0 %(i0)d 4>, <0 %(i1)d 4>, <0 %(i2)d 4>;
                reg = <0x0 0x%(base)x 0x0 0x1000>;
                xlnx,name = "CIPS_0_pspmc_0_psv_ttc_%(n)d";
            };
"""


def test_ttc_counters_match_lop_ttc_split(tmp_path):
    """Counter addresses and names equal the nodes lop-ttc-split makes."""
    nodes = "".join(TTC_NODE % {"n": n, "base": base, "i0": 40 + 3 * n,
                                "i1": 41 + 3 * n, "i2": 42 + 3 * n}
                    for n, base in enumerate(TTC_BASES))
    source = tmp_path / "ttc.dts"
    source.write_text(TTC_SPLIT_DTS % nodes)
    output = tmp_path / "ttc-split.dts"

    run = subprocess.run(
        [sys.executable, str(REPO_ROOT / "lopper.py"), "-f", "--symbols",
         "-i", str(REPO_ROOT / "lopper" / "lops" / "lop-ttc-split.dts"),
         str(source), str(output)],
        cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr

    split = {}
    for base, name in re.findall(
            r"timer@([0-9a-f]+) \{.*?xlnx,name = \"([^\"]+)\";",
            output.read_text(), re.S):
        split[int(base, 16)] = name
    assert len(split) == 3 * len(TTC_BASES)

    for n, base in enumerate(TTC_BASES):
        tree = LopperTree()
        tree + LopperNode(-1, "/axi")
        tree["/axi"]["#address-cells"] = [2]
        tree["/axi"]["#size-cells"] = [2]
        ttc = _node(tree, f"/axi/timer@{base:x}", compatible="cdns,ttc",
                    reg=[0, base, 0, 0x1000])
        ttc["xlnx,name"] = f"CIPS_0_pspmc_0_psv_ttc_{n}"
        for counter in range(3):
            address, instance = timers.ttc_counter(ttc, counter)
            assert split[address] == instance
