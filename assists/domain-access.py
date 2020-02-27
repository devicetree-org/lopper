#!/usr/bin/python3

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
from lopper import LopperAction
import lopper
from libfdt import Fdt, FdtSw, FdtException, QUIET_NOTFOUND, QUIET_ALL
import libfdt

def is_compat( node, compat_string_to_test ):
    if re.search( "access-domain,domain-v1", compat_string_to_test):
        return core_domain_access
    return ""

# tests for a bit that is set, going fro 31 -> 0 from MSB to LSB
def check_bit_set(n, k):
    if n & (1 << (k)):
        return True

    return False

# domain_node: is the openamp domain node
# sdt: is the system device tree
def core_domain_access( domain_node, sdt, verbose=0 ):
    if verbose:
        print( "[INFO]: cb: core_domain_access( %s, %s, %s )" % (domain_node, sdt, verbose))

    sdt.node_ref_reset( "", 2 )

    tgt_domain_name = sdt.node_abspath( domain_node )
    access_list = sdt.property_get( domain_node, "access", LopperFmt.COMPOUND )
    if access_list:
        for ph in access_list[::2]:
            anode = Lopper.node_by_phandle( sdt.FDT, ph )
            if anode > 0:
                full_name = sdt.node_abspath( anode )
                sdt.node_ref_inc( full_name, True )

        refd_nodes = sdt.nodes_refd()

        code = """
                p = Lopper.refcount( sdt, node_name )
                if p <= 0:
                    return True
                else:
                    return False
                """
        # delete any unreferenced nodes
        Lopper.node_filter( sdt, "/", LopperAction.DELETE, code, verbose )

    return True
