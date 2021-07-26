#/*
# * Copyright (c) 2021 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.rao@xilinx.com>
# *       Naga Sureshkumar Relli <naga.sureshkumar.relli@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import struct
import sys
import types
import os
import getopt
import re
import subprocess
from pathlib import Path
from pathlib import PurePath
from lopper import Lopper
from lopper import LopperFmt
import lopper
from lopper_tree import *
from lopper_tree import LopperNode
from re import *
import yaml
import glob
from collections import OrderedDict

sys.path.append(os.path.dirname(__file__))
from baremetalconfig_xlnx import *

def is_compat( node, compat_string_to_test ):
    if re.search( "module,xlnx_overlay_dt", compat_string_to_test):
        return xlnx_generate_overlay_dt
    return ""

def get_label(sdt, symbol_node, node):
    prop_dict = Lopper.node_properties_as_dict(sdt.FDT, symbol_node.abs_path, False)
    match = [label for label,node_abs in prop_dict.items() if re.match(node_abs[0], node.abs_path) and len(node_abs[0]) == len(node.abs_path)]
    if match:
        return match[0]
    else:
        return None

def usage():
    prog = os.path.basename(sys.argv[0])
    print('Usage: %s <system device tree> -- <xlnx_overlay_dt.py> <machine name> <configuration>' % prog)
    print('  machine name:         cortexa53-zynqmp or cortexa72-versal')
    print('  configuration:        should be \'full\'' )

"""
This API generates the overlay dts file by taking pl.dtsi
generated from DTG++.
Args:
    tgt_node: is the baremetal config top level domain node number
    sdt:      is the system device-tree
    options:  There are two valid options
              Machine name as cortexa53-zynqmp or cortexa72-versal
              An optional argument full/partial
                  The default will be full
"""
def xlnx_generate_overlay_dt(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()

    symbol_node = ""
    plat = DtbtoCStruct('pl.dtsi')
    for node in root_sub_nodes:
        try:
            if node.name == "__symbols__":
                symbol_node = node
        except:
           pass

    # Get the list of nodes that are added to fragment@1
    # i,e fragment@1/overlay@1 node. we can ignore these nodes
    # while reading the tree to add nodes under fragment@2/overlay@2
    ignore_list = []
    for node in root_sub_nodes:

        try:
            if re.search("afi0" , node.name) or re.search("clocking" , node.name):
               ignore_list.append(node)
               
        except:
           pass

    # Initialize the variables to its defaults
    root = 1
    tab_len = 0
    platform = "None"
    config = "None"
    external_flag = 1
    parent_node = ""
    parent_tab = 0
    try:
        platform = options['args'][0]
        if options['args'][1] == "external_fpga":
            external_flag = options['args'][1]
        else:
            config = options['args'][1]
            external_flag = options['args'][2]
    except:
        pass

    # If no config option is provided then the default is
    # full bit stream support
    if config == "None":
        config = "full"

    if config != "full":
        print('%s is not a valid argument' % str(config))
        usage()
        sys.exit(1)        

    for node in root_sub_nodes:
        set_ignore = 0

        try:
            path = node.abs_path
            ret = path.split('/')
            pl = 0
            rt = len(ret)
            child_len = len(node.child_nodes)

            # Only check for the nodes under amba_pl
            for x in ret:
                if x == "amba_pl":
                   pl = 1

            if pl == 1:
                if child_len != 0 and node.name != "amba_pl":
                    if child_len > 1:
                        tab_len = 0
                    else:
                        tab_len = tab_len + int(child_len)

                if root == 1:
                    # Create overlay0: __overlay__ node as first node under fragment@0
                    plat.buf('/dts-v1/;')
                    plat.buf('\n/plugin/;')
                    plat.buf('\n/ {')
                    plat.buf('\n\tfragment@0 {')
                    if platform == "cortexa53-zynqmp":
                        plat.buf('\n\t\ttarget = <&fpga_full>;')
                    else:
                        plat.buf('\n\t\ttarget = <&fpga>;')
                    plat.buf('\n\t\toverlay0: __overlay__ {')
                    plat.buf('\n\t\t\t#address-cells = <2>;')
                    plat.buf('\n\t\t\t#size-cells = <2>;')
                    try:
                       if config == "full":
                           if platform == "cortexa53-zynqmp":
                               plat.buf('\n\t\t\t%s' % node['firmware-name'])
                           elif platform == "cortexa72-versal":
                               if external_flag == "external_fpga":
                                   plat.buf('\n\t\t\texternal-fpga-config;')
                               else:
                                   plat.buf('\n\t\t\t%s' % node['firmware-name'])
                           else:
                               print('%s is not a valid Machine' % str(platform))
                               sys.exit(1)        
                               
                    except:
                        pass

                    plat.buf('\n\t\t};')
                    plat.buf('\n\t};')

                    # Create overlay1: __overlay__ node under fragment@1
                    plat.buf('\n\tfragment@1 {')
                    plat.buf('\n\t\ttarget = <&amba>;')
                    plat.buf('\n\t\toverlay1: __overlay__ {')
                    plat.buf('\n\t\t\t#address-cells = <2>;')
                    plat.buf('\n\t\t\t#size-cells = <2>;')

                    # Add afi and clocking nodes to fragment@1     
                    if platform == "cortexa53-zynqmp":
                        for inode in ignore_list:
                            label_name = get_label(sdt, symbol_node, inode)
                            plat.buf('\n\t\t\t%s: %s {' % (label_name, inode.name))
                            for p in inode.__props__.values():
                                if re.search("phandle =", str(p)) or str(p) == '':
                                    continue
                                plat.buf('\n\t\t\t\t%s' % p)
                            plat.buf('\n\t\t\t};')
                        plat.buf('\n\t\t};')
                        plat.buf('\n\t};')
                    
                        # Create overlaye2: __overlay__ node under fragment@2
                        plat.buf('\n\tfragment@2 {')
                        plat.buf('\n\t\ttarget = <&amba>;')
                        plat.buf('\n\t\toverlay2: __overlay__ {')
                        plat.buf('\n\t\t\t#address-cells = <2>;')
                        plat.buf('\n\t\t\t#size-cells = <2>;')

                    root = 0

                # Now add all the nodes except the nodes that are added
                # that are added under fragment@1
                for ignoreip in ignore_list:
                    if re.match(ignoreip.name , node.name):
                       set_ignore = 1

                if set_ignore == 1:
                    continue

                # Add all the nodes exists under amba_pl to fragment@2
                if node.name != "amba_pl":
                    if parent_tab == 1 and parent_node == node.parent:
                        plat.buf('\n')
                        plat.buf('\t' * int(rt))
                        plat.buf('};')
                        parent_tab = 0

                    label_name = get_label(sdt, symbol_node, node)
                    plat.buf('\n')
                    rt = int(rt+1)
                    plat.buf('\t' * int(rt-1))
                    plat.buf('%s: %s {' % (label_name, node.name))
                    for p in node.__props__.values():
                        if re.search("phandle =", str(p)) or str(p) == '':
                            continue
                        plat.buf('\n')
                        plat.buf('\t' * int(rt))
                        if re.search("clocks =", str(p)):
                            plat.buf('%s' % node['clocks'])
                        else:
                            plat.buf('%s' % p)

                    plat.buf('\n')
                    plat.buf('\t' * int(rt-1))
                    if child_len < 1:
                        plat.buf('};')
                        for count in range(tab_len):
                            plat.buf('\n')
                            plat.buf('\t' * int(int(rt-2)-count))
                            plat.buf('};')

                        tab_len = 0
                    else:
                        if child_len > tab_len:
                            parent_node = node.parent
                            parent_tab = 1
        except:
           pass

    plat.buf('\n\t\t};')
    plat.buf('\n\t};')
    plat.buf('\n};')
    plat.out(''.join(plat.get_buf()))

    return True
