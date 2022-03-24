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

sys.path.append(os.path.dirname(__file__))
from openamp_xlnx_common import *

RPU_PATH = "/rpu@ff9a0000"
REMOTEPROC_D_TO_D = "openamp,remoteproc-v1"
RPMSG_D_TO_D = "openamp,rpmsg-v1"
output_file = "openamp-channel-info.txt"

class CPU_CONFIG(Enum):
    RPU_SPLIT = 0
    RPU_LOCKSTEP = 1

class RPU_CORE(Enum):
    RPU_0 = 0
    RPU_1 = 1

def is_compat( node, compat_string_to_test ):
    if re.search( "openamp,xlnx-rpu", compat_string_to_test):
        return xlnx_openamp_rpu
    return ""

def xlnx_rpmsg_native_update_carveouts(tree, elfload_node,
                                       native_shm_mem_area_start, native_shm_mem_area_size,
                                       native_amba_shm_node):
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

    # FIXME: update demos so that start of shm is not hard-coded offset
    native_shm_mem_area_start += 0x20000
    shm_space = [0, native_shm_mem_area_start, 0, native_shm_mem_area_size]
    native_amba_shm_node + LopperProp(name="reg", value=shm_space)

    return True


native_shm_node_count = 0
def xlnx_rpmsg_construct_carveouts(tree, carveouts, rpmsg_carveouts, native,
                                   amba_node = None, elfload_node = None, verbose = 0 ):
    global native_shm_node_count
    res_mem_node = tree["/reserved-memory"]
    native_amba_shm_node = None
    native_shm_mem_area_size = 0
    native_shm_mem_area_start = 0xFFFFFFFF

    if amba_node != None:
        native_amba_shm_node = LopperNode(-1, amba_node.abs_path + "/shm" + str(native_shm_node_count))
        native_amba_shm_node + LopperProp(name="compatible", value="shm_uio")
        tree.add(native_amba_shm_node)
        tree.resolve()
        native_shm_node_count += 1


    # only applicable for DDR carveouts
    for carveout in carveouts:
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

                if "vdev0buffer" in carveout.name:
                    new_node + LopperProp(name="compatible", value="shared-dma-pool")

                tree.add(new_node)
                tree.resolve()

                if new_node.phandle == 0:
                    new_node.phandle = new_node.phandle_or_create()

                new_node + LopperProp(name="phandle", value = new_node.phandle)
                rpmsg_carveouts.append(new_node)

        else:
            print("WARNING: invalid remoteproc elfload carveout", carveout)
            return False


    if native:
        ret = xlnx_rpmsg_native_update_carveouts(tree, elfload_node,
                                                 native_shm_mem_area_start, native_shm_mem_area_size,
                                                 native_amba_shm_node)
        if not ret:
            return ret

    return True


def xlnx_rpmsg_ipi_parse(tree, node, openamp_channel_info,
                         remote_node, channel_id, native,
                         verbose = 0 ):
    remote = node.props("remote")
    carveouts_prop = node.props("carveouts")
    amba_node = None
    ipi_id_prop_name = "xlnx,ipi-id"
    host_to_remote_ipi = None
    remote_to_host_ipi = None

    # collect host ipi
    host_ipi_prop = node.props("mbox")
    if host_ipi_prop == []:
        print("WARNING: ", node, " is missing mbox property")
        return False

    host_ipi_prop = host_ipi_prop[0]
    host_ipi = tree.pnode(host_ipi_prop.value[0])

    # collect corresponding remote rpmsg relation
    remote_node = tree.pnode( remote[0].value[0] )
    remote_rpmsg_relation = None
    try:
        remote_rpmsg_relation = tree[remote_node.abs_path + "/domain-to-domain/rpmsg-relation"]
    except:
        print("WARNING: ", remote_node, " is missing rpmsg relation")
        return False

    # collect remote ipi
    remote_ipi_prop = remote_rpmsg_relation.props("mbox")
    if remote_ipi_prop == []:
        print("WARNING: ", remote_node, " is missing mbox property")
        return False

    remote_ipi_prop = remote_ipi_prop[0]
    remote_ipi = tree.pnode(remote_ipi_prop.value[0])

    host_ipi_id = host_ipi.props(ipi_id_prop_name)
    if host_ipi_id == []:
        print("WARNING", host_ipi, " does not have property name: ", ipi_id_prop_name)
    host_ipi_id = host_ipi_id[0]

    remote_ipi_id = remote_ipi.props(ipi_id_prop_name)
    if remote_ipi_id == []:
        print("WARNING", remote_ipi, " does not have property name: ", ipi_id_prop_name)
        return False
    remote_ipi_id = remote_ipi_id[0]

    # find host to remote buffers
    host_to_remote_ipi_channel = None
    for subnode in host_ipi.subnodes():
        subnode_ipi_id = subnode.props(ipi_id_prop_name)
        if subnode_ipi_id != [] and remote_ipi_id.value[0] == subnode_ipi_id[0].value[0]:
            host_to_remote_ipi_channel = subnode
    if host_to_remote_ipi_channel == None:
        print("WARNING no host to remote IPI channel has been found.")
        return False

    # find remote to host buffers
    remote_to_host_ipi_channel = None
    for subnode in remote_ipi.subnodes():
        subnode_ipi_id = subnode.props(ipi_id_prop_name)
        if subnode_ipi_id != [] and host_ipi_id.value[0] == subnode_ipi_id[0].value[0]:
            remote_to_host_ipi_channel = subnode
    if remote_to_host_ipi_channel == None:
        print("WARNING no remote to host IPI channel has been found.")
        return False

    # set platform
    platform = None
    root_node = tree["/"]
    root_compat = root_node.props("compatible")[0].value
    for compat in root_compat:
        if "zynqmp" in compat:
            platform = SOC_TYPE.ZYNQMP
            break
        elif "versal" in compat:
            platform = SOC_TYPE.VERSAL
            break
    openamp_channel_info["platform"] = platform

    # store IPI IRQ Vector IDs
    platform =  openamp_channel_info["platform"]
    host_ipi_base = host_ipi.props("reg")[0][1]
    remote_ipi_base = remote_ipi.props("reg")[0][1]

    if SOC_TYPE.ZYNQMP == platform:
        if host_ipi_base not in zynqmp_ipi_to_irq_vect_id.keys():
            print("WARNING: host IPI", hex(host_ipi_base), "not in IRQ VECTOR ID Mapping for ZU+")
            return False
        if remote_ipi_base not in zynqmp_ipi_to_irq_vect_id.keys():
            print("WARNING: remote IPI", hex(remote_ipi_base), "not in IRQ VECTOR ID Mapping for ZU+")
            return False

        openamp_channel_info["host_ipi_base"+channel_id] = host_ipi_base
        openamp_channel_info["host_ipi_irq_vect_id"+channel_id] = zynqmp_ipi_to_irq_vect_id[host_ipi_base]
        openamp_channel_info["remote_ipi_base"+channel_id] = remote_ipi_base
        openamp_channel_info["remote_ipi_irq_vect_id"+channel_id] = zynqmp_ipi_to_irq_vect_id[remote_ipi_base]
    else:
        print("Unsupported platform")

    openamp_channel_info["host_ipi_"+channel_id] = host_ipi
    openamp_channel_info["remote_ipi_"+channel_id] = remote_ipi
    openamp_channel_info["rpmsg_native_"+channel_id] = native
    openamp_channel_info["host_to_remote_ipi_channel_" + channel_id] = host_to_remote_ipi_channel
    openamp_channel_info["remote_to_host_ipi_channel_" + channel_id] = remote_to_host_ipi_channel

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
        print("WARNING: host ipi ", host_ipi, " missing interrupts property")
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
                                  amba_ipi_node_index, host_ipi):
    amba_node = openamp_channel_info["amba_node"]
    amba_ipi_node = LopperNode(-1, amba_node.abs_path + "/openamp_ipi" + str(amba_ipi_node_index))
    amba_ipi_node + LopperProp(name="compatible",value="ipi_uio")
    amba_ipi_node + LopperProp(name="interrupts",value=copy.deepcopy(host_ipi.props("interrupts")[0].value))
    amba_ipi_node + LopperProp(name="interrupt-parent",value=[gic_node_phandle])
    reg_val = copy.deepcopy(host_ipi.props("reg")[0].value)
    reg_val[3] = 0x1000
    amba_ipi_node + LopperProp(name="reg",value=reg_val)
    tree.add(amba_ipi_node)
    tree.resolve()
    amba_ipi_node_index += 1

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
                    print("WARNING:  conflicting userspace and kernelspace for host ipi: ", host_ipi)
                    return False

    return True


def xlnx_rpmsg_kernel_update_mboxes(tree, host_ipi, remote_ipi, gic_node_phandle,
                                  openamp_channel_info, channel_id,
                                  core_node, remote_controller_node, rpu_core):
    remote_controller_node + LopperProp(name="phandle", value = remote_controller_node.phandle)
    remote_controller_node + LopperProp(name="#mbox-cells", value = 1)
    remote_controller_node + LopperProp(name="xlnx,ipi-id", value = remote_ipi.props("xlnx,ipi-id")[0].value)
    reg_names_val = ["local_response_region", "local_request_region", "remote_response_region", "remote_request_region"]
    remote_controller_node + LopperProp(name="reg-names", value = reg_names_val)

    cpu_config = str(openamp_channel_info["cpu_config"+channel_id].value)
    host_to_remote_ipi_channel = openamp_channel_info["host_to_remote_ipi_channel_"+channel_id]
    remote_to_host_ipi_channel= openamp_channel_info["remote_to_host_ipi_channel_"+channel_id]
    response_buf_str = "xlnx,ipi-response_message_buffer"
    request_buf_str = "xlnx,ipi-request_message_buffer"

    host_remote_response = host_to_remote_ipi_channel.props(response_buf_str)
    if host_remote_response == []:
        print("WARNING: host_remote_response ", host_to_remote_ipi_channel, "is missing property name: ", response_buf_str)
        return False
    host_remote_request = host_to_remote_ipi_channel.props(request_buf_str)
    if host_remote_request == []:
        print("WARNING: host_remote_request ", host_to_remote_ipi_channel, " is missing property name: ", request_buf_str)
        return False
    remote_host_response = remote_to_host_ipi_channel.props(response_buf_str)
    if remote_host_response == []:
        print("WARNING: remote_host_response ", remote_to_host_ipi_channel, " is missing property name: ", response_buf_str)
        return False
    remote_host_request = remote_to_host_ipi_channel.props(request_buf_str)
    if remote_host_request == []:
        print("WARNING: remote_host_request ", remote_to_host_ipi_channel, " is missing property name: ", request_buf_str)
        return False

    host_remote_response = host_remote_response[0].value[0]
    host_remote_request = host_remote_request[0].value[0]
    remote_host_response = remote_host_response[0].value[0]
    remote_host_request = remote_host_request[0].value[0]

    remote_controller_node + LopperProp(name="reg", value =[ host_remote_request, 0x20,
                                                         host_remote_response, 0x20,
                                                         remote_host_request, 0x20,
                                                         remote_host_response, 0x20])

    core_node + LopperProp(name="mboxes", value = [remote_controller_node.phandle, 0, remote_controller_node.phandle, 1])
    return True

def xlnx_rpmsg_kernel_update_ipis(tree, host_ipi, remote_ipi, gic_node_phandle,
                                  core_node, openamp_channel_info, channel_id):
    controller_parent = xlnx_rpmsg_create_host_controller(tree, host_ipi, gic_node_phandle)

    # check if there is a userspace IPI that has same interrupts
    # if so then error out
    for n in tree["/"].subnodes():
        compat_str = n.props("compatible")
        if compat_str != [] and compat_str[0].value == "ipi_uio" and \
            n.props("interrupts")[0].value[1] ==  host_ipi.props("interrupts")[0].value[1]:
            print("WARNING:  conflicting userspace and kernelspace for host ipi: ", host_ipi)
            return False

    if controller_parent == False:
        return False

    rpu_core = openamp_channel_info["rpu_core" + channel_id]
    remote_controller_node = LopperNode(-1, controller_parent.abs_path + "/ipi_mailbox_rpu" + str(rpu_core.value))
    tree.add(remote_controller_node)
    tree.resolve()

    if remote_controller_node.phandle == 0:
        remote_controller_node.phandle = remote_controller_node.phandle_or_create()


    return xlnx_rpmsg_kernel_update_mboxes(tree, host_ipi, remote_ipi, gic_node_phandle,
                                           openamp_channel_info, channel_id,
                                           core_node, remote_controller_node, rpu_core)


amba_ipi_node_index = 0
def xlnx_rpmsg_update_ipis(tree, host_ipi, remote_ipi, core_node, channel_id, openamp_channel_info, verbose = 0 ):
    global amba_ipi_node_index
    native = openamp_channel_info["rpmsg_native_"+ channel_id]
    platform = openamp_channel_info["platform"]
    controller_parent = None
    amba_node = None
    gic_node_phandle = None

    if platform == SOC_TYPE.VERSAL:
        gic_node_phandle = tree["/amba_apu/interrupt-controller@f9000000"].phandle
    elif platform == SOC_TYPE.ZYNQMP:
        gic_node_phandle = tree["/amba_apu/interrupt-controller@f9010000"].phandle
    else:
        print("invalid platform")
        return False

    if native:
        return xlnx_rpmsg_native_update_ipis(tree, amba_node, openamp_channel_info, gic_node_phandle,
                                             amba_ipi_node_index, host_ipi)
    else:
        return xlnx_rpmsg_kernel_update_ipis(tree, host_ipi, remote_ipi, gic_node_phandle,
                                             core_node, openamp_channel_info, channel_id)


def xlnx_rpmsg_update_tree(tree, node, channel_id, openamp_channel_info, verbose = 0 ):
    cpu_config =  openamp_channel_info["cpu_config"+channel_id]
    carveouts_nodes = openamp_channel_info["carveouts_"+ channel_id]
    host_ipi = openamp_channel_info["host_ipi_"+ channel_id]
    remote_ipi = openamp_channel_info["remote_ipi_"+ channel_id]
    native = openamp_channel_info["rpmsg_native_"+ channel_id]
    amba_node = None
    elfload_node = None
    if native:
        amba_node = openamp_channel_info["amba_node"]
    rpu_core = openamp_channel_info["rpu_core" + channel_id]
    rpmsg_carveouts = []


    # if Amba node exists, then this is for RPMsg native.
    # in this case find elfload node in case of native RPMsg as it may be contiguous
    # for AMBA Shm Node
    if native:
        for node in openamp_channel_info["elfload"+ channel_id]:
            if node.props("start") != []:
                elfload_node = node

    ret = xlnx_rpmsg_construct_carveouts(tree, carveouts_nodes, rpmsg_carveouts, native,
                                         amba_node=amba_node, elfload_node=elfload_node, verbose=verbose)
    if ret == False:
        return ret

    core_node = tree["/rf5ss@ff9a0000/r5f_" + str(rpu_core.value)]
    mem_region_prop = core_node.props("memory-region")[0]

    # add rpmsg carveouts to cluster core node if using rpmsg kernel driver
    new_mem_region_prop_val = mem_region_prop.value
    if not native:
        for rc in rpmsg_carveouts:
            new_mem_region_prop_val.append(rc.phandle)
        # update property with new values
        mem_region_prop.value = new_mem_region_prop_val

    ret = xlnx_rpmsg_update_ipis(tree, host_ipi, remote_ipi, core_node, channel_id, openamp_channel_info, verbose)
    if ret != True:
        return False

    tree - host_ipi
    tree - remote_ipi

    return True


channel_index = 0
def xlnx_construct_text_file(openamp_channel_info, channel_id, verbose = 0 ):
    global channel_index
    text_file_contents = ""
    rpmsg_native = openamp_channel_info["rpmsg_native_"+channel_id]
    carveouts = openamp_channel_info["carveouts_"+channel_id]
    elfload = openamp_channel_info["elfload"+channel_id]
    tx = None
    rx = None

    for c in carveouts:
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

        text_file_contents += "CHANNEL"+str(channel_index)+name+"BASE=\""+base+"\"\n"
        text_file_contents += "CHANNEL"+str(channel_index)+name+"SIZE=\""+size+"\"\n"

    if not rpmsg_native:
        tx = "FW_RSC_U32_ADDR_ANY"
        rx = "FW_RSC_U32_ADDR_ANY"

    elfbase = None
    elfsize = None
    for e in elfload:
        if e.props("start") != []: # filter to only parse ELF LOAD node
            elfbase = hex(e.props("start")[0].value)
            elfsize = hex(e.props("size")[0].value)
            break

    host_ipi = openamp_channel_info["host_ipi_" + channel_id]
    host_ipi_bitmask = hex(host_ipi.props("xlnx,ipi-bitmask")[0].value[0])
    host_ipi_irq_vect_id = hex(openamp_channel_info["host_ipi_irq_vect_id" + channel_id])
    host_ipi_base = hex(openamp_channel_info["host_ipi_base"+channel_id])

    remote_ipi = openamp_channel_info["remote_ipi_" + channel_id]
    remote_ipi_bitmask = hex(remote_ipi.props("xlnx,ipi-bitmask")[0].value[0])
    remote_ipi_irq_vect_id = hex(openamp_channel_info["remote_ipi_irq_vect_id" + channel_id])
    remote_ipi_base = hex(openamp_channel_info["remote_ipi_base"+channel_id])

    text_file_contents += "CHANNEL"+str(channel_index)+name+"RX=\""+rx+"\"\n"
    text_file_contents += "CHANNEL"+str(channel_index)+name+"TX=\""+tx+"\"\n"
    text_file_contents += "CHANNEL"+str(channel_index)+"ELFBASE=\""+elfbase+"\"\n"
    text_file_contents += "CHANNEL"+str(channel_index)+"ELFSIZE=\""+elfsize+"\"\n"
    text_file_contents += "CHANNEL"+str(channel_index)+"TO_HOST=\""+ host_ipi_base  +"\"\n"
    text_file_contents += "CHANNEL"+str(channel_index)+"TO_HOST-BITMASK=\"" + host_ipi_bitmask + "\"\n"
    text_file_contents += "CHANNEL"+str(channel_index)+"TO_HOST-IPIIRQVECTID=\"" + host_ipi_irq_vect_id + "\"\n"
    text_file_contents += "CHANNEL"+str(channel_index)+"TO_REMOTE=\""+ remote_ipi_base  +"\"\n"
    text_file_contents += "CHANNEL"+str(channel_index)+"TO_REMOTE-BITMASK=\"" + remote_ipi_bitmask + "\"\n"
    text_file_contents += "CHANNEL"+str(channel_index)+"TO_REMOTE-IPIIRQVECTID=\"" + remote_ipi_irq_vect_id + "\"\n"

    f = open(output_file, "w")
    f.write(text_file_contents)
    f.close()

    channel_index += 1
    return True


def xlnx_rpmsg_parse(tree, node, openamp_channel_info, verbose = 0 ):
    # Xilinx OpenAMP subroutine to collect RPMsg information from RPMsg
    # relation
    remote = node.props("remote")
    carveouts_nodes = []
    carveouts_prop = node.props("carveouts")
    amba_node = None

    # skip rpmsg remote node which will link to its host via 'host' property
    if node.props("host") != []:
        return True

    if remote == []:
        print("WARNING: ", node, "is missing remote property")
        return False

    remote_node = tree.pnode( remote[0].value[0] )
    channel_id = "_"+node.parent.parent.name+"_"+remote_node.name


    if carveouts_prop == []:
        print("WARNING: ", node, " is missing elfload property")
        return False
    else:
        carveouts_prop = carveouts_prop[0].value

    for p in carveouts_prop:
        carveouts_nodes.append ( tree.pnode(p) )

    openamp_channel_info["carveouts_"+channel_id] = carveouts_nodes

    # rpmsg native?
    native = node.props("openamp-xlnx-native")
    if native != [] and native[0].value[0] == 1:
        native = True
        # if native is true, then find and store amba bus
        # to store IPI and SHM nodes
        try:
            amba_node = tree["/amba"]
        except:
            amba_node = LopperNode(-1, "/amba")
            amba_node + LopperProp(name="u-boot,dm-pre-reloc")
            amba_node + LopperProp(name="ranges")
            amba_node + LopperProp(name="#address-cells", value = 2)
            amba_node + LopperProp(name="#size-cells", value = 2)
            tree.add(amba_node)
            tree.resolve()

        openamp_channel_info["amba_node"] = amba_node
    else:
        native = False

    ret = xlnx_rpmsg_ipi_parse(tree, node, openamp_channel_info,
                         remote_node, channel_id, native, verbose)
    if ret != True:
        return False

    ret = xlnx_rpmsg_update_tree(tree, node, channel_id, openamp_channel_info, verbose )
    if ret != True:
        return False

    # generate text file
    ret = xlnx_construct_text_file(openamp_channel_info, channel_id, verbose)
    if not ret:
        return ret

    # remove definitions
    defn_node =  tree["/definitions"]
    tree - defn_node

    return True


def determine_cpus_config(remote_domain):
  cpus_prop_val = remote_domain.propval("cpus")
  cpu_config = None # split or lockstep

  if cpus_prop_val != ['']:
    if len(cpus_prop_val) != 3:
      print("rpu cluster cpu prop invalid len")
      return -1
    cpu_config = CPU_CONFIG.RPU_LOCKSTEP if  check_bit_set(cpus_prop_val[2], 30)==True else CPU_CONFIG.RPU_SPLIT

  return cpu_config


def determinte_rpu_core(cpu_config, remote_node, remote_prop):
    remote_cpus = remote_node.props("cpus")[0]

    if cpu_config == CPU_CONFIG.RPU_LOCKSTEP:
        return RPU_CORE.RPU_0
    elif cpu_config == CPU_CONFIG.RPU_SPLIT:
        if remote_cpus[1] == 0x1:
            return RPU_CORE.RPU_0
        elif remote_cpus[1] == 0x2:
            return RPU_CORE.RPU_1
        else:
            print("WARNING: invalid cpus for ", remote_node)
            return False
    else:
        print("WARNING: invalid cpu config: ", cpu_config)
        return False


def xlnx_remoteproc_construct_carveouts(tree, carveouts, new_ddr_nodes, verbose = 0 ):
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

            phandle_val = new_node.phandle_or_create()

            new_node + LopperProp(name="phandle", value = phandle_val)
            new_node.phandle = phandle_val
            new_ddr_nodes.append(new_node.phandle)
        else:
            print("WARNING: invalid remoteproc elfload carveout", carveout)
            return False

    return True


def xlnx_remoteproc_construct_cluster(tree, cpu_config, elfload_nodes, new_ddr_nodes, host_node, remote_node, openamp_channel_info, verbose = 0):
    channel_id = "_"+host_node.name+"_"+remote_node.name
    cluster_node = None
    if cpu_config in [ CPU_CONFIG.RPU_LOCKSTEP, CPU_CONFIG.RPU_SPLIT ]:
        try:
            cluster_node = tree["/rf5ss@ff9a0000"]
        except KeyError:
            cluster_node = LopperNode(-1, "/rf5ss@ff9a0000")
            cluster_node + LopperProp(name="compatible", value = "xlnx,zynqmp-r5-remoteproc")
            cluster_node + LopperProp(name="#address-cells", value = 0x2)
            cluster_node + LopperProp(name="#size-cells", value = 0x2)
            cluster_node + LopperProp(name="ranges", value= [])
            cluster_node + LopperProp(name="xlnx,cluster-mode", value = cpu_config.value)
            cluster_node + LopperProp(name="reg", value = [0, 0xff9a0000, 0, 0x10000])
            tree.add(cluster_node)

        rpu_core = openamp_channel_info["rpu_core"+channel_id]
        rpu_core_pd_prop = openamp_channel_info["rpu_core_pd_prop"+channel_id]

        core_node = LopperNode(-1, "/rf5ss@ff9a0000/r5f_" + str(rpu_core.value))
        core_node + LopperProp(name="compatible", value = "xilinx,r5f")
        core_node + LopperProp(name="#address-cells", value = 2)
        core_node + LopperProp(name="#size-cells", value = 2)
        core_node + LopperProp(name="ranges", value=[])
        core_node + LopperProp(name="power-domain", value=copy.deepcopy(rpu_core_pd_prop.value))
        core_node + LopperProp(name="mbox-names", value = ["tx", "rx"]);

        tree.add(core_node)

        srams = []
        memory_region = []
        for carveout in elfload_nodes:
            if carveout.props("status") != []:
                srams.append(carveout.phandle)

        # there may be new nodes created in linux reserved-memory node to account for
        for phandle_val in new_ddr_nodes:
            memory_region.append(phandle_val)

        core_node + LopperProp(name="memory-region", value=memory_region)
        core_node + LopperProp(name="sram", value=srams)

    else:
        return False

    return True

def xlnx_remoteproc_update_tree(tree, node, remote_node, openamp_channel_info, verbose = 0 ):
    channel_id = "_"+node.parent.parent.name+"_"+remote_node.name
    cpu_config =  openamp_channel_info["cpu_config"+channel_id]
    elfload_nodes = openamp_channel_info["elfload"+channel_id]
    new_ddr_nodes = []

    ret = xlnx_remoteproc_construct_carveouts(tree, elfload_nodes, new_ddr_nodes, verbose)
    if ret == False:
        return ret
    ret = xlnx_remoteproc_construct_cluster(tree, cpu_config, elfload_nodes, new_ddr_nodes, node.parent.parent, remote_node, openamp_channel_info, verbose = 0)
    if ret == False:
        return ret

    return True


def xlnx_remoteproc_parse(tree, node, openamp_channel_info, verbose = 0 ):
    # Xilinx OpenAMP subroutine to collect RPMsg information from Remoteproc
    # relation
    remote = node.props("remote")
    elfload_nodes = []
    elfload_prop = node.props("elfload")

    if remote == []:
        print("WARNING: ", node, "is missing remote property")
        return False

    remote_node = tree.pnode( remote[0].value[0] )
    cpu_config = determine_cpus_config(remote_node)

    if cpu_config in [ CPU_CONFIG.RPU_LOCKSTEP, CPU_CONFIG.RPU_SPLIT]:
        rpu_core = determinte_rpu_core(cpu_config, remote_node, remote[0] )
        if not rpu_core:
            return False
    else:
        return False

    if elfload_prop == []:
        print("WARNING: ", node, " is missing elfload property")
        return False
    else:
        elfload_prop = elfload_prop[0].value

    for p in elfload_prop:
        elfload_nodes.append ( tree.pnode(p) )

    rpu_cluster_node = tree.pnode(remote_node.props("cpus")[0].value[0])
    rpu_core_node = rpu_cluster_node.abs_path + "/cpu@"

    if CPU_CONFIG.RPU_SPLIT == cpu_config and RPU_CORE.RPU_1 == rpu_core:
        rpu_core_node = tree[rpu_core_node+"1"]
    elif CPU_CONFIG.RPU_LOCKSTEP == cpu_config:
        rpu_core_node = tree[rpu_core_node+"0"]
    elif CPU_CONFIG.RPU_SPLIT == cpu_config and RPU_CORE.RPU_0 == rpu_core:
        rpu_core_node = tree[rpu_core_node+"0"]
    else:
        print("WARNING invalid RPU and CPU config for relation. cpu: ", cpu_config, " rpu: ", rpu_core)
        return False

    if rpu_core_node.props("power-domains") == []:
        print("WARNING: RPU core does not have power-domains property.")
        return False

    rpu_core_pd_prop = rpu_core_node.props("power-domains")[0]

    channel_id = "_"+node.parent.parent.name+"_"+remote_node.name
    openamp_channel_info["elfload"+channel_id] = elfload_nodes
    openamp_channel_info["rpu_core_pd_prop"+channel_id] = rpu_core_pd_prop
    openamp_channel_info["cpu_config"+channel_id] = cpu_config
    openamp_channel_info["rpu_core"+channel_id] = rpu_core

    return xlnx_remoteproc_update_tree(tree, node, remote_node, openamp_channel_info, verbose = 0 )


def xlnx_openamp_parse(sdt, verbose = 0 ):
    # Xilinx OpenAMP subroutine to parse OpenAMP Channel
    # information and generate Device Tree information.
    tree = sdt.tree
    ret = -1
    openamp_channel_info = {}

    for n in tree["/domains"].subnodes():
            node_compat = n.props("compatible")
            if node_compat != []:
                node_compat = node_compat[0].value

                if node_compat == REMOTEPROC_D_TO_D:
                    ret = xlnx_remoteproc_parse(tree, n, openamp_channel_info, verbose)
                elif node_compat == RPMSG_D_TO_D:
                    ret = xlnx_rpmsg_parse(tree, n, openamp_channel_info, verbose)

                if ret == False:
                    return ret

    return True

def xlnx_openamp_rpmsg_expand(tree, subnode, verbose = 0 ):
    # Xilinx-specific YAML expansion of RPMsg description.
    ret = resolve_host_remote( tree, subnode, verbose)
    if ret == False:
        return ret
    ret = resolve_rpmsg_mbox( tree, subnode, verbose)
    if ret == False:
        return ret
    ret = resolve_rpmsg_carveouts( tree, subnode, verbose)
    if ret == False:
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


def update_mbox_cntr_intr_parent(sdt):
  # find phandle of a72 gic for mailbox controller
  a72_gic_node = sdt.tree["/amba_apu/interrupt-controller@f9000000"]
  # set mailbox controller interrupt-parent to this phandle
  mailbox_cntr_node = sdt.tree["/zynqmp_ipi1"]
  mailbox_cntr_node["interrupt-parent"].value = a72_gic_node.phandle
  sdt.tree.sync()
  sdt.tree.resolve()


# in this case remote is rpu
# find node that is other end of openamp channel
def find_remote(sdt, domain_node, rsc_group_node):
  domains = sdt.tree["/domains"]
  # find other domain including the same resource group
  remote_domain = None
  for node in domains.subnodes():
    # look for other domains with include
    if node.propval("include") != [''] and node != domain_node:
      # if node includes same rsc group, then this is remote
      for i in node.propval("include"):
        included_node = sdt.tree.pnode(i)
        if included_node != None and included_node == rsc_group_node:
           return node

  return -1

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False

# return rpu cluster configuration
# rpu cpus property fields: Cluster | cpus-mask | execution-mode
#
#execution mode ARM-R CPUs:
#bit 30: lockstep (lockstep enabled == 1)
#bit 31: secure mode / normal mode (secure mode == 1)
# e.g. &cpus_r5 0x2 0x80000000>
# this maps to arg1 as rpu_cluster node
# arg2: cpus-mask: 0x2 is r5-1, 0x1 is r5-0, 0x3 is both nodes
#        if 0x3/both nodes and in split then need to openamp channels provided,
#        otherwise return error
#        if lockstep valid cpus-mask is 0x3 needed to denote both being used
#  
def construct_carveouts(sdt, rsc_group_node, core, openamp_app_inputs):
  # static var that persists beyond lifetime of first function call
  # this is needed as there may be more than 1 openamp channel
  # so multiple carveouts' phandles are required
  if not hasattr(construct_carveouts,"carveout_phandle"):
    # it doesn't exist yet, so initialize it
    construct_carveouts.carveout_phandle = 0x5ed0

  # carveouts each have addr,range
  mem_regions = [[0 for x in range(2)] for y in range(4)] 
  mem_region_names = {
    0 : "elfload",
    1 : "vdev0vring0",
    2 : "vdev0vring1",
    3 : "vdev0buffer",
  }
  carveout_phandle_list = []

  for index,value in enumerate(rsc_group_node["memory"].value):
    if index % 2 == 1:
      continue

    region_name = mem_region_names[index/2]

    name = "rpu"+str(core)+region_name
    addr = value
    length = rsc_group_node["memory"].value[index + 1]

    openamp_app_inputs[rsc_group_node.name + region_name + '_base'] = hex(value)
    openamp_app_inputs[rsc_group_node.name + region_name + '_size'] = hex(length)

    new_node = LopperNode(-1, "/reserved-memory/"+name)
    new_node + LopperProp(name="no-map", value=[])
    new_node + LopperProp(name="reg",value=[0,addr,0,length])
    new_node + LopperProp(name="phandle",value=construct_carveouts.carveout_phandle)
    new_node.phandle = new_node

    sdt.tree.add(new_node)
    print("added node: ",new_node)

    carveout_phandle_list.append(construct_carveouts.carveout_phandle)
    construct_carveouts.carveout_phandle += 1

  return carveout_phandle_list

def construct_mem_region(sdt, domain_node, rsc_group_node, core, openamp_app_inputs):
  # add reserved mem if not present
  res_mem_node = None
  carveout_phandle_list = None
  try:
    res_mem_node = sdt.tree["/reserved-memory"]
    print("found pre-existing reserved mem node")
  except:
    res_mem_node = LopperNode(-1, "/reserved-memory")
    res_mem_node + LopperProp(name="#address-cells",value=2)
    res_mem_node + LopperProp(name="#size-cells",value=2)
    res_mem_node + LopperProp(name="ranges",value=[])

    sdt.tree.add(res_mem_node)
    print("added reserved mem node ", res_mem_node)

  return construct_carveouts(sdt, rsc_group_node, core, openamp_app_inputs)


# set pnode id for current rpu node
def set_rpu_pnode(sdt, r5_node, rpu_config, core, platform, remote_domain):
  if r5_node.propval("power-domain") != ['']:
    print("pnode id already exists for node ", r5_node)
    return -1

  rpu_pnodes = {}
  if platform == SOC_TYPE.VERSAL:
    rpu_pnodes = {0 : 0x18110005, 1: 0x18110006}
  elif platform == SOC_TYPE.ZYNQMP:
    rpu_pnodes = {0 : 0x7, 1: 0x8}
  else:
    print("only versal supported for openamp domains")
    return -1
  rpu_pnode = None
  # rpu config : true is split
  if rpu_config == "lockstep":
    rpu_pnode = rpu_pnodes[0]
  else:
     rpu_pnode = rpu_pnodes[core]

  r5_node + LopperProp(name="power-domain", value = rpu_pnodes[core])
  r5_node.sync(sdt.FDT)

  return

def setup_mbox_info(sdt, domain_node, r5_node, mbox_ctr):
  mbox_ctr.phandle = sdt.tree.phandle_gen()

  if mbox_ctr.propval("reg-names") == [''] or mbox_ctr.propval("xlnx,ipi-id") == ['']:
    print("invalid mbox ctr")
    return -1
  mbox_ctr_phandle = mbox_ctr.propval("phandle")
  r5_node + LopperProp(name="mboxes",value=[mbox_ctr_phandle,0,mbox_ctr_phandle,1])
  r5_node + LopperProp(name="mbox-names", value = ["tx", "rx"]);
  sdt.tree.sync()
  r5_node.sync(sdt.FDT)
  return
  
# based on rpu_cluster_config + cores determine which tcm nodes to use
# add tcm nodes to device tree
def setup_tcm_nodes(sdt, r5_node, platform, rsc_group_node):
  # determine which tcm nodes to use based on access list in rsc group
  for i in rsc_group_node["access"].value:
      tcm_node = sdt.tree.pnode(i)
      print(tcm_node)
      if tcm_node != None and tcm_node.propval("phandle") == ['']:
          tcm_node + LopperProp( name="phandle", value = tcm_node.phandle )
  r5_node + LopperProp(name="sram", value = rsc_group_node["access"].value.copy() )

  return 0

def setup_r5_core_node(rpu_config, sdt, domain_node, rsc_group_node, core, remoteproc_node, platform, remote_domain, mbox_ctr, openamp_app_inputs):
  carveout_phandle_list = None
  r5_node = None
  # add r5 node if not present
  try:
    r5_node = sdt.tree["/rpu@ff9a0000/r5_"+str(core)]
    print("node already exists: ", r5_node)
  except:
    r5_node = LopperNode(-1, "/rpu@ff9a0000/r5_"+str(core))
    r5_node + LopperProp(name="#address-cells",value=2)
    r5_node + LopperProp(name="#size-cells",value=2)
    r5_node + LopperProp(name="ranges",value=[])
    r5_node + LopperProp(name="compatible",value="xilinx,r5f")
    sdt.tree.add(r5_node)
    print("added r5 node ", r5_node)
    print("add props for ",str(r5_node))
  # props
  ret = set_rpu_pnode(sdt, r5_node, rpu_config, core, platform, remote_domain)
  if ret == -1:
    print("set_rpu_pnode failed")
    return ret
  ret = setup_mbox_info(sdt, domain_node, r5_node, mbox_ctr)
  if ret == -1:
    print("setup_mbox_info failed")
    return ret

  carveout_phandle_list = construct_mem_region(sdt, domain_node, rsc_group_node, core, openamp_app_inputs)
  if carveout_phandle_list == -1:
    print("construct_mem_region failed")
    return ret

  if carveout_phandle_list != None:
    print("adding prop memory-region to ",r5_node)
    r5_node + LopperProp(name="memory-region",value=carveout_phandle_list)

  #tcm nodes
  for i in r5_node.subnodes():
    if "tcm" in i.abs_path:
      "tcm nodes exist"
      return -1

  # tcm nodes do not exist. set them up
  setup_tcm_nodes(sdt, r5_node, platform, rsc_group_node)
           
# add props to remoteproc node
def set_remoteproc_node(remoteproc_node, sdt, rpu_config):
  props = []
  props.append(LopperProp(name="reg", value =   [0x0, 0xff9a0000, 0x0, 0x10000]))
  props.append(LopperProp(name="#address-cells",value=2))
  props.append(LopperProp(name="ranges",value=[]))
  props.append(LopperProp(name="#size-cells",value=2))
  if rpu_config == "split":
      rpu_config = 0x1
  else:
      rpu_config = 0x0
  props.append(LopperProp(name="xlnx,cluster-mode",value=rpu_config))
  props.append(LopperProp(name="compatible",value="xlnx,zynqmp-r5-remoteproc"))
  for i in props:
    remoteproc_node + i


#core = []
# this should only add nodes  to tree
# openamp_app_inputs: dictionary to fill with openamp header info for openamp code base later on
def construct_remoteproc_node(remote_domain, rsc_group_node, sdt, domain_node,  platform, mbox_ctr, openamp_app_inputs):
  rpu_config = None # split or lockstep
  core = 0

  [core, rpu_config] = determine_core(remote_domain)



  # only add remoteproc node if mbox is present in access list of domain node
  # check domain's access list for mbox
  has_corresponding_mbox = False
  if domain_node.propval("access") != ['']:
    for i in domain_node.propval("access"):
      possible_mbox = sdt.tree.pnode(i)
      if possible_mbox != None:
        if possible_mbox.propval("reg-names") != ['']:
          has_corresponding_mbox = True

  # setup remoteproc node if not already present
  remoteproc_node = None
  try:
    remoteproc_node = sdt.tree["/rpu@ff9a0000"]
  except:
    print("remoteproc node not present. now add it to tree")
    remoteproc_node = LopperNode(-1, "/rpu@ff9a0000")
    set_remoteproc_node(remoteproc_node, sdt, rpu_config)
    sdt.tree.add(remoteproc_node, dont_sync = True)
    remoteproc_node.sync(sdt.FDT)
    remoteproc_node.resolve_all_refs()
    sdt.tree.sync()

  return setup_r5_core_node(rpu_config, sdt, domain_node, rsc_group_node, core, remoteproc_node, platform, remote_domain, mbox_ctr, openamp_app_inputs)


def validate_ipi_node(ipi_node, platform):
    if ipi_node == None:
        print("invalid "+role+" IPI - invalid phandle from access property.")
        return False

    if 'xlnx,zynqmp-ipi-mailbox' not in ipi_node.propval("compatible"):
        print("invalid "+role+" IPI - wrong compatible string")
        return False

    ipi_base_addr = ipi_node.propval("reg")
    if len(ipi_base_addr) != 4:
        print("invalid "+role+" IPI - incorrect reg property of ipi", ipi_node)
        return False

    if platform == SOC_TYPE.VERSAL:
        if ipi_base_addr[1] not in openamp_supported_ipis:
            print(hex(ipi_base_addr[1]), "not supported")
            return False
    elif platform == SOC_TYPE.ZYNQMP:
        if ipi_base_addr[1] in [0xFF330000, 0xFF331000, 0xFF332000, 0xFF333000]:
            print("do not use PMU IPIs in OpenAMP Overlay")
            return False
    else:
        print("unsupported platform: ",platform)

    return True


def parse_ipi_info(sdt, domain_node, remote_domain, current_rsc_group, openamp_app_inputs, platform):
    host_ipi_node = None
    remote_ipi_node = None
    domains_to_process = {
        'host': domain_node,
        'remote' : remote_domain,
    }

    for role in domains_to_process.keys():
        domain = domains_to_process[role]

        access_pval = domain.propval("access")
        if len(access_pval) == 0:
            print("invalid "+role+" IPI - no access property")
            return False
        ipi_node = sdt.tree.pnode(access_pval[0])
        if validate_ipi_node(ipi_node, platform) != True:
            print("parse_ipi_info: invalid IPI node.")
            return False
        ipi_base_addr = ipi_node.propval("reg")[1]
        prefix = current_rsc_group.name + '-' + role + '-'
        openamp_app_inputs[prefix+'ipi'] = hex(ipi_base_addr)

        if platform == SOC_TYPE.VERSAL:
            agent = ipi_to_agent[ipi_base_addr]
            bitmask = agent_to_ipi_bitmask[agent]
            openamp_app_inputs[prefix+'bitmask'] = hex(agent_to_ipi_bitmask[agent])
            openamp_app_inputs[prefix+'ipi-irq-vect-id'] = ipi_to_irq_vect_id[ipi_base_addr]

        elif platform == SOC_TYPE.ZYNQMP:
            bitmask = ipi_node.propval("xlnx,ipi-bitmask")
            if bitmask == ['']:
                print("no bitmask for IPI node: ", ipi_node)
                return False
            interrupts = ipi_node.propval("interrupts")
            if len(interrupts) != 3:
                print("invalid interrupts for IPI node ", ipi_node)
                return False

            openamp_app_inputs[prefix+'bitmask'] = hex(bitmask[0])
            # system interrupts are the GIC val + 32
            openamp_app_inputs[prefix+'ipi-irq-vect-id'] = hex(interrupts[1] + 32)

        else:
            print("unsupported platform: ",platform)


def construct_mbox_ctr_reg(sdt, host_ipi, remote_ipi, openamp_app_inputs, platform, group):
    reg_vals = []
    if platform == SOC_TYPE.VERSAL:
        remote_ipi = int(openamp_app_inputs[group.name+'-'+'remote'+'-ipi'], 16)
        host_ipi   = int(openamp_app_inputs[group.name+'-'+  'host'+'-ipi'], 16)

        remote_agent = ipi_to_agent[remote_ipi]
        host_agent = ipi_to_agent[host_ipi]

        remote_offset = ipi_msg_buf_dest_agent_request_offsets[remote_ipi]
        host_offset = ipi_msg_buf_dest_agent_request_offsets[host_ipi]

        ipi_msg_buf_base = 0xff3f0000
        response_offset = 0x20

        local_request_region = ipi_msg_buf_base | host_agent | remote_offset
        remote_request_region = ipi_msg_buf_base | remote_agent | host_offset

        vals = [
            local_request_region,
            local_request_region | response_offset,
            remote_request_region,
            remote_request_region | response_offset
        ]

        for i in vals:
            reg_vals.append(0x0)
            reg_vals.append(i)
            reg_vals.append(0x0)
            reg_vals.append(0x20)

    elif platform == SOC_TYPE.ZYNQMP:
        # for host and remote IPI
        # find its corresponding IPI ID mapping in the other IPI
        # store its response and request regions
        # return

        host_ipi_id = host_ipi.propval("xlnx,ipi-id")[0]
        remote_ipi_id = remote_ipi.propval("xlnx,ipi-id")[0]

        ipis = [ host_ipi, remote_ipi ]
        target_ipis = [remote_ipi_id , host_ipi_id ]

        match = 0
        for index,current_ipi in enumerate(ipis):
            for node in current_ipi.subnodes():
                subnode_ipi_id = node.propval("xlnx,ipi-id")
                if subnode_ipi_id != [''] and subnode_ipi_id[0] == target_ipis[index]:
                    match += 1
                    if node.propval("xlnx,ipi-message-buffer-response-region") == [''] or node.propval("xlnx,ipi-message-buffer-request-region") == ['']:
                        print("no IPI message buffers found for ZU+ SDT")
                        return None

                    prop_names = [ "xlnx,ipi-message-buffer-response-region", "xlnx,ipi-message-buffer-request-region" ]
                    for name in prop_names:
                        reg_vals.append(0x0)
                        reg_vals.append( node.propval( name )[0] )
                        reg_vals.append(0x0)
                        reg_vals.append(0x20)

            if match == 0:
                print("no IPI nodes found")
                return None

    else:
        print("construct_mbox_ctr_reg: unsupported platform: ", platform)
        return None

    return reg_vals


def construct_mbox_ctr(sdt, openamp_app_inputs, remote_domain, host_ipi, remote_ipi, platform):
    controller_parent = None
    try:
        controller_parent = sdt.tree["/zynqmp_ipi1"]
        print("zynqmp_ipi1 already present.")
    except:
        controller_parent = LopperNode(-1, "/zynqmp_ipi1")
        controller_parent + LopperProp(name="compatible",value="xlnx,zynqmp-ipi-mailbox")
        gic_node_phandle = None

        if platform == SOC_TYPE.VERSAL:
            gic_node_phandle = sdt.tree["/amba_apu/interrupt-controller@f9000000"].phandle
        elif platform == SOC_TYPE.ZYNQMP:

            gic_node_phandle = sdt.tree["/amba_apu/interrupt-controller@f9010000"].phandle
        else:
            print("invalid platform for construct_mbox_ctr")
            return False

        controller_parent + LopperProp(name="interrupt-parent", value = [gic_node_phandle])
        controller_parent + LopperProp(name="interrupts",value= host_ipi.propval("interrupts").copy()   )
        controller_parent + LopperProp(name="xlnx,ipi-id",value= host_ipi.propval("xlnx,ipi-id")[0]  )
        controller_parent + LopperProp(name="#address-cells",value=2)
        controller_parent + LopperProp(name="#size-cells",value=2)
        controller_parent + LopperProp(name="ranges")
        controller_parent + LopperProp(name="phandle",value=sdt.tree.phandle_gen())
        sdt.tree.add(controller_parent)
        print("added node ",controller_parent)


    # for each channel, add agent info to zynqmp_ipi1
    # find resource group per channel
    # map group to host + remote ipi info
    controller_idx = 0
    for key in openamp_app_inputs.keys():
        if '_to_group' in key:
            group_to_channel_record = openamp_app_inputs[key]
            group_name = group_to_channel_record.split('-to-')[1]
            group = sdt.tree["/domains/"+group_name]
            host_prefix = group.name + '-host-'
            remote_prefix = group.name + '-remote-'
            controller_node = LopperNode(-1, "/zynqmp_ipi1/controller" + str(controller_idx))
            controller_node + LopperProp(name="reg-names",value=["local_request_region", "local_response_region", "remote_request_region", "remote_response_region"])
            controller_node + LopperProp(name="#mbox-cells",value=1)
            controller_node + LopperProp(name="phandle",value=sdt.tree.phandle_gen()+1)

            # construct host mbox ctr xlnx,ipi-id from remote's ipi
            access_pval = remote_domain.propval("access")
            if len(access_pval) == 0:
                print("invalid remote IPI - no access property")
                return False
            ipi_node = sdt.tree.pnode(access_pval[0])

            if validate_ipi_node(ipi_node, platform) != True:
                print("IPI node is invalid")
                return False

            remote_ipi_id_val = ipi_node.propval('xlnx,ipi-id')
            controller_node + LopperProp(name="xlnx,ipi-id",value=remote_ipi_id_val[0])
            reg_vals = construct_mbox_ctr_reg(sdt, host_ipi, remote_ipi, openamp_app_inputs, platform, group)
            controller_node + LopperProp(name="reg",value=reg_vals)

            sdt.tree.add(controller_node)
            controller_idx += 1
            print("added mailbox controller node ",controller_node)

    # if needed, will have to remove the existing mailbox
    for i in sdt.tree["/amba"].subnodes():
        if i.propval("compatible") == ['xlnx,zynqmp-ipi-mailbox'] and i.propval('xlnx,ipi-bitmask') != ['']:
            i["status"].value = "disabled"


def setup_userspace_nodes(sdt, domain_node, current_rsc_group, remote_domain, openamp_app_inputs, platform):
    [core, rpu_config] = determine_core(remote_domain)
    construct_mem_region(sdt, domain_node, current_rsc_group, core, openamp_app_inputs)
    base = int(openamp_app_inputs[current_rsc_group.name+'elfload_base'],16)
    end_base = int(openamp_app_inputs[current_rsc_group.name+'vdev0buffer_base'],16)
    end_size = int(openamp_app_inputs[current_rsc_group.name+'vdev0buffer_size'],16)

    carveout_size = end_base - base + end_size

    amba_node = None
    try:
        amba_node = sdt.tree["/amba"]
    except:
        amba_node = LopperNode(-1,"/amba")
        sdt.tree.add(amba_node)

    carveout_node = LopperNode(-1, "/amba/shm@0")
    carveout_node + LopperProp(name="compatible",value="none")
    carveout_node + LopperProp(name="reg",value=[0x0, base, 0x0, carveout_size])
    sdt.tree.add(carveout_node)

    host_ipi = int(openamp_app_inputs[current_rsc_group.name+'-host-ipi'],16)

    userspace_host_ipi_node = LopperNode(-1, "/amba/ipi@0")
    userspace_host_ipi_node + LopperProp(name="compatible",value="none")

    # construct host ipi interrupts property
    access_pval = domain_node.propval("access")
    if len(access_pval) == 0:
        print("invalid "+role+" IPI - no access property")
        return False
    ipi_node = sdt.tree.pnode(access_pval[0])
    if validate_ipi_node(ipi_node, platform) != True:
        return False
    host_ipi_interrupts_val = ipi_node.propval('interrupts')

    userspace_host_ipi_node + LopperProp(name="interrupts",value=host_ipi_interrupts_val)
    gic_path = ""
    if platform == SOC_TYPE.VERSAL:
        gic_path = "/amba_apu/interrupt-controller@f9000000"
    elif platform == SOC_TYPE.ZYNQMP:
        gic_path = "/amba_apu/interrupt-controller@f9010000"
    else:
        print("invalid platform for setup_userspace_nodes")
        return False

    userspace_host_ipi_node + LopperProp(name="interrupt-parent",value=[sdt.tree[gic_path].phandle])
    userspace_host_ipi_node + LopperProp(name="phandle",value=sdt.tree.phandle_gen())
    userspace_host_ipi_node + LopperProp(name="reg",value=[0x0, host_ipi , 0x0,  0x1000])
    sdt.tree.add(userspace_host_ipi_node)

    openamp_app_inputs[current_rsc_group.name+'-tx'] = openamp_app_inputs[current_rsc_group.name+'vdev0vring0_base']
    openamp_app_inputs[current_rsc_group.name+'-rx'] = openamp_app_inputs[current_rsc_group.name+'vdev0vring1_base']


def parse_openamp_domain(sdt, options, tgt_node):
  print("parse_openamp_domain")
  domain_node = sdt.tree[tgt_node]
  root_node = sdt.tree["/"]
  platform = SOC_TYPE.UNINITIALIZED
  openamp_app_inputs = {}
  kernelcase = False

  if 'versal' in str(root_node['compatible']):
      platform = SOC_TYPE.VERSAL
  elif 'zynqmp' in str(root_node['compatible']):
      platform = SOC_TYPE.ZYNQMP
  else:
      print("invalid input system DT")
      return False

  rsc_groups = determine_role(sdt, domain_node)
  if rsc_groups == -1:
    print("failed to find rsc_groups")
    return rsc_groups

  # if host, find corresponding remote
  # if none report error
  channel_idx = 0
  for current_rsc_group in rsc_groups:
    # each openamp channel's remote/slave should be different domain
    # the domain can be identified by its unique combination of domain that includes the same resource group as the
    # openamp remote domain in question
    remote_domain = find_remote(sdt, domain_node, current_rsc_group)
    if remote_domain == -1:
      print("failed to find_remote")
      return remote_domain

    # parse IPI base address, bitmask, vect ID, agent information
    parse_ipi_info(sdt, domain_node, remote_domain, current_rsc_group, openamp_app_inputs, platform)

    # determine if userspace or kernelspace flow
    print(current_rsc_group)
    if 'openamp-xlnx-kernel' in current_rsc_group.__props__:
        kernelcase = True

    openamp_app_inputs['channel'+ str(channel_idx)+  '_to_group'] = str(channel_idx) + '-to-' + current_rsc_group.name
    openamp_app_inputs[current_rsc_group.name] = channel_idx

    if kernelcase:
        host_ipi = sdt.tree.pnode(domain_node.propval("access")[0])
        remote_ipi = sdt.tree.pnode(remote_domain.propval("access")[0])
        construct_mbox_ctr(sdt, openamp_app_inputs, remote_domain, host_ipi, remote_ipi, platform)
        mbox_ctr = sdt.tree["/zynqmp_ipi1/controller"+str(channel_idx)]
        construct_remoteproc_node(remote_domain, current_rsc_group, sdt, domain_node,  platform, mbox_ctr, openamp_app_inputs)
        openamp_app_inputs[current_rsc_group.name+'-tx'] = 'FW_RSC_U32_ADDR_ANY'
        openamp_app_inputs[current_rsc_group.name+'-rx'] = 'FW_RSC_U32_ADDR_ANY'

    else:
        setup_userspace_nodes(sdt, domain_node, current_rsc_group, remote_domain, openamp_app_inputs, platform)

    # update channel for openamp group
    channel_idx += 1

  lines = ""
  for i in openamp_app_inputs.keys():
      lines += i.upper().replace('@','_') + "=\""

      val = openamp_app_inputs[i]
      if isinstance(val, int):
          lines += hex(openamp_app_inputs[i])
      else:
          lines += openamp_app_inputs[i]

      lines += "\"\n"

  with open('openamp-channel-info.txt', 'w') as the_file:
    the_file.write(lines)

  return True
