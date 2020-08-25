#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.rao@xilinx.com>
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
import lopper
from lopper_tree import *
from re import *

sys.path.append(os.path.dirname(__file__))
from baremetalconfig_xlnx import scan_reg_size

def is_compat( node, compat_string_to_test ):
    if re.search( "module,baremetallinker_xlnx", compat_string_to_test):
        return xlnx_generate_bm_linker
    return ""

# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
# options: baremetal application source path
def get_memranges(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    mem_nodes = []
    #Maintain a static memory IP list this is needed inorder to capture proper ip name in the linker script
    xlnx_memipname = {"axi_bram": 0, "ps7_ddr": 0, "psu_ddr": 0, "psv_ddr": 0, "mig": 0, "lmb_bram": 0, "axi_noc": 0, "psu_ocm": 0,  "psv_ocm": 0, "ddr4": 0}
    for node in root_sub_nodes:
        try:
            device_type = node["device_type"].value
            if "memory" in device_type:
                mem_nodes.append(node)
        except:
           pass
   
    mem_ranges = {}
    for node in mem_nodes:
        na = node.parent["#address-cells"].value[0]
        ns = node.parent["#size-cells"].value[0]
        val = node['reg'].value
        total_nodes = int(len(val)/(na+ns))
        name_list = [name.replace("_", "-") for name in list(xlnx_memipname.keys())]
        try:
            compat = node['compatible'].value[0]
            match = [mem for mem in name_list if mem in compat]
            for i in range(total_nodes):
                reg, size = scan_reg_size(node, val, i)
                key = match[0].replace("-", "_")
                linker_secname = key + str("_") + str(xlnx_memipname[key])
                mem_ranges.update({linker_secname: [reg, size]})
                xlnx_memipname[key] += 1
        except KeyError:
            pass

    return mem_ranges

# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
# options: baremetal application source path
def xlnx_generate_bm_linker(tgt_node, sdt, options):
    mem_ranges = get_memranges(tgt_node, sdt, options)
    default_ddr = None
    with open('memory.ld', 'w') as fd:
        fd.write("MEMORY\n")
        fd.write("{\n")
        for key, value in sorted(mem_ranges.items(), key=lambda e: e[1][1], reverse=True):
            if default_ddr is None:
                default_ddr = key
            start,size = value[0], value[1]
            """
            LMB BRAM initial 80 bytes being used by the linker vectors section
            Adjust the size and start address accordingly.
            """
            if "lmb_bram" in key:
                start = 80
                size -= start
            """
            PS7 DDR initial 1MB is reserved memory
            Adjust the size and start address accordingly.
            """
            if "ps7_ddr" in key:
                start = 1048576
                size -= start
            fd.write("\t%s : ORIGIN = %s, LENGTH = %s\n" % (key, hex(start), hex(size)))
        fd.write("}\n")

    src_dir = os.path.dirname(options['args'][0])
    src_dir = os.path.dirname(src_dir)
    appname = src_dir.rsplit('/', 1)[-1]
    cmake_file = appname.capitalize() + str("Example.cmake")
    with open(cmake_file, 'a') as fd:
        fd.write("set(DDR %s)\n" % default_ddr)
    return True
