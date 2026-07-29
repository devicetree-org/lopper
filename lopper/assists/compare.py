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
   Usage: compare [OPTION] <target device tree> [<output file>]
              (with a system device tree loaded: it is the source)
          compare [OPTION] <source device tree> <target device tree> [<output file>]
              (with NO system device tree: both trees passed as peers)

   Structurally compares two device trees (system device tree or not) and
   emits the difference. If lopper loaded a system device tree, it is the
   source and the single positional argument is the target. If lopper was
   run with no system device tree, pass both trees as positional arguments
   (source then target), e.g.:
       lopper.py -- compare A.dts B.dts -o unified

   The diff runs in two steps: first each node in the source is *matched*
   to its counterpart in the target, then the matched pair is diffed. -k
   selects the matching key -- i.e. what makes a node in one tree "the
   same node" as one in the other. This only matters when nodes may have
   been renamed or moved between the two trees:

      -k path     (default) match by absolute path (/bus@x/uart@y). A node
                  that was renamed or reparented has a different path, so
                  it appears as a removal + an addition.
      -k address  match by unit-address (the part after '@'). A node that
                  kept its address but changed name/parent is reported as
                  one node that MOVED, not remove+add.
      -k label    match by devicetree label (&foo). Same idea, keyed on the
                  label instead of the address.
      -k name     match by node name without the unit-address (e.g. 'uart').

      If a key value is missing (no label) or ambiguous (two nodes share
      it), that node falls back to path matching so nothing is mis-paired.
      When no nodes have moved, every key gives the same result.

      NOTE: -k only takes effect together with -o (the structural diff).
      Without -o the legacy name-existence check runs and -k is ignored.

      -o <fmt> emit the structural diff in <fmt>: unified, fragment, or
               equivalence. Written to <output file> if given, else stdout.
               With this option the diff core is used (see lopper.tree_compare).
      -c       legacy name-existence comparison (used when -o is absent;
               default "name")
      -x       exclude nodes or properties (legacy name check)
      -p       permissive matching on target node (regex)
      -v       enable verbose debug/processing

   Examples:
      compare other.dts -o unified                  # human-readable diff
      compare target.dts -k address -o unified      # treat renames as moves
      compare target.dts -k label -o fragment out.dtsi   # emit a patch
      compare golden.dts -o equivalence             # exit 2 if they differ

    """)

def _load_tree( dts_path, outdir, save_temps, verbose ):
    """Compile a dts/dtb and load it into a LopperTree."""
    compiled_file, _ = Lopper.dt_compile( dts_path, "", "", True, outdir,
                                          save_temps, verbose )
    if not compiled_file:
        lopper.log._error( f"could not compile file {dts_path}" )
        sys.exit(1)
    tree = LopperTree()
    tree.load( Lopper.export( Lopper.dt_to_fdt( compiled_file ) ) )
    return tree


# tgt_node: is the openamp domain node number
# sdt: is the system device tree (may be absent -- see the two-tree form)
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

    # Two ways to supply the source tree:
    #   with a system device tree loaded ->  compare <target>
    #       source = the SDT, target = the single positional arg
    #   with NO system device tree        ->  compare <source> <target>
    #       both trees are peers passed as positional args
    have_sdt = sdt is not None and getattr( sdt, "tree", None ) is not None
    outdir = getattr( sdt, "outdir", "." )
    save_temps = getattr( sdt, "save_temps", False )

    if have_sdt:
        if len(args2) < 1:
            lopper.log._error( "comparison tree not passed" )
            sys.exit(1)
        source_tree = sdt.tree
        compare_dts = args2[0]
        output_file = args2[1] if len(args2) > 1 else None
    else:
        if len(args2) < 2:
            lopper.log._error( "without a system device tree, compare needs two "
                               "device trees: compare <source> <target>" )
            sys.exit(1)
        source_tree = _load_tree( args2[0], outdir, save_temps, verbose )
        compare_dts = args2[1]
        output_file = args2[2] if len(args2) > 2 else None

    compare_tree = _load_tree( compare_dts, outdir, save_temps, verbose )

    # Structural-diff path: compare the SDT (source) against the comparison
    # tree (target) via the shared diff core and emit in the chosen format.
    if output_format:
        try:
            delta = source_tree.compare( compare_tree, key=key )
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
        for node_tree_one in source_tree:
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
