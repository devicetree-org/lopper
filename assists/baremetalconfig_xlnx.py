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
import getopt
import re
from pathlib import Path
from pathlib import PurePath
from io import StringIO
import contextlib
import importlib
from lopper import Lopper
from lopper import LopperFmt
import lopper
from lopper_tree import *
from re import *
import yaml


def item_generator(json_input, lookup_key):
    if isinstance(json_input, dict):
        for k, v in json_input.items():
            if k == lookup_key:
                if isinstance(v, str):
                    yield [v]
                else:
                    yield v
            else:
                for child_val in item_generator(v, lookup_key):
                    yield child_val
    elif isinstance(json_input, list):
        for item in json_input:
            for item_val in item_generator(item, lookup_key):
                yield item_val

# This API reads the schema and returns the compatible list
def compat_list(schema):
    if 'compatible' in schema['properties'].keys():
        sch = schema['properties']['compatible']
        compatible_list = []
        for l in item_generator(sch, 'enum'):
            compatible_list.extend(l)

        for l in item_generator(sch, 'const'):
            compatible_list.extend(l)

        if 'contains' in sch.keys():
            for l in item_generator(sch['contains'], 'enum'):
                compatible_list.extend(l)

            for l in item_generator(sch['contains'], 'const'):
                compatible_list.extend(l)
        compatible_list = list(set(compatible_list))
        return compatible_list

"""
This API scans the device-tree node and returns the address
and size of the reg property for the user provided index.

Args:
    node: LopperNode object
    value: property value
    idx: index
"""
def scan_reg_size(node, value, idx):
    na = node.parent["#address-cells"].value[0]
    ns = node.parent["#size-cells"].value[0]
    cells = na + ns
    reg = 0
    size = 0
    if cells > 2:
        reg1 = value[cells * idx]
        if reg1 != 0:
            val = str(value[cells * idx + 1])
            pad = 8 - len(val)
            val = val.ljust(pad + len(val), '0')
            reg = int((str(reg1) + val), base=16)
        else:
            reg = value[cells * idx + 1]

        size1 = value[cells * idx + na]
        if size1 != 0:
            val = value[cells * idx + ns + 1]
            size = size1 + val
        else:
            size = value[cells * idx + ns + 1]
    elif cells == 2:
        reg = value[idx * cells]
        size = value[idx * cells + 1]
    else:
        reg = value[0]
    return reg, size

def get_interrupt_prop(fdt, node, value):
    intr = []
    inp =  node['interrupt-parent'].value[0]
    intr_parent = fdt.node_offset_by_phandle(inp)
    inc = Lopper.property_get(fdt, intr_parent, '#interrupt-cells')
    """
    Baremetal Interrupt Property format:
        bits[11:0]  interrupt-id
        bits[15:12] trigger type and level flags
        bits[19:16] CPU Mask
        bit[20] interrupt-type (1: PPI, 0: SPI)
    """
    # Below logic converts the interrupt propery value to baremetal
    # interrupt property format.
    nintr = len(value)/inc
    tmp = inc % 2
    for val in range(0, int(nintr)):
        intr_sensitivity = value[tmp+1] << 12
        intr_id = value[tmp]
        # Convert PPI interrupt to baremetal interrupt format
        if value[tmp-1] == 1 and inc == 3:
            intr_sensitivity = (value[tmp+1] & 0xF) << 12
            cpu_mask = (value[tmp+1] & 0xFF00) << 8
            ppi_type = 1 << 20
            intr_id = intr_id + cpu_mask + ppi_type
        intr.append(hex(intr_id + intr_sensitivity))
        tmp += inc

    return intr

#Return the base address of the parent node.
def get_phandle_regprop(sdt, prop, value):
    parent_node = sdt.FDT.node_offset_by_phandle(value[0])
    name = sdt.FDT.get_name(parent_node)
    root_sub_nodes = sdt.tree['/'].subnodes()
    parent_node = [node for node in root_sub_nodes if re.search(name, node.name)]
    reg, size = scan_reg_size(parent_node[0], parent_node[0]['reg'].value, 0)
    # Special handling for Soft Ethernet(1/2.5G, and 10G/25G MAC) axistream-connected property
    if prop == "axistream-connected":
        compat = parent_node[0]['compatible'].value[0]
        axi_fifo = re.search("xlnx,axi-fifo", compat)
        axi_dma = re.search("xlnx,eth-dma", compat)
        axi_mcdma = re.search("xlnx,eth-mcdma", compat)
        if axi_fifo:
            reg += 1
        elif axi_dma:
            reg += 2
        elif axi_mcdma:
            reg += 3
    return reg

#Return the base address of the interrupt parent.
def get_intrerrupt_parent(sdt, value):
    intr_parent = sdt.FDT.node_offset_by_phandle(value[0])
    name = sdt.FDT.get_name(intr_parent)
    root_sub_nodes = sdt.tree['/'].subnodes()
    intr_node = [node for node in root_sub_nodes if re.search(name, node.name)]
    reg, size = scan_reg_size(intr_node[0], intr_node[0]['reg'].value, 0)
    """
    Baremetal Interrupt Parent Property Format:
        bits[0]    Interrupt parent type (0: GIC, 1: AXI INTC)
        bits[31:1] Base Address of the interrupt parent
    """
    compat = intr_node[0]['compatible'].value
    axi_intc = [item for item in compat if "xlnx,xps-intc-1.00.a" in item]
    if axi_intc:
        reg += 1
    return reg

class DtbtoCStruct(object):
    def __init__(self, out_file):
        self._outfile = open(out_file, 'w')
        self._lines = []

    def out(self, line):
        """Output a string to the output file

        Args:
            line: String to output
        """
        self._outfile.write(line)

    def buf(self, line):
        """Buffer up a string to send later

        Args:
            line: String to add to our 'buffer' list
        """
        self._lines.append(line)

    def get_buf(self):
        """Get the contents of the output buffer, and clear it

        Returns:
            The output buffer, which is then cleared for future use
        """
        lines = self._lines
        self._lines = []
        return lines

def is_compat(node, compat_string_to_test):
    if re.search( "module,baremetalconfig_xlnx", compat_string_to_test):
        return xlnx_generate_bm_config
    return ""

# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
# options: baremetal driver meta-data file path
def xlnx_generate_bm_config(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    node_list = []
    # Traverse the tree and find the nodes having status=ok property
    for node in root_sub_nodes:
        try:
            status = node["status"].value
            if "okay" in status:
                node_list.append(node)
        except:
           pass

    yaml_file = options['args']
    yamlfile = yaml_file[0]
    driver_compatlist = []
    driver_proplist = []
    # Read the yaml file and get the driver supported compatible list
    # and config data file required driver properties
    with open(yamlfile, 'r') as stream:
        schema = yaml.safe_load(stream)
        driver_compatlist = compat_list(schema)
        driver_proplist = schema['required']

    driver_nodes = []
    for compat in driver_compatlist:
        for node in node_list:
           compat_string = node['compatible'].value[0]
           if compat in compat_string:
               driver_nodes.append(node) 

    # config file name: x<driver_name>_g.c 
    driver_name = yamlfile.split('/')[-1].split('.')[0]
    config_struct = str("X") + driver_name.capitalize() + str("_Config")
    outfile = str("x") + driver_name + str("_g.c")

    plat = DtbtoCStruct(outfile)
    for index,node in enumerate(driver_nodes):
        if index == 0:
            plat.buf('#include "x%s.h"\n' % driver_name)
            plat.buf('\n%s %s __attribute__ ((section (".drvcfg_sec"))) = {\n' % (config_struct, config_struct + str("Table[]")))
        for i, prop in enumerate(driver_proplist):
            pad = 0
            phandle_prop = 0
            # Few drivers has multiple data interface type (AXI4 or AXI4-lite),
            # Driver config structures of these SoftIP's contains baseaddress entry for each possible data interface type.
            # Device-tree node reg property may or may not contain all the possible entries that driver config structure
            # is expecting, In that case we need to add dummy entries(0xFF) in the config structure in order to avoid
            # compilation errors.
            #
            # Yaml meta-data representation/syntax will be like below
            # reg: <range of baseaddress>
            # interrupts: <supported range of interrupts>
            if isinstance(prop, dict):
               pad = list(prop.values())[0]
               prop = list(prop.keys())[0]
               if pad == "phandle":
                   phandle_prop = 1
            if i == 0:
                 plat.buf('\n\t{')

            if prop == "reg":
                val, size = scan_reg_size(node, node[prop].value, 0)
                plat.buf('\n\t\t%s' % hex(val))
                if pad:
                    for j in range(1, pad):
                        try:
                            val, size = scan_reg_size(node, node[prop].value, j)
                            plat.buf(',\n\t\t%s' % hex(val))
                        except IndexError:
                            plat.buf(',\n\t\t%s' % hex(0xFFFF))
            elif prop == "compatible":
                plat.buf('\n\t\t%s' % '"{}"'.format(node[prop].value[0]))
            elif prop == "interrupts":
                try:
                    intr = get_interrupt_prop(sdt.FDT, node, node[prop].value)
                except KeyError:
                    intr = [hex(0xFFFF)]

                if pad:
                    plat.buf('\n\t\t{')
                    for j in range(0, pad):
                        try:
                            plat.buf('%s' % intr[j])
                        except IndexError:
                            plat.buf('%s' % hex(0xFFFF))
                        if j != pad-1:
                            plat.buf(',  ')
                    plat.buf('}')
                else:
                    plat.buf('\n\t\t%s' % intr[0])
            elif prop == "interrupt-parent":
                try:
                    intr_parent = get_intrerrupt_parent(sdt, node[prop].value)
                except KeyError:
                    intr_parent = 0xFFFF
                plat.buf('\n\t\t%s' % hex(intr_parent))
            elif prop == "child,required":
                plat.buf('\n\t\t{')
                for j,child in enumerate(list(node.child_nodes.items())):
                    plat.buf('\n\t\t\t{')
                    for k,p in enumerate(pad):
                        plat.buf('\n\t\t\t\t%s' % hex(child[1][p].value[0]))
                        if k != (len(pad) - 1):
                            plat.buf(',')
                        plat.buf(' /* %s */' % p)
                    if j != (len(list(node.child_nodes.items())) - 1):
                        plat.buf('\n\t\t\t},')
                    else:
                        plat.buf('\n\t\t\t}')
                plat.buf('\n\t\t}')
            elif phandle_prop:
                try:
                    prop_val = get_phandle_regprop(sdt, prop, node[prop].value)
                except KeyError:
                    prop_val = 0
                plat.buf('\n\t\t%s' % hex(prop_val))
            else:
                try:
                    prop_val = node[prop].value
                    # For boolean property if present LopperProp will return
                    # empty string convert it to baremetal config struct expected value
                    if '' in prop_val:
                        prop_val = [1]
                except KeyError:
                    prop_val = [0]

                if len(prop_val) > 1:
                    plat.buf('\n\t\t{')
                    for k,item in enumerate(prop_val):
                        plat.buf('%s' % item)
                        if k != len(prop_val)-1:
                            plat.buf(',  ')
                    plat.buf('}')
                else:
                    plat.buf('\n\t\t%s' % hex(prop_val[0]))

            if i == len(driver_proplist)-1:
                plat.buf(' /* %s */' % prop)
                plat.buf('\n\t},')
            else:
                plat.buf(',')
                plat.buf(' /* %s */' % prop)
        if index == len(driver_nodes)-1:
            plat.buf('\n};')
    plat.out(''.join(plat.get_buf()))

    return True
