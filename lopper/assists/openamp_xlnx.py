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

sys.path.append(os.path.dirname(__file__))
from openamp_xlnx_common import *
from baremetalconfig_xlnx import get_cpu_node
from string import ascii_lowercase as alc

RPU_PATH = "/rpu@ff9a0000"
REMOTEPROC_D_TO_D = "openamp,remoteproc-v1"
REMOTEPROC_D_TO_D_v2 = "openamp,remoteproc-v2"
RPMSG_D_TO_D = "openamp,rpmsg-v1"
LIBMETAL_D_TO_D = "libmetal,ipc-v1"

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
        print("OPENAMP: XLNX: ERROR: expected node ref in xlnx_openamp_keep_node")
        return False

    conditions = [
        linux_dt and "uio" in node.propval('compatible', list),
        "vnd,mbox-consumer" in node.propval('compatible', list),
        "zephyr,mbox-ipm" in node.propval('compatible', list),
    ]

    return any(c for c in conditions if c)


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
        print("xlnx_handle_relations: unable to find machine: ", machine)
        return False

    parse_routines = { REMOTEPROC_D_TO_D_v2: xlnx_remoteproc_parse, RPMSG_D_TO_D: xlnx_rpmsg_parse, LIBMETAL_D_TO_D : xlnx_rpmsg_parse }

    # first collect all relevant openamp domains
    remoteproc_relations = []
    rpmsg_relations = []

    for n in sdt.tree["/domains"].subnodes():
        node_compat = n.propval("compatible")[0]
        if node_compat == '':
            continue

        if n.parent.parent.propval("cpus") == ['']:
            continue

        # ensure target domain matches
        if match_cpunode.parent == sdt.tree.pnode(n.parent.parent.propval("cpus")[0]):
            if find_only:
                 return n
            else: # do processing on found ndoes
                if node_compat in parse_routines.keys():
                    arr = remoteproc_relations if node_compat == REMOTEPROC_D_TO_D_v2 else rpmsg_relations

                    # skip libmetal relations for Linux
                    if os == "linux_dt" and node_compat == LIBMETAL_D_TO_D:
                        continue

                    arr.append(n)

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

    # check if conflicts in ELFLOAD and IPC carveouts
    if not xlnx_validate_carveouts(tree, carveout_validation_arr):
            return False

    if not remoteproc_relations and not rpmsg_relations:
        print("OPENAMP: XLNX: WARNING: no remoteproc or rpmsg relations found for machine", machine)

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
    print(" -> xlnx_rpmsg_update_tree_linux", node)
    vdev0buf_node = [n for n in rpmsg_carveouts if "vdev0buf" in n.name]
    if len(vdev0buf_node) == 1:
        vdev0buf_node[0] + LopperProp(name="compatible", value="shared-dma-pool")
    else:
        print("ERROR: missing or multiple vdev0buf nodes for linux rpmsg case")
        return False

    # vdev0buf should be first after the ELF load prop already in memory-region
    vdev0buf = [ index for index, rc in enumerate(rpmsg_carveouts) if "vdev0buffer" in rc.name ]
    if len(vdev0buf) != 1:
        print("ERROR: xlnx_rpmsg_update_tree_linux: expected 1 vdev0buffer node. got ", vdev0buf)
        return False

    vdev0buf = rpmsg_carveouts.pop(vdev0buf[0])
    rpmsg_carveouts.insert(0, vdev0buf)
    new_mem_region_prop_val = core_node.propval("memory-region")

    [ new_mem_region_prop_val.append(rc.phandle) for rc in rpmsg_carveouts ]

    # If DDRBOOT, ensure that it is after RPMSG carveouts
    # # save ddrboot node and add to end of list
    ddrboot_node_index = [ index for index, phandle in enumerate(new_mem_region_prop_val) if "ddrboot" in tree.pnode(phandle).name ]
    if ddrboot_node_index != []:
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
        print("unable to find machine: ", machine)
        return False

    # map machine to CPU node and then to openamp domain with relation
    target_node = None
    for n in sdt.tree["/domains"].subnodes(children_only=True):
        node_compat = n.propval("compatible")[0]
        # domains must (a) have compatible and (b) relate to CPU
        if node_compat == '' or n.parent.parent.propval("cpus") == ['']:
            continue

        # ensure target domain matches
        if match_cpunode.parent == sdt.tree.pnode(n.parent.parent.propval("cpus")[0]):
             target_node = n
             break

    if target_node == None:
        print("OPENAMP: XLNX: ERROR: unable to map machine", machine, "to relation")
        return False

    # find node described in the domain that is for ELF LOAD

    # remote should only have one relevant host
    rpmsg_rels = target_node.subnodes(children_only=True)
    if len(rpmsg_rels) != 1:
        print("OPENAMP: XLNX: ERROR: expected 1 and only 1 rpmsg relation for ", target_node)
        return False

    rpmsg_rel = rpmsg_rels[0]
    if rpmsg_rel.propval("host") == [''] and len(rpmsg_rel.propval("host")) != 1:
        print("OPENAMP: XLNX: ERROR: expected host prop for", target_node)
        return False

    host = rpmsg_rel.propval("host")
    host_node = sdt.tree.pnode(rpmsg_rel.propval("host")[0])
    if not isinstance(host_node, LopperNode):
        print("OPENAMP: XLNX: ERROR: expected host node ref in host prop for", rpmsg_rel)
        return False

    # look through host for matching remoteproc relation. If found then return the relation's elfload property reg value
    for rel in host_node.subnodes(children_only=True):
        if rel.propval("remote") != [''] and ['openamp,remoteproc-v2'] == rel.parent.propval("compatible"):
            if rel.propval("remote") == ['']:
                print("OPENAMP: XLNX: ERROR: elfload needs remoteproc host to describe elfload region")
                return False

            #  check that the referenced remote matches
            referenced_remote_domain = sdt.tree.pnode(rel.propval("remote")[0])
            if referenced_remote_domain == None or referenced_remote_domain != target_node.parent.parent:
                print("OPENAMP: XLNX: ERROR: referenced remote is invalid for host", rel)
                return False

            elfload_nodes = [ sdt.tree.pnode(i) for i in rel.propval("elfload") ]
            relevant_elfload_nodes = [ i for i in elfload_nodes if i != None and 'mmio-sram' not in i.propval('compatible')]
            if relevant_elfload_nodes == []:
                print("OPENAMP: XLNX: ERROR: expected at least one ELFLOAD node for case of generating openamp linker script using DDR.")
                return False

            # return reg from match
            reg_val = relevant_elfload_nodes[0].propval("reg")
            if reg_val == ['']:
                print("OPENAMP: XLNX: ERROR: expected 'reg' property for elfload entry", relevant_elfload_nodes[0])
                return False

            return (reg_val[1], reg_val[3])

    print("OPENAMP: XLNX: ERROR: unable to find elf load carveout")
    return False

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

    if len(ipc_nodes) != 3:
        print("ERROR: zephyr: rpmsg: only length of 3 ipc node allowed. got: ", ipc_nodes)
        return False

    # have to now combine the 3 IPC nodes into one.
    base = 0xFFFFFFFF
    size = 0
    for i in ipc_nodes:
        reg = i['reg']
        size += reg[3]
        if reg[1] < base:
            base = reg[1]

    # remove current IPC nodes. Create combined one below.
    [ tree - node for node in ipc_nodes ]

    new_ipc_node = LopperNode(-1, "/reserved-memory/ipc@%s" % hex(base)[2:])
    new_ipc_node + LopperProp(name="reg", value=[0, base, 0, size])
    new_ipc_node + LopperProp(name="compatible", value="mmio-sram")

    tree + new_ipc_node
    tree['/chosen']['zephyr,ipc_shm'] = new_ipc_node.abs_path

    if domain_node.propval("xlnx,ddr-boot") != []:
        elfload_nodes = [ tree.pnode(x) for x in domain_node.propval("reserved-memory") ]
        valid_elfload_node = [ node for node in elfload_nodes if node and node.propval("device_type") == ['memory'] ]
        if len(valid_elfload_node) > 0:
            tree['/chosen']['zephyr,sram'] = valid_elfload_node[0].abs_path

    # only create a node for this the first time. in the future this will go away as upstream wants use of ipm mbox node. this is here for bkwd compatibility
    try:
        # if here then mbox_consumer_node was already created.
        mbox_consumer_node = tree['/mbox-consumer']
    except KeyError:
        mbox_consumer_node = LopperNode(-1, "/mbox-consumer")
        mbox_consumer_props = { "compatible" : 'vnd,mbox-consumer', "mboxes" : [ipi_node.phandle, 0, ipi_node.phandle, 1], "mbox-names" : ['tx', 'rx'] }
        [mbox_consumer_node + LopperProp(name=n, value=mbox_consumer_props[n]) for n in mbox_consumer_props.keys()]
        tree.add(mbox_consumer_node)

    mbox_ipm_node = LopperNode(-1, "/mbox_ipi_%s_%s" % (hex(ipi_node['reg'][1])[2:], hex(ipi_node.parent['reg'][1])[2:]))
    mbox_ipm_props = { "compatible" : "zephyr,mbox-ipm", "mbox-names" : ['tx', 'rx'], "status": "okay", "mboxes" : [ipi_node.phandle, 0, ipi_node.phandle, 1] }
    [mbox_ipm_node +  LopperProp(name=n, value=mbox_ipm_props[n]) for n in mbox_ipm_props.keys()]
    tree.add(mbox_ipm_node)

    # do this for upstream compatibility for now
    if RPMSG_D_TO_D == relation_compat:
        tree['/chosen']['zephyr,ipc'] = mbox_ipm_node.abs_path

    if tree['/chosen'].propval('zephyr,flash') != ['']:
        tree['/chosen'].delete(sdt.tree['/chosen']['zephyr,flash'])
    if tree['/chosen'].propval('zephyr,ocm') != ['']:
        tree['/chosen'].delete(sdt.tree['/chosen']['zephyr,ocm'])

    if get_platform(tree, 0) == SOC_TYPE.VERSAL2:
        try:
             serial1_node = tree['/axi/serial@f1930000']
             tree - serial1_node
        except:
            pass

    return True

def xlnx_openamp_gen_outputs_ipi_mapping(tree, output_file, ipi_node, os, verbose = 0 ):
    """Generate .cmake file for Libmetal IPI usage

    Args:
        tree (LopperTree): Device tree being inspected for metadata.
        ipi_node (LopperNode): IPI node used
        os (str): os value
        verbose (int): Verbosity flag for diagnostic printing.

    Returns:
        bool: True on successful file generation, False on failure.
    """
    platform = get_platform(tree, verbose)
    if platform == None:
        return False

    cmake_file_template = "add_definitions(-DLIBMETAL_CFG_PROVIDED)\n"
    cmake_file_entry = None
    cmake_file_dict = {}

    if os == "linux_dt":
        suffix = "ipi" if platform == SOC_TYPE.ZYNQMP else "mailbox"
        cmake_file_template += "set (LIBMETAL_DEMO_IPI \"$parent_ipi_node\") # this is linux platform bus name of the relevant node.\n"
        cmake_file_template += "#the node used is $parent_ipi_node_path\n"
        cmake_file_template += "set (LIBMETAL_DEMO_IPI_BITMASK $bitmask)\n"
        cmake_file_template += "# this is bitmask to kick remote using node $ipi_node_path"

        cmake_file_dict["bitmask"] = hex(ipi_node['xlnx,ipi-bitmask'].value[0])
        cmake_file_dict["ipi_node_path"] = ipi_node.abs_path
        cmake_file_dict["parent_ipi_node_path"] = ipi_node.parent.abs_path
        cmake_file_dict["parent_ipi_node"] = "%s.%s" % (hex(ipi_node.parent['reg'][1])[2:], suffix)
    elif os == "zephyr_dt":
        cmake_file_template += "set (LIBMETAL_DEMO_IPI $ipm_mbox_node)\n"
        cmake_file_template += "# this is path to the IPI node in Zephyr RPU DT\n"
        cmake_file_template += "# IPI used is $ipi_node_path"
        cmake_file_entry = "mbox_ipi_%s_%s" % ((hex(ipi_node['reg'][1])[2:]), hex(ipi_node.parent['reg'][1])[2:])
        cmake_file_dict = {"ipm_mbox_node": cmake_file_entry, "ipi_node_path": ipi_node.abs_path}
    else:
        print("unsupported os:", os)
        return False

    try:
        with open(output_file, "w") as f:
            output = Template(cmake_file_template)
            f.write(output.substitute(cmake_file_dict))
    except Exception as e:
        print("OPENAMP: XLNX: ERROR: xlnx_openamp_gen_outputs_ipi_mapping: Error in generating template for RPU header.", e)
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
    vring_total_sz = hex(sum([n.propval("reg")[3] for n in vrings]))
    shm_pa = hex(min([n.propval("reg")[1] for n in vrings]))
    shbuf_sz = hex([n.propval("reg")[1] for n in memory_region_nodes if 'vdev0buffer' in n.name][0])

    remote_ipi = host_ipi.parent

    remote_vect_id = remote_ipi.propval('xlnx,int-id')[0]
    ipi_irq_vect_id = hex(remote_vect_id)
    ipi_irq_vect_id_rtos = hex(remote_vect_id-32)
    remote_ipi_str = hex(remote_ipi.propval('reg')[1])
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
        "SHARED_BUF_OFFSET": vring_total_sz,
        "EXTRAS":"",
        }

        with open(output_file, "w") as f:
            output = Template(platform_info_header_r5_template)
            f.write(output.substitute(inputs))
    except Exception as e:
        print("OPENAMP: XLNX: ERROR: xlnx_openamp_gen_outputs_only: Error in generating template for RPU header.", e)
        return False

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
    print(" -> xlnx_rpmsg_parse", rpmsg_relation_node)

    platform = get_platform(tree, verbose)
    if platform == None:
        return False

    for node in rpmsg_relation_node.subnodes(children_only=True):
        pname = "remote" if os == "linux_dt" else "host"
        # check for remote property
        if node.props(pname) == []:
            print("ERROR: ", node, "is missing remote property")
            return False

        remote_node = tree.pnode(node.propval(pname)[0])
        if remote_node == None:
             print("ERROR: invalid rpmsg ", pname," for relation: ", rpmsg_relation_node)
             return False

        if os == "linux_dt" and remote_node.name not in channel_to_core_dict.keys():
            print("ERROR: missing needed remoteproc core node for rpmsg parse to occur.")
            return False

        core_node = channel_to_core_dict[remote_node.name] if os == "linux_dt" else None

        # first find host to remote IPI
        mbox_pval = node.propval("mbox")
        if mbox_pval == ['']:
            print("ERROR: ", node, " is missing mbox property")
            return False

        ipi_node = tree.pnode(mbox_pval[0])
        if ipi_node == None:
            print("ERROR: Unable to find ipi")
            return False

        carveouts_node = tree[node.parent.parent.parent.abs_path + "/domain-to-domain/rpmsg-relation"]
        carveout_prop = node.propval("carveouts")
        if carveout_prop == ['']:
            print("ERROR: ", node, " is missing carveouts property")
            return False

        rpmsg_carveouts = [ tree.pnode(phandle) for phandle in carveout_prop ]

        # validate later
        carveout_validation_arr.extend(rpmsg_carveouts)

        # until domain access is in place - need to manually prune some nodes
        try:
            res_mem_node = tree["/reserved-memory"]
            [ tree.delete(i) for i in res_mem_node.subnodes() if i.propval("compatible") == ['mmio-sram'] and os == "linux_dt" ]
            if res_mem_node.propval("ranges") == [1]:
                res_mem_node.delete("ranges")
                res_mem_node + LopperProp(name="ranges")
        except KeyError:
            print("ERROR: carveouts should be in reserved memory.")
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
    print(" -> determine_cpus_config ", remote_domain, remote_domain.propval("cpu_config_str"), remote_domain.propval("cpus"))
    if remote_domain.propval("cpu_config_str") == ['']:
        print(" determine_cpus_config failed. could not find cpu_config_str property on remote domain", remote_domain)
        return -1

    if remote_domain.propval("cpu_config_str") not in [ ['split'], ['lockstep'] ]:
        print(" determine_cpus_config failed. invalid cpu_config_str: ", remote_domain.propval("cpu_config_str"))
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
        Validates the presence of ``core_num`` and converts it into the appropriate
        ``RPU_CORE`` enum instance.
    """
    print(" -> determinte_rpu_core", cpu_config, remote_node)
    if remote_node.propval("core_num") == ['']:
        print(" determinte_rpu_core failed. could not find core_num property no node: ", remote_node)
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
    print(" -> xlnx_validate_carveouts")
    expect_ddr = any(["/reserved-memory/" in n.abs_path for n in carveouts])
    try:
        res_mem_node = tree["/reserved-memory"]
    except KeyError:
        if expect_ddr:
            print("ERROR: carveouts should be in reserved memory.")
            return False

        res_mem_node = LopperNode(-1, "/reserved-memory")
        res_mem_node + LopperProp(name="ranges",value=[])
        tree.add(res_mem_node)

    carveout_pairs = [ [ carveout.propval("reg")[1], carveout.propval("reg")[3] ] for carveout in carveouts ]

    # validate no overlaps or conflicts by generating 2d array of reg values from each reserved memory
    # this array contains reg values for such validation
    res_mem_regs = [ n.propval("reg") for n in res_mem_node.subnodes(children_only=True) if n.propval("reg") != [''] ]
    for i in range(len(res_mem_regs)):
        base1, size1 = res_mem_regs[i][1], res_mem_regs[i][3]
        for j in range(i + 1, len(res_mem_regs)):
            base2, size2 = res_mem_regs[j][1], res_mem_regs[j][3]
            if [ base1, size1 ] not in carveout_pairs: # only validate relevant carveouts
                continue
            if base1 < base2 + size2 and base2 < base1 + size1:
                print("ERROR: conflict between reserved memory nodes reg values: ", [ hex(i) for i in [ base1, size1, base2, size2 ] ])
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
    if platform not in [ SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL, SOC_TYPE.VERSAL_NET, SOC_TYPE.VERSAL2 ]:
        print("ERROR: unsupported platform: ", platform)
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
        Derives compatibility strings/modes from the platform, verifies existing
        cluster nodes for mode consistency, merges ranges when in split mode, or
        constructs a new node populated with all required properties.
    """
    driver_compat_str  = {
      SOC_TYPE.ZYNQMP : "xlnx,zynqmp-r5fss",
      SOC_TYPE.VERSAL : "xlnx,versal-r5fss",
      SOC_TYPE.VERSAL_NET : "xlnx,versal-net-r52fss",
      SOC_TYPE.VERSAL2 : "xlnx,versal-net-r52fss",
    }

    cluster_modes = {
        CPU_CONFIG.RPU_SPLIT: 0,
        CPU_CONFIG.RPU_LOCKSTEP: 1,
    }

    cluster_node_props = {
      "compatible" : driver_compat_str[platform],
      "#address-cells": 0x2,
      "#size-cells": 0x2,
      "xlnx,cluster-mode": cluster_modes[cpu_config.value],
      "ranges": cluster_ranges_val,
    }

    # R5 cores also need tcm mode
    if platform in [ SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL ]:
        cluster_node_props["xlnx,tcm-mode"] = cluster_modes[cpu_config.value]

    try:
        cluster_node = tree[cluster_node_path]

        # here validate if cpu config does not match
        if cluster_modes[cpu_config.value] != cluster_node.propval("xlnx,cluster-mode"):
            print("ERROR: ", "split" if cpu_config == CPU_CONFIG.RPU_SPLIT else "lockstep", "for cpu value mismatches the cluster.")
            return False

        # only in split case, let range value be extended by both cores
        if cpu_config == CPU_CONFIG.RPU_SPLIT:
            cluster_node_props["ranges"].extend(cluster_ranges_val)

    except KeyError:
        cluster_node = LopperNode(-1, cluster_node_path)
        for key in cluster_node_props.keys():
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
    print(" --> xlnx_remoteproc_v2_add_core")
    compatible_strs = { SOC_TYPE.VERSAL2:  "xlnx,versal2-r52f", SOC_TYPE.VERSAL_NET:  "xlnx,versal-net-r52f", SOC_TYPE.VERSAL: "xlnx,versal-r5f", SOC_TYPE.ZYNQMP: "xlnx,zynqmp-r5f" }
    core_names = { SOC_TYPE.VERSAL_NET: "r52f", SOC_TYPE.VERSAL: "r5f", SOC_TYPE.ZYNQMP: "r5f" }
    core_names[SOC_TYPE.VERSAL2] = core_names[SOC_TYPE.VERSAL_NET]

    core_node = LopperNode(-1, "{}/{}@{}".format( cluster_node_path, core_names[platform], int(openamp_channel_info["rpu_core"])))

    core_node_props = {
      "compatible" : compatible_strs[platform],
      "power-domains": power_domains,
      "reg": core_reg_val,
      "reg-names": core_reg_names,
      "memory-region": [ n.phandle for n in openamp_channel_info["new_ddr_nodes"] ]
    }

    if openamp_channel_info["new_ddr_nodes"] == []:
        core_node_props.pop("memory-region")

    for key in core_node_props.keys():
        core_node + LopperProp(name=key, value = core_node_props[key])

    tree.add(core_node)

    return core_node


def xlnx_remoteproc_v2_cluster_base_str(platform, rpu_core):
    """Return the remoteproc cluster base address string for an RPU core.

    Args:
        platform (SOC_TYPE): Detected platform.
        rpu_core (RPU_CORE): Enum indicating the RPU core index.

    Returns:
        str: Hexadecimal string representing the base address for the cluster.

    Algorithm:
        Performs a table lookup keyed by platform and core index to derive the base
        address required for the cluster node path.
    """
    base_addresses = {
        SOC_TYPE.VERSAL_NET: {
            RPU_CORE.RPU_0: "eba00000",
            RPU_CORE.RPU_1: "eba00000",
            RPU_CORE.RPU_2: "eba40000",
            RPU_CORE.RPU_3: "eba40000",
        },
        SOC_TYPE.VERSAL2: {
            RPU_CORE.RPU_0: "eba00000",
            RPU_CORE.RPU_1: "eba00000",
            RPU_CORE.RPU_2: "ebb00000",
            RPU_CORE.RPU_3: "ebb00000",
            RPU_CORE.RPU_4: "ebc00000",
            RPU_CORE.RPU_5: "ebc00000",
            RPU_CORE.RPU_6: "ebac0000",
            RPU_CORE.RPU_7: "ebac0000",
            RPU_CORE.RPU_8: "ebbc0000",
            RPU_CORE.RPU_9: "ebbc0000",
        },
        SOC_TYPE.ZYNQMP: {
            RPU_CORE.RPU_0: "ffe00000",
            RPU_CORE.RPU_1: "ffe00000",
        },
        SOC_TYPE.VERSAL: {
            RPU_CORE.RPU_0: "ffe00000",
            RPU_CORE.RPU_1: "ffe00000",
        },
    }

    return base_addresses[platform][rpu_core]

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
        Validates platform support, merges power-domain data from carveouts, derives
        ranges for TCM and DDR nodes, ensures the cluster node exists with correct
        configuration, tracks newly added DDR regions, and finally inserts the core
        node using ``xlnx_remoteproc_v2_add_core``.
    """
    print(" -> xlnx_remoteproc_v2_construct_cluster")
 
    cpu_config = openamp_channel_info["cpu_config"]

    rpu_core = determinte_rpu_core(tree, cpu_config, openamp_channel_info["remote_node"] )
    platform = get_platform(tree, verbose)
    cluster_ranges_val = []
    core_reg_names = []
    power_domains = []
    core_reg_val = []

    global memory_nodes

    if not platform_validate(platform):
        return False

    power_domains = openamp_channel_info["rpu_core_pd_prop"].value

    # loop through TCM nodes
    for n in [ n for n in channel_elfload_nodes if n.propval("xlnx,ip-name") != [''] ]:
        pd = n.propval("power-domains")
        power_domains.extend(pd)
        core_reg_val.extend(memory_nodes[pd[1]]["rpu_view"])
        cluster_ranges_val.extend(memory_nodes[pd[1]]["system_view"])
        core_reg_names.append(n.name)

    # construct remoteproc cluster node
    cluster_node_path = "/remoteproc@" + xlnx_remoteproc_v2_cluster_base_str(platform, rpu_core)
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
    print(" -> xlnx_remoteproc_rpu_parse", node)

    remote_node = openamp_channel_info["remote_node"] 
    cpu_config = determine_cpus_config(remote_node)
    if cpu_config not in [ CPU_CONFIG.RPU_LOCKSTEP, CPU_CONFIG.RPU_SPLIT]:
        print("ERROR: cpu_config: ", cpu_config, " is not in ", [ CPU_CONFIG.RPU_LOCKSTEP, CPU_CONFIG.RPU_SPLIT])
        return False

    rpu_core = determinte_rpu_core(tree, cpu_config, remote_node )
    if rpu_core not in RPU_CORE:
        print("ERROR: Invalid rpu core: ", rpu_core)
        return False

    if remote_node.propval("rpu_pd_val") == ['']:
        print("ERROR: no RPU Power domain value found")
        return False

    openamp_channel_info["rpu_core_pd_prop"] = remote_node.props("rpu_pd_val")[0]
    openamp_channel_info["cpu_config"] = cpu_config
    openamp_channel_info["rpu_core"] = str(int(rpu_core))

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

    inputs = root_node.propval("compatible") + root_node.propval("model")

    zynqmp = [ 'Xilinx ZynqMP',  "xlnx,zynqmp" ]
    versal = [ 'xlnx,versal', 'Xilinx Versal']
    versalnet = [ 'versal-net', 'Versal NET', "xlnx,versal-net", "Xilinx Versal NET" ]
    versal2 = [ 'xlnx,versal2', 'amd,versal2', 'amd versal vek385 reva' ]

    rpu_socs = [ versal2, zynqmp, versal, versalnet ]
    rpu_socs_enums = [ SOC_TYPE.VERSAL2, SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL, SOC_TYPE.VERSAL_NET ]

    if verbose > 0 and not banner_printed:
        print("[INFO]: ------> OPENAMP: XLNX: \t platform info: ", inputs)
        banner_printed = True

    for index, soc in enumerate(rpu_socs):
        for soc_str in soc:
            for i in inputs:
                if i == soc_str:
                    return rpu_socs_enums[index]

    if platform == None:
        print("Unable to find data for platform: ", root_model, root_compat)

    return platform

def openamp_nontree_outputs_handler(sdt, output_file_name, openamp_args, verbose = 0 ):
    """Derive the platform enum from the root node's model/compatible strings.
       This handler is called where outputs can be derived from the existing tree.
       typically just YAML -> DTS translation
       currently handles:
            1.  BM / freertos RPU openamp header
            2. zephyr / linux libmetal ipc .cmake output file
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

    platform = get_platform(sdt.tree, verbose)
    if platform == None:
        return False

    # get_cpu_node expects dictionary where first arg first element is machine
    machine = openamp_args['machine']
    match_cpunode = get_cpu_node(sdt, {'args':[machine]})
    if not match_cpunode:
        print("openamp_nontree_outputs_handler: unable to find machine: ", machine)
        return False

    domains = sdt.tree['/domains']
    relation_node = None
    relation_parent_search = True if openamp_args['relation_parent'] != None else False
    compatible_string_search = True if openamp_args['compatible_string'] != None else False
    for n in domains.subnodes():
        if n.parent == None or n.parent.parent == None:
            continue

        if n.parent.parent.propval("cpus") == ['']:
            continue

        # ensure target domain matches
        if match_cpunode.parent != sdt.tree.pnode(n.parent.parent.propval("cpus")[0]):
            continue

        # search based on compatible string of relation
        if compatible_string_search and n.propval("compatible") != [openamp_args['compatible_string']]:
            continue

        # filter based on name
        if relation_parent_search and openamp_args['relation_parent'] != n.name:
            continue

        relation_node = n
        break

    os = openamp_args["dt_type"]
    carveouts = None
    ipi_node = None
    relation_node_search = True if openamp_args['relation'] != None else False
    for node in relation_node.subnodes(children_only=True):
        if relation_node_search and openamp_args['relation'] != node.name:
            continue

        pname = "remote" if os == "linux_dt" else "host"
        # check for remote property
        if node.props(pname) == []:
            print("ERROR: ", node, "is missing ", pname, " property")
            return False

        # first find host to remote IPI
        mbox_pval = node.propval("mbox")
        if mbox_pval == ['']:
            print("ERROR: ", node, " is missing mbox property")
            return False

        ipi_node = sdt.tree.pnode(mbox_pval[0])
        if ipi_node == None:
            print("ERROR: Unable to find ipi")
            return False

        carveout_prop = node.propval("carveouts")
        if carveout_prop == ['']:
            print("ERROR: ", node, " is missing carveouts property")
            return False

        carveouts = [ sdt.tree.pnode(phandle) for phandle in carveout_prop ]

    if not openamp_args['ipi_mapping']:
        return xlnx_openamp_gen_outputs_only(sdt.tree, machine, output_file_name, carveouts, ipi_node, verbose)

    if [openamp_args['compatible_string']] == relation_node.propval("compatible"):
        return xlnx_openamp_gen_outputs_ipi_mapping(sdt.tree, output_file_name, ipi_node, os, verbose)

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
    print(" -> xlnx_remoteproc_parse", remoteproc_relation_node)

    # Xilinx OpenAMP subroutine to collect Remoteproc information from relation node in tree
    if get_platform(tree, verbose) == None:
        print("Unsupported platform")
        return False

    channel_to_core_dict = {}

    for node in remoteproc_relation_node.subnodes(children_only=True):
        # check for remote property
        if node.propval("remote") == ['']:
            print("ERROR: ", node, "is missing remote property")
            return False

        remote_node = tree.pnode(node.propval("remote")[0])
        openamp_channel_info = { "remote_node": remote_node }

        # check for elfload prop
        if node.props("elfload") == []:
            print("ERROR: ", node, " is missing elfload property")
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

    parser.add_argument("--ipi_mapping", action='store_true', help="If present - then attempt to decipher relevant IPI for the specified OpenAMP or Libmetal relation. This will also require --compatible-string and --processor. Optionally --relation-parent and --relation are used to specify non-default (e.g. first found) relation.")
    parser.add_argument("--compatible-string", type=str, help="compatible string for relation. expecting either \"libmetal,ipc-v1\" or \"openamp,rpmsg-v1\"")
    parser.add_argument("--os", type=str, help="OS arg")
    parser.add_argument("--relation-parent", type=str, help="parent of relation")
    parser.add_argument("--relation", type=str, help="target relation")

    config = {}
    if len(arg_inputs) == 2 and arg_inputs[1] in ["linux_dt", "zephyr_dt"]:
        config["dt_type"] = "zephyr_dt" if "zephyr_dt" in arg_inputs else "linux_dt"
        config["machine"] = arg_inputs[0]
        for i in ["processor", "os", "ipi_mapping", "openamp_remote", "openamp_output_filename"]:
            config[i] = None
    else:
        args = parser.parse_args(arg_inputs)
        config = vars(args)
        config["dt_type"] = config["os"]
        config["machine"] = False

        if config["processor"] and not config["machine"]:
            config["machine"] = config["processor"]
        elif config["openamp_remote"] and config["openamp_header_only"] and not config["machine"]:
            config["machine"] = config["openamp_remote"]
        elif not config["machine"]:
            print("INFO: OpenAMP plugin: missing processor or openamp_remote being passed in. exiting now")
            return False

        # handling for ipi mapping workflow
        if config["ipi_mapping"] and not config["compatible_string"]:
            print("requires compatible_string to be set for ipi_mapping case")
            return False

        # provide default output file for IPI mapping use case if none provided
        if config["ipi_mapping"] and not config["openamp_output_filename"]:
            print("INFO: OpenAMP plugin: ipi_mapping route is taken. output file is not specified so default is used (ipi_mapping.cmake)")
            config["openamp_output_filename"] = "ipi_mapping.cmake"

    return config

def xlnx_openamp_parse(sdt, options, verbose = 0 ):
    """Entry point for the OpenAMP assist to process remoteproc/RPMsg data.

    Args:
        sdt (LopperSDT): Structured device tree wrapper.
        options (dict): Plugin options containing ``args``.
        verbose (int): Verbosity level for diagnostic output.

    Returns:
        bool: True when processing succeeds or no domains exist, False on errors.

    Algorithm:
        Parses assist arguments, checks for OpenAMP-compatible domains, delegates
        relation handling when appropriate.
    """
    # Xilinx OpenAMP subroutine to parse OpenAMP Channel
    # information and generate Device Tree information.
    print(" -> xlnx_openamp_parse")
    openamp_args = parse_openamp_args(options['args'])
    if not openamp_args:
        return False

    tree = sdt.tree
    ret = -1
    machine = openamp_args["machine"]

    if not xlnx_openamp_find_compat_domains(tree):
        if verbose > 1:
            print("OPENAMP: XLNX: WARNING: no openamp domains found")
        return True

    if openamp_args["openamp_output_filename"]:
        return openamp_nontree_outputs_handler(sdt, openamp_args["openamp_output_filename"], openamp_args, 1 )

    if openamp_args["dt_type"] in ["zephyr_dt", "linux_dt"] or openamp_args["openamp_output_filename"]:
        # if find_only is False, then processing will also occur.
        if not xlnx_handle_relations(sdt, machine, False, openamp_args["dt_type"]):
            return False

        labels_to_keep = [ 'ttc0', 'ttc1' ]
        if openamp_args["dt_type"] == "linux_dt" and get_platform(sdt.tree, verbose) != SOC_TYPE.VERSAL2:
            for node in tree["/"].subnodes(children_only=True, name="timer@*"):
                # for all boards that are NOT VEK385 - keep ttc0 and ttc1 in linux case.
                if "cdns,ttc" in node.propval('compatible') and node.label not in labels_to_keep:
                    sdt.tree.delete(node)
    else:
        print("OPENAMP: XLNX: WARNING: not doing any processing given inputs", options)

    return True

