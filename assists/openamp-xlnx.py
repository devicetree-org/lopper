#!/usr/bin/python3

#/*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

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

def is_compat( node, compat_string_to_test ):
    if re.search( "openamp,xlnx-rpu", compat_string_to_test):
        return xlnx_openamp_rpu
    return ""

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False

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

    try:
        cpu_prop_values = domain_node['cpus'].value
    except:
        return False

    # 1) we have to replace the cpus index in the rpu node
    # the cpu handle is element 0
    cpu_mask = cpu_prop_values[1]

    if verbose:
        print( "[INFO]: cb cpu mask: %s" % cpu_mask )

    # find the added rpu node
    try:
        rpu_node = sdt.tree[".*zynqmp-rpu" ]
    except:
        print( "[ERROR]: cannot find the target rpu node" )
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
        return False

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
    try:
        memory_node = sdt.tree[ "/reserved-memory" ]
    except:
        memory_node = None

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

                # TODO: the list of phandles is coming out as <a b c> versus <a>,<b>,<c>
                #       this may or may not work at runtime and needs to be investigated.
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
                rpu_cpu_node["mbox-names"].value = mbox_names
                rpu_cpu_node.sync( sdt.FDT )


    return True

