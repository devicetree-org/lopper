#/*
# * Copyright (C) 2022 Advanced Micro Devices, Inc.  All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.kedareswara.rao@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */
import sys
import os
import glob
import yaml
import re

sys.path.append(os.path.dirname(__file__))

import common_utils as utils
from baremetalconfig_xlnx import get_cpu_node

def is_compat( node, compat_string_to_test ):
    if re.search( "module,baremetal_getsupported_comp_xlnx", compat_string_to_test):
        return xlnx_baremetal_getsupported_comp
    return ""

class VerboseSafeDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True

def get_yaml_data(comp_name, comp_dir):
    yaml_file = os.path.join(comp_dir, 'data', f'{comp_name}.yaml')
    schema = utils.load_yaml(yaml_file)
    supported_proc_list = schema.get('supported_processors',[])
    supported_os_list = schema.get('supported_os',[])
    description = schema.get('description',"")
    dep_lib_list = list(schema.get('depends_libs',{}).keys())

    return supported_proc_list, supported_os_list, description, dep_lib_list

def xlnx_baremetal_getsupported_comp(tgt_node, sdt, options):
    proc_name = options['args'][0]
    repo_path_data = utils.get_abs_path(options['args'][1])

    matched_node = get_cpu_node(sdt, options)
    proc_ip_name = matched_node['xlnx,ip-name'].value[0]

    supported_app_dict = {proc_name: {'standalone': {}, 'freertos': {}}}
    supported_libs_dict = {proc_name: {'standalone': {}, 'freertos': {}}}

    if utils.is_file(repo_path_data):
        path_schema = utils.load_yaml(repo_path_data)
    else:
        path_schema = {
            'library' : {},
            'apps'    : {}
        }

        files = glob.glob(repo_path_data + '/**/data/*.yaml', recursive=True)
        for entries in files:
            dir_path = utils.get_dir_path(utils.get_dir_path(entries))
            comp_name = utils.get_base_name(dir_path)
            comp_name =  re.sub("_v(\d+)_(\d+)", "", comp_name)
            yaml_data = utils.load_yaml(entries)
            version = yaml_data.get('version','vless')

            if yaml_data['type'] not in ['library','apps']:
                continue

            if yaml_data['type'] in ['library'] and version == 'vless':
                print(f"""\b
                    [ERROR]:  Couldnt set the paths correctly.
                    {comp_name} in {repo_path_data} doesnt have a version.
                    Library and OS needs version numbers in its yaml.
                """)
                sys.exit(1)

            path_schema[yaml_data['type']][comp_name] = {version : dir_path}

    apps_dict = path_schema.get('apps', {})
    libs_dict = path_schema.get('library', {})

    for app_name in list(apps_dict.keys()):
        try:
            supported_proc_list, supported_os_list, description, dep_lib_list = get_yaml_data(app_name, apps_dict[app_name]['vless'])
        except KeyError:
            supported_proc_list, supported_os_list, description, dep_lib_list = get_yaml_data(app_name, apps_dict[app_name]['path'][0])
        if proc_ip_name in supported_proc_list:
            app_dict = {app_name : {'description': description, 'depends_libs': dep_lib_list}}
            if 'standalone' in supported_os_list:
                supported_app_dict[proc_name]['standalone'].update(app_dict)
            if "freertos10_xilinx" in supported_os_list:
                supported_app_dict[proc_name]['freertos'].update(app_dict)

    for lib_name in list(libs_dict.keys()):
        cur_lib_dict = libs_dict[lib_name]
        version_list = list(cur_lib_dict.keys())
        if 'path' in version_list:
            lib_dir = cur_lib_dict['path'][0]
            sorted_lib_dict = {version: cur_lib_dict[version] for version in version_list}
        else:
            version_list.sort(key = float, reverse = True)
            lib_dir = cur_lib_dict[version_list[0]]
            sorted_lib_dict = {version: cur_lib_dict[version] for version in version_list}

        supported_proc_list, supported_os_list, description, dep_lib_list = get_yaml_data(lib_name, lib_dir)
        if proc_ip_name in supported_proc_list:
            if 'path' in version_list:
                lib_dict = {lib_name : {'description': description, 'depends_libs': dep_lib_list, 'path': sorted_lib_dict['path']}}
            else:
                lib_dict = {lib_name : {'description': description, 'depends_libs': dep_lib_list, 'versions': sorted_lib_dict}}
            if 'standalone' in supported_os_list:
                supported_libs_dict[proc_name]['standalone'].update(lib_dict)
            if "freertos10_xilinx" in supported_os_list:
                supported_libs_dict[proc_name]['freertos'].update(lib_dict)


    with open(os.path.join(sdt.outdir, 'app_list.yaml'), 'w') as fd:
        fd.write(yaml.dump(supported_app_dict, sort_keys=False, indent=2, width=32768, Dumper=VerboseSafeDumper))

    with open(os.path.join(sdt.outdir, 'lib_list.yaml'), 'w') as fd:
        fd.write(yaml.dump(supported_libs_dict, sort_keys=False, indent=2, width=32768, Dumper=VerboseSafeDumper))

    return True