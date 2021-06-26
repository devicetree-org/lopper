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

def generate_drvcmake_metadata(sdt, node_list, src_dir, options):
    driver_compatlist = []

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

    # Get the example_schema
    with open(yamlfile, 'r') as stream:
        schema = yaml.safe_load(stream)
        driver_compatlist = compat_list(schema)
        try:
            example_schema = schema['examples']
        except KeyError:
            example_schema = {}
       
    driver_nodes = []
    for compat in driver_compatlist:
        for node in node_list:
           compatlist = node['compatible'].value
           for compat_string in compatlist:
               if compat in compat_string:
                   driver_nodes.append(node)

    driver_nodes = get_mapped_nodes(sdt, driver_nodes, options)
    driver_nodes = list(dict.fromkeys(driver_nodes))
    nodename_list = []
    reg_list = []
    example_dict = {}
    depreg_dict = {}
    for node in driver_nodes:
        depreg_list = []
        reg, size = scan_reg_size(node, node['reg'].value, 0)
        nodename_list.append(node.name)
        reg_list.append(hex(reg))
        
        validex_list = []
        for example,prop in example_schema.items():
            valid_ex = 0
            match_list = []
            for p in prop:
                if isinstance(p, dict):
                    for e,prop_val in p.items():
                        valid_phandle = 0
                        try:
                            val = node[e].value
                            if '' in val:
                                val = 1
                            if e == "axistream-connected":
                                reg = get_phandle_regprop(sdt, e, val)
                                val = reg & 0xF
                            if prop_val == "phandle":
                                depreg_list.append(hex(get_phandle_regprop(sdt, e, val)))
                                valid_phandle = 1
                        except KeyError:
                            val = 0
                        if prop_val == val:
                            match_list.append(True)
                        elif prop_val == "phandle" and valid_phandle == 1:
                            match_list.append(True)
                        elif isinstance(val, list):
                            if prop_val == val[0]:
                                match_list.append(True)
                            else:
                                match_list.append(False)
                        else:
                            match_list.append(False)
                else:
                    try:
                        valid_ex = node[prop[0]].value
                    except KeyError:
                        valid_ex = 0

            if valid_ex or not False in match_list:
                validex_list.append(example)

        example_dict.update({node.name:validex_list})
        depreg_dict.update({node.name:depreg_list})

    drvname = yamlfile.rsplit('/', 1)[-1]
    drvname = drvname.replace('.yaml', '')
    cmake_file = drvname.capitalize() + str("Example.cmake")
    with open(cmake_file, 'a') as fd:
        fd.write("set(NUM_DRIVER_INSTANCES %s)\n" % to_cmakelist(nodename_list))
        fd.write("set(REG_LIST %s)\n" % to_cmakelist(reg_list))
        for index,name in enumerate(nodename_list):
            fd.write("set(EXAMPLE_LIST%s %s)\n" % (index, to_cmakelist(example_dict[name])))
            fd.write("set(DEPDRV_REG_LIST%s %s)\n" % (index, to_cmakelist(depreg_dict[name])))
            fd.write("list(APPEND TOTAL_EXAMPLE_LIST EXAMPLE_LIST%s)\n" % index)
            fd.write("list(APPEND TOTAL_DEPDRV_REG_LIST DEPDRV_REG_LIST%s)\n" % index)

def getmatch_nodes(sdt, node_list, yamlfile, options):
    # Get the example_schema
    with open(yamlfile, 'r') as stream:
        schema = yaml.safe_load(stream)
        driver_compatlist = compat_list(schema)
       
    driver_nodes = []
    for compat in driver_compatlist:
        for node in node_list:
           compat_string = node['compatible'].value[0]
           if compat in compat_string:
               driver_nodes.append(node)

    driver_nodes = get_mapped_nodes(sdt, driver_nodes, options)
    driver_nodes = list(dict.fromkeys(driver_nodes))
    return driver_nodes

def getxlnx_phytype(sdt, value):
    child_node = [node for node in sdt.tree['/'].subnodes() if node.phandle == value[0]]
    phy_type = child_node[0]['xlnx,phy-type'].value[0]
    return hex(phy_type)

def lwip_topolgy(config):
    topology_fd = open('xtopology_g.c', 'w')
    tmp_str = "netif/xtopology.h"
    tmp_str = '"{}"'.format(tmp_str)
    topology_fd.write("\n#include %s\n" % tmp_str)
    tmp_str = "xil_types.h"
    tmp_str = '"{}"'.format(tmp_str)
    topology_fd.write("#include %s\n\n" % tmp_str)
    topology_fd.write("struct xtopology_t xtopology[] = {\n")
    for index, data in enumerate(config):
        if (index % 2) == 0:
            topology_fd.write("\t{\n")
        topology_fd.write("\t\t%s,\n" % data)
        if (index % 2) != 0:
            topology_fd.write("\n\t},\n")
    topology_fd.write("\t{\n")
    topology_fd.write("\t\tNULL\n")
    topology_fd.write("\t}\n")
    topology_fd.write("};")

def generate_hwtocmake_medata(sdt, node_list, src_path, repo_path, options):
    meta_dict = {}
    name = src_path.split('/')[-3]
    yaml_file = Path( src_path + "../data/" + name + ".yaml")
    try:
        yaml_file_abs = yaml_file.resolve()
    except FileNotFoundError:
        yaml_file_abs = ""

    if yaml_file_abs:
        yamlfile = str(yaml_file_abs)
    else:
        print("Driver doesn't have yaml file")
        return False

    with open(yamlfile, 'r') as stream:
        schema = yaml.safe_load(stream)
        meta_dict = schema['required']

    lwip = re.search("lwip211", name)
    cmake_file = name.capitalize() + str("Example.cmake")
    topology_data = []
    with open(cmake_file, "a") as fd:
        lwiptype_index = 0
        for drv, prop_list in sorted(meta_dict.items(), key=lambda kv:(kv[0], kv[1])):
            name = drv + str(".yaml")
            drv_yamlpath = [y for x in os.walk(repo_path) for y in glob.glob(os.path.join(x[0], name))]
            nodes = getmatch_nodes(sdt, node_list, drv_yamlpath[0], options)
            name_list = [node.name for node in nodes]
            fd.write("set(%s_NUM_DRIVER_INSTANCES %s)\n" % (drv.upper(), to_cmakelist(name_list)))
            for index,node in enumerate(nodes):
                val_list = []
                for prop in prop_list:
                    if prop == "reg":
                       reg,size = scan_reg_size(node, node[prop].value, 0)
                       val = hex(reg)
                       if lwip:
                           topology_data.append(val)
                           topology_data.append(lwiptype_index)
                    elif prop == "interrupts":
                       val = get_interrupt_prop(sdt, node, node[prop].value)
                       val = val[0]
                    elif prop == "axistream-connected":
                       val = hex(get_phandle_regprop(sdt, prop, node[prop].value))
                    elif prop == "phy-handle":
                       try:
                           val = getxlnx_phytype(sdt, node[prop].value)
                       except KeyError:
                           val = hex(0)
                    else:
                        val = hex(node[prop].value[0])
                    val_list.append(val)
                fd.write("set(%s%s_PROP_LIST %s)\n" % (drv.upper(), index, to_cmakelist(val_list)))
                fd.write("list(APPEND TOTAL_%s_PROP_LIST %s%s_PROP_LIST)\n" % (drv.upper(), drv.upper(), index))
            lwiptype_index += 1
    if topology_data:
        lwip_topolgy(topology_data)

def is_compat( node, compat_string_to_test ):
    if re.search( "module,bmcmake_metadata_xlnx", compat_string_to_test):
        return xlnx_generate_cmake_metadata
    return ""

def to_cmakelist(pylist):
    cmake_list = ';'.join(pylist)
    cmake_list = '"{}"'.format(cmake_list)

    return cmake_list

def xlnx_generate_cmake_metadata(tgt_node, sdt, options):
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

    src_path = options['args'][1]
    command = options['args'][2]
    repo_path = ""
    try:
        repo_path = options['args'][3]
    except IndexError:
        pass

    if command == "drvcmake_metadata":
        generate_drvcmake_metadata(sdt, node_list, src_path, options)
    elif command == "hwcmake_metadata":
        generate_hwtocmake_medata(sdt, node_list, src_path, repo_path, options)
    return True
