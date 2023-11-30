#/*
# * Copyright (C) 2023 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *     Appana Durga Kedareswara rao <appana.durga.kedareswara.rao@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import yaml
import sys
import os
import glob

sys.path.append(os.path.dirname(__file__))

from baremetalconfig_xlnx import compat_list, get_cpu_node, get_mapped_nodes, get_label
from common_utils import to_cmakelist
from domain_access import update_mem_node

def is_compat( node, compat_string_to_test ):
    if "module,gen_domain_dts" in compat_string_to_test:
        return xlnx_generate_domain_dts
    return ""

# tgt_node: is the top level domain node
# sdt: is the system device-tree
# options: User provided options (processor name)
def xlnx_generate_domain_dts(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
   
    """
    When user provided processor name and system device-tree as input this
    assist produces as a dts file which contains nodes mapped to that 
    processor instance.
    1. Keep the nodes which has status ok and mapped in the address-map property.
    2. Keep the memory nodes which are mapped in address-map and update them as per
    address-map range.
    3. Delete the other cpu cluster nodes.
    4. Rename the procsessor cpu cluster node to cpus.
    5. Remove delete node lables from symbol node.
    """
    machine = options['args'][0]
    symbol_node = sdt.tree['/__symbols__']
    # Get the cpu node for a given Processor
    match_cpunode = get_cpu_node(sdt, options)
    address_map = match_cpunode.parent["address-map"].value
    na = match_cpunode.parent["#ranges-address-cells"].value[0]
    ns = match_cpunode.parent["#ranges-size-cells"].value[0]

    # Delete other CPU Cluster nodes
    cpunode_list = sdt.tree.nodes('/cpu.*@.*')
    clustercpu_nodes = []
    for node in cpunode_list:
        if node.parent.phandle != match_cpunode.parent.phandle and node.phandle != match_cpunode.parent.phandle:
            clustercpu_nodes.append(node.parent)
    clustercpu_nodes = list(dict.fromkeys(clustercpu_nodes))
    for node in clustercpu_nodes:
        if node.name != '':
            sdt.tree.delete(node)

    cells = na + ns
    tmp = na
    all_phandles = []
    while tmp < len(address_map):
        all_phandles.append(address_map[tmp])
        tmp = tmp + cells + na + 1

    node_list = []
    for node in root_sub_nodes:
        if node.propval('status') != ['']:
            if "okay" in node.propval('status', list)[0]:
                node_list.append(node)
        if node.propval('device_type') != ['']:
            if "memory" in node.propval('device_type', list)[0]:
                node_list.append(node)

    mapped_nodelist = get_mapped_nodes(sdt, node_list, options)
    mapped_nodelist.append(symbol_node)
    mapped_nodelist.append(sdt.tree['/aliases'])

    # Update memory nodes as per address-map cluster mapping
    memnode_list = sdt.tree.nodes('/memory@.*')
    for node in memnode_list:
        # Check whether the memory node is mapped to cpu cluster or not
        mem_phandles = [handle for handle in all_phandles if handle == node.phandle]
        prop_val = []
        if mem_phandles:
            mem_phandles = list(dict.fromkeys(mem_phandles))
            # Get all indexes of the address-map for this node
            tmp = na
            indx_list = []
            while tmp < len(address_map):
                for val in mem_phandles:
                    if val == address_map[tmp]:
                        indx_list.append(tmp)
                tmp = tmp + cells + na + 1
            for inx in indx_list:
                start = [address_map[inx+i+1] for i in range(na)]
                if na == 2 and start[0] != 0:
                    val = str(start[1])
                    pad = 8 - len(val)
                    val = val.ljust(pad + len(val), '0')
                    reg = int((str(hex(start[0])) + val), base=16)
                    prop_val.append(reg)
                elif na == 2:
                    prop_val.append(start[1])
                else:
                    prop_val.append(start[0])
                prop_val.append(address_map[inx+2*na])
        modify_val = update_mem_node(node, prop_val)
        node['reg'].value = modify_val

    for node in root_sub_nodes:
        if node not in mapped_nodelist:
            if node.propval('device_type') != ['']:
                if "cpu" in node.propval('device_type', list)[0]:
                    continue
            if node.propval('status') != ['']:
                sdt.tree.delete(node)

    # Remove symbol node referneces
    prop_dict = symbol_node.__props__.copy()
    match_label_list = []
    for node in sdt.tree[tgt_node].subnodes():
        matched_label = get_label(sdt, symbol_node, node)
        if matched_label:
            match_label_list.append(matched_label)
    for prop,node1 in prop_dict.items():
        if prop not in match_label_list:
            sdt.tree['/__symbols__'].delete(prop)

    # Add new property which will be consumed by other assists
    sdt.tree['/']['pruned-sdt'] = 1

    return True
