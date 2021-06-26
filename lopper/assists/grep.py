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
from pathlib import Path
from pathlib import PurePath
from io import StringIO
import contextlib
import importlib
from lopper import Lopper
from lopper import LopperFmt
from lopper.tree import LopperAction
import lopper

def is_compat( node, compat_string_to_test ):
    if re.search( "module,grep", compat_string_to_test):
        return grep
    return ""

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False

# tgt_node: is the openamp domain node number
# sdt: is the system device tree
def grep( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    try:
        args = options['args']
    except:
        args = []

    if verbose:
        print( "[INFO]: cb: grep( %s, %s, %s, %s )" % (tgt_node, sdt, verbose, args))

    node_regex = ""
    tgt_regex = args[0]
    if len(args) == 2:
        node_regex = args[1]

    # for n in sdt.tree:
    #     print( "n: %s" % n )

    nodes = []
    try:
        nodes = sdt.tree.nodes(node_regex)
        lnodes = sdt.tree.lnodes(node_regex)
        nodes = nodes + lnodes
    except:
        print( "[ERROR]: grep: nodes %s not found" % node_regex )
        sys.exit(1)


    matches = {}
    for n in nodes:
        try:
            match = n[tgt_regex]
            #print( "match: %s" % match )
            matches[n.abs_path] = match
            #print( "matches is now: %s" % matches )
        except Exception as e:
            pass

    if matches:
        for m in matches.keys():
            print( "%s: %s" % (m,matches[m]))
    else:
        print( "%s: not found" % tgt_regex )

    return True
