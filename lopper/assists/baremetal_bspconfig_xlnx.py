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
import yaml

sys.path.append(os.path.dirname(__file__))
from baremetalconfig_xlnx import *
from baremetallinker_xlnx import *
from bmcmake_metadata_xlnx import to_cmakelist

def is_compat( node, compat_string_to_test ):
    if re.search( "module,baremetal_bspconfig_xlnx", compat_string_to_test):
        return xlnx_generate_bm_bspconfig
    return ""

# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
# options: baremetal application source path
def xlnx_generate_bm_bspconfig(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
   
    mem_ranges = get_memranges(tgt_node, sdt, options)
    # Generate Memconfig cmake meta-data file.
    with open('MemConfig.cmake', 'w') as fd:
        mem_name_list = []
        mem_size_list = []
        for key, value in sorted(mem_ranges.items(), key=lambda e: e[1][1], reverse=True):
            start,size = value[0], value[1]
            name = "XPAR_{}_BASEADDRESS".format(key.upper())
            mem_name_list.append(name)
            name = "XPAR_{}_HIGHADDRESS".format(key.upper())
            mem_name_list.append(name)
            mem_size_list.append(hex(start))
            mem_size_list.append(hex(start + size))
        fd.write("set(MEM_DEF_NAMES %s)\n" % to_cmakelist(mem_name_list))
        fd.write("set(MEM_RANGES %s)\n" % to_cmakelist(mem_size_list))

    # Yocto Machine to CPU compat mapping
    cpu_dict = {'cortexa53-zynqmp': 'arm,cortex-a53', 'cortexa72-versal':'arm,cortex-a72', 'cortexr5-zynqmp': 'arm,cortex-r5', 'cortexa9-zynq': 'arm,cortex-a9',
                'microblaze-pmu': 'pmu-microblaze', 'microblaze-plm': 'pmc-microblaze', 'microblaze-psm': 'psm-microblaze', 'cortexr5-versal': 'arm,cortex-r5'}
    machine = options['args'][0]
    match_cpunodes = get_cpu_node(sdt, options)

    tmpdir = os.getcwd()
    os.chdir(options['args'][1])
    os.chdir("../data")
    cwd = os.getcwd()
    files = os.listdir(cwd)
    # Generate CPU specific config struct file.
    for name in files:
        os.chdir(cwd)
        if os.path.isdir(name):
            os.chdir(name)
            yamlfile = name + str(".yaml")
            with open(yamlfile) as stream:
                schema = yaml.safe_load(stream)
                compatlist = compat_list(schema)
                prop_list = schema['required']
                match = [compat for compat in compatlist if compat == cpu_dict[machine]]
                if match:
                   config_struct = schema['config'][0] 
                   outfile = tmpdir + str("/") + str("x") + name.lower() + str("_g.c")
                   with open(outfile, 'w') as fd:
                       fd.write('#include "x%s.h"\n' % name.lower())
                       fd.write('\n%s %s __attribute__ ((section (".drvcfg_sec"))) = {\n' % (config_struct, config_struct + str("Table[]")))
                       for index,prop in enumerate(prop_list):
                           if index == 0:
                               fd.write("\t{")
                           try:
                               for i in range(0, len(match_cpunodes[0][prop].value)):
                                   fd.write("\n\t\t%s" % hex(match_cpunodes[0][prop].value[i]))
                                   if i != (len(match_cpunodes[0][prop].value) - 1):
                                       fd.write(",")
                           except:
                               fd.write("\n\t\t 0")
                           if prop == prop_list[-1]:
                               fd.write("  /* %s */" % prop) 
                               fd.write("\n\t}")
                           else:
                               fd.write(",")
                               fd.write("  /* %s */" % prop) 
                       fd.write("\n};")
    os.chdir(tmpdir)
    
    return True
