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
from lopper.tree import *
from re import *

sys.path.append(os.path.dirname(__file__))
from baremetalconfig_xlnx import scan_reg_size, get_cpu_node
from bmcmake_metadata_xlnx import to_cmakelist

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

    versal_noc_ch_ranges =  {"DDR_CH_1": "0x50000000000", "DDR_CH_2": "0x60000000000", "DDR_CH_3": "0x70000000000"}

    # Yocto Machine to CPU compat mapping
    match_cpunodes = get_cpu_node(sdt, options)
   
    address_map = match_cpunodes[0].parent["address-map"].value
    all_phandles = []
    ns = match_cpunodes[0].parent["#ranges-size-cells"].value[0]
    na = match_cpunodes[0].parent["#ranges-address-cells"].value[0]
    cells = na + ns
    tmp = na
    while tmp < len(address_map):
        all_phandles.append(address_map[tmp])
        tmp = tmp + cells + na + 1

    mem_ranges = {}
    for node in mem_nodes:
        # Check whether the memory node is mapped to cpu cluster or not
        mem_phandles = [handle for handle in all_phandles if handle == node.phandle]
        addr_list = []
        if mem_phandles:
           # Remove Duplicate phandle referenecs
           mem_phandles = list(dict.fromkeys(mem_phandles))
           indx_list = [index for index,handle in enumerate(address_map) for val in mem_phandles if handle == val]
           for inx in indx_list:
               start = [address_map[inx+i+1] for i in range(na)]
               if na == 2 and start[0] != 0:
                   val = str(start[1])
                   pad = 8 - len(val)
                   val = val.ljust(pad + len(val), '0')
                   reg = int((str(hex(start[0])) + val), base=16)
                   addr_list.append(reg)
               elif na == 2:
                   addr_list.append(start[1])
               else:
                   addr_list.append(start[0])

        nac = node.parent["#address-cells"].value[0]
        nsc = node.parent["#size-cells"].value[0]
        val = node['reg'].value
        total_nodes = int(len(val)/(nac+nsc))
        name_list = [name.replace("_", "-") for name in list(xlnx_memipname.keys())]
        try:
            compat = node['compatible'].value[0]
            match = [mem for mem in name_list if mem in compat]
            for i in range(total_nodes):
                reg, size = scan_reg_size(node, val, i)
                valid_range = [addr for addr in addr_list if reg == addr or addr > reg]
                if valid_range:
                    key = match[0].replace("-", "_")
                    is_valid_noc_ch = 0
                    if "axi_noc" in key:
                        for ch_name, ran in sorted(versal_noc_ch_ranges.items(), reverse=True):
                            if ran <= hex(valid_range[0]):
                                is_valid_noc_ch = ch_name
                                break

                    if is_valid_noc_ch:
                        linker_secname = key + str("_") + is_valid_noc_ch
                    else:
                        linker_secname = key + str("_") + str(xlnx_memipname[key])
                        xlnx_memipname[key] += 1
                    mem_ranges.update({linker_secname: [valid_range[0], size]})
        except KeyError:
            pass

    return mem_ranges

# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
# options: baremetal application source path
def xlnx_generate_bm_linker(tgt_node, sdt, options):
    mem_ranges = get_memranges(tgt_node, sdt, options)
    default_ddr = None
    memtest_config = None
    machine = options['args'][0]

    try:
        memtest_config = options['args'][2]
    except IndexError:
        pass

    with open('memory.ld', 'w') as fd:
        fd.write("MEMORY\n")
        fd.write("{\n")
        if memtest_config:
            traverse = False
        else:
            traverse = True

        for key, value in sorted(mem_ranges.items(), key=lambda e: e[1][1], reverse=traverse):
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
            """
            For R5 PSU DDR initial 1MB is reserved for tcm
            Adjust the size and start address accordingly.
            """
            if "psu_ddr" in key and machine == "cortexr5-zynqmp" and start == 0:
                start = 1048576
                size -= start
            if "axi_noc" in key and machine == "cortexr5-versal" and start == 0:
                start = 1048576
                size -= start
            fd.write("\t%s : ORIGIN = %s, LENGTH = %s\n" % (key, hex(start), hex(size)))
        fd.write("}\n")

    src_dir = os.path.dirname(options['args'][1])
    src_dir = os.path.dirname(src_dir)
    appname = src_dir.rsplit('/', 1)[-1]
    cmake_file = appname.capitalize() + str("Example.cmake")

    ## To inline with existing tools point default ddr for linker to lower DDR
    lower_ddrs = ["axi_noc_0", "psu_ddr_0", "ps7_ddr_0"]
    has_ddr = [x for x in mem_ranges.keys() for ddr in lower_ddrs if re.search(ddr, x)]
    if has_ddr and not memtest_config:
        default_ddr = has_ddr[0]

    with open(cmake_file, 'a') as fd:
        fd.write("set(DDR %s)\n" % default_ddr)
        memip_list = []
        for key, value in sorted(mem_ranges.items(), key=lambda e: e[1][1], reverse=traverse):
            memip_list.append(key)
            fd.write("set(%s %s)\n" % (key, to_cmakelist([hex(value[0]), hex(value[1])])))
        fd.write("set(TOTAL_MEM_CONTROLLERS %s)\n" % to_cmakelist(memip_list))
    return True
