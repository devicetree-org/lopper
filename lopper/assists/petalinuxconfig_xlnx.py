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

def build_alias_map(sdt, aliases_node):
    """Build a mapping of node paths to their aliases.

    Uses Lopper's alias_node() method to properly resolve alias references
    to their canonical node paths, handling both direct paths and label references.

    Args:
        sdt: The system device tree object
        aliases_node: The /aliases node from the device tree

    Returns:
        Dictionary mapping node absolute paths to lists of alias names
    """
    alias_map = {}
    if not aliases_node:
        return alias_map

    try:
        for alias_name in aliases_node.__props__.keys():
            try:
                # Use Lopper's alias_node() to properly resolve the alias to its node
                resolved_node = sdt.tree.alias_node(alias_name)
                if resolved_node:
                    # Use the resolved node's absolute path as the key
                    node_path = resolved_node.abs_path
                    if node_path not in alias_map:
                        alias_map[node_path] = []
                    alias_map[node_path].append(alias_name)
            except (KeyError, AttributeError, TypeError):
                pass
    except (KeyError, AttributeError, TypeError):
        pass

    return alias_map

def get_aliases_for_node(alias_map, node):
    """Get all aliases that point to a given node.

    Args:
        alias_map: Dictionary mapping node paths to alias lists
        node: The node to find aliases for

    Returns:
        List of alias names that point to this node
    """
    if not alias_map or not node:
        return []
    return alias_map.get(node.abs_path, [])

def extract_serial_port_from_aliases(alias_list):
    """Extract serial port identifier from aliases.

    Args:
        alias_list: List of aliases for a node

    Returns:
        Serial port identifier (e.g., 'serial0', 'uart1') or None
    """
    if not alias_list:
        return None

    # Look for serial or uart aliases
    for alias in alias_list:
        if re.match(r'^(serial|uart)\d+$', alias):
            return alias

    return None

def extract_chosen_node_properties(chosen_node):
    """Extract all properties from the /chosen node into a dictionary.

    Args:
        chosen_node: The /chosen node from the device tree

    Returns:
        Dictionary of all chosen node properties and their values
    """
    chosen_dict = {}
    if not chosen_node:
        return chosen_dict

    try:
        for prop_name in chosen_node.__props__.keys():
            try:
                prop_value = chosen_node.propval(prop_name, list)
                if prop_value and prop_value != ['']:
                    # Store single values as strings, multiple values as lists
                    if len(prop_value) == 1:
                        chosen_dict[prop_name] = prop_value[0]
                    else:
                        chosen_dict[prop_name] = prop_value
            except (KeyError, AttributeError, TypeError, IndexError):
                pass
    except (KeyError, AttributeError, TypeError):
        pass

    return chosen_dict

def add_device_tree_metadata(target_dict, key, alias_map, node):
    """Add dt_node and aliases metadata to a device entry.

    Args:
        target_dict: Dictionary to update
        key: Key in the dictionary to update
        alias_map: Dictionary mapping node paths to alias lists
        node: The device tree node
    """
    target_dict[key].update({"dt_node": node.abs_path})
    node_aliases = get_aliases_for_node(alias_map, node)
    if node_aliases:
        target_dict[key].update({"aliases": node_aliases})

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
    aliases_node = None
    chosen_node = None
    root_compat = tgt_node.propval('compatible')
    yaml_file = options['args'][1]

    # Traverse the tree and find the nodes having status=ok property
    # and create a compatible_list from these nodes.
    # Also extract the aliases and chosen nodes for this domain
    for node in root_sub_nodes:
        try:
            if node.name == "__symbols__":
                symbol_node = node
            elif node.name == "chosen":
                # Domain-specific chosen node takes precedence
                chosen_node = node
            status = node["status"].value
            if "okay" in status:
                node_list.append(node)
        except (KeyError, AttributeError):
            pass

    # Get the aliases node from the device tree root and build alias map
    try:
        aliases_node = sdt.tree['/aliases']
    except (KeyError, AttributeError):
        aliases_node = None

    # If no domain-specific chosen node found, try global /chosen (for root domain)
    if not chosen_node:
        try:
            chosen_node = sdt.tree['/chosen']
        except (KeyError, AttributeError):
            chosen_node = None

    alias_map = build_alias_map(sdt, aliases_node)
    chosen_properties = extract_chosen_node_properties(chosen_node)

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
    tmp_dict = {'slaves': {}}  # Initialize slaves dict to prevent KeyError
    proc_name = ""
    device_type_dict = {}

    if not os.path.isfile(yaml_file):
        print(f"Error: YAML file not found: {yaml_file}")
        return False

    with open(yaml_file, 'r') as stream:
        schema = yaml.safe_load(stream)
        res_list = list(schema.keys())
        device = sdt.tree['/'].propval('device_id')
        if device != ['']:
            device_type_dict['device_id'] = device[0]

        # Add chosen node properties early in the output for better organization
        if chosen_properties:
            device_type_dict['chosen'] = chosen_properties

        # Check if memory device type exists in schema before fetching memory ranges
        has_memory_type = any(re.search("memory", schema[res]['device_type']) for res in res_list)
        if has_memory_type:
            mem_ranges, label_names = get_memranges(tgt_node, sdt, options)
            memnode_list = sdt.tree.nodes('/memory@.*')

        # Build device type mapping for non-processor/non-memory devices
        device_type_map = {}
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
                    # Add DT node path and aliases
                    add_device_tree_metadata(device_type_dict['processor'], label_name, alias_map, match_cpunode)
                    device_type_dict['processor'][label_name].update({"slaves_strings": " ".join(nodename_list)})
            elif re.search("memory", dev_type):
                ### Memory Handling (using pre-fetched mem_ranges and label_names)
                for mem_name,mem in mem_ranges.items():
                    mem_label = label_names.get(mem_name, mem_name)
                    try:
                        mem_node = [node for node in memnode_list if re.search(mem_name[:-2], node.propval('xlnx,ip-name', list)[0])]
                    except (IndexError, AttributeError, TypeError):
                        mem_node = None

                    # Determine device_type
                    device_type = "memory"
                    if mem_node and len(mem_node) > 0 and mem_node[0].propval('memory_type') != ['']:
                        device_type = mem_node[0].propval('memory_type', list)[0]

                    tmp_dict['slaves'][mem_label] = {"device_type": device_type}

                    # Add DT node path and aliases for memory nodes if mem_node exists
                    if mem_node and len(mem_node) > 0:
                        add_device_tree_metadata(tmp_dict['slaves'], mem_label, alias_map, mem_node[0])

                    tmp_dict['slaves'][mem_label].update({"ip_name":mem_name[:-2]})
                    tmp_dict['slaves'][mem_label].update({"baseaddr":hex(mem[0])})
                    tmp_dict['slaves'][mem_label].update({"highaddr":hex(mem[0] + mem[1])})
            else:
                device_type_map[res] = {
                    'dev_type': dev_type,
                    'driver_compatlist': compat_list(schema[res])
                }

        for node in mapped_nodelist:
            label_name = get_label(sdt, symbol_node, node)

            try:
                compatible_list = node["compatible"].value
            except (KeyError, AttributeError):
                compatible_list = None

            matched = False
            if compatible_list:
                for res, info in device_type_map.items():
                    match = [compat for compat in compatible_list if compat in info['driver_compatlist']]
                    if match:
                        matched = True
                        dev_type = info['dev_type']
                        ipname = node.propval('xlnx,ip-name')
                        try:
                            if re.search("serial", dev_type):
                                addr,size = scan_reg_size(node, node['reg'].value, 0)
                                tmp_dict['slaves'][label_name] = {"device_type":"serial"}
                                tmp_dict['slaves'][label_name].update({"ip_name":ipname[0]})
                                tmp_dict['slaves'][label_name].update({"baseaddr":hex(addr)})
                                # Add DT node path and aliases using helper function
                                add_device_tree_metadata(tmp_dict['slaves'], label_name, alias_map, node)
                                # Extract serial port identifier from aliases if available
                                node_aliases = get_aliases_for_node(alias_map, node)
                                serial_port = extract_serial_port_from_aliases(node_aliases)
                                if serial_port:
                                    tmp_dict['slaves'][label_name].update({"serial_port": serial_port})
                            else:
                                tmp_dict['slaves'][label_name] = {"device_type":dev_type}
                                tmp_dict['slaves'][label_name].update({"ip_name":ipname[0]})
                                # Add DT node path and aliases for non-serial devices
                                add_device_tree_metadata(tmp_dict['slaves'], label_name, alias_map, node)
                        except (KeyError, AttributeError, TypeError):
                            pass
                        break

            # If no match found (or no compatible property), add with just ip_name
            if not matched:
                ipname = node.propval('xlnx,ip-name')
                try:
                    if ipname and ipname[0]:
                        tmp_dict['slaves'][label_name] = {"ip_name": ipname[0]}
                        # Add DT node path and aliases even for unmatched devices
                        add_device_tree_metadata(tmp_dict['slaves'], label_name, alias_map, node)
                except (IndexError, TypeError):
                    pass
        # Only update if processor was configured
        if 'processor' in device_type_dict and proc_name and proc_name in device_type_dict['processor']:
            device_type_dict['processor'][proc_name].update(tmp_dict)

        with open("sys_hw_data.yaml", "w") as fd:
            fd.write(yaml.dump(device_type_dict, Dumper=YamlDumper, default_flow_style=False, sort_keys=False, indent=4, width=32768))

    return True
