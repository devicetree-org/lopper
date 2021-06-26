#/*
# * Copyright (c) 2019,2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Bruce Ashfield <bruce.ashfield@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import copy
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
import lopper
from lopper.tree import *
from re import *
from openamp_xlnx_common import *


def is_compat( node, compat_string_to_test ):
    if re.search( "openamp,xlnx-zynq-a9", compat_string_to_test):
        return xlnx_openamp_zynq
    return ""

# tgt_node: is the openamp domain node number
# sdt: is the system device tree
# TODO: this routine needs to be factored and made smaller
def xlnx_openamp_zynq( tgt_node, sdt, options ):
    try:
        verbose = options['verbose']
    except:
        verbose = 0

    if verbose:
        print( "[INFO]: cb: xlnx_openamp_zynq( %s, %s, %s )" % (tgt_node, sdt, verbose))

    domain_node = sdt.tree[tgt_node]

    root_node = sdt.tree["/"]
    try:
        memory_node = sdt.tree[ "/reserved-memory" ]
        is_kernel_case = True
    except:
        return False

    remoteproc_node = sdt.tree["/remoteproc0"]
    mem_carveouts = parse_memory_carevouts(sdt, options, remoteproc_node)
    # last arg (True) denotes for kernelspace case
    inputs = {
        "CHANNEL_0_MEM_SIZE" : "0x80000UL",
        "CHANNEL_0_SHARED_BUF_OFFSET" : "0x80000UL",
        "CHANNEL_0_RSC_MEM_SIZE" : "0x2000UL",
        "CHANNEL_0_TX" : "FW_RSC_U32_ADDR_ANY",
        "CHANNEL_0_RX" : "FW_RSC_U32_ADDR_ANY",
        "CHANNEL_0_VRING_MEM_SIZE" : "0x8000",
    }

    generate_openamp_file( mem_carveouts, options, SOC_TYPE.ZYNQ, is_kernel_case, inputs )
    return True
