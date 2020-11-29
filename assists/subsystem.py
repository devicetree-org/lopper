#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
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
import ast
from pathlib import Path
from pathlib import PurePath
from io import StringIO
import contextlib
import importlib
from lopper import Lopper
from lopper import LopperFmt
from lopper_tree import LopperAction
from lopper_tree import LopperProp
import lopper
import json

def is_compat( node, compat_string_to_test ):
    if re.search( "module,subsystem", compat_string_to_test):
        return subsystem_expand
    return ""

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False

def set_bit(value, bit):
    return value | (1<<bit)

def clear_bit(value, bit):
    return value & ~(1<<bit)

def chunks(l, n):
    # For item i in a range that is a length of l,
    for i in range(0, len(l), n):
        # Create an index range for l of n items:
        yield l[i:i+n]

def property_set( property_name, property_val, node, fdt=None ):
    newprop = LopperProp( property_name, -1, None, property_val )
    node += newprop
    if fdt:
        node.sync( fdt )

def val_as_bool( val ):
    if val == "False":
        return False
    elif val == "True":
        return True

# sdt: is the system device tree
def subsystem_expand( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    try:
        args = options['args']
    except:
        args = []

    if verbose:
        print( "[INFO]: cb: subsystem_expand( %s, %s, %s, %s )" % (tgt_node, sdt, verbose, args))

    node_regex = ""
    if args:
        tgt_regex = args[0]
        if len(args) == 2:
            node_regex = args[1]

    if not node_regex:
        node_regex = "/domains"

    tree = sdt.tree

    domain_node = tree["/domains"]

    # add the cells properties
    property_set( "#address-cells", 2, domain_node )
    property_set( "#size-cells", 2, domain_node )
    domain_node.sync( tree.fdt )

    #domains = sdt.tree.nodes( "/domains/[^/]*$" )
    domain_count = 0
    for subnode in domain_node.child_nodes.values():
        if subnode._source == "yaml":
            if verbose:
                print( "[DBG] yaml input dectected, expanding %s to full device tree domain" % subnode.abs_path )

            # we flip the name and the label, since the yaml name does not
            # follow device tree conventions.
            name = subnode.name
            subnode.name = "domain@{}".format( domain_count )
            subnode.label = name
            property_set( "lopper-label-gen1", subnode.label, subnode )

            # set the compatibility
            property_set( "compatible", "openamp,domain-v1", subnode )

            # ensure that the xilinx,subsystem is set property
            property_set( "xilinx,subsystem", 1, subnode )

            ## cpu processing
            cpus = subnode.props( "cpus" )

            cpus_chunks = json.loads(cpus[0].value)
            cpus_list = []
            for c in cpus_chunks:
                if verbose:
                    print( "[DBG]: cpu: %s" % c )
                    print( "         cluster: %s" % c['cluster'] )
                    print( "         cpumask: %s" % c['cpumask'] )
                    print( "         mode: %s" % c['mode'] )

                cluster = c['cluster']

                try:
                    cluster_node = tree.lnodes( cluster )[0]
                except:
                    cluster_node = None

                if cluster_node:
                    if cluster_node.phandle == 0:
                        cluster_node.phandle = tree.phandle_gen()
                        cluster_node.sync( tree.fdt )
                        if verbose:
                            print( "[DBG]: generated phandle %s for node: %s" % (cluster_node.phandle,cluster_node.abs_path ))

                    cluster_handle = cluster_node.phandle
                else:
                    cluster_handle = 0xdeadbeef

                # /*
                # * cpus specifies on which CPUs this domain runs
                # * on
                # *
                # * link to cluster | cpus-mask | execution-mode
                # *
                # * execution mode for ARM-R CPUs:
                # * bit 30: lockstep (lockstep enabled == 1)
                # * bit 31: secure mode / normal mode (secure mode == 1)
                # */
                mode_mask = 0
                mode = c['mode']
                if mode:
                    try:
                        secure = mode['secure']
                        if secure:
                            mode_mask = set_bit( mode_mask, 31 )
                    except:
                        pass

                    try:
                        el = mode['el']
                        if el:
                            mode_mask = set_bit( mode_mask, 0 )
                            mode_mask = set_bit( mode_mask, 1 )
                    except:
                        pass

                mask = c['cpumask']

                # cpus is <phandle> <mask> <mode>
                if verbose:
                    print( "[DBG]:  cluster handle: %s" % hex(cluster_handle) )
                    print( "        cpu mask: %s" % hex(mask) )
                    print( "        mode mask: %s" % hex(mode_mask) )

                cpus_list.extend( [cluster_handle, int(mask), mode_mask ] )

            if cpus_list:
                property_set( "cpus", cpus_list, subnode )

            ## memory processing
            # /*
            # * 1:1 map, it should match the memory regions
            # * specified under access below.
            # *
            # * It is in the form:
            # * memory = <address size address size ...>
            # */
            try:
                mem = subnode.props( "memory" )[0].value
                mem = json.loads(mem)
                mem_list = []
                for m in mem:
                    start = m['start']
                    size = m['size']
                    mem_list.append(int(start))
                    mem_list.append(int(size))

            except Exception as e:
                mem_list = [0xdead, 0xffff ]

            if verbose:
                # dump the memory as hex
                print( '[DBG] memory: [{}]'.format(', '.join(hex(x) for x in mem_list)) )

            property_set( "memory", mem_list, subnode )

            ## access processing
            # /*
            # * Access specifies which resources this domain
            # * has access to.
            # *
            # * Link to resource | flags
            # *
            # * The "flags" field is mapping specific
            # *
            # * For memory, reserved-memory, and sram:
            # *   bit 0: 0/1: RO/RW
            # *
            # * Other cases: unused
            # *
            # * In this example we are assigning:
            # * - memory range 0x0-0x8000000 RW
            # * - tcm RW
            # * - ethernet card at 0xff0c0000
            # */
            access_node = subnode.props( "access" )
            access_chunks = json.loads(access_node[0].value)
            access_list = []

            for a in access_chunks:
                dev = a['dev']
                try:
                    flags = a['flags']
                except:
                    flags = None

                dev_handle =  0xdeadbeef
                if dev:
                    try:
                        dev_node = tree.lnodes( dev )[0]
                    except:
                        if verbose:
                            print( "[DBG]: WARNING: could not find node %s" % dev )
                        dev_node = None

                    if dev_node:
                        if dev_node.phandle == 0:
                            dev_node.phandle = tree.phandle_gen()
                            dev_node.sync( tree.fdt )
                            if verbose:
                                print( "[DBG]: generated phandle %s for node: %s" % (dev_node.phandle,dev_node.abs_path ))

                        dev_handle = dev_node.phandle
                    else:
                        dev_handle = 0xdeadbeef

                flags_value = 0
                if flags:
                    try:
                        secure = flags['secure']
                        secure = val_as_bool( secure )
                        if secure:
                            flags_value = set_bit( flags_value, 0 )
                    except:
                        secure = False

                    try:
                        requested = flags['requested']
                        requested = val_as_bool( requested )
                        if requested:
                            flags_value = set_bit( flags_value, 1 )
                    except:
                        requested = 0x0

                # save the <phandle> <flags> to the list of values to write
                access_list.append( dev_handle )
                access_list.append( flags_value )


            if verbose:
                # dump the memory as hex
                print( '[DBG] setting access: [{}]'.format(', '.join(hex(x) for x in access_list)) )

            property_set( "access", access_list, subnode )

            subnode.sync( tree.fdt )

        domain_count += 1

    return True
