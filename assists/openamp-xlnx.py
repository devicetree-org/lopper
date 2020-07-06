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
from lopper_tree import *
from re import *
from openamp_xlnx_common import *

def get_r5_needed_symbols(carveout_list):
    rsc_mem_pa = -1
    shared_mem_size = -1
    for i in carveout_list:
        if "vdev0buffer" in i[0]:
            shared_mem_size = int(i[1][3],16)
        elif "elfload" in i[0] or "rproc" in i[0]:
            rsc_mem_pa =  int( i[1][1],16)+0x20000

    return [rsc_mem_pa, shared_mem_size]

# table relating ipi's to IPI_BASE_ADDR -> IPI_IRQ_VECT_ID and IPI_CHN_BITMASK
versal_ipi_lookup_table = { "0xff340000" : [63, 0x0000020 ] , "0xff360000" : [0 , 0x0000008] }
zynqmp_ipi_lookup_table = { "0xff310000" : [65, 0x1000000 ] , "0xff340000" : [0 , 0x100 ] }

def parse_ipis_for_rpu(sdt, domain_node, options):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    ipi_list = []
    for node in sdt.tree:
        if "ps_ipi" in node.abs_path:
            ipi_list.append(hex(int(node["reg"].value[1])))

    if verbose:
        print( "[INFO]: Dedicated IPIs for OpenAMP: %s" % ipi_list)

    return ipi_list

def is_compat( node, compat_string_to_test ):
    if re.search( "openamp,xlnx-rpu", compat_string_to_test):
        return xlnx_openamp_rpu
    return ""

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False

def setup_ipi_inputs(inputs, platform, ipi_list, options):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    # for each pair of ipi's present, write a master+remote ipi
    for index,value in enumerate(ipi_list):
        is_master = False
        key = ""
        if (index % 2 == 0):
            key = "MASTER_"
        else:
            key = "REMOTE_"
        inputs[key+"IPI_BASE_ADDR"] = value
        inputs[key+"IPI_NAME"] = '\"'+value.replace("0x","")+".ps_ipi\""

        try:
            ipi_details_list = None
            if platform == SOC_TYPE.VERSAL:
                ipi_details_list = versal_ipi_lookup_table[value]
            elif platform == SOC_TYPE.ZYNQMP:
                ipi_details_list = zynqmp_ipi_lookup_table[value]
            else:
                if verbose != 0:
                    print ("[WARNING]: invalid device tree. no valid platform found")
                    return

            inputs[key+"IRQ_VECT_ID"] = str(ipi_details_list[0])
            inputs[key+"CHN_BITMASK"] = str(hex(ipi_details_list[1]))+"U"

        except:
            if verbose != 0:
                print ("[WARNING]: unable to find detailed interrupt information for "+i)
                return
    return inputs


def handle_rpmsg_userspace_case(tgt_node, sdt, options, domain_node, memory_node, rpu_node, rsc_mem_pa, shared_mem_size, platform):
    if platform == SOC_TYPE.VERSAL:
        gic_node = sdt.tree["/amba_apu/interrupt-controller@f9000000"]
    if platform == SOC_TYPE.ZYNQMP:
        gic_node = sdt.tree["/amba-apu@0/interrupt-controller@f9010000"]
    openamp_shm_node = sdt.tree["/amba/shm@0"]
    openamp_shm_node.name = "shm@"+hex(rsc_mem_pa).replace("0x","")
    openamp_shm_node["reg"].value = [0x0 , rsc_mem_pa, 0x0, shared_mem_size]
    openamp_shm_node.sync ( sdt.FDT )
    for node in sdt.tree:
        if "ps_ipi" in node.abs_path:
            prop = LopperProp("interrupt-parent")
            prop.value = gic_node.phandle
            node + prop
            node.sync ( sdt.FDT )

def handle_rpmsg_kernelspace_case(tgt_node, sdt, options, domain_node, memory_node, rpu_node, platform):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    try:
        cpu_prop_values = domain_node['cpus'].value
    except:
        return False

    # 1) we have to replace the cpus index in the rpu node
    # the cpu handle is element 0
    cpu_mask = cpu_prop_values[1]

    if verbose:
        print( "[INFO]: cb cpu mask: %s" % cpu_mask )

    if rpu_node == None:
        print( "not valid input systemDT for openamp rpmsg kernelspace case")
        return False

    rpu_path = rpu_node.abs_path

    # Note: we may eventually just walk the tree and look for __<symbol>__ and
    #       use that as a trigger for a replacement op. But for now, we will
    #       run our list of things to change, and search them out specifically
    # find the cpu node of the rpu node
    try:
        rpu_cpu_node = sdt.tree[ rpu_path + "/__cpu__" ]
    except:
        print( "[ERROR]: cannot find the target rpu node" )
        return  memory_node

    # update mboxes value with phandles
    mailbox_node = sdt.tree["/zynqmp_ipi1"]
    for node in mailbox_node.subnodes():
        if node.props('xlnx,open-amp,mailbox') != []:
            rpu_cpu_node["mboxes"].value = [ node.phandle , 0x0, node.phandle, 0x1]
            rpu_node.sync( sdt.FDT )

    # we have to turn the cpu mask into a name, and then apply it
    # to the rpu node for later

    # shift the mask right by one
    nn = cpu_mask >> 1
    new_rpu_name = "r5_{}".format(nn)

    ## TODO: can we force a tree sync on this assignment ???
    rpu_cpu_node.name = new_rpu_name

    # we need to pickup the modified named node
    sdt.tree.sync()

    # 2) we have to fix the core-conf mode
    cpus_mod = cpu_prop_values[2]
    if verbose > 2:
        print( "[INFO]: cpus mod: %s" % hex(cpus_mod) )

    # bit 30 is the cpu mod, device tree goes 31->0
    if check_bit_set( cpus_mod, 30 ):
        core_conf = "sync"
    else:
        core_conf = "split"
    try:
        rpu_node['core_conf'].value = core_conf
        rpu_node.sync( sdt.FDT )
    except Exception as e:
        print( "[WARNING]: exception: %s" % e )

    # 3) handle the memory-region

    # We look for the memory regions that are in the access list (by checking
    # each access list node and if the parent of the node is "memory", it is a
    # memory access. And then filling the collected list of memory access nodes
    # into to memory-region property of the r5 subnode of the added rpu node.

    # Note: we could assume that the /reserved-memory node has already been
    #       pruned and simply walk it .. which is what we'll do for now.
    #       Otherwise, we need to factor our the reference count code, make it a
    #       utility in lopper and use it here and in the openamp domain
    #       processing.
    #
    if memory_node:
        if verbose:
            print( "[INFO]: memory node found, walking for memory regions" )


        phandle_list = []
        sub_mem_nodes = memory_node.subnodes()
        for n in sub_mem_nodes:
            for p in n:
                if p.name == "phandle":
                    phandle_list = phandle_list + p.value

        if phandle_list:
            # we found some phandles, these need to go into the "memory-region" property of
            # the cpu_node
            if verbose:
                print( "[INFO]: setting memory-region to: %s" % phandle_list )
            try:
                rpu_cpu_node = sdt.tree[ rpu_path + "/" + new_rpu_name ]
                rpu_cpu_node["memory-region"].value = phandle_list
                rpu_cpu_node.sync( sdt.FDT )




            except Exception as e:
                exc_type, exc_obj, exc_tb = sys.exc_info()
                fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                print(exc_type, fname, exc_tb.tb_lineno)
    else:
        print( "[WARNING]: /reserved-memory node not found" )

    # 4) fill in the #address-cells and #size-cells.
    #
    # We lookup the values in the domain node, and copy them to the zynqmp node

    a_cells = domain_node[ "#address-cells" ].value
    s_cells = domain_node[ "#size-cells" ].value

    rpu_cpu_node["#address-cells"].value = a_cells
    rpu_cpu_node["#size-cells"].value = s_cells

    # 5) mboxes
    #
    # Walk the access list of the domain node. If there are any ipi resources,
    # we add them to the mboxes property in the zynqmp-rpu node.

    # TODO: this is very similar to the domain processing loop. So we'll have
    #       to factor it out at some point.

    # "access" is a list of tuples: phandles + flags
    access_list = domain_node["access"].value

    if not access_list:
        if verbose:
            print( "[INFO]: xlnx_openamp_rpu: no access list found, skipping ..." )
    else:
        ipi_access = []
        flag_idx = 1

        # although the access list is decoded as a list, it is actually pairs, so we need
        # to get every other entry as a phandle, not every one.
        for ph in access_list[::2]:
            flags = access_list[flag_idx]
            flag_idx = flag_idx + 2

            anode = sdt.tree.pnode(ph)
            if anode:
                node_parent = anode.parent
            else:
                # set this to skip the node_parent processing below
                node_parent = 0

            if node_parent:
                parent_node_type = node_parent.type
                parent_node_name = node_parent.name
                node_grand_parent = node_parent.parent

                if "xlnx,zynqmp-ipi-mailbox" in parent_node_type:
                    if verbose > 1:
                        print( "[INFO]: node parent is an ipi (%s)" % parent_node_name)

                    ipi_access.append( (ph,flags) )

        #
        # We now have to process the phandles + flags, from the SDT description:
        #
        # * xlnx,zynqmp-ipi-mailbox:
        # *   4 bits for each IPI channel to pass special flags
        # *   0-3   bits: channel 0
        # *   4-7   bits: channel 1
        # *   8-11  bits: channel 2
        # *   12-15 bits: channel 3
        # * each 4 bits:
        # *   bit 0: enable/disable (enable==1)
        # *   bit 1: TX/RX (TX==1)
        # *   bit 2-3: unused

        # mboxes_prop will be a list of <phandle> <number>, where <number> is 0
        # for rx and <1> for tx. So for any enabled mboxes, we'll generate this
        # list and then assign it to the property
        #
        mboxes_prop = []
        mbox_names = ""
        if ipi_access:
            for ipi in ipi_access:
                ph,flags = ipi
                if verbose > 1:
                    print( "[INFO]: xlnx_openamp_rpu: processing ipi: ph: %s flags: %s" % (hex(ph), hex(flags)))

                ipi_chan = {}
                ipi_chan_mask = 0xF
                chan_enabled_bit = 0x0
                chan_rx_tx_bit = 0x1
                for i in range(0,4):
                    ipi_chan[i] = flags & ipi_chan_mask
                    ipi_chan_mask = ipi_chan_mask << 4

                    if verbose > 1:
                        print( "        chan: %s, flags: %s" % ( i, hex(ipi_chan[i]) ) )
                    if check_bit_set( ipi_chan[i], chan_enabled_bit ):
                        if verbose > 1:
                            print( "        chan: %s is enabled" % i )
                        mboxes_prop.append( ph )
                        # channel is enabled, is is rx or tx ?
                        if check_bit_set( ipi_chan[i], chan_rx_tx_bit ):
                            if verbose > 1:
                                print( "        channel is tx" )
                            mboxes_prop.append( 1 )
                            mbox_names = mbox_names + "tx" + '\0'
                        else:
                            if verbose > 1:
                                print( "        channel is rx" )
                            mboxes_prop.append( 0 )
                            mbox_names = mbox_names + "rx" + '\0'

                    chan_enabled_bit = chan_enabled_bit + 4
                    chan_rx_tx_bit = chan_rx_tx_bit + 4

            if mboxes_prop:
                # drop a trailing \0 if it was added above
                mbox_names = mbox_names.rstrip('\0')
                rpu_cpu_node["mboxes"].value = mboxes_prop
                print(mbox_names)
                rpu_cpu_node["mbox-names"].value = mbox_names
                rpu_cpu_node.sync( sdt.FDT )
    # if is kernel case, make sure name reflects register prop
    rpu_node.name = rpu_node.name +"@"+hex(rpu_node["reg"].value[1]).replace("0x","")
    rpu_node.sync ( sdt.FDT )
    return memory_node

# tgt_node: is the openamp domain node number
# sdt: is the system device tree
# TODO: this routine needs to be factored and made smaller
def xlnx_openamp_rpu( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    if verbose:
        print( "[INFO]: cb: xlnx_openamp_rpu( %s, %s, %s )" % (tgt_node, sdt, verbose))

    domain_node = sdt.tree[tgt_node]

    root_node = sdt.tree["/"]
    platform = SOC_TYPE.UNINITIALIZED
    if 'versal' in str(root_node['compatible']):
        platform = SOC_TYPE.VERSAL
    elif 'zynqmp' in str(root_node['compatible']):
        platform = SOC_TYPE.ZYNQMP
    else:
        print("invalid input system DT")
        return False

    # find the added rpu node
    try:
        rpu_node = sdt.tree[".*zynqmp-rpu" ]
        is_kernel_case = True
    except:
        print( "[ERROR]: cannot find the target rpu node" )
        rpu_node = None
        is_kernel_case = False

    try:
        memory_node = sdt.tree[ "/reserved-memory" ]
    except:
        return False
    ipis = parse_ipis_for_rpu(sdt, domain_node, options)

    if is_kernel_case:
        remoteproc_node = sdt.tree[ memory_node.abs_path + "/memory_r5@0"]
    else:
        remoteproc_node = None

    mem_carveouts = parse_memory_carevouts(sdt, options, remoteproc_node)

    [rsc_mem_pa,shared_mem_size] = get_r5_needed_symbols(mem_carveouts)
    if rsc_mem_pa == -1 or shared_mem_size == -1:
        print("[ERROR]: failed to find rsc_mem_pa or shared_mem_size")
    inputs = {
        "CHANNEL_0_RSC_MEM_SIZE" : "0x2000UL",
        "CHANNEL_0_TX" : "FW_RSC_U32_ADDR_ANY",
        "CHANNEL_0_RX" : "FW_RSC_U32_ADDR_ANY",
    }
    # userspace case is accounted for later on so do not worry about vring tx/rx

    inputs = setup_ipi_inputs(inputs, platform, ipis, options)

    generate_openamp_file( mem_carveouts, options, platform, is_kernel_case, inputs )

    if rpu_node != None:
        handle_rpmsg_kernelspace_case(tgt_node, sdt, options, domain_node, memory_node, rpu_node, platform)
    else:
        handle_rpmsg_userspace_case(tgt_node, sdt, options, domain_node, memory_node, rpu_node, rsc_mem_pa, shared_mem_size, platform)

    return True

