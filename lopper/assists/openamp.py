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

sys.path.append(os.path.dirname(__file__))
from openamp_xlnx import xlnx_openamp_rpmsg_expand
from openamp_xlnx import xlnx_openamp_remoteproc_expand
from openamp_xlnx import xlnx_openamp_parse

def is_compat( node, compat_string_to_test ):
    if re.search( "openamp,domain-v1", compat_string_to_test):
        return process_domain
    if re.search( "openamp,domain-processing", compat_string_to_test):
        return openamp_parse
    if re.search( "module,openamp", compat_string_to_test):
        return openamp_parse
    return ""

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False

def chunks(l, n):
    # For item i in a range that is a length of l,
    for i in range(0, len(l), n):
        # Create an index range for l of n items:
        yield l[i:i+n]


def openamp_remoteproc_expand(tree, subnode, verbose = 0 ):
    # Generic OpenAMP expansion subroutine which selects the applicable
    # vendor method to use for Remoteproc YAML expansion
    for i in tree["/"]["compatible"].value:
        if "xlnx" in i:
            return xlnx_openamp_remoteproc_expand(tree, subnode, verbose)
    return True


def openamp_rpmsg_expand(tree, subnode, verbose = 0 ):
    # Generic OpenAMP expansion subroutine which selects the applicable
    # vendor method to use for RPMsg YAML expansion
    for i in tree["/"]["compatible"].value:
        if "xlnx" in i:
            return xlnx_openamp_rpmsg_expand(tree, subnode, verbose)

    return True

openamp_d_to_d_compat_strings = {
    "openamp,rpmsg-v1" : openamp_rpmsg_expand,
    "openamp,remoteproc-v1" : openamp_remoteproc_expand,
}

def is_openamp_d_to_d(tree, subnode, verbose = 0 ):
    for n in subnode.subnodes():
        if len(n["compatible"]) == 1 and n["compatible"][0]  in openamp_d_to_d_compat_strings.keys():
            return True
    return False

def openamp_d_to_d_expand(tree, subnode, verbose = 0 ):
    # landing function for generic YAML expansion of
    # domain-to-domain property
    for n in subnode.subnodes():
        if len(n["compatible"]) == 1 and n["compatible"][0]  in openamp_d_to_d_compat_strings.keys():
            return openamp_d_to_d_compat_strings[n["compatible"][0]](tree, n, verbose)

    return False


def openamp_process_cpus( sdt, domain_node, verbose = 0 ):
    if verbose > 1:
        print( "[DBG+]: openamp_process_cpus" )

    try:
        cpu_prop_values = domain_node['cpus'].value
    except:
        print( "[ERROR]: domain node does not have a cpu link" )
        sys.exit(1)

    cpu_prop_list = list( chunks(cpu_prop_values,3) )
    sub_cpus_all = []

    # loop through the nodes, we want to refcount the sub-cpu nodes
    # and their parents, we'll delete anything that isn't used later.
    for cpu_phandle, mask, mode in cpu_prop_list:
        cpu_mask = mask
        if verbose:
            print( "[INFO]: cb cpu mask: %s" % hex(cpu_mask))

        try:
            cpu_node = sdt.tree.pnode(cpu_phandle)
        except:
            # couldn't find the node, skip
            continue

        sub_cpus = sdt.tree.subnodes( cpu_node, "cpu@.*" )
        sub_cpus_all = sub_cpus + sub_cpus_all

        if verbose:
            print( "[INFO]: cpu prop phandle: %s" % cpu_phandle )
            print( "[INFO]: cpu node: %s" % cpu_node )
            print( "[INFO]: sub cpus: %s" % sub_cpus )

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
                    sdt.tree.ref_all( sub_cpu_node, True )
                except:
                    pass

    # now we do two types of refcount delete
    #   - on the cpu clusters
    #   - on the cpus within a cluster
    ref_nodes = sdt.tree.refd( "/cpus.*/cpu.*" )
    if verbose:
        print( "[INFO]: openamp: referenced cpus are: %s" % ref_nodes )
        for r in ref_nodes:
            print( "         %s" % r.abs_path )

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
        print( "[INFO]: filtering on:\n------%s\n-------\n" % code )

    # the action will be taken if the code block returns 'true'
    # Lopper.node_filter( sdt, xform_path, LopperAction.DELETE, code, verbose )
    sdt.tree.filter( xform_path, LopperAction.DELETE, code, None, verbose )

    for s in sub_cpus_all:
        if s not in ref_nodes:
            try:
                sdt.tree.delete( s )
            except Exception as e:
                print( "[WARNING]: %s" % e )


def openamp_parse(root_node, tree, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    for i in root_node["compatible"].value:
        if "xlnx" in i:
            return xlnx_openamp_parse(tree, options, verbose)

    return False


# all the logic for applying a openamp domain to a device tree.
# this is a really long routine that will be broken up as more examples
# are done and it can be propery factored out.
def process_domain( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    if verbose:
        print( "[INFO]: cb: process_domain( %s, %s, %s )" % (tgt_node, sdt, verbose))

    domain_node = sdt.tree[tgt_node]

    sdt.tree.ref( 0 )

    openamp_process_cpus( sdt, domain_node, verbose )

    # lets track any nodes that are referenced by access parameters. We use this
    # for a second patch to drop any nodes that are not accessed, and hence should
    # be removed
    node_access_tracker = {}
    # we want to track child nodes of "/", if they are of type simple-bus AND they are
    # referenced by a <access> value in the domain
    node_access_tracker['/'] = [ "/", "simple-bus" ]

    sdt.tree.ref( 0 )

    # do not consider address-map phandles as references
    all_refs = domain_node.resolve_all_refs( [ ".*address-map.*" ] )
    for n in all_refs:
        n.ref = 1

    # "access" is a list of tuples: phandles + flags
    access_list = []
    try:
        access_list = domain_node['access'].value
    except:
        pass

    if not access_list:
        if verbose:
            print( "[INFO]: no access list found, skipping ..." )
    else:
        # although the access list is decoded as a list, it is actually tuples, so we need
        # to get every other entry as a phandle, not every one.
        for ph in access_list[::2]:
            anode = sdt.tree.pnode( ph )
            if anode:
                # the phandle was found
                node_type = anode.type[0]
                node_name = anode.name
                node_parent = anode.parent
            else:
                 # the phandle wasn't valid. Skip to the next access list item
                if verbose > 0:
                    print( "[DBG+]: WARNING: access item not found by phandle" )

                continue

            if re.search( "simple-bus", node_type ):
                if verbose > 1:
                    print( "[INFO]: access is a simple-bus (%s), leaving all nodes" % node_name)
                # refcount the bus (this node)
                anode.ref = 1
            else:
                # The node is *not* a simple bus, so we must do more processing
                # a) If the node parent is something other than zero, the node is nested, so
                #    we have to do more processing.
                #    Note: this should be recursive eventually, but for now, we keep it simple
                # print( "node name: %s node parent: %s" % (node_name, node_parent) )
                if node_parent:
                    parent_node_type = node_parent.type[0]
                    parent_node_name = node_parent.name
                    node_grand_parent = node_parent.parent
                    if not parent_node_type:
                        # is it a special name ? .. if it is, we'll give it a type to normalize the
                        # code below
                        if re.search( "reserved-memory", parent_node_name ):
                            parent_node_type = "reserved-memory"
                        else:
                            # if there's no type and no special name, we need to bail
                            continue

                    # if the parent is a simple bus, then something within the bus had an
                    # <access>. We need to refcount and delete anything that isn't accessed.
                    if re.search( "simple-bus", parent_node_type ):
                        # refcount the bus
                        if not node_parent.abs_path in node_access_tracker:
                            node_access_tracker[node_parent.abs_path] = [ node_parent.abs_path, "*" ]

                        if verbose > 1:
                            print( "[INFO]: node's (%s)  parent is a simple-bus (%s), dropping sibling nodes" % (anode.abs_path, parent_node_name))

                        sdt.tree.ref_all( anode, True )
                    elif re.search( "reserved-memory", parent_node_type ):
                        if verbose > 1:
                            print( "[INFO]: reserved memory processing for: %s" % node_name)

                        if not node_parent.abs_path in node_access_tracker:
                            node_access_tracker[node_parent.abs_path] = [ node_parent.abs_path, "*" ]

                        # Increment a reference to the current node, since we've added the parent node
                        # to a list of nodes that we'll use to check for referenced children later. Anything
                        # with no reference, will be removed.
                        anode.ref = 1

        # filter #1:
        #    - starting at /
        #    - drop any unreferenced nodes that are of type simple-bus

        # filter #2:
        #    - starting at simple-bus nodes
        #    - drop any unreferenced elements

        # filter #3:
        #    - starting at reserved memory parent
        #    - drop any unreferenced elements

        for n, value in node_access_tracker.values():
            # xform_path is the path to the node that was tracked, so this is a potential
            # delete to any children of that node, that haven't been accessed. If you
            # started from / .. you could delete a lot of nodes by mistake, so be careful!

            xform_path = n
            if value == "*":
                code = """
                       p = node.ref
                       if p <= 0:
                           return True
                       else:
                           return False
                       """
            else:
                prop = value
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
                print( "[INFO]: filtering on:\n------%s\n-------\n" % code )

            # the action will be taken if the code block returns 'true'
            if n == "/":
                sdt.tree.filter( "/", LopperAction.DELETE, code, None, verbose )
            else:
                sdt.tree.filter( n + "/", LopperAction.DELETE, code, None, verbose )

    # we must sync the tree, since its numbering may have changed due to the
    # node_filter deleting things
    sdt.tree.sync()

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
        print( "[INFO]: memory property: %s" % memory_hex )

    # 1) find if there's a top level memory node
    try:
        memory_node = sdt.tree["/memory@.*"]
    except:
        memory_node = None

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

        # TODO: this seems to be shortening up the system memory node. Check to see
        #       if the openamp node is being propery interpreted

        try:
            memory_node['reg'].value = memory_int
        except:
            pass

    return True


