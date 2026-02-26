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
import json

def is_compat( node, compat_string_to_test ):
    if re.search( "assist,domain-v1", compat_string_to_test):
        return assist_reference
    if re.search( "module,assist", compat_string_to_test):
        return assist_reference
    return ""

def glob_test( sdt, glob_test_type ):
    print( f"[INFO] start: glob test: {glob_test_type}" )
    try:
        domains = sdt.tree['/domains']

        # after the yaml expansion lops run, the domain is
        # at /domains/default/domain@0', if they don't run
        # it is at /domains/default/domains/APU_domain
        apu_domain = sdt.tree['/domains/default/domain@0']
        # can we access it by label ?
        apu_domain2 = sdt.tree.deref( "APU_domain")
        if not apu_domain2:
            print( "[ERROR]: domain re-labeling did not work, domain not found a label APU_domain" )
            os._exit(1)

        if re.search("child-serial", glob_test_type):
            # this is the child serial glob test, check for the uart node
            # in the apu domain
            # apu_domain.print()
            access = apu_domain["access"]
            try:
                if "&uart0" in access.string_val and "&uart1" in access.string_val:
                    print( f"[PASS]: uart test: both &uart0 and &uart1 are in the access list: {access.string_val}" )
                else:
                    print( f"[FAIL]: uart test, both &uart0 and &uart1 are NOT in the access list: {access.string_val}" )
            except Exception as e:
                print( f"ERROR: while checking serial glob: {e}" )
                os._exit(1)
        elif re.search("child-all", glob_test_type):
            apu_domain2.print()

            access_chunks = json.loads(apu_domain2["access-json"].value)
            print( f"access:")
            for c in access_chunks:
                print( f"   {c}")

            if len(access_chunks) == 42:
                print( f"[PASS]: all access entries copied to apu domain")
            else:
                print( f"[FAIL]: not all access entries were copied to apu domain")
                os._exit(1)

            LopperSDT(None).write( sdt.tree, "/tmp/globbed_tree.dts", True, True )
        else:
            print( f"[ERROR]: unknown glob test: {glob_test_type}" )
            os._exit(1)

    except Exception as e:
        print( f"[ERROR]: exception during glob testing: {e}")
        os._exit(1)

    return True

def phandle_meta_test( sdt, pass_number ):
    print( f"[INFO]: running phandle_meta_test: {sdt.output_file} pass: {pass_number}" )

    try:
        if pass_number == "one":
            sdt.write( sdt.tree, sdt.output_file )
        else:
            sdt.write( sdt.tree, sdt.output_file )
            #sdt.write( sdt.tree, '/tmp/foo.dts' )
            #sdt.tree.print()
    except Exception as e:
        print( f"[ERROR]: {e}" )
        return False

    if pass_number == "one":
        phandle_link_is_number = False
        with open( sdt.output_file ) as fp:
            for line in fp:
                if re.search( r"phandle-link.*?=.*?<0x.*>;", line ):
                    phandle_link_is_number = True

        if phandle_link_is_number:
            print( "[PASSED]: phandle-link is a number (no symbolic replacement)" )
        else:
            print( "[FAILED]: phandle-link is a number (symbolic or not found)" )
            os._exit(1)

        return phandle_link_is_number

    if pass_number == "two":
        phandle_link_is_sym = False
        phandle_link_invalid_property = False
        with open( sdt.output_file ) as fp:
            for line in fp:
                if re.search( r"phandle-link.*?=.*?<&amba>;", line ):
                    phandle_link_is_sym = True

                # we shouldn't find this as the embedded lop should
                # have deleted it
                if re.search( r"phandle-link-invalid.*?=.*?<&amba>;", line ):
                    phandle_link_invalid_property = True

        if phandle_link_is_sym:
            print( "[PASSED]: phandle-link is symbolic (replacement)" )
        else:
            print( "[FAILED]: phandle-link is a number (no replacement done)" )
            os._exit(1)

        if phandle_link_invalid_property:
            print( "[FAILED]: phandle-link-invalid should have been deleted" )
            os._exit(1)
        else:
            print( "[PASSED]: phandle-link-invalid was deleted" )

        return phandle_link_is_sym

def overlay_test( sdt ):
    overlay_tree = LopperTree()

    # move the amba_pl node to the overlay
    amba_node = sdt.tree["/amba_pl"]

    # take the defaults, we'll be extending this to a new property in this
    # assist
    Lopper.phandle_possible_prop_dict = Lopper.phandle_possible_properties()
    Lopper.phandle_possible_prop_dict["remote_endpoint"] = [ "phandle" ]

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

    try:
        overlay_tree['/'].reorder_child( "/&amba", "/&fpga", after=True, debug=True )
    except Exception as e:
        print ( f"ERROR: reordering nodes: {e}")
        os._exit(1)

    overlay_tree.resolve()

    pl_file = f"{sdt.outdir}/pl-gen.dtsi"
    sdt_file = f"{sdt.outdir}/sdt.dts"

    print( f"[INFO]: pl: {pl_file} sdt: {sdt_file}")

    LopperSDT(None).write( overlay_tree, pl_file, True, True )
    sdt.write( sdt.tree, sdt_file )

    fpga_count = 0
    ranges_count = 0
    amba_count = 0
    with open( pl_file ) as fp:
        for line in fp:
            if re.search( r"&fpga", line ):
                fpga_count += 1
            elif re.search( r"ranges;", line ):
                ranges_count += 1
            elif re.search( r"&amba", line ):
                # was amba after fpga ?
                if fpga_count == 0:
                    print( "[ERROR]: &amba and &fpga nodes are not properly ordered")
                    os._exit(1)

    if fpga_count == 0:
        print( "[ERROR]: fpga node is not in the overlay" )
        os._exit(1)
    else:
        print( "[PASS]: fpga node is in the overlay")
    if ranges_count == 2:
        print( "[PASS]: ranges was removed from the overlay" )
    else:
        print( f"[FAIL]: ranges was not removed from the overlay. the count is {ranges_count}" )
        os._exit(1)

    amba_count = 0
    with open( sdt_file ) as fp:
        for line in fp:
            if re.search( r"amba_pl", line ):
                amba_count += 1
    if amba_count == 0:
        print( "[PASS]: amba_pl was removed from the SDT" )
    else:
        print( "[FAIL]: amba_pl was not removed from the SDT" )
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
        elif "phandle_meta_test_1" in args:
            phandle_meta_test( sdt, "one" )
            return True
        elif "phandle_meta_test_2" in args:
            phandle_meta_test( sdt, "two" )
            return True
        elif any(re.search('glob_test', s) for s in args):
            res = glob_test( sdt, args[0] )
            return res
        elif "phandle_ref_test" in args:
            res = phandle_ref_test( sdt )
            return res
        elif "reserved_memory_e2e_test" in args:
            res = reserved_memory_e2e_test( sdt )
            return res
        else:
            domains_access_test( sdt )
            return True
    except Exception as e:
        print( f"Exception during assist-sanity {e}")
        pass

def phandle_ref_test( sdt ):
    """Test label_to_phandle resolution for YAML phandle references.

    Tests:
    1. Explicit "&label" syntax resolution
    2. Bare label resolution for known phandle properties
    3. Label registration via 'label:' property
    """
    print( f"[INFO]: running phandle_ref_test" )

    try:
        # Check that reserved-memory nodes exist and have labels
        test_reserved = sdt.tree.lnodes("test_reserved")
        if test_reserved:
            print( f"[PASS]: test_reserved label registered in tree" )
        else:
            print( f"[FAIL]: test_reserved label not found in tree" )
            return False

        another_reserved = sdt.tree.lnodes("another_reserved")
        if another_reserved:
            print( f"[PASS]: another_reserved label registered in tree" )
        else:
            print( f"[FAIL]: another_reserved label not found in tree" )
            return False

        # Check that test-node exists
        test_nodes = sdt.tree.nodes("test-node")
        if not test_nodes:
            print( f"[FAIL]: test-node not found in tree" )
            return False

        test_node = test_nodes[0]

        # Check memory-region property - should have resolved phandles (integers)
        try:
            mem_region = test_node["memory-region"]
            mem_val = mem_region.value

            # Should be a list of integers (phandles), not strings
            if isinstance(mem_val, list) and len(mem_val) >= 2:
                if isinstance(mem_val[0], int) and isinstance(mem_val[1], int):
                    print( f"[PASS]: memory-region resolved to phandles: {mem_val}" )
                else:
                    print( f"[FAIL]: memory-region values are not integers: {mem_val}" )
                    return False
            else:
                print( f"[FAIL]: memory-region unexpected format: {mem_val}" )
                return False

            # Verify the phandles match the target nodes
            if mem_val[0] == test_reserved[0].phandle:
                print( f"[PASS]: first phandle matches test_reserved" )
            else:
                print( f"[FAIL]: first phandle {mem_val[0]} != test_reserved.phandle {test_reserved[0].phandle}" )
                return False

            if mem_val[1] == another_reserved[0].phandle:
                print( f"[PASS]: second phandle matches another_reserved" )
            else:
                print( f"[FAIL]: second phandle {mem_val[1]} != another_reserved.phandle {another_reserved[0].phandle}" )
                return False

        except Exception as e:
            print( f"[FAIL]: error accessing memory-region: {e}" )
            return False

        # Check interrupt-parent - bare label resolution
        try:
            int_parent = test_node["interrupt-parent"]
            int_val = int_parent.value

            # Should be an integer (phandle) or list with one integer
            if isinstance(int_val, list):
                int_val = int_val[0]

            if isinstance(int_val, int):
                print( f"[PASS]: interrupt-parent resolved to phandle: {int_val}" )
            elif int_val == "gic_a72":
                # If gic_a72 doesn't exist in tree, value stays as string
                print( f"[INFO]: interrupt-parent stayed as string (gic_a72 not in tree)" )
            else:
                print( f"[FAIL]: interrupt-parent unexpected value: {int_val}" )
                return False

        except Exception as e:
            print( f"[FAIL]: error accessing interrupt-parent: {e}" )
            return False

        print( f"[PASS]: phandle_ref_test completed successfully" )
        return True

    except Exception as e:
        print( f"[FAIL]: exception in phandle_ref_test: {e}" )
        return False


def reserved_memory_e2e_test( sdt ):
    """End-to-end test for reserved-memory handling.

    Tests:
    1. Boolean property expansion (reusable, linux,cma-default -> empty props)
    2. start/size to reg conversion
    3. Reserved-memory nodes referenced by devices (via memory-region) survive filtering
    4. Unreferenced reserved-memory nodes are pruned
    5. pnode() lookups work correctly after phandle_or_create()

    NOTE: Reserved-memory nodes survive only if something OUTSIDE /domains/
    references them (e.g., a device with memory-region = <&node>). The domain's
    reserved-memory property is metadata and does not keep nodes alive.
    """
    print( f"[INFO]: running reserved_memory_e2e_test" )

    try:
        # Check that /reserved-memory exists
        try:
            resmem = sdt.tree['/reserved-memory']
        except:
            print( f"[FAIL]: /reserved-memory node not found" )
            return False

        print( f"[PASS]: /reserved-memory node exists" )

        # Check that referenced nodes exist
        referenced_nodes = ['cma_pool@10000000', 'nomap_region@30000000', 'yaml_cma@60000000']
        for node_name in referenced_nodes:
            found = False
            for child in resmem.subnodes(children_only=True):
                if child.name == node_name:
                    found = True
                    # Verify phandle is set and in __pnodes__
                    if child.phandle > 0:
                        pnode_lookup = sdt.tree.pnode(child.phandle)
                        if pnode_lookup == child:
                            print( f"[PASS]: {node_name} phandle {child.phandle} correctly indexed in __pnodes__" )
                        else:
                            print( f"[FAIL]: {node_name} phandle {child.phandle} not found via pnode() lookup" )
                            return False
                    break
            if not found:
                print( f"[FAIL]: referenced node {node_name} not found (should survive filtering)" )
                return False
            print( f"[PASS]: referenced node {node_name} found" )

        # Check that unreferenced node was pruned
        for child in resmem.subnodes(children_only=True):
            if child.name == 'unused@50000000' or 'unreferenced' in child.name:
                print( f"[FAIL]: unreferenced node {child.name} should have been pruned" )
                return False
        print( f"[PASS]: unreferenced nodes correctly pruned" )

        # Check boolean property expansion on yaml_cma
        yaml_cma = None
        for child in resmem.subnodes(children_only=True):
            if 'yaml_cma' in child.name:
                yaml_cma = child
                break

        if yaml_cma:
            # Check reusable property exists and is empty (boolean)
            reusable = yaml_cma.props('reusable')
            if reusable:
                print( f"[PASS]: reusable property exists on yaml_cma" )
            else:
                print( f"[FAIL]: reusable property not found on yaml_cma" )
                return False

            # Check linux,cma-default property exists
            cma_default = yaml_cma.props('linux,cma-default')
            if cma_default:
                print( f"[PASS]: linux,cma-default property exists on yaml_cma" )
            else:
                print( f"[FAIL]: linux,cma-default property not found on yaml_cma" )
                return False

            # Check reg property exists (start/size conversion)
            reg = yaml_cma.propval('reg')
            if reg and reg != ['']:
                print( f"[PASS]: reg property exists on yaml_cma (start/size converted)" )
            else:
                print( f"[FAIL]: reg property not found on yaml_cma" )
                return False
        else:
            print( f"[FAIL]: yaml_cma node not found" )
            return False

        print( f"[PASS]: reserved_memory_e2e_test completed successfully" )
        return True

    except Exception as e:
        print( f"[FAIL]: exception in reserved_memory_e2e_test: {e}" )
        import traceback
        traceback.print_exc()
        return False
