#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.rao@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */
import sys
import types
import os
import re
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
import json

sys.path.append(os.path.dirname(__file__))
from baremetalconfig_xlnx import *
from baremetal_xparameters_xlnx import *

def is_compat(node, compat_string_to_test):
    if re.search( "module,petalinuxconfig_xlnx", compat_string_to_test):
        return xlnx_generate_petalinux_config
    return ""

# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
def xlnx_generate_petalinux_config(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    compatible_list = []
    driver_list = []
    node_list = []
    symbol_node = ""
    root_compat = tgt_node.propval('compatible')
    yaml_file = options['args'][0]

    if any("zynqmp" in compat for compat in root_compat):
        options['args'] = ["cortexa53-zynqmp", yaml_file]
    
    # Traverse the tree and find the nodes having status=ok property
    # and create a compatible_list from these nodes.
    for node in root_sub_nodes:
        try:
            if node.name == "__symbols__":
                symbol_node = node
            status = node["status"].value
            if "okay" in status:
                node_list.append(node)
        except:
           pass

    """
    1. Create unique dictonary based on the device_type and nodes
    2. Update the dictonray key with the match node label name
    3. For Memory Device_type #special handling
    4. For uart device_type add default baud rate 
    5. ???
    """
    with open(yaml_file, 'r') as stream:
        schema = yaml.safe_load(stream)
        res_list = list(schema.keys())
        device_type_dict = {}
        for res in res_list:
            dev_type = schema[res]['device_type']
            if re.search("processor", dev_type):
                ### Processor Handling
                match_cpunodes = get_cpu_node(sdt, options)
                if match_cpunodes:
                    for node in match_cpunodes:
                        label_name = get_label(sdt, symbol_node, node)
                        try:
                            ## Check if label already exists
                            if not label_name in device_type_dict['processor']:
                                device_type_dict['processor'].append(label_name)
                        except KeyError:
                            device_type_dict['processor'] = [label_name]
            elif re.search("memory", dev_type):
                ### Memory Handling
                mem_ranges = get_memranges(tgt_node, sdt, options)
                for mem_name,mem in mem_ranges.items():
                    try:
                        if not mem in device_type_dict['memory']:
                            device_type_dict['memory'].append(mem_name)
                            device_type_dict['memory'].append(mem)
                    except KeyError:
                        device_type_dict['memory'] = [mem_name, mem]
            else:
                mapped_nodelist = get_mapped_nodes(sdt, node_list, options)
                compatible_list.append(node["compatible"].value)
                for node in mapped_nodelist:
                    compatible_list = node["compatible"].value
                    driver_compatlist = compat_list(schema[res])
                    match = [compat for compat in compatible_list if compat in driver_compatlist]
                    if match:
                        label_name = get_label(sdt, symbol_node, node)
                        try:
                            if not label_name in device_type_dict[dev_type]:
                                if re.search("serial", dev_type):
                                    baud_rate = node.propval('xlnx,baudrate')
                                    device_type_dict[dev_type].append(label_name)
                                    device_type_dict[dev_type].append(baud_rate[0])
                                else:
                                    device_type_dict[dev_type].append(label_name)
                        except KeyError:
                            if re.search("serial", dev_type):
                                baud_rate = node.propval('xlnx,baudrate')
                                device_type_dict[dev_type] = [label_name, baud_rate[0]]
                            else:
                                device_type_dict[dev_type] = [label_name]

        with open("petalinux_config.json", "w") as fd:
            fd.write(json.dumps(device_type_dict, sort_keys=True,
                     indent=4, separators=(',', ': ')))

    return True
