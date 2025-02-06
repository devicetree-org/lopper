#/*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

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
from enum import Enum
from enum import IntEnum
from string import Template

sys.path.append(os.path.dirname(__file__))
from openamp_xlnx_common import *
from baremetalconfig_xlnx import get_cpu_node
from string import ascii_lowercase as alc

RPU_PATH = "/rpu@ff9a0000"
REMOTEPROC_D_TO_D = "openamp,remoteproc-v1"
REMOTEPROC_D_TO_D_v2 = "openamp,remoteproc-v2"
RPMSG_D_TO_D = "openamp,rpmsg-v1"
output_file = "amd_platform_info.h"
info_rproc_driver_version = False

class CPU_CONFIG(IntEnum):
    RPU_SPLIT = 0
    RPU_LOCKSTEP = 1

class RPU_CORE(IntEnum):
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
    RPU_LOCKSTEP = 0
    RPU_0 = 1
    RPU_1 = 2

def is_compat( node, compat_string_to_test ):
    if re.search( "openamp,xlnx-rpu", compat_string_to_test):
        return xlnx_openamp_rpu
    return ""

def xlnx_rpmsg_native_update_carveouts(tree, elfload_node,
                                       native_shm_mem_area_start, native_shm_mem_area_size,
                                       native_amba_shm_node):
    return True


# Given a domain node, get its corresponding node list from the carveouts
# property. If no prop return empty list
def get_rpmsg_carveout_nodes(tree, node):
    carveouts_node = tree[node.abs_path + "/domain-to-domain/rpmsg-relation"]
    if isinstance(carveouts_node, LopperNode):
        node = carveouts_node
    carveout_prop = node.props("carveouts")
    if carveout_prop == []:
        print("ERROR: ", node, " is missing carveouts property")
        return []
    carveouts_nodes = []
    for phandle in carveout_prop[0].value:
        tmp_node = tree.pnode( phandle )
        carveouts_nodes.append ( tmp_node )

    return carveouts_nodes


reserved_mem_nodes = []
res_mem_bases = []
res_mem_sizes = []
def reserved_mem_node_check(tree, node, verbose = 0 ):
    # check if given node conflicts with reserved memory nodes

    res_mem_node = tree["/reserved-memory"]
    nodes_of_interest = res_mem_node.subnodes()
    if reserved_mem_nodes != []:
        nodes_of_interest = [node]
        # the reason we set the list this way is to support the case
        # of first run, where there are no YAML-added nodes. In that case
        # validate pre-existing resreved-mem nodes
        #
        # Otherwise ,validate the newly created node against existing res-mem
        # nodes to ensure there is no overlap

    for rm_subnode in nodes_of_interest:
        # in case of init, skip reserved-mem top level node
        if rm_subnode == res_mem_node:
            continue

        if rm_subnode not in reserved_mem_nodes:
            node_reg = rm_subnode.props("reg")
            if node_reg == []:
                print("ERROR: malformed reserved-memory node: ", rm_subnode.abs_path)
                return False
            node_reg = node_reg[0].value
            if len(node_reg) != 4:
                print("ERROR: malformed reserved-memory node: ", rm_subnode.abs_path)
                return False
            new_base = node_reg[1]
            new_sz = node_reg[3]

            overlap = False
            for base,sz,existing_node in zip(res_mem_bases, res_mem_sizes, reserved_mem_nodes):
                if new_base < base and (new_base+new_sz) > base:
                    overlap = True
                if new_base < (base+sz) and (new_base+new_sz) > (base+sz):
                    overlap = True
                if overlap:
                    print("ERROR: overlap between nodes:", existing_node, rm_subnode)
                    return False
            res_mem_bases.append(new_base)
            res_mem_sizes.append(new_sz)
            reserved_mem_nodes.append(rm_subnode)

    return True

def xlnx_rpmsg_format_res_mem_node(node, base):
    special_node_names = [ "vdev0vring0", "vdev0vring1", "vdev0buffer" ]
    for n in special_node_names:
        if n in node.name:
            node.name = n + "@" + base
            break

native_shm_node_count = 0
def xlnx_rpmsg_construct_carveouts(tree, carveouts, rpmsg_carveouts, native, channel_id,
                                   openamp_channel_info, amba_node = None,
                                   elfload_node = None, verbose = 0 ):
    global native_shm_node_count
    res_mem_node = tree["/reserved-memory"]
    native_amba_shm_node = None
    native_shm_mem_area_size = 0
    native_shm_mem_area_start = 0xFFFFFFFF
    remote_carveouts = get_rpmsg_carveout_nodes(tree, openamp_channel_info["remote_node_"+channel_id])

    vring_total_sz = 0
    for c in remote_carveouts:
        if "vring" in c.name:
            vring_total_sz += c.props("size")[0].value

    openamp_channel_info["shared_buf_offset_"+channel_id] = vring_total_sz

    # only applicable for DDR carveouts
    for carveout in remote_carveouts:
        # SRAM banks have status prop
        # SRAM banks are not in reserved memory
        # FIXME  skip SRAM banks in RPMsg support
        if carveout.props("status") != []:
            continue
        elif carveout.props("no-map") != []:
            start = carveout.props("start")[0].value
            size = carveout.props("size")[0].value
            # handle native RPMsg
            if native:
                if start < native_shm_mem_area_start:
                    native_shm_mem_area_start = start
                native_shm_mem_area_size += size
                # add more space for shared buffers
                if 'vdev0buffer' in carveout.name:
                    native_shm_mem_area_size += size
            else:

                new_node =  LopperNode(-1, "/reserved-memory/"+carveout.name)
                new_node + LopperProp(name="no-map")
                new_node + LopperProp(name="reg", value=[0, start, 0, size])
                if not reserved_mem_node_check(tree, new_node):
                    return False

                if "vdev0buffer" in carveout.name:
                    new_node + LopperProp(name="compatible", value="shared-dma-pool")

                tree.add(new_node)
                tree.resolve()
                if openamp_channel_info[REMOTEPROC_D_TO_D_v2]:
                    xlnx_rpmsg_format_res_mem_node(new_node, hex(start)[2:])

                if new_node.phandle == 0:
                    new_node.phandle = new_node.phandle_or_create()

                new_node + LopperProp(name="phandle", value = new_node.phandle)

                rpmsg_carveouts.append(new_node)
        else:
            print("ERROR: invalid remoteproc elfload carveout", carveout)
            return False

    if native:
        # start and size of reserved-mem rproc0
        start = elfload_node.props("start")[0].value
        size = elfload_node.props("size")[0].value

        # update size if applicable
        if start < native_shm_mem_area_start:
            native_shm_mem_area_start = start
        native_shm_mem_area_size += size
        # update rproc0 size
        elfload_res_mem_node = tree["/reserved-memory/" + elfload_node.name]
        elfload_res_mem_reg = elfload_res_mem_node.props("reg")[0].value
        elfload_res_mem_reg[3] = native_shm_mem_area_size
        elfload_res_mem_node.props("reg")[0].value = elfload_res_mem_reg

        native_amba_shm_node = LopperNode(-1, amba_node.abs_path + "/" + "shm@" + hex(native_shm_mem_area_start)[2:])

        native_amba_shm_node + LopperProp(name="compatible", value="shm_uio")
        tree.add(native_amba_shm_node)
        tree.resolve()
        native_shm_node_count += 1
        openamp_channel_info["native_shm_node_"+channel_id] = native_amba_shm_node
        shm_space = [0, native_shm_mem_area_start, 0, native_shm_mem_area_size]
        native_amba_shm_node + LopperProp(name="reg", value=shm_space)

    return True


def xlnxl_rpmsg_ipi_get_ipi_id(tree, ipi, role):
    ipi_node = None
    ipi_id_prop_name = "xlnx,ipi-id"
    ipi_node = tree.pnode( ipi )

    if ipi_node == None:
        print("ERROR: Unable to find ipi: ", ipi, " for role: ", role)
        return False

    ipi_id = ipi_node.props(ipi_id_prop_name)
    if ipi_id == []:
        print("ERROR: Unable to find IPI ID for ", ipi)
        return False

    return ipi_id[0]


def xlnx_rpmsg_ipi_parse_per_channel(remote_ipi, host_ipi, tree, node, openamp_channel_info,
                                     remote_node, channel_id, native, channel_index, 
                                     verbose = 0):
    ipi_id_prop_name = "xlnx,ipi-id"

    host_ipi_id = xlnxl_rpmsg_ipi_get_ipi_id(tree, host_ipi[channel_index], "host")

    if host_ipi_id == False:
        return host_ipi_id

    remote_ipi_id = xlnxl_rpmsg_ipi_get_ipi_id(tree, remote_ipi, "remote")
    if remote_ipi_id == False:
        return remote_ipi_id

    host_ipi = tree.pnode( host_ipi[channel_index])


    # find host to remote buffers
    host_to_remote_ipi_channel = None
    for subnode in host_ipi.subnodes():
        subnode_ipi_id = subnode.props(ipi_id_prop_name)
        if subnode_ipi_id != [] and remote_ipi_id.value[0] == subnode_ipi_id[0].value[0]:
            host_to_remote_ipi_channel = subnode
    if host_to_remote_ipi_channel == None:
        print("WARNING no host to remote IPI channel has been found.")
        return False

    remote_ipi = tree.pnode( remote_ipi )

    # find remote to host buffers
    remote_to_host_ipi_channel = None
    for subnode in remote_ipi.subnodes():
        subnode_ipi_id = subnode.props(ipi_id_prop_name)
        if subnode_ipi_id != [] and host_ipi_id.value[0] == subnode_ipi_id[0].value[0]:
            remote_to_host_ipi_channel = subnode
    if remote_to_host_ipi_channel == None:
        print("WARNING no remote to host IPI channel has been found.")
        return False

    # store IPI IRQ Vector IDs
    platform =  openamp_channel_info["platform"]
    host_ipi_base = host_ipi.props("reg")[0][1]
    remote_ipi_base = remote_ipi.props("reg")[0][1]
    soc_strs = {
      SOC_TYPE.ZYNQMP: "ZU+",
      SOC_TYPE.VERSAL: "Versal",
      SOC_TYPE.VERSAL_NET: "Versal NET",
      SOC_TYPE.VERSAL2: "Versal2",
    }

    irq_vect_ids = {
      SOC_TYPE.ZYNQMP: zynqmp_ipi_to_irq_vect_id,
      SOC_TYPE.VERSAL: versal_ipi_to_irq_vect_id,
      SOC_TYPE.VERSAL_NET: versal_net_ipi_to_irq_vect_id,
      SOC_TYPE.VERSAL2: versal_net_ipi_to_irq_vect_id,
    }

    irq_vect_id_map = irq_vect_ids[platform]
    soc_str = soc_strs[platform]


    if platform in [SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL, SOC_TYPE.VERSAL_NET, SOC_TYPE.VERSAL2]:
        if host_ipi_base not in irq_vect_id_map.keys():
            print("ERROR: host IPI", hex(host_ipi_base), "not in IRQ VECTOR ID Mapping for ", soc_str)
            return False
        if remote_ipi_base not in irq_vect_id_map.keys():
            print("ERROR: remote IPI", hex(remote_ipi_base), "not in IRQ VECTOR ID Mapping for ", soc_str)
            return False

        openamp_channel_info["host_ipi_base"+channel_id] = host_ipi_base
        openamp_channel_info["host_ipi_irq_vect_id"+channel_id] = irq_vect_id_map[host_ipi_base]
        openamp_channel_info["remote_ipi_base"+channel_id] = remote_ipi_base
        openamp_channel_info["remote_ipi_irq_vect_id"+channel_id] = irq_vect_id_map[remote_ipi_base]
    else:
        print("Unsupported platform")

    openamp_channel_info["host_ipi_"+channel_id] = host_ipi
    openamp_channel_info["remote_ipi_"+channel_id] = remote_ipi
    openamp_channel_info["host_to_remote_ipi_channel_" + channel_id] = host_to_remote_ipi_channel
    openamp_channel_info["remote_to_host_ipi_channel_" + channel_id] = remote_to_host_ipi_channel
    return True


def xlnx_rpmsg_ipi_parse(tree, node, openamp_channel_info,
                         remote_node, channel_id, native, channel_index, 
                         verbose = 0 ):
    amba_node = None
    ipi_id_prop_name = "xlnx,ipi-id"
    host_to_remote_ipi = None
    remote_to_host_ipi = None

    # collect host ipi
    host_ipi_prop = node.props("mbox")
    if host_ipi_prop == []:
        print("ERROR: ", node, " is missing mbox property")
        return False

    host_ipi_prop = host_ipi_prop[0].value
    remote_rpmsg_relation = None
    try:
        remote_rpmsg_relation = tree[remote_node.abs_path + "/domain-to-domain/rpmsg-relation"]
    except:
        print("ERROR: ", remote_node, " is missing rpmsg relation")
        return False

    # collect remote ipi
    remote_ipi_prop = remote_rpmsg_relation.props("mbox")
    if remote_ipi_prop == []:
        print("ERROR: ", remote_node, " is missing mbox property")
        return False

    remote_ipi_prop = remote_ipi_prop[0].value

    ret = xlnx_rpmsg_ipi_parse_per_channel(remote_ipi_prop[0], host_ipi_prop, tree, node, openamp_channel_info,
                                           remote_node, channel_id, native, channel_index, verbose)
    if ret != True:
        return False

    return True


def xlnx_rpmsg_setup_host_controller(tree, controller_parent, gic_node_phandle,
                                     host_ipi):
    controller_parent + LopperProp(name="compatible",value="xlnx,zynqmp-ipi-mailbox")
    controller_parent + LopperProp(name="interrupt-parent", value = [gic_node_phandle])
    controller_parent + LopperProp(name="#address-cells",value=1)
    controller_parent + LopperProp(name="#size-cells",value=1)
    controller_parent + LopperProp(name="ranges")

    host_interrupts_prop = host_ipi.props("interrupts")
    if host_interrupts_prop == []:
        print("ERROR: host ipi ", host_ipi, " missing interrupts property")
        return False

    host_interrupts_pval = host_interrupts_prop[0].value
    controller_parent + LopperProp(name="interrupts", value = copy.deepcopy(host_interrupts_pval))
    controller_parent + LopperProp(name="xlnx,ipi-id", value = host_ipi.props("xlnx,ipi-id")[0].value)

    tree.add(controller_parent)
    tree.resolve()

    if controller_parent.phandle == 0:
        controller_parent.phandle = controller_parent.phandle_or_create()

    controller_parent + LopperProp(name="phandle", value = controller_parent.phandle)

    return True


controller_parent_index = 1
def xlnx_rpmsg_create_host_controller(tree, host_ipi, gic_node_phandle, verbose = 0):
    global controller_parent_index
    controller_parent = None
    ctr_parent_name = "/zynqmp_ipi" + str(controller_parent_index)
    setup_host_controller = True
    try:
        controller_parent = tree[ctr_parent_name]

        ctr_parent_ipi_id = controller_parent.props("xlnx,ipi-id")
        if ctr_parent_ipi_id != [] and ctr_parent_ipi_id[0].value[0] == host_ipi.props("xlnx,ipi-id")[0].value[0]:
            setup_host_controller = False
        else:
            setup_host_controller = True
            controller_parent_index += 1
            # make new parent controller with updated name
            ctr_parent_name = "/zynqmp_ipi" + str(controller_parent_index)
            controller_parent = LopperNode(-1, ctr_parent_name)
    except:
        controller_parent = LopperNode(-1, ctr_parent_name)

    # check if host controller needs to be setup before adding props to it
    if setup_host_controller:
        ret = xlnx_rpmsg_setup_host_controller(tree, controller_parent,
                                               gic_node_phandle, host_ipi)
        if not ret:
            return False

    return controller_parent


def xlnx_rpmsg_native_update_ipis(tree, amba_node, openamp_channel_info, gic_node_phandle,
                                  amba_ipi_node_index, host_ipi, channel_id):
    amba_node = openamp_channel_info["amba_node"]

    # if host ipi already used for other channel do not re-add it.
    if host_ipi in amba_host_ipis:
        idx = 0
        for x in amba_host_ipis:
            if x == host_ipi:
                break
            idx += 1
        amba_ipi_node = amba_ipis[idx]
    else:
        reg_val = copy.deepcopy(host_ipi.props("reg")[0].value)
        reg_val[3] = 0x1000

        amba_ipi_node = LopperNode(-1, amba_node.abs_path + "/openamp_ipi" + str(amba_ipi_node_index) + "@" + hex(reg_val[1])[2:])
        amba_ipi_node + LopperProp(name="compatible",value="ipi_uio")
        amba_ipi_node + LopperProp(name="interrupts",value=copy.deepcopy(host_ipi.props("interrupts")[0].value))
        amba_ipi_node + LopperProp(name="interrupt-parent",value=[gic_node_phandle])
        amba_ipi_node + LopperProp(name="reg",value=reg_val)
        tree.add(amba_ipi_node)
        tree.resolve()

        amba_host_ipis.append(host_ipi)
        amba_ipis.append(amba_ipi_node)

        amba_ipi_node_index += 1

    openamp_channel_info["rpmsg_native_ipi_"+channel_id] = amba_ipi_node

    # check if there is a kernelspace IPI that has same interrupts
    # if so then error out
    for n in tree["/"].subnodes():
        compat_str = n.props("compatible")
        if compat_str != [] and compat_str[0].value == "xlnx,zynqmp-ipi-mailbox" and \
            n.props("interrupts")[0].value[1] ==  host_ipi.props("interrupts")[0].value[1]:
            # check if subnode exists with the correct structure
            # before erroring out. There may be IP IPIs that this
            # catches
            for sub_n in n.subnodes():
                if sub_n.props("reg-names") != []:
                    print("ERROR:  conflicting userspace and kernelspace for host ipi: ", host_ipi)
                    return False

    return True


def xlnx_rpmsg_kernel_create_mboxes_versal(tree, host_ipi, remote_ipi, gic_node_phandle,
                                           openamp_channel_info, channel_id,
                                           core_node, rpu_core):

    platform = openamp_channel_info["platform"]

    nobuf_ipis = {
        SOC_TYPE.VERSAL_NET: [ 0xeb3b0000, 0xeb3b1000, 0xeb3b2000, 0xeb3b3000, 0xeb3b4000, 0xeb3b5000 ],
        SOC_TYPE.VERSAL: [ 0xFF390000 ],
    }

    # versal2 same as vnet for IPI
    nobuf_ipis[SOC_TYPE.VERSAL2] = nobuf_ipis[SOC_TYPE.VERSAL_NET]

    host_reg = host_ipi.propval("reg")[1]
    host_ipi_name = "/openamp_" + host_ipi.name
    host_reg_val = copy.deepcopy(host_ipi.propval("reg"))
    if host_reg not in nobuf_ipis[platform]:
        host_buf_base = host_ipi.props("xlnx,buffer-base")[0].value[0]
        host_reg_val.extend([0, host_buf_base, 0, 0x1ff])

    host_props = {
        "#address-cells": 2, "#size-cells": 2, "compatible": "xlnx,versal-ipi-mailbox",
        "reg": host_reg_val,
        "xlnx,ipi-id": host_ipi.props("xlnx,ipi-id")[0].value,
        "interrupts": copy.deepcopy(host_ipi.propval("interrupts")),
        "reg-names": ["ctrl"] if host_reg in nobuf_ipis[platform] else [ "ctrl", "msg"]
    }

    remote_ipi_name = host_ipi_name + "/" + remote_ipi.name
    remote_reg = remote_ipi.propval("reg")[1]
    remote_reg_val = copy.deepcopy(remote_ipi.propval("reg"))
    if remote_reg not in nobuf_ipis[platform]:
        remote_buf_base = remote_ipi.props("xlnx,buffer-base")[0].value[0]
        remote_reg_val.extend([0, remote_buf_base, 0, 0x1ff])

    remote_props = {
        "compatible": "xlnx,versal-ipi-dest-mailbox", "#mbox-cells": 1,
        "reg-names": ["ctrl"] if remote_reg in nobuf_ipis[platform] else [ "ctrl", "msg"],
        "reg": remote_reg_val,
        "xlnx,ipi-id": remote_ipi.props("xlnx,ipi-id")[0].value
    }
    try:
        host_mbox_node = tree[host_ipi_name]
    except:
        host_mbox_node = LopperNode(-1, host_ipi_name)
        for key in host_props.keys():
            host_mbox_node + LopperProp(name=key, value=host_props[key])
        host_mbox_node + LopperProp(name="ranges")
        host_mbox_node + LopperProp(name="interrupt-parent",value=[gic_node_phandle])

        tree.add(host_mbox_node)
        host_mbox_node.phandle = host_mbox_node.phandle_or_create()

    remote_mbox_node = LopperNode(-1, remote_ipi_name)
    tree.add(remote_mbox_node)
    remote_mbox_node.phandle = remote_mbox_node.phandle_or_create()
    for key in remote_props.keys():
        remote_mbox_node + LopperProp(name=key, value=remote_props[key])
    remote_mbox_node.phandle = remote_mbox_node.phandle_or_create()

    core_node + LopperProp(name="mboxes", value=[remote_mbox_node.phandle, 0, remote_mbox_node.phandle, 1])
    core_node + LopperProp(name="mbox-names", value=["tx", "rx"])

    return True


def xlnx_rpmsg_kernel_update_mboxes(tree, host_ipi, remote_ipi, gic_node_phandle,
                                  openamp_channel_info, channel_id,
                                  core_node, controller_parent, rpu_core):
    platform = openamp_channel_info["platform"]
    remote_controller_node = LopperNode(-1, controller_parent.abs_path + "/ipi_mailbox_rpu" + rpu_core)
    tree.add(remote_controller_node)
    tree.resolve()

    if remote_controller_node.phandle == 0:
        remote_controller_node.phandle = remote_controller_node.phandle_or_create()


    remote_controller_node + LopperProp(name="phandle", value = remote_controller_node.phandle)
    remote_controller_node + LopperProp(name="#mbox-cells", value = 1)
    remote_controller_node + LopperProp(name="compatible", value = "xlnx,zynqmp-ipi-dest-mailbox")
    remote_controller_node + LopperProp(name="xlnx,ipi-id", value = remote_ipi.props("xlnx,ipi-id")[0].value)
    reg_names_val = [ "local_request_region", "local_response_region",
                      "remote_request_region","remote_response_region"]
    remote_controller_node + LopperProp(name="reg-names", value = reg_names_val)

    cpu_config = str(openamp_channel_info["cpu_config"+channel_id].value)
    host_to_remote_ipi_channel = openamp_channel_info["host_to_remote_ipi_channel_"+channel_id]
    remote_to_host_ipi_channel= openamp_channel_info["remote_to_host_ipi_channel_"+channel_id]
    response_buf_str = "xlnx,ipi-rsp-msg-buf"
    request_buf_str = "xlnx,ipi-req-msg-buf"

    host_remote_response = host_to_remote_ipi_channel.props(response_buf_str)
    if host_remote_response == []:
        print("ERROR: host_remote_response ", host_to_remote_ipi_channel, "is missing property name: ", response_buf_str)
        return False
    host_remote_request = host_to_remote_ipi_channel.props(request_buf_str)
    if host_remote_request == []:
        print("ERROR: host_remote_request ", host_to_remote_ipi_channel, " is missing property name: ", request_buf_str)
        return False
    remote_host_response = remote_to_host_ipi_channel.props(response_buf_str)
    if remote_host_response == []:
        print("ERROR: remote_host_response ", remote_to_host_ipi_channel, " is missing property name: ", response_buf_str)
        return False
    remote_host_request = remote_to_host_ipi_channel.props(request_buf_str)
    if remote_host_request == []:
        print("ERROR: remote_host_request ", remote_to_host_ipi_channel, " is missing property name: ", request_buf_str)
        return False

    host_remote_response = host_remote_response[0].value[0]
    host_remote_request = host_remote_request[0].value[0]
    remote_host_response = remote_host_response[0].value[0]
    remote_host_request = remote_host_request[0].value[0]

    remote_controller_node + LopperProp(name="reg", value = [
                                                         host_remote_request,  0x20,
                                                         host_remote_response, 0x20,
                                                         remote_host_request,  0x20,
                                                         remote_host_response, 0x20])

    core_node + LopperProp(name="mboxes", value = [remote_controller_node.phandle, 0, remote_controller_node.phandle, 1])
    core_node + LopperProp(name="mbox-names", value = ["tx", "rx"])

    return True

def xlnx_rpmsg_kernel_update_ipis(tree, host_ipi, remote_ipi, gic_node_phandle,
                                  core_node, openamp_channel_info, channel_id):

    rpu_core = openamp_channel_info["rpu_core" + channel_id]
    if not isinstance(rpu_core, str):
        rpu_core = str(rpu_core.value)

    if openamp_channel_info["platform"] != SOC_TYPE.ZYNQMP:
        return xlnx_rpmsg_kernel_create_mboxes_versal(tree, host_ipi, remote_ipi, gic_node_phandle,
                                                      openamp_channel_info, channel_id,
                                                      core_node, rpu_core)

    controller_parent = xlnx_rpmsg_create_host_controller(tree, host_ipi, gic_node_phandle)

    # check if there is a userspace IPI that has same interrupts
    # if so then error out
    for n in tree["/"].subnodes():
        compat_str = n.props("compatible")
        if compat_str != [] and compat_str[0].value == "ipi_uio" and \
            n.props("interrupts")[0].value[1] ==  host_ipi.props("interrupts")[0].value[1]:
            print("ERROR:  conflicting userspace and kernelspace for host ipi: ", host_ipi)
            return False

    if controller_parent == False:
        return False
    rpu_core = openamp_channel_info["rpu_core" + channel_id]
    if not isinstance(rpu_core, str):
        rpu_core = str(rpu_core.value)

    return xlnx_rpmsg_kernel_update_mboxes(tree, host_ipi, remote_ipi, gic_node_phandle,
                                           openamp_channel_info, channel_id,
                                           core_node, controller_parent, rpu_core)


amba_ipi_node_index = 0
amba_host_ipis = []
amba_ipis = []
def xlnx_rpmsg_update_ipis(tree, channel_id, openamp_channel_info, verbose = 0 ):
    global amba_ipi_node_index
    native = openamp_channel_info["rpmsg_native_"+ channel_id]
    platform = openamp_channel_info["platform"]
    core_node = openamp_channel_info["core_node"+channel_id]
    host_ipi = openamp_channel_info["host_ipi_"+ channel_id]
    remote_ipi = openamp_channel_info["remote_ipi_"+ channel_id]
    controller_parent = None
    amba_node = None
    gic_node_phandle = None

    if platform == SOC_TYPE.VERSAL:
        gic_node_phandle = tree["/apu-bus/interrupt-controller@f9000000"].phandle
    elif platform == SOC_TYPE.VERSAL_NET:
        gic_node_phandle = tree["/apu-bus/interrupt-controller@e2000000"].phandle
    elif platform == SOC_TYPE.VERSAL2:
        gic_node_phandle = tree["/apu-bus/interrupt-controller@e2000000"].phandle
    elif platform == SOC_TYPE.ZYNQMP:
        gic_node_phandle = tree["/apu-bus/interrupt-controller@f9010000"].phandle
    elif platform == SOC_TYPE.ZYNQ:
        gic_node_phandle = tree["/axi/interrupt-controller@f8f01000"].phandle
        core_node + LopperProp(name="interrupt-parent",value=[gic_node_phandle])
        return True
    else:
        print("invalid platform")
        return False

    if native:
        return xlnx_rpmsg_native_update_ipis(tree, amba_node, openamp_channel_info, gic_node_phandle,
                                             amba_ipi_node_index, host_ipi, channel_id)
    else:
        return xlnx_rpmsg_kernel_update_ipis(tree, host_ipi, remote_ipi, gic_node_phandle,
                                             core_node, openamp_channel_info, channel_id)


def xlnx_rpmsg_update_tree(tree, node, channel_id, openamp_channel_info, verbose = 0 ):
    platform = openamp_channel_info["platform"]
    cpu_config = None
    host_ipi = None
    remote_ipi = None
    rpu_core = None
    carveouts_nodes = openamp_channel_info["carveouts_"+ channel_id]
    amba_node = None
    native = False
    rpmsg_carveouts = []
    core_node = None

    if platform in [ SOC_TYPE.VERSAL, SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL_NET, SOC_TYPE.VERSAL2 ]:
        native = openamp_channel_info["rpmsg_native_"+ channel_id]
        cpu_config =  openamp_channel_info["cpu_config"+channel_id]
        rpu_core = openamp_channel_info["rpu_core" + channel_id]

    elfload_node = None
    if native:
        amba_node = openamp_channel_info["amba_node"]

    # if Amba node exists, then this is for RPMsg native.
    # in this case find elfload node in case of native RPMsg as it may be contiguous
    # for AMBA Shm Node
    if native:
        for node in openamp_channel_info["elfload"+ channel_id]:
            if node.props("start") != []:
                elfload_node = node

    ret = xlnx_rpmsg_construct_carveouts(tree, carveouts_nodes, rpmsg_carveouts, native, channel_id, openamp_channel_info,
                                         amba_node=amba_node, elfload_node=elfload_node, verbose=verbose)
    if ret == False:
        return ret

    if platform in [ SOC_TYPE.VERSAL, SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL_NET, SOC_TYPE.VERSAL2]:
        core_node = openamp_channel_info["core_node"+channel_id]
    else:
        core_node = tree["/remoteproc@0"]

    mem_region_prop = core_node.props("memory-region")[0]

    # add rpmsg carveouts to cluster core node if using rpmsg kernel driver

    vdev0buf = None
    for index, rc in enumerate(rpmsg_carveouts):
        if "vdev0buffer" in rc.name:
            vdev0buf = index
            break
    # vdev0buf should be first after the ELF load prop already in memory-region
    vdev0buf = rpmsg_carveouts.pop(vdev0buf)
    rpmsg_carveouts.insert(0, vdev0buf)

    new_mem_region_prop_val = mem_region_prop.value
    if not native:
        for rc in rpmsg_carveouts:
            new_mem_region_prop_val.append(rc.phandle)
        # update property with new values
        mem_region_prop.value = new_mem_region_prop_val

    ret = xlnx_rpmsg_update_ipis(tree, channel_id, openamp_channel_info, verbose)
    if ret != True:
        return False

    return True

# Inputs: openamp-processed SDT, target processor
# If there exists a DDR carveout for ELF-Loading, return the start and size
# of the carveout
def xlnx_openamp_get_ddr_elf_load(machine, sdt, options):
    match_cpunode = get_cpu_node(sdt, options)
    tree = sdt.tree
    global machine_to_dt_mappings
    global machine_to_dt_mappings_v2

    # validate machine
    mach_to_dt_map = machine_to_dt_mappings
    for v in machine_to_dt_mappings_v2.values():
        try:
            lookup = tree[v]
            mach_to_dt_map = machine_to_dt_mappings_v2
            break
        except KeyError:
            continue

    if machine not in mach_to_dt_map.keys():
        print("OPENAMP: XLNX: ERROR: unsupported machine to remoteproc node mapping: ", machine)
        return None

    try:
        target_node = tree[mach_to_dt_map[machine]]
    except KeyError:
        print("OPENAMP: XLNX: ERROR: could not find mapping:", machine, mach_to_dt_map[machine])
        return None

    mem_reg_val = target_node.propval('memory-region')

    if mem_reg_val == []:
        print("OPENAMP: XLNX: ERROR: could not find remoteproc node does not have DDR ELF Load property 'memory-region'")
        return None

    # ELFLOAD carveout for the DT node is first phandle
    elf_load_carveout = tree.pnode(mem_reg_val[0])
    elf_load_carveout_reg = elf_load_carveout.propval('reg')

    return [elf_load_carveout_reg[1], elf_load_carveout_reg[3]]

def xlnx_openamp_gen_outputs_only(sdt, machine, output_file, verbose = 0 ):
    global machine_to_dt_mappings_v2
    tree = sdt.tree
    platform = get_platform(tree, verbose)

    if machine not in machine_to_dt_mappings_v2.keys():
        print("OPENAMP: XLNX: ERROR: unsupported machine to remoteproc node mapping: ", machine)
        return False

    try:
        target_node = tree[machine_to_dt_mappings_v2[machine]]
    except KeyError:
        print("OPENAMP: XLNX: ERROR: could not find mapping:", machine, machine_to_dt_mappings_v2[machine])
        return False

    mem_reg_val = target_node.propval("memory-region")
    if len(mem_reg_val) != 4:
        print("OPENAMP: XLNX: ERROR: malformed memory region property for node: ", target_node)
        return False

    try:
        elfload_base = tree.pnode(mem_reg_val[0]).propval("reg")[1]

        mbox_node_pval = target_node.propval('mboxes')
        if mbox_node_pval == []:
            print("OPENAMP: XLNX: ERROR: xlnx_openamp_gen_outputs_only: mbox_node == []")
            return False

        mbox_node = tree.pnode(mbox_node_pval[0])

        for n in tree['/axi'].subnodes():
            if n.propval("compatible") == [ "xlnx,zynqmp-ipi-mailbox" ] and n.propval('xlnx,ipi-id') == mbox_node.propval('xlnx,ipi-id'):
                remote_interrupt = n.propval('interrupts')[1]
                poll_base_addr = hex(n.propval("reg")[1])
                for mbox_subnode in n.subnodes():
                    if mbox_subnode.propval('xlnx,ipi-id') != [] and mbox_subnode.propval('xlnx,ipi-id')[0] == mbox_node.parent.propval('xlnx,ipi-id')[0]:
                        ipi_chn_bitmask = mbox_subnode.propval('xlnx,ipi-bitmask')[0]
                        break
                break

        inputs = {
        "POLL_BASE_ADDR": poll_base_addr,
        "SHM_DEV_NAME": "\"" + hex(elfload_base)[2:] + '.shm\"',
        "DEV_BUS_NAME": "\"generic\"",
        "IPI_DEV_NAME":  "\"" + poll_base_addr[2:] + '.ipi\"',
        "IPI_IRQ_VECT_ID": hex(remote_interrupt),
        "IPI_IRQ_VECT_ID_FREERTOS": hex(remote_interrupt - 32),
        "IPI_CHN_BITMASK": hex(ipi_chn_bitmask),
        "RING_TX": hex(tree.pnode(mem_reg_val[2]).propval("reg")[1]),
        "RING_RX": hex(tree.pnode(mem_reg_val[3]).propval("reg")[1]),
        "SHARED_MEM_PA": hex(tree.pnode(mem_reg_val[2]).propval("reg")[1]),
        "SHARED_MEM_SIZE":"0x100000UL",
        "SHARED_BUF_OFFSET": hex(tree.pnode(mem_reg_val[2]).propval("reg")[3] + tree.pnode(mem_reg_val[3]).propval("reg")[3]),
        "SHARED_BUF_PA": hex(tree.pnode(mem_reg_val[1]).propval("reg")[1]),
        "SHARED_BUF_SIZE": hex(tree.pnode(mem_reg_val[1]).propval("reg")[3]),
        "EXTRAS":"",
        }
    except:
        print("OPENAMP: XLNX: ERROR: xlnx_openamp_gen_outputs_only: Error in generating template for RPU header.")
        return False

    f = open(output_file, "w")
    output = Template(platform_info_header_r5_template)
    f.write(output.substitute(inputs))
    f.close()

    return True

def xlnx_openamp_gen_outputs(openamp_channel_info, channel_id, role, verbose = 0 ):
    text_file_contents = ""
    rpmsg_native = openamp_channel_info["rpmsg_native_"+channel_id]
    carveouts = openamp_channel_info["carveouts_"+channel_id]
    elfload = openamp_channel_info["elfload"+channel_id]
    platform = openamp_channel_info["platform"]
    tx = None
    rx = None
    SHARED_BUF_PA = 0
    SHARED_BUF_SIZE = 0
    inputs = None
    global output_file

    for c in carveouts:
        if "tcm" in c.name:
            continue
        base = hex(c.props("start")[0].value)
        size = hex(c.props("size")[0].value)
        name = ""
        if "vring0" in c.name:
            name = "VRING0"
            tx = base
        elif "vring1" in c.name:
            name = "VRING1"
            rx = base
        else:
            name = "VDEV0BUFFER"
            SHARED_BUF_PA = base
            SHARED_BUF_SIZE = size

    if not rpmsg_native:
        tx = "FW_RSC_U32_ADDR_ANY"
        rx = "FW_RSC_U32_ADDR_ANY"

    SHARED_MEM_PA = 0
    RSC_MEM_PA = 0
    for e in elfload:
        if e.props("start") != []: # filter to only parse ELF LOAD node
            RSC_MEM_PA = hex(e.props("start")[0].value)
            SHARED_MEM_PA = hex(e.props("start")[0].value + e.props("size")[0].value)
            break

    shm_dev_name = "\"" + RSC_MEM_PA[2:] + '.shm\"'

    template = None
    irq_vect_ids = {
      SOC_TYPE.ZYNQMP: zynqmp_ipi_to_irq_vect_id,
      SOC_TYPE.VERSAL: versal_ipi_to_irq_vect_id,
      SOC_TYPE.VERSAL_NET: versal_net_ipi_to_irq_vect_id,
      SOC_TYPE.VERSAL2: versal_net_ipi_to_irq_vect_id,
    }

    if platform in [ SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL, SOC_TYPE.VERSAL_NET, SOC_TYPE.VERSAL2 ]:
        host_ipi = openamp_channel_info["host_ipi_" + channel_id]
        host_ipi_bitmask = hex(host_ipi.props("xlnx,ipi-bitmask")[0].value[0])
        host_ipi_irq_vect_id = hex(openamp_channel_info["host_ipi_irq_vect_id" + channel_id])
        host_ipi_base = hex(openamp_channel_info["host_ipi_base"+channel_id])

        remote_ipi = openamp_channel_info["remote_ipi_" + channel_id]
        remote_ipi_bitmask = hex(remote_ipi.props("xlnx,ipi-bitmask")[0].value[0])
        remote_ipi_irq_vect_id = hex(openamp_channel_info["remote_ipi_irq_vect_id" + channel_id])
        remote_ipi_base = hex(openamp_channel_info["remote_ipi_base"+channel_id])

        # update IPIs for remote role on Versal NET
        soc_ipi_map = irq_vect_ids[platform]
        if platform in [SOC_TYPE.VERSAL_NET, SOC_TYPE.VERSAL2] and role == 'remote':
            for key in soc_ipi_map.keys():
                value = soc_ipi_map[key]
                soc_ipi_map[key] = value + 32

            remote_ipi_irq_vect_id = int(remote_ipi_irq_vect_id, 16)
            remote_ipi_irq_vect_id += 32
            remote_ipi_irq_vect_id = hex(remote_ipi_irq_vect_id)

            no_buf_offset = 0x1000
            start_no_buf = 96
            end_no_buf = 101
            for nobuf_ipi in range(start_no_buf, end_no_buf + 1):
                nobuf_ipi_key = 0xEB3B0000 + no_buf_offset * (nobuf_ipi - start_no_buf)
                soc_ipi_map[nobuf_ipi_key] = nobuf_ipi

        IPI_IRQ_VECT_ID = remote_ipi_irq_vect_id if role == 'remote' else host_ipi_irq_vect_id
        IPI_IRQ_VECT_ID_FREERTOS = hex(int(IPI_IRQ_VECT_ID,16) - 32)

        POLL_BASE_ADDR = remote_ipi_base if role == 'remote' else host_ipi_base
        # flip this as we are kicking other side with the bitmask value
        IPI_CHN_BITMASK = host_ipi_bitmask if role == 'remote' else remote_ipi_bitmask

        # Add IPI Info for convenience
        EXTRAS = "\n"
        for key in soc_ipi_map.keys():
            EXTRAS += "#define IPI_" + str(soc_ipi_map[key]) + "_BASE_ADDR " + hex(key) + "UL\n"
            EXTRAS += "#define IPI_" + str(soc_ipi_map[key]) + "_VECT_ID " + str(soc_ipi_map[key]) + "U\n"

        template = platform_info_header_r5_template

        bus_name = "\"generic\"" if role == 'remote' else "\"platform\""
        ipi_dev_name = "\"ipi\""

        if rpmsg_native:
            ipi_dev_name_suffix = openamp_channel_info["rpmsg_native_ipi_"+channel_id].name.split("@")[0]
            ipi_dev_name = "\"" + POLL_BASE_ADDR[2:] + "." +  ipi_dev_name_suffix + "\""

        inputs = {
            "POLL_BASE_ADDR":POLL_BASE_ADDR,
            "SHM_DEV_NAME":shm_dev_name,
            "DEV_BUS_NAME":bus_name,
            "IPI_DEV_NAME":ipi_dev_name,
            "IPI_IRQ_VECT_ID":IPI_IRQ_VECT_ID,
            "IPI_IRQ_VECT_ID_FREERTOS":IPI_IRQ_VECT_ID_FREERTOS,
            "IPI_CHN_BITMASK":IPI_CHN_BITMASK,
            "RING_TX":tx,
            "RING_RX":rx,
            "SHARED_MEM_PA": SHARED_MEM_PA,
            "SHARED_MEM_SIZE":"0x100000UL",
            "SHARED_BUF_OFFSET":hex(openamp_channel_info["shared_buf_offset_"+channel_id]),
            "SHARED_BUF_PA":SHARED_BUF_PA,
            "SHARED_BUF_SIZE":SHARED_BUF_SIZE,
            "EXTRAS":EXTRAS,
        }
    elif platform == SOC_TYPE.ZYNQ:
        inputs = {
            "RING_TX":tx,
            "RING_RX":rx,
            "SHARED_MEM_PA": SHARED_MEM_PA,
            "SHARED_MEM_SIZE":"0x100000UL",
            "SHARED_BUF_OFFSET":SHARED_BUF_OFFSET,
            "SHARED_BUF_PA":SHARED_BUF_PA,
            "SHARED_BUF_SIZE":SHARED_BUF_SIZE,
            "SGI_TO_NOTIFY":15,
            "SGI_NOTIFICATION":14,
            "SCUGIC_DEV_NAME":"\"scugic_dev\"",
            "SCUGIC_BUS_NAME":"\"generic\"",
            "SCUGIC_PERIPH_BASE":"0xF8F00000UL",
        }

        template = platform_info_header_a9_template

    f = open(output_file, "w")
    output = Template(template)
    f.write(output.substitute(inputs))
    f.close()

    return True

def xlnx_rpmsg_parse_generate_native_amba_node(tree):
    try:
        amba_node = tree["/axi"]
    except:
        amba_node = LopperNode(-1, "/axi")
        amba_node + LopperProp(name="u-boot,dm-pre-reloc")
        amba_node + LopperProp(name="ranges")
        amba_node + LopperProp(name="#address-cells", value = 2)
        amba_node + LopperProp(name="#size-cells", value = 2)
        tree.add(amba_node)
        tree.resolve()

    return amba_node

def xlnx_rpmsg_parse(tree, node, openamp_channel_info, options, xlnx_options = None, verbose = 0 ):
    # Xilinx OpenAMP subroutine to collect RPMsg information from RPMsg
    # relation
    amba_node = None
    global output_file

    # skip rpmsg remote node which will link to its host via 'host' property
    if node.props("host") != []:
        return True

    platform = get_platform(tree, verbose)
    root_compat = tree['/'].props("compatible")[0]
    if platform == None:
        print("Unsupported platform: ", root_compat)
        return False
    openamp_channel_info["platform"] = platform

    # check for remote property
    if node.props("remote") == []:
        print("ERROR: ", node, "is missing remote property")
        return False

    remote_nodes = populate_remote_nodes(tree, node.props("remote")[0])
    carveout_prop = node.props("carveouts")[0]
    if carveout_prop == []:
        print("ERROR: ", node, " is missing carveouts property")
        return False

    channel_ids = []
    for i, remote_node in enumerate(remote_nodes):
        channel_id = "_"+node.parent.parent.name+"_"+remote_node.name
        openamp_channel_info["remote_node_"+channel_id] = remote_node
        channel_carveouts_nodes = openamp_channel_info["elfload"+channel_id]
        channel_carveouts_nodes.extend( get_rpmsg_carveout_nodes(tree, openamp_channel_info["remote_node_"+channel_id]) )
        openamp_channel_info["carveouts_"+channel_id] = channel_carveouts_nodes

        # rpmsg native?
        native = node.propval("openamp-xlnx-native")
        if native == [] or len(native) != len(remote_nodes):
            print("ERROR: malformed openamp-xlnx-native property.")
            return False

        native = native[i]
        openamp_channel_info["rpmsg_native_"+channel_id] = native

        if native:
            if platform == SOC_TYPE.ZYNQ:
                print("ERROR: Native RPMsg not supported for Zynq")
                return False
            # if native is true, then find and store amba bus
            # to store IPI and SHM nodes
            amba_node = xlnx_rpmsg_parse_generate_native_amba_node(tree)
            openamp_channel_info["amba_node"] = amba_node

        # Zynq has hard-coded IPIs in driver
        if platform in [ SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL, SOC_TYPE.VERSAL_NET, SOC_TYPE.VERSAL2 ]:
            ret = xlnx_rpmsg_ipi_parse(tree, node, openamp_channel_info,
                                 remote_node, channel_id, native, i, verbose)
            if ret != True:
                return False

        ret = xlnx_rpmsg_update_tree(tree, node, channel_id, openamp_channel_info, verbose )
        if ret != True:
            return False

        channel_ids.append( channel_id )

    try:
        args = options['args']
    except:
        print("No arguments passed for OpenAMP Module. Need role property")
        return False

    # Here try for key value pair arguments
    opts,args2 = getopt.getopt( args, "l:m:n:pv", [ "verbose", "permissive", "openamp_no_header", "openamp_role=", "openamp_host=", "openamp_remote=", "openamp_output_filename=" ] )
    if opts == [] and args2 == []:
        print('ERROR: No arguments passed for OpenAMP Module. Erroring out now.')
        return False

    role = None
    arg_host = None
    arg_remote = None
    no_header = False
    if xlnx_options != None:
        role = xlnx_options["openamp_role"]
        arg_host = xlnx_options["openamp_host"]
        arg_remote = xlnx_options["openamp_remote"]
    else:
        for o,a in opts:
            if o in ('-l', "--openamp_role"):
                role = a
            elif o in ('-m', "--openamp_host"):
                arg_host = a
            elif o in ('-n', "--openamp_remote"):
                arg_remote = a
            elif o in ("--openamp_output_filename"):
                output_file = a
            elif o in ("--openamp_no_header"):
                no_header = True
            else:
                print("Argument: ",o, " is not recognized. Erroring out.")

    if role not in ['host', 'remote']:
        print('ERROR: Role value is not proper. Expect either "host" or "remote". Got: ', role)
        return False
    valid_core_inputs = []
    pattern = re.compile( r'_openamp_([0-9a-z]+_[0-9])_')
    for i in channel_ids:
        for j in pattern.findall(i):
            valid_core_inputs.append(j)
    valid_core_inputs = set(valid_core_inputs)

    if arg_host not in valid_core_inputs or arg_remote not in arg_remote:
        print('ERROR: OpenAMP Host or Remote value is not proper. Valid inputs are:', valid_core_inputs)
        return False

    chan_id = None
    for i in channel_ids:
        if arg_remote in i and arg_host in i:
            chan_id = i
    if chan_id == None:
        print("Unable to find channel with pair", arg_host, arg_remote)
        return False

    # Generate Text file to configure OpenAMP Application
    # Only do this for remote firmware configuration

    if role == 'remote' and no_header == False:
        ret = xlnx_openamp_gen_outputs(openamp_channel_info, chan_id, role, verbose)
        if not ret:
            return ret

    xlnx_openamp_remove_channels(tree)

    # remove definitions
    try:
        defn_node =  tree["/definitions"]
        tree - defn_node
    except:
        return True

    return True


# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False


def determine_cpus_config(remote_domain):
  cpus_prop_val = remote_domain.propval("cpus")
  cpu_config =  cpus_prop_val[2] # split or lockstep

  if cpus_prop_val == [''] or len(cpus_prop_val) != 3:
      print("rpu cluster cpu prop invalid len")
      return -1

  return CPU_CONFIG.RPU_LOCKSTEP if ((cpu_config >> 30 & 0x1) == 0x1) else CPU_CONFIG.RPU_SPLIT


def determinte_rpu_core(tree, cpu_config, remote_node):
    remote_cpus = remote_node.props("cpus")[0]

    try:
        core_index = int(str(tree.pnode(remote_node.props("cpus")[0].value[0]))[-1])
        rpu_core_from_int = RPU_CORE(core_index)
        return rpu_core_from_int
    except:
        print("ERROR: determinte_rpu_core: invalid cpus for ", remote_node, cpu_config)
        return False


def xlnx_remoteproc_construct_carveouts(tree, channel_id, openamp_channel_info, verbose = 0 ):
    carveouts = openamp_channel_info["elfload"+channel_id]
    new_ddr_nodes = []
    res_mem_node = None
    try:
        res_mem_node = tree["/reserved-memory"]
    except KeyError:
        res_mem_node = LopperNode(-1, "/reserved-memory")
        res_mem_node + LopperProp(name="#address-cells",value=2)
        res_mem_node + LopperProp(name="#size-cells",value=2)
        res_mem_node + LopperProp(name="ranges",value=[])
        tree.add(res_mem_node)

    # only applicable for DDR carveouts
    for carveout in carveouts:
        # SRAM banks have status prop
        # SRAM banks are not in reserved memory
        if carveout.props("status") != []:
            continue
        elif carveout.props("no-map") != []:
            start = carveout.props("start")[0].value
            size = carveout.props("size")[0].value
            new_node =  LopperNode(-1, "/reserved-memory/"+carveout.name)
            new_node + LopperProp(name="no-map")
            new_node + LopperProp(name="reg", value=[0, start, 0, size])
            tree.add(new_node)

            if not reserved_mem_node_check(tree, new_node):
                return False

            phandle_val = new_node.phandle_or_create()

            new_node + LopperProp(name="phandle", value = phandle_val)
            new_node.phandle = phandle_val
            new_ddr_nodes.append(new_node.phandle)
        else:
            print("ERROR: invalid remoteproc elfload carveout", carveout)
            return False

    openamp_channel_info["new_ddr_nodes"+channel_id] = new_ddr_nodes

    return True

def platform_validate(platform):
    if platform not in [ SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL, SOC_TYPE.VERSAL_NET, SOC_TYPE.VERSAL2 ]:
        print("ERROR: unsupported platform: ", platform)
        return False
    return True

def xlnx_remoteproc_v2_get_tcm_nodes(elfload_nodes):
    core_compat_substr = [ "tcm-1.0", "tcm-global-11.0" ]
    absolute_addr_tcm_substr = [ "tcm-global-1.0", "tcm-lockstep-1.0", "tcm-global-11.0" ]

    # find TCM nodes
    tcm_nodes = []
    for carveout in elfload_nodes:
        if carveout.props("status") != [] and "tcm" in carveout.name:
            carveout_compat_strs = carveout.propval("compatible")
            for ccs in carveout.propval("compatible"):
                absolute_addr_tcm_match = any(aats in ccs for aats in absolute_addr_tcm_substr)
                core_compat_substr_match = any(core_compat_substr_elem in ccs for core_compat_substr_elem in core_compat_substr)

                if core_compat_substr_match or absolute_addr_tcm_match:
                    tcm_nodes.append(carveout)

    return tcm_nodes

def xlnx_remoteproc_v2_parse_tcm_node(tcm_bank, core_reg_names, cluster_ranges_val, core_reg_val, power_domains, cpu_config, platform, rpu_core):
    # only R5 cores have separate lockstep address schema
    use_lockstep = (cpu_config == CPU_CONFIG.RPU_LOCKSTEP) and (platform in [ SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL ] )

    # validate and use TCM nodes
    tcm_props = [ "xlnx,tcm,absolute-view,base", "xlnx,tcm,bank-size", "power-domains",
                  "xlnx,tcm,rpu-view,lockstep-base" if use_lockstep else "xlnx,tcm,rpu-view,split-base" ]

    # genereate dictionary to look up each property for tcm noted above
    tcm_prop_vals = { p:tcm_bank.propval(p) for p in tcm_props }

    # validate tcm bank properties
    for pval in tcm_prop_vals.keys():
        if tcm_prop_vals[pval] == ['']:
            print("ERROR: ", tcm_bank, " is missing property ", pval, ". This means malformed SDT for OpenAMP. Erroring out.")
            return False

    # read simple props
    tcm_absolute_view_base = tcm_prop_vals[tcm_props[0]][0]
    rpu_bank_sz = tcm_prop_vals[tcm_props[1]][0]
    rpu_view_base_pval = tcm_prop_vals[tcm_props[3]][0]

    # power domain is list
    for i in tcm_prop_vals[tcm_props[2]]:
        power_domains.append(i)

    core_reg_names.append(tcm_bank.name)

    # lockstep r5 cores have different scheme to handle
    cluster_tcm_absolute_addr = tcm_absolute_view_base
    if use_lockstep:
        cluster_tcm_absolute_addr = tcm_absolute_view_base & 0xFFF00000 + rpu_view_base_pval

    cluster_ranges_val.extend((rpu_core.value, hex(rpu_view_base_pval), 0, hex(cluster_tcm_absolute_addr), 0, hex(rpu_bank_sz)))
    core_reg_val.extend((rpu_core.value, hex(rpu_view_base_pval), 0, hex(rpu_bank_sz)))

def xlnx_remoteproc_v2_add_cluster(tree, platform, cpu_config, cluster_ranges_val, cluster_node_path):
    driver_compat_str  = {
      SOC_TYPE.ZYNQMP : "xlnx,zynqmp-r5fss",
      SOC_TYPE.VERSAL : "xlnx,versal-r5fss",
      SOC_TYPE.VERSAL_NET : "xlnx,versal-net-r52fss",
      SOC_TYPE.VERSAL2 : "xlnx,versal2-r52fss",
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

        # only in split case, let range value be extended by both cores
        if cpu_config == CPU_CONFIG.RPU_SPLIT:
            cluster_ranges_prop = cluster_node.props("ranges")[0]
            cluster_ranges_pval = cluster_ranges_prop.value
            for i in cluster_ranges_val:
                cluster_ranges_pval.append(i)
            cluster_ranges_prop.value = cluster_ranges_pval

    except KeyError:
        cluster_node = LopperNode(-1, cluster_node_path)
        for key in cluster_node_props.keys():
            cluster_node + LopperProp(name=key, value = cluster_node_props[key])

        tree.add(cluster_node)


def xlnx_remoteproc_v2_add_core(tree, openamp_channel_info, channel_id, power_domains, core_reg_val, core_reg_names, cluster_node_path, platform):
    compatible_strs = { SOC_TYPE.VERSAL2:  "xlnx,versal2-r52f", SOC_TYPE.VERSAL_NET:  "xlnx,versal-net-r52f", SOC_TYPE.VERSAL: "xlnx,versal-r5f", SOC_TYPE.ZYNQMP: "xlnx,zynqmp-r5f" }
    core_names = { SOC_TYPE.VERSAL_NET: "r52f", SOC_TYPE.VERSAL: "r5f", SOC_TYPE.ZYNQMP: "r5f" }
    core_names[SOC_TYPE.VERSAL2] = core_names[SOC_TYPE.VERSAL_NET]

    core_node = LopperNode(-1, "{}/{}@{}".format( cluster_node_path, core_names[platform], openamp_channel_info["rpu_core"+channel_id]))

    core_node_props = {
      "compatible" : compatible_strs[platform],
      "power-domains": power_domains,
      "reg": core_reg_val,
      "reg-names": core_reg_names,
      "memory-region": [ val for val in openamp_channel_info["new_ddr_nodes"+channel_id] ]
    }

    for key in core_node_props.keys():
        core_node + LopperProp(name=key, value = core_node_props[key])

    tree.add(core_node)

    return core_node


def xlnx_remoteproc_v2_cluster_base_str(platform, rpu_core):
    cluster_node_path_name_suffix = {
        str(SOC_TYPE.VERSAL_NET) + RPU_CORE.RPU_0.name: hex(0xeba00000).replace("0x",""),
        str(SOC_TYPE.VERSAL_NET) + RPU_CORE.RPU_1.name: hex(0xeba00000).replace("0x",""),
        str(SOC_TYPE.VERSAL_NET) + RPU_CORE.RPU_2.name: hex(0xeba40000).replace("0x",""),
        str(SOC_TYPE.VERSAL_NET) + RPU_CORE.RPU_3.name: hex(0xeba40000).replace("0x",""),

        str(SOC_TYPE.VERSAL2) + RPU_CORE.RPU_0.name: hex(0xeba00000).replace("0x",""),
        str(SOC_TYPE.VERSAL2) + RPU_CORE.RPU_1.name: hex(0xeba00000).replace("0x",""),
        str(SOC_TYPE.VERSAL2) + RPU_CORE.RPU_2.name: hex(0xebb00000).replace("0x",""),
        str(SOC_TYPE.VERSAL2) + RPU_CORE.RPU_3.name: hex(0xebb00000).replace("0x",""),
        str(SOC_TYPE.VERSAL2) + RPU_CORE.RPU_4.name: hex(0xebc00000).replace("0x",""),
        str(SOC_TYPE.VERSAL2) + RPU_CORE.RPU_5.name: hex(0xebc00000).replace("0x",""),
        str(SOC_TYPE.VERSAL2) + RPU_CORE.RPU_6.name: hex(0xebac0000).replace("0x",""),
        str(SOC_TYPE.VERSAL2) + RPU_CORE.RPU_7.name: hex(0xebac0000).replace("0x",""),
        str(SOC_TYPE.VERSAL2) + RPU_CORE.RPU_8.name: hex(0xebbc0000).replace("0x",""),
        str(SOC_TYPE.VERSAL2) + RPU_CORE.RPU_9.name: hex(0xebbc0000).replace("0x",""),

        str(SOC_TYPE.ZYNQMP) + RPU_CORE.RPU_0.name: "ffe00000",
        str(SOC_TYPE.ZYNQMP) + RPU_CORE.RPU_1.name: "ffe00000",
        str(SOC_TYPE.VERSAL) + RPU_CORE.RPU_0.name: "ffe00000",
        str(SOC_TYPE.VERSAL) + RPU_CORE.RPU_1.name: "ffe00000",
    }

    return cluster_node_path_name_suffix[str(platform) + str(rpu_core.name)]


def xlnx_remoteproc_v2_interim(tree, channel_id, cpu_config, openamp_channel_info, verbose = 0):
    platform = openamp_channel_info["platform"]
    driver_compat_str  = {
      SOC_TYPE.ZYNQMP : "xlnx,zynqmp-r5fss",
      SOC_TYPE.VERSAL : "xlnx,versal-r5fss",
    }

    fw_path = {
        SOC_TYPE.ZYNQMP : "/firmware/zynqmp-firmware",
        SOC_TYPE.VERSAL : "/firmware/versal-firmware",
    }

    cluster_modes = {
        CPU_CONFIG.RPU_SPLIT: 0,
        CPU_CONFIG.RPU_LOCKSTEP: 1,
    }

    fw_node = tree[fw_path[platform]]

    pd_pval_rpu0 = {
        SOC_TYPE.ZYNQMP : [ fw_node.phandle, 0x7, fw_node.phandle, 0xF, fw_node.phandle, 0x10 ],
        SOC_TYPE.VERSAL : [ fw_node.phandle, 0x18110005, fw_node.phandle, 0x1831800B, fw_node.phandle, 0x1831800C ],
    }
    pd_pval_rpu1 = {
        SOC_TYPE.ZYNQMP : [ fw_node.phandle, 0x8, fw_node.phandle, 0x11, fw_node.phandle, 0x12 ],
        SOC_TYPE.VERSAL : [ fw_node.phandle, 0x18110006, fw_node.phandle, 0x1831800D, fw_node.phandle, 0x1831800E ],
    }
    pd_val_lockstep = {
        SOC_TYPE.ZYNQMP : [ fw_node.phandle, 0x7, fw_node.phandle, 0xF, fw_node.phandle, 0x10, fw_node.phandle, 0xF, fw_node.phandle, 0x10 ],
        SOC_TYPE.VERSAL : [ fw_node.phandle, 0x18110005, fw_node.phandle, 0x1831800B, fw_node.phandle, 0x1831800C, fw_node.phandle, 0x1831800D, fw_node.phandle, 0x1831800E ],
    }

    cluster_node_props = {
        "compatible" : driver_compat_str[platform],
        "xlnx,cluster-mode": cluster_modes[cpu_config.value],
        "xlnx,tcm-mode": cluster_modes[cpu_config.value],
        "#address-cells": 2,
        "#size-cells": 2,
        "ranges": [ 0x0, 0x0, 0x0, 0xffe00000, 0x0, 0x10000, 
                    0x0, 0x20000, 0x0, 0xffe20000, 0x0, 0x10000,
                    0x1, 0x0, 0x0, 0xffe90000, 0x0, 0x10000,
                    0x1, 0x20000, 0x0, 0xffeb0000, 0x0, 0x10000 ],
    }

    cluster_name = "/remoteproc@ffe00000"

    rpu_props = {
        "compatible": "xlnx,zynqmp-r5f",
        "reg-names": [ "atcm0", "btcm0" ],
        "reg": [0, 0, 0, 0x10000, 0, 0x20000, 0, 0x10000],
        "memory-region": [ val for val in openamp_channel_info["new_ddr_nodes"+channel_id] ],
        "power-domains": pd_pval_rpu0[platform],
    }

    if cpu_config == CPU_CONFIG.RPU_LOCKSTEP:
        cluster_node_props["ranges"] = [ 0, 0, 0, 0xffe00000, 0, 0x10000,
                                         0, 0x20000, 0, 0xffe20000, 0, 0x10000,
                                         0, 0x10000, 0, 0xffe10000, 0, 0x10000,
                                         0, 0x30000, 0, 0xffe30000, 0, 0x10000]
        rpu_props["reg-names"] = [ "atcm0", "btcm0", "atcm1", "btcm1" ]
        rpu_props["power-domains"] = pd_val_lockstep[platform]

    cluster_node = None
    rpu1_case = False
    try:
        cluster_node = tree[cluster_name]

        # Split RPU1 Case
        rpu_props["reg"] = [1, 0, 0, 0x10000, 1, 0x20000, 0, 0x10000]
        rpu_props["power-domains"] = pd_pval_rpu1[platform]
    except KeyError:
        cluster_node = LopperNode(-1, cluster_name)
        tree.add(cluster_node)
        for key in cluster_node_props.keys():
            cluster_node + LopperProp(name=key, value = cluster_node_props[key])

    core_node = LopperNode(-1, "{}/{}@{}".format( cluster_name, "r5f", openamp_channel_info["rpu_core"+channel_id]))
    for key in rpu_props.keys():
        core_node + LopperProp(name=key, value = rpu_props[key])
    tree.add(core_node)

    openamp_channel_info["core_node"+channel_id] = core_node
    openamp_channel_info["core_index"] = openamp_channel_info["core_index"] + 1

    return True


def xlnx_remoteproc_v2_construct_cluster(tree, channel_id, openamp_channel_info, verbose = 0):
    cpu_config = openamp_channel_info["cpu_config"+channel_id]

    rpu_core = determinte_rpu_core(tree, cpu_config, openamp_channel_info["remote_node"+channel_id] )
    platform = get_platform(tree, verbose)
    cluster_ranges_val = []
    core_reg_names = []
    power_domains = []
    core_reg_val = []

    if not platform_validate(platform):
        return False
    elif platform in [ SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL ]:
        return xlnx_remoteproc_v2_interim(tree, channel_id, cpu_config, openamp_channel_info, verbose = 0)

    for i in openamp_channel_info["rpu_core_pd_prop"+channel_id].value:
       power_domains.append(i)

    # find TCM nodes
    tcm_nodes = xlnx_remoteproc_v2_get_tcm_nodes(openamp_channel_info["elfload"+channel_id])

    # validate and use TCM nodes - this will be used for cluster ranges property too.
    for tcm_bank in tcm_nodes:
        xlnx_remoteproc_v2_parse_tcm_node(tcm_bank, core_reg_names, cluster_ranges_val, core_reg_val, power_domains, cpu_config, platform, rpu_core)

    # construct remoteproc cluster node
    cluster_node_path = "/remoteproc@" + xlnx_remoteproc_v2_cluster_base_str(platform, rpu_core)
    xlnx_remoteproc_v2_add_cluster(tree, platform, cpu_config, cluster_ranges_val, cluster_node_path)

    # add individual core node in cluster node
    openamp_channel_info["core_node"+channel_id] = xlnx_remoteproc_v2_add_core(tree, openamp_channel_info, channel_id, power_domains,
                                                                               core_reg_val, core_reg_names, cluster_node_path, platform)
    openamp_channel_info["core_index"] = openamp_channel_info["core_index"] + 1

    return True


def xlnx_remoteproc_construct_cluster(tree, channel_id, openamp_channel_info, verbose = 0):
    platform = openamp_channel_info["platform"]
    cpu_config = openamp_channel_info["cpu_config"+channel_id]
    node = openamp_channel_info["node"+channel_id]
    host_node = node.parent.parent
    remote_node = openamp_channel_info["remote_node"+channel_id]
    elfload_nodes = openamp_channel_info["elfload"+channel_id]
    new_ddr_nodes = openamp_channel_info["new_ddr_nodes"+channel_id]
    cluster_node = None
    rpu_core = openamp_channel_info["rpu_core"+channel_id]
    cluster_node_path = "/rf5ss@ff9a0000" # SOC_TYPE.VERSAL, SOC_TYPE.ZYNQMP
    cluster_reg = 0xff9a0000
    driver_compat_str  = {
      SOC_TYPE.ZYNQMP : "xlnx,zynqmp-r5-remoteproc",
      SOC_TYPE.VERSAL : "xlnx,versal-r5-remoteproc",
      SOC_TYPE.VERSAL_NET : "xlnx,versal-net-r52-remoteproc",
      SOC_TYPE.VERSAL2: "xlnx,versal2-r52-remoteproc",
    }

    if not platform_validate(platform):
        return False

    if platform == SOC_TYPE.VERSAL_NET:
        cluster_node_path = "/rf52ss_"
        cluster = "0"
        rpu_cfg_reg = 0xFF9A0100
        if int(rpu_core) > int(RPU_CORE.RPU_1):
            cluster = "1"
            rpu_cfg_reg = 0xFF9A0200

        cluster_node_path += cluster + "@" + hex(rpu_cfg_reg).replace("0x","")
        cluster_reg = rpu_cfg_reg
    elif platform == SOC_TYPE.VERSAL2:
        cluster_node_path = "/rf52ss_"
        cluster = str( int(RPU_CORE.RPU_1) / 2 - 1)

        rpu_cfg_reg = { "0" : 0xeba00000, "1" : 0xebb00000, "2" : 0xebc00000, "3" : 0xebac0000, "4" : 0xebbc0000 }[cluster]
        cluster_node_path += cluster + "@" + hex(rpu_cfg_reg).replace("0x","")
        cluster_reg = rpu_cfg_reg

    cluster_node_props = {
      "compatible" : driver_compat_str[platform],
      "#address-cells": 0x2,
      "#size-cells": 0x2,
      "ranges": [],
      "xlnx,cluster-mode": int(cpu_config),
      "reg": [0, cluster_reg, 0, 0x10000],
    }

    if cpu_config in [ CPU_CONFIG.RPU_LOCKSTEP, CPU_CONFIG.RPU_SPLIT ]:
        try:
            cluster_node = tree[cluster_node_path]
        except KeyError:
            cluster_node = LopperNode(-1,cluster_node_path)
            cluster_node = LopperNode(-1, cluster_node_path)

            for key in cluster_node_props.keys():
                cluster_node + LopperProp(name=key, value = cluster_node_props[key])

            tree.add(cluster_node)

        rpu_core = openamp_channel_info["rpu_core"+channel_id]
        rpu_core_pd_prop = openamp_channel_info["rpu_core_pd_prop"+channel_id]

        core_name = "r5f_" + rpu_core
        compatible_str = "xilinx,r5f"
        if platform in [SOC_TYPE.VERSAL_NET, SOC_TYPE.VERSAL2]:
            core_name = "r52f_" + str(rpu_core)
            compatible_str = "xilinx,r52f"

        core_node = LopperNode(-1, cluster_node_path + "/" + core_name)

        core_node_props = {
          "compatible" :compatible_str,
          "#address-cells": 0x2,
          "#size-cells": 0x2,
          "ranges": [],
          "power-domains": copy.deepcopy(rpu_core_pd_prop.value),
          "mbox-names": ["tx", "rx"],
        }

        for key in core_node_props.keys():
            core_node + LopperProp(name=key, value = core_node_props[key])

        tree.add(core_node)
        openamp_channel_info["core_node"+channel_id] = core_node

        srams = []
        for carveout in elfload_nodes:
            if carveout.props("status") != [] or "tcm" in carveout.name:
                srams.append(carveout.phandle)
                # FIXME for each sram, add 'power-domains' prop for kernel driver
                carveout + LopperProp(name="power-domains",
                                      value=copy.deepcopy( carveout.props("power-domains")[0].value ))

        core_node + LopperProp(name="sram", value=srams)
    elif platform == SOC_TYPE.ZYNQ:
        core_node = LopperNode(-1, "/remoteproc@0")
        core_node + LopperProp(name="compatible", value = "xlnx,zynq_remoteproc")
        core_node + LopperProp(name="firmware", value = "firmware")

        tree.add(core_node)

    else:
        return False

    # there may be new nodes created in linux reserved-memory node to account for
    memory_region = []
    for phandle_val in new_ddr_nodes:
        memory_region.append(phandle_val)
    core_node + LopperProp(name="memory-region", value=memory_region)

    return True

def xlnx_remoteproc_update_tree(tree, channel_id, openamp_channel_info, verbose = 0 ):
    global info_rproc_driver_version
    node = openamp_channel_info["node"+channel_id]
    host_node = node.parent.parent

    platform = openamp_channel_info["platform"]

    if platform not in [SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL, SOC_TYPE.VERSAL_NET, SOC_TYPE.VERSAL2]:
        openamp_channel_info["cpu_config"+channel_id] = 0

    ret = xlnx_remoteproc_construct_carveouts(tree, channel_id, openamp_channel_info, verbose)
    if ret == False:
        return ret

    if openamp_channel_info[REMOTEPROC_D_TO_D_v2]:
        if verbose > 0 and not info_rproc_driver_version:
            print("[INFO]: ------> OPENAMP: DRIVER FORMAT 'xlnx,zynqmp-r5fss' USED")
            info_rproc_driver_version = True
        ret = xlnx_remoteproc_v2_construct_cluster(tree, channel_id, openamp_channel_info, verbose = 0)
    else:
        ret = xlnx_remoteproc_construct_cluster(tree, channel_id, openamp_channel_info, verbose = 0)

    if ret == False:
        return ret

    return True


def xlnx_remoteproc_rpu_parse(tree, node, openamp_channel_info, remote_node, elfload_nodes, verbose = 0):
    cpu_config = determine_cpus_config(remote_node)
    platform = get_platform(tree, verbose)
    rpu_core = None

    if cpu_config in [ CPU_CONFIG.RPU_LOCKSTEP, CPU_CONFIG.RPU_SPLIT]:
        rpu_core = determinte_rpu_core(tree, cpu_config, remote_node )
        if rpu_core not in RPU_CORE:
            print("ERROR: Invalid rpu core: ", rpu_core, platform)
            return False
    else:
        print("ERROR: cpu_config: ", cpu_config, " is not in ", [ CPU_CONFIG.RPU_LOCKSTEP, CPU_CONFIG.RPU_SPLIT])
        return False
    rpu_cluster_node = tree.pnode(remote_node.props("cpus")[0].value[0])
    rpu_core_node = rpu_cluster_node.abs_path + "/cpu@"
    # all cores are in cluster topologically in DTS
    rpu_core_int_val = int(rpu_core)
    rpu_core = str(int(rpu_core)) 

    rpu_core_node = tree[rpu_core_node+rpu_core]

    if rpu_core_node.props("power-domains") == []:
        print("ERROR: RPU core does not have power-domains property.")
        return False

    rpu_core_pd_prop = rpu_core_node.props("power-domains")[0]
    channel_id = "_"+node.parent.parent.name+"_"+remote_node.name
    openamp_channel_info["elfload"+channel_id] = elfload_nodes
    openamp_channel_info["rpu_core_pd_prop"+channel_id] = rpu_core_pd_prop
    openamp_channel_info["cpu_config"+channel_id] = cpu_config
    openamp_channel_info["rpu_core"+channel_id] = rpu_core
    return True

banner_printed = False
def get_platform(tree, verbose = 0):
    # set platform
    global banner_printed
    platform = None
    root_node = tree["/"]
    root_model = root_node.propval("model")[0]
    root_compat = root_node.propval("compatible")

    inputs = root_node.propval("compatible")
    inputs.append(root_model)

    zynqmp = [ 'Xilinx ZynqMP',  "xlnx,zynqmp" ]
    versal = [ 'xlnx,versal', 'Xilinx Versal']
    versalnet = [ 'versal-net', 'Versal NET', "xlnx,versal-net", "Xilinx Versal NET" ]
    versal2 = [ 'xlnx,versal2', 'amd,versal2', 'amd versal vek385 reva' ]

    rpu_socs = [ versal2, zynqmp, versal, versalnet ]
    rpu_socs_enums = [ SOC_TYPE.VERSAL2, SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL, SOC_TYPE.VERSAL_NET ]

    if verbose > 0 and not banner_printed:
        print("[INFO]: ------> OPENAMP: XLNX: \troot_model: ", root_model, "\troot_compat: ", root_compat)
        banner_printed = True

    for index, soc in enumerate(rpu_socs):
        for soc_str in soc:
            for i in inputs:
                if i == soc_str:
                    return rpu_socs_enums[index]

    if platform == None:
        print("Unable to find data for platform: ", root_model, root_compat)

    return platform

def get_remote_node(tree, remote_nodes, index):
    return tree.pnode( remote_nodes[i] )

def populate_remote_nodes(tree, remote_prop):
    remote_nodes = []

    for remote_node in remote_prop.value:
        remote_nodes.append( tree.pnode(remote_node) )

    return remote_nodes

def xlnx_remoteproc_parse(tree, node, openamp_channel_info, verbose = 0 ):
    # Xilinx OpenAMP subroutine to collect RPMsg information from Remoteproc
    # relation
    elfload_nodes = []
    platform = get_platform(tree, verbose)
    root_compat = tree['/'].props("compatible")[0]
    if platform == None:
        print("Unsupported platform: ", root_compat)
        return False
    openamp_channel_info["platform"] = platform

    # check for remote property
    if node.props("remote") == []:
        print("ERROR: ", node, "is missing remote property")
        return False
    remote_nodes = populate_remote_nodes(tree, node.props("remote")[0])

    # check for elfload prop
    if node.props("elfload") == []:
        print("ERROR: ", node, " is missing elfload property")
        return False

    openamp_channel_info["core_index"] = 0
    for i, remote_node in enumerate(remote_nodes):
        channel_elfload_nodes = []
        prop_name = "elfload" + str(i)
        elfload_prop = node.propval(prop_name)

        for current_elfload in elfload_prop:
            elfloadnode = tree.pnode( current_elfload )
            channel_elfload_nodes.append ( elfloadnode )

        if platform in [SOC_TYPE.ZYNQMP, SOC_TYPE.VERSAL, SOC_TYPE.VERSAL_NET, SOC_TYPE.VERSAL2]:
            cpu_config = determine_cpus_config(remote_node)
            if cpu_config ==  CPU_CONFIG.RPU_SPLIT and len(remote_nodes) == 1:
                print("ERROR: CPU config is split but only 1 remote node", remote_node, "has been provided.")
                return False

            ret = xlnx_remoteproc_rpu_parse(tree, node, openamp_channel_info, remote_node, channel_elfload_nodes, verbose)
            if not ret:
                return ret

        channel_id = "_"+node.parent.parent.name+"_"+remote_node.name

        openamp_channel_info["elfload"+channel_id] = channel_elfload_nodes
        openamp_channel_info["remote_node"+channel_id] = remote_node
        openamp_channel_info["node"+channel_id] = node

        ret = xlnx_remoteproc_update_tree(tree, channel_id, openamp_channel_info, verbose = 0 )
        if not ret:
            print("ERROR: Failed to update tree for Remoteproc.")
            return False

    return True

def xlnx_openamp_remove_channels(tree, verbose = 0):
    v2 = False
    for n in tree["/domains"].subnodes():
            node_compat = n.props("compatible")
            if node_compat != []:
                node_compat = node_compat[0].value
                if node_compat in [REMOTEPROC_D_TO_D_v2]:
                    v2 = True
                if node_compat == [RPMSG_D_TO_D, REMOTEPROC_D_TO_D_v2, REMOTEPROC_D_TO_D]:
                    tree - n

    # FIXME this will go away once default is upstream driver.
    if v2:
        for n in tree["/"].subnodes():
            if 'tcm' in n.name:
                tree - n


def xlnx_openamp_find_channels(sdt, verbose = 0):
    # Xilinx OpenAMP subroutine to parse OpenAMP Channel
    # information and generate Device Tree information.
    tree = sdt.tree
    domains_present = False
    for n in tree["/"].subnodes():
        if n.name == "domains":
            domains_present = True

    if not domains_present:
        return False

    for n in tree["/domains"].subnodes():
            node_compat = n.props("compatible")
            if node_compat != []:
                node_compat = node_compat[0].value

                if node_compat in [REMOTEPROC_D_TO_D_v2, REMOTEPROC_D_TO_D]:
                    return True
                if node_compat == RPMSG_D_TO_D:
                    return True

    return False

def xlnx_openamp_parse(sdt, options, xlnx_options = None, verbose = 0 ):
    # Xilinx OpenAMP subroutine to parse OpenAMP Channel
    # information and generate Device Tree information.
    tree = sdt.tree
    ret = -1
    openamp_channel_info = {}

    try:
        gen_outputs_only = False
        output_file = None
        arg_remote = None
        args = options['args']
        opts,args2 = getopt.getopt( args, "d:v:n:", [ "verbose", "openamp_header_only", "openamp_output_filename=", "openamp_remote=" ])
        if opts != []:
            for o,a in opts:
                print("arg:", o,a)
                if o in ('-d',"--openamp_header_only"):
                    gen_outputs_only = True
                elif o in ("--openamp_output_filename"):
                    output_file = a
                elif o in ('-n', "--openamp_remote"):
                    arg_remote = a

        if gen_outputs_only and output_file != None and arg_remote != None:
            return xlnx_openamp_gen_outputs_only(sdt, arg_remote, output_file, verbose)
    except:
        pass

    for n in tree["/domains"].subnodes():
            node_compat = n.props("compatible")
            if node_compat != []:
                node_compat = node_compat[0].value

                if node_compat in [REMOTEPROC_D_TO_D_v2, REMOTEPROC_D_TO_D]:
                    openamp_channel_info[REMOTEPROC_D_TO_D_v2] = (node_compat == REMOTEPROC_D_TO_D_v2)
                    ret = xlnx_remoteproc_parse(tree, n, openamp_channel_info, verbose)
                elif node_compat == RPMSG_D_TO_D:
                    ret = xlnx_rpmsg_parse(tree, n, openamp_channel_info, options, xlnx_options, verbose)

                if ret == False:
                    return ret

    return True

def xlnx_openamp_rpmsg_expand(tree, subnode, verbose = 0 ):
    # Xilinx-specific YAML expansion of RPMsg description.
    root_node = tree["/"]
    root_compat = root_node.props("compatible")[0].value
    platform = get_platform(tree, verbose)

    if platform == None:
        print("Unsupported platform: ", root_compat)
        return False

    ret = resolve_host_remote( tree, subnode, verbose)
    if ret == False:
        return ret
    ret = resolve_rpmsg_carveouts( tree, subnode, verbose)
    if ret == False:
        return ret
    ret = resolve_rpmsg_mbox( tree, subnode, verbose)
    # Zynq platform has mailboxes set in driver
    if ret == False and platform != SOC_TYPE.ZYNQ:
        return ret


    return True


def xlnx_openamp_remoteproc_expand(tree, subnode, verbose = 0 ):
    # Xilinx-specific YAML expansion of Remoteproc description.
    ret = resolve_host_remote( tree, subnode, verbose)
    if ret == False:
        return ret
    ret = resolve_remoteproc_carveouts( tree, subnode, verbose)
    if ret == False:
        return ret


    return True
