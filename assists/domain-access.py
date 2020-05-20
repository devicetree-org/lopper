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
from lopper_tree import LopperAction
import lopper

def is_compat( node, compat_string_to_test ):
    if re.search( "access-domain,domain-v1", compat_string_to_test):
        return core_domain_access
    return ""

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False

# tgt_node: is the openamp domain node number
# sdt: is the system device tree
def core_domain_access( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    if verbose:
        print( "[INFO]: cb: core_domain_access( %s, %s, %s )" % (domain_node, sdt, verbose))

    # reset the treewide ref counting
    sdt.tree.ref = 0

    domain_node = sdt.tree[tgt_node]

    access_list = domain_node["access"].value
    if access_list:
        for ph in access_list[::2]:

            anode = sdt.tree.pnode( ph )
            if anode:
                sdt.tree.ref_all( anode, True )

        refd_nodes = sdt.tree.refd()

        if verbose:
            for p in refd_nodes:
                print( "node ref: %s" % p )

        code = """
                p = node.ref
                if p <= 0:
                    return True
                else:
                    return False
                """
        # delete any unreferenced nodes
        sdt.tree.filter( "/", LopperAction.DELETE, code, None, verbose )

    return True
