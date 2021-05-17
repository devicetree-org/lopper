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
import humanfriendly

def is_compat( node, compat_string_to_test ):
    if re.search( "module,subsystem", compat_string_to_test):
        return subsystem
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
        node.sync()

def val_as_bool( val ):
    if val == "False":
        return False
    elif val == "True":
        return True

def firewall_expand( tree, subnode, verbose = 0 ):
    try:
        firewall_domain = subnode["domain"][0]
    except:
        firewall_domain = None

    try:
        firewall_block = subnode["block"][0]
        try:
            firewall_block = int(firewall_block)
        except Exception as e:
            pass
    except:
        firewall_block = 0

    if verbose:
        print( "[DBG]: firewall expand: %s cfg: domain: %s block: %s" % (subnode.abs_path,firewall_domain,firewall_block))

    # The first cell is a link to a node of a bus mastering device (or a domain).
    #
    # The second cell is the action, values can be allow (1), block (0), and block-desirable (2):
    #
    #  block [0]: access is blocked
    #  allow [1]: access is allowed
    #  block-desirable [2]: "block if you can"
    #
    # The third cell is a priority number: the priority of the rule when block-desirable is specified, otherwise unused.

    if firewall_block and not firewall_domain:
        if verbose:
            print( "[DBG]: firewall: block and no domain, generating firewallconf-default" )

        # the first item is "block" (0) and the second is the priority (default 0), so
        # we use <0 0>
        firewall_prop = LopperProp( "firewallconfig-default", -1, subnode.parent, [ 0, 0 ] )
        subnode.parent + firewall_prop
        # delete our node, it has been converted to a property
        subnode.tree - subnode

    elif firewall_block and firewall_domain:
        if verbose:
            print( "[DBG]: firewall: block and domain, generating firewallconf" )

        try:
            tgt_node = tree.lnodes( firewall_domain )[0]
        except:
            if verbose:
                print( "[DBG]: WARNING: could not find node %s" % firewall_domain )
            tgt_node = None

        firewall_priority = 0

        tgt_node_phandle = 0xdeadbeef
        if tgt_node:
            if tgt_node.phandle == 0:
                tgt_node_phandle = tgt_node.phandle_or_create()
                if verbose:
                    print( "[DBG]: generated phandle %s for node: %s" % (tgt_node.phandle,tgt_node.abs_path ))

        if firewall_block:
            if type(firewall_block) == int:
                # print( " ID for firewall block" )
                # priority was passed
                firewall_priority = firewall_block
                # block
                firewall_block = 0
            else:
                # print( "string for firewall block" )
                if re.search( "always", firewall_block ):
                    firewall_block = 0
                elif re.search( "never", firewall_block ):
                    firewall_block = 1


        firewall_prop = LopperProp( "firewallconf", -1, subnode.parent, [ tgt_node_phandle, firewall_block, firewall_priority ] )
        subnode.parent + firewall_prop
        # delete our node, it has been converted to a property
        subnode.tree - subnode




def access_expand( tree, subnode, verbose = 0 ):
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
    access_props = subnode.props( "access" )
    # the loop below used to use json.loads, which always returns a list,
    # so we drop this in a list to avoid needing to change for the property
    # direct access case.
    access_chunks = [access_props[0][0]]
    access_list = []

    ap = access_props[0]
    x,field_count = ap.phandle_params()

    try:
        # is there a property that gives us a field count hint ?
        access_field_count = subnode['#access-flags-cells']
        # if it isn't a list, make it one (using the fact that any direct
        # assignming to a property.value is made into a list
        if not type(access_field_count.value) == list:
            access_field_count.value = access_field_count.value

        access_field_count = access_field_count.value[0]
    except Exception as e:
        access_field_count = field_count

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
                    dev_node.phandle_or_create()
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
        if access_field_count > 2:
            for i in range(2,access_field_count):
                access_list.append( 0xff )

    if verbose:
        # dump the memory as hex
        print( '[DBG] setting access: [{}]'.format(', '.join(hex(x) for x in access_list)) )

    ap.value = access_list


def memory_expand( tree, subnode, memory_start = 0xbeef, verbose = 0 ):
    # /*
    # * 1:1 map, it should match the memory regions
    # * specified under access below.
    # *
    # * It is in the form:
    # * memory = <address size address size ...>
    # */
    try:
        mem = [subnode.props( "memory" )[0][0]]
        mem_list = []
        for m in mem:
            try:
                start = str(m['start'])
            except:
                start = str(int(memory_start))
            try:
                size = str(m['size'])
            except:
                size = str(int(0xbeef))

            #print( "memory expand: start/size as read: %s/%s" % (start,size))
            start = humanfriendly.parse_size( start, True )
            size = humanfriendly.parse_size( size, True )
            #print( "memory expand: start/size as converted: %s/%s" % (start,size))

            mem_list.append(int(start))
            mem_list.append(int(size))

    except Exception as e:
        mem_list = [0xdead, 0xffff ]

    if verbose:
        # dump the memory as hex
        print( '[DBG] memory: [{}]'.format(', '.join(hex(x) for x in mem_list)) )

    property_set( "memory", mem_list, subnode )


def cpu_expand( tree, subnode, verbose = 0):
    ## cpu processing
    cpus = subnode.props( "cpus" )

    cpus_chunks = [cpus[0][0]]
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
            try:
                cluster_node = tree.nodes( cluster )[0]
            except:
                cluster_node = None

        if cluster_node:
            if cluster_node.phandle == 0:
                ph = cluster_node.phandle_or_create()
                if verbose:
                    print( "[DBG]: subsystem assist: generated phandle %s for node: %s" % (cluster_node.phandle,cluster_node.abs_path ))

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
        # property_set( "cpus", cpus_list, subnode )
        cpus[0].value = cpus_list

# sdt: is the system device tree
def subsystem( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    try:
        args = options['args']
    except:
        args = []

    if verbose:
        print( "[INFO]: cb: subsystem( %s, %s, %s, %s )" % (tgt_node, sdt, verbose, args))

    if "generate" in args or "--generate" in args:
        subsystem_generate( tgt_node, sdt, verbose )
    else:
        subsystem_expand( tgt_node, sdt, verbose )

    return True


# sdt: is the system device tree
def subsystem_generate( tgt_node, sdt, verbose = 0):
    if verbose:
        print( "[INFO]: cb: subsystem_generate( %s, %s )" % (tgt_node, sdt))

    tree = sdt.tree
    domain_tree = LopperTree()

    try:
        domain_node = tree["/domains"]
    except:
        domain_node = LopperNode( -1, "/domains" )

    domain_tree.__dbg__ = 4
    domain_tree = domain_tree + domain_node

    subsystem_node = LopperNode( -1 )
    subsystem_node.name = "subsystem1"

    domain_node + subsystem_node

    cpu_prop = None
    for node in sdt.tree:
        try:
            compatibility = node['compatible']
        except:
            compatibility = None

        if compatibility:
            cpu_compat = re.findall(r"(?=("+'|'.join(compatibility.value)+r"))", "cpus,cluster")
            if cpu_compat:
                if not cpu_prop:
                    # Note: The "mask" and "secure" entries are currently placeholders and will be
                    #       calculated differently in the future.
                    cpu_prop = LopperProp( "cpus", -1, subsystem_node,
                                           [ json.dumps( { 'cluster': node.label, "cpu_mask" : 0x3, "mode": { 'secure': True } }) ])
                    cpu_prop.pclass = "json"
                    subsystem_node = subsystem_node + cpu_prop
                else:
                    cpu_prop.value.append( json.dumps( { 'cluster': node.label, "cpu_mask" : 0x3, "mode": { 'secure': True } } ) )

    if verbose > 3:
        tree.__dbg__ = 4

    tree = tree + domain_node

    if verbose > 2:
        print( "[DBG++]: dumping yaml generated default subystem" )
        yaml = LopperYAML( None, domain_tree )
        yaml.to_yaml()

    return True

def subsystem_expand( tgt_node, sdt, verbose = 0 ):
    if verbose:
        print( "[INFO]: cb: subsystem_expand( %s, %s )" % (tgt_node, sdt))

    tree = sdt.tree

    try:
        domain_node = tree["/domains"]
    except:
        domain_node = LopperNode( -1, "/domains" )

    # add the cells properties
    property_set( "#address-cells", 2, domain_node )
    property_set( "#size-cells", 2, domain_node )
    domain_node.sync()

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
            cpu_expand( tree, subnode, verbose )

            ## memory processing
            memory_expand( tree, subnode, verbose )

            ## access processing
            access_expand( tree, subnode, verbose )

            subnode.sync()

        domain_count += 1

    return True
