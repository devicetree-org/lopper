"""OpenAMP and libmetal relations with a MicroBlaze V remote.

A MicroBlaze V in the PL uses an AXI Mailbox below /amba_pl instead of a PS
IPI, runs its firmware from a dual-port BRAM whose host side is a top-level
SDT memory node, and reaches DDR through its own address map.
"""

# Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from lopper.assists import openamp_xlnx, openamp_xlnx_common, \
    yaml_to_dts_expansion
from lopper.tree import LopperNode, LopperProp, LopperTree


def _node(tree, path, label=None, **props):
    node = LopperNode(-1, path)
    tree + node
    # Label the node once it is in the tree, so the tree indexes the label.
    if label:
        node.label = label
    for name, value in props.items():
        node[name.replace("_", "-")] = value
    return node


def _relation_tree():
    """Reserved memory, a top-level BRAM node and a PL mailbox."""
    tree = LopperTree()
    vring0 = _node(tree, "/reserved-memory/vdev0vring0@9b00000",
                   label="mbv_vdev0vring0", reg=[0, 0x09b00000, 0, 0x4000])
    bram = _node(tree, "/memory@b0000000",
                 label="mbv_host_bram_ctrl_memory",
                 reg=[0, 0xb0000000, 0, 0x40000])
    mbox = _node(tree, "/amba_pl/mbv_mailbox_S0@b2060000",
                 label="mbv_mailbox_S0")
    ipi = _node(tree, "/axi/mailbox@eb3b0000", label="mbv_mailbox_S0_alias")
    relations = _node(tree, "/domains/APU_Linux/domain-to-domain/"
                      "rpmsg-relation")
    relation = _node(tree, relations.abs_path + "/relation0")
    return tree, relations, relation, vring0, bram, mbox, ipi


def test_relation_references_resolve_outside_axi():
    """Carveouts, top-level BRAM and PL mailboxes resolve to phandles."""
    tree, relations, relation, vring0, bram, mbox, _ = _relation_tree()
    relation["carveouts"] = ["vdev0vring0@9b00000"]
    relation["elfload"] = ["mbv_host_bram_ctrl_memory"]
    relation["mbox"] = "mbv_mailbox_S0"

    assert yaml_to_dts_expansion.resolve_carveouts(tree, relations,
                                                   "carveouts")
    assert yaml_to_dts_expansion.resolve_carveouts(tree, relations,
                                                   "elfload")
    assert yaml_to_dts_expansion.resolve_rpmsg_mbox(tree, relations)
    assert relation.propval("carveouts", list) == [vring0.phandle]
    assert relation.propval("elfload", list) == [bram.phandle]
    assert relation.propval("mbox", list) == [mbox.phandle]


def test_relation_references_prefer_ps_bus():
    """A name below /axi still wins over the same name elsewhere."""
    tree = LopperTree()
    ps = _node(tree, "/axi/mailbox@eb340000", label="ipi_mbox")
    _node(tree, "/amba_pl/mailbox@b2060000", label="ipi_mbox")
    assert yaml_to_dts_expansion._find_relation_reference(
        tree, "ipi_mbox") == [ps]


def test_missing_mailbox_fails_cleanly():
    """An unknown mailbox is an error, not an exception."""
    tree, relations, relation, *_ = _relation_tree()
    relation["mbox"] = "no_such_mailbox"
    assert not yaml_to_dts_expansion.resolve_rpmsg_mbox(tree, relations)


def test_libmetal_relation_without_timer_resolves_mailbox():
    """A libmetal relation needs no timer; its mailbox still resolves."""
    tree, _, _, _, _, mbox, _ = _relation_tree()
    _node(tree, "/domains/MBV_Remote")
    data = _node(tree, "/axi/mbv_libmetal_data@9b88000",
                 reg=[0, 0x09b88000, 0, 0x40000])
    relations = _node(tree, "/domains/APU_Linux/domain-to-domain/"
                      "libmetal-relation")
    relation = _node(tree, relations.abs_path + "/relation0")
    relation["remote"] = "MBV_Remote"
    relation["carveouts"] = ["mbv_libmetal_data@9b88000"]
    relation["mbox"] = "mbv_mailbox_S0"

    assert yaml_to_dts_expansion.xlnx_libmetal_expand(tree, relations)
    assert relation.propval("carveouts", list) == [data.phandle]
    assert relation.propval("mbox", list) == [mbox.phandle]


D2D = "/domains/APU_Linux/domain-to-domain"


def _mbv_linux_tree(rpmsg=True, libmetal=False):
    """A Linux domain with relations to one MicroBlaze V, as YAML expands it."""
    tree = LopperTree()
    tree["/"]["compatible"] = ["xlnx,versal2"]
    tree["/"]["#address-cells"] = [2]
    tree["/"]["#size-cells"] = [2]
    _node(tree, "/reserved-memory", **{"#address-cells": [2],
                                       "#size-cells": [2]})
    pl = _node(tree, "/amba_pl", **{"#address-cells": [2],
                                     "#size-cells": [2]})
    clk = _node(tree, "/amba_pl/misc_clk_0", label="misc_clk_0")
    _node(tree, "/amba_pl/mbv_host_bram_ctrl@b0000000",
          compatible=["xlnx,axi-bram-ctrl-4.1", "xlnx,axi-bram-ctrl"],
          reg=[0, 0xb0000000, 0, 0x40000], clocks=[clk.phandle_or_create()])
    gpio = _node(tree, "/amba_pl/gpio@b2050000", label="mbv_reset_gpio",
                 reg=[0, 0xb2050000, 0, 0x10000])
    gpio + LopperProp(name="gpio-controller")
    mbox = _node(tree, "/amba_pl/mbv_mailbox_S0@b2060000",
                 label="mbv_mailbox_S0", reg=[0, 0xb2060000, 0, 0x10000],
                 compatible=["xlnx,mailbox-2.1", "xlnx,mailbox"])
    mbox["xlnx,ip-name"] = "mailbox"
    bram = _node(tree, "/memory@b0000000",
                 compatible=["xlnx,axi-bram-ctrl-4.1", "xlnx,axi-bram-ctrl"],
                 reg=[0, 0xb0000000, 0, 0x40000])
    bram["device_type"] = "memory"
    carveouts = [
        _node(tree, "/reserved-memory/%s@%x" % (name, base),
              reg=[0, base, 0, size])
        for name, base, size in (("vdev0vring0", 0x09b00000, 0x4000),
                                 ("vdev0vring1", 0x09b04000, 0x4000),
                                 ("vdev0buffer", 0x09b08000, 0x78000))]
    remote = _node(tree, "/domains/MBV_Remote")
    linux = _node(tree, "/domains/APU_Linux")
    linux["os,type"] = "linux"
    _node(tree, D2D)
    rproc = _node(tree, D2D + "/remoteproc-relation",
                  compatible="openamp,remoteproc-v2")
    _node(tree, rproc.abs_path + "/relation0",
          remote=[remote.phandle_or_create()],
          elfload=[bram.phandle_or_create()],
          reset_controller=[gpio.phandle_or_create()],
          firmware_name="mbv_openamp_echo.elf")
    if rpmsg:
        group = _node(tree, D2D + "/rpmsg-relation",
                      compatible="openamp,rpmsg-v1")
        _node(tree, group.abs_path + "/relation0",
              remote=[remote.phandle], mbox=[mbox.phandle_or_create()],
              carveouts=[c.phandle_or_create() for c in carveouts])
    if libmetal:
        group = _node(tree, D2D + "/libmetal-relation",
                      compatible="libmetal,ipc-v1")
        _node(tree, group.abs_path + "/relation0",
              remote=[remote.phandle], mbox=[mbox.phandle_or_create()])
    return tree, pl, clk, gpio, mbox, carveouts


def _linux_dt(tree):
    """Run the Linux remoteproc, RPMsg and libmetal parsers, as
    xlnx_handle_relations does."""
    validate = []
    cores = openamp_xlnx.xlnx_remoteproc_parse(
        tree, tree[D2D + "/remoteproc-relation"], validate)
    if not cores:
        return False
    for name in ("rpmsg-relation", "libmetal-relation"):
        try:
            group = tree[D2D + "/" + name]
        except KeyError:
            continue
        if name == "rpmsg-relation":
            ok = openamp_xlnx.xlnx_rpmsg_parse(
                tree, group, "microblaze_riscv_0", validate, cores,
                "linux_dt")
        else:
            ok = openamp_xlnx.xlnx_libmetal_linux_setup_ipi(
                tree, group, "microblaze_riscv_0")
        if not ok:
            return False
    return openamp_xlnx.xlnx_validate_carveouts(tree, validate)


def test_mbv_rpmsg_linux_nodes():
    """The Linux tree gets the BRAM remoteproc, its mailbox and carveouts."""
    tree, pl, clk, gpio, mbox, carveouts = _mbv_linux_tree()
    assert _linux_dt(tree)

    bus = tree["/amba_pl/rproc-bus@b0000000"]
    assert bus.propval("compatible", list) == ["simple-pm-bus"]
    assert bus.propval("ranges", list) == [0, 0, 0xb0000000, 0x40000]
    core = tree[bus.abs_path + "/remoteproc@0"]
    assert core.propval("compatible", list) == ["xlnx,zynqmp-bram-rproc"]
    assert core.propval("reg", list) == [0, 0x40000]
    assert core.propval("clocks", list) == [clk.phandle]
    assert core.propval("reset-gpios", list) == [gpio.phandle, 0, 1]
    assert core.propval("firmware-name", list) == ["mbv_openamp_echo.elf"]
    assert core.propval("mboxes", list) == [mbox.phandle, 0, mbox.phandle, 1]
    assert core.propval("mbox-names", list) == ["tx", "rx"]
    assert core.propval("memory-region", list) == [
        c.phandle for c in carveouts]
    assert core.propval("memory-region-names", list) == [
        "vdev0vring0", "vdev0vring1", "vdev0buffer"]

    assert mbox.propval("compatible", list) == [
        "amd,versal2-pl-mbox-2.1", "amd,zynqmp-pl-mbox-2.1"]
    assert mbox.propval("#mbox-cells", list) == [1]
    assert mbox.propval("clocks", list) == [clk.phandle]
    assert carveouts[2].propval("compatible", list) == ["shared-dma-pool"]


def test_mbv_rpmsg_carveouts_in_any_order():
    """The relation may list the carveouts in any order."""
    tree, _, _, _, _, carveouts = _mbv_linux_tree()
    relation = tree[D2D + "/rpmsg-relation/relation0"]
    relation["carveouts"] = [c.phandle for c in reversed(carveouts)]
    assert _linux_dt(tree)
    core = tree["/amba_pl/rproc-bus@b0000000/remoteproc@0"]
    assert core.propval("memory-region", list) == [
        c.phandle for c in carveouts]


def test_mbv_rpmsg_missing_vring_fails():
    """The BRAM remoteproc binding needs both vrings and the buffer."""
    tree, _, _, _, _, carveouts = _mbv_linux_tree()
    relation = tree[D2D + "/rpmsg-relation/relation0"]
    relation["carveouts"] = [carveouts[0].phandle, carveouts[2].phandle]
    assert not _linux_dt(tree)


def test_mbv_libmetal_linux_nodes():
    """libmetal mode: the mailbox goes to UIO, remoteproc has no mailbox."""
    tree, _, _, _, mbox, _ = _mbv_linux_tree(rpmsg=False, libmetal=True)
    assert _linux_dt(tree)

    core = tree["/amba_pl/rproc-bus@b0000000/remoteproc@0"]
    assert core.propval("mboxes", list) == ['']
    assert core.propval("memory-region", list) == ['']
    assert mbox.propval("compatible", list) == ["uio"]


def test_mbv_mailbox_for_rpmsg_and_libmetal_fails():
    """One AXI Mailbox interface cannot serve both RPMsg and libmetal."""
    tree, *_ = _mbv_linux_tree(rpmsg=True, libmetal=True)
    assert not _linux_dt(tree)


def test_mbv_rpmsg_without_remoteproc_fails():
    """An RPMsg relation to a MicroBlaze V needs its remoteproc relation."""
    tree, *_ = _mbv_linux_tree()
    group = tree[D2D + "/rpmsg-relation"]
    assert not openamp_xlnx.xlnx_rpmsg_parse(
        tree, group, "microblaze_riscv_0", [], {}, "linux_dt")


def test_mbv_axi_mailbox_needs_linux_host():
    """A Zephyr host cannot use an AXI Mailbox relation."""
    tree, *_ = _mbv_linux_tree()
    group = tree[D2D + "/rpmsg-relation"]
    relation = tree[group.abs_path + "/relation0"]
    relation["host"] = relation.propval("remote", list)
    assert not openamp_xlnx.xlnx_rpmsg_parse(
        tree, group, "microblaze_riscv_0", [], None, "zephyr_dt")


def test_mbv_elfload_can_name_the_bram_controller():
    """elfload may name the BRAM controller instead of the memory node."""
    tree, _, clk, *_ = _mbv_linux_tree(rpmsg=False)
    ctrl = tree["/amba_pl/mbv_host_bram_ctrl@b0000000"]
    tree - tree["/memory@b0000000"]
    relation = tree[D2D + "/remoteproc-relation/relation0"]
    relation["elfload"] = [ctrl.phandle_or_create()]
    assert _linux_dt(tree)
    core = tree["/amba_pl/rproc-bus@b0000000/remoteproc@0"]
    assert core.propval("clocks", list) == [clk.phandle]


def test_mbv_elfload_without_bram_controller_fails():
    """An elfload that is not a BRAM controller, or backed by one, fails."""
    tree, *_ = _mbv_linux_tree(rpmsg=False)
    tree - tree["/amba_pl/mbv_host_bram_ctrl@b0000000"]
    assert not _linux_dt(tree)


def test_mbv_reset_controller_must_be_gpio():
    """reset-controller must reference a GPIO controller."""
    tree, _, clk, *_ = _mbv_linux_tree(rpmsg=False)
    relation = tree[D2D + "/remoteproc-relation/relation0"]
    relation["reset-controller"] = [clk.phandle]
    assert not _linux_dt(tree)


def test_mbv_relations_next_to_rpu_relations():
    """A group may hold RPU and MicroBlaze V relations; the MicroBlaze V
    relation takes the BRAM path and the RPU one the RPU path."""
    tree, *_ = _mbv_linux_tree(rpmsg=False)
    rpu = _node(tree, "/domains/RPU_1_BM")
    group = tree[D2D + "/remoteproc-relation"]
    calls = []

    def rpu_parse(tree, node, info, elfload, verbose=0):
        calls.append(node.name)
        return True

    def rpu_cluster(tree, info, elfload, verbose=0):
        return LopperNode(-1, "/rpu_core")

    tcm = _node(tree, "/axi/tcm@eba00000", reg=[0, 0xeba00000, 0, 0x10000])
    _node(tree, group.abs_path + "/relation1",
          remote=[rpu.phandle_or_create()], elfload=[tcm.phandle_or_create()])
    saved = (openamp_xlnx.xlnx_remoteproc_rpu_parse,
             openamp_xlnx.xlnx_remoteproc_v2_construct_cluster)
    openamp_xlnx.xlnx_remoteproc_rpu_parse = rpu_parse
    openamp_xlnx.xlnx_remoteproc_v2_construct_cluster = rpu_cluster
    try:
        validate = []
        cores = openamp_xlnx.xlnx_remoteproc_parse(tree, group, validate)
    finally:
        (openamp_xlnx.xlnx_remoteproc_rpu_parse,
         openamp_xlnx.xlnx_remoteproc_v2_construct_cluster) = saved
    assert calls == ["relation1"]
    assert cores["MBV_Remote"].abs_path == \
        "/amba_pl/rproc-bus@b0000000/remoteproc@0"
    assert cores["RPU_1_BM"].abs_path == "/rpu_core"
    # Only the RPU ELF-load region is checked against the carveouts.
    assert validate == [tcm]


def _mbv_cluster(tree):
    """A MicroBlaze V cluster that reaches DDR, its mailbox and, through a
    non-identity window, a device at 0x80000000 as 0x10000000."""
    ddr = _node(tree, "/memory@0")
    mbox = _node(tree, "/amba_pl/mbv_mailbox_S1@40610000",
                 reg=[0, 0x40610000, 0, 0x10000],
                 compatible=["xlnx,mailbox-2.1", "xlnx,mailbox"])
    mbox["xlnx,ip-name"] = "mailbox"
    remap = _node(tree, "/amba_pl/remapped@80000000",
                  reg=[0, 0x80000000, 0, 0x1000])
    cluster = _node(tree, "/cpus_microblaze_riscv@0",
                    **{"#ranges-address-cells": [1],
                       "#ranges-size-cells": [1]})
    cluster["address-map"] = [
        0x09b00000, ddr.phandle_or_create(), 0x09b00000, 0x100000,
        0x40610000, mbox.phandle_or_create(), 0x40610000, 0x10000,
        0x10000000, remap.phandle_or_create(), 0x80000000, 0x1000,
    ]
    return cluster, mbox, remap


def test_cpu_view_region():
    """System addresses translate through the cluster's address map."""
    tree, _, _, _, _, carveouts = _mbv_linux_tree()
    cluster, _, remap = _mbv_cluster(tree)
    view = openamp_xlnx_common.cpu_view_region
    assert view(cluster, carveouts[2]) == (0x09b08000, 0x78000)
    assert view(cluster, remap) == (0x10000000, 0x1000)
    assert view(None, remap) == (0x80000000, 0x1000)
    carveouts[2]["reg"] = [0, 0x09bf0000, 0, 0x20000]
    try:
        view(cluster, carveouts[2])
        assert False, "a region past the window must not translate"
    except ValueError:
        pass


def test_linux_platform_device_name():
    """Linux keeps the case of the node name."""
    tree, _, _, _, mbox, _ = _mbv_linux_tree()
    assert openamp_xlnx_common.linux_platform_device_name(mbox) == \
        "b2060000.mbv_mailbox_S0"


def test_mbv_openamp_header(tmp_path):
    """The header has the RPU layout macros and the MicroBlaze V mailbox."""
    tree, _, _, _, _, carveouts = _mbv_linux_tree()
    cluster, mbox, _ = _mbv_cluster(tree)
    out = tmp_path / "amd_platform_info.h"
    assert openamp_xlnx.xlnx_openamp_gen_outputs_only(
        tree, "microblaze_riscv_0", str(out), carveouts, mbox, 0, cluster)
    text = out.read_text()
    assert "#define SHARED_MEM_PA           0x9b00000UL" in text
    assert "#define SHARED_MEM_SIZE         0x78000UL" in text
    assert "#define SHARED_BUF_OFFSET       0x8000UL" in text
    assert "#define RING_TX                 FW_RSC_U32_ADDR_ANY" in text
    assert "#define MBOX_BASE_ADDR          0x40610000UL" in text
    assert "IPI" not in text


def test_mbv_openamp_header_rejects_unreachable_carveout(tmp_path):
    """A carveout the MicroBlaze V cannot reach is an error."""
    tree, _, _, _, _, carveouts = _mbv_linux_tree()
    cluster, mbox, _ = _mbv_cluster(tree)
    carveouts[2]["reg"] = [0, 0x09c00000, 0, 0x78000]
    assert not openamp_xlnx.xlnx_openamp_gen_outputs_only(
        tree, "microblaze_riscv_0", str(tmp_path / "h"), carveouts, mbox, 0,
        cluster)


def test_mbv_openamp_header_needs_cluster(tmp_path):
    """Without the remote's cluster the MicroBlaze V view is unknown."""
    tree, _, _, _, _, carveouts = _mbv_linux_tree()
    _, mbox, _ = _mbv_cluster(tree)
    assert not openamp_xlnx.xlnx_openamp_gen_outputs_only(
        tree, "microblaze_riscv_0", str(tmp_path / "h"), carveouts, mbox)


LIBMETAL_REGIONS = (("mbv_libmetal_desc0", 0x09b80000, 0x4000),
                    ("mbv_libmetal_desc1", 0x09b84000, 0x4000),
                    ("mbv_libmetal_data", 0x09b88000, 0x40000))


def _libmetal_regions(tree):
    _node(tree, "/axi", **{"#address-cells": [2], "#size-cells": [2]})
    return [_node(tree, "/axi/%s@%x" % (name, base), reg=[0, base, 0, size])
            for name, base, size in LIBMETAL_REGIONS]


def test_mbv_libmetal_remote_cmake(tmp_path):
    """The remote gets the layout and its mailbox, without a timer."""
    tree, *_ = _mbv_linux_tree()
    cluster, mbox, _ = _mbv_cluster(tree)
    out = tmp_path / "remote.cmake"
    assert openamp_xlnx.xlnx_libmetal_gen_output_file(
        tree, str(out), _libmetal_regions(tree), mbox, None, "baremetal_dt",
        0, cluster)
    text = out.read_text()
    assert "set(SHM0_DESC_BASE            0x9b80000)" in text
    assert "set(SHM_PAYLOAD_SIZE          0x40000)" in text
    assert "set(SHM_PAYLOAD_TX_OFFSET     0x20000)" in text
    assert "set(MBOX_BASE_ADDR            0x40610000)" in text
    assert "set(MBOX_SIZE                 0x10000)" in text
    assert 'set(BUS_NAME                  "generic")' in text
    assert "IPI" not in text and "TTC" not in text
    assert "TIMER" not in text


def _mbv_timer(tree, cluster, clock_on_timer=False):
    """A PL AXI Timer both the APU (0xb2070000) and the MicroBlaze V
    (0x40620000) reach: one node, with the APU address in reg."""
    clk = _node(tree, "/amba_pl/misc_clk_1", label="misc_clk_1",
                **{"clock-frequency": [99999001]})
    timer = _node(tree, "/amba_pl/timer@b2070000", label="mbv_timer",
                  reg=[0, 0xb2070000, 0, 0x10000],
                  clocks=[clk.phandle_or_create()])
    if clock_on_timer:
        timer["clock-frequency"] = [100000000]
    cluster["address-map"] = cluster.propval("address-map", list) + [
        0x40620000, timer.phandle_or_create(), 0xb2070000, 0x10000]
    return timer


def test_mbv_libmetal_cmake_with_timer(tmp_path):
    """A relation timer adds its MicroBlaze V address, Linux device name and
    clock, for the demo's optional latency profiling."""
    tree, _, _, _, host_mbox, _ = _mbv_linux_tree()
    cluster, mbox, _ = _mbv_cluster(tree)
    timer = _mbv_timer(tree, cluster)
    regions = _libmetal_regions(tree)

    remote = tmp_path / "remote.cmake"
    assert openamp_xlnx.xlnx_libmetal_gen_output_file(
        tree, str(remote), regions, mbox, timer, "baremetal_dt", 0, cluster)
    text = remote.read_text()
    assert "set(TIMER_BASE_ADDR           0x40620000)" in text
    assert "set(TIMER_CLK_HZ              99999001)" in text
    assert 'set(TIMER_DEV_NAME            "b2070000.timer")' in text

    host = tmp_path / "host.cmake"
    assert openamp_xlnx.xlnx_libmetal_gen_output_file(
        tree, str(host), regions, host_mbox, timer, "linux_dt")
    assert "set(TIMER_BASE_ADDR           0xb2070000)" in host.read_text()

    timer["clock-frequency"] = [100000000]
    assert openamp_xlnx.xlnx_libmetal_gen_output_file(
        tree, str(remote), regions, mbox, timer, "baremetal_dt", 0, cluster)
    assert "set(TIMER_CLK_HZ              100000000)" in remote.read_text()


def test_mbv_libmetal_timer_without_clock_fails(tmp_path):
    tree, *_ = _mbv_linux_tree()
    cluster, mbox, _ = _mbv_cluster(tree)
    timer = _mbv_timer(tree, cluster)
    timer["clocks"] = [0xdead]
    assert not openamp_xlnx.xlnx_libmetal_gen_output_file(
        tree, str(tmp_path / "r.cmake"), _libmetal_regions(tree), mbox,
        timer, "baremetal_dt", 0, cluster)


def test_mbv_libmetal_host_cmake(tmp_path):
    """The Linux host gets platform device names; case is preserved."""
    tree, _, _, _, mbox, _ = _mbv_linux_tree()
    out = tmp_path / "host.cmake"
    assert openamp_xlnx.xlnx_libmetal_gen_output_file(
        tree, str(out), _libmetal_regions(tree), mbox, None, "linux_dt")
    text = out.read_text()
    assert 'set(BUS_NAME                  "platform")' in text
    assert 'set(SHM0_DESC_DEV_NAME        "9b80000.mbv_libmetal_desc0")' in text
    assert 'set(SHM_DEV_NAME              "9b88000.mbv_libmetal_data")' in text
    assert 'set(MBOX_DEV_NAME             "b2060000.mbv_mailbox_S0")' in text
