#/*
# * Copyright (c) 2022 AMD Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import sys
import types
import os
import getopt
import re
import logging
from pathlib import Path
from pathlib import PurePath
from lopper import Lopper
from lopper import LopperFmt
from lopper.tree import LopperAction
from lopper.tree import LopperTree
from lopper.tree import LopperNode
from lopper.tree import LopperProp
import lopper
import lopper.log

lopper.log._init(__name__)

def is_compat( node, compat_string_to_test ):
    if re.search( "module,extract$", compat_string_to_test):
        return extract
    return ""

def usage():
    print( """
   Usage: extract -t <target node> [OPTION]

      -t       target node (full path)
      -i       include node if found in extracted node paths
      -p       permissive matching on target node (regex)
      -v       enable verbose debug/processing
      -x       exclude nodes or properties matching regex
      -o       output file for extracted device tree

    """)

def extract( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    try:
        args = options['args']
    except:
        args = []

    opts,args2 = getopt.getopt( args, "i:pvt:o:x:", [ "verbose", "permissive" ] )

    if opts == [] and args2 == []:
        usage()
        sys.exit(1)

    permissive=False
    exclude_list=[]
    include_list=[]
    output=None
    target_node_name=None
    for o,a in opts:
        # print( "o: %s a: %s" % (o,a))
        if o in ('-x'):
            exclude_list.append( a )
        if o in ('-i'):
            include_list.append( a )
        elif o in ('-o'):
            output=a
        elif o in ('-t'):
            target_node_name=a
        elif o in ('-v', "--verbose"):
            verbose = verbose + 1
        elif o in ('-p', "--permissive"):
            permissive = True

    if verbose > 3:
        desired_level = lopper.log.TRACE2
    elif verbose > 2:
        desired_level = lopper.log.TRACE
    elif verbose > 1:
        desired_level = logging.DEBUG
    elif verbose > 0:
        desired_level = logging.INFO
    else:
        desired_level = None

    if desired_level is not None:
        lopper.log._level(desired_level, __name__)

    lopper.log._info( f"cb: extract( {tgt_node}, {sdt}, {verbose}, {args} )" )

    if not target_node_name:
        lopper.log._error( "no target node provided" )
        usage()
        sys.exit(1)

    tgt_nodes = []
    try:
        tgt_nodes.append(sdt.tree[target_node_name])
    except:
        if permissive:
            tgt_nodes = sdt.tree.nodes( target_node_name )

    if not tgt_nodes:
        lopper.log._error( f"node {target_node_name} not found in tree" )
        if verbose > 0 or lopper.log._is_enabled(logging.INFO):
            sdt.tree.print()
        sys.exit(1)

    lopper.log._info( f"target node(s) {[y.abs_path for y in tgt_nodes]} found" )

    extracted_tree = LopperTree()

    extracted_tree_root = extracted_tree["/"]
    extracted_tree_root["#address-cells"] = 2
    extracted_tree_root["#size-cells"] = 2

    ##
    ##    - pass "interrupt-parent" as a node/property to ignore on the
    ##      command line, since we don't want it in xen
    ##
    extracted_container_node = LopperNode( -1, "/extracted" )
    extracted_container_node["compatible"] = "simple-bus"
    extracted_container_node["ranges"] = None;
    extracted_container_node["#address-cells"] = 2
    extracted_container_node["#size-cells"] = 2

    extracted_tree = extracted_tree + extracted_container_node

    for n in tgt_nodes:
        node_refs = n.resolve_all_refs( parents=False )
        refd_paths = [y.abs_path for y in node_refs]
        lopper.log._info( f"Extracted node {n.abs_path} refs: {refd_paths}" )

        for r in node_refs:
            # Don't add the parent, just ourself + phandle refs
            if r == n.parent:
                lopper.log._info( f"skipping parent of target node: {r}" )
                pass
            else:
                copy_node = True
                for exclude in exclude_list:
                    if re.search( exclude, r.name ):
                        lopper.log._info( f"skipping node (matched exclude): {r.abs_path}" )
                        copy_node = False

                if copy_node:
                    # TODO: this should look all the way up, for for now, we are
                    #       just looking one path up for the include check
                    if include_list:
                        matching_parent = None
                        if r.parent.abs_path != "/":
                            #print( "checking include list" )
                            for i in include_list:
                                if re.search( i, r.parent.name ):
                                    matching_parent = r.parent

                        if matching_parent:
                            extracted_node_copy = matching_parent()
                            for subn in extracted_node_copy.subnodes( children_only = True ):
                                if subn.name != r.name:
                                    # print( "tring to delete %s" % subn.name )
                                    extracted_node_copy - subn
                        else:
                            extracted_node_copy = r()
                    else:
                        lopper.log._info( f"adding {r}: to /extracted" )
                        extracted_node_copy = r()
                    extracted_container_node + extracted_node_copy
                    extracted_node_copy["extracted,path"] = r.abs_path

                # TODO: you are here. We also need to check the exclude list for
                #       properties, and remove them from the copied node. For now
                #       we have the same flag (-x) for nodes and properites, and may
                #       need to split it, or say that a node must have "/.*<name>"
                #       which is probably better than the split argument
                for p in extracted_node_copy:
                    for exclude in exclude_list:
                        if re.search( exclude, p.name ):
                            lopper.log._info( f"dropping masked property {p.name}" )
                            extracted_node_copy - p


    extracted_tree.strict = False
    extracted_tree.resolve()

    sdt.subtrees["extracted"] = extracted_tree

    if output:
        sdt.write( extracted_tree, output, True, True )

    return True
