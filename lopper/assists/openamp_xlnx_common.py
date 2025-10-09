"""Xilinx OpenAMP common helpers shared across assist modules.

This module defines enumerations representing CPU configuration state, the
memory map metadata consumed during remoteproc construction, and helper
routines that resolve references within generated device-tree relations.
Global collections exposed here are documented with inline docstrings so they
can be surfaced by documentation tooling.
"""

from lopper.tree import *
from enum import Enum
from enum import IntEnum
import ast

class CPU_CONFIG(IntEnum):
    """Enumerate the supported RPU execution configurations."""

    RPU_SPLIT = 0
    RPU_LOCKSTEP = 1

class RPU_CORE(IntEnum):
    """Enumerate individual RPU core indices."""

    RPU_0 = 0
    RPU_1 = 1
    RPU_2 = 2
    RPU_3 = 3
    RPU_4 = 4
    RPU_5 = 5
    RPU_6 = 6
    RPU_7 = 7
    RPU_8 = 8
    RPU_9 = 9

# This is used for YAML representation
# after this is parsed, the above enums are used for internal record keeping.
class CLUSTER_CONFIG(Enum):
    """Enumerate cluster configuration strings used by YAML input."""

    RPU_LOCKSTEP = 0
    RPU_0 = 1
    RPU_1 = 2

memory_nodes = {
    15: {  # psu_r5_0_atcm_global
        "system_view": [0x0, 0x0, 0x0, 0xffe00000, 0x0, 0x10000],
        "rpu_view": [0x0, 0x0, 0x0, 0x10000]
    },
    16: {  # psu_r5_0_btcm_global
        "system_view": [0x0, 0x20000, 0x0, 0xffe20000, 0x0, 0x10000],
        "rpu_view": [0x0, 0x20000, 0x0, 0x10000]
    },
    17: {  # psu_r5_1_atcm_global
        "system_view": [0x1, 0x0, 0x0, 0xffe90000, 0x0, 0x10000],
        "rpu_view": [0x1, 0x0, 0x0, 0x10000]
    },
    18: {  # psu_r5_1_btcm_global
        "system_view": [0x1, 0x20000, 0x0, 0xffeb0000, 0x0, 0x10000],
        "rpu_view": [0x1, 0x20000, 0x0, 0x10000]
    },
    0x1831800b: {  # psv_r5_0_atcm_global
        "system_view": [0x0, 0x0, 0x0, 0xffe00000, 0x0, 0x10000],
        "rpu_view": [0x0, 0x0, 0x0, 0x10000]
    },
    0x1831800c: {  # psv_r5_0_btcm_global
        "system_view": [0x0, 0x20000, 0x0, 0xffe20000, 0x0, 0x10000],
        "rpu_view": [0x0, 0x20000, 0x0, 0x10000]
    },
    0x1831800d: {  # psv_r5_1_atcm_global
        "system_view": [0x1, 0x0, 0x0, 0xffe90000, 0x0, 0x10000],
        "rpu_view": [0x1, 0x0, 0x0, 0x10000]
    },
    0x1831800e: {  # psv_r5_1_btcm_global
        "system_view": [0x1, 0x20000, 0x0, 0xffeb0000, 0x0, 0x10000],
        "rpu_view": [0x1, 0x20000, 0x0, 0x10000]
    },
    0x183180cb: {  # r52_0a_atcm_global
        "system_view": [0x0, 0x0, 0x0, 0xeba00000, 0x0, 0x0000],
        "rpu_view": [0x0, 0x0, 0x0, 0x10000]
    },
    0x183180cc: {  # r52_0a_btcm_global
        "system_view": [0x0, 0x10000, 0x0, 0xeba10000, 0x0, 0x8000],
        "rpu_view": [0x0, 0x10000, 0x0, 0x8000]
    },
    0x183180cd: {  # r52_0a_ctcm_global
        "system_view": [0x0, 0x18000, 0x0, 0xeba20000, 0x0, 0x8000],
        "rpu_view": [0x0, 0x18000, 0x0, 0x8000]
    },
    0x183180ce: {  # r52_0b_atcm_global
        "system_view": [0x1, 0x0, 0x0, 0xEBA80000, 0x0, 0x0000],
        "rpu_view": [0x1, 0x0, 0x0, 0x10000]
    },  
    0x183180cf: {  # r52_0b_btcm_global
        "system_view": [0x1, 0x10000, 0x0, 0xEBA90000, 0x0, 0x8000],
        "rpu_view": [0x1, 0x10000, 0x0, 0x8000]
    },  
    0x183180d0: {  # r52_0b_ctcm_global
        "system_view": [0x1, 0x18000, 0x0, 0xEBAA0000, 0x0, 0x8000],
        "rpu_view": [0x1, 0x18000, 0x0, 0x8000]
    },
    0x18318106: {  # r52_0d_atcm_global
        "system_view": [0x0, 0x0, 0x0, 0xEBC00000, 0x0, 0x0000],
        "rpu_view": [0x0, 0x0, 0x0, 0x10000]
    },  
    0x18318107: {  # r52_0d_btcm_global
        "system_view": [0x0, 0x10000, 0x0, 0xEBC10000, 0x0, 0x8000],
        "rpu_view": [0x0, 0x10000, 0x0, 0x8000]
    },  
    0x18318108: {  # r52_0d_ctcm_global
        "system_view": [0x0, 0x18000, 0x0, 0xEBC20000, 0x0, 0x8000],
        "rpu_view": [0x0, 0x18000, 0x0, 0x8000]
    },  
    0x18318109: {  # r52_1d_atcm_global
        "system_view": [0x1, 0x0, 0x0, 0xEBC40000, 0x0, 0x0000],
        "rpu_view": [0x1, 0x0, 0x0, 0x10000]
    },
    0x1831810a: {  # r52_1d_btcm_global
        "system_view": [0x1, 0x10000, 0x0, 0xEBC50000, 0x0, 0x8000],
        "rpu_view": [0x1, 0x10000, 0x0, 0x8000]
    },
    0x1831810b: {  # r52_1d_ctcm_global
        "system_view": [0x1, 0x18000, 0x0, 0xEBC60000, 0x0, 0x8000],
        "rpu_view": [0x1, 0x18000, 0x0, 0x8000]
    },
    0x1831810c: {  # r52_0e_atcm_global
        "system_view": [0x0, 0x0, 0x0, 0xEBC00000, 0x0, 0x0000],
        "rpu_view": [0x0, 0x0, 0x0, 0x10000]
    },  
    0x1831810d: {  # r52_0e_btcm_global
        "system_view": [0x0, 0x10000, 0x0, 0xEBC10000, 0x0, 0x8000],
        "rpu_view": [0x0, 0x10000, 0x0, 0x8000]
    },  
    0x1831810e: {  # r52_0e_ctcm_global
        "system_view": [0x0, 0x18000, 0x0, 0xEBC20000, 0x0, 0x8000],
        "rpu_view": [0x0, 0x18000, 0x0, 0x8000]
    },  
    0x1831810f: {  # r52_1e_atcm_global
        "system_view": [0x1, 0x0, 0x0, 0xEBC40000, 0x0, 0x0000],
        "rpu_view": [0x1, 0x0, 0x0, 0x10000]
    },
    0x18318110: {  # r52_1e_btcm_global
        "system_view": [0x1, 0x10000, 0x0, 0xEBC50000, 0x0, 0x8000],
        "rpu_view": [0x1, 0x10000, 0x0, 0x8000]
    },
    0x18318111: {  # r52_1e_ctcm_global
        "system_view": [0x1, 0x18000, 0x0, 0xEBC60000, 0x0, 0x8000],
        "rpu_view": [0x1, 0x18000, 0x0, 0x8000]
    },
}
"""dict[int, dict[str, list[int]]]: Mapping of power-domain identifiers to
system-view and RPU-view memory descriptors used for remoteproc construction."""

openamp_linux_hosts = [ "psv_cortexa72_0", "psx_cortexa78_0", "psu_cortexa53_0", "cortexa78_0" ]
"""list[str]: Names of processor nodes recognized as OpenAMP Linux hosts."""

openamp_remotes = { "psx_cortexr52_0", "psx_cortexr52_1", "psx_cortexr52_2", "psx_cortexr52_3",
 "cortexr52_0", "cortexr52_1", "cortexr52_2", "cortexr52_3", "cortexr52_4",
 "cortexr52_5", "cortexr52_6", "cortexr52_7", "cortexr52_8", "cortexr52_9",
 "psu_cortexr5_0", "psu_cortexr5_1", "psv_cortexr5_1", "psv_cortexr5_0", }
"""set[str]: Names of processor nodes supported as OpenAMP remotes."""

class SOC_TYPE:
    """Enum-like constants for supported SoC families."""

    UNINITIALIZED = -1
    VERSAL = 0
    ZYNQMP = 1
    ZYNQ = 2
    VERSAL_NET = 3
    VERSAL2 = 4


def resolve_carveouts( tree, subnode, carveout_prop_name, verbose = 0 ):
    """Resolve carveout node references within a relation.

    Args:
        tree (LopperTree): Device tree that contains the reserved/AXI nodes.
        subnode (LopperNode): Relation container node being expanded.
        carveout_prop_name (str): Property name, such as ``carveouts`` or ``elfload``.
        verbose (int): Verbosity flag for diagnostic logging.

    Returns:
        bool: True when all carveout names resolve to phandles, False otherwise.

    Algorithm:
        Iterates relation children, searches reserved-memory and AXI subtrees for
        matching node names or labels, ensures phandles exist, and replaces the
        string references with numeric phandle lists.
    """
    prop = None
    domain_node = None

    subnodes_to_check = subnode.tree["/reserved-memory"].subnodes(children_only=True) + subnode.tree["/axi"].subnodes(children_only=True)
    for relation in subnode.subnodes(children_only=True):
        if relation.props(carveout_prop_name) == []:
            print("WARNING: resolve_carveouts: ", subnode, relation, "missing property", carveout_prop_name)
            return False
        carveoutlist = relation.propval(carveout_prop_name)
        new_prop_val = []

        for carveout_str in carveoutlist:
            current_node = [ n for n in subnodes_to_check if carveout_str == n.name or carveout_str == n.label ]

            # there can be tcm in / and not /axi
            if "tcm" in carveout_str and current_node == []:
                current_node = [ n for n in subnode.tree["/"].subnodes(children_only=True) if carveout_str == n.name or carveout_str == n.label ]

            if current_node == []:
                print("ERROR: Unable to find referenced node name: ", carveout_str, current_node, relation)
                return False
            current_node = current_node[0]

            if current_node.phandle == 0:
                current_node.phandle_or_create()

            if current_node.props("phandle") == []:
               current_node + LopperProp(name="phandle", value=current_node.phandle)

            new_prop_val.append(current_node.phandle)

        relation + LopperProp(name=carveout_prop_name, value = new_prop_val)

    return True

def resolve_rpmsg_mbox( tree, subnode, verbose = 0 ):
    """Replace RPMsg mailbox string identifiers with phandles.

    Args:
        tree (LopperTree): Device tree used to locate mailbox nodes.
        subnode (LopperNode): Relation container node that references mailboxes.
        verbose (int): Verbosity flag for diagnostic logging.

    Returns:
        bool: True when mailbox references resolve successfully, False otherwise.

    Algorithm:
        Validates the presence of ``mbox`` properties, searches the AXI subtree for
        nodes whose name or label matches the mailbox string, and writes the located
        phandle back into the relation.
    """
    for relation in subnode.subnodes(children_only=True):
        if relation.props("mbox") == []:
            print("WARNING:", "rpmsg relation does not have mbox")
            return False

        mbox = relation.propval("mbox")

        # if the node name or label matches then save it
        new_prop_val = [ n.phandle for n in subnode.tree["/axi"].subnodes(children_only=True) if n.name == mbox or n.label == mbox ]
        if new_prop_val == []:
            print("WARNING: could not find ", mbox)

        relation.props("mbox")[0].value = new_prop_val[0]

    return True

def resolve_host_remote( tree, subnode, verbose = 0 ):
    """Resolve host/remote references within a relation description.

    Args:
        tree (LopperTree): Device tree containing ``/domains`` children.
        subnode (LopperNode): Relation container node with host/remote properties.
        verbose (int): Verbosity flag for diagnostic logging.

    Returns:
        bool: True when exactly one role resolves to a domain node, False otherwise.

    Algorithm:
        Checks each relation child to ensure exactly one of ``host`` or ``remote`` is
        provided, searches ``/domains`` for the named node, ensures that node has a
        phandle, and replaces the role property with the corresponding phandle.
    """
    for relation in subnode.subnodes(children_only=True):
        roles_dict = {'host': [], 'remote': []}
        # save host and remote info for relation
        [ roles_dict[role].append(relation.propval(role)) for role in roles_dict.keys() if relation.propval(role) != [''] ]

        if all(roles_dict.values()):
            print("WARNING: relation has both host and remote", relation)
            return False
        if not any(roles_dict.values()):
            print("WARNING: could not find host or remote for ", relation)
            return False

        role = [ k for k, v in roles_dict.items() if v ][0]

        # find each matching domain node in tree for the role
        relevant_node = tree["/domains"].subnodes(children_only=True,name=roles_dict[role][0]+"$")
        if relevant_node == []:
            print("WARNING: could not find relevant node for ", prop_val)
            return False

        relevant_node = relevant_node[0]

        # give matching node phandle if needed
        if relevant_node.phandle == 0:
            relevant_node.phandle_or_create()

        if relevant_node.props("phandle") == []:
            relevant_node + LopperProp(name="phandle", value=relevant_node.phandle)

        relation[role] = relevant_node.phandle

    return True

platform_info_header_r5_template = """
/*
 * Copyright (c) 2025 AMD, Inc.
 * All rights reserved.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

#ifndef _AMD_GENERATED_H_
#define _AMD_GENERATED_H_

/* Interrupt vectors */
#if defined (FREERTOS_BSP) || defined (USE_FREERTOS)
#define IPI_IRQ_VECT_ID         $IPI_IRQ_VECT_ID_FREERTOS
#else
#define IPI_IRQ_VECT_ID         $IPI_IRQ_VECT_ID
#endif
#define POLL_BASE_ADDR          $POLL_BASE_ADDR
#define IPI_CHN_BITMASK         $IPI_CHN_BITMASK

#define NUM_VRINGS              0x02
#define VRING_ALIGN             0x1000
#define VRING_SIZE              256

#define RING_TX                 $RING_TX
#define RING_RX                 $RING_RX

#define SHARED_MEM_PA           $SHARED_MEM_PA
#define SHARED_MEM_SIZE         $SHARED_MEM_SIZE
#define SHARED_BUF_OFFSET       $SHARED_BUF_OFFSET

#define SHM_DEV_NAME            $SHM_DEV_NAME
#define DEV_BUS_NAME            $DEV_BUS_NAME
#define IPI_DEV_NAME            $IPI_DEV_NAME

$EXTRAS

#endif /* _AMD_GENERATED_H_ */
"""
"""str: Template used to generate OpenAMP R5 platform header files."""
