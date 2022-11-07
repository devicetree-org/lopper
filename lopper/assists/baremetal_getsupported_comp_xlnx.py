#/*
# * Copyright (C) 2022 Advanced Micro Devices, Inc.  All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.kedareswara.rao@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */
import sys
import types
from lopper import Lopper
from lopper import LopperFmt
import lopper
from lopper.tree import *
from re import *
import os
import yaml
import json
import glob

def is_compat( node, compat_string_to_test ):
    if re.search( "module,baremetal_getsupported_comp_xlnx", compat_string_to_test):
        return xlnx_baremetal_getsupported_comp
    return ""

class VerboseSafeDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True

def xlnx_get_supported_component(root_dir, proc_ip_name):
    standalone_app_dict = {}
    freertos_app_dict = {}
    # Get supported component names
    for yaml_file in glob.iglob(root_dir + '**/*.yaml', recursive=True):
        with open(str(yaml_file), "r") as stream:
            schema = yaml.safe_load(stream)
            try:
                proc_list = schema['supported_processors']
                is_supported_app = [proc for proc in proc_list if re.match(proc, proc_ip_name)]
                os_list = schema['supported_os']
                app_description = schema['description']
                if is_supported_app:
                    if "standalone" in os_list:
                        standalone_app_dict.update({Path(yaml_file).stem:{"description":app_description}})
                    if "freertos10_xilinx" in os_list:
                        freertos_app_dict.update({Path(yaml_file).stem:{"description":app_description}})
                dep_lib_list = schema['depends_libs']
                if is_supported_app:
                    if "standalone" in os_list:
                        standalone_app_dict[Path(yaml_file).stem].update({"depends_libs":dep_lib_list})
                    if "freertos10_xilinx" in os_list:
                        freertos_app_dict[Path(yaml_file).stem].update({"depends_libs":dep_lib_list})
            except KeyError:
                pass

    return standalone_app_dict, freertos_app_dict

def xlnx_baremetal_getsupported_comp(tgt_node, sdt, options):
    proc_name = options['args'][0]
    repo_path = options['args'][1]

    root_node_subnodes = sdt.tree[tgt_node].subnodes()
    prop_dict = sdt.tree['/__symbols__'].__props__
    proc_ip_name = None
    for c_node in root_node_subnodes:
        try:
            match = [label for label,node_abs in prop_dict.items() if re.match(node_abs[0], c_node.abs_path) and len(node_abs[0]) == len(c_node.abs_path)]
            if re.match(proc_name, match[0]):
                proc_ip_name = c_node['xlnx,ip-name'].value[0]
        except:
            pass

    supported_apps = {}
    root_dir = repo_path + "/lib/sw_apps/"
    standalone_app_dict, freertos_app_dict  = xlnx_get_supported_component(root_dir, proc_ip_name)
    if standalone_app_dict:
        supported_apps.update({"standalone":standalone_app_dict})
    if freertos_app_dict:
        supported_apps.update({"freertos":freertos_app_dict})
    app_supported_dict = {proc_name:supported_apps}
    with open('app_list.yaml', 'w') as fd:
        fd.write(yaml.dump(app_supported_dict, sort_keys=False, indent=2, width=32768))

    supported_libs = {}
    root_dir = repo_path + "/lib/sw_services/"
    standalone_lib_dict, freertos_lib_dict = xlnx_get_supported_component(root_dir, proc_ip_name)
    if standalone_lib_dict:
        supported_libs.update({"standalone":standalone_lib_dict})
    if freertos_lib_dict:
        supported_libs.update({"freertos":freertos_lib_dict})
    
    root_dir = repo_path + "/ThirdParty/sw_services/"
    standalone_lib_dict, freertos_lib_dict = xlnx_get_supported_component(root_dir, proc_ip_name)
    for lib in supported_libs:
        if lib == "standalone":
            supported_libs['standalone'].update(standalone_lib_dict)
        else:
            supported_libs['freertos'].update(freertos_lib_dict)
    lib_supported_dict = {proc_name:supported_libs}
    with open('lib_list.yaml', 'w') as fd:
        fd.write(yaml.dump(lib_supported_dict, sort_keys=False, indent=2, width=32768, Dumper=VerboseSafeDumper))

    return True
