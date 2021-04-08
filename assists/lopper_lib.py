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
from lopper_tree import LopperNode
from lopper_tree import LopperTree
from lopper_yaml import LopperYAML
import lopper
import json
from itertools import chain

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

def node_ancestors_of_type( node, ctype ):
    ret_nodes = []

    p = node.parent
    while p:
        nt = p.type
        if re.search( "reserved-memory", p.name ):
            nt =  [ "reserved-memory" ]

        if ctype in nt:
            ret_nodes.append( p )

        p = p.parent

    return ret_nodes

def node_ancestor_types( node ):
    # The return list from this can be tested as such:
    #         simple_bus = "simple-bus" in chain(*node_types)
    # to get a boolean result
    #
    ret_types = [ node.type ]
    p = node.parent
    while p:
        nt = p.type
        if re.search( "reserved-memory", p.name ):
            nt =  [ "reserved-memory" ]

        if nt:
            ret_types.append( nt )

        p = p.parent

    return ret_types


def includes( tree, include_prop ):
    include_nodes = []
    if include_prop:
        includes = include_prop.value

        # every other entry is a phandle
        for ph in includes[::2]:
            anode = tree.pnode( ph )
            if anode:
                include_nodes.append( anode )

    return include_nodes


def node_accesses( tree, node ):
    try:
        access_list = node["access"].value
    except:
        access_list = []

    accessed_nodes = []
    if access_list:
        # although the access list is decoded as a list, it is actually tuples, so we need
        # to get every other entry as a phandle, not every one.
        for ph in access_list[::2]:
            anode = tree.pnode( ph )
            if anode:
                # print( "node access found: %s" % anode.abs_path )
                accessed_nodes.append( anode )

    return accessed_nodes


# process cpus, and update their references appropriately
def cpu_refs( tree, cpu_node, verbose = 0 ):
    refd_cpus = []

    if not cpu_node:
        return refd_cpus

    if verbose:
        print( "[DBG]: lopper_lib: cpu_refs: processing %s" % cpu_node.abs_path )

    cpu_prop_values = cpu_node.value

    cpu_prop_list = list( chunks(cpu_prop_values,3) )
    sub_cpus_all = []

    # loop through the nodes, we want to refcount the sub-cpu nodes
    # and their parents, we'll delete anything that isn't used later.
    for cpu_phandle, mask, mode in cpu_prop_list:
        cpu_mask = mask
        if verbose:
            print( "[INFO]: cb cpu mask: %s" % hex(cpu_mask))

        try:
            cpu_node = tree.pnode(cpu_phandle)
        except:
            # couldn't find the node, skip
            continue

        sub_cpus = tree.subnodes( cpu_node, "cpu@.*" )
        sub_cpus_all = sub_cpus + sub_cpus_all

        if verbose:
            print( "[INFO]: lopper_lib: cpu prop phandle: %s" % cpu_phandle )
            print( "[INFO]: lopper_lib: cpu node: %s" % cpu_node )
            print( "[INFO]: lopper_lib: sub cpus: %s" % sub_cpus )

        # we'll now walk from 0 -> 31. Checking the mask to see if access is
        # allowed. If it is allowed, we'll check to see if there's a sub-cpu at
        # the same offset. If so, we refcount it AND the parent. For sub-cpus
        # that are available, but have no access, we log them to be delete later
        # (we don't delete them now, since it will shift node numbers.
        for idx in range( 0, 32 ):
            if check_bit_set( cpu_mask, idx ):
                try:
                    sub_cpu_node = sub_cpus[idx]
                    # refcount it AND the parent
                    tree.ref_all( sub_cpu_node, True )
                    refd_cpus.append( sub_cpu_node )
                except:
                    pass

    unrefd_cpus = []
    for s in sub_cpus_all:
        if s not in refd_cpus:
            try:
                unrefd_cpus.append( s )
            except Exception as e:
                print( "[WARNING]: %s" % e )

    # you can globally check for ref'd cpus after calling this routine
    # via:
    #    ref_nodes = tree.refd( "/cpus.*/cpu.*" )

    return refd_cpus, unrefd_cpus
