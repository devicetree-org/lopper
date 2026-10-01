#/*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import argparse
import copy
import struct
import sys
import types
import unittest
import os
import getopt
import re
import subprocess
import shutil
from pathlib import Path
from pathlib import PurePath
from io import StringIO
import contextlib
import importlib
from lopper import Lopper
from lopper import LopperFmt
import lopper
from lopper.tree import *
from re import *
from string import Template
from lopper.log import _init, _warning, _info, _error, _debug

sys.path.append(os.path.dirname(__file__))
from openamp_xlnx_common import *
from openamp_xlnx_common import (
    _openamp_domain_processor,
    _openamp_domain_selects_cpu,
)
from baremetalconfig_xlnx import get_cpu_node
from lopper_lib import (
    int_to_cells,
    node_property_cells,
    node_reg_start_size,
)
from xlnx_rpu_tcm import (
    TCM_LOCAL_ORIGINS,
    rpu_cluster_tcm_base,
    tcm_bank_type,
)
from string import ascii_lowercase as alc

_init(__name__)


RPU_PATH = "/rpu@ff9a0000"
REMOTEPROC_D_TO_D = "openamp,remoteproc-v1"
REMOTEPROC_D_TO_D_v2 = "openamp,remoteproc-v2"
RPMSG_D_TO_D = "openamp,rpmsg-v1"
LIBMETAL_D_TO_D = "libmetal,ipc-v1"


def _required_reg_region(node):
    """Return the first valid ``reg`` region for ``node``.

    OpenAMP consumes only the first region from each device or carveout node.
    Decode it using the parent bus cell widths instead of assuming a fixed
    two-address-cell/two-size-cell representation.
    """
    if not isinstance(node, LopperNode):
        raise ValueError("expected a node with a reg property")

    base, size = node_reg_start_size(node)
    if base is None or size is None or size <= 0:
        raise ValueError("%s has an invalid reg property" % node.abs_path)
    return base, size

def is_compat( node, compat_string_to_test ):
    """Identify whether this plugin handles the provided compatibility string.

    Args:
        node (LopperNode): Device tree node being evaluated. Present to satisfy the
            dispatcher interface; not used for the decision.
        compat_string_to_test (str): Compatibility string extracted from the node.

    Returns:
        Callable | str: ``xlnx_openamp_rpu`` when the compatibility string matches,
        otherwise an empty string to indicate no match.

    Algorithm:
        Performs a regular-expression search for ``openamp,xlnx-rpu`` within the
        provided string and returns the registered handler on success.
    """
    if re.search( "openamp,xlnx-rpu", compat_string_to_test):
        return xlnx_openamp_rpu
    return ""


def xlnx_openamp_keep_node(linux_dt, zephyr_dt, node, tree):
    """Report whether a node shode stay for OpenAMP Use cases.

    Args:
        linux_dt (bool): True if for Linux domain. Else False.
        zephyr_dt (bool): True if for Zephyr domain. Else False.
        node (LopperNode): Node to check
        tree (LopperTree): Tree for lopper nodes.
    Returns:
        True if Node should remain. Else False.

    Algorithm:
        Try each condition for the given node.
    """
    if not isinstance(node, LopperNode):
        _error("openamp_xlnx: xlnx_openamp_keep_node expects a node, got %r"
               % (node,))
        return False

    conditions = [
        "uio" in node.propval('compatible', list) and not zephyr_dt,
        "vnd,mbox-consumer" in node.propval('compatible', list),
        "zephyr,mbox-ipm" in node.propval('compatible', list),
    ]

    return any(conditions)


def xlnx_openamp_update_relation_timers(sdt, target_os, machine):
    """Update timers selected by an OpenAMP or Libmetal relation.

    Args:
        sdt(LopperTree): Tree for lopper nodes.
        target_os (str): OS for this lopper run.
        machine (str): Machine corresponding to the domain.
    Returns:
        True if success. Else False.

    Algorithm:
        Enable relation-selected UIO timers for Linux. For non-Linux output,
        restore the native TTC binding only on relation-selected UIO timers.
    """
    tree = sdt.tree

    if get_platform(tree, 0) in [ SOC_TYPE.VERSAL2, SOC_TYPE.VERSAL_NET ]:
        return False

    # Relations have this shape:
    #
    #   /domains/<domain>/domain-to-domain/<relation-type>/<relation>
    #
    # Find relation-type containers belonging to the requested CPU's domain.
    # Lopper's subnodes() walk includes descendants, despite the historical
    # children_only argument name, so explicitly validate the parent shape.
    match_cpu = get_cpu_node(sdt, {"args": [machine]})
    relation_groups = []
    for node in tree["/domains"].subnodes(children_only=True):
        relation_parent = node.parent
        if relation_parent is None or relation_parent.name != "domain-to-domain":
            continue

        domain = relation_parent.parent
        if domain is None:
            continue

        if _openamp_domain_selects_cpu(tree, domain, match_cpu):
            relation_groups.append(node)

    if not relation_groups:
        return False

    # A relation's timer property names only the timer assigned to that
    # OpenAMP/Libmetal channel.  It is not an allow-list for all system timers.
    timer_phandles = []
    for relation_group in relation_groups:
        for relation in relation_group.subnodes(children_only=True):
            timer_value = relation.propval("timer")
            if timer_value != ['']:
                timer_phandles.extend(timer_value)

    if not timer_phandles:
        return False

    # The shared source tree may contain Linux's UIO override. Non-Linux
    # consumers use the native TTC binding, so undo that override only for the
    # timers explicitly named by their relation. Do not touch other timers.
    for timer_phandle in timer_phandles:
        timer_node = tree.pnode(timer_phandle)
        is_uio_timer = (
            timer_node is not None
            and "uio" in timer_node.propval("compatible", list)
        )
        if is_uio_timer:
            if target_os == "linux_dt":
                timer_node["status"] = "okay"
            else:
                timer_node["compatible"] = "cdns,ttc"

    return True


def xlnx_handle_relations(sdt, machine, find_only = True, os = None):
    """Process OpenAMP relation domains for a given machine.

    Args:
        sdt (LopperSDT): Structured device tree wrapper containing the parsed tree.
        machine (str): Name of the remote machine to target.
        find_only (bool): When True, return the first matching domain without
            modifying the tree.
        os (str | None): Operating-system context (for example ``linux_dt``).

    Returns:
        LopperNode | bool | None: Matching domain when ``find_only`` is True, True
        when processing succeeds, False on failure, or None when the search mode
        finds no relations.

    Algorithm:
        Resolves the CPU node associated with the requested machine, collects
        remoteproc and RPMsg relation nodes, delegates processing to the dedicated
        parsers, tracks carveouts for later validation, and verifies the resulting
        reserved-memory layout does not contain overlaps. Emits a warning when no
        relations are discovered and exits without modifying the tree.
    """
    tree = sdt.tree

    # get_cpu_node expects dictionary where first arg first element is machine
    match_cpunode = get_cpu_node(sdt, {'args':[machine]})
    if not match_cpunode:
        _error("openamp_xlnx: processor '%s' not found in the system "
               "device tree" % machine)
        return False

    # first collect all relevant openamp domains
    remoteproc_relations = []
    rpmsg_relations = []
    libmetal_relations = []

    parse_arrs = { REMOTEPROC_D_TO_D_v2: remoteproc_relations, RPMSG_D_TO_D: rpmsg_relations, LIBMETAL_D_TO_D: libmetal_relations }

    for n in sdt.tree["/domains"].subnodes():
        node_compat = n.propval("compatible")[0]
        if node_compat == '':
            continue

        if n.parent.parent.propval("cpus") == ['']:
            continue

        # ensure target domain matches
        if _openamp_domain_selects_cpu(
                tree, n.parent.parent, match_cpunode):
            if find_only:
                 return n
            else: # do processing on found nodes
                if node_compat in parse_arrs:
                    parse_arrs[node_compat].append(n)

    # As the RPMsg relation will be appending nodes to a remoteproc node, link the rpmsg
    # relation to its corresponding remoteproc relation so the remoteproc relation can pass along
    # the remoteproc core node
    remoteproc_core_mapping_to_rpmsg_relation = {}

    # used to check if conflicts in ELFLOAD and IPC carveouts
    carveout_validation_arr = []

    for rel in remoteproc_relations:
        ret = xlnx_remoteproc_parse(tree, rel, carveout_validation_arr, 1)
        if not ret:
            return ret

        # save remoteproc core nodes for later use in rpmsg processing
        remoteproc_core_mapping_to_rpmsg_relation.update(ret)

    for rel in rpmsg_relations:
        if not xlnx_rpmsg_parse(tree, rel, machine, carveout_validation_arr, remoteproc_core_mapping_to_rpmsg_relation, os, 1):
            return False

    for rel in libmetal_relations:
        if not xlnx_libmetal_linux_setup_ipi(tree, rel, machine, 1):
            return False

    # check if conflicts in ELFLOAD and IPC carveouts
    if not xlnx_validate_carveouts(tree, carveout_validation_arr):
            return False

    if not remoteproc_relations and not rpmsg_relations:
        _warning("openamp_xlnx: no remoteproc or RPMsg relations found for "
                 "processor '%s'" % machine)

    # if here for find case, then return None as failure
    # if processing too and we are here, then this did not encounter error. So return True.
    # note that if the tree does not have openamp nodes True is also returned.
    return None if find_only else True

def xlnx_rpmsg_update_tree_linux(tree, node, ipi_node, core_node, rpmsg_carveouts, verbose = 0 ):
    """Inject Linux-specific RPMsg properties into the device tree.

    Args:
        tree (LopperTree): Device tree being updated.
        node (LopperNode): RPMsg relation child describing a channel endpoint.
        ipi_node (LopperNode): Interrupt node used for mailbox communication.
        core_node (LopperNode): Remoteproc core node associated with the channel.
        rpmsg_carveouts (list[LopperNode]): Reserved-memory nodes assigned to RPMsg.
        verbose (int): Verbosity flag controlling diagnostic output.

    Returns:
        bool: True when the tree is updated successfully, False on validation errors.

    Algorithm:
        Validates carveout naming, promotes the buffer carveout to the front of the
        memory-region list, appends carveouts to the core node, reorders DDR boot
        entries to trail RPMsg carveouts, and injects mailbox properties required by
        the Linux remoteproc driver.
    """
    _debug("openamp_xlnx: xlnx_rpmsg_update_tree_linux %s" % node.abs_path)
    # The core's remoteproc driver takes one mailbox and one vdev0buffer;
    # a second RPMsg relation would replace the first one's mailbox.
    if core_node.propval("mboxes") != ['']:
        _error(f"openamp_xlnx: {core_node.abs_path} already has an RPMsg "
               f"relation; {node.abs_path} is a second one for the same "
               "RPU core")
        return False
    # The carveouts hold one vdev0buffer, which goes first after the ELF
    # load region already in memory-region.
    vdev0buf = [ index for index, rc in enumerate(rpmsg_carveouts) if "vdev0buffer" in rc.name ]
    if len(vdev0buf) != 1:
        _error(f"openamp_xlnx: {node.abs_path}: expected one vdev0buffer "
               f"carveout, found {len(vdev0buf)}")
        return False
    rpmsg_carveouts[vdev0buf[0]] + LopperProp(name="compatible", value="shared-dma-pool")

    vdev0buf = rpmsg_carveouts.pop(vdev0buf[0])
    rpmsg_carveouts.insert(0, vdev0buf)
    new_mem_region_prop_val = core_node.propval("memory-region")

    [ new_mem_region_prop_val.append(rc.phandle) for rc in rpmsg_carveouts ]

    # If DDRBOOT, ensure that it is after RPMSG carveouts
    # # save ddrboot node and add to end of list
    ddrboot_node_index = [ index for index, phandle in enumerate(new_mem_region_prop_val) if "ddrboot" in tree.pnode(phandle).name ]
    if ddrboot_node_index:
        new_mem_region_prop_val.append( new_mem_region_prop_val.pop(ddrboot_node_index[0]) )

    # update property with new values
    core_node["memory-region"].value = new_mem_region_prop_val
    core_node + LopperProp(name="mboxes", value = [ipi_node.phandle, 0, ipi_node.phandle, 1])
    core_node + LopperProp(name="mbox-names", value = ["tx", "rx"])
    return True

# Inputs: openamp-processed SDT, target processor
# If there exists a DDR carveout for ELF-Loading, return the start and size
# of the carveout
def xlnx_openamp_get_ddr_elf_load(machine, sdt):
    """Retrieve the DDR ELF-load carveout for a target machine.

    Args:
        machine (str): Identifier for the remote processor of interest.
        sdt (LopperSDT): Processed device tree structure.

    Returns:
        tuple[int, int] | bool: Tuple of (base, size) when a carveout exists, or
        False if the carveout cannot be resolved.

    Algorithm:
        Matches the machine to its CPU node, locates the associated RPMsg relation,
        validates host references, iterates remoteproc relations, and returns the
        ``reg`` data from the first ELFLOAD node mapped to DDR.
    """
    # get_cpu_node expects dictionary where first arg first element is machine
    match_cpunode = get_cpu_node(sdt, {'args':[machine]})
    if not match_cpunode:
        _error("openamp_xlnx: processor '%s' not found in the system "
               "device tree" % machine)
        return False

    # map machine to CPU node and then to openamp domain with relation
    target_node = None
    for n in sdt.tree["/domains"].subnodes(children_only=True):
        node_compat = n.propval("compatible")[0]
        # domains must (a) have compatible and (b) relate to CPU
        if node_compat == '' or n.parent.parent.propval("cpus") == ['']:
            continue

        # ensure target domain matches
        if _openamp_domain_selects_cpu(
                sdt.tree, n.parent.parent, match_cpunode):
             target_node = n
             break

    if target_node is None:
        _error("openamp_xlnx: no OpenAMP relation found for processor '%s'"
               % machine)
        return False

    # find node described in the domain that is for ELF LOAD

    # remote should only have one relevant host
    rpmsg_rels = target_node.subnodes(children_only=True)
    if len(rpmsg_rels) != 1:
        _error(f"openamp_xlnx: {target_node.abs_path}: expected one "
               f"relation, found {len(rpmsg_rels)}")
        return False

    rpmsg_rel = rpmsg_rels[0]
    host = rpmsg_rel.propval("host")
    if host == [''] or len(host) != 1:
        _error(f"openamp_xlnx: {rpmsg_rel.abs_path}: expected one host "
               "property")
        return False

    host_node = sdt.tree.pnode(host[0])
    if not isinstance(host_node, LopperNode):
        _error(f"openamp_xlnx: {rpmsg_rel.abs_path}: host does not "
               "reference a domain")
        return False

    if target_node.propval('compatible') == ["libmetal,ipc-v1"]:
        for rel in target_node.subnodes(children_only=True):
            elfload = rel.propval("elfload")
            if elfload == ['']:
                _error(f"openamp_xlnx: {rel.abs_path}: a libmetal remote "
                       "needs an elfload property")
                return False
            elfload_node = sdt.tree.pnode(elfload[0])
            try:
                base, size = _required_reg_region(elfload_node)
            except ValueError as exc:
                _error(f"openamp_xlnx: {exc}")
                return False
            return (base, size, "LIBMETAL_DDR")
        _error(f"openamp_xlnx: {target_node.abs_path}: libmetal domain has "
               "no relations")
        return False

    # look through host for matching remoteproc relation. If found then return the relation's elfload property reg value
    for rel in host_node.subnodes(children_only=True):
        if ['openamp,remoteproc-v2'] == rel.parent.propval("compatible"):
            remote = rel.propval("remote")
            if remote == ['']:
                _error(f"openamp_xlnx: {rel.abs_path}: remoteproc relation "
                       "has no remote, so its elfload cannot be found")
                return False

            #  check that the referenced remote matches
            referenced_remote_domain = sdt.tree.pnode(remote[0])
            if referenced_remote_domain is None or referenced_remote_domain != target_node.parent.parent:
                _error(f"openamp_xlnx: {rel.abs_path}: remote does not "
                       "reference this domain")
                return False

            elfload_nodes = [ sdt.tree.pnode(i) for i in rel.propval("elfload") ]
            relevant_elfload_nodes = [ i for i in elfload_nodes if i is not None and 'mmio-sram' not in i.propval('compatible')]
            if not relevant_elfload_nodes:
                _error(f"openamp_xlnx: {rel.abs_path}: a DDR linker script "
                       "needs at least one DDR elfload region")
                return False

            # return reg from match
            try:
                base, size = _required_reg_region(
                    relevant_elfload_nodes[0])
            except ValueError as exc:
                _error(f"openamp_xlnx: {exc}")
                return False

            return (base, size, "RSC_TABLE")

    _error("openamp_xlnx: no remoteproc relation gives an elfload region "
           "for processor '%s'" % machine)
    return False

def xlnx_openamp_uses_direct_ipm(machine):
    """Return True for ZynqMP R5 targets using the legacy direct-IPM driver."""
    return "psu_cortexr5" in machine

def xlnx_openamp_apply_legacy_zephyr_memories(tree, domain_node):
    """Apply deprecated xlnx,zephyr,mems memory selection.

    Description:
        Preserves the original Zephyr domain-YAML behavior while MPU and
        linker policy metadata is adopted. Every resolved legacy memory is
        marked as memory and the first entry selects zephyr,sram only when a
        newer transform has not already selected it.

    Args:
        tree (LopperTree): Device tree being updated.
        domain_node (LopperNode): Domain containing legacy memory references.

    Returns:
        bool: True when the property is absent or every reference resolves;
            False when one or more references cannot be resolved uniquely.

    Raises:
        None.
    """
    references = domain_node.propval("xlnx,zephyr,mems", list)
    if not references or references == [""]:
        return True

    _warning("openamp_xlnx: xlnx,zephyr,mems is deprecated; use "
             "amd,openamp-zephyr-memory-policy-v1")
    memories = []
    for reference in references:
        matches = []
        if isinstance(reference, int):
            node = tree.pnode(reference)
            matches = [node] if node else []
        else:
            reference = str(reference)
            if reference.startswith("/"):
                try:
                    matches = [tree[reference]]
                except KeyError:
                    matches = []
            if not matches:
                for node in tree:
                    aliases = {node.name, node.name.split("@")[0]}
                    if node.label:
                        aliases.add(node.label)
                    for property_name in ("label", "xlnx,ip-name"):
                        values = node.propval(property_name, list)
                        if values and values != [""]:
                            aliases.add(str(values[0]))
                    if reference in aliases:
                        matches.append(node)
        if len(matches) != 1:
            _error("openamp_xlnx: legacy Zephyr memory '%s' resolved to %d "
                   "nodes" % (reference, len(matches)))
            return False
        matches[0]["device_type"] = "memory"
        memories.append(matches[0])

    chosen = tree["/chosen"]
    if chosen.propval("zephyr,sram", list) == [""]:
        chosen["zephyr,sram"] = memories[0].abs_path
    return True

def xlnx_openamp_configure_zephyr_ipc_shm(tree, ipc_nodes):
    """Combine the OpenAMP vrings and buffer into one Zephyr IPC SRAM node."""
    if len(ipc_nodes) != 3:
        raise ValueError("Zephyr RPMsg requires three IPC carveouts; "
                         "found %d" % len(ipc_nodes))

    regions = []
    ipc_phandles = set()
    for node in ipc_nodes:
        base, size = _required_reg_region(node)
        regions.append((base, base + size, node))
        ipc_phandles.add(node.phandle)
    regions.sort(key=lambda region: region[0])

    for previous, current in zip(regions, regions[1:]):
        if previous[1] != current[0]:
            raise ValueError("IPC carveouts are not contiguous: "
                             "%s ends at %#x, %s starts at %#x" %
                             (previous[2].abs_path, previous[1],
                              current[2].abs_path, current[0]))

    base = regions[0][0]
    size = regions[-1][1] - base
    for _, _, node in regions:
        tree - node

    ipc_node = LopperNode(-1, "/reserved-memory/ipc@%s" % hex(base)[2:])
    ipc_node.label = "ipc_shm"
    address_cells, size_cells = node_property_cells(tree["/reserved-memory"])
    ipc_reg = (int_to_cells(base, address_cells) +
               int_to_cells(size, size_cells))
    ipc_node + LopperProp(name="reg", value=ipc_reg)
    ipc_node + LopperProp(name="compatible", value=["mmio-sram"])
    ipc_node + LopperProp(name="status", value="okay")
    tree + ipc_node
    ipc_phandle = ipc_node.phandle_or_create()

    try:
        domains = tree["/domains"].subnodes(children_only=True)
    except KeyError:
        domains = []
    for domain in domains:
        references = domain.propval("reserved-memory", list)
        if not references or references == [""]:
            continue
        replaced = []
        added_ipc = False
        for reference in references:
            if reference in ipc_phandles:
                if not added_ipc:
                    replaced.append(ipc_phandle)
                    added_ipc = True
                continue
            replaced.append(reference)
        if added_ipc:
            domain["reserved-memory"] = replaced

    tree['/chosen']['zephyr,ipc_shm'] = ipc_node.abs_path
    return ipc_node

# Inputs: openamp-processed SDT, target processor, ipi, ipc node
def xlnx_rpmsg_update_tree_zephyr(machine, tree, ipi_node, domain_node, ipc_nodes, relation_compat):
    """Tailor the device tree for Zephyr RPMsg communication.

    Args:
        machine (str): Remote machine identifier (unused, present for symmetry).
        tree (LopperTree): Device tree being updated.
        ipi_node (LopperNode): IPI node used for mailbox signaling.
        domain_node (LopperNode): Domain node. It may contain ddr boot field.
        ipc_nodes (list[LopperNode]): IPC shared-memory nodes tied to RPMsg.
        relation_compat (str): relation compatible string

    Returns:
        bool: True when Zephyr-specific updates succeed, False on validation failure.

    Algorithm:
        Validates IPC node count, sets Zephyr chosen-node properties, injects a
        mailbox consumer helper, removes alternative IPI siblings to avoid conflicts,
        and clears flash/OCM choices that would clash with RPMsg shared memory.
    """

    ipc_node = xlnx_openamp_configure_zephyr_ipc_shm(tree, ipc_nodes)

    direct_ipm_target = xlnx_openamp_uses_direct_ipm(machine)

    if domain_node.props("xlnx,ddr-boot"):
        elfload_nodes = [ tree.pnode(x) for x in domain_node.propval("reserved-memory") ]
        valid_elfload_node = [ node for node in elfload_nodes if node and node.propval("device_type") == ['memory'] ]
        if valid_elfload_node:
            tree['/chosen']['zephyr,sram'] = valid_elfload_node[0].abs_path

    if not xlnx_openamp_apply_legacy_zephyr_memories(tree, domain_node):
        return False

    # only create a node for this the first time. in the future this will go away as upstream wants use of ipm mbox node. this is here for bkwd compatibility
    try:
        # if here then mbox_consumer_node was already created.
        mbox_consumer_node = tree['/mbox-consumer']
    except KeyError:
        if not direct_ipm_target:
            mbox_consumer_node = LopperNode(-1, "/mbox-consumer")
            mbox_consumer_props = { "compatible" : 'vnd,mbox-consumer', "mboxes" : [ipi_node.phandle, 0, ipi_node.phandle, 1], "mbox-names" : ['tx', 'rx'] }
            [mbox_consumer_node + LopperProp(name=n, value=mbox_consumer_props[n]) for n in mbox_consumer_props]
            tree.add(mbox_consumer_node)

    if direct_ipm_target:
        mbox_ipm_node = ipi_node
    else:
        ipi_base, _ = _required_reg_region(ipi_node)
        controller_base, _ = _required_reg_region(ipi_node.parent)
        mbox_ipm_node = LopperNode(
            -1,
            "/mbox_ipi_%s_%s" %
            (hex(ipi_base)[2:], hex(controller_base)[2:]))
        mbox_ipm_props = { "compatible" : "zephyr,mbox-ipm", "mbox-names" : ['tx', 'rx'], "status": "okay", "mboxes" : [ipi_node.phandle, 0, ipi_node.phandle, 1] }
        [mbox_ipm_node +  LopperProp(name=n, value=mbox_ipm_props[n]) for n in mbox_ipm_props]
        tree.add(mbox_ipm_node)

    # do this for upstream compatibility for now
    if RPMSG_D_TO_D == relation_compat:
        tree['/chosen']['zephyr,ipc'] = mbox_ipm_node.abs_path

    if tree['/chosen'].propval('zephyr,flash') != ['']:
        tree['/chosen'].delete(tree['/chosen']['zephyr,flash'])
    if tree['/chosen'].propval('zephyr,ocm') != ['']:
        tree['/chosen'].delete(tree['/chosen']['zephyr,ocm'])

    return True

# Translate SCMI IDs from versal2-scmi-power.h to the XilPM API IDs in
# xlnx-versal-power.h (TTC0-3) and xlnx-versal2-power.h (TTC4-7).
# Apply this table only after identifying the Versal2 SCMI provider: numeric
# IDs alone cannot distinguish SCMI from the direct firmware bindings.
_VERSAL2_SCMI_TTC_XILPM_IDS = {
    22: 0x18224024,  # TTC0
    23: 0x18224025,  # TTC1
    24: 0x18224026,  # TTC2
    25: 0x18224027,  # TTC3
    58: 0x1822411f,  # TTC4
    59: 0x18224120,  # TTC5
    60: 0x18224121,  # TTC6
    61: 0x18224122,  # TTC7
}


def _libmetal_ttc_xilpm_node_id(tree, timer_node, platform):
    """Return the firmware ID consumed by Libmetal's XilPM calls.

    The power-domains argument belongs to its provider's ID space. Direct
    firmware providers already supply a XilPM ID; Versal2 SCMI supplies an
    index that must be translated for TTC_NODEID. Keep the DT property intact
    so Linux can still use its original power-domain provider and argument.

    Both paths require one provider with #power-domain-cells = <1>. Invalid
    or unsupported bindings raise ValueError for the output caller to report.
    """
    def invalid(reason):
        return ValueError(f"{timer_node.abs_path}: {reason}")

    pd = timer_node.propval("power-domains", list)
    if len(pd) != 2 or not all(isinstance(cell, int) and cell >= 0 for cell in pd):
        raise invalid("expected one power-domains reference with one ID")
    provider = tree.pnode(pd[0])
    if provider is None:
        raise invalid(f"unresolved power-domains provider {pd[0]:#x}")
    if provider.propval("#power-domain-cells", list) != [1]:
        raise invalid(f"{provider.abs_path} must declare #power-domain-cells = <1>")

    firmware_compat = {
        SOC_TYPE.ZYNQMP: "xlnx,zynqmp-firmware",
        SOC_TYPE.VERSAL: "xlnx,versal-firmware",
        SOC_TYPE.VERSAL_NET: "xlnx,versal-net-firmware",
        SOC_TYPE.VERSAL2: "xlnx,versal2-firmware",
    }
    if firmware_compat.get(platform) in provider.propval("compatible", list):
        return pd[1]

    # SCMI power providers are protocol children, with compatibility on the
    # transport parent. Neither node names nor phandle labels identify them.
    # Protocol 0x11 is SCMI Power Domain Management.
    parent_compat = (provider.parent.propval("compatible", list)
                     if provider.parent else [])
    is_scmi_power = (
        provider.propval("reg", list) == [0x11]
        and any(compat in parent_compat for compat in
                ("arm,scmi", "arm,scmi-smc", "linaro,scmi-optee")))
    if is_scmi_power and platform == SOC_TYPE.VERSAL2:
        try:
            return _VERSAL2_SCMI_TTC_XILPM_IDS[pd[1]]
        except KeyError:
            raise invalid(f"unsupported Versal2 SCMI TTC power-domain ID {pd[1]:#x}")
    raise invalid(f"unsupported TTC power-domains provider {provider.abs_path} "
                  f"for platform {platform}")


def xlnx_libmetal_gen_output_file(tree, output_file, carveouts, ipi_node, timer_node, os, verbose = 0 ):
    """Generate .cmake file for Libmetal IPI usage

    Args:
        tree (LopperTree): Device tree being inspected for metadata.
        output_file (str): name of output file
        carveouts (list[LopperNode]): Carveouts associated with libmetal
        ipi_node (LopperNode): IPI node used
        timer_node (LopperNode): Timer node used
        os (str): os value
        verbose (int): Verbosity flag for diagnostic printing.

    Returns:
        bool: True on successful file generation, False on failure.

    Raises:
        SystemExit: If a required reg region or the TTC power binding is invalid.
    """
    _debug("openamp_xlnx: xlnx_libmetal_gen_output_file %s" % output_file)
    platform = get_platform(tree, verbose)
    if platform is None:
        _report_unsupported_platform(tree)
        return False
    desc0 = carveouts[0]
    desc1 = carveouts[1]
    data = carveouts[2]

    try:
        desc0_base, desc0_size = _required_reg_region(desc0)
        desc1_base, desc1_size = _required_reg_region(desc1)
        data_base, data_size = _required_reg_region(data)
        timer_base, _ = _required_reg_region(timer_node)
        ipi_base, _ = _required_reg_region(ipi_node.parent)
        ttc_node_id = _libmetal_ttc_xilpm_node_id(tree, timer_node, platform)
    except ValueError as exc:
        # Returning False lets the assist dispatcher warn and exit zero unless
        # --werror is set. Fail here so builds cannot accept a missing CMake file.
        _error(f"openamp_xlnx: {exc}", 1)

    suffix = "ipi" if platform == SOC_TYPE.ZYNQMP else "mailbox"

    values = {"SHM_IMAGE_BASE": hex(data_base),
              "SHM_IMAGE_SIZE": hex(data_size)}

    values.update({
                "SHM_PAYLOAD_BASE": values["SHM_IMAGE_BASE"], "SHM_PAYLOAD_SIZE": values["SHM_IMAGE_SIZE"],
                "SHM_PAYLOAD_HALF_SIZE": hex(data_size//2),
                "SHM_PAYLOAD_RX_OFFSET": "0x0",
                "SHM_BASE_ADDR": values["SHM_IMAGE_BASE"], "SHM_SIZE": values["SHM_IMAGE_SIZE"],
                "SHM0_DESC_BASE": hex(desc0_base), "SHM0_DESC_SIZE": hex(desc0_size),
                "SHM1_DESC_BASE": hex(desc1_base), "SHM1_DESC_SIZE": hex(desc1_size),
                "TTC_DEV_NAME": "%s.timer" % hex(timer_base)[2:], "TTC_NODEID": hex(ttc_node_id),
                "TTC_BASE_ADDR": hex(timer_base),
                "IPI_DEV_NAME": "%s.%s" % (hex(ipi_base)[2:], suffix), "IPI_BASE_ADDR": hex(ipi_base),
                "IPI_MASK": hex(ipi_node['xlnx,ipi-bitmask'].value[0]),
                "IPI_IRQ_VECT_ID": 0 if os == "linux_dt" else ipi_node.parent.propval("xlnx,int-id")[0],
                "BUS_NAME": "platform" if os == "linux_dt" else "generic" })
    values.update({"SHM_PAYLOAD_TX_OFFSET": values["SHM_PAYLOAD_HALF_SIZE"]})
    values.update({"SHM_DEV_NAME": "%s.%s" % (data.name.split("@")[1].lower(), data.name.split("@")[0].lower())})
    values.update({"SHM0_DESC_DEV_NAME": "%s.%s" % (desc0.name.split("@")[1].lower(), desc0.name.split("@")[0].lower())})
    values.update({"SHM1_DESC_DEV_NAME": "%s.%s" % (desc1.name.split("@")[1].lower(), desc1.name.split("@")[0].lower())})

    if os not in [ "linux_dt", "baremetal_dt" ]:
        _error("openamp_xlnx: libmetal output supports linux_dt and "
               "baremetal_dt, not '%s'" % os)
        return False

    try:
        with open(output_file, "w") as f:
            output = Template(libmetal_cmake_template)
            f.write(output.substitute(values))
    except Exception as e:
        _error(f"openamp_xlnx: cannot write libmetal output {output_file}: "
               f"{e}")
        return False

    return True

def xlnx_openamp_gen_outputs_only(tree, machine, output_file, memory_region_nodes, host_ipi, verbose = 0 ):
    """Generate C header output for OpenAMP RPMsg channels.

    Args:
        tree (LopperTree): Device tree being inspected for metadata.
        machine (str): Remote machine identifier (unused directly but retained for
            debugging).
        output_file (str): Destination filepath for the generated header.
        memory_region_nodes (list[LopperNode]): Carveouts associated with RPMsg.
        host_ipi (LopperNode): IPI node connected to the host processor.
        verbose (int): Verbosity flag for diagnostic printing.

    Returns:
        bool: True on successful file generation, False on failure.

    Algorithm:
        Aggregates VRING and buffer sizes from carveouts, extracts IPI configuration
        data, prepares a template substitution dictionary, and writes the rendered
        header to the requested output path.
    """
    vrings = [n for n in memory_region_nodes if 'vring' in n.name]
    buffers = [n for n in memory_region_nodes if 'vdev0buffer' in n.name]
    remote_ipi = host_ipi.parent

    try:
        vring_regions = [_required_reg_region(node) for node in vrings]
        if not vring_regions:
            raise ValueError("no vring carveouts were found")
        if len(buffers) != 1:
            raise ValueError(
                "expected one vdev0buffer carveout; found %d" %
                len(buffers))
        shbuf_base, shbuf_size = _required_reg_region(buffers[0])
        remote_ipi_base, _ = _required_reg_region(remote_ipi)
    except ValueError as exc:
        _error(f"openamp_xlnx: {exc}")
        return False

    shm_base = min(base for base, _ in vring_regions)
    if shbuf_base < shm_base:
        _error("openamp_xlnx: vdev0buffer precedes the vring carveouts")
        return False

    shm_pa = hex(shm_base)
    shbuf_sz = hex(shbuf_size)
    shbuf_offset = hex(shbuf_base - shm_base)

    remote_vect_id = remote_ipi.propval('xlnx,int-id')[0]
    ipi_irq_vect_id = hex(remote_vect_id)
    ipi_irq_vect_id_rtos = hex(remote_vect_id-32)
    remote_ipi_str = hex(remote_ipi_base)
    host_bitmask = hex(host_ipi.propval('xlnx,ipi-bitmask')[0])

    try:
        inputs = {
        "POLL_BASE_ADDR": remote_ipi_str,
        "SHM_DEV_NAME": "\"x.shm\"",
        "DEV_BUS_NAME": "\"generic\"",
        "IPI_DEV_NAME":  "\"y.ipi\"",
        "IPI_IRQ_VECT_ID": ipi_irq_vect_id,
        "IPI_IRQ_VECT_ID_FREERTOS": ipi_irq_vect_id_rtos,
        "IPI_CHN_BITMASK": host_bitmask,
        "RING_TX": "FW_RSC_U32_ADDR_ANY",
        "RING_RX": "FW_RSC_U32_ADDR_ANY",
        "SHARED_MEM_PA": shm_pa,
        "SHARED_MEM_SIZE": shbuf_sz,
        "SHARED_BUF_OFFSET": shbuf_offset,
        "EXTRAS":"",
        }

        with open(output_file, "w") as f:
            output = Template(platform_info_header_r5_template)
            f.write(output.substitute(inputs))
    except Exception as e:
        _error(f"openamp_xlnx: cannot write OpenAMP header {output_file}: "
               f"{e}")
        return False

    return True

def xlnx_libmetal_linux_setup_ipi(tree, relation_node, machine, verbose = 0 ):
    """Parse RPMsg relations and update the device tree accordingly.

    Args:
        tree (LopperTree): Device tree being modified.
        relation_node (LopperNode): Domain relation describing Libmetal channels.
        machine (str): Remote machine identifier (used for logging and lookups).
        verbose (int): Verbosity flag for diagnostic messages.

    Returns:
        bool: True when parsing succeeds, False if required metadata is missing.

    Algorithm:
        Find IPI from relation. Set it for parent.
    """
    _debug("openamp_xlnx: Set up IPI for Libmetal Linux relation %s" %
          relation_node.abs_path)

    platform = get_platform(tree, verbose)
    if platform is None:
        return False

    for node in relation_node.subnodes(children_only=True):
        # first find host to remote IPI
        mbox_pval = node.propval("mbox")
        if mbox_pval == ['']:
            _error("openamp_xlnx: libmetal: %s is missing mbox property" % node.abs_path)
            return False

        ipi_node = tree.pnode(mbox_pval[0])
        if ipi_node is None:
            _error("openamp_xlnx: libmetal: cannot resolve IPI for %s" % node.abs_path)
            return False

        # Linux binds an IPI agent either to the mailbox driver, as RPMsg
        # needs, or to UIO, as libmetal needs; RPMsg relations are processed
        # first, so their mboxes are already in the tree.
        for user in tree["/"].subnodes():
            mboxes = user.propval("mboxes", list)
            if mboxes == [""] or not isinstance(mboxes[0], int):
                continue
            mbox = tree.pnode(mboxes[0])
            if mbox is not None and mbox.parent is not None and \
                    mbox.parent.abs_path == ipi_node.parent.abs_path:
                _error("openamp_xlnx: libmetal: %s uses IPI %s, which %s "
                       "already uses as a mailbox; use another IPI for "
                       "libmetal" % (node.abs_path, ipi_node.parent.abs_path,
                                     user.abs_path))
                return False

        # setup IPI mask so UIO device's corresponding DT node has the remote's bitmask set.
        ipi_node.parent + LopperProp(name="libmetal,uio-ipi-bitmask", value=ipi_node.propval("xlnx,ipi-bitmask"))

    return True


def xlnx_rpmsg_parse(tree, rpmsg_relation_node, machine, carveout_validation_arr, channel_to_core_dict = None, os = None, verbose = 0 ):
    """Parse RPMsg relations and update the device tree accordingly.

    Args:
        tree (LopperTree): Device tree being modified.
        rpmsg_relation_node (LopperNode): Domain relation describing RPMsg channels.
        machine (str): Remote machine identifier (used for logging and lookups).
        carveout_validation_arr (list[LopperNode]): Accumulator for carveouts to be
            validated later.
        channel_to_core_dict (dict[str, LopperNode] | None): Mapping of remote names
            to remoteproc core nodes.
        os (str | None): Operating-system context (``linux_dt`` or ``zephyr_dt``).
        verbose (int): Verbosity flag for diagnostic messages.

    Returns:
        bool: True when parsing succeeds, False if required metadata is missing.

    Algorithm:
        Determines the platform, iterates RPMsg endpoints, validates remote/host
        references, gathers carveouts, applies OS-specific tree rewrites, and
        optionally emits a header file via ``xlnx_openamp_gen_outputs_only``.
    """
    _debug("openamp_xlnx: parsing RPMsg relation %s" %
          rpmsg_relation_node.abs_path)

    platform = get_platform(tree, verbose)
    if platform is None:
        return False

    for node in rpmsg_relation_node.subnodes(children_only=True):
        pname = "remote" if os == "linux_dt" else "host"
        # check for remote property
        if not node.props(pname):
            _error("openamp_xlnx: %s is missing %s property" %
                   (node.abs_path, pname))
            return False

        remote_node = tree.pnode(node.propval(pname)[0])
        if remote_node is None:
            _error("openamp_xlnx: invalid RPMsg %s reference in %s" %
                   (pname, rpmsg_relation_node.abs_path))
            return False

        if os == "linux_dt" and remote_node.name not in channel_to_core_dict:
            _error("openamp_xlnx: remoteproc core is missing for RPMsg relation %s" %
                   rpmsg_relation_node.abs_path)
            return False

        core_node = channel_to_core_dict[remote_node.name] if os == "linux_dt" else None

        # first find host to remote IPI
        mbox_pval = node.propval("mbox")
        if mbox_pval == ['']:
            _error("openamp_xlnx: %s is missing mbox property" % node.abs_path)
            return False

        ipi_node = tree.pnode(mbox_pval[0])
        if ipi_node is None:
            _error("openamp_xlnx: cannot resolve IPI for %s" % node.abs_path)
            return False

        carveouts_node = tree[node.parent.parent.parent.abs_path + "/domain-to-domain/rpmsg-relation"]
        carveout_prop = node.propval("carveouts")
        if carveout_prop == ['']:
            _error("openamp_xlnx: %s is missing carveouts property" %
                   node.abs_path)
            return False

        rpmsg_carveouts = [ tree.pnode(phandle) for phandle in carveout_prop ]

        # validate later
        carveout_validation_arr.extend(rpmsg_carveouts)

        # until domain access is in place - need to manually prune some nodes
        try:
            res_mem_node = tree["/reserved-memory"]
            [ tree.delete(i) for i in res_mem_node.subnodes() if i.propval("compatible") == ['mmio-sram'] and os == "linux_dt" ]
        except KeyError:
            _error("openamp_xlnx: RPMsg carveouts require /reserved-memory")
            return False

        if os == "zephyr_dt" and not xlnx_rpmsg_update_tree_zephyr(machine, tree, ipi_node, node.parent.parent.parent, rpmsg_carveouts, rpmsg_relation_node.propval("compatible")[0]):
            return False
        if os == "linux_dt"  and not xlnx_rpmsg_update_tree_linux(tree, node, ipi_node, core_node, rpmsg_carveouts, verbose):
            return False

    return True

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    """Check whether the k-th bit within an integer is set.

    Args:
        n (int): Value to test.
        k (int): Bit index to inspect.

    Returns:
        bool: True when the bit is set, otherwise False.

    Algorithm:
        Applies a bitmask constructed via ``1 << k`` and performs a bitwise AND,
        returning True when the result is non-zero.
    """
    if n & (1 << (k)):
        return True

    return False


def determine_cpus_config(remote_domain):
    """Map the remote domain CPU configuration string to an enum value.

    Args:
        remote_domain (LopperNode): Domain node describing the remote processor.

    Returns:
        CPU_CONFIG | int: CPU configuration enum for split/lockstep, or -1 on error.

    Algorithm:
        Validates that ``cpu_config_str`` exists, ensures the value is one of the known
        strings, and converts it into the matching ``CPU_CONFIG`` enum constant.
    """
    domain_path = getattr(remote_domain, "abs_path", remote_domain)
    _debug("openamp_xlnx: determine_cpus_config %s cpu_config_str=%s cpus=%s"
           % (domain_path, remote_domain.propval("cpu_config_str"),
              remote_domain.propval("cpus")))
    if remote_domain.propval("cpu_config_str") == ['']:
        _error(f"openamp_xlnx: {domain_path}: no cpu_config_str; the domain "
               "was not expanded from YAML for an RPU cluster")
        return -1

    if remote_domain.propval("cpu_config_str") not in [ ['split'], ['lockstep'] ]:
        _error(f"openamp_xlnx: {domain_path}: cpu_config_str is "
               f"{remote_domain.propval('cpu_config_str')}, expected split "
               "or lockstep")
        return -1

    return { "split": CPU_CONFIG.RPU_SPLIT, "lockstep": CPU_CONFIG.RPU_LOCKSTEP }[remote_domain.propval("cpu_config_str")[0]]

def determinte_rpu_core(tree, cpu_config, remote_node):
    """Determine which RPU core index is used for the remote node.

    Args:
        tree (LopperTree): Device tree containing the remote node.
        cpu_config (CPU_CONFIG): RPU configuration (split or lockstep).
        remote_node (LopperNode): Remote node describing the remote processor.

    Returns:
        RPU_CORE | bool: Enum representing the selected core, or False on failure.

    Algorithm:
        YAML expansion stores the core's number across all RPU clusters in
        ``rpu_core_num``, taken from the SDT: the unit address N of a
        ``cpus-r5@N`` or ``cpus-r52@N`` cluster that holds one core, or the
        core's reg in a cluster that holds both. Remote domains expanded
        without it fall back to ``core_num``.
    """
    remote_path = getattr(remote_node, "abs_path", remote_node)
    _debug("openamp_xlnx: determinte_rpu_core %s %s" % (remote_path, cpu_config))
    rpu_core_num = remote_node.propval("rpu_core_num")
    if rpu_core_num != [''] and isinstance(rpu_core_num[0], int):
        return RPU_CORE(rpu_core_num[0])

    if remote_node.propval("core_num") == ['']:
        _error(f"openamp_xlnx: {remote_path}: no rpu_core_num or core_num, "
               "so its RPU core is not known")
        return False

    core_index = int(remote_node.propval("core_num")[0])
    return RPU_CORE(core_index)


def xlnx_validate_carveouts(tree, carveouts):
    """Verify that carveout regions do not overlap within reserved memory.

    Args:
        tree (LopperTree): Device tree containing reserved-memory nodes.
        carveouts (list[LopperNode]): Carveout nodes to validate.

    Returns:
        bool: True when no overlaps are detected and reserved-memory exists, False
        otherwise.

    Algorithm:
        Ensures the ``/reserved-memory`` node exists, gathers ``reg`` tuples from
        carveouts, and checks for pairwise overlap among relevant regions.
    """
    _debug("openamp_xlnx: xlnx_validate_carveouts")
    expect_ddr = any(["/reserved-memory/" in n.abs_path for n in carveouts])
    try:
        res_mem_node = tree["/reserved-memory"]
    except KeyError:
        if expect_ddr:
            _error("openamp_xlnx: carveouts %s are under /reserved-memory, "
                   "which the tree does not have" %
                   ", ".join(n.abs_path for n in carveouts
                             if "/reserved-memory/" in n.abs_path))
            return False

        res_mem_node = LopperNode(-1, "/reserved-memory")
        res_mem_node + LopperProp(name="ranges",value=[])
        tree.add(res_mem_node)

    if res_mem_node.propval('#size-cells') == [''] or res_mem_node.propval('#address-cells') == ['']:
        _error("openamp_xlnx: /reserved-memory needs #address-cells and "
               "#size-cells")
        return False

    try:
        carveout_pairs = {
            _required_reg_region(carveout) for carveout in carveouts
        }
    except ValueError as exc:
        _error(f"openamp_xlnx: {exc}")
        return False

    # validate no overlaps or conflicts by generating 2d array of reg values from each reserved memory
    # this array contains reg values for such validation
    res_mem_regions = []
    for node in res_mem_node.subnodes(children_only=True):
        base, size = node_reg_start_size(node)
        if base is None or size is None or size <= 0:
            continue
        res_mem_regions.append((base, size, node))

    for i in range(len(res_mem_regions)):
        base1, size1, node1 = res_mem_regions[i]

        for j in range(i + 1, len(res_mem_regions)):
            base2, size2, node2 = res_mem_regions[j]
            # Only validate relevant carveouts
            if ((base1, size1) not in carveout_pairs and
                    (base2, size2) not in carveout_pairs):
                continue
            # Overlap check
            if base1 < base2 + size2 and base2 < base1 + size1:
                _error(f"openamp_xlnx: reserved memory {node1.abs_path} "
                       f"({base1:#x}, size {size1:#x}) overlaps "
                       f"{node2.abs_path} ({base2:#x}, size {size2:#x})")
                return False

    return True

def platform_validate(platform):
    """Confirm that the detected SoC platform is supported.

    Args:
        platform (SOC_TYPE): Enum representing the current platform.

    Returns:
        bool: True when the platform is one of the supported SOC_TYPE values.

    Algorithm:
        Compares the provided enum against a whitelist and prints an error when the
        platform is not supported.
    """
    if platform not in RPU_FAMILIES:
        _error("openamp_xlnx: platform %s has no RPU remoteproc support"
               % platform)
        return False
    return True

def xlnx_remoteproc_v2_add_cluster(tree, platform, cpu_config, cluster_ranges_val, cluster_node_path):
    """Create or update the remoteproc cluster node for an RPU complex.

    Args:
        tree (LopperTree): Device tree to mutate.
        platform (SOC_TYPE): Detected SoC platform.
        cpu_config (CPU_CONFIG): RPU configuration (split or lockstep).
        cluster_ranges_val (list[int]): Flattened ``ranges`` property values.
        cluster_node_path (str): Path of the cluster node in the tree.

    Returns:
        bool: True when the cluster node is valid or successfully created.

    Algorithm:
        Derives compatibility strings/modes from the platform, merges ranges
        when in split mode, or constructs a new node populated with all
        required properties. ``xlnx_remoteproc_v2_construct_cluster`` has
        already rejected a relation whose mode conflicts with the cluster's.
    """
    family = RPU_FAMILIES[platform]

    cluster_modes = {
        CPU_CONFIG.RPU_SPLIT: 0,
        CPU_CONFIG.RPU_LOCKSTEP: 1,
    }

    cluster_node_props = {
      "compatible" : family.cluster_compatible,
      "#address-cells": 0x2,
      "#size-cells": 0x2,
      "xlnx,cluster-mode": cluster_modes[cpu_config.value],
      "ranges": cluster_ranges_val,
    }

    # R5 cores also need tcm mode
    if family.tcm_mode:
        cluster_node_props["xlnx,tcm-mode"] = cluster_modes[cpu_config.value]

    try:
        cluster_node = tree[cluster_node_path]

        # In split mode the cluster maps the banks of both cores: append this
        # core's ranges to those already in the cluster node.
        if cpu_config == CPU_CONFIG.RPU_SPLIT:
            existing_ranges = cluster_node.propval("ranges", list)
            if existing_ranges == [""]:
                existing_ranges = []
            cluster_node["ranges"] = existing_ranges + list(cluster_ranges_val)

    except KeyError:
        cluster_node = LopperNode(-1, cluster_node_path)
        for key in cluster_node_props:
            cluster_node + LopperProp(name=key, value = cluster_node_props[key])

        tree.add(cluster_node)

    return True

def xlnx_remoteproc_v2_add_core(tree, openamp_channel_info, power_domains, core_reg_val, core_reg_names, cluster_node_path, platform):
    """Insert a remoteproc core node beneath the cluster node.

    Args:
        tree (LopperTree): Device tree being updated.
        openamp_channel_info (dict): Aggregated metadata for the current channel.
        power_domains (list[int]): Flattened list of power-domain phandles/indices.
        core_reg_val (list[int]): Flattened ``reg`` values for core memories.
        core_reg_names (list[str]): Names corresponding to each ``reg`` entry.
        cluster_node_path (str): Absolute path to the cluster node.
        platform (SOC_TYPE): Detected SoC platform.

    Returns:
        LopperNode: The newly created core node.

    Algorithm:
        Determines the core node naming scheme from the platform and core index,
        builds the property set (compatibility, power domains, register ranges,
        memory regions), and attaches the node to the tree.
    """
    _debug("openamp_xlnx: xlnx_remoteproc_v2_add_core %s" % cluster_node_path)
    family = RPU_FAMILIES[platform]

    core_node = LopperNode(-1, "{}/{}@{}".format( cluster_node_path, family.core_node_name, int(openamp_channel_info["rpu_core"])))

    core_node_props = {
      "compatible" : family.core_compatible,
      "power-domains": power_domains,
      "reg": core_reg_val,
      "reg-names": core_reg_names,
      "memory-region": [ n.phandle for n in openamp_channel_info["new_ddr_nodes"] ]
    }

    if not openamp_channel_info["new_ddr_nodes"]:
        core_node_props.pop("memory-region")

    for key in core_node_props:
        core_node + LopperProp(name=key, value = core_node_props[key])

    tree.add(core_node)

    return core_node


def xlnx_rpu_sdt_tcm_view(remote_node):
    """Return the core-local TCM addresses that the SDT gives a remote.

    Args:
        remote_node (LopperNode): Remote domain node.

    Returns:
        dict[int, int]: Core-local address of each TCM bank, keyed by the
        bank's power-domain ID.

    Algorithm:
        YAML expansion copies the TCM entries of the remote core's RPU
        cluster ``address-map`` into ``rpu_tcm_view`` as (power-domain ID,
        core-local address, size) triples, because the cluster node is gone
        by the time OpenAMP runs on a domain tree. Only ZynqMP SDTs map TCM
        banks there.
    """
    cells = remote_node.propval("rpu_tcm_view", list)
    if cells == [""]:
        return {}
    return {cells[i]: cells[i + 1] for i in range(0, len(cells) - 2, 3)}


def xlnx_remoteproc_v2_construct_cluster(tree, openamp_channel_info, channel_elfload_nodes, verbose = 0):
    """Build the remoteproc cluster and core nodes for a channel.

    Args:
        tree (LopperTree): Device tree to mutate.
        openamp_channel_info (dict): Aggregated metadata describing the channel.
        channel_elfload_nodes (list[LopperNode]): ELFLOAD carveouts referenced by the channel.
        verbose (int): Verbosity flag for diagnostic output.

    Returns:
        LopperNode | bool: Newly created core node on success, or False on failure.

    Algorithm:
        Each TCM bank comes from the SDT: its global address and size from
        its ``reg``, its power domain from ``power-domains``, and its type
        (ATCM, BTCM, CTCM) from its name. Its core-local address comes from
        the RPU cluster ``address-map`` when the SDT maps the bank there, and
        otherwise from the family's TCM layout. In R5 lockstep the two
        cores' TCMs are combined: the remote lists the SDT's lockstep banks
        at 0xffe10000 and 0xffe30000 along with core 0's, and each bank's
        core-local address is its offset in the cluster's TCM span. R52
        cores do not combine TCM, so an R52 lockstep remote loads its own
        banks as in split mode. The remoteproc bank index is the core's
        position in its two-core RPU cluster. The cluster node is named after
        the base of the RPU cluster's TCM span, found from the remote's TCM
        addresses, or for a remote without TCM from the base that YAML
        expansion took from the SDT. Finally, tracks new DDR regions and
        inserts the core node using ``xlnx_remoteproc_v2_add_core``.
    """
    _debug("openamp_xlnx: xlnx_remoteproc_v2_construct_cluster")

    cpu_config = openamp_channel_info["cpu_config"]
    remote_node = openamp_channel_info["remote_node"]
    lockstep = cpu_config == CPU_CONFIG.RPU_LOCKSTEP

    rpu_core = determinte_rpu_core(tree, cpu_config, remote_node)
    if rpu_core is False:
        return False
    platform = get_platform(tree, verbose)
    if not platform_validate(platform):
        return False
    family = RPU_FAMILIES[platform]
    local_origins = TCM_LOCAL_ORIGINS[family.cpu_type]
    sdt_tcm_view = xlnx_rpu_sdt_tcm_view(remote_node)
    r5_lockstep = lockstep and family.cpu_type == "cortexr5"

    # Bank index of the core's TCM in the remoteproc ranges and reg: the
    # core's position in its two-core RPU cluster.
    core_index = int(rpu_core) % 2

    # A cluster in lockstep runs one firmware, on its core 0.
    if lockstep and core_index != 0:
        _error(f"openamp_xlnx: RPU core {int(rpu_core)} is core {core_index} "
               "of its cluster; a cluster in lockstep runs on its core 0")
        return False

    cluster_ranges_val = []
    core_reg_names = []
    core_reg_val = []
    power_domains = openamp_channel_info["rpu_core_pd_prop"].value

    # In R5 lockstep, core 1's ATCM and BTCM are the cluster's second ATCM
    # and BTCM, at 0xffe10000 and 0xffe30000.
    r5_core_reg_names_mappings = { "ffe00000" : "atcm0", "ffe20000" : "btcm0",
                                "ffe10000" : "atcm1", "ffe30000" : "btcm1",
                                "ffe90000" : "atcm1", "ffeb0000" : "btcm1" }
    r52_core_reg_names_mappings = { "atcm" : "atcm0", "btcm" : "btcm0", "ctcm": "ctcm0" }

    core_reg_names_mappings = r52_core_reg_names_mappings if family.cpu_type == "cortexr52" else r5_core_reg_names_mappings

    tcm_bank_nodes = {}
    cluster_bases = set()

    # loop through TCM nodes
    for n in [ n for n in channel_elfload_nodes if n.propval("xlnx,ip-name") != [''] ]:
        # Preserve the complete provider/specifier tuple for the generated
        # remoteproc node.
        pd = n.propval("power-domains", list)
        node_path = getattr(n, "abs_path", n.name)
        if not pd or pd == [""] or len(pd) < 2:
            _error(f"openamp_xlnx: TCM node {node_path} is missing a valid "
                   "power-domains property")
            return False
        power_domains.extend(pd)
        pd_id = pd[1]

        # Each bank has its own power domain. The SDT's R5 lockstep TCM
        # nodes carry core 0's power domains, where Linux expects core 1's,
        # so Linux would request one bank twice and never power the other.
        # Keep the SDT's value and say that the output is malformed.
        if pd_id in tcm_bank_nodes:
            pd_id_string = hex(pd_id) if isinstance(pd_id, int) else str(pd_id)
            _warning(f"openamp_xlnx: TCM node {node_path} has the same "
                     f"power domain ({pd_id_string}) as TCM node "
                     f"{tcm_bank_nodes[pd_id]}; the remoteproc node for "
                     f"remote {getattr(remote_node, 'abs_path', remote_node)} "
                     "lists it twice and is malformed. Fix the "
                     "power-domains of these nodes in the system device "
                     "tree.")
        else:
            tcm_bank_nodes[pd_id] = node_path

        tcm_base, tcm_size = node_reg_start_size(n)
        if tcm_base is None or not tcm_size:
            _error(f"openamp_xlnx: TCM node {node_path} is missing a valid "
                   "reg property")
            return False

        bank_type = tcm_bank_type(n.name)
        if bank_type not in local_origins:
            _error(f"openamp_xlnx: TCM node {node_path} is not an ATCM, BTCM "
                   f"or CTCM bank of a {family.cpu_type} core")
            return False

        cluster_base = rpu_cluster_tcm_base(tcm_base, family.cpu_type)
        if r5_lockstep:
            # The combined TCM keeps the global layout: core 1's banks at
            # local 0x10000 and 0x30000.
            local_address = tcm_base - cluster_base
        else:
            # The cluster address-map maps a bank either at its core-local
            # address or, on some SDTs, at its global address; only the
            # former is the core's view.
            local_address = sdt_tcm_view.get(pd_id)
            if local_address is None or local_address == tcm_base:
                local_address = local_origins[bank_type]
        local_view = [core_index, local_address]

        # Remoteproc ranges and reg use two address and two size cells.
        size_cells = int_to_cells(tcm_size, 2)
        core_reg_val.extend(local_view + size_cells)
        cluster_ranges_val.extend(local_view + int_to_cells(tcm_base, 2) +
                                  size_cells)
        cluster_bases.add(cluster_base)

        # map TCM node name to binding compliant TCM name
        if not any(tcm_name_substr in n.name.lower() for tcm_name_substr in core_reg_names_mappings):
            _error(f"openamp_xlnx: no reg-names entry for TCM node "
                   f"{node_path}; R5 TCM banks are named by their global "
                   "address")
            return False

        for tcm_substr in core_reg_names_mappings:
            if tcm_substr in n.name:
                core_reg_names.append(core_reg_names_mappings[tcm_substr])

    # The cluster node is named after the base of the RPU cluster's TCM
    # span, so both cores of a cluster share it.
    if len(cluster_bases) > 1:
        _error("openamp_xlnx: the TCM banks of remote "
               f"{getattr(remote_node, 'abs_path', remote_node)} are in more "
               "than one RPU cluster: " +
               ", ".join(hex(base) for base in sorted(cluster_bases)))
        return False
    if cluster_bases:
        cluster_base = cluster_bases.pop()
    else:
        sdt_base = remote_node.propval("rpu_cluster_base", list)
        if sdt_base == [""] or not isinstance(sdt_base[0], int):
            _error("openamp_xlnx: no TCM bank or RPU cluster base found for "
                   f"remote {getattr(remote_node, 'abs_path', remote_node)}")
            return False
        cluster_base = sdt_base[0]
    cluster_node_path = f"/remoteproc@{cluster_base:x}"

    # A core runs one firmware, so it has one remoteproc relation, and a
    # cluster in lockstep has one relation. Check before the cluster node
    # changes: a second core node would replace the first one's properties.
    try:
        cluster_node = tree[cluster_node_path]
    except KeyError:
        cluster_node = None
    if cluster_node is not None:
        lockstep_cluster = lockstep or cluster_node.propval(
            "xlnx,cluster-mode", list) == [int(CPU_CONFIG.RPU_LOCKSTEP)]
        for existing in cluster_node.subnodes(children_only=True):
            if lockstep_cluster:
                _error(f"openamp_xlnx: {existing.abs_path} already uses "
                       f"{cluster_node_path}; a cluster in lockstep can have "
                       "only one remoteproc relation")
                return False
            if existing.name.endswith(f"@{core_index}"):
                _error(f"openamp_xlnx: {existing.abs_path} already exists; "
                       "each RPU core can have only one remoteproc relation")
                return False
    if not xlnx_remoteproc_v2_add_cluster(tree, platform, cpu_config, cluster_ranges_val, cluster_node_path):
        return False

    openamp_channel_info["new_ddr_nodes"] = [ n for n in channel_elfload_nodes if n.propval("xlnx,ip-name") == [''] ]

    # add individual core node in cluster node
    return xlnx_remoteproc_v2_add_core(tree, openamp_channel_info, power_domains,
                                       core_reg_val, core_reg_names, cluster_node_path, platform)

def xlnx_remoteproc_rpu_parse(tree, node, openamp_channel_info, elfload_nodes, verbose = 0):
    """Populate RPU-specific metadata for a remoteproc relation.

    Args:
        tree (LopperTree): Device tree being analyzed.
        node (LopperNode): Remoteproc relation child node.
        openamp_channel_info (dict): Mutable accumulator for channel information.
        elfload_nodes (list[LopperNode]): Carveout nodes used for ELF loading.
        verbose (int): Verbosity flag for diagnostic output.

    Returns:
        bool: True when parsing succeeds, False on validation errors.

    Algorithm:
        Determines CPU configuration mode, resolves the targeted RPU core index,
        validates required power-domain properties, and stores the derived values in
        ``openamp_channel_info`` for downstream processing.
    """
    _debug("openamp_xlnx: xlnx_remoteproc_rpu_parse %s"
           % getattr(node, "abs_path", node))

    remote_node = openamp_channel_info["remote_node"] 
    remote_path = getattr(remote_node, "abs_path", remote_node)
    cpu_config = determine_cpus_config(remote_node)
    if cpu_config not in [ CPU_CONFIG.RPU_LOCKSTEP, CPU_CONFIG.RPU_SPLIT]:
        # determine_cpus_config has reported why.
        return False

    rpu_core = determinte_rpu_core(tree, cpu_config, remote_node )
    if rpu_core not in RPU_CORE:
        _error(f"openamp_xlnx: {remote_path}: no valid RPU core ({rpu_core})")
        return False

    if remote_node.propval("rpu_pd_val") == ['']:
        _error(f"openamp_xlnx: {remote_path}: its RPU core has no "
               "power-domains")
        return False

    openamp_channel_info["rpu_core_pd_prop"] = remote_node.props("rpu_pd_val")[0]
    openamp_channel_info["cpu_config"] = cpu_config
    # Index of the core in its two-core cluster, used to name the core node.
    openamp_channel_info["rpu_core"] = str(int(rpu_core) % 2)

    return True

banner_printed = False
def get_platform(tree, verbose = 0):
    """Derive the platform enum from the root node's model/compatible strings.

    Args:
        tree (LopperTree): Device tree object.
        verbose (int): Verbosity flag controlling banner output.

    Returns:
        SOC_TYPE | None: Enum value representing the platform, or None when unknown.

    Algorithm:
        Combines the root node's ``compatible`` and ``model`` properties, optionally
        emits a banner once per execution, and scans for known substrings to map the
        tree onto a ``SOC_TYPE`` enum value.
    """
    # set platform
    global banner_printed
    platform = None
    root_node = tree["/"]

    inputs = root_node.propval("compatible") + root_node.propval("model") + root_node.propval("device_id")

    zynqmp = [ 'Xilinx ZynqMP',  "xlnx,zynqmp" ]
    versal = [ 'xlnx,versal', 'Xilinx Versal']
    versalnet = [ 'versal-net', 'Versal NET', "xlnx,versal-net", "Xilinx Versal NET" ]
    versal2 = [ 'xlnx,versal2', 'amd,versal2', 'amd versal vek385 reva', 'xc2ve3858' ]

    rpu_socs = [ versal2, zynqmp, versal, versalnet ]
    rpu_socs_enums = [ SOC_TYPE.VERSAL2, SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL, SOC_TYPE.VERSAL_NET ]

    if verbose > 0 and not banner_printed:
        _debug("openamp_xlnx: platform info: %s" % inputs)
        banner_printed = True

    for index, soc in enumerate(rpu_socs):
        for soc_str in soc:
            for i in inputs:
                if i == soc_str:
                    return rpu_socs_enums[index]

    if platform is None:
        _debug("openamp_xlnx: no RPU platform matches %s" % inputs)

    return platform

def _report_unsupported_platform(tree):
    """Log that the tree's platform has no OpenAMP RPU support."""
    root = tree["/"]
    _error("openamp_xlnx: unsupported platform: model %s, compatible %s"
           % (root.propval("model"), root.propval("compatible")))

def openamp_nontree_outputs_handler(sdt, output_file_name, openamp_args, verbose = 0 ):
    """Derive the platform enum from the root node's model/compatible strings.
       This handler is called where outputs can be derived from the existing tree.
       typically just YAML -> DTS translation
       currently handles:
            1. BM / freertos RPU openamp header
            2. libmetal ipc .cmake output file
    Args:
        sdt (LopperSDT): Lopper system device tree with tree object stored.
        output_file_name (str): output file name
        openamp_args (Dict): dictionary of relevant arguments.
        verbose (int): Verbosity flag controlling banner output.

    Returns:
        True or False.

    Algorithm:
        Gather relation's ipi node and carveouts. Then determine the use case. Based on this
        call the output-file routine. That output-file routine shall return True or False.
    """
    _debug("openamp_xlnx: openamp_nontree_outputs_handler %s" % output_file_name)
    platform = get_platform(sdt.tree, verbose)
    if platform is None:
        _report_unsupported_platform(sdt.tree)
        return False

    # get_cpu_node expects dictionary where first arg first element is machine
    machine = openamp_args['machine']

    # OS is used to determine if (a) machine should be used and (b) map to domain
    os = openamp_args["dt_type"]

    match_cpunode = get_cpu_node(sdt, {'args':[machine]}) if os != "linux_dt" else None
    if not match_cpunode and os != "linux_dt":
        _error(
            "openamp_xlnx: cannot generate '%s': processor '%s' was not "
            "found in the system device tree" % (output_file_name, machine),
            1,
        )

    domains = sdt.tree['/domains']
    relation_node = None
    supported_targets = []
    relation_parent_search = bool(openamp_args['relation_parent'] is not None)
    compatible_string_search = bool(openamp_args['compatible_string'] is not None)
    for n in domains.subnodes():
        if n.parent is None or n.parent.parent is None:
            continue

        if n.parent.parent.propval("cpus") == ['']:
            continue

        # search based on compatible string of relation
        if compatible_string_search and n.propval("compatible") != [openamp_args['compatible_string']]:
            continue

        domain_node = n.parent.parent
        domain_os = domain_node.propval("os,type")
        domain_os = domain_os[0] if domain_os and domain_os != [''] else "unspecified"
        domain_processor = n.parent.propval("cluster_cpu")
        if domain_processor and domain_processor != ['']:
            domain_processor = domain_processor[0]
        else:
            cpu_ref = domain_node.propval("cpus")
            cpu_cluster = sdt.tree.pnode(cpu_ref[0]) if cpu_ref and cpu_ref != [''] else None
            domain_processor = cpu_cluster.label if cpu_cluster and cpu_cluster.label else \
                cpu_cluster.name if cpu_cluster else "unspecified"
        supported_targets.append("%s (os=%s, processor=%s)" %
                                 (domain_node.name, domain_os, domain_processor))

        # ensure target domain matches
        if (os != "linux_dt"
                and not _openamp_domain_selects_cpu(
                    sdt.tree, domain_node, match_cpunode)):
            continue

        # filter based on name
        if relation_parent_search and openamp_args['relation_parent'] != n.name:
            continue

        relation_node = n
        break

    if relation_node is None:
        compatible = openamp_args['compatible_string'] or "any"
        targets = ", ".join(supported_targets) if supported_targets else "none"
        _error(
            "openamp_xlnx: cannot generate '%s': no %s relation found for "
            "processor '%s' and OS '%s'; supported targets: %s" %
            (output_file_name, compatible, machine, os, targets),
            1,
        )

    carveouts = None
    ipi_node = None
    relation_node_search = bool(openamp_args['relation'] is not None)
    for node in relation_node.subnodes(children_only=True):
        if relation_node_search and openamp_args['relation'] != node.name:
            continue

        pname = "remote" if os == "linux_dt" else "host"
        # check for remote property
        if not node.props(pname):
            _error(f"openamp_xlnx: {node.abs_path} is missing {pname} "
                   "property")
            return False

        # first find host to remote IPI
        mbox_pval = node.propval("mbox")
        if mbox_pval == ['']:
            _error(f"openamp_xlnx: {node.abs_path} is missing mbox property")
            return False

        ipi_node = sdt.tree.pnode(mbox_pval[0])
        if ipi_node is None:
            _error(f"openamp_xlnx: {node.abs_path}: mbox does not reference "
                   "an IPI")
            return False

        carveout_prop = node.propval("carveouts")
        if carveout_prop == ['']:
            _error(f"openamp_xlnx: {node.abs_path} is missing carveouts "
                   "property")
            return False

        carveouts = [ sdt.tree.pnode(phandle) for phandle in carveout_prop ]

        if not openamp_args['libmetal_output_file']:
            return xlnx_openamp_gen_outputs_only(sdt.tree, machine, output_file_name, carveouts, ipi_node, verbose)

        if [openamp_args['compatible_string']] == relation_node.propval("compatible"):
            timer_pval = node.propval("timer")
            if timer_pval == ['']:
                _error(f"openamp_xlnx: {node.abs_path} is missing timer "
                       "property")
                return False

            timer_node = sdt.tree.pnode(node.propval("timer")[0])

            return xlnx_libmetal_gen_output_file(sdt.tree, output_file_name, carveouts, ipi_node, timer_node, os, verbose)

    return False

def xlnx_remoteproc_parse(tree, remoteproc_relation_node, carveout_validation_arr, verbose = 0 ):
    """Parse remoteproc relations and construct core nodes.

    Args:
        tree (LopperTree): Device tree being updated.
        remoteproc_relation_node (LopperNode): Domain relation describing remoteproc channels.
        carveout_validation_arr (list[LopperNode]): Accumulator for carveout validation.
        verbose (int): Verbosity level for diagnostic printing.

    Returns:
        dict[str, LopperNode] | bool: Mapping of remote node names to created core nodes,
        or False when parsing fails.

    Algorithm:
        Verifies platform support, iterates relation children, validates required
        properties, tracks ELFLOAD carveouts, enriches channel metadata via
        ``xlnx_remoteproc_rpu_parse``, constructs cluster/core nodes, and records the
        core nodes for later RPMsg processing.
    """
    _debug("openamp_xlnx: xlnx_remoteproc_parse %s"
           % remoteproc_relation_node.abs_path)

    # Xilinx OpenAMP subroutine to collect Remoteproc information from relation node in tree
    if get_platform(tree, verbose) is None:
        _report_unsupported_platform(tree)
        return False

    channel_to_core_dict = {}

    for node in remoteproc_relation_node.subnodes(children_only=True):
        # check for remote property
        if node.propval("remote") == ['']:
            _error(f"openamp_xlnx: {node.abs_path} is missing remote "
                   "property")
            return False

        remote_node = tree.pnode(node.propval("remote")[0])
        openamp_channel_info = { "remote_node": remote_node }

        # check for elfload prop
        if not node.props("elfload"):
            _error(f"openamp_xlnx: {node.abs_path} is missing elfload "
                   "property")
            return False

        channel_elfload_nodes = [ tree.pnode(current_elfload) for current_elfload in node.propval("elfload") ]
        # validate later
        carveout_validation_arr.extend(channel_elfload_nodes)

        if not xlnx_remoteproc_rpu_parse(tree, node, openamp_channel_info, channel_elfload_nodes, verbose):
            return False

        core_node = xlnx_remoteproc_v2_construct_cluster(tree, openamp_channel_info, channel_elfload_nodes, verbose = 0)
        if not core_node:
            return False

        channel_to_core_dict[remote_node.name] = core_node # save core node for later use by rpmsg processing

    return channel_to_core_dict

def xlnx_openamp_find_compat_domains(tree, delete_nodes = False):
    """Locate or remove OpenAMP-compatible domain nodes.

    Args:
        tree (LopperTree): Device tree object.
        delete_nodes (bool): When True, delete matching domains instead of reporting them.

    Returns:
        bool: True when compatible domains are found (or removed), False otherwise.

    Algorithm:
        Scans the ``/domains`` node for children whose compatibility matches the known
        OpenAMP strings and optionally deletes them from the tree.
    """
    try:
        domain_node = tree["/domains"]
    except:
        return False

    for n in tree["/domains"].subnodes(children_only=True):
        node_compat = n.propval("compatible")
        if node_compat == ['']:
            continue
        if node_compat[0] in [ REMOTEPROC_D_TO_D_v2, RPMSG_D_TO_D, LIBMETAL_D_TO_D ]:
            if delete_nodes:
                tree - n
            else:
                return True

    return False

def parse_openamp_args(arg_inputs):
    """Parse command-line style arguments for the OpenAMP assist.

    Args:
        arg_inputs (list[str]): Argument list passed to the assist.

    Returns:
        dict: Normalized configuration containing output filename, machine name,
        and detected device tree type.

    Algorithm:
        Filters the argument list for OpenAMP-specific flags, uses argparse to obtain
        structured values, infers the device tree type from positional inputs, and
        normalizes the result for downstream consumption.
    """
    parser = argparse.ArgumentParser(description="OpenAMP argument parser")
    parser.add_argument("--openamp_output_filename", type=str, help="Output header file name")
    parser.add_argument("--openamp_remote", type=str, help="OpenAMP remote machine name")
    parser.add_argument("--openamp_header_only", action='store_true', help="OpenAMP flag to denote to only generate RPU app header. Only relevant for FreeRTOS / BM cases.")

    # This can be used for host or remote. Which means --openamp_remote is equivalent to --processor for remote case
    parser.add_argument("--processor", type=str, help="OpenAMP target processor machine name")

    parser.add_argument("--libmetal_output_file", action='store_true', help="If present - then attempt to decipher relevant IPI for the specified OpenAMP or Libmetal relation. This will also require --compatible-string and --processor. Optionally --relation-parent and --relation are used to specify non-default (e.g. first found) relation.")
    parser.add_argument("--compatible-string", type=str, help="compatible string for relation. expecting either \"libmetal,ipc-v1\" or \"openamp,rpmsg-v1\"")
    parser.add_argument("--os", type=str, help="OS arg")
    parser.add_argument("--relation-parent", type=str, help="parent of relation")
    parser.add_argument("--relation", type=str, help="target relation")
    parser.add_argument("--report-valid-ipis", type=str, metavar="PROCESSOR",
                        help="report supported and configured IPI mappings for PROCESSOR")

    config = {}
    if len(arg_inputs) == 2 and arg_inputs[1] in ["linux_dt", "zephyr_dt"]:
        config["dt_type"] = arg_inputs[1]
        config["machine"] = arg_inputs[0]
        for i in ["processor", "os", "libmetal_output_file", "openamp_remote", "openamp_output_filename"]:
            config[i] = None
    elif len(arg_inputs) == 1:
        config["dt_type"] = "baremetal_dt"
        config["machine"] = arg_inputs[0]
        for i in ["processor", "os", "ipi_mapping", "openamp_remote", "openamp_output_filename"]:
            config[i] = None
    else:
        args = parser.parse_args(arg_inputs)
        config = vars(args)
        config["dt_type"] = config["os"]
        config["machine"] = False

        if config["report_valid_ipis"]:
            config["machine"] = config["report_valid_ipis"]
            config["dt_type"] = "report"

        if config["processor"] and not config["machine"]:
            config["machine"] = config["processor"]
        elif config["openamp_remote"] and config["openamp_header_only"] and not config["machine"]:
            config["machine"] = config["openamp_remote"]
        elif not config["machine"]:
            _error("openamp_xlnx: no processor given; pass it as the first "
                   "assist argument")
            return False

        # handling for ipi mapping workflow
        if config["libmetal_output_file"] and not config["compatible_string"]:
            _error("openamp_xlnx: libmetal output needs a compatible string "
                   "argument")
            return False

        # provide default output file for IPI mapping use case if none provided
        if config["libmetal_output_file"] and not config["openamp_output_filename"]:
            _info("openamp_xlnx: no libmetal output file given; writing "
                  "libmetal_output_file.cmake")
            config["openamp_output_filename"] = "libmetal_output_file.cmake"

    return config

def xlnx_openamp_parse(sdt, options, verbose = 0 ):
    """Entry point for the OpenAMP assist to process remoteproc/RPMsg data.

    Args:
        sdt (LopperSDT): Structured device tree wrapper.
        options (dict): Plugin options containing ``args``.
        verbose (int): Verbosity level for diagnostic output.

    Returns:
        bool: True when processing succeeds or no domains exist, False on errors.

    Raises:
        SystemExit: If a requested OpenAMP relation cannot be processed.

    Algorithm:
        Parses assist arguments, checks for OpenAMP-compatible domains, delegates
        relation handling when appropriate. Relation-processing failures are
        fatal because continuing would write an incomplete OpenAMP device tree.
    """
    # Xilinx OpenAMP subroutine to parse OpenAMP Channel
    # information and generate Device Tree information.
    _info("openamp_xlnx: parsing OpenAMP metadata")
    openamp_args = parse_openamp_args(options['args'])
    if not openamp_args:
        return False

    tree = sdt.tree
    ret = -1
    machine = openamp_args["machine"]

    if openamp_args.get("report_valid_ipis"):
        return xlnx_openamp_report_valid_ipis(sdt, machine)

    if not xlnx_openamp_find_compat_domains(tree):
        _info("openamp_xlnx: no OpenAMP domains found")
        return True

    if openamp_args["openamp_output_filename"]:
        return openamp_nontree_outputs_handler(sdt, openamp_args["openamp_output_filename"], openamp_args, 1 )

    xlnx_openamp_update_relation_timers(
        sdt, openamp_args["dt_type"], machine)

    if openamp_args["dt_type"] in ["zephyr_dt", "linux_dt"] or openamp_args["openamp_output_filename"]:
        # if find_only is False, then processing will also occur.
        if not xlnx_handle_relations(sdt, machine, False, openamp_args["dt_type"]):
            _error(
                "openamp_xlnx: failed to process OpenAMP relations for "
                "processor '%s' and OS '%s'" %
                (machine, openamp_args["dt_type"]),
                1,
            )

    return True
