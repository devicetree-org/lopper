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
import os
import getopt
import re
from pathlib import Path
from pathlib import PurePath
from lopper import Lopper
from lopper import LopperFmt
import lopper
from lopper.tree import *
from re import *
import yaml
import glob
from collections import OrderedDict

sys.path.append(os.path.dirname(__file__))
from baremetalconfig_xlnx import *
from baremetaldrvlist_xlnx import *
from baremetallinker_xlnx import *

def is_compat( node, compat_string_to_test ):
    if re.search( "module,baremetal_xparameters_xlnx", compat_string_to_test):
        return xlnx_generate_xparams
    return ""

def get_label(sdt, symbol_node, node):
    prop_dict = symbol_node.__props__
    match = [label for label,node_abs in prop_dict.items() if re.match(node_abs[0], node.abs_path) and len(node_abs[0]) == len(node.abs_path)]
    if match:
        return match[0]
    else:
        return None

def xlnx_generate_xparams(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()

    node_dict = {}
    node_list = []
    # Traverse the tree and find the nodes having status=ok property
    symbol_node = ""
    chosen_node = ""
    for node in root_sub_nodes:
        try:
            if node.name == "chosen":
                chosen_node = node
            if node.name == "__symbols__":
                symbol_node = node
            status = node["status"].value
            if "okay" in status:
                node_list.append(node)
                compat = node['compatible'].value
                node_dict.update({node.abs_path:compat})
        except:
           pass

    srcdir = options['args'][1]
    for node in node_list:
        try:
            prop_val = node['dma-coherent'].value
            if '' in prop_val:
                cci_en = 1
                break
        except KeyError:
            cci_en = None

    drvlist = xlnx_generate_bm_drvlist(tgt_node, sdt, options)
    plat = DtbtoCStruct('xparameters.h')
    plat.buf('#ifndef XPARAMETERS_H   /* prevent circular inclusions */\n')
    plat.buf('#define XPARAMETERS_H   /* by using protection macros */\n')
    total_nodes = node_list
    for drv in drvlist:
        search_paths = [srcdir] + [srcdir + "/XilinxProcessorIPLib/drivers/"]
        for s in search_paths:
            yaml_file = Path( s + "/" + drv + "/data/" + drv + ".yaml")
            try:
                yaml_file_abs = yaml_file.resolve()
            except FileNotFoundError:
                yaml_file_abs = ""

        if os.path.isfile(str(yaml_file_abs)):
            driver_name = str(yaml_file_abs).split('/')[-1].split('.')[0]
            with open(str(yaml_file_abs), "r") as stream:
                schema = yaml.safe_load(stream)
                driver_compatlist = compat_list(schema)
                driver_proplist = schema['required']
                match_nodes = []
                for comp in driver_compatlist:
                    for node,compatible_list in sorted(node_dict.items(), key=lambda e: e[0], reverse=False):
                       match = [x for x in compatible_list if comp == x]
                       if match:
                           node1 = [x for x in node_list if (x.abs_path == node)]
                           node_list = [x for x in node_list if not(x.abs_path == node)]
                           if node1:
                               match_nodes.append(node1[0])
                match_nodes = get_mapped_nodes(sdt, match_nodes, options)
                for index, node in enumerate(match_nodes):
                    label_name = get_label(sdt, symbol_node, node)
                    label_name = label_name.upper()
                    canondef_dict = {}
                    if index == 0:
                        plat.buf('\n#define XPAR_X%s_NUM_INSTANCES %s\n' % (driver_name.upper(), len(match_nodes)))
                    for i, prop in enumerate(driver_proplist):
                        pad = 0
                        phandle_prop = 0
                        if isinstance(prop, dict):
                            pad = list(prop.values())[0]
                            prop = list(prop.keys())[0]
                            if pad == "phandle":
                                phandle_prop = 1

                        if i == 0:
                            plat.buf('\n/* Definitions for peripheral %s */' % label_name)

                        if prop == "reg":
                            try:
                                val, size = scan_reg_size(node, node[prop].value, 0)
                                plat.buf('\n#define XPAR_%s_BASEADDR %s' % (label_name, hex(val)))
                                plat.buf('\n#define XPAR_%s_HIGHADDR %s' % (label_name, hex(val + size -1)))
                                canondef_dict.update({"BASEADDR":hex(val)})
                                canondef_dict.update({"HIGHADDR":hex(val + size - 1)})
                                if pad:
                                    for j in range(1, pad):
                                        try:
                                            val, size = scan_reg_size(node, node[prop].value, j)
                                            plat.buf('\n#define XPAR_%s_BASEADDR_%s %s' % (label_name, j, hex(val)))
                                        except IndexError:
                                            pass
                            except KeyError:
                                pass
                        elif prop == "compatible":
                            plat.buf('\n#define XPAR_%s_%s %s' % (label_name, prop.upper(), node[prop].value[0]))
                            canondef_dict.update({prop:node[prop].value[0]})
                        elif prop == "interrupts":
                            try:
                                intr = get_interrupt_prop(sdt, node, node[prop].value)
                                plat.buf('\n#define XPAR_%s_%s %s' % (label_name, prop.upper(), intr[0]))
                                canondef_dict.update({prop:intr[0]})
                            except KeyError:
                                intr = [0xFFFF]

                            if pad:
                                for j in range(1, pad):
                                    try:
                                        plat.buf('\n#define XPAR_%s_%s_%s %s' % (label_name, prop.upper(), j, intr[j]))
                                    except IndexError:
                                        pass
                        elif prop == "interrupt-parent":
                            try:
                                intr_parent = get_intrerrupt_parent(sdt, node[prop].value)
                                prop = prop.replace("-", "_")
                                plat.buf('\n#define XPAR_%s_%s %s' % (label_name, prop.upper(), hex(intr_parent)))
                                canondef_dict.update({prop:hex(intr_parent)})
                            except KeyError:
                                pass
                        elif prop == "clocks":
                            clkprop_val = get_clock_prop(sdt, node[prop].value)
                            plat.buf('\n#define XPAR_%s_%s %s' % (label_name, prop.upper(), hex(clkprop_val)))
                            canondef_dict.update({prop:hex(clkprop_val)})
                        elif prop == "child,required":
                            for j,child in enumerate(list(node.child_nodes.items())):
                                for k,p in enumerate(pad):
                                    val = hex(child[1][p].value[0])
                                    p = p.replace("-", "_")
                                    p = p.replace("xlnx,", "")
                                    plat.buf('\n#define XPAR_%s_%s_%s %s' % (label_name, j, p.upper(), val))
                        elif phandle_prop:
                            try:
                                prop_val = get_phandle_regprop(sdt, prop, node[prop].value)
                                plat.buf('\n#define XPAR_%s_%s %s' % (label_name, prop.upper(), hex(prop_val)))
                                canondef_dict.update({prop:hex(prop_val)})
                            except KeyError:
                                pass
                        elif prop == "ranges":
                            try:
                                device_type = node['device_type'].value[0]
                                if device_type == "pci":
                                    device_ispci = 1
                            except KeyError:
                                device_ispci = 0
                            if device_ispci:
                                prop_vallist = get_pci_ranges(node, node[prop].value, pad)
                                i = 0
                                for j, prop_val in enumerate(prop_vallist):
                                    if j % 2:
                                        plat.buf('\n#define XPAR_%s_%s_HIGHADDR_%s %s' % (label_name, prop.upper(), i, prop_val))
                                        cannon_prop = prop + str("_") + str("HIGHADDR") + str("_") + str(i)
                                        canondef_dict.update({cannon_prop:prop_val})
                                        i += 1
                                    else:
                                        plat.buf('\n#define XPAR_%s_%s_BASEADDR_%s %s' % (label_name, prop.upper(), i, prop_val))
                                        cannon_prop = prop + str("_") + str("BASEADDR") + str("_") + str(i)
                                        canondef_dict.update({cannon_prop:prop_val})
                        else:
                            try:
                                prop_val = node[prop].value
                                # For boolean property if present LopperProp will return
                                # empty string convert it to baremetal config struct expected value
                                if '' in prop_val:
                                    prop_val = [1]
                            except KeyError:
                                prop_val = [0]

                            if ('/bits/' in prop_val):
                                prop_val = [int(prop_val[-1][3:-1], base=16)]
                            prop = prop.replace("-", "_")
                            prop = prop.replace("xlnx,", "")
                            if len(prop_val) > 1:
                                for k,item in enumerate(prop_val):
                                    cannon_prop = prop + str("_") + str(k)
                                    canondef_dict.update({cannon_prop:item})
                                    plat.buf('\n#define XPAR_%s_%s_%s %s' % (label_name, prop.upper(), k, item))
                            else:
                                canondef_dict.update({prop:hex(prop_val[0])})
                                plat.buf('\n#define XPAR_%s_%s %s' % (label_name, prop.upper(), hex(prop_val[0])))

                    plat.buf('\n\n/* Canonical definitions for peripheral %s */' % label_name)
                    for prop,val in sorted(canondef_dict.items(), key=lambda e: e[0][0], reverse=False):
                        plat.buf('\n#define XPAR_X%s_%s_%s %s' % (driver_name.upper(), index, prop.upper(), val))
                    plat.buf('\n')
                                    
    # Generate Defines for Generic Nodes
    node_list = get_mapped_nodes(sdt, node_list, options)
    for node in node_list:
        label_name = get_label(sdt, symbol_node, node)
        label_name = label_name.upper()
        try:
            val = scan_reg_size(node, node['reg'].value, 0)
            plat.buf('\n/* Definitions for peripheral %s */' % label_name)
            plat.buf('\n#define XPAR_%s_BASEADDR %s\n' % (label_name, hex(val[0])))
            plat.buf('#define XPAR_%s_HIGHADDR %s\n' % (label_name, hex(val[0] + val[1] - 1)))
        except KeyError:
            pass

    # Define for Board
    board_list = ["zcu102", "kc705", "kcu105"]
    root_compat = sdt.tree['/']['compatible'].value
    board_name = [b for b in board_list for board in root_compat if b in board]
    if board_name:
        plat.buf('\n\n#define XPS_BOARD_%s\n' % board_name[0].upper())
    
    # Memory Node related defines
    mem_ranges = get_memranges(tgt_node, sdt, options)
    for key, value in sorted(mem_ranges.items(), key=lambda e: e[1][1], reverse=True):
        start,size = value[0], value[1]
        plat.buf("\n#define XPAR_%s_BASEADDRESS %s" % (key.upper(), hex(start)))
        plat.buf("\n#define XPAR_%s_HIGHADDRESS %s" % (key.upper(), hex(start + size)))

    if cci_en:
        plat.buf("\n#define XPAR_CACHE_COHERENT \n")

    #CPU Freq related defines
    match_cpunodes = get_cpu_node(sdt, options)
    if re.search("microblaze", options['args'][0]):
        try:
            cpu_freq = match_cpunodes[0]['xlnx,freq'].value[0]
            plat.buf('\n#define XPAR_CPU_CORE_CLOCK_FREQ_HZ %s\n' % cpu_freq)
        except KeyError:
            pass
    else:
        try:
            cpu_freq = match_cpunodes[0]['xlnx,cpu-clk-freq-hz'].value[0]
            plat.buf('\n\n#define XPAR_CPU_CORE_CLOCK_FREQ_HZ %s\n' % cpu_freq)
            pss_ref = match_cpunodes[0]['xlnx,pss-ref-clk-freq'].value[0]
            plat.buf('#define XPAR_PSU_PSS_REF_CLK_FREQ_HZ %s\n' % pss_ref)
        except KeyError:
            pass

    #Defines for STDOUT and STDIN Baseaddress
    if chosen_node:
        stdin_node = get_stdin(sdt, chosen_node, total_nodes)
        val, size = scan_reg_size(stdin_node, stdin_node['reg'].value, 0)
        plat.buf("\n#define STDOUT_BASEADDRESS %s" % hex(val))
        plat.buf("\n#define STDIN_BASEADDRESS %s\n" % hex(val))

    plat.buf('\n#endif  /* end of protection macro */')
    plat.out(''.join(plat.get_buf()))

    return True
