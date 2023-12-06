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
from lopper import Lopper
from lopper import LopperFmt
from lopper.tree import LopperAction
from lopper.tree import LopperTree
import lopper

def is_compat( node, compat_string_to_test ):
    if re.search( "module,compare", compat_string_to_test):
        return compare
    return ""

def usage():
    print( """
   Usage: compare [OPTION] <device tree>

      -p       permissive matching on target node (regex)
      -v       enable verbose debug/processing
      -x       exclude nodes or properties
      -o       output directory for files
      -c       run a specific comparision (default is "all")
               current options are: "name"

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
    
    if verbose:
        print( "[INFO]: cb: compare( %s, %s, %s, %s )" % (tgt_node, sdt, verbose, args))

    opts,args2 = getopt.getopt( args, "c:i:pvt:o:x:h", [ "help", "verbose", "permissive" ] )

    if opts == [] and args2 == []:
        usage()
        sys.exit(1)

    exclude_list=[]
    include_list=[]
    compare_list=[]
    output=None
    for o,a in opts:
        # print( "o: %s a: %s" % (o,a))
        if o in ('-x'):
            exclude_list.append( a )
        if o in ('-i'):
            include_list.append( a )
        elif o in ('-o'):
            output=a
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
        print( "[ERROR]: comparison tree not passed" )
        sys.exit(1)

    compare_dts = args2[0]

    if not compare_list:
        compare_list = [ "name" ]

    if verbose:
        print( "[INFO]: comparing: %s" % compare_list )

    compiled_file = Lopper.dt_compile( compare_dts, "", "", True, sdt.outdir,
                                       sdt.save_temps, verbose )
    if not compiled_file:
        lopper.log._error( f"could not compile file {compare_dts}" )
        sys.exit(1)

    compare_tree = LopperTree()
    fdt = Lopper.dt_to_fdt( compiled_file )
    compare_tree.load( Lopper.export( fdt ) )

    if "name" in compare_list:
        if verbose:
            print( "[INFO]: running name comparision ..." )
        name_pass = True
        for node_tree_one in sdt.tree:
            # print( "n: %s" % node_tree_one.name )
            try:
                if node_tree_one.name:
                    other_tree_node = compare_tree.nodes( ".*/" + node_tree_one.name + "$" )
                    if not other_tree_node and not node_tree_one.name in exclude_list:
                        other_tree_node_fuzzy = compare_tree.nodes( node_tree_one.name )
                        print( "[ERROR]: node with name '%s' does not exist in comparison tree" % node_tree_one.name )
                        if other_tree_node_fuzzy:
                            print( "[ERROR]: closest matches were: " )
                            for o in other_tree_node_fuzzy:
                                print( "            %s" % o.name )
                        name_pass = False
                    else:
                        True

            except Exception as e:
                sys.exit(1)

    return True
