"""Tests for baremetal and FreeRTOS RPU section placement."""

# Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import pytest

from lopper.assists.xlnx_rpu_linker import (
    BAREMETAL_LINKER_SECTIONS,
    default_section_regions,
    elfload_tcm_sections,
    find_linker_domain,
    r52_tcm_regions_snippet,
    resolve_placement,
    section_cmake_variables,
)
from lopper.assists.yaml_to_dts_expansion import zephyr_linker_expand
from lopper.assists.zephyr_memory import LayoutError
from lopper.tree import LopperNode, LopperProp, LopperTree

R5_DDR = ("psu_r5_ddr_0_memory_0", 0x100000, 0x7ff00000)
R52_DDR = ("NoC_C0_C1_ddr_memory_DDR_LOW_0", 0x100000, 0x80000000)
LOCKSTEP = 0x80000003 | (1 << 30)
SPLIT = 0x80000003


def _node(tree, path, properties=None, label=None):
    """Add one node and its properties to a tree."""
    node = LopperNode(-1, path)
    node.label = label
    tree + node
    for name, value in (properties or {}).items():
        node + LopperProp(name=name, value=value)
    return node


def _base_tree():
    tree = LopperTree()
    tree["/"]["#address-cells"] = [2]
    tree["/"]["#size-cells"] = [2]
    _node(tree, "/axi", {"#address-cells": [2], "#size-cells": [2]})
    _node(tree, "/domains")
    return tree


def _bank(tree, name, start, size):
    return _node(tree, f"/axi/{name.replace('_', '-')}@{start:x}", {
        "reg": [0, start, 0, size]}, label=name)


def _r5_tree():
    """ZynqMP-like tree: two single-core R5 clusters and their TCM banks."""
    tree = _base_tree()
    cpus = []
    for core in (0, 1):
        _node(tree, f"/cpus-r5@{core}", {"compatible": ["cpus,cluster"]},
              label=f"cpus_r5_{core}")
        cpus.append(_node(tree, f"/cpus-r5@{core}/cpu@0", {
            "compatible": ["arm,cortex-r5"], "reg": [0]},
            label=f"psu_cortexr5_{core}"))
    _bank(tree, "psu_r5_0_atcm", 0x0, 0x10000)
    _bank(tree, "psu_r5_0_atcm_global", 0xffe00000, 0x10000)
    _bank(tree, "psu_r5_0_btcm_global", 0xffe20000, 0x10000)
    _bank(tree, "psu_r5_0_atcm_lockstep", 0xffe10000, 0x10000)
    _bank(tree, "psu_r5_0_btcm_lockstep", 0xffe30000, 0x10000)
    _bank(tree, "psu_r5_1_atcm_global", 0xffe90000, 0x10000)
    _bank(tree, "psu_r5_1_btcm_global", 0xffeb0000, 0x10000)
    tree.sync()
    return tree, cpus


def _r52_tree():
    """Versal Gen 2-like tree: four single-core R52 clusters."""
    tree = _base_tree()
    cpus = []
    for core in range(4):
        _node(tree, f"/cpus-r52@{core}", {"compatible": ["cpus,cluster"]},
              label=f"cpus_r52_{core}")
        cpus.append(_node(tree, f"/cpus-r52@{core}/cpu@0", {
            "compatible": ["arm,cortex-r52"], "reg": [0]},
            label=f"cortexr52_{core}"))
    for cluster, letter in ((0, "a"), (1, "b")):
        for half in (0, 1):
            base = 0xeba00000 + cluster * 0x80000 + half * 0x40000
            for bank, offset, size in (("atcm", 0, 0x10000),
                                       ("btcm", 0x10000, 0x8000),
                                       ("ctcm", 0x20000, 0x8000)):
                _bank(tree, f"r52_{half}{letter}_{bank}_global",
                      base + offset, size)
    tree.sync()
    return tree, cpus


def _domain(tree, cpu, mode, os_type="baremetal", **properties):
    """Add a domain on ``cpu`` with flattened linker properties."""
    cluster = cpu.parent
    domain = _node(tree, "/domains/RPU", {
        "os,type": [os_type],
        "cpus": [cluster.phandle_or_create(), 0x1, mode],
    })
    for name, value in properties.items():
        domain + LopperProp(name=name.replace("_", "-"), value=value)
    tree.sync()
    return domain


def _ref(tree, label):
    return [tree.lnodes(label)[0].phandle_or_create()]


def test_r5_split_tcm_layout_places_code_in_atcm_and_data_in_btcm():
    tree, cpus = _r5_tree()
    domain = _domain(tree, cpus[0], SPLIT, linker_layout=["tcm"])

    placement = resolve_placement(
        tree, domain, cpus[0], "psu_cortexr5_0", R5_DDR)

    assert placement.cmake_variables() == [
        ("VECTORS", "psu_r5_0_atcm_MEM_0"), ("CODE", "psu_r5_0_atcm_MEM_0"),
        ("RODATA", "psu_r5_0_atcm_MEM_0"), ("DATA", "psu_r5_0_btcm_MEM_0"),
        ("BSS", "psu_r5_0_btcm_MEM_0"), ("HEAP", "psu_r5_0_btcm_MEM_0"),
        ("STACK", "psu_r5_0_btcm_MEM_0"),
        ("RSC_TABLE_REGION", "psu_r5_0_btcm_MEM_0"),
    ]
    assert placement.memory == {
        "psu_r5_0_atcm_MEM_0": (0x0, 0x10000),
        "psu_r5_0_btcm_MEM_0": (0x20000, 0x10000),
    }
    assert placement.tcm_regions == {}


@pytest.mark.parametrize("r52", [False, True])
def test_lockstep_cluster_is_rejected(r52):
    """Section placement supports split mode only, for now."""
    tree, cpus = _r52_tree() if r52 else _r5_tree()
    machine, ddr = (("cortexr52_0", R52_DDR) if r52
                    else ("psu_cortexr5_0", R5_DDR))
    domain = _domain(tree, cpus[0], LOCKSTEP, linker_layout=["tcm"])

    with pytest.raises(LayoutError, match="RPU cluster in lockstep"):
        resolve_placement(tree, domain, cpus[0], machine, ddr)


@pytest.mark.parametrize("bank, message", [
    ("psu_r5_0_atcm_lockstep", "lockstep bank and the cluster is in split"),
    ("psu_r5_1_atcm_global", "belongs to RPU core 1"),
])
def test_r5_split_rejects_banks_of_other_modes_and_cores(bank, message):
    tree, cpus = _r5_tree()
    domain = _domain(tree, cpus[0], SPLIT,
                     linker_section_text=_ref(tree, bank))

    with pytest.raises(LayoutError, match=message):
        resolve_placement(tree, domain, cpus[0], "psu_cortexr5_0", R5_DDR)


def test_split_r5_core_1_uses_its_own_banks_by_address():
    tree, cpus = _r5_tree()
    domain = _domain(tree, cpus[1], SPLIT,
                     linker_section_data=_ref(tree, "psu_r5_1_btcm_global"))

    placement = resolve_placement(
        tree, domain, cpus[1], "psu_cortexr5_1", R5_DDR)

    assert placement.sections["data"] == "psu_r5_0_btcm_MEM_0"


def test_vector_table_must_be_in_atcm():
    tree, cpus = _r5_tree()
    domain = _domain(
        tree, cpus[0], SPLIT,
        linker_section_vector_table=_ref(tree, "psu_r5_0_btcm_global"))

    with pytest.raises(LayoutError, match="vector_table must be placed in "
                       "the ATCM"):
        resolve_placement(tree, domain, cpus[0], "psu_cortexr5_0", R5_DDR)


def test_domain_sram_banks_are_checked_for_the_core():
    tree, cpus = _r5_tree()
    domain = _domain(tree, cpus[0], SPLIT, linker_layout=["tcm"])
    domain + LopperProp(name="sram", value=[0, 0xffe90000, 0, 0x10000])
    tree.sync()

    with pytest.raises(LayoutError, match="belongs to RPU core 1"):
        resolve_placement(tree, domain, cpus[0], "psu_cortexr5_0", R5_DDR)


def test_r52_tcm_layout_uses_ctcm_and_sets_tcm_region_words():
    tree, cpus = _r52_tree()
    domain = _domain(tree, cpus[0], SPLIT, linker_layout=["tcm"])

    placement = resolve_placement(
        tree, domain, cpus[0], "cortexr52_0", R52_DDR)

    assert dict(placement.cmake_variables()) == {
        "VECTORS": "r52_atcm", "CODE": "r52_atcm", "RODATA": "r52_atcm",
        "DATA": "r52_btcm", "BSS": "r52_btcm", "HEAP": "r52_ctcm",
        "STACK": "r52_ctcm", "RSC_TABLE_REGION": "r52_btcm",
    }
    assert placement.memory == {
        "r52_atcm": (0x0, 0x10000), "r52_btcm": (0x10000, 0x8000),
        "r52_ctcm": (0x18000, 0x8000),
    }
    assert placement.tcm_regions == {"A": 0, "B": 0x10001, "C": 0x18001}
    assert "_TCM_B_REGION = .;\n   LONG(${R52_TCM_B_REGION})" in \
        r52_tcm_regions_snippet(placement.tcm_regions)


def test_r52_ddr_layout_leaves_unused_banks_unconfigured():
    tree, cpus = _r52_tree()
    domain = _domain(tree, cpus[0], SPLIT, linker_layout=["ddr"])

    placement = resolve_placement(
        tree, domain, cpus[0], "cortexr52_0", R52_DDR)

    assert placement.sections["text"] == R52_DDR[0]
    assert placement.tcm_regions == {"A": 0, "B": 0, "C": 0}


def _add_vring(tree):
    _node(tree, "/reserved-memory",
          {"#address-cells": [2], "#size-cells": [2], "ranges": []})
    return _node(tree, "/reserved-memory/rpu0vdev0vring0@3ed40000", {
        "reg": [0, 0x3ed40000, 0, 0x4000], "no-map": []})


def test_ddr_region_overlapping_an_ipc_carveout_is_rejected():
    tree, cpus = _r5_tree()
    _add_vring(tree)
    domain = _domain(tree, cpus[0], SPLIT, linker_layout=["tcm-data"])

    with pytest.raises(LayoutError, match="overlaps IPC carveout"):
        resolve_placement(tree, domain, cpus[0], "psu_cortexr5_0", R5_DDR)


def test_tcm_layout_and_elfload_ddr_do_not_hit_ipc_carveouts():
    tree, cpus = _r5_tree()
    _add_vring(tree)
    domain = _domain(tree, cpus[0], SPLIT, linker_layout=["tcm-data"])
    elfload = ("psu_r5_ddr_0_memory_0", 0x3ed00000, 0x40000)

    placement = resolve_placement(
        tree, domain, cpus[0], "psu_cortexr5_0", elfload)

    assert placement.sections["text"] == "psu_r5_ddr_0_memory_0"


def test_carveouts_still_in_a_domain_are_checked():
    """Before the OpenAMP assist runs, carveouts are children of a domain's
    reserved-memory and have start and size instead of reg."""
    tree, cpus = _r5_tree()
    _node(tree, "/domains/APU")
    _node(tree, "/domains/APU/reserved-memory", {"ranges": []})
    _node(tree, "/domains/APU/reserved-memory/rpu0vdev0buffer", {
        "start": [0x9868000], "size": [0x40000]})
    domain = _domain(tree, cpus[0], SPLIT, linker_layout=["ddr"])

    with pytest.raises(LayoutError, match="rpu0vdev0buffer"):
        resolve_placement(tree, domain, cpus[0], "psu_cortexr5_0", R5_DDR)


def test_find_linker_domain_matches_the_cpu_and_os():
    tree, cpus = _r5_tree()
    domain = _domain(tree, cpus[0], SPLIT, os_type="freertos",
                     linker_layout=["tcm"])

    assert find_linker_domain(tree, cpus[0]) is domain
    assert find_linker_domain(tree, cpus[1]) is None
    domain["os,type"].value = ["zephyr"]
    assert find_linker_domain(tree, cpus[0]) is None


@pytest.mark.parametrize("machine, vectors", [
    ("psu_cortexr5_0", "psu_r5_0_atcm_MEM_0"),
    ("psv_cortexr5_0", "psv_r5_0_atcm_MEM_0"),
    ("cortexr52_0", "r52_tcm_alias"),
    ("psx_cortexr52_0", "psx_r52_tcm_alias"),
])
def test_defaults_keep_the_template_regions(machine, vectors):
    """Without a linker block the templates render as before: the vectors
    in the region they hard-coded and every other section in DATA."""
    sections = default_section_regions(machine, "DDR0", "DDR0")

    assert section_cmake_variables(sections) == [
        ("VECTORS", vectors)] + [
        (name, "DDR0") for name in ("CODE", "RODATA", "DATA", "BSS",
                                    "HEAP", "STACK", "RSC_TABLE_REGION")]
    assert default_section_regions("psu_cortexa53_0", "D", "D") is None


def _yaml_domain(os_type="baremetal"):
    tree, cpus = _r5_tree()
    domain = _node(tree, "/domains/RPU", {"os,type": [os_type]})
    linker = _node(tree, "/domains/RPU/linker", {"layout": ["tcm-data"]})
    sections = _node(tree, "/domains/RPU/linker/sections")
    tree.sync()
    return tree, domain, linker, sections


def test_yaml_expansion_accepts_baremetal_linker_blocks():
    tree, domain, _, sections = _yaml_domain("baremetal,newlib,3.3.0")
    _node(tree, f"{sections.abs_path}/vector_table", {
        "region": ["psu_r5_0_atcm_global"], "offset": [0]})
    _node(tree, f"{sections.abs_path}/text", {"region": ["ddr"]})
    tree.sync()

    assert zephyr_linker_expand(tree, domain)

    atcm = tree.lnodes("psu_r5_0_atcm_global")[0]
    assert domain.propval("linker-layout") == ["tcm-data"]
    assert domain.propval("linker-section-vector-table") == [atcm.phandle]
    assert domain.propval("linker-section-vector-table-offset") == [0]
    assert domain.propval("linker-section-text") == ["ddr"]
    with pytest.raises(KeyError):
        tree[f"{domain.abs_path}/linker"]


@pytest.mark.parametrize("section, properties, message", [
    ("noinit", {"region": ["ddr"]}, "unsupported linker section 'noinit'"),
    ("text", {"region": ["ddr"], "offset": [0x20]},
     "only vector_table takes an offset"),
])
def test_yaml_expansion_rejects_invalid_sections(section, properties,
                                                 message):
    tree, domain, _, sections = _yaml_domain("freertos")
    _node(tree, f"{sections.abs_path}/{section}", properties)
    tree.sync()

    with pytest.raises(LayoutError, match=message):
        zephyr_linker_expand(tree, domain)


def test_yaml_expansion_rejects_unknown_layouts_and_zephyr_keys():
    tree, domain, linker, _ = _yaml_domain()
    linker["layout"].value = ["fast"]
    with pytest.raises(LayoutError, match="layout must be one of"):
        zephyr_linker_expand(tree, domain)

    tree, domain, linker, _ = _yaml_domain()
    linker + LopperProp(name="linker_file_output_name", value=["x.ld"])
    with pytest.raises(LayoutError, match="unsupported properties"):
        zephyr_linker_expand(tree, domain)


def test_yaml_expansion_still_rejects_linux_domains():
    tree, domain, _, _ = _yaml_domain("linux")

    with pytest.raises(LayoutError, match="requires os,type"):
        zephyr_linker_expand(tree, domain)


def test_every_section_has_a_cmake_variable():
    sections = {name: name for name in BAREMETAL_LINKER_SECTIONS}
    assert len(section_cmake_variables(sections)) == 8


def _nodes(tree, *labels):
    return [tree.lnodes(label)[0] for label in labels]


def test_tcm_elfload_places_r5_core_1_in_its_local_banks():
    """A libmetal or remoteproc ELF load in core 1's global TCM banks links
    at the core-local addresses, using both banks."""
    tree, cpus = _r5_tree()
    domain = _domain(tree, cpus[1], SPLIT)
    sections = elfload_tcm_sections(_nodes(
        tree, "psu_r5_1_atcm_global", "psu_r5_1_btcm_global"), cpus[1])

    placement = resolve_placement(
        tree, domain, cpus[1], "psu_cortexr5_1", R5_DDR, sections=sections)

    assert dict(placement.cmake_variables()) == {
        "VECTORS": "psu_r5_0_atcm_MEM_0", "CODE": "psu_r5_0_atcm_MEM_0",
        "RODATA": "psu_r5_0_btcm_MEM_0", "DATA": "psu_r5_0_btcm_MEM_0",
        "BSS": "psu_r5_0_btcm_MEM_0", "HEAP": "psu_r5_0_btcm_MEM_0",
        "STACK": "psu_r5_0_btcm_MEM_0",
        "RSC_TABLE_REGION": "psu_r5_0_btcm_MEM_0",
    }
    assert placement.memory == {
        "psu_r5_0_atcm_MEM_0": (0x0, 0x10000),
        "psu_r5_0_btcm_MEM_0": (0x20000, 0x10000),
    }


def test_tcm_elfload_with_atcm_only_keeps_everything_in_atcm():
    tree, cpus = _r5_tree()
    sections = elfload_tcm_sections(
        _nodes(tree, "psu_r5_0_atcm_global"), cpus[0])

    assert {node.label for node in sections.values()} == {
        "psu_r5_0_atcm_global"}


def test_tcm_elfload_in_lockstep_is_rejected():
    tree, cpus = _r5_tree()
    domain = _domain(tree, cpus[0], LOCKSTEP)
    sections = elfload_tcm_sections(_nodes(
        tree, "psu_r5_0_atcm_global", "psu_r5_0_btcm_global"), cpus[0])

    with pytest.raises(LayoutError, match="RPU cluster in lockstep"):
        resolve_placement(tree, domain, cpus[0], "psu_cortexr5_0", R5_DDR,
                          sections=sections)


def test_tcm_elfload_r52_uses_ctcm_for_heap_and_stack():
    tree, cpus = _r52_tree()
    domain = _domain(tree, cpus[1], SPLIT)
    sections = elfload_tcm_sections(_nodes(
        tree, "r52_1a_atcm_global", "r52_1a_btcm_global",
        "r52_1a_ctcm_global"), cpus[1])

    placement = resolve_placement(
        tree, domain, cpus[1], "cortexr52_1", R52_DDR, sections=sections)

    assert placement.sections["heap"] == "r52_ctcm"
    assert placement.sections["data"] == "r52_btcm"
    assert placement.tcm_regions == {"A": 0, "B": 0x10001, "C": 0x18001}


def test_tcm_elfload_banks_of_another_core_are_rejected():
    tree, cpus = _r5_tree()
    domain = _domain(tree, cpus[1], SPLIT)
    sections = elfload_tcm_sections(_nodes(
        tree, "psu_r5_0_atcm_global", "psu_r5_0_btcm_global"), cpus[1])

    with pytest.raises(LayoutError, match="belongs to RPU core 0"):
        resolve_placement(tree, domain, cpus[1], "psu_cortexr5_1", R5_DDR,
                          sections=sections)


def test_tcm_elfload_needs_the_atcm():
    tree, cpus = _r5_tree()

    with pytest.raises(LayoutError, match="must include the ATCM"):
        elfload_tcm_sections(_nodes(tree, "psu_r5_1_btcm_global"), cpus[1])


def test_find_linker_domain_without_a_linker_block():
    tree, cpus = _r5_tree()
    domain = _domain(tree, cpus[1], SPLIT)

    assert find_linker_domain(tree, cpus[1]) is None
    assert find_linker_domain(tree, cpus[1], linker_block=False) is domain
