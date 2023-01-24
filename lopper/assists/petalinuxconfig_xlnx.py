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

class YamlDumper(yaml.Dumper):
    def increase_indent(self, flow=False, indentless=False):
        return super(YamlDumper, self).increase_indent(flow, False)

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
    yaml_file = options['args'][1]

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

    mapped_nodelist = get_mapped_nodes(sdt, node_list, options)
    nodename_list = []
    for node in mapped_nodelist:
        nodename_list.append(get_label(sdt, symbol_node, node))

    """
    1. Create unique dictonary based on the device_type and nodes
    2. Update the dictonray key with the match node label name
    3. For Memory Device_type #special handling
    4. For uart device_type add default baud rate 
    5. ???
    """
    tmp_dict = {}
    proc_name = ""
    device_type_dict = {}
    with open(yaml_file, 'r') as stream:
        schema = yaml.safe_load(stream)
        res_list = list(schema.keys())
        tmp_node_list = []
        device = sdt.tree['/'].propval('device_id')
        if device != ['']:
            device_type_dict['device_id'] = device[0]
        for res in res_list:
            dev_type = schema[res]['device_type']
            if re.search("processor", dev_type):
                ### Processor Handling
                match_cpunode = get_cpu_node(sdt, options)
                if match_cpunode:
                    label_name = get_label(sdt, symbol_node, match_cpunode)
                    ip_name = match_cpunode.propval('xlnx,ip-name')
                    proc_name = label_name
                    ipname = {"arch": "aarch64", "ip_name":ip_name[0]}
                    device_type_dict['processor'] = {label_name:ipname}
                    device_type_dict['processor'][label_name].update({"slaves_strings": " ".join(nodename_list)})
            elif re.search("memory", dev_type):
                ### Memory Handling
                mem_ranges = get_memranges(tgt_node, sdt, options)
                index = 0
                for mem_name,mem in mem_ranges.items():
                    if index == 0:
                        tmp_dict['slaves'] = {mem_name: "None"}
                    else:
                        tmp_dict['slaves'].update({mem_name: "None"})
                    tmp_dict['slaves'][mem_name] = {"device_type":"memory"}
                    tmp_dict['slaves'][mem_name].update({"ip_name":mem_name[:-2]})
                    tmp_dict['slaves'][mem_name].update({"baseaddr":hex(mem[0])})
                    tmp_dict['slaves'][mem_name].update({"highaddr":hex(mem[0] + mem[1])})
                    index += 1
            else:
                for node in mapped_nodelist:
                    compatible_list = node["compatible"].value
                    driver_compatlist = compat_list(schema[res])
                    match = [compat for compat in compatible_list if compat in driver_compatlist]
                    ipname = node.propval('xlnx,ip-name')
                    if match:
                        tmp_node_list.append(node)
                        label_name = get_label(sdt, symbol_node, node)
                        try:
                            if re.search("serial", dev_type):
                                baud_rate = node.propval('xlnx,baudrate')
                                addr,size = scan_reg_size(node, node['reg'].value, 0)
                                tmp_dict['slaves'][label_name] = {"device_type":"serial"}
                                tmp_dict['slaves'][label_name].update({"ip_name":ipname[0]})
                                tmp_dict['slaves'][label_name].update({"baseaddr":hex(addr)})
                            else:
                                tmp_dict['slaves'][label_name] = {"device_type":dev_type}
                                tmp_dict['slaves'][label_name].update({"ip_name":ipname[0]})
                        except KeyError:
                            pass

        mapped_nodelist = [node for node in mapped_nodelist if node not in tmp_node_list]
        for node in mapped_nodelist:
            label_name = get_label(sdt, symbol_node, node)
            try:
                ipname = node.propval('xlnx,ip-name')
                if ipname[0]:
                    tmp_dict['slaves'][label_name] = {"ip_name":ipname[0]}
            except:
                pass
        device_type_dict['processor'][proc_name].update(tmp_dict)

        with open("petalinux_config.yaml", "w") as fd:
            fd.write(yaml.dump(device_type_dict, Dumper=YamlDumper, default_flow_style=False, sort_keys=False, indent=4, width=32768))

    return True
