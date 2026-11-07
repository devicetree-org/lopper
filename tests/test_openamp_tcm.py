"""Tests for OpenAMP and libmetal firmware loaded into RPU TCM only."""

# Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import pytest

from lopper.assists import openamp_xlnx
from lopper.tree import LopperProp

from tests.test_xlnx_rpu_linker import SPLIT, _node, _r5_tree


def _carveout(tree, name, start, size):
    node = _node(tree, f"/reserved-memory/{name}@{start:x}",
                 {"reg": [0, start, 0, size], "no-map": []}, label=name)
    node.phandle_or_create()
    return node


def _openamp_tree(elfload_labels, libmetal=False):
    """R5 tree with a Linux host, an R5_0 remote and its relations, laid
    out as YAML expansion writes them."""
    tree, cpus = _r5_tree()
    _node(tree, "/reserved-memory", {"ranges": []})
    _carveout(tree, "rproc0", 0x9800000, 0x1000)
    for name, start, size in (("vdev0vring0", 0x9860000, 0x4000),
                              ("vdev0vring1", 0x9864000, 0x4000),
                              ("vdev0buffer", 0x9868000, 0x100000)):
        _carveout(tree, name, start, size)
    _node(tree, "/cpus-a53", {"compatible": ["cpus,cluster"]},
          label="cpus_a53")
    _node(tree, "/cpus-a53/cpu@0", {"compatible": ["arm,cortex-a53"],
                                     "reg": [0]}, label="psu_cortexa53_0")
    tree.sync()
    host = _node(tree, "/domains/APU_Linux", {
        "compatible": ["openamp,domain-v1"], "os,type": ["linux"],
        "cpus": [tree["/cpus-a53"].phandle_or_create(), 0x1, SPLIT]})
    remote = _node(tree, "/domains/R5_0", {
        "compatible": ["openamp,domain-v1"], "os,type": ["baremetal"],
        "cpus": [cpus[0].parent.phandle_or_create(), 0x1, SPLIT]})
    elfload = [tree.lnodes(label)[0].phandle_or_create()
               for label in elfload_labels]
    if libmetal:
        group = "/domains/R5_0/domain-to-domain/libmetal-relation"
        _node(tree, "/domains/R5_0/domain-to-domain")
        _node(tree, group, {"compatible": ["libmetal,ipc-v1"]})
        _node(tree, f"{group}/relation0", {
            "host": [host.phandle_or_create()], "elfload": elfload})
    else:
        group = "/domains/APU_Linux/domain-to-domain/remoteproc-relation"
        _node(tree, "/domains/APU_Linux/domain-to-domain")
        _node(tree, group, {"compatible": ["openamp,remoteproc-v2"]})
        _node(tree, f"{group}/relation0", {
            "remote": [remote.phandle_or_create()], "elfload": elfload})
    tree.sync()
    return type("FakeSdt", (), {"tree": tree})(), cpus[0]


@pytest.fixture
def cpu_lookup(monkeypatch):
    """Make get_cpu_node find the R5 CPU of the fake SDT."""
    def lookup(sdt, options):
        return sdt.tree.lnodes(options["args"][0])[0]
    monkeypatch.setattr(openamp_xlnx, "get_cpu_node", lookup)


@pytest.mark.parametrize("libmetal", [False, True])
def test_tcm_only_elfload_gives_its_banks(cpu_lookup, libmetal):
    sdt, _ = _openamp_tree(
        ["psu_r5_0_atcm_global", "psu_r5_0_btcm_global"], libmetal)

    nodes = openamp_xlnx.xlnx_openamp_get_tcm_elf_load_nodes(
        "psu_cortexr5_0", sdt)

    assert [node.label for node in nodes] == [
        "psu_r5_0_atcm_global", "psu_r5_0_btcm_global"]


@pytest.mark.parametrize("libmetal", [False, True])
def test_elfload_with_ddr_keeps_the_ddr_placement(cpu_lookup, libmetal):
    """A DDR carveout in the ELF load keeps today's DDR linker script; a
    linker block is needed to move the sections to TCM."""
    sdt, cpu = _openamp_tree(
        ["psu_r5_0_atcm_global", "psu_r5_0_btcm_global", "rproc0"],
        libmetal)

    assert openamp_xlnx.xlnx_openamp_get_tcm_elf_load_nodes(
        "psu_cortexr5_0", sdt) == []
    assert [node.label for node in
            openamp_xlnx.xlnx_openamp_get_elf_load_nodes(sdt, cpu)] == [
        "psu_r5_0_atcm_global", "psu_r5_0_btcm_global", "rproc0"]


def test_other_cpu_has_no_elfload(cpu_lookup):
    sdt, _ = _openamp_tree(["psu_r5_0_atcm_global"])

    assert openamp_xlnx.xlnx_openamp_get_tcm_elf_load_nodes(
        "psu_cortexr5_1", sdt) == []


def _core_node(tree, memory_region=None):
    core = _node(tree, "/remoteproc@ffe00000/r5f@0",
                 {"compatible": ["xlnx,zynqmp-r5f"]})
    if memory_region is not None:
        core + LopperProp(name="memory-region", value=memory_region)
    ipi = _node(tree, "/axi/ipi@ff340000/child@ff990600", {})
    ipi.phandle_or_create()
    tree.sync()
    return core, ipi


def _rpmsg_carveouts(tree):
    return [tree.lnodes(label)[0] for label in
            ("vdev0vring0", "vdev0vring1", "vdev0buffer")]


def test_linux_memory_region_without_ddr_elfload(cpu_lookup):
    """A remote that loads into TCM only lists the vdev0buffer first,
    then the vrings."""
    sdt, _ = _openamp_tree(["psu_r5_0_atcm_global"])
    tree = sdt.tree
    core, ipi = _core_node(tree)

    assert openamp_xlnx.xlnx_rpmsg_update_tree_linux(
        tree, tree["/domains/APU_Linux"], ipi, core, _rpmsg_carveouts(tree))

    assert [tree.pnode(p).label for p in core.propval("memory-region")] == [
        "vdev0buffer", "vdev0vring0", "vdev0vring1"]


def test_linux_memory_region_keeps_ddr_record_first(cpu_lookup):
    """A small DDR carveout for the resource table metadata stays at
    memory-region[0], where the driver reads it to attach."""
    sdt, _ = _openamp_tree(["psu_r5_0_atcm_global", "rproc0"])
    tree = sdt.tree
    core, ipi = _core_node(
        tree, [tree.lnodes("rproc0")[0].phandle_or_create()])

    assert openamp_xlnx.xlnx_rpmsg_update_tree_linux(
        tree, tree["/domains/APU_Linux"], ipi, core, _rpmsg_carveouts(tree))

    assert [tree.pnode(p).label for p in core.propval("memory-region")] == [
        "rproc0", "vdev0buffer", "vdev0vring0", "vdev0vring1"]


# An R5 remote of a remoteproc relation must list its core's ATCM: the R5
# vector table is at 0x0 in the ATCM, and Linux remoteproc loads only the
# memory the relation lists.

@pytest.mark.parametrize("labels, core, missing", [
    (["psu_r5_0_atcm_global", "rproc0"], 0, False),
    (["psu_r5_0_atcm_global", "psu_r5_0_btcm_global"], 0, False),
    # A bank at its core-local address, named for its core.
    (["psu_r5_0_atcm", "rproc0"], 0, False),
    (["rproc0"], 0, True),
    (["psu_r5_0_btcm_global", "rproc0"], 0, True),
    # Another core's ATCM, and core 1's ATCM seen as a lockstep bank.
    (["psu_r5_1_atcm_global", "rproc0"], 0, True),
    (["psu_r5_0_atcm_lockstep", "rproc0"], 0, True),
    (["psu_r5_1_atcm_global", "rproc0"], 1, False),
    # Core unknown: any core's ATCM.
    (["psu_r5_1_atcm_global", "rproc0"], None, False),
])
def test_r5_atcm_missing(labels, core, missing):
    sdt, _ = _openamp_tree(labels)
    nodes = [sdt.tree.lnodes(label)[0] for label in labels]

    assert openamp_xlnx.xlnx_openamp_r5_atcm_missing(
        sdt.tree, core, nodes) is missing


def _rpmsg_remote_tree(elfload_labels, libmetal=False):
    """_openamp_tree with the remote's RPMsg relation to its host."""
    sdt, cpu = _openamp_tree(elfload_labels, libmetal)
    tree = sdt.tree
    tree["/reserved-memory"]["#address-cells"] = [2]
    tree["/reserved-memory"]["#size-cells"] = [2]
    if not libmetal:
        group = "/domains/R5_0/domain-to-domain/rpmsg-relation"
        _node(tree, "/domains/R5_0/domain-to-domain")
        _node(tree, group, {"compatible": ["openamp,rpmsg-v1"]})
        _node(tree, f"{group}/relation0", {
            "host": [tree["/domains/APU_Linux"].phandle_or_create()]})
    tree.sync()
    return sdt, cpu


def test_r5_remoteproc_ddr_only_elfload_fails(cpu_lookup, caplog):
    """The RPU linker step stops before writing a DDR linker script."""
    sdt, _ = _rpmsg_remote_tree(["rproc0"])

    assert openamp_xlnx.xlnx_openamp_get_ddr_elf_load(
        "psu_cortexr5_0", sdt) is False
    assert ("Linux remoteproc loads R5 core 0, so elfload must list the "
            "core's ATCM" in caplog.text)


def test_r5_remoteproc_with_atcm_keeps_the_ddr_region(cpu_lookup):
    sdt, _ = _rpmsg_remote_tree(["psu_r5_0_atcm_global", "rproc0"])

    assert openamp_xlnx.xlnx_openamp_get_ddr_elf_load(
        "psu_cortexr5_0", sdt) == (0x9800000, 0x1000, "RSC_TABLE")


def test_r5_libmetal_ddr_only_elfload_is_valid(cpu_lookup):
    """A libmetal remote is not loaded by Linux remoteproc."""
    sdt, _ = _rpmsg_remote_tree(["rproc0"], libmetal=True)

    assert openamp_xlnx.xlnx_openamp_get_ddr_elf_load(
        "psu_cortexr5_0", sdt) == (0x9800000, 0x1000, "LIBMETAL_DDR")


def test_r5_remoteproc_node_needs_the_atcm(monkeypatch, caplog):
    """The Linux step does not build a remoteproc node that cannot load
    the R5 vector table."""
    sdt, _ = _openamp_tree(["rproc0"])
    monkeypatch.setattr(openamp_xlnx, "get_platform",
                        lambda tree, verbose=0: openamp_xlnx.SOC_TYPE.ZYNQMP)
    remote = sdt.tree["/domains/R5_0"]
    remote["rpu_core_num"] = [0]
    remote["cpu_config_str"] = ["split"]
    info = {"remote_node": remote, "cpu_config": openamp_xlnx.CPU_CONFIG.RPU_SPLIT,
            "rpu_core_pd_prop": LopperProp(name="power-domains",
                                           value=[0xA5, 0x7])}

    assert openamp_xlnx.xlnx_remoteproc_v2_construct_cluster(
        sdt.tree, info, [sdt.tree.lnodes("rproc0")[0]]) is False
    assert ("Linux remoteproc loads R5 core 0, so elfload must list the "
            "core's ATCM" in caplog.text)
