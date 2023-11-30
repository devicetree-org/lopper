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
from lopper.tree import LopperAction
import lopper
import lopper_lib
from itertools import chain

def is_compat( node, compat_string_to_test ):
    if re.search( "access-domain,domain-v1", compat_string_to_test):
        return core_domain_access
    if re.search( "module,domain_access", compat_string_to_test):
        return core_domain_access
    return ""

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False

# tree: is the lopper system device-tree
def domain_get_subnodes(tree):
    try:
        domain_node = tree['/domains']
    except:
        domain_node = None

    direct_node_refs = []

    if domain_node:
        for node in domain_node.subnodes():
            # 1) memory access/node = <> nodes
            try:
                mem_node = node["memory"].value
                direct_node_refs.append( node )
            except:
                pass
            # 2) direct access = <> nodes
            a_nodes = lopper_lib.node_accesses( tree, node )
            if a_nodes:
                direct_node_refs.append( node )
            # 3) include = <> nodes
            try:
                i_nodes = lopper_lib.includes( tree, node['include'])
                if i_nodes:
                    direct_node_refs.append( node )
            except:
                pass

    # Remove duplicate entries
    direct_node_refs = list(dict.fromkeys(direct_node_refs))
    return direct_node_refs

# node: is the domain node number
# mem_val: Memory node address and size value to be updated
# This api takes the memory value(address and size) and creates
# a new memory node value for memory node reg property based on the
# address-cells and size-cells property.
def update_mem_node(node, mem_val):
    ac = node.parent['#address-cells'][0]
    sc = node.parent['#size-cells'][0]

    new_mem_val = []
    mem_reg_pairs = len(mem_val)/2
    addr_list = mem_val[::2]
    size_list = mem_val[1::2]
    for i in range(0, int(mem_reg_pairs)):
        for j in range(0, ac):
            high_addr = 0
            val = str(hex(addr_list[i]))[2:]
            if len(val) > 8:
                high_addr = 1
            if j == ac-1:
                if len(val) > 8:
                    pad = len(val) - 8
                    upper_val = val[:pad]
                    lower_val = val[pad:]
                    new_mem_val.append(int(upper_val, base=16))
                    new_mem_val.append(int(lower_val, base=16))
                else:
                     new_mem_val.append(addr_list[i])
            elif high_addr != 1:
                new_mem_val.append(0)
        for j in range(0, sc):
            if j == sc-1:
                new_mem_val.append(size_list[i])
            else:
                new_mem_val.append(0)
    return new_mem_val

# tgt_node: is the domain node number
# sdt: is the system device tree
def core_domain_access( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    if tgt_node.abs_path == "/":
        if sdt.target_domain:
            try:
                tgt_node = sdt.tree[sdt.target_domain]
            except Exception as e:
                print( "[ERROR]: target domain %s cannot be found" % sdt.target_domain )
                sys.exit(1)
        else:
            try:
                tgt_node = sdt.tree["/domains/default"]
            except:
                pass

    # reset the treewide ref counting
    sdt.tree.ref = 0
    domain_node = sdt.tree[tgt_node]

    if verbose:
        print( "[INFO]: cb: core_domain_access( %s, %s, %s )" % (domain_node, sdt, verbose))

    direct_node_refs = []

    # 1) direct access = <> nodes
    a_nodes = lopper_lib.node_accesses( sdt.tree, domain_node )
    for anode in a_nodes:
        # add a refcount to the node and it's parents
        sdt.tree.ref_all( anode, True )
        direct_node_refs.append( anode )

    # 2) are there resource group includes ?, they can have access = <> as well
    try:
        includes = domain_node["include"]
    except:
        includes = None

    if includes:
        include_nodes = lopper_lib.includes( sdt.tree, domain_node["include"] )

        for i in include_nodes:
            a_nodes = lopper_lib.node_accesses( sdt.tree, i )
            for anode in a_nodes:
                sdt.tree.ref_all( anode, True )
                direct_node_refs.append( anode )

    # 3) cpus access
    try:
        cpu_prop = domain_node['cpus']
    except:
        if verbose:
            print( "[WARNING]: core_domain: domain node does not have a cpu link" )
        cpu_prop = None

    if cpu_prop:
        refd_cpus, unrefd_cpus = lopper_lib.cpu_refs( sdt.tree, cpu_prop, verbose )
        if refd_cpus:
            ref_nodes = sdt.tree.refd( "/cpus.*/cpu.*" )

            # now we do two types of refcount delete
            #   - betweenthe cpu clusters
            #   - on the cpus within a cluster

            # The following filter code will check for nodes that are compatible to
            # cpus,cluster and if they haven't been referenced, delete them.
            xform_path = "/"
            prop = "cpus,cluster"
            code = """
                     p = node.propval('compatible')
                     if p and "{0}" in p:
                         if node.ref <= 0:
                             return True

                     return False
                   """.format( prop )

            if verbose:
                print( "[INFO]: core_domain_access: filtering on:\n------%s\n-------\n" % code )

            # the action will be taken if the code block returns 'true'
            # Lopper.node_filter( sdt, xform_path, LopperAction.DELETE, code, verbose )
            sdt.tree.filter( xform_path, LopperAction.DELETE, code, None, verbose )

            for s in unrefd_cpus:
                try:
                    if verbose:
                        print( "[INFO]: core_domain_access: deleting unrefernced subcpu: %s" % s.abs_path )
                    sdt.tree.delete( s )
                except Exception as e:
                    print( "[WARNING]: %s" % e )

    # 4) directly accessed nodes. Check their type. If they are busses,
    #    we have some sedoncary processing to do.
    nodes_to_filter = []
    for anode in direct_node_refs:
        node_types = lopper_lib.node_ancestor_types( anode )
        simple_bus = lopper_lib.node_ancestors_of_type( anode, "simple-bus" )
        if simple_bus:
            for i,s in enumerate(simple_bus):
                if not s in nodes_to_filter:
                    if verbose > 1:
                        print( "[INFO]: core_domain_access: rsimple bus processing for: %s" % anode.name )

                    nodes_to_filter.append( s )

        reserved_memory = "reserved-memory" in chain(*node_types)
        if reserved_memory:
            if verbose > 1:
                print( "[INFO]: core_domain_access: reserved memory processing for: %s" % anode.name )
            nodes_to_filter.append( anode.parent )

    # 5) filter nodes that don't have refcounts
    #
    # filter #1:
    #    - starting at /
    #    - drop any unreferenced nodes that are of type simple-bus
    prop = "simple-bus"
    code = """
           p = node.propval( 'compatible' )
           if p and "{0}" in p:
               r = node.ref
               if r <= 0:
                   return True
               else:
                   return False
           else:
               return False
           """.format( prop )

    if verbose:
        print( "[INFO]: core_domain_access: filtering on:\n------%s\n-------\n" % code )

    sdt.tree.filter( "/", LopperAction.DELETE, code, None, verbose )

    # filter #2:
    #    - starting at simple-bus nodes
    #    - drop any unreferenced elements

    # filter #3:
    #    - starting at reserved memory parent
    #    - drop any unreferenced elements

    for n in nodes_to_filter:
        code = """
               p = node.ref
               if p <= 0:
                   return True
               else:
                   return False
               """
        if verbose:
            print( "[INFO]: core_domain_access: filtering on:\n------%s\n-------\n" % code )

        sdt.tree.filter( n + "/", LopperAction.DELETE, code, None, verbose )


    # 6) memory node processing
    try:
        memory_int = domain_node['memory'].int()
        memory_hex = domain_node['memory'].hex()
    except Exception as e:
        memory_hex = 0x0
        memory_int = 0

    # This may be moved to the top of the domain process and then when we are
    # processing cpus and bus nodes, we can apply the memory to ranges <>, etc,
    # and modify them accordingly.
    if verbose > 1:
        print( "[INFO]: core_domain_access: memory property: %s" % memory_hex )

    # 1) find if there's a top level memory node
    try:
        memory_node = sdt.tree["/memory@.*"]
    except:
        memory_node = None

    if memory_node:
        if verbose:
            print( "[INFO]: core_domain_access: memory node found (%s), modifying to match domain memory" % memory_node )

        # 2) modify that memory property to match the node we have here
        # memprop_old = sdt.FDT.getprop(memory_node, 'reg' )
        # num_bits = len(memprop_old)
        # a = 0
        # b = 1
        # c = 0
        # d = 1
        # val = a.to_bytes(4,byteorder='big') + b.to_bytes(4,byteorder='big') + c.to_bytes(4,byteorder='big') + d.to_bytes(4,byteorder='big')

        # TODO: this seems to be shortening up the system memory node. Check to see
        #       if the openamp node is being propery interpreted

        try:
            memory_node['reg'].value = memory_int
        except:
            pass

    # final) deal with unreferenced nodes
    refd_nodes = sdt.tree.refd()
    if verbose:
        for p in refd_nodes:
            code = """
                p = node.ref
                if p <= 0:
                    return True
                else:
                    return False
                """
            # delete any unreferenced nodes
            # not currently enabled, as it deletes our domain and other nodes
            # of values. We could refernece those nodes explicitly if we want
            # to use this as a final house cleaning step in the future.
            # sdt.tree.filter( "/", LopperAction.DELETE, code, None, verbose )


    #sys.exit(1)

    return True
