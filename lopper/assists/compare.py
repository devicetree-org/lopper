#/*
# * Copyright (c) 2022 AMD Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@amd.com>
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
import logging

from lopper import Lopper
from lopper import LopperFmt
from lopper.tree import LopperAction
from lopper.tree import LopperTree
import lopper
import lopper.log

lopper.log._init(__name__)

def is_compat( node, compat_string_to_test ):
    if re.search( "module,compare", compat_string_to_test):
        return compare
    return ""

def usage():
    print( """
   Usage: compare [OPTION] <comparison device tree> [<output file>]

   Structurally compares the system device tree (source) against the
   comparison tree (target) and emits the difference.

      -k <key> node match key: path (default), label, address, name
      -o <fmt> emit the structural diff in <fmt>: unified, fragment, or
               equivalence. Written to <output file> if given, else stdout.
               With this option the diff core is used (see lopper.tree_compare).
      -c       legacy name-existence comparison (used when -o is absent;
               default "name")
      -x       exclude nodes or properties (legacy name check)
      -p       permissive matching on target node (regex)
      -v       enable verbose debug/processing

   Examples:
      compare other.dts -o unified
      compare target.dts -k label -o fragment board.overlay
      compare golden.dts -o equivalence      # exit 2 if they differ

    """)

# tgt_node: is the openamp domain node number
# sdt: is the system device tree
def compare( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    try:
        args = options['args']
    except:
        args = []

    if verbose > 3:
        desired_level = lopper.log.TRACE2
    elif verbose > 2:
        desired_level = lopper.log.TRACE
    elif verbose > 1:
        desired_level = logging.DEBUG
    elif verbose > 0:
        desired_level = logging.INFO
    else:
        desired_level = logging.WARNING
    lopper.log._level(desired_level, __name__)

    lopper.log._debug( f"cb: compare( {tgt_node}, {sdt}, {verbose}, {args} )", level=logging.DEBUG )

    # gnu_getopt permutes: options may appear before or after the positional
    # comparison-tree argument, so "compare B.dts -o unified" and
    # "compare -o unified B.dts" both work.
    opts,args2 = getopt.gnu_getopt( args, "c:i:k:pvt:o:x:h", [ "help", "verbose", "permissive" ] )

    if opts == [] and args2 == []:
        usage()
        sys.exit(1)

    exclude_list=[]
    include_list=[]
    compare_list=[]
    key = "path"
    output_format = None
    permissive = False
    for o,a in opts:
        # print( "o: %s a: %s" % (o,a))
        if o in ('-x'):
            exclude_list.append( a )
        if o in ('-i'):
            include_list.append( a )
        elif o in ('-k'):
            key = a
        elif o in ('-o'):
            output_format = a
        elif o in ('-v', "--verbose"):
            verbose = verbose + 1
        elif o in ('-c', "--compare"):
            compare_list.append( a )
        elif o in ('-p', "--permissive"):
            permissive = True
        elif o in ('-h', "--help"):
            usage()
            sys.exit(1)

    if len(args2) < 1:
        lopper.log._error( "comparison tree not passed" )
        sys.exit(1)

    compare_dts = args2[0]
    output_file = args2[1] if len(args2) > 1 else None

    compiled_file, _ = Lopper.dt_compile( compare_dts, "", "", True, sdt.outdir,
                                          sdt.save_temps, verbose )
    if not compiled_file:
        lopper.log._error( f"could not compile file {compare_dts}" )
        sys.exit(1)

    compare_tree = LopperTree()
    fdt = Lopper.dt_to_fdt( compiled_file )
    compare_tree.load( Lopper.export( fdt ) )

    # Structural-diff path: compare the SDT (source) against the comparison
    # tree (target) via the shared diff core and emit in the chosen format.
    if output_format:
        try:
            delta = sdt.tree.compare( compare_tree, key=key )
            rendered = delta.emit( output_format, output=output_file )
        except NotImplementedError as e:
            lopper.log._error( str(e) )
            sys.exit(1)

        if output_file:
            lopper.log._info( f"compare: {output_format} written to {rendered}" )
        else:
            sys.stdout.write( rendered )

        # equivalence doubles as a regression gate: non-zero exit on differ
        if output_format == "equivalence" and not delta.equivalent():
            sys.exit(2)

        return True

    if not compare_list:
        compare_list = [ "name" ]

    lopper.log._info( f"comparing: {compare_list}" )

    if "name" in compare_list:
        lopper.log._info( "running name comparison ..." )
        name_pass = True
        for node_tree_one in sdt.tree:
            # print( "n: %s" % node_tree_one.name )
            try:
                if node_tree_one.name:
                    other_tree_node = compare_tree.nodes( ".*/" + node_tree_one.name + "$" )
                    if not other_tree_node and not node_tree_one.name in exclude_list:
                        other_tree_node_fuzzy = compare_tree.nodes( node_tree_one.name )
                        lopper.log._error( f"node with name '{node_tree_one.name}' does not exist in comparison tree" )
                        if other_tree_node_fuzzy:
                            lopper.log._error( "closest matches were:" )
                            for o in other_tree_node_fuzzy:
                                lopper.log._error( f"            {o.name}" )
                        name_pass = False
                    else:
                        True

            except Exception as e:
                sys.exit(1)

    return True
