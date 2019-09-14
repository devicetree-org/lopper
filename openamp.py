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
import lopper
from libfdt import Fdt, FdtSw, FdtException, QUIET_NOTFOUND, QUIET_ALL
import libfdt

def get_compatible_strings():
    print( "openamp,domain-v1" )

def is_compat( compat_string_to_test ):
    if re.search( "openamp,domain-v1", compat_string_to_test):
        return True
    if re.search( "openamp,xlnx-rpu", compat_string_to_test):
        return True
    return False

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False

# domain_node: is the openamp domain node
# sdt: is the system device tree
def xlnx_openamp_rpu( domain_node, sdt, verbose=0 ):
    if verbose:
        print( "[INFO]: cb: xlnx_openamp_rpu( %s, %s, %s )" % (domain_node, sdt, verbose))

    # temp; PoC:
    cpu_prop_values = Lopper.prop_get( sdt.FDT, domain_node, "cpus", "compound" )
    if cpu_prop_values == "":
        return False

    # 1) we have to replace the cpus index in the rpu node
    # the cpu handle is element 0
    cpu_mask = cpu_prop_values[1]

    if verbose:
        print( "[INFO]: cb cpu mask: %s" % cpu_mask )

    # find the added rpu node
    rpu_node = Lopper.node_find( sdt.FDT, "/zynqmp-rpu/" )
    if not rpu_node:
        print( "[ERROR]: cannot find the target rpu node" )
        return False

    # Note: we may eventually just walk the tree and look for __<symbol>__ and
    #       use that as a trigger for a replacement op. But for now, we will
    #       run our list of things to change, and search them out specifically
    # find the cpu node of the rpu node
    rpu_cpu_node = Lopper.node_find( sdt.FDT, "/zynqmp-rpu/__cpu__" )
    if not rpu_cpu_node:
        print( "[ERROR]: cannot find the target rpu node" )
        return False

    # we have to turn the cpu mask into a name, and then apply it
    # to the rpu node for later

    # shift the mask right by one
    nn = cpu_mask >> 1
    new_rpu_name = "r5_{}".format(nn)
    sdt.FDT.set_name( rpu_cpu_node, new_rpu_name )

    # double check by searching on the new name
    rpu_cpu_node = Lopper.node_find( sdt.FDT, "/zynqmp-rpu/" + new_rpu_name )

    # 2) we have to fix the core-conf mode
    cpus_mod = cpu_prop_values[2]
    if verbose > 2:
        print( "[INFO]: cpus mod: %s" % hex(cpus_mod) )

    # bit 30 is the cpu mod, device tree goes 31->0
    if check_bit_set( cpus_mod, 30 ):
        core_conf = "sync"
    else:
        core_conf = "split"

    Lopper.prop_set( sdt.FDT, rpu_node, 'core_conf', core_conf )

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
    memory_node = Lopper.node_find( sdt.FDT, "/reserved-memory" )
    if memory_node:
        if verbose:
            print( "[INFO]: memory node found, walking for memory regions" )

        depth = 0
        node = memory_node
        phandle_list = []
        while depth >= 0:
            if node and verbose > 2:
                print( "   --> node name: %s" % sdt.FDT.get_name(node) )

            poffset = sdt.FDT.first_property_offset(node, QUIET_NOTFOUND)
            while poffset > 0:
                # if we delete the only property of a node, all calls to the FDT
                # will throw an except. So if we get an exception, we set our poffset
                # to zero to escape the loop.
                try:
                    prop = sdt.FDT.get_property_by_offset(poffset)
                except:
                    poffset = 0
                    continue

                pname = prop.name
                if verbose > 2:
                    print( "       propname: %s" % prop.name )
                if re.search( pname, "phandle" ):
                    # Note: the propery we found is equivalent to: sdt.FDT.get_phandle( node )
                    #       since we are processing the already pruned node, the phandles show
                    #       up as properties. If that changes, we may go to the raw call.
                    pval = Lopper.prop_get( sdt.FDT, node, pname )
                    pval2 = sdt.FDT.get_phandle( node )
                    if pval == pval2:
                        phandle_list.append( pval )

                poffset = sdt.FDT.next_property_offset(poffset, QUIET_NOTFOUND)

            if verbose > 2:
                print( "" )
            node, depth = sdt.FDT.next_node(node, depth, (libfdt.BADOFFSET,))

        if phandle_list:
            # we found some phandles, these need to go into the "memory-region" property of
            # the cpu_node
            if verbose:
                print( "[INFO]: setting memory-region to: %s" % phandle_list )
            try:
                rpu_cpu_node = Lopper.node_find( sdt.FDT, "/zynqmp-rpu/" + new_rpu_name )

                # TODO: the list of phandles is coming out as <a b c> versus <a>,<b>,<c>
                #       this may or may not work at runtime and needs to be investigated.
                Lopper.prop_set( sdt.FDT, rpu_cpu_node, "memory-region", phandle_list )
            except Exception as e:
                exc_type, exc_obj, exc_tb = sys.exc_info()
                fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                print(exc_type, fname, exc_tb.tb_lineno)

    # 4) fill in the #address-cells and #size-cells.
    #
    # We lookup the values in the domain node, and copy them to the zynqmp node
    a_cells = Lopper.prop_get( sdt.FDT, domain_node, "#address-cells" )
    s_cells = Lopper.prop_get( sdt.FDT, domain_node, "#size-cells" )

    # TODO: should check for an exception
    Lopper.prop_set( sdt.FDT, rpu_cpu_node, "#address-cells", a_cells )
    Lopper.prop_set( sdt.FDT, rpu_cpu_node, "#size-cells", s_cells )

    # 5) mboxes
    #
    # Walk the access list of the domain node. If there are any ipi resources,
    # we add them to the mboxes property in the zynqmp-rpu node.

    # TODO: this is very similar to the domain processing loop. So we'll have
    #       to factor it out at some point.

    # "access" is a list of tuples: phandles + flags
    access_list = Lopper.prop_get( sdt.FDT, domain_node, "access", "compound" )
    if not access_list:
        if verbose:
            print( "[INFO]: xlnx_openamp_rpu: no access list found, skipping ..." )
    else:
        ipi_access = []
        flag_idx = 1
        # although the access list is decoded as a list, it is actually pairs, so we need
        # to get every other entry as a phandle, not every one.
        # TODO: yah, there's a more python way to do this iterator.
        for ph in access_list[::2]:
            flags = access_list[flag_idx]
            flag_idx = flag_idx + 2

            anode = sdt.FDT.node_offset_by_phandle( ph )
            node_parent = sdt.FDT.parent_offset(anode,QUIET_NOTFOUND)
            if node_parent:
                parent_node_type = Lopper.prop_get( sdt.FDT, node_parent, "compatible" )
                parent_node_name = sdt.FDT.get_name( node_parent )
                node_grand_parent = sdt.FDT.parent_offset(node_parent,QUIET_NOTFOUND)

                if re.search( "xlnx,zynqmp-ipi-mailbox", parent_node_type ):
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
                Lopper.prop_set( sdt.FDT, rpu_cpu_node, "mboxes", mboxes_prop )
                Lopper.prop_set( sdt.FDT, rpu_cpu_node, "mbox-names", mbox_names )

    return True



# all the logic for applying a openamp domain to a device tree.
# this is a really long routine that will be broken up as more examples
# are done and it can be propery factored out.
def process_domain( tgt_domain, sdt, verbose=0 ):
    tgt_node = Lopper.node_find( sdt.FDT, tgt_domain )
    cpu_prop_values = Lopper.prop_get( sdt.FDT, tgt_node, "cpus", "compound" )

    if cpu_prop_values == "":
        sys.exit(1)

    # the cpu handle is element 0
    cpu_prop = cpu_prop_values[0]
    cpu_node = sdt.FDT.node_offset_by_phandle( cpu_prop )

    if verbose:
        print( "[INFO]: cpu prop phandle: %s" % cpu_prop )
        print( "[INFO]: cpu node: %s" % cpu_node )

    ## We  need to delete any other nodes that have "compatible = cpus,cluster"
    ## and are Not the ones we just found in the chosen node. All we have is a phandle
    ##  so we need to:
    ##   1) find the nodes that are compatible with the cpus,cluster
    ##   2) check their phandle
    ##   3) delete if it isn't the one we just got
    xform_path = "/"
    prop = "cpus,cluster"
    code = """
p = prop_get( %%FDT%%, %%NODE%%, \"compatible\" )
if p and "%%%prop%%%" in p:
    ph = getphandle( %%FDT%%, %%NODE%% )
    if ph != %%%phandle%%%:
        %%TRUE%%
    else:
        %%FALSE%%
else:
    %%FALSE%%
"""
    code = code.replace( "%%%prop%%%", prop )
    code = code.replace( "%%%phandle%%%", str( cpu_prop ) )

    if verbose:
        print( "[INFO]: filtering on:\n------%s-------\n" % code )

    # the action will be taken if the code block returns 'true'
    Lopper.node_filter( sdt, xform_path, "delete", code, verbose )

    # we must re-find the domain node, since its numbering may have
    # changed due to the node_filter deleting things
    tgt_node = Lopper.node_find( sdt.FDT, tgt_domain )

    # lets track any nodes that are referenced by access parameters. We use this
    # for a second patch to drop any nodes that are not accessed, and hence should
    # be removed
    node_access_tracker = {}
    # we want to track child nodes of "/", if they are of type simple-bus AND they are
    # referenced by a <access> value in the domain
    node_access_tracker['/'] = [ "/", "simple-bus" ]

    # "access" is a list of tuples: phandles + flags
    access_list = Lopper.prop_get( sdt.FDT, tgt_node, "access", "compound" )
    if not access_list:
        if verbose:
            print( "[INFO]: no access list found, skipping ..." )
        pass
    else:
        #print( "[INFO]: converted access list: %s" % access_list )

        # although the access list is decoded as a list, it is actually tuples, so we need
        # to get every other entry as a phandle, not every one.
        for ph in access_list[::2]:
            #ph = int(ph_hex, 16)
            #print( "processing %s" % ph )
            anode = sdt.FDT.node_offset_by_phandle( ph )
            node_type = Lopper.prop_get( sdt.FDT, anode, "compatible" )
            node_name = sdt.FDT.get_name( anode )
            node_parent = sdt.FDT.parent_offset(anode,QUIET_NOTFOUND)
            if re.search( "simple-bus", node_type ):
                if verbose > 1:
                    print( "[INFO]: access is a simple-bus (%s), leaving all nodes" % node_name)
                # refcount the bus
                full_name = Lopper.node_abspath( sdt.FDT, anode )
                sdt.node_ref_inc( full_name )
            else:
                # The node is *not* a simple bus, so we must do more processing

                # a) If the node parent is something other than zero, the node is nested, so
                #    we have to do more processing.
                #    Note: this should be recursive eventually, but for now, we keep it simple
                # print( "node name: %s node parent: %s" % (node_name, node_parent) )
                if node_parent:
                    parent_node_type = Lopper.prop_get( sdt.FDT, node_parent, "compatible" )
                    parent_node_name = sdt.FDT.get_name( node_parent )
                    node_grand_parent = sdt.FDT.parent_offset(node_parent,QUIET_NOTFOUND)
                    if not parent_node_type:
                        # is it a special name ? .. if it is, we'll give it a type to normalize the
                        # code below
                        if re.search( "reserved-memory", parent_node_name ):
                            parent_node_type = "reserved-memory"
                        else:
                            # if there's no type and no special name, we need to bail
                            continue

                    if re.search( "simple-bus", parent_node_type ):
                        if verbose > 1:
                            print( "[INFO]: node parent is a simple-bus (%s), dropping sibling nodes" % parent_node_name)
                        # TODO: this node path must be constructed better than this ...
                        parent_subnodes = Lopper.get_subnodes( sdt.FDT, "/" + parent_node_name )
                        for n in parent_subnodes:
                            if re.search( node_name, n ):
                                pass # do nothing for now
                            else:
                                # we must delete this node
                                tgt_node_path = "/" + parent_node_name + "/" + n
                                try:
                                    tgt_node_id = sdt.FDT.path_offset( tgt_node_path )
                                except:
                                    tgt_node_id = 0
                                if tgt_node_id:
                                    sdt.node_remove( tgt_node_id )
                    elif re.search( "reserved-memory", parent_node_type ):
                        if verbose > 1:
                            print( "[INFO]: reserved memory processing for: %s" % node_name)

                        full_name = Lopper.node_abspath( sdt.FDT, node_parent )
                        if not full_name in node_access_tracker:
                            node_access_tracker[full_name] = [ full_name, "*" ]

                        # Increment a reference to the current node, since we've added the parent node
                        # to a list of nodes that we'll use to check for referenced children later. Anything
                        # with no reference, will be removed.
                        full_name = Lopper.node_abspath( sdt.FDT, anode )
                        sdt.node_ref_inc( full_name )

        for n, value in node_access_tracker.values():
            # xform_path is the path to the node that was tracked, so this is a potential
            # delete to any children of that node, that haven't been accessed. If you
            # started from / .. you could delete a lot of nodes by mistake, so be careful!

            xform_path = n
            if value == "*":
                code = """
p = refcount( %%SDT%%, %%NODENAME%% )
if p <= 0:
    %%TRUE%%
else:
    %%FALSE%%
"""
            else:
                prop = value
                code = """
p = prop_get( %%FDT%%, %%NODE%%, \"compatible\" )
if p and "%%%prop%%%" in p:
    p = refcount( %%SDT%%, %%NODENAME%% )
    if p <= 0:
        %%TRUE%%
    else:
        %%FALSE%%
else:
    %%FALSE%%
"""
                code = code.replace( "%%%prop%%%", prop )

            if verbose:
                print( "[INFO]: filtering on:\n------%s-------\n" % code )

            # the action will be taken if the code block returns 'true'
            if n == "/":
                Lopper.node_filter( sdt, "/", "delete", code, verbose )
            else:
                Lopper.node_filter( sdt, n + "/", "delete", code, verbose )

            # we must re-find the domain node, since its numbering may have
            # changed due to the node_filter deleting things
            tgt_node = Lopper.node_find( sdt.FDT, tgt_domain )

    # we must re-find the domain node, since its numbering may have
    # changed due to the node_filter deleting things
    tgt_node = Lopper.node_find( sdt.FDT, tgt_domain )

    memory_hex = Lopper.prop_get( sdt.FDT, tgt_node, "memory", "compound:hex" )
    memory_int = Lopper.prop_get( sdt.FDT, tgt_node, "memory", "compound" )

    # This may be moved to the top of the domain process and then
    # when we are processing cpus and bus nodes, we can apply the
    # memory to ranges <>, etc, and modify them accordingly.
    if verbose > 1:
        print( "[INFO]: memory property: %s" % memory_hex )

        # 1) find if there's a top level memory node
        memory_node = Lopper.node_find( sdt.FDT, "/memory" )
        if memory_node:
            if verbose:
                print( "[INFO]: memory node found (%s), modifying to match domain memory" % memory_node )

            # 2) modify that memory property to match the node we have here
            # memprop_old = sdt.FDT.getprop(memory_node, 'reg' )
            # num_bits = len(memprop_old)
            # a = 0
            # b = 1
            # c = 0
            # d = 1
            # val = a.to_bytes(4,byteorder='big') + b.to_bytes(4,byteorder='big') + c.to_bytes(4,byteorder='big') + d.to_bytes(4,byteorder='big')

            # TODO: change this to a lopper wrapper call
            sdt.FDT.setprop(memory_node, 'reg', Lopper.encode_byte_array(memory_int))




