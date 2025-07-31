#/*
# * Copyright (C) 2024 Advanced Micro Devices, Inc.  All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.kedareswara.rao@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */
"""
The hardware required for a given ESW component is validated by this assist.
Supported Use Cases:
    1. Template Apps(ex: hello_world, memory_tests, lwip_echo_server, etc...)
       a.The proper error message will be generated if the required memory section
       is not present in a given SDT. 
       b.The proper error message will be generated if the required hardware
       is not present in a given SDT. 
    2. Libraries and OS(ex: freertos, lwip, etc....)
       a.The proper error message will be generated if the required hardware
       is not present in a given SDT. 
Usage:
    lopper --enhanced <SDT system-top.dts> -- baremetal_validate_comp_xlnx <processor name> <component source path> <esw repo path>
"""
import sys
import os
import glob
import yaml
import re

sys.path.append(os.path.dirname(__file__))

import common_utils as utils
from baremetalconfig_xlnx import get_cpu_node, item_generator, get_mapped_nodes, get_label
from bmcmake_metadata_xlnx import getmatch_nodes 
from baremetallinker_xlnx import get_memranges
from lopper.log import _init, _warning, _info, _error, _debug, _level, __logger__

_init(__name__)

def is_compat( node, compat_string_to_test ):
    if re.search( "module,baremetal_validate_comp_xlnx", compat_string_to_test):
        return xlnx_baremetal_validate_comp
    return ""

def check_for_mem(sdt, options, mem_type, required_mem):
    required_mem_start = 0x0
    required_mem_size = 0x0
    if required_mem.get("start", {}):
        required_mem_start = required_mem["start"]
    if required_mem.get("size", {}):
        required_mem_size = required_mem["size"]

    mem_ranges, _ = get_memranges(sdt.tree['/'], sdt, options)
    for key, value in sorted(mem_ranges.items(), key=lambda e: e[1][1], reverse=True):
        start,size = value[0], value[1]
        if mem_type == "any" or re.search(mem_type, key):
            if required_mem_start != 0x0:
                if (required_mem_start == start) and (size <= required_mem_size):
                    return False, mem_type, required_mem_size
            if size >= required_mem_size:
                return True, mem_type, required_mem_size

    return False, mem_type, required_mem_size, required_mem_start

def check_required_prop(sdt, node, required_props):
    prop_dict = {}
    if node.propval('xlnx,name') != ['']:
        label_name = node.propval('xlnx,name', list)[0]
    else:
        label_name = get_label(sdt, sdt.tree['/__symbols__'], node)
    prop_list = []
    for prop in required_props:
        if node.propval(prop) != ['']:
            prop_list.append((prop, "True"))
        else:
            prop_list.append((prop, "False"))
    prop_dict.update({label_name:prop_list})
    return prop_dict

def xlnx_baremetal_validate_comp(tgt_node, sdt, options):
    _level(utils.log_setup(options), __name__)
    proc_name = options['args'][0]
    src_path = utils.get_abs_path(options['args'][1])
    repo_path_data = options['args'][2]

    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()
    node_list = []
    for node in root_sub_nodes:
        try:
            status = node["status"].value
            if "okay" in status:
                node_list.append(node)
        except:
           pass
    matched_node = get_cpu_node(sdt, options)

    proc_ip_name = matched_node['xlnx,ip-name'].value[0]
    src_path = src_path.rstrip(os.path.sep)
    name = utils.get_base_name(utils.get_dir_path(src_path))
    # Incase of versioned component strip the version info
    name = re.split(r"_v(\d+)_(\d+)", name)[0]
    yaml_file = os.path.join(utils.get_dir_path(src_path), "data", f"{name}.yaml")
    if not utils.is_file(yaml_file):
        print(f"{name} Comp doesn't have yaml file")
        return False

    schema = utils.load_yaml(yaml_file)
    """
    Read the required_mem schema and return proper in case if the given sdt
    doesn't match the schema requirments.
    """
    required_mem_schema = schema.get('required_mem',{})
    if required_mem_schema:
        mem_type = list(required_mem_schema.keys())[0]
        if name == "memory_tests":
            if proc_ip_name == "microblaze" or proc_ip_name == "microblaze_riscv":
                mem_type = "bram"
            elif proc_ip_name == "ps7_cortexa9":
                mem_type = "ps7_ram"
            else:
                mem_type = "ocm"
        required_mem = required_mem_schema[mem_type]
        has_valid_mem = check_for_mem(sdt, options, mem_type, required_mem)
        if not has_valid_mem[0]:
            if has_valid_mem[-1] != 0x0:
                err_msg = f'ERROR: {name} application requires atleast {hex(has_valid_mem[2])} bytes of {has_valid_mem[1]} memory at {hex(has_valid_mem[-1])} to run'
            else:
                err_msg = f'ERROR: {name} application requires atleast {hex(has_valid_mem[2])} bytes of {has_valid_mem[1]} memory'
            _error(err_msg)
            print(err_msg)
            sys.exit(1)

    """
    Get the Device type and split the dict based on the device type
    if device type is not there return success
    """
    meta_dict = schema.get('depends',{})
    meta_dict.pop("condition",None)
    dev_dict = {}
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
        
        drv_schema = utils.load_yaml(drv_yamlpath)
        hw_type = drv_schema.get('device_type',{})
        if hw_type:
            if hw_type in dev_dict:
                val = dev_dict[hw_type]
                val.append({drv:prop_list})
                dev_dict[hw_type] = val
            else:
                dev_dict.update({hw_type:[{drv:prop_list}]})
        else:
            continue

    for dev_type, dev_list in dev_dict.items():
        valid_hw = None
        driver_list = []
        for dev in dev_list:
            for drv,prop_list in dev.items():
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

                driver_list.append(drv)
                nodes = getmatch_nodes(sdt, node_list, drv_yamlpath, options)
                if nodes:
                    """
                    For emacps driver phy-handle property presence is optional
                    remove the same from the list if exists.
                    """
                    if (drv == "emacps") and ("phy-handle" in prop_list):
                        prop_list.remove("phy-handle")
                    valid_hw = nodes[0],prop_list
        if valid_hw:
            prop_dict = check_required_prop(sdt, valid_hw[0], valid_hw[1])
            for ip,prop_list in prop_dict.items():
                for prop in prop_list:
                    if 'False' in prop:
                        err_msg = f'ERROR: {name} requires {ip} with {prop[0]} enabled'
                        print(err_msg)
                        _error(err_msg)
                        sys.exit(1)
        else:
            err_msg = f'ERROR: {name} requires at least one {dev_type} hardware instance to be present'
            print(err_msg)
            _error(err_msg)
            sys.exit(1)

    return True
