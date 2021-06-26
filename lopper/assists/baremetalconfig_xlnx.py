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
from lopper.tree import *
from re import *
import yaml

sys.path.append(os.path.dirname(__file__))
from bmcmake_metadata_xlnx import *

def get_cpu_node(sdt, options):
    # Yocto Machine to CPU compat mapping
    cpu_dict = {'cortexa53-zynqmp': 'arm,cortex-a53', 'cortexa72-versal':'arm,cortex-a72', 'cortexr5-zynqmp': 'arm,cortex-r5', 'cortexa9-zynq': 'arm,cortex-a9',
                'microblaze-pmu': 'pmu-microblaze', 'microblaze-plm': 'pmc-microblaze', 'microblaze-psm': 'psm-microblaze', 'cortexr5-versal': 'arm,cortex-r5'}
    nodes = sdt.tree.nodes('/cpu.*')
    machine = options['args'][0]
    match_cpunodes = []
    match = cpu_dict[machine]
    for node in nodes:
        try:
            compat = node['compatible'].value[0]
            match = cpu_dict[machine]
            if compat == match:
                match_cpunodes.append(node)
        except KeyError:
            pass

    return match_cpunodes

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
            val = str(hex(value[cells * idx + 1]))[2:]
            pad = 8 - len(val)
            val = val.ljust(pad + len(val), '0')
            reg = int((str(hex(reg1)) + val), base=16)
        else:
            reg = value[cells * idx + 1]

        size1 = value[cells * idx + na]
        if size1 != 0:
            val = str(hex(value[cells * idx + ns + 1]))[2:]
            pad = 8 - len(val)
            val = val.ljust(pad + len(val), '0')
            size = int((str(hex(size1)) + val), base=16)
        else:
            size = value[cells * idx + ns + 1]
    elif cells == 2:
        reg = value[idx * cells]
        size = value[idx * cells + 1]
    else:
        reg = value[0]
    return reg, size

def get_interrupt_prop(sdt, node, value):
    intr = []
    inp =  node['interrupt-parent'].value[0]
    intr_parent = [node for node in sdt.tree['/'].subnodes() if node.phandle == inp]
    inc = intr_parent[0]["#interrupt-cells"].value[0]
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
    parent_node = [node for node in sdt.tree['/'].subnodes() if node.phandle == value[0]]
    reg, size = scan_reg_size(parent_node[0], parent_node[0]['reg'].value, 0)
    # Special handling for Soft Ethernet(1/2.5G, and 10G/25G MAC) axistream-connected property
    if prop == "axistream-connected":
        compat = parent_node[0]['compatible'].value
        axi_fifo = [item for item in compat if "xlnx,axi-fifo" in item]
        axi_dma = [item for item in compat if "xlnx,eth-dma" in item]
        axi_mcdma = [item for item in compat if "xlnx,eth-mcdma" in item]
        if axi_fifo:
            reg += 1
        elif axi_dma:
            reg += 2
        elif axi_mcdma:
            reg += 3
    return reg

#Return the base address of the interrupt parent.
def get_intrerrupt_parent(sdt, value):
    intr_node = [node for node in sdt.tree['/'].subnodes() if node.phandle == value[0]]
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

"""
This API scans the device-tree node and returns the address
and size of the ranges property for the user provided index.

Args:
    node: LopperNode object
    value: property value
    idx: index
"""
def scan_ranges_size(node, value, idx):
    na = node["#address-cells"].value[0]
    ns = node["#size-cells"].value[0]
    cells = na + ns + 2

    addr = 0
    size = 0

    addr1 = value[cells * idx + 1]
    if addr1 != 0:
        val = str(value[cells * idx + ns])
        pad = 8 - len(val)
        val = val.ljust(pad + len(val), '0')
        addr = int((str(hex(addr1)) + val), base=16)
    else:
        addr = value[cells * idx + ns]

    size1 = value[cells * idx + na + 2]
    if size1 != 0:
        val = str(hex(value[cells * idx + na + 3]))[2:]
        pad = 8 - len(str(hex(size1)))
        val = val.ljust(pad + len(str(hex(size1))), '0')
        size = int((str(hex(size1)) + val), base=16)
    else:
        size = value[cells * idx + na + ns + 1]
    return addr, size

def get_clock_prop(sdt, value):
    clk_node = [node for node in sdt.tree['/'].subnodes() if node.phandle == value[0]]
    """
    Baremetal clock format:
        bits[0] clock parent(controller) type(0: ZynqMP clock controller)
        bits[31:1] clock value
    """
    compat = clk_node[0]['compatible'].value
    return value[1]

def get_pci_ranges(node, value, pad):
    pci_ranges = []
    for i in range(pad):
        try:
            reg, size = scan_ranges_size(node, value, i)
            high_addr = reg + size - 1
            pci_ranges.append(hex(reg))
            pci_ranges.append(hex(high_addr))
        except IndexError:
            pci_ranges.append(hex(0))
            pci_ranges.append(hex(0))
    return pci_ranges

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

def get_stdin(sdt, chosen_node, node_list):
    prop_val = chosen_node['stdout-path'].value
    serial_node = sdt.tree.alias_node(prop_val[0].split(':')[0])
    match = [x for x in node_list if re.search(x.name, serial_node.name)]
    return match[0]

def get_mapped_nodes(sdt, node_list, options):
    # Yocto Machine to CPU compat mapping
    match_cpunodes = get_cpu_node(sdt, options)

    all_phandles = []
    address_map = match_cpunodes[0].parent["address-map"].value
    na = match_cpunodes[0].parent["#ranges-address-cells"].value[0]
    ns = match_cpunodes[0].parent["#ranges-size-cells"].value[0]
    cells = na + ns
    tmp = na
    while tmp < len(address_map):
        all_phandles.append(address_map[tmp])
        tmp = tmp + cells + na + 1
    # Remove duplicate phandle
    all_phandles = list(dict.fromkeys(all_phandles))

    valid_nodes = [node for node in node_list for handle in all_phandles if handle == node.phandle]

    # Get Access or Domain list for the given CPU/Machine
    root_node = sdt.tree['/']
    root_sub_nodes = root_node.subnodes()
    domain_nodes = []
    for node in root_sub_nodes:
        try:
            is_subsystem = node["cpus"].value
            if is_subsystem:
                domain_nodes.append(node)
        except:
            pass

    if domain_nodes:
        # Get Matched Domains for a given cpu cluster
        cpu_phandle = match_cpunodes[0].parent.phandle
        domain_node = [node for node in domain_nodes if node["cpus"].value[0]  == cpu_phandle]

        if domain_node:
            # Get valid node list by reading access property
            access_phandle_list = domain_node[0]["access"].value

            # Check whether Domain node has any shared resource group
            try:
               rsrc_grp_phandle = domain_node[0]["include"].value[0]
               node = [node for node in sdt.tree['/'].subnodes() if node.phandle == rsrc_grp_phandle]
               rsrc_grp_phanlde = node[0]["access"].value
               access_phandle_list.extend(rsrc_grp_phanlde)
            except KeyError:
               pass

            mapped_phandle = [handle for handle in access_phandle_list for phandle in all_phandles if handle == phandle]
            if len(access_phandle_list) != len(mapped_phandle):
               wrong_mapped_handle = [handle for handle in access_phandle_list if handle not in mapped_phandle]
               wrong_nodes = [node.name for node in sdt.tree['/'].subnodes() for handle in wrong_mapped_handle if handle == node.phandle]
               if wrong_nodes:
                  print("[WARNING]: invalid node %s reference mentioned in access property please delete the references" % ','.join(wrong_nodes))

            domain_valid_nodes = [node for node in node_list for handle in mapped_phandle if handle == node.phandle]
            valid_nodes.extend(domain_valid_nodes)
            # Remove duplicate nodes
            valid_nodes = list(dict.fromkeys(valid_nodes))
        return valid_nodes
    else:
        return valid_nodes

# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
# options: baremetal driver meta-data file path
def xlnx_generate_bm_config(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    node_list = []
    chosen_node = ""
    # Traverse the tree and find the nodes having status=ok property
    for node in root_sub_nodes:
        try:
            if node.name == "chosen":
                chosen_node = node
            status = node["status"].value
            if "okay" in status:
                node_list.append(node)
        except:
           pass

    src_dir = options['args'][1]
    stdin = ""
    try:
        stdin = options['args'][2]
        stdin_node = get_stdin(sdt, chosen_node, node_list)
    except IndexError:
        pass

    drvname = src_dir.split('/')[-3]
    yaml_file = Path( src_dir + "../data/" + drvname + ".yaml")
    try:
        yaml_file_abs = yaml_file.resolve()
    except FileNotFoundError:
        yaml_file_abs = ""

    if yaml_file_abs:
        yamlfile = str(yaml_file_abs)
    else:
        print("Driver doesn't have yaml file")
        return False

    driver_compatlist = []
    driver_proplist = []
    # Read the yaml file and get the driver supported compatible list
    # and config data file required driver properties
    with open(yamlfile, 'r') as stream:
        schema = yaml.safe_load(stream)
        driver_compatlist = compat_list(schema)
        driver_proplist = schema['required']
        try:
            config_struct = schema['config']
        except KeyError:
            config_struct = []
        try:
            driver_optproplist = schema['optional']
        except KeyError:
            driver_optproplist = []

    driver_nodes = []
    for compat in driver_compatlist:
        for node in node_list:
           compat_string = node['compatible'].value
           for compa in compat_string:
               if compat in compa:
                   driver_nodes.append(node)

    # Remove duplicate nodes
    driver_nodes = list(dict.fromkeys(driver_nodes))
    driver_nodes = get_mapped_nodes(sdt, driver_nodes, options)
    # config file name: x<driver_name>_g.c 
    driver_name = yamlfile.split('/')[-1].split('.')[0]
    if not config_struct:
        config_struct = str("X") + driver_name.capitalize() + str("_Config")
    else:
        config_struct = config_struct[0]
        driver_name = config_struct.split('_Config')[0].lower()
        driver_name = driver_name[1:]
    outfile = str("x") + driver_name + str("_g.c")

    plat = DtbtoCStruct(outfile)
    nodename_list = []
    for node in driver_nodes:
        nodename_list.append(node.name)

    cmake_file = drvname.upper() + str("Config.cmake")
    with open(cmake_file, 'a') as fd:
       fd.write("set(DRIVER_INSTANCES %s)\n" % to_cmakelist(nodename_list))
       if stdin:
           match = [x for x in nodename_list if re.search(x, stdin_node.name)]
           if match:
               fd.write("set(STDIN_INSTANCE %s)\n" % '"{}"'.format(match[0]))

    for index,node in enumerate(driver_nodes):
        drvprop_list = []
        drvoptprop_list = []
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
                drvprop_list.append(hex(val))
                plat.buf('\n\t\t%s' % hex(val))
                if pad:
                    for j in range(1, pad):
                        try:
                            val, size = scan_reg_size(node, node[prop].value, j)
                            drvprop_list.append(hex(val))
                            plat.buf(',\n\t\t%s' % hex(val))
                        except IndexError:
                            plat.buf(',\n\t\t%s' % hex(0xFFFF))
            elif prop == "compatible":
                plat.buf('\n\t\t%s' % '"{}"'.format(node[prop].value[0]))
                drvprop_list.append(node[prop].value[0])
            elif prop == "interrupts":
                try:
                    intr = get_interrupt_prop(sdt, node, node[prop].value)
                except KeyError:
                    intr = [hex(0xFFFF)]

                if pad:
                    plat.buf('\n\t\t{')
                    for j in range(0, pad):
                        try:
                            plat.buf('%s' % intr[j])
                            drvprop_list.append(intr[j])
                        except IndexError:
                            plat.buf('%s' % hex(0xFFFF))
                            drvprop_list.append(hex(0xFFFF))
                        if j != pad-1:
                            plat.buf(',  ')
                    plat.buf('}')
                else:
                    plat.buf('\n\t\t%s' % intr[0])
                    drvprop_list.append(intr[0])
            elif prop == "interrupt-parent":
                try:
                    intr_parent = get_intrerrupt_parent(sdt, node[prop].value)
                except KeyError:
                    intr_parent = 0xFFFF
                plat.buf('\n\t\t%s' % hex(intr_parent))
                drvprop_list.append(hex(intr_parent))
            elif prop == "clocks":
                clkprop_val = get_clock_prop(sdt, node[prop].value)
                plat.buf('\n\t\t%s' % hex(clkprop_val))
                drvprop_list.append(hex(clkprop_val))
            elif prop == "child,required":
                plat.buf('\n\t\t{')
                for j,child in enumerate(list(node.child_nodes.items())):
                    if len(pad) != 1:
                        plat.buf('\n\t\t\t{')
                    for k,p in enumerate(pad):
                        plat.buf('\n\t\t\t\t%s' % hex(child[1][p].value[0]))
                        drvprop_list.append(hex(child[1][p].value[0]))
                        if k != (len(pad) - 1) or len(pad) == 1:
                            plat.buf(',')
                        plat.buf(' /* %s */' % p)
                    if len(pad) != 1:
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
                drvprop_list.append(hex(prop_val))
            elif prop == "ranges":
                try:
                    device_type = node['device_type'].value[0]
                    if device_type == "pci":
                        device_ispci = 1
                except KeyError:
                    device_ispci = 0
                if device_ispci:
                    prop_vallist = get_pci_ranges(node, node[prop].value, pad)
                    for j, prop_val in enumerate(prop_vallist):
                        plat.buf('\n\t\t%s' % prop_val)
                        if j != (len(prop_vallist) - 1):
                            plat.buf(',')
                        drvprop_list.append(prop_val)
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

                if len(prop_val) > 1:
                    plat.buf('\n\t\t{')
                    for k,item in enumerate(prop_val):
                        if isinstance(item, int):
                            drvprop_list.append(hex(item))
                        else:
                            drvprop_list.append(item)
                        plat.buf('%s' % item)
                        if k != len(prop_val)-1:
                            plat.buf(',  ')
                    plat.buf('}')
                else:
                    drvprop_list.append(hex(prop_val[0]))
                    plat.buf('\n\t\t%s' % hex(prop_val[0]))

            if i == len(driver_proplist)-1:
                plat.buf(' /* %s */' % prop)
                plat.buf('\n\t},')
            else:
                plat.buf(',')
                plat.buf(' /* %s */' % prop)
        if index == len(driver_nodes)-1:
            plat.buf('\n\t {\n\t\t NULL\n\t}')
            plat.buf('\n};')

        for i, prop in enumerate(driver_optproplist):
            if isinstance(prop, dict):
               pad = list(prop.values())[0]
               prop = list(prop.keys())[0]
            if prop == "child,required":
                for j,child in enumerate(list(node.child_nodes.items())):
                    for k,p in enumerate(pad):
                        drvoptprop_list.append(child[1][p].value[0])
            else:
                try:
                    drvoptprop_list.append(hex(node[prop].value[0]))
                except KeyError:
                    pass

        with open(cmake_file, 'a') as fd:
           fd.write("set(DRIVER_PROP_%s_LIST %s)\n" % (index, to_cmakelist(drvprop_list)))
           fd.write("set(DRIVER_OPTPROP_%s_LIST %s)\n" % (index, to_cmakelist(drvoptprop_list)))
           fd.write("list(APPEND TOTAL_DRIVER_PROP_LIST DRIVER_PROP_%s_LIST)\n" % index)
    plat.out(''.join(plat.get_buf()))

    return True
