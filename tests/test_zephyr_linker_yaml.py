"""Tests for readable Zephyr linker YAML expansion."""

# Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import pytest

from lopper.assists.zephyr_memory import LayoutError
from lopper.assists.yaml_to_dts_expansion import zephyr_linker_expand
from lopper.tree import LopperNode, LopperProp, LopperTree


def _node(tree, path, properties=None, label=None):
    """Add one test node and its properties to a tree."""
    node = LopperNode(-1, path)
    node.label = label
    tree + node
    for name, value in (properties or {}).items():
        node + LopperProp(name=name, value=value)
    return node


def _linker_tree():
    """Build a minimal domain containing readable linker metadata."""
    tree = LopperTree()
    _node(tree, "/")
    _node(tree, "/axi")
    atcm = _node(tree, "/axi/atcm@0", label="atcm")
    btcm = _node(tree, "/axi/btcm@10000", label="btcm")
    _node(tree, "/reserved-memory")
    ddr = _node(tree, "/reserved-memory/ddrboot@9800100", label="ddrboot")
    _node(tree, "/domains")
    domain = _node(tree, "/domains/RPU_Zephyr", {"os,type": ["zephyr"]})
    linker = _node(tree, f"{domain.abs_path}/linker", {
        "linker_file_output_name": ["RPU_ZEPHYR.ld"],
        "linker_memories": ["atcm", "btcm", "ddrboot"],
        "entry": ["_vector_table"],
    })
    sections = _node(tree, f"{linker.abs_path}/sections")
    _node(tree, f"{sections.abs_path}/vector_table", {
        "region": ["atcm"], "offset": [0],
    })
    _node(tree, f"{sections.abs_path}/text", {
        "region": ["ddrboot"], "offset": [0x20],
    })
    _node(tree, f"{sections.abs_path}/data", {"region": ["btcm"]})
    tree.sync()
    return tree, domain, atcm, btcm, ddr


def test_zephyr_linker_hierarchy_becomes_domain_properties():
    """Expansion resolves phandles and removes temporary child nodes."""
    tree, domain, atcm, btcm, ddr = _linker_tree()

    assert zephyr_linker_expand(tree, domain)

    assert domain.propval("linker_file_output_name") == ["RPU_ZEPHYR.ld"]
    assert domain.propval("linker-entry") == ["_vector_table"]
    assert domain.propval("linker_memories") == [
        atcm.phandle, btcm.phandle, ddr.phandle,
    ]
    assert domain.propval("linker-section-vector-table") == [atcm.phandle]
    assert domain.propval("linker-section-vector-table-offset") == [0]
    assert domain.propval("linker-section-text") == [ddr.phandle]
    assert domain.propval("linker-section-text-offset") == [0x20]
    assert domain.propval("linker-section-data") == [btcm.phandle]
    with pytest.raises(KeyError):
        tree[f"{domain.abs_path}/linker"]


def test_zephyr_linker_expansion_rejects_unknown_sections():
    """Unknown logical section groups fail before domain-access pruning."""
    tree, domain, _, _, _ = _linker_tree()
    sections = tree[f"{domain.abs_path}/linker/sections"]
    _node(tree, f"{sections.abs_path}/unknown", {"region": ["atcm"]})
    tree.sync()

    with pytest.raises(LayoutError, match="unsupported linker section"):
        zephyr_linker_expand(tree, domain)
