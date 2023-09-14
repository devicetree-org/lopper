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
import glob
import yaml

sys.path.append(os.path.dirname(__file__))
import common_utils as utils
import baremetalconfig_xlnx as bm_config

class YamlDumper(yaml.Dumper):
    def increase_indent(self, flow=False, indentless=False):
        return super(YamlDumper, self).increase_indent(flow, False)

def write_yaml(filepath, data):
    """
    Write the data into a yaml file format

    Args:
        | filepath: the yaml file path
        | data: the data
    """
    with open(filepath, 'w') as outfile:
        yaml.dump(data, outfile, Dumper=YamlDumper, default_flow_style=False, sort_keys=False, indent=4, width=32768)

def generate_drvcmake_metadata(sdt, node_list, src_dir, options):
    driver_compatlist = []
    drvname = utils.get_base_name(utils.get_dir_path(src_dir))
    # Incase of versioned component strip the version info
    drvname = re.sub(r"_v.*_.*$", "", drvname)
    yaml_file = os.path.join(utils.get_dir_path(src_dir), "data", f"{drvname}.yaml")
    if not utils.is_file(yaml_file):
        print(f"{drvname} Driver doesn't have yaml file")
        return False

    # Get the example_schema
    schema = utils.load_yaml(yaml_file)
    driver_compatlist = bm_config.compat_list(schema)
    example_schema = schema.get('examples',{})
       
    driver_nodes = []
    for compat in driver_compatlist:
        for node in node_list:
           compatlist = node['compatible'].value
           for compat_string in compatlist:
               if compat in compat_string:
                   driver_nodes.append(node)

    if sdt.tree['/'].propval('pruned-sdt') == ['']:
        driver_nodes = bm_config.get_mapped_nodes(sdt, driver_nodes, options)
    nodename_list = []
    reg_list = []
    example_dict = {}
    depreg_dict = {}

    for node in driver_nodes:
        depreg_list = []
        reg, size = bm_config.scan_reg_size(node, node['reg'].value, 0)
        if node.propval('xlnx,name') != ['']:
            nodename_list.append(node.propval('xlnx,name', list)[0])
        else:
            nodename_list.append(node.name)
        reg_list.append(hex(reg))
        
        validex_list = []
        for example,prop in example_schema.items():
            valid_ex = 0
            match_list = []
            for p in prop:
                if "dependency_files" in p:
                    continue
                if isinstance(p, dict):
                    for e,prop_val in p.items():
                        valid_phandle = 0
                        try:
                            val = node[e].value
                            if '' in val:
                                val = 1
                            if e == "axistream-connected":
                                reg = bm_config.get_phandle_regprop(sdt, e, val)
                                val = reg & 0xF
                            if prop_val == "phandle":
                                depreg_list.append(hex(bm_config.get_phandle_regprop(sdt, e, val)))
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
                        valid_ex = node[p].value
                        if valid_ex:
                            match_list.append(True)
                    except KeyError:
                        match_list.append(False)

            #If all the example required conditions met it is valid example
            if False in match_list:
                valid_ex = 0
            else:
                valid_ex = 1

            if valid_ex:
                validex_list.append(example)

        if node.propval('xlnx,name') != ['']:
            example_dict.update({node.propval('xlnx,name', list)[0]:validex_list})
        else:
            example_dict.update({node.name:validex_list})
        if node.propval('xlnx,name') != ['']:
            depreg_dict.update({node.propval('xlnx,name', list)[0]:depreg_list})
        else:
            depreg_dict.update({node.name:depreg_list})

    cmake_file = os.path.join(sdt.outdir, f"{drvname.capitalize()}Example.cmake")
    with open(cmake_file, 'a') as fd:
        fd.write(f"set(NUM_DRIVER_INSTANCES {utils.to_cmakelist(nodename_list)})\n")
        fd.write(f"set(REG_LIST {utils.to_cmakelist(reg_list)})\n")
        for index,name in enumerate(nodename_list):
            fd.write(f"set(EXAMPLE_LIST{index} {utils.to_cmakelist(example_dict[name])})\n")
            fd.write(f"set(DEPDRV_REG_LIST{index} {utils.to_cmakelist(depreg_dict[name])})\n")
            fd.write(f"list(APPEND TOTAL_EXAMPLE_LIST EXAMPLE_LIST{index})\n")
            fd.write(f"list(APPEND TOTAL_DEPDRV_REG_LIST DEPDRV_REG_LIST{index})\n")
    yaml_file = os.path.join(sdt.outdir, f"{drvname}_exlist.yaml")
    new_ex_dict = {}
    for ip,ex_list in example_dict.items():
        update_exdict = {}
        for ex in ex_list:
            if "dependency_files" in example_schema[ex][0]:
                update_exdict.update({ex:example_schema[ex][0]['dependency_files']})
            else:
                update_exdict.update({ex:[]})
            new_ex_dict.update({ip:update_exdict})
        example_dict = new_ex_dict
    write_yaml(yaml_file, example_dict)

def getmatch_nodes(sdt, node_list, yaml_file, options):
    # Get the example_schema
    schema = utils.load_yaml(yaml_file)
    driver_nodes = []
    driver_compatlist = bm_config.compat_list(schema)
    for compat in driver_compatlist:
        for node in node_list:
           compat_string = node['compatible'].value
           if compat in compat_string:
               driver_nodes.append(node)

    # Remove duplicate nodes
    driver_nodes = list(set(driver_nodes))
    if sdt.tree['/'].propval('pruned-sdt') == ['']:
        driver_nodes = bm_config.get_mapped_nodes(sdt, driver_nodes, options)
    return driver_nodes

def getxlnx_phytype(sdt, value):
    child_node = [node for node in sdt.tree['/'].subnodes() if node.phandle == value[0]]
    phy_type = child_node[0]['xlnx,phy-type'].value[0]
    return hex(phy_type)

def lwip_topolgy(outdir, config):
    topology_fd = open(os.path.join(outdir, 'xtopology_g.c'), 'w')
    topology_str = f'''
#include "netif/xtopology.h"
#include "xil_types.h"

struct xtopology_t xtopology[] = {{'''
    for key, value in config.items():
        topology_str += f'''
    {{
        {key},
        {value},
    }},'''
    topology_str += f'''
    {{
        NULL
    }}
}};'''
    topology_fd.write(topology_str)

def generate_hwtocmake_medata(sdt, node_list, src_path, repo_path_data, options, chosen_node, symbol_node):
    src_path = src_path.rstrip(os.path.sep)
    name = utils.get_base_name(utils.get_dir_path(src_path))
    # Incase of versioned component strip the version info
    name = re.sub(r"_v.*_.*$", "", name)
    yaml_file = os.path.join(utils.get_dir_path(src_path), "data", f"{name}.yaml")

    if not utils.is_file(yaml_file):
        print(f"{name} Driver doesn't have yaml file")
        return False

    schema = utils.load_yaml(yaml_file)
    meta_dict = schema.get('depends',{})
    comp_type = schema.get('type',{})

    lwip = re.search("lwip", name)
    standalone = re.search("standalone", name)
    cmake_file = os.path.join(sdt.outdir, f"{name.capitalize()}Example.cmake")
    topology_data = {}
    with open(cmake_file, "a") as fd:
        for drv, prop_list in sorted(meta_dict.items(), key=lambda kv:(kv[0], kv[1])):
            if utils.is_file(repo_path_data):
                repo_schema = utils.load_yaml(repo_path_data)
                drv_data = repo_schema['driver']
                drv_dir = drv_data.get(drv,{}).get('vless','')
                if not drv_dir and drv_data.get(drv,{}).get('path',''):
                    drv_dir = drv_data.get(drv,{}).get('path','')[0]
            else:
                drv_dir = os.path.join(repo_path_data, "XilinxProcessorIPLib", "drivers", drv)

            drv_yamlpath = os.path.join(drv_dir, "data", f"{drv}.yaml")
            if not utils.is_file(drv_yamlpath):
                print(f"{drv} yaml file {drv_yamlpath} doesnt exist")
                continue

            nodes = getmatch_nodes(sdt, node_list, drv_yamlpath, options)
            name_list = []
            for node in nodes:
                if node.propval('xlnx,name') != ['']:
                    name_list.append(node.propval('xlnx,name', list)[0])
                else:
                    name_list.append(bm_config.get_label(sdt, symbol_node, node))

            fd.write(f"set({drv.upper()}_NUM_DRIVER_INSTANCES {utils.to_cmakelist(name_list)})\n")
            for index,node in enumerate(nodes):
                val_list = []
                for prop in prop_list:
                    if prop == "reg":
                       reg,size = bm_config.scan_reg_size(node, node[prop].value, 0)
                       val = hex(reg)
                       if lwip and comp_type == "library":
                           if drv == "emaclite":
                                topology_data[val] = 0
                           elif drv == "ll_temac":
                                topology_data[val] = 1
                           elif drv == "axiethernet":
                                topology_data[val] = 2
                           elif drv == "emacps":
                                topology_data[val] = 3
                    elif prop == "interrupts":
                       val = bm_config.get_interrupt_prop(sdt, node, node[prop].value)
                       val = val[0]
                    elif prop == "axistream-connected":
                       val = hex(bm_config.get_phandle_regprop(sdt, prop, node[prop].value))
                    elif prop == "phy-handle":
                       try:
                           val = getxlnx_phytype(sdt, node[prop].value)
                       except KeyError:
                           val = hex(0)
                    else:
                        val = hex(node[prop].value[0])
                    val_list.append(val)
                fd.write(f"set({drv.upper()}{index}_PROP_LIST {utils.to_cmakelist(val_list)})\n")
                fd.write(f"list(APPEND TOTAL_{drv.upper()}_PROP_LIST {drv.upper()}{index}_PROP_LIST)\n")
        if standalone:
            stdin_node = bm_config.get_stdin(sdt, chosen_node, node_list)
            if stdin_node.propval('xlnx,name') != ['']:
                fd.write(f'set(STDIN_INSTANCE "{stdin_node.propval("xlnx,name")[0]}")\n')
            else:
                fd.write(f'set(STDIN_INSTANCE "{bm_config.get_label(sdt, symbol_node, stdin_node)}")\n')
            if sdt.tree['/'].propval('slrcount') != ['']:
                val = sdt.tree['/'].propval('slrcount', list)[0]
                fd.write(f'set(NUMBER_OF_SLRS {hex(val)} CACHE STRING "Number of slrs")\n')
            if sdt.tree['/'].propval('device_id') != ['']:
                val = sdt.tree['/'].propval('device_id', list)[0]
                fd.write(f'set(DEVICE_ID "{val}" CACHE STRING "Device Id")\n')
            if sdt.tree['/'].propval('board') != ['']:
                val = sdt.tree['/'].propval('board', list)[0]
                fd.write(f'set(BOARD "{val}" CACHE STRING "BOARD")\n')
            match_cpunode = bm_config.get_cpu_node(sdt, options)
            if re.search("microblaze", match_cpunode['compatible'].value[0]):
                if match_cpunode.propval('xlnx,family') != ['']:
                    family = match_cpunode.propval('xlnx,family', list)[0]
                    fd.write(f'set(CMAKE_MACHINE "{family}" CACHE STRING "CMAKE MACHINE")\n')

    if topology_data:
        lwip_topolgy(sdt.outdir, topology_data)

def is_compat( node, compat_string_to_test ):
    if re.search( "module,bmcmake_metadata_xlnx", compat_string_to_test):
        return xlnx_generate_cmake_metadata
    return ""


def xlnx_generate_cmake_metadata(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    if options.get('outdir', {}):
        sdt.outdir = options['outdir']

    node_list = []
    chosen_node = ""
    symbol_node = ""
    # Traverse the tree and find the nodes having status=ok property
    for node in root_sub_nodes:
        try:
            if node.name == "chosen":
                chosen_node = node
            if node.name == "__symbols__":
                symbol_node = node
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
        generate_hwtocmake_medata(sdt, node_list, src_path, repo_path, options, chosen_node, symbol_node)
    return True
