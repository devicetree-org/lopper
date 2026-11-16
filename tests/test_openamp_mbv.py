"""OpenAMP and libmetal relations with a MicroBlaze V remote.

A MicroBlaze V in the PL uses an AXI Mailbox below /amba_pl instead of a PS
IPI, runs its firmware from a dual-port BRAM whose host side is a top-level
SDT memory node, and reaches DDR through its own address map.
"""

# Copyright (c) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from lopper.assists import yaml_to_dts_expansion
from lopper.tree import LopperNode, LopperTree


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
