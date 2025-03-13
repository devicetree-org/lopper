#/*
# * Copyright (c) 2024 AMD Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import struct
import sys
import types
import os
import getopt
import re
from pathlib import Path
from pathlib import PurePath
from io import StringIO
import contextlib
import importlib
from lopper import Lopper
from lopper import LopperFmt
from lopper.tree import LopperAction
from lopper.tree import LopperTree, LopperNode
from __init__ import LopperSDT
import lopper
import lopper_lib
from itertools import chain
from lopper_lib import chunks
import copy
from collections import OrderedDict
import filecmp

def is_compat( node, compat_string_to_test ):
    if re.search( "assist,domain-v1", compat_string_to_test):
        return assist_reference
    if re.search( "module,assist", compat_string_to_test):
        return assist_reference
    return ""

def overlay_test( sdt ):
    overlay_tree = LopperTree()

    # move the amba_pl node to the overlay
    amba_node = sdt.tree["/amba_pl"]
    new_amba_node = amba_node()
    sdt.tree = sdt.tree - amba_node

    # rename the new node in the overlay
    new_amba_node.name = "&amba"
    new_amba_node.label = ""

    new_amba_node.delete( "ranges" )
    new_amba_node.delete( "compatible" )
    new_amba_node.delete( "#address-cells" )
    new_amba_node.delete( "#size-cells" )

    # move the firmware_name property to the fpga mode, maybe
    # this could be a .move() operation to avoid issues with
    # node identity
    firmware_name = new_amba_node["firmware-name"]
    new_amba_node = new_amba_node - firmware_name
    overlay_tree = overlay_tree + new_amba_node

    fpga_node = LopperNode( name="&fpga" )
    fpga_node = fpga_node + firmware_name

    # move the fpga nodes to the overlay fpga node
    fpga_PR0 = overlay_tree["/&amba/fpga-PR0"]
    fpga_PR1 = overlay_tree["/&amba/fpga-PR1"]
    overlay_tree = overlay_tree - fpga_PR0
    overlay_tree = overlay_tree - fpga_PR1
    fpga_node = fpga_node + fpga_PR0
    fpga_node = fpga_node + fpga_PR1

    ## Note: once you've assigned a node to the tree, it is copied
    ## into a NEW node you can't keep manipulating the old one and
    ## expect it to change in the tree when you print it
    fpga_node.resolve()

    overlay_tree + fpga_node

    overlay_tree.overlay_of( sdt.tree )
    overlay_tree.resolve()

    pl_file = f"{sdt.outdir}/pl-gen.dtsi"
    sdt_file = f"{sdt.outdir}/sdt.dts"

    LopperSDT(None).write( overlay_tree, pl_file, True, True )
    sdt.write( sdt.tree, sdt_file )

    fpga_count = 0
    ranges_count = 0
    with open( pl_file ) as fp:
        for line in fp:
            if re.search( r"&fpga", line ):
                fpga_count += 1
            elif re.search( r"ranges;", line ):
                ranges_count += 1
    if fpga_count == 0:
        print( "ERROR: fpga node is not in the overlay" )
        os._exit(1)
    else:
        print( "PASSED: fpga node is in the overlay")
    if ranges_count == 2:
        print( "PASSED: ranges was removed from the overlay" )
    else:
        print( "FAILED: ranges was not removed from the overlay" )
        os._exit(1)

    amba_count = 0
    with open( sdt_file ) as fp:
        for line in fp:
            if re.search( r"amba_pl", line ):
                amba_count += 1
    if amba_count == 0:
        print( "PASSED: amba_pl was removed from the SDT" )
    else:
        print( "FAILED: amba_pl was removed from the SDT" )
        os._exit(1)


    return True

def domains_access_test( sdt ):
    # test 1: rename a node
    domains = sdt.tree['/domains']
    domains.name = "domains.renamed"

    # TODO: consider making the sync automatic when a name is changed
    #       but we need this for now to lock in the path changes
    sdt.tree.sync()
    
    # TODO: capture the output and check for the name to
    #       be the new one

    # At this point /domains is now /domains.renamed/
    sdt.tree.print()

    # test #2: copy just the leaf node, and then update
    #          it's path to have a currently non-existent
    #          parent node.
    print( "[INFO]: moving: /domains.renamed/openamp_r5 to /domains.new/openamp_r5" )
    openamp_node = sdt.tree['/domains.renamed/openamp_r5'] 

    # This is not advised, but it should still be smething that
    # works. We are renaming the PARENT, and not the leaf node
    # Also: a good citizen would delete the node before adding
    #       the modified one, but we'll detected and fix it up in
    #       add if this common mistake is made.
    openamp_node.abs_path = "/domains.new/openamp_r5"

    sdt.tree.add( openamp_node )

    # optional sync
    sdt.tree.sync()

    # at this point, we have a /domains.renamed with no
    # subnodes. And the openamp node has been moved and
    # a parent created, so we ahve /domains.new with
    # the openamp_r5 node as a subnode.
    sdt.tree.print()

    # test #3: copy the node structure just created and
    #          then rename the node again.
    print( "[INFO]: adding a node, based on an existing one!" )

    # copy the parent node, this also copies the child
    # nodes (openamp_r5).
    even_newer_domains = sdt.tree["/domains.new"]()
    even_newer_domains.name = "domains.added"

    # copy the openamp_r5 node. note, we are throwing this
    # away, but copying it as an extra test of child node
    # identity
    newamp = sdt.tree["/domains.new/openamp_r5"]()

    # this should print a node "domains.added" with an
    # openamp_r5 node that was copied along with the
    # domains.added node
    even_newer_domains.print()

    # change the path of the domains.added node, since
    # we are going to add it to the tree, it needs a new
    # path
    even_newer_domains.abs_path = "/domains.added"

    # note: if you print the even_newer_domains node right
    #       now, it won't have correct attributes. It must
    #       be added to a tree and then resolved to pickup
    #       things like phandles, etc.
    sdt.tree.add(even_newer_domains)

    sdt.tree.resolve()
    sdt.tree.sync()

    # the result. We still have a /donmains.renamed, which
    # is mostly empty. We have a /doains.added and /domains.new
    sdt.tree.print()

    # test #4: delete domains.renamed, domains.added and
    #          rename /domains.new to /domains.final
    domains_renamed = sdt.tree["/domains.renamed"]
    sdt.tree.delete( domains_renamed )
    
    domains_new = sdt.tree["/domains.new"]
    sdt.tree.delete( domains_new )

    # move the node to it's final resting place
    domains_final = sdt.tree["/domains.added" ]
    domains_final.abs_path = "/domains.final"
    domains_final.name = "domains.final"

    domains_final.print()
  
    sdt.tree.add( domains_final )

    sdt.tree.sync()
    sdt.tree.resolve()

    sdt.tree.print()

    domain_node = sdt.tree["/domains.final/openamp_r5"]
    try:
        path = domain_node.path(sanity_check=True)
        print( f"path: {path}" )
    except Exception as e:
        print( "oops: %s" % e )

    print( "[INFO]: copying and adding a new tcm node" )
    new_tcm = sdt.tree["/tcm"]()
    new_tcm.name = "tcm_new"
    new_tcm.abs_path = new_tcm.path()
    sdt.tree.add( new_tcm )

    sdt.tree.sync()
    sdt.tree.resolve()

    try:
        aa = sdt.tree['/'].reorder_child( "/tcm_new", "/tcm", after=True )
    except Exception as e:
        print( "ooops: %s" % e )

    sdt.tree.print()

    # pull the amba node out of the main tree, make it into a dtsi overlay
    new_tree = LopperTree()
    new_tree._type = "dts_overlay"
    amba_node = sdt.tree["/amba"]

    new_amba_node = amba_node()

    # change/clear the label as a stress test
    new_amba_node.name = "&foobar"
    new_amba_node.label = ""

    new_tree + new_amba_node

    fpga_node = LopperNode( name="&fpga" )
    fpga_node["external-fpga-config"] = [""]
    new_tree + fpga_node

    LopperSDT(None).write( new_tree, "/tmp/amba_overlay.dts", True, True )
    LopperSDT(None).write( new_tree, "/tmp/amba_overlay.dtsi", True, True )

    # compare the two files to ensure they are identical, the file extension
    # is not a deciding factor, it is the type of the tree that makes the
    # difference
    if not filecmp.cmp( "/tmp/amba_overlay.dts", "/tmp/amba_overlay.dtsi" ):
        print( "ERROR: /tmp/amba_overlay.dts and /tmp/amba_overlay.dtsi have differences" )
        os._exit(1)

    # remove the amba node from the original SDT
    sdt.tree - amba_node

    LopperSDT(None).write( sdt.tree, "/tmp/sdt_no_amba.dts", True, True )
    count = 0
    with open("/tmp/sdt_no_amba.dts") as fp:
        for line in fp:
            if re.search( r"amba_foo: amba", line ):
                count += 1
    if count != 0:
        print( "ERROR: amba node was not remmoved from the SDT" )
        os._exit(1)

    return True

def assist_reference( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    # try:
    #     sdt.tree.print()
    # except Exception as e:
    #     print( "Error: %s" % e )
    #     return 0

    print ( f"[INFO]: starting assist_reference run {options}" )
    try:
        args = options['args']
        if "overlay_test" in args:
            overlay_test( sdt )
            return True
        else:
            domains_access_test( sdt )
    except:
        pass
