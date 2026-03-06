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
import lopper.log
import json
from itertools import chain
import humanfriendly

lopper.log._init(__name__)

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
    lopper.log._debug( f"========> json expanding node: {node.name}", level=lopper.log.TRACE )
    for p in node:
        if p.pclass == 'json':
            # save the original json, we may need it again
            p.value_json = p.value

            # this converts it to a list, but that's causing some
            # issues with assumptions in the various _expand routines, so
            # not doing this for now.
            # p.value = p.value

            phandle_index,field_count = p.phandle_params()
            lopper.log._debug( f'   -- json property: [{[p]}] {p.name} [{p.value}]', level=lopper.log.TRACE )
            lopper.log._debug( f'        phandle info: {phandle_index} {field_count}', level=lopper.log.TRACE )

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
            if lopper.log._is_enabled(lopper.log.TRACE):
                lopper.log._debug( f"        [{type(loaded_j)}] {loaded_j}", level=lopper.log.TRACE )
                for j in loaded_j:
                    if type(j) == list:
                        for jj in j:
                            lopper.log._debug(f"        json list element: {jj}", level=lopper.log.TRACE )
                    elif type(j) == dict:
                        for jj,kk in j.items():
                            lopper.log._debug(f"        json dict element: key: {jj}: value: {kk}", level=lopper.log.TRACE )
                            if type(kk) == dict:
                                lopper.log._debug( "              nested dict", level=lopper.log.TRACE )
                    else:
                        lopper.log._debug( f"       non-list: {j}", level=lopper.log.TRACE )

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

def _normalize_start_size_value(raw_value, default_value):
    """Convert YAML-provided start/size representations into integers."""
    if raw_value is None:
        return int(default_value)

    if raw_value == [''] or raw_value == '':
        return int(default_value)

    if isinstance(raw_value, list):
        if not raw_value:
            return int(default_value)

        if len(raw_value) == 1:
            return _normalize_start_size_value(raw_value[0], default_value)

        if all(isinstance(v, int) for v in raw_value):
            combined = 0
            for v in raw_value:
                combined = (combined << 32) | (v & 0xFFFFFFFF)
            return combined

        return _normalize_start_size_value(raw_value[0], default_value)

    if isinstance(raw_value, (int, float)):
        return int(raw_value)

    value_str = str(raw_value).strip()
    if not value_str:
        return int(default_value)

    try:
        return humanfriendly.parse_size(value_str, True)
    except Exception:
        pass

    try:
        return int(value_str, 16)
    except Exception:
        pass

    try:
        return int(value_str)
    except Exception:
        return int(default_value)

def expand_start_size_to_reg(start_size_source, address_cells=2, size_cells=2,
                             default_start=0xbeef, default_size=0xbeef):
    """Convert YAML start/size tuples into reg cell values.

    Args:
        start_size_source (dict | tuple | list | LopperNode): Source providing
            ``start`` and ``size`` values. When a node is supplied, properties
            of the same name are used.
        address_cells (int): Number of cells for the start portion.
        size_cells (int): Number of cells for the size portion.
        default_start (int): Fallback value when start is missing.
        default_size (int): Fallback value when size is missing.

    Returns:
        tuple[list[int], int, int]: A ``reg``-compatible list of integers,
        along with the parsed start and size values.
    """
    if isinstance(start_size_source, LopperNode):
        start_raw = start_size_source.propval("start")
        size_raw = start_size_source.propval("size")
    elif isinstance(start_size_source, dict):
        start_raw = start_size_source.get("start")
        size_raw = start_size_source.get("size")
    elif isinstance(start_size_source, (list, tuple)) and len(start_size_source) >= 2:
        start_raw, size_raw = start_size_source[0], start_size_source[1]
    else:
        raise TypeError("expand_start_size_to_reg expects dict, tuple/list, or LopperNode input")

    start_val = _normalize_start_size_value(start_raw, default_start)
    size_val = _normalize_start_size_value(size_raw, default_size)

    reg_cells = []
    reg_cells.extend(cell_value_split(int(start_val), address_cells))
    reg_cells.extend(cell_value_split(int(size_val), size_cells))

    return reg_cells, int(start_val), int(size_val)

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

    lopper.log._debug( f"cpu_refs: processing {cpu_prop}" )

    cpu_prop_list = list( chunks(cpu_prop.value,3) )
    sub_cpus_all = []

    # loop through the nodes, we want to refcount the sub-cpu nodes
    # and their parents, we'll delete anything that isn't used later.
    for cpu_phandle, mask, mode in cpu_prop_list:
        cpu_mask = mask
        lopper.log._info( f"cb cpu mask: {hex(cpu_mask)}")

        try:
            cpu_node = tree.pnode(cpu_phandle)
            if not cpu_node:
                lopper.log._debug( f"no cpu found at phandle {hex(cpu_phandle)}, skipping")
                continue
        except:
            # couldn't find the node, skip
            continue

        sub_cpus = tree.subnodes( cpu_node, "cpu@.*" )
        sub_cpus_all = sub_cpus + sub_cpus_all

        lopper.log._info( f"cpu prop phandle: {cpu_phandle}" )
        lopper.log._info( f"cpu node: {cpu_node}" )
        lopper.log._info( f"sub cpus: {sub_cpus}" )

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
                lopper.log._warning( f"{e}" )

    # you can globally check for ref'd cpus after calling this routine
    # via:
    #    ref_nodes = tree.refd( "/cpus.*/cpu.*" )

    return refd_cpus, unrefd_cpus
