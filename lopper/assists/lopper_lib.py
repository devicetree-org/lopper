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
from lopper.tree import LopperAction
from lopper.tree import LopperProp
from lopper.tree import LopperNode
from lopper.tree import LopperTree
from lopper.yaml import LopperYAML
import lopper
import json
from itertools import chain

# utility function to return true or false if a number
# is 32 bit, or not.
def check_32_bit(n):
    return (n & 0xFFFFFFFF00000000) == 0

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


def json_expand( node ):
    debug = False
    if debug:
        print( f"[DBG]: ========> json expanding node: {node.name}" )
    for p in node:
        if p.pclass == 'json':
            # save the original json, we may need it again
            p.value_json = p.value

            # this converts it to a list, but that's causing some
            # issues with assumptions in the various _expand routines, so
            # not doing this for now.
            # p.value = p.value

            phandle_index,field_count = p.phandle_params()
            if debug:
                print( f'   -- json property: [{[p]}] {p.name} [{p.value}]' )
                print( f'        phandle info: {phandle_index} {field_count}' )

            loaded_j = json.loads( p.value_json )
            p.struct_value = loaded_j

            p.list_value = []
            if field_count:
                for j in loaded_j:
                    if type(j) == list:
                        p.list_value = p.list_value + j
                    elif type(j) == dict:
                        vals = list(j.values())
                        p.list_list = p.list_value + vals
                    else:
                        p.list_value.append(j)

            # dump the json elements
            if debug:
                print( f"        [{type(loaded_j)}] {loaded_j}" )
                for j in loaded_j:
                    if type(j) == list:
                        for jj in j:
                            print(f"        json list element: {jj}" )
                    elif type(j) == dict:
                        for jj,kk in j.items():
                            print(f"        json dict element: key: {jj}: value: {kk}" )
                            if type(kk) == dict:
                                print( "              nested dict" )
                    else:
                        print( f"       non-list: {j}" )

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
        if re.search( r"reserved-memory", p.name ):
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
        if re.search( r"reserved-memory", p.name ):
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
        for ph in includes:
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

# utility routine to take a list of cells and return a value from
# the list. Both the value and the cells used to construct the value
# are returned. The number of cells used to construct the value is
# dictated by the cell_size parameter.
def cell_value_get( cells, cell_size, start_idx = 0 ):
    used_cells = []
    if cell_size == 2:
        memory_value = (cells[start_idx] << 32) | cells[start_idx+1]
        used_cells.append(cells[start_idx])
        used_cells.append(cells[start_idx+1])
    else:
        memory_value = cells[start_idx]
        used_cells.append(cells[start_idx])

    return memory_value, used_cells

# utility routine to take a value, which can be 32bit or
# 64bit and split it into a number of cells (dictated by
# the cell_size parameter)
def cell_value_split( value, cell_size ):
    ret_val = []

    if cell_size == 2:
        ret_val.append((value & 0xFFFFFFFF00000000) >> 32)
        ret_val.append((value & 0x00000000FFFFFFFF))
        mem_changed_flag = True
    else:
        ret_val.append(value)

    return ret_val

# returns a list of all properties in the tree that
# reference a given node (via phandle)
def all_refs( tree, node ):
    nodes = []

    # get a list of all properties that reference a given node
    for n in tree:
        for p in n:
            phandles = p.resolve_phandles()
            if node in phandles:
                nodes.append( p )

    return nodes

# returns True if a node is compatible with the passed string
# (or list of strings)
def is_compat( node, compat_string ):
    try:
        node_compat = node['compatible'].value
    except:
        return None

    if type(compat_string) == list:
        x = None
        for c in compat_string:
            if not x:
                x = [item for item in node_compat if c in item]
    else:
        x = [item for item in node_compat if compat_string in item]

    return x != []

# process cpus, and update their references appropriately
def cpu_refs( tree, cpu_prop, verbose = 0 ):
    refd_cpus = []

    if not cpu_prop:
        return refd_cpus, refd_cpus

    if verbose:
        print( f"[DBG]: lopper_lib: cpu_refs: processing {cpu_prop}" )

    cpu_prop_list = list( chunks(cpu_prop.value,3) )
    sub_cpus_all = []

    # loop through the nodes, we want to refcount the sub-cpu nodes
    # and their parents, we'll delete anything that isn't used later.
    for cpu_phandle, mask, mode in cpu_prop_list:
        cpu_mask = mask
        if verbose:
            print( f"[INFO]: cb cpu mask: {hex(cpu_mask)}")

        try:
            cpu_node = tree.pnode(cpu_phandle)
            if not cpu_node:
                if verbose:
                    print( f"[DBG]: lopper_lib, no cpu found at phandle {hex(cpu_phandle)}, skipping")
                    continue
        except:
            # couldn't find the node, skip
            continue

        sub_cpus = tree.subnodes( cpu_node, "cpu@.*" )
        sub_cpus_all = sub_cpus + sub_cpus_all

        if verbose:
            print( f"[INFO]: lopper_lib: cpu prop phandle: {cpu_phandle}" )
            print( f"[INFO]: lopper_lib: cpu node: {cpu_node}" )
            print( f"[INFO]: lopper_lib: sub cpus: {sub_cpus}" )

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
                print( f"[WARNING]: {e}" )

    # you can globally check for ref'd cpus after calling this routine
    # via:
    #    ref_nodes = tree.refd( "/cpus.*/cpu.*" )

    return refd_cpus, unrefd_cpus
