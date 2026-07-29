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
                  label instead of the address; the reliable way to track a
                  specific node across a move, since labels are unique.

      If a key value is missing (no label) or ambiguous (two nodes share
      it), that node falls back to path matching so nothing is mis-paired.
      When no nodes have moved, every key gives the same result.

      -o <fmt> emit the difference in <fmt> (default: unified):
                 unified     - true diff style: each change a '-'/'+' pair
                 compact     - one line per change ('~ name: old -> new')
                 fragment    - a concatenated dtsi patch (source + it = target)
                 equivalence - one-line 'equivalent'/'differ' (exit 2 on differ)
               Written to <output file> if given, else stdout.
      -v       enable verbose debug/processing

   Examples:
      compare other.dts                             # unified diff (default)
      compare target.dts -k address -o unified      # treat renames as moves
      compare target.dts -k label -o fragment out.dtsi   # emit a patch
      compare golden.dts -o equivalence             # exit 2 if they differ
      compare A.dts B.dts -o compact                # no SDT: two peer trees

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
    # tree arguments, so "compare B.dts -o unified" and
    # "compare -o unified B.dts" both work.
    try:
        opts,args2 = getopt.gnu_getopt( args, "k:o:vh", [ "help", "verbose" ] )
    except getopt.GetoptError as e:
        lopper.log._error( str(e) )
        usage()
        sys.exit(1)

    if opts == [] and args2 == []:
        usage()
        sys.exit(1)

    key = "path"
    output_format = "unified"   # default when -o is omitted
    for o,a in opts:
        if o in ('-k'):
            key = a
        elif o in ('-o'):
            output_format = a
        elif o in ('-v', "--verbose"):
            verbose = verbose + 1
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

    # Compare the source tree against the target via the shared diff core
    # and emit in the chosen format (see lopper.tree_compare).
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
