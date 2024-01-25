#/*
# * Copyright (c) 2020 Xilinx Inc. All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.rao@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */
import sys
import os
import re
import yaml

sys.path.append(os.path.dirname(__file__))

import common_utils as utils

def get_label(sdt, symbol_node, node):
    prop_dict = symbol_node.__props__
    match = [label for label,node_abs in prop_dict.items() if re.match(node_abs[0], node.abs_path) and len(node_abs[0]) == len(node.abs_path)]
    if match:
        return match[0]
    elif node.propval('xlnx,name') != ['']:
        return node.propval('xlnx,name', list)[0]
    else:
        return None

def get_cpu_node(sdt, options):
    cpu_name = options['args'][0]
    symbol_node = sdt.tree['/__symbols__']
    nodes = sdt.tree.nodes('/cpu.*')
    cpu_labels = []
    matched_label = None
    for node in nodes:
        matched_label = get_label(sdt, symbol_node, node)
        if matched_label is not None:
            if matched_label == cpu_name:
                return node
            elif node.propval('reg') != ['']:
                cpu_labels.append(matched_label)

    print(f"ERROR: In valid CPU Name valid Processors for a given SDT are {cpu_labels}\n")
    sys.exit(1)

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
    compatible_list = []
    if schema and 'compatible' in schema.get('properties',{}).keys():
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

def get_interrupt_id(sdt, node, value):
    intr = []
    inp =  node['interrupt-parent'].value[0]
    intr_parent = [node for node in sdt.tree['/'].subnodes() if node.phandle == inp]
    inc = intr_parent[0]["#interrupt-cells"].value[0]
    nintr = len(value)/inc
    tmp = inc % 2
    for val in range(0, int(nintr)):
        intr_id = value[tmp]
        intr.append(int(hex(intr_id), 16))
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
This API scans the pci node's range property and returns the required address
and size.

Args:
    range_value: List containing one range values.
    ns: size cells value for the pci node.
"""
def scan_ranges_size(range_value, ns):
    """
    Both address and size can be either 32 bit or 64 bit.
    Check the higher cell for both and if that is not zero,
    concatenate the higher and the lower cells.
    e.g. <0x4 0x80000000> => 0x480000000
    """
    addr = hex(range_value[2])
    high_addr_cell = hex(range_value[1])
    if high_addr_cell != "0x0":
        addr = high_addr_cell + addr.lstrip('0x').ljust(8, '0')

    size = hex(range_value[-1])
    high_size_cell = hex(range_value[-2])
    # If ns = 1, then there is no use of high_size_cells
    if high_size_cell != "0x0" and ns > 1:
        size = high_size_cell + size.lstrip('0x').ljust(8, '0')

    return int(addr, base=16), int(size, base=16)


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
    """
    For pci range, address cells is always 3, size-cells may vary between 1 or 2 (usually it is 2).
    Sample pci range for address-cells = 3 and size-cells = 2:
    eg.1: <0x43000000 0x00000080 0x00000000 0x00000080 0x00000000 0x00000000 0x80000000>
    eg.2: <0x42000000 0 0x80000000  0x80000000  0 0x20000000>
    cell[0] => chip-select (Flag for 32bit or 64 bit PCIe Bar)
    cell[1] cell[2] => PCIe address (needed in the config structure)
    cell[-2] cell[-1] => Size of the PCIe Bus (needed to get High address in config table)
    Middle cells (cell[3] cell[4] => In example 1, cell[3] => in example 2)
      => The CPU host address where the PCIe address will be mapped
      => The middle cell size (i.e. 2 or 1) depends on the SoC bus width.
    """
    na = node["#address-cells"].value[0]
    ns = node["#size-cells"].value[0]
    """
    Probable combinations for pci range are:
    <3 address cells> <2 cpu host addr cell> <2 size cells>
    <3 address cells> <1 cpu host addr cell> <2 size cells>
    <3 address cells> <1 cpu host addr cell> <1 size cells>

    cell_size = na + (2*ns) covers first and third conditions
    if condition covers the second one.
    """
    cell_size = na + (2 * ns)

    if len(value) % 6 == 0:
        cell_size = 6

    pci_range_split = []
    for i in range(pad):
        pci_range_split.append(value[i*cell_size:(i+1)*cell_size])

    pci_ranges = []
    for ranges in pci_range_split:
        if ranges:
            reg, size = scan_ranges_size(ranges, ns)
            high_addr = reg + size - 1
            pci_ranges += [hex(reg), hex(high_addr)]
        else:
            pci_ranges += [hex(0), hex(0)]
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
    if chosen_node.propval('stdout-path') != ['']:
        prop_val = chosen_node['stdout-path'].value
        serial_node = sdt.tree.alias_node(prop_val[0].split(':')[0])
        if serial_node:
            match = [x for x in node_list if re.search(x.name, serial_node.name)]
            return match[0]
    return 0

def get_mapped_nodes(sdt, node_list, options):
    # Yocto Machine to CPU compat mapping
    match_cpunode = get_cpu_node(sdt, options)

    all_phandles = []
    address_map = match_cpunode.parent["address-map"].value
    na = match_cpunode.parent["#ranges-address-cells"].value[0]
    ns = match_cpunode.parent["#ranges-size-cells"].value[0]
    cells = na + ns
    tmp = na
    while tmp < len(address_map):
        all_phandles.append(address_map[tmp])
        tmp = tmp + cells + na + 1

    # Make sure node order is preserved
    node_list.sort(key=lambda n: n.phandle, reverse=False)
    valid_nodes = [node for node in node_list for handle in all_phandles if handle == node.phandle]
    return valid_nodes

def xlnx_generate_config_struct(sdt, node, drvprop_list, plat, driver_proplist, is_subnode):
    for i, prop in enumerate(driver_proplist):
        pad = 0
        phandle_prop = 0
        subnode_gen = 0
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
            if isinstance(pad, dict):
                subnode_prop = prop
                prop = list(pad.keys())[0]
                subnode_prop_list = list(pad.values())[0]
                if prop == "subnode_phandle":
                    subnode_gen = 1
                    pad = 0
                    phandle_prop = 0
            if pad == "phandle":
                phandle_prop = 1
    
        if i == 0 and is_subnode:
            plat.buf('\n\t\t{')
        elif i == 0:
            plat.buf('\n\t{')

        if not subnode_gen:
            xlnx_generate_prop(sdt, node, prop, drvprop_list, plat, pad, phandle_prop)
        else:
            # Get the sub node
            try:
                phandle_value = node[subnode_prop].value[0]
            except KeyError:
                print(f"ERROR: In valid property name {subnode_prop}\n")
                sys.exit(1)
                
            sub_node = [node for node in sdt.tree['/'].subnodes() if node.phandle == phandle_value]
            xlnx_generate_config_struct(sdt, sub_node[0], drvprop_list, plat, subnode_prop_list, 1)
    
        if i == len(driver_proplist)-1:
            if "subnode_phandle" not in prop:
                plat.buf(' /* %s */' % prop)
            if is_subnode:
                plat.buf('\n\t\t}')
            else:
                plat.buf('\n\t},')
        else:
            plat.buf(',')
            if "subnode_phandle" not in prop:
                plat.buf(' /* %s */' % prop)

def xlnx_generate_prop(sdt, node, prop, drvprop_list, plat, pad, phandle_prop):
    nosub = 0
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
        numsplits = 0
        splitidx = 0
        for j,child in enumerate(list(node.child_nodes.items())):
            if len(pad) != 1:
                plat.buf('\n\t\t\t{')
            for k,p in enumerate(pad):
                if p == "nosub":
                    nosub = 1
                if len(p) == 1:
                    if 'split' in p.keys():
                        splitidxs = []
                        for idx in str(p['split']).split(' '):
                            splitidxs.append(int(idx))
                        numsplits = len(splitidxs);
                        continue
                    pv = list(p.values())
                    xlnx_generate_prop(sdt, child[1], prop, drvprop_list, plat, pv[0], phandle_prop)
                else:
                    try:
                        plat.buf('\n\t\t\t\t%s' % str(child[1][p].value[0]))
                        drvprop_list.append(str(child[1][p].value[0]))
                        if k != (len(pad) - 1) or len(pad) == 1:
                            plat.buf(',')
                        plat.buf(' /* %s */' % p)
                    except KeyError:
                        if nosub == 0:
                            plat.buf('\n\t\t\t\t%s' % hex(0xFFFF))
                            drvprop_list.append(hex(0xFFFF))
            if len(pad) != 1:
                if j != (len(list(node.child_nodes.items())) - 1):
                    plat.buf('\n\t\t\t},')
                else:
                    plat.buf('\n\t\t\t},')
            if numsplits:
                if splitidx < numsplits:
                    if j == splitidxs[splitidx]:
                        plat.buf('\n},\n{')
                        splitidx = splitidx + 1
        plat.buf('\n\t\t},')
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
    elif prop == "Handler-table":
        # In the driver config structure this property is of type struct
        # to avoid Missing braces around initializer warning add braces
        # around value (https://gcc.gnu.org/bugzilla/show_bug.cgi?id=53119)
        plat.buf('\n\t\t{{0U}}')
    else:
        try:
            prop_val = node[prop].value
            # For boolean property if present LopperProp will return
            # empty string convert it to baremetal config struct expected value
            if '' in prop_val:
                prop_val = [1]
        except KeyError:
            prop_val = [0]

        if pad:
            address_prop = ""
            for index in range(0,pad):
                if index == 0:
                    address_prop = hex(node[prop].value[index])
                elif index < len(node[prop].value):
                    address_prop += f"{node[prop].value[index]:08x}"
            if address_prop:
                plat.buf(f'\n\t\t{address_prop}')
                drvprop_list.append(address_prop)
        else:
            if ('{' in str(prop_val[0])):
                    prop_val_temp = []
                    for k in (str(prop_val[0]).split()[1]).split(","):
                        prop_val_temp.append(int(k, 16))
                    prop_val = prop_val_temp

            if ('/bits/' in prop_val):
                prop_val = [int(prop_val[-1][3:-1], base=16)]

            if isinstance(prop_val[0], str):
                if prop_val[0].replace('.','',1).isdigit():
                    plat.buf('\n\t\t%s' % '{}'.format(node[prop].value[0]))
                else:
                    plat.buf('\n\t\t%s' % '"{}"'.format(node[prop].value[0]))
                drvprop_list.append(node[prop].value[0])
            elif len(prop_val) > 1:
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


# tgt_node: is the baremetal config top level domain node number
# sdt: is the system device-tree
# options: baremetal driver meta-data file path
def xlnx_generate_bm_config(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    node_list = []
    chosen_node = ""
    if options.get('outdir', {}):
        sdt.outdir = options['outdir']
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

    drvpath = utils.get_dir_path(src_dir.rstrip(os.sep))
    drvname = utils.get_base_name(drvpath)
    # Incase of versioned driver strip the version info
    drvname = re.sub(r"_v.*_.*$", "", drvname)
    yaml_file = os.path.join(drvpath, f"data/{drvname}.yaml")
    if not utils.is_file(yaml_file):
        print(f"{drvname} Driver doesn't have yaml file")
        return False

    driver_compatlist = []
    # Read the yaml file and get the driver supported compatible list
    # and config data file required driver properties
    schema = utils.load_yaml(yaml_file)
    driver_compatlist = compat_list(schema)
    driver_proplist = schema.get('required',[])
    config_struct = schema.get('config',[])
    driver_optproplist = schema.get('optional',[])

    if driver_proplist == []:
        return True
    driver_nodes = []
    for compat in driver_compatlist:
        for node in node_list:
           compat_string = node['compatible'].value
           for compa in compat_string:
               if compat in compa:
                   driver_nodes.append(node)

    # Remove duplicate nodes
    driver_nodes = list(set(driver_nodes))
    if sdt.tree[tgt_node].propval('pruned-sdt') == ['']:
        driver_nodes = get_mapped_nodes(sdt, driver_nodes, options)
    if not config_struct:
        config_struct = str("X") + drvname.capitalize() + str("_Config")
    else:
        config_struct = config_struct[0]

    out_g_file_name = utils.find_files("*_g.c", src_dir)
    if out_g_file_name:
        out_g_file_name = utils.get_base_name(out_g_file_name[0])
        drvname = out_g_file_name.replace('_g.c','')
    else:
        drvname = config_struct.split('_Config')[0].lower()
        drvname = f"x{drvname[1:]}"
        out_g_file_name = f"{drvname}_g.c"
    outfile = os.path.join(sdt.outdir, out_g_file_name)

    if driver_nodes == []:
        return True

    plat = DtbtoCStruct(outfile)
    nodename_list = []
    for node in driver_nodes:
        nodename_list.append(node.name)

    cmake_file = drvname[1:].upper() + str("Config.cmake")
    cmake_file = os.path.join(sdt.outdir,f"{cmake_file}")
    with open(cmake_file, 'a') as fd:
       fd.write("set(DRIVER_INSTANCES %s)\n" % utils.to_cmakelist(nodename_list))
       if stdin:
           if stdin_node:
               match = [x for x in nodename_list if re.search(x, stdin_node.name)]
               if match:
                   fd.write("set(STDIN_INSTANCE %s)\n" % '"{}"'.format(match[0]))

    for index,node in enumerate(driver_nodes):
        drvprop_list = []
        drvoptprop_list = []
        if index == 0:
            plat.buf('#include "%s.h"\n' % drvname)
            plat.buf('\n%s %s __attribute__ ((section (".drvcfg_sec"))) = {\n' % (config_struct, config_struct + str("Table[]")))
        xlnx_generate_config_struct(sdt, node, drvprop_list, plat, driver_proplist, 0)
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
           fd.write("set(DRIVER_PROP_%s_LIST %s)\n" % (index, utils.to_cmakelist(drvprop_list)))
           fd.write("set(DRIVER_OPTPROP_%s_LIST %s)\n" % (index, utils.to_cmakelist(drvoptprop_list)))
           fd.write("list(APPEND TOTAL_DRIVER_PROP_LIST DRIVER_PROP_%s_LIST)\n" % index)
    plat.out(''.join(plat.get_buf()))

    return True
