"""Xilinx OpenAMP common helpers shared across assist modules.

This module defines enumerations representing CPU configuration state, the
memory map metadata consumed during remoteproc construction, and helper
routines that resolve references within generated device-tree relations.
Global collections exposed here are documented with inline docstrings so they
can be surfaced by documentation tooling.
"""

from lopper.tree import *
from enum import Enum, IntEnum
import ast
import re
import sys
from pathlib import Path

from baremetalconfig_xlnx import get_cpu_node

IPI_MAILBOX_COMPATIBLES = {
    "xlnx,versal-ipi-mailbox",
    "xlnx,zynqmp-ipi-mailbox",
}
_IPI_RELATION_COMPATIBLES = {
    "openamp,rpmsg-v1",
    "libmetal,ipc-v1",
}


def _openamp_ipi_name(node):
    return node.label or node.name


def _openamp_ipi_sort_key(node):
    name = _openamp_ipi_name(node)
    numbers = re.findall(r"\d+", name)
    return (int(numbers[-1]) if numbers else sys.maxsize, name)


def _openamp_ipi_cpu_name(machine):
    """Translate a Lopper processor name to the mailbox ``xlnx,cpu-name``."""
    name = machine.lower()
    if match := re.search(r"cortexa(?:53|72|78)_(\d+)", name):
        if "cortexa53" in name:
            return "APU"
        core = "A72" if "cortexa72" in name else "A78"
        return "%s_%s" % (core, match.group(1))
    if match := re.search(r"cortexr52?_(\d+)", name):
        if "cortexr5_" in name and "cortexr52" not in name:
            return "RPU%s" % match.group(1)
        return "R52_%s" % match.group(1)
    return machine.upper()


def _openamp_enabled(node):
    status = node.propval("status")
    return status == [""] or status[0] in ("okay", "ok")


def _openamp_buffering(node):
    buf_index = node.propval("xlnx,ipi-buf-index")
    if buf_index != [""]:
        return "unbuffered" if buf_index[0] == 0xffff else "buffered"
    return ("buffered" if "msg" in node.propval("reg-names", list)
            else "unbuffered")


def _openamp_ipi_controllers(tree):
    """Find native and domain-rewritten Xilinx IPI controller nodes."""
    controllers = []
    for node in tree["/"].subnodes():
        native = IPI_MAILBOX_COMPATIBLES.intersection(
            node.propval("compatible", list))
        destination_children = any(
            "xlnx,versal-ipi-dest-mailbox" in child.propval("compatible", list)
            or "xlnx,zynqmp-ipi-dest-mailbox" in child.propval("compatible", list)
            for child in node.subnodes(children_only=True))
        if ((native or destination_children)
                and node.propval("xlnx,cpu-name") != [""]
                and node.propval("xlnx,ipi-id") != [""]
                and _openamp_enabled(node)):
            controllers.append(node)
    return controllers


def _openamp_domain_processor(tree, domain):
    dtd = next((n for n in domain.subnodes(children_only=True)
                if n.name == "domain-to-domain"), None)
    if dtd and dtd.propval("cluster_cpu") != [""]:
        return dtd.propval("cluster_cpu")[0]
    cpus = domain.propval("cpus")
    cluster = tree.pnode(cpus[0]) if cpus != [""] else None
    return (cluster.label or cluster.name) if cluster else "unspecified"


def _openamp_configured_relations(tree):
    """Collect target-side RPMsg/Libmetal mailbox references."""
    configured = {}
    rows = []
    try:
        domains_node = tree["/domains"]
    except KeyError:
        return rows, configured
    for domain in domains_node.subnodes(children_only=True):
        if domain.parent != domains_node:
            continue
        dtd = next((n for n in domain.subnodes(children_only=True)
                    if n.name == "domain-to-domain"), None)
        if not dtd or dtd.propval("cluster_cpu") == [""]:
            continue
        for relation in dtd.subnodes(children_only=True):
            compatible = relation.propval("compatible")
            if compatible == [""] or compatible[0] not in _IPI_RELATION_COMPATIBLES:
                continue
            for endpoint in relation.subnodes(children_only=True):
                mbox = endpoint.propval("mbox")
                if len(mbox) != 1 or mbox == [""]:
                    continue
                mailbox = tree.pnode(mbox[0])
                if not mailbox or not mailbox.parent:
                    continue
                source_cpu = mailbox.parent.propval("xlnx,cpu-name")
                destination_cpu = mailbox.propval("xlnx,cpu-name")
                if source_cpu == [""] or destination_cpu == [""]:
                    continue
                configured[mailbox.phandle] = (domain.name, compatible[0])
                rows.append({
                    "domain": domain.name,
                    "type": compatible[0],
                    "processor": _openamp_domain_processor(tree, domain),
                    "source": mailbox.parent,
                    "destination": mailbox,
                })
    return rows, configured


def xlnx_openamp_report_valid_ipis(sdt, machine):
    """Report supported, configured, and bidirectional IPIs for a processor."""
    tree = sdt.tree
    get_cpu_node(sdt, {"args": [machine]})
    cpu_name = _openamp_ipi_cpu_name(machine)
    controllers = _openamp_ipi_controllers(tree)
    owned = [n for n in controllers
             if n.propval("xlnx,cpu-name") == [cpu_name]]
    owned.sort(key=_openamp_ipi_sort_key)
    relations, configured = _openamp_configured_relations(tree)

    print("Given input DT: %s" % str(Path(sdt.dts).resolve()))
    print("Processor: %s" % machine)
    print("Mailbox CPU identity: %s" % cpu_name)
    print("Supported IPIs are: [ %s ]" %
          ", ".join(_openamp_ipi_name(n) for n in owned))
    print("\nConfigured OpenAMP/Libmetal IPI relations:")
    if not relations:
        print("  none")
    for row in relations:
        source = row["source"]
        destination = row["destination"]
        source_cpu = source.propval("xlnx,cpu-name")[0]
        destination_cpu = destination.propval("xlnx,cpu-name")[0]
        destination_controller = next(
            (n for n in controllers if n.propval("xlnx,ipi-id") ==
             destination.propval("xlnx,ipi-id")), destination)
        print("  %s:" % row["domain"])
        print("    Type: %s" % row["type"])
        print("    Target processor: %s" % row["processor"])
        print("    Direction: %s -> %s" % (source_cpu, destination_cpu))
        print("    Mapping: %s (%s) -> %s (%s)" %
              (_openamp_ipi_name(source), source_cpu,
               _openamp_ipi_name(destination_controller), destination_cpu))
        print("    Mailbox: %s\n" % _openamp_ipi_name(destination))

    incoming = []
    outgoing = []
    controller_ids = {tuple(n.propval("xlnx,ipi-id")) for n in controllers}
    for controller in controllers:
        for child in controller.subnodes(children_only=True):
            if not _openamp_enabled(child):
                continue
            dest_cpu = child.propval("xlnx,cpu-name")
            dest_is_processor = (dest_cpu != [""] and
                                 re.match(r"^(A\d|R\d|APU$|RPU\d+$)",
                                          dest_cpu[0]) and
                                 tuple(child.propval("xlnx,ipi-id")) in
                                 controller_ids)
            if controller in owned and dest_cpu != [cpu_name] and dest_is_processor:
                outgoing.append((controller, child))
            source_cpu = controller.propval("xlnx,cpu-name")
            source_is_processor = source_cpu != [""] and re.match(
                r"^(A\d|R\d|APU$|RPU\d+$)", source_cpu[0])
            if (dest_cpu == [cpu_name] and source_cpu != [cpu_name]
                    and source_is_processor):
                incoming.append((controller, child))

    print("\nUsable bidirectional IPI pairs for %s:" % machine)
    pairs = 0
    for local, tx in outgoing:
        peer_cpu = tx.propval("xlnx,cpu-name")
        reciprocal = next(
            ((peer, rx) for peer, rx in incoming
             if peer.propval("xlnx,cpu-name") == peer_cpu
             and peer.propval("xlnx,ipi-id") == tx.propval("xlnx,ipi-id")
             and rx.propval("xlnx,ipi-id") == local.propval("xlnx,ipi-id")
             and _openamp_buffering(rx) == _openamp_buffering(tx)), None)
        if not reciprocal:
            continue
        peer, rx = reciprocal
        used = next((state for state in
                     (configured.get(tx.phandle), configured.get(rx.phandle))
                     if state), None)
        state_text = ("configured by %s/%s" % used) if used else "available"
        print("  %s <-> %s:" % (machine, peer_cpu[0]))
        print("    %s:" % _openamp_buffering(tx))
        print("      TX: %s -> %s" %
              (_openamp_ipi_name(local), _openamp_ipi_name(peer)))
        print("      RX: %s -> %s" %
              (_openamp_ipi_name(peer), _openamp_ipi_name(local)))
        print("      State: %s" % state_text)
        pairs += 1
    if not pairs:
        print("  none")
    return True

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
        "system_view": [0x0, 0x0, 0x0, 0xeba00000, 0x0, 0x10000],
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
        "system_view": [0x1, 0x0, 0x0, 0xEBA80000, 0x0, 0x10000],
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
        "system_view": [0x0, 0x0, 0x0, 0xEBC00000, 0x0, 0x10000],
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
        "system_view": [0x1, 0x0, 0x0, 0xEBC40000, 0x0, 0x10000],
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
        "system_view": [0x0, 0x0, 0x0, 0xEBC00000, 0x0, 0x10000],
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
        "system_view": [0x1, 0x0, 0x0, 0xEBC40000, 0x0, 0x10000],
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

libmetal_cmake_template = """
# ============================================================================
# AUTOGENERATED FILE — DO NOT EDIT
# Generated by Python (string.Template)
# ============================================================================

# ------------------------------
# Shared Memory Devices
# ------------------------------
set(SHM_DEV_NAME              "$SHM_DEV_NAME")
set(SHM0_DESC_DEV_NAME        "$SHM0_DESC_DEV_NAME")
set(SHM1_DESC_DEV_NAME        "$SHM1_DESC_DEV_NAME")

# ------------------------------
# Base Addresses
# ------------------------------
set(SHM_IMAGE_BASE            $SHM_IMAGE_BASE)
set(SHM0_DESC_BASE            $SHM0_DESC_BASE)
set(SHM1_DESC_BASE            $SHM1_DESC_BASE)

# ------------------------------
# Sizes
# ------------------------------
set(SHM_IMAGE_SIZE            $SHM_IMAGE_SIZE)
set(SHM0_DESC_SIZE            $SHM0_DESC_SIZE)
set(SHM1_DESC_SIZE            $SHM1_DESC_SIZE)

# ------------------------------
# Payload Configuration
# ------------------------------
set(SHM_PAYLOAD_BASE          $SHM_PAYLOAD_BASE)
set(SHM_PAYLOAD_SIZE          $SHM_PAYLOAD_SIZE)
set(SHM_PAYLOAD_HALF_SIZE     $SHM_PAYLOAD_HALF_SIZE)

set(SHM_PAYLOAD_RX_OFFSET     $SHM_PAYLOAD_RX_OFFSET)
set(SHM_PAYLOAD_TX_OFFSET     $SHM_PAYLOAD_TX_OFFSET)

# ------------------------------
# Aggregate SHM Region
# ------------------------------
set(SHM_BASE_ADDR             $SHM_BASE_ADDR)
set(SHM_SIZE                  $SHM_SIZE)

# ------------------------------
# IPI Configuration
# ------------------------------
set(IPI_DEV_NAME              "$IPI_DEV_NAME")
set(IPI_BASE_ADDR             $IPI_BASE_ADDR)
set(IPI_MASK                  $IPI_MASK)
set(IPI_IRQ_VECT_ID           $IPI_IRQ_VECT_ID)

# ------------------------------
# TTC Configuration
# ------------------------------
set(TTC_DEV_NAME              "$TTC_DEV_NAME")
set(TTC_NODEID                $TTC_NODEID)
set(TTC_BASE_ADDR            $TTC_BASE_ADDR)

# ------------------------------
# Bus Configuration
# ------------------------------
set(BUS_NAME                  "$BUS_NAME")
"""
