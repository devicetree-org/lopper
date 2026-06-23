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
import copy
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

sys.path.append( os.path.dirname(__file__) )
import xen_passthrough

lopper.log._init(__name__)

def is_compat( node, compat_string_to_test ):
    if re.search( "module,extract-xen", compat_string_to_test):
        return extract_xen
    return ""

def usage():
    print( """
   Usage: extract-xen -t <target node> [OPTION]

      -t       target node (full path)
      -p       permissive matching on target node (regex)
      -v       enable verbose debug/processing
      -o       output file for extracted device tree

    Standalone two-pass Xen passthrough flow: run the `extract` assist first to
    populate the extracted subtree, then this assist converts it into a Xen
    passthrough device tree. The conversion logic is shared with the
    image-builder --gen-config single-pass path via xen_passthrough.py.
    """)


def extract_xen( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    try:
        args = options['args']
    except:
        args = []

    try:
        xen_tree = sdt.subtrees["extracted"]
    except:
        lopper.log._error( "no extracted tree detected, returning" )
        return False

    opts,args2 = getopt.getopt( args, "vpt:o:", [ "verbose", "permissive" ] )

    permissive=False
    target_node_name=None
    output=None
    for o,a in opts:
        if o in ('-o'):
            output=a
        elif o in ('-t'):
            target_node_name=a
        elif o in ('-v', "--verbose"):
            verbose = verbose + 1
        elif o in ('-p', "--permissive"):
            permissive = True

    try:
        extracted_node = xen_tree["/extracted"]
    except:
        lopper.log._error( "no extracted node detected" )
        return False

    # rename the containing node from /extracted to /passthrough
    extracted_node.name = "passthrough"

    # copy sdt root compatibles into extracted node
    root_compat = sdt.tree["/"]["compatible"].value
    root_compat.append(extracted_node["compatible"].value)
    extracted_node["compatible"].value = root_compat

    # Convert every extracted device node via the shared xenify logic, then
    # prune any host smmu nodes the iommus phandles dragged in (Xen guest
    # fragments carry stream-ids, not the host smmu node).
    smmu_paths_to_prune = []
    source_paths_to_mark = []
    for n in xen_tree:
        if n.abs_path in ("/", "/passthrough"):
            continue

        is_target = bool(target_node_name and n.name == target_node_name)
        smmu_paths = xen_passthrough.xenify_node( n, sdt, is_target=is_target )
        for p in smmu_paths:
            if p not in smmu_paths_to_prune:
                smmu_paths_to_prune.append( p )

        # record the source device path so we can stamp xen,passthrough on the
        # base SDT device (only for the explicit target, matching prior behavior)
        if is_target:
            try:
                src = n["xen,path"].value
            except:
                src = None
            if src:
                source_paths_to_mark.append( src )

    # prune host smmu nodes from the passthrough tree
    smmu_basenames = set( p.split('/')[-1] for p in smmu_paths_to_prune )
    for n in list(xen_tree):
        if n.abs_path in ("/", "/passthrough"):
            continue
        if n.name in smmu_basenames:
            try:
                xen_tree - n
            except:
                pass

    # mark the source device(s) in the base SDT
    if source_paths_to_mark:
        xen_passthrough.mark_source_passthrough( sdt, source_paths_to_mark )

    # resolve() isn't strictly required, but better to be safe
    xen_tree.strict = False
    xen_tree.resolve()

    if output:
        sdt.write( xen_tree, output, True, True )
    elif lopper.log._is_enabled(logging.INFO):
        xen_tree.output = None
        xen_tree.print( sys.stdout )

    return True
