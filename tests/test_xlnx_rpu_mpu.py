"""Tests for the standalone and FreeRTOS RPU MPU table generator."""

# Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from types import SimpleNamespace

import pytest

from lopper.assists import xlnx_rpu_mpu as mpu
from lopper.tree import LopperNode, LopperTree

R5_WB = "NORM_NSHARED_WB_WA | PRIV_RW_USER_RW"
R5_SO = "STRONG_ORDERD_SHARED | PRIV_RW_USER_RW"
DEV = "DEVICE_NONSHARED | PRIV_RW_USER_RW"
R52_WT = "NORM_NSHARED_WT_NWA | PRIV_RW_USER_RW"
SHARED_NC = "NORM_SHARED_NCACHE | PRIV_RW_USER_RW"
NSHARED_NC = "NORM_NSHARED_NCACHE | PRIV_RW_USER_RW"

# The tables below are transcribed from the standalone BSP:
# platform/ZynqMP/mpu_r5.c and platform/versal/mpu_r5.c Init_MPU(), and the
# weak InitialMpu_Config of platform/{versal-net,versal_2ve_2vm}/mpu_r52.c.
ZYNQMP_R5_DEVICES = [
    (0x80000000, 1 << 30, R5_SO),
    (0xC0000000, 512 << 20, DEV),
    (0xE0000000, 256 << 20, DEV),
    (0xF8000000, 16 << 20, DEV),
    (0xF9000000, 1 << 20, DEV),
    (0xFD000000, 16 << 20, DEV),
    (0xFE000000, 16 << 20, DEV),
    (0xFF000000, 16 << 20, DEV),
    (0xFFFC0000, 256 << 10, R5_WB),
]
VERSAL_R5_DEVICES = [
    (0x80000000, 1 << 30, R5_SO),
    (0xC0000000, 512 << 20, DEV),
    (0xE0000000, 256 << 20, DEV),
    (0xF0000000, 128 << 20, DEV),
    (0xF8000000, 16 << 20, DEV),
    (0xF9000000, 1 << 20, DEV),
    (0xFD000000, 16 << 20, DEV),
    (0xFE000000, 16 << 20, DEV),
    (0xFF000000, 16 << 20, DEV),
]
VERSAL_NET_R52 = [
    (0x00000000, 0x7FFFFFFF, R52_WT),
    (0x80000000, 0x1FFFFFFF, DEV),
    (0xA0000000, 0x17FFFFFF, DEV),
    (0xBBF00000, 0xFFFFF, R52_WT),
    (0xC0000000, 0x20FFFFFF, DEV),
    (0xE2000000, 0x1FFFFF, DEV),
    (0xE4000000, 0xFFFFFF, DEV),
    (0xE6000000, 0x7FFFFF, DEV),
    (0xEA000000, 0x2FFFFFF, DEV),
    (0xF0000000, 0x7FFFFFF, DEV),
    (0xF8000000, 0x3FFFFFF, DEV),
]
VERSAL_2VE_2VM_R52 = [
    (0x00000000, 0x7FFFFFFF, R52_WT),
    (0x80000000, 0x1FFFFFFF, DEV),
    (0xA0000000, 0x17FFFFFF, DEV),
    (0xBBE00000, 0x1FFFFF, R52_WT),
    (0xC0000000, 0x20FFFFFF, DEV),
    (0xE2000000, 0x1FFFFF, DEV),
    (0xE8000000, 0x7FFFFF, DEV),
    (0xEA000000, 0x3FFFFFF, DEV),
    (0xF0000000, 0xBFFFFFF, DEV),
]


def _triples(regions):
    return [(r.base, r.size, r.attribute) for r in regions]


@pytest.mark.parametrize("ddr, expected", [
    # zcu102 SDT: xmem_config.h has 0x100000..0x7ffffffe.
    ((0x100000, 0x7FFFFFFE), [(0x0, 2 << 30, R5_WB)]),
    ((0x100000, 0x2FFFFFFF), [(0x0, 1 << 30, R5_WB)]),
    # DDR not at 0x100000: own base plus a 256 KB TCM region at 0x0.
    ((0x0, 0x3FFFFFFF), [(0x0, 256 << 10, R5_WB), (0x0, 1 << 30, R5_WB)]),
    ((0x40000000, 0x5FFFFFFF),
     [(0x0, 256 << 10, R5_WB), (0x40000000, 512 << 20, R5_WB)]),
    # 32-bit XPAR arithmetic: a DDR ending at 0xffffffff has size 0.
    ((0x0, 0xFFFFFFFF), [(0x0, 256 << 10, R5_WB), (0x0, 32, R5_WB)]),
    (None, [(0x0, 256 << 10, R5_WB)]),
])
def test_zynqmp_r5_baseline_matches_bsp(ddr, expected):
    assert _triples(mpu.zynqmp_r5_baseline(ddr)) == \
        expected + ZYNQMP_R5_DEVICES


@pytest.mark.parametrize("ddr, aie, expected", [
    # vck190 SDT: xmem_config.h has 0x40000..0x7fffffff.
    ((0x40000, 0x7FFFFFFF), False, (2 << 30, [])),
    ((0x0, 0x2FFFFFFF), False, (1 << 30, [])),
    ((0x40000, 0x7FFFFFFF), True, (1 << 30, [(0x40000000, 1 << 30, DEV)])),
    (None, False, (256 << 10, [])),
])
def test_versal_r5_baseline_matches_bsp(ddr, aie, expected):
    ddr_size, aie_region = expected
    assert _triples(mpu.versal_r5_baseline(ddr, aie)) == (
        [(0x0, ddr_size, R5_WB)] + VERSAL_R5_DEVICES + aie_region +
        [(0xFFFC0000, 256 << 10, R5_WB)])


@pytest.mark.parametrize("soc, expected", [
    ("versal-net", VERSAL_NET_R52),
    ("versal_2ve_2vm", VERSAL_2VE_2VM_R52),
])
def test_r52_baseline_matches_bsp(soc, expected):
    assert [(r.base, r.size - 1, r.attribute)
            for r in mpu.baseline(soc)] == expected


def test_render_uses_bsp_size_encodings():
    r5 = mpu.render(mpu.baseline("zynqmp", (0x100000, 0x7FFFFFFE)),
                    "cortexr5", "zynqmp", "/domains/R5", "psu_cortexr5_0", 5)
    assert "\t\t0x00000000U,\n\t\tREGION_2G,\n\t\t" + R5_WB in r5
    assert "\t\t0xFFFC0000U,\n\t\tREGION_256K," in r5
    assert "section(\".bootdata\")" in r5
    r52 = mpu.render(mpu.baseline("versal_2ve_2vm"), "cortexr52",
                     "versal_2ve_2vm", "/domains/R52", "cortexr52_0", 3)
    assert "\t\t0x00000000U,\n\t\t0x7FFFFFFFU,\n\t\t" + R52_WT in r52
    assert "_TCM_" not in r52


def _tree(family, processor):
    """Build an assembled tree with one RPU domain and its carveouts."""
    tree = LopperTree()
    root = tree["/"]
    root["#address-cells"] = [2]
    root["#size-cells"] = [2]
    root["family"] = [family]
    cluster = LopperNode(-1, "/cpus-r@0")
    tree + cluster
    cpu = LopperNode(-1, "/cpus-r@0/cpu@0")
    tree + cpu
    cpu["compatible"] = [f"arm,cortex-{processor}"]
    cpu["reg"] = [0]
    cluster.phandle_or_create()
    reserved = LopperNode(-1, "/reserved-memory")
    tree + reserved
    reserved["#address-cells"] = [2]
    reserved["#size-cells"] = [2]
    tree + LopperNode(-1, "/domains")
    domain = LopperNode(-1, "/domains/RPU0")
    tree + domain
    domain["os,type"] = ["freertos"]
    domain["cpus"] = [cluster.phandle, 1, 0]
    tree.sync()
    return tree, domain


def _memory(tree, name, base, size, policy=None):
    node = LopperNode(-1, f"/reserved-memory/{name}@{base:x}")
    tree + node
    node["reg"] = [0, base, 0, size]
    if policy is not None:
        node["mpu-policy"] = policy
    node.phandle_or_create()
    return node


def _rpmsg(tree, domain, carveouts, compatible="openamp,rpmsg-v1",
           name="rel"):
    try:
        tree[f"{domain.abs_path}/domain-to-domain"]
    except KeyError:
        tree + LopperNode(-1, f"{domain.abs_path}/domain-to-domain")
    relation = LopperNode(-1, f"{domain.abs_path}/domain-to-domain/{name}")
    tree + relation
    relation["compatible"] = [compatible]
    channel = LopperNode(-1, f"{relation.abs_path}/relation0")
    tree + channel
    channel["carveouts"] = [node.phandle for node in carveouts]


def _openamp_domain(family, processor, buffer_size=0x78000):
    tree, domain = _tree(family, processor)
    rproc = _memory(tree, "rproc0", 0x9800000, 0x60000,
                    ["readable", "writable", "executable", "cacheable"])
    vrings = [_memory(tree, "vdev0vring0", 0x9880000, 0x4000),
              _memory(tree, "vdev0vring1", 0x9884000, 0x4000)]
    buffer = _memory(tree, "vdev0buffer", 0x9888000, buffer_size)
    domain["reserved-memory"] = [rproc.phandle]
    _rpmsg(tree, domain, vrings + [buffer])
    tree.sync()
    return tree, domain


def test_policy_attributes_use_bsp_macros():
    tree, domain = _tree("ZynqMP", "r5")
    node = _memory(tree, "shm", 0x9000000, 0x10000,
                   ["readable", "writable", "shareable", "userspace"])
    ro = _memory(tree, "ro", 0x9100000, 0x10000, ["readable", "cacheable"])
    domain["reserved-memory"] = [node.phandle, ro.phandle]
    tree.sync()
    opted_in, regions = mpu.domain_regions(tree, domain, "cortexr5")
    assert opted_in
    assert [r.attribute for r in regions] == [
        SHARED_NC, "NORM_NSHARED_WB_WA | PRIV_RO_USER_RO"]
    _, regions = mpu.domain_regions(tree, domain, "cortexr52")
    assert regions[1].attribute == "NORM_NSHARED_WT_NWA | PRIV_RO_USER_RO"


def test_no_mpu_policy_writes_nothing(tmp_path):
    tree, domain = _tree("Versal_2VE_2VM", "r52")
    vring = _memory(tree, "vdev0vring0", 0x9880000, 0x4000)
    _rpmsg(tree, domain, [vring])
    tree.sync()
    sdt = SimpleNamespace(tree=tree, outdir=str(tmp_path))
    assert mpu.generate_mpu_config(
        None, sdt, {"args": ["--domain=/domains/RPU0"]})
    assert not list(tmp_path.iterdir())


def test_r5_carveouts_merge_and_redundant_policy_is_dropped():
    tree, domain = _openamp_domain("ZynqMP", "r5")
    _, regions = mpu.domain_regions(tree, domain, "cortexr5")
    table = mpu.build_table("zynqmp", "cortexr5", regions,
                            (0x100000, 0x7FFFFFFE))
    # rproc0 already has the DDR attributes; the vrings and the buffer form
    # one aligned 512 KB region after the baseline, so it takes priority.
    assert len(table) == 11
    assert _triples(table[-1:]) == [(0x9880000, 0x80000, SHARED_NC)]
    mpu.check_fit(table, 5)


def test_r5_rejects_unaligned_carveout_with_aperture():
    tree, domain = _openamp_domain("ZynqMP", "r5", buffer_size=0x40000)
    _, regions = mpu.domain_regions(tree, domain, "cortexr5")
    with pytest.raises(mpu.MpuError) as error:
        mpu.build_table("zynqmp", "cortexr5", regions,
                        (0x100000, 0x7FFFFFFE))
    message = str(error.value)
    assert "vdev0buffer@9888000 [0x9888000, 0x98c8000)" in message
    assert "[0x9880000, 0x9900000)" in message


def test_free_regions_limit_lists_the_table():
    tree, domain = _openamp_domain("Versal", "r5")
    _, regions = mpu.domain_regions(tree, domain, "cortexr5")
    table = mpu.build_table("versal", "cortexr5", regions,
                            (0x40000, 0x7FFFFFFF))
    with pytest.raises(mpu.MpuError) as error:
        mpu.check_fit(table, mpu.DEFAULT_FREE_REGIONS["cortexr5"])
    message = str(error.value)
    assert "needs 12 regions and 5 must stay free" in message
    assert "11: 0x09880000-0x098fffff" in message
    mpu.check_fit(table, 4)


def test_r52_carves_ddr_around_carveouts(tmp_path):
    tree, domain = _openamp_domain("Versal_2VE_2VM", "r52",
                                   buffer_size=0x40000)
    rsc = _memory(tree, "rsc_table", 0x9700000, 0x1000)
    _rpmsg(tree, domain, [rsc], name="rsc")
    tree.sync()
    sdt = SimpleNamespace(tree=tree, outdir=str(tmp_path))
    assert mpu.generate_mpu_config(
        None, sdt, {"args": ["--domain=/domains/RPU0"]})
    text = (tmp_path / "amd_mpu_config.c").read_text()
    _, regions = mpu.domain_regions(tree, domain, "cortexr52")
    table = mpu.build_table("versal_2ve_2vm", "cortexr52", regions)
    assert _triples(table[:5]) == [
        (0x0, 0x9700000, R52_WT),
        (0x9700000, 0x1000, NSHARED_NC),
        (0x9701000, 0x9880000 - 0x9701000, R52_WT),
        (0x9880000, 0x48000, SHARED_NC),
        (0x98C8000, 0x80000000 - 0x98C8000, R52_WT),
    ]
    assert len(table) == 13
    for previous, current in zip(table, table[1:]):
        assert previous.end <= current.base
    assert "\t\t0x09880000U,\n\t\t0x47FFFU,\n\t\t" + SHARED_NC in text
    assert "_TCM_" not in text


def test_r52_rejects_unaligned_region():
    tree, domain = _tree("VersalNet", "r52")
    node = _memory(tree, "odd", 0x9000010, 0x100, ["readable", "writable"])
    domain["reserved-memory"] = [node.phandle]
    tree.sync()
    _, regions = mpu.domain_regions(tree, domain, "cortexr52")
    with pytest.raises(mpu.MpuError, match="multiples of 64 bytes"):
        mpu.build_table("versal-net", "cortexr52", regions)


def test_static_and_tcm_memories_get_no_region():
    tree, domain = _tree("ZynqMP", "r5")
    static = _memory(tree, "shm", 0x9000000, 0x10000,
                     ["readable", "writable", "static"])
    tcm = _memory(tree, "psu_r5_0_atcm_global", 0xFFE00000, 0x10000,
                  ["readable", "writable", "executable", "cacheable"])
    domain["reserved-memory"] = [static.phandle, tcm.phandle]
    tree.sync()
    assert mpu.domain_regions(tree, domain, "cortexr5") == (True, [])


def test_overlapping_policies_are_rejected():
    tree, domain = _tree("ZynqMP", "r5")
    first = _memory(tree, "a", 0x9000000, 0x10000, ["readable", "writable"])
    second = _memory(tree, "b", 0x9008000, 0x10000, ["readable", "writable"])
    domain["reserved-memory"] = [first.phandle, second.phandle]
    tree.sync()
    with pytest.raises(ValueError, match="MPU regions overlap"):
        mpu.domain_regions(tree, domain, "cortexr5")


def test_linux_domain_is_rejected():
    tree, domain = _tree("ZynqMP", "r5")
    domain["os,type"] = ["linux"]
    tree.sync()
    sdt = SimpleNamespace(tree=tree, outdir=".")
    with pytest.raises(SystemExit) as error:
        mpu.generate_mpu_config(
            None, sdt, {"args": ["--domain=/domains/RPU0"]})
    assert error.value.code == 1


def test_invalid_invocation_exits_nonzero(tmp_path):
    tree, _ = _tree("ZynqMP", "r5")
    sdt = SimpleNamespace(tree=tree, outdir=str(tmp_path))
    with pytest.raises(SystemExit) as error:
        mpu.generate_mpu_config(None, sdt, {"args": []})
    assert error.value.code == 1
    assert not list(tmp_path.iterdir())


def test_unexpected_input_error_is_fatal(monkeypatch):
    def missing_map(*args):
        raise KeyError("address-map")
    monkeypatch.setattr(mpu, "_generate_mpu_config", missing_map)
    with pytest.raises(SystemExit) as error:
        mpu.generate_mpu_config(None, None, {})
    assert error.value.code == 1


def test_invalid_memory_policy_exits_without_output(tmp_path):
    tree, domain = _tree("ZynqMP", "r5")
    memory = _memory(tree, "buffer", 0x9800000, 0x10000,
                     ["invalid-policy"])
    domain["reserved-memory"] = [memory.phandle]
    tree.sync()
    sdt = SimpleNamespace(tree=tree, outdir=str(tmp_path))
    with pytest.raises(SystemExit) as error:
        mpu.generate_mpu_config(
            None, sdt, {"args": ["--domain=/domains/RPU0"]})
    assert error.value.code == 1
    assert not list(tmp_path.iterdir())
