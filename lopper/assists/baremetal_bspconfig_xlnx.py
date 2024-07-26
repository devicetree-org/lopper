#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
# * Copyright (c) 2024 Advanced Micro Devices, Inc. All Rights Reserved.
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
            if "ps7_ddr" in key and start == 0:
                start = 1048576
                size -= start
            suffix = "ADDRESS"
            if "axi_noc" in key:
                suffix = "ADDR"
            name = f"XPAR_{key.upper()}_BASE{suffix}"
            mem_name_list.append(name)
            name = f"XPAR_{key.upper()}_HIGH{suffix}"
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
               if name == "microblaze":
                   outfile = os.path.join(sdt.outdir, "microblaze_exceptions_g.h")
                   with open(outfile, 'w') as fd:
                       fd.write('#ifndef MICROBLAZE_EXCEPTIONS_G_H /**< prevent circular inclusions */\n')
                       fd.write('#define MICROBLAZE_EXCEPTIONS_G_H /**< by using protection macros */\n')
                       is_exception_en = []
                       for prop in prop_list[:10]:
                           if prop == "xlnx,exceptions-in-delay-slots":
                               if match_cpunode.propval(prop) != ['']:
                                   val = match_cpunode.propval(prop, list)[0]
                                   if val != 0:
                                       fd.write("#define MICROBLAZE_CAN_HANDLE_EXCEPTIONS_IN_DELAY_SLOTS\n")
                           elif prop == "xlnx,unaligned-exceptions":
                               if match_cpunode.propval(prop) != ['']:
                                   val = match_cpunode.propval(prop, list)[0]
                                   if val == 0:
                                       fd.write("#define NO_UNALIGNED_EXCEPTIONS 1\n")
                                   else:
                                       is_exception_en.append(True)
                           elif prop == "xlnx,fpu-exception":
                               if match_cpunode.propval(prop) != ['']:
                                   val = match_cpunode.propval(prop, list)[0]
                                   if val != 0:
                                       fd.write("#define MICROBLAZE_FP_EXCEPTION_ENABLED 1\n")
                                       is_exception_en.append(True)
                           elif prop == "xlnx,predecode-fpu-exception":
                               if match_cpunode.propval(prop) != ['']:
                                   val = match_cpunode.propval(prop, list)[0]
                                   if val != 0:
                                       fd.write("#define MICROBLAZE_FP_EXCEPTION_DECODE 1\n")
                                       is_exception_en.append(True)
                           else:
                               if match_cpunode.propval(prop) != ['']:
                                   val = match_cpunode.propval(prop, list)[0]
                                   if val != 0:
                                       is_exception_en.append(True)
                       if any(is_exception_en):
                           fd.write('#define MICROBLAZE_EXCEPTIONS_ENABLED 1\n')
                       fd.write('#endif /* end of protection macro */\n')

    return True
