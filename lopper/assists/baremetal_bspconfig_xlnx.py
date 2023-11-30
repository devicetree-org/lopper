#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.rao@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import yaml
import sys
import os
import glob

sys.path.append(os.path.dirname(__file__))

from baremetalconfig_xlnx import compat_list, get_cpu_node
from baremetallinker_xlnx import get_memranges
from common_utils import to_cmakelist

def is_compat( node, compat_string_to_test ):
    if "module,baremetal_bspconfig_xlnx" in compat_string_to_test:
        return xlnx_generate_bm_bspconfig
    return ""

# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
# options: baremetal application source path
def xlnx_generate_bm_bspconfig(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    if options.get('outdir', {}):
        sdt.outdir = options['outdir']
   
    mem_ranges = get_memranges(tgt_node, sdt, options)
    if not mem_ranges:
        return
    # Generate Memconfig cmake meta-data file.
    memconfig_path = os.path.join(sdt.outdir,'MemConfig.cmake')
    with open(memconfig_path, 'w') as fd:
        mem_name_list = []
        mem_size_list = []
        for key, value in sorted(mem_ranges.items(), key=lambda e: e[1][1], reverse=True):
            start,size = value[0], value[1]
            """
            PS7 DDR initial 1MB is reserved memory
            Adjust the size and start address accordingly.
            """
            if "ps7_ddr" in key:
                start = 1048576
                size -= start
            name = f"XPAR_{key.upper()}_BASEADDRESS"
            mem_name_list.append(name)
            name = f"XPAR_{key.upper()}_HIGHADDRESS"
            mem_name_list.append(name)
            mem_size_list.append(hex(start))
            mem_size_list.append(hex(start + size))
        fd.write(f"set(MEM_DEF_NAMES {to_cmakelist(mem_name_list)})\n")
        fd.write(f"set(MEM_RANGES {to_cmakelist(mem_size_list)})\n")

    machine = options['args'][0]
    # Get the cpu node for a given Processor
    match_cpunode = get_cpu_node(sdt, options)

    srcdir = options['args'][1].rstrip(os.sep)
    datadir = os.path.join(os.path.dirname(srcdir),'data')
    yaml_paths = glob.glob(f"{datadir}/*/*.yaml")
    

    # Generate CPU specific config struct file.
    for yamlfile in yaml_paths:
        name = os.path.basename(os.path.dirname(yamlfile))
        with open(yamlfile) as stream:
            schema = yaml.safe_load(stream)
            compatlist = compat_list(schema)
            prop_list = schema['required']
            match = [compat for compat in compatlist if compat in match_cpunode['compatible'].value]
            if match:
               config_struct = schema['config'][0]
               outfile = os.path.join(sdt.outdir, f"x{name.lower()}_g.c")
               with open(outfile, 'w') as fd:
                   fd.write(f'#include "x{name.lower()}.h"\n')
                   fd.write(f'\n{config_struct} {config_struct}Table[] __attribute__ ((section (".drvcfg_sec"))) = {{\n')
                   for index,prop in enumerate(prop_list):
                       if index == 0:
                           fd.write("\t{")
                       try:
                           for i in range(0, len(match_cpunode[prop].value)):
                               fd.write(f"\n\t\t{hex(match_cpunode[prop].value[i])}")
                               if i != (len(match_cpunode[prop].value) - 1):
                                   fd.write(",")
                       except:
                           fd.write("\n\t\t 0")
                       if prop == prop_list[-1]:
                           fd.write(f"  /* {prop} */")
                           fd.write("\n\t}")
                       else:
                           fd.write(",")
                           fd.write(f"  /* {prop} */") 
                   fd.write("\n};")

    return True
